"""SQLite 持久化单元测试（在 conftest 设置的临时数据目录下运行）。"""

from domain_checker import db
from domain_checker.settings import DB_PATH


def _sample_results(n=3):
    return [{
        'domain': f'sample{i}.com', 'status': 'success' if i % 2 == 0 else 'failed',
        'registrar': 'X Registrar', 'registration_date': '2024-01-01',
        'expiration_date': '2027-01-01', 'updated_date': '',
        'name_servers': 'ns1.x.com', 'dnssec': '',
        'resolved': i % 2 == 0,
        'block_reason': '' if i % 2 == 0 else '未解析（NXDOMAIN）',
        'dns_records': ['1.2.3.4'] if i % 2 == 0 else [],
        'error': '' if i % 2 == 0 else 'boom',
    } for i in range(n)]


class TestHistoryRoundTrip:
    def test_save_and_get(self):
        db.init_db()
        db.save_history('T01', ['a.com', 'b.com'], 'processing')
        history = db.get_history()
        assert history[0]['task_id'] == 'T01'
        assert history[0]['domain_count'] == 2
        assert history[0]['status'] == 'processing'

    def test_results_and_counts(self):
        results = _sample_results(3)
        db.save_history('T02', [r['domain'] for r in results], 'processing')
        db.save_results('T02', results)
        db.update_history_counts('T02', 2, 1)

        history, rows = db.get_history_detail('T02')
        assert history['status'] == 'completed'
        assert history['success_count'] == 2
        assert history['failed_count'] == 1
        assert len(rows) == 3
        # resolved 为布尔/None 的往返转换（sample: 0→True, 1→False, 2→True）
        assert rows[0]['resolved'] == 1
        assert rows[1]['resolved'] == 0
        assert rows[2]['resolved'] == 1
        assert rows[0]['dns_records'] == '1.2.3.4'

    def test_save_results_overwrites(self):
        results = _sample_results(3)
        db.save_history('T03', ['x.com'], 'processing')
        db.save_results('T03', results)
        db.save_results('T03', results[:1])
        _, rows = db.get_history_detail('T03')
        assert len(rows) == 1

    def test_delete(self):
        db.delete_history('T02')
        history, rows = db.get_history_detail('T02')
        assert history is None
        assert rows == []

    def test_clear_old_keeps_fresh(self):
        deleted = db.clear_old_history(30)
        assert deleted == 0
        assert db.get_history()[0]['task_id'] == 'T03'

    def test_missing_detail(self):
        history, rows = db.get_history_detail('NOPE')
        assert history is None
        assert rows == []

    def test_db_lives_in_tmp_dir(self):
        # 测试环境应使用 conftest 配置的临时数据目录，而不是仓库内的真实库
        assert 'domain-checker-test-' in str(DB_PATH)
