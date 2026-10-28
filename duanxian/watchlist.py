"""自选股与分组的只读取数 —— 库里早就有数据，页面一直没读。

用户 2026-09-25 的原话："自选股 分组自选 怎么没数据？这两块是有脚本定时去同步同花顺本地的
自选股和分组到 pg 数据库，你只需要读出来展示就可以了呀。参考 financetool 中有同样的功能，搬过来。"

实测确认他说的对：`config.watchlist_group` **45 组**、`config.watchlist_stock` **1287 条**，
而 YuanShan 的自选只读浏览器 localStorage（`frontend/src/lib/watchlist.ts`，扁平代码数组、
上限 100 只、**没有分组概念**）—— 所以页面空。不是数据缺，是**没接**。

口径照 FinanceTool 的 `app/web/queries.py:1381 q_watchlist`：分组 + 当日行情 +
10/20 日**日均**涨跌与振幅 + 明日振幅预测 + 操作分与象限 + 备注，
默认排序键 **10 日振幅倒序**（用户 2026-09-01 指定的，此前是操作分）。

三条不是注释而是会出错的点：

1. `chg10_avg` / `amp10_avg` 是**日均**，和同表里的 `chg10`（区间涨幅）**同名不同义**。
   混用会把"日均振幅 2.9%"读成"涨了 2.9%"，所以字段名原样保留并随 `unit` 交出。
2. **`is_focus` 全库 0 真**（45 组无一标重点）。这不是"数据没同步"，是用户没勾过。
   交 `focus_groups: 0` 让页面能说清，不要默默造一个"默认组"顶上去。
3. 排序键必须有同值兜底（代码升序），否则同振幅的行每次刷新会互换位置 ——
   FinanceTool 那条注释就是踩过之后写的。
"""

from __future__ import annotations

from . import pg
from . import ranking          # 名次（同分同名、下一名顺延、没分不占号）的唯一实现 —— 公用组件

# `analytics.indicator_value` 的指标键 → 页面上该说什么。口径全部是"日均"。
#
# 这里把单位写死成"个百分点 / 每天"（2026-10-14 用户问「为什么数值都这么小」）：
# `chg10_avg` 是**每日涨跌幅的算术平均**，正负天会相互抵消，所以它天然远小于
# 同键的 `chg10`（区间涨幅）—— 实测 2026-10-14 全场 5,539 只：chg10_avg 中位 -0.56、
# 绝对值均值 0.89，而 chg10 中位 -5.70、绝对值均值 8.64（正好差一个窗口长度）。
# 数值小不是坏了，是量纲：一天本来就只动一两个点。
INDICATORS = {
    "chg10_avg": "10 日日均涨跌幅（近 10 个交易日每日涨跌的算术平均，单位是个点/每天；"
                 "正负天互相抵消后必然比「10 日区间涨幅」小一个量级，别当累计读）",
    "amp10_avg": "10 日日均振幅（每日 (最高-最低)/前收 的平均，单位是个点/每天。"
                 "振幅恒为正、不会抵消，所以它总是比同窗口的日均涨跌大）",
    "chg20_avg": "20 日日均涨跌幅（同上，窗口换成 20 个交易日）",
    # 区间那两把尺子并排列在同一行里（2026-10-14）：只给日均没有合计可比，
    # "平均每天动 2.8 个点"就会被读成"一共涨了 2.8%"。
    "chg10": "10 日区间涨幅（这 10 个交易日合起来的涨跌 =（今收 - 10 场前收）/10 场前收，"
             "单位是个点，**不是每天**。它按未复权收盘价算，所以窗口里有除权时会跳出一个大负数",
    "chg20": "20 日区间涨幅（同上，窗口 20 个交易日，同样未复权）",
    "amp20_avg": "20 日日均振幅（同上，窗口换成 20 个交易日）",
    "amp_pred": "明日振幅预测 = 20 日日均振幅 + 该序列的 20 日斜率 × 10.5（外推一步，"
                "不是涨幅预测；不足 20 根给空值，不补 0）",
}
_LATEST_SQL = "(select max(trade_date) from analytics.indicator_value)"


def _plain(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if hasattr(v, "isoformat") and not isinstance(v, float):
        return v.isoformat()
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    # PG numeric 能存 NaN、pandas 也会把 NULL 变 NaN。它是"算不出"，
    # 绝不能折成 0（0 是一个真实的振幅值），统一交 None 由界面显示 `—`。
    return None if f != f or f in (float("inf"), float("-inf")) else f


def _rows(sql, params=()):
    df = pg.fetch_df(sql, params)
    if df is None:
        return None
    return [{k: _plain(v) for k, v in r.items()} for r in df.to_dict("records")]


def _one(sql, params=()):
    rows = _rows(sql, params)
    return rows[0] if rows else None


def groups(date: str = "") -> dict:
    """全部分组：票数、是否有重点标记、当日平均涨跌幅。"""
    d = date or pg.latest_session("market_data.stock_daily") or ""
    if not d:
        return {"available": False, "date": None,
                "reason": "本地库里还没有日线行情，无法给分组算当日涨跌"}
    rows = _rows(
        f"""
        select g.group_id, g.name, g.is_focus, g.display_order, g.note,
               count(w.security_code)::int as stock_count,
               round(avg(w.cost) ::numeric, 3)                as avg_cost,
               count(w.cost)::int                             as cost_filled
        from config.watchlist_group g
        left join config.watchlist_stock w using (group_id)
        group by g.group_id, g.name, g.is_focus, g.display_order, g.note
        order by g.is_focus desc, g.display_order nulls last, g.group_id
        """)
    if rows is None:
        return {"available": False, "date": d,
                "reason": "读 config.watchlist_group 失败（权限或表不存在）"}
    pct = _rows(
        f"""
        select w.group_id, round(avg(d.pct_change)::numeric, 2) as avg_pct,
               count(*) filter (where d.pct_change > 0)::int as up,
               count(*) filter (where d.pct_change < 0)::int as down,
               count(*) filter (where d.pct_change is null)::int as missing
        from config.watchlist_stock w
        left join market_data.stock_daily d
               on d.security_code = w.security_code and d.trade_date = %s
        group by 1
        """, (d,)) or []
    by = {r["group_id"]: r for r in pct}
    for g in rows:
        g.update(by.get(g["group_id"], {}))
    focus = sum(1 for g in rows if g["is_focus"])
    return {
        "available": True, "date": d, "groups": rows,
        "total_stocks": sum(g["stock_count"] for g in rows),
        "focus_groups": focus,
        # 组数与焦点组数都从这一次读回来的行里算，不许再抄一份「45 组」那样的快照数字
        # （10-14 现查库里是 48 组 / 1,333 条，而屏上那句话还写着 45 —— 那就是屏幕说假话）。
        "caveat": (f"`is_focus`：全 {len(rows)} 组里 {focus} 组勾着重点"
                   + ("（一个都没勾 —— 是用户没勾过，不是数据没同步，所以这里不凭空造一个默认组）。"
                      if focus == 0 else "。")
                   + "`missing` 是该组里当日无行情（停牌或未入库）的只数，不计入涨跌均值。"
                   "同一只票可以属于多个组，所以各组票数之和会大于去重后的自选总数。"),
        "source": "pg", "requests": 0,
    }


def watchlist_codes(group_id: int | None = None) -> dict:
    """**只要代码列**的那一份自选名单。

    为什么单独开这一条（2026-10-25 首页实测）：解禁日历那块「只看自选」打的是
    `/api/watchlist/stocks?limit=2000` —— 一发 457,717 字节、1.4 秒，而它只读
    `security_code` 一个字段；那 17 列的当日行情、七个因子、评分与备注全是为丢掉的。
    明细那一屏（分组自选）该继续读 `watchlist()`，两边读的是同一张 `config.watchlist_stock`，
    所以名单不会分叉：分开的只是**列**，不是口径。
    """
    rows = _rows("""
        SELECT DISTINCT w.security_code
          FROM config.watchlist_stock w
         WHERE (%s::int IS NULL OR w.group_id = %s::int)
         ORDER BY 1
        """, (group_id, group_id))
    if rows is None:
        return {"available": False, "rows": [],
                "reason": "读自选代码失败（config.watchlist_stock 不可读或库不通）"}
    return {"available": True, "codes_only": True, "source": "pg",
            "rows": [{'security_code': r.get('security_code')} for r in rows],
            "count": len(rows),
            "note": "这一份只有代码列；当日行情与因子明细在 /api/watchlist/stocks"}


def watchlist(group_id: int | None = None, date: str = "", limit: int = 2000) -> dict:
    """自选明细：分组 + 当日行情 + 日均振幅/涨跌 + 操作分象限 + 备注。"""
    d = date or pg.latest_session("market_data.stock_daily") or ""
    if not d:
        return {"available": False, "date": None,
                "reason": "本地库里还没有日线行情，自选没有当日数据可读"}
    rows = _rows(
        f"""
        WITH ind AS (
            SELECT security_code,
                   max(value) FILTER (WHERE indicator_key = 'chg10_avg') AS chg10_avg,
                   max(value) FILTER (WHERE indicator_key = 'amp10_avg') AS amp10_avg,
                   max(value) FILTER (WHERE indicator_key = 'chg20_avg') AS chg20_avg,
                   max(value) FILTER (WHERE indicator_key = 'amp20_avg') AS amp20_avg,
                   max(value) FILTER (WHERE indicator_key = 'amp_pred')  AS amp_pred,
                   -- 区间那把尺子并排列进来（2026-10-14 用户问「10日日均为什么这么小」）：
                   -- 只给日均、不给合计，屏幕上就没有可比对象，而旧 caveat 还写着
                   -- 「与同表 chg10 同名不同义」—— 那一列当时根本不在这张表里。
                   max(value) FILTER (WHERE indicator_key = 'chg10')     AS chg10,
                   max(value) FILTER (WHERE indicator_key = 'chg20')     AS chg20
              FROM analytics.indicator_value
             WHERE trade_date = %s
               AND indicator_key IN ('chg10_avg','amp10_avg','chg20_avg','amp20_avg','amp_pred',
                                     'chg10','chg20')
             GROUP BY 1
        )
        SELECT g.name AS group_name, w.group_id, w.security_code, s.name,
               d.close, d.pct_change, d.turnover_rate_percent,
               round(i.chg10_avg::numeric, 2) AS chg10_avg,
               round(i.amp10_avg::numeric, 2) AS amp10_avg,
               round(i.chg20_avg::numeric, 2) AS chg20_avg,
               round(i.amp20_avg::numeric, 2) AS amp20_avg,
               round(i.amp_pred::numeric, 2)  AS amp_pred,
               round(i.chg10::numeric, 2)     AS chg10,
               round(i.chg20::numeric, 2)     AS chg20,
               round(o.value::numeric, 1)     AS oper,
               o.quadrant, w.cost, w.note
          FROM config.watchlist_stock w
          JOIN config.watchlist_group g ON g.group_id = w.group_id
          LEFT JOIN reference_data.security s ON s.security_code = w.security_code
          LEFT JOIN market_data.stock_daily d ON d.security_code = w.security_code
                                             AND d.trade_date = %s
          LEFT JOIN ind i ON i.security_code = w.security_code
          LEFT JOIN analytics.score o ON o.security_code = w.security_code
                                     AND o.trade_date = %s
                                     AND o.capability_key = 'oper_score'
         WHERE (%s::int IS NULL OR w.group_id = %s::int)
         ORDER BY i.amp10_avg DESC NULLS LAST, o.value DESC NULLS LAST, w.security_code
         LIMIT {max(1, min(int(limit or 2000), 5000))}
        """,
        (d, d, d, group_id, group_id))
    if rows is None:
        return {"available": False, "date": d,
                "reason": "读自选明细失败（config.watchlist_stock 或关联表不可读）"}
    missing_ind = sum(1 for r in rows if r.get("amp10_avg") is None)
    return {
        "available": True, "date": d, "rows": rows, "count": len(rows),
        "sort": "amp10_avg desc, oper desc, security_code asc（同值兜底，否则每次刷新会换序）",
        "indicator_meaning": INDICATORS,
        "unit": {"close": "yuan", "pct_change": "pct", "turnover_rate_percent": "pct",
                 "chg10_avg": "pct/日", "amp10_avg": "pct/日", "chg20_avg": "pct/日",
                 "amp20_avg": "pct/日", "amp_pred": "pct",
                 "chg10": "pct（区间合计）", "chg20": "pct（区间合计）",
                 # 「操作分」**不是百分位**（2026-10-14 核对：库里这一列取的是
                 # `analytics.score.value`，当日实测区间 -77.8~+16.2、中位 -43.5 —— 是带符号的
                 # 合成分原值；同表另有 `percent_rank` 那一列才是百分位，这一屏没取）。
                 "oper": "分：0.40×个股质量 + 0.60×环境共振 的合成分原值，值域 -100~100，"
                         "不是百分位、不是涨幅",
                 "quadrant": "当日象限（X、Y 各自切在 0：Q1=X+Y+ / Q2=X+Y- / Q3=X-Y+ / Q4=X-Y-）"},
        "score_meaning": {
            "oper": "操作分 = 0.40×个股质量(X) + 0.60×环境共振(Y)，两项都是 -100~100 的带符号合成分",
            "X": "个股质量 = 形态 0.25 + 趋势 0.25 + 位置 0.20 + 筹码 0.20 + 量能 0.10（逐项都是 -100~100）",
            "Y": "环境共振 = 板块热度 0.65 + 大盘情绪 0.35 —— **它是板块级的数**：同一天同一行业的票共享一个 Y"
                 "（2026-10-14 实测 5,562 只票只有 32 个不同的 Y），所以 Y 这一项差别只落在「你在哪个板块」上",
            "板块热度": "板块涨幅 0.30 + 板块广度 0.30 + 板块动量 0.25 + 板块成交额 0.15",
            "权重出处": "config.binding（改权重要在那儿改，页面上这几行文字由同一份数据说话）",
        },
        "caveat": ("`chg10_avg`/`amp10_avg` 是**日均**（每日数值相加÷窗口天数），"
                   "与同键去掉 `_avg` 的 `chg10`（区间涨幅）同名不同义，别当区间涨跌读 —— "
                   "2026-10-14 实测：全场 chg10_avg 中位 -0.56、绝对值均值 0.89，"
                   "而 chg10 中位 -5.70、绝对值均值 8.64，差的正是窗口长度那 10 倍。\n"
                   "⚠️ 这几列现在按**未复权**原值算：`stock_daily.pre_close` 存的是昨日收盘价，"
                   "**不是除权参考价**，所以除权除息那天库里记的是一个假暴跌 —— "
                   "实测 688110.SH 2026-10-12 库里日涨跌 -37.05%，按复权因子还原只有 -5.66%；"
                   "001309.SZ 10-13 库里 -27.27%，还原后是 +2.30%（连符号都反了）。"
                   "落到这一列上就是：002961.SZ 屏上「10日日均」-3.30，把窗口里那天还原成 -2.54 之后重算是 **+0.24**；"
                   "688110.SH 屏上 -6.04、还原后 -3.15；001309.SZ 屏上 -5.50、还原后 -2.79。"
                   "近 10 场窗口里出现过因子跳变的票 257 只（占 5,568 只的 4.6%），"
                   "本批自选名单里 116 只，它们这一列的平均比没有跳变的低 0.56 个点/日 —— "
                   "看到某只票的日均涨跌异常地负，先去核对它这些天里有没有除权。\n"
                   + (f" 本批 {missing_ind} 只没有指标行（indicator_value 当日缺该行），"
                      "显示为 `—` 而不是 0。" if missing_ind else "")
                   + " `oper` 与 `quadrant` 只来自 capability_key='oper_score'。"),
        "source": "pg", "requests": 0,
    }


# --- 组级分析：分数 / 趋势 / 近 X 日平均涨跌幅 / 组内振幅 --------------------
#
# 用户 2026-09-27 点名要的是**组这一层**的趋势与得分，不要当日涨了几只、不要平均成本、
# 不要"同花顺导入"那一列（"自选股都是同花顺导入的，显示就是多此一举"）。
#
# 一句先把事实说清：**库里没有任何一张"组"级结果表存着这些数**。存的是组内**个股**的
# `analytics.score`（oper_score）、`analytics.indicator_value`（chg10_avg/amp*_avg/amp_pred）、
# `market_data.stock_daily`（pct_change/amount_yuan）。那份参考实现也不是读表 ——
# 它在展示查询里现算聚合（做法与三条坑记在本地留档
# `docs/参考/FinanceTool/docs/系统说明书.md` 的"自选组的分数、趋势与振幅"一节）。
# 所以这里做的是"读现成列 + 按那个口径聚合"，新造的只有聚合本身，没有新指标；
# 聚合口径三条踩过的坑照抄：
#   1. 加权分的分母**只统计有分的行**，写成裸 `sum(amount_yuan)` 会让"组里越多票没评分、
#      加权分越被压低"；
#   2. 窗口是**最近 20 个有 oper_score 的交易日**，不是 20 个自然日 —— 按自然日圈，长假一压
#      只剩十来个点，斜率量纲跟着行情日历漂移，跨月不可比；
#   3. 不足 10 场的组，趋势三列**留 NULL 不补 0**：两三个点也拟合得出斜率，但那个数没意义，
#      而 0 会被读成"横着走"。
#
# 成员是**用今天的分组回溯历史**（`config.watchlist_stock` 没有时间维度，只有 `added_at`），
# 所以刚加进去的票会把它过去的分数算进本组的趋势线里 —— 这条在 `calibers` 里对外说明，
# 不藏。

GROUP_WINDOW = 20
GROUP_MIN_DAYS = 10
#: 近几场的组间名次（用户 2026-10-26 要"近5日排名趋势"；这里的一场＝一个有分的交易日，不是自然日）。
#: 屏上那一列的抬头由接口把这一位数交出去（`rank.days`），列头不许自己抄一个 5。
GROUP_RANK_DAYS = 5

_GROUP_SQL = """
with sess as (
    select trade_date, row_number() over (order by trade_date) as rn
      from (select distinct trade_date from analytics.score
             where capability_key = 'oper_score' and trade_date <= %s::date
             order by trade_date desc limit %s) t
),
member as (
    select w.group_id, w.security_code
      from config.watchlist_stock w
),
hist as (
    select m.group_id, s.trade_date, min(u.rn) as rn,
           sum(s.value * d.amount_yuan) filter (where s.value is not null
                                                  and d.amount_yuan is not null) as sx,
           sum(d.amount_yuan)         filter (where s.value is not null
                                                  and d.amount_yuan is not null) as sw,
           avg(s.value) filter (where s.value is not null)                        as eq
      from member m
      join analytics.score s on s.security_code = m.security_code
                            and s.capability_key = 'oper_score'
      join sess u on u.trade_date = s.trade_date
      left join market_data.stock_daily d
             on d.security_code = s.security_code and d.trade_date = s.trade_date
     group by 1, 2
),
series as (
    select group_id, rn, trade_date,
           round((sx / nullif(sw, 0))::numeric, 3) as w_score,
           round(eq::numeric, 2)                   as eq_score
      from hist
),
trend as (
    select group_id, count(*)::int as trend_days,
           round((regr_slope(w_score::float8, rn::float8) * 10)::numeric, 2) as trend_score,
           round((regr_intercept(w_score::float8, rn::float8)
                  + regr_slope(w_score::float8, rn::float8)
                    * (count(*) + 1)::float8)::numeric, 2)                   as pred_score
      from series group by 1 having count(*) >= %s
),
cur as (
    select group_id, w_score, eq_score, trade_date as score_date
      from series where rn = (select max(rn) from sess)
),
ind as (
    select security_code,
           max(value) filter (where indicator_key = 'chg10_avg') as chg10_avg,
           max(value) filter (where indicator_key = 'chg20_avg') as chg20_avg,
           max(value) filter (where indicator_key = 'amp10_avg') as amp10_avg,
           max(value) filter (where indicator_key = 'amp20_avg') as amp20_avg,
           max(value) filter (where indicator_key = 'amp_pred')  as amp_pred
      from analytics.indicator_value
     where trade_date = %s
       and indicator_key in ('chg10_avg','chg20_avg','amp10_avg','amp20_avg','amp_pred')
     group by 1
),
day as (
    select m.group_id,
           round(avg(d.pct_change)::numeric, 2)                  as avg_pct,
           count(*) filter (where d.pct_change is not null)::int as quoted_count,
           count(*) filter (where d.pct_change > 0)::int         as up_count,
           round((avg(i.chg10_avg) * 100)::numeric, 1)           as chg10_bp,
           round((avg(i.chg20_avg) * 100)::numeric, 1)           as chg20_bp,
           round(avg(i.amp10_avg)::numeric, 2)                   as amp10_avg,
           round(avg(i.amp20_avg)::numeric, 2)                   as amp20_avg,
           round(avg(i.amp_pred)::numeric, 2)                    as amp_pred,
           round(avg(sc.value)::numeric, 2)                      as eq_score_direct,
           count(sc.value)::int                                  as scored_count
      from member m
      left join market_data.stock_daily d
             on d.security_code = m.security_code and d.trade_date = %s
      left join ind i on i.security_code = m.security_code
      left join analytics.score sc on sc.security_code = m.security_code
                                   and sc.capability_key = 'oper_score'
                                   and sc.trade_date = %s
      group by 1
),
dt as (
    select m.group_id, count(*)::int as dt_times
      from member m
      join market_data.dragon_tiger_detail x on x.security_code = m.security_code
      join sess u on u.trade_date = x.trade_date
     group by 1
)
select g.group_id, g.name, g.is_focus, g.display_order,
       mc.member_count,
       c.w_score, c.eq_score, c.score_date,
       t.trend_score, t.pred_score, t.trend_days,
       d.avg_pct, d.quoted_count, d.up_count, d.chg10_bp, d.chg20_bp,
       d.amp10_avg, d.amp20_avg, d.amp_pred, d.scored_count,
       coalesce(x.dt_times, 0)::int as dt_times
  from config.watchlist_group g
  join (select group_id, count(distinct security_code)::int as member_count
          from config.watchlist_stock group by 1) mc on mc.group_id = g.group_id
  left join cur c on c.group_id = g.group_id
  left join trend t on t.group_id = g.group_id
  left join day d on d.group_id = g.group_id
  left join dt x on x.group_id = g.group_id
 order by t.pred_score desc nulls last, c.w_score desc nulls last, g.display_order nulls last
"""

# 全市场 oper_score 等权均值在这 20 场里的最低/最高 —— 用来把"趋势分数混着市场水位"
# 这句话写成当场所测，而不是文档里一个会过期的常数。
_MARKET_LEVEL_SQL = """
with sess as (
    select trade_date from (select distinct trade_date from analytics.score
                              where capability_key = 'oper_score' and trade_date <= %s::date
                              order by trade_date desc limit %s) t
)
select round(min(m)::numeric, 1) as lo, round(max(m)::numeric, 1) as hi, count(*)::int as days,
       round((max(m) - min(m))::numeric, 1) as span
  from (select s.trade_date, avg(s.value) as m
          from analytics.score s join sess u using (trade_date)
         where s.capability_key = 'oper_score'
         group by 1) q
"""

_SERIES_SQL = """
with sess as (
    select trade_date, row_number() over (order by trade_date) as rn
      from (select distinct trade_date from analytics.score
             where capability_key = 'oper_score' and trade_date <= %s::date
             order by trade_date desc limit %s) t
),
hist as (
    select w.group_id, s.trade_date, min(u.rn) as rn,
           sum(s.value * d.amount_yuan) filter (where s.value is not null
                                                  and d.amount_yuan is not null) as sx,
           sum(d.amount_yuan)         filter (where s.value is not null
                                                  and d.amount_yuan is not null) as sw
      from config.watchlist_stock w
      join analytics.score s on s.security_code = w.security_code
                            and s.capability_key = 'oper_score'
      join sess u on u.trade_date = s.trade_date
      left join market_data.stock_daily d
             on d.security_code = s.security_code and d.trade_date = s.trade_date
     group by 1, 2
)
select h.group_id, g.name, u.trade_date, u.rn,
       round((h.sx / nullif(h.sw, 0))::numeric, 2) as w_score,
       (h.sx / nullif(h.sw, 0))::float8            as w_raw
  from hist h
  join config.watchlist_group g on g.group_id = h.group_id
  join sess u on u.trade_date = h.trade_date and u.rn = h.rn
 order by g.name, u.rn
"""


def _daily_group_ranks(series: list[dict], days: int):
    """每天把「当日成交额加权操作分」在组与组之间排一名（用户 2026-10-26：每天的自选组结果搞一个排名）。

    名次本身**不在这里写规则**：调 `duanxian/ranking.rank_in_memory`（同分同名、下一名顺延、
    没有分的不参与也不占号）—— 这一件事全站只准那一处实现。

    三个不显然的点：
     1. 尺子是 `_SERIES_SQL` 交出的**未四舍五入原值** `w_raw`，不是屏上那位小数：
        2026-10-26 实测（基准场 2026-10-21、近 5 场、44 个组）按两位小数每天会并出 0~2 行假并列，
        原值它们有先后。用舍入后的值排名＝把"同分同名"这条规则用在了不同分上。
     2. 场次号 `rn` 是「有 oper_score 的交易日」里的第几档（与折线、趋势斜率同一套），不是自然日。
     3. 某天整组算不出加权分（组里当天没有一只票同时有分与成交额）就**不给名次**，
        路径里是 `·`；给它补一个"最后一名"就是把没数念成最差。

    返回：`({group_id: {rn: 名次}}, 用过的场次号, 每场母体计数, 假并列行数)`
    """
    by_rn: dict[int, list[dict]] = {}
    for s in series:
        rn = s.get("rn")
        if rn is None:
            continue
        by_rn.setdefault(int(rn), []).append(s)
    rns = sorted(by_rn)[-max(1, int(days)):] if by_rn else []

    out: dict[int, dict[int, int]] = {}
    per_session: list[dict] = []
    fake_ties = 0
    for rn in rns:
        members = by_rn[rn]
        ranked = ranking.rank_in_memory(
            [{"group_id": m.get("group_id"), "w_raw": m.get("w_raw")} for m in members],
            key="w_raw", desc=True, rank_key="rank")
        for it in ranked:
            gid, rk = it.get("group_id"), it.get("rank")
            if gid is not None and rk is not None:
                out.setdefault(gid, {})[rn] = rk

        # 假并列计数：按屏幕那两位小数会落到同一档、原值其实不同的行数（口径要用当场数，不背常数）
        bucket_n: dict[float, int] = {}
        bucket_raw: dict[float, set] = {}
        for m in members:
            v = m.get("w_raw")
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            k = round(float(v), 2)
            bucket_n[k] = bucket_n.get(k, 0) + 1
            bucket_raw.setdefault(k, set()).add(float(v))
        hit = sum(n for k, n in bucket_n.items() if n > 1 and len(bucket_raw[k]) > 1)
        fake_ties += hit

        dates = [m["trade_date"] for m in members if m.get("trade_date")]
        per_session.append({"rn": rn,
                            "trade_date": max(dates) if dates else None,
                            "ranked": sum(1 for it in ranked if it.get("rank") is not None),
                            "rows": len(members),
                            "rounded_ties": hit})
    return out, rns, per_session, fake_ties


def group_analytics(date: str = "", window: int = GROUP_WINDOW,
                    min_days: int = GROUP_MIN_DAYS) -> dict:
    """组级分数/趋势/广度/振幅 —— 一行一个组，附 20 场加权分序列供折线用，
    并给每个组交最近 `GROUP_RANK_DAYS` 场的**组间名次**（`rank_now`/`rank_path`/`rank_gain`）。

    趋势三列（趋势分数/预测操作分/趋势天数）在不足 `min_days` 场时是 NULL，
    界面必须显示 `—`，不许补 0。名次同理：那一天排不出就是 `—`／路径里 `·`。
    """
    import datetime
    import time as _t

    d = date or pg.latest_session("market_data.stock_daily") or ""
    if not d:
        return {"available": False, "date": None,
                "reason": "本地库里还没有日线行情，组级分析没有当日数据可聚合"}
    t0 = _t.time()
    rows = _rows(_GROUP_SQL, (d, int(window), int(min_days), d, d, d))
    if rows is None:
        return {"available": False, "date": d,
                "reason": "组级聚合查询失败（score / indicator_value / stock_daily 之一不可读）"}
    series = _rows(_SERIES_SQL, (d, int(window))) or []
    # 名次：把"每天的自选组结果排一名"算出来（判据在 `duanxian/ranking.py` 那一处，这里只调用）。
    # 原值 `w_raw` 只用来排名，算完就丢 —— 屏上与折线读的都是那位小数的 w_score，
    # 多一份全精度数上线只会让人以为屏幕那一列背后还有第二个没交代的水位。
    ranks, rank_rns, rank_per_session, rank_fake_ties = _daily_group_ranks(series, GROUP_RANK_DAYS)
    for s in series:
        s.pop("w_raw", None)
    # 趋势分数里混着"全市场水位"这一件事必须用**本场实测**说，不能写死一个数糊过去：
    # oper_score 是全市场相对分，全市场等权均值自己就会随大盘摆，摆幅多大直接决定
    # "趋势分数是组间强弱"这句话还剩多少可信度。
    level = _one(_MARKET_LEVEL_SQL, (d, int(window))) or {}
    for r in rows:
        q, up = r.get("quoted_count"), r.get("up_count")
        r["up_ratio"] = round(up * 100.0 / q, 1) if q and up is not None else None

    # 「最近一场」以表里「加权操作分」那一列同源的那一天为准（行级 score_date 的最大值）：
    # 名次与那一格必须是同一场，否则屏上会读出"分数是 —、名次却排得出"这种自相矛盾的格子。
    score_dates = {r.get("score_date") for r in rows if r.get("score_date")}
    rn_by_date = {s.get("trade_date"): int(s["rn"]) for s in series
                  if s.get("trade_date") and s.get("rn") is not None}
    now_rn = rn_by_date.get(max(score_dates)) if score_dates else None
    for r in rows:
        seq = ranks.get(r.get("group_id")) or {}
        present = [seq[rn] for rn in rank_rns if seq.get(rn) is not None]
        r["rank_now"] = seq.get(now_rn) if now_rn is not None else None
        r["rank_days"] = len(present)
        # 一名都排不出时路径交 None：五个「·」连起来既不说明名次、又会被读成"排到最后了"
        r["rank_path"] = ("→".join(str(seq[rn]) if seq.get(rn) is not None else "·"
                                   for rn in rank_rns) if present else None)
        # 净变化只在"两端都有名次"时给：终点就是 rank_now 那一场，起点是这一串里最早的一场。
        # 最近一场没名次（那一天整组算不出加权分）就交 None —— 拿一次更早的名次当终点会把它念成"最新"。
        r["rank_gain"] = ((present[0] - r["rank_now"])
                          if r["rank_now"] is not None and len(present) >= 2 else None)

    dated = [r for r in rows if r.get("trend_score") is not None]
    top = {r["group_id"] for r in sorted(dated, key=lambda x: -x["trend_score"])[:3]}
    dates = sorted({s["trade_date"] for s in series}) if series else []
    scored = sum(1 for r in rows if r.get("trend_days") is not None)
    preds = [r["pred_score"] for r in rows if r.get("pred_score") is not None]
    pred_span = (f"本场实测落在 {min(preds):.1f} ~ {max(preds):.1f}，" if preds else "本场算不出（无够长窗口）")
    elapsed = int((_t.time() - t0) * 1000)
    out_elapsed = f"聚合耗时 {elapsed} ms"
    # 名次那一列的母体、缺天与"假并列"都要当场数，不背常数（库里组数会随同步变）
    rank_dates = [p["trade_date"] for p in rank_per_session if p.get("trade_date")]
    ranked_counts = [p["ranked"] for p in rank_per_session if p.get("ranked")]
    with_rank = [r for r in rows if (r.get("rank_days") or 0) > 0]
    rank_partial = sum(1 for r in with_rank if (r.get("rank_days") or 0) < len(rank_rns))
    rank_universe_note = (
        f"分母＝那一天算得出加权分的组数（这一串里"
        f"{f'每天 {ranked_counts[0]} 个' if min(ranked_counts) == max(ranked_counts) else f'每天 {min(ranked_counts)}~{max(ranked_counts)} 个'}；"
        f"这张表只列有成员的组，最多 {len(rows)} 个，空组不占行）"
        if ranked_counts else
        "分母是哪一批：没读到（这一串里一场名次都没排出来，不是 0 个组）")
    return {
        "available": True,
        "date": d,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_ms": elapsed,
        "rows": rows,
        "group_count": len(rows),
        "trend_ready": scored,
        "trend_awaiting": len(rows) - scored,
        "series": series,
        "series_dates": dates,
        "top_trend_groups": sorted([r["name"] for r in rows if r["group_id"] in top]),
        "window": {"sessions": window, "min_days": min_days,
                   "from": dates[0] if dates else None, "to": dates[-1] if dates else None,
                   "note": "窗口按**有 oper_score 的交易日**数，不是自然日"},
        "market_level": level,
        "rank": {
            "days": GROUP_RANK_DAYS,
            "sessions": len(rank_rns),
            "from": rank_dates[0] if rank_dates else None,
            "to": rank_dates[-1] if rank_dates else None,
            "basis": "当日成交额加权操作分（与「加权操作分」那一列、折线、趋势斜率同一把尺），按未舍入原值排",
            "rule": ranking.RULE_NOTE,
            "rule_short": ranking.RULE_NOTE_SHORT,
            "direction": ranking.DIRECTION_NOTE,
            "universe_note": rank_universe_note,
            "per_session": rank_per_session,
            "universe": {"min": min(ranked_counts) if ranked_counts else None,
                         "max": max(ranked_counts) if ranked_counts else None},
            "groups_on_board": len(rows),
            "partial": rank_partial,
            "no_rank": len(rows) - len(with_rank),
            "rounded_ties": rank_fake_ties,
        },
        "unit": {"w_score": "分", "eq_score": "分", "pred_score": "分", "trend_score": "分/10场",
                 "avg_pct": "%（当日）", "chg10_bp": "bp/日", "chg20_bp": "bp/日",
                 "amp10_avg": "%/日", "amp20_avg": "%/日", "amp_pred": "%",
                 "up_ratio": "%（上涨只数 ÷ 有当日行情只数）", "dt_times": "次（窗口 20 场内）",
                 "rank_now": "名（那一天组间，1 = 加权分最高的那个组）",
                 "rank_gain": "名（正数＝名次往前走、数字变小）", "rank_days": "场（这一串里排得出名次的场次）"},
        "calibers": {
            "w_score": "加权操作分 = Σ(操作分×当日成交额) ÷ Σ当日成交额，分母只统计有分的行；"
                       "权重用成交额是因为流通市值那张表还是空的",
            "eq_score": "平均操作分 = 组内等权平均，不看成交额",
            "trend_score": "趋势分数 = 这 20 场加权分对场次号的最小二乘斜率 ×10（PG 的 "
                           "regr_slope(分, 场次号)，参数顺序是 y 在前），"
                           "读作「这组每 10 个交易日的加权分变化多少分」；正负号才是主信息，"
                           "分数高但趋势为负 = 在退潮的强组",
            "pred_score": "预测操作分 = 把那条回归线外推一天（第 n+1 点）。等价写法是"
                          "「这 20 场的平均加权分 + 趋势分数×(n+1)/20」，n=20 时约等于 均值 + 趋势，"
                          "所以它衡量的是『水位 + 方向』，不是今天的水位。"
                          "它是默认排序键；不是预测涨跌方向，一根大阳线或一条突发消息它完全不知道",
            "chg10_bp": "组内个股 10 日『日均』涨跌幅取组平均后 ×100（1bp=0.01%）。"
                        "抬头写 bp 不写 %：标成 % 会把 −25bp 读成 −25%，量级差 100 倍",
            "amp": "组内明日振幅 / 10 日振幅 / 20 日振幅 = 组内个股同名日均指标取均值，"
                   "口径是「日均」不是「区间」",
            "dt_times": "上榜次数 = 组内个股在这 20 个交易日的龙虎榜条数合计"
                        "（本地龙虎榜自 2026-06-01 起，共 80 场）",
            "members": "成员用『今天的』分组回溯整条历史（config.watchlist_stock 没有时间维度），"
                       "刚加进去的票会把它过去的分数算进本组趋势线",
            "rank": f"近 {GROUP_RANK_DAYS} 场名次 = 每天把「当日成交额加权操作分」在组与组之间排一名，"
                    "判据与全站其它名次同源（同分同名、下一名顺延；没有分的那天不参与、也不占号）。"
                    "两件事别混：① 这一列的尺是那「一天」的加权分，而行序（默认序）那把尺是预测操作分"
                    "＝整个窗口的外推值 —— 所以第一名不一定在第一行；"
                    "② 排名用的是未四舍五入的原值，屏上那位小数的加权分只是显示层，"
                    "按显示值排会把只差 0.004 分的两个组念成同一名",
            "rank_path": "路径（早→晚，用 → 连）就是这一串里每天的名次；中间那个「·」是那一天整组"
                         "算不出加权分（组里当天没有一只票同时有分、有成交额），不是最后一名。"
                         "成员口径继承了上面 members 那条：是用今天的名单回溯那一天，"
                         "所以它是「用今天的组重算出来的历史名次」，不是那天真实入选的组榜",
            "rank_gain": "净变化 = 这一串里最早那一场的名次 − 最近一场（rank_now）的名次。"
                         "正数＝往前走了（名次数字变小），负数＝往后退 —— 方向与全站其它名次列一致。"
                         "它是相对量：组间位置变好不等于组里的票变强，绝对水位看同一行的加权操作分",
        },
        "caveats": [
            "这些数没有一张组级表存着：本屏读的是组内个股的 analytics.score、"
            "analytics.indicator_value、market_data.stock_daily 三张表，在查询里现场聚合"
            f"（{out_elapsed}）。参考实现也是这么做的：那张表是聚合出来的，不是读出来的。",
            f"当前 {len(rows)} 个有成员的组里，{scored} 个够 10 场拟合得出趋势、"
            f"{len(rows) - scored} 个趋势三列为空（组是新建的或历史不足），"
            "空就显示『—』、不补 0。",
            "oper_score 是全市场相对分，全市场的均值自己会随大盘一起摆"
            f"（窗口 {level.get('days', '—')} 场里，全市场等权均值从 {level.get('lo', '—')} "
            f"摆到 {level.get('hi', '—')}，摆幅 {level.get('span', '—')} 分）。"
            "所以趋势分数里混着「今天全市场的水位」，组与组之间横着比可以，跨月纵向比要小心。",
            "预测操作分是外推值，" + pred_span + "不是明天涨幅，也不含明天的任何新信息。",
            f"「近 {GROUP_RANK_DAYS} 场名次」那一列排的是组与组之间，不是全市场：{rank_universe_note}。"
            f"其中 {rank_partial} 个组这一串没凑满（缺那天路径里是「·」，不是最后一名）、"
            f"{len(rows) - len(with_rank)} 个组一场名次都排不出。",
            f"名次是按加权分的原值排的，不是按屏上那两位小数：这一串里按两位小数会并成同一档、"
            f"原值其实有先后的行有 {rank_fake_ties} 行"
            f"（每天最多 {max((p['rounded_ties'] for p in rank_per_session), default=0)} 行）。"
            "所以「同分同名」这条规则只在真同分处生效 —— 看到两行分数一样而名次不同，是显示位不够，不是排错。",
        ],
        "source": "pg", "requests": 0,
    }
