# 开发工作流规格

## 修改前

1. 阅读 `AGENTS.md`、`spec/README.md` 和当前任务对应专题。
2. 运行 `git status --short`，把已有改动视为用户工作，不回退、不覆盖。
3. 从结构地图定位边界；涉及数据流时再读 `docs/ARCHITECTURE.md`，涉及 HTTP 时再读 `docs/API.md`。

## 实现规则

- 保持依赖方向 `web -> tasks -> checker -> settings/state`，路由只做参数与响应编排。
- 配置项必须同时补默认值、API 类型转换、持久化行为、README 配置表和测试。
- API 字段改动同步前端与 `docs/API.md`；查询流程改动同步 `docs/ARCHITECTURE.md`。
- 用户状态文案同步 checker、前端、导出、README；数据库初始化保持幂等。
- 不读取、重建或删除真实 `data/domain_checker.db`。测试依赖 `tests/conftest.py` 的临时目录隔离。
- 网络测试全部 mock；bug 修复应添加能在旧行为下失败的回归测试。

## 验证矩阵

| 改动 | 最低验证 |
|------|----------|
| Python 逻辑 | 相关 pytest + `ruff check .` |
| API/任务状态 | `tests/test_api.py` / `tests/test_tasks.py` + 全量 pytest |
| WHOIS/DNS | `tests/test_checker.py`，确认无真实网络 |
| 前端 | `tests/test_frontend.py` + JavaScript 语法检查 |
| 数据库/导出 | 对应测试，确认使用临时数据目录 |
| 文档 | 链接、命令、状态/API 文案与代码一致 |

交付前运行：

```powershell
pytest -q
ruff check .
git diff --check
```

最后更新 `CHANGELOG.md` 的 `[Unreleased]`，并按 [`agent-handoff.md`](agent-handoff.md) 汇总交接信息。

