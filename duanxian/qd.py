"""quantdash 口径轨（PG schema `qd`）的只读读取层。

与 `pg_sources.py` 的分工不同：那份是"本地库顶替在线源"，产出形状必须和在线
取数同形；这份是**并列展示另一套算法**，形状由 `页面展示工作指南/02 §8` 定，
不需要和任何东西同形 —— 它就是给 `/qd-compare` 那一屏用的。

三条硬规矩（02 §1）：
  · `own`（本地口径）与 `qd`（quantdash 口径）**分两个键交出，永不合并**。
    `own=None` 表示本轨没这个能力，界面只能显示"本轨暂无"，不许拿 qd 的值顶上。
  · 算不出来的就是 None，**不折成 0**。0 会被下游 AI 当成真实数值继续推理。
  · 取不到一律 `{available: False, reason}`，**不许回落到"今天"、也不许拿上一个
    有数据的交易日冒充**。date 缺省时取 `qd` 表里最新的 trade_date。

只查库，绝不建表改表（YuanShan 侧身份是只读 `ft_reader`）。
"""

from __future__ import annotations

import datetime
import decimal
import json
import logging
from pathlib import Path

from . import pg

logger = logging.getLogger(__name__)

# qd.sentiment_cycle 里属于"指标"的列。stage/risk/volume/confidence 单独摆，
# 其余全进 metrics 交给界面按 key 取 —— 加列不用改前端类型声明。
_METRIC_COLS = (
    "sentiment_value", "board_height", "leader_board_count", "high_board_count",
    "limit_up_count", "limit_down_count", "touched_count", "broken_touched_count",
    "first_board_count", "first_board_ratio_pct", "high_board_ratio_pct",
    "relay_count", "success_rate_pct", "avg_board_gain", "premium_score",
    "broken_rate_sealed_pct", "broken_rate_touched_pct",
    "broken_repair_rate_lag1_pct", "big_face_count_lag1",
    "big_face_repair_rate_lag1_pct", "a_kill_count_lag1", "weak_count_lag1",
    "leader_next_close_pct", "rise_count", "fall_count", "flat_count",
    "amount_yi", "amount_change_pct",
)
_METRIC_COLS_SQL = ", ".join(_METRIC_COLS)


def _plain(v):
    """把 Decimal/date/time 折成 JSON 可序列化的原生值；None 保持 None。"""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return None if v != v else v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return str(v)


def _rows(sql: str, params=()):
    """跑一条查询，返回 list[dict]（值已 plain 化）；失败/无库返回 None。"""
    df = pg.fetch_df(sql, params)
    if df is None:
        return None
    return [{k: _plain(v) for k, v in row.items()} for row in df.to_dict("records")]


def _unavailable(table: str, date: str) -> dict:
    """说清"为什么没数"，而不是给个空壳 —— 空壳会被读成"quantdash 算法认为没有"。"""
    if not pg.enabled():
        return {"available": False, "date": date or None,
                "reason": "本地库未启用（VIBE_PG=0 或未配 PG 连接），quantdash 口径轨无从读取"}
    if not date:
        return {"available": False, "date": None,
                "reason": f"{table} 里还没有任何已算好的场次，本地计算尚未覆盖该项"}
    return {"available": False, "date": date,
            "reason": f"{table} 里 {date} 这一场没有数据（未回填或当日非交易日），"
                      "不回落到邻近交易日 —— 邻近交易日的数不是这一场的答案"}


def _resolve_date(table: str, date: str) -> str:
    """'YYYYMMDD'/'YYYY-MM-DD' 归一；空则取该表最新 trade_date。"""
    if date:
        s = str(date).strip().replace("-", "")
        if len(s) == 8 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        return str(date).strip()
    return pg.latest_session(table) or ""


def _compute_local(kind: str, date: str):
    """走 YuanShan 自己的计算层；没实现或算不出就返回 None 让调用方退回读 PG 表。

    这一层是"能力落在本项目里"的落点（用户裁定）。PG `qd` 表只是对照基准 ——
    它自己有已记录的缺陷（见 09 §7 D-43），所以不能反过来当唯一来源。
    """
    if not date:
        return None
    from . import qd_calc, qd_ladder
    impl = {
        "sentiment": (qd_calc, getattr(qd_calc, "compute_sentiment", None), _shape_sentiment),
        "ladder": (qd_ladder, getattr(qd_ladder, "compute", None), _shape_ladder),
    }.get(kind)
    if impl is None or impl[1] is None:
        # 找不到计算函数必须出声：静默退回读表会让人以为"本地计算已生效"，
        # 实际拿到的还是基准表那份带 D-43 的数。
        logger.warning("qd 本地计算层没有 %s 的实现，本场次退回读 qd 表", kind)
        return None
    _, fn, shape = impl
    try:
        hit = qd_calc.cached(kind, date)
        mine = hit if hit is not None else fn(date)
    except Exception as exc:  # noqa: BLE001  本地算崩了要能退回读表，而不是白屏
        logger.warning("qd 本地计算失败（退回读 qd 表）%s %s: %s", kind, date, exc)
        return None
    if not mine:
        return None
    if hit is None:
        qd_calc.remember(kind, date, mine)
    return shape(kind, date, mine)


def _shape_sentiment(kind, date, mine):
    return {
        "available": True, "date": date, "own": None,
        "qd": {
            "stage_ui": mine.get("stage_ui"), "stage_mcp": mine.get("stage_mcp"),
            "stage_feishu": mine.get("stage_feishu"),
            "stage_reasons": mine.get("reasons") or [],
            "confidence": mine.get("confidence"), "risk_level": mine.get("risk_level"),
            "volume_state": mine.get("volume_state"),
            "metrics": mine.get("metrics") or {},
            "deviation": _deviation_of("qd.sentiment_cycle", date),
        },
        "scope": mine.get("scope"), "universe": mine.get("universe"),
        "spec_version": mine.get("spec_version"), "source": mine.get("source"),
        "computed_by": "yuanshan",
    }


def _shape_ladder(kind, date, mine):
    return {
        "available": True, "date": date,
        "seal_time_available": mine.get("seal_time_available"),
        "tiers": mine.get("tiers") or [],
        "leader": mine.get("leader"),
        "board_height": mine.get("board_height"),
        "deviation": mine.get("deviation") or [],
        "spec_version": mine.get("spec_version"), "source": mine.get("source"),
        "computed_by": "yuanshan",
    }


def _deviation_of(table: str, date: str):
    """偏差清单：优先取基准表里那一份（它逐条带出处），没有就交空数组。"""
    rows = _rows(f"select deviation from {table} where trade_date = %s", (date,))
    return (rows[0].get("deviation") if rows else None) or []


def sentiment_cycle(date: str = "") -> dict:
    """情绪周期（quantdash 口径）。

    三个 stage 列并存是**事实不是 bug** —— quantdash 自己的页面、它自己的 AI
    （MCP）、它的飞书机器人用的是三套互相矛盾的阈值，读到的是不同阶段。
    所以这里三列都交出，不一致由界面打 ⚠。

    `own` 固定 None：本轨（YuanShan）没有情绪阶段算法，"情绪五档"只有 UI。
    补上之前界面必须如实显示"本轨暂无阶段算法"，不许拿 qd 的值冒充。
    """
    table = "qd.sentiment_cycle"
    d = _resolve_date(table, date)

    # **先在 YuanShan 内算**（`qd_calc`，从只读日线现算）。PG 的 `qd` 表退为兜底与
    # 对照基准 —— 用户裁定"好用的东西要落在 YuanShan 里，不是存在其他地方"。
    local = _compute_local("sentiment", d)
    if local is not None:
        return local
    if not d or not pg.session_has(table, d):
        return _unavailable(table, d)
    rows = _rows(
        f"""
        select trade_date::text as trade_date, spec_version, source, deviation,
               calculated_at::text as calculated_at,
               stage_ui, stage_mcp, stage_feishu, reasons,
               confidence, risk_level, volume_state, scope,
               {_METRIC_COLS_SQL}
        from {table} where trade_date = %s
        """,
        (d,),
    )
    if not rows:
        return _unavailable(table, d)
    r = rows[0]
    metrics = {c: r.get(c) for c in _METRIC_COLS}
    return {
        "available": True,
        "date": r["trade_date"],
        "own": None,
        "qd": {
            "stage_ui": r.get("stage_ui"),
            "stage_mcp": r.get("stage_mcp"),
            "stage_feishu": r.get("stage_feishu"),
            # 库里列名是 reasons，契约要的是 stage_reasons（02 §8.1 定了形状）。
            "stage_reasons": r.get("reasons") if r.get("reasons") is not None else [],
            "confidence": r.get("confidence"),
            "risk_level": r.get("risk_level"),
            "volume_state": r.get("volume_state"),
            "metrics": metrics,
            "deviation": r.get("deviation") or [],
        },
        "scope": r.get("scope"),
        "spec_version": r.get("spec_version"),
        "source": r.get("source"),
        "calculated_at": r.get("calculated_at"),
    }


def board_ladder(date: str = "") -> dict:
    """连板天梯 + 龙头 + 板高度（quantdash 口径）。

    `seal_time_available` 是这一屏最要紧的字段：历史里只有 2026-09-22 有分钟线，
    其余交易日**封板时间根本不可得**。quantdash 在这种情况下用代码哈希编造一个
    时间（09 §7 的 D-28），页面看起来完全正常。我们不跟：交 None，由界面整列灰底。

    `leader_tie_break` 必须交出 —— 它回答"龙头是怎么选出来的"，而当我们没有
    封板时间时，那个选择已经**不等价于** quantdash 的原口径。
    """
    table = "qd.board_ladder"
    d = _resolve_date(table, date)

    # 先在 YuanShan 内算，PG 表退为兜底与对照（同上）。
    local = _compute_local("ladder", d)
    if local is not None:
        return local
    if not d or not pg.session_has(table, d):
        return _unavailable(table, d)

    tiers = _rows(
        f"""select board_tier, tier_label, stock_count, is_highest_tier, deviation, source
            from {table} where trade_date = %s order by board_tier desc""",
        (d,))
    members = _rows(
        f"""select security_code, stock_name, board_tier, board_count,
                   industry_name, concept_name, limit_up_pct_regime, pct_change_actual,
                   is_one_word, is_turnover_board, turnover_rate_pct,
                   first_seal_time::text as first_seal_time, seal_time_available,
                   price_close, amount_yuan, source
            from qd.board_ladder_member where trade_date = %s
            order by board_tier desc,
                     (first_seal_time is null) asc, first_seal_time asc,
                     amount_yuan desc nulls last, security_code asc""",
        (d,))
    leader = _rows(
        """select leader_security_code, leader_stock_name, leader_board_count, leader_count,
                  second_highest_board, three_plus_count, continued_count,
                  leader_tie_break, leader_is_one_word, leader_continued,
                  leader_next_open_pct, leader_next_close_pct,
                  leader_status_label, leader_status_code, deviation
           from qd.board_ladder_leader where trade_date = %s""",
        (d,))
    height = _rows(
        """select main_board_highest, main_board_highest_names,
                  main_board_second_highest, chinext_highest,
                  star_highest, star_highest_names,
                  bj_highest, bj_highest_names, deviation
           from qd.board_height where trade_date = %s""",
        (d,))
    if tiers is None:
        return _unavailable(table, d)

    members = members or []
    by_tier: dict = {}
    for m in members:
        by_tier.setdefault(m.get("board_tier"), []).append(m)

    return {
        "available": True,
        "date": d,
        # 只要该日有任何一条真封板时间，这一列才算可用；否则界面整列降级 + 横幅。
        "seal_time_available": any(m.get("seal_time_available") for m in members),
        "tiers": [{
            "board_tier": t.get("board_tier"),
            "label": t.get("tier_label"),
            "stock_count": t.get("stock_count"),
            "is_highest_tier": t.get("is_highest_tier"),
            "members": by_tier.get(t.get("board_tier"), []),
        } for t in tiers],
        "leader": (leader[0] if leader else None),
        "board_height": (height[0] if height else None),
        "deviation": (leader[0].get("deviation") if leader else None) or [],
        "spec_version": _spec_version(table, d),
    }


def _spec_version(table: str, date: str):
    df = pg.fetch_df(f"select max(spec_version) as v from {table} where trade_date = %s", (date,))
    if df is None or not len(df):
        return None
    v = df["v"].iloc[0]
    return None if v is None else str(v)


def sector_cycle(date: str = "") -> dict:
    """板块周期（quantdash 口径）= 轮动矩阵 + 持续性统计。

    ⚠️ 这**不是**本轨 `analytics.ignition` 的五档生命周期阶段（那个走
    `/api/market/sector-cycle`，界面另有自己的块）。quantdash 的"板块周期"
    根本没有阶段枚举，它回答的是"领涨板块换了几手、谁连庄最久、强度在增还是减"。
    两块各自独立呈现，不许合并成一张表（02 §5）。
    """
    table = "qd.sector_rotation"
    d = _resolve_date(table, date)
    if not d or not pg.session_has(table, d):
        return _unavailable(table, d)

    matrix = _rows(
        """
        with days as (
            select distinct trade_date from qd.sector_rotation
            where trade_date <= %s order by 1 desc limit 5
        )
        select r.trade_date::text as trade_date, r.sector_code, r.sector_name,
               r.sector_type, r.day_rank, r.pct_change,
               r.up_count, r.down_count, r.member_count, r.window_size, r.source
        from qd.sector_rotation r
        join days d on d.trade_date = r.trade_date
        order by r.trade_date desc, r.day_rank asc, r.sector_name asc
        """,
        (d,))
    persistence = _rows(
        """select current_leader_name, current_leader_code, streak_days,
                  top_three_appearances, strength_delta_pct,
                  strongest_repeat_name, strongest_repeat_code, window_days,
                  window_start::text as window_start, deviation, source
           from qd.sector_persistence where trade_date = %s""",
        (d,))
    if matrix is None:
        return _unavailable(table, d)

    # 界面那张矩阵表的**每一行是一个交易日**、格子里放当日涨幅前 8 的板块，
    # 所以必须按日期分组交出 —— 平铺 8 行会被当成 8 个日期、整表渲染成 "—"。
    by_day: dict = {}
    for r in matrix:
        day = by_day.setdefault(r["trade_date"], {
            "trade_date": r["trade_date"],
            # `window_size` 存的是**参与排名的板块总数**（universe），不是"取前几名"。
            # 写成"取前 N"会造出一个看着可信的假口径（契约里 top_n=24 是另一回事）。
            "universe": r.get("window_size"),
            "items": [],
        })
        day["items"].append({k: v for k, v in r.items()
                             if k not in ("trade_date", "window_size")})
    for day in by_day.values():
        day["sector_types"] = sorted({i["sector_type"] for i in day["items"]
                                      if i.get("sector_type")})

    days = list(by_day.values())
    latest = days[0] if days else None
    return {
        "available": True,
        "date": d,
        "rotation": days,
        "persistence": (persistence[0] if persistence else None),
        "scope": (f"参与排名板块 {latest['universe']} 个"
                  f"（{'+'.join(latest['sector_types']) or '类型未知'}）；"
                  f"矩阵只留每日涨幅前 8，共 {len(days)} 个交易日")
                 if latest else "qd.sector_rotation 里这一场没有行",
        "universe": latest["universe"] if latest else None,
        "sector_types": latest["sector_types"] if latest else [],
        "deviation": (persistence[0].get("deviation") if persistence else None) or [],
        "spec_version": _spec_version(table, d),
        "source": (matrix[0].get("source") if matrix else None),
    }


# ── 区间分布与本地快讯 ─────────────────────────────────────────────
# 双轨契约 02 §2 / §3 / §6 要求上界面的两块。2026-09-24 用户裁定边界解除后由
# 页面展示会话直接实现（原"不碰后端"的分工作废）。

#: 三套判定各自的**优先级顺序**（命中即返回，后面的档再也轮不到）。
#: 顺序与取值抄自 `qd_calc.py:247-277`，不是从这里推的。
#: 零值档必须照样交出并列出："冰点一次都没出现过"本身就是结论。
STAGE_PRIORITY: dict[str, list[str]] = {
    "stage_ui": ["退潮", "冰点", "修复", "主升", "试错", "分歧"],
    "stage_mcp": ["退潮", "冰点", "修复", "主升", "试错", "分歧"],
    "stage_feishu": ["退潮", "修复", "主升", "分歧震荡"],
}

#: 龙头并列时的四种选法；后三种不是 quantdash 原口径（02 §4）。
TIE_BREAK_ORDER = ["seal_time", "amount_no_seal_time", "code_asc", "no_candidate"]


def distribution(days: int = 0) -> dict:
    """三套 stage 判定的区间分布 + 龙头 tie-break 分布。

    为什么非要有它：单看一天，"天天退潮"像故障；把上千天摊开才看得见
    **quantdash 优先级链自身的偏置** —— 退潮排第一且带着"修复率<20"这类常态
    条件，于是永远在它后面的冰点一次都轮不到。这是它逻辑的性质，不是算错。

    只统计、不判定：不改任何 stage 计算，纯粹把既有结果按档计数。
    """
    if not pg.enabled():
        return {"available": False, "days": None,
                "reason": "本地库未启用（VIBE_PG=0 或未配 PG 连接），区间分布无从统计"}

    n = int(days) if days and int(days) > 0 else 0
    limit = f" limit {n}" if n else ""
    stage_rows = _rows(
        "select trade_date, stage_ui, stage_mcp, stage_feishu, source "
        f"from qd.sentiment_cycle order by trade_date desc{limit}")
    if not stage_rows:
        return {"available": False, "days": None,
                "reason": "qd.sentiment_cycle 里还没有任何已算好的场次，区间分布无从统计"}

    counts: dict[str, dict[str, int]] = {}
    seen: dict[str, set] = {}
    for col in STAGE_PRIORITY:
        c: dict[str, int] = {}
        for row in stage_rows:
            v = row.get(col)
            key = str(v) if v not in (None, "") else "（未判定）"
            c[key] = c.get(key, 0) + 1
            seen.setdefault(col, set()).add(key)
        # 枚举顺序在前、数据里冒出来的新值在后 —— 新档位不能被静默吞掉
        ordered = {k: c.get(k, 0) for k in STAGE_PRIORITY[col]}
        ordered.update({k: v for k, v in sorted(c.items()) if k not in ordered})
        counts[col] = ordered

    tie_rows = _rows(
        "select leader_tie_break from qd.board_ladder_leader"
        + (f" where trade_date >= (select min(trade_date) from "
           f"(select trade_date from qd.sentiment_cycle order by trade_date desc limit {n}) t)"
           if n else "")
    )
    tie: dict[str, int] = {}
    if tie_rows is not None:
        for row in tie_rows:
            key = str(row.get("leader_tie_break") or "（未给出）")
            tie[key] = tie.get(key, 0) + 1
        tie.update({k: 0 for k in TIE_BREAK_ORDER if k not in tie})

    dates = sorted({str(r["trade_date"]) for r in stage_rows})
    return {
        "available": True,
        "days": len(dates),
        "from_date": dates[0], "to_date": dates[-1],
        "stage_distribution": counts,
        "stage_priority": STAGE_PRIORITY,
        "never_seen_stages": {col: [s for s in STAGE_PRIORITY[col] if s not in seen[col]]
                              for col in STAGE_PRIORITY},
        "tie_break_distribution": tie,
        "sample_total": {"stage": len(stage_rows), "tie_break": len(tie_rows or [])},
        # 诚实声明统计口径的来源：目前 stage 结果来自 PG 基准表 qd.*，
        # 本地重算（qd_calc）目前只覆盖 sentiment 单场次，还没有区间汇总。
        "basis": "qd.sentiment_cycle / qd.board_ladder_leader（PG 基准表，逐场统计）",
        "observed_sources": sorted({str(r.get("source")) for r in stage_rows if r.get("source")}),
        "reason": None,
    }


def news(date: str = "", limit: int = 200) -> dict:
    """只读 YuanShan 本地快讯 jsonl（由 `duanxian/newswire.py` 落的）。

    三条纪律（02 §6）在这里就定死，界面只负责原样显示：
      · date 缺省取**目录里最新那份**，不是"今天"（今天可能没采、也可能没开盘）；
      · 没有文件 / 当天没采 → `available=False` + `reason`，界面绝不回落示例快讯；
      · `sentiment` / `level` 为 None 就是 None，**不折成"中性"**。
    """
    from .paths import data_path

    root = data_path("news")
    files = sorted(Path(root).glob("news-*.jsonl")) if Path(root).is_dir() else []
    if not files:
        return {"available": False, "date": None, "items": [],
                "reason": f"本地快讯目录里还没有任何文件（{root}）。快讯不可回补，"
                          "当天没采就永久没有 —— 先让采集常驻起来，别拿旧场次顶"}

    picked = files[-1]
    if date:
        want = str(date).strip().replace("/", "-")[:10]
        hit = [f for f in files if f.name == f"news-{want}.jsonl"]
        if not hit:
            return {"available": False, "date": want, "items": [],
                    "reason": f"{want} 没有采集文件（本地快讯按天落盘，只保留实采到的日子）。"
                              "不回落到邻近日期 —— 邻近场次的快讯不是这一天的答案"}
        picked = hit[0]

    day = picked.stem.removeprefix("news-")
    items: list[dict] = []
    bad_lines = 0
    with picked.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                bad_lines += 1
                continue
            if not isinstance(rec, dict):
                bad_lines += 1
                continue
            items.append({
                "item_id": rec.get("item_id"),
                "title": rec.get("title"),
                "content": rec.get("content"),
                "url": rec.get("url"),
                "source": rec.get("source"),
                "published_at": rec.get("published_at"),
                "captured_at": rec.get("captured_at"),
                "level": rec.get("level"),
                "sentiment": rec.get("sentiment"),
                "news_type": rec.get("news_type"),
                "logic_source": rec.get("logic_source"),
                "spec_version": rec.get("spec_version"),
                "deviation": rec.get("deviation") or [],
            })

    def key(x: dict) -> str:
        return str(x.get("published_at") or x.get("captured_at") or "")

    items.sort(key=key, reverse=True)
    total = len(items)
    cap = max(1, min(int(limit or 200), 500))
    return {
        "available": total > 0,
        "date": day,
        "items": items[:cap],
        "count": total,
        "truncated": total > cap,
        "corrupt_lines": bad_lines,
        "file": str(picked),
        # 存储里没有个股关联字段，按股过滤只能靠标题文本匹配 —— 那正是 02 §6
        # 禁止的"把无关快讯塞给某股"。要支持请 newswire 落东财 `stockList`。
        "supports_code_filter": False,
        "reason": None if total > 0 else (
            f"{day} 的采集文件是空的（0 条）。不是接口坏了，是那一天确实没采到东西"),
    }
