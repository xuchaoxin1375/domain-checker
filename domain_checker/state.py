"""内存中的批量任务运行状态。

注意：运行中的任务数据只存在于当前进程内存中，服务重启后不能继续运行；
已完成任务可由 Web 重查入口从 SQLite 历史按需恢复。这里的对象被多个线程并发访问，
修改时请持有各自的锁。
"""

import threading
import time
from datetime import datetime

# task_id -> {status,total,completed,results（含 query_state）,logs,operation,operation_total,
#             operation_completed,operation_started_at,duration_seconds,...}
task_storage = {}
task_lock = threading.Lock()

# task_id -> bool，暂停/继续标志（不要求与 task_lock 同步，普通字典操作即可）
task_pause_flags = {}


def task_is_cancelled(task_id: str) -> bool:
    """任务是否已取消或已从内存移除。"""
    with task_lock:
        task = task_storage.get(task_id)
        return task is None or task.get('status') == 'cancelled'


def wait_for_task_resume(task_id: str, interval: float = 0.1) -> bool:
    """协作式等待任务继续；取消时返回 False，避免线程永久停在暂停循环。"""
    while task_pause_flags.get(task_id, False):
        if task_is_cancelled(task_id):
            return False
        time.sleep(interval)
    return not task_is_cancelled(task_id)


def get_task_paused_duration(task_id: str | None) -> float:
    """返回任务当前累计暂停秒数，包含尚未恢复的本次暂停。"""
    if not task_id:
        return 0.0
    with task_lock:
        task = task_storage.get(task_id)
        if not task:
            return 0.0
        duration = float(task.get('paused_duration_seconds', 0.0))
        paused_at = task.get('_paused_started_monotonic')
        if paused_at is not None:
            duration += max(0.0, time.monotonic() - paused_at)
        return duration


def append_task_log(task_id: str, level: str, message: str) -> None:
    """线程安全地追加一条面向网页的任务日志。"""
    with task_lock:
        task = task_storage.get(task_id)
        if task is not None:
            task['logs'].append({
                'time': datetime.now().strftime('%H:%M:%S'),
                'level': level,
                'message': message,
            })
