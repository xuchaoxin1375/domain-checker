"""批量查询任务的异步编排。

当前实现为每个域名启动一个守护线程（带启动间隔与限流延迟），
任务结束后把结果写入 SQLite。已知限制见 docs/ARCHITECTURE.md
（如 max_workers 尚未真正生效、无取消中的任务线程回收机制）。
"""

import logging
import random
import threading
import time
from datetime import datetime

from .checker import process_single_domain
from .db import save_history, save_results, update_history_counts
from .state import task_lock, task_pause_flags, task_storage

logger = logging.getLogger(__name__)


def process_domains_async(domains: list, task_id: str):
    """异步处理一批域名，完成后写入数据库。"""
    total = len(domains)
    task_pause_flags[task_id] = False

    logger.info(f"[任务{task_id}] 开始处理 {total} 个域名")

    with task_lock:
        task_storage[task_id]['logs'].append({
            'time': datetime.now().strftime('%H:%M:%S'),
            'level': 'info',
            'message': f'任务开始，共 {total} 个域名'
        })

    # 保存初始历史
    save_history(task_id, domains, 'processing')

    # 直接使用线程处理
    threads = []
    for d in domains:
        while task_pause_flags.get(task_id, False):
            time.sleep(0.3)

        t = threading.Thread(target=process_single_domain, args=(d, task_id))
        t.daemon = True
        t.start()
        threads.append(t)
        time.sleep(0.1)

    # 等待所有线程完成
    for t in threads:
        t.join()

    # 保存结果到数据库
    with task_lock:
        results = list(task_storage[task_id]['results'])
        success_count = sum(1 for r in results if r['status'] == 'success')
        failed_count = len(results) - success_count

        task_pause_flags.pop(task_id, None)
        task_storage[task_id]['status'] = 'completed'
        task_storage[task_id]['completed_at'] = datetime.now().isoformat()
        task_storage[task_id]['logs'].append({
            'time': datetime.now().strftime('%H:%M:%S'),
            'level': 'info',
            'message': f'任务完成！共 {len(results)} 条结果'
        })

    # 在锁外保存到数据库
    save_results(task_id, results)
    update_history_counts(task_id, success_count, failed_count)

    logger.info(f"[任务{task_id}] 全部完成")


def generate_task_id() -> str:
    """生成任务ID：月日时分秒 + 3位随机数。"""
    return datetime.now().strftime('%m%d%H%M%S') + str(random.randint(100, 999))
