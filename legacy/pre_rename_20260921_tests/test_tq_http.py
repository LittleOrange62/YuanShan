# -*- coding: utf-8 -*-
"""
TQ 本地 JSON-RPC 客户端（`app/datasource/tq_http.py`）。

**刻意不打真接口**：通达信客户端开没开不是代码的事，测试挂在"客户端今天没开"
上等于把断言变成天气预报。真通不通交给 `/datasource` 的探测（kind=tqhttp）。
这里只钉住三件容易写错的事：返回怎么剥、超时怎么归类、日期有没有排序。
"""
import urllib.error

import pytest

from app.datasource import tq_http as T


# ------------------------------------------------------------ 返回解包
def test_unwrap_strips_errorid_envelope():
    assert T._unwrap('m', {'ErrorId': '0', 'Value': [1, 2]}) == [1, 2]


def test_unwrap_raises_on_nonzero_errorid():
    with pytest.raises(T.TqHttpError) as e:
        T._unwrap('get_gb_info', {'ErrorId': '9', 'Value': None})
    # 报错要带上 method，否则一堆 ErrorId=9 分不清是哪个接口
    assert 'get_gb_info' in str(e.value)


def test_unwrap_passes_through_bare_payloads():
    """
    有接口直接返回数组（`get_sector_list` 实测就是），盲目取 ['Value'] 会炸。
    """
    bare = [{'Code': '880201.SH', 'Name': '黑龙江'}]
    assert T._unwrap('get_sector_list', bare) is bare
    assert T._unwrap('m', 'ok') == 'ok'


# ------------------------------------------------------------ 探活
def test_available_reports_false_instead_of_raising(monkeypatch):
    """
    通达信没开是**常态**，不是异常。`available()` 必须返回 (False, 原因)，
    否则每个调用方都要包一层 try。
    """
    def boom(*a, **kw):
        raise urllib.error.URLError('拒绝连接')

    monkeypatch.setattr(T, '_post', boom)
    ok, msg = T.available()
    assert ok is False
    assert '通达信' in msg           # 原因要说清是客户端没开，不是网络坏了


def test_available_reports_false_on_empty_result(monkeypatch):
    monkeypatch.setattr(T, '_post', lambda *a, **kw: [])
    assert T.available()[0] is False


# ------------------------------------------------------------ 参数
def test_gb_info_sorts_dates_and_fills_count(monkeypatch):
    """
    `date_list` 乱序时 TQ 会静默给出错的对应关系 —— 比报错更难查，所以替调用方排。
    """
    seen = {}

    def fake(method, params, *, timeout, **kw):
        seen.update(params)
        return []

    monkeypatch.setattr(T.reg, 'call',
                        lambda key, fn, *a, **kw: fn(*a, **kw))
    monkeypatch.setattr(T, '_post', fake)
    T.gb_info('600519.SH', ['20260831', '20260828'])
    assert seen['date_list'] == ['20260828', '20260831']
    assert seen['count'] == 2


def test_market_data_omits_range_when_not_given(monkeypatch):
    """
    `count > 0` 时 TQ 只看 count；不传 start/end 就别硬塞空串进去。
    """
    seen = {}

    def fake(method, params, *, timeout, **kw):
        seen.update(params)
        return {}

    monkeypatch.setattr(T.reg, 'call',
                        lambda key, fn, *a, **kw: fn(*a, **kw))
    monkeypatch.setattr(T, '_post', fake)
    T.market_data(['600519.SH'], period='5m', count=3)
    assert 'start_time' not in seen and 'end_time' not in seen
    assert seen['period'] == '5m'
    assert seen['dividend_type'] == 'none'


# ------------------------------------------------------------ 口径
def test_amount_scale_is_wan_yuan():
    """`Amount` 是万元。这个常量错了，成交额会差一万倍且图上很难看出来。"""
    assert T.AMOUNT_SCALE == 10_000
