"""数据质量闸与筛选器的只读取数 —— FT 的 /data、/screen 两页对应物。

规则本身**配置即数据**（`config.quality_rule`；条数与 `blocking` 那几列**一律现查，注释里不背** ——
2026-10-18 快照是 30 条登记、全部 enabled、其中 12 条 blocking 且都属 error 级），
判定结果在 `operations.quality_result`（同一批数由查询现算，2026-10-18 快照 969 行：914 过 / 55 败 / 0 空）。

三条实测出来的、必须随数据交出的事实：

1. **最近的审计是残缺的**：09-23 那天跑了当时登记的全部规则，而 09-24 只有 2 条、09-25 只有 1 条
   （且都是备份自检）。页面若只显示"最近一次：通过"，就是在**用残缺样本给人心安** ——
   FT 原版那类 `catch→[]`、`continue` 的毛病（见 09 §7 D-41）我们不继承。
   所以这里交出 `rules_audited` 与 `rules_total` 两个数，让差值自己说话。
2. **`trade_date` 为 NULL 的行存在**（4 行，8-26 起）：那是非场次类作业（如备份）写的，
   不是脏数据；按日期筛时要显式带上，否则会被误报成"那几天没审计"。
3. **筛选器只有 5 种登记条件，`config.screen_preset` 是 0 行**：没有用户预设。
   页面上不能摆出"有很多方案可选"的样子 —— 那就成了 FT 那种看着可信的空壳。
"""

from __future__ import annotations

from . import pg


def _plain(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if hasattr(v, "isoformat") and not isinstance(v, float):
        return v.isoformat()
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    # numeric 能存 NaN、pandas 会把 NULL 变 NaN。它是"算不出"，不是 0。
    return None if f != f or f in (float("inf"), float("-inf")) else f


def _rows(sql, params=()):
    df = pg.fetch_df(sql, params)
    if df is None:
        return None
    return [{k: _plain(v) for k, v in r.items()} for r in df.to_dict("records")]


def _latest_audit_date() -> "str | None":
    df = pg.fetch_df("select max(trade_date)::text d from operations.quality_result")
    if df is None or not len(df) or df["d"].iloc[0] is None:
        return None
    return str(df["d"].iloc[0])


def quality(date: str = "") -> dict:
    """质量闸总览：登记在册的规则 + 该场判定 + 最近一次完整审计是哪天。

    `date` 省略时用**有审计记录的最近一场**，不是"今天"—— 今天很可能根本没跑审计，
    拿今天当默认会让页面显示"零条规则"像是系统坏了。
    """
    d = date or _latest_audit_date() or ""
    rules = _rows("""select rule_key, name, scope, severity, target, check_kind, threshold,
                            blocking, enabled, description
                     from config.quality_rule order by blocking desc, severity, rule_key""")
    if rules is None:
        return {"available": False, "date": d or None,
                "reason": "读 config.quality_rule 失败（权限或表不存在）"}
    results = _rows(
        """select rule_key, passed, actual, expected, sample, run_at
           from operations.quality_result
           where trade_date = %s or (trade_date is null and run_at::date = %s::date)
           order by rule_key""",
        (d, d)) or []
    by = {}
    for r in results:                      # 同规则多行时取最后一次
        by[r["rule_key"]] = r
    for rule in rules:
        got = by.get(rule["rule_key"])
        rule["passed"] = None if got is None else got["passed"]
        rule["actual"] = got.get("actual") if got else None
        rule["expected"] = got.get("expected") if got else None
        rule["run_at"] = got.get("run_at") if got else None

    audited = [r for r in rules if r["passed"] is not None]
    failed = [r for r in audited if not r["passed"]]
    # 结果表里存在**未登记规则**的行（实测 09-25 那条 `backup_daily` 不在当时登记的那批规则之内）。
    # 只报"本场审了 N 条"会让人以为页面坏了，其实是有规则在登记之外写结果 —— 这是两回事，
    # 分开交出去，让人看见"谁在写"而不是只看见"没数"。
    orphan = [r for r in results if r["rule_key"] not in {x["rule_key"] for x in rules}]
    full = _rows("""select trade_date::text d, count(distinct rule_key)::int n
                    from operations.quality_result
                    where trade_date is not null group by 1 order by 1 desc limit 14""") or []
    last_full = next((x["d"] for x in full if x["n"] >= len(rules)), None)
    return {
        "available": True, "date": d, "rules": rules,
        "rules_total": len(rules),
        "rules_blocking": sum(1 for r in rules if r["blocking"] and r["enabled"]),
        "rules_audited": len(audited),
        "failed": [r["rule_key"] for r in failed],
        "unregistered_results": [{"rule_key": o["rule_key"], "passed": o["passed"],
                                  "run_at": o["run_at"]} for o in orphan],
        "last_full_audit": last_full,
        "caveat": (f"本场只审了 {len(audited)}/{len(rules)} 条登记规则；"
                   f"最近一次**全量**审计是 {last_full or '没有记录'}。"
                   "残缺样本下'全部通过'不等于数据没问题。"
                   + (f" 另有 {len(orphan)} 条结果来自**未登记**规则"
                      f"（{', '.join(sorted({o['rule_key'] for o in orphan}))}），"
                      "不在 config.quality_rule 里。" if orphan else "")
                   + "`trade_date` 为 NULL 的记录是非场次类作业（如备份自检），已按日期一并带出。"),
        "source": "pg", "requests": 0,
    }


def screens() -> dict:
    """已登记的筛选条件（FT `/screen` 对应物）。"""
    rows = _rows("""select rule_key, name, param_schema, builtin, enabled, note,
                           (sql_template is not null) as has_sql
                    from config.screen_rule order by rule_key""")
    if rows is None:
        return {"available": False,
                "reason": "读 config.screen_rule 失败（权限或表不存在）"}
    presets = _rows("select count(*)::int n from config.screen_preset") or [{"n": 0}]
    return {
        "available": True, "rules": rows, "count": len(rows),
        "presets": presets[0]["n"],
        "caveat": (f"库里只有 {len(rows)} 种「已登记条件」、用户预设 {presets[0]['n']} 条 —— "
                   "页面不要摆成「有很多方案可选」。`sql_template` 原文不外显（只给 `has_sql`），"
                   "自由 SQL 入口在本项目刻意不做（见 docs/FinanceTool页面映射.md 判定 13）。"),
        "source": "pg", "requests": 0,
    }
