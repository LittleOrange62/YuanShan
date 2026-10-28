# -*- coding: utf-8 -*-
"""
C6 · 采集龙虎榜，写入 md.lhb_detail 与 md.lhb_inst。

**为什么要有这一步**：游资组热度算不出来就是缺这个。龙虎榜是唯一公开的
"谁在买"数据 —— 席位明细能区分机构专用席位、知名游资营业部和散户席位，
而日线只能看到总成交额。旧系统的 `calc_group_heat` 缺的正是这一半。

**数据源**：只能走 akshare（东财转发）。Tushare 的 `top_list` / `top_inst`
在本项目 token 下是 40203 无权限，实测确认。

    明细    ak.stock_lhb_detail_em('20260826','20260826')    347 行 21 列
    机构    ak.stock_lhb_jgmmtj_em('20260826','20260826')    198 行 16 列

**主键必须带 reason**：一只票同一天可能因多条原因同时上榜（"日涨幅偏离值达 7%"
和"连续三个交易日内涨幅达 20%"），不带 reason 会在同一批次内 ON CONFLICT
自己撞自己，只留下一条，机构统计那张表同理。

**东财接口不稳**：实测同一时段 `stock_board_concept_name_em` /
`stock_individual_fund_flow` 连续 3 次全部 `RemoteDisconnected`。所以这里带
指数退避重试，而不是一次失败就当没有数据 —— 静默少一天龙虎榜，
游资热度会平白掉一个台阶且不报错。

**代码没有市场后缀**，按号段还原：6/5/9→SH，0/1/2/3→SZ，4/8/920→BJ。

用法：
    python scripts\\ingest\\c6_lhb.py                       # 最近交易日
    python scripts\\ingest\\c6_lhb.py --date 20260826
    python scripts\\ingest\\c6_lhb.py --start 20260701 --end 20260827
    python scripts\\ingest\\c6_lhb.py --date 20260826 --dry-run
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

RETRY = 3
RETRY_BASE = 1.5

DETAIL_STG, DETAIL_TARGET = 'md.stg_c6_detail', 'md.lhb_detail'
INST_STG, INST_TARGET = 'md.stg_c6_inst', 'md.lhb_inst'

DETAIL_COLS = ('ts_code', 'trade_date', 'reason', 'close', 'pct_chg', 'net_buy',
               'buy_amount', 'sell_amount', 'lhb_amount', 'market_amount',
               'net_buy_ratio', 'amount_ratio', 'turnover_rate_pct',
               'float_mv_yuan', 'interpret', 'src_batch_id')
INST_COLS = ('ts_code', 'trade_date', 'reason', 'buy_inst_count',
             'sell_inst_count', 'inst_buy', 'inst_sell', 'inst_net',
             'market_amount', 'net_ratio', 'src_batch_id')

DETAIL_DDL = ('ts_code text, trade_date date, reason text, close numeric(14,4), '
              'pct_chg numeric(10,4), net_buy numeric(20,2), '
              'buy_amount numeric(20,2), sell_amount numeric(20,2), '
              'lhb_amount numeric(20,2), market_amount numeric(20,2), '
              'net_buy_ratio numeric(10,4), amount_ratio numeric(10,4), '
              'turnover_rate_pct numeric(10,4), float_mv_yuan numeric(24,2), '
              'interpret text, src_batch_id bigint')
INST_DDL = ('ts_code text, trade_date date, reason text, buy_inst_count int, '
            'sell_inst_count int, inst_buy numeric(20,2), '
            'inst_sell numeric(20,2), inst_net numeric(20,2), '
            'market_amount numeric(20,2), net_ratio numeric(10,4), '
            'src_batch_id bigint')

DETAIL_MAP = {
    'code': '代码', 'close': '收盘价', 'pct_chg': '涨跌幅',
    'net_buy': '龙虎榜净买额', 'buy_amount': '龙虎榜买入额',
    'sell_amount': '龙虎榜卖出额', 'lhb_amount': '龙虎榜成交额',
    'market_amount': '市场总成交额', 'net_buy_ratio': '净买额占总成交比',
    'amount_ratio': '成交额占总成交比', 'turnover_rate_pct': '换手率',
    'float_mv_yuan': '流通市值', 'interpret': '解读', 'reason': '上榜原因',
}
INST_MAP = {
    'code': '代码', 'buy_inst_count': '买方机构数',
    'sell_inst_count': '卖方机构数', 'inst_buy': '机构买入总额',
    'inst_sell': '机构卖出总额', 'inst_net': '机构买入净额',
    'market_amount': '市场总成交额', 'net_ratio': '机构净买额占总成交额比',
    'reason': '上榜原因',
}


def market_of(code: str) -> str:
    """按号段还原交易所后缀。"""
    if code.startswith('920') or code[0] in '48':
        return 'BJ'
    if code[0] in '659':
        return 'SH'
    return 'SZ'


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(str(v).replace(',', '').strip())
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _text(v, default: str = '') -> str:
    if v is None:
        return default
    s = str(v).strip()
    return default if s in ('', 'nan', 'None') else s


def fetch(fn, day: dt.date, what: str):
    """带指数退避的取数。东财偶发 RemoteDisconnected，一次失败不等于没数据。"""
    ds = day.strftime('%Y%m%d')
    last = None
    for i in range(RETRY):
        try:
            return fn(start_date=ds, end_date=ds)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i < RETRY - 1:
                time.sleep(RETRY_BASE ** (i + 1))
    raise RuntimeError(f'{what} 重试 {RETRY} 次仍失败：{type(last).__name__}: {last}')


def create_staging() -> None:
    with engine.conn('admin') as c, c.cursor() as cur:
        for stg, ddl in ((DETAIL_STG, DETAIL_DDL), (INST_STG, INST_DDL)):
            cur.execute(f'DROP TABLE IF EXISTS {stg}')
            cur.execute(f'CREATE UNLOGGED TABLE {stg} ({ddl})')


def drop_staging() -> None:
    with engine.conn('admin') as c, c.cursor() as cur:
        for stg in (DETAIL_STG, INST_STG):
            cur.execute(f'DROP TABLE IF EXISTS {stg}')


def _check_cols(df, mapping: dict[str, str], where: str) -> None:
    missing = [v for v in mapping.values() if v not in df.columns]
    if missing:
        raise RuntimeError(f'{where} 列与预期不符，缺少 {missing}；'
                           f'实际 = {list(df.columns)}')


def rows_detail(day: dt.date, batch_id: int) -> list[tuple]:
    import akshare as ak
    df = fetch(ak.stock_lhb_detail_em, day, 'stock_lhb_detail_em')
    if df is None or df.empty:
        return []
    _check_cols(df, DETAIL_MAP, 'stock_lhb_detail_em')
    out = []
    for r in df.itertuples(index=False):
        d = r._asdict()
        code = _text(d[DETAIL_MAP['code']])
        if len(code) != 6 or not code.isdigit():
            continue
        out.append((
            f'{code}.{market_of(code)}', day,
            _text(d[DETAIL_MAP['reason']], '未标注'),
            _num(d[DETAIL_MAP['close']]), _num(d[DETAIL_MAP['pct_chg']]),
            _num(d[DETAIL_MAP['net_buy']]), _num(d[DETAIL_MAP['buy_amount']]),
            _num(d[DETAIL_MAP['sell_amount']]), _num(d[DETAIL_MAP['lhb_amount']]),
            _num(d[DETAIL_MAP['market_amount']]),
            _num(d[DETAIL_MAP['net_buy_ratio']]), _num(d[DETAIL_MAP['amount_ratio']]),
            _num(d[DETAIL_MAP['turnover_rate_pct']]),
            _num(d[DETAIL_MAP['float_mv_yuan']]),
            _text(d[DETAIL_MAP['interpret']]) or None, batch_id))
    return out


def rows_inst(day: dt.date, batch_id: int) -> list[tuple]:
    import akshare as ak
    df = fetch(ak.stock_lhb_jgmmtj_em, day, 'stock_lhb_jgmmtj_em')
    if df is None or df.empty:
        return []
    _check_cols(df, INST_MAP, 'stock_lhb_jgmmtj_em')
    out = []
    for r in df.itertuples(index=False):
        d = r._asdict()
        code = _text(d[INST_MAP['code']])
        if len(code) != 6 or not code.isdigit():
            continue
        bc, sc = _num(d[INST_MAP['buy_inst_count']]), _num(d[INST_MAP['sell_inst_count']])
        out.append((
            f'{code}.{market_of(code)}', day,
            _text(d[INST_MAP['reason']], '未标注'),
            int(bc) if bc is not None else None,
            int(sc) if sc is not None else None,
            _num(d[INST_MAP['inst_buy']]), _num(d[INST_MAP['inst_sell']]),
            _num(d[INST_MAP['inst_net']]), _num(d[INST_MAP['market_amount']]),
            _num(d[INST_MAP['net_ratio']]), batch_id))
    return out


def merge(stg: str, target: str, cols, *, overwrite: bool) -> int:
    collist = ', '.join(cols)
    key = '(ts_code, trade_date, reason)'
    if overwrite:
        sets = ', '.join(f'{c} = EXCLUDED.{c}' for c in cols
                         if c not in ('ts_code', 'trade_date', 'reason'))
        tail = f'ON CONFLICT {key} DO UPDATE SET {sets}, updated_at = now()'
        cond = ''
    else:
        tail = f'ON CONFLICT {key} DO NOTHING'
        cond = (f'WHERE NOT EXISTS (SELECT 1 FROM {target} t '
                'WHERE t.ts_code = s.ts_code AND t.trade_date = s.trade_date '
                'AND t.reason = s.reason)')
    return engine.execute(
        f"""
        INSERT INTO {target} ({collist})
        SELECT DISTINCT ON (ts_code, trade_date, reason) {collist}
          FROM {stg} s
        {cond}
         ORDER BY ts_code, trade_date, reason
        {tail}
        """, role='admin')


def target_dates(args) -> list[dt.date]:
    if args.start:
        start = dt.datetime.strptime(args.start, '%Y%m%d').date()
        end = (dt.datetime.strptime(args.end, '%Y%m%d').date()
               if args.end else dt.date.today())
        out, cur = [], start
        while cur <= end:
            if cur.weekday() < 5:
                out.append(cur)
            cur += dt.timedelta(days=1)
        return out
    if args.date:
        return [dt.datetime.strptime(args.date, '%Y%m%d').date()]
    latest = engine.scalar('SELECT max(trade_date) FROM md.stock_daily')
    if latest is None:
        raise SystemExit('md.stock_daily 是空的，请显式给 --date')
    return [latest]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='YYYYMMDD')
    ap.add_argument('--start', help='YYYYMMDD')
    ap.add_argument('--end', help='YYYYMMDD')
    ap.add_argument('--overwrite', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    days = target_dates(args)
    print('=== 目标日期 %d 天：%s .. %s ===' % (len(days), days[0], days[-1]))

    batch_id = 0
    if not args.dry_run:
        batch_id = ops_repo.begin_batch(
            'akshare_lhb', DETAIL_TARGET,
            source_path='stock_lhb_detail_em + stock_lhb_jgmmtj_em')
        print('批次 %s' % batch_id)
        create_staging()

    nd = ni = 0
    try:
        for day in days:
            t0 = time.time()
            got = {}
            for label, fn, stg, cols in (
                    ('明细', rows_detail, DETAIL_STG, DETAIL_COLS),
                    ('机构', rows_inst, INST_STG, INST_COLS)):
                try:
                    rows = fn(day, batch_id)
                except Exception as exc:  # noqa: BLE001
                    print('  %s %s 拉取失败：%s' % (day, label, str(exc)[:140]))
                    continue
                got[label] = len(rows)
                if rows and not args.dry_run:
                    engine.copy_rows(stg, list(cols), iter(rows), role='admin')
            if not any(got.values()):
                print('  %s 无龙虎榜数据（非交易日或当日无票上榜），跳过' % day)
                continue
            nd += got.get('明细', 0)
            ni += got.get('机构', 0)
            print('  %s  明细 %4d 条 | 机构 %4d 条  %.1fs'
                  % (day, got.get('明细', 0), got.get('机构', 0), time.time() - t0))

        if args.dry_run:
            print('\n--dry-run，未写库（明细 %s / 机构 %s）' % (f'{nd:,}', f'{ni:,}'))
            return 0

        print('\n=== 合并%s ===' % ('（覆盖）' if args.overwrite else '（只补空洞）'))
        a = merge(DETAIL_STG, DETAIL_TARGET, DETAIL_COLS, overwrite=args.overwrite)
        b = merge(INST_STG, INST_TARGET, INST_COLS, overwrite=args.overwrite)
        print('  md.lhb_detail %s 行（暂存 %s）' % (f'{a:,}', f'{nd:,}'))
        print('  md.lhb_inst   %s 行（暂存 %s）' % (f'{b:,}', f'{ni:,}'))
        ops_repo.end_batch(batch_id, status='ok', row_count=a + b)
    except Exception as exc:
        if not args.dry_run:
            ops_repo.end_batch(batch_id, status='failed', message=str(exc)[:2000])
        raise
    finally:
        if not args.dry_run:
            drop_staging()

    print('\n=== 验收 ===')
    for label, sql in (
            ('md.lhb_detail', 'SELECT count(*) FROM md.lhb_detail'),
            ('md.lhb_inst', 'SELECT count(*) FROM md.lhb_inst'),
            ('日期区间', "SELECT min(trade_date)::text || ' .. ' || "
                         'max(trade_date)::text FROM md.lhb_detail'),
            ('一票多原因',
             'SELECT count(*) FROM (SELECT ts_code, trade_date FROM md.lhb_detail '
             'GROUP BY 1,2 HAVING count(*) > 1) x'),
    ):
        print('  %-14s %s' % (label, engine.scalar(sql)))
    for r in engine.query(
            """
            SELECT trade_date, count(*) n,
                   round(sum(net_buy) / 1e8, 2) net_yi
              FROM md.lhb_detail GROUP BY 1 ORDER BY 1 DESC LIMIT 5
            """):
        print('  %s  %4d 条  龙虎榜净买额合计 %+9.2f 亿元'
              % (r['trade_date'], r['n'], r['net_yi'] or 0))

    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
