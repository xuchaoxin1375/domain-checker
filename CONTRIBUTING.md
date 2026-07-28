# 参与贡献

感谢你愿意改进这个项目！本文档覆盖环境搭建、代码规范、测试与提交流程。
如果你是 coding agent，请优先阅读 [AGENTS.md](AGENTS.md)，再通过
[spec/README.md](spec/README.md) 按任务加载专题。完整开发流程见
[spec/development-workflow.md](spec/development-workflow.md)。

## 环境搭建

```bash
git clone https://github.com/xuchaoxin1375/domain-checker.git
cd domain-checker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

提交前自检（CI 会做同样的事）：

```bash
ruff check .
pytest
```

## 工作流

1. 从 `main` 切分支：`git checkout -b feat/xxx` 或 `fix/xxx`
2. 小步提交，提交信息建议遵循 Conventional Commits：`feat: ...` / `fix: ...` /
   `docs: ...` / `refactor: ...` / `test: ...` / `chore: ...`（中英文均可，正文用中文）
3. 推送并开 Pull Request，描述"做了什么、为什么、如何验证"
4. CI 通过后等待 review

## 代码规范

- Python：PEP 8，行宽 120（`pyproject.toml` 已配置 ruff）；类型标注欢迎但不强制
- 文案/注释/docstring：**中文**；日志格式保持 `域名批量查询系统` 现有风格
- 接口：保持向后兼容；新增字段优于改名字段；错误返回 `{"error": ...}` + 合适状态码
- 前端：`templates/index.html` 原生 JS，无框架无构建；Tab/组件间通过 DOM id 交互
- 并发：访问 `task_storage`/`CONFIG` 持对应锁，锁内不做数据库 IO
- 测试：离线可跑；网络调用必须 mock；新功能附测试，bug 修复附回归用例更好

## 本项目特有的注意事项

- `data/domain_checker.db` 是入库的真实数据文件，**不要**在提交中改动或删除它
- `data/settings.json` 为运行时产物，不要提交
- 面向用户的"解析状态"文案（正常解析/未解析/未知及原因）在前端、日志、导出中三处保持一致，
  改动时全局搜索同步；相关历史数据不一致属预期
- `allow_lan_access` 等配置改动只影响新进程，测试里注意"重启生效"语义

## 提交 PR 前的 checklist

- [ ] `ruff check .` 与 `pytest` 全绿
- [ ] 文档同步（README / docs/API.md / docs/ARCHITECTURE.md，视改动面）
- [ ] `CHANGELOG.md` 的 `[Unreleased]` 区新增条目
- [ ] 未误提交 `data/settings.json`、导出报表、虚拟环境

## 发布（维护者）

1. 更新 `domain_checker/__init__.py` 的 `__version__`、`templates/index.html` 标题版本号
2. 整理 `CHANGELOG.md`，把 `[Unreleased]` 固化为新版本号与日期
3. 合并后打 tag：`git tag -a v2.5.0 -m "v2.5.0" && git push origin v2.5.0`

## 反馈

问题与建议请开 [Issue](https://github.com/xuchaoxin1375/domain-checker/issues)；
附上报错日志、复现步骤与环境信息可加速处理。
