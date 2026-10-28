# -*- coding: utf-8 -*-
"""「当日涨幅 / 15 日涨幅」：倍量榜末尾那两列的判据（用户 2026-10-10 点名）。

两条不是随手定的：

1. **两列都用后复权价** `close × adjustment_factor`（因子的含义写在 `app/db/repo/quote.py:444`：
   「`close × adjustment_factor` 是后复权价，跨除权日连续」）。**不读 `stock_daily.pct_change`** ——
   那一列是未复权的原始比值：实测 000155 在 2026-09-22 因子从 0.97817 跳到 1.0（那天除息），
   `pct_change` 记的是 −2.77%（= 11.58/11.91−1），而按复权价算是 −0.60%（= 11.58/(11.91×0.97817)−1），
   后者才和行情软件一致。既然 15 日那列必须复权（不然整段的涨跌里混着一次派息），
   今日这列就用同一把尺子，**两列同尺才能对着看**。
2. **「15 个交易日」按市场场次算，不是按这只票自己的 K 线根数**：窗口取目标日往前 16 场
   （含目标日），基期是**第 16 场那一日**。停牌过的票如果基期那天没有行，就交 `None` ——
   拿更早的一天冒充基期会把「15 日涨幅」说成「23 日涨幅」，而格子里看不出区别。
   同理目标日缺行 ⇒ 两列都空。`bars`（窗口里真有行的场数）一起交出，界面把它摆在悬停里，
   读者能看出这票这一段停过几次牌。
"""
from __future__ import annotations

#: 跨度（个交易日）。15 日涨幅要 16 场：目标日 + 往前 15 场。
SPAN = 15

RULE_NOTE = (
    "「当日涨幅」「N 日涨幅」两列同尺：都用后复权价（收盘价 × adjustment_factor，"
    "跨除权日连续），不是本地日线里那列未复权的 pct_change。"
    "「当日」= 本页基准交易日那一场（不是此刻的实时行情）；"
    "N 日 = 基准日 ÷ 往前数第 N 个交易日那一场 − 1（窗口按市场场次数，不是这只票自己的 K 线根数）。"
    "除息日那一格会和行情软件的原始涨跌幅对不上 —— 那个差就是派息，复权口径才是对的"
    "（实测 000155 在 2026-09-22：未复权 −2.77%／复权 −0.60%）。"
    "空着（—）= 基期或基准日那天它没有行（停牌/未上市），不是 0%（平盘）；"
    "悬停给出窗口里真有行情的场数。")


def _num(v) -> "float | None":
    """pandas 把 SQL 里的 NULL 读成 **float NaN**（列里只要有一个 NULL，整列就升成 float），
    而 `NaN is not None` 恒真 ⇒ 不收口的话 NaN 会一路跟着 `json.dumps` 出去，
    产出非法的 `NaN` 字面量、浏览器 `JSON.parse` 直接炸（10-10 这条在实跑里真发生了）。"""
    try:
        f = None if v is None else float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _txt(v) -> "str | None":
    """文字列同样是 NaN 的入口：一列里既有字符串又有 NULL 时 pandas 用 StringDtype，
    缺的那格交回来是 float NaN 而不是 None（10-10 在 `sentiment_on_dates` 上踩过一次）。"""
    return None if v is None or v != v else str(v)


def _pct(last, base) -> "float | None":
    """涨幅（%）。缺任何一边、或基期为 0 → None，**不返回 0**（0 会被读成"平盘"）。"""
    last, base = _num(last), _num(base)
    if last is None or base is None or not base:
        return None
    return round((last / base - 1) * 100.0, 2)


def returns_by_code(codes, target_date: str, span: int = SPAN) -> dict:
    """`{全码: {chg_today, chg_span, bars, base_day}}`；库不通或没数交回空 dict。

    空 dict 与"这只票算不出涨幅"是两件事，调用方必须分开说（同 `board_span.boards_by_code`）。
    """
    from . import pg

    pool = sorted({str(c).strip() for c in (codes or []) if c})
    if not pool or not pg.enabled():
        return {}
    need = int(span) + 1
    df = pg.fetch_df(
        """
        with d as (select distinct trade_date from market_data.stock_daily
                    where trade_date <= %s::date order by trade_date desc limit %s),
        w as (select trade_date, row_number() over (order by trade_date desc) as back from d),
        s as (select sd.security_code, w.back, w.trade_date,
                     (sd.close * coalesce(sd.adjustment_factor, 1))::float as hfq
                from market_data.stock_daily sd join w on w.trade_date = sd.trade_date
               where sd.security_code = any(%s::text[]) and sd.close is not null)
        select security_code,
               max(hfq) filter (where back = 1)   as h_last,
               max(hfq) filter (where back = 2)   as h_prev,
               max(hfq) filter (where back = %s)  as h_base,
               max(trade_date) filter (where back = %s)::text as base_day,
               count(*)::int as bars
          from s group by 1
        """, (str(target_date), need, pool, need, need))
    if df is None:
        return {}
    out = {}
    for r in df.to_dict('records'):
        last = _num(r.get('h_last'))
        out[str(r['security_code'])] = {
            'chg_today': _pct(last, r.get('h_prev')),
            'chg_span': _pct(last, r.get('h_base')),
            'bars': None if _num(r.get('bars')) is None else int(_num(r.get('bars'))),
            'base_day': _txt(r.get('base_day')),
            'has_row_on_target': last is not None}
    return out


#: 起算日前要多取几场：基期是「起算日的前一场」，那一行必须落在取数范围内。
BASE_PAD = 10


def series_by_code(spans: dict, target_date: str) -> dict:
    """`{全码: {'days': [{day_no, trade_date, pct, cum}], 'base_day', 'missing_base'}}`。

    给「加权分进榜后的逐日涨跌幅」那一块用（`quant.energy_track`）：每只票的起算日各不相同，
    入参是 `{code: 起算日}`，一次取回所有票从各自起算日到目标日的逐日涨幅（一条 SQL，
    不是一只票一问）。

    **尺子与 `returns_by_code` 同一条**（后复权、按市场场次找上一场），三条纪律照搬：
    ① 某场它没有行（停牌）⇒ 那一格 `pct=None`。做法是先给全市场场次编号 `sr`，
       只有「上一行的 sr ＝ 本行 sr − 1」时才认这个前值；只按这只票自己的行 `lag`
       会把"隔了 7 场"的涨幅说成"上一场"的涨幅（10-19 写这段时最容易做错的一处）。
    ② 空就是空，界面显示 `—`，不是 0%（0% 会被读成"平盘"）。
    ③ 起算日前一场取不到（那天才上市）⇒ `missing_base=True`，`cum` 全交空：
       没有基期就没有「整体涨跌幅」，不许拿起算日自己当基期（那会恒等于 0%，一条假平盘）。
    """
    from . import pg

    pool = {str(c).strip(): str(d) for c, d in (spans or {}).items() if c and d}
    if not pool or not pg.enabled():
        return {}
    # 日期上下界必须当**字面参数**递进去。原先它住在 CTE 里（`(select d from lo)`），
    # `market_data.stock_daily` 是按年分区的表 ⇒ 规划器剪不掉分区，2015..2026 每一片都要
    # 全表扫一遍（10-26 实测 120 只 × 近一年：1,516 毫秒，扫到 12 个分区）。
    # 先单独一发取 lo（44 毫秒），主查询再按字面上下界走：同一批行 390 毫秒、只碰 2026 那一片，
    # 行数与 hfq 合计逐位不变（20,990 行、359,324.0611）—— 省的全在 `w` 那一段。
    seed = min(pool.values())          # ISO 日期串按字典序比＝按时间比
    lo_df = pg.fetch_df(
        """select coalesce(min(trade_date), %s::date)::text as d from (
             select distinct trade_date from market_data.stock_daily
              where trade_date <= %s::date
              order by trade_date desc limit %s) z""", (seed, seed, BASE_PAD))
    lo = ((lo_df.to_dict('records') or [{}])[0].get('d')) or seed
    df = pg.fetch_df(
        """
        with st as (select code, start from unnest(%s::text[], %s::date[]) as t(code, start)),
        days as (select trade_date, row_number() over (order by trade_date) as sr
                   from (select distinct trade_date from market_data.stock_daily
                          where trade_date between %s::date and %s::date) z),
        w as (select sd.security_code, sd.trade_date, dd.sr,
                     (sd.close * coalesce(sd.adjustment_factor, 1))::float as hfq
                from market_data.stock_daily sd
                join days dd on dd.trade_date = sd.trade_date
               where sd.security_code = any(%s::text[]) and sd.close is not null
                 and sd.trade_date between %s::date and %s::date),
        lagged as (select security_code, trade_date, sr, hfq,
                          lag(hfq)        over w2 as h_prev,
                          lag(trade_date) over w2 as prev_date,
                          lag(sr)         over w2 as sr_prev
                     from w
                    window w2 as (partition by security_code order by trade_date))
        select l.security_code, l.trade_date::text as trade_date, l.hfq,
               case when l.sr_prev = l.sr - 1 then l.h_prev end as h_prev,
               case when l.sr_prev = l.sr - 1 then l.prev_date::text end as prev_date
          from lagged l
          join st on st.code = l.security_code and l.trade_date >= st.start
         order by l.security_code, l.trade_date
        """, (list(pool), list(pool.values()), lo, str(target_date),
              list(pool), lo, str(target_date)))
    if df is None:
        return {}
    out: dict = {}
    for r in df.to_dict('records'):
        c = str(r['security_code'])
        item = out.setdefault(c, {'days': [], 'base_day': None,
                                  'missing_base': True, '_base': None})
        cur, prev = _num(r.get('hfq')), _num(r.get('h_prev'))
        if not item['days']:                      # 第一行＝起算日本身，它的前值就是基期
            item['_base'] = prev
            item['base_day'] = None if prev is None else _txt(r.get('prev_date'))
            item['missing_base'] = prev is None
        item['days'].append({'day_no': len(item['days']),
                             'trade_date': str(r['trade_date']),
                             'pct': _pct(cur, prev),
                             'cum': _pct(cur, item['_base'])})
    for item in out.values():
        item.pop('_base', None)
    return out
