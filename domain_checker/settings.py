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
    'timeout': 15,                    # 超时时间(秒)
    'max_workers': 5,                 # 并发线程数
    'proxy_enabled': False,           # 是否启用代理
    'proxy_url': 'http://127.0.0.1:7897',  # 代理地址
    'proxy_auth': None,
    'platform': 'whois',              # 查询平台: whois, whoisxml, rdap
    'allow_lan_access': True,         # 是否允许局域网访问（关闭后仅监听 127.0.0.1，重启生效）
}

# 保存到 settings.json / 历史记录快照时剔除的敏感字段
PRIVATE_CONFIG_KEYS = {'proxy_auth'}

# 查询平台元信息（仅用于界面展示与记录；当前实现统一走 WHOIS 协议，
# 接入真实的多平台查询属于已知扩展点，见 docs/ARCHITECTURE.md）
PLATFORMS = {
    'whois': {'name': 'WHOIS标准查询', 'icon': '🔍', 'desc': '使用标准WHOIS协议'},
    'whoisxml': {'name': 'WHOIS XML', 'icon': '🌐', 'desc': '使用WHOIS XML API'},
    'rdap': {'name': 'RDAP安全查询', 'icon': '🛡️', 'desc': '使用RDAP协议，更安全'}
}


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
                        CONFIG[k] = v
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
