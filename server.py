# 本文件继承自上游 vibe-astock v1.1.0（Apache-2.0，见根 LICENSE），本机有实质性改动。
# 改了什么、归属怎么判、还有哪些文件没逐个声明：docs/上游改动与归属清单.md；本机自写部分见 LICENSE.local.md。
"""短线复盘 Web 后端 —— FastAPI 包住复盘图 + 缓存 + 静态前端"""

from __future__ import annotations

from duanxian.paths import data_path
import html
import hmac
import json
import os
import re
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import Body, FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from duanxian import live_emotion, overseas, preflight, reflection, review_store, trade_calendar
from duanxian import jobs as jstore          # 收盘链的表与互斥都在这里（两个入口共用）
from duanxian.review_store import md_to_html as _md_to_html, strip_prefix as _strip_prefix
from duanxian.config import make_llm
from duanxian.review_graph import build_review_graph
from duanxian.deepdive.graph import run as deepdive_run
from duanxian.roles import ROLES
from main import initial_state
from duanxian.util import (
    china_now, china_today, is_a_share_closed, is_degraded_report, is_weekend,
    safe_join, strip_model_noise, validate_trade_date,
)
from duanxian.prompts import PACK

SCHEMA_VERSION = 1
_HERE = os.path.dirname(os.path.abspath(__file__))
# React 构建产物：存在就优先服务它，单端口即可访问全部界面，不必另起 vite dev。
# 构建：cd frontend && npm run build
_DIST = os.path.join(_HERE, "frontend", "dist")
_REVIEW_DIR = data_path("reviews")
_DD_DIR = data_path("deepdive")
_WK_DIR = data_path("weekly")
os.makedirs(_REVIEW_DIR, exist_ok=True)
os.makedirs(_WK_DIR, exist_ok=True)
# 允许全部 API 读写的 Host。默认只认本机；挂到域名下访问时，用
# `VIBE_ALLOW_HOSTS="myhost,www.myhost"`（逗号分隔）把域名加进来，否则全部 API 会 403。
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"} | {
    h.strip().lower() for h in os.environ.get("VIBE_ALLOW_HOSTS", "").split(",") if h.strip()
}
import netconfig as _net

# 开放局域网时对面是拿**本机自己的局域网地址**敲进来的，Host/Origin 就是那个 IP。
# 这道判定必须**动态**问 `netconfig.is_local_host()`（见 `_vr_guard`），不能在 import 时
# 一次性并进 `_ALLOWED_HOSTS`：那样"配置开了、Host 校验还拦着"，而拦人的是一道无关的检查。

from vr.product_version import PRODUCT_NAME, PRODUCT_VERSION

app = FastAPI(title=PRODUCT_NAME, version=PRODUCT_VERSION)

# ---------------------------------------------------------------- 并入 VR 后端
# 盘面数据 / 首板分析 / 盯盘 / 持仓股 / 自选股 / 个股数据 / 资讯雷达 这几个分栏的
# 后端放在 `vr/`，它的路由在这里并到本 app 上，一个进程一个端口即可访问全部界面。
# `vr/` 来自 Vibe-Research，已包含本产品适配；同步必须逐文件合并并回归，不能整目录覆盖。


def _alert(msg: str) -> None:
    """必达的告警输出"""
    import sys

    print(msg, file=sys.stderr, flush=True)


_VR_PATH_RES: list[re.Pattern] = []


def _load_vr_app_once(vr_dir: str):
    """按文件路径加载 `vr/app.py`，注册名固定为 `vr_app`，**全程只加载一次**。

    两个坑必须同时避开（都是实测过的）：
    - 不能 `import app`：顶层 `app/` 包（2026-09-26 从 FinanceTool 搬进来的摄取层）
      已经占了这个名字。谁先 import 谁赢，另一边就变成
      `ModuleNotFoundError: 'app' is not a package`。
    - 不能用 `vr.app` 包路径名：那是**另一个**模块对象，等于把 `vr/app.py` 执行两遍 ——
      它在模块级起后台调度线程，跑两遍就是同一个作业两个线程，会去抢 TQ 的单连接。
    """
    import importlib.util
    import sys

    mod = sys.modules.get("vr_app")
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location("vr_app",
                                                 os.path.join(vr_dir, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vr_app"] = mod      # 先登记：vr/app.py 内部若有自指 import 才不会递归
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop("vr_app", None)   # 半途而废的模块不许留在表里冒充已加载
        raise
    return mod


def _merge_vr_routes() -> int:
    """把 `vr/` 的路由并进本 app。返回并入条数；失败不影响本仓库自有功能。"""
    import sys

    vr_dir = os.path.join(_HERE, "vr")
    if not os.path.isdir(vr_dir):
        return 0
    if vr_dir not in sys.path:
        # **追加，不放最前**。2026-09-26 从 FinanceTool 搬进来一个顶层 `app/` 包
        # （摄取/计算层的本体），而 `vr/app.py` 也叫 app —— vr 排在前面时
        # `import app` 解析到的是 vr 那个**模块**，于是 `app.datasource` 这类
        # 包内路径当场 `ModuleNotFoundError: 'app' is not a package`。
        # 反过来放末尾只影响"同名裸模块谁赢"，而 ROOT 里没有任何 vr 同名文件
        # （`astock`/`market`/`chat` 都只在 vr/ 下有），所以是安全的一侧。
        sys.path.append(vr_dir)
    try:
        vr_app = _load_vr_app_once(vr_dir)

        before = len(app.router.routes)
        # 只拿 /api/* 的；VR 没有非 /api 路由，这层过滤是为了防它日后加了根路由
        # 把我们的 SPA fallback 顶掉
        for r in vr_app.app.router.routes:
            path = getattr(r, "path", "")
            if not path.startswith("/api/") or path == "/api/chat":
                continue
            app.router.routes.append(r)
            # 记下路径模板 → 正则（`{rid}` 这类参数换成"一段非斜杠"），
            # 供下面那道补偿闸判断"这个请求是不是打在 VR 路由上"
            _VR_PATH_RES.append(re.compile(
                "^" + re.sub(r"\{[^}]+\}", r"[^/]+", re.escape(path)
                             .replace(r"\{", "{").replace(r"\}", "}")) + "$"))
        return len(app.router.routes) - before
    except Exception as exc:  # noqa: BLE001  VR 挂了不该让复盘/交易日志也用不了
        _alert(f"⚠️ VR 后端并入失败（那 7 个分栏会不可用）：{type(exc).__name__}: {exc}")
        return 0


def _guard_vr_userdata() -> None:
    """启动时给 VR 的**不可再生用户数据**留一份备份"""
    import shutil

    vr_home = os.environ.get("VR_DATA_DIR") or os.path.expanduser("~/.vibe-astock-agent/market-data")
    pf = os.path.join(vr_home, "portfolio.json")
    if not os.path.isfile(pf):
        return
    try:
        with open(pf, encoding="utf-8") as fh:
            json.load(fh)
    except Exception as exc:  # noqa: BLE001
        # 损坏：把原始字节另存（带时间戳，不覆盖之前的），并大声说出来
        stamp = china_now().strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(vr_home, f"portfolio.corrupt-{stamp}.json")
        try:
            shutil.copy2(pf, dst)
        except Exception:  # noqa: BLE001
            dst = "（另存也失败了）"
        _alert(f"🔴 VR 持仓文件无法解析（{type(exc).__name__}）！"
               "vr/portfolio.py 会把它当成空持仓，并在 30 分钟内写回覆盖掉。")
        _alert(f"   原始字节已另存：{dst}")
        _alert(f"   历史可解析备份：{vr_home}/portfolio.good-*.json（挑最近的非空那份恢复）")
        return
    try:
        with open(pf, encoding="utf-8") as fh:
            cur = json.load(fh)
        holdings = (cur or {}).get("holdings") or []
        stamp = china_now().strftime("%Y%m%d")
        dst = os.path.join(vr_home, f"portfolio.good-{stamp}.json")
        if not holdings:
            # 空持仓 + 已经存在任何非空备份 → 这大概率是"损坏后被写成空"的产物，别覆盖
            import glob

            for old_bak in glob.glob(os.path.join(vr_home, "portfolio.good-*.json")):
                try:
                    with open(old_bak, encoding="utf-8") as fh:
                        if (json.load(fh) or {}).get("holdings"):
                            return   # 有非空的历史备份在，什么都不做
                except Exception:  # noqa: BLE001
                    continue
        shutil.copy2(pf, dst)
    except Exception as exc:  # noqa: BLE001  备份失败要出声，但不阻断启动
        _alert(f"⚠️ VR 持仓备份失败（{type(exc).__name__}）：{vr_home}/portfolio.good-*.json")


_SAFE_CLI_KINDS = frozenset({"claude"})


def _opted_in_clis() -> frozenset[str]:
    """`VIBE_ALLOW_UNSAFE_CLI=qwen,deepseek` —— 运行服务的人**显式**放开的"""
    raw = os.environ.get("VIBE_ALLOW_UNSAFE_CLI", "")
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


_ALLOWED_CLI_KINDS = _SAFE_CLI_KINDS | _opted_in_clis()

# 已知并有意禁掉的。只用于区分「预期内」和「上游新来的」，**不参与放行判断**
# —— 放行只看白名单，这个集合少写了什么也不会让谁被放进来。
_KNOWN_UNSAFE_CLIS = frozenset({"qwen", "deepseek", "codex", "opencode", "cursor", "kimi"})

# 摘除前的完整 CLI 定义快照 —— 给 `/api/cli/available` 报"这个 kind 装没装"用。
# 摘掉之后 `detect_cli()` 一律返回 None，分不清"没装"和"被禁"，而这两件事
# 对用户是完全不同的信息（一个去装、一个别想了）。
_ALL_CLI_BINS: dict[str, list[str]] = {}

from duanxian.cli_llm import _BINS_ATTR_NAME as _BINS_ATTR  # 单一来源，见那边注释


def _cli_runtime_modules() -> list:
    """所有**已加载的** cli_runtime 模块对象"""
    mods = [m for name, m in list(sys.modules.items())
            if m is not None and (name == "cli_runtime" or name.endswith(".cli_runtime"))
            and hasattr(m, "_CLI_DEFS")]
    if mods:
        return mods
    try:
        import cli_runtime as _cr  # noqa: PLC0415
    except Exception:              # noqa: BLE001  这一版可能根本没带这个文件
        return []
    return [_cr] if hasattr(_cr, "_CLI_DEFS") else []


def _disable_unsafe_clis() -> list[str]:
    """把不在白名单里的 CLI 从**每一份** CLI 运行时字典摘掉，返回实际摘掉的。"""
    mods = _cli_runtime_modules()
    if not mods:
        _alert("🔴 找不到 cli_runtime 模块 ——「接入AI」的自动批准 CLI 可能仍可用，请检查")
        return []

    for mod in mods:
        if not hasattr(mod, _BINS_ATTR):
            setattr(mod, _BINS_ATTR,
                    {k: list(d.get("bins") or []) for k, d in mod._CLI_DEFS.items()})
        _ALL_CLI_BINS.update(getattr(mod, _BINS_ATTR))

    removed: set[str] = set()
    for mod in mods:
        for k in [k for k in list(mod._CLI_DEFS) if k not in _ALLOWED_CLI_KINDS]:
            mod._CLI_DEFS.pop(k, None)
            removed.add(k)

    live = sys.modules.get("vr_app")     # vr/app.py 的注册名，见 _load_vr_app_once
    if live is None or not hasattr(live, "cli_runtime"):
        _alert("⚠️ 无法校验 CLI 禁用是否生效：没找到已加载的 VR app 模块")
    else:
        leftover = sorted(set(live.cli_runtime._CLI_DEFS) - _ALLOWED_CLI_KINDS)
        if leftover:
            _alert(f"🔴 禁用没生效！/api/chat 实际用的运行时里仍有 {leftover} —— 存在第三份 cli_runtime？")

    # 上游新增的（不在我们已知清单里的）要报警 —— 白名单挡住了它，但人得知道
    unknown = sorted(removed - _KNOWN_UNSAFE_CLIS)
    if unknown:
        _alert(f"⚠️ vr/ 上游新增了 CLI {unknown}，已按白名单摘掉。确认安全后再加进 _ALLOWED_CLI_KINDS")
    return sorted(removed)


def _add_vr_to_path() -> None:
    """只把 `vr/` 放进 sys.path，不挂它的路由"""
    import sys
    vr_dir = os.path.join(_HERE, "vr")
    if os.path.isdir(vr_dir) and vr_dir not in sys.path:
        sys.path.append(vr_dir)     # 末尾，理由同 _merge_vr_routes：别抢顶层 app/ 包的名


_VR_ROUTES = _merge_vr_routes()
def _pin_pool_to_settled_session() -> int:
    """把「涨停池」的可见范围钉在**已经收盘**的交易日上。返回 1 = 已生效。

    `vr/` 的首板分析、短线情绪都是"从今天往前回溯，第一天有池子就用它"。
    东财在**盘中**就发布当日池子，于是 09:35 打开看到的是「今天才 18 家涨停」
    这种半成品，而复盘看板是上一场（07-29 的 81 家）—— 同一个产品两个日期，
    而且每刷一次数字都变，最容易让人整块不信。

    复盘系统不做当日动态分析。所以这里不去改 `vr/`（要保持能同步上游），
    而是给它的取数口包一层：**未收盘的交易日，涨停池视为"还没有"**，
    它的回溯逻辑就自然落到上一场，一行都不用动它。

    影响面只在 `vr/` 那几个走涨停池的块；复盘链路走的是
    `duanxian/fetchers.py`（akshare），不受这层影响。
    """
    live = sys.modules.get("astock")
    if live is None or not hasattr(live, "em_zt_topic_pool"):
        _alert("⚠️ 没能钉住涨停池日期：找不到已加载的 vr astock —— "
               "首板/短线情绪可能显示盘中半成品")
        return 0
    if getattr(live, "_pool_pinned", False):
        return 1                                   # 幂等：重复调用无害

    orig = live.em_zt_topic_pool

    def guarded(kind, date, sort, *a, **kw):
        ymd = str(date)
        if len(ymd) == 8 and ymd.isdigit():
            iso = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
            if not trade_calendar.is_settled(iso):
                return []                          # 还没收盘 → 当作还没有池子
        return orig(kind, date, sort, *a, **kw)

    live.em_zt_topic_pool = guarded
    live._pool_pinned = True
    # 留一个未加锁的原函数出口：盘面数据页要显示**今日实时**的打板情绪，
    # 而这道锁是给复盘类块用的。锁是钝器，需要今天数据的地方走这个口子。
    live._pool_unpinned = orig
    return 1


_POOL_PINNED = _pin_pool_to_settled_session()
_guard_vr_userdata()
_DISABLED_CLIS = _disable_unsafe_clis()

_VR_API_KEY = os.environ.get("VR_API_KEY", "").strip()

# This addition is local-only and has its own credentials/data directory.
from review_agent.api import Manager as ReviewAgentManager, create_router as review_agent_router
from review_agent.runtime import Runtime as ReviewAgentRuntime
from review_agent.store import Store as ReviewAgentStore
from review_agent.evidence import EvidenceError as ReviewAgentError

_REVIEW_AGENT_ROOT = Path(os.environ.get("ASTOCK_AGENT_HOME", "~/.vibe-astock-agent")).expanduser().resolve()


def _get_review_agent(_app=app):
    manager = getattr(_app.state, "review_agent", None)
    if manager is None:
        raise ReviewAgentError("复盘 Agent 服务尚未启动")
    return manager


app.include_router(review_agent_router(_get_review_agent, _VR_API_KEY))


@app.get("/api/astock/health")
def astock_health():
    """Launcher readiness: identifies this launch, exposes no user configuration."""
    return {"service": "yuanshan", "version": PRODUCT_VERSION, "launch_id": os.environ.get("ASTOCK_LAUNCH_ID", ""),
            "ready": getattr(app.state, "review_agent", None) is not None
                     and os.path.isfile(os.path.join(_DIST, "index.html"))}


# =============================================================================
# 传输层：压缩、缓存头、慢请求的一行账（2026-10-25）
#
# 为什么动这一层（实测，不是猜）：首页一次打开发 26 个请求、拉 **1,274 KB JSON，一个字节都没压**
# （`content-encoding` 为空）。本机 CPU 不是瓶颈（`/api/market/emotion` 热态 7ms 就是证据），
# 卡的是"浏览器对同一站点只有约 6 条 HTTP/1.1 连接"——慢的那几发把连接占住，本地那 20 发排队。
# 本机实测 gzip(-6)：`/api/watchlist/stocks?limit=2000` 447 KB→71 KB、
# `/api/market/sector-cycle` 321 KB→38 KB ⇒ 整包约降到两成。
# =============================================================================

#: 不压缩的路径前缀。旁路的理由**不是**省 CPU：
#: `/api/chat` 是 ndjson **逐块推送**（`vr/app.py:126`），压一层就把"每个事件立刻可见"
#: 变成"攒一块再 flush"，AI 那一栏的字会一顿一顿；研报原文件与回测执行 zip 已经是压缩包，
#: 再压一遍只费 CPU。
_NO_GZIP_PREFIXES = ("/api/chat", "/api/myreports/file/", "/api/review-agent/chat-jobs/")


class _SelectiveGZip:
    """Starlette 的 `GZipMiddleware`，但上面那几个前缀直接旁路。"""

    def __init__(self, app, *, minimum_size: int = 1024, exempt: tuple = ()):
        self.inner = GZipMiddleware(app, minimum_size=minimum_size)
        self.uncompressed = self.inner.app          # GZipMiddleware 存的"下一层"
        self.exempt = exempt

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and str(scope.get("path", "")).startswith(self.exempt):
            await self.uncompressed(scope, receive, send)
            return
        await self.inner(scope, receive, send)


_SLOW_MS = 500
_SLOW_LOG = os.path.join(_HERE, ".local", "slow_requests.log")

#: vite 的产物带内容哈希（`index-<hash>.js`），可以永久缓存；页面 HTML 反过来**必须每次问服务器**
#: —— 否则 `npm run build` 之后浏览器还在读旧包，而这个项目判断"前端改动生效了没有"
#: 靠的就是 index.html 里那一串哈希（见《01-启停与端口》）。
_IMMUTABLE_PREFIXES = ("/assets/",)

# 注册顺序（后加的在外层）：这一道先进来 ⇒ 外面还包着两道 IP/Host 闸，
# 里面才是下面那道计时闸。计时闸因此**量到的是含压缩的墙钟**——它要回答的问题是
# "浏览器这一发等了多久"，不是"SQL 跑了多久"。
app.add_middleware(_SelectiveGZip, minimum_size=1024, exempt=_NO_GZIP_PREFIXES)


@app.middleware("http")
async def _transport_headers_and_slow_log(request: Request, call_next):
    """补 `Cache-Control`，并把 ≥500ms 的请求各记一行到 `.local/slow_requests.log`。

    为什么"只记慢的"：`--no-access-log` 是刻意的（全量访问日志把取数与复盘每一发都写进磁盘，
    还带查询串），但"页面卡"这件事不能继续靠猜 —— 一行慢请求账就定位得到是哪个接口。
    ⚠️ 这个 ms 是从进到出的墙钟，含压缩；**流式响应在响应头写完就返回**，所以 `/api/chat`
    那类端点的总耗时不在这里（看作业自己的日志），别拿这些行去判 AI 栏快慢。
    """
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    path = request.url.path
    try:
        if "cache-control" not in response.headers:
            if path.startswith(_IMMUTABLE_PREFIXES):
                response.headers["cache-control"] = "public, max-age=31536000, immutable"
            elif not path.startswith(("/api/", "/assets/")):
                response.headers["cache-control"] = "no-cache"
    except Exception:   # noqa: BLE001 —— 缓存头是锦上添花，不能因为它把请求变成 500
        pass
    if elapsed_ms >= _SLOW_MS and not path.startswith("/assets/"):
        try:
            with open(_SLOW_LOG, "a", encoding="utf-8") as fh:
                fh.write("%s\t%s\t%dms\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), path,
                                                 int(elapsed_ms), response.status_code))
        except OSError:  # noqa: BLE001 —— 目录不在或被别的进程占着，绝不能带崩这一发请求
            pass
    return response


def _host_ok(hostname) -> bool:
    """Host/Origin 是不是"该认的本地地址"。

    动态问一次 `netconfig`：开放局域网时对面敲的是本机网卡地址，写死在 import 期的
    集合里就会"配置开着、这道无关的校验还拦着"。这不放松 DNS 重绑定防护 ——
    重绑定时 Host 是攻击者域名，仍然不在两个来源里。
    """
    return hostname in _ALLOWED_HOSTS or _net.is_local_host(hostname)


@app.middleware("http")
async def _vr_guard(request: Request, call_next):
    """所有 API 共用本机 Host、来源和可选密钥防护。"""
    if request.url.path.startswith("/api/"):
        try:
            host = urlparse("http://" + request.headers.get("host", "")).hostname
            if not _host_ok(host):
                return JSONResponse({"error": "非法 Host；通过域名访问时请配置 VIBE_ALLOW_HOSTS"}, status_code=403)
            for header in ("origin", "referer"):
                value = request.headers.get(header)
                if value:
                    parsed = urlparse(value)
                    if parsed.scheme not in {"http", "https"} or not _host_ok(parsed.hostname):
                        return JSONResponse({"error": "非法来源"}, status_code=403)
        except ValueError:
            return JSONResponse({"error": "非法来源"}, status_code=403)
        if (_VR_API_KEY and request.method != "OPTIONS"
                and request.url.path not in {"/api/health", "/api/astock/health"}
                and not hmac.compare_digest(request.headers.get("authorization", "").encode(),
                                            f"Bearer {_VR_API_KEY}".encode())):
            return JSONResponse({"error": "未授权：缺少或错误的 VR_API_KEY"}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def _origin_ip_guard(request: Request, call_next):
    """**先判来源 IP**：白名单外的机器连页面都拿不到（不只是 API 403）。

    放在 `_vr_guard` 之后注册 = 包在它外面 = 每个请求第一个过这道闸，静态页也不例外。
    只认 `request.client.host`（内核给的对端地址）；`X-Forwarded-For` 是客户端自己写的，
    信它等于把白名单交给对面伪造。本机没有任何反向代理，所以不需要那套信任链。

    被挡下来的来源会留一小段记录（`/api/net` 里能看）：白名单最难查的不是"加错了"，
    而是"对面到底以哪个 IP 进来" —— 中间过一层 NAT 时，看到的就不是你心里那个地址。
    """
    ip = request.client.host if request.client is not None else None
    allowed, why = _net.is_allowed_client(ip)
    if not allowed:
        _net.note_denial(ip, why, request.url.path, china_now().strftime("%Y-%m-%d %H:%M:%S"))
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "未授权：本机未对该来源地址开放",
                                 "client": ip or "未知", "reason": why}, status_code=403)
        return PlainTextResponse(f"403 未授权：本机未对该来源地址开放（你的地址：{ip or '未知'}）",
                                 status_code=403)
    return await call_next(request)

_lock = threading.Lock()
_job = {
    "running": False, "job_id": None, "date": None, "error": None,
    "started": None, "elapsed": 0, "finished_at": None,
}
_dd_job = {
    "running": False, "job_id": None, "stock": None, "error": None,
    "started": None, "elapsed": 0, "finished_at": None,
}

_JOB_TIMEOUT = 15 * 60
_DD_TIMEOUT = 10 * 60


def _job_stuck(job: dict, limit: int) -> bool:
    """任务是否已超时卡死 —— 卡死的任务不该再挡住新任务。"""
    return bool(job.get("running") and job.get("started")
                and time.time() - job["started"] > limit)


def _atomic_write(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)  # 缓存目录首次写时可能尚不存在
    tmp = f"{path}.{uuid.uuid4().hex}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)  # 原子替换，读方永远看到完整文件


def _capture_theme_reasons() -> None:
    """囤当天的题材串（1 次问财请求）。 只能查最近交易日，过期不候"""
    try:
        from duanxian import theme_tree

        r = theme_tree.capture()
        if not r.get("ok"):
            print(f"⚠️ 题材串囤积失败（{r.get('date')}）：{r.get('reason')}")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 题材串囤积异常：{type(exc).__name__}: {exc}")


def _capture_archive(date: str) -> None:
    """把**被复盘那一天**的原始数据整套永久归档 —— 日后还能按新口径重算的唯一依据。

    ⚠️ 必须把 `date` 传下去：补跑历史某天的复盘时不传参，归档的会是"最近交易日"，
       而目标历史日照样缺失 —— 归档看着照常成功，缺口却一直在。
    """
    try:
        from duanxian import archive

        r = archive.capture_day(date)
        if not r.get("ok"):
            print(f"⚠️ 原始数据归档失败（{r.get('date')}）：{r.get('reason')}")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 原始数据归档异常：{type(exc).__name__}: {exc}")


def _capture_backtest_corpus(date: str) -> None:
    """顺手把**被复盘那一天**的回测语料囤下来（1 次请求）。

    ⚠️ 数据源只留最近约 15 个交易日，**过期不候** —— 没在窗口期内抓下来的日子
    就永久缺失。挂在复盘完成之后，语料随日子推移自己长长，回测窗口就不再受
    数据源留存限制。失败只记日志，绝不影响复盘本身。
    """
    try:
        from duanxian.backtest import capture

        r = capture(date)
        if not r.get("ok"):
            print(f"⚠️ 回测语料捕获失败（{r.get('date')}）：{r.get('reason')}")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 回测语料捕获异常：{type(exc).__name__}: {exc}")


def _run_review(date: str, job_id: str) -> None:
    try:
        # 先体检输入 —— 核心数据取不到就别跑。喂空数据进去，模型会硬凑出
        # 看着可信的结论（实测点到一只当天 -6.81% 的票当"主线代表"）。
        pre = preflight.check(date)
        if not pre["ok"]:
            raise RuntimeError(preflight.refuse_reason(pre, date))

        graph = build_review_graph()
        final = graph.invoke(initial_state(date), {"recursion_limit": 50})
        reflection.auto_evaluate_prior(date)  # 先回评上期预测（其次日=今天，数据已出）
        # 体检发现的非核心缺失要如实落进 warnings，界面才会显示「⚠ 部分数据降级」
        payload = review_store.serialize(final, date, pre["warnings"])
        res = review_store.save(payload, date)
        if not res.written:
            # 产物不可用 → 必须让用户看见，不能"任务成功但内容是空的"
            raise RuntimeError(res.reason)
        _capture_theme_reasons()     # 题材串同理（问财只给最近交易日）
        _capture_archive(date)           # 原始数据永久归档（零额外请求，走已有缓存）
        _capture_backtest_corpus(date)   # 复盘写完再囤语料，失败也不影响已产出的复盘
    except Exception as exc:  # noqa: BLE001
        with _lock:
            if _job["job_id"] == job_id:
                _job["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        with _lock:
            if _job["job_id"] == job_id:  # 只结束属于自己的任务（#5）
                _job["running"] = False
                if _job["started"]:
                    _job["elapsed"] = int(time.time() - _job["started"])  # 收尾定格 elapsed（#17）
                _job["finished_at"] = china_now().strftime("%Y-%m-%d %H:%M:%S") + " CST"


def _origin_ok(request: Request) -> bool:
    """只允许本机来源触发昂贵任务，挡掉恶意网页的跨站 POST（#22）。"""
    ref = request.headers.get("origin") or request.headers.get("referer") or ""
    if not ref:
        return True  # 无 Origin（如 curl 本地调用）放行
    return (urlparse(ref).hostname or "") in _ALLOWED_HOSTS


def _force_flag(request: Request) -> bool:
    """`?force=1` —— 已复盘过还要重跑。只认 POST 上的查询参数（本路由本身是 POST）。"""
    return str(request.query_params.get("force", "")).strip().lower() in ("1", "true", "yes")


@app.post("/api/review/run")
def api_run(request: Request, date: str | None = None):
    if getattr(app.state, "review_agent", None) is not None:
        return JSONResponse({"error": "生成入口已升级，请刷新页面并使用已保存的复盘 AI 来源"}, status_code=409)
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    try:
        explicit = bool(date)
        if not explicit:
            # 复盘的对象只能是**已经收盘的那一场**。原来这里在工作日直接用 today，
            # 于是盘前点一下就为「还没开盘的今天」开跑：涨停池 / 龙虎榜 / 资金流全空，
            # 四个分析师如实说"数据缺失"，裁判却照样端出三个方向 + 点名个股
            # （实测点到一只当天 -6.81% 的票当"主线代表"）。
            # 复盘系统不做当日动态分析 —— 没收盘就还是上一场那份。
            date = trade_calendar.latest_session() or china_today()
        date = validate_trade_date(date)  # #1
        if is_weekend(date):
            return JSONResponse({"error": f"{date} 为周末非交易日"}, status_code=400)
        if not trade_calendar.is_settled(date):
            latest = trade_calendar.latest_session()
            return JSONResponse(
                {"error": f"{date} 还没收盘，复盘要用当天的收盘数据",
                 "suggest_date": latest,
                 "hint": f"最近已收盘的是 {latest}" if latest else None},
                status_code=409)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    # 那一场已经复盘过就别重跑 —— 直接告诉前端去看那天的页面。
    # 想重跑：带 force=1（改了口径/修了 bug 时才需要）。
    if not _force_flag(request) and review_store.usable(review_store.load(date)):
        return {"running": False, "date": date, "already_done": True,
                "message": f"{date} 已复盘"}

    with _lock:  # 原子 check-then-act（#5）
        if _job["running"] and not _job_stuck(_job, _JOB_TIMEOUT):
            return {"running": True, "date": _job["date"]}
        if _job["running"]:   # 卡死的旧任务：让位，并把原因如实写进 error
            _job["error"] = f"上一个任务（{_job.get('date')}）超过 {_JOB_TIMEOUT // 60} 分钟无响应，已判为卡死"
        job_id = uuid.uuid4().hex
        _job.update(running=True, job_id=job_id, date=date, error=None,
                    started=time.time(), elapsed=0, finished_at=None)
    threading.Thread(target=_run_review, args=(date, job_id), daemon=True).start()
    return {"running": True, "date": date, "job_id": job_id}


@app.get("/api/cli/available")
def api_cli_available():
    """哪些订阅 CLI 真的能用 —— **服务端是唯一权威**，UI 照这个渲染"""
    live = sys.modules.get("vr_app")
    find_bin = getattr(getattr(live, "cli_runtime", None), "_find_bin", None)

    out = []
    for kind, bins in sorted(_ALL_CLI_BINS.items()):
        allowed = kind in _ALLOWED_CLI_KINDS
        # 被摘掉的 kind 用不了 `detect_cli`（它读的就是被摘空的那个 dict），
        # 所以直接拿摘除前记下的可执行名去找。
        installed = bool(find_bin and any(find_bin(b) for b in bins))
        out.append({
            "kind": kind,
            "allowed": allowed,
            "installed": installed,
            "reason": None if allowed else "自动批准 / 无沙箱，默认禁用",
        })
    return {
        "clis": out,
        # 放开的办法，直接告诉调用方，不用去翻文档
        "optInEnv": "VIBE_ALLOW_UNSAFE_CLI",
        "optedIn": sorted(_opted_in_clis()),
    }


@app.get("/api/review/status")
def api_status():
    with _lock:
        snap = dict(_job)
    if snap["running"] and snap["started"]:
        snap["elapsed"] = int(time.time() - snap["started"])
    snap.pop("started", None)
    return snap


@app.post("/api/review/evaluate")
def api_evaluate(request: Request, date: str):
    """手动评估某天的『明天关注点』命中情况（次一交易日实际涨跌）"""
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    try:
        date = validate_trade_date(date)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    res = reflection.evaluate(date)
    return res or {"error": "无可评估数据（缺该日预测，或次一交易日数据尚未出）"}


@app.get("/api/market/session")
def api_market_session():
    """此刻的「实时行情」到底属于哪一场。

    腾讯 / 东财的实时接口在盘前返回的是**上一场的收盘**，而且不带任何提示。
    界面若按「今天」给它们贴日期，盘前打开就会看到「今天 上证 +0.4%」——
    今天还没开盘，这个数是昨收。数字没错，标签错了，而这种错最容易让人
    对整块数据失去信任。所以把「哪一场」当成一等公民返回，由 UI 如实标注。
    """
    today = china_today()
    quotes_of = trade_calendar.quote_trade_day()
    is_today = bool(quotes_of) and quotes_of == today
    now = china_now()
    phase = trade_calendar.session_phase(now, quotes_of)
    return {"now": now.strftime("%Y-%m-%d %H:%M"), "today": today,
            "quotes_of": quotes_of, "is_today": is_today, **phase}


@app.get("/api/market/live-emotion")
def api_market_live_emotion():
    """今日**实时**打板情绪（盘面数据页用）。

    与 `vr/` 的 `/api/market/emotion` 并存：那条被锁在已收盘那一场（复盘口径），
    这条要的就是今天、随盘变化。两个块在界面上分别标清是哪一场。
    """
    return live_emotion.snapshot()


@app.get("/api/market/overseas")
def api_market_overseas():
    """隔夜外围：美港股指数 + 美股七姐妹，各自带交易日。

    与 `vr/` 的 `/api/global/indices` 并存 —— 那条不回行情时间，界面无法说清
    「这批数是哪一场的」（详见 duanxian/overseas.py 顶部）。
    """
    return overseas.overseas_snapshot()


@app.get("/api/review/expected-date")
def api_review_expected_date():
    """复盘页默认该看哪一场：① 日历 → ② 库里实际有数的那场，取②（与首板/情绪同一函数）。

    ⚠️ 这里以前只回日历那一层，于是周六的页面写着「最近已收盘交易日为
    2026-09-24」，点「查看这一天」就落进一场库里没有数的日子 —— 生成的复盘整块证据是空的，
    用户看到的正是"昨日强势股反馈这个模块没了"。缺口用 `note` 交出去，不静默换日子。
    """
    date, note = trade_calendar.latest_data_session()
    return {"date": date, "note": note}


_capture_retry_lock = threading.Lock()


@app.post("/api/review/capture")
def api_review_capture(date: str):
    try:
        date = validate_trade_date(date)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not review_store.usable(review_store.load(date)):
        return JSONResponse({"error": "所选日没有有效报告，不能补归档"}, status_code=400)
    if not _capture_retry_lock.acquire(blocking=False):
        return JSONResponse({"error": "归档正在处理，请稍后查看"}, status_code=409)
    try:
        from review_agent.post_review import capture_bounded
        results = capture_bounded(date)
        return {"ok": all(r.get("ok") for r in results.values()), "results": results}
    finally:
        _capture_retry_lock.release()


@app.get("/api/review/dates")
def api_review_dates():
    """跑过复盘的交易日，新→旧。给看板的历史入口用。

    在此之前 `reviews/` 每天存一份、但只有 `latest.json` 有接口 ——
    对一个复盘产品来说「翻回上周三看看」是刚需，却没有入口。
    """
    return {"dates": review_store.dates()}


@app.get("/api/review/latest")
def api_latest(date: Optional[str] = None):
    """读复盘：不带 date 读最近一份；带 date 读那天的历史存档。"""
    if date:
        try:
            date = validate_trade_date(date)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    payload = review_store.load(date)   # 损坏/不存在都返回 None（不 500）
    if payload is None or not review_store.usable(payload):
        # 空对象 = 「这天没有」，前端据此显示"还没跑过"；带上 date 好让前端知道问的是哪天
        return JSONResponse({"requested_date": date} if date else {}, status_code=200)
    try:
        anchor = payload.get("target_date") or payload.get("trade_date")
        payload["reflection"] = reflection.latest_reflection(end=anchor)
        payload["scoreboard"] = reflection.scoreboard(end=anchor)
    except Exception as exc:  # noqa: BLE001  战绩算不出来不该让整个复盘打不开
        print(f"⚠️ 战绩统计失败：{type(exc).__name__}: {exc}")
    return JSONResponse(payload)


# ==================== ③ 近 5 天热度 ====================
_wk_lock = threading.Lock()


def _wk_load():
    path = os.path.join(_WK_DIR, "latest.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return None


def _wk_fresh(cached: dict) -> bool:
    """缓存是否仍是最新已收盘交易日窗口（ #3）。判不了(网络失败)→按新鲜处理不硬刷"""
    from duanxian.weekly import LINEAGE_SCHEMA

    days = cached.get("days") or []
    if not days:
        return False
    if cached.get("lineage_schema") != LINEAGE_SCHEMA:
        return False
    try:
        from duanxian.weekly import _last_trade_dates

        latest = _last_trade_dates(1)
        expected = latest[-1] if latest else None
    except Exception:  # noqa: BLE001
        expected = None
    return expected is None or days[-1].get("date") == expected


def _wk_good(w: dict) -> bool:
    """结果够格替换有效缓存吗（ #4：至少有一天真实取到数）"""
    return not w.get("error") and any((d.get("limit_up") is not None) for d in (w.get("days") or []))


@app.get("/api/weekly")
def api_weekly(request: Request, refresh: int = 0):
    """近 5 交易日热度 + 龙头谱系。缓存按交易日过期；单飞锁防并发重复计算（~40s）"""
    return _weekly(force=False)


@app.post("/api/weekly/refresh")
def api_weekly_refresh(request: Request):
    """强制重算近 5 天热度。写操作走 POST + Origin 校验（见 api_weekly 的说明）。"""
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    return _weekly(force=True)


def _weekly(force: bool):
    """近 5 天热度的真正实现。`force` **只能由 POST 处理器传入**，不接受查询参数。"""
    from duanxian.weekly import build_weekly

    cached = _wk_load()
    if not force and cached and _wk_fresh(cached):
        return JSONResponse(cached)

    if not _wk_lock.acquire(blocking=False):
        if cached:
            return JSONResponse({**cached, "busy": True})
        return JSONResponse({"error": "正在计算中，请稍后重试", "busy": True}, status_code=409)
    try:
        cached2 = _wk_load()
        if not force and cached2 and _wk_fresh(cached2):
            return JSONResponse(cached2)
        w = build_weekly(5)
        w["generated_at"] = china_now().strftime("%Y-%m-%d %H:%M")
        days = w.get("days") or []
        w["last_trade_date"] = days[-1].get("date") if days else None
        if _wk_good(w):
            try:
                import hashlib
                w["input_revision"] = hashlib.sha256(json.dumps(w["days"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                revision = hashlib.sha256(json.dumps({k:v for k,v in w.items() if k != "generated_at"}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                w["revision"] = revision
                if cached2:
                    prior_id = hashlib.sha256(json.dumps(cached2, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                    prior_path = safe_join(_WK_DIR, "prior-" + prior_id + ".json")
                    if cached2.get("revision") != revision and not os.path.exists(prior_path):
                        _atomic_write(prior_path, cached2)
                    w["revised_days"] = [d["date"] for d in w["days"] for old in cached2.get("days", [])
                                         if d["date"] == old.get("date") and any(d.get(k) != old.get(k)
                                         for k in ("limit_up", "highest_consec", "broken_rate", "leaders") if k in old)]
                archive_path = safe_join(_WK_DIR, "revision-" + revision + ".json")
                if not os.path.exists(archive_path):
                    _atomic_write(archive_path, w)
                _atomic_write(safe_join(_WK_DIR, "latest.json"), w)
            except Exception:  # noqa: BLE001
                w.setdefault("warnings", []).append("本次数据已取得，但版本归档失败；未确认保存成功")
            return JSONResponse(w)
        if cached2 and (cached2.get("days")):
            stale = dict(cached2)
            stale["stale"] = True
            stale["warnings"] = (stale.get("warnings") or []) + ["刷新失败，展示上一份有效数据"]
            return JSONResponse(stale)
        return JSONResponse(w)
    finally:
        _wk_lock.release()


_chat_llm = None
_ROLE_MAP = {"user": "human", "assistant": "ai"}

# 对话体量上限。本机其它进程也能打这个口，不设限就能构造超长请求把 LLM 额度烧光。
_CHAT_MAX_MESSAGES = 40       # 超出部分只保留最近的（_chat 内还会再截到 12 轮）
_CHAT_MAX_CHARS_EACH = 4000
_CHAT_MAX_CHARS_TOTAL = 24000


def _sanitize_messages(msgs: object) -> tuple[list, Optional[str]]:
    """校验并裁剪对话消息。返回 (清洗后的消息, 错误信息)。"""
    if not isinstance(msgs, list) or not msgs:
        return [], "空消息"
    if len(msgs) > _CHAT_MAX_MESSAGES:
        msgs = msgs[-_CHAT_MAX_MESSAGES:]
    out, total = [], 0
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "user")
        if role not in _ROLE_MAP:
            return [], f"不支持的 role：{role!r}（只接受 user / assistant）"
        content = str(m.get("content", ""))[:_CHAT_MAX_CHARS_EACH]
        total += len(content)
        if total > _CHAT_MAX_CHARS_TOTAL:
            return [], "对话内容过长，请开新会话"
        out.append({"role": role, "content": content})
    return (out, None) if out else ([], "空消息")


def _chat_model():
    global _chat_llm
    if _chat_llm is None:
        _chat_llm = make_llm(deep=False)
    return _chat_llm


def _strip_html(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def _load_latest_json(dirpath: str) -> dict:
    path = os.path.join(dirpath, "latest.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return {}


def _review_context() -> str:
    d = _load_latest_json(_REVIEW_DIR)
    if not d:
        return "（暂无复盘数据，请先在复盘 Agent 生成一次复盘。）"
    parts = [f"复盘交易日 {d.get('target_date', '')}", f"【明天关注点】\n{d.get('focus_md', '')}"]
    if d.get("macro_sector"):
        parts.append(f"【大板块本周】\n{d['macro_sector']}")
    for a in d.get("analysts", []):
        parts.append(f"【{a.get('title', '')}】\n{_strip_html(a.get('html', ''))}")
    return "\n\n".join(parts)[:8000]
def _chat(context: str, role_desc: str, messages: list) -> dict:
    system = (
        f"你是{role_desc}。下面是刚才多 agent 产出的结论与数据，用户会就它追问或让你展开。\n"
        f"{PACK.chat_guidance}\n"
        "不要提及你自己的身份或模型名。\n\n" + context
    )
    try:
        chain = [("system", system)] + [
            (_ROLE_MAP.get(m["role"], "human"), m["content"])
            for m in messages[-12:]
        ]
        ans = _chat_model().invoke(chain).content
        return {"answer": strip_model_noise(ans)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


@app.post("/api/chat")
def api_legacy_chat():
    return JSONResponse({"detail": "问答入口已升级，请刷新页面并使用统一 AI 接入"}, status_code=409)


@app.post("/api/review/chat")
def api_review_chat(request: Request, body: dict = Body(...)):
    return JSONResponse({"error": "问答入口已升级，请刷新页面并使用统一 AI 接入"}, status_code=409)


def _legacy_api_review_chat(request: Request, body: dict):
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    msgs, err = _sanitize_messages(body.get("messages"))
    if err:
        return JSONResponse({"error": err}, status_code=400)
    return _chat(_review_context(), "A 股短线复盘助手", msgs)


def index():
    dist_index = os.path.join(_DIST, "index.html")
    if os.path.isfile(dist_index):
        return FileResponse(dist_index)
    return JSONResponse(
        {"error": "未找到前端构建产物，请先执行：cd frontend && npm install && npm run build"},
        status_code=503,
    )


@app.get("/api/verification/menu")
def api_verify_menu(date: Optional[str] = None):
    """可选指标清单（前端下拉用）。"""
    from duanxian.verification import DIRECTIONS, METRICS

    if date:
        try:
            date = validate_trade_date(date)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    report = review_store.load(date) or {}
    readings = {r.get("metric"): r.get("available") for r in (report.get("report_grounding") or {}).get("records", []) if r.get("kind") == "metric"}
    return JSONResponse({
        "directions": DIRECTIONS,
        "metrics": [{"key": m.key, "label": m.label, "hint": m.hint,
                     "unit": m.unit, "higher_is_hotter": m.higher_is_hotter, "available": readings.get(m.key)}
                    for m in METRICS],
    })


@app.get("/api/verification/items")
def api_verify_items(date: str):
    from duanxian import verification

    try:
        date = validate_trade_date(date)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"date": date, "items": verification.load_user_items(date)})


@app.post("/api/verification/items")
def api_verify_save(request: Request, date: str, body: dict = Body(...)):
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    from duanxian import verification

    try:
        date = validate_trade_date(date)
        return JSONResponse(verification.save_user_items(date, body.get("items") or []))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ==================== ④ 交易日志与模式卡 ====================
# ⛔ 本段的数据全部是使用者自己录入的个人交易记录，**不接入任何 AI prompt**。


@app.get("/api/journal/list")
def api_journal_list(limit: int = 200, offset: int = 0):
    from duanxian import journal

    try:
        return JSONResponse(journal.list_trades(max(1, min(limit, 1000)), max(0, offset)))
    except journal.JournalCorrupted as exc:
        # ⚠️ 账本损坏必须报 500 并说明原因，绝不返回空表 —— 空表会被读成
        #    "记录丢了"，而下一次写入就真的把它覆盖掉了。
        return JSONResponse({"error": str(exc), "corrupted": True}, status_code=500)


@app.get("/api/journal/stats")
def api_journal_stats():
    from duanxian import journal

    try:
        return JSONResponse(journal.stats())
    except journal.JournalCorrupted as exc:
        return JSONResponse({"error": str(exc), "corrupted": True}, status_code=500)


@app.post("/api/journal/add")
def api_journal_add(request: Request, body: dict = Body(...)):
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    from duanxian import journal

    try:
        pnl = body.get("pnl_pct")
        return JSONResponse(journal.add_trade(
            date=str(body.get("date") or "").strip(),
            code=str(body.get("code") or "").strip(),
            name=str(body.get("name") or "").strip(),
            playbook=str(body.get("playbook") or "其它").strip(),
            pnl_pct=None if pnl in (None, "") else float(pnl),
            as_planned=body.get("as_planned"),
            note=str(body.get("note") or ""),
            # ⚠️ 每个字段都必须透传。漏掉 fills 会让界面上填的成交明细被静默丢弃，
            #    且前端不报错 —— 而加权成本、已实现盈亏、持有天数全靠它算。
            fills=body.get("fills") or [],
            planned_stop=body.get("planned_stop"),
            planned_target=body.get("planned_target"),
        ))
    except (ValueError, TypeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except journal.JournalCorrupted as exc:
        return JSONResponse({"error": str(exc), "corrupted": True}, status_code=500)
    except RuntimeError as exc:      # 写盘失败 —— 必须让用户知道这一笔没记上
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/journal/update")
def api_journal_update(request: Request, trade_id: str, body: dict = Body(...)):
    """更新一笔已有交易 —— 主要用来给持仓中的记录**补上后来的成交**。

    只处理请求里出现的字段，其余保持不变。没有这个接口时，补一笔卖出只能
    删掉重录，`created_at` 与"下单时写下的计划边界"这个证据就一起没了。
    """
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    from duanxian import journal

    kw = {}
    if "fills" in body:
        kw["fills"] = body.get("fills") or []
    if "note" in body:
        kw["note"] = str(body.get("note") or "")
    for k in ("as_planned", "planned_stop", "planned_target"):
        if k in body:                      # ⚠️ 用 in 判断：没传 = 不动，传了 null = 清空
            kw[k] = body[k]
    try:
        r = journal.update_trade(trade_id, **kw)
        if not r.get("ok"):
            return JSONResponse({"error": r.get("reason") or "更新失败"}, status_code=404)
        return JSONResponse(r)
    except (ValueError, TypeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except journal.JournalCorrupted as exc:
        return JSONResponse({"error": str(exc), "corrupted": True}, status_code=500)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/positions")
def api_positions():
    """当前持仓 —— 从交易日志的成交明细聚合，**不另存一份账**。

    ⚠️ 与 `vr` 的 `/api/portfolio` 是两套不同的账：那一套自己存 holdings，
    需要重复录入且和日志对不上。新界面一律走这个口，`/api/portfolio` 保留
    只为兼容旧数据（`vr/` 是已做本地适配的兼容层，来源与改动见开发日志）。
    """
    from duanxian import journal, positions

    try:
        return JSONResponse(positions.report())
    except journal.JournalCorrupted as exc:
        return JSONResponse({"error": str(exc), "corrupted": True}, status_code=500)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"available": False,
                             "reason": f"{type(exc).__name__}: {exc}"}, status_code=200)


@app.post("/api/positions/import-legacy")
def api_positions_import(request: Request, body: dict | None = Body(None)):
    """把旧的 `vr` 持仓（`~/.vibe-research/portfolio.json`）一次性导入交易日志。

    ⚠️ 只导**当前持仓**，每条建成一笔"只有买入、尚未卖出"的交易。
    旧记录里没有成交日期，用 `date` 字段或今天，并在备注里注明是导入的 ——
    不伪造一个看起来精确的建仓日。已导过的（同代码同股数同成本）跳过，可重复调用。
    """
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    from duanxian import journal, positions

    if body and "holdings" not in body:
        return JSONResponse({"error": "导入文件缺少 holdings"}, status_code=400)
    if body and "holdings" in body:
        legacy = body["holdings"]
        if not isinstance(legacy, list) or len(legacy) > 1000:
            return JSONResponse({"error": "请选择最多1000条持仓的 JSON 文件"}, status_code=400)
        # Validate the whole selected file before writing even one journal item.
        import math
        try:
            for h in legacy:
                if not isinstance(h, dict) or not re.fullmatch(r"[0-9]{6}", str(h.get("code", ""))):
                    raise ValueError()
                validate_trade_date(str(h.get("date") or "").strip() or china_today())
                for name in ("shares", "cost"):
                    v = float(h[name])
                    if isinstance(h[name], bool) or not math.isfinite(v) or not 0 < v < 1e12 or round(v, 2 if name == "shares" else 4) <= 0:
                        raise ValueError()
        except (KeyError, ValueError, TypeError, OverflowError):
            return JSONResponse({"error": "持仓文件格式不正确，尚未导入任何记录"}, status_code=400)
    else:
        try:
            import portfolio as vr_pf
            legacy = (vr_pf._load() or {}).get("holdings") or []
        except Exception:
            return JSONResponse({"error": "读不到旧持仓；可选择原 portfolio.json 文件导入"}, status_code=500)
    if not legacy:
        return JSONResponse({"ok": True, "imported": 0, "skipped": 0,
                             "message": "旧持仓是空的，没有需要导入的内容"})

    existing = set() if body and "holdings" in body else {(p["code"], p["shares"], p["cost"]) for p in positions.open_positions()}
    imported = skipped = 0
    errors = []
    for h in legacy:
        code = str(h.get("code") or "").zfill(6)
        shares, cost = float(h.get("shares") or 0), float(h.get("cost") or 0)
        if not code or shares <= 0 or cost <= 0:
            errors.append(f"{code or '(空代码)'}：股数或成本不可用，跳过")
            continue
        if (code, round(shares, 2), round(cost, 4)) in existing:
            skipped += 1
            continue
        day = str(h.get("date") or "").strip() or china_today()
        try:
            import hashlib
            identity = hashlib.sha256(json.dumps([code, round(shares, 2), round(cost, 4), str(h.get("date") or "").strip()], ensure_ascii=True).encode()).hexdigest()
            saved = journal.add_trade(
                import_id=identity, date=day, code=code, name=str(h.get("name") or ""), playbook="其它",
                note="由旧持仓导入（原记录没有成交明细，建仓日期可能不准）",
                fills=[{"side": "buy", "date": day, "price": cost, "shares": shares}])
            if saved and saved.get("skipped"):
                skipped += 1
                continue
            imported += 1
            existing.add((code, round(shares, 2), round(cost, 4)))
        except (ValueError, TypeError) as exc:
            errors.append(f"{code}：{exc}")
    return JSONResponse({"ok": True, "imported": imported, "skipped": skipped,
                         "errors": errors})


@app.get("/api/journal/fees")
def api_journal_fees():
    from duanxian import journal

    try:
        return JSONResponse({"fees": journal.load_fees(), "labels": journal._FEE_LABELS,
                             "defaults": journal.DEFAULT_FEES})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/journal/fees-schema")
def api_journal_fees_schema():
    from duanxian import journal
    return JSONResponse({"labels": journal._FEE_LABELS, "defaults": journal.DEFAULT_FEES})


@app.post("/api/journal/fees")
def api_journal_save_fees(request: Request, body: dict = Body(...)):
    """保存**使用者自己的**费率。默认值只是能跑起来的初值，不是推荐值。"""
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    from duanxian import journal

    try:
        fees = body.get("fees")
        if not isinstance(fees, dict) or any(k not in fees or fees[k] in (None, "") for k in journal.DEFAULT_FEES):
            raise ValueError("请填写全部费率项目后保存，缺项不会自动使用初值")
        return JSONResponse(journal.save_fees(fees))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/journal/delete")
def api_journal_delete(request: Request, trade_id: str):
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    from duanxian import journal

    try:
        return JSONResponse(journal.delete_trade(trade_id))
    except (journal.JournalCorrupted, RuntimeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/modes")
def api_modes():
    """模式卡列表 + 按版本分段的业绩。"""
    from duanxian import modes

    try:
        return JSONResponse({**modes.list_cards(), "performance": modes.performance()})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


@app.post("/api/modes")
def api_save_mode(request: Request, body: dict = Body(...)):
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    from duanxian import modes

    try:
        return JSONResponse(modes.save_card(body or {}))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/modes/delete")
def api_delete_mode(request: Request, body: dict = Body(...)):
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    from duanxian import modes

    try:
        return JSONResponse(modes.delete_card(str(body.get("id") or "")))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ==================== ⑤ 账户风险与执行偏差 ====================
# ⛔ 与 ④ 同：这一段读的全是使用者自己录入的交易数据，**不接入任何 AI prompt**。


@app.get("/api/risk/report")
def api_risk_report():
    from duanxian import journal, risk

    try:
        return JSONResponse(risk.report())
    except journal.JournalCorrupted as exc:
        return JSONResponse({"error": str(exc), "corrupted": True}, status_code=500)

    except ValueError as exc:
        return JSONResponse({"error": str(exc), "corrupted": True}, status_code=500)

@app.get("/api/risk/attribution")
def api_risk_attribution():
    """判断 vs 执行归因四格。看对了却亏钱 = 执行问题；看错了还赚钱 = 运气。"""
    from duanxian import attribution, journal

    try:
        return JSONResponse(attribution.attribution())
    except journal.JournalCorrupted as exc:
        return JSONResponse({"error": str(exc), "corrupted": True}, status_code=500)


@app.get("/api/risk/excursion")
def api_risk_excursion():
    """MFE/MAE 与盈利回吐。

    ⚠️ 逐笔要拉历史行情（已永久缓存，首次会慢）→ **单独端点，不塞进 /risk/report**，
    否则每次打开交易日志页都要干等一轮网络。
    """
    from duanxian import excursion, journal

    try:
        return JSONResponse(excursion.summary())
    except journal.JournalCorrupted as exc:
        return JSONResponse({"error": str(exc), "corrupted": True}, status_code=500)


@app.get("/api/risk/at-risk")
def api_at_risk():
    """在险资金：现在这些仓位最坏会亏掉多少（按用户自己写下的计划止损算）。"""
    from duanxian import at_risk, journal

    try:
        return JSONResponse(at_risk.report())
    except journal.JournalCorrupted as exc:
        return JSONResponse({"error": str(exc), "corrupted": True}, status_code=500)

    except ValueError as exc:
        return JSONResponse({"error": str(exc), "corrupted": True}, status_code=500)

@app.get("/api/risk/equity-base")
def api_get_equity_base():
    from duanxian import at_risk

    try:
        return JSONResponse({"equity_base": at_risk.load_equity_base()})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/risk/equity-base")
def api_set_equity_base(request: Request, body: dict = Body(...)):
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    from duanxian import at_risk

    try:
        return JSONResponse(at_risk.save_equity_base(body.get("equity_base")))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/risk/inbox")
def api_inbox():
    """异常交易收件箱：值得回头看一眼的那几笔（判定基准全来自用户自己）。"""
    from duanxian import inbox, journal

    try:
        return JSONResponse(inbox.build())
    except journal.JournalCorrupted as exc:
        return JSONResponse({"error": str(exc), "corrupted": True}, status_code=500)


@app.get("/api/risk/rules-schema")
def api_risk_rules_schema():
    """Read-only form metadata, available even when the saved file is damaged."""
    from duanxian import risk
    return JSONResponse({"rules": {}, "labels": risk._RULE_LABELS, "defaults": risk.DEFAULT_RULES})


@app.get("/api/risk/rules")
def api_risk_rules():
    from duanxian import risk

    try:
        return JSONResponse({"rules": risk.load_rules(), "labels": risk._RULE_LABELS,
                             "defaults": risk.DEFAULT_RULES})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/risk/rules")
def api_risk_save_rules(request: Request, body: dict = Body(...)):
    """保存**用户自己的**风险宪法。系统只监控他有没有违反自己定的规矩。"""
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    from duanxian import risk

    try:
        return JSONResponse(risk.save_rules(body.get("rules") or {}))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ==================== ⑥ 原始数据归档 · 结构漂移 · 策略回测 ====================

@app.get("/api/archive/summary")
def api_archive_summary():
    """原始数据归档总览：囤了多久、多大、字段有没有漂移过。"""
    from duanxian import archive

    result = archive.summary()
    result.update(archive.coverage_status(result.get("date_to"), trade_calendar.latest_session()))
    return JSONResponse(result)




# ========== 第二段：个人交易日志 ==========

# ==================== 个人交易日志 ====================
# ⚠️ 只统计用户自己录入的历史行为，**不产出任何下一笔建议**。
# 它是记账本 + 体检报告，不是参谋：不选股、不给买卖时机、不给参与倾向。


@app.get("/api/drift")
def api_drift():
    """结构漂移：数据源字段 + 市场结构 + 已登记的制度事件。"""
    from duanxian import drift

    from duanxian import archive
    result = drift.report()
    dates = [d.get("date_to") for d in result.get("field_drift", {}).values() if d.get("date_to")]
    result.update(archive.coverage_status(max(dates) if dates else None, trade_calendar.latest_session()))
    return JSONResponse(result)


@app.get("/api/drift/calendar")
def api_get_regime_calendar():
    from duanxian import drift

    return JSONResponse({"events": drift.load_calendar()})


@app.post("/api/drift/calendar")
def api_save_regime_calendar(request: Request, body: dict = Body(...)):
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    from duanxian import drift

    try:
        return JSONResponse(drift.save_calendar(body.get("events") or []))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------- 策略回测
_bt_lock = threading.Lock()


def _bt_path(days: int) -> str:
    """回测结果缓存路径 —— 与 backtest.prior_context 读的是同一份，别两处各写一份。"""
    from duanxian.backtest import result_path

    return result_path(days)


def _bt_load(days: int) -> Optional[dict]:
    """读回测缓存。走 backtest.load_result —— 它会连**策略集是否变过**一起校验。"""
    from duanxian.backtest import load_result

    return load_result(days)


def _bt_fresh(cached: dict) -> bool:
    """缓存是否覆盖到最新已收盘交易日。判不了（网络失败）→ 按新鲜处理，不硬刷。

    ⚠️ 必须用 `latest_session()` 而不是裸的 `last_trade_dates(1)`：腾讯 hist 收盘后
    有延迟，后者会返回上一个交易日，导致当天的缓存永远判成过期、每次请求重算。
    """
    try:
        from duanxian.trade_calendar import latest_session

        expected = latest_session()
    except Exception:  # noqa: BLE001
        expected = None
    return expected is None or cached.get("date_to") == expected


@app.get("/api/backtest")
def api_backtest(request: Request, days: int = 60, refresh: int = 0):
    """短线策略回测。首次约 1-2 分钟（逐日取数），历史结果落盘缓存后很快。

    ⚠️ 产出是「规则的历史统计」，不是前瞻标的。
    ⚠️ 强制刷新走 POST `/api/backtest/refresh`，GET 携带 refresh 会返回 405，避免链接或预加载触发重算。
    """
    from duanxian.backtest import run_backtest

    if refresh and not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    if refresh and request.method != "POST":
        return JSONResponse({"error": "强制刷新请使用 POST /api/backtest/refresh"}, status_code=405)
    force = bool(refresh) and request.method == "POST" and _origin_ok(request)

    days = max(10, min(int(days or 60), 120))   # 夹在合理区间，防误传
    cached = _bt_load(days)
    if not force and cached and cached.get("available") and _bt_fresh(cached):
        return JSONResponse(cached)

    # 非阻塞取锁：正在算就回缓存 + busy，不让请求线程干等一两分钟
    if not _bt_lock.acquire(blocking=False):
        if cached and cached.get("available"):
            return JSONResponse({**cached, "busy": True})
        return JSONResponse({"error": "正在回测中，请稍后重试", "busy": True}, status_code=409)
    try:
        cached2 = _bt_load(days)
        if not force and cached2 and cached2.get("available") and _bt_fresh(cached2):
            return JSONResponse(cached2)
        bt = run_backtest(days)
        bt["generated_at"] = china_now().strftime("%Y-%m-%d %H:%M")
        if bt.get("available"):
            try:
                _atomic_write(_bt_path(days), bt)
            except Exception as exc:  # noqa: BLE001  写缓存失败不该拖垮响应，但必须出声
                print(f"⚠️ 回测缓存写入失败（每次请求都会重算）：{type(exc).__name__}: {exc}")
            return JSONResponse(bt)
        # 算失败：不覆盖有效缓存，尽量返回上一份 + stale 标记
        if cached2 and cached2.get("available"):
            stale = dict(cached2)
            stale["stale"] = True
            stale["warnings"] = (stale.get("warnings") or []) + ["刷新失败，展示上一份有效数据"]
            return JSONResponse(stale)
        return JSONResponse(bt)
    finally:
        _bt_lock.release()


@app.post("/api/backtest/refresh")
def api_backtest_refresh(request: Request, days: int = 60):
    """强制重跑回测。写操作走 POST + Origin 校验（见 api_weekly 的说明）。"""
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    return api_backtest(request, days=days, refresh=1)


# ==================== 主线 B：个股深挖 ====================
from duanxian.deepdive.store import serialize as _serialize_dd


def _run_dd(stock: str, job_id: str) -> None:
    error = None
    try:
        final = deepdive_run(stock)
        if final.get("error"):
            error = final["error"]
        else:
            payload = _serialize_dd(final)
            _atomic_write(safe_join(_DD_DIR, "latest.json"), payload)
            if payload.get("code"):
                _atomic_write(safe_join(_DD_DIR, f"{payload['code']}.json"), payload)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    finally:
        with _lock:
            if _dd_job["job_id"] == job_id:
                _dd_job["running"] = False
                _dd_job["error"] = error
                if _dd_job["started"]:
                    _dd_job["elapsed"] = int(time.time() - _dd_job["started"])
                _dd_job["finished_at"] = china_now().strftime("%Y-%m-%d %H:%M:%S") + " CST"


@app.post("/api/deepdive/run")
def api_dd_run(request: Request, stock: str):
    return JSONResponse({"error": "深挖入口已升级，请刷新页面并使用统一 AI 接入后发起"}, status_code=409)


def _legacy_dd_run(request: Request, stock: str):
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    stock = (stock or "").strip()
    if not stock:
        return JSONResponse({"error": "缺 stock 参数（6 位代码或简称）"}, status_code=400)
    with _lock:
        if _dd_job["running"] and not _job_stuck(_dd_job, _DD_TIMEOUT):
            # 繁忙：明确告知当前占用的是哪只票，前端据此不误加载别人的结果
            return {"running": True, "busy": True, "stock": _dd_job["stock"]}
        if _dd_job["running"]:   # 卡死的旧任务让位（同 api_run，见 _job_stuck）
            _dd_job["error"] = (f"上一次深挖（{_dd_job.get('stock')}）超过 "
                                f"{_DD_TIMEOUT // 60} 分钟无响应，已判为卡死")
        job_id = uuid.uuid4().hex
        _dd_job.update(running=True, job_id=job_id, stock=stock, error=None,
                       started=time.time(), elapsed=0, finished_at=None)
    threading.Thread(target=_run_dd, args=(stock, job_id), daemon=True).start()
    return {"running": True, "busy": False, "stock": stock, "job_id": job_id}


@app.get("/api/deepdive/status")
def api_dd_status():
    with _lock:
        snap = dict(_dd_job)
    if snap["running"] and snap["started"]:
        snap["elapsed"] = int(time.time() - snap["started"])
    snap.pop("started", None)
    return snap


@app.get("/api/deepdive/latest")
def api_dd_latest():
    path = os.path.join(_DD_DIR, "latest.json")
    if not os.path.exists(path):
        return JSONResponse({}, status_code=200)
    try:
        with open(path, encoding="utf-8") as fh:
            return JSONResponse(json.load(fh))
    except (json.JSONDecodeError, OSError):
        return JSONResponse({}, status_code=200)


def _deepdive_context() -> str:
    d = _load_latest_json(_DD_DIR)
    if not d:
        return "（暂无个股深挖数据，请先在个股深挖 Agent 深挖一只票。）"
    parts = [f"标的 {d.get('name', '')}（{d.get('code', '')}）", f"【深挖结论】\n{d.get('verdict_md', '')}"]
    r = d.get("reports", {}) or {}
    for k, t in [("theme", "题材归属"), ("capital", "资金流向"), ("technical", "技术形态"), ("risk", "风险排查")]:
        if r.get(k):
            parts.append(f"【{t}】\n{_strip_html(r[k])}")
    return "\n\n".join(parts)[:8000]


@app.post("/api/deepdive/chat")
def api_dd_chat(request: Request, body: dict = Body(...)):
    return JSONResponse({"error": "问答入口已升级，请刷新页面并使用统一 AI 接入"}, status_code=409)


def _legacy_api_dd_chat(request: Request, body: dict):
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    msgs, err = _sanitize_messages(body.get("messages"))
    if err:
        return JSONResponse({"error": err}, status_code=400)
    return _chat(_deepdive_context(), "A 股短线个股深挖助手", msgs)


# ==================== 盘中快照 / 竞价核验 ====================
# ⚠️ 盘中快照**过了那个点就永远没有了**（拿的是实时行情），所以要后台按点自动抓，
# 不能等有人想起来点一下。⚠️ 调度一律按**上海时区**判断，不用本机时区 ——
#    机器在别的时区时，用本机时间会整天抓不到任何一个时点，而界面上看不出异样。
_intraday_thread_started = False
_intraday_start_lock = threading.Lock()


def _intraday_journal(ok: bool, detail: dict) -> None:
    """盘中快照的记账。**只有 print 不算说过**（stdout 进文件是块缓冲，页面也读不到），
    而 `intraday` 这条原先一个字都不记 —— 2026-10-06 自查表判据①点名的就是它。
    记账坏了不许带崩抓取。"""
    try:
        from duanxian import jobs
        jobs.record("intraday", ok=ok, detail=detail)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 盘中快照记账失败（抓取照跑）：{type(exc).__name__}: {exc}")


def _intraday_scheduler() -> None:
    """按 SNAPSHOT_SLOTS 定时抓盘面快照。交易日之外空转。

    ⚠️ 导入放在循环内的 try 里：放在函数开头（try 之外）的话，任一导入失败会让线程
       **当场退出**，而 `_intraday_thread_started` 已经是 True、不会再重启，
       循环里的异常日志也永远打不出来 —— 表现是"快照整天没抓，日志里一个字都没有"。
    """
    done: set[str] = set()
    last_day = None
    last_ok: bool | None = None
    last_record = 0.0
    while True:
        try:
            from duanxian import intraday
            from duanxian.util import china_now, china_today, is_weekend

            # 在册开关每轮重读（与收盘链同一套"改完即生效、不用重启"）。
            # 没有这一句时，面板上那个开关是假的：线程照抓六个时点，而人以为关了。
            if not _loop_enabled("", "intraday", _jobs_defaults("intraday")):
                if _loop_off_note("intraday"):
                    print("⏸ 盘中快照按开关暂停（`intraday.enabled=false`，改回即恢复）")
                time.sleep(60)
                continue
            _loop_off_seen.discard("intraday")
            today = china_today()
            if last_day is None:
                last_day = today
            if today != last_day:      # 跨天清账
                done.clear()
                last_day = today
            if not is_weekend(today):
                hhmm = china_now().strftime("%H:%M")
                for slot in intraday.SNAPSHOT_SLOTS:
                    key = f"{today}#{slot}"
                    # 到点后 6 分钟内补抓（进程刚起或调度稍晚也不漏）
                    if key not in done and slot <= hhmm <= _plus_minutes(slot, 6):
                        r = intraday.capture(slot, today)
                        # ⚠️ **只有成功才标完成**：无条件标记会让某个时点第一次碰上
                        #    数据源未更新或瞬时超时就被永久放弃，白白浪费后面的重试窗口。
                        ok = bool(r.get("ok"))
                        if ok:
                            done.add(key)
                        elif r.get("skipped"):
                            # 未开市/补不了历史：按口径不抓，不是这个作业坏了。
                            # 标完成是为了这一天的这个时点不再空转重试 6 分钟。
                            done.add(key)
                            print(f"⏸ 盘中快照 {slot} 按口径跳过：{r.get('reason')}")
                        else:
                            print(f"⚠️ 盘中快照 {slot} 失败（窗口内会重试）：{r.get('reason')}")
                        # 翻转立刻记、稳定期最多 15 分钟一次心跳（同快讯那条判据）。
                        # `slot` 必须进 detail：一天六个时点，只记"成功/失败"分不出是哪个点缺的。
                        if _newswire_due_to_record(ok, last_ok, time.monotonic() - last_record):
                            _intraday_journal(ok, {"slot": slot, "date": str(today),
                                                   "reason": r.get("reason"),
                                                   "skipped": r.get("skipped")})
                            last_ok, last_record = ok, time.monotonic()
                        break
        except Exception as exc:  # noqa: BLE001  调度线程绝不能死
            reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            print(f"⚠️ 盘中调度异常：{reason}")
            # 异常也要记账：这条线程"活着但天天抛"和"死了"在页面上长得一样，
            # 而前者原来只在 stdout 里出现过。
            if _newswire_due_to_record(False, last_ok, time.monotonic() - last_record):
                _intraday_journal(False, {"reason": "调度循环异常：" + reason})
                last_ok, last_record = False, time.monotonic()
        time.sleep(60)


def _plus_minutes(hhmm: str, n: int) -> str:
    h, m = int(hhmm[:2]), int(hhmm[3:])
    m += n
    return f"{(h + m // 60) % 24:02d}:{m % 60:02d}"


def _minutes_since_slot(slot: str, hhmm: str) -> int:
    """现在距离收盘链时点多少分钟 —— 重试节奏表按它决定"这一档到点没有"。

    负数是合法的（心跳跑在 slot 之前），表里第一档是 0，所以那时不会被判"到点"。
    """
    def mins(s: str) -> int:
        return int(s[:2]) * 60 + int(s[3:5])
    return mins(hhmm) - mins(slot)


def _start_intraday() -> None:
    """启动盘中调度线程。幂等 —— 重复调用不会起第二个。

    ⚠️ 先探一下模块在不在再起线程：不探的话，模块缺失时线程会在后台每轮 ImportError，
       而**接口全是好的、界面也正常**，只有日志里刷错 —— 典型的"看不出来的坏"。
    """
    global _intraday_thread_started
    with _intraday_start_lock:          # 检查与置位必须在同一把锁里，否则可能起两个线程
        if _intraday_thread_started:
            return
        # ⚠️ 两种失败的异常形态**实测过，不要凭直觉写**（Python 3.12）：
        #    · 子模块不存在  → ImportError，exc.name == "duanxian"（不是 "duanxian.intraday"），
        #                      消息形如 "cannot import name 'intraday' from 'duanxian'"
        #    · 内部依赖缺失  → ModuleNotFoundError，exc.name 是**缺失的那个依赖名**
        #    照直觉写成「ModuleNotFoundError 且 name == 'duanxian.intraday'」的话，
        #    兼容分支永远命中不了，而真故障反而被当成兼容情况。
        try:
            from duanxian import intraday  # noqa: F401  只探在不在
        except ModuleNotFoundError as exc:
            # 走到这里 = intraday 在，但它依赖的东西缺了 —— 真故障，必须出声
            print(f"⚠️ 盘中调度未启动：intraday 的依赖缺失（{exc}）")
            return
        except ImportError as exc:
            if exc.name == "duanxian" and "intraday" in str(exc):
                return          # 该版本不含开盘核验，安静跳过
            print(f"⚠️ 盘中调度未启动：导入 intraday 失败（{type(exc).__name__}: {exc}）")
            return
        t = threading.Thread(target=_intraday_scheduler, daemon=True, name="intraday")
        t.start()
        _intraday_thread_started = True  # ⚠️ 只在 start() 成功之后才置位


# 快讯采集常驻轮询 —— 宿主就是本进程，不挂 Windows 计划任务、不依赖任何外部调度器。
# （FinanceTool 那条路走死过：scheduler_enabled=false + dry_run=true + 计划任务
#  LastTaskResult=267011 从未跑过一次，整套"调度"是纸面的，页面还一切正常。）
_newswire_thread_started = False
_newswire_start_lock = threading.Lock()


def _newswire_enabled() -> bool:
    """总闸 + 系统配置里那个开关，**每轮都重读**（见 `_newswire_poller`）。

    以前这里只看 `VIBE_NEWS`：系统配置面板上 `news` 那个框是假的 —— 关掉之后
    线程照旧每 30 秒出一趟网。10-07 梳理定时任务时发现，同一形状的还有
    `intraday` 与 `watchtower` 两条，三条一起接到 `_loop_enabled` 这一份读法上。
    """
    return _loop_enabled("VIBE_NEWS", "news", _jobs_defaults("news"))


def _newswire_round() -> tuple[bool, dict, float]:
    """跑一轮快讯采集，返回 `(成功?, 归因 detail, 下轮间隔秒)`。

    单独拆出来只为一件事：**这一轮能被测试**。假故障那次（体检说"最近成功停在 30 小时前"
    而 jsonl 每 30 秒在长）之所以难查，就是因为"抓、落盘、记账"三件事糊在一个死循环里，
    谁都证明不了中间哪一步没做。副作用限于抓与落盘；**记账由调用方做**，
    这样测试能同时验到"这轮成没成"和"记的归因文字对不对"。

    归因文字一律放 `reason` 键 —— `jobs.record()` 只认这一个键名（系统配置定的约定）。
    """
    ok = False
    detail: dict = {}
    gap = 30.0
    try:
        from duanxian import newswire
        res = newswire.collect_once(newswire.read_cursors())
        if res["available"]:
            out = newswire.persist(res)
            ok = bool(out.get("ok"))
            detail = {"written": out.get("written", 0),
                      "rows_in_round": len(res.get("rows") or []),
                      "reason": out.get("reason")}
            if not ok:
                print(f"⚠️ 快讯落盘失败（游标未推进，下轮重试）：{out.get('reason')}")
        else:
            detail = {"reason": res.get("reason")}
            print(f"⚠️ 快讯本轮无数据：{res.get('reason')}")
        gap = float(os.environ.get("VIBE_NEWS_INTERVAL_SECONDS", "") or newswire.POLL_SECONDS)
    except Exception as exc:  # noqa: BLE001  采集线程绝不能死
        detail = {"reason": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
        print(f"⚠️ 快讯采集异常：{detail['reason']}")
        gap = 30.0             # 拿不到配置也要有节奏，否则这里会变成忙轮询打爆对端
    return ok, detail, gap


def _newswire_due_to_record(ok: bool, last_ok, since_record: float) -> bool:
    """这一轮要不要往 jobs 状态里记：**状态翻转立刻记，稳定期最多 15 分钟一次心跳**。
    按 30 秒一轮去记，`consecutive_failures` 一天会刷到两千多，那个数字就失去意义了。"""
    return last_ok is None or ok != last_ok or since_record >= 900


def _newswire_journal(ok: bool, detail: dict) -> None:
    """把这轮的结果记进 `duanxian/jobs.py` 的状态文件（`/api/jobs`、任务调度大全、
    `manage.py doctor` 都读它）。**记账坏了不许带崩采集。**"""
    try:
        from duanxian import jobs
        jobs.record("news", ok=ok, detail=detail)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 快讯记账失败（采集照跑）：{type(exc).__name__}: {exc}")


def _newswire_poller() -> None:
    """30 秒一轮：抓 → 落盘 → 才推进游标 → 再记账。

    ⚠️ 导入放在循环内的 try 里，理由与 `_intraday_scheduler` 同一条：放函数开头的话
       导入失败会让线程当场退出，而 `_newswire_thread_started` 已置 True 不再重启，
       表现是"资讯一整天没落盘，日志里一个字都没有"。

    ⚠️ 必须往 `jobs/state.json` 记账（2026-09-28 补）：这条线程过去只写
    `newswire_state.json` 和 jsonl，而页面与体检读的是 jobs 那份 —— 于是
    "最近成功停在 30 小时前"对着一个每 30 秒都在变大的文件报了**假故障**，
    比真故障更难查，因为界面和日志互相矛盾。
    """
    last_ok: bool | None = None
    last_record = 0.0
    while True:
        if not _newswire_enabled():
            if _loop_off_note("news"):
                print("⏸ 快讯采集按开关暂停（`news.enabled=false`，改回即恢复）")
            time.sleep(30)
            continue
        _loop_off_seen.discard("news")
        ok, detail, gap = _newswire_round()
        if _newswire_due_to_record(ok, last_ok, time.monotonic() - last_record):
            _newswire_journal(ok, detail)
            last_ok = ok
            last_record = time.monotonic()
        time.sleep(max(5.0, gap))


def _start_newswire() -> None:
    """幂等 —— 重复调用不会起第二个轮询线程。"""
    global _newswire_thread_started
    if not _newswire_enabled():
        return
    with _newswire_start_lock:
        if _newswire_thread_started:
            return
        try:
            from duanxian import newswire  # noqa: F401  只探在不在
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ 快讯采集未启动：newswire 导入失败（{type(exc).__name__}: {exc}）")
            return
        threading.Thread(target=_newswire_poller, daemon=True, name="newswire").start()
        _newswire_thread_started = True     # ⚠️ 只在 start() 成功之后才置位


_watchlist_thread_started = False
_watchlist_start_lock = threading.Lock()
WATCHLIST_DONE = threading.Event()      # 供测试与 doctor 观察"今天这一轮成没成"


def _watchlist_enabled() -> bool:
    from duanxian import jobs
    return _loop_enabled("VIBE_WATCHLIST_SYNC", "watchlist", jobs.WATCHLIST_DEFAULTS)


def _watchlist_slot() -> str:
    """时点读法只有一份（`duanxian/jobs.py::run_at`）：作业配置 `watchlist.run_at`
    > 环境变量 `WATCHLIST_SYNC_AT` > `WATCHLIST_DEFAULTS`。默认值不在这里重抄。

    ⚠️ 这条作业**故意不按交易日筛**：`config.watchlist_*` 是同花顺 ini 的镜像，
    用户周末改自选也要跟着新，否则周一早上页面是旧的。节假日限制只落在
    **成员快照**那一步（`write_snapshot` 自己查日历，非交易日拒写）。
    """
    from duanxian import jobs
    return jobs.run_at(name="watchlist", env_var="WATCHLIST_SYNC_AT",
                       defaults=jobs.WATCHLIST_DEFAULTS)[0]


def _watchlist_scheduler() -> None:
    """每天 20:40 一次：同花顺自选 ini → `config.watchlist_*`（成员快照另按日历写）。

    三条规矩全部照 `_intraday_scheduler`：**导入放循环内的 try**（否则线程当场退出且
    日志一个字都没有）、**到点后窗口内补抓**（进程刚起或稍晚也不漏）、
    **只有成功才标记完成**（写失败下一轮还会重试）。

    ⚠️ **这里故意不判交易日、也不判周末**（2026-09-28 按系统配置的②改）：
    `config.watchlist_*` 是同花顺 ini 的**镜像**，用户周末改自选也想当天跟上；
    以前用 `util.is_weekend` 一挡，周六的改动要到周一 20:40 才进库，页面整整一个周末是旧的。
    "不许把 ini 的改动漏进镜像"和"不许给成员快照写非交易日归属日"是两件事 ——
    后者由 `watchlist_sync.write_snapshot` 自己查 `reference_data.trade_calendar` 把关
    （`is_weekend` 只看周六日、节假日一律放行，当不了这道闸）。
    """
    done_day = None
    while True:
        try:
            from duanxian import util, watchlist_sync
            today = util.china_today()
            if done_day != today:
                hhmm = util.china_now().strftime("%H:%M")
                slot = _watchlist_slot()
                if slot <= hhmm <= _plus_minutes(slot, 90):     # 收盘后动作，补抓窗口给 90 分钟
                    rep = watchlist_sync.run(write=True)
                    # 记账进 `jobs/state.json`（`/api/jobs`、任务调度大全、doctor 都读它）。
                    # 归因文字用 `reason` 键 —— `jobs.record()` 只认这一个键名。
                    try:
                        from duanxian import jobs
                        jobs.record("watchlist", ok=bool(rep.get("ok")),
                                    detail={"reason": rep.get("reason"),
                                            "applied": rep.get("applied"),
                                            "snapshot_rows": (rep.get("member_snapshot") or {})
                                            .get("rows")})
                    except Exception as exc:  # noqa: BLE001  记账坏了不许带崩同步
                        print(f"⚠️ 自选同步记账失败（同步照跑）：{type(exc).__name__}: {exc}")
                    if rep.get("ok") and not rep.get("dry_run"):
                        done_day = today
                        WATCHLIST_DONE.set()
                        print("✅ 自选同步完成：%s" % (rep.get("applied") or {}))
                    elif rep.get("fatal"):
                        # 权限/缺表这类硬错误今天重试也不会变好，别再每分钟刷一次；
                        # 但状态文件里的 reason 保持红色可见，明早自动再试。
                        done_day = today
                        print("⛔ 自选同步硬失败（今日不再重试，原因见 ingest_state.json）：%s"
                              % str(rep.get("reason"))[:120])
                    else:
                        print("⚠️ 自选同步未成功（今日会重试）：%s" % rep.get("reason"))
        except Exception as exc:  # noqa: BLE001  同步线程绝不能死
            print(f"⚠️ 自选同步异常：{type(exc).__name__}: {exc}")
        time.sleep(60)


def _start_watchlist_sync() -> None:
    global _watchlist_thread_started
    if not _watchlist_enabled():
        return
    with _watchlist_start_lock:
        if _watchlist_thread_started:
            return
        try:
            from duanxian import watchlist_sync  # noqa: F401  只探在不在
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ 自选同步未启动：watchlist_sync 导入失败（{type(exc).__name__}: {exc}）")
            return
        threading.Thread(target=_watchlist_scheduler, daemon=True,
                         name="watchlist-sync").start()
        _watchlist_thread_started = True


# ---------------------------------------------------------------- 个股资金流（新浪）
_fundflow_thread_started = False
_fundflow_start_lock = threading.Lock()


def _fundflow_enabled() -> bool:
    """开关读法与其余循环**同一份**（`_loop_enabled` → `duanxian.jobs.loop_enabled`）。"""
    from duanxian import jobs
    return _loop_enabled("VIBE_FUNDFLOW_SYNC", "fundflow", jobs.FUNDFLOW_DEFAULTS)


def _fundflow_slot() -> str:
    """时点读法只有一份（`duanxian/jobs.py::run_at`）：
    作业配置 `fundflow.run_at` > 环境变量 `FUNDFLOW_AT` > `FUNDFLOW_DEFAULTS`。
    默认值住在 jobs.py，不在这里重抄 —— 抄了就会出现"页面改得动、代码不读"。"""
    from duanxian import jobs
    return jobs.run_at(name="fundflow", env_var="FUNDFLOW_AT",
                       defaults=jobs.FUNDFLOW_DEFAULTS)[0]


def _fundflow_scheduler() -> None:
    """每工作日收盘后一次：新浪全市场个股资金流 → `market_data.fund_flow_rank`。

    为什么是"当天 + 进程内"而不是挂盘后链尾：这个口**报文里没有日期**，
    当天不采就永久少一天，事后补不出来。东财那条（`push2his …/fflow/daykline/get`，
    即 `c11` 的大盘资金流）已经从 09-18 起单发即断 —— 2026-09-26 实测 4/4 全断，
    而同主机的普通 kline 接口 200，所以死的是**接口不是频率风控**，重试也不会自己活。

    用子进程而不是 in-process import：一次爬 65 页约 37s，且 c21 自己的退出码
    （0 成功 / 3 非交易日拒写 / 1、2 真失败）比异常更适合判"今天还要不要再试"。

    ⚠️ 交易日判定**只认 `_trade_day_or_none` 那一份**（走 `reference_data.trade_calendar`），
    不许用 `util.is_weekend` 顶：2026-09-27 实测 09-25 是周五但**中秋休市**
    （日历 `is_open=false`），按周末判会把节假日当成该跑的日子。
    `None`（判不了，比如库没连上）**照跑** —— 归属判定归 `c21` 自己（它按日历拒写、
    退出码 3），宿主不替它决定，也不因为判不了就悄悄停一整天。

    ⚠️ 结果要记进 `duanxian/jobs.py` 的状态文件：`/api/jobs`、「任务调度大全」和
    `manage.py doctor` 都读那份 `state.json`。不记就会出现 2026-09-27 快讯那种**假故障**
    —— 一边 jsonl 每 30 秒在长，一边体检说"最近成功停在 30 小时前"。
    记账节流：状态翻转立刻记，稳定期最多 15 分钟一次心跳（按轮记会把
    `consecutive_failures` 一天刷到几千，那个数字就没意义了）。
    """
    import subprocess

    # 这一行的存在本身就是一条踩坑记录：循环里 2026-10-12 整晚都在报
    # `NameError: name 'NO_WINDOW' is not defined` —— 名字在本函数作用域里根本没定义，
    # 于是"进程起不来"被记成采集失败（连败 5），而循环看起来活得好好的。
    # 定义在 `app.tdx_client.win32`（非 Windows 上自动退化成 0），别再在这里抄一份。
    from app.tdx_client.win32 import NO_WINDOW

    done_day = None
    last_ok: bool | None = None
    last_record = 0.0
    while True:
        ok = False
        detail: dict = {}
        tried = False
        try:
            from duanxian import util
            today = util.china_today()
            is_day = _trade_day_or_none(today)
            if done_day != today and is_day is not False:
                hhmm = util.china_now().strftime("%H:%M")
                slot = _fundflow_slot()
                if slot <= hhmm <= _plus_minutes(slot, 90):
                    tried = True
                    p = subprocess.run([sys.executable, "-m", "ingest.jobs.c21_fundflow_sina"],
                                       cwd=_HERE, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=900,
                                       creationflags=NO_WINDOW)
                    last = ((p.stdout or "").strip().splitlines() or [""])[-1]
                    detail = {"rc": p.returncode, "tail": last[:200]}
                    if p.returncode == 0:
                        ok, done_day = True, today
                        print("✅ 个股资金流已入库：%s" % last[:120])
                    elif p.returncode == 3:
                        # 非交易日拒写不是失败：记下，但今天别再刷第二遍
                        ok, done_day = True, today
                        detail["skipped"] = "非交易日（c21 按日历自己拒写，未发起抓取）"
                        print("ℹ️ 个股资金流今日不采（非交易日）：%s" % last[:120])
                    else:
                        detail["reason"] = "rc=%s｜%s｜%s" % (p.returncode, last[:120],
                                                             (p.stderr or "")[-160:])
                        print("⚠️ 个股资金流未成功（今日会重试）%s" % detail["reason"])
        except Exception as exc:  # noqa: BLE001  采集线程绝不能死
            detail = {"reason": "%s: %s" % (type(exc).__name__, str(exc)[:180])}
            print(f"⚠️ 个股资金流线程异常：{detail['reason']}")
        if not tried:
            time.sleep(60)
            continue
        try:
            from duanxian import jobs
            if (last_ok is None or ok != last_ok
                    or time.monotonic() - last_record >= 900):
                jobs.record("fundflow", ok=ok, detail=detail)
                last_ok, last_record = ok, time.monotonic()
        except Exception as exc:  # noqa: BLE001  记账坏了不许带崩采集
            print(f"⚠️ 个股资金流记账失败（采集照跑）：{type(exc).__name__}: {exc}")
        time.sleep(60)


def _start_fundflow() -> None:
    global _fundflow_thread_started
    if not _fundflow_enabled():
        return
    with _fundflow_start_lock:
        if _fundflow_thread_started:
            return
        _fundflow_thread_started = True
        threading.Thread(target=_fundflow_scheduler, daemon=True, name="fundflow-sina").start()


# ---------------------------------------------------------------- 每日数据库备份（第 11 条常驻循环）
# 备份的动作（dump → 恢复验证 → 轮转 → 写 operations.quality_result）在 `app/ops/backup.py`
# 里早就齐了，**缺的从来不是能力，是"每天有人去敲一次"**：2026-10-12 实测 daily 与 weekly
# 两份都停在 148 小时前，而 doctor 只能把同一句话红着念一遍 —— 谁都没伸手补。
# 库里是这台机器唯一的真资产：日线/分钟线重灌要按小时计，而**已经采过的那些场补不回来**。

_backup_thread_started = False
_backup_start_lock = threading.Lock()


def _backup_enabled() -> bool:
    from duanxian import jobs
    return _loop_enabled("VIBE_BACKUP", "backup", jobs.BACKUP_DEFAULTS)


def _backup_slot() -> str:
    """时点读法只有一份：作业配置 `backup.run_at` > 环境变量 `BACKUP_AT` > `BACKUP_DEFAULTS`。"""
    from duanxian import jobs
    return jobs.run_at(name="backup", env_var="BACKUP_AT",
                       defaults=jobs.BACKUP_DEFAULTS)[0]


def _backup_scheduler() -> None:
    """到点（默认 18:40，盘后链与覆盖体检都收工之后）背一次库，一天只背成一次。

    三条约束与覆盖体检同形：**每轮重读开关**（面板上关掉就真的停手，不等重启 8910）、
    当天背成一次不再重跑、**每次尝试都记账**（`jobs.record("backup")`）——
    "今天到底有没有那一份"要从台账读得出来，不是靠人去翻备份目录。

    与加工段撞车时按 `skipped` 记（不涨连败）并顺延到下一分钟：pg_dump 本身不冲突，
    但夜间那一档正在重跑失败的加工步时，两份磁盘压力叠在一起不值得。
    """
    done_day = None
    while True:
        try:
            if not _backup_enabled():
                time.sleep(60)
                continue
            from app.ops import backup
            from duanxian import jobs, util

            today = util.china_today()
            if done_day == today:
                time.sleep(60)
                continue
            hhmm = util.china_now().strftime("%H:%M")
            slot = _backup_slot()
            if not (slot <= hhmm <= _plus_minutes(slot, 120)):
                time.sleep(60)
                continue
            busy = jobs.stage_busy()
            if busy:
                backup.journal_skipped("加工段 %s 正在跑，这一轮不背（下一分钟再看）" % busy)
                time.sleep(60)
                continue
            kind = backup.kind_for(today)
            out = backup.run_once(day=today)
            if out.get("ok"):
                done_day = today
                print("✅ 数据库已备份（%s）：%s %.2f MB%s" % (
                    kind, os.path.basename(str(out.get("artifact") or "-")),
                    float(out.get("mb") or 0),
                    "，恢复验证通过" if out.get("verify_ok") else "，本档不做恢复验证"))
            else:
                print("⚠️ 数据库备份未成功（今日会重试）：%s" % out.get("reason"))
        except Exception as exc:  # noqa: BLE001  这条线程死了=再没人备份，而界面上看不出来
            reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            print(f"⚠️ 数据库备份循环异常（线程保活，下一轮继续）：{reason}")
            try:
                from app.ops import backup
                backup.journal_failure("循环异常：" + reason)
            except Exception:  # noqa: BLE001  记账坏了不许带崩循环
                pass
        time.sleep(60)


def _start_backup() -> None:
    global _backup_thread_started
    if not _backup_enabled():
        return
    with _backup_start_lock:
        if _backup_thread_started:
            return
        _backup_thread_started = True
        threading.Thread(target=_backup_scheduler, daemon=True, name="pg-backup").start()


# ---------------------------------------------------------------- 消息推送（第 12 条）
# 渠道与计划住 `<data home>/notify/config.json`（判据在 `notifyconfig.py`，
# 内容组装与发送在 `duanxian/notify.py`）—— 这里只负责"活着、每分钟问一次到点没有"。
_notify_thread_started = False
_notify_start_lock = threading.Lock()
NOTIFY_SCAN_SEC = 60


def _notify_enabled() -> bool:
    """开关读法与其余循环**同一份**（`_loop_enabled` → `duanxian.jobs.loop_enabled`）。"""
    from duanxian import jobs
    return _loop_enabled("VIBE_NOTIFY", "notify_push", jobs.NOTIFY_PUSH_DEFAULTS)


def _notify_scheduler() -> None:
    """每 60 秒扫一遍定时计划，到点的就发。

    三条刻意的形状：
      · **这条循环自己没有 `run_at`** —— "几点发"是每条计划自己的字段（`plan.run_at`
        + `run_freq`/`weekly_days`），给循环再配一个时点就是页面上多一个不改也不生效的框
        （与 `tdx_watch` 同一条理由）；
      · **每轮重读开关**：面板上关掉，下一个心跳就停手，不必重启 8910；
      · **一条计划坏了不拖累同一分钟里的另一条**（内层各自 try），
        而循环自己异常要**既出声又记账**：线程死了没人推，界面上是看不出来的。

    ⚠️ 睡 60 秒是在**处理完之后**，所以一轮慢发送就会把下一个整分钟吃掉。这件事交给
    `notifyconfig.LATE_WINDOW_MIN`（到点之后 15 分钟内仍算"这一档还没发"）兜住 ——
    否则"错过了那一分钟"什么都不留下，比"发失败"难查一个数量级。窗口**不是自动重试**：
    发失败也记当天一笔（`mark_sent`），要重发仍然只走面板上的「立刻发一次」。
    """
    while True:
        try:
            if not _notify_enabled():
                time.sleep(NOTIFY_SCAN_SEC)
                continue
            import notifyconfig as NC              # noqa: F401  只为了它把 data home 建出来
            from duanxian import jobs, notify, util
            now = util.china_now()
            for plan in notify.due_plans(now):
                try:
                    out = notify.run_plan(plan, now=now)
                    mark = "✅ 推送已发" if out.get("status") == "ok" else "⚠️ 推送未成"
                    print(f"{mark}（{out.get('name')}）："
                          f"{str(out.get('detail') or out.get('reason'))[:140]}")
                except Exception as exc:  # noqa: BLE001  一条计划坏了不许带走这一分钟里的别的计划
                    reason = f"{type(exc).__name__}: {str(exc)[:140]}"
                    print(f"⚠️ 这条计划执行异常（其余照跑）：{plan.get('id')}：{reason}")
                    try:
                        jobs.record("notify_push", ok=False, detail={
                            "reason": f"计划 {plan.get('id')} 执行异常：{reason}",
                            "plan": plan.get("id"), "status": "error"})
                    except Exception:  # noqa: BLE001  记账坏了不许带崩循环
                        pass
        except Exception as exc:  # noqa: BLE001  这条线程死了 = 再没人推送，而界面上看不出来
            print(f"⚠️ 消息推送循环异常（线程保活，下一轮继续）：{type(exc).__name__}: {str(exc)[:160]}")
        time.sleep(NOTIFY_SCAN_SEC)


def _start_notify_push() -> None:
    """幂等 —— 重复调用不会起第二条。"""
    global _notify_thread_started
    if not _notify_enabled():
        return
    with _notify_start_lock:
        if _notify_thread_started:
            return
        _notify_thread_started = True
        threading.Thread(target=_notify_scheduler, daemon=True, name="notify-push").start()
        print(f"✅ 消息推送已启动（每 {NOTIFY_SCAN_SEC} 秒扫一遍计划表，凭据只在 "
              f"<data home>/notify/config.json 或 .env，pid {os.getpid()}）")


# ---------------------------------------------------------------- 收盘后每日链（15:59 两段）
# 判据只认**数据日期**与**真交易日历**，不认"上次跑过"：这是 P4 定下的口径。
# 为什么挂常驻 uvicorn 而不是计划任务：宿主本来就在跑，而 Windows 计划任务那条
# （`FinanceTool-DailyCloseSync`）实测会在结尾 `taskkill /F /IM TdxW.exe` 强杀客户端、
# 退出码 1 却已经写库 —— 出问题没人知道。注册与否必须由人点头。
_daily_close_thread_started = False
_daily_close_start_lock = threading.Lock()

# 链的形状（模块、超时、每段退出码的读法、stop 的措辞）住在 `duanxian/jobs.py::CLOSE_CHAIN`，
# **不在这里定义第二遍**：手动触发走的是同一批执行体，两份表迟早一份改了另一份没改。
DAILY_CLOSE_STAGES = tuple(
    (key, spec["label"], spec["module"], spec["timeout"])
    for key, spec in jstore.CLOSE_CHAIN.items())
DAILY_CLOSE_MAX_TRIES = jstore.close_attempts_max()   # 上限 = 重试节奏表的长度（`jobs.py`）


def _daily_close_enabled() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return os.environ.get("VIBE_DAILY_CLOSE", "").strip().lower() not in ("0", "false", "off", "no")


def _daily_close_slot() -> str:
    """收盘链的时点。**读法只有一份**，在 `duanxian/jobs.py::run_at`：
    作业配置 `daily_close.run_at` > 环境变量 `DAILY_CLOSE_AT` > 15:59。
    宿主与 `/api/jobs` 都走它，否则页面显示一个时刻、实际按另一个跑。"""
    from duanxian import jobs as jstore
    return jstore.run_at()[0]


def _trade_day_or_none(day) -> bool | None:
    """今天是不是 A 股交易日。**None = 判不了**（库没连、日历没覆盖这一天），
    判不了就不许当成"非交易日"跳过 —— 那样整条链会安静地一天都不跑。

    判定本身只有一份实现：`duanxian/pg.py::trade_day_open()`（`/api/jobs` 的开关面板共用它）。
    这一层只负责"查询本身炸了也别弄死调度线程"。
    注意别用 `duanxian.util.is_weekend` 顶替：它只看周六日，节假日一律放行。
    """
    try:
        from duanxian import pg as _pg
        return _pg.trade_day_open(day)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 交易日判定失败（不猜，今天暂不跑）：{type(exc).__name__}: {exc}")
        return None


def _trade_day_why(day) -> str:
    """那句"今天不跑"是**哪一层**给的依据 —— 日志必须跟着说，否则 2027 年起的周末
    与跨年那几天，屏幕上会写着"判据是真日历"而实际是星期几或行情时间戳在答
    （一句会在自己那天变成假话的措辞，不如一开始就跟着读数走）。
    """
    try:
        from duanxian import pg as _pg
        return {
            "calendar": "库里的交易日历",
            "weekend": "星期几自判（日历没有这一天）",
            "quote": "实时行情的时间戳自证（日历没有这一天）",
        }.get(_pg.trade_day_evidence(day), "判据读不到")
    except Exception:  # noqa: BLE001 — 措辞探测失败不该弄死调度线程
        return "判据读不到"


def _calendar_max_day() -> str | None:
    """日历覆盖到哪一天 —— 只在"判不了"时查一次，用于把话说清楚。"""
    try:
        from duanxian import pg as _pg
        return _pg.calendar_max_day()
    except Exception:  # noqa: BLE001
        return None


def _daily_close_verdict(key: str, rc: int) -> tuple[str, bool]:
    """退出码的读法。**自己不实现**，转 `duanxian/jobs.py::close_verdict` ——
    手动触发那条路也要按同一份读法记账，两份实现必然分叉。"""
    return jstore.close_verdict(key, rc)


def _daily_close_next(ok: set[str], closed: set[str], skipped: set[str],
                      tries: dict[str, int], minutes_since_slot: int):
    """`((段 | None), 还在等什么)`。**顺序与"过不了就整条停"是这里唯一的安全依据**。

    四种"这段今天不跑"必须分开，混了就等于替操作员做决定：
      · `ok`      —— 真跑成了，放行下一段；
      · `skipped` —— **作业开关为关**（系统配置里关的）。跳过它、继续往下：
                     段2（`e1_fill_all`）自己带 `tdxdl`/`daily` 两步补齐，本来就不硬依赖段1，
                     把"我关了获取段"读成"连加工也别做"是越权；
      · `closed`  —— 跑过了但按设计停下（要人来/未到收盘线）。这种**必须挡住后面**：
                     库里的数确实没到位，越过它去算只会产出"看着算完其实是空"的结果；
      · `waiting` —— 失败过（hold），但**没到下一档重试的时刻**。这时既不能往下跑、
                     也不能收工：上游（通达信盘后文件）昨天与前天都是 17:2x 才到，
                     老逻辑每 60 秒敲一次、3 次在两分钟内烧完，于是"晚到"被读成"没有"。
    重试次数用尽同样挡住后面（那是失败，不是人关的）。
    """
    for key, label, module, tmo in DAILY_CLOSE_STAGES:
        if key in closed:
            return None, False
        if key in ok or key in skipped:
            continue
        used = tries.get(key, 0)
        if used >= DAILY_CLOSE_MAX_TRIES:
            return None, False
        if used and not jstore.close_attempt_due(used + 1, minutes_since_slot):
            # 这一段还要试，只是现在没到点 —— 后面的段必须一起等住
            return None, True
        return (key, label, module, tmo), False
    return None, False


def _daily_close_scheduler() -> None:
    ok: set[str] = set()                    # 今天真跑成的段
    closed: set[str] = set()                # 今天到此为止的段（按设计跳过 / 要人来）
    skipped: set[str] = set()               # 作业开关为关的段（每轮重读，改开关不必重启）
    tries: dict[str, int] = {}              # 各段今天试过几次（hold 有上限）
    stopped = False                         # 整条链今天不用再进这个分支
    disabled_flagged: dict[str, object] = {}   # 开关为关 → 一天只记一次账（按 key→日期）
    last_day, calendar_unknown_day = None, None
    launch_done_day = None                  # "提前拉起"今天发过没有（**一天只发一次**，
    #                                        #  发失败也不补：那是连续点桌面，比不点更糟）
    while True:
        try:
            from duanxian import jobs as jstore
            from duanxian import util
            today = util.china_today()
            if last_day != today:
                ok, closed, skipped, tries, stopped = set(), set(), set(), {}, False
                last_day = today
            hhmm = util.china_now().strftime("%H:%M")
            slot = _daily_close_slot()
            cfg, _warns = jstore.settings("daily_close", jstore.DAILY_CLOSE_DEFAULTS)
            lead = int(cfg.get("launch_lead_min") or 0)
            only_trade = bool(cfg.get("trading_days_only", True))
            # ① **提前 N 分钟把通达信拉起并登录**（用户 2026-10-07 要的可配置项，默认 0=不提前）。
            # 为什么要比链早：客户端登录之后自己还要同步 7~10 分钟数据，15:59 才拉起的话，
            # 链里 ③b 问 TQ 那一步正好撞上"它正在写 vipdoc"，而那正是历史上天天把链跑死的时刻。
            # 放在窗口判断之前是有意的：这一步的意义就是"比链早"，等到点再拉起等于没提前。
            if (lead > 0 and launch_done_day != today
                    and _plus_minutes(slot, -lead) <= hhmm < slot
                    and bool(cfg.get("enabled", True))):
                launch_done_day = today       # 先占位再动手：失败也不在同一分钟里重试
                trade = _trade_day_or_none(today)
                if only_trade and trade is not True:
                    print(f"ℹ️ 不提前拉起：{today} "
                          + ("不是交易日（判据是真日历）" if trade is False
                             else "交易日历判不了，宁可不碰桌面"))
                else:
                    out = jstore.run_tdx_launch_only(str(today))
                    mark = "✅" if out.get("rc") == 0 else "⚠️"
                    detail = out.get("reason") or out.get("last_line") or "已发起"
                    print(f"{mark} 提前拉起通达信（{slot} 前 {lead} 分钟 = "
                          f"{_plus_minutes(slot, -lead)}）：{detail}")
            # 窗口给 3 小时：进程晚起、机器休眠、段1 失败都要在今天内补上
            if not stopped and slot <= hhmm <= _plus_minutes(slot, 180):
                # 每轮都重读开关：用户在系统配置里关掉某个作业，不该等到明天或重启才生效
                skipped = {k for k, *_r in DAILY_CLOSE_STAGES
                           if not jstore.settings(k, jstore.CLOSE_DEFAULTS[k])[0]
                           .get("enabled", True)}
                # **到点了却因为开关没跑**：必须记一次账。`/jobs` 那一格只会写"已停用"，
                # 那看着像"用户自己不想跑"，与被谁误关是两件事 —— 留痕与时间才分得开。
                for key in sorted(skipped):
                    if disabled_flagged.get(key) == today:
                        continue
                    disabled_flagged[key] = today
                    note = jstore.change_note(key)
                    jstore.record(key, ok=False, detail={
                        "verdict": "disabled",
                        "reason": (f"{today} {slot} 到点没跑：作业开关为关"
                                   f"（改于 {note.get('changed_at') or '未知时间'}，"
                                   f"来自 {note.get('changed_by') or '未知调用方'}）"
                                   "；开关关着不会自动恢复，要显式打开")})
                    print(f"⚠️ 收盘链 {key} 到点未跑：开关为关（"
                          f"{note.get('changed_at') or '?'} / {note.get('changed_by') or '?'}）")
                stage, waiting = _daily_close_next(ok, closed, skipped, tries,
                                                  _minutes_since_slot(slot, hhmm))
                if waiting:
                    # 今天还要再试，只是没到下一档（见 `jobs.DAILY_CLOSE_RETRY_MIN`）：
                    # 既不能收工，也不能往下跑 —— 上游的盘后文件常常 17:2x 才到。
                    pass
                elif stage is None:
                    stopped = True
                    # 收工这一刻才谈"关客户端"：加工段真跑过才动（不然就是白碰桌面）。
                    _maybe_close_client(today, ran_fill_all="fill_all" in (closed or {}))
                else:
                    trade = _trade_day_or_none(today)
                    if not only_trade and trade is not True:
                        # 用户把"只在交易日执行"关掉了：每天都发一次。
                        # 这不是"每天都会有数"—— 段脚本自己按日历拒写（`fundflow` rc 3），
                        # 非交易日只会多打一次请求。为什么还要留这一档：日历只登到
                        # `reference_data.trade_calendar` 的最大日，跨年那几天它判不了，
                        # 而"判不了就不跑"会让那几天集体缺数。
                        print(f"ℹ️ {today} 日历说不是交易日（{trade}），但"
                              f"`trading_days_only=false` → 照跑一次，段脚本自己会把不该写的挡掉")
                        trade = True
                    if trade is None:
                        # 一天记一次账（不是每 60 秒刷）：页面上要看得见"今天为什么没跑"，
                        # 而只 print 进块缓冲的日志等于没说。
                        if calendar_unknown_day != today:
                            calendar_unknown_day = today
                            upto = _calendar_max_day() or "未知"
                            why = (f"交易日历判不了 {today}（库里日历最大到 {upto}，"
                                   "实时行情的时间戳也没探到）"
                                   "—— 这不是「今天不是交易日」，是不敢猜")
                            jstore.record("daily_close", ok=False,
                                          detail={"verdict": "unknown_calendar", "reason": why})
                            print(f"⚠️ 收盘链未跑：{why}")
                    elif trade is False:
                        stopped = True
                        print(f"ℹ️ {today} 非交易日，收盘链今天不跑（依据：{_trade_day_why(today)}）")
                    else:
                        # 一段接一段跑到底，`_daily_close_next` 说没有可跑的了才收手。
                        # 一天只跑一段会把 15:59 的链拆成 15:59 / 16:00 / …，
                        # 中间任何一次进程重启都会让"跑了一半的今天"没人说得清。
                        while True:
                            stage, waiting = _daily_close_next(ok, closed, skipped, tries,
                                                               _minutes_since_slot(slot, hhmm))
                            if waiting or stage is None:
                                # 没到下一档就交回心跳循环（它每分钟重看一次时钟）；
                                # 真收工（全跑完 / 上限用尽 / 段被 stop 挡住）才置 stopped。
                                stopped = not waiting
                                break
                            key, label, module, tmo = stage
                            # 执行体只有一份（`jobs.run_close_stage`）：它内部抢 claim 锁，
                            # 与 `/api/jobs` 的手动触发、`scripts/jobs.py run-now` 互斥
                            print(f"▶️ 收盘链 · {label}")
                            tries[key] = tries.get(key, 0) + 1
                            out = jstore.run_close_stage(key, {"attempt": tries[key]})
                            if not out["ran"]:
                                # 手动触发正占着：本轮让路，**不计失败也不计数**（那不是坏了）
                                print(f"ℹ️ {key} 被另一个入口占着，本轮让路")
                                tries[key] -= 1
                                break
                            rc, verdict, note = out["rc"], out["verdict"], out["reason"]
                            mark = {"run": "✅", "stop": "ℹ️", "hold": "⚠️"}[verdict]
                            print(f"{mark} {key} 结束 rc={rc}｜{verdict}｜{note[:150]}")
                            if verdict == "run":
                                ok.add(key)
                                continue
                            if verdict == "stop":
                                closed.add(key)
                            # hold / stop 都不给后面的段写运行记录：**没跑过的段 `last_run_at`
                            # 必须是 null**（契约 03 §3），今天被挡住这件事由 `/api/jobs`
                            # 从上游的 `last_detail` 现读现说，不落到状态文件里造假时刻。
                            break
        except Exception as exc:  # noqa: BLE001  调度线程绝不能死
            print(f"⚠️ 收盘链线程异常：{type(exc).__name__}: {exc}")
        time.sleep(60)


def _start_daily_close() -> None:
    global _daily_close_thread_started
    if not _daily_close_enabled():
        print("ℹ️ 收盘链宿主未启动（VIBE_DAILY_CLOSE 关着，或在 pytest 里）"
              " —— 15:59 那两条作业今天不会自己跑")
        return
    with _daily_close_start_lock:
        if _daily_close_thread_started:
            return
        _daily_close_thread_started = True       # ⚠️ 只在 start() 成功之后置位
        threading.Thread(target=_daily_close_scheduler, daemon=True,
                         name="daily-close").start()
        # 这行是给人看的"今天到底会不会跑"：线程起了但没这行 = 进程里跑的还是旧代码，
        # 别靠 `/api/jobs` 里有没有那两条作业反推（作业行是新代码渲染的，两码事）
        print(f"✅ 收盘链宿主已启动（pid {os.getpid()}，时点 {_daily_close_slot()}，"
              f"判据=reference_data.trade_calendar，窗口 3 小时，段内重试上限 {DAILY_CLOSE_MAX_TRIES} 次）")


# 覆盖体检每日预计算 —— 首屏靠它，不然页面要么等 50 秒要么回退成"未接入"。
# 走缓存时 `generated_at` / `is_stale` 随数据一起交出（`duanxian/coverage.py`），
# 新鲜度不许被缓存隐掉。
_coverage_thread_started = False
_coverage_start_lock = threading.Lock()


def _jobs_defaults(name: str) -> dict:
    """某条循环在册的默认值（开关与代价都按它判）。读不到 = 按"开着"处理，不停循环。"""
    try:
        from duanxian import jobs
        if name == "news":
            return jobs.news_defaults()
        return jobs.CLOSE_DEFAULTS.get(name) or {}
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 读 {name} 的在册默认值失败（按默认开着处理）：{type(exc).__name__}: {exc}")
        return {"enabled": True}


_loop_off_seen: set[str] = set()


def _loop_off_note(name: str) -> bool:
    """"这条被开关停着"只在**刚开始停**时印一次：盯盘那条每 3 秒问一次，
    每轮都喊等于把日志刷成噪音，而噪音里的真信号没人看得见。"""
    if name in _loop_off_seen:
        return False
    _loop_off_seen.add(name)
    return True


def _loop_enabled(env_var: str, name: str, defaults: dict) -> bool:
    """常驻循环的开关读法**只有一份**：`duanxian.jobs.loop_enabled`。

    这里只留一个转发，是为了让"总闸环境变量 + 系统配置里的 `enabled`"这条判据
    没有第二份实现可以漂移（10-07 之前 `server.py` 里有三份拷贝、`vr/watchtower.py`
    里还有第四份：改一处不传另外三处，"页面关了、线程照跑"就是这么长出来的）。

    10-07 把 `intraday` / `watchtower` / `news` 三条也接进这一份：它们原先只有环境变量闸，
    系统配置面板上的开关是**没人读的假生效**。三条现在都在 `CLOSE_DEFAULTS`（news 走
    `jobs.news_defaults()`，数字仍住 `newswire.py`）里，看得见也就关得掉。
    """
    from duanxian import jobs
    return jobs.loop_enabled(env_var, name, defaults)


def _coverage_enabled() -> bool:
    from duanxian import jobs
    return _loop_enabled("VIBE_COVERAGE_PRECOMPUTE", "coverage", jobs.COVERAGE_DEFAULTS)


def _coverage_slot() -> str:
    """时点读法只有一份：作业配置 `coverage.run_at` > 环境变量 > `COVERAGE_DEFAULTS`。
    原先这里是 env + 一个字面量 `"18:20"`，默认值住在宿主里 —— 那正是
    "系统配置改了时点、宿主按另一个跑"的形状。"""
    from duanxian import jobs
    return jobs.run_at(name="coverage", env_var="COVERAGE_PRECOMPUTE_AT",
                       defaults=jobs.COVERAGE_DEFAULTS)[0]


def _coverage_journal(ok: bool, detail: dict) -> None:
    """覆盖体检预计算的记账（判据①：只 print 等于没说）。记账坏了不许带崩重算。"""
    try:
        from duanxian import jobs
        jobs.record("coverage", ok=ok, detail=detail)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 覆盖体检记账失败（重算照跑）：{type(exc).__name__}: {exc}")


def _coverage_scheduler() -> None:
    """每天 18:20 之后重算一次覆盖体检（首屏靠它，现算要 50 秒）。

    ⚠️ 这条原先**完全不记账**：它挂了、或每天都算不成功时，页面照样显示昨天那份缓存，
    看上去一切正常。2026-10-06 按《自动执行机制自查表》判据①补上：
    补抓窗口照旧 120 分钟、只在成功那天清账，但**每次尝试都记一次**，
    这样"今天到底算成没有"能从 `jobs/state.json` 读出来，不用猜。
    """
    done_day = None
    while True:
        try:
            from duanxian import coverage, util
            today = util.china_today()
            if done_day == today or util.is_weekend(today):
                done_day = today      # 周末与已完成那天都别每分钟再判一次
            else:
                hhmm = util.china_now().strftime("%H:%M")
                slot = _coverage_slot()
                if slot <= hhmm <= _plus_minutes(slot, 120):
                    rep = coverage.report(refresh=True)
                    ok = bool(rep.get("available"))
                    _coverage_journal(ok, {"reason": rep.get("reason"),
                                           "generated_at": rep.get("generated_at")})
                    if ok:
                        done_day = today
                        print("✅ 覆盖体检已重算：%s" % rep.get("counts"))
                    else:
                        print("⚠️ 覆盖体检重算未成功（今日会重试）：%s" % rep.get("reason"))
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            print(f"⚠️ 覆盖体检预计算异常：{reason}")
            _coverage_journal(False, {"reason": "循环异常：" + reason})
        time.sleep(60)


def _start_coverage_precompute() -> None:
    global _coverage_thread_started
    if not _coverage_enabled():
        return
    with _coverage_start_lock:
        if _coverage_thread_started:
            return
        try:
            from duanxian import coverage  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ 覆盖体检预计算未启动：coverage 导入失败（{type(exc).__name__}: {exc}）")
            return
        threading.Thread(target=_coverage_scheduler, daemon=True, name="coverage-precompute").start()
        _coverage_thread_started = True


# ---- 同花顺自选云端同步（用户 2026-10-08 点名：启动 → 等云端落盘 → 关闭）----------------
# 默认**关**（`THS_SYNC_DEFAULTS.enabled=False`）：这条动的是桌面上的客户端窗口，
# 第一次必须人在键盘前手动演练（`python -m ingest.jobs.c23_ths_cloud_sync --no-close`）。
_ths_sync_thread_started = False
_ths_sync_start_lock = threading.Lock()


def _ths_sync_enabled() -> bool:
    from duanxian import jobs
    return _loop_enabled("VIBE_THS_SYNC", "ths_sync", jobs.THS_SYNC_DEFAULTS)


def _ths_sync_slot() -> str:
    """时点读法只有一份：作业配置 `ths_sync.run_at` > 环境变量 > 默认值（15:30）。"""
    from duanxian import jobs
    return jobs.run_at(name="ths_sync", env_var="THS_SYNC_AT",
                       defaults=jobs.THS_SYNC_DEFAULTS)[0]


def _ths_sync_scheduler() -> None:
    """每天（或每勾选的那几天）到点起一次同花顺，等 `stockblock.ini` 被云端刷新，再收掉窗口。

    三条实现约束：
    1. **每轮重读开关**（`intraday` 的做法）：启动时才判一次的写法，会让面板上"关掉"
       对当前这个进程完全不生效 —— 10-07 那条假开关就是这种形状，别在第九条上重犯。
    2. **一天只成一次**，但失败/跳过那天还可以重试（补抓窗口 120 分钟，与覆盖体检同一条），
       因为同花顺可能正好被人开着、或网络慢。
    3. 工作本体放在 `ingest.jobs.c23_ths_cloud_sync.run_once()` 里（那里有 pytest 硬挡与
       "只关自己起的 pid"两条规矩），本线程只做时机与记账。
    """
    done_day = None
    while True:
        try:
            from duanxian import jobs, util
            if not _ths_sync_enabled():
                time.sleep(60)
                continue
            today = util.china_today()
            merged, _warns = jobs.settings("ths_sync", jobs.THS_SYNC_DEFAULTS)
            if done_day == today or not jobs.freq_allows(merged, today):
                done_day = today if done_day == today else done_day
                time.sleep(60)
                continue
            hhmm = util.china_now().strftime("%H:%M")
            slot = _ths_sync_slot()
            if not (slot <= hhmm <= _plus_minutes(slot, 120)):
                time.sleep(60)
                continue
            from ingest.jobs import c23_ths_cloud_sync as ths
            res = ths.run_once(wait_sec=int(merged.get("wait_max_sec") or 240),
                                do_close=bool(merged.get("close_after", True)))
            jobs.record("ths_sync", ok=bool(res.get("ok")),
                        detail={"reason": res.get("reason"), "skipped": res.get("skipped"),
                                "changed": res.get("changed"), "ini": res.get("ini"),
                                "after": res.get("after")})
            if res.get("ok"):
                done_day = today
                print("✅ 同花顺云端自选已落到本机 ini：%s" % (res.get("after"),))
            else:
                print("ℹ️ 同花顺云端同步这轮不成（窗口内还会再试）：%s" % res.get("reason"))
        except Exception as exc:  # noqa: BLE001  这条线程死了就等于自选镜像从此不更新
            reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            print(f"⚠️ 同花顺云端同步循环异常：{reason}")
            try:
                from duanxian import jobs
                jobs.record("ths_sync", ok=False, detail={"reason": "循环异常：" + reason})
            except Exception:  # noqa: BLE001
                pass
        time.sleep(60)


def _start_ths_cloud_sync() -> None:
    global _ths_sync_thread_started
    if not _ths_sync_enabled():
        return
    with _ths_sync_start_lock:
        if _ths_sync_thread_started:
            return
        try:
            from ingest.jobs import c23_ths_cloud_sync  # noqa: F401  导入失败就别起线程
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ 同花顺云端同步未启动：作业导入失败（{type(exc).__name__}: {exc}）")
            return
        threading.Thread(target=_ths_sync_scheduler, daemon=True, name="ths-cloud-sync").start()
        _ths_sync_thread_started = True
        print(f"✅ 同花顺云端同步已挂号（默认时点 {_ths_sync_slot()}，开关关闭时只是不起线程）")


# ---------------------------------------------------------------- 第十条常驻循环：失败单步重试阶梯
_step_retry_thread_started = False
_step_retry_start_lock = threading.Lock()


def _step_retry_enabled() -> bool:
    from duanxian import jobs
    return _loop_enabled("VIBE_STEP_RETRY", "step_retry", jobs.STEP_RETRY_DEFAULTS)


def _step_retry_scheduler() -> None:
    """加工段**失败的那几步**在夜里按档重跑（21:00 / 22:00 / 23:00 / 01:00 / 02:30 / 04:20 默认）。

    为什么要有这一条：链 17:21 收工时报"某步失败"，在那之前**今天不会再有人试第二次** ——
    要么等明天 15:59 顺手补，要么人记得去敲 `--only`。用户 10-08 点的就是这个洞。

    三条实现约束（与第九条同形）：
    1. **每轮重读开关**：关掉就真的停手，不等到重启 8910。
    2. 判据全在 `ingest.jobs.step_retry`：每档先重读逐步台账，**只跑还挂着 fail 的步**
       （`refused` = 按口径拒写，不进名单），跑完再读一次台账摘掉补成的；
       档期用完仍有失败就记放弃，等下一次收盘链。
    3. 真起进程的那一步在 `step_retry._run_only` 里，测试进程硬挡；与 15:59 那条链
       抢**同一把** `fill_all` 锁，抢不到就顺延到下一档（不算失败）。
    """
    while True:
        try:
            if not _step_retry_enabled():
                time.sleep(60)
                continue
            import datetime as _dt

            from ingest.jobs import step_retry
            row, why = step_retry.due_lane(_dt.datetime.now())
            if row is None:
                time.sleep(60)
                continue
            print(f"▶️ 重试阶梯 · 档 {row['label']}：准备补跑（判据见 step_retry）")
            out = step_retry.run_due()
            if out.get("busy"):
                print(f"⏸ 重试阶梯 · 档 {row['label']} 顺延：{out.get('reason')}")
            elif out.get("skipped"):
                print(f"ℹ️ 重试阶梯这轮不跑：{out.get('reason')}")
            elif out.get("ok"):
                print("✅ 重试阶梯 · %s" % out.get("reason"))
            elif out.get("worked"):
                # 补成了几步、还剩几步：动作是有效的，别用 ⚠️ 说一句成功的话
                # （判据与台账同源，见 `step_retry.run_due` 里那段注释）
                print("✅ 重试阶梯 · %s（还剩 %s 没补上，下一档再试）"
                      % (out.get("reason"), "、".join(out.get("still_failing") or []) or "无"))
            else:
                print("⚠️ 重试阶梯 · %s" % out.get("reason"))
            time.sleep(60)
        except Exception as exc:  # noqa: BLE001  这条线程死了=失败再没人补，且界面上看不出来
            reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            print(f"⚠️ 失败重试阶梯异常（线程保活，下一轮继续）：{reason}")
            try:
                from duanxian import jobs
                jobs.record("step_retry", ok=False, detail={"reason": reason})
            except Exception:  # noqa: BLE001
                pass
            time.sleep(60)


def _start_step_retry() -> None:
    global _step_retry_thread_started
    if not _step_retry_enabled():
        return
    with _step_retry_start_lock:
        if _step_retry_thread_started:
            return
        try:
            from ingest.jobs import step_retry  # noqa: F401  导入失败就别起线程
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ 失败重试阶梯未启动：作业导入失败（{type(exc).__name__}: {exc}）")
            return
        threading.Thread(target=_step_retry_scheduler, daemon=True, name="step-retry").start()
        _step_retry_thread_started = True
        from duanxian import jobs as _j
        print("✅ 失败单步重试阶梯已挂号（默认档 %s；只重试点里失败的步）"
              % _j.STEP_RETRY_DEFAULTS["times"])


#: 「链收工后自动关客户端」一天只试一次：收掉了不必再碰；没收掉的那一天由待人工清单说。
_client_closed_day = None


#: 退出确认框的按钮集合（**2026-10-13 实测**：标题是「通达信金融终端64」、类名 `#32770`、
#: 按钮是「退出 / 重新登录 / 取消」）。`s8_finish.confirm_boxes` 只认
#: "放弃这一轮下载"那类正文，所以它**看不见这个框** —— 台账于是写成"没看到决定框"，
#: 那是假读数：框就挂在桌上。认框按**按钮文字精确匹配**（不 substring、不猜位置）。
EXIT_CONFIRM_BUTTONS = ("退出",)


def _exit_confirm_boxes(pid: int | None = None) -> list[dict]:
    """当前挂着的"要不要退出通达信"决定框（含 hwnd / 标题 / 按钮文字）。没有就交回空表。

    ⚠️ **这里不吞异常**（10-13 实测的教训）：上一版把按钮类名元组写成 `s8_finish.BUTTON_CLASSES`
    —— 那个元组其实住在 `locator` 上 —— `AttributeError` 被函数里的 `except` 咽掉，于是返回
    空表，台账就写了"没看到决定框"，而桌上正挂着那只框。**判据坏了必须表现为"探测本身坏了"，
    不许表现为"什么都没看到"**（同 memory 那条"闸要包在自己的 try 里、坏了按出声"）。
    """
    from app.tdx_client import locator, win32
    from app.tdx_client.steps import s8_finish

    out: list[dict] = []
    for w in win32.top_windows():
        if pid is not None and w.pid != pid:
            continue
        if not w.visible or w.cls != "#32770":
            continue
        kids = [c for c in win32.children(w.hwnd, max_depth=4)
                if c.visible and c.cls.startswith(locator.BUTTON_CLASSES)]
        labels = [s8_finish.button_text(c) for c in kids]
        if any((t or "").strip() in EXIT_CONFIRM_BUTTONS for t in labels):
            out.append({"hwnd": w.hwnd, "title": (w.text or "")[:60],
                        "buttons": [t for t in labels if t][:6]})
    return out


def _exit_boxes_safe(pid: int | None) -> tuple[list[dict], str]:
    """`(框, 探测本身坏了的那句原话)`。坏 ⇒ 交回空表 + 原因，绝不静当作"没有框"。"""
    try:
        return _exit_confirm_boxes(pid), ""
    except Exception as exc:  # noqa: BLE001  读桌面读坏了不能带崩探测线程，但要说出来
        return [], f"{type(exc).__name__}: {str(exc)[:120]}"


def _answer_exit_confirm(box: dict) -> dict:
    """点这张框上的「退出」：**全文相等**才点，框里有黑名单文字就不点，pytest 里硬挡。

    为什么要有这一步（用户 2026-10-13 点名）：`on_demand` 那一档的承诺是"带外自动关掉、
    定时任务不要弹窗、后台静默执行"。只发一次"点 X"就住手，等于每天要人对着那个框点一次
    「退出」—— 那条模式就还是半真的。点击走的仍是**温和退出**（等价于人点「退出」），
    不是 `/F` 强杀；开关是 `tdx_watch.exit_confirm_click`，关掉就退回"只请退、交人工"。
    """
    import os as _os
    from app.tdx_client import locator, win32
    from app.tdx_client.steps import s8_finish

    # 黑名单在**这里**判，不在认框那一层判：这是碰到按钮前的最后一道闸，
    # 谁调用都得过它（认框函数交回来的表要是少了这一格，也不能变成"照点"）。
    unsafe = [t for t in (box.get("buttons") or [])
              if t and any(b in t for b in s8_finish.NEVER_CLICK)]
    if unsafe:
        return {"clicked": False,
                "reason": f"框里有不能点的按钮（{'、'.join(unsafe)}）→ 不点"}
    if _os.environ.get("PYTEST_CURRENT_TEST") and not _os.environ.get("TDX_ALLOW_TEST_CLICK"):
        return {"clicked": False, "reason": "PYTEST 硬挡：不在用户桌面上点按钮"}
    hits = [c for c in locator.find_child(box["hwnd"], text="退出",
                                          classes=locator.BUTTON_CLASSES,
                                          exact=True) if c.visible]
    if not hits:
        return {"clicked": False, "reason": "框上没有文字正好等于「退出」的按钮 → 不点"}
    return {"clicked": bool(win32.bm_click(hits[0].hwnd)), "reason": "已投给「退出」按钮"}


def _client_gone(pid: int, rounds: int) -> bool:
    """重探 `rounds` 轮（每轮 2 秒），进程退了就返回 True。"""
    from app.tdx_client import watchman

    for _ in range(max(1, rounds)):
        time.sleep(2)
        if not watchman.probe().get("running"):
            return True
    return False


def _close_tdx_client(*, settle_sec: int = 40) -> dict:
    """温和地请通达信退出：恢复最小化主窗口 → 只发一次"点 X" → 确认框按开关答「退出」。

    四条守则：
    1. 加工段真跑完才动（调用方判），且 `TPyth*` 还在同步 / vipdoc 还在往前写 → 不动；
    2. **绝不 /F 强杀主程序**：TQ 那条腿与次日的自动登录都靠它，误杀的代价比"多开着一个窗口"大；
    3. 确认框只认**全文等于「退出」**的按钮（10-06 那次子串匹配按到「取消下载」，掉的是正在下的
       数据），框里出现 `s8_finish.NEVER_CLICK` 任何一个词就不点；
    4. 桌上已经挂着确认框 ⇒ **不再发第二次"点 X"**（那只会把框叠一层），直接答已有的那张。

    `tdx_watch.exit_confirm_click=False` 时回到旧行为：只请退、确认框交人工。
    """
    import os as _os

    if _os.environ.get("PYTEST_CURRENT_TEST"):
        return {"acted": False, "closed": False, "reason": "测试进程：不发任何窗口消息"}
    from app.tdx_client import locator, watchman, win32
    from app.tdx_client.steps import s8_finish
    from duanxian import jobs as jstore

    cfg, _w = jstore.settings("tdx_watch", jstore.TDX_WATCH_DEFAULTS)
    auto_click = bool(cfg.get("exit_confirm_click"))

    st = watchman.probe()
    pid = st.get("pid")
    if not st.get("running") or not pid:
        return {"acted": False, "closed": False, "reason": "客户端本来就没在跑"}
    busy_sync, who = locator.sync_running()
    if busy_sync:
        return {"acted": False, "closed": False,
                "reason": f"{who} 还在同步 → 不收（掐断它就是把盘后数据打断）"}
    if _vipdoc_still_growing() is not False:
        return {"acted": False, "closed": False,
                "reason": "vipdoc 还在往前写（或判不了）→ 不收"}
    frames = [w for w in win32.windows_of_pid(pid)
              if getattr(w, "cls", "") == "TdxW_MainFrame_Class"
              or locator.MAIN_TITLE in (getattr(w, "text", "") or "")]
    if not frames:
        return {"acted": False, "closed": False, "reason": f"找不到主窗口（pid {pid}）→ 不动"}

    # **桌上已经挂着退出确认框就不要再点一次 X**：那只会把对话框叠一层，
    # 而且"程序说它试过了"其实是"又催了用户一次"。10-13 第一次实测就是栽在这里。
    already, box_err = _exit_boxes_safe(pid)
    if already and not auto_click:
        return {"acted": False, "closed": False, "pid": pid, "dialogs": already,
                "box_error": box_err or None,
                "reason": "上一次请退留下的退出确认框还挂着（%s：按钮 %s）→ 不再发第二次退出请求，"
                          "等你点「退出」或「取消」；那一天不再重复催（程序不替你点按钮）"
                          % (already[0]["title"] or "无标题",
                             "、".join(already[0]["buttons"]) or "没读到")}
    restored = False
    boxes: list[str] = []
    buttons: list[str] = []
    exit_boxes = already
    if not exit_boxes:
        # **最小化的主窗口会直接无视 `SC_CLOSE`**（10-13 实测第一次请退就没退掉，窗口停在
        # -32000,-32000 那一档）。先恢复再发退出请求；恢复只是把窗口放回屏幕，不点任何东西。
        if win32.is_iconic(frames[0].hwnd):
            restored = bool(win32.restore(frames[0].hwnd))
            time.sleep(1.5)
        win32.close_window(frames[0].hwnd, syscommand=True)      # 等价于人点标题栏的 X
        for _ in range(max(1, settle_sec // 2)):
            time.sleep(2)
            re = watchman.probe()
            if not re.get("running"):
                return {"acted": True, "closed": True, "pid": pid,
                        "reason": f"已请客户端退出（pid {pid}），重探确实不在了"}
            found = s8_finish.confirm_boxes(pid)
            exit_boxes, box_err = _exit_boxes_safe(pid)
            if found or exit_boxes:
                boxes = [(getattr(b, "text", "") or "")[:80] for b in found]
                break
        if not exit_boxes:
            for box in s8_finish.confirm_boxes(pid):
                buttons += [s8_finish.button_text(c)
                            for c in win32.children(box.hwnd, max_depth=4)
                            if c.visible and c.cls.startswith(locator.BUTTON_CLASSES)][:6]

    if exit_boxes:
        e0 = exit_boxes[0]
        shape = "%s：按钮 %s" % (e0["title"] or "无标题",
                                "、".join(e0["buttons"]) or "没读到")
        if not auto_click:
            return {"acted": True, "closed": False, "pid": pid, "dialogs": [e0],
                    "buttons": e0["buttons"], "exit_confirm": True, "box_error": box_err or None,
                    "restored_first": restored,
                    "reason": (f"已发退出请求（pid {pid}"
                               + ("，先把最小化的主窗口恢复了" if restored else "，窗口本来就是展开的")
                               + f"），客户端停在确认框上（{shape}）"
                               + "—— 这一档没开自动点退出，交人工决定；今天不再重复催")}
        ans = _answer_exit_confirm(e0)
        gone = _client_gone(pid, max(1, settle_sec // 2)) if ans.get("clicked") else False
        click_note = ("%s（%s）" % (ans.get("reason"), shape))
        if gone:
            return {"acted": True, "closed": True, "pid": pid, "exit_confirm": True,
                    "clicked_exit": True, "click": click_note, "dialogs": [e0],
                    "buttons": e0["buttons"], "box_error": box_err or None,
                    "reason": f"确认框已答（{click_note}），重探客户端确实不在了（pid {pid} 已退）"}
        return {"acted": True, "closed": False, "pid": pid, "exit_confirm": True,
                "clicked_exit": bool(ans.get("clicked")), "click": click_note,
                "dialogs": [e0], "buttons": e0["buttons"], "box_error": box_err or None,
                "restored_first": restored,
                "reason": (f"客户端停在确认框上（{shape}），点退出的结果：{ans.get('reason')}，"
                           f"进程还在（pid {pid}）"
                           + "—— 只认「退出」这一个按钮，不猜别的、不强杀，交人工")}

    return {"acted": True, "closed": False, "pid": pid, "dialogs": boxes,
            "buttons": buttons, "restored_first": restored, "box_error": box_err or None,
            "reason": (f"发了退出请求但客户端还在（pid {pid}，"
                       + ("先把最小化的主窗口恢复了再发的请求" if restored else "窗口本来就是展开的")
                       + "），"
                       + (f"而且挂着决定框 {boxes[0]!r}（按钮：{'、'.join(buttons) or '没读到'}）"
                          if boxes else "没看到决定框")
                       + ("（并且确认框探测本身坏了：%s ⇒ 上面那句「没看到」不算证据）" % box_err
                          if box_err else "")
                       + "—— 不猜按钮、不强杀，交人工点一次")}


def _maybe_close_client(today, *, ran_fill_all: bool = True) -> dict | None:
    """链收工那一刻：开关开着才动，一天只试一次（默认关，见 `daily_close.close_client_after`）。

    `ran_fill_all=False` 直接不动 —— "链没真跑过就关客户端"是最坏的一种顺手：
    客户端是 ③b（TQ 直取）与次日自动登录要用的东西。
    """
    global _client_closed_day
    from duanxian import jobs as jstore

    if not ran_fill_all:
        return None
    merged, _warns = jstore.settings("daily_close", jstore.DAILY_CLOSE_DEFAULTS)
    if not merged.get("close_client_after"):
        return None
    if _client_closed_day == today:
        return None
    _client_closed_day = today
    res = _close_tdx_client()
    jstore.record("daily_close_client", ok=bool(res.get("closed")),
                  detail={"reason": res.get("reason"), "closed": res.get("closed"),
                          "acted": res.get("acted"), "dialogs": res.get("dialogs"),
                          "buttons": res.get("buttons"),
                          "exit_confirm": res.get("exit_confirm"),
                          "clicked_exit": res.get("clicked_exit"),
                          "click": res.get("click"), "box_error": res.get("box_error"),
                          "trigger": "close_client_after（链收工那一路）"})
    if res.get("closed"):
        why = res.get("reason") or "客户端已退出（读数没给原因）"
        _gui("close_client", "链收工后关闭通达信客户端", "done",
             detail=why, target=str(today), supersedes=True)
        print("✅ %s" % why)
    elif res.get("acted"):
        why = res.get("reason") or "发了退出请求但客户端还在（读数没给原因）"
        _gui("close_client", "链收工后没关掉通达信客户端，需要人工点一次退出", "need_human",
             detail=why, target=str(today))
        print("⚠️ %s" % why)
    else:
        print("ℹ️ 链收工，但没关客户端：%s" % (res.get("reason") or "读数没给原因"))
    return res


# ⚠️ 用 lifespan 不用 @app.on_event（后者已废弃，FastAPI 升级后会静默不执行 →
# 盘中快照全天不抓，而界面上看不出任何异样）。
# 通达信进程看护 —— **只探测，绝不动界面**。
# 为什么必须补这一条：`app/tdx_client/watchman.py` 里 `probe()/heal()` 早就写好了，
# 但**全仓零引用**（`grep -rn watchman` 除它自己以外 0 命中），而且它自己 `enabled=False`。
# 于是用户问"tdx 进程有没有监控"，真实答案是"代码在、没人启动" —— 那等于没有。
# 这里只接**只读探测**：枚举进程/主窗口/登录框/同步子进程，写进 `jobs/state.json`（键 `tdx_watch`）
# 并让 `/api/jobs` 的 `host_loops` 能看见这条线程活着。
# ⚠️ **自愈 `heal()` 不接**：它会启动客户端、抢前台、用真实键鼠登录 ——
# 那件事必须由用户点头（红线见《系统配置工作指南/06-自动执行机制自查表》通达信那三条）。
_tdx_watch_thread_started = False
_tdx_watch_start_lock = threading.Lock()
TDX_WATCH_INTERVAL_SEC = 120


def _tdx_watch_enabled() -> bool:
    from duanxian import jobs
    return _loop_enabled("VIBE_TDX_WATCH", "tdx_watch", jobs.TDX_WATCH_DEFAULTS)


#: 通达信"该开着"的时间带：链前 `launch_lead_min + 这一档分钟数` 到链后这么多分钟。
#: 180 与宿主 `_daily_close_next` 里那条窗口 (`_plus_minutes(slot, 180)`) 是同一个数 ——
#: 链的窗口结束了，客户端就没有任何理由还开着。
TDX_EXPECT_AFTER_MIN = 180
TDX_EXPECT_BEFORE_MIN = 10


def _tdx_expected_now() -> tuple[bool, str]:
    """现在**该不该**有一台开着登录着的通达信。只有"该在而不在"才算看护的故障。

    为什么要有这一层（用户 2026-10-08 指出"失败了没人知道"的另一半：没失败却报失败）：
    夜里、周末、节假日客户端本来就该停着 —— 链跑完不再动它，而"收工后自动关"那一档默认关。
    探针把"没在跑"无条件回 `ok=False`，10-08 实测一晚上刷出 `连败 11`：
    一条按设计停在那里的循环天天红，真故障混在里面就看不出来了。
    `jobs.record` 的第三种结果（`detail['skipped']`）正是为这个形状准备的。

    三层判据，任何一层说不，就不该在：
      ① 今天开不开市 —— 与链自己同一份读法（`pg.trade_day_open`：判不了 → 链照跑 → 算该在）；
      ② 现在落在 `[链前 lead+10 分, 链后 180 分]` 这个时间带里；
      ③ 探测本身坏了**不算这一层**：调用点只在"确实读到没在跑"时才降级，
         读不到进程表是真故障，不许被这条口径掩盖。
    """
    from duanxian import jobs as jstore, pg, util

    # ⚠️ `util.china_today()` 的契约是**字符串** `'YYYY-MM-DD'`（全站十几处都当 str 用），不是 date。
    # 这里曾写成 `.isoformat()`：客户端一停，看护循环每 2 分钟在**记账之前**抛
    # `AttributeError`，异常被循环自己的 except 吞成一行 stdout ⇒ `jobs/state.json` 冻在最后一次
    # 成功那一刻（10-09 21:37 起 18 小时），而 `/jobs` 那格显示"正常·连败 0"—— 假绿。
    # 更要紧的是抛点在收进程/空壳回收/下载阶梯**之前**，那三件事也一起停摆了。
    today = util.china_today()
    hhmm = util.china_now().strftime("%H:%M")
    opened = pg.trade_day_open(today)
    cfg_w, _w = jstore.settings("tdx_watch", jstore.TDX_WATCH_DEFAULTS)
    if str(cfg_w.get("client_mode") or "on_demand") == "keep_alive":
        # 「保持运行」这一档覆盖掉时间带：开市的日子里全天候都算"该在"，这样坏了才有轮次去重启它。
        # 但**不开市的日子不覆盖** —— 节假日没有数可取，让客户端整夜挂着只是费电，
        # 而且日历判不了（`opened is None`）时按"开不开都可能"处理：照样保持，交给时间带之外的判据出声。
        if opened is False:
            return False, (f"client_mode=keep_alive，但 {today} 按日历不开市 —— "
                           "没有数可取，不必让它整夜挂着")
        return True, ("client_mode=keep_alive：开市日全天候保持运行，"
                      "探测会判「停在登录框 / 窗口不响应（假死）/ 交易时段里 vipdoc 不往前写」并据此重启")
    if opened is False:
        return False, f"{today} 按日历不开市，通达信没有理由开着"
    # 时间带**可以在面板上拨**（用户 2026-10-19：「允许 9 点到 19 点都可以保持在线」）。
    # 两格都留空＝按收盘链推导（下面那三行旧算式，15:49–18:59 就是这么来的）；
    # 只拨一格就只替换那一头 —— 半配的状态比"要么全配要么全不配"更常见，也不该逼用户填两格。
    band_lo = str(cfg_w.get("client_band_lo") or "").strip()
    band_hi = str(cfg_w.get("client_band_hi") or "").strip()
    slot = _daily_close_slot()
    cfg, _warns = jstore.settings("daily_close", jstore.DAILY_CLOSE_DEFAULTS)
    lead = int(cfg.get("launch_lead_min") or 0)
    lo = band_lo or _plus_minutes(slot, -(lead + TDX_EXPECT_BEFORE_MIN))
    hi = band_hi or _plus_minutes(slot, TDX_EXPECT_AFTER_MIN)
    # 只拨了一头也可能把带子配反（例：lo=19:00 而另一端仍是推导出来的 18:59）——
    # 那时 `lo <= hhmm <= hi` 恒假 = "整天都不在带内"，屏幕上「带外主动关」那句照样通顺，
    # 而客户端永远不会开着。**这种必须出声，不许静默按"今天不该开"处理。**
    if lo > hi:
        return False, (f"⚠️ 配出来的时间带是反的（开始 {lo} 晚于结束 {hi}，另一头按链推导）—— "
                       "这样会被读成「整天都不在带内」，通达信永远不会被保持开着。"
                       "请把两格填成一对（例 09:00 / 19:00）或都留空。")
    inside = lo <= hhmm <= hi
    basis = "面板拨的" if (band_lo or band_hi) else "按收盘链推导的"
    return inside, (f"通达信该开着的时间带（{basis}）是 {lo}–{hi}（现在 {hhmm}，"
                    f"链时点 {slot}、提前 {lead} 分钟）")


# ---------------------------------------------------------------- 两档客户端模式的判据与动作
#
# 用户 2026-10-13 点名的那个开关。两档互斥，默认 `on_demand`（需要时才运行）：
#   · 为什么原来会"一直挂着"：`tdx_watch.auto_launch` 在 15:59 把客户端拉起并登录，而
#     "链收工后自动关"（`daily_close.close_client_after`）**默认关** —— 没人去关它。10-12 实测
#     `TdxW.exe` pid 40672 从 15:59:22 挂到深夜，主窗口句柄 0、Responding=True。
#   · 为什么旧的看护不会重启它：探针只判"在不在 + 有没有停在登录框"，
#     **假死（窗口不响应）与连不上服务器（vipdoc 不往前写）都不在它的能力范围**，
#     所以"挂着但用不了"这种最坏形状没人管。下面两条判据就是补这一块的。

#: 盘中判"数据没往前走"的两个时段（含集合竞价尾巴）。收盘后 vipdoc 本来就不动，
#: 拿它判连不上服务器就是天天误报 —— 与 `sector_member_valid` 那条"永远红的规则叫噪音"同教训。
TDX_MARKET_WINDOWS = (("09:30", "11:35"), ("13:00", "15:05"))
#: 交易时段里 vipdoc 的"已到目标日"抽样数多久没往前走就算坏（分钟）。
#: 30 是保守值：正常盘中分钟线落盘的间隔远小于这个数。
TDX_DATA_STALE_MIN = 30
#: 「需要时才运行」带外关客户端前要求的最长空档（秒）。人在打字就别关（10-06 那句条件）。
TDX_IDLE_GATE_SEC = 600

#: keep_alive 的重启小账本（跨轮记忆，进程重启就归零 —— 归零不危险，反正上限是防死循环）。
_keep_alive_restarts: dict = {"day": None, "count": 0}
#: vipdoc 往前走的时间戳（`_tdx_data_stale` 用），同上一样是进程内的。
_vipdoc_watch: dict = {"count": None, "advanced_at": None}
#: 「带外主动关客户端」一天只试一次（与 `_client_closed_day` 同形；关不掉由待人工清单说）。
_client_down_day = None


def _in_market_window(hhmm: str) -> bool:
    return any(lo <= hhmm <= hi for lo, hi in TDX_MARKET_WINDOWS)


def _tdx_data_stale(hhmm: str, *, now_ts: float | None = None) -> str | None:
    """交易时段里 vipdoc 已经 `TDX_DATA_STALE_MIN` 分钟没往前写 ⇒ 一句原因；否则 None。

    三条纪律：① 只在盘中判；② **读不出数交 None**（判不了不算坏，不许拿"取不到"当"坏了"
    去重启客户端 —— 那是 `#111` 那条"探不到≠false"的同一个洞）；③ 每轮只采一次样，
    不睡 20 秒（`_vipdoc_still_growing` 那道睡是"收进程前确认没在下"用的，不该搬进 2 分钟一轮的探测）。
    """
    import time as _t

    if not _in_market_window(hhmm):
        return None
    from app.tdx_client import readiness

    try:
        v = readiness.Readiness(extra_samples=12).day_ready(readiness.expected_trade_date())
        target = v.target
        count = sum(1 for p in v.probes if p.fresh(target))
    except Exception:  # noqa: BLE001  读不出数就不是判据，别拿它当"连不上"
        return None
    now_ts = now_ts if now_ts is not None else _t.time()
    prev = _vipdoc_watch.get("count")
    if prev is None or count > prev:
        _vipdoc_watch["count"] = count
        _vipdoc_watch["advanced_at"] = now_ts
        return None
    _vipdoc_watch["count"] = count
    idle_min = int((now_ts - float(_vipdoc_watch.get("advanced_at") or now_ts)) / 60)
    if idle_min >= TDX_DATA_STALE_MIN:
        return (f"盘中 {idle_min} 分钟里 vipdoc「已到目标日」的文件数停在 {count} 没往前走"
                "（连不上服务器 / 掉登录 / 客户端不再收数）")
    return None


def _tdx_stuck_reason(st: dict, hhmm: str) -> tuple[str | None, dict]:
    """客户端**开着但用不了**的原因（假死 / 不再收数），连同读数一起交出去进台账。

    登录框那一档不在这判据里：`_tdx_probe_round` 已经把它当成"不通"，两处各判会打架。
    """
    read: dict = {"hung": None}
    reasons: list[str] = []
    try:
        from app.tdx_client import locator, win32

        main = locator.main_window()
        hung = win32.window_hung(main.hwnd) if main else None
    except Exception as exc:  # noqa: BLE001  探测工具自己坏了不算客户端坏了
        hung = None
        read["hung_error"] = f"{type(exc).__name__}: {str(exc)[:90]}"
    read["hung"] = hung
    if hung is True:
        reasons.append("主窗口消息线程不响应（假死，人点它也没反应）")
    stale = _tdx_data_stale(hhmm)
    if stale:
        reasons.append(stale)
    return ("；".join(reasons) or None), read


def _tdx_keep_alive_restart(detail: dict) -> dict | None:
    """`keep_alive` 那一档的动作：判坏了就收掉再拉起、登录、清弹窗，并**当天限量**。

    三道闸，缺一不可：
      ① `tdx_watch.auto_restart` 关着 ⇒ 明确不让自动重启（只做"查"），写待人工清单；
      ② 收盘链/阶梯正在跑 ⇒ 不抢它的客户端（`_restart_tdx` 内部还会再判一次）；
      ③ 当天已重启 `restart_max_per_day` 次 ⇒ 停手，把"又判坏但不再自动重启"写出来
         —— 没这道闸，夜里会形成「收→起→登录→又判坏」的死循环，每轮都动界面。
    """
    from duanxian import jobs as jstore
    from duanxian import util as _util

    cfg, _w = jstore.settings("tdx_watch", jstore.TDX_WATCH_DEFAULTS)
    limit = int(cfg.get("restart_max_per_day") or 0)
    today = _util.china_today()          # 全站契约：这是字符串 'YYYY-MM-DD'，不是 date
    if _keep_alive_restarts.get("day") != today:
        _keep_alive_restarts.update({"day": today, "count": 0})
    if limit <= 0:
        _gui("restart_client", "keep_alive 判坏但 restart_max_per_day=0（只查不重启）",
             "need_human", detail=str(detail.get("reason") or ""), target=today)
        return {"did": False, "reason": "restart_max_per_day=0 —— 只查不重启（已写待人工清单）"}
    if _keep_alive_restarts["count"] >= limit:
        return {"did": False,
                "reason": f"今天已自动重启 {limit} 次，不再重启（再试就是死循环）——等下一次收盘链或人工"}
    _keep_alive_restarts["count"] = int(_keep_alive_restarts["count"]) + 1
    res = _restart_tdx(f"keep_alive 判坏：{detail.get('reason')}", trigger="keep_alive",
                       count_key="tdx_keep_alive_restart")
    res["did"] = True
    res["attempt"] = _keep_alive_restarts["count"]
    return res


#: 「每日定时重启」当天只做一次（进程内跨轮记忆，与 `_keep_alive_restarts`/`_client_down_day` 同形）。
_daily_restart_day = None


def _tdx_daily_timed_restart() -> dict | None:
    """**每天定时把通达信整个重启一次**（用户 2026-10-19 点名：「再加一个策略，定时自动重启通达信，
    默认时间是下午 15 点 38 分」）。到点就走唯一出口 `_restart_tdx`（收进程 → 重新起 → 登录 → 清弹窗），
    不在这里新写一套关/起 —— 那条路 10-06 已经证明"两处各写一遍迟早打架"。

    为什么默认排在 **15:38**：它落在收盘链（15:59）与其提前量之前，链拿到的是一台刚登录好、
    窗口状态干净的客户端；而它又排在 15:00 收盘之后，不会打断盘中看盘。

    三道闸（缺一条就**出声说明为什么不做**，不静默跳过）：
    ① `restart_daily_enabled=false` → 不做；
    ② pytest 进程里硬挡 —— 这个执行体会 `taskkill` 真收客户端，测试绝不能碰到（同
       `_close_tdx_client`/`_answer_exit_confirm`/`jobs._spawn_stage` 那四个洞一族）；
    ③ 当天已经做过就不重复（循环每 2 分钟一轮，到点后不挡会重启一百次）—— 判据读的是**作业台账**
       而不是进程内存，否则服务一重启这份记忆就没了，15:38 之后每次重启都白动一遍界面。
    链忙（`stage_busy`）与 `auto_restart=false` 那两道在 `_restart_tdx` 里面判，这里不重复。
    """
    global _daily_restart_day
    import os

    from duanxian import jobs as jstore, pg, util
    merged, _w = jstore.settings("tdx_watch", jstore.TDX_WATCH_DEFAULTS)
    if not merged.get("restart_daily_enabled", True):
        return None
    at = str(merged.get("restart_daily_at") or "").strip()
    if not at:
        return {"reason": "restart_daily_at 是空的，这一手没处对时（面板上填一个 HH:MM）"}
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {"skipped": True, "reason": "pytest 进程里不真重启通达信（执行体会 taskkill 收客户端）"}
    hhmm = util.china_now().strftime("%H:%M")
    today = util.china_today()
    if hhmm < at:
        return None                                   # 还没到点，一句话都不必说
    if _daily_restart_day == str(today):
        return None                                   # 今天做过了（进程内快路，不必每两分钟读一次文件）
    # 「今天做过」不能只住在进程内存里：8910 一重启它就清零，而 15:38 之后每一次重启都会再走
    # 一遍收进程→重新起→登录→清弹窗。10-26 实测同一夜做了三次（15:39 / 20:05:48 / 20:15:22，
    # 台账 `tdx_daily_restart` 的 runs 三条都在，前两次是别的原因起的重启带出来的）。
    # 真源是作业台账里那一次的落笔时间 —— 它本来就由这一手自己写进去（`_restart_tdx` 的 `count_key`）。
    last_run = str(((jstore.state().get("tdx_daily_restart") or {}).get("last_run_at") or ""))[:10]
    if last_run == str(today):
        _daily_restart_day = str(today)               # 补上快路，今天后面的轮次不再读文件
        return None
    opened = pg.trade_day_open(today)
    if opened is False:
        return {"skipped": True,
                 "reason": f"{today} 按日历不开市，{at} 这一手不空转（周末重启一次只是白动界面）"}
    _daily_restart_day = str(today)
    res = _restart_tdx(f"每日定时（{at}）", trigger="daily_timed", count_key="tdx_daily_restart")
    res["did"] = True
    return res


def _tdx_close_when_idle(today) -> dict | None:
    """`on_demand` 那一档的动作：**时间带外**还开着，就主动请它退出。

    三道闸：
      ① 今天已经关过一次（或已经交过人工）就不再反复点它 —— `_client_down_day`；
      ② 人最近碰过键盘鼠标（`input_idle_sec()` < `TDX_IDLE_GATE_SEC`）⇒ **不关**，
         写待人工清单。这是用户 10-06 那句"万一我当时也在操作电脑"的落点：
         顺手关掉一个正在被使用的客户端，比多开着一个窗口糟得多；
      ③ 真关的时候走 `_close_tdx_client()` 那套（温和点 X、不强杀、有决定框交人工、
         同步中/vipdoc 还在写都不动）。
    """
    global _client_down_day
    from duanxian import jobs as jstore

    if _client_down_day == today:
        return None
    from app.tdx_client import win32

    try:
        idle = win32.input_idle_sec()
    except Exception as exc:  # noqa: BLE001  判不了空闲 = 保守不关
        return {"closed": False, "reason": f"读不到键鼠空闲时间（{type(exc).__name__}）→ 不关"}
    if idle is None:
        return {"closed": False, "reason": "键鼠空闲时间判不了 → 不关（判不了不等于「没人用」）"}
    if idle < TDX_IDLE_GATE_SEC:
        _client_down_day = today
        why = (f"客户端在时间带外还开着，但你 {int(idle)} 秒前还在动键盘鼠标 → 不关；"
               f"要程序自动关就把 tdx_watch.client_mode 保持 on_demand 并在空闲超过 "
               f"{TDX_IDLE_GATE_SEC // 60} 分钟的时段再判一次")
        _gui("close_client", "带外该关客户端，但此刻有人在用", "need_human",
             detail=why, target=str(today))
        jstore.record("daily_close_client", ok=False,
                      detail={"skipped": True, "trigger": "on_demand", "reason": why})
        return {"closed": False, "skipped": True, "reason": why}
    _client_down_day = today
    res = _close_tdx_client()
    # 三种结果分开记（同 `jobs.record` 的 ok / fail / skipped 三档）：
    #   关掉了 = 成；发了请求但停在确认框且点不动 = 失败（该出声，桌上正挂着东西）；
    #   根本没动手（找不到主窗口 / 本来就没在跑 / 开关关着且已有框）= 观望，不涨连败。
    closed = bool(res.get("closed"))
    acted = bool(res.get("acted"))
    jstore.record("daily_close_client", ok=closed,
                  detail={"skipped": (not closed and not acted) or None,
                          "trigger": "on_demand（时间带外主动关）",
                          "reason": res.get("reason"), "closed": closed,
                          "acted": acted, "dialogs": res.get("dialogs"),
                          "buttons": res.get("buttons"),
                          "exit_confirm": res.get("exit_confirm"),
                          "clicked_exit": res.get("clicked_exit"),
                          "click": res.get("click"),
                          "box_error": res.get("box_error")})
    if res.get("closed"):
        # 这一笔 `done` 带 `supersedes`：同一步早先那次"需要人工点一次退出"到这里已经做完了，
        # 留在待人工清单上是一句假警报（人会为它把刚关掉的客户端再打开）。
        _gui("close_client", "带外已关闭通达信客户端", "done",
             detail=str(res.get("reason") or ""), target=str(today), supersedes=True)
        print("✅ %s" % (res.get("reason") or "带外已关闭通达信"))
    elif res.get("acted"):
        _gui("close_client", "带外该关通达信但没关掉，需要人工点一次退出", "need_human",
             detail=str(res.get("reason") or ""), target=str(today))
        print("⚠️ %s" % (res.get("reason") or "发了退出请求但客户端还在"))
    else:
        print("ℹ️ 带外该关通达信，但没动手：%s" % (res.get("reason") or "读数没给原因"))
    return res


def _tdx_probe_round() -> tuple[bool, dict]:
    """探测一次，返回 `(是不是"能取数"的状态, 交出去的记录)`。

    判"能不能取数"而不是"进程在不在"：**开着但停在登录框上，TQ 那条腿照样取不到数**
    （收盘链 ③b 要靠已登录的客户端）。所以 `running 且 login_box=False` 才算通。
    """
    from app.tdx_client import watchman

    try:
        st = watchman.probe()
    except Exception as exc:  # noqa: BLE001  探测本身坏了也要出声，不能安静当作"没事"
        return False, {"reason": "探测本身失败：%s: %s" % (type(exc).__name__, str(exc)[:150])}
    running = bool(st.get("running"))
    login_box = bool(st.get("login_box"))
    ok = running and not login_box
    stuck = None
    if ok:
        # 第三道判据（10-13 新增）：**开着、也登录了，但用不了** —— 假死或不再收数。
        # 原来这一格会报"正常·连败 0"，而 TQ 那条腿其实取不到数：那是最坏的一种假绿。
        from duanxian import util as _util
        stuck, stuck_read = _tdx_stuck_reason(st, _util.china_now().strftime("%H:%M"))
        detail_read = stuck_read
        if stuck:
            ok = False
    else:
        detail_read = {}
    detail = {"running": running, "pid": st.get("pid"), "login_box": login_box,
              "minimized": st.get("minimized"), "sync": st.get("sync"),
              "window": st.get("window"), **detail_read,
              # `watchman.heal()` 这条老路仍然没接（它会抢前台发键鼠）。
              # 但"没在跑就没人管"已经不是事实：10-06 起，**收盘链 ③b 的前置会自己
              # 拉起并登录**（开关 `tdx_watch.auto_launch`），每一次动作进待人工清单。
              "heal": "watchman.heal 未接；拉起并登录由收盘链 ③b 前置做（tdx_watch.auto_launch）"}
    if not running:
        detail["reason"] = ("通达信没在跑 —— vipdoc 不会再刷新，收盘链的 ③b（TQ 直取）"
                            "也要它登录着才有数；到点那条链会自己把它拉起来并登录"
                            "（不想让它动界面就把 tdx_watch.auto_launch 关掉）")
    elif login_box:
        detail["reason"] = "客户端开着但停在登录框上（TQ 取数此刻不可用；链会自己补登录）"
    elif stuck:
        # 10-13 新增的这一档：**"开着且登录着"不等于"能取数"**。
        # 假死与"不再往前写"从前都会报成"正常·连败 0"，那是最坏的一种假绿。
        detail["reason"] = f"客户端开着（pid {st.get('pid')}）但用不了：{stuck}"
        detail["stuck"] = True
    else:
        detail["reason"] = "在跑（pid %s）· 同步子进程：%s" % (st.get("pid"), st.get("sync") or "无")
    return ok, detail


def _sync_process_age(pid: int) -> float | None:
    """`TPyth*` 已经活了多久（秒）。读不到创建时间就交回 `None` —— **宁可不杀也不能瞎杀**。"""
    try:
        import psutil
        return max(0.0, time.time() - psutil.Process(pid).create_time())
    except Exception:  # noqa: BLE001  进程刚好退了 / 权限读不到
        return None


def _vipdoc_still_growing(*, gap_seconds: int = 20) -> bool | None:
    """文件还在不在往前写：隔 `gap_seconds` 各数一次"尾部日期已到目标日"的抽样数。

    为什么要有这一道：**光看进程活了多久会误杀正在下载的**。40 分钟这条线是用户给的
    （"时间设置长一点"），但万一那天网络慢、真在下，收了就前功尽弃。
    交回 `True` = 还在长（别动）；`False` = 没动（可以收）；`None` = 判不了（也别动）。
    """
    from app.tdx_client import readiness

    def fresh_count():
        try:
            v = readiness.Readiness(extra_samples=20).day_ready(
                readiness.expected_trade_date())
            target = v.target
            return sum(1 for p in v.probes if p.fresh(target))
        except Exception:  # noqa: BLE001
            return None

    first = fresh_count()
    if first is None:
        return None
    time.sleep(gap_seconds)
    second = fresh_count()
    if second is None:
        return None
    return second > first


def _reap_stale_sync(*, force: bool = False) -> dict | None:
    """**用户 2026-10-06 点名的那件事**：盘后下载跑完（或跑到一半死了）之后，
    「管理员: TPyth-可以获取各类字段」那个进程常常还赖着不走 —— 要求是
    "结束了你帮我关掉，或者定时去关闭，时间设置长一点"。

    三道闸，缺一个都不动手：
    1. **没有收盘链在跑**（`jobs.stage_busy()`）—— 那个进程就是链自己叫起来的；
    2. 活了超过 `tdx_watch.sync_reap_after_min`（默认 40 分钟，远长于一轮分钟线 7′41″ + 转档）；
    3. **vipdoc 没在往前写**（还在长就是正在下，收了等于掐断）。
    ⚠️ 只收 `TPyth*`，**绝不动 `TdxW.exe`**：TQ 那条腿靠主客户端登录着才能取数
    （`tq_bridge.available()` 的判据就是 `TdxW.exe` 在不在），杀错一个就把 ③b 弄瘫了。
    """
    from app.tdx_client import win32
    from app.tdx_client.locator import SYNC_EXE_PREFIX
    from duanxian import jobs as jstore

    merged, _warns = jstore.settings("tdx_watch", jstore.TDX_WATCH_DEFAULTS)
    after_min = int(merged.get("sync_reap_after_min", 40) or 0)
    if after_min <= 0 and not force:
        return None                                   # 0/负数 = 明确关掉这件事（阶梯除外）
    pids = [p for _n, p in win32.pids_by_prefix(SYNC_EXE_PREFIX)]
    if not pids:
        return None
    busy = jstore.stage_busy()
    ages = {p: _sync_process_age(p) for p in pids}
    stale = {p: a for p, a in ages.items() if a is not None and a >= after_min * 60}
    out = {"pids": pids, "ages_seconds": {str(p): (round(a) if a else None) for p, a in ages.items()},
           "after_min": after_min, "stage_busy": busy, "killed": []}
    if busy:
        out["reason"] = f"收盘链 {busy} 正在跑，这个进程是它叫起来的 → 不收"
        return out
    if not stale and not force:
        out["reason"] = (f"在跑但没到 {after_min} 分钟（最老 {max([a for a in ages.values() if a] or [0]):.0f}s）→ 不收")
        return out
    if force:
        stale = {p: a for p, a in ages.items() if a is not None} or {p: 0.0 for p in pids}
    elif _vipdoc_still_growing() is not False:
        out["reason"] = f"vipdoc 还在往前写（或判不了）→ 不收 {sorted(stale)}"
        return out
    from duanxian.proc import kill_tree
    for pid in sorted(stale):
        note = kill_tree(pid)
        out["killed"].append({"pid": pid, "age_seconds": round(stale[pid]), "how": note})
        print(f"🧹 收了赖着不走的通达信同步进程 pid={pid}（活了 {stale[pid] / 60:.0f} 分钟）：{note}")
    out["reason"] = ("已收 %d 个 TPyth* 进程（%s）；主客户端 TdxW 未动" % (
        len(out["killed"]),
        "数据已判全，阶梯直接收" if force else
        f"活了超过 {after_min} 分钟且文件不再往前写"))
    jstore.record("tdx_sync_reap", ok=bool(out["killed"]),
                  detail={"reason": out["reason"], "killed": out["killed"]})
    return out


def _reap_plugin_shells() -> dict | None:
    """收了通达信客户端 AutoRun 留下的**空壳控制台**（用户 2026-10-08 授权）。

    那一行是 `cmd.exe /k "python -u D:\\new_tdx64\\PYPlugins\\user\\Get_DayEnd.py --run_tdx 1"`，
    `/k` 让它跑完也不自己关 —— 10-08 实测它从 16:00 一直挂到晚上，里面 python 早退了，
    只剩一个 conhost。判据与四道闸全在 `app/tdx_client/shells.py`，这里只负责时机与留痕。

    贵的那一步（数 vipdoc 判"数据全没全"）**只在真看到壳的时候才做**：这条循环两分钟一轮，
    没壳的日子一次 PowerShell 就够。
    """
    from app.tdx_client import readiness, shells
    from duanxian import jobs as jstore

    merged, _warns = jstore.settings("tdx_watch", jstore.TDX_WATCH_DEFAULTS)
    if not merged.get("reap_script_shells", True):
        return None
    try:
        found = shells.find_plugin_shells()
    except Exception as exc:  # noqa: BLE001  探不到进程表就什么都不动，也别带崩探测循环
        return {"acted": False, "reason": f"探不到进程表（{type(exc).__name__}: {str(exc)[:80]}）→ 不收"}
    if not found:
        # "这一轮没有壳"就是它把活干完了。以前这里直接 return 不记账，于是**上一轮的 fail/连败
        # 会一直挂着**：桌面早就干净了，体检还在说"发了关闭请求但还在（pid ×××）、要人工看一眼"
        # （10-18 实测 pid 3896 已经不在，台账还停在 10-17 的 fail）。只在**确实有旧账要平**的时候
        # 补记一笔成功并抹掉待人工那条，两分钟一轮的空白轮不写，免得把台账刷成"没有壳"。
        import datetime as _dt

        prev = (jstore.state().get("tdx_shell_reap") or {})
        if str(prev.get("last_status") or "") != "ok":
            jstore.record("tdx_shell_reap", ok=True,
                          detail={"reason": "没有 PYPlugins 留下的壳（上一轮的账在这里平）",
                                  "killed": [], "left": [], "cleared_prev": str(prev.get("last_run_at") or "")})
            _gui("reap_plugin_shell", "空壳窗口已收净：本轮没看到壳", "done",
                 detail="桌面上没有 PYPlugins 留下的壳，上一轮的待人工那条作废",
                 target=str(_dt.date.today()), supersedes=True)
        return None
    import datetime as _dt

    complete, why = _vipdoc_download_complete(_dt.date.fromisoformat(str(readiness.expected_trade_date())))
    out = shells.reap(complete=complete, chain_busy=jstore.stage_busy(),
                      probe=lambda: found,             # 进场那一眼刚探过，别为同一件事再花一次 powershell
                      after=shells.find_plugin_shells)  # 收完必须重探真实现场：拿杀之前的快照复核＝必然"还在"，
                                                        # 于是天天记一条"发了关闭请求但还在"的假 need_human
    if out.get("killed"):
        _gui("reap_plugin_shell", "收掉通达信 AutoRun 留下的空壳控制台窗口", "done",
             detail=out.get("reason") or "", target=str(_dt.date.today()))
        print("🧹 %s" % out["reason"])
    elif out.get("left"):
        # 发了关闭请求但还在：这一格必须出声，否则就是"页面上说收了、桌上还开着"
        _gui("reap_plugin_shell", "空壳窗口没收掉，需要人工关", "need_human",
             detail=out.get("reason") or "", target=str(_dt.date.today()))
        print("⚠️ %s" % out["reason"])
    verdict = out.get("outcome") or ("ok" if out.get("killed") else "fail")
    jstore.record("tdx_shell_reap", ok=verdict == "ok",
                  detail={"skipped": verdict == "skipped",
                          "reason": out.get("reason"), "killed": out.get("killed"),
                          "left": out.get("left"), "still_running_scripts":
                          out.get("still_running_scripts"), "complete": complete,
                          "complete_why": why})
    return out


#: 用户 2026-10-06 给的阶梯，原文：「大概下载了 10 分钟之后你就应该去检测对应的数据是否全了，
#: 全了就关闭进程。如果不全，就再等 10 分钟，再检测如果数据全了就关闭，
#: 如果不全，就关闭重新试一次（我指的是通达信相关的全重启一下）」
#: ⇒ 一档 10 分钟、最多两档，两档都不全就**全重启通达信**再让它下一档自己下。
TDX_LADDER_CHECK_SEC = 600
TDX_LADDER_MAX_CHECKS = 2
#: 一轮"正在下载"的现场。进程活着才算一轮，进程一消失就清零 ——
#: 不然上一天的判断会串到下一天。
_ladder: dict = {'target': None, 'checks': 0, 'next_at': 0.0}


def _vipdoc_download_complete(target) -> tuple[bool, str]:
    """**"数据全了"的判据**：日期到了 + **整目录条数不比基线少**，日线与 1/5 分钟线都要。

    ⚠️ 这里**不看 `TPyth*` 在不在** —— 这一档要回答的就是"进程还活着，但数是不是已经齐了"，
    齐了才去关它。判"齐不齐"用的是 `readiness` 的尾部日期 + `duanxian/completeness.py`
    的条数规则，不是窗口文字、不是文件 mtime、也不是退出码（09-27 实测过：窗口关了而
    `.day` 还是上一天）。

    条数这一道是用户 2026-10-06 点名换掉的：原先这里看"抽样 40 只里 90% 到齐"，
    抽到的票恰好全停牌就会误判；现在是**数整个 vipdoc 目录**，并且基线是系统自己
    每天登记下来的条数（`>= 前第 2 个交易日`，新股只会让数往上走）。实测 2026-09-30：
    `.day` 9,490 / `.lc1` 9,398 / `.lc5` 9,398 个文件已到那天。
    """
    from app.tdx_client import readiness
    from duanxian import completeness

    h = readiness.Readiness()
    parts = {'个股日线': h.day_ready(target), '指数日线': h.index_ready(target),
             '1 分钟线': h.minute_ready(target, period=1),
             '5 分钟线': h.minute_ready(target, period=5)}
    missing = [k for k, v in parts.items() if not v.ok]
    if missing:
        return False, '还没到那天：%s' % '、'.join(missing)
    check = completeness.check_vipdoc(target)
    if not check["complete"]:
        return False, ('日期到了但条数没过：' + '；'.join(check["missing"]))
    return True, '%s —— %s' % (
        '、'.join('%s %s 个' % (completeness.KINDS[k]["label"], f"{n:,}")
                  for k, n in sorted(check["counts"].items())),
        '、'.join(v["why"] for v in check["verdicts"].values()))


def _restart_tdx(reason: str, *, trigger: str = "ladder",
                 count_key: str = "tdx_sync_restart") -> dict:
    """**全重启通达信**（关掉 → 重新起 → 登录 → 清弹窗），两个入口共用这一个执行体。

    调用方有两处：① 下载阶梯「两档都没下全」（`trigger="ladder"`，用户 10-08 点名的那一步）；
    ② `keep_alive` 那一档的看护判坏（假死 / 不再收数）。
    **不在这这里点"开始下载"** —— 那是收盘链/人的事；重启之后 vipdoc 的登录后自动同步
    自己会补一段，链的下一档也会重新判一次。四道闸：
    ① 有收盘链在跑就不动（它自己正在操作这个客户端，两个手会打同一个窗口）；
    ② `tdx_watch.auto_restart=false` 就是明确不让自动重启（只做"查+关进程"那两步）；
    ③ 判据自己坏了（探测抛错）不进这里 —— 调用点只在**真读到坏**时才叫；
    ④ keep_alive 另有当天上限（`_tdx_keep_alive_restart`），这里不重复判。
    """
    import subprocess
    import sys

    from app.tdx_client.win32 import NO_WINDOW
    from duanxian import jobs as jstore
    from duanxian.proc import kill_tree

    busy = jstore.stage_busy()
    if busy:
        return {"restarted": False, "reason": f"收盘链 {busy} 正在跑，不抢它的客户端"}
    merged, _w = jstore.settings("tdx_watch", jstore.TDX_WATCH_DEFAULTS)
    if not merged.get("auto_restart", True):
        _gui('restart_client', f'{trigger} 本该全重启通达信，但 tdx_watch.auto_restart=false',
             'need_human', detail=reason, target=None)
        if trigger == "ladder":
            _gui('ladder_wait', '两档都没下全，机器侧没路了', 'need_human',
                 detail=reason, target=None)
        return {"restarted": False,
                "reason": "tdx_watch.auto_restart=false —— 只查数据与关进程，不自动重启"
                          "（已写进待人工清单）"}
    root = Path(__file__).resolve().parent
    steps = []
    for argv, what, budget in (
            (['-m', 'app.tdx_client.reset'], '关掉通达信全部进程', 120),
            (['-m', 'app.tdx_client.run_agent', '--only', 's1_launch',
              '--only', 's2_login', '--only', 's3_popup'], '重新起 + 登录 + 清弹窗', 600)):
        proc = subprocess.Popen([sys.executable, '-u', *argv], cwd=str(root),
                                creationflags=NO_WINDOW)
        try:
            rc = proc.wait(timeout=budget)
        except subprocess.TimeoutExpired:
            rc = 124
            kill_tree(proc.pid)
        steps.append({'what': what, 'rc': rc, 'argv': argv})
        # 每一步都单独记：人要看的恰好是"哪一步没成"，而不是"整段重启失败"。
        _gui('restart_client' if 'reset' in argv[-1] else 's2_login',
             what, 'done' if rc == 0 else ('need_human' if rc in (2, 124) else 'failed'),
             detail='退出码 %s（124 = 超过 %d 秒已按进程树收掉）' % (rc, budget))
    head = (f"两档都没下全（{reason}）→ 已全重启通达信，下一档重新判" if trigger == "ladder"
            else f"{reason} → 已按进程树收掉并重新拉起登录，下一轮重新判")
    out = {"restarted": all(x['rc'] == 0 for x in steps), "steps": steps, "reason": head}
    if not out["restarted"]:
        _gui('ladder_wait' if trigger == "ladder" else 'restart_client',
             '全重启没做干净，下一档也不会自动补' if trigger == "ladder"
             else 'keep_alive 的全重启没做干净，需要人工看一眼',
             'need_human', detail=out["reason"])
    jstore.record(count_key, ok=out["restarted"], detail={"reason": out["reason"],
                                                          "trigger": trigger})
    print(("🔁 " if out["restarted"] else "⚠️ ") + out["reason"])
    return out


def _restart_tdx_for_retry(reason: str) -> dict:
    """下载阶梯那一档的调用点（保持原名字与原语义，实现已经与 keep_alive 共用一份）。"""
    return _restart_tdx(reason, trigger="ladder")


def _gui(step: str, what: str, status: str, *, detail: str = '',
         target: str | None = None, supersedes: bool = False) -> None:
    """**每一次动界面都往待人工清单记一笔**（用户 10-06 的条件："万一我当时也在操作电脑……
    也可以我主动的去找出来，哪些没执行的我手动执行下"）。

    记账本身坏了不许带崩动作 —— 界面上已经做完的事不能因为写文件失败就重做一遍。
    `supersedes` 只在"这件事后来由程序自己做完了"那条 `done` 上给（把同一步早先那次
    交人工的记录从待人工清单里抹掉，否则人会去补一件已经做完的事）。
    """
    try:
        from duanxian import gui_trail
        gui_trail.add(step=step, what=what, status=status, detail=detail,
                      target=target, source='server', supersedes=supersedes)
    except Exception as exc:  # noqa: BLE001
        print('⚠️ 待人工清单没记上（%s: %s）：%s / %s'
              % (type(exc).__name__, str(exc)[:80], step, what))


def _close_windows_now(target, *, force: bool = True) -> str:
    """**唯一的测试洞**：真发关窗/最小化的那一段就在这里。

    ⚠️ pytest 里没打桩走到这里会**硬挡**（同 `s8_finish.close_dialog` 里那一道）：
    测试机上"当时有没有通达信摊在桌面上"是不确定的，而 `cleanup()` 里除了关窗还会
    `win32.minimize(主窗口)` —— 那一下不在 `close_dialog` 的闸后面。用户 10-06 已经被
    "假 subprocess 不再被调用 → 测试真把通达信起起来"吓过一次（见 memory），
    所以这一层宁可挡死也不赌。
    """
    if os.environ.get('PYTEST_CURRENT_TEST') and not os.environ.get('TDX_ALLOW_TEST_CLICK'):
        return 'PYTEST 硬挡：不在用户桌面上关窗或最小化（请 monkeypatch server._close_windows_now）'
    from app.tdx_client import win32
    from app.tdx_client.steps import s8_finish

    pids = [p for _n, p in win32.pids_by_name('TdxW.exe')]
    return s8_finish.cleanup(pids[0] if pids else None, target=target, force=force)


def _close_download_windows(target, *, force: bool = True) -> dict:
    """**用户 10-06 点名的那一下**："下载好了就把弹窗取消"、"下载完之后关闭这个窗口"。

    动作走 数据获取 那一层已经加固过的 `s8_finish.cleanup`（精确标签 + `NEVER_CLICK` 黑名单
    + 决定框按判据答，绝不再按子串去点「取消下载」），这里只决定**什么时候**关：
    调用方必须先过了 `_vipdoc_download_complete` 那道条数门。
    关完再收 `TPyth*`：先温和地关窗，进程才有机会自己退。
    """
    out: dict = {'notes': '', 'ok': False}
    try:
        notes = _close_windows_now(target, force=force)
        out['notes'] = notes
        out['ok'] = ('对话框已关' in notes or '本来就没开' in notes
                     or '已是最小化' in notes)
    except Exception as exc:  # noqa: BLE001  关窗失败只是桌面上难看，不该带崩阶梯
        out['notes'] = '关窗这一步自己坏了：%s: %s' % (type(exc).__name__, str(exc)[:120])
    _gui('s8_finish', '数据已判全 → 关掉「盘后数据下载」/「TQ数据设置」窗口并最小化',
         'done' if out['ok'] else 'need_human', detail=out['notes'], target=str(target))
    return out


def _download_ladder_round() -> dict | None:
    """用户要的那架阶梯：**下载进行中 → 每 10 分钟查一次数据全不全**。

    · 全了 → 收 `TPyth*`（数据已经落盘，进程留着只会握着文件句柄）；
    · 没全且还有下一档 → 说清缺在哪，10 分钟后再查；
    · 两档都没全 → **全重启通达信**（`_restart_tdx_for_retry`）再让它自己下一遍。

    只在**没有收盘链在跑**时接管：链在跑的时候 p0 自己有 `STALL_LIMIT` 与 `--attempts`，
    两边同时动手就是两双手抢一个客户端。
    """
    from app.tdx_client import locator, readiness
    from duanxian import jobs as jstore

    if jstore.stage_busy():
        # 链在跑：p0 自己有 `STALL_LIMIT` 与 `--attempts`，这里不抢
        _ladder.update(target=None, checks=0, next_at=0.0)
        return None
    running, who = locator.sync_running()
    if not running:
        _ladder.update(target=None, checks=0, next_at=0.0)
        return None
    target = str(readiness.expected_trade_date())
    if _ladder['target'] != target:
        # 新的一天 = 新的一轮，从"第一次看到进程"起算 10 分钟
        _ladder.update(target=target, checks=0, next_at=time.monotonic() + TDX_LADDER_CHECK_SEC)
        return {'target': target, 'check': 0, 'action': 'watching',
                'reason': f'看到 {who} 在跑 → {TDX_LADDER_CHECK_SEC // 60} 分钟后查数据全不全'}
    if time.monotonic() < _ladder['next_at']:
        return None
    _ladder['checks'] += 1
    check = _ladder['checks']
    import datetime as _dt
    complete, why = _vipdoc_download_complete(_dt.date.fromisoformat(target))
    out = {'target': target, 'check': check, 'complete': complete, 'reason': why}
    if complete:
        # 顺序是**先关窗再收进程**：「盘后数据下载」是 TdxW 的对话框，
        # 温和点掉之后 TPyth 常常自己就退了；先杀进程的话窗口会一直挂着没人收，
        # 下一轮 s5 再点就是两层窗口叠着（那正是 10-06 用户截图里的形状）。
        closed = _close_download_windows(_dt.date.fromisoformat(target))
        killed = _reap_stale_sync(force=True)
        got = (killed or {}).get('killed')
        if got:
            _gui('reap_sync', '收掉赖着不走的 TPyth* 同步进程', 'done',
                 detail=(killed or {}).get('reason') or '', target=target)
        _ladder.update(target=None, checks=0, next_at=0.0)
        out['action'] = 'closed'
        out['closed'] = closed['notes']
        out['killed'] = got
        jstore.record('tdx_sync_ladder', ok=True,
                      detail={'reason': f'第 {check} 档查到数据已全 → 关窗 + 收进程：{why}',
                              'closed': closed['notes']})
    elif check >= TDX_LADDER_MAX_CHECKS:
        res = _restart_tdx_for_retry(why)
        _ladder.update(target=None, checks=0, next_at=0.0)
        out['action'] = 'restarted' if res.get('restarted') else 'restart-blocked'
        out['restart'] = res
    else:
        _ladder['next_at'] = time.monotonic() + TDX_LADDER_CHECK_SEC
        out['action'] = 'waiting'
        out['reason'] = f'第 {check} 档没下全（{why}）→ 再等 {TDX_LADDER_CHECK_SEC // 60} 分钟查第二次'
    print(('✅ ' if out.get('action') == 'closed' else '⚠️ ') + f'下载阶梯：{out["reason"]}')
    return out


def _tdx_watch_scheduler() -> None:
    """两分钟探一次；**状态翻转立刻记账，稳定期最多 15 分钟一次心跳**。
    每两分钟记一次会把 `consecutive_failures` 一天刷到几百，那个数字就失去意义了
    （同 `_newswire_journal` 的取舍，复用 `_newswire_due_to_record` 这同一份判据）。

    顺带干第二件事（用户 2026-10-06 点名）：**收掉赖着不走的 `TPyth*` 同步进程**，
    三道闸见 `_reap_stale_sync`。它只在真看到这个进程时才多花那 20 秒去判"还在写吗"。
    """
    last_ok: bool | None = None
    last_record = 0.0
    while True:
        try:
            from duanxian import jobs
            ok, detail = _tdx_probe_round()
            # 「该不该有一台开着且登录着的客户端」每轮都判一次。它同时喂两件事：
            # ① "没在跑"算不算故障（#85 那条夜间假红就修在这），② 两档模式要不要动手。
            # 这道闸自己抛错时**按"该在"处理**（= 不降级 = 出声），且绝不许把整轮带停：
            # 记账、收同步进程、收空壳、下载阶梯全排在这之后，10-10 就是被这里一句
            # `.isoformat()` 顶掉，台账冻了 18 小时而页面上仍是"正常·连败 0"。
            try:
                expected, why_expected = _tdx_expected_now()
            except Exception as exc:  # noqa: BLE001  判据坏了不算客户端坏了
                expected, why_expected = True, ""
                detail = dict(detail, gate_error="这道「该不该在」的闸自己坏了：%s: %s —— 不降级，照常出声"
                              % (type(exc).__name__, str(exc)[:120]))
            if not ok and detail.get("running") is False and not expected:
                # "没在跑"在这个时段是正常的 → 记成**按口径跳过**，不进连败。
                # 只降级这一种读数：停在登录框、假死、不再收数、探测本身坏了，都仍然是失败
                # （该出声的必须出声）。
                detail = dict(detail, skipped=True,
                              reason=f"{detail.get('reason')}｜不算故障：{why_expected}")
            try:
                reaped = _reap_stale_sync()
            except Exception as exc:  # noqa: BLE001  收进程这件事不许带崩探测
                reaped = {"reason": f"收同步进程这件事自己坏了：{type(exc).__name__}: {str(exc)[:120]}"}
            if reaped:
                detail["sync_reap"] = reaped.get("reason")
                if reaped.get("killed"):
                    detail["reason"] = f"{detail['reason']}（{reaped['reason']}）"
            # 第三件事（用户 10-08 授权）：收掉 AutoRun 留下的**空壳控制台**。
            # 它和上面那条不是一件事：`TPyth*` 是同步进程（要判它还在不在写文件），
            # 这个是客户端插件执行器留下的 cmd 窗口（脚本早退了、`/k` 把窗口留着）。
            try:
                shells_reaped = _reap_plugin_shells()
            except Exception as exc:  # noqa: BLE001  收壳不许带崩探测
                shells_reaped = {"reason": f"收空壳这件事自己坏了：{type(exc).__name__}: {str(exc)[:120]}"}
            if shells_reaped:
                detail["shell_reap"] = shells_reaped.get("reason")
                if shells_reaped.get("killed") or shells_reaped.get("left"):
                    detail["reason"] = f"{detail['reason']}（{shells_reaped['reason']}）"
            # 用户要的阶梯：下载进行中每 10 分钟查一次数据全不全，全了关进程、
            # 两档都不全就全重启通达信。它自己只在真看到 TPyth* 在跑时才动。
            ladder = _download_ladder_round()
            if ladder:
                detail["ladder"] = ladder
                if ladder.get('action') in ('closed', 'restarted', 'restart-blocked'):
                    detail["reason"] = f"{detail['reason']}（{ladder['reason']}）"
            # **两档客户端模式各自的动作**（用户 10-13 点名要的那个开关）。排在收进程/收空壳/
            # 下载阶梯之后、记账之前 —— 这样动作的原因能一起进同一格 detail，一页看得完。
            try:
                from duanxian import util as _util
                mode = str((jobs.settings("tdx_watch", jobs.TDX_WATCH_DEFAULTS)[0]
                            .get("client_mode") or "on_demand"))
                if mode == "keep_alive":
                    # 保持运行：判坏（没在跑 / 登录框 / 假死 / 不再收数）就重启，当天限量、每次都留痕
                    if not ok and not detail.get("skipped"):
                        act = _tdx_keep_alive_restart(detail)
                        if act:
                            detail["keep_alive_restart"] = act.get("reason")
                            detail["reason"] = f"{detail['reason']}（重启这一手：{act.get('reason')}）"
                elif detail.get("running") and not expected:
                    # 需要时才运行：时间带外还开着 ⇒ 主动请它退出（人在用就不关，改记待人工）
                    act = _tdx_close_when_idle(_util.china_today())
                    if act:
                        detail["client_down"] = act.get("reason")
            except Exception as exc:  # noqa: BLE001  动作这一手坏了不许带崩探测与记账
                detail["mode_action_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            # **每日定时重启**（用户 10-19 点名，默认 15:38，面板可改可关）。排在两档动作之后 ——
            # 上一手可能刚把客户端收掉或拉起，同一轮里不许两只手同时操作那个窗口。
            try:
                daily = _tdx_daily_timed_restart()
                if daily:
                    detail["daily_restart"] = ("已执行" if daily.get("did")
                                               else daily.get("reason") or "跳过")
            except Exception as exc:  # noqa: BLE001  这一手坏了不许带崩探测与记账
                detail["daily_restart_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            if _newswire_due_to_record(ok, last_ok, time.monotonic() - last_record):
                jobs.record("tdx_watch", ok=ok, detail=detail)
                last_ok, last_record = ok, time.monotonic()
            if not ok:
                print("⚠️ 通达信探测：%s" % detail.get("reason"))
        except Exception as exc:  # noqa: BLE001  这条线程死了不该带崩别的循环
            print(f"⚠️ 通达信探测循环异常（继续跑）：{type(exc).__name__}: {exc}")
        time.sleep(TDX_WATCH_INTERVAL_SEC)


def _start_tdx_watch() -> None:
    """幂等 —— 重复调用不会起第二条。"""
    global _tdx_watch_thread_started
    if not _tdx_watch_enabled():
        return
    with _tdx_watch_start_lock:
        if _tdx_watch_thread_started:
            return
        try:
            from app.tdx_client import watchman  # noqa: F401  只探在不在
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ 通达信看护未启动：watchman 导入失败（{type(exc).__name__}: {exc}）")
            return
        _tdx_watch_thread_started = True
        threading.Thread(target=_tdx_watch_scheduler, daemon=True,
                         name="tdx-watch").start()
        print(f"✅ 通达信看护已启动（只探测、不动界面，pid {os.getpid()}，"
              f"每 {TDX_WATCH_INTERVAL_SEC // 60} 分钟一次；自愈未接线）")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Acquire the process reservation at server startup, never on module import.
    # Imports/reloads used by existing tools and tests must remain side-effect free
    # with respect to the new Agent's worker and private database.
    manager = ReviewAgentManager(ReviewAgentStore(_REVIEW_AGENT_ROOT), Path(_REVIEW_DIR),
                                 ReviewAgentRuntime(_REVIEW_AGENT_ROOT))
    _app.state.review_agent = manager
    try:
        _start_intraday()
        _start_newswire()
        _start_watchlist_sync()
        _start_coverage_precompute()
        _start_ths_cloud_sync()
        _start_step_retry()
        _start_fundflow()
        _start_backup()
        _start_notify_push()
        _start_daily_close()
        _start_tdx_watch()
        yield
    finally:
        manager.shutdown()
        _app.state.review_agent = None


app.router.lifespan_context = _lifespan


@app.get("/api/intraday/auction")
def api_auction(date: str | None = None):
    """09:25 竞价核验。没有当日存档时会现抓一张（仅当天有效）。"""
    from duanxian import intraday

    try:
        return JSONResponse(intraday.auction_check(date))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"available": False, "reason": f"{type(exc).__name__}: {exc}"})


@app.get("/api/intraday/path")
def api_intraday_path(date: str | None = None):
    """当天的情绪路径（各时点快照串成线）。"""
    from duanxian import intraday

    try:
        return JSONResponse(intraday.path_summary(date))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"available": False, "reason": f"{type(exc).__name__}: {exc}"})


@app.post("/api/intraday/capture")
def api_intraday_capture(request: Request, slot: str | None = None):
    """手动抓一张快照（写操作，走 POST + Origin 校验）。"""
    if not _origin_ok(request):
        return JSONResponse({"error": "非法来源"}, status_code=403)
    from duanxian import intraday

    return JSONResponse(intraday.capture(slot))


def _mount_static() -> None:
    """挂静态资源 + SPA 兜底"""
    if not os.path.isdir(_DIST):
        print(f"ℹ️ 未找到前端构建产物（{_DIST}）。请先执行："
              f"cd frontend && npm install && npm run build")
        return
    app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")),
              name="assets")

    @app.get("/{full_path:path}")
    def _spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"error": f"未知接口 /{full_path}"}, status_code=404)
        # 真实存在的静态文件（favicon / manifest / 图片…）直接给
        candidate = safe_join(_DIST, full_path) if full_path else None
        if candidate and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_DIST, "index.html"))

    print(f"✓ React 构建产物已挂载（{_DIST}）")


_mount_static()

if __name__ == "__main__":
    import uvicorn

    # 端口可用 VIBE_PORT 覆盖，默认 8910（被占时不必改代码）
    port = int(os.environ.get("VIBE_PORT", "8910"))
    if _DISABLED_CLIS:
        # 有声地说出限制：不说的话，"少了几个可选项"看起来像 bug 而不是有意为之
        print(f"🔒 已禁用自动批准 CLI：{', '.join(_DISABLED_CLIS)}"
              f"（保留 {', '.join(sorted(_ALLOWED_CLI_KINDS))}）")
    if _opted_in_clis():
        # 放开了危险 CLI 就必须说清放开了什么、代价是什么 ——
        # 光印一句"保留 claude, codex"看不出后者是被特批进来的
        print(f"⚠️  VIBE_ALLOW_UNSAFE_CLI 已放开：{', '.join(sorted(_opted_in_clis()))}"
              f" —— 这些 CLI 会不经询问地读写文件、执行命令，"
              f"而问 AI 时抓来的外部新闻原文会原样进 prompt。确认你信任数据源再用。")
    print(f"→ 打开 http://127.0.0.1:{port}  （VR 分栏路由 {_VR_ROUTES} 条已并入）")
    # `python server.py` 这条路也必须按同一份配置决定绑哪儿：
    # 只让 `manage.py start` 认配置、这条路写死回环，就会出现"页面上开着、
    # 换个起法其实没开"（或反过来，以为没开其实开着）。
    uvicorn.run(app, host=_net.bind_host(), port=port)
