# -*- coding: utf-8 -*-
"""「跌停分析」的读侧：母体＝本地日线 `market_data.stock_daily`，不出网、不建新表。

为什么跌停这一侧没有对应的库表：`analytics.limit_up_pool_daily`（lup-1）**整张只有涨停方向**
（列是 `limit_up_price/touched/sealed/consec/first_seal_at/order_amount/reason`），
写侧也没算过跌停行。而跌停侧唯一的多源出处是同花顺揭秘池／东财跌停池，本机都没采。
⇒ 这一版只摆**日线算得出的列**：封住、连续封住场次、收盘价、跌幅、成交额、换手、流通市值、概念。
封单额、首次封板时刻、编辑归因那三族在跌停侧本机没有数据，**整列不出现**（不是摆 `—` 占位）。

三条纪律（与涨停那一侧同一份）：
  · 判"封住"用的是全项目那半分容差 `limit_prices.LIMIT_EPS`，与首页短线情绪那格「跌停」家数
    **同一个数**（那一处现在也调本模块，实现只有一份）。
  · 库里那一天没有日线行 ⇒ `available=False` 并说清该跑哪一步，不拿别的日子顶。
  · NULL 一路保持 NULL（界面 `—`），不折成 0：0 在跌幅那一格是真读数。
"""
from __future__ import annotations

from app.compute.limit_prices import LIMIT_EPS
from . import pg
from .limit_up_board import _num                         # NaN/NaT 出口收口全站一份（不写第二个 _num）

#: 「连续跌停」回溯多少场：档位最多四档，30 场的底足够（与几天几板同一份窗口常量）
WINDOW_SESSIONS = 30

CALIBER_NOTE = (
    "跌停母体自 2026-10-29 起按本地日线判据算：收盘价 ≤ 当日跌停价（档位取 stock_daily 的 "
    "limit_down/limit_basis，容差与全项目涨停价口径同一份半分）。这一侧没有库表、也没有分钟线"
    "首封与封单额，所以那一族整列不出现 —— 不是「今天没有」，是本机没有这个读数。"
    "触及＝当日最低价踩到档位；打开＝触及而收盘没封住，与「炸板」在涨停侧是同一形状。")

#: 「连续跌停」那一列的口径文字（列头悬停显示的那一段，只在这一份里）
STREAK_RULE = (
    "「连续跌停」= 从今天往前紧挨着数，收盘仍封在跌停档上的场次（含本场）。断一场即归 1 —— "
    "与「几天几跌停」那一列不是一回事：那一把允许中间空一场仍算同一轮。")


def sealed_count(day: str) -> "int | None":
    """这一场收盘封住跌停的只数（首页短线情绪与本屏共用这一个数，实现只在这一处）。

    读不到库 ⇒ `None`（没读到 ≠ 今天没有跌停）。
    """
    if not day or not pg.enabled():
        return None
    r = pg.fetch_df("select count(*)::int n from market_data.stock_daily "
                    "where trade_date = %s::date and limit_down is not null "
                    "and close::numeric <= limit_down::numeric + %s::numeric", (day, LIMIT_EPS))
    if r is None or not len(r):
        return None
    return int(r['n'][0])


TOUCHED_SQL = """
with t as (
  select substr(sd.security_code, 1, 6)          as code,
         sd.security_code                         as full_code,
         sd.close::float                          as close,
         sd.pct_change::float                     as pct,
         sd.amount_yuan::float                    as amount,
         sd.turnover_rate_percent::float          as turnover,
         sd.limit_down::float                     as limit_down,
         sd.limit_basis,
         (sd.close::numeric <= sd.limit_down::numeric + %s::numeric) as sealed,
         (sd.low::numeric  <= sd.limit_down::numeric + %s::numeric)  as touched
    from market_data.stock_daily sd
   where sd.trade_date = %s::date and sd.limit_down is not null
)
select t.*, coalesce(sec.name, t.code)           as name,
       sec.board, sec.is_st,
       sn.float_market_value_yuan::float         as float_cap
  from t
  left join reference_data.security sec on sec.security_code = t.full_code
  left join market_data.stock_snapshot sn
         on sn.security_code = t.full_code and sn.trade_date = %s::date
 where t.touched
 order by t.pct asc, t.code
"""

#: 那一天在 stock_daily 里到底有没有行（判"没数"与"读坏了"要分开）
DAY_ROW_SQL = "select count(*)::int n, count(*) filter (where limit_down is null)::int no_ld " \
              "from market_data.stock_daily where trade_date = %s::date"

#: 上一场封住跌停的只数（续跌率的分母，读同一张表的上一场）
PREV_SQL = """
with prev as (select trade_date from market_data.stock_daily
               where trade_date < %s::date group by 1 order by 1 desc limit 1)
select prev.trade_date::text as d,
       (select count(*)::int from market_data.stock_daily sd
         where sd.trade_date = prev.trade_date and sd.limit_down is not null
           and sd.close::numeric <= sd.limit_down::numeric + %s::numeric) as n
  from prev
"""

#: 这张表能读到哪一天为止（界面要说"最早哪天有数"）
COVERAGE_SQL = ("select to_char(min(trade_date),'YYYY-MM-DD') as first_day, "
                "to_char(max(trade_date),'YYYY-MM-DD') as last_day, count(distinct trade_date)::int as sessions "
                "from market_data.stock_daily")


def board_on(day: str) -> dict:
    """这一场的跌停整块读数。库里没有那一天的日线就 `available=False` 并说清该跑哪一步。"""
    if not day or not pg.enabled():
        return {'available': False, 'reason': '本地库没连上（PG 未配置或读不到），这一屏不回退在线池子'}
    df = pg.fetch_df(DAY_ROW_SQL, (day,))
    if df is None:
        return {'available': False, 'reason': '库里这一场读取出错（不是没有数）'}
    rec = df.to_dict('records')[0]
    day_rows = int(rec['n'] or 0)
    if not day_rows:
        return {'available': False, 'date': day,
                'reason': f'market_data.stock_daily 里没有 {day} 这一场（{day_rows} 行）—— '
                          '这一屏读的是本地日线，该跑的是收盘链的日线入库与加工那一步，'
                          '不是"今天没有跌停"。'}

    df = pg.fetch_df(TOUCHED_SQL, (LIMIT_EPS, LIMIT_EPS, day, day))
    if df is None:
        return {'available': False, 'date': day, 'reason': '跌停行读取出错（不是没有数）'}
    touched_rows = [{k: _num(v) for k, v in r.items()} for r in df.to_dict('records')]
    sealed = [r for r in touched_rows if r['sealed']]
    opened = [r for r in touched_rows if not r['sealed']]

    codes = [r['code'] for r in sealed]
    # 「连续跌停」用半分容差（与上面数出来的家数同一把尺），「几天几跌停」用 board_span 那把更严的
    # 0.001 —— 两把尺在同一只票上会分叉，分叉要点名，不许静默按"没连"处理。
    from . import board_span
    st = board_span.streak_by_code(codes, day, WINDOW_SESSIONS, 'down', LIMIT_EPS)
    bs = board_span.boards_by_code(codes, day, 'down') if codes else {}
    for r in sealed:
        g = st.get(str(r['code'])[:6]) or {}
        r['streak'] = g.get('streak')
        r['streak_bars'] = g.get('bars')
        b = bs.get(str(r['code'])[:6]) or {}
        r['days_boards'] = b.get('label')
        r['board_days'] = b.get('days')
        r['board_ct'] = b.get('boards')
        r['board_dates'] = b.get('sealed_dates')
    streak_warn = ''
    if codes and not st:
        streak_warn = ('「连续跌停」整列没读到：这批票的回溯窗口日线取不回来（库不通或那几场没行）—— '
                       '空着不等于它们都是首跌，档位筛选这一屏会筛不出东西。')

    diverging = [f"{r['name']}({r['code']})" for r in sealed
                 if r.get('streak') and not r.get('board_ct')]
    span_warn = ''
    if diverging:
        span_warn = ('这几天几跌停没数出来：' + '、'.join(diverging[:6])
                     + ('…' if len(diverging) > 6 else '')
                     + ' —— 半分那把尺判它今天封住了，按「几天几跌停」那把更严的容差却没算进这一轮。'
                       '别把它读成"没有跌停板"。')
    elif not bs and codes:
        span_warn = '本地日线读不到（库没连或这场没数）：这一列整列空着，不是这批票都没连过跌停。'

    from . import pg_sources
    concepts = pg_sources.concepts_by_code(codes)
    for r in sealed:
        con = concepts.get(str(r['code'])) or {}
        r['industry'] = con.get('concepts')
        r['concept_count'] = con.get('concept_count')

    prev_d = prev_n = None
    pdf = pg.fetch_df(PREV_SQL, (day, LIMIT_EPS))
    if pdf is not None and len(pdf):
        prev_d = _num(pdf['d'][0])
        prev_n = int(pdf['n'][0] or 0)
    streak_ge2 = [r for r in sealed if (r.get('streak') or 0) >= 2]
    # 缺市值的只数：流通市值来自 stock_snapshot（另一条采集链），那一格缺了要说得出是谁缺
    no_cap = [r for r in sealed if r.get('float_cap') is None]

    cdf = pg.fetch_df(COVERAGE_SQL, ())
    coverage = ({k: _num(v) for k, v in cdf.to_dict('records')[0].items()}
                if cdf is not None and len(cdf) else None)

    return {
        'available': True,
        'date': day,
        'dt_count': len(sealed),
        'touched_count': len(touched_rows),
        'opened_count': len(opened),
        'seal_rate': round(len(sealed) / len(touched_rows), 3) if touched_rows else None,
        'open_rate': round(len(opened) / len(touched_rows), 3) if touched_rows else None,
        'prev_date': prev_d, 'prev_sealed': prev_n,
        'streak_ge2_count': len(streak_ge2),
        'continuation_rate': round(len(streak_ge2) / prev_n, 3) if prev_n else None,
        'no_limit_rows': int(rec['no_ld'] or 0),
        'rows': sealed,
        'opened_rows': opened,
        'coverage': coverage,
        'caliber': CALIBER_NOTE,
        'streak_rule': STREAK_RULE,
        'streak_warn': streak_warn,
        'days_boards_rule': board_span.rule_note('down'),
        'days_boards_warn': span_warn,
        'float_cap_missing': len(no_cap),
        'window_sessions': WINDOW_SESSIONS,
        'eps': LIMIT_EPS,
    }
