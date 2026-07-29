"""生产部署模板的关键约束测试。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_gunicorn_template_keeps_in_memory_tasks_in_one_worker():
    config = (ROOT / 'deploy' / 'gunicorn.conf.py').read_text(encoding='utf-8')

    assert 'workers = 1' in config
    assert "worker_class = 'gthread'" in config
    assert "127.0.0.1" in config
    assert "record_operation('启动'" in config
    assert "record_operation('终止'" in config


def test_nginx_and_systemd_templates_keep_backend_private():
    nginx = (ROOT / 'deploy' / 'nginx-domain-checker.conf').read_text(encoding='utf-8')
    service = (ROOT / 'deploy' / 'domain-checker.service').read_text(encoding='utf-8')
    environment = (ROOT / 'deploy' / 'domain-checker.env.example').read_text(encoding='utf-8')

    assert 'proxy_pass http://127.0.0.1:5000;' in nginx
    assert 'auth_basic "Domain Checker";' in nginx
    assert 'ExecStart=/opt/domain-checker/.venv/bin/gunicorn' in service
    assert 'ReadWritePaths=/var/lib/domain-checker' in service
    assert 'DOMAIN_CHECKER_HOST=127.0.0.1' in environment


def test_deployment_guide_recommends_uv_and_covers_bt_panel():
    guide = (ROOT / 'docs' / 'DEPLOYMENT_NGINX.md').read_text(encoding='utf-8')

    assert '推荐 **uv + 项目内 `.venv`**' in guide
    assert 'uv sync --extra prod --no-dev --frozen' in guide
    assert '不要同时在\n宝塔“Python 项目”与 systemd 中启动应用' in guide
    assert '对应路径的面板“访问限制”规则可能失效' in guide
    assert '关闭 `/api/` 的缓存' in guide
