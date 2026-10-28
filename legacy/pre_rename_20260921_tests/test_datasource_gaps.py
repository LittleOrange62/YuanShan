# -*- coding: utf-8 -*-
"""
「已知缺口」要能说出缺的是什么（`/datasource` 摘要块 + 清单卡）。

这块原来只有一个数字。数字不带清单等于没说：`no_permission` / `broken` 是
**重试无用**的一类，看到"已知缺口 7"的人如果不知道是哪 7 个，只会去查网络 ——
而这两种状态查网络是白查。所以这里盯三件事：

1. 数量与清单必须同源（摘要块上的 7 与清单卡里的 7 条是同一次查询）；
2. 每条都要能回答"为什么算缺口"和"怎么消除"，而且这两句是**推出来的**，
   不是逐个 ds_key 写死的；
3. `degraded` / `down` 不算缺口 —— 它们是真故障，该重试或换源。
"""
from __future__ import annotations

import pytest

from app.datasource import registry as ds_reg
from app.db import engine
from app.web.pages import datasource as page


@pytest.fixture(scope='module')
def gaps():
    return ds_reg.gaps()


def test_gaps_are_exactly_the_sticky_health_rows(gaps):
    want = engine.scalar(
        'SELECT count(*) FROM config.datasource WHERE is_active '
        'AND health = ANY(%s)', (list(ds_reg.STICKY),))
    assert len(gaps) == want
    assert all(g['health'] in ds_reg.STICKY for g in gaps)


def test_real_failures_are_not_counted_as_gaps(gaps):
    """
    `degraded` / `down` 是"本该通而现在不通"，重试或换源能解决，不进这张清单。
    混进来的后果是：真故障被当成"已知情况"放着不管。
    """
    keys = {g['ds_key'] for g in gaps}
    broken_now = engine.query(
        "SELECT ds_key FROM config.datasource WHERE is_active "
        "AND health IN ('degraded', 'down')")
    for r in broken_now:
        assert r['ds_key'] not in keys


def test_every_gap_says_why_and_how_to_clear_it(gaps):
    assert gaps, '当前一个缺口都没有，这条测试就没意义了（不是失败）'
    for g in gaps:
        assert g['reason'], f"{g['ds_key']} 没说为什么算缺口"
        assert g['fix'], f"{g['ds_key']} 没说怎么消除"
        if g['health'] == ds_reg.NO_PERMISSION and g.get('auth_ref'):
            # 无权限的源要指出密钥变量名，否则"怎么消除"落不到地
            assert g['auth_ref'] in g['fix']
        if g['health'] == ds_reg.BROKEN:
            assert '上游' in g['fix'], 'broken 是上游的问题，不该让人去查自己'


def test_permission_and_probe_error_are_not_conflated(gaps):
    """
    ⚠️ 这两件事必须分开说：`health=no_permission` 是上次 40203（付费档位不够）
    留下的结论，而当前 `last_err` 可能是"环境变量未设置"。
    合起来讲会让人以为"填个 token 就有权限了"。
    """
    for g in gaps:
        if g['health'] == ds_reg.NO_PERMISSION and '未设置' in (g.get('last_err') or ''):
            assert g['probe_note'], f"{g['ds_key']} 该提示权限结论无法复验"
            assert '复验' in g['probe_note']


def test_hover_note_lists_every_gap(gaps):
    note = page._gap_note()
    for g in gaps:
        assert g['ds_key'] in note, f"悬浮说明里没有 {g['ds_key']}"
        assert g['name'] in note
    assert '重试无用' in note, '要先说清这一类和真故障的区别'


def test_gap_table_has_the_columns_that_answer_the_question(gaps):
    rows = page._gap_rows()
    assert len(rows) == len(gaps)
    for c in ('类型', '数据源', 'key', '层级', '为什么算缺口', '怎么消除',
              '影响与替代路径', '上次成功', '当前探测报错'):
        assert c in rows[0], f'清单缺 {c} 这一列'
    # 从来没通过一次的源要写明白，不能显示成空白
    never = [r for r in rows if r['上次成功'] == '从未成功过']
    assert all(r['上次成功'] for r in rows)
    assert isinstance(never, list)


def test_summary_number_and_the_list_come_from_the_same_place():
    """
    摘要块上的数字和清单卡的条数**必须一致**。两处各查一遍 SQL 迟早出现
    "卡上写 7、清单里 6 条"，而这种不一致最难被发现。
    """
    by = {r['health']: r['n'] for r in engine.query(
        'SELECT health, count(*) n FROM config.datasource '
        'WHERE is_active GROUP BY 1')}
    shown = by.get('no_permission', 0) + by.get('broken', 0)
    assert shown == len(page._gap_rows())


def test_card_and_stat_both_render():
    assert page._gap_card() is not None
    cols = page._summary()
    assert len(cols) == 4
    note = engine.query_one(
        "SELECT * FROM config.help_note WHERE target = 'datasource.gaps'")
    assert note, 'config.help_note 里没有缺口清单卡的说明'
    assert 'token' in (note['caveat'] or '').lower(), \
        '说明里要写明 health 与当前报错可能讲两件事'
