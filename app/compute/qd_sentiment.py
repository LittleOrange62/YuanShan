# -*- coding: utf-8 -*-
"""
qd 轨 · 情绪周期 qd.sentiment_cycle（每日一行）。

算法契约：`数据加工工作指南/09-quantdash逻辑规格.md` §4.1。
零行 quantdash 源码（PolyForm Noncommercial，见 qd_common 模块头）。

**为什么这一族用 Python 逐日算、而不是 set-based SQL**：09 §4.1 是一张 24 行的
公式表 + 三套 if-elif 短路阈值 + 一堆"分母为 0 写 NULL / 缺数据不判"的分支。
翻成一条大 SQL 后，任何人拿文档核对都要在 CTE 迷宫里做控制流反推。逐日一段
直白代码，一次回填 1170 个交易日要几分钟，而日常增量只算 2 天 —— 慢得起，
而且**"能被逐条对着文档检查"在这张表上比快重要**。

舍入按 09 §2「显式做、不留给前端」的**语义**执行：落库前用 ROUND_HALF_UP 定好
位数（与 PostgreSQL `round(numeric, n)` 同一种舍入），存进去的已是最终值。
例外是 avg_board_gain，契约写"不舍入"，但列类型 numeric(6,3) 本身限定位宽，
已在 deviation 记 L-12。

连板分布一律**读 qd.board_ladder_member / board_ladder_leader / stock_strength**
（由 qd2_ladder 落库），不从原始日线另推一遍：qd 轨内部先自洽，才谈得上和
analytics.* 那轨比差异。所以 **calc_qd_sentiment 依赖 calc_qd_ladder 先跑**，
且要多看前后各一天（T+1 的晋级、T-1 的修复），故 `config.job.after_job` 指它。

`source` 列本表恒为 'quantdash-logic'：本表所有"用本地实算替换 quantdash 伪造"
的地方都由 `deviation` 的 D-* 编码逐行说明（见 qd_common 模块头的编码约定），
不需要再用 source 重复表达；而天梯成员表的 +local 是**加了它没有的列**，两者
不是一回事。
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import ROUND_HALF_EVEN, Decimal

from app.compute import qd_common as Q
from app.db import engine

D = Decimal

FEISHU_DEFAULT = '分歧震荡'
CONF_BASE = {'主升': 72, '修复': 68, '退潮': 75, '冰点': 70}
CONF_RISK = {'low': 6, 'high': 8}
CHUNK = 60          # 一次装载的交易日数：涨跌 pct 映射按天全市场拉，攒太多会吃内存

INSERT_SQL = """
INSERT INTO qd.sentiment_cycle
       (trade_date, spec_version, source, deviation,
        stage_ui, stage_mcp, stage_feishu, reasons, confidence,
        risk_level, volume_state, sentiment_value, board_height,
        leader_board_count, high_board_count, limit_up_count, limit_down_count,
        first_board_ratio_pct, broken_rate_sealed_pct, broken_repair_rate_lag1_pct,
        success_rate_pct, a_kill_count_lag1, leader_next_close_pct,
        first_board_count, high_board_ratio_pct, relay_count, avg_board_gain,
        premium_score, broken_rate_touched_pct, touched_count, broken_touched_count,
        big_face_count_lag1, big_face_repair_rate_lag1_pct, weak_count_lag1,
        rise_count, fall_count, flat_count, scope, amount_yi, amount_change_pct)
VALUES
       (%(trade_date)s, %(sv)s, 'quantdash-logic', %(dev)s::jsonb,
        %(stage_ui)s, %(stage_mcp)s, %(stage_feishu)s, %(reasons)s::jsonb,
        %(confidence)s, %(risk_level)s, %(volume_state)s, %(sentiment_value)s,
        %(board_height)s, %(leader_board_count)s, %(high_board_count)s,
        %(limit_up_count)s, %(limit_down_count)s, %(first_board_ratio_pct)s,
        %(broken_rate_sealed_pct)s, %(broken_repair_rate_lag1_pct)s,
        %(success_rate_pct)s, %(a_kill_count_lag1)s, %(leader_next_close_pct)s,
        %(first_board_count)s, %(high_board_ratio_pct)s, %(relay_count)s,
        %(avg_board_gain)s, %(premium_score)s, %(broken_rate_touched_pct)s,
        %(touched_count)s, %(broken_touched_count)s, %(big_face_count_lag1)s,
        %(big_face_repair_rate_lag1_pct)s, %(weak_count_lag1)s,
        %(rise_count)s, %(fall_count)s, %(flat_count)s, %(scope)s,
        %(amount_yi)s, %(amount_change_pct)s)
ON CONFLICT (trade_date, spec_version) DO UPDATE SET
    source = EXCLUDED.source, deviation = EXCLUDED.deviation,
    stage_ui = EXCLUDED.stage_ui, stage_mcp = EXCLUDED.stage_mcp,
    stage_feishu = EXCLUDED.stage_feishu, reasons = EXCLUDED.reasons,
    confidence = EXCLUDED.confidence, risk_level = EXCLUDED.risk_level,
    volume_state = EXCLUDED.volume_state, sentiment_value = EXCLUDED.sentiment_value,
    board_height = EXCLUDED.board_height,
    leader_board_count = EXCLUDED.leader_board_count,
    high_board_count = EXCLUDED.high_board_count,
    limit_up_count = EXCLUDED.limit_up_count,
    limit_down_count = EXCLUDED.limit_down_count,
    first_board_ratio_pct = EXCLUDED.first_board_ratio_pct,
    broken_rate_sealed_pct = EXCLUDED.broken_rate_sealed_pct,
    broken_repair_rate_lag1_pct = EXCLUDED.broken_repair_rate_lag1_pct,
    success_rate_pct = EXCLUDED.success_rate_pct,
    a_kill_count_lag1 = EXCLUDED.a_kill_count_lag1,
    leader_next_close_pct = EXCLUDED.leader_next_close_pct,
    first_board_count = EXCLUDED.first_board_count,
    high_board_ratio_pct = EXCLUDED.high_board_ratio_pct,
    relay_count = EXCLUDED.relay_count, avg_board_gain = EXCLUDED.avg_board_gain,
    premium_score = EXCLUDED.premium_score,
    broken_rate_touched_pct = EXCLUDED.broken_rate_touched_pct,
    touched_count = EXCLUDED.touched_count,
    broken_touched_count = EXCLUDED.broken_touched_count,
    big_face_count_lag1 = EXCLUDED.big_face_count_lag1,
    big_face_repair_rate_lag1_pct = EXCLUDED.big_face_repair_rate_lag1_pct,
    weak_count_lag1 = EXCLUDED.weak_count_lag1,
    rise_count = EXCLUDED.rise_count, fall_count = EXCLUDED.fall_count,
    flat_count = EXCLUDED.flat_count, scope = EXCLUDED.scope,
    amount_yi = EXCLUDED.amount_yi, amount_change_pct = EXCLUDED.amount_change_pct,
    calculated_at = now()
"""


rnd = Q.rnd      # 定值只认一处实现，与 PostgreSQL round(numeric,n) 同为 ROUND_HALF_UP


def _lt(a, b):
    """a < b；任一为 None 则**不成立**。

    quantdash 用 `int(x or 0)` 让缺数据满足 `<=4`（无数据即判"试错"，D-5），
    又用哨兵 -99 让缺失把阶段推向退潮（D-4）。契约裁定：缺失 → 条件不成立。
    """
    return a is not None and b is not None and D(str(a)) < D(str(b))


def _le(a, b):
    return a is not None and b is not None and D(str(a)) <= D(str(b))


def _ge(a, b):
    return a is not None and b is not None and D(str(a)) >= D(str(b))


def _txt(v):
    """给 reason 文案用的取值显示。缺失写"缺"，不写 0 —— 与 NULL≠0 同一条纪律。"""
    return '缺' if v is None else str(v)


def _ratio(num, den, n: int):
    """num/den×100，定值 n 位；分母 0 或 None → None（不写 0，D-6 的核心）。"""
    if not den:
        return None
    return rnd(D(num) * 100 / D(den), n)


# =============================================================================
# 输入装载（按 chunk 批量拉，逐日只做字典查找）
# =============================================================================

def _adjacent(dates: list[dt.date]) -> list[dt.date]:
    """dates 自身 + 每日的上一/下一交易日：T-1 场次修到 T、T 场次晋级到 T+1 都要用。"""
    out = set(dates)
    rows = engine.query(
        """SELECT calendar_date, prev_open_date, next_open_date
             FROM reference_data.trade_calendar
            WHERE calendar_date = ANY (%s) AND is_open""", (dates,))
    for r in rows:
        for k in ('prev_open_date', 'next_open_date'):
            if r[k]:
                out.add(r[k])
    return sorted(out)


def load(dates: list[dt.date], sv: str) -> dict:
    all_d = _adjacent(dates)
    L = {'boards': {}, 'broken': {}, 'limit_down': {}, 'breadth': {},
         'leader': {}, 'amount': {}, 'pct': {}, 'have_ladder': set()}

    for r in engine.query(
            """SELECT trade_date, board_count, security_code, pct_change_actual
                 FROM qd.board_ladder_member
                WHERE trade_date = ANY (%s) AND spec_version = %s""", (all_d, sv)):
        L['boards'].setdefault(r['trade_date'], {})\
                  .setdefault(r['board_count'], []).append(r['security_code'])

    for r in engine.query(
            """SELECT trade_date, security_code, pct_change
                 FROM qd.stock_strength
                WHERE trade_date = ANY (%s) AND pool_kind = 'touched_not_sealed'""",
            (all_d,)):
        L['broken'].setdefault(r['trade_date'], []).append(
            (r['security_code'], r['pct_change']))

    # 跌停家数：双边严格判据，**不取 analytics.market_breadth**（那张表是单边、虚高）
    for r in engine.query(
            """SELECT trade_date,
                      count(*) FILTER (WHERE limit_down IS NOT NULL
                                         AND abs(close - limit_down) <= %s::numeric) AS ld,
                      count(*) FILTER (WHERE close > pre_close)  AS up,
                      count(*) FILTER (WHERE close < pre_close)  AS dn,
                      count(*) FILTER (WHERE close = pre_close)  AS fl
                 FROM market_data.stock_daily
                WHERE trade_date = ANY (%s) AND close IS NOT NULL
                  AND pre_close IS NOT NULL
                GROUP BY 1""", (Q.LIMIT_EPS, all_d)):
        L['limit_down'][r['trade_date']] = r['ld']
        L['breadth'][r['trade_date']] = (r['up'], r['dn'], r['fl'])

    # pct 映射：只查用到的票太碎，直接按天全市场拉（一个 chunk 约 33 万行）
    for r in engine.query(
            """SELECT trade_date, security_code, pct_change FROM market_data.stock_daily
                WHERE trade_date = ANY (%s) AND pct_change IS NOT NULL""", (all_d,)):
        L['pct'][(r['trade_date'], r['security_code'])] = r['pct_change']

    for r in engine.query(
            """SELECT trade_date, leader_board_count, leader_next_close_pct
                 FROM qd.board_ladder_leader
                WHERE trade_date = ANY (%s) AND spec_version = %s""", (all_d, sv)):
        L['leader'][r['trade_date']] = (r['leader_board_count'],
                                        r['leader_next_close_pct'])

    # 两市成交额：上证 + 深成指，**两只都有**才算，缺一只写 NULL（09 §4.1）
    for r in engine.query(
            """SELECT trade_date, sum(amount_yuan) AS amt,
                      count(DISTINCT security_code) AS n
                 FROM market_data.index_daily
                WHERE trade_date = ANY (%s)
                  AND security_code IN (%s, %s)
                GROUP BY 1 HAVING count(DISTINCT security_code) = 2""",
            (all_d, Q.SH_INDEX, Q.SZ_INDEX)):
        L['amount'][r['trade_date']] = r['amt']

    for r in engine.query(
            """SELECT DISTINCT trade_date FROM qd.board_ladder
                WHERE trade_date = ANY (%s) AND spec_version = %s""", (all_d, sv)):
        L['have_ladder'].add(r['trade_date'])
    return L


def _prev(d: dt.date) -> dt.date | None:
    return engine.scalar('SELECT prev_open_date FROM reference_data.trade_calendar '
                         'WHERE calendar_date = %s AND is_open', (d,))


def _nxt(d: dt.date) -> dt.date | None:
    return engine.scalar('SELECT next_open_date FROM reference_data.trade_calendar '
                         'WHERE calendar_date = %s AND is_open', (d,))


# =============================================================================
# 单日计算
# =============================================================================

def compute(d: dt.date, sv: str, L: dict, cal: dict) -> dict:
    """
    算出一行的全部列。契约表里每一行在这里都能找到对应的一段。

    cal 是该日的 {prev, next} 交易日，由 refresh 预先批量算好。
    """
    pd_, nd = cal['prev'], cal['next']
    dev: list[dict] = []

    boards = L['boards'].get(d, {}) if d in L['have_ladder'] else None
    boards_p = L['boards'].get(pd_, {}) if pd_ in L['have_ladder'] else None
    boards_n = L['boards'].get(nd, {}) if nd in L['have_ladder'] else None
    broken = L['broken'].get(d, [])
    broken_p = L['broken'].get(pd_, [])

    # ---- 连板分布族 --------------------------------------------------------
    limit_up = sum(len(v) for v in boards.values()) if boards is not None else None
    first_board = len(boards.get(1, [])) if boards is not None else None
    high_board = sum(len(v) for k, v in boards.items() if k >= Q.HIGH_BOARD) \
        if boards is not None else None
    board_height = max(boards) if boards else 0        # 无涨停 → 0 是真值
    touched = (limit_up + len(broken)) if limit_up is not None else None

    leader_row = L['leader'].get(d)
    leader_board = leader_row[0] if leader_row else None
    # 阶段用的 leader_next_close_pct 是 **T-1 龙头在 T 日**的收盘涨幅（09 §日期错位）
    leader_prev_row = L['leader'].get(pd_)
    leader_next_close_pct = leader_prev_row[1] if leader_prev_row else None

    # ---- 晋级率族：需要 T+1 -----------------------------------------------
    if boards is None or boards_n is None or not limit_up:
        success_rate = avg_gain = None
        if nd and nd not in L['have_ladder'] and limit_up:
            dev.append({'code': 'L-3', 'note': 'T+1 尚未入库，晋级率四列留 NULL'})
    else:
        promoted = [(b, c) for b, codes in boards.items() for c in codes
                    if any(c in v for v in boards_n.values())]
        nb = {c: k for k, v in boards_n.items() for c in v}
        success_rate = _ratio(len(promoted), limit_up, 2)
        # 单股封顶 3 板增量；只有次日确实还在板上才有"收益"可言
        avg_gain = rnd(sum(min(nb[c] - b, 3) for b, c in promoted) / D(limit_up), 3) \
            if promoted else D(0)
        dev.append({'code': 'L-9',
                    'note': '契约未写明"次日未涨停"是否计入 avg_board_gain 的分子；'
                            '本实现只累加次日仍封板的票（否则分子会被负板数差污染）'})
    broken_sealed = None if success_rate is None else rnd(D(100) - success_rate, 1)
    premium = None
    if success_rate is not None and avg_gain is not None:
        # 注意 success_rate 在这里要换回**分数**再乘 60（quantdash 存分数，D-2 换算陷阱）
        premium = rnd(success_rate / 100 * 60 + avg_gain * 10, 2)
    dev.append({'code': 'L-12',
                'note': 'avg_board_gain 契约写"不舍入"，但列类型 numeric(6,3) 限定位宽'})

    broken_touched_pct = _ratio(len(broken), touched, 1)
    if broken_touched_pct is not None:
        dev.append({'code': 'D-3',
                    'note': '日线推不出"曾封板后炸开"，分子只能用触板未封近似；'
                            '分母是触板数，与东财炸板池口径差约 64%%（实测 09-22 36.3 vs 22.2）'})

    # ---- T-1 场次修到 T（_lag1 族）----------------------------------------
    if pd_ not in L['have_ladder']:
        repair = big_face = big_face_repair = a_kill = weak = None
    else:
        def now(c):
            return L['pct'].get((d, c))

        repaired = [c for c, _ in broken_p if now(c) is not None and now(c) > 0]
        repair = _ratio(len(repaired), len(broken_p), 1) if broken_p else None
        if broken_p == []:
            dev.append({'code': 'D-6', 'note': '昨日炸板池为空 → 修复率 NULL 而不是 0'})
        faces = [c for c, p in broken_p if p is not None and p <= D(str(Q.BIG_FACE_PCT))]
        big_face = len(faces)
        big_face_repair = (_ratio(sum(1 for c in faces if now(c) is not None and now(c) > 0),
                                  len(faces), 1) if faces else None)
        high_prev = [c for b, codes in (boards_p or {}).items()
                     if b >= Q.HIGH_BOARD for c in codes]
        a_kill = sum(1 for c in high_prev
                     if now(c) is not None and now(c) <= D(str(Q.A_KILL_PCT)))
        weak = sum(1 for c in high_prev if now(c) is not None and now(c) < 0)
        skipped = [c for c in high_prev if now(c) is None]
        if skipped:
            dev.append({'code': 'L-13',
                        'note': '高板股次日无 K 线被跳过 %d 只（%s）；'
                                'quantdash 同样跳过但不记家数'
                                % (len(skipped), ','.join(sorted(skipped)[:5]))})

    # ---- 情绪分（09 §4.1 唯一一条容易写错的公式）--------------------------
    # 10 × Σ_{b=2..14, prev[b]>0} (curr[b+1] / prev[b]) ÷ 参与求和的 b 个数
    def sentiment_of(curr, prev):
        if curr is None or prev is None:
            return None
        terms = [D(len(curr.get(b + 1, []))) / D(len(prev[b]))
                 for b in range(Q.SENT_B_MIN, Q.SENT_B_MAX + 1) if prev.get(b)]
        return rnd(D(10) * sum(terms) / len(terms), 2) if terms else None

    sentiment_value = sentiment_of(boards, boards_p)
    if boards is not None and boards_p is not None and sentiment_value is None:
        dev.append({'code': 'D-2',
                    'note': '昨日至 14 板无分布 → 情绪分 NULL；'
                            'quantdash 在此写常量 5（涨停>50）或 2，TS 路径从不算公式'})
        dev.append({'code': 'L-14',
                    'note': '契约未规定"无可求和项"时的退路，此处按 NULL ≠ 0 的总纪律写空'})

    # ---- 成交额与量能状态 --------------------------------------------------
    def amount_yi_of(day):
        """(上证 + 深成指)/1e8，整数，照抄 Python round 的 banker 舍入。缺一只 → None。"""
        if day not in L['amount'] or not L['amount'][day]:
            return None
        return int((D(str(L['amount'][day])) / D(10) ** 8)
                   .quantize(D(1), rounding=ROUND_HALF_EVEN))

    def chg_of(day, base):
        if day not in L['amount'] or base not in L['amount']:
            return None
        a, b = D(str(L['amount'][day])), D(str(L['amount'][base]))
        return rnd((a - b) * 100 / b, 2) if b else None

    amount_yi = amount_yi_of(d)
    dev.append({'code': 'D-37',
                'note': '两市成交额=上证+深成指，不含北交所与深市非成分股，'
                        '不等于全 A 成交额；沿用 quantdash 口径并如实标注'})
    # ---- 成交额环比：基期是 T-1，不是 T-2（2026-09-25 勘误 Q-fix-1）--------
    # 这里原本写的是 `chg_of(d, prev2)` / `chg_of(prev2, prev3)`，于是全库 1,148 个
    # 非空值没有一条是 T 对前一交易日：09-18 库里曾存 +12.94%（基数其实是 09-16 的
    # 18,391 亿），真值是对 09-17 的 18,231 亿 = +13.93%；09-17 更狠，存 +13.05%
    # 而真值是 −0.87%，**符号都是反的**。下游 volume_state → confidence → 阶段链
    # 一起错（实测波及 volume_state 656 场、stage_ui/stage_mcp/reasons 各 45 场、
    # confidence 528 场）。
    # 注意 `sentiment_value_p` 用 pd2 是**对的**（它要的就是前一天的情绪分），
    # 只错了一半，所以任何"和自家别的列对一下"的自洽校验都发现不了 ——
    # 能发现它的只有拿 index_daily 独立重算一遍。
    pd2 = cal.get('prev2')
    chg = chg_of(d, pd_)
    chg_p = chg_of(pd_, pd2)
    dev.append({'code': 'Q-fix-1',
                'note': '2026-09-25 勘误：本列曾按 `amount_change_pct = T 对 T-2` 落库'
                        '（基期误取 prev2），现按契约 T 对 T-1。受影响列：'
                        'amount_change_pct、volume_state、stage_ui、stage_mcp、'
                        'confidence、reasons、deviation。编号不占 D-*（那族是'
                        '"quantdash 的缺陷未被复现"，这是本实现自己的勘误）。'})
    sentiment_value_p = sentiment_of(boards_p, L['boards'].get(pd2, {})
                                     if pd2 in L['have_ladder'] else None)

    # 四值判定。契约明说"其余（含 change 为 NULL）"归存量震荡 —— 照搬，但记 L-1
    if chg is not None and chg_p is not None and D(str(chg)) > 0 and D(str(chg_p)) > 0:
        volume_state = '持续放量'
    elif chg is not None and chg_p is not None and D(str(chg)) < 0 and D(str(chg_p)) < 0:
        volume_state = '缩量再缩量'
    elif (chg is not None and D(str(chg)) > 0 and sentiment_value is not None
          and sentiment_value_p is not None
          and D(str(sentiment_value)) <= D(str(sentiment_value_p))):
        volume_state = '放量滞涨'
    else:
        volume_state = '存量震荡'
        if chg is None or chg_p is None:
            dev.append({'code': 'L-1',
                        'note': '成交额环比缺失却被归入"存量震荡"（照搬 quantdash 行为，'
                                '但这不是实测出来的量能状态）'})

    # ---- risk_level（输入全 NULL 时写 NULL，不写 low —— D-7）---------------
    # ⚠️ 契约 09 §4.1 把两件事接错线了，这里按 quantdash 的**真实代码**走：
    #   · 喂 risk_level 的 brokenRate = 炸板池/涨停池
    #       —— scripts/fetch_sentiment_cycle_snapshots.py:496-503
    #          `broken_rate = round((len(broken_pool)/len(today_pool))*100, 1)`，
    #          紧接 `if a_kill_count>=2 or broken_rate>=35 then high`
    #       —— services/sentimentCycle/highRisk.ts:32 同一条
    #   · (1 - successRate)×100 是**另一个面板**的历史炸板率曲线
    #       —— services/sentimentCycle/performance.ts:88,124
    # 两者不是同一个数。若按契约字面把 (1-晋级率)×100 喂进 risk 判据，A 股晋级率
    # 常年 20~50%% ⇒ 该值常年 50~80%% ⇒ 每天都判 high ⇒ stage 恒为退潮
    # （实测 32 天全是 退潮/退潮/退潮，且 09 §8 要求的"三套 stage 出现分歧"永远做不出来）。
    # 本列保留 (1-晋级率)×100 在原位（它就是 performance 面板的那条曲线），
    # 只把 risk 的输入换成触板口径。
    if a_kill is None and broken_touched_pct is None and weak is None:
        risk = None
        dev.append({'code': 'D-7', 'note': '风险输入全缺 → risk_level 留 NULL；'
                                           'quantdash 把无数据当"低风险"'})
    elif _ge(a_kill, 2) or _ge(broken_touched_pct, 35):
        risk = 'high'
    elif _ge(a_kill, 1) or _ge(broken_touched_pct, 20) or _ge(weak, 2):
        risk = 'medium'
    else:
        risk = 'low'
    dev.append({'code': 'L-19',
                'note': 'risk_level 的炸板率输入用**触板口径**（broken_rate_touched_pct），'
                        '不是契约字面写的 (1-晋级率)×100；后者是 quantdash performance '
                        '面板的独立曲线（performance.ts:124），两个数不等价。'
                        '判据阈值 20/35 与出处见 fetch_sentiment_cycle_snapshots.py:496-503'})

    # ---- 三套阶段（顺序短路，一条都不许换）--------------------------------
    # reasons 的形状与归属按 09 §4.1 + 页面展示工作指南/02 §8.1 的裁定：
    # **每个 stage 各一条**，形如 {stage, hit_rule, reason}，hit_rule 是该套判据链里
    # 命中的优先级序号。三个 stage 一致时看不出差别，不一致的日子前端要把理由贴到
    # 对应那颗胶囊上，所以不能给一条不分口径的字符串。
    fbr = _ratio(first_board, limit_up, 1)

    def stage_ui():
        if risk == 'high' or _le(leader_next_close_pct, -5) or _lt(repair, 20):
            return '退潮', 1, ('风险等级 high / 龙头次日收盘 <=-5 / 昨日炸板修复率 <20，'
                              '命中其一即判退潮（本行修复率 %s、龙头次日 %s、风险 %s）'
                              % (_txt(repair), _txt(leader_next_close_pct), risk))
        if _le(board_height, 2) and _lt(limit_up, 20):
            return '冰点', 2, '最高仅 %s 板且涨停仅 %s 家' % (board_height, limit_up)
        if _ge(repair, 35) and _ge(leader_next_close_pct, 0)                 and volume_state != '缩量再缩量':
            return '修复', 3, '昨日炸板修复率 %s 已满 35，龙头次日未砸' % _txt(repair)
        if _ge(sentiment_value, 5) and _ge(high_board, 3) and _ge(leader_board, 5)                 and volume_state != '缩量再缩量':
            return '主升', 4, '情绪分 %s 满 5、4 板以上 %s 只、龙头 %s 板，且非缩量再缩量'                 % (_txt(sentiment_value), _txt(high_board), _txt(leader_board))
        if _ge(fbr, 60) or _le(leader_board, 4):
            return '试错', 5, '首板占比 %s 满 60，或龙头仅 %s 板' % (_txt(fbr), _txt(leader_board))
        return '分歧', 6, '未命中任何阶段条件，兜底为分歧'

    def stage_mcp():
        if risk == 'high' or _le(leader_next_close_pct, -5) or _lt(repair, 20):
            return '退潮', 1, '与总览版同一条：风险 high / 龙头次日 <=-5 / 修复率 <20'
        if _le(leader_board, 2) and _lt(limit_up, 20):
            return '冰点', 2, 'MCP 版冰点看的是龙头板数而非最高板：龙头 %s 板 <=2 且涨停 %s <20'                 % (_txt(leader_board), _txt(limit_up))
        if _ge(repair, 35) and _ge(leader_next_close_pct, 0)                 and volume_state != '缩量再缩量':
            return '修复', 3, '修复率 %s 满 35 且龙头未砸，量能非缩量再缩量' % _txt(repair)
        if _ge(high_board, 3) and _ge(leader_board, 5)                 and volume_state != '缩量再缩量':
            return '主升', 4, ('MCP 版主升不要求情绪分满 5（这是它与总览版的差异）：'
                              '高板 %s 只满 3、龙头 %s 板满 5' % (_txt(high_board), _txt(leader_board)))
        if _ge(fbr, 60) or _le(leader_board, 4):
            return '试错', 5, '首板占比 %s 满 60 或龙头仅 %s 板' % (_txt(fbr), _txt(leader_board))
        return '分歧', 6, '未命中任何判据，兜底为分歧'

    def stage_feishu():
        # 契约只给了飞书版三条判据（09 §4.1）：冰点/试错两档**无判据可依**，
        # 不替它编阈值 —— 本列只会出四值，写进 deviation L-10
        if risk == 'high' or _ge(broken_touched_pct, 35) or _lt(repair, 20):
            return '退潮', 1, ('飞书版偏防守：风险 high / 炸板率 %s 满 35 / 修复率 <20'
                              % _txt(broken_touched_pct))
        if _ge(repair, 35) and _ge(success_rate, 65) and _ge(leader_next_close_pct, 0):
            return '修复', 2, '飞书版偏进攻：修复率满 35 且晋级率 %s 满 65 且龙头未砸'                 % _txt(success_rate)
        if _ge(leader_board, 5) and _ge(success_rate, 70) and a_kill == 0:
            return '主升', 3, '飞书版延续：龙头 %s 板满 5、晋级率 %s 满 70、无 A 杀'                 % (_txt(leader_board), _txt(success_rate))
        return FEISHU_DEFAULT, 4, ('未命中飞书版三条判据，落默认档分歧震荡'
                                  '（契约未给该版冰点/试错判据，故本列只出四值）')

    s_ui, n_ui, t_ui = stage_ui()
    s_mcp, n_mcp, t_mcp = stage_mcp()
    s_fs, n_fs, t_fs = stage_feishu()
    reasons = [{'stage': 'stage_ui', 'hit_rule': n_ui, 'reason': t_ui},
               {'stage': 'stage_mcp', 'hit_rule': n_mcp, 'reason': t_mcp},
               {'stage': 'stage_feishu', 'hit_rule': n_fs, 'reason': t_fs}]

    dev.append({'code': 'D-43',
                'note': '阶段分布严重偏斜是**照搬优先级链的必然结果**，不是本实现算错：'
                        '"退潮"排第一且条件含 `昨日炸板修复率<20`（而修复率低于 20 是常态），'
                        '"冰点"虽列第二但要求 `最高板<=2 且涨停<20`，永远先被"退潮"截走。'
                        '实测 1169 天：stage_ui 退潮 936 / 修复 125 / 试错 88 / 主升 19 / '
                        '分歧 1 / **冰点 0 次**；stage_mcp 退潮 936 / 修复 125 / 试错 84 / '
                        '主升 23 / 分歧 1；risk high 890 / medium 272 / low 7。'
                        '飞书版更窄，1169 天只出两档（退潮 901 / 分歧震荡 268），'
                        '等于它只区分"防守 / 震荡"。前端不要按六档均匀出现来设计'})
    dev.append({'code': 'D-4',
                'note': 'quantdash 判阶段时混用交易日（leader/sentiment 取 T、'
                        'repair/high_risk 取 T-1）；本行已把 T-1 场次一律命名 _lag1'})
    dev.append({'code': 'D-5',
                'note': '缺失的判据输入按"条件不成立"处理，不用 0/-99 哨兵参与比较'})
    dev.append({'code': 'D-9', 'note': '兜底分歧也给出理由文案（quantdash 不给）'})
    if s_fs != s_ui or s_mcp != s_ui:
        dev.append({'code': 'L-10',
                    'note': '三套阈值给出三个阶段（ui=%s / mcp=%s / feishu=%s）—— '
                            '这是 quantdash 自身前后矛盾，如实呈现不取平均；'
                            '且飞书版契约只给了三条判据，冰点/试错两档无判据可依'
                            % (s_ui, s_mcp, s_fs)})

    # confidence：查表加和，**不是统计置信度**（D-8），去掉恒不触发的 min(95,·)
    conf = (CONF_BASE.get(s_ui, 62) + CONF_RISK.get(risk or '', 3)
            + (6 if volume_state in ('持续放量', '缩量再缩量') else 0))
    dev.append({'code': 'D-8',
                'note': '确定性查表加和（base+risk_adj+vol_adj），非统计置信度；'
                        '去掉 quantdash 那个恒不触发的 min(95,·) 死上限；'
                        'risk_level 为 NULL 时 risk_adj 取中档 +3'})

    up, dn, fl = L['breadth'].get(d, (None, None, None))

    return {
        'trade_date': d, 'sv': sv,
        'dev': json.dumps(dev, ensure_ascii=False),
        'reasons': json.dumps(reasons, ensure_ascii=False),
        'confidence': conf,
        'stage_ui': s_ui, 'stage_mcp': s_mcp, 'stage_feishu': s_fs,
        'risk_level': risk, 'volume_state': volume_state,
        'sentiment_value': sentiment_value, 'board_height': board_height,
        'leader_board_count': leader_board, 'high_board_count': high_board,
        'limit_up_count': limit_up,
        'limit_down_count': L['limit_down'].get(d),
        'first_board_ratio_pct': fbr,
        'broken_rate_sealed_pct': broken_sealed,
        'broken_repair_rate_lag1_pct': repair,
        'success_rate_pct': success_rate,
        'a_kill_count_lag1': a_kill,
        'leader_next_close_pct': leader_next_close_pct,
        'first_board_count': first_board,
        'high_board_ratio_pct': _ratio(high_board, limit_up, 1),
        'relay_count': None if limit_up is None or first_board is None
                       else max(limit_up - first_board, 0),
        'avg_board_gain': avg_gain, 'premium_score': premium,
        'broken_rate_touched_pct': broken_touched_pct,
        'touched_count': touched, 'broken_touched_count': len(broken),
        'big_face_count_lag1': big_face,
        'big_face_repair_rate_lag1_pct': big_face_repair,
        'weak_count_lag1': weak,
        'rise_count': up, 'fall_count': dn, 'flat_count': fl,
        'scope': Q.BOARD_SCOPE,
        'amount_yi': amount_yi, 'amount_change_pct': chg,
    }


def refresh(dates: list[dt.date]) -> dict:
    """逐日算 qd.sentiment_cycle。**天梯没跑过的那天整行不写**（宁可缺行不可假行）。"""
    sv = Q.spec_version()
    rows = 0
    skipped = []
    for i in range(0, len(dates), CHUNK):
        batch = dates[i:i + CHUNK]
        L = load(batch, sv)
        cal = {}
        for d in batch:
            p, n = _prev(d), _nxt(d)
            cal[d] = {'prev': p, 'next': n, 'prev2': _prev(p) if p else None,
                      'prev3': _prev(_prev(p)) if p else None}
        with engine.conn('etl') as c:
            for d in batch:
                if d not in L['have_ladder']:
                    skipped.append(d)          # 天梯没跑过这天，整行不写
                    continue
                c.execute(INSERT_SQL, compute(d, sv, L, cal[d]))
                rows += 1
    return {'rows': rows, 'days': len(dates), 'skipped_no_ladder': skipped}
