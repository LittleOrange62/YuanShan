# -*- coding: utf-8 -*-
"""午间复盘的「几大指数量能」一节（外部 `intraday_zt/index_volume.py` 迁入，2026-10-30 定版）。

三格读数（都是客观统计 + 阈值贴标签，不是预测、不含买卖建议）：
  · 今早（截至 11:30 累计）成交额 vs 前一交易日同时点 → 放量/缩量/持平
  · 近 5 个交易日全天成交额（沪/深分开 + 两市合计）→ 前一日 vs 前 4 日均值
  · 北证 50：没有分钟源 ⇒ 只给 `.day` 全天序列，早间那一格明标缺数

源与优先级（主源挂了自动降级；缺数**不拿上一场的值顶**）：
  1. 腾讯 `web.ifzq.gtimg.cn/appstock/app/day/query`：近 5 个交易日逐分钟累计成交额；
  2. 本地通达信 `.day`（`app.config.VIPDOC_DIR`，全天额）—— 用前先看尾行日期是不是前一交易日，
     不是就当缺数（盘后没下载就报缺，不报昨天的数）；
  3. 两源都算到前日时做一次交叉校验（差 >0.5% 记一行 `noon_vol_cross_mismatch`）。

`note` 回调由调用方给（作业侧接 `store.write_broadcast`）：这一层不连库、不猜默认值。
"""
from __future__ import annotations

import datetime as dt
import json
import struct
import urllib.request
from pathlib import Path
from typing import Callable

from app.config import VIPDOC_DIR

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
TX_IDX = {"sh000001": "上证指数", "sz399001": "深证成指"}
DAY_FILES = {"上证指数": ("sh", "sh000001"), "深证成指": ("sz", "sz399001"),
             "北证50": ("bj", "bj899050")}
#: 放量/缩量带宽（±10%），带内算持平 —— 外部 10-30 定标的阈值规则，改它要改 spec_version。
BAND = 0.10
_TIMEOUT_S = 15
_DAY_ROW = struct.Struct('<IIIIIfII')


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:
        return r.read().decode('utf-8', 'replace')


def tx_minutes(code: str) -> dict[str, dict[str, float]]:
    """腾讯 day/query → `{YYYYMMDD: {HHMM: 累计成交额(元)}}`（近 5 个交易日，新→旧）。"""
    d = json.loads(_get(f"https://web.ifzq.gtimg.cn/appstock/app/day/query?code={code}"))
    out: dict[str, dict[str, float]] = {}
    for day in d.get("data", {}).get(code, {}).get("data", []):
        rows = {}
        for line in day.get("data") or []:
            parts = line.split(" ")
            if len(parts) >= 4:
                rows[parts[0]] = float(parts[3])
        if day.get("date"):
            out[day["date"]] = rows
    return out


def day_path(name: str) -> Path:
    market, code = DAY_FILES[name]
    return VIPDOC_DIR / market / "lday" / f"{code}.day"


def day_amounts(path: Path, n: int = 5) -> list[tuple[int, float]]:
    """本地 `.day` 尾 n 行 → `[(YYYYMMDD, 全天成交额元)]`（旧→新）。文件不在 ⇒ []。"""
    if not path.is_file():
        return []
    data = path.read_bytes()
    n = min(n, len(data) // _DAY_ROW.size)
    out = []
    for i in range(n):
        off = len(data) - (n - i) * _DAY_ROW.size
        d, _, _, _, _, amt, _, _ = _DAY_ROW.unpack_from(data, off)
        out.append((d, float(amt)))
    return out


def label(pct: float) -> str:
    if pct >= BAND:
        return "放量"
    if pct <= -BAND:
        return "缩量"
    return "持平"


def cmp_text(a: float | None, b: float | None) -> str:
    """a 对 b 的 %+标签；任一缺数就报「缺数」，不猜。"""
    if a is None or b is None or b <= 0:
        return "缺数"
    p = a / b - 1
    return f"{p:+.1%} {label(p)}"


def yi(v: float | None) -> str:
    return "缺数" if v is None else f"{v / 1e8:,.0f}亿"


def _morning(minutes: dict[str, float] | None) -> float | None:
    """早间额＝当日 11:30 累计（午间推送时上午已收盘，11:30 即全上午）。"""
    if not minutes:
        return None
    if "1130" in minutes:
        return minutes["1130"]
    vals = [v for k, v in minutes.items() if k <= "1130"]
    return max(vals, default=None)


def volume_lines(now: dt.datetime, *, note: Callable[[str, dict], None] | None = None) -> tuple[list[str], str]:
    """→ (这一节的若干行, 出处串)。全挂 ⇒ `(["量能：缺数（主源不可用）"], "无")`。"""
    def log(what: str, **kw) -> None:
        if note:
            note(what, kw)

    src_parts: list[str] = []
    lines: list[str] = []
    today_s = f"{now:%Y%m%d}"

    tx: dict[str, dict[str, dict[str, float]]] = {}
    for code in TX_IDX:
        try:
            tx[code] = tx_minutes(code)
        except Exception as exc:  # noqa: BLE001  一只挂了不影响另一只
            log('noon_vol_tx_fail', code=code, err=f'{type(exc).__name__}: {exc}'[:150])
    if tx:
        src_parts.append("腾讯分钟")

    dates: list[str] = []
    for code in TX_IDX:
        ds = sorted(tx.get(code) or {}, reverse=True)
        if ds:
            dates = [d for d in ds if d <= today_s]
            break
    past = [d for d in dates if d != today_s][:5]
    prev_d = past[0] if past else None
    prev_md = f"{prev_d[4:6]}-{prev_d[6:8]}" if prev_d else "--"

    mornings: list[str] = []
    for code, name in TX_IDX.items():
        m = tx.get(code) or {}
        t = _morning(m.get(today_s))
        p = _morning(m.get(prev_d)) if prev_d else None
        mornings.append(f"{name}今早{yi(t)}（比{prev_md}同时点{cmp_text(t, p)}）")

    def full_day_series(name: str) -> tuple[list[float] | None, list[str]]:
        rows = day_amounts(day_path(name), 5)
        if rows and prev_d and rows[-1][0] == int(prev_d):
            return [a for _, a in rows], [f"{d}" for d, _ in rows]
        code = next(c for c, n in TX_IDX.items() if n == name)
        vals, ds = [], []
        for d in past[::-1]:
            v = (tx.get(code) or {}).get(d, {}).get("1500")
            if v is not None:
                vals.append(v)
                ds.append(d)
        return (vals or None), ds

    # 精简（用户 11-03：这一节太复杂）：**不再逐只指数各列一行近 5 日全天**，
    # 只留「两市合计」那一行趋势；指数自己的近 5 日序列仍然算（合计要用、交叉校验要用），
    # 不进正文而已。缺数该说还是说 —— 省字数不能省到把"没有"说成"没提"。
    mismatch: list[tuple[str, str, float, float]] = []
    day_series: dict[str, list[float]] = {}
    for name in ("上证指数", "深证成指"):
        vals, ds = full_day_series(name)
        if not vals:
            mornings.append(f"{name}近5日全天缺数（.day未更新且腾讯无15:00数据）")
            continue
        day_series[name] = vals
        code = next(c for c, n in TX_IDX.items() if n == name)
        tx_prev = (tx.get(code) or {}).get(prev_d or "", {}).get("1500")
        if tx_prev and vals[-1] and abs(tx_prev - vals[-1]) / vals[-1] > 0.005:
            mismatch.append((name, prev_d or "", vals[-1], tx_prev))

    if len(day_series) == 2 and len(day_series["上证指数"]) == len(day_series["深证成指"]):
        tot = [a + b for a, b in zip(day_series["上证指数"], day_series["深证成指"])]
        arrow = "→".join(f"{v / 1e8:,.0f}" for v in tot)
        lines.append(f"两市近{len(tot)}日全天(亿){arrow}·前日比前{len(tot) - 1}日均"
                     f"{cmp_text(tot[-1], sum(tot[:-1]) / (len(tot) - 1))}")

    for name, d, a, b in mismatch:
        log('noon_vol_cross_mismatch', idx=name, day=d, dayfile=a, tx=b)

    bj = day_amounts(day_path("北证50"), 5)
    if bj and prev_d and bj[-1][0] == int(prev_d):
        vals = [a for _, a in bj]
        arrow = "→".join(f"{v / 1e8:,.1f}" for v in vals)
        lines.append(f"北证50近{len(vals)}日全天(亿){arrow}（早间无分钟源，只有全天数）")
        src_parts.append(".day")
    elif bj:
        lines.append(f"北证50缺数（.day最新仅到{bj[-1][0]}，未到{prev_d}）")
    else:
        lines.append("北证50缺数（.day不可读）")

    if not lines and not mornings:
        return ["量能：缺数（主源不可用）"], "无"
    # 今早那一行就是这一节的开头：一行说完"今早多少、比前日同时点多/少多少"
    lines.insert(0, "量能：" + (" · ".join(mornings) if mornings else "今早缺数（分钟源未刷新）"))
    return lines, "+".join(dict.fromkeys(src_parts)) or "无"
