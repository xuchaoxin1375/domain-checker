"""pytest 公共夹具。

关键：在 import 业务包之前，将数据目录指向临时目录，
避免测试读写仓库里的真实 data/domain_checker.db。
测试一律 mock 网络调用（whois / dns.resolver），不访问外网。
"""

import os
import shutil
import sys
import tempfile

# 保证可导入仓库内的包（无论从哪个目录运行 pytest）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 必须在 import domain_checker 之前设置
_TMP_DIR = tempfile.mkdtemp(prefix='domain-checker-test-')
os.environ['DOMAIN_CHECKER_DATA_DIR'] = _TMP_DIR

import pytest


@pytest.fixture(scope='session')
def app():
    from domain_checker.web import app as flask_app
    flask_app.config.update(TESTING=True)
    yield flask_app
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def tmp_dir():
    """临时数据目录路径。"""
    return _TMP_DIR
