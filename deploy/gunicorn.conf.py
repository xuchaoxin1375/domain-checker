"""域名查询服务的 Gunicorn 生产配置。"""

import os

bind = f"{os.getenv('DOMAIN_CHECKER_HOST', '127.0.0.1')}:{os.getenv('DOMAIN_CHECKER_PORT', '5000')}"

# 任务状态保存在进程内存中，多 worker 会让轮询请求读到不同状态。
workers = 1
worker_class = 'gthread'
threads = 8
timeout = 180
graceful_timeout = 30
keepalive = 5

accesslog = '-'
errorlog = '-'
capture_output = True


def post_fork(server, worker):
    """在实际提供请求的 worker 中同步运行状态并记录启动。"""
    from domain_checker import settings
    from domain_checker.operations import record_operation

    host = os.getenv('DOMAIN_CHECKER_HOST', '127.0.0.1')
    port = int(os.getenv('DOMAIN_CHECKER_PORT', '5000'))
    settings.SERVER_RUNTIME.update({'host': host, 'port': port})
    record_operation('启动', f'Gunicorn worker 已启动，监听 {host}:{port}，pid={worker.pid}')


def worker_exit(server, worker):
    """正常退出或重启 worker 时写入网页操作日志。"""
    from domain_checker.operations import record_operation

    record_operation('终止', f'Gunicorn worker 已停止，pid={worker.pid}')

