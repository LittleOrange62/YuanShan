# -*- coding: utf-8 -*-
"""
筛选引擎与新因子口径的验收测试。

这两处都属于"不报错但错得看不出来"：
- 模板占位符替换错一个字符，SQL 照样能跑，只是筛出来的是另一批票；
- `chg10_avg` 如果按 9 天或按区间涨幅算，页面上依然是个合理的小数。
所以这里全部拿真库对答案，而不是断言函数被调用过。
"""
from __future__ import annotations

import re

import pytest

from app.compute import screener
from app.db import engine

# 每种条件给一组"库里必定有票命中"的参数
CASES = {
    'price_compare': {'on_date': None, 'field': 'close', 'op': '>', 'value': 5},
    'limit_up': {'days': 10, 'pct': 9.9},
    'intraday_limit_up': {'days': 10, 'pct': 9.9},
    'amp10_over': {'value': 3},
    'rising_closes': {'days': 5, 'min_days': 3},
}


@pytest.fixture(scope='module')
def latest():
    return engine.scalar('SELECT max(trade_date) FROM md.stock_daily')


def test_every_enabled_rule_has_a_case():
    """注册表里加了条件类型就必须补测试 —— 否则"能配"和"能跑"又会分叉。"""
    keys = {r['rule_key'] for r in screener.rules()}
    assert keys == set(CASES), f'注册表与测试用例不一致：{keys ^ set(CASES)}'


@pytest.mark.parametrize('key', sorted(CASES))
def test_rule_renders_and_runs(key, latest):
    params = dict(CASES[key])
    if params.get('on_date') is None and 'on_date' in params:
        params['on_date'] = latest
    conds = [{'rule_key': key, 'params': params}]
    sql, binds = screener.build(conds, universe='market', limit=5)
    # 占位符必须全部换成绑定参数，模板里不能有漏下的 `:name`
    assert not re.search(r'(?<!:):[a-z_]', sql), sql
    assert '%(d)s' in sql
    rows = screener.run(conds, universe='market', limit=5)
    assert isinstance(rows, list)
    assert binds['d'] == latest


def test_price_compare_adds_two_columns(latest):
    rows = screener.run(
        [{'rule_key': 'price_compare',
          'params': {'on_date': latest, 'field': 'close', 'op': '>', 'value': 5}}],
        universe='market', limit=3)
    assert rows, '全市场收盘价 > 5 元的票不该是 0 只'
    assert '条件1价' in rows[0] and '条件1到今涨跌幅' in rows[0]
    # 列名里不能出现 `%`：psycopg 会把它当占位符起手（2026-08-29 踩过）
    assert not any('%' in k for k in rows[0])


def test_same_rule_twice_gets_separate_binds(latest):
    """两条同类型条件必须各自绑定，否则后一条会覆盖前一条的参数。"""
    conds = [{'rule_key': 'limit_up', 'params': {'days': 5, 'pct': 9.9}},
             {'rule_key': 'limit_up', 'params': {'days': 20, 'pct': 5}}]
    sql, binds = screener.build(conds, universe='market')
    assert binds['c1_pct'] == 9.9 and binds['c2_pct'] == 5
    assert binds['c1_start'] > binds['c2_start']    # 5 日窗口起点更靠后
    assert '%(c1_pct)s' in sql and '%(c2_pct)s' in sql


@pytest.mark.parametrize('bad', [
    {'field': 'close; DROP TABLE md.stock_daily', 'op': '>', 'value': 1},
    {'field': 'close', 'op': 'OR 1=1 --', 'value': 1},
    {'field': 'pre_close', 'op': '>', 'value': 1},          # 不在 enum 里
])
def test_literal_params_are_enum_only(bad, latest):
    """列名和比较符是**文本替换**，只有枚举白名单能过 —— 这是唯一的注入面。"""
    with pytest.raises(screener.ScreenError):
        screener.build([{'rule_key': 'price_compare',
                         'params': {'on_date': latest, **bad}}])


def test_unknown_rule_and_bad_logic():
    with pytest.raises(screener.ScreenError):
        screener.build([{'rule_key': '不存在', 'params': {}}])
    with pytest.raises(screener.ScreenError):
        screener.build([], logic='xor')
    with pytest.raises(screener.ScreenError):
        screener.build([], universe='everything')


def test_placeholder_prefix_collision():
    """
    `:d` 和 `:days` 同时出现时不能互相咬掉。

    这条是回归测试：第一版按名字逐个 `str.replace`，`:d` 会把 `:days` 替换成
    `%(d)ss`，拼出一条语法错的 SQL（或者更糟：一条语法对但含义变了的）。
    """
    rule = {'rule_key': 'fake', 'name': '假条件',
            'param_schema': {'properties': {'days': {'type': 'integer'}}},
            'sql_template': 'x.trade_date = :d AND x.n = :days'}
    binds: dict = {}
    out = screener._render(1, rule, {'days': 7}, None, binds)   # noqa: SLF001
    assert out == '(x.trade_date = %(d)s AND x.n = %(c1_days)s)'
    assert binds == {'c1_days': 7}


def test_empty_conditions_returns_the_pool():
    """一条条件都不勾＝候选集本身，不是报错（沿用旧系统语义）。"""
    n_pool = engine.scalar(
        'SELECT count(DISTINCT ts_code) FROM config.watchlist_stock') or 0
    rows = screener.run([], universe='watchlist', limit=2000)
    assert len(rows) <= n_pool          # 停牌/退市的票当天没有日线行
    assert len(rows) > 0


def test_preset_roundtrip():
    conds = [{'rule_key': 'amp10_over', 'params': {'value': 4}}]
    pid = screener.save_preset('pytest 临时方案', conds, logic='or',
                               universe='market')
    try:
        got = next(p for p in screener.presets() if p['preset_id'] == pid)
        body = got['conditions']
        # 逻辑和候选集必须跟着一起存：同一组条件 and / or 是两个方案
        assert body['logic'] == 'or' and body['universe'] == 'market'
        assert body['conditions'] == conds
    finally:
        assert screener.delete_preset(pid) == 1


# ---------------------------------------------------------------- 新因子口径

def test_chg10_avg_is_a_daily_mean_not_an_interval_return():
    """
    `chg10_avg` ≡ 近 10 个交易日每日涨跌幅的算术平均。

    旧系统这一列是"10 日涨跌幅之和 ÷ 9"，比正确值大约小 1/10，而且和同表里的
    `chg10`（区间涨幅）长得一模一样。这条测试把两者钉开。
    """
    row = engine.query_one(
        """
        WITH d AS (SELECT max(trade_date) AS d FROM fct.indicator_value),
        pick AS (
            SELECT i.ts_code FROM fct.indicator_value i, d
             WHERE i.trade_date = d.d AND i.ind_key = 'chg10_avg'
             ORDER BY i.ts_code LIMIT 1
        ), x AS (
            SELECT s.pct_chg,
                   row_number() OVER (ORDER BY s.trade_date DESC) AS rn
              FROM md.stock_daily s, d, pick
             WHERE s.ts_code = pick.ts_code AND s.trade_date <= d.d
        )
        SELECT pick.ts_code,
               (SELECT round(avg(pct_chg), 4) FROM x WHERE rn <= 10) AS hand,
               (SELECT round(i.value, 4) FROM fct.indicator_value i, d
                 WHERE i.ts_code = pick.ts_code AND i.trade_date = d.d
                   AND i.ind_key = 'chg10_avg') AS stored
          FROM pick
        """)
    assert row and row['stored'] is not None
    assert abs(float(row['hand']) - float(row['stored'])) < 0.001, row


def test_amp_pred_is_within_a_plausible_range():
    """
    明日振幅是趋势外推，不是预言。它可以偏，但不能出现负数或者超过 A 股
    单日理论最大振幅（涨跌停各 20% → 40%）—— 那种数一眼就假。
    """
    row = engine.query_one(
        """
        SELECT count(*) AS n, min(value) AS lo, max(value) AS hi
          FROM fct.indicator_value
         WHERE ind_key = 'amp_pred'
           AND trade_date = (SELECT max(trade_date) FROM fct.indicator_value)
        """)
    assert row['n'] > 1000, row
    assert float(row['lo']) >= 0, row
    assert float(row['hi']) <= 40, row


def test_short_history_yields_null_not_zero():
    """
    不足窗口长度的票**没有值**，不是 0。

    `_roll` 用 `min_periods=n`，所以上市不满 20 天的票不会得到一个用 3 天
    凑出来的"20 日均值"。这一条是"除有效天数"那个老 bug 的反面。
    """
    row = engine.query_one(
        """
        WITH d AS (SELECT max(trade_date) AS d FROM fct.indicator_value),
        young AS (
            SELECT s.ts_code, count(*) AS n
              FROM md.stock_daily s, d
             WHERE s.trade_date <= d.d
             GROUP BY 1 HAVING count(*) BETWEEN 3 AND 15
             ORDER BY 2 LIMIT 1
        )
        SELECT y.ts_code, y.n,
               (SELECT count(*) FROM fct.indicator_value i, d
                 WHERE i.ts_code = y.ts_code AND i.trade_date = d.d
                   AND i.ind_key = 'amp20_avg') AS has_amp20
          FROM young y
        """)
    if row is None:
        pytest.skip('库里没有上市不满 15 个交易日的票')
    assert row['has_amp20'] == 0, row
