# -*- coding: utf-8 -*-
"""自选股与关注池。"关注"是自选的一个特殊分组，不需要独立的表和页面。"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.db import engine

# 自选组的归属用户。**必须和迁移进来的历史数据一致**（一次性搬迁脚本
# `legacy/pre_rename_20260921_scripts/migrate/b5_fct.py` 里写死的就是 'fin'，
# 那份已归档、不再跑）。
#
# ★这不是可有可无的默认值：`config.watchlist_group` 的唯一约束是 `(owner, name)`，
# 而这一列的库默认值是 `current_user` —— 网站连库用的是 ft_app 角色，于是
# 页面新建的组会落到 owner='ft_app'，和老组不在同一个命名空间里。后果是
# **重名不再报错**：列表里能出现两行同名的组，而 `q_group_trend` 里那句
# `SELECT DISTINCT group_name` 会因此给同一个名字画出两条曲线。
# 系统目前没有登录，所有组都属于同一个人，所以钉死成一个值。
DEFAULT_OWNER = 'fin'


def groups(owner: str | None = None, *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT g.group_id, g.name, g.owner, g.is_focus, g.display_order, g.note,
               count(s.security_code) AS stock_count
          FROM config.watchlist_group g
          LEFT JOIN config.watchlist_stock s USING (group_id)
         WHERE (%s::text IS NULL OR g.owner = %s)
         GROUP BY g.group_id, g.name, g.owner, g.is_focus, g.display_order, g.note
         ORDER BY g.is_focus DESC, g.display_order, g.name
        """,
        (owner, owner), role=role)


def create_group(name: str, *, owner: str = DEFAULT_OWNER,
                 is_focus: bool = False,
                 note: str | None = None, role: str = 'app') -> int:
    return engine.scalar(
        """
        INSERT INTO config.watchlist_group (name, owner, is_focus, note)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (owner, name) DO UPDATE SET is_focus = EXCLUDED.is_focus,
                                                note = EXCLUDED.note
        RETURNING group_id
        """,
        (name, owner, is_focus, note), role=role)


def delete_group(group_id: int, *, role: str = 'app') -> int:
    return engine.execute(
        'DELETE FROM config.watchlist_group WHERE group_id = %s', (group_id,), role=role)


def add_stocks(group_id: int, codes: list[str], *, cost: float | None = None,
               note: str | None = None, role: str = 'app') -> int:
    return engine.executemany(
        """
        INSERT INTO config.watchlist_stock (group_id, security_code, cost, note)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (group_id, security_code) DO UPDATE SET
            cost = coalesce(EXCLUDED.cost, config.watchlist_stock.cost),
            note = coalesce(EXCLUDED.note, config.watchlist_stock.note)
        """,
        [(group_id, c, cost, note) for c in codes], role=role)


def remove_stocks(group_id: int, codes: list[str], *, role: str = 'app') -> int:
    return engine.execute(
        'DELETE FROM config.watchlist_stock WHERE group_id = %s AND security_code = ANY(%s)',
        (group_id, codes), role=role)


def stocks(group_id: int, *, role: str = 'reader') -> list[str]:
    rows = engine.query(
        'SELECT security_code FROM config.watchlist_stock WHERE group_id = %s ORDER BY security_code',
        (group_id,), role=role)
    return [r['security_code'] for r in rows]


def focus_codes(*, role: str = 'reader') -> list[str]:
    rows = engine.query(
        """
        SELECT DISTINCT s.security_code FROM config.watchlist_stock s
          JOIN config.watchlist_group g USING (group_id)
         WHERE g.is_focus ORDER BY s.security_code
        """, role=role)
    return [r['security_code'] for r in rows]


def enhanced(group_id: int, trade_date: dt.date, *, role: str = 'reader') -> pd.DataFrame:
    """
    增强视图：近 5 日收盘 + 10/20 日涨幅振幅 + 评分。
    迁自 financeWeb 的 watchlist_stocks_enhanced，但口径写清楚且不再靠前端拼 SQL。
    """
    rows = engine.query(
        """
        WITH w AS (
            SELECT security_code, cost, note FROM config.watchlist_stock WHERE group_id = %(g)s),
        dates AS (
            SELECT calendar_date, row_number() OVER (ORDER BY calendar_date DESC) AS rn
              FROM reference_data.trade_calendar
             WHERE is_open AND calendar_date <= %(d)s
             ORDER BY calendar_date DESC LIMIT 21),
        px AS (
            SELECT d.security_code, dd.rn, d.close, d.high, d.low, d.pre_close,
                   d.pct_change, d.amount_yuan, d.turnover_rate_percent
              FROM market_data.stock_daily d
              JOIN dates dd ON dd.calendar_date = d.trade_date
              JOIN w ON w.security_code = d.security_code),
        agg AS (
            SELECT security_code,
                   max(close) FILTER (WHERE rn = 1)  AS close_d0,
                   max(close) FILTER (WHERE rn = 2)  AS close_d1,
                   max(close) FILTER (WHERE rn = 3)  AS close_d2,
                   max(close) FILTER (WHERE rn = 4)  AS close_d3,
                   max(close) FILTER (WHERE rn = 5)  AS close_d4,
                   max(pct_change) FILTER (WHERE rn = 1) AS pct_change,
                   max(amount_yuan) FILTER (WHERE rn = 1) AS amount_yuan,
                   max(turnover_rate_percent) FILTER (WHERE rn = 1) AS turnover_rate_percent,
                   max(close) FILTER (WHERE rn = 11) AS close_d10,
                   max(close) FILTER (WHERE rn = 21) AS close_d20,
                   avg((high - low) / nullif(pre_close, 0) * 100)
                       FILTER (WHERE rn <= 10) AS amp10,
                   avg((high - low) / nullif(pre_close, 0) * 100)
                       FILTER (WHERE rn <= 20) AS amp20
              FROM px GROUP BY security_code)
        SELECT a.security_code, sec.name, sec.board, w.cost, w.note,
               a.close_d0, a.close_d1, a.close_d2, a.close_d3, a.close_d4,
               a.pct_change, a.amount_yuan, a.turnover_rate_percent,
               round((a.close_d0 - a.close_d10) / nullif(a.close_d10, 0) * 100, 2) AS chg10,
               round((a.close_d0 - a.close_d20) / nullif(a.close_d20, 0) * 100, 2) AS chg20,
               round(a.amp10::numeric, 2) AS amp10,
               round(a.amp20::numeric, 2) AS amp20,
               round((a.close_d0 - w.cost) / nullif(w.cost, 0) * 100, 2) AS profit_pct,
               sc.value AS oper_score, sc.quadrant
          FROM agg a
          JOIN w USING (security_code)
          LEFT JOIN reference_data.security sec ON sec.security_code = a.security_code
          LEFT JOIN analytics.score sc ON sc.security_code = a.security_code
               AND sc.trade_date = %(d)s AND sc.capability_key = 'oper_score'
         ORDER BY a.pct_change DESC NULLS LAST
        """,
        {'g': group_id, 'd': trade_date}, role=role)
    return pd.DataFrame(rows)
