# -*- coding: utf-8 -*-
"""
板块趋势形成度与左侧低吸信号（判据：`docs/页面展示工作指南/05-板块趋势与低吸判据.md`）。

一句话：**把"有点斜率"和"形成趋势了"分开，把"形成趋势"和"现在还能不能低吸"再分开。**

三条设计约束，都是这套判据最容易骗人的地方：

1. **算不出就是 NULL，不是 0。** 板块涨幅能被三只权重股拉起来，所以广度
   （`up_count/member_count`）单独一项；缺涨跌家数的板块广度项是 NULL，
   0 分会参与排名、NULL 不参与 —— 这是两件事。
2. **点不足一律 `insufficient`，不补 0、不缩短窗口。** 3 个点也能拟合出一条斜率，
   但那不是 21 日趋势；把它当 21 日趋势用会看错方向。`point_count` 原样进 `reasons`。
3. **`spec_version` 进主键。** 调阈值/权重是**新一批行**，旧行不动 ——
   否则历史上的"刚转"会被今天的参数悄悄改写，回看等于造假。

两种 `scope`（`run()` 现在只放行第一种，第二种明写拒绝）：
  * `sector`         通达信板块（默认母体 = 概念 + 行业）。成分表 `sector_member` 带
                     `in_date/out_date` 是真时点，所以板块的历史可以回算。
  * `group`          自选组。**只有每日成员快照攒够长窗口之后才成立** —— 要按
                     `watchlist_member_snapshot` 逐日重建成员表现。
  * `group_rebuilt`  以**今日名单**回算过去：这是未来函数（用未来知道的名单做过去的决定），
                     只给人看形状，**不可用于回测与胜率统计**，界面必须和实盘序列分开显示。
                     这条刻意不做成"顺手一算"：一旦落进同一张表，日后没人分得清哪批行是假的。
                     `--scope group*` 现在返回 `skipped` 并把快照场次数交出来，不写一行。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import defaultdict
from typing import Any

from app.db import engine

SETTING_KEY = 'sector_trend_signal.params'
FALLBACK: dict[str, Any] = {
    'windows': [5, 11, 21],
    'min_points_ratio': 0.7,
    'position_low': 0.35, 'position_high': 0.6,
    'turn_lookback': 3, 'breadth_min': 0.55, 'vol_range': [1.0, 3.0],
    'overheat': {'position': 0.85, 'heat_pctile': 0.9},
    'weights': {'freshness': 35, 'position': 25, 'breadth': 20, 'volume': 10, 'rank': 10},
    'sector_types': ['概念', '行业'],
    'rank_lookback': 5,
    'spec_version': 'sts-2',
}


def params() -> dict:
    """生效参数。库里那份优先；读不到或形状不对就退回默认，并让调用方能看出用的是哪一份。"""
    row = engine.query_one('select value from config.setting where key = %s', (SETTING_KEY,))
    raw = row.get('value') if row else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    out = dict(FALLBACK)
    if isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if k in FALLBACK})
    return out


def _f(x: Any) -> float | None:
    """PG 的 numeric 回来是 Decimal，参与算术会一路传染成 Decimal；统一先落地成 float。"""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def _clamp01(x: float) -> float:
    v = _f(x)
    if v is None:
        return 0.0
    return max(0.0, min(1.0, v))


def _position(prices: list[float]) -> float | None:
    """收盘价在窗口高低区间里的位置。0=最低、1=最高。低吸的全部含意都在这一项。"""
    vals = [_f(x) for x in prices]
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return None
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return None            # 整窗口一个价：位置无定义，不硬给 0.5（那是"看不出来"，不是"正中间"）
    return (vals[-1] - lo) / (hi - lo)


def _dir_agree(slopes: list[float | None], strength: float | None) -> tuple[float | None, int, int]:
    """短/中/长三窗口斜率同向的程度。一根插针就能翻掉单窗口斜率，所以三窗口同向才算形状。

    返回 `(得分, 正向数, 负向数)`。**档位不许从得分反推方向**：得分里含强度分，
    2 正 1 负也可能凑到 60 分 —— 实测就发生过把中线斜率 -13 的板块判成 `forming`。
    所以同向数单独交出去，判档用同向数、画图用得分。
    """
    got = [v for v in (_f(s) for s in slopes) if v is not None]
    if len(got) < 2:
        return None, 0, 0
    pos = sum(1 for s in got if s > 0)
    neg = sum(1 for s in got if s < 0)
    same = max(pos, neg)
    base = {3: 60.0, 2: 20.0}.get(same, 0.0)
    if same < 2 or strength is None:
        return base, pos, neg
    # 强度分：|中线斜率| 相对同日全体板块的 60 分位，最多 40 分
    return base + 40.0 * _clamp01(strength), pos, neg


def _volume_score(vr: float | None, lo: float, hi: float) -> float | None:
    """量比。温和放量给满分，**超过 hi 反而降分**：三倍量通常是一次性情绪而不是趋势启动。"""
    if vr is None:
        return None
    if lo <= vr <= hi:
        return 1.0
    if vr > hi:
        return _clamp01(1.0 - (vr - hi) / hi)
    return _clamp01(vr / lo)


def _rank_score(rank_now: int | None, rank_prev: int | None) -> float | None:
    if rank_now is None or rank_prev is None:
        return None
    return _clamp01((rank_prev - rank_now) / max(1, rank_prev))


def _heat_pctile(heats: list, now: float | None) -> float | None:
    """热度在**自身**长窗口里的分位（0~1，1.0 = 正处于自身区间最高）。

    口径与 `analytics.ignition.detail._q_heat` 一致：只看过去、窗口含当日。
    刻意不用「当日热度在全体板块里的名次 / 母体」—— 那是横截面位置，和 `position`
    （自身价格区间里的位置）不是同一个维度，放进同一条过热判据里会把**最冷**的板块
    读成过热（sts-1 就是这么错的：实测 369 行 `overheated` 的热度全在当日中位数以下）。
    横截面名次仍然要交出去，但在 `reasons.heat_rank` + `universe` 里，不进档位。
    """
    if now is None:
        return None
    vals = [v for v in (_f(x) for x in heats) if v is not None]
    if len(vals) < 2:
        return None
    return sum(1 for v in vals if v <= now) / len(vals)


def _fetch_sector_history(dmax: dt.date, p: dict, hist: int) -> dict[str, list[dict]]:
    ws = [int(w) for w in p['windows']]
    lo, mid, hi = ws[0], ws[len(ws) // 2], ws[-1]
    sql = f"""
        with days as (
            select distinct trade_date from analytics.sector_heat_daily
             where source = 'sector' and trade_date <= %(dmax)s
             order by trade_date desc limit %(hist)s
        )
        select h.sector_code, h.trade_date, h.heat,
               s.close, s.up_count, s.down_count, s.member_count, s.amount_yuan,
               i.name, i.sector_type,
               t_lo.slope  as slope_lo,  t_lo.point_count  as p_lo,
               t_md.slope  as slope_mid, t_md.point_count  as p_mid,
               t_hi.slope  as slope_hi,  t_hi.point_count  as p_hi
          from days d
          join analytics.sector_heat_daily h on h.trade_date = d.trade_date and h.source = 'sector'
          join reference_data.sector_info i on i.sector_code = h.sector_code
           and i.is_active and i.sector_type = any(%(types)s)
          left join market_data.sector_daily s on s.sector_code = h.sector_code and s.trade_date = h.trade_date
          left join analytics.sector_trend t_lo on t_lo.sector_code = h.sector_code
               and t_lo.trade_date = h.trade_date and t_lo.window_days = %(ws_lo)s
          left join analytics.sector_trend t_md on t_md.sector_code = h.sector_code
               and t_md.trade_date = h.trade_date and t_md.window_days = %(ws_mid)s
          left join analytics.sector_trend t_hi on t_hi.sector_code = h.sector_code
               and t_hi.trade_date = h.trade_date and t_hi.window_days = %(ws_hi)s
         order by h.sector_code, h.trade_date
    """
    rows = engine.query(sql, {'dmax': dmax, 'hist': hist, 'ws_lo': lo, 'ws_mid': mid, 'ws_hi': hi,
                             'types': [str(t) for t in p['sector_types']]})
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[r['sector_code']].append(r)
    return out


def _windows(p: dict) -> list[int]:
    return [int(w) for w in p['windows']]


def _min_points(p: dict, w: int) -> int:
    return int(round(float(p['min_points_ratio']) * w))


def _series_rows(rows: list[dict]) -> list[float | None]:
    return [r.get('close') for r in rows]


def compute_one(series: list[dict], idx: int, p: dict, strength_ref: float | None,
                ranks: dict[dt.date, int], universe: int) -> dict:
    """算第 idx 个交易日这一行的六分项 + 档位。series 是按日期升序的该板块历史。"""
    ws = _windows(p)
    up_to = series[:idx + 1]
    row = series[idx]
    day = row['trade_date']

    slopes = [_f(row.get('slope_lo')), _f(row.get('slope_mid')), _f(row.get('slope_hi'))]
    points = [row.get('p_lo'), row.get('p_mid'), row.get('p_hi')]   # 点数是整数，原样带着进 reasons
    missing_pts = sum(1 for w, pc in zip(ws, points) if (pc or 0) < _min_points(p, w))

    # 持续性：窗口内热度抬升的天数占比
    win = ws[len(ws) // 2]
    heats = [r.get('heat') for r in up_to[-win:] if r.get('heat') is not None]
    persist = None
    if len(heats) >= 2:
        hv = [_f(x) for x in heats]
        hv = [x for x in hv if x is not None]
        ups = sum(1 for a, b in zip(hv, hv[1:]) if b > a)
        persist = 100.0 * ups / (len(hv) - 1) if len(hv) >= 2 else None

    up_ratio = None
    if row.get('up_count') is not None and row.get('member_count'):
        try:
            up_ratio = _f(row['up_count']) / _f(row['member_count'])
        except (TypeError, ZeroDivisionError):
            up_ratio = None

    vols = [v for v in (_f(r.get('amount_yuan')) for r in up_to[-6:]) if v]
    vr = None
    if len(vols) >= 3 and vols[-1]:
        base = sum(vols[:-1]) / len(vols[:-1])
        vr = vols[-1] / base if base else None

    pos = _position([r for r in _series_rows(up_to[-ws[-1]:]) if r is not None])

    rank_now = ranks.get(day)
    lb = int(p.get('rank_lookback', 5))
    prev_day = up_to[-lb - 1]['trade_date'] if len(up_to) > lb else None
    rank_score = _rank_score(rank_now, ranks.get(prev_day) if prev_day else None)

    heat_pct = _heat_pctile([r.get('heat') for r in up_to[-ws[-1]:]], _f(row.get('heat')))

    ref = _f(strength_ref)
    dir_agree, pos_n, neg_n = _dir_agree(slopes, (abs(slopes[1]) / ref) if (ref and slopes[1] is not None) else None)
    volume_ok = (_volume_score(vr, float(p['vol_range'][0]), float(p['vol_range'][1]))
                 if vr is not None else None)
    terms = {'dir_agree': dir_agree, 'persist': persist,
             'breadth': (100.0 * up_ratio if up_ratio is not None else None),
             'volume_ok': (100.0 * volume_ok if volume_ok is not None else None),
             'position': (100.0 * pos if pos is not None else None),
             'rank_momentum': (100.0 * rank_score if rank_score is not None else None)}
    missing = sum(1 for v in terms.values() if v is None)

    # 「刚转」：短窗斜率在 turn_lookback 天内由 ≤0 翻正
    back = int(p['turn_lookback'])
    turned_days = None
    if slopes[0] is not None and slopes[0] > 0:
        hist_short = [r.get('slope_lo') for r in up_to[-(back + 1):-1]]
        if any(s is not None and s <= 0 for s in hist_short):
            negs = [i for i, s in enumerate(hist_short) if s is not None and s <= 0]
            turned_days = (len(hist_short) - 1 - negs[-1]) if negs else None
    freshness = None
    if turned_days is not None:
        freshness = _clamp01(1.0 - turned_days / (back + 1))

    regime, why = _regime(p, missing_pts=missing_pts, slopes=slopes, points=points,
                          pos=pos, up_ratio=up_ratio, heat_pct=heat_pct,
                          turned_days=turned_days, dir_agree=dir_agree,
                          pos_n=pos_n, neg_n=neg_n, vr=vr)

    dip = None
    if regime in ('turn_up_seed', 'forming'):
        w = p['weights']
        parts = [(w['freshness'], freshness),
                 (w['position'], (1.0 - pos) if pos is not None else None),
                 (w['breadth'], _clamp01((up_ratio - 0.4) / 0.4) if up_ratio is not None else None),
                 (w['volume'], volume_ok),
                 (w['rank'], rank_score)]
        got = [(float(wt), _f(v)) for wt, v in parts if v is not None]
        got = [(wt, v) for wt, v in got if v is not None]
        if got:
            # 缺项不补 0、也不归一化放大：权重本身就是百分数，缺的那几项就按 0 计入，
            # 但把"这一行的上限只有 80"一起交出去 —— 否则缺项的行会被读成"分数低"，那是两件事。
            total = sum(wt for wt, _ in got)
            dip = round(sum(wt * v for wt, v in got), 2)
            result_note = round(total, 1)

    return {'terms': terms, 'regime': regime, 'dip_score': dip,
            'missing_terms': missing + missing_pts, 'heat': _f(row.get('heat')),
            'heat_pctile': heat_pct, 'slope_short': slopes[0], 'slope_mid': slopes[1],
            'slope_long': slopes[2], 'points_short': points[0], 'points_mid': points[1],
            'points_long': points[2], 'universe': universe,
            'reasons': {'dip_score_ceiling': locals().get('result_note'),
                        'windows': ws, 'point_counts': points,
                        'min_points': [_min_points(p, w) for w in ws],
                        'up_ratio': up_ratio, 'vol_ratio': vr, 'position': pos,
                        'heat_rank': rank_now, 'heat_self_pctile': heat_pct,
                        'turned_days_ago': turned_days,
                        'dir_agree_raw_slopes': slopes,
                        'missing_terms': missing + missing_pts,
                        'why': why}}


def _regime(p: dict, *, missing_pts: int, slopes: list, points: list, pos, up_ratio,
            heat_pct, turned_days, dir_agree, pos_n: int = 0, neg_n: int = 0,
            vr: float | None = None) -> tuple[str, str]:
    """档位互斥、按序判；第二个返回值是"凭什么"的短句，进 reasons。"""
    if missing_pts:
        return 'insufficient', f'{missing_pts} 个窗口的点数不足 min_points'
    if slopes[1] is not None and slopes[1] < 0 and (slopes[2] or 0) < 0 and neg_n >= 2:
        return 'downtrend', '中线与长线斜率同为负（至少两个窗口向下）'
    if (turned_days is not None and pos is not None and pos <= float(p['position_low'])
            and up_ratio is not None and up_ratio >= float(p['breadth_min'])):
        return 'turn_up_seed', (f'短窗斜率 {turned_days} 日前由负翻正、位置 {pos:.2f} ≤ '
                               f'{p["position_low"]}、广度 {up_ratio:.0%} ≥ {p["breadth_min"]}')
    # 「过热」排在「已确立」之前：位置 0.95、热度分位 1.0 会被 established 先吃掉，
    # 而那一档恰恰是左侧交易最该避开的 —— 顺序判据在这里会骗人，所以先说不该伸手。
    # 判据文档 §2 第 6 档写的是「position ≥ 0.85 **且** heat_pctile ≥ 0.9（或 V_t > 3）」：
    # 两项都要高才算过热 —— 单看位置高，可能只是它自己横盘在区间上沿、热度根本没起来。
    oh = p['overheat']
    both_high = (pos is not None and heat_pct is not None
                 and pos >= float(oh['position']) and heat_pct >= float(oh['heat_pctile']))
    blowout = vr is not None and vr > float(p['vol_range'][1])
    if both_high or blowout:
        return 'overheated', (f'位置 {round(pos, 2) if pos is not None else "—"} / 热度自身分位 '
                              f'{round(heat_pct, 2) if heat_pct is not None else "—"}'
                              + (f'、量比 {vr:.2f} > {p["vol_range"][1]}（一次性情绪）' if blowout else ''))
    # "三窗同为正"只看同向数，不看得分：得分含强度分，2 正 1 负也能凑到 60
    if pos_n == 3 and pos is not None:
        if pos > float(p['position_high']):
            return 'established', '三窗口斜率同为正、位置已抬到区间上半部'
        return 'forming', '三窗口斜率同为正、位置仍在下半部'
    # 点数够、但六项没有一个凑成分形：老实说 `no_shape`，不落进 insufficient（那是"数据不够"，两件事）
    return 'no_shape', '点数够但方向/位置/广度没有凑成任一分档 —— 判据给不出形状，不等于数据不足'


_UPSERT = """
insert into analytics.sector_trend_signal
  (sector_code, trade_date, window_set, scope, universe,
   dir_agree, persist, breadth, volume_ok, position, rank_momentum,
   heat, heat_pctile, slope_short, slope_mid, slope_long,
   points_short, points_mid, points_long, dip_score, regime, reasons,
   missing_terms, spec_version, calculated_at)
values
  (%(code)s, %(day)s, %(wset)s, %(scope)s, %(universe)s,
   %(dir_agree)s, %(persist)s, %(breadth)s, %(volume_ok)s, %(position)s, %(rank_momentum)s,
   %(heat)s, %(heat_pctile)s, %(slope_short)s, %(slope_mid)s, %(slope_long)s,
   %(points_short)s, %(points_mid)s, %(points_long)s, %(dip_score)s, %(regime)s,
   %(reasons)s::jsonb, %(missing_terms)s, %(spec)s, now())
on conflict (sector_code, trade_date, window_set, scope, spec_version) do update set
  universe = excluded.universe,
  dir_agree = excluded.dir_agree, persist = excluded.persist, breadth = excluded.breadth,
  volume_ok = excluded.volume_ok, position = excluded.position, rank_momentum = excluded.rank_momentum,
  heat = excluded.heat, heat_pctile = excluded.heat_pctile,
  slope_short = excluded.slope_short, slope_mid = excluded.slope_mid, slope_long = excluded.slope_long,
  points_short = excluded.points_short, points_mid = excluded.points_mid, points_long = excluded.points_long,
  dip_score = excluded.dip_score, regime = excluded.regime, reasons = excluded.reasons,
  missing_terms = excluded.missing_terms, calculated_at = excluded.calculated_at
"""


def _row_params(code: str, day: dt.date, wset: str, scope: str, result: dict, spec: str) -> dict:
    t = result['terms']
    return {
        'code': code, 'day': day, 'wset': wset, 'scope': scope, 'universe': result['universe'],
        'dir_agree': t['dir_agree'], 'persist': t['persist'], 'breadth': t['breadth'],
        'volume_ok': t['volume_ok'], 'position': t['position'], 'rank_momentum': t['rank_momentum'],
        'heat': result['heat'], 'heat_pctile': result['heat_pctile'],
        'slope_short': result['slope_short'], 'slope_mid': result['slope_mid'], 'slope_long': result['slope_long'],
        'points_short': result['points_short'], 'points_mid': result['points_mid'], 'points_long': result['points_long'],
        'dip_score': result['dip_score'], 'regime': result['regime'],
        'reasons': json.dumps(result['reasons'], ensure_ascii=False, default=str),
        'missing_terms': result['missing_terms'], 'spec': spec}


def _flush(batch: list[dict]) -> int:
    """一批一批地写。逐行 `execute` 在 30 场上无所谓，全量 86 万行时它是主要耗时。

    `rowcount` 在 executemany 之后不保证是累计值，所以按送进去的行数计数 ——
    真出事时以「库里的行数对不上」暴露，而不是这里悄悄少算。
    """
    engine.executemany(_UPSERT, batch)
    return len(batch)


def run(target_dates: list[dt.date], *, scope: str = 'sector', dry_run: bool = False) -> dict:
    """算并把结果写回 PG。`dry_run` 时只算不写，且**不产生任何写库副作用**。"""
    p = params()
    ws = _windows(p)
    wset = '/'.join(str(w) for w in ws)
    spec = str(p['spec_version'])
    if scope != 'sector':
        need = max(ws)
        have = snapshot_days()
        return {'rows': 0, 'scope': scope,
                'skipped': (f'scope={scope} 还没接：组序列要用每日成员快照重建成分表现，'
                            f'目前快照 {have} 场 / 长窗口需要 {need} 场。'
                            '放行只会把通达信板块口径的结论贴上"自选组"标签写进库 —— 那是造假，不是回填')}
    if not target_dates:
        return {'rows': 0, 'skipped': '没有目标交易日'}
    dmax = max(target_dates)
    # 历史长度要按**目标日期跨度**算，不是固定 31 天：只给尾部 31 场的话，
    # 回算更早的日期时 21 日窗口凑不够点，全批变成 insufficient —— 静默地算出一堆废行。
    hist = max(ws) + int(p.get('rank_lookback', 5)) + 5 + len(set(target_dates))
    series_by = _fetch_sector_history(dmax, p, hist)
    if not series_by:
        return {'rows': 0, 'skipped': '母体为空（检查 sector_type 参数与 sector_daily/heat 是否有数）'}

    days = sorted({r['trade_date'] for rows in series_by.values() for r in rows})
    # 三张索引一次建好。原来每算一行都 `{d: rk for (c, d), rk in ranks.items() if c == code}`，
    # 那是「行数 × 全表名次条目」的乘积：30 场还能跑（85 秒），全量 1244 场要跑一天。
    day_rows: dict[dt.date, list[tuple[str, int]]] = defaultdict(list)   # 当日有哪些板块、在其序列的第几行
    heat_of: dict[tuple[str, int], float | None] = {}                    # 键是 (板块, 行号)，与 day_rows 对齐
    for code, rows in series_by.items():
        for i, r in enumerate(rows):
            day_rows[r['trade_date']].append((code, i))
            heat_of[(code, i)] = _f(r.get('heat'))

    ranks_by_code: dict[str, dict[dt.date, int]] = defaultdict(dict)
    universe_by_day: dict[dt.date, int] = {}
    slope_mid_by_day: dict[dt.date, list[float]] = defaultdict(list)
    for day in days:
        ordered = sorted(day_rows[day], key=lambda ci: (heat_of[ci] is None, -(heat_of[ci] or -1e18)))
        universe_by_day[day] = len(ordered)
        for i, (code, idx) in enumerate(ordered, start=1):
            ranks_by_code[code][day] = i
        for code, idx in day_rows[day]:
            v = _f(series_by[code][idx].get('slope_mid'))
            if v is not None:
                slope_mid_by_day[day].append(abs(v))

    # 强度参照：同日 |中线斜率| 的 60 分位（跨板块比，不是和自己比 —— 这点写进界面判据）
    strength_ref: dict[dt.date, float | None] = {}
    for day, vals in slope_mid_by_day.items():
        vals.sort()
        strength_ref[day] = vals[int(len(vals) * 0.6)] if vals else None

    targets = set(target_dates)
    batch: list[dict] = []
    written = 0
    per_day: dict[str, dict] = {}
    for day in sorted(targets):
        counts: dict[str, int] = defaultdict(int)
        for code, idx in day_rows.get(day, []):
            res = compute_one(series_by[code], idx, p, strength_ref.get(day),
                              ranks_by_code[code], universe_by_day.get(day, 0))
            counts[res['regime']] += 1
            if not dry_run:
                batch.append(_row_params(code, day, wset, scope, res, spec))
        per_day[str(day)] = dict(counts)
        if not dry_run and len(batch) >= 2000:
            written += _flush(batch)
            batch = []
    if batch:
        written += _flush(batch)
    # 目标日在热度表里没有行 = 上游还没入库。这里必须说出来：
    # 沉默地写 0 行、退出码 0，链子上看就是"这一步成功了"，而库里什么都没多。
    no_heat = [str(d) for d in sorted(targets) if not day_rows.get(d)]
    return {'rows': written, 'windows': ws, 'window_set': wset, 'spec_version': spec,
            'scope': scope, 'per_day': per_day, 'dry_run': dry_run,
            'no_heat_days': no_heat}


def snapshot_members(day: dt.date, *, dry_run: bool = False) -> dict:
    """把当天的自选组成员名单存一份。**这是唯一能让组趋势变成"实盘序列"的办法。**"""
    rows = engine.query("""
        select g.group_id, g.name, s.security_code
          from config.watchlist_group g
          join config.watchlist_stock s on s.group_id = g.group_id
         order by g.group_id, s.security_code""")
    if dry_run:
        return {'captured': len(rows), 'groups': len({r['group_id'] for r in rows}), 'dry_run': True}
    n = 0
    for r in rows:
        n += engine.execute("""
            insert into reference_data.watchlist_member_snapshot (trade_date, group_id, group_name, security_code, captured_at)
            values (%(d)s, %(g)s, %(n)s, %(c)s, now())
            on conflict (trade_date, group_id, security_code) do update set group_name = excluded.group_name""",
            {'d': day, 'g': r['group_id'], 'n': r['name'], 'c': r['security_code']})
    return {'captured': n, 'groups': len({r['group_id'] for r in rows}), 'day': str(day)}


def snapshot_days() -> int:
    return int(engine.scalar('select count(distinct trade_date) from reference_data.watchlist_member_snapshot') or 0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1] if __doc__ else '')
    ap.add_argument('--date', action='append', default=[], help='目标交易日，可重复；不给就用热度表最新一场')
    ap.add_argument('--backfill', type=int, default=0, help='回算最近 N 个交易日（含当日）')
    ap.add_argument('--scope', default='sector', choices=['sector', 'group', 'group_rebuilt'])
    ap.add_argument('--snapshot-only', action='store_true', help='只存今天的自选成员快照')
    ap.add_argument('--dry-run', action='store_true', help='只算不写；这条路径上不得有任何写库副作用')
    a = ap.parse_args(argv)

    if a.snapshot_only or a.scope in ('group', 'group_rebuilt'):
        today = dt.date.today()
        print(json.dumps(snapshot_members(today, dry_run=a.dry_run), ensure_ascii=False))
        print(f'快照累计天数：{snapshot_days()}（21 日窗口需要 21 场）')
        if a.snapshot_only:
            return 0

    days = [dt.date.fromisoformat(x) for x in a.date]
    if not days:
        last = engine.scalar("select max(trade_date) from analytics.sector_heat_daily where source='sector'")
        days = [last]
    if a.backfill > 1:
        hist = engine.query("""select distinct trade_date from analytics.sector_heat_daily
                                where source='sector' order by trade_date desc limit %s""", (a.backfill,))
        days = sorted(r['trade_date'] for r in hist)

    # 成员快照顺手在这里存一份 —— **不能等有人单独去点 `--snapshot-only`**：
    # 自选组的实盘序列要求连续 21 场，漏一天就少一天，而漏的那些天事后补不回来
    # （补出来的只是"今日名单"，那是未来函数）。dry-run 这条路径上不许有写副作用。
    if not a.dry_run and a.scope == 'sector':
        snap = snapshot_members(dt.date.today())
        print('成员快照：%(captured)s 行 / %(groups)s 组（累计 %(days)s 场，21 日窗口需要 21 场）'
              % {**snap, 'days': snapshot_days()})

    res = run(days, scope=a.scope, dry_run=a.dry_run)
    print(json.dumps(res, ensure_ascii=False, default=str, indent=2))
    # 组那条路是"还没到时候"，不是失败：链子上跑要交 0，别把每日追平拖红。
    if res.get('skipped') and a.scope == 'sector':
        return 1
    if (not a.dry_run and a.scope == 'sector' and res.get('no_heat_days')
            and not res.get('rows')):
        print('**目标交易日在 `analytics.sector_heat_daily` 里没有行**（%s）：'
              '上游 daily/sectortrend 还没入库，本步不算成功' % '、'.join(res['no_heat_days']))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
