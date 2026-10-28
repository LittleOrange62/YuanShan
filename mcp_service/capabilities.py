"""MCP 服务：登记表与全量只读端点索引。

台账（人类读的那一份）在 docs/MCP服务登记表.md；这一份是机器读的那一份，
两者由 tests/test_mcp_registry_sync.py 逐条对齐——多一条、少一条、字段漂移都会红。
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "MCP服务登记表.md"

#: 索引扫描范围。review_agent/ 那一族是**另一个** MCP 服务（冻结复盘包），不并进来。
INDEX_SOURCES = ("server.py", "vr/app.py")

#: 扫到但不给智能体用：起子进程/碰桌面窗口的探测类读端点（理由见 docs/MCP服务登记表.md 第七节）
EXCLUDE_INDEX = {
    "/api/cli/available": "会探测本机 CLI 子进程",
    "/api/tdx/status": "会枚举通达信桌面窗口",
    "/api/tdx/watch": "会枚举并操作通达信桌面窗口",
    "/api/wxpush": "读的是本机微信桌面通道状态",
}

#: 端点上名叫 refresh 的参数一律不暴露、不传递：那会触发重算并抢 8910 的互斥
HIDDEN_PARAMS = {"refresh"}

LAYER_A = "A"
LAYER_B = "B"
LAYER_C = "C"


@dataclass(frozen=True)
class Capability:
    """一条登记：屏幕上的一格数 ↔ 一个只读端点。参数 schema 不在这里抄，现读端点源码。"""

    id: str
    label: str
    where: str
    path: str
    layer: str = LAYER_C
    tables: tuple[str, ...] = ()
    source_module: str = ""
    note: str = ""
    slow: bool = False


def _cap(id: str, label: str, where: str, path: str, layer: str, tables: str = "",
         source_module: str = "", note: str = "", slow: bool = False) -> Capability:
    return Capability(id=id, label=label, where=where, path=path, layer=layer,
                      tables=tuple(t for t in tables.split() if t),
                      source_module=source_module, note=note, slow=slow)


CURATED: tuple[Capability, ...] = (
    # ---- A 层：全市场榜与数据加工产出（每个都是一件专用工具）----
    _cap("quadrant_board", "四维象限", "/quadrant", "/api/quant/quadrant", LAYER_A,
         "analytics.score analytics.score_trend", "duanxian/quant.py", slow=True),
    _cap("rank_stability", "名次稳定榜", "/quadrant", "/api/quant/rank-stability", LAYER_A,
         "analytics.score", "duanxian/quant.py", slow=True),
    _cap("rank_momentum", "名次稳步前进榜", "/quadrant", "/api/quant/rank-momentum", LAYER_A,
         "analytics.score", "duanxian/quant.py", slow=True),
    _cap("momentum_follow", "前进榜前 N 名今日与近 5 日表现", "/quadrant",
         "/api/quant/momentum-follow", LAYER_A,
         "analytics.score analytics.volume_double analytics.volume_breakout_signal "
         "analytics.ambush_setup analytics.trend_stock_daily analytics.abnormal_move_daily "
         "market_data.stock_daily", "duanxian/momentum_follow.py", slow=True),
    _cap("volume_double", "倍量形态", "/volume-double", "/api/quant/volume-double", LAYER_A,
         "analytics.volume_double analytics.volume_ratio_daily", "duanxian/quant.py"),
    _cap("energy_track", "加权分进榜后的逐日涨跌幅", "/volume-double", "/api/quant/energy-track",
         LAYER_A, "analytics.score market_data.stock_daily", "duanxian/quant.py", slow=True),
    _cap("startup_volume", "启动前量能分布", "/volume-double", "/api/quant/startup-volume", LAYER_A,
         "analytics.startup_volume_profile", "duanxian/quant.py"),
    _cap("first_board_shape", "首板形状预示", "/volume-double", "/api/quant/first-board-shape",
         LAYER_A, "analytics.first_board_shape analytics.volume_ratio_daily",
         "duanxian/first_board_view.py", slow=True),
    _cap("startup_quadrant_track", "启动前 30 场四维象限轨迹", "/volume-double",
         "/api/quant/startup-quadrant-track", LAYER_A,
         "analytics.startup_quadrant_track analytics.startup_volume_profile "
         "analytics.first_board_shape analytics.score", "duanxian/quadrant_track_view.py", slow=True),
    _cap("startup_spike_days", "启动前爆量与突然拉高", "/volume-double",
         "/api/quant/startup-spike-days", LAYER_A,
         "analytics.startup_spike_days analytics.startup_volume_profile "
         "analytics.first_board_shape analytics.volume_ratio_daily", "duanxian/spike_days_view.py",
         slow=True),
    _cap("volume_breakout", "突破信号", "/volume-breakout", "/api/quant/volume-breakout", LAYER_A,
         "analytics.volume_breakout_signal", "duanxian/quant.py"),
    _cap("ambush_setup", "埋伏票", "/volume-breakout", "/api/quant/ambush", LAYER_A,
         "analytics.ambush_setup", "duanxian/quant.py"),
    _cap("confluence_hits", "选股汇总（各榜交叉命中）", "/confluence", "/api/quant/confluence",
         LAYER_A, "analytics.confluence_daily analytics.score", "duanxian/confluence_view.py"),
    _cap("system_entry", "交易体系榜", "/confluence", "/api/quant/system-entry", LAYER_A,
         "analytics.system_entry analytics.system_entry_stats", "duanxian/system_entry_view.py",
         slow=True),
    _cap("system_entry_cohort", "走完三条的个股近六个月名单", "/confluence",
         "/api/quant/system-entry-cohort", LAYER_A,
         "analytics.system_entry analytics.confluence_daily analytics.score market_data.stock_daily",
         "duanxian/system_entry_cohort.py", slow=True),
    _cap("intraday_spike", "异动筛查（盘中冲高／触板）", "/spike", "/api/quant/intraday-spike",
         LAYER_A, "analytics.intraday_spike market_data.stock_daily", "duanxian/spike_view.py",
         slow=True),
    _cap("trend_stocks", "趋势股", "/trend-stocks", "/api/quant/trend-stocks", LAYER_A,
         "analytics.trend_stock_daily market_data.sector_daily", "duanxian/trend_view.py", slow=True),
    _cap("sector_heat", "板块热度与点火", "/sector-heat", "/api/quant/sector-heat", LAYER_A,
         "analytics.sector_heat_daily analytics.ignition", "duanxian/quant.py"),
    _cap("sector_trend", "板块趋势（位置×阶段）", "/sector-heat", "/api/quant/sector-trend", LAYER_A,
         "analytics.sector_trend analytics.sector_trend_signal config.setting", "duanxian/quant.py"),
    _cap("sector_allocator", "分配者视角", "/allocator", "/api/quant/allocator", LAYER_A,
         "analytics.sector_allocator market_data.index_daily", "duanxian/allocator_view.py", slow=True),
    _cap("abnormal_move", "异动提醒", "首页（短线情绪下面）", "/api/market/abnormal-move", LAYER_A,
         "analytics.abnormal_move_daily", "duanxian/abnormal_view.py", slow=True),
    _cap("first_board_daily", "首板分析", "首页（换手率 TOP20 上面）", "/api/quant/first-board-daily",
         LAYER_A, "analytics.first_board_daily analytics.first_board_shape analytics.volume_double",
         "duanxian/first_board_daily.py", slow=True),
    _cap("risk_signal", "风险提示（市场宽度×阿姆斯）", "首页（盘面数据下面）", "/api/quant/risk-signal",
         LAYER_A, "analytics.market_breadth", "duanxian/risk_signal.py"),
    _cap("market_emotion", "短线情绪", "首页", "/api/market/emotion", LAYER_A,
         "analytics.limit_up_pool_daily analytics.market_sentiment_daily "
         "analytics.board_node_daily", "duanxian/limit_up_board.py",
         note="母体 2026-10-26 起在库里（每场触过板的全部：封住/没封住、连板数、自算首封、封单额、题材串），"
              "读侧只读行不现抓东财池"),

    # ---- B 层：个股明细 ----
    _cap("stock_scores", "趋势评分：八项能力与分量明细", "/stock-review", "/api/quant/scores",
         LAYER_B, "analytics.score analytics.score_detail", "duanxian/quant.py"),
    _cap("stock_score_trend", "趋势评分：窗口斜率与迷你走势", "/stock-review",
         "/api/quant/score-trend", LAYER_B, "analytics.score analytics.score_trend", "duanxian/quant.py"),
    _cap("stock_indicators", "35 项技术指标读数", "/stock-review", "/api/stock/indicators", LAYER_B,
         "analytics.indicator_value", "vr/app.py"),
    _cap("stock_dossier", "个股量化档案（八块拼一页）", "/stock-review", "/api/quant/stock-dossier",
         LAYER_B,
         "analytics.score analytics.score_detail analytics.score_trend analytics.volume_double "
         "analytics.volume_breakout_signal analytics.ambush_setup analytics.confluence_daily "
         "analytics.sector_heat_daily analytics.startup_volume_profile", "duanxian/stock_dossier.py",
         slow=True),
    _cap("stock_facts", "个股信息主档", "/stock-review", "/api/stock/facts", LAYER_B,
         "reference_data.security reference_data.stock_profile reference_data.sector_member "
         "market_data.stock_daily market_data.stock_snapshot analytics.stock_brief",
         "duanxian/stock_facts.py"),
    _cap("stock_fundamentals", "财务多期与外推", "/stock-review", "/api/stock/fundamentals", LAYER_B,
         "reference_data.stock_financial_period", "duanxian/stock_fundamentals.py"),
    _cap("stock_price_volume_trend", "量价走势 日／周／月", "/stock-review",
         "/api/stock/price-volume-trend", LAYER_B, "market_data.stock_daily", "vr/app.py"),
    _cap("security_search", "找一只票（代码／名称／全拼／首字母）", "全站搜索框",
         "/api/quant/security-search", LAYER_B, "reference_data.security", "duanxian/security_search.py"),

    # ---- C 层：只进目录，靠 capability_data 取 ----
    _cap("stock_metrics_ledger", "全指标总账", "/stock-review", "/api/stock/metrics-ledger", LAYER_C,
         "analytics.indicator_value analytics.market_sentiment_daily reference_data.stock_profile",
         "duanxian/stock_metrics_ledger.py"),
    _cap("stock_volume_similarity", "近 30 根量价形状与历史最相近样本", "/stock-review",
         "/api/stock/volume-similarity", LAYER_C,
         "analytics.startup_volume_profile analytics.volume_ratio_daily market_data.stock_daily",
         "duanxian/volume_similarity.py", slow=True),
    _cap("confluence_history", "单票交叉命中历史", "/confluence", "/api/quant/confluence-history",
         LAYER_C, "analytics.confluence_daily", "duanxian/confluence_view.py"),
    _cap("sector_members", "板块成分股明细", "预设题二级展开", "/api/quant/sector-members", LAYER_C,
         "reference_data.sector_member", "duanxian/quant.py"),
    _cap("new_high", "自选股历史新高", "/grouped", "/api/quant/new-high", LAYER_C,
         "reference_data.security market_data.stock_daily", "duanxian/quant.py", slow=True),
    _cap("market_overview", "盘面总览", "/sector-heat、首页", "/api/market/overview", LAYER_C,
         "market_data.sector_fund_flow_daily", "vr/market.py"),
    _cap("market_turnover_top", "换手率 TOP20", "首页", "/api/market/turnover-top", LAYER_C,
         "analytics.ignition", "vr/market.py"),
    _cap("market_first_board", "昨日首板梯队", "首页", "/api/market/first-board", LAYER_C,
         "analytics.board_node_daily", "vr/firstboard.py"),
    _cap("market_sector_flow", "板块资金流累计", "/sector-heat", "/api/market/sector-flow", LAYER_C,
         "analytics.sector_flow_trend market_data.fund_flow_rank "
         "market_data.sector_fund_flow_daily", "duanxian/sector_flow.py"),
    _cap("market_sector_cycle", "板块周期", "首页", "/api/market/sector-cycle", LAYER_C,
         "analytics.sector_flow_trend analytics.ignition", "vr/app.py"),
    _cap("market_sector_rotation", "板块轮动", "lib/api.ts", "/api/market/sector-rotation", LAYER_C,
         "analytics.indicator_value analytics.sector_flow_trend", "duanxian/flow_trend.py"),
    _cap("qd_sentiment_cycle", "情绪周期（quantdash 口径）", "/qd-compare", "/api/qd/sentiment-cycle",
         LAYER_C, "qd.sentiment_cycle", "vr/app.py"),
    _cap("qd_board_ladder", "连板梯队（quantdash 口径）", "/qd-compare", "/api/qd/board-ladder",
         LAYER_C, "qd.board_ladder qd.board_ladder_member", "vr/app.py"),
    _cap("qd_sector_cycle", "题材周期（quantdash 口径）", "/qd-compare", "/api/qd/sector-cycle",
         LAYER_C, "qd.sector_rotation", "vr/app.py"),
    _cap("qd_sector_follow", "前三甲轨迹与相似板块（qd 口径轨）", "首页、/qd-compare",
         "/api/qd/sector-follow", LAYER_C, "qd.sector_follow", "duanxian/qd_follow.py",
         note="判据与落库在 app/compute/qd_sector_follow.py（链步 qd_sector）；读侧只读行"),
    _cap("qd_distribution", "指标分布对照（quantdash 口径）", "/qd-compare", "/api/qd/distribution",
         LAYER_C, "qd.sentiment_cycle", "vr/app.py"),
    _cap("qd_news", "资讯条目", "/intel", "/api/qd/news", LAYER_C, "qd.news_item", "vr/app.py"),
    _cap("screen_rules", "筛选条件登记", "/screen", "/api/screen/rules", LAYER_C,
         "config.screen_rule", "duanxian/screen_view.py"),
    _cap("screen_run", "筛选器执行", "/screen", "/api/screen", LAYER_C,
         "analytics.indicator_value market_data.stock_daily", "duanxian/screen_view.py", slow=True),
    _cap("watchlist_groups", "自选分组", "/grouped、/my-stocks", "/api/watchlist/groups", LAYER_C,
         "config.watchlist_group", "duanxian/watchlist.py"),
    _cap("watchlist_stocks", "组内自选明细", "/grouped", "/api/watchlist/stocks", LAYER_C,
         "config.watchlist_stock analytics.score", "duanxian/watchlist.py"),
    _cap("watchlist_group_analytics", "分组整体分析", "/grouped 顶部",
         "/api/watchlist/group-analytics", LAYER_C, "analytics.score", "vr/app.py", slow=True),
    _cap("data_coverage", "覆盖体检（逐表缺几天）", "/coverage", "/api/coverage", LAYER_C,
         "analytics.* 各产出表的覆盖日", "duanxian/coverage.py", slow=True),
    _cap("data_quality", "质量闸", "/quality", "/api/quality", LAYER_C, "config.quality_rule",
         "duanxian/quality.py"),
    _cap("datasource_status", "数据源健康", "/datasource", "/api/datasource", LAYER_C),
    _cap("jobs_snapshot", "任务与调度", "/jobs", "/api/jobs", LAYER_C, "operations.*",
         "vr/jobsboard.py"),
    _cap("jobs_chain_steps", "加工段逐步结果", "/system", "/api/jobs/chain-steps", LAYER_C,
         "operations.*", "vr/jobsboard.py"),
    _cap("volume_energy_config", "倍量加权分档（只读）", "/system", "/api/config/volume-energy",
         LAYER_C, "config.setting", "vr/app.py"),
    _cap("quote_links_config", "行情外链地址（只读）", "/system", "/api/config/quote-links", LAYER_C,
         "config.setting", "vr/app.py"),
    _cap("journal_list", "交易日志流水", "/journal", "/api/journal/list", LAYER_C, "ys.*",
         "server.py"),
    _cap("journal_stats", "交易日志统计", "/journal", "/api/journal/stats", LAYER_C, "ys.*",
         "server.py"),
    _cap("positions", "重点关注（持仓）", "/my-stocks", "/api/positions", LAYER_C, "ys.*",
         "server.py"),
    _cap("risk_report", "风险监控报告", "/my-stocks", "/api/risk/report", LAYER_C, "ys.*",
         "server.py"),
    _cap("review_latest", "复盘报告", "/agent/review", "/api/review/latest", LAYER_C,
         "文件侧产出", "server.py"),
    _cap("backtest_stats", "历史统计", "/backtest", "/api/backtest", LAYER_C, "ys.*", "server.py",
         slow=True),
    _cap("weekly_heat", "近 5 天热度", "/heat", "/api/weekly", LAYER_C, "analytics.sector_heat_daily",
         "duanxian/weekly.py", slow=True),
    _cap("live_emotion", "盘中实时情绪", "首页", "/api/market/live-emotion", LAYER_C,
         "market_data.stock_snapshot", "duanxian/live_emotion.py"),
    _cap("intraday_path", "盘中走势", "复盘材料", "/api/intraday/path", LAYER_C,
         "market_data.stock_minute", "server.py"),
    _cap("verification_menu", "盘中核验清单", "复盘材料", "/api/verification/menu", LAYER_C,
         "market_data.stock_minute", "duanxian/verification.py"),
    _cap("review_notes", "复盘笔记", "/journal 的 tab", "/api/review-notes", LAYER_C, "ys.review_note",
         "vr/app.py", note="端到端还没真跑过，别当已验证能力宣传"),
    _cap("reminders", "提醒", "/journal 的 tab", "/api/reminders", LAYER_C, "ys.reminder", "vr/app.py"),
)

CURATED_BY_ID = {c.id: c for c in CURATED}
CURATED_BY_PATH = {c.path: c for c in CURATED}


def capabilities_of_layer(layer: str) -> tuple[Capability, ...]:
    return tuple(c for c in CURATED if c.layer == layer)


# ---------------------------------------------------------------- 端点索引

_QUERY_KW_RE = re.compile(r"\b(default|ge|le|gt|lt|min_length|max_length|pattern|description)\s*=")


def _split_query_call(text: str) -> dict[str, str]:
    """把 `Query(7, ge=2, le=20, description='x')` 拆成关键字段；不追求完备，够用且可回读。"""
    inner = text[text.index("(") + 1: text.rindex(")")]
    fields: dict[str, str] = {}
    try:
        node = ast.parse(f"f({inner})" if inner.strip() else "f()", mode="eval").body
    except SyntaxError:
        return {}
    for arg in getattr(node, "args", []):
        fields["default"] = ast.unparse(arg)
    for kw in getattr(node, "keywords", []):
        if kw.arg:
            fields[kw.arg] = ast.unparse(kw.value)
    return fields


def _infer_type(annotation: str, default_src: str) -> str:
    text = (annotation or "") + " " + (default_src or "")
    if "bool" in text or default_src in ("True", "False"):
        return "boolean"
    if "float" in text or re.search(r"^\s*-?\d+\.\d+", default_src or ""):
        return "number"
    if "int" in text or re.fullmatch(r"-?\d+", (default_src or "").strip()):
        return "integer"
    if "list[" in text or default_src.startswith("["):
        return "array"
    return "string"


def _params_of(node) -> list[dict[str, Any]]:
    """处理函数签名 → 参数 schema（名／类型／默认／上下界／是否必填）。`refresh` 一类不外露。"""
    a = node.args
    pos = [x.arg for x in a.args]
    defaults = [ast.unparse(d) for d in a.defaults]
    d_start = len(pos) - len(defaults)
    raw_by_name = {n: (defaults[i - d_start] if i >= d_start else "") for i, n in enumerate(pos)}
    ann_by_name = {x.arg: ast.unparse(x.annotation) if x.annotation else ""
                   for x in a.args + a.kwonlyargs}
    for i, x in enumerate(a.kwonlyargs):
        raw_by_name[x.arg] = ast.unparse(a.kw_defaults[i]) if a.kw_defaults[i] else ""

    out = []
    for name in pos + [x.arg for x in a.kwonlyargs]:
        if name in ("self", "request", "req", "response") or name in HIDDEN_PARAMS:
            continue
        raw = raw_by_name.get(name, "")
        default, bounds, required = raw, {}, raw in ("Ellipsis", "...")
        if raw.startswith("Query("):
            fields = _split_query_call(raw)
            default = fields.get("default", "")
            bounds = {k: v for k, v in fields.items()
                      if k in ("ge", "le", "gt", "lt", "min_length", "max_length", "pattern")}
            required = required or default in ("Ellipsis", "...")
        out.append({"name": name, "type": _infer_type(ann_by_name.get(name, ""), default),
                    "default": _pyval(default), "required": required, "bounds": bounds})
    return out


def _pyval(src: str) -> Any:
    """把源码里的默认值文本变成 JSON 友好的值；解析不了就原样带回。"""
    if src in ("Ellipsis", "...", ""):
        return None
    try:
        return ast.literal_eval(src)
    except (ValueError, SyntaxError):
        return src


@lru_cache(maxsize=1)
def endpoint_index() -> dict[str, dict[str, Any]]:
    """静态扫源码里的 `@*.get/head` 装饰器，得到 path → 参数 schema + 处理函数位置。

    用静态解析而不是导入 app：导入会把常驻线程与调度器一起拉起来。
    """
    index: dict[str, dict[str, Any]] = {}
    for rel in INDEX_SOURCES:
        source = (ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(source, rel)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or getattr(dec.func, "attr", "") not in ("get", "head"):
                    continue
                if not dec.args or not isinstance(dec.args[0], ast.Constant):
                    continue
                path = dec.args[0].value
                if not isinstance(path, str) or not path.startswith("/api/"):
                    continue
                params = _params_of(node)
                index[path] = {"path": path, "file": rel, "line": node.lineno,
                               "handler": node.name, "params": params,
                               "templated": "{" in path,
                               "excluded": next((why for p, why in EXCLUDE_INDEX.items() if path == p), "")}
                break
    return index


def usable_endpoints() -> list[dict[str, Any]]:
    """索引层：真实存在、可寻址（无路径参数）、未被显式排除的只读端点。"""
    out = []
    for path, entry in sorted(endpoint_index().items()):
        if entry["excluded"] or entry["templated"]:
            continue
        curated = CURATED_BY_PATH.get(path)
        out.append({**entry, "capability_id": curated.id if curated else "",
                    "label": curated.label if curated else "",
                    "where": curated.where if curated else "",
                    "layer": curated.layer if curated else "index"})
    return out


def capability_entry(cap: Capability) -> dict[str, Any]:
    """把一条登记与现读的参数 schema 拼成给外部看的形状。"""
    entry = endpoint_index().get(cap.path)
    if entry is None:
        raise KeyError(f"登记的端点不存在或不是只读：{cap.id} → {cap.path}")
    params = [dict(p) for p in entry["params"]]
    return {
        "capability_id": cap.id,
        "label": cap.label,
        "where": cap.where,
        "layer": cap.layer,
        "path": cap.path,
        "method": "GET",
        "handler": f"{entry['file']}:{entry['line']} {entry['handler']}",
        "source_module": cap.source_module,
        "tables": list(cap.tables),
        "note": cap.note,
        "slow": cap.slow,
        "params": params,
    }


def registry_payload() -> dict[str, Any]:
    caps = [capability_entry(c) for c in CURATED]
    index = usable_endpoints()
    return {
        "capabilities": caps,
        "endpoints": index,
        "counts": {
            "A": sum(1 for c in caps if c["layer"] == LAYER_A),
            "B": sum(1 for c in caps if c["layer"] == LAYER_B),
            "C": sum(1 for c in caps if c["layer"] == LAYER_C),
            "index": len(index),
            "tools": len(AGENT_TOOLS),
        },
        "tools": list(AGENT_TOOLS),
        "service_name": SERVICE_NAME,
        "doc": doc_registry_diff(),
    }


#: A/B 层的工具名，逐条必须在 mcp_service/server.py 里真的注册过（单测把关）
AGENT_TOOLS: tuple[str, ...] = tuple(c.id for c in CURATED if c.layer in (LAYER_A, LAYER_B))

SERVICE_NAME = "yuanshan_displayed_data"

# ---------------------------------------------------------------- 台账同步

#: 登记表正文里的三张表所在的小节（标题前缀逐字对这里的键）
DOC_SECTIONS = ("三", "四", "五")
_ID_CELL_RE = re.compile(r"`([a-z0-9_]+)`")
_PATH_CELL_RE = re.compile(r"(/api/[a-z0-9/_-]*)")


def doc_registry_diff() -> dict[str, Any]:
    """比对 docs/MCP服务登记表.md 的三张表与 CURATED：谁多、谁少、谁的端点写岔了。

    这条检查存在的理由：那份文档是给人看的台账，一旦漂了就变成"写着有其实没接"的反面版本。
    """
    if not DOC_PATH.exists():
        return {"ok": False, "reason": f"台账文件不存在：{DOC_PATH}"}
    rows: dict[str, str] = {}
    inside = False
    for line in DOC_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            inside = any(f"## {s}" in line for s in DOC_SECTIONS)
            continue
        if not inside or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells:
            continue
        found = _ID_CELL_RE.match(cells[0])
        if not found or found.group(1) == "capability_id":
            continue
        path = ""
        for cell in cells[2:3]:
            hit = _PATH_CELL_RE.search(cell)
            path = hit.group(1) if hit else ""
        rows[found.group(1)] = path

    registry = {c.id: c.path for c in CURATED}
    missing_in_doc = sorted(set(registry) - set(rows))
    extra_in_doc = sorted(set(rows) - set(registry))
    path_mismatch = sorted(f"{i}：文档 {rows[i]} ≠ 登记 {registry[i]}"
                           for i in set(rows) & set(registry) if rows[i] != registry[i])
    return {"ok": not (missing_in_doc or extra_in_doc or path_mismatch),
            "rows_in_doc": len(rows), "rows_in_registry": len(registry),
            "missing_in_doc": missing_in_doc, "extra_in_doc": extra_in_doc,
            "path_mismatch": path_mismatch, "doc": str(DOC_PATH.relative_to(ROOT))}
