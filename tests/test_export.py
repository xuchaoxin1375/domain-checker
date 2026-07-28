"""导出功能单元测试。"""

import csv
import os

import pytest

from domain_checker.export import EXPORT_HEADERS, create_csv, create_export_file, create_xlsx


def _results():
    return [
        {'domain': 'ok.com', 'status': 'success', 'resolved': True,
         'dns_records': ['1.1.1.1'], 'block_reason': ''},
        {'domain': 'held.com', 'status': 'success', 'resolved': False,
         'dns_records': [], 'block_reason': '未解析（NXDOMAIN）：域名已注册但DNS中无解析记录'},
        {'domain': 'bad.com', 'status': 'failed', 'resolved': None,
         'dns_records': [], 'block_reason': '', 'error': '域名不存在'},
    ]


class TestCsv:
    def test_header_and_rows(self):
        path = create_csv(_results(), 'test_ts_csv')
        try:
            with open(path, encoding='utf-8-sig') as f:
                rows = list(csv.reader(f))
            assert rows[0] == EXPORT_HEADERS
            assert '解析异常原因' in rows[0]
            assert '封禁原因' not in rows[0]
            assert rows[1][8] == '正常解析'
            assert rows[2][8] == '未解析'
            assert rows[3][8] == '未知'
        finally:
            os.remove(path)


class TestXlsx:
    def test_file_created(self):
        path = create_xlsx(_results(), 'test_ts_xlsx')
        try:
            assert path.endswith('.xlsx')
            assert os.path.getsize(path) > 0
        finally:
            os.remove(path)


class TestFilters:
    @pytest.mark.parametrize('filter_type,expected', [
        ('all', {'ok.com', 'held.com', 'bad.com'}),
        ('success', {'ok.com', 'held.com'}),
        ('normal', {'ok.com'}),
        ('failed', {'bad.com'}),
        ('blocked', {'held.com'}),
    ])
    def test_filter_matrix(self, filter_type, expected):
        path, _name = create_export_file(_results(), 'csv', filter_type)
        try:
            with open(path, encoding='utf-8-sig') as f:
                rows = list(csv.reader(f))
            assert {r[0] for r in rows[1:]} == expected
        finally:
            os.remove(path)
