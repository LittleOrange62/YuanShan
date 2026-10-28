# -*- coding: utf-8 -*-
"""
C10 · 追平到最新已收盘交易日（日历 + 个股日线 + 指数日线 + 板块日线 + 回算）。

**为什么要有这个脚本**：2026-08-27 发现库里最新数据停在 08-25，而 08-26、08-27
都是交易日。根子不在"同步漏了"，而在 **`ref.trade_calendar` 本身只到 08-25**
—— 日历没有往后，系统就不知道自己缺了两天，任何"缺口告警"都不会响。
所以这里第一步永远是把日历接上，再由日历反推缺哪几天。

**判据是数据日期，不是脚本跑没跑完**：目标交易日 = 日历里 `is_open`
且已经收盘（北京时间 ≥ 15:30 才算当天收盘）且 `md.stock_daily` 里没有的日期。

**数据源优先级（硬规则，不许绕）**：

    1. 通达信 vipdoc 本地文件 —— 个股/指数/板块日线的**主源**
    2. 通达信客户端下载（vipdoc 还没覆盖目标日就先去下）
    3. 网络源（新浪 akshare）—— **兜底，默认关闭，必须显式 `--allow-web`**

2026-08-27 我用新浪把 08-26/08-27 的全市场日线补进了库，用户当场纠正：
「日线等数据优先用通达信 vipdoc，这个逻辑你要记好」。原因很实在 ——
vipdoc 是权威源（`sh600519.day` 尾部记录与库里逐字段相同），
网络源的复权口径、成交额单位、板块加权算法都和它不完全可比，
悄悄混进来会让同一条曲线上出现两种口径而没人看得出。
所以现在网络兜底必须显式开关，写进库的行也带来源标记（见 `md.v_daily_source`）。

**兜底源的实测状态**（真要用 `--allow-web` 时的选择依据）：东财
`push2his.eastmoney.com` 对所有请求直接掐连接（`RemoteDisconnected`），
`datacenter-web` 正常，是分域名故障；`mootdx` 标准行情服务器返回空表。
新浪 `stock_zh_a_daily` 通，而且**自带 outstanding_share 与 turnover**，
省掉"先猜股本再算换手率"这一步 —— 所以兜底走新浪。

**板块日线两条口径**：vipdoc 路径走 `c3_sector.py`，是通达信板块指数原始口径；
兜底路径（`catch_up_sector`）用成分股按成交额加权算涨幅、成交额求和、
涨跌家数计数，`close` 用上一交易日 `close × (1 + pct/100)` 递推保持曲线连续。
**后者与历史通达信数据不完全可比**，所以以后通达信补下来要用
`--overwrite` 覆盖回通达信口径。

用法：
    python scripts\\ingest\\c10_catchup.py                 # vipdoc 优先，自动补全
    python scripts\\ingest\\c10_catchup.py --dry-run       # 只看缺哪几天、vipdoc 到哪天
    python scripts\\ingest\\c10_catchup.py --overwrite     # 用 vipdoc 覆盖已有（兜底源）行
    python scripts\\ingest\\c10_catchup.py --allow-web     # 允许落到网络兜底
    python scripts\\ingest\\c10_catchup.py --date 2026-08-26 --allow-web --threads 8
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402
from app.db.repo import quote as quote_repo  # noqa: E402
from app.db.repo import security as sec_repo  # noqa: E402
from app.tdx_client import readiness  # noqa: E402

CLOSE_HHMM = (15, 30)      # 15:30 之后才认为当天已收盘（留出清算时间）
RETRY = 3
SCRIPTS = Path(__file__).resolve().parent



# ------------------------------------------------------------------ 日历

def sync_calendar() -> int:
    """从新浪同步交易日历。一次调用拿到全年，够用且不依赖 Tushare token。"""
    import akshare as ak
    df = ak.tool_trade_date_hist_sina()
    dates = []
    for v in df['trade_date'].tolist():
        d = v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)[:10])
        dates.append(d)
    return sec_repo.upsert_calendar(dates)


def target_dates(explicit: str | None) -> list[dt.date]:
    """要补的交易日。"""
    if explicit:
        return [dt.date.fromisoformat(explicit)]
    now = dt.datetime.now()
    today_closed = (now.hour, now.minute) >= CLOSE_HHMM
    cutoff = now.date() if today_closed else now.date() - dt.timedelta(days=1)
    rows = engine.query(
        """
        SELECT c.cal_date FROM ref.trade_calendar c
         WHERE c.is_open AND c.cal_date <= %s
           AND c.cal_date > (SELECT max(trade_date) - 1 FROM md.stock_daily)
           AND NOT EXISTS (SELECT 1 FROM md.stock_daily d
                            WHERE d.trade_date = c.cal_date)
         ORDER BY c.cal_date
        """, (cutoff,))
    return [r['cal_date'] for r in rows]


# --------------------------------------------------------- vipdoc 优先
def vipdoc_verdict(target: dt.date) -> readiness.Verdict:
    """
    vipdoc 覆盖到目标交易日了吗。

    判据复用 `app.tdx_client.readiness` —— 那边已经把"只 seek 尾部 32 字节
    读日期""按市场分别判定再取交集""停牌股不能当锚定"这些坑踩完了，
    这里再写一版必然出现两处判据不一致。
    """
    return readiness.Readiness().day_ready(target)


def run_script(name: str, *extra: str) -> int:
    """
    调另一个入库脚本，**输出实时透传**。

    不重定向不捕获：长时任务把输出吞进文件会被当成卡死。这里让 c1_day.py
    自己的进度直接打到同一个终端上。
    """
    cmd = [sys.executable, str(SCRIPTS / name), *extra]
    print('  $ %s' % ' '.join(cmd[1:]), flush=True)
    return subprocess.run(cmd, cwd=str(SCRIPTS.parent.parent)).returncode


def catch_up_from_vipdoc(*, overwrite: bool, do_sector: bool,
                         since: dt.date | None = None) -> bool:
    """
    走主源：c1_day.py 入个股/指数/基金日线，c3_sector.py 入板块日线。

    覆盖模式下**一定要传 since**：不带日期范围的 `--overwrite` 会把 600 多万行
    全过一遍 `md.trg_daily_sanity`，要十分钟；只想把某几天换回通达信口径时
    带上 since 是几秒钟的事。
    """
    args = ['--overwrite'] if overwrite else []
    if since:
        args += ['--since', since.isoformat()]
    if run_script('c1_day.py', *args) != 0:
        print('  c1_day.py 失败')
        return False
    if do_sector and run_script('c3_sector.py', *args) != 0:
        print('  c3_sector.py 失败（个股已入库，板块可单独重跑）')
        return False
    return True



def try_tdx_download(target: dt.date, *, timeout: int = 5400) -> bool:
    """
    vipdoc 还没到目标日 → 走 **P0 前置下载** 让通达信把盘后数据下下来。

    调 `scripts/ops/p0_tdx_predownload.py --day-only`，那个脚本才是这条链路的
    固化入口（分步调用 run_agent、逐行透传进度、心跳与卡死检测、失败先杀进程
    再从头来过）。**这里不再直接调 `run_agent`**：2026-08-31 之前是那样写的，
    结果重来机制、卡死判定、分钟线这些都要在两处各写一遍。

    `--day-only`：补日线这条路上分钟线不是必需的，多跑一遍要 7 分多钟。
    要分钟线就直接跑那个脚本（不带 `--day-only`）。

    **必须带超时**：2026-08-28 实测 s5 打不开菜单时整条链路会一直耗在定位重试
    上，从外面看就是"卡死"。这里给一个硬上限，超时就杀掉子进程继续往下走 ——
    自动化卡住不该把整条补数链路一起拖住。（p0 脚本自己也有每步超时和
    "多久没出声"检测，这一层是最后的兜底。）

    **判据只看 vipdoc 尾部日期，不看窗口关没关、也不只看出口码**：
    实测出现过下载窗口消失而 `.day` 尾部还是上一个交易日的情况
    （通达信自己那侧失败但不报错）。所以跑完一定再探一次文件。
    """
    cmd = [sys.executable, '-u', str(config.BASE_DIR / 'scripts' / 'ops'
                                     / 'p0_tdx_predownload.py'),
           '--day-only', '--date', target.isoformat()]
    print('  $ %s   （超时上限 %s 分钟）' % (' '.join(cmd[1:]), timeout // 60),
          flush=True)
    rc: int | str
    try:
        rc = subprocess.run(cmd, cwd=str(config.BASE_DIR),
                            timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        rc = f'超时 {timeout}s，已终止'
        print('  **通达信自动化超时**，已杀掉子进程', flush=True)
    except OSError as exc:
        print('  启动失败：%s' % exc)
        return False
    v = vipdoc_verdict(target)
    print(v.report())
    if not v.ok:
        print('  通达信下载没把 vipdoc 推到 %s（p0 前置下载结果 %s）' % (target, rc))
        print('  单独重跑看细节：python scripts\\ops\\p0_tdx_predownload.py '
              '--date %s' % target)
    return v.ok





# ------------------------------------------------------------------ 个股

def universe() -> list[str]:
    """
    要抓的代码。最后一个已有交易日的全集 ∪ 在市**股票** —— 只用前者会漏掉
    这两天之间上市的新股，只用后者会带上一堆早已退市的壳。

    **必须按 `asset_type='stock'` 过滤，不能按代码正则**。第一版用
    `^(6|0|3|9|4|8)[0-9]{5}\\.(SH|SZ|BJ)$` 筛，结果把上证综指 000001.SH
    也当成股票抓了进来，写库时被 `md.trg_daily_sanity` 拦下：
    「amount_yuan/vol_shares=17.5103 超出价格区间 [3881.74, 3926.44]」——
    指数的 volume 是手、amount 口径也不同，均价算出来只有 17 块，
    而"价格"是 3900 点。这个触发器这次帮了大忙，否则脏数据会静默入库。
    """
    rows = engine.query(
        """
        SELECT DISTINCT d.ts_code FROM md.stock_daily d
         WHERE d.trade_date = (SELECT max(trade_date) FROM md.stock_daily)
        UNION
        SELECT s.ts_code FROM ref.security s
         WHERE s.is_active AND s.asset_type = 'stock'
        """)
    return sorted(r['ts_code'] for r in rows)



def _sina_symbol(ts_code: str) -> str:
    code, _, mkt = ts_code.partition('.')
    return f'{mkt.lower()}{code}'


def fetch_stock(ts_code: str, d0: dt.date, d1: dt.date) -> list[dict]:
    """
    取 [d0, d1] 的日线。d0 传"库里已有的最后一天"，为的是拿到它的收盘价
    当作第一个补齐日的 pre_close —— 同一个源里前后相邻的两根，
    比拿库里的收盘去和新浪的收盘比更不容易踩到口径差。
    """
    import akshare as ak
    last = None
    for attempt in range(RETRY):
        try:
            df = ak.stock_zh_a_daily(symbol=_sina_symbol(ts_code),
                                     start_date=d0.strftime('%Y%m%d'),
                                     end_date=d1.strftime('%Y%m%d'))
            if df is None or df.empty:
                return []
            out = []
            for r in df.to_dict('records'):
                try:
                    day = dt.date.fromisoformat(str(r['date'])[:10])
                except (KeyError, TypeError, ValueError):
                    continue
                out.append({
                    'trade_date': day,
                    'open': r.get('open'), 'high': r.get('high'),
                    'low': r.get('low'), 'close': r.get('close'),
                    'vol_shares': r.get('volume'), 'amount_yuan': r.get('amount'),
                    'turnover': r.get('turnover'),
                    'float_share': r.get('outstanding_share'),
                })
            return out
        except Exception as exc:
            last = exc
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f'{ts_code}: {last}')


def _f(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def build_rows(ts_code: str, bars: list[dict], want: set[dt.date],
               batch_id: int) -> list[tuple]:
    """组装 md.stock_daily 的行。pre_close 取同一序列里的上一根。"""
    bars = sorted(bars, key=lambda r: r['trade_date'])
    rows = []
    for i, b in enumerate(bars):
        if b['trade_date'] not in want:
            continue
        close = _f(b['close'])
        if close is None or close <= 0:
            continue
        prev = _f(bars[i - 1]['close']) if i else None
        chg = round(close - prev, 4) if prev else None
        pct = round((close - prev) / prev * 100, 4) if prev and prev > 0 else None
        turn = _f(b['turnover'])
        rows.append((
            ts_code, b['trade_date'], _f(b['open']), _f(b['high']), _f(b['low']),
            close, prev, chg, pct, _f(b['vol_shares']), _f(b['amount_yuan']),
            round(turn * 100, 4) if turn is not None else None,
            None, None,           # 涨跌停由 recompute_limits 按板块规则回算
            1.0,                  # 最新日之后没有除权事件，后复权因子恒为 1
            batch_id))
    return rows


def _flush(buf: list[tuple]) -> tuple[int, int]:
    """
    批量写；整批被拒时退回逐行写，把坏行单独挑出来。

    `md.trg_daily_sanity` 是 RAISE 异常型触发器，一行不合口径整个
    executemany 都会 abort（psycopg 的 pipeline 直接 INERROR）。
    前面 1000 只票的成果不该被一只坏票带走，所以这里兜一层。
    """
    if not buf:
        return 0, 0
    try:
        return quote_repo.upsert_daily(buf) or len(buf), 0
    except Exception as exc:
        print('    整批被拒，改逐行写：%s' % str(exc).split(chr(10))[0][:120],
              flush=True)
    ok = bad = 0
    for row in buf:
        try:
            quote_repo.upsert_daily([row])
            ok += 1
        except Exception:
            bad += 1
    return ok, bad


def catch_up_stocks(dates: list[dt.date], threads: int, batch_id: int) -> int:
    codes = universe()
    d0 = engine.scalar('SELECT max(trade_date) FROM md.stock_daily')
    want = set(dates)
    print('  代码 %s 个，窗口 %s .. %s' % (len(codes), d0, max(dates)), flush=True)
    total = fail = bad = 0
    done = 0
    with ThreadPoolExecutor(max_workers=threads) as pool:
        futs = {pool.submit(fetch_stock, c, d0, max(dates)): c for c in codes}
        buf: list[tuple] = []
        for fut in as_completed(futs):
            code = futs[fut]
            done += 1
            try:
                bars = fut.result()
            except RuntimeError:
                fail += 1
                bars = []
            rows = build_rows(code, bars, want, batch_id) if bars else []
            buf.extend(rows)
            if len(buf) >= 2000:
                n, b = _flush(buf)
                total += n
                bad += b
                buf = []
            if done % 500 == 0:
                print('    %s/%s  已写 %s 行  取数失败 %s  坏行 %s'
                      % (done, len(codes), f'{total:,}', fail, bad), flush=True)
        n, b = _flush(buf)
        total += n
        bad += b
    print('  个股日线写入 %s 行，取数失败 %s 个代码，被口径校验拒掉 %s 行'
          % (f'{total:,}', fail, bad))
    return total



# ------------------------------------------------------------------ 指数

def catch_up_index(dates: list[dt.date], threads: int, batch_id: int) -> int:
    import akshare as ak
    codes = [r['ts_code'] for r in engine.query(
        'SELECT DISTINCT ts_code FROM md.index_daily ORDER BY ts_code')]
    want = set(dates)

    def one(ts_code: str) -> list[tuple]:
        sym = _sina_symbol(ts_code)
        for attempt in range(RETRY):
            try:
                df = ak.stock_zh_index_daily(symbol=sym)
                break
            except Exception:
                time.sleep(0.5 * (attempt + 1))
        else:
            return []
        if df is None or df.empty:
            return []
        bars = []
        for r in df.to_dict('records'):
            try:
                day = dt.date.fromisoformat(str(r['date'])[:10])
            except (KeyError, TypeError, ValueError):
                continue
            bars.append((day, r))
        bars.sort(key=lambda x: x[0])
        out = []
        for i, (day, r) in enumerate(bars):
            if day not in want:
                continue
            close = _f(r.get('close'))
            if close is None:
                continue
            prev = _f(bars[i - 1][1].get('close')) if i else None
            pct = round((close - prev) / prev * 100, 4) if prev and prev > 0 else None
            out.append((ts_code, day, _f(r.get('open')), _f(r.get('high')),
                        _f(r.get('low')), close, prev, pct,
                        _f(r.get('volume')), _f(r.get('amount')), batch_id))
        return out

    total = 0
    with ThreadPoolExecutor(max_workers=threads) as pool:
        for rows in pool.map(one, codes):
            if rows:
                total += quote_repo.upsert_index_daily(rows) or len(rows)
    print('  指数日线写入 %s 行（%s 个指数）' % (f'{total:,}', len(codes)))
    return total


# ------------------------------------------------------------------ 板块

def catch_up_sector(dates: list[dt.date], batch_id: int) -> int:
    """
    由成分股聚合板块日线。**口径与通达信不同，见模块开头说明。**
    涨幅按成交额加权 —— 等权会让一堆小票把板块拉飞。
    """
    n = 0
    for d in dates:
        n += engine.execute(
            """
            WITH agg AS (
                SELECT m.sector_code,
                       sum(dd.amount_yuan)                        AS amount_yuan,
                       sum(dd.vol_shares)                         AS vol_shares,
                       count(*)                                   AS member_count,
                       count(*) FILTER (WHERE dd.pct_chg > 0)      AS up_count,
                       count(*) FILTER (WHERE dd.pct_chg < 0)      AS down_count,
                       CASE WHEN sum(dd.amount_yuan) > 0
                            THEN sum(dd.pct_chg * dd.amount_yuan) / sum(dd.amount_yuan)
                            ELSE avg(dd.pct_chg) END               AS pct_chg
                  FROM ref.sector_member m
                  JOIN md.stock_daily dd ON dd.ts_code = m.ts_code
                                        AND dd.trade_date = %(d)s
                 WHERE m.out_date IS NULL AND dd.pct_chg IS NOT NULL
                 GROUP BY m.sector_code
                HAVING count(*) >= 3
            ),
            prev AS (
                SELECT sd.sector_code, sd.close
                  FROM md.sector_daily sd
                 WHERE sd.trade_date = (SELECT max(trade_date) FROM md.sector_daily
                                         WHERE trade_date < %(d)s)
            )
            INSERT INTO md.sector_daily
                (sector_code, trade_date, open, high, low, close, pre_close,
                 pct_chg, vol_shares, amount_yuan, up_count, down_count,
                 member_count, src_batch_id)
            SELECT a.sector_code, %(d)s,
                   NULL, NULL, NULL,
                   round(p.close * (1 + a.pct_chg / 100), 4),
                   p.close,
                   round(a.pct_chg, 4), a.vol_shares, a.amount_yuan,
                   a.up_count, a.down_count, a.member_count, %(b)s
              FROM agg a
              JOIN prev p ON p.sector_code = a.sector_code
            ON CONFLICT (sector_code, trade_date) DO UPDATE SET
                close = EXCLUDED.close, pre_close = EXCLUDED.pre_close,
                pct_chg = EXCLUDED.pct_chg, vol_shares = EXCLUDED.vol_shares,
                amount_yuan = EXCLUDED.amount_yuan, up_count = EXCLUDED.up_count,
                down_count = EXCLUDED.down_count,
                member_count = EXCLUDED.member_count,
                src_batch_id = EXCLUDED.src_batch_id, updated_at = now()
            """, {'d': d, 'b': batch_id}, role='etl')
    print('  板块日线写入 %s 行（成分股聚合口径）' % f'{n:,}')
    return n


# ------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='只补这一天 YYYY-MM-DD')
    ap.add_argument('--threads', type=int, default=6)
    ap.add_argument('--no-sector', action='store_true')
    ap.add_argument('--no-calendar', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--allow-web', action='store_true',
                    help='允许落到网络兜底源（新浪）。**默认不允许** —— '
                         '日线主源是通达信 vipdoc，兜底会引入口径差')
    ap.add_argument('--overwrite', action='store_true',
                    help='用 vipdoc 覆盖已有行（把之前网络兜底写的行换回通达信口径）')
    ap.add_argument('--no-tdx-download', action='store_true',
                    help='vipdoc 没覆盖时也不去动通达信客户端')
    args = ap.parse_args()

    print('=== 1. 交易日历 ===')
    if args.no_calendar:
        print('  跳过')
    else:
        before = engine.scalar('SELECT max(cal_date) FROM ref.trade_calendar')
        n = sync_calendar()
        after = engine.scalar('SELECT max(cal_date) FROM ref.trade_calendar')
        print('  写入/更新 %s 行，日历最后一天 %s → %s' % (n, before, after))

    print('=== 2. 要补的交易日 ===')
    dates = target_dates(args.date)
    have = engine.scalar('SELECT max(trade_date) FROM md.stock_daily')
    print('  库里最新 %s，待补 %s' % (have, dates or '无'))

    # 「日期不缺」不等于「口径对」。08-26/08-27 是新浪兜底写的，日期看起来齐了，
    # 口径却和历史不一致 —— 所以这里额外把非通达信来源的日子报出来，
    # 而不是看到 dates 为空就宣布"已经最新"。
    fb = engine.query(
        "SELECT trade_date, source, row_count FROM md.v_daily_source "
        "WHERE trade_date >= (SELECT max(trade_date) - 10 FROM md.stock_daily) "
        "  AND source NOT IN ('通达信vipdoc', '旧库迁移') "
        'ORDER BY trade_date')
    if fb:
        print('  近 10 日里来源不是通达信的：')
        for r in fb:
            print('    %s  %-10s %s 行' % (r['trade_date'], r['source'],
                                           f"{r['row_count']:,}"))
        print('  → 想换回通达信口径就加 --overwrite')
    if not dates and not args.overwrite and not args.dry_run:
        print('\nDONE（日期已齐；要把上面这些天换成通达信口径请加 --overwrite）'
              if fb else '\nDONE（已经是最新）')
        return 0


    target = max(dates) if dates else (
        dt.date.fromisoformat(args.date) if args.date
        else readiness.expected_trade_date())

    # 覆盖模式下没有"待补日期"，但被覆盖的那几天照样要回算派生列，
    # 否则换手率/涨跌停还留着兜底源那一版。
    touched = sorted(set(dates) |
                     ({r['trade_date'] for r in fb} if args.overwrite else set()))
    if not touched:
        touched = [target]




    print('=== 3. vipdoc 覆盖情况（主源） ===')
    v = vipdoc_verdict(target)
    print(v.report())
    if args.dry_run:
        print('\nDONE（dry-run 未写库）')
        return 0

    # ---- 主源路径：vipdoc 已覆盖 → 直接入库 -------------------------
    if v.ok:
        print('=== 4. 从 vipdoc 入库（c1_day.py / c3_sector.py） ===')
        if catch_up_from_vipdoc(overwrite=args.overwrite,
                                do_sector=not args.no_sector,
                                since=min(touched)):
            return finish(touched, refresh=args.overwrite)
        print('  vipdoc 入库失败，见上面的输出')
        return 1

    # ---- 次源路径：让通达信去下 -------------------------------------
    if not args.no_tdx_download:
        print('=== 4. vipdoc 还没到 %s，指挥通达信下载 ===' % target)
        if try_tdx_download(target):
            print('=== 5. 从 vipdoc 入库 ===')
            if catch_up_from_vipdoc(overwrite=args.overwrite,
                                    do_sector=not args.no_sector,
                                    since=min(touched)):
                return finish(touched, refresh=args.overwrite)
            return 1


    # ---- 兜底路径：必须显式开 --------------------------------------
    if not args.allow_web:
        print('\n**停在这里，不走网络兜底。**')
        print('  日线主源是通达信 vipdoc，网络源（新浪）的复权口径、成交额单位、')
        print('  板块加权算法都与它不完全可比，混进同一条曲线没人看得出来。')
        print('  下一步二选一：')
        print('    1. 把通达信的盘后数据下下来：'
              'python scripts\\ops\\p0_tdx_predownload.py --check 先看差哪一步')
        print('    2. 确实等不了，显式加 --allow-web 走兜底，事后再 --overwrite 换回来')
        return 2

    print('=== 5. 网络兜底（新浪）—— 口径与 vipdoc 不完全可比 ===')
    if not dates:
        print('  没有待补日期，兜底无事可做')
        return 0
    batch_id = ops_repo.begin_batch('sina_ak', 'md.stock_daily')
    print('  批次 %s' % batch_id)
    try:
        print('--- 5.1 个股日线 ---')
        t0 = time.time()
        catch_up_stocks(dates, args.threads, batch_id)
        print('  用时 %.0fs' % (time.time() - t0))

        print('--- 5.2 指数日线 ---')
        catch_up_index(dates, args.threads, batch_id)

        if not args.no_sector:
            print('--- 5.3 板块日线（成分股聚合，非通达信口径） ---')
            catch_up_sector(dates, batch_id)
        ops_repo.end_batch(batch_id, row_count=None)
    except Exception as exc:
        ops_repo.end_batch(batch_id, row_count=None, status='failed',
                           message=str(exc)[:500])
        raise
    return finish(dates, fallback=True)


def finish(dates: list[dt.date], *, fallback: bool = False,
           refresh: bool = False) -> int:
    """
    回算派生列 + 验收。两条路径共用，避免其中一条忘了回算。

    `refresh=True`（覆盖模式）时**连已有值一起重算**：c1_day.py 的覆盖只换
    open/high/low/close/vol/amount，pre_close、涨跌幅、换手率、涨跌停还留着
    兜底源那一版 —— 一行里 close 是通达信口径而涨跌幅是新浪口径，
    每列单独看都正常，最难查。
    """
    print('=== 回算派生列%s ===' % ('（覆盖模式：全部重算）' if refresh else ''))
    for d in dates:
        pre = quote_repo.recompute_pre_close(d, role='admin', force=refresh)
        lim = quote_repo.recompute_limits(d, role='admin',
                                         only_missing=not refresh)
        turn = quote_repo.recompute_turnover(d, only_missing=not refresh,
                                            role='admin')
        print('  %s  前收/涨跌幅 %s 行  涨跌停 %s 行  换手率 %s 行'
              % (d, pre, lim, turn))
    if refresh:
        # 除权日的涨跌幅要按复权价重算，否则 002594.SZ 那种会出 -66.94%
        ex = quote_repo.recompute_pct_chg_ex(role='admin')
        print('  除权日涨跌幅修正 %s 行' % ex)
    # **板块家数要在 pct_chg 之后再算一遍。** c3_sector 自己也算，但它跑在
    # 上面这些回算之前 —— 那时候 pct_chg 可能还是 NULL（覆盖会先把它清掉），
    # 数出来的 up/down 就全是 0，`sector_guangdu_v1`（板块广度）整项算不出值、
    # 权重被静默摊到其他三个分项上。2026-08-29 在 c12 那条路上踩到过，
    # 症状是图上完全看不出来的一致性错误，所以两条路都补这一刀。
    if dates:
        cnt = quote_repo.recompute_sector_counts(min(dates), max(dates))
        print('  板块涨跌家数 %s 行' % f'{cnt:,}')


    print('=== 验收 ===')
    for t in ('md.stock_daily', 'md.index_daily', 'md.sector_daily'):
        print('  %-18s 最新 %s  当日行数 %s'
              % (t, engine.scalar(f'SELECT max(trade_date) FROM {t}'),
                 engine.scalar(f'SELECT count(*) FROM {t} '
                               f'WHERE trade_date = (SELECT max(trade_date) FROM {t})')))
    rows = engine.query(
        # 带 WHERE 是为了让谓词下推进 GROUP BY —— 不加就是对 950 万行做全表聚合
        "SELECT trade_date, source, row_count FROM md.v_daily_source "
        "WHERE trade_date >= (SELECT max(trade_date) - 5 FROM md.stock_daily) "
        'ORDER BY trade_date DESC, row_count DESC')

    if rows:
        print('  最近几天的日线来源：')
        for r in rows:
            print('    %s  %-12s %s 行'
                  % (r['trade_date'], r['source'], f"{r['row_count']:,}"))
    if fallback:
        print('\n**这批是网络兜底源写入的**，等通达信把这几天的 vipdoc 下下来后跑：'
              '\n  python scripts\\ingest\\c10_catchup.py --overwrite')
    print('\n补完后还要跑：'
          '\n  python scripts\\compute\\d1_indicators.py --recent %s'
          '\n  python scripts\\compute\\d2_scores.py --recent %s'
          % (len(dates), len(dates)))
    print('\nDONE')
    return 0



if __name__ == '__main__':
    raise SystemExit(main())
