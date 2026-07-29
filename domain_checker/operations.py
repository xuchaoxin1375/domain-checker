"""服务启动与终止操作日志。"""

import json
import logging
import os
import threading
from datetime import datetime

from .settings import DATA_DIR

logger = logging.getLogger(__name__)

OPERATIONS_LOG_PATH = DATA_DIR / 'operations.log'
_operations_lock = threading.Lock()


def record_operation(action: str, detail: str) -> dict:
    """追加一条 JSON Lines 操作记录；写入失败不影响服务主流程。"""
    entry = {
        'time': datetime.now().replace(microsecond=0).isoformat(sep=' '),
        'action': action,
        'pid': os.getpid(),
        'detail': detail,
    }
    try:
        with _operations_lock, OPERATIONS_LOG_PATH.open('a', encoding='utf-8') as file:
            file.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except OSError as exc:
        logger.warning(f'写入服务操作日志失败: {exc}')
    return entry


def get_operations(limit: int = 100) -> list[dict]:
    """读取最近的操作记录，按时间倒序返回。"""
    limit = max(1, min(int(limit), 500))
    if not OPERATIONS_LOG_PATH.exists():
        return []
    try:
        with _operations_lock:
            lines = OPERATIONS_LOG_PATH.read_text(encoding='utf-8').splitlines()
    except OSError as exc:
        logger.warning(f'读取服务操作日志失败: {exc}')
        return []

    entries = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
        if len(entries) >= limit:
            break
    return entries
