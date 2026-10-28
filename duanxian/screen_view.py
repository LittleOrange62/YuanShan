# -*- coding: utf-8 -*-
"""筛选器那一屏的读层：把 `config.screen_rule` 里登记的条件变成「可选、可跑、可解释」。

**为什么之前那一页没有"应用筛选"的地方**：条件、参数、SQL 模板都在库里，执行引擎
（`app/compute/screener.py`）也写好了，但**没有任何 HTTP 入口调用它** ——
`/api/screen/rules` 只把条件清单端出来，页面因此只能显示"有什么条件"，
点不动、跑不了。用户 2026-10-06 问的「这个页面是干嘛的？没有应用筛选的页面呀？」
就是这条断链。这里补的是那一段：`GET /api/screen` = 按登记的模板实跑一次。
（执行引擎原本零测试 —— 这一层的测试顺带把它的 enum 白名单与 required 校验第一次跑实。）

三条不许糊过去的规矩：

1. **参数按登记的 schema 收**：`required` 缺哪一项就报缺哪一项（不是"筛选失败"），
   `literal` 类参数（列名、比较符）只能取 `enum` 白名单里的值 —— 执行引擎本来就这么判，
   这一层不放宽、也不自己再拼一遍 SQL。
2. **母体数随行交出**。自选池只有几十只时"筛出 8 只"与全市场"筛出 8 只"完全是两件事，
   分母不写出来这一屏就没法读。
3. **截断要说**。命中数撞上 limit 时界面拿到的就是前 limit 行，不写 truncated
   会被读成"只筛出这些"。
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from app.compute import screener
from app.db import engine

#: 候选集。名字与执行引擎的 `universe` 参数一字不差，页面不认第三种写法。
UNIVERSES: dict[str, str] = {
    "watchlist": "自选池（config.watchlist_stock 里登记过的票）",
    "market": "全市场（reference_data.security 里 asset_type=stock 的票）",
}
DEFAULT_LIMIT = 200
#: 上限比执行引擎自己的 MAX_LIMIT(2000) 更严：这一屏是给人在页面上读的，
#: 一次交两千行既没人看也拖慢首屏。要全量请用 CLI。
LIMIT_CHOICES = (50, 100, 200, 500, 1000)


def _params_of(rule: dict) -> list[dict]:
    """把 jsonb 里的 param_schema 摊成页面能直接渲染的控件清单。

    交的是**结构化字段**（不是原样贴 repr 字符串）：`/api/screen/rules` 旧写法走的是
    pandas 那一路，jsonb 到了 pandas 变成字符串 repr，界面就只能"给人看一眼"而无法机读 ——
    要生成输入框就必须拿回真 dict。
    """
    props = screener.schema_props(rule) or {}
    required = set((rule.get("param_schema") or {}).get("required") or [])
    out = []
    for name, spec in props.items():
        spec = spec or {}
        out.append({
            "name": name,
            "type": spec.get("type") or "string",
            "format": spec.get("format"),
            "default": spec.get("default"),
            "required": name in required,
            "enum": spec.get("enum"),
            "literal": bool(spec.get("literal")),
            # 字面类参数（列名 / 比较符）是文本替换进 SQL 的，只能取 enum 白名单
            "hint": ("只能取 " + "/".join(map(str, spec["enum"])) + "（这一档是文本替换进 SQL 的，"
                     "所以白名单是硬门）") if spec.get("enum") else None,
        })
    return sorted(out, key=lambda p: (not p["required"], p["name"]))


def rules_view() -> dict:
    """条件清单（可机读版）。库里只有几种就写几种，不摆成"方案很多"。

    这里直接读表而不是用 `screener.rules()`：那条 SQL 只取启用中的行、也不交 `enabled`，
    而页面要把"停用的那条"也摆出来并标明状态（隐藏停用项会让人以为条件从来只有这些）。
    """
    rows = engine.query(
        "SELECT rule_key, name, param_schema, note, builtin, enabled, "
        "       (sql_template is not null) AS has_sql "
        "  FROM config.screen_rule ORDER BY rule_key", role="reader") or []
    return {
        "available": True,
        "count": len(rows),
        "rules": [{
            "rule_key": r["rule_key"], "name": r["name"], "note": r.get("note"),
            "builtin": bool(r.get("builtin")), "enabled": bool(r.get("enabled")),
            "has_sql": bool(r.get("has_sql")),
            "params": _params_of(r),
        } for r in rows],
        "universes": [{"key": k, "means": v} for k, v in UNIVERSES.items()],
        "limit_choices": list(LIMIT_CHOICES),
        "caveat": (f"库里只有 {len(rows)} 种「已登记条件」。条件的 SQL 模板原文不外显（只给有没有），"
                   "自由 SQL 入口在本项目刻意不做 —— 给一个只读成品项目开自由 SQL 等于自己放大权限。"
                   "跑一次 = 按登记的模板 + 这里给的参数，在指定那一天实筛。"),
        "source": "pg config.screen_rule + app/compute/screener.py",
    }


def _pool_count(universe: str, target: dt.date) -> int | None:
    """母体有多少只票（分母）。"""
    sql = ("select count(distinct d.security_code) as n from market_data.stock_daily d "
           "join reference_data.security s on s.security_code = d.security_code "
           "where d.trade_date = %(d)s and "
           + ("d.security_code in (select distinct security_code from config.watchlist_stock)"
              if universe == "watchlist" else "s.asset_type = 'stock'"))
    row = engine.query_one(sql, {"d": target}, role="reader")
    return int(row["n"]) if row and row.get("n") is not None else None


def _given(query: dict, params: list[dict]) -> dict:
    """按 schema 把 URL 参数收进来：没给的就用登记的默认值。"""
    out: dict[str, Any] = {}
    for p in params:
        raw = query.get(p["name"])
        if raw is None or raw == "":
            if p["default"] is not None:
                out[p["name"]] = p["default"]
            continue
        out[p["name"]] = raw
    return out


def run(rule_key: str, query: dict, *, universe: str = "watchlist",
        date: str = "", limit: int = DEFAULT_LIMIT) -> dict:
    """跑**一条**已登记条件。`query` 是原始 URL 参数（字符串），按 schema 收类型。

    10-14 起这只是 `run_many` 的一格特例：条件本来就可以多条一起用（执行引擎
    `screener.build(conds, logic=…)` 一直是收数组的），单条是它最短的那条路。
    """
    return run_many([{"rule_key": rule_key, "params": query}], universe=universe,
                    date=date, limit=limit, single=rule_key)


def _given_all(conds: list[dict], rmap: dict) -> tuple[list[dict], list[str]]:
    """按各自登记的 schema 收每一条条件的参数。返回 (收好的清单, 缺参说明)。

    缺参说明带上**第几条**：四条条件里第二条缺 `max_pct` 时报"这条还缺"，
    人在页面上才知道动的是哪一格。
    """
    out: list[dict] = []
    missing: list[str] = []
    for i, c in enumerate(conds, 1):
        key = (c.get("rule_key") or "").strip()
        rule = rmap.get(key)
        if not rule:
            missing.append(f"第 {i} 条：没有这种**启用中**的筛选条件「{key}」")
            continue
        specs = _params_of(rule)
        given = _given(c.get("params") or {}, specs)
        lost = [p["name"] for p in specs if p["required"] and p["name"] not in given]
        if lost:
            missing.append(f"第 {i} 条「{rule['name']}」还缺参数：" + "、".join(lost))
            continue
        out.append({"index": i, "rule_key": key, "name": rule["name"],
                    "note": rule.get("note"), "params": given,
                    "needs": specs, "template": rule.get("sql_template") or ""})
    return out, missing


def _indicators_ready(target: dt.date) -> bool | None:
    """这一场有没有基础因子行 —— 读 `analytics.indicator_value` 的条件要先过这道闸。

    为什么必须有这道闸（`app/compute/pattern.py` 头部记着同一条教训）：均线类条件的模板
    是 `EXISTS (… FROM analytics.indicator_value …)`，而**那一场的因子还没算时，EXISTS 一只都不给**，
    页面于是写"这条条件在这一天一只都没命中"—— 那是取数没跑到，不是筛出来空集，
    两者在屏幕上长得一模一样。
    探不到（查询坏了）交 `None`，**不当 False**：判不了就继续跑并说明没验过，
    而不是替库下结论说"这一天没有因子"。
    """
    try:
        n = engine.scalar(
            "SELECT count(*) FROM analytics.indicator_value WHERE trade_date = %s",
            (target,), role="reader")
        return None if n is None else int(n) > 0
    except Exception:  # noqa: BLE001  探不到就是判不了，交 None，不是 0、也不是 False
        return None


#: 一次最多叠几条。再多就说明这套条件该存成方案了，不是该塞进一个 URL。
MAX_CONDS = 6
#: 条件模板里出现这段表名，就说明这一条依赖 d1 已经跑到目标日
IND_TABLE = "analytics.indicator_value"


def run_many(conds: list[dict], *, logic: str = "and", universe: str = "watchlist",
             date: str = "", limit: int = DEFAULT_LIMIT, single: str = "") -> dict:
    """把几条已登记条件拼成一次筛选（`logic` = and / or）。

    `conds` 形如 `[{"rule_key": "ma_position", "params": {"ma": "ma250", "op": "<"}}]`。
    条件之间是拼 WHERE 片段，不是先筛一遍再筛一遍 —— 所以四条一起用与一条一条用
    在母体上是一致的（分母仍是 `universe_count`）。
    """
    if universe not in UNIVERSES:
        return {"available": False,
                "reason": f"候选集只能是 {' / '.join(UNIVERSES)}，收到「{universe}」",
                "rows": []}
    if logic not in ("and", "or"):
        return {"available": False, "reason": f"条件之间的关系只认 and / or，收到「{logic}」",
                "rows": []}
    conds = [c for c in (conds or []) if isinstance(c, dict)]
    if not conds:
        return {"available": False,
                "reason": "一条条件都没有。空条件=不过滤（那是把整个候选集交出来），"
                          "不是一个可用的筛法，所以这里不替你跑",
                "rows": []}
    if len(conds) > MAX_CONDS:
        return {"available": False,
                "reason": f"一次最多叠 {MAX_CONDS} 条条件，收到 {len(conds)} 条",
                "rows": []}

    rmap = screener.rules_map()          # 只含 enabled 的条件（执行引擎那一条 SQL 就这么筛）
    if single and single not in rmap:
        return {"available": False,
                "reason": (f"没有这种**启用中**的筛选条件：「{single}」。现在能用的是："
                           + "、".join(sorted(rmap)) + "（条件是登记的，不能现场造；"
                             "被停用的那条不参与筛选）"),
                "rows": []}

    picked, missing = _given_all(conds, rmap)
    if missing:
        return {"available": False,
                "reason": ("；".join(missing)
                           + "。缺哪一项就说哪一项，不替你猜一个默认值跑一遍"
                             "（猜出来的命中名单会被当成你筛的）"),
                "rows": [],
                "params": picked[0]["params"] if picked else {}}

    lim = max(1, min(int(limit or DEFAULT_LIMIT), max(LIMIT_CHOICES)))
    try:
        target = dt.date.fromisoformat(date) if date else None
        if date:
            probe = target
        else:
            probe = engine.scalar(
                "SELECT max(trade_date) FROM market_data.stock_daily", role="reader")
        # 均线类条件读 indicator_value：那一场没因子行就是取数没跑到，必须先出声
        if probe is not None and any(IND_TABLE in c["template"] for c in picked):
            ready = _indicators_ready(probe if isinstance(probe, dt.date)
                                      else dt.date.fromisoformat(str(probe)[:10]))
            if ready is False:
                return {"available": False,
                        "reason": (f"{probe} 这一场在 `analytics.indicator_value` 里一行都没有 —— "
                                   "是加工链还没跑到这一天，**不是这几条条件一只都没命中**。"
                                   "先去数据加工页看 indicators 那一步，或直接绕开均线类条件"),
                        "rows": [], "date": str(probe)[:10],
                        "conditions": [{"index": c["index"], "rule_key": c["rule_key"],
                                        "name": c["name"], "params": c["params"]} for c in picked]}
            if ready is None:
                unverified = ("（没能确认这一场的基础因子行是否齐：探测本身失败了，"
                              "所以下面的结果按原样交出，不作保证）")
            else:
                unverified = ""
        else:
            unverified = ""
        rows = screener.run([{"rule_key": c["rule_key"], "params": c["params"]} for c in picked],
                            logic=logic, universe=universe, trade_date=target, limit=lim)
    except screener.ScreenError as exc:
        # 参数白名单没过 / 形状不对：原话端出去，那是调用方的错，不是服务的错
        return {"available": False, "reason": str(exc), "rows": [],
                "params": picked[0]["params"] if picked else {},
                "needs": picked[0]["needs"] if picked else []}
    except Exception as exc:  # noqa: BLE001  库那边炸了要说炸在哪，不能变空表
        return {"available": False,
                "reason": f"实跑异常：{type(exc).__name__}: {str(exc)[:200]}",
                "rows": []}

    hit_date = rows[0].get("交易日") if rows else None
    if not hit_date:
        hit_date = engine.scalar("SELECT max(trade_date) FROM market_data.stock_daily",
                                 role="reader")
    used = date or str(hit_date)
    pool = _pool_count(universe, dt.date.fromisoformat(str(used)[:10]))
    columns = list(rows[0].keys()) if rows else []
    return {
        "available": True,
        # 单条也交 conditions 数组：页面只认一种形状，不为了"只选了一条"另拼一套字段
        "conditions": [{"index": c["index"], "rule_key": c["rule_key"], "name": c["name"],
                        "params": c["params"]} for c in picked],
        "logic": logic,
        "rule": ({"rule_key": picked[0]["rule_key"], "name": picked[0]["name"],
                  "note": picked[0]["note"]} if len(picked) == 1 else None),
        "params": picked[0]["params"] if len(picked) == 1 else {},
        "needs": picked[0]["needs"] if len(picked) == 1 else [],
        "date": str(used)[:10],
        "universe": universe, "universe_means": UNIVERSES[universe],
        "universe_count": pool,
        "limit": lim, "rows": rows, "columns": columns,
        "returned": len(rows), "truncated": len(rows) >= lim,
        "caliber": ("命中口径 = 库里登记的这段模板，前端只给参数值，模板原文不外显。\n"
                    + "\n".join(f"{c['index']}. {c['name']}（{c['rule_key']}）："
                                + "; ".join(f"{k}={v}" for k, v in c["params"].items())
                                + (("\n   " + c["note"]) if c["note"] else "")
                                for c in picked)
                    + ("\n条件之间是 " + ("并且（四条全过才算）" if logic == "and"
                                          else "或者（任一条过就算）"))
                    + unverified),
        "source": "pg config.screen_rule → app/compute/screener.py",
    }

