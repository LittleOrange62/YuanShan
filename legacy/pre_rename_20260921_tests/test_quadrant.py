# -*- coding: utf-8 -*-
"""
四象限选票的验收测试。

最重要的一条是 `test_quadrant_label_matches_xy_signs`：象限标签的含义**只能
由数据定义**。代码里原来的卡片说明把 Q3 / Q4 写反了（"Q4 左上 / Q3 左下"），
靠人读注释发现不了 —— 这条断言逐票核对符号，写反就红。
"""
from __future__ import annotations

import pytest

from app.db import engine
from app.web import queries as q

# 象限 → (X 是否非负, Y 是否非负)。这是**实测**出来的口径，不是抄注释。
QUAD_SIGNS = {
    'Q1': (True, True),
    'Q2': (True, False),
    'Q3': (False, True),
    'Q4': (False, False),
}


@pytest.fixture(scope='module')
def latest():
    d = engine.scalar(
        "SELECT max(trade_date) FROM fct.score WHERE cap_key = 'oper_score'")
    if d is None:
        pytest.skip('fct.score 里没有 oper_score，先跑评分')
    return d


def test_quadrant_label_matches_xy_signs(latest):
    """
    库里每一条 `quadrant` 标签都必须和它的 X / Y 符号一致。

    这条同时锁住了"最好的象限是哪一个"：用户要的默认格是"X 和 Y 都大于 0"，
    页面上写的是 Q1 —— 如果哪天评分脚本换了标签约定，这里会先红，
    而不是等到有人照着 Q1 去买票。
    """
    rows = engine.query(
        """
        SELECT o.quadrant,
               count(*) AS n,
               count(*) FILTER (WHERE x.value >= 0) AS x_pos,
               count(*) FILTER (WHERE y.value >= 0) AS y_pos
          FROM fct.score o
          JOIN fct.score x ON x.ts_code = o.ts_code
                          AND x.trade_date = o.trade_date
                          AND x.cap_key = 'stock_quality'
          JOIN fct.score y ON y.ts_code = o.ts_code
                          AND y.trade_date = o.trade_date
                          AND y.cap_key = 'env_resonance'
         WHERE o.cap_key = 'oper_score' AND o.trade_date = %s
         GROUP BY 1
        """, (latest,))
    assert rows, '当日没有象限数据'
    for r in rows:
        want_x, want_y = QUAD_SIGNS[r['quadrant']]
        exp_x = r['n'] if want_x else 0
        exp_y = r['n'] if want_y else 0
        assert r['x_pos'] == exp_x, f"{r['quadrant']} 的 X 符号和标签不一致"
        assert r['y_pos'] == exp_y, f"{r['quadrant']} 的 Y 符号和标签不一致"


def test_counts_cover_every_scored_stock(latest):
    rows = q.q_quadrant_counts(trade_date=latest)
    assert rows
    total = engine.scalar(
        """
        SELECT count(*) FROM fct.score o
          JOIN fct.score x ON x.ts_code = o.ts_code
                          AND x.trade_date = o.trade_date
                          AND x.cap_key = 'stock_quality'
          JOIN fct.score y ON y.ts_code = o.ts_code
                          AND y.trade_date = o.trade_date
                          AND y.cap_key = 'env_resonance'
         WHERE o.cap_key = 'oper_score' AND o.trade_date = %s
        """, (latest,))
    assert sum(r['stocks'] for r in rows) == total


def test_pick_defaults_to_the_all_positive_quadrant(latest):
    """默认那一格必须是"X 和 Y 都 ≥ 0"，这是用户点开页面第一眼要看到的东西。"""
    rows = q.q_quadrant_pick(trade_date=latest)
    assert rows
    assert all(r['quadrant'] == 'Q1' for r in rows)
    assert all(float(r['x_score']) >= 0 and float(r['y_score']) >= 0
               for r in rows)


def test_pick_returns_only_the_asked_quadrant(latest):
    for quad, (want_x, want_y) in QUAD_SIGNS.items():
        rows = q.q_quadrant_pick(trade_date=latest, quadrant=quad, limit=50)
        if not rows:
            continue
        assert all(r['quadrant'] == quad for r in rows)
        for r in rows:
            assert (float(r['x_score']) >= 0) is want_x, (quad, r['ts_code'])
            assert (float(r['y_score']) >= 0) is want_y, (quad, r['ts_code'])


def test_no_threshold_by_default(latest):
    """
    不传门槛就只按象限过。

    默认卡个 30 会把 Q1 从上千只砍到几十只，"先看全貌再收紧"这一步就没了 ——
    所以这条断言不传门槛时拿到的票数**多于**卡了门槛的。
    """
    loose = q.q_quadrant_pick(trade_date=latest, limit=2000)
    tight = q.q_quadrant_pick(trade_date=latest, min_x=30, min_y=30,
                              limit=2000)
    assert len(loose) > len(tight)
    assert all(float(r['x_score']) >= 30 and float(r['y_score']) >= 30
               for r in tight)


def test_sort_actually_sorts(latest):
    for key in ('oper_score', 'xy_total', 'x_score', 'y_score'):
        rows = q.q_quadrant_pick(trade_date=latest, sort=key, limit=30)
        if len(rows) < 2:
            continue
        vals = [float(r[key]) for r in rows]
        assert vals == sorted(vals, reverse=True), key


def test_sort_column_is_whitelisted(latest):
    """
    排序列是**拼进 SQL** 的，不是绑定参数，所以必须走白名单。

    传一个不在白名单里的值要直接抛错，不能"兜底成默认列"静默通过 ——
    静默通过的话页面上排序不生效，没人会知道是被吞了。
    """
    with pytest.raises(ValueError):
        q.q_quadrant_pick(trade_date=latest, sort="value; DROP TABLE fct.score")
    with pytest.raises(ValueError):
        q.q_quadrant_pick(trade_date=latest, sort='close')


def test_rows_carry_detail_deeplink(latest):
    rows = q.q_quadrant_pick(trade_date=latest, limit=5)
    assert rows
    for r in rows:
        assert r['detail'].startswith('[')
        assert r['ts_code'] in r['detail']


def test_page_sort_options_match_query_whitelist():
    """
    页面下拉和查询白名单是同一份东西的两个投影，**不能各写一份**。

    各写一份的结果是下拉里有个选项、查询不认，点下去报错。
    """
    from app.web.pages import scores
    assert set(scores.SORT_CN) == set(q.QUADRANT_SORTS)
    assert {v for v, *_ in
            [(o['value'],) for o in scores._sort_options()]} == set(
                q.QUADRANT_SORTS)


def test_page_declares_all_four_quadrants_with_verified_rules():
    """页面上四个按钮的判据文案要和实测符号一致，否则说明写反了照样误导人。"""
    from app.web.pages import scores
    assert scores.DEFAULT_QUAD == 'Q1'
    assert {qd for qd, *_ in scores.QUADS} == set(QUAD_SIGNS)
    rules = {qd: rule for qd, _short, rule, _use in scores.QUADS}
    assert rules['Q1'] == 'X≥0 且 Y≥0'
    assert rules['Q3'] == 'X<0 且 Y≥0'
    assert rules['Q2'] == 'X≥0 且 Y<0'
    assert rules['Q4'] == 'X<0 且 Y<0'


def _by_id(comp) -> dict:
    """`{组件 id: 组件}`，递归 children。Dash 组件树没有现成的按 id 取法。"""
    found: dict = {}

    def walk(node):
        if isinstance(node, (list, tuple)):
            for x in node:
                walk(x)
            return
        cid = getattr(node, 'id', None)
        if isinstance(cid, str):
            found[cid] = node
        kids = getattr(node, 'children', None)
        if kids is not None:
            walk(kids)

    walk(comp)
    return found


def test_page_defaults_are_the_dual_high_combo():
    """
    2026-09-01 用户要求：选票结果**默认就是双高**（X≥30 且 Y≥30，按 X+Y 排序）。

    三个常量必须一致地摆在一起 —— 只改门槛不改排序，页面显示的还是按操作分
    （0.40X + 0.60Y）排的表，标题却写着"按 X+Y 排序"，属于标题骗人。
    """
    from app.web.pages import scores
    assert scores.DEFAULT_MIN_X == 30
    assert scores.DEFAULT_MIN_Y == 30
    assert scores.DEFAULT_SORT == 'xy_total'
    assert scores.DEFAULT_SORT in q.QUADRANT_SORTS
    # 首屏输入框要**带着**这两个门槛，不然"默认双高"只在函数签名里成立，
    # 用户看到的是空输入框 + 已经被过滤的表，改一下门槛反而变多。
    # 按 id 在组件树里找，**不要拿 str(组件) 去匹配** —— Dash 的 repr 会把长树
    # 截断成 `...`，断言会因为"被截掉了"而失败，看着像默认值没设上。
    ctrl = _by_id(scores._controls({}, None))
    assert ctrl['qd-minx'].value == scores.DEFAULT_MIN_X
    assert ctrl['qd-miny'].value == scores.DEFAULT_MIN_Y
    assert ctrl['qd-sort'].value == scores.DEFAULT_SORT
    assert ctrl['qd-quad'].value == scores.DEFAULT_QUAD


def test_dual_high_card_is_gone_from_the_layout():
    """
    双高卡合并进选票结果之后，`config.page_layout` 里那一行必须真的没了 ——
    留着就是同一个问题两张表（而且下面那张的门槛写死、改上面的输入框不跟着走）。
    """
    from app.db.repo import registry as reg_repo
    refs = {c.get('card_ref') for c in reg_repo.layout('scores')}
    assert 'q_quadrant_strong' not in refs
    # 查询本身**保留注册**：它仍是一块可用积木，想单独摆一张写死门槛的表就加回去
    assert 'q_quadrant_strong' in q.names()


def test_result_table_shows_enough_rows_at_once():
    """
    用户要求"表格再拉长一点，直接能看到的个股多一些"。
    判据是**一页的行数全部可见**：`page_size` 大于可见行数等于把翻页换成滚动。
    """
    from app.web import ui
    from app.web.pages import scores
    assert scores.PAGE_SIZE >= 80
    assert scores._fit_height(scores.PAGE_SIZE) >= ui.fit_height(
        scores.PAGE_SIZE, cap=10_000)
    # 条数上限要大于一页，否则永远只有一页、翻页控件形同虚设
    assert scores.DEFAULT_LIMIT > scores.PAGE_SIZE


def test_scores_route_is_a_dedicated_page_with_callbacks():
    """
    `/scores` 必须登记成专属页且带回调 —— 只登记 render 不登记
    `register_callbacks`，页面画得出来但换象限没反应，属于"声明了用不了"。
    """
    from app.web import pages
    page = pages.PAGES.get('/scores')
    assert page is not None, '/scores 没登记进 PAGES，会退回配置页、选票框消失'
    assert callable(page['render'])
    assert callable(page.get('register_callbacks'))


def test_queries_registered():
    assert 'q_quadrant_pick' in q.names()
    assert 'q_quadrant_counts' in q.names()


def test_buttons_count_the_same_day_the_result_table_uses():
    """
    ★2026-08-31 实测抓到的骗人现象：四个按钮全写"无数据"，下面的结果表却有票。

    `_counts()` 原来不传日期 → `q_quadrant_counts` 走 `_d()`（**日历上**最新的
    已收盘交易日，15:00 后就算当天），而结果表用的是"最近一个有评分的交易日"。
    收盘后到日常链路跑完之间，两者不是同一天，按钮就集体变成"无数据"。
    这里核的是"按钮和表同一天"，不是某个具体数字。
    """
    from app.web.pages import scores
    day = q.q_score_dates(limit=1)
    if not day:
        pytest.skip('fct.score 是空的')
    day = day[0]['trade_date']
    counts = scores._counts(day)
    assert counts, f'{day} 这天四个象限一个都没数出来'
    rows = q.q_quadrant_pick(quadrant='Q1', trade_date=day, limit=5)
    if rows:
        assert counts.get('Q1'), 'Q1 有票但按钮上写"无数据"'
        assert counts['Q1'] >= len(rows)
