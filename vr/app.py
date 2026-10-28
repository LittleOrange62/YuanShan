# 本文件继承自上游 Vibe-Research 后端（MIT，见 docs/upstream/Research-LICENSE.txt 与 research_data/NOTICE.md），本机有实质性改动。
# 现存 /api/quant/* 那批判据读数端点多为本机新增，启动时由 server.py 并入 8910；归属清单见 docs/上游改动与归属清单.md。
"""Vibe-Research 后端 —— A股数据层 HTTP 接口（FastAPI）。

端点全部在 /api 下，前端 vite 代理 /api → localhost:8900。
只读、无状态、按用户传入代码返回客观数据。不预置标的、不建议。

启动：
    uvicorn app:app --host 127.0.0.1 --port 8900
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import astock
import chat as chat_layer
import cli_runtime
import gstock
import newsradar
import portfolio as pf
import market
import myreports as mr
import firstboard
import watchtower

from product_version import PRODUCT_NAME, PRODUCT_VERSION

app = FastAPI(title=PRODUCT_NAME, version=PRODUCT_VERSION)

# 迁入的旧持仓仅供显式导入；本产品持仓来自交易日志，不启动旧持仓后台写入。

# CORS：默认放开（本地自托管友好）；公网部署时用 VR_ALLOW_ORIGINS 收紧成白名单。
#   例：VR_ALLOW_ORIGINS="https://myhost"  （逗号分隔多个）
_ORIGINS = [o.strip() for o in os.environ.get("VR_ALLOW_ORIGINS", "*").split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# 可选鉴权：设了 VR_API_KEY 就要求所有 /api/* 带 `Authorization: Bearer <key>`
#   （本地自托管不设=开放；公网部署务必设，否则别人能读你的持仓/调你的后端）。
_API_KEY = os.environ.get("VR_API_KEY", "").strip()


@app.middleware("http")
async def _require_api_key(request: Request, call_next):
    if (
        _API_KEY
        and request.method != "OPTIONS"
        and request.url.path.startswith("/api/")
        and request.url.path != "/api/health"
    ):
        if request.headers.get("authorization", "") != f"Bearer {_API_KEY}":
            return JSONResponse({"detail": "未授权：缺少或错误的 API Key（VR_API_KEY）"}, status_code=401)
    return await call_next(request)

_CODE_RE = r"^\d{6}$"


def _validate(code: str) -> str:
    code = (code or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(400, "代码必须是 6 位数字")
    return code


@app.get("/api/health")
def health():
    return {"ok": True, "service": "yuanshan", "version": PRODUCT_VERSION}


class LLMConfig(BaseModel):
    provider: str = ""       # cli-* = 订阅接入（调本机 CLI）；其余 = API 接入
    baseURL: str = ""        # 订阅接入时留空
    apiKey: str = ""         # 订阅接入时留空
    model: str


class ChatReq(BaseModel):
    messages: list[dict]
    context: str = ""
    llm: LLMConfig


@app.post("/api/chat")
def chat(req: ChatReq):
    """系统 AI 对话，**流式** NDJSON（每行一个事件 {type: tool|delta|done|error}）。

    - API 接入：OpenAI 兼容 function-calling，边流答案边推工具调用事件。
    - 订阅接入（provider=cli-*）：调本机已登录的 CLI，stdout 边出边流（数据靠 context）。
    配置错误（缺 key / 未装 CLI）走 HTTP 400；运行时错误走流内 error 事件。用户配置随请求传入，后端不持久化。
    """
    if not req.messages:
        raise HTTPException(400, "messages 不能为空")
    if not req.llm.model:
        raise HTTPException(400, "缺少模型配置，请先在「AI 探索 · 接入设置」里选择")

    is_cli = req.llm.provider.startswith("cli-")
    if is_cli:
        kind = req.llm.provider[4:]
        if not cli_runtime.detect_cli(kind):
            raise HTTPException(400, f"未检测到「{kind}」对应的本机命令。请先安装并登录该 CLI，或改用「API 接入」。")
    elif not req.llm.apiKey or not req.llm.baseURL:
        raise HTTPException(400, "缺少 Base URL 或 API Key，请先在「AI 探索 · 接入设置」里填写")

    cfg = req.llm.model_dump()

    def gen():
        try:
            events = (chat_layer.run_chat_cli_stream if is_cli else chat_layer.run_chat_stream)(cfg, req.messages, req.context)
            for ev in events:
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except Exception as e:  # noqa: BLE001 — 运行时错误以流内事件上报，不中断连接
            yield json.dumps({"type": "error", "message": f"对话失败：{e}"}, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


class HoldingIn(BaseModel):
    code: str
    shares: float
    cost: float


@app.get("/api/portfolio")
def portfolio_get():
    """持仓 + 实时盈亏（浮动盈亏红涨绿跌）。"""
    try:
        return {"data": pf.get_portfolio()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"持仓读取异常：{e}") from e


@app.post("/api/portfolio/holding")
def portfolio_add(h: HoldingIn):
    """加一笔持仓（同代码按加权平均成本合并）。存本地，不上传。"""
    code = (h.code or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(400, "代码必须是 6 位数字")
    if h.shares <= 0:
        raise HTTPException(400, "数量必须大于 0")
    # 成本价不限正负：融券 / 返息 / 摊薄后为负成本等情形按结果计算，用户想怎么输就怎么输。
    out = pf.add_holding(code, h.shares, h.cost)
    watchtower.poke()  # 每日盯盘：持仓变化立即重建快照
    return {"data": out}


@app.delete("/api/portfolio/holding")
def portfolio_remove(code: str = Query(...)):
    out = pf.remove_holding(code.strip())
    watchtower.poke()  # 每日盯盘：持仓变化立即重建快照
    return {"data": out}


# ---- 我的研报（用户上传自己的研报，存本地、不上传、不进开源仓库）----

class ReportIn(BaseModel):
    name: str
    content_b64: str


@app.get("/api/myreports")
def myreports_list():
    return {"data": mr.list_reports()}


@app.post("/api/myreports")
def myreports_upload(r: ReportIn):
    """上传一份研报（base64）→ 存本地 + 按文件名自动打行业标签。"""
    try:
        return {"data": mr.save_report(r.name, r.content_b64)}
    except mr.ReportError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/myreports/file/{rid}")
def myreports_file(rid: str):
    """下载/预览某份研报原文件。"""
    hit = mr.report_path(rid)
    if not hit:
        raise HTTPException(404, "研报不存在")
    path, name = hit
    return FileResponse(str(path), filename=name)


@app.delete("/api/myreports/{rid}")
def myreports_delete(rid: str):
    return {"data": {"ok": mr.delete_report(rid)}}


class CloseIn(BaseModel):
    code: str
    date: str
    price: float
    shares: float
    cost: float


@app.post("/api/portfolio/close")
def portfolio_close(c: CloseIn):
    """记一笔已清仓（已实现盈亏）。存本地。"""
    code = (c.code or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(400, "代码必须是 6 位数字")
    if c.price <= 0 or c.shares <= 0:
        raise HTTPException(400, "清仓价与股数必须大于 0")
    # 买入成本不限正负（同持仓录入）：按 (清仓价 - 成本) × 股数 的结果计算已实现盈亏。
    date = (c.date or "").strip()
    if not date:
        raise HTTPException(400, "请填清仓日期")
    from datetime import datetime
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "清仓日期格式应为 YYYY-MM-DD") from None
    return {"data": pf.close_position(code, date, c.price, c.shares, c.cost)}


@app.delete("/api/portfolio/close")
def portfolio_close_remove(index: int = Query(...)):
    return {"data": pf.remove_closed(index)}


@app.post("/api/portfolio/refresh")
def portfolio_refresh():
    """手动刷新：立即重拉行情算盈亏。"""
    try:
        return {"data": pf.get_portfolio()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"刷新失败：{e}") from e


@app.get("/api/radar")
def radar():
    """资讯雷达：12 赛道公开 RSS 资讯（读缓存，无缓存返回赛道骨架）。"""
    try:
        return {"data": newsradar.get_radar(force=False)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"资讯雷达异常：{e}") from e


@app.post("/api/radar/refresh")
def radar_refresh():
    """强制重抓全部 RSS 源（耗时约 20-40s），更新缓存。"""
    try:
        return {"data": newsradar.fetch_radar()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"资讯雷达刷新失败：{e}") from e


@app.get("/api/market/overview")
def market_overview():
    """市场情绪 + 板块资金流（板块/大盘级，全站共享缓存 5 分钟）。"""
    try:
        return {"data": market.get_overview()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"市场总览异常：{e}") from e


@app.get("/api/market/sector-flow")
def market_sector_flow(since: str = "", months: int = 3, limit: int = 0):
    """行业净流入的**逐日累计**（读 `market_data.sector_fund_flow_daily`）。

    用户 2026-10-06 要的是"2024 年以来每个板块的净流入/净流出都算一遍，默认近 3 个月
    为正由大到小、显示前 50 并可翻页"。接口只把**表里真有的那些天**加起来：
    这张表 2026-10-06 才建，过去没存过这份榜（接口只有即时一档、报文无日期），
    所以 `coverage.sessions` 与 `window` 必须一起交出 —— 缺料要可见，不能拿一个
    看起来像"2024 年以来"的数顶上。
    """
    import datetime as _dt

    from duanxian import sector_flow

    try:
        d = _dt.date.fromisoformat(since) if since else None
        return {"data": sector_flow.aggregate(since=d, months=max(1, min(months, 36)),
                                              limit=max(0, min(limit, 500)))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"行业资金流累计取数异常：{e}") from e


@app.get("/api/market/emotion")
def market_emotion():
    """短线情绪：连板梯队 / 最高连板 / 炸板率 / 封板率 / 晋级率 / 涨跌停家数。

    含连板梯队个股清单（code/name/连板数等）——2026-07-05 起如实展示客观公开榜单（东财同款），
    只呈现事实，不附推荐/评分/预测/买卖时机。全站共享缓存 5 分钟。

    ⚠️ 母体 2026-10-26 起换到库里：读 `analytics.limit_up_pool_daily`（写侧
    `app/compute/limit_up_pool.py`，接在加工段 fbdaily 那一步），这一块**不再每次请求现抓
    东财 push2ex 四个池子**。家数与比率随之改按本地日线判据，与东财池差几只到十几只
    （口径文字与实测差随 payload 交出，见 `duanxian/limit_up_board.CALIBER_NOTE`）。
    库里没有那一场的行时 `available=false` 并交"该跑哪一步"，不回退抓在线池子顶数。
    """
    try:
        return {"data": market.get_short_term_emotion()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"短线情绪异常：{e}") from e


@app.get("/api/monitor/snapshot")
def monitor_snapshot(watch: str = Query("")):
    """每日盯盘快照：持仓/自选/500亿大票异动/三板+/昨日成交前十 + 异动事件流。

    后端常驻线程盘中 3 秒轮询（腾讯 L1 快照），本接口只读内存、毫秒级返回——前端可放心
    3 秒轮询。watch 参数只筛选返回数据；名单通过 POST /api/monitor/watch 按页面独立注册，下一轮生效。
    """
    codes = {c.strip() for c in watch.split(",") if c.strip()}
    watchtower.ensure_started()
    snap = watchtower.get_snapshot()
    return {"data": {**snap, "watchlist": [r for r in snap.get("watchlist", []) if r.get("code") in codes]}}


class MonitorWatchInput(BaseModel):
    client_id: str
    codes: list[str]


@app.post("/api/monitor/watch")
def monitor_watch(body: MonitorWatchInput):
    try:
        watchtower.set_client_watch(body.client_id, body.codes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    watchtower.ensure_started()
    return {"data": {"ok": True}}


@app.get("/api/market/first-board")
def market_first_board(date: str | None = None):
    """首板分析：指定交易日的首板涨停股（连板数=1）+ 涨停原因题材串（问财，缺 key 优雅降级）。

    `date` 不给就用 `firstboard.data_session()` —— **库里实际有数的那一场**，
    不是"今天"，也不是日历上的交易日（09-25 中秋休市、09-24 库里没有，都不该显示）。
    客观公开榜单数据（东财涨停池同款），只呈现事实，不附推荐/评分/预测/买卖时机。缓存 10 分钟。
    """
    try:
        return {"data": firstboard.get_first_board(date)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"首板数据异常：{e}") from e


@app.get("/api/market/turnover-top")
def market_turnover_top():
    """全市场成交额榜 Top20（客观公开榜单数据，非推荐/非预测/不评分）。全站共享缓存 5 分钟。"""
    try:
        return {"data": market.get_turnover_top()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"成交额榜异常：{e}") from e


@app.get("/api/market/sector-cycle")
def market_sector_cycle(date: str = ""):
    """板块周期榜（蓄势/点火/延续/退潮）。

    这不是"板块涨跌榜"——那已经有 /api/market/overview。它给的是**某板块处在周期
    哪一段**，五要素（蓄势/位置/点火/量能/广度）分开可见，页面要能回答
    "凭什么说它点火"。date 省略时取本地库里最新一场已算好的。

    取不到就 available=False 并说清原因，**不拿在线板块涨幅榜凑数** —— 那样读者
    会以为"没点火"是市场事实，其实是我们没算。
    """
    from duanxian import pg as _pg
    from duanxian import pg_sources as _pgs

    if not _pg.enabled():
        return {"data": {"available": False,
                         "reason": "板块周期榜读本地库 analytics.ignition；"
                                   "未配置 .env 的 PG_USER/PG_PASSWORD 时没有这一项"}}
    d = (date or _pg.latest_session("analytics.ignition") or "").strip()
    if not d:
        return {"data": {"available": False, "reason": "本地库里还没有算好的板块周期数据"}}
    try:
        rows = _pgs.sector_cycle(d)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"板块周期取数异常：{e}") from e
    if rows is None:
        return {"data": {"available": False, "date": d,
                         "reason": f"本地库 {d} 这一场没有板块周期数据"}}
    return {"data": {"available": True, "date": d, "sectors": rows,
                     "caveat": "230 个行业板块缺涨跌家数、广度分项算不出，规则上永远不会被判为点火；"
                               "昨日涨停/昨日突涨这类是通达信的行为统计板块，不是题材"}}


@app.get("/api/market/sector-rotation")
def market_sector_rotation(date: str = "", basis: str = Query(default="", description="share(默认) / turnover")):
    """资金轮动本地榜，两条口径：`share`=份额（默认）、`turnover`=换手强度。
    近 10/20/30 场的累计位移、连续场次分布与头部板块曲线。

    读 `analytics.sector_flow_trend`（数据加工按场写入的统计量），不缓存 —— 本地库读一次约 1 秒，
    而缓存要把"这份结果多久了"一起交出去，这里直接用库里的 `calculated_at` / `age_seconds` 更准。

    两条口径剔的是两件不同的事，**不是同一数的两种写法**：份额剔"全场共同放量"，
    换手剔"这个板块盘子本来就大"（2026-10-12 实测：同一场里 银行 份额名次 57 而换手名次 822）。

    ⚠️ 这一屏只交统计量，不交方向判断：实测"份额连续 n 场抬升"之后**下一场仍抬升**的概率
    从 46%(n=1) 单调掉到 31%(n=9)，基线本来就是 46% —— 越连续越不延续，是均值回复不是惯性。
    所以界面不许把它写成"主力连续吸金"，也不许给它配红色箭头。
    """
    from duanxian import flow_trend

    try:
        data = flow_trend.rotation_board(date or None, basis or None)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"轮动榜取数异常：{e}") from e
    return {"data": data}


@app.get("/api/stock/indicators")
def stock_indicators(code: str = Query(...), date: str = ""):
    """个股技术指标读数（MA/MACD/Bias/ATR/CCI/量比/250日位置/相对板块强度…）。

    数据来自本地库 `analytics.indicator_value`（按日已算，35 项）—— 不是现算，
    也不是信号：只回答"现在处在什么位置"，买卖判断是使用者的。date 省略取最新一场。
    本地源没配好时 available=False 并说清原因，不回退去装一个指标库现算。
    """
    from duanxian import pg as _pg
    from duanxian import pg_sources as _pgs

    if not _pg.enabled():
        return {"data": {"available": False,
                         "reason": "技术指标读本地库 analytics.indicator_value；"
                                   "未配置 .env 的 PG_USER/PG_PASSWORD 时没有这一项"}}
    try:
        data = _pgs.indicators(code, date)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"技术指标取数异常：{e}") from e
    if data is None:
        return {"data": {"available": False, "code": code, "date": date or None,
                         "reason": "本地库里没有这只票这一场的指标值（代码写错、停牌或尚未计算）"}}
    return {"data": data}


# --- quantdash 口径轨（PG schema `qd`）：与本轨并排展示，永不合并 -------------
#
# 契约：`页面展示工作指南/02-quantdash逻辑双轨展示.md` §8；算法侧
# `数据加工工作指南/09-quantdash逻辑规格.md`。逻辑来源 quantdash-ai-stock-main
# （PolyForm Noncommercial），本项目只重实现其算法口径、未复制源码。
#
# 三条：`own` 与 `qd` 两个键不合并；算不出就是 null；取不到就 available=False +
# reason，**绝不回落到"今天"或邻近交易日**（邻近场次的数不是这一场的答案）。

@app.get("/api/qd/sentiment-cycle")
def qd_sentiment_cycle(date: str = ""):
    """情绪周期（quantdash 口径轨）。

    三个 stage 并存是事实不是 bug：quantdash 自己的页面 / 它的 AI(MCP) / 它的飞书
    机器人用的是**三套互相矛盾的阈值**，读到不同阶段。三列都交出，不一致由界面打 ⚠。

    `own` 恒为 null —— 本轨（YuanShan）没有情绪阶段算法，"情绪五档"只有 UI 没有计算。
    补上之前界面要如实显示"本轨暂无阶段算法"，不许拿 qd 轨的值冒充本轨。
    """
    from duanxian import qd as _qd

    try:
        return {"data": _qd.sentiment_cycle(date)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"quantdash 口径情绪周期异常：{e}") from e


@app.get("/api/qd/board-ladder")
def qd_board_ladder(date: str = ""):
    """连板天梯 + 龙头 + 板高度（quantdash 口径轨）。

    `seal_time_available=false` 时界面必须把封板时间整列降级并挂横幅：历史里只有
    2026-09-22 有分钟线，其余交易日封板时间**根本不可得**，而 quantdash 原版在
    这种情况下用代码哈希编造时间。`leader_tie_break` 回答"龙头是怎么选出来的"——
    没有封板时间时我们的选择并不等价于它的原口径，这点必须可见。
    """
    from duanxian import qd as _qd

    try:
        return {"data": _qd.board_ladder(date)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"quantdash 口径连板天梯异常：{e}") from e


@app.get("/api/qd/sector-cycle")
def qd_sector_cycle(date: str = ""):
    """板块周期（quantdash 口径轨）= 轮动矩阵 + 持续性统计。

    ⚠️ 与 `/api/market/sector-cycle`（本轨 `analytics.ignition` 的五档生命周期阶段）
    **不是同一个东西，也不许合并成一张表**：quantdash 的"板块周期"没有阶段枚举，
    它回答"领涨板块换了几手、谁连庄最久、强度在增还是减"。
    """
    from duanxian import qd as _qd

    try:
        return {"data": _qd.sector_cycle(date)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"quantdash 口径板块周期异常：{e}") from e


@app.get("/api/qd/sector-follow")
def qd_sector_follow(date: str = "", lag: int = Query(2, ge=0, le=3,
                                                     description="形状匹配错开几场名次")):
    """当日前三甲 + 它们前五场名次 + 与它们轨迹最像的板块（用户 2026-10-25 点名）。

    判据只住 `app/compute/qd_sector_follow.py` 一份：算好落进 `qd.sector_follow`
    （接在加工链 `qd_sector` 那一步里，不另开步数），这里与 `duanxian/qd_follow.py`
    都只读落库行 —— 本模块不再有任何现算。为什么必须落库：`qd.sector_rotation`
    每天只落前 8 名（写侧 L-16 那条偏差就这么记的），8 名之外"某天排第几"库里没有，
    形状匹配要回母体逐场重排全 914 个板块；这件事每天只需做一次，放在读侧等于每个
    访客重付那两秒。同一场同一档谁打开都是同一批数，批次时刻随数据交出。
    四档 lag（0/1/2/3）都落库，所以这个参数不是"能填但读不到"的摆设。
    """
    from duanxian import qd_follow

    try:
        return {"data": qd_follow.read(date=date, lag=lag)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"quantdash 口径前三甲与相似板块异常：{e}") from e


@app.get("/api/qd/distribution")
def qd_distribution(days: int = Query(0, ge=0, le=5000)):
    """三套 stage 判定的区间分布 + 龙头 tie-break 分布（quantdash 口径轨）。

    存在的理由：只看某一天，"天天退潮"像故障；把上千天摊开才看得见 quantdash
    优先级链自身的偏置（退潮排第一且含常态条件，冰点永远轮不到）。这是**它逻辑
    的性质**，不是本实现算错 —— 所以界面只呈现分布与优先级，不改判定。

    `days=0` = 全表；>0 = 最近 N 个已算好的交易日。
    """
    from duanxian import qd as _qd

    try:
        return {"data": _qd.distribution(days=days)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"quantdash 口径区间分布异常：{e}") from e


@app.get("/api/qd/news")
def qd_news(date: str = "", limit: int = Query(200, ge=1, le=500)):
    """本地快讯只读轨（`duanxian/newswire.py` 落的 jsonl，quantdash 口径采集）。

    三条硬规矩落在取数层：date 缺省取**最新采集文件**而不是"今天"；当天没采就
    `available=False` + reason（界面绝不回落示例快讯）；`sentiment`/`level` 为
    None 就交 None（**不折成"中性"** —— NULL 是"没做情感分析"）。
    """
    from duanxian import qd as _qd

    try:
        return {"data": _qd.news(date=date, limit=limit)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"本地快讯读取异常：{e}") from e


# --- 数据覆盖体检轨 --------------------------------------------------------
#
# 存在的理由：页面上一列空白有两种完全不同的原因 ——「这几天本来就不是交易日」和
# 「这张表根本没数」，混成一个"暂无数据"用户就没法判断该等、该补还是该怀疑库坏了。
# 判据只认**数据日期**，不认文件时间不认状态栏（本项目铁律）。

@app.get("/api/coverage")
def api_coverage(table: str = "all", refresh: bool = Query(False)):
    """各表覆盖哪段、区间内缺哪几个交易日、还是压根没这张表。

    `table` = 表 key 或 `all`。**页面首屏一律按表单表拉**（命中磁盘缓存 0.001 秒/张，
    单张实算最贵的也才 5 秒）；`all` 冷跑要 55 秒，只给后台预计算和"重算"按钮用
    （`?refresh=true` 强制重算）。常驻进程每日 18:20 自动重算一次。
    走缓存时 `cache.{source,saved_at,age_seconds,is_stale,very_stale}` 必须显示出来，
    `generated_at` 是**算出来那一刻**不是"现在" —— 新鲜度不许被缓存隐掉。
    状态五枚举不许合并：`ok` / `gaps` / `no_data`（表在能读但 0 行）/
    `missing_table`（未建）/ `no_access`（无 SELECT 权限）—— 后两种现在最容易被
    前两种掩盖。大表 `rows` 是 `pg_class.reltuples` 估算，`rows_kind` 会标出来。
    """
    from duanxian import coverage

    try:
        return {"data": coverage.report(table=table, refresh=refresh)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"数据覆盖体检异常：{e}") from e


# --- 量化选股轨（读 `analytics.score` 家族）--------------------------------
#
# 用户 2026-09-25 点名："导航栏一个都没增加……把这么多的数据全部体现出来。"
# `analytics.score` 有 194 万行、新鲜到 09-23、8 个 capability 齐全，却一个页面都没读过它。
# 这两个接口是「量化选股」模块的数据底，页面由 code：页面展示 建。

@app.get("/api/quant/quadrant")
def quant_quadrant(date: str = "", min_x: float = Query(default=None),
                   min_y: float = Query(default=None), limit: int = Query(default=200, ge=1, le=1000),
                   refresh: bool = Query(default=False)):
    """四象限选票（四维象限）：X=个股质量、Y=环境共振，`oper=0.40X+0.60Y`。

    和复盘页那套"判对错×盈亏"的归因象限**是两个不同的东西**，语义正交，两边都保留，
    谁也不许翻译成对方的一格。`axes` 里带两轴的构成权重与合成公式 —— 没有它，
    "Q3 试探"三个字对读者没有意义。`null_coverage` 交出象限为空的实测比例：
    四格相加不等于全市场票数，空的那部分显示 `—` 而不是 0。
    `refresh=1` 重算那几项全库聚合（刚回填完时用）；默认一小时算一次，年龄随数据交出。
    """
    from duanxian import quant

    try:
        return {"data": quant.quadrant(date, min_x=min_x, min_y=min_y, limit=limit, refresh=refresh)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"四象限取数异常：{e}") from e


@app.get("/api/quant/scores")
def quant_scores(code: str = Query(..., min_length=6, max_length=16), date: str = ""):
    """单票八个 capability 的加权明细：分量、权重、贡献逐项可见。

    分值是**百分位化的相对量**，不是绝对好坏；只描述"处在什么位置"，不给买卖信号。
    """
    from duanxian import quant

    try:
        return {"data": quant.scores(code, date)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"评分明细取数异常：{e}") from e


@app.get("/api/quant/score-trend")
def quant_score_trend(code: str = Query(..., min_length=6, max_length=16),
                      window: int = Query(10, ge=2, le=120),
                      sessions: int = Query(60, ge=1, le=500),
                      refresh: bool = Query(default=False)):
    """评分趋势：窗口斜率 + 相关性 + 连续上涨天数 + sparkline（`analytics.score_trend`）。

    库里 `window_days` 目前只有 10 一种。**要别的窗口直接返回不可用并列出实有窗口**，
    不悄悄改成 10 天代答 —— 那是把不同长度的斜率混成同一个数。

    `window_entry_quadrant` 是**进窗口第一天**的象限，与 `/api/quant/quadrant` 的
    "当日象限"是两个问题的答案，两者分布不同是应该的，界面别去对齐它们。
    它为空（约一半的场）时页面给 `—`，比例随 `null_coverage` 交出。
    """
    from duanxian import quant

    try:
        return {"data": quant.score_trend(code, window=window, sessions=sessions, refresh=refresh)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"评分趋势取数异常：{e}") from e


@app.get("/api/quant/rank-stability")
def quant_rank_stability(days: int = Query(7, ge=2, le=20),
                         limit: int = Query(50, ge=5, le=200),
                         date: str = '', refresh: bool = Query(default=False)):
    """近 N 场**全市场名次之和**从低到高（用户 2026-10-15 点名 3 / 7 / 11 三档、前 50 名）。

    名次读的是 `analytics.score.rank_in_market`（`capability_key='oper_score'`），那份名次
    每天每票一行**本来就落库**（写入方 `app/compute/scoring.py`），这里只求和不另存副本。

    两条读法上的硬口径（都随数据交出，界面必须一起显示）：
     · 只有 N 场**每场都有名次**的票进榜 —— 缺场次还求和，等于让少加一项的票假优；
       被排除多少只在 `universe.excluded_partial`。
     · 窗口是交易日不是日历日；名次是"当日全场第几名"，各场母体只数略有出入。
    `days` 不在 3/7/11 里时**直接返回不可用并列明可选项**，不悄悄换成 7 场代答。
    """
    from duanxian import quant

    try:
        return {"data": quant.rank_stability(days=days, limit=limit, date=date, refresh=refresh)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"名次稳定榜取数异常：{e}") from e


@app.get("/api/quant/rank-momentum")
def quant_rank_momentum(metric: str = Query('xy', max_length=8),
                        limit: int = Query(80, ge=5, le=200),
                        date: str = '', refresh: bool = Query(default=False)):
    """近 6 场**名次稳步前进**的票（用户 2026-10-18 点名：「展示这个 xy 名次近 6 个交易日稳步增长的个股，
    是名次！可以手动切换成总分稳步增长的」）。

    `metric` 只有两档：`xy` = X 名次 + Y 名次（两轴合名次，两轴各自都要净前进）、
    `oper` = 综合分名次。别的值直接返回不可用并列出可选项，不悄悄换成 xy 代答。

    ⚠️ 这里的「增长」读的是**名次往前走**（数字变小，1 = 全场第一），不是数字变大 ——
    判据、门槛与两条实测口径（Y 轴名次只有几十个档位、名次是相对量）都随返回体交出。

    与 `/api/quant/rank-stability` 的分工：那张答「这几天谁一直靠前」（名次之和越小越好），
    这一张答「这几天谁一直在往前走」（名次的变化方向）。读同一列 `rank_in_market`，都不另存副本。
    """
    from duanxian import quant

    try:
        return {"data": quant.rank_momentum(metric=metric, limit=limit, date=date, refresh=refresh)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"名次稳步前进榜取数异常：{e}") from e


@app.get("/api/quant/momentum-follow")
def quant_momentum_follow(metric: str = Query('xy', max_length=8),
                          date: str = '',
                          limit: int = Query(80, ge=5, le=200),
                          refresh: bool = Query(default=False)):
    """上一场**名次稳步前进榜**前 N 名的后续股价表现（用户 2026-10-20 凌晨点名：
    「昨日前 80 名的进入股价表现（把各种名次都显示出来，包括最近倍量名次，突破信号等等）」）。

    母体＝`date`（默认＝库里最新有名次场次的**前一场**，因为要有一场「母体之后的第一场行情」
    才算得出后续表现，这件事随 caveats 原样上屏）那场稳步前进榜交出的前 N 名，与它同一档
    `metric`（xy / oper 可切，坏值由母体的 `_reject` 原样拒掉，不换成 xy 代答、
    也不借用「这一场没有数据」那句）。
    判据只住 `duanxian/quant.py::rank_momentum` 一处，本端点直接调它取母体，没有第二份名次 SQL。

    表现窗口＝「今日涨跌 + 近 5 日涨跌」两栏（后复权，尺子只住 `duanxian/day_returns.py`，停牌不桥接）：
    今日＝母体之后的**第一场**（键名 `chg_next_day`；母体默认是前一场 ⇒ 那一场就是今天，
    10-27 用户点名把旧列名「次日涨跌」改过来）；近 5 日＝读至日往前 5 个市场场次的累计
    （`chg_5d`，**不以母体日为起点** ⇒ 两列不许相减；旧「至今累计」在母体=前一场时与今日是同一个数，已撤下）。
    **不逐日全铺**——这张榜每天换人，逐日铺会是几百列；逐日那一族看 `/api/quant/energy-track`。
    各路名次/在榜：综合分全场名次与象限、倍量加权分全场名次（`dbl_rank`）、突破信号类型、
    埋伏**榜内**名次（这张表只落命中的票，与全场名次不是同一把尺，列名已分开卖）、
    异动筛查母体、趋势股 5/10 入选；名次规则原文随 `rank.rule` 交出（`duanxian/ranking.py` 那一份）。

    ⚠️ 缺任何一边一律 null、界面显示 —：**0 与「否」都是实质读数**（平盘、没进这一族榜），
    每行带 `perf_missing_reason`（今日那一栏）/`perf5_missing_reason`（近 5 日那一栏）/
    `dbl_missing_reason` 原因码；哪一族表那天整场没行，
    caveats 会点名「那天没跑」，不拿「不在榜」充数。
    """
    from duanxian import momentum_follow

    try:
        return {"data": momentum_follow.board(metric=metric, date=date, limit=limit, refresh=refresh)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"名次稳步前进榜后续表现取数异常：{e}") from e


@app.get("/api/quant/new-high")
def quant_new_high(group_id: int = Query(default=None, ge=1),
                   code: str = Query(default='', max_length=16),
                   date: str = '', window: str = Query('all', max_length=8),
                   limit: int = Query(200, ge=1, le=500),
                   refresh: bool = Query(default=False)):
    """自选（可限定分组/单票）里谁在**库内新高**、谁离新高还差几个点。

    名字要按数据现实叫：本机日线最早 **2021-08-02**（PG 与通达信本地 `.day` 同一起点），
    而 `reference_data.security.list_date` 全表为空 —— 拿不到上市日，所以这里给不出
    "上市以来新高/历史新高"。窗口、母体、复权口径、缓存年龄都随数据交出，
    判据只住 `duanxian/quant.py:new_high()` 一处。

    ⚠️ 第一次算要扫自选里 1,093 只 × 五年日线（实测十几秒），之后一小时走进程内缓存；
    `cache_age_seconds` 与 `caveats` 会说清这次是不是缓存值。
    """
    from duanxian import quant

    try:
        return {"data": quant.new_high(group_id=group_id, code=code, date=date,
                                       window=window, limit=limit, refresh=refresh)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"自选股新高取数异常：{e}") from e


@app.get("/api/quant/security-search")
def quant_security_search(q: str = "", limit: int = Query(20, ge=1, le=100), kinds: str = ""):
    """选一只票：代码 / 名称 / **全拼** / **拼音首字母** 四种匹配（`reference_data.security`）。

    用户 2026-10-15 点名：「搜索个股的地方，能否支持股票名称的首字母搜索？这个应该也是个通用控件吧，
    你整个代码都梳理一下，把这个控件也统一」。数据侧早就有 `pinyin`/`pinyin_abbr` 两列
    （10-15 实测 9,551 条里 9,439 条有值；5,577 只股票里 30 只没登记），
    `app/db/repo/security.py::search()` 也写好了却**一个调用者都没有**——这一发把它接上界面。

    三条判读口径随体交出，界面不许自己编：
     · `matched_by`（这一条凭什么命中的：code/name/abbr/pinyin/other）；
     · 读不到 ≠ 没有（`available:false` + `reason` 原文是"查询失败"，不是"没这只票"）；
     · 0 命中时带 `py_gap`：那 30 只没拼音的股票用首字母**必然搜不到**，要说清是参考数据缺。
    `kinds`（逗号分隔，如 `stock,etf`）给"只能填可交易标的"的入口收窄；默认不收窄，
    但每条都带 `asset_label`，指数就写着指数——不悄悄滤掉，也不让人填错了还不知道。
    """
    from duanxian import security_search as _ss

    # 搜索框是输入辅助：慢一拍比"红屏"更糟，所以这里不抛 502，把失败写进体里由控件念出来
    return {"data": _ss.search(q, limit=limit, kinds=kinds)}


@app.get("/api/quant/volume-double")
def quant_volume_double(date: str = "", limit: int = Query(150, ge=1, le=800),
                        min_energy: float = Query(0.0, ge=0, le=40,
                                                  description='加权分下限（2026-10-19 起：'
                                                              '不再是倍量次数）'),
                        quadrants: str = Query('', description='四维象限多选，逗号分隔；'
                                                              '可用 Q1/Q2/Q3/Q4/none'),
                        step_gap_min: float | None = Query(None, ge=-40, le=40,
                                                           description='加权分较前一整窗增减的下限；'
                                                                         '不给＝不筛，给了就在库里筛')):
    """倍量形态：40 日**量能加权分** × 贴 MA60 震荡（`analytics.volume_double.dbl_energy`）。

    这是**形态统计，不是买卖信号**。分母与窗口随体交出（`bars` / `ma60_days` /
    `win_days`），因为不足 40 日的新票窗偏短、分低不代表不活跃。"贴着均线"(`near_ma60`)
    与"站上均线"(`above_ratio`) 是两件事，分开给。

    ⚠️ **2026-10-19 换了尺**：主列从 `dbl_count`（日量比 ≥2.0 才算一次的硬计数）换成
    `dbl_energy`（量比按 0.1 切档，**得分 = Σ 每档（这一档在窗口里出现的次数 × 这一档的分值）**，
    分值在「系统配置」里可改）。没有系数、没有占比、没有归一化这一层。
    旧计数只在行里留作对照（`dbl_count_legacy`），不参与筛、不上屏。
    `energy` 那块交的是尺子自己：生效指纹、库里是否同一批（`in_sync`）、这一份配置的
    数学上限 `scale.max_score` 与实测分位 —— **加权分的单位是"分"**：默认档表顶档一天记 1 分，
    满窗（40 场全是顶档放量）上限就是 40 分，所以界面上填筛值要照 `scale` 来，不能照次数。
    当日名次按它排，同分同名、下一名顺延（`rank.rule`，实现只住 `duanxian/ranking.py`）。

    ⚠️ 覆盖区间随体交出（`coverage`：起止与场数现算，一小时内复用），别把它的分布当长期规律；
    `definitions`（取自库里的列注释 + 读侧派生列 `dbl_step_gap` 的口径原文）一并交出，
    界面不必把字段含义写死。

    `quadrants` 与 `min_energy` 都在**库里**筛（拼在 `order by … limit` 之前），
    不是在已交出的 150 行里筛 —— 后者会让"选 Q2 只剩 3 行"被读成"全场只有 3 只 Q2"
    （实测全场 1,152 只）。每档全场实有几只随 `quadrant_counts` 一起交出。

    `step_gap_min`（「稳步提升」）同样**拼在 limit 之前**：库里筛
    `dbl_step_gap >= step_gap_min`，即"本窗加权分 − 紧邻前一整窗（同一只票往前数第
    win_days 行那场）≥ 该值"。默认不给＝不筛；界面「只看稳步提升」发的是
    `thresholds.step_gap_min`（阈值只住 `duanxian/quant.py` 的 STEP_GAP_MIN 一处）。
    没有可比前窗的行交 NULL（**不是 0**），每行带 `dbl_step_gap_null_reason` 说明是哪一种；
    这一档全场可比母体里实有几只随 `step_gap_pool` 交出（没读到是 null，不是 0 只）。
    """
    from duanxian import quant

    try:
        return {"data": quant.volume_double(date, limit=limit, min_energy=min_energy,
                                            quadrants=quadrants, step_gap_min=step_gap_min)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"倍量形态取数异常：{e}") from e


@app.get("/api/quant/energy-track")
def quant_energy_track(date: str = "",
                       top_n: int | None = Query(
                           None, ge=10, le=500,
                           description='母体＝当日得分前 N 名；不给＝用后端默认 '
                                       'thresholds.top_n_default（120）'),
                       refresh: bool = Query(
                           False, description='清掉本接口这一场这一批的一小时缓存重算；'
                                              '刚回算完倍量分时用它立刻看新数')):
    """倍量**加权分进榜后的逐日涨跌幅**（用户 2026-10-19 点名的第二块）。

    母体＝基准日这一场**得分前 N 名**（`top_n`，默认 120；同分并列一起进来，只数可略多）；
    每只票的起算日是它全史里**第一次排进前 N 名**那一场；格子＝起算日到今天的**每一场**当日涨跌幅（后复权，
    判据只住 `duanxian/day_returns.py`，与倍量榜那两列同一把尺）。
    默认按「整体涨跌幅」从低到高排（`rows` 已排好，页面不必再排一次才能对上悬停）。

    ⚠️ **列数不定、一直铺到今天**（用户点名不限行数、分页 60 在前端；`page_size` 随体交出）：
    `rows[i].series` 长度可以差到 200 场，`max_series_len` 是这批里最深的一条，
    界面按它开列，起算日不同的票在同一列上代表**各自的后第 N 场**（不是同一个日历日）。
    ⚠️ `first_censored=true` 说明这只票的首次进榜日落在/紧贴库里最早那一场，
    真正的首次**可能更早、看不到** —— 界面要念「不晚于该日」，不许念成「就是该日」。
    ⚠️ `chg_cum=null` 是算不出整体涨跌幅（起算日前一天没有行），显示 —，**不是 0%**。
    ⚠️ 取数按「这一场 ＋ 倍量配置指纹 ＋ 名次线」缓存一小时（10-26 实测：整发 13.1 秒里
    8.1 秒在母体那条名次 SQL、3.5 秒在逐日涨幅那条扫遍 12 个年度分区的 SQL，两处都改了。
    同一条链路改后 2.5 秒：名次那条 8,087→39 毫秒、逐日那条 3,503→996 毫秒，
    行数与加权分合计逐位不变；界面上冷发 12.5 秒→3.07 秒、热发 11.8 秒→0.69 秒，
    那 0.69 秒是 1.88 MB 出体加 gzip，库里一件没跑）：
    配置一改指纹就换、键跟着换，旧键不会把上一批的数顶给这一批；刚回算完要看新数就带 `refresh=1`。
    """
    from duanxian import quant

    try:
        return {"data": quant.energy_track(date, top_n=top_n, refresh=refresh)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"加权分进榜后的逐日走势取数异常：{e}") from e


@app.get("/api/config/volume-energy")
def config_volume_energy_get():
    """倍量加权分那张档表：当前生效配置、可选档名/作用域、数学上限、实测分布、
    库里批次与后台重算现场（`job`）。界面逐字读，不在前端抄第二份档表。

    这里不交「量纲选项」：第一稿的 分值÷Σ分值 已被用户否掉，`scale.formula` 是唯一算式原文。
    """
    from app.compute import volume_energy as venc
    from duanxian import ranking

    return {"data": {"profile": venc.profile(), "fingerprint": venc.fingerprint(),
                     "scale": venc.scale(), "status": venc.status(),
                     "bands": [{"label": lab, "lo": lo, "hi": hi}
                               for (lo, hi), lab in zip(venc.BANDS, venc.BAND_LABELS)],
                     "default_scores": dict(venc.DEFAULT_SCORES),
                     "scopes": list(venc.SCOPES), "markets": list(venc.MARKETS),
                     "top_n_default": venc.DEFAULT_TOP_N,
                     "page_size": venc.PAGE_SIZE,
                     "rank_rule": ranking.RULE_NOTE,
                     "rank_rule_short": ranking.RULE_NOTE_SHORT,
                     "job": venc.job_state()}}


@app.post("/api/config/volume-energy")
def config_volume_energy_set(payload: dict, request: Request):
    """存档表并**当场起一次全量重算**（用户要的机制：改配置→回算覆盖，不许"存了没跑"）。

    校验全在 `volume_energy.validate`（一处）：非法就 422 原样说原因，半份配置都不存。
    合法且指纹变了 ⇒ 后台线程跑 `rebuild()`（约 2 分钟，211 场全量），界面读 `job` 看进度；
    没变（改完又改回来）⇒ 不重算，直接说"口径没变"。
    ⚠️ **保存 ≠ 已生效**：重算跑完之前库里还是旧批，倍量榜会带 `energy_batch_stale` 那句
    —— 这一条必须在界面上看见，不能等用户自己猜（同「保存了不等于会推送」那条纪律）。
    """
    from app.compute import volume_energy as venc

    who = f"api:/api/config/volume-energy@{request.client.host if request.client else '未知来源'}"
    try:
        saved = venc.save(payload)
    except ValueError as e:
        raise HTTPException(422, f"这份档表没采纳：{e}") from e
    job = venc.start_rebuild_async(f"{who} 改了加权分档表") if saved['needs_rebuild'] \
        else {'started': False, 'reason': '口径与库里批次一致，不需要重算'}
    return {"data": {"saved": saved, "rebuild": job, "job": venc.job_state(),
                     "scale": venc.scale()},
            "reason": ('已保存，正在按新档表重算历史场（这一档筛的数在跑完前还是旧批）'
                       if job.get('started') else
                       '已保存（口径没变，不需要重算）' if saved['needs_rebuild'] is False
                       else '已保存，但重算没起来：' + str(job.get('reason')))}


@app.get("/api/quant/ambush")
def quant_ambush(date: str = "", limit: int = Query(150, ge=1, le=800),
                 types: str = Query('', description='埋伏类型多选，逗号分隔：'
                                                    'sticky_pull/pullback_ma20/pullback_ma60/'
                                                    'obv_bid/dragon_back'),
                 only_strict: bool = False,
                 min_weekly_ups: int = Query(0, ge=0, le=10)):
    """埋伏票（左侧机会）榜：`analytics.ambush_setup`，与「突破信号」同一屏的左栏。

    判据与阈值只住 `app/compute/ambush_setup.py`；这里只读出算好的行。
    `types` 在**库里**筛（拼在 `order by … limit` 之前），每类全场实有几只随 `type_options` 交出 ——
    在前端筛就只能筛到合成分最高的那 150 只，"这一类剩几行"会被读成"全场只有几类"。
    """
    from duanxian import quant

    try:
        return {"data": quant.ambush(date, limit=limit, types=types,
                                     only_strict=only_strict, min_weekly_ups=min_weekly_ups)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"埋伏票取数异常：{e}") from e


@app.get("/api/quant/confluence")
def quant_confluence(date: str = "", min_hits: int = Query(1, ge=1, le=6),
                     sources: str = Query('', description='交叉源多选，逗号分隔：'
                                                        'voldbl/quadrant/ambush/first_break/dgt/sysentry'),
                     limit: int = Query(300, ge=1, le=1000)):
    """选股汇总：各条量化链当天选出的票并到一张榜上，看谁被多个判据**重复**点到。

    排序口径（用户 2026-10-13 选定）：命中榜数优先，其次各榜里最好的名次 —— 不做合成总分。
    源清单与各榜选法只住 `app/compute/confluence.py` 的 `SOURCES` 一处，本接口只读落好的行。
    `min_hits` 与 `sources` 都在**库里**筛（拼在 `order by … limit` 之前），
    每场"命中 N 榜的各有几只"走一条不带筛选的全场聚合（`hits_distribution`）。
    """
    from duanxian import confluence_view

    try:
        return {"data": confluence_view.summary(date, min_hits=min_hits, sources=sources, limit=limit)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"选股汇总取数异常：{e}") from e


@app.get("/api/quant/confluence-history")
def quant_confluence_history(code: str = Query(..., min_length=6, max_length=16),
                             date: str = "", sessions: int = Query(250, ge=1, le=500)):
    """一只票**历史上**被这些榜同时点到过哪些天（"重复出现"的时间维度，不是只有今天）。"""
    from duanxian import confluence_view

    try:
        return {"data": confluence_view.history(code, date=date, sessions=sessions)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"交叉命中历史取数异常：{e}") from e


@app.get("/api/quant/intraday-spike")
def quant_intraday_spike(date: str = "",
                         tags: str = Query('', description='事件标签多选，逗号分隔：'
                                                            'fast/quick/slow/touched/sealed/'
                                                            'held5/late/reopen'),
                         min_hi: float = Query(0.0, ge=0.0, le=30.0,
                                               description='只看在盘中最高较前收不低于这个点数的票'),
                         limit: int = Query(400, ge=1, le=1000),
                         days: int = Query(43, ge=2, le=250)):
    """异动筛查：盘中「瞬间冲高」与「触及涨停」的逐事件榜 + 之后走势的分布复盘。

    母体与阈值只住 `app/compute/intraday_spike.py` 一处（水平 >=5 个点 且 速度 >=4 个点/15 分钟
    或 触板），本接口只读落好的行。排序 = 用户 2026-10-13 点名要的**时间顺序**：
    日内高点出现的时刻升序。`tags`/`min_hi` 都在**库里**筛（拼在 `order by … limit` 之前）。
    次日/3 日/5 日走势不入表，在这里用 lead + 复利现算，并与**同期基线**并排交出 ——
    只交档内数字不交基线，屏幕会把"跟基线一样"读成"危险"。
    """
    from duanxian import spike_view

    try:
        return {"data": spike_view.screen(date, tags=tags, min_hi=min_hi or None,
                                          limit=limit, days=days)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"异动筛查取数异常：{e}") from e


@app.get("/api/quant/system-entry")
def quant_system_entry(date: str = "",
                       only_entry: bool = Query(True, description='只列三条全过的；false 给观察池'),
                       min_gap: float = Query(0.0, ge=0.0, le=30.0,
                                              description='只列缺口幅度不低于这个点数的'),
                       limit: int = Query(300, ge=1, le=1000),
                       days: int = Query(250, ge=10, le=600,
                                         description='历史分布的窗口（场次）。每日链只算 250 场那一档，'
                                                     '换档要计算模块加档，否则这一档会说"还没算"')):
    """交易体系榜：近 20 场有涨停 × 向上跳空缺口三天不回补 × 缩量回调后一根阳线确认（组合判据）。

    用户 2026-10-14 口述的三条件体系。判据与阈值只住 `app/compute/system_entry.py` 一处，
    本接口只读落好的行；`only_entry`/`min_gap` 都在**库里**筛（拼在 `order by … limit` 之前）。
    一起交出的是这张表自己的**历史分布**（含"带好止损"那条路径：20 场内先碰 +20% 还是先碰 −7%）
    与同期基线并排 —— 这套筛法实测右尾更肥、期望更差，两句必须同时说。
    """
    from duanxian import system_entry_view

    try:
        return {"data": system_entry_view.read(date, only_entry=only_entry,
                                               min_gap_pct=min_gap or None, limit=limit,
                                               days=days)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"交易体系榜取数异常：{e}") from e


@app.get("/api/quant/system-entry-cohort")
def quant_system_entry_cohort(months: int = Query(6, ge=1, le=24,
                                                  description='往回看几个自然月（不是场次）'),
                              refresh: bool = Query(False, description='绕过缓存现算')):
    """交易体系榜下面那一叠：最近 N 个月里三条全过的个股名单（用户 2026-10-22 点名）。

    每行交三样：符合日与其证据读数、后 1/2/3/5/10/15 个场次的涨幅（累计为主、单日在悬停）、
    前 2 场~后 5 场共 8 格在交叉命中榜里的名次与原值（四维象限那行另带 Q1~Q4 档位）。
    判据一块都不在这儿重算：母体读 `analytics.system_entry`，位次读 `analytics.confluence_daily`，
    档位与分数读 `analytics.score`，源清单读 `app/compute/confluence.py` 的 `SOURCES`。
    整批一次算完 ~8 秒，所以进程内缓存一小时并交出 `generated_at`/`cache_age_seconds`；
    刚回填过要立刻看新数就带 `refresh=1`。一页 20 行是界面切的（`page_size`），不是这儿截的。
    """
    from duanxian import system_entry_cohort

    try:
        return {"data": system_entry_cohort.read(months=months, refresh=refresh)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"近 6 个月名单取数异常：{e}") from e


@app.get("/api/quant/stock-dossier")
def quant_stock_dossier(code: str = Query(..., min_length=6, max_length=16),
                        date: str = ""):
    """一只票的量化档案：四维象限 / 趋势评分八分量 / 倍量形态 / 启动前画像 / 板块热度 /
    埋伏 / 突破 / 交叉命中，全部按**这一只票**的角度交出（用户 2026-10-13 点名）。

    判据一块都不在这儿重算：每块读的是各自那张表已经落好的行，八分量直接叫
    `quant.scores()`（与 `/scores` 同一个函数），概念归属叫 `pg_sources.concepts_by_code()`。
    八块并行取（串行实测 ~3 秒），任何一块失败只让那一块 `available:false` 并带出异常原文，
    整份照交 —— 缺席分三种（查询失败／榜没算到这天／算了但没进榜），措辞不许合并。
    """
    from duanxian import stock_dossier

    try:
        return {"data": stock_dossier.dossier(code, date=date)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"个股量化档案取数异常：{e}") from e


@app.get("/api/quant/volume-breakout")
def quant_volume_breakout(date: str = "", limit: int = Query(150, ge=1, le=800),
                          signal_type: str = ""):
    """缩量收敛后首次突破形态信号（`analytics.volume_breakout_signal`）。

    用户 2026-10-11 的原话拆成三段式：
      ① 2 个月前有过一波放量（启动痕迹，vol ≥ 前5日均量 ×1.8）
      ② 之后持续缩量到均线附近（筹码沉淀，连续 5 天以上 |close/ma60-1| < 5%）
      ③ 第一个同时满足"突破均线 + 放量 ≥1.6×"的那天 = 潜在买点

    `signal_type` 可筛三类：'first_break' / 'surge_after_shrink' / 'converging'；
    不传就返回全部。这是**时序形态识别**，不是单日统计。
    """
    from duanxian import quant

    try:
        return {"data": quant.breakout_signal(date, limit=limit, signal_type=signal_type)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"突破信号取数异常：{e}") from e


@app.get("/api/quant/startup-volume")
def quant_startup_volume(cohort: str = 'board4', months: int = Query(3, ge=1, le=12),
                         limit: int = Query(200, ge=1, le=1200)):
    """启动前量能画像：涨起来之前那 30 根有效日线，量是怎么放的。

    `cohort` 三类**必须分开看**：`board4`＝近 N 月出现过 4 板及以上（连续涨停 ≥4 天）；
    `board4_45`＝同一批里再筛「前四根累计涨幅大于 45%」（基准=首板日前一天收盘，用户 2026-10-15 加的，
    它是 `board4` 的**子集**，不是第四类票）；`jump40`＝近 N 月里创业板/科创板/北证存在
    4 日累计涨幅超四成的票。
    合并成一榜等于用 10% 的尺子量 30% 的票；跨板块比强度请用 `equiv_limit_ups`。
    每行还带一根 `mood_score`（启动日那天的大盘环境，−100~100；合成口径只住
    `duanxian/pg_sources.py::mood_score()`，同一场全市场同一个值，所以没有横截面意义）。

    这是**描述统计不是信号**：母体本身就是已经涨起来的票，所以本表回答"这种票启动前长什么样"，
    不回答"放量之后会不会涨"（那要另一头的全样本对照）。`bars_used`<30 的行是次新/长期停牌，
    不能与满窗的票并排排名 —— `caveats` 里点了名。判据在 `app/compute/startup_volume.py`。
    """
    from duanxian import quant

    try:
        return {"data": quant.startup_volume(cohort, months, limit)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"启动前量能画像取数异常：{e}") from e


@app.get("/api/quant/first-board-shape")
def quant_first_board_shape(months: int = Query(60, ge=1, le=60),
                            bucket: str = Query('dbl_days'),
                            buckets: int = Query(4, ge=2, le=10),
                            metric: str = Query('ex5'),
                            board: str = Query(''),
                            complete_only: bool = Query(True),
                            exclude_locked: bool = Query(True),
                            min_bars: int = Query(20, ge=1, le=30)):
    """首板形状预示：启动前什么形状**预示能走到几板**、以及预示次日开盘进场划不划算。

    与 `/api/quant/startup-volume` 的分工就是这一屏的要点：那一屏的母体是 `len>=4`（回头看才成立，
    它的"启动后收益"带未来函数）；**这里的母体是每一个连板段的第一天**，当天就能构造、真能交易，
    所以段长 `seg*` 在这里是**结果**不是筛子。判据只住 `app/compute/first_board_shape.py`（fb-2），
    分档聚合只住 `duanxian/first_board_view.py`，页面不重算。

    控件背后就是这几个参数：`bucket` 分桶列（白名单，不在名单里退回 dbl_days）、`buckets` 档数、
    `metric` 结果指标、`board` 板别、`complete_only` 剔掉"段还在走"的那些（它们的段长是下界）、
    `exclude_locked` 剔掉"次日一字买不进"的那些。切点按**当前筛选出来的那一批**现算，
    所以每一档都交 `[lo, hi]` 实际区间 —— 换窗换板别之后 Q1 指的是另一段，页面上必须把区间念出来。
    """
    from duanxian import first_board_view

    try:
        return {"data": first_board_view.board(months, bucket=bucket, n_buckets=buckets,
                                               metric=metric, board_filter=board,
                                               complete_only=complete_only,
                                               exclude_locked=exclude_locked, min_bars=min_bars)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"首板形状预示取数异常：{e}") from e


@app.get("/api/quant/startup-quadrant-track")
def quant_startup_quadrant_track(cohort: str = Query('board4'),
                                 months: int = Query(12, ge=1, le=12),
                                 window: int = Query(30),
                                 key: str = Query('all'),
                                 limit: int = Query(200, ge=1, le=600)):
    """启动前 N 场四维象限轨迹：分数、名次、象限位置怎么挪的，以及**名次往上爬是不是更容易走到 4 板**。

    母体沿用 `/api/quant/startup-volume` 那四类（sv-2：board4 / board4_45 / board4_or_gain45 / jump40），
    一行轨迹＝(票, 启动日, cohort, capability_key)，读的是启动日**之前** N 个该票自己的有效交易日
    （不含当天，当天已涨停、分数被启动本身污染）。判据只住 `app/compute/startup_quadrant_track.py`（sqt-1），
    分档聚合只住 `duanxian/quadrant_track_view.py`，页面不重算。

    三个读数口径必须在屏幕上念出来（`caliber`/`definitions` 已带着）：
    ① `rank_delta = rank_first − rank_last`，**正数＝名次上升**（rank 越小越靠前），`buckets` 就按这一列切三分位；
    ② 象限那一族只在 `oper_score` 那一行有值，其余六个键交 null 不是 0（库里那些键没有象限这回事），
       `market_mood` 一族（同日全市场同一个数）根本不进这张表；
    ③ 窗口凑不满 N 场是覆盖事实不是缺数 —— 这张表只取 `analytics.score` 里 `is_stale=false` 的那一批
       （旧算法迁入批列注释写明不可与新结果混比，混了 `rank_delta` 就成了「旧算法减新算法」）。
    `cohort`/`key`/`window` 不在白名单里不报错也不 400：退回默认**并在 caveats 里点名**。
    """
    from duanxian import quadrant_track_view

    try:
        return {"data": quadrant_track_view.track(cohort=cohort, months=months, window=window,
                                                  key=key, limit=limit)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"启动前象限轨迹取数异常：{e}") from e


@app.get("/api/quant/startup-spike-days")
def quant_startup_spike_days(cohort: str = Query('anyfirst'),
                             months: int = Query(12, ge=1, le=12),
                             window: int = Query(30),
                             jump_pct: float = Query(0.07, ge=0.03, le=0.15),
                             limit: int = Query(200, ge=1, le=600)):
    """启动前 N 场爆量/突然拉高 × 量增/量缩（**日线代理**）：用户 10-18 那一问的答案落库读侧。

    母体两档来源：`anyfirst`＝`analytics.first_board_shape`（fb-2）全部首板（不筛结果，默认档、
    也是唯一答得了「预示」的母体）；sv-2 四类是对照。判据只住 `app/compute/startup_spike_days.py`（ssd-1），
    聚合只住 `duanxian/spike_days_view.py`，页面不重算。三条必须念出来的口径：
    ① 这是日线代理（一根交易日一个数），分钟线口径已按用户要求取消，不冒充分时图上的爆量/急拉；
    ② 联合计数是本屏的核心：`joint` 交「倍量日里涨几天、跌几天」（`dbl_up_days + dbl_down_days = dbl_days`
       是自证判据），`jumps` 交「急拉落在倍量还是缩量」；
    ③ `jump_days=0`（凑满窗口真没拉高）与 NULL（窗口不满）是两件事，分开数、caveat 点名条数。
    `jump_pct` 只是回显与口径核对——库里那批 `jump_*` 是按写侧阈值算好落库的，读侧改不动（那条 caveat
    会给要重跑的命令行）。`cohort`/`window`/`jump_pct` 不在白名单里不报错也不 400：退回默认**并在 caveats 里点名**。
    """
    from duanxian import spike_days_view

    try:
        return {"data": spike_days_view.board(cohort=cohort, months=months, window=window,
                                              jump_pct=jump_pct, limit=limit)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"启动前爆量急拉取数异常：{e}") from e


@app.get("/api/stock/facts")
def stock_facts(code: str = Query(..., min_length=6, max_length=16,
                                  description='带市场后缀的全码，如 600519.SH')):
    """个股深度复盘最上方那张「个股信息」卡：主档 + 最近一场行情/估值 + 个股档案 + 板块归属 + 本机分组。

    判据不在这一层，读侧只住 `duanxian/stock_facts.py`。三条口径由后端交出、页面不自编：
    ① 全部读本机库（不出网），所以休市也在，但可能比「个股研究」那块公开源即时值晚一场；
    ② 每个数各带自己的日期（行情按 `trade_date`，档案按它自己的 `hq_date` + 几天没刷）；
    ③ 主档里 `list_date`/`total_share` 本机全表为空、`stock_snapshot` 的 eps/bps/roe 全为 0
       ⇒ 这些一律交 null 并带说明，不拿 0 顶上（每股类与 ROE 去「财务多期」那一块读）。
    """
    from duanxian import stock_facts as SK

    try:
        return {"data": SK.board(code)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"个股信息取数异常：{e}") from e


class BriefMaterialIn(BaseModel):
    """材料组装的入参：代码＋「个股研究」那一屏本轮**真的取到的**各段原始返回（原样交，不由页面先挑）。

    为什么材料组装放后端：哪几段进、每段几条、单条截多长，是这份结论的口径；
    口径写进 TS 就成了第二份真相（且逐屏各数一遍）。取舍回执（每段 total/taken/truncated）也由同一处算。
    """
    code: str
    segments: dict[str, Any] = {}
    fetched_at: str | None = None


class BriefResultIn(BaseModel):
    """AI 那一趟的结果回执。`ok=False` 时只记账（失败原因＋这一轮材料回执），不动已有正文。"""
    code: str
    ok: bool
    brief: str | None = None
    error: str | None = None
    material_chars: int | None = None
    segments: list[dict[str, Any]] | None = None
    missing: list[str] | None = None
    model: str | None = None
    ai_provider: str | None = None
    prompt_slot: str | None = None
    prompt_source: str | None = None
    input_fetched_at: str | None = None
    word_limit: int | None = None


@app.post("/api/stock/brief/material")
def stock_brief_material(body: BriefMaterialIn):
    """把「个股研究」那一整块组成一份喂 AI 的材料，并交出**逐段取舍回执**。

    两条不是修辞的边界（`duanxian/stock_brief.py` 一处定）：
    ① 材料硬顶 8,000 字 —— 这条 AI 通道（`lib/llm.ts`）超了是直接抛，不静默截断，所以削了哪段必须留痕；
    ② 认不出的形状、空返回的段一律记进 `missing`，**不写一行看起来像读到了的话**。
    """
    from duanxian import stock_brief as SB

    try:
        return {"data": SB.compose(body.code, body.segments, body.fetched_at)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"个股说明材料组装异常：{e}") from e


@app.post("/api/stock/brief/result")
def stock_brief_result(body: BriefResultIn):
    """AI 跑完（或跑失败）之后唯一的写入口。

    成功那一路在这里**重新过一次闸**（空正文 / 出现交易动作或点位 / 超契约容忍上限都 400 拒收，
    原因原样交回屏幕）—— 正文是从浏览器交回来的，不因它已经过 AI 通道那一档的闸就免检：
    那一档按设计允许给参考价位与止损，而这一格只放"这是一家什么公司"。
    失败那一路只写 error/failed_at 与这一轮的材料回执，**绝不动上一次已经生成的正文**。
    """
    from duanxian import stock_brief as SB

    try:
        if body.ok:
            data = SB.save_brief(
                body.code, body.brief or '', word_limit=body.word_limit or SB.WORD_LIMIT,
                material_chars=body.material_chars, segments=body.segments, missing=body.missing,
                model=body.model, ai_provider=body.ai_provider,
                prompt_slot=body.prompt_slot, prompt_source=body.prompt_source,
                input_fetched_at=body.input_fetched_at)
            return {"data": data}
        data = SB.record_failure(
            body.code, body.error or '失败（原因未交）', material_chars=body.material_chars,
            segments=body.segments, missing=body.missing,
            model=body.model, ai_provider=body.ai_provider,
            prompt_slot=body.prompt_slot, prompt_source=body.prompt_source,
            input_fetched_at=body.input_fetched_at)
        return {"data": data}
    except ValueError as e:
        return {"data": {"saved": False, "reason": str(e)}}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"个股说明未能保存：{e}") from e


@app.get("/api/stock/fundamentals")
def stock_fundamentals(code: str = Query(..., min_length=6, max_length=16,
                                         description='带市场后缀的全码，如 600519.SH')):
    """个股财务多期（近 5 年逐季 + 年报）与三格加权外推（下一季 / 当年 / 明年）。

    判据只住 `app/compute/financial_forecast.py`，这一层只读 `reference_data.stock_financial_period`
    （采集器 `ingest/jobs/c25_financials_ths.py`，源＝同花顺，唯一一份）。三件界面上最容易糊的事：
    ① `cum`（年初到报告期累计）与 `single`（本季度单季）**不可混用**，同比也是各档各自的；
    ② 三格外推**不是分析师预期**：两法（半衰加权同比 / 近 3 年同季季节环比）分歧 >25% 就不给单一数，
       补「当年」时 Q4 单独一档（比别的季粗）；明年的 `scenario_low/high` 是**情景带**不是置信区间；
    ③ 缺数是 null 不是 0，且按 `fetched_age_hours` 说这份财务有多旧——源断供时这里停在最后一场。
    """
    from duanxian import stock_fundamentals as SF

    try:
        return {"data": SF.board(code)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"个股财务多期取数异常：{e}") from e


@app.get("/api/stock/volume-similarity")
def stock_volume_similarity(code: str = Query(..., min_length=6, max_length=16,
                                               description='带市场后缀的全码，如 600519.SH'),
                           k: int = Query(10, ge=1, le=20, description='最相近样本取几个（按票去重后的名额）')):
    """近 30 根有效日线的量价形状 ＋ 历史上最相近的样本（表头照「启动前逐日量能比对」那一屏）。

    判据与权重只住 `duanxian/volume_similarity.py` 一处（`WEIGHTS` 六维、缺维从权重里剔掉再归一）。
    三件界面上不许糊掉的事，都由后端交出、页面不自编：
    ① 样本库只有 `sv-2` 那批约一千个「票·启动日」窗口（近一年进入过启动母体的票），
       不是全市场历史所有窗口——所以"像它"不等于"会涨"，那是母体本身的选择偏差；
    ② 主板与双创（10% 与 20% 档位）不互比，任一侧认不出板块档就不比，榜上出现同票多窗口时只留最像的一个；
    ③ 逐日倍数与区间计数调的是 `app/compute/startup_volume` 那一份实现，与那张表同一个口径。
    """
    from duanxian import volume_similarity as VS

    try:
        return {"data": VS.similar(code, k=k)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"量能相似样本取数异常：{e}") from e


@app.get("/api/stock/price-volume-trend")
def stock_price_volume_trend(code: str = Query(..., min_length=6, max_length=16,
                                                description='带市场后缀的全码，如 600519.SH'),
                             days: int = Query(60, ge=6, le=250, description='日线画几根'),
                             weeks: int = Query(60, ge=6, le=160, description='周线画几根'),
                             months: int = Query(48, ge=6, le=64, description='月线画几根')):
    """量价走势三块（日 / 周 / 月）：同一只票、同一个最新一场，由日线聚合，不另找源也不建表。

    聚合、均线、区间涨跌都只住 `duanxian/price_volume_trend.py` 一处，页面只画。三件界面上最容易读错的事：
    ① 价格是**前复权形状**（`close × adjustment_factor`，最新一场为 1）—— 每根另给 `close_raw` 不复权收；
    ② 区间涨跌与**上一期复权收**比（不是本期开盘），第一期给 null 不给 0%；
    ③ 最后一根常常"本期未走完"（本周未收 / 本月未满），`partial` 与图上虚线必须一起标出来。
    本机日线最早 2021-08-02 ⇒ 月线最多约 63 根，次新股更少；不足 6 根的那一块不画线，只列读数。
    """
    from duanxian import price_volume_trend as P

    try:
        return {"data": P.board(code, days=days, weeks=weeks, months=months)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"量价走势取数异常：{e}") from e


@app.get("/api/stock/metrics-ledger")
def stock_metrics_ledger(code: str = Query('', max_length=16,
                                           description='只用来回一句「这一屏现在选的是哪一只」，可空')):
    """个股深度复盘的全指标总账：有哪几块、每块出自哪张表、每个字段说什么话。

    ⚠️ 这张表**不复制第二份措辞**：三块新屏（个股信息 / 财务多期 / 量能相似样本）的字段清单是现场
    `import` 各自模块的 `DEFINITIONS` 活对象（`duanxian/stock_metrics_ledger.py`），改那边这里跟着变；
    其余块（档案 / 趋势量化 / 个股研究 / 多空辩论）只登记出处与逻辑，字段义留在各接口自己的 payload 里。
    某一格"是有数、判不了、还是库里没这一列"由那一块自己上屏，总账不替读数下结论。
    """
    from duanxian import stock_metrics_ledger as L

    try:
        return {"data": L.ledger(code)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"全指标总账取数异常：{e}") from e


@app.get("/api/market/abnormal-move")
def market_abnormal_move(date: str = '', recent: int = Query(10, description='「近期严重异动」往回看几场：5 / 10 / 20'),
                         hot_limit: int = Query(20, ge=1, le=100, description='热门股偏离值榜取前几只'),
                         limit: int = Query(60, ge=1, le=200, description='明日涨停前瞻榜最多交几行')):
    """异动提醒：偏离值 / 异常波动 / 严重异常波动 + 「明日涨停会不会踩到」的前瞻。

    判据与落库只住 `app/compute/abnormal_move.py`（加工段步骤名 `abnormalmove`，第 15 步），
    这一层只读 `analytics.abnormal_move_daily`。三块榜（用户 2026-10-15 点名）：
    ① `forward` 明日涨幅至涨停能触发严重异动的票 —— 每行给**触发涨幅、触发价、涨停价、
       还差几个偏离点、明日涨停后的累计偏离**；
    ② `hot` 热门股偏离值前 N（按 10 日累计偏离降序；「几天几板」与首页连板榜同一份实现）；
    ③ `recent_severe` 近期已踩到严重异动的（异动日期、踩到哪几条、次日涨幅、次日停牌）。
    三句必须一起读的话（`caveats` 里 hard=true 的那三条）：本机没有"交易所公告日"⇒ 计数是纯滚动、
    前瞻按"明日指数 0%"假设、北交所不纳入。`severe=null` 是判不了、不是没踩到。
    """
    from duanxian import abnormal_view

    try:
        return {"data": abnormal_view.screen(date, recent, hot_limit, limit)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"异动提醒取数异常：{e}") from e


@app.get("/api/quant/trend-stocks")
def quant_trend_stocks(date: str = '', lookback: int = Query(5, description='周期：5 或 10 个交易日'),
                       limit: int = Query(300, ge=1, le=1000)):
    """趋势股：近 N 个交易日**每一个**交易日收盘都严格高于当日 5 日均线，且当天没封涨停。

    判据与落库只住 `app/compute/trend_stock.py`（加工段步骤名 `trendstock`），这一层只读
    `analytics.trend_stock_daily`。三件界面上最容易糊的事：
    ① 按主板／创业板／科创板**分组**交出（顺序固定，空组也交出 0，页面保留标题写「当日暂无」），
       组内按当日涨幅降序；**北交所不纳入**，不是漏了。
    ② 每行带两把新高尺子（收盘对**前 10／前 20 根的最高价**，不含当日）与放量两口径
       （对昨日单根、对前 5 根均量）—— 与「历史新高」「含当日的自然日新高」都不是一个尺子。
    ③ 这一屏与板块卡片里的「趋势强势股」不是同一份名单：卡片固定看 5 天且**可以**含当天涨停。
    整组汇总是服务端按同一批过滤条件在库里算的（`summary`），榜被 `limit` 截断时不代表全场。
    """
    from duanxian import trend_view

    try:
        return {"data": trend_view.screen(date, lookback, limit)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"趋势股榜取数异常：{e}") from e


@app.get("/api/quant/sector-members")
def quant_sector_members(sector: str = Query('', description="板块代码 TDX_881251 或板块名「其他医疗服务」"),
                         date: str = '', limit: int = Query(120, ge=1, le=400)):
    """板块成分股明细（位置/区间涨幅/市值/换手/当日是否封住）。

    给右侧「AI分析」的预设题当材料用 —— 没有它，"题材内部高低切"这类题只能得到
    "材料里没有个股"的回答。口径与坑见 `duanxian/quant.py:sector_members()`。
    """
    from duanxian import quant

    try:
        return {"data": quant.sector_members(sector=sector, date=date, limit=limit)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"板块成分明细异常：{e}") from e


@app.get("/api/prompts")
def prompts_list():
    """页面上那些「一键运行」的话术：内置槽位 + 用户自建模板，连「类型」表一起交出。

    `scopes` 是配置页「新建模板」的类型下拉与可插入字段清单的唯一来源；
    `source`/`edited` 必须随每条交出：改了但库里没落进去时，只有这一句能让人看出
    "我改的和跑的不是同一份"。
    """
    from duanxian import prompt_slots

    try:
        return {"data": {
            "slots": prompt_slots.list_slots(),
            "scopes": [{"id": k, "label": v["label"], "page": v["page"], "fields": v["fields"]}
                       for k, v in prompt_slots.SCOPES.items()],
        }}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"AI 提示词读取异常：{e}") from e


class TemplateIn(BaseModel):
    title: str
    scope: str
    template: str
    id: str = ""


@app.post("/api/prompts/custom")
def prompts_save_custom(body: TemplateIn):
    """新建或改一份自建模板。校验不过就 400，错误里点名是哪个字段。"""
    from duanxian import prompt_slots

    try:
        return {"data": prompt_slots.save_custom(body.title, body.scope, body.template, body.id)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"模板未能保存：{e}") from e


@app.delete("/api/prompts/custom/{tid}")
def prompts_delete_custom(tid: str):
    from duanxian import prompt_slots

    try:
        prompt_slots.delete_custom(tid)
        return {"data": {"deleted": tid.strip()}}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"模板未能删除：{e}") from e


class PromptIn(BaseModel):
    template: str


@app.post("/api/prompts/{slot}")
def prompts_save(slot: str, body: PromptIn):
    """存一份自定义话术。空话术与未知占位符都会被 400 点名拒掉。"""
    from duanxian import prompt_slots

    try:
        return {"data": prompt_slots.save(slot, body.template)}
    except KeyError:
        raise HTTPException(404, f"没有这个提示词槽位：{slot}") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"话术未能保存：{e}") from e


@app.delete("/api/prompts/{slot}")
def prompts_reset(slot: str):
    """恢复默认 = 删掉 `config.setting` 里那个键，回到代码里的内置默认。"""
    from duanxian import prompt_slots

    try:
        return {"data": prompt_slots.reset(slot)}
    except KeyError:
        raise HTTPException(404, f"没有这个提示词槽位：{slot}") from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"恢复默认失败：{e}") from e


@app.get("/api/quant/sector-heat")
def quant_sector_heat(date: str = "", type: str = Query("", description="行业/概念/风格"),
                      stage: str = Query("", description="ignite/follow/fade/coil/none"),
                      sort: str = Query("heat", pattern="^(heat|pct)$",
                                        description="榜单主序：heat=热度合成分；pct=板块自己那条 880 指数当日涨幅"),
                      limit: int = Query(200, ge=1, le=1000),
                      stats: str = Query("day", pattern="^(day|full)$"),
                      refresh: bool = False):
    """板块热度（FT `/sector` 的对应物）：`sector_heat_daily` + `ignition` + `sector_trend`。

    首屏按 `stats=day` 走，只查当日（榜单本身 0.35 秒）。三项全库聚合（旧口径还剩多少行、
    广度覆盖、各表实有多少场）要 `stats=full` 才现算 —— 它们占掉 5 秒而结论不变，
    算过一次进一小时缓存，之后默认请求会顺带带出，`freshness` 里写着它是几点算的。

    `heat` 与 `score` **是两套判据不是两列**（四分项热度 vs 五要素点火）；五要素
    与权重分开交出，页面要能回答"凭什么说它点火"。

    ⚠️ 两条会被读成"数据缺失"的口径已随数据交出：`member_scored_count` 在现行
    `source=sector` 口径恒为 NULL 是**设计**（旧口径才用），可信度看 `part_count`；
    行业板块因没有涨跌家数**规则上永远不会判为点火**（实测 296 个行业、229,221 行、
    `ignite` 0 次），榜上看不到它们不是没轮动。库里列注释还写着旧的"230 个"，
    要当下的数以 `stats=full` 的 `breadth_reality` 为准。
    """
    from duanxian import quant

    try:
        return {"data": quant.sector_heat(date, sector_type=type, stage=stage,
                                          limit=limit, stats=stats, refresh=refresh,
                                          sort=sort)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"板块热度取数异常：{e}") from e



@app.get("/api/intraday/timeline")
def intraday_timeline(date: str = "", limit: int = Query(400, ge=50, le=2000)):
    """盘中涨跌停事件流（每一针谁新封/回封/炸板/跌停开板 + 概念前三的家数来源）。

    判据住在 `app/intraday/pool_events.py`（写侧）、表在 `analytics.intraday_pool_event`
    （DDL 54，`spec_version` 进条件），这里只搬运不重算。
    ⚠️ 这张表**从 2026-10-26 才开线**：更早的日子 `available=false` 并说明"库里还没有事件行"，
    不是"那天没有涨跌停"。盘中首封取自东财池 `fbt`，**跌停池结构上没有这一列** ⇒ 那一格 null 是常态；
    盘后回看的首封另有本地分钟线那份（`analytics.limit_up_pool_daily.first_seal_at`）。
    """
    from duanxian import intraday_view
    return {"data": intraday_view.timeline(date or None, limit=limit)}


@app.get("/api/quant/allocator")
def quant_allocator(date: str = "", limit: int = Query(300, ge=20, le=1000)):
    """分配者视角四块读数：收费站榜 / 拥挤度温度 / 再分配阶段 / 位置×想象力象限，
    外加「故事热度 vs 回报」那条规则的本地复算。

    判据与权重只住在 `app/compute/allocator.py`（写侧）与 `duanxian/allocator_view.py`（读侧），
    接口自己不重算任何东西 —— 换判据版本就是换 `spec_version`，旧行不覆盖。

    ⚠️ 两条会被读成"数据缺失"的现实：① 这张表**从落库那天起才有历史**，
    所以历史曲线要等加工链每天攒（现在只有一场就是只有一场，不画假线）；
    ② 本地只有**价格指数**（上证红利），没有视频引用的「中证红利低波全收益」，
    所以那条规则的年化必然低于视频给的数，页面上那条说明不是免责声明，是口径本身。
    """
    from duanxian import allocator_view

    try:
        return {"data": allocator_view.view(date, limit=limit)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"分配者视角取数异常：{e}") from e


@app.get("/api/quant/sector-trend")
def quant_sector_trend(date: str = "", regime: str = Query("", description="turn_up_seed/forming/established/overheated/downtrend/no_shape/insufficient"),
                       type: str = Query("", description="行业/概念/风格"),
                       scope: str = Query("sector", pattern="^(sector|group|group_rebuilt)$"),
                       spec: str = Query("", description="判据版本，如 sts-2；空=用 config.setting 里的生效版本"),
                       sort: str = Query("dip", pattern="^(dip|seed)$", description="dip=档位表(低吸分降序) / seed=低吸榜(position 升序，越低位越前)"),
                       limit: int = Query(200, ge=1, le=1000)):
    """板块趋势形成度与左侧低吸候选：读 `analytics.sector_trend_signal`，接口自己不判。

    判据只允许存在一处（`app/compute/sector_trend_signal.py` + 文档 05）。这里交出的
    六分项、档位、`dip_score`、`reasons` 都是当时落库的那一份 —— 换判据版本就是换
    `spec_version`，旧行不覆盖，所以历史上"哪天算刚转"不会被今天的参数改写。

    `scope=group` 只在自选组成员**每日快照**攒够窗口后才有实盘序列；`group_rebuilt`
    是以今日名单回算的未来函数读数，只给形状、不可用于回测与胜率统计。
    """
    from duanxian import quant

    try:
        return {"data": quant.sector_trend(date, regime=regime, sector_type=type,
                                           limit=limit, scope=scope, spec=spec, sort=sort)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"板块趋势信号取数异常：{e}") from e

@app.get("/api/quant/risk-signal")
def quant_risk_signal(months: str = Query('3', description='窗口：3 / 6 / 12 / 24 个月（非法值退回 3 并在 caveats 点名）'),
                      group: str = Query('day', description='粒度：day / week / month（非法值退回 day 并在 caveats 点名）'),
                      date: str = Query('', description='锚定场次 YYYY-MM-DD（含当天）；空＝库里最晚一场')):
    """首页「风险提示」：市场宽度（全市场涨跌家数）+ 阿姆氏指标（上涨额 ÷ 下跌额）+ 两条线的区间背离。

    判据与落库只住 `app/compute/breadth.py`（每日 `ingest/jobs/d6_breadth.py`，
    历史回算 `python -m app.compute.breadth --backfill`），周期摊法与分位/背离只住
    `duanxian/risk_signal.py` 一处 —— 这一层不重算、界面不重算（阈值也从返回体的 `thresholds` 读）。
    四条最容易读错、写错还不出声的事：
    ① 阿姆斯的分子分母与 `up_count` / `down_count` **同一批票、同一个判据**；算不出是 NULL（界面 `—`、
       折线断开），不是 0；
    ② 按周/按月是**期内两个合计相除**（上涨额合计 ÷ 下跌额合计），**不是把日比值取平均**；
       家数取期内日均并交 `bars_in_period`；
    ③ 背离＝宽度分位 − 阿姆斯分位（各自近 20 期），≥40 高危 / 20~40 中度 / ≤-40 反向；
       **分位是相对量、不是概率**，不足 20 期一律 NULL；
    ④ 不可用分三种（`available:false` 不带 `series`）：库不通 / 那一段没有 breadth 行 /
       这一族还没回算（`reason` 里给 `--backfill` 那条命令）。
    """
    from duanxian import risk_signal as RS

    try:
        return {"data": RS.panel(months=months, group=group, date=date)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"风险提示取数异常：{e}") from e


@app.get("/api/quant/first-board-daily")
def quant_first_board_daily(date: str = Query('', description='场次 YYYY-MM-DD；空＝库里最新一场'),
                           limit: int = Query(80, ge=10, le=240, description='一次交几行（默认 80；一场首板实测 30~60 只）'),
                           shapes: str = Query('', description='形态档多选，逗号分隔（在库里筛，先于 limit）；非法档退回不筛并出声')):
    """首页「首板分析」：当日首板个股 + 涨停原因 + **前一天的走势** + 近期走势。

    判据与回填只住 `app/compute/first_board_daily.py`（母体＝`analytics.first_board_shape` 的 fb-2
    那一把尺：连板段第一天），这一层不重算；题材串拆词与首板页共用 `duanxian/reason_tags.py` 一份。
    四件最容易读错的事：
    ① 首板母体与首板页那份**东财实时池**不是一张名单（差异来自封板容差与实时/收盘时点），
       所以 `universe`（当日收盘封住家数）与 `first_count`（本场首板只数）随行交出；
    ② 涨停原因＝源站编辑归因、不是公司确认事实，且源站池不收北交所、几乎不收 ST
       —— 串空着通常是「源站没收这只」（`reason_missing=source_row_absent`），与「本场整池没取到」
       （`source_no_records`，这才是本机漏采）两件事，界面必须分开念；
    ③ `first_seal_at` / `last_seal_at` / `break_count` 是**首板当日**的，不是前一日的；
       前一日那一根只有日线读数（本地分钟线只到 2026-10-19）。`break_count` 空是源站没给这个字段，
       不是「0 次没炸」；
    ④ 全场聚合（`shape_tally`/`reason_tally`/`universe`/`first_count`）永远按这一场全部首板算，
       不随 `shapes` 与 `limit` 变 —— 每档后面那个数是全场数，不是筛完剩下的数。
    """
    from duanxian import first_board_daily as FBD_VIEW

    try:
        return {"data": FBD_VIEW.panel(date=date, limit=limit, shapes=shapes)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"首板分析取数异常：{e}") from e


# --- 自选股与分组（读 `config.watchlist_group` / `watchlist_stock`）---------
#
# 库里 45 组 / 1283 条早就在（同花顺本地同步进来的），YuanShan 的自选只读浏览器
# localStorage，所以页面是空的 —— 不是没数据，是没接。用户 2026-09-25 点名要搬。

@app.get("/api/watchlist/groups")
def watchlist_groups(date: str = ""):
    """自选分组一览：每组票数、当日平均涨跌、停牌缺行情只数。"""
    from duanxian import watchlist as _wl

    try:
        return {"data": _wl.groups(date)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"自选分组取数异常：{e}") from e


@app.get("/api/watchlist/stocks")
def watchlist_stocks(group_id: int = Query(default=None), date: str = "",
                     limit: int = Query(default=2000, ge=1, le=5000),
                     fields: str = Query(default="", description="codes = 只要代码列")):
    """自选明细（分组+当日行情+日均振幅+操作分象限+备注），不传 group_id 就是全部组。

    默认上限 2000 是**按全库 1283 条定的**：默认 500 会在不传 group_id 时静默截掉
    600 多条，而页面看不出被截 —— 那种缺法比直接报错危险。
    默认按 **10 日日均振幅**倒序（FinanceTool 同页同键）。注意 `chg10_avg` 这类是
    **日均**、与 `chg10`（区间涨幅）同名不同义 —— 口径随 `indicator_meaning` 交出。

    `fields=codes` 走另一条只查代码列的轻 SQL（首页「只看自选」要的就是一份名单，
    实测整明细一发是 457 KB / 1.4 秒）。两份读同一张 `config.watchlist_stock`，
    分开的只是列，不是口径。
    """
    from duanxian import watchlist as _wl

    try:
        if fields.strip().lower() == 'codes':
            return {"data": _wl.watchlist_codes(group_id=group_id)}
        return {"data": _wl.watchlist(group_id=group_id, date=date, limit=limit)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"自选明细取数异常：{e}") from e


@app.get("/api/watchlist/group-analytics")
def watchlist_group_analytics(date: str = ""):
    """组级分析：加权/等权/趋势/预测操作分 + 近 10/20 日日均涨跌(bp) + 组内振幅 + 上榜次数。

    库里**没有组级结果表**，这一屏是把组内个股的 `analytics.score`/`indicator_value`/
    `stock_daily` 按参考实现的同口径现场聚合出来的（口径随 `calibers` 交出）。
    一次 ~2 秒，无缓存 —— 交出去的永远是这一场的数，`elapsed_ms` 一并给出。
    """
    from duanxian import watchlist as _wl

    try:
        return {"data": _wl.group_analytics(date)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"组级分析取数异常：{e}") from e


# --- 数据质量闸与筛选器（FT 的 /data 质量标签、/screen 对应物）--------------

@app.get("/api/quality")
def quality_overview(date: str = ""):
    """质量闸的规则与该场判定（条数由 `config.quality_rule` 现查，不在这里抄），并显式交出"本场只审了几条"。

    `date` 省略时取**有审计记录的最近一场**，不是今天 —— 今天可能根本没跑审计，
    拿今天当默认会让页面显示零条规则、看着像系统坏了。
    """
    from duanxian import quality as _q

    try:
        return {"data": _q.quality(date)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"质量闸取数异常：{e}") from e


@app.get("/api/screen/rules")
def screen_rules():
    """已登记的筛选条件（含**机读参数表**，页面按它生成输入控件）。

    旧写法走 `quality.screens()`，那条 SQL 经 pandas 把 jsonb 的 `param_schema` 交成了
    repr 字符串 —— 人能看一眼，程序没法用，所以这一屏长不出"跑一次"的按钮。
    库里只有几种就写几种，不摆成"方案很多"。
    """
    from duanxian import screen_view

    try:
        return {"data": screen_view.rules_view()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"筛选条件取数异常：{e}") from e


@app.get("/api/screen")
def screen_run(request: Request, rule: str = Query("", max_length=64),
               conds: str = Query("", max_length=4000), logic: str = "and",
               universe: str = "watchlist", date: str = "", limit: int = 200):
    """按库里登记的条件**实跑一次**（用户 2026-10-06 问的"没有应用筛选的页面呀"就是这段断链）。

    两种写法都走同一条执行路径（`app/compute/screener.py` 那一份，这里不拼 SQL、
    也不放宽它的判据）：
      · `rule=` 一条条件，条件自己的参数（`days` / `pct` / `field` / `op` / `value` / `on_date`
        以及均线那几条的 `ma` / `max_pct` / `min_layers` …）直接从 query string 取；
      · `conds=` 一条 JSON 数组：`[{"rule_key":"ma_position","params":{"ma":"ma250","op":"<"}},
        {"rule_key":"dense_short5","params":{"max_pct":"2"}}]`，条件之间由 `logic` 定 and / or。
        用户 2026-10-14 要的「250 线以下 + 其他均线密集」就是这一条 ——
        引擎一直是收数组的（`screener.build(conds, logic=…)`，参数名前缀 `c{i}_`
        就是为了同一条条件用不同参数出现两次），缺的只是这一层入口。
    """
    from duanxian import screen_view

    if conds.strip():
        import json

        try:
            spec = json.loads(conds)
        except (ValueError, TypeError) as exc:
            return {"data": {"available": False,
                             "reason": f"conds 不是一段能读的 JSON 数组：{exc}", "rows": []}}
        if not isinstance(spec, list):
            return {"data": {"available": False,
                             "reason": "conds 必须是数组（每一项是一条条件）", "rows": []}}
        return {"data": screen_view.run_many(spec, logic=logic.strip().lower(),
                                             universe=universe.strip(),
                                             date=date.strip(), limit=limit)}

    given = {k: v for k, v in request.query_params.items()
             if k not in ("rule", "conds", "logic", "universe", "date", "limit")}
    if not rule.strip():
        return {"data": {"available": False, "reason": "没给 rule，也没给 conds —— 不知道要筛什么",
                         "rows": []}}
    try:
        return {"data": screen_view.run(rule.strip(), given, universe=universe.strip(),
                                        date=date.strip(), limit=limit)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"筛选实跑异常：{e}") from e


@app.get("/api/global/indices")
def global_indices():
    """全球指数快照（道指 / 标普500 / 纳斯达克 / 恒生 / 恒生科技）—— A 股看隔夜外围脸色。

    成功缓存 5 分钟，取不到也按 60 秒缓存（旧写法"空结果不缓存"让每一发都重付一遍
    对面超时，实测 12.4 秒）。`status` 是**给界面的那句话**：这一路现在是取到了、
    返回空、还是刚炸过 —— 空数组单独交出去会被读成"今天没有外围行情"，那是假读数。
    """
    try:
        return {"data": market.get_global_indices(), "status": market.cache_status('global_indices')}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"全球指数异常：{e}") from e


@app.get("/api/global/stock")
def global_stock(symbol: str = Query(..., min_length=1, max_length=16)):
    """美股 / 港股个股聚合：行情 + 关键财务指标（东财域内源）。symbol 如 AAPL / BABA / 00700。"""
    try:
        data = gstock.us_hk_stock(symbol.strip())
        if not data:
            raise HTTPException(404, f"未找到美股/港股代码「{symbol}」")
        return {"data": data}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"美港股查询异常：{e}") from e


@app.get("/api/indices")
def indices():
    """A股大盘指数实时行情（上证/深证成指/创业板指/沪深300）。仅标准库。"""
    try:
        return {"data": astock.index_quote()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"指数行情异常：{e}") from e


@app.get("/api/quote")
def quote(codes: str = Query(..., description="逗号分隔的 6 位代码")):
    """实时行情：现价/涨跌/PE/PB/市值/换手/涨跌停。仅标准库，永远可用。"""
    lst = [c.strip() for c in codes.split(",") if c.strip()]
    if not lst or any(not c.isdigit() or len(c) != 6 for c in lst):
        raise HTTPException(400, "codes 必须是逗号分隔的 6 位数字")
    try:
        return {"data": astock.tencent_quote(lst)}
    except Exception as e:  # noqa: BLE001 — 边界统一兜底
        raise HTTPException(502, f"行情源异常：{e}") from e


import time as _time
_PCT_CACHE: dict = {}


@app.get("/api/valuation/percentile")
def valuation_percentile(code: str = Query(...)):
    """PE-TTM / PB 历史分位（近5年）。全站缓存 30 分钟/代码（历史序列日频、变化慢）。"""
    code = _validate(code)
    hit = _PCT_CACHE.get(code)
    if hit and _time.time() - hit[0] < 1800:
        return {"data": hit[1]}
    try:
        data = astock.valuation_percentile(code)
        _PCT_CACHE[code] = (_time.time(), data)
        return {"data": data}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"估值分位异常：{e}") from e


_ANN_CACHE: dict = {}


@app.get("/api/announcements")
def announcements(code: str = Query(...)):
    """个股近期公告（东财，仅 requests）。缓存 15 分钟/代码。"""
    code = _validate(code)
    hit = _ANN_CACHE.get(code)
    if hit and _time.time() - hit[0] < 900:
        return {"data": hit[1]}
    try:
        data = astock.announcements(code)
        _ANN_CACHE[code] = (_time.time(), data)
        return {"data": data}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"公告源异常：{e}") from e


_FIN_CACHE: dict = {}


@app.get("/api/financials")
def financials(code: str = Query(...)):
    """财务关键指标（同花顺财务摘要，最新报告期）。缓存 30 分钟/代码。"""
    code = _validate(code)
    hit = _FIN_CACHE.get(code)
    if hit and _time.time() - hit[0] < 1800:
        return {"data": hit[1]}
    try:
        data = astock.financials(code)
        _FIN_CACHE[code] = (_time.time(), data)
        return {"data": data}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"财务摘要异常：{e}") from e


@app.get("/api/valuation")
def valuation(code: str = Query(...)):
    """完整估值：行情 + 一致预期 + 前向PE/PEG/消化年数。"""
    code = _validate(code)
    try:
        return {"data": astock.full_valuation(code)}
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"估值计算异常：{e}") from e


@app.get("/api/reports")
def reports(code: str = Query(...), pages: int = Query(2, ge=1, le=5)):
    """个股研报列表（东财，含 PDF 链接）。仅需 requests。"""
    code = _validate(code)
    try:
        rows = astock.eastmoney_reports(code, max_pages=pages)
        for r in rows:
            r["pdfUrl"] = astock.pdf_url(r.get("infoCode", "")) if r.get("infoCode") else None
        return {"data": rows}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"研报源异常：{e}") from e


@app.get("/api/news")
def news(code: str = Query(...), limit: int = Query(20, ge=1, le=50)):
    """个股新闻（东财，需 akshare）。"""
    code = _validate(code)
    try:
        return {"data": astock.stock_news(code, limit=limit)}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"新闻源异常：{e}") from e


@app.get("/api/info")
def info(code: str = Query(...)):
    """个股基本面：行业/股本/上市时间（需 akshare）。"""
    code = _validate(code)
    try:
        return {"data": astock.individual_info(code)}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"基本面源异常：{e}") from e


@app.get("/api/disclosure")
def disclosure(code: str = Query(...)):
    """巨潮公告列表（需 akshare）。"""
    code = _validate(code)
    try:
        return {"data": astock.disclosure(code)}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"公告源异常：{e}") from e


@app.get("/api/kline")
def kline(code: str = Query(...), category: int = Query(4), offset: int = Query(60, ge=1, le=800)):
    """K线（需 mootdx）。category 4=日 5=周 6=月 11=60分钟。"""
    code = _validate(code)
    try:
        return {"data": astock.kline(code, category=category, offset=offset)}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"K线源异常：{e}") from e


@app.get("/api/finance")
def finance(code: str = Query(...)):
    """季报财务快照（需 mootdx）。"""
    code = _validate(code)
    try:
        return {"data": astock.finance(code)}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"财务源异常：{e}") from e


# ---------------------------------------------------------------------------
# 资金面 / 筹码 / 信号（东财数据中心，v3.3 并入）—— 均为「用户查的那只股」的公开数据。
# 东财有 1s 限流，这些多为日/季级静态数据，统一走 30 分钟缓存，进一步降低被封风险。
# ---------------------------------------------------------------------------

_DC_CACHE: dict = {}  # key=(endpoint, code) -> (ts, data)


def _cached(endpoint: str, code: str, ttl: int, fetch):
    key = (endpoint, code)
    hit = _DC_CACHE.get(key)
    if hit and _time.time() - hit[0] < ttl:
        return hit[1]
    data = fetch()
    _DC_CACHE[key] = (_time.time(), data)
    return data


@app.get("/api/margin")
def margin(code: str = Query(...)):
    """融资融券明细（东财，日级）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("margin", code, 1800, lambda: astock.margin_trading(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"融资融券异常：{e}") from e


@app.get("/api/block-trade")
def block_trade(code: str = Query(...)):
    """大宗交易（东财）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("block", code, 1800, lambda: astock.block_trade(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"大宗交易异常：{e}") from e


@app.get("/api/holders")
def holders(code: str = Query(...)):
    """股东户数变化（东财，季度级）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("holders", code, 1800, lambda: astock.holder_num_change(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"股东户数异常：{e}") from e


@app.get("/api/dividend")
def dividend(code: str = Query(...)):
    """分红送转历史（东财）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("dividend", code, 1800, lambda: astock.dividend_history(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"分红送转异常：{e}") from e


@app.get("/api/fund-flow")
def fund_flow(code: str = Query(...)):
    """个股资金流（东财 push2his，120 日主力净流入）。缓存 15 分钟。
    注：push2his 对部分大陆住宅 IP 有间歇风控，可能返回空（非代码问题）。"""
    code = _validate(code)
    try:
        return {"data": _cached("fundflow", code, 900, lambda: astock.stock_fund_flow_120d(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"资金流异常：{e}") from e


@app.get("/api/dragon-tiger")
def dragon_tiger(code: str = Query(...)):
    """龙虎榜：该股近期上榜记录 + 买卖席位 + 机构净买（东财）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("dt", code, 1800, lambda: astock.dragon_tiger_board(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"龙虎榜异常：{e}") from e


@app.get("/api/lockup-calendar")
def lockup_calendar(window: str = Query("upcoming", pattern="^(upcoming|recent)$")):
    """Ten-day public unlock calendar; no watchlist is received."""
    try:
        from duanxian.util import china_now
        day = china_now().strftime("%Y-%m-%d")
        return {"data": _cached("lockup-calendar", day + window, 1800,
                               lambda: astock.lockup_calendar(window, day))}
    except Exception as exc:
        raise HTTPException(502, "解禁日历取数失败，无法判断有无事件，请稍后重试") from exc


@app.get("/api/lockup")
def lockup(code: str = Query(...)):
    """限售解禁日历：历史解禁 + 未来 90 天待解禁（东财）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        from duanxian.util import china_now
        day = china_now().strftime("%Y-%m-%d")
        return {"data": _cached("lockup", code + day, 1800, lambda: astock.lockup_expiry(code, day))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"解禁日历异常：{e}") from e


@app.get("/api/blocks")
def blocks(code: str = Query(...)):
    """个股所属板块/概念归属（东财 slist）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("blocks", code, 1800, lambda: astock.concept_blocks(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"板块归属异常：{e}") from e


@app.get("/api/hot-concepts")
def hot_concepts(code: str = Query(...)):
    """个股当下被市场归到哪些概念在炒（东财热门概念命中）。缓存 15 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("hotcon", code, 900, lambda: astock.hot_concepts(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"热门概念异常：{e}") from e


@app.get("/api/investor-qa")
def investor_qa(code: str = Query(...)):
    """互动易问答（巨潮）：投资者提问 + 公司回复。缓存 15 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("irm", code, 900, lambda: astock.investor_qa(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"互动易异常：{e}") from e


@app.get("/api/industry")
def industry(top: int = Query(20, ge=5, le=50)):
    """全行业涨跌幅排名（东财行业板块，板块级、零个股名单）。缓存 5 分钟。"""
    key = ("industry", str(top))
    hit = _DC_CACHE.get(key)
    if hit and _time.time() - hit[0] < 300:
        return {"data": hit[1]}
    try:
        data = astock.industry_comparison(top_n=top)
        _DC_CACHE[key] = (_time.time(), data)
        return {"data": data}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"行业排名异常：{e}") from e


# ==================== 作业与运维（契约：docs/页面展示工作指南/03-任务调度页契约.md）====================

@app.get("/api/jobs")
def jobs_snapshot():
    """任务调度页的数据底。**读不到就说读不到**，不渲染成"当前没有作业"。

    顶层 `scheduler` 只描述本进程宿主；FinanceTool 登记的作业靠各自 `available=false`
    + `reason` 表态（判定顺序在 `jobsView.ts` 里只有一份实现，这里不重复算标签）。
    """
    import jobsboard

    try:
        return {"data": jobsboard.build()}
    except Exception as exc:  # noqa: BLE001
        return {"data": {"generated_at": None, "scheduler": None,
                         "scheduler_available": False,
                         "scheduler_reason": f"{type(exc).__name__}: {exc}",
                         "jobs": [], "jobs_available": False,
                         "jobs_reason": f"作业清单读不出来：{type(exc).__name__}: {exc}",
                         "truncated": False}}


@app.get("/api/jobs/chain")
def jobs_chain(request: Request):
    """收盘链的开关面板读数：两段各自开关、时点与来源、最近结果、**谁什么时候改的**。

    原先这些键只有命令行能改，界面上留下的痕迹是一个小灰标签「已停用」——
    那看着像"用户自己不想跑"，与被谁误关完全分不开。09-28 同一个作业被关三次就是靠
    文件 mtime 才勉强还原出时间线，所以这里把留痕做成一等公民。
    """
    import jobsboard

    try:
        data = jobsboard.chain_status()
    except Exception as exc:  # noqa: BLE001
        return {"data": {"generated_at": None, "stages": [], "available": False,
                         "reason": f"开关状态读不出来：{type(exc).__name__}: {exc}"}}
    data["can_edit"] = True
    return {"data": data}


@app.get("/api/tdx/status")
def api_tdx_status():
    """通达信控制页的读数：进程/登录/同步子进程/vipdoc 到哪一天/TQ 能不能走。
    读不到就说读不到，**不拿"看起来像"的值顶上**（主站连接、后台同步开关这两格恒为空）。
    """
    import jobsboard

    try:
        return {"data": jobsboard.tdx_status()}
    except Exception as exc:  # noqa: BLE001
        return {"data": {"available": False,
                         "reason": "这一屏取不到数：%s: %s" % (type(exc).__name__, str(exc)[:160])}}


@app.get("/api/tdx/watch")
def api_tdx_watch():
    """通达信看护那一屏：客户端在不在、登录框在不在、同步子进程在不在、**待人工清单**。

    这一格原先在页面上挂着"接口 404"：看护循环 10-06 已经真的在跑，而看不见 = 用户
    只能自己开任务管理器数进程。现问现答（只枚举窗口，绝不动界面）。
    """
    import jobsboard

    try:
        return {"data": jobsboard.tdx_watch()}
    except Exception as exc:  # noqa: BLE001
        return {"data": {"available": False, "loop_alive": None,
                         "reason": "看护读数取不出来：%s: %s" % (type(exc).__name__, str(exc)[:160])}}


class ChainPatch(BaseModel):
    key: str
    enabled: bool | None = None
    run_at: str | None = None
    note: str | None = None
    # 收盘前拉起通达信的提前量与"只在交易日执行"（用户 2026-10-07 要的两格）
    launch_lead_min: int | None = None
    trading_days_only: bool | None = None
    # 链收工后自动关通达信客户端（用户 2026-10-08 要加的那一档，默认关）
    close_client_after: bool | None = None
    # 自选同步按全量还是增量跑（用户 2026-10-07 要的第三格：`watchlist.sync_mode`）
    sync_mode: str | None = None


# 页面/CLI 能改的键。**注册表与取值范围只在 `duanxian/jobs.py` 一处**：这里加一个名字
# 而那里没登记，就会造出"页面上有个框、改了没人读"的假生效。
# 后四个是"某个作业的在册键"（`run_freq`/`weekly_days` 是用户 2026-10-08 要的
# "可以选每周/每日 + 时间可改"；`wait_max_sec`/`close_after` 是同花顺那条的参数）。
# 写不存在的键会被 `jobs.set_setting` 挡回来，这里只负责让请求能走到那一层。
_JOB_KEYS = ("enabled", "run_at", "note", "launch_lead_min", "trading_days_only", "sync_mode",
           "run_freq", "weekly_days", "wait_max_sec", "close_after", "times", "close_client_after",
           # 通达信客户端两档（用户 10-13 点名）。取值范围住在 `duanxian/jobs.py::_valid`，
           # 这里只让请求能走到那一层 —— 在册表没这个键的作业会被 `set_setting` 挡回来。
           # `exit_confirm_click`：请退之后那只「退出 / 重新登录 / 取消」由程序答不答。
           "client_mode", "restart_max_per_day", "exit_confirm_click",
           # 时间带两格 + 每日定时重启（用户 2026-10-19 点名要在系统配置里能改）。
           # 校验与默认值都住在 `duanxian/jobs.py`（`_hhmm` / 成对方向那条跨键判据 / `TDX_WATCH_DEFAULTS`）。
           "client_band_lo", "client_band_hi", "restart_daily_enabled", "restart_daily_at")


def _apply_job_patch(body: dict, key: str, who: str) -> tuple[int, dict]:
    """写入面共用的派发：**一次只走一件**。
    校验与留痕都住在 `jobsboard.set_*` / `jobs.set_setting` 里，这里不重复一份规则。"""
    import jobsboard
    if key == "enabled":
        return jobsboard.set_switch(body["key"], bool(body["enabled"]), via=who)
    if key == "note":
        return jobsboard.set_note(body["key"], str(body["note"]), via=who)
    if key == "run_at":
        return jobsboard.set_slot(body["key"], str(body["run_at"]), via=who)
    # 剩下的都是"某个作业的某个在册键"：注册表、取值范围与"改了没人读"的拦截
    # 全在 `jobs.set_setting` 一处（它拿 `CLOSE_DEFAULTS` 当在册清单）。
    return jobsboard.set_knob(body["key"], key, body[key], via=who)


def _job_patch_guard(body: dict) -> list[str]:
    touched = [k for k in body if k in _JOB_KEYS]
    if not touched:
        raise HTTPException(400, f"没有要改的项（可用：{' / '.join(_JOB_KEYS)}）")
    if len(touched) > 1:
        raise HTTPException(400, f"一次只改一件事，收到 {'、'.join(touched)}")
    return touched


class CompletenessPatch(BaseModel):
    deviation: int | None = None
    deviation_minute: int | None = None
    note: str | None = None


@app.get("/api/jobs/completeness")
def jobs_completeness():
    """判"这一场的数全不全"的那条规则现在长什么样（基线 + 偏离值 + 各口径最新一场）。"""
    import jobsboard

    try:
        return {"data": jobsboard.completeness_status()}
    except Exception as exc:  # noqa: BLE001
        return {"data": {"available": False, "kinds": [],
                         "reason": "完整性读数取不出来：%s: %s" % (type(exc).__name__, str(exc)[:160])}}


@app.post("/api/jobs/completeness")
def jobs_completeness_update(payload: CompletenessPatch, request: Request):
    """改偏离值（条）。0 是合法值 = "少一条都算不全"；下一场就按新值判，不用重启。"""
    import jobsboard

    who = (f"api:/api/jobs/completeness@{request.client.host if request.client else '未知来源'}")
    status, resp = jobsboard.set_completeness(
        who, deviation=payload.deviation, deviation_minute=payload.deviation_minute,
        note=payload.note)
    if status != 200:
        raise HTTPException(status, resp.get("reason") or "未采纳")
    return {"data": jobsboard.completeness_status(), "reason": resp["reason"]}


class QuoteLinkPatch(BaseModel):
    route: str
    template: str


@app.get("/api/config/quote-links")
def config_quote_links_get():
    """按市场的行情外链地址表（每行一个市场，值里 `******` 是代码占位）。"""
    import jobsboard
    return {"data": jobsboard.quote_links_status()}


@app.post("/api/config/quote-links")
def config_quote_links_set(payload: QuoteLinkPatch, request: Request):
    """改一行。**非法地址整笔拒并说明原因**（拼不出地址的串存进去＝全站股票名一起变死链），
    合法则当场作废那 15 秒的模板缓存 —— 页面上下一屏链接就是新的，不用重启。"""
    import jobsboard

    who = f"api:/api/config/quote-links@{request.client.host if request.client else '未知来源'}"
    status, resp = jobsboard.set_quote_link(payload.route, payload.template, via=who)
    if status != 200:
        raise HTTPException(status, resp.get("reason") or "未采纳")
    return {"data": resp["data"], "reason": resp["reason"]}


@app.post("/api/jobs/chain")
def jobs_chain_update(payload: ChainPatch, request: Request):
    """改开关 / 改时点 / 写停用理由。**一次一件**，且把改动记进留痕（含来源 IP）。

    写操作不限制成"只有回环"（与 `/api/net` 不同）：关一个作业不扩任何权限，
    被放行的机器本来就能触发它跑；而"到点没跑"已经有留痕可查。
    """
    import jobsboard

    body = payload.model_dump(exclude_none=True)
    touched = _job_patch_guard(body)
    who = (f"api:/api/jobs/chain@{request.client.host if request.client else '未知来源'}")
    status, resp = _apply_job_patch(body, touched[0], who)
    if status != 200:
        raise HTTPException(status, resp.get("reason") or "未采纳")
    return {"data": jobsboard.chain_status(), "reason": resp["reason"]}


class LoopPatch(BaseModel):
    key: str
    enabled: bool | None = None
    run_at: str | None = None
    note: str | None = None
    # `watchlist.sync_mode`：全量镜像 / 增量添加。与 `/api/jobs/chain` 同一条写入面，
    # 两处都能改同一份 `jobs/config.json`，所以键名与校验必须一致（`_JOB_KEYS` 一份）。
    sync_mode: str | None = None
    # `ths_sync` 那一条的参数（频率档 / 每周哪几天 / 最多等多久 / 等完要不要收窗口）
    run_freq: str | None = None
    weekly_days: str | None = None
    wait_max_sec: int | None = None
    close_after: bool | None = None
    # `step_retry.times`：重试阶梯的时点表（逗号分隔的 HH:MM，界面上逐条增删改）。
    times: str | None = None
    # `tdx_watch.client_mode`（用户 10-13：保持运行 / 需要时才运行）与它的重启上限。
    # 取值范围住在 `duanxian/jobs.py::_valid`，这里只负责让请求走到那一层。
    client_mode: str | None = None
    restart_max_per_day: int | None = None
    # `tdx_watch.exit_confirm_click`：确认框那只「退出」要不要程序替你点（10-13）。
    # 面板上真有这个勾选框，所以 `jobsboard` 那一行也交得出当前值与措辞。
    exit_confirm_click: bool | None = None
    # `tdx_watch.client_band_lo/hi`（保持开着的时间带，留空＝按收盘链推导）与
    # `restart_daily_*`（每日定时重启，默认 15:38）—— 用户 2026-10-19 点名的两格。
    # 成对方向（开始早于结束）那条跨键判据住在 `duanxian/jobs.py::set_setting`，不在这里判第二遍。
    client_band_lo: str | None = None
    client_band_hi: str | None = None
    restart_daily_enabled: bool | None = None
    restart_daily_at: str | None = None


@app.get("/api/jobs/loops")
def jobs_loops(request: Request):
    """**常驻循环的开关面板**：7 条循环逐条给开关、时点与来源、最近记账、留痕、关掉的代价。

    为什么单独一个端点而不是并进 `/api/jobs/chain`：那个的标题是"收盘链"，
    把探测（`tdx-watch`）与采集（`fundflow`/`watchlist`/`coverage`）塞进去会让标题说谎。
    但**写入面共用同一组 `set_*`**（认整张在册表），否则又回到"两份开关读法、一份留痕"。
    """
    import jobsboard

    try:
        data = jobsboard.loop_switches()
    except Exception as exc:  # noqa: BLE001  读不出来要说"判断不了"，不许回退成"都在跑"
        return {"data": {"generated_at": None, "loops": [], "available": False,
                         "reason": f"常驻循环状态读不出来：{type(exc).__name__}: {exc}"}}
    data["can_edit"] = True
    return {"data": data}


@app.post("/api/jobs/loops")
def jobs_loops_update(payload: LoopPatch, request: Request):
    """关/开一条常驻循环（或改它的时点、写理由）。与收盘链同一套校验与留痕。"""
    import jobsboard

    body = payload.model_dump(exclude_none=True)
    touched = _job_patch_guard(body)
    who = (f"api:/api/jobs/loops@{request.client.host if request.client else '未知来源'}")
    status, resp = _apply_job_patch(body, touched[0], who)
    if status != 200:
        raise HTTPException(status, resp.get("reason") or "未采纳")
    return {"data": jobsboard.loop_switches(), "reason": resp["reason"]}


@app.get("/api/jobs/chain-steps")
def jobs_chain_steps():
    """加工段**最近一次逐步结果**：哪一步没成、为什么（网络/无权限/按口径拒写/超时）、跑了多久。

    为什么必须有这一格：28 步的结果原先只存在于跑的时候那段 stdout 里，跑完就没地方查 ——
    于是"昨天资金流没补上"只能靠人记得去翻日志。现在它是有据可查的读数，
    配合下面那条 POST 就是"看见失败 → 单独补救一次"的闭环。
    """
    import jobsboard
    return {"data": jobsboard.chain_steps()}


@app.post("/api/jobs/chain-steps/{name}/run")
def jobs_chain_step_rerun(name: str, request: Request):
    """单独补跑一步（页面上那颗"补跑这一步"）。抢的是加工段那把锁，跑不出两次并发。"""
    import jobsboard

    who = f"api:/api/jobs/chain-steps@{request.client.host if request.client else '未知来源'}"
    status, payload = jobsboard.rerun_step(name, via=who)
    if status != 202:
        raise HTTPException(status, payload.get("reason") or "未触发")
    return payload


def _notify_payload(request: Request, cfg: dict | None = None) -> dict:
    """`/api/notify` 两份读数的**同一个构造处**（与 `_net_payload` 同一条理由）。

    GET 与 POST 各写一遍字段，迟早出现"改完一个字段，面板上别的格子回到旧形状"；
    而 POST 之后正是要看到新状态，所以两边必须同源。

    ⚠️ 这里**永远不含凭据**：`NC.state()` 交的是掩码。写一次就盖一次"谁/从哪个 IP"，
    因为加一条渠道 = 给这台机器加一条数据外送的出口 —— 那是必须可追溯的动作。
    """
    import notifyconfig as NC
    from duanxian import jobs as _jobs, notify

    data = NC.state(via_hint=f"{request.client.host if request.client else '未知来源'}")
    data["can_edit"] = True      # 白名单内的机器本来就能看到全部数据；加一条外送出口不扩权限，但留痕带 IP
    data["catalog"] = notify.scene_catalog()
    data["history"] = notify.history()
    # 「今天这一档处于什么状态」：判据与执行同住 `notifyconfig.slot_state`，这里只把发送侧的
    # 去重（`sent.json`）与**三态**交易日读数喂给它。专治"到点却没发，而界面上不存在这件事"。
    from duanxian.util import china_now, china_today
    cfg = cfg if cfg is not None else NC.load()
    _now, _session = china_now(), notify._session_date()
    _trade = notify.trade_day_or_none(str(china_today()))
    by_id = {p.get("id"): p for p in (cfg.get("plans") or [])}
    for view in data.get("plans") or []:
        raw = by_id.get(view.get("id"))
        if raw is None:
            continue
        st = NC.slot_state(raw, _now, sent=notify.already_sent(str(raw.get("id")), _session),
                           trade_day=_trade)
        view["today_state"] = st["state"]
        view["today_label"] = st["label"]
    ledger = _jobs.state().get("notify_push")
    ledger = ledger if isinstance(ledger, dict) else {}
    detail = ledger.get("last_detail") if isinstance(ledger.get("last_detail"), dict) else {}
    data["last_run"] = {"last_run_at": ledger.get("last_run_at"),
                        "last_ok_at": ledger.get("last_ok_at"),
                        "last_status": ledger.get("last_status"),
                        "consecutive_failures": ledger.get("consecutive_failures"),
                        # 上一次为什么没成 —— 归因只认 `reason` 这一个键（系统配置定的约定）
                        "reason": detail.get("reason") or ledger.get("last_error")}
    data["generated_at"] = _now.isoformat(timespec="seconds")
    return data


@app.get("/api/notify")
def notify_status(request: Request):
    """消息推送：四个渠道（邮件 / 企业微信群助手 / 钉钉群助手 / 飞书群助手）+ 定时计划。

    读的是 `<data home>/notify/config.json` 现值。凭据一律掩码，见 `_notify_payload`。
    """
    return {"data": _notify_payload(request)}


class NotifyPatch(BaseModel):
    """一次只改一件事（与 `/api/net`、`/api/jobs/chain` 同一条规矩）：
    合并改会让"到底改了什么"事后没人答得上来。"""
    enabled: bool | None = None
    channel: dict | None = None
    remove_channel: str | None = None
    plan: dict | None = None
    remove_plan: str | None = None
    test_channel: str | None = None
    #: 手动跑一条计划（面板上那个「立刻发一次」）。它绕过"今天发过了"那道去重，
    #: 但**不绕过**渠道可用性校验 —— 没配好就是不许发，那正是要让人看见的失败。
    run_now: str | None = None


@app.post("/api/notify")
def notify_update(payload: NotifyPatch, request: Request):
    """改渠道 / 改计划 / 总开关 / 发一条测试。校验全在 `notifyconfig`，这里不重复一份。"""
    import notifyconfig as NC
    from duanxian import notify

    body = payload.model_dump(exclude_none=True)
    allowed = ("enabled", "channel", "remove_channel", "plan", "remove_plan",
               "test_channel", "run_now")
    touched = [k for k in body if k in allowed]
    if not touched:
        raise HTTPException(400, f"没有要改的项（可用：{' / '.join(allowed)}）")
    if len(touched) > 1:
        raise HTTPException(400, f"一次只改一件事，收到 {'、'.join(touched)}")
    key = touched[0]
    who = f"api:/api/notify@{request.client.host if request.client else '未知来源'}"
    if key == "enabled":
        NC.set_enabled(bool(body[key]), via=who)
        reason = f"推送总开关已{'打开' if body[key] else '关闭（到点的计划也不会发出去）'}"
    elif key == "channel":
        cfg, why = NC.upsert_channel(body[key] or {}, via=who)
        if why:
            raise HTTPException(400, why)
        reason = f"渠道 {body[key].get('key')} 已保存（凭据只写不读，页面上永远显示掩码）"
    elif key == "remove_channel":
        cfg, why = NC.remove_channel(str(body[key]), via=who)
        if why and "已删除" not in why:
            raise HTTPException(404, why)
        reason = why or "已删除"
    elif key == "plan":
        cfg, why = NC.upsert_plan(body[key] or {}, via=who)
        if why:
            raise HTTPException(400, why)
        reason = f"计划 {body[key].get('id')} 已保存（下一个心跳起按它判，不用重启）"
    elif key == "remove_plan":
        cfg, why = NC.remove_plan(str(body[key]), via=who)
        if why:
            raise HTTPException(404, why)
        reason = "计划已删除"
    elif key == "test_channel":
        # 「测试发送」是真的往外发一条：正文里写明是测试，且只发这一条渠道
        res = notify.send_to([str(body[key])], "YuanShan 测试消息",
                             "这是一条测试推送：看到它说明这个渠道配通了。"
                             "（正文不含任何账户、持仓与库内数据）")
        if not res["ok"]:
            # `detail` 里已经带渠道键（`deliver` 的逐渠道原话），这里别再拼一遍 ——
            # 实测拼过：`panel-check：没发出去 —— panel-check：回体说没收到…`
            raise HTTPException(502, f"没发出去 —— {res['detail']}"
                                     + (f"；另有 {len(res['skipped'])} 条跳过原因" if res["skipped"] else ""))
        reason = f"{body[key]}：{res['detail']}"
    else:   # key == "run_now"
        # 「立刻发一次」：绕过"今天发过了"那道去重（人就是要再看一遍），
        # 但**不绕过**渠道可用性校验 —— 没配好就是不许发，那正是要让人看见的失败。
        pid = str(body[key])
        plan = next((x for x in NC.load()["plans"] if str(x.get("id")) == pid), None)
        if plan is None:
            raise HTTPException(404, f"没有 id 为 {pid!r} 的计划")
        out = notify.run_plan(plan, force=True)
        if out.get("status") != "ok":
            raise HTTPException(502, f"{out.get('name') or pid}：没发出去 —— "
                                     f"{out.get('detail') or out.get('reason')}"
                                     + (f"；跳过 {len(out.get('skipped_channels') or [])} 条渠道"
                                        if out.get("skipped_channels") else ""))
        reason = (f"{out.get('name') or pid}：已发送（{out.get('detail')}），"
                  "今天这一场算发过了；下次还要立刻发就再点一次")
    return {"data": _notify_payload(request), "reason": reason}


class NotifyPreview(BaseModel):
    plan: dict
    date: str = ""


@app.post("/api/notify/preview")
def notify_preview(payload: NotifyPreview, request: Request):
    """不发送，只看这条计划将要发出去的正文。

    为什么必须有：多选内容之后光看勾是看不出对错的 —— 而一条已经发到群里的错消息收不回来。
    """
    from duanxian import notify
    return {"data": notify.preview(payload.plan or {}, payload.date)}


def _wxpush_payload(request: Request, cfg: dict | None = None) -> dict:
    """`/api/wxpush` 两份读数的**同一个构造处**（与 `_notify_payload` 同一条理由）。

    ⚠️ 这里挂的 `preflight()` **全是只读查询**（前台窗口、渲染面采样、窗口在不在、空闲时长），
    面板每打开一次就跑一遍，所以绝不允许为了"看看行不行"往桌面注入按键或起进程。
    真按键只发生在 `POST /api/wxpush/send` 且 `dry_run=false` 那一条路上。
    """
    import wxpushconfig as WC
    from duanxian import wxpush as WX

    cfg = cfg if cfg is not None else WC.load()
    data = WC.state(via_hint=f"{request.client.host if request.client else '未知来源'}")
    data["can_edit"] = True      # 与 /api/notify 同口径：改这条通道不扩权限，但留痕带 IP
    data["preflight"] = WX.preflight(cfg)
    data["history"] = WX.history()
    from duanxian.util import china_now
    data["generated_at"] = china_now().isoformat(timespec="seconds")
    return data


@app.get("/api/wxpush")
def wxpush_status(request: Request):
    """个人微信推送：界面自动化步骤、预检闸现状、内容档、最近发送留痕。

    读数零副作用：不按键、不动剪贴板、不动窗口。预检的每一条实情都在这里原样交出，
    包括"这台机器现在为什么发不出去"。
    """
    return {"data": _wxpush_payload(request)}


class WxPushPatch(BaseModel):
    """一次只改一件事（与 `/api/notify`、`/api/net` 同一条规矩）。"""
    enabled: bool | None = None
    default_to: str | None = None
    step: dict | None = None
    reset_steps: bool | None = None
    guard: dict | None = None
    template: dict | None = None
    remove_template: str | None = None


@app.post("/api/wxpush")
def wxpush_update(payload: WxPushPatch, request: Request):
    """改开关 / 改默认收件人 / 改一步 / 还原步骤出厂 / 改一道闸 / 内容与内容档增删改。"""
    import wxpushconfig as WC

    body = payload.model_dump(exclude_none=True)
    allowed = ("enabled", "default_to", "step", "reset_steps", "guard",
               "template", "remove_template")
    touched = [k for k in body if k in allowed]
    if not touched:
        raise HTTPException(400, f"没有要改的项（可用：{' / '.join(allowed)}）")
    if len(touched) > 1:
        raise HTTPException(400, f"一次只改一件事，收到 {'、'.join(touched)}")
    key = touched[0]
    who = f"api:/api/wxpush@{request.client.host if request.client else '未知来源'}"
    if key == "enabled":
        cfg = WC.set_enabled(bool(body[key]), via=who)
        reason = ("微信推送总开关已打开（预检过了才会真按键）" if body[key] else
                  "微信推送总开关已关闭（到点的调用也会被预检拒掉）")
    elif key == "default_to":
        cfg, why = WC.set_default_to(str(body[key]), via=who)
        if why:
            raise HTTPException(400, why)
        reason = f"默认收件人已设为 {cfg['default_to'] or '（空）'}"
    elif key == "step":
        cfg, why = WC.set_step(body[key] or {}, via=who)
        if why:
            raise HTTPException(400, why)
        reason = f"步骤 {body[key].get('key')} 已保存（下一次发送就按这一格走，不用重启）"
    elif key == "reset_steps":
        cfg = WC.reset_steps(via=who)
        reason = "所有步骤已还原成出厂流程"
    elif key == "guard":
        cfg, why = WC.set_guard(body[key] or {}, via=who)
        if why:
            raise HTTPException(400, why)
        reason = f"预检闸 {body[key].get('key')} 已保存"
    elif key == "template":
        cfg, why = WC.upsert_template(body[key] or {}, via=who)
        if why:
            raise HTTPException(400, why)
        reason = f"内容档 {body[key].get('key')} 已保存"
    else:   # remove_template
        cfg, why = WC.remove_template(str(body[key]), via=who)
        if why and "已删除" not in why:
            raise HTTPException(404, why)
        reason = why or "内容档已删除"
    return {"data": _wxpush_payload(request, cfg), "reason": reason}


class WxPushSend(BaseModel):
    """给其他程序调用的那一个入口。"""
    to: str = ""
    text: str = ""
    template: str = ""
    scenes: dict | None = None
    title: str = ""
    date: str = ""
    dry_run: bool = False


@app.post("/api/wxpush/send")
def wxpush_send(payload: WxPushSend, request: Request):
    """往本机微信发一条：`to` + 三选一的内容（`template` / `scenes` / `text`）。

    `dry_run=true` ⇒ **一个键都不按**，只回「正文 + 预检每条实情 + 将要执行的动作序列」。
    接这条通道的第一步就该是它：先看清这台机器现在有没有桌面可用。
    真发送失败（预检拒发、中途焦点跑掉、注入被系统拒绝）一律回 502 + 原因，
    不返回 200 的"大概发了" —— 这一侧读不回执，`status` 最多只到 `injected`，
    送达要到自己手机上看。
    """
    from duanxian import wxpush as WX

    body = payload.model_dump(exclude_none=True)
    who = f"api:/api/wxpush/send@{request.client.host if request.client else '未知来源'}"
    out = WX.send(to=body.get("to", ""), text=body.get("text", ""),
                  template=body.get("template", ""), scenes=body.get("scenes"),
                  title=body.get("title", ""), date=body.get("date", ""),
                  dry_run=bool(body.get("dry_run")), source="api", via=who)
    if not out["ok"]:
        if out.get("status") == "dry_run":
            return {"data": out,
                    "reason": "这台机器现在发不出去（dry_run，一个键都没按）—— "
                              + (out.get("reason") or "预检没给出具体原因")}
        raise HTTPException(502, out.get("reason") or "没发出去（原因未记）")
    if out.get("status") == "dry_run":
        return {"data": out, "reason": f"预检通过：将要执行 {out['op_count']} 个动作，"
                                       f"正文 {out['chars']} 字（**这次一个键都没按**）"}
    return {"data": out,
            "reason": f"已按完 {out['op_count']} 个动作（{out['chars']} 字 → {out['to']}）。"
                      "这一侧读不回执：**送达请到自己手机上看那一条**"}


@app.post("/api/jobs/{key}/run")
def jobs_run(key: str):
    import jobsboard

    status, payload = jobsboard.trigger(key)
    if status != 202:
        raise HTTPException(status, payload.get("reason") or "未触发")
    return payload


def _net_payload(request: Request, cfg: dict | None = None) -> dict:
    """`/api/net` 两份读数的**同一个构造处**。

    POST 也走这里，不能只回 `state()`：少带 `can_edit` 时，浏览器上刚刚改完一次
    白名单，"加白/删除"这些控件就因为 `can_edit` 变 undefined 而消失了 ——
    看着像"改完就不让我改了"。GET 与 POST 的字段必须一模一样。
    """
    import netconfig as _net

    loop = _is_loopback(request)
    data = _net.state(cfg)
    data["can_edit"] = loop
    data["edit_reason"] = None if loop else (
        "只能在主机上改（127.0.0.1）：局域网侧改白名单等于让被放行的一方自己扩权限")
    data.update(_net.denials())
    from duanxian.util import china_now
    data["generated_at"] = china_now().isoformat(timespec="seconds")
    return data


@app.get("/api/net")
def net_status(request: Request):
    """网络边界的现状：配置说什么 vs 进程实际绑在哪，两个都给（勾了没重启必须看得出来）。

    `can_edit` 只在本机回环时为真 —— 从局域网那一侧改白名单，等于让客人给自己开门。
    """
    return {"data": _net_payload(request)}


class NetPatch(BaseModel):
    """一次只改一件事：开关 / 加一条 / 删一条。合并改会让"改了什么"说不清。"""
    lan_enabled: bool | None = None
    add: str | None = None
    remove: str | None = None


@app.post("/api/net")
def net_update(payload: NetPatch, request: Request):
    import netconfig as _net

    if not _is_loopback(request):
        raise HTTPException(403, "只能在主机上改网络边界（127.0.0.1）"
                                 "：从局域网侧加白名单，等于让被放行的一方自己扩权限")
    body = payload.model_dump()
    touched = [k for k, v in body.items() if v is not None]
    if not touched:
        raise HTTPException(400, "没有要改的项（可用：lan_enabled / add / remove）")
    if len(touched) > 1:
        raise HTTPException(400, f"一次只改一件事，收到 {len(touched)} 件：{'、'.join(touched)}")
    # 盖章要带上"从哪儿改的 + 哪个地址改的"：白名单多出两个没人认领的地址，
    # 就是这么查不出来的
    who = f"api:/api/net@{request.client.host if request.client else '未知来源'}"
    key = touched[0]
    if key == "lan_enabled":
        cfg = _net.save(lan_enabled=bool(body[key]), via=who)
    elif key == "add":
        cfg, why = _net.add_ip(str(body[key]), via=who)
        if why:
            raise HTTPException(400, why)
    else:
        cfg, why = _net.remove_ip(str(body[key]), via=who)
        if why:
            raise HTTPException(400, why)
    data = _net_payload(request, cfg)
    data["changed"] = key
    # 加白/删白是**立刻生效**的（每个请求现读一次配置）；只有开关改的是绑定地址，
    # 那个必须重启 —— 两种"生效时机"不一样，不说的话用户会以为重启才是常态。
    data["applied_now"] = key != "lan_enabled"
    return {"data": data}


def _is_loopback(request: Request) -> bool:
    ip = request.client.host if request.client is not None else ""
    return ip == "::1" or str(ip).startswith("127.")


@app.get("/api/datasource")
def datasource_status():
    """源登记 + 每个源"多久没说话了"。**没调度就没有新鲜**：只报 `last_probe_at`
    不报 `stale_seconds`，整屏陈旧健康度会被当成现值用。"""
    import sysboard

    try:
        return {"data": sysboard.datasource_payload()}
    except Exception as exc:  # noqa: BLE001
        return {"data": {"generated_at": None, "available": False, "counts": None,
                         "sources": [], "probe": None,
                         "reason": f"源登记读不出来：{type(exc).__name__}: {exc}"}}


@app.get("/api/system/doctor")
def system_doctor(fresh: int = Query(0, ge=0, le=1)):
    """`manage.py doctor` 的结果上界面：**判定逻辑仍在 manage.py 那一处**，这里只转述。
    返回带 `age_seconds`/`ttl_seconds`/`refreshing` —— 体检单跑约 10 秒，首屏给旧值并交年龄。"""
    import sysboard

    try:
        return {"data": sysboard.doctor_payload(force=bool(fresh))}
    except Exception as exc:  # noqa: BLE001
        return {"data": {"generated_at": None, "available": False, "items": [],
                         "reason": f"体检跑不起来：{type(exc).__name__}: {exc}"}}


@app.get("/api/system/log")
def system_log(name: str = Query("uvicorn.out"), lines: int = Query(200, ge=20, le=800),
               run: str = Query("", max_length=32)):
    """原始文本日志的**尾部**（用户 2026-10-10：日志分了几层，但要开终端才看得见最后一层）。

    只读 `logtail.LOGS` 白名单里那两份，路径由后端拼 —— 这台机器开着局域网访问，
    接口绝不接受路径参数。`run` 给一个归档号就读那一次运行留下的历史（同样只认当场列出来的名单）。
    体检/台账坏了时，这里是唯一的现场证据，所以按"能读多少算多少"回报，读不到就说原因。
    """
    import logtail

    try:
        data = logtail.tail(name, lines, run=(run or None))
        data["index"] = logtail.live_summary()
        return {"data": data}
    except Exception as exc:  # noqa: BLE001
        return {"data": {"available": False, "lines": [], "name": name,
                         "reason": f"日志读不出来：{type(exc).__name__}: {exc}"}}


# --------------------------------------------------------------- MCP 服务（展示层数据的只读出口）
# 台账在 docs/MCP服务登记表.md，机器读的那份在 mcp_service/capabilities.py，两者由
# tests/test_mcp_registry_sync.py 逐条对齐。这里三条都是**只读**：registry 现读登记表、
# status 现做体检、try 只按白名单调一次已有 GET（不重算、不写任何东西）。

class McpTryReq(BaseModel):
    capability_id: str = ""
    params: dict[str, Any] = {}


@app.get("/api/mcp/registry")
def mcp_registry():
    """MCP 工具与来源端点的对照表：登记的 A/B/C 层条目＋现扫的全量只读索引＋台账同步状态。"""
    import mcpboard

    try:
        return {"data": mcpboard.registry_payload()}
    except Exception as exc:  # noqa: BLE001 目录读不出来就是事故，不能回一份空清单冒充「今天没有」
        return {"data": {"available": False, "capabilities": [], "endpoints": [],
                         "counts": {}, "reason": f"登记表读不出来：{type(exc).__name__}: {exc}"}}


@app.get("/api/mcp/status")
def mcp_status():
    """接入前的体检：后端可达、登记表条数、台账是否对齐、SDK 版本、HTTP 模式端口在不在听。"""
    import mcpboard

    try:
        return {"data": mcpboard.status_payload()}
    except Exception as exc:  # noqa: BLE001
        return {"data": {"available": False, "checks": [],
                         "reason": f"体检跑不起来：{type(exc).__name__}: {exc}"}}


@app.post("/api/mcp/try")
def mcp_try(req: McpTryReq):
    """在界面上真的调一次某个登记工具（走的是与智能体同一条 `mcp_service.client`）。

    只读：`capability_id` 必须在登记表里，或直接给索引里的 GET 路径；参数逐条对回端点
    真认识的那些键。POST 只是因为这带请求体，本身不写任何状态。
    """
    import mcpboard

    if not (req.capability_id or "").strip():
        raise HTTPException(400, "capability_id 不能为空")
    return {"data": mcpboard.try_invoke(req.capability_id.strip(), req.params or {})}


# --------------------------------------------------------------- 复盘笔记与关注提醒
# 列结构、字段中文、默认值判据全部在 `duanxian/review_notes.py`（那张表是唯一真相源），
# 这里只做 HTTP 外壳：校验不过 400 点名、认不出来的 id 404、取数异常 502 带原因。
# ⚠️ 与交易日志同一条红线：这两张表的内容**不接入任何 AI prompt**。

class ReviewNoteIn(BaseModel):
    date: str
    values: dict[str, str] = {}
    machine_baseline: dict[str, str] = {}
    machine_generated_at: str = ""


@app.get("/api/review-note")
def review_note_get(date: str = Query("", description="YYYY-MM-DD；空=库里实际有数的最近一场"),
                    refresh: int = Query(0, ge=0, le=1)):
    """这一天的笔记 + 机器草稿 + 字段清单（一次给全，界面不必自己拼列名）。"""
    from duanxian import review_notes

    try:
        want = date.strip() or review_notes.expected_date()
        if not want:
            return {"data": {"available": False, "reason": "库里没有任何已入库的行情日，没有可打开的笔记"}}
        return {"data": {"available": True, **review_notes.day_bundle(want, refresh=bool(refresh))}}
    except review_notes.NoteError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"复盘笔记读取出错：{exc}") from exc


@app.post("/api/review-note")
def review_note_save(body: ReviewNoteIn):
    """按行情日存一份。**返回存完之后读回的那一份**，界面不用自己拼。"""
    from duanxian import review_notes

    try:
        return {"data": review_notes.save_note(body.date, body.values, body.machine_baseline,
                                               body.machine_generated_at or None)}
    except review_notes.NoteError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"复盘笔记未能保存：{exc}") from exc


@app.get("/api/review-notes")
def review_notes_list(limit: int = Query(120, ge=1, le=500)):
    from duanxian import review_notes

    try:
        return {"data": {"rows": review_notes.list_notes(limit)}}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"笔记清单读不出来：{exc}") from exc


class ReminderIn(BaseModel):
    base_date: str
    sessions: int
    kind: str = "stock"
    security_code: str = ""
    security_name: str = ""
    note: str = ""
    reason: str = ""


class ReminderStatusIn(BaseModel):
    status: str


@app.get("/api/reminders")
def reminders_list(status: str = Query("", description="open/done/cancelled；空=全部")):
    """到期时间轴：`next` 是今天或以后最近的一条，过期的单独列出来（过期不等于完成）。"""
    from duanxian import review_notes

    try:
        return {"data": {"available": True, **review_notes.list_reminders(status.strip() or None)}}
    except review_notes.NoteError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"提醒读不出来：{exc}") from exc


@app.get("/api/reminders/due")
def reminders_due(base_date: str = Query(..., description="起算日 YYYY-MM-DD"),
                 sessions: int = Query(..., ge=1, le=250)):
    """建之前先看一眼"3/7/10 个交易日之后到底是哪天"。到期日只由后端算，
    界面拿同一个函数预览，避免"按钮上写的和存进去的日子"两套口径。"""
    from duanxian import review_notes

    try:
        return {"data": review_notes.due_on(base_date, sessions)}
    except review_notes.NoteError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"到期日算不出来：{exc}") from exc


@app.post("/api/reminders")
def reminders_add(body: ReminderIn):
    """建一条提醒。到期日由后端按**交易日**算，界面不许自己加天数（那样周末与长假会算歪）。"""
    from duanxian import review_notes

    try:
        return {"data": review_notes.add_reminder(
            body.base_date, body.sessions, note=body.note, reason=body.reason,
            kind=body.kind, security_code=body.security_code, security_name=body.security_name)}
    except review_notes.NoteError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"提醒未能建立：{exc}") from exc


@app.post("/api/reminders/{rid}/status")
def reminders_status(rid: int, body: ReminderStatusIn):
    from duanxian import review_notes

    try:
        return {"data": review_notes.set_reminder_status(rid, body.status)}
    except review_notes.NoteError as exc:
        raise HTTPException(400, str(exc)) from exc
    except KeyError:
        raise HTTPException(404, f"没有 id={rid} 这条提醒") from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"提醒状态未能更新：{exc}") from exc


# --------------------------------------------------------------- 界面状态（上次选了谁）
class UiPrefIn(BaseModel):
    key: str
    value: str = ""
    note: str = ""


@app.get("/api/ui-prefs")
def ui_prefs_get():
    """交出已登记键的当前值（一次往返拿全）。读不到库时每个键给空串，页面按"没记过"处理。"""
    from duanxian import ui_pref

    return {"data": {"rows": ui_pref.snapshot(), "registered": ui_pref.registered()}}


@app.post("/api/ui-pref")
def ui_pref_save(body: UiPrefIn):
    """写一个界面状态键。未登记的键、形状不对的值一律 400 点名。"""
    from duanxian import ui_pref

    try:
        return {"data": ui_pref.set_value(body.key, body.value, body.note)}
    except ui_pref.PrefError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"界面状态未能保存：{exc}") from exc
