"""HTTP API 集成测试（Flask test_client，不发起真实域名查询）。"""

import json
import os
from datetime import datetime, timedelta

from domain_checker import db
from domain_checker.state import task_lock, task_storage


class TestConfig:
    def test_get_shape(self, client):
        data = client.get('/api/config').get_json()
        assert 'allow_lan_access' in data
        assert 'platforms' in data
        assert 'server' in data
        assert set(data['server']) == {'bind_host', 'port', 'lan_active'}
        # 敏感字段不下发
        assert 'proxy_auth' not in data

    def test_lan_toggle_persisted_with_restart_notice(self, client, tmp_dir):
        original = client.get('/api/config').get_json()['allow_lan_access']
        new_value = not original

        resp = client.post('/api/config', json={'allow_lan_access': new_value})
        data = resp.get_json()
        assert resp.status_code == 200
        assert '重启' in data['message']
        assert data['config']['allow_lan_access'] is new_value

        # 已写入 settings.json
        settings_file = os.path.join(tmp_dir, 'settings.json')
        with open(settings_file, encoding='utf-8') as f:
            saved = json.load(f)
        assert saved['allow_lan_access'] is new_value

        # 改回去，且未变化时不应再提示重启
        resp = client.post('/api/config', json={'allow_lan_access': original})
        assert '重启' in resp.get_json()['message']
        resp = client.post('/api/config', json={'allow_lan_access': original})
        assert '重启' not in resp.get_json()['message']

    def test_numeric_config(self, client):
        client.post('/api/config', json={'max_retries': 2, 'rate_limit_delay': 0.5})
        data = client.get('/api/config').get_json()
        assert data['max_retries'] == 2
        assert data['rate_limit_delay'] == 0.5

    def test_timeout_config_is_clamped(self, client):
        original = client.get('/api/config').get_json()['timeout']
        client.post('/api/config', json={'timeout': 999})
        assert client.get('/api/config').get_json()['timeout'] == 120
        client.post('/api/config', json={'timeout': 0})
        assert client.get('/api/config').get_json()['timeout'] == 1
        client.post('/api/config', json={'timeout': original})

    def test_invalid_platform_falls_back_to_whois(self, client):
        client.post('/api/config', json={'platform': 'nope'})
        assert client.get('/api/config').get_json()['platform'] == 'whois'

    def test_platforms_have_impl_and_desc(self, client):
        platforms = client.get('/api/config').get_json()['platforms']
        assert platforms['whois']['implemented'] is True
        assert platforms['whoisxml']['implemented'] is False
        assert platforms['rdap']['implemented'] is False
        for p in platforms.values():
            assert p.get('desc'), '每个平台都应带效果与信息介绍'


class TestQueryValidation:
    def test_empty_input(self, client):
        resp = client.post('/api/query', json={'domains': '   \n  '})
        assert resp.status_code == 400
        assert '输入' in resp.get_json()['error']

    def test_batch_limit(self, client):
        big = '\n'.join(f'd{i}.com' for i in range(600))
        resp = client.post('/api/query', json={'domains': big})
        assert resp.status_code == 400
        assert '最多' in resp.get_json()['error']

    def test_query_accepts_quick_mode(self, client, monkeypatch):
        monkeypatch.setattr('domain_checker.web.process_domains_async', lambda domains, task_id: None)
        resp = client.post('/api/query', json={'domains': 'www.baidu.com', 'query_mode': 'quick'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['query_mode'] == 'quick'
        try:
            with task_lock:
                assert task_storage[data['task_id']]['query_mode'] == 'quick'
        finally:
            with task_lock:
                task_storage.pop(data['task_id'], None)

    def test_query_defaults_to_unlimited_and_accepts_task_timeout(self, client, monkeypatch):
        monkeypatch.setattr('domain_checker.web.process_domains_async', lambda domains, task_id: None)
        resp = client.post('/api/query', json={'domains': 'example.com', 'query_timeout': 999})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['query_mode'] == 'unlimited'
        assert data['query_timeout'] == 120
        try:
            with task_lock:
                task = task_storage[data['task_id']]
                assert task['query_mode'] == 'unlimited'
                assert task['query_timeout'] == 120
        finally:
            with task_lock:
                task_storage.pop(data['task_id'], None)


class TestTaskEndpoints:
    def test_unknown_task(self, client):
        assert client.get('/api/status/NOPE').status_code == 404
        assert client.get('/api/results/NOPE').status_code == 404
        assert client.post('/api/retry/NOPE', json={'domains': ['a.com']}).status_code == 404
        assert client.post('/api/retry-failed/NOPE').status_code == 404

    def test_status_exposes_operation_progress_and_duration(self, client):
        started_at = datetime.now() - timedelta(seconds=3)
        with task_lock:
            task_storage['STATUS01'] = {
                'status': 'processing', 'total': 4, 'completed': 4,
                'results': [], 'logs': [], 'refresh': False,
                'created_at': started_at.isoformat(), 'completed_at': None,
                'operation': 'retry', 'operation_total': 2, 'operation_completed': 1,
                'operation_started_at': started_at.isoformat(), 'duration_seconds': None,
            }
        try:
            data = client.get('/api/status/STATUS01').get_json()
            assert data['operation'] == 'retry'
            assert data['progress'] == 50.0
            assert data['operation_completed'] == 1
            assert data['elapsed_seconds'] >= 3
        finally:
            with task_lock:
                task_storage.pop('STATUS01', None)

    def test_results_log_cursor_returns_only_new_logs(self, client):
        with task_lock:
            task_storage['LOG01'] = {
                'status': 'processing', 'total': 1, 'completed': 0,
                'results': [], 'refresh': False,
                'logs': [
                    {'time': '10:00:00', 'level': 'info', 'message': '第一条'},
                    {'time': '10:00:01', 'level': 'warn', 'message': '第二条'},
                ],
            }
        try:
            data = client.get('/api/results/LOG01?log_after=1').get_json()
            assert data['log_cursor'] == 2
            assert [log['message'] for log in data['logs']] == ['第二条']
        finally:
            with task_lock:
                task_storage.pop('LOG01', None)

    def test_retry_enters_processing_state_and_rejects_overlap(self, client, monkeypatch):
        monkeypatch.setattr('domain_checker.web.retry_domains_async', lambda domains, task_id: None)
        now = datetime.now().isoformat()
        with task_lock:
            task_storage['RETRY01'] = {
                'status': 'completed', 'total': 1, 'completed': 1,
                'results': [{'domain': 'a.com', 'status': 'failed'}],
                'logs': [], 'refresh': False, 'created_at': now, 'completed_at': now,
            }
        try:
            assert client.post('/api/retry/RETRY01', json={'domains': 'a.com'}).status_code == 400
            assert client.post('/api/retry/RETRY01', json={'domains': ['other.com']}).status_code == 400

            resp = client.post('/api/retry/RETRY01', json={'domains': ['A.COM', 'a.com']})
            assert resp.status_code == 200
            assert resp.get_json()['started'] is True
            status = client.get('/api/status/RETRY01').get_json()
            assert status['status'] == 'processing'
            assert status['operation'] == 'retry'
            assert status['operation_total'] == 1
            assert status['query_mode'] == 'unlimited'
            assert client.post('/api/retry/RETRY01', json={'domains': ['a.com']}).status_code == 409
        finally:
            with task_lock:
                task_storage.pop('RETRY01', None)

    def test_retry_restores_completed_task_from_history(self, client, monkeypatch):
        db.save_history('RESTORE01', ['saved.com'], 'processing')
        db.save_results('RESTORE01', [{
            'domain': 'saved.com', 'status': 'failed', 'registrar': '',
            'resolved': None, 'block_reason': '', 'dns_records': [],
            'error': 'WHOIS 查询超时',
        }])
        db.update_history_counts('RESTORE01', 0, 1)
        monkeypatch.setattr('domain_checker.web.retry_domains_async', lambda domains, task_id: None)
        with task_lock:
            task_storage.pop('RESTORE01', None)

        try:
            resp = client.post('/api/retry/RESTORE01', json={'domains': ['saved.com']})

            assert resp.status_code == 200
            assert resp.get_json()['started'] is True
            with task_lock:
                task = task_storage['RESTORE01']
                assert task['status'] == 'processing'
                assert task['query_mode'] == 'unlimited'
                assert task['results'][0]['domain'] == 'saved.com'
                assert task['logs'][0]['message'].startswith('已从历史记录恢复任务')
        finally:
            with task_lock:
                task_storage.pop('RESTORE01', None)


class TestHistory:
    def _seed(self):
        db.save_history('HT01', ['a.com'], 'processing')
        db.save_results('HT01', [{
            'domain': 'a.com', 'status': 'success', 'registrar': 'R',
            'resolved': False, 'block_reason': '未解析（NXDOMAIN）', 'dns_records': [],
            'error': ''}])
        db.update_history_counts('HT01', 1, 0)

    def test_list_and_detail(self, client):
        self._seed()
        listing = client.get('/api/history').get_json()['history']
        assert any(h['task_id'] == 'HT01' for h in listing)

        detail = client.get('/api/history/HT01').get_json()
        assert detail['history']['task_id'] == 'HT01'
        assert len(detail['results']) == 1
        assert detail['results'][0]['block_reason'] == '未解析（NXDOMAIN）'

    def test_detail_404(self, client):
        assert client.get('/api/history/NOPE').status_code == 404

    def test_delete(self, client):
        resp = client.delete('/api/history/HT01')
        assert resp.status_code == 200
        assert client.get('/api/history/HT01').status_code == 404

    def test_clear(self, client):
        resp = client.post('/api/history/clear', json={'days': 30})
        assert resp.status_code == 200

    def test_export_from_db_fallback(self, client):
        self._seed()
        resp = client.get('/api/export/HT01?format=csv')
        assert resp.status_code == 200
        body = resp.data.decode('utf-8-sig', errors='replace')
        assert '解析异常原因' in body
        assert 'a.com' in body
