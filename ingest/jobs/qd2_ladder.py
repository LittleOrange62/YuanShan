# -*- coding: utf-8 -*-
"""
qd 轨 · 连板天梯族入库（qd.board_ladder / _member / _leader / qd.board_height /
qd.stock_strength）。

算法契约：`数据加工工作指南/09-quantdash逻辑规格.md` §4.2。
**这一族是 qd1_sentiment 的前置**：情绪轨直接读这里落的连板分布，不另推一遍。

用法：
    python -m ingest.jobs.qd2_ladder                 # 最新有日线的一天
    python -m ingest.jobs.qd2_ladder --recent 30     # 往前 30 个交易日
    python -m ingest.jobs.qd2_ladder --date 2026-09-22
    python -m ingest.jobs.qd2_ladder --from 2021-12-01   # 全量回填
    python -m ingest.jobs.qd2_ladder --dry-run       # 只算不写

退出码：0 成功 / 1 没有可算的交易日。
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
# 两个流都要显式设 UTF-8：作业是被 runner.py 用子进程跑的，
# 不设的话异常回溯里的中文会变成乱码，页面上看不出失败原因
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from app.compute import qd_common as Q          # noqa: E402
from app.compute import qd_ladder               # noqa: E402
from app.db import engine                       # noqa: E402
from app.compute import target_guard as tguard  # noqa: E402


def count_qd() -> int:
    """qd schema 全部表的行数总和。dry-run 前后一比就知道有没有真的写进去。"""
    tables = [r['table_name'] for r in engine.query(
        """SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'qd' AND table_type = 'BASE TABLE' ORDER BY 1""")]
    return sum(engine.scalar('SELECT count(*) FROM qd.%s' % t) for t in tables)


def report(dates: list[dt.date]) -> None:
    """把关键读数打出来 —— 落库不自检等于把量级错误直接送到页面上。"""
    rows = engine.query(
        """
        SELECT b.trade_date,
               sum(b.stock_count)                        AS zt,
               max(b.board_tier) FILTER (WHERE b.is_highest_tier) AS height,
               max(l.leader_board_count)                 AS leader,
               max(l.leader_count)                       AS leader_n,
               max(l.leader_tie_break)                   AS tie,
               count(*) FILTER (WHERE b.board_tier = 1 AND b.stock_count > 0) AS has_first
          FROM qd.board_ladder b
          LEFT JOIN qd.board_ladder_leader l USING (trade_date, spec_version)
         WHERE b.trade_date = ANY (%s)
         GROUP BY 1 ORDER BY 1
        """, (dates,))
    for r in rows:
        print('    %s 涨停 %s 只，最高 %s 板，龙头 %s 板 ×%s 只（tie=%s）'
              % (r['trade_date'], r['zt'], r['height'], r['leader'],
                 r['leader_n'], r['tie']), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--from', dest='start')
    ap.add_argument('--recent', type=int, default=1)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    end = dt.date.fromisoformat(args.date) if args.date else None
    # 目标日闸门：`--date D` 要的是 D。输入不齐就出声，不退回算别的日子。
    _no = tguard.missing_target_reason(args.date, 'market_data.stock_daily', 'trade_date',
                                      where='', min_rows=1000)
    if _no:
        print(_no)
        engine.close_all()
        return 1

    dates = Q.trade_days(end, 10000)
    if args.start:
        s = dt.date.fromisoformat(args.start)
        dates = [d for d in dates if d >= s]
    else:
        dates = dates[-max(1, args.recent):]
    if not dates:
        print('没有可算的交易日：先跑 `python -m ingest.jobs.c1_day`（或 `python -m ingest.jobs.c12_tq_day --date <哪天>`）把日线补齐')
        return 1

    n0 = count_qd()
    print('=== qd 轨 · 连板天梯：%s 个交易日 %s .. %s（spec=%s%s）==='
          % (len(dates), dates[0], dates[-1], Q.spec_version(),
             '，dry-run 不写库' if args.dry_run else ''))
    t0 = time.time()
    out = qd_ladder.refresh(dates, dry_run=args.dry_run)
    print('  落库 %s（%.1fs）' % (out, time.time() - t0))
    n1 = count_qd()
    print('  库里 qd.* 总行数：写前 %s → 写后 %s%s'
          % (n0, n1, '（dry-run 必须相等）' if args.dry_run else ''), flush=True)
    if not args.dry_run:
        report(dates if len(dates) <= 12 else dates[-12:])
    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
