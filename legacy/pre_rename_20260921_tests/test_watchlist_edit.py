# -*- coding: utf-8 -*-
"""
就地编辑（组名 / 排序 / 备注 / ★ / 成本 / 成员备注）的验收测试。

这一批是"点开单元格就能改"这件事的底座，测的是三样东西：
1. `_diff_cell` 能从两份 `data` 里认出**是哪一格被改了** —— DataTable 不会告诉
   你，只会把旧值塞进 `data_previous`；
2. 白名单之外的列**写不进去**（列名要拼进 SQL 文本，这是唯一的注入面）；
3. 左栏的默认排序键是「10日均涨跌bp」（2026-09-01 起；此前是预测操作分），
   而且这一列和下半截那张「自选组热度」卡**同一个数** —— 两处口径一漂，
   "先看哪个组"就会给出两个不同的答案。预测操作分那一列也照样比一遍。

所有写操作都在测试里当场回滚（建一个临时组、改完删掉），不动既有名单。
"""
from __future__ import annotations

import pytest

from app.db import engine
from app.web import queries as q
from app.web.pages import watchlist as wl

# ---------------------------------------------------------------- _diff_cell

def test_diff_cell_finds_the_changed_cell():
    prev = [{'a': 1, 'b': 'x'}, {'a': 2, 'b': 'y'}]
    cur = [{'a': 1, 'b': 'x'}, {'a': 2, 'b': 'YY'}]
    assert wl._diff_cell(prev, cur) == (1, 'b', 'YY', 'y')


def test_diff_cell_none_when_nothing_changed():
    rows = [{'a': 1}, {'a': 2}]
    assert wl._diff_cell(rows, [dict(r) for r in rows]) is None


def test_diff_cell_none_on_full_rebuild():
    """行数变了＝整表重画（建组/删组/换组），不能当成一次编辑去写库。"""
    assert wl._diff_cell([{'a': 1}], [{'a': 1}, {'a': 2}]) is None
    assert wl._diff_cell(None, [{'a': 1}]) is None
    assert wl._diff_cell([{'a': 1}], None) is None


# ---------------------------------------------------------------- 白名单

def test_group_field_whitelist_rejects_unknown_column():
    with pytest.raises(ValueError, match='不能改'):
        wl.update_group_field(1, '成员数', 99)
    with pytest.raises(ValueError, match='不能改'):
        wl.update_group_field(1, 'name; DROP TABLE config.watchlist_group', 'x')


def test_stock_field_whitelist_rejects_unknown_column():
    with pytest.raises(ValueError, match='不能改'):
        wl.update_stock_field(1, '600519.SH', '操作分', 99)


def test_editable_cols_are_all_whitelisted():
    """页面上开了编辑的列必须都能写库，否则改完只是白改一场。"""
    assert set(wl.GROUP_EDITABLE) >= {'组名', '排序', '备注', '重点'}
    assert set(wl.STOCK_EDITABLE) == {'成本', '备注'}


# ---------------------------------------------------------------- 真写库

@pytest.fixture
def tmp_group():
    gid = wl.create_group('__测试组_勿留__', 999)
    yield gid
    engine.execute('DELETE FROM config.watchlist_group WHERE group_id = %s', (gid,))


def test_create_group_returns_new_id(tmp_group):
    """返回的必须是 group_id 而不是影响行数 —— 建完要立刻切到它。"""
    assert isinstance(tmp_group, int)
    name = engine.scalar('SELECT name FROM config.watchlist_group '
                         'WHERE group_id = %s', (tmp_group,))
    assert name == '__测试组_勿留__'


def test_update_group_fields_one_by_one(tmp_group):
    wl.update_group_field(tmp_group, '组名', '__改过的名字__')
    wl.update_group_field(tmp_group, '排序', '7')          # 表格给的是字符串
    wl.update_group_field(tmp_group, '备注', '  两边留白  ')
    wl.update_group_field(tmp_group, '重点', '★')
    r = engine.query_one(
        'SELECT name, seq, note, is_focus FROM config.watchlist_group '
        'WHERE group_id = %s', (tmp_group,))
    assert r['name'] == '__改过的名字__'
    assert r['seq'] == 7
    assert r['note'] == '两边留白'
    assert r['is_focus'] is True
    # ★ 那一列清空＝取消重点，不是"保持原样"
    wl.update_group_field(tmp_group, '重点', '')
    assert engine.scalar('SELECT is_focus FROM config.watchlist_group '
                         'WHERE group_id = %s', (tmp_group,)) is False


def test_empty_group_name_is_refused(tmp_group):
    with pytest.raises(ValueError, match='组名不能为空'):
        wl.update_group_field(tmp_group, '组名', '   ')
    assert engine.scalar('SELECT name FROM config.watchlist_group '
                         'WHERE group_id = %s', (tmp_group,)) == '__测试组_勿留__'


def test_duplicate_group_name_raises_and_writes_nothing(tmp_group):
    other = engine.scalar(
        'SELECT name FROM config.watchlist_group WHERE group_id <> %s LIMIT 1',
        (tmp_group,))
    if other is None:
        pytest.skip('库里只有一个组，测不了重名')
    with pytest.raises(Exception, match='(?i)unique|duplicate|唯一'):
        wl.update_group_field(tmp_group, '组名', other)
    assert engine.scalar('SELECT name FROM config.watchlist_group '
                         'WHERE group_id = %s', (tmp_group,)) == '__测试组_勿留__'


def test_update_stock_cost_and_note(tmp_group):
    code = engine.scalar(
        "SELECT ts_code FROM ref.security WHERE asset_type = 'stock' "
        ' AND is_active ORDER BY ts_code LIMIT 1')
    wl.add_stock(tmp_group, code)
    wl.update_stock_field(tmp_group, code, '成本', '12.34')
    wl.update_stock_field(tmp_group, code, '备注', '就地填的')
    r = engine.query_one('SELECT cost, note FROM config.watchlist_stock '
                         'WHERE group_id = %s AND ts_code = %s',
                         (tmp_group, code))
    assert float(r['cost']) == pytest.approx(12.34)
    assert r['note'] == '就地填的'
    # 成本清空＝没记成本（NULL），不是 0 —— 0 会被读成"刚好回本"
    wl.update_stock_field(tmp_group, code, '成本', '')
    assert engine.scalar('SELECT cost FROM config.watchlist_stock '
                         'WHERE group_id = %s AND ts_code = %s',
                         (tmp_group, code)) is None


def test_negative_cost_is_refused(tmp_group):
    code = engine.scalar(
        "SELECT ts_code FROM ref.security WHERE asset_type = 'stock' "
        ' AND is_active ORDER BY ts_code LIMIT 1')
    wl.add_stock(tmp_group, code)
    with pytest.raises(ValueError, match='负数'):
        wl.update_stock_field(tmp_group, code, '成本', '-1')
    with pytest.raises(ValueError):
        wl.update_stock_field(tmp_group, code, '成本', '不是数字')


# ---------------------------------------------------------------- 左栏排序

def test_group_rows_sorted_by_chg10_asc_nulls_last():
    """默认按「10日均涨跌bp」**从低到高**（用户 2026-09-01 指定）。"""
    rows = wl.group_rows()
    assert rows, 'config.watchlist_group 是空的'
    seen_none = False
    prev = None
    for r in rows:
        if r[wl.CHG10_COL] is None:
            seen_none = True
            continue
        assert not seen_none, '算不出10日均涨跌的组必须排在最后，不能插在中间'
        if prev is not None:
            assert float(r[wl.CHG10_COL]) >= float(prev) - 1e-9
        prev = r[wl.CHG10_COL]


def test_group_rows_chg10_matches_the_heat_card():
    """左栏那一列和「自选组热度」卡的 `组内10日均涨跌bp` 必须是同一个数。"""
    heat = {r['group_name']: r['avg_chg10_bp'] for r in q.q_group_heat()}
    if not heat:
        pytest.skip('还没评分，q_group_heat 是空的')
    checked = 0
    for r in wl.group_rows():
        name = wl.split_focus(r['组名'])[0]
        if name in heat and heat[name] is not None:
            assert r[wl.CHG10_COL] == heat[name], f'{name} 两处对不上'
            checked += 1
    assert checked > 0


def test_group_rows_pred_matches_the_heat_card():
    """左栏那一列分数和下半截「自选组热度」卡的 pred_oper 必须是同一个数。"""
    heat = {r['group_name']: r['pred_oper'] for r in q.q_group_heat()}
    if not heat:
        pytest.skip('还没评分，q_group_heat 是空的')
    checked = 0
    for r in wl.group_rows():
        # 组名单元格带 ★ 前缀（重点组），比之前要先剥掉
        name = wl.split_focus(r['组名'])[0]
        if name in heat and heat[name] is not None:
            assert r['预测操作分'] == heat[name], f'{name} 两处对不上'
            checked += 1
    assert checked > 0


def test_group_rows_column_order_is_locked():
    """列序是产品决定：组名在最前、组号在最后（表里隐藏、用来翻 group_id）。"""
    cols = list(wl.group_rows()[0])
    assert cols[0] == '组名'
    assert cols[-1] == '组号'
    assert '预测操作分' in cols and '成员数' in cols
    assert wl.CHG10_COL in cols          # 2026-09-01 加的默认排序列
    # 2026-08-30：★ 并进组名单元格，不再单独占一列
    assert '重点' not in cols


def test_focus_shows_as_a_star_prefix_in_the_name_cell():
    """重点组的组名单元格必须以 ★ 开头 —— 页面上靠它认重点，没有别的列了。"""
    rows = wl.group_rows()
    focus_ids = {int(r['group_id']) for r in engine.query(
        'SELECT group_id FROM config.watchlist_group WHERE is_focus')}
    if not focus_ids:
        pytest.skip('库里没有重点组')
    for r in rows:
        starred = str(r['组名']).startswith(wl.FOCUS_MARK)
        assert starred is (int(r['组号']) in focus_ids), r['组名']


def test_split_focus_strips_the_star_and_reads_the_flag():
    assert wl.split_focus('★ 打板') == ('打板', True)
    assert wl.split_focus('* 打板') == ('打板', True)   # 半角星也认
    assert wl.split_focus('打板') == ('打板', False)
    assert wl.split_focus('  ') == (None, False)


def test_editing_the_name_cell_toggles_focus(tmp_group):
    """
    一格写两列：组名单元格里的 ★ 就是 is_focus。
    只写 name 的话星号一提交就没了，看起来像"点了没反应"。
    """
    wl.update_group_field(tmp_group, '组名', '★ __测试组_勿留__')
    r = engine.query_one('SELECT name, is_focus FROM config.watchlist_group '
                         'WHERE group_id = %s', (tmp_group,))
    assert r['name'] == '__测试组_勿留__', '星号不能写进库里的 name'
    assert r['is_focus'] is True
    # 删掉星号＝取消重点
    wl.update_group_field(tmp_group, '组名', '__测试组_勿留__')
    assert engine.scalar('SELECT is_focus FROM config.watchlist_group '
                         'WHERE group_id = %s', (tmp_group,)) is False



def test_group_pred_map_keys_are_group_ids():
    m = q.group_pred_map()
    ids = {int(r['组号']) for r in wl.group_rows()}
    assert set(m) <= ids
    assert all(isinstance(k, int) for k in m)


# ---------------------------------------------------------------- 勾选取代码

def test_selected_codes_reads_the_hidden_raw_code_column():
    """
    代码必须从隐藏的 `_code` 列取。显示的「代码」列已经被 ui.table 换成了
    Markdown 链接，从那串字面量里抠代码早晚出错。
    """
    data = [{'代码': '[600519.SH](https://x)', wl.CODE_COL: '600519.SH'},
            {'代码': '[000001.SZ](https://x)', wl.CODE_COL: '000001.SZ'},
            {'代码': '[300750.SZ](https://x)', wl.CODE_COL: '300750.SZ'}]
    assert wl.selected_codes(data, [0, 2]) == ['600519.SH', '300750.SZ']
    assert wl.selected_codes(data, []) == []
    assert wl.selected_codes(data, None) == []
    # 越界的下标直接忽略，不炸
    assert wl.selected_codes(data, [99]) == []


def test_members_table_close_columns_run_oldest_to_newest():
    """
    收盘价列必须**从早到晚**排（左 08-24 → 右 08-28）：一行收盘价读的是一条走势，
    从左往右就该是时间往前走。反过来排的时候，"这只票在涨还是在跌"要倒着读。
    """
    gid = engine.scalar(
        'SELECT group_id FROM config.watchlist_stock GROUP BY group_id '
        'ORDER BY count(*) DESC LIMIT 1')
    if gid is None:
        pytest.skip('没有任何组里有票')
    tbl = wl.members_table(gid).children[1]
    ids = [c['id'] for c in tbl.columns]
    days = [c for c in ids if len(c) == 5 and c[2] == '-' and
            c.replace('-', '').isdigit()]
    assert len(days) >= 2, f'没认出收盘价列：{ids}'
    assert days == sorted(days), f'收盘价列不是从早到晚：{days}'
    # 而且必须紧跟在「详情」后面，不散落在其它列中间
    assert ids[ids.index(days[0]) - 1] == '详情'
    assert ids[ids.index(days[0]):ids.index(days[0]) + len(days)] == days


def test_members_table_carries_the_hidden_code_column():
    gid = engine.scalar(
        'SELECT group_id FROM config.watchlist_stock GROUP BY group_id '
        'ORDER BY count(*) DESC LIMIT 1')
    if gid is None:
        pytest.skip('没有任何组里有票')
    div = wl.members_table(gid)
    tbl = div.children[1]
    assert wl.CODE_COL in tbl.hidden_columns
    assert all(r.get(wl.CODE_COL) for r in tbl.data)
    editable = {c['id'] for c in tbl.columns if c.get('editable')}
    assert editable == {'成本', '备注'}
    assert tbl.row_selectable == 'multi'


def test_groups_table_is_editable_and_single_select():
    gid = wl._first_group()
    tbl = wl.groups_table(gid)
    editable = {c['id'] for c in tbl.columns if c.get('editable')}
    assert editable == {'组名', '排序', '备注'}
    assert tbl.row_selectable == 'single'
    assert tbl.selected_rows and int(tbl.data[tbl.selected_rows[0]]['组号']) == gid
    # 「重点」列连同它的下拉一起去掉了（★ 现在贴在组名单元格里）。
    # 没传 dropdowns 时 DataTable 上压根没有 `dropdown` 这个属性，所以用 getattr
    assert '重点' not in (getattr(tbl, 'dropdown', None) or {})
    assert '重点' not in {c['id'] for c in tbl.columns}
    assert '组号' in tbl.hidden_columns


def test_both_maintenance_cards_carry_a_note():
    """
    维护台那两张卡是手写的，不在 config.page_layout 里，所以
    tests/test_card_notes.py 管不到它们 —— 这条测试补上。
    说明里必须能看出取数来源和预测操作分的算法。
    """
    from app.web import ui

    for key in ('wl_groups', 'wl_members'):
        note = ui.CARD_HELP.get(key)
        assert note, f'{key} 没有说明'
        assert 'config.watchlist' in note, f'{key} 的说明没提数据来源'
    groups_note = ui.CARD_HELP['wl_groups']
    for bit in ('成交额加权', 'regr_slope', '截距 + 斜率', 'is_focus',
                '任意一格'):
        assert bit in groups_note, f'自选组说明里缺「{bit}」'
    # 2026-09-01 用户反馈"操作分是什么意思、这边缺一个说明"：成员表的说明必须
    # 写清这一列的**公式 / 量纲 / 能当什么参考 / 不能当什么用**，光写一个
    # `0.40×X+0.60×Y` 不够 —— 那句话解释不了"看到 30 分该做什么"。
    members_note = ui.CARD_HELP['wl_members']
    for bit in ('0.40', '个股质量X', '环境共振Y', 'd2_scores.py',
                '−100 ~ +100', '可以作为什么的参考', '不能当什么用',
                'Q1', 'Q4', '10日振幅'):
        assert bit in members_note, f'成员表说明里缺「{bit}」'
