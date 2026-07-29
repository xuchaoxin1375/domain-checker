# AGENTS.md — 代码助手接手指南

写给 coding agent（以及想快速上手的人）：先读这里的速览与陷阱，再到
[`spec/README.md`](spec/README.md) 按任务选择专题规格，动手前按"常用命令"自检。

## 项目速览

中文界面的域名批量查询 Web 应用：Flask + SQLite + 原生 JS 单页前端。
批量 WHOIS 查询 + DNS 解析状态检查，支持暂停/继续、历史记录、CSV/Excel 导出。

- 语言：Python ≥ 3.10，界面文案、日志、注释均为**中文**，修改时保持一致
- 无前端构建步骤：`templates/index.html` 直接编辑生效，无需任何打包
- 数据库与运行配置在 `data/`，其中 `domain_checker.db` 已入库（含真实用户数据），
  `settings.json` 为运行时文件（已 gitignore），**测试不得触碰真实 data/**

## 常用命令

```bash
# 环境（仓库根目录）
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

pytest            # 全部测试（网络调用已 mock，可离线运行）
ruff check .      # 代码检查（CI 同款，配置在 pyproject.toml）
python app.py     # 启动，默认 5000 端口；控制台会打印局域网访问状态
python cli.py --help
```

## 结构地图

| 位置 | 你要改什么时来这里 |
|------|------------------|
| `domain_checker/settings.py` | 配置项、持久化、环境变量、平台元信息 |
| `domain_checker/domains.py` | 域名解析/校验 |
| `domain_checker/checker.py` | WHOIS 重试、DNS 检查、单域名流水线、面向用户的原因文案 |
| `domain_checker/db.py` | SQLite 结构与读写（`init_db` 幂等） |
| `domain_checker/tasks.py` | 批量任务编排、暂停语义 |
| `domain_checker/export.py` | 导出表头/状态文案集中在此 |
| `domain_checker/operations.py` | 服务启动/终止操作日志（`data/operations.log`） |
| `domain_checker/web.py` | 全部 HTTP API 与启动逻辑；路由薄、逻辑薄 |
| `templates/index.html` | 前端；Tab 切换按 `data-tab` 属性定位（勿回退到全局 `event`） |
| `tests/` | 每类功能一个文件；conftest 已隔离数据目录 |
| `docs/ARCHITECTURE.md` | 想改架构/流程前先读它 |
| `spec/README.md` | Coding Agent 渐进式规格入口，按任务选读 |

## 必须遵守的约束

1. **不要触碰真实数据**：测试一律通过 `DOMAIN_CHECKER_DATA_DIR` 临时目录（conftest 已做好）。
   不要删除/重建 `data/domain_checker.db`——里面有用户真实查询历史。
2. **网络调用要 mock**：测试禁止请求 WHOIS/DNS；用 `unittest.mock` 打 `dns.resolver.Resolver`
   等桩（参照 `tests/test_checker.py` 的 `_fake` 模式，dnspython 2.8 异常构造比较麻烦）。
3. **import 无环依赖**：方向 `web → tasks → checker → settings/state`；新模块保持单向。
4. **保持 API 与文案兼容**：JSON 字段名、导出头、前端所依赖的字段（如 `server`）改动需同步
   前端 + `docs/API.md`；面向用户的解析状态文案改动需同步导出（export.py）与 README 表格。
5. **幂等初始化**：debug reloader 会以两个进程各初始化一次应用；任何模块级副作用都必须幂等
   （参考 `db.init_db` 的 `CREATE TABLE IF NOT EXISTS`）。
6. **lint 配置有意的取舍**：中文全角标点（RUF001-003）已显式忽略，不要"修正常";`== False`
   等三值布尔比较（True/False/None）语义化保留，如需改写用 `is False`。
7. 线程内访问 `task_storage/CONFIG` 持锁（`task_lock`/`config_lock`），锁内不做数据库 IO。
8. **默认不做截图检查**：除非用户明确要求截图，否则前端验证使用自动测试、JavaScript 语法检查或
   无截图的浏览器 DOM 检查。

## 改动的完成 checklist

- [ ] `pytest` 与 `ruff check .` 全绿
- [ ] 涉及 API 或行为：更新 `docs/API.md` / `docs/ARCHITECTURE.md` / README 相应表格
- [ ] 面向用户的文案：同步前端、导出、`README` 状态说明
- [ ] 新增配置项：写入 `settings.CONFIG` 默认值、类型转换分支（api_config）、README 配置表
- [ ] 在 `CHANGELOG.md` 的 `[Unreleased]` 区补一条

## 常见任务速查

- **加查询平台**：见 ARCHITECTURE「扩展指南」。注意三处同步：`settings.PLATFORMS`、
  前端 `platforms` 对象（desc/implemented）、README 平台表；前端不读后端 PLATFORMS
- **改「未注册」语义**：`checker._NOT_FOUND_MARKERS` 与 `NOT_REGISTERED_MESSAGE`；
  未注册是确定性结论立即返回，切勿再加重试
- **加导出格式**：`export.py` 新增函数 + `create_export_file` 分派 + `web.api_export` mimetype
- **加 API**：`web._register_routes` 分组内添加；补 `tests/test_api.py`
- **改启动监听**：`web.run_server`（`DOMAIN_CHECKER_HOST/PORT` 与 `allow_lan_access` 的优先级）
