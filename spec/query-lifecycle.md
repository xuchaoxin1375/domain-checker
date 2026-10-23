# 查询生命周期规格

## 状态与结论

- `success`：RDAP 或 WHOIS 有明确注册证据。无 hold 状态时才继续 DNS 检查。
- `not_registered`：只有明确的未找到响应或专属异常才能得出；这是确定性结论，不重试。
- `timeout`：网络阶段达到配置超时且重试后仍失败。查询失败、空响应不能归入未注册。
- `failed`：配额、空响应、解析错误或其他非确定性失败。
- `invalid`：输入无法解析为合法域名。
- `clientHold` 或 `serverHold`：任务状态仍为 `success`，但 `resolved=false`，展示“停止解析（域名被封）”，保存 `hold_status` 和原始 `whois_status`，不再查询 DNS。
- 子域通过离线公共后缀规则归一化为可注册主域执行 RDAP/WHOIS（例如 `www.example.co.uk → example.co.uk`），DNS 仍检查原输入；只有权威注册对象的明确未找到响应才可判定未注册。
- 默认通过 IANA Bootstrap 发现权威 RDAP 服务并优先查询；TLD 未发布 RDAP、RDAP 超时或非确定性失败时回退 WHOIS。显式 WHOIS 模式仍在首次失败后尝试 RDAP 容错。原始 WHOIS 文本或 RDAP JSON 保存到 `raw_response`。

状态文案变更必须同步 checker、前端、导出、README 和相关测试。

## 批量与重查不变量

- 输入经过 `parse_domain_input()` 规范化、按首次出现顺序去重。
- `/api/retry/{task_id}` 只接受数组，且域名必须属于该任务已有结果。
- 任务因服务重启或热重载不在内存时，重查接口从 SQLite 历史恢复结果后继续；数据库读取不得在 `task_lock` 内执行。
- 重查进入 `processing`，`operation=retry`；同域名结果原位覆盖，不重复追加。
- 重查受理时立即把目标行恢复为 `queued`，清空旧的 `query_time` 与 `query_duration_seconds`；worker
  切换为 `querying` 时立即写入本次开始时间，最终结果再写入本次完整耗时。
- 同一任务不能同时运行查询和重查，重叠请求返回 `409`。
- 每个 worker 无论成功或意外异常都必须推进 `operation_completed`。意外异常写成 `failed` 结果，不能让任务永久停在 `processing`。
- 完成状态写入后，前端再读取最终结果，避免最后一条结果竞态丢失。
- 每条结果保存 `query_time` 和 `query_duration_seconds`；任务保存整体耗时。复用结果为 `reused=true`，
  这两个字段保留原始查询值，不伪造为本次任务开始时间。
- 初次查询创建全部域名占位行，`query_state` 通常按 `queued -> querying -> completed` 更新；暂停时仅未完成行
  临时进入 `paused`，恢复后回到原来的 `queued/querying`，终止时仅未完成行进入 `cancelled`。已经完成的行不变；
  最终 `status` 继续表示成功、未注册、超时、失败或无效，不与执行阶段混用。
- DNS 超时或意外异常自动复查一次；仍为未知时明确建议重查，并纳入 `retry-failed` 的域名集合。
- 暂停为协作式暂停：worker 启动、注册数据请求返回后、WHOIS/DNS 重试前、HTTP 探测前和结果提交前均检查暂停标志。
  已发出的同步网络请求不能强制中断，会在当前请求返回后停住；任务启动线程不得覆盖用户刚设置的暂停标志。
- 暂停期间前端只轮询 `/api/status`，不拉取 `/api/results`；恢复后再按 `log_after` 补齐日志。因此暂停后
  最多可能看到在途请求产生的一次结果，但日志区不会持续刷新。
- 前端把当前任务 ID 与查询参数保存在 `localStorage`；页面刷新后重新拉取状态和结果恢复进度，输入区
  为空时用结果域名回填。服务重启导致任务丢失时，启动时把遗留 `processing` 历史标记为 `interrupted`，
  历史页提供“回填域名”。
- 暂停区间不计入状态栏实时耗时、任务完成总耗时和单域名 `query_duration_seconds`；恢复后从暂停前数值继续。

## 执着模式

- 查询区「执着模式」是任务级开关（`persistent`），可与任意查询模式组合；重查接口可带 `persistent`
  覆盖任务设置，省略时沿用任务创建值。
- 首轮结束后，只有 `failed`、`timeout` 和 `status=success && resolved=null`（解析未知）的域名会进入
  下一轮；`not_registered`、`invalid` 和已有明确解析结论的成功结果不重试，避免无意义循环。
- 第 `n` 轮的单次请求超时按 `基础超时 × 2^(n-1)` 递增，并由 `normalize_timeout` 封顶 120 秒；
  该超时同时作用于注册查询、DNS 检查和 HTTP 探测。
- 轮数上限由 `persistent_max_rounds`（默认 5，含首轮，1-20）控制；达到上限仍有未结论域名时记录
  `warn` 日志并保留最后一轮结果，任务仍正常完成。
- 追加轮次不推进 `operation_completed`（进度条保持在首轮完成度），并按轮次回退/恢复 `completed`
  计数，保证完成数不超过总数；状态接口暴露 `persistent_round`、`persistent_max_rounds` 和
  `persistent_retrying`，前端在进度区显示当前轮次与剩余重试数。
- 每轮开始前检查取消状态；worker 启动与网络请求之间沿用协作式暂停，暂停不会跳过后续轮次。
- 重试轮次不使用最近成功结果复用（等价于强制联网）；逐条落库与取消收尾逻辑不变。

## 并发和持久化

- 并发由固定数量的守护工作线程 + 共享队列控制，配置入口为 1-32 的每任务线程池上限；任务启动时读取
  一次，运行中修改只影响后续任务。多个任务并行会各自占用线程，全局并发约为任务数 × `max_workers`。
- 默认不限流模式不添加主动等待，标准/快速模式只调整限流和重试等待；`rate_limit_delay` 同时作用于
  RDAP（默认平台）与 WHOIS。简略快速模式最多一次 WHOIS 尝试和一次 DNS 查询，并跳过 HTTP 探测与
  DNS 自动复查；它只减少网络步骤，不放宽确定性判断语义。`query_timeout` 可按任务覆盖全局超时。
- 新任务按 `reuse_recent_seconds`（默认 3600 秒，0 关闭）复用窗口内 `status=success` 且 DNS 有结论的
  最新结果，命中项标记 `reused=true` 并跳过网络查询；新鲜度按原始 `query_time` 计算，复用标记随结果
  写入历史。查询请求可带 `ignore_cache=true` 跳过复用；重查始终强制联网。
- 每个域名完成后立即落库，任务取消或进程中断不会丢失已完成结果；取消任务的历史标记为
  `cancelled`，其成功行仍参与后续复用。取消产生的失败结果不得覆盖同域名已保存的成功行。
- 数值配置在 API 与 `settings.json` 加载时统一归一化：超范围收敛到边界，非法值忽略或返回 `400`，
  不能让任务线程因配置异常退出。
- 访问 `task_storage` 持 `task_lock`，访问 `CONFIG` 持 `config_lock`，锁内不执行数据库 IO。
- 完成后在锁外保存结果和历史计数。测试必须使用临时数据目录且 mock 所有 WHOIS、DNS、HTTP 网络调用。

实现细节和状态图见 [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)。
