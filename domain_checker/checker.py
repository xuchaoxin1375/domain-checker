"""域名查询核心：WHOIS 查询、DNS 解析检查、单域名处理流水线。"""

import json
import logging
import random
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

import whois

from .domains import is_valid_domain
from .settings import CONFIG, QUERY_MODES, config_lock, normalize_query_mode, normalize_timeout
from .state import append_task_log, task_lock, task_pause_flags, task_storage

logger = logging.getLogger(__name__)

# WHOIS 响应中表示"域名未注册"的常见关键词（小写匹配）。
# 只有响应文本或明确的 WhoisDomainNotFoundError 命中这些词时，才允许判定未注册。
# 网络错误（例如 "server not found"）不能依靠宽泛的 "not found" 归类，避免误报。
_NOT_FOUND_MARKERS = (
    'no match', 'domain not found', 'no data found',
    'no entries found', 'not registered', 'has not been registered',
    'not been registered', 'no object found', 'no such domain',
    'status: available', 'available for registration',
)

# 未注册结果的固定错误文案，前端/导出/日志共用此措辞
NOT_REGISTERED_MESSAGE = '域名未被注册'
WHOIS_TIMEOUT_MESSAGE = 'WHOIS 查询超时，无法确认域名状态'
WHOIS_EMPTY_RESPONSE_MESSAGE = 'WHOIS 未返回有效数据，无法确认域名状态'

_HOLD_STATUS_KEYS = {'clienthold': 'clientHold', 'serverhold': 'serverHold'}
_RDAP_ENDPOINTS = {
    'com': 'https://rdap.verisign.com/com/v1/domain/{domain}',
    'net': 'https://rdap.verisign.com/net/v1/domain/{domain}',
}


def is_domain_not_found(exc: Exception) -> bool:
    """判断异常是否代表"域名未注册"（而非网络/服务器问题）。"""
    msg = str(exc).lower()
    if isinstance(exc, whois.exceptions.WhoisDomainNotFoundError):
        # python-whois 也会用此异常表示"该 TLD 没有 WHOIS 服务器"，那不是未注册。
        return not ('no whois server' in msg or 'no whois server is known' in msg)
    return any(marker in msg for marker in _NOT_FOUND_MARKERS)


def is_timeout_error(value) -> bool:
    """识别 WHOIS/套接字超时，避免被空响应误判为未注册。"""
    if isinstance(value, (socket.timeout, TimeoutError)):
        return True
    message = str(value).lower()
    return any(marker in message for marker in ('timed out', 'timeout', 'time out', '超时'))


def _whois_response_text(whois_data) -> str:
    """取 python-whois 返回对象中的原始文本，兼容旧版字典对象。"""
    if whois_data is None:
        return ''
    text = getattr(whois_data, 'text', None)
    if text:
        return str(text)
    if isinstance(whois_data, dict):
        raw = whois_data.get('raw')
        return str(raw) if raw else ''
    return ''


def _whois_field(whois_data, field: str):
    """读取 WHOIS 字段，兼容 WhoisEntry 属性和普通字典返回值。"""
    if isinstance(whois_data, dict):
        return whois_data.get(field)
    return getattr(whois_data, field, None)


def has_registration_evidence(whois_data) -> bool:
    """WHOIS 对象至少包含一项注册证据，才能判定域名已注册。"""
    if not whois_data:
        return False
    fields = (
        'domain_name', 'registrar', 'creation_date', 'expiration_date',
        'updated_date', 'name_servers', 'status',
    )
    return any(_whois_field(whois_data, field) for field in fields)


def _status_values(value) -> list[str]:
    """将 python-whois 的字符串/列表状态统一为去重后的文本列表。"""
    values = value if isinstance(value, (list, tuple, set)) else [value]
    normalized = []
    for item in values:
        if item is None:
            continue
        for part in re.split(r'[,;\n]+', str(item)):
            text = part.strip()
            if text and text not in normalized:
                normalized.append(text)
    return normalized


def format_whois_status(value) -> str | None:
    """格式化 WHOIS 原始状态，保留 clientHold 等注册局状态名。"""
    values = _status_values(value)
    return ', '.join(values) if values else None


def get_hold_statuses(value) -> list[str]:
    """提取会导致域名停止解析的 WHOIS 状态（clientHold/serverHold）。"""
    held = []
    for status in _status_values(value):
        key = re.sub(r'[^a-z]', '', status.lower())
        for hold_key, label in _HOLD_STATUS_KEYS.items():
            if hold_key in key and label not in held:
                held.append(label)
    return held


def _registration_lookup_domain(domain: str) -> str:
    """返回注册局可查询的域名；当前 RDAP 覆盖的 .com/.net 取主域名。"""
    normalized = domain.lower().rstrip('.')
    labels = normalized.split('.')
    if len(labels) > 2 and labels[-1] in _RDAP_ENDPOINTS:
        return '.'.join(labels[-2:])
    return normalized


def _base_whois_result(domain: str, status: str, error: str | None,
                       raw_response: str | None = None) -> dict:
    return {
        'domain': domain, 'status': status,
        'registrar': None, 'registration_date': None, 'expiration_date': None,
        'updated_date': None, 'name_servers': None, 'dnssec': None,
        'whois_status': None, 'hold_status': None, 'error': error,
        'raw_response': raw_response,
    }


def not_registered_result(domain: str, raw_response: str | None = None) -> dict:
    return _base_whois_result(
        domain, 'not_registered', NOT_REGISTERED_MESSAGE, raw_response=raw_response,
    )


def timeout_result(domain: str, error: str = WHOIS_TIMEOUT_MESSAGE,
                   raw_response: str | None = None) -> dict:
    return _base_whois_result(domain, 'timeout', error, raw_response=raw_response)


def failed_result(domain: str, error: str | None, raw_response: str | None = None) -> dict:
    return _base_whois_result(domain, 'failed', error, raw_response=raw_response)


def _whois_success_result(domain: str, whois_data) -> dict:
    """将已确认存在注册证据的 WHOIS 对象整理为统一结果。"""
    result = {
        'domain': domain, 'status': 'success',
        'registrar': None, 'registration_date': None, 'expiration_date': None,
        'updated_date': None, 'name_servers': None, 'dnssec': None,
        'whois_status': format_whois_status(_whois_field(whois_data, 'status')),
        'hold_status': None, 'error': None,
        'raw_response': _whois_response_text(whois_data) or None,
    }

    registrar = _whois_field(whois_data, 'registrar')
    if registrar:
        result['registrar'] = str(registrar)[:100]

    for field, key in [('creation_date', 'registration_date'),
                       ('expiration_date', 'expiration_date'),
                       ('updated_date', 'updated_date')]:
        val = _whois_field(whois_data, field)
        if val:
            if isinstance(val, list):
                val = val[0]
            if hasattr(val, 'strftime'):
                result[key] = val.strftime('%Y-%m-%d')

    name_servers = _whois_field(whois_data, 'name_servers')
    if name_servers:
        ns = name_servers if isinstance(name_servers, list) else [name_servers]
        result['name_servers'] = ', '.join([str(n)[:30] for n in ns[:5]])

    dnssec = _whois_field(whois_data, 'dnssec')
    if dnssec:
        result['dnssec'] = str(dnssec)[:50]

    return result


def _rdap_entity_name(entities) -> str | None:
    """从 RDAP 实体的 vCard 中提取注册商名称。"""
    for entity in entities or []:
        if 'registrar' not in entity.get('roles', []):
            continue
        vcard = entity.get('vcardArray', [])
        properties = vcard[1] if len(vcard) > 1 and isinstance(vcard[1], list) else []
        for prop in properties:
            if len(prop) >= 4 and prop[0] in {'fn', 'org'}:
                return str(prop[3])[:100]
    return None


def _rdap_success_result(domain: str, data: dict) -> dict:
    """把标准 RDAP 域名响应整理为现有 WHOIS 结果结构。"""
    result = _base_whois_result(domain, 'success', None)
    result['raw_response'] = json.dumps(data, ensure_ascii=False, indent=2)
    result['registrar'] = _rdap_entity_name(data.get('entities'))
    result['whois_status'] = format_whois_status(data.get('status'))
    result['name_servers'] = ', '.join(
        str(server.get('ldhName', ''))[:30]
        for server in data.get('nameservers', [])[:5]
        if server.get('ldhName')
    ) or None
    secure_dns = data.get('secureDNS') or {}
    if 'delegationSigned' in secure_dns:
        result['dnssec'] = 'signedDelegation' if secure_dns['delegationSigned'] else 'unsigned'
    event_fields = {
        'registration': 'registration_date',
        'expiration': 'expiration_date',
        'last changed': 'updated_date',
    }
    for event in data.get('events', []):
        target = event_fields.get(str(event.get('eventAction', '')).lower())
        if target and event.get('eventDate'):
            result[target] = str(event['eventDate'])[:10]
    return result


def _query_rdap_fallback(domain: str, timeout: float) -> dict | None:
    """WHOIS 不可用时查询注册局 RDAP；当前覆盖 Verisign 的 .com/.net。"""
    lookup_domain = _registration_lookup_domain(domain)
    tld = lookup_domain.rsplit('.', 1)[-1]
    endpoint = _RDAP_ENDPOINTS.get(tld)
    if not endpoint:
        return None
    request = urllib.request.Request(
        endpoint.format(domain=lookup_domain),
        headers={'Accept': 'application/rdap+json', 'User-Agent': 'domain-checker/2.6'},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))
        if not data.get('ldhName') and not data.get('handle'):
            return failed_result(domain, 'RDAP 未返回有效注册数据')
        return _rdap_success_result(domain, data)
    except urllib.error.HTTPError as exc:
        try:
            raw_response = exc.read().decode('utf-8', errors='replace') or None
        except Exception:
            raw_response = None
        if exc.code == 404:
            return not_registered_result(domain, raw_response=raw_response)
        return failed_result(
            domain, f'RDAP 请求失败：HTTP {exc.code}', raw_response=raw_response,
        )
    except (socket.timeout, TimeoutError):
        return timeout_result(domain, 'RDAP 查询超时，无法确认域名状态')
    except Exception as exc:
        return failed_result(domain, f'RDAP 回退失败：{str(exc)[:80]}')


def get_proxies() -> dict:
    """获取代理配置（当前 python-whois 不使用，保留给后续 HTTP 类平台）。"""
    with config_lock:
        if not CONFIG.get('proxy_enabled') or not CONFIG.get('proxy_url'):
            return None

    proxies = {
        'http': CONFIG['proxy_url'],
        'https': CONFIG['proxy_url']
    }
    return proxies


def check_domain_resolved(domain: str, timeout: float | None = None) -> dict:
    """检查域名当前是否有DNS解析。

    本函数仅在WHOIS查询成功（域名已注册）后调用。
    DNS查询不到解析记录并不代表"域名不存在"，真实的含义是：
    域名已注册，但当前无解析 —— 通常是被注册局/注册商停止解析（如
    serverHold/clientHold 冻结），或尚未配置DNS记录。
    """
    result = {'resolved': None, 'dns_records': [], 'http_accessible': None, 'block_reason': None}

    if timeout is None:
        with config_lock:
            timeout = normalize_timeout(CONFIG.get('timeout', 15))
    else:
        timeout = max(1.0, float(timeout))

    try:
        import dns.resolver
        resolver = dns.resolver.Resolver()
        resolver.timeout = timeout
        resolver.lifetime = timeout

        try:
            answers = resolver.resolve(domain, 'A')
            result['resolved'] = True
            result['dns_records'] = [str(rdata) for rdata in answers]
        except dns.resolver.NXDOMAIN:
            result['resolved'] = False
            result['block_reason'] = '未解析（NXDOMAIN）：域名已注册但DNS中无解析记录，疑似已被停止解析/冻结'
        except dns.resolver.NoAnswer:
            result['resolved'] = False
            result['block_reason'] = '未解析：域名已注册但未配置A记录（无网站解析）'
        except dns.resolver.NoNameservers:
            result['resolved'] = False
            result['block_reason'] = '未解析：权威DNS服务器异常，无法获取解析结果'
        except dns.resolver.Timeout:
            result['resolved'] = None
            result['block_reason'] = 'DNS查询超时，解析状态未知'
        except Exception as e:
            result['resolved'] = None
            result['block_reason'] = f'DNS查询异常: {str(e)[:60]}'

        if result['resolved']:
            try:
                import urllib.request
                url = f'http://{domain}'
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                urllib.request.urlopen(req, timeout=timeout)
                result['http_accessible'] = True
            except Exception:
                result['http_accessible'] = False
    except Exception:
        result['block_reason'] = '缺少依赖'

    return result


def query_whois_with_retry(domain: str, query_mode: str = 'unlimited', timeout: int | None = None) -> dict:
    """带重试机制的 WHOIS 查询；模式只影响主动等待，不改变判断规则。"""
    query_mode = normalize_query_mode(query_mode)
    with config_lock:
        cfg = CONFIG.copy()
    cfg['timeout'] = normalize_timeout(timeout if timeout is not None else cfg.get('timeout', 15))
    cfg['max_retries'] = max(1, int(cfg.get('max_retries', 3)))

    mode_config = QUERY_MODES[query_mode]
    if mode_config['rate_limit_delay'] is not None:
        cfg['rate_limit_delay'] = min(float(cfg['rate_limit_delay']), mode_config['rate_limit_delay'])
        cfg['retry_delay'] = min(float(cfg['retry_delay']), mode_config['retry_delay'])
    if query_mode == 'unlimited':
        rate_jitter = (0.0, 0.0)
        retry_jitter = 0.0
    elif query_mode == 'quick':
        rate_jitter = (0.02, 0.08)
        retry_jitter = 0.3
    else:
        rate_jitter = (0.1, 0.3)
        retry_jitter = 1.0

    last_error = None
    last_status = 'failed'
    rdap_attempted = False
    lookup_domain = _registration_lookup_domain(domain)

    for attempt in range(cfg['max_retries']):
        # 检查暂停
        task_id = getattr(threading.current_thread(), 'task_id', None)
        if task_id and task_pause_flags.get(task_id, False):
            while task_pause_flags.get(task_id, False):
                time.sleep(0.5)

        try:
            rate_wait = max(0, cfg['rate_limit_delay']) + random.uniform(*rate_jitter)
            if task_id:
                append_task_log(
                    task_id,
                    'info',
                    f'[{domain}] WHOIS 第 {attempt + 1}/{cfg["max_retries"]} 次尝试，限流等待 {rate_wait:.1f} 秒',
                )
            if rate_wait > 0:
                time.sleep(rate_wait)

            try:
                # 关闭 python-whois 对套接字错误的吞异常行为，超时才能与未注册区分。
                w = whois.whois(
                    lookup_domain,
                    timeout=cfg['timeout'],
                    ignore_socket_errors=False,
                )
            except TypeError:
                try:
                    # 兼容不支持 ignore_socket_errors 的旧版 python-whois。
                    w = whois.whois(lookup_domain, timeout=cfg['timeout'])
                except TypeError:
                    # 极旧版本连 timeout 参数也不支持，只能退回库默认超时。
                    w = whois.whois(lookup_domain)

            response_text = _whois_response_text(w)
            if is_timeout_error(response_text):
                last_status = 'timeout'
                last_error = WHOIS_TIMEOUT_MESSAGE
                logger.warning(f"[{domain}] {last_error}")
            elif is_domain_not_found(response_text):
                logger.info(f"[{domain}] {NOT_REGISTERED_MESSAGE}")
                return not_registered_result(domain)
            elif not has_registration_evidence(w):
                last_status = 'failed'
                last_error = WHOIS_EMPTY_RESPONSE_MESSAGE
                logger.warning(f"[{domain}] {last_error}")
            else:
                result = _whois_success_result(domain, w)
                logger.info(f"[{domain}] WHOIS成功")
                if task_id:
                    append_task_log(task_id, 'info', f'[{domain}] WHOIS 查询成功，开始检查 DNS 解析')
                return result

        except whois.exceptions.WhoisDomainNotFoundError as e:
            # 该异常通常是未注册，但"没有可用 WHOIS 服务器"不是注册状态结论。
            if is_domain_not_found(e):
                logger.info(f"[{domain}] {NOT_REGISTERED_MESSAGE}")
                return not_registered_result(domain)
            last_status = 'failed'
            last_error = f'WHOIS查询失败: {str(e)[:80]}'
            logger.warning(f"[{domain}] {last_error}")
        except whois.exceptions.WhoisQuotaExceededError as e:
            last_status = 'failed'
            last_error = f'查询配额超限: {str(e)[:50]}'
            logger.warning(f"[{domain}] {last_error}")
        except whois.exceptions.PywhoisError as e:
            if is_timeout_error(e):
                last_status = 'timeout'
                last_error = WHOIS_TIMEOUT_MESSAGE
                logger.warning(f"[{domain}] {last_error}")
            elif is_domain_not_found(e):
                logger.info(f"[{domain}] {NOT_REGISTERED_MESSAGE}")
                return not_registered_result(domain)
            else:
                last_status = 'failed'
                last_error = f'WHOIS解析错误: {str(e)[:80]}'
                logger.warning(f"[{domain}] {last_error}")
        except (socket.timeout, TimeoutError):
            last_status = 'timeout'
            last_error = WHOIS_TIMEOUT_MESSAGE
            logger.warning(f"[{domain}] {last_error}")
        except Exception as e:
            if is_timeout_error(e):
                last_status = 'timeout'
                last_error = WHOIS_TIMEOUT_MESSAGE
                logger.warning(f"[{domain}] {last_error}")
            elif is_domain_not_found(e):
                logger.info(f"[{domain}] {NOT_REGISTERED_MESSAGE}")
                return not_registered_result(domain)
            else:
                last_status = 'failed'
                if isinstance(e, socket.gaierror):
                    last_error = '网络解析失败：无法解析 WHOIS 服务器地址'
                elif isinstance(e, (ConnectionError, OSError)):
                    last_error = f'网络连接失败：{str(e)[:80]}'
                else:
                    last_error = f'查询异常: {str(e)[:80]}'
                logger.warning(f"[{domain}] {last_error}")

        if not rdap_attempted:
            rdap_attempted = True
            rdap_result = _query_rdap_fallback(domain, cfg['timeout'])
            if rdap_result and rdap_result['status'] in {'success', 'not_registered'}:
                logger.info(f"[{domain}] WHOIS 不可用，RDAP 回退查询成功")
                if task_id:
                    append_task_log(task_id, 'info', f'[{domain}] WHOIS 不可用，RDAP 回退查询成功')
                return rdap_result
            if rdap_result and task_id:
                append_task_log(task_id, 'warn', f'[{domain}] {rdap_result["error"]}')

        if task_id:
            append_task_log(task_id, 'warn', f'[{domain}] 第 {attempt + 1} 次尝试失败：{last_error}')

        if attempt < cfg['max_retries'] - 1:
            wait_time = cfg['retry_delay'] * (attempt + 1) + random.uniform(0, retry_jitter)
            logger.info(f"[{domain}] 重试 ({attempt + 2}/{cfg['max_retries']}), 等待 {wait_time:.1f}s")
            if task_id:
                append_task_log(task_id, 'info', f'[{domain}] {wait_time:.1f} 秒后重试')
            if wait_time > 0:
                time.sleep(wait_time)

    logger.error(f"[{domain}] 查询结束: {last_error}")
    return timeout_result(domain, last_error) if last_status == 'timeout' else failed_result(domain, last_error)


def process_single_domain(domain: str, task_id: 'str | None' = None,
                          query_mode: str = 'unlimited', query_timeout: int | None = None) -> dict:
    """处理单个域名：格式校验 → WHOIS 查询 → DNS 解析检查。

    传入 task_id 时会将结果实时写入对应内存任务（供 Web 端轮询展示）。
    """
    domain = domain.strip().lower()
    started = time.perf_counter()
    query_time = datetime.now().replace(microsecond=0).isoformat(sep=' ')
    logger.info(f"[{domain}] 开始处理")
    if task_id:
        append_task_log(task_id, 'info', f'[{domain}] 开始查询')

    resolve_result = {'resolved': None, 'block_reason': None, 'dns_records': None}
    if not is_valid_domain(domain):
        logger.warning(f"[{domain}] 格式无效")
        result = {
            'domain': domain, 'status': 'invalid',
            'registrar': None, 'registration_date': None, 'expiration_date': None,
            'updated_date': None, 'name_servers': None, 'dnssec': None,
            'whois_status': None, 'hold_status': None,
            'error': '域名格式无效', 'raw_response': None,
            'resolved': None, 'block_reason': None, 'dns_records': None
        }
    else:
        whois_result = query_whois_with_retry(
            domain,
            query_mode=normalize_query_mode(query_mode),
            timeout=query_timeout,
        )
        hold_statuses = get_hold_statuses(whois_result.get('whois_status'))
        if whois_result['status'] == 'success':
            whois_result['hold_status'] = ', '.join(hold_statuses) if hold_statuses else None
            if hold_statuses:
                resolve_result = {
                    'resolved': False,
                    'block_reason': (
                        f"停止解析（域名被封）：WHOIS 状态 {', '.join(hold_statuses)}，"
                        '注册商/注册局已暂停域名解析'
                    ),
                    'dns_records': [],
                }
                logger.warning(f"[{domain}] {resolve_result['block_reason']}")
            else:
                resolve_result = check_domain_resolved(domain, timeout=query_timeout)
                if resolve_result['resolved']:
                    logger.info(f"[{domain}] DNS正常: {resolve_result['dns_records'][:1]}")
                else:
                    logger.warning(f"[{domain}] DNS异常: {resolve_result['block_reason']}")

        result = {**whois_result, **resolve_result}

    duration = time.perf_counter() - started
    result['query_time'] = query_time
    result['query_duration_seconds'] = round(duration, 2)

    # 更新任务
    if task_id:
        with task_lock:
            if task_id in task_storage:
                task_storage[task_id]['refresh'] = True

                found = False
                for i, r in enumerate(task_storage[task_id]['results']):
                    if r['domain'] == domain:
                        task_storage[task_id]['results'][i] = result
                        found = True
                        break

                if not found:
                    task_storage[task_id]['results'].append(result)
                    task_storage[task_id]['completed'] += 1

                # 日志
                log_level = 'info'
                if result['status'] in {'not_registered', 'timeout'}:
                    log_level = 'warn'
                elif result['status'] != 'success':
                    log_level = 'error'
                elif resolve_result['resolved'] is False:
                    log_level = 'warn'

                detail = f"{'完成' if result['status'] == 'success' else '结束'}，耗时 {duration:.2f} 秒"
                if result.get('registrar'):
                    detail += f"，注册商 {result['registrar']}"
                if resolve_result['resolved'] is True:
                    detail += '，DNS 正常'
                elif resolve_result['resolved'] is False:
                    detail += f"，{resolve_result['block_reason']}"
                if result['error']:
                    detail += f"，{result['error']}"
                task_storage[task_id]['logs'].append({
                    'time': datetime.now().strftime('%H:%M:%S'),
                    'level': log_level,
                    'message': f'[{domain}] {detail}',
                })

    return result
