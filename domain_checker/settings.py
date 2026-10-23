"""路径、全局配置与配置持久化。

配置的生效顺序（后者覆盖前者）：
1. 本模块中的 ``CONFIG`` 默认值
2. ``data/settings.json``（网页端保存配置时自动写入，重启后自动恢复）

可用环境变量：
    DOMAIN_CHECKER_DATA_DIR  数据目录（默认 <仓库根目录>/data）
    DOMAIN_CHECKER_HOST      监听地址（默认按 allow_lan_access 推导 0.0.0.0/127.0.0.1）
    DOMAIN_CHECKER_PORT      监听端口（默认 5000）
    DOMAIN_CHECKER_DEBUG     Flask debug 开关（默认 1，置 0 关闭）
"""

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# 仓库根目录（本文件位于 <repo>/domain_checker/settings.py）
REPO_ROOT = Path(__file__).resolve().parent.parent

# 数据目录与其下的关键文件
DATA_DIR = Path(os.environ.get('DOMAIN_CHECKER_DATA_DIR', str(REPO_ROOT / 'data')))
DB_PATH = Path(os.environ.get('DOMAIN_CHECKER_DB', str(DATA_DIR / 'domain_checker.db')))
SETTINGS_PATH = DATA_DIR / 'settings.json'
TEMPLATES_DIR = REPO_ROOT / 'templates'

# 确保数据目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 服务器实际监听状态（run_server 启动时写入，用于网页端展示）
SERVER_RUNTIME = {'host': None, 'port': 5000}

# 配置（可动态修改）。所有对 CONFIG 的读写都应持有 config_lock。
config_lock = threading.Lock()
CONFIG = {
    'max_domains_per_batch': 500,     # 单批最大域名数
    'rate_limit_delay': 1.0,          # 请求间隔(秒)
    'max_retries': 3,                 # 最大重试次数
    'retry_delay': 2,                 # 重试间隔(秒)
    'timeout': 15,                    # WHOIS/RDAP/DNS/HTTP 每次网络请求超时(秒)
    'max_workers': 5,                 # 每个任务的并发线程数（1-32）
    'reuse_recent_seconds': 3600,     # 最近成功结果的复用窗口(秒)，默认 1 小时；0 表示关闭
    'proxy_enabled': False,           # 是否启用代理
    'proxy_url': 'http://127.0.0.1:7897',  # 代理地址
    'proxy_auth': None,
    'platform': 'rdap',               # 查询平台: rdap（默认）, whois, whoisxml
    'allow_lan_access': True,         # 是否允许局域网访问（关闭后仅监听 127.0.0.1，重启生效）
}

TIMEOUT_MIN_SECONDS = 1
TIMEOUT_MAX_SECONDS = 120

# 配置项取值范围：网页、API 与 settings.json 共用同一套归一化规则，避免非法值
# 在任务线程里抛异常，或让单个任务创建尺寸失控的线程池。
# 并发上限刻意收敛：实际吞吐仍受目标 WHOIS/RDAP 服务限流、网络时延与超时约束，
# 线程数远超对端许可时只会增加超时和限流概率。
CONFIG_LIMITS = {
    'max_domains_per_batch': (1, 2000),
    'rate_limit_delay': (0.0, 60.0),
    'max_retries': (1, 10),
    'retry_delay': (0.0, 300.0),
    'timeout': (TIMEOUT_MIN_SECONDS, TIMEOUT_MAX_SECONDS),
    'max_workers': (1, 32),
    'reuse_recent_seconds': (0, 86400),
}


def coerce_config_value(key: str, value):
    """按配置项类型与范围归一化外部输入。

    无法转换时抛出 ``ValueError``（消息可直接返回给前端）；数值超出范围时
    收敛到边界，与网页输入框的 min/max 保持一致。
    """
    if key in ('proxy_enabled', 'allow_lan_access'):
        return bool(value)
    if key == 'platform':
        return value if value in PLATFORMS else 'rdap'
    if key in ('proxy_url', 'proxy_auth'):
        return value

    limits = CONFIG_LIMITS.get(key)
    if limits is None:
        return value

    low, high = limits
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{key} 必须是数字') from None
    if number != number or number in (float('inf'), float('-inf')):
        raise ValueError(f'{key} 必须是有效数字')
    if isinstance(low, int):
        return max(low, min(high, int(number)))
    return max(low, min(high, float(number)))


def normalize_timeout(value) -> int:
    """将外部超时配置限制在可用范围内，避免无效设置中断任务线程。"""
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 15
    return max(TIMEOUT_MIN_SECONDS, min(TIMEOUT_MAX_SECONDS, value))

# 查询模式只作用于当前任务，不写入全局配置。不限流模式取消主动等待，仍受并发、超时和重试次数约束。
QUERY_MODES = {
    'unlimited': {'name': '不限流模式', 'rate_limit_delay': 0.0, 'retry_delay': 0.0},
    'standard': {'name': '标准模式', 'rate_limit_delay': None, 'retry_delay': None},
    'quick': {'name': '快速模式', 'rate_limit_delay': 0.1, 'retry_delay': 0.5},
    'brief': {
        'name': '简略快速模式', 'rate_limit_delay': 0.0, 'retry_delay': 0.0,
        'max_retries': 1, 'dns_attempts': 1, 'http_probe': False,
    },
}


def normalize_query_mode(mode: str | None) -> str:
    """将外部查询模式收敛为受支持的值。"""
    return mode if isinstance(mode, str) and mode in QUERY_MODES else 'unlimited'

# 保存到 settings.json / 历史记录快照时剔除的敏感字段
PRIVATE_CONFIG_KEYS = {'proxy_auth'}

# 查询平台元信息。implemented=False 的平台当前未接入专用接口，
# 实际查询自动回落 WHOIS 标准协议（前后端都会向用户明示，避免误解）。
# 接入真实的多平台查询属于已知扩展点，见 docs/ARCHITECTURE.md「扩展指南」。
PLATFORMS = {
    'whois': {
        'name': 'WHOIS标准查询', 'icon': '🔍', 'implemented': True,
        'desc': '使用标准 WHOIS 协议（43端口）直连注册局，信息全面：注册商、注册/过期/更新日期、DNS服务器、DNSSEC'
    },
    'whoisxml': {
        'name': 'WHOIS XML', 'icon': '🌐', 'implemented': False,
        'desc': '第三方 WHOIS XML API（whoisxmlapi.com，需 API 密钥），数据结构化、稳定。暂未接入，当前自动使用 WHOIS 标准协议'
    },
    'rdap': {
        'name': 'RDAP优先查询', 'icon': '🛡️', 'implemented': True,
        'desc': '通过 IANA 自动发现权威 RDAP 服务，使用 HTTPS 获取结构化注册数据；服务不可用时自动回退 WHOIS'
    }
}


def is_platform_implemented(platform: str) -> bool:
    """平台是否已接入专用查询接口（否则实际使用 WHOIS 标准协议）。"""
    return bool(PLATFORMS.get(platform, {}).get('implemented', False))


def public_config() -> dict:
    """当前 CONFIG 的可对外副本（剔除敏感字段）。"""
    with config_lock:
        return {k: v for k, v in CONFIG.items() if k not in PRIVATE_CONFIG_KEYS}


def load_config_from_file():
    """从配置文件加载设置（覆盖默认值），用于重启后保留配置。"""
    try:
        if SETTINGS_PATH.exists():
            saved = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
            with config_lock:
                for k, v in saved.items():
                    if k in CONFIG and k not in PRIVATE_CONFIG_KEYS:
                        try:
                            CONFIG[k] = coerce_config_value(k, v)
                        except ValueError as exc:
                            logger.warning(f'忽略无效配置 {k}={v!r}: {exc}')
            logger.info(f"已加载配置文件: {SETTINGS_PATH}")
    except Exception as e:
        logger.warning(f"加载配置文件失败: {e}")


def save_config_to_file():
    """将当前配置持久化到文件，保证重启后仍可生效（如局域网访问设置）。"""
    try:
        data = public_config()
        SETTINGS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        logger.info(f"配置已保存: {SETTINGS_PATH}")
    except Exception as e:
        logger.warning(f"保存配置文件失败: {e}")


def get_server_info() -> dict:
    """获取服务器实际监听状态。"""
    host = SERVER_RUNTIME.get('host')
    return {
        'bind_host': host,
        'port': SERVER_RUNTIME.get('port', 5000),
        'lan_active': (host == '0.0.0.0') if host else None
    }
