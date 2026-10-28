# -*- coding: utf-8 -*-
"""目标日闸门（`app/compute/target_guard.py`）的回归。

立这条的原因（实测 2026-09-30）：给 10 个加工作业一场**上游没有的交易日**，
它们**全部 rc=0**，其中七个还退回去把上一场已有的日子重算了一遍
（`.local/audit_missing_target.py`，目标日 2026-10-01/10-02）。
页面上"点火/情绪/趋势"看着新鲜，其实晚一场 —— 这比报错难发现得多。
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys

from app.compute import target_guard as tg


def test_没给_date_就不拦(monkeypatch):
    called = []
    monkeypatch.setattr(tg.engine, 'scalar', lambda *a, **k: called.append(1) or 0)
    assert tg.missing_target_reason(None, 'market_data.stock_daily', 'trade_date') is None
    assert tg.missing_target_reason('', 'market_data.stock_daily', 'trade_date') is None
    assert called == []                       # 不该白跑一次查询


def test_输入不够时给一句能归因的话(monkeypatch):
    monkeypatch.setattr(tg.engine, 'scalar', lambda sql, params=None, **k: 0)
    r = tg.missing_target_reason('2026-10-02', 'market_data.stock_daily', 'trade_date',
                                 min_rows=1000)
    assert r and '2026-10-02' in r and 'stock_daily' in r
    assert '不算成功' in r and '退回去算' in r      # 明说它不退回去


def test_输入齐备放行(monkeypatch):
    monkeypatch.setattr(tg.engine, 'scalar', lambda sql, params=None, **k: 5561)
    assert tg.missing_target_reason('2026-09-30', 'market_data.stock_daily',
                                    'trade_date', min_rows=1000) is None


def test_查询坏了要放行而不是变成第二个故障源(monkeypatch):
    """闸门自己挂掉时不许把作业拦死 —— 它只是提醒，不是新的失败源。"""
    def boom(sql, params=None, **k):
        raise RuntimeError('connection reset')
    monkeypatch.setattr(tg.engine, 'scalar', boom)
    assert tg.missing_target_reason('2026-09-30', 'market_data.stock_daily', 'trade_date') is None


def test_非法日期字符串不拦(monkeypatch):
    monkeypatch.setattr(tg.engine, 'scalar', lambda *a, **k: 0)
    assert tg.missing_target_reason('not-a-date', 'x.y', 'trade_date') is None


def test_十一个作业都接了闸门():
    """静态钉住接线：以后新增/重写作业忘了闸门，这条会红。"""
    import pathlib
    import re
    jobs = ['d1_indicators', 'd2_scores', 'd3_sector_trend', 'd4_ignition', 'd5_score_trend',
            'd6_breadth', 'd7_vol_double', 'qd1_sentiment', 'qd2_ladder', 'qd3_sector']
    for name in jobs:
        t = (pathlib.Path('ingest/jobs') / (name + '.py')).read_text(encoding='utf-8')
        assert 'target_guard' in t, name
        assert re.search(r'missing_target_reason\(\s*args\.date', t), name
        # 拦下来必须是 rc=1，不许 print 完继续跑
        assert 'return 1' in t.split('missing_target_reason')[1][:400], name


def test_真跑一场没有数据的日期_必须非零退出():
    """端到端：d6 是最快的一步，拿它验"rc=1 + 那句原因"。"""
    p = subprocess.run([sys.executable, '-m', 'ingest.jobs.d6_breadth',
                        '--date', '2026-10-02', '--recent', '1'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace',
                       timeout=300)
    out = (p.stdout or '') + (p.stderr or '')
    assert p.returncode == 1, out[-400:]
    assert '2026-10-02' in out and '没有数' in out
