# -*- coding: utf-8 -*-
"""涨停基因公用件（`app/compute/limit_gene.py`）的闸。

这块判据从 2026-11-04 起有两个调用方：短线复盘的首板分析（窗口 63/126/489 场）与
埋伏票榜的股性表（窗口 250 场）。**两屏读同一段代码**是本模块存在的唯一理由，
所以这里的测试分三层，一层都不能省：

① 纯判据不打桩：封住 vs 触及两把尺、窗口起点、缺档位价整族判不了、缺数 NULL 而不是 0。
② 两种表达式逐只对账（真库、不打桩）：同一把尺有「给一批行按只数」与「全场一次聚合」两种写法，
   它们本来就属于「该禁止并存」的那一类，所以只允许成对出现在这一个模块里，并由这条测试钉住；
   改了上面那段判据而忘了改下面那段 SQL，这一条当场红。
③ 调用方确实读的是这一份：首板榜与股性表里那些同名符号必须是别名，不是第二份实现。
"""
import datetime as dt
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.compute import limit_gene as gene                       # noqa: E402
from app.compute import first_board_daily as fbd                  # noqa: E402
from app.compute import stock_character as ch                     # noqa: E402

D0 = dt.date(2026, 10, 1)


def _d(offset_days: int) -> dt.date:
    return D0 + dt.timedelta(days=offset_days)


def _bar(day: dt.date, **kw) -> dict:
    """一根日线：只写这一族判据用到的四格，其余缺省（None）就是「没读到」。"""
    b = {'trade_date': day, 'open': 10.0, 'high': 10.0, 'low': 10.0, 'close': 10.0,
         'limit_up': 11.0, 'limit_down': 9.0}
    b.update(kw)
    return b


# ============================================================ ① 纯判据（不打桩）

def test_封住与触及是两把尺_冲板没封住只算进触及():
    """本机实测同一场 触板 103 对 封住 84：只留一个数就会把「谁在试盘」那批票整个藏掉。"""
    bars = [
        _bar(_d(1), close=11.00, high=11.00),          # 封住（收盘贴在档位上）
        _bar(_d(2), close=10.40, high=11.00),          # 冲过板、收盘没封住 ⇒ 只进 touched
        _bar(_d(3), close=10.00, high=10.20),          # 两把尺都不计
    ]
    cols, gap = gene.seal_touch_counts(bars, _d(1))
    assert cols['lu_sealed'] == 1 and cols['lu_touched'] == 2
    assert cols['ld_sealed'] == 0 and cols['ld_touched'] == 0
    assert gap == {}, '全是可读的行就不该点名任何缺口：%s' % gap


def test_跌停方向是反的_收盘贴住算封住_冲下去没贴住算触及():
    bars = [
        _bar(_d(1), close=9.00, low=9.00),             # 封住跌停
        _bar(_d(2), close=9.60, low=9.00),             # 盘中踩到档位、收盘弹回来
        _bar(_d(3), close=10.0, low=9.80),             # 两把尺都不计
    ]
    cols, _ = gene.seal_touch_counts(bars, _d(1))
    assert cols['ld_sealed'] == 1 and cols['ld_touched'] == 2


def test_窗口起点之前的行一律不计():
    """`since` 是调用方给的场次（首板榜 63 场、股性表 250 场），窗口外的行混进来，
    那一列就不是「这段窗口有几天封住」而是「这只票一辈子有几天封住」。"""
    bars = [_bar(_d(1), close=11.00, high=11.00), _bar(_d(2), close=11.00, high=11.00)]
    cols, _ = gene.seal_touch_counts(bars, _d(2))
    assert cols['lu_sealed'] == 1 and cols['lu_touched'] == 1
    assert gene.win(bars, _d(2)) == [bars[1]]
    assert len(gene.win(bars, None)) == 2, 'since=None 是「全都要」，不是「一个都不要」'


def test_缺一天档位价就整族判不了_且不连坐另一族():
    """缺一天 = 少计一次，报出来的数就成了「说不清少了谁的下界」。宁可整族 NULL + 点名。

    涨停那一族缺档位不该把跌停那一族一起打掉（两族各自的输入列不同）。
    """
    bars = [_bar(_d(1), close=11.00, high=11.00), _bar(_d(2), limit_up=None)]
    cols, gap = gene.seal_touch_counts(bars, _d(1))
    assert cols['lu_sealed'] is None and cols['lu_touched'] is None
    assert gap['lu_sealed'] == gene.GAP_HIST_LIMIT_UP == gap['lu_touched']
    assert cols['ld_sealed'] == 0 and cols['ld_touched'] == 0
    assert 'ld_sealed' not in gap, '涨停那一族的缺口不许写到跌停那一族头上'


def test_空窗口点名的是没有行_而不是零次():
    """窗口里一根日线都没有（全新上市、或读库失败）——说成「0 次涨停」就是把缺数排成最差。"""
    cols, gap = gene.seal_touch_counts([_bar(_d(1))], _d(5))
    assert all(cols[k] is None for k in gene.GENE_KEYS)
    assert set(gap) == set(gene.GENE_KEYS)
    assert set(gap.values()) == {gene.GAP_HIST_ROWS}


def test_真的一次都没封住是零_不是空():
    """0 在这里是**真实读数**，NULL 才是判不了；两者混成一个数就废了排名。"""
    bars = [_bar(_d(1), close=10.0, high=10.3, low=9.8), _bar(_d(2), close=10.1, high=10.4, low=9.7)]
    cols, gap = gene.seal_touch_counts(bars, _d(1))
    assert cols == {'lu_sealed': 0, 'lu_touched': 0, 'ld_sealed': 0, 'ld_touched': 0}
    assert gap == {}


def test_NaN_不参与比较():
    """pandas 读回来的空格是 NaN。`NaN >= x` 恒 False 会把它悄悄算成「没涨停」，
    而它其实是「这一格没数」——必须按缺档位价那一类处理。"""
    nan = float('nan')
    cols, gap = gene.seal_touch_counts([_bar(_d(1), high=nan)], _d(1))
    assert cols['lu_sealed'] is None and gap['lu_touched'] == gene.GAP_HIST_LIMIT_UP
    cols2, gap2 = gene.seal_touch_counts([_bar(_d(1), low=nan)], _d(1))
    assert cols2['ld_touched'] is None and gap2['ld_sealed'] == gene.GAP_HIST_LIMIT_DOWN
    assert 'lu_sealed' not in gap2, 'high 没缺，涨停那一族不该被 low 拖累'


def test_容差只有一个数字():
    """0.01 是「收盘贴在板上」的那把尺。抄第二份到别处（0.005 或 0.02）就是两把尺，
    而两把尺给出的行数差会直接被念成「今天怎么少了几只涨停」。"""
    assert gene.SEAL_EPS == 0.01
    assert fbd.LIMIT_SEAL_EPS is gene.SEAL_EPS
    ch_src = (ROOT / 'app/compute/stock_character.py').read_text(encoding='utf-8')
    assert 'SEAL_EPS =' not in ch_src, '股性表自己定义一份容差 = 第三把尺，它必须用 gene.SEAL_EPS'
    assert 'gene.SEAL_EPS' in ch_src


def test_四个中性键名不许带窗口后缀():
    """列名（首板榜叫 `lu_sealed_3m`、股性表叫 `seal_250`）由调用方加后缀，
    否则「同一个算法因为列名不同被写成两份」会在这里复发。"""
    assert gene.GENE_KEYS == ('lu_sealed', 'lu_touched', 'ld_sealed', 'ld_touched')
    assert set(fbd._GENE_COLS) == set(gene.GENE_KEYS), \
        '首板榜那份列名映射必须逐个盖住四个中性键：漏一个键就是那一列悄悄不落库'
    assert len(set(fbd._GENE_COLS.values())) == 4, '两键映到同一列名会互相覆盖'


# ============================================================ ② 两种表达式逐只对账（真库）

def test_同一把尺的两种写法逐只必须相等():
    """`seal_touch_counts`（逐票数行）与 `seal_touch_market`（全场一次聚合）算的是同一件事。

    不打桩：抽一场真日线的最近 10 场，两种写法逐只比四列。改判据（eps、缺行怎么处理、
    哪一格参与比较）只动了一边 ⇒ 这条当场红，而且红的是那一只票、那一列。
    """
    from app.db import engine

    days = [r['trade_date'] for r in (engine.query(
        "select distinct trade_date from market_data.stock_daily "
        "order by 1 desc limit 10") or [])]
    if len(days) < 10:
        pytest.skip('本机日线不足 10 场，这一条要对账的是真数据')
    to, frm = days[0], days[-1]
    agg = gene.seal_touch_market(frm, to, gene.SEAL_EPS)
    assert agg, '全场聚合读回空——库不通还是日线表空着？这里不该继续往下判'

    # 取样要「有肉的」和「干净的」两头都有：只抽封住次数最高的那批，等于没测 0 与 NULL 那两档。
    ranked = sorted(agg.items(), key=lambda kv: (-(kv[1]['lu_sealed'] or -1), kv[0]))
    sample = [c for c, _ in ranked[:12]] + [c for c, _ in ranked[-6:]]
    sample += [c for c, v in ranked if v['lu_sealed'] is None][:4]
    sample = list(dict.fromkeys(sample))

    rows = engine.query(
        "select security_code, trade_date, high, low, close, limit_up, limit_down "
        "  from market_data.stock_daily "
        " where trade_date between %(frm)s and %(to)s "
        "   and security_code = any(%(codes)s::text[]) order by 1, 2",
        {'frm': frm, 'to': to, 'codes': sample}, role='reader') or []
    per: dict[str, list] = {}
    for r in rows:
        per.setdefault(r['security_code'], []).append(dict(r))

    checked = 0
    for code in sample:
        a = agg[code]
        if not per.get(code):
            # 聚合里有行、逐票读不回行 = 两边筛的窗口不一致，这是真分叉，不能放过去
            pytest.fail('%s 在聚合里有 %d 行，逐票却读不回任何日线行' % (code, a['bars']))
        cols, gap = gene.seal_touch_counts(per[code], frm)
        for k in gene.GENE_KEYS:
            assert cols[k] == a[k], (
                '%s 的 %s 两把尺分叉了：逐票数 %r（缺口 %s）／全场聚合 %r（缺口 %s）—— '
                '一处改了判据忘了另一处' % (code, k, cols[k], gap.get(k), a[k],
                                          (a['gap'] or {}).get(k)))
        assert a['bars'] == len(per[code]), '%s 的窗口行数对不上：%d 对 %d' % (
            code, a['bars'], len(per[code]))
        checked += 1
    assert checked >= 15, '只比了 %d 只就收工，抽样太少不足以钉住两种写法' % checked


def test_聚合那一种写法也要把缺档位价报成判不了():
    """SQL 里 `count(*) filter (...)` 天然会把 NULL 那一行**不计入**，也就是给一个下界。
    必须靠 `lu_bad_rows`/`ld_bad_rows` 把这一族整体判成 NULL，否则屏上的数没人在解释少了谁。"""
    assert 'lu_bad_rows' in gene.GENE_AGG_SQL and 'ld_bad_rows' in gene.GENE_AGG_SQL
    assert 'limit_up - %(eps)s' in gene.GENE_AGG_SQL and 'limit_down + %(eps)s' in gene.GENE_AGG_SQL
    assert gene.GENE_AGG_SQL.count('%(eps)s') == 4, '四把尺共用同一个容差参数，抄两份就会漂'


def test_连板那一腿只读_lup_不自数():
    """`consec` 的唯一实现在 `limit_up_pool`（lup-1）。这里再数一遍就有两把尺：
    停牌那天算不算断、ST 档位容差取几分，两处各判一次必分叉。"""
    sql = gene.CONSEC_SQL
    assert 'analytics.limit_up_pool_daily' in sql and 'max(consec)' in sql
    assert 'spec_version = %(lup)s' in sql, '不带版本条件就会把旧批的连板数一起顶进来'
    assert 'sealed' in sql, '只数封住的那几行：冲板没封住不构成连板'


# ============================================================ ③ 调用方读的是这一份

def test_首板榜与股性表都经由公用件而非各自再实现一遍():
    """两个调用方不许留第二份算法。别名可以留（读侧与自检在用），函数体不行。"""
    assert fbd.LIMIT_SEAL_EPS is gene.SEAL_EPS
    assert fbd.CONSEC_SQL is gene.CONSEC_SQL

    # 逐票那一种写法：首板榜的 `limit_counts` 必须只是换列名的薄包
    src = (ROOT / 'app/compute/first_board_daily.py').read_text(encoding='utf-8')
    body = src[src.index('def limit_counts('):]
    body = body[:body.index('\ndef ')] if '\ndef ' in body else body
    assert 'gene.seal_touch_counts(' in body, '首板榜自己在函数体里数封住/触及 = 第二把尺'
    assert 'limit_up' not in body.replace(' ', '').replace('LIMIT_SEAL_EPS', ''), \
        'limit_counts 里再出现档位比较本体，就是有人把判据抄回了这一边'

    # 全场那一种写法：股性表只许调 `seal_touch_market`，不许在自己那条聚合 SQL 里再数一遍档位
    src2 = (ROOT / 'app/compute/stock_character.py').read_text(encoding='utf-8')
    assert 'gene.seal_touch_market(' in src2
    assert 'limit_up' not in ch.AGG_SQL, \
        'AGG_SQL 里出现档位列 = 第三种数法；涨停次数只有 `limit_gene` 那两处'


def test_整批读回空要说成读不到_而不是没有连板(monkeypatch):
    """`consec_by_code` 交回第二个值不是装饰：lup-1 那一批根本没跑时，
    「没有连板」是查不到，不是没有。混成一个就会把停摆写成一句合理的假读数。"""
    calls = []

    def fake_query(sql, params=None, role=None, **kw):
        calls.append((sql, params, role))
        return []

    monkeypatch.setattr(gene.engine, 'query', fake_query)
    got, batch_empty = gene.consec_by_code(['000001'], _d(1), _d(9), 'lup-1')
    assert got == {} and batch_empty is True
    assert calls and calls[0][2] == 'reader', '读侧用 reader，写角色是越权'

    monkeypatch.setattr(gene.engine, 'query',
                        lambda sql, params=None, role=None, **kw:
                        [{'security_code': '000001', 'max_consec': 3}])
    got2, empty2 = gene.consec_by_code(['000001'], _d(1), _d(9), 'lup-1')
    assert got2 == {'000001': 3} and empty2 is False

    got3, empty3 = gene.consec_by_code([], _d(1), _d(9), 'lup-1')
    assert got3 == {} and empty3 is False, '名单本来就空 ≠ 读库失败，不该报 batch_empty'


def test_两个缺数令牌各有分工():
    """`lup_rows_unavailable`（整批改是空＝没读到库）与 `no_local_round`（池里有行但数不出这一轮）
    是两种「判不了」，界面要说的是不同的一句话；写成一个码就分不出该等谁。"""
    assert gene.GAP_CONSEC_SOURCE != gene.GAP_NO_ROUND
    assert fbd.GAP_CONSEC_SOURCE == gene.GAP_CONSEC_SOURCE
    assert fbd.GAP_NO_ROUND == gene.GAP_NO_ROUND
    assert ch.GAP_CONSEC == gene.GAP_CONSEC_SOURCE


def test_注释里点名的文件必须真实存在():
    """本模块的 docstring 直接点名 `tests/test_limit_gene.py` 里那条对账用例——
    用例搬家或删掉，注释就成了下一代的假路标（同 `test_volume_ratio` 那条扫描一条规矩）。"""
    text = (ROOT / 'app/compute/limit_gene.py').read_text(encoding='utf-8')
    assert 'tests/test_limit_gene.py' in text
    found = set(re.findall(r'(?:app|ingest|duanxian|tests)/[\w/.\-]*\.py', text))
    assert found, '注释里一个文件路径都没有？那这条钉该改判据'
    for rel in found:
        rel = rel.rstrip('.,;')
        assert (ROOT / rel).exists(), '注释指向不存在的文件：%s' % rel
