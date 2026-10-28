# -*- coding: utf-8 -*-
"""`/api/quant/startup-quadrant-track` 的读侧：启动前 N 场的四维象限轨迹 + 名次变化分档的答案。

这一屏回答的是用户 2026-10-17 那一问的后半句：**启动前那 30 场里四维象限名次往上爬的票，
是不是更容易走成 4 板、买进去是不是更划算**。所以返回体一半是读数（`keys` 七键横向对照、`rows` 逐条轨迹），
另一半是**答案**（`buckets` 按 `rank_delta` 现算三分位，逐档交后来走到几板与进场收益）。
判据一律不在这里 —— 那些在 `app/compute/startup_quadrant_track.py`（sqt-1）。

四条读侧纪律（都有对应用例）：
  · **`rank_delta` 正数＝名次上升**（rank 越小越靠前）。分档、`rank_up_share`、buckets 的档名全都按这一条读，
    写反了整屏结论就是反的。
  · **象限那一族只在 `oper_score` 有值**（库里其余七键恒为 NULL 是设计）⇒ 非 oper_score 的
    `quad_switches_med` / `quad_last_switch_gap_med` / `quad_first` / `quad_last` 一律交 **null**，
    不是 0、不是空对象（0 会被读成「这些票启动前从没换过象限」）。
  · **分母要说清**。`seg4_rate` 的分母是「fb-2 里有那一行」的事件、`ex5_med`/`win5` 的分母是「有基准/有读数」
    的那些 —— 北交所没有对应分类指数 ⇒ 那批剔出超额中位并在 caveat 点名条数，**不拿 0 当「没赚」**；
    `jump40` 的触发日多半不是连板段首日 ⇒ `outcome_len` 为空的那些行不进 4 板率分母。
  · 坏参数（cohort / key / window）**在 caveats 里点名退回**，不悄悄退回：屏幕上读的是退回之后的那个口径。

聚合全部下推 SQL（百分位、分组、分档计数都在库里做）：这张表 12 个月才 2,263 行，拉到 python 里分档
不至于慢，但 `analytics.score` 那一路的回查实测在宽窗口上会撞 `ft_reader` 60 秒 `statement_timeout`，
而这是一屏**每改一个参数都要重算**的界面 —— 少一条全表扫描就少一发慢查询。
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict, List, Optional

from app.db import engine

TABLE = 'analytics.startup_quadrant_track'
SPEC_VERSION = 'sqt-1'                       # 与 app/compute/startup_quadrant_track.py 同一个数，测试钉两边

#: 七个键的唯一名单（market_mood 一族不进表：同一天全市场同一个数，名次没有横截面意义）
KEY_COLS: Dict[str, str] = {
    'oper_score': '操作评分',
    'temp_score': '温度',
    'vol_temp': '量能温度',
    'sector_heat': '板块热度',
    'stock_quality': '个股质量(X轴)',
    'env_resonance': '环境共振(Y轴)',
    'vol_stage': '量能阶段（档位 1~5，读档位差）',
}
QUADRANT_KEY = 'oper_score'
COHORTS: Dict[str, str] = {
    # 排第一＝这一档才是答「预示」那一问的母体：sv-2 的 board4 一族本身就是按结果筛过的（已走出 ≥4 板），
    # 4 板率恒 100% 是同义反复；anyfirst＝fb-2 全部首板、seg_len 不筛（outcome_len 就是该行自己的段长）。
    'anyfirst': '全部首板（不筛结果，能回答预示那一问）',
    'board4': '4 板及以上（连板段第一天）',
    'board4_45': '4 板及以上且前四根涨幅超四成半',
    'board4_or_gain45': '4 板及以上 或 四日涨幅超四成半',
    'jump40': '双创与北证的 4 日超四成',
}
WINDOWS = (10, 20, 30)
MONTHS = (3, 6, 12)
QUADRANTS = ('Q1', 'Q2', 'Q3', 'Q4')
BUCKET_N = 3


def options() -> Dict[str, Any]:
    """控件清单只在这一份：界面镜像一份白名单，迟早会跟判据那边对不上（`fell_back` 那条 caveat 就是这么抓出来的）。"""
    return {'cohorts': [{'value': k, 'label': v} for k, v in COHORTS.items()],
            'keys': [{'value': 'all', 'label': '七个键全要'}]
                    + [{'value': k, 'label': v} for k, v in KEY_COLS.items()],
            'months': [{'value': m, 'label': f'近 {m} 个月'} for m in MONTHS],
            'windows': [{'value': w, 'label': f'启动前 {w} 场'} for w in WINDOWS]}


CALIBER = (
    '取数口径：母体两个入口（10-18 追加）——`anyfirst`＝`analytics.first_board_shape`（fb-2）的全部首板事件'
    '（seg_len 不筛，这一档才答得了「预示」那一问：sv-2 的 board4 一族本身就是按「回头看 ≥4 板」筛过的，'
    '4 板率恒 100% 是同义反复）；其余四档＝`analytics.startup_volume_profile`（sv-2）那四类启动事件。'
    '启动日＝连板段第一天（`jump40` 是四日窗口的触发日；`anyfirst` 就是首板日当天）；'
    '轨迹＝启动日「之前」N 个该票自己的有效交易日（不含启动日当天，'
    '当天已经涨停、分数被启动本身污染），读数一律来自 `analytics.score`；'
    '`value_delta = last − first`；`rank_delta = rank_first − rank_last`，「正数＝名次上升」（rank 越小越靠前）；'
    '`pct_*` 照库里的 percent_rank 原样存，实测刻度 0~100（库里列注释写的「0~1」不准）；'
    '象限那一族（`quad_*`、`q1_days..q4_days`）只在 `oper_score` 那一行有值，其余六个键在库里就没有象限，'
    '所以交 null 不交 0；`market_mood` 一族（同一天全市场同一个数）不进这张表；'
    '`vol_stage` 那一行读的是档位差（1 冷却 / 2 酝酿 / 3 高潮 / 4 背离 / 5 退潮），不是百分比；'
    '只取 `is_stale=false` 的那一批（旧算法迁入批列注释写明不可与新结果混比，混了 `rank_delta` 就成了'
    '「旧算法减新算法」）⇒ 可用批次比母体晚得多，窗口凑不满 N 场是覆盖事实不是缺陷，'
    '起点与场数看同一份返回里的 `coverage`（现查库，背在注释里就会过期）；'
    '`outcome_len` 取 `analytics.first_board_shape`（fb-2）同 (票, 启动日) 的 seg_len（走到几板，结果变量），'
    '`outcome_gain4` 取 sv-2 的 trigger_gain（小数比例，0.4641＝+46.41%）；'
    '`r5`/`ex5`/`win5` 取 fb-2 的首板次日开盘进场读数：`ex5` 的基准是分类指数（68→000680、6→000002、'
    '30→399102、00→399107），北交所没有对应指数 ⇒ 剔出超额分母并点名条数，不拿 0 当「没赚」；'
    '分档切点按当前筛选出来的那一批 `rank_delta` 现算三分位（左开右闭、切点去重、取值少时按值切），'
    '所以换了 cohort 或窗口，Q1 指的是另一段，页面上必须把区间念出来'
)

DEFINITIONS: Dict[str, str] = {
    'bars': '窗口里实际拿到几场读数。小于 window 就是不满窗（score 可用批次的起点看 `coverage.earliest_score_date`）',
    'value_first': '窗口最早那一场的读数（该票该键在 score 里的 value）',
    'value_last': '紧邻启动日那一场（T-1）的读数',
    'value_delta': 'value_last − value_first。vol_stage 那一行是档位差（1~5 的档位），不是百分比',
    'rank_first': '窗口最早那一场的全市场名次（越小越靠前）',
    'rank_last': 'T-1 那一场的全市场名次（与 rank_first 同一把尺子，越小越靠前）',    'rank_delta': 'rank_first − rank_last。正数＝启动前名次往上爬（不是往下掉），分档就按这一列切',
    'rank_best': '窗口内到过的最好名次（最小值）；rank_worst 是最大名次',
    'pct_first': '窗口最早那一场的全市场分位，刻度 0~100（第 1 名＝100.0）',
    'quad_first': '窗口最早那一场的象限（只有 oper_score 那一行有）；quad_last 是 T-1 那一场',
    'quad_switches': '窗口内相邻两场象限不同的次数。非 oper_score 的键这一列是 null：库里那些键没有象限',
    'quad_last_switch_gap': '最后一次象限翻转距 T-1 有几场：0＝就发生在启动日前一天；'
                            'null＝整个窗口一次都没翻（不是 0）',
    'outcome_len': '后来走到几板（fb-2 的 seg_len）。结果变量，不是筛子；空＝那天不是任何连板段的第一天',
    'outcome_gain4': 'sv-2 的 trigger_gain：board4 一族是连板段累计涨幅、jump40 是四日累计涨幅。'
                     '小数比例（0.4641＝+46.41%）',
    'rank_up_share': '这一族键里 rank_delta>0 的行占比（分母是有名次变化的行）',
    'seg4_rate': '这一档里后来走到 ≥4 板的比例，分母只数 outcome_len 有值的那些行（没结果的行不进分母）',
    'seg_len_med': '这一档 outcome_len 的中位（几板）',
    'r5_med': '首板次日开盘进场后第 5 场收盘的中位涨幅（后复权，绝对，百分数）。空＝fb-2 没有那一行',
    'ex5_med': '同一场相对对应分类指数的超额中位。北交所没有基准 ⇒ 不计入中位，剔掉几条见 caveats',
    'win5': 'r5 为正的比例（分母＝有 r5 的那些行）。不拿空值当「没赚」',
    'n': '这一档 / 这一族键的行数（一行＝一个母体事件在一个键上的轨迹，不是票数）',
    'range': '这一档的 rank_delta 区间，左开右闭；`—` 表示这一头不设限。档名 Q1/Q2/Q3 是三分位档位'
             '（Q1＝名次上升最少那一档），与四维象限的 Q1~Q4 不是一回事',
    'coverage': '本屏这批筛选覆盖的起止、事件数、只数，以及窗口满/不满各多少个。'
                'earliest_score_date 是 score 里可用（非陈旧）批次的起点，窗口凑不满都是从这一天倒推出来的',
}

_CAVEATS = {
    'no_batch': '窗口 {w} 场那一批还没算过（这张表按 window_days 分行存）—— '
                '跑一次 `python -m app.compute.startup_quadrant_track --window {w} --months {m}` 才有',
    'short_window': 'score 里可用（`is_stale=false`）的读数只从 {d} 起，共 {s} 场 ⇒ 启动日早于它的母体事件'
                    '往前凑不满 {w} 场：这一屏里 {n} 个事件的窗口不满（bars<{w}）。那是覆盖事实，不是缺数',
    'no_events': '当前筛选（{c} / 近 {m} 个月 / 窗口 {w}）一个母体事件都没有 —— 是这段日子没有启动事件，'
                 '还是启动日都在可用读数之前？',
    'fell_back': '传来的{what}「{asked}」不在白名单里，已退回「{now}」—— 屏幕上读的是退回之后的那个口径，'
                 '不是你以为的那一个（悄悄退回去而不说一句，是这类控件最坏的坏法）',
    'no_bench': '北交所这批没有对应分类指数 ⇒ {n} 行的超额不计入中位（只报绝对收益，不拿上证指数冒充）',
    'no_outcome': '{n} 个事件在 `analytics.first_board_shape` 里没有同一天的行（`jump40` 的触发日多半不是'
                  '连板段的第一天）⇒ 它们不进「走到 4 板率」的分母，也不该被读成「一板都没走」',
    'no_rank': '{n} 行的 rank_delta 是空（窗口里某一场没有名次读数）⇒ 不进分档，也不当「名次没变」',
    'thin': '最空的一档只有 {n} 条 —— 这么薄的分档只是读数，不足以当规律',
    'thin_result': '「走到 4 板率」与进场收益只数得到「结果那一半」：最空的一档 {b} 条轨迹里只有 {n} 条在 '
                   '`analytics.first_board_shape` 里有同一天的行 ⇒ 那一档的 4 板率/`r5`/`ex5` 是 {n} 条样本的'
                   '读数，不是规律（`jump40` 的触发日多半不是连板段的第一天，所以这一档的结果列本来就稀）',
    'one_bucket': '这一列在当前筛选里只有 {n} 个不同的名次变化值，分不出三档，只交一整档 —— '
                  '所以这一屏没有「爬得快的比爬得慢的是不是更容易走成 4 板」这一问的答案',
    'quad_null': '象限那一族只有 oper_score 那一行有值，其余六个键交 null 不是 0（库里那些键就没有象限这回事）',
    'no_power': '这一档母体本身就是「回头看 ≥4 板」筛出来的（sv-2 的 board4 / board4_45）⇒ 每一行后来都到 4 板，'
                '「名次上升的是不是更容易走到 4 板」这一问在这里没有分辨力（4 板率恒 100%）。'
                '要读这一问就换 cohort=anyfirst（全部首板、不筛结果，这一档才有分辨力），'
                '或看 board4_or_gain45 与 jump40 两档，也可以去 /volume-double 的首板形状那一屏',
    'stage_unit': 'vol_stage 那一行的 value 是档位（1~5）⇒ value_delta 读档位差，读成百分比就是错',
    'mood_out': 'market_mood 一族不进这张表：实测同一天全市场同一个值（名次恒 1、分位恒 50.0090），'
                '它的「名次变化」没有横截面意义',
    'limit_cut': '已达取数上限 {n} 行：下面的轨迹明细被截断了，屏幕上看到的不是全部',
}


def _num(v: Any) -> Optional[float]:
    """出口清洗：`Decimal` 要收成 float，NaN/NaT 一律交 None（裸 NaN 会让浏览器 JSON.parse 炸）。"""
    if v is None or isinstance(v, bool):
        return None if v is None else float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _int(v: Any) -> Optional[int]:
    return None if v is None else int(v)


def _pct(part: Optional[float], whole: Optional[float]) -> Optional[float]:
    return round(100.0 * part / whole, 1) if (part is not None and whole) else None


def _conds(cohort: str, window: int, d_from: dt.date, key: Optional[str] = None,
           alias: str = '') -> tuple:
    """筛选条件只此一份（七键汇总、分档、明细共用）—— 抄两遍就会有一处忘了带 window_days。

    `alias` 是给分档那一条用的：那一发 left join 了 `first_board_shape`，两边都有
    `spec_version`/`security_code`/`launch_date`，不带别名直接就是 `column is ambiguous`。
    """
    a = f'{alias}.' if alias else ''
    p: Dict[str, Any] = {'spec': SPEC_VERSION, 'cohort': cohort, 'w': window, 'd_from': str(d_from)}
    where = (f"{a}spec_version = %(spec)s and {a}cohort = %(cohort)s and {a}window_days = %(w)s "
             f"and {a}launch_date >= %(d_from)s::date")
    if key:
        where += f" and {a}capability_key = %(key)s"
        p['key'] = key
    return where, p


def track(cohort: str = 'board4', months: int = 12, window: int = 30,
          key: str = 'all', limit: int = 200) -> Dict[str, Any]:
    """七键横向对照 + 名次变化三分位分档（答案那一半）+ 逐条轨迹 + 诚实性提示。参数全部来自界面控件。"""
    asked_cohort, asked_key, asked_window = cohort, key, window
    cohort = cohort if cohort in COHORTS else 'board4'
    key = key if (key == 'all' or key in KEY_COLS) else 'all'
    window = window if window in WINDOWS else 30
    months = max(1, min(12, int(months or 12)))
    limit = max(1, min(600, int(limit or 200)))

    latest = engine.scalar(f"select max(launch_date)::text from {TABLE} where spec_version = %s",
                           (SPEC_VERSION,), role='reader')
    if not latest:
        return {'available': False,
                'reason': '库里还没有启动前象限轨迹的行（`python -m app.compute.startup_quadrant_track '
                          '--months 12` 跑一次才有）'}
    d_to = dt.date.fromisoformat(latest)
    d_from = d_to - dt.timedelta(days=months * 31)

    # ① 这一档窗口到底算过没有（这张表按 window_days 分行存，没算过的窗口不该被读成「没有轨迹」）
    have = engine.scalar(f"select count(*) from {TABLE} where spec_version = %s and window_days = %s",
                         (SPEC_VERSION, window), role='reader') or 0
    if not have:
        return {'available': False, 'reason': _CAVEATS['no_batch'].format(w=window, m=months)}

    where, params = _conds(cohort, window, d_from)
    oper_where = where + " and capability_key = 'oper_score'"

    # ② 七键横向对照（名次变化、分位变化、象限翻转各是什么水平）
    raw_keys = engine.query(
        f"select capability_key as key, count(*) as n, "
        f"       percentile_cont(0.5) within group (order by rank_first)  as rank_first_med, "
        f"       percentile_cont(0.5) within group (order by rank_last)   as rank_last_med, "
        f"       percentile_cont(0.5) within group (order by rank_delta)  as rank_delta_med, "
        f"       count(*) filter (where rank_delta > 0) as up, "
        f"       count(*) filter (where rank_delta < 0) as down, "
        f"       count(rank_delta) as dn, "
        f"       percentile_cont(0.5) within group (order by pct_first)   as pct_first_med, "
        f"       percentile_cont(0.5) within group (order by pct_last)    as pct_last_med, "
        f"       percentile_cont(0.5) within group (order by value_first) as value_first_med, "
        f"       percentile_cont(0.5) within group (order by value_last)  as value_last_med, "
        f"       percentile_cont(0.5) within group (order by quad_switches) as quad_switches_med, "
        f"       percentile_cont(0.5) within group (order by quad_last_switch_gap) as gap_med, "
        f"       count(*) filter (where quad_first = 'Q1') as f1, "
        f"       count(*) filter (where quad_first = 'Q2') as f2, "
        f"       count(*) filter (where quad_first = 'Q3') as f3, "
        f"       count(*) filter (where quad_first = 'Q4') as f4, "
        f"       count(*) filter (where quad_last  = 'Q1') as l1, "
        f"       count(*) filter (where quad_last  = 'Q2') as l2, "
        f"       count(*) filter (where quad_last  = 'Q3') as l3, "
        f"       count(*) filter (where quad_last  = 'Q4') as l4, "
        f"       min(bars) as bars_min, max(bars) as bars_max "
        f"  from {TABLE} where {where} group by capability_key", params, role='reader') or []
    keys_out = [_key_row(r) for r in sorted(raw_keys, key=lambda r: list(KEY_COLS).index(r['key'])
                                            if r['key'] in KEY_COLS else 99)]

    # ③ 答案那一半：按 oper_score 的 rank_delta 现算三分位（左开右闭、去重、取值少时按值切）
    q = engine.query_one(
        f"select count(rank_delta) as notnull, count(distinct rank_delta) as uniq, "
        f"       percentile_cont(1.0 / {BUCKET_N}) within group (order by rank_delta) as c0, "
        f"       percentile_cont(2.0 / {BUCKET_N}) within group (order by rank_delta) as c1 "
        f"  from {TABLE} where {oper_where}", params, role='reader') or {}
    uniq = int(q.get('uniq') or 0)
    no_rank = int(engine.scalar(f"select count(*) from {TABLE} where {oper_where} and rank_delta is null",
                                params, role='reader') or 0)
    if uniq <= BUCKET_N:
        vals = engine.query(f"select distinct rank_delta as v from {TABLE} "
                            f"where {oper_where} and rank_delta is not null order by 1", params,
                            role='reader') or []
        cuts = [int(v['v']) for v in vals][:-1]
    else:
        cuts = sorted({c for c in (_num(q.get(f'c{i}')) for i in range(BUCKET_N - 1)) if c is not None})
    where_t, params_t = _conds(cohort, window, d_from, None, 't')
    qparams = dict(params_t)
    for i, c in enumerate(cuts):
        qparams[f'cut{i}'] = c
    case = ('case ' + ' '.join(f'when t.rank_delta <= %(cut{i})s then {i}' for i in range(len(cuts)))
            + f' else {len(cuts)} end') if cuts else '0'
    bucket_where = where_t + " and t.capability_key = 'oper_score' and t.rank_delta is not null"

    inner = (f"select {case} as g, t.rank_delta, t.quad_switches, t.outcome_len, t.bars, "
             f"       f.r5, f.ex5, f.bench_code "
             f"  from {TABLE} t left join analytics.first_board_shape f "
             f"    on f.security_code = t.security_code and f.launch_date = t.launch_date "
             f"   and f.spec_version = 'fb-2' "
             f" where {bucket_where}")
    agg_rows = engine.query(
        f"select g, count(*) as n, percentile_cont(0.5) within group (order by rank_delta) as rd_med, "
        f"       count(*) filter (where outcome_len >= 4) as s4, count(outcome_len) as outcome_n, "
        f"       percentile_cont(0.5) within group (order by outcome_len) as seg_len_med, "
        f"       percentile_cont(0.5) within group (order by r5) as r5_med, "
        f"       percentile_cont(0.5) within group (order by ex5) as ex5_med, "
        f"       count(*) filter (where r5 > 0) as win_pos, count(r5) as r5_n, "
        f"       percentile_cont(0.5) within group (order by quad_switches) as qs_med, "
        f"       count(*) filter (where r5 is not null and ex5 is null) as no_bench, "
        f"       min(rank_delta) as lo_obs, max(rank_delta) as hi_obs "
        f"  from ({inner}) x group by grouping sets ((g), ())", qparams, role='reader') or []

    base_raw = next((r for r in agg_rows if r.get('g') is None), None)
    per_bucket = {int(r['g']): r for r in agg_rows if r.get('g') is not None}
    buckets = [_bucket(g, per_bucket.get(g), cuts) for g in sorted(per_bucket)]
    base = _bucket(None, base_raw, cuts)

    # ④ 覆盖度：事件数 / 只数 / 满窗与不满窗各多少（按 oper_score 那一行判满不满）
    cov = engine.query_one(
        f"select count(distinct (security_code, launch_date)) as events, "
        f"       count(distinct security_code) as codes, "
        f"       count(*) filter (where bars >= window_days) as full_bars, "
        f"       count(*) filter (where bars < window_days) as short_bars "
        f"  from {TABLE} where {oper_where}", params, role='reader') or {}
    events = int(cov.get('events') or 0)
    # 「可用读数从哪天起」只数**进这张表的那七个键**（market_mood 一族起点更早但根本没进表，
    # 拿它当起点会让 caveat 里那句「凑不满窗」的日子对不上）
    earliest = engine.scalar(
        "select min(trade_date)::text from analytics.score where not is_stale "
        "and capability_key = any(%s)", (list(KEY_COLS),), role='reader')
    sessions = int(engine.scalar(
        "select count(distinct trade_date) from analytics.score where not is_stale "
        "and capability_key = 'oper_score'", (), role='reader') or 0)

    # ⑤ 逐条轨迹（key 控键；一屏最多 600 行，截断要出声）
    rows_where, rows_params = _conds(cohort, window, d_from, key if key != 'all' else None)
    fetched = engine.query(
        f"select security_code as code, name, board, launch_date::text as launch_date, cohort, "
        f"       capability_key, bars, value_first, value_last, value_delta, "
        f"       rank_first, rank_last, rank_delta, rank_best, rank_worst, "
        f"       quad_first, quad_last, quad_switches, quad_last_switch_gap, "
        f"       outcome_len, outcome_gain4, "
        f"       count(*) over () as total "
        f"  from {TABLE} where {rows_where} "
        f" order by launch_date desc, rank_delta desc nulls last, security_code, capability_key "
        f" limit %(limit)s", {**rows_params, 'limit': limit}, role='reader') or []
    total = int(fetched[0]['total']) if fetched else 0
    rows = [{'code': r['code'], 'name': r['name'], 'board': r['board'], 'launch_date': r['launch_date'],
             'cohort': r['cohort'], 'capability_key': r['capability_key'],
             'capability_label': KEY_COLS.get(r['capability_key'], r['capability_key']),
             'bars': _int(r['bars']),
             **{k: _num(r[k]) for k in ('value_first', 'value_last', 'value_delta')},
             **{k: _int(r[k]) for k in ('rank_first', 'rank_last', 'rank_delta', 'rank_best', 'rank_worst')},
             'quad_first': r['quad_first'], 'quad_last': r['quad_last'],
             'quad_switches': _int(r['quad_switches']),
             'quad_last_switch_gap': _int(r['quad_last_switch_gap']),
             'outcome_len': _int(r['outcome_len']), 'outcome_gain4': _num(r['outcome_gain4'])}
            for r in fetched]

    caveats = []
    if asked_cohort != cohort:
        caveats.append(_CAVEATS['fell_back'].format(what='母体档', asked=asked_cohort, now=cohort))
    if asked_key != key:
        caveats.append(_CAVEATS['fell_back'].format(what='判据键', asked=asked_key, now=key))
    if asked_window != window:
        caveats.append(_CAVEATS['fell_back'].format(what='窗口', asked=asked_window, now=window))
    if not events:
        caveats.append(_CAVEATS['no_events'].format(c=COHORTS[cohort], m=months, w=window))
    if earliest:
        caveats.append(_CAVEATS['short_window'].format(d=earliest, s=sessions, w=window,
                                                       n=int(cov.get('short_bars') or 0)))
    caveats.append(_CAVEATS['quad_null'])
    caveats.append(_CAVEATS['stage_unit'])
    caveats.append(_CAVEATS['mood_out'])
    no_outcome = int(engine.scalar(f"select count(*) from {TABLE} where {oper_where} "
                                   f"and outcome_len is null", params, role='reader') or 0)
    if no_outcome:
        caveats.append(_CAVEATS['no_outcome'].format(n=no_outcome))
    if base['seg4_rate'] == 100.0 and base['n']:
        # 分档表上「4 板率」三档全 100% 不是「名次爬得快没用」，而是这一档母体筛子就是 ≥4 板
        caveats.append(_CAVEATS['no_power'].format())
    if no_rank:
        caveats.append(_CAVEATS['no_rank'].format(n=no_rank))
    if int((base_raw or {}).get('no_bench') or 0):
        caveats.append(_CAVEATS['no_bench'].format(n=int(base_raw['no_bench'])))
    thinnest = min((b['n'] for b in buckets), default=0)
    if 0 < thinnest < 20:
        caveats.append(_CAVEATS['thin'].format(n=thinnest))
    # 答案那一半的分母是「有结果的事件」，不是轨迹条数：jump40 一档 38 条轨迹里只有 7 条在 fb-2 有行，
    # 那 7 条拼出来的 0% 四板率不该被读成「这一档一只都没走到 4 板」。
    thinnest_outcome = min((int((per_bucket[g] or {}).get('outcome_n') or 0) for g in per_bucket), default=0)
    if 0 < thinnest_outcome < 20:
        thin_row = next((per_bucket[g] for g in sorted(per_bucket)
                         if int((per_bucket[g] or {}).get('outcome_n') or 0) == thinnest_outcome), {})
        caveats.append(_CAVEATS['thin_result'].format(b=int(thin_row.get('n') or 0), n=thinnest_outcome))
    if len(buckets) <= 1:
        caveats.append(_CAVEATS['one_bucket'].format(n=uniq))
    if total > len(rows):
        caveats.append(_CAVEATS['limit_cut'].format(n=limit))

    return {'available': True, 'spec_version': SPEC_VERSION,
            'params': {'cohort': cohort, 'cohort_text': COHORTS[cohort], 'months': months,
                       'window': window, 'key': key, 'limit': limit},
            'coverage': {'from': str(d_from), 'to': str(d_to), 'events': events,
                         'codes': int(cov.get('codes') or 0), 'earliest_score_date': earliest,
                         'window_full_events': int(cov.get('full_bars') or 0),
                         'window_short_events': int(cov.get('short_bars') or 0)},
            'keys': keys_out, 'buckets': buckets, 'base': base, 'rows': rows,
            'cuts': cuts, 'caveats': caveats, 'caliber': CALIBER, 'definitions': DEFINITIONS,
            'options': options()}


def _key_row(r: dict) -> Dict[str, Any]:
    """非 oper_score 的键：象限那一族交 **null**（库里那些键就没有象限），不是 0、不是空对象。"""
    is_oper = r['key'] == QUADRANT_KEY
    return {'key': r['key'], 'label': KEY_COLS.get(r['key'], r['key']), 'n': int(r['n'] or 0),
            'rank_first_med': _num(r['rank_first_med']), 'rank_last_med': _num(r['rank_last_med']),
            'rank_delta_med': _num(r['rank_delta_med']),
            'rank_up_share': _pct(_int(r['up']), _int(r['dn'])),
            'rank_down_share': _pct(_int(r['down']), _int(r['dn'])),
            'pct_first_med': _num(r['pct_first_med']), 'pct_last_med': _num(r['pct_last_med']),
            'value_first_med': _num(r['value_first_med']), 'value_last_med': _num(r['value_last_med']),
            'quad_switches_med': _num(r['quad_switches_med']) if is_oper else None,
            'quad_last_switch_gap_med': _num(r['gap_med']) if is_oper else None,
            'quad_first': {k: int(r[f'f{i}'] or 0) for i, k in enumerate(QUADRANTS, 1)} if is_oper else None,
            'quad_last': {k: int(r[f'l{i}'] or 0) for i, k in enumerate(QUADRANTS, 1)} if is_oper else None}


def _bucket(g: Optional[int], row: Optional[dict], cuts: List[int]) -> Dict[str, Any]:
    """一档（或全体基线）的读数。区间左开右闭 ⇒ 第一档不设下限、最后一档不设上限。

    ⚠️ 下限必须写 `cuts[g - 1] if g >= 1`，不能写 `if g - 1 < len(cuts)`：第一档的 -1 会绕回
    最后一个切点，屏幕上就念出「2～1（不含 2）」那种倒着的区间（fb-2 首发实测踩过）。
    """
    row = row or {}
    n = int(row.get('n') or 0)
    out = {'label': '全部母体事件' if g is None else f'Q{g + 1}',
           'n': n,
           'rank_delta_med': _num(row.get('rd_med')),
           'seg4_rate': _pct(_int(row.get('s4')), _int(row.get('outcome_n'))),
           'seg_len_med': _num(row.get('seg_len_med')),
           'r5_med': _num(row.get('r5_med')), 'ex5_med': _num(row.get('ex5_med')),
           'win5': _pct(_int(row.get('win_pos')), _int(row.get('r5_n'))),
           'quad_switches_med': _num(row.get('qs_med')),
           'lo_obs': _num(row.get('lo_obs')), 'hi_obs': _num(row.get('hi_obs'))}
    if g is not None:
        out['range'] = [cuts[g - 1] if g >= 1 else None, cuts[g] if g < len(cuts) else None]
    return out
