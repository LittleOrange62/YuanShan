# -*- coding: utf-8 -*-
"""趋势两把尺的**唯一**实现：逐期均线（`moving_average`）与已确认转折点（`zigzag_turns`）。

为什么单独成一份而不是各读侧各写一份（2026-10-31）：
「风险提示」的宽度线与「国家队代理指标」的占比/份额线都要在同一种图上说同一件事 ——
「这一段在往上还是往下、顶和底在哪一天」。第一版这两个屏各抄了一份均线与 zigzag，
两份实现迟早给出两个不同的转折点（那正是本项目反复出过的"两份真相源"，
同 [[yuanshan-shared-block-ledger]] 那条纪律：一个读数只准一处实现）。

两条不许破的形状（都各自被真实数据钉过，`tests/test_risk_signal.py` 与
`tests/test_national_team.py` 都打这里）：
· **缺数不硬凑**：窗口里有效值不满 `k` 就交 None，不拿现有几期的均值顶上去 ——
  顶上去会让一条断续的线看起来连贯。
· **zigzag 只交被反向走够门槛的极值**：末端那个还没确认的极值不交（交了就等于把最后一根
  抖动说成拐点），窗口第一期也不交（它没有左侧，只是窗口边界，画出来会被读成「这里拐过」）。
  ⚠️ 方向未定时 `hi`/`lo` 必须分开追：共用一个极值变量的第一版，在阈值一大时整段返回空
  （2026-10-26 实测 MA10 宽度在 12 个月窗口上 th=5pp 报 34 个、th=10pp 报 0 个，而那段极差 31pp）。
"""
from __future__ import annotations

import math
from typing import Any


def _valid(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def moving_average(values: list, k: int) -> list:
    """逐期的近 `k` 期均值（含当期）；窗口内有效值不满 `k` ⇒ None（不拿几期硬凑）。

    与 `trailing_pctiles` 同一口径：缺数就是判不了，不用现有几期顶替。
    """
    out = []
    for i in range(len(values)):
        win = [float(x) for x in values[max(0, i - (k - 1)): i + 1] if _valid(x)]
        out.append(round(sum(win) / len(win), 2) if len(win) >= k else None)
    return out


def zigzag_turns(values: list, th: float) -> list[dict]:
    """只记幅度 ≥ `th` 的反转（zigzag），返回 [{'i', 'value', 'kind': 'peak'|'trough'}]。

    末端那个还没被反向走够 `th` 确认的极值**不交** —— 交了就等于把最后一根抖动当成拐点。
    窗口第一期同样不交：它没有左侧，只是窗口边界，交出去会被读成「这里拐过」。

    ⚠️ 方向未定时 `hi`/`lo` 必须分开追：第一版共用一个极值变量，方向没定下来之前
    那个变量会跟着每一个点跑，结果阈值一大就整段返回空
    （2026-10-26 实测：MA10 宽度在 12 个月窗口上 th=5pp 报 34 个转折点、th=10pp 报 0 个 ——
    而那段极差有 31pp，0 个显然是假的）。`tests/test_risk_signal.py` 里钉了这一条。
    """
    pts = [(i, float(x)) for i, x in enumerate(values) if _valid(x)]
    if len(pts) < 3 or not _valid(th) or float(th) <= 0:
        return []
    th = float(th)
    out: list[dict] = []
    direction = 0                                  # 0=未定 1=向上找顶 -1=向下找底
    hi_i, hi_v = pts[0]
    lo_i, lo_v = pts[0]
    ex_i, ex_v = pts[0]
    for i, x in pts[1:]:
        if direction == 0:
            if x > hi_v:
                hi_i, hi_v = i, x
            if x < lo_v:
                lo_i, lo_v = i, x
            if hi_v - x >= th:
                out.append({'i': hi_i, 'value': round(hi_v, 2), 'kind': 'peak'})
                direction, ex_i, ex_v = -1, i, x
            elif x - lo_v >= th:
                out.append({'i': lo_i, 'value': round(lo_v, 2), 'kind': 'trough'})
                direction, ex_i, ex_v = 1, i, x
        elif direction == 1:
            if x > ex_v:
                ex_i, ex_v = i, x
            elif ex_v - x >= th:
                out.append({'i': ex_i, 'value': round(ex_v, 2), 'kind': 'peak'})
                direction, ex_i, ex_v = -1, i, x
        else:
            if x < ex_v:
                ex_i, ex_v = i, x
            elif x - ex_v >= th:
                out.append({'i': ex_i, 'value': round(ex_v, 2), 'kind': 'trough'})
                direction, ex_i, ex_v = 1, i, x
    # 窗口第一期不是转折点：它没有左侧，只是窗口边界（不删就会被读成"这里拐过"）
    first_i = pts[0][0]
    return [t for t in out if t['i'] != first_i]
