# -*- coding: utf-8 -*-
"""`analytics.first_board_shape`（fb-2）· 首板形状预示：每个连板段首日的启动前量能形状 → 走到几板 / 次日进场收益。

**为什么要有这一步**（用户 2026-10-16 点名）：`analytics.startup_volume_profile`（sv-2）的母体是 `len >= 4`，
那是**回头看**才成立的条件，所以它交出的"启动后收益"带未来函数（同一批样本：复盘口径 5 场 +32.22%、
能买到口径 −6.31%、胜率 37%，见《倍量形态.md》§8）。要回答「启动前什么形状**预示**能走到 4 板」，
母体必须换成**当天就能构造**的事件：那一天收在涨停价上、且它不是已有连板段的第二天。
于是段长 `seg_len` 在这里是**结果**，不是筛子。

四条写在旁边的实测事实：
  · 母体量级：五个 12 个月片共 **60,173 条首板事件**（2021-11-01→2026-10-16），一把跑 5 年会撞
    reader 角色 60 秒 `statement_timeout`（同一条 SQL 在 5 年窗口上实测 61 秒）⇒ 本模块**按 12 个月分片**，
    每片走 etl 角色（30 分钟上限）。
  · 段还没走完的行必须点名：末板日之后还没有出现过非涨停日 ⇒ `seg_complete=false`，此时 `seg_len` 是**下界**。
    读侧算"走到 N 板率"要么剔掉这些行，要么单列一档，**不许把在走的段当已走完**（那会把 4 板率算低）。
  · 次日一字板（`high==low`）＝开盘买不进（五年实测占 3.1%）⇒ 存 `entry_locked`，聚合时"剔掉买不进"另交一列。
  · 北交所（8x/4x/920）本机**没有对应分类指数** ⇒ `bench_code`/`ex*` 一律 NULL，只报绝对收益，
    不拿上证指数冒充（同 `app/compute/abnormal_move.py` 那一条纪律）。
  · 超额的基准起点是**进场当日指数的开盘**（fb-2），与"次日开盘进场"同一个时刻。fb-1 拿的是当日**收盘** ⇒
    `ex1` 的分子分母同日、基准恒为 0，那一列写着"超额"其实等于绝对涨幅；`verify()` 现在逐批数"ex1 与 r1 相等"
    的行数，非 0 就把这一批判成不通过（这类缺陷不会报错，只会给出一句听着合理的假读数）。

用法：
    python -m app.compute.first_board_shape --date 2026-10-16 --recent-sessions 40    # 每日链：只重算最近 40 场
    python -m app.compute.first_board_shape --start 2021-11-01 --end 2026-10-16       # 回算（自动分片）
    python -m app.compute.first_board_shape --dry-run --months 3                       # 只解析不写库
    python -m app.compute.first_board_shape --verify
退出码：0 写了 / 1 一条都没写成 / 2 按设计不写（区间里没有首板事件）/ 3 写了但体检不过（清单在输出里）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.db import engine                                        # noqa: E402

TABLE = 'analytics.first_board_shape'
SPEC_VERSION = 'fb-2'        # fb-1→fb-2：超额基准的起点从"进场当日指数收盘"改成"进场当日指数开盘"

WINDOW = 30                 # 启动前根数（与 sv-2 同一把尺子，好对照）
FORWARD = 12                # 首板日之后取几场（够算 r10 与 5 日最低）
DBL, NEAR, CALM = 2.0, 1.6, 0.6
DISCLOSE_LAG_DAYS = 60      # 财务"已披露"的保守滞后补偿
CHUNK_DAYS = 366            # 分片宽度：一年一片，避开 reader 60 秒 / 单片实测 33~110 秒

#: 号段 → 对应分类指数（与 `app/compute/abnormal_move.py:86` 同一张表；北交所没有 ⇒ None）
BENCH_PREFIX = (('68', '000680.SH'), ('6', '000002.SH'), ('30', '399102.SZ'), ('00', '399107.SZ'))


def bench_code(code: str) -> Optional[str]:
    for pre, idx in BENCH_PREFIX:
        if str(code).startswith(pre):
            return idx
    return None


def shape_features(ratios: Sequence[Optional[float]]) -> Dict[str, Any]:
    """启动前那串倍数 → 形状读数。缺的格子（None）**不参与、不补 0**，只数实际根数。"""
    v = [float(x) for x in ratios if x is not None]
    n = len(v)
    if not n:
        return {'bars': 0, 'dbl_days': 0, 'near_dbl_days': 0, 'calm_days': 0, 'last10_quiet': 0,
                'r_avg': None, 'r_med': None, 'r_p80': None, 'r_p90': None, 'r_max': None,
                'r_std': None, 'last5_avg': None, 'base25_avg': None, 'pre5': None}
    q = _quantiles(v)
    last5 = v[-5:]
    base25 = v[:-5]
    pre5 = (sum(last5) / len(last5)) / (sum(base25) / len(base25)) if base25 and sum(base25) > 0 else None
    return {
        'bars': n,
        'dbl_days': sum(1 for x in v if x >= DBL),
        'near_dbl_days': sum(1 for x in v if NEAR <= x < DBL),
        'calm_days': sum(1 for x in v if x < CALM),
        'last10_quiet': sum(1 for x in v[-10:] if x < CALM),
        'r_avg': round(sum(v) / n, 4),
        'r_med': q[0.5], 'r_p80': q[0.8], 'r_p90': q[0.9],
        'r_max': round(max(v), 4),
        'r_std': _std(v),
        'last5_avg': round(sum(last5) / len(last5), 4),
        'base25_avg': round(sum(base25) / len(base25), 4) if base25 else None,
        'pre5': round(pre5, 4) if pre5 is not None else None,
    }


def _quantiles(vals: List[float]) -> Dict[float, Optional[float]]:
    s = sorted(vals)
    out = {}
    for p in (0.5, 0.8, 0.9):
        out[p] = round(_interp(s, p), 4)
    return out


def _interp(s: List[float], p: float) -> float:
    """与 PG 的 `percentile_cont` 同一条插值规则（线性、含两端），两边数才能对得上。"""
    if len(s) == 1:
        return s[0]
    pos = p * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _std(vals: List[float]) -> Optional[float]:
    """样本标准差（分母 n−1），与 SQL 的 `stddev_samp` 同一条式子。"""
    n = len(vals)
    if n < 2:
        return None
    m = sum(vals) / n
    return round((sum((x - m) ** 2 for x in vals) / (n - 1)) ** 0.5, 4)


def forward_metrics(entry_open: Optional[float], path: Sequence[tuple]) -> Dict[str, Any]:
    """`path` ＝ [(日期, 开, 收, 高, 低), …] 按场次升序（首板日之后第一场起）。全后复权。

    进场价＝第一场开盘；r1/r3/r5/r10 是第 1/3/5/10 场**收盘**相对进场价。
    凑不满的场次交 None（**不写 0**：0 会被下游读成"这 5 天平盘"，而真意是没拿到）。
    """
    out: Dict[str, Any] = {'entry_date': None, 'entry_open': None, 'entry_locked': False,
                           'r1': None, 'r3': None, 'r5': None, 'r10': None,
                           'maxup5': None, 'low5': None}
    if not entry_open or not path:
        return out
    d, o, c = path[0][0], path[0][1], path[0][2]
    h1, l1 = path[0][3], path[0][4]
    out['entry_date'] = d
    out['entry_open'] = round(float(entry_open), 6)
    out['entry_locked'] = bool(h1 is not None and l1 is not None
                               and abs(float(h1) - float(l1)) <= 1e-6 * max(1.0, abs(float(h1))))
    for k, key in ((1, 'r1'), (3, 'r3'), (5, 'r5'), (10, 'r10')):
        if len(path) >= k:
            out[key] = round((float(path[k - 1][2]) / float(entry_open) - 1.0) * 100.0, 4)
    hs = [float(p[3]) for p in path[:5] if p[3] is not None]
    ls = [float(p[4]) for p in path[:5] if p[4] is not None]
    if hs:
        out['maxup5'] = round((max(hs) / float(entry_open) - 1.0) * 100.0, 4)
    if ls:
        out['low5'] = round((min(ls) / float(entry_open) - 1.0) * 100.0, 4)
    return out


def excess(abs_pct: Optional[float], base_pct: Optional[float]) -> Optional[float]:
    return round(abs_pct - base_pct, 4) if (abs_pct is not None and base_pct is not None) else None


# --------------------------------------------------------------------------- 取数

EVENTS_SQL = """
with bounds as (
  select %(a)s::date - interval '150 days' as scan_from, %(b)s::date as d_to
),
series as (                       -- 未过滤序列：岛号用它减（见模块文档 ①）
  select d.security_code, d.trade_date, d.close, d.pre_close, d.limit_up,
         d.pct_change, d.turnover_rate_percent, d.amount_yuan, d.high, d.low,
         row_number() over (partition by d.security_code order by d.trade_date) as bar_no,
         lag(d.close) over (partition by d.security_code order by d.trade_date) as prev_close
    from market_data.stock_daily d, bounds b
   where d.trade_date between b.scan_from and b.d_to
),
zt as (
  select security_code, trade_date, pct_change, turnover_rate_percent, amount_yuan, high, low, bar_no,
         (close >= limit_up - 0.01) as is_zt
    from series
   where limit_up is not null
),
grp as (
  select security_code, trade_date, is_zt, high, low, pct_change, turnover_rate_percent, amount_yuan,
         bar_no - row_number() over (partition by security_code, is_zt order by trade_date) as g
    from zt
),
runs as (
  select security_code, g, count(*) as seg_len, min(trade_date) as first_day, max(trade_date) as last_day
    from grp where is_zt group by 1, 2
)
select r.security_code, r.first_day as launch_date, r.seg_len, r.last_day as last_board_date,
       d.pct_change as launch_pct, d.turnover_rate_percent as launch_turnover,
       d.amount_yuan as launch_amount_yuan,
       (abs(d.high - d.low) <= 0.000001 * greatest(1, d.high)) as launch_is_1word,
       exists (select 1 from market_data.stock_daily q
                where q.security_code = r.security_code and q.trade_date > r.last_day) as seg_complete,
       sec.name, sec.board
  from runs r
  join market_data.stock_daily d on d.security_code = r.security_code and d.trade_date = r.first_day
  left join reference_data.security sec on sec.security_code = r.security_code
 where r.first_day between %(a)s::date and %(b)s::date
 order by r.first_day, r.security_code
"""

PATH_SQL = """
-- 一批（代码, 首板日）→ 之后 FORWARD 场的后复权 OHLC ＋ 之前 WINDOW 根的倍数（升序，缺量就不交）
with keys as (
  select (x->>0)::text as code, (x->>1)::date as launch from jsonb_array_elements(%(pairs)s::jsonb) x
),
px AS (
  select k.code, k.launch, s.trade_date, s.o, s.c, s.h, s.l
    from keys k cross join lateral (
      select q.trade_date,
             q.open * coalesce(q.adjustment_factor,1) as o,
             q.close * coalesce(q.adjustment_factor,1) as c,
             q.high * coalesce(q.adjustment_factor,1) as h,
             q.low  * coalesce(q.adjustment_factor,1) as l
        from market_data.stock_daily q
       where q.security_code = k.code and q.trade_date > k.launch
       order by q.trade_date limit %(fwd)s) s
),
vr AS (
  select k.code, k.launch, q.vol_ratio
    from keys k join lateral (
      select q.vol_ratio
        from analytics.volume_ratio_daily q
       where q.security_code = k.code and q.spec_version = 'vr-1'
         and q.trade_date < k.launch and q.vol_ratio is not null
       order by q.trade_date desc limit %(win)s) q on true
)
select code, launch::text as launch,
       jsonb_agg(jsonb_build_object('d', to_char(px.trade_date,'YYYY-MM-DD'),
                                    'o', px.o, 'c', px.c, 'h', px.h, 'l', px.l) order by px.trade_date) as path
  from px group by 1, 2
"""

VR_SQL = """
select q.security_code as code, to_char(q.trade_date,'YYYY-MM-DD') as d, q.vol_ratio
  from analytics.volume_ratio_daily q
 where q.spec_version = 'vr-1' and q.trade_date between %(from)s::date and %(to)s::date
   and q.security_code = any(%(codes)s::text[])
 order by 1, 2
"""

FIN_SQL = """
with keys as (
  select (x->>0)::text as code, (x->>1)::date as launch from jsonb_array_elements(%(pairs)s::jsonb) x
)
select k.code, k.launch::text as launch, f.report_date, f.revenue_yoy_percent, f.net_profit_yoy_percent,
       f.gross_margin_percent, f.roe_percent
  from keys k cross join lateral (
    select q.report_date, q.revenue_yoy_percent, q.net_profit_yoy_percent, q.gross_margin_percent, q.roe_percent
      from reference_data.stock_financial_period q
     where q.security_code = k.code and q.period_kind = 'cum'
       and q.report_date <= k.launch - %(lag)s
     order by q.report_date desc limit 1) f
"""

MOOD_SQL = ("select to_char(trade_date,'YYYY-MM-DD') d, limit_up_real, breadth_score, theme_score, "
            "breadth_level, theme_level from analytics.market_sentiment_daily "
            "where trade_date between %s and %s")
IDX_SQL = ("select security_code, to_char(trade_date,'YYYY-MM-DD') d, open, close from market_data.index_daily "
           "where security_code = any(%s) and trade_date between %s and %s")


def _pairs(events: Iterable[dict]) -> str:
    import json
    return json.dumps([[e['security_code'], str(e['launch_date'])] for e in events])


def _chunks(d_from: dt.date, d_to: dt.date) -> List[tuple]:
    out, a = [], d_from
    while a <= d_to:
        b = min(d_to, a + dt.timedelta(days=CHUNK_DAYS - 1))
        out.append((a, b))
        a = b + dt.timedelta(days=1)
    return out


def load_index(d_from: dt.date, d_to: dt.date) -> Dict[str, Dict[str, Dict[str, float]]]:
    """指数当日两个锚点都要：`c`（第 k 场收盘，分子的终点）与 `o`（进场当日开盘，基准的起点）。

    fb-1 只取了 `c` 并拿**进场当日收盘**当基准 ⇒ `ex1` 恒等于 `r1`（那一档里根本没有基准，
    屏幕上却写着"超额"），其余 ex* 也统一少了进场当日从开盘到收盘那一站。基准必须与进场时刻同一点。
    """
    rows = engine.query(IDX_SQL, ([list(dict.fromkeys(v for _, v in BENCH_PREFIX))],
                                  d_from - dt.timedelta(days=200), d_to + dt.timedelta(days=60)),
                       role='reader') or []
    idx: Dict[str, Dict[str, Dict[str, float]]] = {}
    for r in rows:
        idx.setdefault(r['security_code'], {})[r['d']] = {'o': _num(r['open']), 'c': _num(r['close'])}
    return idx


def collect(d_from: dt.date, d_to: dt.date) -> List[dict]:
    """一片窗口里的首板事件 + 形状 + 结果。分片由 `compute_range` 负责。"""
    events = engine.query(EVENTS_SQL, {'a': d_from, 'b': d_to}, role='etl') or []
    if not events:
        return []
    pairs = _pairs(events)
    paths = {f"{r['code']}|{r['launch']}": r['path'] for r in
             (engine.query(PATH_SQL, {'pairs': pairs, 'fwd': FORWARD, 'win': WINDOW}, role='etl') or [])}
    codes = sorted({e['security_code'] for e in events})
    # 倍数单独一条按代码+日期范围取（比在 PATH_SQL 里塞第二个 lateral 快得多，也避开了 jsonb_agg 混序）
    vr_rows = engine.query(VR_SQL, {'from': d_from - dt.timedelta(days=200), 'to': d_to,
                                    'codes': codes}, role='etl') or []
    series: Dict[str, List[float]] = {}
    for r in vr_rows:
        series.setdefault(r['code'], []).append((r['d'], r['vol_ratio']))
    fin = {f"{r['code']}|{r['launch']}": r for r in
           (engine.query(FIN_SQL, {'pairs': pairs, 'lag': DISCLOSE_LAG_DAYS}, role='etl') or [])}
    mood = {r['d']: r for r in (engine.query(MOOD_SQL, (d_from, d_to), role='reader') or [])}
    idx = load_index(d_from, d_to)

    out = []
    for e in events:
        key = f"{e['security_code']}|{e['launch_date']}"
        ld = str(e['launch_date'])
        ratios = [v for (d, v) in sorted(series.get(e['security_code'], []), key=lambda x: x[0])
                  if d < ld][-WINDOW:]
        feat = shape_features(ratios)
        path = [(p['d'], p['o'], p['c'], p['h'], p['l']) for p in (paths.get(key) or [])]
        entry_open = path[0][1] if path else None
        fwdm = forward_metrics(entry_open, path)
        b = bench_code(e['security_code'])
        bp = idx.get(b, {}) if b else {}
        # 基准的起点＝进场当日指数的**开盘**（与"次日开盘进场"是同一个时刻）。fb-1 用的是当日收盘，
        # 于是 ex1 的分子分母同日 ⇒ 基准恒为 0 ⇒ "超额"那一列其实一个基准都没含，屏幕上却写着超额。
        b0 = (bp.get(fwdm['entry_date']) or {}).get('o') if fwdm['entry_date'] else None
        ex = {}
        for n, r_key, x_key in ((1, 'r1', 'ex1'), (3, 'r3', 'ex3'), (5, 'r5', 'ex5'), (10, 'r10', 'ex10')):
            bb = None
            if b0 and len(path) >= n:
                here = (bp.get(path[n - 1][0]) or {}).get('c')
                bb = round((here / b0 - 1.0) * 100.0, 4) if here else None
            ex[x_key] = excess(fwdm[r_key], bb)
        f = fin.get(key) or {}
        m = mood.get(ld) or {}
        out.append({'security_code': e['security_code'], 'launch_date': e['launch_date'],
                    'spec_version': SPEC_VERSION, 'name': e.get('name'), 'board': e.get('board'),
                    'seg_len': int(e['seg_len']), 'seg_complete': bool(e['seg_complete']),
                    'last_board_date': e['last_board_date'],
                    'bench_code': b, 'launch_pct': _num(e.get('launch_pct')),
                    'launch_turnover': _num(e.get('launch_turnover')),
                    'launch_amount_yuan': _num(e.get('launch_amount_yuan')),
                    'launch_is_1word': bool(e.get('launch_is_1word')),
                    'limit_up_real': m.get('limit_up_real'), 'breadth_score': _num(m.get('breadth_score')),
                    'theme_score': _num(m.get('theme_score')),
                    'breadth_level': m.get('breadth_level'), 'theme_level': m.get('theme_level'),
                    'fin_report_date': f.get('report_date'),
                    'rev_yoy': _num(f.get('revenue_yoy_percent')), 'np_yoy': _num(f.get('net_profit_yoy_percent')),
                    'gross_margin': _num(f.get('gross_margin_percent')), 'roe': _num(f.get('roe_percent')),
                    **feat, **fwdm, **ex})
    return out


def _num(v: Any) -> Optional[float]:
    return None if v is None else (float(v) if not isinstance(v, (int, float)) else v)


#: 列清单**只有一个真值**：INSERT 的列、占位符个数、SET 子句都从它生成。
#: （本文件曾经把三处各写一遍，多一个少一个占位符就整批报错而人看不出是哪一处对不上。）
#: `calculated_at` 不进 INSERT 列表（默认值 `now()` 就是它的写入时刻），但重算要在 SET 里刷新它。
COLS = ('security_code', 'launch_date', 'spec_version', 'name', 'board', 'seg_len', 'seg_complete',
        'last_board_date', 'entry_date', 'entry_open', 'entry_locked', 'r1', 'r3', 'r5', 'r10',
        'maxup5', 'low5', 'bench_code', 'ex1', 'ex3', 'ex5', 'ex10', 'bars', 'dbl_days',
        'near_dbl_days', 'calm_days', 'last10_quiet', 'r_avg', 'r_med', 'r_p80', 'r_p90', 'r_max',
        'r_std', 'last5_avg', 'base25_avg', 'pre5', 'launch_pct', 'launch_turnover',
        'launch_amount_yuan', 'launch_is_1word', 'limit_up_real', 'breadth_score', 'theme_score',
        'breadth_level', 'theme_level', 'fin_report_date', 'rev_yoy', 'np_yoy', 'gross_margin', 'roe')

_PK = ('security_code', 'launch_date', 'spec_version')

UPSERT = (
    f"insert into {TABLE} ({', '.join(COLS)}) "
    f"values ({', '.join(['%s'] * len(COLS))}) "
    f"on conflict ({', '.join(_PK)}) do update set "
    + ', '.join(f'{c} = excluded.{c}' for c in COLS if c not in _PK)
    + ", calculated_at = now()")


def write(rows: List[dict]) -> int:
    if not rows:
        return 0
    params = [tuple(r.get(c) for c in COLS) for r in rows]
    return engine.executemany(UPSERT, params, role='etl')


def compute_range(d_from: dt.date, d_to: dt.date, *, write_rows: bool = True,
                  quiet: bool = False) -> Dict[str, Any]:
    total_w = total_n = 0
    for a, b in _chunks(d_from, d_to):
        rows = collect(a, b)
        total_n += len(rows)
        if rows and write_rows:
            total_w += write(rows)
        if not quiet:
            print('  %s .. %s：解析 %d 条%s' % (a, b, len(rows),
                                               '（dry-run 不写库）' if not write_rows else ''), flush=True)
    return {'asked': total_n, 'written': total_w, 'range': (str(d_from), str(d_to))}


def verify() -> List[str]:
    bad: List[str] = []
    r = engine.query_one(
        f"select count(*) n, count(distinct security_code) codes, min(launch_date)::text a, "
        f"max(launch_date)::text z, count(*) filter (where not seg_complete) 未走完, "
        f"count(*) filter (where bars < {WINDOW}) 短窗, count(*) filter (where entry_locked) 一字, "
        f"count(r5) 有r5, count(ex5) 有ex5, count(rev_yoy) 有财务, "
        f"count(*) filter (where ex1 is not null and abs(ex1 - r1) < 0.0000005) ex1充数, "
        f"count(*) filter (where dbl_days > bars) 越界 "
        f"from {TABLE} where spec_version = %s", (SPEC_VERSION,), role='reader')
    if not r or not r['n']:
        return ['这张表一行都没有 —— 回填没跑，还是写库坏了？']
    if int(r['越界']):
        bad.append(f'{r["越界"]} 行 dbl_days > bars（分类计数越界）')
    # 这条专抓 fb-1 那个静默缺陷：基准与终点同日 ⇒ 减出来恒等于 0，"超额"其实是绝对涨幅本身。
    if int(r['ex1充数']):
        bad.append(f'{r["ex1充数"]}/{r["n"]} 行 ex1 与 r1 逐条相等 —— 超额基准退化成了 0，'
                   f'那一列没有基准却写着「超额」（进场当日指数开盘有没有取到？）')
    latest = engine.scalar('select max(trade_date)::text from market_data.stock_daily')
    print(f'  库里：{r["n"]} 条 / {r["codes"]} 只 / 首板日 {r["a"]} … {r["z"]}（日线最新一场 {latest}）')
    print(f'  段未走完 {r["未走完"]} 条、启动前不满 {WINDOW} 根 {r["短窗"]} 条、次日一字 {r["一字"]} 条、'
          f'r5 有值 {r["有r5"]} 条、超额有值 {r["有ex5"]} 条、财务对上 {r["有财务"]} 条、'
          f'ex1 等于 r1（基准退化）{r["ex1充数"]} 条')
    if r['a'] > '2021-11-05':
        bad.append(f'起点只到 {r["a"]} —— 用户要的跨年样本没铺满，先按 12 个月分片回算')
    if int(r['有r5']) < int(r['n']) * 0.9:
        bad.append(f'r5 只有 {r["有r5"]}/{r["n"]} 有值 —— 是回算区间贴到了库尾，还是路径那条 SQL 没接上？')
    return bad


def sessions_back(n: int) -> dt.date:
    """库里最新一场往前 N 个交易日的日期（每日链重算范围用）。"""
    rows = engine.query(f"select distinct trade_date from market_data.stock_daily order by 1 desc limit {int(n)}",
                        (), role='reader') or []
    return rows[-1]['trade_date'] if rows else dt.date.today()


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description='首板形状预示（走到几板 / 次日进场）')
    ap.add_argument('--months', type=int, default=3, help='往前几个月（默认 3）')
    ap.add_argument('--start', help='区间起日（给了就覆盖 --months）')
    ap.add_argument('--end', help='区间止日（默认库里最新交易日）')
    ap.add_argument('--date', help='只算到这一天（等价 --end）')
    ap.add_argument('--recent-sessions', type=int, help='只重算最近 N 个交易日起（每日链用这个）')
    ap.add_argument('--dry-run', action='store_true', help='只解析不写库')
    ap.add_argument('--verify', action='store_true', help='写完/单独跑体检')
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.verify:
        bad = verify()
        print('\n'.join(f'✗ {b}' for b in bad) if bad else '✓ 首板形状表体检通过')
        return 3 if bad else 0

    d_to = (dt.date.fromisoformat(args.end) if args.end else
            dt.date.fromisoformat(args.date) if args.date else
            engine.scalar('select max(trade_date) from market_data.stock_daily'))
    if d_to is None:
        print('库里没有日线，谈不上母体')
        return 1
    if args.recent_sessions:
        d_from = sessions_back(int(args.recent_sessions))
    elif args.start:
        d_from = dt.date.fromisoformat(args.start)
    else:
        d_from = d_to - dt.timedelta(days=args.months * 31)
    print(f'=== 首板形状（{SPEC_VERSION}）：首板日 {d_from} .. {d_to}'
          f'{"（dry-run）" if args.dry_run else ""}', flush=True)
    res = compute_range(d_from, d_to, write_rows=not args.dry_run)
    if not res['asked']:
        print('  这一段里没有首板事件（区间不对，还是库里没这段日线？）—— 按设计不写，不算失败')
        return 2
    if args.dry_run:
        return 0
    print(f'  写入 {res["written"]} 行')
    bad = verify()
    print('\n'.join(f'✗ {b}' for b in bad) if bad else '✓ 体检通过')
    return 0 if not bad else 3


if __name__ == '__main__':
    sys.exit(main())
