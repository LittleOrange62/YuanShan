# -*- coding: utf-8 -*-
"""「几天几板」：从本地日线数出来的涨停统计，不是连板数。

用户 2026-10-09 要的是「三天两板 / 5天4板」那一类分类，并指定**回溯前 30 个交易日**。
为什么不直接用东财池里现成的 `zttj`：2026-10-08 实测涨停池 10 只 2 板+ 的票，
`zttj.days == zttj.ct == lbc` **全部相等** —— 那一天恰好每只都是纯连板，这一列就会和
「连板」列一模一样，白占一格。它给不出"断了一天又封上"的形态，而用户要的就是那个形态。

口径（三条都是可核的判据，不是感觉）：

1. **母体**：`market_data.stock_daily` 里该票最近 30 个交易日的行（含目标日）；
   「封住」= `close >= limit_up − 0.001`。用 `limit_up`/`limit_basis` 这两列是因为它们
   已经是当日档位的收口结果（10-07 落地的那套），这里不再自己按板块猜百分比。
   **触板不算板**：只有收盘还封在涨停价上才算，否则炸板那天会被数进"几板"。
2. **一轮的边界**：从今天(最后一天)往前数，**连续 2 个交易日没封住**就认为这一轮结束 ——
   只隔 1 天仍算同一轮，这正是「5天4板」这种形态（4 次涨停跨 5 个交易日、中间空 1 天）。
   轮首之后那些没涨停的日子**不算进"几天"**（它们属于上一轮的空窗，算进来就把 4 天说成 5 天）。
3. **今天必须封住**：这一列只描述"当下这一轮"。今天没封住涨停 ⇒ 没有这一轮，交回 `None`
   （界面 —），**不退回"上一轮 5天4板"**：那样读出来的是历史，不是今天的分类。
   东财池里今天这只票是封住的（它就在涨停池里），所以本地数出来 M=0 就说明**日线缺当天那行**，
   这种不一致要点名，不能静默按 0 处理。
"""
from __future__ import annotations

#: 回溯多少个交易日（用户 2026-10-09 指定 30）
WINDOW_SESSIONS = 30
#: 连续多少个交易日没封住就算这一轮断了
BREAK_DAYS = 2
#: 容差：`close` 与 `limit_up` 都是 numeric，比较一律在 SQL 侧 `::numeric`，
#: 这里只给浮点残余留一个极小的量（不分板块，板块档位已经由 `limit_up` 那列决定）
SEAL_EPS = 0.001

RULE_NOTE = (
    "「几天几板」= 从今天往前数这一轮：连续 %d 个交易日没封住涨停才算断，只空 1 天仍算同一轮；"
    "窗口回溯 %d 个交易日。封住 = 收盘价 ≥ 当日涨停价（档位取 stock_daily 的 limit_up/limit_basis，"
    "触板又炸掉的那天不算板）。所以「5天4板」是 4 次封板跨 5 个交易日、中间空了一天，"
    "而连板数只有 1 —— 这一列与「连板」不是一回事。" % (BREAK_DAYS, WINDOW_SESSIONS))


def span_from_sealed(sealed) -> tuple[int, int, list] | None:
    """纯判据：输入按日期升序的 `[(date, 是否封住)]`，输出 `(几天, 几板, [封板日])`。

    「几天」是这一轮的**跨度**（含中间那天空窗），「封板日」只列真封住的日期 —— 两者分开，
    否则悬停里那串日期会比天数多一天，读者以为自己数错了。
    今天没封住 ⇒ `None`（没有"这一轮"）。空列表同样 `None`。
    """
    seq = list(sealed or [])
    if not seq or not seq[-1][1]:
        return None
    picked: list = []
    gap = 0
    for day, ok in reversed(seq):
        picked.append((day, ok))
        if ok:
            gap = 0
        else:
            gap += 1
            if gap >= BREAK_DAYS:
                break
    while picked and not picked[-1][1]:       # 轮首之后那些空日属于上一轮，不算"几天"
        picked.pop()
    if not picked:
        return None
    picked.reverse()
    return len(picked), sum(1 for _d, ok in picked if ok), [d for d, ok in picked if ok]


def _full_codes(codes) -> list[str]:
    out = []
    for c in (codes or []):
        s = str(c or '').strip()
        if s:
            out.append(s[:6])
    return sorted(set(out))


def boards_by_code(codes, target_date: str) -> dict:
    """批量算「几天几板」：`{6 位代码: {days, boards, label, sealed_dates, rows, sealed_today}}`。

    读不到库或那天没有日线时交回空 dict —— 调用方必须把这看成"没读到"，
    不是"这批票都没有板"（两者在界面上要说的话完全不同）。
    """
    pool = _full_codes(codes)
    by, ok = _sealed_series(pool, target_date, WINDOW_SESSIONS)
    if not ok:
        return {}
    out = {}
    for code in pool:
        seq = by.get(code) or []
        got = span_from_sealed(seq)
        rows_today = [x for x in seq if str(x[0])[:10] == str(target_date)[:10]]
        out[code] = {'days': got and got[0], 'boards': got and got[1],
                     'label': ('%d天%d板' % got[:2]) if got else None,
                     'sealed_dates': [str(d)[:10] for d in (got[2] if got else [])],
                     'rows': len(seq), 'sealed_today': bool(rows_today and rows_today[0][1]),
                     'has_today_row': bool(rows_today)}
    return out


#: 「近 N 场有过几板」的窗口（用户 2026-10-27 点名首板那一屏要这个，不要「几天几板」）
COUNT_WINDOW_SESSIONS = 20


def _sealed_series(codes, target_date: str, sessions: int):
    """把这批票在 `sessions` 个交易日里「每天封没封住」取回来：`({6 位码: [(日期, 封住)]}, 读到了吗)`。

    两个读数共用这一份取数（回溯 30 场的几天几板、近 20 场的有过几板）——
    封住的判据与容差只在这一条 SQL 里出现，别处不许再写第二遍。
    库不通／没数据 ⇒ `({}, False)`：调用方要说"没读到"，不能按"这批票都没封过"处理。
    """
    from . import pg

    pool = list(codes or [])
    if not pool or not pg.enabled():
        return {}, False
    # 先把 6 位码展开成库里的全码，再用 `security_code = any(...)`：
    # 实测同一个查询 `left(security_code,6)=any(...)` 要 2.63 秒、写成等值 0.25 秒（10 只票、30 场）。
    # 这一列是首屏读的，不该为了一种写法省一次小查询而慢十倍。
    expansion = pg.fetch_df(
        "select security_code, left(security_code, 6) as code6 from reference_data.security"
        " where left(security_code, 6) = any(%s::text[])", (pool,))
    if expansion is None:
        return {}, False
    full = [str(r['security_code']) for r in expansion.to_dict('records')]
    if not full:
        return {}, False
    df = pg.fetch_df(
        """
        with win as (select distinct trade_date from market_data.stock_daily
                      where trade_date <= %s::date order by trade_date desc limit %s),
             s as (select left(sd.security_code, 6) as code, sd.trade_date,
                          (sd.close::numeric >= sd.limit_up::numeric - %s) as sealed
                     from market_data.stock_daily sd
                     join win w on w.trade_date = sd.trade_date
                    where sd.security_code = any(%s::text[])
                      and sd.limit_up is not null)
        select code, trade_date, sealed from s order by code, trade_date
        """, (str(target_date), sessions, SEAL_EPS, full))
    if df is None:
        return {}, False
    by: dict = {}
    for r in df.to_dict('records'):
        by.setdefault(str(r['code'])[:6], []).append((r['trade_date'], bool(r['sealed'])))
    return by, True


def sealed_days_by_code(codes, target_date: str,
                        sessions: int = COUNT_WINDOW_SESSIONS) -> dict:
    """批量数「最近 `sessions` 个交易日里有几天收盘封住涨停」：`{6 位码: {sealed_days, bars, dates}}`。

    与「几天几板」的区别：那一列只看**当前这一轮**（连续断两天就不再往回数），
    这一列看**窗口内的总次数** —— 所以一只票可以「1天1板」（这一轮只有今天）而近 20 场 5 板。
    `bars` 是它在这个窗口里真有几根日线：新股只有 6 根时，5 次要读成「6 根里 5 次」而不是「20 场里 5 次」，
    所以这个数必须跟着交出去。读不到库 ⇒ 空 dict（没读到 ≠ 一只都没封过）。
    """
    pool = _full_codes(codes)
    by, ok = _sealed_series(pool, target_date, sessions)
    if not ok:
        return {}
    out = {}
    for code in pool:
        seq = by.get(code) or []
        dates = [str(d)[:10] for d, sealed in seq if sealed]
        out[code] = {'sealed_days': len(dates), 'bars': len(seq), 'dates': dates}
    return out
