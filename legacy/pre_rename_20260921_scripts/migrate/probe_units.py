# -*- coding: utf-8 -*-
"""
B3 前置：判定旧日线的单位口径。

这是整次迁移最容易出错的地方。判定方法不靠文档、不靠字段名，靠算术：
    均价 = amount / vol
若均价落在 [low, high] 区间内，两者单位自洽；
若均价 ≈ 区间的 100 倍，说明 vol 是「手」而不是「股」（差 100）；
若均价 ≈ 区间的 1/1000，说明 amount 是「千元」而不是「元」。

分别对个股、指数、板块各抽样，因为实测发现它们的口径不一致。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402

YEARS = (2021, 2022, 2023, 2024, 2025, 2026)


def verdict(ratio: float) -> str:
    """ratio = 均价 / 区间中值。"""
    for factor, text in ((1, '自洽（amount=元, vol=股）'),
                         (100, 'vol 是「手」，需 ×100 转股'),
                         (0.01, 'vol 已是股但 amount 偏小 100 倍'),
                         (1000, 'amount 是「千元」，需 ×1000'),
                         (0.001, 'amount 偏大 1000 倍')):
        if factor * 0.5 <= ratio <= factor * 2:
            return text
    return '未知偏差 %.4g 倍' % ratio


def probe_table(c, table, code_col, label, where=''):
    sql = f"""
      SELECT {code_col} AS code, trade_date, open, high, low, close, vol, amount
        FROM {table}
       WHERE vol > 0 AND amount > 0 AND low > 0 {where}
       LIMIT 400
    """
    try:
        rows = c.execute(sql).fetchall()
    except sqlite3.Error as exc:
        print('  %-14s 读取失败：%s' % (label, exc))
        return
    if not rows:
        print('  %-14s 无样本' % label)
        return

    ratios = []
    for _code, _d, _o, high, low, _cl, vol, amount in rows:
        mid = (high + low) / 2
        if mid <= 0:
            continue
        ratios.append((amount / vol) / mid)
    ratios.sort()
    med = ratios[len(ratios) // 2]
    print('  %-14s 样本 %4d 条  中位比值 %12.6g  →  %s'
          % (label, len(ratios), med, verdict(med)))
    print('                 分位 p10=%.6g p90=%.6g'
          % (ratios[len(ratios) // 10], ratios[len(ratios) * 9 // 10]))
    c0 = rows[0]
    print('                 例：%s %s O/H/L/C=%s/%s/%s/%s vol=%s amount=%s 均价=%.4f'
          % (c0[0], c0[1], c0[2], c0[3], c0[4], c0[5], c0[6], c0[7], c0[7] / c0[6]))


def main():
    for y in YEARS:
        p = config.LEGACY_DATA / f'tq_daily_{y}.db'
        if not p.exists():
            continue
        print('\n=== tq_daily_%d.db ===' % y)
        c = sqlite3.connect(f'file:{p.as_posix()}?mode=ro', uri=True, timeout=5)
        c.execute('PRAGMA busy_timeout=5000')
        # 真个股：6 位数字 + .SZ/.SH/.BJ，排掉指数常用前缀
        probe_table(c, 'stock_daily', 'ts_code', '个股',
                    "AND (ts_code LIKE '6%.SH' OR ts_code LIKE '00%.SZ' "
                    "OR ts_code LIKE '30%.SZ')")
        probe_table(c, 'stock_daily', 'ts_code', '混入的指数',
                    "AND (ts_code LIKE '000001.SH' OR ts_code LIKE '399%.SZ')")
        probe_table(c, 'index_daily', 'ts_code', 'index_daily')
        probe_table(c, 'sector_daily', 'sector_code', 'sector_daily')
        c.close()


if __name__ == '__main__':
    main()
