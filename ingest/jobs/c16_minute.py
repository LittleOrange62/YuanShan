# -*- coding: utf-8 -*-
"""
C16 · 从通达信 vipdoc 的 `.lc1`/`.lc5` 摄取 1/5 分钟线，写进 market_data.stock_minute。

**为什么缺这一步**：vipdoc 的日线由 C1 落库、板块由 C3 落库，唯独分钟线此前
只落到本地文件 —— `readiness.minute_ready` 的"同步完成"判据到 `.lc1/.lc5`
尾部日期为止，`market_data.stock_minute` 一直是空表，`sync_minute` 作业也没有执行器。
解析原语（`tdx_binary.parse_minute`）和 UPSERT（`repo.quote.upsert_minute`）
早就写好了，缺的只是把两者装配成一个入库脚本，这一步补上。

**按日增量，不灌全史**：一个 `.lc1` 里存着几个月、每票每天 240 根，全量重扫
是上千万行。默认只摄取目标交易日当天（`--date`）；要回填更早的区间用 `--since`。

单位口径：vipdoc 分钟线的 vol 是「股」、amount 是「元」，与 `.day` 同源
（见 tdx_binary 模块 docstring 的实测校验），直接入库不做 ×100 换算。

用法（一律 `python -m`；本项目没有 `scripts/ingest/` 这套目录）：
    python -m ingest.jobs.c16_minute --date 2026-09-22 --dry-run
    python -m ingest.jobs.c16_minute --date 2026-09-22
    python -m ingest.jobs.c16_minute --since 2026-09-01 --period 5
    python -m ingest.jobs.c16_minute --date 2026-09-22 --overwrite
"""
from __future__ import annotations

import argparse
import datetime as dt
import signal
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402
from app.ingest import tdx_binary as tb  # noqa: E402

CHUNK = 200_000
STAGING = 'market_data.staging_c16_minute'
COLUMNS = ('security_code', 'dt', 'period', 'open', 'high', 'low', 'close',
           'volume_shares', 'amount_yuan', 'source_batch_id')

# 分钟线时间戳是本地交易时间（A 股 09:30–15:00）。库列是 timestamptz，
# 连接会话的 timezone 已设为 Asia/Shanghai，但显式带 tzinfo 更稳妥，
# 不依赖会话设置。
_TZ = ZoneInfo(config.TIMEZONE)


def create_staging() -> None:
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {STAGING}')
        cur.execute(f'CREATE UNLOGGED TABLE {STAGING} ('
                    'security_code text, dt timestamptz, period smallint, '
                    'open numeric(12,4), high numeric(12,4), '
                    'low numeric(12,4), close numeric(12,4), '
                    'volume_shares bigint, amount_yuan numeric(20,2), '
                    'source_batch_id bigint)')


def drop_staging() -> None:
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {STAGING}')


def merge(*, overwrite: bool) -> int:
    collist = ', '.join(COLUMNS)
    # ck_minute_ohlc：low<=high。坏记录（解析错位）在合并时挡掉，不炸批次。
    where = ['low IS NULL OR high IS NULL OR low <= high']
    if not overwrite:
        where.append('NOT EXISTS (SELECT 1 FROM market_data.stock_minute t '
                     'WHERE t.security_code = s.security_code AND t.dt = s.dt '
                     'AND t.period = s.period)')
    tail = ('ON CONFLICT (security_code, dt, period) DO UPDATE SET '
            'open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, '
            'close = EXCLUDED.close, volume_shares = EXCLUDED.volume_shares, '
            'amount_yuan = EXCLUDED.amount_yuan, '
            'source_batch_id = EXCLUDED.source_batch_id'
            if overwrite else
            'ON CONFLICT (security_code, dt, period) DO NOTHING')
    return engine.execute(
        f"""
        INSERT INTO market_data.stock_minute ({collist})
        SELECT DISTINCT ON (security_code, dt, period) {collist}
          FROM {STAGING} s
         WHERE {' AND '.join(where)}
         ORDER BY security_code, dt, period
        {tail}
        """, role='admin')


#: 一天算"已经齐了"的最低根数：1 分钟 240 根 / 5 分钟 48 根，留停牌与半日停市的余量。
#: 低于这个数的日子按"没齐"处理，还会重解重写（合并是 upsert/只补空洞，重写无害）。
FULL_DAY_BARS = {1: 200, 5: 40}
#: 每多少个文件打一行进度。9557 个文件一句话不说，被 `timeout` 抢杀掉的人
#: 根本分不清它是在跑、是卡、还是已经悄悄跑完了（2026-09-26 就是这么误判过一次）。
PROGRESS_EVERY = 2000


def landed_codes(period: int, since: dt.date, until: dt.date | None) -> set[str]:
    """**已经齐了一天的代码**，重跑时整文件跳过 —— 断点续跑靠这个。

    不加这条的话，中断后重跑永远从第一个文件重新解一遍：`minute_window` 省的是
    "一个文件解几个月"，省不掉"9557 个文件各解一次"。实测 09-23 全市场扫描 77.8 秒
    只是**读**，被中断后重跑真正贵的是把已入库的那 9 千只再解一遍。

    判据用当日**根数**而不是"有没有行"：只进了一半的日子必须重来。
    """
    hi = (until or since) + dt.timedelta(days=1)
    rows = engine.query(
        """SELECT security_code FROM market_data.stock_minute
            WHERE period = %s AND dt >= %s AND dt < %s
            GROUP BY security_code HAVING count(*) >= %s""",
        (period, since, hi, FULL_DAY_BARS.get(period, 200)), role='admin') or []
    return {r['security_code'] for r in rows}


def scan(periods: tuple[int, ...], markets: tuple[str, ...], batch_id: int, *,
         since: dt.date | None, until: dt.date | None, dry_run: bool,
         overwrite: bool = False) -> dict:
    stat = {f'bars_p{p}': 0 for p in periods}
    stat.update(files=0, skipped_files=0, bad_ohlc=0, already_in_files=0)
    buf: list[tuple] = []
    t0 = time.time()

    def flush() -> None:
        if not buf or dry_run:
            buf.clear()
            return
        engine.copy_rows(STAGING, list(COLUMNS), iter(buf), role='admin')
        buf.clear()

    for period in periods:
        # 断点续跑的起点。dry-run 不跳（要让人看见"这场一共多少行待写"），
        # --overwrite 也不跳（那就是要重解重写）。
        landed = set() if (dry_run or overwrite or not since) else landed_codes(period, since, until)
        paths = list(tb.iter_minute_files(config.VIPDOC_DIR, period, markets))
        total = len(paths)
        for i, path in enumerate(paths, 1):
            ts = tb.ts_code_of(path)
            if not ts:
                stat['skipped_files'] += 1
                continue
            if ts in landed:
                stat['already_in_files'] += 1
                continue
            stat['files'] += 1
            n = 0
            # 有下界时走"尾部反向定位"只解目标窗口（`parse_minute` 是 O(整个文件)，
            # 一个 .lc1 存几个月、每票每天 240 根，`--date` 一天照样全解一遍）。
            # 探到 None = 这文件布局/顺序不认识 → 老老实实回退全量解析，
            # 宁可慢也不能少数据；回退了多少个文件照实报。
            bars = tb.minute_window(path, since, until, security_code=ts) if since else None
            if bars is None:
                if since:
                    stat['fallback_files'] = stat.get('fallback_files', 0) + 1
                bars = list(tb.parse_minute(path, security_code=ts))
            for b in bars:
                d = b.dt.date()
                if since and d < since:
                    continue
                if until and d > until:
                    continue
                if b.low is not None and b.high is not None and b.low > b.high:
                    stat['bad_ohlc'] += 1
                    continue
                buf.append((b.security_code, b.dt.replace(tzinfo=_TZ), period,
                            b.open, b.high, b.low, b.close,
                            b.vol, b.amount, batch_id))
                n += 1
                if len(buf) >= CHUNK:
                    flush()
            stat[f'bars_p{period}'] += n
            if i % PROGRESS_EVERY == 0 or i == total:
                # 一行进度：跑到第几个文件、攒了多少行、跳过多少已入库的。
                # 被打断的人看这一行就知道它当时在跑还是已经跑完。
                print('  [进度] period=%-2s 文件 %s/%s  已攒 %s 行  跳过已入库 %s 个  %.1fs'
                      % (period, f'{i:,}', f'{total:,}', f'{stat[f"bars_p{period}"]:,}',
                         f'{stat["already_in_files"]:,}', time.time() - t0), flush=True)
    flush()
    stat['seconds'] = round(time.time() - t0, 1)
    return stat


def _on_term(signo, _frame):
    """`kill`/`SIGTERM` 也当"被中断"处理，别让半截跑成一条绿灯。

    说明白边界：**硬杀（TerminateProcess / `taskkill /F`）拦不住**。
    那种情况下这条批次会永远停在 `running`，而"批次没走完"本身就是可读的信号；
    数据侧的兜底是 `duanxian/coverage.py` 的只数占比 —— 半截日子会被报成 partial。
    """
    raise KeyboardInterrupt('收到终止信号 %s' % signo)


def main() -> int:
    ap = argparse.ArgumentParser(
        description='C16 · 通达信 vipdoc 分钟线入库（market_data.stock_minute）')
    ap.add_argument('--date', help='只摄取这一天（YYYY-MM-DD）。默认取目标交易日')
    ap.add_argument('--since', help='摄取这一天及之后（YYYY-MM-DD），与 --date 互斥')
    ap.add_argument('--period', action='append', type=int, choices=[1, 5],
                    help='只摄取指定周期，可重复；默认 1 和 5 都要')
    ap.add_argument('--market', action='append', choices=['sh', 'sz', 'bj'])
    ap.add_argument('--overwrite', action='store_true',
                    help='用 vipdoc 覆盖已有分钟线（默认只补空洞）')
    ap.add_argument('--full', action='store_true',
                    help='不限日期，摄取 vipdoc 里全部历史（慎用，量极大）')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    periods = tuple(args.period) if args.period else (1, 5)
    markets = tuple(args.market) if args.market else ('sh', 'sz', 'bj')

    if args.full:
        since = until = None
        span = '全部历史'
    elif args.since and args.date:
        print('--since 与 --date 不能同时给')
        return 2
    elif args.since:
        since = dt.date.fromisoformat(args.since)
        until = None
        span = f'{since} 起'
    else:
        from app.tdx_client import readiness
        since = dt.date.fromisoformat(args.date) if args.date \
            else readiness.expected_trade_date()
        until = since
        span = str(since)

    if not config.VIPDOC_DIR.is_dir():
        print('vipdoc 目录不存在：%s' % config.VIPDOC_DIR)
        return 1

    print('=== C16 分钟线入库 · 周期 %s · 市场 %s · 区间 %s%s ==='
          % ('/'.join(map(str, periods)), '/'.join(markets), span,
             '（--dry-run）' if args.dry_run else ''))

    batch_id = 0
    if not args.dry_run:
        batch_id = ops_repo.begin_batch('tdx_vipdoc_minute', 'market_data.stock_minute',
                                        source_path=str(config.VIPDOC_DIR))
        print('批次 %s' % batch_id)
        create_staging()
        try:
            signal.signal(signal.SIGTERM, _on_term)
        except (ValueError, OSError):      # 非主线程 / 平台不支持时不硬撑
            pass

    try:
        st = scan(periods, markets, batch_id, since=since, until=until,
                  dry_run=args.dry_run, overwrite=args.overwrite)
        for k, v in st.items():
            if v:
                print('  %-16s %s' % (k, f'{v:,}' if isinstance(v, int) else v))

        if args.dry_run:
            print('\n--dry-run，未写库')
            return 0

        print('\n=== 合并进 market_data.stock_minute（%s） ==='
              % ('覆盖模式' if args.overwrite else '只补空洞'))
        t0 = time.time()
        n = merge(overwrite=args.overwrite)
        print('  market_data.stock_minute  %s 行  %.1fs' % (f'{n:,}', time.time() - t0))
        ops_repo.end_batch(batch_id, status='ok', row_count=n)
    except (KeyboardInterrupt, SystemExit) as exc:
        # **被抢杀不算成功**。`timeout` / Ctrl-C / SIGTERM 走的是 BaseException，
        # 原来那个 `except Exception` 接不住 —— 于是进程明明只跑到一半，
        # 批次记录却永远停在 running，页面上看不见失败，谁都会把半截读成完成。
        if not args.dry_run:
            ops_repo.end_batch(batch_id, status='failed',
                               message='中断（%s）：本次未完成，未合并的暂存已丢弃' % type(exc).__name__)
        print('\n**中断**（%s）：本次**没有**当成成功 —— 批次已记 failed，'
              '已入库的部分保持原样，重跑会从缺的那一天继续（已齐整日的文件会被跳过）'
              % type(exc).__name__)
        return 130
    except Exception as exc:
        if not args.dry_run:
            ops_repo.end_batch(batch_id, status='failed', message=str(exc)[:2000])
        raise
    finally:
        if not args.dry_run:
            drop_staging()

    print('\n=== 验收 ===')
    # ⚠️ **这一屏不数全表**。`stock_minute` 是按日期分区（49 个分区），
    # `SELECT count(*) FROM market_data.stock_minute` 会递归扫全部分区，
    # 2026-10-15 实测被 60 秒 `statement_timeout` 取消 —— 而那时 merge 早已提交、
    # 批次已记 ok，脚本却因为一句"给人看的总数"回 1，把一次成功的入库报成失败
    # （链尾看到的是"这一步没成"，谁都会去重跑已经跑完的东西）。
    # 总行数要看得个大概就用目录估算：`pg_class.reltuples` + `pg_inherits` 求和（0 秒）。
    lo_hi = engine.query('SELECT min(dt) lo, max(dt) hi FROM market_data.stock_minute')
    _lo, _hi = (lo_hi[0]['lo'], lo_hi[0]['hi']) if lo_hi else (None, None)
    print('  历史跨度              %s' % ('%s .. %s' % (_lo, _hi) if _lo else '—（库里没有任何行）'))
    if not args.full:
        # **逐日只数**才是这一场的验收判据：总行数会被量级掩盖、max(dt) 会被半截数据骗过
        # （09-23 曾只有北交所 351 只 / 83,523 行，日期看着"到了"）。每条查询只查一天，
        # 一天约 17~20 秒（2026-10-15 实测），不会撞 60 秒上限。
        # 右边界是**开区间**，而 `--date` 会把 until 定成同一天（含当天），所以要 +1 天，
        # 否则 `dt < '2026-10-15'` 把目标日整个排除在外（实测第一版就是这样读出"覆盖到的交易日：无"）。
        _hi = (until if until is not None else dt.date.today()) + dt.timedelta(days=1)
        days = [r['d'] for r in engine.query(
            'SELECT DISTINCT dt::date d FROM market_data.stock_minute '
            'WHERE dt >= %s AND dt < %s ORDER BY 1',
            (since.isoformat(), _hi.isoformat()))]
        print('  本次区间 %s 起，覆盖到的交易日：%s' % (since, '、'.join(map(str, days)) or '无'))
        for d in days:
            per = engine.query(
                """SELECT period, count(*) n, count(DISTINCT security_code) codes
                     FROM market_data.stock_minute WHERE dt::date = %s
                    GROUP BY period ORDER BY period""", (d.isoformat(),))
            print('  %s  %s 行 / %s 只%s' % (
                d, f"{sum(r['n'] for r in per):,}",
                f"{max((r['codes'] for r in per), default=0):,}",
                '（' + '、'.join('period=%s %s 行' % (r['period'], f"{r['n']:,}") for r in per) + '）'
                if per else '（这一日没有任何分钟线）'))

    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
