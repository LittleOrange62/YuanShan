"""calc_base 的 YuanShan 侧生产者：`config.indicator` 公式 → `ys.indicator_value`。

来源与对等关系（这是"吸收 FinanceTool"的第一个写库生产者，来历写清楚）：
本文件与 FT `app/compute/indicators.py` **逐函数对应**，只有三处不同：

1. 写库目标是 **`ys.indicator_value`**，不是 `ys.indicator_value`。
   双跑期两套并存，绝不同名双写（`docs/吸收FinanceTool方案.md` §3-P2 的硬规则）。
2. 读走 `ft_reader`、写走 `ft_etl`（`compute/db.py`），不再用 FT 的连接池。
3. staging 表跟着建在 `ys` 里（`ft_etl` 只在 `ys` 有 CREATE，`market_data` 里建不了表）。

**公式求值器 `compute/expr.py` 是从 FT 原样拷来的，一个字节都没改** —— 对账要的就是
"数学完全相同、只有管道不同"，改了就没法把差异归因到管道上。要动它先去动 FT 那份
然后整份重拷。

口径细节（回溯窗口自动推导、板块归属取一级行业等）与原文件一致，注释里那两条
"缺一个下游就静默失效"的约束同样适用于这里。
"""

from __future__ import annotations

import datetime as dt
import io
import time

import pandas as pd

from . import db, expr as ex

PANEL_COLS = ('open', 'high', 'low', 'close', 'pre_close', 'change_yuan',
              'pct_change', 'volume_shares', 'amount_yuan', 'turnover_rate_percent',
              'limit_up', 'limit_down', 'adjustment_factor')

LOOKBACK_MIN = 60
LOOKBACK_MAX = 400
LOOKBACK_MARGIN = 12

TARGET = 'ys.indicator_value'
STAGING = 'ys.staging_indicator_value'

UPSERT = f"""
INSERT INTO {TARGET} (security_code, trade_date, indicator_key, value, task_id)
SELECT security_code, trade_date, indicator_key, value, task_id FROM {STAGING}
ON CONFLICT (security_code, trade_date, indicator_key) DO UPDATE SET
    value = EXCLUDED.value, task_id = EXCLUDED.task_id
"""


def load_specs(ind_keys=None):
    return db.query(
        """
        SELECT indicator_key, name, kind, expr, params, depends_on, decimals
          FROM config.indicator
         WHERE enabled AND (%s::text[] IS NULL OR indicator_key = ANY(%s))
         ORDER BY indicator_key
        """, (ind_keys, ind_keys))


def required_lookback(specs):
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


def sector_map():
    return {r['security_code']: r['sector'] for r in db.query(
        """
        WITH have_daily AS (
            SELECT DISTINCT sector_code FROM market_data.sector_daily
        )
        SELECT m.security_code,
               substring(min(CASE WHEN i.sector_type = '行业' THEN '0' ELSE '1' END
                              || m.sector_code) from 2) AS sector
          FROM reference_data.sector_member m
          JOIN reference_data.sector_info i ON i.sector_code = m.sector_code
          JOIN have_daily h ON h.sector_code = m.sector_code
         WHERE m.out_date IS NULL
           AND i.sector_type IN ('行业', '概念')
         GROUP BY m.security_code
        """)}


def load_panel(trade_date, lookback, codes=None):
    cols = ', '.join('d.' + c for c in PANEL_COLS)
    rows = db.query(
        f"""
        WITH dates AS (
            SELECT calendar_date FROM reference_data.trade_calendar
             WHERE is_open AND calendar_date <= %(d)s
             ORDER BY calendar_date DESC LIMIT %(n)s)
        SELECT d.security_code, d.trade_date, {cols}, s.board, s.is_st, s.float_share
          FROM market_data.stock_daily d
          JOIN reference_data.security s USING (security_code)
         WHERE d.trade_date IN (SELECT calendar_date FROM dates)
           AND s.asset_type = 'stock'
           AND (%(codes)s::text[] IS NULL OR d.security_code = ANY(%(codes)s))
         ORDER BY d.security_code, d.trade_date
        """,
        {'d': trade_date, 'n': lookback, 'codes': codes})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for c in (*PANEL_COLS, 'float_share'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['sector'] = df['security_code'].map(sector_map())
    return df.set_index(['security_code', 'trade_date']).sort_index()


def _copy_rows(cur, rows, task_id):
    """COPY 走文本协议：`\\N` 表示 NULL，字段用制表符分隔。

    psycopg2 没有 psycopg3 的 `cur.copy()`，得用 `copy_expert` + 一个文件对象。
    187k 行/场在这个量级上 execute-many 慢一个数量级，所以不图省事。
    """
    buf = io.StringIO()
    for code, d, key, val, tid in rows:
        tid = tid if tid is not None else task_id
        buf.write('%s\t%s\t%s\t%s\t%s\n' % (
            code, d.isoformat() if hasattr(d, 'isoformat') else d, key,
            val if val is not None else r'\N',
            tid if tid is not None else r'\N'))
    buf.seek(0)
    cur.copy_expert(
        f"COPY {STAGING} (security_code, trade_date, indicator_key, value, task_id)"
        " FROM STDIN WITH (FORMAT text)", buf)


def _stage(rows, task_id=None):
    """COPY 进 ys 的 staging，再 upsert 进目标表。一条一条 execute 在这个量级上不成立。"""
    conn = db.connect(readonly=False)
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP TABLE IF EXISTS {STAGING}')
            cur.execute(f'CREATE UNLOGGED TABLE {STAGING} ('
                        'security_code text, trade_date date, indicator_key text, '
                        'value numeric(24,8), task_id bigint)')
            _copy_rows(cur, rows, task_id)
            cur.execute(UPSERT)
            n = cur.rowcount
            cur.execute(f'DROP TABLE IF EXISTS {STAGING}')
        conn.commit()
        return n
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def compute_date(trade_date, ind_keys=None, codes=None, task_id=None,
                 dry_run=False, panel=None):
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

    stat = {'trade_date': str(trade_date), 'lookback': lookback,
            'panel_rows': len(panel),
            'codes': panel.index.get_level_values('security_code').nunique(),
            'load_seconds': round(time.time() - t0, 1), 'values': {}}

    target = pd.Timestamp(trade_date).date()
    rows = []
    failed = {}
    for s in specs:
        try:
            series = ex.evaluate(s['expr'], panel)
        except ex.ExprError as exc:
            failed[s['indicator_key']] = str(exc)
            continue
        cut = series[series.index.get_level_values('trade_date') == target].dropna()
        stat['values'][s['indicator_key']] = int(len(cut))
        dec = s['decimals'] if s['decimals'] is not None else 6
        for (code, d), v in cut.items():
            rows.append((code, d, s['indicator_key'], round(float(v), dec), task_id))

    stat['rows'] = len(rows)
    if failed:
        stat['failed'] = failed
    if rows and not dry_run:
        stat['written'] = _stage(rows, task_id)
    stat['seconds'] = round(time.time() - t0, 1)
    return stat
