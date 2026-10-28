# -*- coding: utf-8 -*-
"""
行情外链的口径测试。

链接错了不会抛异常，点开才发现是 404 —— 尤其北交所：东财的路径是
`bj/920000` 而不是 `bj920000`（后者 404，2026-08-29 实测）。这一条最容易在
重构时被"顺手统一"成前缀式写法，所以钉在测试里。
"""
from __future__ import annotations

import pytest

from app import quote_link as ql
from app.web import ui


@pytest.mark.parametrize(('code', 'url'), [
    ('600367.SH', 'https://quote.eastmoney.com/sh600367.html'),
    ('600519.SH', 'https://quote.eastmoney.com/sh600519.html'),
    ('000001.SZ', 'https://quote.eastmoney.com/sz000001.html'),
    ('300750.SZ', 'https://quote.eastmoney.com/sz300750.html'),
    ('510300.SH', 'https://quote.eastmoney.com/sh510300.html'),   # ETF
    ('159915.SZ', 'https://quote.eastmoney.com/sz159915.html'),   # ETF
    ('920000.BJ', 'https://quote.eastmoney.com/bj/920000.html'),  # 带斜杠
    ('000001.SH', 'https://quote.eastmoney.com/zs000001.html'),   # 指数用 zs
    ('399001.SZ', 'https://quote.eastmoney.com/zs399001.html'),
    # 输入形态：带前缀、裸 6 位、小写，都要归到同一个地址
    ('sh600367', 'https://quote.eastmoney.com/sh600367.html'),
    ('600367', 'https://quote.eastmoney.com/sh600367.html'),
    ('sz000001', 'https://quote.eastmoney.com/sz000001.html'),
])
def test_em_url(code, url):
    assert ql.em_url(code) == url


@pytest.mark.parametrize('code', [
    '880501.SH',    # 通达信板块，没有个股行情页
    'TDX_880501',   # 板块主键
    '999999.SH',    # 通达信伪代码
    '', None, '一段中文', '60051',
])
def test_no_url(code):
    assert ql.em_url(code) is None


def test_markdown_falls_back_to_plain_text():
    assert ql.em_markdown('600367.SH', '红星发展') == (
        '[红星发展](https://quote.eastmoney.com/sh600367.html)')
    # 没有行情页时保留文字本身，不能变成空
    assert ql.em_markdown('880501.SH', '半导体') == '半导体'


def test_linkify_rows_links_code_and_name():
    rows = [{'代码': '600519.SH', '名称': '贵州茅台', '操作分': 61.2}]
    out, md_cols = ui.linkify_rows(rows)
    assert md_cols == {'代码', '名称'}
    assert out[0]['代码'].startswith('[600519.SH](https://quote.eastmoney.com/')
    assert out[0]['名称'] == (
        '[贵州茅台](https://quote.eastmoney.com/sh600519.html)')
    assert out[0]['操作分'] == 61.2          # 其它列一个字都不许动
    assert rows[0]['代码'] == '600519.SH'    # 原始行不被就地改写


def test_linkify_rows_leaves_sector_rows_alone():
    rows = [{'代码': '880501', '名称': '半导体', '热度': 88}]
    out, md_cols = ui.linkify_rows(rows)
    assert md_cols == set()
    assert out == rows


def test_linkify_rows_without_code_column():
    rows = [{'交易日': '2026-08-28', '上涨家数': 3011}]
    out, md_cols = ui.linkify_rows(rows)
    assert (out, md_cols) == (rows, set())
