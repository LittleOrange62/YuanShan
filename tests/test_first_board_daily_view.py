# -*- coding: utf-8 -*-
"""首页「首板分析」读侧（`duanxian/first_board_daily.py`）的闸。

钉的四件事都对应一类会"看着正常、其实是假数"的形状：

1. **不可用不带 `rows` 键**：表没跑过 / 库不通时必须交 `{available:false, reason}`，
   界面才会念"这一族还没算过"而不是"今天没有首板"。
2. **全场聚合不随筛参变**：`shape_tally`/`reason_tally`/`first_count`/`universe` 永远按这一场
   全部首板算 —— 筛了一档之后每档那个数若跟着变小，界面就在说"全场只剩这些"。
3. **非法形态档出声退回**：不能静默当成"筛出 0 只"（同筛选器那条 `long_dir` 假可用的教训）。
4. **屏幕上念出来的句子没有 markdown 星号、没有 ASCII 双引号**（全站一条规矩，用「」）；
   聚合读不到交 None→caveat，不兜成空表（空表与"今天没有"在屏幕上长得一样）。
"""
from __future__ import annotations

import json
import re

import pytest

from duanxian import first_board_daily as V
from app.compute import first_board_daily as FBD

PAIR = re.compile(r'(?<!\*)\*\*(?!\*)')


def _strings(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _strings(v)
    elif isinstance(o, str):
        yield o


@pytest.fixture
def fake(monkeypatch):
    """把 `engine.query` / `engine.scalar` 打成一张可控的假表。

    ⚠️ 这是**形状**测试用的桩；真库那条（`test_真库里读得到最新一场`）不打桩，
    因为历史上假绿最常见的原因就是桩与生产字段对不上（同 [[stubs-must-match-real-contract]]）。
    """
    class Eng:
        def __init__(self, *, rows=None, agg=None, shapes=None, pool=None, cov=None, fail=False):
            # 注意 `x or 默认` 会把 False 吞成默认值 —— 这几块要能单独被打成"读不到"
            self.rows = rows if rows is not None else [{'security_code': '600000.SH', 'name': '浦发银行',
                                                        'board': '主板', 'market': 'SH',
                                                        'launch_pct': 10.0, 'launch_close': 10.0,
                                                        'launch_turnover': 3.0, 'launch_amount_yuan': 1e8,
                                                        'limit_up_price': 10.0, 'is_one_word': False,
                                                        'float_cap_yuan': 3e9, 'reason': 'A+B',
                                                        'first_seal_at': None, 'last_seal_at': None,
                                                        'break_count': None, 'order_amount': 1e7,
                                                        'order_volume': 1e6, 'limit_up_kind': '换手板',
                                                        'source_high_days': '首板', 'reason_source': 'ths',
                                                        'reason_captured_at': None,
                                                        'boards': 1, 'board_days': 1, 'board_label': '1天1板',
                                                        'source_boards_agree': True,
                                                        'prev_date': None, 'prev_pct': -6.0,
                                                        'prev_turnover': 2.0, 'prev_amount_yuan': 8e7,
                                                        'prev_vol_ratio': 0.5, 'prev_touched': False,
                                                        'prev_sealed': False, 'prev_shape': 'deep_pullback',
                                                        'prev_shape_note': '前一日跌幅在 5% 及以上',
                                                        'chg5': 1.0, 'chg10': 2.0, 'chg15': 3.0,
                                                        'vol_grab_ratio': 0.9, 'dbl_days': 2, 'calm_days': 3,
                                                        'dbl_energy': 4.0, 'energy_fp': 'ev-x',
                                                        'universe': 50, 'missing': {'break_count': 'source_field_null'},
                                                        'calculated_at': '2026-10-21 21:00:00+08',
                                                        'dbl_rank': 7, 'energy_universe': 5519}]
            self.agg = agg if agg is not None else [{'first_count': 34, 'universe': 50, 'has_reason': 30,
                                'reason_row_absent': 3, 'reason_no_records': 1, 'break_null': 12,
                                'shape_null': 0, 'boards_disagree': 2, 'boards_null': 1,
                                'one_word': 0, 'prev_touched': 2, 'prev_pct_med': 1.03}]
            self.shapes = shapes if shapes is not None else [{'k': 'deep_pullback', 'n': 5}, {'k': None, 'n': 0}]
            self.pool = pool if pool is not None else [{'reason': 'A+B'}, {'reason': None}]
            self.cov = cov if cov is not None else [{'sessions': 40, 'a': '2026-08-19', 'z': '2026-10-21'}]
            self.fail = fail

        def query(self, sql, params=None, *, role='reader'):
            if self.fail:
                raise RuntimeError('库不通')
            low = ' '.join(sql.split())
            if 'as dbl_rank' in low:                       # 明细那一发
                return self.rows
            if 'group by prev_shape' in low:
                return None if self.shapes is False else self.shapes
            if low.startswith('select f.reason from'):
                return None if self.pool is False else self.pool
            if 'max(universe)' in low:
                return None if self.agg is False else self.agg
            if 'count(distinct trade_date)' in low:
                return None if self.cov is False else self.cov
            raise AssertionError(f'假引擎不认识这一发：{low[:70]}')

        def scalar(self, sql, params=None, *, role='reader'):
            if self.fail:
                raise RuntimeError('库不通')
            if 'max(trade_date)' in sql and 'spec_version' in sql:
                return '2026-10-21'
            return 1        # _on_day：这场有行

    def _install(**flags):
        eng = Eng(**flags)
        monkeypatch.setattr(V.engine, 'query', eng.query)
        monkeypatch.setattr(V.engine, 'scalar', eng.scalar)
        return eng
    return _install


class TestUnavailable:
    def test_表没跑过_没有rows键且指向补跑命令(self, monkeypatch):
        def scalar(sql, params=None, *, role='reader'):
            return None
        monkeypatch.setattr(V.engine, 'scalar', scalar)
        out = V.panel()
        assert out['available'] is False
        assert 'rows' not in out
        assert 'first_board_daily' in out['reason'], 'reason 要指出该跑哪一步，不然界面只能说"没有数"'

    def test_库不通也是不可用不是空榜(self, fake):
        fake(fail=True)
        out = V.panel(date='2026-10-21')
        assert out['available'] is False and 'rows' not in out


class TestAggregates:
    def test_全场聚合不随筛参与上限变(self, fake):
        fake()
        a = V.panel()
        b = V.panel(shapes='deep_pullback', limit=10)
        assert b['shape_tally'] == a['shape_tally'], '各档只数是本场全体的，筛一档不该把别的档抹成 0'
        assert b['first_count'] == a['first_count'] and b['universe'] == a['universe']
        assert b['reason_tally'] == a['reason_tally']
        assert b['rows_returned'] <= 10 < 34          # 只有行被筛/被截

    def test_聚合读不到时出声且不交空表冒充零(self, fake):
        fake(agg=False, shapes=False, pool=False, cov=False)
        out = V.panel()
        codes = [c['code'] for c in out['caveats']]
        assert codes.count('agg_unread') >= 4, '四块聚合各自都要有一条"没读到"'
        assert all(c['hard'] for c in out['caveats'] if c['code'] == 'agg_unread'), '读不到是告警，不许折进折叠框'

    def test_非法形态档退回不筛并点名(self, fake):
        fake()
        out = V.panel(shapes='sideways,deep_pullback')
        bad = [c for c in out['caveats'] if c['code'] == 'bad_shape_filter']
        assert bad and 'sideways' in bad[0]['text']
        assert out['shape_filter'] == ['deep_pullback'], '认得下的那档照旧生效，不整条作废'


class TestRowHonesty:
    def test_缺数走null且有归因(self, fake):
        fake()
        r = V.panel()['rows'][0]
        assert r['first_seal_at'] is None, '源站没给时间戳就是 null，不写 00:00'
        assert r['break_count'] is None, '"没给这个字段"不等于"0 次没炸"'
        assert r['reason_missing'] == 'source_field_null' or r['missing'], '缺什么都要有键'

    def test_形态短标签来自写侧一份不自己起名(self, fake):
        fake()
        r = V.panel()['rows'][0]
        assert r['prev_shape_label'] == FBD.SHAPE_NAMES['deep_pullback']
        tally = {x['key']: x['label'] for x in V.panel()['shape_tally']}
        assert tally['deep_pullback'] == FBD.SHAPE_NAMES['deep_pullback']
        assert tally[None] == V.SHAPE_UNJUDGED


class TestRecentFourColumns:
    """2026-10-27 新加那四列的读侧契约（`_enrich`）。

    钉的是三类会"看着正常其实是假数"的形状：字段名平铺（界面 sortKey 要等于格子里读的字段）、
    一族读不到只折它自己那句（不把整屏拖空、也不悄悄当成"这批票都没有"）、
    缺数是 None（0 与 — 在屏幕上是两句话）。
    """

    def _run(self, monkeypatch, *, spans=None, full=None, quad=None, dbl=None, spike=None):
        rows = [{'code': '600000.SH'}, {'code': '301314.SZ'}]
        monkeypatch.setattr(V.BS, 'sealed_days_by_code',
                            lambda *a, **k: spans or {})

        def q(sql, params):
            low = ' '.join(sql.split())
            if 's.rank_in_market' in low:
                return quad
            if 'bars_lo' in low:
                return dbl
            if 's.spike_tags' in low:
                return spike
            if 'from reference_data.security' in low:
                return full if full is not None else [
                    {'security_code': '600000.SH', 'c6': '600000'},
                    {'security_code': '301314.SZ', 'c6': '301314'}]
            raise AssertionError(f'这一发没登记：{low[:70]}')

        monkeypatch.setattr(V, '_safe_query', q)
        warns: list[str] = []
        meta = V._enrich(rows, '2026-10-21', warns)
        return rows, meta, warns

    def test_四族都读到就各自平铺到行上(self, monkeypatch):
        rows, meta, warns = self._run(
            monkeypatch,
            spans={'600000': {'sealed_days': 4, 'bars': 20, 'dates': ['2026-10-09']}},
            quad=[{'code': '600000', 'day': '2026-10-20', 'quadrant': 'Q1',
                   'rank_in_market': 12, 'value': 0.4, 'is_stale': False}],
            dbl=[{'code': '600000', 'bars_lo': 10, 'dbl_lo': 2, 'near_lo': 1,
                  'bars_hi': 20, 'dbl_hi': 5, 'near_hi': 3, 'last_day': '2026-10-21'}],            spike=[{'code': '600000', 'spike_tags': ['fast', 'sealed'], 'first5_time': '09:41',
                    'speed15_pct': 8.2, 'above5_min': 30, 'touched': True, 'sealed': True,
                    'minute_available': True}])
        a, b = rows
        assert (a['boards_20d'], a['boards_20d_bars']) == (4, 20)
        assert a['quad'][0]['rank'] == 12 and a['quad'][0]['stale'] is False
        # 平铺：界面那一列的 sortKey 必须等于格子里读的字段名，嵌一层的 dbl.10 排不了序
        assert a['dbl10'] == {'bars': 10, 'dbl': 2, 'near': 1}
        assert a['dbl20'] == {'bars': 20, 'dbl': 5, 'near': 3}
        assert a['dbl_unit'] == '对昨日量倍数'
        assert a['dbl_last_day'] == '2026-10-21', '窗口截至哪天要随行交出：取错年代时只有它能露馅'
        assert a['spike_tier'] and a['spike_tier_rank'] == 3
        assert a['spike_first5_at'] == '09:41' and a['spike_checked'] is True
        assert meta['boards_window'] == V.BS.COUNT_WINDOW_SESSIONS
        assert meta['dbl_windows'] == list(V.DBL_WINDOWS)
        # 这一族里没有的那只票：格子留 None（—），不拿别的票的数顶上、也不写 0
        assert b['boards_20d'] is None and b['dbl10'] is None and b['quad'] == []
        assert b['spike_tier'] is None and b['spike_checked'] is True, '探过了没进榜＝真没进榜'
        assert warns == []

    def test一族读不到只记它自己那句其余照旧(self, monkeypatch):
        rows, _, warns = self._run(
            monkeypatch,
            spans={'600000': {'sealed_days': 4, 'bars': 20, 'dates': []}},
            dbl=None,                     # 倍量那一族查挂了
            quad=[{'code': '600000', 'day': '2026-10-20', 'quadrant': 'Q1',
                   'rank_in_market': 12, 'value': 0.4, 'is_stale': False}],
            spike=[])
        assert len(warns) == 1 and '倍量' in warns[0], warns
        assert rows[0]['dbl10'] is None and rows[0]['dbl20'] is None
        assert rows[0]['boards_20d'] == 4 and rows[0]['quad'], '一族的故障不许把整屏拖空'
        assert rows[0]['spike_checked'] is True, '交了空表＝探过了没进榜，不是没读到'

    def test代码对照表读不到时四族一起说没说而不是各说没数(self, monkeypatch):
        rows, _, warns = self._run(monkeypatch, full=[])
        assert len(warns) == 1 and '代码对照表没读到' in warns[0]
        assert rows[0]['boards_20d'] is None and rows[0]['spike_checked'] is None, \
            '对照表都没有 ⇒ spike_checked 是 None（没探），不是 False（探过没进榜）'

    def test_倍量等号归倍量准倍量到线(self, monkeypatch):
        """判据的边界由 SQL 的 >= 与 < 决定，这里钉的是读侧把它平铺对了档：lo→10、hi→20。"""
        rows, _, _ = self._run(monkeypatch, spans={}, quad=[], spike=[],
            dbl=[{'code': '600000', 'bars_lo': 3, 'dbl_lo': 0, 'near_lo': 0,
                  'bars_hi': 9, 'dbl_hi': 1, 'near_hi': 0, 'last_day': '2026-10-21'}])
        assert rows[0]['dbl10'] == {'bars': 3, 'dbl': 0, 'near': 0}, '真 0 次照常交，界面才念得出 0 而不是 —'
        assert rows[0]['dbl20'] == {'bars': 9, 'dbl': 1, 'near': 0}


class TestScreenTextHygiene:
    def test_常量层干净(self):
        for s in _strings({'rule': V.RULE, 'definitions': V.DEFINITIONS}):
            assert not PAIR.search(s), s[:70]
            assert '"' not in s, s[:70]

    def test_每一种状态的上屏文字都干净(self, fake):
        blobs = []
        fake()
        blobs.append(V.panel())
        blobs.append(V.panel(shapes='sideways,bad,deep_pullback'))
        fake(agg=False, shapes=False, pool=False, cov=False)
        blobs.append(V.panel())
        bad = []
        for p in blobs:
            blob = json.dumps(p, ensure_ascii=False, default=str)
            assert 'NaN' not in blob and 'Infinity' not in blob
            for s in _strings(p):
                if PAIR.search(s) or '"' in s:
                    bad.append(s[:70])
        assert not bad, '\n'.join(bad)


def test_真库里读得到最新一场():
    """不打桩的一条：真库读回最新一场，字段形状与判据档位必须都在。

    桩只证明"代码按我想的跑"，这条证明"库里真有这么个东西、界面能拿到数"。
    """
    p = V.panel()
    if not p.get('available'):
        pytest.skip('本机库还没跑过 first_board_daily（写侧：python -m app.compute.first_board_daily）')
    assert p['spec_version'] == FBD.SPEC_VERSION
    assert p['rows'] and p['rows_returned'] == len(p['rows'])
    assert p['first_count'] and p['universe'] and p['coverage']['sessions'] >= 1
    keys = {t['key'] for t in p['shape_tally']}
    assert keys <= set(FBD.SHAPE_PRIORITY) | {None}
    assert all(r['prev_shape'] is None or r['prev_shape'] in FBD.SHAPE_NAMES for r in p['rows'])
    # 前一日"收盘封住"在首板母体下必须为 0（不为 0 说明母体串了别的判据）
    assert not any(r['prev_sealed'] for r in p['rows'])
    # 10-27 新加那四列：键必须每一行都在（读不到是 None，不是没有这个键），窗口长度随元数据交出
    for r in p['rows']:
        for k in ('boards_20d', 'boards_20d_bars', 'quad', 'dbl10', 'dbl20', 'dbl_unit',
                  'spike_tier', 'spike_tier_rank', 'spike_first5_at', 'spike_speed15_pct',
                  'spike_above5_min', 'spike_tags', 'spike_checked'):
            assert k in r, f'{k} 没随行交出＝这一格根本没接'
    assert p['recent']['boards_window'] == V.BS.COUNT_WINDOW_SESSIONS
    assert p['recent']['dbl_windows'] == list(V.DBL_WINDOWS)
    assert p['recent']['quad_sessions'] == V.QUAD_SESSIONS
    assert 'recent_sources' in {c['code'] for c in p['caveats']}, \
        '这四列各有各的源表，那句"不是本表算的"必须随本场一起到界面'
    assert 'boards_tolerance' not in {c['code'] for c in p['caveats']}, \
        '「几天几板」那一列已撤下，没有指对象的对账数不该再出现在屏上'
    _对账近20场倍量(p)


def _对账近20场倍量(p):
    """独立重算一次「近 20 场倍量」再比：窗口取错年代时，格子照样有数、只有对账能抓出来。

    2026-10-27 实测那条教训：`select distinct trade_date ... limit 20` 少了 `order by ... desc`，
    Postgres 给的是**最早**那 20 场（2021-08-02~08-27），屏上名字却写着"近 20 场"；
    当时 7 只 2022 年后上市的票整格是 — 才露馅。这条用例把"窗口=最近 N 场"钉成数字。
    """
    from app.db import engine
    from app.compute import volume_ratio as VR

    cands = [r for r in p['rows'] if r.get('dbl20')]
    assert cands, '交了这些行却一只都没有量能行＝这一族根本没接上'
    # 取窗口根数最少的那只（多半是新股）：它对"窗口取错年代"最敏感——老票两种窗口都是 20 根，看不出来
    want = min(cands, key=lambda r: int(r['dbl20'].get('bars') or 0))
    code6 = want['code'][:6]
    got = want['dbl20']
    ref = engine.query(
        """with days as (select trade_date, row_number() over (order by trade_date desc) as rn
                           from (select distinct trade_date from analytics.volume_ratio_daily
                                  where trade_date <= %s::date
                                  order by trade_date desc limit 20) x)
           select count(*) bars,
                  count(*) filter (where v.vol_ratio >= 2.0) dbl,
                  count(*) filter (where v.vol_ratio >= 1.6 and v.vol_ratio < 2.0) near
             from analytics.volume_ratio_daily v join days d using (trade_date)
            where left(v.security_code, 6) = %s and v.spec_version = %s""",
        (p['date'], code6, VR.SPEC_VERSION), role='reader')[0]
    assert got['bars'] == ref['bars'], f"{code6} 窗口根数 {got['bars']} 对不上独立重算 {ref['bars']}"
    assert got['dbl'] == ref['dbl'] and got['near'] == ref['near']
    # 窗口截至那一天必须就是这一场：少了 `order by trade_date desc`，这里会念出 2021-08-27，
    # 而屏上的数照样"看着正常"（老票两种窗口都是 20 根），只有这个日期能当场露馅。
    for r in cands:
        assert r['dbl_last_day'] == p['date'], f'{r["code"]} 的窗口截至 {r["dbl_last_day"]}，不是这一场 {p["date"]}'
