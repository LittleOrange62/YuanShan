# -*- coding: utf-8 -*-
"""
C9 · 补换手率与流通股本（针对算不出换手的那批票）。

**缺口是什么**：`md.stock_daily.turnover_rate_pct` 有 254,617 行是空的，
分布在 **351 个代码**上 —— 339 个北交所 920xxx + 12 只近期新股。
换手率的算法是 `vol_shares / ref.security.float_share × 100`，
而这 351 只在 `ref.security` 里没有 float_share（旧库压根没采北交所），
所以整段历史都算不出来，置 NULL 而不是填 0。

**为什么从东财日线取"换手率"而不是先猜股本**：
东财日线自带换手率，是**每一天各自的口径**；用"当前流通股本"回算三年前的
换手率，本身就是错的（次新股股本变化剧烈，解禁前后差几倍）。
拿到当日换手率之后，反过来还能得到当日的流通股本：
    float_share = vol_shares / (换手率 / 100)
这个值是**由成交量和换手率反算出来的实测值**，不是估计，顺手写进
`ref.share_capital`，以后 recompute_turnover 就有分段股本可用。

**通达信本地的 gbbq（股本变迁）为什么没用**：文件能解析（191,071 条，
含北交所），但 pytdx 给的四个字段在 category=5 上语义对不上 ——
920045 在 2024-12-12 那条读出来"后流通盘 1090 万股"比"前流通盘 3181 万股"
还小，流通盘不可能变少。在没有第二个来源对齐之前，按猜的语义算出来的
换手率比空着更危险，所以这里不用它。

用法：
    python scripts\\ingest\\c9_share.py                  # 补所有缺换手的代码
    python scripts\\ingest\\c9_share.py --limit 20       # 先试 20 只
    python scripts\\ingest\\c9_share.py --code 920045.BJ
    python scripts\\ingest\\c9_share.py --recompute-only # 只用库里已有股本回算
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402
from app.db.repo import quote as quote_repo  # noqa: E402
from app.db.repo import security as sec_repo  # noqa: E402

RETRY = 3
SLEEP = 1.2          # 东财对连续请求会掐连接，慢一点比重试便宜


def missing_codes(limit: int | None = None, code: str | None = None) -> list[dict]:
    """缺换手率的代码 + 缺失区间。按缺失行数从多到少，先补影响面大的。"""
    rows = engine.query(
        """
        SELECT ts_code, count(*) AS n,
               min(trade_date) AS d0, max(trade_date) AS d1
          FROM md.stock_daily
         WHERE turnover_rate_pct IS NULL
           AND (%s::text IS NULL OR ts_code = %s)
         GROUP BY ts_code
         ORDER BY count(*) DESC
        """, (code, code))
    return rows[:limit] if limit else rows


def _sina_symbol(ts_code: str) -> str:
    """600519.SH → sh600519。新浪要市场前缀在前面，北交所用 bj。"""
    code, _, mkt = ts_code.partition('.')
    return f'{mkt.lower()}{code}'


def fetch_turnover(ts_code: str, d0: dt.date, d1: dt.date) -> list[dict]:
    """
    每日换手率 + 流通股本。

    **主源是新浪 `stock_zh_a_daily`**：它直接给 `outstanding_share`（流通股本，
    股）和 `turnover`（换手率，**比例不是百分数**），而且覆盖北交所。
    实测 920768.BJ 2026-08-25：volume 1,014,504 / outstanding 107,389,517
    = 0.0094470，与接口给的 turnover 完全相同 —— 所以两列是自洽的，
    百分数只要 ×100。

    东财 `stock_zh_a_hist` 留作备用：2026-08-27 实测 push2his.eastmoney.com
    对所有请求直接掐连接（RemoteDisconnected），而 datacenter-web 那边正常，
    是分域名的故障，不是被封 IP。
    """
    import akshare as ak
    last = None
    for attempt in range(RETRY):
        try:
            df = ak.stock_zh_a_daily(symbol=_sina_symbol(ts_code),
                                     start_date=d0.strftime('%Y%m%d'),
                                     end_date=d1.strftime('%Y%m%d'))
            if df is None or df.empty:
                return []
            rows = []
            for r in df.to_dict('records'):
                try:
                    day = dt.date.fromisoformat(str(r['date'])[:10])
                    turn = float(r['turnover']) * 100.0
                    fs = float(r.get('outstanding_share') or 0)
                except (KeyError, TypeError, ValueError):
                    continue
                if turn > 0:
                    rows.append({'trade_date': day, 'turnover': round(turn, 4),
                                 'float_share': int(fs) if fs > 0 else None})
            return rows
        except Exception as exc:      # 网络抖动/被掐连接，退避重试
            last = exc
            time.sleep(SLEEP * (attempt + 1))
    raise RuntimeError(f'{ts_code} 取数失败：{last}')


def apply_rows(ts_code: str, rows: list[dict]) -> tuple[int, int]:
    """写换手率，并把流通股本按"变化才留一条"的方式存成分段记录。"""
    if not rows:
        return 0, 0
    n = engine.executemany(
        """
        UPDATE md.stock_daily
           SET turnover_rate_pct = %(turnover)s
         WHERE ts_code = %(ts_code)s AND trade_date = %(trade_date)s
           AND turnover_rate_pct IS NULL
        """,
        [{'turnover': r['turnover'], 'trade_date': r['trade_date'],
          'ts_code': ts_code} for r in rows], role='etl')

    # 每天存一条等于把 ref.share_capital 变成日频表，没有意义 ——
    # 它的语义是"从这天起股本是多少"，所以只在变动超过 0.5% 时留一条。
    seg, prev = [], None
    for r in rows:
        fs = r.get('float_share')
        if not fs:
            continue
        if prev is None or abs(fs - prev) / prev > 0.005:
            seg.append((ts_code, r['trade_date'], None, fs))
            prev = fs
    if seg:
        sec_repo.upsert_share_capital(seg)
        # ref.security.float_share 用最新一段，供还没走分段逻辑的地方兜底
        engine.execute('UPDATE ref.security SET float_share = %s WHERE ts_code = %s',
                       (seg[-1][3], ts_code), role='etl')
    return n or len(rows), len(seg)



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int)
    ap.add_argument('--code')
    ap.add_argument('--recompute-only', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if args.recompute_only:
        t0 = time.time()
        n = quote_repo.recompute_turnover(role='admin')
        print('recompute_turnover 更新 %s 行  %.1fs' % (f'{n:,}', time.time() - t0))
        left = engine.scalar(
            'SELECT count(*) FROM md.stock_daily WHERE turnover_rate_pct IS NULL')
        print('仍缺换手率 %s 行' % f'{left:,}')
        return 0

    todo = missing_codes(args.limit, args.code)
    total_missing = sum(r['n'] for r in todo)
    print('=== 缺换手率的代码 ===')
    print('  %s 个代码 / %s 行' % (len(todo), f'{total_missing:,}'))
    if not todo:
        print('\nDONE（没有缺口）')
        return 0

    batch_id = 0
    if not args.dry_run:
        batch_id = ops_repo.begin_batch('akshare_em', 'md.stock_daily.turnover')
        print('  批次 %s' % batch_id)

    ok = fail = filled = segs = 0
    try:
        for i, r in enumerate(todo, 1):
            code = r['ts_code']
            try:
                rows = fetch_turnover(code, r['d0'], r['d1'])
            except RuntimeError as exc:
                fail += 1
                print('  [%s/%s] %s  失败：%s' % (i, len(todo), code, exc),
                      flush=True)
                continue
            if args.dry_run:
                print('  [%s/%s] %s  取到 %s 行（dry-run 不写）'
                      % (i, len(todo), code, len(rows)), flush=True)
                ok += 1
                continue
            n, s = apply_rows(code, rows)
            filled += n
            segs += s
            ok += 1
            print('  [%s/%s] %s  缺 %s 行 → 写 %s 行  股本分段 %s'
                  % (i, len(todo), code, r['n'], n, s), flush=True)
            time.sleep(SLEEP)
        if batch_id:
            ops_repo.end_batch(batch_id, row_count=filled,
                               status='ok' if not fail else 'partial',
                               message=None if not fail else f'{fail} 个代码取数失败')
    except Exception as exc:
        if batch_id:
            ops_repo.end_batch(batch_id, row_count=filled, status='failed',
                               message=str(exc)[:500])
        raise

    print('=== 结果 ===')
    print('  成功 %s 个 / 失败 %s 个  写换手 %s 行  股本分段 %s 条'
          % (ok, fail, f'{filled:,}', segs))
    if not args.dry_run:
        left = engine.scalar(
            'SELECT count(*) FROM md.stock_daily WHERE turnover_rate_pct IS NULL')
        print('  仍缺换手率 %s 行' % f'{left:,}')
    print('\nDONE')
    return 0 if not fail else 1


if __name__ == '__main__':
    raise SystemExit(main())
