# -*- coding: utf-8 -*-
"""`analytics.limit_up_pool_daily`（lup-1）· 首页「涨停与连板」的母体写侧。

用户 2026-10-26 点名：这一屏的数**要在库里**，不要每次请求现抓东财池子。落库后
读侧（`duanxian/limit_up_board.py`）只读这张表，`vr/market.py::_emotion` 不再出网。

四把尺各自住哪、为什么是这一把（都是现查出来的，不是推的）：

1. **封住 / 触板 / 连板：本地日线**，容差用 `app/compute/limit_prices.py::LIMIT_EPS`
   （半分，全项目唯一），与 `vr/firstboard.py::zt_count_from_db` 同一条口径。
   实测 2026-10-23：封住 84 只、触板 103 只（⇒ 炸板 19 只）、跌停 9 只（同一把半分容差、
   档位取 limit_down）、2 板及以上 13 家（梯队 2:10／3:1／5+:2）、最高 6 板。
   同一天东财四个池子给的是 82／94（82+12）／6／12 家 —— 家数差来自**判据不同**：
   本地含北交所与 ST（源站池 filter=HS,GEM2STAR 实测 0 只北证），
   东财炸板按开板事件数、本地按"收盘没封住的只数"。两侧数都上屏，见读侧的 caliber。
2. **首封 / 回封 / 开板次数：本地 1 分钟线**（第一根收盘仍在档位上的分钟）。
   实测 2026-10-23 那 84 只**全部**有 1 分钟行；与源站 first_limit_up_time 可比的 80 只里
   84% 落在 1 分钟内、中位差 +1.0 分（分钟收盘必然比秒级撮合晚一根）。
   ⚠️ 分钟线只从 **2026-08-14** 起（分区实测：202608/202609/202610 有行，更早为空），
   且 2026-10-20/10-21 两天整天没采到 ⇒ 这些场次这三列交 **NULL 并在 missing 里点名**，
   不拿源站值冒充自算（源站那一份另存在 src_* 列里，屏幕上那一格用的是哪把尺由
   `seal_basis` 交出）。
3. **封单额 / 封单量 / 板型 / 题材串 / 源站首封 / 源站开板次数：同花顺涨停揭秘整池**。
   这条路加工段 `fbdaily` 每天本来就要拉一遍（`app/compute/first_board_daily.py`），
   本模块**复用那一次出网**：由那一步把已经拉回的整池传进来，不重复请求。
   池子超出保留窗（实测约一年）或整场取不到 ⇒ 这一族全 NULL，`missing` 记明是哪一种；
   只有单独回算历史（`--with-source`）才会自己出网。
4. **流通市值**：优先源站 currency_value，没有（北证/ST 必然不在池里）就用当日日线反推
   `100 × 成交额 ÷ 换手率`（与 `analytics.sector_flow_trend` 的换手率分母同一条），
   用的是哪一把尺写进 `float_cap_basis` —— 封板强度里那个"封单占流通 %"吃这个分母，
   两把尺不标出来就等于同一列底下混两种口径。

三条不许破的纪律：
  · **缺数交 NULL，不写 0**：0 在这里是真读数（开板 0 次＝从头封到尾、封单 0 元＝真没人排队）。
  · **重算一场先清这一场这一版**（照 `first_board_daily.write` 那条）：母体会随分钟线到没到
    变多变更少，只 upsert 不清场，上一轮半截残留会一直挂在页面上。
  · **每一场的行必须全部来自那一场的日线**：不拿别日的封板时刻贴今日（源站那边由
    `fetch_limit_up_pool` 逐行核过日期，这一侧再核一次 `first_seal_at` 落在本场）。

用法：
    python -m app.compute.limit_up_pool --date 2026-10-23
    python -m app.compute.limit_up_pool --recent 45                      # 最近 45 场（含源站）
    python -m app.compute.limit_up_pool --since 2023-01-01 --no-source    # 全史只算本地那两把尺
    python -m app.compute.limit_up_pool --recent 3 --dry-run
    python -m app.compute.limit_up_pool --verify
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.db import engine                                       # noqa: E402
from app.compute.limit_prices import LIMIT_EPS                  # noqa: E402  全项目唯一涨停容差
from duanxian import historical_sources as hsrc                 # noqa: E402

TABLE = 'analytics.limit_up_pool_daily'
SPEC_VERSION = 'lup-1'

#: 连板回看多少个自然日：连续封住最多也就十几板（约 20 个交易日），150 个自然日留足余量，
#: 又不至于把一场全史回算的取数撑爆（跨片边界问题与 `first_board_daily` 的 90 天同源、更宽）。
LOOKBACK_NATURAL_DAYS = 150

#: 源站那一族的出处（与 `first_board_daily.REASON_SOURCE_POOL` 同一个端点、同一个字符串）
REASON_SOURCE = 'ths_limit_up_pool'

#: `missing` 的取值（键名＝列名；短码只在本模块出现一次，界面上那句话由读侧按这些码拼）
GAP_NO_MINUTE_DAY = 'minute_day_absent'        # 这一场整天没有分钟线（分区为空）
GAP_NO_MINUTE_ROW = 'minute_row_absent'        # 这场有分钟线，这只票一根都没有（全天停牌/新股）
GAP_SRC_NO_RECORDS = 'source_no_records'       # 源站这一场整场没有（超出保留窗 ⇒ 永久缺）
GAP_SRC_NOT_FETCHED = 'source_not_fetched'     # 这一跑按 `--no-source` 没去取（不是源站不收，重跑就有）
GAP_SRC_HTTP = 'source_http_error'             # 网络/解析失败（当场可重试，与过期不同）
GAP_SRC_ROW = 'source_row_absent'              # 源站有这一场但池里没这只票（北交所、ST 居多）
GAP_SRC_FIELD = 'source_field_null'            # 池里有这只票、这一栏它没给
GAP_TURNOVER_ZERO = 'turnover_null'            # 换手率缺或为 0 ⇒ 流通市值反推不出来

#: 屏幕上「涨停原因」那一族缺的时候，界面要念得出是哪一种，不是只念「—」
GAP_LABELS = {
    GAP_NO_MINUTE_DAY: '这一场没有分钟线数据（整天），首封／开板次数判不了',
    GAP_NO_MINUTE_ROW: '这一场有分钟线，但这只票一根都没有（多为全天停牌或当天才上市）',
    GAP_SRC_NO_RECORDS: '源站这一场整场没有（超出保留窗，补不回来）',
    GAP_SRC_NOT_FETCHED: '这一跑没去取源站（`--no-source`，例如全史回算）⇒ 这一族是空的，'
                         '不是「源站不收这只票」，重跑带 source 就有',
    GAP_SRC_HTTP: '源站这一场取数失败（当场可重试，与超出保留窗不是一件事）',
    GAP_SRC_ROW: '源站池里没有这只票（池只含沪深主板/创业板/科创板，不含北交所，也基本不含 ST）',
    GAP_SRC_FIELD: '源站池里有这只票，但这一栏它没给',
    GAP_TURNOVER_ZERO: '换手率缺或为 0，流通市值反推不出来',
}


# --------------------------------------------------------------------------- 纯判据

def consec_at(day: dt.date, sessions: Sequence[dt.date],
              sealed_dates: set) -> Tuple[Optional[int], Optional[dt.date]]:
    """连板数：从 `day` 往前**连续**封住的场次个数（含本场）与这一轮最早那一板的日子。

    `sessions` 是升序的**市场场次**（不是这只票自己的 K 线根数）：停牌那天没有它的行，
    按自己的行数往前数会把"隔了 4 场"说成"上一场"（与 `day_returns.series_by_code`
    那条同一个坑）。本场必须封住，否则没有"这一轮"⇒ `(None, None)`。
    """
    upto = [s for s in sessions if s <= day]
    if not sealed_dates or not upto or upto[-1] != day or day not in sealed_dates:
        return None, None
    n, start = 0, None
    for s in reversed(upto):
        if s in sealed_dates:
            n += 1
            start = s
        else:
            break
    return n or None, start


def seal_stats(bars: Sequence[Tuple[Any, Optional[float]]],
               limit_up: Optional[float], eps: float = LIMIT_EPS) -> Dict[str, Any]:
    """1 分钟线 → `{first_seal_at, last_seal_at, reopen_count, at_limit_minutes, minute_bars}`。

    `bars` 是升序的 `((钟表时间, close), ...)`。判据与日线同一条尺：这根分钟的收盘
    还在档位上（`close ≥ 档位 − eps`）就算"这一分钟封着"。

    三条：
      · 没有档位、或一根都没封住 ⇒ 全 None（**不是 09:25、也不是 0 次**）。
      · `reopen_count` 数的是"从封着掉下档位"的次数：先要封着才有得掉，所以首根之前
        没封过的那些分钟不计，`at_limit_minutes=0` 时它是 None 而不是 0。
      · `last_seal_at` 是最后一根还封着的分钟：一字板它与首封不同（尾盘仍封着），
        从没回封过的票它等于首封。
    """
    if not bars or limit_up is None:
        return {'first_seal_at': None, 'last_seal_at': None, 'reopen_count': None,
                'at_limit_minutes': None, 'minute_bars': len(bars or [])}
    at = [(t, c is not None and float(c) >= float(limit_up) - eps) for t, c in bars]
    sealed = [t for t, ok in at if ok]
    if not sealed:
        return {'first_seal_at': None, 'last_seal_at': None, 'reopen_count': None,
                'at_limit_minutes': 0, 'minute_bars': len(bars)}
    reopen = sum(1 for prev, cur in zip(at, at[1:]) if prev[1] and not cur[1])
    return {'first_seal_at': sealed[0], 'last_seal_at': sealed[-1], 'reopen_count': reopen,
            'at_limit_minutes': len(sealed), 'minute_bars': len(bars)}


def float_cap_of(raw: Optional[float], amount_yuan: Optional[float],
                 turnover_pct: Optional[float]) -> Tuple[Optional[float], Optional[str]]:
    """流通市值（元）与用的是哪把尺。源站原值优先，缺了才用当日日线反推。"""
    if raw is not None and float(raw) > 0:
        return float(raw), 'source'
    if amount_yuan and turnover_pct:
        return round(100.0 * float(amount_yuan) / float(turnover_pct), 2), 'daily_replay'
    return None, None


def hhmmss_of(clock: Optional[dt.time]) -> Optional[int]:
    """钟表时间 → 池子那套 HHMMSS 整数（封板强度 `first_seal_score` 吃的就是这一形状）。"""
    if clock is None:
        return None
    return clock.hour * 10000 + clock.minute * 100 + clock.second


# --------------------------------------------------------------------------- 取数

UNIVERSE_SQL = f"""
select sd.security_code, substr(sd.security_code, 1, 6) as code, sec.name,
       sd.limit_up::float          as limit_up_price,
       sd.close::float             as close,
       sd.pct_change::float        as pct_change,
       sd.turnover_rate_percent::float as turnover_pct,
       sd.amount_yuan::float       as amount_yuan,
       (sd.high >= sd.limit_up - %(eps)s)::bool  as touched,
       (abs(sd.close - sd.limit_up) <= %(eps)s)::bool as sealed
  from market_data.stock_daily sd
  join reference_data.security sec using (security_code)
 where sd.trade_date = %(d)s
   and sd.limit_up is not null
   and (sd.high >= sd.limit_up - %(eps)s or abs(sd.close - sd.limit_up) <= %(eps)s)
 order by sd.security_code
"""
# ⚠️ 母体条件写成 `触板 或 封住` 而不是只写触板：库里个别行有 high < close 的毛刺，
# 只按 high 判会把收盘封住的票漏掉 —— 漏掉的正是这一屏要看的对象。


def universe(day: dt.date) -> List[dict]:
    return engine.query(UNIVERSE_SQL, {'d': day, 'eps': LIMIT_EPS}, role='reader') or []


def sessions_between(a: dt.date, b: dt.date) -> List[dt.date]:
    rows = engine.query("select distinct trade_date from market_data.stock_daily "
                        "where trade_date between %(a)s and %(b)s order by 1",
                        {'a': a, 'b': b}, role='reader') or []
    return [r['trade_date'] for r in rows]


def sealed_pairs(a: dt.date, b: dt.date) -> Dict[str, set]:
    """`{全码: {封住的日子}}`，一次取回整段（逐场各问一次＝全史回算要问上千次）。"""
    rows = engine.query("select security_code, trade_date from market_data.stock_daily "
                        "where trade_date between %(a)s and %(b)s and limit_up is not null "
                        "and abs(close - limit_up) <= %(eps)s",
                        {'a': a, 'b': b, 'eps': LIMIT_EPS}, role='reader') or []
    out: Dict[str, set] = {}
    for r in rows:
        out.setdefault(r['security_code'], set()).add(r['trade_date'])
    return out


def minute_bars(day: dt.date, full_codes: Sequence[str]) -> Dict[str, List[Tuple[dt.time, Optional[float]]]]:
    """这一场母体那批票的 1 分钟线（钟表时间升序）。整场没有分钟线 ⇒ 空 dict。

    过滤按**全码**（`security_code = any(...)`）而不是截 6 位比对：后者用不上主键索引，
    实测一场要扫 228 万行、直接撞 ft_reader 的 60 秒 statement_timeout。
    """
    if not full_codes:
        return {}
    rows = engine.query("""select sm.security_code,
                                  (sm.dt at time zone 'Asia/Shanghai')::time as tm,
                                  sm.close::float as close
                             from market_data.stock_minute sm
                            where sm.period = 1 and sm.dt >= %(d)s and sm.dt < %(d)s + 1
                              and sm.security_code = any(%(codes)s::text[])
                            order by sm.security_code, sm.dt""",
                        {'d': day, 'codes': list(full_codes)}, role='reader') or []
    out: Dict[str, List[Tuple[dt.time, Optional[float]]]] = {}
    for r in rows:
        out.setdefault(str(r['security_code'])[:6], []).append((r['tm'], r['close']))
    return out


def source_pool(day: dt.date) -> Tuple[Dict[str, dict], Optional[str]]:
    """同花顺涨停揭秘整池。返回 `(池, 整场缺数原因)`：出网只在这一条路上发生。

    取数与四条闸都复用 `first_board_daily.fetch_limit_up_pool`（那份已经写了：逐行核
    封板时间戳落在本场、翻页数对上合计、一只都没有就是超出保留窗）。这里只把异常收成
    "整场缺数原因"，让本地那两把尺照写不误。
    """
    from app.compute import first_board_daily as FBD

    try:
        return FBD.fetch_limit_up_pool(day), None
    except FBD.SourceUnavailable as exc:
        return {}, (exc.code or 'source_unreadable')
    except Exception as exc:                              # noqa: BLE001
        return {}, f'source_unreadable:{type(exc).__name__}'


def _clock(value: Any, day: dt.date, miss: Dict[str, str], col: str,
           optional: bool = False) -> Optional[dt.time]:
    """源站 unix 秒 → 本场钟表时间；核不上日期就 NULL + 点名（**不兜成 00:00**）。"""
    from app.compute import first_board_daily as FBD

    if value is None:
        if not optional:
            miss[col] = GAP_SRC_FIELD
        return None
    try:
        landed, clock = FBD.stamp_to_clock(value, day, hsrc.TZ)
    except FBD.SourceUnavailable as exc:
        miss[col] = exc.code or 'source_unreadable'
        return None
    if landed != day:
        miss[col] = 'source_date_mismatch'
        return None
    return clock


# --------------------------------------------------------------------------- 组一行

def assemble(day: dt.date, base: List[dict], sessions: Sequence[dt.date],
             sealed_by_code: Dict[str, set], minutes: Dict[str, list],
             pool: Optional[Dict[str, dict]], src_gap: Optional[str]) -> List[dict]:
    """把三把尺拼成落库行。不碰库、不出网 ⇒ 单测可以直接喂假数据。"""
    has_minute_day = bool(minutes)
    now = dt.datetime.now(hsrc.TZ)
    rows: List[dict] = []
    for b in base:
        code = str(b['code'])
        full = str(b['security_code'])
        sealed = bool(b['sealed'])
        miss: Dict[str, str] = {}

        bands = sealed_by_code.get(full, set())
        n, start = consec_at(day, sessions, bands)
        if not sealed:
            n, start = None, None          # 收盘没封住就没有"这一轮连板"，不是 1 板

        bars = minutes.get(code) or []
        st = seal_stats(bars, b.get('limit_up_price'))
        first_local, reopen_local = st['first_seal_at'], st['reopen_count']
        if not bars:
            miss['first_seal_at'] = GAP_NO_MINUTE_DAY if not has_minute_day else GAP_NO_MINUTE_ROW
            miss['reopen_count'] = miss['first_seal_at']

        raw = (pool or {}).get(code)
        src = {'src_has_row': None, 'src_first_seal_at': None, 'src_last_seal_at': None,
               'src_reopen': None, 'src_high_days': None, 'order_amount': None,
               'order_volume': None, 'board_kind': None, 'reason': None,
               'reason_source': None, 'reason_captured_at': None}
        if raw is not None:
            src.update({
                'src_has_row': True,
                'src_first_seal_at': _clock(raw.get('first_limit_up_time'), day, miss,
                                            'src_first_seal_at'),
                'src_last_seal_at': _clock(raw.get('last_limit_up_time'), day, miss,
                                           'src_last_seal_at', optional=True),
                'src_reopen': raw.get('open_num'),
                'src_high_days': raw.get('high_days'),
                'order_amount': raw.get('order_amount'),
                'order_volume': raw.get('order_volume'),
                'board_kind': raw.get('limit_up_type'),
                'reason': (raw.get('reason_type') or '').strip() or None,
                'reason_source': REASON_SOURCE,
                'reason_captured_at': now,
            })
            if src['reason'] is None:
                miss['reason'] = 'source_reason_empty'
            # 池里有这只票、但某一栏它没给（实测池内 50 只里 27 只没给 open_num）：
            # 这一格是 null 并且**说得出为什么是 null**，不静默留空让读者猜"0 次"。
            for col in ('src_reopen', 'src_high_days', 'board_kind', 'order_amount', 'order_volume'):
                if src[col] is None:
                    miss[col] = GAP_SRC_FIELD
        else:
            src['src_has_row'] = False
            gap = src_gap or GAP_SRC_ROW
            for col in ('reason', 'src_first_seal_at', 'order_amount', 'src_reopen',
                        'board_kind', 'src_high_days'):
                miss[col] = gap

        cap, cap_basis = float_cap_of(raw.get('currency_value') if raw else None,
                                      b.get('amount_yuan'), b.get('turnover_pct'))
        if cap is None:
            miss['float_cap_yuan'] = GAP_TURNOVER_ZERO

        # 屏幕上"首封"那一格用的是哪把尺：自算优先，退回源站要标出来（两把尺不同天）
        if first_local is not None:
            seal_basis = 'local'
        elif src['src_first_seal_at'] is not None:
            seal_basis = 'source'
            miss['first_seal_at'] = 'local_minute_absent_use_source'
        else:
            seal_basis = 'none'

        rows.append({
            'security_code': full, 'trade_date': day, 'spec_version': SPEC_VERSION,
            'touched': bool(b['touched']), 'sealed': sealed,
            'limit_up_price': b.get('limit_up_price'), 'close': b.get('close'),
            'pct_change': b.get('pct_change'), 'turnover_pct': b.get('turnover_pct'),
            'amount_yuan': b.get('amount_yuan'),
            'consec': n, 'streak_start': start,
            'first_seal_at': first_local,
            'last_seal_at': st['last_seal_at'],
            'reopen_count': reopen_local,
            'at_limit_minutes': st['at_limit_minutes'],
            'minute_bars': st['minute_bars'],
            **src,
            'float_cap_yuan': cap, 'float_cap_basis': cap_basis,
            'seal_basis': seal_basis, 'missing': miss, 'name': b.get('name'),
        })
    return rows


def compute_day(day: dt.date, *, pool: Optional[Dict[str, dict]] = None,
                src_gap: Optional[str] = None, fetch_source: bool = True,
                pairs: Optional[Dict[str, set]] = None,
                sessions: Optional[Sequence[dt.date]] = None) -> Dict[str, Any]:
    """一场的行。`pool`/`src_gap` 由调用方交（链上那一步已经拉过池子就不再拉一遍）。"""
    base = universe(day)
    if pool is None and src_gap is None and fetch_source:
        pool, src_gap = source_pool(day)
    if pool is None and not fetch_source:
        # 没去取 与 取不到 是两种缺数：前者重跑就有，后者永久缺。标错一种，
        # 界面上那句"源站不收这只票"就成了假解释。
        src_gap = GAP_SRC_NOT_FETCHED
    codes = [str(b['security_code']) for b in base]
    if sessions is None or pairs is None:
        start = day - dt.timedelta(days=LOOKBACK_NATURAL_DAYS)
        sessions = sessions_between(start, day)
        pairs = sealed_pairs(start, day)
    minutes = minute_bars(day, codes)
    return {'rows': assemble(day, base, sessions or [], pairs or {}, minutes,
                             pool or {}, src_gap),
            'universe': len(base),
            'sealed': sum(1 for b in base if b['sealed']),
            'minute_codes': len(minutes),
            'src_rows': len(pool or {}), 'src_gap': src_gap}


# --------------------------------------------------------------------------- 落库

#: 列清单只有一个真值：INSERT 的列与占位符都从它生成
COLS = ('security_code', 'trade_date', 'spec_version',
        'touched', 'sealed', 'limit_up_price', 'close', 'pct_change', 'turnover_pct',
        'amount_yuan', 'consec', 'streak_start',
        'first_seal_at', 'last_seal_at', 'reopen_count', 'at_limit_minutes', 'minute_bars',
        'src_has_row', 'src_first_seal_at', 'src_last_seal_at', 'src_reopen', 'src_high_days',
        'order_amount', 'order_volume', 'board_kind', 'float_cap_yuan', 'float_cap_basis',
        'reason', 'reason_source', 'reason_captured_at',
        'seal_basis', 'missing')

_CASTS = {'missing'}

INSERT_SQL = (f"insert into {TABLE} ({', '.join(COLS)}) values ("
              + ', '.join(f"%({c})s::jsonb" if c in _CASTS else f"%({c})s" for c in COLS) + ')')


def clear_day(day: dt.date) -> int:
    with engine.cursor(role='etl') as cur:
        cur.execute(f"delete from {TABLE} where trade_date = %s and spec_version = %s",
                    (day, SPEC_VERSION))
        return cur.rowcount


def write(rows: List[Dict[str, Any]]) -> int:
    """重算一场先清这一场这一版，再整批写。`name` 不落库（读侧从 security 表 join）。"""
    if not rows:
        return 0
    days = sorted({r['trade_date'] for r in rows})
    with engine.cursor(role='etl') as cur:
        for day in days:
            cur.execute(f"delete from {TABLE} where trade_date = %s and spec_version = %s",
                        (day, SPEC_VERSION))
        payload = []
        for r in rows:
            p = {c: r.get(c) for c in COLS}
            p['missing'] = json.dumps(r.get('missing') or {}, ensure_ascii=False)
            payload.append(p)
        cur.executemany(INSERT_SQL, payload)
    return len(rows)


def land_day(day: dt.date, *, pool: Optional[Dict[str, dict]] = None,
             src_gap: Optional[str] = None, dry_run: bool = False) -> int:
    """给加工段用的单场落库：整池由调用方交（`fbdaily` 那一步已经拉过，**不再出第二遍网**）。

    返回写库行数（dry_run 时返回算出的行数，不碰库）。
    """
    out = compute_day(day, pool=pool, src_gap=src_gap, fetch_source=False)
    return len(out['rows']) if dry_run else write(out['rows'])


# --------------------------------------------------------------------------- 跑批

def latest_day() -> Optional[dt.date]:
    return engine.scalar("select max(trade_date) from market_data.stock_daily")


def trade_days(end: dt.date, recent: int) -> List[dt.date]:
    rows = engine.query("select trade_date from (select distinct trade_date from market_data.stock_daily "
                        "where trade_date <= %(e)s order by trade_date desc limit %(n)s) z order by 1",
                        {'e': end, 'n': int(recent)}, role='reader') or []
    return [r['trade_date'] for r in rows]


def days_between(a: dt.date, b: dt.date) -> List[dt.date]:
    return sessions_between(a, b)


def run(days: Iterable[dt.date], *, dry_run: bool = False,
        fetch_source: bool = True) -> Dict[str, Any]:
    """逐场算并写库。跨片只取一次封住集合与场次表（全史回算否则要问上千次）。"""
    days = list(days)
    if not days:
        return {'days': 0, 'rows': 0, 'written': 0}
    start = days[0] - dt.timedelta(days=LOOKBACK_NATURAL_DAYS)
    sessions = sessions_between(start, days[-1])
    pairs = sealed_pairs(start, days[-1])
    total = written = 0
    gaps: Dict[str, int] = {}
    for day in days:
        out = compute_day(day, fetch_source=fetch_source, pairs=pairs, sessions=sessions)
        rows = out['rows']
        n = 0 if dry_run else write(rows)
        if not dry_run and not rows:
            clear_day(day)
        total += len(rows)
        written += n
        if out['src_gap']:
            gaps[out['src_gap']] = gaps.get(out['src_gap'], 0) + 1
        print(f'  {day}：母体 {out["universe"]} 只（封住 {out["sealed"]}）｜'
              f'分钟线 {out["minute_codes"]} 只｜源站 {out["src_rows"]} 只'
              + (f'｜缺：{out["src_gap"]}' if out['src_gap'] else ''), flush=True)
    if gaps:
        print('  整场缺数原因计数：' + '、'.join(f'{k}×{v}' for k, v in sorted(gaps.items())),
              flush=True)
    return {'days': len(days), 'rows': total, 'written': written, 'gaps': gaps}


# --------------------------------------------------------------------------- 体检

def verify(days: Optional[Sequence[dt.date]] = None) -> List[str]:
    """体检（不打网络）。红的每一条都是"错了不报错、只给一句听着合理的读数"的地方。"""
    bad: List[str] = []
    r = engine.query_one(
        f"""select count(*) n, count(distinct trade_date) days, min(trade_date)::text a,
                   max(trade_date)::text z,
                   count(*) filter (where sealed) 封住,
                   count(*) filter (where touched and not sealed) 触板未封,
                   count(*) filter (where reason is not null) 有题材串,
                   count(*) filter (where first_seal_at is not null) 有自算首封,
                   count(*) filter (where order_amount is not null) 有封单额,
                   count(*) filter (where seal_basis='source') 首封退回源站,
                   count(*) filter (where float_cap_basis='daily_replay') 市值反推,
                   count(*) filter (where consec is not null and not sealed) 未封却有连板,
                   count(*) filter (where sealed and minute_bars > 0 and first_seal_at is null)
                     封住却没首封,
                   count(*) filter (where first_seal_at < time '09:15'
                                          or first_seal_at > time '15:00') 首封越界
          from {TABLE} where spec_version = %s""", (SPEC_VERSION,), role='reader')
    if not r or not r['n']:
        return [f'{TABLE} 一行都没有 —— 回填没跑，还是写库坏了？']
    print(f'  库里：{r["n"]} 行 / {r["days"]} 场（{r["a"]} … {r["z"]}）｜封住 {r["封住"]}、'
          f'触板未封 {r["触板未封"]}、题材串 {r["有题材串"]}、自算首封 {r["有自算首封"]}、'
          f'封单额 {r["有封单额"]} 行', flush=True)
    print(f'  首封退回源站 {r["首封退回源站"]} 行；流通市值走反推 {r["市值反推"]} 行', flush=True)

    if r['未封却有连板']:
        bad.append(f'{r["未封却有连板"]} 行 touched 未封却有 consec —— 收盘没封住就没有"这一轮"，'
                   f'这一格必须是 NULL')
    if r['封住却没首封']:
        bad.append(f'{r["封住却没首封"]} 行有分钟线、判成封住、却算不出首封时刻 —— 日线与分钟线'
                   f'两条尺在同一只票上打架（档位或容差分叉了），逐行查：'
                   f"select security_code, trade_date from {TABLE} where spec_version='{SPEC_VERSION}'"
                   f' and sealed and minute_bars > 0 and first_seal_at is null')
    if r['首封越界']:
        bad.append(f'{r["首封越界"]} 行首封时刻不在 09:15~15:00 之间 —— 时区换算错了')

    # 与日线独立重算对账：**只比这张表已经铺到的场次**（回填是按 --recent/--since 一批批铺的，
    # 拿全历史来比会把"还没铺到"比成"算错了"，一屏假红）。
    drift = engine.query(
        f"""with days as (select distinct trade_date d from {TABLE} where spec_version = %(v)s),
                t as (select trade_date d, count(*) n from {TABLE}
                       where spec_version = %(v)s and sealed group by 1),
                x as (select sd.trade_date d, count(*) n from market_data.stock_daily sd
                       where sd.limit_up is not null and abs(sd.close - sd.limit_up) <= %(eps)s
                       group by 1)
           select days.d::text d, coalesce(t.n,0) 落库, coalesce(x.n,0) 现算
             from days left join t on t.d = days.d left join x on x.d = days.d
            where coalesce(t.n,0) <> coalesce(x.n,0)
            order by 1 desc limit 15""",
        {'v': SPEC_VERSION, 'eps': LIMIT_EPS}, role='reader') or []
    if drift:
        bad.append('落库封住数与日线现算数不一致的场次（前 15 场）：'
                   + '、'.join(f'{m["d"]}（落库 {m["落库"]} vs 现算 {m["现算"]}）' for m in drift))
    if days:
        print(f'  （本轮跑过 {days[0]} … {days[-1]}，共 {len(days)} 场）', flush=True)
    return bad


# --------------------------------------------------------------------------- CLI

def _days_from_args(args: argparse.Namespace) -> List[dt.date]:
    end = (dt.date.fromisoformat(args.date) if args.date else latest_day())
    if end is None:
        return []
    if args.since:
        return days_between(dt.date.fromisoformat(args.since), end)
    if args.recent:
        return trade_days(end, args.recent)
    return [end]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description='首页「涨停与连板」母体落库')
    ap.add_argument('--date', help='只算这一场 YYYY-MM-DD')
    ap.add_argument('--recent', type=int, default=1, help='最近 N 场（默认 1）')
    ap.add_argument('--since', help='从这一天到库里最新一场')
    ap.add_argument('--dry-run', action='store_true', help='只算不写')
    ap.add_argument('--no-source', action='store_true',
                    help='不出网取源站那一族（全史回算用这个，源站列一律 NULL）')
    ap.add_argument('--verify', action='store_true', help='体检（不打网络）')
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.verify:
        bad = verify()
        for m in bad:
            print('  ✗ ' + m)
        print('  体检通过' if not bad else f'  体检红 {len(bad)} 条')
        return 0 if not bad else 1

    days = _days_from_args(args)
    if not days:
        print('库里没有日线场次可算（stock_daily 是空的？）')
        return 1
    print(f'跑 {len(days)} 场：{days[0]} … {days[-1]}'
          + ('（dry-run，不写库）' if args.dry_run else '')
          + ('（不出网，源站那一族交 NULL）' if args.no_source else ''), flush=True)
    out = run(days, dry_run=args.dry_run, fetch_source=not args.no_source)
    print(f'  共 {out["rows"]} 行，写库 {out["written"]} 行', flush=True)
    if args.dry_run:
        print('  （dry-run：没有写库，所以不做体检）', flush=True)
        return 0
    bad = verify(days)
    for m in bad:
        print('  ✗ ' + m)
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
