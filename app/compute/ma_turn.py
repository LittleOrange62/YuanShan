# -*- coding: utf-8 -*-
"""均线拐头（筛选条件 `rule_key = ma_turn`）——纯判据一份，登记模板与页面都指回这里。

用户 2026-10-20 的原话拆成**一条参数化规则**（不是四条硬编码规则）：
「XX日线拐头的，参数60就是60日线拐头的；长期下行 or 长期上行，叠加近 X 日横住了
或者有所回升，回升率（降低率）低于（高于）Y%」

一条规则四个维度，全部是参数：
  · 均线周期 N（`n`）—— 现算 N 日均线，不局限于库里登记过的那几档；
  · 长期段（`long_days` = M 日，方向 `long_dir` = down/up）；
  · 近期段（`recent_days` = X 日，形态 `recent_shape` = flat/recover/fall）；
  · 阈值（`threshold_pct` = Y，比较方向 `cmp` = gte/lte）。

**判据四件事同时成立才算拐头**（缺件不算，只有斜率翻号、幅度不够不算）：
  1. 长期段：`ma_N(t-M) → ma_N(t)` 净变化方向 = `long_dir`（down＝<0，up＝>0）；
  2. 拐头确认：`ma_N` 在基准日的一阶差分（`ma_N(t) − ma_N(t−1)`）与长期方向**反号**；
  3. 近期段：`ma_N(t-X) → ma_N(t)` 净变化符合形态（recover>0 / fall<0 / flat 幅度≤Y）；
  4. 阈值：近期变化幅度 |Δ| 与 Y% 按 `cmp` 比（gte ≥ / lte ≤）；`flat` 档天然是 lte。

**为什么均线是现算的后复权均线，不读 `analytics.indicator_value`**：
  · 那张表只留最近 94 个交易日的因子行（2026-10-21 实测：min 2026-06-02、max 2026-10-20），
    `long_days` 一档超出就静默查无此行 —— 空名单在页面上和「真筛出 0 只」长得一模一样
    （同 `app/compute/pattern.py` 头部那条教训：现算不依赖 d1 有没有跑到基准日）；
  · 表里的均线只有 ma5/10/20/30/60/120/250 固定几档，任意 N 本来就得现算
    （`vol_double.py` 自算 MA60 是同一路线的先例）；
  · 复权口径与 `35_ma_hfq_screen.sql` 一字不差：`close × adjustment_factor`，
    因子缺失或 ≤0 退回不复权（全表 61,295 行/115 只没因子，裸乘会让它们的均线整条空掉）。
    **绝不允许未复权均线** —— 除息日会把「下行」判成「走平」，这是本仓库踩过的坑
    （2026-10-13 实测 133/4,933 只年线上下判定因复不复权而翻转）。

缺数不判 0：均线值凑不齐四件（基准日、基准日前一日、t−X、t−M）或不足 N 根 K 线的票，
SQL 那边 NULL 传播直接判不了、不进结果；纯函数这边照实抛 ValueError 点名缺哪一段。
"""
from __future__ import annotations

from typing import Any

#: 这一族条件在 `config.screen_rule` 里的键
RULE_KEY = 'ma_turn'

#: 判据一句话（随接口交出的原文，界面逐字读它，不在前端另写一句）
CRITERION = ('一根后复权均线，长期段按 long_dir 走了净趋势，且基准日的日均线一阶差分翻出反号（拐头），'
             '且近期段净变化符合 recent_shape、其幅度按 cmp 与阈值 Y% 比得成立 —— '
             '长期方向、末端翻号、近期形态、幅度四件同时满足才算命中；只有斜率翻号、幅度不够，不算。')

#: 允许值（与登记表 param_schema 的 enum 一字不差；界面读接口拿到的就是这几档）
LONG_DIRS = ('down', 'up')
RECENT_SHAPES = ('recover', 'flat', 'fall')
CMPS = ('gte', 'lte')

#: 参数取值区间（与登记表 param_schema 的 minimum/maximum 一字不差，改要两处一起改）
BOUNDS = {
    'n': (5, 250),
    'long_days': (10, 250),
    'recent_days': (2, 120),
    'threshold_pct': (0.0, 50.0),
}


def definitions() -> list[dict]:
    """每个参数什么含义 —— 一件事一句话，文档与测试都指回这一份。"""
    return [
        {'name': 'n', 'means': f'均线周期 N（{BOUNDS["n"][0]}..{BOUNDS["n"][1]} 的整数）；'
                               '均线用后复权收盘价在 SQL 里现算，所以 N 不受库里固定几档均线名的限制'},
        {'name': 'long_days', 'means': f'长期段回看 M 个交易日（{BOUNDS["long_days"][0]}..{BOUNDS["long_days"][1]}），'
                                       '判 ma_N(t−M) → ma_N(t) 的净变化方向'},
        {'name': 'long_dir', 'means': '长期方向，两档：down＝长期下行（净变化<0）、up＝长期上行（净变化>0）'},
        {'name': 'recent_days', 'means': f'近期段回看 X 个交易日（{BOUNDS["recent_days"][0]}..{BOUNDS["recent_days"][1]}），'
                                         '必须小于 long_days'},
        {'name': 'recent_shape', 'means': '近期形态，三档：recover＝回升（净变化>0）、'
                                          'flat＝横住（幅度≤Y）、fall＝回落（净变化<0）'},
        {'name': 'threshold_pct', 'means': f'阈值 Y（百分数，{BOUNDS["threshold_pct"][0]}..{BOUNDS["threshold_pct"][1]}），'
                                           '作用于近期段净变化的幅度'},
        {'name': 'cmp', 'means': '比较方向：gte＝幅度≥Y 才算、lte＝幅度≤Y 才算；flat 档天然是 lte'},
    ]


def check_params(p: dict) -> None:
    """参数**之间**的关系校验（单值的 enum/区间白名单由执行引擎按 param_schema 把关）。

    不合法就抛 ValueError 点名是哪个参数、收到的是什么 —— 由 `screener` 包成
    ScreenError 原话端给页面，不许静默当作没筛。
    """
    ld, rd = p.get('long_days'), p.get('recent_days')
    if ld is not None and rd is not None and rd >= ld:
        raise ValueError(f'长期段回看 long_days 必须大于近期段 recent_days'
                         f'（拐头是「长期趋势末端的一次短期翻向」，两档重叠就没这件事可判了），'
                         f'收到 long_days={ld}、recent_days={rd}')


def _need(ma: dict, off: int, label: str) -> float:
    if off not in ma or ma[off] is None:
        raise ValueError(f'{label}（基准日前第 {off} 个交易日）缺数 —— 判不了，不当 0 参与')
    v = float(ma[off])
    if v <= 0:
        raise ValueError(f'{label} 读到的均线值不是正数（{v}）—— 均线为 0 或负是坏数，不硬判')
    return v


def evaluate(ma: dict, *, long_days: int, recent_days: int,
             long_dir: str, recent_shape: str,
             threshold_pct: float, cmp: str) -> dict:
    """纯判据：`ma` 是 `{距基准日的交易日偏移: 后复权 ma_N 的值}`，至少要含
    `0`（基准日）、`1`（前一日）、`recent_days`、`long_days` 四个偏移。

    返回逐件判定与两个净变化率，命中与否看 `hit`。缺数抛 ValueError —— 调用方
    （SQL 那边）的对应语义是「这只票判不了、不进结果」，不是「判它不合格」。
    """
    if long_dir not in LONG_DIRS:
        raise ValueError(f'参数 long_dir 只能是 {list(LONG_DIRS)} 之一，收到 {long_dir!r}')
    if recent_shape not in RECENT_SHAPES:
        raise ValueError(f'参数 recent_shape 只能是 {list(RECENT_SHAPES)} 之一，收到 {recent_shape!r}')
    if cmp not in CMPS:
        raise ValueError(f'参数 cmp 只能是 {list(CMPS)} 之一，收到 {cmp!r}')
    check_params({'long_days': long_days, 'recent_days': recent_days})
    thr = float(threshold_pct)
    if not (BOUNDS['threshold_pct'][0] <= thr <= BOUNDS['threshold_pct'][1]):
        raise ValueError(f'阈值 threshold_pct 只能是 {BOUNDS["threshold_pct"][0]}..{BOUNDS["threshold_pct"][1]} '
                         f'之间的百分数（负数没有方向意义，>50 会把横盘也判成拐头），收到 {thr}')

    a0 = _need(ma, 0, '均线在基准日的值')
    a1 = _need(ma, 1, '均线在基准日前一日的值')
    ar = _need(ma, recent_days, f'均线在基准日前第 {recent_days} 日（近期段起点）的值')
    al = _need(ma, long_days, f'均线在基准日前第 {long_days} 日（长期段起点）的值')

    long_chg = (a0 - al) / al * 100.0        # ma_N(t−M) → ma_N(t) 的净变化率（%）
    recent_chg = (a0 - ar) / ar * 100.0      # ma_N(t−X) → ma_N(t) 的净变化率（%）
    diff_end = a0 - a1                       # 一阶差分在近期段末端

    long_ok = long_chg < 0 if long_dir == 'down' else long_chg > 0
    # 末端翻号：长期往下则此刻在抬升，长期往上则此刻在下压
    turn_ok = diff_end > 0 if long_dir == 'down' else diff_end < 0
    magnitude = abs(recent_chg)
    if recent_shape == 'recover':
        shape_ok = recent_chg > 0
    elif recent_shape == 'fall':
        shape_ok = recent_chg < 0
    else:
        shape_ok = magnitude <= thr
    if recent_shape == 'flat':
        # flat 档的「横住」本身就是幅度≤Y，天然是 lte，不再按 cmp 加第二道比较
        threshold_ok = magnitude <= thr
    else:
        threshold_ok = magnitude >= thr if cmp == 'gte' else magnitude <= thr

    return {
        'long_chg_pct': round(long_chg, 4),
        'recent_chg_pct': round(recent_chg, 4),
        'diff_end': round(diff_end, 6),
        'long_ok': bool(long_ok),
        'turn_ok': bool(turn_ok),
        'shape_ok': bool(shape_ok),
        'threshold_ok': bool(threshold_ok),
        'hit': bool(long_ok and turn_ok and shape_ok and threshold_ok),
    }


def hit(ma: dict, **kw: Any) -> bool:
    """只要布尔结果的入口（文档/测试用；页面读的是逐件判定那份）。"""
    return evaluate(ma, **kw)['hit']
