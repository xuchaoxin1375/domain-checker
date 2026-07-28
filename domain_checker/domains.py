"""输入文本解析与域名格式校验。"""

import re

_DOMAIN_PATTERN = r'^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$'


def parse_domain_input(text: str) -> list:
    """解析输入文本，支持纯域名和URL格式

    支持格式:
    - example.com
    - www.example.com
    - https://example.com
    - https://www.example.com
    - http://example.com/path (只取域名部分)

    返回去重后（保持输入顺序）的域名列表。
    """
    domains = []
    lines = text.strip().split('\n')

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 提取域名
        domain = extract_domain(line)
        if domain:
            domains.append(domain)

    # 去重保持顺序
    seen = set()
    unique_domains = []
    for d in domains:
        if d not in seen:
            seen.add(d)
            unique_domains.append(d)

    return unique_domains


def extract_domain(text: str) -> str:
    """从URL或纯域名中提取域名。"""
    text = text.strip().lower()

    # 已经是纯域名
    if is_valid_domain(text):
        return text

    # 尝试从URL中提取
    # 匹配协议 + 可能的www + 域名 + 可选路径
    patterns = [
        r'https?://(?:www\.)?([^/]+)',                    # https://www.example.com/path
        r'//(?:www\.)?([^/]+)',                           # //www.example.com/path
        r'(?:www\.)?([a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,})',  # 兜底
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            domain = match.group(1).strip()
            if is_valid_domain(domain):
                return domain

    return None


def is_valid_domain(domain: str) -> bool:
    """验证域名格式。"""
    return bool(re.match(_DOMAIN_PATTERN, domain.strip()))
