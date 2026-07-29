"""批量任务编排与重查状态测试。"""

import threading
from unittest import mock

from domain_checker.settings import CONFIG
from domain_checker.state import task_lock, task_pause_flags, task_storage, wait_for_task_resume
from domain_checker.tasks import (
    _complete_operation,
    _process_domain_worker,
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
                      query_mode='unlimited', query_timeout=None, platform='rdap'):
        captured.update(
            max_workers=max_workers,
            track_operation=track_operation,
            query_mode=query_mode,
            query_timeout=query_timeout,
            platform=platform,
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
