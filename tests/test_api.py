"""HTTP API 集成测试（Flask test_client，不发起真实域名查询）。"""

import json
import os

from domain_checker import db


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


class TestTaskEndpoints:
    def test_unknown_task(self, client):
        assert client.get('/api/status/NOPE').status_code == 404
        assert client.get('/api/results/NOPE').status_code == 404
        assert client.post('/api/retry/NOPE', json={'domains': ['a.com']}).status_code == 404
        assert client.post('/api/retry-failed/NOPE').status_code == 404


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
