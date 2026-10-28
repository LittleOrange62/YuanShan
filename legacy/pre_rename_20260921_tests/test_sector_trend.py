# -*- coding: utf-8 -*-
"""
板块热度的多窗口趋势：`fct.sector_heat_daily` + `fct.sector_trend`。

这套东西最容易出的错是**符号反了**。斜率的正负号是这些列全部的意义
（"向上为正、向下为负"），而 x 轴取反、`ORDER BY trade_date DESC` 忘了改回来
这类笔误不会报错，只会让整张榜的趋势列反过来 —— 页面照样好看，结论全错。
所以这里拿 numpy 独立重算一遍，并且专门断言"热度在涨的板块斜率为正"。
"""
from __future__ import annotations

import pathlib

import numpy as np
import pytest

from app.compute import sector_trend as st
from app.db import engine
from app.orchestrator import runner
from app.web import queries

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope='module')
def day():
    d = engine.scalar('SELECT max(trade_date) FROM fct.sector_trend')
    if d is None:
        pytest.skip('fct.sector_trend 是空的，先跑 d3_sector_trend.py')
    return d


# ------------------------------------------------------------------ 配置驱动


def test_windows_come_from_config():
    wins = st.windows()
    assert wins == sorted(set(wins)), '窗口要去重且有序'
    assert all(isinstance(w, int) and 1 <= w <= 250 for w in wins)
    # 用户点名要的七个窗口
    for w in (3, 5, 7, 9, 11, 15, 21):
        assert w in wins, f'缺 {w} 日趋势'
    assert 1 in wins, '缺日趋势分值（win=1）'


def test_windows_rejects_garbage(monkeypatch):
    """窗口值会被拼进 SQL 的窗口帧，非法值必须挡在外面。"""
    monkeypatch.setattr(st.reg_repo, 'setting',
                        lambda *a, **k: [3, '5', 'DROP TABLE', -1, 0, 999, 7.9])
    assert st.windows() == [3, 5, 7]


# ------------------------------------------------------------------ 数值正确


def test_slope_matches_numpy_polyfit(day):
    """独立复核：同一段热度序列，numpy 算的斜率必须和库里一致。"""
    wins = [w for w in st.windows() if w > 1]
    rows = engine.query(
        """
        SELECT sector_code FROM fct.sector_trend
         WHERE trade_date = %s AND win = %s AND n_points >= %s
         ORDER BY abs(slope) DESC LIMIT 3
        """, (day, max(wins), max(wins)))
    assert rows, '没有点数足够的板块可对照'
    checked = 0
    for r in rows:
        hist = engine.query(
            """
            SELECT heat FROM fct.sector_heat_daily
             WHERE sector_code = %s AND trade_date <= %s
             ORDER BY trade_date DESC LIMIT %s
            """, (r['sector_code'], day, max(wins)))
        ys = [float(x['heat']) for x in reversed(hist)]
        for w in wins:
            if len(ys) < w:
                continue
            mine = float(np.polyfit(range(w), ys[-w:], 1)[0])
            got = engine.scalar(
                'SELECT slope FROM fct.sector_trend WHERE sector_code = %s '
                'AND trade_date = %s AND win = %s', (r['sector_code'], day, w))
            assert got is not None
            assert abs(mine - float(got)) < 0.01, (
                f"{r['sector_code']} win={w}：库里 {got}，polyfit {mine:.4f}")
            checked += 1
    assert checked >= len(wins), '对照的组合太少，这条测试没起作用'


def test_daily_trend_equals_the_heat_difference(day):
    """win=1 就是"日趋势分值"＝今日热度 − 昨日热度。"""
    rows = engine.query(
        """
        WITH h AS (
            SELECT sector_code, trade_date, heat,
                   lag(heat) OVER (PARTITION BY sector_code
                                   ORDER BY trade_date) AS prev
              FROM fct.sector_heat_daily
        )
        SELECT h.sector_code, h.heat - h.prev AS diff, t.slope
          FROM h JOIN fct.sector_trend t
               ON t.sector_code = h.sector_code
              AND t.trade_date = h.trade_date AND t.win = 1
         WHERE h.trade_date = %s AND h.prev IS NOT NULL
         LIMIT 200
        """, (day,))
    assert rows, '当天没有可比对的行'
    for r in rows:
        assert abs(float(r['diff']) - float(r['slope'])) < 0.01


def test_sign_is_up_positive_down_negative(day):
    """涨的板块斜率为正、跌的为负 —— 这一列全部的意义就是这个符号。"""
    wins = [w for w in st.windows() if w > 1]
    w = min(wins)
    rows = engine.query(
        """
        WITH s AS (
            SELECT t.sector_code, t.slope,
                   h0.heat AS now_heat,
                   (SELECT heat FROM fct.sector_heat_daily x
                     WHERE x.sector_code = t.sector_code
                       AND x.trade_date <= %(d)s
                     ORDER BY x.trade_date DESC OFFSET %(off)s LIMIT 1) AS old_heat
              FROM fct.sector_trend t
              JOIN fct.sector_heat_daily h0
                   ON h0.sector_code = t.sector_code AND h0.trade_date = %(d)s
             WHERE t.trade_date = %(d)s AND t.win = %(w)s
               AND t.n_points >= %(w)s
        )
        SELECT * FROM s WHERE old_heat IS NOT NULL
           AND abs(now_heat - old_heat) > 5
         LIMIT 100
        """, {'d': day, 'w': w, 'off': w - 1})
    assert rows, '没有变化足够大的板块可判符号'
    wrong = [r for r in rows
             if (float(r['now_heat']) > float(r['old_heat'])) !=
                (float(r['slope']) > 0)]
    # 斜率是全窗口拟合，首尾方向偶尔会和它不一致（中间反转），
    # 但**大方向反过来的不该超过少数**；全反了就是 x 轴取反了
    assert len(wrong) < len(rows) * 0.35, (
        f'{len(wrong)}/{len(rows)} 个板块的斜率符号和首尾方向相反，怀疑 x 轴取反')


def test_short_windows_are_recorded_not_padded(day):
    """点数不足不补齐、不置 0，只如实记 n_points。"""
    rows = engine.query(
        'SELECT win, n_points, slope FROM fct.sector_trend '
        'WHERE trade_date = %s', (day,))
    assert rows
    for r in rows:
        assert r['n_points'] is not None
        assert r['n_points'] >= 2, 'n_points 至少要有两个点才算得出斜率'
        assert r['slope'] is not None, '算不出来的行不该落库'


# ------------------------------------------------------------------ 页面


def test_board_exposes_every_configured_window():
    rows = queries.run('q_sector_heat', limit=5)
    assert rows, '板块热度榜是空的'
    cols = list(rows[0])
    for w in st.windows():
        label = '日趋势' if w == 1 else f'{w}日趋势'
        assert label in cols, f'榜单缺 {label} 这一列'
    assert cols.index('日趋势') < cols.index('3日趋势') < cols.index('21日趋势')


def test_board_daily_trend_comes_from_the_precomputed_table():
    """榜单上的日趋势必须和趋势表一致 —— 两处各算一遍迟早对不上。"""
    rows = queries.run('q_sector_heat', limit=10)
    tab = {r['sector_code']: r['slope'] for r in engine.query(
        'SELECT sector_code, slope FROM fct.sector_trend '
        'WHERE trade_date = (SELECT max(trade_date) FROM fct.sector_trend) '
        'AND win = 1')}
    hit = 0
    for r in rows:
        got, want = r['日趋势'], tab.get(r['sector_code'])
        if got is None or want is None:
            continue
        assert abs(float(got) - float(want)) < 0.02
        hit += 1
    assert hit >= 5, '比对到的行太少'


# ------------------------------------------------------------------ 定时任务


def test_job_is_declared_and_actually_runnable():
    """
    ★声明和实现必须同批：只在 config.job 里插一行，页面上点「运行」会
    直接 skipped（没有执行器），那等于这个功能不存在。
    """
    job = engine.query_one(
        "SELECT * FROM config.job WHERE job_key = 'calc_sector_trend'")
    assert job, 'config.job 里没有 calc_sector_trend'
    assert job['enabled']
    assert job['after_job'] == 'calc_scores', '必须排在评分之后'
    name = runner.job_runner(job)
    assert name == 'compute_sector_trend', '这个作业没有执行器'
    script = ROOT / runner.RUNNERS[name]['script']
    assert script.exists(), f'执行器指向的脚本不存在：{script}'


def test_fill_all_chain_runs_it_after_scores():
    """真正每天会跑到它的是「一键追平」那条链 —— 本系统没有调度器进程。"""
    src = (ROOT / 'scripts/ops/e1_fill_all.py').read_text(encoding='utf-8')
    assert "'sectortrend'" in src, 'e1_fill_all 没有这一步'
    assert src.index("('scores'") < src.index("('sectortrend'"), \
        '板块趋势必须排在评分之后，否则算的是昨天的热度'
