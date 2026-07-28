#!/usr/bin/env python3
"""域名批量查询系统 —— Web 服务启动入口。

用法:
    python app.py

所有业务代码位于 domain_checker 包中；本文件只做启动。
常用环境变量见 domain_checker/settings.py 的模块文档。
"""

from domain_checker.web import app, run_server

__all__ = ['app', 'run_server']

if __name__ == '__main__':
    run_server()
