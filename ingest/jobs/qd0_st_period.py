# -*- coding: utf-8 -*-
"""
qd 轨 · ST 生效区间回填（reference_data.security_st_period）。

08-跨源口径契约 §二.3 的根治表。**本机实测没有 ST 历史真源**（逐条证据见
`app/db/sql/12_qd_schema.sql` 的表注释）：is_st 是 2026-08-27 单日快照、通达信
ST板块成员 in_date 全是 2026-08-03 且 out_date 全空、stock_profile.raw 的 TPFlag
是"停牌"不是 ST、akshare 1.18.88 只有当前 ST 列表无名称变更接口。

所以本脚本**只写可证的两类行**，不拿猜测填满历史：
  non_st_proved_by_move  主板且当前 ST 的票，最后一次 |涨幅|>5.5% 的那一天
                         —— 那一天可证不是 5% 档（5% 档不可能涨 5.5%）
  snapshot_current       当前 is_st 快照，只对其后日期有效

用法：
    python -m ingest.jobs.qd0_st_period                  # 默认 2021-12-01 起
    python -m ingest.jobs.qd0_st_period --since 2020-01-01
    python -m ingest.jobs.qd0_st_period --report
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
# 两个流都要显式设 UTF-8：作业是被 runner.py 用子进程跑的，
# 不设的话异常回溯里的中文会变成乱码，页面上看不出失败原因
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from app.compute import qd_common as Q   # noqa: E402
from app.db import engine                # noqa: E402


def report() -> None:
    rows = engine.query(
        """
        SELECT method, count(*) AS n, count(*) FILTER (WHERE is_st) AS st_rows,
               min(valid_from) AS first_from, max(valid_from) AS last_from
          FROM reference_data.security_st_period GROUP BY 1 ORDER BY 1
        """)
    if not rows:
        print('  表是空的 —— 先不带 --report 跑一次')
        return
    for r in rows:
        print('  %-22s %6s 行（is_st=true %6s），valid_from %s .. %s'
              % (r['method'], r['n'], r['st_rows'], r['first_from'], r['last_from']))
    print('  覆盖股票数：%s（当前主板 is_st 共 %s 只）'
          % (engine.scalar('SELECT count(DISTINCT security_code) FROM '
                           'reference_data.security_st_period'),
             engine.scalar("SELECT count(*) FROM reference_data.security "
                           "WHERE asset_type='stock' AND board='主板' AND is_st")))
    print('  ⚠️ 本表**不是**完整历史：缺的那块要等外部名称变更源接入（见表注释）')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2021-12-01')
    ap.add_argument('--report', action='store_true')
    args = ap.parse_args()

    if args.report:
        report()
        engine.close_all()
        return 0

    since = dt.date.fromisoformat(args.since)
    print('=== ST 生效区间回填（自 %s 起找反证锚点）===' % since)
    out = Q.refresh_st_period(since)
    print('  %s' % out)
    report()
    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
