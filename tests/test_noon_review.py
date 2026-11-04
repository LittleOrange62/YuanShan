# -*- coding: utf-8 -*-
"""12:10 午间复盘（`ingest/jobs/push/t05_noon_review.py` + `app/intraday/volume.py`）的钉住用例。

这一族判据是从外部 `intraday_zt/noon_review.py`（2026-10-30 定版）搬来的，
搬的时候按仓库口径改了**一处已登记的缺陷**（涨跌家数的 `or None` 会吞掉真实的 0），
所以这里既有"规则一字未改"的档位钉住，也有那条改动本身的取证。
所有用例**不出网、不写库**：取数与落库都被打桩。
"""
from __future__ import annotations

import datetime as dt
import json
import struct

import pytest

from app.intraday import store, volume
from ingest.jobs.push import t05_noon_review as N


# ---------------------------------------------------------------- 列序与落库

def test_午间快照的列序与_DDL_逐字一致():
    assert store._noon_sql_columns() == store.NOON_COLUMNS + ('calculated_at',)


def test_少交一格当场点名不许静默默认():
    parts = {c: None for c in store.NOON_COLUMNS}
    parts.pop('body')
    with pytest.raises(KeyError, match='body'):
        store.noon_row(parts)
    parts['body'] = 'x'
    parts['unknown_col'] = 1
    with pytest.raises(KeyError, match='unknown_col'):
        store.noon_row(parts)


def test_午间行宽与插入占位符数相等():
    parts = {c: None for c in store.NOON_COLUMNS}
    parts.update({'trade_date': dt.date(2026, 11, 2), 'spec_version': 'x',
                  'missing': '{}'})
    assert len(store.noon_row(parts)) == len(store.NOON_COLUMNS) == 29


# ---------------------------------------------------------------- 判读档位

def test_情绪档位命中条数决定标签():
    assert '冰点偏退潮' in N.mood(2, 20, 30, 100, 900)[0]      # 倒挂+炸板率+涨跌比
    assert '退潮分歧' in N.mood(2, 20, 30, None, None)[0]      # 倒挂+炸板率
    assert '分歧偏弱' in N.mood(2, 20, 0, None, None)[0]       # 只命中倒挂一条
    assert '偏暖修复' in N.mood(40, 3, 5, 3000, 1000)[0]
    assert '中性' in N.mood(20, 10, 5, None, None)[0]


def test_炸板率封板率各说一格():
    _txt, zb_pct, seal_pct = N.mood(60, 5, 40, None, None)
    assert zb_pct == '40%' and seal_pct == '60%'
    assert N.mood(0, 0, 0, None, None)[1] == '缺数'


def test_涨跌家数为0是读数不是缺数():
    """外部那句 `sum(...) or None` 会把"全场只数 0"吞成缺数 —— 搬进来时必须改掉
    （《10-盘中播报与事件流入库》§二.3 点名的三处之一）。"""
    payload = json.dumps({"data": {"diff": [
        {"f14": "上证指数", "f3": 1.0, "f104": 0, "f105": 1200},
        {"f14": "深证成指", "f3": 1.2, "f104": 0, "f105": 800}]}})
    monkey = _patch_get(N, lambda url, enc='utf-8': payload)
    with monkey:
        idx, up, down, src = N.market_stats()
    assert (up, down) == (0, 2000) and src == "eastmoney_ulist"
    txt, _zb, _seal = N.mood(5, 500, 5, up, down)
    assert '极端弱势' in txt                       # 0 家涨必须参与判读，不能被跳过


def test_东财挂了退腾讯时家数明标缺数():
    def boom(url, enc='utf-8'):
        raise OSError('连不上')
    monkey = _patch_get(N, boom)
    with monkey:
        idx, up, down, src = N.market_stats()
    assert up is None and down is None and '两源皆挂' in src


# ---------------------------------------------------------------- 量能一节

def test_量能阈值带内持平():
    assert volume.label(0.19) == '放量'
    assert volume.label(-0.19) == '缩量'
    assert volume.label(0.03) == '持平'
    assert volume.cmp_text(None, 100) == '缺数'
    assert volume.cmp_text(100, 0) == '缺数'          # 分母 0 不算 +inf
    assert '放量' in volume.cmp_text(110, 100)


def test_day_尾行按32字节结构读():
    row = struct.Struct('<IIIIIfII')
    blob = (row.pack(20261028, 0, 0, 0, 0, 100.0, 0, 0)
            + row.pack(20261029, 0, 0, 0, 0, 200.0, 0, 0)
            + row.pack(20261030, 0, 0, 0, 0, 300.0, 0, 0))
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 'sh000001.day'
        p.write_bytes(blob)
        assert volume.day_amounts(p, 5) == [(20261028, 100.0), (20261029, 200.0), (20261030, 300.0)]
        assert volume.day_amounts(Path(td) / 'not_here.day') == []


def test_主源全挂时量能报缺数而不是空():
    def boom(code):
        raise OSError('腾讯不通')
    orig = volume.tx_minutes
    volume.tx_minutes = boom
    try:
        lines, src = volume.volume_lines(dt.datetime(2026, 11, 2, 12, 10), note=lambda w, kw: None)
    finally:
        volume.tx_minutes = orig
    assert src in ('无', '.day')
    assert any('缺数' in ln for ln in lines)


# ---------------------------------------------------------------- 测试自己不许脏当天的账

def test_测试进程里写这五张表一律被挡():
    """11-03 的现场：`market_stats()` 的失败留痕在测试里真写进了 `intraday_broadcast`，
    当天凭空多出 22 行"取数失败"，界面与看护一起念这些假事。
    闸住在 `store._no_test_write`，这里钉住它**五个写入口一个都不放过**。"""
    import pytest as _pt
    calls = [
        ('write_broadcast', lambda: store.write_broadcast(dt.datetime.now(), task='noon', what='x')),
        ('write_events', lambda: store.write_events(dt.datetime.now(), [], pool_n={})),
        ('upsert_auction_rows', lambda: store.upsert_auction_rows([])),
        ('upsert_concept_counts', lambda: store.upsert_concept_counts([])),
        ('upsert_noon', lambda: store.upsert_noon(())),
    ]
    for name, fn in calls:
        with _pt.raises(RuntimeError, match='pytest 进程里不许真写'):
            fn()


def test_留痕写不进去时取数照旧出声不把作业带崩(monkeypatch):
    """测试里那道闸会抛（上面一条钉着）；`_log` 必须吞掉它 —— 留痕是观测层，
    观测层坏了不该让播报本身失败。但**吞的是写库异常，不是取数异常**。"""
    def boom(*a, **kw):
        raise RuntimeError('pytest 进程里不许真写 analytics 表')
    monkeypatch.setattr(store, 'write_broadcast', boom)
    with _patch_get(N, lambda url, enc='utf-8': (_ for _ in ()).throw(OSError('连不上'))):
        idx, up, down, src = N.market_stats()
    assert (idx, up, down) == ({}, None, None) and '两源皆挂' in src


# ---------------------------------------------------------------- 整链路（全打桩）

def _patch_get(mod, fn):
    import unittest.mock as m
    return m.patch.object(mod, '_get', side_effect=fn)


def _fake_round(monkeypatch, captured: dict):
    zt = [{'code': f'6000{i:02d}', 'name': f'股{i}', 'board_count': 3 if i == 0 else 1,
           'seal_fund_yuan': 2e8 if i == 0 else 5e7, 'pct': 10.0} for i in range(5)]
    monkeypatch.setattr(N.zt_watch, 'fetch_pools',
                        lambda now: ({r['code']: r for r in zt}, {}, {}))
    monkeypatch.setattr(N, 'market_stats', lambda: ({"上证指数": 1.1, "深证成指": 1.6}, 3000, 1000, "eastmoney_ulist"))
    monkeypatch.setattr(N, 'board_labels', lambda rows: {rows[0]['code']: '3天3板'})
    monkeypatch.setattr(N, 'premium_stats', lambda: {"date": dt.date(2026, 10, 30), "n": 3,
                                                     "quotes": 3, "pos": 2, "pos_rate": 2 / 3,
                                                     "avg": 1.5})
    monkeypatch.setattr(N, 'concept_groups',
                        lambda rows, labels, **kw: [("人工智能", 3, "60%", "主线候选(家数第一)，梯队最高3板",
                                                     "股0 3天3板", 0.6, 3, "BK0001", 0)])
    monkeypatch.setattr(N.volume, 'volume_lines',
                        lambda now, **kw: (["量能：测试行"], "腾讯分钟"))
    monkeypatch.setattr(N.tasks, 'send_channels', lambda ch=None: ["feishu-main"])

    def fake_upsert(row):
        captured['noon_row'] = row
        return 1
    monkeypatch.setattr(store, 'upsert_noon', fake_upsert)
    monkeypatch.setattr(store, 'upsert_concept_counts',
                        lambda rows: captured.setdefault('concepts', rows) and len(rows))
    monkeypatch.setattr(store, 'write_broadcast',
                        lambda *a, **kw: captured.setdefault('casts', []).append((a, kw)))
    def fake_send(keys, title, body):
        captured.setdefault('sent', []).append((keys, title))
        return {"ok": True, "detail": "ok"}
    monkeypatch.setattr('duanxian.notify.send_to', fake_send)


def test_整轮落库的午间行宽与列序正确(monkeypatch):
    captured: dict = {}
    _fake_round(monkeypatch, captured)
    assert N.one_round(dry_run=False, send=True, channel=None, force=True) == 0
    row = captured['noon_row']
    assert len(row) == len(store.NOON_COLUMNS)
    d = dict(zip(store.NOON_COLUMNS, row))
    assert d['limit_up_n'] == 5 and d['limit_down_n'] == 0 and d['unseal_n'] == 0
    assert d['up_count'] == 3000 and d['down_count'] == 1000      # 0 与读数都不吞
    assert d['premium_pos_rate_pct'] == pytest.approx(2 / 3 * 100)
    assert '人工智能' in d['section_cluster'] and '测试行' in d['body']
    # 用户 11-03 定的版式：标题不带【】、正文末尾不再挂那句来源说明
    assert d['body'].split('\n')[0] == f"午间复盘 · {d['trade_date']:%m-%d} 上午盘" \
        or d['body'].startswith('午间复盘 · ')
    assert '【' not in d['body'].split('\n')[0], '第一行还带方括号 = 群里标题会重复一遍'
    assert '—东财涨跌停池+腾讯行情' not in d['body']
    assert '量能:腾讯分钟' in d['index_source'], '来源不发了，但必须仍然落在库里（不然事后问"这数哪来的"没人答得上）'
    assert json.loads(d['missing']) == {}
    assert captured['sent'] and captured['sent'][0][0] == ['feishu-main']
    buckets = {r[2] for r in captured['concepts']}
    assert buckets == {'noon_zt'}                                  # 与竞价/热度同一张表同一列序


def test_dry_run_一条都不写(monkeypatch):
    captured: dict = {}
    _fake_round(monkeypatch, captured)
    assert N.one_round(dry_run=True, send=True, channel=None, force=True) == 0
    assert 'noon_row' not in captured and 'sent' not in captured and 'casts' not in captured
