# -*- coding: utf-8 -*-
"""
D2 · 算评分。

用法：
    python scripts\\compute\\d2_scores.py --dry-run
    python scripts\\compute\\d2_scores.py                    # 最新交易日
    python scripts\\compute\\d2_scores.py --date 2026-08-25
    python scripts\\compute\\d2_scores.py --recent 20
    python scripts\\compute\\d2_scores.py --cap stock_quality
    python scripts\\compute\\d2_scores.py --verify           # 与迁入的历史评分对照

**--verify 一律不落库。** 迁入的 227,985 行历史评分（is_stale=true）就是
对照基准，而 UPSERT 会按 (ts_code, trade_date, cap_key) 原地覆盖它并把
is_stale 置为 false —— 先写再比，基准就没了。所以对照跑 dry-run，
并把结论写进 ops.quality_result，这样等真正回算覆盖掉历史行之后，
证据还在库里。
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app.compute import scoring  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402
from app.db.repo import security as sec_repo  # noqa: E402

LEGACY_CAPS = ('stock_quality', 'env_resonance', 'oper_score')
# 相关性低于此值就认为"新旧不是同一个东西"，需要给出解释而不是放过
CORR_WARN = 0.30


def legacy_window() -> tuple[dt.date | None, dt.date | None]:
    r = engine.query(
        'SELECT min(trade_date) AS mn, max(trade_date) AS mx '
        'FROM fct.score WHERE is_stale')
    return (r[0]['mn'], r[0]['mx']) if r else (None, None)


def verify(sample: int) -> int:
    """
    在历史评分覆盖的日期里抽样，新旧同日同票做相关性对照。

    用 Spearman 秩相关而不是 Pearson：旧系统的量纲和分档规则跟新的不一样
    （比如涨停一刀切 1.1、位置用 60 日而非 250 日），数值不可能对得上，
    但**排序**应该大体一致 —— 复盘选票看的是排序。
    Pearson 一并算出来只作参考。
    """
    import pandas as pd

    mn, mx = legacy_window()
    if not mn:
        print('  fct.score 里没有 is_stale=true 的历史评分，无法对照')
        return 1

    dates = [r['trade_date'] for r in engine.query(
        """
        SELECT DISTINCT trade_date FROM fct.score
         WHERE is_stale AND cap_key = 'oper_score'
         ORDER BY trade_date DESC
        """)]
    if not dates:
        print('  历史评分里没有 oper_score')
        return 1
    step = max(1, len(dates) // sample)
    picked = dates[::step][:sample]
    print('  历史评分窗口 %s .. %s，共 %s 个交易日，抽 %s 天'
          % (mn, mx, len(dates), len(picked)))

    ok = True
    summary: list[dict] = []
    for d in sorted(picked):
        st = scoring.compute_date(d, dry_run=True, collect=True)
        if 'error' in st:
            print('  %s  %s' % (d, st['error']))
            ok = False
            continue
        new = pd.DataFrame(st['rows'], columns=[
            'ts_code', 'trade_date', 'cap_key', 'value', 'rank', 'pct', 'q', 'task'])
        old = pd.DataFrame(engine.query(
            'SELECT ts_code, cap_key, value FROM fct.score '
            'WHERE trade_date = %s AND is_stale AND cap_key = ANY(%s)',
            (d, list(LEGACY_CAPS))))
        if old.empty:
            print('  %s  历史评分缺这一天' % d)
            continue

        print('  %s  新算 %s 只 / %s 个功能点  %.1fs'
              % (d, st['codes'], len(st['caps']), st['seconds']))
        for cap in LEGACY_CAPS:
            a = new[new['cap_key'] == cap].set_index('ts_code')['value']
            b = old[old['cap_key'] == cap].set_index('ts_code')['value']
            common = a.index.intersection(b.index)
            if len(common) < 30:
                print('    [SKIP] %-14s 可比对样本只有 %s 只' % (cap, len(common)))
                continue
            x = a[common].astype(float)
            y = b[common].astype(float)
            sp = float(x.corr(y, method='spearman'))
            pe = float(x.corr(y, method='pearson'))
            # 头部一致性：新旧各取前 10%，看重叠比例。相关性看整体，
            # 这个看真正会被推到页面上的那批票
            k = max(10, len(common) // 10)
            top_new = set(x.nlargest(k).index)
            top_old = set(y.nlargest(k).index)
            overlap = len(top_new & top_old) / k * 100
            flag = 'OK  ' if sp >= CORR_WARN else 'WARN'
            if sp < CORR_WARN:
                ok = False
            print('    [%s] %-14s %s 只  Spearman %+.3f  Pearson %+.3f  '
                  '前10%%重叠 %.1f%%  新区间 %.1f~%.1f  旧区间 %.1f~%.1f'
                  % (flag, cap, f'{len(common):,}', sp, pe, overlap,
                     x.min(), x.max(), y.min(), y.max()))
            summary.append({'trade_date': str(d), 'cap_key': cap,
                            'n': len(common), 'spearman': round(sp, 4),
                            'pearson': round(pe, 4),
                            'top_decile_overlap_pct': round(overlap, 2),
                            'new_min': round(float(x.min()), 2),
                            'new_max': round(float(x.max()), 2),
                            'old_min': round(float(y.min()), 2),
                            'old_max': round(float(y.max()), 2)})

    if summary:
        by_cap = {}
        for s in summary:
            by_cap.setdefault(s['cap_key'], []).append(s['spearman'])
        print('\n  === 各功能点平均秩相关 ===')
        for cap, vals in sorted(by_cap.items()):
            print('    %-14s %+.3f  （%s 天）'
                  % (cap, sum(vals) / len(vals), len(vals)))
        ops_repo.save_quality_result(
            None, 'score_legacy_correlation', 'fct.score',
            'warn' if not ok else 'info', ok,
            actual={cap: round(sum(v) / len(v), 4) for cap, v in by_cap.items()},
            expected={'spearman_min': CORR_WARN}, sample=summary)
        print('  已记入 ops.quality_result / score_legacy_correlation')
    return 0 if ok else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--recent', type=int, default=1)
    ap.add_argument('--cap', action='append', default=None)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--sample', type=int, default=5,
                    help='--verify 抽多少个交易日对照')
    args = ap.parse_args()

    if args.verify:
        print('=== 新算评分 vs 迁入的历史评分 ===')
        rc = verify(args.sample)
        engine.close_all()
        return rc

    end = (dt.date.fromisoformat(args.date) if args.date
           else sec_repo.latest_trade_date())
    if not end:
        print('拿不到交易日')
        return 1
    dates = sec_repo.recent_trade_dates(args.recent, end)
    print('=== 算评分：%s 个交易日 %s .. %s ==='
          % (len(dates), dates[0], dates[-1]))

    mn, mx = legacy_window()
    if mn and not args.dry_run and any(mn <= d <= mx for d in dates):
        print('  注意：本次要算的日期落在历史评分窗口 %s .. %s 内，'
              '写入会覆盖那批 is_stale=true 的对照基准。' % (mn, mx))
        print('  如果还没做过对照，先跑 --verify。')

    for d in dates:
        st = scoring.compute_date(d, cap_keys=args.cap, dry_run=args.dry_run)
        if 'error' in st:
            print('  %s  %s' % (d, st['error']))
            continue
        print('  %s  %s 只  回溯 %s 天  评分 %s 行 / 分项 %s 行  %.1fs'
              % (d, f"{st['codes']:,}", st['lookback'],
                 f"{st['score_rows']:,}", f"{st['detail_rows']:,}",
                 st['seconds']))
        for cap, info in st['caps'].items():
            if isinstance(info, str):
                print('    [SKIP] %-14s %s' % (cap, info))
                continue
            print('    %-14s %6s 只  分项 %s'
                  % (cap, f"{info['scored']:,}", ','.join(info['parts'])))
            for k in info.get('empty_parts') or []:
                print('      [EMPTY] %s 一个值都没算出来，该分项的权重被静默摊到'
                      '其他分项上了' % k)
            for k, msg in (info.get('failed') or {}).items():
                print('      [FAIL] %s: %s' % (k, msg))

    if not args.dry_run:
        print('\n=== 验收 ===')
        for r in engine.query(
                'SELECT cap_key, count(*) AS n, round(min(value),2) AS mn, '
                'round(max(value),2) AS mx, count(DISTINCT quadrant) AS nq '
                'FROM fct.score WHERE trade_date = %s AND NOT is_stale '
                'GROUP BY 1 ORDER BY 1', (dates[-1],)):
            print('  %-14s %6s 只  区间 %s ~ %s  象限 %s 种'
                  % (r['cap_key'], f"{r['n']:,}", r['mn'], r['mx'], r['nq']))
        print('  fct.score          %s' % engine.scalar(
            'SELECT count(*) FROM fct.score'))
        print('  fct.score_detail   %s' % engine.scalar(
            'SELECT count(*) FROM fct.score_detail'))
        for r in engine.query(
                "SELECT quadrant, count(*) AS n FROM fct.score "
                "WHERE trade_date = %s AND cap_key = 'oper_score' "
                "GROUP BY 1 ORDER BY 1", (dates[-1],)):
            print('    象限 %-4s %6s 只' % (r['quadrant'], f"{r['n']:,}"))
    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
