# -*- coding: utf-8 -*-
"""
qd 轨 · 板块周期入库（qd.sector_rotation / qd.sector_persistence / qd.sector_follow）。

算法契约：`数据加工工作指南/09-quantdash逻辑规格.md` §4.3。
**这一族与 analytics.ignition 是两套东西**：quantdash 的"板块周期"没有阶段枚举，
实质是轮动矩阵 + 持续性统计；本轨不替换 ignition，两轨并排展示。

用法：
    python -m ingest.jobs.qd3_sector                 # 最新有板块日线的一天
    python -m ingest.jobs.qd3_sector --recent 30
    python -m ingest.jobs.qd3_sector --from 2021-12-01
    python -m ingest.jobs.qd3_sector --top 10        # 看当前领涨前 10

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
from app.compute import qd_sector               # noqa: E402
from app.compute import qd_sector_follow        # noqa: E402
from app.db import engine                       # noqa: E402
from app.compute import target_guard as tguard  # noqa: E402


def show_top(n: int) -> None:
    rows = engine.query(
        """
        SELECT trade_date, day_rank, sector_name, sector_type, pct_change,
               up_count, down_count, member_count, window_size
          FROM qd.sector_rotation
         WHERE trade_date = (SELECT max(trade_date) FROM qd.sector_rotation)
           AND day_rank <= %s
         ORDER BY day_rank
        """, (n,))
    for r in rows:
        print('    %s #%s %-14s(%s) %+6.2f%%  涨%d/跌%d/%d成  样本%d'
              % (r['trade_date'], r['day_rank'], r['sector_name'], r['sector_type'],
                 r['pct_change'], r['up_count'] or -1, r['down_count'] or -1,
                 r['member_count'] or -1, r['window_size']))
    p = engine.query_one(
        """
        SELECT * FROM qd.sector_persistence
         WHERE trade_date = (SELECT max(trade_date) FROM qd.sector_persistence)
        """)
    if p:
        print('    持续性：leader=%s 连庄 %s 天，前三 %s 次，最强连庄 %s，'
              '涨幅变化 %s，窗口 %s 天(%s 起)'
              % (p['current_leader_name'], p['streak_days'],
                 p['top_three_appearances'], p['strongest_repeat_name'],
                 p['strength_delta_pct'], p['window_days'], p['window_start']))


def show_follow(d: dt.date) -> None:
    """把刚落库的这一场前三甲与榜首相似板块读回来打一行 —— 判"写进去了没有"要看库里的行，
    不看作业说的 rc=0（同 193 号那类残留与 08 号那条「已回算 N 场必须回查库」的纪律）。"""
    rows = engine.query(
        """
        SELECT lag, day_rank, sector_name, universe_size, top_n, k_similar,
               jsonb_array_length(similar_block -> 'items') AS n_items,
               similar_block ->> 'matched' AS matched,
               similar_block ->> 'skipped_incomplete' AS skipped,
               similar_block -> 'items' -> 0 ->> 'sector_name' AS top_similar,
               similar_block -> 'items' -> 0 ->> 'gap_mean_rank' AS top_gap
          FROM qd.sector_follow
         WHERE trade_date = %s AND spec_version = %s AND lag = 2
         ORDER BY day_rank
        """, (d, qd_sector_follow.spec_version()))
    if not rows:
        print('    相似榜：这一场没有落库行（当天没有干净的板块行，或本步没算成）')
        return
    for r in rows:
        print('    第 %s 名 %-12s 母体%s：%s 条相似，榜首 %s（平均名次差 %s），缺行未参与 %s'
              % (r['day_rank'], r['sector_name'], r['universe_size'], r['n_items'],
                 r['top_similar'] or '—', r['top_gap'] or '—', r['skipped']))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--from', dest='start')
    ap.add_argument('--recent', type=int, default=1)
    ap.add_argument('--top', type=int)
    args = ap.parse_args()

    if args.top:
        show_top(args.top)
        engine.close_all()
        return 0

    end = dt.date.fromisoformat(args.date) if args.date else None
    # 判据是 sector_daily 而不是 stock_daily：板块日线缺日时轮动矩阵会整天空掉，
    # 用股票日线的日期会算出一行"leader 为空"的假行
    # 目标日闸门：`--date D` 要的是 D。输入不齐就出声，不退回算别的日子。
    _no = tguard.missing_target_reason(args.date, 'market_data.sector_daily', 'trade_date',
                                      where='', min_rows=1)
    if _no:
        print(_no)
        engine.close_all()
        return 1

    dates = engine.query(
        """
        SELECT DISTINCT trade_date FROM market_data.sector_daily
         WHERE (%(end)s::date IS NULL OR trade_date <= %(end)s::date)
         ORDER BY trade_date DESC LIMIT %(lim)s
        """, {'end': end, 'lim': 10000})
    dates = sorted(r['trade_date'] for r in dates)
    dates = ([d for d in dates if d >= dt.date.fromisoformat(args.start)]
             if args.start else dates[-max(1, args.recent):])
    if not dates:
        print('没有可算的交易日：先跑 c3_sector.py 把板块日线补齐')
        return 1

    print('=== qd 轨 · 板块周期：%s 个交易日 %s .. %s（top_n=%s）==='
          % (len(dates), dates[0], dates[-1], Q.setting('qd_sector_top_n', int)))
    t0 = time.time()
    # 计算模块自己不 print（被测试或常驻线程调到时，写字节会撞 pytest 的全局捕获文件），
    # 分片进度由这里给的回显打出来 —— 全史回算一小时以上，没有进度就没法判它卡没卡住
    out = qd_sector.refresh(dates, progress=print)
    print('  %s（%.1fs）' % (out, time.time() - t0))
    show_top(8)
    show_follow(dates[-1])
    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
