# -*- coding: utf-8 -*-
"""个股财务的**多期读法与加权外推**（纯函数层：进series出数，不碰库、不碰网络）。

用户 2026-10-16 的原话：「财务关键指标，这里把近 5 年的都分析一下，季报和年报，营业额和利润，
然后预测各下一季度或者当年，明年的情况，根据之前的加权计算预测一下」。
"加权"这一条我定成这样，写死在本文件一处（`HALFLIFE` / `SEASON_YEARS` / `DIVERGENCE_*`）：

**三条外推，各自都能被反驳，所以三条都交出来而不是合成一个数**

1. **下一季单季**（`forecast_next_quarter`）：
   · A 法＝同比外推：`预测 = 上一年的同一季实际 × (1 + g)`，
     `g` 是**近 4 季单季同比的半衰加权均值**（权重 0.5^k，k=0..3 归一 ⇒ 越近越重），
     并夹在 [-60%, +150%] 内 —— 不夹的话一只去年靠一次性收益涨了 400% 的票会把"明年"推到天上。
   · B 法＝季节环比：`预测 = 上一季实际 × s`，`s` 取**近 3 年"这一季相对上一季"环比的中位数**
     （中位数不是均值：年货季/减值季这种单点异常会把均值带走）。
   · 两法分歧 ≤25% 时以 A 为主、B 作校验；**>25% 时不给"一个数"**，只给两法各自的值 +
     一句"季节性档位与增速档位打架"。分歧这件事本身就是信息，抹平它是假数。
   · 不确定带：近 8 季"|实际同比 − 当季起点预测同比|"的 σ（样本 <6 季 ⇒ 交 None，不给区间）。
2. **当年年报**（`forecast_fiscal_year`）：**已披露的累计原样保留**，只把**还没披露的那几季**
   用第 1 条逐季补上再累加。已经披露到 12-31 的年份不叫预测，`basis='已披露'`。
   ⚠️ 这条是整套里最容易出事的地方：**Q4 在很多票上是"一年减去前三季"**，而 Q4 常年含减值与
   年终费用 ⇒ 用 Q1..Q3 的季节比例外推 Q4 会系统性偏乐观。所以 Q4 一律**单独一档**，
   用近 3 年「Q4 ÷ 前三季均值」的中位数，并在界面上明写这一档更粗。
3. **明年年报**（`forecast_next_year`）：`增速 = 0.4×近5年年报CAGR + 0.6×近3年年报CAGR`
   （偏近期但仍留长期锚），区间用近 5 年年报同比的 25/75 分位夹。

**判不了就是判不了，不降级成 0 或"持平"**：单季不足 8 季 ⇒ 季节性算不了；不足 12 季 ⇒ 连"上一季
同比"的样本都薄；新股上市不满 6 季 ⇒ 三条全交 None，并写 `reason`。这一条是硬规矩：
把"没数据"排成"业绩平平"是本项目犯过最多次的那类错。

ROE 那一列另外处理：累计档的 ROE 是**年内累计**（Q1 的 10% 与全年的 10% 不是一个尺子），
所以逐期表里的 ROE 一律用**单季档**并标「单季口径，不可跨季比绝对水平」。
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SPEC_VERSION = 'finfc-1'

HALFLIFE = 2.0                # 半衰加权：最近的季权重最大
GROWTH_WINDOW = 4             # 用近几季单季同比做加权
GROWTH_CLAMP = (-60.0, 150.0)  # 增速夹住（%），防一次性损益把外推推到天上
SEASON_YEARS = 3              # 季节环比用近几年的同一季
DIVERGENCE_MAX = 25.0         # A/B 两法相对差超过这个百分点 ⇒ 不给单一数
BAND_MIN_SAMPLES = 6
SCENARIO_YEARS = 5            # 明年的情景带：只看近几份年报同比（不是全部可得年报）
Q4_SEASON_RATIO = 4.0 / 3.0   # Q4 单独一档：前三季均值 × 该比例是"正常 Q4"的参照

#: 源里我们真正要看的三条金额（其余留给逐期表原样交）
AMOUNTS = ('revenue', 'net_profit', 'deduct_profit')
_COL = {'revenue': 'revenue_yuan', 'net_profit': 'net_profit_yuan', 'deduct_profit': 'deduct_profit_yuan'}


# ------------------------------------------------------------------ 基础算术（都有三态）

def half_weighted(values: Sequence[float], halflife: float = HALFLIFE) -> Optional[float]:
    """越近越重的加权均值。`values[0]` 是**最近**的一期。空表 None，不返回 0。"""
    xs = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not xs:
        return None
    w = [0.5 ** (i / max(0.1, halflife)) for i in range(len(xs))]
    s = sum(w)
    return sum(x * wi for x, wi in zip(xs, w)) / s if s else None


def yoy(cur: Optional[float], prev_year_same: Optional[float]) -> Optional[float]:
    """同比 %。分母为 0 或任一为空 ⇒ None（不写 0，也不写 -100%）。"""
    if cur is None or prev_year_same is None:
        return None
    if not math.isfinite(float(prev_year_same)) or float(prev_year_same) == 0:
        return None
    return round((float(cur) - float(prev_year_same)) / abs(float(prev_year_same)) * 100.0, 2)


def qoq(cur: Optional[float], prev_quarter: Optional[float]) -> Optional[float]:
    """环比 %。基期为 0（银行/控股这类常有）⇒ None。"""
    return yoy(cur, prev_quarter)


def median(xs: Sequence[float]) -> Optional[float]:
    vals = [float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return round(statistics.median(vals), 4) if vals else None


def pctl(xs: Sequence[float], q: float) -> Optional[float]:
    vals = sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not vals:
        return None
    pos = (len(vals) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return round(vals[lo] if lo == hi else vals[lo] + (vals[hi] - vals[lo]) * (pos - lo), 3)


def cagr(first: Optional[float], last: Optional[float], years: int) -> Optional[float]:
    """年复合增速 %。任一端 ≤0（亏损/为零）⇒ None：负基数上没有合法的 CAGR，硬算出来是假数。"""
    if not first or not last or years <= 0:
        return None
    if float(first) <= 0 or float(last) <= 0:
        return None
    return round(((float(last) / float(first)) ** (1.0 / years) - 1.0) * 100.0, 2)


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ------------------------------------------------------------------ 序列整理

def by_quarter(rows: Sequence[Dict[str, Any]], kind: str = 'single') -> Dict[str, Dict[str, float]]:
    """{列名: {报告期: 值}}，只留该档、且值非 None 的格子。"""
    out: Dict[str, Dict[str, float]] = {}
    for r in rows:
        if r.get('period_kind') != kind:
            continue
        d = r.get('report_date')
        key = d.isoformat() if hasattr(d, 'isoformat') else str(d)
        for name in AMOUNTS:
            v = r.get(_COL[name])
            if v is not None and math.isfinite(float(v)):
                out.setdefault(name, {})[key] = float(v)
    return out


def sorted_periods(series: Dict[str, float]) -> List[str]:
    return sorted(series.keys())


def same_quarter_years(series: Dict[str, float], period: str) -> List[Tuple[str, float]]:
    """同一季（月日相同）在历年上的取值，按时间倒序（最近在前）。"""
    mmdd = period[5:]
    return [(p, series[p]) for p in sorted_periods(series) if p.endswith(mmdd)][::-1]


def prev_year_period(period: str) -> str:
    return f'{int(period[:4]) - 1}{period[4:]}'


def next_period(period: str) -> str:
    """下一季报告期（Q 末）。"""
    y, m = int(period[:4]), int(period[5:7])
    order = [3, 6, 9, 12]
    i = order.index(m) if m in order else 3
    if i == 3:
        return f'{y + 1}-03-31'
    return f'{y}-{order[i + 1]:02d}-' + ('31' if order[i + 1] in (3, 12) else '30')


def fiscal_year_of(period: str) -> int:
    return int(period[:4])


# ------------------------------------------------------------------ 三条外推

def growth_from_yoy(yoys: Sequence[float]) -> Optional[float]:
    g = half_weighted(list(yoys)[:GROWTH_WINDOW])
    return round(clamp(g, *GROWTH_CLAMP), 2) if g is not None else None


def season_ratio(series: Dict[str, float], target_period: str,
                 years: int = SEASON_YEARS) -> Optional[float]:
    """「这一季相对它的参照期」的比例，近 N 年的中位数。凑不满 N 年也不硬给（None）。

    Q1..Q3 的参照期是上一季；**Q4 单独一档**：分母换成同年 Q1..Q3 的均值
    （`Q4_SEASON_RATIO` 那个 4/3 就是这个意思）。理由写在模块 docstring 第 2 条 ——
    Q4 常年是"全年减前三季"并含减值，拿"对 Q3 的环比"外推会系统性偏乐观。
    """
    is_q4 = target_period.endswith('-12-31')
    got: List[float] = []
    for p, v in same_quarter_years(series, target_period)[:years]:
        if v is None:
            continue
        if is_q4:
            y = p[:4]
            q = [series.get(f'{y}-{m:02d}-' + ('31' if m in (3, 12) else '30')) for m in (3, 6, 9)]
            q = [x for x in q if x is not None]
            if len(q) == 3 and sum(q) > 0:
                got.append(v / (sum(q) / 3.0))
            continue
        prev = _prev_quarter_of(p, series)
        if prev and prev > 0:
            got.append(v / prev)
    need = max(2, min(years, 2))
    return median(got) if len(got) >= need else None


def _prev_quarter_of(period: str, series: Dict[str, float]) -> Optional[float]:
    y, m = int(period[:4]), int(period[5:7])
    if m == 3:
        key = f'{y - 1}-12-31'
    else:
        order = [3, 6, 9, 12]
        key = f'{y}-{order[order.index(m) - 1]:02d}-' + ('31' if order[order.index(m) - 1] in (3, 12) else '30')
    return series.get(key)


def forecast_next_quarter(series: Dict[str, float], as_of: Optional[str] = None,
                          label: str = '下一季') -> Dict[str, Any]:
    """一个金额序列 → 下一季单季的三态预测。"""
    periods = sorted_periods(series)
    out: Dict[str, Any] = {'label': label, 'basis': None, 'target': None, 'value': None,
                           'method_a': None, 'method_b': None, 'divergence': None,
                           'band_low': None, 'band_high': None, 'quarters_used': len(periods),
                           'reason': None}
    if len(periods) < 8:
        out['reason'] = (f'单季只有 {len(periods)} 期：不足 8 期，季节性与增速都算不出来 —— '
                         '不是「预计持平」')
        return out
    last = as_of or periods[-1]
    tgt = next_period(last)
    out['target'] = tgt
    yy = series.get(prev_year_period(tgt))                 # 上一年的同一季
    last_v = series.get(last)
    # A 法：上一年的同一季 × (1 + 半衰加权同比)
    hist = [(p, yoy(series.get(p), series.get(prev_year_period(p)))) for p in periods]
    hist_y = [v for _p, v in reversed(hist) if v is not None]     # 最近在前
    g = growth_from_yoy(hist_y)
    if g is None:
        out['reason'] = '一年前同季或同比全缺 ⇒ 增速算不出来'
        return out
    if yy is not None:
        out['method_a'] = {'growth_percent': g, 'base_period': prev_year_period(tgt),
                           'value': round(yy * (1.0 + g / 100.0), 2)}
    s = season_ratio(series, tgt)
    if s is not None and last_v is not None:
        out['method_b'] = {'season_ratio': s, 'base_period': last, 'value': round(last_v * s, 2)}
    a = (out['method_a'] or {}).get('value')
    b = (out['method_b'] or {}).get('value')
    if a is None and b is None:
        out['reason'] = '两条外推的基期都取不到'
        return out
    if a is not None and b is not None:
        div = abs(a - b) / max(abs(a), abs(b), 1e-9) * 100.0
        out['divergence'] = round(div, 1)
        if div > DIVERGENCE_MAX:
            out['basis'] = '两法分歧'
            out['reason'] = (f'A（同比外推）与 B（季节环比）相差 {div:.0f}%：这只票的近季增速'
                             '与它历年这一季的季节形状打架 ⇒ 只交两法各自的数，不合成一个数')
            return out
        out['value'], out['basis'] = a, f'同比外推（半衰加权 {g:+.1f}%），季节环比校验'
    else:
        out['value'] = a if a is not None else b
        out['basis'] = '同比外推（缺季节样本，未校验）' if a is not None else '季节环比（缺同比基期）'
    # 不确定带：近 8 季 |实际同比 − 起点半衰预测| 的 σ
    errs: List[float] = []
    for i in range(1, min(8, len(hist_y))):
        # 用"该季之前的同比序列"重放一次外推，与实际比
        window = [v for v in hist_y[i:i + GROWTH_WINDOW]]
        pred = growth_from_yoy(window)
        act = hist_y[i - 1] if i - 1 < len(hist_y) else None
        if pred is not None and act is not None:
            errs.append(abs(act - pred))
    sig = statistics.pstdev(errs) if errs else None
    if len(errs) >= BAND_MIN_SAMPLES and sig:
        # ⚠️ 量纲：errs 是"增速的绝对误差"（百分点），这里当成金额的相对幅度 ±sig% 画区间。
        #    两者在增速接近 0 时几乎相等（金额 = 基期 ×(1+g)，g 差 sig 个点 ≈ 金额差 sig%），
        #    所以这句话必须写清是"近似"，不能让屏幕读成严格的金额置信带。
        out['band_low'] = round((out['value'] or 0) * (1 - sig / 100.0), 2)
        out['band_high'] = round((out['value'] or 0) * (1 + sig / 100.0), 2)
        out['band_note'] = (f'区间＝近 {len(errs)} 次同法回放的增速平均绝对误差 ±{sig:.1f} 个百分点，'
                            '按金额同幅近似给出（不是严格的金额置信带）')
    elif len(errs) >= BAND_MIN_SAMPLES:
        out['band_note'] = f'回放 {len(errs)} 次误差全为 0 ⇒ 不给区间（区间会退成一个点，看着像精确）'
    else:
        out['band_note'] = f'回放样本只有 {len(errs)} 次（<{BAND_MIN_SAMPLES}）⇒ 不给区间，宁缺不假'
    return out


def forecast_fiscal_year(single: Dict[str, float], cum: Dict[str, float],
                         year: int) -> Dict[str, Any]:
    """当年年报（12-31 累计）：已披露的累计原样留，没披露的季用单季外推逐季补。"""
    out: Dict[str, Any] = {'year': year, 'reported_through': None, 'disclosed': 0,
                           'missing_quarters': [], 'forecast_value': None, 'basis': None,
                           'reason': None}
    disclosed = [p for p in sorted_periods(cum) if p.startswith(f'{year}-')]
    through = max(disclosed, default=None)
    out['reported_through'] = through
    # `disclosed` 是"这一年已经披露到第几期"的期数。它必须真的等于那个数：这一格与
    # `reported_through` 并排上屏，写 0 而另一处说"已披露到 2026-06-30"，屏幕上就是自相矛盾。
    out['disclosed'] = len(disclosed)
    if through and through.endswith('12-31'):
        out['basis'] = '已披露年报'
        out['forecast_value'] = cum[through]
        out['reason'] = f'{year} 年年报已披露（累计 {cum[through]:,.0f} 元），这一格不是预测'
        return out
    base = cum[through] if through else None
    if base is None:
        out['reason'] = f'{year} 年一期都没披露 ⇒ 连「已披露到第几季」都说不出'
        return out
    total = base
    cur = through
    for m in (3, 6, 9, 12):
        key = f'{year}-{m:02d}-' + ('31' if m in (3, 12) else '30')
        if through and key <= through:
            continue
        f = forecast_next_quarter(single, as_of=cur)
        if f['value'] is None:
            out['reason'] = f'补 {key} 时断链：{f["reason"]}'
            # 断链也保持与成功项同一个形状（dict），否则页面上 `q.period` 会读出 undefined，
            # "补到第几季断了"这一句就变成"少了一行"。
            out['missing_quarters'].append({'period': key, 'value': None, 'basis': None,
                                            'fail_reason': f['reason']})
            return out
        out['missing_quarters'].append({'period': key, 'value': f['value'], 'basis': f['basis']})
        total += f['value']
        cur = key
    out['forecast_value'] = round(total, 2)
    out['basis'] = ('已披露累计 + 外推季' if out['missing_quarters'] else '已披露累计')
    if any(str(q.get('period', '')).endswith('12-31') for q in out['missing_quarters']):
        out['q4_caveat'] = ('补的那几季里含 Q4：Q4 在很多公司＝"全年减前三季"，常年含减值与年终费用，'
                            '这一档比 Q1..Q3 粗（季节比例用的是近 3 年 Q4÷前三季均值的中位数）')
    return out


def forecast_next_year(cum_annual: Dict[str, float], this_year_forecast: Optional[float]) -> Dict[str, Any]:
    """明年年报：0.4×近 5 年 CAGR + 0.6×近 3 年 CAGR，情景带用近 5 份年报同比的 25/75 分位。

    ⚠️ 分位窗口是**近 5 份**，不是"全部可得年报"：茅台那发实测有 28 份年报，把 2001~2012 那一段
    高增长也放进分位，25/75 会拉到 +14.5%~+30.7%，那条带子描述的是这家公司二十年前的样子，
    摆在"明年"这一格上是假话。窗口写死在 `SCENARIO_YEARS`，与口径句子里说的"近 5 年"必须是同一个数。
    """
    out: Dict[str, Any] = {'growth_percent': None, 'value': None, 'band_low': None,
                           'band_high': None, 'years_used': 0, 'reason': None}
    if this_year_forecast is None:
        out['reason'] = '当年那一格就没算出来 ⇒ 明年只能在此基础上再乘一次，不猜'
        return out
    annual = {p: v for p, v in cum_annual.items() if p.endswith('12-31')}
    ps = sorted(annual)
    if len(ps) < 4:
        out['reason'] = f'年报只有 {len(ps)} 份（<4）⇒ CAGR 算不出来'
        return out
    last = ps[-1]
    n3 = annual.get(ps[-3]) if len(ps) >= 3 else None
    n5 = annual.get(ps[-5]) if len(ps) >= 5 else None
    g3 = cagr(n3, annual[last], 2) if n3 else None
    g5 = cagr(n5, annual[last], 4) if n5 else None
    ys = [yoy(annual[p], annual.get(prev_year_period(p))) for p in ps[1:]][-SCENARIO_YEARS:]
    ys = [y for y in ys if y is not None]
    if g3 is None and g5 is None:
        out['reason'] = '年报序列里有 0 或负基数（亏损年），CAGR 没有合法定义 ⇒ 不给明年增速'
        return out
    g = (0.4 * g5 + 0.6 * g3) if (g5 is not None and g3 is not None) else (g3 if g5 is None else g5)
    g = clamp(g, *GROWTH_CLAMP)
    out['growth_percent'] = round(g, 2)
    out['years_used'] = len(ps)
    out['scenario_years_used'] = len(ys)
    out['cagr_basis'] = ('5年×0.4 + 3年×0.6' if (g5 is not None and g3 is not None)
                         else ('3 年 CAGR（凑不满 5 份年报）' if g5 is None else '5 年 CAGR（3 年那一档算不出）'))
    out['value'] = round(this_year_forecast * (1.0 + g / 100.0), 2)
    q25, q75 = pctl(ys, 0.25), pctl(ys, 0.75)
    if q25 is not None and q75 is not None and len(ys) >= 3:
        # ⚠️ 这不是点估计的"置信带"，是**两种情景**：按历史 25/75 分位增速各自会得到多少。
        #    实测茅台营收那一发：点估计 +8.93%，而近 27 份年报同比的 25 分位是 +14.5% ——
        #    如果把它写成"区间 1946~2222 亿"，区间整段在点之上，屏幕上就成了一句自相矛盾的话。
        #    所以端点与点估计取并集：区间一定包含点估计，并且说清它是分位情景带。
        lo = this_year_forecast * (1 + min(q25, g) / 100.0)
        hi = this_year_forecast * (1 + max(q75, g) / 100.0)
        out['scenario_low'] = round(min(lo, out['value']), 2)
        out['scenario_high'] = round(max(hi, out['value']), 2)
        out['band_note'] = (f'情景带＝近 {len(ys)} 份年报同比的 25/75 分位（{q25:+.1f}% ~ {q75:+.1f}%）'
                            f'与点估计 {g:+.1f}% 取并 ⇒ 一定包含点估计，不是置信区间')
    return out


# ------------------------------------------------------------------ 逐期表

# 源那一族的比率在**基期接近 0** 时会给出天文数字（10-16 实测：库里现存绝对值最大 9.78e7，
# net_profit_yoy_percent 的 99.9 分位是 2.77 万；net_margin 最大 3.89e7）。这类数不是"这把尺子量出来的读数"，
# 摆上屏就是假读数（点一下"净利率"降序，第一格是 38933440.56%）。库里**照源存着**（源镜像不删数），
# 读侧按下面两条封顶交 null，理由写进 caveat。
RATIO_ABS_MAX_PERCENT = 1000.0       # 毛利率/净利率/ROE 这类"率"：超过 1000% 就不是率了
YOY_SRC_ABS_MAX_PERCENT = 10000.0    # 源给的同比：超过 ±1 万% 只剩"基期有多接近 0"这一个信息量


def readable_ratio(v: Any, cap: float) -> Optional[float]:
    """把"源给的可读范围之外的比率"变成 null。非数字、NaN、inf、超过 ±cap 都算读不出。"""
    try:
        x = float(v)                                    # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if v is None or not math.isfinite(x) or abs(x) > cap:
        return None
    return x

def period_table(rows: Sequence[Dict[str, Any]], quarters: int = 20, years: int = 5) -> Dict[str, Any]:
    """近 N 季单季 + 近 M 份年报。每行交：金额、同比（源给的与我们重算的）、环比、单季 ROE/毛利率。

    ⚠️ 同比我们**自己按同季重算一份**（`yoy_recomputed`）：源那一列在累计档里是"对去年累计"、
    在单季档里才是"对去年同季"，直接抄会在跨档时说出假话。两列并存，界面上说的是哪一个要写清。
    """
    ser = by_quarter(rows, 'single')
    cumser = by_quarter(rows, 'cum')
    rev = ser.get('revenue', {})
    npr = ser.get('net_profit', {})
    dpr = ser.get('deduct_profit', {})
    qdates = sorted(set(list(rev) + list(npr)), reverse=True)[:quarters]
    # 源那一行按报告期索引，取毛利率/净利率/ROE/源同比用；键统一成 ISO 日期串
    look: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if r.get('period_kind') != 'single':
            continue
        d = r.get('report_date')
        look[d.isoformat() if hasattr(d, 'isoformat') else str(d)] = r
    out_rows: List[Dict[str, Any]] = []
    suppressed = 0

    def _readable(v: Any, cap: float) -> Optional[float]:
        """源那一格能不能当比率读。读不出计入 `suppressed_source_ratios`，屏上那句会报数（不静默藏数）。"""
        nonlocal suppressed
        got = readable_ratio(v, cap)
        if got is None and v is not None:
            suppressed += 1
        return got

    for d in qdates:
        prev_q = _prev_quarter_of(d, rev)
        src = look.get(d, {})
        out_rows.append({
            'period': d,
            'revenue': rev.get(d), 'revenue_yoy': yoy(rev.get(d), rev.get(prev_year_period(d))),
            'revenue_yoy_src': _readable(src.get('revenue_yoy_percent'), YOY_SRC_ABS_MAX_PERCENT),
            'revenue_qoq': qoq(rev.get(d), prev_q),
            'net_profit': npr.get(d), 'net_profit_yoy': yoy(npr.get(d), npr.get(prev_year_period(d))),
            'net_profit_qoq': qoq(npr.get(d), _prev_quarter_of(d, npr)),
            'deduct_profit': dpr.get(d),
            'gross_margin': _readable(src.get('gross_margin_percent'), RATIO_ABS_MAX_PERCENT),
            'net_margin': _readable(src.get('net_margin_percent'), RATIO_ABS_MAX_PERCENT),
            'roe_single': _readable(src.get('roe_percent'), RATIO_ABS_MAX_PERCENT),
        })
    annual = []
    for d in sorted([p for p in cumser.get('revenue', {}) if p.endswith('12-31')], reverse=True)[:years]:
        annual.append({
            'period': d,
            'revenue': cumser['revenue'].get(d),
            'revenue_yoy': yoy(cumser['revenue'].get(d), (cumser.get('revenue', {}).get(prev_year_period(d)))),
            'net_profit': cumser.get('net_profit', {}).get(d),
            'net_profit_yoy': yoy(cumser.get('net_profit', {}).get(d),
                                  cumser.get('net_profit', {}).get(prev_year_period(d))),
            'deduct_profit': cumser.get('deduct_profit', {}).get(d),
        })
    return {'quarters': out_rows, 'annual': annual, 'suppressed_source_ratios': suppressed}


def forecasts(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """一整套三块外推（下一季 / 当年 / 明年）×（营收、归母、扣非）。"""
    ser = by_quarter(rows, 'single')
    cum = by_quarter(rows, 'cum')
    res: Dict[str, Any] = {}
    for name in AMOUNTS:
        s = ser.get(name, {})
        c = cum.get(name, {})
        if not s:
            res[name] = {'next_quarter': {'label': '下一季', 'value': None, 'reason': '没有单季序列'},
                         'this_year': {'year': None, 'forecast_value': None,
                                       'reason': '没有单季序列'},
                         'next_year': {'value': None, 'reason': '没有单季序列'}}
            continue
        last_q = sorted_periods(s)[-1]
        year = fiscal_year_of(last_q)
        nq = forecast_next_quarter(s, label='下一季（' + next_period(last_q) + '）')
        ty = forecast_fiscal_year(s, c, year)
        ny = forecast_next_year(c, ty.get('forecast_value'))
        res[name] = {'next_quarter': nq, 'this_year': dict(ty, year=year),
                     'next_year': dict(ny, year=year + 1)}
    return res
