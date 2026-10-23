"""配置归一化与 settings.json 加载测试。"""

import json

import pytest

from domain_checker import settings
from domain_checker.settings import CONFIG, coerce_config_value


class TestCoerceConfigValue:
    def test_workers_are_clamped_to_supported_range(self):
        assert coerce_config_value('max_workers', 999) == 32
        assert coerce_config_value('max_workers', 0) == 1
        assert coerce_config_value('max_workers', -5) == 1
        assert coerce_config_value('max_workers', '8') == 8

    def test_reuse_window_and_delays_are_clamped(self):
        assert coerce_config_value('reuse_recent_seconds', -30) == 0
        assert coerce_config_value('reuse_recent_seconds', 999999) == 86400
        assert coerce_config_value('rate_limit_delay', -1) == 0.0
        assert coerce_config_value('retry_delay', 999) == 300.0
        assert coerce_config_value('max_retries', 0) == 1

    def test_timeout_keeps_existing_clamp_behaviour(self):
        assert coerce_config_value('timeout', 999) == 120
        assert coerce_config_value('timeout', 0) == 1

    def test_persistent_rounds_are_clamped(self):
        assert coerce_config_value('persistent_max_rounds', 0) == 1
        assert coerce_config_value('persistent_max_rounds', 999) == 20
        assert coerce_config_value('persistent_max_rounds', '4') == 4
        with pytest.raises(ValueError, match='persistent_max_rounds'):
            coerce_config_value('persistent_max_rounds', 'many')

    def test_invalid_numbers_and_platform_fallback(self):
        with pytest.raises(ValueError, match='max_workers'):
            coerce_config_value('max_workers', 'abc')
        with pytest.raises(ValueError):
            coerce_config_value('rate_limit_delay', None)
        assert coerce_config_value('platform', 'nope') == 'rdap'
        assert coerce_config_value('proxy_enabled', 0) is False
        assert coerce_config_value('proxy_enabled', 1) is True


def test_load_config_normalizes_invalid_file_values(tmp_path, monkeypatch):
    settings_file = tmp_path / 'settings.json'
    settings_file.write_text(json.dumps({
        'max_workers': 999,
        'reuse_recent_seconds': -10,
        'rate_limit_delay': 'not-a-number',
        'max_retries': 0,
        'unknown_key': 'ignored',
    }), encoding='utf-8')
    monkeypatch.setattr(settings, 'SETTINGS_PATH', settings_file)

    original = dict(CONFIG)
    try:
        settings.load_config_from_file()

        assert CONFIG['max_workers'] == 32
        assert CONFIG['reuse_recent_seconds'] == 0
        assert CONFIG['max_retries'] == 1
        # 非法值被忽略，保留原值，而不是让任务线程稍后抛异常
        assert CONFIG['rate_limit_delay'] == original['rate_limit_delay']
        assert 'unknown_key' not in CONFIG
    finally:
        with settings.config_lock:
            CONFIG.update(original)


def test_load_config_missing_file_keeps_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'SETTINGS_PATH', tmp_path / 'missing.json')
    original = dict(CONFIG)

    settings.load_config_from_file()

    assert original == CONFIG
