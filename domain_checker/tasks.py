"""批量查询与重查任务的异步编排。"""

import logging
import random
import threading
import time
from datetime import datetime
from queue import Empty, Queue

from .checker import TASK_CANCELLED_MESSAGE, process_single_domain
from .db import (
    finalize_cancelled_history,
    find_recent_success_results,
    save_history,
    save_result,
    save_results,
    update_history_counts,
)
from .settings import (
    CONFIG,
    PLATFORMS,
    QUERY_MODES,
    coerce_config_value,
    config_lock,
    is_platform_implemented,
    normalize_query_mode,
    normalize_timeout,
)
from .state import (
    append_task_log,
    task_lock,
    task_pause_flags,
    task_storage,
    wait_for_task_resume,
)

logger = logging.getLogger(__name__)


def create_queued_result(domain: str) -> dict:
    """创建表格可立即展示的排队占位结果。"""
    return {
        'domain': domain, 'query_state': 'queued', 'status': None,
        'registrar': None, 'contact_email': None,
        'registration_date': None, 'expiration_date': None, 'updated_date': None,
        'name_servers': None, 'dnssec': None, 'whois_status': None, 'hold_status': None,
        'resolved': None, 'block_reason': None, 'dns_records': None,
        'error': None, 'raw_response': None, 'query_time': None,
        'query_duration_seconds': None, 'reused': False,
    }


def _config_int(key: str, default: int) -> int:
    """读取整型配置；非法值回落到默认值，绝不让任务线程因配置异常退出。"""
    try:
        return int(coerce_config_value(key, CONFIG.get(key, default)))
    except (TypeError, ValueError):
        return default


def _config_float(key: str, default: float) -> float:
    """读取浮点配置；非法值回落到默认值。"""
    try:
        return float(coerce_config_value(key, CONFIG.get(key, default)))
    except (TypeError, ValueError):
        return default


def _worker_config(query_mode: str = 'unlimited', query_timeout: int | None = None) -> dict:
    with config_lock:
        cfg = {
            'max_workers': _config_int('max_workers', 5),
            'max_retries': _config_int('max_retries', 3),
            'rate_limit_delay': _config_float('rate_limit_delay', 1.0),
            'timeout': normalize_timeout(query_timeout if query_timeout is not None else CONFIG['timeout']),
            'retry_delay': _config_float('retry_delay', 2.0),
        }
    query_mode = normalize_query_mode(query_mode)
    mode_config = QUERY_MODES[query_mode]
    if mode_config['rate_limit_delay'] is not None:
        cfg['rate_limit_delay'] = min(cfg['rate_limit_delay'], mode_config['rate_limit_delay'])
        cfg['retry_delay'] = min(cfg['retry_delay'], mode_config['retry_delay'])
    if mode_config.get('max_retries') is not None:
        cfg['max_retries'] = min(cfg['max_retries'], int(mode_config['max_retries']))
    return cfg


def _persistent_timeout(base_timeout: int, round_index: int) -> int:
    """执着模式逐轮放宽单次请求超时：第 2 轮翻倍，之后继续翻倍并封顶。"""
    factor = 2 ** max(0, int(round_index) - 1)
    return normalize_timeout(int(base_timeout) * factor)


def _needs_persistent_retry(result: dict) -> bool:
    """执着模式仍需重试的结果：查询失败/超时，或注册成功但解析状态未知。"""
    status = result.get('status')
    if status in {'failed', 'timeout'}:
        return True
    return status == 'success' and result.get('resolved') is None


def _reusable_results(domains: list[str]) -> dict[str, dict]:
    """读取复用窗口内的最近成功结果；缓存读取失败不影响正常查询。"""
    with config_lock:
        raw_ttl = CONFIG.get('reuse_recent_seconds', 0)
    try:
        ttl = int(coerce_config_value('reuse_recent_seconds', raw_ttl))
    except ValueError:
        return {}
    if ttl <= 0:
        return {}
    try:
        return find_recent_success_results(domains, ttl)
    except Exception as exc:
        logger.warning(f"读取最近成功结果失败，本次不使用复用: {str(exc)[:80]}")
        return {}


def _process_domain_worker(domain: str, task_id: str, query_mode: str = 'unlimited',
                           query_timeout: int | None = None, platform: str = 'rdap',
                           cached_result: dict | None = None):
    """等待任务可运行后处理域名，并把任务 ID 传给深层重试日志。"""
    if not wait_for_task_resume(task_id):
        return None

    with task_lock:
        task = task_storage.get(task_id)
        if task and task['status'] != 'cancelled':
            for result in task['results']:
                if result['domain'] == domain:
                    result['query_state'] = 'querying'
                    result['query_time'] = datetime.now().replace(microsecond=0).isoformat(sep=' ')
                    result['query_duration_seconds'] = None
                    task['refresh'] = True
                    break

    current_thread = threading.current_thread()
    previous_task_id = getattr(current_thread, 'task_id', None)
    current_thread.task_id = task_id
    try:
        return process_single_domain(
            domain,
            task_id,
            query_mode=normalize_query_mode(query_mode),
            query_timeout=query_timeout,
            platform=platform,
            cached_result=cached_result,
        )
    finally:
        if previous_task_id is None:
            del current_thread.task_id
        else:
            current_thread.task_id = previous_task_id


def _record_worker_failure(domain: str, task_id: str, exc: Exception) -> None:
    """把未预期的 worker 异常收敛为结果，防止整个异步任务永久停滞。"""
    error = f'内部处理异常: {type(exc).__name__}: {str(exc)[:120]}'
    result = {
        'domain': domain, 'query_state': 'completed', 'status': 'failed',
        'registrar': None, 'registration_date': None, 'expiration_date': None,
        'updated_date': None, 'name_servers': None, 'dnssec': None,
        'contact_email': None,
        'whois_status': None, 'hold_status': None,
        'resolved': None, 'block_reason': None, 'dns_records': None,
        'error': error, 'raw_response': None,
        'query_time': datetime.now().replace(microsecond=0).isoformat(sep=' '),
        'query_duration_seconds': 0, 'reused': False,
    }
    with task_lock:
        task = task_storage.get(task_id)
        if not task or task['status'] == 'cancelled':
            return
        task['refresh'] = True
        for index, existing in enumerate(task['results']):
            if existing['domain'] == domain:
                task['results'][index] = result
                if task.get('operation') == 'query' and existing.get('query_state') != 'completed':
                    task['completed'] += 1
                break
        else:
            task['results'].append(result)
            task['completed'] += 1
        task['logs'].append({
            'time': datetime.now().strftime('%H:%M:%S'),
            'level': 'error',
            'message': f'[{domain}] {error}',
        })


def _run_pool(domains: list[str], task_id: str, max_workers: int,
              track_operation: bool = False, query_mode: str = 'unlimited',
              query_timeout: int | None = None, platform: str = 'rdap',
              reuse_map: dict[str, dict] | None = None) -> None:
    """用守护线程池处理域名。

    使用守护线程而不是 ``ThreadPoolExecutor``：调试重载或进程退出时，在途查询线程
    不能阻止旧进程结束，否则旧进程会继续查询并占用数据库/端口，新进程无法正常服务。
    任务仍是协作式暂停/取消；进程退出时未完成任务由下次启动标记为 interrupted。
    """
    reuse_map = reuse_map or {}
    work_queue: Queue[str] = Queue()
    for domain in domains:
        work_queue.put(domain)

    def worker() -> None:
        while True:
            try:
                domain = work_queue.get_nowait()
            except Empty:
                return
            result = None
            try:
                result = _process_domain_worker(
                    domain, task_id, query_mode, query_timeout, platform,
                    reuse_map.get(domain),
                )
            except Exception as exc:
                logger.exception(f"[{domain}] worker 处理异常")
                _record_worker_failure(domain, task_id, exc)
            try:
                # 逐条落库：任务取消或进程中断后，已完成的成功结果仍可被后续查询复用。
                # 取消产生的失败结果不覆盖该域名可能已有的有效历史。
                if result is not None and result.get('error') != TASK_CANCELLED_MESSAGE:
                    save_result(task_id, result)
            except Exception as exc:
                logger.warning(f"[{domain}] 增量保存结果失败，不影响本次查询: {str(exc)[:80]}")
            finally:
                work_queue.task_done()
                if track_operation:
                    with task_lock:
                        task = task_storage.get(task_id)
                        if task and task['status'] != 'cancelled':
                            task['operation_completed'] += 1

    threads = [
        threading.Thread(
            target=worker,
            name=f'domain-{task_id}-{index}',
            daemon=True,
        )
        for index in range(max(1, int(max_workers)))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def _run_persistent_rounds(task_id: str, max_workers: int, query_mode: str,
                           base_timeout: int, platform: str, max_rounds: int) -> None:
    """执着模式：把失败/超时域名自动重新入队，直到得到结论或达到轮数上限。

    每轮把单次请求超时放宽一倍（封顶由 ``normalize_timeout`` 控制），
    并通过 ``persistent_round``/``persistent_retrying`` 向前端暴露当前轮次。
    """
    max_rounds = max(1, int(max_rounds))
    round_index = 1
    while round_index < max_rounds:
        with task_lock:
            task = task_storage.get(task_id)
            if not task or task['status'] == 'cancelled' or not task.get('persistent'):
                return
            candidates = [r['domain'] for r in task['results'] if _needs_persistent_retry(r)]
            if not candidates:
                task['persistent_retrying'] = 0
                return
            round_index += 1
            timeout = _persistent_timeout(base_timeout, round_index)
            task['persistent_round'] = round_index
            task['persistent_retrying'] = len(candidates)
            task['refresh'] = True
            candidate_set = set(candidates)
            for result in task['results']:
                if result['domain'] not in candidate_set:
                    continue
                # 初次查询按完成数推进进度；重开这些行时同步回退，避免完成数超过总数
                if task.get('operation') == 'query' and result.get('query_state') == 'completed':
                    task['completed'] = max(0, task.get('completed', 0) - 1)
                result['query_state'] = 'queued'
                result['query_time'] = None
                result['query_duration_seconds'] = None

        append_task_log(
            task_id,
            'info',
            f"执着模式第 {round_index}/{max_rounds} 轮：重试 {len(candidates)} 个失败、超时或解析未知域名，"
            f"单次超时放宽至 {timeout} 秒",
        )
        _run_pool(
            candidates, task_id, max_workers, track_operation=False,
            query_mode=query_mode, query_timeout=timeout, platform=platform,
        )

    with task_lock:
        task = task_storage.get(task_id)
        if not task or task['status'] == 'cancelled':
            return
        task['persistent_retrying'] = 0
        remaining = sum(1 for result in task['results'] if _needs_persistent_retry(result))
    if remaining:
        append_task_log(
            task_id,
            'warn',
            f"执着模式已达 {max_rounds} 轮上限，仍有 {remaining} 个域名未得到结论",
        )


def _complete_operation(task_id: str, started: float, label: str, work_count: int) -> tuple[list, int, int] | None:
    raw_duration = time.perf_counter() - started
    with task_lock:
        task = task_storage.get(task_id)
        if not task or task['status'] == 'cancelled':
            task_pause_flags.pop(task_id, None)
            return None

        results = list(task['results'])
        success_count = sum(1 for result in results if result['status'] == 'success')
        failed_count = len(results) - success_count
        duration = round(max(0.0, raw_duration - float(task.get('paused_duration_seconds', 0.0))), 2)
        task_pause_flags.pop(task_id, None)
        task['status'] = 'completed'
        task['operation'] = 'completed'
        task['completed_at'] = datetime.now().isoformat()
        task['duration_seconds'] = duration

    average = duration / max(1, work_count)
    append_task_log(
        task_id,
        'info',
        f'{label}完成：成功 {success_count}，其他 {failed_count}，耗时 {duration:.2f} 秒，平均 {average:.2f} 秒/域名',
    )
    return results, success_count, failed_count


def _finalize_cancelled_task(task_id: str) -> None:
    """取消任务后收尾：把已逐条落库的结果计入历史，并标记为已取消。"""
    with task_lock:
        task = task_storage.get(task_id)
        cancelled = bool(task and task.get('status') == 'cancelled')
    if not cancelled:
        return
    try:
        finalize_cancelled_history(task_id)
    except Exception as exc:
        logger.warning(f"[任务{task_id}] 取消后更新历史失败: {str(exc)[:80]}")


def process_domains_async(domains: list, task_id: str):
    """使用受控线程池处理一批域名，完成后写入数据库。"""
    started = time.perf_counter()
    total = len(domains)
    with config_lock:
        default_timeout = CONFIG['timeout']
    persistent_default_rounds = _config_int('persistent_max_rounds', 5)
    with task_lock:
        task = task_storage[task_id]
        query_mode = normalize_query_mode(task.get('query_mode', 'unlimited'))
        query_timeout = normalize_timeout(task.get('query_timeout', default_timeout))
        ignore_cache = bool(task.get('ignore_cache', False))
        persistent = bool(task.get('persistent', False))
        persistent_max_rounds = int(task.get('persistent_max_rounds') or persistent_default_rounds)
    cfg = _worker_config(query_mode, query_timeout)
    # 保留用户在后台线程真正启动前已经发出的暂停请求。
    task_pause_flags.setdefault(task_id, False)

    logger.info(f"[任务{task_id}] 开始处理 {total} 个域名，并发数 {cfg['max_workers']}")
    append_task_log(task_id, 'info', f"任务开始：共 {total} 个域名，并发线程 {cfg['max_workers']}，{QUERY_MODES[query_mode]['name']}")
    append_task_log(
        task_id,
        'info',
        f"查询参数：超时 {cfg['timeout']} 秒，最多尝试 {cfg['max_retries']} 次，请求间隔 {cfg['rate_limit_delay']:.1f} 秒，失败重试等待 {cfg['retry_delay']:.1f} 秒",
    )
    if persistent:
        append_task_log(
            task_id,
            'info',
            f"已开启执着模式：失败/超时/解析未知域名自动重试，最多 {persistent_max_rounds} 轮，单次超时逐轮放宽",
        )

    reuse_map = {} if ignore_cache else _reusable_results(domains)
    if ignore_cache:
        append_task_log(
            task_id,
            'info',
            '已勾选忽略缓存：本任务不使用最近成功结果，全部域名重新联网查询',
        )
    elif reuse_map:
        with config_lock:
            reuse_ttl = CONFIG.get('reuse_recent_seconds', 0)
        append_task_log(
            task_id,
            'info',
            f"命中 {len(reuse_map)} 条 {reuse_ttl} 秒内的成功结果，复用并跳过网络查询；重查操作始终强制联网",
        )

    with task_lock:
        platform = task_storage[task_id].get('platform', 'rdap')
    if not is_platform_implemented(platform):
        append_task_log(
            task_id,
            'warn',
            f"「{PLATFORMS[platform]['name']}」暂未接入专用接口，本任务实际使用 WHOIS 标准协议查询",
        )

    save_history(task_id, domains, 'processing')
    _run_pool(
        domains, task_id, cfg['max_workers'], track_operation=True,
        query_mode=query_mode, query_timeout=query_timeout,
        platform=platform, reuse_map=reuse_map,
    )
    if persistent:
        _run_persistent_rounds(
            task_id, cfg['max_workers'], query_mode, query_timeout, platform,
            persistent_max_rounds,
        )

    completed = _complete_operation(task_id, started, '查询', len(domains))
    if completed is None:
        _finalize_cancelled_task(task_id)
        return
    results, success_count, failed_count = completed
    save_results(task_id, results)
    update_history_counts(task_id, success_count, failed_count)
    logger.info(f"[任务{task_id}] 全部完成")


def retry_domains_async(domains: list[str], task_id: str) -> None:
    """并发重查指定域名，完成后同步刷新内存与历史结果。"""
    started = time.perf_counter()
    with config_lock:
        default_timeout = CONFIG['timeout']
    persistent_default_rounds = _config_int('persistent_max_rounds', 5)
    with task_lock:
        task = task_storage.get(task_id, {})
        query_mode = normalize_query_mode(task.get('query_mode', 'unlimited'))
        query_timeout = normalize_timeout(task.get('query_timeout', default_timeout))
        platform = task.get('platform', 'rdap')
        persistent = bool(task.get('persistent', False))
        persistent_max_rounds = int(task.get('persistent_max_rounds') or persistent_default_rounds)
    cfg = _worker_config(query_mode, query_timeout)
    task_pause_flags.setdefault(task_id, False)
    append_task_log(task_id, 'info', f"开始重新查询 {len(domains)} 个域名，并发线程 {cfg['max_workers']}，{QUERY_MODES[query_mode]['name']}")
    _run_pool(
        domains, task_id, cfg['max_workers'], track_operation=True,
        query_mode=query_mode, query_timeout=query_timeout,
        platform=platform,
    )
    if persistent:
        _run_persistent_rounds(
            task_id, cfg['max_workers'], query_mode, query_timeout, platform,
            persistent_max_rounds,
        )

    completed = _complete_operation(task_id, started, '重新查询', len(domains))
    if completed is None:
        _finalize_cancelled_task(task_id)
        return
    results, success_count, failed_count = completed
    save_results(task_id, results)
    update_history_counts(task_id, success_count, failed_count)


def generate_task_id() -> str:
    """生成任务ID：月日时分秒 + 3位随机数。"""
    return datetime.now().strftime('%m%d%H%M%S') + str(random.randint(100, 999))
