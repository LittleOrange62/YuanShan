# -*- coding: utf-8 -*-
"""个股量化档案（`duanxian/stock_dossier.py`）的读数诚实性。

这一层**不重算任何判据**，所以单测钉的不是算得对不对，而是三件会骗人的事：

① **缺席的三种念法不许混**：查询失败要说失败（带异常原文）、榜没算到这天要说没算、
   算了但没进榜要说没进榜 —— 混成一句「没有数据」会让人去查回填，
   而实情可能是这只票真的不符合形态（或反过来）。
② **占位符个数与参数个数必须一致**。`_presence` 那一段 `own` 条件在 count 与 max 里
   各出现一次，少给一份 psycopg 抛 `IndexError`，而 `pg.fetch_df` 会把任何异常兜成空表 ⇒
   「这只票没进榜」与「库崩了」长成同一个样（本项目实测过这两条）。
③ **一块崩不带走整份档案**，崩了要把原文留在屏幕上。

`fake_q` 里那句 `sql.count('%s') == len(params)` 是**全局闸**：
任何一条查询少给或多给参数都会当场红，不用等真库。
"""
import json
import math

import pytest

from app.compute import ambush_setup as amb
from app.compute import breakout_signal as bs
from app.compute import confluence as CF
from app.compute import startup_volume as sv
from duanxian import pg, quant, stock_dossier as SD

DAY = '2026-10-12'
CODE = '000678.SZ'

# 一条 SQL 属于哪一格，按**先后顺序**认第一个命中的标记（先具体后笼统）。
MARKERS = ('own_rows', 'string_agg(quadrant', 'analytics.score_trend', 'from analytics.score',
           'rank() over (partition by energy_fp', 'analytics.volume_double',
           'analytics.startup_volume_profile', 'analytics.ambush_setup',
           'analytics.volume_breakout_signal', 'analytics.confluence_daily',
           'config.capability', 'reference_data.security', 'market_data.stock_daily',
           'analytics.sector_heat_daily', 'reference_data.sector_member')


def _marker(sql: str) -> str:
    return next((m for m in MARKERS if m in sql), 'other')


@pytest.fixture
def stubbed(monkeypatch):
    box = {'calls': [], 'data': {}, 'fail': set()}

    def fake_q(sql, params=()):
        for bad in box['fail']:
            if bad in sql:
                raise RuntimeError(f'库崩了：{bad}')
        box['calls'].append((sql, tuple(params)))
        assert sql.count('%s') == len(params), \
            f'占位符 {sql.count("%s")} 个、参数 {len(params)} 个：{sql[:90]}'
        key = _marker(sql)
        if 'own_rows' in sql:                      # 探查询恒有一行：给 box 里那份 presence
            return [box['data'].get('presence', {'own_rows': 0, 'own_latest': None,
                                                  'board_latest': DAY})]
        got = box['data'].get(key)
        if got is None:
            return []
        if isinstance(got, list):
            return list(got)
        return [got]

    monkeypatch.setattr(SD, '_q', fake_q)
    monkeypatch.setattr(SD, '_latest', lambda table, spec=None: DAY)
    monkeypatch.setattr(SD, '_labels', lambda: ({'Q1': '进攻', 'Q4': '回避'}, '测试'))
    monkeypatch.setattr(SD, '_coverage', lambda tables: {})
    monkeypatch.setattr(SD, '_cache_caveat', lambda keys: [])
    monkeypatch.setattr(SD, '_caveats', lambda *a, **k: [])
    monkeypatch.setattr(pg, 'enabled', lambda: True)
    box['data']['reference_data.security'] = [
        {'code': CODE, 'name': '测试股', 'board': '主板', 'is_st': False, 'list_date': '1997-01-01'}]
    box['data']['market_data.stock_daily'] = [
        {'d': DAY, 'close': 11.07, 'pct_change': -2.55, 'turnover_rate_percent': 15.78,
         'amount_yuan': 7.9e8}]
    box['data']['analytics.score_trend'] = [
        {'window_days': 10, 'point_count': 10, 'slope': -0.2, 'up_streak_days': 0,
         'first_quadrant': 'Q3', 'corr_coef': 0.9, 'as_of': DAY}]
    box['data']['from analytics.score'] = [
        {'capability_key': 'oper_score', 'value': -10.0, 'rank_in_market': 2297,
         'percent_rank': 58.7, 'quadrant': 'Q4', 'is_stale': False, 'as_of': DAY}]
    return box


def _ambush_row(**over):
    row = {'d': DAY, 'ambush_score': 62.5, 'ambush_types': ['obv_bid'], 'strict_pass': True,
           'ma_spread_pct': 7.98, 'weekly_streak': 0, 'weekly_ups6': 1, 'weekly_bars': 11,
           'week_partial': True, 'bars_60': 133, 'missing': None, 'rank_in_board': 1,
           'days_since_big': 18, 'close_hfq': 11.77, 'range20_pct': 26.76, 'box_pos_pct': 13.65,
           'dev_ma20_pct': 1.0, 'dev_ma60_pct': 2.0, 'trend60_pct': 3.0, 'shrink_ratio': 0.8,
           'obv_ratio': 1.1, 'hold_vs_big_pct': 4.0}
    row.update(over)
    return row


# ------------------------------------------------------------------ ① 缺席的三种念法

def test_没进榜说的是没进榜而不是没有这个形态(stubbed):
    stubbed['data']['presence'] = {'own_rows': 0, 'own_latest': None, 'board_latest': DAY}
    stubbed['data']['analytics.ambush_setup'] = []
    blk = SD._blk_ambush(CODE, DAY)
    assert blk['available'] is False
    assert blk['status'] == 'not_on_board'
    assert '它没进这一榜' in blk['reason']
    assert DAY in blk['reason'], '要说清这一榜算到哪天，否则读者无从判断是不是没回填'


def test_榜一场都没算要说没算过(stubbed):
    stubbed['data']['presence'] = {'own_rows': 0, 'own_latest': None, 'board_latest': None}
    blk = SD._blk_ambush(CODE, DAY)
    assert blk['status'] == 'not_computed'
    assert '一场都没有' in blk['reason']


def test_三种缺席各有各的词_不会读成同一句(stubbed):
    stubbed['data']['analytics.ambush_setup'] = []
    stubbed['data']['presence'] = {'own_rows': 0, 'own_latest': None, 'board_latest': DAY}
    on_board = SD._blk_ambush(CODE, DAY)
    stubbed['data']['presence'] = {'own_rows': 0, 'own_latest': None, 'board_latest': None}
    computed = SD._blk_ambush(CODE, DAY)
    stubbed['fail'].add('analytics.ambush_setup')
    try:
        failed = SD.dossier('000678', date=DAY)['blocks']['ambush']
    finally:
        stubbed['fail'].clear()
    assert failed['status'] == 'read_failed'
    words = {on_board['status'], computed['status'], failed['status']}
    assert words == {'not_on_board', 'not_computed', 'read_failed'}
    assert len({on_board['reason'], computed['reason'], failed['reason']}) == 3


def test_查询失败必须带出异常原文而不是空块(stubbed):
    stubbed['fail'].add('analytics.ambush_setup')
    got = SD.dossier('000678', date=DAY)
    blk = got['blocks']['ambush']
    assert blk['available'] is False and blk['status'] == 'read_failed'
    assert '库崩了' in blk['reason'] and 'RuntimeError' in blk['reason']
    # 一块崩不带走整份档案：其余照交，且那块崩的原因不是「没数据」的措辞
    assert got['available'] is True
    other = got['blocks']['volume_double']
    assert '库崩了' not in (other.get('reason') or ''), other
    assert 'dossier_blocks_missing' in [c['code'] for c in got['caveats']]


def test_代码不存在与库连不上是两句话(stubbed):
    stubbed['data']['reference_data.security'] = []
    got = SD.dossier('999999', date=DAY)
    assert got['available'] is False and got['status'] == 'unknown_code'

    stubbed['data']['reference_data.security'] = [
        {'code': CODE, 'name': '测试股', 'board': '主板', 'is_st': False, 'list_date': None}]
    stubbed['fail'].add('reference_data.security')
    got2 = SD.dossier('000678', date=DAY)
    assert got2['available'] is False and got2['status'] == 'read_failed'
    assert '没有数据' in got2['reason'], '连接失败要说清不是「没有数据」，否则会被读成回填缺失'


# ------------------------------------------------------------------ ② 参数与版本条件

def test_presence的占位符与参数一致(stubbed):
    SD._presence('analytics.ambush_setup', CODE, DAY, spec=amb.SPEC_VERSION)
    sql, params = stubbed['calls'][-1]
    # own 那三条条件在 count 与 max 里各出现一次（3+3），board 那两条出现一次（2）= 8
    assert sql.count('%s') == len(params) == 8, (sql.count('%s'), params)
    assert 'spec_version' in sql, \
        '探查询也要带版本条件，否则 board_latest 会挑到旧版本那一天的行'


def test_每一块都带自己的判据版本(stubbed):
    for key, mod in (('ambush', amb), ('breakout', bs), ('confluence', CF), ('startup', sv)):
        stubbed['calls'].clear()
        getattr(SD, f'_blk_{key}')(CODE, DAY)
        assert all('spec_version' in s for s, _p in stubbed['calls']), f'{key} 有条查询没带版本'
        assert any(mod.SPEC_VERSION in p for _s, p in stubbed['calls']), \
            f'{key} 没用 {mod.SPEC_VERSION}'


def test_日期列名不是_trade_date的那张表要指名(stubbed):
    SD._presence('analytics.startup_volume_profile', CODE, DAY,
                 spec=sv.SPEC_VERSION, date_col='launch_date')
    sql = stubbed['calls'][-1][0]
    assert 'launch_date <= %s' in sql and 'trade_date' not in sql, \
        'startup_volume_profile 没有 trade_date 列，拿它去问会 UndefinedColumn'


# ------------------------------------------------------------------ ③ 不重算、不自己拼

def test_八分量必须叫同一个函数(stubbed, monkeypatch):
    seen = {}

    def spy(code, date=''):
        seen['args'] = (code, date)
        return {'available': True, 'date': DAY, 'n_components': 2, 'caveats': [],
                'capabilities': [{'capability_key': 'oper_score', 'value': -10.0,
                                  'rank_in_market': 1, 'percent_rank': 50.0, 'quadrant': 'Q4',
                                  'is_stale': False, 'component_count': 2, 'weight_sum': 1.0,
                                  'weight_sum_ok': True, 'weight_mismatch': False,
                                  'components': [{'component_key': 'x', 'component_label': '形态',
                                                  'component_value': 1.0, 'weight': 0.4,
                                                  'contribution': 0.4}]}]}

    monkeypatch.setattr(quant, 'scores', spy)
    blk = SD._blk_scores(CODE, DAY)
    assert seen['args'] == (CODE, DAY), '档案不许自己另起一条取数路径'
    cap = blk['capabilities'][0]
    assert cap['component_count'] == 2 and cap['components'][0]['component_label'] == '形态'


def test_能力名称来自注册表而不是页面写死(stubbed, monkeypatch):
    """2026-10-22 起这一层的规矩变了：中文名由 `quant.scores()` 一并交出（真源
    `config.capability.name`，那边有它自己的用例钉着），档案只转述。
    所以这里钉两件事：① 名字照旧是注册表那一份而不是页面手抄；② 档案**不再自己查
    `config.capability`** —— 同一个名字两处各查一遍就是两份真相源，改名时必有一处慢半拍。
    """
    monkeypatch.setattr(quant, 'scores', lambda code, date='': {
        'available': True, 'date': DAY, 'caveats': [], 'n_components': 0,
        'names_source': 'config.capability（10 项）',
        'capabilities': [{'capability_key': 'oper_score', 'capability_name': '操作评分',
                          'capability_note': 'X/Y 加权', 'component_count': 0,
                          'rank_kind': 'ok', 'rank_note': '', 'rank_in_market': 7,
                          'percent_rank': 99.0, 'tie_at_value': 1, 'distinct_values': 5533,
                          'universe': 5568,
                          'components': [{'component_key': 'xingtai', 'component_name': '形态分',
                                          'component_label': 'xingtai_v1', 'component_value': 1.0,
                                          'weight': 0.25, 'contribution': 0.25}]}]})
    stubbed['calls'].clear()
    blk = SD._blk_scores(CODE, DAY)
    cap = blk['capabilities'][0]
    assert cap['capability_label'] == '操作评分' and cap['capability_note'] == 'X/Y 加权'
    assert cap['components'][0]['component_label'] == '形态分', '分量名也走同一份出处'
    assert blk['names_source'] == 'config.capability（10 项）'
    assert not any('config.capability' in s for s, _p in stubbed['calls']), \
        '档案又自己查了一遍能力名 —— 那就是第二份出处'
    # 名次那一档的判据也原样带走，档案不自己判"能不能排名次"
    assert cap['rank_kind'] == 'ok' and cap['tie_at_value'] == 1 and cap['universe'] == 5568


def test_评分那块不可用时档案不假装成功(stubbed, monkeypatch):
    monkeypatch.setattr(quant, 'scores', lambda code, date='': {
        'available': False, 'reason': '该场没有这只票的 capability 评分'})
    blk = SD._blk_scores(CODE, DAY)
    assert blk['available'] is False and 'capability' in blk['reason']


def test_概念那一串是顿号串_必须切成数组再进_sql(stubbed, monkeypatch):
    monkeypatch.setattr(SD.pg_sources, 'concepts_by_code', lambda codes: {CODE[:6]: {
        'concepts': '农机、减速器、军民融合', 'concept_count': 3}})
    SD._blk_sectors(CODE, DAY)
    lists = [p for _s, p in stubbed['calls'] if any(isinstance(x, list) for x in p)]
    assert lists, '概念名没有作为数组参数进查询（整串塞进 unnest 会 malformed array literal）'
    assert lists[-1][-1] == ['农机', '减速器', '军民融合']


def test_同名多条板块码要先取当天有热度记录的那条(stubbed, monkeypatch):
    """实测 000678 的「军民融合」挂三条码：THS_D4F4（245 只、当天没有热度行）、
    TDX_880950（195 只、有热度行）、GN_880950（0 只）。按成员最多挑会挑到没热度那条，
    屏幕上就是一格「—」—— 那不等于「这个概念不热」，是查错了码。"""
    monkeypatch.setattr(SD.pg_sources, 'concepts_by_code', lambda codes: {CODE[:6]: {
        'concepts': '军民融合', 'concept_count': 1}})
    SD._blk_sectors(CODE, DAY)
    sql = next(s for s, _p in stubbed['calls'] if "'概念'" in s)
    prio = sql.index("row_number() over (partition by i.name order by")
    assert 'h.sector_code is null then 0 else 1 end) desc' in sql[prio:prio + 420], \
        '没先按「当天有没有热度行」排，就会拿空码去报 null'
    assert 'codes' in sql, '同名几条码的条数要一起交出，否则读者不知道这是在多条里挑的一条'


def test_数组列是_list时不能被当串切(stubbed):
    assert SD._as_list(['voldbl', 'dgt']) == ['voldbl', 'dgt']
    assert SD._as_list('voldbl,dgt') == ['voldbl', 'dgt']
    assert SD._as_list(None) == []
    # 这条是防回归：拿「按逗号 split 串」的那套硬套 list，会切出带方括号的脏元素
    assert all(x in ('a', 'b') for x in SD._as_list(['a', 'b']))


def test_覆盖的屏与实际交出的块一致():
    keys = {k for k, _l, _b, _t in SD.BLOCKS}
    assert keys == set(SD.BLOCK_FNS), '声称覆盖八块却只算七块，屏幕上那一块会永远空着'


# ------------------------------------------------------------------ 出口形状

def test_nan_出口必须是_none(stubbed):
    stubbed['data']['analytics.ambush_setup'] = [_ambush_row(ambush_score=float('nan'))]
    blk = SD._blk_ambush(CODE, DAY)
    assert blk['available'] is True
    assert blk['latest']['ambush_score'] is None, 'NaN 折成 0 会被读成「零分」'
    assert blk['latest']['ambush_type_labels'] == [amb.TYPE_LABELS['obv_bid']]
    assert 'nan' not in json.dumps(blk, allow_nan=False, default=str).lower()


def test_整份响应体能过_allow_nan_false(stubbed):
    stubbed['data'].update({
        'analytics.volume_double': [{'d': DAY, 'dbl_energy': 33.1, 'dbl_count': 8,
                                     'dbl_prev_count': 4,
                                     'cross_count': 7, 'dev_med': 4.9, 'dev_max': 9.1,
                                     'above_ratio': 67.5, 'ma60_chg': -6.29, 'bars': 40,
                                     'ma60_days': 40, 'close_last': 11.07, 'ma60_last': 11.6}],
        'rank() over (partition by energy_fp': {'rk': 12, 'uni': 5100, 'dbl_energy': 33.1},
        'analytics.startup_volume_profile': [{'launch_date': '2026-09-24', 'cohort': 'board4',
                                              'board': '主板', 'equiv_limit_ups': 4.0,
                                              'trigger_len': 4, 'trigger_gain': 0.46,
                                              'window_days': 30, 'bars_used': 30, 'up_days': 9,
                                              'down_days': 12, 'flat_days': 9, 'dbl_days': 2,
                                              'near_dbl_days': 1, 'calm_days': 3, 'still_days': 2,
                                              'ratio_avg': 0.9, 'ratio_median': 0.85,
                                              'ratio_p90': 1.2, 'ratio_max': 2.0, 'ratio_min': 0.4,
                                              'std_days': 1}],
        'analytics.ambush_setup': [_ambush_row()],
        'analytics.volume_breakout_signal': [
            {'d': DAY, 'signal_type': 'surge_after_shrink', 'last_surge_date': '2026-09-01',
             'surge_vol_ratio': 2.1, 'shrink_days': 6, 'converge_days': 8,
             'break_vol_ratio': 1.8, 'break_dev': 1.2, 'break_close': 11.0, 'break_ma60': 10.9,
             'window_days': 60, 'bars_used': 60}],
        'analytics.confluence_daily': {'d': DAY, 'hits': 2, 'hit_sources': 'voldbl,dgt',
                                       'ranks': '{"voldbl": 6}', 'sizes': '{"voldbl": 150}',
                                       'best_source': 'voldbl', 'best_rank': 6,
                                       'best_rank_pct': 4.0, 'source_count': 5,
                                       'sources_absent': None},
        'analytics.sector_member': [{'sector_code': 'TDX_1', 'name': '测试行业',
                                     'sector_type': '行业', 'heat': 1.0, 'heat_rank': 5,
                                     'uni': 983, 'part_count': 4}],
        'config.capability': [{'capability_key': 'oper_score', 'name': '操作评分',
                               'description': None}],
    })
    got = SD.dossier('000678', date=DAY)
    text = json.dumps(got, ensure_ascii=False, allow_nan=False, default=str)
    assert 'NaN' not in text and 'Infinity' not in text
    assert set(got['blocks']) == {k for k, _l, _b, _t in SD.BLOCKS}
    assert got['boards_covered'] and got['not_covered'], '没覆盖哪几块也要交给屏幕'
    assert got['blocks']['confluence']['hits'] == 2
    assert got['blocks']['confluence']['ranks'] == {'voldbl': 6}


# ------------------------------------------------------------------ 真库（一条都不打桩）

@pytest.mark.skipif(not pg.enabled(), reason='需要本地库')
def test_真库跑一只票的档案():
    got = SD.dossier('000678')
    assert got.get('available') is True, got
    assert got['code'] == '000678.SZ'
    assert set(got['blocks']) == {k for k, _l, _b, _t in SD.BLOCKS}
    q = got['quote']
    assert q['close'] is None or math.isfinite(q['close'])
    for key, blk in got['blocks'].items():
        if blk.get('available'):
            assert blk.get('as_of'), f'{key} 报可用却没说自己是哪一天的数'
        else:
            assert blk.get('status') in ('read_failed', 'not_computed', 'not_on_board'), \
                f'{key} 的缺席没定成三种之一：{blk}'
            assert blk.get('reason'), f'{key} 不可用却没说为什么'
    json.dumps(got, ensure_ascii=False, allow_nan=False)
