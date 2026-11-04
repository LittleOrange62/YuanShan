# -*- coding: utf-8 -*-
"""「个股信息」读侧与「全指标总账」（`duanxian/stock_facts.py` / `duanxian/stock_metrics_ledger.py`）。

总账这一层的存在理由只有一个：汇总，不复制。所以这里最关键的一条是
**字段清单必须是各模块那份 definitions 的活对象** —— 复制一份措辞＝第二份真相，
迟早出现"页面说的是旧的、接口说的是新的"（本项目在步数、涨停价口径上犯过）。
真库那两条不打桩：`stock_profile` 一码多行、板块成员数曾恒为 1，都是实测抓出来的。
"""
from __future__ import annotations

import datetime as dt
import inspect
import json
import re

import pytest

from duanxian import stock_facts as SK
from duanxian import stock_metrics_ledger as L


# ------------------------------------------------------------------ 总账不复制措辞

def test_三块新屏的字段是活取的_不是抄的():
    before = len(SK.DEFINITIONS)
    SK.DEFINITIONS['__probe__'] = '临时加的一条'
    try:
        got = L.ledger()
        facts = [b for b in got['blocks'] if b['key'] == 'facts'][0]
        assert any(f['name'] == '__probe__' for f in facts['fields']), \
            '总账没跟着模块的定义字典变 ⇒ 它抄了一份，改那边这里不会动'
    finally:
        SK.DEFINITIONS.pop('__probe__', None)
    assert L.ledger()['blocks'][0]['field_count'] == before


def test_没有definitions的块只登记出处并说明为什么没有():
    got = L.ledger()
    for b in got['blocks']:
        if b['key'] in ('facts', 'financials', 'similarity', 'pv'):
            assert b['fields'], f'{b["key"]} 该交出字段清单'
        else:
            assert not b['fields'] and b['fields_note'], \
                f'{b["key"]} 没有独立 definitions，就该只登记出处并说明（不能硬凑）'
            assert b['endpoint'] and b['source'], f'{b["key"]} 连出处都没登记'


def test_块序与页面挂载顺序一致():
    # 2026-10-28 用户点名「量价走势移动到这只票的量化档案的前面」⇒ pv 提到第 2 位。
    # 另一份真源是前端 JSX（`StockReview.tsx`），由 `frontend/test/stockReviewReads.test.ts` 钉住：
    # 只改这里＝屏幕上没换，总账这一屏先变了 —— 两边必须一起改。
    assert [b['key'] for b in L.BLOCKS] == \
        ['facts', 'pv', 'dossier', 'trend', 'financials', 'similarity', 'research', 'debate']


def test_总账里没有的那个块不许冒充有指标():
    got = L.ledger()
    assert 'caveats' in got and any(c['code'] == 'not_a_second_copy' for c in got['caveats'])
    assert got['how_to_read'] and got['spec'] == 'ledger-1'


# ------------------------------------------------------------------ 个股信息读侧

def test_没交代码与查无此码都只交available加reason():
    for d in (SK.board(''), SK.board('999999.SZ')):
        assert d['available'] is False and d['reason']
        for k in ('session', 'profile', 'sectors', 'groups', 'rows'):
            assert k not in d, f'不可用还带着 {k} 键，页面会读成"有但为空"'


def test_caveats都是code加text的形状_共用组件按这个吃():
    d = SK.board('999999.SZ')
    assert 'caveats' not in d          # 不可用时不下策，也不带空壳
    ok = SK.board('600519.SH') if SK._identity('600519.SH') else None
    if ok:
        for c in ok['caveats']:
            assert isinstance(c, dict) and c.get('code') and c.get('text')
            assert '**' not in c['text'], '服务到屏幕的散文里不许留 markdown 粗体标记'


def test_档案表一码多行必须取最近那一天():
    """`reference_data.stock_profile` 是按日存的（实测 600519 有 3 行）⇒ 不排序会拿到任意一天。"""
    from app.db import engine
    rows = engine.query("select hq_date from reference_data.stock_profile where security_code = %s "
                        "order by hq_date desc limit 3", ('600519.SH',), role='reader')
    assert rows, '档案表里茅台一行都没有 —— TQ 那条腿没跑还是被清了？'
    got = SK._profile('600519.SH')
    assert got['available'] is True
    assert got['hq_date'] == rows[0]['hq_date'].isoformat(), \
        f"取到的不是最新一天：{got['hq_date']} vs {rows[0]['hq_date']}"
    assert isinstance(got['profile_age_days'], int) and got['profile_age_days'] >= 0


def test_板块成员数不能恒为一():
    """第一版把 `count(*) over (partition by sector_code)` 写在带 `where security_code=这一只`
    的那一层 ⇒ 每个板块都数出 1 只，屏上会把"19 只的小行业"读成"只有它一只"。"""
    rows = SK._sectors('600519.SH')
    assert rows, '茅台一个板块归属都没有？先查 sector_member 那张对表'
    assert any(int(r['members'] or 0) > 1 for r in rows), \
        f'所有板块的成员数都是 1 ⇒ 成员数没另起子查询：{[ (r["name"], r["members"]) for r in rows ][:5]}'
    names = [r['name'] for r in rows]
    assert not any(str(n).isdigit() for n in names), '只有代码的板块名（如 880595）不该摆上屏'
    assert len(names) == len(set(zip(names, [r['sector_type'] for r in rows]))), \
        '同名同类型的两份（通达信/同花顺各一份）该并成一条'


def test_真库这一只的信息能被json_parse_且每股类与ROE不在这块():
    d = SK.board('600519.SH')
    assert d['available'] is True, d.get('reason')
    s = json.dumps({'data': d}, ensure_ascii=False, allow_nan=False)
    assert 'NaN' not in s and 'NaT' not in s and '**' not in s
    for banned in ('eps', 'bps', 'roe_percent'):
        assert banned not in s, f'{banned} 在本机收盘快照里全是 0，摆上屏就是假读数'
    assert d['session']['trade_date'] and isinstance(d['session']['trade_date'], str)
    assert d['caliber'] and d['definitions']
    # 主档那两列本机为空：交 null 而不是 0，并有一条说明替它解释
    assert d['list_date'] is None
    assert any(c['code'] == 'master_columns_empty' for c in d['caveats'])


def test_每条_caveat_都带中文_label_屏上不许念机器码():
    """`Caveats` 那颗 chip 取的是 `label ?? code`（`DataPanel.tsx`）—— 没配 label 就把机器键摆到读者脸上。

    11-03 实测抓到三条（`local_only`／`master_columns_empty`／`profile_stale` 全在念 code）。
    这条闸不许再退回去：后端交几条，就得有几条中文标签。
    """
    from duanxian import stock_facts as SF

    labels = {c['code']: c.get('label') for c in SF.HARD_CAVEATS}
    assert labels, '硬声明一份都没有，谈不上标签'
    # 动态那几条也在同一份源码里，逐个键名扫一遍（新增一条却忘了 label ⇒ 这里红）
    src = inspect.getsource(SF)
    codes = re.findall(r"'code':\s*'([a-z0-9_]+)'", src)
    assert codes, '这个文件里没找到任何 caveat 定义，检查上面那条正则'
    for code in set(codes):
        # 逐条要求：源码里这条的字典必须同时写 label
        block = src[src.index("'code': '%s'" % code):][:260]
        assert "'label': '" in block, f'{code} 没有 label —— 屏上那颗 chip 会念机器码'
        m = re.search(r"'label':\s*'([^']+)'", block)
        assert m and re.search(r'[一-鿿]', m.group(1)), f'{code} 的 label 不是中文：{labels.get(code)}'


def test_财务多期与档案与趋势那几处的_caveat_也都要带中文_label():
    """11-03 屏上那一行计数（`CaveatTally`）点的就是这些标签：没配 label 就只能念"第 N 条"。

    逐个文件扫源码：每出现一条 `'code': '...'`，同一条字典里就得有中文 `'label'`
    （走 `quant._caveats(...)` 那份注册表的除外 —— 那边由 `_CAV_LABEL` 统一配）。
    """
    import inspect
    import re as _re
    from duanxian import (stock_fundamentals, stock_dossier, momentum_follow,
                           price_volume_trend, stock_metrics_ledger)
    for mod in (stock_fundamentals, stock_dossier, momentum_follow,
                price_volume_trend, stock_metrics_ledger):
        src = inspect.getsource(mod)
        for m in _re.finditer(r"\{'code':\s*'([a-z0-9_]+)'", src):
            block = src[m.start():][:300]
            assert "'label': '" in block, f'{mod.__name__} 的 {m.group(1)} 没配中文标签'
