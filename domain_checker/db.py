"""SQLite 持久化：查询历史（query_history）与明细结果（query_results）。

每次连接即用即关；表结构在 init_db 中创建，init_db 幂等，
可被 Flask debug reloader 重复调用。DB 路径来自 settings.DB_PATH，
测试可通过环境变量 DOMAIN_CHECKER_DATA_DIR 指向临时目录。
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta

from . import settings

logger = logging.getLogger(__name__)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(settings.DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化SQLite数据库（幂等）。"""
    conn = sqlite3.connect(str(settings.DB_PATH))
    cursor = conn.cursor()

    # 查询历史记录表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS query_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT UNIQUE NOT NULL,
            domains TEXT NOT NULL,
            domain_count INTEGER NOT NULL,
            results_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'processing',
            created_at TEXT NOT NULL,
            completed_at TEXT,
            config TEXT
        )
    ''')

    # 查询详情表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS query_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            domain TEXT NOT NULL,
            status TEXT,
            whois_status TEXT,
            hold_status TEXT,
            registrar TEXT,
            contact_email TEXT,
            registration_date TEXT,
            expiration_date TEXT,
            updated_date TEXT,
            name_servers TEXT,
            dnssec TEXT,
            resolved INTEGER,
            block_reason TEXT,
            dns_records TEXT,
            error TEXT,
            raw_response TEXT,
            query_time TEXT,
            query_duration_seconds REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES query_history(task_id)
        )
    ''')

    # 为已有数据库补齐新字段，避免升级时丢失历史记录。
    cursor.execute('PRAGMA table_info(query_results)')
    existing_columns = {row[1] for row in cursor.fetchall()}
    for column, definition in {
        'whois_status': 'TEXT',
        'hold_status': 'TEXT',
        'query_time': 'TEXT',
        'query_duration_seconds': 'REAL',
        'raw_response': 'TEXT',
        'contact_email': 'TEXT',
    }.items():
        if column not in existing_columns:
            cursor.execute(f'ALTER TABLE query_results ADD COLUMN {column} {definition}')

    # 最近成功结果复用按域名 + 时间查询，索引避免每次批量查询都全表扫描
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_query_results_domain_created
        ON query_results(domain, created_at)
    ''')

    conn.commit()
    conn.close()
    logger.info(f"数据库初始化完成: {settings.DB_PATH}")


def mark_interrupted_tasks() -> int:
    """将上次进程遗留的 processing 历史标记为 interrupted，返回标记条数。

    运行中任务只存在于内存；服务启动时仍为 processing 的历史必然已随进程中断，
    不应长期显示为“处理中”。本函数只在应用启动时调用，不处理当前进程内的任务。
    """
    conn = sqlite3.connect(str(settings.DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE query_history
        SET status = 'interrupted', completed_at = COALESCE(completed_at, ?)
        WHERE status = 'processing'
    ''', (datetime.now().isoformat(),))
    count = cursor.rowcount
    conn.commit()
    conn.close()
    if count:
        logger.warning(f"已将 {count} 条上次进程遗留的查询标记为已中断")
    return count


def save_history(task_id: str, domains: list, status: str = 'processing',
                 completed_at: 'str | None' = None):
    """保存查询历史。"""
    conn = sqlite3.connect(str(settings.DB_PATH))
    cursor = conn.cursor()

    success_count = 0
    failed_count = 0

    if status == 'completed':
        cursor.execute(
            'SELECT status, COUNT(*) FROM query_results WHERE task_id = ? GROUP BY status',
            (task_id,))
        for row in cursor.fetchall():
            if row[0] == 'success':
                success_count = row[1]
            else:
                failed_count += row[1]

    config_json = json.dumps(settings.public_config())

    cursor.execute('''
        INSERT OR REPLACE INTO query_history
        (task_id, domains, domain_count, results_count, success_count, failed_count, status, created_at, completed_at, config)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        task_id,
        '\n'.join(domains),
        len(domains),
        success_count + failed_count,
        success_count,
        failed_count,
        status,
        datetime.now().isoformat(),
        completed_at,
        config_json
    ))

    conn.commit()
    conn.close()


def save_results(task_id: str, results: list):
    """保存查询结果（整体覆盖同一 task_id 的旧结果）。"""
    conn = sqlite3.connect(str(settings.DB_PATH))
    cursor = conn.cursor()

    # 先删除旧结果
    cursor.execute('DELETE FROM query_results WHERE task_id = ?', (task_id,))

    for r in results:
        dns_records = r.get('dns_records', [])
        if isinstance(dns_records, list):
            dns_records = ','.join(dns_records)

        cursor.execute('''
            INSERT INTO query_results
            (task_id, domain, status, whois_status, hold_status, registrar, contact_email, registration_date,
             expiration_date, updated_date, name_servers, dnssec, resolved, block_reason,
             dns_records, error, raw_response, query_time, query_duration_seconds, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            task_id, r.get('domain', ''), r.get('status', ''), r.get('whois_status', ''),
            r.get('hold_status', ''), r.get('registrar', ''),
            r.get('contact_email', ''),
            r.get('registration_date', ''), r.get('expiration_date', ''), r.get('updated_date', ''),
            r.get('name_servers', ''), r.get('dnssec', ''),
            1 if r.get('resolved') is True else (0 if r.get('resolved') is False else None),
            r.get('block_reason', ''), dns_records, r.get('error', ''), r.get('raw_response'),
            r.get('query_time', ''), r.get('query_duration_seconds'),
            datetime.now().isoformat()
        ))

    conn.commit()
    conn.close()


def update_history_counts(task_id: str, success_count: int, failed_count: int):
    """更新历史记录的统计，并将任务标记为已完成。"""
    conn = sqlite3.connect(str(settings.DB_PATH))
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE query_history
        SET results_count = ?, success_count = ?, failed_count = ?, status = 'completed', completed_at = ?
        WHERE task_id = ?
    ''', (success_count + failed_count, success_count, failed_count,
          datetime.now().isoformat(), task_id))

    conn.commit()
    conn.close()


def get_history(limit: int = 50) -> list:
    """获取查询历史。"""
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT * FROM query_history
        ORDER BY created_at DESC
        LIMIT ?
    ''', (limit,))

    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_history_detail(task_id: str):
    """获取历史详情，返回 (history 或 None, results 列表)。"""
    conn = _connect()
    cursor = conn.cursor()

    # 获取历史记录
    cursor.execute('SELECT * FROM query_history WHERE task_id = ?', (task_id,))
    row = cursor.fetchone()
    history = dict(row) if row else None

    # 获取结果
    cursor.execute('SELECT * FROM query_results WHERE task_id = ? ORDER BY id', (task_id,))
    results = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return history, results


def _result_row_to_dict(row: sqlite3.Row) -> dict:
    """把 query_results 行转换为与内存结果一致的字段结构。"""
    result = dict(row)
    resolved = result.get('resolved')
    result['resolved'] = (
        True if resolved == 1 else False if resolved == 0 else None
    )
    records = result.get('dns_records')
    result['dns_records'] = records.split(',') if records else []
    for db_only in ('id', 'task_id', 'created_at'):
        result.pop(db_only, None)
    return result


def find_recent_success_results(domains: list[str], max_age_seconds: int) -> dict[str, dict]:
    """查找 TTL 内最近一次成功且 DNS 有明确结论的结果，用于跳过网络查询。

    新鲜度按结果自身的 ``query_time`` 计算：复用结果再次落库时不会延长 TTL，
    避免连续小批次查询把旧数据无限期续命。只复用 ``status='success'`` 且
    ``resolved`` 为 1/0 的行；未注册、失败以及 DNS 未知的结果仍走实时查询。
    """
    unique_domains = list(dict.fromkeys(str(domain) for domain in domains if domain))
    if not unique_domains:
        return {}

    try:
        max_age = int(max_age_seconds)
    except (TypeError, ValueError):
        return {}
    if max_age <= 0:
        return {}

    cutoff = (datetime.now() - timedelta(seconds=max_age)).strftime('%Y-%m-%dT%H:%M:%S')
    found: dict[str, dict] = {}
    conn = _connect()
    try:
        # SQLite 旧版本单个语句最多 999 个参数，按 500 分块以兼容
        for start in range(0, len(unique_domains), 500):
            chunk = unique_domains[start:start + 500]
            placeholders = ','.join('?' for _ in chunk)
            cursor = conn.execute(f'''
                SELECT * FROM query_results
                WHERE domain IN ({placeholders})
                  AND status = 'success'
                  AND resolved IS NOT NULL
                  AND COALESCE(
                        NULLIF(substr(replace(query_time, ' ', 'T'), 1, 19), ''),
                        substr(created_at, 1, 19)
                      ) >= ?
                ORDER BY COALESCE(
                        NULLIF(replace(query_time, ' ', 'T'), ''),
                        created_at
                      ) DESC
            ''', (*chunk, cutoff))
            for row in cursor.fetchall():
                # 已按时间倒序，每个域名只保留最新一条
                if row['domain'] not in found:
                    found[row['domain']] = _result_row_to_dict(row)
    finally:
        conn.close()
    return found


def delete_history(task_id: str):
    """删除历史记录。"""
    conn = sqlite3.connect(str(settings.DB_PATH))
    cursor = conn.cursor()
    cursor.execute('DELETE FROM query_results WHERE task_id = ?', (task_id,))
    cursor.execute('DELETE FROM query_history WHERE task_id = ?', (task_id,))
    conn.commit()
    conn.close()


def delete_histories(task_ids: list[str]) -> int:
    """在一个事务中删除多条历史及明细，返回实际删除的历史条数。"""
    unique_ids = list(dict.fromkeys(task_ids))
    if not unique_ids:
        return 0
    placeholders = ','.join('?' for _ in unique_ids)
    conn = sqlite3.connect(str(settings.DB_PATH))
    cursor = conn.cursor()
    cursor.execute(f'DELETE FROM query_results WHERE task_id IN ({placeholders})', unique_ids)
    cursor.execute(f'DELETE FROM query_history WHERE task_id IN ({placeholders})', unique_ids)
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def clear_all_history() -> int:
    """删除全部查询历史及明细，返回删除的历史条数。"""
    conn = sqlite3.connect(str(settings.DB_PATH))
    cursor = conn.cursor()
    cursor.execute('DELETE FROM query_results')
    cursor.execute('DELETE FROM query_history')
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def clear_old_history(days: int = 30) -> int:
    """清理旧历史，返回删除的条数。"""
    conn = sqlite3.connect(str(settings.DB_PATH))
    cursor = conn.cursor()
    cutoff = datetime.now()
    cursor.execute('''
        DELETE FROM query_history
        WHERE created_at < datetime(?, '-' || ? || ' days')
    ''', (cutoff.isoformat(), days))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    logger.info(f"已清理 {deleted} 条{days}天前的历史记录")
    return deleted
