# -*- coding: utf-8 -*-
"""异动筛查（盘中事件）判据与写入：`analytics.intraday_spike`。

用户 2026-10-13 点名要的功能：复盘最近两个月盘中「瞬间冲高 5 个点以上」或「冲到涨停」的票，
之后走势分别怎么样，并按最近一天冲高的**时间顺序**排。

母体口径（用户选定「水平＋速度组合」）：

    日内最高较前收 >= 5%  **且**  （15 分钟内拉升 >= 4 个点  或  触及涨停价）

为什么要有速度那一刀：只按水平（>=5%）实测 2026-08-14~10-13 是 19,588 条 / 37 场 = 平均每天 529 只，
里面混着大量「慢涨一天、收盘还在高位」的票 —— 那与「一瞬间拉起来再被打下来」根本不是一回事，
而复盘里真正有区分度的三把尺子（峰值后 5 分钟守不守得住、拉升速度、高点出现在几点）
都被混在一起稀释掉了。加速度这一刀后每天约 350~400 条。

**缺分钟线怎么办**（这一步必须出声，不许静默缩榜）：速度那一刀判不了 ⇒ 事件按水平档收，
`minute_available=false` 与 `missing` 一起写清，读侧与页面必须显出来。当天分钟线整场缺失时
`refresh()` 交 `degraded=True`，加工段据此记 degraded（不是 rc=0 的"成功"）。

复盘实测（判据的出处，改判据要先重跑这些数）：
  · 幅度档（两个月，日线，n=19,588）：5~7 档次日上涨 41.6%、7~10 档 39.8%、10~15 档 39.4%、
    15~20 档 37.2%、触板未封 36.3%、触板封住 51.0%；同期基线 46.0%。封住涨停的溢价只有次日一天
    （3 日 −0.02%、5 日 −0.71%）。
  · 承接档（9 场分钟样本，基线 37.9%）：峰值后 5 分钟回撤 <1 个点 40.0%（n=1,224）、
    回撤 1~3 个点 33.1%、回撤 >3 个点 34.4% —— 这是最强的一把尺子。
  · 速度档：慢拉 <4 个点 37.6%、4~7 个点 35.6%、>7 个点 33.9%（急拉次日最差，且单调）。
  · 时刻档：10:00 前 36.9%、10:00~11:30 35.1%、13:00~14:30 32.7%、14:30 后 36.7%。
  · 没有区分度的两把尺子（所以不给它们加"信号"标签）：收盘回落深度（39.3~44.8% 无单调）、
    全天均价线上方占比（35.2~36.0% 三档一样）。
  · 封住时长（09-30 全市场 72 只触板）：封 0 分钟 6 只、1~30 分 8、31~120 分 14、121~200 分 10、
    >200 分 34；9 场 861 次触板里"全程没开板" 52.2% vs "开了又回封" 39.4%。

三条纪律：
① 分钟级读数算不出就是 NULL，不按 0 参与排序或判据（0 会被读成「没人卖」「没守住」，方向常常相反）；
② 判据只住本文件，接口与页面只读算好的行、不自己判一遍；
③ 次日/3 日/5 日走势**不入本表** —— 那是读侧用 lead + 复利现算的，入表就要每天回写昨天的行，
   两个写入方早晚会漂；而且统计窗口要能跟着用户改，算好的数锁在行里反而动不了。

用法：
    python -m app.compute.intraday_spike                      # 最新有日线的那一天
    python -m app.compute.intraday_spike --date 2026-10-13
    python -m app.compute.intraday_spike --recent 5
    python -m app.compute.intraday_spike --since 2026-08-14 --skip-done --max-minutes 30
    python -m app.compute.intraday_spike --dry-run            # 只打前 20 条不写库

退出码：0 成功 / 1 没有可算的交易日 / 2 一趟都没写进去 /
        3 有场次算失败或分钟线缺失（已写下的留着，重跑只补没成的）/ 4 自限预算用完。
"""
from __future__ import annotations

import argparse
import datetime as dt
import time
from typing import Any, Dict, List, Optional, Sequence

from app.db import engine

SPEC_VERSION = 'spk-1'

# ---- 判据阈值（出处见模块 docstring；改判据就换 SPEC_VERSION） ----
LEVEL_MIN_PCT = 5.0      # 日内最高较前收的母体门槛（用户点名的「5 个点」）
SPEED_MIN_PCT = 4.0      # 15 分钟滚动窗口内拉升达到它才算「瞬间」
FAST_PCT = 7.0           # 急拉（网上叫「尖刀式脉冲」）那一档
HOLD_DD_PCT = -1.0       # 峰值后 5 分钟回撤浅于它 = 「守住了」
SEAL_TOL = 0.005         # 封住判据：|close - 涨停价| <= 它（A 股最小变动 0.01，别用 0.5% 容差）
SPEED_WINDOW = 15        # 滚动窗口根数（1 分钟 bar ⇒ 15 分钟）
TAIL_FROM = dt.time(14, 30)   # 尾盘起算点
# 同一日多峰 v1 不切：一行只描述「日内最高那一次」，切多峰要先定义回落多少算一个新峰。

TAG_LABELS = {          # 标签只有这一处定义，页面读接口交出的那一份
    'fast': '急拉（15 分钟内 >=7 个点）',
    'quick': '快拉（4~7 个点）',
    'slow': '慢拉（不足 4 个点）',
    'touched': '盘中触及涨停价',
    'sealed': '收盘封住涨停',
    'held5': '峰值后 5 分钟守住（回撤不足 1 个点）',
    'late': '高点出现在 14:30 后',
    'reopen': '开板后又回封',
}

_COLUMNS = ('trade_date', 'security_code', 'pre_close', 'day_high', 'day_low', 'open_price',
            'close_price', 'hi_pct', 'cl_pct', 'open_pct', 'fade_pct', 'upper_shadow_pct',
            'limit_up', 'board_pct', 'touched', 'sealed', 'touch_min', 'sealed_min', 'board_runs',
            'first_seal_time', 'peak_time', 'first5_time', 'peak_min_no', 'speed15_pct',
            'speed15_time', 'above5_min', 'after5_low_close', 'after5_dd_pct', 'above_vwap_pct',
            'tail_above_vwap', 'day_vol_shares', 'vol_above5_share', 'pre_peak_min_vol',
            'post_peak_min_vol', 'fade_vol_ratio', 'gain20_pct', 'is_hi60_break', 'spike_tags',
            'bars_used', 'minute_available', 'missing', 'spec_version')


def _num(v: Any) -> Optional[float]:
    """库里的 Decimal / 日期都收成 float 或 None；NaN 不算数。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if v != v else float(v)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def qualifies(hi_pct: Optional[float], speed15_pct: Optional[float],
              touched: bool, minute_available: bool) -> bool:
    """母体：水平 >=5% 且（15 分钟速度 >=4 个点 或 触及涨停价）。

    `minute_available=false` 时速度判不了 —— 按水平档收，让调用方用 missing 出声，
    而不是把这一天整场悄悄丢掉（缩了榜而屏幕上看不出区别，是最坏的那种错）。
    """
    if hi_pct is None or hi_pct < LEVEL_MIN_PCT:
        return False
    if touched:
        return True
    if speed15_pct is not None:
        return speed15_pct >= SPEED_MIN_PCT
    return not minute_available


def spike_tags(row: Dict[str, Any]) -> List[str]:
    """标签的唯一生成处。顺序固定，页面直接按这个顺序渲染徽章。"""
    sp, dd = _num(row.get('speed15_pct')), _num(row.get('after5_dd_pct'))
    pt, runs = row.get('peak_time'), _num(row.get('board_runs'))
    out = []
    if sp is not None:
        out.append('fast' if sp >= FAST_PCT else ('quick' if sp >= SPEED_MIN_PCT else 'slow'))
    if row.get('touched'):
        out.append('touched')
    if row.get('sealed'):
        out.append('sealed')
    if dd is not None and dd > HOLD_DD_PCT:
        out.append('held5')
    if isinstance(pt, dt.time) and pt >= TAIL_FROM:
        out.append('late')
    if runs is not None and runs >= 2:
        out.append('reopen')
    return out


def minute_metrics(bars: Sequence[Dict[str, Any]], pre_close: Optional[float],
                   limit_up: Optional[float]) -> Dict[str, Any]:
    """从一摞 1 分钟 bar 算出全部分钟级读数（不碰库，单测就打这里）。

    bar 的键：`t`（naive 上海时刻）、`high`、`low`、`close`、`volume_shares`、`amount_yuan`，
    必须已按 t 升序。bar 是**右端标注**：09:35 那根覆盖 09:30~09:35。
    """
    n = len(bars)
    out: Dict[str, Any] = {'bars_used': n, 'peak_time': None, 'peak_min_no': None,
                           'first5_time': None, 'above5_min': None, 'speed15_pct': None,
                           'speed15_time': None, 'after5_low_close': None, 'after5_dd_pct': None,
                           'above_vwap_pct': None, 'tail_above_vwap': None, 'touch_min': None,
                           'sealed_min': None, 'board_runs': None, 'first_seal_time': None,
                           'vol_above5_share': None, 'pre_peak_min_vol': None,
                           'post_peak_min_vol': None, 'fade_vol_ratio': None}
    if not n:
        return out

    highs = [_num(b.get('high')) or 0.0 for b in bars]
    lows = [_num(b.get('low')) for b in bars]
    closes = [_num(b.get('close')) for b in bars]
    vols = [_num(b.get('volume_shares')) or 0.0 for b in bars]
    amts = [_num(b.get('amount_yuan')) or 0.0 for b in bars]

    day_high = max(highs)
    peak = highs.index(day_high)                      # 首次到达日高那一根
    out['peak_time'] = bars[peak]['t'].time()
    out['peak_min_no'] = peak + 1

    # 累计口径的均价线（当日 VWAP）：成交额累计 ÷ 成交量累计
    cv = ca = 0.0
    vwaps = []
    for i in range(n):
        cv += vols[i]
        ca += amts[i]
        vwaps.append(ca / cv if cv > 0 else None)
    above = sum(1 for i in range(n) if closes[i] is not None and vwaps[i] and closes[i] > vwaps[i])
    out['above_vwap_pct'] = round(100.0 * above / n, 1)
    tail = [i for i in range(n) if bars[i]['t'].time() >= TAIL_FROM]
    if tail:
        t_above = sum(1 for i in tail if closes[i] is not None and vwaps[i] and closes[i] > vwaps[i])
        out['tail_above_vwap'] = round(100.0 * t_above / len(tail), 1)

    if pre_close and pre_close > 0:
        lvl = pre_close * (1 + LEVEL_MIN_PCT / 100.0)
        idx5 = [i for i in range(n) if highs[i] >= lvl]
        out['above5_min'] = len(idx5)
        if idx5:
            out['first5_time'] = bars[idx5[0]]['t'].time()
        tot = sum(vols)
        if tot > 0:
            out['vol_above5_share'] = round(100.0 * sum(vols[i] for i in idx5) / tot, 2)

    if limit_up and limit_up > 0:
        touch = [i for i in range(n) if highs[i] >= limit_up - SEAL_TOL]
        seal_flags = [closes[i] is not None and abs(closes[i] - limit_up) <= SEAL_TOL
                      for i in range(n)]
        out['touch_min'] = len(touch)
        out['sealed_min'] = sum(1 for f in seal_flags if f)
        runs = prev = 0
        first_seal = None
        for i, f in enumerate(seal_flags):
            cur = 1 if f else 0
            if cur and not prev:
                runs += 1
                if first_seal is None:
                    first_seal = bars[i]['t'].time()
            prev = cur
        out['board_runs'] = runs
        out['first_seal_time'] = first_seal

    # 任意 15 根连续窗口里的最大拉升：峰值用窗口内最低点起算（与复盘脚本同一条 SQL 口径）
    best, best_i = None, None
    for i in range(n):
        lo = min((x for x in lows[max(0, i - SPEED_WINDOW + 1):i + 1] if x and x > 0), default=None)
        if not lo:
            continue
        rise = 100.0 * (highs[i] / lo - 1.0)
        if best is None or rise > best:
            best, best_i = rise, i
    if best is not None and best_i is not None:
        out['speed15_pct'] = round(best, 2)
        out['speed15_time'] = bars[best_i]['t'].time()

    after = [c for c in closes[peak + 1:peak + 6] if c is not None]
    if after and day_high > 0:
        out['after5_low_close'] = min(after)
        out['after5_dd_pct'] = round(100.0 * (min(after) / day_high - 1.0), 2)

    pre, post = vols[:peak], vols[peak + 1:]
    if pre:
        out['pre_peak_min_vol'] = round(sum(pre) / len(pre), 1)
    if post:
        out['post_peak_min_vol'] = round(sum(post) / len(post), 1)
    if pre and post:
        out['fade_vol_ratio'] = round((sum(post) / len(post)) / (sum(pre) / len(pre)), 3)
    return out


def _daily_readings(day: dt.date) -> List[Dict[str, Any]]:
    """日线侧的候选与位置读数。母体的水平那一刀先在这里筛掉，分钟线只查这批票。"""
    start = day - dt.timedelta(days=150)
    return engine.query("""
        with b as (select security_code, trade_date, open, high, low, close, pre_close, limit_up,
                          limit_basis, pct_change, volume_shares, adjustment_factor,
                          sum(pct_change) over (partition by security_code order by trade_date
                              rows between 20 preceding and 1 preceding) gain20,
                          max(high * adjustment_factor) over (partition by security_code
                              order by trade_date rows between 60 preceding and 1 preceding) hi60
                     from market_data.stock_daily
                    where trade_date between %s and %s and pre_close > 0 and close > 0
                      and adjustment_factor > 0)
        select security_code, open, high, low, close, pre_close, limit_up, limit_basis,
               pct_change, volume_shares, gain20, hi60, adjustment_factor,
               round(100.0 * (high / nullif(pre_close, 0) - 1), 3)         as hi_pct,
               round(100.0 * (close / nullif(pre_close, 0) - 1), 3)        as cl_pct,
               round(100.0 * (open / nullif(pre_close, 0) - 1), 3)         as open_pct,
               round(100.0 * (close / nullif(high, 0) - 1), 3)             as fade_pct,
               round(100.0 * (high - greatest(open, close))
                     / nullif(high - low, 0), 3)                           as upper_shadow_pct,
               round(100.0 * (limit_up / nullif(pre_close, 0) - 1), 2)     as board_pct,
               (limit_up is not null and high >= limit_up - 0.005)         as touched,
               (limit_up is not null and abs(close - limit_up) <= 0.005)   as sealed
          from b
         where trade_date = %s and high >= pre_close * (1 + %s / 100.0)
         order by security_code""", (start, day, day, LEVEL_MIN_PCT), role='reader')


def _minute_bars(day: dt.date, codes: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    """只查候选票的分钟线，按票分组带回。返回空字典 = 这一天分钟线整场没入库。"""
    if not codes:
        return {}
    rows = engine.query("""
        select security_code, dt at time zone 'Asia/Shanghai' as t,
               open, high, low, close, volume_shares, amount_yuan
          from market_data.stock_minute
         where period = 1 and dt::date = %s and security_code = any(%s::text[])
         order by security_code, dt""", (day, list(codes)), role='reader')
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        grouped.setdefault(r['security_code'], []).append(r)
    return grouped


def compute_day(day: dt.date) -> List[Dict[str, Any]]:
    dailies = _daily_readings(day)
    bars_by_code = _minute_bars(day, [d['security_code'] for d in dailies])
    out = []
    for d in dailies:
        bars = bars_by_code.get(d['security_code'], [])
        mm = minute_metrics(bars, _num(d['pre_close']), _num(d['limit_up']))
        hi, sp = _num(d['hi_pct']), mm['speed15_pct']
        has_min = mm['bars_used'] > 0
        if not qualifies(hi, sp, bool(d['touched']), has_min):
            continue
        row = {
            'trade_date': day, 'security_code': d['security_code'],
            'pre_close': _num(d['pre_close']), 'day_high': _num(d['high']),
            'day_low': _num(d['low']), 'open_price': _num(d['open']),
            'close_price': _num(d['close']), 'hi_pct': hi, 'cl_pct': _num(d['cl_pct']),
            'open_pct': _num(d['open_pct']), 'fade_pct': _num(d['fade_pct']),
            'upper_shadow_pct': _num(d['upper_shadow_pct']),
            'limit_up': _num(d['limit_up']), 'board_pct': _num(d['board_pct']),
            'touched': bool(d['touched']), 'sealed': bool(d['sealed']),
            'day_vol_shares': _num(d['volume_shares']),
            'gain20_pct': _num(d['gain20']),
            # 60 日新高要比后复权价：拿未复权 high 去比，除权那天必然"没创新高"（分子分母不是一个价）
            'is_hi60_break': (_num(d['high']) is not None and _num(d['hi60']) is not None
                              and _num(d['high']) * (_num(d['adjustment_factor']) or 1.0)
                              >= _num(d['hi60'])),
            'bars_used': mm['bars_used'], 'minute_available': has_min,
            **{k: mm[k] for k in ('touch_min', 'sealed_min', 'board_runs', 'first_seal_time',
                                  'peak_time', 'first5_time', 'peak_min_no', 'speed15_pct',
                                  'speed15_time', 'above5_min', 'after5_low_close',
                                  'after5_dd_pct', 'above_vwap_pct', 'tail_above_vwap',
                                  'vol_above5_share', 'pre_peak_min_vol', 'post_peak_min_vol',
                                  'fade_vol_ratio')},
        }
        row['spike_tags'] = spike_tags(row)
        row['spec_version'] = SPEC_VERSION
        miss = [k for k in ('speed15_pct', 'peak_time', 'after5_dd_pct', 'fade_vol_ratio',
                            'sealed_min', 'gain20_pct', 'is_hi60_break', 'above_vwap_pct')
                if row[k] is None]
        if not has_min:
            miss.append('minute_bars')
        row['missing'] = ','.join(miss) or None
        out.append(row)
    return out


UPSERT = ("insert into analytics.intraday_spike ({cols}) values ({ph}) "
          "on conflict (security_code, trade_date, spec_version) do update set {upd}, "
          "calculated_at = now()")
_PK = {'security_code', 'trade_date', 'spec_version'}


def write(day: dt.date, rows: List[Dict[str, Any]]) -> int:
    # **先删这一场这一版本的旧行，再写**。这一屏的母体会随"分钟线到没到"变多变更少：
    # 2026-10-15 实测，17:44 那一场在分钟线缺失时写了 224 条（速度那一刀判不了，
    # 整批 `minute_available=false`），补跑之后真事件只有 218 条 —— 只 upsert 不清理，
    # 那 6 条就是上一次半截计算的残留，页面会把它们读成"今天有 6 个事件的涨速判不了"，
    # 而实际上今天一条都没判不了。空批也必须删：那表示重算后这一场一条都不成立。
    with engine.cursor(role='etl') as cur:
        cur.execute("delete from analytics.intraday_spike "
                    "where trade_date = %s and spec_version = %s",
                    (day, SPEC_VERSION))
    if not rows:
        return 0
    cols = ', '.join(_COLUMNS)
    ph = ', '.join(['%s'] * len(_COLUMNS))
    upd = ', '.join(f'{c} = excluded.{c}' for c in _COLUMNS if c not in _PK)
    payload = [tuple(r.get(c) for c in _COLUMNS) for r in rows]
    with engine.cursor(role='etl') as cur:
        cur.executemany(UPSERT.format(cols=cols, ph=ph, upd=upd), payload)
    return len(rows)


def latest_day() -> Optional[dt.date]:
    r = engine.query_one("select max(trade_date) d from market_data.stock_daily", role='reader')
    return r['d'] if r and r['d'] else None


def trade_days(end: dt.date, recent: int) -> List[dt.date]:
    rows = engine.query("""select distinct trade_date d from market_data.stock_daily
                            where trade_date <= %s order by d desc limit %s""",
                        (end, recent), role='reader')
    return sorted(r['d'] for r in rows)


def days_between(start: dt.date, end: dt.date) -> List[dt.date]:
    rows = engine.query("""select distinct trade_date d from market_data.stock_daily
                            where trade_date between %s and %s order by d""",
                        (start, end), role='reader')
    return [r['d'] for r in rows]


def refresh(day: dt.date) -> Dict[str, Any]:
    minute_empty = _minute_day_empty(day)
    rows = compute_day(day)
    written = write(day, rows)
    no_min = sum(1 for r in rows if not r['minute_available'])
    return {'day': day, 'events': len(rows), 'written': written, 'minute_missing': no_min,
           # 分钟线整场缺失 ⇒ 速度/承接那一刀根本没生效，记降级而不是"成功"（这一条与 #136 同类）
           'degraded': minute_empty or no_min > 0}


def _minute_day_empty(day: dt.date) -> bool:
    r = engine.query_one("select count(*) n from market_data.stock_minute "
                         "where period = 1 and dt::date = %s", (day,), role='reader')
    return not (r and r['n'])


def done_days(since: dt.date, end: dt.date) -> set:
    rows = engine.query("""select distinct trade_date d from analytics.intraday_spike
                            where trade_date between %s and %s and spec_version = %s""",
                        (since, end, SPEC_VERSION), role='reader')
    return {r['d'] for r in rows}


def run(day: Optional[dt.date] = None, recent: int = 1, since: Optional[dt.date] = None,
        dry_run: bool = False, skip_done: bool = False,
        max_minutes: Optional[float] = None) -> int:
    end = day or latest_day()
    if not end:
        print('库里没有日线，没什么可算')
        return 1
    if day:
        # 每日链传的是 `--date 目标日 --recent 1`；给了更大的 recent 就按"以该日结尾的 N 个
        # 交易日"补，语义与 breadth/ambush 那几步一致，别让 --recent 在带 --date 时静默失效。
        days = trade_days(day, max(1, recent)) if recent > 1 else [day]
    elif since:
        days = days_between(since, end)
    else:
        days = trade_days(end, recent)
    if skip_done and len(days) > 1:
        done = done_days(days[0], days[-1])
        before = len(days)
        days = [d for d in days if d not in done]
        print(f'  跳过已算好的 {before - len(days)} 场，还剩 {len(days)} 场')
    if not days:
        print('没有要算的交易日')
        return 1
    print(f'异动筛查 spec_version={SPEC_VERSION}，{len(days)} 场：{days[0]} ~ {days[-1]}')
    start_t = time.monotonic()
    wrote_any = failed = degraded = 0
    for i, d in enumerate(days, 1):
        if max_minutes and (time.monotonic() - start_t) / 60.0 >= max_minutes:
            print(f'⏸ 自限预算 {max_minutes:.0f} 分钟用完：{len(days) - i + 1} 场没轮到，'
                  f'重跑同一条命令（带 --skip-done）继续')
            return 4
        try:
            if dry_run:
                rows = compute_day(d)
                print(f'  {d} 事件 {len(rows)} 条（dry-run，前 5 条）')
                for r in rows[:5]:
                    print(f"    {r['security_code']} 最高 {r['hi_pct']}% 高点 {r['peak_time']} "
                          f"速度 {r['speed15_pct']}% 峰值后5分回撤 {r['after5_dd_pct']}% "
                          f"板 {'封' if r['sealed'] else ('触' if r['touched'] else '-')} "
                          f"{r['spike_tags']}")
                wrote_any += 1
                continue
            res = refresh(d)
            wrote_any += 1 if res['written'] else 0
            flag = ' ⚠️ 分钟线缺失，速度判据没生效' if res['degraded'] else ''
            print(f"  {res['day']} 事件 {res['events']} 条，写 {res['written']} 条"
                  f"（{res['minute_missing']} 条无分钟读数）{flag}")
            degraded += 1 if res['degraded'] else 0
        except Exception as e:      # noqa: BLE001 —— 回算一批时单场失败不能带走整批
            failed += 1
            print(f'  {d} 失败：{type(e).__name__}: {e}')
    if failed or degraded:
        print(f'⚠️ {failed} 场失败、{degraded} 场降级（分钟线缺失）——已写下的留着')
        return 3
    return 0 if wrote_any else 2


def main() -> int:
    ap = argparse.ArgumentParser(description='异动筛查（盘中事件）：analytics.intraday_spike')
    ap.add_argument('--date', help='只算这一天（YYYY-MM-DD）')
    ap.add_argument('--recent', type=int, default=1, help='往前数 N 个交易日')
    ap.add_argument('--since', help='从这一天起把所有交易日都算一遍')
    ap.add_argument('--skip-done', action='store_true', help='跳过表里已有该 spec_version 的场')
    ap.add_argument('--max-minutes', type=float, help='自限预算，到点就停并交还剩几场')
    ap.add_argument('--dry-run', action='store_true', help='只打前几条不写库')
    a = ap.parse_args()
    return run(day=dt.date.fromisoformat(a.date) if a.date else None,
               recent=a.recent,
               since=dt.date.fromisoformat(a.since) if a.since else None,
               dry_run=a.dry_run, skip_done=a.skip_done, max_minutes=a.max_minutes)


if __name__ == '__main__':
    raise SystemExit(main())
