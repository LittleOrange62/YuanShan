# -*- coding: utf-8 -*-
"""「个股深度复盘 · 个股信息」读侧：一屏最上方那张卡要的全部读数，**只读本机库**。

为什么单独一块、而且摆在最上面（用户 2026-10-16 点名「把个股信息这些挪上去」）：
下面那几块（档案 / 趋势 / 财务 / 量价 / 个股研究）都是"某一面"的数，而读者先要知道
**这一只是什么、今天什么价、归在哪些板块、我在本机把它放在哪个组里**。
这些信息本机库早有，且比公开源那一发稳定（`/api/stock-data` 那张卡要出网、休市/断供就没数）。

三条口径纪律：
1. **每个数都带自己的日期**。行情/估值是"这一只最近一场"，档案（主营、员工数、β、历史高低）
   是通达信个股档案的最近一次刷新，两者不同日不说成同日；档案给 `profile_age_days`，
   超过 30 天屏上就报"这份档案 X 天没刷"。
2. **缺就是 null，不顶 0**。`stock_snapshot` 的 `eps/bps/roe_percent` 在本机全表是 0
   （实测 216,517 行非零 0 行，见《数据加工工作指南/10》），所以这一块**不交那三个字段**，
   每股与 ROE 类一律从财务多期那一块读（源＝同花顺）。
3. 主档里认不出的代码（没登记、或是 ETF/港美股）直说"本机主档里没有这一只"，
   不拿号段猜一个板块出来。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from app.db import engine

from . import pg_sources as PS
from .quant import jsonable

YI = PS.YI

CALIBER = (
    '全部只读本机库：名称/板块/ST 标记来自 `reference_data.security`（那份主档没有上市日与总股本两列，'
    '本机全表为空，见下面那条说明）；最近一场行情读 '
    '`market_data.stock_daily`（成交额、换手率、当日触板价）；即时估值读 '
    '`market_data.stock_snapshot` 同一场（PE-TTM、PB、总市值、流通市值）；'
    '主营/员工数/IPO 价/历史高低/β 读 `reference_data.stock_profile`（通达信个股档案，'
    '按它自己的刷新日，不假装与行情同日）；板块与概念归属读 `reference_data.sector_member` '
    '× `sector_info`；本机分组归属读 `reference_data.watchlist_member_snapshot` 最新一场。'
    '每股类与 ROE 不在这一块：`stock_snapshot` 那几列在本机全是 0，读它会读出假数，'
    '要看就去下面「财务多期与外推」那一块。'
    '另有一格 AI 公司说明读 `analytics.stock_brief`：它不是本机算出来的读数，'
    '是你点按钮那一刻由模型读下面「个股研究」那一整块写成的正文，存进库只为让'
    '「双击开新窗」与「从导航进来」看到同一份；材料边界与生成时刻由 '
    '`duanxian/stock_brief.py` 一处交出（见那一格自己的口径句）。'
)

DEFINITIONS: Dict[str, str] = {
    'trade_date': '这一行行情属于哪一场（不是采集时间）。休市时它就是最近一个交易日。',
    'close': '收盘价（元，不复权）。要算跨年的涨幅请看下面「趋势量化」那一块，它走后复权。',
    'pct_change': '当日涨跌幅 %，由本机按 `close / pre_close` 落库的那一份。',
    'amount_yi': '成交额（亿元）。',
    'turnover_rate': '换手率 %（当日成交量 ÷ 流通股本）。',
    'limit_up': '当日涨停价（元）。档位按板块与 ST 状态定：主板 10%、创业板/科创板 20%、'
                '主板 ST 也走 10%（2026-10 修正过），北交所 30%。取不到就是 null，不是"没有涨跌停"。',
    'pe_ttm': '滚动市盈率（即时值，来自收盘快照）。这一列在快照缺采时就是 null。',
    'pb': '市净率（最近一期报表口径）。',
    'total_mv_yi': '总市值（亿元）。',
    'float_mv_yi': '流通市值（亿元）—— 下面量价那一块的"盘子大小"用的是它。',
    'main_business': '主营/行业标签（通达信个股档案）。只有一个标签、没有层级树，'
                     '所以它回答"这票大致做什么"，不回答产业链位置。',
    'staff_num': '员工数（人，档案口径，年度更新）。',
    'ipo_price': 'IPO 发行价（元，不复权）。与今天的价直接比没有意义，中间隔着送转与分红。',
    'his_high': '历史最高价（档案给的，不复权）。',
    'his_low': '历史最低价（档案给的，不复权）。',
    'beta': 'β 系数（档案口径，与本股行情不同源、不同窗口）—— 只是参考，不参与任何判据合分。',
    'list_date': '上市日期。上市不足一年的票，下面那些"近 30 根""近 5 年"的格子会自动变薄或判不了。',
    'sectors': '归属板块/概念（本机字典）。概念归属是二值的（在/不在），没有权重，'
               '所以这里只列名单不排名次；`members` 是这个板块在本机字典里有多少只成分股'
               '（行业板常常只有十几只，概念板几百只，这个数决定"它到底窄不窄"）。'
               '同一个名字在通达信与同花顺各有一份时并成一条，`source` 标的是留下那一份的来源。',
    'members': '该板块在本机字典里的成分股只数（不是这只票的排名）。',
    'list_date': '本机主档里这一列全表为空（实测 5,577 只 0 行非空）⇒ 它恒为 null，'
                 '不是"这只票没上市"。要上市日期得走下面「个股研究」那一块的公开源。',
    'groups': '本机自选分组归属（同花顺 ini 同步进来的那份名单，最新一场快照）。'
              '不在任何组就是空表，不是"没同步"。',
    'profile_age_days': '这份个股档案距今几天没刷新。它本来就一年刷几次，老了不等于坏了。',
}

HARD_CAVEATS = (
    {'code': 'local_only', 'text':
     '这一块全是本机库读数，不出网；下面「个股研究」那一块的行情与估值是公开源即时值，'
     '两边可能差一场（本机落库要到收盘链跑完），差在那儿是时点不同，不是有一边算错。'},
    {'code': 'master_columns_empty', 'text':
     '主档里「上市日」与「总股本」两列本机全表为空（实测 5,577 只 0 行非空）⇒ 交的是 null 不是 0，'
     '总市值走的是收盘快照那一列；每股类与 ROE 也不在这一块'
     '（收盘快照那几列本机全是 0，读它就是读假数）。'},
)


def _yi(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x / YI, 3)


def _identity(code: str) -> Optional[Dict[str, Any]]:
    return engine.query_one(
        'select security_code, name, symbol, market, board, asset_type, is_st, list_date, '
        'pinyin_abbr, total_share, float_share '
        'from reference_data.security where security_code = %s', (code,), role='reader')


def _latest_session(code: str) -> Dict[str, Any]:
    r = engine.query_one(
        'select trade_date, close, pre_close, pct_change, amount_yuan, turnover_rate_percent, '
        'volume_shares, limit_up, limit_down from market_data.stock_daily '
        'where security_code = %s order by trade_date desc limit 1', (code,), role='reader')
    if not r:
        return {}
    d = r['trade_date']
    out = {'trade_date': d.isoformat() if hasattr(d, 'isoformat') else str(d)[:10],
           'close': r['close'], 'pre_close': r['pre_close'], 'pct_change': r['pct_change'],
           'amount_yi': _yi(r['amount_yuan']), 'turnover_rate': r['turnover_rate_percent'],
           'limit_up': r['limit_up'], 'limit_down': r['limit_down']}
    snap = engine.query_one(
        'select pe_ttm, pb, total_market_value_yuan, float_market_value_yuan '
        'from market_data.stock_snapshot where security_code = %s and trade_date = %s',
        (code, d), role='reader') or {}
    out.update({'pe_ttm': snap.get('pe_ttm'), 'pb': snap.get('pb'),
                'total_mv_yi': _yi(snap.get('total_market_value_yuan')),
                'float_mv_yi': _yi(snap.get('float_market_value_yuan')),
                'snapshot_present': bool(snap)})
    return out


def _profile(code: str) -> Dict[str, Any]:
    # ⚠️ 这张表是**按日**存的（实测一码多行，2026-10-16：600519 有 3 行、全表 28,515 行）
    # ⇒ 必须 `order by hq_date desc limit 1`，不排序就会拿到任意一天，屏上说的是"最新档案"。
    r = engine.query_one(
        'select hq_date, main_business, staff_num, ipo_price, his_high, his_low, beta, '
        'updated_at from reference_data.stock_profile where security_code = %s '
        'order by hq_date desc limit 1', (code,), role='reader')
    if not r:
        return {'available': False,
                'reason': '这只票在 `reference_data.stock_profile` 里没有行 —— 那份档案由通达信 TQ 采，'
                          '需要客户端在跑；缺它不影响行情与估值那两列。'}
    age = None
    stamp = r.get('updated_at') or r.get('hq_date')
    if stamp is not None:
        try:
            day = stamp.date() if isinstance(stamp, dt.datetime) else stamp
            age = (dt.date.today() - day).days
        except (TypeError, ValueError):
            age = None
    return {'available': True, 'hq_date': str(r['hq_date'])[:10] if r.get('hq_date') else None,
            'main_business': r.get('main_business'), 'staff_num': r.get('staff_num'),
            'ipo_price': r.get('ipo_price'), 'his_high': r.get('his_high'),
            'his_low': r.get('his_low'), 'beta': r.get('beta'), 'profile_age_days': age}


def _sectors(code: str, limit: int = 14) -> List[Dict[str, Any]]:
    """归属板块/概念。

    ⚠️ 成员数必须另起一个**不带这只票条件**的子查询：把它写在同一层窗口里
    （`count(*) over (partition by sector_code)` + `where security_code = 这一只`）
    数出来的恒为 1 —— 10-16 第一版就这样，屏上会把"19 只的小行业"读成"只有它一只"。
    """
    rows = engine.query(
        """with cnt as (select sector_code, count(*) members
                          from reference_data.sector_member group by 1)
           select i.sector_code, i.name, i.sector_type, cnt.members,
                  split_part(i.sector_code, '_', 1) as source
             from reference_data.sector_member m
             join reference_data.sector_info i using (sector_code)
             join cnt using (sector_code)
            where m.security_code = %s
              and coalesce(i.is_active, true)
              and (m.out_date is null or m.out_date > current_date)
            order by case i.sector_type when '行业' then 0 when '概念' then 1 else 2 end,
                     cnt.members asc, i.name
            limit %s""", (code, limit * 3), role='reader')
    if rows is None:
        raise RuntimeError('板块归属读不到（sector_member × sector_info）—— 查询失败不是「这只票没板块」')
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for r in rows or []:
        name = str(r.get('name') or '')
        if not name or name.isdigit():
            # 字典里有整表只叫「880595」这种只有代码的板块，摆到屏幕上就是一串数字，不如不摆
            continue
        key = (name, r['sector_type'])
        if key in seen:
            continue          # 同一个行业名通达信与同花顺各有一份 ⇒ 按名字并一条，来源标在第一处
        seen.add(key)
        out.append(r)
    return out[:limit]


def _groups(code: str) -> List[Dict[str, Any]]:
    rows = engine.query(
        'select group_id, group_name, trade_date from reference_data.watchlist_member_snapshot '
        'where security_code = %s and captured_at = '
        '  (select max(captured_at) from reference_data.watchlist_member_snapshot) '
        'order by group_name', (code,), role='reader')
    if rows is None:
        raise RuntimeError('本机分组读不到（watchlist_member_snapshot）—— 查询失败不是「这票不在任何组」')
    return rows or []


def board(code: str) -> Dict[str, Any]:
    """最上方那张「个股信息」卡的全部读数。不可用时只交 available/reason，**不带行键**。"""
    code = (code or '').strip().upper()
    if not code:
        return {'available': False, 'reason': '没交代码', 'code': code}
    ident = _identity(code)
    if not ident:
        return {'available': False, 'code': code,
                'reason': '本机主档（`reference_data.security`）里没有这个代码 —— '
                          '先确认它带没带市场后缀（600519.SH）。美/港/韩股不走本机字典，'
                          '请直接看下面「个股研究」那一块。'}
    session = _latest_session(code)
    prof = _profile(code)
    caveats: List[str] = list(HARD_CAVEATS)
    if not session:
        caveats.append({'code': 'no_daily', 'text':
            '这只票在 `market_data.stock_daily` 里一根日线都没有 ⇒ 上面那些行情/估值格子是空的。'
            '新股当天不入库、或本机还没采到它，两种都会这样。'})
    if session and not session.get('snapshot_present'):
        caveats.append({'code': 'no_snapshot', 'text':
            '最近这一场的收盘快照没入库 ⇒ PE/PB/市值三格是 null（不是 0）。'
            '快照来自通达信客户端插件，插件没跑就没有新表。'})
    if prof.get('available') is False:
        caveats.append({'code': 'no_profile', 'text': prof['reason']})
    elif (prof.get('profile_age_days') or 0) > 30:
        caveats.append({'code': 'profile_stale', 'text':
            f'这份个股档案已 {prof["profile_age_days"]} 天没刷新（它本来就一年刷几次）'
            '—— 主营/员工数/β 是老读数，行情与估值不是。'})
    st = bool(ident.get('is_st'))
    if st:
        caveats.append({'code': 'is_st', 'text':
            '主档标了 ST：涨跌停档位与主板非 ST 不同，异动判据也按 ST 档走。'})
    # 那份 AI 公司说明也在这一块（用户 2026-10-22：「在个股信息……把 ai 的结论放过来」）。
    # 读的是 `analytics.stock_brief` ⇒ 仍然只读本机库、不出网；没有这一行就是**还没生成过**，
    # 不是"生成出来是空的"，两者在界面上必须是两句话（`brief.exists` 分得开）。
    from . import stock_brief as SB
    brief = SB.read_brief(code)
    if brief:
        brief = {**brief, 'caliber': SB.CALIBER, 'definitions': SB.DEFINITIONS,
                 'word_limit': brief.get('word_limit') or SB.WORD_LIMIT}
    return jsonable({
        'available': True, 'code': code, 'spec': 'facts-1',
        'name': ident.get('name'), 'symbol': ident.get('symbol'), 'market': ident.get('market'),
        'board': ident.get('board'), 'asset_type': ident.get('asset_type'),
        'is_st': st, 'list_date': ident.get('list_date'), 'pinyin_abbr': ident.get('pinyin_abbr'),
        'total_share': ident.get('total_share'), 'float_share': ident.get('float_share'),
        'session': session, 'profile': prof,
        'sectors': _sectors(code), 'groups': _groups(code),
        'brief': brief, 'brief_spec_version': SB.SPEC_VERSION,
        'caliber': CALIBER, 'definitions': DEFINITIONS, 'caveats': caveats,
    })
