"""qd 轨情绪周期的计算层（移植自 FinanceTool `app/compute/qd_sentiment.py`）。

**为什么 YuanShan 侧要有一份**：`app/compute/qd_sentiment.py:356-358` 把成交额环比的
基期取成 `prev2`（应为 `prev1`），于是 `amount_change_pct` 全表 1148 行都是"T 对 T-2"，
下游 `volume_state` 判错 668 天。用户 2026-09-25 裁定的修法是 **(a)：YuanShan 侧生产者
把修正值写回 `qd.sentiment_cycle`**，不改 FT 一行（FT 冻结为只读参考实现）。

与 FT 那份的**唯一**算法差异就是基期：

    chg   = amount(T)  / amount(T-1) - 1     ← FT 写成 / amount(T-2)
    chg_p = amount(T-1)/ amount(T-2) - 1     ← FT 写成  amount(T-2)/amount(T-3)

其余判据、优先级链、deviation 编码逐字保持一致 —— 本模块跑完会拿"未受影响的列"
和库里现值逐列比对，**只要有一列对不上就说明移植走样，整批不写**。这是本文件
存在的唯一验收条件。

零行 quantdash 源码（PolyForm Noncommercial，见 `docs/数据加工工作指南/09-quantdash逻辑规格.md`）。
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from . import db

D = Decimal

# ---- 口径常量（与 FT qd_common 逐字相同；改这些要同时升 spec_version）----
LIMIT_EPS = 0.005
SENT_B_MIN, SENT_B_MAX = 2, 14
A_KILL_PCT = -8.0
BIG_FACE_PCT = -5.0
HIGH_BOARD = 4
SH_INDEX = '000001.SH'
SZ_INDEX = '399001.SZ'
BOARD_SCOPE = 'all_boards'

FEISHU_DEFAULT = '分歧震荡'
CONF_BASE = {'主升': 72, '修复': 68, '退潮': 75, '冰点': 70}
CONF_RISK = {'low': 6, 'high': 8}
CHUNK = 60

SETTINGS_KEYS = ('qd_spec_version', 'qd_turnover_board_pct', 'qd_sector_top_n')


def rnd(x, n: int):
    if x is None:
        return None
    return D(str(x)).quantize(D(1).scaleb(-n), rounding='ROUND_HALF_UP')


def setting(key: str, as_type=str):
    """读 config.setting。**读不到就抛，不给默认值**（口径写两份迟早不一致）。"""
    row = db.query('SELECT value FROM config.setting WHERE key = %(k)s', {'k': key})
    if not row or row[0]['value'] is None:
        raise KeyError('config.setting 缺 key=%s（三个键：%s）' % (key, ' / '.join(SETTINGS_KEYS)))
    val = row[0]['value']
    # psycopg 已把 jsonb 解成 Python 值，不能再 json.loads 一次
    if isinstance(val, dict) and 'value' in val:
        val = val['value']
    return as_type(val)


def spec_version() -> str:
    return setting('qd_spec_version', str)


def _lt(a, b):
    return a is not None and b is not None and D(str(a)) < D(str(b))


def _le(a, b):
    return a is not None and b is not None and D(str(a)) <= D(str(b))


def _ge(a, b):
    return a is not None and b is not None and D(str(a)) >= D(str(b))


def _txt(v):
    return '缺' if v is None else str(v)


def _ratio(num, den, n: int):
    if not den:
        return None
    return rnd(D(num) * 100 / D(den), n)


def _dec(v):
    """把驱动交回来的各种数值统一成 Decimal；None 原样传出去（NULL ≠ 0）。"""
    if v is None:
        return None
    if isinstance(v, D):
        return v
    try:
        return D(str(v))
    except InvalidOperation:
        return None


# =============================================================================
# 输入装载
# =============================================================================

def _adjacent(dates):
    out = set(dates)
    for r in db.query("""SELECT calendar_date, prev_open_date, next_open_date
                           FROM reference_data.trade_calendar
                          WHERE calendar_date = ANY (%s) AND is_open""", (list(dates),)):
        for k in ('prev_open_date', 'next_open_date'):
            if r[k]:
                out.add(r[k])
    return sorted(out)


def load(dates, sv: str) -> dict:
    all_d = _adjacent(dates)
    L = {'boards': {}, 'broken': {}, 'limit_down': {}, 'breadth': {},
         'leader': {}, 'amount': {}, 'pct': {}, 'have_ladder': set()}

    for r in db.query("""SELECT trade_date, board_count, security_code, pct_change_actual
                           FROM qd.board_ladder_member
                          WHERE trade_date = ANY (%s) AND spec_version = %s""",
                     (list(all_d), sv)):
        L['boards'].setdefault(r['trade_date'], {}).setdefault(r['board_count'], []) \
            .append(r['security_code'])

    for r in db.query("""SELECT trade_date, security_code, pct_change
                           FROM qd.stock_strength
                          WHERE trade_date = ANY (%s) AND pool_kind = 'touched_not_sealed'""",
                     (list(all_d),)):
        L['broken'].setdefault(r['trade_date'], []).append((r['security_code'], r['pct_change']))

    for r in db.query("""SELECT trade_date,
                                count(*) FILTER (WHERE limit_down IS NOT NULL
                                                   AND abs(close - limit_down) <= %s) AS ld,
                                count(*) FILTER (WHERE close > pre_close) AS up,
                                count(*) FILTER (WHERE close < pre_close) AS dn,
                                count(*) FILTER (WHERE close = pre_close) AS fl
                           FROM market_data.stock_daily
                          WHERE trade_date = ANY (%s) AND close IS NOT NULL
                            AND pre_close IS NOT NULL
                          GROUP BY 1""", (LIMIT_EPS, list(all_d))):
        L['limit_down'][r['trade_date']] = r['ld']
        L['breadth'][r['trade_date']] = (r['up'], r['dn'], r['fl'])

    for r in db.query("""SELECT trade_date, security_code, pct_change
                           FROM market_data.stock_daily
                          WHERE trade_date = ANY (%s) AND pct_change IS NOT NULL""",
                     (list(all_d),)):
        L['pct'][(r['trade_date'], r['security_code'])] = r['pct_change']

    for r in db.query("""SELECT trade_date, leader_board_count, leader_next_close_pct
                           FROM qd.board_ladder_leader
                          WHERE trade_date = ANY (%s) AND spec_version = %s""",
                     (list(all_d), sv)):
        L['leader'][r['trade_date']] = (r['leader_board_count'], r['leader_next_close_pct'])

    for r in db.query("""SELECT trade_date, sum(amount_yuan) AS amt,
                                count(DISTINCT security_code) AS n
                           FROM market_data.index_daily
                          WHERE trade_date = ANY (%s) AND security_code IN (%s, %s)
                          GROUP BY 1 HAVING count(DISTINCT security_code) = 2""",
                     (list(all_d), SH_INDEX, SZ_INDEX)):
        L['amount'][r['trade_date']] = r['amt']

    for r in db.query("""SELECT DISTINCT trade_date FROM qd.board_ladder
                          WHERE trade_date = ANY (%s) AND spec_version = %s""",
                     (list(all_d), sv)):
        L['have_ladder'].add(r['trade_date'])
    return L


def calendar_offsets(dates):
    """批量取每日的 prev/next/prev2/prev3 交易日（一次查完，别逐日打库）。"""
    need = set(dates)
    rows = db.query("""SELECT calendar_date, prev_open_date, next_open_date
                         FROM reference_data.trade_calendar
                        WHERE calendar_date = ANY (%s) AND is_open""", (list(dates),))
    cal = {r['calendar_date']: {'prev': r['prev_open_date'], 'next': r['next_open_date']} for r in rows}
    extra = {v['prev'] for v in cal.values() if v['prev']}
    rows2 = db.query("""SELECT calendar_date, prev_open_date FROM reference_data.trade_calendar
                         WHERE calendar_date = ANY (%s) AND is_open""", (list(extra),)) if extra else []
    p1 = {r['calendar_date']: r['prev_open_date'] for r in rows2}
    for d, v in cal.items():
        p = v['prev']
        v['prev2'] = p1.get(p) if p else None
        v['prev3'] = p1.get(p1.get(p)) if p and p1.get(p) else None
        need.update(x for x in (v['prev2'], v['prev3']) if x)
    return cal


# =============================================================================
# 单日计算
# =============================================================================

def compute(d, sv: str, L: dict, cal: dict, dev_extra=None) -> dict:
    pd_, nd = cal['prev'], cal['next']
    dev = []

    boards = L['boards'].get(d, {}) if d in L['have_ladder'] else None
    boards_p = L['boards'].get(pd_, {}) if pd_ in L['have_ladder'] else None
    boards_n = L['boards'].get(nd, {}) if nd in L['have_ladder'] else None
    broken = L['broken'].get(d, [])
    broken_p = L['broken'].get(pd_, [])

    limit_up = sum(len(v) for v in boards.values()) if boards is not None else None
    first_board = len(boards.get(1, [])) if boards is not None else None
    high_board = sum(len(v) for k, v in boards.items() if k >= HIGH_BOARD) \
        if boards is not None else None
    board_height = max(boards) if boards else 0
    touched = (limit_up + len(broken)) if limit_up is not None else None

    leader_row = L['leader'].get(d)
    leader_board = leader_row[0] if leader_row else None
    leader_prev_row = L['leader'].get(pd_)
    leader_next_close_pct = _dec(leader_prev_row[1]) if leader_prev_row else None

    if boards is None or boards_n is None or not limit_up:
        success_rate = avg_gain = None
        if nd and nd not in L['have_ladder'] and limit_up:
            dev.append({'code': 'L-3', 'note': 'T+1 尚未入库，晋级率四列留 NULL'})
    else:
        promoted = [(b, c) for b, codes in boards.items() for c in codes
                    if any(c in v for v in boards_n.values())]
        nb = {c: k for k, v in boards_n.items() for c in v}
        success_rate = _ratio(len(promoted), limit_up, 2)
        avg_gain = rnd(sum(min(nb[c] - b, 3) for b, c in promoted) / D(limit_up), 3) \
            if promoted else D(0)
        dev.append({'code': 'L-9',
                    'note': '契约未写明"次日未涨停"是否计入 avg_board_gain 的分子；'
                            '本实现只累加次日仍封板的票（否则分子会被负板数差污染）'})
    broken_sealed = None if success_rate is None else rnd(D(100) - success_rate, 1)
    premium = None
    if success_rate is not None and avg_gain is not None:
        premium = rnd(success_rate / 100 * 60 + avg_gain * 10, 2)
    dev.append({'code': 'L-12',
                'note': 'avg_board_gain 契约写"不舍入"，但列类型 numeric(6,3) 限定位宽'})

    broken_touched_pct = _ratio(len(broken), touched, 1)
    if broken_touched_pct is not None:
        dev.append({'code': 'D-3',
                    'note': '日线推不出"曾封板后炸开"，分子只能用触板未封近似；'
                            '分母是触板数，与东财炸板池口径差约 64%%（实测 09-22 36.3 vs 22.2）'})

    if pd_ not in L['have_ladder']:
        repair = big_face = big_face_repair = a_kill = weak = None
    else:
        def now(c):
            return _dec(L['pct'].get((d, c)))

        repaired = [c for c, _ in broken_p if now(c) is not None and now(c) > 0]
        repair = _ratio(len(repaired), len(broken_p), 1) if broken_p else None
        if broken_p == []:
            dev.append({'code': 'D-6', 'note': '昨日炸板池为空 → 修复率 NULL 而不是 0'})
        faces = [c for c, p in broken_p if p is not None and _dec(p) <= D(str(BIG_FACE_PCT))]
        big_face = len(faces)
        big_face_repair = (_ratio(sum(1 for c in faces if now(c) is not None and now(c) > 0),
                                  len(faces), 1) if faces else None)
        high_prev = [c for b, codes in (boards_p or {}).items() if b >= HIGH_BOARD for c in codes]
        a_kill = sum(1 for c in high_prev if now(c) is not None and now(c) <= D(str(A_KILL_PCT)))
        weak = sum(1 for c in high_prev if now(c) is not None and now(c) < 0)
        skipped = [c for c in high_prev if now(c) is None]
        if skipped:
            dev.append({'code': 'L-13',
                        'note': '高板股次日无 K 线被跳过 %d 只（%s）；'
                                'quantdash 同样跳过但不记家数'
                                % (len(skipped), ','.join(sorted(skipped)[:5]))})

    def sentiment_of(curr, prev):
        if curr is None or prev is None:
            return None
        terms = [D(len(curr.get(b + 1, []))) / D(len(prev[b]))
                 for b in range(SENT_B_MIN, SENT_B_MAX + 1) if prev.get(b)]
        return rnd(D(10) * sum(terms) / len(terms), 2) if terms else None

    sentiment_value = sentiment_of(boards, boards_p)
    if boards is not None and boards_p is not None and sentiment_value is None:
        dev.append({'code': 'D-2',
                    'note': '昨日至 14 板无分布 → 情绪分 NULL；'
                            'quantdash 在此写常量 5（涨停>50）或 2，TS 路径从不算公式'})
        dev.append({'code': 'L-14',
                    'note': '契约未规定"无可求和项"时的退路，此处按 NULL ≠ 0 的总纪律写空'})

    def amount_yi_of(day):
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

    # ---- 基期修正：环比的基期是 T-1，不是 T-2 ------------------------------
    pd2 = cal.get('prev2')
    chg = chg_of(d, pd_)
    chg_p = chg_of(pd_, pd2)
    dev.append({'code': 'Q-fix-1',
                'note': '2026-09-25 勘误：本列原先按 `amount_change_pct = T 对 T-2` 落库'
                        '（FT `app/compute/qd_sentiment.py:356-358` 基期取 prev2），'
                        '现按契约 T 对 T-1。受影响列：amount_change_pct、volume_state、'
                        'stage_ui、stage_mcp、confidence、reasons、deviation。'})
    sentiment_value_p = sentiment_of(boards_p, L['boards'].get(pd2, {})
                                     if pd2 in L['have_ladder'] else None)

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

    fbr = _ratio(first_board, limit_up, 1)

    def stage_ui():
        if risk == 'high' or _le(leader_next_close_pct, -5) or _lt(repair, 20):
            return '退潮', 1, ('风险等级 high / 龙头次日收盘 <=-5 / 昨日炸板修复率 <20，'
                              '命中其一即判退潮（本行修复率 %s、龙头次日 %s、风险 %s）'
                              % (_txt(repair), _txt(leader_next_close_pct), risk))
        if _le(board_height, 2) and _lt(limit_up, 20):
            return '冰点', 2, '最高仅 %s 板且涨停仅 %s 家' % (board_height, limit_up)
        if _ge(repair, 35) and _ge(leader_next_close_pct, 0) \
                and volume_state != '缩量再缩量':
            return '修复', 3, '昨日炸板修复率 %s 已满 35，龙头次日未砸' % _txt(repair)
        if _ge(sentiment_value, 5) and _ge(high_board, 3) and _ge(leader_board, 5) \
                and volume_state != '缩量再缩量':
            return '主升', 4, '情绪分 %s 满 5、4 板以上 %s 只、龙头 %s 板，且非缩量再缩量' \
                % (_txt(sentiment_value), _txt(high_board), _txt(leader_board))
        if _ge(fbr, 60) or _le(leader_board, 4):
            return '试错', 5, '首板占比 %s 满 60，或龙头仅 %s 板' % (_txt(fbr), _txt(leader_board))
        return '分歧', 6, '未命中任何阶段条件，兜底为分歧'

    def stage_mcp():
        if risk == 'high' or _le(leader_next_close_pct, -5) or _lt(repair, 20):
            return '退潮', 1, '与总览版同一条：风险 high / 龙头次日 <=-5 / 修复率 <20'
        if _le(leader_board, 2) and _lt(limit_up, 20):
            return '冰点', 2, ('MCP 版冰点看的是龙头板数而非最高板：龙头 %s 板 <=2 且涨停 %s <20'
                              % (_txt(leader_board), _txt(limit_up)))
        if _ge(repair, 35) and _ge(leader_next_close_pct, 0) \
                and volume_state != '缩量再缩量':
            return '修复', 3, '修复率 %s 满 35 且龙头未砸，量能非缩量再缩量' % _txt(repair)
        if _ge(high_board, 3) and _ge(leader_board, 5) and volume_state != '缩量再缩量':
            return '主升', 4, ('MCP 版主升不要求情绪分满 5（这是它与总览版的差异）：'
                              '高板 %s 只满 3、龙头 %s 板满 5'
                              % (_txt(high_board), _txt(leader_board)))
        if _ge(fbr, 60) or _le(leader_board, 4):
            return '试错', 5, '首板占比 %s 满 60 或龙头仅 %s 板' % (_txt(fbr), _txt(leader_board))
        return '分歧', 6, '未命中任何判据，兜底为分歧'

    def stage_feishu():
        if risk == 'high' or _ge(broken_touched_pct, 35) or _lt(repair, 20):
            return '退潮', 1, ('飞书版偏防守：风险 high / 炸板率 %s 满 35 / 修复率 <20'
                              % _txt(broken_touched_pct))
        if _ge(repair, 35) and _ge(success_rate, 65) and _ge(leader_next_close_pct, 0):
            return '修复', 2, '飞书版偏进攻：修复率满 35 且晋级率 %s 满 65 且龙头未砸' \
                % _txt(success_rate)
        if _ge(leader_board, 5) and _ge(success_rate, 70) and a_kill == 0:
            return '主升', 3, ('飞书版延续：龙头 %s 板满 5、晋级率 %s 满 70、无 A 杀'
                              % (_txt(leader_board), _txt(success_rate)))
        return FEISHU_DEFAULT, 4, ('未命中飞书版三条判据，落默认档分歧震荡'
                                  '（契约未给该版冰点/试错判据，故本列只出四值）')

    s_ui, n_ui, t_ui = stage_ui()
    s_mcp, n_mcp, t_mcp = stage_mcp()
    s_fs, n_fs, t_fs = stage_feishu()
    reasons = [{'stage': 'stage_ui', 'hit_rule': n_ui, 'reason': t_ui},
               {'stage': 'stage_mcp', 'hit_rule': n_mcp, 'reason': t_mcp},
               {'stage': 'stage_feishu', 'hit_rule': n_fs, 'reason': t_fs}]

    # D-43 是"阶段分布偏斜"的说明项。它原来给的那串分布数是**修正前**测的，
    # 所以这里不写死数字，由 refresh 用修正后的全局分布填进去（见 dev_extra）。
    dev.append({'code': 'D-43', 'note': dev_extra or '__PENDING_DISTRIBUTION__'})
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

    conf = (CONF_BASE.get(s_ui, 62) + CONF_RISK.get(risk or '', 3)
            + (6 if volume_state in ('持续放量', '缩量再缩量') else 0))
    dev.append({'code': 'D-8',
                'note': '确定性查表加和（base+risk_adj+vol_adj），非统计置信度；'
                        '去掉 quantdash 那个恒不触发的 min(95,·) 死上限；'
                        'risk_level 为 NULL 时 risk_adj 取中档 +3'})

    up, dn, fl = L['breadth'].get(d, (None, None, None))

    return {
        'trade_date': d, 'sv': sv,
        'dev': dev,
        'reasons': reasons,
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
        'scope': BOARD_SCOPE,
        'amount_yi': amount_yi, 'amount_change_pct': chg,
    }


def distribution_note(rows: list) -> str:
    """D-43 的正文：**用修正后的实测分布**，并把修正前后的差异一起写清楚。

    这条曾经是"照搬优先级链的必然结果"的说明项。修正基期不改变它的结论
    （退潮仍占绝对多数、冰点仍可能一次都不出现），但数字必须换成当前库里的真值，
    否则一条勘误说明本身就成了过期数据。
    """
    def tally(key):
        out = {}
        for r in rows:
            out[r[key]] = out.get(r[key], 0) + 1
        return ' / '.join('%s %d' % (k, v) for k, v in sorted(out.items(), key=lambda x: -x[1]))

    risk = tally('risk_level')
    return ('阶段分布严重偏斜是**照搬优先级链的必然结果**，不是本实现算错：'
            '"退潮"排第一且条件含 `昨日炸板修复率<20`（而修复率低于 20 是常态），'
            '"冰点"虽列第二但要求 `最高板<=2 且涨停<20`，永远先被"退潮"截走。'
            '修正成交额环比基期后实测（%d 场）：stage_ui %s；stage_mcp %s；'
            'volume_state %s；risk %s。'
            '飞书版只给三条判据，故只出有限档位。前端不要按六档均匀出现来设计'
            % (len(rows), tally('stage_ui'), tally('stage_mcp'),
               tally('volume_state'), risk))


def recompute(dates, sv: str = None, progress=None) -> list:
    """逐日重算，返回 [{row, dev(json 字符串)}]。**不写库** —— 写不写由调用方决定。"""
    sv = sv or spec_version()
    out = []
    for i in range(0, len(dates), CHUNK):
        batch = dates[i:i + CHUNK]
        L = load(batch, sv)
        cal = calendar_offsets(batch)
        for d in batch:
            if d not in L['have_ladder']:
                continue
            out.append(compute(d, sv, L, cal[d]))
        if progress:
            progress(min(i + CHUNK, len(dates)), len(dates))
    note = distribution_note(out)
    for r in out:
        # 只换 note 正文，`code` 必须留着 —— 少了它这条就无法被页面和复核按码检索
        r['dev'] = json.dumps([{**x, 'note': note} if x['code'] == 'D-43' else x
                               for x in r['dev']], ensure_ascii=False)
        r['reasons'] = json.dumps(r['reasons'], ensure_ascii=False)
    return out
