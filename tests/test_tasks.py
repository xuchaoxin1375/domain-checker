"""批量任务编排与重查状态测试。"""

import threading
import time
from datetime import datetime
from unittest import mock

from domain_checker import db
from domain_checker.settings import CONFIG
from domain_checker.state import task_lock, task_pause_flags, task_storage, wait_for_task_resume
from domain_checker.tasks import (
    _complete_operation,
    _process_domain_worker,
    _run_pool,
    _worker_config,
    create_queued_result,
    process_domains_async,
    retry_domains_async,
)


def test_brief_worker_config_limits_retries():
    cfg = _worker_config('brief')

    assert cfg['max_retries'] == 1
    assert cfg['rate_limit_delay'] == 0
    assert cfg['retry_delay'] == 0


def test_worker_marks_domain_as_querying(monkeypatch):
    observed = []
    with task_lock:
        task_storage['STAGE01'] = {
            'status': 'processing', 'results': [create_queued_result('stage.com')],
        }

    def fake_process(domain, task_id, **_kwargs):
        with task_lock:
            result = task_storage[task_id]['results'][0]
            observed.append({
                'query_state': result['query_state'],
                'query_time': result['query_time'],
                'query_duration_seconds': result['query_duration_seconds'],
            })
        return {'domain': domain}

    monkeypatch.setattr('domain_checker.tasks.process_single_domain', fake_process)
    try:
        _process_domain_worker('stage.com', 'STAGE01')
        assert observed[0]['query_state'] == 'querying'
        assert observed[0]['query_time']
        assert observed[0]['query_duration_seconds'] is None
    finally:
        with task_lock:
            task_storage.pop('STAGE01', None)


def test_worker_waits_until_task_resumes(monkeypatch):
    called = threading.Event()
    with task_lock:
        task_storage['PAUSE01'] = {
            'status': 'processing', 'results': [create_queued_result('paused.com')],
        }
    task_pause_flags['PAUSE01'] = True
    monkeypatch.setattr(
        'domain_checker.tasks.process_single_domain',
        lambda *args, **kwargs: called.set(),
    )
    worker = threading.Thread(target=_process_domain_worker, args=('paused.com', 'PAUSE01'))
    try:
        worker.start()
        assert not called.wait(0.2)
        task_pause_flags['PAUSE01'] = False
        worker.join(timeout=2)
        assert called.is_set()
        assert not worker.is_alive()
    finally:
        task_pause_flags.pop('PAUSE01', None)
        with task_lock:
            task_storage.pop('PAUSE01', None)


def test_cancelled_pause_wait_returns_without_blocking():
    with task_lock:
        task_storage['CANCELWAIT'] = {'status': 'cancelled'}
    task_pause_flags['CANCELWAIT'] = True
    try:
        assert wait_for_task_resume('CANCELWAIT') is False
    finally:
        task_pause_flags.pop('CANCELWAIT', None)
        with task_lock:
            task_storage.pop('CANCELWAIT', None)


def test_task_completion_excludes_paused_duration(monkeypatch):
    with task_lock:
        task_storage['PAUSEDURATION'] = {
            'status': 'processing', 'results': [{'status': 'success'}],
            'logs': [], 'paused_duration_seconds': 4.0,
        }
    monkeypatch.setattr('domain_checker.tasks.time.perf_counter', lambda: 110.0)
    try:
        completed = _complete_operation('PAUSEDURATION', 100.0, '查询', 1)
        assert completed is not None
        with task_lock:
            assert task_storage['PAUSEDURATION']['duration_seconds'] == 6.0
    finally:
        with task_lock:
            task_storage.pop('PAUSEDURATION', None)


def test_process_domains_uses_configured_worker_count(monkeypatch):
    captured = {}
    now = '2026-07-28T10:00:00'
    with task_lock:
        task_storage['POOL01'] = {
            'status': 'processing', 'total': 2, 'completed': 0,
            'results': [], 'logs': [], 'refresh': False,
            'created_at': now, 'completed_at': None, 'platform': 'whois',
            'operation': 'query', 'operation_total': 2, 'operation_completed': 0,
            'operation_started_at': now, 'duration_seconds': None,
        }

    def fake_run_pool(domains, task_id, max_workers, track_operation=False,
                      query_mode='unlimited', query_timeout=None, platform='rdap',
                      reuse_map=None):
        captured.update(
            max_workers=max_workers,
            track_operation=track_operation,
            query_mode=query_mode,
            query_timeout=query_timeout,
            platform=platform,
            reuse_map=reuse_map,
            paused_before_pool=task_pause_flags.get(task_id, False),
        )
        with task_lock:
            task_storage[task_id]['results'] = [
                {'domain': domain, 'status': 'success'} for domain in domains
            ]
            task_storage[task_id]['completed'] = len(domains)
            task_storage[task_id]['operation_completed'] = len(domains)

    monkeypatch.setitem(CONFIG, 'max_workers', 2)
    monkeypatch.setattr('domain_checker.tasks._run_pool', fake_run_pool)
    monkeypatch.setattr('domain_checker.tasks.save_history', lambda *args: None)
    monkeypatch.setattr('domain_checker.tasks.save_results', lambda *args: None)
    monkeypatch.setattr('domain_checker.tasks.update_history_counts', lambda *args: None)
    task_pause_flags['POOL01'] = True

    try:
        process_domains_async(['a.com', 'b.com'], 'POOL01')
        assert captured['max_workers'] == 2
        assert captured['track_operation'] is True
        assert captured['query_mode'] == 'unlimited'
        assert captured['query_timeout'] == CONFIG['timeout']
        assert captured['platform'] == 'whois'
        assert captured['paused_before_pool'] is True
        with task_lock:
            assert task_storage['POOL01']['status'] == 'completed'
            assert task_storage['POOL01']['duration_seconds'] is not None
    finally:
        with task_lock:
            task_storage.pop('POOL01', None)


def test_worker_exception_becomes_failed_result_and_task_completes(monkeypatch):
    now = '2026-07-28T10:00:00'
    with task_lock:
        task_storage['POOLFAIL'] = {
            'status': 'processing', 'total': 1, 'completed': 0,
            'results': [], 'logs': [], 'refresh': False,
            'created_at': now, 'completed_at': None, 'platform': 'whois',
            'operation': 'query', 'operation_total': 1, 'operation_completed': 0,
            'operation_started_at': now, 'duration_seconds': None,
        }

    monkeypatch.setattr(
        'domain_checker.tasks._process_domain_worker',
        lambda *args: (_ for _ in ()).throw(RuntimeError('unexpected')),
    )
    monkeypatch.setattr('domain_checker.tasks.save_history', lambda *args: None)
    monkeypatch.setattr('domain_checker.tasks.save_results', lambda *args: None)
    monkeypatch.setattr('domain_checker.tasks.update_history_counts', lambda *args: None)

    try:
        process_domains_async(['broken.com'], 'POOLFAIL')
        with task_lock:
            task = task_storage['POOLFAIL']
            assert task['status'] == 'completed'
            assert task['operation_completed'] == 1
            assert task['results'][0]['domain'] == 'broken.com'
            assert task['results'][0]['status'] == 'failed'
            assert '内部处理异常' in task['results'][0]['error']
    finally:
        with task_lock:
            task_storage.pop('POOLFAIL', None)


def test_retry_replaces_result_with_client_hold_and_persists(monkeypatch):
    now = '2026-07-28T10:00:00'
    original = {
        'domain': 'held.com', 'status': 'success', 'registrar': 'Registrar',
        'whois_status': 'ok', 'hold_status': None, 'resolved': True,
        'block_reason': None, 'dns_records': ['1.2.3.4'], 'error': None,
    }
    with task_lock:
        task_storage['RETRYHOLD'] = {
            'status': 'processing', 'total': 1, 'completed': 1,
            'results': [original], 'logs': [], 'refresh': True,
            'created_at': now, 'completed_at': None, 'platform': 'whois',
            'query_mode': 'standard',
            'operation': 'retry', 'operation_total': 1, 'operation_completed': 0,
            'operation_started_at': now, 'duration_seconds': None,
        }

    whois_result = {
        'domain': 'held.com', 'status': 'success', 'registrar': 'Registrar',
        'registration_date': None, 'expiration_date': None, 'updated_date': None,
        'name_servers': None, 'dnssec': None,
        'whois_status': 'clientHold, clientTransferProhibited',
        'hold_status': None, 'error': None,
    }
    persisted = {}
    dns_check = mock.Mock()
    monkeypatch.setattr('domain_checker.checker.query_whois_with_retry', lambda domain, **kwargs: whois_result.copy())
    monkeypatch.setattr('domain_checker.checker.check_domain_resolved', dns_check)
    monkeypatch.setattr('domain_checker.tasks.save_results', lambda task_id, results: persisted.update(results=results))
    monkeypatch.setattr('domain_checker.tasks.update_history_counts', lambda *args: None)

    try:
        retry_domains_async(['held.com'], 'RETRYHOLD')
        with task_lock:
            task = task_storage['RETRYHOLD']
            assert task['status'] == 'completed'
            assert task['operation_completed'] == 1
            assert len(task['results']) == 1
            result = task['results'][0]
            assert result['hold_status'] == 'clientHold'
            assert result['resolved'] is False
            assert result['block_reason'].startswith('停止解析（域名被封）')
        assert persisted['results'][0]['hold_status'] == 'clientHold'
        dns_check.assert_not_called()
    finally:
        with task_lock:
            task_storage.pop('RETRYHOLD', None)


class TestResultReuse:
    def _task(self, task_id, domains, operation='query'):
        now = '2026-08-01T10:00:00'
        with task_lock:
            task_storage[task_id] = {
                'status': 'processing', 'total': len(domains), 'completed': 0,
                'results': [create_queued_result(domain) for domain in domains],
                'logs': [], 'refresh': False,
                'created_at': now, 'completed_at': None, 'platform': 'rdap',
                'operation': operation,
                'operation_total': len(domains), 'operation_completed': 0,
                'operation_started_at': now, 'duration_seconds': None,
                'paused_duration_seconds': 0.0, '_paused_started_monotonic': None,
            }

    def test_query_passes_reuse_map_and_logs_hit(self, monkeypatch):
        cached = {
            'domain': 'cached.com', 'status': 'success', 'resolved': True,
            'query_time': '2026-08-01 09:55:00', 'query_duration_seconds': 1.2,
        }
        captured = {}
        self._task('REUSE01', ['cached.com', 'fresh.com'])
        monkeypatch.setitem(CONFIG, 'reuse_recent_seconds', 600)
        lookup = mock.Mock(return_value={'cached.com': cached})
        monkeypatch.setattr('domain_checker.tasks.find_recent_success_results', lookup)

        def fake_run_pool(domains, task_id, max_workers, **kwargs):
            captured.update(kwargs)
            with task_lock:
                task_storage[task_id]['completed'] = len(domains)
                task_storage[task_id]['operation_completed'] = len(domains)

        monkeypatch.setattr('domain_checker.tasks._run_pool', fake_run_pool)
        monkeypatch.setattr('domain_checker.tasks.save_history', lambda *args: None)
        monkeypatch.setattr('domain_checker.tasks.save_results', lambda *args: None)
        monkeypatch.setattr('domain_checker.tasks.update_history_counts', lambda *args: None)

        try:
            process_domains_async(['cached.com', 'fresh.com'], 'REUSE01')

            lookup.assert_called_once_with(['cached.com', 'fresh.com'], 600)
            assert captured['reuse_map'] == {'cached.com': cached}
            with task_lock:
                logs = [log['message'] for log in task_storage['REUSE01']['logs']]
            assert any('命中 1 条' in message for message in logs)
        finally:
            with task_lock:
                task_storage.pop('REUSE01', None)

    def test_retry_always_runs_fresh(self, monkeypatch):
        captured = {}
        self._task('RETRYREUSE', ['retry.com'], operation='retry')
        lookup = mock.Mock(side_effect=AssertionError('重查不得读取复用缓存'))
        monkeypatch.setattr('domain_checker.tasks.find_recent_success_results', lookup)

        def fake_run_pool(domains, task_id, max_workers, **kwargs):
            captured.update(kwargs)
            with task_lock:
                task_storage[task_id]['results'] = [
                    {'domain': domain, 'status': 'success'} for domain in domains
                ]

        monkeypatch.setattr('domain_checker.tasks._run_pool', fake_run_pool)
        monkeypatch.setattr('domain_checker.tasks.save_results', lambda *args: None)
        monkeypatch.setattr('domain_checker.tasks.update_history_counts', lambda *args: None)

        try:
            retry_domains_async(['retry.com'], 'RETRYREUSE')

            lookup.assert_not_called()
            assert captured.get('reuse_map') is None
        finally:
            with task_lock:
                task_storage.pop('RETRYREUSE', None)

    def test_ignore_cache_skips_reuse_lookup_and_logs(self, monkeypatch):
        captured = {}
        self._task('IGNORECACHE', ['cached.com'])
        with task_lock:
            task_storage['IGNORECACHE']['ignore_cache'] = True
        lookup = mock.Mock(side_effect=AssertionError('忽略缓存时不得读取复用结果'))
        monkeypatch.setattr('domain_checker.tasks.find_recent_success_results', lookup)

        def fake_run_pool(domains, task_id, max_workers, **kwargs):
            captured.update(kwargs)
            with task_lock:
                task_storage[task_id]['completed'] = len(domains)
                task_storage[task_id]['operation_completed'] = len(domains)

        monkeypatch.setattr('domain_checker.tasks._run_pool', fake_run_pool)
        monkeypatch.setattr('domain_checker.tasks.save_history', lambda *args: None)
        monkeypatch.setattr('domain_checker.tasks.save_results', lambda *args: None)
        monkeypatch.setattr('domain_checker.tasks.update_history_counts', lambda *args: None)

        try:
            process_domains_async(['cached.com'], 'IGNORECACHE')

            lookup.assert_not_called()
            assert captured['reuse_map'] == {}
            with task_lock:
                logs = [log['message'] for log in task_storage['IGNORECACHE']['logs']]
            assert any('忽略缓存' in message for message in logs)
        finally:
            with task_lock:
                task_storage.pop('IGNORECACHE', None)

    def test_worker_forwards_cached_result(self, monkeypatch):
        cached = {'domain': 'forward.com', 'status': 'success'}
        seen = {}
        self._task('FORWARD', ['forward.com'])

        def fake_process(domain, task_id, **kwargs):
            seen.update(kwargs)
            return {'domain': domain, 'status': 'success'}

        monkeypatch.setattr('domain_checker.tasks.process_single_domain', fake_process)
        try:
            _process_domain_worker('forward.com', 'FORWARD', cached_result=cached)
            assert seen['cached_result'] is cached
        finally:
            with task_lock:
                task_storage.pop('FORWARD', None)


def test_run_pool_actually_uses_configured_workers(monkeypatch):
    """并发设置必须真的并行执行，而不只是写进日志。"""
    domains = [f'concurrent{i}.com' for i in range(4)]
    now = '2026-08-01T10:00:00'
    with task_lock:
        task_storage['POOLCONC'] = {
            'status': 'processing', 'total': len(domains), 'completed': 0,
            'results': [create_queued_result(domain) for domain in domains],
            'logs': [], 'refresh': False, 'platform': 'rdap',
            'created_at': now, 'completed_at': None, 'duration_seconds': None,
        }

    lock = threading.Lock()
    state = {'active': 0, 'peak': 0}

    def fake_process(domain, task_id, **_kwargs):
        with lock:
            state['active'] += 1
            state['peak'] = max(state['peak'], state['active'])
        time.sleep(0.05)
        with lock:
            state['active'] -= 1
        return {'domain': domain, 'status': 'success'}

    monkeypatch.setattr('domain_checker.tasks.process_single_domain', fake_process)
    try:
        _run_pool(domains, 'POOLCONC', max_workers=2)
        assert state['peak'] == 2
        assert state['active'] == 0
    finally:
        with task_lock:
            task_storage.pop('POOLCONC', None)


def test_run_pool_workers_are_daemon_threads(monkeypatch):
    """进程退出/调试重载时，查询线程不能阻止旧进程结束。"""
    domain = 'daemon-check.com'
    now = '2026-08-01T10:00:00'
    with task_lock:
        task_storage['DAEMON01'] = {
            'status': 'processing', 'total': 1, 'completed': 0,
            'results': [create_queued_result(domain)], 'logs': [], 'refresh': False,
            'platform': 'rdap', 'created_at': now, 'completed_at': None,
            'duration_seconds': None,
        }

    daemon_flags = []

    def fake_process(domain, task_id, **_kwargs):
        daemon_flags.append(threading.current_thread().daemon)
        return {'domain': domain, 'status': 'success'}

    monkeypatch.setattr('domain_checker.tasks.process_single_domain', fake_process)
    try:
        _run_pool([domain], 'DAEMON01', max_workers=1)
        assert daemon_flags == [True]
    finally:
        with task_lock:
            task_storage.pop('DAEMON01', None)


def test_worker_config_clamps_unsafe_values(monkeypatch):
    monkeypatch.setitem(CONFIG, 'max_workers', 999)
    monkeypatch.setitem(CONFIG, 'rate_limit_delay', -5)

    cfg = _worker_config('standard')

    assert cfg['max_workers'] == 32
    assert cfg['rate_limit_delay'] == 0


def test_recent_success_result_is_reused_end_to_end(monkeypatch):
    """真实串联 DB 查询 → 工作线程 → 单域名流水线：命中复用时不发网络请求。"""
    task_id = 'REUSEE2E'
    domain = 'reuse-e2e.com'
    db.save_history(task_id, [domain], 'processing')
    db.save_results(task_id, [{
        'domain': domain, 'status': 'success', 'registrar': 'Cached Registrar',
        'whois_status': 'ok', 'hold_status': '', 'resolved': True,
        'block_reason': '', 'dns_records': ['1.2.3.4'], 'error': '',
        'raw_response': 'raw', 'query_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'query_duration_seconds': 3.5,
    }])
    db.update_history_counts(task_id, 1, 0)

    now = '2026-08-01T10:00:00'
    with task_lock:
        task_storage['REUSEJOB'] = {
            'status': 'processing', 'total': 1, 'completed': 0,
            'results': [create_queued_result(domain)], 'logs': [], 'refresh': False,
            'created_at': now, 'completed_at': None, 'platform': 'rdap',
            'operation': 'query', 'operation_total': 1, 'operation_completed': 0,
            'operation_started_at': now, 'duration_seconds': None,
        }

    query = mock.Mock(side_effect=AssertionError('命中复用后不得联网查询注册信息'))
    dns_check = mock.Mock(side_effect=AssertionError('命中复用后不得联网检查 DNS'))
    monkeypatch.setitem(CONFIG, 'reuse_recent_seconds', 600)
    monkeypatch.setattr('domain_checker.checker.query_registration_with_retry', query)
    monkeypatch.setattr('domain_checker.checker.check_domain_resolved', dns_check)
    monkeypatch.setattr('domain_checker.tasks.save_history', lambda *args: None)
    monkeypatch.setattr('domain_checker.tasks.save_results', lambda *args: None)
    monkeypatch.setattr('domain_checker.tasks.update_history_counts', lambda *args: None)

    try:
        process_domains_async([domain], 'REUSEJOB')

        query.assert_not_called()
        dns_check.assert_not_called()
        with task_lock:
            task = task_storage['REUSEJOB']
            result = task['results'][0]
            assert task['status'] == 'completed'
            assert task['operation_completed'] == 1
            assert result['reused'] is True
            assert result['registrar'] == 'Cached Registrar'
            assert result['query_duration_seconds'] == 3.5
        assert any('复用最近成功结果' in log['message'] for log in task['logs'])
    finally:
        with task_lock:
            task_storage.pop('REUSEJOB', None)
        db.delete_history(task_id)


def test_completed_batch_is_reused_by_next_batch(monkeypatch):
    """验证完整闭环：第一批联网完成后落库，第二批同域名直接复用历史结果。"""
    domain = 'reuse-chain.com'
    first_id, second_id = 'REUSECHAIN1', 'REUSECHAIN2'
    now = '2026-08-01T10:00:00'

    def add_task(task_id):
        with task_lock:
            task_storage[task_id] = {
                'status': 'processing', 'total': 1, 'completed': 0,
                'results': [create_queued_result(domain)], 'logs': [], 'refresh': False,
                'created_at': now, 'completed_at': None, 'platform': 'rdap',
                'operation': 'query', 'operation_total': 1, 'operation_completed': 0,
                'operation_started_at': now, 'duration_seconds': None,
            }

    registration = mock.Mock(return_value={
        'domain': domain, 'status': 'success', 'registrar': 'Chain Registrar',
        'registration_date': '2020-01-01', 'expiration_date': '2030-01-01',
        'updated_date': None, 'name_servers': 'ns1.chain.com', 'dnssec': 'unsigned',
        'contact_email': None, 'whois_status': 'ok', 'hold_status': None,
        'error': None, 'raw_response': 'raw',
    })
    dns_check = mock.Mock(return_value={
        'resolved': True, 'block_reason': None, 'dns_records': ['1.2.3.4'],
    })
    monkeypatch.setitem(CONFIG, 'reuse_recent_seconds', 600)
    monkeypatch.setattr('domain_checker.checker.query_registration_with_retry', registration)
    monkeypatch.setattr('domain_checker.checker.check_domain_resolved', dns_check)

    try:
        add_task(first_id)
        process_domains_async([domain], first_id)

        assert registration.call_count == 1
        with task_lock:
            assert task_storage[first_id]['results'][0]['reused'] is False
        # 关键前提：第一批结果确实写入了 SQLite（复用只读历史，不读内存任务）
        _, rows = db.get_history_detail(first_id)
        assert rows and rows[0]['status'] == 'success'

        registration.side_effect = AssertionError('第二批命中复用后不得联网查询注册信息')
        dns_check.side_effect = AssertionError('第二批命中复用后不得联网检查 DNS')
        add_task(second_id)
        process_domains_async([domain], second_id)

        assert registration.call_count == 1
        assert dns_check.call_count == 1
        with task_lock:
            second = task_storage[second_id]['results'][0]
            assert second['reused'] is True
            assert second['registrar'] == 'Chain Registrar'
        _, rows = db.get_history_detail(second_id)
        assert rows[0]['reused'] == 1
    finally:
        with task_lock:
            task_storage.pop(first_id, None)
            task_storage.pop(second_id, None)
        task_pause_flags.pop(first_id, None)
        task_pause_flags.pop(second_id, None)
        db.delete_history(first_id)
        db.delete_history(second_id)


def test_cancelled_task_persists_partial_results_for_reuse(monkeypatch):
    """取消任务时保留已完成结果：历史标记为已终止，成功行仍可被下次查询复用。"""
    domain = 'cancel-partial.com'
    task_id = 'CANCELPARTIAL'
    now = '2026-08-01T10:00:00'
    with task_lock:
        task_storage[task_id] = {
            'status': 'processing', 'total': 1, 'completed': 0,
            'results': [create_queued_result(domain)], 'logs': [], 'refresh': False,
            'created_at': now, 'completed_at': None, 'platform': 'rdap',
            'operation': 'query', 'operation_total': 1, 'operation_completed': 0,
            'operation_started_at': now, 'duration_seconds': None,
        }

    def fake_process(domain, task_id, **_kwargs):
        # 模拟查询完成的同时用户终止任务
        with task_lock:
            task_storage[task_id]['status'] = 'cancelled'
        return {
            'domain': domain, 'status': 'success', 'registrar': 'Partial Registrar',
            'whois_status': 'ok', 'hold_status': None, 'resolved': True,
            'block_reason': None, 'dns_records': ['1.2.3.4'], 'error': None,
            'raw_response': 'raw', 'reused': False, 'query_state': 'completed',
            'query_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'query_duration_seconds': 1.0,
        }

    monkeypatch.setitem(CONFIG, 'reuse_recent_seconds', 600)
    monkeypatch.setattr('domain_checker.tasks.process_single_domain', fake_process)
    try:
        process_domains_async([domain], task_id)

        history, rows = db.get_history_detail(task_id)
        assert history['status'] == 'cancelled'
        assert history['results_count'] == 1
        assert rows[0]['status'] == 'success'

        found = db.find_recent_success_results([domain], 600)
        assert found[domain]['registrar'] == 'Partial Registrar'
    finally:
        with task_lock:
            task_storage.pop(task_id, None)
        task_pause_flags.pop(task_id, None)
        db.delete_history(task_id)


def test_cancelled_inflight_failure_does_not_overwrite_saved_success(monkeypatch):
    """取消产生的失败结果不能覆盖该域名已经落库的成功结果。"""
    domain = 'keep-partial.com'
    task_id = 'CANCELKEEP'
    db.save_history(task_id, [domain], 'processing')
    db.save_result(task_id, {
        'domain': domain, 'status': 'success', 'registrar': 'Keep Registrar',
        'resolved': True, 'dns_records': ['1.2.3.4'], 'error': None,
        'query_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'query_duration_seconds': 1.0,
    })
    with task_lock:
        task_storage[task_id] = {
            'status': 'processing', 'total': 1, 'completed': 0,
            'results': [create_queued_result(domain)], 'logs': [], 'refresh': False,
            'created_at': '2026-08-01T10:00:00', 'completed_at': None,
            'platform': 'rdap', 'operation': 'query', 'operation_total': 1,
            'operation_completed': 0, 'operation_started_at': '2026-08-01T10:00:00',
            'duration_seconds': None,
        }

    def fake_process(domain, **_kwargs):
        return {
            'domain': domain, 'status': 'failed', 'error': '任务已取消',
            'resolved': None, 'query_state': 'completed',
        }

    monkeypatch.setattr('domain_checker.tasks.process_single_domain', fake_process)
    try:
        _run_pool([domain], task_id, max_workers=1)

        found = db.find_recent_success_results([domain], 600)
        assert found[domain]['registrar'] == 'Keep Registrar'
    finally:
        with task_lock:
            task_storage.pop(task_id, None)
        task_pause_flags.pop(task_id, None)
        db.delete_history(task_id)
