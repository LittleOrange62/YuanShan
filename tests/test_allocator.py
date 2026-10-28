"""分配者视角（`app/compute/allocator.py` + `duanxian/allocator_view.py`）的回归。

这里钉的全是"会被读成结论但其实没有"的形状：
① 缺项当 NULL 不当 0；② 分位样本不足就不给分位；③ 阶段判据必须**互斥且可复算**
（理由句里的数就是触发它的那几个数）；④ 象限分界线是当日中位数不是写死的 50；
⑤ 那条减仓规则的均线只能用**昨天为止**的（用上当日就是未来函数）；
⑥ 数据不够就是算不出，不缩短窗口硬算。
"""

from __future__ import annotations

import math

from app.compute import allocator as A
from duanxian import allocator_view as V


# ---------------------------------------------------------------- 归一化与 NULL

def test_缺项不进分母_也不当零():
    parts = {'div_yield': 0.9, 'stability': None, 'share_med': 0.5, 'mv_share': None}
    val, missing = A.score_from_parts(parts, A.TOLL_W)
    assert missing == ['mv_share', 'stability']
    # 只剩 35 + 20 两项：(0.9*35 + 0.5*20) / 55 * 100 = 75.45
    assert abs(val - 75.45) < 0.01


def test_全部缺项交回_none_而不是_zero():
    val, missing = A.score_from_parts({'a': None, 'b': None}, {'a': 50, 'b': 50})
    assert val is None and sorted(missing) == ['a', 'b']


def test_分位样本不足_不猜():
    """约定是"`<= x` 的占比"：最低的拿 0 分位（不是 0.1），最高的拿 1.0。
    界面与 `share_pctile` 的说明都按这个约定写，换约定就是换一批数，所以钉在这里。"""
    assert A._pct_rank([0.1, 0.2, 0.3], 0.25) is None      # 3 个点算出的分位是噪音
    assert A._pct_rank([0.1, 0.2, 0.3, 0.4, 0.5], 0.5) == 1.0
    assert A._pct_rank([0.1, 0.2, 0.3, 0.4, 0.5], 0.0) == 0.0
    assert A._pct_rank([0.1, 0.2, 0.3, 0.4, 0.5], 0.3) == 0.6
    assert A._pct_rank([0.1, 0.2, None, 0.3, 0.4, 0.5], None) is None


# ---------------------------------------------------------------- 阶段判定

def test_阶段互斥_共识优先于涌入():
    """份额高分位 + 温度高 + 动量向上 = S4；同一条数据不该同时被 S3 也认走。"""
    stage, why = A.judge_stage(share_pctile=0.95, share_slope20=1.5,
                               crowding_temp=92.0, mom60=20.0, member_delta=3)
    assert stage == 's4_consensus'
    assert '95%' in why and '92' in why and '+20.0%' in why      # 理由里就是触发的那几个数


def test_过剩期认的是_份额回落_而产能还在进场():
    stage, why = A.judge_stage(share_pctile=0.20, share_slope20=-0.4,
                               crowding_temp=30.0, mom60=-15.0, member_delta=12)
    assert stage == 's5_overhang'
    assert '12' in why


def test_低位抬头才是起步_高位继续涨不算():
    assert A.judge_stage(share_pctile=0.20, share_slope20=0.1, crowding_temp=40.0,
                         mom60=5.0, member_delta=0)[0] == 's1_seed'
    assert A.judge_stage(share_pctile=0.70, share_slope20=0.05, crowding_temp=60.0,
                         mom60=5.0, member_delta=0)[0] == 's2_boom'


def test_条件都不满足就是不在五段里():
    stage, why = A.judge_stage(share_pctile=0.45, share_slope20=-0.1,
                               crowding_temp=50.0, mom60=-2.0, member_delta=0)
    assert stage is None and '不硬贴一个阶段' in why


def test_算不出就明说算不出_不猜阶段():
    stage, why = A.judge_stage(share_pctile=None, share_slope20=0.5,
                               crowding_temp=88.0, mom60=30.0, member_delta=5)
    assert stage is None and '不猜' in why


# ---------------------------------------------------------------- 象限

def test_象限分界取当日中位数而不是写死():
    """写死 50 的话，分布一动整个象限全体搬家 —— 昨天的"收费站"今天就变"无人区"。"""
    assert V_quadrant(70, 30, tm=60, im=55) == 'toll'
    assert V_quadrant(40, 80, tm=60, im=55) == 'story'
    assert V_quadrant(70, 70, tm=60, im=55) == 'consensus'
    assert V_quadrant(30, 30, tm=60, im=55) == 'nowhere'
    # 同一组分数，换一条中位线就换一格 —— 这正是"必须把中位线交出去"的理由
    assert V_quadrant(70, 30, tm=75, im=55) == 'nowhere'
    assert V_quadrant(70, 30, tm=75, im=25) == 'story'   # 中位线一低，它就从无人区搬进故事区


def V_quadrant(toll, imagine, tm, im):
    return A.quadrant_of(toll=toll, imagine=imagine, toll_median=tm, imagine_median=im)


def test_坐标缺一项就没有象限():
    assert A.quadrant_of(toll=None, imagine=50, toll_median=50, imagine_median=50) is None


# ---------------------------------------------------------------- 那条规则的本地复算

def _rising(n, start=100.0, step=0.2):
    return [start + step * i for i in range(n)]


def test_均线窗口不够_就是算不出():
    r = V.ma_rule([100.0] * (V.MA_WINDOW + 1))
    assert r['available'] is False and '不够' in r['reason']


def test_规则用的是昨天为止的均线_不含当日():
    """把当日算进均线会让"今天的暴涨"自己把线抬起来 → 永远触发不了卖出，是未来函数。"""
    closes = _rising(400)                       # 一路缓涨，收盘始终贴着均线之上但不过 15%
    r = V.ma_rule(closes)
    assert r['available'] is True and r['trades'] == 0
    # 造一段"高出均线 15% 后回落"的行情：先横盘拉均线，再跳 25%，再跌回均线下面
    flat = [100.0] * (V.MA_WINDOW + 5)
    pop = [100.0 * (1 + 0.25)] * 8
    back = [99.0] * 8
    r2 = V.ma_rule(flat + pop + back)
    assert r2['trades'] >= 2 and r2['trade_log'][0]['action'] == '卖一半'
    assert any(x['action'] == '买回满仓' for x in r2['trade_log'])


def test_减仓期间不再卖_所以不会被一路砍到零仓():
    flat = [100.0] * (V.MA_WINDOW + 5)
    pop = [125.0] * 30                        # 连续 30 场都高出均线 25%
    r = V.ma_rule(flat + pop)
    sells = [x for x in r['trade_log'] if x['action'] == '卖一半']
    assert len(sells) == 1                     # 只有满仓那次触发


def test_净值与年化交得出_且丢掉的预热场数写明():
    r = V.ma_rule(_rising(380))
    assert r['sessions_dropped'] == V.MA_WINDOW
    assert r['sessions_used'] == 380 - V.MA_WINDOW
    assert r['buy_hold_total'] > 0 and r['rule_total'] >= 0
    assert not math.isnan(r['buy_hold_annualized'])


def test_说明里带的极值是实测出来的那个数():
    """`peak_above` 是"为什么规则一次都没触发"的唯一解释 —— 000015 上证红利实测 14.2%。"""
    flat = [100.0] * (V.MA_WINDOW + 5)
    r = V.ma_rule(flat + [110.0] * 10)
    assert r['peak_above'] is not None and 0.09 < r['peak_above'] < 0.11


# ---------------------------------------------------------------- 读数层的诚实

def test_库连不上时读数层说明原因不放空榜(monkeypatch):
    from duanxian import pg
    monkeypatch.setattr(pg, 'enabled', lambda: False)
    out = V.view()
    assert out['available'] is False and '本地库' in out['reason']
    assert 'rows' not in out                    # 不许带一个空榜让页面渲染成"今天没有板块"


def test_表里一场都没有时教人怎么补(monkeypatch):
    from duanxian import pg
    monkeypatch.setattr(pg, 'enabled', lambda: True)
    monkeypatch.setattr(V, 'sessions_count', lambda: {'sessions': 0})
    out = V.view()
    assert out['available'] is False and 'app.compute.allocator' in out['reason']


def test_nan_一律变_none_否则浏览器解析不了整份_json():
    """pandas 的空值是 NaN；`json.dumps` 会把 NaN 原样写出去，而那不是合法 JSON。
    现象是页面只报"读不到"，看不出接口其实返回了东西 —— 所以这道闸必须在出口层。"""
    import numpy as np
    import pandas as pd
    assert V._num(float('nan')) is None
    assert V._num(np.nan) is None
    assert V._num(pd.NaT) is None
    assert V._num(Decimal_like('1.5')) == 1.5
    assert V._num('银行') == '银行'


class Decimal_like:
    def __init__(self, v): self.v = v
    def __float__(self): return float(self.v)
