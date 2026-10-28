"""quantdash 口径轨的**本地计算** —— 不依赖 PG 的 `qd` 表，从只读日线现算。

为什么要有这一份（2026-09-24 用户裁定）：`D:\\finance\\YuanShan` 是唯一落地点，
"好用的地方要搬进 YuanShan，而不是存在其他地方"。原先这套算法落在 PG 的 `qd` schema
（由 FinanceTool 侧回填了 1169 个交易日），YuanShan 只是个读表客户端 —— 那等于能力
还在别处。现在算法在本文件里，`qd` 表退为**对照基准**。

算法口径逐条来自 `docs/数据加工工作指南/09-quantdash逻辑规格.md` §4，
逻辑来源 quantdash-ai-stock-main（PolyForm Noncommercial）—— **只重实现口径，未复制源码**。

三条实现纪律：
  · **本轨判据是"双边严格"**（`abs(close-limit_up)<=0.005`），与 `pg_sources._ZT_PRED`
    那条双规则**刻意不同**，因为 09 §3 这么要求。两套判据给出不同家数是"双轨并存"的
    应有之义，但谁和谁不一样必须写在注释里，不许让人以为是巧合。
  · **算不出就是 None**，不用 0/常量/哨兵兜底（09 §7 那 42 处伪造点一个不搬）。
  · **一次请求只发几条有界查询**：池成员用一条窗口查询，明细只取用到的那几天的
    那几百只票，市场宽度用一条聚合。不许把全市场 5500 只 × 200 天拉进内存。
"""

from __future__ import annotations

import datetime
import json
import os
import threading

from . import pg
from .util import atomic_write_json

# ⚠️ **qd 轨专用的涨跌停判据：双边严格**，与 `pg_sources._ZT_PRED` 那条"价格精确对上
# 或涨幅落在档位 ±0.3"的**双规则不同**。这不是疏忽 —— 09 §3 明文要求本轨用双边严格，
# 双轨并存下两套判据允许给出不同家数，但必须写明是谁和谁不一样。
_ZT_STRICT = "(sd.limit_up is not null and abs(sd.close - sd.limit_up) <= 0.005)"
_DT_STRICT = "(sd.limit_down is not null and abs(sd.close - sd.limit_down) <= 0.005)"
__all__ = ["compute_sentiment", "cached", "remember", "cache_dir"]

_LOOKBACK_DAYS = 90        # 连板数回看：90 自然日约 62 个场次，远超实见最高板（12）
_CACHE_SCHEMA = "qd-local-1"
_lock = threading.Lock()   # 单请求内不重复算同一场


def cache_dir() -> str:
    from . import pg as _p  # noqa: F401  仅为可读性：缓存在项目本地，不落库
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".local", "qd_cache")
    os.makedirs(root, exist_ok=True)
    return root


# --------------------------------------------------------------- 取数（有界）

def _window(d: str) -> tuple[str, str, str]:
    end = datetime.date.fromisoformat(d)
    start = end - datetime.timedelta(days=_LOOKBACK_DAYS)
    nxt = end + datetime.timedelta(days=12)   # 覆盖 T+1（周末/节假日留余量）
    return start.isoformat(), d, nxt.isoformat()


def _pool_membership(start: str, end: str) -> dict[str, dict[str, set]]:
    """窗口内每个交易日三种池的成员：涨停 / 触板未封 / 跌停。

    返回 {date: {"zt": set, "broken": set, "dt": set}}。只回 (code, date) 级别的
    存在性，行数很小（约 (65+40+10)/日 × 200 日）。
    """
    sql = f"""
        select sd.security_code, sd.trade_date::text as d,
               ({_ZT_STRICT}) as is_zt,
               (sd.limit_up is not null and sd.high >= sd.limit_up - 0.005
                and not ({_ZT_STRICT})) as is_broken,
               ({_DT_STRICT}) as is_dt
        from market_data.stock_daily sd
        join reference_data.security sec using (security_code)
        where sd.trade_date between %s and %s
          and (sd.limit_up is not null or sd.limit_down is not null)
        """
    df = pg.fetch_df(sql, (start, end))
    if df is None:
        return {}
    out: dict[str, dict[str, set]] = {}
    for r in df.itertuples():
        day = out.setdefault(r.d, {"zt": set(), "broken": set(), "dt": set()})
        if r.is_zt:
            day["zt"].add(r.security_code)
        if r.is_broken:
            day["broken"].add(r.security_code)
        if r.is_dt:
            day["dt"].add(r.security_code)
    return out


def _board_streaks(members: dict) -> dict[str, dict[str, int]]:
    """按会话升序推"截至该日连续涨停板数"。

    比逐票回看快得多：上一日不在板上就重置为 0，所以 `bc[code] = prev.get(code,0)+1`。
    """
    streaks: dict[str, dict[str, int]] = {}
    prev: dict[str, int] = {}
    for d in sorted(members):
        cur = {c: prev.get(c, 0) + 1 for c in members[d]["zt"]}
        streaks[d] = cur
        prev = cur
    return streaks


def _details(codes: set, dates: list) -> dict:
    """取这些票在这些交易日的明细。 {(date, code): row}。"""
    if not codes or not dates:
        return {}
    sql = f"""
        select sd.trade_date::text as d, sd.security_code, sec.name,
               sd.open, sd.high, sd.low, sd.close, sd.pre_close, sd.pct_change,
               sd.amount_yuan, sd.turnover_rate_percent, sd.limit_up, sd.limit_down
        from market_data.stock_daily sd
        join reference_data.security sec using (security_code)
        where sd.security_code = any(%s) and sd.trade_date = any(%s::date[])
        """
    df = pg.fetch_df(sql, (sorted(codes), dates))
    if df is None:
        return {}
    return {(r.d, r.security_code): r for r in df.itertuples()}


def _market_agg(d: str) -> dict:
    """当日涨跌家数 / 成交额 / 总数（本地日线实算，绝不用 quantdash 的线性估算模型）。"""
    sql = f"""
        with x as (
          select sd.pct_change, sd.amount_yuan,
                 (left(sd.security_code,1) in ('8','4')
                  or left(split_part(sd.security_code,'.',1),3)='920') as is_bj
          from market_data.stock_daily sd
          where sd.trade_date = %s
        )
        select count(*) filter (where pct_change > 0)::int as up,
               count(*) filter (where pct_change < 0)::int as down,
               count(*) filter (where pct_change = 0)::int as flat,
               count(*)::int as universe,
               coalesce(sum(amount_yuan), 0)::float8 as amount
        from x
        """
    df = pg.fetch_df(sql, (d,))
    if df is None or not len(df):
        return {}
    return df.to_dict("records")[0]


def _index_amounts(dates: list) -> dict:
    """上证 + 深成两市成交额（亿元）。quantdash 口径 D-37：这不等于全 A 成交额。"""
    if not dates:
        return {}
    sql = """
        select trade_date::text as d, coalesce(sum(amount_yuan),0)::float8 as a
        from market_data.index_daily
        where security_code = any(%s) and trade_date = any(%s::date[])
        group by 1
        """
    df = pg.fetch_df(sql, (["000001.SH", "399001.SZ"], dates))
    if df is None:
        return {}
    return {r.d: r.a for r in df.itertuples()}


def _sessions(members: dict, upto: str) -> list:
    return [d for d in sorted(members) if d <= upto]


def _r(v, n):
    return None if v is None else round(float(v), n)


# --------------------------------------------------------------- 情绪周期

def _tier_counts(cur: dict) -> dict:
    """板数分布 {board: 家数}，板数 >= 1。"""
    out: dict[int, int] = {}
    for b in cur.values():
        out[b] = out.get(b, 0) + 1
    return out


def _sentiment_value(prev: dict, cur: dict) -> "float | None":
    """砸盘系数/情绪分：10 × Σ_{b=2..14, prev[b]>0} curr[b+1]/prev[b] ÷ 参与求和的档数。

    quantdash 在退化时写常量 5/2（D-2）—— 我们不跟，档数为 0 就是 None。
    """
    terms = [cur.get(b + 1, 0) / prev[b] for b in range(2, 15) if prev.get(b, 0) > 0]
    if not terms:
        return None
    return _r(10 * sum(terms) / len(terms), 2)


def _broken_rate(broken: int, zt: int) -> "float | None":
    """炸板率（触板口径）= 触板未封 / 触板数 × 100。

    ⚠️ 分母是**触板数**（涨停 + 触板未封），不是"涨停家数"。字段名带 `touched`
    就是为了不许和 `broken_rate_sealed`（1-晋级率）互换。日线推不出"曾封板后炸开"，
    这是 quantdash 高位风险口径的近似（D-3），实测与东财炸板池口径差约 64%。
    """
    touched = zt + broken
    return None if touched <= 0 else _r(broken / touched * 100, 1)


def _risk_level(a_kill: "int | None", weak: "int | None",
                broken_rate: "float | None") -> "str | None":
    """三档风险。输入全缺 → None（quantdash 把无数据当"低风险"，D-7，不跟）。"""
    if a_kill is None and broken_rate is None and weak is None:
        return None
    ak = a_kill or 0
    br = broken_rate if broken_rate is not None else 0
    wc = weak or 0
    if ak >= 2 or br >= 35:
        return "high"
    if ak >= 1 or br >= 20 or wc >= 2:
        return "medium"
    return "low"


def _volume_state(ch_now: "float | None", ch_prev: "float | None",
                  sv: "float | None", sv_prev: "float | None") -> str:
    """持续放量 / 缩量再缩量 / 放量滞涨 / 存量震荡。

    变化率为 None 时归"存量震荡"是 quantdash 原行为，照搬但要在 deviation 里写明。
    """
    if ch_now is not None and ch_prev is not None:
        if ch_now > 0 and ch_prev > 0:
            return "持续放量"
        if ch_now < 0 and ch_prev < 0:
            return "缩量再缩量"
        if ch_now > 0 and sv is not None and sv_prev is not None and sv <= sv_prev:
            return "放量滞涨"
    return "存量震荡"


def _stage(sv, height, lb, hb, fbr, repair, lncl, vs, risk, zt, variant):
    """三套阶段判定共用的 if-elif 短路链（09 §4.1）。

    `variant` 是 quantdash 自己那三套互相矛盾的阈值里的两套（`ui` = 前端总览，
    `mcp` = 它的 AI 读的那套），差异只有两处：冰点用最高板高度还是龙头板数、
    主升是否额外要求情绪分 >= 5。**并存是事实，不是我们要修的 bug**。
    缺失的判据一律按"条件不成立"处理，不用 quantdash 的 0 / -99 哨兵（D-5）。
    """
    def ge(x, t):
        return x is not None and x >= t

    def le(x, t):
        return x is not None and x <= t

    # 1 退潮
    if risk == "high" or le(lncl, -5) or (repair is not None and repair < 20):
        return "退潮", 1
    # 2 冰点：ui 用 board_height，mcp 用 leader_board_count
    gate = height if variant == "ui" else lb
    if gate is not None and gate <= 2 and (zt is not None and zt < 20):
        return "冰点", 2
    # 3 修复
    if ge(repair, 35) and lncl is not None and lncl >= 0 and vs != "缩量再缩量":
        return "修复", 3
    # 4 主升：ui 额外要求情绪分 >= 5
    ok_high = hb is not None and hb >= 3 and lb is not None and lb >= 5 and vs != "缩量再缩量"
    if ok_high and (variant != "ui" or ge(sv, 5)):
        return "主升", 4
    # 5 试错
    if ge(fbr, 60) or (lb is not None and lb <= 4):
        return "试错", 5
    return "分歧", 6


def _stage_feishu(hb, zt, lb, fbr, repair, success, lncl, a_kill, risk):
    """飞书机器人那套（只有两档：退潮偏防守 / 主升延续 / 修复偏进攻，默认分歧震荡）。"""
    if risk == "high" or (repair is not None and repair < 20):
        return "退潮"
    if (repair is not None and repair >= 35) and (success is not None and success >= 65) \
            and lncl is not None and lncl >= 0:
        return "修复"
    if (lb is not None and lb >= 5) and (success is not None and success >= 70) \
            and (a_kill == 0):
        return "主升"
    return "分歧震荡"


_CONF_BASE = {"主升": 72, "修复": 68, "退潮": 75, "冰点": 70}


def _confidence(stage: str, risk: "str | None", vs: str) -> int:
    """查表加和。**不是统计置信度**（D-8）。quantdash 那个 min(95,·) 恒不触发，去掉。"""
    v = _CONF_BASE.get(stage, 62)
    v += {"low": 6, "high": 8}.get(risk or "", 3)
    if vs in ("持续放量", "缩量再缩量"):
        v += 6
    return v


def _next_pct(details, date: str, codes, base_close: dict) -> dict:
    """这批票在 `date` 那天的收盘涨幅（相对 `base_close`，通常是前一日的收盘价）。

    没有行情就是"没有"—— 不塞进结果里，让调用方按缺失处理，不折成 0。
    """
    out = {}
    for c in codes:
        row = details.get((date, c))
        b = base_close.get(c)
        if row is None or not b:
            continue
        try:
            out[c] = (float(row.close) - float(b)) / float(b) * 100
        except (TypeError, ValueError):
            continue
    return out


# --------------------------------------------------------------- 公开入口

_MAIN = ("600", "601", "603", "605", "000", "001", "002", "003")
_GEM = ("300", "301")
_STAR = ("688", "689")
MAIN, GEM, STAR = _MAIN, _GEM, _STAR      # 给 qd_ladder 用的公开名


def next_session_of(date: str):
    """下一交易日走交易日历。

    原实现取"kline 里最后一个 date <= 目标日"，非交易日会**静默前移**（D-18）——
    那样算出来的"次日表现"其实隔了好几天，却看不出来。
    """
    df = pg.fetch_df("""select next_open_date::text as d from reference_data.trade_calendar
                        where calendar_date = %s and is_open""", (date,))
    if df is None or not len(df) or df["d"].iloc[0] is None:
        return None
    return str(df["d"].iloc[0])


def _is_bj(code6: str) -> bool:
    return code6[0] in "84" or code6.startswith("920")


def _tier(b: int) -> int:
    return 7 if b >= 7 else b


def cached(key: str, date: str):
    path = os.path.join(cache_dir(), f"{key}_{date}.json")
    try:
        with open(path, encoding="utf-8") as f:
            blob = json.load(f)
        if blob.get("schema") == _CACHE_SCHEMA and blob.get("date") == date:
            return blob.get("data")
    except (OSError, ValueError):
        pass
    return None


def remember(key: str, date: str, data) -> None:
    # 只缓存已过去的场次：当天的数还在变，缓存了就会拿半成品当定稿。
    if date >= datetime.date.today().isoformat():
        return
    atomic_write_json(os.path.join(cache_dir(), f"{key}_{date}.json"),
                      {"schema": _CACHE_SCHEMA, "date": date, "data": data})


def compute_sentiment(date: str) -> "dict | None":
    """quantdash 口径的情绪周期一场（09 §4.1）。`date` 为 'YYYY-MM-DD'。"""
    start, _, nxt = _window(date)
    members = _pool_membership(start, nxt)
    if date not in members:
        return None
    sessions = sorted(members)
    i = sessions.index(date)
    streaks = _board_streaks(members)

    cur = streaks[date]
    counts = _tier_counts(cur)
    zt = len(cur)
    broken = len(members[date]["broken"])
    dt = len(members[date]["dt"])
    height = max(counts) if counts else 0
    first = counts.get(1, 0)
    high = sum(n for b, n in counts.items() if b >= 4)
    lb = height

    prev_s = sessions[i - 1] if i > 0 else None
    prev2_s = sessions[i - 2] if i > 1 else None
    next_s = sessions[i + 1] if i + 1 < len(sessions) else None

    prev_counts = _tier_counts(streaks[prev_s]) if prev_s else {}
    prev2_counts = _tier_counts(streaks[prev2_s]) if prev2_s else {}
    sv = _sentiment_value(prev_counts, counts)
    sv_prev = _sentiment_value(prev2_counts, prev_counts) if prev_s else None

    # T-1 场次的池子 + 这些票在 T 日的收盘，是全部 `_lag1` 指标的原料。
    codes = set(members[date]["broken"]) | set(members[date]["zt"])
    if prev_s:
        codes |= set(members[prev_s]["broken"]) | set(streaks[prev_s])
    if next_s:
        codes |= set(streaks.get(next_s, {})) | set(cur)
    dates_needed = [x for x in (prev2_s, prev_s, date, next_s) if x]
    details = _details(codes, dates_needed)

    base_prev = {c: r.close for c, r in
                 ((c, details.get((prev_s, c))) for c in sorted(codes))
                 if r is not None and r.close is not None} if prev_s else {}

    repair = big_rep = big_cnt = None
    a_kill = weak = None
    lncl = None
    if prev_s:
        bp = sorted(members[prev_s]["broken"])
        nxt_pct = _next_pct(details, date, bp, base_prev)
        # 池子非空但次日行情一条都没取到 → None（"还没观察到"），不是 0%（"全没修复"）。
        if bp and nxt_pct:
            repair = _r(sum(1 for v in nxt_pct.values() if v > 0) / len(bp) * 100, 1)
        big = [c for c in bp
               if (row := details.get((prev_s, c))) and row.pct_change is not None
               and float(row.pct_change) <= -5]
        big_cnt = len(big)
        if big and nxt_pct:
            big_rep = _r(sum(1 for c in big if nxt_pct.get(c, 0) > 0) / len(big) * 100, 1)
        hp = {c: b for c, b in streaks[prev_s].items() if b >= 4}
        pn = _next_pct(details, date, sorted(hp), base_prev)
        if hp:
            a_kill = sum(1 for v in pn.values() if v <= -8)
            weak = sum(1 for v in pn.values() if v < 0)
        top = max(streaks[prev_s].values()) if streaks[prev_s] else 0
        if top:
            lead = sorted(c for c, b in streaks[prev_s].items() if b == top)
            # 契约要的是"**那只**龙头"在 T 日的收盘涨幅，不是并列龙头的平均值。
            # 平均会把判定链带偏（实测 09-21：平均 4.36 vs 单只 -1.3，阶段因此从
            # 主升掉到修复）。选法与天梯那侧同一套 tie-break：封板时间 → 成交额 → 代码。
            def _amt(c):
                row = details.get((prev_s, c))
                return float(row.amount_yuan) if row and row.amount_yuan is not None else -1.0
            lead.sort(key=lambda c: (-_amt(c), c))
            pv = _next_pct(details, date, lead[:1], base_prev)
            lncl = _r(pv[lead[0]], 2) if lead and lead[0] in pv else None

    br_touched = _broken_rate(broken, zt)
    risk = _risk_level(a_kill, weak, br_touched)

    agg = _market_agg(date)
    aggs = {d: _market_agg(d) for d in (prev_s, prev2_s) if d}
    amts = _index_amounts([x for x in (date, prev_s, prev2_s) if x])
    amount_yi = _r(amts.get(date, 0) / 1e8, 0) if date in amts else None
    ch = _r((amts[date] - amts[prev_s]) / amts[prev_s] * 100, 2) \
        if prev_s and amts.get(prev_s) and date in amts else None
    ch_p = _r((amts[prev_s] - amts[prev2_s]) / amts[prev2_s] * 100, 2) \
        if prev2_s and prev_s and amts.get(prev2_s) and amts.get(prev_s) else None
    vs = _volume_state(ch, ch_p, sv, sv_prev)

    # 晋级率/平均板增益要 T+1，最新一场算不出就是 None（quantdash 在这里写 0，D-34）。
    success = avg_gain = premium = br_sealed = None
    if next_s and zt:
        ns = streaks.get(next_s, {})
        gains = [ns.get(c, 0) - b for c, b in cur.items()]
        ups = [g for g in gains if g > 0]
        success = _r(len(ups) / zt * 100, 2)
        # 只累加晋级者的板增益、单股封顶 3；未晋级的**不**计入分子。
        # （把负增益也算进去会让"平均板增益"变成负数，看着像 bug 而非口径。）
        avg_gain = _r(sum(min(g, 3) for g in ups) / zt, 3)
        if success is not None and avg_gain is not None:
            premium = _r(success / 100 * 60 + avg_gain * 10, 2)
        if success is not None:
            br_sealed = _r((1 - success / 100) * 100, 1)

    fbr = _r(first / zt * 100, 1) if zt else None
    hbr = _r(high / zt * 100, 1) if zt else None

    stage_ui, hit_ui = _stage(sv, height, lb, high, fbr, repair, lncl, vs, risk, zt, "ui")
    stage_mcp, hit_mcp = _stage(sv, height, lb, high, fbr, repair, lncl, vs, risk, zt, "mcp")
    stage_fs = _stage_feishu(high, zt, lb, fbr, repair, success, lncl, a_kill, risk)
    facts = (f"风险 {risk or '未知'}、最高板 {height}、涨停 {zt} 家、首板占比 "
             f"{fbr if fbr is not None else '—'}%、龙头板数 {lb}、高板 {high} 家、"
             f"炸板修复率(T-1) {repair if repair is not None else '—'}%、"
             f"龙头次日收盘 {lncl if lncl is not None else '—'}%、量能 {vs}")
    reasons = [
        {"stage": "stage_ui", "hit_rule": hit_ui, "reason": f"{stage_ui}（优先级第 {hit_ui} 条命中）｜{facts}"},
        {"stage": "stage_mcp", "hit_rule": hit_mcp, "reason": f"{stage_mcp}（优先级第 {hit_mcp} 条命中）｜{facts}"},
        {"stage": "stage_feishu",
         "reason": f"{stage_fs}（飞书版只有退潮/修复/主升三档硬条件，默认分歧震荡）｜{facts}"},
    ]

    return {
        "trade_date": date, "spec_version": "qd-1", "source": "quantdash-logic+local",
        "stage_ui": stage_ui, "stage_mcp": stage_mcp, "stage_feishu": stage_fs,
        "reasons": reasons, "confidence": _confidence(stage_ui, risk, vs),
        "risk_level": risk, "volume_state": vs, "scope": "all_boards",
        "metrics": {
            "sentiment_value": sv, "board_height": height, "leader_board_count": lb,
            "high_board_count": high, "limit_up_count": zt, "limit_down_count": dt,
            "touched_count": zt + broken, "broken_touched_count": broken,
            "first_board_count": first, "first_board_ratio_pct": fbr,
            "high_board_ratio_pct": hbr,
            "relay_count": max(zt - first, 0) if zt else None,
            "success_rate_pct": success, "avg_board_gain": avg_gain,
            "premium_score": premium, "broken_rate_sealed_pct": br_sealed,
            "broken_rate_touched_pct": br_touched,
            "broken_repair_rate_lag1_pct": repair, "big_face_count_lag1": big_cnt,
            "big_face_repair_rate_lag1_pct": big_rep, "a_kill_count_lag1": a_kill,
            "weak_count_lag1": weak, "leader_next_close_pct": lncl,
            "rise_count": agg.get("up"), "fall_count": agg.get("down"),
            "flat_count": agg.get("flat"), "amount_yi": amount_yi,
            "amount_change_pct": ch,
        },
        "universe": agg.get("universe"),
    }
