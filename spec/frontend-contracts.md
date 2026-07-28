# 前端契约

## 基础约束

- 前端为单文件 `templates/index.html`，原生 HTML/CSS/JS，无构建步骤。
- 中文界面；API 字段与后端保持兼容。Tab 通过 `data-tab` 定位，不依赖浏览器全局 `event`。
- 域名默认点击打开 `https://域名`；复制、重查、详细结果是独立按钮。
- 状态筛选与解析筛选互相独立；`not_registered` 是状态值，在解析筛选中为“不适用”。
- 查询进度属于结果板块；处理中同时提供滚动不丢失的固定状态提示，结束后显示最终耗时。
- 失败状态必须在表格状态列直接显示可判读类别（超时、配额、网络解析、网络连接、空响应或解析错误），
  备注列和详细结果继续保留完整原始错误。
- 详细结果展示后端返回的 `raw_response`，即 WHOIS 原始文本或 RDAP JSON；历史详情同样保留。
- 运行日志和查询结果仅在对应区块可见时出现在侧栏大纲。

## 侧栏和移动端

- 桌面侧栏支持左/右停靠与紧凑模式，偏好保存在 `localStorage`，颜色使用主题变量。
- `max-width: 1250px` 时显示汉堡按钮，侧栏变为从已保存方向滑入的抽屉，不继承桌面紧凑宽度。
- 窄屏页眉是统一的 sticky 工具栏；汉堡按钮必须位于页眉布局内，并随侧栏停靠方向排列到左端或右端，
  不得作为独立 fixed 元素覆盖页面。
- 页眉只保留产品名称、版本和汉堡按钮；当前查询平台在配置区表达，不在页眉重复展示。
- 抽屉打开时显示遮罩并锁定页面滚动；点击遮罩、菜单项或按 Escape 关闭。
- 汉堡按钮维护 `aria-controls`、`aria-expanded` 和可读标签。
- `max-width: 700px` 时配置单列、按钮可换行、统计双列、历史记录纵向排列；表格保持横向滚动。
- 移动端“查询 / 历史记录”两个主 Tab 等宽排列并居中，不把剩余空间集中留在一侧。

## 验证

默认不截图。前端修改至少运行：

```powershell
pytest tests/test_frontend.py -q
node -e "const fs=require('fs');const h=fs.readFileSync('templates/index.html','utf8');new Function(h.slice(h.indexOf('<script>')+8,h.lastIndexOf('</script>')));"
```

按风险补充 API/流程测试和无截图 DOM 检查。只有用户明确要求时才做截图检查。
