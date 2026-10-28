# -*- coding: utf-8 -*-
"""埋伏票（左侧机会）判据与写入：`analytics.ambush_setup`。

用户 2026-10-13 要把突破信号那屏改成左右两栏：右栏是已经确认的右侧（突破信号），
左栏放**埋伏票** —— 点名重点关注「各均线都汇集到一起的横盘股」并且「看周线、基本每周重心都上移」，
再让我把左侧机会常见的选法找一些进来。于是这一张表收五种形态（同一只票同场可命中多个）：

| 类型码 | 一句话 | 主要读数 |
|---|---|---|
| `sticky_pull`   | 均线汇集 + 横盘窄幅 + 周线重心连升（用户点名的那条） | ma_spread_pct / range20_pct / weekly_ups6 |
| `pullback_ma20` | 趋势还在，回踩 MA20 且缩量 | trend60_pct / dev_ma20_pct / shrink_ratio |
| `pullback_ma60` | 同上但踩的是 MA60（更深的回调） | trend60_pct / dev_ma60_pct / shrink_ratio |
| `obv_bid`       | 量价背离：钱在进、价没走（吸筹那一类） | obv_ratio / trend60_pct |
| `dragon_back`   | 龙回头：放量大阳/涨停后横盘、不破那根阳线低点且缩量 | days_since_big / hold_vs_big_pct / shrink_ratio |

阈值全部来自**当天实测分布**（2026-10-12，MA60 满窗 5,526 只），不是抄来的整数：
均线极差中位 6.46%、P25 3.87%，`<1.5%` 只剩 181 只 —— 严档就用 1.5%；
20 日高低差 / 当日后复权收盘：中位 16.9%、P25 11.7%（分母是当日收盘，不是 20 日均价）—— 窄幅用 11.7%；
「极差<1.5% + 振幅<11.7% + 周线近 6 周连升>=4 周」三条全过 = 42 只（要求 5 周只剩 3 只，太苛就否掉）。
龙回头单独量过：60 日内有大阳/涨停 2,731 只 → 横够 7 天 2,401 → 不破大阳低点 535
→ 且缩量 292 → 全套 260，所以再加「横盘不超过 30 天」与「现价不脱离那根阳线收盘 12%」收口。

三条纪律：
① 缺数一律 NULL，不按 0 参与判据或排序（0 会被读成「很紧/很缩」，方向正好相反）；
② 判据只住本文件，接口与页面只读算好的行；
③ 周线按 ISO 周分桶，当前周不足 5 个交易日就不计入连升（实测 2026-W41 只有 2 场、W42 只有 1 场），
   并把 `week_sessions_cur` 一起交出去，界面要能自证这一周有几天。

用法：
    python -m app.compute.ambush_setup                      # 最新有日线的那一天
    python -m app.compute.ambush_setup --date 2026-10-12
    python -m app.compute.ambush_setup --recent 5           # 往前 5 个交易日
    python -m app.compute.ambush_setup --since 2025-10-13   # 回算（一天一趟）
    python -m app.compute.ambush_setup --since 2025-10-13 --skip-done --max-minutes 90
        # 回算近一年实测要 3~4 小时（单日 65 秒）：--skip-done 让断点续跑不重算已经写好的场，
        # --max-minutes 到点就停并报「还剩几场」，避免这一批挤进 15:59 的收盘链。
    python -m app.compute.ambush_setup --dry-run            # 只打前 20 名不写库

退出码：0 成功 / 1 没有可算的交易日 / 2 一趟都没写进去 /
        3 有场次算失败（已经写下的留着，重跑同一条命令只补没成的）/
        4 自限预算用完、还剩几场没轮到。
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import time
from typing import Any, Dict, List, Optional, Sequence

from app.db import engine

SPEC_VERSION = 'ambush-1'

# ---- 判据阈值（来源见模块 docstring；改判据就换 SPEC_VERSION） ----
SPREAD_MAX = 1.5            # 均线极差（%）上限：小于它才算"汇集"
RANGE20_MAX = 11.7          # 20 日振幅（%）上限（P25）：横得紧
WEEKLY_UPS6_MIN = 4         # 近 6 个完整周里重心抬升的周数下限
WEEKLY_MIN_BARS = 7         # 数「近 6 周抬升」要 7 个完整周（6 次比较）。
#                            ⚠️ 不是 8：2026-10-12 那 11 个周桶里有 4 个是国庆造成的不完整周
#                            （1/2/3/4 场），完整周只剩 7 个 —— 阈值定成 8 会把整批严档票清空，
#                            那是跟假期过不去，不是跟票过不去（实测那样严档恒为 0 只）。
PB_TREND60_MIN = 15.0       # 回踩型要求趋势还在：60 日涨幅（%）下限
PB_DEV_MA20_MAX = 3.0       # 贴 MA20 的容差（%）
PB_DEV_MA60_MAX = 5.0       # 贴 MA60 的容差（%）
SHRINK_MAX = 0.90           # 缩量：近 5 场（含当日）均量 ÷ 第 6~60 场均量（SQL 里 `v5 / v60p`，:272-273,303）
OBV_MIN = 0.25              # 量价背离的吸筹强度下限
OBV_DRIFT_MIN, OBV_DRIFT_MAX = -3.0, 8.0    # 而价基本没走（60 日涨幅在这个带里）
DRAGON_DAYS_MIN, DRAGON_DAYS_MAX = 7, 30    # 大阳后横 7~30 天：太短没洗透、太长不是回头
DRAGON_HOLD_MIN, DRAGON_HOLD_MAX = -10.0, 12.0   # 现价相对那根阳线收盘（%）：守住且没脱离
DRAGON_LOOKBACK = 60        # 那根大阳必须在最近 60 场内
BIG_GAIN_MIN = 7.0          # 大阳：当日涨幅（%）下限
BIG_VOL_MULT = 1.8          # 且量 >= 前 5 日均量 ×1.8
LIMIT_VOL_MULT = 1.5        # 或涨停收盘且量 >= 前 5 日均量 ×1.5

BARS_MIN = 60               # 少 60 根 K 线就不判（MA60 与 60 日涨幅都不可信）
CAL_DAYS = 200              # 取数日历跨度（约 140 个交易日）：够 MA60 + 14 个周桶 + 龙回头窗口

TYPE_LABELS = {
    'sticky_pull': '均线汇集蓄势',
    'pullback_ma20': '回踩 MA20',
    'pullback_ma60': '回踩 MA60',
    'obv_bid': '量价背离吸筹',
    'dragon_back': '龙回头',
}

#: 每一类在筛什么、为什么这算埋伏。**措辞只住这一处**（读侧接口与页面都引这份）。
TYPE_MEANINGS = {
    'sticky_pull': 'MA5/10/20/30/60 挤在一起（极差 <1.5%）+ 20 日高低差 <11.7% + 周线重心'
                   '近 6 个完整周里 >=4 周抬升：各周期持仓成本趋同、横得紧、还在慢慢抬高。',
    'pullback_ma20': '60 日涨幅 >15%（趋势还在）+ 现价贴 MA20 ±3% + 缩量（近 5 场含当日均量 < 第 6~60 场均量的 0.9 倍）：'
                     '涨过一波后缩量回到短期均线附近，不是破位。',
    'pullback_ma60': '同上但踩的是 MA60（±5%）：回调更深一档，成本线换到季线附近重新聚集。',
    'obv_bid': '近 20 日"涨日量 - 跌日量"占总成交 >25%，而 60 日涨幅落在 -3%~8%：'
               '钱在进、价没走，吸筹那一类形状（不代表一定有人控盘）。',
    'dragon_back': '最近 60 场内有过放量大阳或涨停（涨幅 >=7% 且量 >= 前 5 日均量 1.8 倍，'
                   '或涨停且量 >=1.5 倍），之后横 7~30 天、期间没跌破那根阳线的最低价、'
                   '且缩量、现价没脱离那根阳线收盘 12% 以内：洗盘没走坏的回头位。',
}

# 合成分的权重。缺的那一块从分母里剔掉，不按 0 计（纪律①）
SCORE_WEIGHTS = {'tight': 40.0, 'weekly': 25.0, 'quiet': 20.0, 'box': 15.0}


def _num(v: Any) -> Optional[float]:
    """Decimal / None / NaN 一律收成 float 或 None。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


# ------------------------------------------------------- 纯判据（不碰库，单测直接打这些）

def ma_spread_pct(mas: Sequence[Optional[float]]) -> Optional[float]:
    """几根均线的极差 / 最低那根（%）。少任何一根就交 None —— 拿缺的那根当 0 会把极差算成假小。"""
    vals = [_num(m) for m in mas]
    vals = [v for v in vals if v is not None and v > 0]
    if len(vals) < 5:
        return None
    lo, hi = min(vals), max(vals)
    return round((hi - lo) / lo * 100.0, 3)


def weekly_metrics(weeks: Sequence[tuple]) -> Dict[str, Any]:
    """周线重心。`weeks` 是倒序（最近在前）的 (周均价, 该周交易日数) 序列。

    只认完整周（>=5 场）参与连升：当前周不足 5 场时不把它算进去（节假日周只有 2~4 场，
    拿它跟 5 天周比重心是假的），但把它的场数一起交出去，界面要能自证。
    """
    if not weeks:
        return {'weekly_streak': None, 'weekly_ups6': None, 'weekly_bars': 0,
                'week_sessions_cur': None, 'week_partial': None}
    cur_sess = _num(weeks[0][1])
    cur_sessions = int(cur_sess) if cur_sess is not None else None
    partial = None if cur_sessions is None else cur_sessions < 5
    means = [m for m in (_num(mm) for mm, s in weeks if (_num(s) or 0) >= 5) if m is not None]
    if len(means) < 2:
        return {'weekly_streak': None, 'weekly_ups6': None, 'weekly_bars': len(means),
                'week_sessions_cur': cur_sessions, 'week_partial': partial}
    streak = 0
    for i in range(len(means) - 1):        # means[0] 是最近一个完整周
        if means[i] <= means[i + 1]:
            break
        streak += 1
    ups6 = sum(1 for i in range(min(6, len(means) - 1)) if means[i] > means[i + 1])
    return {'weekly_streak': streak, 'weekly_ups6': ups6, 'weekly_bars': len(means),
            'week_sessions_cur': cur_sessions, 'week_partial': partial}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def ambush_score(readings: Dict[str, Any]) -> Optional[float]:
    """0~100 的合成分，只为排序（不是涨跌概率）。缺的块不进分母，全缺交 None。

    tight  越紧越高（极差 0→1、SPREAD_MAX→0）
    weekly 近 6 周抬升的周数（0→0、>=5→1）
    quiet  缩量程度（shrink 1.0→0、0.4→1）
    box    60 日区间位置：贴内而不脱沿（80 处最高）
    """
    acc = got = 0.0
    sp = _num(readings.get('ma_spread_pct'))
    if sp is not None:
        acc += SCORE_WEIGHTS['tight'] * _clamp01(1.0 - sp / SPREAD_MAX)
        got += SCORE_WEIGHTS['tight']
    up = _num(readings.get('weekly_ups6'))
    if up is not None:
        acc += SCORE_WEIGHTS['weekly'] * _clamp01(up / 5.0)
        got += SCORE_WEIGHTS['weekly']
    sh = _num(readings.get('shrink_ratio'))
    if sh is not None:
        acc += SCORE_WEIGHTS['quiet'] * _clamp01((1.0 - sh) / 0.6)
        got += SCORE_WEIGHTS['quiet']
    bp = _num(readings.get('box_pos_pct'))
    if bp is not None:
        acc += SCORE_WEIGHTS['box'] * _clamp01(1.0 - abs(bp - 80.0) / 45.0)
        got += SCORE_WEIGHTS['box']
    if got <= 0:
        return None
    return round(acc / got * 100.0, 3)


def judge(code: str, daily: Dict[str, Any], weekly: Dict[str, Any]) -> Dict[str, Any]:
    """把一只票的读数判成一行。判不出 = 没命中，不是 0 分、也不是"很紧"。"""
    r: Dict[str, Any] = dict(daily)
    r.update(weekly)
    bars = _num(r.get('bars_60'))
    tradable = bars is not None and bars >= BARS_MIN

    spread = _num(r.get('ma_spread_pct'))
    range20 = _num(r.get('range20_pct'))
    ups6 = _num(r.get('weekly_ups6'))
    wbars = _num(r.get('weekly_bars'))
    shrink = _num(r.get('shrink_ratio'))
    trend60 = _num(r.get('trend60_pct'))
    dev20 = _num(r.get('dev_ma20_pct'))
    dev60 = _num(r.get('dev_ma60_pct'))
    obv = _num(r.get('obv_ratio'))
    days_big = _num(r.get('days_since_big'))
    hold = _num(r.get('hold_vs_big_pct'))
    kept_low = r.get('post_low_ge')

    is_sticky = bool(tradable and spread is not None and spread < SPREAD_MAX
                     and range20 is not None and range20 < RANGE20_MAX
                     and ups6 is not None and wbars is not None
                     and wbars >= WEEKLY_MIN_BARS and ups6 >= WEEKLY_UPS6_MIN)
    is_pb20 = bool(tradable and trend60 is not None and trend60 > PB_TREND60_MIN
                   and dev20 is not None and abs(dev20) <= PB_DEV_MA20_MAX
                   and shrink is not None and shrink < SHRINK_MAX)
    is_pb60 = bool(tradable and trend60 is not None and trend60 > PB_TREND60_MIN
                   and dev60 is not None and abs(dev60) <= PB_DEV_MA60_MAX
                   and shrink is not None and shrink < SHRINK_MAX)
    is_obv = bool(tradable and obv is not None and obv > OBV_MIN
                  and trend60 is not None and OBV_DRIFT_MIN <= trend60 <= OBV_DRIFT_MAX)
    is_dragon = bool(tradable and days_big is not None
                     and DRAGON_DAYS_MIN <= days_big <= DRAGON_DAYS_MAX
                     and bool(kept_low) and shrink is not None and shrink < SHRINK_MAX
                     and hold is not None and DRAGON_HOLD_MIN <= hold <= DRAGON_HOLD_MAX)

    types = [t for t, hit in (('sticky_pull', is_sticky), ('pullback_ma20', is_pb20),
                              ('pullback_ma60', is_pb60), ('obv_bid', is_obv),
                              ('dragon_back', is_dragon)) if hit]
    missing = [k for k in ('ma_spread_pct', 'range20_pct', 'weekly_ups6', 'shrink_ratio',
                           'trend60_pct', 'obv_ratio', 'box_pos_pct') if _num(r.get(k)) is None]

    return {
        'security_code': code,
        'ma_spread_pct': spread, 'range20_pct': range20, 'close_hfq': _num(r.get('close_hfq')),
        'ma5': _num(r.get('ma5')), 'ma10': _num(r.get('ma10')), 'ma20': _num(r.get('ma20')),
        'ma30': _num(r.get('ma30')), 'ma60': _num(r.get('ma60')),
        'weekly_streak': weekly.get('weekly_streak'), 'weekly_ups6': ups6,
        'weekly_bars': wbars, 'week_sessions_cur': weekly.get('week_sessions_cur'),
        'week_partial': weekly.get('week_partial'),
        'box_pos_pct': _num(r.get('box_pos_pct')),
        'dev_ma20_pct': dev20, 'dev_ma60_pct': dev60, 'trend60_pct': trend60,
        'shrink_ratio': shrink, 'obv_ratio': obv,
        'big_day_date': r.get('big_day_date'),
        'big_low_hfq': _num(r.get('big_low_hfq')), 'big_close_hfq': _num(r.get('big_close_hfq')),
        'days_since_big': None if days_big is None else int(days_big),
        'hold_vs_big_pct': hold,
        'is_sticky': is_sticky, 'is_pullback_ma20': is_pb20, 'is_pullback_ma60': is_pb60,
        'is_obv_bid': is_obv, 'is_dragon_back': is_dragon,
        'ambush_types': types, 'strict_pass': is_sticky,
        'bars_60': None if bars is None else int(bars),
        'missing': ','.join(missing) if missing else None,
        'ambush_score': ambush_score(r),
        'spec_version': SPEC_VERSION,
    }


# --------------------------------------------------------------------- 取数（三条，各 <60 秒）
# 刻意拆成三条：合成一条会超 reader 的 60 秒 statement_timeout
# （2026-10-13 实测被 QueryCanceled 打断过一次）。拆开各 15~30 秒，合并在 Python 侧做。

_DAILY_SQL = """
with lim as (select max(trade_date) mx from market_data.stock_daily where trade_date <= %(day)s),
base as (select sd.security_code c, sd.trade_date dt,
              (sd.close * coalesce(sd.adjustment_factor,1))::numeric hfq,
              (sd.high * coalesce(sd.adjustment_factor,1))::numeric h,
              (sd.low  * coalesce(sd.adjustment_factor,1))::numeric l,
              sd.volume_shares v
         from market_data.stock_daily sd, lim
        where sd.trade_date > (lim.mx - %(span)s::int) and sd.trade_date <= lim.mx),
w as (select c, dt, hfq, h, l, v,
        row_number() over (partition by c order by dt desc) rn,
        lag(hfq) over (partition by c order by dt) prev_h,
        avg(v) over (partition by c order by dt rows between 4 preceding and current row) v5,
        avg(v) over (partition by c order by dt rows between 59 preceding and 5 preceding) v60p,
        avg(hfq) over (partition by c order by dt rows between 4 preceding and current row) m5,
        avg(hfq) over (partition by c order by dt rows between 9 preceding and current row) m10,
        avg(hfq) over (partition by c order by dt rows between 19 preceding and current row) m20,
        avg(hfq) over (partition by c order by dt rows between 29 preceding and current row) m30,
        avg(hfq) over (partition by c order by dt rows between 59 preceding and current row) m60,
        max(h) over (partition by c order by dt rows between 59 preceding and current row) hi60,
        min(l) over (partition by c order by dt rows between 59 preceding and current row) lo60,
        count(*) over (partition by c) n_days
     from base),
agg as (select c,
              max(hfq)  filter (where rn = 1)  close_hfq,
              max(m5)   filter (where rn = 1)  ma5,  max(m10) filter (where rn = 1) ma10,
              max(m20)  filter (where rn = 1)  ma20, max(m30) filter (where rn = 1) ma30,
              max(m60)  filter (where rn = 1)  ma60,
              max(hi60) filter (where rn = 1)  hi60, max(lo60) filter (where rn = 1) lo60,
              max(v5)   filter (where rn = 1)  v5,   max(v60p) filter (where rn = 1) v60p,
              max(hfq)  filter (where rn = 61) hfq60ago,
              max(h) filter (where rn <= 20) - min(l) filter (where rn <= 20) r20abs,
              sum(case when rn <= 20 and prev_h is not null and hfq > prev_h then v
                       when rn <= 20 and prev_h is not null and hfq < prev_h then -v
                       else 0 end) / nullif(sum(v) filter (where rn <= 20), 0) obv_ratio,
              max(n_days) n_days
         from w group by c)
select c security_code, close_hfq, ma5, ma10, ma20, ma30, ma60,
       r20abs / nullif(close_hfq,0) * 100 range20_pct,
       (close_hfq - lo60) / nullif(hi60 - lo60, 0) * 100 box_pos_pct,
       (close_hfq / nullif(ma20,0) - 1) * 100 dev_ma20_pct,
       (close_hfq / nullif(ma60,0) - 1) * 100 dev_ma60_pct,
       (close_hfq / nullif(hfq60ago,0) - 1) * 100 trend60_pct,
       v5 / nullif(v60p,0) shrink_ratio, obv_ratio, n_days bars_60
  from agg where n_days >= 20
"""

# 龙回头。涨停判据是 `raw_close >= limit_up - 0.01`：`limit_up` 存的是涨停价，不是布尔标记
# （同一句话在 02_schema.sql:701 也写着）。
_DRAGON_SQL = """
with lim as (select max(trade_date) mx from market_data.stock_daily where trade_date <= %(day)s),
base as (select sd.security_code c, sd.trade_date dt,
              (sd.close * coalesce(sd.adjustment_factor,1))::numeric hfq,
              (sd.low  * coalesce(sd.adjustment_factor,1))::numeric l,
              sd.close::numeric raw_close, sd.limit_up::numeric lu, sd.volume_shares v
         from market_data.stock_daily sd, lim
        where sd.trade_date > (lim.mx - %(span)s::int) and sd.trade_date <= lim.mx),
w as (select c, dt, hfq, l, raw_close, lu, v,
        row_number() over (partition by c order by dt desc) rn,
        lag(hfq) over (partition by c order by dt) prev_h,
        avg(v) over (partition by c order by dt rows between 5 preceding and 1 preceding) v5prev
     from base),
b as (select *, ((prev_h is not null and hfq/prev_h - 1 >= %(biggain)s and v >= %(bigvol)s*nullif(v5prev,0))
              or (lu is not null and raw_close >= lu - 0.01 and v >= %(limvol)s*nullif(v5prev,0)))::bool is_big
     from w),
a1 as (select c, min(rn) filter (where is_big and rn <= %(lookback)s) big_rn from b group by c),
pin as (select a.c, a.big_rn, x.dt big_dt, x.hfq big_close, x.l big_low
        from a1 a join b x on x.c = a.c and x.rn = a.big_rn where a.big_rn is not null)
select p.c security_code, p.big_dt big_day_date, p.big_close big_close_hfq, p.big_low big_low_hfq,
       (p.big_rn - 1) days_since_big,
       (max(x.hfq) filter (where x.rn = 1) / nullif(p.big_close,0) - 1) * 100 hold_vs_big_pct,
       (min(x.l) filter (where x.rn < p.big_rn) >= p.big_low) post_low_ge
  from pin p join b x on x.c = p.c
 group by p.c, p.big_rn, p.big_dt, p.big_close, p.big_low
"""

# 周线重心按 ISO 周平均。"连续几周上移"刻意留在 Python 的 `weekly_metrics()` 里
# —— 写进 SQL 就没人能单测它（"判据在 SQL 里、单测打不到"是本仓的老洞）。
_WEEKLY_SQL = """
with lim as (select max(trade_date) mx from market_data.stock_daily where trade_date <= %(day)s),
base as (select sd.security_code c,
              extract(isoyear from sd.trade_date)::int
                || lpad(extract(week from sd.trade_date)::int::text, 2, '0') wk,
              (sd.close * coalesce(sd.adjustment_factor,1))::numeric hfq
         from market_data.stock_daily sd, lim
        where sd.trade_date > (lim.mx - %(span)s::int) and sd.trade_date <= lim.mx),
wk as (select c, wk, avg(hfq) wm, count(*) sess from base group by c, wk),
rk as (select c, wk, wm, sess,
              row_number() over (partition by c order by wk desc) wrn from wk)
select c security_code,
       array_agg(wm::text || ',' || sess::text order by wk desc) weeks
  from rk where wrn <= 14 group by c
"""


def _daily_readings(day: dt.date) -> Dict[str, Dict[str, Any]]:
    rows = engine.query(_DAILY_SQL, {'day': day, 'span': CAL_DAYS})
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        d['ma_spread_pct'] = ma_spread_pct([d.get('ma5'), d.get('ma10'), d.get('ma20'),
                                           d.get('ma30'), d.get('ma60')])
        out[str(d['security_code'])] = d
    return out


def _dragon_readings(day: dt.date) -> Dict[str, Dict[str, Any]]:
    """龙回头那几列。最近 60 场内没有大阳/涨停的票不在这张表里（不补 0、不判"守住了"）。"""
    rows = engine.query(_DRAGON_SQL, {'day': day, 'span': CAL_DAYS,
                                      'biggain': BIG_GAIN_MIN / 100.0, 'bigvol': BIG_VOL_MULT,
                                      'limvol': LIMIT_VOL_MULT, 'lookback': DRAGON_LOOKBACK})
    return {str(r['security_code']): dict(r) for r in rows}


def _weekly_series(day: dt.date) -> Dict[str, List[tuple]]:
    """每票最近 14 个 ISO 周的 (周均价, 该周场数)，最近在前。解析不了的那格跳过（不猜）。"""
    rows = engine.query(_WEEKLY_SQL, {'day': day, 'span': CAL_DAYS})
    out: Dict[str, List[tuple]] = {}
    for r in rows:
        series: List[tuple] = []
        for cell in (r.get('weeks') or []):
            mean_txt, _, sess_txt = str(cell).rpartition(',')
            try:
                series.append((float(mean_txt), int(sess_txt)))
            except ValueError:
                continue
        out[str(r['security_code'])] = series
    return out


# ------------------------------------------------------------------------- 一天的活

def compute_day(day: dt.date) -> List[Dict[str, Any]]:
    """算一天：命中至少一种埋伏形态的票才落表（一物一行，同场可多类型）。"""
    daily = _daily_readings(day)
    weekly = _weekly_series(day)
    dragon = _dragon_readings(day)
    rows: List[Dict[str, Any]] = []
    for code, d in daily.items():
        d.update(dragon.get(code) or {})
        row = judge(code, d, weekly_metrics(weekly.get(code) or []))
        if row['ambush_types']:
            row['trade_date'] = day
            rows.append(row)
    rows.sort(key=lambda x: (not x['strict_pass'], -(x['ambush_score'] or -1.0)))
    return rows


_COLS = ('trade_date', 'security_code', 'ma_spread_pct', 'ma5', 'ma10', 'ma20', 'ma30', 'ma60',
         'close_hfq', 'weekly_streak', 'weekly_ups6', 'weekly_bars', 'week_sessions_cur',
         'week_partial', 'range20_pct', 'box_pos_pct', 'dev_ma20_pct', 'dev_ma60_pct',
         'trend60_pct', 'shrink_ratio', 'obv_ratio', 'big_day_date', 'big_low_hfq',
         'big_close_hfq', 'days_since_big', 'hold_vs_big_pct', 'is_sticky', 'is_pullback_ma20',
         'is_pullback_ma60', 'is_obv_bid', 'is_dragon_back', 'ambush_types', 'strict_pass',
         'ambush_score', 'bars_60', 'missing', 'spec_version')

UPSERT = ("insert into analytics.ambush_setup ({cols}) values ({ph}) "
          "on conflict (security_code, trade_date, spec_version) do update set {upd}, "
          "calculated_at = now()").format(
    cols=', '.join(_COLS), ph=', '.join(['%s'] * len(_COLS)),
    upd=', '.join(f'{c} = excluded.{c}' for c in _COLS
                  if c not in ('security_code', 'trade_date', 'spec_version')))


def write(day: dt.date, rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    payload = []
    for r in rows:
        r = dict(r)
        # 直接交 Python list，让 psycopg 适配成 text[]；手写 '{a,b}' 会被当 text ⇒ 类型不匹配
        r['ambush_types'] = list(r.get('ambush_types') or [])
        payload.append(tuple(r.get(c) for c in _COLS))
    engine.execute("delete from analytics.ambush_setup where trade_date = %s and spec_version = %s",
                   (day, SPEC_VERSION), role='etl')
    engine.executemany(UPSERT, payload, role='etl')
    return len(payload)


def latest_day() -> Optional[dt.date]:
    r = engine.query_one("select max(trade_date) d from market_data.stock_daily")
    return r['d'] if r and r.get('d') else None


def trade_days(end: dt.date, recent: int) -> List[dt.date]:
    rows = engine.query("select distinct trade_date d from market_data.stock_daily "
                        "where trade_date <= %s order by trade_date desc limit %s", (end, recent))
    return sorted(r['d'] for r in rows)


def refresh(day: dt.date) -> Dict[str, Any]:
    rows = compute_day(day)
    n = write(day, rows)
    types: Dict[str, int] = {}
    for r in rows:
        for t in r['ambush_types']:
            types[t] = types.get(t, 0) + 1
    return {'day': str(day), 'rows': n, 'strict': sum(1 for r in rows if r['strict_pass']),
            'types': types}


def done_days(since: dt.date, end: dt.date) -> set:
    """这一批里**已经算过**的场次（同 spec_version）。回算近一年要跑几个钟头，
    中途断掉、机器重启、被收盘链挤掉都不该让人从头再来一遍 —— 续跑靠这个集合跳过。
    判据改版换了 spec_version，旧行留着对账，所以它不会被当成「已经算过」。"""
    rows = engine.query(
        "SELECT trade_date AS d FROM analytics.ambush_setup "
        "WHERE spec_version = %s AND trade_date BETWEEN %s AND %s GROUP BY 1",
        (SPEC_VERSION, since, end), role='reader') or []
    return {r['d'] for r in rows if isinstance(r['d'], dt.date)}


def run(day: Optional[dt.date] = None, recent: int = 1, since: Optional[dt.date] = None,
        dry_run: bool = False, skip_done: bool = False,
        max_minutes: Optional[float] = None) -> int:
    end = day or latest_day()
    if not end:
        print('没有可算的交易日（market_data.stock_daily 是空的？）')
        return 1
    days = trade_days(end, recent)
    if since:
        days = [d for d in trade_days(end, 400) if d >= since]
    if dry_run:
        rows = compute_day(days[-1])
        print(f'{days[-1]} 命中 {len(rows)} 只（严档 {sum(1 for r in rows if r["strict_pass"])} 只），前 20 名：')
        for r in rows[:20]:
            print(f'  {r["security_code"]} 极差 {_num(r["ma_spread_pct"])}% 振幅 {_num(r["range20_pct"])}% '
                  f'抬升 {r["weekly_ups6"]}/6 周 缩量 {_num(r["shrink_ratio"])} '
                  f'分 {_num(r["ambush_score"])} 类型 {"、".join(TYPE_LABELS.get(t, t) for t in r["ambush_types"])}')
        return 0

    skipped = []
    if skip_done and len(days) > 1:
        done = done_days(days[0], days[-1])
        skipped = [d for d in days if d in done]
        days = [d for d in days if d not in done]
        print(f'已算过 {len(skipped)} 场（同 {SPEC_VERSION}），这一遍只补剩下 {len(days)} 场')
    if not days:
        print('这一批都已经算过，不需要重跑（要重来就去掉 --skip-done）')
        return 0

    total = failed = 0
    start = time.monotonic()
    for i, d in enumerate(days, 1):
        if max_minutes and (time.monotonic() - start) / 60.0 >= max_minutes:
            print(f'⏸ 自限预算 {max_minutes:.0f} 分钟用完：{len(days) - i + 1} 场没轮到'
                  f'（从 {d} 起）。再跑同一条 --skip-done 命令就会接着这几场补，'
                  f'不重算已经写好的 {total} 行。')
            return 4
        try:
            info = refresh(d)
        except Exception as e:      # noqa: BLE001  一场算崩不许带走整批：留着下一场，最后按失败收
            failed += 1
            print(f'[{i}/{len(days)}] {d} 失败：{type(e).__name__}: {e}'
                  ' —— 跳过这一场继续（补跑同一条命令会再来一次）')
            continue
        total += info['rows']
        print(f'[{i}/{len(days)}] {info["day"]} 写 {info["rows"]} 行（严档 {info["strict"]}） 类型 {info["types"]}')
    print(f'合计写入 {total} 行（spec_version={SPEC_VERSION}）'
          + (f'，另有 {failed} 场失败' if failed else ''))
    if failed:
        return 3
    return 0 if total else 2


def main() -> int:
    ap = argparse.ArgumentParser(description='埋伏票（左侧机会）榜：analytics.ambush_setup')
    ap.add_argument('--date', default='')
    ap.add_argument('--recent', type=int, default=1)
    ap.add_argument('--since', default='')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-done', action='store_true',
                    help='回算用：这一批里已经算过的场次直接跳过（断点续跑）')
    ap.add_argument('--max-minutes', type=float, default=None,
                    help='回算用：跑满这么多分钟就停下并报告剩几场（别让长批挤进收盘链）')
    a = ap.parse_args()
    return run(day=dt.date.fromisoformat(a.date) if a.date else None,
               recent=a.recent,
               since=dt.date.fromisoformat(a.since) if a.since else None,
               dry_run=a.dry_run,
               skip_done=a.skip_done,
               max_minutes=a.max_minutes)


if __name__ == '__main__':
    raise SystemExit(main())
