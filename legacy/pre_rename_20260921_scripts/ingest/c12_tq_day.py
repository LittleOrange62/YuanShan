# -*- coding: utf-8 -*-
"""
C12 · 通过 TQ 本地接口从**已登录的通达信客户端**取日线入库。

**这是通达信的第二条通道，不是网络兜底。** 数据源优先级因此变成：

    1. 通达信 vipdoc 本地文件（`c1_day.py`）—— 精度最高的主源
    2. 通达信 TQ 接口（本脚本）—— 同一个客户端、同一份行情，无 GUI
    3. 网络源（新浪 akshare，`c10_catchup.py --allow-web`）—— 兜底，默认关

**为什么需要第 2 条**：vipdoc 只有在客户端跑完"盘后数据下载"之后才会写盘，
而那个下载入口挂在自绘菜单上，`GetMenu` 返回 0、UIA 也看不到菜单项，
自动点不了（详见 `app/tdx_client/tq_bridge.py` 的模块说明）。
2026-08-29 实测：vipdoc 停在 08-27，而 TQ 接口已经能取到 08-28 的完整日线。
于是"菜单点不开"就不再等于"拿不到通达信数据"。

**口径实测对齐（2026-08-29）**：拿 TQ 的 08-26/08-27 与刚从 vipdoc 覆盖入库的
同日行逐字段比：

    600519.SH 08-26  OHLC 完全相同；Volume 2,173,083 完全相同
                     Amount 283876.41 万 → 2,838,764,100 vs vipdoc 2,838,764,032
    000001.SZ 08-26  Volume 116,728,880 vs 116,728,878

差异是 **1e-8 量级**，来自 vipdoc `.day` 用 float32 存 amount/vol；
两边同源无疑。**Amount 的单位是万元，必须 ×10000**，
这一条错了就是成交额差 10000 倍，`md.trg_daily_sanity` 会当场拦下。

**没有的字段不猜**：TQ 的日线里没有换手率、没有涨跌停价，
`VolInStock`（流通股）实测恒为 0 —— 所以这三样照旧交给
`recompute_turnover` / `recompute_limits` 按 `ref.share_capital` 和板块规则回算，
不从这里编。

用法：
    python scripts\\ingest\\c12_tq_day.py --date 2026-08-28
    python scripts\\ingest\\c12_tq_day.py --date 2026-08-28 --dry-run
    python scripts\\ingest\\c12_tq_day.py --start 2026-08-26 --end 2026-08-28
    python scripts\\ingest\\c12_tq_day.py --date 2026-08-28 --no-index --no-sector
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402
from app.db.repo import quote as quote_repo  # noqa: E402
from app.tdx_client import tq_bridge  # noqa: E402

# 一次问客户端多少只。tqcenter 内部会按 stock_page_index 自动分页，
# 但一次塞太多的话超时（60s）就整批丢；400 只实测稳。
CHUNK = 400

# Amount 是万元。这个常量单独拎出来，是因为它是整条链路上最容易错的一个数：
# 错了不是报错而是成交额差 10000 倍，图上看不出来，只有触发器能抓到。
# 个股/指数/板块三种都是万元 —— 拿库里 vipdoc 写的行逐个核对过：
#   000001.SH 08-27  TQ 101,022,656 万 → 1,010,226,560,000
#                    库里（vipdoc）    1,010,226,561,024
#   TDX_880001 08-27 TQ 214,096,224 万 → 2,140,962,240,000
#                    库里（vipdoc）    2,140,962,291,712
AMOUNT_SCALE = 10_000

# 取数窗口往前多留几天：指数/板块的 pre_close 要从同一条序列里的上一根拿，
# 拿库里的收盘去跟 TQ 的收盘比更容易踩到口径差。
LOOKBACK_DAYS = 10



def universe() -> list[str]:
    """
    要取的代码：在市股票 ∪ 最后一个已有交易日出现过的代码。

    **按 asset_type='stock' 过滤，不按代码正则** —— 正则会把上证综指
    000001.SH 当成股票，写库时被 `md.trg_daily_sanity` 拦下
    （指数的 vol/amount 口径不同，均价算出来只有 17 块而"价格"是 3900 点）。
    """
    rows = engine.query(
        """
        SELECT s.ts_code FROM ref.security s
         WHERE s.is_active AND s.asset_type = 'stock'
        UNION
        SELECT d.ts_code FROM md.stock_daily d
         WHERE d.trade_date = (SELECT max(trade_date) FROM md.stock_daily)
        """)
    return sorted(r['ts_code'] for r in rows)


# 子进程里跑的取数逻辑。写成模板字符串是因为它必须在通达信的
# PYPlugins\user 目录下、由 tq_bridge 起的子进程里执行。
# **进度是 print 出来的**：全市场十几个批次要跑几分钟，没有进度回显
# 从外面看就是卡死，之前就因为这个被当成挂了手动 kill 过。
_FETCH = '''
import json as _json
import os

codes = %(codes)r
start, end = %(start)r, %(end)r
chunk = %(chunk)d
path = os.path.join(OUT_DIR, %(fname)r)

n_rows = 0
failed = []
FIELDS = ('Open', 'High', 'Low', 'Close', 'Volume', 'Amount')

with open(path, 'w', encoding='utf-8') as fh:
    for i in range(0, len(codes), chunk):
        part = codes[i:i + chunk]
        try:
            d = tq.get_market_data(stock_list=part, period='1d',
                                   start_time=start, end_time=end,
                                   dividend_type='none')
        except Exception as exc:
            failed.extend(part)
            print('  批次 %%s-%%s 取数失败：%%s' %% (i, i + len(part), exc), flush=True)
            continue
        if not isinstance(d, dict) or 'Close' not in d:
            failed.extend(part)
            print('  批次 %%s-%%s 返回异常：%%s' %% (i, i + len(part), str(d)[:120]),
                  flush=True)
            continue
        close = d['Close']
        for day in close.index:
            day_str = str(day)[:10]
            for code in close.columns:
                row = {'ts_code': code, 'trade_date': day_str}
                bad = False
                for f in FIELDS:
                    try:
                        v = d[f].loc[day, code]
                    except Exception:
                        bad = True
                        break
                    # NaN != NaN：客户端对停牌/未上市的日子返回 NaN，
                    # 这些日子本来就不该有行，跳过而不是写 0。
                    if v != v:
                        bad = True
                        break
                    row[f] = float(v)
                if bad:
                    continue
                fh.write(_json.dumps(row, ensure_ascii=False) + chr(10))
                n_rows += 1
        print('  %%s/%%s 只，已写 %%s 行' %% (min(i + chunk, len(codes)),
                                          len(codes), n_rows), flush=True)

emit({'rows_file': path, 'n_rows': n_rows, 'failed': failed})
'''


def fetch(codes: list[str], start: dt.date, end: dt.date, chunk: int,
          fname: str = 'tq_day_rows.ndjson') -> dict:
    body = _FETCH % {'codes': codes, 'chunk': chunk, 'fname': fname,
                     'start': start.strftime('%Y%m%d'),
                     'end': end.strftime('%Y%m%d')}
    # stream=True：进度实时回显。超时给足 —— 5500 只 × 3 天实测几分钟。
    return tq_bridge.run_snippet(body, timeout=3600, stream=True)


def load_series(path: Path) -> dict[str, list[dict]]:
    """NDJSON → {代码: 按日期排好序的记录列表}。给需要算 pre_close 的品种用。"""
    out: dict[str, list[dict]] = {}
    with path.open(encoding='utf-8') as fh:
        for line in fh:
            r = json.loads(line)
            out.setdefault(r['ts_code'], []).append(r)
    for rows in out.values():
        rows.sort(key=lambda r: r['trade_date'])
    return out



def build_rows(path: Path, want: set[dt.date], batch_id: int) -> list[tuple]:
    """
    NDJSON → `md.stock_daily` 的 16 列元组。

    pre_close/涨跌幅**不在这里算**：库里前一天的收盘价才是权威 pre_close，
    交给 `recompute_pre_close(force=True)` 统一回填，省得同一列出现两种算法。
    """
    rows: list[tuple] = []
    with path.open(encoding='utf-8') as fh:
        for line in fh:
            r = json.loads(line)
            day = dt.date.fromisoformat(r['trade_date'])
            if day not in want:
                continue
            close = r.get('Close')
            vol = r.get('Volume')
            amt = r.get('Amount')
            if not close or close <= 0 or not vol or vol <= 0:
                continue
            rows.append((
                r['ts_code'], day, r.get('Open'), r.get('High'), r.get('Low'),
                close,
                None, None, None,            # pre_close / change / pct 由回算统一填
                int(vol), round(amt * AMOUNT_SCALE, 2),
                None,                        # 换手率靠 recompute_turnover
                None, None,                  # 涨跌停靠 recompute_limits
                1.0,                         # 最新日之后没有除权事件，后复权因子恒 1
                batch_id))
    return rows


def flush(buf: list[tuple]) -> tuple[int, int]:
    """
    批量写；整批被拒时退回逐行写。

    `md.trg_daily_sanity` 是 RAISE 型 BEFORE INSERT 触发器，一行不合口径
    整个 executemany 都会 abort（psycopg pipeline 直接 INERROR）。
    前面几千只票的成果不该被一只坏票带走。
    """
    if not buf:
        return 0, 0
    try:
        return quote_repo.upsert_daily(buf) or len(buf), 0
    except Exception as exc:
        print('    整批被拒，改逐行写：%s' % str(exc).split(chr(10))[0][:140],
              flush=True)
    ok = bad = 0
    for row in buf:
        try:
            quote_repo.upsert_daily([row])
            ok += 1
        except Exception:
            bad += 1
    return ok, bad


# ---------------------------------------------------------------- 指数

def index_codes() -> list[str]:
    """库里已有的指数。只补已有品种，不擅自扩表。"""
    return [r['ts_code'] for r in engine.query(
        'SELECT DISTINCT ts_code FROM md.index_daily ORDER BY ts_code')]


def build_index_rows(series: dict[str, list[dict]], want: set[dt.date],
                     batch_id: int) -> list[tuple]:
    """
    指数日线。**pre_close 取同一条 TQ 序列里的上一根**，不取库里的收盘 ——
    同源前后相邻两根比"库里的收盘 vs TQ 的收盘"更不容易踩到口径差。
    """
    rows: list[tuple] = []
    for code, bars in series.items():
        for i, b in enumerate(bars):
            day = dt.date.fromisoformat(b['trade_date'])
            if day not in want:
                continue
            close = b.get('Close')
            if not close or close <= 0:
                continue
            prev = bars[i - 1].get('Close') if i else None
            pct = (round((close - prev) / prev * 100, 4)
                   if prev and prev > 0 else None)
            rows.append((code, day, b.get('Open'), b.get('High'), b.get('Low'),
                         close, prev, pct, int(b.get('Volume') or 0),
                         round((b.get('Amount') or 0) * AMOUNT_SCALE, 2),
                         batch_id))
    return rows


# ---------------------------------------------------------------- 板块

def sector_pairs() -> dict[str, str]:
    """
    TQ 用的代码 → `ref.sector_info.sector_code`。

    通达信的板块指数就是 `sh88xxxx`，TQ 那边写成 `88xxxx.SH`。
    库里的主键带前缀（`TDX_880501` / `GN_880501` / `FG_880081`），所以按后 6 位对回去。
    **只取 88 开头的**：其余前缀的板块在 vipdoc 里没有对应的指数文件，
    通达信也不发布它们的指数，硬取只会拿到空值。

    **后 6 位会撞车，必须挑出唯一的那个主键**（2026-08-29 踩过）：
    命中正则的 1,080 个主键只有 653 个不同的 6 位码 —— `含H股` 同时以
    `TDX_880501`（is_active）和 `GN_880501`（已停用的旧分类镜像）存在。
    第一版这里直接 `out[key] = sector_code`，等于让查询顺序决定胜者，
    结果 08-28 有 313 个板块写到了 GN_/FG_ 上：那天 TDX_ 只有 339 行
    （历史每天都是 649 行），既断了序列又凭空多出一批孤儿行，
    `recompute_sector_counts` 也只能匹配上 109 行。

    所以按 **TDX_ 优先 → is_active 优先 → 主键字典序** 取 `DISTINCT ON`，
    和 `c3_sector.py` 从 vipdoc 写进来的主键保持一致。
    """
    return {right6(r['sector_code']) + '.SH': r['sector_code']
            for r in engine.query(
                """
                SELECT DISTINCT ON (right(sector_code, 6)) sector_code
                  FROM ref.sector_info
                 WHERE right(sector_code, 6) ~ '^88[01][0-9]{3}$'
                 ORDER BY right(sector_code, 6),
                          (sector_code LIKE 'TDX\\_%') DESC, is_active DESC,
                          sector_code
                """)}



def right6(s: str) -> str:
    return s[-6:]


def build_sector_rows(series: dict[str, list[dict]], mapping: dict[str, str],
                      want: set[dt.date], batch_id: int) -> list[tuple]:
    """
    板块日线，**通达信板块指数原始口径**（不是成分股聚合）。

    家数三列传 None：它们由 `recompute_sector_counts` 从成员股回算，
    这里塞值等于把刚算好的家数抹掉 —— c3_sector 的覆盖模式踩过这个坑。
    """
    rows: list[tuple] = []
    for code, bars in series.items():
        sector_code = mapping.get(code)
        if not sector_code:
            continue
        for i, b in enumerate(bars):
            day = dt.date.fromisoformat(b['trade_date'])
            if day not in want:
                continue
            close = b.get('Close')
            if not close or close <= 0:
                continue
            prev = bars[i - 1].get('Close') if i else None
            pct = (round((close - prev) / prev * 100, 4)
                   if prev and prev > 0 else None)
            rows.append((sector_code, day, b.get('Open'), b.get('High'),
                         b.get('Low'), close, prev, pct,
                         int(b.get('Volume') or 0),
                         round((b.get('Amount') or 0) * AMOUNT_SCALE, 2),
                         None, None, None, batch_id))
    return rows



def ingest_extra(codes: list[str], *, target: str, fname: str,
                 build, upsert, start: dt.date, end: dt.date,
                 want: set[dt.date], chunk: int, dry_run: bool) -> int:
    """
    指数和板块共用的一段流程：取数 → 建行 → 整批写。

    和个股那条路不同的地方只有两点：**取数窗口往前多留 LOOKBACK_DAYS 天**
    （pre_close 要从同一条 TQ 序列的上一根拿），以及这两张表都没有触发器
    级的口径校验，所以不需要 `flush()` 那种逐行退化。
    """
    if not codes:
        print('  库里没有对应品种，跳过')
        return 0
    batch_id = 0
    if not dry_run:
        batch_id = ops_repo.begin_batch('tdx_tq_api', target,
                                        source_path=str(config.TDX_DIR))
        print('  批次 %s，%s 个代码' % (batch_id, len(codes)))
    t0 = time.time()
    res = fetch(codes, start - dt.timedelta(days=LOOKBACK_DAYS), end,
                chunk, fname=fname)
    if not res.get('ok'):
        print('  取数失败：%s' % res.get('error'))
        if batch_id:
            ops_repo.end_batch(batch_id, status='failed',
                               message=str(res.get('error'))[:500])
        return 0
    rows_file = Path(res['rows_file'])
    try:
        series = load_series(rows_file)
        rows = build(series, want, batch_id)
        print('  取到 %s 行，目标日期内 %s 行，用时 %.0fs'
              % (f"{res.get('n_rows', 0):,}", f'{len(rows):,}', time.time() - t0))
        if dry_run:
            for r in rows[:3]:
                print('    %s' % (r,))
            return 0
        n = upsert(rows) or len(rows)
        print('  写入 %s 行' % f'{n:,}')
        ops_repo.end_batch(batch_id, row_count=n)
        return n
    except Exception as exc:
        if batch_id:
            ops_repo.end_batch(batch_id, status='failed', message=str(exc)[:500])
        raise
    finally:
        rows_file.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='只取这一天 YYYY-MM-DD')
    ap.add_argument('--start', help='起始日 YYYY-MM-DD')
    ap.add_argument('--end', help='结束日 YYYY-MM-DD')
    ap.add_argument('--chunk', type=int, default=CHUNK)
    ap.add_argument('--limit', type=int, help='只取前 N 只（自检用）')
    ap.add_argument('--dry-run', action='store_true', help='取数但不写库')
    ap.add_argument('--no-index', action='store_true', help='跳过指数')
    ap.add_argument('--no-sector', action='store_true', help='跳过板块')
    args = ap.parse_args()

    if args.date:
        start = end = dt.date.fromisoformat(args.date)
    elif args.start and args.end:
        start = dt.date.fromisoformat(args.start)
        end = dt.date.fromisoformat(args.end)
    else:
        print('要么给 --date，要么给 --start 和 --end')
        return 2
    want = {start + dt.timedelta(days=i) for i in range((end - start).days + 1)}

    ok, why = tq_bridge.available()
    print('=== 0. TQ 接口 ===')
    print('  %s —— %s' % ('可用' if ok else '**不可用**', why))
    if not ok:
        print('  先把通达信启动并登录：python -m app.tdx_client.run_agent '
              '--only s1_launch --only s2_login')
        return 2

    codes = universe()
    if args.limit:
        codes = codes[:args.limit]
    print('=== 1. 取数 %s .. %s，共 %s 只 ===' % (start, end, len(codes)))

    batch_id = 0
    if not args.dry_run:
        batch_id = ops_repo.begin_batch('tdx_tq_api', 'md.stock_daily',
                                        source_path=str(config.TDX_DIR))
        print('  批次 %s' % batch_id)

    t0 = time.time()
    res = fetch(codes, start, end, args.chunk)
    if not res.get('ok'):
        print('  取数失败：%s' % res.get('error'))
        if batch_id:
            ops_repo.end_batch(batch_id, status='failed',
                               message=str(res.get('error'))[:500])
        return 1
    rows_file = Path(res['rows_file'])
    print('  取到 %s 行，失败 %s 只，用时 %.0fs'
          % (f"{res.get('n_rows', 0):,}", len(res.get('failed') or []),
             time.time() - t0))

    try:
        print('=== 2. 个股入库 ===')
        rows = build_rows(rows_file, want, batch_id)
        print('  目标日期内 %s 行' % f'{len(rows):,}')
        if args.dry_run:
            for r in rows[:5]:
                print('    %s' % (r,))
        else:
            total = bad = 0
            for i in range(0, len(rows), 2000):
                n, b = flush(rows[i:i + 2000])
                total += n
                bad += b
            print('  写入 %s 行，被口径校验拒掉 %s 行' % (f'{total:,}', bad))
            ops_repo.end_batch(batch_id, row_count=total)
    except Exception as exc:
        if batch_id:
            ops_repo.end_batch(batch_id, status='failed', message=str(exc)[:500])
        raise
    finally:
        rows_file.unlink(missing_ok=True)

    # **派生列必须在板块之前回算。** 板块的涨跌家数是从
    # `md.stock_daily.pct_chg` 数出来的，而上面那次 upsert 把 pct_chg 写成了
    # NULL（等回算）。2026-08-29 第一版把这段放在板块之后，结果 08-28 的 418 个
    # 板块 up_count/down_count 全是 0，`sector_guangdu_v1`（板块广度）
    # 一个值都算不出来、权重被静默摊到其他三个分项上 —— 图上看不出异常。
    if not args.dry_run:
        print('=== 3. 回算个股派生列 ===')
        for d in sorted(want):
            pre = quote_repo.recompute_pre_close(d, role='admin', force=True)
            lim = quote_repo.recompute_limits(d, role='admin', only_missing=False)
            turn = quote_repo.recompute_turnover(d, only_missing=False,
                                                 role='admin')
            print('  %s  前收/涨跌幅 %s 行  涨跌停 %s 行  换手率 %s 行'
                  % (d, pre, lim, turn))

    if not args.no_index:
        print('=== 4. 指数 ===')
        ingest_extra(index_codes(), target='md.index_daily',
                     fname='tq_index_rows.ndjson', build=build_index_rows,
                     upsert=quote_repo.upsert_index_daily,
                     start=start, end=end, want=want, chunk=args.chunk,
                     dry_run=args.dry_run)

    if not args.no_sector:
        print('=== 5. 板块 ===')
        mapping = sector_pairs()
        n = ingest_extra(sorted(mapping), target='md.sector_daily',
                         fname='tq_sector_rows.ndjson',
                         build=lambda s, w, b: build_sector_rows(s, mapping, w, b),
                         upsert=quote_repo.upsert_sector_daily,
                         start=start, end=end, want=want, chunk=args.chunk,
                         dry_run=args.dry_run)
        if n and not args.dry_run:
            # 家数三列在 build_sector_rows 里传的是 None，写完必须马上回算，
            # 否则库里就是"有指数没广度"的半成品，板块热度那一维直接失效。
            cnt = quote_repo.recompute_sector_counts(start, end)
            print('  回算涨跌家数 %s 行' % f'{cnt:,}')

    if args.dry_run:
        print('\nDONE（dry-run 未写库）')
        return 0

    print('=== 6. 验收 ===')
    for r in engine.query(
            "SELECT trade_date, source, row_count FROM md.v_daily_source "
            "WHERE trade_date >= %s ORDER BY trade_date DESC, row_count DESC",
            (start - dt.timedelta(days=4),)):
        print('  %s  %-12s %s 行' % (r['trade_date'], r['source'],
                                     f"{r['row_count']:,}"))
    for r in engine.query(
            'SELECT %s AS name, max(trade_date) AS d FROM md.index_daily '
            'UNION ALL SELECT %s, max(trade_date) FROM md.sector_daily '
            'UNION ALL SELECT %s, max(trade_date) FROM md.stock_daily',
            ('md.index_daily', 'md.sector_daily', 'md.stock_daily')):
        print('  %-16s 最新 %s' % (r['name'], r['d']))

    print('\n补完后还要跑：'
          '\n  python scripts\\compute\\d1_indicators.py --recent %s'
          '\n  python scripts\\compute\\d2_scores.py --recent %s'
          % (len(want), len(want)))
    print('\nDONE')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
