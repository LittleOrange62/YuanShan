# -*- coding: utf-8 -*-
"""
自选组聚合（加权分 / 趋势分数 / 组内振幅）的验收测试。

这些断言都跑在**真库**上，不搭假数据：这三个数的口径问题（分母该不该算
没评分的票、窗口该按交易日还是自然日）只有在真实的"有缺口的数据"上才暴露，
造一份齐整的假数据反而会把 bug 盖住。

库里没有自选组或没跑过评分时整体跳过，不让环境问题伪装成失败。
"""
from __future__ import annotations

import pytest

from app.db import engine
from app.web import queries as q

WIN = 20
MIN_N = 10


@pytest.fixture(scope='module')
def latest():
    d = engine.scalar(
        "SELECT max(trade_date) FROM fct.score WHERE cap_key = 'oper_score'")
    if d is None:
        pytest.skip('fct.score 里没有 oper_score，先跑评分')
    if not engine.scalar('SELECT count(*) FROM config.watchlist_group'):
        pytest.skip('库里没有自选组')
    return d


@pytest.fixture(scope='module')
def heat(latest):
    rows = q.q_group_heat(trade_date=latest, win=WIN, min_n=MIN_N)
    assert rows, 'q_group_heat 一行都没有'
    return rows


def test_one_row_per_group(heat):
    n = engine.scalar('SELECT count(*) FROM config.watchlist_group g '
                      'WHERE EXISTS (SELECT 1 FROM config.watchlist_stock w '
                      '              WHERE w.group_id = g.group_id)')
    assert len(heat) == n
    names = [r['group_name'] for r in heat]
    assert len(set(names)) == len(names), '组名重复说明 GROUP BY 漏了维度'


def test_columns_present(heat):
    want = {'group_name', 'members', 'scored', 'w_oper', 'avg_oper',
            'trend_score', 'pred_oper', 'trend_days',
            'avg_chg10_bp', 'avg_chg20_bp',
            'avg_amp10', 'avg_amp20', 'avg_amp_pred',
            'up_ratio', 'avg_pct', 'lhb_count'}
    assert want <= set(heat[0])


def test_chg_bp_columns_are_hundred_times_the_percent(heat, latest):
    """
    `avg_chg10_bp` / `avg_chg20_bp` = 组内日均涨跌幅(%) 的组平均 **×100**
    （用户 2026-09-01 指定，单位正好是基点）。逐组用 Python 独立复算一遍 ——
    少乘或多乘一个 100 的话，页面上那一列会差两个数量级，而"−0.25"和"−25"
    在表里都长得像个正常数字，光看页面抓不出来。
    """
    checked = 0
    for r in heat[:8]:                       # 抽前 8 组够了，这条是量纲回归
        gid = engine.scalar('SELECT group_id FROM config.watchlist_group '
                            'WHERE name = %s', (r['group_name'],))
        for col, key in (('avg_chg10_bp', 'chg10_avg'),
                         ('avg_chg20_bp', 'chg20_avg')):
            want = engine.scalar(
                """
                SELECT round(avg(i.value) * 100, 1)
                  FROM config.watchlist_stock w
                  JOIN fct.indicator_value i ON i.ts_code = w.ts_code
                                            AND i.ind_key = %s
                                            AND i.trade_date = %s
                 WHERE w.group_id = %s
                """, (key, latest, gid))
            assert r[col] == want, (r['group_name'], col, r[col], want)
            checked += 1
    assert checked


def test_chg_bp_magnitude_is_sane(heat):
    """
    组内日均涨跌幅（bp）的量级体检：个股日均涨跌幅撑到 ±7% 已是极端，
    一组票平均下来 ±300bp（±3%/日）就该怀疑口径。用常识卡量级，不核具体值。
    """
    vals = [float(r['avg_chg10_bp']) for r in heat
            if r['avg_chg10_bp'] is not None]
    assert vals
    assert max(abs(v) for v in vals) < 300, max(vals, key=abs)


def test_group_chg10_map_matches_the_heat_card(heat, latest):
    """
    左栏组列表的排序键（`group_chg10_map`）和热度卡的 `组内10日均涨跌bp`
    必须是同一个数：一边 LEFT JOIN 后 `avg`、一边 INNER JOIN 后 `avg`，
    结果相同（`avg` 跳过 NULL）—— 但这是"现在相同"，两处 SQL 各写一份，
    没有断言就会慢慢走散，而那时左栏的组顺序和卡里的数字对不上。
    """
    m = q.group_chg10_map(trade_date=latest)
    checked = 0
    for r in heat:
        gid = engine.scalar('SELECT group_id FROM config.watchlist_group '
                            'WHERE name = %s', (r['group_name'],))
        if r['avg_chg10_bp'] is None:
            assert int(gid) not in m
            continue
        assert m[int(gid)] == r['avg_chg10_bp'], r['group_name']
        checked += 1
    assert checked


def test_column_order_puts_pred_first_and_meta_last(heat):
    """
    列序是**产品决定**，不是随手写的：`预测操作分` 紧跟组名放最前（默认排序键），
    `趋势天数 / 成员数 / 有分只数 / 重点关注` 这几列元信息压到最后。

    表格渲染直接吃 dict 的键顺序，所以这条断言等于在锁住页面上的列序 ——
    以后有人往 SELECT 中间插一列，这里会红。
    """
    cols = list(heat[0])
    assert cols[0] == 'group_name'
    assert cols[1] == 'pred_oper'
    assert cols[-4:] == ['trend_days', 'members', 'scored', 'is_focus']


def test_default_sort_is_pred_oper_desc_with_nulls_last(heat):
    vals = [r['pred_oper'] for r in heat]
    got = [float(v) for v in vals if v is not None]
    assert got == sorted(got, reverse=True), '默认没有按预测操作分降序'
    # 算不出预测分的组必须排在最后，不能用 0 顶到中间
    first_null = next((i for i, v in enumerate(vals) if v is None), len(vals))
    assert all(v is None for v in vals[first_null:])



def test_weighted_score_matches_hand_calc(heat, latest):
    """
    加权分＝`Σ(分×成交额) ÷ Σ成交额`，逐组用 Python 独立复算。

    **这条同时是"分母只算有分的票"的回归测试**：如果 SQL 里的分母写成
    `sum(amount_yuan)`（不带 FILTER），那么组里只要有一只票没评分，
    它的成交额也会进权重和，加权分就会被系统性压低 —— 这里的 Python
    实现只对"两边都有值"的票求和，两者一致才算对。
    """
    checked = 0
    for r in heat:
        gid = engine.scalar('SELECT group_id FROM config.watchlist_group '
                            'WHERE name = %s', (r['group_name'],))
        recs = engine.query(
            """
            SELECT o.value AS score, d.amount_yuan AS amt
              FROM config.watchlist_stock w
              LEFT JOIN fct.score o ON o.ts_code = w.ts_code
                                   AND o.trade_date = %s
                                   AND o.cap_key = 'oper_score'
              LEFT JOIN md.stock_daily d ON d.ts_code = w.ts_code
                                        AND d.trade_date = %s
             WHERE w.group_id = %s
            """, (latest, latest, gid))
        pairs = [(float(x['score']), float(x['amt'])) for x in recs
                 if x['score'] is not None and x['amt'] is not None]
        if not pairs:
            assert r['w_oper'] is None, '一只有效票都没有却给出了加权分'
            continue
        tot = sum(a for _, a in pairs)
        want = sum(s * a for s, a in pairs) / tot
        assert r['w_oper'] is not None
        assert abs(float(r['w_oper']) - want) < 0.06, r['group_name']
        checked += 1
    assert checked, '一个组都没复算到'


def test_weighted_and_equal_weight_are_different_numbers(heat):
    """
    加权分和等权分是两个不同的口径，页面上必须两列都给。

    只断言"至少有一个组两者不同"：全都相同意味着加权那一步没生效
    （比如权重列取空了，退化成等权），那张表也就白加了一列。
    """
    both = [(r['w_oper'], r['avg_oper']) for r in heat
            if r['w_oper'] is not None and r['avg_oper'] is not None]
    assert both
    assert any(abs(float(w) - float(e)) > 0.05 for w, e in both)


def test_trend_score_matches_least_squares(heat, latest):
    """趋势分数＝最小二乘斜率 × 10，用纯 Python 最小二乘复算同一条序列。"""
    tr = q.q_group_trend(trade_date=latest, win=WIN, top=99, min_n=MIN_N)
    if not tr:
        pytest.skip('没有足够长的组序列')
    ser: dict[str, list] = {}
    for r in tr:
        ser.setdefault(r['group_name'], []).append(
            (r['trade_date'], float(r['wscore'])))
    by_name = {r['group_name']: r for r in heat}
    checked = 0
    for name, pts in ser.items():
        pts.sort()
        ys = [v for _, v in pts]
        n = len(ys)
        xs = list(range(1, n + 1))
        mx = sum(xs) / n
        my = sum(ys) / n
        den = sum((x - mx) ** 2 for x in xs)
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / den
        a = my - b * mx
        got = by_name[name]
        # 序列在 SQL 里 round 到 1 位才返回，这里的复算吃的是圆整后的值，
        # 所以容差给到 0.2 分/10 日，不是要求逐位相同。
        assert abs(float(got['trend_score']) - b * 10) < 0.2, name
        assert abs(float(got['pred_oper']) - (a + b * (n + 1))) < 0.2, name
        checked += 1
    assert checked


def test_short_history_gets_null_trend_not_zero(latest):
    """
    窗口内不足 min_n 天的组，趋势三列必须留空。

    0 会被读成"横着走"，而实际是"算不出来"。把 min_n 顶到一个不可能满足的
    值来构造这种情形，比去库里找一个新建的组稳定。
    """
    rows = q.q_group_heat(trade_date=latest, win=WIN, min_n=10_000)
    assert rows
    assert all(r['trend_score'] is None for r in rows)
    assert all(r['pred_oper'] is None for r in rows)
    assert all(r['trend_days'] is None for r in rows)
    # 趋势算不出来不该连带把当日的分也弄没
    assert any(r['w_oper'] is not None for r in rows)


def test_trend_window_counts_trade_days_not_calendar_days(latest):
    """
    窗口是"最近 win 个有数的交易日"，所以每组的点数不超过 win。

    按自然日圈的话，长假一压 20 个自然日里只剩十来个交易日，斜率的量纲
    就跟着行情日历漂移，跨月不可比。
    """
    tr = q.q_group_trend(trade_date=latest, win=WIN, top=99, min_n=MIN_N)
    if not tr:
        pytest.skip('没有足够长的组序列')
    cnt: dict[str, int] = {}
    for r in tr:
        cnt[r['group_name']] = cnt.get(r['group_name'], 0) + 1
    assert cnt
    assert max(cnt.values()) <= WIN
    assert min(cnt.values()) >= MIN_N


def test_trend_returns_every_eligible_group_not_just_the_top(latest):
    """
    图里要有**所有**够长度的组，`top` 只决定默认画几条。

    以前是"只查前 6 个"，等于把第 7 名从图里删掉，想看得改 config 或改代码；
    现在全部返回、多余的用 `show=False` 收进图例。这条就是防止有人把
    `LIMIT` 加回去。
    """
    eligible = {r['group_name'] for r in q.q_group_heat(
        trade_date=latest, win=WIN, min_n=MIN_N) if r['trend_days'] is not None}
    if not eligible:
        pytest.skip('没有足够长的组序列')
    tr = q.q_group_trend(trade_date=latest, win=WIN, top=3, min_n=MIN_N)
    assert {r['group_name'] for r in tr} == eligible


def test_trend_show_flag_marks_exactly_top_n_by_slope(latest):
    """
    默认显示的是**斜率**最高的 `top` 个，不是当日分最高的那几个。

    这张图问的是"谁在起来"；当日分最高的组可能正在退潮，那件事上面那张表的
    `趋势分数` 列已经说了。搞混的话默认三条线会挑错组。
    """
    tr = q.q_group_trend(trade_date=latest, win=WIN, top=3, min_n=MIN_N)
    if not tr:
        pytest.skip('没有足够长的组序列')
    flag = {}
    for r in tr:
        flag.setdefault(r['group_name'], r['show'])
    shown = [g for g, v in flag.items() if v]
    assert len(shown) == min(3, len(flag))

    heat = {r['group_name']: r for r in q.q_group_heat(
        trade_date=latest, win=WIN, min_n=MIN_N)}
    by_slope = sorted(flag, key=lambda g: float(heat[g]['trend_score']),
                      reverse=True)
    assert set(shown) == set(by_slope[:len(shown)])


def test_trend_shown_series_come_first_for_legend_order(latest):
    tr = q.q_group_trend(trade_date=latest, win=WIN, top=3, min_n=MIN_N)
    if not tr:
        pytest.skip('没有足够长的组序列')
    order, flag = [], {}
    for r in tr:
        if r['group_name'] not in flag:
            flag[r['group_name']] = r['show']
            order.append(r['group_name'])
    flags = [flag[g] for g in order]
    assert flags == sorted(flags, reverse=True), '默认显示的组没排在图例最前'


def test_hidden_series_render_as_legendonly(latest):
    """
    收起来的线必须是 Plotly 的 `legendonly`（数据在图里、默认不画），
    不能是 `visible=False`（图例里点不出来）也不能干脆不给这条 trace。
    """
    from app.db.repo import registry as reg_repo
    from app.web import ui
    chart = {c['chart_key']: c for c in reg_repo.charts()}.get(
        'group_score_trend')
    if chart is None:
        pytest.skip('config.chart 里还没有 group_score_trend')
    assert chart['options'].get('visible_col') == 'show', \
        'config.chart 没把 visible_col 指向 show 列，图会 24 条全画'
    rows = q.q_group_trend(trade_date=latest, win=WIN,
                           top=chart['params'].get('top', 3), min_n=MIN_N)
    if not rows:
        pytest.skip('没有足够长的组序列')
    fig = ui.figure(chart, rows)
    vis = [t.visible for t in fig.data]
    assert len(fig.data) == len({r['group_name'] for r in rows})
    assert sum(1 for v in vis if v is True) == chart['params'].get('top', 3)
    assert all(v is True or v == 'legendonly' for v in vis)
    assert any(v == 'legendonly' for v in vis)



def test_trend_series_is_date_sorted_per_group(latest):
    tr = q.q_group_trend(trade_date=latest, win=WIN, top=6, min_n=MIN_N)
    if not tr:
        pytest.skip('没有足够长的组序列')
    ser: dict[str, list] = {}
    for r in tr:
        ser.setdefault(r['group_name'], []).append(r['trade_date'])
    for name, dates in ser.items():
        assert dates == sorted(dates), f'{name} 的点没按日期排，折线会画成锯齿'


def test_amplitude_columns_are_non_negative(heat):
    """振幅只有大小没有正负；出现负数说明取错了列。"""
    for r in heat:
        for k in ('avg_amp10', 'avg_amp20', 'avg_amp_pred'):
            if r[k] is not None:
                assert float(r[k]) >= 0, (r['group_name'], k, r[k])


def test_amplitude_magnitude_is_sane(heat):
    """
    组内日均振幅落在个位数量级：A 股涨跌停幅度 10~30，日均振幅超过 20
    只可能是把"区间涨幅"当振幅取了。用常识做量级体检，不核对具体值。
    """
    vals = [float(r['avg_amp10']) for r in heat if r['avg_amp10'] is not None]
    assert vals
    assert max(vals) < 20, max(vals)


def test_up_ratio_is_a_percentage(heat):
    for r in heat:
        if r['up_ratio'] is not None:
            assert 0 <= float(r['up_ratio']) <= 100


def test_scored_never_exceeds_members(heat):
    for r in heat:
        assert r['scored'] <= r['members']


def test_registered_under_expected_names():
    """
    两个查询都要在命名注册表里，否则 config 里配了卡也调不到。

    这条是"声明与实现同批交付"的机器化检查：`config.chart.group_score_trend`
    指向 `q_group_trend`，名字对不上页面只会显示"尚未实现"。
    """
    assert 'q_group_heat' in q.names()
    assert 'q_group_trend' in q.names()
    ref = engine.scalar(
        "SELECT data_query FROM config.chart WHERE chart_key = 'group_score_trend'")
    assert ref in q.names(), f'config.chart 指向了不存在的命名查询：{ref}'
