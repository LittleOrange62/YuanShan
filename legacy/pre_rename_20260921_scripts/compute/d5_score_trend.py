# -*- coding: utf-8 -*-
"""
算个股操作分的窗口趋势，落 `fct.score_trend`（`/scores` 的「四象限趋势分」）。

**排在 `d2_scores.py` 之后**：它读 `fct.score` 的 oper_score / stock_quality /
env_resonance 三个 cap_key。评分没写今天，这一步只会把昨天的再算一遍。

用法：
    python scripts/compute/d5_score_trend.py                  # 最新有评分的一天
    python scripts/compute/d5_score_trend.py --recent 60       # 往前 60 个交易日
    python scripts/compute/d5_score_trend.py --date 2026-08-28
    python scripts/compute/d5_score_trend.py --win 10 --recent 5
    python scripts/compute/d5_score_trend.py --verify          # 和 numpy 对照

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

from app.compute import score_trend  # noqa: E402
from app.db import engine  # noqa: E402


def verify(d: dt.date, win: int) -> int:
    """
    独立复核：用 numpy 的 polyfit 重算几只票的斜率，和库里的比。

    **斜率最容易把 x 轴取反**（rn 递增方向搞错的话正负号整体翻转，而页面上
    "向上为正"是这一列全部的意义），所以专门盯符号和数量级。
    """
    import numpy as np

    rows = engine.query(
        """
        SELECT ts_code, slope FROM fct.score_trend
         WHERE trade_date = %(d)s AND win = %(w)s AND n_points = %(w)s
         ORDER BY abs(slope) DESC LIMIT 8
        """, {'d': d, 'w': win})
    if not rows:
        print('  没有点数足够的票可对照')
        return 1
    bad = 0
    for r in rows:
        hist = engine.query(
            """
            SELECT value FROM fct.score
             WHERE ts_code = %(c)s AND cap_key = 'oper_score'
               AND trade_date <= %(d)s AND value IS NOT NULL
             ORDER BY trade_date DESC LIMIT %(w)s
            """, {'c': r['ts_code'], 'd': d, 'w': win})
        ys = [float(x['value']) for x in reversed(hist)]
        if len(ys) < win:
            continue
        mine = float(np.polyfit(range(win), ys, 1)[0])
        got = float(r['slope'])
        ok = abs(mine - got) < 0.01
        bad += 0 if ok else 1
        if not ok:
            print('  [FAIL] %s 库里 %.4f / polyfit %.4f'
                  % (r['ts_code'], got, mine))
    print('  对照 %s 只票，不一致 %s 处' % (len(rows), bad))
    return 0 if bad == 0 else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--recent', type=int, default=1)
    ap.add_argument('--win', type=int, default=score_trend.DEFAULT_WIN)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--verify', action='store_true')
    args = ap.parse_args()

    end = dt.date.fromisoformat(args.date) if args.date else None
    win = int(args.win)

    if args.verify:
        d = end or (score_trend.scored_days(recent=1) or [None])[-1]
        if not d:
            print('没有有评分的交易日')
            return 1
        print('=== 与 numpy.polyfit 对照 %s（win=%s）===' % (d, win))
        rc = verify(d, win)
        engine.close_all()
        return rc

    # 判据是**有评分的交易日**，不是交易日历：按日历取会把评分还没算的日子
    # 也带进来，那些日子落出来的趋势是空的，页面上看不出是"没算"还是
    # "真的没趋势"。
    dates = score_trend.scored_days(end, args.recent)
    if not dates:
        print('%s 及之前没有 fct.score，先跑 d2_scores.py' % (end or '今天'))
        return 1

    print('=== 算个股趋势：%s 个交易日 %s .. %s，窗口 %s ==='
          % (len(dates), dates[0], dates[-1], win))
    if args.dry_run:
        print('  --dry-run：不写库')
        engine.close_all()
        return 0

    t0 = time.time()
    # 逐天回显 —— 回填 60 天要一分多钟，中间不出声会被当成卡死
    total = 0
    for i, d in enumerate(dates, 1):
        t1 = time.time()
        n = score_trend.refresh([d], win)
        total += n
        print('  [%d/%d] %s 写入 %s 行（%.2fs）'
              % (i, len(dates), d, f'{n:,}', time.time() - t1), flush=True)
    print('  合计 %s 行，%.1fs' % (f'{total:,}', time.time() - t0))

    # 覆盖度当场打出来 —— 窗口比评分历史还长的时候，那一列**看着有数**、
    # 其实只用了几个点，不打出来没人会去查 n_points
    cov = score_trend.coverage(dates[-1], win)
    if cov:
        flag = '' if cov['stocks'] == cov['full_window'] else '  ← 有点数不足的'
        print('  %s：%s 只票，窗口满的 %s，平均点数 %s，斜率 %s ~ %s%s'
              % (dates[-1], cov['stocks'], cov['full_window'],
                 cov['avg_points'], cov['min_slope'], cov['max_slope'], flag))
    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
