"""批量查询与重查任务的异步编排。"""

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .checker import process_single_domain
from .db import save_history, save_results, update_history_counts
from .settings import (
    CONFIG,
    PLATFORMS,
    QUERY_MODES,
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
        'query_duration_seconds': None,
    }


def _worker_config(query_mode: str = 'unlimited', query_timeout: int | None = None) -> dict:
    with config_lock:
        cfg = {
            'max_workers': max(1, int(CONFIG['max_workers'])),
            'max_retries': max(1, int(CONFIG['max_retries'])),
            'rate_limit_delay': float(CONFIG['rate_limit_delay']),
            'timeout': normalize_timeout(query_timeout if query_timeout is not None else CONFIG['timeout']),
            'retry_delay': float(CONFIG['retry_delay']),
        }
    query_mode = normalize_query_mode(query_mode)
    mode_config = QUERY_MODES[query_mode]
    if mode_config['rate_limit_delay'] is not None:
        cfg['rate_limit_delay'] = min(cfg['rate_limit_delay'], mode_config['rate_limit_delay'])
        cfg['retry_delay'] = min(cfg['retry_delay'], mode_config['retry_delay'])
    if mode_config.get('max_retries') is not None:
        cfg['max_retries'] = min(cfg['max_retries'], int(mode_config['max_retries']))
    return cfg


def _process_domain_worker(domain: str, task_id: str, query_mode: str = 'unlimited',
                           query_timeout: int | None = None, platform: str = 'rdap'):
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
        'query_duration_seconds': 0,
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
              query_timeout: int | None = None, platform: str = 'rdap') -> None:
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f'domain-{task_id}') as executor:
        futures = {
            executor.submit(
                _process_domain_worker, domain, task_id, query_mode, query_timeout, platform,
            ): domain
            for domain in domains
        }
        for future in as_completed(futures):
            domain = futures[future]
            try:
                future.result()
            except Exception as exc:
                logger.exception(f"[{domain}] worker 处理异常")
                _record_worker_failure(domain, task_id, exc)
            if track_operation:
                with task_lock:
                    task = task_storage.get(task_id)
                    if task and task['status'] != 'cancelled':
                        task['operation_completed'] += 1


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


def process_domains_async(domains: list, task_id: str):
    """使用受控线程池处理一批域名，完成后写入数据库。"""
    started = time.perf_counter()
    total = len(domains)
    with config_lock:
        default_timeout = CONFIG['timeout']
    with task_lock:
        task = task_storage[task_id]
        query_mode = normalize_query_mode(task.get('query_mode', 'unlimited'))
        query_timeout = normalize_timeout(task.get('query_timeout', default_timeout))
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
        platform=platform,
    )

    completed = _complete_operation(task_id, started, '查询', len(domains))
    if completed is None:
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
    with task_lock:
        task = task_storage.get(task_id, {})
        query_mode = normalize_query_mode(task.get('query_mode', 'unlimited'))
        query_timeout = normalize_timeout(task.get('query_timeout', default_timeout))
        platform = task.get('platform', 'rdap')
    cfg = _worker_config(query_mode, query_timeout)
    task_pause_flags.setdefault(task_id, False)
    append_task_log(task_id, 'info', f"开始重新查询 {len(domains)} 个域名，并发线程 {cfg['max_workers']}，{QUERY_MODES[query_mode]['name']}")
    _run_pool(
        domains, task_id, cfg['max_workers'], track_operation=True,
        query_mode=query_mode, query_timeout=query_timeout,
        platform=platform,
    )

    completed = _complete_operation(task_id, started, '重新查询', len(domains))
    if completed is None:
        return
    results, success_count, failed_count = completed
    save_results(task_id, results)
    update_history_counts(task_id, success_count, failed_count)


def generate_task_id() -> str:
    """生成任务ID：月日时分秒 + 3位随机数。"""
    return datetime.now().strftime('%m%d%H%M%S') + str(random.randint(100, 999))
