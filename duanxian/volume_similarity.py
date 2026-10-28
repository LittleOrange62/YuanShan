# -*- coding: utf-8 -*-
"""「这只票最近 30 根的量价形状」＋「历史上最相近的 N 个样本」（一处实现，页面只念）。

用户 2026-10-16：「把这个股近 30 根有效日线里的量价关系，按照（启动前逐日量能比对）这个表的表头写一份，
并且把那张表里面跟这个个股……最相近的几个都摘录出来显示在一起，个股排第一行，
接下来把最相近的 10 个按照相近程度排序。（相近程度你帮我思考好直接开干）」

三条刻意的选择，改的人请先读完：

1. **同一把尺子，不重新量一遍。** 逐日倍数直接读 `analytics.volume_ratio_daily`（`vr-1`），
   天数分类与分位数调的是 `app/compute/startup_volume.classify()` 那一份，五档区间调的是
   `startup_volume.range_counts()`——与「启动前逐日量能比对」那张表**同一处实现**。
   两边各写一遍迟早漂（本项目在涨停价口径上犯过）。唯一区别是窗口方向：
   那张表是「启动日**往前** 30 根」，这里是「**截止今天**往前 30 根」。

2. **样本库有上限，这句话必须上屏。** 能拿来比的只有 `analytics.startup_volume_profile` 里
   `sv-2` 那批（board4 + board4_45 + jump40，实测 1,300 行、两个母体重叠去重后 **1,010 个「票·启动日」窗口**，
   启动日跨度 2025-09-23 ~ 2026-10-14）。也就是说"最相近"说的是
   **「近一年进入过启动母体的那些窗口里最相近」**，不是"全市场历史所有窗口里最相近"。
   母体本身就是"已经涨起来的票"，所以这个相似度回答的是"历史上谁以这种量能形状启动过、
   之后 5/10/20 日走得怎样"，不回答「我这只也像这样了所以会涨」——那是选择偏差。
   同一只票的多个启动窗口只留最像的一个（十个名额＝十只票）。

3. **相近程度是一个可解释的加权分，不是一个黑盒距离。** 权重写死在 `WEIGHTS` 一处：

   | 分量 | 权重 | 比的是什么 | 为什么是它 |
   |---|---|---|---|
   | shape 量能形状 | 0.35 | 12 格占比向量（涨天/跌天/持平/量没变/倍量/准倍量/缩量 + 五个区间）的**余弦** | 形状本身。用占比不用天数，否则窗口根数不同的样本会被数量带偏 |
   | intensity 强度分布 | 0.20 | p60/p80/p90/中位/最大 **各自除以自身均值**后的欧氏距离 | 剔掉"整体量级"，只比"放量集中在哪一段" |
   | rhythm 节奏 | 0.15 | 近 10 根均倍数 ÷ 前 20 根，与 std÷均值 | 同样是放量，"越放越猛"和"开头一根巨量后面缩"是两回事 |
   | price_volume 量价配合 | 0.15 | 窗口内逐日涨跌幅之和（百分点、不复利）与涨天占比 | 放量是配合涨还是配合跌 |
   | mood 大盘情绪 | 0.10 | 两端窗口末日的大盘情绪分差 ÷ 200 | 与那张表同一个 `pg_sources.mood_score()` 出处 |
   | comparability 可比性 | 0.05 | 同板块档 + 同是否 ST | 主板 10% 与双创 20% 的量能形状不可比 |

   每一格都同时交出**分量得分**，页面上要能回答"到底像在哪"。

硬过滤（不计分，直接不入选）：`bars_used ≥ 20`、窗口内没有长停牌复牌跳空（`long_gap_days = 0`）、
板块档与目标一致、排除目标票自身**窗口重叠**的那些启动日（不然一只票能给自己找出九个"相似样本"）。
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.compute import startup_volume as SV
from app.db import engine

from . import quant as Q

SPEC_VERSION = SV.SPEC_VERSION           # 'sv-2'：样本库与判据同版本
VR_SPEC = 'vr-1'
WINDOW = SV.WINDOW_DAYS                  # 30
MIN_BARS = 20                            # 少于这个根数不比较（分布列本身也会是 NULL）
MOOD_WIDEN = 3                           # 情绪补完后要重排，所以先多取 k 的三倍再截断
COHORTS = SV.COHORTS                     # ('board4', 'board4_45', 'jump40')

WEIGHTS: Dict[str, float] = {'shape': 0.35, 'intensity': 0.20, 'rhythm': 0.15,
                              'price_volume': 0.15, 'mood': 0.10, 'comparability': 0.05}

#: 形状向量的格子（与那张表交出的列一一对应，顺序固定 ⇒ 余弦才可比）
SHAPE_KEYS = ('up_days', 'down_days', 'flat_days', 'still_days', 'dbl_days', 'near_dbl_days',
              'calm_days', 'range_16_18', 'range_14_16', 'range_12_14', 'range_10_12', 'range_08_10')
BOARD_GROUP = {'沪主板': '主板', '深主板': '主板', '创业板': '双创', '科创板': '双创'}


def _jsonable(v: Any) -> Any:
    """出口清洗走 `duanxian.quant.jsonable`（一处实现）：Decimal→float、日期→ISO 串、NaN/NaT→None。

    这里留个名字只是想让本文件的调用点读起来短一层；判据与坑都写在那一份里。
    """
    return Q.jsonable(v)


def board_group(code: str, board: Optional[str] = None) -> Optional[str]:
    """板块档：**先读 security.board，为空就按代码号段定档**（与异动提醒那张表同一条规矩）。

    ⚠️ 不能只信 `security.board`：实测有 3,972 行是 NULL。10-16 第一版就栽在这——
    目标票（沪主板）与一批北交所票的 board 都是 NULL，`None != None` 成立 ⇒ 同板块硬过滤形同虚设，
    榜上五个"相似样本"全是 920xxx。认不出来的档一律返回 None，调用方**必须跳过**这一对
    （"不知道像不像"与"不像"是两句话，但都不该进榜）。
    """
    if board and BOARD_GROUP.get(board):
        return BOARD_GROUP[board]
    c = str(code or '')
    if c.startswith('68'):
        return '双创'
    if c.startswith('30'):
        return '双创'
    if c.startswith(('60', '00')):
        return '主板'
    if c.startswith(('83', '87', '88', '92', '43')) or c.endswith('.BJ'):
        return '北交所'
    return None

#: 屏幕悬停与字段义只在这一个地方写（页面不许自己编一句列含义）
CALIBER = (
    '逐日倍数读 `analytics.volume_ratio_daily`（`vr-1`，当日量 ÷ 上一根有效日线量）；'
    '天数分类（增/减/持平/量没变/倍量/准倍量/缩量）与五档区间计数调的是 '
    '`app/compute/startup_volume.classify()` 与 `range_counts()` 那一份实现 —— '
    '与「启动前逐日量能比对」那张表同一把尺子，不重算第二遍。'
    '唯一区别是窗口方向：那张表是「启动日往前 30 根」，这里是「截止今天往前 30 根」。'
    '相近分＝六个分量按权重合成 0~100（形状 35% / 强度 20% / 节奏 15% / 量价配合 15% / 情绪 10% / 可比性 5%），'
    '算不出的那一维从权重里剔掉再归一，所以缺维不是扣分。'
)

DEFINITIONS: Dict[str, str] = {
    'window_from': '这段窗口的第一根有效日线日期。中间停牌的日子不占格，所以窗口跨的自然日可能多于 30 天。',
    'window_to': '这段窗口的最后一场。对本票那一行就是"今天为止"，对历史样本那一行是它的启动日前一天。',
    'bars_used': '窗口里真正有几根有效日线（少于 20 根的不进榜）。次新股与长期停牌的票常常不满 30 根。',
    'up_days': '当天量比上一根大（倍数 > 1.05）的根数。',
    'down_days': '当天量比上一根小（倍数 < 0.95）的根数。',
    'flat_days': '倍数精确等于 1.0000 的根数 —— 很少见，别把它当"量没变"，那要看「量没变」那一列。',
    'still_days': '量基本没变（0.95 ≤ 倍数 ≤ 1.05）的根数。',
    'dbl_days': '倍量（倍数 ≥ 2.0）的根数。',
    'near_dbl_days': '准倍量（1.6 ~ 2.0 之间）的根数。',
    'calm_days': '缩量（倍数 ≤ 0.8）的根数。',
    'long_gap_days': '窗口里"上一根隔了很久"的天数（复牌跳空）。有长跳空的窗口不进榜：那天的分母是很久以前的量，倍数天然偏大。',
    'range_16_18': '倍数落在 1.6~1.8（下闭上开）的根数。',
    'range_14_16': '倍数落在 1.4~1.6 的根数。',
    'range_12_14': '倍数落在 1.2~1.4 的根数。',
    'range_10_12': '倍数落在 1.0~1.2 的根数。',
    'range_08_10': '倍数落在 0.8~1.0 的根数。',
    'avg': '这 30 根倍数的算术均值（本票那一行读 `target.features.avg`）。',
    'median': '中位数：与均值差得远说明放量集中在少数几天。',
    'p60': '60 分位倍数。', 'p80': '80 分位倍数。', 'p90': '90 分位倍数（放量最猛的那几天是什么水平）。',
    'max': '窗口内最大倍数（一根巨量与每天温和放量，均值可能一样，这一列不一样）。',
    'min': '窗口内最小倍数。', 'std': '倍数的标准差（波动）。',
    'gain_points': '窗口内逐日涨跌幅之和（百分点、不复利）。不复利是为了与样本库同一个定义，'
                   '跨很长或涨跌很烈的窗口它与真实累计涨幅会有出入。',
    'mood': '窗口末日的全市场情绪读数（−100~100），合成口径只住 `pg_sources.mood_score()` 一处，'
            '与那张表同一出处。它是横切面同一个值，只用来比"那天大盘什么温度"。',
    'score': '相近分 0~100，六个分量按权重合成；分高只说明形状像，不说明会涨。',
    'parts': '六个分量各自的分（满分 100）。没算出来的那一维不在里面，见「缺维」那句。',
    'weights_used': '这一行实际用的权重（缺的那一维给 0，其余按剩下的归一）。',
    'after_5': '那一只样本启动日之后 5 个交易日的累计涨跌幅（百分点之和）。'
               '说的是那个历史样本后来怎么走，不是对本票的预测。',
    'after_10': '启动日之后 10 个交易日的累计涨跌幅（百分点）。',
    'after_20': '启动日之后 20 个交易日的累计涨跌幅（百分点）。本机没读到那么多场就给「—」，不当 0。',
    'cohorts': '那一只当年是靠哪类母体进的那张表：连续 4 板及以上 / 4 板且前 4 日涨幅>45% / 前 4 日涨幅跳空。'
               '同一窗口属于两类母体时并成一条，两个名额不会都给同一只票。',
    'board_group': '板块档（主板 / 双创）。判据在这块只用于"不互比"：主板 10% 与双创 20% 的量能形状不可比。',
    'is_st': '启动日那天的 ST 身份。库里的 ST 期快照从 2026-08-27 起才有，更早的日子多数是「不知道」。',
    'trigger_len': '那一只样本当年的连续涨停数（本票那一行没有这个数 —— 它是一段截止今天的窗口，不是涨停启动日）。',
    'trigger_gain': '那一只样本启动区间的涨幅（小数口径：0.4648 = 46.48%，屏上换算成百分数念）。',
    'gain_first4': '启动日前 4 个交易日累计涨幅（同样是小数口径）。第三类母体 board4_45 就是按这一列筛的。',
    'equiv_limit_ups': '等效涨停数：把 20%/30% 档的涨幅折算成 10% 档的板数，跨板块比强度看这一列。',
    'ratios': '本票这 30 根的逐日倍数（旧→新）。`v: null` 是那天没有效日线，画走势时留缺口，不画成 0。',
}


# ------------------------------------------------------------------ 特征向量（纯函数，单测打在这一层）

def features(counts: Dict[str, Any], security_code: str, avg: Optional[float], median: Optional[float],
             p60: Optional[float], p80: Optional[float], p90: Optional[float],
             mx: Optional[float], std: Optional[float], bars: int,
             gain_points: Optional[float], near_avg: Optional[float], prior_avg: Optional[float],
             mood: Optional[float], board: Optional[str], is_st: bool = False) -> Dict[str, Any]:
    """一摞原始读数 → 一份可比较的特征。缺项一律 None，比较时那一维不计（分母跟着缩）。"""
    n = float(bars) if bars else None
    shape = {k: (None if (counts.get(k) is None or not n) else float(counts[k]) / n)
             for k in SHAPE_KEYS}
    return {
        'bars': bars,
        'shape': shape,
        # 强度：全部除以自身均值 ⇒ 只比"分布形状"，不比整体量级
        'intensity': [None if not avg or v is None else round(float(v) / float(avg), 4)
                      for v in (p60, p80, p90, median, mx)],
        'avg': avg, 'median': median, 'p60': p60, 'p80': p80, 'p90': p90,
        'max': mx, 'min': counts.get('ratio_min'), 'std': std,
        'rhythm': {'late_vs_early': (None if not prior_avg or near_avg is None
                                      else round(float(near_avg) / float(prior_avg), 4)),
                   'volatility': (None if not avg or std is None else round(float(std) / float(avg), 4))},
        'price_volume': {'gain_points': None if gain_points is None else round(float(gain_points), 2),
                         'up_share': None if not n else round(float(counts.get('up_days') or 0) / n, 4)},
        'mood': mood,
        'board_group': board_group(security_code, board),
        'is_st': bool(is_st),
    }


def _cosine(a: Dict[str, Optional[float]], b: Dict[str, Optional[float]]) -> Optional[float]:
    """只在**两边都有数**的格子上算余弦；两边同时有数的格子不超过一半 ⇒ None（不假装像）。

    为什么卡一半：12 格里只剩 6 格对得上时，余弦读起来与"12 格全对上"一模一样，
    屏幕上那一列 shape 分会凭空把人说服。判不了就说判不了。
    """
    ka = [k for k in a if a[k] is not None and b.get(k) is not None]
    if len(ka) <= len(a) // 2:
        return None
    va = [float(a[k]) for k in ka]
    vb = [float(b[k]) for k in ka]
    na = math.sqrt(sum(x * x for x in va))
    nb = math.sqrt(sum(x * x for x in vb))
    if not na or not nb:
        return None
    return max(0.0, min(1.0, sum(x * y for x, y in zip(va, vb)) / (na * nb)))


def _euclid(u: Sequence[Optional[float]], v: Sequence[Optional[float]]) -> Optional[float]:
    """同样只在两边都有数的坐标上算；对得上的坐标不超过一半 ⇒ None（与 `_cosine` 一条规矩）。"""
    pairs = [(float(a), float(b)) for a, b in zip(u, v) if a is not None and b is not None]
    if len(pairs) <= len(u) // 2:
        return None
    return round(math.sqrt(sum((a - b) ** 2 for a, b in pairs)), 4)


def score_pair(target: Dict[str, Any], cand: Dict[str, Any]) -> Dict[str, Any]:
    """两条特征 → 0~100 的相近分 + 每个分量各自的分（缺的那一维**从权重里剔掉再归一**）。"""
    # 六个分量**全都建键**（算不出的那一维留 None）：页面按同一组列画分量，
    # 少一个键就少一列，读者看不出"这一行是没算"还是"算了是 0 分"。
    parts: Dict[str, Optional[float]] = {k: None for k in WEIGHTS}
    wsum = 0.0
    acc = 0.0

    c = _cosine(target['shape'], cand['shape'])
    if c is not None:
        parts['shape'] = round(c * 100.0, 1)

    d = _euclid(target.get('intensity') or [], cand.get('intensity') or [])
    if d is not None:
        parts['intensity'] = round(max(0.0, 1.0 - d / 2.0) * 100.0, 1)   # 距离 2 个单位以上算完全不像

    rt, rc = target['rhythm'], cand['rhythm']
    if rt['late_vs_early'] is not None and rc['late_vs_early'] is not None \
            and rt['volatility'] is not None and rc['volatility'] is not None:
        dd = abs(math.log(max(0.05, rt['late_vs_early'] / rc['late_vs_early']))) \
            + abs(rt['volatility'] - rc['volatility'])
        parts['rhythm'] = round(max(0.0, 1.0 - dd / 1.5) * 100.0, 1)

    pt, pc = target['price_volume'], cand['price_volume']
    if pt['gain_points'] is not None and pc['gain_points'] is not None:
        same_dir = (pt['gain_points'] >= 0) == (pc['gain_points'] >= 0)
        dd = abs(pt['gain_points'] - pc['gain_points']) / 100.0
        share = abs((pt['up_share'] or 0) - (pc['up_share'] or 0)) if pt['up_share'] is not None \
            and pc['up_share'] is not None else 0.5
        parts['price_volume'] = round(max(0.0, 1.0 - dd / 3.0) * (0.5 + 0.5 * (1 - share * 2))
                                      * 100.0 * (1.0 if same_dir else 0.35), 1)

    if target.get('mood') is not None and cand.get('mood') is not None:
        parts['mood'] = round(max(0.0, 1.0 - abs(target['mood'] - cand['mood']) / 200.0) * 100.0, 1)

    if target.get('board_group') and target['board_group'] == cand.get('board_group'):
        parts['comparability'] = 100.0 if bool(target.get('is_st')) == bool(cand.get('is_st')) else 60.0
    else:
        parts['comparability'] = None

    for k, v in parts.items():
        if v is not None:
            acc += WEIGHTS[k] * v
            wsum += WEIGHTS[k]
    total = round(acc / wsum, 1) if wsum else None
    # `weights_used` 交的是**这一对实际上用的权重**：缺的那一维给 0，其余按剩下的归一。
    # 不能照抄 WEIGHTS/wsum 全表 —— 那样屏幕上会显示"情绪占 11%"，而这一对根本没算情绪那一维。
    return {'score': total, 'parts': parts,
            'weights_used': {k: (round(WEIGHTS[k] / wsum, 3) if parts.get(k) is not None else 0.0)
                             for k in WEIGHTS} if wsum else {},
            'missing_parts': [k for k, v in parts.items() if v is None]}


# ------------------------------------------------------------------ 取数

def _window_rows(code: str, as_of: dt.date) -> List[dict]:
    return engine.query(
        f"""select trade_date, vol_ratio, prev_gap_days from analytics.volume_ratio_daily
             where security_code = %s and spec_version = %s and trade_date <= %s
             order by trade_date desc limit %s""",
        (code, VR_SPEC, as_of, WINDOW), role='reader') or []


def _pct_sum(code: str, dates: List[dt.date]) -> Optional[float]:
    """窗口内逐日涨跌幅之和（百分点，不复利）—— 与样本库用**同一个定义**，见模块 docstring。"""
    if not dates:
        return None
    r = engine.query_one(
        "select sum(pct_change) s, count(*) n from market_data.stock_daily "
        "where security_code = %s and trade_date = any(%s)", (code, dates), role='reader')
    if not r or r['s'] is None or int(r['n'] or 0) < max(2, len(dates) // 2):
        return None
    return float(r['s'])


def target_features(code: str, as_of: Optional[dt.date] = None) -> Dict[str, Any]:
    """这只票截止 as_of（默认最新一场）往前 30 根有效日线的量能形状。"""
    if as_of is None:
        r = engine.query_one('select max(trade_date) d from market_data.stock_daily', role='reader')
        as_of = r['d'] if r else None
    if as_of is None:
        return {'available': False, 'reason': '库里没有日线，谈不上窗口'}
    rows = _window_rows(code, as_of)
    if not rows:
        return {'available': False,
                'reason': f'{code} 在 analytics.volume_ratio_daily（{VR_SPEC}）里 {as_of} 之前一根都没有 —— '
                          '是这张倍量表没覆盖到它，不是「这只票没有量价关系」'}
    rows = list(reversed(rows))
    ratios = [r['vol_ratio'] for r in rows]
    gaps = [r['prev_gap_days'] for r in rows]
    cls = SV.classify(ratios, gaps)                     # ★ 与那张表同一处分类
    cls['range'] = SV.range_counts(ratios)              # ★ 与那张表同一处区间计数
    dates = [r['trade_date'] for r in rows]
    first = dates[0].isoformat()
    last = dates[-1].isoformat()
    avg = cls.get('ratio_avg')
    near = ratios[-10:] if len(ratios) >= 10 else ratios
    prior = ratios[:-10] if len(ratios) > 10 else []
    near_avg = (sum(float(x) for x in near if x is not None) / max(1, len(near))) if near else None
    prior_avg = (sum(float(x) for x in prior if x is not None) / max(1, len(prior))) if prior else None
    from duanxian import pg_sources
    mood = (pg_sources.mood_score(pg_sources.sentiment_on_dates([last]).get(last)) or {}).get('mood_score')
    name_row = engine.query_one(
        "select coalesce(nullif(name,''), security_code) n, board from reference_data.security "
        "where security_code = %s", (code,), role='reader')
    st = engine.query_one(
        "select exists(select 1 from reference_data.security_st_period s where s.security_code = %s "
        "and %s >= s.valid_from and (s.valid_to is null or %s <= s.valid_to)) t",
        (code, last, last), role='reader')
    return _jsonable({'available': True, 'code': code, 'window_from': first, 'window_to': last,
            'name': (name_row or {}).get('n'), 'board': (name_row or {}).get('board'),
            'is_st': bool((st or {}).get('t')),
            'counts': {**cls, **{k: v for k, v in cls['range'].items() if k.startswith('range_')}},
            'ratios': [{'d': r['trade_date'].isoformat(), 'v': (None if r['vol_ratio'] is None
                                                                else round(float(r['vol_ratio']), 4))}
                       for r in rows],
            'features': features({**cls, **cls['range']}, code, avg, cls.get('ratio_median'),
                                  cls.get('ratio_p60'), cls.get('ratio_p80'), cls.get('ratio_p90'),
                                  cls.get('ratio_max'), cls.get('std_days'), len(rows),
                                  _pct_sum(code, dates), near_avg, prior_avg,
                                  mood, (name_row or {}).get('board'), bool((st or {}).get('t'))),
            'bars_below_min': len(rows) < MIN_BARS})


LIBRARY_SQL = """
with lib as (
  select p.security_code, p.launch_date, p.cohort, p.board, p.name,
         p.bars_used, p.up_days, p.down_days, p.flat_days, p.still_days,
         p.dbl_days, p.near_dbl_days, p.calm_days, p.long_gap_days,
         p.ratio_avg, p.ratio_median, p.ratio_p60, p.ratio_p80, p.ratio_p90,
         p.ratio_max, p.ratio_min, p.std_days, p.ratios,
         -- 那三块"为什么进母体"的读数（连板数 / 区间涨幅 / 前 4 日涨幅 / 等效涨停）：
         -- 页面要照「启动前逐日量能比对」那张表的表头摆，缺这四列那张表就少一截，
         -- 而这些数本来就是 profile 表存的原始读数 —— 这里只搬运，不重算。
         p.trigger_len, p.trigger_gain, p.gain_first4, p.equiv_limit_ups,
         -- ⚠️ 这里必须用 exists，不能 left join：一只票可以有多段 ST 期（实测 145 只），
         -- left join 会把同一个「票·启动日」叉成两行 ⇒ 289 对重复（10-16 实测），
         -- 榜上就会出现「同一个样本连着出现两次」。
         exists(select 1 from reference_data.security_st_period s
                 where s.security_code = p.security_code
                   and p.launch_date >= s.valid_from
                   and (s.valid_to is null or s.valid_to > p.launch_date)) as is_st
    from analytics.startup_volume_profile p
   where p.spec_version = %(spec)s
)
select * from lib
"""

#: 窗口逐日涨跌幅**另开一发**：把样本库涉及的票一次取回，在 Python 侧按窗口对齐相加。
#: 为什么不在 SQL 里 join： unnest 出 36,266 个「票·日」再 join `stock_daily`（643 万行）实测 14.6s，
#: 强走嵌套循环更慢（2 分 19 秒）；而按票号数组 + 日期区间一次取回是 1.8s（10-16 实测）。
PCT_SQL = """select security_code, trade_date, pct_change from market_data.stock_daily
              where security_code = any(%(codes)s) and trade_date between %(lo)s and %(hi)s"""

LIB_CACHE_TTL = 900          # 秒：样本库只在 startup_volume 重跑时变，没必要每屏重取 3 秒
_lib_cache: Dict[str, Any] = {'key': None, 'rows': None, 'ts': 0.0}


def _window_pct_map(codes: List[str], lo: str, hi: str) -> Dict[Tuple[str, str], Optional[float]]:
    """(票号, 'YYYY-MM-DD') → 当日涨跌幅。读不到库就抛，不返回空表（空表会念成「这些窗口都没涨过」）。"""
    rows = engine.query(PCT_SQL, {'codes': codes, 'lo': lo, 'hi': hi}, role='reader')
    if rows is None:
        raise RuntimeError('样本库窗口涨跌幅读不到（market_data.stock_daily）—— 不是「这些窗口没涨过」，是查询失败')
    return {(r['security_code'], r['trade_date'].isoformat() if hasattr(r['trade_date'], 'isoformat')
             else str(r['trade_date'])[:10]): (None if r['pct_change'] is None else float(r['pct_change']))
            for r in rows}


def _ratio_list(v: Any) -> List[float]:
    """库里 `ratios` 存的是 [{'d':..,'v':..}]（有时是 jsonb 字符串）→ 纯倍数序列（旧→新）。"""
    import json
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    out: List[float] = []
    for p in (v or []):
        x = (p or {}).get('v') if isinstance(p, dict) else p
        if x is not None:
            try:
                out.append(float(x))
            except (TypeError, ValueError):
                pass
    return out


def _ratio_dates(v: Any) -> List[str]:
    """`ratios` → 窗口日期串列表（旧→新，'YYYY-MM-DD'）。与 `_ratio_list` 同一条容错。"""
    import json
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    out: List[str] = []
    for p in (v or []):
        d = (p or {}).get('d') if isinstance(p, dict) else None
        if d:
            out.append(str(d)[:10])
    return out


def library_rows(cohorts: Optional[Sequence[str]] = None, use_cache: bool = True) -> List[Dict[str, Any]]:
    """样本库（sv-2 那批「票·启动日」窗口）。读不到就抛，不返回空表——
    空表会被调用方念成"历史上一只都不像"，那是两句完全不同的话。

    结果按 `SPEC_VERSION` + 母体 + 进程内 15 分钟缓存（`use_cache=False` 是给测试和重算后强读留的口子）：
    全量取一次要 3 秒上下，而样本库只在 `startup_volume` 重跑时变。
    """
    keep = sorted(set(cohorts or COHORTS))
    ckey = f'{SPEC_VERSION}|{"|".join(keep)}'
    import time as _t
    if use_cache and _lib_cache['key'] == ckey and (_t.time() - _lib_cache['ts']) < LIB_CACHE_TTL:
        return [dict(r, cohorts=list(r['cohorts'])) for r in _lib_cache['rows']]

    rows = engine.query(LIBRARY_SQL, {'spec': SPEC_VERSION}, role='reader')
    if rows is None:
        raise RuntimeError(f'样本库读不到（{SPEC_VERSION}）—— 先查 analytics.startup_volume_profile 还在不在')
    kept = [r for r in rows if r.get('cohort') in keep]
    dates_by_row = [_ratio_dates(r.get('ratios')) for r in kept]
    all_dates = [d for ds in dates_by_row for d in ds]
    pct: Dict[Tuple[str, str], Optional[float]] = {}
    if all_dates:
        pct = _window_pct_map(sorted({r['security_code'] for r in kept}), min(all_dates), max(all_dates))

    out: List[Dict[str, Any]] = []
    by_window: Dict[Tuple[str, Any], Dict[str, Any]] = {}
    for r, ds in zip(kept, dates_by_row):
        ratios = _ratio_list(r.get('ratios'))
        counts = {k: r.get(k) for k in SHAPE_KEYS if k in r}
        counts.update(SV.range_counts(ratios))            # ★ 同一份区间判据
        near = ratios[-10:] if len(ratios) >= 10 else ratios
        prior = ratios[:-10] if len(ratios) > 10 else []
        near_avg = (sum(near) / len(near)) if near else None
        prior_avg = (sum(prior) / len(prior)) if prior else None
        # 窗口 = profile 存下来的那 30 根（不是"启动日之前的全部历史"）；逐日涨跌幅在 Python 侧对齐相加
        vals = [pct.get((r['security_code'], d)) for d in ds]
        got = [v for v in vals if v is not None]
        # 与 `_pct_sum` 同一条门槛：窗口一半以上的日子读不到 ⇒ 这个和不能用（给 None，不给半截数）
        gp = round(sum(got), 2) if len(got) >= max(2, len(ds) // 2) else None
        key = (r['security_code'], r['launch_date'])
        row = by_window.get(key)
        if row is not None:
            # board4 与 board4_45 会对同一个「票·启动日」各交一行（窗口完全相同）⇒ 只留一份，
            # 母体归属并成 cohorts。不然榜上会出现「同一只票同一天连着两行」，十个名额被吃掉一半。
            if r['cohort'] not in row['cohorts']:
                row['cohorts'].append(r['cohort'])
            continue
        row = {
            'security_code': r['security_code'], 'launch_date': r['launch_date'],
            'cohorts': [r['cohort']], 'cohort': r['cohort'],
            'name': r.get('name'), 'board': r.get('board'),
            'bars_used': r.get('bars_used'), 'avg': r.get('ratio_avg'),
            'counts': counts,
            'features': features(counts, r['security_code'], r.get('ratio_avg'), r.get('ratio_median'),
                                 r.get('ratio_p60'), r.get('ratio_p80'), r.get('ratio_p90'),
                                 r.get('ratio_max'), r.get('std_days'),
                                 int(r.get('bars_used') or len(ratios)),
                                 gp, near_avg, prior_avg,
                                 None, r.get('board'), bool(r.get('is_st'))),
            'long_gap_days': r.get('long_gap_days'),
            'is_st': bool(r.get('is_st')),
            'trigger_len': r.get('trigger_len'), 'trigger_gain': r.get('trigger_gain'),
            'gain_first4': r.get('gain_first4'), 'equiv_limit_ups': r.get('equiv_limit_ups'),
        }
        by_window[key] = row
        out.append(row)
    if use_cache:
        _lib_cache.update({'key': ckey, 'rows': out, 'ts': _t.time()})
    return out


def _moods(dates: List[str]) -> Dict[str, Optional[float]]:
    if not dates:
        return {}
    from duanxian import pg_sources
    got = pg_sources.sentiment_on_dates(dates) or {}
    out: Dict[str, Optional[float]] = {}
    for d in dates:
        out[d] = (pg_sources.mood_score(got.get(d)) or {}).get('mood_score') if got.get(d) else None
    return out


def _after(d0: dt.date, code: str, offs: Sequence[int] = (5, 10, 20)) -> Dict[str, Any]:
    """启动日之后第 N 个交易日的累计涨跌幅（百分点之和，与窗口那一侧同定义）。

    读不到就是 None，**不当 0**：新样本可能因为链路晚于它跑而缺后半段。
    """
    r = engine.query_one(
        "with x as (select trade_date, pct_change, row_number() over (order by trade_date) rn "
        "  from market_data.stock_daily where security_code=%s and trade_date > %s "
        " order by trade_date limit 25) "
        "select max(rn) n, sum(pct_change) filter (where rn<=%s) a, "
        "  sum(pct_change) filter (where rn<=%s) b, sum(pct_change) filter (where rn<=%s) c "
        "from x", (code, d0, offs[0], offs[1], offs[2]), role='reader')
    if not r or not r.get('n'):
        return {'after_5': None, 'after_10': None, 'after_20': None, 'sessions_after': None}
    return {'after_5': None if r['a'] is None else round(float(r['a']), 2),
            'after_10': None if r['b'] is None else round(float(r['b']), 2),
            'after_20': None if r['c'] is None else round(float(r['c']), 2),
            'sessions_after': int(r['n'] or 0)}


def similar(code: str, k: int = 10, cohorts: Optional[Sequence[str]] = None,
            as_of: Optional[dt.date] = None, distinct_code: bool = True) -> Dict[str, Any]:
    """一屏要的全部：目标票那一行在最前，随后是最相近的 k 个样本（相近分降序）。"""
    t = target_features(code, as_of)
    if not t.get('available'):
        return {'available': False, 'reason': t.get('reason'), 'code': code}
    tf = t['features']
    if tf['bars'] < MIN_BARS:
        return {'available': False, 'code': code,
                'reason': f'这只票在本机只有 {tf["bars"]} 根倍数读数（<{MIN_BARS}）⇒ 窗口形状本身不可比，'
                          '不硬凑一个相似榜', 'target': t}
    lib = library_rows(cohorts)
    tdate = dt.date.fromisoformat(t['window_to'])
    cands: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for c in lib:
        if int(c.get('bars_used') or 0) < MIN_BARS:
            continue                                       # 硬过滤：根数不够
        if (c.get('long_gap_days') or 0) > 0:
            continue                                       # 硬过滤：窗口里有长停牌跳空
        cg, tg = c['features'].get('board_group'), tf.get('board_group')
        if not cg or not tg or cg != tg:
            continue          # 硬过滤：不同档不互比；**任一侧认不出档也不比**（不猜像）
        if c['security_code'] == code:                     # 排除自身重叠窗口
            launch = c['launch_date']
            if isinstance(launch, str):
                launch = dt.date.fromisoformat(launch[:10])
            if abs((launch - tdate).days) <= 90:
                continue
        s = score_pair(tf, c['features'])
        if s['score'] is None:
            continue
        cands.append((c, s))
    cands.sort(key=lambda x: (-x[1]['score'], str(x[0]['launch_date'])))
    # 情绪那一维是筛完之后批量补的（`_moods` 一发查询，不逐只打），补上它会改合分 ⇒
    # 先多取 MOOD_WIDEN 倍，补完重排再截断；否则"排名"用的是没有情绪的分，榜却是有了分数的榜，两头对不上。
    pool = cands[:max(k, k * MOOD_WIDEN)]
    dates = sorted({str(t['window_to'])[:10]} | {str(c['launch_date'])[:10] for c, _ in pool})
    moods = _moods(dates)
    tf['mood'] = moods.get(str(t['window_to'])[:10])
    rows: List[Dict[str, Any]] = []
    for c, _s in pool:
        # ⚠️ 不改 `c['features']`：样本库在进程内缓存，往缓存对象上写字段会把上一只票的情绪带给下一只
        feats = dict(c['features'])
        feats['mood'] = moods.get(str(c['launch_date'])[:10])
        d0 = c['launch_date']
        d0 = dt.date.fromisoformat(str(d0)[:10]) if isinstance(d0, str) else d0
        fresh = score_pair(tf, feats)
        rows.append({**c, 'mood': feats['mood'], **_after(d0, c['security_code']),
                     'score': fresh['score'], 'parts': fresh['parts'],
                     'weights_used': fresh['weights_used'],
                     'missing_parts': fresh['missing_parts']})
    rows.sort(key=lambda r: (-(r['score'] or 0), r['launch_date']))
    # 一只票可以有多个启动窗口（实测 300691 的榜里 300589 的两个窗口占了两个名额）。
    # 十个名额按**十只票**取（每只留它最像的那个窗口）：同一只票的两个窗口高度相关，摆两行只会让人以为样本更多。
    neighbours: List[Dict[str, Any]] = []
    seen_codes: set = set()
    same_code_dropped = 0
    for r in rows:
        if distinct_code and r['security_code'] in seen_codes:
            same_code_dropped += 1
            continue
        seen_codes.add(r['security_code'])
        neighbours.append(r)
        if len(neighbours) >= k:
            break
    return _jsonable({'available': True, 'code': code,
            'spec_version': SPEC_VERSION, 'vr_spec': VR_SPEC, 'window': WINDOW,
            'min_bars': MIN_BARS, 'cohorts': list(cohorts or COHORTS), 'weights': WEIGHTS,
            'target': t,
            'neighbours': [{'security_code': c['security_code'], 'name': c.get('name'),
                            'board': c.get('board'), 'launch_date': str(c['launch_date'])[:10],
                            'cohort': c['cohort'], 'cohorts': c.get('cohorts') or [c['cohort']],
                            'is_st': c.get('is_st'), 'board_group': c['features'].get('board_group'),
                            'mood': c.get('mood'),
                            'bars_used': c.get('bars_used'),
                            'score': c['score'], 'parts': c['parts'],
                            'weights_used': c.get('weights_used'),
                            'missing_parts': c['missing_parts'], 'counts': c['counts'],
                            'stats': {k: c['features'].get(k)
                                      for k in ('avg', 'median', 'p60', 'p80', 'p90', 'max', 'min', 'std')},
                            'trigger_len': c.get('trigger_len'), 'trigger_gain': c.get('trigger_gain'),
                            'gain_first4': c.get('gain_first4'), 'equiv_limit_ups': c.get('equiv_limit_ups'),
                            'gain_points': c['features']['price_volume'].get('gain_points'),
                            'up_share': c['features']['price_volume'].get('up_share'),
                            'late_vs_early': c['features']['rhythm'].get('late_vs_early'),
                            'volatility': c['features']['rhythm'].get('volatility'),
                            'after_5': c.get('after_5'), 'after_10': c.get('after_10'),
                            'after_20': c.get('after_20'),
                            'sessions_after': c.get('sessions_after')}
                           for c in neighbours],
            'library_size': len(lib), 'candidates_after_filter': len(cands),
            'same_code_dropped': same_code_dropped, 'distinct_code': distinct_code,
            'caveats': [
                {'code': 'library_scope', 'text':
                 f'样本库只有 {len(lib)} 个「票·启动日」窗口（{SPEC_VERSION}，启动日 '
                 f'{min(str(c["launch_date"])[:10] for c in lib)} ~ {max(str(c["launch_date"])[:10] for c in lib)}）。'
                 '所以「最相近」指的是近一年进入过启动母体的那些窗口里最相近，'
                 '不是全市场历史所有窗口里最相近。'},
                {'code': 'one_per_stock', 'text':
                 f'同一只票的多个启动窗口只留最像的一个（本次合掉 {same_code_dropped} 个），'
                 '十个名额是十只票，不是十种互不相关的形状。'},
                {'code': 'st_unknown', 'text':
                 'ST 身份这一维只有 0.05 权重，而且库里 `security_st_period` 的快照从 2026-08-27 起才有'
                 '（更早的启动日大多是「不知道是不是 ST」，反证法只能证明个别日子「绝非 5% 档」）'
                 '⇒ 名单里出现名称带 ST 的票不算这一维失效，它本来就没被当成筛子。'},
                {'code': 'selection_bias', 'text':
                 '相近分只回答「历史上谁的量能形状像」，不回答「所以也会涨」——样本库本身就是「已经涨起来的票」，'
                 '反过来读成「像它就一定会涨」是选择偏差。'},
            ],
            'caliber': CALIBER, 'definitions': DEFINITIONS,
            'score_note': '相近分 0~100，由六个分量按权重合成（权重与理由见本文件 WEIGHTS 的表）；'
                          '缺的那一维从权重里剔掉再归一，所以分量少的样本不是「扣了分」，是「按剩下的维度算」。'})

