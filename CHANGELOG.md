# 更新日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [2.6.0] - 2026-07-28

体验与信息透明度优化版本。

### 变更（性能与行为）

- **查询未注册域名不再耗时重试**：WHOIS 返回"No match/NOT FOUND"等确定性结论时立即返回
  `not_registered`（原地重试 3 次约需 10 秒 → 现在一次即返回），错误文案统一为「域名未被注册」；
  配额/网络等临时性错误仍按原策略重试
- WHOIS 请求接入 `CONFIG['timeout']` 的 socket 超时（旧版 python-whois 自动回退兼容）
- 任务状态新增 `not_registered`：前端表格显示「未注册」徽标与单独统计卡、
  日志级别为 warn、导出文案「域名未被注册」、CLI 显示 `⊘ 未注册`
- 修正 WHOIS 异常捕获顺序：`WhoisDomainNotFoundError`/`WhoisQuotaExceededError`
  是 `PywhoisError` 的子类，此前被父类捕获导致专属分支永远不生效
- 历史记录中旧的"域名不存在/WHOIS解析错误"类失败结果保持原样，新查询才会有新状态

### 新增

- **平台能力提示**：`PLATFORMS` 增加 `implemented` 与详细 `desc`；WHOIS XML / RDAP
  目前未接入专用接口（自动回落 WHOIS 标准协议），页面选择时给出常驻提示与 Toast、
  任务启动时运行日志中也会写明实际查询方式，配置面板下方新增各平台效果与信息介绍
- **页面侧边栏大纲**：查询配置/输入域名/运行日志/查询结果/历史记录 五项锚点跳转，
  支持收起为悬浮按钮，收起状态保存到 localStorage；窄屏（<1250px）自动隐藏

### 测试

- 新增 5 例：未注册快速返回与"不重试"保证、配额错误仍重试、平台元信息完整性、
  导出 not_registered 文案；现共 43 例

## [2.5.0] - 2026-07-28

工程化重构版本：**行为不变**，重点为可维护性、可测试性与文档。

### 变更（结构）

- 将单文件 `app.py`（约 1000 行）拆分为 `domain_checker` 包：
  `settings`（配置/持久化）、`state`（内存任务状态）、`domains`（解析校验）、
  `checker`（WHOIS/DNS/流水线）、`db`（SQLite）、`tasks`（任务编排）、`export`（报表）、
  `web`（Flask 工厂与 API）；`app.py` 仅保留启动入口
- 依赖方向 `web → tasks → checker → settings/state`，无循环导入
- 版本号统一由 `domain_checker.__version__` 驱动（启动横幅）；界面标题同步 v2.5
- 新增环境变量：`DOMAIN_CHECKER_DATA_DIR/DB/HOST/PORT/DEBUG`（见 README 配置表）
- 导出目录改用 `tempfile.gettempdir()`，不再硬编码 `/tmp`

### 新增

- `tests/`：38 个 pytest 用例（网络调用全部 mock，离线可跑；数据目录自动隔离）
- `pyproject.toml`：打包元数据 + ruff/pytest 配置；`requirements-dev.txt`
- CI：GitHub Actions（Python 3.10–3.12，ruff + pytest）
- 文档：重写 README；新增 `docs/ARCHITECTURE.md`（架构/数据流/并发模型/扩展指南）、
  `docs/API.md`（HTTP API 参考）、`AGENTS.md`（代码助手接手指南）、
  `CONTRIBUTING.md`、`CHANGELOG.md`、`LICENSE`（MIT）

### 修复

- `cli.py` 遗留的未使用导入/变量；false 比较改为 `is False`（语义不变）

## [2.4.0] - 2026-07-28

### 修复

- **历史记录无法加载**：`switchTab` 依赖隐式全局 `event`，异步调用时抛错导致
  "加载失败"且无法切回查询页；改为 `data-tab` 属性切换；查看历史时自动展示结果卡片、
  设置当前任务并附带结果条数提示
- **`cli.py` 无法启动**：修正不存在的 `process_domain` 导入

### 新增

- 配置面板新增「允许局域网访问」开关：保存后持久化到 `data/settings.json`，
  重启后生效；页面显示服务器实际监听状态；启动横幅打印当前模式与局域网地址

### 变更（文案更准确）

- DNS 返回 NXDOMAIN 不再显示"域名不存在"：改为"已注册但无解析记录，疑似已被停止解析/冻结"
  （NoAnswer/NoNameservers/Timeout 均有对应准确文案，超时单列为"未知"状态徽标）
- 前后端统一用词：解析列徽标"未解析"、统计"未解析"、导出列"解析异常原因"

## [2.3.0] - 2026-07-27

初始发布基线：批量 WHOIS 查询、暂停/继续/取消、单条与批量重查、多主题、表格排序、
历史记录（SQLite）、CSV/Excel 导出、URL 自动提取域名、限流与自动重试、多平台选择界面。

[Unreleased]: https://github.com/xuchaoxin1375/domain-checker/compare/v2.6.0...HEAD
[2.6.0]: https://github.com/xuchaoxin1375/domain-checker/compare/v2.5.0...v2.6.0
[2.5.0]: https://github.com/xuchaoxin1375/domain-checker/compare/v2.4.0...v2.5.0
[2.4.0]: https://github.com/xuchaoxin1375/domain-checker/compare/v2.3.0...v2.4.0
[2.3.0]: https://github.com/xuchaoxin1375/domain-checker/releases/tag/v2.3.0
