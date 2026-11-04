# -*- coding: utf-8 -*-
"""「股性」与「近 10 场日均振幅」的写侧：一场一行×一只票，落 `analytics.stock_character_daily`。

用户 2026-11-03 点名给突破信号页左栏「埋伏票·左侧机会」加两列，原话是
「是不是要加一个 股性（你看下这个参数怎么定义，最好也要加入每日的数据计算）和 近10个交易日日平均振幅」。
"最好也要加入每日的数据计算"就是这一张表存在理由：这两个数**不读侧现算**，
每天跟着加工链算一次落库，屏上读的是落库行（读侧只 join + 出口清洗，不重算分位、不自己定档）。

---- 三条腿与档位（判据只住本文件，中文档名也只在这里）----

「股性」不做成一个加权总分——上次那句"达标后的逐日涨跌幅"就是因为一个数糊住了三件事被点名看不懂。
这里给三条腿各自的**当日全场分位**，再取**中位数**定档：

    涨停基因  `gene_pctile`      ← 近 250 场**封住**涨停的天数（判据在 `limit_gene`，与首板分析同一把尺）
    摆动性    `amp_250_pctile`   ← 近 250 场日均振幅 (high − low) ÷ pre_close × 100
    钱活跃度  `turn_250_pctile`  ← 近 250 场日均换手率

    中位数 ≥70 → strong（股性强）；40~70 → middle（股性中）；<40 → weak（股性弱）
    任一条腿判不了 ⇒ 整档 NULL。**不拿两条腿硬凑一个看起来像的档**，也不补 50（补 50 会把"判不了"画成"中性"）。

取中位数而不是加权：一条腿特别差要留在结论里（换手极低但老封板的票，加权会把它抬成"股性好"）。

---- 窗口按**市场场次**取起点，不是按这只票自己的根数 ----

"近 10 个交易日"说的是全市场的倒数第 10 场到今天。按这只票自己的 10 根算，
长期停牌的票会拿半年前的 10 根冒充"近 10 场"——那一天它确实停牌，摆动是"没有可测"，不是"平静"。
真有的行数一并落库（`bars_10`/`bars_250`），不足门槛就不给数并在 `missing` 点名：
次新股没有 250 场可说，屏幕上那一格是 `—` 加一句"这只票上市不足 120 场"，不是 0。

---- 缺数是 NULL，0 是读数 ----

`seal_250 = 0` 是"这 250 场一次都没封住"（真实、可排名）；`seal_250 IS NULL` 是"那几天档位价没读到，判不了"。
两者混成一个数，就是把缺数排成最差——本仓反复钉的那条红线。

用法：
    python -m app.compute.stock_character --date 2026-11-03          # 只算这一场
    python -m app.compute.stock_character --date 2026-11-03 --recent 3   # 以该日结尾的三场（每日链这样传）
    python -m app.compute.stock_character --recent 250               # 最近 250 场
    python -m app.compute.stock_character --since 2025-11-01         # 从这一天到库里最新一场
    python -m app.compute.stock_character --recent 5 --dry-run       # 只算不写
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
from bisect import bisect_right
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from app.db import engine                                     # noqa: E402
from app.compute import limit_up_pool, limit_gene as gene      # noqa: E402

TABLE = 'analytics.stock_character_daily'
SPEC_VERSION = 'stockchar-1'

#: 两条窗口的长度（市场场次数）。250 场 ≈ 一年，是"股性"这种慢变量的最低样本；
#: 10 场是用户点名的那一列（近 10 个交易日日均振幅）。
CHAR_SESSIONS = 250
AMP_SESSIONS = 10
#: 窗口起点定了，但**这只票真有的行**要够多才给数：不足门槛就是判不了，不是"振幅小"。
#: 8/10 与 120/250 是这么定的：允许少量停牌日（一次临时停牌两三天很正常），
#: 但过半缺失的那只票算出来的"日均"已经不是这 10 场的日均了。
AMP_MIN_BARS = 8
CHAR_MIN_BARS = 120
#: 分位的最低样本：与 `allocator._pct_rank` 同一条规矩（3 个点算出的分位是噪音）。
PCTILE_MIN_SAMPLE = 5

#: 档位的门槛（作用在三腿分位的中位数上，单位＝分位 0~100）
CHAR_STRONG_MIN = 70.0
CHAR_MIDDLE_MIN = 40.0
#: 中文档名只住这里：读侧原样转交，界面不自己翻译（屏上念的词与库里的键必须一份出处）
CHAR_LEVEL_LABELS = {'strong': '股性强', 'middle': '股性中', 'weak': '股性弱'}

# 缺数点名用的短码（进 `missing`，界面悬停念的就是它）
GAP_SESSIONS = 'no_sessions'                      # 交易日历/日线读不到场次
GAP_AMP_BARS = f'bars_10_lt{AMP_MIN_BARS}'        # 近 10 场真有的行不足 8 根 ⇒ 振幅判不了
GAP_CHAR_BARS = f'bars_250_lt{CHAR_MIN_BARS}'     # 近 250 场真有的行不足 120 根 ⇒ 三腿全判不了
GAP_AMP_ROWS = 'amp_inputs_missing'              # 有行但缺 high/low/pre_close ⇒ 那几天不进分母（全缺才点名）
GAP_TURN_ROWS = 'turnover_missing'               # 换手率整批没值（不是"换手 0"）
GAP_GENE_LIMIT = gene.GAP_HIST_LIMIT_UP          # 档位价缺失 ⇒ 涨停基因判不了（与首板榜同一个码）
GAP_CONSEC = gene.GAP_CONSEC_SOURCE              # lup-1 整批读回空（读不到库，不能写成"没有连板"）

#: 一只票一场的全部产出列（写库与读侧都用这一份名字，不靠字符串拼）
COLUMNS = ('amp_10', 'amp_10_pctile', 'bars_10', 'amp_250', 'amp_250_pctile',
           'turn_250', 'turn_250_pctile', 'seal_250', 'touch_250', 'consec_max_250',
           'gene_pctile', 'char_mid_pctile', 'char_level', 'char_level_label', 'bars_250')

SESSIONS_SQL = """
select trade_date from (
  select distinct trade_date from market_data.stock_daily
   where trade_date <= %(d)s order by trade_date desc limit %(n)s) z
 order by trade_date
"""

#: 全场逐只的振幅与换手：`back` 是"倒数第几个**市场**场次"（dense_rank over 日期倒序），
#: 所以停牌的那只票不会被"自己的前 10 根"顶替掉"全市场近 10 场"。
AGG_SQL = """
with s as (
  select security_code, trade_date, high, low, pre_close, turnover_rate_percent,
         dense_rank() over (order by trade_date desc) as back
    from market_data.stock_daily
   where trade_date between %(frm)s and %(to)s
)
select security_code,
       count(*) filter (where back <= %(n10)s)                       as bars_10,
       count(*) filter (where back <= %(n10)s and high is not null
                        and low is not null and pre_close > 0)      as days_10,
       avg(case when back <= %(n10)s and high is not null and low is not null
                  and pre_close > 0
                then (high - low) / pre_close * 100 end)            as amp_10,
       count(*)                                                      as bars_250,
       count(*) filter (where high is not null and low is not null
                        and pre_close > 0)                           as days_250,
       avg(case when high is not null and low is not null and pre_close > 0
                then (high - low) / pre_close * 100 end)            as amp_250,
       count(turnover_rate_percent)                                  as turn_days,
       avg(turnover_rate_percent)                                    as turn_250
  from s
 group by 1
"""

_UPSERT = f"""
insert into {TABLE}
    (security_code, trade_date, spec_version,
     amp_10, amp_10_pctile, bars_10, amp_250, amp_250_pctile, turn_250, turn_250_pctile,
     seal_250, touch_250, consec_max_250, gene_pctile,
     char_mid_pctile, char_level, char_level_label, bars_250, missing)
values (%(code)s, %(day)s, %(spec)s,
        %(amp_10)s, %(amp_10_pctile)s, %(bars_10)s, %(amp_250)s, %(amp_250_pctile)s,
        %(turn_250)s, %(turn_250_pctile)s, %(seal_250)s, %(touch_250)s, %(consec_max_250)s,
        %(gene_pctile)s, %(char_mid_pctile)s, %(char_level)s, %(char_level_label)s,
        %(bars_250)s, %(missing)s::jsonb)
on conflict (security_code, trade_date, spec_version) do update set
    amp_10 = excluded.amp_10, amp_10_pctile = excluded.amp_10_pctile, bars_10 = excluded.bars_10,
    amp_250 = excluded.amp_250, amp_250_pctile = excluded.amp_250_pctile,
    turn_250 = excluded.turn_250, turn_250_pctile = excluded.turn_250_pctile,
    seal_250 = excluded.seal_250, touch_250 = excluded.touch_250,
    consec_max_250 = excluded.consec_max_250, gene_pctile = excluded.gene_pctile,
    char_mid_pctile = excluded.char_mid_pctile, char_level = excluded.char_level,
    char_level_label = excluded.char_level_label, bars_250 = excluded.bars_250,
    missing = excluded.missing, calculated_at = now()
"""


# ---------------------------------------------------------------- 纯判据（可单测，不碰库）

def _round(v: Any, nd: int) -> Optional[float]:
    try:
        f = float(v)      # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(f) else round(f, nd)


def cross_pctile(pairs: Iterable[Tuple[str, Optional[float]]]) -> Dict[str, Optional[float]]:
    """当日**全场横截面**分位（0~100）：`x` 的分位＝「有值的票里 ≤x 的只数 ÷ 有值的只数」×100。

    · 同分同名（并列的几只在同一个分位上，不因为代码顺序被拆开）——与 `allocator._pct_rank` 一条规矩；
    · 没有值的那只票**不进分母也不占号**（它不该被排成"最差"），自己拿 NULL；
    · 有值的只数不足 `PCTILE_MIN_SAMPLE` ⇒ 全部 NULL：5,500 只票的分位有意义，3 只没有。

    （算法用排序 + bisect 而不是两两比较：全场 5,500 只，逐只再扫一遍就是 3,000 万次比较。）
    """
    have = [(c, float(v)) for c, v in pairs if v is not None and math.isfinite(float(v))]
    n = len(have)
    if n < PCTILE_MIN_SAMPLE:
        return {c: None for c, _v in pairs}
    srt = sorted(v for _, v in have)
    return {c: (None if v is None else round(100.0 * bisect_right(srt, v) / n, 2))
            for c, v in pairs}


def char_level_of(legs: Sequence[Optional[float]]) -> Tuple[Optional[float], Optional[str]]:
    """三条腿的分位 → (中位数, 档位键)。任一条腿缺 ⇒ 整档 NULL，不拿两条腿硬凑。"""
    if any(v is None for v in legs):
        return None, None
    s = sorted(float(v) for v in legs)
    mid = s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2.0
    mid = round(mid, 2)
    return mid, ('strong' if mid >= CHAR_STRONG_MIN
                 else 'middle' if mid >= CHAR_MIDDLE_MIN else 'weak')


def build_row(code: str, agg: dict, g_seal: Optional[int], g_touch: Optional[int],
              consec: Optional[int], gaps_g: Dict[str, str]) -> Tuple[dict, dict]:
    """一只票一场的原始读数（分位与档位是后面全场一起算的，这里只出原值）。

    返回 `(列, 缺数点名)`。门槛这条线一次判清：近 10 场看 `bars_10`、慢变量三条腿看 `bars_250`，
    所以一只次新股可能只有"近 10 场日均振幅"而没有"股性"——那一格是 `—`，不是 0，也不是"股性差"。
    """
    bars_10 = int(agg.get('bars_10') or 0)
    bars_250 = int(agg.get('bars_250') or 0)
    gap: Dict[str, str] = {}

    if bars_10 < AMP_MIN_BARS:
        amp10 = None
        gap['amp_10'] = GAP_AMP_BARS
    else:
        amp10 = _round(agg.get('amp_10'), 3)
        if amp10 is None:
            gap['amp_10'] = GAP_AMP_ROWS
    if bars_250 < CHAR_MIN_BARS:
        amp250 = turn250 = None
        gap['amp_250'] = gap['turn_250'] = GAP_CHAR_BARS
    else:
        amp250 = _round(agg.get('amp_250'), 3)
        if amp250 is None:
            gap['amp_250'] = GAP_AMP_ROWS
        turn250 = _round(agg.get('turn_250'), 4)
        if turn250 is None:
            gap['turn_250'] = GAP_TURN_ROWS
    # 涨停基因那一族的三种"没有数"要分得开：窗口太短（次新股）、档位价缺（判不了）、
    # 真的 250 场一次都没封住（0，可排名）。前两者 NULL、后者 0，混了就等于把缺数排成最差。
    seal = touch = None
    if bars_250 < CHAR_MIN_BARS:
        gap['seal_250'] = gap['touch_250'] = GAP_CHAR_BARS
    elif gaps_g.get('lu_sealed'):
        gap['seal_250'] = gap['touch_250'] = GAP_GENE_LIMIT
    else:
        seal, touch = g_seal, g_touch
    # `consec_max_250` 为 NULL 不是缺数：这 250 场里没有过可数的连板轮次（含从没封住板），
    # 屏上是 `—` 加一句"这段时间没连板"。只有**整批读不到库**才算判不了 —— 那一种由
    # compute_day 覆盖成 GAP_CONSEC（"没读到库"绝不能写成"没有连板"）。
    row = {'code': code, 'bars_10': bars_10, 'bars_250': bars_250,
           'amp_10': amp10, 'amp_250': amp250, 'turn_250': turn250,
           'seal_250': seal, 'touch_250': touch, 'consec_max_250': consec}
    return row, gap


def with_percentiles(rows: List[dict]) -> List[dict]:
    """把全场原值换成分位与档位（**当日横截面**，缺数不占号）。"""
    legs = {'gene': 'seal_250', 'amp': 'amp_250', 'turn': 'turn_250'}
    pct = {k: cross_pctile([(r['code'], r.get(v)) for r in rows]) for k, v in legs.items()}
    amp10 = cross_pctile([(r['code'], r.get('amp_10')) for r in rows])
    for r in rows:
        r['gene_pctile'] = pct['gene'].get(r['code'])
        r['amp_250_pctile'] = pct['amp'].get(r['code'])
        r['turn_250_pctile'] = pct['turn'].get(r['code'])
        r['amp_10_pctile'] = amp10.get(r['code'])
        mid, level = char_level_of([r['gene_pctile'], r['amp_250_pctile'], r['turn_250_pctile']])
        r['char_mid_pctile'] = mid
        r['char_level'] = level
        r['char_level_label'] = CHAR_LEVEL_LABELS.get(level or '')
    return rows


# ---------------------------------------------------------------- 取数与写库

def market_sessions(target: dt.date, n: int) -> List[dt.date]:
    """`target` 往前 n 个市场场次（升序，最后一个＝target 当天或之前最近的一场）。"""
    return [r['trade_date'] for r in (engine.query(
        SESSIONS_SQL, {'d': target, 'n': int(n)}, role='reader') or [])]


def compute_day(day: dt.date) -> Tuple[List[dict], Dict[str, Any]]:
    """算一场：全场每只票的振幅/换手/涨停基因 + 分位 + 档位。返回 `(行, 场次信息)`。"""
    ses = market_sessions(day, CHAR_SESSIONS)
    if not ses:
        return [], {'gap': GAP_SESSIONS, 'bars_10': 0, 'bars_250': 0, 'last': None}
    since250, since10 = ses[0], ses[max(0, len(ses) - AMP_SESSIONS)]
    aggs = engine.query(AGG_SQL, {'frm': since250, 'to': ses[-1],
                                  'n10': AMP_SESSIONS}, role='reader') or []
    by = {a['security_code']: a for a in aggs}
    # 涨停基因走全场那一种写法（`limit_gene.seal_touch_market`）：逐票拉 250 行×5,500 只
    # 是把整张日线表拖进内存数，且与首板榜用的是同一条判据（两写法成对且有一条对账测试钉着）。
    gm = gene.seal_touch_market(since250, ses[-1], gene.SEAL_EPS)
    consec, consec_batch_empty = gene.consec_by_code(
        list(by), since250, ses[-1], limit_up_pool.SPEC_VERSION)
    rows: List[dict] = []
    for code, agg in by.items():
        g = gm.get(code) or {}
        r, gap = build_row(code, agg, g.get('lu_sealed'), g.get('lu_touched'),
                           consec.get(code), dict(g.get('gap') or {}))
        if consec_batch_empty:
            gap['consec_max_250'] = GAP_CONSEC
        r['gap'] = gap
        rows.append(r)
    rows = with_percentiles(rows)
    info = {'since_250': since250, 'since_10': since10, 'last': ses[-1],
            'bars_250': len(ses), 'bars_10': len([s for s in ses if s >= since10]),
            'codes': len(rows)}
    return rows, info


def _payload(row: dict, day: dt.date) -> dict:
    d = {k: row.get(k) for k in COLUMNS}
    d.update({'code': row['code'], 'day': day, 'spec': SPEC_VERSION,
              'missing': json.dumps(row.get('gap') or {}, ensure_ascii=False)})
    return d


def write(day: dt.date, rows: List[dict]) -> int:
    """写一场。**先清这一场这一版**再插：只 upsert 会把上一批里已经不存在的票留下（残留行会被读成"有数"）。

    与 `intraday_spike`/`first_board_daily` 同一套处置（10-15 那次 6+48 条残留行的教训），
    授权里给 delete 就是为了这一步（只给 insert/update 会 permission denied）。
    """
    with engine.cursor(role='etl') as cur:
        cur.execute(f"delete from {TABLE} where trade_date = %s and spec_version = %s",
                    (day, SPEC_VERSION))
        cur.executemany(_UPSERT, [_payload(r, day) for r in rows])
    return len(rows)


def latest_day() -> Optional[dt.date]:
    return (engine.query_one(f"select max(trade_date) d from market_data.stock_daily", (),
                             role='reader') or {}).get('d')


def sessions_since(first: dt.date) -> List[dt.date]:
    rows = engine.query("select distinct trade_date from market_data.stock_daily "
                        "where trade_date >= %s order by 1", (first,), role='reader') or []
    return [r['trade_date'] for r in rows]


def run(days: Sequence[dt.date], dry: bool = False) -> int:
    """逐场算并写库。退出码：0 全好；3 有场降级（缺日线的场次、或某条腿整场判不了）。"""
    rc = 0
    for i, day in enumerate(days, 1):
        rows, info = compute_day(day)
        if not rows:
            print(f'  {day} 没算出任何行（{info.get("gap") or "窗口空"}）—— 这一场跳过，不写空批',
                  flush=True)
            rc = 3
            continue
        deg = sum(1 for r in rows if r.get('char_level') is None)
        no_pctile = sum(1 for r in rows if r.get('amp_10_pctile') is None)
        line = (f'  {day} 写 {len(rows)} 只｜近 10 场窗口 {info["bars_10"]} 场、'
                f'慢变量窗口 {info["bars_250"]} 场｜股性判不了 {deg} 只｜振幅分位空 {no_pctile} 只')
        if dry:
            head = rows[0]
            print(line + '（--dry-run，未写库）示例 '
                  + f'{head["code"]}: amp10={head["amp_10"]} pct={head["amp_10_pctile"]} '
                  + f'档={head["char_level"]} 分位={head["char_mid_pctile"]}', flush=True)
        else:
            write(day, rows)
            print(line, flush=True)
        if i % 20 == 0:
            print(f'    …… 已跑 {i}/{len(days)} 场', flush=True)
    return rc


def verify(days: Sequence[dt.date]) -> str:
    """回查库里的 min/max/场次数——"已回算 N 场"必须回查，rc=0 与日志都会说谎。"""
    if not days:
        return '没跑任何场（目标区间为空）'
    r = engine.query_one(
        f"select count(distinct trade_date) s, min(trade_date)::text a, max(trade_date)::text z,"
        f" count(*) n, count(*) filter (where char_level is null) no_level,"
        f" count(*) filter (where amp_10 is null) no_amp10 from {TABLE}"
        f" where spec_version = %s and trade_date between %s and %s",
        (SPEC_VERSION, days[0], days[-1]), role='reader') or {}
    return (f"库里现有 {r.get('s')} 场 {r.get('n')} 行（{r.get('a')} ~ {r.get('z')}）"
            f"｜股性判不了 {r.get('no_level')} 行、近10场振幅判不了 {r.get('no_amp10')} 行")


def main() -> int:
    ap = argparse.ArgumentParser(description='股性与近 10 场日均振幅入库（analytics.stock_character_daily）')
    ap.add_argument('--date', help='只算这一天（YYYY-MM-DD）；带 --recent 时是以该日结尾的 N 场')
    ap.add_argument('--since', help='从这一天起把所有交易日都算一遍')
    ap.add_argument('--recent', type=int, help='最近 N 个市场场次')
    ap.add_argument('--dry-run', action='store_true', help='只算不写')
    a = ap.parse_args()
    if a.date:
        # 与 `first_board_daily` 同一套传法：每日链给的是「以目标日结尾的 N 场」，
        # 这里要是把 --recent 吃掉只算一天，那第二天永远补不回昨天那几个晚到的涨停档位。
        end = dt.date.fromisoformat(a.date)
        days = market_sessions(end, max(1, int(a.recent))) if a.recent else [end]
    else:
        last = latest_day()
        if last is None:
            print('库里没有日线，没什么可算的')
            return 3
        if a.since:
            days = sessions_since(dt.date.fromisoformat(a.since))
        elif a.recent:
            days = market_sessions(last, int(a.recent))
        else:
            days = [last]
    if not days:
        # 目标日/起始日在一场日线都没有之前或之后：交不出场，就不写空批。
        print('目标区间里没有市场场次（日线表里没有这一天），什么都没算')
        return 3
    print(f'=== 股性入库 spec={SPEC_VERSION}：{len(days)} 场 {days[0]} ~ {days[-1]}'
          + ('（--dry-run）' if a.dry_run else '') + ' ===', flush=True)
    rc = run(days, dry=a.dry_run)
    print('=== 验收 ===')
    print('  ' + verify(days), flush=True)
    if rc == 3:
        print('⚠️ 有场降级或跳过（见上面逐场那几行），不是"全跑完了"', flush=True)
    return rc


if __name__ == '__main__':
    sys.exit(main())
