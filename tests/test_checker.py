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
