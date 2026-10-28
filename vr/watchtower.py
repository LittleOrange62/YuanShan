"""每日盯盘数据层 —— 盘中 3 秒轮询的实时监控（短线投资实例专属，不回推开源仓库）。

五个模块的标的池：持仓股（后端可读）/ 自选股（前端传入）/ 总市值≥500亿大票（东财市值榜，
每日一次）/ 前一交易日涨停梯队（固定全样本，附原板数）/ 昨日成交额前十。
持仓统一读交易日志；昨日梯队按行情场次和真实交易日历确定，缺口显式返回。

架构：常驻轮询线程只在交易时段活跃 —— 每 3 秒用腾讯批量行情（qt.gtimg.cn，L1 快照 3 秒
一帧、不封 IP、60 只/请求）拉全池，内存帧差分做异动检测（急拉急跌/触板开板，冷却去重），
异动事件当日落盘复盘。前端每 3 秒读内存快照（零上游请求，毫秒级响应）。
异动仅供用户在页面内主动查看，不提供系统通知、到价提醒或后台推送。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import astock
from vr import previous_ladder

BEIJING = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

_DATA_DIR = Path(os.environ.get("VR_DATA_DIR") or Path.home() / ".vibe-astock-agent/market-data") / "monitor"

POLL_SECONDS = 3          # L1 快照 3 秒一帧（沪深两所官方口径），更快无意义
SURGE_WINDOW = 180        # 急拉急跌观察窗（秒）
SURGE_PCT = 1.5           # 3 分钟涨跌超此值报异动（500亿大票口径）
ALERT_COOLDOWN = 300      # 同票同类型异动冷却（秒）
FRAME_KEEP = 120          # 每只票保留帧数（120 帧 ≈ 6 分钟）

_lock = threading.Lock()
_wake = threading.Event()   # 录入持仓/改自选后唤醒线程立即重建快照
_thread: threading.Thread | None = None
_extra_watch: list[str] = []      # 前端传入的自选股
_frames: dict[str, deque] = {}    # code -> deque[(ts, price, amount)]
_limit_state: dict[str, bool] = {}  # code -> 上一帧是否封涨停
_alerts: list[dict] = []          # 当日异动事件（新的在前）
_alert_last: dict[tuple, float] = {}
_snapshot: dict = {}              # 前端直接读的最新快照
_bigcaps_cache: tuple[str, list] | None = None   # (date, [{code,name}])
_turnover_saved_date = ""
_previous_cache: tuple[float, str, dict] = (0.0, "", {})
_previous_lock = threading.Lock()
_previous_worker: threading.Thread | None = None

# 整轮失败的读数。判据见 docs/系统配置工作指南/06-自动执行机制自查表.md §1：
# 捕住之后只 print 等于没说，页面看不见等于没出过声。
HEARTBEAT_SECONDS = 900            # 健康时也记账，否则「上次成功」越走越旧会报假故障
FAILURE_RECORD_EVERY = 20          # 连续失败时每隔这么多轮再记一次（盘中 3 秒一轮）
_loop_error: dict | None = None
_loop_streak = 0
_last_ok_at = 0.0
_jobs_module = None


# ---------------------------------------------------------------- 腾讯批量行情
def _prefix(code: str) -> str:
    if code[0] in ("6", "5", "9"):
        return "sh"
    if code[0] in ("4", "8") or code.startswith("92"):
        return "bj"
    return "sz"


def _tencent_batch(codes: list[str]) -> dict[str, dict]:
    """腾讯批量实时行情。返回 {code: {...}}；失败的批次静默跳过（下一轮自然补上）。"""
    out: dict[str, dict] = {}
    for i in range(0, len(codes), 60):
        batch = codes[i : i + 60]
        q = ",".join(_prefix(c) + c for c in batch)
        try:
            req = urllib.request.Request(f"https://qt.gtimg.cn/q={q}", headers={"User-Agent": UA})
            raw = urllib.request.urlopen(req, timeout=5).read().decode("gbk", "ignore")
        except Exception:  # noqa: BLE001
            continue
        for line in raw.split(";"):
            line = line.strip()
            m = re.match(r'v_(?:sh|sz|bj)(\d{6})="(.*)"', line)
            if not m:
                continue
            code, f = m.group(1), m.group(2).split("~")
            if len(f) < 49 or not f[3] or not f[32]:
                continue
            try:
                out[code] = {
                    "name": f[1],
                    "quote_time": f[30],
                    "high": float(f[33]) if f[33] else None,
                    "price": float(f[3]),
                    "prev_close": float(f[4] or 0),
                    "pct": float(f[32] or 0),
                    "amount": float(f[37] or 0) * 1e4,  # 万元 → 元（当日累计成交额）
                    "zt_price": float(f[47] or 0),
                    "dt_price": float(f[48] or 0),
                }
            except ValueError:
                continue
    return out


# ---------------------------------------------------------------- 标的池
def _holdings() -> list[dict]:
    # The journal is the same authority used by /api/positions and My Stocks.
    from duanxian.positions import open_positions
    return open_positions()


def _bigcaps() -> list[dict]:
    """总市值 ≥500 亿名单（东财市值榜降序翻页，每日一次；东财挂了用昨日落盘名单）。"""
    global _bigcaps_cache
    today = datetime.now(BEIJING).strftime("%Y%m%d")
    if _bigcaps_cache and _bigcaps_cache[0] == today:
        return _bigcaps_cache[1]
    out: list[dict] = []
    for page in range(1, 10):
        url = ("https://push2delay.eastmoney.com/api/qt/clist/get?pn=%d&pz=100&po=1&np=1"
               "&fltt=2&invt=2&fid=f20&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
               "&fields=f12,f14,f20" % page)
        try:
            r = astock.em_get(url, headers={"User-Agent": UA}, timeout=10)
            diff = (r.json().get("data") or {}).get("diff") or []
        except Exception:  # noqa: BLE001
            break
        if not diff:
            break
        stop = False
        for p in diff:
            cap = p.get("f20")
            if not isinstance(cap, (int, float)):
                continue
            if cap < 5e10:
                stop = True
                break
            out.append({"code": str(p.get("f12", "")), "name": p.get("f14", "")})
        if stop:
            break
    file = _DATA_DIR / "bigcaps.json"
    if out:
        _bigcaps_cache = (today, out)
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            file.write_text(json.dumps({"date": today, "stocks": out}, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass
        return out
    try:  # 东财不可用：回退最近一次落盘名单
        saved = json.loads(file.read_text())
        _bigcaps_cache = (today, saved["stocks"])
        return saved["stocks"]
    except Exception:  # noqa: BLE001
        return []


def _turnover_file() -> Path:
    return _DATA_DIR / "turnover_top.json"


def _turnover_top10_live() -> list[dict]:
    url = ("https://push2delay.eastmoney.com/api/qt/clist/get?pn=1&pz=10&po=1&np=1"
           "&fltt=2&invt=2&fid=f6&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
           "&fields=f12,f14,f6")
    try:
        r = astock.em_get(url, headers={"User-Agent": UA}, timeout=10)
        diff = (r.json().get("data") or {}).get("diff") or []
        return [{"code": str(p.get("f12", "")), "name": p.get("f14", "")} for p in diff]
    except Exception:  # noqa: BLE001
        return []


def _turnover_yesterday() -> tuple[str, list[dict]]:
    """昨日成交额前十。有收盘快照用快照；首日没有则用实时榜暂代（标注清楚）。"""
    today = datetime.now(BEIJING).strftime("%Y%m%d")
    try:
        saved = json.loads(_turnover_file().read_text())
        if saved.get("date") and saved["date"] != today and saved.get("stocks"):
            return "昨日成交前十", saved["stocks"]
    except Exception:  # noqa: BLE001
        pass
    live = _turnover_top10_live()
    return "成交前十（今日实时·首日暂代昨日）", live


def _save_turnover_snapshot() -> None:
    """收盘后存当日成交前十快照，供次日作「昨日前十」。每天存一次。"""
    global _turnover_saved_date
    today = datetime.now(BEIJING).strftime("%Y%m%d")
    if _turnover_saved_date == today:
        return
    live = _turnover_top10_live()
    if live:
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            _turnover_file().write_text(json.dumps({"date": today, "stocks": live}, ensure_ascii=False))
            _turnover_saved_date = today
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- 交易时段
def _market_phase() -> str:
    """只有连续交易时段启用盘中异动检测。"""
    from duanxian import trade_calendar
    return trade_calendar.session_phase(datetime.now(BEIJING), trade_calendar.quote_trade_day())["phase_key"]


# ---------------------------------------------------------------- 异动检测
def _emit(code: str, name: str, kind: str, msg: str, sources: list[str], change_pct: float | None = None) -> None:
    key = (code, kind)
    now = time.time()
    if now - _alert_last.get(key, 0) < ALERT_COOLDOWN:
        return
    _alert_last[key] = now
    evt = {
        "ts": datetime.now(BEIJING).strftime("%H:%M:%S"),
        "code": code,
        "name": name,
        "kind": kind, "change_pct": change_pct,
        "msg": msg,
        "sources": sources,
    }
    _alerts.insert(0, evt)
    del _alerts[500:]
    try:  # 当日事件落盘（收盘复盘）
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        day = datetime.now(BEIJING).strftime("%Y%m%d")
        with open(_DATA_DIR / f"alerts-{day}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _detect(code: str, q: dict, sources: list[str]) -> None:
    now = time.time()
    frames = _frames.setdefault(code, deque(maxlen=FRAME_KEEP))
    frames.append((now, q["price"], q["amount"]))

    # 规则① 急拉/急跳水：SURGE_WINDOW 秒窗口首尾涨跌幅
    old = next((f for f in frames if now - f[0] <= SURGE_WINDOW), None)
    if old and old[1] > 0 and now - old[0] >= 60:  # 至少积累 1 分钟才判
        delta = (q["price"] / old[1] - 1) * 100
        if delta >= SURGE_PCT:
            _emit(code, q["name"], "急拉", f"3分钟 +{delta:.1f}%（现价 {q['price']}，今日 {q['pct']:+.1f}%）", sources, delta)
        elif delta <= -SURGE_PCT:
            _emit(code, q["name"], "急跌", f"3分钟 {delta:.1f}%（现价 {q['price']}，今日 {q['pct']:+.1f}%）", sources, delta)

    # 规则② 触板/开板（涨停价来自行情快照，含北交所 30cm）
    if q["zt_price"] > 0:
        is_limit = q["price"] >= q["zt_price"] - 1e-6
        was_limit = _limit_state.get(code)
        if was_limit is not None:
            if is_limit and not was_limit:
                _emit(code, q["name"], "封板", f"触及涨停 {q['zt_price']}", sources)
            elif was_limit and not is_limit:
                _emit(code, q["name"], "开板", f"涨停打开（现价 {q['price']}，今日 {q['pct']:+.1f}%）", sources)
        _limit_state[code] = is_limit


# ---------------------------------------------------------------- 主循环
def _build_snapshot(quotes: dict, holdings: list[dict], watch: list[str],
                    bigcaps: list[dict], lianban: list[dict],
                    turnover_label: str, turnover: list[dict], phase: str, cohort: dict | None = None, holdings_error: str = "") -> dict:
    def row(code: str) -> dict:
        q = quotes.get(code) or {}
        return {
            "code": code,
            "name": q.get("name", ""),
            "price": q.get("price"),
            "pct": q.get("pct"),
            "amount": q.get("amount"),
        }

    hold_rows = []
    for h in holdings:
        r = row(h["code"])
        r["cost"] = h.get("cost")
        r["shares"] = h.get("shares")
        if r["price"] and h.get("cost"):
            r["pnl_pct"] = round((r["price"] / h["cost"] - 1) * 100, 2)
        hold_rows.append(r)

    lianban_rows = []
    for s in lianban:
        r = row(s["code"])
        r["boards"] = s["boards"]
        q = quotes.get(s["code"]) or {}
        stamp = str(q.get("quote_time") or "")
        fresh = len(stamp) == 14 and stamp.startswith(datetime.now(BEIJING).strftime("%Y%m%d"))
        if fresh and phase not in {"auction", "closing", "unknown"} and q.get("zt_price") and q.get("price") is not None:
            r["is_limit"] = q["price"] >= q["zt_price"] - 1e-6
        lianban_rows.append(r)

    bigcap_rows = sorted(
        (row(b["code"]) for b in bigcaps),
        key=lambda r: -(r["pct"] if isinstance(r["pct"], (int, float)) else -999),
    )[:10]

    return {
        "ts": datetime.now(BEIJING).strftime("%H:%M:%S"),
        "phase": phase,
        "poll_seconds": POLL_SECONDS,
        "holdings": hold_rows,
        "holdings_error": holdings_error,
        "watchlist": [row(c) for c in watch],
        "bigcap": {"total": len(bigcaps), "top": bigcap_rows},
        "lianban3": lianban_rows,
        "yesterday_ladder": previous_ladder.render_cohort(cohort or {}, quotes, phase=phase),
        "turnover": {"label": turnover_label, "stocks": [row(t["code"]) for t in turnover]},
        "alerts": _alerts[:120],
    }


def _refresh_previous_cohort(wall_day: str) -> None:
    global _previous_cache
    quote_day = None
    try:
        quote_day = previous_ladder.trade_calendar.quote_trade_day()
        result = previous_ladder.load_cohort(quote_day)
    except Exception:
        result = {"available": False, "quote_date": quote_day, "sample_date": None,
                  "stocks": [], "reason": "昨日梯队后台刷新失败，请稍后重试"}
    with _previous_lock:
        previous = _previous_cache[2]
        if (not result.get("available") and quote_day
                and _previous_cache[1] == wall_day and previous.get("available")
                and previous.get("quote_date") == quote_day):
            result = {**previous, "warning": "昨日名单刷新失败，保留同一行情交易日已确认的样本；行情仍独立更新。"}
        _previous_cache = (time.monotonic(), wall_day, result)
    _wake.set()


def _previous_cohort() -> dict:
    """Never wait for calendar/pool I/O in the quote loop. At most one worker.

    A hung upstream can delay this card, but cannot block quote polling or spawn
    unbounded retries. Existing samples remain explicitly dated while refreshing.
    """
    global _previous_worker
    wall_day = datetime.now(BEIJING).strftime("%Y-%m-%d")
    with _previous_lock:
        stamp, cache_day, result = _previous_cache
        current = cache_day == wall_day
        if current and time.monotonic() - stamp < 120:
            return result
        if _previous_worker is None or not _previous_worker.is_alive():
            _previous_worker = threading.Thread(target=_refresh_previous_cohort,
                                                args=(wall_day,), name="yesterday-cohort", daemon=True)
            _previous_worker.start()
        if current and result.get("available"):
            return {**result, "warning": "昨日名单正在后台刷新，暂保留下方所示日期的样本；行情独立更新。"}
        return {"available": False, "quote_date": None, "sample_date": None, "stocks": [],
                "reason": "正在后台确认昨日梯队；其他盯盘行情正常刷新。"}


def _jobs():
    """记账通道延迟取。取不到也只是少一份记录，绝不能反过来弄死轮询线程。"""
    global _jobs_module
    if _jobs_module is None:
        from duanxian import jobs
        _jobs_module = jobs
    return _jobs_module


def _note_failure(exc: Exception, phase: str) -> None:
    """整轮失败要出声，而且要查得到：快照里留读数 + jobs/state.json 记一次。

    这里原先是 `except Exception: pass`。盘中报价轮询坏掉时，`_snapshot` 停在
    上一轮的老值、页面照样渲染、日志一个字没有 —— 用户只能把"页面正常"读成
    "盯盘正常"。记账有节奏：坏的第一轮立刻记，之后每 20 轮一次（盘中 3 秒一轮，
    每轮都写 state.json 会把这份共享文件刷成噪音，而体检的连败阈值是 3）。
    """
    global _loop_error, _loop_streak
    _loop_streak += 1
    _loop_error = {
        "reason": f"{type(exc).__name__}：{str(exc)[:160]}" or type(exc).__name__,
        "at": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S"),
        "consecutive_rounds": _loop_streak,
        "phase": phase,
    }
    if _loop_streak != 1 and _loop_streak % FAILURE_RECORD_EVERY:
        return
    try:
        _jobs().record("watchtower", ok=False, detail={
            "reason": f"{_loop_error['reason']}（连续 {_loop_streak} 轮，phase={phase}）",
            "phase": phase})
    except Exception:  # noqa: BLE001  记账失败时内存读数照留，读接口的人仍看得见
        pass


def _note_success() -> None:
    """恢复要说一句；健康时也要有心跳，否则「上次成功」越走越旧会报假故障
    （2026-09-28 快讯那条就是活例：state.json 不记，体检对着在跑的循环报红）。"""
    global _loop_error, _loop_streak, _last_ok_at
    _loop_error = None
    if _loop_streak or time.monotonic() - _last_ok_at >= HEARTBEAT_SECONDS:
        reason = (f"盯盘快照恢复（此前连续失败 {_loop_streak} 轮）" if _loop_streak
                  else "盯盘轮询心跳")
        try:
            _jobs().record("watchtower", ok=True, detail={"reason": reason})
        except Exception:  # noqa: BLE001
            pass
        _last_ok_at = time.monotonic()
        _loop_streak = 0


def _round() -> None:
    """一轮：读五路标的池 → 拉批量行情 → 异动检测 → 换快照。抛错交给 `_loop` 记账。"""
    global _snapshot
    phase = _market_phase()
    holdings_error = ""
    try:
        holdings = _holdings()
    except Exception:
        holdings = []
        # 子源缺口不写成作业失败：整轮是好的，写成 fail 会让一条在跑的循环变红，
        # 那和原先的静默一样误导人。它走 degraded 通道，读接口的人看得见。
        holdings_error = "持仓读取失败，未展示旧账替代数据；请在我的股票检查交易日志。"
    with _lock:
        watch = list(_extra_watch)
    bigcaps = _bigcaps()
    cohort = _previous_cohort()
    lianban = [s for s in cohort.get("stocks", []) if s["boards"] >= 3]
    turnover_label, turnover = _turnover_yesterday()

    pool: dict[str, list[str]] = {}
    for c in (h["code"] for h in holdings):
        pool.setdefault(c, []).append("持仓")
    for c in watch:
        pool.setdefault(c, []).append("自选")
    for b in bigcaps:
        pool.setdefault(b["code"], []).append("大票")
    for s in cohort.get("stocks", []):
        pool.setdefault(s["code"], []).append(f"昨日{s['boards']}板")
    for t in turnover:
        pool.setdefault(t["code"], []).append("昨十")

    quotes = _tencent_batch(list(pool.keys()))
    if phase == "open":
        stamp_day = datetime.now(BEIJING).strftime("%Y%m%d")
        for code, q in quotes.items():
            stamp = str(q.get("quote_time") or "")
            earliest = "1300" if datetime.now(BEIJING).hour >= 13 else "0930"
            if len(stamp) == 14 and stamp.startswith(stamp_day) and stamp[8:12] >= earliest:
                _detect(code, q, pool[code])
    else:
        # 竞价/午休的报价不进入连续交易的急拉与开板比较基准。
        _frames.clear()
        _limit_state.clear()
    built = _build_snapshot(quotes, holdings, watch, bigcaps, lianban,
                            turnover_label, turnover, phase, cohort, holdings_error)
    degraded = [text for text in (holdings_error,) if text]
    if not quotes and pool:
        degraded.append(f"批量行情返回空（池内 {len(pool)} 只），本轮价格不可用")
    # 每一轮都交出生成时刻：读接口的人必须能分清"这是刚算的"还是"上一轮的旧账"。
    _snapshot = {**built, "generated_at": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S"),
                 "degraded": degraded, "loop_error": None}


def enabled() -> bool:
    """这条 3 秒轮询该不该干活：**判据只有 `duanxian.jobs.loop_enabled` 那一份**。

    10-07 之前它既不在 `CLOSE_DEFAULTS` 也不在常驻循环面板里（只有懒启动），
    是全仓唯一一条"每 3 秒发一次外部请求却没人能关"的循环。现在它挂了名、有开关，
    而开关要真的有人读才算开关 —— 判断放在每一轮开头，改完不必重启。

    ⚠️ 这里原先是自己抄的一份读法。合成一份后总闸与配置都由 `jobs.loop_enabled` 判；
    `pytest_block=False` 是因为**这条循环的测试洞在 `ensure_started()`**（那里硬挡真起线程），
    既有三条测试要直接调 `_loop()` 喂假行情，验的是"坏轮怎么出声、怎么记账"——
    两道挡叠在每一轮开头会把它们全堵成零轮。
    """
    from duanxian import jobs

    return jobs.loop_enabled("VIBE_WATCHTOWER", "watchtower", jobs.WATCHTOWER_DEFAULTS,
                             pytest_block=False)


def _loop() -> None:
    global _snapshot
    alert_day = None
    while True:
        if not enabled():
            # 关着时低频空转：醒来先看开关再决定要不要继续 —— 改回 true 下一次 20 秒内恢复。
            # ⚠️ "测试里不许起真轮询"这条硬挡**不放在这里**：本仓库有用例直接驱动 `_loop()`
            #    跑一轮（`test_previous_ladder.py`），在这里判 PYTEST 会把那条用例判成
            #    "一轮都没抓"。真正的洞是线程，见 `ensure_started()`。
            _wake.wait(20)
            _wake.clear()
            continue
        _expire_watch_clients()
        phase = _market_phase()
        today = datetime.now(BEIJING).strftime("%Y%m%d")
        if today != alert_day:
            _alerts.clear(); _frames.clear(); _limit_state.clear()
            alert_day = today
        if phase == "closed" and datetime.now(BEIJING).strftime("%H:%M") >= "15:05":
            from duanxian.trade_calendar import quote_trade_day
            if quote_trade_day() == datetime.now(BEIJING).strftime("%Y-%m-%d"):
                _save_turnover_snapshot()

        # 非交易时段也低频全量重建（收盘后录入持仓/加自选立刻可见），只是不做异动检测
        try:
            # 数据源抖动不杀线程，下一轮照跑 —— 但必须出声并可查（见 _note_failure）。
            _round()
            _note_success()
        except Exception as exc:  # noqa: BLE001
            _note_failure(exc, phase)
            stale = {**(_snapshot or {}), "loop_error": _loop_error}
            if _snapshot:
                stale["snapshot_stale"] = True
            _snapshot = stale
        _wake.wait(POLL_SECONDS if phase == "open" else 20)
        _wake.clear()


def ensure_started() -> None:
    """懒启动盯盘线程。**pytest 里直接拒**：这条线程一跑就是每 3 秒真打一次腾讯报价，
    而 `conftest.py` 拦的是 socket —— 拦不住的就是"每轮一条假故障 + 连败计数刷爆真状态"。
    （与 `_spawn_stage` / `_spawn_backup` 同一类洞的同一类处理：洞只留一个，测试里硬挡。）"""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_loop, name="watchtower", daemon=True)
        _thread.start()


def poke() -> None:
    """外部数据变化（录入持仓/改自选）后唤醒线程，立即重建快照。"""
    _wake.set()


_watch_clients: dict[str, tuple[float, list[str]]] = {}

def set_client_watch(client_id: str, codes: list[str]) -> None:
    global _extra_watch
    if not isinstance(client_id, str) or not re.fullmatch(r"[a-f0-9]{32}", client_id):
        raise ValueError("监控客户端编号无效")
    if not isinstance(codes, list) or len(codes) > 100 or any(not isinstance(c, str) or not re.fullmatch(r"\d{6}", c) for c in codes):
        raise ValueError("自选最多100只，代码须为6位数字")
    _expire_watch_clients()
    with _lock:
        now = time.monotonic()
        if client_id not in _watch_clients and len(_watch_clients) >= 32:
            raise ValueError("同时监控页面过多，请关闭不用的页面")
        candidate = {**_watch_clients, client_id: (now, list(dict.fromkeys(codes)))}
        clean = list(dict.fromkeys(c for _, group in candidate.values() for c in group))
        if len(clean) > 300:
            raise ValueError("全部监控页面合计最多300只，请关闭不用的页面或缩减自选")
        _watch_clients[client_id] = candidate[client_id]
        changed = clean != _extra_watch
        _extra_watch = clean
    if changed:
        poke()


def _expire_watch_clients() -> None:
    global _extra_watch
    with _lock:
        expired = [key for key, (stamp, _) in _watch_clients.items() if time.monotonic() - stamp > 120]
        if expired:
            for key in expired:
                del _watch_clients[key]
            _extra_watch = list(dict.fromkeys(c for _, group in _watch_clients.values() for c in group))


def get_snapshot() -> dict:
    if not _snapshot:
        # 从未成功过一轮：交回空结构，但把"为什么"一起给出，不给一个看起来正常的空页。
        return {"warming_up": True, "ts": "", "phase": _market_phase(), "poll_seconds": POLL_SECONDS,
                "holdings": [], "watchlist": [], "bigcap": {"total": 0, "top": []},
                "lianban3": [], "turnover": {"label": "", "stocks": []}, "alerts": [],
                "generated_at": None, "snapshot_age_seconds": None, "degraded": [],
                "loop_error": _loop_error}
    snap = dict(_snapshot)
    snap["loop_error"] = _loop_error or snap.get("loop_error")
    stamp = str(snap.get("generated_at") or "")
    if snap["loop_error"] and stamp:
        try:
            age = datetime.now(BEIJING) - datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING)
            snap["snapshot_age_seconds"] = max(0, int(age.total_seconds()))
        except ValueError:
            snap["snapshot_age_seconds"] = None
    else:
        snap["snapshot_age_seconds"] = 0
    return snap
