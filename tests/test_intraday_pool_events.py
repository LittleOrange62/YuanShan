# -*- coding: utf-8 -*-
"""盘中事件差判据（`app/intraday/pool_events.py`）的回归 —— 全部离线，不连库不联网。

这块判据是从外部 `intraday_zt/zt_watcher.py` 整体搬来的，**规则一字未改**，
改的只有数据诚实性三处（缺 `lbc`/`zbc`/`p` 不再拿 1/0/0.0 顶）。
所以测试分两组：一组钉"规则没搬错"，一组钉"缺数不再编"。
"""
from __future__ import annotations

from app.intraday.pool_events import (KIND_LABELS, KIND_ORDER, KIND_SIDE, digest,
                                      diff_events, fmt_rows, normalize)


def _row(code, **kw):
    base = {'c': code, 'n': kw.get('name', '股' + code), 'zdp': kw.get('zdp', 10.0),
            'p': kw.get('p', 12345)}
    for k, src in (('lbc', 'board_count'), ('zbc', 'open_count'), ('fbt', 'first_seal_time'),
                   ('fund', 'seal_fund_yuan'), ('hybk', 'industry')):
        if k in kw:
            base[k] = kw[k]
    return base


# ---------------------------------------------------------------- 取数换算（现查 10-26）

def test_池行单位换算():
    """`p` 是 元×1000；`zdp` 已是百分数；`fbt` 是 hhmmss 整数。"""
    r = normalize(_row('600519', p=146600, zdp=10.0123, fbt=92500, lbc=3, zbc=1, fund=2.5e8))
    assert r['price'] == 146.6 and r['pct'] == 10.0123
    assert r['first_seal_time'] == '09:25' and r['board_count'] == 3
    assert r['seal_fund_yuan'] == 2.5e8 and r['missing'] == {}


def test_缺字段一律NULL并进missing不许拿默认值顶():
    """外部旧写法：`lbc or 1`、`zbc or 0`、`(_num(p) or 0)/1000`。

    后果各不相同：连板数缺了填 1 会把一只 3 板票在第一针记成**首板**且事后看不出来；
    开板次数缺了填 0 与"从头封到尾"这个真实读数**同一个值**；价格缺了算出 0.0 元。
    """
    r = normalize(_row('000001'))
    assert r['board_count'] is None and r['open_count'] is None
    assert r['price'] == 12.345          # 有 p 格时照常换算，缺的只有 lbc/zbc
    assert set(r['missing']) == {'board_count', 'open_count', 'seal_fund_yuan'}
    assert normalize(_row('000002', p=None))['price'] is None
    assert 'price' in normalize(_row('000002', p=None))['missing']


def test_停牌横杠不当成零():
    assert normalize(_row('000003', p='-', zdp='-'))['price'] is None
    assert normalize(_row('000003', p='-', zdp='-'))['pct'] is None


# ---------------------------------------------------------------- 事件差规则（外部定版）

def test_首次进池是新封_历史进过则是回封():
    cur = {'A': normalize(_row('A'))}
    assert diff_events({}, cur)[0]['kind'] == 'seal_new'
    assert diff_events({}, cur, seen={'A'})[0]['kind'] == 'reseal'


def test_开板次数变大记开板又回封():
    prev = {'A': normalize(_row('A', zbc=1))}
    cur = {'A': normalize(_row('A', zbc=2))}
    assert [e['kind'] for e in diff_events(prev, cur)] == ['reseal_after_open']


def test_开板次数缺数时不触发回封判定():
    """两侧都缺 → 旧实现拿 0 比 0 不触发，新实现 None 比较同样不触发（行为一致，不许"顺手修成触发"）。"""
    prev = {'A': normalize(_row('A'))}
    cur = {'A': normalize(_row('A'))}
    assert diff_events(prev, cur) == []


def test_出池要炸板池里有才算炸板():
    prev = {'A': normalize(_row('A'))}
    assert diff_events(prev, {}, zb_codes=set()) == []
    assert [e['kind'] for e in diff_events(prev, {}, zb_codes={'A'})] == ['unseal']


def test_跌停轨出池即判开板():
    """跌停池**没有炸板池对应物**（外部 10-21 定）。"""
    prev = {'A': normalize(_row('A', zdp=-10.0))}
    assert [e['kind'] for e in diff_events(prev, {}, side='dt')] == ['dt_open']
    cur = {'B': normalize(_row('B', zdp=-10.0))}
    assert diff_events({}, cur, side='dt')[0]['kind'] == 'dt_seal_new'
    assert diff_events({}, cur, seen={'B'}, side='dt')[0]['kind'] == 'dt_reseal'
    p2 = {'B': normalize(_row('B', zbc=1))}
    c2 = {'B': normalize(_row('B', zbc=2))}
    assert diff_events(p2, c2, side='dt')[0]['kind'] == 'dt_reseal_after_open'


def test_标签与侧只有一份定义且顺序固定():
    assert all(KIND_SIDE[k] == ('dt' if k.startswith('dt_') else 'zt') for k in KIND_LABELS)
    assert set(KIND_ORDER) == set(KIND_LABELS)
    assert KIND_LABELS['dt_open'] == '跌停开板' and KIND_LABELS['seal_new'] == '新封'


# ---------------------------------------------------------------- 基线摘要

def test_摘要不拿缺数当首板且要点名():
    """旧版 `r["lbc"]` 直接参与，缺数已被填成 1 ⇒ 首板数虚高。新口径：None 排除 + 点名。"""
    pool = {'A': normalize(_row('A', lbc=1)), 'B': normalize(_row('B', lbc=4)),
            'C': normalize(_row('C'))}
    s = digest(pool, {})
    assert '涨停池 3 家（首板 1' in s
    assert '板数缺数 1 只未计入' in s and '最高板：股B 4板' in s


def test_缺的那一格不念出来但要念真实值():
    """用户 10-26：满屏"缺数"读着累，且跌停池**结构上没有 fbt**，永远'首封缺数'。
    库里仍写 NULL + missing（数据层不省），只是消息里不说。"""
    rows = [normalize(_row('A', lbc=None, fund=None, zdp=None, p=12345))]
    t = fmt_rows(rows, 6)
    assert '缺数' not in t and '0连板' not in t and '+0.00%' not in t
    assert '股A A' in t and '12.35' not in t          # 价不进消息，别处才念
    got = fmt_rows([normalize(_row('B', lbc=2, zdp=10.0, p=15000, fbt=92500, fund=2e8))], 6)
    assert '2连板' in got and '+10.00%' in got and '首封09:25' in got and '封单2.00亿' in got
