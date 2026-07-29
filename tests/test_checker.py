"""DNS 解析检查单元测试（mock dns.resolver，不访问外网）。

重点验证各种 DNS 异常对应的中文提示是准确的，
尤其是 NXDOMAIN 不再被误报为"域名不存在"。
"""

import json
import socket
from types import SimpleNamespace
from unittest import mock

import pytest

dns = pytest.importorskip('dns.resolver')

from domain_checker.checker import check_domain_resolved, process_single_domain
from domain_checker.state import task_lock, task_pause_flags, task_storage


def _fake(exc_cls):
    """构造一个可 raise 的异常实例（dnspython 2.8 异常构造参数较繁琐，统一走轻量子类）。"""

    class Fake(exc_cls):
        def __init__(self):
            Exception.__init__(self)

        def __str__(self):
            return exc_cls.__name__

    return Fake()


def _resolve_side_effect(exc):
    def side_effect(domain, rdtype):
        raise exc
    return side_effect


class TestCheckDomainResolved:
    def _check(self, exc):
        with mock.patch('dns.resolver.Resolver') as R:
            R.return_value.resolve.side_effect = _resolve_side_effect(exc)
            return check_domain_resolved('some-registered-domain.com')

    def test_nxdomain_means_registered_but_unresolved(self):
        r = self._check(_fake(dns.NXDOMAIN))
        assert r['resolved'] is False
        assert '域名不存在' not in r['block_reason']
        assert '已注册' in r['block_reason']
        assert 'NXDOMAIN' in r['block_reason']

    def test_no_answer(self):
        r = self._check(_fake(dns.NoAnswer))
        assert r['resolved'] is False
        assert 'A记录' in r['block_reason']

    def test_no_nameservers(self):
        r = self._check(_fake(dns.NoNameservers))
        assert r['resolved'] is False
        assert '权威DNS' in r['block_reason']

    def test_timeout_is_unknown_not_not_exist(self):
        with mock.patch('dns.resolver.Resolver') as resolver:
            resolver.return_value.resolve.side_effect = _resolve_side_effect(_fake(dns.Timeout))
            r = check_domain_resolved('some-registered-domain.com')
        assert r['resolved'] is None
        assert '超时' in r['block_reason']
        assert '已自动重试 1 次' in r['block_reason']
        assert '建议重新查询' in r['block_reason']
        assert resolver.return_value.resolve.call_count == 2

    def test_transient_dns_failure_recovers_on_retry(self):
        answers = ['1.2.3.4']
        with mock.patch('dns.resolver.Resolver') as resolver, \
                mock.patch('urllib.request.urlopen'):
            resolver.return_value.resolve.side_effect = [_fake(dns.Timeout), answers]
            r = check_domain_resolved('some-registered-domain.com')
        assert r['resolved'] is True
        assert r['dns_records'] == ['1.2.3.4']
        assert resolver.return_value.resolve.call_count == 2

    def test_dns_timeout_uses_configured_timeout(self):
        from domain_checker import settings

        with settings.config_lock:
            original = settings.CONFIG['timeout']
            settings.CONFIG['timeout'] = 7
        try:
            with mock.patch('dns.resolver.Resolver') as R:
                R.return_value.resolve.side_effect = _resolve_side_effect(_fake(dns.Timeout))
                check_domain_resolved('some-registered-domain.com')
                assert R.return_value.timeout == 7
                assert R.return_value.lifetime == 7
        finally:
            with settings.config_lock:
                settings.CONFIG['timeout'] = original

    def test_dns_and_http_probe_use_explicit_timeout(self):
        with mock.patch('dns.resolver.Resolver') as resolver, \
                mock.patch('urllib.request.urlopen') as urlopen:
            resolver.return_value.resolve.return_value = ['1.2.3.4']

            result = check_domain_resolved('example.com', timeout=4)

        assert result['resolved'] is True
        assert resolver.return_value.timeout == 4
        assert resolver.return_value.lifetime == 4
        assert urlopen.call_args.kwargs['timeout'] == 4

    def test_brief_dns_check_skips_http_probe_and_retry(self):
        with mock.patch('dns.resolver.Resolver') as resolver, \
                mock.patch('urllib.request.urlopen') as urlopen:
            resolver.return_value.resolve.side_effect = _resolve_side_effect(_fake(dns.Timeout))

            result = check_domain_resolved(
                'example.com', timeout=3, attempts=1, check_http=False,
            )

        assert result['resolved'] is None
        assert '已自动重试' not in result['block_reason']
        assert resolver.return_value.resolve.call_count == 1
        urlopen.assert_not_called()


def test_process_single_domain_completes_queued_placeholder():
    with task_lock:
        task_storage['STAGE02'] = {
            'operation': 'query', 'completed': 0,
            'results': [{'domain': 'invalid domain', 'query_state': 'querying'}],
            'logs': [], 'refresh': False,
        }
    try:
        result = process_single_domain('invalid domain', task_id='STAGE02')
        with task_lock:
            task = task_storage['STAGE02']
            assert task['completed'] == 1
            assert task['results'][0]['query_state'] == 'completed'
        assert result['query_state'] == 'completed'
    finally:
        with task_lock:
            task_storage.pop('STAGE02', None)


class TestQueryWhoisNotRegistered:
    """未注册域名：应立即返回 not_registered，不重试（重试只是浪费时间）。"""

    def _run(self, side_effect, cfg_overrides=None, query_mode='standard'):
        import whois as whois_mod

        from domain_checker import settings
        from domain_checker.checker import query_whois_with_retry

        overrides = {'max_retries': 3, 'rate_limit_delay': 0, 'retry_delay': 0,
                     **(cfg_overrides or {})}
        # 注意：原地修改 CONFIG 字典对象（checker 持有同一引用）。
        # 不可如 settings.CONFIG = {...} 替换对象，也不可持锁调用（Lock 非可重入）。
        with settings.config_lock:
            saved = {k: settings.CONFIG[k] for k in overrides if k in settings.CONFIG}
            settings.CONFIG.update(overrides)
        try:
            with mock.patch.object(whois_mod, 'whois') as m_whois, \
                    mock.patch('domain_checker.checker._query_rdap_fallback', return_value=None):
                m_whois.side_effect = side_effect
                result = query_whois_with_retry(
                    'not-a-real-domain-xyz.com', query_mode=query_mode,
                )
        finally:
            with settings.config_lock:
                settings.CONFIG.update(saved)
        return result, m_whois.call_count

    def test_not_found_error_skips_retry(self):
        import whois as whois_mod
        result, calls = self._run(whois_mod.exceptions.WhoisDomainNotFoundError('NOT FOUND'))
        assert result['status'] == 'not_registered'
        assert result['error'] == '域名未被注册'
        assert calls == 1, '未注册不应触发重试'

    def test_no_whois_server_is_not_registration_evidence(self):
        import whois as whois_mod
        result, calls = self._run(
            whois_mod.exceptions.WhoisDomainNotFoundError(
                'No whois server is known for this kind of object.'
            )
        )
        assert result['status'] == 'failed'
        assert calls == 3

    def test_message_marker_skips_retry(self):
        import whois as whois_mod
        result, calls = self._run(whois_mod.exceptions.PywhoisError('No match for "X.COM".'))
        assert result['status'] == 'not_registered'
        assert calls == 1

    def test_empty_whois_data_is_failed_not_not_registered(self):
        empty = SimpleNamespace(
            domain_name=None, registrar=None, creation_date=None, expiration_date=None,
            updated_date=None, name_servers=None, status=None,
        )
        result, calls = self._run(lambda *args, **kwargs: empty)
        assert result['status'] == 'failed'
        assert result['error'] == 'WHOIS 未返回有效数据，无法确认域名状态'
        assert calls == 3

    def test_socket_timeout_is_timeout_not_not_registered(self):
        result, calls = self._run(socket.timeout('timed out'))
        assert result['status'] == 'timeout'
        assert '超时' in result['error']
        assert calls == 3

    def test_domain_name_is_registration_evidence(self):
        registered = SimpleNamespace(
            domain_name='EXAMPLE.COM', registrar='Example Registrar', creation_date=None,
            expiration_date=None, updated_date=None, name_servers=None, status=None, dnssec=None,
        )
        result, calls = self._run(lambda *args, **kwargs: registered)
        assert result['status'] == 'success'
        assert calls == 1

    def test_dict_whois_data_keeps_registration_evidence(self):
        registered = {
            'domain_name': 'EXAMPLE.COM',
            'registrar': 'Example Registrar',
            'status': 'ok',
        }
        result, calls = self._run(lambda *args, **kwargs: registered)
        assert result['status'] == 'success'
        assert result['registrar'] == 'Example Registrar'
        assert calls == 1

    def test_whois_status_is_preserved(self):
        registered = SimpleNamespace(
            domain_name='CARTUTUOFICINA.COM', registrar='Example Registrar',
            creation_date=None, expiration_date=None, updated_date=None,
            name_servers=None, status=['clientHold', 'clientTransferProhibited'], dnssec=None,
        )
        result, calls = self._run(lambda *args, **kwargs: registered)
        assert result['status'] == 'success'
        assert result['whois_status'] == 'clientHold, clientTransferProhibited'
        assert calls == 1

    def test_whois_contact_emails_are_deduplicated(self):
        registered = SimpleNamespace(
            domain_name='EXAMPLE.COM', registrar='Example Registrar', creation_date=None,
            expiration_date=None, updated_date=None, name_servers=None, status='ok', dnssec=None,
            emails=['Abuse@Example.com', 'admin@example.com', 'abuse@example.com'], email=None,
        )
        result, _calls = self._run(lambda *args, **kwargs: registered)
        assert result['contact_email'] == 'abuse@example.com, admin@example.com'
    def test_quota_still_retries(self):
        import whois as whois_mod
        result, calls = self._run(whois_mod.exceptions.WhoisQuotaExceededError('quota'))
        assert result['status'] == 'failed'
        assert calls == 3, '配额类错误仍应重试满 max_retries 次'

    def test_generic_error_retries_and_fails(self):
        result, calls = self._run(ConnectionError('boom'))
        assert result['status'] == 'failed'
        assert calls == 3
        assert 'boom' in result['error']

    def test_quick_mode_shortens_rate_wait(self):
        registered = SimpleNamespace(
            domain_name='EXAMPLE.COM', registrar='Example Registrar', creation_date=None,
            expiration_date=None, updated_date=None, name_servers=None, status=None,
        )
        with mock.patch('domain_checker.checker.time.sleep') as sleep, \
                mock.patch('domain_checker.checker.random.uniform', return_value=0.02):
            result, calls = self._run(lambda *args, **kwargs: registered,
                                      {'rate_limit_delay': 1.0}, query_mode='quick')
        assert result['status'] == 'success'
        assert calls == 1
        assert sleep.call_args_list[0].args[0] < 0.13

    def test_brief_mode_limits_whois_to_one_attempt(self):
        import whois as whois_mod

        result, calls = self._run(
            whois_mod.exceptions.WhoisQuotaExceededError('quota'),
            query_mode='brief',
        )

        assert result['status'] == 'failed'
        assert calls == 1


class TestDomainHold:
    def test_client_hold_is_domain_block_and_skips_dns(self, monkeypatch):
        whois_result = {
            'domain': 'cartutuoficina.com', 'status': 'success',
            'registrar': 'Example Registrar', 'registration_date': None,
            'expiration_date': None, 'updated_date': None, 'name_servers': None,
            'dnssec': None, 'whois_status': 'clientHold, clientTransferProhibited',
            'hold_status': None, 'error': None,
        }
        monkeypatch.setattr('domain_checker.checker.query_whois_with_retry', lambda domain, **kwargs: whois_result)
        dns_check = mock.Mock()
        monkeypatch.setattr('domain_checker.checker.check_domain_resolved', dns_check)

        result = process_single_domain('cartutuoficina.com', platform='whois')

        assert result['resolved'] is False
        assert result['hold_status'] == 'clientHold'
        assert result['block_reason'].startswith('停止解析（域名被封）')
        assert 'clientHold' in result['block_reason']
        assert result['query_time']
        assert result['query_duration_seconds'] >= 0
        dns_check.assert_not_called()

    def test_brief_mode_passes_minimal_dns_options(self, monkeypatch):
        registration_result = {
            'domain': 'example.com', 'status': 'success',
            'registrar': None, 'registration_date': None, 'expiration_date': None,
            'updated_date': None, 'name_servers': None, 'dnssec': None,
            'contact_email': None, 'whois_status': 'active', 'hold_status': None,
            'error': None, 'raw_response': None,
        }
        dns_result = {
            'resolved': True, 'dns_records': ['1.2.3.4'],
            'http_accessible': None, 'block_reason': None,
        }
        monkeypatch.setattr(
            'domain_checker.checker.query_registration_with_retry',
            mock.Mock(return_value=registration_result),
        )
        dns_check = mock.Mock(return_value=dns_result)
        monkeypatch.setattr('domain_checker.checker.check_domain_resolved', dns_check)

        result = process_single_domain('example.com', query_mode='brief', query_timeout=3)

        assert result['resolved'] is True
        dns_check.assert_called_once_with(
            'example.com', timeout=3, attempts=1, check_http=False,
        )

    def test_pause_after_registration_blocks_dns_until_resume(self, monkeypatch):
        import threading
        import time

        task_id = 'PAUSEDNS'
        registration_done = threading.Event()
        result_holder = {}
        registration_result = {
            'domain': 'example.com', 'status': 'success',
            'registrar': None, 'registration_date': None, 'expiration_date': None,
            'updated_date': None, 'name_servers': None, 'dnssec': None,
            'contact_email': None, 'whois_status': 'active', 'hold_status': None,
            'error': None, 'raw_response': None,
        }

        def registration_query(*args, **kwargs):
            task_pause_flags[task_id] = True
            registration_done.set()
            return registration_result

        dns_check = mock.Mock(return_value={
            'resolved': True, 'dns_records': ['1.2.3.4'],
            'http_accessible': None, 'block_reason': None,
        })
        monkeypatch.setattr('domain_checker.checker.query_registration_with_retry', registration_query)
        monkeypatch.setattr('domain_checker.checker.check_domain_resolved', dns_check)
        with task_lock:
            task_storage[task_id] = {'status': 'processing'}

        def run_query():
            threading.current_thread().task_id = task_id
            result_holder['result'] = process_single_domain('example.com', platform='whois')

        worker = threading.Thread(target=run_query)
        try:
            worker.start()
            assert registration_done.wait(1)
            time.sleep(0.15)
            dns_check.assert_not_called()
            assert worker.is_alive()
            task_pause_flags[task_id] = False
            worker.join(timeout=2)
            assert not worker.is_alive()
            assert result_holder['result']['resolved'] is True
            dns_check.assert_called_once_with('example.com', timeout=None)
        finally:
            task_pause_flags.pop(task_id, None)
            with task_lock:
                task_storage.pop(task_id, None)

    def test_pause_during_final_dns_blocks_result_submission(self, monkeypatch):
        import threading
        import time

        task_id = 'PAUSEFINAL'
        dns_done = threading.Event()
        result_holder = {}
        registration_result = {
            'domain': 'example.com', 'status': 'success',
            'registrar': None, 'registration_date': None, 'expiration_date': None,
            'updated_date': None, 'name_servers': None, 'dnssec': None,
            'contact_email': None, 'whois_status': 'active', 'hold_status': None,
            'error': None, 'raw_response': None,
        }

        def dns_query(*args, **kwargs):
            task_pause_flags[task_id] = True
            dns_done.set()
            return {
                'resolved': True, 'dns_records': ['1.2.3.4'],
                'http_accessible': None, 'block_reason': None,
            }

        monkeypatch.setattr(
            'domain_checker.checker.query_registration_with_retry',
            mock.Mock(return_value=registration_result),
        )
        monkeypatch.setattr('domain_checker.checker.check_domain_resolved', dns_query)
        with task_lock:
            task_storage[task_id] = {'status': 'processing'}

        def run_query():
            threading.current_thread().task_id = task_id
            result_holder['result'] = process_single_domain(
                'example.com', platform='whois', query_mode='brief',
            )

        worker = threading.Thread(target=run_query)
        try:
            worker.start()
            assert dns_done.wait(1)
            time.sleep(0.15)
            assert worker.is_alive()
            assert 'result' not in result_holder
            task_pause_flags[task_id] = False
            worker.join(timeout=2)
            assert not worker.is_alive()
            assert result_holder['result']['resolved'] is True
        finally:
            task_pause_flags.pop(task_id, None)
            with task_lock:
                task_storage.pop(task_id, None)

    def test_single_domain_duration_excludes_pause_wait(self, monkeypatch):
        registration_result = {
            'domain': 'held.com', 'status': 'success',
            'registrar': None, 'registration_date': None, 'expiration_date': None,
            'updated_date': None, 'name_servers': None, 'dnssec': None,
            'contact_email': None, 'whois_status': 'clientHold', 'hold_status': None,
            'error': None, 'raw_response': None,
        }
        monkeypatch.setattr(
            'domain_checker.checker.query_registration_with_retry',
            mock.Mock(return_value=registration_result),
        )
        monkeypatch.setattr(
            'domain_checker.checker.get_task_paused_duration',
            mock.Mock(side_effect=[2.0, 7.0]),
        )
        monkeypatch.setattr(
            'domain_checker.checker.time.perf_counter',
            mock.Mock(side_effect=[100.0, 110.0]),
        )

        result = process_single_domain('held.com', platform='whois')

        assert result['query_duration_seconds'] == 5.0

    def test_whois_network_failure_falls_back_to_rdap_hold(self, monkeypatch):
        payload = {
            'ldhName': 'HANDWERKSZUBEHOER.COM',
            'status': ['client hold', 'client transfer prohibited', 'client update prohibited'],
            'nameservers': [{'ldhName': 'NS1.EXAMPLE.COM'}],
        }
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        monkeypatch.setattr('domain_checker.checker.whois.whois', mock.Mock(side_effect=socket.gaierror()))
        monkeypatch.setattr('domain_checker.checker.urllib.request.urlopen', mock.Mock(return_value=response))
        monkeypatch.setattr('domain_checker.checker.time.sleep', mock.Mock())
        dns_check = mock.Mock()
        monkeypatch.setattr('domain_checker.checker.check_domain_resolved', dns_check)

        result = process_single_domain('handwerkszubehoer.com', query_mode='quick', platform='whois')

        assert result['status'] == 'success'
        assert result['hold_status'] == 'clientHold'
        assert result['resolved'] is False
        assert result['block_reason'].startswith('停止解析（域名被封）')
        dns_check.assert_not_called()


class TestSubdomainRegistrationLookup:
    def test_com_subdomain_queries_parent_and_keeps_original_domain(self, monkeypatch):
        payload = {'ldhName': 'BAIDU.COM', 'status': ['active'], 'entities': []}
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        urlopen = mock.Mock(return_value=response)
        whois_query = mock.Mock(side_effect=socket.gaierror())
        dns_result = {
            'resolved': True, 'dns_records': ['1.2.3.4'],
            'http_accessible': True, 'block_reason': None,
        }
        dns_check = mock.Mock(return_value=dns_result)
        monkeypatch.setattr('domain_checker.checker.whois.whois', whois_query)
        monkeypatch.setattr('domain_checker.checker.urllib.request.urlopen', urlopen)
        monkeypatch.setattr('domain_checker.checker.check_domain_resolved', dns_check)

        result = process_single_domain(
            'www.baidu.com', query_mode='unlimited', query_timeout=5, platform='whois',
        )

        assert whois_query.call_args.args[0] == 'baidu.com'
        assert urlopen.call_args.args[0].full_url.endswith('/baidu.com')
        assert urlopen.call_args.kwargs['timeout'] == 5
        assert '/www.baidu.com' not in urlopen.call_args.args[0].full_url
        assert result['domain'] == 'www.baidu.com'
        assert result['status'] == 'success'
        assert json.loads(result['raw_response']) == payload
        dns_check.assert_called_once_with('www.baidu.com', timeout=5)


def test_unlimited_mode_adds_no_active_wait(monkeypatch):
    from domain_checker.checker import query_whois_with_retry

    registered = SimpleNamespace(
        domain_name='EXAMPLE.COM', registrar='Example Registrar', creation_date=None,
        expiration_date=None, updated_date=None, name_servers=None, status=None, dnssec=None,
    )
    sleep = mock.Mock()
    whois_query = mock.Mock(return_value=registered)
    monkeypatch.setattr('domain_checker.checker.whois.whois', whois_query)
    monkeypatch.setattr('domain_checker.checker.time.sleep', sleep)

    result = query_whois_with_retry('example.com', query_mode='unlimited', timeout=9)

    assert result['status'] == 'success'
    assert whois_query.call_args.kwargs['timeout'] == 9
    sleep.assert_not_called()


def test_timeout_is_applied_per_whois_attempt_and_rdap_fallback(monkeypatch):
    from domain_checker import settings
    from domain_checker.checker import query_whois_with_retry

    with settings.config_lock:
        original_retries = settings.CONFIG['max_retries']
        settings.CONFIG['max_retries'] = 3
    whois_query = mock.Mock(side_effect=socket.timeout('timed out'))
    rdap_fallback = mock.Mock(return_value=None)
    monkeypatch.setattr('domain_checker.checker.whois.whois', whois_query)
    monkeypatch.setattr('domain_checker.checker._query_rdap_fallback', rdap_fallback)
    monkeypatch.setattr('domain_checker.checker.time.sleep', mock.Mock())
    try:
        result = query_whois_with_retry('example.com', query_mode='unlimited', timeout=3)
    finally:
        with settings.config_lock:
            settings.CONFIG['max_retries'] = original_retries

    assert result['status'] == 'timeout'
    assert whois_query.call_count == 3
    assert all(call.kwargs['timeout'] == 3 for call in whois_query.call_args_list)
    rdap_fallback.assert_called_once_with('example.com', 3)


def test_whois_success_preserves_raw_response(monkeypatch):
    from domain_checker.checker import query_whois_with_retry

    registered = SimpleNamespace(
        domain_name='EXAMPLE.COM', registrar='Example Registrar', creation_date=None,
        expiration_date=None, updated_date=None, name_servers=None, status='ok', dnssec=None,
        text='Domain Name: EXAMPLE.COM\nDomain Status: ok',
    )
    monkeypatch.setattr('domain_checker.checker.whois.whois', mock.Mock(return_value=registered))

    result = query_whois_with_retry('example.com', query_mode='unlimited')

    assert result['raw_response'] == 'Domain Name: EXAMPLE.COM\nDomain Status: ok'


class TestRdapPrimaryQuery:
    @staticmethod
    def _response(payload):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return response

    def test_public_suffix_normalizes_co_uk_subdomain(self):
        from domain_checker.checker import _registration_lookup_domain

        assert _registration_lookup_domain('www.shop.example.co.uk') == 'example.co.uk'

    def test_iana_bootstrap_discovers_authoritative_service(self, monkeypatch):
        from domain_checker import checker

        bootstrap = {'services': [[['uk'], ['https://rdap.example.test/uk/']]]}
        payload = {'ldhName': 'EXAMPLE.CO.UK', 'status': ['active']}
        urlopen = mock.Mock(side_effect=[self._response(bootstrap), self._response(payload)])
        monkeypatch.setattr(checker, '_rdap_bootstrap_services', {})
        monkeypatch.setattr(checker, '_rdap_bootstrap_expires_at', 0.0)
        monkeypatch.setattr(checker.urllib.request, 'urlopen', urlopen)

        result = checker.query_rdap('www.example.co.uk', timeout=4)

        assert result['status'] == 'success'
        assert result['domain'] == 'www.example.co.uk'
        assert urlopen.call_args_list[0].args[0].full_url == checker._RDAP_BOOTSTRAP_URL
        assert urlopen.call_args_list[1].args[0].full_url == (
            'https://rdap.example.test/uk/domain/example.co.uk'
        )
        assert all(call.kwargs['timeout'] == 4 for call in urlopen.call_args_list)

    def test_bootstrap_is_reused_from_cache(self, monkeypatch):
        from domain_checker import checker

        bootstrap = {'services': [[['test'], ['https://rdap.example.test/']]]}
        payload = {'ldhName': 'EXAMPLE.TEST', 'status': ['active']}
        urlopen = mock.Mock(side_effect=[
            self._response(bootstrap), self._response(payload), self._response(payload),
        ])
        monkeypatch.setattr(checker, '_rdap_bootstrap_services', {})
        monkeypatch.setattr(checker, '_rdap_bootstrap_expires_at', 0.0)
        monkeypatch.setattr(checker.urllib.request, 'urlopen', urlopen)

        checker.query_rdap('one.test', timeout=2)
        checker.query_rdap('two.test', timeout=2)

        bootstrap_calls = [
            call for call in urlopen.call_args_list
            if call.args[0].full_url == checker._RDAP_BOOTSTRAP_URL
        ]
        assert len(bootstrap_calls) == 1

    def test_rdap_failure_falls_back_to_whois_without_loop(self, monkeypatch):
        from domain_checker import checker

        monkeypatch.setattr(
            checker, 'query_rdap',
            mock.Mock(return_value=checker.failed_result('example.com', 'RDAP 请求失败')),
        )
        whois_query = mock.Mock(return_value=checker.not_registered_result('example.com'))
        monkeypatch.setattr(checker, 'query_whois_with_retry', whois_query)

        result = checker.query_rdap_with_whois_fallback(
            'example.com', query_mode='unlimited', timeout=3,
        )

        assert result['status'] == 'not_registered'
        whois_query.assert_called_once_with(
            'example.com', query_mode='unlimited', timeout=3, allow_rdap_fallback=False,
        )

    def test_rdap_success_log_does_not_claim_dns_started(self, monkeypatch):
        import threading

        from domain_checker import checker

        task_id = 'RDAPLOG'
        result = checker._base_whois_result('held.com', 'success', None)
        result['whois_status'] = 'client hold'
        with task_lock:
            task_storage[task_id] = {'logs': []}
        monkeypatch.setattr(checker, 'query_rdap', mock.Mock(return_value=result))
        current_thread = threading.current_thread()
        previous_task_id = getattr(current_thread, 'task_id', None)
        current_thread.task_id = task_id
        try:
            checker.query_rdap_with_whois_fallback('held.com', timeout=3)
            with task_lock:
                messages = [item['message'] for item in task_storage[task_id]['logs']]
            assert '[held.com] RDAP 查询成功' in messages
            assert not any('开始检查 DNS' in message for message in messages)
        finally:
            if previous_task_id is None:
                del current_thread.task_id
            else:
                current_thread.task_id = previous_task_id
            with task_lock:
                task_storage.pop(task_id, None)
