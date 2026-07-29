"""HTTP API 集成测试（Flask test_client，不发起真实域名查询）。"""

import json
import os
import time
from datetime import datetime, timedelta

from domain_checker import db
from domain_checker.state import task_lock, task_pause_flags, task_storage


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

    def test_invalid_platform_falls_back_to_rdap(self, client):
        client.post('/api/config', json={'platform': 'nope'})
        assert client.get('/api/config').get_json()['platform'] == 'rdap'

    def test_platforms_have_impl_and_desc(self, client):
        platforms = client.get('/api/config').get_json()['platforms']
        assert platforms['whois']['implemented'] is True
        assert platforms['whoisxml']['implemented'] is False
        assert platforms['rdap']['implemented'] is True
        for p in platforms.values():
            assert p.get('desc'), '每个平台都应带效果与信息介绍'


def test_shutdown_endpoint_is_not_exposed(client):
    assert client.post('/api/shutdown', json={}).status_code == 404


class TestOperations:
    def test_operations_endpoint(self, client, monkeypatch):
        entries = [{'time': '2026-07-29 10:00:00', 'action': '启动', 'pid': 12, 'detail': 'started'}]
        monkeypatch.setattr('domain_checker.web.get_operations', lambda limit: entries[:limit])

        resp = client.get('/api/operations?limit=1')

        assert resp.status_code == 200
        assert resp.get_json()['operations'] == entries

    def test_run_server_records_start_and_stop(self, monkeypatch):
        from domain_checker import web

        recorded = []
        monkeypatch.setenv('DOMAIN_CHECKER_HOST', '127.0.0.1')
        monkeypatch.setenv('DOMAIN_CHECKER_PORT', '5099')
        monkeypatch.setenv('DOMAIN_CHECKER_DEBUG', '0')
        monkeypatch.setattr(web, 'record_operation', lambda action, detail: recorded.append((action, detail)))
        monkeypatch.setattr(web.app, 'run', lambda **_kwargs: None)

        web.run_server()

        assert recorded[0] == ('启动', '服务开始监听 127.0.0.1:5099，debug=False')
        assert recorded[1] == ('终止', '服务进程已停止')


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

    def test_query_accepts_brief_mode(self, client, monkeypatch):
        monkeypatch.setattr('domain_checker.web.process_domains_async', lambda *args: None)
        resp = client.post('/api/query', json={'domains': 'example.com', 'query_mode': 'brief'})
        data = resp.get_json()
        try:
            assert resp.status_code == 200
            assert data['query_mode'] == 'brief'
            with task_lock:
                assert task_storage[data['task_id']]['query_mode'] == 'brief'
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
                assert task['results'][0]['query_state'] == 'queued'
                assert task['results'][0]['domain'] == 'example.com'
        finally:
            with task_lock:
                task_storage.pop(data['task_id'], None)

    def test_query_timeout_is_clamped_to_lower_bound(self, client, monkeypatch):
        monkeypatch.setattr('domain_checker.web.process_domains_async', lambda domains, task_id: None)
        resp = client.post('/api/query', json={'domains': 'example.com', 'query_timeout': 0})
        data = resp.get_json()
        try:
            assert resp.status_code == 200
            assert data['query_timeout'] == 1
            with task_lock:
                assert task_storage[data['task_id']]['query_timeout'] == 1
        finally:
            with task_lock:
                task_storage.pop(data['task_id'], None)

    def test_query_exposes_all_domains_as_queued_in_input_order(self, client, monkeypatch):
        monkeypatch.setattr('domain_checker.web.process_domains_async', lambda domains, task_id: None)
        resp = client.post('/api/query', json={'domains': 'b.com\na.com\nc.com'})
        task_id = resp.get_json()['task_id']
        try:
            results = client.get(f'/api/results/{task_id}').get_json()['results']
            assert [result['domain'] for result in results] == ['b.com', 'a.com', 'c.com']
            assert [result['query_state'] for result in results] == ['queued', 'queued', 'queued']
            assert all(result['status'] is None for result in results)
        finally:
            with task_lock:
                task_storage.pop(task_id, None)


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

    def test_elapsed_timer_freezes_while_paused(self, client):
        started_at = datetime.now() - timedelta(seconds=2)
        with task_lock:
            task_storage['PAUSETIMER'] = {
                'status': 'processing', 'total': 1, 'completed': 0,
                'results': [], 'logs': [], 'refresh': False,
                'created_at': started_at.isoformat(), 'completed_at': None,
                'operation': 'query', 'operation_total': 1, 'operation_completed': 0,
                'operation_started_at': started_at.isoformat(), 'duration_seconds': None,
                'paused_duration_seconds': 0.0, '_paused_started_monotonic': None,
            }
        try:
            assert client.post('/api/pause/PAUSETIMER').status_code == 200
            first = client.get('/api/status/PAUSETIMER').get_json()['elapsed_seconds']
            time.sleep(0.12)
            second = client.get('/api/status/PAUSETIMER').get_json()['elapsed_seconds']
            assert abs(second - first) < 0.05

            assert client.post('/api/resume/PAUSETIMER').status_code == 200
            time.sleep(0.12)
            resumed = client.get('/api/status/PAUSETIMER').get_json()['elapsed_seconds']
            assert resumed >= second + 0.08
        finally:
            task_pause_flags.pop('PAUSETIMER', None)
            with task_lock:
                task_storage.pop('PAUSETIMER', None)

    def test_pause_resume_and_cancel_update_only_unfinished_rows(self, client):
        now = datetime.now().isoformat()
        with task_lock:
            task_storage['ROWSTATES'] = {
                'status': 'processing', 'total': 3, 'completed': 1,
                'results': [
                    {'domain': 'done.com', 'status': 'success', 'query_state': 'completed'},
                    {'domain': 'queued.com', 'status': None, 'query_state': 'queued'},
                    {'domain': 'active.com', 'status': None, 'query_state': 'querying'},
                ],
                'logs': [], 'refresh': False,
                'created_at': now, 'completed_at': None,
                'operation': 'query', 'operation_total': 3, 'operation_completed': 1,
                'operation_started_at': now, 'duration_seconds': None,
                'paused_duration_seconds': 0.0, '_paused_started_monotonic': None,
            }
        try:
            assert client.post('/api/pause/ROWSTATES').status_code == 200
            paused = client.get('/api/results/ROWSTATES').get_json()['results']
            assert [result['query_state'] for result in paused] == [
                'completed', 'paused', 'paused',
            ]

            assert client.post('/api/resume/ROWSTATES').status_code == 200
            resumed = client.get('/api/results/ROWSTATES').get_json()['results']
            assert [result['query_state'] for result in resumed] == [
                'completed', 'queued', 'querying',
            ]

            assert client.post('/api/cancel/ROWSTATES').status_code == 200
            cancelled = client.get('/api/results/ROWSTATES').get_json()['results']
            assert [result['query_state'] for result in cancelled] == [
                'completed', 'cancelled', 'cancelled',
            ]
        finally:
            task_pause_flags.pop('ROWSTATES', None)
            with task_lock:
                task_storage.pop('ROWSTATES', None)

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
                'results': [{
                    'domain': 'a.com', 'status': 'failed', 'query_state': 'completed',
                    'query_time': '2026-07-29 10:00:00', 'query_duration_seconds': 4.25,
                }],
                'logs': [], 'refresh': False, 'created_at': now, 'completed_at': now,
            }
        try:
            assert client.post('/api/retry/RETRY01', json={'domains': 'a.com'}).status_code == 400
            assert client.post('/api/retry/RETRY01', json={'domains': ['other.com']}).status_code == 400

            resp = client.post('/api/retry/RETRY01', json={'domains': ['A.COM', 'a.com']})
            assert resp.status_code == 200
            assert resp.get_json()['started'] is True
            assert resp.get_json()['domains'] == ['a.com']
            status = client.get('/api/status/RETRY01').get_json()
            assert status['status'] == 'processing'
            assert status['operation'] == 'retry'
            assert status['operation_total'] == 1
            assert status['query_mode'] == 'unlimited'
            with task_lock:
                result = task_storage['RETRY01']['results'][0]
                assert result['query_state'] == 'queued'
                assert result['query_time'] is None
                assert result['query_duration_seconds'] is None
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

    def test_retry_failed_includes_success_with_unknown_dns(self, client, monkeypatch):
        retried = []
        monkeypatch.setattr(
            'domain_checker.web.retry_domains_async',
            lambda domains, task_id: retried.extend(domains),
        )
        now = datetime.now().isoformat()
        with task_lock:
            task_storage['DNSUNKNOWN'] = {
                'status': 'completed', 'total': 2, 'completed': 2,
                'results': [
                    {
                        'domain': 'unknown.com', 'status': 'success', 'resolved': None,
                        'query_time': '2026-07-29 10:00:00', 'query_duration_seconds': 8.5,
                    },
                    {'domain': 'ok.com', 'status': 'success', 'resolved': True},
                ],
                'logs': [], 'refresh': False, 'created_at': now, 'completed_at': now,
            }
        try:
            resp = client.post('/api/retry-failed/DNSUNKNOWN', json={})
            assert resp.status_code == 200
            assert resp.get_json()['started'] is True
            assert resp.get_json()['domains'] == ['unknown.com']
            with task_lock:
                result = task_storage['DNSUNKNOWN']['results'][0]
                assert result['query_time'] is None
                assert result['query_duration_seconds'] is None
            for _ in range(20):
                if retried:
                    break
                time.sleep(0.01)
            assert retried == ['unknown.com']
        finally:
            with task_lock:
                task_storage.pop('DNSUNKNOWN', None)


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

    def test_batch_delete_selected_history(self, client):
        for task_id in ['BATCH01', 'BATCH02']:
            db.save_history(task_id, [f'{task_id.lower()}.com'], 'processing')

        resp = client.post('/api/history/delete-batch', json={'task_ids': ['BATCH01', 'BATCH02']})

        assert resp.status_code == 200
        assert resp.get_json()['deleted'] == 2
        assert client.get('/api/history/BATCH01').status_code == 404
        assert client.get('/api/history/BATCH02').status_code == 404

    def test_batch_delete_validates_selection_and_busy_tasks(self, client):
        assert client.post('/api/history/delete-batch', json={'task_ids': 'BATCH01'}).status_code == 400
        assert client.post('/api/history/delete-batch', json={'task_ids': []}).status_code == 400
        with task_lock:
            task_storage['BUSY_HISTORY'] = {'status': 'processing'}
        try:
            resp = client.post('/api/history/delete-batch', json={'task_ids': ['BUSY_HISTORY']})
            assert resp.status_code == 409
        finally:
            with task_lock:
                task_storage.pop('BUSY_HISTORY', None)

    def test_clear_all_history(self, client, monkeypatch):
        monkeypatch.setattr('domain_checker.web.clear_all_history', lambda: 7)
        assert client.post('/api/history/clear-all').status_code == 415
        resp = client.post('/api/history/clear-all', json={})
        assert resp.status_code == 200
        assert resp.get_json()['deleted'] == 7

    def test_export_from_db_fallback(self, client):
        self._seed()
        resp = client.get('/api/export/HT01?format=csv')
        assert resp.status_code == 200
        body = resp.data.decode('utf-8-sig', errors='replace')
        assert '解析异常原因' in body
        assert 'a.com' in body
