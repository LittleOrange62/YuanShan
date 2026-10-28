# -*- coding: utf-8 -*-
"""
D1 · 算指标。

用法：
    python scripts\\compute\\d1_indicators.py --dry-run
    python scripts\\compute\\d1_indicators.py                    # 最新交易日
    python scripts\\compute\\d1_indicators.py --date 2026-08-25
    python scripts\\compute\\d1_indicators.py --recent 20         # 最近 20 个交易日
    python scripts\\compute\\d1_indicators.py --verify            # 与 pandas 手算对照
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app.compute import indicators  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import security as sec_repo  # noqa: E402


def verify(trade_date: dt.date) -> int:
    """
    抽三个指标与 pandas 直算比对。求值器算错了不会报错，只会给出错的数，
    所以必须有一处独立算法做对照。
    """
    import pandas as pd

    from app.compute import expr as ex
    panel = indicators.load_panel(trade_date, 300)
    if panel.empty:
        print('  没有数据')
        return 1
    target = pd.Timestamp(trade_date).date()
    ok = True

    close = panel['close']
    by = close.groupby(level='ts_code', sort=False)

    cases = {
        'ma20': (by.transform(lambda s: s.rolling(20, min_periods=20).mean()),
                 'MA(close, 20)'),
        'bias20': None,
        'std20': None,
        'atr14': None,
    }
    # bias20 = (close - MA20) / MA20 * 100
    ma20 = by.transform(lambda s: s.rolling(20, min_periods=20).mean())
    cases['bias20'] = ((close - ma20) / ma20 * 100, 'BIAS20')
    pct = panel['pct_chg']
    cases['std20'] = (pct.groupby(level='ts_code', sort=False).transform(
        lambda s: s.rolling(20, min_periods=20).std(ddof=1)), 'STD(pct_chg,20)')
    high, low = panel['high'], panel['low']
    prev = by.shift(1)
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()],
                   axis=1).max(axis=1)
    cases['atr14'] = (tr.groupby(level='ts_code', sort=False).transform(
        lambda s: s.rolling(14, min_periods=14).mean()), 'ATR14')

    specs = {s['ind_key']: s for s in indicators.load_specs()}
    for key, (manual, label) in cases.items():
        mine = ex.evaluate(specs[key]['expr'], panel)
        a = mine[mine.index.get_level_values('trade_date') == target].dropna()
        b = manual[manual.index.get_level_values('trade_date') == target].dropna()
        common = a.index.intersection(b.index)
        if len(common) == 0:
            print('  [FAIL] %-8s 没有可比对的行' % key)
            ok = False
            continue
        diff = (a[common].astype(float) - b[common].astype(float)).abs().max()
        flag = 'OK  ' if diff < 1e-6 else 'FAIL'
        if diff >= 1e-6:
            ok = False
        print('  [%s] %-8s %-22s 比对 %s 只，最大绝对差 %.10f'
              % (flag, key, label, f'{len(common):,}', diff))
    return 0 if ok else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--recent', type=int, default=1)
    ap.add_argument('--ind', action='append', default=None)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--verify', action='store_true')
    args = ap.parse_args()

    end = (dt.date.fromisoformat(args.date) if args.date
           else sec_repo.latest_trade_date())
    if not end:
        print('拿不到交易日')
        return 1

    if args.verify:
        print('=== 与 pandas 手算对照 %s ===' % end)
        rc = verify(end)
        engine.close_all()
        return rc

    dates = sec_repo.recent_trade_dates(args.recent, end)
    print('=== 算指标：%s 个交易日 %s .. %s ==='
          % (len(dates), dates[0], dates[-1]))
    total = 0
    for d in dates:
        st = indicators.compute_date(d, ind_keys=args.ind, dry_run=args.dry_run)
        if 'error' in st:
            print('  %s  %s' % (d, st['error']))
            continue
        empty = [k for k, v in st['values'].items() if v == 0]
        print('  %s  面板 %s 行 / %s 只  回溯 %s 天  取数 %.1fs  产出 %s 行  合计 %.1fs%s'
              % (d, f"{st['panel_rows']:,}", st['codes'], st['lookback'],
                 st['load_seconds'], f"{st['rows']:,}", st['seconds'],
                 '  全空指标: ' + ','.join(empty) if empty else ''))
        if st.get('failed'):
            for k, msg in st['failed'].items():
                print('    [FAIL] %s: %s' % (k, msg))
        total += st.get('written', 0)

    if not args.dry_run:
        print('\n=== 验收 ===')
        print('  fct.indicator_value   %s' % engine.scalar(
            'SELECT count(*) FROM fct.indicator_value'))
        print('  指标数                %s' % engine.scalar(
            'SELECT count(DISTINCT ind_key) FROM fct.indicator_value'))
        for r in engine.query(
                'SELECT ind_key, count(*) AS n, round(min(value),4) AS mn, '
                'round(max(value),4) AS mx FROM fct.indicator_value '
                'WHERE trade_date = %s GROUP BY 1 ORDER BY 1', (dates[-1],)):
            print('    %-12s %6s 只  区间 %s ~ %s'
                  % (r['ind_key'], f"{r['n']:,}", r['mn'], r['mx']))
    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
