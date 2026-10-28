# -*- coding: utf-8 -*-
"""`analytics.startup_quadrant_track`（sqt-1）· 启动前 N 场四维象限轨迹：分数、名次、象限位置怎么挪的。

**为什么要有这一步**（用户 2026-10-17 的原话）：「你把这几类票**前 30 个交易日**的**四维象限分数、名次、
象限位置变化情况**分析一下」。母体两个入口（10-18 追加）：① `analytics.startup_volume_profile`（sv-2）
那四类启动事件（board4 / board4_45 / board4_or_gain45 / jump40，启动日＝连板段第一天或四日窗口的触发日）；
② **`anyfirst`＝`analytics.first_board_shape`（fb-2）的全部首板事件，seg_len 不筛**——sv-2 的 board4 一族
本身就是按结果筛过的（已走出 ≥4 板），「名次上升的是不是更容易走到 4 板」在它们身上恒 100%，只有不筛
结果的母体答得了「预示」那一问；这一档里 `outcome_len` 就是该行自己的 `seg_len`（恒有值）。
读数一律来自 `analytics.score`（长表：每票每天每键一行，带 value / rank_in_market / percent_rank / quadrant）。
一行 = (票, 启动日, cohort, capability_key) ⇒ 这一行说的是一条**轨迹**（首末两读 + 中间的极值与翻转），
不是一个读数。

六条写在旁边的口径（都有对应用例，错了不报错、只给一句听着合理的假读数）：
  · **窗口不含启动日当天**：当天已经涨停，分数被启动本身污染。`win_to`＝启动日前一场（T-1），
    `win_from`＝窗口最早一场。
  · **`rank_delta = rank_first - rank_last`，正数＝名次上升**（rank 越小越靠前）。这条符号约定是本表
    最容易读反的一列，写进 DDL 表注释、模块、读侧 `caliber` 与 `definitions` 四处。
  · `quadrant` 只在 `capability_key='oper_score'` 的行上有值（其余七键恒为 NULL 是**设计**，见
    `app/compute/scoring.py` 与 `duanxian/quant.py` 第一条纪律）⇒ `quad_first/quad_last/quad_switches/
    quad_last_switch_gap/q1_days..q4_days` 六个键那几行**必须 NULL 不是 0**（0 会被读成「一次都没换过象限」）。
    这一条由 `quad_columns()` 一处把关（不在 SQL 里另写一遍键名判断，两处迟早分叉）。
  · **`market_mood` 一族不进表**：实测同一天全市场同一个值（`rank_in_market` 恒 1、`percent_rank` 恒 50.0090）
    ⇒ 它的「名次/分位」没有横截面意义。这是显式排除，不是漏采，读侧 caveat 里点名。
  · **`vol_stage` 的 value 是档位（1 冷却 / 2 酝酿 / 3 高潮 / 4 背离 / 5 退潮）**，不是连续分数 ⇒ 那一行
    读「档位差」，读成百分比就是错（`config.capability.output_spec` 原文）。
  · **只取 `is_stale=false` 的那一批**：库里那 227,985 行旧算法迁入批（列注释原话「算法已改，不能与新结果
    混着比」，实测横跨 2026-01-27..2026-07-27 共 119 场、`quadrant` 全为 NULL）混进同一个窗口，
    `value_delta`/`rank_delta` 就成了「旧算法减新算法」。代价如实交出：可用读数只从 2026-07-29 起 47 场 ⇒
    **满窗（30 场）事件很少，`bars < window_days` 是覆盖事实不是缺陷**，读侧必须说话。

母体量级与分片：sv-2 全史 7,893 个事件（2021-11-01→2026-10-14）。逐票 lateral 在宽窗口上会撞 `ft_reader`
60 秒 `statement_timeout`（同一条 SQL 在 5 年窗口上实测 61 秒，见 `first_board_shape.py` 那一课）
⇒ **按 12 个月分片**（`CHUNK_DAYS=366`），取数与写库都走 `role='etl'`（30 分钟上限）。

用法：
    python -m app.compute.startup_quadrant_track --months 12                 # 回填（自动分片，五档母体全要）
    python -m app.compute.startup_quadrant_track --months 12 --cohort anyfirst  # 只回填「全部首板」那一档
    python -m app.compute.startup_quadrant_track --start 2026-01-01 --end 2026-10-16
    python -m app.compute.startup_quadrant_track --date 2026-10-16 --window 20 --cohort board4
    python -m app.compute.startup_quadrant_track --dry-run --months 3        # 只解析不写库
    python -m app.compute.startup_quadrant_track --verify                   # 单独跑体检
退出码：0 写了 / 1 一条都没写成 / 2 按设计不写（这一跑没有母体事件，或窗口一场都凑不满）/ 3 写了但体检不过。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.db import engine                                        # noqa: E402

TABLE = 'analytics.startup_quadrant_track'
SPEC_VERSION = 'sqt-1'

WINDOW_DAYS = 30                # 启动日之前往前几场（与 sv-2 / fb-2 同一把尺子，好对照）
WINDOWS = (10, 20, 30)          # --window 与端点只收这三档
LOOKBACK_DAYS = 400             # 往前扫多少个日历日取那 N 场（长期停牌才可能因此凑不满，如实交 bars）
CHUNK_DAYS = 366                # 分片宽度：一年一片，避开 reader 60 秒语句超时

#: 七个键的**唯一名单**（market_mood 一族不进表，理由见模块文档）。顺序就是界面 options 的顺序。
TRACK_KEYS = ('oper_score', 'temp_score', 'vol_temp', 'sector_heat', 'stock_quality',
              'env_resonance', 'vol_stage')

#: 中文label 抄自 `config.capability.name` ＋ `05_seed.sql`（vol_temp 那一条 seed 里有、
#: config.capability 里没建），语义抄自 §4.2 与 §11.1；别自己编。
KEY_LABELS: Dict[str, str] = {
    'oper_score': '操作评分',
    'temp_score': '温度',
    'vol_temp': '量能温度',
    'sector_heat': '板块热度',
    'stock_quality': '个股质量(X轴)',
    'env_resonance': '环境共振(Y轴)',
    'vol_stage': '量能阶段',
}
#: 只有这一个键在库里有象限（`analytics.score.quadrant`）
QUADRANT_KEY = 'oper_score'

#: 档位键的 value 不是分数（`config.capability.output_spec.labels`：1 冷却 / 2 酝酿 / 3 高潮 / 4 背离 / 5 退潮）
STAGE_KEY = 'vol_stage'

COHORTS_SV2 = ('board4', 'board4_45', 'board4_or_gain45', 'jump40')
#: 第二个母体入口（用户 10-18 点名）：`anyfirst` ＝ `analytics.first_board_shape`（fb-2）的**全部**首板事件，
#: `seg_len` 一律不筛。为什么要它：sv-2 那四档里 board4/`board4_45` 的母体筛子本身就是「回头看 ≥4 板」，
#: 「名次往上爬的是不是更容易走到 4 板」那一问在它们身上恒 100%、没有分辨力；只有不筛结果的母体
#: 才答得了「预示」。这一档里 `outcome_len` 就是该行自己的 `seg_len`（恒有值），分片/角色/rc 语义不变。
ANYFIRST = 'anyfirst'
COHORTS = (ANYFIRST,) + COHORTS_SV2
COHORT_LABELS: Dict[str, str] = {
    'anyfirst': '全部首板（不筛结果，能回答预示那一问）',
    'board4': '4 板及以上（连板段第一天）',
    'board4_45': '4 板及以上且前四根累计涨幅大于 45%（board4 的子集）',
    'board4_or_gain45': '4 板及以上 或 启动日起 4 个交易日涨幅大于 45%',
    'jump40': '双创与北证里存在 4 日累计涨幅超四成的票（触发日往前数）',
}

QUADRANTS = ('Q1', 'Q2', 'Q3', 'Q4')


# ============================================================ 纯判据（不落库，单测打这一组）

def delta_of(first: Optional[float], last: Optional[float]) -> Optional[float]:
    """`value_delta = last - first`。任何一边缺就是 None，**不折成 0**（0 是「真算出一样的分」）。"""
    if first is None or last is None:
        return None
    return round(float(last) - float(first), 4)


def rank_delta_of(rank_first: Optional[int], rank_last: Optional[int]) -> Optional[int]:
    """`rank_delta = rank_first - rank_last`。⚠️ **正数＝名次上升**（rank 越小越靠前）。

    写成 `rank_last - rank_first` 就把「启动前名次往上爬」说成「往下掉」，
    而这一列正是 buckets 那一半的分组依据 —— 反了以后每一档的结论都是反的。
    """
    if rank_first is None or rank_last is None:
        return None
    return int(rank_first) - int(rank_last)


def switch_count(series: Sequence[Optional[str]]) -> int:
    """窗口内**相邻两场象限不同**的次数（场次按时间正序）。

    两边都取到象限才比：象限为空的那一场不参与、也不算一次翻转 ——
    「NULL → Q1」是「补上了一个读数」，不是「位置变了」。

    ⚠️ 这一族三个函数（`switch_count` / `last_switch_gap` / `quadrant_days`）是 SQL 那一路
    `lag(quadrant)` 计数的**对照真值**：`TRACK_SQL` 为了不把整段序列拉回 python 而在库里数，
    两处一旦分叉就没人报错 —— 所以 `tests/test_startup_quadrant_track.py` 拿真库一条轨迹把
    场次序列取回来用这三个函数重算一遍，逐列与库里那一行对。
    """
    q = [x if x in QUADRANTS else None for x in series]
    return sum(1 for a, b in zip(q, q[1:]) if a is not None and b is not None and a != b)


def last_switch_gap(series: Sequence[Optional[str]]) -> Optional[int]:
    """最后一次翻转那一场距序列末尾（T-1）还有几场：0＝翻转就发生在 T-1，`len-1`＝只在最开头翻过一次。

    一次都没翻转交 **None**（不是 0；0 的意思是「刚刚才翻」，两者读出来是相反的故事）。
    """
    q = [x if x in QUADRANTS else None for x in series]
    n = len(q)
    for i in range(n - 1, 0, -1):
        if q[i] is not None and q[i - 1] is not None and q[i] != q[i - 1]:
            return n - 1 - i
    return None


def quadrant_days(series: Sequence[Optional[str]]) -> Dict[str, int]:
    """Q1..Q4 各占几场。相加 <= 场数（象限为空的场次不在任何一档里）。"""
    return {f'{key.lower()}_days': sum(1 for x in series if x == key) for key in QUADRANTS}


def quad_columns(capability_key: str, agg: dict, bars: int) -> Dict[str, Any]:
    """象限那一族六列的**唯一出口**：非 `oper_score` 的键一律交回全 None。

    这一条必须在 Python 一处把关，不能只靠 SQL 里写 `case when capability_key='oper_score'`：
    SQL 那一路对没有象限的行只会数出 0 次翻转、0 场 Q1，屏幕上就成了「这六个键的票启动前从没换过
    象限」—— 而真意是**它们压根没有象限这个读数**。`gap` 在这里由 `bars - last_switch_rn` 现算，
    因为「距 T-1 几场」这句人话只该有一个算式。
    """
    if capability_key != QUADRANT_KEY:
        return {'quad_first': None, 'quad_last': None, 'quad_switches': None,
                'quad_last_switch_gap': None,
                'q1_days': None, 'q2_days': None, 'q3_days': None, 'q4_days': None}
    rn = agg.get('last_switch_rn')
    return {'quad_first': agg.get('quad_first_raw'),
            'quad_last': agg.get('quad_last_raw'),
            'quad_switches': int(agg.get('quad_switches_raw') or 0),
            'quad_last_switch_gap': None if rn is None else int(bars) - int(rn),
            **{f'{k.lower()}_days': int(agg.get(f'q{k[1]}_raw') or 0) for k in QUADRANTS}}


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _int(v: Any) -> Optional[int]:
    return None if v is None else int(v)


def _round(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(v, 4)


# ============================================================ 取数

EVENTS_SQL = """
select p.security_code, p.launch_date, p.cohort, p.name, p.board, p.trigger_gain
  from analytics.startup_volume_profile p
 where p.spec_version = 'sv-2'
   and p.launch_date between %(a)s::date and %(b)s::date
   and (%(cohort)s::text is null or p.cohort = %(cohort)s)
 order by p.launch_date, p.security_code
"""

ANYFIRST_EVENTS_SQL = """
-- 第二个母体入口（10-18）：fb-2 的全部首板事件，seg_len 不筛 —— 段长在这里是结果变量不是筛子。
-- trigger_gain 这一档没有（fb-2 没有那一列）⇒ outcome_gain4 交 NULL，读侧不许把它读成 0。
select f.security_code, f.launch_date, 'anyfirst'::text as cohort, f.name, f.board,
       null::numeric as trigger_gain
  from analytics.first_board_shape f
 where f.spec_version = 'fb-2'
   and f.launch_date between %(a)s::date and %(b)s::date
 order by f.launch_date, f.security_code
"""

TRACK_SQL = """
-- 一批（代码, 启动日, cohort）→ 每个键启动日之前那 %(win)s 场的窗口读数（聚合全在 SQL 里做）
with ev as (
  select (x->>0)::text as code, (x->>1)::date as launch, (x->>2)::text as cohort
    from jsonb_array_elements(%(pairs)s::jsonb) x
),
s0 as (                        -- rn_desc＝往前第几场（1＝T-1，启动日当天不进窗口）
  select e.code, e.launch, e.cohort, s.capability_key, s.trade_date,
         s.value, s.rank_in_market, s.percent_rank, s.quadrant,
         row_number() over (partition by e.code, e.launch, e.cohort, s.capability_key
                            order by s.trade_date desc) as rn_desc
    from ev e
    join analytics.score s
      on s.security_code = e.code
     and s.capability_key = any(%(keys)s::text[])
     and s.trade_date < e.launch
     and s.trade_date >= e.launch - (%(back)s::int)
     and not s.is_stale
),
w as (                         -- 只留窗口那几场，重新按时间正序编号并做相邻比较
  select s0.*,
         row_number() over (partition by code, launch, cohort, capability_key
                            order by trade_date) as rn,
         lag(quadrant) over (partition by code, launch, cohort, capability_key
                             order by trade_date) as prev_quad
    from s0
   where rn_desc <= %(win)s
)
select code, launch::text as launch, cohort, capability_key,
       count(*) as bars,
       min(trade_date)::text as win_from, max(trade_date)::text as win_to,
       (array_agg(value order by trade_date))[1]                  as value_first,
       (array_agg(value order by trade_date desc))[1]             as value_last,
       percentile_cont(0.5) within group (order by value)          as value_med,
       (array_agg(rank_in_market order by trade_date))[1]          as rank_first,
       (array_agg(rank_in_market order by trade_date desc))[1]     as rank_last,
       min(rank_in_market) as rank_best, max(rank_in_market) as rank_worst,
       (array_agg(percent_rank order by trade_date))[1]            as pct_first,
       (array_agg(percent_rank order by trade_date desc))[1]       as pct_last,
       (array_agg(quadrant order by trade_date))[1]                as quad_first_raw,
       (array_agg(quadrant order by trade_date desc))[1]           as quad_last_raw,
       sum(case when quadrant in ('Q1','Q2','Q3','Q4')
                 and prev_quad in ('Q1','Q2','Q3','Q4')
                 and quadrant <> prev_quad then 1 else 0 end)      as quad_switches_raw,
       max(case when quadrant in ('Q1','Q2','Q3','Q4')
                 and prev_quad in ('Q1','Q2','Q3','Q4')
                 and quadrant <> prev_quad then rn end)            as last_switch_rn,
       count(*) filter (where quadrant = 'Q1')                      as q1_raw,
       count(*) filter (where quadrant = 'Q2')                      as q2_raw,
       count(*) filter (where quadrant = 'Q3')                      as q3_raw,
       count(*) filter (where quadrant = 'Q4')                      as q4_raw
  from w
 group by code, launch, cohort, capability_key
"""

FB_SQL = """
-- 同一个 (票, 启动日) 在 fb-2 里走到几板（结果读数；fb-2 没有那一行就不交，读侧当 NULL）
with ev as (
  select (x->>0)::text as code, (x->>1)::date as launch from jsonb_array_elements(%(pairs)s::jsonb) x
)
select e.code, e.launch::text as launch, f.seg_len, f.seg_complete
  from ev e
  join analytics.first_board_shape f
    on f.security_code = e.code and f.launch_date = e.launch and f.spec_version = 'fb-2'
"""


def _pairs(events: Iterable[dict]) -> str:
    return json.dumps([[e['security_code'], str(e['launch_date']), e['cohort']] for e in events])


def _chunks(d_from: dt.date, d_to: dt.date) -> List[tuple]:
    out, a = [], d_from
    while a <= d_to:
        b = min(d_to, a + dt.timedelta(days=CHUNK_DAYS - 1))
        out.append((a, b))
        a = b + dt.timedelta(days=1)
    return out


def _row(event: dict, agg: dict, window_days: int, outcome: Dict[str, Any]) -> dict:
    """一条（事件 × 键）的窗口读数 → 一行。象限那一族只走 `quad_columns()` 那一个出口（NULL 纪律）。"""
    key = agg['capability_key']
    bars = int(agg['bars'])
    return {
        'security_code': event['security_code'], 'launch_date': event['launch_date'],
        'cohort': event['cohort'], 'capability_key': key, 'spec_version': SPEC_VERSION,
        'name': event.get('name'), 'board': event.get('board'),
        'window_days': window_days, 'bars': bars,
        'win_from': agg.get('win_from'), 'win_to': agg.get('win_to'),
        'value_first': _num(agg.get('value_first')), 'value_last': _num(agg.get('value_last')),
        'value_delta': delta_of(_num(agg.get('value_first')), _num(agg.get('value_last'))),
        'value_med': _round(_num(agg.get('value_med'))),
        'rank_first': _int(agg.get('rank_first')), 'rank_last': _int(agg.get('rank_last')),
        'rank_delta': rank_delta_of(_int(agg.get('rank_first')), _int(agg.get('rank_last'))),
        'rank_best': _int(agg.get('rank_best')), 'rank_worst': _int(agg.get('rank_worst')),
        'pct_first': _num(agg.get('pct_first')), 'pct_last': _num(agg.get('pct_last')),
        **quad_columns(key, agg, bars),
        'outcome_len': outcome.get('seg_len'),
        'outcome_gain4': _round(_num(event.get('trigger_gain'))),
    }


def load_events(d_from: dt.date, d_to: dt.date, cohort: Optional[str]) -> List[dict]:
    """母体两个入口（10-18）：`anyfirst`＝fb-2 全部首板（不筛结果）；其余四档＝sv-2 的事件名单。
    `cohort=None` 两路都要 —— 每日链跑 `--months 12` 时 anyfirst 也得跟着刷新，不然这一档当天就烂掉。"""
    events: List[dict] = []
    if cohort is None or cohort in COHORTS_SV2:
        events += engine.query(EVENTS_SQL, {'a': d_from, 'b': d_to, 'cohort': cohort}, role='etl') or []
    if cohort is None or cohort == ANYFIRST:
        events += engine.query(ANYFIRST_EVENTS_SQL, {'a': d_from, 'b': d_to}, role='etl') or []
    return events


def collect(d_from: dt.date, d_to: dt.date, *, window_days: int = WINDOW_DAYS,
            cohort: Optional[str] = None) -> List[dict]:
    """一片窗口里的母体事件 × 七个键 → 轨迹行。分片由 `compute_range` 负责。"""
    events = load_events(d_from, d_to, cohort)
    if not events:
        return []
    pairs = _pairs(events)
    tracks = engine.query(TRACK_SQL, {'pairs': pairs, 'keys': list(TRACK_KEYS),
                                       'win': window_days, 'back': LOOKBACK_DAYS}, role='etl') or []
    fb = {f"{r['code']}|{r['launch']}": r for r in
          (engine.query(FB_SQL, {'pairs': pairs}, role='etl') or [])}
    by_event: Dict[str, dict] = {f"{e['security_code']}|{e['launch_date']}|{e['cohort']}": e
                                 for e in events}
    out = []
    for t in tracks:
        ev = by_event.get(f"{t['code']}|{t['launch']}|{t['cohort']}")
        if ev is None:
            continue
        outcome = fb.get(f"{t['code']}|{t['launch']}") or {}
        out.append(_row(ev, t, window_days, outcome))
    return out


#: 列清单**只有一个真值**：INSERT 的列、占位符个数、SET 子句都从它生成（照 fb-2 那一版）。
#: `calculated_at` 不进 INSERT 列表（默认值 `now()` 就是它的写入时刻），但重算要在 SET 里刷新它。
COLS = ('security_code', 'launch_date', 'cohort', 'capability_key', 'spec_version', 'name', 'board',
        'window_days', 'bars', 'win_from', 'win_to', 'value_first', 'value_last', 'value_delta',
        'value_med', 'rank_first', 'rank_last', 'rank_delta', 'rank_best', 'rank_worst',
        'pct_first', 'pct_last', 'quad_first', 'quad_last', 'quad_switches', 'quad_last_switch_gap',
        'q1_days', 'q2_days', 'q3_days', 'q4_days', 'outcome_len', 'outcome_gain4')

_PK = ('security_code', 'launch_date', 'cohort', 'capability_key', 'window_days', 'spec_version')

UPSERT = (
    f"insert into {TABLE} ({', '.join(COLS)}) "
    f"values ({', '.join(['%s'] * len(COLS))}) "
    f"on conflict ({', '.join(_PK)}) do update set "
    + ', '.join(f'{c} = excluded.{c}' for c in COLS if c not in _PK)
    + ", calculated_at = now()")


def write(rows: List[dict]) -> int:
    if not rows:
        return 0
    params = [tuple(r.get(c) for c in COLS) for r in rows]
    return engine.executemany(UPSERT, params, role='etl')


def compute_range(d_from: dt.date, d_to: dt.date, *, window_days: int = WINDOW_DAYS,
                  cohort: Optional[str] = None, write_rows: bool = True,
                  quiet: bool = False) -> Dict[str, Any]:
    total_w = total_n = 0
    for a, b in _chunks(d_from, d_to):
        rows = collect(a, b, window_days=window_days, cohort=cohort)
        total_n += len(rows)
        if rows and write_rows:
            total_w += write(rows)
        if not quiet:
            print('  %s .. %s：解析 %d 行（事件×键）%s' % (a, b, len(rows),
                                                           '（dry-run 不写库）' if not write_rows else ''),
                  flush=True)
    return {'asked': total_n, 'written': total_w, 'range': (str(d_from), str(d_to))}


def verify() -> List[str]:
    """真数体检。每条都数得出行数，不许「查不到」折成 0。"""
    bad: List[str] = []
    r = engine.query_one(
        f"select count(*) n, count(distinct security_code) codes, count(distinct (security_code, "
        f"       launch_date, cohort)) events, min(launch_date)::text a, max(launch_date)::text z, "
        f"       count(*) filter (where capability_key = 'oper_score') oper_rows, "
        f"       count(distinct (security_code, launch_date, cohort)) filter "
        f"         (where capability_key = 'oper_score') oper_events, "
        f"       count(*) filter (where bars < window_days) 短窗, "
        f"       count(*) filter (where bars = 0) 零场, "
        f"       count(*) filter (where capability_key <> 'oper_score' and quad_first is not null) "
        f"         脏象限, "
        f"       count(*) filter (where capability_key <> 'oper_score' and (quad_switches is not null "
        f"         or q1_days is not null or quad_last_switch_gap is not null)) 脏象限2, "
        f"       count(*) filter (where capability_key = 'oper_score' "
        f"         and (q1_days is null or q2_days is null or q3_days is null or q4_days is null)) "
        f"         象限天数缺, "
        f"       count(*) filter (where capability_key = 'oper_score' and bars >= 3 "
        f"         and coalesce(q1_days,0)+coalesce(q2_days,0)+coalesce(q3_days,0)+coalesce(q4_days,0) = 0) "
        f"         象限全零, "
        f"       count(*) filter (where capability_key = 'oper_score' "
        f"         and coalesce(q1_days,0)+coalesce(q2_days,0)+coalesce(q3_days,0)+coalesce(q4_days,0) "
        f"             > bars) 天数越界, "
        f"       count(*) filter (where rank_delta is not null "
        f"         and rank_delta is distinct from rank_first - rank_last) 名次不自洽, "
        f"       count(*) filter (where outcome_len is not null and outcome_len < 1) 板数越界, "
        f"       count(*) filter (where cohort = 'anyfirst' and outcome_len is null) anyfirst缺结果, "
        f"       count(*) filter (where cohort = 'anyfirst') anyfirst行, "
        f"       count(*) filter (where win_to >= launch_date) 窗口含启动日, "
        f"       count(*) filter (where capability_key = 'market_mood') 混进情绪族 "
        f"  from {TABLE} where spec_version = %s", (SPEC_VERSION,), role='reader')
    if not r or not r['n']:
        return ['这张表一行都没有 —— 回填没跑，还是写库坏了？']
    checks = (('脏象限', '非 oper_score 的行 quad_first 不是 NULL（写坏了：那些键在库里就没有象限）'),
              ('脏象限2', '非 oper_score 的行 quad_switches/q1_days/quad_last_switch_gap 不是 NULL'),
              ('象限天数缺', 'oper_score 行的 q1_days..q4_days 是 NULL（那一族的列名拼错了就整列空着，'
                           '而 0 才是真读数）'),
              ('象限全零', 'oper_score 行四档天数相加为 0（窗口里每场都有象限读数 ⇒ 全 0 就是 SQL 别名与列名接错了）'),
              ('天数越界', 'oper_score 行 q1..q4 天数之和大过了 bars（象限场次比窗口场次还多）'),
              ('名次不自洽', 'rank_delta 与 rank_first-rank_last 对不上（符号或口径漂了）'),
              ('窗口含启动日', 'win_to 不早于启动日（窗口本该不含当天，分数被启动本身污染）'),
              ('零场', 'bars=0 的行进了库（窗口一场都没有就该整行不写）'),
              ('板数越界', 'outcome_len < 1（fb-2 的段长不可能是 0，缺应该是 NULL）'),
              ('anyfirst缺结果', 'cohort=anyfirst 的行 outcome_len 是 NULL —— 这一档的母体就在 fb-2 里，'
                                 '每一行都必有自己那一行的 seg_len，缺了就是 join 断了'),
              ('混进情绪族', 'market_mood 那一族进了表（同一天全市场同一个数，名次没有意义）'))
    for col, why in checks:
        if int(r[col]):
            bad.append(f'{r[col]} 行{why}')
    latest = engine.scalar('select max(trade_date)::text from market_data.stock_daily')
    have_outcome = int(engine.scalar(f"select count(outcome_len) from {TABLE} where spec_version = %s",
                                     (SPEC_VERSION,), role='reader') or 0)
    print(f'  库里：{r["n"]} 行 / {r["codes"]} 只 / {r["events"]} 个母体事件'
          f'（其中 oper_score 有轨迹的 {r["oper_events"]} 个）/ 启动日 {r["a"]} … {r["z"]}'
          f'（日线最新一场 {latest}）')
    print(f'  窗口不满 {r["短窗"]} 行、名次不自洽 {r["名次不自洽"]} 行、'
          f'非 oper_score 带象限 {r["脏象限"]} 行、outcome_len 有值 {have_outcome} 行')
    per = engine.query(f"select cohort, count(*) n, count(distinct (security_code, launch_date)) events, "
                       f"       count(outcome_len) has_result, count(*) filter (where bars < window_days) 短窗 "
                       f"  from {TABLE} where spec_version = %s group by 1 order by 1",
                       (SPEC_VERSION,), role='reader') or []
    print('  按 cohort 分：' + '、'.join(
        f'{p["cohort"]} {p["n"]} 行/{p["events"]} 事件（outcome 有值 {p["has_result"]}、不满窗 {p["短窗"]}）'
        for p in per))
    if int(r['oper_events']) == 0:
        bad.append('oper_score 一行都没有 —— buckets 那一半（按名次变化分三档）就没有分组的底')
    return bad


def sessions_back(n: int) -> dt.date:
    """库里最新一场往前 N 个交易日的日期（每日链重算范围用）。"""
    rows = engine.query(f"select distinct trade_date from market_data.stock_daily order by 1 desc limit {int(n)}",
                        (), role='reader') or []
    return rows[-1]['trade_date'] if rows else dt.date.today()


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description='启动前 N 场四维象限轨迹（sqt-1）')
    ap.add_argument('--months', type=int, default=12, help='往前几个月（默认 12）')
    ap.add_argument('--start', help='区间起日（给了就覆盖 --months）')
    ap.add_argument('--end', help='区间止日（默认库里最新交易日）')
    ap.add_argument('--date', help='只算到这一天（等价 --end）')
    ap.add_argument('--window', type=int, default=WINDOW_DAYS, choices=WINDOWS,
                    help='启动日往前几场（10/20/30，默认 30）')
    ap.add_argument('--cohort', choices=COHORTS, help='只跑一档母体（默认五档全跑：anyfirst ＋ sv-2 四类）')
    ap.add_argument('--recent-sessions', type=int, help='只重算最近 N 个交易日起（每日链用这个）')
    ap.add_argument('--dry-run', action='store_true', help='只解析不写库')
    ap.add_argument('--verify', action='store_true', help='写完/单独跑体检')
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.verify:
        bad = verify()
        print('\n'.join(f'✗ {b}' for b in bad) if bad else '✓ 四维象限轨迹表体检通过')
        return 3 if bad else 0

    d_to = (dt.date.fromisoformat(args.end) if args.end else
            dt.date.fromisoformat(args.date) if args.date else
            engine.scalar('select max(trade_date) from market_data.stock_daily'))
    if d_to is None:
        print('库里没有日线，谈不上母体')
        return 1
    if args.recent_sessions:
        d_from = sessions_back(int(args.recent_sessions))
    elif args.start:
        d_from = dt.date.fromisoformat(args.start)
    else:
        d_from = d_to - dt.timedelta(days=args.months * 31)
    print(f'=== 启动前象限轨迹（{SPEC_VERSION}）：启动日 {d_from} .. {d_to}'
          f'，窗口 {args.window} 场，母体 {args.cohort or "五档全要（anyfirst ＋ sv-2 四类）"}'
          f'{"（dry-run）" if args.dry_run else ""}', flush=True)
    res = compute_range(d_from, d_to, window_days=args.window, cohort=args.cohort,
                        write_rows=not args.dry_run)
    if not res['asked']:
        print('  这一段里没有能凑出窗口的母体事件（score 的可用批次还没铺到这段日子？）'
              '—— 按设计不写，不算失败')
        return 2
    if args.dry_run:
        return 0
    print(f'  写入 {res["written"]} 行')
    bad = verify()
    print('\n'.join(f'✗ {b}' for b in bad) if bad else '✓ 体检通过')
    return 0 if not bad else 3


if __name__ == '__main__':
    sys.exit(main())

# 10-18：_PK 跟上 window_days —— 主键加了这一维之后，ON CONFLICT 必须按新主键给，
# 否则整批写库报 InvalidColumnReference（no unique constraint matching ON CONFLICT）。
