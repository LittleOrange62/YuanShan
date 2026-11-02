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

#: 两个方向共用同一条查询形状：档位列与比较符号按方向取，**别处不许再手写第二遍**。
#: `up` 是收盘还封在涨停价上；`down` 是收盘还封在跌停价上（符号反过来，容差同一份）。
#: 容差一律 `%s::numeric`：numeric 与 Python float 直接相加会整式升成 float8，
#: 边界那一分会不会翻只取决于装箱顺序（触板那档实测栽过一次：92 对 94）。
SEAL_SIDES = {
    'up': ('limit_up', 'sd.close::numeric >= sd.{col}::numeric - %s::numeric'),
    'down': ('limit_down', 'sd.close::numeric <= sd.{col}::numeric + %s::numeric'),
}

RULE_NOTE = (
    "「几天几板」= 从今天往前数这一轮：连续 %d 个交易日没封住涨停才算断，只空 1 天仍算同一轮；"
    "窗口回溯 %d 个交易日。封住 = 收盘价 ≥ 当日涨停价（档位取 stock_daily 的 limit_up/limit_basis，"
    "触板又炸掉的那天不算板）。所以「5天4板」是 4 次封板跨 5 个交易日、中间空了一天，"
    "而连板数只有 1 —— 这一列与「连板」不是一回事。" % (BREAK_DAYS, WINDOW_SESSIONS))

#: 跌停那一侧的同一把尺（措辞跟着列名改，规则一条没变：同一个窗口、同一个断点、同一个容差）
DOWN_RULE_NOTE = (
    "「几天几跌停」= 从今天往前数这一轮：连续 %d 个交易日没封住跌停才算断，只空 1 天仍算同一轮；"
    "窗口回溯 %d 个交易日。封住 = 收盘价 ≤ 当日跌停价（档位取 stock_daily 的 limit_down/limit_basis，"
    "盘中触及又打开的那天不算）。与「连续跌停」那一列不是一回事：那一列按涨停池同款的半分容差判，"
    "中间断一天就归 1，这一列仍算在同一轮里。" % (BREAK_DAYS, WINDOW_SESSIONS))


def rule_note(direction: str = 'up') -> str:
    """这一侧的口径文字（列头悬停显示的那一段，只在这一份里）。"""
    return DOWN_RULE_NOTE if direction == 'down' else RULE_NOTE


def _side(direction: str):
    """方向 → (档位列名, 比较式模板)。不认识的方向**当场抛错**，不静默按涨停算 ——
    静默回落会让跌停那一屏读出涨停的数，而两块的列名都还在屏幕上。"""
    try:
        col, expr = SEAL_SIDES[direction]
    except KeyError:
        raise ValueError(f"不支持的方向：{direction}（可选 {'、'.join(SEAL_SIDES)}）") from None
    return col, expr.format(col=col)


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


def boards_by_code(codes, target_date: str, direction: str = 'up') -> dict:
    """批量算「几天几板」（跌停侧同一把尺，列名换叫「几天几跌停」）：
    `{6 位代码: {days, boards, label, sealed_dates, rows, sealed_today}}`。

    读不到库或那天没有日线时交回空 dict —— 调用方必须把这看成"没读到"，
    不是"这批票都没有板"（两者在界面上要说的话完全不同）。
    格子里那一串中文跟着方向换字（跌停那一侧不许念成"2 天 2 板"，屏幕上两个词会被读成两回事）。
    """
    pool = _full_codes(codes)
    by, ok = _sealed_series(pool, target_date, WINDOW_SESSIONS, direction)
    if not ok:
        return {}
    unit = '跌停' if direction == 'down' else '板'
    out = {}
    for code in pool:
        seq = by.get(code) or []
        got = span_from_sealed(seq)
        rows_today = [x for x in seq if str(x[0])[:10] == str(target_date)[:10]]
        out[code] = {'days': got and got[0], 'boards': got and got[1],
                     'label': ('%d天%d%s' % (got[0], got[1], unit)) if got else None,
                     'sealed_dates': [str(d)[:10] for d in (got[2] if got else [])],
                     'rows': len(seq), 'sealed_today': bool(rows_today and rows_today[0][1]),
                     'has_today_row': bool(rows_today)}
    return out


#: 「近 N 场有过几板」的窗口（用户 2026-10-27 点名首板那一屏要这个，不要「几天几板」）
COUNT_WINDOW_SESSIONS = 20


def _sealed_series(codes, target_date: str, sessions: int, direction: str = 'up',
                   eps: float | None = None):
    """把这批票在 `sessions` 个交易日里「每天封没封住」取回来：`({6 位码: [(日期, 封住)]}, 读到了吗)`。

    两个读数共用这一份取数（回溯 30 场的几天几板、近 20 场的有过几板）——
    封住的判据与容差只在这一条 SQL 里出现，别处不许再写第二遍。
    `direction` 取 `up`／`down`：涨停与跌停共用这一条查询，只换档位列与比较符号。
    `eps` 只在"这一把尺要跟着别的读数走"时才传（跌停那一屏的「连续跌停」用的半分容差
    与它自己数出来的跌停家数是同一个尺，不能又换成 `SEAL_EPS`）；不传就是本模块那把 0.001。
    库不通／没数据 ⇒ `({}, False)`：调用方要说"没读到"，不能按"这批票都没封过"处理。
    """
    from . import pg

    col, cmp_expr = _side(direction)
    tol = SEAL_EPS if eps is None else eps
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
        f"""
        with win as (select distinct trade_date from market_data.stock_daily
                      where trade_date <= %s::date order by trade_date desc limit %s),
             s as (select left(sd.security_code, 6) as code, sd.trade_date,
                          ({cmp_expr}) as sealed
                     from market_data.stock_daily sd
                     join win w on w.trade_date = sd.trade_date
                    where sd.security_code = any(%s::text[])
                      and sd.{col} is not null)
        select code, trade_date, sealed from s order by code, trade_date
        """, (str(target_date), sessions, tol, full))
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


#: 「连续跌停」档位用的窗口：连板档位最多四档，30 场的底足够；与几天几板同一份回溯场次
STREAK_WINDOW_SESSIONS = WINDOW_SESSIONS


def streak_from_sealed(sealed) -> int:
    """纯判据：从今天（序列最后一天）往前**紧挨着**数，连续封住多少场。今天没封住 ⇒ 0。

    与 `span_from_sealed` 的区别：那一把允许中间空一天仍算同一轮（给出「5天4板」那种形态），
    这一把只看紧挨着的场次 —— 屏幕上「连板／连续跌停」那一列是这一把，档位筛选也按它。
    """
    n = 0
    for _d, ok in reversed(list(sealed or [])):
        if not ok:
            break
        n += 1
    return n


def streak_by_code(codes, target_date: str, sessions: int = STREAK_WINDOW_SESSIONS,
                   direction: str = 'down', eps: float | None = None) -> dict:
    """批量数「从今天往回连续封住多少场」：`{6 位码: {streak, bars, sealed_dates}}`。

    `bars` 是窗口里真有几根日线：上市不满窗口的票，"6 根里连续 3 场"与"30 场里连续 3 场"
    不是一回事，所以这个数要跟着交出去。读不到库 ⇒ 空 dict（没读到 ≠ 一只都没连）。
    """
    pool = _full_codes(codes)
    by, ok = _sealed_series(pool, target_date, sessions, direction, eps)
    if not ok:
        return {}
    out = {}
    for code in pool:
        seq = by.get(code) or []
        out[code] = {'streak': streak_from_sealed(seq), 'bars': len(seq),
                     'sealed_dates': [str(d)[:10] for d, sealed in seq if sealed]}
    return out
