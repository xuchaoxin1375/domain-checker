"""内存中的批量任务运行状态。

注意：运行中的任务数据只存在于当前进程内存中，服务重启后不能继续运行；
已完成任务可由 Web 重查入口从 SQLite 历史按需恢复。这里的对象被多个线程并发访问，
修改时请持有各自的锁。
"""

import threading
from datetime import datetime

# task_id -> {status,total,completed,results,logs,operation,operation_total,
#             operation_completed,operation_started_at,duration_seconds,...}
task_storage = {}
task_lock = threading.Lock()

# task_id -> bool，暂停/继续标志（不要求与 task_lock 同步，普通字典操作即可）
task_pause_flags = {}


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
