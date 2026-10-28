# -*- coding: utf-8 -*-
"""首页「短线情绪 · 涨停与连板」的读侧：只读 `analytics.limit_up_pool_daily`，不出网。

2026-10-26 之前这一块的母体是**每次请求现抓东财 push2ex 四个池子**（`vr/market.py::_emotion`）。
现在母体在库里（写侧 `app/compute/limit_up_pool.py`，判据只住那一处），这一份只做三件事：

1. 把落库行读成 `_emotion` 要的形状（封住清单、炸板只数、跌停只数、昨日封住只数）；
2. **把口径差当面说出来**：家数与比率从 2026-10-26 起按本地日线判据，与东财池会差几只到十几只，
   差的来处在 `CALIBER_NOTE` 里写死（含实测数字），不靠"看着差不多"糊过去；3. **缺数说清是哪一种缺**：这一场没有分钟线（10-20/10-21 那种）、源站超出保留窗、
   库里这一场根本还没加工 —— 三种是不同的话，也对应不同的下一步。

三条纪律：
  · 读侧**不重算判据**：连板数、首封、封单额一律取落库原值，界面上那一格用的是哪把尺读
    `seal_basis`（这份只把它转成人话，不自己判）。
  · 库里没有这一场的行 ⇒ `available=False` 并说清该跑哪一步，**不回退去抓在线池子顶数**：
    回退就意味着这一屏随时可能因为加工没跑而"看着有数、其实是别的地方的数"。
  · NULL 一路保持 NULL（`—`），不折成 0：0 在"开板次数""封单额"这些位置是真读数。
"""
from __future__ import annotations

import datetime as dt
import json

from app.compute.limit_up_pool import SPEC_VERSION       # 判据版本只有一份：写侧升号读侧跟着走
from app.compute.limit_up_pool import GAP_LABELS
from . import pg

#: 这张表的场次区间（一次查询带到 payload 里，界面才说得出"最早哪天有数"）
TABLE = 'analytics.limit_up_pool_daily'

#: 口径说明（屏幕上那段悬停/折叠文字的唯一来源）。数字是 2026-10-26 现查 2026-10-23 那一场：
#: 本地日线判出封住 84／触板未封 19／跌停 11／2 板以上 13 家，东财四个池子给 82／12／6／12 家。
CALIBER_NOTE = (
    "家数、梯队、封板率、炸板率、晋级率从 2026-10-26 起按本地日线判据算（收盘与当日涨停"
    "档位相差半分以内算封住，跌停同尺，容差与全项目涨停价口径同一份），不再每次请求现抓东财池。"
    "与东财池的差是判据不同，不是谁算错：本地含北交所与 ST（源站池只含沪深主板/创业板/科创板），"
    "炸板按「收盘没封住的只数」算、东财按「开板事件」算。同一场 2026-10-23 实测两侧："
    "封住 84 对 82、炸板 19 对 12、跌停 9 对 6、2 板以上 13 家对 12 家。")

SEAL_TIME_RULE = (
    "「首封」这一列自 2026-10-26 起是本地 1 分钟线自己数出来的：第一根收盘仍封在当日涨停"
    "档位上的分钟。与源站秒级首封实测可比 80 只里 84%% 落在 1 分钟内（分钟收盘必然比秒级撮合"
    "晚一根）。09:31 那批是集合竞价一字 —— 分钟线的第一根就在 09:30/09:31，所以「早」不等于"
    "早盘承接强。这一场没有分钟线时（2026-08-14 之前、以及 10-20/10-21 那两天）退回源站秒级值"
    "并在格子里标注，两边都没有就显示 —。")

#: `missing` 里那几个短码在屏幕上怎么念（写侧只出短码，措辞只在这一处）
GAP_TEXT = GAP_LABELS

ROWS_SQL = f"""
select substr(p.security_code, 1, 6)                    as code,
       p.security_code,
       coalesce(sec.name, substr(p.security_code, 1, 6)) as name,
       sec.board, sec.market,
       p.sealed, p.touched, p.consec, p.streak_start::text as streak_start,
       p.close::float        as close,
       p.pct_change::float   as pct,
       p.amount_yuan::float  as amount,
       p.float_cap_yuan::float as float_cap,
       p.float_cap_basis, p.order_amount::float as order_amount,
       p.reason, p.board_kind,
       to_char(p.first_seal_at,      'HH24:MI')          as seal_time,
       to_char(p.first_seal_at,      'HH24MISS')::int    as seal_raw,
       to_char(p.src_first_seal_at,  'HH24:MI')          as src_seal_time,
       to_char(p.src_first_seal_at,  'HH24MISS')::int    as src_seal_raw,
       p.seal_basis, p.reopen_count, p.src_reopen, p.at_limit_minutes, p.minute_bars,
       -- 批次时刻跟着行交出：这一屏看到的不是"此刻的库"，是"这一批算完的库"
       p.calculated_at::text                            as calculated_at,
       p.missing::text                                    as missing
  from {TABLE} p
  join reference_data.security sec using (security_code)
 where p.spec_version = %s and p.trade_date = %s::date
 order by p.consec desc nulls last, p.first_seal_at nulls last, code
"""


def _num(v):
    """出口收口：pandas 把 SQL 里的 NULL 读成 **float NaN**（列里只要有一个 NULL，整列就升成 float），
    而 `NaN is not None` 恒真 ⇒ 不收口的话 NaN 会一路跟着 `json.dumps` 出去，
    产出非法的 `NaN` 字面量、浏览器 `JSON.parse` 直接炸。日期同理（`pd.NaT` 是 datetime 实例）。

    numpy 标量先 `.item()` 换成 Python 原生值：`int64` 不是 `int` 的子类，不换的话
    名次/次数会被 `float()` 兜成 2.0 这种带小数点的样子（屏幕上"2.0 板"）。
    """
    if hasattr(v, 'item'):
        v = v.item()
    if v is None:
        return None
    try:
        if v != v:                       # NaN / NaT
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, str):               # 代码、时刻串、板型：本来就是文字，不许被 float 兜走
        return v
    if isinstance(v, (dt.date, dt.datetime)):
        return str(v)
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)                  # Decimal 等数值
    except (TypeError, ValueError):
        return str(v)


#: 这些列在库里是整数，但同列里只要有一个 NULL，pandas 就把整列升成 float ⇒ 屏幕上"6.0 板"。
#: 出口统一收回到 int（None 保持 None，不兜 0）。
INT_COLS = ('consec', 'reopen_count', 'src_reopen', 'at_limit_minutes', 'minute_bars',
            'seal_raw', 'src_seal_raw')


def _as_int(v):
    return None if v is None else int(v)


def _rows(sql, params):
    df = pg.fetch_df(sql, params)
    if df is None:
        return None
    out = []
    for r in df.to_dict('records'):
        row = {k: _num(v) for k, v in r.items()}
        for c in INT_COLS:
            row[c] = _as_int(row.get(c))
        row['missing'] = _load_missing(row.get('missing'))
        out.append(row)
    return out


def _load_missing(text) -> dict:
    """`missing` 在库里是 jsonb，过 pandas 出来是一段 JSON 文本。坏成什么样都只当"没记"，
    不把整块读数带崩 —— 这一列只用来解释"这一格为什么是 —"。"""
    if not text:
        return {}
    try:
        got = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def coverage() -> dict:
    """这张表铺到哪、分钟线那一族从哪天起有数、源站那一族铺到哪。一次查询全交。"""
    df = pg.fetch_df(
        f"""select min(trade_date)::text as from_day, max(trade_date)::text as to_day,
                   count(distinct trade_date)::int as sessions, count(*)::int as rows_total,
                   (select min(trade_date)::text from {TABLE} p
                     where p.spec_version = %s and p.first_seal_at is not null) as minute_from,
                   (select max(trade_date)::text from {TABLE} p
                     where p.spec_version = %s and p.first_seal_at is not null) as minute_to,
                   (select min(trade_date)::text from {TABLE} p
                     where p.spec_version = %s and p.reason is not null) as src_from,
                   (select max(trade_date)::text from {TABLE} p
                     where p.spec_version = %s and p.reason is not null) as src_to
            from {TABLE} where spec_version = %s""",
        (SPEC_VERSION,) * 5)
    if df is None or not len(df):
        return {}
    return {k: _num(v) for k, v in df.to_dict('records')[0].items()}


def prev_sealed_count(day: str) -> "tuple[str | None, int | None]":
    """上一场的封住只数（晋级率的分母）—— 读**这张表**的上一场，不读源站昨涨停池。

    返回 `(那一场是哪天, 几只)`。库里这一场没行 ⇒ `(None, None)`：分母没有就不给晋级率，
    **不拿"昨日涨停池"那种在线数顶上**（顶上来的就是两套口径混在一个比率里）。
    """
    r = pg.fetch_df(
        f"""select trade_date::text d, count(*) n from {TABLE}
             where spec_version = %s and sealed and trade_date < %s::date
             group by 1 order by 1 desc limit 1""", (SPEC_VERSION, day))
    if r is None or not len(r):
        return None, None
    return str(r['d'][0]), int(r['n'][0])


def dt_count(day: str) -> "int | None":
    """当日跌停只数（同一条半分容差，档位取 limit_down）。判据与写侧同源，这里只数一行。"""
    from app.compute.limit_prices import LIMIT_EPS

    r = pg.fetch_df("select count(*)::int n from market_data.stock_daily "
                    "where trade_date = %s::date and limit_down is not null "
                    "and abs(close - limit_down) <= %s", (day, LIMIT_EPS))
    if r is None or not len(r):
        return None
    return int(r['n'][0])


def board_on(day: str) -> dict:
    """这一场的整块读数。库里没行就 `available=False` 并说清该跑哪一步。"""
    if not day or not pg.enabled():
        return {'available': False, 'reason': '本地库没连上（PG 未配置或读不到），'
                                              '这一块不回退在线池子',
                'spec_version': SPEC_VERSION}
    rows = _rows(ROWS_SQL, (SPEC_VERSION, day))
    if rows is None:
        return {'available': False, 'reason': '库里这一场读取出错（不是没有数）',
                'spec_version': SPEC_VERSION}
    if not rows:
        return {'available': False, 'spec_version': SPEC_VERSION,
                'reason': f'analytics.limit_up_pool_daily 里没有 {day} 这一场的行 —— '
                          '这一块的母体改成落库后就不再现抓在线池子了，加工段没跑就没有数。'
                          '补跑：python -m app.compute.limit_up_pool --date '
                          f'{day}（或加工段 fbdaily 那一步）'}
    sealed = [r for r in rows if r['sealed']]
    broken = [r for r in rows if r['touched'] and not r['sealed']]
    prev_day, prev_n = prev_sealed_count(day)
    # 退回源站首封的那些行要点名到场次级：整个窗口没有分钟线（10-20 那种）不是每只票都缺
    backfilled = [r for r in rows if r['seal_basis'] == 'source']
    none_seal = [r for r in rows if r['seal_basis'] == 'none']
    # 缺数按"几只票缺这一族"数，不是按"几格缺"数：同一只票的题材串与封单额一起缺，
    # 念成两件事会把 23 只说成 138 处（分母冒充是这一族缺陷的老毛病）。
    gaps = {}
    for r in rows:
        for code in set((r['missing'] or {}).values()):
            gaps[code] = gaps.get(code, 0) + 1
    return {
        'available': True, 'date': day, 'spec_version': SPEC_VERSION,
        'rows': rows, 'sealed': sealed, 'broken': broken,
        'rows_total': len(rows), 'source': 'pg',
        'sealed_count': len(sealed), 'broken_count': len(broken),
        'dt_count': dt_count(day),
        'prev_date': prev_day, 'prev_sealed': prev_n,
        'lianban': [r for r in sealed if (r['consec'] or 0) >= 2],
        'seal_backfilled': len(backfilled), 'seal_none': len(none_seal),
        'gap_counts': gaps,
        'coverage': coverage(),
        'caliber': CALIBER_NOTE, 'seal_rule': SEAL_TIME_RULE,
        'gap_texts': GAP_TEXT,
    }
