# -*- coding: utf-8 -*-
"""`analytics.startup_spike_days`（ssd-1）· 启动前 N 场爆量／突然拉高 × 量增／量缩（日线代理）。

**为什么要有这一步**（用户 2026-10-18 追加的原话）：「分析前 30 个交易日，看看有多少爆量，或者突然拉高，
这种情况分别出现在**量增的天数还是量减的天数**」。分钟线那一层他已明确取消，所以这里只用**日线代理判据**
（一根交易日一个数）——读侧屏上必须写清是代理、不冒充分时图上的爆量/急拉。
关键在**联合计数**：只报「倍量 8 天 / 量增 17 天」两个独立天数答不了「落在哪一边」；
每个倍量/准倍量/缩量日再按当日涨跌分一次堆，`dbl_up_days + dbl_down_days = dbl_days`
这条自证判据由 `verify()` 与测试两边盯着。

母体两个入口（与 `startup_quadrant_track.py` 同一族纪律）：
  · `anyfirst`＝`analytics.first_board_shape`（fb-2）的**全部**首板事件，seg_len 不筛——
    sv-2 那四档里 board4 一族本身就是按「回头看 ≥4 板」筛过的，只有不筛结果的母体答得了「预示」；
  · sv-2 四类（board4 / board4_45 / board4_or_gain45 / jump40）＝对照用。
结果四列（走到几板/段走完/进场后 5 场绝对与超额）一律从 fb-2 同 (票, 启动日) join。

四条写在旁边的口径（都有对应用例）：
  · vr 尺子**复用** `app/compute/startup_volume.py` 的 `classify()` 与它的 `DBL/NEAR_DBL/CALM` 常量
    （up=vr>1、down=vr<1、flat=四位收口精确 1.0000、still=0.95~1.05）——import，不抄数，不许写第三份。
  · 当日涨跌**必须后复权重算**（`close × coalesce(adjustment_factor,1)` 相对前一根同式，
    同 `duanxian/day_returns.py` 那条式子），**不读 `stock_daily.pct_change`**（除权日错）。
    vr 只读 `analytics.volume_ratio_daily`（vr-1），不在这里再除一遍量。
  · 突然拉高＝当日后复权收益 `>= JUMP_PCT`（模块常量 0.07，CLI `--jump-pct` 可覆盖）。
    **阈值下闭**：正好等于 0.07 算拉高（边界归哪一头写死并有断言，两边各判一次就会有一天两头都不算）。
  · 窗口不满（bars<window_days）时 jump 那一族整组交 **NULL 不是 0**：0 的意思是「凑满了 N 场、
    一次都没拉高」，不满窗没有资格说这句话。`jump_days=0`（真没有）与 NULL（窗口不满）是两件事。
  · 涨停判定容差引用 `app/compute/limit_prices.py::LIMIT_EPS`（全项目唯一，半分），不新写 0.01。

窗口＝启动日**之前** window_days 个该票自己的有效交易日（不含启动日当天）；取数多取一场只当
首根收益的基价。缺的格子不参与、不补 0（vr 缺的那场不进 vr 尺子，ret 缺的那场不进涨跌判定）。
真数据里倍量日必有前一根（vr 行存在即证明前一根存在），所以库里 `dbl_up+dbl_down=dbl` 应恒成立。

主键不带 window_days ⇒ 换 `--window` 重灌是覆盖同一批行（`window_days` 列说这一行按几场判的）。

用法：
    python -m app.compute.startup_spike_days --months 12                    # 回填（五档母体全要，自动分片）
    python -m app.compute.startup_spike_days --months 12 --cohort anyfirst  # 只回填全部首板那一档
    python -m app.compute.startup_spike_days --start 2026-01-01 --end 2026-10-16 --window 20
    python -m app.compute.startup_spike_days --date 2026-10-16 --recent-sessions 40   # 每日链同款
    python -m app.compute.startup_spike_days --dry-run --months 3           # 只解析不写库
    python -m app.compute.startup_spike_days --verify                       # 单独跑体检
退出码：0 写了 / 1 一条都没写成 / 2 按设计不写（这一段没有母体事件或一场窗口都凑不出）/
3 写了但体检不过（清单在输出里）。
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
from app.compute.limit_prices import LIMIT_EPS                   # noqa: E402  全项目唯一的涨停容差（半分）
from app.compute.startup_volume import CALM, DBL, NEAR_DBL, classify    # noqa: E402  vr 尺子只此一份

TABLE = 'analytics.startup_spike_days'
SPEC_VERSION = 'ssd-1'

WINDOW_DAYS = 30                # 启动日前根数（与 sv-2 / fb-2 / sqt-1 同一把尺子，好对照）
WINDOWS = (10, 20, 30)          # --window 与端点只收这三档
CHUNK_DAYS = 366                # 分片宽度：一年一片，避开 reader 60 秒语句超时（同 fb-2 那一课）

#: 突然拉高的日线代理阈值（当日后复权收益，小数比例）。**阈值下闭**：`ret >= JUMP_PCT` 算拉高。
#: CLI `--jump-pct` 可覆盖；改了阈值重灌会盖掉旧批（主键没有这一维），要留对照就换 spec_version。
JUMP_PCT = 0.07

SV2_COHORTS = ('board4', 'board4_45', 'board4_or_gain45', 'jump40')
ANYFIRST = 'anyfirst'
COHORTS = (ANYFIRST,) + SV2_COHORTS
COHORT_LABELS: Dict[str, str] = {
    'anyfirst': '全部首板（不筛结果，能回答预示那一问）',
    'board4': '4 板及以上（连板段第一天）',
    'board4_45': '4 板及以上且前四根累计涨幅大于 45%（board4 的子集）',
    'board4_or_gain45': '4 板及以上 或 启动日起 4 个交易日涨幅大于 45%',
    'jump40': '双创与北证里存在 4 日累计涨幅超四成的票（触发日往前数）',
}


# ============================================================ 纯判据（不碰库，单测打这一组）

def _is_dbl(v: Optional[float]) -> bool:
    return v is not None and v >= DBL


def _is_near(v: Optional[float]) -> bool:
    return v is not None and NEAR_DBL <= v < DBL


def _is_calm(v: Optional[float]) -> bool:
    return v is not None and v <= CALM


def window_counts(bars: Sequence[tuple], *, window_days: int = WINDOW_DAYS,
                  jump_pct: float = JUMP_PCT) -> Dict[str, Any]:
    """一窗 `(vr, ret, is_limit)` 序列（场次正序，最早一场在前，T-1 在末）→ 那一串天数。

    三条纪律（都是「错了不报错、只给一句听着合理的读数」的地方）：
      · vr 为 None 的那场**不进 vr 尺子**（`classify` 同一条：不参与、不补 0）。
      · ret 为 None 的那场**不进涨跌判定**（这一天的倍量既不算 up 也不算 down ⇒ 联合计数与
        `dbl_days` 对不上时，差的就是这些天——真数据里倍量日必有前一根，所以 verify 数「例外」要求为 0）。
      · 窗口不满（len(bars) < window_days）时 jump 那一族整组交 None：0 是「凑满了 N 场一次都没拉高」，
        不满窗没有资格说这句话。`first_dbl_gap` 不受此限（「出现过」是正着数出来的事实）。
    涨跌判定：涨＝当日后复权收益 >0，跌＝<=0（平盘归跌那一头，保证 up+down 恰好覆盖全部有 ret 的天）。
    急拉阈值**下闭**：`ret >= jump_pct`（正好等于 0.07 算拉高）。
    """
    n = len(bars)
    cls = classify([b[0] for b in bars], [None] * n)     # vr 尺子：与 sv-2 同一条，复用不重写
    out: Dict[str, Any] = {
        'bars': n,
        'up_days': cls['up_days'], 'down_days': cls['down_days'],
        'flat_days': cls['flat_days'], 'still_days': cls['still_days'],
        'dbl_days': cls['dbl_days'], 'near_dbl_days': cls['near_dbl_days'],
        'calm_days': cls['calm_days'],
        'dbl_up_days': 0, 'dbl_down_days': 0,
        'near_up_days': 0, 'near_down_days': 0,
        'calm_up_days': 0, 'calm_down_days': 0,
        'jump_days': 0, 'jump_up_days': 0, 'jump_down_days': 0,
        'jump_dbl_days': 0, 'jump_near_days': 0, 'jump_calm_days': 0, 'jump_limit_days': 0,
        'limit_days': 0, 'first_dbl_gap': None, 'first_jump_gap': None,
    }
    for i, (vr, ret, lim) in enumerate(bars):
        gap = n - 1 - i                                   # 距 T-1 几场（0＝就是最后一场，即启动日前一天）
        if lim:
            out['limit_days'] += 1
        for flag, up_key, down_key in ((_is_dbl, 'dbl_up_days', 'dbl_down_days'),
                                       (_is_near, 'near_up_days', 'near_down_days'),
                                       (_is_calm, 'calm_up_days', 'calm_down_days')):
            if flag(vr):
                if ret is not None:
                    out[up_key if ret > 0 else down_key] += 1
        if _is_dbl(vr) and out['first_dbl_gap'] is None:
            out['first_dbl_gap'] = gap
        if ret is not None and ret >= jump_pct:           # 阈值下闭：==jump_pct 算拉高
            out['jump_days'] += 1
            out['jump_up_days' if ret > 0 else 'jump_down_days'] += 1
            if _is_dbl(vr):
                out['jump_dbl_days'] += 1
            elif _is_near(vr):
                out['jump_near_days'] += 1
            elif _is_calm(vr):
                out['jump_calm_days'] += 1
            if lim:
                out['jump_limit_days'] += 1
            if out['first_jump_gap'] is None:
                out['first_jump_gap'] = gap
    if n < window_days:
        for k in ('jump_days', 'jump_up_days', 'jump_down_days', 'jump_dbl_days',
                  'jump_near_days', 'jump_calm_days', 'jump_limit_days', 'first_jump_gap'):
            out[k] = None
    return out


# ============================================================ 取数

EVENTS_SQL = """
select p.security_code, p.launch_date, p.cohort, p.name, p.board
  from analytics.startup_volume_profile p
 where p.spec_version = 'sv-2'
   and p.launch_date between %(a)s::date and %(b)s::date
   and (%(cohort)s::text is null or p.cohort = %(cohort)s)
 order by p.launch_date, p.security_code
"""

ANYFIRST_EVENTS_SQL = """
-- 母体第二入口：fb-2 的全部首板事件，seg_len 不筛（段长在这里是结果变量，筛了就又是未来函数）
select f.security_code, f.launch_date, 'anyfirst'::text as cohort, f.name, f.board
  from analytics.first_board_shape f
 where f.spec_version = 'fb-2'
   and f.launch_date between %(a)s::date and %(b)s::date
 order by f.launch_date, f.security_code
"""

BARS_SQL = """
-- 一批（代码, 启动日）→ 启动日之前那 %(fetch)s 场的日线（多取一场只当首根收益的基价）＋ vr + 涨停判定
with ev as (
  select (x->>0)::text as code, (x->>1)::date as launch from jsonb_array_elements(%(pairs)s::jsonb) x
),
b as (
  select k.code, k.launch, s.trade_date, s.hfq, s.close, s.limit_up, v.vol_ratio,
         lag(s.hfq) over (partition by k.code, k.launch order by s.trade_date) as prev_hfq,
         row_number() over (partition by k.code, k.launch order by s.trade_date) as rn
    from ev k
    cross join lateral (
      select q.trade_date,
             q.close * coalesce(q.adjustment_factor, 1) as hfq,
             q.close, q.limit_up
        from market_data.stock_daily q
       where q.security_code = k.code and q.trade_date < k.launch
       order by q.trade_date desc limit %(fetch)s) s
    left join analytics.volume_ratio_daily v
      on v.security_code = k.code and v.trade_date = s.trade_date and v.spec_version = 'vr-1'
)
select code, launch::text as launch,
       jsonb_agg(jsonb_build_object(
         'd', to_char(trade_date, 'YYYY-MM-DD'),
         'vr', vol_ratio,
         'ret', case when prev_hfq is not null and prev_hfq > 0 and hfq is not null
                     then round(hfq / prev_hfq - 1, 6) end,
         'lim', case when close is not null and limit_up is not null
                     then close >= limit_up - %(eps)s end
       ) order by trade_date) as day_rows
  from b
 where rn >= 2
 group by 1, 2
"""

OUTCOME_SQL = """
-- 结果四列一律从 fb-2 同 (票, 启动日) join：fb-2 没有那一行就整组不交（读侧当 NULL，jump40 常态）
with ev as (
  select (x->>0)::text as code, (x->>1)::date as launch from jsonb_array_elements(%(pairs)s::jsonb) x
)
select e.code, e.launch::text as launch, f.seg_len, f.seg_complete, f.r5, f.ex5
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


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def load_events(d_from: dt.date, d_to: dt.date, cohort: Optional[str]) -> List[dict]:
    """母体两个入口：`anyfirst`＝fb-2 全部首板（不筛结果）；其余四档＝sv-2。`cohort=None` 两路都要。"""
    events: List[dict] = []
    if cohort is None or cohort in SV2_COHORTS:
        events += engine.query(EVENTS_SQL, {'a': d_from, 'b': d_to, 'cohort': cohort}, role='etl') or []
    if cohort is None or cohort == ANYFIRST:
        events += engine.query(ANYFIRST_EVENTS_SQL, {'a': d_from, 'b': d_to}, role='etl') or []
    return events


def collect(d_from: dt.date, d_to: dt.date, *, window_days: int = WINDOW_DAYS,
            cohort: Optional[str] = None, jump_pct: float = JUMP_PCT) -> List[dict]:
    """一片窗口里的母体事件 → 联合计数行。分片由 `compute_range` 负责。窗口一场都没有的整行不写。"""
    events = load_events(d_from, d_to, cohort)
    if not events:
        return []
    pairs = _pairs(events)
    rows_sql = engine.query(BARS_SQL, {'pairs': pairs, 'fetch': window_days + 1,
                                       'eps': LIMIT_EPS}, role='etl') or []
    series_map = {f"{r['code']}|{r['launch']}": r['day_rows'] for r in rows_sql}
    outcomes = {f"{r['code']}|{r['launch']}": r for r in
                (engine.query(OUTCOME_SQL, {'pairs': pairs}, role='etl') or [])}
    out = []
    for e in events:
        key = f"{e['security_code']}|{e['launch_date']}"
        day_rows = series_map.get(key) or []
        if not day_rows:
            continue                     # 启动日之前一场日线都没有（上市即首板那种）⇒ 整行不写，不写一排 0
        bars = [(r.get('vr'), r.get('ret'), r.get('lim')) for r in day_rows]
        c = window_counts(bars, window_days=window_days, jump_pct=jump_pct)
        oc = outcomes.get(key) or {}
        out.append({'security_code': e['security_code'], 'launch_date': e['launch_date'],
                    'cohort': e['cohort'], 'spec_version': SPEC_VERSION,
                    'name': e.get('name'), 'board': e.get('board'),
                    'window_days': window_days,
                    'win_from': day_rows[0].get('d'), 'win_to': day_rows[-1].get('d'),
                    **c,
                    'outcome_len': oc.get('seg_len'),
                    'outcome_seg_complete': oc.get('seg_complete'),
                    'outcome_r5': _num(oc.get('r5')), 'outcome_ex5': _num(oc.get('ex5'))})
    return out


#: 列清单**只有一个真值**：INSERT 的列、占位符个数、SET 子句都从它生成（照 fb-2/sqt-1 那一版）。
#: `calculated_at` 不进 INSERT 列表（默认值 `now()` 就是它的写入时刻），但重算要在 SET 里刷新它。
COLS = ('security_code', 'launch_date', 'cohort', 'spec_version', 'name', 'board',
        'window_days', 'bars', 'win_from', 'win_to',
        'up_days', 'down_days', 'flat_days', 'still_days',
        'dbl_days', 'near_dbl_days', 'calm_days',
        'dbl_up_days', 'dbl_down_days', 'near_up_days', 'near_down_days',
        'calm_up_days', 'calm_down_days',
        'jump_days', 'jump_up_days', 'jump_down_days',
        'jump_dbl_days', 'jump_near_days', 'jump_calm_days', 'jump_limit_days',
        'limit_days', 'first_dbl_gap', 'first_jump_gap',
        'outcome_len', 'outcome_seg_complete', 'outcome_r5', 'outcome_ex5')

_PK = ('security_code', 'launch_date', 'cohort', 'window_days', 'spec_version')

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
                  cohort: Optional[str] = None, jump_pct: float = JUMP_PCT,
                  write_rows: bool = True, quiet: bool = False) -> Dict[str, Any]:
    total_w = total_n = 0
    for a, b in _chunks(d_from, d_to):
        rows = collect(a, b, window_days=window_days, cohort=cohort, jump_pct=jump_pct)
        total_n += len(rows)
        if rows and write_rows:
            total_w += write(rows)
        if not quiet:
            print('  %s .. %s：解析 %d 条（母体事件×窗口联合计数）%s' % (
                a, b, len(rows), '（dry-run 不写库）' if not write_rows else ''), flush=True)
    return {'asked': total_n, 'written': total_w, 'range': (str(d_from), str(d_to))}


def verify() -> List[str]:
    """真数体检。每条都数得出行数，不许「查不到」折成 0；四条自证判据都在这里真数。"""
    bad: List[str] = []
    r = engine.query_one(
        f"select count(*) n, count(distinct security_code) codes, "
        f"       count(distinct (security_code, launch_date, cohort)) events, "
        f"       min(launch_date)::text a, max(launch_date)::text z, "
        f"       count(*) filter (where dbl_up_days + dbl_down_days "
        f"         is distinct from dbl_days) 联合不自洽, "
        f"       count(*) filter (where jump_days is not null and coalesce(jump_dbl_days,0) "
        f"         + coalesce(jump_near_days,0) + coalesce(jump_calm_days,0) > jump_days) jump越界, "
        f"       count(*) filter (where up_days + down_days + flat_days > bars) vr超总根, "
        f"       count(*) filter (where dbl_days > bars or near_dbl_days > bars or calm_days > bars "
        f"         or still_days > bars or limit_days > bars "
        f"         or (jump_days is not null and jump_days > bars)) 越界, "
        f"       count(*) filter (where bars = 0) 零场, "
        f"       count(*) filter (where bars < window_days) 短窗, "
        f"       count(*) filter (where win_to >= launch_date) 窗口含启动日, "
        f"       count(*) filter (where cohort = 'anyfirst') anyfirst行, "
        f"       count(*) filter (where cohort = 'anyfirst' and outcome_len is null) anyfirst缺结果, "
        f"       count(outcome_len) filter (where cohort <> 'anyfirst') 非anyfirst有结果, "
        f"       count(*) filter (where cohort <> 'anyfirst') 非anyfirst行, "
        f"       count(*) filter (where jump_days = 0) jump零, "
        f"       count(*) filter (where jump_days is null) jump空 "
        f"  from {TABLE} where spec_version = %s", (SPEC_VERSION,), role='reader')
    if not r or not r['n']:
        return ['这张表一行都没有 —— 回填没跑，还是写库坏了？']
    checks = (('联合不自洽', 'dbl_up_days + dbl_down_days <> dbl_days（联合计数的自证判据，对不上就是'
                             '倍量日里有哪天缺了前一根的复权收盘——判据与真库对不上）'),
              ('jump越界', 'jump_dbl + jump_near + jump_calm > jump_days（同一批日子被数了两遍）'),
              ('vr超总根', 'up + down + flat > bars（分类计数越界）'),
              ('越界', '某一类天数大于 bars（窗口一共就那么多场，多出的是数错了）'),
              ('零场', 'bars=0 的行进了库（一场都没有就该整行不写，一排 0 会被读成「平盘」）'),
              ('窗口含启动日', 'win_to 不早于启动日（窗口本该不含当天，当天往往已涨停）'),
              ('anyfirst缺结果', 'cohort=anyfirst 的行 outcome_len 是 NULL —— 母体就在 fb-2 里，'
                                 '每行必有自己那一行的 seg_len，缺了就是 join 断了'))
    for col, why in checks:
        if int(r[col]):
            bad.append(f'{r[col]} 行{why}')
    latest = engine.scalar('select max(trade_date)::text from market_data.stock_daily')
    print(f'  库里：{r["n"]} 行 / {r["codes"]} 只 / {r["events"]} 个母体事件，启动日 {r["a"]} … {r["z"]}'
          f'（日线最新一场 {latest}）')
    print(f'  不满窗 {r["短窗"]} 行、jump_days=0 {r["jump零"]} 行（真没有）vs NULL {r["jump空"]} 行'
          f'（窗口不满，两件事）、anyfirst {r["anyfirst行"]} 行（outcome 缺 {r["anyfirst缺结果"]}）、'
          f'非 anyfirst {r["非anyfirst行"]} 行（outcome 有值 {r["非anyfirst有结果"]}）')
    if int(r['非anyfirst行']) and int(r['非anyfirst有结果']) * 2 < int(r['非anyfirst行']):
        print('  ⚠ 非 anyfirst 档 outcome 有值率不足一半 —— jump40 的触发日多半不是连板段首日，'
              '这是设计如此；但 board4 一族若也缺，就是 fb-2 覆盖断了，去读侧看那两条 caveat')
    per = engine.query(f"select cohort, count(*) n, count(*) filter (where bars < window_days) 短窗, "
                       f"       count(outcome_len) 有结果 from {TABLE} where spec_version = %s "
                       f"group by 1 order by 1", (SPEC_VERSION,), role='reader') or []
    print('  按 cohort 分：' + '、'.join(f'{p["cohort"]} {p["n"]} 行（短窗 {p["短窗"]}、有结果 {p["有结果"]}）'
                                          for p in per))
    return bad


def sessions_back(n: int) -> dt.date:
    """库里最新一场往前 N 个交易日的日期（每日链重算范围用）。"""
    rows = engine.query(f"select distinct trade_date from market_data.stock_daily order by 1 desc limit {int(n)}",
                        (), role='reader') or []
    return rows[-1]['trade_date'] if rows else dt.date.today()


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description='启动前爆量/急拉 × 量增/量缩（日线代理，ssd-1）')
    ap.add_argument('--months', type=int, default=12, help='往前几个月（默认 12）')
    ap.add_argument('--start', help='区间起日（给了就覆盖 --months）')
    ap.add_argument('--end', help='区间止日（默认库里最新交易日）')
    ap.add_argument('--date', help='只算到这一天（等价 --end）')
    ap.add_argument('--window', type=int, default=WINDOW_DAYS, choices=WINDOWS,
                    help='启动日往前几场（10/20/30，默认 30）')
    ap.add_argument('--cohort', choices=COHORTS, help='只跑一档母体（默认五档全跑：anyfirst ＋ sv-2 四类）')
    ap.add_argument('--jump-pct', type=float, default=JUMP_PCT,
                    help=f'突然拉高阈值（小数比例，默认 {JUMP_PCT}；阈值下闭）')
    ap.add_argument('--recent-sessions', type=int, help='只重算最近 N 个交易日起（每日链用这个）')
    ap.add_argument('--dry-run', action='store_true', help='只解析不写库')
    ap.add_argument('--verify', action='store_true', help='写完/单独跑体检')
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.verify:
        bad = verify()
        print('\n'.join(f'✗ {b}' for b in bad) if bad else '✓ 爆量急拉联合计数表体检通过')
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
    print(f'=== 启动前爆量/急拉联合计数（{SPEC_VERSION}）：启动日 {d_from} .. {d_to}'
          f'，窗口 {args.window} 场，急拉阈值 {args.jump_pct}，母体 {args.cohort or "五档全要（anyfirst ＋ sv-2 四类）"}'
          f'{"（dry-run）" if args.dry_run else ""}', flush=True)
    res = compute_range(d_from, d_to, window_days=args.window, cohort=args.cohort,
                        jump_pct=args.jump_pct, write_rows=not args.dry_run)
    if not res['asked']:
        print('  这一段里没有能凑出窗口的母体事件（区间不对，还是库里没有这段日线？）'
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
