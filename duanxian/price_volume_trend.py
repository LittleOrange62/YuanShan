# -*- coding: utf-8 -*-
"""「量价走势」读侧：同一只票的日线 / 周线 / 月线三块，各给 K 线 + 量柱 + 均线。

用户 2026-10-16：「个股深度复盘再加一个量价走势，日线、周线、月线分别为一块，占界面左中右三块」。

这一层只做四件事（判据一句都不在前端）：
1. **只读 `market_data.stock_daily` 一处**（本机 1,259 场全史，2021-08-02 起），周线与月线由日线聚合，
   不另找源、不建表 —— 建表就会与日线那张分叉（本项目在涨停价、 breadth 上犯过三方不一致）。
2. **复权口径明写**：价格按 `close × adjustment_factor` 折算，因子在最新一场为 1、越老越小
   ⇒ 这是**以今天为基准的前复权形状**（历史价被缩到与今天可比），不是名义成交价。
   每根同时交 `close_raw`（当天真成交的价），悬停两个都给。月线不折权会因分红除权假跳空。
3. **区间涨跌用复权收**，且与**上一根区间的收盘**比（不是与本期开盘比）——
   周线/月线的"涨跌"读者默认就是"比上周/上月收"，按开盘算会把整根实体读反。
4. **最后一根常常是"至今"**（当月未满、当周未收），交 `partial` 并在屏上说，
   不然一根只走了 3 天的"月线"看起来像暴跌。

数据现实（2026-10-16 实测本机）：全市场日线最早 2021-08-02 ⇒ 月线最多约 63 根；
次新与长期停牌的票会更少 ⇒ 各块自己报"画了几根 / 一共几根"，不硬凑。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from app.db import engine

from .quant import jsonable

SPEC = 'pvt-1'
DAILY_BARS = 60
WEEKLY_BARS = 60
MONTHLY_BARS = 48
FETCH_BARS = 1700          # 覆盖 63 个月的日线还有余量（全史才 1,259 根）
MIN_BUCKETS = 6            # 少于这个根数不画线：3 个点连线看着像"趋势成立"，那是骗人


CALIBER = (
    '三块都只读 `market_data.stock_daily`（不复权原始日线 + 复权因子），周线与月线由日线聚合：'
    '开=本期第一根的开、高=本期最高、低=本期最低、收=本期最后一根的收、量与额逐根相加、'
    '换手率逐根相加（近似：分母随股本变，跨期不是严格可加）。'
    '价格一律按 `close × adjustment_factor` 折算 —— 因子在最新一场为 1、越老越小，'
    '所以这是以今天为基准的前复权形状，与悬停里那个"不复权收"可以不同。'
    '均线复权收的简单移动平均：日线 MA5/MA20、周线 MA5/MA10、月线 MA3/MA6，'
    '均线用整段可用历史算完再截断，所以窗口最左边那根也有均线；'
    '但本机序列开头不足一根完整窗口的那几根不给均线（不拿三根的均值冒充 MA6）。'
)

DEFINITIONS: Dict[str, str] = {
    'date': '这根 K 线属于哪个期间：日线是交易日，周线是那一周的周一，月线是当月 1 号。',
    'open': '本期开盘（复权后）。日线就是当天开盘；周/月是本期第一根的开盘。',
    'high': '本期最高（复权后），取本期各根最高的最大者。',
    'low': '本期最低（复权后）。',
    'close': '本期收盘（复权后）= 本期最后一根的收 × 因子。悬停同时给不复权那一列，两个数不是一回事。',
    'close_raw': '当天名义收盘价（不复权）。历史那根的它与 `close` 的差就是这些年的分红与送转折算。',
    'pct': '本期涨跌 %：本期复权收 ÷ 上一期复权收 − 1。第一期没有基期就是 null，不当 0%。',
    'volume_wan_shares': '本期成交量（万股），逐根相加。',
    'amount_yi': '本期成交额（亿元），逐根相加。',
    'turnover_sum': '本期换手率 %（逐日相加，近似值：分母随股本变，跨期严格不可加，所以它只用来比"这周比上周活跃多少"）。',
    'bars': '这一根里含几根日线。周线正常 3~5 根、月线 15~23 根；只有 1 根的"月线"通常是当月刚开始。',
    'partial': '本期是否还没走完（当周未收 / 当月未满）。这根的高度、量都只到最新一场为止，不能当成一个完整周期读。',
    'ma': '复权收盘的简单移动平均（日线 MA5/MA20、周线 MA5/MA10、月线 MA3/MA6）。'
          '整段算完再截断，所以窗口最左边那根也有值；本机序列开头凑不满一整根窗口的那几根是 null（不给半截窗口）。',
    'as_of': '最新一场交易日。三块共用同一个 `as_of`，与上面「个股信息」那张卡同源同一天。',
}

HARD_CAVEATS = (
    {'code': 'adjusted_shape', 'text':
     '图上的价是前复权形状（以最新一场为基准），不是历史名义价：三块里最左那根的绝对价位可能从未真实成交过。'
     '要看当天真成交的价，悬停里那一列是不复权的。'},
    {'code': 'partial_last', 'text':
     '最后一根常常是"至今"（本周未收 / 本月未满），图上会单独标注；'
     '它的收盘与量都只到最新一场，拿它跟上一根完整周期比涨跌会偏。'},
    {'code': 'depth', 'text':
     '本机日线最早 2021-08-02 ⇒ 月线最多约 63 根，次新股更少。'
     '哪一块的根数不够，那一块自己报名字与实数，不硬凑成 60 根。'},
)


def _rows(code: str, limit: int = FETCH_BARS) -> List[Dict[str, Any]]:
    rows = engine.query(
        'select trade_date, open, high, low, close, volume_shares, amount_yuan, '
        'turnover_rate_percent, adjustment_factor from market_data.stock_daily '
        'where security_code = %s order by trade_date desc limit %s',
        (code, limit), role='reader')
    if rows is None:
        raise RuntimeError('日线读不到（market_data.stock_daily）—— 查询失败不是"这只票没有走势"')
    return list(reversed(rows))       # 旧→新，聚合与均线都按这个方向走


def _f(v: Any) -> Optional[float]:
    return None if v is None else float(v)


def _bar(raw: List[Dict[str, Any]]) -> Dict[str, Any]:
    """一组日线 → 一根 K 线。复权用各自那根的因子，所以跨除权日的高低不会被拉成假影。"""
    k = [(_f(r.get('adjustment_factor')) or 1.0) for r in raw]
    adj = lambda key: [(_f(r.get(key)) or 0.0) * kk for r, kk in zip(raw, k)]     # noqa: E731
    opens, highs, lows = adj('open'), adj('high'), adj('low')
    closes = adj('close')
    last = raw[-1]
    vol = sum(_f(r.get('volume_shares')) or 0.0 for r in raw)
    amt = sum(_f(r.get('amount_yuan')) or 0.0 for r in raw)
    trn = sum(_f(r.get('turnover_rate_percent')) or 0.0 for r in raw)
    return {
        'date': last['trade_date'].isoformat() if hasattr(last['trade_date'], 'isoformat') else str(last['trade_date'])[:10],
        'open': round(opens[0], 4), 'high': round(max(highs), 4),
        'low': round(min(lows), 4), 'close': round(closes[-1], 4),
        'close_raw': _f(last.get('close')),
        'volume_wan_shares': round(vol / 1e4, 2),
        'amount_yi': round(amt / 1e8, 4),
        'turnover_sum': round(trn, 3) if trn else None,
        'bars': len(raw),
        '_adj_close': closes[-1],
    }


def _rollup(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """几根日线 K 线 → 一根周/月 K 线（开=首根开、高=最高、低=最低、收=末根收、量额相加）。

    入参已经是 `_bar()` 折算过的日线，所以这里不再乘因子：一根日线的高/低只在它自己那一天有效，
    跨除权日也不会互相拉成假影。日线那一块传进来的是单根（`bars` 数＝1），走的同一条代码。
    """
    first, last = bars[0], bars[-1]
    trn = sum(b.get('turnover_sum') or 0.0 for b in bars)
    return {
        'date': last['date'], 'open': first['open'],
        'high': max(b['high'] for b in bars), 'low': min(b['low'] for b in bars),
        'close': last['close'], 'close_raw': last['close_raw'],
        'volume_wan_shares': round(sum(b.get('volume_wan_shares') or 0.0 for b in bars), 2),
        'amount_yi': round(sum(b.get('amount_yi') or 0.0 for b in bars), 4),
        'turnover_sum': round(trn, 3) if trn else None,
        'bars': sum(b.get('bars') or 1 for b in bars),
        '_adj_close': last['_adj_close'],
    }


def _grouped(bars: List[Dict[str, Any]], key_of) -> List[List[Dict[str, Any]]]:
    """把连续的日线按同一个期间归到一组（升序序列上，同一周/月必然相邻）。"""
    buckets: List[List[Dict[str, Any]]] = []
    for b in bars:
        if buckets and key_of(buckets[-1][-1]) == key_of(b):
            buckets[-1].append(b)
        else:
            buckets.append([b])
    return buckets


def _iso_week(b: Dict[str, Any]) -> tuple:
    d = dt.date.fromisoformat(b['date'])
    y, w, _ = d.isocalendar()
    return (y, w)


def _month(b: Dict[str, Any]) -> tuple:
    return tuple(int(x) for x in b['date'][:7].split('-'))


def _week_start(date_str: str) -> str:
    d = dt.date.fromisoformat(date_str)
    return (d - dt.timedelta(days=d.weekday())).isoformat()


def _month_start(date_str: str) -> str:
    return date_str[:7] + '-01'


def _series(daily: List[Dict[str, Any]], buckets: List[List[Dict[str, Any]]],
            want: int, label_of, period_start, min_bars: int) -> Dict[str, Any]:
    out: List[Dict[str, Any]] = []
    prev_close: Optional[float] = None
    for grp in buckets:
        b = _rollup(grp)
        b['date'] = period_start(b['date']) if period_start else b['date']
        b['pct'] = None if prev_close in (None, 0) else round((b['_adj_close'] / prev_close - 1.0) * 100.0, 2)
        b['partial'] = False
        prev_close = b['_adj_close']
        out.append(b)
    if out:
        out[-1]['partial'] = True            # 最后一组永远是"走到最新一场为止"
    closes = [o['_adj_close'] for o in out]
    for n in min_bars:
        for i, o in enumerate(out):
            # 只有凑满一整根窗口才给均线：三根的均值挂上 MA6 的牌子就是假读数
            o[f'ma{n}'] = round(sum(closes[i - n + 1:i + 1]) / n, 4) if i + 1 >= n else None
    for o in out:
        o.pop('_adj_close', None)
    return {
        'bars_total': len(out),
        'drawn': min(want, len(out)),
        'rows': out[-want:] if want < len(out) else out,
        'ma_keys': [f'ma{n}' for n in min_bars],
        'min_bars': MIN_BUCKETS,
        'too_few': len(out) < MIN_BUCKETS,
        'label_of': label_of,
    }


def board(code: str, days: int = DAILY_BARS, weeks: int = WEEKLY_BARS,
          months: int = MONTHLY_BARS) -> Dict[str, Any]:
    """三块（日/周/月）的全部读数。不可用时只交 available/reason。"""
    code = (code or '').strip().upper()
    if not code:
        return {'available': False, 'reason': '没交代码', 'code': code}
    rows = _rows(code)
    if not rows:
        return {'available': False, 'code': code,
                'reason': '本机 `market_data.stock_daily` 里没有这个代码的一根日线 —— '
                          '先确认代码带没带市场后缀；美/港/韩股不走本机日线库。'}
    daily = [_bar([r]) for r in rows]
    dser = _series(daily, [[d] for d in daily], days, 'day', None, (5, 20))
    # 日线不存在"本期未走完"：最后一根的收盘就是最新一场的真收（周/月才是截到那天为止）
    for o in dser['rows']:
        o['partial'] = False
    wser = _series(daily, _grouped(daily, _iso_week), weeks, 'week', _week_start, (5, 10))
    mser = _series(daily, _grouped(daily, _month), months, 'month', _month_start, (3, 6))
    caveats = list(HARD_CAVEATS)
    name = engine.query_one(
        "select coalesce(nullif(name,''), security_code) n from reference_data.security where security_code=%s",
        (code,), role='reader')
    if wser['bars_total'] < MIN_BUCKETS or mser['bars_total'] < MIN_BUCKETS:
        caveats.append({'code': 'too_few', 'text':
            f'周线 {wser["bars_total"]} 根、月线 {mser["bars_total"]} 根 —— '
            '少于 6 根的那一块不画线（点数太少连线看起来像趋势，那是骗人），只把有的那几根列出来。'})
    return jsonable({
        'available': True, 'code': code, 'name': (name or {}).get('n'), 'spec': SPEC,
        'as_of': daily[-1]['date'],
        'daily': dser, 'weekly': wser, 'monthly': mser,
        'want': {'daily': days, 'weekly': weeks, 'monthly': months},
        'caliber': CALIBER, 'definitions': DEFINITIONS, 'caveats': caveats,
    })
