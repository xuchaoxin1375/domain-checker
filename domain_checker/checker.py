"""域名查询核心：WHOIS 查询、DNS 解析检查、单域名处理流水线。"""

import logging
import random
import threading
import time
from datetime import datetime

import whois

from .domains import is_valid_domain
from .settings import CONFIG, config_lock
from .state import task_lock, task_pause_flags, task_storage

logger = logging.getLogger(__name__)


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


def check_domain_resolved(domain: str) -> dict:
    """检查域名当前是否有DNS解析。

    本函数仅在WHOIS查询成功（域名已注册）后调用。
    DNS查询不到解析记录并不代表"域名不存在"，真实的含义是：
    域名已注册，但当前无解析 —— 通常是被注册局/注册商停止解析（如
    serverHold/clientHold 冻结），或尚未配置DNS记录。
    """
    result = {'resolved': None, 'dns_records': [], 'http_accessible': None, 'block_reason': None}

    try:
        import dns.resolver
        resolver = dns.resolver.Resolver()
        resolver.timeout = 5
        resolver.lifetime = 5

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
                urllib.request.urlopen(req, timeout=5)
                result['http_accessible'] = True
            except Exception:
                result['http_accessible'] = False
    except Exception:
        result['block_reason'] = '缺少依赖'

    return result


def query_whois_with_retry(domain: str) -> dict:
    """带重试机制的WHOIS查询。"""
    with config_lock:
        cfg = CONFIG.copy()

    last_error = None

    for attempt in range(cfg['max_retries']):
        # 检查暂停
        task_id = getattr(threading.current_thread(), 'task_id', None)
        if task_id and task_pause_flags.get(task_id, False):
            while task_pause_flags.get(task_id, False):
                time.sleep(0.5)

        try:
            time.sleep(cfg['rate_limit_delay'] + random.uniform(0.1, 0.3))

            w = whois.whois(domain)

            result = {
                'domain': domain, 'status': 'success',
                'registrar': None, 'registration_date': None, 'expiration_date': None,
                'updated_date': None, 'name_servers': None, 'dnssec': None, 'error': None
            }

            if w:
                if w.registrar:
                    result['registrar'] = str(w.registrar)[:100]

                for field, key in [('creation_date', 'registration_date'),
                                   ('expiration_date', 'expiration_date'),
                                   ('updated_date', 'updated_date')]:
                    val = getattr(w, field, None)
                    if val:
                        if isinstance(val, list):
                            val = val[0]
                        if hasattr(val, 'strftime'):
                            result[key] = val.strftime('%Y-%m-%d')

                if w.name_servers:
                    ns = w.name_servers if isinstance(w.name_servers, list) else [w.name_servers]
                    result['name_servers'] = ', '.join([str(n)[:30] for n in ns[:5]])

                if w.dnssec:
                    result['dnssec'] = str(w.dnssec)[:50]

            logger.info(f"[{domain}] WHOIS成功")
            return result

        except whois.exceptions.PywhoisError as e:
            last_error = f'WHOIS解析错误: {str(e)[:80]}'
            logger.warning(f"[{domain}] {last_error}")
        except whois.exceptions.WhoisDomainNotFoundError as e:
            last_error = f'域名不存在: {str(e)[:50]}'
            logger.warning(f"[{domain}] {last_error}")
        except whois.exceptions.WhoisQuotaExceededError as e:
            last_error = f'查询配额超限: {str(e)[:50]}'
            logger.warning(f"[{domain}] {last_error}")
        except Exception as e:
            last_error = f'查询异常: {str(e)[:80]}'
            logger.warning(f"[{domain}] {last_error}")

        if attempt < cfg['max_retries'] - 1:
            wait_time = cfg['retry_delay'] * (attempt + 1) + random.uniform(0, 1)
            logger.info(f"[{domain}] 重试 ({attempt + 2}/{cfg['max_retries']}), 等待 {wait_time:.1f}s")
            time.sleep(wait_time)

    logger.error(f"[{domain}] 查询失败: {last_error}")
    return {
        'domain': domain, 'status': 'failed',
        'registrar': None, 'registration_date': None, 'expiration_date': None,
        'updated_date': None, 'name_servers': None, 'dnssec': None,
        'error': last_error
    }


def process_single_domain(domain: str, task_id: 'str | None' = None) -> dict:
    """处理单个域名：格式校验 → WHOIS 查询 → DNS 解析检查。

    传入 task_id 时会将结果实时写入对应内存任务（供 Web 端轮询展示）。
    """
    domain = domain.strip().lower()
    logger.info(f"[{domain}] 开始处理")

    if not is_valid_domain(domain):
        logger.warning(f"[{domain}] 格式无效")
        return {
            'domain': domain, 'status': 'invalid',
            'registrar': None, 'registration_date': None, 'expiration_date': None,
            'updated_date': None, 'name_servers': None, 'dnssec': None,
            'error': '域名格式无效', 'resolved': None, 'block_reason': None, 'dns_records': None
        }

    whois_result = query_whois_with_retry(domain)

    resolve_result = {'resolved': None, 'block_reason': None, 'dns_records': None}
    if whois_result['status'] == 'success':
        resolve_result = check_domain_resolved(domain)
        if resolve_result['resolved']:
            logger.info(f"[{domain}] DNS正常: {resolve_result['dns_records'][:1]}")
        else:
            logger.warning(f"[{domain}] DNS异常: {resolve_result['block_reason']}")

    result = {**whois_result, **resolve_result}

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
                if result['status'] != 'success':
                    log_level = 'error'
                elif resolve_result['resolved'] is False:
                    log_level = 'warn'

                task_storage[task_id]['logs'].append({
                    'time': datetime.now().strftime('%H:%M:%S'),
                    'level': log_level,
                    'message': f"{'✓' if result['status'] == 'success' else '✗'} {domain}"
                               + (f" [未解析: {resolve_result['block_reason']}]"
                                  if resolve_result['resolved'] is False else "")
                               + (f" - {result['error']}" if result['error'] else "")
                })

    return result
