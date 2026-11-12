# -*- coding: utf-8 -*-
"""近期各量化方案的得分与名次 ＋ 多窗口趋势带动 —— 个股深度复盘页的新模块。

用户 2026-11-09 点名（原话摘要）：
  「在这个模块上方新增模块『近期各量化方案名词展示及趋势』：把这只个股近五个交易日 得分、名次 都展示一下，
   包括左侧机会等；还有各个量化评分的趋势，展示每个指标近2日／近5日／近10日／近30日／近60日趋势的连带情况
   —— 就是数字越小的日期范围的趋势在带动数字大的趋势：比如近5日的线最平稳往上，
   把近10日的下降趋势带动得变缓、或平稳、或拐头准备向上。不同日期范围的带动情况都要对比一下。」

这一块回答两问：**这一阵子它在各套方案里各排到什么位置**（横着看，一场一排），
以及**每套方案的读数现在是被短期带着走、还是自己仍在原来的方向上**（纵着看，五档窗口）。

八条不会从代码里自己长出来的口径，写死在这里：

1. **名次必须连母体一起念。** 四族的名次是三把不同的尺（2026-11-09 真库实测）：八项能力是**当日全场**名次
   （分母 5,571~5,573 只），倍量加权分是**当日倍量档表**名次（分母 5,524~5,529 只），
   埋伏分（左侧机会）是**当日入选埋伏形态那批票内部**的名次（分母 134~196 只），
   XY 合计名次是 xyr-1 落好的两轴名次之和。每格都带 `universe`：第 62 名在 179 只里和在 5,529 只里
   是两句不同的话，所以不许只念「第 62 名」。
2. **能不能排名次要先判再念。** 八项能力直接复用 `quant._rank_view` 那一个函数（同分同名、下一名顺延；
   全场同一个值 ⇒ 名次与分位都交 —；取值只有几个档位 ⇒ 名次留着但连并列只数一起念）。
   实测这一族里 `market_mood` 当天全场只有 1 个取值、`vol_stage` 5 个、`sector_heat` 30 个、`env_resonance` 31 个。
3. **窗口是「最近 w 场这段日历」，不是「凑够 w 个有值的场次」。** x 坐标＝场次在这条日历里的序号（1…N），
   缺值的那一场**跳过但不重编号** —— 重编号会把「隔了三天才有值」当成「隔了一场」，斜率的量纲就变了。
   于是每一档同时交 `window_days` 与 `points_used`：10 场的窗口里只有 4 场有值，那条线的置信度就是 4 个点的，
   屏上要标「稀疏」，不能装作它是 10 场的线。点数不足 `min_points(w)`（2 场那一档＝2，其余 `max(3, ⌈w/3⌉)`）⇒ 一个数都不交。
4. **带动不看斜率的绝对大小，只看它挪动了多少个「自己的标准误」。** 各族量纲不同（分／档／名），
   Δ = slope_long(t) − slope_long(t−s) 一律除以长期斜率的标准误 se 得到 z 再判。
   阈值以 se 为单位：|z| ≥ 1 看得见、0.5~1 有苗头；|slope_long| < 0.5·se 先认定长期已经走平（不存在「带动谁」）。
   长期斜率在这几场里过了 0（且过了噪声）⇒ 判拐头，且这一档必须先于同向/反向的分档来判：
   Δ 朝短期方向记「带动拐头」，Δ 不朝短期方向记「自己拐头」（老点滑出走出来的，不是带动）。
   2026-11-09 真库实测：600519.SH 的 44 个相邻档对里有 11 个属于后者，把它们念成
   「没动到」是假话——长期明明已经过了 0。
5. **名次那一条线方向要反过来。** 名次数字越小越好 ⇒ 斜率取负才叫「在前进」；每族交 `direction`，
   屏上红绿按方向念，不按数值正负念（全站那条「名次 1＝最靠前」的纪律在这块同样成立）。
6. **四种状态分开交**：`ok`／`not_selected`（这一族本来就只收入选的票，这一场它没入选——事实，不是缺数）／
   `no_value`（有行但那个字段是空）／`out_of_calendar`（这一场库里没这一族）。合并成一个 —，
   就会把「它今天不在埋伏形态里」念成「埋伏分算不出来」。
7. **2 场那一档只交方向，不交标准误。** 两点定一条线 ⇒ 没有残差可言，se 是空的。
   它当短期档可以（z 的分母取长期档的 se），当长期档不行；五档里它最小，天然当不了长期档。
8. **不落表、不接每日链、不进推送。** 只读已落库的四张表；斜率与带动是读侧现算（一族最多 60 个点，纯 Python）。
   场次与窗口都是界面可调的探索参数，落库要按参数组做主键，而这一块的用途是换着参数对照看。

成本（2026-11-09 本机真库，600519.SH）：八分序列 1.8s、XY 0.2s、倍量全场名次 2.6s、埋伏 0.3s，
分布与并列只数只取**界面上那几场**（不是 60 场，省掉 2.6M 行的扫描），cold 一趟约 5~6 秒
⇒ TTL 缓存一小时，键＝代码＋场次＋本模块口径版本。
"""
from __future__ import annotations

import datetime as dt
import math
import time

from . import pg, ttlcache
from .quant import _capability_names, _coverage, _int, _num, _rank_view

SPEC_VERSION = 'radar-1'

SESSIONS_DEFAULT = 5
SESSIONS_MIN = 2
SESSIONS_MAX = 10

#: 趋势窗口（场）——用户点名的五档。
WINDOWS: tuple[int, ...] = (2, 5, 10, 30, 60)

#: 带动判定的三个阈值，单位都是「长期斜率的标准误」，不是分数也不是百分点。
Z_VISIBLE = 1.0
Z_HINT = 0.5
FLAT_BAND = 0.5

#: 距拐头的线性外推最多念几场，再远就不交这个数。
TURNS_MAX = 60

TTL = 3600

STATE_OK = 'ok'
STATE_NOT_SELECTED = 'not_selected'
STATE_NO_VALUE = 'no_value'
STATE_OUT_OF_CALENDAR = 'out_of_calendar'

#: 每一族是什么、量纲朝哪边、这一格该说什么话。
#: 八项能力的中文名与说明不写在这里（从 `config.capability` join，一处真源）；
#: 另三族注册表里没有，所以名字住这里，且只有这一处。
SERIES_SPEC: tuple[dict, ...] = (
    {'key': 'oper_score', 'family': 'score', 'unit': '分', 'direction': 'higher_better',
     'says': '操作分：0.40 × 个股质量(X) + 0.60 × 环境共振(Y) 的合分，四象限就按它分。'
             '实测对得上：2026-11-06 这场 0.40×52.1967 + 0.60×(−28.2515) = 3.9278，'
             '与库里那格存的 3.9278 逐位相同'},
    {'key': 'stock_quality', 'family': 'score', 'unit': '分', 'direction': 'higher_better',
     'says': '四维象限的 X 轴（个股质量）：形态／趋势／位置／筹码／量能五个分量加权，量纲 −100~100'},
    {'key': 'env_resonance', 'family': 'score', 'unit': '分', 'direction': 'higher_better',
     'says': '四维象限的 Y 轴（环境共振）：板块热度＋大盘情绪。同一天全市场的大盘情绪是同一个值，'
             '所以这一项的跳动多半来自板块与大盘，不是来自这只票自己'},
    {'key': 'temp_score', 'family': 'score', 'unit': '分', 'direction': 'higher_better',
     'says': '温度分：阈值按流通市值插值'},
    {'key': 'vol_temp', 'family': 'score', 'unit': '分', 'direction': 'higher_better',
     'says': '量能温度：量比／放量节奏／持续性／位置加权'},
    {'key': 'sector_heat', 'family': 'score', 'unit': '分', 'direction': 'higher_better',
     'says': '板块热度：涨幅／广度／动量／成交额加权。一只票跟着它所在板块走，全场取值只有几十个档'},
    {'key': 'market_mood', 'family': 'score', 'unit': '分', 'direction': 'higher_better',
     'says': '大盘情绪：「同一天全市场同一个值」⇒ 名次没有横截面意义（屏上念 —）；'
             '五档趋势读的是大盘本身往哪走，不是这只票的相对位置'},
    {'key': 'vol_stage', 'family': 'score', 'unit': '档', 'direction': 'higher_better',
     'says': '量能阶段：离散码（判定顺序 4→3→2→5→1，背离最先判）。只有几个取值，是档位不是连续分，'
             '所以它的斜率读作「档位平均在往哪边移」'},
    {'key': 'xy_rank_sum', 'family': 'combo', 'unit': '名', 'direction': 'lower_better',
     'label': 'XY 合计名次',
     'says': 'X 轴名次 + Y 轴名次（xyr-1 落库），数字越小两轴都越靠前。'
             '趋势那几档先把名次取负再算斜率，所以这一族里「线往上」＝名次在前进'},
    {'key': 'dbl_energy', 'family': 'event', 'unit': '分', 'direction': 'higher_better',
     'label': '倍量加权分',
     'says': '最近 40 根 K 线里每一档倍量按分值累加（顶档 1 分，满窗上限 40）；'
             '名次是当日全场档表名次（分母＝当天读得到加权分的只数）'},
    {'key': 'ambush_score', 'family': 'event', 'unit': '分', 'direction': 'higher_better',
     'label': '埋伏分（左侧机会）',
     'says': '五种埋伏形态的合成读数。「这一族只收当天入选的票」：没行＝这一场它没入选（事实，不是缺数）；'
             '名次只在入选那批票内部排（实测母体 134~196 只，与全场名次不可直接比）。'
             '因为母体本身稀疏，各窗口的 points_used 会明显小于 window_days'},
)

#: 四种状态各自说什么话（屏上那一排缺数声明按这里逐态念，不许合并成一个「没读数」）。
#: 名字与 `STATE_*` 一对一；页面只转录，不自造第二套中文。
STATE_SAYS = {
    STATE_OK: '',
    STATE_NOT_SELECTED: '这一场它不在那批入选票里（这是事实，不是缺数）',
    STATE_NO_VALUE: '有行，但那个字段是空的',
    STATE_OUT_OF_CALENDAR: '这一族在库里没有这一场',
}

FAMILY_LABEL = {'score': '八项量化能力', 'combo': '两轴合计名次', 'event': '形态榜'}

#: 每一档都是「短词：整句」。屏上那一格只念短词（`stateWord` 在「：」处截），
#: 悬停与抽屉念整句。短词漏了冒号就会把整句摆进十列宽的表里（2026-11-09 浏览器实测就是这样）。
CARRY_LABEL = {
    'accelerating': '加速：短期与长期同向，且在把长期往前推',
    'dragging_slow': '变缓：短期与长期反向，长期正在被拖慢',
    'turned_flat': '走平：反向带动已到位，长期斜率已经贴 0',
    'turned_over': '带动拐头：短期把长期推过了 0，现在两档同向',
    'turned_own': '自己拐头：长期过了 0，但这一步是窗口滑走的老点带的，不是最近这几场推的',
    'hint': '苗头：带动还没过噪声',
    'silent': '没带动：短期这点动静推不动长期（差在噪声量级内）',
    'opposite_stuck': '没动到：短期与长期相反，但长期还没被推动',
    'unknown': '算不出：某一档窗口点数不足，或长期斜率没有残差可言',
}

COLUMNS: tuple[dict, ...] = (
    {'key': 'label', 'label': '量化方案',
     'says': '这一行是哪一套读数；括号里的族说它出自哪一类表（八项能力／两轴合计／形态榜）'},
    {'key': 'cells', 'label': '近几场的得分与名次',
     'says': '每一格四样：分值／当日名次（带母体只数）／本屏分位＝（母体−名次）÷母体／'
             '名次比上一场动了几名（正＝前进）。母体必须一起念：第 62 名在 179 只里和在 5,529 只里是两句不同的话。'
             '名次那格里带「并列」的就一起看：同分的挤在一起时，相邻几名之间可能压根没有分差'},
    {'key': 'trend', 'label': '各窗口斜率（每场动多少）',
     'says': '在最近 w 场这段日历上做最小二乘；括号里前一个是 r²（线性拟合优度），'
             '后一个是这一段真正用掉几个点（window_days 之外还有 points_used 时标「稀疏」）。'
             'r² 低 ⇒ 这条趋势线本身不成立，只当形状看'},
    {'key': 'carry', 'label': '带动判定',
     'says': '短期窗口的方向，有没有把长期窗口的斜率挪动：Δ 除以长期斜率自己的标准误得到 z，'
             '|z| 过 1 才说「带动了」。括号里那个数就是 z，「距拐头」是把 Δ 当匀速外推'},
)

RULES = {
    'sessions': '「近几场」是交易日场次，不是日历日：长假一压，5 个日历日可能只剩 3 个交易日。'
                '基准日取 `analytics.score` 的最新一场；其他三族各按自己的日历落点，'
                '对不齐的场次在格子里按状态念出来，不硬凑。',
    'window': '窗口＝最近 w 场这段日历，x 坐标＝场次序号；缺值的那一场跳过但「不重编号」'
              '（重编号会把「隔三天才有值」当成「隔一场」，斜率量纲就变了）。'
              '每档同时交 window_days 与 points_used，凑不到门槛点数（2 场那一档要 2 个点，其余 max(3, ⌈w/3⌉)）就一个数都不交。',
    'slope': '斜率＝对窗口内「场次序号 → 值」做最小二乘，单位是每场动多少（分／档／名各按各的量纲）。'
             '标准误 se ＝ 残差标准差 ÷ √Σ(t − t̄)²：它说的是这条斜率本身有多不确定。'
             '两点定一条线 ⇒ 没有残差，2 场那一档的 se 是空的：它只当方向读数，'
             '当短期档可以（z 的分母取的是长期档的 se），当长期档不行——而它是五档里最小的，天然当不了长期档。',
    'carry': f'带动＝Δ = slope_long(t) − slope_long(t−s)（s 是短期窗口那几场），z = Δ ÷ se_long。'
             f'先判长期是否还在走：|slope_long| < {FLAT_BAND}×se_long ⇒ 屏上念「走平」，谈不上带动谁。'
             '再判长期有没有跨过 0：在这 s 场里过了 0 且过了噪声（|z| ≥ '
             f'{Z_HINT}）就是拐头，这一档先于「同向/反向」的分档来判——'
             'Δ 朝短期方向 ⇒ 念「带动拐头」（短期把长期推过了 0，现在两档同向）；'
             'Δ 不朝短期方向 ⇒ 念「自己拐头」：长期确实过了 0，但那一步是窗口滑走的老点带的，'
             '不是最近这几场推的，不许算成带动。'
             f'短期与长期同向时：|z| ≥ {Z_VISIBLE} 且 Δ 也朝这一向 ⇒「加速」；{Z_HINT} ≤ |z| < {Z_VISIBLE} ⇒「苗头」；'
             f'|z| < {Z_HINT} ⇒「没带动」（差在噪声量级内）。'
             '短期与长期反向时：Δ 不朝短期方向或没过噪声 ⇒「没动到」；Δ 朝短期方向且 |z| ≥ '
             f'{Z_VISIBLE} ⇒「变缓」（长期正在被拖慢）。'
             f'某一档窗口点数不足或长期斜率没有残差 ⇒「算不出」，那一格念 —，不猜一个状态。'
             f'「距拐头」＝ |slope_long| ÷（|Δ| ÷ s），只在 Δ 确实在把长期往回拉时才算，超过 {TURNS_MAX} 场不交这个数。',
    'rank': '名次口径全站只有一份（同分同名、下一名按位置顺延；没得分的不占号），'
            '但母体是三把尺：八项能力与倍量是当日全场，埋伏分是当日入选那批票内部，'
            'XY 合计名次由 xyr-1 落库。每格自带的分母就是那把尺。'
            '这一块的「分位」是屏幕上那两个数直接推出的（母体 − 名次）÷ 母体，四族同一把尺；'
            '库里那列 percent_rank 在这里不念——实测它推不出这个关系（同场同项反推的分母从 76 散布到 11,173），'
            '它是写侧另一套分布的产物。八项加权明细那一块念的才是库里那一个，两处各种自己的尺。',
    'direction': '名次那一族数字越小越好 ⇒ 斜率取负才叫在前进；屏上红绿按方向念，不按数值正负念。',
    'not_this': '这一块不预测股价、不给买卖点、不把各套方案加权成一个总分'
                '（三把不同母体的尺合成一个数就是假数）。名次比上一场动了几名只是记账。',
}

GUIDE: tuple[dict, ...] = (
    {'heading': '这一块回答什么', 'body': RULES['sessions'] + ' ' + RULES['not_this']},
    {'heading': '各方案分别出自哪张表、名次是哪一批里的名次', 'body': RULES['rank']},
    {'heading': '五档窗口在量什么', 'body': RULES['window'] + ' ' + RULES['slope']},
    {'heading': '「带动」这个词怎么变成一个数', 'body': RULES['carry']},
    {'heading': '为什么有的窗口是空的、有的是「稀疏」',
     'body': '点数不足（2 场那一档要 2 个点，其余要 max(3, ⌈w/3⌉) 个）⇒ 一个数都不交，念 —，不是 0；'
             '点数够但明显少于 window_days ⇒ 交斜率但标「稀疏」，意思是这条线只由那几个点撑著。'
             '埋伏分这一族最常见：实测 600519.SH 近 60 场只有 30 场入选。'},
    {'heading': '没入选、没读到、没有值是三种不同的话', 'body': '见上面四态：' +
     'ok＝算得出；not_selected＝这一场它不在那批入选票里（事实）；'
     'no_value＝有行但那个字段是空；out_of_calendar＝这一场库里没这一族。'},
    {'heading': '名次能不能排名次，先判再念',
     'body': '同一天全市场同一个值的那一项（大盘情绪）名次与分位都是 —；'
             '取值只有几个档位的（量能阶段、板块热度、环境共振）名次挤在档位里，必须连并列只数一起看。'
             '判据与全站其它名次格同一个函数（`_rank_view`），这块不另写一份。'},
)

#: 只有这两条可以收进抽屉；缺数／截断／告警一律留屏。
FOLDABLE_CAVEAT_CODES = frozenset({'radar_stale_rows', 'radar_trend_shape'})

#: 全指标总账那一屏按这两个名字来取（`duanxian/stock_metrics_ledger.py`）：
#: 字段义一份、这块的母体句一份，页面与总账读的都是这里的原句。
DEFINITIONS: dict = {c['label']: c['says'] for c in COLUMNS}
CALIBER = RULES['rank']


def min_points(w: int) -> int:
    """一档窗口最少要有几个点才交数。

    2 场那一档按定义只有两个点 ⇒ 门槛就是 2（它交的是「这两天往哪走」，不是一条要残差撑的线）；
    其余各档要三点以上才有斜率的标准误可言，长窗口按三分之一兜。
    """
    return 2 if w <= 2 else max(3, -(-w // 3))


# ───────────────────────────────────────────────── 纯判据（不碰库）

def slope_stats(points: list[tuple[float, float]]) -> dict:
    """最小二乘：斜率、截距、r²、斜率标准误。points＝[(场次序号, 值)]，调用方已按窗口切好。

    点数 < 2 ⇒ 一个数都不交（不是交 0）。点数正好 2 ⇒ se 交 None：两点定一条线，没有残差可言。
    """
    n = len(points)
    if n < 2:
        return {'n': n, 'slope': None, 'intercept': None, 'r2': None, 'slope_se': None,
                'why': f'这一段只有 {n} 个点，两点才成一条线'}
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    if sxx <= 0:
        return {'n': n, 'slope': None, 'intercept': None, 'r2': None, 'slope_se': None,
                'why': '场次序号没有变化'}
    slope = sxy / sxx
    intercept = my - slope * mx
    syy = sum((y - my) ** 2 for y in ys)
    corr = sxy / math.sqrt(sxx * syy) if syy > 0 else None
    resid = sum((ys[i] - (intercept + slope * xs[i])) ** 2 for i in range(n))
    se = math.sqrt(resid / (n - 2) / sxx) if n > 2 and sxx > 0 else None
    return {'n': n, 'slope': round(slope, 4), 'intercept': round(intercept, 4),
            'r2': round(corr * corr, 3) if corr is not None else None,
            'slope_se': round(se, 4) if se is not None else None, 'why': None}


def window_stats(seq: list[tuple[float, float, str]], w: int, shift: int = 0) -> dict:
    """最近 w 场这段日历（再往前挪 `shift` 场）里的拟合。seq＝[(场次序号, 值, 日期)]，已按整条日历编号。

    ⚠️ 这里按 **x 落在 (hi−w, hi]** 取点，不是取最后 w 个有值的点：
    取最后 w 个点会把稀疏那族的窗口悄悄拉长到一两百场，那已经不是「近 30 场趋势」了。
    """
    hi = seq[-1][0] if seq else 0.0
    top = hi - shift
    lo = top - w
    pts = [(x, y) for x, y, _ in seq if lo < x <= top]
    dates = [d for x, _, d in seq if lo < x <= top]
    st = slope_stats(pts)
    need = min_points(w)
    usable = st['n'] >= need
    st.update({'window': w, 'shift': shift, 'points_used': st['n'], 'window_days': w,
               'min_points': need, 'usable': usable,
               'no_se': st['n'] <= 2,
               'sparse': usable and st['n'] < w,
               'from': dates[0] if dates else None, 'to': dates[-1] if dates else None})
    if not usable:
        st['slope'] = st['intercept'] = st['r2'] = st['slope_se'] = None
        st['why'] = (st['why'] or '') + (f'；这一窗 {w} 场里只有 {st["n"]} 场有值，'
                                        f'门槛是 {need} 个点' if st['n'] < need else '')
    elif st['n'] <= 2:
        st['why'] = '两点定一条线：这一档只交方向与两点斜率，没有残差 ⇒ 斜率标准误是空的'
    return st


def carry_state(short: dict | None, long_now: dict | None, long_prev: dict | None) -> dict:
    """带动判定。三个输入都是 `window_stats`/`slope_stats` 的返回；
    `long_prev` 是同一个长期窗口往前挪 s 场那一次的拟合结果。

    ⚠️ 任一缺数就交 unknown，不猜一个状态出来：猜出来的「拐头」会被下游念成"这票要转了"。
    """
    base = {'state': 'unknown', 'z': None, 'delta': None, 'turns_left': None,
            'label': CARRY_LABEL['unknown']}
    if not short or not long_now or not long_prev:
        return base
    ss, sl, sp = short.get('slope'), long_now.get('slope'), long_prev.get('slope')
    se = long_now.get('slope_se')
    if ss is None or sl is None or sp is None or not se:
        return base
    delta = sl - sp
    z = delta / se
    flat = abs(sl) < FLAT_BAND * se
    toward = (delta > 0) == (ss > 0)
    crossed = (sp > 0) != (sl > 0)        # 长期斜率在这 s 场里过了 0：用户点名的「拐头」就是这一种
    if flat:
        st = 'turned_flat'
    elif crossed and abs(z) >= Z_HINT:
        # 拐头这一档必须先于同向/反向的分档来判：否则「近 5 日上行把近 10 日下行带动得拐头」
        # 会被念成「同向加速」，而 Δ 不朝短期方向的拐头会被念成「长期还没被动到」——两句都是假话。
        st = 'turned_over' if toward else 'turned_own'
    elif (ss > 0) == (sl > 0):
        st = 'accelerating' if (abs(z) >= Z_VISIBLE and toward) else (
            'hint' if abs(z) >= Z_HINT else 'silent')
    else:
        if not toward or abs(z) < Z_HINT:
            st = 'opposite_stuck'
        elif abs(z) >= Z_VISIBLE:
            st = 'dragging_slow'
        else:
            st = 'hint'
    turns = None
    if st in ('dragging_slow', 'hint') and delta and (delta > 0) != (sl > 0):
        per = abs(delta) / max(short.get('window') or 1, 1)        # 每场把长期斜率挪多少
        if per > 0:
            est = abs(sl) / per
            if est <= TURNS_MAX:
                turns = round(est, 1)
    return {'state': st, 'z': round(z, 2), 'delta': round(delta, 4), 'turns_left': turns,
            'label': CARRY_LABEL[st]}


def rank_delta(prev_rank: int | None, rank: int | None) -> int | None:
    """名次比上一场动了几名：正＝前进（名次数字变小）。任何一边没数就交 None，不补 0。"""
    if prev_rank is None or rank is None:
        return None
    return prev_rank - rank


# ───────────────────────────────────────────────── 取数（只读）

def _days(n: int) -> list[str]:
    df = pg.fetch_df("""select trade_date::text d from (
                          select distinct trade_date from analytics.score
                           order by trade_date desc limit %s) z order by d""", (int(n),))
    return [] if df is None else [r['d'] for r in df.to_dict('records')]


def _rows_or(sql: str, params: tuple, why: str) -> tuple[list[dict], str | None]:
    df = pg.fetch_df(sql, params)
    if df is None:
        return [], f'{why}读不到（库不通或这条查询失败）—— 这一族的格子全交 —，不是它没有分'
    return df.to_dict('records'), None


_SCORE_SQL = """select trade_date::text d, capability_key k, value v, rank_in_market rk,
                       percent_rank pct, quadrant qd, is_stale stale
                  from analytics.score
                 where security_code = %s and trade_date >= %s::date
                   and capability_key = any(%s::text[])
                 order by d, k"""

#: 分布与并列只数：只算界面上那几场（不是整条 60 场日历）。
#: ⚠️ 三份名单一律走 `= any(%s::text[])`，**不许**拼成 `'a','b'` 再当参数塞进 `in (%s)` ——
#: 那样驱动会把整串当成**一个**文本参数绑定（2026-11-09 实测：八项能力 0 行、
#: 报错 `invalid input syntax for type date: "'2026-11-02','2026-11-03',..."`），
#: 结果是"这一族没读到"被界面念成"这一族没有分"。同族缺陷在 ambush 那条 `INTERVAL '%s'` 上踩过一次。
_DIST_SQL = """select d::text d, k, count(*) uni, count(distinct v) dv, max(tie) biggest_tie
                 from (select trade_date d, capability_key k, value v,
                              count(*) over (partition by trade_date, capability_key, value) tie
                         from analytics.score
                        where trade_date = any(%s::date[])
                          and capability_key = any(%s::text[]) and value is not null) z
                group by 1, 2"""

_TIE_SQL = """select s.trade_date::text d, s.capability_key k, count(*) tie_at
                 from analytics.score s
                 join analytics.score m
                   on m.trade_date = s.trade_date and m.capability_key = s.capability_key
                  and m.value = s.value
                where m.security_code = %s and m.capability_key = any(%s::text[])
                  and s.trade_date = any(%s::date[]) and s.capability_key = any(%s::text[])
                  and s.value is not null
                group by 1, 2"""

_XY_SQL = """select trade_date::text d, rank_x rx, rank_y ry, rank_sum rs, universe uni, stale
               from analytics.xy_rank_daily
              where security_code = %s and spec_version = 'xyr-1' and trade_date >= %s::date
              order by d"""

_DBL_SQL = """with c as (select trade_date::text d, security_code code, dbl_energy::float v,
                                rank() over (partition by trade_date order by dbl_energy desc nulls last)::int rk,
                                count(*) over (partition by trade_date)::int uni
                           from analytics.volume_double
                          where dbl_energy is not null and trade_date >= %s::date)
             select d, v, rk, uni from c where code = %s order by d"""

_AMB_SQL = """with c as (select trade_date::text d, security_code code, ambush_score::float v,
                                rank() over (partition by trade_date order by ambush_score desc nulls last)::int rk,
                                count(*) over (partition by trade_date)::int uni,
                                ambush_types types, strict_pass pass
                           from analytics.ambush_setup
                          where trade_date >= %s::date)
             select d, v, rk, uni, types, pass from c where code = %s order by d"""


def _fetch(sec: str, lo: str, recent: list[str], keys: list[str]) -> dict:
    err: list[str] = []
    score, e = _rows_or(_SCORE_SQL, (sec, lo, keys), '八项能力的分值'); err += [e] if e else []
    dist, e = _rows_or(_DIST_SQL, (recent, keys), '八项能力的当日分布'); err += [e] if e else []
    tie, e = _rows_or(_TIE_SQL, (sec, keys, recent, keys), '并列只数'); err += [e] if e else []
    xy, e = _rows_or(_XY_SQL, (sec, lo), 'XY 合计名次'); err += [e] if e else []
    dbl, e = _rows_or(_DBL_SQL, (lo, sec), '倍量加权分'); err += [e] if e else []
    amb, e = _rows_or(_AMB_SQL, (lo, sec), '埋伏分（左侧机会）'); err += [e] if e else []
    return {'score': score, 'dist': dist, 'tie': tie, 'xy': xy, 'dbl': dbl, 'amb': amb,
            'errors': err}


def _indexed(spec: dict, frames: dict) -> dict:
    key = spec['key']
    if spec['family'] == 'score':
        return {(r['d']): r for r in frames['score'] if r['k'] == key}
    src = frames['dbl'] if key == 'dbl_energy' else frames['amb'] if key == 'ambush_score' else frames['xy']
    return {r['d']: r for r in src}


def _text(v) -> str | None:
    """文本列的出口清洗。pandas 把 SQL 的 NULL 落在文本列上交给的是 `nan`（不是 None），
    带进 JSON 就是非法的 `NaN` 字面量，浏览器 `JSON.parse` 当场炸——这一族每一场都有象限为空，
    不清洗等于每一发都可能炸。"""
    if v is None or (isinstance(v, float) and v != v):
        return None
    return str(v)


def _flag(v) -> bool | None:
    """布尔列的出口清洗：`bool(nan)` 是 True，直接套会把「库里没写」念成「是旧的」。"""
    return v if isinstance(v, bool) else None


def _share(rank, uni) -> float | None:
    """本屏分位＝（母体 − 名次）÷ 母体 × 100，越大越靠前。四族只用这一把尺。

    ⚠️ 这一块**不念**库里那列 `percent_rank`：它不是「名次 ÷ 母体」。
    2026-11-06 一场实测（全 5,573 行 × 八项）按 `名次 × 100 ÷（100 − 库里分位）` 反推分母，
    得到的数从 76 一直散布到 11,173（带并列档位的那几项直接反推成几十），
    它是写侧另一套分布的产物，与本屏交出的母体对不齐。八项加权明细那一块念的是库里那一个，
    两处各自声明自己的尺，不许在一屏里并排摆两个分位。
    """
    if not uni or rank is None or uni <= 0:
        return None
    return round((uni - int(rank)) * 100.0 / uni, 2)


def _cell(spec: dict, key: str, d: str, row: dict | None, sp: dict | None) -> dict:
    """一格：把"这一场这一族"读成一条带状态的结构。四种状态在这里分完。

    `sp` 只有界面那几场有（分布与并列只数按那几场取），趋势序列传 None：
    那一头的目的是取值，不是排名次。
    """
    if spec['family'] == 'score':
        if row is None:
            return {'date': d, 'state': STATE_OUT_OF_CALENDAR}
        v = _num(row.get('v'), 4)
        if v is None:
            return {'date': d, 'state': STATE_NO_VALUE}
        rv = _rank_view({'rank_in_market': row.get('rk'), 'percent_rank': row.get('pct'),
                         'value': v}, sp)
        return {'date': d, 'state': STATE_OK, 'value': v, 'rank': rv.get('rank_in_market'),
                'share': _share(rv.get('rank_in_market'), rv.get('universe')),
                'quadrant': _text(row.get('qd')),
                'stale': _flag(row.get('stale')), 'universe': rv.get('universe'),
                'distinct_values': rv.get('distinct_values'), 'tie_at_value': rv.get('tie_at_value'),
                'biggest_tie': rv.get('biggest_tie'), 'rank_kind': rv.get('rank_kind'),
                'rank_note': rv.get('rank_note')}
    if row is None:
        return {'date': d, 'state': STATE_NOT_SELECTED if key == 'ambush_score'
                else STATE_OUT_OF_CALENDAR}
    if key == 'xy_rank_sum':
        rs = _int(row.get('rs'))
        if rs is None:
            return {'date': d, 'state': STATE_NO_VALUE}
        return {'date': d, 'state': STATE_OK, 'value': float(rs), 'rank': rs,
                'share': _share(rs, _int(row.get('uni'))),
                'universe': _int(row.get('uni')), 'rank_kind': 'ok',
                'rank_note': '合计名次＝X 轴名次 + Y 轴名次（xyr-1 落库），母体是当天参与排名的只数；'
                             '并列只数这一块没取（要按合计值全场分组再数一遍），所以那一格念 —',
                'stale': _flag(row.get('stale')), 'rank_x': _int(row.get('rx')),
                'rank_y': _int(row.get('ry'))}
    v = _num(row.get('v'), 4)
    if v is None:
        return {'date': d, 'state': STATE_NO_VALUE}
    rk, uni = _int(row.get('rk')), _int(row.get('uni'))
    note = ('名次＝本块自己按当日档表里有加权分的只数现排（与倍量榜那一列同一把尺）；'
            '并列只数没取——顶档同分的往往几十只，那一格念 —'
            if key == 'dbl_energy' else
            '名次只在当天入选埋伏形态那批票内部排（母体 134~196 只），与全场名次不是同一把尺；'
            '并列只数没取，那一格念 —')
    return {'date': d, 'state': STATE_OK, 'value': v, 'rank': rk, 'universe': uni,
            'rank_kind': 'ok', 'share': _share(rk, uni), 'rank_note': note,
            'types': _text(row.get('types')),
            'strict_pass': row.get('pass') if isinstance(row.get('pass'), bool) else None}


def _caveats(series: list[dict], frames: dict, recent: list[str], days: list[str]) -> list[dict]:
    out: list[dict] = []
    for e in frames.get('errors') or []:
        out.append({'code': 'radar_frame_failed', 'label': '有一族没读到', 'text': e})
    stale = sum(1 for s in series for c in s['cells'] if c.get('stale'))
    if stale:
        out.append({'code': 'radar_stale_rows', 'label': '用的是上一场的值',
                    'text': f'本次 {len(recent)} 场 × {len(series)} 族里有 {stale} 格标着 is_stale'
                            f'（当天没刷进来，读数沿用上一场）'})
    nosel = sum(1 for s in series for c in s['cells'] if c.get('state') == STATE_NOT_SELECTED)
    if nosel:
        out.append({'code': 'radar_not_selected', 'label': '有些场次没入选',
                    'text': f'埋伏分只收当天入选的票：本次有 {nosel} 场它没入选。这是事实，不是缺数，'
                            f'屏上念「没入选」而不是 —'})
    short = [(s['label'], t) for s in series for t in s['trend'] if not t['usable']]
    if short:
        eg = '、'.join(f'{lb}·近{t["window"]}场（{t["points_used"]}/{t["window"]} 个点）'
                       for lb, t in short[:8])
        out.append({'code': 'radar_window_short', 'label': '窗口点数不足',
                    'text': f'{len(short)} 档窗口点数不足门槛（例：{eg}）⇒ 那些格交 —，不是 0'})
    sparse = sum(1 for s in series for t in s['trend'] if t.get('sparse'))
    if sparse:
        out.append({'code': 'radar_window_sparse', 'label': '稀疏窗口',
                    'text': f'{sparse} 档窗口交得出斜率但点数少于窗口长度（屏上标「稀疏」）：'
                            f'那条线只由那几个点撑著，置信度按点数算，不按窗口长度算'})
    coarse = sorted({s['label'] for s in series for c in s['cells']
                     if c.get('rank_kind') in ('degenerate', 'coarse', 'no_rank', 'unknown')})
    if coarse:
        out.append({'code': 'radar_rank_kind', 'label': '有些名次要先判能不能排',
                    'text': f'{"、".join(coarse)} 的名次要么没有横截面意义、要么挤在档位里 ⇒ '
                            f'名次与分位各按那一档念，屏上不硬给一个「第几名」'})
    unknown = sum(1 for s in series for c in s['carry'] if c['state'] == 'unknown')
    if unknown:
        out.append({'code': 'radar_trend_shape', 'label': '带动判定有算不出的格',
                    'text': f'{unknown} 个窗口对的带动判定算不出（某一档点数不足，或长期斜率没有残差可言）。'
                            f'那一格念 —，不猜一个状态出来'})
    out.append({'code': 'radar_calendar', 'label': '日历长度',
                'text': f'趋势最多回看 {len(days)} 场（库里 {days[0]} 起，`analytics.score` 的场次日历）；'
                        f'得分与名次只念最近 {len(recent)} 场'})
    return out


def board(code: str, sessions: int | None = None, refresh: bool = False) -> dict:
    """单票：各方案近几场的得分与名次 ＋ 五档窗口趋势 ＋ 各窗口两两的带动判定。"""
    t0 = time.monotonic()
    sec = (code or '').strip().upper()
    if not sec:
        return {'available': False, 'reason': '缺少 code 参数：这一块必须先选一只票'}
    n = max(SESSIONS_MIN, min(int(sessions or SESSIONS_DEFAULT), SESSIONS_MAX))
    key = f'scheme_radar:{SPEC_VERSION}:{sec}:{n}'
    if refresh:
        ttlcache.clear(key)

    def build() -> dict:
        days = _days(max(WINDOWS))
        if not days:
            return {'available': False,
                    'reason': 'analytics.score 里一场都没有 ⇒ 谈不上各方案的得分与名次'}
        recent = days[-n:]
        keys = [s['key'] for s in SERIES_SPEC if s['family'] == 'score']
        frames = _fetch(sec, days[0], recent, keys)
        names, names_src = _capability_names()
        sr_of = {d: i + 1 for i, d in enumerate(days)}
        dist_of = {(r['d'], r['k']): {'universe': _int(r.get('uni')),
                                      'distinct_values': _int(r.get('dv')),
                                      'biggest_tie': _int(r.get('biggest_tie')),
                                      'tie_at_value': None} for r in frames['dist']}
        tie_of = {(r['d'], r['k']): _int(r.get('tie_at')) for r in frames['tie']}
        series: list[dict] = []
        for spec in SERIES_SPEC:
            kap = spec['key']
            idx = _indexed(spec, frames)
            cells = []
            for d in recent:
                sp = None
                if spec['family'] == 'score':
                    sp = dist_of.get((d, kap))
                    if sp is not None:
                        sp = dict(sp)
                        sp['tie_at_value'] = tie_of.get((d, kap))
                cells.append(_cell(spec, kap, d, idx.get(d), sp))
            seq = []
            for d in days:
                c = _cell(spec, kap, d, idx.get(d), None)
                if c.get('state') != STATE_OK or c.get('value') is None:
                    continue
                v = float(c['value'])
                y = -v if spec['direction'] == 'lower_better' else v
                seq.append((float(sr_of[d]), y, d))
            trend = [window_stats(seq, w) for w in WINDOWS]
            by_w = {t['window']: t for t in trend}
            carry = []
            for i, sw in enumerate(WINDOWS):
                for lw in WINDOWS[i + 1:]:
                    prev = window_stats(seq, lw, shift=sw)
                    carry.append({'short': sw, 'long': lw, 'adjacent': lw == WINDOWS[i + 1],
                                  'long_prev_from': prev.get('from'), 'long_prev_to': prev.get('to'),
                                  **carry_state(by_w[sw], by_w[lw], prev if prev['usable'] else None)})
            label = (((names.get(kap) or {}).get('name') or kap) if spec['family'] == 'score'
                     else spec['label'])
            deltas, prev_rank = [], None
            for c in cells:
                if c.get('state') != STATE_OK:
                    # 中间断一场就断开链条：下一格不念「比上一场动了几名」，那是隔了两场的差
                    deltas.append({'date': c['date'], 'rank': None, 'delta': None,
                                   'gap': True})
                    prev_rank = None
                    continue
                rk = c.get('rank')
                deltas.append({'date': c['date'], 'rank': rk, 'delta': rank_delta(prev_rank, rk)})
                prev_rank = rk
            series.append({'key': kap, 'label': label, 'family': spec['family'],
                           'family_label': FAMILY_LABEL[spec['family']], 'unit': spec['unit'],
                           'direction': spec['direction'], 'says': spec['says'],
                           'capability_note': (names.get(kap) or {}).get('description')
                           if spec['family'] == 'score' else None,
                           'cells': cells, 'trend': trend, 'carry': carry, 'rank_deltas': deltas,
                           'points_available': len(seq), 'calendar_days': len(days),
                           'calendar_from': days[0], 'calendar_to': days[-1]})
        state_counts = {st: sum(1 for s in series for c in s['cells'] if c['state'] == st)
                        for st in (STATE_OK, STATE_NOT_SELECTED, STATE_NO_VALUE,
                                   STATE_OUT_OF_CALENDAR)}
        return {'available': True, 'code': sec, 'as_of': recent[-1], 'sessions': n,
                'series': series, 'windows': list(WINDOWS),
                'state_counts': state_counts, 'cell_states': dict(STATE_SAYS),
                'sessions_default': SESSIONS_DEFAULT, 'sessions_min': SESSIONS_MIN,
                'sessions_max': SESSIONS_MAX, 'spec_version': SPEC_VERSION,
                'min_points_rule': 'min_points(w)：w ≤ 2 时是 2（两点只交方向，本就没有残差），'
                                   '其余 max(3, ⌈w/3⌉)。只住 duanxian/scheme_radar.py 一处',
                'thresholds': {'z_visible': Z_VISIBLE, 'z_hint': Z_HINT, 'flat_band': FLAT_BAND,
                               'turns_max': TURNS_MAX,
                               'unit': '长期斜率的标准误（se）——不是分数，也不是百分点'},
                'columns': list(COLUMNS), 'rules': dict(RULES), 'guide': list(GUIDE),
                'carry_states': dict(CARRY_LABEL),
                'definitions': dict(DEFINITIONS),
                'names_source': names_src,
                'calendar': {'sessions': len(days), 'from': days[0], 'to': days[-1]},
                'coverage': _coverage(('score', 'xy_rank_daily', 'volume_double', 'ambush_setup')),
                'caveats': _caveats(series, frames, recent, days)}

    # 命中与否不能问缓存「这一发是新算的还是旧的」——ttlcache 没这个说法，只能夹一次前后状态：
    # 取数前那条记录本来就是 ok、且时间戳没挪 ⇒ 这一发没有出网，交的是缓存里那一份。
    before = ttlcache.status(key)
    data = ttlcache.cached(key, build, valid=lambda r: bool(r.get('available')), ttl=TTL)
    if not data.get('available'):
        return data
    st = ttlcache.status(key)
    saved = st.get('at')
    hit = bool(before.get('ok')) and before.get('at') == saved
    age = None
    if saved:
        age = max(0, int((dt.datetime.now(dt.timezone.utc)
                          - dt.datetime.fromisoformat(saved)).total_seconds()))
    d = dict(data)
    d['generated_at'] = saved
    d['cached'] = hit
    d['cache_age_seconds'] = age
    d['cache_key'] = key
    d['cache_ttl'] = TTL
    d['elapsed_ms'] = int((time.monotonic() - t0) * 1000)
    d['source'] = ('analytics.score + analytics.xy_rank_daily + analytics.volume_double'
                   ' + analytics.ambush_setup')
    return d
