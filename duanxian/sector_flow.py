# -*- coding: utf-8 -*-
"""行业资金流的**逐日累计读数**（读 `market_data.sector_fund_flow_daily`）。

为什么单独一层：这块内容原先**只在被点开页面那一刻现抓一次接口**（`vr/market.py:_sectors()`），
2026-10-06 之前一张历史都没留。现在有了落库（写侧 `ingest/jobs/c22_sector_flow.py`），
这一层负责把"某一天每个行业净流入多少"变成能回答下面两类问题的东西：

* 「近 3 个月哪些行业在被灌、哪些在被抽干」→ `net_sum_3m`；
* 「2024 年以来累计」→ `net_sum_since`（**能算多前取决于表里最早那天**，见 `coverage`）。

一条不许糊过去的事实：这张表**从今天才有第一天**。用户要的"2024-01-01 以来的全部净流入"
在本机算不出来，原因是数据侧的，不是没做：

| 想回溯到 | 现实 |
|---|---|
| 2024-01-01 的行业净流入 | 接口只给「即时」一档；同花顺的 3日/5日/10日 档在本机 akshare 1.18.97 已坏 |
| 用个股主力净额加总上去 | `market_data.fund_flow_rank` 只有 3 场（`c21`，同样无日期字段、不回填），且 sector 一档 0 行 |
| 用份额代替 | `analytics.sector_flow_trend` 有 1,253 场（2021-08 起），但那是**成交额份额**，不是净流入 —— 可以回答"钱相对往哪走"，不能回答"净流入多少亿" |

所以 `long_history` 那一项**明写替代口径**（份额累计，有 2024 年以来），而不是给一个
看起来像净流入的数。窗口口径全在这一份里，页面与首页共用。
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

from . import pg
from . import em_board_link

SOURCE = 'ths_industry_daily_net'
DEFAULT_SINCE = dt.date(2024, 1, 1)      # 用户指定的起点（能算多前由表里的最早那天决定）
RECENT_MONTHS = 3                        # 用户指定的"近 3 个月"
TOP_DEFAULT = 50                         # 用户指定"显示前 50 个"

CALIBERS = {
    'net': ('净流入（亿）= 同花顺行业榜的「净额」原样落库（流入资金 − 流出资金）。'
            '⚠️ 它是**大单方向推断口径**，不是交易所实测的资金流；跨源不可比，'
            '也不能与本地那批通达信板块的"成交额份额"混着读。'),
    'sum_since': ('自 %s 起累计。能算多前**取决于表里最早那天** —— 这张表 2026-10-06 才建，'
                  '更早的历史本机没有（接口只有即时一档、报文里没有日期字段），'
                  '不足一年时会把实际覆盖场次一起交出。'),
    'sum_3m': '近 3 个月（按日历往前推 3 个月，不是"最近 60 场"）累计。',
    'order': '默认序 = 近 3 个月累计**为正、由大到小**；负数排在后面，翻到最后一页能看到被抽干的那些。',
    'long_history': ('真要"2024 年以来谁被持续灌入"只能看**份额**口径：'
                     'analytics.sector_flow_trend 有 1,253 场（2021-08 起）。'
                     '份额说的是"钱相对往哪儿走"，不是"净流入多少亿"，两件事不许混一个列名。'),
    'classification': ('这里的"行业"是同花顺分类，本地板块表是通达信分类，'
                       '**两套体系 join 不上是设计如此** —— 按名字模糊匹配等于造数，所以不做。'),
}


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def coverage() -> dict:
    """表里现在有几场、覆盖到哪天。没有这一格，页面上那些"累计"就没有可解读的分母。"""
    rows = pg.fetch_df("select count(distinct trade_date) n, min(trade_date)::date lo, "
                       "max(trade_date)::date hi from market_data.sector_fund_flow_daily "
                       "where source = %s", (SOURCE,))
    if rows is None or rows.empty:
        return {'available': False, 'reason': '库读不到（不是"没有行业有数"）'}
    r = rows.to_dict('records')[0]
    n = int(r['n'] or 0)
    return {'available': n > 0, 'sessions': n,
            'from': str(r['lo']) if r['lo'] else None,
            'to': str(r['hi']) if r['hi'] else None,
            'requested_since': str(DEFAULT_SINCE),
            'months': RECENT_MONTHS}


def _minus_months(day: dt.date, months: int) -> dt.date:
    """按日历往前推整月（用户说的是"近 3 个月"，不是"最近 60 场"）。"""
    total = day.year * 12 + (day.month - 1) - months
    y, m = divmod(total, 12)
    m += 1
    d = min(day.day, [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return dt.date(y, m, d)


def _calibers(since: str, window_from: str, window_to: str, sessions_window: int,
              sessions_total: int) -> dict:
    """把口径里的占位符**换成当场的实数**再交出去。

    10-08 修的两件事：① `sum_since` 那句原先带着没替换的 `%s` 直接上了屏（用户看到的是
    「自 %s 起累计」—— 占位符当文案）；② 两条累计都只说窗口、不说库里实际有几场，
    于是只有 1 场时读起来像"电力三个月才净流入 27.8 亿"，被当场判成"累计数肯定是错的"。
    数本身没错（90 个行业逐行与同花顺源比对，0 条不一致），是名字在承诺一个还没有的
    窗口 —— 分母要跟着数一起走。
    """
    cal = dict(CALIBERS)
    cal['sum_since'] = (
        f'自 {since} 起累计，但库里这段实际只有 {sessions_total} 场 —— '
        '能算多前取决于表里最早那天：这张表 2026-10-06 才建（接口只有即时一档、'
        '报文里没有日期字段，更早的历史本机没有，也回填不了）。')
    cal['sum_3m'] = (
        f'近 {RECENT_MONTHS} 个月累计：按日历从末场 {window_to} 往前推到 {window_from}'
        f'（不是"最近 60 场"），而库里这段实际只有 {sessions_window} 场。'
        '不满窗时这一列就等于末场那一列 —— 读的是"这几场的和"，不是"这三个月的和"。')
    return cal


def _market_caps() -> dict:
    """行业市值（**末场快照那一份**）：同花顺行业成分 × `stock_snapshot` 逐只相加。

    为什么能拼、为什么必须写清分母：
     · `reference_data.sector_member` 里 THS_ 那批行业是有成分的（10-15 实测 1,140 个板块 / 13.3 万行），
       而资金流这张表的 `sector_name` 在字典里 **90/90 都叫得开** ⇒ 不需要跨体系模糊对齐。
       （`CALIBERS['classification']` 那句"两套体系 join 不上所以不做"讲的是**通达信 vs 同花顺**，
       这里是同花顺自己的行业码配同花顺自己的成分，不是那件事。）
     · 快照只取 `market_data.stock_snapshot` 的**最新一场**（10-15 实测 38 场、max=2026-10-14，
       当日 5,581 行里 5,574 行有流通市值），所以这个比值是"**窗口累计流入 ÷ 今天的体量**"，
       不是逐日时点加权。窗口拉长时分会失真 ⇒ 快照日期必须随数交出、上屏。
    """
    df = pg.fetch_df(
        """with snap as (select * from market_data.stock_snapshot
                          where trade_date = (select max(trade_date) from market_data.stock_snapshot)),
        mem as (select si.name, m.security_code
                  from reference_data.sector_info si
                  join reference_data.sector_member m on m.sector_code = si.sector_code
                 where left(si.sector_code, 4) = 'THS_' and m.out_date is null
                 group by 1, 2)
        select mem.name,
               sum(sn.float_market_value_yuan) / 1e8 as float_yi,
               sum(sn.total_market_value_yuan) / 1e8 as total_yi,
               count(sn.security_code)::int as priced,
               count(*)::int as members,
               max(sn.trade_date)::text as as_of
          from mem join snap sn on sn.security_code = mem.security_code
         group by 1""")
    out = {}
    for r in (df.to_dict('records') if df is not None else []):
        out[r['name']] = {'float_yi': _f(r['float_yi']), 'total_yi': _f(r['total_yi']),
                          'priced': int(r['priced'] or 0), 'members': int(r['members'] or 0),
                          'as_of': r['as_of']}
    return out


def aggregate(*, since: dt.date | None = None, months: int = RECENT_MONTHS,
              limit: int = 0) -> dict:
    """逐行业累计。返回按 `net_sum_recent` 降序的全量（分页在前端做，后端不给"这一页之外不存在"）。

    10-15 按用户要求加了三族列（原来只有"和"与"场数"，读不出大小）：
     · **市值加权**：`net_sum_recent ÷ 行业流通市值`（`float_pct`）—— 同样 20 亿净流入，
       对银行（11.6 万亿）和对油气开采（0.37 万亿）完全不是一件事，不加权就是按体量排座次；
     · **涨跌幅**：窗口内 `pct` 复利累计（`chg_recent_pct`）+ 末场当日（`pct_last`）；
     · **拆开流入与流出**：`inflow_sum_recent / outflow_sum_recent`（原表就有这两列，以前只交差值），
       以及**净流入为正的天数**（`pos_days_recent`）—— 天天小灌与最后一天冲一笔，和相同、含义不同。
    """
    cov = coverage()
    since = since or DEFAULT_SINCE
    if not cov.get('available'):
        return {'available': False, 'rows': [], 'coverage': cov,
                'calibers': _calibers(str(since), '—', '—', 0, 0),
                'reason': ('`market_data.sector_fund_flow_daily` 里还没有任何一场 —— 这不是"没有资金流"，'
                           '是**这张表 2026-10-06 才建**，过去没有任何地方存过这份榜，回填不了。'
                           '每天由加工链的 `sectorflow` 步采一次（手工：'
                           '`python -m ingest.jobs.c22_sector_flow`）。')}
    hi = dt.date.fromisoformat(cov['to'])
    # "近 3 个月"按日历往前推（用户说的是月，不是场次）；推不满就把实际起点交出去
    m_start = _minus_months(hi, months)
    rows = pg.fetch_df(
        """with s as (
             select sector_name,
                    sum(net_yi) filter (where trade_date >= %s::date) as sum_since,
                    count(*)    filter (where trade_date >= %s::date) as days_since,
                    sum(net_yi) filter (where trade_date >= %s::date) as sum_recent,
                    count(*)    filter (where trade_date >= %s::date) as days_recent,
                    sum(inflow_yi) filter (where trade_date >= %s::date) as inflow_recent,
                    sum(outflow_yi) filter (where trade_date >= %s::date) as outflow_recent,
                    count(*) filter (where trade_date >= %s::date and net_yi > 0) as pos_days_recent,
                    -- 区间涨跌幅 = 逐日 pct 复利（exp(sum(ln(1+pct/100)))−1）。
                    -- 只对有 pct 的那些天复利，缺天的行业由 pct_days_recent 暴露分母 ——
                    -- 不拿"缺"当"平"（当平就是把不知道卖成零涨幅）。
                    sum(ln(1 + pct / 100.0)) filter (where trade_date >= %s::date
                                                     and pct is not null and pct > -99.9) as ln_recent,
                    count(*) filter (where trade_date >= %s::date and pct is not null
                                                     and pct > -99.9) as pct_days_recent,
                    max(trade_date)::date as last_day,
                    (array_agg(net_yi order by trade_date desc))[1] as net_last,
                    (array_agg(trade_date order by trade_date desc))[1]::date as net_last_day,
                    (array_agg(pct order by trade_date desc))[1] as pct_last,
                    (array_agg(firms order by trade_date desc))[1] as firms_last
             from market_data.sector_fund_flow_daily
             where source = %s
             group by 1
           )
           select * from s order by sum_recent desc nulls last, sector_name""",
        (since, since, m_start, m_start, m_start, m_start, m_start, m_start, m_start, SOURCE))
    caps = _market_caps()
    out = []
    mv_covered = 0
    for r in (rows.to_dict('records') if rows is not None else []):
        cap = caps.get(r['sector_name']) or {}
        float_yi = cap.get('float_yi')
        total_yi = cap.get('total_yi')
        recent = _f(r['sum_recent'])
        since_sum = _f(r['sum_since'])
        ln_recent = _f(r['ln_recent'])
        pct_days = _f(r['pct_days_recent'])
        days_recent = _f(r['days_recent'])
        if float_yi:
            mv_covered += 1
        out.append({
            'sector_name': r['sector_name'],
            'net_sum_since': since_sum, 'days_since': _f(r['days_since']),
            'net_sum_recent': recent, 'days_recent': days_recent,
            'net_last': _f(r['net_last']), 'net_last_day': str(r['net_last_day']) if r['net_last_day'] else None,
            'pct_last': _f(r['pct_last']), 'firms_last': _f(r['firms_last']),
            # ---- 10-15 新增（用户：「有没有根据行业的总体市值去折算流入流出，来加权计算？还有就是涨跌幅？多加几列」）
            'net_avg_recent': (round(recent / days_recent, 3) if recent is not None and days_recent else None),
            'inflow_sum_recent': _f(r['inflow_recent']), 'outflow_sum_recent': _f(r['outflow_recent']),
            'pos_days_recent': _f(r['pos_days_recent']),
            'chg_recent_pct': (round((math.exp(ln_recent) - 1) * 100, 2)
                               if ln_recent is not None and pct_days else None),
            'chg_days': pct_days,
            'float_mv_yi': round(float_yi, 1) if float_yi else None,
            'total_mv_yi': round(total_yi, 1) if total_yi else None,
            'mv_members': cap.get('members'), 'mv_priced': cap.get('priced'),
            # 加权强度：窗口累计净流入 ÷ 行业流通市值（%）—— 分母是**末场快照**的体量，见 `_market_caps`
            'net_pct_float_recent': (round(recent / float_yi * 100, 3)
                                     if recent is not None and float_yi else None),
            'net_pct_float_since': (round(since_sum / float_yi * 100, 3)
                                    if since_sum is not None and float_yi else None),
        })
    if limit:
        out = out[:limit]
    # 东财板块页那一列（用户 2026-10-15：板块名也要能点开）。对表只住 `reference_data.em_board_link`，
    # 读法只住 `duanxian/em_board_link.py` —— 这里不 join、不在界面拼地址，两块屏幕共用一份。
    em_board_link.decorate(out, kind_of=lambda row: '行业')
    link_status = em_board_link.status()
    # 窗口内实际有几场：各行业的 `days_recent` 取最大值（缺榜的那些场数会更少，
    # 所以这是"这个窗口最多几场"，用它判满没满窗，而不是假装 60 场。
    win_sessions = max([int(x['days_recent'] or 0) for x in out], default=0)
    cov = dict(cov, sessions_recent=win_sessions)
    mv_missing = [x['sector_name'] for x in out if not x.get('float_mv_yi')]
    mv_as_of = next((x['as_of'] for x in caps.values() if x.get('as_of')), None)
    calibers = _calibers(str(since), str(m_start), str(hi), win_sessions, int(cov.get('sessions') or 0))
    calibers.update({
        'weight': ('加权口径：`净流入 ÷ 行业流通市值`（%）。用流通市值是因为它是本地唯一有逐日快照、'
                   '且成分能按同花顺行业码对齐的那一维（10-15 实测 90/90 个行业拼得出市值）；'
                   '总市值同一行一起交出，但**比值只用流通市值一个分母** —— 两个分母各出一列，'
                   '同一屏会出现两套"谁强"的排序，那是又一处"同一个数两把尺"。'),
        'weight_denominator': (f'分母是 **{mv_as_of or "末场"} 那一场快照**的行业成分流通市值合计（不是窗口内每日各自的分母），'
                               '所以这列读作"这段时间的累计流入相对**今天这个体量**有多大"；'
                               '窗口拉长、行业涨过一轮之后，同一笔流入会算出更小的比值 —— 那不是流入变少了。'),
        'chg': ('窗口累计涨跌幅 = 窗口内各天行业涨跌幅**复利**相乘（逐日 ln 相加再还原），'
                '不是"末场涨跌幅"，也不是把百分比直接相加。缺涨跌幅的那些天不参与复利，'
                '复利覆盖了几天由 `chg_days` 交出一一对照；一天都没有时这列是空，不按 0% 平盘。'),
        'pos_days': '净流入为正的天数 ÷ 落库天数：天天小灌与最后一天冲一笔，累计可以相同、这两列会分开。',
    })
    mv_missing = [x['sector_name'] for x in out if not x.get('float_mv_yi')]
    return {'available': True, 'rows': out, 'coverage': cov,
            'calibers': calibers,
            'window': {'since': str(since), 'recent_from': str(m_start),
                       'recent_to': cov['to'], 'top_default': TOP_DEFAULT},
            'positive_recent': sum(1 for x in out if (x['net_sum_recent'] or 0) > 0),
            # 市值那一维的来源与覆盖。没有这一格，"90/90 拼得出"就只是注释里的一句话，
            # 屏幕读不到它**今天**到底覆盖了几个行业、分母出自哪一场。
            'market_value': {'as_of': mv_as_of, 'industries': len(out),
                             'covered': sum(1 for x in out if x.get('float_mv_yi')),
                             'basis': 'reference_data.sector_member（同花顺行业成分）× market_data.stock_snapshot 末场',
                             'missing': mv_missing[:12], 'missing_total': len(mv_missing)},
            # 这一格决定屏幕上"行业名"是不是一个链接。两个分母都要交，且各说各的：
            # `rows_linked/rows_total` 是**这张表这批行业**（同花顺榜单当日那 90 个），
            # `dict_linked/dict_total` 是整张对表（含热度榜的通达信体系）。
            # 只交后一个，屏幕就会在 90 行的表旁边念"354/613"，读起来像"这张表缺了一大片链接"。
            'board_link': ({'available': link_status['available'],
                            'rows_linked': sum(1 for x in out if x.get('em_board_code')),
                            'rows_total': len(out),
                            'dict_linked': (link_status['kinds'].get('行业') or {}).get('linked'),
                            'dict_total': (link_status['kinds'].get('行业') or {}).get('total'),
                            'tried_at': link_status['tried_at'],
                            'reason': link_status['reason'],
                            'basis': 'reference_data.em_board_link（同类才对表 + 精确同名或 Ⅱ/Ⅲ 后缀）'}
                           if link_status['available'] else
                           {'available': False, 'rows_linked': None, 'rows_total': len(out),
                            'dict_linked': None, 'dict_total': None,
                            'tried_at': None, 'reason': link_status['reason'],
                            'basis': 'reference_data.em_board_link'}),
            'definitions': {
                'net_sum_recent': '近 3 个月（按日历推）窗口内净流入合计，单位亿元',
                'net_sum_since': f'自 {since} 起累计净流入（能算多前由表里最早那天决定）',
                'net_avg_recent': '窗口累计 ÷ 落库天数 = 日均净流入（亿/天）',
                'inflow_sum_recent / outflow_sum_recent': '窗口内流入、流出的**绝对额**（各天相加，不互相抵消）',
                'pos_days_recent': '窗口内净流入为正的天数（与 days_recent 一起读才分得出"天天灌"与"冲一笔"）',
                'chg_recent_pct': '窗口累计涨跌幅 %（逐日复利），不是末场那一天',
                'chg_days': '上面那个复利真用了几天 —— 小于 days_recent 说明有些天源里没交涨跌幅',
                'pct_last / net_last': f'末场（{cov["to"]}）当天的涨跌幅与净流入，不是此刻现抓',
                'float_mv_yi / total_mv_yi': '行业成分流通市值 / 总市值合计（亿，末场快照）',
                'net_pct_float_recent': '窗口累计净流入 ÷ 流通市值 ×100（%）＝加权强度',
                'net_pct_float_since': '同上，分子换成"覆盖以来"的累计',
                'mv_members / mv_priced': '该行业在册成分数 / 其中今天有市值快照的只数',
                'em_board_code / em_board_name': '东财板块页那一档的码与真名（点开的新标签页页眉就是这个名字）。'
                                                 '空 = 对表没对上，界面退回纯文本，不拼死链',
            },
            'caveats': (
                ([{'code': 'mv_missing',
                   'text': f'{len(mv_missing)} 个行业拼不出市值（{("、".join(mv_missing[:6]) + ("…" if len(mv_missing) > 6 else ""))}）：'
                           '加权那两列对它们是 —，不是 0%，也不是"没有流入"'}] if mv_missing else [])
                + ([{'code': 'mv_as_of',
                     'text': f'市值分母全部取自 {mv_as_of} 那一场快照（末场），不是各天各自的市值；'
                             '窗口越长、行业自身涨得越多，这个比值与"当时的强度"偏离越大'}] if mv_as_of else [])
                + ([{'code': 'chg_partial',
                     'text': f'{len([x for x in out if (x["chg_days"] or 0) < (x["days_recent"] or 0)])} 个行业的窗口涨跌幅复利用的天数'
                             '少于落库天数（那些天源里没有涨跌幅）—— 那个和只覆盖有涨跌幅的那几天'}]
                   if any((x['chg_days'] or 0) < (x['days_recent'] or 0) for x in out) else [])
                + ([{'code': 'board_link',
                     'text': f"东财板块页只对上 {link_status['kinds'].get('行业', {}).get('linked')}/"
                             f"{link_status['kinds'].get('行业', {}).get('total')} 个名字（对表 {link_status['tried_at']}）——"
                             '没对上的行业名是纯文本。这里只对同类、只认精确同名与 Ⅱ/Ⅲ 后缀，'
                             '名字相近但不是一个族的一律不链'}]
                   if link_status['available'] else
                   [{'code': 'board_link_off',
                     'text': f"东财板块页对表读不到：{link_status['reason']} —— "
                             '这一屏所有行业名退回纯文本（不拼一个猜出来的地址）。'
                             '手工刷一次：`python -m ingest.jobs.c24_em_board_link`'}])
            )}


def live_is_stored(as_of: dt.date | None = None) -> bool:
    """今天的即时榜是不是已经落库了。

    没落库时首页那列必须写明"此刻现抓、未入库"：它是**盘中/盘后某一分钟的截面**，
    与逐日收盘后落的累计不是一个时刻的数，混进同一列就是把两个时刻加起来。
    """
    cov = coverage()
    return cov.get('to') == str(as_of or dt.date.today())
