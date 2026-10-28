# -*- coding: utf-8 -*-
"""「风险提示」的**读数层**（用户 2026-10-20 夜点名，放在首页「盘面数据」下面）。

原话："用折线图展示这两个指标……一个是市场宽度（全市场的涨跌家数），一个是阿姆氏指标
（上涨票的成交额 ÷ 下跌票的成交额，这个是看量的）。同时显示这两个指标的区间背离情况，
背离越大越危险。备注：我用资金追踪判断大资金/聪明钱怎么走，同时关注量价关系；
纯技术分析不看量能胜率有限。"

这一层**只读 `analytics.market_breadth`**（写侧与列定义只住 `app/compute/breadth.py`，
日线行由加工链的 `breadth`/d6 步骤落库；`up_amount`/`down_amount`/`arms_ratio`
三列的回填归写侧跑 `python -m app.compute.breadth --backfill`）。这里不写库、不建列。

五条写死的纪律（改动前先读）：

1. **阿姆斯读不到 ≠ 阿姆斯是 0**，三种情况分开说（`arms.reason`）：
   库不通 ／ 这一族列还没建 ／ 列在但全库（或本窗口）没有一行回填过。
   后两种一律 `arms = {available:false, reason:…}` 且**不带 `series` 键**，
   reason 里给出能自救的那句命令：`python -m app.compute.breadth --backfill`。
2. **算不出就是 null，界面 —**。窗口内样本不足 20 期 ⇒ 分位/背离/档位一律 null：
   不拿现有几期硬凑、不补 50（补 50 会把"判不了"画成"中性"，那正是这条线最危险的假数）。
3. **周/月是把成员日合计重算，不是把日比值取平均**：
   `arms_ratio = Σup_amount ÷ Σdown_amount`、`up_ratio = Σup_count ÷ Σstocks × 100`；
   家数取期内**日均**并交出 `bars_in_period`。日比值取平均是假数 ——
   一周里"缩量上涨日"与"放量下跌日"各占三天时，日均比值会掩盖量能在哪一边。
4. **参数只认白名单**：月份档 `3/6/12/24/36/60`（10-21 加 36/60，见 `ALLOWED_MONTHS` 那段实测）、
   聚合档 `day/week/month`，非法值退回默认
   并把"收到了什么、退回成什么"写进 `caveats` 出声，绝不静默当成没筛。
   两份列表随 `month_options`/`group_options` 交出 —— **界面按它们渲染按钮，不自己抄一份**。
5. **趋势与转折点只画在站得住的地方**（用户 2026-10-26 点名「这个图看不出趋势和转折点」）：
   背离 `divergence` 逐期独立（全史 lag1~lag10 自相关 0.01~0.03，放大分位窗口到 250 也只到 0.09）
   ⇒ **不在背离条上做均线、不在背离条上标转折点**（那是把噪声的平均画成信号）；
   趋势住在水位线上（`MA10(up_ratio)` lag1 0.90）⇒ 转折点打在平滑后的宽度线上（zigzag，幅度门槛
   `TREND_ZIGZAG_PP`），背离这一族只交出**密度线**（近 `DENSITY_WINDOW` 期里有几期达到中度，
   实测超散 2.50 倍 ⇒ 命中确实成簇，密度是背离唯一站得住的趋势形式）。
   末端未被反向走够门槛确认的极值**一律不交**（未确认的拐点画上去就是事后诸葛）；
   窗口第一期也不交（它没有前置形态），但若它正好是窗口内的最高/最低，改由 `trend.boundary` 说出来。

档位阈值（≥40 高危 / 20~40 中度 / ≤−40 反向 / 其余无明显）**只住本文件这一处**，
随 `thresholds`/`levels` 交出，界面读它、不写死。趋势层的参数（平滑窗口、幅度门槛、密度窗口）
同样只住本文件这一处，随 `trend.smooth`/`trend.zigzag_pp`/`trend.density` 交出。
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

from . import pg

# ------------------------------------------------------------ 唯一的阈值出处（界面读 thresholds）

#: 分位窗口：两条线各自取"近 20 期"滚动样本。不满 20 期 ⇒ null，不硬凑。
PCT_WINDOW = 20
#: 背离 = 宽度分位 − 阿姆斯分位（点），分档线。
DIV_HIGH = 40.0        # ≥ 40：高危
DIV_MID = 20.0         # 20 ~ 40：中度背离
DIV_REVERSE = -40.0    # ≤ -40：反向背离

LEVEL_LABELS = {
    'high': '高危：家数在走强、钱没跟上',
    'moderate': '中度背离',
    'reverse': '反向：钱在进、家数没跟上',
    'none': '无明显背离',
}

#: 图上要标出的「极端点位」判据（用户 2026-10-21 点名：9-21 那种背离日与 24 年 10 月初那种崩点
#: 要能一眼看到）。两类是两件事，各自一条判据、各自成列，不混成一个分数：
#: ① `divergence` 量价背离 —— 上涨票的**家数占比**明显高于它的**成交额占比**（钱没跟家数走）；
#: ② `collapse` 宽度崩塌 —— 一期之内宽度掉下一大截，且上一期本来很高（"权重一松手"那种）。
#: ⚠️ 这两条**都抓不到**旧的「宽度分位 − 阿姆斯分位」那一档：9-21 那天阿姆斯本身也在自己的历史高位，
#:    分位差只有 15（旧尺子判「无明显背离」），而当天家数占比 81.6% 对成交额占比 71.5% 是 +10.1pp。
#:    三把尺各答各的问题，屏上分开摆，谁也不许顶替谁。
EXTREME_THRESHOLDS = {
    'share_gap_pp': 8.0,        # ① 家数占比 − 成交额占比 ≥ 这么多个百分点（10-21 实测全史 p90 = 7.3）
    'width_min_pct': 80.0,      # ① 且宽度 ≥80%：用户说的"80% 的票在涨、看着非常强"就是这一档
    'collapse_drop_pp': 25.0,   # ② 一期之内宽度掉这么多 pp（10-09 那一场是 −88.9）
    'prev_width_min_pct': 80.0,  # ② 且上一期宽度 ≥80%（"权重一松手"要先是真的强）
    'max_marks': 6,             # 每类各标最严重的几个（两类合起来最多 12 个标记，图才不会糊）
}

EXTREME_KIND_LABELS = {
    'divergence': '量价背离（家数在走强、钱没跟上）',
    'collapse': '宽度崩塌（上一期还很宽，这一期直接掉下去）',
}

#: ---------------- 趋势与转折点（用户 2026-10-26 点名「这个图看不出趋势和转折点」）
#: 先量后画的结论，三件都写在 `DEFINITIONS['trend_basis']` 里随接口交出：
#: ① `divergence` 在日档上是**逐期独立**的：全史 1,193 期 lag1~lag10 自相关 0.01~0.03，
#:    逐期符号翻转 132/221，均值 ≈0、标准差 17~19；把分位窗口从 20 放大到 60/120/250，
#:    lag1 也只到 0.07/0.08/0.09 ⇒ **对它做均线得到的"转折点"是平均噪声的产物，不画**。
#: ② 趋势真住在**水位线**里：`MA10(up_ratio)` lag1 自相关 0.90、`MA20` 0.95。
#:    所以转折点标在平滑后的宽度线上，用 zigzag 只记幅度够大的反转。
#: ③ 背离**命中日确实成簇**（不是纯随机）：滚动 20 期里「≥40 高危」期数的观测方差是
#:    独立期望的 2.50 倍（「≥20 中度及以上」那档 1.44 倍）⇒ 「近 20 期里有几期背离」
#:    这条密度线是背离唯一站得住的趋势形式，画出来给"这一段是不是密集危险"用。
TREND_SMOOTH = 10          #: 宽度线的均线窗口（期）
TREND_ZIGZAG_PP = 10.0     #: 只记幅度 ≥ 这么多 pp 的反转：3个月窗口 2 个、12个月 13 个、全史 75 个
DENSITY_WINDOW = 20        #: 背离密度：近这么多期里有几期达到 `DIV_MID`（中度及以上）

#: 月份档（10-21 加 36/60：用户点名要看 2024 年 10 月初那一段，而"今天往回 24 个月"= 2024-10-21，
#: 那一段整场在窗口外 —— 这是窗口档的问题，不是判据的问题。库里 breadth 覆盖 2021-08-02 起
#: （10-21 实测 1,263 场、arms 非空 1,262），60 个月那一档还在覆盖内，再往前就是空窗口，
#: 界面自己会念「窗口内没有行」那句。
ALLOWED_MONTHS = (3, 6, 12, 24, 36, 60)
ALLOWED_GROUPS = ('day', 'week', 'month')

#: 这一族列由写侧（另一个会话）添加/回填；缺哪一种状态，reason 都指向这一句命令。
BACKFILL_CMD = 'python -m app.compute.breadth --backfill'
ARMS_COLS = ('up_amount', 'down_amount', 'arms_ratio')

RULE = ('背离 = 宽度 20 期分位 − 阿姆斯 20 期分位（两条线各自取近 20 期滚动样本的百分位，'
        f'含当期）：≥{DIV_HIGH:g}「{LEVEL_LABELS["high"]}」、'
        f'{DIV_MID:g}~{DIV_HIGH:g}「{LEVEL_LABELS["moderate"]}」、'
        f'≤{DIV_REVERSE:g}「{LEVEL_LABELS["reverse"]}」、其余「{LEVEL_LABELS["none"]}」。'
        '「分位是相对量、不是概率」—— 80 分位只说明「当期读数在近 20 期样本里排在第几」，'
        '不说明明天涨的概率。窗口内样本不足 20 期时分位/背离/档位一律 null（界面 —），'
        '不拿现有几期硬凑、不补 50。')

DEFINITIONS = {
    'width_line': ('宽度线主读 `up_ratio`（上涨家数 ÷ 参与统计只数 × 100，库里日频原值）。'
                   '每个点同时交出 `up_count` / `down_count` / `net`（净家数 = up_count − down_count）'
                   '三个原始数供悬停，「没有第四个指标」。'),
    'arms_line': ('阿姆斯线读 `arms_ratio` = 上涨票成交额合计 ÷ 下跌票成交额合计'
                  '（`up_amount`/`down_amount`，单位元，母体与宽度的 up/down 是同一批票、'
                  '同一判据：`pct_change > 0 / < 0`，先排除 NULL 行）。'
                  '分母为 0 或任一边缺 ⇒ null（0 在这里是真实读数「下跌侧彻底没量」，不能拿它冒充「算不出」）。'),
    'divergence': ('`divergence = 宽度分位 − 阿姆斯分位`；`width_pctile`/`arms_pctile` '
                   '各是在近 20 期滚动样本里「≤当期值的样本占比 × 100」（含自身，所以最低 5、最高 100）。'
                   '背离越大越危险：家数在走强而钱没跟上，就是用户说的「纯技术分析不看量能」要躲的那种走势。'),
    'pctile_relative': '分位是相对量、不是概率 —— 只说当期在近 20 期样本里的位置，不预测涨跌。',
    'week_month': ('周/月：家数取期内日均并交出 `bars_in_period`（成员天数）；'
                   '`up_ratio` 与 `arms_ratio` 用期内合计重算：Σup_count ÷ Σstocks × 100、'
                   'Σup_amount ÷ Σdown_amount —— 不是把日比值取平均（那是假数）。'
                   '任一成员日缺分量 ⇒ 该期整块不硬凑，交 null。'),
    'nulls': ('算不出就是 null，不是 0：分位/背离/档位、周月的缺员期、库里的空 up_ratio 行，'
              '界面上都是 —。序列前 ' + str(PCT_WINDOW - 1) + ' 期样本不足也全是 null。'),
    'thresholds': ('档位阈值只住 `duanxian/risk_signal.py` 一处，随 `thresholds`/`levels` 交出；'
                   '界面读它渲染档位文字，不自己写死数字或重算分档。'
                   '`thresholds.extremes` 那五条是图上极端点位的判据（同一个出处）。'),
    'count_ratio': ('`count_ratio = 上涨家数 ÷ 下跌家数`（周/月那一档用的是期内日均家数，'
                    '与 `up_count`/`down_count` 同一口径）。下跌家数为 0 的全市场普涨日 ⇒ null，'
                    '不写成无限大：那一刻「倍数」没有意义，屏上念 —。'),
    'amount_share_up': ('`amount_share_up = Σ上涨票成交额 ÷ (Σ上涨 + Σ下跌) × 100`，'
                        '就是「上涨的那批票吃掉了全市场多少成交」。优先用分量原值算，'
                        '分量没落库时才从 `arms_ratio` 反推（反推带写侧四舍五入，只做兜底）。'),
    'share_gap': ('`share_gap = up_ratio − amount_share_up`（百分点）—— 用户 10-21 那段分析的机器版本：'
                  '「宽度 81.6%、涨停 102 家，看着很强；可上涨票只吃掉 71.5% 的成交，'
                  '也就是说那 18% 在跌的票占了近三成成交额」⇒ 那天 +10.1pp。'
                  '⚠️ 结构性：全史中位是 −5.6pp（涨的那批平均成交额更大是常态），'
                  '所以正数不等于危险，要看它高过常态多少 —— 判据用 ≥8pp（实测 p90 = 7.3）而不是「>0」。'
                  '与 `divergence` 是两把尺：那条比的是各自的历史分位，这一条比的是同一天的家数与钱。'),
    'extremes': ('图上标的极端点位，两类各一列、各一条判据（阈值只住 `EXTREME_THRESHOLDS`）：'
                 '① `divergence` 量价背离 = `share_gap ≥ 8pp` 且宽度 ≥ 80%；'
                 '② `collapse` 宽度崩塌 = 上一期宽度 ≥ 80% 而这一期掉下 ≥ 25pp。'
                 '每类按严重程度取前 `max_marks` 个（默认 6），但最近 ' + str(PCT_WINDOW) + ' 期内的命中一律保留 —— '
                 '这块屏是天天读的，昨天刚命中的点不该被去年更大的那一次挤掉。'
                 '命中多少、被上限挤掉多少都交回、屏上一念 —— 不然 24 个月那一档几十个命中只看到几个，'
                 '会被读成「就这么点危险」。缺成交额占比的期数另记 `skipped_no_gap`（判不了 ≠ 没命中）。'),
    'trend': ('趋势与转折点标在「平滑后的宽度线」上，不标在背离条上（参数只住本文件：'
              f'均线 {TREND_SMOOTH} 期、zigzag 幅度 ≥{TREND_ZIGZAG_PP:g}pp）。'
              'zigzag 的口径是「只记幅度够大的反转」：从上一个极值起，反向走够 '
              f'{TREND_ZIGZAG_PP:g}pp 才承认那是转折点；末端还没被反向确认的那个极值不交'
              '（否则最后一根抖动会被当成拐点），窗口第一期也不交（它没有左侧，只是窗口边界 —— '
              '但它恰好是本窗口平滑值的最高/最低点时，`trend.boundary` 会把这件事单独交出去念）。'
              '实测转折点密度：3 个月窗口 2 个、12 个月 13 个（≈13/年）、全史 5 年 75 个（≈15/年）。'),
    'trend_basis': ('为什么转折点不画在背离条上（2026-10-26 实测，全史 1,193 期）：'
                    '背离是逐期独立的 —— 今天这一期与昨天／前几天那一期几乎不相关'
                    '（相隔 1~10 期的相关系数只有 0.01~0.03），'
                    '相邻两期的正负号翻转 132/221 次，均值 ≈0、标准差 17~19；'
                    '把分位窗口从 20 期放大到 60/120/250 期，相邻一期的相关也只到 0.07/0.08/0.09，'
                    '还是噪声 ⇒ 对它做均线得到的「转折点」是把噪声平均出来的，不是状态转换，所以不画。'
                    '趋势住在水位线里：10 期均线的宽度与它前一期的相关 0.90、20 期均线 0.95。'),
    'risk_density': (f'`risk_density` = 近 {DENSITY_WINDOW} 期里背离达到「中度及以上」'
                     f'（≥{DIV_MID:g}）的期数，窗口内判得出背离的期数不满 {DENSITY_WINDOW} ⇒ null（界面 —）。'
                     '这一条是背离唯一站得住的趋势形式：命中日在时间上确实成簇 —— '
                     '滚动 20 期里「≥40 高危」期数的观测方差是「各期独立」期望值的 2.50 倍，'
                     '「≥20 中度及以上」那档 1.44 倍（全史 1,212 期实测）。'
                     '读法是「这一段密集不密集」，不是「明天会不会跌」。'),
    'divergence_scale': ('背离条的纵轴按本窗口实际量程自适应（不再固定 ±100）：'
                         '实测 3 个月窗口只有 −40~+45、全史 −50~+60，而固定 ±100 的映射'
                         '会把条子压到半高中位 2.1 像素（46 像素高的条带里），看上去就是一条平线 —— '
                         '这是「看不出趋势」的头一个原因。阈值线仍按 `thresholds` 画，轴一定罩住阈值。'),
    'scope': ('这是市场层面的量价形状读数：不打分、不预测、不给买卖建议。'
              '「高危」说的是家数与量能的背离程度，不宣称「明天必跌」。'),
}


# ------------------------------------------------------------ 出口清洗（与 allocator_view 同一道闸）

def _num(v: Any, nd: int | None = None) -> Any:
    """库里出来的值统一成 JSON 安全的形式；**NaN/NaT 必须变成 None**。

    `pandas.isna` 的闸排在日期判断**之前** —— `pd.NaT` 是 datetime 的实例，
    顺序反了就会带着 NaT 进 JSON，`json.dumps` 写出裸 `NaT`，浏览器 `JSON.parse` 炸。
    """
    if v is None:
        return None
    if isinstance(v, str):
        return v
    import pandas

    try:
        if pandas.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (dt.date, dt.datetime)):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f or f in (float('inf'), float('-inf')):   # NaN / Infinity 都不许上屏
        return None
    return round(f, nd) if nd is not None else f


def _rows(sql: str, params=()) -> list[dict]:
    df = pg.fetch_df(sql, params)
    return [] if df is None else [
        {k: _num(v) for k, v in row.items()} for row in df.to_dict('records')]


# ------------------------------------------------------------ 纯判据（不碰库，单测直接打这些）

def _valid(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def percentile_of(samples: list, v: Any) -> float | None:
    """当期值在样本里的百分位（≤ 当期值的样本数 ÷ 样本数 × 100，含自身）。

    样本不足 `PCT_WINDOW` 期 ⇒ **None**（判不了就是判不了，不许用几期硬凑、不许补 50）。
    """
    vals = [float(x) for x in samples if _valid(x)]
    if len(vals) < PCT_WINDOW or not _valid(v):
        return None
    v = float(v)
    return round(100.0 * sum(1 for x in vals if x <= v) / len(vals), 1)


def trailing_pctiles(values: list) -> list:
    """逐期的"近 20 期滚动分位"：第 i 期取 values[max(0,i-19) .. i]（含当期）。

    切片里只要有效值凑不满 20 个（含序列本身不足 20 期、或成员行有空值），这一期就是 None。
    """
    out = []
    for i, v in enumerate(values):
        out.append(percentile_of(values[max(0, i - (PCT_WINDOW - 1)): i + 1], v))
    return out


def divergence_band(div: Any) -> tuple[str | None, str | None]:
    """背离值 → (档位键, 档位文字)。None ⇒ (None, None)：界面 —，不落到"无明显背离"。"""
    if not _valid(div):
        return None, None
    div = float(div)
    if div >= DIV_HIGH:
        key = 'high'
    elif div >= DIV_MID:
        key = 'moderate'
    elif div <= DIV_REVERSE:
        key = 'reverse'
    else:
        key = 'none'
    return key, LEVEL_LABELS[key]


def moving_average(values: list, k: int) -> list:
    """逐期的近 `k` 期均值（含当期）；窗口内有效值不满 `k` ⇒ None（不拿几期硬凑）。

    与 `trailing_pctiles` 同一口径：缺数就是判不了，不用现有几期顶替。
    """
    out = []
    for i in range(len(values)):
        win = [float(x) for x in values[max(0, i - (k - 1)): i + 1] if _valid(x)]
        out.append(round(sum(win) / len(win), 2) if len(win) >= k else None)
    return out


def zigzag_turns(values: list, th: float) -> list[dict]:
    """只记幅度 ≥ `th` 的反转（zigzag），返回 [{'i', 'value', 'kind': 'peak'|'trough'}]。

    末端那个还没被反向走够 `th` 确认的极值**不交** —— 交了就等于把最后一根抖动当成拐点。
    窗口第一期同样不交：它没有左侧，只是窗口边界，交出去会被读成「这里拐过」。

    ⚠️ 方向未定时 `hi`/`lo` 必须分开追：第一版共用一个极值变量，方向没定下来之前
    那个变量会跟着每一个点跑，结果阈值一大就整段返回空
    （2026-10-26 实测：MA10 宽度在 12 个月窗口上 th=5pp 报 34 个转折点、th=10pp 报 0 个 ——
    而那段极差有 31pp，0 个显然是假的）。`tests/test_risk_signal.py` 里钉了这一条。
    """
    pts = [(i, float(x)) for i, x in enumerate(values) if _valid(x)]
    if len(pts) < 3 or not _valid(th) or float(th) <= 0:
        return []
    th = float(th)
    out: list[dict] = []
    direction = 0                                  # 0=未定 1=向上找顶 -1=向下找底
    hi_i, hi_v = pts[0]
    lo_i, lo_v = pts[0]
    ex_i, ex_v = pts[0]
    for i, x in pts[1:]:
        if direction == 0:
            if x > hi_v:
                hi_i, hi_v = i, x
            if x < lo_v:
                lo_i, lo_v = i, x
            if hi_v - x >= th:
                out.append({'i': hi_i, 'value': round(hi_v, 2), 'kind': 'peak'})
                direction, ex_i, ex_v = -1, i, x
            elif x - lo_v >= th:
                out.append({'i': lo_i, 'value': round(lo_v, 2), 'kind': 'trough'})
                direction, ex_i, ex_v = 1, i, x
        elif direction == 1:
            if x > ex_v:
                ex_i, ex_v = i, x
            elif ex_v - x >= th:
                out.append({'i': ex_i, 'value': round(ex_v, 2), 'kind': 'peak'})
                direction, ex_i, ex_v = -1, i, x
        else:
            if x < ex_v:
                ex_i, ex_v = i, x
            elif x - ex_v >= th:
                out.append({'i': ex_i, 'value': round(ex_v, 2), 'kind': 'trough'})
                direction, ex_i, ex_v = 1, i, x
    # 窗口第一期不是转折点：它没有左侧，只是窗口边界（不删就会被读成"这里拐过"）
    first_i = pts[0][0]
    return [t for t in out if t['i'] != first_i]


def hit_density(values: list, th: float, k: int) -> list:
    """逐期的「近 `k` 期里有几期 ≥ `th`」；窗口内有效值不满 `k` ⇒ None。

    这是把一个逐期独立的噪声序列换成它的**发生密度**：单期背离没有趋势可言
    （见 `DEFINITIONS['trend_basis']` 的自相关实测），但命中日在时间上成簇，
    密度线才读得出「这一段是不是密集危险」。
    """
    out = []
    for i in range(len(values)):
        win = [float(x) for x in values[max(0, i - (k - 1)): i + 1] if _valid(x)]
        out.append(sum(1 for x in win if x >= float(th)) if len(win) >= k else None)
    return out


def attach_trend(periods: list[dict]) -> list[dict]:
    """就地挂上趋势两列：平滑宽度 `up_ratio_ma` 与背离密度 `risk_density`。

    必须在 `attach_divergence` 之后调用（密度读的是 `divergence`）。
    """
    ma = moving_average([p.get('up_ratio') for p in periods], TREND_SMOOTH)
    dens = hit_density([p.get('divergence') for p in periods], DIV_MID, DENSITY_WINDOW)
    for p, m, dn in zip(periods, ma, dens):
        p['up_ratio_ma'] = m
        p['risk_density'] = dn
    return periods


def find_turns(periods: list[dict]) -> dict:
    """平滑宽度线上的转折点清单（判据与参数只住本文件，界面只按 `period` 找位置画）。

    `swing_pp` 是与上一个转折点之间的落差（第一个转折点没有上一个 ⇒ null，不写 0）；
    `raw_up_ratio` 是那一期的原始宽度，供清单里对照「平滑值 vs 当期值」。

    `boundary`：窗口第一期被 zigzag 排除（它没有左侧），但**它恰好是本窗口平滑值的
    最高/最低点**时把这件事交出去 —— 否则默认 3 个月那一档会出现「图上最高点没有标记、
    也没人解释」的读法（实测那一档只剩 2 个转折点，被排掉的那个正是 08-07 的峰）。
    """
    ma = [p.get('up_ratio_ma') for p in periods]
    turns = zigzag_turns(ma, TREND_ZIGZAG_PP)
    rows = []
    prev_val = None
    for t in turns:
        p = periods[t['i']]
        rows.append({
            'period': p['period'], 'label': p['label'], 'kind': t['kind'],
            'ma_value': t['value'], 'raw_up_ratio': p.get('up_ratio'),
            'risk_density': p.get('risk_density'),
            'swing_pp': (round(abs(t['value'] - prev_val), 2) if prev_val is not None else None),
        })
        prev_val = t['value']

    boundary = None
    valid = [(i, float(v)) for i, v in enumerate(ma) if _valid(v)]
    if len(valid) >= 3:
        (bi, bv), (_, vmax), (_, vmin) = valid[0], max(valid, key=lambda t: t[1]), min(valid, key=lambda t: t[1])
        if bv == vmax or bv == vmin:
            p = periods[bi]
            boundary = {'period': p['period'], 'label': p['label'],
                        'kind': 'peak' if bv == vmax else 'trough',
                        'ma_value': round(bv, 2), 'raw_up_ratio': p.get('up_ratio')}

    return {'smooth': TREND_SMOOTH, 'zigzag_pp': TREND_ZIGZAG_PP, 'rows': rows,
            'boundary': boundary}


def month_start(end: dt.date, months: int) -> dt.date:
    """`end` 往前推 `months` 个日历月（日序按目标月月末钳制）。"""
    total = end.year * 12 + (end.month - 1) - months
    y, m = divmod(total, 12)
    m += 1
    last_day = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28,
                31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return dt.date(y, m, min(end.day, last_day))


def bucket_of(day: dt.date, group: str) -> tuple[str, str]:
    """(period 唯一键, 展示 label)。周=ISO 周、月=自然月，**库里没有周期行，全是读侧重算**。"""
    if group == 'week':
        iy, iw, _ = day.isocalendar()
        p = f'{iy}-W{iw:02d}'
        return p, p
    if group == 'month':
        p = f'{day.year}-{day.month:02d}'
        return p, p
    return day.isoformat(), day.isoformat()[5:]


def aggregate_periods(daily: list[dict], group: str) -> list[dict]:
    """日行 → 周/月期行。**家数取日均；比值用合计重算**（口径见模块头第 3 条）。

    `group == 'day'` 时一行一期，直接取库里的日频原值（`up_ratio`/`arms_ratio` 是写侧算好的，
    读侧不越权重算），但同样交出 `bars_in_period = 1` 与净家数。
    """
    if group == 'day':
        out = []
        for r in daily:
            up, down = r.get('up_count'), r.get('down_count')
            out.append({
                'period': r['trade_date'],
                'label': str(r['trade_date'])[5:],
                'bars_in_period': 1,
                'up_count': up, 'down_count': down,
                'limit_up_count': r.get('limit_up_count'),
                'net': (up - down) if (_valid(up) and _valid(down)) else None,
                'up_ratio': r.get('up_ratio'),
                'up_amount': r.get('up_amount'), 'down_amount': r.get('down_amount'),
                'arms_ratio': r.get('arms_ratio'),
            })
        return out

    buckets: dict[str, dict] = {}
    order: list[str] = []
    for r in daily:
        day = dt.date.fromisoformat(str(r['trade_date'])[:10])
        p, label = bucket_of(day, group)
        if p not in buckets:
            buckets[p] = {'period': p, 'label': label, 'members': []}
            order.append(p)
        buckets[p]['members'].append(r)

    out = []
    for p in order:
        members = buckets[p]['members']
        ups = [m['up_count'] for m in members if _valid(m.get('up_count'))]
        downs = [m['down_count'] for m in members if _valid(m.get('down_count'))]
        # up_ratio 的合计重算要求**每一员**都有 up_count 与 stocks —— 缺一员就不硬凑
        ratio_ok = all(_valid(m.get('up_count')) and _valid(m.get('stocks')) for m in members)
        sum_up = sum(float(m['up_count']) for m in members if ratio_ok)
        sum_stocks = sum(float(m['stocks']) for m in members if ratio_ok)
        # 周期阿姆斯：Σup_amount ÷ Σdown_amount；任一成员日缺任一分量 ⇒ 合计不是真的期合计 ⇒ null
        amt_ok = all(_valid(m.get('up_amount')) and _valid(m.get('down_amount')) for m in members)
        sum_up_amt = sum(float(m['up_amount']) for m in members) if amt_ok else None
        sum_down_amt = sum(float(m['down_amount']) for m in members) if amt_ok else None
        up_avg = round(sum(ups) / len(ups), 1) if ups else None
        down_avg = round(sum(downs) / len(downs), 1) if downs else None
        lus = [m['limit_up_count'] for m in members if _valid(m.get('limit_up_count'))]
        lu_avg = round(sum(lus) / len(lus), 1) if lus else None
        out.append({
            'period': p, 'label': buckets[p]['label'], 'bars_in_period': len(members),
            'up_count': up_avg, 'down_count': down_avg,
            # 周/月那一档的涨停家数是**期内日均**（与上面家数同一口径），不是期内合计 —— 
            # 屏上要说清，不然"一周 500 家涨停"会被读成一週累计
            'limit_up_count': lu_avg, 'limit_up_days': len(lus),
            'net': (round(up_avg - down_avg, 1)
                    if up_avg is not None and down_avg is not None else None),
            'up_ratio': round(100.0 * sum_up / sum_stocks, 2)
                        if ratio_ok and sum_stocks > 0 else None,
            'up_amount': round(sum_up_amt, 2) if sum_up_amt is not None else None,
            'down_amount': round(sum_down_amt, 2) if sum_down_amt is not None else None,
            'arms_ratio': (round(sum_up_amt / sum_down_amt, 6)
                           if amt_ok and sum_down_amt is not None and sum_down_amt > 0 else None),
        })
    return out


def attach_divergence(periods: list[dict]) -> list[dict]:
    """就地给每一期挂 宽度分位 / 阿姆斯分位 / 背离 / 档位。缺任何一侧 ⇒ 三个都 null。"""
    wp = trailing_pctiles([p.get('up_ratio') for p in periods])
    ap = trailing_pctiles([p.get('arms_ratio') for p in periods])
    for p, w, a in zip(periods, wp, ap):
        p['width_pctile'] = w
        p['arms_pctile'] = a
        p['divergence'] = round(w - a, 1) if (w is not None and a is not None) else None
        p['level'], p['level_label'] = divergence_band(p['divergence'])
    return periods


def _ratio_pair(point: dict) -> tuple:
    """(上涨家数÷下跌家数, 上涨票成交额占比%) —— 两个都算不出就是 (None, None)。

    成交额占比优先用**分量原值**（`up_amount/(up_amount+down_amount)`），只有分量没落库、
    但有 `arms_ratio` 时才从比值反推 —— 反推会把写侧的四舍五入带进来，能做兜底不能当首选。
    下跌那一侧家数为 0（全站涨停那种日子）时家数比是 null，**不写成无限大**。
    """
    up, down = point.get('up_count'), point.get('down_count')
    cr = round(float(up) / float(down), 3) if (_valid(up) and _valid(down) and float(down) > 0) else None
    ua, da = point.get('up_amount'), point.get('down_amount')
    if _valid(ua) and _valid(da) and (float(ua) + float(da)) > 0:
        return cr, round(100.0 * float(ua) / (float(ua) + float(da)), 2)
    ar = point.get('arms_ratio')
    if _valid(ar) and float(ar) >= 0:
        return cr, round(100.0 * float(ar) / (1.0 + float(ar)), 2)
    return cr, None


def attach_ratio_view(periods: list[dict]) -> list[dict]:
    """就地挂上「当日同一把尺的两个占比」：家数比、成交额占比、以及两者之差（pp）。

    `share_gap = up_ratio − amount_share_up`：**正值＝上涨的票在家数上占优、在钱上不占优**，
    就是用户那句话的机器版本（9-21：家数 81.62% 而成交额只 71.53% ⇒ +10.09pp，
    「只有 17.6% 的票在跌，可这批票的成交额占了全市场近三成」）。
    全史中位是 **−5.6pp**（涨的票平均更大是常态），所以这一格要和 0、和窗口分布一起读，
    不是"正数就是危险"。缺任何一侧 ⇒ null，界面 —。
    """
    for p in periods:
        cr, share = _ratio_pair(p)
        p['count_ratio'] = cr
        p['amount_share_up'] = share
        p['share_gap'] = (round(float(p['up_ratio']) - share, 2)
                          if (_valid(p.get('up_ratio')) and share is not None) else None)
    return periods


def find_extremes(periods: list[dict]) -> dict:
    """窗口内的极端点位，两类各一列（判据与阈值只住 `EXTREME_THRESHOLDS` 一处）。

    不做的事：不把两类合成一个分数、不因为某期缺阿姆斯就把"缺数"排进极端、
    也不悄悄截断 —— 每类命中多少、这里列多少、被上限挤掉多少，全部交回给界面念。
    """
    th = EXTREME_THRESHOLDS
    div, col = [], []
    skipped_no_arms = 0
    prev = None
    for p in periods:
        if p.get('up_ratio') is None:
            continue
        if p.get('share_gap') is None:
            skipped_no_arms += 1
        elif (float(p['share_gap']) >= th['share_gap_pp']
              and float(p['up_ratio']) >= th['width_min_pct']):
            div.append({
                'period': p['period'], 'label': p['label'], 'kind': 'divergence',
                'width_pct': p['up_ratio'], 'amount_share_up': p['amount_share_up'],
                'share_gap': p['share_gap'], 'count_ratio': p['count_ratio'],
                'arms_ratio': p.get('arms_ratio'), 'limit_up_count': p.get('limit_up_count'),
                'severity': round(float(p['share_gap']), 2),
            })
        if prev is not None and _valid(prev.get('up_ratio')):
            drop = round(float(prev['up_ratio']) - float(p['up_ratio']), 2)
            if (drop >= th['collapse_drop_pp']
                    and float(prev['up_ratio']) >= th['prev_width_min_pct']):
                col.append({
                    'period': p['period'], 'label': p['label'], 'kind': 'collapse',
                    'width_pct': p['up_ratio'], 'prev_period': prev['period'],
                    'prev_label': prev['label'], 'prev_width_pct': prev['up_ratio'],
                    'drop_pp': drop, 'amount_share_up': p.get('amount_share_up'),
                    'share_gap': p.get('share_gap'), 'prev_share_gap': prev.get('share_gap'),
                    'limit_up_count': p.get('limit_up_count'),
                    'severity': drop,
                })
        prev = p

    cap = th['max_marks']
    # 最近 PCT_WINDOW 期内的命中一律保留：这块屏是天天读的，"昨天刚命中"不该被去年更大的那一次挤掉
    recent = {str(p['period']) for p in periods[-PCT_WINDOW:]}

    def _top(rows: list[dict], key: str) -> dict:
        ordered = sorted(rows, key=lambda r: -float(r[key]))
        kept = [r for i, r in enumerate(ordered) if i < cap or str(r['period']) in recent]
        return {'matched': len(ordered), 'dropped_by_cap': max(0, len(ordered) - len(kept)),
                'rows': kept}

    return {
        'divergence': _top(div, 'severity'),
        'collapse': _top(col, 'severity'),
        'thresholds': dict(th),
        'kind_labels': dict(EXTREME_KIND_LABELS),
        # 缺阿姆斯（列没建/没回填）的期数：判据①对它们判不了，必须报出来，
        # 不然"图上只有两个点"会被读成"这段窗口就这么点危险"。
        'skipped_no_gap': skipped_no_arms,
        'periods_in_window': len(periods),
    }


# ------------------------------------------------------------ 参数白名单（非法值出声退回）

def _norm_months(raw: Any) -> tuple[int, str | None]:
    try:
        m = int(raw)      # type: ignore[arg-type]
    except (TypeError, ValueError):
        m = None
    if m in ALLOWED_MONTHS:
        return m, None
    return 3, (f'received months={raw!r}：只认 {list(ALLOWED_MONTHS)}，已退回 3 个月展示 '
               '—— 没有把你给的值当成「不筛窗口」')


def _norm_group(raw: Any) -> tuple[str, str | None]:
    g = raw.strip().lower() if isinstance(raw, str) else raw
    if g in ALLOWED_GROUPS:
        return g, None
    return 'day', (f'received group={raw!r}：只认 {list(ALLOWED_GROUPS)}，已退回按日聚合 '
                   '—— 没有把你给的值当成「没筛」')


def _norm_date(raw: Any) -> tuple[dt.date | None, str | None]:
    if raw in (None, ''):
        return None, None
    try:
        return dt.date.fromisoformat(str(raw)[:10]), None
    except ValueError:
        return None, (f'received date={raw!r} 不是 YYYY-MM-DD，已退回用库里最近一场作窗口右端 '
                      '—— 没有静默当成「查今天」')


# ------------------------------------------------------------ 取数（碰库的就这一族函数）

def _coverage() -> dict | None:
    rows = _rows('select count(*) n, min(trade_date)::text lo, max(trade_date)::text hi '
                 'from analytics.market_breadth')
    return rows[0] if rows else None


def _arms_columns() -> set:
    return {r['column_name'] for r in _rows(
        "select column_name from information_schema.columns "
        "where table_schema = 'analytics' and table_name = 'market_breadth'")} & set(ARMS_COLS)


def _arms_global() -> dict | None:
    rows = _rows('select count(arms_ratio) n_arms, count(up_amount) n_up_amt, '
                 'count(down_amount) n_down_amt, '
                 'max(case when arms_ratio is not null then trade_date end)::text last_arms '
                 'from analytics.market_breadth')
    return rows[0] if rows else None


# ------------------------------------------------------------ 页面读数

def panel(months: Any = 3, group: Any = 'day', date: Any = '') -> dict:
    """首页「风险提示」一屏的全部读数。

    · `months` 只认 3/6/12/24，`group` 只认 day/week/month —— 非法值退回默认并写进 caveats；
    · 库不通 / 窗口内没有 breadth 行 ⇒ `{available:false, reason}`，**不带 `series` 键**；
    · 阿姆斯那一族列没建 / 没回填 ⇒ 宽度线照常出，`arms` 交 `{available:false, reason}`
      （同样**不带 `series` 键**），reason 指向 `python -m app.compute.breadth --backfill`。
    """
    caveats: list[dict] = []
    m, why = _norm_months(months)
    if why:
        caveats.append({'code': 'param_months', 'text': why})
    g, why = _norm_group(group)
    if why:
        caveats.append({'code': 'param_group', 'text': why})
    end_req, why = _norm_date(date)
    if why:
        caveats.append({'code': 'param_date', 'text': why})

    if not pg.enabled():
        return {'available': False,
                'reason': '本地库连不上（VIBE_PG / PG_USER / PG_PASSWORD 没配或没起来）—— '
                          '这是「库不通」，不是「市场没有宽度数据」。'}

    cov = _coverage()
    if cov is None:
        return {'available': False,
                'reason': '本地库读不到（查询失败）—— 这是「库不通」，不是「没有 breadth 行」。'}
    if not cov.get('n'):
        return {'available': False,
                'reason': '`analytics.market_breadth` 里还没有任何一行 —— 加工链的 `breadth`（d6）步'
                          f'还没跑过。先跑：`{BACKFILL_CMD}`。'}

    hi = dt.date.fromisoformat(str(cov['hi'])[:10])
    base = end_req or hi
    lo = month_start(base, m)

    cols = _arms_columns()
    arms_cols_present = cols >= set(ARMS_COLS)
    arms_extra = ', up_amount, down_amount, arms_ratio' if arms_cols_present else ''
    rows = _rows('select trade_date::text as trade_date, stocks, up_count, down_count, up_ratio, '
                 'limit_up_count'
                 + arms_extra +
                 ' from analytics.market_breadth '
                 'where trade_date >= %s::date and trade_date <= %s::date order by trade_date',
                 (lo.isoformat(), base.isoformat()))
    if not rows:
        return {'available': False, 'date': base.isoformat(), 'months': m, 'group': g,
                'reason': f'窗口 {lo} ~ {base} 内没有 breadth 行；库里最近一场是 {cov["hi"]} —— '
                          f'差的场次没算过，补 `{BACKFILL_CMD}`（近 {PCT_WINDOW} 期分位要有样本底）。'}

    lo_actual, hi_actual = rows[0]['trade_date'], rows[-1]['trade_date']

    # 先聚合成展示序列（day 就是一行一期），宽与阿的线、背离、档位都挂在同一批期行上；
    # 再挂趋势两列（平滑宽度 + 背离密度，密度要读背离 ⇒ 排在 attach_divergence 之后），
    # 接着挂「当日家数占比 vs 成交额占比」那一把尺，最后按它筛极端点位（判据只住本文件）。
    series = attach_ratio_view(attach_trend(attach_divergence(aggregate_periods(rows, g))))
    extremes = find_extremes(series)
    trend = find_turns(series)

    # ---------------- 阿姆斯那一族列的三态（列没建 / 全库没回填 / 本窗口没铺到）
    arms: dict
    if not arms_cols_present:
        arms = {'available': False,
                'reason': f'`analytics.market_breadth` 里还没有 {"/".join(f"`{c}`" for c in ARMS_COLS)} '
                          f'这一族列（写侧尚未建列）—— 阿姆斯线算不出来。跑 `{BACKFILL_CMD}` '
                          '（建列 + 全史回填），完成后这条线与背离档位会自己长出来。'}
    else:
        glob = _arms_global() or {}
        in_win = sum(1 for r in rows if _valid(r.get('arms_ratio'))
                     or _valid(r.get('up_amount')) or _valid(r.get('down_amount')))
        if not glob.get('n_arms') and not glob.get('n_up_amt') and not glob.get('n_down_amt'):
            arms = {'available': False,
                    'reason': f'{"/".join(f"`{c}`" for c in ARMS_COLS)} 三列在库里还没有回填过一行'
                              f'（全表非空计数 = 0）—— 这是「还没回填」，不是「市场没有成交」。'
                              f'跑 `{BACKFILL_CMD}` 补全史（分位要 ≥{PCT_WINDOW} 期样本底）。'}
        elif not in_win:
            arms = {'available': False,
                    'reason': f'这一族列全库已有 {int(glob.get("n_arms") or 0)} 行'
                              f'（最近回填到 {glob.get("last_arms") or "—"}），'
                              f'但本窗口 {lo_actual} ~ {hi_actual} 一行都没有 —— 回填还没铺到这段：'
                              f'跑 `{BACKFILL_CMD}`。窗口内阿姆斯与背离按 null（界面 —）处理，不硬凑。'}
        else:
            arms = {'available': True,
                    'series': [{'period': p['period'], 'label': p['label'],
                                'up_amount': p.get('up_amount'),
                                'down_amount': p.get('down_amount'),
                                'arms_ratio': p.get('arms_ratio')} for p in series],
                    'coverage': {'rows_in_window': in_win, 'rows_total': int(glob.get('n_arms') or 0),
                                 'last_backfilled': glob.get('last_arms')}}

    # 阿姆斯不可得时：每一期的 arms_ratio/arms_pctile/divergence/level 在上面天然就是 null
    # （读不到就是读不到），而**宽度线自己的分位照旧有效** —— 它只依赖 up_ratio，不陪绑。

    latest_row = next((p for p in reversed(series) if p.get('divergence') is not None), None)
    latest = None
    if latest_row:
        latest = {k: latest_row[k] for k in
                  ('period', 'label', 'width_pctile', 'arms_pctile', 'divergence',
                   'level', 'level_label', 'up_ratio', 'up_count', 'down_count',
                   'limit_up_count', 'arms_ratio', 'count_ratio', 'amount_share_up',
                   'share_gap') if k in latest_row}
        # 趋势两列取**同一期**的：算不出就是 null，不拿别的期顶上（顶上去就成了假当期读数）
        latest['up_ratio_ma'] = latest_row.get('up_ratio_ma')
        latest['risk_density'] = latest_row.get('risk_density')

    judged = sum(1 for p in series if p.get('divergence') is not None)
    if len(series) < PCT_WINDOW:
        caveats.append({'code': 'pct_window',
                        'text': f'这一窗口聚合后只有 {len(series)} 期，分位要满 {PCT_WINDOW} 期样本 ⇒ '
                                '本窗口内 分位/背离/档位 一律 null（界面 —）—— '
                                '不拿现有几期硬凑、不补 50。换更长窗口或等回填攒底。'})
    else:
        caveats.append({'code': 'pct_warmup',
                        'text': f'序列前 {PCT_WINDOW - 1} 期样本不足 {PCT_WINDOW} ⇒ 那几期分位为 null'
                                f'（界面 —）；本窗口有 {judged} 期算得出背离。'})
    dense = sum(1 for p in series if p.get('risk_density') is not None)
    trend['density'] = {'window': DENSITY_WINDOW, 'threshold': DIV_MID,
                        'computable': dense, 'judged': judged, 'periods': len(series)}
    if not dense:
        # 密度线画不出来 ≠「没有危险」：这一档/这一窗口判得出背离的期数不够 20 期
        caveats.append({'code': 'density_unjudged',
                        'text': f'这一窗口有 {judged} 期算得出背离，而背离密度要满 {DENSITY_WINDOW} 期'
                                f'滚动样本 ⇒ 密度线整条 null（界面 —）。不是「这段没有危险」，'
                                '是「这一档判不了」：按周/按月聚合时一期就是一次样本，'
                                '换按日或拉长窗口就能出密度线。'})
    if not trend['rows']:
        caveats.append({'code': 'turns_none',
                        'text': f'这一窗口在平滑 {TREND_SMOOTH} 期之后没有任何幅度 ≥'
                                f'{TREND_ZIGZAG_PP:g}pp 的反转被确认 ⇒ 图上没有转折点标记。'
                                '两种可能：这一段确实走得平（极差小），或末端那个极值还没被反向走够'
                                f'{TREND_ZIGZAG_PP:g}pp 确认（未确认的拐点一律不画）。'
                                '拉长窗口可以看到更多已确认的转折点。'})
    if g != 'day':
        caveats.append({'code': 'period_recompute', 'hard': True,
                        'text': '周期值是按成员日合计重算的，库里没存周期行 —— '
                                'up_ratio = Σup_count ÷ Σstocks × 100、'
                                'arms_ratio = Σup_amount ÷ Σdown_amount（不是把日比值取平均）；'
                                '家数那一栏是期内日均，`bars_in_period` 给这一期数了几天。'})
    holes = sum(1 for r in rows if r.get('up_ratio') is None)
    if holes:
        caveats.append({'code': 'up_ratio_holes',
                        'text': f'窗口里有 {holes} 行 `up_ratio` 在库里就是空（缺数不是 0）；'
                                f'含空值的近 {PCT_WINDOW} 期样本凑不满 ⇒ 相关期的分位/背离为 null。'})
    if not arms['available']:
        caveats.append({'code': 'arms_missing', 'text': arms['reason']})
    if extremes['skipped_no_gap']:
        # 判不了不能装成"没命中"：这些期没有成交额占比，背离那一类对它们根本没有发言权
        caveats.append({'code': 'extreme_gap_unjudged',
                        'text': f'窗口里有 {extremes["skipped_no_gap"]} 期算不出成交额占比'
                                f'（成交额分量没落库或下跌侧成交额为 0）—— 这些期没被判断，'
                                f'不等于「没背离」。补 `{BACKFILL_CMD}`。'})
    caveats.append({'code': 'not_signal',
                    'text': DEFINITIONS['scope']})

    return {
        'available': True,
        'date': hi_actual,
        'months': m, 'group': g,
        # 参数档也只住本文件一处：界面按这两个列表渲染按钮，不自己抄一份（抄的那份会漂）
        'month_options': list(ALLOWED_MONTHS), 'group_options': list(ALLOWED_GROUPS),
        'window': {'from': lo.isoformat(), 'to': base.isoformat(),
                   'from_actual': str(lo_actual)[:10], 'to_actual': str(hi_actual)[:10]},
        'coverage': {'sessions': int(cov['n'] or 0), 'from': cov['lo'], 'to': cov['hi']},
        'thresholds': {'window': PCT_WINDOW, 'high': DIV_HIGH, 'moderate': DIV_MID,
                       'reverse': DIV_REVERSE, 'extremes': dict(EXTREME_THRESHOLDS)},
        'levels': dict(LEVEL_LABELS),
        'rule': RULE,
        'definitions': dict(DEFINITIONS),
        'arms': arms,
        'series': series,
        'latest': latest,
        'extremes': extremes,
        'trend': trend,
        'generated_at': dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec='seconds'),
        'caveats': caveats,
    }
