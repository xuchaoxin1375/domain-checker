"""DNS 解析检查单元测试（mock dns.resolver，不访问外网）。

重点验证各种 DNS 异常对应的中文提示是准确的，
尤其是 NXDOMAIN 不再被误报为"域名不存在"。
"""

from unittest import mock

import pytest

dns = pytest.importorskip('dns.resolver')

from domain_checker.checker import check_domain_resolved


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
        r = self._check(_fake(dns.Timeout))
        assert r['resolved'] is None
        assert '超时' in r['block_reason']


class TestQueryWhoisNotRegistered:
    """未注册域名：应立即返回 not_registered，不重试（重试只是浪费时间）。"""

    def _run(self, side_effect, cfg_overrides=None):
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
            with mock.patch.object(whois_mod, 'whois') as m_whois:
                m_whois.side_effect = side_effect
                result = query_whois_with_retry('not-a-real-domain-xyz.com')
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

    def test_message_marker_skips_retry(self):
        import whois as whois_mod
        result, calls = self._run(whois_mod.exceptions.PywhoisError('No match for "X.COM".'))
        assert result['status'] == 'not_registered'
        assert calls == 1

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
