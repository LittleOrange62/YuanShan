"""MCP 服务装配：把登记表里的只读端点逐个注册成智能体能调的工具。

启动：
    python -m mcp_service                                   # stdio（本机智能体自己拉起）
    python -m mcp_service --transport streamable-http --port 8920   # 局域网/常驻
    python -m mcp_service --check-tools                     # 不起会话，只打印装配结果

工具描述由登记表生成，不在这里抄第二遍标签；A/B 层每条必须有同名函数，
tests/test_mcp_registry_sync.py 会逐条核对。
"""
from __future__ import annotations

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import client
from .capabilities import (AGENT_TOOLS, CURATED_BY_ID, SERVICE_NAME, capability_entry,
                           registry_payload, usable_endpoints)


def tool_description(capability_id: str) -> str:
    entry = capability_entry(CURATED_BY_ID[capability_id])
    params = "、".join(p["name"] for p in entry["params"]) or "无"
    tables = "、".join(entry["tables"]) or "现场聚合，不落新表"
    return (f"{entry['label']}｜屏幕落点 {entry['where']}｜来源 GET {entry['path']}｜读 {tables}"
            f"｜可传参数：{params}（refresh 不接受，MCP 侧只读不触发重算）"
            + (f"｜{entry['note']}" if entry["note"] else ""))


def _forward(path: str, **params: Any) -> dict[str, Any]:
    return client.fetch(path, {k: v for k, v in params.items() if v not in (None, "")})


def build_server() -> FastMCP:
    mcp = FastMCP(SERVICE_NAME, instructions=(
        "YuanShan 展示层数据的只读 MCP 服务：屏幕上那些指标排名与数据加工产出，"
        "按登记的原样取回，不重算、不建议、不写任何东西。"
        "不知道有什么可取时先调 list_capabilities；判数据新不新先调 data_freshness。"))

    # ---------------- A 层：全市场榜与数据加工产出 ----------------
    @mcp.tool(name="quadrant_board", description=tool_description("quadrant_board"))
    def quadrant_board(date: str = "", min_x: float | None = None, min_y: float | None = None,
                       limit: int = 200) -> dict:
        return _forward("/api/quant/quadrant", date=date, min_x=min_x, min_y=min_y, limit=limit)

    @mcp.tool(name="rank_stability", description=tool_description("rank_stability"))
    def rank_stability(days: int = 7, limit: int = 50, date: str = "") -> dict:
        return _forward("/api/quant/rank-stability", days=days, limit=limit, date=date)

    @mcp.tool(name="rank_momentum", description=tool_description("rank_momentum"))
    def rank_momentum(metric: str = "xy", limit: int = 80, date: str = "") -> dict:
        return _forward("/api/quant/rank-momentum", metric=metric, limit=limit, date=date)

    @mcp.tool(name="momentum_follow", description=tool_description("momentum_follow"))
    def momentum_follow(metric: str = "xy", date: str = "", limit: int = 80) -> dict:
        return _forward("/api/quant/momentum-follow", metric=metric, date=date, limit=limit)

    @mcp.tool(name="volume_double", description=tool_description("volume_double"))
    def volume_double(date: str = "", limit: int = 150, min_energy: float = 0.0,
                      quadrants: str = "", step_gap_min: float | None = None) -> dict:
        return _forward("/api/quant/volume-double", date=date, limit=limit, min_energy=min_energy,
                        quadrants=quadrants, step_gap_min=step_gap_min)

    @mcp.tool(name="energy_track", description=tool_description("energy_track"))
    def energy_track(date: str = "", top_n: int | None = None) -> dict:
        return _forward("/api/quant/energy-track", date=date, top_n=top_n)

    @mcp.tool(name="startup_volume", description=tool_description("startup_volume"))
    def startup_volume(cohort: str = "board4", months: int = 3, limit: int = 200) -> dict:
        return _forward("/api/quant/startup-volume", cohort=cohort, months=months, limit=limit)

    @mcp.tool(name="first_board_shape", description=tool_description("first_board_shape"))
    def first_board_shape(months: int = 60, bucket: str = "dbl_days", buckets: int = 4,
                          metric: str = "ex5", board: str = "", complete_only: bool = True,
                          exclude_locked: bool = True, min_bars: int = 20) -> dict:
        return _forward("/api/quant/first-board-shape", months=months, bucket=bucket,
                        buckets=buckets, metric=metric, board=board,
                        complete_only=complete_only, exclude_locked=exclude_locked,
                        min_bars=min_bars)

    @mcp.tool(name="startup_quadrant_track", description=tool_description("startup_quadrant_track"))
    def startup_quadrant_track(cohort: str = "board4", months: int = 12, window: int = 30,
                               key: str = "all", limit: int = 200) -> dict:
        return _forward("/api/quant/startup-quadrant-track", cohort=cohort, months=months,
                        window=window, key=key, limit=limit)

    @mcp.tool(name="startup_spike_days", description=tool_description("startup_spike_days"))
    def startup_spike_days(cohort: str = "anyfirst", months: int = 12, window: int = 30,
                           jump_pct: float = 0.07, limit: int = 200) -> dict:
        return _forward("/api/quant/startup-spike-days", cohort=cohort, months=months,
                        window=window, jump_pct=jump_pct, limit=limit)

    @mcp.tool(name="volume_breakout", description=tool_description("volume_breakout"))
    def volume_breakout(date: str = "", limit: int = 150, signal_type: str = "") -> dict:
        return _forward("/api/quant/volume-breakout", date=date, limit=limit, signal_type=signal_type)

    @mcp.tool(name="ambush_setup", description=tool_description("ambush_setup"))
    def ambush_setup(date: str = "", limit: int = 150, types: str = "", only_strict: bool = False,
                     min_weekly_ups: int = 0) -> dict:
        return _forward("/api/quant/ambush", date=date, limit=limit, types=types,
                        only_strict=only_strict, min_weekly_ups=min_weekly_ups)

    @mcp.tool(name="confluence_hits", description=tool_description("confluence_hits"))
    def confluence_hits(date: str = "", min_hits: int = 1, sources: str = "",
                        limit: int = 300) -> dict:
        return _forward("/api/quant/confluence", date=date, min_hits=min_hits, sources=sources,
                        limit=limit)

    @mcp.tool(name="system_entry", description=tool_description("system_entry"))
    def system_entry(date: str = "", only_entry: bool = True, min_gap: float = 0.0,
                     limit: int = 300, days: int = 250) -> dict:
        return _forward("/api/quant/system-entry", date=date, only_entry=only_entry,
                        min_gap=min_gap, limit=limit, days=days)

    @mcp.tool(name="system_entry_cohort", description=tool_description("system_entry_cohort"))
    def system_entry_cohort(months: int = 6) -> dict:
        return _forward("/api/quant/system-entry-cohort", months=months)

    @mcp.tool(name="intraday_spike", description=tool_description("intraday_spike"))
    def intraday_spike(date: str = "", tags: str = "", min_hi: float = 0.0, limit: int = 400,
                       days: int = 43) -> dict:
        return _forward("/api/quant/intraday-spike", date=date, tags=tags, min_hi=min_hi,
                        limit=limit, days=days)

    @mcp.tool(name="trend_stocks", description=tool_description("trend_stocks"))
    def trend_stocks(date: str = "", lookback: int = 5, limit: int = 300) -> dict:
        return _forward("/api/quant/trend-stocks", date=date, lookback=lookback, limit=limit)

    @mcp.tool(name="sector_heat", description=tool_description("sector_heat"))
    def sector_heat(date: str = "", type: str = "", stage: str = "", sort: str = "heat",
                    limit: int = 200, stats: str = "day") -> dict:
        return _forward("/api/quant/sector-heat", date=date, type=type, stage=stage, sort=sort,
                        limit=limit, stats=stats)

    @mcp.tool(name="sector_trend", description=tool_description("sector_trend"))
    def sector_trend(date: str = "", regime: str = "", type: str = "", scope: str = "sector",
                     spec: str = "", sort: str = "dip", limit: int = 200) -> dict:
        return _forward("/api/quant/sector-trend", date=date, regime=regime, type=type, scope=scope,
                        spec=spec, sort=sort, limit=limit)

    @mcp.tool(name="sector_allocator", description=tool_description("sector_allocator"))
    def sector_allocator(date: str = "", limit: int = 300) -> dict:
        return _forward("/api/quant/allocator", date=date, limit=limit)

    @mcp.tool(name="abnormal_move", description=tool_description("abnormal_move"))
    def abnormal_move(date: str = "", recent: int = 10, hot_limit: int = 20,
                      limit: int = 60) -> dict:
        return _forward("/api/market/abnormal-move", date=date, recent=recent,
                        hot_limit=hot_limit, limit=limit)

    @mcp.tool(name="first_board_daily", description=tool_description("first_board_daily"))
    def first_board_daily(date: str = "", limit: int = 80, shapes: str = "") -> dict:
        return _forward("/api/quant/first-board-daily", date=date, limit=limit, shapes=shapes)

    @mcp.tool(name="risk_signal", description=tool_description("risk_signal"))
    def risk_signal(months: str = "3", group: str = "day", date: str = "") -> dict:
        return _forward("/api/quant/risk-signal", months=months, group=group, date=date)

    @mcp.tool(name="market_emotion", description=tool_description("market_emotion"))
    def market_emotion() -> dict:
        return _forward("/api/market/emotion")

    # ---------------- B 层：个股明细 ----------------
    @mcp.tool(name="stock_scores", description=tool_description("stock_scores"))
    def stock_scores(code: str, date: str = "") -> dict:
        return _forward("/api/quant/scores", code=code, date=date)

    @mcp.tool(name="stock_score_trend", description=tool_description("stock_score_trend"))
    def stock_score_trend(code: str, window: int = 10, sessions: int = 60) -> dict:
        return _forward("/api/quant/score-trend", code=code, window=window, sessions=sessions)

    @mcp.tool(name="stock_indicators", description=tool_description("stock_indicators"))
    def stock_indicators(code: str, date: str = "") -> dict:
        return _forward("/api/stock/indicators", code=code, date=date)

    @mcp.tool(name="stock_dossier", description=tool_description("stock_dossier"))
    def stock_dossier(code: str, date: str = "") -> dict:
        return _forward("/api/quant/stock-dossier", code=code, date=date)

    @mcp.tool(name="stock_facts", description=tool_description("stock_facts"))
    def stock_facts(code: str) -> dict:
        return _forward("/api/stock/facts", code=code)

    @mcp.tool(name="stock_fundamentals", description=tool_description("stock_fundamentals"))
    def stock_fundamentals(code: str) -> dict:
        return _forward("/api/stock/fundamentals", code=code)

    @mcp.tool(name="stock_price_volume_trend", description=tool_description("stock_price_volume_trend"))
    def stock_price_volume_trend(code: str, days: int = 60, weeks: int = 60, months: int = 48) -> dict:
        return _forward("/api/stock/price-volume-trend", code=code, days=days, weeks=weeks,
                        months=months)

    @mcp.tool(name="security_search", description=tool_description("security_search"))
    def security_search(q: str, limit: int = 20, kinds: str = "") -> dict:
        return _forward("/api/quant/security-search", q=q, limit=limit, kinds=kinds)

    # ---------------- 通用层：目录、兜底、新鲜度 ----------------
    @mcp.tool()
    def list_capabilities(layer: str = "all") -> dict:
        """有哪些可读的数。layer 取 A（全市场榜）／B（个股明细）／C（只进目录，靠 capability_data 取）
        ／index（全部只读端点现扫，含没登记的）。返回每条的 capability_id、标签、屏幕落点、
        来源端点、参数 schema（名／类型／默认／上下界）。"""
        payload = registry_payload()
        if layer in ("A", "B", "C"):
            return {"layer": layer, "capabilities": [c for c in payload["capabilities"]
                                                     if c["layer"] == layer]}
        if layer == "index":
            return {"layer": "index", "endpoints": usable_endpoints()}
        return payload

    @mcp.tool()
    def capability_data(capability_id: str, params: dict | None = None) -> dict:
        """按 capability_id（或直接给 /api/... 路径）取一条登记过的数。C 层与索引层只能这样取。
        参数名必须是该端点真认识的键，否则会直接报错而不是静默返回全量。"""
        return client.call_capability(capability_id, params or {})

    @mcp.tool()
    def data_freshness(table: str = "all") -> dict:
        """判「读到的是哪一场的数、哪些产出表落后了」。各表的覆盖日来自页面在用的 /api/coverage。
        任何榜单结果都带 _meta.as_of；怀疑数陈旧就先调这一条。"""
        return client.fetch("/api/coverage", {"table": table or "all"})

    return mcp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YuanShan 展示层数据的只读 MCP 服务")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8920)
    parser.add_argument("--check-tools", action="store_true", help="只打印装配结果，不起会话")
    args = parser.parse_args(argv)

    mcp = build_server()
    if args.transport == "streamable-http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
    if args.check_tools:
        import asyncio
        tools = asyncio.run(mcp.list_tools())
        missing = [t for t in AGENT_TOOLS if t not in {x.name for x in tools}]
        print(f"service={SERVICE_NAME} tools={len(tools)} 期望={len(AGENT_TOOLS) + 3} "
              f"缺={missing or '无'}")
        for t in tools:
            print(f"  {t.name}: {(t.description or '')[:60]}")
        return 1 if missing else 0

    if args.transport == "streamable-http":
        print(f"{SERVICE_NAME} streamable-http on http://{args.host}:{args.port}/mcp "
              f"（后端 {client.base_url()}）", flush=True)
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
