"""前端模板关键交互的契约测试。"""


def test_result_domain_has_copy_button(client):
    html = client.get('/').get_data(as_text=True)

    assert 'class="copy-domain-btn"' in html
    assert 'onclick="copyDomain(this,' in html
    assert 'navigator.clipboard.writeText(domain)' in html
    assert "showToast(`已复制域名: ${domain}`)" in html
    assert 'class="retry-domain-btn"' in html
    assert 'onclick="retrySingle(' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html
    assert 'function copyTableCell(event, value)' in html
    assert "event.target.closest('a, button, input, select')" in html


def test_results_support_contact_email_unknown_retry_and_column_resize(client):
    html = client.get('/').get_data(as_text=True)

    assert 'data-sort="contact_email"' in html
    assert 'data-sort="whois_status"' in html
    assert 'data-sort="query_state"' in html
    assert '>域名状态<span class="sort-icon">' in html
    assert '>注册状态<span class="sort-icon">' in html
    for state in ['排队中', '正在查询', '已暂停', '已终止', '查询成功', '已注册', '未注册']:
        assert state in html
    assert 'function resultRegistrationText(result)' in html
    assert "['联系邮箱', result.contact_email]" in html
    assert '未知（建议重查）' in html
    assert '重新查询异常/未知' in html
    assert "r.status === 'success' && r.resolved === null" in html
    assert 'function initResizableColumns()' in html
    assert 'domainCheckerColumnWidths' in html
    registration_column = html.index('data-column="status" data-sort="status"')
    resolution_column = html.index('data-column="resolved" data-sort="resolved"')
    domain_status_column = html.index('data-column="whois_status" data-sort="whois_status"')
    assert registration_column < resolution_column < domain_status_column
    assert 'function initDraggableColumns()' in html
    assert 'function applyColumnOrder(order)' in html
    assert 'domainCheckerColumnOrder' in html
    assert "header.draggable = true" in html
    assert '.badge-querying::before {' in html
    assert 'animation: query-spin 0.75s linear infinite;' in html
    assert "markUnfinishedQueryState('cancelled')" in html


def test_result_display_mode_switches_between_live_and_progressive(client):
    html = client.get('/').get_data(as_text=True)

    assert 'data-result-display-mode="live"' in html
    assert 'data-result-display-mode="progressive"' in html
    assert '>状态刷新</button>' in html
    assert '>逐条加入</button>' in html
    assert 'function setResultDisplayMode(mode)' in html
    assert "currentResultDisplayMode = mode === 'progressive' ? 'progressive' : 'live'" in html
    assert 'domainCheckerResultDisplayMode' in html
    assert "result.status !== null && result.status !== undefined" in html
    assert "setResultDisplayMode(localStorage.getItem('domainCheckerResultDisplayMode') || 'live')" in html


def test_query_progress_has_loading_duration_and_incremental_logs(client):
    html = client.get('/').get_data(as_text=True)

    assert 'id="queryActivity"' in html
    assert 'id="queryElapsed"' in html
    assert 'function formatDuration(seconds)' in html
    assert 'log_after=${lastLogCursor}' in html
    assert '已请求暂停；在途网络请求返回后生效' in html


def test_runtime_logs_are_a_dialog_opened_from_query_float(client):
    html = client.get('/').get_data(as_text=True)

    assert 'class="log-number"' in html
    assert 'function copyRunLogs()' in html
    assert '运行日志已复制' in html
    # 日志默认不占版面：卡片移除，改为浮层按钮打开的弹窗
    assert 'id="logCard"' not in html
    assert 'id="btnOpenRunLogs" onclick="openLogDialog()"' in html
    assert 'id="logModal"' in html
    assert 'id="logContainer"' in html
    assert 'function openLogDialog()' in html
    assert 'function closeLogDialog()' in html
    assert 'id="logLevelFilter"' in html
    assert 'id="logFullscreenButton"' not in html
    assert 'data-section="resultsCard"' in html
    assert '.query-activity.processing {' in html
    assert 'const statusResp = await fetch(`/api/status/${currentTaskId}`)' in html
    assert '状态完成后再读取结果' in html
    results_start = html.index('<div class="card hidden" id="resultsCard">')
    assert results_start < html.index('id="queryTaskControls"')
    assert "document.getElementById('queryTaskControls').style.display = busy ? 'flex' : 'none'" in html
    assert '确定终止当前查询' in html


def test_results_show_query_time_duration_and_domain_hold_status(client):
    html = client.get('/').get_data(as_text=True)

    assert 'data-sort="query_time"' in html
    assert 'data-sort="query_duration_seconds"' in html
    assert '查询时间' in html
    assert '查询耗时' in html
    assert '停止解析（域名被封）' in html
    assert 'whois_status' in html
    assert 'formatResultDuration' in html
    assert 'function markDomainsQueuedForRetry(domains)' in html
    assert 'result.query_duration_seconds = null;' in html
    assert 'result.query_time = null;' in html
    assert 'id="statTimeout"' in html
    assert 'badge-timeout' in html


def test_results_show_query_source_column(client):
    html = client.get('/').get_data(as_text=True)

    assert 'data-column="query_source" style="width: 100px"' in html
    assert 'data-sort="query_source"' in html
    assert '查询来源' in html
    assert 'function resultSourceLabel(result)' in html
    assert "return result.reused ? '本地缓存' : '在线查询';" in html
    assert "if (column === 'query_source')" in html
    assert "defaultColumnAnchors = { query_source: 'query_duration_seconds' }" in html
    assert "data-column=\"query_source\">${sourceBadge}" in html
    assert 'badge-cache' in html and 'badge-online' in html
    assert '本地缓存' in html and '在线查询' in html
    assert 'resultSourceLabel(r),' in html


def test_result_filter_and_progress_are_in_results_card(client):
    html = client.get('/').get_data(as_text=True)

    assert '<option value="not_registered">未注册</option>' in html
    results_start = html.index('<div class="card hidden" id="resultsCard">')
    progress_start = html.index('<div class="progress-section" id="progressSection">')
    assert results_start < progress_start
    assert 'margin: 15px 0 24px' in html


def test_result_details_and_query_modes(client):
    html = client.get('/').get_data(as_text=True)

    assert 'class="detail-result-btn"' in html
    assert 'function showResultDetails(domain)' in html
    assert 'id="detailModal"' in html
    assert 'data-query-mode="quick"' in html
    assert 'data-query-mode="unlimited"' in html
    assert 'query_mode: currentQueryMode' in html
    assert '不添加主动请求间隔' in html
    assert 'id="queryTimeout"' in html
    assert '单次请求超时' in html
    assert '重试会累计总耗时' in html
    assert 'query_timeout: queryTimeoutValue()' in html
    assert "['接口原始返回', result.raw_response]" in html
    assert 'raw_response: r.raw_response' in html
    assert 'onclick="selectAll()">☑ 全选</button>' not in html
    assert 'onclick="selectNone()">☐ 取消</button>' not in html


def test_result_detail_dialog_has_fixed_titlebar_and_json_viewer(client):
    html = client.get('/').get_data(as_text=True)

    # 经典窗口标题行：头部固定，只有 .detail-dialog-body 滚动
    assert '<div class="detail-dialog-body">' in html
    assert '.detail-dialog-body { flex: 1 1 auto; min-height: 0; overflow: auto;' in html
    assert '.detail-dialog > .detail-dialog-header {' in html
    assert 'flex: 0 0 auto;' in html
    dialog_start = html.index('id="detailModal"')
    header_pos = html.index('class="detail-dialog-header"', dialog_start)
    body_pos = html.index('class="detail-dialog-body"', dialog_start)
    assert header_pos < body_pos
    assert html.index('id="detailSummary"', dialog_start) > body_pos
    assert html.index('id="detailGrid"', dialog_start) > body_pos

    # 接口原始返回：行号 + JSON 高亮 + 复制
    assert 'function renderRawResponse(value)' in html
    assert 'function highlightJsonLine(line)' in html
    assert 'function copyCodeBlock(button)' in html
    assert '接口原始返回已复制' in html
    assert 'class="code-line-number"' in html
    assert '.code-key { color:' in html
    assert '.code-string { color:' in html
    assert "label === '接口原始返回'" in html


def test_result_status_and_resolution_filters_are_independent(client):
    html = client.get('/').get_data(as_text=True)

    assert 'id="statusFilter"' in html
    assert 'id="resolutionFilter"' in html
    assert 'function resolutionFilterValue(result)' in html
    assert 'function applyResultFilters()' in html
    assert 'function resetResultFilters()' in html
    assert '<option value="timeout">超时</option>' in html


def test_result_filters_live_in_button_dialog(client):
    html = client.get('/').get_data(as_text=True)

    # 筛选项收进「筛选器」按钮的弹窗，按钮位于结果板块下方的导出工具条
    assert 'result-filter-bar' not in html
    assert 'onclick="openFilterDialog()" id="btnOpenFilters"' in html
    assert 'id="filterModal"' in html
    assert 'function openFilterDialog()' in html
    assert 'function closeFilterDialog()' in html
    assert 'function activeResultFilterCount()' in html
    assert 'id="filterActiveBadge"' in html
    assert 'function refreshFilterOptionCounts()' in html
    assert 'function applyFilterOptionCounts(selectId, pool, keyOf)' in html
    assert 'RESULT_FILTER_LABELS' in html
    # 两个下拉与条数提示仍在弹窗/工具条上，保持原有 id 以便筛选逻辑不变
    modal_start = html.index('id="filterModal"')
    assert html.index('id="statusFilter"') > modal_start
    assert html.index('id="resolutionFilter"') > modal_start
    assert 'id="visibleResultCount"' in html
    assert html.index('id="visibleResultCount"') < modal_start


def test_domain_presets_line_numbers_and_sidebar_preferences(client):
    html = client.get('/').get_data(as_text=True)

    assert 'id="domainLineNumbers"' in html
    assert 'function updateDomainLineNumbers()' in html
    assert "fillDomainPreset('popular')" in html
    for domain in ['www.baidu.com', 'cartutuoficina.com', 'handwerkszubehoer.com',
                   'docs.python.org', '2333lqbz.xyz']:
        assert domain in html
    assert "setQueryMode('quick')" in html
    assert "fillDomainPreset('mixed'); setQueryMode('unlimited')" in html
    assert "fillDomainPreset('mixed'); setQueryMode('quick')" not in html
    assert 'domainCheckerSidebarPosition' in html
    assert 'function syncOutlineVisibility()' in html
    assert 'data-section="resultsCard" data-conditional="true"' in html
    assert 'data-section="logCard"' not in html


def test_domain_draft_survives_refresh_and_editor_grows_with_content(client):
    html = client.get('/').get_data(as_text=True)

    # 域名草稿写入浏览器本地，刷新后回填；只有“清空”才移除
    assert "const DOMAIN_DRAFT_STORAGE_KEY = 'domainCheckerDomainDraft'" in html
    assert 'function saveDomainDraft()' in html
    assert 'function loadDomainDraft()' in html
    assert 'function clearDomainDraft()' in html
    assert 'oninput="updateDomainLineNumbers(); saveDomainDraft()"' in html
    assert 'const savedDomainDraft = loadDomainDraft();' in html
    assert "if (savedDomainDraft) document.getElementById('domains').value = savedDomainDraft;" in html
    clear_all_start = html.index('function clearAll()')
    clear_all_end = html.index('// 历史记录', clear_all_start)
    assert 'clearDomainDraft();' in html[clear_all_start:clear_all_end]
    # 输入框随内容增高，最高不超过半个浏览器窗口
    assert 'function updateDomainEditorHeight()' in html
    assert 'max-height: 50vh;' in html
    assert 'window.innerHeight * 0.5' in html
    assert "window.addEventListener('resize', updateDomainEditorHeight);" in html


def test_mobile_sidebar_uses_accessible_hamburger_drawer(client):
    html = client.get('/').get_data(as_text=True)

    assert 'id="sidebarMenuButton"' in html
    assert 'aria-controls="outlineSidebar"' in html
    assert 'aria-expanded="false"' in html
    assert 'id="sidebarBackdrop"' in html
    assert 'function setMobileSidebarOpen(open)' in html
    assert 'function toggleMobileSidebar()' in html
    assert '.outline-sidebar.mobile-open { transform: translateX(0); }' in html
    assert "window.matchMedia('(max-width: 1250px)')" in html
    assert "setMobileSidebarOpen(false);" in html
    # 页眉只保留产品名，标题随内容滚走；主题入口并入吸顶工具栏
    header_start = html.index('<div class="header">')
    header_end = html.index('</div>', header_start)
    assert 'id="themeToggle"' not in html[header_start:header_end]
    assert 'id="sidebarMenuButton"' not in html[header_start:header_end]
    tabs_start = html.index('<div class="tabs">')
    tabs_end = html.index('<!-- 查询页面 -->', tabs_start)
    tabs_block = html[tabs_start:tabs_end]
    assert 'id="sidebarMenuButton"' in tabs_block
    assert 'id="themeToggle"' in tabs_block
    assert '.tabs {\n            position: sticky;' in html
    assert '.tabs-list { display: flex; gap: 5px; min-width: 0; flex: 1 1 auto; }' in html
    assert 'body.sidebar-right .sidebar-menu-button { order: 4; }' in html
    assert '.tab { flex: 0 0 auto; padding: 10px 8px; text-align: center; font-size: 12.5px; }' in html


def test_usage_tab_has_manual_shutdown_and_platform_commands(client):
    html = client.get('/').get_data(as_text=True)

    assert 'data-tab="usage"' in html
    assert 'id="tabUsage"' in html
    assert "document.getElementById('tabUsage').classList.toggle('hidden', tab !== 'usage')" in html
    assert "fetch('/api/shutdown'" not in html
    assert 'id="shutdownServiceButton"' not in html
    assert 'Nginx + Gunicorn + systemd' in html
    assert 'Gunicorn 必须保持单 worker' in html
    assert 'Python 环境推荐使用 uv' in html
    assert '不要同时通过宝塔 Python 项目与 systemd 启动' in html
    assert 'docs/DEPLOYMENT_NGINX.md' in html
    assert '网页不提供关闭服务入口' in html
    assert 'Get-NetTCPConnection -LocalPort 5000' in html
    assert '<h5>1. 查询端口</h5><pre>Get-NetTCPConnection' in html
    assert '<h5>3. 停止服务</h5><pre>Stop-Process' in html
    assert 'lsof -nP -iTCP:5000 -sTCP:LISTEN' in html
    assert "ss -ltnp 'sport = :5000'" in html
    assert '.usage-platforms { display: block; }' in html
    assert '.usage-platforms { display: grid;' not in html
    assert 'id="operationLog"' in html
    assert "fetch('/api/operations?limit=100')" in html
    assert 'function loadOperationLogs()' in html
    assert 'data-tab="operations"' in html
    assert 'id="tabOperations"' in html
    usage_start = html.index('<div id="tabUsage"')
    usage_end = html.index('<!-- 操作日志页面 -->', usage_start)
    assert not usage_start < html.index('id="operationLog"') < usage_end


def test_history_management_uses_batch_selection_and_clear_all(client):
    html = client.get('/').get_data(as_text=True)

    assert 'id="historySelectAll"' in html
    assert 'id="deleteSelectedHistoryButton"' in html
    assert 'function toggleHistorySelection(taskId, checked)' in html
    assert "fetch('/api/history/delete-batch'" in html
    assert "fetch('/api/history/clear-all'" in html
    assert '清理全部' in html
    assert '清理30天前' not in html
    assert "onclick=\"deleteHistory('" not in html


def test_mobile_header_and_failure_reason_are_explicit(client):
    html = client.get('/').get_data(as_text=True)

    assert 'class="header-name">域名批量查询</span>' in html
    assert 'id="currentPlatform"' not in html
    assert 'id="platformName"' not in html
    assert 'body.sidebar-left:not(.sidebar-collapsed)' in html
    assert 'body.sidebar-right:not(.sidebar-collapsed)' in html
    assert 'function resultFailureReason(result)' in html
    for reason in ['配额限制', '网络解析失败', '网络连接失败', 'WHOIS 空响应', 'WHOIS 解析失败']:
        assert reason in html
    assert '查询失败类型：${resultFailureReason(result)}' in html


def test_rdap_is_the_default_implemented_platform(client):
    html = client.get('/').get_data(as_text=True)

    assert "let currentPlatform = 'rdap';" in html
    assert 'class="platform-btn active" data-platform="rdap"' in html
    assert "rdap: { name: 'RDAP优先查询', icon: '🛡️', implemented: true" in html


def test_result_status_columns_sort_by_semantic_rank(client):
    html = client.get('/').get_data(as_text=True)

    for fn in ['queryStateSortRank', 'registrationSortRank', 'resolutionSortRank']:
        assert f'function {fn}(result)' in html
    assert 'statusSortRank = { success: 0, not_registered: 1, timeout: 2, failed: 3, invalid: 4 }' in html
    assert "if (column === 'query_state')" in html
    assert "if (column === 'status')" in html
    assert "if (column === 'resolved')" in html
    assert '空值始终排在最后' in html
    assert 'if (nA === null) return 1;' in html


def test_export_format_is_specifiable_and_count_hints_exist(client):
    html = client.get('/').get_data(as_text=True)

    assert 'id="exportFormat"' in html
    assert 'onchange="saveExportFormat()"' in html
    assert 'domainCheckerExportFormat' in html
    assert 'id="exportCountHint"' in html
    assert 'function updateExportCountHint()' in html
    assert '将导出筛选出的 ${count} 条' in html
    assert '将导出全部 ${count} 条' in html
    # 导出全部跟随表格筛选，不再有独立的导出过滤下拉
    assert 'id="exportFilter"' not in html
    assert 'params.set(\'status\', statusFilter)' in html
    assert 'params.set(\'resolution\', resolutionFilter)' in html
    # 全选只选当前筛选后可见的行
    assert 'getFilteredResults()\n                .filter(r => (r.query_state || \'completed\') === \'completed\')' in html
    assert 'id="historyExportFormat"' in html
    assert 'onchange="saveHistoryExportFormat()"' in html
    assert 'domainCheckerHistoryExportFormat' in html
    assert "const format = document.getElementById('historyExportFormat')?.value || 'csv';" in html
    assert '共 ${total} 条 · 筛选出 ${visibleResults.length} 条' in html
    assert '共 0 条' in html
    assert "document.getElementById('exportFormat').value = savedExportFormat;" in html


def test_favicon_link_and_static_serving(client):
    html = client.get('/').get_data(as_text=True)

    assert '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">' in html
    favicon = client.get('/static/favicon.svg')
    assert favicon.status_code == 200
    assert favicon.content_type.startswith('image/svg+xml')
    assert b'<svg xmlns=' in favicon.data
    assert '域名批量查询系统'.encode('utf-8') in favicon.data


def test_stats_cards_have_icons_and_semantic_colors(client):
    html = client.get('/').get_data(as_text=True)

    for stat_id in ['statTotal', 'statSuccess', 'statFailed', 'statTimeout',
                     'statNotRegistered', 'statBlocked', 'statNormal']:
        assert f'id="{stat_id}"' in html
    assert 'class="stat-icon"' in html
    assert '--stat-color' in html
    assert 'grid-template-columns: repeat(7, minmax(0, 1fr))' in html
    assert '@media (max-width: 1100px)' in html


def test_brief_query_mode_and_empty_duration_placeholder(client):
    html = client.get('/').get_data(as_text=True)

    assert 'data-query-mode="brief"' in html
    assert "setQueryMode('brief')" in html
    assert '单轮注册状态查询（RDAP 必要时回退一次 WHOIS）和一次 DNS 检查，不做 HTTP 探测或自动复查' in html
    assert '<div class="query-elapsed" id="queryElapsed"></div>' in html
    assert "if (seconds === null || seconds === undefined || seconds === '') return '';" in html


def test_config_card_collapses_by_default_with_summary(client):
    html = client.get('/').get_data(as_text=True)

    # 标题行即折叠开关，默认收起，且有可读摘要与主题入口
    assert 'id="configToggle" onclick="toggleConfigCard()"' in html
    assert 'aria-expanded="false" aria-controls="configBody"' in html
    assert '<div class="config-body" id="configBody" hidden>' in html
    assert 'id="configSummary"' in html
    assert 'function toggleConfigCard()' in html
    assert 'function setConfigCollapsed(collapsed, persist = true)' in html
    assert 'function updateConfigSummary()' in html
    assert 'domainCheckerConfigCollapsed' in html
    # 折叠偏好只在用户操作后写入，初始化不覆盖
    assert 'setConfigCollapsed(collapsed, false);' in html
    config_pos = html.index('id="configCard"')
    assert html.index('id="configBody"', config_pos) > html.index('id="configToggle"', config_pos)


def test_concurrency_and_reuse_settings_are_wired(client):
    html = client.get('/').get_data(as_text=True)

    # 并发入口真实可用且有明确上限与生效时机说明
    assert 'id="cfgWorkers"' in html
    assert 'max="32"' in html
    assert '每个任务的并发线程上限' in html
    assert '任务开始后修改配置只影响后续任务' in html
    # 结果复用入口与提示：小时 + 分钟自定义窗口，提交时换算成秒
    assert 'id="cfgReuseHours"' in html
    assert 'id="cfgReuseMinutes"' in html
    assert '结果复用' in html
    assert 'id="reuseSummary"' in html
    assert '复用窗口' in html
    assert 'min="0" max="24"' in html
    assert 'min="0" max="59"' in html
    assert 'reuse_recent_seconds: reuseWindowSeconds()' in html
    assert 'function reuseWindowSeconds()' in html
    assert 'function setReuseWindowInputs(totalSeconds)' in html
    assert 'setReuseWindowInputs(config.reuse_recent_seconds)' in html
    assert '重查」始终强制联网' in html
    # 查询级忽略缓存开关
    assert 'id="ignoreCache"' in html
    assert '默认复用 1 小时内成功结果' in html
    assert "ignore_cache: document.getElementById('ignoreCache').checked" in html
    assert "document.getElementById('ignoreCache').checked = Boolean(ignoreCache)" in html
    # 历史详情同样保留复用来源，重新加载后不会误标为实时查询
    assert 'reused: Boolean(r.reused)' in html
    # 命中复用的结果在状态列可见，详细结果说明来源
    assert "✓ 查询成功${r.reused ? " in html
    assert '查询成功（复用最近结果）' in html
    assert "['结果来源', result.reused" in html


def test_results_table_has_fixed_display_order_number_column(client):
    html = client.get('/').get_data(as_text=True)

    select_pos = html.index('<col data-column="select"')
    number_pos = html.index('<col data-column="row_number"')
    domain_pos = html.index('<col data-column="domain"')
    assert select_pos < number_pos < domain_pos
    assert 'data-column="row_number" style="width: 56px"' in html
    assert 'data-column="row_number" title="按当前展示顺序编号">序号' in html
    assert '<td class="row-number-cell" data-column="row_number">${index + 1}</td>' in html
    assert 'index + 1,\n                    r.domain' in html
    # 序号跟随当前展示顺序，不参与排序和拖拽换序
    assert "'select', 'row_number'" in html
    assert ':not([data-column="row_number"])' in html


def test_result_actions_are_pinned_in_last_column_as_icon_buttons(client):
    html = client.get('/').get_data(as_text=True)

    # 操作列固定在末列
    assert 'data-column="actions" style="width: 112px"' in html
    assert '<th class="actions-cell" data-column="actions" scope="col">操作</th>' in html
    assert 'th.actions-cell {' in html
    assert 'td.actions-cell {' in html
    assert '.table-container.is-h-scrolled td.actions-cell' in html
    assert 'function updateActionsColumnShadow()' in html
    assert "container.classList.toggle('is-h-scrolled'" in html
    error_pos = html.index('<col data-column="error"')
    actions_pos = html.index('<col data-column="actions"')
    assert error_pos < actions_pos
    # 列顺序收敛：旧存储里缺失或乱序的 actions 也会回到末尾
    assert "'registration_date', 'expiration_date', 'name_servers', 'error', 'actions'" in html
    assert "return ['select', 'row_number', ...movable, 'actions'];" in html
    assert "new Set(['select', 'row_number', 'actions'])" in html
    # 操作列不参与拖拽、列宽调整与点击复制
    assert ':not([data-column="actions"])' in html
    assert "!['row_number', 'actions'].includes(header.dataset.column)" in html
    assert 'td:not(.checkbox-cell):not(.actions-cell)' in html
    # 三个操作从域名列移入操作列，且为带 tooltip / aria-label 的图标按钮
    domain_cell_start = html.index('<td data-column="domain">')
    domain_cell_end = html.index('</td>', domain_cell_start)
    domain_cell = html[domain_cell_start:domain_cell_end]
    assert 'retry-domain-btn' not in domain_cell
    assert 'detail-result-btn' not in domain_cell
    assert 'copy-domain-btn' not in domain_cell
    assert '<span class="row-actions">' in html
    assert 'const ACTION_ICONS = {' in html
    assert '${ACTION_ICONS.retry}' in html
    assert '${ACTION_ICONS.detail}' in html
    assert '${ACTION_ICONS.copy}' in html
    assert 'title="查看详细结果" aria-label="查看 ${escapeHtml(r.domain)} 的详细结果"' in html
    assert "button.innerHTML = ACTION_ICONS.check;" in html


def test_persistent_mode_toggle_and_payload(client):
    html = client.get('/').get_data(as_text=True)

    assert 'id="persistentMode"' in html
    assert '> 执着模式' in html
    assert 'id="persistentHint"' in html
    assert 'function persistentModeEnabled()' in html
    assert 'function setPersistentMode(enabled, persist = true)' in html
    assert 'domainCheckerPersistentMode' in html
    assert 'function persistentPayload()' in html
    assert '...persistentPayload()' in html
    assert 'persistent: persistentModeEnabled(),' in html
    assert 'const persistentInfo = status.persistent ? {' in html
    assert 'status.persistent_retrying' in html
    assert '执着模式第' in html
    assert 'id="cfgPersistentRounds"' in html
    assert 'persistent_max_rounds: parseInt' in html
    assert "document.getElementById('cfgPersistentRounds').value = config.persistent_max_rounds || 5;" in html


def test_pause_stops_log_polling_and_refresh_restores_task(client):
    html = client.get('/').get_data(as_text=True)

    # 暂停期间只轮询状态，不再反复拉取日志/结果
    assert "const CURRENT_TASK_STORAGE_KEY = 'domainCheckerCurrentTask'" in html
    assert '暂停期间只刷新状态，不拉取日志/结果' in html
    assert "const skipResults = isPaused && status.status === 'processing'" in html
    assert '等待在途网络请求返回' in html
    # 任务 ID 持久化，刷新后从服务端恢复进度；任务丢失时回填历史域名
    assert 'function saveCurrentTaskState()' in html
    assert 'function restoreCurrentTask()' in html
    assert 'loadConfig().then(restoreCurrentTask)' in html
    assert '已恢复刷新前的查询进度' in html
    assert 'function fillDomainsFromHistory(taskId)' in html
    assert '回填域名' in html
    assert '已中断' in html
    assert "h.status === 'cancelled'" in html
    # 轮询中发现任务因重启丢失时必须停止轮询并回填，而不是永久 404 轮询
    assert 'if (statusResp.status === 404)' in html
    assert '查询任务已中断（服务可能已重启）' in html
