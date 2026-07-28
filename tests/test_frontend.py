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


def test_query_progress_has_loading_duration_and_incremental_logs(client):
    html = client.get('/').get_data(as_text=True)

    assert 'id="queryActivity"' in html
    assert 'id="queryElapsed"' in html
    assert 'function formatDuration(seconds)' in html
    assert 'log_after=${lastLogCursor}' in html
    assert 'data-section="resultsCard"' in html
    assert '.query-activity.processing {' in html
    assert 'const statusResp = await fetch(`/api/status/${currentTaskId}`)' in html
    assert '状态完成后再读取结果' in html


def test_results_show_query_time_duration_and_domain_hold_status(client):
    html = client.get('/').get_data(as_text=True)

    assert 'data-sort="query_time"' in html
    assert 'data-sort="query_duration_seconds"' in html
    assert '查询时间' in html
    assert '查询耗时' in html
    assert '停止解析（域名被封）' in html
    assert 'whois_status' in html
    assert 'formatResultDuration' in html
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
    assert '.tab { flex: 1 1 50%; padding: 11px 8px; text-align: center; }' in html


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
