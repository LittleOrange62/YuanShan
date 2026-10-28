# -*- coding: utf-8 -*-
"""
C11 · 采资金流（大盘主力/超大单/大单/中单/小单；个股与板块主力榜）。

**这是"资金去哪了"模块的数据源。** `md.stock_daily` 只有成交额，
看不出"这笔钱是谁的"：同样两万亿成交，主力净流入 +538 亿和净流出 200 亿，
复盘结论完全相反。

两块数据分属东财的两个域名，可用性不一样（2026-08-27 实测）：
    datacenter-web.eastmoney.com   大盘资金流   **通**，一次给 120 个交易日
    push2.eastmoney.com            个股/板块榜  **掐连接**（RemoteDisconnected）
所以本脚本对第二块是"能采就采、采不到不算失败"：`md.fund_flow_rank` 空着
页面也能跑（板块/个股去向先用成交额份额变化这个代理口径），
等域名恢复重跑即可补上真数据。

净额单位是**元**，不在库里折成亿 —— 折算是展示层的事，存的时候留原始精度。

用法：
    python scripts\\ingest\\c11_fundflow.py              # 大盘 + 尝试个股/板块
    python scripts\\ingest\\c11_fundflow.py --market-only
    python scripts\\ingest\\c11_fundflow.py --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402

UPSERT_MARKET = """
INSERT INTO md.market_fund_flow
    (trade_date, sh_close, sh_pct, sz_close, sz_pct,
     main_net, main_pct, xl_net, xl_pct, lg_net, lg_pct,
     md_net, md_pct, sm_net, sm_pct, src_batch_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (trade_date) DO UPDATE SET
    sh_close = EXCLUDED.sh_close, sh_pct = EXCLUDED.sh_pct,
    sz_close = EXCLUDED.sz_close, sz_pct = EXCLUDED.sz_pct,
    main_net = EXCLUDED.main_net, main_pct = EXCLUDED.main_pct,
    xl_net = EXCLUDED.xl_net, xl_pct = EXCLUDED.xl_pct,
    lg_net = EXCLUDED.lg_net, lg_pct = EXCLUDED.lg_pct,
    md_net = EXCLUDED.md_net, md_pct = EXCLUDED.md_pct,
    sm_net = EXCLUDED.sm_net, sm_pct = EXCLUDED.sm_pct,
    src_batch_id = EXCLUDED.src_batch_id, updated_at = now()
"""

UPSERT_RANK = """
INSERT INTO md.fund_flow_rank
    (trade_date, scope, obj_code, obj_name, window_days,
     main_net, main_pct, pct_chg, src_batch_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (trade_date, scope, obj_code, window_days) DO UPDATE SET
    obj_name = EXCLUDED.obj_name, main_net = EXCLUDED.main_net,
    main_pct = EXCLUDED.main_pct, pct_chg = EXCLUDED.pct_chg,
    src_batch_id = EXCLUDED.src_batch_id, updated_at = now()
"""

# 东财的"今日/3日/5日/10日" → 我们的窗口天数。短线看 1/3 日，中线看 5/10 日。
WINDOWS = {'今日': 1, '3日': 3, '5日': 5, '10日': 10}


def _f(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _retry(fn, tries: int = 5, wait: float = 4.0, label: str = ''):
    """
    退避重试。东财对短时间内的连续请求会直接掐连接（`RemoteDisconnected`），
    **同一个域名十分钟前还好用、下一次就被拒**，所以不能一次失败就放弃 ——
    实测退避几秒后往往就通了。
    """
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as exc:
            last = exc
            print('    %s第 %s/%s 次失败：%s，等 %.0fs 再试'
                  % (label, i + 1, tries, type(exc).__name__, wait * (i + 1)),
                  flush=True)
            time.sleep(wait * (i + 1))
    raise RuntimeError(f'{label}重试 {tries} 次仍失败：{last}')


def pull_market(batch_id: int, dry: bool) -> int:
    import akshare as ak
    df = _retry(ak.stock_market_fund_flow, label='大盘资金流 ')

    if df is None or df.empty:
        print('  大盘资金流返回空')
        return 0
    rows = []
    for r in df.to_dict('records'):
        try:
            day = dt.date.fromisoformat(str(r['日期'])[:10])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append((
            day, _f(r.get('上证-收盘价')), _f(r.get('上证-涨跌幅')),
            _f(r.get('深证-收盘价')), _f(r.get('深证-涨跌幅')),
            _f(r.get('主力净流入-净额')), _f(r.get('主力净流入-净占比')),
            _f(r.get('超大单净流入-净额')), _f(r.get('超大单净流入-净占比')),
            _f(r.get('大单净流入-净额')), _f(r.get('大单净流入-净占比')),
            _f(r.get('中单净流入-净额')), _f(r.get('中单净流入-净占比')),
            _f(r.get('小单净流入-净额')), _f(r.get('小单净流入-净占比')),
            batch_id))
    print('  取到 %s 个交易日：%s .. %s'
          % (len(rows), rows[0][0] if rows else '-', rows[-1][0] if rows else '-'))
    if dry or not rows:
        return len(rows)
    return engine.executemany(UPSERT_MARKET, rows, role='etl') or len(rows)


def _code_map() -> dict[str, str]:
    return {r['symbol']: r['ts_code'] for r in engine.query(
        "SELECT symbol, ts_code FROM ref.security "
        "WHERE asset_type = 'stock' AND symbol IS NOT NULL")}


def _sector_map() -> dict[str, str]:
    return {r['name']: r['sector_code'] for r in engine.query(
        'SELECT name, sector_code FROM ref.sector_info')}


def pull_rank(day: dt.date, batch_id: int, dry: bool) -> int:
    """
    个股与板块主力榜。**采不到不算失败** —— 见模块开头对域名可用性的说明。
    """
    import akshare as ak
    cmap = _code_map()
    smap = _sector_map()
    total = 0
    for label, wd in WINDOWS.items():
        # lambda 的默认参数把 label 绑死在这一轮。不这么写的话闭包捕获的是
        # 循环变量本身，四个窗口跑完全部指向最后一个 '10日'（ruff B023）。
        for scope, getter, namer in (
                ('stock',
                 lambda lb=label: ak.stock_individual_fund_flow_rank(indicator=lb),
                 lambda r: (cmap.get(str(r.get('代码') or '').zfill(6)),
                            r.get('名称'))),
                ('sector',
                 lambda lb=label: ak.stock_sector_fund_flow_rank(
                     indicator=lb, sector_type='行业资金流'),
                 lambda r: (smap.get(str(r.get('名称') or '').strip()),
                            r.get('名称')))):

            try:
                df = getter()
            except Exception as exc:
                print('    %s %s 采不到：%s' % (scope, label, type(exc).__name__))
                continue
            if df is None or df.empty:
                continue
            net_col = next((c for c in df.columns if '主力净流入-净额' in c), None)
            pct_col = next((c for c in df.columns if '主力净流入-净占比' in c), None)
            chg_col = next((c for c in df.columns if '涨跌幅' in c), None)
            rows = []
            for r in df.to_dict('records'):
                code, name = namer(r)
                if not code:
                    continue
                rows.append((day, scope, code, name, wd,
                             _f(r.get(net_col)), _f(r.get(pct_col)),
                             _f(r.get(chg_col)), batch_id))
            if rows and not dry:
                engine.executemany(UPSERT_RANK, rows, role='etl')
            total += len(rows)
            print('    %s %s：%s 行' % (scope, label, len(rows)))
            time.sleep(0.8)
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--market-only', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    day = engine.scalar('SELECT max(trade_date) FROM md.stock_daily')
    batch_id = 0 if args.dry_run else ops_repo.begin_batch(
        'akshare_em', 'md.market_fund_flow')
    print('=== 1. 大盘资金流 ===')
    try:
        n = pull_market(batch_id, args.dry_run)
        print('  写入 %s 行' % n)

        rank = 0
        if not args.market_only:
            print('=== 2. 个股 / 板块主力榜（基准日 %s）===' % day)
            rank = pull_rank(day, batch_id, args.dry_run)
            print('  写入 %s 行' % rank)
        if batch_id:
            ops_repo.end_batch(batch_id, row_count=n + rank)
    except Exception as exc:
        if batch_id:
            ops_repo.end_batch(batch_id, row_count=None, status='failed',
                               message=str(exc)[:500])
        raise

    if not args.dry_run:
        print('=== 3. 验收 ===')
        print('  md.market_fund_flow  %s 行，最新 %s'
              % (engine.scalar('SELECT count(*) FROM md.market_fund_flow'),
                 engine.scalar('SELECT max(trade_date) FROM md.market_fund_flow')))
        print('  md.fund_flow_rank    %s 行'
              % engine.scalar('SELECT count(*) FROM md.fund_flow_rank'))
    print('\nDONE')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
