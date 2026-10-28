# -*- coding: utf-8 -*-
"""指标值与评分读写，含评分拆解与 stale 标记。"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.db import engine

UPSERT_INDICATOR = """
INSERT INTO fct.indicator_value (ts_code, trade_date, ind_key, value, task_id)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (ts_code, trade_date, ind_key) DO UPDATE SET
    value = EXCLUDED.value, task_id = EXCLUDED.task_id
"""


def upsert_indicators(rows: list[tuple], *, role: str = 'app') -> int:
    return engine.executemany(UPSERT_INDICATOR, rows, role=role)


UPSERT_SCORE = """
INSERT INTO fct.score (ts_code, trade_date, cap_key, value, rank_in_market,
                       pct_rank, quadrant, task_id, is_stale, calculated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (ts_code, trade_date, cap_key) DO UPDATE SET
    value = EXCLUDED.value, rank_in_market = EXCLUDED.rank_in_market,
    pct_rank = EXCLUDED.pct_rank, quadrant = EXCLUDED.quadrant,
    task_id = EXCLUDED.task_id, is_stale = EXCLUDED.is_stale,
    calculated_at = now()
"""


def upsert_scores(rows: list[tuple], *, role: str = 'app') -> int:
    return engine.executemany(UPSERT_SCORE, rows, role=role)


UPSERT_DETAIL = """
INSERT INTO fct.score_detail (ts_code, trade_date, cap_key, part_key, part_label,
                              part_value, weight, contribution, script_key, script_ver)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (ts_code, trade_date, cap_key, part_key) DO UPDATE SET
    part_label = EXCLUDED.part_label, part_value = EXCLUDED.part_value,
    weight = EXCLUDED.weight, contribution = EXCLUDED.contribution,
    script_key = EXCLUDED.script_key, script_ver = EXCLUDED.script_ver
"""


def upsert_score_details(rows: list[tuple], *, role: str = 'app') -> int:
    """拆解明细是"评分不再是黑盒"的基础，也是 score_not_constant 规则的检测对象。"""
    return engine.executemany(UPSERT_DETAIL, rows, role=role)


# ============================================================ 读

def indicators(trade_date: dt.date, ind_keys: list[str] | None = None,
               codes: list[str] | None = None, *, wide: bool = True,
               role: str = 'reader') -> pd.DataFrame:
    rows = engine.query(
        """
        SELECT ts_code, trade_date, ind_key, value
          FROM fct.indicator_value
         WHERE trade_date = %(d)s
           AND (%(keys)s::text[] IS NULL OR ind_key = ANY(%(keys)s))
           AND (%(codes)s::text[] IS NULL OR ts_code = ANY(%(codes)s))
        """,
        {'d': trade_date, 'keys': ind_keys, 'codes': codes}, role=role)
    df = pd.DataFrame(rows)
    if wide and not df.empty:
        df = df.pivot_table(index='ts_code', columns='ind_key',
                            values='value', aggfunc='first').reset_index()
        df.columns.name = None
    return df


def indicator_series(ts_code: str, ind_key: str, start: dt.date | None = None,
                     end: dt.date | None = None, *, role: str = 'reader') -> pd.DataFrame:
    rows = engine.query(
        """
        SELECT trade_date, value FROM fct.indicator_value
         WHERE ts_code = %s AND ind_key = %s
           AND (%s::date IS NULL OR trade_date >= %s)
           AND (%s::date IS NULL OR trade_date <= %s)
         ORDER BY trade_date
        """,
        (ts_code, ind_key, start, start, end, end), role=role)
    return pd.DataFrame(rows)


def scores(cap_key: str, trade_date: dt.date, codes: list[str] | None = None,
           *, role: str = 'reader') -> pd.DataFrame:
    rows = engine.query(
        """
        SELECT s.ts_code, sec.name, sec.board, s.value, s.rank_in_market,
               s.pct_rank, s.quadrant, s.is_stale, s.calculated_at
          FROM fct.score s
          LEFT JOIN ref.security sec USING (ts_code)
         WHERE s.cap_key = %(cap)s AND s.trade_date = %(d)s
           AND (%(codes)s::text[] IS NULL OR s.ts_code = ANY(%(codes)s))
         ORDER BY s.value DESC NULLS LAST
        """,
        {'cap': cap_key, 'd': trade_date, 'codes': codes}, role=role)
    return pd.DataFrame(rows)


def score_wide(trade_date: dt.date, cap_keys: list[str] | None = None,
               *, role: str = 'reader') -> pd.DataFrame:
    """把多个功能点的评分拉成宽表，供四象限散点与个股明细表使用。"""
    rows = engine.query(
        """
        SELECT ts_code, cap_key, value, quadrant
          FROM fct.score
         WHERE trade_date = %(d)s
           AND (%(caps)s::text[] IS NULL OR cap_key = ANY(%(caps)s))
        """,
        {'d': trade_date, 'caps': cap_keys}, role=role)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    quad = (df[df['quadrant'].notna()][['ts_code', 'quadrant']]
            .drop_duplicates('ts_code'))
    wide = df.pivot_table(index='ts_code', columns='cap_key',
                          values='value', aggfunc='first').reset_index()
    wide.columns.name = None
    return wide.merge(quad, on='ts_code', how='left')


def score_detail(ts_code: str, trade_date: dt.date, cap_key: str | None = None,
                 *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT cap_key, total_value, quadrant, is_stale,
               part_key, part_label, part_value, weight, contribution,
               script_key, script_ver
          FROM fct.v_score_detail
         WHERE ts_code = %s AND trade_date = %s
           AND (%s::text IS NULL OR cap_key = %s)
         ORDER BY cap_key, part_key
        """,
        (ts_code, trade_date, cap_key, cap_key), role=role)


def score_series(ts_code: str, cap_key: str, days: int = 120,
                 *, role: str = 'reader') -> pd.DataFrame:
    rows = engine.query(
        """
        SELECT trade_date, value, quadrant FROM fct.score
         WHERE ts_code = %s AND cap_key = %s
         ORDER BY trade_date DESC LIMIT %s
        """,
        (ts_code, cap_key, days), role=role)
    return pd.DataFrame(rows[::-1])


def scored_dates(cap_key: str, *, role: str = 'reader') -> list[dt.date]:
    rows = engine.query(
        'SELECT DISTINCT trade_date FROM fct.score WHERE cap_key = %s '
        'ORDER BY trade_date DESC', (cap_key,), role=role)
    return [r['trade_date'] for r in rows]


def quadrant_summary(trade_date: dt.date, *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT quadrant, count(*) AS cnt, round(avg(value), 2) AS avg_value
          FROM fct.score
         WHERE trade_date = %s AND cap_key = 'oper_score' AND quadrant IS NOT NULL
         GROUP BY quadrant ORDER BY quadrant
        """,
        (trade_date,), role=role)


# ============================================================ stale 管理

def mark_stale(cap_key: str, *, role: str = 'app') -> int:
    """
    脚本或权重变更后，把受影响的历史结果标记为过期。
    旧系统权重一改，75,995 条历史记录就与新配置不一致，但页面上完全看不出来。
    """
    return engine.execute(
        """
        UPDATE fct.score SET is_stale = true
         WHERE cap_key = %s
           AND calculated_at < coalesce(
                 (SELECT max(updated_at) FROM config.binding WHERE cap_key = %s), now())
        """,
        (cap_key, cap_key), role=role)


def mark_all_stale_for_changed_scripts(*, role: str = 'app') -> int:
    """checksum 变了的脚本 → 它参与的所有功能点结果标 stale。"""
    return engine.execute(
        """
        UPDATE fct.score s SET is_stale = true
         WHERE EXISTS (
            SELECT 1 FROM config.binding b
              JOIN config.script sc ON sc.script_id = b.script_id
             WHERE b.cap_key = s.cap_key AND b.enabled
               AND sc.registered_at > s.calculated_at)
        """, role=role)


def stale_count(*, role: str = 'reader') -> int:
    return engine.scalar('SELECT count(*) FROM fct.score WHERE is_stale', role=role) or 0


def clear_scores(cap_key: str, trade_date: dt.date, *, role: str = 'app') -> int:
    n = engine.execute(
        'DELETE FROM fct.score_detail WHERE cap_key = %s AND trade_date = %s',
        (cap_key, trade_date), role=role)
    n += engine.execute(
        'DELETE FROM fct.score WHERE cap_key = %s AND trade_date = %s',
        (cap_key, trade_date), role=role)
    return n
