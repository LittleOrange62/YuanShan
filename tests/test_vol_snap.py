# -*- coding: utf-8 -*-
"""量能快照（`app/intraday/vol_snap.py` + `analytics.index_volume_snap`，DDL 62）的钉住用例。

用户 11-05：量能脚本要写进库（每天两针：noon/close，五指数+两市）、推送要精炼
（同比只留百分比、不堆绝对亿值）、要位置（250 日**加权**分位）和判断（低位反转/高位反转九宫）。
所有用例**不出网、不写库**：取数从 `vol_snap._fetch_minutes` / `vol_snap._read_day` 打桩，
落库经 `store.upsert_index_volume_snap`（pytest 进程里有 `_no_test_write` 硬闸）。
"""
from __future__ import annotations

import datetime as dt
import struct

import pytest

from app.intraday import store, vol_snap

ROW = struct.Struct('<IIIIIfII')


def _day_blob(days_amounts: list[tuple[int, float]]) -> bytes:
    return b''.join(ROW.pack(d, 0, 0, 0, 0, a, 0, 0) for d, a in days_amounts)


# ---------------------------------------------------------------- 纯函数

def test_加权分位比原始分位更重近端():
    # 序列旧→新：先 100 个高位（100），再 5 个低位（1）。v=1：
    # 原始分位 = 5/105；加权（半衰期 1 天）几乎只看最后几天 ⇒ 分位应显著更高。
    series = [100.0] * 100 + [1.0] * 5
    raw = sum(1 for x in series if x <= 1.0) / len(series)
    w = vol_snap.weighted_pctile(1.0, series, half_life=1.0)
    assert w > raw
    assert w > 0.9            # 近端几乎全是 1，压过的权重比例很高
    assert vol_snap.weighted_pctile(1.0, [], half_life=60) is None


def test_加权分位端点():
    s = [float(i) for i in range(1, 11)]
    assert vol_snap.weighted_pctile(10.0, s, half_life=60) == pytest.approx(1.0)
    # x=1 是最旧的一天，权重最小 ⇒ 加权分位比原始 10% 略低（权重压在近端）
    assert vol_snap.weighted_pctile(1.0, s, half_life=60) == pytest.approx(0.0949, abs=1e-4)


def test_trend_5d_末比初():
    assert vol_snap.trend_5d([10, 11, 12, 13, 14]) == pytest.approx(14 / 10 - 1)
    assert vol_snap.trend_5d([1, 2, 3]) is None          # 不足 5 个
    assert vol_snap.trend_5d([0, 1, 2, 3, 4]) is None    # 初值 0


def test_九宫标签_用户点名的两格():
    j = vol_snap.judge
    assert j(0.10, 0.20) == '低位反转'     # 用户 11-05 举例的形态
    assert j(0.85, -0.20) == '高位反转'
    assert j(0.50, 0.20) == '中位上行'
    assert j(0.05, -0.20) == '低位下行'
    assert j(0.95, 0.20) == '高位放量'
    assert j(0.50, 0.01) == '中位徘徊'     # ±5% 带内算平
    assert j(0.50, 0.05) == '中位上行'     # 边界：≥ +5% 算升
    assert j(0.30, 0.20) == '中位上行'     # 边界：30 不算低（低 <30）
    assert j(0.70, -0.20) == '中位下行'    # 边界：70 不算高（高 >70）
    assert j(None, 0.20) is None
    assert j(0.50, None) is None


def test_pattern_5d_升降计数():
    assert vol_snap.pattern_5d([15.5, 16.7, 17.8, 17.0, 20.4]) == '3升1降'
    assert vol_snap.pattern_5d([1, 2, 3, 4, 5]) == '4连升'
    assert vol_snap.pattern_5d([5, 4, 3, 2, 1]) == '4连降'
    assert vol_snap.pattern_5d([1, 2, 3]) is None


# ---------------------------------------------------------------- compute（打桩不出网）

def _patch_sources(monkeypatch, minutes: dict, day_rows: dict, quote: float | None = None):
    """minutes: {tencent_code: {YYYYMMDD: {HHMM: 累计元}}}；day_rows: {.day名: [(YYYYMMDD, 元)]}。
    quote：腾讯实时报价 p[37]（元）的桩值，默认 None＝报价也取不到。"""
    def fake_fetch(code):
        if code not in minutes:
            raise OSError('分钟源挂了')
        return minutes[code]
    def fake_read(name, n):
        return (day_rows.get(name) or [])[-n:]
    monkeypatch.setattr(vol_snap, '_fetch_minutes', fake_fetch)
    monkeypatch.setattr(vol_snap, '_read_day', fake_read)
    monkeypatch.setattr(vol_snap, '_fetch_quote_day', lambda code: quote)


def test_compute_noon_早间口径与缺数纪律(monkeypatch):
    today, prev = 20261105, 20261104
    minutes = {
        'sh000001': {str(today): {'1130': 6.7e11, '0930': 3e11},
                     str(prev): {'1130': 4.3e11, '1500': 9.5e11},
                     '20261103': {'1500': 1e12}},
        'sz399001': {str(today): {'1130': 7.9e11}, str(prev): {'1130': 5e11, '1500': 1.1e12}},
        'sz399006': {str(today): {'1130': 3e11}, str(prev): {'1130': 2e11}},
        'sh000688': {str(today): {'1130': 1e11}, str(prev): {'1130': 8e10}},
    }
    # .day：尾行 = 前一交易日（11-05 还没落），5 行足够
    days = [20261028, 20261030, 20261102, 20261103]
    day_rows = {
        '上证指数': [(d, 9e11) for d in days] + [(prev, 9.5e11)],
        '深证成指': [(d, 1e12) for d in days] + [(prev, 1.1e12)],
        '创业板指': [(d, 5e11) for d in days] + [(prev, 5.5e11)],
        '科创50': [(d, 1e11) for d in days] + [(prev, 1.05e11)],
        '北证50': [(d, 1.5e11) for d in days] + [(prev, 1.45e11)],
    }
    _patch_sources(monkeypatch, minutes, day_rows)
    d = vol_snap.compute('noon', dt.datetime(2026, 11, 5, 12, 10))

    sh = d['idx']['上证']
    assert sh['amount'] == pytest.approx(6.7e11)                    # 今早 11:30 累计
    assert sh['yoy'] == pytest.approx(6.7e11 / 4.3e11 - 1)          # 对前日 11:30
    assert d['idx']['北证']['amount'] is None                       # 北证无分钟源 ⇒ 缺
    assert '北证50' in d['idx']['北证']['missing']['amount']
    assert d['asof']['上证'] == prev                                # 分位窗尾 = 前一交易日
    assert d['tot']['amount'] is None                               # 两市早间合计不算（缺北证腿）
    assert d['tot']['pctile'] is not None                           # 但两市位置照样有
    assert d['src'].startswith('腾讯分钟')


def test_compute_noon_分钟源全挂时位置仍可判(monkeypatch):
    days = [20261030, 20261102, 20261103, 20261104]
    day_rows = {
        '上证指数': [(d, 9e11) for d in days] + [(20261104, 9.5e11)],
        '深证成指': [(d, 1e12) for d in days] + [(20261104, 1.1e12)],
    }
    _patch_sources(monkeypatch, {}, day_rows)
    d = vol_snap.compute('noon', dt.datetime(2026, 11, 5, 12, 10))
    for name in ('上证', '深证'):
        assert d['idx'][name]['amount'] is None
        assert d['idx'][name]['pctile'] is not None                # .day 还在 ⇒ 位置不缺


def test_compute_close_分钟挂时本地日线已落当天就用本地(monkeypatch):
    today, prev = 20261104, 20261103
    # 腾讯今天这条挂了（只剩前日全天可对比），.day 尾行恰好已落到当天
    minutes = {'sh000001': {str(prev): {'1500': 1e12}}}
    days = [20261029, 20261030, 20261102, prev]
    day_rows = {
        '上证指数': [(d, 9e11) for d in days] + [(today, 9.5e11)],
        '深证成指': [(d, 1e12) for d in days] + [(today, 1.05e12)],
    }
    _patch_sources(monkeypatch, minutes, day_rows)
    d = vol_snap.compute('close', dt.datetime(2026, 11, 4, 18, 18))
    sh = d['idx']['上证']
    assert sh['amount'] == pytest.approx(9.5e11)                    # .day 尾行是当天 ⇒ 本地优先
    assert sh['yoy'] == pytest.approx(9.5e11 / 9e11 - 1)
    assert d['asof']['上证'] == today                               # 分位窗吃到当天


def test_compute_close_日线未落当天用分钟1500兜底(monkeypatch):
    today, prev = 20261104, 20261103
    minutes = {'sh000001': {str(today): {'1500': 9.4e11}, str(prev): {'1500': 1e12}}}
    days = [20261029, 20261030, 20261102, prev]
    day_rows = {
        '上证指数': [(d, 9e11) for d in days],
        '深证成指': [(d, 1e12) for d in days],
    }
    _patch_sources(monkeypatch, minutes, day_rows)
    d = vol_snap.compute('close', dt.datetime(2026, 11, 4, 18, 18))
    assert d['idx']['上证']['amount'] == pytest.approx(9.4e11)      # 腾讯 15:00
    assert d['asof']['上证'] == prev                                # 分位窗尾仍是前一日


# ---------------------------------------------------------------- 落库行宽

def test_close_北证_day未落当天时用实时报价兜底(monkeypatch):
    """北证 50 无分钟源，18:18 时本地 .day 常还没落到当天 —— 全天额用腾讯报价 p[37]。"""
    today, prev = 20261105, 20261104
    days = [20261028, 20261030, 20261102, 20261103]
    day_rows = {'北证50': [(d, 1.4e11) for d in days] + [(prev, 1.45e11)]}
    _patch_sources(monkeypatch, {}, day_rows, quote=1.47e11)
    d = vol_snap.compute('close', dt.datetime(2026, 11, 5, 18, 18))
    bj = d['idx']['北证']
    assert bj['amount'] == pytest.approx(1.47e11)
    assert bj['yoy'] == pytest.approx(1.47e11 / 1.45e11 - 1)   # 前一日全天用 .day（同源最近）
    assert bj['pctile'] is not None


def test_write_snap_六行且缺数是NULL不是0(monkeypatch):
    captured = {}
    monkeypatch.setattr(store, 'upsert_index_volume_snap',
                        lambda rows: captured.setdefault('rows', rows) and len(rows))
    today, prev = 20261105, 20261104
    minutes = {'sh000001': {str(today): {'1130': 1e11}, str(prev): {'1130': 9e10}}}
    days = [20261028, 20261030, 20261102, 20261103]
    day_rows = {
        '上证指数': [(d, 9e11) for d in days] + [(prev, 9e11)],
        '深证成指': [(d, 1e12) for d in days] + [(prev, 1e12)],
    }
    _patch_sources(monkeypatch, minutes, day_rows)
    n = vol_snap.write_snap('noon', dt.datetime(2026, 11, 5, 12, 10))
    assert n == 6 and len(captured['rows']) == 6
    by_name = {r[2]: r for r in captured['rows']}
    assert set(by_name) == {'上证', '深证', '创业板', '科创', '北证', '两市'}
    bj = by_name['北证']
    assert bj[0] == dt.date(2026, 11, 5) and bj[1] == 'noon' and bj[3] == 'v1'
    assert bj[5] is None, '北证早间额没有分钟源 ⇒ NULL（不是 0，0 是零成交的真实读数）'
    tot = by_name['两市']
    assert tot[7] is not None, '两市位置照样有（分位走 .day，与早间额无关）'
    # 12 值 + calculated_at 走 now()：行宽必须与 store._VOLSNAP 的占位符数一致
    import re
    ph = len(re.findall(r'%s', store._VOLSNAP.split('ON CONFLICT')[0].split('VALUES')[1]))
    assert len(by_name['上证']) == ph


def test_write_snap_测试进程里不许真写():
    _patch_sources(pytest.MonkeyPatch(), {}, {})
    with pytest.raises(RuntimeError, match='pytest 进程里不许真写'):
        vol_snap.write_snap('noon', dt.datetime(2026, 11, 5, 12, 10))
