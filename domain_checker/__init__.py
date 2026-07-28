"""domain_checker：域名批量查询系统核心包。

模块职责：
    settings  —— 路径、全局配置、配置持久化
    state     —— 内存中的任务运行状态（进程重启即丢失）
    domains   —— 输入文本解析与域名格式校验
    checker   —— WHOIS 查询、DNS 解析检查、单域名处理流水线
    db        —— SQLite 历史记录读写
    tasks     —— 批量任务的异步编排（线程、暂停/继续）
    export    —— CSV / XLSX 报表导出
    web       —— Flask 应用与 HTTP API
"""

__version__ = '2.6.0'
