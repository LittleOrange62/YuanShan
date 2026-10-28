# -*- coding: utf-8 -*-
"""
命名查询注册表。

`config.chart.data_query` 里存的是查询的**名字**，不是 SQL。页面渲染时按名字
到这里取实现。这样做的原因：图表可以在页面上配（改类型、改标题、改字段映射），
但取数逻辑必须是受审的代码，不能让配置里塞任意 SQL —— 否则 `/blocks` 页面
就变成了一个远程执行入口，等于把旧系统 `/api/sql/execute` 的漏洞搬过来。

新增查询的方式：在这里 @register 一个函数，然后在 config.chart 里引用它的名字。
"""
from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable

from app import quote_link
from app.compute import explain, pattern, score_trend, sector_trend, vol_double
from app.db import engine
from app.db.repo import quote as quote_repo
from app.db.repo import security as sec_repo

_REGISTRY: dict[str, Callable] = {}


def register(name: str):
    def deco(fn):
        _REGISTRY[name] = fn
        return fn
    return deco


def get(name: str) -> Callable | None:
    return _REGISTRY.get(name)


def names() -> list[str]:
    return sorted(_REGISTRY)


def run(name: str, **params):
    fn = _REGISTRY.get(name)
    if fn is None:
        raise KeyError(f'未注册的命名查询：{name}')
    return fn(**params)


def _d(trade_date=None) -> dt.date | None:
    return trade_date or latest_date()


# 最新交易日被几乎每个查询调一次。一次 30~60ms 不多，一个页面十几张卡片
# 就是半秒白等。TTL 缓存 60 秒 —— 盘后数据一天才变一次，60 秒足够新鲜。
_LATEST: dict[str, object] = {'at': 0.0, 'val': None}
_LATEST_TTL = 60.0


def latest_date():
    now = time.monotonic()
    if _LATEST['val'] is None or now - float(_LATEST['at']) > _LATEST_TTL:
        _LATEST['val'] = sec_repo.latest_trade_date()
        _LATEST['at'] = now
    return _LATEST['val']



# ---------------------------------------------------------------- 行情

@register('q_stock_kline')
def q_stock_kline(ts_code: str = '600519.SH', lookback: int = 120, **_):
    return engine.query(
        """
        SELECT trade_date, open, high, low, close, vol_shares, amount_yuan,
               pct_chg, turnover_rate_pct
          FROM md.stock_daily
         WHERE ts_code = %s
         ORDER BY trade_date DESC
         LIMIT %s
        """, (ts_code, lookback))[::-1]


@register('q_score_history')
def q_score_history(ts_code: str = '600519.SH', lookback: int = 120, **_):
    """
    一只票的评分历史：操作分与 X/Y 两个轴，一行一天。

    **横过来取**（`max(value) FILTER (...)`）而不是一天三行竖着返回：折线图
    要的是"同一天的三条线"，竖着回去还得在 Python 里 pivot 一遍。

    只回有评分的天：没跑评分的日子留空、不补 0 —— 0 分在 -100~100 的值域里
    是"中性"，和"没算"是两个意思，补 0 会在图上画出一条假的回归中线。
    """
    return engine.query(
        """
        SELECT trade_date,
               round(max(value) FILTER (WHERE cap_key = 'oper_score'), 1)
                   AS oper_score,
               round(max(value) FILTER (WHERE cap_key = 'stock_quality'), 1)
                   AS stock_quality,
               round(max(value) FILTER (WHERE cap_key = 'env_resonance'), 1)
                   AS env_resonance,
               round(max(value) FILTER (WHERE cap_key = 'vol_temp'), 1)
                   AS vol_temp,
               max(quadrant) FILTER (WHERE cap_key = 'oper_score') AS quadrant
          FROM fct.score
         WHERE ts_code = %s
           AND cap_key IN ('oper_score','stock_quality','env_resonance','vol_temp')
         GROUP BY trade_date
         ORDER BY trade_date DESC
         LIMIT %s
        """, (ts_code, lookback))[::-1]


@register('q_indicator_history')
def q_indicator_history(ts_code: str = '600519.SH', lookback: int = 120,
                        ind_keys: tuple[str, ...] | list[str] | None = None, **_):
    """
    一只票的指标历史，竖表（一天一指标一行）。

    这里**故意不横过来**：指标是可配的（`config.indicator` 现在 34 条），横过来
    就得把列名写死在 SQL 里，加一个指标又要改代码。画图时按 `ind_key` 分组，
    分几条线由传进来的 `ind_keys` 决定。

    `ind_keys` 不做白名单校验 —— 它是绑定参数（`= ANY(%s)`），传什么都只是
    在 `fct.indicator_value` 里查不到，不构成注入面。
    """
    keys = list(ind_keys or ('chg10_avg', 'amp10_avg', 'amp_pred'))
    return engine.query(
        """
        SELECT trade_date, ind_key, round(value, 3) AS value
          FROM fct.indicator_value
         WHERE ts_code = %(c)s AND ind_key = ANY(%(k)s)
           AND trade_date > (SELECT max(trade_date) FROM fct.indicator_value
                              WHERE ts_code = %(c)s) - %(n)s
         ORDER BY trade_date, ind_key
        """, {'c': ts_code, 'k': keys, 'n': int(lookback) * 2})


@register('q_index_daily')
def q_index_daily(ts_code: str = '000001.SH', lookback: int = 250, **_):
    return engine.query(
        """
        SELECT trade_date, open, high, low, close, pct_chg, amount_yuan
          FROM md.index_daily
         WHERE ts_code = %s
         ORDER BY trade_date DESC LIMIT %s
        """, (ts_code, lookback))[::-1]


@register('q_daily_pct')
def q_daily_pct(trade_date=None, **_):
    d = _d(trade_date)
    return quote_repo.pct_distribution(d) if d else []


@register('q_market_mood')
def q_market_mood(lookback: int = 40, trade_date=None, **_):
    """
    情绪时序。`value` 是给图用的主序列 —— `config.chart.mood_series` 的
    encoding 写的是 `y:["value","ma5"]`，所以这两个列名必须有，否则图是空的。

    `value` 优先用算出来的 `market_mood` 分（-100~100），没算过的日子退回
    上涨占比（0~100）。两个量纲不同，所以 `mood_source` 一起返回，
    页面上能看出这一段是真情绪分还是代理值，不至于把 60 和 -60 混着看。

    `ma5` 是 `value` 的 5 日均线，用来看**趋势**：单看日线在 ±10 分之间
    来回跳，抬头还是掉头得靠均线。**均线跨口径时没有意义** —— 一段里既有
    情绪分又有上涨占比，均值是两个量纲的混合，看 `mood_source` 那一列判断。

    默认窗口 40 个交易日 ≈ 两个月。窗口在卡片配置（`config.chart.params`）里，
    改天数不用改代码。SQL 里取的是窗口的两倍天数，为的是让左端的均线
    也是"喂饱"的，否则第一根点的 ma5 只由它自己一个值算出来。

    **`trade_date` 是曲线的右端点，不是筛选条件。** 复盘首页可以选历史
    交易日回看，那时这条线必须停在所选那天 —— 否则同一页上别的卡片是
    08-26、这条线画到 08-28，看着像数据自相矛盾。
    """
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        """
        WITH breadth AS (
            SELECT trade_date,
                   count(*)                                          AS total,
                   count(*) FILTER (WHERE pct_chg > 0)                AS up,
                   count(*) FILTER (WHERE pct_chg < 0)                AS down,
                   round(count(*) FILTER (WHERE pct_chg > 0)::numeric
                         / nullif(count(*), 0) * 100, 2)              AS up_ratio,
                   round(sum(amount_yuan) / 1e8, 1)                   AS amount_yi
              FROM md.stock_daily
             WHERE trade_date <= %(d)s
               AND trade_date > %(d)s::date - (%(days)s || ' days')::interval
             GROUP BY trade_date
        ),
        mood AS (
            SELECT trade_date, round(avg(value), 2) AS mood
              FROM fct.score
             WHERE cap_key = 'market_mood' AND trade_date <= %(d)s
             GROUP BY trade_date
        ),
        joined AS (
            SELECT b.trade_date,
                   coalesce(m.mood, b.up_ratio)                 AS value,
                   CASE WHEN m.mood IS NULL THEN 'up_ratio'
                        ELSE 'market_mood' END                  AS mood_source,
                   b.total, b.up, b.down, b.up_ratio, b.amount_yi
              FROM breadth b LEFT JOIN mood m USING (trade_date)
        )
        SELECT trade_date, value,
               round(avg(value) OVER (ORDER BY trade_date
                                      ROWS BETWEEN 4 PRECEDING
                                               AND CURRENT ROW), 2) AS ma5,
               mood_source, total, up, down, up_ratio, amount_yi
          FROM joined
         ORDER BY trade_date
        """, {'d': d, 'days': lookback * 2})[-lookback:]


@register('q_mood_recent')
def q_mood_recent(trade_date=None, days: int = 7, **_):
    """
    最近 N 个交易日的情绪**震荡**明细，最近的在最上面。

    两个月的曲线回答"在往上还是往下"，这张表回答"最近几天是在震还是在走"：
    `日变化` 正负交替就是震荡，连着同号就是单边。所以这里给的是逐日的
    情绪分与它的日变化，而不是再画一条更短的线 —— 七个点的折线看不出
    这个区别，七行数字看得出。

    **跨口径的日变化留空**：某一天用的是真情绪分、前一天退回上涨占比时，
    两者量纲不同，相减出来是个假的"暴涨暴跌"。宁可空着，也不给假数。

    直接复用 `q_market_mood`，不另写一段 SQL —— 两张卡片的情绪分必须是
    同一个口径，各写一遍迟早会漂。
    """
    rows = q_market_mood(lookback=days + 1, trade_date=trade_date)
    if not rows:
        return []
    out = []
    # 从第二根开始：第一根只是用来算第二根的日变化的
    for prev, cur in zip(rows, rows[1:], strict=False):
        v, pv = cur.get('value'), prev.get('value')
        same = cur.get('mood_source') == prev.get('mood_source')
        out.append({
            'trade_date': cur['trade_date'],
            'value': v,
            'delta': (round(v - pv, 2)
                      if same and v is not None and pv is not None else None),
            'up_ratio': cur.get('up_ratio'),
            'amount_yi': cur.get('amount_yi'),
            'mood_source': cur.get('mood_source'),
        })
    return out[::-1]



@register('q_top_movers')
def q_top_movers(trade_date=None, limit: int = 20, **_):
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        """
        SELECT d.ts_code, s.name, s.board, d.close, d.pct_chg,
               round(d.amount_yuan / 1e8, 2) AS amount_yi, d.turnover_rate_pct
          FROM md.stock_daily d
          JOIN ref.security s ON s.ts_code = d.ts_code
         WHERE d.trade_date = %s AND s.asset_type = 'stock'
         ORDER BY d.pct_chg DESC NULLS LAST
         LIMIT %s
        """, (d, limit))


@register('q_sector_rank')
def q_sector_rank(trade_date=None, limit: int = 20, **_):
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        """
        SELECT sd.sector_code, i.name, i.sector_type, sd.close, sd.pct_chg,
               round(sd.amount_yuan / 1e8, 2) AS amount_yi, sd.member_count
          FROM md.sector_daily sd
          LEFT JOIN ref.sector_info i ON i.sector_code = sd.sector_code
         WHERE sd.trade_date = %s
         ORDER BY sd.pct_chg DESC NULLS LAST
         LIMIT %s
        """, (d, limit))


@register('q_quadrant')
def q_quadrant(trade_date=None, **_):
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        """
        SELECT x.ts_code, s.name,
               x.value AS x_score, y.value AS y_score,
               o.value AS oper_score, o.quadrant
          FROM fct.score x
          JOIN fct.score y ON y.ts_code = x.ts_code
                          AND y.trade_date = x.trade_date
                          AND y.cap_key = 'env_resonance'
          LEFT JOIN fct.score o ON o.ts_code = x.ts_code
                               AND o.trade_date = x.trade_date
                               AND o.cap_key = 'oper_score'
          JOIN ref.security s ON s.ts_code = x.ts_code
         WHERE x.cap_key = 'stock_quality' AND x.trade_date = %s
         LIMIT 3000
        """, (d,))


@register('q_quadrant_strong')
def q_quadrant_strong(trade_date=None, min_x: float = 30, min_y: float = 30,
                      limit: int = 500, **_):
    """
    四象限里 **X 和 Y 同时过线** 的票（默认都 > 30），按 X+Y 从高到低。

    这就是散点图右上角 Q1 里最靠外的那一簇 —— 图上能看出"有一堆"，
    但看不出是哪些、分别多少分，所以单独列成表。

    两条线是 **AND**，不是 OR：X 高 Y 低是"好票在冷板块"，Y 高 X 低是
    "热板块里的差票"，这两类都不是这张表要回答的问题。

    `xy_total` 是 X+Y 的直接相加，**和操作分不是一回事** —— 操作分是
    0.40·X + 0.60·Y 的加权，Y 的话语权更大。两列都给出来，排序用前者
    （用户要的口径），需要按加权看就点 `操作分` 那一列排序。
    """
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        """
        SELECT x.ts_code, s.name,
               round(x.value, 2)             AS x_score,
               round(y.value, 2)             AS y_score,
               round(x.value + y.value, 2)   AS xy_total,
               round(o.value, 2)             AS oper_score,
               o.quadrant, sd.close, sd.pct_chg,
               round(sd.amount_yuan / 1e8, 2) AS amount_yi
          FROM fct.score x
          JOIN fct.score y ON y.ts_code = x.ts_code
                          AND y.trade_date = x.trade_date
                          AND y.cap_key = 'env_resonance'
          LEFT JOIN fct.score o ON o.ts_code = x.ts_code
                               AND o.trade_date = x.trade_date
                               AND o.cap_key = 'oper_score'
          JOIN ref.security s ON s.ts_code = x.ts_code
          LEFT JOIN md.stock_daily sd ON sd.ts_code = x.ts_code
                                     AND sd.trade_date = x.trade_date
         WHERE x.cap_key = 'stock_quality' AND x.trade_date = %(d)s
           AND x.value > %(mx)s AND y.value > %(my)s
         ORDER BY (x.value + y.value) DESC
         LIMIT %(lim)s
        """, {'d': d, 'mx': min_x, 'my': min_y, 'lim': limit})


@register('q_quadrant_counts')
def q_quadrant_counts(trade_date=None, **_):
    """
    当日四个象限各有多少只票。选票框上的四个按钮要带只数，不然点进去
    才知道那个象限是空的。

    **象限的定义不靠注释、靠符号**（实测 2026-08-28 逐条核对过）：
    `Q1` X≥0 且 Y≥0、`Q2` X≥0 且 Y<0、`Q3` X<0 且 Y≥0、`Q4` X<0 且 Y<0。
    这里顺手把 X / Y 的符号统计也返回，能当"标签有没有写错"的体检。
    """
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        """
        SELECT o.quadrant,
               count(*) AS stocks,
               count(*) FILTER (WHERE x.value >= 0) AS x_pos,
               count(*) FILTER (WHERE y.value >= 0) AS y_pos,
               round(avg(o.value), 1) AS avg_oper
          FROM fct.score o
          JOIN fct.score x ON x.ts_code = o.ts_code
                          AND x.trade_date = o.trade_date
                          AND x.cap_key = 'stock_quality'
          JOIN fct.score y ON y.ts_code = o.ts_code
                          AND y.trade_date = o.trade_date
                          AND y.cap_key = 'env_resonance'
         WHERE o.cap_key = 'oper_score' AND o.trade_date = %s
         GROUP BY 1
         ORDER BY 1
        """, (d,))


# 允许的排序列。**白名单，不接受任意字符串** —— 排序列拼进 SQL 里，
# 放开就是注入面；下拉里能选的只有这五个。
QUADRANT_SORTS: dict[str, str] = {
    'oper_score': 'o.value',
    'xy_total': '(x.value + y.value)',
    'x_score': 'x.value',
    'y_score': 'y.value',
    'pct_chg': 'sd.pct_chg',
    'amount_yi': 'sd.amount_yuan',
}


@register('q_quadrant_pick')
def q_quadrant_pick(trade_date=None, quadrant: str = 'Q1',
                    min_x: float | None = None, min_y: float | None = None,
                    limit: int = 200, sort: str = 'oper_score', **_):
    """
    **按象限选票**：给定象限（默认 `Q1`，也就是 X 和 Y 都 ≥ 0 那一格）里的票。

    和 `q_quadrant_strong` 的分工：那个只回答"右上角最靠外的一簇"（X、Y 都
    过 30 的硬门槛），这个能选**任意一个象限**、门槛自己定 —— Q3 里也有值得
    试探的票（板块热、个股暂时弱），写死只看 Q1 就看不到。

    `min_x` / `min_y` 传 None 就只按象限过，不再加门槛：象限本身已经定了
    符号，再默认卡个 30 会让 Q1 只剩几十只，"先看全貌再收紧"这一步就没了。

    排序列走 `QUADRANT_SORTS` 白名单。**不能直接拼用户给的字符串** ——
    这里的列名是拼进 SQL 的，不是绑定参数。
    """
    d = _d(trade_date)
    if not d:
        return []
    col = QUADRANT_SORTS.get(sort or 'oper_score')
    if col is None:
        raise ValueError(f'不支持的排序列：{sort}，可选 {list(QUADRANT_SORTS)}')
    rows = engine.query(
        f"""
        SELECT x.ts_code, s.name,
               round(x.value, 2)             AS x_score,
               round(y.value, 2)             AS y_score,
               round(x.value + y.value, 2)   AS xy_total,
               round(o.value, 2)             AS oper_score,
               o.quadrant,
               -- 量能阶段存在 fct.score 里（cap_key='vol_stage'，值是 1..5 的
               -- 编码），不是独立表。口径与 q_review_stocks 那一列保持一致。
               CASE vs.value::int WHEN 1 THEN '冷却' WHEN 2 THEN '酝酿'
                                  WHEN 3 THEN '高潮' WHEN 4 THEN '背离'
                                  WHEN 5 THEN '退潮' END AS vol_stage,
               sd.close, sd.pct_chg,
               round(sd.amount_yuan / 1e8, 2) AS amount_yi,
               round(sd.turnover_rate_pct, 2) AS turnover_rate_pct
          FROM fct.score x
          JOIN fct.score y ON y.ts_code = x.ts_code
                          AND y.trade_date = x.trade_date
                          AND y.cap_key = 'env_resonance'
          JOIN fct.score o ON o.ts_code = x.ts_code
                          AND o.trade_date = x.trade_date
                          AND o.cap_key = 'oper_score'
          JOIN ref.security s ON s.ts_code = x.ts_code
          LEFT JOIN md.stock_daily sd ON sd.ts_code = x.ts_code
                                     AND sd.trade_date = x.trade_date
          LEFT JOIN fct.score vs ON vs.ts_code = x.ts_code
                                AND vs.trade_date = x.trade_date
                                AND vs.cap_key = 'vol_stage'
         WHERE x.cap_key = 'stock_quality' AND x.trade_date = %(d)s
           AND o.quadrant = %(q)s
           -- 门槛可以不给（None＝不加这一条）。**必须显式 ::numeric** ——
           -- 参数只出现在 `IS NULL` 和比较里，PG 推不出类型，会报
           -- `could not determine data type of parameter`（刚栽过）。
           AND (%(mx)s::numeric IS NULL OR x.value >= %(mx)s::numeric)
           AND (%(my)s::numeric IS NULL OR y.value >= %(my)s::numeric)
         ORDER BY {col} DESC NULLS LAST
         LIMIT %(lim)s
        """, {'d': d, 'q': quadrant, 'mx': min_x, 'my': min_y, 'lim': limit})
    for r in rows:
        r['detail'] = quote_link.detail_link(r['ts_code'])
    return rows


# ---------------------------------------------------------------- 四象限趋势
# 象限的「好坏次序」。**不是 Q 编号的大小**：Q1 双正最好，Q3（板块热、个股
# 暂时弱）好于 Q2（票好、没风口），Q4 最差 —— 与 /scores 页面按钮的顺序一致。
# 用来判断 10 日前 → 今天是「升格」还是「降格」。
QUAD_ORDER: dict[str, int] = {'Q4': 0, 'Q2': 1, 'Q3': 2, 'Q1': 3}

# 形态判定的三个阈值。
#   TREND_FLAT  趋势分绝对值小于它就当没方向（10 日累计变化不到 1 分）
#   R2_TIGHT    拟合度分界：≥0.5 叫「稳步」，否则叫「震荡」
#   FADE_GAP    今天的 X+Y 比 10 日最高低这么多分，就先叫「冲高回落」
TREND_FLAT, R2_TIGHT, FADE_GAP = 1.0, 0.5, -15.0

# 趋势表的排序白名单。**不接受任意字符串** —— 拼进 SQL 的东西必须是白名单。
# 列名是 `fct.score_trend` 的列（现算回退那条路输出同名列，见
# `app/compute/score_trend.py` 的 CORE_SQL），所以两条路排序结果一致。
QUADRANT_TREND_SORTS: dict[str, str] = {
    'trend_score':  'g.slope',
    'pred_oper':    '(g.intercept + g.slope * (g.n_points + 1))',
    'r2':           '(g.r * g.r)',
    'oper_score':   'o.value',
    'xy_total':     '(x.value + y.value)',
    'xy_max10':     'g.xy_max',
    'gap_from_max': '(x.value + y.value - g.xy_max)',
    'up_streak':    'g.up_streak',
}


def _scored_day(trade_date=None):
    """
    **最近一个有评分的交易日**（≤ 给定日期，不给就是库里最新的）。

    为什么不用 `_d()`：`latest_date()` 读的是 `ref.v_latest_trade_date`，也就是
    **日历上最新的已收盘交易日**（15:00 之后就算当天），跟"库里有没有这天的数据"
    没关系。实测 2026-08-31 15:50 它给的是 08-31，而 `md.stock_daily` 和
    `fct.score` 都只到 08-28 —— 当日链路还没跑。趋势表按 08-31 去 JOIN
    `fct.score` 一行都对不上，卡片会静默显示"无数据"，看起来像功能坏了。

    所以评分类查询要以**评分自己的最新日**为基准，并且把请求日向下取整到有分的
    那天：宁可显示"这是 08-28 的"，也不要给一张空表。
    """
    return engine.scalar(
        """
        SELECT max(trade_date) FROM fct.score
         WHERE cap_key = 'oper_score'
           AND (%(d)s::date IS NULL OR trade_date <= %(d)s::date)
        """, {'d': trade_date})


# 落库那条路的取数：一次索引扫（`ix_score_trend_date`），没有窗口计算。
# 列名与 `score_trend.CORE_SQL` 的输出**逐列同名** —— 外层那条 SELECT 两边共用，
# 名字对不上就会在切换到回退路径时才炸，而那正是最需要它能跑的时候。
_TREND_STORED = """
            SELECT ts_code, n_points, slope, intercept, r,
                   xy_max, xy_max_date, up_streak, quad_first, spark
              FROM fct.score_trend
             WHERE trade_date = %(d)s AND win = %(win)s
"""


def _trend_is_stored(d, win: int) -> bool:
    """
    这一天这个窗口有没有落库。**不看"表里有没有行"而看"这一天有没有行"**：
    日常链路今天还没跑时表里全是昨天的，读表会让整张卡显示昨天的趋势配今天的
    切片，比慢 1 秒危险得多。
    """
    return bool(engine.scalar(
        'SELECT 1 FROM fct.score_trend '
        ' WHERE trade_date = %(d)s AND win = %(win)s LIMIT 1',
        {'d': d, 'win': int(win)}))


def trend_shape(trend, r2, gap) -> str:
    """
    趋势分 + 拟合度 + 距最高 → 一句话形态。

    **判定有先后，顺序就是语义**：

    1. 趋势分绝对值 < `TREND_FLAT` → `横盘`。方向都没有，谈"稳不稳"没意义。
    2. `距最高 <= FADE_GAP` → `冲高回落`。**这一条必须排在"上行"前面** ——
       一只票 10 日里前 8 天一路上、最后两天塌回去，最小二乘算出来的斜率仍然
       是正的，只看趋势分会把它标成「稳步上行」，而它当下已经在回落。
    3. 趋势分 > 0：拟合度 ≥ `R2_TIGHT` 叫 `稳步上行`，否则 `震荡上行`。
    4. 趋势分 < 0：同理 `稳步下行` / `震荡下行`。

    为什么要拟合度这一列：斜率只说"首尾连线往哪斜"，不说中间有多颠。
    `+8 分 / R²=0.9` 是一条斜线，`+8 分 / R²=0.1` 是一团噪声恰好收在高位，
    这两种在排行榜上会挨着，但能不能跟完全不是一回事。
    """
    t = float(trend or 0.0)
    rr = float(r2 or 0.0)
    g = float(gap if gap is not None else 0.0)
    if abs(t) < TREND_FLAT:
        return '横盘'
    if g <= FADE_GAP:
        return '冲高回落'
    if t > 0:
        return '稳步上行' if rr >= R2_TIGHT else '震荡上行'
    return '稳步下行' if rr >= R2_TIGHT else '震荡下行'


@register('q_quadrant_trend')
def q_quadrant_trend(trade_date=None, win: int = 10, min_n: int = 8,
                     quadrant: str | None = None, limit: int = 300,
                     sort: str = 'trend_score', **_):
    """
    **四象限趋势分**：一行一只票，回答"这只票近 `win` 个交易日的 X/Y 在往哪走"。

    和 `q_quadrant_pick` 的分工：那张表是**当天的切片**（今天在哪一格、多少分），
    这张表是**一段时间的方向**。同样 60 分的票，一个是从 20 分爬上来的、一个是
    从 90 分掉下来的，切片里长得一模一样。

    三个主角（谁都不能单看）：

    - `trend_score` = 最小二乘斜率 × `win`，也就是**整个窗口的累计变化分**。
      口径与自选组页的「趋势分数」一致（那边是 `b × 10`），都用 PG 的
      `regr_slope`，不把序列拉回 Python。
    - `r2` = `corr(oper, rn)²`，趋势有多"直"。低 R² 的高趋势分不可信，
      见 `trend_shape()` 的说明。
    - `quad_path` = 窗口第一天的象限 → 今天的象限（如 `Q4→Q1`），
      `quad_up` 是它有没有升格（次序见 `QUAD_ORDER`，不是 Q 编号大小）。

    还有两列专门回答"是不是已经回落了"：`xy_max10`（窗口内 X+Y 的最高值）和
    `gap_from_max`（今天 − 最高，负得越多回落越深）。用户原话是"近 10 日 xy
    总分和最大"，但**光有最大值会误导** —— 最大值出现在第 2 天还是今天，
    含义完全相反，所以配了 `xy_max_date` 和差值一起给。

    `spark` 是 `▁▂▃▅▇` 块字符拼的迷你走势（窗口内按操作分在自身
    min~max 之间归一化）。DataTable 画不了图，块字符是零成本的替代 ——
    **它只表示形状，不表示绝对高低**：两只票的 `▇` 不是同一个分数。

    参数：`win` 窗口长度（按**有评分的交易日**数，不是日历日）；
    `min_n` 窗口内至少几天有分（挡掉新股和长停 —— 3 天数据拟合出来的斜率
    没有意义）；`quadrant` 传 None 就是全市场，传 `Q1` 只看那一格。

    **2026-08-31 改成读 `fct.score_trend`**（落库预算）。现算一次 1.0s，而
    `/scores` 是服务端一次性渲染，这 1.0s 直接加在首屏上；趋势是当天收盘后就定死
    的历史值，每次进页面重算是纯浪费。库里没有那天那个窗口时**自动回退现算**，
    并且两条路共用 `score_trend.CORE_SQL` 这一份 SQL —— 各写一份的话，落库值和
    现算值会慢慢漂开，而页面上两条路看起来一模一样，这种错发现不了。
    """
    d = _scored_day(trade_date)
    if not d:
        return []
    col = QUADRANT_TREND_SORTS.get(sort or 'trend_score')
    if col is None:
        raise ValueError(f'不支持的排序列：{sort}，'
                         f'可选 {list(QUADRANT_TREND_SORTS)}')
    src = _TREND_STORED if _trend_is_stored(d, win) else score_trend.CORE_SQL
    rows = engine.query(
        f"""
        SELECT g.ts_code, s.name, o.quadrant,
               round((g.slope * %(win)s::int)::numeric, 2)       AS trend_score,
               round((g.r * g.r)::numeric, 2)                    AS r2,
               round((g.intercept
                      + g.slope * (g.n_points + 1))::numeric, 1) AS pred_oper,
               round(o.value, 1)                                AS oper_score,
               round(x.value, 1)                                AS x_score,
               round(y.value, 1)                                AS y_score,
               round(x.value + y.value, 1)                      AS xy_total,
               round(g.xy_max, 1)                               AS xy_max10,
               g.xy_max_date,
               round(x.value + y.value - g.xy_max, 1)           AS gap_from_max,
               g.up_streak,
               g.quad_first || '→' || o.quadrant                AS quad_path,
               g.spark,
               g.n_points                                       AS trend_days
          FROM ({src}) g
          JOIN fct.score o ON o.ts_code = g.ts_code
                          AND o.trade_date = %(d)s
                          AND o.cap_key = 'oper_score'
          JOIN fct.score x ON x.ts_code = g.ts_code
                          AND x.trade_date = %(d)s
                          AND x.cap_key = 'stock_quality'
          JOIN fct.score y ON y.ts_code = g.ts_code
                          AND y.trade_date = %(d)s
                          AND y.cap_key = 'env_resonance'
          JOIN ref.security s ON s.ts_code = g.ts_code
                             AND s.asset_type = 'stock'
         -- `min_n` 在这里过滤而不在落库时过滤：表里如实存 `n_points`
         -- （3 天也算一条斜率，但那不是 10 日趋势），要不要看是展示层的事。
         WHERE g.n_points >= %(min_n)s
         -- 象限可以不给（None＝全市场）。必须显式 ::text，否则 PG 推不出
         -- 参数类型（只出现在 IS NULL 和等值比较里），这个坑刚踩过一次。
           AND (%(q)s::text IS NULL OR o.quadrant = %(q)s::text)
         -- **必须带 ts_code 兜底**：排序列几乎都是 round 过的两位小数，同分的票
         -- 一抓一把（2026-09-01 实测 trend_score=49.87 上就并着好几只）。只按
         -- 排序列排，PG 给同分行的物理顺序是不保证的 —— 表现为"同一天同一张表
         -- 刷两次，第 283 行换了一只票"，而落库路和现算路更是必然排不出同一个
         -- 顺序（`test_stored_and_live_paths_agree_row_for_row` 就是这么红的）。
         ORDER BY {col} DESC NULLS LAST, g.ts_code
         LIMIT %(lim)s
        """, {'d': d, 'win': win, 'min_n': min_n, 'q': quadrant, 'lim': limit})
    for r in rows:
        first, _, last = str(r['quad_path']).partition('→')
        # 升格标记**用符号而不是 True/False**：一是页面上 `↑` 比 `true` 好读，
        # 二是 DataTable 的 `filter_query` 对布尔列的写法在版本之间不稳，
        # 而文本等值（`{quad_up} = "↑"`）是文档里明确支持的 —— 配色规则匹配不上
        # 是**静默失效**（页面不报错，只是不亮），不能赌。
        r['quad_up'] = ('↑' if QUAD_ORDER.get(last, -1) > QUAD_ORDER.get(first, -1)
                        else '')
        r['shape'] = trend_shape(r['trend_score'], r['r2'], r['gap_from_max'])
        r['detail'] = quote_link.detail_link(r['ts_code'])
    # 列序＝看的顺序：先"往哪走、稳不稳"，再当日切片，元信息压到最后。
    order = ('ts_code', 'name', 'quadrant', 'trend_score', 'r2', 'shape',
             'spark', 'pred_oper', 'oper_score', 'x_score', 'y_score',
             'xy_total', 'xy_max10', 'xy_max_date', 'gap_from_max',
             'up_streak', 'quad_path', 'quad_up', 'trend_days', 'detail')
    return [{k: r[k] for k in order} for r in rows]


@register('q_score_dates')
def q_score_dates(limit: int = 30, **_):
    """
    有评分的交易日，最近的在前。复盘首页的日期下拉用它。

    **判据取 `fct.score` 而不是 `md.stock_daily`**：有行情不等于算过评分，
    四象限是评分的产物，列出没评分的日子只会让人选完看到一张空图。

    只扫最近一段（按最新评分日往前推 `limit * 3` 天），不对 120 万行做
    全表 GROUP BY。
    """
    return engine.query(
        """
        SELECT trade_date, count(*) AS stocks
          FROM fct.score
         WHERE cap_key = 'oper_score'
           AND trade_date > (SELECT max(trade_date) FROM fct.score)
                            - (%(days)s || ' days')::interval
         GROUP BY trade_date
         ORDER BY trade_date DESC
         LIMIT %(lim)s
        """, {'days': limit * 3, 'lim': limit})


# ---------------------------------------------------------------- 运维

@register('q_coverage')
def q_coverage(lookback: int = 90, **_):
    # **必须带 trade_date 下界。** 不带的话这条 GROUP BY 会扫完 md.stock_daily
    # 全部 950 万行再 LIMIT，实测 3.3 秒；加了下界就只碰到最近一两个年度分区，
    # 降到几十毫秒。`LIMIT` 是在聚合之后才生效的，救不了扫全表。
    d = _d()
    if d is None:
        return []
    return engine.query(
        """
        SELECT trade_date, count(*) AS stocks,
               count(*) FILTER (WHERE turnover_rate_pct IS NOT NULL) AS has_turnover,
               count(*) FILTER (WHERE limit_up IS NOT NULL)          AS has_limit
          FROM md.stock_daily
         WHERE trade_date > %s::date - (%s * 2 || ' days')::interval
         GROUP BY trade_date ORDER BY trade_date DESC LIMIT %s
        """, (d, lookback, lookback))[::-1]



@register('q_table_rows')
def q_table_rows(**_):
    return engine.query(
        """
        SELECT schemaname AS schema, relname AS table,
               n_live_tup AS approx_rows
          FROM pg_stat_user_tables
         WHERE schemaname IN ('md','ref','fct','config','ops')
           AND n_live_tup > 0
         ORDER BY n_live_tup DESC LIMIT 40
        """)


@register('q_recent_batches')
def q_recent_batches(limit: int = 20, **_):
    return engine.query(
        """
        SELECT batch_id, source, target, status, row_count,
               started_at, finished_at, left(coalesce(message,''), 120) AS message
          FROM ops.ingest_batch ORDER BY batch_id DESC LIMIT %s
        """, (limit,))


@register('q_quality_recent')
def q_quality_recent(limit: int = 50, **_):
    # 带上规则中文名与 actual：只看 rule_key 和 passed 的话，红一行也不知道
    # "差在哪、差多少"，还得自己去查 config.quality_rule。
    return engine.query(
        """
        SELECT q.trade_date AS 交易日, r.name AS 规则, q.rule_key AS 规则key,
               q.severity AS 级别, q.passed AS 通过,
               left(q.actual::text, 120) AS 实测, q.run_at AS 跑的时间
          FROM ops.quality_result q
          LEFT JOIN config.quality_rule r USING (rule_key)
         ORDER BY q.run_at DESC, q.passed, q.rule_key LIMIT %s
        """, (limit,))


@register('q_audit_recent')
def q_audit_recent(limit: int = 50, **_):
    return engine.query(
        """
        SELECT at, actor, channel, action, target, rows_affected, ok,
               left(coalesce(error, ''), 120) AS error
          FROM ops.audit ORDER BY at DESC LIMIT %s
        """, (limit,))


def _term_brief(t: dict) -> str:
    """子项摆成 `名字 0.4`。没写内部权重的只给名字，不补 0（0 会被读成"不算"）。"""
    w = t.get('w')
    return f"{t.get('name')}" if w is None else f"{t.get('name')} {float(w):g}"


@register('q_effective_binding')
def q_effective_binding(**_):
    """
    当前有效权重，**每个脚本带上它自己的内容说明**。

    原来这张表只有 `功能点 / 脚本 / 顺序 / 原始权重 / 有效权重` 五列 ——
    看得出"形态占 0.25"，看不出"形态"到底在算什么。而说明本来就有：每个
    `app/compute/builtin/*.py` 里都写着 `EXPLAIN`（判据 / 子项 / 合成方式），
    「评分归因」那张卡在用。这里 join 上来，权重表就成了"这一套评分由哪些脚本
    组成、各自干什么、各占多少"的一页答案。

    三列的口径：
        判据    `EXPLAIN['basis']`，一句话说清这个脚本在判什么
        子项    `EXPLAIN['terms']` 的名字 + 内部权重（脚本**内部**的分配，
                和这一行的 `有效权重` 不是一回事 —— 后者是脚本在功能点里的占比）
        合成    `EXPLAIN['combine']`，几个子项怎么合成一个分值

    **说明来自代码不是注册表**：它描述的是代码怎么算，塞进 `config.script`
    只会多一份能和代码对不上的副本（`tests/test_explain.py` 拿源码核对 `expr`
    与内部权重，改了公式忘改说明会红）。所以这里在 Python 侧拼，不在 SQL 里 join。

    没有 `EXPLAIN` 的脚本三列留空 —— 空着是"这个脚本还没写说明"的诚实信号，
    填个"（无）"反而看不出缺口。
    """
    rows = engine.query(
        """
        SELECT cap_key, script_key, script_name, seq,
               round(raw_weight, 4) AS raw_weight,
               round(eff_weight, 4) AS eff_weight
          FROM config.v_effective_binding
        """)
    ex_map = explain.explain_map([r['script_key'] for r in rows])
    for r in rows:
        ex = ex_map.get(r['script_key']) or {}
        terms = ex.get('terms') or ()
        r['basis'] = ex.get('basis')
        # 子项列摆成 `名字 0.4 ／ 名字 0.6`：一行一个子项会把 17 行的表撑成
        # 五十几行，而这一列是"里面有什么"的概览，不是明细（明细在评分归因页）。
        r['terms_brief'] = ' ／ '.join(_term_brief(t) for t in terms) or None
        r['combine'] = ex.get('combine')
        r['caveat'] = ex.get('caveat')
    return rows


def _trend_label(win: int) -> str:
    """win=1 是"日趋势分值"（今日热度 − 昨日热度），其余是 N 日斜率。"""
    return '日趋势' if int(win) == 1 else f'{int(win)}日趋势'


@register('q_sector_heat')
def q_sector_heat(trade_date=None, limit: int = 20, ma_n: int = 5,
                  include_style: bool = False, **_):
    """
    板块热度榜。`value` 是 `sector_heat` 分，不是涨幅。**取的是最新有数据的
    交易日**（`latest_trade_date()`），不是日历上的今天 —— 盘后数据没摄进来
    之前，"今天"这一列压根不存在，硬按日历日期查只会得到空表。

    热度和趋势都**直接读预计算表**（`fct.sector_heat_daily` /
    `fct.sector_trend`，由 `scripts/compute/d3_sector_trend.py` 落库）。
    以前是每次查询现场从成员股 `fct.score[sector_heat]` 聚回板块 ——
    同一个数在三处 SQL 里各聚一遍，改一次口径要盯三个地方，而且慢。

    **只读 `src='sector'`**（板块级直算口径，2026-08-30 起）。表里可能还留着
    `src='member_agg'` 的旧行，那个口径是错的（摊下去用唯一主板块、聚回来用全部
    归属，等于把别的板块的热度记到自己头上），混进来会让同一列有两种含义。


    趋势列：
        日趋势     今日热度 − 上一交易日热度（= 两点斜率）
        N 日趋势   最近 N 个交易日热度的**最小二乘斜率**，单位分/天
                   向上为正、向下为负、0 为走平
        趋势       按日趋势给的文字档位，阈值 ±3 分
        偏离均值   今日热度 − 近 ma_n 日均值（正=还在加速，负=已经在退）

    ★**斜率不是"(今日 − N 日前) ÷ N"**：那个式子只用首尾两点，中间怎么走全丢，
    一根插针就能把趋势判反。窗口列表来自 `config.setting['sector_trend.windows']`，
    加一个 13 日趋势改那个数组就行，这里的列跟着自动多一个。

    点数不足窗口长度的格子**留空不补 0**（`fct.sector_trend.n_points` 记着真
    用了几个点）：3 个点也能算出斜率，但那不是 21 日趋势。

    没算过评分的日子退回按涨幅排 —— 页面宁可显示涨幅榜，也不要空白。
    """
    d = _d(trade_date)
    if not d:
        return []
    wins = sector_trend.windows()
    # 窗口值来自配置但已经在 windows() 里整数化+范围校验，这里只做拼装
    pivot = ',\n'.join(
        f'round(max(slope) FILTER (WHERE win = {w} AND n_points >= '
        f'{2 if w == 1 else w}), 2) AS "{_trend_label(w)}"' for w in wins)
    trend_cols = ',\n'.join(f'tr."{_trend_label(w)}"' for w in wins)
    rows = engine.query(
        f"""
        WITH days AS (
            SELECT DISTINCT trade_date
              FROM fct.sector_heat_daily
             WHERE trade_date <= %(d)s
               AND src = 'sector'
             ORDER BY trade_date DESC
             LIMIT %(n)s
        ),
        agg AS (
            SELECT h.sector_code,
                   avg(h.heat)                                        AS ma,
                   max(h.heat)    FILTER (WHERE h.trade_date = %(d)s) AS v0,
                   max(h.n_parts) FILTER (WHERE h.trade_date = %(d)s) AS parts
              FROM fct.sector_heat_daily h
              JOIN days ON days.trade_date = h.trade_date
             WHERE h.src = 'sector'
             GROUP BY h.sector_code
        ),

        tr AS (
            SELECT sector_code,
                   {pivot}
              FROM fct.sector_trend
             WHERE trade_date = %(d)s
             GROUP BY sector_code
        )
        SELECT sd.sector_code, i.name, i.sector_type,
               round(a.v0, 2)                                AS value,
               -- 阶段：把 fct.ignition 的四档拉进明细表（2026-09-01 加）。
               -- 之前这张表只有"多热、在升还是在降"，答不上"是刚起来还是
               -- 已经涨完"—— 而那个判断早就算好落库了，只是没有一处显示。
               CASE ig.stage WHEN 'ignite' THEN '① 点火'
                             WHEN 'follow' THEN '② 延续'
                             WHEN 'coil'   THEN '③ 蓄势'
                             WHEN 'fade'   THEN '④ 退潮'
                             ELSE '⑤ 无' END                 AS 阶段,
               {trend_cols},
               CASE WHEN tr."日趋势" IS NULL     THEN '—'
                    WHEN tr."日趋势" >=  3       THEN '↑ 上升'
                    WHEN tr."日趋势" <= -3       THEN '↓ 下降'
                    ELSE '→ 走平' END                        AS 趋势,
               round(a.v0 - a.ma, 2)                         AS 偏离均值,
               sd.pct_chg,
               round(sd.amount_yuan / 1e8, 2)                AS amount_yi,
               sd.up_count, sd.down_count, sd.member_count,
               a.parts                                       AS 有效分项

          FROM md.sector_daily sd
          JOIN ref.sector_info i ON i.sector_code = sd.sector_code
          JOIN agg a             ON a.sector_code = sd.sector_code
          LEFT JOIN tr           ON tr.sector_code = sd.sector_code
          LEFT JOIN fct.ignition ig ON ig.obj_key = sd.sector_code
                                   AND ig.scope = 'sector'
                                   AND ig.trade_date = %(d)s
         WHERE sd.trade_date = %(d)s
           AND a.v0 IS NOT NULL
           AND i.name !~ '^[0-9]+$'
           AND (%(style)s OR i.sector_type IN ('行业', '概念'))
         ORDER BY a.v0 DESC
         LIMIT %(lim)s
        """, {'d': d, 'n': max(2, int(ma_n)), 'lim': limit,
              'style': bool(include_style)})
    if rows:
        return rows
    return engine.query(
        """
        SELECT sd.sector_code, i.name, i.sector_type,
               sd.pct_chg                     AS value,
               NULL::numeric                  AS 日趋势,
               '（当日无评分）'                AS 趋势,
               NULL::numeric                  AS 偏离均值,
               sd.pct_chg,
               round(sd.amount_yuan / 1e8, 2) AS amount_yi,
               sd.up_count, sd.down_count, sd.member_count
          FROM md.sector_daily sd
          JOIN ref.sector_info i ON i.sector_code = sd.sector_code
         WHERE sd.trade_date = %s
           AND i.name !~ '^[0-9]+$'
           AND i.sector_type IN ('行业', '概念')
         ORDER BY sd.pct_chg DESC NULLS LAST
         LIMIT %s
        """, (d, limit))


@register('q_sector_heat_trend')
def q_sector_heat_trend(trade_date=None, lookback: int = 10, top: int = 6, **_):
    """
    板块热度趋势：热度前 top 个板块最近 lookback 个交易日的热度曲线。

    热度榜只有当天一个截面，看不出"这个板块是刚起来还是快退了"。这条查询
    返回长表（trade_date / name / value），由 `ui._line` 按 `encoding.color`
    分组画多条线。

    **窗口 2026-09-01 从 20 改成 10（用户指定）**：20 个点铺在一张 360px 高的
    图里，近几天的转折被压成一小段，"接下来往哪走"反而看不出来 —— 而这张图
    要的就是最近的斜率。要看更长的历史，`板块明细` 那张表里有 3/5/10/21 日
    多个窗口的最小二乘斜率。

    **板块按今天的热度选，不按区间累计**：这张图要回答的是"我现在关注的
    这几个板块，之前是什么走势"，所以入选标准必须是当天的排名；按区间
    累计选出来的是"过去一段时间的赢家"，那是轮动图的问题。

    top 默认只给 6 个 —— 一条线一个颜色，超过 8 条图例就糊了。
    """
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        """
        WITH days AS (
            SELECT DISTINCT trade_date FROM fct.sector_heat_daily
             WHERE trade_date <= %(d)s AND src = 'sector'
             ORDER BY trade_date DESC LIMIT %(n)s
        ),
        heat AS (
            SELECT h.sector_code, h.trade_date, round(h.heat, 2) AS value
              FROM fct.sector_heat_daily h
              JOIN days ON days.trade_date = h.trade_date
             WHERE h.src = 'sector'
        ),

        pick AS (
            SELECT h.sector_code
              FROM heat h
             WHERE h.trade_date = (SELECT max(trade_date) FROM days)
               AND EXISTS (SELECT 1 FROM ref.sector_info i
                            WHERE i.sector_code = h.sector_code
                              AND i.name !~ '^[0-9]+$'
                              AND i.sector_type IN ('行业', '概念'))
             ORDER BY h.value DESC
             LIMIT %(top)s
        )
        SELECT h.trade_date, i.name, h.value
          FROM heat h
          JOIN pick ON pick.sector_code = h.sector_code
          JOIN ref.sector_info i ON i.sector_code = h.sector_code
         ORDER BY h.trade_date, i.name
        """, {'d': d, 'n': lookback, 'top': top})



@register('q_sector_rotation')
def q_sector_rotation(trade_date=None, lookback: int = 15, top: int = 15, **_):
    """
    板块轮动：最近 N 个交易日 × 热度前 N 个板块的涨幅矩阵（长表，热力图用）。

    板块先按**区间累计涨幅**选前 top 个，而不是按当天涨幅 —— 只看当天会把
    一日异动的板块选进来，热力图上就是一行孤立的红格子，看不出轮动。

    两个"看不懂"的坑，都在这条 SQL 里堵掉：
      1. **只取有涨跌幅的交易日。** 迁移进来的板块日线只有 close，没有
         pre_close/pct_chg（2026-08-27 实测：22 个交易日里只有 2 天有值），
         剩下 13 列全是空白格 —— 图看着像坏的。历史涨跌幅已由
         `lag(close)` 回算补齐，这里再加一道 `pct_chg IS NOT NULL`
         的日期过滤，保证以后再出现同样的洞时是"少几列"而不是"整张图空"。
      2. **剔掉没有中文名的板块。** `ref.sector_info` 里 880001~880099 段的
         49 个行业指数 name 就是代码本身，落到 y 轴上是一串六位数字。
         能从通达信配置认出名字的已由 c7_sector_name.py 补好（79 个）。
      3. **只看行业和概念板块。** `sector_type='风格'` 里装的是"昨日涨停/
         昨日连板/最近多板/轮动趋势"这类**信号板块**，它们天生涨幅巨大
         （成分股就是刚涨停的票），按累计涨幅排永远是它们占满前 15 行，
         真正的行业轮动一行都挤不进来。
    """
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        """
        WITH days AS (
            SELECT DISTINCT trade_date FROM md.sector_daily
             WHERE trade_date <= %s AND pct_chg IS NOT NULL
             ORDER BY trade_date DESC LIMIT %s
        ),
        pick AS (
            SELECT sd.sector_code
              FROM md.sector_daily sd
              JOIN days ON days.trade_date = sd.trade_date
              JOIN ref.sector_info i ON i.sector_code = sd.sector_code
             WHERE i.name !~ '^[0-9]+$'
               AND i.sector_type IN ('行业', '概念')
             GROUP BY sd.sector_code
             ORDER BY sum(sd.pct_chg) DESC NULLS LAST
             LIMIT %s
        )

        SELECT sd.trade_date, i.name, sd.sector_code, sd.pct_chg
          FROM md.sector_daily sd
          JOIN days ON days.trade_date = sd.trade_date
          JOIN pick ON pick.sector_code = sd.sector_code
          JOIN ref.sector_info i ON i.sector_code = sd.sector_code
         ORDER BY sd.trade_date, i.name
        """, (d, lookback, top))



@register('q_sector_parts')
def q_sector_parts(trade_date=None, limit: int = 30, **_):
    """板块热度的四个分项拆解，用来回答"这个板块凭什么热"。"""
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        """
        SELECT i.name,
               round(avg(CASE WHEN sd.part_key = 'sector_zhangfu'   THEN sd.part_value END), 1) AS 涨幅,
               round(avg(CASE WHEN sd.part_key = 'sector_guangdu'   THEN sd.part_value END), 1) AS 广度,
               round(avg(CASE WHEN sd.part_key = 'sector_dongliang' THEN sd.part_value END), 1) AS 动量,
               round(avg(CASE WHEN sd.part_key = 'sector_chengjiao' THEN sd.part_value END), 1) AS 成交,
               round(avg(sc.value), 1) AS 热度
          FROM fct.score_detail sd
          JOIN ref.sector_member m ON m.ts_code = sd.ts_code AND m.out_date IS NULL
          JOIN ref.sector_info i   ON i.sector_code = m.sector_code
          JOIN fct.score sc ON sc.ts_code = sd.ts_code AND sc.trade_date = sd.trade_date
                           AND sc.cap_key = 'sector_heat'
         WHERE sd.trade_date = %s AND sd.cap_key = 'sector_heat'
           AND EXISTS (SELECT 1 FROM md.sector_daily x
                        WHERE x.sector_code = m.sector_code AND x.trade_date = sd.trade_date)
         GROUP BY i.name
         ORDER BY avg(sc.value) DESC NULLS LAST
         LIMIT %s
        """, (d, limit))


# 阶段的中文抬头。**编号前缀是排序键**：表格默认按 SQL 的 ORDER BY 出来，
# 但用户点表头重排时，"点火/延续/蓄势/退潮"按拼音排毫无意义，带上 ①②③④
# 就还是那个先后顺序。文案与 `app/compute/ignition.py` 的档位定义一一对应。
STAGE_CN_SQL = """
        CASE {alias}.stage WHEN 'ignite' THEN '① 点火'
                           WHEN 'follow' THEN '② 延续'
                           WHEN 'coil'   THEN '③ 蓄势'
                           WHEN 'fade'   THEN '④ 退潮'
                           ELSE '⑤ 无' END
"""


@register('q_sector_ignite')
def q_sector_ignite(trade_date=None, limit: int = 40,
                    stages: tuple = ('ignite', 'follow', 'coil'),
                    include_style: bool = False, **_):
    """
    板块启动候选：把 `fct.ignition`（scope='sector'）的档位与五要素摊成一张表。

    **这张卡是为了回答"哪个板块刚起来"，热度榜回答不了这件事** —— 热度是当日
    横截面分位，榜首永远是"已经涨完的那几个"。启动是**状态转变**，模型
    （`app/compute/ignition.py`，`scripts/compute/d4_ignition.py` 落库）同时看
    "之前有多静"和"今天有多不一样"，输出四档：

        ① 点火 ignite  前几天在蓄势、今天三条确认到位  ← 核心产出
        ② 延续 follow  点火后还在走（错过第一天还能不能上）
        ③ 蓄势 coil    波动+低位成立、点火还没出现     ← 只是观察名单
        ④ 退潮 fade    高位 + 斜率转负                 ← 反向清单

    ⚠️ **`③ 蓄势` 不是买入清单**：样本外 T+5 超额 −0.107、胜率 46.3%
    （全板块对照组 +0.074 / 48.7%），"只蓄势没点火"本身是负期望。它的用途是
    ignite 的前置和"提前把名字放进视野"。`① 点火` 样本外 n=196、胜率 57.5%、
    T+5 超额 +0.455（阈值就是按这组回测反推的）。

    ⚠️ **行业板块永远不会是 `① 点火`**：`md.sector_daily` 里 230 个行业板块
    一条涨跌家数都没有（概念 268/268 有），广度这一维缺了就凑不满五要素。
    实测放宽（缺广度就跳过）是负期望，所以不放宽 —— 这是数据缺口，不是口径选择。
    表里保留 `sector_type` 就是为了让这件事看得见。

    五要素各 0~100，`有效要素` 说明这一行真算了几条（<5 就是有缺口）。
    `热度分位` 是当日热度在自身 120 日里的分位（越低越"还在低位"），
    `量比20日` / `份额倍数` 是量能那一维的原始数（份额比绝对放量重要：
    大盘普涨那天所有板块都放量）。
    """
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        f"""
        WITH ig AS (
            SELECT i.obj_key AS sector_code, i.stage, i.score, i.squeeze, i.pos,
                   i.ignition, i.volume, i.breadth, i.n_terms,
                   (i.detail->>'heat_pctile')::numeric  AS heat_pctile,
                   (i.detail->>'vol_ratio')::numeric    AS vol_ratio,
                   (i.detail->>'share_ratio')::numeric  AS share_ratio,
                   (i.detail->>'up_ratio')::numeric     AS up_ratio
              FROM fct.ignition i
             WHERE i.scope = 'sector'
               AND i.trade_date = %(d)s
               AND i.stage = ANY(%(stages)s)
        )
        SELECT {STAGE_CN_SQL.format(alias='ig')}       AS 阶段,
               si.name, si.sector_type,
               round(ig.score, 1)                      AS 综合分,
               round(ig.ignition, 0)                   AS 点火,
               round(ig.volume, 0)                     AS 量能,
               round(ig.breadth, 0)                    AS 广度,
               round(ig.pos, 0)                        AS 低位,
               round(ig.squeeze, 0)                    AS 波动,
               round(ig.heat_pctile * 100, 0)          AS 热度分位,
               round(h.heat, 1)                        AS 热度,
               round(t.slope, 2)                       AS 日趋势,
               sd.pct_chg,
               round(sd.amount_yuan / 1e8, 2)          AS amount_yi,
               round(ig.vol_ratio, 2)                  AS 量比20日,
               round(ig.share_ratio, 2)                AS 份额倍数,
               round(ig.up_ratio * 100, 0)             AS 上涨占比,
               sd.member_count,
               ig.n_terms                              AS 有效要素
          FROM ig
          JOIN ref.sector_info si ON si.sector_code = ig.sector_code
          LEFT JOIN fct.sector_heat_daily h ON h.sector_code = ig.sector_code
                                           AND h.trade_date = %(d)s
                                           AND h.src = 'sector'
          LEFT JOIN fct.sector_trend t ON t.sector_code = ig.sector_code
                                      AND t.trade_date = %(d)s AND t.win = 1
          LEFT JOIN md.sector_daily sd ON sd.sector_code = ig.sector_code
                                      AND sd.trade_date = %(d)s
         WHERE si.name !~ '^[0-9]+$'
           AND (%(style)s OR si.sector_type IN ('行业', '概念'))
         -- 档位优先、同档按综合分：先看"今天点火的"，再看"还在走的"，
         -- 最后才是观察名单。同档里综合分高的在前。
         ORDER BY CASE ig.stage WHEN 'ignite' THEN 1 WHEN 'follow' THEN 2
                                WHEN 'coil'   THEN 3 WHEN 'fade'   THEN 4
                                ELSE 5 END,
                  ig.score DESC NULLS LAST, si.name
         LIMIT %(lim)s
        """, {'d': d, 'stages': list(stages), 'lim': limit,
              'style': bool(include_style)})


@register('q_sector_stage_dist')
def q_sector_stage_dist(trade_date=None, include_style: bool = False, **_):
    """
    板块阶段分布：四档各有几个板块，**并给出与上一交易日的变化**。

    单看"今天有 3 个点火"没有参照物；`变化` 那一列才是市场层面的信息 ——
    点火数在增加是资金在找新方向，退潮数在增加是在撤。这张卡回答的是
    "现在整体处在什么阶段"，下面那张候选表回答"具体是哪几个板块"。

    两天都取 `fct.ignition` 里**真有数据的交易日**（不是日历日减一），
    所以长假之后比较的仍然是相邻两个交易日。
    """
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        f"""
        WITH days AS (
            SELECT DISTINCT trade_date FROM fct.ignition
             WHERE scope = 'sector' AND trade_date <= %(d)s
             ORDER BY trade_date DESC LIMIT 2
        ),
        cur AS (SELECT max(trade_date) AS d FROM days),
        prev AS (SELECT min(trade_date) AS d FROM days),
        base AS (
            SELECT i.stage, i.trade_date
              FROM fct.ignition i
              JOIN ref.sector_info si ON si.sector_code = i.obj_key
             WHERE i.scope = 'sector'
               AND i.trade_date IN (SELECT trade_date FROM days)
               AND si.name !~ '^[0-9]+$'
               AND (%(style)s OR si.sector_type IN ('行业', '概念'))
        ),
        agg AS (
            SELECT stage,
                   count(*) FILTER (WHERE trade_date = (SELECT d FROM cur))  AS n_cur,
                   count(*) FILTER (WHERE trade_date = (SELECT d FROM prev)) AS n_prev
              FROM base GROUP BY stage
        )
        SELECT {STAGE_CN_SQL.format(alias='agg')} AS 阶段,
               agg.n_cur                          AS 今日家数,
               agg.n_prev                         AS 上一交易日,
               agg.n_cur - agg.n_prev             AS 变化
          FROM agg
         ORDER BY CASE agg.stage WHEN 'ignite' THEN 1 WHEN 'follow' THEN 2
                                 WHEN 'coil'   THEN 3 WHEN 'fade'   THEN 4
                                 ELSE 5 END
        """, {'d': d, 'style': bool(include_style)})


@register('q_review_stocks')
def q_review_stocks(trade_date=None, limit: int = 200, **_):
    """复盘个股明细：评分 + 量能阶段 + 行情，一屏看完当天该看的票。"""
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        """
        SELECT d.ts_code, s.name, s.board,
               d.close, d.pct_chg, d.turnover_rate_pct,
               round(d.amount_yuan / 1e8, 2)        AS amount_yi,
               round(q.value, 1)                    AS x_quality,
               round(y.value, 1)                    AS y_env,
               round(o.value, 1)                    AS oper,
               o.quadrant,
               round(t.value, 1)                    AS vol_temp,
               CASE vs.value::int WHEN 1 THEN '冷却' WHEN 2 THEN '酝酿'
                                  WHEN 3 THEN '高潮' WHEN 4 THEN '背离'
                                  WHEN 5 THEN '退潮' END AS vol_stage
          FROM md.stock_daily d
          JOIN ref.security s ON s.ts_code = d.ts_code AND s.asset_type = 'stock'
          LEFT JOIN fct.score q  ON q.ts_code = d.ts_code AND q.trade_date = d.trade_date
                                AND q.cap_key = 'stock_quality'
          LEFT JOIN fct.score y  ON y.ts_code = d.ts_code AND y.trade_date = d.trade_date
                                AND y.cap_key = 'env_resonance'
          LEFT JOIN fct.score o  ON o.ts_code = d.ts_code AND o.trade_date = d.trade_date
                                AND o.cap_key = 'oper_score'
          LEFT JOIN fct.score t  ON t.ts_code = d.ts_code AND t.trade_date = d.trade_date
                                AND t.cap_key = 'vol_temp'
          LEFT JOIN fct.score vs ON vs.ts_code = d.ts_code AND vs.trade_date = d.trade_date
                                AND vs.cap_key = 'vol_stage'
         WHERE d.trade_date = %s
         ORDER BY o.value DESC NULLS LAST, d.pct_chg DESC NULLS LAST
         LIMIT %s
        """, (d, limit))


# ---------------------------------------------------------------- 资金
@register('q_etf_flow')
def q_etf_flow(lookback: int = 30, asset_class: str = 'equity', **_):
    """
    ETF 净申赎（按份额变化推算金额）。**默认只看 equity** ——
    货币 ETF 是 100 元/份、债券 ETF 有 1000 元/份的，混在一起算金额会虚高
    好几倍（实测 08-25 全类别 +85.88 亿份 → +2629 亿元，隐含 30.6 元/份）。

    这里**不查 `md.v_etf_flow` 视图**，而是自己在 CTE 里先框日期再开窗。
    视图里的 `lag() OVER (PARTITION BY ts_code ORDER BY trade_date)` 是窗口函数，
    外层对 `trade_date` 的过滤条件**推不进窗口下面**，所以走视图等于每次都对
    23 万行全量开窗、再 JOIN 165 万行的 `md.fund_daily` —— 实测 3.8 秒。
    先框日期再开窗降到几十毫秒。视图保留给 /sql 探索用，口径完全一致。

    多取 5 个交易日再切掉：窗口的第一天没有前值，如果正好卡在区间边界，
    那天会白白变成 NULL。
    """
    d = latest_date()
    if d is None:
        return []
    return engine.query(
        """
        WITH span AS (
            SELECT DISTINCT trade_date FROM md.etf_share
             WHERE trade_date <= %s
             ORDER BY trade_date DESC LIMIT %s
        ),
        base AS (
            SELECT s.ts_code, s.trade_date, s.share_units, s.fund_name, s.etf_type
              FROM md.etf_share s
              JOIN span ON span.trade_date = s.trade_date
        ),
        flow AS (
            SELECT b.ts_code, b.trade_date,
                   b.share_units - lag(b.share_units)
                       OVER (PARTITION BY b.ts_code ORDER BY b.trade_date) AS delta_units,
                   CASE
                     WHEN b.fund_name ~ '(货币|添益|现金|保证金|理财)' THEN 'money'
                     WHEN b.fund_name ~ '(国债|地债|政金|信用债|城投|公司债|可转债|债ETF|短融|债)' THEN 'bond'
                     WHEN b.etf_type = '跨境'
                       OR b.fund_name ~ '(恒生|恒指|港股|纳指|纳斯达克|标普|道琼斯|日经|德国|法国|沙特|东南亚|亚太|中概|美国)' THEN 'qdii'
                     WHEN b.fund_name ~ '(黄金|白银|有色|豆粕|能化|原油|商品)' THEN 'commodity'
                     ELSE 'equity'
                   END AS asset_class
              FROM base b
        )
        SELECT f.trade_date,
               round(sum(f.delta_units) / 1e8, 2)                  AS delta_yi_units,
               round(sum(f.delta_units * d.close) / 1e8, 1)        AS delta_yi_yuan,
               count(*)                                            AS etf_count
          FROM flow f
          LEFT JOIN md.fund_daily d ON d.ts_code = f.ts_code
                                   AND d.trade_date = f.trade_date
         WHERE f.delta_units IS NOT NULL
           AND (%s = 'all' OR f.asset_class = %s)
         GROUP BY f.trade_date ORDER BY f.trade_date DESC LIMIT %s
        """, (d, lookback + 5, asset_class, asset_class, lookback))[::-1]



@register('q_margin_trend')
def q_margin_trend(lookback: int = 30, **_):
    """两融余额趋势（全市场合计，单位亿元）。"""
    return engine.query(
        """
        SELECT trade_date,
               round(sum(rz_balance) / 1e8, 1)   AS rz_yi,
               round(sum(rq_balance) / 1e8, 1)   AS rq_yi,
               round(sum(rzrq_balance) / 1e8, 1) AS rzrq_yi
          FROM md.margin_detail
         GROUP BY trade_date ORDER BY trade_date DESC LIMIT %s
        """, (lookback,))[::-1]


@register('q_lhb_recent')
def q_lhb_recent(trade_date=None, limit: int = 100, **_):
    """龙虎榜明细。一票一天可多条上榜原因，这里不去重。"""
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        """
        SELECT l.ts_code, s.name, l.reason, l.close, l.pct_chg,
               round(l.net_buy / 1e4, 1)     AS net_buy_wan,
               round(l.lhb_amount / 1e4, 1)  AS lhb_wan,
               l.net_buy_ratio, l.turnover_rate_pct
          FROM md.lhb_detail l
          LEFT JOIN ref.security s ON s.ts_code = l.ts_code
         WHERE l.trade_date = %s
         ORDER BY l.net_buy DESC NULLS LAST LIMIT %s
        """, (d, limit))


# ---------------------------------------------------------------- 自选 / 评分
@register('q_watchlist')
def q_watchlist(group_id: int | None = None, trade_date=None, **_):
    """
    自选池 + 当日行情 + 多日聚合 + 评分。不传 group_id 就是全部组。

    多日列（10/20 日日均涨跌与振幅、明日振幅预测）来自 `fct.indicator_value`，
    口径见 `05_seed.sql` 里 `chg10_avg` 那几条的注释 —— 是**日均**，不是区间涨幅，
    跟同表里的 `chg10` 同名不同义。

    末尾补一个「详情」列（站内深链 `/stock?code=…`）：★重点关注这张卡是复盘的
    第一落点，从这里一步就能到个股详情，而不是记下代码再去 /stock 打一遍。
    链接在 Python 里拼（`quote_link.detail_link`），不在 SQL 里用 `||` 拼 ——
    URL 的样子只该有一处定义。
    """
    d = _d(trade_date)
    rows = engine.query(
        """
        WITH ind AS (
            SELECT ts_code,
                   max(value) FILTER (WHERE ind_key = 'chg10_avg') AS chg10_avg,
                   max(value) FILTER (WHERE ind_key = 'amp10_avg') AS amp10_avg,
                   max(value) FILTER (WHERE ind_key = 'chg20_avg') AS chg20_avg,
                   max(value) FILTER (WHERE ind_key = 'amp20_avg') AS amp20_avg,
                   max(value) FILTER (WHERE ind_key = 'amp_pred')  AS amp_pred
              FROM fct.indicator_value
             WHERE trade_date = %s
               AND ind_key IN ('chg10_avg','amp10_avg','chg20_avg','amp20_avg','amp_pred')
             GROUP BY 1
        )
        SELECT g.name AS group_name, w.ts_code, s.name,
               d.close, d.pct_chg, d.turnover_rate_pct,
               round(i.chg10_avg, 2) AS chg10_avg,
               round(i.amp10_avg, 2) AS amp10_avg,
               round(i.chg20_avg, 2) AS chg20_avg,
               round(i.amp20_avg, 2) AS amp20_avg,
               round(i.amp_pred, 2)  AS amp_pred,
               round(o.value, 1) AS oper, o.quadrant, w.note
          FROM config.watchlist_stock w
          JOIN config.watchlist_group g ON g.group_id = w.group_id
          LEFT JOIN ref.security s  ON s.ts_code = w.ts_code
          LEFT JOIN md.stock_daily d ON d.ts_code = w.ts_code AND d.trade_date = %s
          LEFT JOIN ind i ON i.ts_code = w.ts_code
          LEFT JOIN fct.score o ON o.ts_code = w.ts_code AND o.trade_date = %s
                               AND o.cap_key = 'oper_score'
         WHERE (%s::int IS NULL OR w.group_id = %s::int)
         -- 2026-09-01 用户指定：默认按**10 日振幅**从高到低（08-30 那次是操作分，
         -- 在此之前也是振幅）。和右栏「组内成员」表用同一个排序键 —— 同一批票在
         -- 两张表里给出两种默认顺序，对着看的时候只会互相干扰。
         -- 全表排、不先按组分块：想按组看点「组名」表头排一下就行。
         -- 同值按代码兜底：不给兜底键的话同值行每次刷新会互换位置。
         ORDER BY i.amp10_avg DESC NULLS LAST, o.value DESC NULLS LAST, w.ts_code
        """, (d, d, d, group_id, group_id))
    for r in rows:
        r['detail'] = quote_link.detail_link(r['ts_code'])
    return rows


# 自选组的「加权分逐日序列」CTE，被 q_group_heat（算趋势）和 q_group_trend
# （画曲线）共用。两处各写一份的话，表里的趋势分数和图上的曲线迟早对不上。
# 参数用命名占位符（%(d)s / %(win)s / %(min_n)s），拼进外层 SQL 时不用数位置。
_GROUP_WSCORE_CTE = """
        mem AS (
            SELECT g.group_id, g.name AS group_name, g.is_focus, w.ts_code
              FROM config.watchlist_group g
              JOIN config.watchlist_stock w ON w.group_id = g.group_id
        ),
        wday AS (
            -- 组的「成交额加权操作分」逐日序列。
            -- 权重取当日成交额：钱堆在哪只票上，这个组当天就更像那只票。
            -- 本想用流通市值，但 md.stock_snapshot 还是空的（已知缺口），
            -- 用一个查不到的字段当权重＝整列变空，不如用真有数的成交额。
            -- **分母只能统计"有分的行"**：写成 sum(amount_yuan) 会把没评分的票
            -- 的成交额也算进权重和，结果是组里越多票没评分、加权分越被压低，
            -- 这是这段里最容易错的一处。
            SELECT m.group_id, s.trade_date,
                   sum(s.value * d.amount_yuan)
                     / nullif(sum(d.amount_yuan) FILTER (WHERE s.value IS NOT NULL), 0)
                       AS wscore
              FROM mem m
              JOIN fct.score s ON s.ts_code = m.ts_code
                              AND s.cap_key = 'oper_score'
              JOIN md.stock_daily d ON d.ts_code = m.ts_code
                                   AND d.trade_date = s.trade_date
             WHERE s.trade_date <= %(d)s
               AND s.trade_date > %(d)s::date - (%(win)s::int * 2)
             GROUP BY 1, 2
        ),
        wwin AS (
            -- 只留最近 win 个**有数的交易日**。日历日减法圈不出固定条数：
            -- 长假一压，20 个自然日里可能只剩 12 个交易日，斜率的量纲就变了。
            SELECT group_id, trade_date, wscore,
                   row_number() OVER (PARTITION BY group_id
                                      ORDER BY trade_date) AS rn_asc
              FROM (
                    SELECT group_id, trade_date, wscore,
                           row_number() OVER (PARTITION BY group_id
                                              ORDER BY trade_date DESC) AS rn
                      FROM wday
                     WHERE wscore IS NOT NULL
                   ) z
             WHERE rn <= %(win)s
        ),
        wtrend AS (
            -- 最小二乘拟合，学旧系统 score_amp_trend.py 的思路（那边是对个股
            -- 20 日振幅做 np.polyfit(x, y, 1)），这里换成对「组加权分」做，
            -- 而且用 PG 自带的 regr_slope/regr_intercept，不把序列拉回 Python。
            SELECT group_id, count(*) AS n,
                   regr_slope(wscore, rn_asc)     AS b,
                   regr_intercept(wscore, rn_asc) AS a
              FROM wwin
             GROUP BY 1
            HAVING count(*) >= %(min_n)s
        )
"""


@register('q_group_heat')
def q_group_heat(trade_date=None, win: int = 20, min_n: int = 10, **_):
    """
    自选组热度：一行一个自选组，把「这个组今天什么状态、在往哪走」摊平。

    **列序按"复盘时的提问顺序"排**，不是按算的顺序：
    `预测操作分`（明天哪个组值得看）→ 当日分数 → 涨跌 → 振幅 → 龙虎榜 →
    最后才是 `趋势天数 / 成员数 / 有分只数 / 重点关注`。后面这几列是**元信息**，
    用来判断前面那些数可不可信（只有 2 只票的组、一半票没评分的组），不是主角。

    **默认按 `预测操作分` 从高到低**：这一列已经把"今天多少分"和"在往哪走"
    合成了一个数，比单看当日分更接近"明天先看谁"。算不出来的组（历史不足）
    排在最后，不用 0 顶到中间去。

    分三块看：
    - **分数**：预测操作分 / 加权分（成交额加权）/ 等权分 / 趋势分数。
    - **振幅**：组内明日、10 日、20 日日均振幅的组内均值。
    - **广度**：平均涨跌、组内 10 / 20 日均涨跌（bp）、上涨占比、上龙虎榜家数。

    **加权分与等权分要一起看**：两者差得多，说明这个组的分是被成交额最大的
    一两只票拉出来的，均值那套"组很强"的印象就不成立。只给一个数看不出这件事。

    **趋势分数 = 斜率 × 10**，读作"这组的加权分每 10 个交易日变化多少分"。
    直接给斜率（每天多少分）小数点后要看三位，量纲不好用眼睛比。
    正负号才是主信息：分数高但趋势为负 = 在退潮的强组。

    **预测操作分 = 回归线外推一天**（第 `n+1` 点，`a + b×(n+1)`）。它是
    "这条 20 日趋势线照原样再走一天会落在哪"，**不是预测涨跌方向**，
    也不含明天的任何新信息。

    「组内10日均涨跌bp / 组内20日均涨跌bp」是旧系统左侧分组列表那个得分的
    正确版本：旧口径是 `Σ(组内每票 10 日涨跌幅之和) / 10 / 股票数`，但它只
    累加了 9 个涨跌幅（10 天两两相减只有 9 个差）却仍除以 10，系统性偏小约
    1/10。这里用 `avg(chg10_avg)`，分母是"该票近 10 个交易日里真有行情的天数"。
    **两列都 ×100 以基点显示**（用户 2026-09-01 指定，理由见 SQL 注释）。

    不足 `min_n` 个交易日的组趋势列留空，不补 0：两三个点也能算出一条斜率，
    但那个数没有意义，而 0 会被读成"横着走"。
    """
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        """
        WITH """ + _GROUP_WSCORE_CTE + """,
        ind AS (
            SELECT ts_code,
                   max(value) FILTER (WHERE ind_key = 'chg10_avg') AS chg10_avg,
                   max(value) FILTER (WHERE ind_key = 'chg20_avg') AS chg20_avg,
                   max(value) FILTER (WHERE ind_key = 'amp10_avg') AS amp10_avg,
                   max(value) FILTER (WHERE ind_key = 'amp20_avg') AS amp20_avg,
                   max(value) FILTER (WHERE ind_key = 'amp_pred')  AS amp_pred
              FROM fct.indicator_value
             WHERE trade_date = %(d)s
               AND ind_key IN ('chg10_avg','chg20_avg','amp10_avg',
                               'amp20_avg','amp_pred')
             GROUP BY 1
        ),
        today AS (
            SELECT m.group_id, m.group_name, m.is_focus,
                   count(*) AS members,
                   count(o.value) AS scored,
                   round(sum(o.value * d.amount_yuan)
                         / nullif(sum(d.amount_yuan)
                                  FILTER (WHERE o.value IS NOT NULL), 0), 1)
                       AS w_oper,
                   round(avg(o.value), 1)   AS avg_oper,
                   -- 10 / 20 日**日均**涨跌幅的组内均值，**乘 100 显示**
                   -- （用户 2026-09-01 指定）。组一平均，日均涨跌幅只剩
                   -- 正负 1 以内（实测 −1.11 ~ +1.08），两位小数在表里全是
                   -- "0.01/−0.25"这种看不出差别的数。乘 100 之后单位正好是
                   -- **基点**（1bp = 万分之一），所以列名写 bp 不写百分号 ——
                   -- 标成百分数的话 35 会被读成 35 个百分点，量级差 100 倍。
                   -- 注意这段注释里**不能出现百分号**：psycopg 连注释一起扫
                   -- 占位符，一个裸的百分号会让整条 SQL 解析失败（实测踩过）。
                   round(avg(i.chg10_avg) * 100, 1) AS avg_chg10_bp,
                   round(avg(i.chg20_avg) * 100, 1) AS avg_chg20_bp,
                   round(avg(i.amp10_avg), 2) AS avg_amp10,
                   round(avg(i.amp20_avg), 2) AS avg_amp20,
                   round(avg(i.amp_pred), 2)  AS avg_amp_pred,
                   round(count(*) FILTER (WHERE d.pct_chg > 0)::numeric
                         / nullif(count(d.pct_chg), 0) * 100, 1) AS up_ratio,
                   round(avg(d.pct_chg), 2) AS avg_pct,
                   count(l.ts_code) AS lhb_count
              FROM mem m
              LEFT JOIN md.stock_daily d ON d.ts_code = m.ts_code
                                        AND d.trade_date = %(d)s
              LEFT JOIN fct.score o ON o.ts_code = m.ts_code
                                   AND o.trade_date = %(d)s
                                   AND o.cap_key = 'oper_score'
              LEFT JOIN ind i ON i.ts_code = m.ts_code
              LEFT JOIN (SELECT DISTINCT ts_code FROM md.lhb_detail
                          WHERE trade_date = %(d)s) l ON l.ts_code = m.ts_code
             GROUP BY 1, 2, 3
        )
        SELECT t.group_name,
               -- 列序＝看的顺序。**预测操作分放最前并当默认排序键**：复盘先问
               -- "明天哪个组值得看"，这一列就是那个答案（趋势线外推一天）。
               -- 成员数 / 有分只数 / 重点关注是**元信息**，用来判断上面那些数
               -- 可不可信（只有 2 只票的组、一半没评分的组），不是主角，压到最后。
               round((w.a + w.b * (w.n + 1))::numeric, 1)    AS pred_oper,
               t.w_oper, t.avg_oper,
               round((w.b * 10)::numeric, 2)                AS trend_score,
               t.avg_pct, t.avg_chg10_bp, t.avg_chg20_bp, t.up_ratio,
               t.avg_amp_pred, t.avg_amp10, t.avg_amp20,
               t.lhb_count,
               w.n                                           AS trend_days,
               t.members, t.scored, t.is_focus
          FROM today t
          LEFT JOIN wtrend w ON w.group_id = t.group_id
         -- 没算出预测分的组（历史不够）排在最后，不要用 0 顶到中间去
         ORDER BY pred_oper DESC NULLS LAST, t.w_oper DESC NULLS LAST
        """, {'d': d, 'win': win, 'min_n': min_n})


def group_pred_map(trade_date=None, win: int = 20,
                   min_n: int = 10) -> dict[int, float]:
    """
    `{group_id: 预测操作分}`。自选维护页左栏的组列表拿它当默认排序键。

    **不注册成命名查询**：它不是给卡片用的（卡片要的是 `q_group_heat` 那张
    完整的表），只是维护台排序要的一个映射。

    口径必须和 `q_group_heat.pred_oper` **一个字节都不差** —— 复用同一个
    `_GROUP_WSCORE_CTE`，外推公式也照抄 `a + b*(n+1)`。两处各写一份的话，
    左栏的名次和下面那张卡的名次会对不上，而"哪个组该先看"正是这一页的主问题。
    `q_group_heat` 返回的是 `group_name`（卡片要给人看），这里要 `group_id`
    才能跟选中行对上，所以不能直接拿那个结果反查。
    """
    d = _d(trade_date)
    if not d:
        return {}
    rows = engine.query(
        """
        WITH """ + _GROUP_WSCORE_CTE + """
        SELECT group_id,
               round((a + b * (n + 1))::numeric, 1) AS pred_oper
          FROM wtrend
        """, {'d': d, 'win': win, 'min_n': min_n})
    return {int(r['group_id']): r['pred_oper'] for r in rows
            if r['pred_oper'] is not None}


def group_chg10_map(trade_date=None) -> dict[int, float]:
    """
    `{group_id: 组内 10 日均涨跌 bp}`。左栏组列表拿它当默认排序键（从低到高）。

    口径必须和 `q_group_heat.avg_chg10_bp` 一致：都是「组内每票的 `chg10_avg`
    取平均，再 ×100」。那边是 `avg(i.chg10_avg)` 配 LEFT JOIN，`avg` 本身跳过
    NULL，所以这里直接 INNER JOIN 求平均是**同一个数**（`test_group_agg.py`
    里有一条断言逐组比对，防止两处慢慢走散）。

    **不注册成命名查询**：它不是给卡片用的，只是维护台排序要的一个映射。
    """
    d = _d(trade_date)
    if not d:
        return {}
    rows = engine.query(
        """
        SELECT w.group_id, round(avg(i.value) * 100, 1) AS chg10_bp
          FROM config.watchlist_stock w
          JOIN fct.indicator_value i ON i.ts_code = w.ts_code
                                    AND i.ind_key = 'chg10_avg'
                                    AND i.trade_date = %(d)s
         GROUP BY 1
        """, {'d': d})
    return {int(r['group_id']): r['chg10_bp'] for r in rows
            if r['chg10_bp'] is not None}



@register('q_group_trend')
def q_group_trend(trade_date=None, win: int = 20, top: int = 3,
                  min_n: int = 10, **_):
    """
    自选组趋势曲线：**所有**够长度的组都返回，但只有趋势最好的 `top` 个
    默认显示，其余的在图例里点一下才画出来。

    为什么不像以前那样只返回前几个：24 条线一起画是一团毛线，但"只返回 6 条"
    等于把另外 18 个组从图里删掉了 —— 想看第 7 名得改代码或改 config。
    Plotly 的 `visible='legendonly'` 正好解决这个：**数据都在，默认不画**，
    图例点一下就出来，再点一下收回去。

    `show` 列就是这个开关，按 **趋势斜率**（不是当日分）取前 `top` 个：
    这张图问的是"谁在起来"，当日分最高的组可能正在退潮（那件事上面那张表
    的 `趋势分数` 列已经说了）。默认显示的排在前面，图例顺序跟着走。

    返回竖表（一天一组一行，列 `trade_date / group_name / wscore / show`），
    由 `config.chart` 的 `encoding.color` 分线 —— 横过来就得把组名写进 SQL，
    加一个组要改代码。
    """
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        """
        WITH """ + _GROUP_WSCORE_CTE + """,
        ranked AS (
            -- 按斜率排名。`wtrend` 已经把不足 min_n 天的组挡掉了，所以这里
            -- 出现的每个组都够长度画一条线。
            SELECT group_id, b,
                   row_number() OVER (ORDER BY b DESC NULLS LAST) AS rk
              FROM wtrend
        )
        SELECT w.trade_date,
               (SELECT DISTINCT m.group_name FROM mem m
                 WHERE m.group_id = w.group_id) AS group_name,
               round(w.wscore::numeric, 1)      AS wscore,
               (r.rk <= %(top)s)                AS show
          FROM wwin w
          JOIN ranked r ON r.group_id = w.group_id
         -- 默认显示的排在前面（图例顺序＝这里的行序），组内再按日期升序
         ORDER BY (r.rk <= %(top)s) DESC, r.rk, w.trade_date
        """, {'d': d, 'win': win, 'top': top, 'min_n': min_n})



@register('q_score_rank')
def q_score_rank(trade_date=None, cap_key: str = 'oper_score', limit: int = 100,
                 sectors: int = 3, **_):
    """
    操作分排行，**每只票带上它所在的板块**。

    为什么要有板块列：一只票排在前面，光看分数不知道它是"独走"还是"整个板块
    在动"。同一板块里冒出好几只高分票，那是板块效应；孤零零一只，多半是个股
    事件。这个判断以前要在这一页和 `/sector` 之间来回切。

    三列的口径：
        hot_sector      所属板块里**当日热度最高**的那个（排序键，不是随便挑）
        sector_heat     那个板块的热度分，与「板块热度榜」同一口径 ——
                        `sector_heat` 分是算在个股上再广播的，板块级分数就是
                        成员股的均值（见 `q_sector_heat` 的说明）
        other_sectors   其余板块，按热度依次列到 `sectors` 个为止

    **只取「行业 / 概念」两类板块**，与 `/sector` 页一致；"低价股""融资融券"
    "深港通SZ"这些风格/自定义板块每只票都挂七八个，列出来全是噪声。
    名字是纯数字的板块（没补到中文名的 49 个）也排除。

    实测提醒（2026-08-28）：**「行业」类板块目前一个成员都没有**（880001–880499
    的成分股从未入库，已知缺口），所以这一列实际显示的都是概念板块。
    Top100 里 96 只能查到概念板块，另外 4 只这两列为空 —— 空着比填个"未分类"
    诚实。
    """
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        """
        WITH top AS (
            SELECT sc.ts_code, sc.rank_in_market AS rank, sc.value,
                   sc.pct_rank, sc.quadrant
              FROM fct.score sc
             WHERE sc.trade_date = %(d)s AND sc.cap_key = %(cap)s
             ORDER BY sc.value DESC NULLS LAST
             LIMIT %(lim)s
        ),
        heat AS (
            SELECT m.sector_code, avg(sc.value) AS heat
              FROM fct.score sc
              JOIN ref.sector_member m ON m.ts_code = sc.ts_code
                                      AND m.out_date IS NULL
             WHERE sc.cap_key = 'sector_heat' AND sc.trade_date = %(d)s
             GROUP BY m.sector_code
        ),
        mem AS (
            SELECT m.ts_code, i.name, h.heat,
                   row_number() OVER (PARTITION BY m.ts_code
                                      ORDER BY h.heat DESC NULLS LAST, i.name)
                       AS rn
              FROM ref.sector_member m
              JOIN ref.sector_info i ON i.sector_code = m.sector_code
              LEFT JOIN heat h ON h.sector_code = m.sector_code
             WHERE m.out_date IS NULL
               AND m.ts_code IN (SELECT ts_code FROM top)
               AND i.sector_type IN ('行业', '概念')
               AND i.name !~ '^[0-9]+$'
        )
        SELECT t.rank, t.ts_code, s.name, s.board,
               round(t.value, 2) AS value, t.pct_rank, t.quadrant,
               (SELECT mm.name FROM mem mm
                 WHERE mm.ts_code = t.ts_code AND mm.rn = 1)      AS hot_sector,
               (SELECT round(mm.heat, 1) FROM mem mm
                 WHERE mm.ts_code = t.ts_code AND mm.rn = 1)      AS sector_heat,
               (SELECT string_agg(mm.name, '、' ORDER BY mm.rn) FROM mem mm
                 WHERE mm.ts_code = t.ts_code
                   AND mm.rn BETWEEN 2 AND %(sec)s)               AS other_sectors,
               d.close, d.pct_chg
          FROM top t
          LEFT JOIN ref.security s ON s.ts_code = t.ts_code
          LEFT JOIN md.stock_daily d ON d.ts_code = t.ts_code
                                    AND d.trade_date = %(d)s
         ORDER BY t.value DESC NULLS LAST
        """, {'d': d, 'cap': cap_key, 'lim': limit,
              'sec': max(1, int(sectors))})



# ---------------------------------------------------------------- 全市场扫描
# 板块列只取「行业 / 概念」两类，与 /sector 页一致；"低价股""融资融券""深港通SZ"
# 这些风格/自定义板块每只票挂七八个，列出来全是噪声。名字是纯数字的板块
# （没补到中文名的 49 个）也排除。两个查询共用，避免口径漂移。
#
# **按板块名去重（`GROUP BY i.name`）**：同一个板块在三个源下各有一个 code
# （`TDX_880642` / `GN_880642` / `THS_D522` 都叫「供销社」，实测 1,015 个名字里
# 有 300 多个这样的），不去重的话一只票的板块列会写成「供销社 / 新材料、供销社」，
# Top10 里这只票还会给同一个板块投三票 —— 页面上看着像"板块行情"，其实是
# 同一个板块被数了三遍。热度取三份里最高的那份（某个源当天没算出来就取到另一个源）。
_SECTOR_PICK = """
        SELECT m.ts_code, i.name, max(h.heat) AS heat,
               row_number() OVER (PARTITION BY m.ts_code
                                  ORDER BY max(h.heat) DESC NULLS LAST,
                                           i.name) AS rn
          FROM ref.sector_member m
          JOIN ref.sector_info i ON i.sector_code = m.sector_code
          -- 板块热度取 **fct.sector_heat_daily 的 src='sector'**，也就是板块级
          -- 直算的那份，不是旧的"个股 sector_heat 分求成员均值再广播"。
          -- 后者是当年没有板块级分数时的替代品，均值会把板块里的分歧抹平。
          LEFT JOIN fct.sector_heat_daily h ON h.sector_code = m.sector_code
                                           AND h.trade_date = %(d)s
                                           AND h.src = 'sector'
         WHERE m.out_date IS NULL
           AND m.ts_code IN (SELECT ts_code FROM base)
           AND i.sector_type IN ('行业', '概念')
           AND i.name !~ '^[0-9]+$'
         GROUP BY m.ts_code, i.name
"""

# 合表的候选集：当日全市场逐票的评分 + 行情，按操作分排序后截断。
# 板块列只对这一批票算（`_SECTOR_PICK` 里的 `IN (SELECT ts_code FROM base)`）——
# 对全市场 5,547 只票都去展开 26 万行成员关系，等于把两秒白送出去。
_SCAN_BASE = """
        SELECT d.ts_code, s.name, s.board,
               d.close, d.pct_chg, d.turnover_rate_pct, d.amount_yuan,
               o.value AS oper, o.rank_in_market, o.quadrant,
               q.value AS x_quality, y.value AS y_env,
               t.value AS vol_temp, vs.value AS vol_stage_code
          FROM md.stock_daily d
          JOIN ref.security s ON s.ts_code = d.ts_code AND s.asset_type = 'stock'
          LEFT JOIN fct.score o  ON o.ts_code = d.ts_code
                                AND o.trade_date = d.trade_date
                                AND o.cap_key = 'oper_score'
          LEFT JOIN fct.score q  ON q.ts_code = d.ts_code
                                AND q.trade_date = d.trade_date
                                AND q.cap_key = 'stock_quality'
          LEFT JOIN fct.score y  ON y.ts_code = d.ts_code
                                AND y.trade_date = d.trade_date
                                AND y.cap_key = 'env_resonance'
          LEFT JOIN fct.score t  ON t.ts_code = d.ts_code
                                AND t.trade_date = d.trade_date
                                AND t.cap_key = 'vol_temp'
          LEFT JOIN fct.score vs ON vs.ts_code = d.ts_code
                                AND vs.trade_date = d.trade_date
                                AND vs.cap_key = 'vol_stage'
         WHERE d.trade_date = %(d)s
         ORDER BY o.value DESC NULLS LAST, d.pct_chg DESC NULLS LAST
         LIMIT %(base_lim)s
"""


@register('q_stock_scan')
def q_stock_scan(trade_date=None, limit: int = 300, sectors: int = 3, **_):
    """
    **全市场逐票扫描**：把原来的「个股明细」和「操作分排行」合成一张表。

    为什么合：两张表**同一天、同一批票、同一个排序键**（操作分从高到低），
    区别只是明细那张多了量能列、排行那张多了板块列和名次。摆成两张卡的结果是
    同一只票要在上下两个表格里各找一次，才能同时看到"它的量能阶段"和
    "它所在的板块热不热" —— 而这两件事本来就是同一个判断的两半。

    列序＝复盘时的提问顺序：

    1. `名次 / 代码 / 名称 / 上市板` —— 它是谁；
    2. `操作分 / 象限 / X / Y` —— 值不值得看（`名次` 是全市场名次，
       取 `fct.score.rank_in_market`，不是这张表里的行号）；
    3. `量能温度 / 量能阶段` —— 量给不给力（原「个股明细」的那两列）；
    4. `收盘 / 涨跌幅 / 换手率 / 成交额` —— 当日行情；
    5. `最热板块 / 板块热度 / 其它板块` —— 是独走还是整个板块在动
       （原「操作分排行」的三列）；
    6. `详情` 深链。

    `limit` 是**取几只票**（默认 300，卡片一页 40 行、翻页看后面）。板块列只对
    这几只票展开成员关系，所以放大 `limit` 会线性变慢 —— 全市场 5,547 只都要
    板块列的话得改成落库预算。

    基准日走 `_scored_day()`（最近一个**有评分**的交易日），不是 `_d()`：
    原来那两张卡用 `latest_date()`，而它给的是日历上最新的已收盘交易日 ——
    2026-08-31 15:50 就给 08-31，那天行情和评分都还没入库，两张卡当场变成"无数据"。
    这张表宁可显示上一个交易日，也不要在收盘后到链路跑完之间空着。
    """
    d = _scored_day(trade_date)
    if not d:
        return []
    rows = engine.query(
        """
        WITH base AS (""" + _SCAN_BASE + """),
        mem AS (""" + _SECTOR_PICK + """),
        sec AS (
            -- 把每只票的板块折成一行。**不要用三个相关子查询**（`(SELECT … WHERE
            -- mm.ts_code = b.ts_code AND mm.rn = 1)` 那种写法）：300 只票就是
            -- 900 次子查询，实测整条 1.6 秒；这里一次聚合，降到 0.4 秒。
            SELECT ts_code,
                   max(name) FILTER (WHERE rn = 1)              AS hot_sector,
                   max(heat) FILTER (WHERE rn = 1)              AS sector_heat,
                   string_agg(name, '、' ORDER BY rn)
                       FILTER (WHERE rn BETWEEN 2 AND %(sec)s)  AS other_sectors
              FROM mem
             GROUP BY 1
        )
        SELECT b.rank_in_market                      AS rank,
               b.ts_code, b.name, b.board,
               round(b.oper, 1)                      AS oper,
               b.quadrant,
               round(b.x_quality, 1)                 AS x_quality,
               round(b.y_env, 1)                     AS y_env,
               round(b.vol_temp, 1)                  AS vol_temp,
               CASE b.vol_stage_code::int
                    WHEN 1 THEN '冷却' WHEN 2 THEN '酝酿' WHEN 3 THEN '高潮'
                    WHEN 4 THEN '背离' WHEN 5 THEN '退潮' END AS vol_stage,
               b.close, b.pct_chg,
               round(b.turnover_rate_pct, 2)         AS turnover_rate_pct,
               round(b.amount_yuan / 1e8, 2)         AS amount_yi,
               sec.hot_sector,
               round(sec.sector_heat::numeric, 1)    AS sector_heat,
               sec.other_sectors
          FROM base b
          LEFT JOIN sec ON sec.ts_code = b.ts_code
         ORDER BY b.oper DESC NULLS LAST, b.pct_chg DESC NULLS LAST
        """, {'d': d, 'base_lim': limit, 'sec': max(1, int(sectors))})
    for r in rows:
        r['detail'] = quote_link.detail_link(r['ts_code'])
    return rows


@register('q_hot_sector_stats')
def q_hot_sector_stats(trade_date=None, top_n: int = 666, sectors: int = 3,
                       limit: int = 10, **_):
    """
    **操作分前 `top_n` 只票里，哪些板块反复出现**（默认前 666 只，各取前 10 名）。

    两列分别统计，摆成并排的 10 行：

    - `最热板块` —— 每只票**那一个**最热板块（`rn = 1`）的出现次数。
      分母就是 `top_n`，所以这一列的次数加起来 ≤ `top_n`。
    - `其它板块` —— 每只票的第 2 ~ `sectors` 个板块，**逐个计数**。
      一只票贡献多个板块，所以**这一列的总数必然超过 `top_n`**
      （2026-09-01 实测 666 只 → 最热合计 666、其它合计 1,328）。

    **2026-09-01 从前 100 只放大到前 666 只**（用户要求"扩大范围"）：100 只只覆盖
    全市场 5,545 只的 1.8%，一个真正在扩散的板块在里面往往只露 3~5 只，和噪声
    分不开；666 只是 12%，够看出"这个概念到底铺开了多少只"。代价可以忽略 ——
    实测 0.17s（100 只时 0.33s，差别在缓存不在行数：统计的重头是逐票取板块，
    而票数从 100 到 666 只是让同一遍扫描多带几行）。

    关于"拆分板块名称"：页面上那一列显示的是 `化肥、煤炭开采加工` 这样用 `、`
    连起来的字符串，用户要求先拆开再计数。这里**不去拆字符串**，而是回到
    `ref.sector_member` 逐行计数 —— 那个字符串本来就是 `string_agg` 拼出来的，
    从源头数等价于拆开数，而且不会被"板块名里本来带 `、`"坑到
    （实测 1,015 个板块名里没有一个含 `、`，但依赖这一点不如不依赖）。

    为什么要这张表：排行榜上"第 3、7、12 名都在同一个概念里"这件事，靠眼睛
    在几百行里扫是扫不出来的，而它正是"今天是不是板块行情"的直接答案。

    基准日与合表 `q_stock_scan` 同一个 `_scored_day()`，票的取法也复用同一段
    SQL（`_SCAN_BASE`）—— 两处各写一份的话，统计的"前 666 只"和表里看到的
    前 666 行迟早不是同一批。**所以 `config.page_layout` 里那两张卡的
    `top_n` / `limit` 要一起改**：合表只取 300 行、统计却数 666 只的话，
    统计里会有一批票在表上根本找不到。
    """
    d = _scored_day(trade_date)
    if not d:
        return []
    return engine.query(
        """
        WITH base AS (""" + _SCAN_BASE + """),
        mem AS (""" + _SECTOR_PICK + """),
        hot AS (
            SELECT name, count(*) AS cnt,
                   row_number() OVER (ORDER BY count(*) DESC, name) AS rk
              FROM mem WHERE rn = 1
             GROUP BY 1
        ),
        others AS (
            SELECT name, count(*) AS cnt,
                   row_number() OVER (ORDER BY count(*) DESC, name) AS rk
              FROM mem WHERE rn BETWEEN 2 AND %(sec)s
             GROUP BY 1
        ),
        -- 两边名次对齐成并排的行。用 generate_series 而不是 FULL JOIN：
        -- 某一侧不足 limit 行时，FULL JOIN 会把行数变成 max(两侧)、名次错位。
        seq AS (SELECT generate_series(1, %(lim)s) AS rk)
        SELECT s.rk                                  AS rank,
               h.name AS hot_sector, h.cnt AS hot_cnt,
               o.name AS other_sector, o.cnt AS other_cnt
          FROM seq s
          LEFT JOIN hot    h ON h.rk = s.rk
          LEFT JOIN others o ON o.rk = s.rk
         ORDER BY s.rk
        """, {'d': d, 'base_lim': max(1, int(top_n)),
              'lim': max(1, int(limit)), 'sec': max(1, int(sectors))})


@register('q_hot_sector_cross')
def q_hot_sector_cross(trade_date=None, top_n: int = 666, sectors: int = 3,
                       top_k: int = 3, **_):
    """
    **上一张卡两列各取前 `top_k` 名，交叉成 `top_k × top_k` 种组合，逐个列出票**。

    上一张卡（`q_hot_sector_stats`）只告诉你"智能家居出现 41 次、机器人概念
    出现 33 次"，看不出这两个概念**是不是同一批票撑起来的** —— 而这正是
    "今天是一个板块在动还是两个概念叠在一起动"的分界。这张卡把两列的前
    `top_k` 名两两配对，直接给出每个组合里的票。

    **口径：同时属于这两个板块就算，不管这两个板块在这只票里排第几。**
    这和上一张卡的列口径**不完全一样**，必须说清楚：

    - 上一张卡的「最热板块」是每只票**那一个**热度最高的板块（`rn = 1`），
      「其它板块」是它的第 2 ~ `sectors` 个。按这个严格口径去交叉，
      2026-09-01 实测 **9 个组合里只有 1 个有票**（AIGC概念 × 机器人概念 ＝
      迈瑞医疗）—— 因为"A 是这只票最热的板块"和"B 也在它前三里"同时成立
      本来就很少见。那样的表没有信息量。
    - 所以这里放宽成"同时属于 A 和 B"。同一天实测 9 个组合里 8 个有票，
      最大的智能家居 × 机器人概念 19 只。`严格` 那一列仍然给出严格口径的
      只数，两个口径的差就摆在同一行上，不用在两张卡之间来回换算。

    9 行**一行都不省**：某个组合是 0 只票也照样占一行（票列显示 `—`）。
    "这两个概念完全不重叠"和"这一行漏了"是两件事，省掉行就分不出来了。

    票池、基准日、板块口径全部复用 `q_hot_sector_stats` 那一套
    （`_SCAN_BASE` + `_SECTOR_PICK`），所以 `top_n` / `sectors` 要跟上一张卡
    在 `config.page_layout` 里保持一致，否则两张卡数的不是同一批票。
    """
    d = _scored_day(trade_date)
    if not d:
        return []
    return engine.query(
        """
        WITH base AS (""" + _SCAN_BASE + """),
        mem AS (""" + _SECTOR_PICK + """),
        -- 两列各自的前 top_k 名，口径与 q_hot_sector_stats 完全一致
        hot AS (
            SELECT name, count(*) AS cnt,
                   row_number() OVER (ORDER BY count(*) DESC, name) AS rk
              FROM mem WHERE rn = 1 GROUP BY 1
        ),
        others AS (
            SELECT name, count(*) AS cnt,
                   row_number() OVER (ORDER BY count(*) DESC, name) AS rk
              FROM mem WHERE rn BETWEEN 2 AND %(sec)s GROUP BY 1
        ),
        -- 3×3 的格子先摆出来，再往里填票：没有票的组合也要占一行
        grid AS (
            SELECT h.name AS hot_name, h.rk AS hot_rk, h.cnt AS hot_cnt,
                   o.name AS oth_name, o.rk AS oth_rk, o.cnt AS oth_cnt
              FROM hot h CROSS JOIN others o
             WHERE h.rk <= %(k)s AND o.rk <= %(k)s
        ),
        -- 宽松口径：同时属于两个板块（不看 rn）。板块同名的格子（最热榜和
        -- 其它榜可能出现同一个板块）直接排除 —— 自己和自己交叉没有意义。
        loose AS (
            SELECT g.hot_name, g.oth_name, m1.ts_code
              FROM grid g
              JOIN mem m1 ON m1.name = g.hot_name
              JOIN mem m2 ON m2.ts_code = m1.ts_code AND m2.name = g.oth_name
             WHERE g.hot_name <> g.oth_name
        ),
        -- 严格口径：A 是这只票的 rn=1，B 在它的 rn 2..sectors 里
        strict AS (
            SELECT g.hot_name, g.oth_name, m1.ts_code
              FROM grid g
              JOIN mem m1 ON m1.name = g.hot_name AND m1.rn = 1
              JOIN mem m2 ON m2.ts_code = m1.ts_code AND m2.name = g.oth_name
                         AND m2.rn BETWEEN 2 AND %(sec)s
             WHERE g.hot_name <> g.oth_name
        ),
        agg AS (
            SELECT l.hot_name, l.oth_name, count(*) AS n,
                   string_agg(b.name || '(' || l.ts_code || ')', '、'
                              ORDER BY b.oper DESC NULLS LAST) AS names,
                   round(avg(b.pct_chg)::numeric, 2) AS avg_chg,
                   round(avg(b.oper)::numeric, 1)    AS avg_oper
              FROM loose l JOIN base b ON b.ts_code = l.ts_code
             GROUP BY 1, 2
        ),
        st AS (
            SELECT hot_name, oth_name, count(*) AS n FROM strict GROUP BY 1, 2
        )
        SELECT g.hot_name                       AS hot_sector,
               g.hot_cnt                        AS hot_cnt,
               g.oth_name                       AS other_sector,
               g.oth_cnt                        AS other_cnt,
               coalesce(a.n, 0)                 AS cross_n,
               coalesce(s.n, 0)                 AS strict_n,
               a.avg_oper                       AS cross_avg_oper,
               a.avg_chg                        AS cross_avg_chg,
               -- 两榜可能出现同一个板块（乡村振兴既是最热榜第 3 也是其它榜第 1）。
               -- 那个格子不是"没有交叉票"，是"自己跟自己没得交叉"，写清楚比
               -- 显示一个 0 让人以为算错了好。
               CASE WHEN g.hot_name = g.oth_name
                    THEN '（两榜同一个板块，不与自己交叉）'
                    ELSE coalesce(a.names, '—') END AS cross_names

          FROM grid g
          LEFT JOIN agg a ON a.hot_name = g.hot_name AND a.oth_name = g.oth_name
          LEFT JOIN st  s ON s.hot_name = g.hot_name AND s.oth_name = g.oth_name
         ORDER BY coalesce(a.n, 0) DESC, g.hot_rk, g.oth_rk
        """, {'d': d, 'base_lim': max(1, int(top_n)),
              'sec': max(1, int(sectors)), 'k': max(1, int(top_k))})


@register('q_pattern_streak')
def q_pattern_streak(trade_date=None, n_back: int = 3, m_back: int = 7,
                     times: int = 3, op: str = '>=',
                     universe: str = 'market', limit: int = 500, **_):
    """
    形态扫描①：**前 N 到前 M 个交易日里涨停 `times` 次的票**。

    薄封装，真正的判据在 `app/compute/pattern.py:limit_streak_scan`。
    放在这里是因为页面只认命名查询（`config.chart` / `render_card` 都按名字
    取），而且参数从 URL/控件来的时候全是字符串，收类型的地方要有且只有一处。
    """
    return pattern.limit_streak_scan(
        trade_date, n_back=int(n_back), m_back=int(m_back),
        times=int(times), op=str(op), universe=str(universe),
        limit=int(limit))


@register('q_pattern_box')
def q_pattern_box(trade_date=None, lookback: int = 20, box_days: int = 3,
                  box_pct: float = 15.0, ma: str = 'ma5', price: str = 'close',
                  universe: str = 'market', limit: int = 500, **_):
    """
    形态扫描②：**断板后走箱体、当日跌破均线的票**。

    薄封装，判据在 `app/compute/pattern.py:box_break_scan`。
    """
    return pattern.box_break_scan(
        trade_date, lookback=int(lookback), box_days=int(box_days),
        box_pct=float(box_pct), ma=str(ma), price=str(price),
        universe=str(universe), limit=int(limit))


@register('q_score_parts')


def q_score_parts(ts_code: str = '600519.SH', trade_date=None, **_):
    """
    单票的评分归因：每个分项的分值/权重/贡献。让评分不是黑盒。

    **中文名从注册表来，不在代码里写死映射。** `fct.score_detail.part_label`
    存的其实是脚本键（`xingtai_v1` 这种），页面上直接显示就是一串英文，
    看不出"这一行是形态还是趋势"。所以这里 LEFT JOIN `config.script.name`
    取分项中文名、JOIN `config.capability.name` 取功能点中文名 ——
    改名字在「积木管理」里改注册表就行，不用动代码、也不用重算 5,547×17 行明细。

    `part_key` / `cap_key` 仍然返回，出问题时要能对回原始键。
    """
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        """
        SELECT sd.cap_key,
               coalesce(c.name, sd.cap_key)                       AS cap_name,
               sd.part_key,
               coalesce(s.name, sd.part_label, sd.part_key)       AS part_label,
               round(sd.part_value, 2)   AS part_value,
               round(sd.weight, 4)       AS weight,
               round(sd.contribution, 3) AS contribution, sd.script_key
          FROM fct.score_detail sd
          LEFT JOIN config.script     s ON s.script_key = sd.script_key
          LEFT JOIN config.capability c ON c.cap_key    = sd.cap_key
         WHERE sd.ts_code = %s AND sd.trade_date = %s
         ORDER BY sd.cap_key, sd.part_key
        """, (ts_code, d))


@register('q_vol_stage_dist')
def q_vol_stage_dist(trade_date=None, **_):
    """量能阶段分布。背离那一档是"天量后缩量上涨"，最该盯的。"""
    d = _d(trade_date)
    if not d:
        return []
    return engine.query(
        """
        SELECT CASE vs.value::int WHEN 1 THEN '1 冷却' WHEN 2 THEN '2 酝酿'
                                  WHEN 3 THEN '3 高潮' WHEN 4 THEN '4 背离'
                                  WHEN 5 THEN '5 退潮' END AS stage,
               count(*) AS n,
               round(avg(t.value), 1) AS avg_temp
          FROM fct.score vs
          LEFT JOIN fct.score t ON t.ts_code = vs.ts_code
                               AND t.trade_date = vs.trade_date
                               AND t.cap_key = 'vol_temp'
         WHERE vs.trade_date = %s AND vs.cap_key = 'vol_stage'
           AND vs.value IS NOT NULL
         GROUP BY 1 ORDER BY 1
        """, (d,))


@register('q_vol_double')
def q_vol_double(trade_date=None, limit: int | None = None, **kw):
    """
    **倍量震荡榜**：近 40 个交易日频繁倍量、且价格沿 60 日线震荡的票，
    按倍量次数从高到低取前 50。

    找的是"主力在 60 线附近反复进出"的形态 —— 量能一次次翻倍（有资金在动），
    但价格既没突破也没破位，一直贴着 60 线摆。**它的用法是等突破，不是当天买**。

    列序＝看这张表时的提问顺序：

    1. `代码 / 名称 / 上市板` —— 它是谁；
    2. `倍量次数`（排序键）／`倍量次数(对昨日)` —— 资金来过几次。两个口径差得大
       说明这只票的量本身在剧烈跳动；
    3. `穿越次数 / 线上天数占比` —— 是不是真在"震荡"（0 次穿越是单边，不算）；
    4. `偏离中位数 / 最大偏离` —— 贴得有多紧。中位小而最大值大 = 中间拉过一次又回来；
    5. `60线变化` —— 60 线自己是不是走平的（陡涨时"贴着线"是趋势不是震荡）；
    6. `收盘 / MA60 / 日均成交额` —— 现在的位置和流动性；
    7. `详情` 深链。

    **读 `fct.vol_double` 不现算**：现算实测 10 秒（MA60 要 60 根暖机，再往前推
    40 天观察窗，一次扫 55 万行），页面上点一下就是卡死。入选阈值在
    `config.setting['vol_double.filter']`，改了立刻生效、不用重算 —— 因为库里存的
    是全市场每票的原始读数，不是"入选名单"。

    基准日：库里最新的那天。日线到了但 `calc_vol_double` 还没跑时，
    这张卡显示上一天而不是空表（空表会被当成"今天没票入选"）。
    """
    rows, meta = vol_double.picks(trade_date, **({'limit': limit} if limit else {}))
    for r in rows:
        # 基准日要跟着每一行走：日线到了但作业还没跑时，看的人必须一眼看出
        # 这张表是哪天的，否则"今天只有 3 只"分不清是市场里真没有还是数据没到
        r['trade_date'] = meta['trade_date']
        r['detail'] = quote_link.detail_link(r['ts_code'])
    return rows


# ---------------------------------------------------------------- 任务
@register('q_task_recent')
def q_task_recent(limit: int = 20, trade_date=None, **_):
    return engine.query(
        """
        SELECT t.task_id, t.job_key, j.name, t.cap_key, t.trade_date, t.state,
               t.attempt, t.started_at, t.finished_at,
               left(coalesce(t.message, ''), 100) AS message
          FROM ops.task t
          LEFT JOIN config.job j ON j.job_key = t.job_key
         ORDER BY t.task_id DESC LIMIT %s
        """, (limit,))


@register('q_job_list')
def q_job_list(**_):
    return engine.query(
        """
        SELECT job_key, name, kind, trigger_kind, cron_expr, after_job,
               only_trade_day, enabled, timeout_sec
          FROM config.job ORDER BY job_key
        """)


# ---------------------------------------------------------------- 单值（stat 卡片）
# `config.page_layout` 里 card_kind='stat' 的 card_ref 指向这一族。
# 约定：**返回恰好一行，键名固定为 value / hint**。ui.render_card 的 stat
# 分支按这个约定取数，所以新增一个指标块 = 在这里加一个 k_ 函数 + 在
# config.page_layout 里插一行，不用改渲染代码。

@register('k_mood')
def k_mood(trade_date=None, **_):
    d = _d(trade_date)
    if not d:
        return [{'value': '—', 'hint': '没有行情'}]
    row = engine.query(
        """
        SELECT round(avg(value), 1) AS v
          FROM fct.score WHERE trade_date = %s AND cap_key = 'market_mood'
        """, (d,))
    v = row[0]['v'] if row else None
    if v is None:
        up = engine.query(
            """
            SELECT round(count(*) FILTER (WHERE pct_chg > 0)::numeric
                         / nullif(count(*), 0) * 100, 1) AS v
              FROM md.stock_daily WHERE trade_date = %s
            """, (d,))
        v = up[0]['v'] if up else None
        return [{'value': v if v is not None else '—',
                 'hint': f'{d} 上涨占比（情绪分未计算）'}]
    return [{'value': v, 'hint': f'{d} market_mood'}]


@register('k_up_count')
def k_up_count(trade_date=None, **_):
    d = _d(trade_date)
    if not d:
        return [{'value': '—', 'hint': '没有行情'}]
    r = engine.query(
        """
        SELECT count(*) FILTER (WHERE pct_chg > 0) AS up,
               count(*) FILTER (WHERE pct_chg < 0) AS down
          FROM md.stock_daily WHERE trade_date = %s
        """, (d,))[0]
    return [{'value': r['up'], 'hint': f'跌 {r["down"]} 家 · {d}'}]


@register('k_limit_up')
def k_limit_up(trade_date=None, **_):
    d = _d(trade_date)
    if not d:
        return [{'value': '—', 'hint': '没有行情'}]
    r = engine.query(
        """
        SELECT count(*) FILTER (WHERE limit_up IS NOT NULL
                                 AND close >= limit_up - 0.001) AS up,
               count(*) FILTER (WHERE limit_down IS NOT NULL
                                 AND close <= limit_down + 0.001) AS down,
               count(*) FILTER (WHERE limit_up IS NULL) AS no_limit
          FROM md.stock_daily WHERE trade_date = %s
        """, (d,))[0]
    hint = f'跌停 {r["down"]} 家'
    if r['no_limit']:
        # 涨跌停价缺失的票算不出涨停，明说缺多少，别让人以为是真没涨停
        hint += f' · {r["no_limit"]} 只缺涨跌停价'
    return [{'value': r['up'], 'hint': hint}]


@register('k_amount')
def k_amount(trade_date=None, **_):
    d = _d(trade_date)
    if not d:
        return [{'value': '—', 'hint': '没有行情'}]
    r = engine.query(
        """
        SELECT round(sum(amount_yuan) / 1e8) AS yi FROM md.stock_daily
         WHERE trade_date = %s
        """, (d,))[0]
    return [{'value': f'{r["yi"]:,.0f} 亿' if r['yi'] is not None else '—',
             'hint': f'{d} 两市成交额'}]


@register('k_score_avg')
def k_score_avg(trade_date=None, **_):
    d = _d(trade_date)
    if not d:
        return [{'value': '—', 'hint': '没有行情'}]
    r = engine.query(
        """
        SELECT round(avg(value), 1) AS v, count(*) AS n
          FROM fct.score WHERE trade_date = %s AND cap_key = 'oper_score'
        """, (d,))[0]
    if r['v'] is None:
        return [{'value': '—', 'hint': f'{d} 还没跑评分'}]
    return [{'value': r['v'], 'hint': f'{r["n"]} 只票已评分'}]


@register('k_health')
def k_health(**_):
    """
    系统健康。判据是**数据日期**而不是时钟 —— 这条链路平时交给智能体跑、
    会换模型，看时间没意义，看数据到哪天才是真的。
    """
    d = sec_repo.latest_trade_date()
    if not d:
        return [{'value': '无数据', 'hint': 'md.stock_daily 是空的'}]
    bad = engine.query(
        """
        SELECT count(*) AS n FROM ops.quality_result
         WHERE passed = false AND run_at > now() - interval '2 days'
        """)[0]['n']
    lag = (dt.date.today() - d).days
    if bad:
        return [{'value': f'{bad} 项告警', 'hint': f'最新数据 {d}'}]
    return [{'value': '正常' if lag <= 3 else f'滞后 {lag} 天',
             'hint': f'最新数据 {d}'}]


@register('k_review_head')
def k_review_head(trade_date=None, **_):
    d = _d(trade_date)
    if not d:
        return [{'value': '—', 'hint': '没有行情'}]
    r = engine.query(
        """
        SELECT count(*) AS n,
               count(*) FILTER (WHERE pct_chg > 0) AS up,
               round(avg(pct_chg), 2) AS avg_pct,
               round(sum(amount_yuan) / 1e8) AS yi
          FROM md.stock_daily WHERE trade_date = %s
        """, (d,))[0]
    return [{'value': f'{d} · 涨 {r["up"]}/{r["n"]} · 均 {r["avg_pct"]}%',
             'hint': f'成交 {r["yi"]:,.0f} 亿' if r['yi'] is not None else ''}]


@register('q_screen_rules')
def q_screen_rules(**_):
    """筛选条件类型清单。前台按 param_schema 渲染表单，不写死条件种类。"""
    return engine.query(
        """
        SELECT rule_key AS 条件类型, name AS 名称,
               param_schema::text AS 参数, note AS 说明
          FROM config.screen_rule WHERE enabled ORDER BY rule_key
        """)


@register('q_screen_presets')
def q_screen_presets(**_):
    return engine.query(
        """
        SELECT preset_id AS id, name AS 名称, owner AS 归属,
               is_shared AS 共享, conditions::text AS 条件, created_at AS 创建时间
          FROM config.screen_preset ORDER BY preset_id DESC
        """)


@register('q_field_dict')
def q_field_dict(**_):
    return engine.query('SELECT * FROM ops.v_field_dict')


