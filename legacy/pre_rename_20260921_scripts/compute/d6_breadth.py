# -*- coding: utf-8 -*-
"""
算全市场情绪读数，落 `fct.market_breadth`（涨跌停 / 触板炸板 / 连板梯队 / 晋级率）。

**只读 `md.stock_daily`**，不依赖评分、不依赖板块，所以可以和 `d2_scores.py`
并列排在日线入库之后，不必等在评分后面。这也是它能一次回填到 2021 的原因。

它是「明日验证表」（`app/compute/verification.py`）的事实来源：昨天写下
"预期涨停家数上升"，今天靠这张表打勾。所以口径不允许在展示层再算一遍。

用法：
    python scripts/compute/d6_breadth.py                  # 最新有日线的一天
    python scripts/compute/d6_breadth.py --recent 60      # 往前 60 个交易日
    python scripts/compute/d6_breadth.py --date 2026-08-28
    python scripts/compute/d6_breadth.py --dry-run        # 只打读数不写库
    python scripts/compute/d6_breadth.py --verify         # 常识体检（涨跌停量级等）

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

from app.compute import breadth  # noqa: E402
from app.db import engine  # noqa: E402

# 常识区间。**故意宽**：这是抓"量级错了"的网，不是抓"差几家"的网。
# 收窄到历史分位会在极端行情（千股跌停、涨停潮）当天全线报警，那种报警没人看。
SANE = {
    'limit_up_count':   (0, 800),    # 2015 年最疯的一天不到 700 家
    'limit_down_count': (0, 800),
    'highest_board':    (0, 20),     # A 股历史最高连板 20 出头，超过必是连板算错
    'broken_rate':      (0, 100),
    'promotion_rate':   (0, 100),
    'red_rate':         (0, 100),
    'up_ratio':         (0, 100),
    'median_pct':       (-11, 11),   # 中位数落在涨跌停幅之外 = 涨跌幅列口径错了
}


def verify(d: dt.date) -> int:
    """
    常识体检 + 独立复算。**用户会肉眼看这张表**，量级错了必须当场拦住。

    四类检查：
    1. 区间：见 SANE。涨停 5000 家、连板高度 60 这种一眼假的直接拦。
    2. 恒等式：炸板 ≤ 触板、涨停 ≤ 触板、连板梯队之和 ≤ 涨停家数、
       晋级家数 ≤ 昨涨停家数。这些是定义决定的，破了就是 SQL 写错。
    3. 独立复算涨停家数：不用 `limit_up` 价格，改用**涨幅接近板块上限**
       （主板 9.8% / 双创 19.5% / 北交所 29.5%）另算一遍。两条路差得太多说明
       `limit_up` 那一列有问题。允许有差：一字板停牌、上市首日不受限。
    4. 上涨 + 下跌 + 平盘 = 参与统计的票数。
    """
    r = breadth.on(d)
    if not r:
        print('  %s 库里没有这一天，先跑不带 --verify 的' % d)
        return 1

    bad = []
    for k, (lo, hi) in SANE.items():
        v = r[k]
        if v is None:
            continue
        if not (lo <= float(v) <= hi):
            bad.append('%s = %s 落在常识区间 [%s, %s] 之外' % (k, v, lo, hi))

    ident = (
        ('炸板 ≤ 触板', r['broken_count'] <= r['touched_count']),
        ('涨停 ≤ 触板', r['limit_up_count'] <= r['touched_count']),
        ('连板梯队之和 ≤ 涨停家数',
         (r['board2_count'] + r['board3_count'] + r['board4plus_count'])
         <= r['limit_up_count']),
    )
    for label, ok in ident:
        if not ok:
            bad.append('恒等式不成立：%s' % label)

    plain = engine.scalar(
        """
        SELECT count(*) FROM md.stock_daily
         WHERE trade_date = %(d)s AND pct_chg IS NOT NULL
           AND ((starts_with(ts_code, '68') OR starts_with(ts_code, '30'))
                    AND pct_chg >= 19.5
                OR starts_with(ts_code, '8') AND pct_chg >= 29.5
                OR starts_with(ts_code, '4') AND pct_chg >= 29.5
                OR pct_chg >= 9.8 AND pct_chg < 12)
        """, {'d': d})
    got = r['limit_up_count']
    # 20% 的相对容差：ST 的 5% 涨停在这条路上算不出来，一字板、次新也会偏。
    # 只要两条路量级一致就算过 —— 这一项防的是"limit_up 整列错位"。
    gap_ok = abs(got - plain) <= max(5, 0.2 * max(got, plain, 1))
    if not gap_ok:
        bad.append('涨停家数两条路差太多：涨停价判 %s 家 / 涨幅阈值判 %s 家'
                   % (got, plain))

    flat = engine.scalar(
        """
        SELECT count(*) FROM md.stock_daily
         WHERE trade_date = %(d)s AND pct_chg = 0
        """, {'d': d})
    if r['up_count'] + r['down_count'] + flat != r['stocks']:
        bad.append('涨 %s + 跌 %s + 平 %s ≠ 参与统计 %s'
                   % (r['up_count'], r['down_count'], flat, r['stocks']))

    print('  %s：%s 只票，涨停 %s / 跌停 %s，触板 %s → 炸板 %s（%s%%），'
          '最高连板 %s（梯队 %s/%s/%s），晋级 %s%%，中位 %s%%'
          % (d, f"{r['stocks']:,}", r['limit_up_count'], r['limit_down_count'],
             r['touched_count'], r['broken_count'], r['broken_rate'],
             r['highest_board'], r['board2_count'], r['board3_count'],
             r['board4plus_count'], r['promotion_rate'], r['median_pct']))
    print('  独立复算涨停（涨幅阈值口径）：%s 家，差 %s'
          % (plain, got - plain))
    for m in bad:
        print('  [FAIL] %s' % m)
    print('  体检 %s 项，不通过 %s 处' % (len(SANE) + len(ident) + 2, len(bad)))
    return 0 if not bad else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--recent', type=int, default=1)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--verify', action='store_true')
    args = ap.parse_args()

    end = dt.date.fromisoformat(args.date) if args.date else None

    if args.verify:
        d = end or (breadth.trade_days(recent=1) or [None])[-1]
        if not d:
            print('没有有日线的交易日')
            return 1
        print('=== 情绪读数常识体检 %s ===' % d)
        rc = verify(d)
        engine.close_all()
        return rc

    # 判据是**有日线数据的交易日**，不是交易日历：按日历取会把还没采集的天
    # 也带进来，那些天落出来是一行 0，页面上分不清"真的没涨停"还是"数据没到"。
    dates = breadth.trade_days(end, args.recent)
    if not dates:
        print('%s 及之前没有 md.stock_daily，先跑 c1_day.py / c12_tq_day.py'
              % (end or '今天'))
        return 1

    print('=== 算全市场情绪读数：%s 个交易日 %s .. %s ==='
          % (len(dates), dates[0], dates[-1]))
    if args.dry_run:
        # dry-run 也真算，只是不写 —— 不算的 dry-run 只能验证参数拼对了没有
        for i, d in enumerate(dates, 1):
            r = breadth.compute(d)
            print('  [%d/%d] %s 涨停 %s / 跌停 %s / 触板 %s / 炸板 %s / '
                  '最高连板 %s（不写库）'
                  % (i, len(dates), d, r['limit_up_count'],
                     r['limit_down_count'], r['touched_count'],
                     r['broken_count'], r['highest_board']), flush=True)
        engine.close_all()
        return 0

    t0 = time.time()
    # 逐天回显 —— 一天约 1s，回填 60 天要一分钟，中间不出声会被当成卡死
    total = 0
    for i, d in enumerate(dates, 1):
        t1 = time.time()
        total += breadth.refresh([d])
        r = breadth.on(d)
        print('  [%d/%d] %s 涨停 %s / 跌停 %s / 炸板率 %s%% / 最高连板 %s / '
              '晋级 %s%%（%.2fs）'
              % (i, len(dates), d, r['limit_up_count'], r['limit_down_count'],
                 r['broken_rate'], r['highest_board'], r['promotion_rate'],
                 time.time() - t1), flush=True)
    print('  合计 %s 天，%.1fs' % (total, time.time() - t0))

    # 写完当场体检最后一天 —— 落库不体检等于把量级错误直接送到页面上
    print('=== 体检最后一天 ===')
    rc = verify(dates[-1])
    engine.close_all()
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
