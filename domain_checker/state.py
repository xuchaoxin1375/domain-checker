"""内存中的批量任务运行状态。

注意：任务数据只存在于当前进程内存中，服务重启后任务不可恢复；
持久化的历史结果由 db 模块写入 SQLite。这里的对象被多个线程并发访问，
修改时请持有各自的锁。
"""

import threading

# task_id -> {status,total,completed,results,logs,refresh,created_at,completed_at,platform}
task_storage = {}
task_lock = threading.Lock()

# task_id -> bool，暂停/继续标志（不要求与 task_lock 同步，普通字典操作即可）
task_pause_flags = {}

# task_id -> bool，取消标志与暂停分开，避免取消后工作线程永久等待
# （修改时应与 task_pause_flags 一样仅做原子字典操作）
task_cancel_flags = {}
