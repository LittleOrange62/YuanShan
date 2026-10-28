# -*- coding: utf-8 -*-
"""C22 · 行业资金流（同花顺「即时」榜）逐日落库 → `market_data.sector_fund_flow_daily`。

**为什么必须有这一步**（用户 2026-10-06 点名：「板块资金趋势榜，这个表也入库了的吧？
没有的话改一下逻辑，要先入数据库」）：这块内容原先**完全不落库** ——
`vr/market.py:_sectors()` 每次打开首页现抓一次 akshare，进程内 5 分钟缓存，
关掉页面就什么都不剩。所以"近 3 个月累计净流入"、"2024 年以来累计"这类问题
在当时**一个都答不出来**，不是没做榜单，是**没有历史**。这张表就是那段历史的第一天。

一条和 C21 同款的硬约束（先记住再写代码）：

1. **报文里没有日期字段** —— 接口只给"此刻这一份即时榜"。所以归属交易日一律由
   `reference_data.trade_calendar` 判（最近一个 `is_open` 的日子），**非交易日抓到的是
   上一场的数却无法证明** ⇒ 默认拒写。
2. **更不能时间旅行**：今天抓一份榜盖到 8 月 1 日 = 造数据。`--date` 只接受
   「最近一个已收盘交易日」或今天，更早的日期直接拒收（与 `c21_fundflow_sina.py` 同一条规矩）。
3. **一天只许一发**：即时截面在盘中/盘后不一样，两次覆盖一次的话，事后只看得到"今天有数"，
   看不出数是哪一刻的 ⇒ `already_written()` + 状态文件，要重采显式 `--rerun`。

⚠️ 别把这张表和 `analytics.sector_*` 那批表混起来：这里的 `sector_name` 是**同花顺行业名**，
本地板块体系是通达信（`reference_data.sector_info`，代码 `TDX_/GN_/THS_`），
**两套分类 join 不上是设计如此**，要跨过去只能按名字模糊匹配 —— 那是另一种造数，所以不做。

用法：
    python -m ingest.jobs.c22_sector_flow                    # 归属日=最近一个已收盘交易日（链上就这么跑）
    python -m ingest.jobs.c22_sector_flow --dry-run          # 只打印会写什么，不落库
    python -m ingest.jobs.c22_sector_flow --rerun            # 同一天重采（覆盖当日那份）
    python -m ingest.jobs.c22_sector_flow --date 2026-10-09  # 显式归属日（只接受今天或最近一场）

退出码：0 写了 / 已经写过跳过；2 按设计不写（非交易日且没 --force、接口空、归属日太远）；
1 真失败（接口异常、行数过少 = 半截榜）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.db import engine  # noqa: E402
from app.db.repo import schedule as sched_repo  # noqa: E402

SOURCE = 'ths_industry_daily_net'
TARGET_TABLE = 'market_data.sector_fund_flow_daily'
STATE_FILE = 'sector_fund_state.json'
#: 少于这个行数就当"半截榜"不写：这个口正常是 90 个行业，抓到 20 行说明源在抖，
#: 写进去会让当天的榜头名是个统计残影（与 `completeness` 那条"半截比缺数更坏"同理）。
MIN_ROWS = 60
#: 归属日允许的最老程度：跨零点补跑够用就行。超过就说明"这份即时榜已经不属于那一天了"。
MAX_AGE_DAYS = 2

UPSERT = """
insert into market_data.sector_fund_flow_daily
 (trade_date, sector_name, source, industry_index, pct, inflow_yi, outflow_yi, net_yi,
  firms, leader, leader_pct, captured_at)
values (%(trade_date)s,%(sector_name)s,%(source)s,%(industry_index)s,%(pct)s,%(inflow_yi)s,
        %(outflow_yi)s,%(net_yi)s,%(firms)s,%(leader)s,%(leader_pct)s,now())
on conflict (trade_date, sector_name, source) do update set
  industry_index = excluded.industry_index, pct = excluded.pct,
  inflow_yi = excluded.inflow_yi, outflow_yi = excluded.outflow_yi, net_yi = excluded.net_yi,
  firms = excluded.firms, leader = excluded.leader, leader_pct = excluded.leader_pct,
  captured_at = now()
"""


def _state_path() -> Path:
    from duanxian.paths import data_path
    d = Path(data_path('ingest'))
    d.mkdir(parents=True, exist_ok=True)
    return d / STATE_FILE


def read_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    p = _state_path()
    tmp = p.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, p)


def already_written(trade_date: str) -> dict | None:
    st = read_state()
    if st.get('trade_date') == trade_date and (st.get('written') or 0) > 0:
        return st
    return None


def attribution(now: dt.datetime) -> tuple[dt.date, bool]:
    """(归属交易日, 今天是不是交易日)。判据只走 `reference_data.trade_calendar`。"""
    open_today = sched_repo.is_trade_day(now.date())
    latest = engine.scalar(
        'select max(calendar_date) from reference_data.trade_calendar '
        'where is_open and calendar_date <= %s', (now.date(),), role='reader')
    return (latest, bool(open_today))


def fetch() -> list[dict]:
    """抓「即时」榜。akshare 的 3日/5日/10日 窗口在本机 1.18.97 已坏
    （`Length mismatch: Expected axis has 11 elements, new values have 8`）⇒ 只有即时一档。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'vr'))
    import astock
    df = astock._akshare().stock_fund_flow_industry(symbol='即时')
    out = []
    for _, row in df.iterrows():
        name = str(row.get('行业') or '').strip()
        if not name or name.isdigit():
            continue
        net = _f(row.get('净额'))
        out.append({'sector_name': name, 'source': SOURCE,
                    'industry_index': _f(row.get('行业指数')), 'pct': _f(row.get('行业-涨跌幅')),
                    'inflow_yi': _f(row.get('流入资金')), 'outflow_yi': _f(row.get('流出资金')),
                    'net_yi': net,
                    'firms': None if _f(row.get('公司家数')) is None else int(_f(row.get('公司家数'))),
                    'leader': str(row.get('领涨股') or '').strip() or None,
                    'leader_pct': _f(row.get('领涨股-涨跌幅'))})
    return out


def _f(v):
    try:
        if v is None or (isinstance(v, float) and v != v):
            return None
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='行业资金流逐日落库（归属日由交易日历判，报文无日期）')
    ap.add_argument('--date', help='归属日；只接受今天或最近一个已收盘交易日')
    ap.add_argument('--rerun', action='store_true', help='同一天重采（默认跳过，见模块文档第 3 条）')
    ap.add_argument('--force', action='store_true', help='跳过非交易日/归属日过远两道闸（调试用，写出的归属日不可信）')
    ap.add_argument('--dry-run', action='store_true', help='只打印会写什么，不落库')
    args = ap.parse_args(argv)

    now = dt.datetime.now()
    latest, open_today = attribution(now)
    if latest is None:
        print('✗ 交易日历读不到（reference_data.trade_calendar），归属日判不了 —— 宁可不写')
        return 1
    own = now.date() if open_today else latest
    if args.date:
        want = dt.date.fromisoformat(args.date)
        if want not in (now.date(), latest):
            print('✗ --date 只接受 %s（最近一个已收盘交易日）或 %s（今天）；'
                  '这个口没有历史查询能力，更早的日期会被拒收 —— 传进去只会把今天的数字'
                  '盖成那天的归属日，那是造数据' % (latest, now.date()))
            return 2
        own = want
    age = (now.date() - own).days
    if not open_today and age > MAX_AGE_DAYS and not args.force:
        print('✗ 今天 %s 不是交易日，最近的交易日 %s 已经过去 %d 天（>%d）—— '
              '此刻抓到的即时榜已经不属于任何可归属的那一天，拒绝写。'
              '要调试加 --force（写出来的归属日不可信）。' % (now.date(), own, age, MAX_AGE_DAYS))
        return 2
    if not open_today:
        print('! 今天 %s 不是交易日，按 %s 归属（age=%d 天）%s'
              % (now.date(), own, age, '，--force 已给' if args.force else ''))
    done = already_written(own.isoformat())
    if done and not args.rerun:
        print('→ %s 已经采过（%s 行，ran_at=%s），本次跳过；确实要重采加 --rerun'
              % (own, done.get('written'), done.get('ran_at')))
        return 0

    print('=== 抓同花顺行业资金流（即时）===')
    try:
        rows = fetch()
    except Exception as exc:  # noqa: BLE001  接口坏要说清坏在哪，别写成"今天没有资金流"
        print('✗ 接口异常：%s: %s' % (type(exc).__name__, str(exc)[:160]))
        return 1
    print('  抓到 %d 个行业（正常约 90）' % len(rows))
    if len(rows) < MIN_ROWS:
        print('✗ 少于 %d 行，判为半截榜，不写（半截比缺数更坏：榜头会变成残影）' % MIN_ROWS)
        return 1
    top = sorted(rows, key=lambda r: -(r['net_yi'] or 0))[:3]
    for r in top:
        print('  净额前列：%s %+0.2f 亿（涨 %s%%，%s 家）' % (r['sector_name'], r['net_yi'] or 0,
                                                              r['pct'], r['firms']))
    if args.dry_run:
        print('DONE（--dry-run 未写库）；归属日会是 %s' % own)
        return 0

    batch = engine.query_one(
        """insert into operations.ingest_batch
             (source, source_path, target, row_count, actor, status, started_at, finished_at, message)
           values ('ths_industry_flow', 'ckhs.10jqka.com.cn/fund/date_industry/（akshare 即时档）',
                   %s, %s, 'ft_etl', 'running', now(), now(),
                   '归属交易日由日历判定（报文里没有日期字段）')
           returning batch_id""", (TARGET_TABLE, len(rows)), role='etl')      # ← 必须显式给写角色
    bid = (batch or {}).get('batch_id')
    n = engine.executemany(UPSERT, [{**r, 'trade_date': own} for r in rows], role='etl')
    if bid is not None:
        engine.execute("update operations.ingest_batch set status='ok', row_count=%s, finished_at=now() "
                       "where batch_id=%s", (n or len(rows), bid), role='etl')
    save_state({'trade_date': own.isoformat(), 'written': len(rows),
                'ran_at': now.isoformat(timespec='seconds'), 'batch_id': bid})
    print('=== 结果 ===\n  写入 %s 行，归属 %s，源 %s' % (len(rows), own, SOURCE))
    print('  从这一刻起这张表才有历史：**过去没有任何地方存过这份榜**，回填不了。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
