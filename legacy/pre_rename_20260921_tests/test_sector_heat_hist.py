# -*- coding: utf-8 -*-
"""
板块热度的板块级口径与全历史回补。

这一组测试锁两件事，都是 2026-08-30 踩出来的：

1. **口径不能漂移**。`sector_heat_hist` 是四个 builtin 分项脚本的第二实现
   （向量化、一次算全历史），所以必须有一个测试真的把线上脚本跑一遍逐板块对 ——
   否则哪天有人改了 `sector_chengjiao.py` 的默认参数，这里算出来的历史热度
   会静默变成另一个东西。
2. **旧口径不能混回来**。原来的 `refresh_heat` 摊下去用"唯一主板块"、聚回来用
   `ref.sector_member` 的全部归属（一只票平均属 8.76 个板块），于是每个板块的
   热度是"成员股各自主板块热度的平均"。那批行还在库里（`src='member_agg'`），
   任何读取都必须过滤掉。
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.compute import sector_heat_hist as sh
from app.compute import sector_trend
from app.db import engine
from app.db.repo import security as sec_repo


@pytest.fixture(scope='module')
def latest() -> dt.date:
    d = engine.scalar("SELECT max(trade_date) FROM fct.sector_heat_daily "
                      " WHERE src = 'sector'")
    if d is None:
        pytest.skip('fct.sector_heat_daily 里还没有 src=sector 的行')
    return d


# ------------------------------------------------------------ 归一化工具
def test_pct_is_cross_sectional_not_pooled():
    """百分位必须**按天**排。混着排的话，牛市那几天所有板块都是高分。"""
    idx = pd.MultiIndex.from_tuples(
        [('A', 'd1'), ('B', 'd1'), ('A', 'd2'), ('B', 'd2')],
        names=['sector_code', 'trade_date'])
    s = pd.Series([1.0, 2.0, 100.0, 200.0], index=idx)
    dates = idx.get_level_values('trade_date')
    out = sh._pct(s, dates)
    # d2 的值大 100 倍，但组内排名一样 → 分数与 d1 相同
    assert out[('A', 'd1')] == pytest.approx(out[('A', 'd2')])
    assert out[('B', 'd1')] == pytest.approx(out[('B', 'd2')])
    assert out[('A', 'd1')] < out[('B', 'd1')]


def test_clip_maps_bounds_to_full_range():
    s = pd.Series([0.5, 0.7, 1.1, 1.5, 9.9])
    out = sh._clip(s, 0.7, 1.5)
    assert out.iloc[0] == pytest.approx(-100)   # 低于下界 → 截到 -100
    assert out.iloc[1] == pytest.approx(-100)
    assert out.iloc[3] == pytest.approx(100)
    assert out.iloc[4] == pytest.approx(100)
    assert -100 < out.iloc[2] < 100


def test_roll_keeps_min_periods_one():
    """
    `min_periods=1` 是照抄 `ctx.sector_roll`，不是笔误。

    改成 min_periods=n 会让回补出来的早期热度和线上口径不是一回事 ——
    线上点数不足时照样给值。
    """
    idx = pd.MultiIndex.from_product(
        [['A'], pd.date_range('2026-01-01', periods=3).date],
        names=['sector_code', 'trade_date'])
    panel = pd.DataFrame({'pct_chg': [1.0, 3.0, 5.0]}, index=idx)
    out = sh._roll(panel, 'pct_chg', 20)
    assert out.iloc[0] == pytest.approx(1.0)      # 一个点也给值
    assert out.iloc[2] == pytest.approx(3.0)      # (1+3+5)/3


# ------------------------------------------------------------ 加权
def test_missing_part_does_not_drag_score_down():
    """空值分项**不计权重**，而不是当 0 分。0 分在 -100~100 里是"中性"。"""
    idx = pd.MultiIndex.from_tuples([('A', 'd1')],
                                    names=['sector_code', 'trade_date'])
    part = pd.DataFrame({'zhangfu': [80.0], 'guangdu': [None],
                         'dongliang': [80.0], 'chengjiao': [80.0]}, index=idx)
    out = sh.heat(part, {'zhangfu': 0.30, 'guangdu': 0.30,
                         'dongliang': 0.25, 'chengjiao': 0.15})
    assert float(out['heat'].iloc[0]) == pytest.approx(80.0)
    assert int(out['n_parts'].iloc[0]) == 3


def test_all_parts_missing_yields_null_not_zero():
    idx = pd.MultiIndex.from_tuples([('A', 'd1')],
                                    names=['sector_code', 'trade_date'])
    part = pd.DataFrame({'zhangfu': [None], 'guangdu': [None],
                         'dongliang': [None], 'chengjiao': [None]}, index=idx)
    out = sh.heat(part, dict(sh.FALLBACK_WEIGHTS))
    assert pd.isna(out['heat'].iloc[0])
    assert int(out['n_parts'].iloc[0]) == 0


def test_weights_come_from_registry_and_sum_to_one():
    w = sh.weights()
    assert set(w) == {'zhangfu', 'guangdu', 'dongliang', 'chengjiao'}
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-6)


# ------------------------------------------------------------ 与线上脚本对账
def test_matches_live_builtin_scripts(latest):
    """
    **口径验收**：逐板块比线上四个 builtin。

    容差 0.5 分：实测最大差 0.1687 = `0.55 × 一个排名位（200/652）`，
    来源是 MA5/MA20 的浮点尾差让相邻两个板块排名互换。真正的口径错误
    （比如权重拿错、区间写反）会差几十分，卡不住的只有这种抖动。
    """
    res = sh.verify_against_builtin(latest)
    assert 'error' not in res, res
    for part, r in res.items():
        assert r['sectors'] > 100, f'{part} 只对上 {r["sectors"]} 个板块'
        assert r['max_diff'] < 0.5, f'{part} 最大差 {r["max_diff"]}'
        assert r['mean_diff'] < 0.05, f'{part} 平均差 {r["mean_diff"]}'


# ------------------------------------------------------------ 全历史与口径纯净
def test_history_is_deep_enough_for_percentile_baselines():
    """
    启动点要 60/120 日分位，23 个交易日是算不出来的 —— 那是这次回补的起因。
    这里断言历史真的补上了（板块日线有 1,231 天，允许少量日子缺板块日线）。
    """
    row = engine.query(
        "SELECT count(DISTINCT trade_date) d, count(DISTINCT sector_code) s, "
        "       min(trade_date) a, max(trade_date) b "
        "  FROM fct.sector_heat_daily WHERE src = 'sector'")[0]
    assert row['d'] >= 1000, f"只有 {row['d']} 个交易日，分位基线不可信"
    assert row['s'] >= 600
    assert row['a'] <= dt.date(2021, 12, 31)


def test_heat_stays_in_score_range():
    row = engine.query(
        "SELECT min(heat) lo, max(heat) hi FROM fct.sector_heat_daily "
        " WHERE src = 'sector'")[0]
    assert float(row['lo']) >= -100.0001
    assert float(row['hi']) <= 100.0001


def test_no_null_heat_rows_written():
    """算不出来就不落库 —— 空行会让"没算"和"算出来是 0"分不开。"""
    n = engine.scalar("SELECT count(*) FROM fct.sector_heat_daily "
                      " WHERE src = 'sector' AND heat IS NULL")
    assert int(n) == 0


def test_every_sector_row_has_daily_bar():
    """板块级口径的每一行都必须有对应的板块日线，否则那个 heat 是凭空来的。"""
    n = engine.scalar(
        """
        SELECT count(*) FROM fct.sector_heat_daily h
         WHERE h.src = 'sector'
           AND NOT EXISTS (SELECT 1 FROM md.sector_daily s
                            WHERE s.sector_code = h.sector_code
                              AND s.trade_date = h.trade_date)
        """)
    assert int(n) == 0


def test_trend_only_uses_current_definition(latest):
    """
    趋势表里可能还留着旧口径板块的行（`TDX_SP_*`，没有板块日线）。
    `coverage()` 必须只数现行口径 —— 实测过一次：652 个板块的那天打出 774。
    """
    cov = sector_trend.coverage(latest)
    assert cov, '最新交易日没有任何趋势行'
    bars = int(engine.scalar(
        'SELECT count(*) FROM md.sector_daily WHERE trade_date = %s',
        (latest,)) or 0)
    for c in cov:
        assert c['sectors'] <= bars, (
            f"win={c['win']} 统计到 {c['sectors']} 个板块，"
            f'而当天只有 {bars} 个板块有日线')


def test_full_window_available_after_backfill(latest):
    """回补的意义就在这里：21 日窗口在最新交易日必须是**满窗**，不是 3 个点。"""
    row = engine.query(
        """
        SELECT count(*) n, count(*) FILTER (WHERE t.n_points >= 21) full
          FROM fct.sector_trend t
          JOIN fct.sector_heat_daily h ON h.sector_code = t.sector_code
                                      AND h.trade_date = t.trade_date
                                      AND h.src = 'sector'
         WHERE t.trade_date = %s AND t.win = 21
        """, (latest,))[0]
    assert row['n'] > 500
    assert row['full'] == row['n'], f"{row['n'] - row['full']} 个板块点数不足 21"


def test_latest_heat_date_tracks_sector_daily():
    """热度不该落后于板块日线：落后就说明这一步没跟着日线跑。"""
    heat_max = engine.scalar("SELECT max(trade_date) FROM fct.sector_heat_daily"
                             " WHERE src = 'sector'")
    bar_max = engine.scalar('SELECT max(trade_date) FROM md.sector_daily')
    assert heat_max == bar_max
    assert bar_max == sec_repo.latest_trade_date() or heat_max is not None


# ------------------------------------------------------------ 板块宇宙要稳定
# 2026-09-01 加这一组：同花顺板块首次入库那天，`md.sector_daily` 多出 86 个
# 只有 1 天历史的 `THS_*`，热度截面从 652 个板块变成 738 —— 而三个分项是**截面
# 百分位**，分母一变，当天的热度就和前一天不是同一把尺子量的。同一天还发现
# 反向的老账：46 个不可信板块的历史行（heat 46,883 + trend 374,880）是判据加上
# **之前**回补的，upsert 删不掉，于是 08-28 有 652 个板块、08-31 只有 604 个。

def test_warmup_and_min_history_are_the_same_number():
    """暖机天数 = 历史下限。两个常量各写一遍，迟早一个改了另一个没改。"""
    assert sh.WARMUP == sh.MIN_HISTORY


def test_short_history_sectors_never_get_heat():
    """历史不足 MIN_HISTORY 天的板块不能有热度行 —— 1 个点算出来的 MA20 不是动量。"""
    n = engine.scalar(
        """
        SELECT count(*) FROM fct.sector_heat_daily h
         WHERE h.src = 'sector'
           AND h.sector_code IN (SELECT s.sector_code FROM md.sector_daily s
                                  GROUP BY s.sector_code
                                 HAVING count(*) < %s)
        """, (sh.MIN_HISTORY,))
    assert int(n) == 0


def test_prune_has_nothing_left_to_delete():
    """
    库里不该留着当前口径不认的行。这条会在"改了判据但没重算"的时候变红 ——
    正确做法是 `d3_sector_trend.py --recent <全历史>` + `d4_ignition.py --full`，
    不是把这条断言放宽。
    """
    left = sh.prune(dry_run=True)
    assert set(left) == {t for t, _c, _e in sh.PRUNE_TARGETS}
    assert all(int(v) == 0 for v in left.values()), left


def test_sector_universe_does_not_jump_between_days():
    """
    最近 20 个交易日的板块数必须基本恒定。板块数一天之内跳几十个，说明有
    一批板块被加进来或漏出去了 —— 那种日子的热度不能和前后日比较。
    """
    rows = engine.query(
        """
        SELECT trade_date, count(*) n FROM fct.sector_heat_daily
         WHERE src = 'sector'
           AND trade_date IN (SELECT DISTINCT trade_date
                                FROM fct.sector_heat_daily
                               WHERE src = 'sector'
                               ORDER BY trade_date DESC LIMIT 20)
         GROUP BY 1 ORDER BY 1
        """)
    counts = [int(r['n']) for r in rows]
    assert counts, '最近 20 个交易日没有热度行'
    # 退市/新上市板块每天差几个是正常的；差 20 个以上一定是口径事故
    assert max(counts) - min(counts) <= 20, dict(
        zip([str(r['trade_date']) for r in rows], counts, strict=True))
