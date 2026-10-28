# -*- coding: utf-8 -*-
"""
算板块热度的多窗口趋势，落 `fct.sector_heat_daily` + `fct.sector_trend`。

**2026-08-30 起不再依赖个股评分**：热度由 `app/compute/sector_heat_hist.py`
直接从 `md.sector_daily` 算板块级（`src='sector'`），所以这一步不用等
`d2_scores.py`，也不再被评分的 23 天历史卡住。

用法：
    python scripts/compute/d3_sector_trend.py                  # 最新交易日
    python scripts/compute/d3_sector_trend.py --recent 120     # 往前 120 个交易日
    python scripts/compute/d3_sector_trend.py --date 2026-08-28
    python scripts/compute/d3_sector_trend.py --verify         # 和独立算法对照
    python scripts/compute/d3_sector_trend.py --prune-only     # 只清口径不认的旧行

每次正常运行都会先 `sector_heat_hist.prune()`：热度是 upsert 写的，口径收紧之后
旧行不会自己消失，不清就会出现"08-28 有 652 个板块、08-31 只有 604 个"这种
同一列跨日含义不同的情况。`--no-prune` 可以跳过。

退出码：0 成功 / 1 拿不到交易日或没有板块日线 / 2 --verify 不通过。
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app.compute import sector_heat_hist, sector_trend  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import security as sec_repo  # noqa: E402


def prune(*, dry_run: bool = False) -> None:
    """清掉当前口径不认的板块的旧行，逐表打出行数。"""
    st = sector_heat_hist.prune(dry_run=dry_run)
    head = '  预演（不删）' if dry_run else '  清理'
    total = sum(st.values())
    if not total:
        print(f'{head}：三张派生表都没有口径外的行')
        return
    for t, n in st.items():
        print(f'{head} {t}：{n:,} 行')


def verify(d: dt.date) -> int:
    """
    独立复核：用 numpy 的 polyfit 重算几个板块的斜率，和库里的比。

    只对照，不写库。**斜率这种东西很容易把 x 轴取反**（rn 递增方向搞错的话
    正负号整体翻转，而页面上"向上为正"是这一列全部的意义），
    所以这一步专门盯符号和数量级。
    """
    import numpy as np

    wins = sector_trend.windows()
    rows = engine.query(
        """
        SELECT sector_code FROM fct.sector_trend
         WHERE trade_date = %s AND win = %s AND n_points >= %s
         ORDER BY abs(slope) DESC LIMIT 5
        """, (d, max(wins), max(wins)))
    if not rows:
        print('  没有点数足够的板块可对照')
        return 1
    bad = 0
    for r in rows:
        code = r['sector_code']
        hist = engine.query(
            """
            SELECT trade_date, heat FROM fct.sector_heat_daily
             WHERE sector_code = %s AND trade_date <= %s AND src = 'sector'
             ORDER BY trade_date DESC LIMIT %s

            """, (code, d, max(wins)))
        ys = [float(x['heat']) for x in reversed(hist)]
        for w in wins:
            if len(ys) < w or w == 1:
                continue
            seg = ys[-w:]
            mine = float(np.polyfit(range(w), seg, 1)[0])
            got = engine.scalar(
                'SELECT slope FROM fct.sector_trend WHERE sector_code = %s '
                'AND trade_date = %s AND win = %s', (code, d, w))
            got = float(got) if got is not None else None
            ok = got is not None and abs(mine - got) < 0.01
            bad += 0 if ok else 1
            if not ok:
                print('  [FAIL] %s win=%s 库里 %s / polyfit %.4f'
                      % (code, w, got, mine))
    print('  对照 %s 个板块 × %s 个窗口，不一致 %s 处'
          % (len(rows), len(wins) - 1, bad))
    return 0 if bad == 0 else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--recent', type=int, default=1)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--no-prune', action='store_true',
                    help='跳过"清理口径外旧行"')
    ap.add_argument('--prune-only', action='store_true',
                    help='只清理，不重算')
    args = ap.parse_args()

    if args.prune_only:
        print('=== 只清理口径外的旧行 ===')
        prune(dry_run=args.dry_run)
        engine.close_all()
        return 0


    end = (dt.date.fromisoformat(args.date) if args.date
           else sec_repo.latest_trade_date())
    if not end:
        print('拿不到交易日')
        return 1

    if args.verify:
        print('=== 与 numpy.polyfit 对照 %s ===' % end)
        rc = verify(end)
        engine.close_all()
        return rc

    wins = sector_trend.windows()
    # 只算**真有板块日线**的日子。热度不再依赖评分（2026-08-30 换成板块级直算），
    # 所以判据从 fct.score 换成 md.sector_daily —— 按交易日历取会把没采到板块
    # 日线的日子也带进来，那些日子写出来的热度是空的，趋势跟着空一片。
    scored = [r['trade_date'] for r in engine.query(
        """
        SELECT DISTINCT trade_date FROM md.sector_daily
         WHERE trade_date <= %s
         ORDER BY trade_date DESC LIMIT %s
        """, (end, max(1, args.recent)))]
    if not scored:
        print('%s 及之前没有 md.sector_daily，先跑板块日线采集' % end)
        return 1

    dates = sorted(scored)

    print('=== 算板块趋势：%s 个交易日 %s .. %s，窗口 %s ==='
          % (len(dates), dates[0], dates[-1], wins))
    if args.dry_run:
        print('  --dry-run：不写库')
        prune(dry_run=True)
        engine.close_all()
        return 0

    # **先清后算**：清理是按"当前口径认不认这个板块"删的，先删掉再重算，
    # 这一趟写出来的行和留在库里的历史行就一定是同一个口径。
    if not args.no_prune:
        prune()

    t0 = time.time()
    st = sector_trend.run(dates, wins=wins)
    print('  fct.sector_heat_daily 写入 %s 行' % f"{st['heat_rows']:,}")
    for w in wins:
        print('  win=%-3s 写入 %s 行' % (w, f"{st['trend_rows'].get(w, 0):,}"))
    print('  合计 %.1fs' % (time.time() - t0))

    # 覆盖度当场打出来 —— 窗口比历史还长的时候，那一列**看着有数**、
    # 其实只用了几个点，不打出来没人会去查 n_points
    print('  %s 各窗口覆盖：' % dates[-1])
    for c in sector_trend.coverage(dates[-1]):
        flag = '' if c['full_window'] == c['sectors'] else '  ← 点数不足'
        print('    win=%-3s 板块 %s，点数足够 %s，平均点数 %s%s'
              % (c['win'], c['sectors'], c['full_window'],
                 c['avg_points'], flag))
    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
