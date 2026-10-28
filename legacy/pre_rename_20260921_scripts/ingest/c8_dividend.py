# -*- coding: utf-8 -*-
"""
C8 · 采分红送配填 ref.dividend，再回算 md.stock_daily.adj_factor。

**为什么这是 P0**：`ref.dividend` 是空表 → `adj_factor` 全 NULL → 取数时
`coalesce(adj_factor, 1)` → 等于**全部数据未复权**。后果不是"少一列"，是
跨除权日的涨跌幅、均线、形态类指标全部失真：一只 10 送 10 的票在除权日
当天的 close 会腰斩，未复权的均线会被这根假跌破带下去，形态分跟着错。
旧系统把 adj_factor 硬编码成 1.0，等于把这个问题藏起来了。

**为什么按报告期批量拿而不是按股票逐个拿**：`stock_fhps_detail_em` 一次一只，
5,546 只要跑几个小时；`stock_fhps_em(date=报告期)` 一次给全市场（实测
20251231 一次返回 3,653 行 / 约 5 秒），22 个报告期两分钟跑完。

**东财的口径是"每 10 股"**，库里存的是"每股"，所以两个字段都要 ÷10：
    现金分红-现金分红比例  1.46 元/10股  →  cash_per_share  0.146
    送转股份-送转总比例    4    股/10股  →  bonus_ratio     0.4
用沃华医药对过：1.46 / 10 / 6.86 元 = 0.0213，与接口给的股息率 0.021283 一致。

**配股（rights_ratio）这个接口没有**，留 0。A 股配股本来就少，等真的遇到
一只配股票算错了再补一个专门的源，比现在猜一个比例安全。

用法：
    python scripts\\ingest\\c8_dividend.py                     # 2020Q4 起全量 + 回算
    python scripts\\ingest\\c8_dividend.py --start 20231231    # 只补近两年
    python scripts\\ingest\\c8_dividend.py --no-recompute      # 只入库，不回算因子
    python scripts\\ingest\\c8_dividend.py --dry-run
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

DEFAULT_START = '20201231'      # 日线从 2021 起，2020 年报的除权日落在 2021 上半年
COL_EX = '除权除息日'
COL_BONUS = '送转股份-送转总比例'
COL_CASH = '现金分红-现金分红比例'
COL_TOTAL = '总股本'


def periods(start: str, end: dt.date) -> list[str]:
    """报告期序列：每年 0331 / 0630 / 0930 / 1231，到 end 所在季度为止。"""
    out = []
    y0 = int(start[:4])
    for y in range(y0, end.year + 1):
        for md in ('0331', '0630', '0930', '1231'):
            p = f'{y}{md}'
            if p >= start and dt.date(y, int(md[:2]), int(md[2:])) <= end + dt.timedelta(days=1):
                out.append(p)
    return out


def code_map() -> dict[str, str]:
    """
    6 位代码 → ts_code。东财只给 6 位码，库里是带后缀的。

    同一个 6 位码理论上只对一个市场，但 ref.security 里有退市重用的历史行，
    所以按 is_active 优先，避免把新股的分红挂到同号的退市票上。
    """
    rows = engine.query(
        'SELECT symbol, ts_code FROM ref.security '
        'WHERE symbol IS NOT NULL ORDER BY is_active, ts_code')
    return {r['symbol']: r['ts_code'] for r in rows}


def _num(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f != f else f      # NaN


def pull(period: str, cmap: dict[str, str]) -> tuple[list[tuple], list[tuple]]:
    """
    一次请求同时产出两批行：分红送配 + 总股本。

    **只调一次接口**：一个报告期约 5 秒，分开取等于把 22 个报告期变成 44 次
    请求、两分钟变四分钟，还多一倍被限流的机会。

    总股本存 `ref.share_capital.total_share`，`float_share` 留 NULL ——
    东财这张表没有流通股本，用总股本顶替会把换手率算小（大盘股差好几倍）。
    换手率要的股本由 c9_share.py 专门补。
    """
    import akshare as ak
    df = ak.stock_fhps_em(date=period)
    if df is None or df.empty:
        return [], []
    day = dt.date(int(period[:4]), int(period[4:6]), int(period[6:]))
    div: list[tuple] = []
    share: list[tuple] = []
    skipped = 0
    for r in df.to_dict('records'):
        ts = cmap.get(str(r.get('代码') or '').zfill(6))
        if ts is None:
            skipped += 1
            continue

        total = r.get(COL_TOTAL)
        try:
            n = int(float(total))
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            share.append((ts, day, n, None))

        ex = r.get(COL_EX)
        if ex in (None, '', '-'):
            continue
        try:
            ex_date = dt.date.fromisoformat(str(ex)[:10])
        except ValueError:
            continue
        cash = _num(r.get(COL_CASH)) / 10.0
        bonus = _num(r.get(COL_BONUS)) / 10.0
        if cash <= 0 and bonus <= 0:
            continue                 # 只有预案没有实施的行，除权日没有意义
        div.append((ts, ex_date, round(cash, 6), round(bonus, 6), 0, None))
    if skipped:
        print('    代码映射不上 %s 行（多为北交所与已退市）' % skipped)
    return div, share


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default=DEFAULT_START, help='起始报告期 YYYYMMDD')
    ap.add_argument('--no-recompute', action='store_true',
                    help='只入库分红，不回算 adj_factor')
    ap.add_argument('--no-share', action='store_true',
                    help='不顺带写 ref.share_capital 的总股本')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    cmap = code_map()
    ps = periods(args.start, dt.date.today())
    print('=== 采分红送配（东财 stock_fhps_em）===')
    print('  报告期 %s 个：%s .. %s' % (len(ps), ps[0], ps[-1]))
    print('  ref.security 代码映射 %s 条' % len(cmap))

    batch_id = 0
    if not args.dry_run:
        batch_id = ops_repo.begin_batch('akshare_em', 'ref.dividend')
        print('  批次 %s' % batch_id)

    total_div = total_share = 0
    try:
        for p in ps:
            t0 = time.time()
            div, share = pull(p, cmap)
            if not args.dry_run and div:
                sec_repo.upsert_dividends(div)
            if not args.dry_run and share and not args.no_share:
                sec_repo.upsert_share_capital(share)
            total_div += len(div)
            total_share += 0 if args.no_share else len(share)
            print('  %s  除权记录 %5s 条  总股本 %5s 条  %.1fs'
                  % (p, len(div), len(share), time.time() - t0), flush=True)
        if not args.dry_run:
            ops_repo.end_batch(batch_id, row_count=total_div)
    except Exception as exc:
        if batch_id:
            ops_repo.end_batch(batch_id, row_count=total_div, status='failed',
                               message=str(exc)[:500])
        raise


    print('=== 入库结果 ===')
    if args.dry_run:
        print('  dry-run，未写库。除权记录 %s 条 / 总股本 %s 条'
              % (total_div, total_share))
        return 0
    print('  ref.dividend      %s 行（%s 只票）'
          % (engine.scalar('SELECT count(*) FROM ref.dividend'),
             engine.scalar('SELECT count(DISTINCT ts_code) FROM ref.dividend')))
    print('  ref.share_capital %s 行'
          % engine.scalar('SELECT count(*) FROM ref.share_capital'))

    if args.no_recompute:
        print('\nDONE（未回算 adj_factor）')
        return 0

    print('=== 回算 adj_factor ===')
    t0 = time.time()
    n = quote_repo.recompute_adj_factor(role='admin')
    print('  更新 %s 行  %.1fs' % (f'{n:,}', time.time() - t0))
    row = engine.query(
        """
        SELECT count(*) AS total,
               count(adj_factor) AS has_adj,
               count(*) FILTER (WHERE adj_factor <> 1) AS not_one
          FROM md.stock_daily
        """)[0]
    print('  md.stock_daily 共 %s 行，有因子 %s 行，因子≠1 的 %s 行'
          % (f"{row['total']:,}", f"{row['has_adj']:,}", f"{row['not_one']:,}"))
    print('\nDONE')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
