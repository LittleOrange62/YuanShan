# -*- coding: utf-8 -*-
"""给「首页涨停与连板」那一条链用的测试桩：造出与**真读侧同形**的假读数。

2026-10-26 那一屏的母体从"现抓东财池"搬到库里（`analytics.limit_up_pool_daily`，
读侧 `duanxian/limit_up_board.py`），四个原来桩 `astock.em_zt_topic_pool` 的测试文件
必须跟着换桩位。**换桩不是换意图**：排序先后、几天几板对不上要点名、非交易日不许拿
别日顶数、强度少于两项不给分 —— 这些断言一条都不许松。

所以这里只提供一个东西：`fake_board(...)`，它产出的字典**形状与 `board_on()` 一致**
（键少了读侧就会 KeyError，测试当场红 —— 这正是这份支持模块存在的意义：
桩与真契约分叉时先炸在测试里，而不是绿着骗过一整轮）。
`test_limit_up_board_contract.py` 另有一条用例逐键比对这个形状与真实读侧的返回。
"""
from __future__ import annotations

import datetime as dt

from duanxian import limit_up_board

DAY = "2026-10-08"
NOW = dt.datetime(2026, 10, 8, 16, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))


def hhmm(raw: int) -> str:
    """HHMMSS 整数 → 屏幕那个 HH:MM（与读侧 to_char 同一形状）。"""
    s = str(int(raw)).zfill(6)
    return f"{s[:2]}:{s[2:4]}"


def landed_row(code: str, name: str, consec, *, seal=92500, reopen=0, order=None,
               cap=1e9, reason=None, src_high=None, src_seal=None, src_reopen=None,
               board_kind="换手板", at_limit=240, minute_bars=240, close=10.0,
               pct=10.0, amount=1e8, sealed=True, touched=True,
               streak_start=None, missing=None) -> dict:
    """一行落库读数（`limit_up_board.board_on` 从库里读出来的就是这些键）。

    `seal=None` ⇒ 这一场没有分钟线（`seal_basis` 跟着变），与真读侧同一套标注。
    """
    basis = "local" if seal is not None else ("source" if src_seal is not None else "none")
    return {
        "code": code, "security_code": f"{code}.SZ", "name": name,
        "board": "主板", "market": "SZ",
        "sealed": sealed, "touched": touched,
        "consec": consec, "streak_start": streak_start,
        "close": close, "pct": pct, "amount": amount,
        "float_cap": cap, "float_cap_basis": "source", "order_amount": order,
        "reason": reason, "board_kind": board_kind,
        "seal_time": hhmm(seal) if seal is not None else None,
        "seal_raw": seal,
        "src_seal_time": hhmm(src_seal) if src_seal is not None else None,
        "src_seal_raw": src_seal,
        "seal_basis": basis,
        "reopen_count": reopen, "src_reopen": src_reopen,
        "at_limit_minutes": at_limit, "minute_bars": minute_bars,
        "src_high_days": src_high,
        "reason_captured_at": NOW if reason else None,
        "calculated_at": NOW,
        "missing": missing or {},
    }


def fake_board(rows, *, day=DAY, broken=(), dt_count=1, prev_date=None, prev_sealed=None,
               coverage=None, unavailable=None) -> dict:
    """整块读数。`unavailable` 给一句话 ⇒ 走"库里没有这一场"那一支（读侧就是这么交的）。"""
    if unavailable is not None:
        return {"available": False, "reason": unavailable, "date": day,
                "spec_version": limit_up_board.SPEC_VERSION, "lianban_stocks": []}
    sealed = [r for r in rows if r["sealed"]]
    lianban = [r for r in sealed if (r["consec"] or 0) >= 2]
    return {
        "available": True, "date": day, "spec_version": limit_up_board.SPEC_VERSION,
        "rows": list(rows) + list(broken), "sealed": sealed, "broken": list(broken),
        "rows_total": len(rows) + len(broken), "source": "pg",
        "sealed_count": len(sealed), "broken_count": len(broken),
        "dt_count": dt_count, "prev_date": prev_date, "prev_sealed": prev_sealed,
        "lianban": lianban,
        "seal_backfilled": sum(1 for r in rows if r["seal_basis"] == "source"),
        "seal_none": sum(1 for r in rows if r["seal_basis"] == "none"),
        "gap_counts": {}, "coverage": coverage or {},
        # 口径文字用**真读侧那一份**，测试才是在钉"接没接上"而不是桩里写了什么话
        "caliber": limit_up_board.CALIBER_NOTE, "seal_rule": limit_up_board.SEAL_TIME_RULE,
        "gap_texts": limit_up_board.GAP_TEXT,
    }
