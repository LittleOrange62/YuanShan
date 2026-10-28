# -*- coding: utf-8 -*-
"""`/api/quant/first-board-shape` 的读侧：首板母体 + 可调参数的分档聚合（判据一律不在这里）。

这块的**用途**是把"启动前什么形状预示能走到几板 / 预示次日进场能不能赚"变成现场可调参数的读数：
换窗口月数、换板别、换分桶列、换档数、换结果指标、要不要剔掉"段还在走"与"次日一字买不进" ——
改的全是**聚合口径**，任何一列的算法都不在这里（那些在 `app/compute/first_board_shape.py`，fb-2）。

三条读侧纪律（都有对应用例）：
  · 母体是**首板日**（当天就能构造），所以这里的"预示"没有未来函数。`seg_complete=false` 的行段长是**下界**，
    默认 `complete_only=true` 把它们剔掉；关掉时屏幕必须念出"有 N 条还在走的段被算进了分母"。
  · 分桶切点**按当前筛选出来的那一批现算**（等分位；取值少时按值切），所以换窗口换板别之后"Q1 是哪一段"会变；
    每一档都交出实际区间与这一档观测到的 `lo/hi`，页面必须把区间显示出来，不许让人以为 Q1 是固定门槛。
  · 缺就是 None：`ex*` 在北交所没有对应指数时为 NULL，聚合把它们剔出中位并交回剔掉的条数，
    **不拿 0 当"没赚"**（0 是平盘，一条相反的结论）。

聚合全部下推到 SQL（`grouping sets ((g),(g,y),())` 一次拿回"每档汇总 + 每档逐年 + 全体基线"）：
5.8 万行拉到 python 里分档，第一发实测 7.3 秒 —— 而这是一屏**每改一个参数就要重算**的界面，慢一发等于不能用。
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict, List, Optional

from app.db import engine

TABLE = 'analytics.first_board_shape'
SPEC_VERSION = 'fb-2'                       # 与 app/compute/first_board_shape.py 同一个数，测试钉两边

#: 可分桶的列：白名单而不是拼字符串 —— 列名没法参数化，白名单是唯一既不出错又不引注入的写法
BUCKET_COLS: Dict[str, str] = {
    'dbl_days': '启动前倍量天数（倍数 ≥2.0）',
    'near_dbl_days': '启动前准倍量天数（1.6 ≤ 倍数 < 2.0）',
    'calm_days': '启动前缩量天数（倍数 < 0.6）',
    'last10_quiet': '启动前最后 10 根里的缩量天数',
    'pre5': '启动前夜放量比（最后 5 根均值 ÷ 前 25 根均值）',
    'r_med': '启动前倍数中位',
    'r_p80': '启动前倍数 80 分位',
    'r_p90': '启动前倍数 90 分位',
    'r_max': '启动前最大单日倍数',
    'r_std': '启动前倍数标准差（抖不抖）',
    'launch_turnover': '首板日换手率',
    'limit_up_real': '首板日全市场涨停家数',
    'breadth_score': '首板日大盘宽度分值',
    'theme_score': '首板日题材投机分值',
    'rev_yoy': '当时已披露的营收同比',
    'np_yoy': '当时已披露的净利同比',
    'gross_margin': '当时已披露的毛利率',
    'roe': '当时已披露的 ROE',
}

#: 结果指标（界面那个下拉）：能连板与能赚钱是两件事，必须能分开看
RESULT_COLS = ('r1', 'r3', 'r5', 'r10', 'ex1', 'ex3', 'ex5', 'ex10', 'maxup5', 'low5')
METRIC_LABELS: Dict[str, str] = {
    'r1': '进场后 1 场（绝对）', 'r3': '进场后 3 场（绝对）', 'r5': '进场后 5 场（绝对）',
    'r10': '进场后 10 场（绝对）',
    'ex1': '进场后 1 场（超额）', 'ex3': '进场后 3 场（超额）', 'ex5': '进场后 5 场（超额）',
    'ex10': '进场后 10 场（超额）',
    'maxup5': '5 场内最高（纸面能摸到多高）', 'low5': '5 场内最低（大概吃到多深）',
}
BOARDS = ('主板', '创业板', '科创板', '北交所')


def options() -> Dict[str, Any]:
    """控件清单只在这一份：界面镜像一份白名单，迟早会跟判据那边对不上（`fell_back` 那条 caveat 就是这么抓出来的）。"""
    return {'buckets': [{'value': k, 'label': v} for k, v in BUCKET_COLS.items()],
            'metrics': [{'value': k, 'label': v} for k, v in METRIC_LABELS.items()],
            'boards': [{'value': '', 'label': '全部板别'}] + [{'value': b, 'label': b} for b in BOARDS],
            'months': [{'value': m, 'label': f'近 {m} 个月'} for m in (3, 6, 12, 24, 36, 60)],
            'n_buckets': [{'value': n, 'label': f'分 {n} 档'} for n in (2, 3, 4, 5, 6, 8, 10)]}

CALIBER = (
    '取数口径：母体＝每一个连板段的第一天（当天收在涨停价上、且不是已有段的一部分），段长是「结果」不是筛子；'
    '启动前 30 根倍数读 `analytics.volume_ratio_daily`（vr-1，2021-08-02 起全史）；'
    '进场＝首板次日开盘、后复权；超额基准按号段配对应分类指数（沪主板 000002.SH、深主板 399107.SZ、'
    '创业板 399102.SZ、科创板 000680.SH，与异动提醒同一张表），基准起点＝进场当日该指数的开盘（与「次日开盘进场」'
    '同一个时刻，fb-2），北交所没有对应指数 ⇒ 超额为空、只报绝对收益；'
    '财务取启动日往前 60 天已披露的那一期（同花顺只有报告期，60 天是保守滞后补偿）；'
    '分桶切点按当前筛选出来的那一批现算，所以换了窗口或板别，Q1 指的是另一段'
)

DEFINITIONS: Dict[str, str] = {
    'bucket': '分桶用的那一列（启动前形状 / 首板日读数 / 大盘环境 / 财务）。切点按当前筛选结果现算，换窗换板别就换区间',
    'range': '这一档的切点区间，左开右闭；`—` 表示这一头不设限。旁边那两个数是这一档实际观测到的最小/最大值',
    'seg2_rate': '这一档里后来走到 ≥2 板的比例（段还在走的行默认已被剔掉，见「只数走完的」）',
    'seg3_rate': '这一档里后来走到 ≥3 板的比例',
    'seg4_rate': '这一档里后来走到 ≥4 板的比例 —— 这就是「预示能走到几板」那一问的答案',
    'seg5_rate': '这一档里后来走到 ≥5 板的比例',
    'r5_med': '首板次日开盘进场后第 5 场收盘的中位涨幅（后复权，绝对）',
    'ex5_med': '同一场相对对应分类指数的超额，基准起点＝进场当日该指数开盘；北交所无指数 ⇒ 不计入中位并单独报剔掉几条',
    'win5': '所选结果指标为正的比例（超额口径自动剔掉没有基准的那些）',
    'low5_med': '进场后 5 场内最低价相对进场价的中位（这一档最深大概吃到多少）',
    'maxup5_med': '进场后 5 场内最高价相对进场价的中位（纸面能摸到多高）',
    'locked_rate': '这一档里首板次日一字板＝开盘买不进的占比；取消「剔掉买不进」的勾选后这些行才进分母',
    'year_agree': '这一档相对整族的位置，在几个完整年里是不是同向（只数两边样本都≥8 的年份）。'
                  '比「与 0 同号」没用：几乎每档的超额都是负的，那样算出来什么都算稳',
    'samples': '这一档最近 6 条首板事件；双击一行开个股深度复盘',
    'n': '这一档的首板事件条数（不是票数：同一只票不同日子各算一条）',
}

_CAVEATS = {
    'seg_open': '有 {n} 条首板的连板段还在走（末板日之后没出现过非涨停日），段长是下界；'
                '现在按「只数走完的」剔掉了。取消那个勾选它们会进分母，「走到 4 板率」会偏低',
    'no_bench': '北交所这批没有对应分类指数 ⇒ {n} 条的超额不计入中位（只报绝对收益，不拿上证指数冒充）',
    'thin': '最空的一档只有 {n} 条 —— 这么薄的分档只是读数，不足以当规律',
    'locked_out': '已剔掉首板次日一字板（开盘根本买不进）的 {n} 条（占 {pct}%），所以每一档的「买不进」都是 0 —— '
                  '那不是没有一字，是被这个筛子筛掉了；想看它们就取消这个勾选',
    'one_bucket': '这一列在当前筛选里取值太少（只有 {n} 个不同值），分不出多档，只交一整档',
    'fell_back': '传来的{what}「{asked}」不在白名单里，已退回「{now}」—— 屏幕上读的是退回之后的那个口径，'
                 '不是你以为的那一个（悄悄退回去而不说一句，是这类控件最坏的坏法）',
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


def _pct(part: Optional[float], whole: Optional[float]) -> Optional[float]:
    return round(100.0 * part / whole, 1) if (part is not None and whole) else None


def _conds(params: Dict[str, Any], drop: tuple = ()) -> List[str]:
    out = ["spec_version = %(spec)s", "launch_date >= %(d_from)s::date", "bars >= %(min_bars)s"]
    if params.get('board') and 'board' not in drop:
        out.append("board = %(board)s")
    if params.get('complete_only') and 'complete' not in drop:
        out.append("seg_complete")
    if params.get('exclude_locked') and 'locked' not in drop:
        out.append("not entry_locked")
    return out


def _merge(params: Dict[str, Any], drop: tuple = ()) -> Dict[str, Any]:
    """`drop` 掉的那个开关对应的参数若不再被引用，psycopg 允许多给，不必裁剪。"""
    return dict(params)


def board(months: int = 60, *, bucket: str = 'dbl_days', n_buckets: int = 4, metric: str = 'ex5',
          board_filter: str = '', complete_only: bool = True, exclude_locked: bool = True,
          min_bars: int = 20) -> Dict[str, Any]:
    """一张分档表 + 全体基线 + 每档逐年拆分 + 每档最近样本 + 诚实性提示。参数全部来自界面控件。"""
    asked_bucket = bucket
    bucket = bucket if bucket in BUCKET_COLS else 'dbl_days'
    asked_metric = metric
    metric = metric if metric in RESULT_COLS else 'ex5'
    n_buckets = max(2, min(10, int(n_buckets or 4)))
    months = max(1, min(60, int(months or 60)))
    latest = engine.scalar(f"select max(launch_date)::text from {TABLE} where spec_version = %s",
                           (SPEC_VERSION,), role='reader')
    if not latest:
        return {'available': False,
                'reason': '库里还没有首板形状的行（`python -m app.compute.first_board_shape '
                          '--start 2021-11-01 --end 2026-10-16` 跑一次才有）'}
    d_to = dt.date.fromisoformat(latest)
    d_from = d_to - dt.timedelta(days=months * 31)
    params: Dict[str, Any] = {'spec': SPEC_VERSION, 'd_from': str(d_from), 'min_bars': min_bars,
                              'board': board_filter if board_filter in BOARDS else None,
                              'complete_only': complete_only, 'exclude_locked': exclude_locked}
    where = ' and '.join(_conds(params))

    # ① 切点：按当前筛选后的那一批现算（等分位；取值少时按值切，别造空档）
    q = engine.query_one(
        f"select count(*) as notnull, count(distinct {bucket}) as uniq, "
        + ', '.join(f"percentile_cont({i * 1.0 / n_buckets}) within group (order by {bucket}) as c{i}"
                    for i in range(1, n_buckets))
        + f" from {TABLE} where {where} and {bucket} is not null", params, role='reader') or {}
    uniq = int(q.get('uniq') or 0)
    n_valid = int(q.get('notnull') or 0)
    if not n_valid:
        return _empty_by_bucket(bucket, months, n_buckets, metric, board_filter,
                                complete_only, exclude_locked, min_bars, d_from, d_to, latest)
    if uniq <= n_buckets:
        vals = engine.query(f"select distinct {bucket} as v from {TABLE} "
                            f"where {where} and {bucket} is not null order by 1", params, role='reader') or []
        cuts = [float(v['v']) for v in vals][:-1]
    else:
        # 四分位常落在同一个整数上，必须去重：不去重会造出一档是空的，屏幕上就出现缺号的读数
        cuts = sorted({c for c in (_num(q.get(f'c{i}')) for i in range(1, n_buckets)) if c is not None})
    qparams = dict(params)
    for i, c in enumerate(cuts):
        qparams[f'cut{i}'] = c
    case = ('case ' + ' '.join(f'when {bucket} <= %(cut{i})s then {i}' for i in range(len(cuts)))
            + f' else {len(cuts)} end') if cuts else '0'

    # ② 一次 grouping sets 拿回每档汇总 + 每档逐年 + 全体基线
    aggs = ['count(*) as n',
            'count(*) filter (where seg_len >= 2) as s2',
            'count(*) filter (where seg_len >= 3) as s3',
            'count(*) filter (where seg_len >= 4) as s4',
            'count(*) filter (where seg_len >= 5) as s5',
            'count(*) filter (where entry_locked) as locked',
            'count(*) filter (where bench_code is null) as no_bench',
            'min(bv) as lo_obs', 'max(bv) as hi_obs',
            f'count(*) filter (where {metric} > 0) as win_pos',
            f'count({metric}) as win_n']
    aggs += [f'percentile_cont(0.5) within group (order by {c}) as med_{c}' for c in RESULT_COLS]
    inner = (f"select {case} as g, {bucket} as bv, seg_len, entry_locked, bench_code, "
             f"extract(year from launch_date)::int as y, "
             f"{', '.join(RESULT_COLS)} from {TABLE} where {where}")
    agg_rows = engine.query(
        f"select g, y, {', '.join(aggs)} from ({inner}) x "
        f"group by grouping sets ((g), (g, y), (y), ())", qparams, role='reader') or []

    base_raw = next((r for r in agg_rows if r.get('g') is None and r.get('y') is None), None)
    per_bucket: Dict[int, Dict[str, Any]] = {}
    per_year: Dict[tuple, Dict[str, Any]] = {}
    base_year: Dict[int, Dict[str, Any]] = {}
    for r in agg_rows:
        if r.get('g') is None and r.get('y') is None:
            continue
        if r.get('g') is None:
            base_year[int(r['y'])] = r
            continue
        g = int(r['g'])
        if r.get('y') is None:
            per_bucket[g] = r
        else:
            per_year[(g, int(r['y']))] = r
    years = sorted(base_year)

    def shape(g: Optional[int], row: Optional[dict]) -> Dict[str, Any]:
        row = row or {}
        n = int(row.get('n') or 0)
        out = {'label': '全部首板' if g is None else f'Q{g + 1}',
               'n': n,
               'seg2_rate': _pct(row.get('s2'), n), 'seg3_rate': _pct(row.get('s3'), n),
               'seg4_rate': _pct(row.get('s4'), n), 'seg5_rate': _pct(row.get('s5'), n),
               'locked_rate': _pct(row.get('locked'), n), 'no_bench': int(row.get('no_bench') or 0),
               'win5': _pct(row.get('win_pos'), row.get('win_n')),
               'lo_obs': _num(row.get('lo_obs')), 'hi_obs': _num(row.get('hi_obs'))}
        for c in RESULT_COLS:
            out[f'{c}_med'] = _num(row.get(f'med_{c}'))
        if g is not None:
            # g 是 0 基的档号（case 里 `<= cut_i then i`）⇒ 这一档的区间是 (cuts[g-1], cuts[g]]。
            # 写成 `cuts[g - 1] if g - 1 < len(cuts)` 时，第一档的 -1 会绕回最后一个切点，
            # 屏幕上就念出「2～1（不含 2）」这种倒着的区间（10-17 首发实测 Q1 正是这条）。
            out['range'] = [cuts[g - 1] if g >= 1 else None,
                            cuts[g] if g < len(cuts) else None]
            out['per_year'] = [{'year': str(y), 'n': int(per_year[(g, y)]['n'] or 0),
                                'seg4_rate': _pct(per_year[(g, y)]['s4'], per_year[(g, y)]['n']),
                                **{f'{c}_med': _num(per_year[(g, y)].get(f'med_{c}')) for c in RESULT_COLS}}
                               for y in years if (g, y) in per_year]
        return out

    base = shape(None, base_raw)
    base['per_year'] = [{'year': str(y), 'n': int(base_year[y]['n'] or 0),
                         'seg4_rate': _pct(base_year[y]['s4'], base_year[y]['n']),
                         **{f'{c}_med': _num(base_year[y].get(f'med_{c}')) for c in RESULT_COLS}}
                        for y in years]
    buckets = [shape(g, per_bucket.get(g)) for g in sorted(per_bucket)]

    # ③ 每档最近 6 条（单独一条查询：窗口函数在这张表上很便宜，比塞进上面那条汇总更容易读）
    srows = engine.query(
        f"select g, launch_date, security_code, name, board, seg_len, seg_complete, bv, "
        f"       r5, ex5, entry_locked "
        f"  from (select {case} as g, launch_date::text as launch_date, security_code, name, board, "
        f"               seg_len, seg_complete, {bucket} as bv, r5, ex5, entry_locked, "
        f"               row_number() over (partition by {case} order by launch_date desc) as rn "
        f"          from {TABLE} where {where}) x "
        f" where rn <= 6 order by g, launch_date desc", qparams, role='reader') or []
    samples: Dict[int, List[dict]] = {}
    for r in srows:
        samples.setdefault(int(r['g']), []).append(
            {'launch_date': r['launch_date'], 'code': r['security_code'], 'name': r['name'],
             'board': r['board'], 'seg_len': int(r['seg_len']), 'seg_complete': bool(r['seg_complete']),
             'value': _num(r['bv']), 'r5': _num(r['r5']), 'ex5': _num(r['ex5']),
             'locked': bool(r['entry_locked'])})
    for b in buckets:
        g = int(b['label'][1:]) - 1
        b['samples'] = samples.get(g, [])
        b['year_agree'] = _agree(b, base, 'seg4_rate')
        b['metric_year_agree'] = _agree(b, base, f'{metric}_med')
    base['samples'] = []

    caveats = []
    if asked_bucket != bucket:
        caveats.append(_CAVEATS['fell_back'].format(what='分桶列', asked=asked_bucket, now=bucket))
    if asked_metric != metric:
        caveats.append(_CAVEATS['fell_back'].format(what='结果指标', asked=asked_metric, now=metric))
    if params.get('board') != (board_filter or None) and board_filter:
        caveats.append(_CAVEATS['fell_back'].format(what='板别', asked=board_filter, now='全部'))
    if complete_only:
        open_segs = engine.scalar(
            f"select count(*) from {TABLE} where {' and '.join(_conds(params, drop=('complete',)))} "
            f"and not seg_complete", _merge(params, ('complete',)), role='reader') or 0
        if open_segs:
            caveats.append(_CAVEATS['seg_open'].format(n=open_segs))
    if exclude_locked:
        locked_out = engine.scalar(
            f"select count(*) from {TABLE} where {' and '.join(_conds(params, drop=('locked',)))} "
            f"and entry_locked", _merge(params, ('locked',)), role='reader') or 0
        if locked_out:
            caveats.append(_CAVEATS['locked_out'].format(
                n=locked_out, pct=round(100.0 * locked_out / max(1, base['n'] + locked_out), 1)))
    if base['no_bench']:
        caveats.append(_CAVEATS['no_bench'].format(n=base['no_bench']))
    thinnest = min((b['n'] for b in buckets), default=0)
    if 0 < thinnest < 200:
        caveats.append(_CAVEATS['thin'].format(n=thinnest))
    if len(buckets) <= 1:
        caveats.append(_CAVEATS['one_bucket'].format(n=uniq))

    return {'available': True, 'spec_version': SPEC_VERSION,
            'params': {'months': months, 'bucket': bucket, 'bucket_label': BUCKET_COLS[bucket],
                       'n_buckets': n_buckets, 'board': params.get('board') or '全部',
                       'complete_only': complete_only, 'exclude_locked': exclude_locked,
                       'min_bars': min_bars, 'metric': metric},
            'coverage': {'from': str(d_from), 'to': str(d_to), 'events': base['n'],
                         'codes': int(engine.scalar(f"select count(distinct security_code) from {TABLE} "
                                                    f"where {where}", params, role='reader') or 0),
                         'latest_launch': latest, 'bucket_values': uniq},
            'cuts': cuts, 'base': base, 'buckets': buckets,
            'years': [{'year': p['year'], 'n': p['n'], 'seg4_rate': p['seg4_rate'],
                       'seg2_rate': _pct(base_year[int(p['year'])]['s2'], p['n']),
                       f'{metric}_med': p[f'{metric}_med'], 'ex5_med': p['ex5_med']}
                      for p in base['per_year']],
            'caveats': caveats, 'caliber': CALIBER, 'definitions': DEFINITIONS, 'options': options()}


def _agree(bucket: Dict[str, Any], base: Dict[str, Any], key: str) -> Optional[str]:
    """这一档**相对整族**的位置，在几个完整年里同不同向（只数两边样本都≥8 的年份）。

    比"与 0 同号"没用：母体里几乎每档的超额都是负的，那样算出来什么都算"稳"。
    """
    own, ref = bucket.get(key), base.get(key)
    if own is None or ref is None:
        return None
    up = own >= ref
    agree = tot = 0
    by_year = {p['year']: p for p in base.get('per_year', [])}
    for p in bucket.get('per_year', []):
        bp = by_year.get(p['year'])
        if not bp or p['n'] < 8 or bp['n'] < 8 or p.get(key) is None or bp.get(key) is None:
            continue
        tot += 1
        agree += 1 if (p[key] >= bp[key]) == up else 0
    return f'{agree}/{tot}' if tot else None


def _empty_by_bucket(bucket, months, n_buckets, metric, board_filter, complete_only,
                     exclude_locked, min_bars, d_from, d_to, latest) -> Dict[str, Any]:
    return {'available': False,
            'reason': f'这一列（{BUCKET_COLS.get(bucket, bucket)}）在当前筛选里一条有值的都没有 —— '
                      f'是这一列本机没有历史，还是筛选组合本来就是空的？换个分桶列或放宽窗口试试',
            'params': {'months': months, 'bucket': bucket, 'metric': metric, 'board': board_filter or '全部',
                       'complete_only': complete_only, 'exclude_locked': exclude_locked, 'min_bars': min_bars},
            'coverage': {'from': str(d_from), 'to': str(d_to), 'events': 0, 'latest_launch': latest},
            'caliber': CALIBER, 'definitions': DEFINITIONS, 'caveats': [], 'options': options()}
