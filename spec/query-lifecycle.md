# 查询生命周期规格

## 状态与结论

- `success`：WHOIS 有明确注册证据。无 hold 状态时才继续 DNS 检查。
- `not_registered`：只有明确的未找到响应或专属异常才能得出；这是确定性结论，不重试。
- `timeout`：网络阶段达到配置超时且重试后仍失败。查询失败、空响应不能归入未注册。
- `failed`：配额、空响应、解析错误或其他非确定性失败。
- `invalid`：输入无法解析为合法域名。
- `clientHold` 或 `serverHold`：任务状态仍为 `success`，但 `resolved=false`，展示“停止解析（域名被封）”，保存 `hold_status` 和原始 `whois_status`，不再查询 DNS。
- `.com/.net` 子域使用可注册主域执行 WHOIS/RDAP（例如 `www.baidu.com → baidu.com`），DNS 仍检查原输入；只有正确注册对象的明确未找到响应才可判定未注册。
- WHOIS 首次失败时立即尝试 Verisign RDAP；只有 RDAP 明确返回 hold 才显示被封，双重失败仍显示具体失败或超时。原始 WHOIS 文本或 RDAP JSON 保存到 `raw_response`。

状态文案变更必须同步 checker、前端、导出、README 和相关测试。

## 批量与重查不变量

- 输入经过 `parse_domain_input()` 规范化、按首次出现顺序去重。
- `/api/retry/{task_id}` 只接受数组，且域名必须属于该任务已有结果。
- 任务因服务重启或热重载不在内存时，重查接口从 SQLite 历史恢复结果后继续；数据库读取不得在 `task_lock` 内执行。
- 重查进入 `processing`，`operation=retry`；同域名结果原位覆盖，不重复追加。
- 同一任务不能同时运行查询和重查，重叠请求返回 `409`。
- 每个 worker 无论成功或意外异常都必须推进 `operation_completed`。意外异常写成 `failed` 结果，不能让任务永久停在 `processing`。
- 完成状态写入后，前端再读取最终结果，避免最后一条结果竞态丢失。
- 每条结果保存 `query_time` 和 `query_duration_seconds`；任务保存整体耗时。

## 并发和持久化

- 并发由 `ThreadPoolExecutor(max_workers)` 控制；默认不限流模式不添加主动等待，标准/快速模式只调整限流和重试等待，不改变判断语义。`query_timeout` 可按任务覆盖全局超时。
- 访问 `task_storage` 持 `task_lock`，访问 `CONFIG` 持 `config_lock`，锁内不执行数据库 IO。
- 完成后在锁外保存结果和历史计数。测试必须使用临时数据目录且 mock 所有 WHOIS、DNS、HTTP 网络调用。

实现细节和状态图见 [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)。
