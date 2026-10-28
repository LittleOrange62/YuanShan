# -*- coding: utf-8 -*-
"""
板块启动模块（`fct.ignition` 上层的两张卡）的验收测试。

这一批锁的是 2026-09-01 那三条用户反馈：

1. **"缺少日期，不知道这个热度指的是啥时候"** —— 板块页每张卡的标题都必须带
   `{d}` 占位符，并且渲染后要变成真实日期（`ui._title_date`）。
2. **"20 日看不出接下来的趋势，改成 10 天"** + "为什么 8.31 的数据没有" ——
   窗口默认值和"曲线右端点必须是最新交易日"都在这里断言。
   （查过：08-31 一直都在数据里，看不出来是因为卡上没有日期。）
3. **"就是无脑展示，缺少数据加工"** —— `fct.ignition` 的四档模型每天都在算，
   但 2026-09-01 之前 `app/web/` 一处都没引用。这里保证它至少有一张卡在用。

都跑在真库上：档位是回测反推的阈值筛出来的，造假数据只能验证 SQL 语法，
验证不了"行业板块永远凑不满五要素"这类真实缺口。
"""
from __future__ import annotations

import pytest

from app.db import engine
from app.db.repo import registry as reg_repo
from app.web import queries as q
from app.web import ui

FACTORS = ('点火', '量能', '广度', '低位', '波动')


@pytest.fixture(scope='module')
def latest():
    d = engine.scalar("SELECT max(trade_date) FROM fct.ignition "
                      " WHERE scope = 'sector'")
    if d is None:
        pytest.skip('fct.ignition 里没有板块行，先跑 d4_ignition.py')
    return d


@pytest.fixture(scope='module')
def cand(latest):
    return q.q_sector_ignite(trade_date=latest)


# ---------------------------------------------------------------- 启动候选

def test_only_the_requested_stages_come_back(latest):
    rows = q.q_sector_ignite(trade_date=latest, stages=('ignite',))
    assert all(r['阶段'] == '① 点火' for r in rows), {r['阶段'] for r in rows}
    # 默认那三档里不能混进退潮 —— 退潮是反向清单，混在候选里会被当成候选
    assert all(r['阶段'] != '④ 退潮' for r in q.q_sector_ignite(trade_date=latest))


def test_factors_are_scores_between_0_and_100(cand):
    if not cand:
        pytest.skip('当天没有任何启动候选')
    for r in cand:
        for k in FACTORS:
            if r[k] is not None:
                assert 0 <= float(r[k]) <= 100, (r['name'], k, r[k])
        assert 1 <= int(r['有效要素']) <= 5, (r['name'], r['有效要素'])


def test_industry_sectors_can_never_be_ignite(latest):
    """
    `md.sector_daily` 里 230 个行业板块一条涨跌家数都没有，广度这一维缺了就
    凑不满五要素 —— 所以行业永远不会是「点火」。实测过放宽（缺广度就跳过）
    是负期望，这条断言是防止有人"顺手补一个 coalesce"把它放宽回去。
    """
    rows = q.q_sector_ignite(trade_date=latest, stages=('ignite',))
    assert all(r['sector_type'] != '行业' for r in rows), \
        [r['name'] for r in rows if r['sector_type'] == '行业']


def test_default_order_is_stage_then_score(cand):
    if len(cand) < 2:
        pytest.skip('候选不足两行')
    keys = [(r['阶段'], -float(r['综合分'] or 0)) for r in cand]
    assert keys == sorted(keys), '默认顺序不是「档位优先、同档按综合分」'


# ---------------------------------------------------------------- 阶段分布

def test_stage_distribution_counts_match_the_universe(latest):
    rows = q.q_sector_stage_dist(trade_date=latest)
    assert rows, '阶段分布是空的'
    for r in rows:
        assert r['变化'] == r['今日家数'] - r['上一交易日'], r['阶段']
    total = sum(int(r['今日家数']) for r in rows)
    want = engine.scalar(
        """
        SELECT count(*) FROM fct.ignition i
          JOIN ref.sector_info si ON si.sector_code = i.obj_key
         WHERE i.scope = 'sector' AND i.trade_date = %s
           AND si.name !~ '^[0-9]+$'
           AND si.sector_type IN ('行业', '概念')
        """, (latest,))
    assert total == want, f'四档合计 {total} ≠ 板块数 {want}'


def test_stage_names_agree_between_the_two_cards(latest, cand):
    """两张卡的档位文案必须一样 —— 一处写"点火"一处写"启动"就没法对着看。"""
    dist = {r['阶段'] for r in q.q_sector_stage_dist(trade_date=latest)}
    assert {r['阶段'] for r in cand} <= dist


# ---------------------------------------------------------------- 日期与窗口

def test_every_sector_card_title_carries_the_date():
    """用户反馈「缺少日期」：这一页的每张卡标题都要有 `{d}` 占位符。"""
    missing = [r['slot'] for r in reg_repo.layout('sector')
               if '{d}' not in (r['title'] or '')]
    assert not missing, f'这些槽位的标题没带日期：{missing}'


def test_title_placeholder_becomes_a_real_date():
    assert ui._title_date('热度榜（{d}）', {}, {}) \
        == f'热度榜（{q.latest_date()}）'
    # 没有占位符就原样返回，不自动追加 —— 哪张卡显示日期由布局决定
    assert ui._title_date('热度榜', {}, {}) == '热度榜'
    # 页面级参数优先于卡片默认值，和查询里的 `_d()` 同一套优先级
    assert ui._title_date('x{d}', {'params': {'trade_date': '2020-01-02'}},
                          {'trade_date': '2020-03-04'}) == 'x2020-03-04'


def test_heat_trend_window_is_ten_days_and_ends_today():
    """
    窗口 10 天（用户 2026-09-01 指定），且**右端点必须是最新交易日** ——
    "为什么 8.31 的数据没有"那次排查的结论是数据一直都在，这条断言把它钉住。
    """
    import inspect
    sig = inspect.signature(q.q_sector_heat_trend)
    assert sig.parameters['lookback'].default == 10
    rows = q.q_sector_heat_trend()
    if not rows:
        pytest.skip('板块热度还没算过')
    days = sorted({r['trade_date'] for r in rows})
    assert len(days) <= 10
    assert days[-1] == q.latest_date(), '曲线右端点不是最新交易日'


def test_heat_detail_shows_the_stage_column():
    rows = q.q_sector_heat()
    if not rows:
        pytest.skip('板块热度还没算过')
    assert '阶段' in rows[0]
    assert all(str(r['阶段'])[0] in '①②③④⑤' for r in rows)


def test_ignition_is_actually_wired_into_a_card():
    """
    这个模型 2026-09-01 之前一处都没显示（`app/web/` 里 grep 不到 ignition）。
    档位表在库里、页面上看不到，等于没算 —— 这条断言拦的是那种状态。
    """
    refs = {r['card_ref'] for r in reg_repo.layout('sector')}
    assert {'q_sector_ignite', 'q_sector_stage_dist'} <= refs
