# -*- coding: utf-8 -*-
"""
算板块的**趋势启动点**，落 `analytics.ignition`（scope='sector'）。

四个档位：`coil` 蓄势 / `ignite` 点火 / `follow` 延续 / `fade` 退潮，口径与全部
参数说明在 `app/compute/ignition.py` 的模块 docstring 里（含被回测推翻的那条
直觉：`squeeze` 方向是 `active` 而不是 `quiet`）。

**必须排在 d3_sector_trend 之后**：启动点读的是 `analytics.sector_heat_daily`
（`source='sector'`）+ `market_data.sector_daily`，热度没写今天，这一步只会把昨天的再算一遍。
它**不依赖个股评分**，所以和 d2_scores 没有先后关系。

暖机：分位基线要 120 个交易日，脚本自动往前多读 `ignition.WARMUP=130` 天，
但只写 `--recent` 圈定的那几天 —— 每天跑一次的成本是"读 131 天、写 1 天"。

用法：
    python -m ingest.jobs.d4_ignition                  # 最新交易日
    python -m ingest.jobs.d4_ignition --recent 120     # 往前 120 个交易日
    python -m ingest.jobs.d4_ignition --date 2026-08-28
    python -m ingest.jobs.d4_ignition --full           # 全历史重算（约 3 分钟）
    python -m ingest.jobs.d4_ignition --verify         # 回测自证：ignite 要赢对照组

退出码：0 成功 / 1 没有热度或拿不到交易日 / 2 --verify 不通过。
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app.compute import ignition  # noqa: E402
from app.db import engine  # noqa: E402
from app.compute import target_guard as tguard  # noqa: E402

#: `--verify` 的样本外起点。样本内网格是 2021-08 ~ 2025-06 调的，拿它之后的
#: 数据当验收集：阈值是在样本内选的，用样本内自证等于自己给自己打分。
OOS_START = dt.date(2025, 7, 1)

#: 验收下限。样本外实测 ignite n=196 / 胜率 57.5 / T+5 超额 +0.455（对照组
#: +0.074）。这里放宽到"胜率 > 52 且比对照组高 0.2"，留出重算热度、板块宇宙
#: 微调带来的抖动空间 —— 卡太死会变成每次重算都要来改这个数。
MIN_WIN5 = 52.0
MIN_EDGE5 = 0.2


def heat_days(end: dt.date, n: int) -> list[dt.date]:
    """最近 n 个**有板块热度**的交易日（升序）。"""
    return sorted(r['trade_date'] for r in engine.query(
        """
        SELECT DISTINCT trade_date FROM analytics.sector_heat_daily
         WHERE source = 'sector' AND trade_date <= %s
         ORDER BY trade_date DESC LIMIT %s
        """, (end, max(1, n))))


def verify() -> int:
    """
    回测自证：ignite 档的未来超额必须明显好于 `none` 对照组。

    这一步**不写库**，也不看已经落库的行 —— 它按当前参数重算一遍要素和分级，
    所以改了阈值/方向之后先跑它，比先落库再看页面快得多，也不会把库弄脏。
    """
    df = ignition.load_sector()
    if df.empty:
        print('  没有取到 analytics.sector_heat_daily(source=sector)')
        return 1
    p = ignition.params()
    print('  参数：squeeze_dir=%s  ig=(%s, %s, %s)  coil=(%s, %s)'
          % (p['squeeze_dir'], p['ig_ignition'], p['ig_volume'],
             p['ig_breadth'], p['coil_squeeze'], p['coil_pos']))
    t = ignition.terms(df, p)
    st = ignition.stages(t, p)
    oos = df.index.get_level_values('trade_date') >= OOS_START
    rows = {r['stage']: r for r in ignition.backtest(df[oos], t[oos], st[oos])}
    print('  样本外（%s 起）各档未来收益（超额＝减去当日全板块等权基准）：'
          % OOS_START)
    print('    %-7s %7s %8s %9s %9s' % ('档位', '样本数', '胜率5', '超额5', '超额10'))
    for stage in ignition.STAGES:
        r = rows.get(stage)
        if not r:
            continue
        print('    %-7s %7s %8s %9s %9s'
              % (stage, f"{r['n']:,}", r.get('win5'), r.get('exc5'),
                 r.get('exc10')))
    a, b = rows.get('ignite'), rows.get('none')
    if not a or not b:
        print('  [FAIL] 样本外没有 ignite 或没有对照组')
        return 2
    bad = []
    if (a.get('win5') or 0) <= MIN_WIN5:
        bad.append('胜率 %s <= %s' % (a.get('win5'), MIN_WIN5))
    if (a.get('exc5') or 0) - (b.get('exc5') or 0) <= MIN_EDGE5:
        bad.append('T+5 超额只比对照组高 %.3f'
                   % ((a.get('exc5') or 0) - (b.get('exc5') or 0)))
    if bad:
        print('  [FAIL] ' + '；'.join(bad))
        return 2
    print('  [OK] ignite 胜率 %s、T+5 超额 %s，对照组 %s'
          % (a.get('win5'), a.get('exc5'), b.get('exc5')))
    return 0


def coverage(day: dt.date) -> None:
    """当天各档多少个板块 + 点火清单前几名，跑完当场看得见。"""
    rows = engine.query(
        """
        SELECT stage, count(*) n FROM analytics.ignition
         WHERE scope = 'sector' AND trade_date = %s
         GROUP BY 1 ORDER BY 2 DESC
        """, (day,))
    if not rows:
        print('  %s 没有落任何行' % day)
        return
    print('  %s 分档：' % day
          + '  '.join('%s=%s' % (r['stage'], f"{r['n']:,}") for r in rows))
    top = engine.query(
        """
        SELECT i.object_key, coalesce(s.name, i.object_key) AS name, i.score,
               i.ignition, i.volume, i.breadth
          FROM analytics.ignition i
          LEFT JOIN reference_data.sector_info s ON s.sector_code = i.object_key
         WHERE i.scope = 'sector' AND i.trade_date = %s AND i.stage = 'ignite'
         ORDER BY i.score DESC LIMIT 5
        """, (day,))
    if not top:
        print('  当天没有 ignite —— 阈值是按"宁可漏不可滥"定的，'
              '实测平均 0.7 个/交易日，空着是常态')
        return
    print('  点火清单（分数前 5）：')
    for r in top:
        print('    %-12s %-14s 分 %-6s 点火 %-6s 量能 %-6s 广度 %s'
              % (r['object_key'], r['name'], r['score'], r['ignition'],
                 r['volume'], r['breadth']))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--recent', type=int, default=1)
    ap.add_argument('--full', action='store_true',
                    help='全历史重算（忽略 --recent）')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--verify', action='store_true')
    args = ap.parse_args()

    end = (dt.date.fromisoformat(args.date) if args.date
           else engine.scalar("SELECT max(trade_date) FROM "
                              "analytics.sector_heat_daily WHERE source = 'sector'"))
    if not end:
        print('analytics.sector_heat_daily 里没有 src=sector 的行，'
              '先跑 python -m ingest.jobs.d3_sector_trend')
        return 1

    if args.verify:
        print('=== 启动点回测自证 ===')
        rc = verify()
        engine.close_all()
        return rc

    if args.full:
        # 全历史：从"第 131 个交易日"起写，前 130 天只当暖机 —— 分位基线不满
        # 的那段算出来是残缺的，写进去等于给页面一片"看着有数"的假档位。
        start = engine.scalar(
            "SELECT trade_date FROM (SELECT DISTINCT trade_date "
            "  FROM analytics.sector_heat_daily WHERE source = 'sector' "
            " ORDER BY trade_date OFFSET %s LIMIT 1) z", (ignition.WARMUP,))
        if start is None:
            print('热度历史不足 %s 个交易日，没法算分位基线' % ignition.WARMUP)
            return 1
    else:
        # 目标日闸门：`--date D` 要的是 D。输入不齐就出声，不退回算别的日子。
        _no = tguard.missing_target_reason(args.date, 'analytics.sector_heat_daily', 'trade_date',
                                          where="and source = 'sector'", min_rows=1)
        if _no:
            print(_no)
            engine.close_all()
            return 1

        days = heat_days(end, args.recent)
        if not days:
            print('%s 及之前没有板块热度' % end)
            return 1
        start = days[0]

    print('=== 算板块启动点：%s .. %s ===' % (start, end))
    t0 = time.time()
    res = ignition.run_sector(start, end, dry_run=args.dry_run, echo=print)
    if res.get('error'):
        print('  ' + res['error'])
        engine.close_all()
        return 1
    print('  合计 %.1fs' % (time.time() - t0))
    if not args.dry_run:
        coverage(end)
    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
