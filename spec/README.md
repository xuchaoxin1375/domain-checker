# Coding Agent 规格入口

本目录采用渐进式披露：先用最少上下文判断任务类型，再只读取相关专题。不要把所有文档一次性载入。

## 阅读层级

| 层级 | 文档 | 何时读取 |
|------|------|----------|
| L0 | [`AGENTS.md`](../AGENTS.md) | 每次接手先读，了解命令、目录和硬约束 |
| L1 | 本页 | 根据任务选择专题规格 |
| L2 | 下列 `spec/*.md` | 只读与当前修改有关的专题 |
| L3 | [`docs/API.md`](../docs/API.md)、[`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) | 需要完整接口或实现背景时再深入 |

## 按任务选读

| 要修改的内容 | 必读规格 | 进一步参考 |
|--------------|----------|------------|
| WHOIS、DNS、状态文案、重查、暂停、并发 | [`query-lifecycle.md`](query-lifecycle.md) | `docs/ARCHITECTURE.md` |
| 页面、筛选、侧栏、主题、移动端 | [`frontend-contracts.md`](frontend-contracts.md) | `templates/index.html`、`tests/test_frontend.py` |
| 配置、API、数据库、导出 | [`development-workflow.md`](development-workflow.md) | `docs/API.md`、`docs/ARCHITECTURE.md` |
| 开始或结束一轮 Agent 工作 | [`agent-handoff.md`](agent-handoff.md) | `git status --short`、`CHANGELOG.md` |

若一个改动跨越多个边界，读取对应的多个 L2 专题。专题规格描述必须保持的行为，`docs/` 解释完整实现。

