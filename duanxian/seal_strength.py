# -*- coding: utf-8 -*-
"""封板强度：把"这一根涨停封得有多硬"折成一个 0~100 的数。

用户 2026-10-09 点名要这一列：「按首封之后是否开过板（炸板次数）、封单量与流通市值的比例
等参数做一个加权算法，算法方法在页面上要有地方可以查看」。这一页就是那个"地方"的**唯一真值**：
`vr/market.py` 拿它算，页面上悬停看到的那段说明由 `spec_text()` 从下面这几张表现拼，
**不许在前端再写一遍分档**（两处各写一套，早晚会互相走偏，而读者看到的是同一个列名）。

三条输入（东财涨停板池同一场同时给得到；2026-10-08 实测 43/43 行都有值、且非零）：

* **封单资金 ÷ 流通市值**（权重 45）：排队愿意买的人堆了多深。用绝对额不行 ——
  百亿市值的票堆一千万封单是弱，小盘票堆一千万是强，所以除以流通市值折成百分比。
* **开板（炸板）次数 `zbc`**（权重 35）：这根封板被吃掉过几次，0 次是从头到尾没开过。
* **首次封板时间 `fbt`**（权重 20）：多早就封住。早 = 承接时间给得久。

两条口径上的自我约束：

1. **绝对分档，不用当日分位**。连板股常常只有十来只，分位在 n=10 上会跳，而且今天的第一名
   和昨天的第一名不是一回事；分档让"82 分意味着什么"每天都一样。
2. **缺数不给 0 分**。任何一项缺（池子没给这栏、流通市值为 0）就把该模块从**分母**里摘掉、
   权重在其余项上重新归一，并把缺了什么交回调用方。按 0 算会把"没数据"排成"最弱"。
   这一列是**当日快照的相对强度**，不是预测、不构成建议。
"""
from __future__ import annotations

#: 口径版本。**改分档或权重就升号**，页面上会跟着显示它是按哪一版算的。
SPEC_VERSION = "seal-strength-1"

#: 三个模块的权重（加起来 100）。
WEIGHTS = {"fund": 45, "reopen": 35, "first": 20}

#: 越大越好：`((达到多少→分, ...), 不到最低档的保底分)`。封单占比的单位是**百分数**（4.47 = 4.47%）。
FUND_STEPS, FUND_FLOOR = ((3.0, 1.00), (1.0, 0.85), (0.5, 0.70), (0.2, 0.50), (0.05, 0.30)), 0.15
#: 越小越好：`((不超过多少→分, ...), 超过最后一档的保底分)`。
REOPEN_STEPS, REOPEN_FLOOR = ((0, 1.00), (1, 0.70), (2, 0.50), (3, 0.35), (5, 0.20)), 0.05
#: 越小越好，单位是池内的 HHMMSS 整数。09:25 那一档是**集合竞价一字**（给满分，但页面必须注明它不等于早盘承接）。
FIRST_STEPS, FIRST_FLOOR = ((92500, 1.00), (93000, 0.95), (94000, 0.85), (100000, 0.70),
                            (103000, 0.55), (113000, 0.40), (140000, 0.25)), 0.10

_CAVEAT = ("这一列只说「这一场封得硬不硬」，到收盘为止，不是预测、不构成任何交易建议。\n"
           "09:25 那批是集合竞价撮出来的封板，「早」不等于早盘承接强；它给满分是因为一字板全天不开板的可能性最高。")


def _at_least(value, steps, floor):
    return next((sc for hi, sc in steps if value >= hi), floor)


def _at_most(value, steps, floor):
    return next((sc for hi, sc in steps if value <= hi), floor)


def fund_score(ratio_pct) -> float:
    """封单 ÷ 流通市值（百分数）→ 0~1。"""
    return _at_least(ratio_pct, FUND_STEPS, FUND_FLOOR)


def reopen_score(open_times) -> float:
    """开板（炸板）次数 → 0~1。"""
    return _at_most(open_times, REOPEN_STEPS, REOPEN_FLOOR)


def first_seal_score(fbt) -> float:
    """首次封板时间（HHMMSS 整数）→ 0~1；尾盘才封住的保底 0.10。"""
    return _at_most(fbt, FIRST_STEPS, FIRST_FLOOR)


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f          # NaN → None：缺数，不是 0


def seal_strength(fund, float_cap, open_times, first_seal) -> dict:
    """三条输入 → `{score, parts, missing, weights}`。

    `fund` / `float_cap` 单位都是元；`open_times` 是池内 `zbc`；`first_seal` 是 `fbt`（HHMMSS）。
    缺哪一项就不计哪一项（**不按 0 算**），权重在其余项上重新归一；
    **有数的少于两项就不给分**（`score=None`），否则一条输入会被归一成满分。
    """
    parts, avail, missing = {}, {}, []
    f, cap = _num(fund), _num(float_cap)
    if f is not None and cap is not None and cap > 0:
        ratio = f / cap * 100.0
        parts["fund"] = {"ratio_pct": round(ratio, 3), "band": fund_score(ratio)}
        avail["fund"] = WEIGHTS["fund"]
    else:
        missing.append("fund")

    ot = _num(open_times)
    if ot is not None:
        parts["reopen"] = {"opens": int(ot), "band": reopen_score(ot)}
        avail["reopen"] = WEIGHTS["reopen"]
    else:
        missing.append("reopen")

    fs = _num(first_seal)
    if fs is not None:
        parts["first"] = {"hhmmss": int(fs), "band": first_seal_score(fs)}
        avail["first"] = WEIGHTS["first"]
    else:
        missing.append("first")

    if len(avail) < 2:
        # 「强度」这个词至少要两条输入撑。只剩一条时归一化会把它自己那档放大成 100 分
        # （只看首封时间就能得满分），那是造假而不是估分 —— **交 None，页面显示 —**。
        return {"score": None, "parts": parts, "missing": missing, "weights": dict(WEIGHTS)}
    total = sum(avail.values())
    raw = sum(avail[k] * parts[k]["band"] for k in avail) / total
    return {"score": int(round(raw * 100)), "parts": parts, "missing": missing,
            "weights": {k: avail[k] for k in avail}}


def _hhmm(v: int) -> str:
    s = str(int(v)).zfill(6)
    return "%s:%s" % (s[:2], s[2:4])


def spec_text() -> str:
    """给页面悬停看的算法说明 —— **从上面那三张表现拼**，改了分档页面自动跟着改。"""
    fund = "｜".join("≥%g%% %.2f" % (hi, sc) for hi, sc in FUND_STEPS) + "｜不到 0.05%% %.2f" % FUND_FLOOR
    reopen = "｜".join("≤%d 次 %.2f" % (hi, sc) for hi, sc in REOPEN_STEPS) + "｜≥6 次 %.2f" % REOPEN_FLOOR
    first = "｜".join("%s 及以前 %.2f" % (_hhmm(hi), sc) for hi, sc in FIRST_STEPS) \
            + "｜更晚 %.2f" % FIRST_FLOOR
    return ("封板强度 = 三项加权（%s），满分 100：\n"
            "· 封单资金 ÷ 流通市值（权重 %d）：%s\n"
            "· 开板（炸板）次数（权重 %d）：%s\n"
            "· 首次封板时间（权重 %d）：%s\n"
            "任何一项缺数：这一项不计分、也不按 0 算，权重在其余项上重新归一；"
            "少于两项有数就不给分（显示 —），别让一条输入撑起一个「强度」。\n%s"
            % (SPEC_VERSION, WEIGHTS["fund"], fund, WEIGHTS["reopen"], reopen,
               WEIGHTS["first"], first, _CAVEAT))
