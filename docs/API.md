# HTTP API 参考

Base URL：`http://localhost:5000`（或启动日志输出的地址）。所有数据接口返回 JSON（除导出）；
错误统一返回 `{"error": "消息"}` 与 4xx/5xx 状态码。

## 目录

- [配置](#配置)
- [查询任务](#查询任务)
- [历史记录](#历史记录)
- [导出](#导出)

---

## 配置

### GET /api/config

```jsonc
// 响应
{
  "max_domains_per_batch": 500,
  "rate_limit_delay": 1.0,
  "max_retries": 3,
  "retry_delay": 2,
  "timeout": 15,                  // WHOIS/RDAP/DNS/HTTP 单次网络请求超时，范围 1-120
  "max_workers": 5,
  "proxy_enabled": false,
  "proxy_url": "http://127.0.0.1:7897",
  "platform": "rdap",             // rdap（默认）| whois | whoisxml
  "allow_lan_access": true,
  // 平台元信息。implemented=false 表示未接入专用接口，实际查询自动回落 WHOIS 标准协议，
  // 页面与运行日志均会向用户明示；desc 为各平台效果与信息介绍
  "platforms": {
    "whois":    { "name": "WHOIS标准查询", "icon": "🔍", "implemented": true,  "desc": "..." },
    "whoisxml": { "name": "WHOIS XML",     "icon": "🌐", "implemented": false, "desc": "..." },
    "rdap":     { "name": "RDAP优先查询",  "icon": "🛡️", "implemented": true,  "desc": "..." }
  },
  "server": {                     // 服务器实际监听状态
    "bind_host": "0.0.0.0",       // 未启动 run_server 时为 null
    "port": 5000,
    "lan_active": true
  }
}
```

注：敏感字段（如 `proxy_auth`）不下发。

### POST /api/config

Body 为任意配置键子集，类型自动转换；修改后自动写入 `data/settings.json`。
`timeout` 为 WHOIS、RDAP、DNS 和 HTTP 每次网络请求的超时秒数，服务端限制为 1-120 秒。
它不是单域名或整批任务总时限；重试、RDAP 回退和后续探测会累计计入查询耗时。

```bash
curl -X POST localhost:5000/api/config \
  -H 'Content-Type: application/json' \
  -d '{"allow_lan_access": false, "rate_limit_delay": 2.0}'
```

```jsonc
// 响应（allow_lan_access 发生变化时会提示需要重启）
{ "message": "配置已更新；局域网访问设置将在重启服务后生效", "config": {...}, "platforms": {...}, "server": {...} }
```

非法 `platform` 值回退为 `rdap`。

---

## 查询任务

### POST /api/query

```jsonc
// 请求：domains 为多行文本（支持 URL 混排，自动提取域名并去重）
{ "domains": "example.com\nhttps://www.qq.com/path", "platform": "rdap",
  "query_mode": "unlimited", "query_timeout": 15 }

// 响应
{ "task_id": "0728111007366", "total": 2, "platform": "rdap",
  "query_mode": "unlimited", "query_timeout": 15, "message": "任务已创建，..." }
```

`query_mode` 可选 `unlimited`（默认，不添加主动等待）、`standard`（按配置限流）、`quick`
（降低 WHOIS 请求与失败重试等待）或 `brief`（简略快速：最多一次 WHOIS 尝试、一次 DNS 查询，
不做 HTTP 探测和 DNS 自动复查）。简略快速模式仍保持确定性判定规则，但瞬时故障更容易返回未知或失败。
`query_timeout` 是本任务及随后
重查使用的单次网络尝试超时，范围 1-120 秒，传递给 WHOIS、RDAP、DNS 和 HTTP 探测；省略时使用
全局 `timeout`。它不是整批任务的总时限；非简略模式的失败重试和 DNS 自动复查会累加整体耗时。

错误：`400` 无有效域名；`400` 超过 `max_domains_per_batch`。

### GET /api/status/{task_id}

```jsonc
{ "status": "processing|completed|cancelled", "total": 403, "completed": 120,
  "progress": 50.0, "paused": false,
  "query_mode": "quick",
  "operation": "query|retry|completed", "operation_total": 20, "operation_completed": 10,
  "elapsed_seconds": 12.34, "duration_seconds": null }
```

未知任务返回 `404`。

### GET /api/results/{task_id}?log_level=all|info|warn|error&log_after=0

```jsonc
{ "status": "processing", "total": 403, "completed": 120, "paused": false,
  "results": [ {
      "domain": "example.com", "query_state": "completed", "status": "success",
      "whois_status": "ok", "hold_status": null,
      "registrar": "X Registrar", "contact_email": "abuse@example.com",
      "registration_date": "2024-01-01",
      "expiration_date": "2027-01-01", "updated_date": null,
      "name_servers": "ns1.x.com", "dnssec": null, "error": null,
      "resolved": true, "block_reason": null, "dns_records": ["1.2.3.4"],
      "query_time": "2026-07-28 12:00:01", "query_duration_seconds": 2.34,
      "raw_response": "Domain Name: X.COM\n..."
  } ],
  "logs": [ {"time":"11:10:07","level":"warn","message":"[x.com] DNS 检查异常..."} ],
  "log_cursor": 8, "refresh": false }
```

结果完成后 `results[].status ∈ {success, failed, timeout, not_registered, invalid}`；排队或查询中的占位行
为 `null`：
- `success`：RDAP 或 WHOIS 成功（同时有 DNS 检查字段）
- `not_registered`：权威 RDAP 返回 404，或 WHOIS 响应文本/明确异常包含确定性的未找到信号（`error` 固定为「域名未被注册」，**不再重试**）
- `timeout`：RDAP 回退后的 WHOIS 在 `timeout` 秒内未完成，重试后仍超时（`error` 为「WHOIS 查询超时，无法确认域名状态」）
- `failed`：空响应、解析错误、配额或其他网络错误重试后仍失败（`error` 为最后错误）；空响应不能判定为未注册

子域通过离线公共后缀规则转换为可注册主域（如 `www.example.co.uk` 查询 `example.co.uk`）执行
RDAP/WHOIS 注册状态查询，DNS 仍检查原输入域名。默认先通过 IANA Bootstrap 发现并查询权威 RDAP；
TLD 不支持或 RDAP 非确定性失败时自动回退 WHOIS。失败时不会猜测注册或封禁结论，前端会根据
`error` 明示配额、网络解析、网络连接、空响应或解析错误。
- `invalid`：域名格式非法

`results[].query_state ∈ {queued,querying,paused,cancelled,completed}` 是独立的执行阶段：任务创建后立即为全部域名返回
`queued` 占位行，worker 开始处理时改为 `querying`，得到最终结果后改为 `completed`。暂停或终止任务时，
仅尚未完成的行分别改为 `paused` 或 `cancelled`，已经 `completed` 的行保持不变；继续任务时暂停行恢复到
暂停前的 `queued` 或 `querying`。重查时仅被重查的
域名重新进入排队阶段，并立即清空旧的 `query_time` 与 `query_duration_seconds`；worker 开始时实时写入
本次 `query_time`。该字段不替代 `status`：`status` 仍表示最终查询结论。
SQLite 历史只保存最终结果，因此历史详情可视为 `query_state=completed`。

前端“状态”列显示执行阶段及最终查询结果；“注册状态”根据最终 `status` 显示已注册、未注册、无法确认
或不适用；“域名状态”显示 `whois_status` 中的 EPP/WHOIS 原始状态。

`results[].resolved ∈ {true,false,null}` 对应 正常解析 / 未解析 / 未知；
DNS 超时或意外异常会自动复查一次；仍为 `null` 时 `block_reason` 会包含重查建议，
`POST /api/retry-failed/{task_id}` 也会重查 `status=success && resolved=null` 的结果。
`results[].whois_status` 保留注册局返回的原始状态；`hold_status` 为 `clientHold` 或
`serverHold` 时，`resolved=false` 且 `block_reason` 会明确标记为「停止解析（域名被封）」。
`query_time` 为该域名开始查询的本地时间，`query_duration_seconds` 为 WHOIS 重试、RDAP 回退、DNS、
HTTP 探测及等待时间合计的完整流水线耗时，因此可以大于 `query_timeout`。
`raw_response` 保存 WHOIS 原始文本或 RDAP 原始 JSON，供详细结果查看；可能为 `null`。
`contact_email` 为 WHOIS/RDAP 能提供的联系邮箱（RDAP 优先投诉联系人），可能为 `null`。
`refresh=true` 表示发生了重查，前端应丢弃旧全表。`log_after` 传入上次响应的
`log_cursor` 可只获取新增日志，切换日志级别时传 `0` 重新读取。

### POST /api/pause/{task_id} · /api/resume/{task_id} · /api/cancel/{task_id}

暂停会在当前同步网络请求返回后的检查点生效，阻止进入下一阶段和提交结果；未完成行显示为“已暂停”，继续后恢复原执行阶段。
暂停期间 `elapsed_seconds` 保持冻结，暂停区间不计入最终 `duration_seconds` 和单域名查询耗时。
`cancel` 置任务为 `cancelled` 并阻止后续域名启动，未完成行显示为“已终止”；已完成行不变。

### POST /api/retry/{task_id}

```jsonc
// 请求
{ "domains": ["a.com", "b.com"] }
// 响应
{ "message": "正在重新查询 2 个域名", "started": true, "domains": ["a.com", "b.com"] }
```

按"同域名覆盖"更新任务结果；若服务重启后任务已不在内存，会先从 SQLite 历史恢复再重查。
重查期间任务恢复为 `processing`，状态接口的 `operation=retry`；当前已有查询或重查在运行时返回 `409`。`domains`
必须是数组，规范化去重后每个域名都必须属于该任务已有结果，否则返回 `400`。

### POST /api/retry-failed/{task_id}

重查该任务下所有 `failed`、`timeout`、`invalid`，以及 `status=success && resolved=null` 的域名；
确定性 `not_registered` 不自动重查；无异常或解析未知项时返回提示消息。

### GET /api/operations?limit=100

返回最近的服务启动与终止操作日志，按时间倒序排列；`limit` 范围为 1-500。

```jsonc
{ "operations": [
  { "time": "2026-07-29 10:00:00", "action": "启动", "pid": 1234,
    "detail": "服务开始监听 127.0.0.1:5000，debug=False" },
  { "time": "2026-07-29 09:30:00", "action": "终止", "pid": 1200,
    "detail": "服务进程已停止" }
] }
```

---

## 历史记录

### GET /api/history?limit=50

```jsonc
{ "history": [ {
    "task_id": "0728111007366", "domains": "a.com\nb.com", "domain_count": 2,
    "results_count": 2, "success_count": 2, "failed_count": 0,
    "status": "completed", "created_at": "2026-07-28T11:10:07.496868",
    "completed_at": "2026-07-28T11:11:05.634565", "config": "{...}"
} ] }
```

`created_at` 为本地 naive ISO 时间字符串。

### GET /api/history/{task_id}

```jsonc
{ "history": {...},
  "results": [ { "domain": "a.com", "status": "success", "resolved": 0,
                 "whois_status": "ok", "hold_status": null,
                 "query_time": "2026-07-28 12:00:01", "query_duration_seconds": 2.34,
                 "block_reason": "未解析（NXDOMAIN）：..." } ] }
```

注意 `resolved` 在 DB 中为 `1/0/null`。记录不存在返回 `404`。

### DELETE /api/history/{task_id}

删除单条历史与明细结果；任务仍在运行时返回 `409`。

### POST /api/history/delete-batch

在一个事务中批量删除选中的历史与明细。`task_ids` 必须是非空数组，单次最多 500 条；
任一选中任务仍在运行时返回 `409`。

```jsonc
// 请求
{ "task_ids": ["TASK01", "TASK02"] }
// 响应
{ "message": "已删除 2 条历史记录", "deleted": 2 }
```

### POST /api/history/clear-all

清理全部查询历史和结果明细。存在运行中的任务时返回 `409`；成功响应包含实际删除条数。

### POST /api/history/clear

```jsonc
// 请求
{ "days": 30 }
// 响应
{ "message": "已清理 N 条记录" }
```

---

## 导出

### GET /api/export/{task_id}?format=csv|xlsx&filter=all&selected=

| 参数 | 取值 | 说明 |
|------|------|------|
| format | `csv`（默认）/ `xlsx` | CSV 带 BOM，Excel 可直接打开 |
| filter | `all` / `success` / `normal` / `failed` / `timeout` / `not_registered` / `blocked` | normal=成功且解析非失败；timeout=查询超时；not_registered=未注册；blocked=未解析 |
| selected | 逗号分隔域名 | 指定时优先于 filter |

优先取内存任务结果；任务不在内存时自动回退读数据库。无数据返回 `400`。

成功响应为文件流（`Content-Disposition: attachment; filename=domain_report_<时间戳>.<ext>`）。
