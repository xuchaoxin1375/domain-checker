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
  "timeout": 15,
  "max_workers": 5,
  "proxy_enabled": false,
  "proxy_url": "http://127.0.0.1:7897",
  "platform": "whois",            // whois | whoisxml | rdap
  "allow_lan_access": true,
  "platforms": { /* 平台元信息 */ },
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
{ "domains": "example.com\nhttps://www.qq.com/path", "platform": "whois" }

// 响应
{ "task_id": "0728111007366", "total": 2, "platform": "whois", "message": "任务已创建，..." }
```

错误：`400` 无有效域名；`400` 超过 `max_domains_per_batch`。

### GET /api/status/{task_id}

```jsonc
{ "status": "processing|completed|cancelled", "total": 403, "completed": 120,
  "progress": 29.8, "paused": false }
```

未知任务返回 `404`。

### GET /api/results/{task_id}?log_level=all|info|warn|error

```jsonc
{ "status": "processing", "total": 403, "completed": 120, "paused": false,
  "results": [ {
      "domain": "example.com", "status": "success",
      "registrar": "X Registrar", "registration_date": "2024-01-01",
      "expiration_date": "2027-01-01", "updated_date": null,
      "name_servers": "ns1.x.com", "dnssec": null, "error": null,
      "resolved": true, "block_reason": null, "dns_records": ["1.2.3.4"]
  } ],
  "logs": [ {"time":"11:10:07","level":"warn","message":"✗ x.com [未解析: ...]"} ],
  "refresh": false }
```

`results[].resolved ∈ {true,false,null}` 对应 正常解析 / 未解析 / 未知；
`refresh=true` 表示发生了重查，前端应丢弃旧全表。日志最多返回最近 100 条。

### POST /api/pause/{task_id} · /api/resume/{task_id} · /api/cancel/{task_id}

暂停/继续立即生效于"尚未启动"的域名；`cancel` 置 `cancelled` 并阻止后续域名启动。

### POST /api/retry/{task_id}

```jsonc
// 请求
{ "domains": ["a.com", "b.com"] }
```

按"同域名覆盖"更新内存任务结果，仅支持**内存中**的任务（服务重启后 404）。

### POST /api/retry-failed/{task_id}

重查该任务下所有 `status != success` 的域名；无失败项时返回提示消息。

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
| filter | `all` / `success` / `normal` / `failed` / `blocked` | normal=成功且解析非失败；blocked=未解析 |
| selected | 逗号分隔域名 | 指定时优先于 filter |

优先取内存任务结果；任务不在内存时自动回退读数据库。无数据返回 `400`。

成功响应为文件流（`Content-Disposition: attachment; filename=domain_report_<时间戳>.<ext>`）。
