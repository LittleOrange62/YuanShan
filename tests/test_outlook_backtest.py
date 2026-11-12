# -*- coding: utf-8 -*-
"""「⑩ 明日展望」回溯计算（`app/compute/outlook_backtest.py`，1/2/3 日扩展）的钉住用例。

钉的是**纯判据**（不碰库）：
· `forward_metrics` —— 后复权基准价 + 往后最多 3 日的 (开,高,收) 三元组 → 全部涨幅列。
  缺哪一天的行 ⇒ 那一天该列 NULL 且 missing 点名；3 日全无行 ⇒ `up3d=None`（判不了），
  不是 False（没冲高）也不是 True。
· `summarize` —— `up3d_pct` 只数可判的行（None 从分子分母都剔），mean 是 true 占比的百分数。
· 列序 —— `PICK_COLUMNS` 与 `_UPSERT_PICK` 的占位符数必须一致（模块里的 assert 是运行时闸，
  这里再钉死具体列数：加列只改一处就是"屏上那一列写进隔壁那一列"的事故形状）。

写库路径由 `_no_test_write`（同一道 pytest 闸）守着，本文件一个库都不碰。
"""
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from app.compute import outlook_backtest as OB


# ─────────────────────────────────────────────── forward_metrics

BASE = 100.0
FULL = [(101.0, 105.0, 104.0), (103.0, 108.0, 110.0), (111.0, 118.0, 112.0)]


def test_齐全时每一列都对得上手算():
    m = OB.forward_metrics(BASE, FULL)
    assert m['gap_pct'] == pytest.approx(1.0)            # 第 1 日开 101 / 100
    assert m['hi_pct'] == pytest.approx(5.0)             # 第 1 日高 105
    assert m['cl_pct'] == pytest.approx(4.0)             # 第 1 日收 104
    assert m['cl2_pct'] == pytest.approx(10.0)            # 第 2 日收 110
    assert m['cl3_pct'] == pytest.approx(12.0)            # 第 3 日收 112
    assert m['open_to_hi_pct'] == pytest.approx(3.96)     # (105/101-1)×100，两位小数
    assert m['open_to_cl_pct'] == pytest.approx(2.97)      # (104/101-1)×100
    assert m['hi_max_3d_pct'] == pytest.approx(18.0)     # 3 日最高 118
    assert m['up3d'] is True
    assert m['missing'] == []


def test_第2日缺行只空那一天():
    m = OB.forward_metrics(BASE, [FULL[0], None, FULL[2]])
    assert m['cl_pct'] == pytest.approx(4.0)
    assert m['cl2_pct'] is None                          # 第 2 日没行 ⇒ NULL，不顶 0
    assert m['cl3_pct'] == pytest.approx(12.0)            # 第 3 日照算
    assert m['hi_max_3d_pct'] == pytest.approx(18.0)     # 高点在 1/3 日里取
    assert m['up3d'] is True
    assert len(m['missing']) == 1 and m['missing'][0].startswith('d2：')


def test_三天全缺则冲高判不了而不是没冲高():
    m = OB.forward_metrics(BASE, [None, None, None])
    assert m['gap_pct'] is None and m['hi_pct'] is None and m['cl_pct'] is None
    assert m['cl2_pct'] is None and m['cl3_pct'] is None
    assert m['hi_max_3d_pct'] is None
    assert m['up3d'] is None                              # 不是 False
    assert [x.split('：')[0] for x in m['missing']] == ['d1', 'd2', 'd3']


def test_没有基准价则全部判不了():
    m = OB.forward_metrics(None, FULL)
    for k in ('gap_pct', 'hi_pct', 'cl_pct', 'cl2_pct', 'cl3_pct', 'hi_max_3d_pct'):
        assert m[k] is None
    assert m['up3d'] is None


def test_高点恰好等于基准价算没冲高():
    # "冲过"是严格大于；持平到收盘不算冲 —— 边界错一格，"冲高率"整体漂。
    m = OB.forward_metrics(BASE, [(100.5, 100.0, 99.0), None, None])
    assert m['hi_pct'] == pytest.approx(0.0)
    assert m['up3d'] is False


def test_后两天还没到只算到场的那几天():
    # 窗口末端的两场：chain 尾巴是 None（还没发生），不产生任何"缺行"点名之外的假 NULL。
    m = OB.forward_metrics(BASE, [FULL[0], None, None])
    assert m['cl_pct'] == pytest.approx(4.0)
    assert m['cl2_pct'] is None and m['cl3_pct'] is None
    assert m['hi_max_3d_pct'] == pytest.approx(5.0)      # 只有第 1 日的高
    assert m['up3d'] is True
    assert [x.split('：')[0] for x in m['missing']] == ['d2', 'd3']


# ─────────────────────────────────────────────── summarize

def _row(section, up3d, in_both=False, *, code=None, day='2026-11-04', **kw):
    r = {'section': section, 'in_both': in_both, 'up3d': up3d,
         'trade_date': day, 'security_code': code or f'{section.upper()}.SZ',
         'gap_pct': 1.0, 'hi_pct': 2.0, 'cl_pct': 0.5,
         'open_to_hi_pct': 1.0, 'open_to_cl_pct': 0.5,
         'cl2_pct': 2.0, 'cl3_pct': 3.0, 'hi_max_3d_pct': 6.0}
    r.update(kw)
    return r


def test_冲高率按可判行算占比():
    s = OB.summarize([_row('hits', True), _row('hits', False), _row('hits', None)])
    assert s['hits']['n'] == 3
    assert s['hits']['up3d_pct'] == {'n': 2, 'mean': 50.0}   # None 从分子分母都剔
    assert s['hits']['cl3_pct']['mean'] == pytest.approx(3.0)


def test_两榜都进只数交集_且一只票只数一次():
    """`both` 是"场次×票"的集合：同一只票同一天在两榜各有一行，数成两行就是把一只票算成两只
    （`n` 翻倍，均值不变 ⇒ 屏上看不出来，所以这条得钉住）。视图那边 SQL 同口径去重。"""
    rows = [_row('hits', True, in_both=True, code='000001.SZ'),
            _row('trend', False, in_both=True, code='000001.SZ'),   # 同一只票、同一天的另一行
            _row('hits', False),
            _row('trend', True)]
    s = OB.summarize(rows)
    assert s['both']['n'] == 1, '交集把一只票数了两次'
    assert s['both']['up3d_pct'] == {'n': 1, 'mean': 100.0}
    assert s['hits']['n'] == 2 and s['trend']['n'] == 2   # 两桶仍按各自榜的行数算，不受影响


def test_一族全空交None而不是全0():
    s = OB.summarize([_row('hits', None, cl3_pct=None)])
    assert s['hits']['cl3_pct'] is None
    assert s['hits']['up3d_pct'] is None


# ─────────────────────────────────────────────── 列序

def test_列序与占位符数钉死():
    assert len(OB.PICK_COLUMNS) == 32
    assert OB._UPSERT_PICK.count('%s') == 32
    assert OB.PICK_COLUMNS[:4] == ('trade_date', 'next_date', 'spec_version', 'section')
    # 新六列在 `open_to_cl_pct` 之后、`missing` 之前（加列的位置钉死，别插错缝）
    assert OB.PICK_COLUMNS.index('open_to_cl_pct') + 1 == OB.PICK_COLUMNS.index('next2_date')
    assert OB.PICK_COLUMNS.index('up3d') + 1 == OB.PICK_COLUMNS.index('missing')


def test_next_chain的尾巴交None不缩短(monkeypatch):
    # 场次只查到 2 天时，chain 仍恒长 3：第 3 位是 None（"还没发生"），不是截成短列表 ——
    # 截短了 `forward_metrics` 的 missing 点名和基线 SQL 的固定 3 槽位都会错位。
    from app.compute import outlook_backtest as mod
    def fake_query(sql, params=(), *, role='reader'):
        start = params[0]
        return [{'trade_date': start + dt.timedelta(days=1)},
                {'trade_date': start + dt.timedelta(days=2)}]
    monkeypatch.setattr(mod.engine, 'query', fake_query)
    assert mod.next_chain(dt.date(2026, 1, 5), 3) == \
        [dt.date(2026, 1, 6), dt.date(2026, 1, 7), None]
