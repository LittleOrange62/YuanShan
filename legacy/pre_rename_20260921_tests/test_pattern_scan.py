# -*- coding: utf-8 -*-
"""
形态扫描（`app/compute/pattern.py`）的验收测试。

这两个扫描属于典型的"不报错但错得看不出来"：窗口偏一格、涨停判据漏掉 ST、
破线方向写反，页面上照样出一张像样的表，只是里面的票是另一批。所以这里全部
拿真库对答案 —— 手算一遍再和 SQL 的结果比，而不是断言函数被调用过。

另外钉住两条已经踩过的坑：
- 均线现算 vs `fct.indicator_value`：因子只算到昨天时读表会安静返回 0 行；
- 相关子查询打 CTE 会撞 statement timeout，所以留了耗时上限。
"""
from __future__ import annotations

import datetime as dt
import time

import pytest

from app.compute import pattern
from app.db import engine
from app.web import queries

# 全市场跑一次的耗时上限。实测 1.1s / 1.6s，给 4 倍余量：这条不是为了测性能，
# 是为了在有人把相关子查询写回 CTE 时立刻炸出来（那种写法直接撞 30s 超时）。
SLOW_SEC = 6.0


@pytest.fixture(scope='module')
def day():
    d = pattern.latest_day()
    assert d, 'md.stock_daily 是空的，这套测试没法跑'
    return d


# ---------------------------------------------------------------- 窗口与边界

def test_back_days_index_zero_is_the_base_day(day):
    """`back_days()[0]` 必须是基准日本身 —— 页面上的"前 N"就是按这个 0 起点数的。"""
    dates = pattern.back_days(day, 5)
    assert dates[0] == day
    assert dates == sorted(dates, reverse=True)
    assert len(set(dates)) == len(dates)


def test_back_days_are_real_trading_days(day):
    """取的是库里真有行情的日子，不能出现周末。"""
    for d in pattern.back_days(day, 10):
        assert d.weekday() < 5, f'{d} 是周末，说明窗口没走交易日'


def test_window_bounds_includes_both_ends(day):
    """
    "前 3 到前 7"＝ `dates[3] .. dates[7]`，两端都含，窗口里正好 5 个交易日。
    """
    dates = pattern.back_days(day, 8)
    lo, hi = pattern.window_bounds(day, 3, 7)
    assert hi == dates[3] and lo == dates[7]
    n = engine.scalar(
        'SELECT count(DISTINCT trade_date) FROM md.stock_daily '
        ' WHERE trade_date BETWEEN %s AND %s', (lo, hi))
    assert n == 5, f'前 3 到前 7 应该是 5 个交易日，实际 {n}'


def test_window_zero_includes_the_base_day(day):
    """N 填 0 就要把基准日本身圈进来。"""
    lo, hi = pattern.window_bounds(day, 0, 4)
    assert hi == day
    assert lo < hi


def test_window_bounds_swaps_reversed_input(day):
    """N/M 填反了自己正过来，不为此报错 —— 页面上两个输入框谁先填不该有讲究。"""
    assert pattern.window_bounds(day, 7, 3) == pattern.window_bounds(day, 3, 7)


def test_window_bounds_rejects_negative_and_too_far(day):
    with pytest.raises(pattern.PatternError):
        pattern.window_bounds(day, -1, 5)
    with pytest.raises(pattern.PatternError):
        pattern.window_bounds(day, 99999, 99999)


# ---------------------------------------------------------------- 白名单

@pytest.mark.parametrize('bad', ['ma7', 'close', "ma5; DROP TABLE md.stock_daily", ''])
def test_ma_whitelist(bad, day):
    """均线名要拼进 SQL，只有白名单能过 —— 这是这两个函数唯一的注入面。"""
    with pytest.raises(pattern.PatternError):
        pattern.box_break_scan(day, ma=bad)


@pytest.mark.parametrize('bad', ['high', 'close, low', 'ma5'])
def test_price_whitelist(bad, day):
    with pytest.raises(pattern.PatternError):
        pattern.box_break_scan(day, price=bad)


@pytest.mark.parametrize('bad', ['>', '<', '!=', 'IS NOT NULL OR 1=1 --'])
def test_op_whitelist(bad, day):
    with pytest.raises(pattern.PatternError):
        pattern.limit_streak_scan(day, op=bad)


def test_universe_whitelist(day):
    with pytest.raises(pattern.PatternError):
        pattern.limit_streak_scan(day, universe='everything')
    with pytest.raises(pattern.PatternError):
        pattern.box_break_scan(day, universe='everything')


# ---------------------------------------------------------------- 连板扫描

def test_streak_scan_runs_fast_and_has_all_columns(day):
    t0 = time.perf_counter()
    rows = pattern.limit_streak_scan(day, n_back=3, m_back=7, times=3)
    cost = time.perf_counter() - t0
    assert cost < SLOW_SEC, f'全市场连板扫描花了 {cost:.1f}s'
    if not rows:
        pytest.skip('这个窗口里没有涨停 3 次以上的票')
    need = {'代码', '名称', '涨停次数', '连板高度', '涨停日期', '最后涨停日',
            '收盘', '涨跌幅', '换手率', '操作分', '象限'}
    assert need <= set(rows[0]), need - set(rows[0])


def test_zt_days_matches_the_listed_dates(day):
    """
    「涨停次数」＝窗口内涨停**天数**（用户 2026-09-01 确认的口径），
    所以它必须等于「涨停日期」那一列里的日期个数 —— 不是连板段数、也不是板高。
    """
    rows = pattern.limit_streak_scan(day, n_back=0, m_back=9, times=2, limit=50)
    if not rows:
        pytest.skip('窗口里没有涨停 2 次以上的票')
    for r in rows:
        assert r['涨停次数'] == len(r['涨停日期'].split('、')), r
        # 板高不可能超过总天数，也至少是 1
        assert 1 <= r['连板高度'] <= r['涨停次数'], r


def test_streak_zt_days_matches_hand_count(day):
    """
    抽一只票，逐日回库手算窗口内的涨停天数，必须和扫描结果相等。

    手算这一遍用的是同一套判据（`limit_up` 优先、按板块兜底），如果 SQL 里的
    窗口边界或 pool 条件写偏，这里就会对不上。
    """
    rows = pattern.limit_streak_scan(day, n_back=0, m_back=9, times=2, limit=1)
    if not rows:
        pytest.skip('窗口里没有涨停 2 次以上的票')
    code = rows[0]['代码']
    lo, hi = pattern.window_bounds(day, 0, 9)
    hand = engine.scalar(
        """
        SELECT count(*) FROM md.stock_daily x
          JOIN ref.security s ON s.ts_code = x.ts_code
         WHERE x.ts_code = %(code)s
           AND x.trade_date BETWEEN %(lo)s AND %(hi)s
           AND (
             (x.limit_up IS NOT NULL AND x.close >= x.limit_up - 0.005)
             OR (x.limit_up IS NULL AND x.pre_close > 0 AND
                 (x.close - x.pre_close) / x.pre_close * 100 >=
                 CASE WHEN s.is_st THEN 4.8
                      WHEN x.ts_code LIKE '9%%' THEN 29.8
                      WHEN x.ts_code LIKE '30%%' OR x.ts_code LIKE '68%%' THEN 19.8
                      ELSE 9.8 END)
           )
        """, {'code': code, 'lo': lo, 'hi': hi})
    assert hand == rows[0]['涨停次数'], (code, hand, rows[0])


def test_op_eq_is_a_subset_of_ge(day):
    """`=` 的结果必须是 `>=` 的子集，且每行的次数正好等于阈值。"""
    ge = {r['代码'] for r in pattern.limit_streak_scan(
        day, n_back=0, m_back=9, times=2, op='>=', limit=2000)}
    eq = pattern.limit_streak_scan(
        day, n_back=0, m_back=9, times=2, op='=', limit=2000)
    if not ge:
        pytest.skip('窗口里没有涨停 2 次以上的票')
    assert {r['代码'] for r in eq} <= ge
    assert all(r['涨停次数'] == 2 for r in eq)


def test_op_le_excludes_the_high_streaks(day):
    """`<=` 不能把高于阈值的票带进来。"""
    rows = pattern.limit_streak_scan(
        day, n_back=0, m_back=9, times=2, op='<=', limit=2000)
    assert all(r['涨停次数'] <= 2 for r in rows)


def test_limit_up_judgement_covers_every_board(day):
    """
    涨停判据不能整类漏掉某个板块。

    用统一的 9.8% 阈值时**漏掉**的正是非 10% 的那几类（主板 ST ±5% /
    创业板科创板 20% / 北交所 30%），而"漏掉"在页面上表现为"这票明明涨停了
    却没筛出来"，是最难发现的一种错。所以这里按代码段分类，确认每一类都有
    样本命中。
    """
    rows = pattern.limit_streak_scan(day, n_back=0, m_back=19, times=1,
                                     limit=2000)
    if not rows:
        pytest.skip('近 20 个交易日一个涨停都没有，库不对')
    codes = [r['代码'] for r in rows]
    got = engine.query(
        """
        SELECT CASE WHEN s.is_st                       THEN 'st'
                    WHEN x.ts_code LIKE '9%%'          THEN 'bj'
                    WHEN x.ts_code LIKE '30%%'
                      OR x.ts_code LIKE '68%%'         THEN 'kc20'
                    ELSE 'main10' END                  AS cls,
               count(*) AS n
          FROM md.stock_daily x
          JOIN ref.security s ON s.ts_code = x.ts_code
         WHERE x.ts_code = ANY(%(codes)s)
         GROUP BY 1
        """, {'codes': codes})
    cls = {r['cls']: r['n'] for r in got}
    assert cls.get('main10'), '主板 10% 的涨停一个都没筛到'
    assert cls.get('kc20'), '创业板/科创板 20% 的涨停一个都没筛到（阈值写死 9.8 就会这样）'
    assert cls.get('st'), 'ST 的涨停一个都没筛到'


def test_limit_price_is_derived_and_its_error_stays_bounded():
    """
    ⚠️ **`md.stock_daily.limit_up` 是推导值，不是交易所公布值** ——
    `app/db/repo/quote.py` 的 `recompute_limits()` 用
    `pre_close × ref.board_limit_ratio(board, is_st)` 现算。

    2026-09-01 实测：这个推导对 **ST 票是错的**，近 20 个交易日每天有 6~21 行
    收盘价直接越过了算出来的涨跌停价（`*ST西旅 000610` 09-01 涨 10.07%，
    推导涨停价只给了 ±5%；`*ST萃华` 连续三天 −9.5% 左右）。成交价站在
    通达信 `ZTPrice` 那一边，也就是说 8 月 30 日那条"库里 ST ±5% 是对的、
    通达信快照不对"的结论**对 ST 这一半是反的**。

    这条测试**不修**那个缺陷（改涨停口径要先定数据源，得用户拍板），只把
    量级钉住：越界行占全市场 < 0.5%。修好之后越界行变 0 也照样通过；但要是
    有人把 `board_limit_ratio` 改坏、或者 `is_st` 大面积走样，这里会先炸。
    """
    row = engine.query_one(
        """
        WITH w AS (
            SELECT DISTINCT trade_date FROM md.stock_daily
             ORDER BY trade_date DESC LIMIT 20
        )
        SELECT count(*) AS n_all,
               count(*) FILTER (WHERE x.close > x.limit_up + 0.005
                                   OR x.close < x.limit_down - 0.005) AS n_bad
          FROM md.stock_daily x
         WHERE x.trade_date IN (SELECT trade_date FROM w)
           AND x.limit_up IS NOT NULL
        """)
    assert row['n_all'] > 10000, row
    ratio = row['n_bad'] / row['n_all'] * 100
    assert ratio < 0.5, f'越界行 {row["n_bad"]}/{row["n_all"]}＝{ratio:.2f}%，涨跌停价推导跑偏了'


# ---------------------------------------------------------------- 箱体扫描

def test_box_scan_runs_fast_and_has_all_columns(day):
    t0 = time.perf_counter()
    rows = pattern.box_break_scan(day, lookback=20, box_days=3, box_pct=15.0)
    cost = time.perf_counter() - t0
    assert cost < SLOW_SEC, f'全市场箱体扫描花了 {cost:.1f}s（相关子查询回来了？）'
    if not rows:
        pytest.skip('当日没有断板走箱体又破 5 日线的票')
    need = {'代码', '名称', '最后涨停日', '连板高度', '断板日', '箱体天数',
            '箱体上沿', '箱体下沿', '箱体振幅', '收盘', '最低', '均线',
            '破线幅度', '涨跌幅', '换手率', '操作分', '象限'}
    assert need <= set(rows[0]), need - set(rows[0])


def test_box_break_is_never_reversed(day):
    """
    破线幅度必须是负的：价在均线下方才叫破线，方向写反会筛出一批强势票。

    价与「均线」列的比较留 0.005 的余量 —— 那一列是 `round(ma_val, 2)`，
    刚好贴线的票（收盘 11.83 / 均线 11.834）四舍五入后会显示成相等，
    但真实关系仍是"在线下"，`破线幅度` 那一列的负号才是判据本身。
    """
    for ma in ('ma5', 'ma10'):
        for price in ('close', 'low'):
            rows = pattern.box_break_scan(day, ma=ma, price=price, limit=200)
            for r in rows:
                px = float(r['收盘'] if price == 'close' else r['最低'])
                assert px <= float(r['均线']) + 0.005, (ma, price, r)
                assert float(r['破线幅度']) <= 0, (ma, price, r)


def test_low_break_is_a_superset_of_close_break(day):
    """
    盘中最低破线的票必然包含收盘破线的票（收盘 ≥ 当日最低）。

    这条能同时钉住"price 白名单被拼错列"和"两个分支用了不同窗口"。
    """
    low = {r['代码'] for r in pattern.box_break_scan(day, price='low', limit=2000)}
    close = {r['代码'] for r in pattern.box_break_scan(day, price='close', limit=2000)}
    if not close:
        pytest.skip('当日没有收盘破线的票')
    assert close <= low, close - low


def test_box_amplitude_matches_hand_calculation(day):
    """
    抽一只票，按「断板日到基准日的最高/最低」手算箱体振幅，和结果对答案。

    这里专门盯住一个坑：箱体上下沿**不含**断板前那根涨停 —— 含进去的话振幅
    永远等于那根板的涨幅，判据就废了。
    """
    rows = pattern.box_break_scan(day, lookback=20, box_days=3, box_pct=15.0,
                                  limit=5)
    if not rows:
        pytest.skip('当日没有断板走箱体又破 5 日线的票')
    r = rows[0]
    hand = engine.query_one(
        """
        SELECT count(*) AS n, max(high) AS hi, min(low) AS lo
          FROM md.stock_daily
         WHERE ts_code = %(code)s
           AND trade_date BETWEEN %(bust)s AND %(d)s
        """, {'code': r['代码'], 'bust': r['断板日'], 'd': day})
    assert hand['n'] == r['箱体天数'], (r, hand)
    assert float(hand['hi']) == float(r['箱体上沿']), (r, hand)
    assert float(hand['lo']) == float(r['箱体下沿']), (r, hand)
    amp = (float(hand['hi']) - float(hand['lo'])) / float(hand['lo']) * 100
    assert abs(amp - float(r['箱体振幅'])) < 0.01, (r, amp)
    assert float(r['箱体振幅']) <= 15.0


def test_bust_date_is_the_day_after_the_last_limit_up(day):
    """断板日 ＝ 最后涨停日的**次一个交易日**，不是自然日 +1。"""
    rows = pattern.box_break_scan(day, limit=20)
    if not rows:
        pytest.skip('当日没有符合形态的票')
    for r in rows:
        nxt = engine.scalar(
            'SELECT min(trade_date) FROM md.stock_daily '
            ' WHERE ts_code = %s AND trade_date > %s', (r['代码'], r['最后涨停日']))
        assert nxt == r['断板日'], r
        assert r['断板日'] <= day


def test_no_limit_up_inside_the_box(day):
    """箱体期间不许再涨停 —— 再涨停就是二波，形态含义完全不同。"""
    rows = pattern.box_break_scan(day, limit=20)
    if not rows:
        pytest.skip('当日没有符合形态的票')
    for r in rows:
        n = engine.scalar(
            """
            SELECT count(*) FROM md.stock_daily x
             WHERE x.ts_code = %(code)s
               AND x.trade_date BETWEEN %(bust)s AND %(d)s
               AND x.limit_up IS NOT NULL AND x.close >= x.limit_up - 0.005
            """, {'code': r['代码'], 'bust': r['断板日'], 'd': day})
        assert n == 0, r


def test_box_days_threshold_is_respected(day):
    """箱体天数下限调高，结果必须收紧且每行都够天数。"""
    loose = pattern.box_break_scan(day, box_days=3, limit=2000)
    tight = pattern.box_break_scan(day, box_days=6, limit=2000)
    if not loose:
        pytest.skip('当日没有符合形态的票')
    assert {r['代码'] for r in tight} <= {r['代码'] for r in loose}
    assert all(r['箱体天数'] >= 6 for r in tight)


def test_inline_ma_equals_stored_indicator(day):
    """
    现算均线 ≡ `fct.indicator_value` 里的 ma5（同一个交易日上逐票比）。

    换成现算是因为因子链只跑到昨天时，读表的写法会安静返回 0 行，而 0 行在
    页面上跟"今天真没有这种票"长得一模一样。等价性必须钉住，否则这次替换
    就等于偷偷改了口径。
    """
    d = engine.scalar(
        "SELECT max(trade_date) FROM fct.indicator_value WHERE ind_key = 'ma5'")
    if not d:
        pytest.skip('fct.indicator_value 里没有 ma5')
    row = engine.query_one(
        """
        WITH px AS (
            SELECT x.ts_code, x.trade_date,
                   CASE WHEN count(*) OVER (PARTITION BY x.ts_code
                                            ORDER BY x.trade_date
                                            ROWS BETWEEN 4 PRECEDING
                                                     AND CURRENT ROW) = 5
                        THEN avg(x.close) OVER (PARTITION BY x.ts_code
                                                ORDER BY x.trade_date
                                                ROWS BETWEEN 4 PRECEDING
                                                         AND CURRENT ROW)
                   END AS mine
              FROM md.stock_daily x
             WHERE x.trade_date BETWEEN %(d)s - INTERVAL '20 days' AND %(d)s
        )
        SELECT count(*) AS n,
               max(abs(round(p.mine, 4) - round(i.value, 4))) AS gap
          FROM px p
          JOIN fct.indicator_value i ON i.ts_code = p.ts_code
                                    AND i.trade_date = p.trade_date
                                    AND i.ind_key = 'ma5'
         WHERE p.trade_date = %(d)s AND p.mine IS NOT NULL
        """, {'d': d})
    assert row['n'] > 1000, row
    assert float(row['gap'] or 0) < 0.005, row


def test_box_scan_works_when_factors_lag(day):
    """
    因子只算到昨天时，箱体扫描在**今天**也要能出结果（这就是现算的理由）。

    如果哪天有人把均线改回读 `fct.indicator_value`，这条会在因子滞后的那天
    立刻失败，而不是等用户看到一张空表来问。
    """
    fct_day = engine.scalar(
        "SELECT max(trade_date) FROM fct.indicator_value WHERE ind_key = 'ma5'")
    if not fct_day or fct_day >= day:
        pytest.skip('因子已经跑到最新交易日，这条构造不出滞后场景')
    rows = pattern.box_break_scan(day, price='low', box_pct=25.0, limit=50)
    assert rows, f'因子只到 {fct_day}，基准日 {day} 的箱体扫描不该是空的'


# ---------------------------------------------------------------- 注册与页面

@pytest.mark.parametrize('key', ['q_pattern_streak', 'q_pattern_box',
                                 'q_hot_sector_cross'])
def test_named_queries_registered(key):
    assert queries.get(key) is not None, f'{key} 没注册，页面拿不到数据'


def test_named_query_wrappers_pass_params_through(day):
    """页面走的是命名查询，参数必须真的传下去（默认值吃掉参数是老毛病）。"""
    a = queries.get('q_pattern_streak')(
        trade_date=day, n_back=0, m_back=9, times=2, op='=', limit=20)
    assert all(r['涨停次数'] == 2 for r in a)
    b = queries.get('q_pattern_box')(
        trade_date=day, box_days=6, ma='ma10', price='close', limit=20)
    assert all(r['箱体天数'] >= 6 for r in b)


def test_hot_sector_cross_always_returns_nine_rows(day):
    rows = queries.get('q_hot_sector_cross')(
        trade_date=day, top_n=666, sectors=3, top_k=3)
    assert len(rows) == 9, f'两榜前三交叉必须恒 9 行，实际 {len(rows)}'
    # 同一个板块和自己交叉的那行要写明白，不能留个空白让人以为没数据
    self_pairs = [r for r in rows if '不与自己交叉' in str(r.get('cross_names') or '')]
    for r in self_pairs:
        assert (r.get('cross_n') or 0) == 0, r


def test_screen_page_has_two_tabs():
    """筛选器现在是双 Tab：条件组合 + 形态扫描。"""
    from app.web.pages import screen
    node = screen.render()
    labels = [c.label for c in node.children]
    assert len(labels) == 2, labels
    assert any('形态' in s for s in labels), labels


def test_window_before_the_database_starts_fails_loudly(day):
    """
    基准日早于建库时间时要抛 `PatternError` 并说清"库里只有几天"。

    这种情况**不能**静悄悄返回空表：空表在页面上跟"今天真没有这种票"长得
    一模一样，用户会以为形态没命中，而实际上是日期填错了。
    """
    old = dt.date(1990, 1, 2)
    with pytest.raises(pattern.PatternError) as e:
        pattern.limit_streak_scan(old, n_back=0, m_back=5)
    assert '交易日' in str(e.value)
    # 箱体扫描走的是另一条路（不经过 window_bounds），它返回空表
    assert pattern.box_break_scan(old) == []
