# -*- coding: utf-8 -*-
"""
趋势启动点：把"要起来 / 刚起来 / 还在走 / 已经在退"分成四档标出来。

## 为什么不能只看斜率

`ys.sector_trend` 那 8 个窗口的斜率度量的是"**已经在走**"。斜率翻正的时候，
行情通常已经走了三五天 —— 拿它当买点，每次都是接最后一棒。启动点是**状态
转变**，要同时看"之前有多静"和"今天有多不一样"，这就是下面五个要素的分工。

## 五个要素（各 0~100，越高越符合）

- `squeeze` 波动状态：热度 20 日标准差、日内振幅 20 日均值，各取自身 **120 日分位**。
  方向由 `squeeze_dir` 决定，**默认 `active`（波动已在放大＝高分）**。
  ⚠ 这一条与方案里写的直觉**相反**，是被回测推翻后改的，别再"顺手改回沉寂方向"：
  `quiet`（沉寂加分，原直觉）样本内 ignite T+5 超额 **−0.209**、胜率 **42.7%**，比
  `none` 对照组还差；`active` 同期 **+0.148**、**51.1%**；32 组网格 top-8 全是
  `('active','low',...)`，最差的全是 `quiet`。样本外（2025-07 起）方向依旧稳定。
  事后看也说得通：热度是**截面分位**，"沉寂"在这里等于"截面排名一直没动"，
  那更多是没人管的冷板块，不是压紧的弹簧。
- `position_score` 位置：当日热度在自身 120 日分位里的**低位**。这一条专治追高：分位 0.9
  的板块再放量也不是启动，是末段。
- `ignition` 点火：热度 3 日斜率**由负转正**（40 分）＋ 斜率还在**加速**（30 分）
  ＋ 热度 5 日均线**上穿** 20 日（30 分）。三件事都是"今天开始不一样了"。
- `volume` 量能：板块成交额 ÷ 自身 20 日均额，**并且**该板块占全市场成交额的
  **份额**在抬升。份额比绝对放量重要 —— 大盘普涨那天所有板块都放量，只看倍数
  会全体误报（这个坑 `sector_chengjiao.py` 已经踩过一次，那里的修法是除以全市场
  中位数，这里换成看份额，同一个道理）。
- `breadth` 广度：板块内上涨家数占比的水平 + 相对 10 日均值的抬升。广度是最难
  造假的一维：指数可以被几只权重股拉起来，上涨家数占比骗不了人。

## 四个档位

    coil    蓄势   波动状态 + 低位都成立，点火还没出现      → 提前观察名单
    ignite  点火   蓄势在**前几天**成立，今天三条确认到位  → 核心产出
    follow  延续   点火后仍在走                          → "错过第一天还能不能上"
    fade    退潮   高位 + 斜率转负                        → 反向清单

**最容易写错的一处**：`ignite` 的前置条件必须读**信号日之前**的蓄势状态
（`pre_days` 天内曾经成立，且整段 `shift(1)`）。用当天的状态去筛"刚启动"会把
"启动前什么样"这一维直接丢掉 —— 那就退化成了纯动量筛选。

**`coil` 不是买入清单**。它样本外 T+5 超额 **−0.107**、胜率 46.3（对照组 +0.074／
48.7），也就是说"只蓄势、还没点火"本身是负期望 —— 它的唯一用途是**当 ignite 的
前置**和"提前把名字放进视野"。页面上必须这么写，否则那两万多行会被当成推荐位。



## 两个诚实的取舍

1. **广度只用 `market_data.sector_daily` 自带的 `up_count/down_count`**，不从成员股现算。
   `reference_data.sector_member` 263,325 行**全部 `out_date IS NULL`**（没有历史成分变动），
   拿今天的成分去算 2021 年的广度是生存偏差。板块日线里的家数是采集当时的真值。

   代价很硬，必须写在这里：**230 个行业板块一条涨跌家数都没有**（概念 268/268、
   风格 150/154 都有，行业 0/230），所以行业板块的 `term_count` 最多 4，
   **永远不会被判成 ignite**。已经实测过"缺广度就跳过这一条"的放宽版：
   行业 OOS n=151、胜率 **40.3%**、T+5 超额 **−0.318**（对照组 +0.009）——
   **放宽是负期望**，不是"少一维差一点"。所以这里不放宽，缺口靠补采数据解决。
   顺带说明 880001~880005 这类指数型板块同理落在 ignite 之外，这是对的：
   上证指数不是"启动的板块"。

2. **只用到 T 日收盘为止的信息**。分钟线还是空的（已知缺口），所以最快是 T 日
   盘后给结论，做不到盘中预警。所有滚动窗口都以当日为右端点，`shift` 只往过去
   看 —— `tests/test_ignition.py` 里有一条专门验证"不含未来数据"。
"""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd

from compute import engine
from compute import registry as reg_repo

SCOPES = ('sector', 'group')
STAGES = ('coil', 'ignite', 'follow', 'fade', 'none')

#: 所有旋钮的默认值。**实际生效的是 `config.setting['ignition.params']`**，
#: 这里只是兜底与文档 —— 回测反推出来的阈值要能改配置就生效，不用改代码。
DEFAULTS = {
    # 窗口
    'base_win': 120,        # 分位基线窗口（压缩、位置都在这个尺度上比）
    'base_min': 60,         # 基线至少要这么多个点，不够就不给分位（宁可空着）
    'vol_win': 20,          # 放量倍数与份额倍数的基准窗口
    'breadth_win': 10,      # 广度抬升的比较基准
    'std_win': 20,          # 热度波动的窗口
    'pre_days': 5,          # ignite 的前置：这么多天内曾经蓄势
    'follow_days': 3,       # 点火后还算"延续"的天数

    # 要素方向
    #: 'active' = 波动已在放大得高分（回测选定）；'quiet' = 越沉寂越高分（原直觉，
    #: 已被证伪，留着是为了能一键复现那次对比，不是给日常切换用的）
    'squeeze_dir': 'active',

    # 要素阈值。**这三个 ig_* 是回测反推的，不是拍的**：样本外（2025-07 起）
    #   (70,65,65) n=157  胜率 58.4  T+5 超额 +0.526
    #   (60,65,55) n=196  胜率 57.5  T+5 超额 +0.455   ← 当前默认
    #   (60,55,55) n=268  胜率 56.1  T+5 超额 +0.299
    #   (50,50,50) n=321  胜率 56.2  T+5 超额 +0.304
    #   基准（全板块等权同期）+0.074
    # 取中间那档：越严超额越高，但 157 个样本铺在 280 个交易日上，一天不到 0.6 条，
    # 少一次也看不出是运气还是规律。要更严就改 config.setting，不用动代码。
    'coil_squeeze': 60.0,
    'coil_pos': 55.0,
    'ig_ignition': 60.0,
    'ig_volume': 65.0,
    'ig_breadth': 55.0,
    'fade_pos_pctile': 0.80,   # 热度分位高于此算高位


    # 量能与广度的映射区间
    'vol_lo': 1.00, 'vol_hi': 2.00,
    'share_lo': 1.00, 'share_hi': 1.50,
    'up_lo': 0.40, 'up_hi': 0.80,
    'up_chg_lo': 0.00, 'up_chg_hi': 0.15,

    # 综合分权重（会自动归一）
    'w_squeeze': 0.15, 'w_position_score': 0.20, 'w_ignition': 0.30,
    'w_volume': 0.20, 'w_breadth': 0.15,
}

SETTING_KEY = 'ignition.params'

TERMS = ('squeeze', 'position_score', 'ignition', 'volume', 'breadth')

SQUEEZE_DIRS = ('active', 'quiet')

STG = 'ys.staging_ignition'


def params(**override) -> dict:
    """默认值 ← `config.setting['ignition.params']` ← 调用方覆盖。"""
    p = dict(DEFAULTS)
    try:
        raw = reg_repo.setting(SETTING_KEY, {})
    except Exception:  # noqa: BLE001
        raw = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in p:
                try:
                    p[k] = _cast(p[k], v)
                except (TypeError, ValueError):
                    continue
    for k, v in override.items():
        if k in p and v is not None:
            p[k] = v
    if p['squeeze_dir'] not in SQUEEZE_DIRS:
        raise ValueError(f'squeeze_dir 只能是 {SQUEEZE_DIRS}，'
                         f'收到 {p["squeeze_dir"]!r}')
    return p


def _cast(proto, v):
    """按默认值的类型转换配置值 —— 字符串旋钮不能被 float() 吞掉。"""
    if isinstance(proto, str):
        return str(v).strip().lower()
    if isinstance(proto, float):
        return float(v)
    return int(v)



# ---------------------------------------------------------------- 小工具
def _clip01(s: pd.Series, lo: float, hi: float) -> pd.Series:
    """映射到 0~100（不是 -100~100）：要素分是"符合程度"，没有负向含义。"""
    v = pd.to_numeric(s, errors='coerce').clip(lo, hi)
    return (v - lo) / (hi - lo) * 100.0


def _by(df: pd.DataFrame, col: str):
    return pd.to_numeric(df[col], errors='coerce').groupby(
        level='object_key', sort=False)


def _roll(df: pd.DataFrame, col: str, win: int, how: str = 'mean',
          min_periods: int | None = None) -> pd.Series:
    out = getattr(_by(df, col).rolling(win, min_periods=min_periods or win), how)()
    if out.index.nlevels > df.index.nlevels:
        out = out.droplevel(0)
    return out.reindex(df.index)


def _pctile(df: pd.DataFrame, col: str, win: int, min_periods: int) -> pd.Series:
    """
    自身历史分位（0~1），右端点是当日。

    用 `rolling(...).rank(pct=True)`：窗口里包含当日本身，所以"当日是窗口内最大"
    时得 1.0。这是**只看过去**的分位，没有未来数据。
    """
    out = _by(df, col).rolling(win, min_periods=min_periods).rank(pct=True)
    if out.index.nlevels > df.index.nlevels:
        out = out.droplevel(0)
    return out.reindex(df.index)


def _shift(df: pd.DataFrame, s: pd.Series, n: int = 1) -> pd.Series:
    return s.groupby(level='object_key', sort=False).shift(n)


# ---------------------------------------------------------------- 读取
SECTOR_SQL = """
SELECT h.sector_code AS object_key, h.trade_date, h.heat,
       d.high, d.low, d.close, d.pct_change, d.amount_yuan,
       d.up_count, d.down_count, d.member_count
  FROM ys.sector_heat h
  JOIN market_data.sector_daily d ON d.sector_code = h.sector_code
                        AND d.trade_date = h.trade_date
 WHERE h.source = 'sector'
   -- 不可用的板块显式挡在这里，不靠"热度表里应该已经没有它们了"。
   -- 热度是 upsert 写的，删不掉历史上落进去的坏行（`sector_heat_hist.prune()`
   -- 才会删）；而这一层是给人看的清单，混进一个次日涨幅四千多倍的板块、
   -- 或者一个只有 1 天历史的新板块，就等于整张表不能信。判据见 sector_heat_hist。
   -- （注意：这段注释里不能出现百分号 —— psycopg 会把它当成占位符起头，
   --   报 "incomplete placeholder"，SQL 注释它是不认的。踩过一次。）
   AND {reliable}

   -- `::date` 不能省：全是 NULL 时 PG 推不出参数类型，报
   -- "could not determine data type of parameter $1"
   AND (%(start)s::date IS NULL OR h.trade_date >= %(start)s::date)
   AND (%(end)s::date   IS NULL OR h.trade_date <= %(end)s::date)
 ORDER BY 1, 2
"""



NUM_COLS = ('heat', 'high', 'low', 'close', 'pct_change', 'amount_yuan',
            'up_count', 'down_count', 'member_count')


def load_sector(start: dt.date | None = None,
                end: dt.date | None = None) -> pd.DataFrame:
    from compute.sector_heat_hist import usable_clause
    rows = engine.query(SECTOR_SQL.format(reliable=usable_clause('d')),
                        {'start': start, 'end': end})

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for c in NUM_COLS:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df.set_index(['object_key', 'trade_date']).sort_index()


# ---------------------------------------------------------------- 五个要素
def terms(df: pd.DataFrame, p: dict | None = None) -> pd.DataFrame:
    """
    算五个要素分 + 保留原始中间量（给 `detail` 用，页面要能解释）。

    输入索引必须是 (object_key, trade_date) 且按时间升序 —— 所有滚动量都依赖这个顺序。
    """
    if df.empty:
        return pd.DataFrame()
    p = p or params()
    base_win, base_min = int(p['base_win']), int(p['base_min'])
    out = pd.DataFrame(index=df.index)

    # ---- 波动状态：热度波动 + 日内振幅，各取自身 120 日分位
    work = df.copy()
    work['heat_std'] = _roll(df, 'heat', int(p['std_win']), 'std',
                             min_periods=max(5, int(p['std_win']) // 2))
    amp = (pd.to_numeric(df['high'], errors='coerce')
           - pd.to_numeric(df['low'], errors='coerce')) / \
        pd.to_numeric(df['close'], errors='coerce').replace(0, pd.NA)
    work['amp'] = amp
    work['amp20'] = _roll(work, 'amp', int(p['std_win']), 'mean',
                          min_periods=max(5, int(p['std_win']) // 2))
    q_std = _pctile(work, 'heat_std', base_win, base_min)
    q_amp = _pctile(work, 'amp20', base_win, base_min)
    # 两个分位取平均。一项缺失就用另一项，都缺就留空 —— 不拿 0 顶（0 分在这里是
    # "波动最小"，含义正好相反，用 0 填等于给冷板块发满分的反面）
    q_vol = pd.concat([q_std, q_amp], axis=1).mean(axis=1, skipna=True)
    # 方向：active = 分位越高（波动已放大）越高分。见模块 docstring 里那段回测记录，
    # 这个方向是被数据选出来的，不是推理出来的。
    out['squeeze'] = (q_vol if p['squeeze_dir'] == 'active'
                      else 1.0 - q_vol) * 100.0


    # ---- 位置：热度自身分位的低位
    q_heat = _pctile(df, 'heat', base_win, base_min)
    out['position_score'] = (1.0 - q_heat) * 100.0

    # ---- 点火：3 日斜率由负转正 + 加速 + 5/20 均线上穿
    # 3 个等距点的最小二乘斜率就是 (y3 − y1) / 2，与 ys.sector_trend 的
    # regr_slope 同一口径（等距 x），不用再拟合一次。
    heat = pd.to_numeric(df['heat'], errors='coerce')
    lag2 = heat.groupby(level='object_key', sort=False).shift(2)
    slope3 = (heat - lag2) / 2.0
    prev3 = slope3.groupby(level='object_key', sort=False).shift(1)
    ma5 = _roll(df, 'heat', 5, 'mean', min_periods=3)
    ma20 = _roll(df, 'heat', 20, 'mean', min_periods=10)
    prev_ma5 = ma5.groupby(level='object_key', sort=False).shift(1)
    prev_ma20 = ma20.groupby(level='object_key', sort=False).shift(1)

    turn = (slope3 > 0) & (prev3 <= 0)
    accel = slope3 > prev3
    cross = (ma5 > ma20) & (prev_ma5 <= prev_ma20)
    known = slope3.notna() & prev3.notna()
    ig = (turn.astype(float) * 40.0 + accel.astype(float) * 30.0
          + cross.astype(float) * 30.0)
    out['ignition'] = ig.where(known)

    # ---- 量能：自身放量倍数 × 全市场份额抬升
    amt = pd.to_numeric(df['amount_yuan'], errors='coerce')
    amt_ma = _roll(df, 'amount_yuan', int(p['vol_win']), 'mean',
                   min_periods=max(5, int(p['vol_win']) // 2))
    vol_ratio = amt / amt_ma.replace(0, pd.NA)
    dates = df.index.get_level_values('trade_date')
    total = amt.groupby(dates).transform('sum')
    share = amt / total.replace(0, pd.NA)
    work['share'] = share
    share_ma = _roll(work, 'share', int(p['vol_win']), 'mean',
                     min_periods=max(5, int(p['vol_win']) // 2))
    share_ratio = share / share_ma.replace(0, pd.NA)
    out['volume'] = (_clip01(vol_ratio, p['vol_lo'], p['vol_hi']) * 0.5
                     + _clip01(share_ratio, p['share_lo'], p['share_hi']) * 0.5)

    # ---- 广度：上涨占比的水平 + 相对 10 日均值的抬升
    up = pd.to_numeric(df['up_count'], errors='coerce')
    dn = pd.to_numeric(df['down_count'], errors='coerce')
    traded = (up + dn).replace(0, pd.NA)
    up_ratio = up / traded
    work['up_ratio'] = up_ratio
    up_ma = _roll(work, 'up_ratio', int(p['breadth_win']), 'mean',
                  min_periods=max(3, int(p['breadth_win']) // 2))
    out['breadth'] = (_clip01(up_ratio, p['up_lo'], p['up_hi']) * 0.5
                      + _clip01(up_ratio - up_ma, p['up_chg_lo'],
                                p['up_chg_hi']) * 0.5)

    # ---- 综合分：空要素不计权重（与评分驱动同一约定）
    w = {t: float(p[f'w_{t}']) for t in TERMS}
    num = pd.Series(0.0, index=df.index)
    den = pd.Series(0.0, index=df.index)
    cnt = pd.Series(0, index=df.index)
    for t, weight in w.items():
        v = pd.to_numeric(out[t], errors='coerce')
        ok = v.notna()
        num = num + (v.fillna(0.0) * weight).where(ok, 0.0)
        den = den + pd.Series(weight, index=df.index).where(ok, 0.0)
        cnt = cnt + ok.astype(int)
    out['score'] = (num / den.where(den > 0)).round(2)
    out['term_count'] = cnt.astype(int)

    # ---- 原始量留痕：页面要能解释"凭什么说它点火"，人也要能自己核
    out['_q_heat'] = q_heat
    out['_q_vol'] = q_vol

    out['_slope3'] = slope3
    out['_vol_ratio'] = vol_ratio
    out['_share_ratio'] = share_ratio
    out['_up_ratio'] = up_ratio
    out['_up_ma'] = up_ma
    out['_turn'] = turn & known
    out['_accel'] = accel & known
    out['_cross'] = cross
    return out


# ---------------------------------------------------------------- 分级
def stages(t: pd.DataFrame, p: dict | None = None) -> pd.Series:
    """
    四档 + none。判据里 `ignite` 的前置读的是**信号日之前**的蓄势状态。

    顺序有意义：ignite > follow > fade > coil > none。一个板块同一天可能既满足
    "还在低位"又满足"今天点火"，这时候要报 ignite（那才是要看的事）。
    """
    if t.empty:
        return pd.Series(dtype='object')
    p = p or params()
    coil_ok = ((pd.to_numeric(t['squeeze'], errors='coerce')
                >= float(p['coil_squeeze']))
               & (pd.to_numeric(t['position_score'], errors='coerce')
                  >= float(p['coil_pos'])))

    # 前置：过去 pre_days 天里**曾经**蓄势（含 shift(1)，不看今天）
    pre = (coil_ok.astype(float)
           .groupby(level='object_key', sort=False)
           .rolling(int(p['pre_days']), min_periods=1).max())
    if pre.index.nlevels > t.index.nlevels:
        pre = pre.droplevel(0)
    pre = pre.reindex(t.index).groupby(level='object_key', sort=False).shift(1)
    pre_coil = pre.fillna(0.0) > 0

    fired = (pre_coil
             & (pd.to_numeric(t['ignition'], errors='coerce')
                >= float(p['ig_ignition']))
             & (pd.to_numeric(t['volume'], errors='coerce')
                >= float(p['ig_volume']))
             & (pd.to_numeric(t['breadth'], errors='coerce')
                >= float(p['ig_breadth']))
             # 五个要素齐全才算点火：广度缺失的指数型板块（880001 上证之类）
             # 到不了这一档，这是有意的
             & (pd.to_numeric(t['term_count'], errors='coerce') >= len(TERMS)))

    # 延续：过去 follow_days 天内点过火，且今天热度 3 日斜率还是正的
    recent = (fired.astype(float).groupby(level='object_key', sort=False)
              .rolling(int(p['follow_days']) + 1, min_periods=1).max())
    if recent.index.nlevels > t.index.nlevels:
        recent = recent.droplevel(0)
    recent = recent.reindex(t.index).groupby(level='object_key',
                                             sort=False).shift(1)
    follow = ((recent.fillna(0.0) > 0)
              & (pd.to_numeric(t['_slope3'], errors='coerce') > 0)
              & ~fired)

    fade = ((pd.to_numeric(t['_q_heat'], errors='coerce')
             >= float(p['fade_pos_pctile']))
            & (pd.to_numeric(t['_slope3'], errors='coerce') < 0))

    out = pd.Series('none', index=t.index, dtype='object')
    out[coil_ok.fillna(False)] = 'coil'
    out[fade.fillna(False)] = 'fade'
    out[follow.fillna(False)] = 'follow'
    out[fired.fillna(False)] = 'ignite'
    return out


DETAIL_KEYS = {
    '_q_heat': 'heat_pctile', '_q_vol': 'vol_pctile', '_slope3': 'slope3',

    '_vol_ratio': 'vol_ratio', '_share_ratio': 'share_ratio',
    '_up_ratio': 'up_ratio', '_up_ma': 'up_ratio_ma',
    '_turn': 'turned_up', '_accel': 'accelerating', '_cross': 'ma_cross',
}


#: 这三项在 detail 里必须是 JSON 的 true/false，**按列名认，不按类型认**：
#: 从 DataFrame 里取一行（`iloc` / `iterrows`）会把混合列上抬成 float64，
#: bool 到这里已经是 1.0/0.0，`isinstance(v, bool)` 判不出来 —— 于是 detail
#: 里写成数字，页面拿 `turned_up == true` 去筛一条都筛不到。踩过一次。
BOOL_KEYS = {'_turn', '_accel', '_cross'}



def _detail_dict(get) -> dict:
    d = {}
    for src, name in DETAIL_KEYS.items():
        v = get(src)
        if v is None:
            continue
        if src in BOOL_KEYS:
            d[name] = bool(v)
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f != f:      # NaN；不用 pd.isna，这里一行要跑 67 万次
            continue
        d[name] = round(f, 6)
    return d


def _detail(row) -> str:
    return json.dumps(_detail_dict(row.get), ensure_ascii=False)


def _details(t: pd.DataFrame) -> list[str]:
    """
    整列取 numpy 再逐行组 dict。**不要换回 `iterrows()`** —— 67 万行时
    `iterrows()` 每行都要新建一个 Series，实测是分钟级；这里是秒级。
    """
    arrs = {src: t[src].to_numpy() for src in DETAIL_KEYS if src in t.columns}
    out = []
    for i in range(len(t)):
        out.append(json.dumps(
            _detail_dict(lambda s, i=i: arrs[s][i] if s in arrs else None),
            ensure_ascii=False))
    return out




# ---------------------------------------------------------------- 落库
UPSERT = f"""
INSERT INTO ys.ignition (scope, object_key, trade_date, stage, score, squeeze,
                          position_score, ignition, volume, breadth, term_count, detail,
                          task_id)
SELECT scope, object_key, trade_date, stage, score, squeeze, position_score, ignition,
       volume, breadth, term_count, detail::jsonb, task_id
  FROM {STG}
ON CONFLICT (scope, object_key, trade_date) DO UPDATE SET
    stage = EXCLUDED.stage, score = EXCLUDED.score,
    squeeze = EXCLUDED.squeeze, position_score = EXCLUDED.position_score,
    ignition = EXCLUDED.ignition, volume = EXCLUDED.volume,
    breadth = EXCLUDED.breadth, term_count = EXCLUDED.term_count,
    detail = EXCLUDED.detail, task_id = EXCLUDED.task_id,
    calculated_at = now()
"""

_COLS = ('scope', 'object_key', 'trade_date', 'stage', 'score', 'squeeze', 'position_score',
         'ignition', 'volume', 'breadth', 'term_count', 'detail', 'task_id')


def _num_arr(s: pd.Series):

    """整列转成"数值或 None"，给 COPY 用。NaN 不能直接进 numeric 列。"""
    v = pd.to_numeric(s, errors='coerce').round(2)
    return v.astype(object).where(v.notna(), None).to_numpy()


def write(scope: str, t: pd.DataFrame, stage: pd.Series, *,
          task_id: int | None = None) -> int:
    if scope not in SCOPES:
        raise ValueError(f'scope 只能是 {SCOPES}')
    if t.empty:
        return 0
    obj = t.index.get_level_values('object_key').to_numpy()
    day = t.index.get_level_values('trade_date').to_numpy()
    st = stage.reindex(t.index).fillna('none').astype(str).to_numpy()
    num = {c: _num_arr(t[c]) for c in ('score', 'squeeze', 'position_score', 'ignition',
                                       'volume', 'breadth')}
    nt = (pd.to_numeric(t['term_count'], errors='coerce')
          .fillna(0).astype(int).to_numpy())
    det = _details(t)
    rows = (
        (scope, obj[i], day[i], st[i], num['score'][i], num['squeeze'][i],
         num['position_score'][i], num['ignition'][i], num['volume'][i],
         num['breadth'][i], int(nt[i]), det[i], task_id)
        for i in range(len(t)))
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {STG}')
        cur.execute(
            f'CREATE UNLOGGED TABLE {STG} (scope text, object_key text, '
            'trade_date date, stage text, score numeric(8,2), '
            'squeeze numeric(8,2), position_score numeric(8,2), ignition numeric(8,2), '
            'volume numeric(8,2), breadth numeric(8,2), term_count smallint, '
            'detail text, task_id bigint)')
    engine.copy_rows(STG, list(_COLS), rows, role='admin')
    n = engine.execute(UPSERT, role='admin')
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {STG}')
    return n



# ---------------------------------------------------------------- 回测自证
#: 回测看的持有期（交易日）。中线尺度，所以重点是 5 和 10。
HORIZONS = (1, 3, 5, 10)


def forward(df: pd.DataFrame, horizons=HORIZONS) -> pd.DataFrame:
    """
    每个 (板块, 日) 的未来 k 日收益与**同期全市场等权基准**的超额。

    用板块自己的 `close`（板块指数点位）算 `close[T+k]/close[T] − 1`，不累加
    `pct_change` —— 累加会把复利丢掉，而且 `pct_change` 在除权日有历史问题。

    这里**故意**用了未来数据（`shift(-k)`）：这是评估，不是信号。要素与分级里
    一个未来值都不能出现，`tests/test_ignition.py::test_terms_use_no_future_data`
    专门盯这件事。
    """
    close = pd.to_numeric(df['close'], errors='coerce')
    dates = df.index.get_level_values('trade_date')
    out = pd.DataFrame(index=df.index)
    g = close.groupby(level='object_key', sort=False)
    for k in horizons:
        fwd = g.shift(-k) / close.replace(0, pd.NA) - 1.0
        bench = fwd.groupby(dates).transform('mean')
        out[f'ret{k}'] = fwd * 100.0
        out[f'exc{k}'] = (fwd - bench) * 100.0
    return out


def backtest(df: pd.DataFrame, t: pd.DataFrame, stage: pd.Series,
             *, horizons=HORIZONS, min_terms: int = len(TERMS)) -> list[dict]:
    """
    按档位统计未来收益。**`none` 档是对照组** —— 如果 ignite 的超额和它差不多，
    这套要素就是白算的，那种情况必须看得见，不能只报 ignite 好看的那一行。

    只统计 `term_count >= min_terms` 的样本：广度缺失的指数型板块混进来会把
    "板块启动"的统计稀释成"指数涨跌"。
    """
    fwd = forward(df, horizons)
    ok = pd.to_numeric(t['term_count'], errors='coerce') >= min_terms
    data = fwd.join(stage.rename('stage')).join(t[['score']])
    data = data[ok.reindex(data.index).fillna(False)]
    rows: list[dict] = []
    for name, grp in data.groupby('stage', sort=False):
        rec = {'stage': name, 'n': int(len(grp))}
        for k in horizons:
            e = pd.to_numeric(grp[f'exc{k}'], errors='coerce').dropna()
            r = pd.to_numeric(grp[f'ret{k}'], errors='coerce').dropna()
            if e.empty:
                continue
            rec[f'win{k}'] = round(float((e > 0).mean()) * 100, 1)
            rec[f'exc{k}'] = round(float(e.mean()), 3)
            rec[f'med{k}'] = round(float(e.median()), 3)
            rec[f'p05_{k}'] = round(float(e.quantile(0.05)), 3)
            rec[f'ret{k}'] = round(float(r.mean()), 3)
        rows.append(rec)
    order = {s: i for i, s in enumerate(('ignite', 'follow', 'coil',
                                        'fade', 'none'))}
    rows.sort(key=lambda x: order.get(x['stage'], 99))
    return rows


# ---------------------------------------------------------------- 主流程

#: 暖机：120 日分位 + 20 日窗口，少于这个数算出来的分位是残缺的
WARMUP = 130


def run_sector(start: dt.date | None = None, end: dt.date | None = None, *,
               task_id: int | None = None, dry_run: bool = False,
               echo=None, **over) -> dict:
    """
    算板块启动点。`start` 之前会自动多读 `WARMUP` 天暖机，但只写 start 之后的行。
    """
    say = echo or (lambda *_a, **_k: None)
    p = params(**over)
    lo = None
    if start:
        prev = engine.query(
            'SELECT DISTINCT trade_date FROM market_data.sector_daily '
            ' WHERE trade_date < %s ORDER BY trade_date DESC LIMIT %s',
            (start, WARMUP))
        lo = prev[-1]['trade_date'] if prev else start
    say(f'读热度+板块日线：{lo or "全部"} ~ {end or "最新"}'
        f'（暖机 {WARMUP} 个交易日）')
    df = load_sector(lo, end)
    if df.empty:
        return {'rows': 0, 'error': '没有取到 ys.sector_heat(source=sector)'}
    say(f'  面板 {len(df):,} 行 / '
        f'{df.index.get_level_values("object_key").nunique()} 个板块 / '
        f'{df.index.get_level_values("trade_date").nunique()} 个交易日')

    t = terms(df, p)
    st = stages(t, p)
    if start:
        keep = t.index.get_level_values('trade_date') >= start
        t, st = t[keep], st[keep]
    dist = st.value_counts().to_dict()
    say('  分档：' + '  '.join(f'{k}={v:,}' for k, v in sorted(dist.items())))

    if dry_run:
        return {'rows': 0, 'stages': dist, 'params': p, 'dry_run': True}
    n = write('sector', t, st, task_id=task_id)
    say(f'  落库 {n:,} 行 → ys.ignition(scope=sector)')
    return {'rows': n, 'stages': dist, 'params': p}
