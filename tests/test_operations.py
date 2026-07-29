"""服务操作日志持久化测试。"""


def test_operation_log_round_trip(tmp_path, monkeypatch):
    from domain_checker import operations

    log_path = tmp_path / 'operations.log'
    monkeypatch.setattr(operations, 'OPERATIONS_LOG_PATH', log_path)
    monkeypatch.setattr(operations.os, 'getpid', lambda: 1234)

    operations.record_operation('启动', '监听 127.0.0.1:5000')
    operations.record_operation('终止', '网页关闭')

    entries = operations.get_operations(limit=1)
    assert entries == [{
        'time': entries[0]['time'],
        'action': '终止',
        'pid': 1234,
        'detail': '网页关闭',
    }]
    assert len(log_path.read_text(encoding='utf-8').splitlines()) == 2
