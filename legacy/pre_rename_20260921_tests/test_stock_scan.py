# -*- coding: utf-8 -*-
"""
合表（`q_stock_scan`）、板块 Top10（`q_hot_sector_stats`）、列紧凑（`ui`）、
权重表补说明（`q_effective_binding`）—— 2026-08-31 这一轮四项改动的守卫。

测的都是**会静默出错**的地方：

1. `名次` 是全市场名次（`fct.score.rank_in_market`），不是表里的行号 ——
   截断到 666 行后行号和名次长得很像，写错了页面上看不出来；
2. 两个查询必须取**同一批票**（共用 `_SCAN_BASE`）。各写一份的话，统计说的
   "前 666 只"和表里前 666 行迟早不是同一批，而两边单看都合理；
3. Top10 两列**量纲不同**（最热板块 ≤ top_n、其它板块必然超 top_n）。
   有人"顺手统一"成一个分母，这张表就开始骗人；
4. 行高与 padding 必须同源（`ui.ROW_PX` ⇄ `style_cell`），否则"一屏铺完 40 行"
   会差出几十像素，表现只是"表格自己在滚"；
5. 权重表右边四列来自各 builtin 的 `EXPLAIN`，少一个脚本就有一行是空的。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.db import engine
from app.web import queries, ui

#: 统计范围与合表行数**必须相等**（2026-09-01 一起从 100/300 放大到 666）：
#: 不相等的话「板块出现次数」里会有一批票在扫描表上根本找不到。
TOP_N = 666
SCAN_LIMIT = TOP_N
SECTORS = 3


def _walk(node):
    """递归遍历组件树。断言要落到 DataTable / CardBody 上，而 Dash 的 `repr`
    会把深层节点截成 `...`，靠字符串匹配是查不到的。"""
    yield node
    ch = getattr(node, 'children', None)
    if isinstance(ch, (list, tuple)):
        for c in ch:
            yield from _walk(c)
    elif ch is not None:
        yield from _walk(ch)


@pytest.fixture(scope='module')
def scan():
    rows = queries.q_stock_scan(limit=SCAN_LIMIT, sectors=SECTORS)
    if not rows:
        pytest.skip('当天没有评分数据')
    return rows


@pytest.fixture(scope='module')
def hot():
    rows = queries.q_hot_sector_stats(top_n=TOP_N, sectors=SECTORS, limit=10)
    if not rows:
        pytest.skip('当天没有评分数据')
    return rows


# ------------------------------------------------------------ 合表的列与口径
def test_columns_follow_the_review_question_order(scan):
    """
    列序＝复盘时的提问顺序（它是谁 → 值不值得看 → 量给不给力 → 当日行情 →
    是不是板块在动 → 深链）。这不是审美：合表的全部意义就是"一行看完一只票"，
    列一乱就退回成"在两个地方各找一次"。
    """
    assert list(scan[0]) == [
        'rank', 'ts_code', 'name', 'board',
        'oper', 'quadrant', 'x_quality', 'y_env',
        'vol_temp', 'vol_stage',
        'close', 'pct_chg', 'turnover_rate_pct', 'amount_yi',
        'hot_sector', 'sector_heat', 'other_sectors',
        'detail',
    ]


def test_rank_is_the_whole_market_rank_not_the_row_number(scan):
    """
    `名次` 取 `fct.score.rank_in_market`。表被截到 `SCAN_LIMIT` 行，行号和名次
    长得很像，所以这里回库逐行核对，而不是"看起来递增就算对"。
    """
    d = queries._scored_day()
    got = {r['ts_code']: r['rank'] for r in scan[:50]}
    want = {r['ts_code']: r['rank_in_market'] for r in engine.query(
        'SELECT ts_code, rank_in_market FROM fct.score '
        " WHERE trade_date = %(d)s AND cap_key = 'oper_score' "
        '   AND ts_code = ANY(%(codes)s)',
        {'d': d, 'codes': list(got)})}
    assert want, '库里没取到这批票的名次'
    assert got == want


def test_sector_columns_split_hot_from_the_rest(scan):
    """
    `最热板块` 是**一个**板块（`rn = 1`），`其它板块` 是第 2 ~ sectors 个用 `、`
    连起来 —— 所以最热那列里不能出现 `、`，其它那列最多 sectors-1 个。
    """
    for r in scan:
        if r['hot_sector']:
            assert '、' not in r['hot_sector']
        if r['other_sectors']:
            assert len(r['other_sectors'].split('、')) <= SECTORS - 1


def test_the_same_sector_is_not_counted_three_times(scan):
    """
    **踩过的坑**：同一个板块在三个源下各有一个 code（`TDX_880642` / `GN_880642` /
    `THS_D522` 都叫「供销社」），最初按 `sector_code` 排名，于是一只票的板块列
    写成「供销社 / 新材料、供销社」，Top10 里这只票还给同一个板块投三票 ——
    页面上像"板块行情"，其实是同一个板块被数了三遍。
    所以 `_SECTOR_PICK` 按**板块名**去重（`GROUP BY m.ts_code, i.name`）。
    """
    assert 'GROUP BY m.ts_code, i.name' in queries._SECTOR_PICK
    for r in scan:
        names = [r['hot_sector']] if r['hot_sector'] else []
        names += (r['other_sectors'] or '').split('、') if r['other_sectors'] else []
        assert len(names) == len(set(names)), (r['ts_code'], names)


def test_sector_heat_comes_from_the_sector_level_score(scan):
    """
    热度取 `fct.sector_heat_daily` 里 `src='sector'`（板块级直算）那份，不是旧的
    "成员均值广播"。均值会把板块内部的分歧抹平，看着还是个分数，没法证伪。

    ⚠️ **这一列是 -100 ~ +100 的相对强弱，不是 0~100 的评分**：实测当日 652 个
    板块里 329 个是负的、均值 0.8。所以看到「-33.1」不是脏数据，而是"这个板块
    今天弱于市场"。断言按真实值域写，写成 0~100 反倒是把口径记错。
    """
    assert "src = 'sector'" in queries._SECTOR_PICK
    heats = [float(r['sector_heat']) for r in scan if r['sector_heat'] is not None]
    if heats:
        assert min(heats) >= -100 and max(heats) <= 100


# ------------------------------------------------------------ 两张卡同一批票
def test_both_cards_take_the_stocks_from_the_same_sql(scan, hot):
    """
    统计的"前 666 只"必须就是表里的前 666 行 —— 两处共用 `_SCAN_BASE`。
    这里不看代码文本，直接拿合表前 `TOP_N` 行自己数一遍最热板块，与统计对齐。
    """
    from collections import Counter
    mine = Counter(r['hot_sector'] for r in scan[:TOP_N] if r['hot_sector'])
    for row in hot:
        if row['hot_sector']:
            assert mine[row['hot_sector']] == row['hot_cnt'], row['hot_sector']


def test_two_columns_have_different_denominators(hot):
    """
    最热板块一票一个，加起来 ≤ top_n；其它板块一票贡献多个，**总数必然超 top_n**。
    用户明确认过这一点，所以它是特性不是 bug —— 有人来"对齐分母"时这里先红。
    """
    full = queries.q_hot_sector_stats(top_n=TOP_N, sectors=SECTORS, limit=500)
    hot_total = sum(r['hot_cnt'] or 0 for r in full)
    other_total = sum(r['other_cnt'] or 0 for r in full)
    assert hot_total <= TOP_N
    assert other_total > TOP_N, (hot_total, other_total)


def test_ranks_are_aligned_side_by_side(hot):
    """
    两列各自排名、并排显示。用 `generate_series` 对齐而不是 FULL JOIN：
    某一侧不足 10 行时 FULL JOIN 会让行数变成 max(两侧)、名次错位。
    """
    assert [r['rank'] for r in hot] == list(range(1, 11))
    for col, cnt in (('hot_sector', 'hot_cnt'), ('other_sector', 'other_cnt')):
        vals = [r[cnt] for r in hot if r[cnt] is not None]
        assert vals == sorted(vals, reverse=True), col


# ------------------------------------------------------------ 列紧凑
def test_row_height_matches_the_cell_padding():
    """
    行高是 padding 算出来的常量。padding 改了不改 `ROW_PX`，"一屏铺完 40 行"
    就会差几十像素 —— 页面上只表现为"表格自己在滚"，看不出是这里错了。
    """
    src = Path('app/web/ui.py').read_text(encoding='utf-8')
    assert "'padding': '2px 5px'" in src
    assert (ui.ROW_PX, ui.HEAD_PX, ui.TAIL_PX) == (23, 54, 12)
    assert ui.fit_height(40) == 54 + 23 * 40 + 12
    assert ui.fit_height(1000, cap=800) == 800          # 封顶
    assert ui.fit_height(0) == ui.fit_height(1)         # 空表不塌成负高


def test_header_wraps_and_there_is_no_filter_row():
    """
    用户："标题过长的话改为换行显示"、"第二行是啥？是不是数据库的字段名？
    这个就不用显示了"。

    第二行其实是 Dash 原生的**逐列筛选输入框**（`filter_action='native'`），
    不是字段名 —— 去掉之后抬头只剩一行标题，`HEAD_PX` 也跟着从 76 收到 54。
    表头改成可换行（`whiteSpace: normal` + `height: auto`），长标题折行而不是
    把整列撑宽。这三件事必须同时成立：只删筛选行不改 `HEAD_PX` 会多出 32px 空白，
    只改宽度不让表头换行会把长标题截断成「窗口最高…」。
    """
    src = Path('app/web/ui.py').read_text(encoding='utf-8')
    assert "filter_action='native'," not in src, '筛选行回来了，HEAD_PX 得跟着回 76'
    assert "sort_action='native'," in src, '排序不该一起被删掉'
    head = src.split('style_header={', 1)[1].split('}', 1)[0]
    for k in ("'whiteSpace': 'normal'", "'height': 'auto'"):
        assert k in head, (k, head)



def test_narrow_columns_do_not_hold_wide_empty_space(scan):
    """
    用户："单元格紧凑一些，列紧凑一些，很多都是很空的"。

    两三字的列（名次、象限）现在贴着下限 `lo=44`：**宽度只按单元格内容算**
    （2026-08-31 用户："按照单元格内容自适应宽度，而不是按照标题"），表头放不下
    就换行。旧口径取 `max(表头, 内容)`，「窗口最高X+Y」那种六字表头能把一列
    `47.9` 撑到 105px。同时任何一列都不能超过 `hi`。
    """
    keys = [k for k in scan[0] if k != 'detail']
    w = {c['if']['column_id']: int(c['width'].removesuffix('px'))
         for c in ui.col_widths(scan, keys)}
    assert min(w.values()) <= 44, w        # 表头重新参与宽度就会红
    assert max(w.values()) <= 300, w
    assert w['quadrant'] < w['name'], '象限比名称还宽，说明列宽又不按内容算了'
    assert sum(w.values()) < 1800, ('合表要能在 1920 屏一眼看完', sum(w.values()))



# ------------------------------------------------------------ 权重表补说明
def test_every_binding_row_explains_itself():
    """
    右边四列来自各 builtin 源码里的 `EXPLAIN`（`app/compute/explain.py` 汇总）。
    漏一个脚本就有一行是空的 —— 而"这一行为什么算进总分"正是这张表存在的理由。
    `base_factors_v1` / `review_report_v1` 不参与加权，也必须写清楚。
    """
    rows = queries.q_effective_binding()
    assert len(rows) >= 19
    for r in rows:
        for col in ('basis', 'terms_brief', 'combine'):
            assert r[col], f"{r['script_key']} 缺 {col}"
    table_only = {r['script_key']: r['combine'] for r in rows
                  if r['script_key'] in ('base_factors_v1', 'review_report_v1')}
    for key, combine in table_only.items():
        assert '不参与加权' in combine, key


def test_binding_help_says_where_the_four_columns_come_from():
    """
    说明必须交代"这四列不在库里，在代码里" —— 否则下一个人会去 `config.binding`
    里找这四列，找不到就以为是漏了。库里的说明优先于代码，所以两边都要核。
    """
    from app.web import help as help_repo
    code = ui.CARD_HELP['q_effective_binding']
    note = help_repo.for_card('q_effective_binding')
    for text in (code, str(note)):
        assert 'EXPLAIN' in text
        assert '不参与加权' in text


# ------------------------------------------------------------ 卡片接得上
@pytest.mark.parametrize('card_ref, page_size', [
    ('q_stock_scan', 30), ('q_hot_sector_stats', 10), ('q_effective_binding', 20)])
def test_cards_are_registered_in_layout_and_seed(card_ref, page_size):
    """只改库不改 seed，下次重建库这几张卡就退回旧样子。"""
    row = engine.query_one(
        'SELECT height, options FROM config.page_layout '
        ' WHERE page_key = %s AND card_ref = %s', ('scores', card_ref))
    assert row, f'scores 页没有 {card_ref} 这张卡'
    assert int(row['options'].get('page_size', 0)) == page_size
    seed = Path('app/db/sql/05_seed.sql').read_text(encoding='utf-8')
    assert f"'{card_ref}'" in seed


# ------------------------------------------------------------ 高度＝一页行数
def test_table_cards_do_not_pin_a_height():
    """
    ★**表格卡不许有固定高度**（2026-09-01）。

    原来卡片高度和 `page_size` 是两个手填的数：矮了就"想看更多得**在表格里**
    滚"，高了就"表格下面一片空白"—— 用户两条反馈正是这两个症状。实测当时库里
    18 张表格卡有 13 张的高度比一页内容矮 11~1,386px。

    现在 `ui.render_card` 对 `card_kind='table'` 既不给卡片高度也不给表格高度，
    一页有几行就画多高；要多看几行改 `options.page_size`。这条断言盯的是
    "有人又把 height 接回去" —— 那是静默回归，页面上只表现为"表格自己在滚"。
    """
    from app.db.repo import registry as reg_repo
    for page in ('home', 'scores', 'sector', 'screen', 'watchlist'):
        for row in reg_repo.layout(page):
            if row['card_kind'] != 'table':
                continue
            for node in _walk(ui.render_card(dict(row), {})):
                if type(node).__name__ == 'DataTable':
                    st = node.style_table or {}
                    assert not st.get('height'), (page, row['slot'], st)
                    # maxHeight 只有显式配了 options.max_height 才该有
                    want_cap = (row.get('options') or {}).get('max_height')
                    assert bool(st.get('maxHeight')) == bool(want_cap), (
                        page, row['slot'], st)
                if type(node).__name__ == 'CardBody':
                    assert not (node.style or {}).get('height'), (page,
                                                                  row['slot'])


def test_scan_color_rules_point_at_columns_that_exist(scan):
    """配色列名对不上是静默失效：页面不报错，只是不上色。"""
    row = engine.query_one(
        'SELECT options FROM config.page_layout '
        ' WHERE page_key = %s AND card_ref = %s', ('scores', 'q_stock_scan'))
    color = row['options'].get('color') or {}
    cols = set(scan[0])
    assert color, '合表没配任何配色'
    assert set(color.get('pct') or ()) <= cols
    assert set(color.get('amp') or ()) <= cols
    assert ui._table_color(row['options'])


@pytest.mark.parametrize('card_ref', ['q_stock_scan', 'q_hot_sector_stats',
                                      'q_effective_binding'])
def test_cards_render_without_blowing_up(card_ref):
    """渲染失败在页面上是一条红条而不是异常，所以要直接查渲染结果。"""
    row = engine.query_one(
        'SELECT * FROM config.page_layout '
        ' WHERE page_key = %s AND card_ref = %s', ('scores', card_ref))
    assert '渲染失败' not in str(ui.render_card(dict(row), {}))


def test_queries_are_fast_enough_for_a_server_rendered_card():
    """
    /scores 是服务端一次性渲染，这两张卡慢多少页面就慢多少。实测（666 行）
    合表 0.32s、Top10 0.17s（300 行 / 前 100 只时是 0.28s / 0.33s ——
    放大范围几乎不花钱，重头是那一遍面板扫描，不是行数），留余量当红线。
    """
    t0 = time.time()
    queries.q_stock_scan(limit=SCAN_LIMIT)
    scan_s = time.time() - t0
    t0 = time.time()
    queries.q_hot_sector_stats()
    hot_s = time.time() - t0
    assert scan_s < 1.2, scan_s
    assert hot_s < 0.6, hot_s
