# -*- coding: utf-8 -*-
"""名次稳步前进榜 · 后续表现（`duanxian/momentum_follow.py`，端点 `/api/quant/momentum-follow`）。

用户 2026-10-20 凌晨点名：「昨日前 80 名的进入股价表现（把各种名次都显示出来，包括最近倍量名次，
突破信号等等）」。已确认口径：母体＝**上一场**那张稳步前进榜的前 N 名（同档 metric），
表现窗口＝「今日涨跌 + 近 5 日涨跌」两栏（不逐日全铺）。

2026-10-27 用户点名的两处改名（这一份测试跟着重钉）：
 · 「次日涨跌」这名字是错的 —— 母体默认是**前一场**，它之后的第一场行情就是今天，
   所以屏上念「今日涨跌」；键名保留 `chg_next_day`（＝基准后第一场），因为把基准日指到更早那天时
   它确实不是今天，那一格靠「期间场数」自证。
 · 「至今累计」撤下：母体是前一场时它与「今日涨跌」是同一个数（只隔一场），摆两列是假的两把尺。
   换成 `chg_5d`＝读至日往回 5 个**市场场次**的累计涨跌幅，**不以母体日为起点**（两列不许相减），
   出口与首板分析屏的「近 5 场」同一份 `day_returns.returns_by_code(span=5)`。

钉住的七件最容易做错、做错了也不会报错的事：

① **母体取的是上一场那张榜**（库里最新有名次场次的前一场），不是最新一场 ——
   传错日期整屏的「今日」就落在不存在/错误的场次上。
② **metric 坏值走母体的 `_reject`**（「只收 xy、oper」那句原话透传），
   不许被 `_fail` 的「这一场没有数据」冒充 —— 参数不对与库里没这场是两句话。
③ **缺今日一律交 null 且带原因码**（停牌在今日、母体之后整段没行、库里还没下一场，三种各有码），
   界面 — 而不是 0%。
④ **两栏各有自己的原因码**（`perf_missing_reason` / `perf5_missing_reason`）：近 5 日不依赖母体日，
   「今日是 — 而近 5 日有数」与反过来都是常态，拿一栏的话去解释另一栏就是屏幕说假话。
⑤ **名次文案就是 `duanxian/ranking.py` 的 `RULE_NOTE` 那个对象本身**（`is` 比的是对象身份），
   出现第二份抄本这条就红。
⑥ **每只票的「今日」是母体场次之后的第一场**：series 首行日期不等于那一场 ⇒ 不许拿后面那场
   冒充（停牌不桥接，`day_returns` 那条纪律在行上的落点）。
⑦ 母体判据零抄本：本模块必须**调用** `quant.rank_momentum`，文件里不许出现第二份稳步前进 SQL。

最后一条**不打桩**（真库现跑；`pg.enabled()` 不通才 skip），照 `tests/test_ranking_shared.py`
最后那条的写法：桩绿不代表接线对。
"""
import json
import pathlib

import pytest

from duanxian import momentum_follow as MF
from duanxian import pg
from duanxian import quant as Q
from duanxian import ranking

REAL_RANK_MOMENTUM = Q.rank_momentum    # fixture 会把它换成桩；坏值那条要还原真身

PARENT_DATE = '2026-10-16'
NEXT_DAY = '2026-10-19'
TO_DATE = '2026-10-21'

COUNTS = {'dbl_rows': 5527, 'dbl_scored': 5517, 'oper_scored': 5563,
          'breakout_rows': 4246, 'ambush_rows': 238, 'abnormal_rows': 421, 'trend_rows': 5215}


def _parent_rows():
    return [
        {'security_code': '600295.SH', 'name': '鄂尔多斯', 'board': '主板', 'board_rank': 1,
         'first_series': 3454, 'latest_series': 181, 'gain_abs': 3273, 'gain_frac': 94.8,
         'quadrant': 'Q1', 'quadrant_label': '买入', 'oper_value': 32.04, 'matched_total': 372},
        {'security_code': '000001.SZ', 'name': '平安银行', 'board': '主板', 'board_rank': 2,
         'first_series': 486, 'latest_series': 15, 'gain_abs': 471, 'gain_frac': 96.9,
         'quadrant': 'Q2', 'quadrant_label': '质量待确认', 'oper_value': 21.5, 'matched_total': 372},
    ]


def _fake_parent(metric='xy', limit=80, date='', refresh=False):
    return {'available': True, 'metric': metric, 'metric_label': 'X/Y 两轴合名次',
            'series_label': 'X+Y 两轴合名次', 'limit': min(int(limit or 80), 200),
            'total_matched': 372, 'rule': '名次稳步前进榜（母体判据原句）',
            'window': {'sessions': 6, 'steps': 5, 'first_day': '2026-10-09',
                       'last_day': date or PARENT_DATE},
            'thresholds': {'min_better_steps': 3, 'max_worse_steps': 1,
                           'min_gain_frac_pct': 10.0, 'require_both_axes': True},
            'caveats': [{'code': 'board_truncated',
                         'text': '符合判据的共 372 只，接口只交前 80 只 —— 榜被上限截断过。'},
                        {'code': 'y_axis_levels', 'text': 'Y 轴名次的分辨率声明（母体原话）。'}],
            'rows': _parent_rows()}


def _one_dispatch(sql):
    s = ' '.join(str(sql).split())
    if 'min(trade_date)::text d from ( select distinct trade_date from analytics.score' in s:
        return {'d': PARENT_DATE}                     # 最新两场的**较早一场**（母体默认基准日）
    if 'min(trade_date)::text d from market_data.stock_daily where trade_date >' in s:
        return {'d': NEXT_DAY}                        # 次日＝母体之后的第一场
    if 'max(trade_date)::text d from market_data.stock_daily' in s:
        return {'d': TO_DATE}                         # 读至日（最新一场行情）
    if 'count(*)::int n from ( select distinct trade_date from market_data.stock_daily' in s:
        return {'n': 2}
    if 'dbl_rows' in s:
        return dict(COUNTS)
    return {}


def _rows_dispatch(sql):
    s = ' '.join(str(sql).split())
    if 'analytics.volume_double v' in s:
        return [{'code': '600295.SH', 'dbl_energy': 5.85, 'dbl_rank': 962}]     # 000001 那天没行
    if 'from analytics.score' in s:
        return [{'code': '600295.SH', 'rank_in_market': 25},
                {'code': '000001.SZ', 'rank_in_market': 480}]
    if 'volume_breakout_signal' in s:
        return [{'code': '600295.SH', 'signal_type': 'surge_after_shrink'}]
    if 'analytics.ambush_setup' in s:
        return [{'code': '000001.SZ', 'ambush_types': ['pullback_ma20'],
                 'ambush_score': 61.5, 'rank_in_board': 7}]
    if 'abnormal_move_daily' in s:
        return [{'code': '000001.SZ', 'universe_by': 'sealed10,dev10_ge25'}]
    if 'trend_stock_daily' in s:
        return [{'code': '600295.SH', 'selected5': True, 'selected10': False},
                {'code': '000001.SZ', 'selected5': False, 'selected10': False}]
    return []


def _series_ok(spans, target_date):
    """600295 正常（今日有行）；000001 **今日停牌**（首行是再下一场）——不许桥接。"""
    assert target_date == TO_DATE, '读至日必须是 market_data.stock_daily 的最新一场'
    assert set(spans) == {'600295.SH', '000001.SZ'}
    assert all(str(v) == NEXT_DAY for v in spans.values()), \
        '每只票的起算必须统一是母体之后的第一场（spans 的值就是要找的「今日」）'
    return {
        '600295.SH': {'days': [{'day_no': 0, 'trade_date': NEXT_DAY, 'pct': 2.31, 'cum': 2.31},
                               {'day_no': 1, 'trade_date': TO_DATE, 'pct': 1.0, 'cum': 3.33}],
                      'base_day': PARENT_DATE, 'missing_base': False},
        '000001.SZ': {'days': [{'day_no': 0, 'trade_date': '2026-10-20', 'pct': -1.2, 'cum': -1.2}],
                      'base_day': '2026-10-19', 'missing_base': False},
    }


def _perf5_ok(codes, target_date, span):
    """近 5 场那一发：以**读至日**为终点往回数，与母体日无关。

    `day_returns` 的窗口是「读至日 + 往前 5 场」＝**满档 6 根**（基期那根也在窗口里）。
    000001 只给 4 根（新股/停牌凑不满）——`bars_5d` 必须把这件事交到行上，
    界面才会念成「4 根里累计」而不是「6 场里累计」。
    """
    assert target_date == TO_DATE, '近 5 日的终点必须是读至日，不是母体日、也不是今天'
    assert int(span) == MF.PERF_SPAN, f'窗口档位只住 MF.PERF_SPAN 一处，桩跟着它走（实得 {span}）'
    assert set(codes) == {'600295.SH', '000001.SZ'}
    return {
        '600295.SH': {'chg_today': 1.0, 'chg_span': 6.2, 'bars': 6,
                      'base_day': '2026-10-13', 'has_row_on_target': True},
        '000001.SZ': {'chg_today': None, 'chg_span': -3.1, 'bars': 4,
                      'base_day': '2026-10-14', 'has_row_on_target': True},
    }


@pytest.fixture
def stub(monkeypatch):
    seen = {'rank_momentum': [], 'series_calls': 0, 'perf5_calls': 0}

    def fake_rank_momentum(metric='xy', limit=80, date='', refresh=False):
        seen['rank_momentum'].append({'metric': metric, 'limit': limit, 'date': date})
        return _fake_parent(metric=metric, limit=limit, date=date)

    monkeypatch.setattr(Q, 'rank_momentum', fake_rank_momentum)
    monkeypatch.setattr(Q, '_one', lambda sql, params=(): _one_dispatch(sql))
    monkeypatch.setattr(Q, '_rows', lambda sql, params=(): _rows_dispatch(sql))
    monkeypatch.setattr(Q, 'clear_agg_cache', lambda prefix='': 0)
    monkeypatch.setattr(Q.venc, 'fingerprint', lambda *a, **k: 'ev-testfp')
    monkeypatch.setattr(Q, '_coverage', lambda tables: {t: {'from': None, 'to': None, 'sessions': None}
                                                        for t in tables})
    monkeypatch.setattr(Q, '_cache_caveat', lambda keys: [])

    def fake_series(spans, target_date):
        seen['series_calls'] += 1
        return _series_ok(spans, target_date)

    def fake_perf5(codes, target_date, span):
        seen['perf5_calls'] += 1
        return _perf5_ok(codes, target_date, span)

    monkeypatch.setattr(MF.day_returns, 'series_by_code', fake_series)
    monkeypatch.setattr(MF.day_returns, 'returns_by_code', fake_perf5)
    return seen


def test_母体取的是上一场那张榜_不是最新场(stub):
    d = MF.board(metric='xy', limit=80)
    assert d['available'], d.get('reason')
    calls = stub['rank_momentum']
    assert len(calls) == 1
    # 库里最新一场是 2026-10-19（桩里 min-of-top-2 交回较早的那场 10-16）：
    # 母体必须问 10-16 那张榜，不是 10-19
    assert calls[0]['date'] == PARENT_DATE != '2026-10-19', \
        '母体基准日必须是「上一场」（要有次日才算得出后续表现）'
    assert d['parent_date'] == PARENT_DATE and d['window']['next_day'] == NEXT_DAY
    assert d['date'] == TO_DATE, 'date 说的是「读到哪一场」（至今），与 parent_date 分开'
    assert d['metric'] == calls[0]['metric'] == 'xy'
    assert d['limit'] == 80 and d['page_size'] == MF.PAGE_SIZE
    assert len(d['rows']) == 2 and d['total_matched'] == 372


def test_metric坏值走母体的_reject_不是fail那族(stub, monkeypatch):
    """拒答那句必须**来自 rank_momentum 的 `_reject`**（本模块不抄第二份文案），
    而且不能被 `_fail` 的「这一场没有数据」冒充。用真实 rank_momentum（只桩取数层）。"""
    monkeypatch.setattr(Q, 'rank_momentum', REAL_RANK_MOMENTUM)  # 还原真实实现（只桩取数层）
    monkeypatch.setattr(Q, '_agg', lambda key, sql, params=(), many=False:
                        {'sessions': 6, 'first_day': '2026-10-09', 'last_day': PARENT_DATE,
                         'universe_min': 5558, 'universe_max': 5563, 'stale_rows': 0,
                         'y_levels_min': 41, 'y_levels_max': 41, 'x_levels_min': 5535,
                         'x_levels_max': 5552, 'biggest_y_tie': 755,
                         'breadth_missing': 0, 'breadth_present': 6})
    monkeypatch.setattr(Q, '_latest', lambda table, spec=None: PARENT_DATE)
    monkeypatch.setattr(Q.pg, 'session_has', lambda table, d: True)
    d = MF.board(metric='score', limit=80)
    assert d['available'] is False
    reason = str(d.get('reason') or '')
    assert '只收' in reason and 'xy' in reason and 'oper' in reason and '收到「score」' in reason, \
        '必须透传 _reject 那句「列出能收的两档」的原话'
    assert '这一场没有数据' not in reason and '连不上' not in reason, \
        '参数不对不许借「库里没这场」那句 —— 读起来完全不同'
    assert stub['series_calls'] == 0 and stub['perf5_calls'] == 0, '被拒之后不该再发任何表现侧的取数'


def test_缺今日一律null并带原因码_不许当0(stub):
    d = MF.board(metric='xy')
    r1, r2 = d['rows']
    assert r1['chg_next_day'] == 2.31 and r1['bars_after'] == 2
    # 000001 今日（10-19）停牌、首行落在 10-20：不许拿 10-20 冒充「今日」
    assert r2['chg_next_day'] is None
    assert r2['perf_missing_reason'] == 'no_row_on_next_day'
    assert r2['perf_missing_note'], '原因码必须配那句完整的话（只住本模块一处）'
    # 近 5 日那一栏独立成立：它看读至日往回 5 场，与今日停不停牌无关
    assert r2['chg_5d'] == -3.1 and r2['bars_5d'] == 4 and r2['base_5d_day'] == '2026-10-14'
    assert r2['perf5_missing_reason'] is None, '今日是 — 而近 5 日有数是常态，不许串用同一个原因码'
    # 窗口不足满档（读至日 + 往前 5 场＝6 根）要交出来：4 根就是「4 根里累计」，不是「6 场里累计」
    assert r1['bars_5d'] == MF.PERF_SPAN + 1, '满档是 6 根（基期那根也在窗口里），桩与真库同形'
    # 倍量：000001 那天 volume_double 没行 → null + no_row，不是 0 分也不是「没进过倍量榜」
    assert r1['dbl_rank'] == 962 and r1['dbl_energy'] == 5.85
    assert r2['dbl_rank'] is None and r2['dbl_missing_reason'] == 'no_row'


def test_missing_base_交null并归因到基期(stub, monkeypatch):
    def fake_series(spans, target_date):
        return {'600295.SH': {'days': [{'day_no': 0, 'trade_date': NEXT_DAY, 'pct': None, 'cum': None}],
                              'base_day': None, 'missing_base': True},
                '000001.SZ': {'days': [], 'base_day': None, 'missing_base': True}}
    monkeypatch.setattr(MF.day_returns, 'series_by_code', fake_series)
    d = MF.board(metric='xy')
    r1, r2 = d['rows']
    assert r1['chg_next_day'] is None and r1['chg_5d'] == 6.2
    assert r1['perf_missing_reason'] == 'missing_base', '今日有行但没有基期 ⇒ 归因到基期，不怪停牌'
    assert r2['perf_missing_reason'] == 'no_price_after', '母体之后整段没行是另一种话'
    assert r1['perf_missing_note'] == MF._PERF_NOTES['missing_base'], '话与码一一对应，两码两话'


def test_近5日自己缺数时用的是另一套原因码(stub, monkeypatch):
    """两栏各有各的话：`missing_base5` 说的是「往前第 5 场那一场没有它的行」，
    拿今日那一栏的 `missing_base`（母体日没行）去解释它就是屏幕说假话。"""
    def fake_perf5(codes, target_date, span):
        # 600295：基期那场没行；000001：**整个窗口里一根日线都没有**（map 里没这个键）
        return {'600295.SH': {'chg_span': None, 'bars': 3, 'base_day': None}}
    monkeypatch.setattr(MF.day_returns, 'returns_by_code', fake_perf5)
    d = MF.board(metric='xy')
    r1, r2 = d['rows']
    assert r1['chg_next_day'] == 2.31, '今日那一栏照旧有数 —— 两栏互不牵连'
    assert r1['chg_5d'] is None and r1['bars_5d'] == 3
    assert r1['perf5_missing_reason'] == 'missing_base5'
    assert r1['perf5_missing_note'] == MF._PERF_NOTES['missing_base5']
    assert r2['perf5_missing_reason'] == 'no_bars_in_window', '窗口里一根都没有是另一种话'
    assert r2['bars_5d'] is None and r2['base_5d_day'] is None
    assert r2['perf_missing_reason'] == 'no_row_on_next_day', \
        '今日那一栏的原因码来自它自己那一发（000001 今日停牌），近 5 日没数不许改写它'


def test_读至日那天停牌时近5日说的是第三句话(stub, monkeypatch):
    """基期有、读至日没行 ⇒ 累计没有落点。不许把它念成「缺基期」，也不许拿前面那场冒充终点。"""
    monkeypatch.setattr(MF.day_returns, 'returns_by_code',
                        lambda codes, t, span: {'600295.SH': {'chg_span': None, 'bars': 5,
                                                              'base_day': '2026-10-13'},
                                                '000001.SZ': {'chg_span': 1.5, 'bars': 6,
                                                              'base_day': '2026-10-13'}})
    d = MF.board(metric='xy')
    r1, r2 = d['rows']
    assert r1['perf5_missing_reason'] == 'no_row_on_to_date'
    assert r1['perf5_missing_note'] == MF._PERF_NOTES['no_row_on_to_date']
    assert r2['chg_5d'] == 1.5 and r2['bars_5d'] == 6 and r2['perf5_missing_reason'] is None


def test_近5日整发没读回来时点名成硬声明(stub, monkeypatch):
    """`{}` 是「这一发查询失败」，与「这些票五日内都没动」是两句话 —— 后者会被读成实质读数。"""
    calls = []

    def unread(codes, target_date, span):
        calls.append((tuple(sorted(codes)), target_date, span))
        return {}
    monkeypatch.setattr(MF.day_returns, 'returns_by_code', unread)
    d = MF.board(metric='xy')
    assert all(r['chg_5d'] is None and r['perf5_missing_reason'] == 'perf5_unread' for r in d['rows'])
    hard = [c for c in d['caveats'] if c['code'] == 'perf5_unread']
    assert hard and hard[0].get('hard'), '整列没读到＝硬声明，不许折进普通说明框里静音'
    assert len(calls) == 1 and calls[0][2] == MF.PERF_SPAN, '一只票一问不许，整批发一次'


def test_库里没有下一场时今日整栏null但近5日照常有数(stub, monkeypatch):
    monkeypatch.setattr(Q, '_one', lambda sql, params=(): (
        {'d': None} if 'min(trade_date)::text d from market_data.stock_daily where trade_date >'
        in ' '.join(str(sql).split()) else _one_dispatch(sql)))
    d = MF.board(metric='xy')
    assert d['window']['next_day'] is None
    assert all(x['chg_next_day'] is None for x in d['rows'])
    assert all(x['perf_missing_reason'] == 'no_next_session' for x in d['rows'])
    assert any(c['code'] == 'no_next_session' for c in d['caveats'])
    # 近 5 日以读至日为终点、不以母体日为起点 ⇒ 母体之后一场都没有时它照样算得出来
    assert [x['chg_5d'] for x in d['rows']] == [6.2, -3.1]
    assert all(x['perf5_missing_reason'] is None for x in d['rows'])


def test_母体之后只有一场时声明两列不许相减(stub, monkeypatch):
    """基准日=前一场 ⇒ 常态就是「只隔一场」，此时「今日」正好是近 5 日窗口的最后一根。
    两列起点不同，减出来的东西不存在。"""
    monkeypatch.setattr(Q, '_one', lambda sql, params=(): (
        {'n': 1} if 'count(*)::int n from ( select distinct trade_date from market_data.stock_daily'
        in ' '.join(str(sql).split()) else _one_dispatch(sql)))
    d = MF.board(metric='xy')
    assert any(c['code'] == 'today_is_last_day_of_window' and '不许把两列相减' in c['text']
               for c in d['caveats'])


def test_名次文案就是ranking那一个对象(stub):
    d = MF.board(metric='xy')
    assert d['rank']['rule'] is ranking.RULE_NOTE, '抄本（哪怕逐字相同）早晚走偏 —— 要比对象身份'
    assert d['rank']['rule_short'] is ranking.RULE_NOTE_SHORT
    assert d['rank']['universe']['scored'] == COUNTS['oper_scored']
    assert '榜内' in d['rank']['universe']['note'] and '全场' in d['rank']['universe']['note'], \
        '埋伏的榜内名次与全场名次是两把尺，universe 那句必须点破'


def test_各路名次与在榜按母体场次各取各的数(stub):
    d = MF.board(metric='xy')
    r1, r2 = d['rows']
    assert r1['oper_rank'] == 25 and r2['oper_rank'] == 480
    assert r1['breakout_type'] == 'surge_after_shrink' and r1['breakout_type_label'] == '放量后缩量'
    assert r2['breakout_type'] is None and r2['breakout_type_label'] is None
    assert r2['ambush_rank_in_board'] == 7 and r2['ambush_score'] == 61.5
    assert '回踩 MA20' in r2['ambush_type_labels']
    # 异动：abnormal_hit 三态 —— 那天有榜（421 行）而 600295 没进 ⇒ **false 是实质读数**
    assert r1['abnormal_hit'] is False and r2['abnormal_hit'] is True
    assert r2['abnormal_universe_by_label'] == '近 10 场封过板、10 日累计偏离 ≥25 点'
    assert r1['trend_selected5'] is True and r1['trend_selected10'] is False


def test_母体声明原样带出_definitions与行字段一一对应(stub):
    d = MF.board(metric='xy')
    codes = {c['code'] for c in d['caveats']}
    assert 'board_truncated' in codes and 'follow_scope' in codes and 'dash_not_zero' in codes
    keys = set()
    for r in d['rows']:
        keys |= set(r)
    assert keys == set(d['definitions']), '少一个键＝那一格上屏没有出处；多一个＝解释对着不存在的字段'
    src = pathlib.Path('duanxian/momentum_follow.py').read_text(encoding='utf-8')
    assert 'STEADY_MIN_BETTER' not in src and 'better_steps >= ' not in src, \
        '母体判据只许调用 rank_momentum，不许出现第二份稳步前进 SQL'


# ---------------------------------------------------------------- 上屏文字的闸


def _all_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _all_strings(v)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from _all_strings(v)


class TestScreenTextHygiene:
    """这块的说明框/悬停/caveat 全是**纯文本渲染**，星号没有任何一层会剥掉。

    10-21 真踩过（首页那一屏念出 `**分位是相对量、不是概率**`），所以这里不看判据，
    只看「念出来的样子」：把 stub 那一份完整 payload 里每一个字符串都过一遍。
    """

    def test_整份payload没有裸星号与ASCII双引号(self, stub):
        d = MF.board(metric='xy')
        assert d['available'], d.get('reason')
        bad = [s[:60] for s in _all_strings(d) if '**' in s or '"' in s]
        assert not bad, f'这些上屏文字里有星号或 ASCII 双引号：{bad}'

    def test_两栏的名字在后端出处也念今日与近5日(self, stub):
        """口径改了词必须跟着改（10-19「加权分达标后的逐日涨跌幅——这是什么玩意？」同一条纪律）：
        「次日」「至今」那两种旧念法不许当**列名**出现在说明里 —— 只有在解释「为什么改名」时
        允许提一次旧名，所以判据落在第一句（列名那一句）上。"""
        d = MF.board(metric='xy')
        defs = d['definitions']
        assert '今日' in defs['chg_next_day'], defs['chg_next_day'][:60]
        assert '近 5 日' in defs['chg_5d'] and '它的起点不是母体日' in defs['chg_5d']
        assert '不许相减' in defs['chg_5d'], '两栏不是同一段区间这件事要写在出处里，不只写在悬停'
        assert 'chg_to_date' not in defs and 'to_date_day' not in defs, \
            '作废那一栏的解释还留着就是对着不存在的字段说话'
        stale = [k for k, v in defs.items()
                 if '次日' in str(v).split('（')[0] or '至今' in str(v).split('（')[0]]
        assert not stale, f'这些列的名字那一句还念旧名字：{stale}'


# ---------------------------------------------------------------- 不打桩：真库

def test_真库_母体是上一场且两栏涨跌幅各归各():
    if not pg.enabled():
        pytest.skip('本机库没通，这条要真库')
    probe = pg.fetch_df("""select min(trade_date)::text a, max(trade_date)::text b from (
                             select distinct trade_date from analytics.score
                              where capability_key = 'oper_score' and rank_in_market is not null
                              order by trade_date desc limit 2) x""")
    if probe is None or probe.empty:
        pytest.skip('本机库读不到有名次的场次')
    expect_parent = str(probe.iloc[0]['a'])     # 最新两场里较早的一场 = 上一场
    d = MF.board(metric='xy', limit=80)
    if not d.get('available'):
        pytest.skip(f'本机库这一场不可读：{d.get("reason")}')
    assert d['parent_date'] == expect_parent, '母体必须取上一场那张榜（不是最新场）'
    assert d['rows'], '近 6 场稳步前进的票真库里不可能 0 只（10-19 实测 372 只符合）'
    assert len(d['rows']) <= 80 and d['total_matched'] >= len(d['rows'])
    for r in d['rows']:
        assert isinstance(r['gain_frac'], float)
        assert r['chg_next_day'] is None or isinstance(r['chg_next_day'], float), '缺数是 None，不是 0'
        assert r['chg_5d'] is None or isinstance(r['chg_5d'], float)
        if r['perf_missing_reason'] is not None:
            assert r['perf_missing_reason'] in MF._PERF_NOTES
            assert r['chg_next_day'] is None, f'{r["code"]} 带今日的原因码却有今日的数 —— 话与数对不上'
        if r['perf5_missing_reason'] is not None:
            assert r['perf5_missing_reason'] in MF._PERF_NOTES
            assert r['chg_5d'] is None, f'{r["code"]} 带近 5 日的原因码却有近 5 日的数'
        else:
            assert r['chg_5d'] is not None
            # 「近 5 日」是按市场场次数的：窗口含基期最多 6 根，不足时必须说实话
            assert 1 <= (r['bars_5d'] or 0) <= MF.PERF_SPAN + 1
    assert d['rank']['rule'] is ranking.RULE_NOTE
    assert d['rank']['rule_short'] is ranking.RULE_NOTE_SHORT
    assert d['indicator_counts']['oper_scored'] > 5000
    keys = set()
    for r in d['rows']:
        keys |= set(r)
    assert keys == set(d['definitions'])
    # 出口清洗：整份 payload 必须能被严格 JSON 序列化（NaN/Infinity 会当场炸 json.dumps）
    json.dumps(d, ensure_ascii=False, allow_nan=False)
