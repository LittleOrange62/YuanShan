# -*- coding: utf-8 -*-
"""盘中事件流/播报留痕的**唯一写入口**（DDL 54）。

三条规矩：
· 幂等：`event_key`/`broadcast_key` 撞了就 `DO NOTHING` —— 重跑同一针、重放同一份历史
  都不会插出第二行（外部那套只有 jsonl 追加，重跑就是重复文本）。
· 缺数写 NULL，`missing` jsonb 点名为什么缺；**不写 0**。
· 写入方只有这里。作业脚本不许自己拼 INSERT。
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any, Optional

from app.db import engine

from app.intraday.pool_events import KIND_LABELS, KIND_SIDE, SPEC_VERSION

_EVT = """
INSERT INTO analytics.intraday_pool_event (
    trade_date, captured_at, event_key, spec_version,
    side, kind, kind_label, security_code, security_name, pct, price,
    board_count, open_count, first_seal_time, seal_fund_yuan, industry,
    pool_zt_n, pool_zb_n, pool_dt_n, round_seq, is_baseline,
    basis, source, missing, calculated_at)
VALUES (%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s,%s,
        'source_pool_diff','eastmoney_push2ex', %s::jsonb, now())
ON CONFLICT (trade_date, captured_at, event_key, spec_version) DO NOTHING
"""

_BCAST = """
INSERT INTO analytics.intraday_broadcast (
    trade_date, broadcast_key, spec_version, task, what, at_ts, ok,
    channel_key, http_status, delivered, err, body, counts, source_detail, calculated_at)
VALUES (%s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s::jsonb, %s::jsonb, now())
ON CONFLICT (trade_date, broadcast_key, spec_version)
DO UPDATE SET ok = EXCLUDED.ok, delivered = EXCLUDED.delivered,
              err = EXCLUDED.err, channel_key = EXCLUDED.channel_key
"""


def event_key(captured_at: dt.datetime, kind: str, code: str) -> str:
    """幂等键。同一针（同一 `captured_at`）同一只票同一类事件只有一行。"""
    return f'{captured_at:%Y%m%d%H%M%S}|{kind}|{code}'


def write_events(captured_at: dt.datetime, events: list[dict], *,
                 pool_n: dict[str, Optional[int]], round_seq: Optional[int] = None,
                 is_baseline: bool = False, trade_date: Optional[dt.date] = None) -> int:
    """落一整针的事件。返回**新插入**的行数（撞键的不算，那是重放）。"""
    day = trade_date or captured_at.date()
    rows = []
    for e in events:
        kind = e['kind']
        rows.append((
            day, captured_at, event_key(captured_at, kind, e['code']), SPEC_VERSION,
            KIND_SIDE.get(kind, 'zt'), kind, KIND_LABELS.get(kind, kind),
            e['code'], e.get('name'), e.get('pct'), e.get('price'),
            e.get('board_count'), e.get('open_count'), e.get('first_seal_time'),
            e.get('seal_fund_yuan'), e.get('industry'),
            pool_n.get('zt'), pool_n.get('zb'), pool_n.get('dt'),
            round_seq, is_baseline,
            json.dumps(e.get('missing') or {}, ensure_ascii=False)))
    if not rows:
        return 0
    return engine.executemany(_EVT, rows, role='etl')


def write_broadcast(captured_at: dt.datetime, *, task: str, what: str,
                    body: str = '', ok: Optional[bool] = None,
                    delivered: Optional[bool] = None, err: Optional[str] = None,
                    channel_key: Optional[str] = None, http_status: Optional[int] = None,
                    counts: Optional[dict[str, Any]] = None,
                    source_detail: Optional[dict[str, Any]] = None,
                    trade_date: Optional[dt.date] = None) -> int:
    """一条播报/一次失败留痕。`delivered` 必须是**业务码**判出来的，不是 HTTP 200。"""
    day = trade_date or captured_at.date()
    key = f'{task}|{captured_at:%Y%m%d%H%M%S}|{what}'
    return engine.execute(_BCAST, (
        day, key, SPEC_VERSION, task, what, captured_at, ok,
        channel_key, http_status, delivered, err, body or None,
        json.dumps(counts or {}, ensure_ascii=False),
        json.dumps(source_detail or {}, ensure_ascii=False)), role='etl')


_CONCEPT = """
INSERT INTO analytics.intraday_concept_count (
    trade_date, asof_ts, bucket, sector_name, spec_version,
    sector_code, multi_code, hit_n, rank, share_pct, tier, top_board_count, reps,
    sector_pct, sector_pct_asof, unmapped_n, calculated_at)
VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s,%s, %s,%s,%s, now())
ON CONFLICT (trade_date, asof_ts, bucket, sector_name, spec_version)
DO UPDATE SET hit_n = EXCLUDED.hit_n, rank = EXCLUDED.rank,
              sector_pct = EXCLUDED.sector_pct, sector_pct_asof = EXCLUDED.sector_pct_asof,
              unmapped_n = EXCLUDED.unmapped_n, calculated_at = now()
"""

_AUCTION = """
INSERT INTO analytics.auction_board (
    trade_date, board, rank, spec_version,
    security_code, security_name, pct, amount_yuan, board_count, seal_fund_yuan,
    first_seal_time, industry, listed_n, truncated_n, source, unavailable, reason,
    captured_at, calculated_at)
VALUES (%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s, %s, now())
ON CONFLICT (trade_date, board, rank, spec_version)
DO UPDATE SET security_code = EXCLUDED.security_code, pct = EXCLUDED.pct,
              amount_yuan = EXCLUDED.amount_yuan, source = EXCLUDED.source
"""


def upsert_auction_rows(rows: list[tuple]) -> int:
    """竞价双榜 + 一字块。元组顺序同 `_AUCTION`；`unavailable=True` 的那一侧
    也要落一行（`security_code=None`、rank=0、reason 写明没取到），
    否则"跌幅榜挂了"这件事到库里就变成"那天没有跌幅榜"，两种情况事后分不出来。"""
    return engine.executemany(_AUCTION, rows, role='etl')


_NOON = """
INSERT INTO analytics.noon_review (
    trade_date, spec_version,
    limit_up_n, limit_down_n, unseal_n, unseal_rate_pct, max_board_text, max_board_count,
    board2_codes, idx_sh_pct, idx_sz_pct, idx_bj_pct, up_count, down_count,
    premium_n, premium_pos_n, premium_pos_rate_pct, premium_avg_pct, mood_label, mood_tags,
    section_mood, section_cluster, section_watch, body,
    pool_source, index_source, ladder_basis, delivered, missing, calculated_at)
VALUES (%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,
        %s,%s,%s,%s, %s::jsonb, now())
ON CONFLICT (trade_date, spec_version) DO UPDATE SET body = EXCLUDED.body,
    delivered = EXCLUDED.delivered, calculated_at = now()
"""


def upsert_noon(row: tuple) -> int:
    """12:10 午间快照，一场一行。"""
    return engine.execute(_NOON, row, role='etl')


def upsert_concept_counts(rows: list[tuple]) -> int:
    """
    批量落概念计数（竞价／热度／午间共用这一张表、这一个入口）。

    元组顺序与 `_CONCEPT` 一致：`(trade_date, asof_ts, bucket, sector_name, spec_version,
    sector_code, multi_code, hit_n, rank, share_pct, tier, top_board_count, reps,
    sector_pct, sector_pct_asof, unmapped_n)`。`sector_pct` **必须是百分数**
    （换算在 `sources.sector_pcts` 一处做完，库里 `sector_daily.pct_change` 是小数）。
    """
    return engine.executemany(_CONCEPT, rows, role='etl')


def state_summary(day: dt.date) -> dict:
    """当日已落库的针数/事件数/播报数 —— 作业自己回读，别拿"我写了"当"库里有了"。"""
    return engine.query_one(
        "SELECT count(*) AS events, count(DISTINCT captured_at) AS rounds,"
        "       min(captured_at) AS first_at, max(captured_at) AS last_at"
        "  FROM analytics.intraday_pool_event"
        " WHERE trade_date = %s AND spec_version = %s", (day, SPEC_VERSION), role='reader') or {}
