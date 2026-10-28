# -*- coding: utf-8 -*-
"""
C15 · 把通达信收盘快照库搬进 `md.stock_snapshot`（顺带修 `md.stock_daily` 的涨跌停价）。

**数据从哪来**：`D:\\new_tdx64\\daily_close_data.db`（SQLite，97.8 MB）。
它不是通达信自带的，是本机通达信客户端里的 Python 插件
`PYPlugins\\user\\Get_DayEnd.py`（`py_strategy.cfg` 里 AutoRun=1）每天收盘后
调 `tq.get_market_snapshot` + `tq.get_more_info` 落下来的，
**每天一张表 `day_end_YYYYMMDD`、每行一只票、115 个字段**。
实测 2026-07-21 ~ 08-31 共 26 张非空表 × 5,563 行。

**为什么值得搬**：这里面有本系统别处拿不到的东西 ——

    ZTPrice / DTPrice   涨跌停价（**别当权威**，见下）
    Zsz / Ltsz          总市值 / 流通市值（亿元）← 评分链路的市值缺口就是这个
    Inside / Outside    内外盘（主动买 / 主动卖）
    Average / fLianB    均价 / 量比        Wtb 委比
    DynaPE / StaticPE_TTM / PB_MRQ / DYRatio / BetaValue   估值
    EverZTCount / YearZTDay / ZTDate_Recent / TopDate_Recent  涨停与上榜统计
    FreeLtgb            自由流通股         MainBusiness 主营
    ZAFPre2D .. ZAFPreOneYear   多窗口涨跌幅

`md.stock_snapshot` 只有 pe/pb/市值/eps/bps/roe 六个定型列，**剩下的全塞
`extra` jsonb**：这些字段的口径没经过验证（比如 `Zjl` 资金流实测全是 0），
提前给它们各开一列等于把没验证的东西当结论。要用哪个再从 extra 里提。

**⚠️ 表名里的日期是采集日期，不是交易日期**。插件是 AutoRun，什么时候
被拉起来就什么时候采，实测 26 张表里：
    17 张 收盘后采集 → 表名日期就是交易日
     2 张 周末 / 凌晨采集（08-01 17:45、08-02 09:16 → 都是 07-31 的数据；
          08-21 00:58 → 08-20 的数据）→ **要按数据回推交易日**
     7 张 盘中或盘前采集（10:44 / 12:5x / 08:52…）→ `Now` 是当时现价不是
          收盘价，整张表报废，`resolve_trade_date()` 会把它跳掉
所以本脚本不认表名，用 `(Open,Max,Min,Now)` 回 `md.stock_daily` 投票定日期，
命中率低于 80% 的表直接跳过 —— 按表名写库会把 07-31 的市值挂到 08-01
这个根本不存在的交易日上。

**⚠️ 代码是怎么对上的**：快照接口**不返回股票代码**，原脚本也没补，

所以 08-31 之前那 26 张表里**没有代码列**。这里的对法是拿
`(open, high, low, close)` 四元组回 `md.stock_daily` 反查，实测
5,545/5,563 能对上、其中 5,541 是唯一命中（4 个撞价的丢掉，不猜）。
2026-09-01 已经给 `Get_DayEnd.py` 补了 `Code` 列，**以后新表直接走代码**，
本脚本两种都认（有 Code 用 Code，没有才 OHLC 反查）。

**⚠️ 涨跌停价：结论跟我一开始想的相反 —— 库里的多半是对的**。
一开始我以为"通达信给的 ZTPrice 才准"，08-31 实测 324 只与库里不一致，
逐只查完发现三类，只有第三类库里是错的：

    175 只 北交所 920xxx.BJ  两边都用 1.30，差一分钱：
        920932.BJ pre=11.23 → 11.23×1.30 = 14.599
        库 14.60（四舍五入）  通达信 14.59（截断）→ **库里对**
    142 只 库 ±5% vs 通达信 ±10%：600491.SH ST龙元 / 603429.SH *ST集友 /
        000016.SZ *ST康佳A / 000078.SZ ST海王 …
        在 `ref.security` 里核过，这些**确实是 ST**（is_st=True，212 只
        is_st 行名字里全带 ST，0 处标志与名称冲突），主板 ST 就是 ±5%
        → **库里对，通达信给的是没区分 ST 的通用值**
      2 只 000711.SZ / 002586.SZ：库里 pre_close=0、涨跌停 NULL，
        通达信有值 → **只有这类值得补**
      1 只 002703.SZ 浙江世宝（非 ST）：库 18.30(1.100) / 通达信 18.24(1.096)，
        步进口径差，谁对没定论 → 不动

所以默认**只填空**（`limit_up`/`limit_down` IS NULL 的行），不覆盖已有值。
真要按通达信全覆盖得显式 `--overwrite-limits`，那会把上面 317 只本来
正确的值改坏，别用。


用法：
    python scripts\\ingest\\c15_snapshot.py                 # 全部日期
    python scripts\\ingest\\c15_snapshot.py --date 20260831 # 只做一天
    python scripts\\ingest\\c15_snapshot.py --dry-run       # 只看不写
    python scripts\\ingest\\c15_snapshot.py --no-fill-limits    # 连空的也不填
    python scripts\\ingest\\c15_snapshot.py --overwrite-limits  # 别用，见上
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402
from app.db.repo import quote as quote_repo  # noqa: E402

#: 插件落库的位置。跟着通达信目录走，换安装路径不用改代码
DB_PATH = config.TDX_DIR / 'daily_close_data.db'

TABLE_RE = re.compile(r'^day_end_(\d{8})$')

#: 亿元 → 元。Zsz/Ltsz 实测是亿元（000001.SZ 2274.37 ≈ 2.27 万亿）
YI = 100_000_000

#: 定型列的取值来源。**PE 用 StaticPE_TTM 不用 DynaPE**：
#: DynaPE 是动态市盈率（按最近一期年化推），TTM 是滚动四季度，
#: 后者才是 `pe_ttm` 这个列名的意思
FIXED = (('pe_ttm', 'StaticPE_TTM'), ('pb', 'PB_MRQ'))

#: 塞进 extra 的字段。不是"全都塞"——采集脚本把 115 个字段一股脑存了，
#: 其中一片是常年 0（L2 相关的 TotalBVol/BCancel/Zjl…，没买 L2 权限）
#: 或者本系统另有权威来源（Open/Max/Min/Now 走 vipdoc）。只挑有信息量的。
EXTRA_KEYS = (
    'ZTPrice', 'DTPrice',                      # 涨跌停价
    'Inside', 'Outside', 'InOutFlag',          # 内外盘
    'Average', 'Before5MinNow', 'MA5Value',    # 均价 / 尾盘 / 5 日均线
    'fHSL', 'fLianB', 'Wtb', 'Fzhsl', 'FzAmo',  # 换手 / 量比 / 委比 / 封单
    'DynaPE', 'MorePE', 'DYRatio', 'BetaValue',  # 其它估值
    'FreeLtgb', 'Zsz', 'Ltsz',                 # 股本与市值（原始亿元，留档）
    'EverZTCount', 'YearZTDay', 'ZTGPNum', 'LastZTHzNum', 'ConZAFDateNum',
    'ZTDate_Recent', 'DTDate_Recent', 'TopDate_Recent',  # 涨停 / 上榜日期
    'ZAF', 'ZAFYesterday', 'ZAFPre2D', 'ZAFPre5', 'ZAFPre10', 'ZAFPre20',
    'ZAFPre30', 'ZAFPre60', 'ZAFYear', 'ZAFPreOneYear',  # 多窗口涨跌
    'HisHigh', 'HisLow', 'IPO_Price',
    'MainBusiness', 'StaffNum', 'ReportDate', 'SafeValue', 'ShineValue',
    'IsKzz', 'IsT0Fund', 'TPFlag', 'XsFlag',
    '采集日期', '采集时间',
)


def sqlite_tables(con: sqlite3.Connection) -> list[tuple[str, dt.date]]:
    """`day_end_YYYYMMDD` 表名 → (表名, 日期)，只要非空表、按日期升序。"""
    out = []
    for (name,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        m = TABLE_RE.match(name)
        if not m:
            continue
        if not con.execute(f'SELECT 1 FROM [{name}] LIMIT 1').fetchone():
            continue                      # 空表：那天插件跑了但一条没取到
        out.append((name, dt.datetime.strptime(m.group(1), '%Y%m%d').date()))
    return out


#: 按日缓存的 OHLC 反查表。定日期要拿同一天的表投票、写入时又要用一次，
#: 26 张表 × 每张最多试 6 天，不缓存就是上百次全表扫
_OHLC_CACHE: dict[dt.date, dict[tuple, str]] = {}


def ohlc_map(trade_date: dt.date) -> dict[tuple, str]:
    """
    `(open, high, low, close)` → ts_code，**只留唯一命中**。

    撞价的（同一天四个价完全一样的两只票）直接不放进来 —— 让它们变成
    "对不上"比随便挑一只更安全，市值挂到错的票上是无声的错。
    """
    if trade_date in _OHLC_CACHE:
        return _OHLC_CACHE[trade_date]
    hits: dict[tuple, list[str]] = {}
    for r in engine.query(
            'SELECT ts_code, open, high, low, close FROM md.stock_daily '
            ' WHERE trade_date = %s', (trade_date,)):
        key = (float(r['open']), float(r['high']),
               float(r['low']), float(r['close']))
        hits.setdefault(key, []).append(r['ts_code'])
    _OHLC_CACHE[trade_date] = {k: v[0] for k, v in hits.items() if len(v) == 1}
    return _OHLC_CACHE[trade_date]


#: 认定"这张表就是这天的收盘快照"所需的命中率。0.8 是拍的，但两类情况
#: 分得很开：收盘后采集的命中 99.6%（5541/5563），盘中采集的只有 1%~6%，
#: 中间没有样本落在 10%~99% 之间
MATCH_MIN = 0.80

#: 往前找几个交易日。跨周末采集最多差 2 天，凌晨采集差 1 天，给到 5 天足够
LOOKBACK_DAYS = 5


def trading_dates_upto(day: dt.date) -> list[dt.date]:
    """`md.stock_daily` 里 <= day 的最近几个交易日，近的在前。"""
    return [r['trade_date'] for r in engine.query(
        'SELECT DISTINCT trade_date FROM md.stock_daily '
        ' WHERE trade_date <= %s ORDER BY trade_date DESC LIMIT %s',
        (day, LOOKBACK_DAYS))]


def resolve_trade_date(keys: set[tuple], table_day: dt.date
                       ) -> tuple[dt.date | None, float, dt.date | None]:
    """
    从行里的 `(Open, Max, Min, Now)` 反推这张表到底是哪个交易日的收盘。

    **表名里的日期是采集日期，不是交易日期** —— 实测：
        `day_end_20260801` 采集于 08-01 17:45（周六）→ 数据是 07-31 的
        `day_end_20260821` 采集于 08-21 00:58（凌晨）→ 数据是 08-20 的
        `day_end_20260818` 采集于 08-18 08:52（盘前）→ 一天都对不上，是废表
    按表名写库就会把 07-31 的市值挂到 08-01（一个不存在的交易日）上。

    返回 `(交易日或None, 命中率, 命中率最高的那天)`。命中率低于
    `MATCH_MIN` 时第一项是 None —— 那种表是盘中采集的，`Now` 是
    当时的现价不是收盘价，整张表都不能当收盘快照用。
    """
    best, best_hit = None, 0
    for d in trading_dates_upto(table_day):
        hit = sum(1 for k in keys if k in ohlc_map(d))
        if hit > best_hit:
            best, best_hit = d, hit
    ratio = best_hit / max(len(keys), 1)
    return (best if ratio >= MATCH_MIN else None), ratio, best


def normalize_code(raw: object) -> str | None:
    """
    插件存的 `Code` 是 `600519.SH` 这种带后缀的，本库用 `600519.SH` —— 同形。
    但 `clean_value` 可能把纯数字代码转成 int（`1` ← `000001`），
    所以这里只认带后缀的字符串，其它一律当没有、退回 OHLC 反查。
    """
    if isinstance(raw, str) and re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', raw.strip()):
        return raw.strip()
    return None


def read_table(con: sqlite3.Connection, table: str) -> list[dict]:
    cols = [r[1] for r in con.execute(f'PRAGMA table_info([{table}])')]
    return [dict(zip(cols, rec, strict=True))
            for rec in con.execute(f'SELECT * FROM [{table}]')]


def build_rows(recs: list[dict], trade_date: dt.date,
               batch_id: int) -> tuple[list[tuple], dict]:
    cmap = ohlc_map(trade_date)
    stat = {'rows': 0, 'by_code': 0, 'by_ohlc': 0, 'unmatched': 0}
    out: list[tuple] = []
    for d in recs:
        stat['rows'] += 1
        code = normalize_code(d.get('Code'))
        if code:
            stat['by_code'] += 1
        else:
            key = (d.get('Open'), d.get('Max'), d.get('Min'), d.get('Now'))
            code = cmap.get(key)
            if code:
                stat['by_ohlc'] += 1
            else:
                stat['unmatched'] += 1

                continue
        extra = {k: d[k] for k in EXTRA_KEYS if d.get(k) not in (None, '')}
        vals = {name: d.get(src) for name, src in FIXED}
        zsz, ltsz = d.get('Zsz'), d.get('Ltsz')
        out.append((
            code, trade_date,
            vals['pe_ttm'], vals['pb'],
            round(zsz * YI, 2) if zsz else None,
            round(ltsz * YI, 2) if ltsz else None,
            None, None, None,               # eps / bps / roe_pct：快照里没有
            json.dumps(extra, ensure_ascii=False), batch_id))
    return out, stat


#: 只填空。`WHERE limit_up IS NULL OR limit_down IS NULL` 是这条语句的全部
#: 意义 —— 实测通达信的 ZTPrice 在北交所（截断 vs 四舍五入）和主板 ST
#: （不区分 ±5%）两处比库里差，覆盖已有值是往回改坏。
LIMIT_FILL = """
UPDATE md.stock_daily d
   SET limit_up = coalesce(d.limit_up, v.zt),
       limit_down = coalesce(d.limit_down, v.dt),
       updated_at = now()
  FROM (SELECT unnest(%(codes)s::text[]) AS ts_code,
               unnest(%(zt)s::numeric[])  AS zt,
               unnest(%(dt)s::numeric[])  AS dt) v
 WHERE d.ts_code = v.ts_code
   AND d.trade_date = %(d)s
   AND (d.limit_up IS NULL OR d.limit_down IS NULL)
"""

#: `--overwrite-limits` 才走这条。留着是因为哪天证明通达信更准得有个开关，
#: 不是因为现在推荐用。
LIMIT_OVERWRITE = """
UPDATE md.stock_daily d
   SET limit_up = v.zt, limit_down = v.dt, updated_at = now()
  FROM (SELECT unnest(%(codes)s::text[]) AS ts_code,
               unnest(%(zt)s::numeric[])  AS zt,
               unnest(%(dt)s::numeric[])  AS dt) v
 WHERE d.ts_code = v.ts_code
   AND d.trade_date = %(d)s
   -- 只动确实不一致的行：全表重写会把 updated_at 刷成"今天全改过"，
   -- 之后再也分不清哪些是真被修正过的
   AND (d.limit_up IS DISTINCT FROM v.zt OR d.limit_down IS DISTINCT FROM v.dt)
"""

COUNT_NULL = """
SELECT count(*) FROM md.stock_daily d
  JOIN (SELECT unnest(%(codes)s::text[]) AS ts_code) v ON v.ts_code = d.ts_code
 WHERE d.trade_date = %(d)s
   AND (d.limit_up IS NULL OR d.limit_down IS NULL)
"""

COUNT_DIFF = """
SELECT count(*) FROM md.stock_daily d
  JOIN (SELECT unnest(%(codes)s::text[]) AS ts_code,
               unnest(%(zt)s::numeric[])  AS zt,
               unnest(%(dt)s::numeric[])  AS dt) v ON v.ts_code = d.ts_code
 WHERE d.trade_date = %(d)s
   AND d.limit_up IS NOT NULL AND d.limit_down IS NOT NULL
   AND (d.limit_up IS DISTINCT FROM v.zt OR d.limit_down IS DISTINCT FROM v.dt)
"""


def fix_limits(rows: list[tuple], trade_date: dt.date, *, dry_run: bool,
               overwrite: bool = False) -> tuple[int, int, int]:
    """
    用快照里的涨跌停价补 `md.stock_daily`。

    返回 `(空缺数, 已有但不一致数, 实际改动行数)`。默认只补空缺；
    "不一致"只报数不改 —— 库里那批多半才是对的（见模块 docstring）。
    """
    codes, zts, dts = [], [], []
    for r in rows:
        extra = json.loads(r[9])
        zt, dt_ = extra.get('ZTPrice'), extra.get('DTPrice')
        if zt in (None, 0) or dt_ in (None, 0):
            continue                      # 停牌那天两个都是 0，不能当真值写进去
        codes.append(r[0])
        zts.append(zt)
        dts.append(dt_)
    if not codes:
        return 0, 0, 0
    p = {'codes': codes, 'zt': zts, 'dt': dts, 'd': trade_date}
    empty = int(engine.scalar(COUNT_NULL, p) or 0)
    diff = int(engine.scalar(COUNT_DIFF, p) or 0)
    if dry_run:
        return empty, diff, 0
    sql = LIMIT_OVERWRITE if overwrite else LIMIT_FILL
    return empty, diff, engine.execute(sql, p, role='etl') or 0



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='只做某一张表，格式 20260831（表名=采集日期）')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-fill-limits', action='store_true',
                    help='连 md.stock_daily 里空的涨跌停价也不填')
    ap.add_argument('--overwrite-limits', action='store_true',
                    help='用通达信的值覆盖已有涨跌停价（不推荐，见模块说明）')
    args = ap.parse_args()

    print('=== 通达信收盘快照库 ===')
    print('  %s' % DB_PATH)
    if not DB_PATH.is_file():
        print('  文件不在。先在通达信里跑一次 PYPlugins\\user\\Get_DayEnd.py')
        return 1
    print('  %.1f MB' % (DB_PATH.stat().st_size / 1e6))

    con = sqlite3.connect(f'file:{DB_PATH.as_posix()}?mode=ro', uri=True)
    tables = sqlite_tables(con)
    if args.date:
        tables = [t for t in tables if t[0] == f'day_end_{args.date}']
        if not tables:
            print('  没有 day_end_%s（或者是空表）' % args.date)
            return 1
    print('  非空日表 %d 张（表名=采集日期）：%s ~ %s'
          % (len(tables), tables[0][1], tables[-1][1]))

    batch_id = 0
    if not args.dry_run:
        batch_id = ops_repo.begin_batch('tdx_snapshot_db', 'md.stock_snapshot',
                                        source_path=str(DB_PATH))
    total = filled_total = empty_total = diff_total = 0
    skipped: list[tuple[dt.date, float]] = []
    try:
        for table, day in tables:
            t0 = time.time()
            recs = read_table(con, table)
            keys = {(d.get('Open'), d.get('Max'), d.get('Min'), d.get('Now'))
                    for d in recs}
            trade_date, ratio, guess = resolve_trade_date(keys, day)
            if trade_date is None:
                skipped.append((day, ratio))
                print('  采集于 %s  %4d 行 → 跳过：盘中/盘前采集，'
                      '最像 %s 但只对上 %.0f%%'
                      % (day, len(recs), guess, ratio * 100))
                continue
            rows, stat = build_rows(recs, trade_date, batch_id)
            wrote = 0 if args.dry_run else quote_repo.upsert_snapshot(rows)
            total += len(rows)
            src = ('Code 列' if stat['by_code'] else 'OHLC 反查')
            shift = '' if trade_date == day else '（采集于 %s）' % day
            print('  %s%s  %4d 行 → 写 %4s（%s，对不上 %d）  %.1fs'
                  % (trade_date, shift, stat['rows'],
                     len(rows) if args.dry_run else wrote,
                     src, stat['unmatched'], time.time() - t0))
            if not args.no_fill_limits:
                empty, diff, n = fix_limits(
                    rows, trade_date, dry_run=args.dry_run,
                    overwrite=args.overwrite_limits)
                empty_total += empty
                diff_total += diff
                filled_total += n
                if empty or diff:
                    print('      涨跌停价：库里空缺 %d 只、已有但不一致 %d 只%s'
                          % (empty, diff,
                             '（--dry-run 未改）' if args.dry_run else
                             '，改了 %d 行' % n))
        if not args.dry_run:
            ops_repo.end_batch(batch_id, status='ok', row_count=total)

    except Exception as exc:
        if not args.dry_run:
            ops_repo.end_batch(batch_id, status='failed', message=str(exc))
        raise
    finally:
        con.close()

    print('=== 结果 ===')
    print('  md.stock_snapshot 写入 %s 行' % f'{total:,}')
    if skipped:
        print('  跳过 %d 张盘中/盘前采集的表：%s'
              % (len(skipped), '、'.join(str(d) for d, _ in skipped)))

    if not args.no_fill_limits:
        print('  涨跌停价：空缺合计 %s 只、已有但不一致 %s 只（默认不覆盖），'
              '实际改动 %s 行'
              % (f'{empty_total:,}', f'{diff_total:,}', f'{filled_total:,}'))

    if not args.dry_run:
        got = engine.query_one(
            'SELECT count(*) n, count(DISTINCT trade_date) d, '
            '       min(trade_date) lo, max(trade_date) hi '
            '  FROM md.stock_snapshot')
        print('  库里现在 %s 行 / %s 个交易日（%s ~ %s）'
              % (f"{got['n']:,}", got['d'], got['lo'], got['hi']))
    print('\nDONE' + ('（--dry-run，未写库）' if args.dry_run else ''))
    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
