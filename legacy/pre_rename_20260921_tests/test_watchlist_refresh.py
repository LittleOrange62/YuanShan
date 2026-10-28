# -*- coding: utf-8 -*-
"""
自选维护台「该刷什么、不该刷什么」的验收测试。

`_refresh()` 默认一个 Output 都不动，各分支按需打开 `groups` / `members` /
`opts` / `head`。这条规矩有两个来由，都被这里锁住：

1. 一步一回车地录票时，每下回车都重建整页 DOM 会一直闪，很晃眼；
2. **就地编辑更狠** —— 重建表格会把正在编辑的单元格连光标一起换掉，
   连着改两行就没法做了。
"""
from __future__ import annotations

from dash import no_update

from app.web.pages import watchlist as wl

# `_refresh` 返回值的下标，跟回调上的 Output 列表一一对应。
I_GROUPS, I_MEMBERS, I_MSG, I_HEAD, I_TOPTS = 0, 1, 2, 3, 4
I_Q, I_COST, I_SNOTE, I_FOCUS, I_NEWNAME = 5, 6, 7, 8, 9
I_CAND_OPT, I_CAND_VAL, I_HINT, I_STEP = 10, 11, 12, 13
I_GID, I_MODAL, I_MODAL_BODY = 14, 15, 16


def _a_group():
    return wl._first_group()


def test_refresh_returns_exactly_the_output_count():
    out = wl._refresh('x', _a_group())
    assert len(out) == wl.N_OUT == 17
    assert len(wl.NOOP) == wl.N_OUT


def test_nothing_refreshed_by_default():
    """默认全 no_update：往下走一步 / 提示类的分支就是这样。"""
    out = wl._refresh('回车＝加入本组（备注可留空）', _a_group(),
                      'text-muted', focus='wl-snote', seq=3)
    for i in (I_GROUPS, I_MEMBERS, I_HEAD, I_TOPTS):
        assert out[i] is no_update, f'第 {i} 个输出应当不动'
    assert '加入本组' in str(out[I_MSG].children)
    assert out[I_FOCUS] == {'id': 'wl-snote', 'n': 3}


def test_light_refresh_does_not_hit_db(monkeypatch):
    """真·不查库：取数函数换成会炸的，默认分支还能返回。"""
    def boom(*_a, **_k):
        raise AssertionError('没打开 groups/members 就不该重建表')

    monkeypatch.setattr(wl, 'groups_table', boom)
    monkeypatch.setattr(wl, 'members_table', boom)
    monkeypatch.setattr(wl, 'group_options', boom)
    out = wl._refresh('先在左边点一个组', None, 'text-danger')
    assert out[I_MSG] is not no_update


def test_write_refresh_rebuilds_both_tables():
    """写库那几下（加票 / 建组 / 移动）要重建两张表并刷「移动到」下拉。"""
    gid = _a_group()
    out = wl._refresh('已加入', gid, reset=True, focus='wl-q', seq=1,
                      groups=True, members=True)
    for i in (I_GROUPS, I_MEMBERS, I_TOPTS):
        assert out[i] is not no_update, f'第 {i} 个输出应当刷新'
    assert out[I_Q] == '' and out[I_COST] is None and out[I_SNOTE] == ''
    assert out[I_CAND_OPT] == [] and out[I_CAND_VAL] is None


def test_opts_only_refreshes_dropdown(monkeypatch):
    """改组名：刷「移动到哪个组」的下拉，但**不重建组表**（光标在格子里）。"""
    def boom(*_a, **_k):
        raise AssertionError('就地编辑不该重建组表')

    monkeypatch.setattr(wl, 'groups_table', boom)
    monkeypatch.setattr(wl, 'members_table', boom)
    out = wl._refresh('改了', _a_group(), opts=True)
    assert out[I_GROUPS] is no_update
    assert out[I_MEMBERS] is no_update
    assert out[I_TOPTS] is not no_update


def test_modal_and_gid_passthrough():
    out = wl._refresh('', 1, gid=7, modal=True, modal_body='问一句')
    assert out[I_GID] == 7
    assert out[I_MODAL] is True
    assert out[I_MODAL_BODY] == '问一句'
    # msg 传空串就是把消息区清掉，不是"不动"
    assert out[I_MSG] == ''
