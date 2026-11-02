# -*- coding: utf-8 -*-
"""把「X 名次 + Y 名次的合计名次」逐场逐票落进 `analytics.xy_rank_daily`。

用户 2026-10-28 点名：这一档**要作为数据加工的一环每天跑**，别每个屏各加各的
（首页「首板分析」要摆近 5 场的 `21-20-19-18-5`，「名次稳步前进榜」一直在自己的 SQL 里现加同一个和）。
两个加数都是库里已入库的（`analytics.score.rank_in_market`，实测 2026-10-28 八个键各 5,570 行、
名次非空 5,570/5,570），**这里不加判据、不重排名**，只做一件事：把那一天的 X 名次与 Y 名次配成一行、
把和算出来存着。

三条口径上的硬规定（都写在 DDL 的列注释里，这里是实现）：
1. `rank_sum = rank_x + rank_y` ⇒ **越小越靠前**。它是"两个名次之和"，不是"对合计再排一次名"，
   也不是 `oper_score` 的合成分名次（同一只票同一天实测 100 对 25，谁也不许替谁）。
2. **两轴都有名次才落行**：少一轴就没有"和"可算 ⇒ 那只票那一天整行不落，
   读侧那一格是 `—`（读不到），不是 0、不是"没上榜"。
3. 旧系统迁入批（`analytics.score.is_stale = true`）**进不了这张表**：2026-10-28 回查那一批 59,756 行的
   `rank_in_market` 全是 NULL ⇒ 少一轴就没有和可算。`stale` 因此当前全库 0 行 true，
   它是"哪天旧批也重算出名次"的哨兵，读侧一旦看到 true 就要标〈旧基准〉、不参与前进/后退比较。

用法：
    python -m app.compute.xy_rank                        # 默认：库里最近一场往前 40 场
    python -m app.compute.xy_rank --date 20261028 --recent 1
    python -m app.compute.xy_rank --start 20250828 --end 20261028   # 历史回补（分片跑）
    python -m app.compute.xy_rank --date 20261028 --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from app.db import engine

TABLE = 'analytics.xy_rank_daily'
SPEC = 'xyr-1'
#: X / Y 两根轴的 capability_key 只住这里一份。`duanxian/quant.py` 的「名次稳步前进榜」
#: 在自己的 SQL 里也用了这两个键，`tests/test_xy_rank.py` 钉着两边不许分叉。
X_KEY = 'stock_quality'
Y_KEY = 'env_resonance'
#: 一次写多少个交易日（回补时分片用：一场约 5,570 行，一整年 242 场 ≈ 135 万行，
#: 一条语句写完会把事务撑大，按 40 场一片实测每片几秒）
CHUNK_SESSIONS = 40

#: 一天一条语句：把两轴配成一行、算和、带上任一加数是否来自旧批。
#: `count(*) over ()` 直接给"当日两轴都有名次的只数"＝这一列的分母（母体有上限时屏上不许说"全场"）。
_ONE_DAY = f"""
insert into {TABLE} (security_code, trade_date, spec_version, rank_x, rank_y, rank_sum, universe, stale)
select p.security_code, p.trade_date, %(spec)s, p.rx, p.ry, p.rx + p.ry,
       count(*) over (),
       (p.sx or p.sy)
  from (select s.security_code, s.trade_date,
               max(s.rank_in_market) filter (where s.capability_key = %(x)s) as rx,
               max(s.rank_in_market) filter (where s.capability_key = %(y)s) as ry,
               bool_or(s.is_stale)   filter (where s.capability_key = %(x)s) as sx,
               bool_or(s.is_stale)   filter (where s.capability_key = %(y)s) as sy
          from analytics.score s
         where s.capability_key in (%(x)s, %(y)s)
           and s.trade_date = %(d)s::date
           and s.rank_in_market is not null
         group by 1, 2) p
 where p.rx is not null and p.ry is not null
on conflict (security_code, trade_date, spec_version)
  do update set rank_x = excluded.rank_x, rank_y = excluded.rank_y, rank_sum = excluded.rank_sum,
                universe = excluded.universe, stale = excluded.stale, calculated_at = now()
"""


def sessions(end: dt.date, n: int) -> list[dt.date]:
    """以 `end` 结尾、往前 `n` 个交易日（交易日按 `analytics.score` 里有行的日子判，不按日历）。"""
    rows = engine.query(
        'select distinct trade_date from analytics.score '
        'where capability_key = %s and trade_date <= %s::date '
        'order by trade_date desc limit %s', (X_KEY, end, n), role='reader')
    return sorted(dt.date.fromisoformat(str(r['trade_date'])[:10]) for r in rows)


def latest() -> dt.date:
    d = engine.scalar('select max(trade_date) from analytics.score where capability_key = %s', (X_KEY,))
    if d is None:
        raise SystemExit('`analytics.score` 里一行都没有（加工段 `scores` 步没跑过）—— 这一步没有原料')
    return dt.date.fromisoformat(str(d)[:10])


def run_day(day: dt.date, dry: bool = False) -> dict:
    """写一天并**回读自证**：写完立刻查这一天，核 `rank_sum == rank_x + rank_y` 与量级上界。"""
    if not dry:
        engine.execute(_ONE_DAY, {'spec': SPEC, 'x': X_KEY, 'y': Y_KEY, 'd': day.isoformat()},
                       role='etl')
    back = engine.query(
        f'select count(*)::int n, min(rank_sum)::int lo, max(rank_sum)::int hi, '
        f'       max(universe)::int univ, count(*) filter (where stale)::int stale_rows, '
        f'       count(*) filter (where rank_sum <> rank_x + rank_y)::int bad_sum, '
        f'       count(*) filter (where rank_x > universe or rank_y > universe)::int bad_rank '
        f'  from {TABLE} where trade_date = %s::date and spec_version = %s',
        (day.isoformat(), SPEC), role='reader')
    got = dict(back[0]) if back else {'n': 0}
    got['universe'] = got.get('univ')
    if not dry and not got.get('n'):
        raise SystemExit(f'{day} 写完了却回读不到一行 —— 原料没了还是语句没生效？不许交"成功"')
    if got.get('n'):
        problems = []
        if int(got.get('bad_sum') or 0):
            problems.append(f'{got["bad_sum"]} 行 rank_sum ≠ rank_x + rank_y')
        if int(got.get('bad_rank') or 0):
            problems.append(f'{got["bad_rank"]} 行的名次大于当日只数（名次与分母不同批）')
        if int(got['hi']) > 2 * int(got['universe']):
            problems.append(f'最大和 {got["hi"]} 超过 2 × 当日只数 {got["universe"]}')
        if problems:
            raise SystemExit(f'{day} 自证不过：' + '；'.join(problems))
    return got


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='锚定交易日 YYYYMMDD 或 YYYY-MM-DD（与 --recent 一起用）')
    ap.add_argument('--recent', type=int, default=40, help='锚定日往前几场（默认 40）')
    ap.add_argument('--start', help='区间起日（给了 --start/--end 就覆盖 --date/--recent）')
    ap.add_argument('--end', help='区间止日')
    ap.add_argument('--chunk', type=int, default=CHUNK_SESSIONS, help='回补时多少场报一次进度')
    ap.add_argument('--dry-run', action='store_true', help='只回读不写（新的一天 dry-run 读不到行是正常的）')
    args = ap.parse_args(argv)

    def norm(s: str) -> dt.date:
        return dt.datetime.strptime(s.replace('-', ''), '%Y%m%d').date()

    if args.start:
        days = sessions(norm(args.end) if args.end else latest(), 10 ** 6)
        lo = norm(args.start)
        hi = norm(args.end) if args.end else latest()
        days = [d for d in days if lo <= d <= hi]
    else:
        end = norm(args.date) if args.date else latest()
        days = sessions(end, args.recent)

    if not days:
        print('这一片一场都没有（`analytics.score` 里没有可配对的场次）—— 不是"都写好了"，是原料为空')
        return 2

    print(f'=== {SPEC}：{days[0]} .. {days[-1]} 共 {len(days)} 场'
          f'{"（--dry-run，只回读）" if args.dry_run else ""} ===')
    total = 0
    for i, d in enumerate(days, 1):
        got = run_day(d, args.dry_run)
        total += int(got.get('n') or 0)
        if i == 1 or i % max(1, args.chunk) == 0 or i == len(days):
            print(f'  {i}/{len(days)} {d}：{got.get("n", 0)} 行'
                  f'｜名次和 {got.get("lo", "—")}~{got.get("hi", "—")}'
                  f'｜当日两轴都有名次的只数 {got.get("universe", got.get("univ", "—"))}'
                  f'｜旧基准 {got.get("stale_rows", 0)} 行')
    print(f'合计 {total} 行（{"回读" if args.dry_run else "写入"}口径：一天一条 INSERT..SELECT，'
          f'冲突就更新那一行，重复跑幂等）')
    cov = engine.query(f'select count(distinct trade_date) s, min(trade_date) lo, max(trade_date) hi '
                       f'from {TABLE} where spec_version = %s', (SPEC,), role='reader')[0]
    print(f'表里现在：{cov["s"]} 场 {str(cov["lo"])[:10]} .. {str(cov["hi"])[:10]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
