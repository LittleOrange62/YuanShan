# -*- coding: utf-8 -*-
"""
算「倍量震荡」读数，落 `analytics.volume_double`（近 40 个交易日频繁倍量 ＋ 沿 60 日线震荡）。

**只读 `market_data.stock_daily`**，MA60 在 SQL 里自算（不依赖 `analytics.indicator_value`：
那张表只有最近 23 个交易日，40 天窗口里过半天数取不到 ma60，筛出来是空的）。
所以日线一入库就能跑，和评分没有先后关系。

一天约 10 秒（5,500 只票 × 100 个交易日的窗口函数），**回填要按天回显**。

用法：
    python -m ingest.jobs.d7_vol_double                 # 最新有日线的一天
    python -m ingest.jobs.d7_vol_double --recent 20     # 往前 20 个交易日
    python -m ingest.jobs.d7_vol_double --date 2026-08-28
    python -m ingest.jobs.d7_vol_double --dry-run       # 只打前 20 名不写库
    python -m ingest.jobs.d7_vol_double --verify        # 抽一只票逐日复核

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

from app.compute import vol_double as vd  # noqa: E402
from app.compute import volume_energy as ve  # noqa: E402
from app.db import engine  # noqa: E402
from app.compute import target_guard as tguard  # noqa: E402


def verify_energy(d: dt.date, codes: list[str]) -> int:
    """加权分复核：逐票在 Python 里按定义重算一遍，与库里的 `dbl_energy` 比。

    这条不是可选装饰 —— 加权分是**配置驱动**的（档表一改全变），
    只测"代码跑完了 rc=0"证明不了按对的那份配置算的。手算走的是同一份 `BANDS`/`points()`，
    但不碰那条 SQL，所以两边一致才算真一致。
    """
    bad = 0
    for c in codes:
        rows = list(reversed(engine.query(
            'SELECT trade_date, volume_shares FROM market_data.stock_daily'
            ' WHERE security_code=%(c)s AND trade_date<=%(d)s'
            '   AND volume_shares>0 AND close IS NOT NULL'
            ' ORDER BY trade_date DESC LIMIT %(span)s',
            {'c': c, 'd': d, 'span': ve.LOOKBACK + 5})))
        market = engine.scalar('SELECT market FROM reference_data.security'
                               ' WHERE security_code=%s', (c,))
        sh = ve.points(market)
        total = 0.0
        for i in range(len(rows)):
            base = [float(x['volume_shares']) for x in rows[max(0, i - ve.VMA_WIN):i]]
            if len(base) < ve.VMA_WIN:
                continue
            ratio = float(rows[i]['volume_shares']) / (sum(base) / len(base))
            for (lo, hi), lab in zip(ve.BANDS, ve.BAND_LABELS):
                if ratio >= lo and (hi is None or ratio < hi):
                    total += sh.get(lab, 0.0)
                    break
        stored = engine.scalar('SELECT dbl_energy FROM analytics.volume_double'
                               ' WHERE security_code=%s AND trade_date=%s', (c, d))
        diff = abs(round(total, 4) - float(stored or 0))
        ok = stored is not None and diff < 0.0002
        bad += 0 if ok else 1
        print('  %s 加权分 库里 %s / 复核 %.4f（市场 %s，窗口 %d 根）%s'
              % (c, stored, total, market, min(len(rows), ve.LOOKBACK),
                 '' if ok else '  ← [FAIL]'))
    return bad


def verify(d: dt.date) -> int:
    """
    独立复核：**逐日拉原始行、在 Python 里重数一遍**倍量次数与穿越次数。

    盯的是三件最容易错的事：
    1. 倍量基准有没有把当日算进去（算进去会系统性少数几次）；
    2. MA60 是不是真用了 60 根 K 线（不足 60 根却算出值 = 次新股会挤满榜单）；
    3. 穿越次数有没有把 ma60 为 NULL 的那几天当成"翻转"。
    """
    top = engine.query(
        """
        SELECT security_code, dbl_count, cross_count, ma60_days, dev_med, above_ratio
          FROM analytics.volume_double WHERE trade_date = %s
         ORDER BY dbl_count DESC, security_code LIMIT 3
        """, (d,))
    if not top:
        print('  %s 库里没有这一天，先跑不带 --verify 的' % d)
        return 1

    bad = 0
    for t in top:
        rows = engine.query(
            """
            SELECT trade_date, close, volume_shares FROM market_data.stock_daily
             WHERE security_code = %(c)s AND trade_date <= %(d)s
               AND close IS NOT NULL AND volume_shares > 0
             ORDER BY trade_date DESC LIMIT %(span)s
            """, {'c': t['security_code'], 'd': d, 'span': vd.SPAN})
        rows = list(reversed(rows))
        closes = [float(r['close']) for r in rows]
        vols = [float(r['volume_shares']) for r in rows]
        n = min(vd.LOOKBACK, len(rows))
        idx = range(len(rows) - n, len(rows))

        dbl = 0
        for i in idx:
            base = vols[max(0, i - 5):i]        # 前 5 日，**不含当日**
            if len(base) == 5 and vols[i] >= vd.MULT * (sum(base) / 5):
                dbl += 1

        ma, sides = {}, []
        for i in idx:
            if i + 1 >= vd.MA_WIN:              # 满 60 根才算
                ma[i] = sum(closes[i - vd.MA_WIN + 1:i + 1]) / vd.MA_WIN
                sides.append(1 if closes[i] >= ma[i] else -1)
            else:
                sides.append(None)
        cross = sum(1 for a, b in zip(sides, sides[1:], strict=False)
                    if a is not None and b is not None and a != b)
        ma_days = sum(1 for s in sides if s is not None)

        ok = (dbl == t['dbl_count'] and cross == t['cross_count']
              and ma_days == t['ma60_days'])
        bad += 0 if ok else 1
        print('  %s 倍量 库里%s/复核%s，穿越 库里%s/复核%s，MA60天数 库里%s/复核%s %s'
              % (t['security_code'], t['dbl_count'], dbl, t['cross_count'], cross,
                 t['ma60_days'], ma_days, '' if ok else '  ← [FAIL]'))

    picks, meta = vd.picks(d)
    print('  当前阈值下入选 %s 只（%s）'
          % (len(picks), '、'.join('%s=%s' % (k, v) for k, v in meta.items()
                                   if k != 'trade_date')))
    e_bad = verify_energy(d, [t['security_code'] for t in top])
    print('  复核 %s 只票，计数不一致 %s 处、加权分不一致 %s 处'
          % (len(top), bad, e_bad))
    return 0 if bad == 0 and e_bad == 0 else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--recent', type=int, default=1)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--verify', action='store_true')
    args = ap.parse_args()

    end = dt.date.fromisoformat(args.date) if args.date else None

    if args.verify:
        d = end or vd.latest_date()
        if not d:
            print('analytics.volume_double 是空的，先跑不带 --verify 的')
            return 1
        print('=== 倍量震荡复核 %s ===' % d)
        rc = verify(d)
        engine.close_all()
        return rc

    # 目标日闸门：`--date D` 要的是 D。输入不齐就出声，不退回算别的日子。
    _no = tguard.missing_target_reason(args.date, 'market_data.stock_daily', 'trade_date',
                                      where='', min_rows=1000)
    if _no:
        print(_no)
        engine.close_all()
        return 1

    dates = vd.trade_days(end, args.recent)
    if not dates:
        print('%s 及之前没有 market_data.stock_daily，先跑 `python -m ingest.jobs.c1_day` 或 `python -m ingest.jobs.c12_tq_day --date <哪天>`'
              % (end or '今天'))
        return 1

    print('=== 算倍量震荡：%s 个交易日 %s .. %s（窗口 %s 日，倍量 ≥%s 倍）==='
          % (len(dates), dates[0], dates[-1], vd.LOOKBACK, vd.MULT))
    if args.dry_run:
        rows = vd.compute(dates[-1], limit=20)
        print('  --dry-run：%s 倍量次数前 20（不写库）' % dates[-1])
        for r in rows:
            print('    %s 倍量 %s 次 / 穿越 %s / 偏离中位 %.2f%% / 线上占比 %.0f%%'
                  % (r['security_code'], r['dbl_count'], r['cross_count'],
                     float(r['dev_med'] or 0), float(r['above_ratio'] or 0)))
        engine.close_all()
        return 0

    t0 = time.time()
    total = 0
    # 逐天回显：一天 10 秒，20 天就是三分多钟，中间不出声会被当成卡死
    for i, d in enumerate(dates, 1):
        t1 = time.time()
        n = vd.refresh([d])
        total += n
        picks, _ = vd.picks(d)
        print('  [%d/%d] %s 落 %s 行，当前阈值入选 %s 只（%.1fs）'
              % (i, len(dates), d, f'{n:,}', len(picks), time.time() - t1),
              flush=True)
    print('  合计 %s 行，%.1fs' % (f'{total:,}', time.time() - t0))

    # 加权分（`dbl_energy`）跟着这一步一起追平。**整天/整段只跑一条集合式 SQL**：
    # 单场 13 秒、211 场全量 123 秒（2026-10-19 实测），逐日跑等于把同一个窗口重扫 N 遍。
    en = ve.rebuild(dates[0], dates[-1])
    print('  加权分 %s .. %s：追平 %s/%s 行（指纹 %s，%.1fs）%s'
          % (en['from'], en['to'], f'{en["rows_synced"]:,}', f'{en["rows_target"]:,}',
             en['fingerprint'], en['seconds'],
             '' if en['ok'] else '  ← [FAIL] 还有 %s 行没打上指纹' % en['rows_unsynced']),
          flush=True)

    print('=== 复核最后一天 ===')
    rc = verify(dates[-1])
    if not en['ok']:
        rc = rc or 1        # 计数复核绿了但加权分没追平，也是这一步没做完
    engine.close_all()
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
