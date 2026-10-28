# -*- coding: utf-8 -*-
"""
D1 · 算指标。

用法：
    python -m ingest.jobs.d1_indicators --dry-run
    python -m ingest.jobs.d1_indicators                    # 最新交易日
    python -m ingest.jobs.d1_indicators --date 2026-08-25
    python -m ingest.jobs.d1_indicators --recent 20         # 最近 20 个交易日
    python -m ingest.jobs.d1_indicators --verify            # 与 pandas 手算对照
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
from app.compute import target_guard as tguard  # noqa: E402
from app.db.repo import security as sec_repo  # noqa: E402


def verify(trade_date: dt.date) -> int:
    """
    抽四条指标与 pandas 直算比对。求值器算错了不会报错，只会给出错的数，
    所以必须有一处独立算法做对照。

    ⚠️ 均线族在 2026-10-14 换成了**复权价**（`config.indicator` 的 ma5..ma250、bias5/10/20，
    缘由见 `app/db/sql/35_ma_hfq_screen.sql` 头部：未复权价会让 133/4,933 只票的
    「在年线上方还是下方」判定翻转）。这一处的主算也跟着用复权价 ——
    **两边一起改才是真对照**：只改生产实现、这里还按未复权价手算，`--verify` 会一路报红，
    而红久了人就当它是噪声，那这条独立校验就名存实亡了。
    复权因子的兜底规矩与求值器那句 `IF(adjustment_factor > 0, …, 1)` 一致：
    缺因子（全表 61,295 行、115 只票）按不复权算，不整体给空。
    """
    import numpy as np
    import pandas as pd

    from app.compute import expr as ex
    panel = indicators.load_panel(trade_date, 300)
    if panel.empty:
        print('  没有数据')
        return 1
    target = pd.Timestamp(trade_date).date()
    ok = True

    af = panel['adjustment_factor']
    af = pd.Series(np.where(af.to_numpy(dtype='float64') > 0,
                            af.to_numpy(dtype='float64'), 1.0), index=panel.index)
    aclose = panel['close'].astype('float64') * af
    by = aclose.groupby(level='security_code', sort=False)

    cases = {
        'ma20': (by.transform(lambda s: s.rolling(20, min_periods=20).mean()),
                 'MA(复权close, 20)'),
        'ma250': (by.transform(lambda s: s.rolling(250, min_periods=250).mean()),
                  'MA(复权close, 250)'),
        'bias20': None,
        'std20': None,
        'atr14': None,
    }
    # bias20 = (复权价 − 复权MA20) / 复权MA20 * 100 —— 价与均线必须同底
    ma20 = by.transform(lambda s: s.rolling(20, min_periods=20).mean())
    cases['bias20'] = ((aclose - ma20) / ma20 * 100, 'BIAS20（复权价）')
    pct = panel['pct_change']
    cases['std20'] = (pct.groupby(level='security_code', sort=False).transform(
        lambda s: s.rolling(20, min_periods=20).std(ddof=1)), 'STD(pct_change,20)')
    high, low = panel['high'], panel['low']
    # ATR14 的公式本轮没动（仍是未复权价），所以它的手算对照也必须留在未复权价上 ——
    # 拿复权价去对未复权的实现，红了就不是在比算法，是在比口径。
    raw = panel['close'].astype('float64')
    prev = raw.groupby(level='security_code', sort=False).shift(1)
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()],
                   axis=1).max(axis=1)
    cases['atr14'] = (tr.groupby(level='security_code', sort=False).transform(
        lambda s: s.rolling(14, min_periods=14).mean()), 'ATR14')


    specs = {s['indicator_key']: s for s in indicators.load_specs()}
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

    # 目标日闸门：`--date D` 要的是 D。输入不齐就出声，不退回算别的日子。
    _no = tguard.missing_target_reason(args.date, 'market_data.stock_daily', 'trade_date',
                                      where='', min_rows=1000)
    if _no:
        print(_no)
        engine.close_all()
        return 1

    dates = sec_repo.recent_trade_dates(args.recent, end)
    print('=== 算指标：%s 个交易日 %s .. %s ==='
          % (len(dates), dates[0], dates[-1]))
    total = 0
    # 一场算崩不许把剩下几十场一起带走（2026-10-14：84 场的均线回算跑到第 31 场被一次
    # UndefinedTable 整批中止，末尾那 53 场一行日志都没有，看着像"这批跑过了"）。
    # 逐场兜住、点名是哪一场、什么异常，末尾用非零退出码把"这批有洞"交给台账。
    broken: list[str] = []
    for d in dates:
        try:
            st = indicators.compute_date(d, ind_keys=args.ind, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001
            print('  %s  [ABORT] %s: %s' % (d, type(exc).__name__, exc))
            broken.append(str(d))
            continue
        if 'error' in st:
            print('  %s  %s' % (d, st['error']))
            broken.append(str(d))
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
        print('  analytics.indicator_value   %s' % engine.scalar(
            'SELECT count(*) FROM analytics.indicator_value'))
        print('  指标数                %s' % engine.scalar(
            'SELECT count(DISTINCT indicator_key) FROM analytics.indicator_value'))
        for r in engine.query(
                'SELECT indicator_key, count(*) AS n, round(min(value),4) AS mn, '
                'round(max(value),4) AS mx FROM analytics.indicator_value '
                'WHERE trade_date = %s GROUP BY 1 ORDER BY 1', (dates[-1],)):
            print('    %-12s %6s 只  区间 %s ~ %s'
                  % (r['indicator_key'], f"{r['n']:,}", r['mn'], r['mx']))
    if broken:
        print('\n⚠️ 这批有 %s 场没算成：%s' % (len(broken), '、'.join(broken)))
        print('   补法：--date <这批里最晚的那一场> --recent <覆盖到的场数>，'
              '或等失败单步重试阶梯那一档再点一次')
    engine.close_all()
    return 1 if broken else 0


if __name__ == '__main__':
    raise SystemExit(main())
