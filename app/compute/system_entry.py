# -*- coding: utf-8 -*-
"""交易体系榜（组合判据）：`analytics.system_entry`。

用户 2026-10-14 口述的一套"交易体系"，三条同时成立才算，不是任一单形态：

  ① 近 20 个场次内出现过封住的涨停 —— 原话「二十天之内一定有一个涨停的……有资金的地方、有鱼的鱼塘，
     我们才玩，这是最前提一个条件，你没这个条件你全都是浪费时间」；
  ② 向上**整段跳空**的缺口，**三天不回补**，且到今天仍未回补 —— 原话「主力花了钱去抢筹……
     或者他快速把成本拉高，不给你散户上车机会」「回调了都是弱势股」；
  ③ 缺口之后缩量回调（一天两天三天都行，**回调的阴是缩量的**），再来**一根阳线确认** ——
     原话「他得歇口气……货出不去，到这个时候才是你上车的机会……带好你的止损，冲」。

口径全部按用户当日选定的那一份，写死在这里一处：

  · 跳空 = 缺口日**最低价 > 前一日最高价**（整段没重叠，不是只开盘跳空），且缺口幅度 >= 1 个点
    （0.2% 那种贴线不算，实测不设下限的母体大得多也脏得多）；
  · 不回补 = 缺口之后连续 3 个场次的**最低价**都没跌回缺口下沿（= 缺口日前一日最高），
    且今日最低仍在下沿之上；
  · 缺口日到今日限 **4~7 个场次**：3 场用来确认不回补，剩下 1~3 场就是"歇口气"那一段；
  · 缩量 = 回调段（今天往前 3 场）最大单日量 <= 缺口日量，且其中至少一根阴线；
  · 确认 = 今日后复权收盘 > 开盘 且 > 前收；
  · 止损参考位 = 缺口下沿（与判据同源：跌破就是这套体系的证据被推翻）。

价格一律后复权（`× adjustment_factor`）。拿未复权价判跳空会在除权日造出一堆假缺口，
这条不是洁癖：本机 `stock_daily` 的 `pre_close` 是除权调整后的口径，两种尺度混用必然错。

⚠️ 落这张表不等于担保它赚钱。本机 1.7 年全市场实测（见 `system_entry_view` 交出的历史分布）：
   三条件全过 + 阳线 ≈ 平均每天 6.8 只，相对基线**右尾更肥**（后 20 日冲出两成 17.9% vs 9.1%）、
   **胜率与期望更差**（次日上涨 45.3% vs 48.7%；四档止盈/止损配对下带止损的期望都只有基线的三分之一）。
   榜单页面必须把这两句一起说，不许只留好听的半句。

用法：
    python -m app.compute.system_entry                       # 最新有日线的那一天
    python -m app.compute.system_entry --date 2026-10-13
    python -m app.compute.system_entry --recent 5
    python -m app.compute.system_entry --since 2025-10-14 --skip-done --max-minutes 40
    python -m app.compute.system_entry --dry-run             # 只打前 20 条不写库

退出码：0 成功 / 1 没有可算的交易日 / 2 一趟都没写进去 / 3 有场次算失败 / 4 自限预算用完。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import time
from typing import Any, Dict, List, Optional, Sequence

from app.db import engine

SPEC_VERSION = 'sys-1'

# ---- 判据阈值（出处见模块 docstring；改判据就换 SPEC_VERSION） ----
LIMIT_LOOKBACK = 20      # ① 往前数几个场次内必须有涨停
GAP_MIN_PCT = 1.0        # ② 缺口幅度下限（个点）
GAP_UNFILLED_DAYS = 3    # ② 要连续几个场次不回补才算"三天不回补"
GAP_AGO_MIN = 4          # ② 缺口日距今最少几个场次（= 3 场确认 + 至少 1 场回调）
GAP_AGO_MAX = 7          # ② 缺口日距今最多几个场次（3 场确认 + 最多 3 场回调）
PULLBACK_WINDOW = 3      # ③ 回调段看今天往前几场
SEAL_TOL = 0.005         # 涨停封住的判据容差（A 股最小变动 0.01，与 limit_prices 那套同一把尺）
SERIES_BARS = 30         # 每次往前取几根日线（要覆盖 20 场涨停窗口 + 7 场缺口窗口）

_COLUMNS = ('trade_date', 'security_code', 'open', 'high', 'low', 'close', 'pre_close',
            'pct_change', 'volume_shares', 'adjustment_factor', 'close_hfq',
            'c1_ok', 'limit_up_count20', 'last_limit_up_date', 'limit_up_ago',
            'c2_ok', 'gap_date', 'gap_ago', 'gap_pct', 'gap_floor_hfq', 'gap_unfilled',
            'gap_vol_shares', 'c3_ok', 'pullback_sessions', 'pullback_down_cnt',
            'pullback_max_vol_ratio', 'pullback_min_low_hfq',
            'confirm_yang', 'vol_vs_prev', 'entry_ok', 'watch_ok',
            'stop_ref_hfq', 'stop_dist_pct', 'bars_used', 'missing', 'spec_version')


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if v != v else float(v)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _int(v: Any) -> Optional[int]:
    """只数（分母）走整数：落 integer 列，过 `_num` 会变成 5568.0 那种带小数的只数。"""
    f = _num(v)
    return None if f is None else int(f)


def _d(s: Any) -> Optional[dt.date]:
    if isinstance(s, dt.date):
        return s
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def judge(code: str, series: Sequence[Sequence]) -> Optional[Dict[str, Any]]:
    """纯判据：吃一只票**按时间升序**的最近若干根日线，吐出这一场的全部读数。

    每根 bar 的字段顺序（与 `_series` 的 jsonb 一致）：
    `[日期, 开, 高, 低, 收, 前收, 涨跌%, 涨停价, 成交量, 复权因子]`。
    不碰库、不碰表 —— 单测就打这里。三条不成立时返回 None（观察池都不要的票不进表）。
    """
    n = len(series)
    if n < 2:
        return None
    bars = []
    for r in series:
        af = _num(r[9]) or 1.0
        bars.append({
            'date': _d(r[0]),
            'o': (_num(r[1]) or 0.0) * af, 'h': (_num(r[2]) or 0.0) * af,
            'l': (_num(r[3]) or 0.0) * af, 'c': (_num(r[4]) or 0.0) * af,
            'pre': (_num(r[5]) or 0.0) * af, 'pct': _num(r[6]),
            'lu': _num(r[7]), 'vol': _num(r[8]) or 0.0, 'af': af,
            'raw_c': _num(r[4]), 'raw_o': _num(r[1]), 'raw_h': _num(r[2]), 'raw_l': _num(r[3]),
            'raw_pre': _num(r[5]),
        })
    t = bars[-1]

    # ── ① 近 LIMIT_LOOKBACK 场内有封住的涨停 ──
    sealed_idx = [i for i in range(max(0, n - LIMIT_LOOKBACK), n)
                  if bars[i]['lu'] and abs(bars[i]['raw_c'] - bars[i]['lu']) <= SEAL_TOL]
    enough_history = n >= LIMIT_LOOKBACK
    c1 = enough_history and bool(sealed_idx)

    # ── ② 向上整段跳空 + 三天不回补 + 今天仍未回补（取最近一个合格缺口）──
    gap_i: Optional[int] = None
    for ago in range(GAP_AGO_MIN, GAP_AGO_MAX + 1):
        i = n - 1 - ago
        if i < 1:
            continue
        floor = bars[i - 1]['h']
        if not floor or bars[i]['l'] <= floor:
            continue
        if 100.0 * (bars[i]['l'] / floor - 1.0) < GAP_MIN_PCT:
            continue
        after = bars[i + 1:i + 1 + GAP_UNFILLED_DAYS]
        if len(after) < GAP_UNFILLED_DAYS or min(b['l'] for b in after) <= floor:
            continue
        if t['l'] <= floor:                       # 今天已经回补了就不算
            continue
        gap_i = i
        break                                     # 最近的合格缺口优先
    c2 = gap_i is not None

    row: Dict[str, Any] = {
        'trade_date': t['date'], 'security_code': code,
        'open': t['raw_o'], 'high': t['raw_h'], 'low': t['raw_l'], 'close': t['raw_c'],
        'pre_close': t['raw_pre'], 'pct_change': t['pct'], 'volume_shares': t['vol'],
        'adjustment_factor': t['af'], 'close_hfq': t['c'],
        'c1_ok': c1,
        'limit_up_count20': len(sealed_idx) if enough_history else None,
        'last_limit_up_date': bars[sealed_idx[-1]]['date'] if sealed_idx else None,
        'limit_up_ago': (n - 1 - sealed_idx[-1]) if sealed_idx else None,
        'c2_ok': c2, 'gap_date': bars[gap_i]['date'] if c2 else None,
        'gap_ago': (n - 1 - gap_i) if c2 else None,
        'gap_pct': round(100.0 * (bars[gap_i]['l'] / bars[gap_i - 1]['h'] - 1.0), 3) if c2 else None,
        'gap_floor_hfq': bars[gap_i - 1]['h'] if c2 else None,
        'gap_unfilled': None, 'gap_vol_shares': bars[gap_i]['vol'] if c2 else None,
        'c3_ok': False, 'pullback_sessions': None, 'pullback_down_cnt': None,
        'pullback_max_vol_ratio': None, 'pullback_min_low_hfq': None,
        'confirm_yang': t['c'] > t['o'] and t['c'] > t['pre'],
        'vol_vs_prev': round(t['vol'] / bars[-2]['vol'], 3) if bars[-2]['vol'] else None,
        'bars_used': n, 'spec_version': SPEC_VERSION,
    }
    if not (c1 and c2):
        return None                                # 门票与缺口是母体，缺一样就不进表
    gap_vol = row['gap_vol_shares']
    win = bars[-1 - PULLBACK_WINDOW:-1]            # 今天往前 3 场 = 回调段
    row['pullback_sessions'] = row['gap_ago'] - 1
    row['pullback_down_cnt'] = sum(1 for b in win if (b['pct'] or 0) < 0)
    row['pullback_max_vol_ratio'] = (round(max(b['vol'] for b in win) / gap_vol, 3)
                                     if win and gap_vol else None)
    row['pullback_min_low_hfq'] = min(b['l'] for b in win) if win else None
    unfilled = 0
    for b in bars[gap_i + 1:]:
        if b['l'] <= row['gap_floor_hfq']:
            break
        unfilled += 1
    row['gap_unfilled'] = unfilled
    row['c3_ok'] = bool(win) and row['pullback_down_cnt'] >= 1 and (
        row['pullback_max_vol_ratio'] is not None and row['pullback_max_vol_ratio'] <= 1.0)
    row['entry_ok'] = bool(row['c3_ok'] and row['confirm_yang'])
    row['watch_ok'] = not row['entry_ok']
    row['stop_ref_hfq'] = row['gap_floor_hfq']
    row['stop_dist_pct'] = round(100.0 * (row['stop_ref_hfq'] / t['c'] - 1.0), 3) if t['c'] else None
    miss = [k for k in ('limit_up_count20', 'gap_pct', 'pullback_max_vol_ratio',
                        'pullback_min_low_hfq', 'vol_vs_prev', 'gap_unfilled')
            if row[k] is None]
    if not enough_history:
        miss.append('lookback_window')
    row['missing'] = ','.join(miss) or None
    return row


def _series(day: dt.date) -> List[tuple]:
    """一次查询取回全市场每票最近 SERIES_BARS 根日线（含今天要算的那一根）。"""
    start = day - dt.timedelta(days=SERIES_BARS * 3)     # 日历留 3 倍余量，覆盖长假与停牌
    rows = engine.query("""
        with s as (select security_code, trade_date, open, high, low, close, pre_close,
                          pct_change, limit_up, volume_shares, adjustment_factor,
                          row_number() over (partition by security_code order by trade_date desc) back
                     from market_data.stock_daily
                    where trade_date between %s and %s
                      and close > 0 and pre_close > 0 and adjustment_factor > 0)
        select security_code,
               jsonb_agg(jsonb_build_array(trade_date::text, open, high, low, close, pre_close,
                                           pct_change, limit_up, volume_shares, adjustment_factor)
                         order by trade_date) rows
          from s where back <= %s
         group by 1""", (start, day, SERIES_BARS), role='etl') or []
    out = []
    for r in rows:
        try:
            bars = json.loads(r['rows']) if isinstance(r['rows'], str) else r['rows']
        except (TypeError, ValueError):
            continue
        if bars and _d(bars[-1][0]) == day:             # 今天没行情就不算这一票
            out.append((r['security_code'], bars))
    return out


def compute_day(day: dt.date) -> List[Dict[str, Any]]:
    out = []
    for code, bars in _series(day):
        try:
            row = judge(code, bars)
        except Exception:                              # noqa: BLE001 —— 一只票的脏数据不带走整场
            continue
        if row:
            out.append(row)
    return out


UPSERT = ("insert into analytics.system_entry ({cols}) values ({ph}) "
          "on conflict (security_code, trade_date, spec_version) do update set {upd}, "
          "calculated_at = now()")
_PK = {'security_code', 'trade_date', 'spec_version'}


def write(day: dt.date, rows: List[Dict[str, Any]]) -> int:
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


def done_days(since: dt.date, end: dt.date) -> set:
    rows = engine.query("""select distinct trade_date d from analytics.system_entry
                            where trade_date between %s and %s and spec_version = %s""",
                        (since, end, SPEC_VERSION), role='reader')
    return {r['d'] for r in rows}


def refresh(day: dt.date) -> Dict[str, Any]:
    rows = compute_day(day)
    written = write(day, rows)
    return {'day': day, 'watch': len(rows), 'written': written,
            'entries': sum(1 for r in rows if r['entry_ok'])}


# ------------------------------------------------------------------ 历史分布（落库，不现算）
#
# 为什么这张聚合表存在：三档读数要扫全市场票·日（250 场 ≈ 135 万行）并取每一根的未来 20 根。
# 2026-10-14 实测 `250 场 261 秒 / 60 场 40 秒`（261 秒那一遍是跟另一个查询抢 CPU 时测的），
# 而只读角色带 60 秒 `statement_timeout` —— 页面现算的第一后果不是慢，是**查询被超时咬死后
# 界面把"没算出来"说成"这一场没有数据"**：第一版就是把分布放在读侧现算，浏览器实测 62 秒后
# 拿到的是 available:false ＋ 一句错误的理由。判据落库、页面只读，是本仓一贯的做法，这里照办。
STATS_TABLE = 'analytics.system_entry_stats'
WINDOW_DAYS = 250

# key 稳定（前端按它着色、测试按它断言）；给人看的那一句也只住这一处
BUCKETS = (('baseline', '基线（同期全部票·日）'),
           ('c12', '①② 门票 + 缺口未回补（观察池母体）'),
           ('entry', '①②③ 全过 + 今日阳线'))

_STATS_COLUMNS = ('bucket_key', 'bucket', 'window_days', 'window_from', 'window_to', 'spec_version',
                  'n', 'win_next', 'avg_next', 'avg_2d', 'avg_3d', 'avg_4d', 'avg_5d',
                  'n_2d', 'n_3d', 'n_4d', 'n_5d',
                  'up10_med', 'up10_p90', 'p20_up20', 'd20_med',
                  'hit_take', 'hit_stop', 'rule_expect', 'missing', 'bars_scanned')
_STATS_PK = {'bucket_key', 'window_to', 'window_days', 'spec_version'}

#: 后 N 日均值那四列的 N（用户 2026-10-22 点名：除次日均值外还要 2/3/4/5 日）。
#: 语义是**从当日收盘起连续 N 个场次的涨跌幅复利累乘**再取平均，不是"第 N 天那一天"的单日涨幅 ——
#: 这样「次日均值 → 后 2 日 → … → 后 20 场复利中位」才是同一把尺上的一串，
#: 中间断开（一半单日、一半累计）就会被读成"持有越久越赚"。
AVG_HORIZONS = (2, 3, 4, 5)

# 次日/后 N 日一律 lead（不是 lag）。路径那三列**必须用按 bar_no 的自连接，不能用窗口帧内自比**：
# 写成 `min(case when high*af >= close*af*1.20 then trade_date end) over (rows between 1 following
# and 20 following)` 时，`close` 是**帧内那一根**的收盘，不是锚点（信号日）的收盘 —— 一根 K 线自己
# 的最高价 ≥ 自己收盘的 1.2 倍几乎不可能，实测 hit_take 只有 0.1~0.4%（基线），量的其实是
# "某天盘中冲高又回落"，跟"20 场内先碰到 +20%"是两个问题。2026-10-14 现算现发现，改掉了。
#
# 后 N 日均值（`avg_2d`…`avg_5d`，2026-10-22 用户点名要）：把未来 N 根的涨跌幅按复利累乘
# （`exp(Σ ln(1+pc/100)) − 1`），且**要求这 N 根一根不缺**（`ck = k` 才计入），所以每一档的分母
# 单独数出来（`n_2d`…`n_5d`）—— 越长的窗分母越小（锚点票·日里最后几根没凑齐的会被剔掉），
# 不数分母就会把"后 5 日均值"与"次日均值"当成同一批样本比大小。
# `bar_no` 是这根票自己的日线序号（缺一天停牌就是一根不在了），所以"N 日"是 **N 个场次**、不是日历日。
STATS_SQL = """
with n as (select security_code, trade_date,
                  row_number() over (partition by security_code order by trade_date) bar_no,
                  close * adjustment_factor as ac,
                  high * adjustment_factor as hi,
                  low * adjustment_factor as lo,
                  pct_change as pc
             from market_data.stock_daily
            where close > 0 and pre_close > 0 and adjustment_factor > 0 and pct_change is not null
              and trade_date >= %s),
 a as (select * from n where trade_date between %s and %s),
 nx as (select a.security_code as c, a.trade_date as d,
                max(a.ac) as ac,
                max(b.hi) filter (where b.bar_no between a.bar_no + 1 and a.bar_no + 10) h10,
                max(b.hi) filter (where b.bar_no between a.bar_no + 1 and a.bar_no + 20) h20,
                sum(ln(1 + b.pc/100.0)) filter (where b.bar_no between a.bar_no + 1 and a.bar_no + 20) s20,
                max(b.pc) filter (where b.bar_no = a.bar_no + 1) n1,
                sum(ln(1 + b.pc/100.0)) filter (where b.bar_no between a.bar_no + 1 and a.bar_no + 2) s2,
                count(*) filter (where b.bar_no between a.bar_no + 1 and a.bar_no + 2) nb2,
                sum(ln(1 + b.pc/100.0)) filter (where b.bar_no between a.bar_no + 1 and a.bar_no + 3) s3,
                count(*) filter (where b.bar_no between a.bar_no + 1 and a.bar_no + 3) nb3,
                sum(ln(1 + b.pc/100.0)) filter (where b.bar_no between a.bar_no + 1 and a.bar_no + 4) s4,
                count(*) filter (where b.bar_no between a.bar_no + 1 and a.bar_no + 4) nb4,
                sum(ln(1 + b.pc/100.0)) filter (where b.bar_no between a.bar_no + 1 and a.bar_no + 5) s5,
                count(*) filter (where b.bar_no between a.bar_no + 1 and a.bar_no + 5) nb5,
                min(b.trade_date) filter (where b.hi >= a.ac * 1.20
                                          and b.bar_no between a.bar_no + 1 and a.bar_no + 20) tk,
                min(b.trade_date) filter (where b.lo <= a.ac * 0.93
                                          and b.bar_no between a.bar_no + 1 and a.bar_no + 20) st
           from a join n b
             on b.security_code = a.security_code
            and b.bar_no between a.bar_no + 1 and a.bar_no + 20
          group by 1, 2),
 e as (select nx.*, (s.security_code is not null) c12,
              coalesce(s.entry_ok, false) entry_ok,
              coalesce(s.c3_ok, false) c3, coalesce(s.confirm_yang, false) yang,
              exp(nx.s20) - 1 s20ret,
              case when nx.nb2 = 2 then 100.0 * (exp(nx.s2) - 1) end r2,
              case when nx.nb3 = 3 then 100.0 * (exp(nx.s3) - 1) end r3,
              case when nx.nb4 = 4 then 100.0 * (exp(nx.s4) - 1) end r4,
              case when nx.nb5 = 5 then 100.0 * (exp(nx.s5) - 1) end r5
         from nx left join analytics.system_entry s
           on s.security_code = nx.c and s.trade_date = nx.d and s.spec_version = %s)
select k bucket_key, count(*)::int n,
       round(100.0*count(*) filter (where n1 > 0)
             /nullif(count(*) filter (where n1 is not null),0),1) win_next,
       round(avg(n1)::numeric,2) avg_next,
       round(avg(r2)::numeric,2) avg_2d, count(r2)::int n_2d,
       round(avg(r3)::numeric,2) avg_3d, count(r3)::int n_3d,
       round(avg(r4)::numeric,2) avg_4d, count(r4)::int n_4d,
       round(avg(r5)::numeric,2) avg_5d, count(r5)::int n_5d,
       round((percentile_cont(0.5) within group (order by 100.0*(h10/ac-1)))::numeric,1) up10_med,
       round((percentile_cont(0.9) within group (order by 100.0*(h10/ac-1)))::numeric,1) up10_p90,
       round(100.0*count(*) filter (where 100.0*(h20/ac-1) >= 20)
             /nullif(count(*) filter (where h20 is not null),0),1) p20_up20,
       round((100.0*(exp(percentile_cont(0.5) within group (order by s20))) - 100)::numeric,2) d20_med,
       round(100.0*count(*) filter (where tk is not null and (st is null or tk <= st))
             /nullif(count(*) filter (where h20 is not null),0),1) hit_take,
       round(100.0*count(*) filter (where st is not null and (tk is null or st < tk))
             /nullif(count(*) filter (where h20 is not null),0),1) hit_stop,
       round(avg(case when st is not null and (tk is null or st <= tk) then -7.0
                      when tk is not null then 20.0
                      else 100.0 * s20ret end)::numeric,2) rule_expect
  from e cross join lateral (values ('baseline', true),
                                   ('c12', c12),
                                   ('entry', c12 and c3 and yang)) as t(k, hit)
 where hit
 group by 1
 order by case k when 'baseline' then 0 when 'c12' then 1 else 2 end
"""

_STATS_MEASURES = ('win_next', 'avg_next', 'avg_2d', 'avg_3d', 'avg_4d', 'avg_5d',
                   'up10_med', 'up10_p90', 'p20_up20', 'd20_med',
                   'hit_take', 'hit_stop', 'rule_expect')

#: 后 N 日均值各自的**分母**（这一档里凑齐 N 根的锚点数）。它们不是读数，是"这一列的数
#: 是从多少只票·日里平均出来的" —— 越长的窗这个数越小，不交出来就会被读成同一批样本。
_STATS_COUNTS = ('n_2d', 'n_3d', 'n_4d', 'n_5d')

#: 历史分布那张表的**列头文字与逐列说明只住这一处**（2026-10-22 用户点名：
#: 「后10最大上涨中位／后10 p90／后20冲出两成／后20复利中位／先碰+20%／先碰−7%／这条规则的期望
#: 分别是什么意思，做个说明，说明默认收缩」）。
#: 读侧随 `history()` 一起交出，前端按它渲染列头、格子与折叠说明——**页面不再手写一遍中文列名**
#: （写了就会漂：以前那张表的中文表头是前端手抄的，说明却在 `calibers` 里，两处各说各话）。
#:
#: 三条约定：
#:  · `signed=True` 的列格子带正号（收益类），否则是"占比/分位数"类的裸百分数；
#:  · `versus_base=True` 的列与基线那行比色（高了绿、低了红），其余列不着色 —— 只在这几列上
#:    "比基线高"才是结论，别的列高低都不说明好坏；
#:  · `denom_key` 指出这一列自己的分母（后 N 根凑不齐的锚点会被剔掉，越长的窗分母越小）。
#: 说明里刻意**不写具体数字**（那些数每天随窗口滚，写了就会过期）；数就在同一张表的格子里。
STATS_COLUMNS_SPEC = (
    {'key': 'bucket', 'label': '档', 'note': '三档是同一个滚动窗口、同一句查询里并排算出来的：'
             '基线（同期全部票·日）是尺子本身，①② 是门票加缺口未回补的观察池母体，'
             '①②③ 全过才是这套体系认的下车机会。跨档比数只在同窗内成立，别拿它跟别的窗口比。',
     'signed': False, 'versus_base': False},
    {'key': 'n', 'label': 'n', 'note': '这一档在窗口里的票·日数（一只票一场算一行），不是只数股票。'
             '它也是下面那些占比与分位数的分母。', 'signed': False, 'versus_base': False},
    {'key': 'win_next', 'label': '次日上涨', 'note': '次日收盘上涨的比例 %，分母＝次日有数的那些行。'
             '这是胜率，不是赚多少；赚多少看旁边那一列均值。',
     'signed': False, 'versus_base': True},
    {'key': 'avg_next', 'label': '次日均值', 'note': '从当日收盘起第 1 个场次的涨跌幅平均值 %。'
             '它是"后 N 日均值"那一串里 N=1 的那一个，但那一列算的是单日，后面四列算的是累计（见下）。',
     'signed': True, 'versus_base': False},
    {'key': 'avg_2d', 'label': '后2日均值', 'note': '从当日收盘起连续 2 个场次的涨跌幅**按复利累乘**后的平均值 %'
             '（不是第 2 天那一天的涨幅）。要这一档凑齐 2 根，所以分母比次日均值小，悬停里给出这一档自己的 N。'
             '"日"在这里是场次、不是日历日：停牌那天没有 K 线，就不占一根。',
     'signed': True, 'versus_base': False, 'denom_key': 'n_2d'},
    {'key': 'avg_3d', 'label': '后3日均值', 'note': '同上，累计 3 个场次。分母再小一圈（最后 3 根凑不齐的锚点被剔掉）。',
     'signed': True, 'versus_base': False, 'denom_key': 'n_3d'},
    {'key': 'avg_4d', 'label': '后4日均值', 'note': '同上，累计 4 个场次。',
     'signed': True, 'versus_base': False, 'denom_key': 'n_4d'},
    {'key': 'avg_5d', 'label': '后5日均值', 'note': '同上，累计 5 个场次。这四个窗口与次日均值连成一串，'
             '说的是"拿 N 天平均几个点"；它们互相之间比较时还要看各自的 N（样本越短的那列越容易被少数几天带动）。',
     'signed': True, 'versus_base': False, 'denom_key': 'n_5d'},
    {'key': 'up10_med', 'label': '后10最大上涨中位', 'note': '后 10 个场次内**盘中最高价**相对当日收盘的涨幅，'
             '取中位数 %。它量的是"这 10 天里最肥的那一下有多肥"，不等于你拿得到 —— '
             '要卖在那根最高价上才算数。', 'signed': False, 'versus_base': False},
    {'key': 'up10_p90', 'label': '后10 p90', 'note': '同一个量的 90 分位 %，即最走运的那一成能到多少（右尾）。'
             '它与中位差得越远，说明这一档的收益越集中在少数样本上；只看 p90 会把运气读成本事。',
     'signed': False, 'versus_base': False},
    {'key': 'p20_up20', 'label': '后20冲出两成', 'note': '后 20 个场次内盘中最高冲出 +20% 的比例 %'
             '（按触发价判定，不代表你真能以 +20% 成交）。这是"有浪"的量，不是"会涨"的量。',
     'signed': False, 'versus_base': True, 'bold': True},
    {'key': 'd20_med', 'label': '后20复利中位', 'note': '后 20 个场次涨跌幅按复利累乘后的中位数 %。'
             '用中位数是为了不被少数暴涨暴跌拉动；它旁边那两列（冲出两成的比例、下面那条规则的期望）'
             '才回答"这个分布长什么样"。', 'signed': True, 'versus_base': False},
    {'key': 'hit_take', 'label': '先碰+20%', 'note': '从当日收盘起 20 个场次内，盘中**先**碰到 +20% 的比例 %。'
             '与旁边那列在同一批样本上互斥判定：两个价位都碰到时，算先碰到的那一个。',
     'signed': False, 'versus_base': False},
    {'key': 'hit_stop', 'label': '先碰−7%', 'note': '同期内**先**碰到 −7% 的比例 %。'
             '它是"带好止损，冲"这句话的量：这一列比先碰 +20% 大得多时，说明冲的代价先落在止损上。',
     'signed': False, 'versus_base': False},
    {'key': 'rule_expect', 'label': '这条规则的期望', 'note': '按一条具体规则跑的期望收益 %：先到 −7% 记 −7、'
             '先到 +20% 记 +20，20 个场次两个都没碰到就按第 20 场收盘的复利收益算。'
             '每个样本都进分母（没触发也算）。它是把右尾扣掉止损之后剩下的净读数，'
             '与上面"冲出两成"不矛盾才叫结论。', 'signed': True, 'versus_base': True},
)


def stats_columns() -> List[Dict[str, Any]]:
    """列头与说明（读侧/前端唯一出处）。原样交出，不在别处再抄一份中文列名。"""
    return [dict(c) for c in STATS_COLUMNS_SPEC]

STATS_UPSERT = (f"insert into {STATS_TABLE} ({{cols}}) values ({{ph}}) "
                f"on conflict (bucket_key, window_to, window_days, spec_version) "
                f"do update set {{upd}}, calculated_at = now()")


def compute_stats(end: dt.date, window_days: int = WINDOW_DAYS) -> List[Dict[str, Any]]:
    """三档在同一个滚动窗口里的前瞻读数。窗口右端 = `end`，长度 = `window_days` 个场次。"""
    wdays = trade_days(end, window_days)
    if not wdays:
        return []
    from_date, to_date = wdays[0], wdays[-1]
    rows = engine.query(STATS_SQL, (from_date, from_date, to_date, SPEC_VERSION), role='etl') or []
    labels = dict(BUCKETS)
    base_n = next((int(r['n']) for r in rows if r['bucket_key'] == 'baseline'), None)
    out = []
    for r in rows:
        missing = [c for c in _STATS_MEASURES if r.get(c) is None]
        out.append({'bucket_key': r['bucket_key'], 'bucket': labels.get(r['bucket_key'],
                                                                        r['bucket_key']),
                    'window_days': window_days, 'window_from': from_date, 'window_to': to_date,
                    'spec_version': SPEC_VERSION, 'n': int(r['n']),
                    **{c: _num(r.get(c)) for c in _STATS_MEASURES},
                    **{c: _int(r.get(c)) for c in _STATS_COUNTS},
                    'missing': ', '.join(missing) if missing else None,
                    'bars_scanned': base_n})
    return out


def check_stats(rows: List[Dict[str, Any]]) -> List[str]:
    """算完先自己验一遍不变量，别把坏数落进库再靠人眼发现。

    后 N 日均值那四列共用一条铁律：**窗口越长、分母越小**（凑不齐 N 根的锚点被剔掉），
    而且分母不可能大于这一档自己的样本数。这条一旦反过来，说明上面那句 `nbN = N` 的判据
    或 `bar_no` 序号坏了 —— 那四列的数就会是"不同批样本互相比"，屏幕上看不出来。
    """
    bad: List[str] = []
    for r in rows:
        tag = f'{r["bucket_key"]}'
        n = r.get('n')
        seq = [r.get(f'n_{k}d') for k in AVG_HORIZONS]
        if any(v is None for v in seq):
            bad.append(f'{tag}：后 N 日均值的分母缺数 {seq}（这一场未来根不够？）')
            continue
        for a, b in zip(seq, seq[1:]):
            if a < b:
                bad.append(f'{tag}：分母随窗口变长而变大（n_2d={seq[0]} … n_5d={seq[-1]}），不成立')
        if n is not None and seq[-1] > n:
            bad.append(f'{tag}：n_5d={seq[-1]} 大于这一档的样本数 n={n}')
        if r.get('avg_next') is None and r.get('win_next') is not None:
            bad.append(f'{tag}：次日均值空着但次日胜率有数，两列该同一批样本')
    return bad



def write_stats(rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    cols = ', '.join(_STATS_COLUMNS)
    ph = ', '.join(['%s'] * len(_STATS_COLUMNS))
    upd = ', '.join(f'{c} = excluded.{c}' for c in _STATS_COLUMNS if c not in _STATS_PK)
    payload = [tuple(r.get(c) for c in _STATS_COLUMNS) for r in rows]
    with engine.cursor(role='etl') as cur:
        cur.executemany(STATS_UPSERT.format(cols=cols, ph=ph, upd=upd), payload)
    return len(rows)


def refresh_stats(end: dt.date, window_days: int = WINDOW_DAYS) -> Dict[str, Any]:
    """算一遍 → **自己验一遍** → 落库。`bad` 非空就是那几列的数不可信，调用方必须念出来。"""
    rows = compute_stats(end, window_days)
    bad = check_stats(rows)
    return {'window': (rows[0]['window_from'], rows[0]['window_to']) if rows else None,
            'written': write_stats(rows), 'rows': rows, 'bad': bad}


def run(day: Optional[dt.date] = None, recent: int = 1, since: Optional[dt.date] = None,
        dry_run: bool = False, skip_done: bool = False,
        max_minutes: Optional[float] = None,
        skip_stats: bool = False, stats_only: bool = False) -> int:
    end = day or latest_day()
    if not end:
        print('库里没有日线，没什么可算')
        return 1
    if stats_only:
        res = refresh_stats(end)
        print(f'历史分布 {res["written"]} 档（窗口 {res["window"]}）')
        for r in res['rows']:
            print(f'  {r["bucket"]}: n={r["n"]} 次日上涨 {r["win_next"]}% '
                  f'次日均值 {r["avg_next"]}% 后2/3/4/5日均值 '
                  f'{r["avg_2d"]}/{r["avg_3d"]}/{r["avg_4d"]}/{r["avg_5d"]}%（分母 '
                  f'{r["n_2d"]}/{r["n_3d"]}/{r["n_4d"]}/{r["n_5d"]}） '
                  f'后20场冲出两成 {r["p20_up20"]}% 规则期望 {r["rule_expect"]}%')
        for b in res['bad']:
            print(f'⚠️ 自检不过：{b}')
        return 0 if res['written'] and not res['bad'] else (3 if res['bad'] else 2)
    if day:
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
    print(f'交易体系榜 spec_version={SPEC_VERSION}，{len(days)} 场：{days[0]} ~ {days[-1]}')
    start_t = time.monotonic()
    wrote_any = failed = 0
    for i, d in enumerate(days, 1):
        if max_minutes and (time.monotonic() - start_t) / 60.0 >= max_minutes:
            print(f'⏸ 自限预算 {max_minutes:.0f} 分钟用完：{len(days) - i + 1} 场没轮到，'
                  f'重跑同一条命令（带 --skip-done）继续')
            return 4
        try:
            if dry_run:
                rows = compute_day(d)
                ent = [r for r in rows if r['entry_ok']]
                print(f'  {d} 观察池 {len(rows)} 只、全过 {len(ent)} 只（dry-run，前 5 条全过）')
                for r in ent[:5]:
                    print(f"    {r['security_code']} 缺口 {r['gap_date']}（{r['gap_ago']} 场前 "
                          f"{r['gap_pct']}%）未回补 {r['gap_unfilled']} 场 回调量比 "
                          f"{r['pullback_max_vol_ratio']} 阴线 {r['pullback_down_cnt']} "
                          f"止损距 {r['stop_dist_pct']}%")
                wrote_any += 1
                continue
            res = refresh(d)
            wrote_any += 1 if res['written'] else 0
            print(f"  {res['day']} 观察池 {res['watch']} 条，写 {res['written']} 条"
                  f"（三条全过 {res['entries']} 条）")
        except Exception as e:                        # noqa: BLE001 —— 单场失败不带走整批
            failed += 1
            print(f'  {d} 失败：{type(e).__name__}: {e}')
    if failed:
        print(f'⚠️ {failed} 场失败 —— 已写下的留着')
        return 3
    if not dry_run and not skip_stats:
        # 分布算在最后一场之后：窗口右端跟着榜单走，页面读的是这张表（现算要 96 秒）
        try:
            res = refresh_stats(days[-1])
            print(f"  历史分布 {res['written']} 档（窗口 {res['window'][0]} ~ {res['window'][1]}）"
                  if res['rows'] else '  历史分布：这一窗口的行没算出来')
            for b in res['bad']:
                # 那四列的分母一旦不单调，屏幕上就是"不同批样本互相比"——看不出来，只能在这儿出声
                print(f'⚠️ 历史分布自检不过：{b}')
            if res['bad']:
                failed += 1
                return 3
        except Exception as e:                        # noqa: BLE001 —— 分布失败不带走整批榜单
            print(f'⚠️ 历史分布失败：{type(e).__name__}: {e}（榜单已写，页面那一块会如实说"还没算"）')
            failed += 1
            return 3
    return 0 if wrote_any else 2


def main() -> int:
    ap = argparse.ArgumentParser(description='交易体系榜（组合判据）：analytics.system_entry')
    ap.add_argument('--date', help='只算这一天（YYYY-MM-DD）')
    ap.add_argument('--recent', type=int, default=1, help='往前数 N 个交易日')
    ap.add_argument('--since', help='从这一天起把所有交易日都算一遍')
    ap.add_argument('--skip-done', action='store_true', help='跳过表里已有该 spec_version 的场')
    ap.add_argument('--max-minutes', type=float, help='自限预算，到点就停并交还剩几场')
    ap.add_argument('--dry-run', action='store_true', help='只打前几条不写库')
    ap.add_argument('--skip-stats', action='store_true',
                    help='这一遍不算历史分布（长回算用：分布只跟最新一场有意义）')
    ap.add_argument('--stats-only', action='store_true',
                    help='只重算历史分布那一档聚合，不动榜单')
    a = ap.parse_args()
    return run(day=dt.date.fromisoformat(a.date) if a.date else None,
               recent=a.recent,
               since=dt.date.fromisoformat(a.since) if a.since else None,
               dry_run=a.dry_run, skip_done=a.skip_done, max_minutes=a.max_minutes,
               skip_stats=a.skip_stats, stats_only=a.stats_only)


if __name__ == '__main__':
    raise SystemExit(main())
