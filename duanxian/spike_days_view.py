# -*- coding: utf-8 -*-
"""`/api/quant/startup-spike-days` 的读侧：启动前 N 场爆量／突然拉高 × 量增／量缩（日线代理）的答案。

这一屏回答用户 10-18 追加的那一问（原话）：「分析前 30 个交易日，看看有多少爆量，或者突然拉高，
这种情况分别出现在**量增的天数还是量减的天数**」。分钟线已取消，所以这里全部是**日线代理**读数
（一根交易日一个数），屏上必须写清这一点、不冒充分时图上的爆量/急拉。核心是**联合计数**：
`joint` 那一块交「倍量日里涨的占几成、跌的占几成」，`jumps` 那一块交「急拉落在倍量还是缩量」；
`buckets` 再按 `dbl_up_days` 现算三分位，逐档交**结果**（4 板率 / 进场后 5 场）——两半合起来才是答案。
判据一律不在这里 —— 那些在 `app/compute/startup_spike_days.py`（ssd-1）。

四条读侧纪律（都有对应用例）：
  · **母体档白名单里 `anyfirst` 排第一**（全部首板、不筛结果）：sv-2 的 board4 一族本身就是按
    「回头看 ≥4 板」筛过的，那一问在它们身上恒 100% 是同义反复（`no_power` caveat 会点名）。
  · **`jump_days=0`（凑满窗口真的一次没拉高）与 NULL（窗口不满，没资格说 0）是两件事**，
    两块读数分开数、caveat 里点名条数。
  · **分母要说清**。`seg4_rate` 的分母是 outcome_len 有值的行；`ex5_med`/`win5` 的分母是
    「有基准/有读数」的那些 —— 北交所没有对应分类指数 ⇒ 剔出超额中位并点名条数，不拿 0 当「没赚」。
  · **坏参数（cohort / window / jump_pct）在 caveats 里点名退回**，不悄悄退回：屏幕上读的是
    退回之后的那个口径。另：库里 `jump_*` 是写侧那一批按写侧阈值算好的 —— 读侧改 `jump_pct`
    改不动库里的数，必须出声（那条 caveat 会给要重跑的命令行）。

聚合全部下推 SQL（百分位、分组、分档计数都在库里做），与 `quadrant_track_view.py` 同一族写法：
`grouping sets ((g), ())` 一次拿回「每档汇总 + 全体基线」，切点左开右闭、去重、取值少时按值切。
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict, List, Optional

from app.db import engine

TABLE = 'analytics.startup_spike_days'
SPEC_VERSION = 'ssd-1'                        # 与 app/compute/startup_spike_days.py 同一个数，测试钉三处
JUMP_PCT = 0.07                               # 与写侧同一个数（库里那批 jump_* 就是按它算的），测试钉两边

#: 母体档：anyfirst 必须排第一（界面下拉顺序就是这份顺序）
COHORTS: Dict[str, str] = {
    'anyfirst': '全部首板（不筛结果，能回答预示那一问）',
    'board4': '4 板及以上（连板段第一天）',
    'board4_45': '4 板及以上且前四根涨幅超四成半',
    'board4_or_gain45': '4 板及以上 或 四日涨幅超四成半',
    'jump40': '双创与北证的 4 日超四成',
}
WINDOWS = (10, 20, 30)
MONTHS = (3, 6, 12)
BUCKET_N = 3
#: 急拉阈值可传的合法区间（端点与这里同一条；越界退回模块常量并出声）
JUMP_MIN, JUMP_MAX = 0.03, 0.15


def options() -> Dict[str, Any]:
    """控件清单只在这一份：界面镜像一份白名单，迟早会跟判据那边对不上（`fell_back` 那条 caveat 就是这么抓出来的）。"""
    return {'cohorts': [{'value': k, 'label': v} for k, v in COHORTS.items()],
            'months': [{'value': m, 'label': f'近 {m} 个月'} for m in MONTHS],
            'windows': [{'value': w, 'label': f'启动前 {w} 场'} for w in WINDOWS]}


CALIBER = (
    '取数口径：母体两档来源（10-18）——`anyfirst`＝`analytics.first_board_shape`（fb-2）的全部首板事件'
    '（seg_len 不筛，这一档才答得了「预示」：sv-2 的 board4 一族本身就是按「回头看 ≥4 板」筛过的）；'
    '其余四档＝`analytics.startup_volume_profile`（sv-2）那四类启动事件，对照用。'
    '窗口＝启动日「之前」N 个该票自己的有效交易日（不含启动日当天，当天往往已涨停、读数被启动本身污染）。'
    '⚠️ 这是「日线代理」判据（一根交易日一个数），不是分时图上的爆量/急拉——分钟线口径已按用户要求取消。'
    'vr 尺子与 `app/compute/startup_volume.py` 的 classify 同一条：up＝vr>1、down＝vr<1、'
    'flat＝四位收口后精确 1.0000、still＝0.95~1.05；倍量＝vr>=2.0、准倍量＝阈值区间下闭上开、缩量＝vr<=0.5'
    '（阈值引用 startup_volume.py 常量，写侧不抄数）。vr 只读 `analytics.volume_ratio_daily`（vr-1）。'
    '爆量＝倍量日的日线代理；突然拉高＝当日「后复权收益」 >= 0.07（阈值下闭，正好等于也算；'
    '复权式子同 `duanxian/day_returns.py`：close × adjustment_factor 相对前一根同式，不读 pct_change）。'
    '联合计数是本屏的核心：每个倍量/准倍量/缩量日再按当日涨跌分堆，`dbl_up_days + dbl_down_days = dbl_days`'
    '（平盘归「跌」那一头）；急拉日再按量型分堆，`jump_dbl + jump_near + jump_calm <= jump_days`'
    '（差额是既非倍量也非缩量的普通量日子）。'
    '窗口不满（bars<window）时 jump 那一族整组为 NULL：0 是「凑满了 N 场一次都没拉高」，'
    '不满窗没有资格说 0 —— jump_days=0（真没有）与 NULL（窗口不满）是两件事。'
    '涨停天数容差引用 `app/compute/limit_prices.py` 的全项目唯一常量 LIMIT_EPS（半分），不新写 0.01。'
    '结果四列一律从 fb-2 同 (票,启动日) join：outcome_len 是走到几板（结果变量）、outcome_r5/ex5 是'
    '首板次日开盘进场后第 5 场的绝对/超额读数；ex5 的基准是分类指数（68→000680、6→000002、30→399102、'
    '00→399107），北交所没有对应指数 ⇒ 超额为空，剔出分母并点名条数，不拿 0 当「没赚」。'
    '分档切点按当前筛选出来的那一批 `dbl_up_days` 现算三分位（左开右闭、切点去重、取值少时按值切），'
    '所以换了 cohort 或窗口，Q1 指的是另一段，页面上必须把区间念出来'
)

DEFINITIONS: Dict[str, str] = {
    'bars': '窗口里实际拿到几场。小于 window 就是不满窗（上市太近/停牌；不满窗的行 jump 那一族整组为 NULL）',
    'win_from': '窗口最早一场；win_to＝紧邻启动日那一场（T-1），启动日当天不进窗口',
    'up_days': '量增天数（vr>1）；down_days＝量减（vr<1）；flat_days＝四位收口精确 1.0000；'
               'still_days＝0.95~1.05（与 up/down 有重叠，那是 sv-2 尺子的原样）',
    'dbl_days': '爆量的日线代理：vr>=2.0 的天数（阈值引用 startup_volume.py 的 DBL）',
    'near_dbl_days': '准倍量天数（下闭上开：NEAR_DBL<=vr<DBL，等号归下界，同一档只被数一次）',
    'calm_days': '缩量天数（vr<=0.5，引用 CALM）',
    'dbl_up_days': '联合计数（这一屏的核心）：倍量日里当日涨（后复权收益 >0）的天数。'
                   '恒有 dbl_up_days + dbl_down_days = dbl_days，这条自证判据写侧 verify 与测试两边都盯着',
    'dbl_down_days': '倍量日里当日跌（收益 <=0，平盘算这一头）的天数',
    'near_up_days': '准倍量日里当日涨的天数（near_up + near_down = near_dbl_days）',
    'near_down_days': '准倍量日里当日跌的天数',
    'calm_up_days': '缩量日里当日涨的天数（calm_up + calm_down = calm_days）',
    'calm_down_days': '缩量日里当日跌的天数',
    'jump_days': '突然拉高的日线代理：当日后复权收益 >= 0.07（阈值下闭）的天数。0＝凑满窗口真的一次没拉高；'
                 'NULL＝窗口不满（没资格说 0）——两件事',
    'jump_up_days': '急拉日里当日涨的天数（jump_up + jump_down = jump_days）',
    'jump_down_days': '急拉日里当日跌（<=0）的天数（收益 >= 阈值后这一列正常就该是 0）',
    'jump_dbl_days': '急拉且同日倍量的天数。「急拉落在倍量还是缩量」读 jump_dbl/jump_near/jump_calm 三个数',
    'jump_near_days': '急拉且同日准倍量的天数',
    'jump_calm_days': '急拉且同日缩量的天数（拉高配缩量＝没有量配合的拉高）',
    'jump_limit_days': '急拉且当日封住涨停的天数（容差 LIMIT_EPS，全项目唯一常量）',
    'limit_days': '窗口内涨停场数（同一容差判据）',
    'first_dbl_gap': '窗口内第一次倍量距 T-1 有几场：0＝就发生在启动日前一天；NULL＝整个窗口没有倍量（不是 0）',
    'first_jump_gap': '第一次急拉距 T-1 有几场；没有急拉＝NULL；窗口不满＝NULL（jump 族整组随不满窗交 NULL）',
    'outcome_len': '后来走到几板（fb-2 的 seg_len，结果变量）。anyfirst 档恒有值（就是该行自己的段长）；'
                   '空＝fb-2 没有那一行（jump40 的触发日多半不是连板段第一天），不该被读成「一板都没走」',
    'outcome_seg_complete': 'fb-2：段走完没有。false＝还在走，outcome_len 是下界',
    'outcome_r5': '首板次日开盘进场后第 5 场收盘的绝对涨幅（后复权，百分数）。空＝没凑满，0 才是真平盘',
    'outcome_ex5': '同一场相对对应分类指数的超额。北交所没有基准 ⇒ NULL 并被剔出超额分母（点名条数见 caveats）',
    'joint': '联合计数总读数：dbl_total_days＝全档倍量日合计，*_share＝那一类日子里「当日涨」/「当日跌」各占几成'
             '（分母是对应类型的天数合计，不是事件数）',
    'jumps': '急拉读数：jump_days_med＝满窗行的中位急拉天数；jump_on_*_share＝急拉日落在倍量/准倍量/缩量的占比；'
             'jump_days_zero_events 与 jump_days_null_events 是「真没有」与「窗口不满」两个数，别混读',
    'seg4_rate': '这一档里后来走到 ≥4 板的比例，分母只数 outcome_len 有值的行（没结果的行不进分母）。'
                 'board4/`board4_45` 两档恒 100% 是同义反复（母体筛子就是 ≥4 板），要看分辨力就切 anyfirst',
    'r5_med': '这一档 outcome_r5 的中位（进场后 5 场绝对涨幅，分母＝有读数的行）',
    'ex5_med': '这一档 outcome_ex5 的中位。北交所无基准的行已剔出（剔掉几条见 caveats）',
    'win5': 'outcome_r5 为正的比例（分母＝有 r5 的那些行），不拿空值当「没赚」',
    'range': '这一档 dbl_up_days 的区间，左开右闭；`—` 表示这一头不设限。档名 Q1/Q2/Q3 是三分位档位',
    'n': '这一档 / 这一族母体的行数（一行＝一个母体事件的窗口联合计数，不是票数）',
    'coverage': '本屏这批筛选覆盖的起止、事件数、只数、满窗/不满窗各多少，以及 vr 表起点（vr-1 最早一场）',
}

_CAVEATS = {
    'no_batch': '窗口 {w} 场那一批还没算过（这张表按 window_days 判，主键不带窗口 —— 换窗重灌是覆盖同一批行）—— '
                '跑一次 `python -m app.compute.startup_spike_days --window {w} --months {m}` 才有',
    'no_events': '当前筛选（{c} / 近 {m} 个月 / 窗口 {w}）一个母体事件都没有 —— 是这段日子没有启动事件，'
                 '还是启动日都太早、窗口一场都凑不出？',
    'fell_back': '传来的{what}「{asked}」不在白名单里，已退回「{now}」—— 屏幕上读的是退回之后的那个口径，'
                 '不是你以为的那一个（悄悄退回去而不说一句，是这类控件最坏的坏法）',
    'proxy': '这是「日线代理」读数（一根交易日一个数：当日量÷前一根量、当日后复权收益），'
             '不是分时图上的爆量/急拉 —— 分钟线口径已按要求取消，这一屏没有那个粒度，别把它读成分时',
    'short_window': '窗口不满（bars<{w}）的有 {n} 条：vr 表（vr-1）最早只到 {d}，上市太近或长期停牌的票'
                    '往前凑不满 {w} 场。那是覆盖事实不是缺数',
    'jump_null': 'jump_days=0 的 {z} 条是「凑满窗口真的一次没拉高」；jump_days 为 NULL 的 {n} 条是'
                 '「窗口不满，没资格说 0」—— 两件事，别把 NULL 读成 0，也别把 0 读成缺数',
    'jump_pct': '传来的急拉阈值 {asked} 与库里那批 jump_* 的写侧阈值 {now} 不一致 —— 读侧改不动库里的数：'
                '那一列是按写侧 `--jump-pct {now}` 算好落库的。要换阈值请重跑写侧，屏幕上读的是库里的口径',
    'no_bench': '北交所这批没有对应分类指数 ⇒ {n} 行的超额不计入中位（只报绝对收益，不拿上证指数冒充）',
    'no_outcome': '{n} 行在 fb-2 里没有同一天的行（多半是 jump40 的触发日不是连板段第一天）⇒ 它们不进'
                  '「走到 4 板率」的分母，也不该被读成「一板都没走」',
    'no_power': '这一档母体本身就是「回头看 ≥4 板」筛出来的（sv-2 的 board4 / board4_45）⇒ 每一行后来都到 4 板，'
                '「爆量/急拉预示什么」这一问在这里没有分辨力（4 板率恒 100%）。要读这一问就换 cohort=anyfirst'
                '（全部首板、不筛结果）',
    'thin': '最空的一档只有 {n} 条 —— 这么薄的分档只是读数，不足以当规律',
    'one_bucket': '这一列在当前筛选里只有 {n} 个不同的取值，分不出三档，只交一整档 —— 所以这一屏没有'
                  '「倍量涨得多还是跌得多、分档后结果有没有差别」这一问的答案',
    'limit_cut': '已达取数上限 {n} 行：下面的逐条明细被截断了，屏幕上看到的不是全部',
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


def _conds(cohort: str, window: int, d_from: dt.date, alias: str = '') -> tuple:
    """筛选条件只此一份（汇总、分档、明细共用）—— 抄两遍就会有一处忘了带 window_days。"""
    a = f'{alias}.' if alias else ''
    p: Dict[str, Any] = {'spec': SPEC_VERSION, 'cohort': cohort, 'w': window, 'd_from': str(d_from)}
    where = (f"{a}spec_version = %(spec)s and {a}cohort = %(cohort)s and {a}window_days = %(w)s "
             f"and {a}launch_date >= %(d_from)s::date")
    return where, p


def board(cohort: str = 'anyfirst', months: int = 12, window: int = 30,
          jump_pct: Optional[float] = None, limit: int = 200) -> Dict[str, Any]:
    """联合计数 + 急拉读数 + 按 `dbl_up_days` 三分位的答案 + 逐条窗口 + 诚实性提示。参数全部来自界面控件。"""
    asked_cohort, asked_window, asked_jump = cohort, window, jump_pct
    cohort = cohort if cohort in COHORTS else 'anyfirst'
    window = window if window in WINDOWS else 30
    months = max(1, min(12, int(months or 12)))
    limit = max(1, min(600, int(limit or 200)))
    jump_pct = JUMP_PCT if jump_pct is None else float(jump_pct)
    jump_pct = jump_pct if JUMP_MIN <= jump_pct <= JUMP_MAX else JUMP_PCT

    latest = engine.scalar(f"select max(launch_date)::text from {TABLE} where spec_version = %s",
                           (SPEC_VERSION,), role='reader')
    if not latest:
        return {'available': False,
                'reason': '库里还没有爆量/急拉联合计数的行（`python -m app.compute.startup_spike_days '
                          '--months 12` 跑一次才有）'}
    d_to = dt.date.fromisoformat(latest)
    d_from = d_to - dt.timedelta(days=months * 31)

    # ① 这一档窗口到底算过没有（换窗重灌覆盖同一批行，库里可能只有某一种 window_days）
    have = engine.scalar(f"select count(*) from {TABLE} where spec_version = %s and window_days = %s",
                         (SPEC_VERSION, window), role='reader') or 0
    if not have:
        return {'available': False, 'reason': _CAVEATS['no_batch'].format(w=window, m=months)}

    where, params = _conds(cohort, window, d_from)

    # ② 联合计数那一块：倍量/准倍量/缩量日各里「涨几天、跌几天」——这才回答「落在哪一边」
    joint_raw = engine.query_one(
        f"select sum(dbl_days) as dbl_total, sum(dbl_up_days) as dbl_up, sum(dbl_down_days) as dbl_dn, "
        f"       sum(near_dbl_days) as near_total, sum(near_up_days) as near_up, "
        f"       sum(near_down_days) as near_dn, "
        f"       sum(calm_days) as calm_total, sum(calm_up_days) as calm_up, "
        f"       sum(calm_down_days) as calm_dn "
        f"  from {TABLE} where {where}", params, role='reader') or {}
    joint = {'dbl_total_days': _int(joint_raw.get('dbl_total')),
             'dbl_up_share': _pct(_int(joint_raw.get('dbl_up')), _int(joint_raw.get('dbl_total'))),
             'dbl_down_share': _pct(_int(joint_raw.get('dbl_dn')), _int(joint_raw.get('dbl_total'))),
             'near_up_share': _pct(_int(joint_raw.get('near_up')), _int(joint_raw.get('near_total'))),
             'near_down_share': _pct(_int(joint_raw.get('near_dn')), _int(joint_raw.get('near_total'))),
             'calm_up_share': _pct(_int(joint_raw.get('calm_up')), _int(joint_raw.get('calm_total'))),
             'calm_down_share': _pct(_int(joint_raw.get('calm_dn')), _int(joint_raw.get('calm_total')))}

    # ③ 急拉那一块：只数满窗的行（jump 族在不满窗是 NULL）；「真没有」与「窗口不满」分开数
    jumps_raw = engine.query_one(
        f"select percentile_cont(0.5) within group (order by jump_days) as jd_med, "
        f"       percentile_cont(0.5) within group (order by first_jump_gap) as gap_med, "
        f"       sum(jump_days) as jd_sum, sum(jump_dbl_days) as on_dbl, sum(jump_near_days) as on_near, "
        f"       sum(jump_calm_days) as on_calm, sum(jump_limit_days) as on_limit, "
        f"       count(*) filter (where jump_days = 0) as zero_n, "
        f"       count(*) filter (where jump_days is null) as null_n "
        f"  from {TABLE} where {where}", params, role='reader') or {}
    jd_sum = _int(jumps_raw.get('jd_sum'))
    jumps = {'jump_days_med': _num(jumps_raw.get('jd_med')),
             'jump_on_dbl_share': _pct(_int(jumps_raw.get('on_dbl')), jd_sum),
             'jump_on_near_share': _pct(_int(jumps_raw.get('on_near')), jd_sum),
             'jump_on_calm_share': _pct(_int(jumps_raw.get('on_calm')), jd_sum),
             'jump_limit_share': _pct(_int(jumps_raw.get('on_limit')), jd_sum),
             'first_jump_gap_med': _num(jumps_raw.get('gap_med')),
             'jump_days_zero_events': _int(jumps_raw.get('zero_n')),
             'jump_days_null_events': _int(jumps_raw.get('null_n'))}

    # ④ 答案那一半：按 dbl_up_days 现算三分位（左开右闭、去重、取值少时按值切）
    q = engine.query_one(
        f"select count(dbl_up_days) as notnull, count(distinct dbl_up_days) as uniq, "
        f"       percentile_cont(1.0 / {BUCKET_N}) within group (order by dbl_up_days) as c0, "
        f"       percentile_cont(2.0 / {BUCKET_N}) within group (order by dbl_up_days) as c1 "
        f"  from {TABLE} where {where}", params, role='reader') or {}
    uniq = int(q.get('uniq') or 0)
    if uniq <= BUCKET_N:
        vals = engine.query(f"select distinct dbl_up_days as v from {TABLE} "
                            f"where {where} and dbl_up_days is not null order by 1", params,
                            role='reader') or []
        cuts = [int(v['v']) for v in vals][:-1]
    else:
        cuts = sorted({c for c in (_num(q.get(f'c{i}')) for i in range(BUCKET_N - 1)) if c is not None})
    where_t, params_t = _conds(cohort, window, d_from, 't')
    qparams = dict(params_t)
    for i, c in enumerate(cuts):
        qparams[f'cut{i}'] = c
    case = ('case ' + ' '.join(f'when t.dbl_up_days <= %(cut{i})s then {i}' for i in range(len(cuts)))
            + f' else {len(cuts)} end') if cuts else '0'
    bucket_where = where_t + ' and t.dbl_up_days is not null'

    inner = (f"select {case} as g, t.dbl_up_days, t.dbl_down_days, t.jump_days, t.jump_dbl_days, "
             f"       t.outcome_len, t.outcome_r5, t.outcome_ex5 "
             f"  from {TABLE} t where {bucket_where}")
    agg_rows = engine.query(
        f"select g, count(*) as n, "
        f"       percentile_cont(0.5) within group (order by dbl_up_days) as dup_med, "
        f"       percentile_cont(0.5) within group (order by dbl_down_days) as ddown_med, "
        f"       percentile_cont(0.5) within group (order by jump_days) as jd_med, "
        f"       sum(jump_dbl_days) as on_dbl, sum(jump_days) as jd_sum, "
        f"       count(*) filter (where outcome_len >= 4) as s4, count(outcome_len) as outcome_n, "
        f"       percentile_cont(0.5) within group (order by outcome_r5) as r5_med, "
        f"       percentile_cont(0.5) within group (order by outcome_ex5) as ex5_med, "
        f"       count(*) filter (where outcome_r5 > 0) as win_pos, count(outcome_r5) as r5_n, "
        f"       count(*) filter (where outcome_r5 is not null and outcome_ex5 is null) as no_bench "
        f"  from ({inner}) x group by grouping sets ((g), ())", qparams, role='reader') or []

    base_raw = next((r for r in agg_rows if r.get('g') is None), None)
    per_bucket = {int(r['g']): r for r in agg_rows if r.get('g') is not None}
    buckets = [_bucket(g, per_bucket.get(g), cuts) for g in sorted(per_bucket)]
    base = _bucket(None, base_raw, cuts)

    # ⑤ 覆盖度：事件数 / 只数 / 满窗与不满窗各多少 + vr 表起点（只数这一批筛出来的票）
    cov = engine.query_one(
        f"select count(*) as events, count(distinct security_code) as codes, "
        f"       count(*) filter (where bars >= window_days) as full_bars, "
        f"       count(*) filter (where bars < window_days) as short_bars "
        f"  from {TABLE} where {where}", params, role='reader') or {}
    events = int(cov.get('events') or 0)
    vr_earliest = engine.scalar(
        "select min(v.trade_date)::text from analytics.volume_ratio_daily v "
        "join (select distinct security_code from " + TABLE + " where " + where +
        ") s on s.security_code = v.security_code where v.spec_version = 'vr-1'",
        params, role='reader')

    # ⑥ 逐条窗口（一屏最多 600 行，截断要出声）
    fetched = engine.query(
        f"select security_code as code, name, board, launch_date::text as launch_date, cohort, "
        f"       bars, up_days, down_days, flat_days, still_days, dbl_days, near_dbl_days, calm_days, "
        f"       dbl_up_days, dbl_down_days, near_up_days, near_down_days, calm_up_days, calm_down_days, "
        f"       jump_days, jump_dbl_days, jump_near_days, jump_calm_days, jump_limit_days, limit_days, "
        f"       first_dbl_gap, first_jump_gap, outcome_len, outcome_r5, outcome_ex5, "
        f"       count(*) over () as total "
        f"  from {TABLE} where {where} "
        f" order by launch_date desc, dbl_days desc nulls last, dbl_up_days desc nulls last, security_code "
        f" limit %(limit)s", {**params, 'limit': limit}, role='reader') or []
    total = int(fetched[0]['total']) if fetched else 0
    int_cols = ('bars', 'up_days', 'down_days', 'flat_days', 'still_days', 'dbl_days', 'near_dbl_days',
                'calm_days', 'dbl_up_days', 'dbl_down_days', 'near_up_days', 'near_down_days',
                'calm_up_days', 'calm_down_days', 'jump_days', 'jump_dbl_days', 'jump_near_days',
                'jump_calm_days', 'jump_limit_days', 'limit_days', 'first_dbl_gap', 'first_jump_gap',
                'outcome_len')
    rows = [{k: (r[k] if k in ('code', 'name', 'board', 'launch_date', 'cohort')
                 else _int(r[k]) if k in int_cols else _num(r[k])) for k in
             ('code', 'name', 'board', 'launch_date', 'cohort', *int_cols, 'outcome_r5', 'outcome_ex5')}
            for r in fetched]

    caveats = [_CAVEATS['proxy']]
    if asked_cohort != cohort:
        caveats.append(_CAVEATS['fell_back'].format(what='母体档', asked=asked_cohort, now=cohort))
    if asked_window != window:
        caveats.append(_CAVEATS['fell_back'].format(what='窗口', asked=asked_window, now=window))
    if asked_jump is not None and _num(asked_jump) != jump_pct:
        caveats.append(_CAVEATS['fell_back'].format(what='急拉阈值', asked=asked_jump, now=JUMP_PCT))
    if jump_pct != JUMP_PCT:
        caveats.append(_CAVEATS['jump_pct'].format(asked=jump_pct, now=JUMP_PCT))
    if not events:
        caveats.append(_CAVEATS['no_events'].format(c=COHORTS[cohort], m=months, w=window))
    if int(cov.get('short_bars') or 0):
        caveats.append(_CAVEATS['short_window'].format(w=window, n=int(cov['short_bars']),
                                                       d=vr_earliest or '—'))
    caveats.append(_CAVEATS['jump_null'].format(z=jumps['jump_days_zero_events'] or 0,
                                                n=jumps['jump_days_null_events'] or 0))
    no_outcome = int(events - int((base_raw or {}).get('outcome_n') or 0))
    if cohort != 'anyfirst' and no_outcome > 0:
        caveats.append(_CAVEATS['no_outcome'].format(n=no_outcome))
    if base['seg4_rate'] == 100.0 and base['n']:
        caveats.append(_CAVEATS['no_power'].format())
    if int((base_raw or {}).get('no_bench') or 0):
        caveats.append(_CAVEATS['no_bench'].format(n=int(base_raw['no_bench'])))
    thinnest = min((b['n'] for b in buckets), default=0)
    if 0 < thinnest < 20:
        caveats.append(_CAVEATS['thin'].format(n=thinnest))
    if len(buckets) <= 1:
        caveats.append(_CAVEATS['one_bucket'].format(n=uniq))
    if total > len(rows):
        caveats.append(_CAVEATS['limit_cut'].format(n=limit))

    return {'available': True, 'spec_version': SPEC_VERSION,
            'params': {'cohort': cohort, 'cohort_text': COHORTS[cohort], 'months': months,
                       'window': window, 'jump_pct': jump_pct, 'limit': limit},
            'coverage': {'from': str(d_from), 'to': str(d_to), 'events': events,
                         'codes': int(cov.get('codes') or 0), 'vr_earliest': vr_earliest,
                         'window_full_events': int(cov.get('full_bars') or 0),
                         'window_short_events': int(cov.get('short_bars') or 0)},
            'joint': joint, 'jumps': jumps, 'buckets': buckets, 'base': base, 'rows': rows,
            'caveats': caveats, 'caliber': CALIBER, 'definitions': DEFINITIONS, 'options': options()}


def _bucket(g: Optional[int], row: Optional[dict], cuts: List[int]) -> Dict[str, Any]:
    """一档（或全体基线）的读数。区间左开右闭 ⇒ 第一档不设下限、最后一档不设上限。

    ⚠️ 下限必须写 `cuts[g - 1] if g >= 1`，不能写 `if g - 1 < len(cuts)`：第一档的 -1 会绕回
    最后一个切点，屏幕上就念出「2～1（不含 2）」那种倒着的区间（fb-2 首发实测踩过）。
    """
    row = row or {}
    out = {'label': '全部母体事件' if g is None else f'Q{g + 1}',
           'n': int(row.get('n') or 0),
           'dbl_up_days_med': _num(row.get('dup_med')),
           'dbl_down_days_med': _num(row.get('ddown_med')),
           'jump_days_med': _num(row.get('jd_med')),
           'jump_on_dbl_share': _pct(_int(row.get('on_dbl')), _int(row.get('jd_sum'))),
           'seg4_rate': _pct(_int(row.get('s4')), _int(row.get('outcome_n'))),
           'r5_med': _num(row.get('r5_med')), 'ex5_med': _num(row.get('ex5_med')),
           'win5': _pct(_int(row.get('win_pos')), _int(row.get('r5_n')))}
    if g is not None:
        out['range'] = [cuts[g - 1] if g >= 1 else None, cuts[g] if g < len(cuts) else None]
    return out
