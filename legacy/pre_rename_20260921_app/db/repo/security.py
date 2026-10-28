# -*- coding: utf-8 -*-
"""证券基础信息、交易日历、板块。"""
from __future__ import annotations

import datetime as dt

from app import config
from app.db import engine

# ============================================================ 代码与板块判定


def full_code(symbol: str, market: str) -> str:
    return f'{symbol}.{market.upper()}'


def guess_board(symbol: str, market: str) -> str | None:
    """
    按代码前缀判定板块。板块决定涨跌停幅度，必须算对：
    主板 10% / 创业板 20% / 科创板 20% / 北交所 30%（ST 主板 5%）。
    """
    market = market.upper()
    if market == 'BJ':
        return '北交所'
    for board, prefixes in config.BOARD_PREFIX.items():
        if symbol.startswith(prefixes):
            return board
    if symbol.startswith(config.A_SHARE_PREFIX.get(market, ())):
        return '主板'
    return None


def guess_asset_type(symbol: str, market: str) -> str:
    market = market.upper()
    if symbol.startswith(config.SECTOR_PREFIX):
        return 'sector'
    if symbol.startswith(config.ETF_PREFIX.get(market, ())):
        return 'etf'
    if symbol.startswith(config.A_SHARE_PREFIX.get(market, ())):
        return 'stock'
    if symbol.startswith(config.INDEX_PREFIX.get(market, ())):
        return 'index'
    return 'other'


# ============================================================ 证券

UPSERT_SECURITY = """
INSERT INTO ref.security (ts_code, symbol, market, name, pinyin, pinyin_abbr,
                          board, asset_type, is_st, list_date,
                          total_share, float_share, is_active)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (ts_code) DO UPDATE SET
    symbol = EXCLUDED.symbol, market = EXCLUDED.market,
    name = coalesce(EXCLUDED.name, ref.security.name),
    pinyin = coalesce(EXCLUDED.pinyin, ref.security.pinyin),
    pinyin_abbr = coalesce(EXCLUDED.pinyin_abbr, ref.security.pinyin_abbr),
    board = coalesce(EXCLUDED.board, ref.security.board),
    asset_type = EXCLUDED.asset_type,
    is_st = EXCLUDED.is_st,
    list_date = coalesce(EXCLUDED.list_date, ref.security.list_date),
    total_share = coalesce(EXCLUDED.total_share, ref.security.total_share),
    float_share = coalesce(EXCLUDED.float_share, ref.security.float_share),
    is_active = EXCLUDED.is_active,
    updated_at = now()
"""


def upsert_securities(rows: list[tuple], *, role: str = 'etl') -> int:
    return engine.executemany(UPSERT_SECURITY, rows, role=role)


def get(ts_code: str, *, role: str = 'reader') -> dict | None:
    return engine.query_one(
        'SELECT * FROM ref.security WHERE ts_code = %s', (ts_code,), role=role)


def stock_list(*, board: str | None = None, market: str | None = None,
               asset_type: str = 'stock', active: bool = True,
               role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT ts_code, symbol, market, name, board, asset_type, is_st,
               total_share, float_share
          FROM ref.security
         WHERE (%s::text IS NULL OR board = %s)
           AND (%s::text IS NULL OR market = %s)
           AND (%s::text IS NULL OR asset_type = %s)
           AND (NOT %s OR is_active)
         ORDER BY ts_code
        """,
        (board, board, market, market, asset_type, asset_type, active), role=role)


def search(keyword: str, limit: int = 30, *, role: str = 'reader') -> list[dict]:
    """代码/名称/全拼/首字母 检索。首字母检索沿用 financeWeb 已验证的思路。"""
    kw = (keyword or '').strip()
    if not kw:
        return []
    like = f'%{kw}%'
    prefix = f'{kw.lower()}%'
    return engine.query(
        """
        SELECT ts_code, symbol, name, market, board, asset_type
          FROM ref.security
         WHERE symbol LIKE %s OR ts_code LIKE %s OR name LIKE %s
            OR lower(pinyin) LIKE %s OR lower(pinyin_abbr) LIKE %s
         ORDER BY (symbol = %s) DESC, (lower(pinyin_abbr) = lower(%s)) DESC, ts_code
         LIMIT %s
        """,
        (like, like, like, prefix, prefix, kw, kw, limit), role=role)


def codes_missing_board(*, role: str = 'reader') -> list[str]:
    rows = engine.query(
        "SELECT ts_code FROM ref.security WHERE board IS NULL AND asset_type = 'stock'",
        role=role)
    return [r['ts_code'] for r in rows]


# ============================================================ 交易日历

def upsert_calendar(dates: list[dt.date], *, role: str = 'etl') -> int:
    """
    灌交易日历。表驱动是刚需：旧 scheduler._is_trading_day 三个 return 全是
    weekday() < 5，节假日照样触发任务并产出垃圾数据。
    """
    n = engine.executemany(
        """
        INSERT INTO ref.trade_calendar (cal_date, is_open)
        VALUES (%s, true)
        ON CONFLICT (cal_date) DO UPDATE SET is_open = true
        """,
        [(d,) for d in dates], role=role)
    # 补全非交易日与前后交易日指针
    engine.execute(
        """
        INSERT INTO ref.trade_calendar (cal_date, is_open)
        SELECT d::date, false FROM generate_series(
                   (SELECT min(cal_date) FROM ref.trade_calendar),
                   (SELECT max(cal_date) FROM ref.trade_calendar),
                   interval '1 day') d
        ON CONFLICT (cal_date) DO NOTHING
        """, role=role)
    engine.execute(
        """
        WITH ord AS (
            SELECT cal_date,
                   lag(cal_date)  OVER (ORDER BY cal_date) AS p,
                   lead(cal_date) OVER (ORDER BY cal_date) AS n
              FROM ref.trade_calendar WHERE is_open)
        UPDATE ref.trade_calendar c
           SET prev_open = o.p, next_open = o.n
          FROM ord o WHERE o.cal_date = c.cal_date
        """, role=role)
    return n


def is_trading_day(d: dt.date, *, role: str = 'reader') -> bool:
    return bool(engine.scalar(
        'SELECT is_open FROM ref.trade_calendar WHERE cal_date = %s', (d,), role=role))


def latest_trade_date(*, role: str = 'reader') -> dt.date | None:
    """15:00 前算作上一交易日；叠加日历的 is_open。"""
    return engine.scalar('SELECT latest FROM ref.v_latest_trade_date', role=role)


def prev_trade_date(d: dt.date, *, role: str = 'reader') -> dt.date | None:
    return engine.scalar(
        'SELECT prev_open FROM ref.trade_calendar WHERE cal_date = %s', (d,), role=role)


def next_trade_date(d: dt.date, *, role: str = 'reader') -> dt.date | None:
    return engine.scalar(
        'SELECT next_open FROM ref.trade_calendar WHERE cal_date = %s', (d,), role=role)


def trade_dates(start: dt.date, end: dt.date, *, role: str = 'reader') -> list[dt.date]:
    rows = engine.query(
        'SELECT cal_date FROM ref.trade_calendar '
        'WHERE is_open AND cal_date BETWEEN %s AND %s ORDER BY cal_date',
        (start, end), role=role)
    return [r['cal_date'] for r in rows]


def recent_trade_dates(n: int, end: dt.date | None = None,
                       *, role: str = 'reader') -> list[dt.date]:
    rows = engine.query(
        """
        SELECT cal_date FROM ref.trade_calendar
         WHERE is_open AND (%s::date IS NULL OR cal_date <= %s)
         ORDER BY cal_date DESC LIMIT %s
        """,
        (end, end, n), role=role)
    return [r['cal_date'] for r in reversed(rows)]


# ============================================================ 板块

def upsert_sector_info(rows: list[tuple], *, role: str = 'etl') -> int:
    return engine.executemany(
        """
        INSERT INTO ref.sector_info (sector_code, name, sector_type, level, parent_code)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (sector_code) DO UPDATE SET
            name = EXCLUDED.name, sector_type = EXCLUDED.sector_type,
            level = EXCLUDED.level, parent_code = EXCLUDED.parent_code
        """, rows, role=role)


def upsert_sector_members(rows: list[tuple], *, role: str = 'etl') -> int:
    return engine.executemany(
        """
        INSERT INTO ref.sector_member (sector_code, ts_code, in_date)
        VALUES (%s, %s, %s)
        ON CONFLICT (sector_code, ts_code) DO NOTHING
        """, rows, role=role)


def sector_members(sector_code: str, *, role: str = 'reader') -> list[str]:
    rows = engine.query(
        'SELECT ts_code FROM ref.sector_member WHERE sector_code = %s ORDER BY ts_code',
        (sector_code,), role=role)
    return [r['ts_code'] for r in rows]


def sectors_of(ts_code: str, *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT m.sector_code, i.name, i.sector_type
          FROM ref.sector_member m JOIN ref.sector_info i USING (sector_code)
         WHERE m.ts_code = %s ORDER BY i.sector_type, i.name
        """,
        (ts_code,), role=role)


def sector_list(sector_type: str | None = None, *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT i.sector_code, i.name, i.sector_type,
               count(m.ts_code) AS member_count
          FROM ref.sector_info i
          LEFT JOIN ref.sector_member m USING (sector_code)
         WHERE i.is_active AND (%s::text IS NULL OR i.sector_type = %s)
         GROUP BY i.sector_code, i.name, i.sector_type
         ORDER BY i.sector_type, i.name
        """,
        (sector_type, sector_type), role=role)


# ============================================================ 分红与股本

def upsert_dividends(rows: list[tuple], *, role: str = 'etl') -> int:
    return engine.executemany(
        """
        INSERT INTO ref.dividend (ts_code, ex_date, cash_per_share,
                                  bonus_ratio, rights_ratio, rights_price)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (ts_code, ex_date) DO UPDATE SET
            cash_per_share = EXCLUDED.cash_per_share,
            bonus_ratio = EXCLUDED.bonus_ratio,
            rights_ratio = EXCLUDED.rights_ratio,
            rights_price = EXCLUDED.rights_price
        """, rows, role=role)


def upsert_share_capital(rows: list[tuple], *, role: str = 'etl') -> int:
    return engine.executemany(
        """
        INSERT INTO ref.share_capital (ts_code, change_date, total_share, float_share)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (ts_code, change_date) DO UPDATE SET
            total_share = EXCLUDED.total_share, float_share = EXCLUDED.float_share
        """, rows, role=role)
