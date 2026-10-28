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
INSERT INTO reference_data.security (security_code, symbol, market, name, pinyin, pinyin_abbr,
                          board, asset_type, is_st, list_date,
                          total_share, float_share, is_active)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (security_code) DO UPDATE SET
    symbol = EXCLUDED.symbol, market = EXCLUDED.market,
    name = coalesce(EXCLUDED.name, reference_data.security.name),
    pinyin = coalesce(EXCLUDED.pinyin, reference_data.security.pinyin),
    pinyin_abbr = coalesce(EXCLUDED.pinyin_abbr, reference_data.security.pinyin_abbr),
    board = coalesce(EXCLUDED.board, reference_data.security.board),
    asset_type = EXCLUDED.asset_type,
    is_st = EXCLUDED.is_st,
    list_date = coalesce(EXCLUDED.list_date, reference_data.security.list_date),
    total_share = coalesce(EXCLUDED.total_share, reference_data.security.total_share),
    float_share = coalesce(EXCLUDED.float_share, reference_data.security.float_share),
    is_active = EXCLUDED.is_active,
    updated_at = now()
"""


def upsert_securities(rows: list[tuple], *, role: str = 'etl') -> int:
    return engine.executemany(UPSERT_SECURITY, rows, role=role)


def get(security_code: str, *, role: str = 'reader') -> dict | None:
    return engine.query_one(
        'SELECT * FROM reference_data.security WHERE security_code = %s', (security_code,), role=role)


def stock_list(*, board: str | None = None, market: str | None = None,
               asset_type: str = 'stock', active: bool = True,
               role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT security_code, symbol, market, name, board, asset_type, is_st,
               total_share, float_share
          FROM reference_data.security
         WHERE (%s::text IS NULL OR board = %s)
           AND (%s::text IS NULL OR market = %s)
           AND (%s::text IS NULL OR asset_type = %s)
           AND (NOT %s OR is_active)
         ORDER BY security_code
        """,
        (board, board, market, market, asset_type, asset_type, active), role=role)


def _like(kw: str, wrap: bool) -> str:
    """把用户输入拼成 LIKE 样式，**先转义 `%`/`_`/`\\`**。

    不转义会怎样：搜索框里出现 `%` 或 `_`（粘贴一段带百分号的文字、或随手打错）时，
    它们变成通配符 ⇒ 接口回"匹配到 30 只"，其实一只都没搜到，而屏上一切看起来正常。
    """
    esc = kw.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{esc}%' if wrap else f'{esc}%'


def search(keyword: str, limit: int = 30, *, asset_types: list[str] | None = None,
           role: str = 'reader') -> list[dict]:
    """代码/名称/全拼/首字母 检索。首字母检索沿用 financeWeb 已验证的思路。

    `asset_types`（如 `['stock','etf']`）给"只能填可交易标的"的那些入口（建仓、提醒）收窄；
    默认不收窄 —— 选股研究那一族要能搜到指数，但要**在屏上看清它是指数**，
    所以这里把 `asset_type` 一起交出去，由界面标出来，而不是悄悄滤掉。
    """
    kw = (keyword or '').strip()
    if not kw:
        return []
    mid, head = _like(kw, True), _like(kw.lower(), False)
    kinds = [k for k in (asset_types or []) if k]
    kind_sql = 'AND asset_type = ANY(%s) ' if kinds else ''
    params: list = [mid, mid, mid, head, head] + ([kinds] if kinds else []) + [kw, kw, limit]
    return engine.query(
        f"""
        SELECT security_code, symbol, name, market, board, asset_type,
               pinyin, pinyin_abbr
          FROM reference_data.security
         WHERE (symbol ILIKE %s OR security_code ILIKE %s OR name ILIKE %s
                OR lower(pinyin) LIKE %s OR lower(pinyin_abbr) LIKE %s)
         {kind_sql}
         ORDER BY coalesce(symbol = %s, false) DESC,
                  coalesce(lower(pinyin_abbr) = lower(%s), false) DESC,
                  (asset_type = 'stock') DESC, security_code
         LIMIT %s
        """,
        tuple(params), role=role)


def codes_missing_board(*, role: str = 'reader') -> list[str]:
    rows = engine.query(
        "SELECT security_code FROM reference_data.security WHERE board IS NULL AND asset_type = 'stock'",
        role=role)
    return [r['security_code'] for r in rows]


# ============================================================ 交易日历

def upsert_calendar(dates: list[dt.date], *, role: str = 'etl') -> int:
    """
    灌交易日历。表驱动是刚需：旧 scheduler._is_trading_day 三个 return 全是
    weekday() < 5，节假日照样触发任务并产出垃圾数据。
    """
    n = engine.executemany(
        """
        INSERT INTO reference_data.trade_calendar (calendar_date, is_open)
        VALUES (%s, true)
        ON CONFLICT (calendar_date) DO UPDATE SET is_open = true
        """,
        [(d,) for d in dates], role=role)
    # 补全非交易日与前后交易日指针
    engine.execute(
        """
        INSERT INTO reference_data.trade_calendar (calendar_date, is_open)
        SELECT d::date, false FROM generate_series(
                   (SELECT min(calendar_date) FROM reference_data.trade_calendar),
                   (SELECT max(calendar_date) FROM reference_data.trade_calendar),
                   interval '1 day') d
        ON CONFLICT (calendar_date) DO NOTHING
        """, role=role)
    engine.execute(
        """
        WITH ord AS (
            SELECT calendar_date,
                   lag(calendar_date)  OVER (ORDER BY calendar_date) AS p,
                   lead(calendar_date) OVER (ORDER BY calendar_date) AS n
              FROM reference_data.trade_calendar WHERE is_open)
        UPDATE reference_data.trade_calendar c
           SET prev_open_date = o.p, next_open_date = o.n
          FROM ord o WHERE o.calendar_date = c.calendar_date
        """, role=role)
    return n


def is_trading_day(d: dt.date, *, role: str = 'reader') -> bool:
    return bool(engine.scalar(
        'SELECT is_open FROM reference_data.trade_calendar WHERE calendar_date = %s', (d,), role=role))


def latest_trade_date(*, role: str = 'reader') -> dt.date | None:
    """15:00 前算作上一交易日；叠加日历的 is_open。"""
    return engine.scalar('SELECT latest FROM reference_data.view_latest_trade_date', role=role)


def prev_trade_date(d: dt.date, *, role: str = 'reader') -> dt.date | None:
    return engine.scalar(
        'SELECT prev_open_date FROM reference_data.trade_calendar WHERE calendar_date = %s', (d,), role=role)


def next_trade_date(d: dt.date, *, role: str = 'reader') -> dt.date | None:
    return engine.scalar(
        'SELECT next_open_date FROM reference_data.trade_calendar WHERE calendar_date = %s', (d,), role=role)


def trade_dates(start: dt.date, end: dt.date, *, role: str = 'reader') -> list[dt.date]:
    rows = engine.query(
        'SELECT calendar_date FROM reference_data.trade_calendar '
        'WHERE is_open AND calendar_date BETWEEN %s AND %s ORDER BY calendar_date',
        (start, end), role=role)
    return [r['calendar_date'] for r in rows]


def recent_trade_dates(n: int, end: dt.date | None = None,
                       *, role: str = 'reader') -> list[dt.date]:
    rows = engine.query(
        """
        SELECT calendar_date FROM reference_data.trade_calendar
         WHERE is_open AND (%s::date IS NULL OR calendar_date <= %s)
         ORDER BY calendar_date DESC LIMIT %s
        """,
        (end, end, n), role=role)
    return [r['calendar_date'] for r in reversed(rows)]


# ============================================================ 板块

def upsert_sector_info(rows: list[tuple], *, role: str = 'etl') -> int:
    return engine.executemany(
        """
        INSERT INTO reference_data.sector_info (sector_code, name, sector_type, level, parent_code)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (sector_code) DO UPDATE SET
            name = EXCLUDED.name, sector_type = EXCLUDED.sector_type,
            level = EXCLUDED.level, parent_code = EXCLUDED.parent_code
        """, rows, role=role)


def upsert_sector_members(rows: list[tuple], *, role: str = 'etl') -> int:
    return engine.executemany(
        """
        INSERT INTO reference_data.sector_member (sector_code, security_code, in_date)
        VALUES (%s, %s, %s)
        ON CONFLICT (sector_code, security_code) DO NOTHING
        """, rows, role=role)


def sector_members(sector_code: str, *, role: str = 'reader') -> list[str]:
    rows = engine.query(
        'SELECT security_code FROM reference_data.sector_member WHERE sector_code = %s ORDER BY security_code',
        (sector_code,), role=role)
    return [r['security_code'] for r in rows]


def sectors_of(security_code: str, *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT m.sector_code, i.name, i.sector_type
          FROM reference_data.sector_member m JOIN reference_data.sector_info i USING (sector_code)
         WHERE m.security_code = %s ORDER BY i.sector_type, i.name
        """,
        (security_code,), role=role)


def sector_list(sector_type: str | None = None, *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT i.sector_code, i.name, i.sector_type,
               count(m.security_code) AS member_count
          FROM reference_data.sector_info i
          LEFT JOIN reference_data.sector_member m USING (sector_code)
         WHERE i.is_active AND (%s::text IS NULL OR i.sector_type = %s)
         GROUP BY i.sector_code, i.name, i.sector_type
         ORDER BY i.sector_type, i.name
        """,
        (sector_type, sector_type), role=role)


# ============================================================ 分红与股本

def upsert_dividends(rows: list[tuple], *, role: str = 'etl') -> int:
    return engine.executemany(
        """
        INSERT INTO reference_data.dividend (security_code, ex_date, cash_per_share,
                                  bonus_ratio, rights_ratio, rights_price)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (security_code, ex_date) DO UPDATE SET
            cash_per_share = EXCLUDED.cash_per_share,
            bonus_ratio = EXCLUDED.bonus_ratio,
            rights_ratio = EXCLUDED.rights_ratio,
            rights_price = EXCLUDED.rights_price
        """, rows, role=role)


def upsert_share_capital(rows: list[tuple], *, role: str = 'etl') -> int:
    return engine.executemany(
        """
        INSERT INTO reference_data.share_capital (security_code, change_date, total_share, float_share)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (security_code, change_date) DO UPDATE SET
            total_share = EXCLUDED.total_share, float_share = EXCLUDED.float_share
        """, rows, role=role)
