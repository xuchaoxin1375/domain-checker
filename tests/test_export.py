"""导出功能单元测试。"""

import csv
import os

import pytest

from domain_checker.export import EXPORT_HEADERS, create_csv, create_export_file, create_xlsx


def _results():
    return [
        {'domain': 'ok.com', 'status': 'success', 'resolved': True,
         'whois_status': 'ok', 'contact_email': 'abuse@example.com',
         'dns_records': ['1.1.1.1'], 'block_reason': ''},
        {'domain': 'held.com', 'status': 'success', 'resolved': False,
         'dns_records': [], 'block_reason': '未解析（NXDOMAIN）：域名已注册但DNS中无解析记录'},
        {'domain': 'bad.com', 'status': 'failed', 'resolved': None,
         'dns_records': [], 'block_reason': '', 'error': 'boom'},
        {'domain': 'never.com', 'status': 'not_registered', 'resolved': None,
         'dns_records': [], 'block_reason': '', 'error': '域名未被注册'},
        {'domain': 'slow.com', 'status': 'timeout', 'resolved': None,
         'dns_records': [], 'block_reason': '', 'error': 'WHOIS 查询超时，无法确认域名状态'},
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
            assert rows[0][2] == '注册状态'
            assert rows[0][3] == '域名状态'
            assert rows[0][5] == '联系邮箱'
            assert rows[1][2] == '已注册'
            assert rows[1][3] == 'ok'
            assert rows[1][5] == 'abuse@example.com'
            assert rows[1][11] == '正常解析'
            assert rows[2][11] == '未解析'
            assert rows[3][11] == '未知'
            # 未注册状态导出为明确文案
            assert rows[4][1] == '查询成功'
            assert rows[4][2] == '未注册'
            assert rows[4][-1] == '域名未被注册'
            assert rows[5][1] == '查询超时'
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
        ('all', {'ok.com', 'held.com', 'bad.com', 'slow.com', 'never.com'}),
        ('success', {'ok.com', 'held.com'}),
        ('normal', {'ok.com'}),
        ('failed', {'bad.com'}),
        ('timeout', {'slow.com'}),
        ('not_registered', {'never.com'}),
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
