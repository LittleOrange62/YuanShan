# -*- coding: utf-8 -*-
"""「当日涨幅 / 15 日涨幅」：窗口读数与它的出口清洗。

用户 2026-10-10 要倍量榜末尾加这两列。三条钉的是三个真踩过的点：

1. **必须用后复权价**（`close × adjustment_factor`，定义在 `app/db/repo/quote.py:444`）：
   本地那列 `pct_change` 是未复权的原始比值，除息日会把一次派息算成一根大跌
   （实测 000155 在 2026-09-22：未复权 −2.77%，复权 −0.60%）；
2. **NULL 在 pandas 里是 float NaN 而不是 None**：`NaN is not None` 恒真，
   不收口就会一路跟到 `json.dumps` 出去，产出非法的 `NaN` 字面量、浏览器解析直接炸
   —— 这一条不是推演，10-10 第一次实跑就在 `chg_15` 上炸出来了；
3. **缺行交 None，不拿更早的一天冒充基期**：那样"15 日涨幅"其实是"23 日涨幅"，
   而格子上完全看不出来。
"""
import json

import pandas as pd
import pytest

from duanxian import day_returns as dr
from duanxian import pg


# ------------------------------------------------------------------ 纯判据

def test_缺任何一边都是none而不是零():
    assert dr._pct(10.0, 10.0) == 0.0, "真的平盘才给 0"
    assert dr._pct(None, 10.0) is None
    assert dr._pct(10.0, None) is None
    assert dr._pct(10.0, 0) is None, "基期为 0（脏数据）不能除出个无穷大"
    assert dr._pct(float('nan'), 10.0) is None
    assert dr._pct(10.0, float('nan')) is None


def test_涨幅按百分给两位():
    assert dr._pct(12.62, 9.10) == 38.68
    assert dr._pct(11.58, 11.91 * 0.97817) == -0.6, "复权口径下 000155 那天是微跌，不是 −2.77%"


# ------------------------------------------------------------------ 出口清洗

def test_null变成nan也必须收成none_json才出不去非法字面量(monkeypatch):
    """10-10 实测：某只票只有 14 根（缺基期那天），pandas 交回的 `h_base` 是 NaN。"""
    monkeypatch.setattr(pg, "enabled", lambda: True)
    monkeypatch.setattr(pg, "fetch_df", lambda sql, params: pd.DataFrame(
        [{'security_code': '605577.SH', 'h_last': 9.9, 'h_prev': 9.5,
          'h_base': float('nan'), 'base_day': float('nan'), 'bars': 14},
         {'security_code': '600000.SH', 'h_last': 12.0, 'h_prev': 11.0,
          'h_base': 10.0, 'base_day': '2026-09-10', 'bars': 16}]))
    got = dr.returns_by_code(['605577.SH', '600000.SH'], '2026-10-09')
    assert got['605577.SH']['chg_span'] is None
    assert got['605577.SH']['base_day'] is None, "文字列同样会带 NaN（StringDtype），不能直接交出去"
    assert got['605577.SH']['chg_today'] == 4.21
    assert got['600000.SH']['chg_span'] == 20.0
    assert got['605577.SH']['bars'] == 14 and isinstance(got['605577.SH']['bars'], int)
    json.dumps(got, ensure_ascii=False, allow_nan=False)      # 有 NaN 就抛


def test_查询坏了交回空表_与个别缺行是两句话(monkeypatch):
    monkeypatch.setattr(pg, "enabled", lambda: True)
    monkeypatch.setattr(pg, "fetch_df", lambda sql, params: None)
    assert dr.returns_by_code(['600000.SH'], '2026-10-09') == {}


def test_没有代码就不打库(monkeypatch):
    called = []
    monkeypatch.setattr(pg, "enabled", lambda: True)
    monkeypatch.setattr(pg, "fetch_df", lambda sql, params: called.append(1) or pd.DataFrame())
    assert dr.returns_by_code([], '2026-10-09') == {}
    assert not called


# ------------------------------------------------------------------ SQL 的口径

def test_窗口用后复权价并且不读那一列未复权的涨跌幅():
    import inspect

    src = inspect.getsource(dr.returns_by_code)
    assert "coalesce(sd.adjustment_factor, 1)" in src, "价格必须乘因子，否则跨除权日不连续"
    assert "pct_change" not in src, "本地 pct_change 是未复权的原始比值，用它=除息日算成暴跌"
    # 按**市场场次**数窗口：`row_number() over (order by trade_date desc)`，
    # 写成对该票自己的行取第 N 根，就把停牌票的跨度悄悄拉长了
    assert "row_number() over (order by trade_date desc)" in src
    assert "sd.security_code = any(" in src, "整批一次查询；逐行打库就是 150 次"


def test_窗口场数是跨度加一(monkeypatch):
    """15 日涨幅要 16 场：目标日自己也算一场。少给一场就把 15 日算成 14 日。"""
    seen = {}
    import pandas as _pd

    def spy(sql, params):
        seen['params'] = params
        return _pd.DataFrame()
    monkeypatch.setattr(pg, "enabled", lambda: True)
    monkeypatch.setattr(pg, "fetch_df", spy)
    dr.returns_by_code(['600000.SH'], '2026-10-09', span=15)
    assert seen['params'][1] == 16 and seen['params'][3] == 16
    assert dr.SPAN == 15
