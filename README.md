# 域名批量查询系统

[![CI](https://github.com/xuchaoxin1375/domain-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/xuchaoxin1375/domain-checker/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

批量查询域名 WHOIS 信息并检查 DNS 解析状态的 Web 工具：支持暂停/继续、单条与批量重查、
历史记录、CSV/Excel 导出、多主题界面，以及局域网访问开关。纯原生 JS 前端，无构建步骤。

![version](https://img.shields.io/badge/version-2.6.0-blue.svg)

## 快速开始

```bash
git clone https://github.com/xuchaoxin1375/domain-checker.git
cd domain-checker

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python app.py
# 控制台会打印当前访问模式与地址，默认 http://localhost:5000
```

打开浏览器访问输出的地址即可使用。

## 功能特性

| 分组 | 功能 |
|------|------|
| 查询 | WHOIS 批量查询、默认不限流及标准/快速模式、任务级超时、受控并发、自动重试、暂停/继续/取消、单条重查、批量重查失败项 |
| 未注册识别 | 只有 WHOIS 明确返回未找到文本时才判为「未注册」；空响应、网络异常不会伪装成未注册，表格另列「超时」 |
| 解析检查 | 域名注册成功后自动检查 A 记录；识别 `clientHold/serverHold` 并准确标为**停止解析（域名被封）** |
| 平台透明 | 各平台效果与信息在配置面板内介绍；未接入的平台（WHOIS XML/RDAP）页面与运行日志均明示自动回落 WHOIS |
| 结果 | 查询/重查加载状态与耗时、单域名查询时间/耗时、详细结果及 WHOIS/RDAP 原始响应、详细分阶段日志（可按级别过滤）、状态与解析独立筛选、表格排序、点击域名打开网站、复制域名、显眼的重新查询按钮、CSV/Excel 导出 |
| 历史 | SQLite 持久化（跨重启），列表/详情/删除/定期清理，历史结果可直接回填表格或导出 |
| 配置 | 网页可视化配置、WHOIS/DNS/HTTP 超时秒数（1-120）、配置自动持久化到 `data/settings.json`、局域网访问开关（重启生效） |
| 界面 | 带行号的域名输入与演示预设、四主题、自适应布局、可左右停靠的 **VS Code 风格紧凑侧边栏** |

更多界面与使用细节见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 与界面内提示。

## 使用方法

### 网页端

1. 在文本框粘贴域名（每行一个），支持直接粘贴 URL（自动提取域名部分）
2. 点击「开始查询」，可随时暂停/继续/取消
3. 点击结果表中的域名可单条重查；勾选后可批量重查/导出
4. 「历史记录」Tab 可查看、回填、导出、删除历史任务

### 命令行

```bash
python cli.py domains.txt          # 从文件读取
cat domains.txt | python cli.py    # 管道输入
python cli.py --single example.com # 单个查询
python cli.py --help               # 用法说明
```

## 配置

三种方式，后者覆盖前者：

| 方式 | 位置 | 说明 |
|------|------|------|
| 代码默认值 | `domain_checker/settings.py` 的 `CONFIG` | 内置默认 |
| 持久化文件 | `data/settings.json` | 网页端「保存配置」时自动写入，重启自动加载，已加入 `.gitignore` |
| 环境变量 | `DOMAIN_CHECKER_*` | 部署级覆盖（数据目录、监听地址、端口、debug 开关），见下表 |

环境变量一览：

| 变量 | 默认 | 说明 |
|------|------|------|
| `DOMAIN_CHECKER_DATA_DIR` | `<仓库>/data` | 数据目录（放数据库与 settings.json） |
| `DOMAIN_CHECKER_DB` | `$DATA_DIR/domain_checker.db` | 数据库文件路径 |
| `DOMAIN_CHECKER_HOST` | 按 `allow_lan_access` 推导 | 监听地址，显式设置时优先于开关 |
| `DOMAIN_CHECKER_PORT` | `5000` | 监听端口 |
| `DOMAIN_CHECKER_DEBUG` | `1` | Flask debug；置 `0` 关闭（生产建议） |

### 局域网访问控制

- **开启**（默认）：监听 `0.0.0.0`，同局域网设备可通过 `http://本机IP:5000` 访问
- **关闭**：仅监听 `127.0.0.1`，仅本机可访问

修改后写入 `data/settings.json`，**重启服务生效**；启动日志会打印当前模式与可访问地址，
网页配置面板也会显示服务器当前实际监听状态。

### 查询平台说明

| 平台 | 状态 | 实际效果 |
|------|------|----------|
| 🔍 WHOIS标准查询 | ✅ 已接入 | 直连注册局 43 端口，信息全面：注册商、注册/过期/更新日期、DNS服务器、DNSSEC |
| 🌐 WHOIS XML | 🚧 接入中 | 第三方 API（whoisxmlapi.com，需密钥）。**暂未接入，会自动回落 WHOIS 标准协议**，页面与运行日志都会明示 |
| 🛡️ RDAP安全查询 | 🚧 接入中 | HTTPS + 结构化 JSON（RFC 7480）。**暂未接入，同样回落 WHOIS 标准协议** |

接入进展见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)「扩展指南」。

### 解析状态说明

WHOIS 查询结果分两类：

| 状态 | 含义 |
|------|------|
| 未注册 | WHOIS 响应文本或明确异常返回未找到信号，表格显示「未注册」徽标；仅凭空响应不能下结论 |
| 超时 | WHOIS 在超时设置内未完成，状态不确定，表格显示「超时」并保留错误原因；可在查询配置中调整 1-120 秒 |
| 成功/失败 | 成功进入 DNS 解析检查；空响应、解析错误或其他网络错误显示具体失败原因 |

域名已注册时的 DNS 解析状态：

| 解析状态 | 含义 |
|----------|------|
| 正常 | A 记录存在 |
| 停止解析（域名被封） | WHOIS 状态含 `clientHold` 或 `serverHold`，注册商/注册局已暂停域名解析；此时不再重复发起 DNS 查询 |
| 未解析 | 域名已注册但当前无解析：NXDOMAIN（通常已被停止解析/冻结）、无 A 记录、或权威 DNS 异常 |
| 未知 | DNS 查询超时等，状态不确定 |

## HTTP API

详细请求/响应示例见 [docs/API.md](docs/API.md)。概览：

```
GET  /api/config              配置查询（含服务器实际监听状态）
POST /api/config              保存配置（自动持久化）
POST /api/query               提交查询 {domains, platform}
GET  /api/status/<task_id>    任务进度
GET  /api/results/<task_id>   结果与日志（支持按级别过滤）
POST /api/pause|resume|cancel/<task_id>
POST /api/retry/<task_id>     重查指定域名 {domains:[...]}
POST /api/retry-failed/<task_id>
GET  /api/export/<task_id>?format=&filter=&selected=
GET  /api/history             历史列表
GET  /api/history/<task_id>   历史详情
DELETE /api/history/<task_id>
POST /api/history/clear       清理 N 天前记录 {days:30}
```

## 仓库结构

```
domain-checker/
├── app.py                    # Web 启动入口（python app.py），业务代码都在包里
├── cli.py                    # 命令行入口
├── domain_checker/           # ★ 核心包
│   ├── __init__.py           # 版本号
│   ├── settings.py           # 路径/全局配置/配置持久化/环境变量
│   ├── state.py              # 内存任务状态（线程共享）
│   ├── domains.py            # 输入解析与域名校验
│   ├── checker.py            # WHOIS 查询 + DNS 解析检查 + 单域名流水线
│   ├── db.py                 # SQLite 历史记录读写
│   ├── tasks.py              # 批量任务异步编排
│   ├── export.py             # CSV / XLSX 导出
│   └── web.py                # Flask 应用工厂、全部 HTTP API、启动函数
├── templates/index.html      # 单页前端（原生 JS，无构建）
├── tests/                    # pytest 测试（网络调用全部 mock）
├── spec/                     # Coding Agent 渐进式规格与交接规范
├── docs/
│   ├── ARCHITECTURE.md       # 架构、数据流、并发模型、扩展指南
│   └── API.md                # HTTP API 详细说明
├── AGENTS.md                 # 给 coding agent 的接手指南
├── CONTRIBUTING.md           # 开发参与指南
├── CHANGELOG.md              # 版本历史
├── LICENSE                   # MIT
├── pyproject.toml            # 打包元数据 + ruff/pytest 配置
├── requirements.txt          # 运行依赖
├── requirements-dev.txt      # 开发依赖（含 pytest/ruff）
├── .github/workflows/ci.yml  # CI：lint + 测试（Python 3.10-3.12）
└── data/                     # 运行时数据（数据库、settings.json）
```

模块间的依赖方向（不含循环）：`web → tasks → checker → settings/state`，`db/export/domains` 被
上层按需引用。

## 开发

```bash
pip install -r requirements-dev.txt

pytest            # 全部测试，离线运行（网络已 mock）
ruff check .      # 代码检查，CI 同步执行
```

参与贡献请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)；代码助手先读 [AGENTS.md](AGENTS.md)，
再由 [spec/README.md](spec/README.md) 按任务进入专题规格。

## 已知限制（欢迎 PR）

- 批量任务使用 `ThreadPoolExecutor`，并发数由网页配置中的 `max_workers` 控制
- 进行中任务仅存于内存，服务重启后任务中断（历史结果不受影响）
- `whoisxml`/`rdap` 平台选择目前仍是界面标识；WHOIS 失败时，`.com/.net` 会自动回退 Verisign RDAP
  获取注册与 hold 状态（扩展点见 ARCHITECTURE）
- Flask 内置开发服务器，仅适合内网/自用小规模使用

## License

[MIT](LICENSE) © domain-checker contributors
