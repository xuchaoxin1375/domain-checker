"""域名解析与校验单元测试。"""

from domain_checker.domains import extract_domain, is_valid_domain, parse_domain_input


class TestIsValidDomain:
    def test_common_domains(self):
        assert is_valid_domain('example.com')
        assert is_valid_domain('www.example.com')
        assert is_valid_domain('a-b.example.co')
        assert is_valid_domain('sub.domain.museu.museum')
        # punycode TLD 含数字与连字符，按当前规则视为非法（如实锁定现有行为）
        assert not is_valid_domain('xn--fiq228c.xn--fiqs8s')

    def test_invalid(self):
        assert not is_valid_domain('')
        assert not is_valid_domain('example')
        assert not is_valid_domain('-bad.com')
        assert not is_valid_domain('bad-.com')
        assert not is_valid_domain('example.c')
        assert not is_valid_domain('_example.com')
        assert not is_valid_domain('exa mple.com')


class TestExtractDomain:
    def test_plain(self):
        assert extract_domain('example.com') == 'example.com'
        assert extract_domain('EXAMPLE.COM') == 'example.com'
        assert extract_domain(' www.example.com ') == 'www.example.com'

    def test_url(self):
        # 注意：URL 输入会去掉 www 前缀（原始正则行为），纯文本输入则保留
        assert extract_domain('https://example.com') == 'example.com'
        assert extract_domain('https://www.example.com') == 'example.com'
        assert extract_domain('http://example.com/path') == 'example.com'
        assert extract_domain('https://www.qq.com/path/to/page?a=1') == 'qq.com'
        assert extract_domain('//www.example.com/x') == 'example.com'
        assert extract_domain('https://sub.example.com/x') == 'sub.example.com'

    def test_no_match(self):
        assert extract_domain('not a domain at all!') is None
        assert extract_domain('::::') is None


class TestParseDomainInput:
    def test_multiline_and_dedup(self):
        text = 'example.com\nwww.example.com\nexample.com\nhttps://abc.org/p\n\n'
        assert parse_domain_input(text) == ['example.com', 'www.example.com', 'abc.org']

    def test_empty(self):
        assert parse_domain_input('') == []
        assert parse_domain_input('\n\n  \n') == []

    def test_skips_invalid_lines(self):
        assert parse_domain_input('???\nok-domain.com') == ['ok-domain.com']
