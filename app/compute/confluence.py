# -*- coding: utf-8 -*-
"""选股汇总（交叉命中榜）：把各条量化链**当天选出来的票**收成一票一行，看谁被多个判据同时点到。

用户 2026-10-13 的原话：「把倍量形态中筛选出来的 150 个和四维象限中筛选出来的 200 个，看下有没有
重复出现的，附上趋势评分」+「把我这边的量化、复盘等各种结果的内容进行一个汇总展示和交叉赋分，
重点关注重复出现的票」。两条裁定：

* 源清单：倍量前 150、四维象限前 200、埋伏票前 150、突破·首破、龙虎榜（其余只作属性列）；
* 赋分口径：**命中榜数优先，其次各榜里最好的名次**。不做单一合成分 ——
  那种数会把「只在象限排第 1」和「四个榜都排第 120」并排放在一起，看着整齐但会骗人。

本文件唯一要守的纪律：**各榜的选法只住 `SOURCES` 这一处**（表、判据版本、排序、取数上限）。
那一屏页面显示什么名单，这里就必须数什么名单；两边各写一遍一定会漂，漂了这张表说的「重复出现」就是假的。
所以 `SOURCES[*].limit` 与各端点的默认 `limit` 由 `tests/test_confluence.py` 钉住不许分叉。

实测 2026-10-12（写这份判据时先量的）：倍量 150 ∩ 象限 200 = **8 只**；象限 ∩ 首破 = 16 只；
倍量 ∩ 埋伏 = 3 只；三个榜同时命中的 **0 只**。也就是说"重复出现"本来就稀少，
这一屏的价值在把稀少的那几只捞出来，而不是造一个每次都有一堆高分的合成分。

用法：
    python -m app.compute.confluence                      # 最新有数的那一天
    python -m app.compute.confluence --date 2026-10-12
    python -m app.compute.confluence --since 2025-12-03 --skip-done --max-minutes 60
    python -m app.compute.confluence --dry-run            # 只打前 20 名不写库

退出码：0 成功 / 1 没有可算的交易日 / 2 一趟都没写进去 / 3 有场次失败 / 4 预算用完还剩几场。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from typing import Any, Dict, List, Optional, Sequence

from app.compute import system_entry as SE
from app.db import engine

SPEC_VERSION = 'confl-2'
# 为什么升号（2026-10-14）：交叉源从 5 个变 6 个（加进交易体系榜 sysentry）。
# `hits`/`source_count` 的分母变了 —— 不升号的话，同一张表里"命中 5 榜"在 10-13 之前是满分、
# 之后不是，而屏幕上看不出任何区别。旧行留在 confl-1 对账，不与新批并排。
TABLE = 'analytics.confluence_daily'


class Source:
    """一个源 = 一条链当天交出的那份名单，连它的选法一起。"""

    __slots__ = ('key', 'label', 'meaning', 'sql', 'limit', 'metric', 'board')

    def __init__(self, key: str, label: str, meaning: str, sql: str,
                 limit: Optional[int], metric: str, board: str):
        self.key = key
        self.label = label
        self.meaning = meaning
        self.sql = sql
        self.limit = limit           # None = 这一场该源有多少交多少（首破、龙虎榜）
        self.metric = metric         # 榜就是按它排的，所以原值要一起交回页面
        self.board = board           # 它在哪一屏（读侧给链接用）


#: 各榜选法。**每个源的 SQL 都必须带 %(day)s**，并且名次由 `row_number()` 在这条链自己的排序下数出来，
#: 不是拿全市场的分位冒充 —— 象限榜第 30 名和首破榜第 30 名不是一回事，只有"名次/该榜榜长"可比。
SOURCES: Sequence[Source] = (
    Source(
        key='voldbl', label='倍量形态前 150', board='/volume-double',
        meaning='倍量榜当天按倍量次数（dbl_count）排下来的前 150 只，与页面上那份名单同一选法。',
        metric='dbl_count', limit=150,
        sql="""
        with b as (select security_code c, dbl_count m,
                          row_number() over (order by dbl_count desc nulls last,
                                             dev_med asc nulls last, security_code) rn
                     from analytics.volume_double
                    where trade_date = %(day)s)
        select c security_code, m metric, rn from b where rn <= %(limit)s
        """),
    Source(
        key='quadrant', label='四维象限前 200', board='/quadrant',
        meaning='四维象限（oper = 0.40×个股质量 + 0.60×环境共振）按综合分排下来的前 200 只。',
        metric='oper_value', limit=200,
        sql="""
        with b as (select security_code c, value m,
                          row_number() over (order by value desc nulls last, security_code) rn
                     from analytics.score
                    where trade_date = %(day)s and capability_key = 'oper_score')
        select c security_code, m metric, rn from b where rn <= %(limit)s
        """),
    Source(
        key='ambush', label='埋伏票前 150', board='/volume-breakout',
        meaning='埋伏票（左栏）按埋伏分排下来的前 150 只；严档那只角标在 metrics.strict 里一起带出来。',
        metric='ambush_score', limit=150,
        sql="""
        with b as (select a.security_code c, a.ambush_score m, a.strict_pass st,
                          row_number() over (order by a.strict_pass desc nulls last,
                                             a.ambush_score desc nulls last, a.security_code) rn
                     from analytics.ambush_setup a
                    where a.trade_date = %(day)s and a.spec_version = 'ambush-1')
        select c security_code, m metric, st strict, rn from b where rn <= %(limit)s
        """),
    Source(
        key='first_break', label='突破·首破', board='/volume-breakout',
        meaning='当天全部"首破"（收敛后第一次同时站上 MA60 并放量 ≥1.6×）的票 —— '
                '这是右栏里唯一"已确认"的那一档，所以不截断，有多少算多少。',
        metric='break_vol_ratio', limit=None,
        sql="""
        with b as (select security_code c, break_vol_ratio m,
                          row_number() over (order by break_vol_ratio desc nulls last,
                                             converge_days desc nulls last, security_code) rn
                     from analytics.volume_breakout_signal
                    where trade_date = %(day)s and spec_version = 'breakout-sv-2'
                      and signal_type = 'first_break')
        select c security_code, m metric, rn from b
        """),
    Source(
        key='dgt', label='龙虎榜', board='/first-board',
        meaning='当天上龙虎榜的票，按净买额排（一只票可能因多个原因上榜，取最大那笔净买额作名次依据，'
                '上榜原因只数放在 metrics.reasons）。资金面事实，不是形态判据。',
        metric='net_buy', limit=None,
        sql="""
        with b as (select security_code c, max(net_buy) m, count(*) reasons,
                          row_number() over (order by max(net_buy) desc nulls last,
                                             security_code) rn
                     from market_data.dragon_tiger_detail
                    where trade_date = %(day)s
                    group by security_code)
        select c security_code, m metric, rn, reasons from b
        """),
    # 交易体系榜（用户 2026-10-14 点名的组合判据）。它本身就是"几条链同时成立"的产物，
    # 所以它进交叉命中是有意义的：一只票既走完体系、又被倍量/埋伏/首破同时点到，说明两套
    # 独立算法在同一只票上撞见了。榜内按缺口幅度排（缺口越大 = 抢筹越急）；不给合成总分。
    Source(
        key='sysentry', label='交易体系三条件全过', board='/confluence',
        meaning='近 20 场有涨停 × 向上整段跳空缺口三天不回补 × 缩量回调后今日阳线确认。'
                '判据只住 `app/compute/system_entry.py`，这一源读它落好的行；'
                '名次按缺口幅度排（缺口越大 = 抢筹越急），没有合成总分。',
        metric='gap_pct', limit=None,
        sql=f"""
        with b as (select security_code c, gap_pct m,
                          row_number() over (order by gap_pct desc nulls last,
                                             pullback_max_vol_ratio asc nulls last, security_code) rn
                     from analytics.system_entry
                    where trade_date = %(day)s and spec_version = '{SE.SPEC_VERSION}' and entry_ok)
        select c security_code, m metric, rn from b
        """),
)

SOURCE_KEYS = tuple(s.key for s in SOURCES)
SOURCE_LABELS = {s.key: s.label for s in SOURCES}


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float('inf'), float('-inf')) else f


def _day_params(s: Source, day: dt.date) -> Dict[str, Any]:
    p: Dict[str, Any] = {'day': day}
    if s.limit:
        p['limit'] = s.limit
    return p


def fetch_source(s: Source, day: dt.date) -> List[Dict[str, Any]]:
    """一个源当天交出的名单（带名次与判据原值）。读不到就抛 —— 不返回空表冒充"这一场没有票"。"""
    return engine.query(s.sql, _day_params(s, day), role='reader') or []


def day_sources(day: dt.date) -> Dict[str, List[Dict[str, Any]]]:
    """逐源取数。某个源失败**不带走整场**：它记进 absent 列表，hits 的分母跟着缩。

    这一条就是"埋伏链今天没跑"与"这只票不被埋伏判据认同"的区别 ——
    不分开就会把上游没算读成判据反对，那是最贵的一类假读数。
    """
    got: Dict[str, List[Dict[str, Any]]] = {}
    failed: Dict[str, str] = {}
    for s in SOURCES:
        try:
            got[s.key] = fetch_source(s, day)
        except Exception as e:      # noqa: BLE001
            failed[s.key] = f'{type(e).__name__}: {e}'
    if failed:
        got['__failed__'] = [failed]        # type: ignore[list-item]
    return got


def merge(day: dt.date, per_source: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """把各源的名单并成一票一行：命中榜数 + 各榜名次/榜长/原值 + 最好的那个名次。"""
    failed = per_source.get('__failed__') or [{}]
    absent = sorted(failed[0].keys()) if failed else []
    present = [s for s in SOURCES if s.key in per_source and s.key != '__failed__']

    sizes = {s.key: len(per_source.get(s.key) or []) for s in SOURCES}
    acc: Dict[str, Dict[str, Any]] = {}
    for s in present:
        for r in (per_source.get(s.key) or []):
            code = str(r['security_code'])
            row = acc.setdefault(code, {
                'trade_date': day, 'security_code': code, 'spec_version': SPEC_VERSION,
                'ranks': {}, 'sizes': {}, 'metrics': {},
            })
            rank = int(r['rn'])
            row['ranks'][s.key] = rank
            row['sizes'][s.key] = sizes[s.key]
            mv = _num(r.get('metric'))
            row['metrics'][s.key] = {'metric': mv}
            if s.key == 'dgt' and r.get('reasons') is not None:
                row['metrics'][s.key]['reasons'] = int(r['reasons'])
            if s.key == 'ambush':
                row['metrics'][s.key]['strict'] = bool(r.get('strict'))

    rows: List[Dict[str, Any]] = []
    for code, a in acc.items():
        # 名次分位：该榜名次 / 该榜这一场的榜长。跨榜比较只用它（榜长不一样，名次本身不可比）。
        pcts = {k: (a['ranks'][k] / a['sizes'][k] * 100.0 if a['sizes'][k] else None)
                for k in a['ranks']}
        usable = {k: v for k, v in pcts.items() if v is not None}
        best_key = min(usable, key=lambda k: usable[k]) if usable else None
        order = sorted(a['ranks'], key=lambda k: (usable.get(k, 1e9), k))
        missing = [k for k, v in a['metrics'].items() if v.get('metric') is None]
        rows.append({
            'trade_date': day, 'security_code': code, 'spec_version': SPEC_VERSION,
            'hits': len(a['ranks']),
            'hit_sources': order,
            # 这三格在 Python 侧一直是 dict，直到 `write` 才转 jsonb 字符串 ——
            # 提前 dumps 会让 dry-run 的回显、测试的断言都在字符串上做，读不出形状
            'ranks': a['ranks'],
            'sizes': a['sizes'],
            'metrics': a['metrics'],
            'best_source': best_key,
            'best_rank': a['ranks'].get(best_key) if best_key else None,
            'best_rank_pct': round(usable[best_key], 3) if best_key else None,
            'source_count': len(present),
            'sources_absent': ','.join(absent) if absent else None,
            'missing': ','.join(sorted(missing)) if missing else None,
        })
    # 主尺子是命中榜数，其次才是各榜最好名次（用户 2026-10-13 选的口径）
    rows.sort(key=lambda r: (-r['hits'], r['best_rank_pct'] if r['best_rank_pct'] is not None else 1e9,
                             r['security_code']))
    return rows


def compute_day(day: dt.date) -> List[Dict[str, Any]]:
    return merge(day, day_sources(day))


def write(day: dt.date, rows: List[Dict[str, Any]]) -> int:
    engine.execute(f"delete from {TABLE} where trade_date = %s and spec_version = %s",
                   (day, SPEC_VERSION), role='etl')
    if not rows:
        return 0
    # 三格 jsonb 在这里才转字符串（psycopg 不认 dict → `cannot adapt type 'dict'`），
    # 上面 Python 侧一直是 dict，测试与 dry-run 都按 dict 读。
    payload = []
    for r in rows:
        p = dict(r)
        for k in ('ranks', 'sizes', 'metrics'):
            p[k] = json.dumps(p[k], ensure_ascii=False, default=str)
        payload.append(p)
    engine.executemany(f"""
        insert into {TABLE} (trade_date, security_code, spec_version,
                             hits, hit_sources, ranks, sizes, metrics,
                             best_source, best_rank, best_rank_pct,
                             source_count, sources_absent, missing)
        values (%(trade_date)s, %(security_code)s, %(spec_version)s,
                %(hits)s, %(hit_sources)s, %(ranks)s::jsonb, %(sizes)s::jsonb, %(metrics)s::jsonb,
                %(best_source)s, %(best_rank)s, %(best_rank_pct)s,
                %(source_count)s, %(sources_absent)s, %(missing)s)
        """, payload, role='etl')
    return len(rows)


def refresh(day: dt.date) -> Dict[str, Any]:
    rows = compute_day(day)
    n = write(day, rows)
    dist: Dict[int, int] = {}
    for r in rows:
        dist[r['hits']] = dist.get(r['hits'], 0) + 1
    return {'day': str(day), 'rows': n, 'dist': dist,
            'source_count': rows[0]['source_count'] if rows else 0,
            'absent': (rows[0]['sources_absent'] if rows else None) or ''}


def done_days(since: dt.date, end: dt.date) -> set:
    """**源齐全**才算"这一场已经算过"。

    为什么不能只看"有没有行"：某条链当天没跑时，这一场照样会写出行，但它的
    `source_count` 少一个 —— 那种行留着，`--skip-done` 就永远不会补它，
    于是那一天的「只命中 1 榜」被永久钉在一个因为缺源而偏低的事实上。
    等那条链补完，重跑这一场才会把分母补回来。
    """
    rows = engine.query(f"select trade_date d from {TABLE} "
                        f"where spec_version = %s and trade_date between %s and %s "
                        f"group by 1 having min(source_count) = %s",
                        (SPEC_VERSION, since, end, len(SOURCES)), role='reader') or []
    return {r['d'] for r in rows if isinstance(r['d'], dt.date)}


def latest_day() -> Optional[dt.date]:
    r = engine.query("select max(trade_date) d from analytics.score", role='reader')
    d = (r or [{}])[0].get('d')
    return d if isinstance(d, dt.date) else None


def trade_days(end: dt.date, recent: int) -> List[dt.date]:
    rows = engine.query("select distinct trade_date d from analytics.score "
                        "where trade_date <= %s order by trade_date desc limit %s",
                        (end, recent), role='reader')
    return sorted(r['d'] for r in rows or [])


def run(day: Optional[dt.date] = None, recent: int = 1, since: Optional[dt.date] = None,
        dry_run: bool = False, skip_done: bool = False,
        max_minutes: Optional[float] = None) -> int:
    end = day or latest_day()
    if not end:
        print('没有可算的交易日（analytics.score 是空的？）')
        return 1
    days = trade_days(end, recent)
    if since:
        days = [d for d in trade_days(end, 400) if d >= since]
    if dry_run:
        rows = compute_day(days[-1])
        dist: Dict[int, int] = {}
        for r in rows:
            dist[r['hits']] = dist.get(r['hits'], 0) + 1
        print(f'{days[-1]} 至少命中一个榜的票 {len(rows)} 只｜命中榜数分布 {dict(sorted(dist.items()))}')
        print('前 20 名（命中榜数 → 各榜名次）：')
        for r in rows[:20]:
            step = '、'.join(f'{SOURCE_LABELS.get(k, k)} #{r["ranks"][k]}/{r["sizes"][k]}'
                             for k in r['hit_sources'])
            print(f'  {r["security_code"]} 命中 {r["hits"]} 榜｜{step}')
        return 0

    if skip_done and len(days) > 1:
        done = done_days(days[0], days[-1])
        skipped = [d for d in days if d in done]
        days = [d for d in days if d not in done]
        print(f'已算过 {len(skipped)} 场（同 {SPEC_VERSION}），这一遍只补剩下 {len(days)} 场')
    if not days:
        print('这一批都已经算过，不需要重跑（要重来就去掉 --skip-done）')
        return 0

    total = failed = 0
    start = time.monotonic()
    for i, d in enumerate(days, 1):
        if max_minutes and (time.monotonic() - start) / 60.0 >= max_minutes:
            print(f'⏸ 自限预算 {max_minutes:.0f} 分钟用完：{len(days) - i + 1} 场没轮到（从 {d} 起）')
            return 4
        try:
            info = refresh(d)
        except Exception as e:      # noqa: BLE001  一场算崩不带走整批
            failed += 1
            print(f'[{i}/{len(days)}] {d} 失败：{type(e).__name__}: {e} —— 跳过这一场继续')
            continue
        total += info['rows']
        print(f'[{i}/{len(days)}] {info["day"]} 写 {info["rows"]} 行｜命中分布 {info["dist"]}｜'
              f'有数的源 {info["source_count"]}/{len(SOURCES)}'
              + (f'｜没数：{info["absent"]}' if info['absent'] else ''))
    print(f'合计写入 {total} 行（spec_version={SPEC_VERSION}）'
          + (f'，另有 {failed} 场失败' if failed else ''))
    return 3 if failed else (0 if total else 2)


def main() -> int:
    ap = argparse.ArgumentParser(description='选股汇总（交叉命中榜）：analytics.confluence_daily')
    ap.add_argument('--date', default='')
    ap.add_argument('--recent', type=int, default=1)
    ap.add_argument('--since', default='')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-done', action='store_true', help='回算用：已经算过的场次跳过')
    ap.add_argument('--max-minutes', type=float, default=None, help='回算用：到点收手')
    a = ap.parse_args()
    return run(day=dt.date.fromisoformat(a.date) if a.date else None,
               recent=a.recent,
               since=dt.date.fromisoformat(a.since) if a.since else None,
               dry_run=a.dry_run, skip_done=a.skip_done, max_minutes=a.max_minutes)


if __name__ == '__main__':
    raise SystemExit(main())
