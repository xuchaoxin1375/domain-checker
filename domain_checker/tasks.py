"""批量查询任务的异步编排。"""

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .checker import process_single_domain
from .db import save_history, save_results, update_history_counts
from .settings import CONFIG, config_lock
from .state import task_lock, task_pause_flags, task_cancel_flags, task_storage

logger = logging.getLogger(__name__)


def _process_one(domain: str, task_id: str) -> None:
    """在线程池工作项中执行一个域名；暂停不会占用新的查询配额。"""
    while task_pause_flags.get(task_id, False) and not task_cancel_flags.get(task_id, False):
        time.sleep(0.3)
    if task_cancel_flags.get(task_id, False):
        return
    # checker 通过该属性让重试过程也遵守暂停语义。
    threading.current_thread().task_id = task_id
    process_single_domain(domain, task_id)


def process_domains_async(domains: list, task_id: str):
    """异步处理一批域名，使用配置的最大并发数，完成后写入数据库。"""
    total = len(domains)
    task_pause_flags[task_id] = False
    task_cancel_flags[task_id] = False
    logger.info(f"[任务{task_id}] 开始处理 {total} 个域名")

    with task_lock:
        task_storage[task_id]['logs'].append({'time': datetime.now().strftime('%H:%M:%S'),
            'level': 'info', 'message': f'任务开始，共 {total} 个域名'})
    save_history(task_id, domains, 'processing')

    with config_lock:
        # 配置来自网页，防御手工修改/旧配置文件中的非法值。
        workers = max(1, min(len(domains), int(CONFIG.get('max_workers', 1))))

    # executor 保证同时运行的域名数不超过 max_workers，并在退出时回收线程。
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f'domain-{task_id}') as executor:
        futures = [executor.submit(_process_one, domain, task_id) for domain in domains]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                logger.exception('[任务%s] 域名处理线程异常', task_id)

    with task_lock:
        results = list(task_storage[task_id]['results'])
        success_count = sum(1 for r in results if r['status'] == 'success')
        failed_count = len(results) - success_count
        cancelled = task_cancel_flags.get(task_id, False)
        task_storage[task_id]['status'] = 'cancelled' if cancelled else 'completed'
        task_storage[task_id]['completed_at'] = datetime.now().isoformat()
        task_storage[task_id]['logs'].append({'time': datetime.now().strftime('%H:%M:%S'),
            'level': 'warn' if cancelled else 'info',
            'message': '任务已取消' if cancelled else f'任务完成！共 {len(results)} 条结果'})

    task_pause_flags.pop(task_id, None)
    task_cancel_flags.pop(task_id, None)
    save_results(task_id, results)
    update_history_counts(task_id, success_count, failed_count)
    logger.info(f"[任务{task_id}] 全部完成")


def generate_task_id() -> str:
    """生成任务ID：月日时分秒 + 3位随机数。"""
    return datetime.now().strftime('%m%d%H%M%S') + str(random.randint(100, 999))
