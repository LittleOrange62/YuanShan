"""P0 本地数据源的具体查询 —— 每个函数产出的结构与对应在线取数**完全同形**，

取不到（未配置 / 连不上 / 该场次无数据）一律返回 None，由 `fetchers` 回退在线源。
表口径（financetool 库）：
  · market_data.stock_daily      个股日线（含 limit_up/limit_down 涨停价）
  · market_data.index_daily      指数日线
  · market_data.sector_daily     板块日线（TDX_/THS_ 前缀代码）
  · market_data.fund_flow_rank   个股/板块资金流排名（scope=stock|sector, window_days=1/3/5/10/20）
  · market_data.dragon_tiger_detail 龙虎榜
  · reference_data.security / sector_info / sector_member / trade_calendar
"""

from __future__ import annotations

import datetime
import re

import pandas as pd

from . import pg
from .util import china_today

YI = 1e8

# 行业名：通达信/同花顺二级行业体系（TDX_881xxx，全市场覆盖），取最深一级；
# 880xxx 里混着省份名与无名占位，不能用。
_INDUSTRY_JOIN = """
    left join lateral (
        select si.name
        from reference_data.sector_member sm
        join reference_data.sector_info si using (sector_code)
        where si.sector_type = '行业'
          and (left(si.sector_code, 7) = 'TDX_881' or left(si.sector_code, 7) = 'THS_881')
          and sm.security_code = sd.security_code
          and (sm.out_date is null or sm.out_date > sd.trade_date)
        order by si.sector_code desc
        limit 1
    ) hy on true
"""


def _dash(date: str) -> str:
    """'YYYYMMDD' / 'YYYY-MM-DD' → 'YYYY-MM-DD'。"""
    s = str(date).replace("-", "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


# 该票的涨跌幅制度（%）—— 与 market_facts.board_of 同一套规则：
# 北交所 30；创业板/科创板（含 ST）20；主板 ST 5；主板其余 10。
_REGIME_CASE = """
    case
        when left(sd.security_code, 1) in ('8', '4')
             or left(split_part(sd.security_code, '.', 1), 3) = '920' then 30.0
        when left(sd.security_code, 2) in ('30', '68') then 20.0
        when position('ST' in upper(sec.name)) > 0 then 5.0
        else 10.0
    end
"""

# 涨停判定：日线里个别行的 limit_up 列与行情不符（数据毛刺），
# 所以按项目既有口径双规则 —— 价格精确对上 或 涨幅落在制度档位内（±0.3），
# 且涨幅不超档位上限（防新股首日等无价格限制的大涨混进来）。
_ZT_PRED = f"""
    ((sd.limit_up is not null and abs(sd.close - sd.limit_up) <= 0.005)
     or (sd.pct_change is not null
         and sd.pct_change >= ({_REGIME_CASE}) - 0.3
         and sd.pct_change <= ({_REGIME_CASE}) + 0.3))
"""

_DT_PRED = f"""
    ((sd.limit_down is not null and abs(sd.close - sd.limit_down) <= 0.005)
     or (sd.pct_change is not null
         and sd.pct_change <= -(({_REGIME_CASE}) - 0.3)
         and sd.pct_change >= -(({_REGIME_CASE}) + 0.3)))
"""


# ---------------------------------------------------------------------------
# 交易日历
# ---------------------------------------------------------------------------
def trade_dates(start: str, end: str) -> "list[str] | None":
    df = pg.fetch_df(
        """
        select calendar_date::text as d
        from reference_data.trade_calendar
        where is_open and calendar_date between %s and %s
        order by 1
        """,
        (start, end),
    )
    return None if df is None else list(df["d"])


# ---------------------------------------------------------------------------
# 龙虎榜（历史场次可直接本地查，收盘前后都优先走本地）
# ---------------------------------------------------------------------------
def lhb(date: str, top: int = 15) -> "list[dict] | None":
    d = _dash(date)
    if not pg.session_has("market_data.dragon_tiger_detail", d):
        return None
    df = pg.fetch_df(
        """
        select substr(t.security_code, 1, 6) as code,
               sec.name,
               t.net_buy,
               left(t.reason, 40) as reason,
               ins.inst_net
        from (
            select security_code, max(net_buy) as net_buy,
                   (array_agg(reason order by net_buy desc nulls last))[1] as reason
            from market_data.dragon_tiger_detail
            where trade_date = %s
            group by security_code
            order by net_buy desc nulls last
            limit %s
        ) t
        join reference_data.security sec using (security_code)
        left join lateral (
            select sum(institution_net) as inst_net
            from market_data.dragon_tiger_institution i
            where i.security_code = t.security_code and i.trade_date = %s
        ) ins on true
        """,
        (d, top, d),
    )
    if df is None:
        return None
    rows = []
    for _, r in df.iterrows():
        nb = None if r["net_buy"] is None else round(float(r["net_buy"]) / YI, 2)
        # 机构净买额：在线那个 akshare 接口给不了（fetchers.fetch_lhb 里恒为 None，
        # 注释写明"从解读推断不可靠"）。本地库有逐日机构汇总，所以这条路能真填上。
        inst = None if r.get("inst_net") is None or pd.isna(r.get("inst_net")) \
            else round(float(r["inst_net"]) / YI, 2)
        rows.append({
            "code": str(r["code"]),
            "name": str(r["name"]),
            "net_buy": nb,
            "reason": str(r["reason"] or ""),
            "inst_net": inst,
        })
    return rows


# ---------------------------------------------------------------------------
# 大盘指数（A 股四项；美股本地无源，仍走腾讯）
# ---------------------------------------------------------------------------
A_INDEX_CODES = {
    "sh000001": "000001.SH", "sz399001": "399001.SZ",
    "sz399006": "399006.SZ", "sh000300": "000300.SH",
}


def indices_a() -> "dict[str, dict] | None":
    """{在线代码: {price, changePct}} —— 取本地最新一场收盘。
    index_daily 的 pct_change 列未落数，用前一场收盘现算涨跌幅。"""
    df = pg.fetch_df(
        f"""
        with latest as (select max(trade_date) as d from market_data.index_daily)
        select i.security_code, i.close::float8 as price,
               case when p.close is not null and p.close > 0
                    then round(((i.close / p.close - 1) * 100)::numeric, 2)::float8
               end as chg
        from market_data.index_daily i
        cross join latest l
        left join lateral (
            select close from market_data.index_daily x
            where x.security_code = i.security_code and x.trade_date < i.trade_date
            order by x.trade_date desc limit 1
        ) p on true
        where i.trade_date = l.d and i.security_code = any(%s)
        """,
        (list(A_INDEX_CODES.values()),),
    )
    if df is None or len(df) < len(A_INDEX_CODES):
        return None
    rev = {v: k for k, v in A_INDEX_CODES.items()}
    return {rev[r.security_code]: {"price": r.price, "changePct": r.chg} for r in df.itertuples()}


# ---------------------------------------------------------------------------
# 成交额 TOP（最近一场收盘）
# ---------------------------------------------------------------------------
def turnover_top(top: int = 20) -> "list[dict] | None":
    df = pg.fetch_df(
        f"""
        select substr(sd.security_code, 1, 6) as code, sec.name,
               sd.close::float8 as price, sd.pct_change::float8 as chg,
               sd.amount_yuan::float8 as amount, hy.name as sector
        from market_data.stock_daily sd
        join reference_data.security sec using (security_code)
        {_INDUSTRY_JOIN}
        where sd.trade_date = (select max(trade_date) from market_data.stock_daily)
        order by sd.amount_yuan desc nulls last
        limit %s
        """,
        (top,),
    )
    if df is None or not len(df):
        return None
    return [{
        "code": str(r.code), "name": str(r.name),
        "price": r.price, "change_pct": r.chg,
        "amount": None if r.amount is None else round(r.amount / YI, 2),
        "sector": r.sector if r.sector and r.sector != "-" else None,
    } for r in df.itertuples()]


# ---------------------------------------------------------------------------
# 概念板块归属（参考数据，与行情来源无关：在线榜也能拼上这一列）
# ---------------------------------------------------------------------------
def concepts_by_code(codes) -> dict:
    """code → {concepts, concept_count}：概念板块归属（参考数据，与行情来源无关，在线榜也能拼）。

    三条不是随手定的口径：
    1. 一只票可属多个概念（实测平均 4.1 个、最多 123 个），且**同一个概念名可能挂在多个板块码上**
       （实测 `减肥药` 有两条板块码）—— 所以先按名字去重再数，`concept_count` 是"几个不同概念"、
       不是"几条成员行"；宽窄取同名板块里成员数最小的一条来判。
    2. 只列前 3 个，顺序是**成员数由少到多**（窄题材排在宽题材前面）：`融资融券``深股通` 那种
       几千只票都挂的宽概念排在后面，才看得出这只票的题材身份。取"当日最热的概念"要的是板块热度，
       本函数手里没有它，硬选就是编。
    3. 没有任何概念行的票**不出现**（界面显示 `—`）—— 实测北交所股票基本没有概念归属，
       `—` 说的是"库里没有"，不等于"它不属于任何题材"。
    """
    codes = [str(c)[:6] for c in (codes or []) if c]
    if not codes:
        return {}
    df = pg.fetch_df(
        """
        with board as (
            select i.name, i.sector_code, count(distinct m.security_code) as members
              from reference_data.sector_member m
              join reference_data.sector_info i using (sector_code)
             where i.sector_type = '概念'
             group by 1, 2
        ),
        link as (
            select distinct left(m.security_code, 6) as code, i.name
              from reference_data.sector_member m
              join reference_data.sector_info i using (sector_code)
             where i.sector_type = '概念'
               and (m.out_date is null or m.out_date > current_date)
               and left(m.security_code, 6) = any(%s::text[])
        ),
        narrow as (
            select l.code, l.name, min(b.members) as members
              from link l join board b using (name)
             group by 1, 2
        ),
        ranked as (
            select code, name,
                   row_number() over (partition by code order by members asc, name asc) as rk,
                   count(*) over (partition by code) as total
              from narrow
        )
        select code, max(total)::int as concept_count,
               string_agg(name, '、' order by rk) filter (where rk <= 3) as concepts
          from ranked group by 1
        """, (codes,))
    if df is None or not len(df):
        return {}
    out = {}
    for r in df.itertuples():
        out[str(r.code)] = {"concepts": r.concepts or None,
                            "concept_count": int(r.concept_count or 0) or None}
    return out


def concept_rollup(codes) -> dict:
    """把一批票**反向**按概念板块聚成榜：概念 → 这批里有几只挂了它。

    与上面 `concepts_by_code` 是同一套成员口径（`sector_type='概念'`、`out_date` 未出、
    **按名字去重**再数），三条共用规矩那边写了，这里不重复发明：
      · 同一个概念名可能挂多条板块码 ⇒ 按名字数，不按板块码数；
      · `sector_alias` 只做跨源（同花顺 THS_ ↔ 通达信 TDX_）板块码映射，**不做同义名合并**，
        所以「新能源车」(1,083 只) 与「新能源汽车」(1,064 只) 是两行 —— 合不合是源站的事，
        这里替他合并就是编；
      · 没有概念行的票不进榜（由调用方说清"榜上有几只有归属"）。

    除了只数还交两件事，因为只数本身会骗人：
      · `members_total` 是这个概念在全市场的成员数 —— `融资融券` 排第一（实测 3,872 只）
        是因为它本身就宽，不是因为这批票有什么题材共性；
      · `lift_pctile` = 这只概念在全市场的成员里，有多大比例进了这批票（= 集中度）。
        同样 30 只，`人工智能` 30/800 与 `卫星导航` 30/120 完全不是一回事。
    """
    codes = [str(c)[:6] for c in (codes or []) if c]
    codes = sorted(set(codes))
    if not codes:
        return {'pool_size': 0, 'covered': 0, 'rows': []}
    df = pg.fetch_df(
        """
        with pool(c) as (select unnest(%s::text[])),
        member as (
            select distinct i.name, left(m.security_code, 6) as code
              from reference_data.sector_member m
              join reference_data.sector_info i using (sector_code)
             where i.sector_type = '概念'
               and (m.out_date is null or m.out_date > current_date)
        ),
        hit as (select name, count(*)::int as stocks
                  from member join pool p on p.c = code group by 1),
        total as (select m.name, count(*)::int as members_total
                    from member m join (select name from hit) h using (name) group by 1),
        samp as (select name, string_agg(code, '、') as sample_codes
                   from (select m.name, m.code,
                                row_number() over (partition by m.name order by m.code) as rk
                           from member m join pool p on p.c = m.code) t
                  where t.rk <= 6 group by 1),
        cov as (select count(distinct code)::int as covered
                  from member join pool p on p.c = code)
        select h.name as concept, h.stocks, t.members_total,
               round(100.0 * h.stocks / %s, 1)::float as pool_share_pct,
               round(100.0 * h.stocks / nullif(t.members_total, 0), 1)::float as lift_pctile,
               s.sample_codes, cv.covered
          from hit h join total t using (name) left join samp s using (name) cross join cov cv
         order by h.stocks desc, lift_pctile desc nulls last, h.name
        """, (codes, len(codes)))
    if df is None:          # 查询坏了 ≠ 这批票没有概念：调用方必须能把这两种分开
        return {'pool_size': len(codes), 'covered': None, 'rows': [], 'failed': True}
    def num(v):
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None

    def i(v):
        f = num(v)
        return None if f is None else int(f)

    rows = [{'concept': r.get('concept'), 'stocks': i(r.get('stocks')),
             'members_total': i(r.get('members_total')),
             'pool_share_pct': num(r.get('pool_share_pct')),
             'lift_pctile': num(r.get('lift_pctile')),
             'sample_codes': r.get('sample_codes') or None}
            for r in df.to_dict('records')]
    # 这批票里**有几只**至少有 1 个概念归属（456 个概念名 ≠ 150 只票都有题材）：
    # 分母要说清，否则"共 456 个概念"会被读成"这批票的题材很分散"。
    covered = i(df['covered'].iloc[0]) if len(df) else 0
    return {'pool_size': len(codes), 'covered': covered or 0, 'rows': rows}


# 「概念板块」这一列 + 反向聚合榜的口径文字：由本模块交出，页面悬停直接显示（同
# FIRST_BOARD_RULE_NOTE / seal_strength 的做法）。这段要上界面，所以里面不许写 markdown 星号。
CONCEPT_RULE_NOTE = (
    "归属读 `reference_data.sector_member` 里 `sector_type=概念` 且在册（`out_date` 为空或还没到）的行。"
    "同一个概念名可能挂多条板块码（实测「减肥药」两条），所以按名字数、不按板块码数；"
    "`sector_alias` 只做跨源（同花顺 ↔ 通达信）板块码映射，不做同义名合并 —— "
    "「新能源车」与「新能源汽车」在源站是两条，这里替他合成一条就是编。"
    "逐票那一列只列前 3 个，顺序是成员数由少到多（窄题材在前）：`融资融券``深股通` 这种几千只票都挂的"
    "宽概念排在后面，才看得出这只票的题材身份。一只票可以有多个概念（实测中位 14 个、最多 90 个），"
    "反向数概念时每一条归属都算一次，不按只数摊薄。"
    "反向榜的分母是当前这一批榜上的票，不是全市场：所以「某概念 30 只」要连着"
    "「全市场成员数」和「集中度」一起读，否则第一名永远是宽概念。"
    "没有概念行的票显示 —：实测北交所的票基本没有概念归属，— 说的是库里没有这一行，"
    "不等于它不属于任何题材。")


# 「首板日大盘情绪」这一列的口径文字：由本模块交出、页面悬停直接显示（同 seal_strength /
# board_span 的做法）。这段文字要上界面，所以常量里不许写 markdown 星号。
FIRST_BOARD_RULE_NOTE = (
    "「首板日」= 这一轮最早的那个封板日（就是「几天几板」悬停里那串日期的第一个），不是今天。"
    "主读数取 analytics.market_sentiment_daily：宽度档分 冰点/偏弱/中性/偏强/普涨、"
    "题材档分 冰点/普通/活跃/亢奋，两档各带一个分值（近 30 场实测一场不缺，10-10 复核）。"
    "悬停末尾那个「大盘情绪分」取 analytics.score 的 market_mood（四维象限 Y 轴的分量），"
    "它只从 2026-06-29 起有数、最近 30 场实测缺 5 天（09-03/09-04/09-07/09-15/09-16），"
    "所以只作注记：那天没有就空着，不填 0 —— 0 在这把尺子上的意思是「中性」，不是「没数」。"
    "整格空着（—）有两种：这一轮的封板日本地没数出来，或那一天的档位表没行 —— 都不是「那天没情绪」。")


def sentiment_on_dates(dates) -> dict:
    """若干个交易日的大盘情绪读数：**以 `analytics.market_sentiment_daily` 为准，`market_mood` 只做注记。**
    为什么不拿 `market_mood`（四维象限 Y 轴里那个"大盘情绪"分量）当这一格的主读数：
    它只从 2026-06-29 起有数，且**最近 30 个交易日实测缺 5 天**（09-03 / 09-04 / 09-07 /
    09-15 / 09-16）；`market_sentiment_daily` 是 1,255 场（2021-08-02→今）、同样这 30 场**一场不缺**。
    首板日落在缺档那天时，mood 交回 None（界面空着并说明"那天没有这个分"），**不填 0** ——
    0 在这把尺子上是"中性"，会被读成"情绪不冷不热"，而事实是"没这个数"。

    返回 `{'2026-09-30': {...}, ...}`；库不通或查询坏了时**额外带 `failed: True`**。
    调用方必须把「那一天没有行」和「这一批整批没读到」分开说：前者是数据现实，后者是链路坏了。
    """
    want = sorted({str(d)[:10] for d in (dates or []) if d})
    if not want:
        return {}
    df = pg.fetch_df(
        """
        with d(td) as (select unnest(%s::date[])),
        -- 这一句原来不带日期条件：`analytics.score` 里 market_mood 是**每场每票一行**
        -- （2026-10-26 实测全史 distinct 出来 272 场、扫的是百万行级），
        -- 而下游 left join 只按 d.td 取那几天 ⇒ 其余的全扫一遍再丢掉。首页那一块实测 4.46 秒
        -- 就花在这里（限定到要的那几天 0.27 秒）。加了条件，交出的行一模一样。
        mood as (select distinct trade_date, value from analytics.score
                  where capability_key = 'market_mood' and trade_date in (select td from d))
        select d.td::text as day, m.breadth_level, m.breadth_score::float as breadth_score,
               m.theme_level, m.theme_score::float as theme_score,
               m.limit_up_real::int as limit_up_real, m.up_count::int as up_count,
               m.down_count::int as down_count, m.ratio::float as ratio,
               m.source, mo.value::float as mood
          from d
          left join analytics.market_sentiment_daily m on m.trade_date = d.td
          left join mood mo on mo.trade_date = d.td
        """, (want,))
    if df is None:
        return {'failed': True}

    def num(v):
        # pandas 把 LEFT JOIN 没命中的格子读成 NaN；NaN 进 JSON 会产出非法的 `NaN` 字面量
        # （浏览器 JSON.parse 直接炸），出口这一层必须把它收成 None。
        try:
            f = None if v is None else float(v)
        except (TypeError, ValueError):
            return None
        return None if f != f else f

    def i(v):
        # 整数列同样会因 LEFT JOIN 没命中被 pandas 升成 float（列里出现 NaN 就把 int 变 float），
        # 所以这里也必须走 num 再收成 int，不能直接交 pandas 的值。
        f = num(v)
        return None if f is None else int(f)

    def txt(v):
        # 文字列也一样：一列里既有 str 又有 None 时 pandas 用的是 StringDtype，缺的那格交回来是
        # **float NaN 而不是 None**（10-10 实测 2026-10-07 那场没行时就这样）。NaN 进 JSON 同样是非法字面量。
        return None if v is None or v != v else str(v)

    out = {}
    for r in df.to_dict('records'):
        out[str(r.get('day'))[:10]] = {
            'breadth_level': txt(r.get('breadth_level')), 'breadth_score': num(r.get('breadth_score')),
            'theme_level': txt(r.get('theme_level')), 'theme_score': num(r.get('theme_score')),
            'limit_up_real': i(r.get('limit_up_real')), 'up_count': i(r.get('up_count')),
            'down_count': i(r.get('down_count')), 'ratio': num(r.get('ratio')),
            'source': txt(r.get('source')), 'mood': num(r.get('mood'))}
    return out


#: 「启动前量能比对」那一屏的**情绪列**：把当天那两档合到一根 -100~100 的尺子上。
#: 权重与各档的理论极值只住这里一处（屏幕、接口、文档都读这里，别在界面再算一遍）。
MOOD_EXTREMES = {'breadth': 2.0, 'theme': 1.5}
MOOD_WEIGHTS = {'breadth': 0.5, 'theme': 0.5}
MOOD_SCALE_NOTE = (
    "这根 -100~100 的尺子只做了两件事：把当天那两档读数各归到 -100~100，再一半分。"
    "宽度档取 analytics.market_sentiment_daily 的 breadth_score（冰点 −2／偏弱 −1／中性 0／偏强 +1／普涨 +2，"
    "除以理论极值 2 再乘 100）；题材档取 theme_score（冰点 −1.5／普通 −0.5／活跃 +0.5／亢奋 +1.5，"
    "除以 1.5 再乘 100）。两档各占 50%，所以满打满算 +100 = 普涨 + 亢奋，−100 = 冰点 + 冰点。"
    "三点要说清：① 这是描述性合成、不是市场规律 —— 50/50 是我给的同权先验，"
    "打板的人可以把题材看得更重，两档原值就在悬停里，随时能自己换权重核；"
    "② 题材那一族库里没有 0 档（只有 ±0.5 与 ±1.5），所以「宽度中性 + 题材普通」这种最常见的平淡日"
    "算出来是 −17 而不是 0，那是档位表的形状，不是我把日子判空了；"
    "③ 同一天的所有票是同一个数 —— 它是全市场那一场的读数，没有横截面意义，"
    "所以这一列只用来读「这批票是在什么样的市场环境里启动的」，不用来比谁强。"
    "缺任何一档就整格交空（不是 0）：0 在这把尺子上是「中性」，不是「没数」。")


def mood_score(sp) -> dict:
    """`sentiment_on_dates` 里某一天的格子 → 一根 -100~100 的情绪读数（纯函数，不碰库）。

    传 None（那一天在档位表里没有行）或两档缺一档时，`mood_score` 交回 None 并说明为什么，
    **不拿单档充数、也不填 0**。
    """
    sp = sp or {}
    b, t = sp.get('breadth_score'), sp.get('theme_score')
    out = {'mood_score': None, 'mood_breadth': sp.get('breadth_level'),
           'mood_breadth_score': b, 'mood_theme': sp.get('theme_level'), 'mood_theme_score': t,
           'mood_parts': '', 'mood_missing': None, 'mood_source': sp.get('source')}
    if b is None and t is None:
        out['mood_missing'] = ('那天在 analytics.market_sentiment_daily 里没有行'
                               '（不是「那天没情绪」，是这一天没有这场记录）')
        return out
    if b is None or t is None:
        out['mood_missing'] = '两档里缺了%s，一根尺子要两档都在才算得出' % (
            '宽度' if b is None else '题材')
        return out
    parts = {'breadth': b / MOOD_EXTREMES['breadth'] * 100,
             'theme': t / MOOD_EXTREMES['theme'] * 100}
    score = sum(MOOD_WEIGHTS[k] * parts[k] for k in parts)
    out['mood_score'] = int(round(score))
    out['mood_parts'] = '宽度 %s(%+.1f→%+.0f) · 题材 %s(%+.1f→%+.0f)' % (
        out['mood_breadth'] or '缺', b, parts['breadth'],
        out['mood_theme'] or '缺', t, parts['theme'])
    return out


def first_board_view(day, sp) -> dict:
    """把「首板日 + 那一天的档位读数」收成连板榜这一格要交的字段（纯函数，不碰库）。

    `sp` 是 `sentiment_on_dates` 里那一天的格子；传 `None` 表示那一天在档位表里没有行，
    这时除 `first_board_day` 外全部交 None（页面显示 —），**不拿"今天"的档位冒充首板日**。
    """
    blank = {'first_board_day': None, 'first_board_breadth': None, 'first_board_breadth_score': None,
             'first_board_theme': None, 'first_board_theme_score': None, 'first_board_mood': None,
             'first_board_limit_up': None, 'first_board_note': ''}
    d = str(day)[:10] if day else None
    sp = sp or {}
    if not d:
        return dict(blank)

    def sc(v):
        # 0 不带正号：这一把尺子上 0 是「中性」那一格，写成 +0.0 会让人以为它偏多一格
        return '' if v is None else ('0.0' if v == 0 else '%+.1f' % v)

    def b(key):
        # 档位字段是 `breadth_level`/`theme_level`，分值是 `breadth_score`/`theme_score`：
        # 拼错一个后缀不会报错，只会让悬停永远显示「缺」，所以这里显式把两个后缀都写出来。
        v = sp.get(key + '_level')
        s = sc(sp.get(key + '_score'))
        return '%s%s' % (v or '缺', '' if s == '' else '(%s)' % s)

    if not (sp.get('breadth_level') or sp.get('theme_level')):
        # 那一天整行没有（不是场次，或那一天的加工没落库）：只说这一件事，
        # 不要把「涨停 — 家」「宽度 缺」一并堆上去 —— 那读起来像"那天什么都没涨"。
        return dict(blank, first_board_day=d, first_board_note=(
            '首板日 %s 在 analytics.market_sentiment_daily 里没有行'
            '（不是「那天没情绪」，是这一天没有这场记录）' % d))

    bits = ['首板日 %s' % d, '宽度 ' + b('breadth'), '题材 ' + b('theme')]
    bits.append('涨停 %s 家' % ('—' if sp.get('limit_up_real') is None else sp.get('limit_up_real')))
    if sp.get('up_count') is not None or sp.get('down_count') is not None:
        bits.append('涨/跌 %s/%s' % ('—' if sp.get('up_count') is None else sp.get('up_count'),
                                     '—' if sp.get('down_count') is None else sp.get('down_count')))
    m = sp.get('mood')
    bits.append('大盘情绪分 %s' % ('那天没有这个分（market_mood 从 2026-06-29 起才有，最近 30 场缺 5 天）'
                                   if m is None else sc(m)))
    return {'first_board_day': d, 'first_board_breadth': sp.get('breadth_level'),
            'first_board_breadth_score': sp.get('breadth_score'), 'first_board_theme': sp.get('theme_level'),
            'first_board_theme_score': sp.get('theme_score'), 'first_board_mood': m,
            'first_board_limit_up': sp.get('limit_up_real'), 'first_board_note': '｜'.join(bits)}


# ---------------------------------------------------------------------------
# 个股资金流排名（fund_flow_rank 到货后自动切本地；空表回退在线）
# ---------------------------------------------------------------------------
def individual_fund_flow() -> "tuple[dict, list] | None":
    df = pg.fetch_df(
        """
        select substr(sd.security_code, 1, 6) as code,
               f.object_name as name,
               sd.close::float8 as price,
               coalesce(sd.pct_change::float8, f.pct_change::float8) as chg,
               f.main_net::float8 as inflow_raw
        from market_data.fund_flow_rank f
        join market_data.stock_daily sd
          on (sd.security_code = f.object_code
              or substr(sd.security_code, 1, 6) = f.object_code)
         and sd.trade_date = f.trade_date
        where f.scope = 'stock' and f.window_days = 1
          and f.trade_date = (select max(trade_date) from market_data.fund_flow_rank
                              where scope = 'stock' and window_days = 1)
        order by f.main_net desc nulls last
        """
    )
    if df is None or not len(df):
        return None
    by_code, flat = {}, []
    for r in df.itertuples():
        rec = {"code": str(r.code), "name": str(r.name or ""), "price": r.price,
               "change_pct": r.chg, "inflow_raw": r.inflow_raw}
        by_code[rec["code"]] = rec
        flat.append(rec)
    return by_code, flat


# ---------------------------------------------------------------------------
# 板块资金流（sector_t: '2'=行业, '3'=概念；三窗口 1/5/10 日）
# ---------------------------------------------------------------------------
def sector_flow(sector_t: str) -> "list[dict] | None":
    stype = {"2": "行业", "3": "概念"}.get(str(sector_t))
    if not stype:
        return None
    df = pg.fetch_df(
        """
        with latest as (
            select max(trade_date) as d from market_data.fund_flow_rank
            where scope = 'sector'
        ),
        flows as (
            select f.object_code,
                   max(f.object_name) filter (where f.window_days = 1) as name,
                   max(f.pct_change)  filter (where f.window_days = 1) as chg,
                   max(f.main_net)    filter (where f.window_days = 1) as t1,
                   max(f.main_net)    filter (where f.window_days = 5) as t5,
                   max(f.main_net)    filter (where f.window_days = 10) as t10
            from market_data.fund_flow_rank f
            cross join latest l
            join reference_data.sector_info si
              on si.sector_code = f.object_code
              or right(si.sector_code, 6) = right(f.object_code, 6)
            where f.scope = 'sector' and f.trade_date = l.d and si.sector_type = %s
            group by f.object_code
        )
        select object_code, name, chg::float8 as chg,
               t1::float8 as t1, t5::float8 as t5, t10::float8 as t10
        from flows
        order by t1 desc nulls last
        """,
        (stype,),
    )
    if df is None or not len(df):
        return None
    return [{
        "bk_code": str(r.object_code), "name": str(r.name or ""),
        "change_pct": r.chg, "inflow_raw": r.t1,
        "in5_raw": r.t5, "in10_raw": r.t10,
        "lead_stock": "",  # 本地源无领涨股字段，下游按空串渲染
    } for r in df.itertuples()]


# ---------------------------------------------------------------------------
# 涨停池 / 炸板 / 跌停（由 stock_daily 推导，仅覆盖本地已有场次）
# ---------------------------------------------------------------------------
def zt_pool(date: str) -> "dict | None":
    """与 fetchers.fetch_zt_pool 同形。封板时间/炸板次数日线推不出 → 留空，
    下游封板质量类统计对空值按缺数据处理；连板数由本地历史精确计算。"""
    d = _dash(date)
    if not pg.session_has("market_data.stock_daily", d):
        return None
    zt_df = pg.fetch_df(
        f"""
        select sd.security_code, substr(sd.security_code, 1, 6) as code,
               sec.name, hy.name as sector
        from market_data.stock_daily sd
        join reference_data.security sec using (security_code)
        {_INDUSTRY_JOIN}
        where sd.trade_date = %s and {_ZT_PRED}
        """,
        (d,),
    )
    if zt_df is None:
        return None
    # 连板数：往前数连续涨停的交易日（回看 ~90 个自然日足够覆盖高标）
    start = (datetime.date.fromisoformat(d) - datetime.timedelta(days=90)).isoformat()
    hist = pg.fetch_df(
        f"""
        select sd.security_code, sd.trade_date::text as d
        from market_data.stock_daily sd
        join reference_data.security sec using (security_code)
        where sd.trade_date between %s and %s and {_ZT_PRED}
        """,
        (start, d),
    )
    counts = pg.fetch_df(
        f"""
        select
          count(*) filter (where sd.limit_up is not null
                                    and sd.high >= sd.limit_up - 0.005
                                    and not ({_ZT_PRED})) as zb,
          count(*) filter (where {_DT_PRED}) as dt
        from market_data.stock_daily sd
        join reference_data.security sec using (security_code)
        where sd.trade_date = %s
        """,
        (d,),
    )
    if hist is None or counts is None:
        return None
    by_date: dict[str, set] = {}
    for r in hist.itertuples():
        by_date.setdefault(r.d, set()).add(r.security_code)
    sessions = sorted(by_date)

    def consec(code_full: str) -> int:
        n = 0
        for s in reversed(sessions):
            if code_full in by_date[s]:
                n += 1
            else:
                break
        return n

    zt_df = zt_df.copy()
    zt_df["连板数"] = [consec(c) for c in zt_df["security_code"]]
    zt_df["首次封板时间"] = ""
    zt_df = zt_df.rename(columns={"code": "代码", "name": "名称", "sector": "所属行业"})
    zt_df = zt_df[["代码", "名称", "连板数", "所属行业", "首次封板时间"]]

    out = {
        "zt": zt_df,
        "zb_count": int(counts["zb"].iloc[0] or 0),
        "dt_count": int(counts["dt"].iloc[0] or 0),
        "highest_consec": int(zt_df["连板数"].max()) if len(zt_df) else 0,
        "ladder": [],
    }
    for _, r in zt_df.sort_values("连板数", ascending=False).iterrows():
        out["ladder"].append({
            "code": str(r["代码"]), "name": str(r["名称"]),
            "consec_boards": int(r["连板数"]), "sector": str(r["所属行业"] or ""),
            "first_seal_time": "",
        })
    out["source"] = "pg"
    return out


def breadth(date: str) -> "dict | None":
    """某场的全市场宽度 + 涨停/触板/炸板家数。

    形状对齐 `breadth.market_breadth()` 的 payload，让它能直接换源而不破坏
    `_payload_ok` 校验和 prompt 渲染。

    ⚠️ 刻意**不读** `analytics.market_breadth` 那张现成的物化表：它的涨停判据是
       `close >= limit_up - 0.01`（单边），会把"收盘高于涨停价"的行也算成涨停。
       实测 2026-09-22 该表给 70 家，其中 5 家（000056/002082/002581/002650/600337）
       当日实际涨 5.88%~9.99% 却按 5% ST 档算的涨停价 —— `reference_data.security.is_st`
       是过期快照，那些票早已不受 5% 约束。这里改用与 `_ZT_PRED` 同一套双边判据。
    """
    d = _dash(date)
    if not pg.session_has("market_data.stock_daily", d):
        return None
    df = pg.fetch_df(
        f"""
        with x as (
            select sd.pct_change, sd.amount_yuan,
                   ({_ZT_PRED})                                as is_zt,
                   (sd.limit_down is not null
                    and abs(sd.close - sd.limit_down) <= 0.005) as is_dt,
                   (sd.limit_up is not null
                    and sd.high >= sd.limit_up - 0.005)         as is_touched,
                   (sd.limit_up is not null
                    and sd.high >= sd.limit_up - 0.005
                    and not ({_ZT_PRED}))                       as is_broken,
                   (left(sd.security_code, 1) in ('8', '4')
                    or left(split_part(sd.security_code, '.', 1), 3) = '920') as is_bj
            from market_data.stock_daily sd
            join reference_data.security sec using (security_code)
            where sd.trade_date = %s
        )
        select count(*) filter (where not is_bj and pct_change > 0)::int as up,
               count(*) filter (where not is_bj and pct_change < 0)::int as down,
               count(*) filter (where not is_bj and pct_change = 0)::int as flat,
               coalesce(sum(amount_yuan) filter (where not is_bj), 0)::float8 as amount,
               count(*)::int as universe,
               count(*) filter (where pct_change >= 5)::int as up5,
               count(*) filter (where pct_change <= -5)::int as down5,
               count(*) filter (where is_zt)::int as limit_up,
               count(*) filter (where is_dt)::int as limit_down,
               count(*) filter (where is_touched)::int as touched,
               count(*) filter (where is_broken)::int as broken
        from x
        """,
        (d,),
    )
    if df is None or not len(df) or int(df["universe"].iloc[0]) == 0:
        return None
    r = df.iloc[0]
    touched, sealed, broken = int(r["touched"]), int(r["limit_up"]), int(r["broken"])
    return {
        "available": True,
        "date": d,
        "up": int(r["up"]), "down": int(r["down"]), "flat": int(r["flat"]),
        # 口径如实写出来：这是我们自己数出来的 A 股，不是东财指数页那两个数
        "up_down_scope": "沪深 A 股（本地日线自数，不含北交所）",
        "amount_yi": round(float(r["amount"]) / YI, 0),
        "universe": int(r["universe"]),
        "deep_up_5_incl": int(r["up5"]), "deep_down_5": int(r["down5"]),
        "dist_scope": "全 A 含北交所（本地日线自数）",
        "dist_available": True,
        "dist_partial": False,
        "requests": 0,
        "source": "pg",
        # 以下四项目前在线宽度取不到，是本地库白送的
        "limit_up_count": sealed,
        "limit_down_count": int(r["limit_down"]),
        "touched_count": touched,
        # 名字里写清分母：炸板率 = 触板没封住 / 触板。
        # 别和 `market_facts.seal_quality()["broken_rate"]`（炸板池/(涨停+炸板池)）混用。
        "broken_of_touched_count": broken,
        "broken_of_touched_rate": round(broken / touched * 100, 2) if touched else None,
    }


# ---------------------------------------------------------------------------
# 板块周期（蓄势/点火/延续/退潮）
# ---------------------------------------------------------------------------
# 通达信这批"板块"其实是行为统计（昨日涨停/昨日突涨/连续亏损…）或全市场聚合指标
# （板块趋势/轮动趋势/最近情绪指数），把它们当题材看会得出同义反复的结论
# （"昨日突涨正在蓄势"）。判据别靠名单硬编 —— 这里只打标，同时把 sector_type
# 一起交出去，让界面按"概念/行业/风格"自己决定默认展示哪一类。
_STAT_SECTOR = r"^昨[日天]|^今[日天]|趋势|情绪指数|^连续亏损|^ST板块|首板|连板"


def sector_cycle(date: str, scope: str = "sector") -> "list[dict] | None":
    """某场的板块周期榜 —— 数据来自本地库 `analytics.ignition`（FinanceTool 每晚算）。

    YuanShan 原本**没有**这一层：题材的"发酵/退潮"只出现在分析师 prompt 的定性
    要求里（`analysts.py:54`），没有可复算的判定。这里补的是确定性读数，
    不是把某个词换成另一个词。

    五档 stage 的判据由库那边定义（列注释里写得很细）：
      ignite 点火 = 前 5 日内曾蓄势 + 今日量能/广度/点火三项达标
      follow 延续 = 近 3 日点过火且热度 3 日斜率仍正
      fade   退潮 = 热度 120 日分位 ≥0.80 且斜率转负
      coil   蓄势 = 波动放大 + 低位（只是前置观察，不是买入清单）
    ⚠️ 已知边界（库注释原文）：230 个行业板块没有涨跌家数，`term_count` 最多 4，
       所以它们**永远不会**被判成 ignite —— 榜上看不到它们不是没轮动，是算不出。
    """
    d = _dash(date)
    if not pg.session_has("analytics.ignition", d):
        return None
    df = pg.fetch_df(
        """
        select i.object_key as code,
               coalesce(si.name, i.object_key) as name,
               coalesce(si.sector_type, '') as stype,
               i.stage, i.score, i.squeeze, i.position_score, i.ignition,
               i.volume, i.breadth, i.term_count,
               i.detail ->> 'heat_pctile'  as heat_pctile,
               i.detail ->> 'slope3'      as slope3,
               i.detail ->> 'vol_ratio'   as vol_ratio,
               i.detail ->> 'share_ratio' as share_ratio,
               i.detail ->> 'up_ratio'    as up_ratio
        from analytics.ignition i
        left join reference_data.sector_info si on si.sector_code = i.object_key
        where i.trade_date = %s and i.scope = %s
        order by case i.stage when 'ignite' then 0 when 'follow' then 1
                              when 'coil' then 2 when 'fade' then 3 else 4 end,
                 i.score desc nulls last
        """,
        (d, scope),
    )
    if df is None or not len(df):
        return None
    rows = []
    for r in df.itertuples():
        num = lambda v: None if v is None or pd.isna(v) else round(float(v), 2)  # noqa: E731
        rows.append({
            "code": str(r.code), "name": str(r.name), "stage": str(r.stage),
            "sector_type": str(r.stype),
            "score": num(r.score),
            # 五要素分开给，页面要能回答"凭什么说它点火"，不能只留一个综合分
            "elements": {
                "squeeze": num(r.squeeze), "position": num(r.position_score),
                "ignition": num(r.ignition), "volume": num(r.volume),
                "breadth": num(r.breadth),
            },
            "term_count": None if pd.isna(r.term_count) else int(r.term_count),
            "heat": {"pctile": num(r.heat_pctile), "slope3": num(r.slope3),
                     "vol_ratio": num(r.vol_ratio), "share_ratio": num(r.share_ratio),
                     "up_ratio": num(r.up_ratio)},
            # 统计性占位板块（板块趋势/轮动趋势/最近情绪指数…）打标，由界面决定藏不藏
            "synthetic": bool(re.search(_STAT_SECTOR, str(r.name))),
        })
    return rows


# ---------------------------------------------------------------------------
# 技术指标（35 项，本地库每日算好）
# ---------------------------------------------------------------------------
def full_code(code: str) -> str:
    """六位代码 → 库里的 `security_code`（带 .SH/.SZ/.BJ 后缀）。

    判据与 `_REGIME_CASE` 同源：8/4 开头或 920 段是北交所，6 开头是沪市，其余深市。
    """
    c = str(code).strip().split(".")[0]
    if len(c) != 6 or not c.isdigit():
        return c
    if c[0] in "84" or c.startswith("920"):
        return f"{c}.BJ"
    return f"{c}.SH" if c[0] == "6" else f"{c}.SZ"


def indicators(code: str, date: str = "") -> "dict | None":
    """某票某场的技术指标读数（MA/MACD/Bias/ATR/CCI/量比/250日位置/相对板块强度…）。

    YuanShan 原本**一个技术指标都没有**（`duanxian`/`vr`/前端全零命中），而它自己的
    个股研究页又确实要展示"现在处在什么位置"。这里不装 stockstats 现算：
    本地库 `analytics.indicator_value` 已按日存 35 项、`config.indicator` 还带
    公式与单位，现算只会多一条要自己维护复权口径的链路。

    指标值本身是**描述量，不是信号** —— 键名/单位原样交出，判读留给使用者。
    """
    sc = full_code(code)
    d = _dash(date) if date else pg.latest_session("analytics.indicator_value")
    if not d:
        return None
    df = pg.fetch_df(
        """
        select v.indicator_key as key, coalesce(i.name, v.indicator_key) as name,
               coalesce(i.output_unit, '') as unit, i.decimals,
               v.value::float8 as value, v.trade_date::text as d
        from analytics.indicator_value v
        left join config.indicator i using (indicator_key)
        where v.security_code = %s and v.trade_date = %s
        order by v.indicator_key
        """,
        (sc, d),
    )
    if df is None or not len(df):
        return None
    out = {}
    for r in df.itertuples():
        out[r.key] = {
            "name": str(r.name), "unit": str(r.unit or ""),
            "value": None if pd.isna(r.value) else float(r.value),
            "decimals": None if pd.isna(r.decimals) else int(r.decimals),
        }
    return {"available": True, "code": str(sc), "date": str(df["d"].iloc[0]),
            "count": len(out), "indicators": out, "source": "pg"}


def snapshot_of_today() -> "str | None":
    """stock_daily 最新一场（YYYY-MM-DD），供上层判断'本地是否就是当前场次'。"""
    df = pg.fetch_df("select max(trade_date)::text as d from market_data.stock_daily")
    if df is None or df["d"].isna().iloc[0]:
        return None
    return str(df["d"].iloc[0])


def local_is_settled() -> bool:
    """现在是否处于本地数据可信的时段（当日已收盘，或本地最新场次就是上一交易日）。
    盘中优先在线（本地日线可能滞后于实时接口）。"""
    from .util import is_a_share_closed

    latest = snapshot_of_today()
    if latest is None:
        return False
    return is_a_share_closed() or latest < china_today()
