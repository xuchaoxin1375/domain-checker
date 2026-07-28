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
  "timeout": 15,                  // WHOIS/DNS/HTTP 单阶段超时秒数，范围 1-120
  "max_workers": 5,
  "proxy_enabled": false,
  "proxy_url": "http://127.0.0.1:7897",
  "platform": "whois",            // whois | whoisxml | rdap
  "allow_lan_access": true,
  // 平台元信息。implemented=false 表示未接入专用接口，实际查询自动回落 WHOIS 标准协议，
  // 页面与运行日志均会向用户明示；desc 为各平台效果与信息介绍
  "platforms": {
    "whois":    { "name": "WHOIS标准查询", "icon": "🔍", "implemented": true,  "desc": "..." },
    "whoisxml": { "name": "WHOIS XML",     "icon": "🌐", "implemented": false, "desc": "..." },
    "rdap":     { "name": "RDAP安全查询",  "icon": "🛡️", "implemented": false, "desc": "..." }
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
`timeout` 为 WHOIS、DNS 和 HTTP 探测的单阶段超时秒数，服务端限制为 1-120 秒。

```bash
curl -X POST localhost:5000/api/config \
  -H 'Content-Type: application/json' \
  -d '{"allow_lan_access": false, "rate_limit_delay": 2.0}'
```

```jsonc
// 响应（allow_lan_access 发生变化时会提示需要重启）
{ "message": "配置已更新；局域网访问设置将在重启服务后生效", "config": {...}, "platforms": {...}, "server": {...} }
```

非法 `platform` 值回退为 `whois`。

---

## 查询任务

### POST /api/query

```jsonc
// 请求：domains 为多行文本（支持 URL 混排，自动提取域名并去重）
{ "domains": "example.com\nhttps://www.qq.com/path", "platform": "whois",
  "query_mode": "unlimited", "query_timeout": 15 }

// 响应
{ "task_id": "0728111007366", "total": 2, "platform": "whois",
  "query_mode": "unlimited", "query_timeout": 15, "message": "任务已创建，..." }
```

`query_mode` 可选 `unlimited`（默认，不添加主动等待）、`standard`（按配置限流）或 `quick`
（降低 WHOIS 请求与失败重试等待；不改变注册/解析判定规则）。`query_timeout` 是本任务及随后
重查使用的单阶段超时，范围 1-120 秒；省略时使用全局 `timeout`。

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
      "domain": "example.com", "status": "success",
      "whois_status": "ok", "hold_status": null,
      "registrar": "X Registrar", "registration_date": "2024-01-01",
      "expiration_date": "2027-01-01", "updated_date": null,
      "name_servers": "ns1.x.com", "dnssec": null, "error": null,
      "resolved": true, "block_reason": null, "dns_records": ["1.2.3.4"],
      "query_time": "2026-07-28 12:00:01", "query_duration_seconds": 2.34,
      "raw_response": "Domain Name: X.COM\n..."
  } ],
  "logs": [ {"time":"11:10:07","level":"warn","message":"[x.com] DNS 检查异常..."} ],
  "log_cursor": 8, "refresh": false }
```

`results[].status ∈ {success, failed, timeout, not_registered, invalid}`：
- `success`：WHOIS 成功（同时有 DNS 检查字段）
- `not_registered`：WHOIS 响应文本或明确异常包含确定性的未找到信号（`error` 固定为「域名未被注册」，**不再重试**）
- `timeout`：WHOIS 在 `timeout` 秒内未完成，重试后仍超时（`error` 为「WHOIS 查询超时，无法确认域名状态」）
- `failed`：空响应、解析错误、配额或其他网络错误重试后仍失败（`error` 为最后错误）；空响应不能判定为未注册

`.com/.net` 子域会以可注册主域（如 `www.baidu.com` 查询 `baidu.com`）执行 WHOIS/RDAP 注册状态查询，
DNS 仍检查原输入域名。WHOIS 首次失败后自动尝试 Verisign RDAP；成功时仍返回 `success` 并保留
RDAP 状态，失败时不会猜测注册或封禁结论。前端会根据 `error` 明示配额、网络解析、网络连接、
空响应或解析错误。
- `invalid`：域名格式非法

`results[].resolved ∈ {true,false,null}` 对应 正常解析 / 未解析 / 未知；
`results[].whois_status` 保留注册局返回的原始状态；`hold_status` 为 `clientHold` 或
`serverHold` 时，`resolved=false` 且 `block_reason` 会明确标记为「停止解析（域名被封）」。
`query_time` 为该域名开始查询的本地时间，`query_duration_seconds` 为该域名完整查询流水线耗时。
`raw_response` 保存 WHOIS 原始文本或 RDAP 原始 JSON，供详细结果查看；可能为 `null`。
`refresh=true` 表示发生了重查，前端应丢弃旧全表。`log_after` 传入上次响应的
`log_cursor` 可只获取新增日志，切换日志级别时传 `0` 重新读取。

### POST /api/pause/{task_id} · /api/resume/{task_id} · /api/cancel/{task_id}

暂停/继续立即生效于"尚未启动"的域名；`cancel` 置 `cancelled` 并阻止后续域名启动。

### POST /api/retry/{task_id}

```jsonc
// 请求
{ "domains": ["a.com", "b.com"] }
```

按"同域名覆盖"更新任务结果；若服务重启后任务已不在内存，会先从 SQLite 历史恢复再重查。
重查期间任务恢复为 `processing`，状态接口的 `operation=retry`；当前已有查询或重查在运行时返回 `409`。`domains`
必须是数组，规范化去重后每个域名都必须属于该任务已有结果，否则返回 `400`。

### POST /api/retry-failed/{task_id}

重查该任务下所有 `failed`、`timeout` 或 `invalid` 域名；确定性 `not_registered` 不自动重查；无失败项时返回提示消息。

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

删除历史与明细结果（当前实现不校验任务是否进行/存在）。

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
