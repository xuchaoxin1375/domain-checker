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
    assert '已请求暂停，当前网络请求完成后生效' in html


def test_runtime_logs_have_line_numbers_copy_and_fullscreen(client):
    html = client.get('/').get_data(as_text=True)

    assert 'class="log-number"' in html
    assert 'function copyRunLogs()' in html
    assert '运行日志已复制' in html
    assert 'function toggleLogFullscreen()' in html
    assert 'id="logFullscreenButton"' in html
    assert '#logCard:fullscreen' in html
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


def test_result_status_and_resolution_filters_are_independent(client):
    html = client.get('/').get_data(as_text=True)

    assert 'id="statusFilter"' in html
    assert 'id="resolutionFilter"' in html
    assert 'function resolutionFilterValue(result)' in html
    assert 'function applyResultFilters()' in html
    assert 'function resetResultFilters()' in html
    assert '<option value="timeout">超时</option>' in html


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
    assert 'data-section="logCard" data-conditional="true"' in html


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
    header_start = html.index('<div class="header">')
    header_end = html.index('</div>', header_start)
    assert header_start < html.index('id="sidebarMenuButton"') < header_end
    assert 'position: sticky;' in html
    assert 'body.sidebar-right .sidebar-menu-button { order: 3; }' in html
    assert '.tab { flex: 1 1 25%; padding: 11px 6px; text-align: center; }' in html


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


def test_brief_query_mode_and_empty_duration_placeholder(client):
    html = client.get('/').get_data(as_text=True)

    assert 'data-query-mode="brief"' in html
    assert "setQueryMode('brief')" in html
    assert '单轮注册状态查询（RDAP 必要时回退一次 WHOIS）和一次 DNS 检查，不做 HTTP 探测或自动复查' in html
    assert '<div class="query-elapsed" id="queryElapsed"></div>' in html
    assert "if (seconds === null || seconds === undefined || seconds === '') return '';" in html
