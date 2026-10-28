# -*- coding: utf-8 -*-
"""
指标计算：把 config.indicator 的 27 条公式算成 fct.indicator_value 的行。

**按交易日算，不算全历史。** 27 个指标 × 5,646 只 × 1,228 天 = 1.87 亿行，
存下来毫无收益：复盘只看最近的截面，历史指标要用时按需重算即可。
所以对外接口是 compute_date(某个交易日)，回溯窗口只为了让时序函数有料。

**回溯窗口自动推导**：从公式里取最大的整数常量（MA(close,250) 取 250），
再留余量。写死一个 250 不行——将来在页面上改成 MA(close,500) 就会静默算出
一片 NaN，而不是报错。

**板块归属取一级行业**：一只票可能同时属于多个概念板块，`MEAN(pct_chg BY sector)`
必须有唯一分组。取 880* 行业板块里代码最小的那个作为主行业，概念板块不参与。
"""
from __future__ import annotations

import datetime as dt
import time

import pandas as pd

from app.compute import expr as ex
from app.db import engine

# 面板要 SELECT 的基础列。公式里可能用到的字段都在这，多取一两列不值得优化
PANEL_COLS = ('open', 'high', 'low', 'close', 'pre_close', 'change_yuan',
              'pct_chg', 'vol_shares', 'amount_yuan', 'turnover_rate_pct',
              'limit_up', 'limit_down', 'adj_factor')

LOOKBACK_MIN = 60
LOOKBACK_MAX = 400
# MA(close,250) 需要正好 250 行，留 12 天余量足够（停牌造成的空档）。
# 乘系数会把面板放大到 400 天、多读 70 万行，纯浪费。
LOOKBACK_MARGIN = 12


def load_specs(ind_keys: list[str] | None = None, *,
               role: str = 'reader') -> list[dict]:
    rows = engine.query(
        """
        SELECT ind_key, name, kind, expr, params, depends_on, decimals
          FROM config.indicator
         WHERE enabled AND (%s::text[] IS NULL OR ind_key = ANY(%s))
         ORDER BY ind_key
        """, (ind_keys, ind_keys), role=role)
    return rows


def required_lookback(specs: list[dict]) -> int:
    """所有公式里最大的整数常量决定窗口长度。"""
    import ast
    biggest = 0
    for s in specs:
        try:
            tree = ex.check(s['expr'])
        except ex.ExprError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, int):
                biggest = max(biggest, n.value)
    return int(min(LOOKBACK_MAX, max(LOOKBACK_MIN, biggest + LOOKBACK_MARGIN)))


def sector_map(*, role: str = 'reader') -> dict[str, str]:
    """
    代码 → 主板块。

    两条硬约束，缺一个下游都会静默失效：

    1. **板块必须在 md.sector_daily 里有日线**。这个映射的唯一用途是让
       `ctx.broadcast()` 把板块级分值摊回个股，摊不回去就全是空值。
       B7 归一之前这里按 `sector_type = '行业'` 取，取到的是
       `ZS_中证800` 这类**指数成分股**（旧库把它们错标成"行业"），
       而 md.sector_daily 里只有 `TDX_880xxx`，交集为 0 ——
       板块热度四个分项因此全部为空，且不报任何错。

    2. **一只票只能有一个分组**。`MEAN(pct_chg BY sector)` 需要唯一分组，
       而一只票同时属于多个概念和风格板块。

    优先行业、其次概念：行业分类互斥且稳定，概念是"这段时间市场怎么讲故事"。
    同一层级内取代码最小的那个，保证结果可复现。

    **当前口径缺口**：通达信行业板块（880001-880499）在 ref.sector_member
    里一条成员记录都没有 —— 从来没采过。所以实际生效的是概念板块，
    覆盖约 5,400 只个股。补行业成分需要另解析通达信 block 文件。
    """
    rows = engine.query(
        """
        WITH have_daily AS (
            SELECT DISTINCT sector_code FROM md.sector_daily
        )
        SELECT m.ts_code,
               -- 排序前缀 0/1 把行业排在概念前面，取到后再切掉
               substring(min(CASE WHEN i.sector_type = '行业' THEN '0' ELSE '1' END
                              || m.sector_code) from 2) AS sector
          FROM ref.sector_member m
          JOIN ref.sector_info i ON i.sector_code = m.sector_code
          JOIN have_daily h ON h.sector_code = m.sector_code
         WHERE m.out_date IS NULL
           AND i.sector_type IN ('行业', '概念')
         GROUP BY m.ts_code
        """, role=role)
    return {r['ts_code']: r['sector'] for r in rows}


def load_panel(trade_date: dt.date, lookback: int, *,
               codes: list[str] | None = None, role: str = 'reader') -> pd.DataFrame:
    cols = ', '.join('d.' + c for c in PANEL_COLS)
    rows = engine.query(
        f"""
        WITH dates AS (
            SELECT cal_date FROM ref.trade_calendar
             WHERE is_open AND cal_date <= %(d)s
             ORDER BY cal_date DESC LIMIT %(n)s)
        SELECT d.ts_code, d.trade_date, {cols}, s.board, s.is_st, s.float_share
          FROM md.stock_daily d
          JOIN ref.security s USING (ts_code)
         WHERE d.trade_date IN (SELECT cal_date FROM dates)
           AND s.asset_type = 'stock'
           AND (%(codes)s::text[] IS NULL OR d.ts_code = ANY(%(codes)s))
         ORDER BY d.ts_code, d.trade_date
        """,
        {'d': trade_date, 'n': lookback, 'codes': codes}, role=role)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for c in (*PANEL_COLS, 'float_share'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    smap = sector_map(role=role)
    df['sector'] = df['ts_code'].map(smap)
    return df.set_index(['ts_code', 'trade_date']).sort_index()


UPSERT = """
INSERT INTO fct.indicator_value (ts_code, trade_date, ind_key, value, task_id)
SELECT ts_code, trade_date, ind_key, value, task_id FROM md.stg_indval
ON CONFLICT (ts_code, trade_date, ind_key) DO UPDATE SET
    value = EXCLUDED.value, task_id = EXCLUDED.task_id
"""


def _stage(rows: list[tuple]) -> int:
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute('DROP TABLE IF EXISTS md.stg_indval')
        cur.execute('CREATE UNLOGGED TABLE md.stg_indval ('
                    'ts_code text, trade_date date, ind_key text, '
                    'value numeric(24,8), task_id bigint)')
    engine.copy_rows('md.stg_indval',
                     ['ts_code', 'trade_date', 'ind_key', 'value', 'task_id'],
                     iter(rows), role='admin')
    n = engine.execute(UPSERT, role='admin')
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute('DROP TABLE IF EXISTS md.stg_indval')
    return n


def compute_date(trade_date: dt.date, *, ind_keys: list[str] | None = None,
                 codes: list[str] | None = None, task_id: int | None = None,
                 dry_run: bool = False, panel: pd.DataFrame | None = None) -> dict:
    """
    算一个交易日的全部指标并落库。返回每个指标的有效值个数，
    好让调用方看出「算出来了但全是 NaN」这种失败——它比抛异常更危险。

    `panel` 可由调用方传入复用。评分驱动已经为同一天读过一份 262 天的面板，
    base_factors 再读一次是 40 秒和 200 万行的纯浪费。传入时要自己保证
    回溯长度够 —— 不够的话时序函数会静默出 NaN，所以下面校验实际天数。
    """
    specs = load_specs(ind_keys)
    if not specs:
        return {'error': 'config.indicator 里没有启用的指标'}
    lookback = required_lookback(specs)
    t0 = time.time()
    if panel is None:
        panel = load_panel(trade_date, lookback, codes=codes)
    elif not panel.empty:
        have = panel.index.get_level_values('trade_date').nunique()
        if have < lookback:
            return {'error': f'传入面板只有 {have} 个交易日，指标需要 {lookback} 日'}
    if panel.empty:
        return {'error': f'{trade_date} 没有行情数据'}

    stat: dict = {'trade_date': str(trade_date), 'lookback': lookback,
                  'panel_rows': len(panel),
                  'codes': panel.index.get_level_values('ts_code').nunique(),
                  'load_seconds': round(time.time() - t0, 1), 'values': {}}

    target = pd.Timestamp(trade_date).date()
    rows: list[tuple] = []
    failed: dict[str, str] = {}
    for s in specs:
        try:
            series = ex.evaluate(s['expr'], panel)
        except ex.ExprError as exc:
            failed[s['ind_key']] = str(exc)
            continue
        cut = series[series.index.get_level_values('trade_date') == target]
        cut = cut.dropna()
        stat['values'][s['ind_key']] = int(len(cut))
        dec = s['decimals'] if s['decimals'] is not None else 6
        for (code, d), v in cut.items():
            rows.append((code, d, s['ind_key'], round(float(v), dec), task_id))

    stat['rows'] = len(rows)
    if failed:
        stat['failed'] = failed
    if rows and not dry_run:
        stat['written'] = _stage(rows)
    stat['seconds'] = round(time.time() - t0, 1)
    return stat
