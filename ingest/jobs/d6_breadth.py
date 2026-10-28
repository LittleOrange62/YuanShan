# -*- coding: utf-8 -*-
"""
算全市场情绪读数，落 `analytics.market_breadth`（涨跌停 / 触板炸板 / 连板梯队 / 晋级率）。

**只读 `market_data.stock_daily`**，不依赖评分、不依赖板块，所以可以和 `d2_scores.py`
并列排在日线入库之后，不必等在评分后面。这也是它能一次回填到 2021 的原因。

它是「明日验证表」（`app/compute/verification.py`）的事实来源：昨天写下
"预期涨停家数上升"，今天靠这张表打勾。所以口径不允许在展示层再算一遍。

用法：
    python -m ingest.jobs.d6_breadth                  # 最新有日线的一天
    python -m ingest.jobs.d6_breadth --recent 60      # 往前 60 个交易日
    python -m ingest.jobs.d6_breadth --date 2026-08-28
    python -m ingest.jobs.d6_breadth --dry-run        # 只打读数不写库
    python -m ingest.jobs.d6_breadth --verify         # 常识体检（涨跌停量级等）

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
from app.compute import target_guard as tguard  # noqa: E402

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
    # 阿姆斯：区间**不住这里**，由 `app/compute/breadth.py::ARMS_SANE` 一份给
    # （那边 `--status` 也读它）。依据 2026-10-21 现查 1,262 场：min 0.0672 / p50 1.135 / max 2039.48。
    'arms_ratio':       breadth.ARMS_SANE,
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
        SELECT count(*) FROM market_data.stock_daily
         WHERE trade_date = %(d)s AND pct_change IS NOT NULL
           AND ( ((starts_with(security_code, '68') OR starts_with(security_code, '30'))
                    AND pct_change >= 19.5)
              OR ((starts_with(security_code, '4') OR starts_with(security_code, '8')
                   OR starts_with(security_code, '92')) AND pct_change >= 29.5)
              OR ((starts_with(security_code, '60') OR starts_with(security_code, '00'))
                   AND pct_change >= 9.8 AND pct_change < 12) )
        """, {'d': d})
    got = r['limit_up_count']
    # 20% 的相对容差：一字板停牌、次新不受限、B 股（200/900 段）在这条路上算不出来。
    # 只要两条路量级一致就算过 —— 这一项防的是"limit_up 整列错位"。
    # ⚠️ 2026-10-21 实测：这一支**必须按板块分档**。上一版把 `pct_change >= 9.8 AND < 12`
    # 加在所有代码上，于是 ① 创业板 30xxxx／科创板 68xxxx 涨 9.8~12%（那是 20% 档，
    # 根本没到涨停）被算成涨停，② 北交所新代码段 920xxx（30% 档）落进同一支 ——
    # 那天两条路给出 55 / 74（差 34.5%，超容差），体检假红而**涨停价那条路才是权威**。
    # 按板块分档后 20 场逐日 56/55、50/50、63/62、36/36、36/37、69/70、47/47、72/72、
    # 46/44、58/56、55/54、35/35、52/52、51/51、65/65、104/102、83/79、49/49、91/91、32/33，
    # 全部落在容差内。
    gap_ok = abs(got - plain) <= max(5, 0.2 * max(got, plain, 1))
    if not gap_ok:
        bad.append('涨停家数两条路差太多：涨停价判 %s 家 / 涨幅阈值判 %s 家'
                   % (got, plain))

    flat = engine.scalar(
        """
        SELECT count(*) FROM market_data.stock_daily
         WHERE trade_date = %(d)s AND pct_change = 0
        """, {'d': d})
    if r['up_count'] + r['down_count'] + flat != r['stocks']:
        bad.append('涨 %s + 跌 %s + 平 %s ≠ 参与统计 %s'
                   % (r['up_count'], r['down_count'], flat, r['stocks']))

    # ---- 阿姆氏那一族（2026-10-21 加，用户 10-20 夜「风险提示」） --------------------
    # ① `up_amount + down_amount` 不得大于**当日全市场成交额合计**。
    #    这条抓的是"FILTER 判据写成另一个母体"——比如漏了 `pct_change` 的方向条件、
    #    把全天的额加进了分子，那种行的两个合计相加必超总成交额。
    total_amt = engine.scalar(
        """SELECT coalesce(sum(amount_yuan), 0) FROM market_data.stock_daily
          WHERE trade_date = %(d)s""", {'d': d})
    up_amt, down_amt = r.get('up_amount'), r.get('down_amount')
    if up_amt is None or down_amt is None:
        # 一边缺 = 那天该方向一票没有（实测 2021-08-02 那场 `pct_change` 全空）。
        # 缺是允许的，但它必须与家数那一侧自洽：家数 > 0 而合计 NULL 就是 SQL 漏了。
        if (up_amt is None and r['up_count'] > 0) or (down_amt is None and r['down_count'] > 0):
            bad.append('成交额合计为 NULL 但对应家数 > 0：涨 %s 家 / 跌 %s 家，'
                       '涨额 %s / 跌额 %s —— FILTER 判据与 count 判据不是同一批票'
                       % (r['up_count'], r['down_count'], up_amt, down_amt))
    else:
        if float(up_amt) + float(down_amt) > float(total_amt or 0):
            bad.append('涨额 %s + 跌额 %s = %s 元 > 当日全市场成交额合计 %s 元'
                       % (up_amt, down_amt, float(up_amt) + float(down_amt), total_amt))
        # ② 比值与两个合计自洽（库里存的必须是同一行自己算出来的，不能一边新一边旧）
        arms = r.get('arms_ratio')
        if float(down_amt) > 0:
            if arms is None:
                bad.append('跌额 %s > 0 而 arms_ratio 为 NULL —— 判得出来却不写'
                           % down_amt)
            else:
                want = round(float(up_amt) / float(down_amt), 6)
                if abs(float(arms) - want) > max(1e-6, 1e-6 * abs(want)):
                    bad.append('arms_ratio = %s 与 up_amount/down_amount = %s 不自洽'
                               % (arms, want))
        elif arms is not None:
            bad.append('跌额 = 0 时 arms_ratio 必须是 NULL（不是 0、不是 Infinity），现在是 %s'
                       % arms)
        # ③ 家数与金额同向：涨 0 家却有涨额 = 两边母体不是同一批票
        if (r['up_count'] == 0) != (float(up_amt) == 0):
            bad.append('up_count = %s 与 up_amount = %s 方向不一致'
                       % (r['up_count'], up_amt))
        if (r['down_count'] == 0) != (float(down_amt) == 0):
            bad.append('down_count = %s 与 down_amount = %s 方向不一致'
                       % (r['down_count'], down_amt))

    print('  %s：%s 只票，涨停 %s / 跌停 %s，触板 %s → 炸板 %s（%s%%），'
          '最高连板 %s（梯队 %s/%s/%s），晋级 %s%%，中位 %s%%，'
          '涨 %s 家 / 跌 %s 家，涨额 %s 亿 / 跌额 %s 亿，阿姆斯 %s'
          % (d, f"{r['stocks']:,}", r['limit_up_count'], r['limit_down_count'],
             r['touched_count'], r['broken_count'], r['broken_rate'],
             r['highest_board'], r['board2_count'], r['board3_count'],
             r['board4plus_count'], r['promotion_rate'], r['median_pct'],
             r['up_count'], r['down_count'],
             '—' if up_amt is None else round(float(up_amt) / 1e8, 1),
             '—' if down_amt is None else round(float(down_amt) / 1e8, 1),
             '—' if r.get('arms_ratio') is None else round(float(r['arms_ratio']), 4)))
    print('  独立复算涨停（涨幅阈值口径）：%s 家，差 %s'
          % (plain, got - plain))
    for m in bad:
        print('  [FAIL] %s' % m)
    # 项数：区间 SANE（含 arms_ratio）+ 恒等式 + 独立复算 + 涨跌平自洽
    #        + 阿姆氏那 4 条（合计不超总额 / 比值自洽 / 涨侧同向 / 跌侧同向）
    print('  体检 %s 项，不通过 %s 处' % (len(SANE) + len(ident) + 2 + 4, len(bad)))
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
    # 目标日闸门：`--date D` 要的是 D。输入不齐就出声，不退回算别的日子。
    _no = tguard.missing_target_reason(args.date, 'market_data.stock_daily', 'trade_date',
                                      where='', min_rows=1000)
    if _no:
        print(_no)
        engine.close_all()
        return 1

    dates = breadth.trade_days(end, args.recent)
    if not dates:
        print('%s 及之前没有 market_data.stock_daily，先跑 `python -m ingest.jobs.c1_day` 或 `python -m ingest.jobs.c12_tq_day --date <哪天>`'
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
