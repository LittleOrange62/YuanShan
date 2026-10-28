"""当前持仓 —— **从交易日志的成交明细聚合出来**，不另存一份。

## 为什么不单独存一份持仓

持仓和交易日志本来就是同一件事的两个视角：日志记「我做了哪些成交」，
持仓是「这些成交到现在还剩什么」。各存一份的话，一笔买入要录两次，
漏录或改动不同步就会出现：持仓页显示有仓、风险报告却说没有持仓；
两页的成本与已实现盈亏对不上；而使用者不知道哪一份才是权威账本。

所以这里只做**聚合**：唯一的账本是 `journal`，本模块把它的 fills 折算成
「现在还持有什么」，再叠加实时行情。要改持仓就去改那笔交易的成交明细。

## 边界

只读使用者自己录入的数据 + 公开行情，不产出任何操作建议。
⛔ 本模块的数据**不接入任何 AI prompt**。
"""

from __future__ import annotations

import os
import sys
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_VR = os.path.join(os.path.dirname(_HERE), "vr")
if _VR not in sys.path:                      # vr/ 是上游逐字副本，只读不改
    sys.path.append(_VR)                     # 末尾：插到最前会让 `vr/app.py` 抢走顶层 app/ 包的名


def _quotes(codes: list[str]) -> dict:
    """取实时行情。取不到就返回空 —— 由调用方如实标注，绝不用 0 顶替。

    ⚠️ `price=0` 会让市值算成 0、盈亏算成 −100%，界面上显示成"持仓全部归零"。
    那是彻头彻尾的假事实，所以这里宁可给不出价，也不给一个 0。
    """
    if not codes:
        return {}
    try:
        import astock

        return astock.tencent_quote(codes) or {}
    except Exception:  # noqa: BLE001  行情源挂了不该让持仓页打不开
        return {}


def open_positions(trades: Optional[list[dict]] = None) -> list[dict]:
    """把日志里所有**未平仓**的部分聚合成持仓列表。

    同一只票在多笔交易里都有剩余时合并成一行，成本按剩余股数加权。
    """
    if trades is None:
        from . import journal

        # ⚠️ 必须读整本账。用 list_trades(limit=N) 的话，账本超过 N 条之后，
        #    较早但仍未平仓的记录会被静默漏掉 —— 持仓少一只、在险资金偏小，
        #    而两边都不会报错。
        trades = journal.all_trades()

    agg: dict[str, dict] = {}
    for t in trades:
        st = t.get("settled") or {}
        shares = st.get("open_shares")
        if not shares or shares <= 0:
            continue
        code = str(t.get("code") or "").zfill(6)
        cost = float(st.get("avg_cost") or 0)
        row = agg.setdefault(code, {
            "code": code, "name": t.get("name") or code,
            "shares": 0.0, "_cost_sum": 0.0,
            "trade_ids": [], "playbooks": [], "planned_stops": [],
        })
        row["shares"] += float(shares)
        row["_cost_sum"] += cost * float(shares)
        row["trade_ids"].append(t.get("id"))
        if t.get("playbook"):
            row["playbooks"].append(t["playbook"])
        if t.get("planned_stop") is not None:
            row["planned_stops"].append(float(t["planned_stop"]))

    out = []
    for row in agg.values():
        shares = row.pop("_cost_sum") / row["shares"] if row["shares"] else 0.0
        row["cost"] = round(shares, 4)
        row["shares"] = round(row["shares"], 2)
        out.append(row)
    return sorted(out, key=lambda r: r["code"])


def watchlist_membership(codes: list[str]) -> dict:
    """这些代码在不在自选清单里 —— **市场所有股票 ⊃ 自选 ⊃ 重点关注（持仓）** 那条
    包含关系的实测（用户 2026-10-07 定的三层关系）。

    这里只点名、不兜底（用户选的就是"严格全量，以同花顺为准"）：自选的唯一真源是同花顺本机
    `stockblock.ini`，而那条链每天 20:40 **全量镜像**（ini 里没有的会被删）。所以持仓里
    出现 ini 没有的票时，系统不去改写那份 ini，也不偷偷在库里造一条"本机补充"，
    而是把"这几只明天同步后不在自选中"摊开给人看 —— 要它常驻，动作在同花顺客户端里做一次。

    读不到自选清单时交 `available:false`，**不许把"读不到"折成"都不在自选里"**：
    那会造出一屏假缺口，而真原因只是库没连上。
    """
    if not codes:
        return {"available": True, "codes": [], "missing": []}
    try:
        from . import pg

        df = pg.fetch_df(
            "select distinct security_code from config.watchlist_stock "
            "where security_code = any(%s::text[])", (list(codes),))
    except Exception as exc:  # noqa: BLE001  包含关系是附加读数，不能拖垮持仓页
        return {"available": False, "codes": [], "missing": [],
                "reason": f"自选清单读不到（{type(exc).__name__}），所以判不了这几只在不在自选里"}
    if df is None:
        return {"available": False, "codes": [], "missing": [],
                "reason": "自选清单查库失败（库没连上或查询被超时打断），"
                          "这不是「这些票都不在自选里」"}
    have = {str(c) for c in df.iloc[:, 0].tolist()}
    full = {c for c in codes if (c or "").strip()}
    return {"available": True, "codes": sorted(have),
            "missing": sorted(c for c in full if c not in have)}


def _sync_mode() -> dict:
    """自选同步现在按哪一档跑（`watchlist.sync_mode`）。默认值只住 `jobs.WATCHLIST_DEFAULTS`。"""
    try:
        from . import jobs

        merged, warns = jobs.settings("watchlist", jobs.WATCHLIST_DEFAULTS)
        mode = str(merged.get("sync_mode") or "full")
        return {"sync_mode": mode,
                "sync_meaning": ("全量更新：以同花顺本机 stockblock.ini 为准，ini 里删掉的组/成员这里也删"
                                 if mode != "append" else
                                 "增量添加：只加不删，库里留着同花顺已经不要的那些票"),
                "sync_warning": warns[0] if warns else None}
    except Exception as exc:  # noqa: BLE001
        return {"sync_mode": None, "sync_meaning": f"同步模式读不到（{type(exc).__name__}）"}


def report() -> dict:
    """持仓 + 实时行情。行情取不到的逐行标出来，并让汇总也标成不完整。"""
    positions = open_positions()
    if not positions:
        return {"available": True, "holdings": [], "total": None,
                "containment": {"available": True, "codes": [], "missing": []},
                **_sync_mode(),
                "note": "当前没有未平仓的交易 —— 持仓由交易日志的成交明细聚合而来，"
                        "要建仓就去交易日志记一笔。"}

    q = _quotes([p["code"] for p in positions])
    rows, stale = [], 0
    mv_sum = cost_sum = 0.0
    for p in positions:
        quote = q.get(p["code"]) or {}
        price = quote.get("price")
        ok = isinstance(price, (int, float)) and price > 0
        if not ok:
            stale += 1
        cost_amt = p["cost"] * p["shares"]
        mv = (price * p["shares"]) if ok else None
        if ok:
            mv_sum += mv
            cost_sum += cost_amt
        rows.append({
            **p,
            "name": quote.get("name") or p["name"],
            "price": price if ok else None,
            "quote_ok": ok,                       # ⚠️ 前端据此显示"行情不可用"而不是 0
            "cost_amount": round(cost_amt, 2),
            "market_value": round(mv, 2) if ok else None,
            "pnl": round(mv - cost_amt, 2) if ok else None,
            "pnl_pct": round((price / p["cost"] - 1) * 100, 2) if ok and p["cost"] else None,
            "planned_stop": min(p["planned_stops"]) if p["planned_stops"] else None,
        })
    containment = watchlist_membership([str(r.get("code") or "") for r in rows])
    for r in rows:
        # 逐行也带一个标记：页面在范围筛选（只看自选/只看某组）之外，还要能一眼看出
        # "这只票在不在自选里"。读不到自选清单时是 null，不是 false。
        r["in_watchlist"] = (None if not containment["available"]
                             else str(r.get("code") or "") in set(containment["codes"]))
    return {
        "available": True,
        "holdings": rows,
        "stale_count": stale,
        "containment": containment,
        **_sync_mode(),
        # ⚠️ 有取不到行情的标的时，汇总必须标成不完整 —— 否则它看着是个确切数字，
        #    实际上少算了几只，而界面上看不出来。
        "total": {
            "market_value": round(mv_sum, 2), "cost": round(cost_sum, 2),
            "pnl": round(mv_sum - cost_sum, 2),
            "pnl_pct": round((mv_sum / cost_sum - 1) * 100, 2) if cost_sum else None,
            "complete": stale == 0,
            "counted": len(rows) - stale, "of": len(rows),
        },
        "note": "持仓由交易日志的成交明细聚合而来，不另存一份 —— 改持仓请去改那笔交易的成交明细。",
    }
