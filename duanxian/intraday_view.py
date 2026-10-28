# -*- coding: utf-8 -*-
"""盘中事件流的只读出口（页面读这一份，**页面不重算判据**）。

三态纪律：库不通 / 没有这一场 / 这一场没有行 —— 都只交 `{available:false, reason:...}`，
**不带 `rows` 键**，不交空数组冒充"今天没有涨跌停"（那是两件不同的事）。

`spec_version` 必须进每个查询条件：判据改版后旧行留着对账，混着读会念出两套口径的数。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from app.db import engine

from app.intraday.pool_events import KIND_LABELS, SPEC_VERSION


def _num(v: Any) -> Optional[float]:
    """Decimal/日期都要收。**isna 的闸必须排在 isinstance(date/datetime) 之前** ——
    `pd.NaT` 是 datetime 实例，顺序反了就带着 NaT 进 JSON，`json.dumps` 产出非法 JSON。"""
    import pandas as pd
    if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v):
        return None
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    return float(v) if isinstance(v, (int, float)) else v


def _day(day: Optional[str]) -> Optional[dt.date]:
    if not day:
        return None
    return dt.date.fromisoformat(day)


def latest_session() -> Optional[dt.date]:
    return engine.scalar("SELECT max(trade_date) FROM analytics.intraday_pool_event"
                         " WHERE spec_version = %s", (SPEC_VERSION,), role='reader')


def timeline(day: Optional[str] = None, *, limit: int = 400) -> dict:
    """一整场的盘中事件流 + 每针摘要。`day` 缺省＝库里最近有事件的那一场。"""
    try:
        target = _day(day) or latest_session()
        if target is None:
            return {'available': False,
                    'reason': '库里还没有任何盘中事件行（这功能今晚才接上，明早开盘后才有第一场）',
                    'spec_version': SPEC_VERSION}
        rounds = engine.query(
            """
            SELECT captured_at, min(pool_zt_n) zt_n, min(pool_dt_n) dt_n,
                   count(*) events, bool_or(is_baseline) is_baseline
              FROM analytics.intraday_pool_event
             WHERE trade_date = %s AND spec_version = %s
             GROUP BY 1 ORDER BY 1
            """, (target, SPEC_VERSION), role='reader')
        rows = engine.query(
            """
            SELECT captured_at, kind, kind_label, side, security_code, security_name,
                   pct, price, board_count, open_count, first_seal_time, seal_fund_yuan,
                   industry, missing
              FROM analytics.intraday_pool_event
             WHERE trade_date = %s AND spec_version = %s
             ORDER BY captured_at DESC, kind
             LIMIT %s
            """, (target, SPEC_VERSION, limit), role='reader')
        return {
            'available': True, 'trade_date': target.isoformat(),
            'spec_version': SPEC_VERSION,
            'rounds': [{'captured_at': str(_num(r['captured_at'])), 'zt_n': r['zt_n'],
                        'dt_n': r['dt_n'], 'events': r['events'],
                        'is_baseline': bool(r['is_baseline'])} for r in rounds],
            'rows': [{k: _num(v) for k, v in r.items()} for r in rows],
            'truncated': len(rows) >= limit,
            'kind_labels': KIND_LABELS,
            # 首封的两把尺要说清：盘中念的是源站 fbt（跌停池结构上没有 ⇒ null 是常态）；
            # 盘后回看能从 analytics.limit_up_pool_daily.first_seal_at 拿本地分钟线自算的那一份。
            'first_seal_basis': '盘中＝东财池 fbt（跌停池没有这一列）；盘后回看＝analytics.limit_up_pool_daily.first_seal_at（本地 1 分钟线自算，只从 2026-08-14 起有）',
        }
    except Exception as exc:  # noqa: BLE001  出口层出声，不让页面拿到半截结构
        return {'available': False, 'reason': f'盘中事件流读不出来：{type(exc).__name__}: {exc}'[:300],
                'spec_version': SPEC_VERSION}
