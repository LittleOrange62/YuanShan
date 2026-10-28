# -*- coding: utf-8 -*-
"""
四象限趋势分（`q_quadrant_trend`）。

这张表最容易**静默出错**，所以测的不是"能返回行"，而是能让它悄悄说谎的几件事：

1. 斜率算错（口径、窗口长度、量纲）—— 页面上看不出来，排序还是好好的；
2. 窗口偷看了未来（`trade_date <= 基准日` 少写一处就成了预知）；
3. 形态标签的**判定顺序**被人"顺手整理"成先判上行 —— 冲高回落全被吞掉；
4. 基准日取成"有行情的最新一天"而不是"有评分的最新一天" —— 卡片整张变空；
5. 列改名之后 `options.color` 里的列名对不上 —— 颜色没了，页面不报错。
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import pytest

from app.db import engine
from app.web import queries

BLOCKS = set('▁▂▃▄▅▆▇')


@pytest.fixture(scope='module')
def rows():
    out = queries.q_quadrant_trend(win=10, min_n=8, limit=200)
    if not out:
        pytest.skip('fct.score 里没有足够的评分历史')
    return out


# ------------------------------------------------------------ 形态判定顺序
def test_shape_flat_wins_first():
    """方向都没有的时候不谈"稳不稳"：|趋势分| < 1 一律横盘。"""
    assert queries.trend_shape(0.5, 0.99, 0.0) == '横盘'
    assert queries.trend_shape(-0.5, 0.99, -99.0) == '横盘'


def test_shape_fade_is_judged_before_up():
    """
    **这一条是这张表的核心语义**：前 8 天一路上、最后两天塌回去，最小二乘的斜率
    仍然是正的、R² 还很高。如果先判"上行"，这种票会被标成「稳步上行」，
    而它当下已经在回落 —— 与量能阶段里"背离必须最先判"是同一类错误。
    """
    assert queries.trend_shape(20.0, 0.9, -30.0) == '冲高回落'
    # 只差一点点没到阈值的仍然是上行，边界不能反过来
    assert queries.trend_shape(20.0, 0.9, -14.9) == '稳步上行'


def test_shape_r2_separates_steady_from_choppy():
    assert queries.trend_shape(20.0, 0.9, 0.0) == '稳步上行'
    assert queries.trend_shape(20.0, 0.1, 0.0) == '震荡上行'
    assert queries.trend_shape(-20.0, 0.9, 0.0) == '稳步下行'
    assert queries.trend_shape(-20.0, 0.1, 0.0) == '震荡下行'


def test_shape_order_is_documented_in_source():
    """顺序写在 docstring 里，改代码不改说明的话这里先红。"""
    doc = queries.trend_shape.__doc__ or ''
    assert '判定有先后' in doc
    assert doc.index('冲高回落') < doc.index('稳步上行')


# ------------------------------------------------------------ 斜率对得上
def test_trend_score_matches_numpy_polyfit(rows):
    """
    趋势分 = 最小二乘斜率 × 窗口长度。这里拿 numpy 独立算一遍：
    PG 的 `regr_slope` 和 `np.polyfit(x, y, 1)` 必须给同一个数，
    否则"趋势分"这一列就只是个自洽的幻觉。
    """
    np = pytest.importorskip('numpy')
    day = queries._scored_day()
    checked = 0
    for r in rows[:5]:
        ser = engine.query(
            """
            SELECT trade_date, value FROM fct.score
             WHERE ts_code = %(c)s AND cap_key = 'oper_score'
               AND trade_date <= %(d)s AND value IS NOT NULL
             ORDER BY trade_date DESC LIMIT %(w)s
            """, {'c': r['ts_code'], 'd': day, 'w': 10})
        ys = [float(x['value']) for x in ser][::-1]
        assert len(ys) == int(r['trend_days'])
        xs = list(range(1, len(ys) + 1))
        b = float(np.polyfit(xs, ys, 1)[0])
        assert abs(b * 10 - float(r['trend_score'])) < 0.05, r['ts_code']
        # R² 同样独立核一遍
        rr = float(np.corrcoef(xs, ys)[0, 1]) ** 2
        assert abs(rr - float(r['r2'])) < 0.01, r['ts_code']
        checked += 1
    assert checked >= 1


def test_pred_oper_extrapolates_one_day(rows):
    """预测分 = a + b×(n+1)，与自选组页 `q_group_heat.pred_oper` 同公式。"""
    np = pytest.importorskip('numpy')
    day = queries._scored_day()
    r = rows[0]
    ser = engine.query(
        """
        SELECT value FROM fct.score
         WHERE ts_code = %(c)s AND cap_key = 'oper_score'
           AND trade_date <= %(d)s AND value IS NOT NULL
         ORDER BY trade_date DESC LIMIT 10
        """, {'c': r['ts_code'], 'd': day})
    ys = [float(x['value']) for x in ser][::-1]
    xs = list(range(1, len(ys) + 1))
    b, a = (float(v) for v in np.polyfit(xs, ys, 1))
    assert abs((a + b * (len(ys) + 1)) - float(r['pred_oper'])) < 0.1


# ------------------------------------------------------------ 不许看未来
def test_window_never_looks_past_the_basis_date():
    """
    给一个更早的基准日，窗口里出现的任何一天都必须 ≤ 它。
    `xy_max_date` 是唯一会把窗口里的日期带出来的列，正好当探针。
    """
    days = [r['trade_date'] for r in queries.q_score_dates(limit=8)]
    if len(days) < 6:
        pytest.skip('评分历史不足')
    basis = days[5]
    out = queries.q_quadrant_trend(trade_date=basis, win=5, min_n=3, limit=50)
    if not out:
        pytest.skip(f'{basis} 这天算不出趋势')
    for r in out:
        assert r['xy_max_date'] <= basis, (r['ts_code'], r['xy_max_date'])


def test_basis_day_falls_back_to_the_latest_scored_day():
    """
    **踩过的坑**：`latest_date()` 取的是有*行情*的最新一天（实测 2026-08-31
    有行情、没评分），拿它去 JOIN `fct.score` 一行都对不上，卡片静默变空。
    所以基准日必须向下取整到"有评分的那天"，传一个更晚的日期也要出行。
    """
    scored = queries._scored_day()
    assert scored is not None
    later = scored + dt.timedelta(days=3)
    assert queries._scored_day(later) == scored
    assert queries.q_quadrant_trend(trade_date=later, limit=5), \
        '传了一个还没算分的日期就返回空 —— 页面上会看成功能坏了'


# ------------------------------------------------------------ 列的自洽
def test_spark_is_one_block_per_day(rows):
    for r in rows:
        assert len(r['spark']) == int(r['trend_days'])
        assert set(r['spark']) <= BLOCKS, r['spark']


def test_min_n_is_enforced(rows):
    """新股 / 长停票必须整片缺席，而不是拿 3 天数据拟合出一条斜率。"""
    assert all(int(r['trend_days']) >= 8 for r in rows)
    strict = queries.q_quadrant_trend(win=10, min_n=10, limit=50)
    assert all(int(r['trend_days']) == 10 for r in strict)


def test_gap_from_max_is_consistent(rows):
    """距最高 = 今天的 X+Y − 窗口最高，且今天不可能高过窗口最高值。"""
    for r in rows:
        gap = float(r['xy_total']) - float(r['xy_max10'])
        assert abs(gap - float(r['gap_from_max'])) < 0.11, r['ts_code']
        assert float(r['gap_from_max']) <= 0.11, r['ts_code']


def test_quad_path_and_upgrade_flag(rows):
    """升格按"好坏次序" Q1 > Q3 > Q2 > Q4 判，不是 Q 编号大小。"""
    assert queries.QUAD_ORDER['Q3'] > queries.QUAD_ORDER['Q2']
    for r in rows:
        first, arrow, last = str(r['quad_path']).partition('→')
        assert arrow == '→', r['quad_path']
        assert last == r['quadrant']
        want = queries.QUAD_ORDER[last] > queries.QUAD_ORDER[first]
        # 升格列给的是符号（配色要做文本等值比较），值域只有这两个
        assert r['quad_up'] in ('↑', ''), r['quad_up']
        assert bool(r['quad_up']) is want, r['quad_path']


def test_shape_column_matches_the_helper(rows):
    for r in rows:
        assert r['shape'] == queries.trend_shape(
            r['trend_score'], r['r2'], r['gap_from_max'])


# ------------------------------------------------------------ 排序与安全
def test_default_sort_is_trend_desc_with_nulls_last(rows):
    prev = None
    for r in rows:
        v = r['trend_score']
        if v is None:
            prev = None
            continue
        if prev is not None:
            assert float(v) <= float(prev) + 1e-9
        prev = v


def test_sort_column_is_a_whitelist():
    """排序列是拼进 SQL 的，放开就是注入面。"""
    with pytest.raises(ValueError):
        queries.q_quadrant_trend(sort='value; DROP TABLE fct.score')
    for key in queries.QUADRANT_TREND_SORTS:
        assert queries.q_quadrant_trend(sort=key, limit=3) is not None


def test_quadrant_filter_narrows_to_one_cell():
    out = queries.q_quadrant_trend(quadrant='Q1', limit=50)
    if not out:
        pytest.skip('当天 Q1 没有票')
    assert {r['quadrant'] for r in out} == {'Q1'}


# ------------------------------------------------------------ 卡片接得上
def test_card_is_registered_in_layout_and_seed():
    """只改库不改 seed，下次重建库这张卡就没了。"""
    row = engine.query_one(
        'SELECT height, options FROM config.page_layout '
        ' WHERE page_key = %s AND card_ref = %s', ('scores', 'q_quadrant_trend'))
    assert row, 'scores 页没有 q_quadrant_trend 这张卡'
    assert int(row['options'].get('page_size', 0)) == 40
    # 40 行一屏铺完。行高是 `ui.ROW_PX`（2026-08-31 列紧凑后 27 → 23），
    # 所以这里按常量算，不写死 —— 写死的话调一次 padding 就得来改测试。
    from app.web import ui
    assert int(row['height']) >= ui.fit_height(40, cap=10_000)
    seed = Path('app/db/sql/05_seed.sql').read_text(encoding='utf-8')
    assert "'q_quadrant_trend'" in seed
    assert '"page_size":40' in seed


def test_color_rules_point_at_columns_that_exist(rows):
    """
    配色列名写在 `options.color` 里、列名来自查询。改了列名却忘了改这里，
    页面不会报错 —— 只是颜色没了。
    """
    from app.web import ui
    row = engine.query_one(
        'SELECT options FROM config.page_layout '
        ' WHERE page_key = %s AND card_ref = %s', ('scores', 'q_quadrant_trend'))
    color = row['options'].get('color') or {}
    cols = set(rows[0])
    assert color.get('pct'), '这张卡没配任何配色列'
    assert set(color['pct']) <= cols, f"配色列不在查询结果里：{color['pct']}"
    assert set(color['flag']['cols']) <= cols
    assert set(color['dim']['cols']) <= cols
    assert ui._table_color(row['options']), 'options.color 没被翻成条件样式'


def test_flag_rule_matches_a_value_that_actually_occurs(rows):
    """
    **踩过的坑**：一开始把 `quad_path` 也写进 flag 的列里，它的值是 `Q2→Q1`，
    永远等不到 `↑` —— 配色匹配不上是静默失效，页面不报错、只是不亮。
    所以这里要求 flag 的每一列都真的出现过那个符号。
    """
    row = engine.query_one(
        'SELECT options FROM config.page_layout '
        ' WHERE page_key = %s AND card_ref = %s', ('scores', 'q_quadrant_trend'))
    flag = (row['options'].get('color') or {}).get('flag') or {}
    val = flag.get('value')
    for c in flag.get('cols') or []:
        seen = {r[c] for r in rows}
        assert val in seen, f'{c} 这一列从没出现过 {val!r}，配色规则是死的（{seen}）'


def test_dim_threshold_tracks_the_code_constant():
    """压灰的阈值必须和 `trend_shape` 里"稳步 vs 震荡"的分界同一个数，
    否则页面上"压灰的"和形态说"震荡"的不是同一批行。"""
    row = engine.query_one(
        'SELECT options FROM config.page_layout '
        ' WHERE page_key = %s AND card_ref = %s', ('scores', 'q_quadrant_trend'))
    dim = (row['options'].get('color') or {}).get('dim') or {}
    assert float(dim['below']) == float(queries.R2_TIGHT)


def test_card_renders_without_blowing_up():
    """卡片渲染失败在页面上是一条红条，不是异常 —— 所以要直接查渲染结果。"""
    from app.web import ui
    row = engine.query_one(
        'SELECT * FROM config.page_layout '
        ' WHERE page_key = %s AND card_ref = %s', ('scores', 'q_quadrant_trend'))
    comp = ui.render_card(dict(row), {})
    assert '渲染失败' not in str(comp)


def test_query_is_fast_enough_for_a_server_rendered_card():
    """
    /scores 是服务端一次性渲染的，这张卡慢多少页面就慢多少。
    现算过 2.0s → 1.0s（改掉三表自连接）→ 2026-08-31 落库后 0.45s。
    红线按落库那条路留一倍余量；回退现算那条路另有一条更宽的红线（见下）。
    """
    t0 = time.time()
    queries.q_quadrant_trend(limit=300)
    assert time.time() - t0 < 1.2


# ------------------------------------------------------------ 落库预算
def test_stored_and_live_paths_agree_row_for_row():
    """
    ★**落库这件事只有这一条测试真正在保护**：落库值和现算值必须逐行逐列相等。

    两条路共用 `score_trend.CORE_SQL`（落库时 INSERT ... SELECT 它，回退时把它
    当子查询），所以这里核的是"共用真的生效了"。各写一份的话两边会慢慢漂开，
    而页面上两条路看起来一模一样 —— 这种错不会有人发现。
    """
    from app.compute import score_trend
    d = queries._scored_day()
    if not score_trend.coverage(d, 10) or not queries._trend_is_stored(d, 10):
        pytest.skip(f'{d} 还没落库，先跑 d5_score_trend.py')
    stored = queries.q_quadrant_trend(limit=400)
    orig = queries._trend_is_stored
    try:
        queries._trend_is_stored = lambda *_a, **_k: False
        live = queries.q_quadrant_trend(limit=400)
    finally:
        queries._trend_is_stored = orig
    assert len(stored) == len(live) > 0
    for a, b in zip(stored, live, strict=True):
        assert a == b, a['ts_code']


def test_falls_back_to_live_when_that_day_is_not_stored():
    """
    库里没有那天时**不能给空表**。日常链路没跑的那个窗口里，页面宁可慢 0.7 秒，
    也不要显示"无数据" —— "声明了但用不了"比慢危险得多。
    """
    d = queries._scored_day()
    assert d is not None
    # 找一个肯定没落库的窗口长度。注意 `min_n` 要跟着窗口收 ——
    # 默认 min_n=8 比 win=7 还大，那样出来的空表是参数矛盾，不是回退失效。
    assert not queries._trend_is_stored(d, 7), 'win=7 竟然落库了，换一个没算过的'
    rows = queries.q_quadrant_trend(win=7, min_n=6, limit=20)
    assert rows, 'win=7 没落库，回退现算也该出票'
    assert rows[0]['trend_days'] <= 7


def test_stored_rows_are_for_the_same_day_the_page_shows():
    """
    读表的判据是"**这一天**有没有行"，不是"表里有没有行"。否则日常链路今天没跑时
    会拿昨天的趋势配今天的切片 —— 页面上看不出来，比慢一秒危险得多。
    """
    src = Path('app/web/queries.py').read_text(encoding='utf-8')
    i = src.index('def _trend_is_stored')
    body = src[i:i + 900]
    assert 'trade_date = %(d)s' in body and 'win = %(win)s' in body


def test_stored_table_keeps_slope_not_the_display_number():
    """
    表里存**斜率（分/天）**，页面上的 `趋势分数` = slope × win 是展示层换算。
    存成展示值的话，换窗口长度就得重刷历史，而且和 `fct.sector_trend` 的
    `slope` 量纲对不上（同一个系统里"趋势"只该有一种算法）。
    """
    d = queries._scored_day()
    row = engine.query_one(
        'SELECT slope, n_points FROM fct.score_trend '
        ' WHERE trade_date = %(d)s AND win = 10 AND n_points = 10 '
        ' ORDER BY abs(slope) DESC LIMIT 1', {'d': d})
    if not row:
        pytest.skip('还没落库')
    page = queries.q_quadrant_trend(limit=1, sort='trend_score')
    assert page
    assert abs(float(page[0]['trend_score'])
               - float(row['slope']) * 10) < 0.02


def test_the_etl_job_is_registered_with_a_runner_and_a_script():
    """
    作业没执行器就是"声明了不能跑"。这里同时卡注册表、`RUNNERS` 和 seed ——
    只改库不改 seed，下次重建库这条作业就没了。
    """
    from app.orchestrator import runner
    job = engine.query_one(
        "SELECT * FROM config.job WHERE job_key = 'calc_score_trend'")
    assert job, 'calc_score_trend 没登记'
    assert job['after_job'] == 'calc_scores', '必须排在评分之后'
    name = runner.job_runner(job)
    assert name == 'compute_score_trend', '这个作业没有执行器'
    script = Path(runner.runner_script(name))
    assert script.exists(), f'{script} 不存在'
    seed = Path('app/db/sql/05_seed.sql').read_text(encoding='utf-8')
    assert "'calc_score_trend'" in seed
    assert 'compute_score_trend' in seed
    # 一键追平那条链也要带上它 —— 本系统没有调度器进程，日常真正跑的是它
    fill = Path('scripts/ops/e1_fill_all.py').read_text(encoding='utf-8')
    assert 'd5_score_trend.py' in fill
    assert "'scoretrend'" in fill


def test_spark_column_is_wide_enough_for_ten_blocks(rows):
    """
    用户 2026-08-31：「趋势图字段再宽一点」。

    根因不在这张卡而在 `ui._text_px`：`▁▂▃▄▅▆▇` 的东亚宽度是"歧义"，在中文字体里
    按**全角**渲染，而它们的码位（U+2581 起）小于原来那条判据 `> 0x2E7F`，于是被
    当成半宽 —— 10 格量出 73px、列宽给到 85px，实际要 130px 左右。页面上表现为
    最后几格被挤掉，看起来像"这只票只有 6 天数据"。

    这里核的是"10 格装得下"，不写死 144 —— 字号一改宽度就该跟着变。
    """
    from app.web import ui
    spark = next(r['spark'] for r in rows if r['spark'])
    assert len(spark) == 10
    assert ui._text_px(spark) >= 13.0 * len(spark) - 1
    w = {c['if']['column_id']: int(c['width'].removesuffix('px'))
         for c in ui.col_widths(rows, list(rows[0]))}
    assert w['spark'] >= ui._text_px(spark)
    assert w['spark'] > w['quad_path'], '趋势图比象限迁移还窄，说明块字符又被当半宽了'
    """库里建了表但 02_schema.sql 里没有，重建库之后这张卡就永远走回退现算。"""
    ddl = Path('app/db/sql/02_schema.sql').read_text(encoding='utf-8')
    assert 'fct.score_trend' in ddl
    assert 'ix_score_trend_date' in ddl
