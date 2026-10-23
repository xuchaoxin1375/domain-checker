"""SQLite 持久化单元测试（在 conftest 设置的临时数据目录下运行）。"""

from datetime import datetime, timedelta

from domain_checker import db
from domain_checker.settings import DB_PATH


def _sample_results(n=3):
    return [{
        'domain': f'sample{i}.com', 'status': 'success' if i % 2 == 0 else 'failed',
        'whois_status': 'clientHold' if i == 1 else 'ok',
        'hold_status': 'clientHold' if i == 1 else '',
        'registrar': 'X Registrar', 'registration_date': '2024-01-01',
        'contact_email': 'abuse@example.com',
        'expiration_date': '2027-01-01', 'updated_date': '',
        'name_servers': 'ns1.x.com', 'dnssec': '',
        'resolved': i % 2 == 0,
        'block_reason': '' if i % 2 == 0 else '未解析（NXDOMAIN）',
        'dns_records': ['1.2.3.4'] if i % 2 == 0 else [],
        'error': '' if i % 2 == 0 else 'boom',
        'raw_response': f'raw response {i}',
        'query_time': f'2026-07-28 12:00:0{i}',
        'query_duration_seconds': i + 0.25,
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
        assert rows[1]['whois_status'] == 'clientHold'
        assert rows[1]['hold_status'] == 'clientHold'
        assert rows[1]['contact_email'] == 'abuse@example.com'
        assert rows[1]['query_time'] == '2026-07-28 12:00:01'
        assert rows[1]['query_duration_seconds'] == 1.25
        assert rows[1]['raw_response'] == 'raw response 1'

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


def test_batch_delete_and_clear_all_use_single_database(tmp_path, monkeypatch):
    isolated_db = tmp_path / 'batch.db'
    monkeypatch.setattr(db.settings, 'DB_PATH', isolated_db)
    db.init_db()
    for task_id in ['DBB01', 'DBB02', 'DBB03']:
        db.save_history(task_id, [f'{task_id.lower()}.com'])

    assert db.delete_histories(['DBB01', 'DBB02']) == 2
    assert db.get_history_detail('DBB01')[0] is None
    assert db.clear_all_history() == 1
    assert db.get_history() == []


class TestRecentSuccessReuse:
    """最近成功结果复用：只取 TTL 内成功且 DNS 有结论的最新记录。"""

    def _save(self, task_id, results):
        db.save_history(task_id, [r['domain'] for r in results], 'processing')
        db.save_results(task_id, results)
        db.update_history_counts(task_id, len(results), 0)

    @staticmethod
    def _result(domain, *, status='success', resolved=True, query_time=None, registrar='R'):
        return {
            'domain': domain, 'status': status, 'registrar': registrar,
            'whois_status': 'ok', 'hold_status': '', 'resolved': resolved,
            'block_reason': '' if resolved is not False else '未解析',
            'dns_records': ['1.2.3.4'] if resolved else [],
            'error': '', 'raw_response': 'raw',
            'query_time': query_time or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'query_duration_seconds': 1.5,
        }

    def test_returns_fresh_success_with_converted_fields(self):
        self._save('REUSE01', [self._result('fresh.com')])

        found = db.find_recent_success_results(['fresh.com', 'missing.com'], 600)

        assert set(found) == {'fresh.com'}
        result = found['fresh.com']
        assert result['resolved'] is True
        assert result['dns_records'] == ['1.2.3.4']
        assert result['registrar'] == 'R'
        assert 'id' not in result and 'created_at' not in result

    def test_skips_failed_unregistered_and_unknown_dns(self):
        self._save('REUSE02', [
            self._result('failed.com', status='failed', resolved=None),
            self._result('available.com', status='not_registered', resolved=None),
            self._result('unknown.com', status='success', resolved=None),
        ])

        found = db.find_recent_success_results(
            ['failed.com', 'available.com', 'unknown.com'], 600,
        )

        assert found == {}

    def test_respects_ttl_and_ignores_zero_window(self):
        old_time = (datetime.now() - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')
        self._save('REUSE03', [self._result('old.com', query_time=old_time)])

        assert db.find_recent_success_results(['old.com'], 600) == {}
        assert db.find_recent_success_results(['old.com'], 3 * 3600)
        assert db.find_recent_success_results(['old.com'], 0) == {}

    def test_newest_record_wins(self):
        older = (datetime.now() - timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
        newer = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self._save('REUSE04', [self._result('multi.com', query_time=older, registrar='Old')])
        self._save('REUSE05', [self._result('multi.com', query_time=newer, registrar='New')])

        found = db.find_recent_success_results(['multi.com'], 600)

        assert found['multi.com']['registrar'] == 'New'

    def test_reused_copy_does_not_extend_freshness(self):
        # 复用结果再次落库时 created_at 是新的，但 query_time 仍是原始时间；
        # TTL 必须按 query_time 判断，否则连续查询会让旧数据永久"保鲜"。
        old_time = (datetime.now() - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')
        self._save('REUSE06', [self._result('chain.com', query_time=old_time)])

        found = db.find_recent_success_results(['chain.com'], 600)

        assert found == {}
