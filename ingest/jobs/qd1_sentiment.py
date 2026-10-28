# -*- coding: utf-8 -*-
"""
qd 轨 · 情绪周期入库（qd.sentiment_cycle）。

算法契约：`数据加工工作指南/09-quantdash逻辑规格.md` §4.1。
**前置**：目标区间（含前后各一个交易日）必须已由 qd2_ladder.py 落库，
否则那几天整行不写 —— 宁可缺行，不可假行。

用法：
    python -m ingest.jobs.qd1_sentiment                # 最新一天
    python -m ingest.jobs.qd1_sentiment --recent 30
    python -m ingest.jobs.qd1_sentiment --from 2021-12-01
    python -m ingest.jobs.qd1_sentiment --stages       # 只看三套阶段的一致率
    python -m ingest.jobs.qd1_sentiment --verify       # 常识体检 + 恒等式

退出码：0 成功 / 1 无输入 / 2 --verify 不通过。
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
from app.compute import qd_sentiment            # noqa: E402
from app.db import engine                       # noqa: E402
from app.compute import target_guard as tguard  # noqa: E402

SANE = {
    # 量级网，不是"差几家"的网。收窄到历史分位会在涨停潮当天全线报警，那种报警没人看。
    'limit_up_count':        (0, 800),
    'limit_down_count':      (0, 800),
    'board_height':          (0, 20),
    'success_rate_pct':      (0, 100),
    'broken_rate_touched_pct': (0, 100),
    'broken_rate_sealed_pct': (0, 100),
    'broken_repair_rate_lag1_pct': (0, 100),
    'premium_score':         (0, 90),
    'confidence':            (0, 100),
}


def stages() -> int:
    """
    三套阶段的一致率与不一致样本 —— 09 §8 要求至少找出一个三列不同日的交易日。
    """
    rows = engine.query(
        """
        SELECT count(*) AS n,
               count(*) FILTER (WHERE stage_ui = stage_mcp
                                  AND stage_mcp = stage_feishu) AS all_same,
               count(*) FILTER (WHERE stage_ui <> stage_mcp)     AS ui_ne_mcp,
               count(*) FILTER (WHERE stage_ui <> stage_feishu)  AS ui_ne_fs
          FROM qd.sentiment_cycle
        """)
    r = rows[0]
    if not r['n']:
        print('  qd.sentiment_cycle 还是空的')
        return 1
    print('  共 %s 天：三套全同 %s 天（%.1f%%），ui≠mcp %s 天，ui≠feishu %s 天'
          % (r['n'], r['all_same'], 100.0 * r['all_same'] / r['n'],
             r['ui_ne_mcp'], r['ui_ne_fs']))
    diff = engine.query(
        """
        SELECT trade_date, stage_ui, stage_mcp, stage_feishu, confidence
          FROM qd.sentiment_cycle
         WHERE NOT (stage_ui = stage_mcp AND stage_mcp = stage_feishu)
         ORDER BY trade_date DESC LIMIT 8
        """)
    for d in diff:
        print('    %s  ui=%s  mcp=%s  feishu=%s  conf=%s'
              % (d['trade_date'], d['stage_ui'], d['stage_mcp'],
                 d['stage_feishu'], d['confidence']))
    return 0


def verify(d: dt.date) -> int:
    r = engine.query_one('SELECT * FROM qd.sentiment_cycle WHERE trade_date = %s',
                         (d,))
    if not r:
        print('  %s 库里没有这一行' % d)
        return 1
    bad = []
    for k, (lo, hi) in SANE.items():
        v = r[k]
        if v is not None and not (lo <= float(v) <= hi):
            bad.append('%s = %s 落在常识区间 [%s, %s] 之外' % (k, v, lo, hi))
    # 恒等式：定义决定的，破了就是 SQL 写错
    for label, ok in (
        ('首板 + 连板 = 涨停家数',
         r['first_board_count'] + (r['relay_count'] or 0) == r['limit_up_count']),
        ('高板 <= 涨停家数', (r['high_board_count'] or 0) <= r['limit_up_count']),
        ('触板 = 涨停 + 触板未封',
         r['touched_count'] == r['limit_up_count'] + r['broken_touched_count']),
        ('面板炸板率 = 100 - 晋级率',
         abs(float(r['broken_rate_sealed_pct']) - (100 - float(r['success_rate_pct'])))
         < 0.06 if r['broken_rate_sealed_pct'] is not None
         and r['success_rate_pct'] is not None else True),
    ):
        if not ok:
            bad.append('恒等式不成立：%s' % label)
    print('  %s 涨停 %s / 跌停 %s / 触板 %s（未封 %s，%s%%）最高 %s 板 龙头 %s 板 '
          '晋级 %s%% 修复(lag1) %s%% 情绪分 %s 量能 %s 风险 %s'
          % (d, r['limit_up_count'], r['limit_down_count'], r['touched_count'],
             r['broken_touched_count'], r['broken_rate_touched_pct'],
             r['board_height'], r['leader_board_count'], r['success_rate_pct'],
             r['broken_repair_rate_lag1_pct'], r['sentiment_value'],
             r['volume_state'], r['risk_level']))
    print('  阶段 ui=%s mcp=%s feishu=%s conf=%s；deviation %s 条'
          % (r['stage_ui'], r['stage_mcp'], r['stage_feishu'], r['confidence'],
             len(r['deviation'] or [])))
    for m in bad:
        print('  [FAIL] %s' % m)
    return 0 if not bad else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--from', dest='start')
    ap.add_argument('--recent', type=int, default=1)
    ap.add_argument('--stages', action='store_true')
    ap.add_argument('--verify', action='store_true')
    args = ap.parse_args()

    if args.stages:
        rc = stages()
        engine.close_all()
        return rc

    end = dt.date.fromisoformat(args.date) if args.date else None
    # 目标日闸门：`--date D` 要的是 D。输入不齐就出声，不退回算别的日子。
    _no = tguard.missing_target_reason(args.date, 'qd.board_ladder', 'trade_date',
                                      where='', min_rows=1)
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
        print('没有可算的交易日')
        return 1

    if args.verify:
        rc = verify(end or dates[-1])
        engine.close_all()
        return rc

    print('=== qd 轨 · 情绪周期：%s 个交易日 %s .. %s ==='
          % (len(dates), dates[0], dates[-1]))
    t0 = time.time()
    out = qd_sentiment.refresh(dates)
    print('  写 %s 行，跳过（天梯未跑）%s 天，%.1fs'
          % (out['rows'], len(out['skipped_no_ladder']), time.time() - t0))
    if out['skipped_no_ladder']:
        print('  跳过的前 5 天：%s —— 先跑 qd2_ladder.py 覆盖这些日期'
              % out['skipped_no_ladder'][:5])
    print('=== 体检最后一天 ===')
    rc = verify(dates[-1]) if dates else 1
    engine.close_all()
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
