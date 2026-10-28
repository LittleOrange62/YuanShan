# -*- coding: utf-8 -*-
"""
全市场情绪读数 `fct.market_breadth`。

这张表**会被人肉眼看**，也会被「明日验证表」当判据，所以这里的重点不是"函数
返回值对不对"，而是**数值可不可信**：

1. 涨停家数用两条互不相干的口径各算一遍（涨停价 vs 涨幅阈值），差太多就报警 ——
   这是唯一能发现 `limit_up` 那一列整体错位的办法。
2. 恒等式（炸板 ≤ 触板、梯队之和 ≤ 涨停、涨+跌+平 = 参与统计）破了就是 SQL 写错。
3. 比率的 NULL 语义：没人触板那天炸板率必须是 NULL 而不是 0。
   0% 意思是"都封住了"，把两者混成一个值，验证表会把"没数据"判成"成立"。
4. 声明与实现同批：config.job 里有 calc_breadth，就必须真有执行器和脚本。
"""
from __future__ import annotations

import pathlib

import pytest

from app.compute import breadth
from app.db import engine
from app.orchestrator import runner

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope='module')
def day():
    d = engine.scalar('SELECT max(trade_date) FROM fct.market_breadth')
    if d is None:
        pytest.skip('fct.market_breadth 是空的，先跑 d6_breadth.py')
    return d


@pytest.fixture(scope='module')
def row(day):
    return breadth.on(day)


# ------------------------------------------------------------------ 数值可信


def test_limit_up_count_agrees_with_an_independent_yardstick(day, row):
    """
    涨停家数换一条口径重算：不用 `limit_up` 价格，改用涨幅接近板块上限。

    两条路的原始输入不同（价格列 vs 涨跌幅列），所以量级一致才说明
    `limit_up` 没有整列错位。允许有差：ST 的 5% 在涨幅口径下算不出来。
    """
    plain = engine.scalar(
        """
        SELECT count(*) FROM md.stock_daily
         WHERE trade_date = %(d)s AND pct_chg IS NOT NULL
           AND ((starts_with(ts_code, '68') OR starts_with(ts_code, '30'))
                    AND pct_chg >= 19.5
                OR starts_with(ts_code, '8') AND pct_chg >= 29.5
                OR starts_with(ts_code, '4') AND pct_chg >= 29.5
                OR pct_chg >= 9.8 AND pct_chg < 12)
        """, {'d': day})
    got = row['limit_up_count']
    assert got > 0, f'{day} 一家涨停都没有，先查 limit_up 这一列'
    assert abs(got - plain) <= max(5, 0.2 * max(got, plain)), (
        f'涨停家数两条口径差太多：涨停价判 {got} 家 / 涨幅阈值判 {plain} 家')


def test_identities_that_the_definition_forces(row):
    assert row['broken_count'] <= row['touched_count'], '炸板不可能多于触板'
    assert row['limit_up_count'] <= row['touched_count'], \
        '收盘封住的一定盘中摸到过涨停价'
    ladder = (row['board2_count'] + row['board3_count']
              + row['board4plus_count'])
    assert ladder <= row['limit_up_count'], '连板梯队之和不可能超过涨停家数'


def test_up_down_flat_add_up_to_the_stated_universe(day, row):
    """`stocks` 必须如实等于参与统计的票数 —— 分母被悄悄放大最难发现。"""
    flat = engine.scalar(
        'SELECT count(*) FROM md.stock_daily '
        'WHERE trade_date = %s AND pct_chg = 0', (day,))
    assert row['up_count'] + row['down_count'] + flat == row['stocks']
    total = engine.scalar(
        'SELECT count(*) FROM md.stock_daily WHERE trade_date = %s', (day,))
    assert row['stocks'] <= total, 'pct_chg 为空的行应当被排除，不该多出来'


def test_readings_are_within_common_sense(day):
    """
    量级体检。**故意宽**：抓的是"口径错了"，不是"差几家"。
    收窄到历史分位会在千股跌停那种极端行情当天全线误报。
    """
    rows = engine.query(
        'SELECT * FROM fct.market_breadth ORDER BY trade_date DESC LIMIT 60')
    assert rows, 'fct.market_breadth 是空的'
    for r in rows:
        d = r['trade_date']
        assert 0 <= r['limit_up_count'] <= 800, f'{d} 涨停 {r["limit_up_count"]} 家'
        assert 0 <= r['limit_down_count'] <= 800, f'{d} 跌停家数离谱'
        # A 股历史最高连板 20 出头，超过就是连板序列算错（多半是 rn 方向反了）
        assert 0 <= r['highest_board'] <= 20, f'{d} 最高连板 {r["highest_board"]}'
        assert -11 <= float(r['median_pct']) <= 11, \
            f'{d} 中位涨幅 {r["median_pct"]}% 落在涨跌停幅之外'
        for k in ('broken_rate', 'promotion_rate', 'red_rate', 'up_ratio'):
            if r[k] is not None:
                assert 0 <= float(r[k]) <= 100, f'{d} {k} = {r[k]} 不是百分比'


def test_prev_limit_up_uses_the_previous_day_with_data(day, row):
    """
    昨涨停的分母取"上一个有数据的交易日"，不是 `d - 1 day`。
    取错的表现是周一那天分母为 0（周日没有行情），晋级率整列变 NULL。
    """
    prev = engine.scalar(
        'SELECT max(trade_date) FROM md.stock_daily WHERE trade_date < %s',
        (day,))
    want = engine.scalar(
        'SELECT count(*) FROM md.stock_daily WHERE trade_date = %s '
        'AND limit_up IS NOT NULL AND close >= limit_up - %s',
        (prev, breadth.EPS_YUAN))
    assert row['prev_limit_up'] == want
    assert row['prev_limit_up'] > 0, '昨涨停家数为 0，多半是分母取到了非交易日'


def test_rates_are_null_not_zero_when_the_denominator_is_zero():
    """
    分母为 0 时比率必须是 NULL。0% 的意思是"都封住了/一个都没晋级"，
    和"今天没人摸涨停"是两回事，混成同一个值验证表会把"没数据"判成"成立"。
    """
    src = (ROOT / 'app/compute/breadth.py').read_text(encoding='utf-8')
    for col in ('c.touched_count', 'c.prev_limit_up', 'c.stocks'):
        assert f'NULLIF({col}, 0)' in src, f'{col} 当分母时没有用 NULLIF 兜住'
    bad = engine.query(
        """
        SELECT trade_date FROM fct.market_breadth
         WHERE touched_count = 0 AND broken_rate IS NOT NULL
            OR prev_limit_up = 0 AND promotion_rate IS NOT NULL
        """)
    assert not bad, f'分母为 0 却算出了比率：{[r["trade_date"] for r in bad]}'


def test_compute_and_stored_row_agree(day, row):
    """现算和库里必须一致 —— 落库预算的意义就是"页面不再自己算一遍"。"""
    live = breadth.compute(day)
    for k in ('stocks', 'limit_up_count', 'limit_down_count', 'touched_count',
              'broken_count', 'highest_board', 'prev_limit_up'):
        assert live[k] == row[k], f'{k}：现算 {live[k]} / 库里 {row[k]}'


# ------------------------------------------------------------------ 口径说明


def test_explain_covers_every_stored_reading():
    """页面说明从 EXPLAIN 读。改了算法不改这里，说明就会悄悄过期。"""
    keys = ' '.join(t['key'] for t in breadth.EXPLAIN['terms'])
    for col in ('limit_up_count', 'limit_down_count', 'touched_count',
                'broken_count', 'broken_rate', 'highest_board',
                'board2_count', 'promotion_rate', 'red_rate', 'median_pct',
                'up_ratio'):
        assert col in keys, f'EXPLAIN 里没有 {col} 的口径'
    assert breadth.EXPLAIN['caveat'], '必须写明 pct_chg 为空的行怎么处理'


# ------------------------------------------------------------------ 声明=实现


def test_job_is_declared_and_actually_runnable():
    job = engine.query_one(
        "SELECT * FROM config.job WHERE job_key = 'calc_breadth'")
    assert job, 'config.job 里没有 calc_breadth'
    assert job['enabled']
    # 只读 md.stock_daily：不该给它挂评分依赖，评分挂了不能连情绪读数一起丢
    assert job['after_job'] is None, '情绪读数不依赖任何计算作业'
    assert 'md.stock_daily' in (job['ready_on'] or []), '应当由日线就绪触发'
    name = runner.job_runner(job)
    assert name == 'compute_breadth', '这个作业没有执行器'
    script = ROOT / runner.RUNNERS[name]['script']
    assert script.exists(), f'执行器指向的脚本不存在：{script}'


def test_fill_all_runs_it_right_after_daily():
    """
    真正每天会跑到它的是「一键追平」那条链（本系统没有调度器进程）。
    位置有讲究：紧跟日线，排在评分之前 —— 评分那段挂了不该连它一起丢。
    """
    src = (ROOT / 'scripts/ops/e1_fill_all.py').read_text(encoding='utf-8')
    assert "'breadth'" in src, 'e1_fill_all 没有这一步'
    assert src.index("('daily'") < src.index("('breadth'") < src.index("('scores'"), \
        '情绪读数应当排在日线之后、评分之前'
    assert 'd6_breadth.py' in src, 'e1_fill_all 里没有指向执行脚本'
