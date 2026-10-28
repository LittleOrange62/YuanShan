# -*- coding: utf-8 -*-
"""板块名 → 东财板块页（BK 码）这一条链的回归。

用户 2026-10-15 点名：「热度榜与五要素 里面的板块也要有超链接可以点开，比如『油气开采』对应
`https://quote.eastmoney.com/bk/90.BK1276.html`，多测试几个，把这个功能完善一下。」

这里钉四件事（打桩的不连库，最后一条不桩、真读库）：
① 对表判据 `resolve()` 只有精确同名与 Ⅱ/Ⅲ 后缀两条路，跨类不链、对不上不编码；
② 作业遇到半截清单**整批不刷**（半截比缺数更坏：会把昨天对好的码盖成 miss）；
③ 读侧两块屏幕共用同一份读法，没码就是 None（界面据此退回纯文本）；
④ DDL 与加工段接线还在（手抄的步数与那张表是同一件事的两半）。
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re

import pandas as pd
import pytest

from app.db import engine
from duanxian import em_board_link as EBL
from ingest.jobs import c24_em_board_link as C24

TABLES = {'行业': {'半导体': 'BK1036', '油气开采Ⅱ': 'BK1276', '机械Ⅲ': 'BK1599',
                   '白酒': 'BK0465', '银行': 'BK1283'},
          '概念': {'白酒': 'BK0896', '储能': 'BK1128'}}


# ------------------------------------------------------------------ ① 判据只住一处

def test_精确同名优先于一切():
    assert C24.resolve('半导体', '行业', TABLES) == ('BK1036', '半导体', 'exact')


def test_差一个级别后缀才对得上时认下来并记下东财那个名():
    """用户举的例子就是这个形状：屏上「油气开采」，东财那侧叫「油气开采Ⅱ」。"""
    assert C24.resolve('油气开采', '行业', TABLES) == ('BK1276', '油气开采Ⅱ', 'roman_ii')


def test_没有二级才退到三级():
    only_three = {'行业': {'机械Ⅲ': 'BK1599'}}
    assert C24.resolve('机械', '行业', only_three) == ('BK1599', '机械Ⅲ', 'roman_iii')
    both = {'行业': {'机械Ⅱ': 'BK1500', '机械Ⅲ': 'BK1599'}}
    assert C24.resolve('机械', '行业', both) == ('BK1500', '机械Ⅱ', 'roman_ii'), 'Ⅱ 必须排在 Ⅲ 之前'


def test_跨类同名一律不链():
    """东财概念榜里有个「白酒」，我方行业档也叫「白酒」—— 撞名不等于同一个族。"""
    assert C24.resolve('白酒', '行业', TABLES) == ('BK0465', '白酒', 'exact'), '行业榜里同名的先算'
    only_concept = {'行业': {}, '概念': TABLES['概念']}
    assert C24.resolve('白酒', '行业', only_concept) == (None, None, 'miss')
    assert C24.resolve('白酒', '概念', only_concept) == ('BK0896', '白酒', 'exact')


def test_对不上不编码_不认识的档也不链():
    assert C24.resolve('旅游及酒店', '行业', TABLES) == (None, None, 'miss')
    assert C24.resolve('半导体', '风格', TABLES) == (None, None, 'miss'), '东财没有风格族，不该拿行业榜凑'


def test_读侧只认行业与概念两档():
    """风格 / 市场级统计连库都不该去问 —— 问了也不会给链接，但会把"这一档为什么全空"糊掉。"""
    assert EBL.LINKABLE_KINDS == ('行业', '概念')
    calls = []
    monkey_no_db = lambda *a, **k: calls.append(a) or None
    EBL.pg.fetch_df, real = monkey_no_db, EBL.pg.fetch_df
    try:
        assert EBL.load('风格') == {}
    finally:
        EBL.pg.fetch_df = real
    assert calls == [], "风格档不该发那条 SQL"


# ------------------------------------------------------------------ ② 作业：半截清单整批不刷

def _stub_job(monkeypatch, *, names, tables):
    written: list[dict] = []

    def _executemany(sql, rows, **kw):
        written.extend(rows)
        return len(rows)

    monkeypatch.setattr(C24, 'our_names', lambda: names)
    monkeypatch.setattr(C24, 'fetch_lists', lambda: tables)
    monkeypatch.setattr(C24.engine, 'query_one', lambda *a, **k: {'batch_id': 1})
    monkeypatch.setattr(C24.engine, 'execute', lambda *a, **k: None)
    monkeypatch.setattr(C24.engine, 'executemany', _executemany)
    return written


def _full_lists():
    """把两张清单垫到 `MIN_ROWS` 以上（判据只认行数，不认内容），再放我要测的那几个名字。"""
    out = {}
    for kind, base in (('行业', TABLES['行业']), ('概念', TABLES['概念'])):
        rows = dict(base)
        pad = 0
        while len(rows) < C24.MIN_ROWS:
            rows[f'垫行{pad}'] = f'BK{pad:04d}'
            pad += 1
        out[kind] = rows
    return out


def test_清单只有几行时判为半截_一条都不写(monkeypatch):
    """半截比缺数更坏：写进去会把昨天对好的码盖成 miss，屏幕当场少一片链接。"""
    short = {'行业': {'半导体': 'BK1036'}, '概念': {}}
    written = _stub_job(monkeypatch, names=[{'sector_kind': '行业', 'sector_name': '半导体'}], tables=short)
    assert C24.main([]) == 2
    assert written == []


def test_名单一条都没读到是真失败不是空表(monkeypatch):
    """底表读不到 ≠ 今天没有板块 —— 返回 1 并说清，别刷成一张空对表让全站链接一起消失。"""
    _stub_job(monkeypatch, names=[], tables=TABLES)
    assert C24.main([]) == 1


def test_接口异常不写也不说成没有板块(monkeypatch):
    def boom():
        raise ConnectionError('东财清单抓不到')
    monkeypatch.setattr(C24, 'our_names', lambda: [{'sector_kind': '行业', 'sector_name': '半导体'}])
    monkeypatch.setattr(C24, 'fetch_lists', boom)
    assert C24.main([]) == 1


def test_正常一批会写进对表_并把miss行也留着(monkeypatch):
    """对不上（miss）要留痕：与「这颗名字从来没被试过」是两件事。"""
    names = [{'sector_kind': '行业', 'sector_name': '半导体'},
             {'sector_kind': '行业', 'sector_name': '旅游及酒店'}]
    written = _stub_job(monkeypatch, names=names, tables=_full_lists())
    assert C24.main([]) == 0
    kinds = {r['sector_name']: r for r in written}
    assert kinds['半导体']['match_kind'] == 'exact' and kinds['半导体']['em_board_code'] == 'BK1036'
    assert kinds['旅游及酒店']['match_kind'] == 'miss'
    assert kinds['旅游及酒店']['em_board_code'] is None, 'miss 行留痕但不许带一个猜出来的码'


# ------------------------------------------------------------------ ③ 读侧一份、没码就是 None

def _stub_read(monkeypatch, links, status):
    def fake(sql, params=()):
        text = " ".join(str(sql).split())
        if "em_board_link" not in text:
            return None
        return pd.DataFrame([status]) if "group by" in text else pd.DataFrame(links)

    monkeypatch.setattr(EBL.pg, 'fetch_df', fake)


def test_decorate只挂真有的码_其余留None(monkeypatch):
    rows = [{'name': '半导体', 'sector_type': '行业'}, {'name': '油气开采', 'sector_type': '行业'},
            {'name': '旅游及酒店', 'sector_type': '行业'}, {'name': '储能', 'sector_type': '风格'}]
    links = [{'sector_name': '半导体', 'em_board_code': 'BK1036', 'em_board_name': '半导体',
              'match_kind': 'exact', 'tried_at': '2026-10-15'},
             {'sector_name': '油气开采', 'em_board_code': 'BK1276', 'em_board_name': '油气开采Ⅱ',
              'match_kind': 'roman_ii', 'tried_at': '2026-10-15'}]
    _stub_read(monkeypatch, links, {'sector_kind': '行业', 'linked': 2, 'total': 613,
                                    'tried_at': '2026-10-15'})
    EBL.decorate(rows, kind_of=lambda r: r['sector_type'], name_of=lambda r: r['name'])
    assert [r['em_board_code'] for r in rows] == ['BK1036', 'BK1276', None, None]
    assert rows[1]['em_board_name'] == '油气开采Ⅱ', '东财那一侧的真名要交出去，点开页眉是它'
    st = EBL.status()
    assert st['available'] is True and st['kinds']['行业']['linked'] == 2


def test_对表不可访问时status交availableFalse加原因而不是空表(monkeypatch):
    """`load()` 兜空 + `status()` 兜不可用：屏幕要说"对表读不到"，不能说成"这些板块没有东财页"。"""
    monkeypatch.setattr(EBL.pg, 'fetch_df', lambda sql, params=(): None)
    assert EBL.load('行业') == {}
    st = EBL.status()
    assert st['available'] is False and st['reason']


def test_对表是空表时也说读不到而不是零覆盖(monkeypatch):
    """表在、行没有（作业从没跑过）与"跑过但一个都没对上"是两件事，都必须在屏幕上分别念出来。"""
    monkeypatch.setattr(EBL.pg, 'fetch_df',
                        lambda sql, params=(): pd.DataFrame([]))
    st = EBL.status()
    assert st['available'] is False
    assert 'c24_em_board_link' in st['reason']


# ------------------------------------------------------------------ ④ 接线与库

def test_加工段里有boardlink这一步():
    src = (pathlib.Path(__file__).resolve().parents[1] / 'ingest' / 'jobs' / 'e1_fill_all.py').read_text(encoding='utf-8')
    assert "('boardlink'," in src, '步骤清单里没有 boardlink —— 对表就只能手工刷，屏幕上的链接会一天天少下去'
    assert re.search(r"'boardlink':\s*\('ingest\.jobs\.c24_em_board_link'", src), 'STAGES 有条目但没接执行入口'
    timeouts = src.split('STEP_TIMEOUTS')[1]
    assert re.search(r"'boardlink':\s*\d+", timeouts[:6000]), '这一步没有自己的超时额度'


def test_库里那张对表真的有行而且用户那个例子对得上():
    """不打桩、真读库的一条：判据与接线全对但表是空的，屏幕上依旧一个链接都没有。"""
    if not EBL.pg.enabled():
        pytest.skip('本机库没通，这条要真库')
    rows = engine.query(
        """select sector_name, em_board_code, em_board_name, match_kind, tried_at
             from reference_data.em_board_link
            where sector_kind = '行业' and sector_name in ('油气开采','半导体','银行','旅游及酒店')""",
        role='reader')
    got = {r['sector_name']: r for r in rows}
    assert got, '对表里连这四个名字都没有 —— 加工段 boardlink 那一步没跑过'
    # 用户点名的那一个必须正是他给的码（BK1276），且是"差一个后缀"那条规则换来的
    assert got['油气开采']['em_board_code'] == 'BK1276'
    assert got['油气开采']['match_kind'] == 'roman_ii'
    assert got['油气开采']['em_board_name'] == '油气开采Ⅱ'
    assert got['半导体']['match_kind'] == 'exact' and got['银行']['match_kind'] == 'exact'
    # 对不上名的那一个必须在表里、且没有码 —— 留痕，而不是被静默丢掉
    assert got['旅游及酒店']['em_board_code'] is None and got['旅游及酒店']['match_kind'] == 'miss'
    assert all(isinstance(r['tried_at'].date(), dt.date) and r['tried_at'].date() >= dt.date(2026, 10, 15)
               for r in rows), '对表新鲜度是这一天（每天由加工段刷）'
