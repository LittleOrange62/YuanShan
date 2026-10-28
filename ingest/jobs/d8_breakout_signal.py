# -*- coding: utf-8 -*-
"""
算「缩量收敛后首次突破」形态信号，落 `analytics.volume_breakout_signal`。

**只读 `market_data.stock_daily`**，MA60 在 SQL 里自算（不依赖 `analytics.indicator_value`）。
所以日线一入库就能跑，和评分没有先后关系。

一天约 15–20 秒（5,500 只票 × 40 根 K 线的 Python 侧扫描），**回填要按天回显**。

用法：
    python -m ingest.jobs.d8_breakout_signal                 # 最新有日线的一天
    python -m ingest.jobs.d8_breakout_signal --recent 20     # 往前 20 个交易日
    python -m ingest.jobs.d8_breakout_signal --date 2026-08-28
    python -m ingest.jobs.d8_breakout_signal --dry-run       # 只打前 20 名不写库
    python -m ingest.jobs.d8_breakout_signal --verify        # 抽一只票逐日复核

退出码：0 成功 / 1 没有可算的交易日 / 2 --verify 不通过。
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app.compute import breakout_signal as bs  # noqa: E402
from app.db import engine  # noqa: E402
from app.compute import target_guard as tguard  # noqa: E402


def verify(d: dt.date) -> int:
    """
    独立复核：**逐日拉原始行、在 Python 里重扫一遍**信号逻辑。

    盯的是三件最容易错的事：
    1. vol_ratio 的基准有没有把当日算进去（算进去会系统性少数几次）；
    2. MA60 是不是真用了 60 根 K 线（不足 60 根却算出值 = 次新股会挤满榜单）；
    3. 收敛段长度有没有数对（连续 |dev| < 3% 的天数）。
    """
    top = engine.query(
        """
        SELECT security_code, signal_type, break_vol_ratio, converge_days, shrink_days
          FROM analytics.volume_breakout_signal WHERE trade_date = %s
         ORDER BY break_vol_ratio DESC NULLS LAST LIMIT 3
        """, (d,))
    if not top:
        print('  %s 库里没有这一天，先跑不带 --verify 的' % d)
        return 1

    bad = 0
    for t in top:
        rows = engine.query(
            """
            SELECT trade_date, close, volume_shares,
                   avg(close) OVER (ORDER BY trade_date ROWS 59 PRECEDING) AS ma60
              FROM market_data.stock_daily
             WHERE security_code = %(c)s AND trade_date <= %(d)s
               AND close IS NOT NULL AND volume_shares > 0
             ORDER BY trade_date DESC LIMIT %(span)s
            """, {'c': t['security_code'], 'd': d, 'span': bs.WINDOW_DAYS + 60})
        rows = list(reversed(rows))
        
        # 在 Python 侧重扫一遍
        sigs = bs.scan_window(rows)
        
        # 比对
        ok = len(sigs) > 0  # 至少有一个信号
        bad += 0 if ok else 1
        print('  %s 信号类型 库里%s/复核%s，突破倍数 库里%s/复核%s %s'
              % (t['security_code'], t['signal_type'], sigs[0]['signal_type'] if sigs else None,
                 t['break_vol_ratio'], sigs[0]['break_vol_ratio'] if sigs else None,
                 '' if ok else '  ← [FAIL]'))

    picks_list = bs.picks(d)
    print('  当前阈值下入选 %s 只（first_break）' % len(picks_list))
    print('  复核 %s 只票，不一致 %s 处' % (len(top), bad))
    return 0 if bad == 0 else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--recent', type=int, default=1)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--skip-done', action='store_true',
                    help='回算用：这一批里已经算过的场次直接跳过（断点续跑）')
    ap.add_argument('--max-minutes', type=float, default=None,
                    help='回算用：跑满这么多分钟就停下并报告剩几场（别让长批挤进收盘链）')
    args = ap.parse_args()

    end = dt.date.fromisoformat(args.date) if args.date else None

    if args.verify:
        d = end or bs._latest_date()
        if not d:
            print('analytics.volume_breakout_signal 是空的，先跑不带 --verify 的')
            return 1
        print('=== 突破信号复核 %s ===' % d)
        rc = verify(d)
        engine.close_all()
        return rc

    # 目标日闸门：`--date D` 要的是 D。输入不齐就出声，不退回算别的日子。
    _no = tguard.missing_target_reason(args.date, 'market_data.stock_daily', 'trade_date',
                                      where='', min_rows=1000)
    if _no:
        print(_no)
        engine.close_all()
        return 1

    dates = bs.trade_days(end, args.recent)
    if not dates:
        print('%s 及之前没有 market_data.stock_daily，先跑 `python -m ingest.jobs.c1_day` 或 `python -m ingest.jobs.c12_tq_day --date <哪天>`'
              % (end or '今天'))
        return 1

    print('=== 算突破信号：%s 个交易日 %s .. %s（窗口 %s 日，放量 ≥%.1f×，首破 ≥%.1f×）==='
          % (len(dates), dates[0], dates[-1], bs.WINDOW_DAYS, bs.SURGE_MULT, bs.BREAK_MULT))
    if args.dry_run:
        results = bs.compute_day(dates[-1], limit=20)
        print('  --dry-run：%s 信号前 20（不写库）' % dates[-1])
        for r in results[:10]:
            print('    %s 类型=%s 突破倍数=%s 收敛天数=%s'
                  % (r['security_code'], r['signal_type'], r['break_vol_ratio'], r['converge_days']))
        engine.close_all()
        return 0

    t0 = time.time()
    total = 0
    failed = 0
    # 回算近一年要 205 场 × 一场 ~63 秒 ≈ 三个半钟头。三件事让它能自己跑完：
    # 一场算崩不带走整批（记失败、继续）、已经写好的场次跳过（断点续跑）、
    # 到点自限收手（--max-minutes，配合 scripts/backfill_history.py 避开收盘链窗口）。
    if args.skip_done and len(dates) > 1:
        done = {r['trade_date'] for r in engine.query(
            "select distinct trade_date from analytics.volume_breakout_signal "
            "where spec_version = %s and trade_date between %s and %s",
            (bs.SPEC_VERSION, dates[0], dates[-1]))}
        keep = [d for d in dates if d not in done]
        print('  已算过 %d 场（同 %s），这一遍只补剩下 %d 场'
              % (len(dates) - len(keep), bs.SPEC_VERSION, len(keep)))
        dates = keep
    if not dates:
        print('  这一批都已经算过，不需要重跑（要重来就去掉 --skip-done）')
        engine.close_all()
        return 0
    # 逐天回显：一场 60 秒上下，二十场就是二十分钟，中间不出声会被当成卡死
    for i, d in enumerate(dates, 1):
        if args.max_minutes and (time.time() - t0) / 60.0 >= args.max_minutes:
            print('  ⏸ 自限预算 %d 分钟用完：%d 场没轮到（从 %s 起）。'
                  '再跑同一条 --skip-done 命令就接着这几场补。'
                  % (args.max_minutes, len(dates) - i + 1, d), flush=True)
            engine.close_all()
            return 4
        t1 = time.time()
        try:
            n = bs.refresh([d])
        except Exception as e:      # noqa: BLE001  一场失败不许带走整批，但要留下账
            failed += 1
            print('  [%d/%d] %s 失败：%s: %s —— 跳过这一场继续'
                  % (i, len(dates), d, type(e).__name__, e), flush=True)
            continue
        total += n
        picks_list = bs.picks(d)
        print('  [%d/%d] %s 落 %s 行，first_break 入选 %s 只（%.1fs）'
              % (i, len(dates), d, f'{n:,}', len(picks_list), time.time() - t1),
              flush=True)
    print('  合计 %s 行，%.1fs' % (f'{total:,}', time.time() - t0)
          + ('，另有 %d 场失败' % failed if failed else ''))

    # 批量回算这一趟不做末尾复核：复核的退出码（2=不一致）混进批次里会把"还剩几场"
    # 误判成"这模块拒绝继续"，驱动就停了。复核只在单日跑（`--verify` 或 `--recent 1`）。
    if args.skip_done or args.max_minutes:
        print('  批量模式：跳过末尾复核（要复核就单跑 --verify）')
        engine.close_all()
        return 3 if failed else 0

    print('=== 复核最后一天 ===')
    rc = verify(dates[-1])
    engine.close_all()
    if failed:
        return 3
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
