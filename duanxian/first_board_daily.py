# -*- coding: utf-8 -*-
"""「首板分析」的读数层（用户 2026-10-21 点名，挂在首页「全市场换手率 TOP20」上面）。

原话：「这个上面加一个首板分析，分析首板个股的情况，已经近期的走势，分析涨停原因，
特别是前一天的走势。」

这一层**只读 `analytics.first_board_daily`**（判据与回填只住 `app/compute/first_board_daily.py`，
母体是 `analytics.first_board_shape` 的 fb-2 那一把尺）。这里不写库、不抓网络、不自算第二套判据。

四条口径红线（每条都在 caveats 里念给界面）：

1. **母体只有 fb-2 一把尺**：连板段第一天（本地日线 `close >= limit_up - 0.01`）。
   首板页那张 `GET /api/market/first-board` 读的是**东财实时池**（`lbc==1`），两份名单不是一件事，
   谁也不许冒充谁 —— 所以这里把 `universe`（本地当日收盘封住家数）随行交出。
2. **涨停原因是源站的编辑归因，不是公司确认的事实**；ST 与北交所基本不在池里
   （实测 40 场 1,911 行：ST 首板 82 只只有 1 只有串、北交所 23 只 0 只有），
   所以「串空着」几乎总是**源站不收**，不是本机漏采。空就是 null（界面 —），不写「无题材」。
3. **前一日那一根只有日线读数**（涨幅/换手/额/量比/是否触板/形态标签）。本地分钟线只到
   2026-10-19（2026-10-20、10-21 整场没有），而 `first_seal_at`/`last_seal_at`/`break_count`
   是**首板当日**的源站时间戳，不是前一日的 —— 界面与悬停都不许把它念成"前一天的分时"。
   （2026-10-27 用户点名把这一整组列从屏上撤掉、与短线情绪那块合并去重；接口仍原样交出这些键，
   「前一日形态档」那个筛子也照旧在库里生效。）
4. **缺数交 null、并在 `missing` 里记为什么缺**，绝不折算成 0：
   `break_count` 空是"源站这一行没给这个字段"（实测 2026-10-21 池内 50 只里 27 只没给），
   不是"一次都没炸"；`prev_*` 空是"没有前一交易日行/没有涨停档"，不是"平盘"。
5. **10-27 新加的那四列各有各的源表，都不由本表算**（`_enrich` 一族一次集合查，
   读不到只记它自己那句，其余照旧出数）：近 20 场几板走 `duanxian/board_span.py` 的封住判据、
   近 5 场象限名次走 `analytics.score` 的 `oper_score`（八个分量里只有它带象限）、
   近 10/20 场倍量走 `analytics.volume_ratio_daily` 的 vr-1（分母是**对昨日量**，
   与同屏「倍量加权分」那把 ≥2×前 5 场均量不是一把尺）、盘中三列走 `analytics.intraday_spike`
   的 spk-1（那是一张**筛过的异动榜**：没进榜 ≠ 盘中没冲高）。
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from app.compute import first_board_daily as FBD
from app.db import engine

from . import quant as Q
from . import ranking as RANKING
from . import board_span as BS
from . import reason_tags

TABLE = 'analytics.first_board_daily'
SPEC_VERSION = FBD.SPEC_VERSION          # 版本号只住写侧一份，读侧跟着它走
DEFAULT_LIMIT = 80                        # 一场首板实测 30~60 只（近 40 场），80 够装且不会静默切
MAX_LIMIT = 240

# ---------------------------------------------------------------- 近期与盘中的窗口与档位（10-27 用户点名）
#: 「近 N 场的倍量分布」的两个窗口。分母是**对昨日量**（`analytics.volume_ratio_daily`，vr-1，
#: 加工段每日算）——与同一屏「倍量加权分」那把「≥2×前 5 场均量」（`analytics.volume_double`，
#: 40 场窗口）不是一件事，屏上必须点名分母，否则两列会长成同一个名字。
DBL_WINDOWS = (10, 20)
DBL_MULT = 2.0
DBL_NEAR = 1.6
#: 四维象限与名次摆最近几场（用户点名 5 场）。八个 capability 里实测只有 `oper_score` 带 quadrant。
QUAD_SESSIONS = 5
QUAD_CAPABILITY = 'oper_score'
#: 盘中急拉档：标签与阈值只住写侧 `app/compute/intraday_spike.py`（TAG_LABELS / FAST_PCT / SPEED_MIN_PCT），
#: 这里只排"哪一档更劲"的序，给排序键用。
SPIKE_TIER_RANK = {'fast': 3, 'quick': 2, 'slow': 1}

#: 列注释的唯一出处仍是库里的 comment on，界面读它（见 `duanxian/quant.py::_comments`）；
#: 这里只补**聚合口径**与"为什么空"那几句，别处不许再写第二份。
RULE = (f'母体＝fb-2 判据下『连板段第一天』那批票（本地日线 close >= limit_up − 0.01，'
        f'与首板页那张东财实时池不是一张名单）；本屏读 `analytics.first_board_daily` '
        f'的 spec_version={SPEC_VERSION}。默认按首封时间由早到晚排（早封在前，与首页连板榜同一条规矩），'
        f'没有首封时间戳的行排在最后 —— 那是「源站没给」，不是「封得晚」。')

#: 形态档的两个字短标签与那句"为什么这么画"，**只住写侧**（`SHAPE_NAMES` / `SHAPE_LABELS`），
#: 这里原样端出去 —— 界面不许给某一档另起名字，两屏会分叉。
SHAPE_KEYS = list(FBD.SHAPE_PRIORITY)

DEFINITIONS = {
    'launch': '当日那一根：换手/是否一字/封单额（涨幅那一列 2026-10-27 起不在这屏上摆，接口仍交出）。'
              '「一字」的判据是「当日最高价=最低价」（全天没有一根别的成交价），'
              '与「首封时间是不是 09:30」是两件事 —— 开盘秒板但盘中打开过的票，首封时间也写 09:30:00，它不是一字。',
    'reason': '涨停原因＝源站编辑归因（同花顺涨停揭秘），不是公司确认的事实；'
              'ST 与北交所基本不在池里，串空着通常是「源站不收」而不是「本机没采到」。',
    'seal_time': '首封/最后封板时间是「首板当日」的源站时间戳（逐行核对过落在本场）。'
                 '本地分钟线只到 2026-10-19，所以前一日的分时读数这里没有，也不拿它当日线冒充。',
    'break_count': '炸板次数：源站这一行没给这个字段就是 null（实测 2026-10-21 池内 50 只里 27 只没给），'
                   '不折算成「0 次＝一次没炸」。',
    'prev_day': '前一交易日那一根（日线口径）：涨幅、换手、成交额、量比、是否盘中触板/收盘封住、形态标签。'
                '量比＝前一交易日成交量 ÷ 它之前 5 场均量（不含它自己），分母不足 3 场就算不出。'
                '【这一组列 2026-10-27 起不在这屏上摆了（与短线情绪那块重复），接口仍原样交出】',
    'prev_shape': '前一日形态标签（互斥、按优先级，阈值只住 app/compute/first_board_daily.PREV_SHAPE_THRESHOLDS）：'
                  '触板炸开 → 大跌 → 冲高未封 → 缩量横盘 → 缩量回调 → 温和放量 → 其余。'
                  '首板定义下「前一日收盘封住」恒为 0（全表实测 0 行），那是一条自检，不是筛子。',
    'trend': '近期走势：chg5/chg10/chg15 是后复权区间涨幅（与全站同一把尺，`duanxian/day_returns.py` 一份）；'
             'vol_grab_ratio 是启动前最后 5 根均量 ÷ 前 25 根均量（>1＝前夜已经在抢跑，它是量能比不是涨幅）；'
             'dbl_days/calm_days 是母体那张表的窗口计数原值。',
    'energy': '倍量加权分 dbl_energy 与它的全场名次 dbl_rank：与倍量榜同一个表达式、同一批（energy_fp），'
              '分母是当日该批有分的只数，不是全部股票。',
    'boards': '（这一列 2026-10-27 起不在这屏上摆了，字段本身仍在表里算）几天几板由本地 board_span 算'
              '（封的容差 0.001），源站那句「几天几板」只作对照：'
              '两边不一致的行会如实给 source_boards_agree=false（近 40 场实测 93 行），不偷偷取其中一个。',
    'universe': 'universe＝当日本地判据下收盘封住的涨停家数（这块名单的分母）；'
                '首板只数 = 本屏母体行数，两者一起看才知道今天是大面还是窄面。',
    'boards_20d': '「近 20 场几板」＝最近 20 个交易日里有几天收盘封住涨停（次数，不要求连着）。'
                  '它与原来那列「几天几板」问的不是同一件事：那一列只看「当前这一轮」'
                  '（连续两天没封住就不再往回数），所以会出现「1天1板」而近 20 场已经 4 板。'
                  '封住的判据与容差只住 `duanxian/board_span.py` 一份（收盘 ≥ 当日涨停价 − 0.001）。'
                  '后面那个分母是这只票在窗口里真有几根日线：新股只有 6 根时，「5 次」要读成「6 根里 5 次」，'
                  '不是「20 场里 5 次」。',
    'quad': '「近 5 场四维象限」读 `analytics.score` 的 `oper_score` 那一行 —— 八个分量里只有它带象限'
            '（Q1 强势／Q2／Q3／Q4，算法与四维象限那屏同一份），名次是它在全市场的排位。'
            '五段从左到右是由早到晚，最后一场就是本场。标了〈旧基准〉的那一段是旧系统迁进来的行，'
            '象限可用、名次不参与对比。这一场没有 score 行就整段是 —（没算到），不是「排名第 0」。',
    'dbl_windows': '「近 10 场 / 近 20 场倍量」的分母是「对昨日量」的倍数'
                   f'（`analytics.volume_ratio_daily`，判据版 {0}，加工段每日算）：'
                   '倍数 ≥ {1} 记一次倍量，{2}~{1} 记一次准倍量。'
                   '⚠️ 它和同一屏那列「倍量加权分 / 名次」不是同一把尺 —— 那一把是'
                   '「量 ≥ 2×前 5 场均量」（`analytics.volume_double`，40 场窗口）；'
                   '两列都叫「倍量」是分母不同的历史遗留，屏上各带自己的分母。'
                   '括号里那个数是窗口内真有的根数（新股与停牌会不足）。'.format('vr-1', DBL_MULT, DBL_NEAR),
    'spike': '盘中三列读 `analytics.intraday_spike`（判据版 spk-1，本地 1 分钟线自算）：'
             '急拉档＝15 分钟滚动窗口内最大拉升的分档（急拉 ≥7 个点／快拉 4~7／慢拉不足 4），'
             '+5% 首现＝当天第一次站上昨收 +5% 的那一分钟，15 分钟最大拉升是那个滚动窗口的峰值。'
             '⚠️ 这张表是「筛过的异动榜」（入表要先过最高涨幅或 15 分钟拉升的阈值）：'
             '没进榜只说明「没过入表线」，不等于「盘中没冲高」；而分钟线只有 2026-08-14 起、'
             '10-20 与 10-21 两天整场没入库 —— 那几天的 — 是「没有分时读数」。',
    'hist_limits': '「近 3 个月涨/跌停」两个口径都给：斜杠前＝收盘贴在板上（封住），斜杠后＝盘中摸到过'
                   '那个价（含后来炸开的）。窗口按「市场场次」数（63 场≈3 个月），不是 90 个自然日 —— '
                   '拿自然日会把长假整段算进去。封住的容差与母体同一条（±0.01 元），只住写侧。'
                   '0 是真读数（这三个月一次都没被按死过），缺档位价那几天会让整列变 —，不报一个偏小的下界。',
    'hist_consec': '「近 6 个月连板最高」读 `analytics.limit_up_pool_daily.consec`（lup-1）里这只票封住行的'
                   '最大值 —— 连板数只住那一处实现（停牌那天算不算断、ST 档位取几分，两处各判一次必分叉）。'
                   '它与「几天几板」不是一把尺：那一列只看当前这一轮，这一列看半年里最远走到第几板。',
    'hist_pos': '「2 年位置%」=（现价 − 两年最低）÷（两年最高 − 两年最低）×100，全部后复权'
                '（价 × 复权因子，缺因子按不复权）：用未复权价算两年最高，'
                '一次 10 送 10 就会造出一个假顶。0 是正趴在两年最低点、100 是正顶在两年最高点；'
                '最高取盘中 high、最低取盘中 low（打板要问的是离历史顶还有多远）。'
                '⚠️ 它不是涨幅、也不是分位数（分位要数有多少天比它低），括号里那个数是窗口里真有的日线根数，'
                f'不足 {FBD.POS_MIN_BARS} 根就判不了（次新股没有两年可说）。',
    'hist_ma': '「均线排列」用后复权收盘的 MA5/MA10/MA20/MA60 四条线分五档（依次在上＝四线多头；'
               '短期金叉但季线压在上方＝反弹压季线；短期回落但季线在下方托着＝回调托季线；'
               '依次在下＝四线空头；剩下＝纠缠）。判档顺序就是判据，反了会把「季线压顶的反弹」'
               '报成多头排列。四条线在本模块 SQL 自算（与 `config.indicator` 的均线族同底，'
               '但不依赖指标步当天有没有跑到基线）。不足 60 根 ⇒ 判不了，不拿三档冒充五档。'
               '悬停那格给四条线原值与现价。',
    'not_signal': '这是形态与归因的读数：不打分、不预测、不给买卖建议；'
                  '首板本身是「已经涨停」的事实陈述，不是「明天会怎样」的判断。',
}

#: 界面直接读这份短标签（长解释在 DEFINITIONS 里）
SHAPE_SHORT = {k: FBD.SHAPE_NAMES[k] for k in SHAPE_KEYS}
SHAPE_UNJUDGED = '判不了'


def _num(v: Any, nd: int | None = None) -> Any:
    """库里的值统一成 JSON 安全形式；NaN/NaT 一律变 None（不许带着 NaT 进 json）。"""
    import pandas as pd
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v
    try:
        if pd.isna(v):
            return None
    except Exception:                                    # noqa: BLE001  数组类 isna 会炸，原样交
        pass
    if isinstance(v, (int,)):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f or f in (float('inf'), float('-inf')):
        return None
    return round(f, nd) if nd is not None else f


def _dash(date: str | None) -> str | None:
    if not date:
        return None
    s = str(date)[:10].strip()
    return s or None


def _latest() -> str | None:
    try:
        v = engine.scalar(f'select max(trade_date) from {TABLE} where spec_version = %s',
                          (SPEC_VERSION,), role='reader')
    except Exception:                                    # noqa: BLE001
        return None
    return str(v)[:10] if v is not None else None


def _on_day(d: str) -> bool:
    """这一场在库里有没有行（有行才算"这一场跑了"，跑了但 0 只是另一件事）。"""
    try:
        n = engine.scalar(f'select count(*) from {TABLE} where spec_version = %s and trade_date = %s',
                          (SPEC_VERSION, d), role='reader')
    except Exception:                                    # noqa: BLE001  探不到就说探不到
        return True
    return (n or 0) > 0


def _fail(what: str, d: str | None) -> dict:
    return {'available': False, 'what': what, 'date': d,
            'reason': (f'本地库读不到「{what}」{("（" + d + "）") if d else ""} —— '
                       f'这一族行还没算过，或库不通。加工链那一步是 '
                       f'`python -m app.compute.first_board_daily --date <日期>`'
                       f'（或等当晚 `fbdaily` 步），别把它读成「今天没有首板」。')}


def _safe_query(sql: str, params: tuple) -> list[dict] | None:
    """**读不到交 None，不交空表**。

    空表与"查询坏了"在界面上会长成同一句"今天没有"，而那正是把取数故障说成结论
    （同《风险提示》那条红线）。调用方拿到 None 要么走 `available:false`，要么如实写"没读到"。
    """
    try:
        return engine.query(sql, params, role='reader')
    except Exception:                                    # noqa: BLE001  原样出声，不兜成空
        return None


# ---------------------------------------------------------------- 近期与盘中（10-27 用户点名新加的四类读数）
# 原来那 8 个「前一交易日那一根」的列被整组换掉：同一批数在屏上与短线情绪那块重复，
# 而用户要的是"这只票最近什么样"——几板、象限名次、倍量分布、盘中信号。
# 窗口与档位那份常量在文件头（DEFINITIONS 组句子要用，一处定义两处用）。


def _full_map(codes) -> dict:
    """6 位码 → 库内全码的对照（`{全码: 6 位}`）。读不到交空 dict，调用方按"没读到"处理。

    为什么先展开再等值查：同一个查询写成 `left(security_code,6)=any(...)` 在 `analytics.score`
    这种百万行表上要慢一个量级（《短线情绪》那屏实测 2.63 秒对 0.25 秒）。
    """
    pool = sorted({str(c)[:6] for c in (codes or []) if c})
    if not pool:
        return {}
    rows = _safe_query("select security_code, left(security_code, 6) as c6 from reference_data.security"
                       " where left(security_code, 6) = any(%s::text[])", (pool,))
    if rows is None:
        return {}
    return {str(r['security_code']): str(r['c6'])[:6] for r in rows}


def _enrich(out: list[dict], d: str, warns: list[str]) -> dict:
    """给这一屏的行就地补四类读数，交回这四项的元数据（窗口/场次/标签）。

    四类各一次集合查询，**任何一类读不到只记它自己那句**：该类格子留 None（界面 —），
    其余照旧出数。一类的取数故障不许把整屏拖成空，也不许悄悄当成"这批票都没有"。
    """
    meta: dict = {'boards_window': BS.COUNT_WINDOW_SESSIONS,
                  'quad_sessions': QUAD_SESSIONS,
                  'dbl_windows': list(DBL_WINDOWS),
                  'dbl_mult': DBL_MULT, 'dbl_near': DBL_NEAR}
    codes = [str(r['code'])[:6] for r in out]      # 行上交的是全码（301314.SZ），四类读数都按 6 位码建键
    for r in out:
        r.update({'boards_20d': None, 'boards_20d_bars': None, 'boards_20d_dates': [],
                  'quad': [], 'quad_stale': False,
                  'dbl10': None, 'dbl20': None, 'dbl_unit': None, 'dbl_last_day': None,
                  'spike_tier': None, 'spike_tier_rank': None,
                  'spike_first5_at': None, 'spike_speed15_pct': None,
                  'spike_above5_min': None, 'spike_tags': [], 'spike_checked': None})
    if not codes:
        return meta
    full = _full_map(codes)
    if not full:
        warns.append('近 20 场几板 / 近 5 场四维象限 / 近 10 与 20 场倍量 / 盘中信号'
                     '（代码对照表没读到，这四类格子都是 —）')
        return meta
    anycodes = list(full)

    # ① 近 20 交易日有过几板（封住判据与容差只住 board_span 那一份）
    spans = BS.sealed_days_by_code(codes, d)
    if spans:
        for r in out:
            got = spans.get(str(r['code'])[:6]) or {}
            r['boards_20d'] = got.get('sealed_days')
            r['boards_20d_bars'] = got.get('bars')
            r['boards_20d_dates'] = got.get('dates') or []
    else:
        warns.append('近 20 场的封板次数没读到（这一列是 —，不是「20 场里一次都没封」）')

    # ② 近 5 场四维象限与全市场名次
    quad = _safe_query("""
        with days as (select trade_date from (select distinct trade_date
                        from analytics.score
                       where capability_key = %(cap)s and trade_date <= %(d)s::date
                       order by trade_date desc limit %(n)s) x),
             want as (select security_code, left(security_code, 6) as c6 from reference_data.security
                       where security_code = any(%(codes)s::text[]))
        select w.c6 as code, s.trade_date::text as day, s.quadrant,
               s.rank_in_market, s.value, s.is_stale
          from analytics.score s
          join want w using (security_code)
          join days g on g.trade_date = s.trade_date
         where s.capability_key = %(cap)s
         order by w.c6, s.trade_date""",
        {'cap': QUAD_CAPABILITY, 'd': d, 'n': QUAD_SESSIONS, 'codes': anycodes})
    if quad is None:
        warns.append('近 5 场的四维象限与名次没读到（`analytics.score` 那一族）')
    else:
        by: dict = {}
        for r in quad:
            by.setdefault(str(r['code'])[:6], []).append(r)
        for r in out:
            seq = by.get(str(r['code'])[:6]) or []
            r['quad'] = [{'day': str(x['day'])[:10], 'quadrant': x.get('quadrant'),
                          'rank': _num(x.get('rank_in_market')),
                          'value': _num(x.get('value'), 3),
                          'stale': bool(x.get('is_stale'))} for x in seq]
            r['quad_stale'] = any(x['stale'] for x in r['quad'])
            if not seq:
                r['quad'] = []

    # ③ 近 10 / 近 20 场的倍量分布（对昨日量，vr-1）
    from app.compute import volume_ratio as VR
    meta['dbl_spec'] = VR.SPEC_VERSION
    # ⚠️ 那个 `order by trade_date desc` 不能省：写成 `distinct ... limit 20` 时 Postgres 按索引顺序
    # 给的是**最早**那 20 场（实测 2021-08-02~2021-08-27），窗口就成了"2021 年那 20 场"，
    # 而屏上那一列的名字是"近 20 场"。当时 7 只 2022 年后上市的票整格是 —，才暴露出来。
    dbl = _safe_query("""
        with days as (select trade_date, row_number() over (order by trade_date desc) as rn
                        from (select distinct trade_date from analytics.volume_ratio_daily
                               where trade_date <= %(d)s::date
                               order by trade_date desc limit %(hi)s) x),
             want as (select security_code, left(security_code, 6) as c6 from reference_data.security
                       where security_code = any(%(codes)s::text[]))
        select w.c6 as code,
               count(*) filter (where g.rn <= %(lo)s)::int                as bars_lo,
               count(*) filter (where g.rn <= %(lo)s
                                and v.vol_ratio >= %(mult)s)::int         as dbl_lo,
               count(*) filter (where g.rn <= %(lo)s
                                and v.vol_ratio >= %(near)s
                                and v.vol_ratio <  %(mult)s)::int         as near_lo,
               count(*)::int                                             as bars_hi,
               count(*) filter (where v.vol_ratio >= %(mult)s)::int      as dbl_hi,
               count(*) filter (where v.vol_ratio >= %(near)s
                                and v.vol_ratio <  %(mult)s)::int        as near_hi,
               max(v.trade_date)::text                                   as last_day
          from analytics.volume_ratio_daily v
          join days g using (trade_date)
          join want w using (security_code)
         where v.spec_version = %(spec)s
         group by w.c6""",
        {'d': d, 'lo': DBL_WINDOWS[0], 'hi': DBL_WINDOWS[1], 'mult': DBL_MULT,
         'near': DBL_NEAR, 'codes': anycodes, 'spec': VR.SPEC_VERSION})
    if dbl is None:
        warns.append('近 10 与近 20 场的倍量分布没读到（`analytics.volume_ratio_daily` 那一族）')
    else:
        dmap = {str(r['code'])[:6]: r for r in dbl}
        for r in out:
            x = dmap.get(str(r['code'])[:6])
            if not x:
                continue
            # 平铺成交给界面（sortKey 必须等于格子里读的那个字段，嵌一层的 `dbl.10` 排不了序）
            r['dbl10'] = {'bars': _num(x.get('bars_lo')), 'dbl': _num(x.get('dbl_lo')),
                          'near': _num(x.get('near_lo'))}
            r['dbl20'] = {'bars': _num(x.get('bars_hi')), 'dbl': _num(x.get('dbl_hi')),
                          'near': _num(x.get('near_hi'))}
            r['dbl_unit'] = '对昨日量倍数'
            # 窗口截至哪一天要交出去：那把尺取错了年代时（少了 order by desc 就取到 2021 年头 20 场），
            # 屏上的数照样"看着正常"，只有这个日期能当场露馅
            ld = x.get('last_day')
            r['dbl_last_day'] = str(ld)[:10] if ld is not None else None

    # ④ 盘中信号（当日那一场异动筛查）
    from app.compute import intraday_spike as SPK
    meta['spike_spec'] = SPK.SPEC_VERSION
    meta['spike_tier_labels'] = {k: SPK.TAG_LABELS.get(k, k) for k in SPIKE_TIER_RANK}
    meta['spike_thresholds'] = {'fast_pct': SPK.FAST_PCT, 'speed_min_pct': SPK.SPEED_MIN_PCT,
                                'speed_window': SPK.SPEED_WINDOW}
    spike = _safe_query("""
        select left(s.security_code, 6) as code, s.spike_tags, s.first5_time, s.speed15_pct,
               s.above5_min, s.touched, s.sealed, s.minute_available
          from analytics.intraday_spike s
         where s.trade_date = %s::date and s.spec_version = %s
           and left(s.security_code, 6) = any(%s::text[])""",
        (d, SPK.SPEC_VERSION, codes))
    if spike is None:
        # 探不到 ≠ 没进榜：这一类三列全交 None，界面念"没读到"，不念成"本场没冲高"
        warns.append('盘中信号没读到（`analytics.intraday_spike` 那一族）—— '
                     '这三列的是 —，不等于这批票盘中都没冲高')
    else:
        smap = {}
        for r in spike:
            smap.setdefault(str(r['code'])[:6], r)
        for r in out:
            x = smap.get(str(r['code'])[:6])
            r['spike_checked'] = True          # 探过了：没行就是真没进榜
            if not x:
                continue
            tags = [t for t in (x.get('spike_tags') or []) if t]
            tier = next((t for t in ('fast', 'quick', 'slow') if t in tags), None)
            r['spike_tier'] = SPK.TAG_LABELS.get(tier) if tier else None
            r['spike_tier_rank'] = SPIKE_TIER_RANK.get(tier) if tier else None
            r['spike_first5_at'] = str(x['first5_time'])[:5] if x.get('first5_time') is not None else None
            r['spike_speed15_pct'] = _num(x.get('speed15_pct'), 2)
            r['spike_above5_min'] = _num(x.get('above5_min'))
            r['spike_tags'] = [SPK.TAG_LABELS.get(t, t) for t in tags]
    return meta


def panel(date: str = '', limit: int = DEFAULT_LIMIT, shapes: str = '') -> dict:
    """首页「首板分析」一屏的全部读数。

    · `shapes` 是**在库里筛**（拼在 order/limit 之前），逗号分隔的形态档键；非法值退回不筛并出声；
    · 全场聚合（`shape_tally`/`reason_tally`/`universe`/`first_count`）永远按**这一场全部首板**算，
      不受 `shapes` 与 `limit` 影响 —— 每档后面那个数必须是全场数，否则筛一次就变成"全场只剩这些"；
    · 表没跑过 / 库不通 ⇒ `{available:false, reason}` 且**不带 `rows` 键**。
    """
    d = _dash(date) or _latest() or ''
    if not d:
        return _fail('首板分析', None)
    if not _on_day(d):
        return _fail('首板分析', d)

    want = [s.strip() for s in str(shapes or '').split(',') if s.strip()]
    illegal = [s for s in want if s not in SHAPE_KEYS]
    use = [s for s in want if s in SHAPE_KEYS]

    n = int(limit or DEFAULT_LIMIT)
    n = max(10, min(n, MAX_LIMIT))

    where = ['f.spec_version = %s', 'f.trade_date = %s']
    params: list[Any] = [SPEC_VERSION, d]
    if use:
        where.append('f.prev_shape = any(%s)')
        params.append(use)
    filt = ' and '.join(where)

    rows = _safe_query(f"""
        select f.security_code,
               coalesce(s.name, f.security_code) as name,
               s.board, s.market,
               f.launch_pct, f.launch_close, f.launch_turnover, f.launch_amount_yuan,
               f.limit_up_price, f.is_one_word, f.float_cap_yuan,
               f.reason, f.first_seal_at, f.last_seal_at, f.break_count,
               f.order_amount, f.order_volume, f.limit_up_kind,
               f.source_high_days, f.reason_source, f.reason_captured_at,
               f.boards, f.board_days, f.board_label, f.source_boards_agree,
               f.prev_date, f.prev_pct, f.prev_turnover, f.prev_amount_yuan,
               f.prev_vol_ratio, f.prev_touched, f.prev_sealed,
               f.prev_shape, f.prev_shape_note,
               f.chg5, f.chg10, f.chg15, f.vol_grab_ratio,
               f.dbl_days, f.calm_days, f.dbl_energy, f.energy_fp,
               -- 这只票自己的历史位置四列（10-27 用户点名；判据只住写侧，这里只搬与加中文档位）
               f.lu_sealed_3m, f.lu_touched_3m, f.ld_sealed_3m, f.ld_touched_3m,
               f.max_consec_6m, f.close_adj, f.hi_2y, f.lo_2y, f.pos_2y, f.bars_2y,
               f.ma5, f.ma10, f.ma20, f.ma60, f.ma_stack,
               f.universe, f.missing, f.calculated_at,
               -- 倍量加权分的全场名次：表达式只住 `quant._energy_rank_expr` 一处（与倍量榜、
               -- 紧随榜同一个 competition ranking、同一批 energy_fp），这里不另写第二遍。
               {Q._energy_rank_expr('f')} as dbl_rank,
               (select count(distinct y.security_code)::int
                  from analytics.volume_double y
                 where y.trade_date = f.trade_date and y.energy_fp = f.energy_fp
                   and y.dbl_energy is not null) as energy_universe
          from {TABLE} f
          left join reference_data.security s using (security_code)
         where {filt}
         order by f.first_seal_at asc nulls last, f.security_code
         limit %s""", tuple(params + [n]))
    if rows is None:
        return _fail('首板分析', d)

    out = [{
        'code': str(r['security_code']),
        'name': r.get('name'),
        'board': r.get('board'),
        'market': r.get('market'),
        'launch_pct': _num(r.get('launch_pct'), 4),
        'launch_close': _num(r.get('launch_close'), 4),
        'launch_turnover': _num(r.get('launch_turnover'), 4),
        'launch_amount_yi': (round(_num(r.get('launch_amount_yuan')) / 1e8, 3)
                             if r.get('launch_amount_yuan') is not None else None),
        'limit_up_price': _num(r.get('limit_up_price'), 4),
        'is_one_word': r.get('is_one_word'),
        'float_cap_yi': (round(_num(r.get('float_cap_yuan')) / 1e8, 2)
                         if r.get('float_cap_yuan') is not None else None),
        'reason': r.get('reason'),
        'reason_missing': (r.get('missing') or {}).get('reason') if isinstance(r.get('missing'), dict) else None,
        'first_seal_at': str(r['first_seal_at'])[:8] if r.get('first_seal_at') is not None else None,
        'last_seal_at': str(r['last_seal_at'])[:8] if r.get('last_seal_at') is not None else None,
        'break_count': _num(r.get('break_count')),
        'order_amount_wan': (round(_num(r.get('order_amount')) / 1e4, 1)
                             if r.get('order_amount') is not None else None),
        'limit_up_kind': r.get('limit_up_kind'),
        'source_high_days': r.get('source_high_days'),
        'boards': _num(r.get('boards')),
        'board_days': _num(r.get('board_days')),
        'board_label': r.get('board_label'),
        'source_boards_agree': r.get('source_boards_agree'),
        'prev_date': str(r['prev_date'])[:10] if r.get('prev_date') is not None else None,
        'prev_pct': _num(r.get('prev_pct'), 4),
        'prev_turnover': _num(r.get('prev_turnover'), 4),
        'prev_amount_yi': (round(_num(r.get('prev_amount_yuan')) / 1e8, 3)
                           if r.get('prev_amount_yuan') is not None else None),
        'prev_vol_ratio': _num(r.get('prev_vol_ratio'), 4),
        'prev_touched': r.get('prev_touched'),
        'prev_sealed': r.get('prev_sealed'),
        'prev_shape': r.get('prev_shape'),
        'prev_shape_label': SHAPE_SHORT.get(r.get('prev_shape') or ''),
        'prev_shape_note': r.get('prev_shape_note'),
        'chg5': _num(r.get('chg5'), 4),
        'chg10': _num(r.get('chg10'), 4),
        'chg15': _num(r.get('chg15'), 4),
        'vol_grab_ratio': _num(r.get('vol_grab_ratio'), 4),
        'dbl_days': _num(r.get('dbl_days')),
        'calm_days': _num(r.get('calm_days')),
        'dbl_energy': _num(r.get('dbl_energy'), 4),
        'dbl_rank': _num(r.get('dbl_rank')),
        'energy_universe': _num(r.get('energy_universe')),
        # ── 这只票自己的历史位置（10-27 四列）。中文档位由写侧那两份字典加，界面不自己翻第二份 ──
        'lu_sealed_3m': _num(r.get('lu_sealed_3m')),
        'lu_touched_3m': _num(r.get('lu_touched_3m')),
        'ld_sealed_3m': _num(r.get('ld_sealed_3m')),
        'ld_touched_3m': _num(r.get('ld_touched_3m')),
        'max_consec_6m': _num(r.get('max_consec_6m')),
        'close_adj': _num(r.get('close_adj'), 4),
        'hi_2y': _num(r.get('hi_2y'), 4),
        'lo_2y': _num(r.get('lo_2y'), 4),
        'pos_2y': _num(r.get('pos_2y'), 2),
        'bars_2y': _num(r.get('bars_2y')),
        'ma5': _num(r.get('ma5'), 4),
        'ma10': _num(r.get('ma10'), 4),
        'ma20': _num(r.get('ma20'), 4),
        'ma60': _num(r.get('ma60'), 4),
        'ma_stack': r.get('ma_stack'),
        'ma_stack_label': FBD.MA_STACK_NAMES.get(r.get('ma_stack') or ''),
        'ma_stack_note': FBD.MA_STACK_LABELS.get(r.get('ma_stack') or ''),
        # 排序键：格子上念的是中文档名，能比大小的是"从最多头到最空头"这一串次序（写侧一份）。
        # 与盘中「急拉档」那一列同一条纪律 —— 显示值与排序键不是一个字段，就必须逐列点击实测。
        'ma_stack_rank': (len(FBD.MA_STACK_PRIORITY) - FBD.MA_STACK_PRIORITY.index(r['ma_stack'])
                          if r.get('ma_stack') in FBD.MA_STACK_PRIORITY else None),
        'missing': r.get('missing') if isinstance(r.get('missing'), dict) else {},
    } for r in rows]

    # ---- 近期与盘中四类读数（10-27 用户点名，替掉屏上那 8 个「前一交易日」列）----
    # ---- 全场聚合（不受 shapes / limit 影响）----
    agg_failed: list[str] = []
    recent = _enrich(out, d, agg_failed)
    agg = _safe_query(f"""
        select count(*)::int first_count,
               max(universe)::int universe,
               count(*) filter (where reason is not null)::int has_reason,
               count(*) filter (where reason is null
                                and missing->>'reason' = 'source_row_absent')::int reason_row_absent,
               count(*) filter (where reason is null
                                and missing->>'reason' = 'source_no_records')::int reason_no_records,
               count(*) filter (where break_count is null)::int break_null,
               count(*) filter (where prev_shape is null)::int shape_null,
               count(*) filter (where source_boards_agree is false)::int boards_disagree,
               count(*) filter (where boards is null)::int boards_null,
               count(*) filter (where is_one_word)::int one_word,
               count(*) filter (where prev_touched)::int prev_touched,
               round(percentile_cont(0.5) within group (order by prev_pct)::numeric, 2)::float prev_pct_med
          from {TABLE} f where spec_version = %s and trade_date = %s""", (SPEC_VERSION, d))
    if agg is None:
        agg_failed.append('本场聚合（首板只数/封住家数/原因覆盖/形态计数）没读到')
        agg = []
    a = agg[0] if agg else {}

    shapes_all = _safe_query(f"""
        select prev_shape k, count(*)::int n from {TABLE} f
         where spec_version = %s and trade_date = %s
         group by prev_shape order by n desc""", (SPEC_VERSION, d))
    if shapes_all is None:
        agg_failed.append('各形态档的只数没读到')
        shapes_all = []
    shape_tally = [{'key': r['k'], 'label': SHAPE_SHORT.get(r['k'] or '') or SHAPE_UNJUDGED,
                    'n': _num(r['n'])} for r in shapes_all]

    # 题材词频按**这一场全部首板**算（不随 shapes 与 limit 变）——算法与首板页同一份共用块
    # `duanxian/reason_tags.py`；随筛参变的会是"这一小堆里哪个词多"，那不是这一格要回答的问题。
    # 本屏**不交行业汇总**：`reference_data.security` 里没有行业列（实测列清单里没有），
    # 行业只在首板页那份东财实时池里有 —— 交个空列表会被读成"今天没有重复行业"，那是假数。
    pool = _safe_query(f"""
        select f.reason
          from {TABLE} f
         where f.spec_version = %s and f.trade_date = %s""", (SPEC_VERSION, d))
    if pool is None:
        agg_failed.append('题材词频')
        pool = []
    tags = reason_tags.tally(
        [{'reason': r.get('reason')} for r in pool],
        scope=f'本场全部 {len(pool)} 只首板的涨停原因里哪个词重复出现'
              f'（不随形态筛与取数上限变；原因＝源站编辑归因，不是公司确认事实）')
    tags['industry_summary'] = None
    tags['summary_meta']['industry_note'] = (
        '本屏不交行业汇总：库里 `reference_data.security` 没有行业列，'
        '行业只在首板页那份东财实时池里有 —— 空列表不等于「今天没有重复行业」。')

    caveats = [{'code': 'one_ruler',
                'text': f'首板母体只有 fb-2 这一把尺（本地日线收盘封住且是段第一天）：'
                        f'本场首板 {a.get("first_count", "—")} 只、当日收盘封住 {a.get("universe", "—")} 只。'
                        f'首板页那块读的是东财实时池（连板数=1），两份名单不是一件事，'
                        f'差异来自封板容差与实时/收盘时点，别把它们读成同一个数。'},
               {'code': 'reason_attribution',
                'text': '涨停原因是源站编辑归因、不是公司确认的事实；且源站池不收北交所、'
                        f'几乎不收 ST（近 40 场实测：ST 首板 82 只仅 1 只有串、北交所 23 只 0 只有）'
                        f'—— 本场有串 {a.get("has_reason", "—")} 只、'
                        f'源站没收这只 {a.get("reason_row_absent", "—")} 只、'
                        f'那场整池没取到 {a.get("reason_no_records", "—")} 只（后者才是本机漏采）。'},
               {'code': 'recent_sources',
                'text': '「近 20 场几板 / 近 5 场四维象限 / 近 10 与 20 场倍量」读的是本地库里另外三张表'
                        '（stock_daily 的封住判据、analytics.score 的 oper_score、'
                        'analytics.volume_ratio_daily 的 vr-1），不是本表算的；'
                        '「盘中」那三列读 analytics.intraday_spike（spk-1），'
                        '分钟线只有 2026-08-14 起、10-20 与 10-21 两天整场没入库 —— '
                        '那几天的 — 是「没有分时读数」，而没进异动榜是「没过入表线」，两种都要分开念。'},
               {'code': 'break_count_null',
                'text': f'炸板次数空＝源站这一行没给这个字段（本场 {a.get("break_null", "—")} 只空着），'
                        f'不是「0 次没炸」；界面念 —。'},
               {'code': 'not_signal', 'text': DEFINITIONS['not_signal']}]

    if illegal:
        caveats.append({'code': 'bad_shape_filter', 'hard': True,
                        'text': f'形态档只认 {"/".join(SHAPE_KEYS)}，收到 {illegal} —— '
                                f'那几档已退回不筛（不是「筛出 0 只」）。'})
    if use:
        caveats.append({'code': 'shape_filter', 'hard': True,
                        'text': f'现在只列形态属于 {use} 的首板（在库里筛，先于取数上限）；'
                                f'上面每档那个数仍是本场全部首板的全场数。'})
    # 「几天几板」那一列 2026-10-27 用户点名撤掉了（换成「近 20 场几板」），那句
    # 「本场 N 只算不出几天几板」的告警就没有了屏幕上的指对象，随列一起撤；
    # `boards_disagree` / `boards_null` 仍照旧交（表里那两列还在算，首板页与对账要用），
    # 这一屏不再念它们。近 20 场那一列读不到时由 `_enrich` 那句 warn 与格子自身的 — 说话。

    for what in agg_failed:
        caveats.append({'code': 'agg_unread', 'hard': True,
                        'text': f'{what} —— 相关格子显示 —，不拿 0、也不拿空榜顶替「读不到」。'})

    cov = _safe_query(f"""
        select count(distinct trade_date)::int sessions, min(trade_date)::text a, max(trade_date)::text z
          from {TABLE} where spec_version = %s""", (SPEC_VERSION,))
    if cov is None:
        agg_failed.append('表覆盖区间')
        caveats.append({'code': 'agg_unread', 'hard': True,
                        'text': '表覆盖区间（共几场、从哪天到哪天）没读到 —— 那一格显示 —。'})
        cov = []
    c = cov[0] if cov else {}

    return {
        'available': True,
        'date': d,
        'spec_version': SPEC_VERSION,
        'rule': RULE,
        'rows': out,
        'rows_returned': len(out),
        'limit': n,
        'shape_filter': use or None,
        'first_count': _num(a.get('first_count')),
        'universe': _num(a.get('universe')),
        'one_word': _num(a.get('one_word')),
        'prev_touched': _num(a.get('prev_touched')),
        'prev_pct_med': _num(a.get('prev_pct_med'), 2),
        'boards_disagree': _num(a.get('boards_disagree')),
        'boards_null': _num(a.get('boards_null')),
        'shape_tally': shape_tally,
        'reason_tally': tags,
        'recent': recent,
        # 四列历史位置的窗口与档位（屏上"近 3 个月""≈2 年""MA5/10/20/60"这些字都从这里来，
        # 界面不抄第二份：窗口一改（63→66）屏上那句"近 3 个月"就会变成谎话）
        'history': {'sessions_3m': FBD.HIST_3M_SESSIONS, 'sessions_6m': FBD.HIST_6M_SESSIONS,
                    'sessions_2y': FBD.HIST_2Y_SESSIONS, 'pos_min_bars': FBD.POS_MIN_BARS,
                    'ma_spans': list(FBD.MA_SPANS),
                    'stack_names': dict(FBD.MA_STACK_NAMES),
                    'stack_labels': dict(FBD.MA_STACK_LABELS),
                    'stack_order': list(FBD.MA_STACK_PRIORITY),
                    'seal_eps': FBD.LIMIT_SEAL_EPS},
        'coverage': {'sessions': _num(c.get('sessions')), 'from': c.get('a'), 'to': c.get('z')},
        'caliber': (f'表内 spec_version={SPEC_VERSION}，覆盖 {c.get("a")} ~ {c.get("z")} 共 '
                    f'{c.get("sessions")} 场；本场首板 {a.get("first_count")} 只、'
                    f'当日封住 {a.get("universe")} 只。名次算法：'
                    f'{RANKING.RULE_NOTE_SHORT}'),
        'definitions': dict(DEFINITIONS),
        'thresholds': FBD.PREV_SHAPE_THRESHOLDS,
        'caveats': caveats,
        'calculated_at': (str(rows[0]['calculated_at']) if rows and rows[0].get('calculated_at') else None),
        'source': 'pg analytics.first_board_daily（写侧 app/compute/first_board_daily.py，'
                  '母体 analytics.first_board_shape fb-2，原因/封板时间取自同花顺涨停揭秘池）',
    }
