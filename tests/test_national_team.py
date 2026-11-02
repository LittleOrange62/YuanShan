# -*- coding: utf-8 -*-
"""国家队代理指标：读侧判据、两把尺接线与"判不了不许说成没越界"。

用户 2026-10-26 点名的那一屏（「头部沪深300基金市值占沪深300流通市值比例的变化」），
四条硬规矩逐条钉在这里：
1. 判据（分档、异动、分位）只住 `duanxian/national_team.py` 一处，界面只转述；
2. 分母（本地自算的流通市值）比分子晚入库 ⇒ 早期占比是 null，不是 0、不是插值；
3. 净申赎那一族**不建历史档**（有正负的量级取分位不成立），不许落成"中性"；
4. 两把尺（big4 / all）任一越界都要点名，同一天的同一个动作不许报两遍。

最后两条**不打桩**：桩绿不代表接线对 —— 表里的真行数、链上的步骤、端点真的在不在，
必须能自己复算（同 `test_rank_momentum` 那条纪律）。
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

from duanxian import national_team as NT

ROOT = Path(__file__).resolve().parent.parent


def _db_up() -> bool:
    from app.db import engine
    try:
        return bool(engine.scalar('select 1'))
    except Exception:  # noqa: BLE001
        return False


def _pt(day: str, **kw) -> dict:
    p = {'trade_date': day, 'etf_count': 4, 'share_yi': None, 'size_yi': None, 'net_yi': None,
         'float_mv_wy': None, 'ratio_float_pct': None, 'ratio_total_pct': None, 'missing': None}
    p.update(kw)
    return p


# ============================================================ 一、纯判据（不碰库）

def test_分位与档位_样本不足就是判不了():
    assert NT._pctl([], 0.5) is None
    assert NT._pctl([1, 2, 3, 4], 0.0) == 1
    assert NT._pctl([1, 2, 3, 4], 1.0) == 4
    assert NT._pctl([1, 2, 3, 4], 0.5) == 2.5
    # 切点不齐（bands 还没算出来）时当期值不许被硬塞进"中性"
    assert NT.band_of(0.7, None) == (None, None)
    cuts = {'p10': 0.4, 'p25': 0.45, 'p50': 0.5, 'p75': 0.6, 'p90': 0.7}
    assert NT.band_of(0.35, cuts) == ('ultra_low', '超低')
    assert NT.band_of(0.95, cuts) == ('ultra_high', '超高')
    assert NT.band_of(0.5, cuts)[0] == 'mid'
    # 值本身缺 ⇒ 档也缺，不是"偏低"
    assert NT.band_of(None, cuts) == (None, None)


def test_变动跨缺数时点数要跟着交出来():
    pts = [_pt('2026-01-01', ratio_float_pct=0.40), _pt('2026-01-02'),
           _pt('2026-01-03', ratio_float_pct=0.55)]
    d = NT.deltas([p['ratio_float_pct'] for p in pts])
    assert d == [None, None, pytest.approx(0.15)], '缺数那一天不产生变动，但下一个有效值要跟得上'
    g = NT.delta_gaps(pts, 'ratio_float_pct')
    assert g[2] == 2, '这两个有效读数之间隔了一场 —— 异动那句必须说"跨 2 场"'


def test_异动三档判法_同一天不报两遍():
    """jump＝占比线、level＝净申赎、none＝份额/规模（份额当日变动就是净申赎那一条）。"""
    flat = [_pt(f'2026-01-{i + 1:02d}', ratio_float_pct=0.50 + i * 0.001,
                share_yi=800 + i, size_yi=3400 + i, net_yi=1.0) for i in range(30)]
    spec_share = NT.SERIES_BY_KEY['share']
    hits, meta = NT.anomalies_of(NT._points(flat, spec_share['field']), spec_share, None)
    assert meta['judge'] == 'none' and hits == [], '份额/规模不该判单日异动'
    assert meta['rule'] is None, '设计上不判 ≠ "判不了"，不该往 caveats 里塞一条告警'

    spec_ratio = NT.SERIES_BY_KEY['ratio_float']
    spikes = [_pt(f'2026-02-{i + 1:02d}', ratio_float_pct=v) for i, v in
              enumerate([0.50] * 24 + [0.62])]
    hits, meta = NT.anomalies_of(NT._points(spikes, spec_ratio['field']), spec_ratio, None)
    assert meta['judge'] == 'jump'
    assert len(hits) == 1 and hits[0]['date'] == spikes[-1]['trade_date']
    assert 'pp' in hits[0]['text'] and 'p95' in hits[0]['text']

    spec_flow = NT.SERIES_BY_KEY['flow']
    flows = [_pt(f'2026-03-{i + 1:02d}', net_yi=1.0) for i in range(60)] + \
            [_pt('2026-03-61', net_yi=-80.0)]
    hits, meta = NT.anomalies_of(NT._points(flows, spec_flow['field']), spec_flow, 40.0)
    assert meta['judge'] == 'level' and len(hits) == 1
    assert '赎回' in hits[0]['text'], '方向要在那句话里，读者不该自己看符号'


def test_样本底不够时说明是判不了而不是没越界():
    few = [_pt(f'2026-04-{i + 1:02d}', net_yi=float(i)) for i in range(5)]
    spec = NT.SERIES_BY_KEY['flow']
    hits, meta = NT.anomalies_of(NT._points(few, spec['field']), spec, None)
    assert hits == [] and meta['rule'], '凑不满样本底必须写出为什么判不了'
    assert NT.MIN_SAMPLES_FLOW > len(few)
    b = NT.bands_of(NT._points(few, spec['field']), spec)
    assert b['available'] is False and '分' in b['reason'] or '档' in b['reason']


def test_净申赎不建历史档():
    """有正负的量级取分位＝把"赎得狠"排进 p10、"买得狠"排进 p90，那种档没有读法。

    2026-10-29 追加第二段：份额与规模**也**不建档了（`bandable` 为假）—— 它们是趋势量，
    新基金成立与行情涨跌都会推着它一路走，取自己历史的分位只会把"指数涨了"念成"国家队买了"。
    """
    spec = NT.SERIES_BY_KEY['flow']
    assert spec['stock'] is False
    # 样本底抬到 120 场之后，这里要给够，否则测的就不是"panel 决定不建档"而是"样本不够"
    pts = [_pt((dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat(),
               net_yi=float(i - 70)) for i in range(140)]
    b = NT.bands_of(NT._points(pts, spec['field']), spec)
    assert b['available'] is True   # 函数本身算得出，是 panel 那一层决定不给它建档
    assert NT.SERIES_BY_KEY['ratio_float']['stock'] is True
    for key in ('share', 'size'):
        s = NT.SERIES_BY_KEY[key]
        assert s.get('bandable') is False, f'{key} 是趋势量，不许再走历史分位那条路'
        bb = NT.bands_of(NT._points(pts, s['field']), s)
        assert bb['available'] is False and bb['not_applicable'] is True, bb
        assert ('趋势' in bb['reason'] or '行情' in bb['reason']), bb['reason']


def test_占比线跨度不够时档位判不了而且把跨度顶在最前():
    """46 场（2026-07-22 起）的 p95 不是"历史位置"。这一条钉住那一句为什么。"""
    spec = NT.SERIES_BY_KEY['ratio_float']
    pts = [_pt((dt.date(2026, 7, 22) + dt.timedelta(days=i)).isoformat(),
               ratio_float_pct=0.60 + i * 0.001) for i in range(46)]
    b = NT.bands_of(NT._points(pts, spec['field']), spec)
    assert b['available'] is False and b['samples'] == 46, b
    assert b['reason'].startswith('只有 46 个有效读数（2026-07-22'), b['reason']
    assert '2026-09-05' in b['reason'] and '跨 45 天' in b['reason'], b['reason']
    assert f'≥{NT.MIN_SAMPLES_BAND}' in b['reason']
    assert '不许念成「这把尺自己的历史分位」' in b['reason'], b['reason']
    # 同样的 46 场拿去判跳变是可以的（那是变动，不是位置），但阈值句必须自带跨度
    hits, meta = NT.anomalies_of(NT._points(pts, spec['field']), spec, None)
    assert meta['samples'] == 45 and meta['span_note'] == \
        '45 个样本（46 个读数日），日期 2026-07-22 ~ 2026-09-05（跨 45 天）', meta


# ============================================================ 二、panel 的信封（打桩 _rows）

def _stub_rows(monkeypatch, rows):
    monkeypatch.setattr(NT.pg, 'enabled', lambda *a, **k: True)
    monkeypatch.setattr(NT, '_rows', lambda sql, params=(): rows)


def _two_scale_rows(days: int = 30, flow=None):
    """两把尺各 `days` 场：前 20 场没有分母（快照表还没入库），后面才有占比。

    `days` 要开大才跑得动样本底那几道闸（净申赎判异动要 ≥60 场），
    所以它是个参数而不是一串写死的行。`flow(i)` 自己造净申赎的形状
    （2026-10-29 起档位那条路不再产生提示，要真出异动就得让净申赎真的越过 p95）。
    """
    rows = []
    ratio_from = max(20, days - 10)
    for i in range(days):
        day = (dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat()
        for scale in ('big4', 'all'):
            has_ratio = i >= ratio_from
            rows.append({'trade_date': day, 'scale_key': scale,
                         'etf_count': 4 if scale == 'big4' else 25,
                         'share_yi': 800.0 + i, 'size_yi': 3400.0 + i,
                         'net_yi': flow(i) if flow else (2.0 if i != 25 else 90.0),
                         'float_mv_wy': 48.0 if has_ratio else None,
                         'ratio_float_pct': round((800.0 + i) / 48.0 * 1e4 / 1e8, 4) if has_ratio else None,
                         'ratio_total_pct': 0.6 if has_ratio else None,
                         'missing': None if has_ratio else 'float_mv_yuan'})
    return rows


def test_库不通与表里没行是两句不同的话(monkeypatch):
    monkeypatch.setattr(NT.pg, 'enabled', lambda *a, **k: False)
    d = NT.panel()
    assert d['available'] is False and '库不通' in d['reason']
    assert 'series' not in str(d.keys())
    _stub_rows(monkeypatch, [])
    d2 = NT.panel()
    assert d2['available'] is False and 'national_team' in d2['reason']
    # 两种都没有 scales 键 —— 界面不会因为"空数组"而画出一张假图
    assert 'scales' not in d and 'scales' not in d2


def test_非法时间档退回近一年并在caveats点名(monkeypatch):
    _stub_rows(monkeypatch, _two_scale_rows())
    d = NT.panel(range='99')
    assert d['range'] == '1y'
    assert any(c['code'] == 'param_range' for c in d['caveats'])
    assert d['range_options'] and all(o['key'] in {'3m', '6m', '1y', '2y', 'all'}
                                      for o in d['range_options'])


def test_占比早期就是null而且这一屏要说出为什么(monkeypatch):
    _stub_rows(monkeypatch, _two_scale_rows())
    d = NT.panel(range='all')
    s = next(x for x in d['scales'] if x['key'] == 'big4')
    pts = s['points']
    assert pts[0]['ratio_float_pct'] is None, '没有分母的那些场必须交 null（界面 —），不补 0'
    assert pts[0]['missing'] == ['float_mv_yuan']
    assert s['ratio_sessions'] == 10 and s['share_sessions'] == 30
    assert d['coverage']['ratio_from'] == pts[20]['trade_date']
    hard = [c for c in d['caveats'] if c.get('hard')]
    assert any('占比只有' in c['text'] and '份额线有' in c['text'] for c in hard), \
        '两条线长度不等是这一屏最容易读错的事，必须硬报出来'


def test_两把尺都摆且任一越界都要点名(monkeypatch):
    """用户裁的是"但凡有一个报警都提示"。

    2026-10-29 换母体（档位那条路被 120 场门槛关掉了 ⇒ 提示只能来自真异动），
    并加一段合并核对：同一天同一个动作，两把尺 × 两条占比线**不许再各报一遍**。
    """
    _stub_rows(monkeypatch, _two_scale_rows(days=90, flow=lambda i: 2.0 + 0.9 * i))
    d = NT.panel(range='all')
    assert [s['key'] for s in d['scales']] == ['big4', 'all']
    a = d['alert']
    assert a['fired'] is True
    assert a['items'], '两把尺都得有发言权'
    for i in a['items']:
        assert i['text'] and i['scale_label'], '每条要点名是哪一把尺'
        assert set(i['scale_keys']) == {'big4', 'all'}, f'合并后的事件要把两把尺一起点名：{i}'
        assert i['family'] == 'flow' and i['parts_count'] == 2, i
        assert '+' in i['scale_label'] or '＋' in i['scale_label'], i['scale_label']
    # 一个事件 = (日期 × 动作族)：条目数必然大于事件数，屏上念的是事件数
    assert a['count_all'] < a['raw_entries'], (a['count_all'], a['raw_entries'])
    assert len({(i['date'], i['family']) for i in a['items']}) == len(a['items'])
    assert '合并后共' in a['text'] and '原始条目' in a['text'], a['text']
    # 份额/规模既不进异动、也不进档 ⇒ 清单里根本不该出现这两条序列
    assert all(i['series'] not in ('share', 'size') for i in a['items'])


def test_提示条超出上限要报条数不许悄悄丢(monkeypatch):
    rows = _two_scale_rows(days=90)
    for r in rows:                     # 每一天都砸一次大额申赎 ⇒ 条数远超上限
        r['net_yi'] = 200.0
    _stub_rows(monkeypatch, rows)
    d = NT.panel(range='all')
    assert d['alert']['count_all'] > NT.ALERT_CAP, d['alert']['count_all']
    assert d['alert']['truncated'] == d['alert']['count_all'] - len(d['alert']['items'])
    assert len(d['alert']['items']) == NT.ALERT_CAP
    assert '没列出' in d['alert']['text'], '截断必须写在屏上那句里，不是只藏在字段里'


def test_交出去的点里不许有内部字段_prev(monkeypatch):
    """`_prev` 是算变动用的脚手架。留着＝屏幕上多一列真数，下一有人会拿它当"昨日份额"。"""
    _stub_rows(monkeypatch, _two_scale_rows())
    d = NT.panel(range='all')
    for s in d['scales']:
        for p in s['points']:
            assert '_prev' not in p


def test_阈值与档名只有一份_随接口交出(monkeypatch):
    _stub_rows(monkeypatch, _two_scale_rows())
    d = NT.panel(range='all')
    assert set(d['levels']) == {'ultra_low', 'low', 'mid', 'high', 'ultra_high'}
    assert d['thresholds']['min_samples'] == {'band': NT.MIN_SAMPLES_BAND,
                                              'jump': NT.MIN_SAMPLES_JUMP, 'flow': NT.MIN_SAMPLES_FLOW,
                                              'trend': NT.MIN_SAMPLES_TREND}
    assert d['series_options'] and all(o['label'] and o['unit'] and o['read'] for o in d['series_options'])
    assert d['rule'] and d['definitions']['scope']


# ============================================================ 三、写侧与链上接线（不打桩）

def test_链上两步都登记且顺序站得住():
    import ingest.jobs.e1_fill_all as e1
    names = [s[0] for s in e1.STAGES]
    assert 'index_mv' in names and 'national_team' in names, '没接进每日链 ⇒ 这张表从此不再更新'
    assert names.index('national_team') > names.index('etf'), '分子读 etf_share'
    assert names.index('national_team') > names.index('snapshot'), '分母读 stock_snapshot'
    assert names.index('national_team') > names.index('share')
    src = (ROOT / 'ingest' / 'jobs' / 'e1_fill_all.py').read_text(encoding='utf-8')
    assert re.search(r"'index_mv':\s*\('ingest\.jobs\.c26_index_mv',\s*\[\]\)", src), \
        'STAGES 有条目但没接执行入口（跑到那一步会 KeyError）'
    assert re.search(r"'national_team':\s*\('app\.compute\.national_team'", src), '同上'
    assert 'index_mv' in e1.STEP_TIMEOUTS and 'national_team' in e1.STEP_TIMEOUTS
    # 只给当天的接口：归属日对账不过就是按设计不写，重试一百次也还是"现在"
    assert e1._retryable('index_mv', 2) is False
    assert e1._retryable('index_mv', 1) is True


def test_归属日自证那一道闸不许兜成空值():
    from ingest.jobs import c26_index_mv as C26
    assert C26.check_date(4083.87, 4083.87, dt.date(2026, 10, 28)) is None
    why = C26.check_date(4200.0, 4083.87, dt.date(2026, 10, 28))
    assert why and '4083' in why and '不写' in why
    # 接口没给收盘价：不能确认是同一天就不写（空值比报错更坏）
    assert C26.check_date(None, 4083.87, dt.date(2026, 10, 28))
    # 库里那场没有可比收盘价 ⇒ 这一道判不了，但取到的数照写（不是拒写）
    assert C26.check_date(4200.0, None, dt.date(2026, 10, 28)) is None


def test_价格单位那一道自检必须在写侧():
    """`fund_daily.close` 是 0.1 元：第一版没换算 ⇒ 规模与占比整体放大 10 倍（3,388 亿被念成 3.388 万亿）。"""
    src = (ROOT / 'app' / 'compute' / 'national_team.py').read_text(encoding='utf-8')
    assert 'PRICE_TICK_TO_YUAN = 10.0' in src
    assert 'AVG_PRICE_BAND' in src and 'avg_price_out_of_band' in src
    assert 'view_etf_flow' in src, '净申赎要走分区 LAG 的视图，自己今日减昨日会把首发算成申购'


def test_缺数与告警那一族不许念机器码(monkeypatch):
    """caveats 与 alert.items 站在全站折叠红线外面（缺数/告警绝不折），
    所以那几句必须读得出人话：列名 `float_mv_yuan` 之类只待在接口的 `missing` 字段里。"""
    _stub_rows(monkeypatch, _two_scale_rows())
    d = NT.panel(range='all')
    texts = [c['text'] for c in d['caveats']] + [i['text'] for i in d['alert']['items']] + [d['alert']['text']]
    joined = ' '.join(texts)
    for code in ('float_mv_yuan', 'size_yuan', 'share_units', 'ratio_float_pct', 'net_creation_units'):
        assert code not in joined, f'屏幕那句话里念了列名：{code}'
    assert '缺数标记' not in joined or '那天没有收盘快照行' in joined
    # 列名本身照旧随点行交出（读侧词表要认得它），只是不上屏
    assert any(p['missing'] for p in d['scales'][0]['points'])


def test_读侧表里没有的东西不许凭空出现():
    """异动与分档不落库：库里出现 threshold/band 列就说明有人把阈值焊进了历史行。"""
    if not _db_up():
        pytest.fail('库不通：这条核对必须连真库跑')
    from app.db import engine
    cols = {r['column_name'] for r in engine.query(
        "select column_name from information_schema.columns "
        "where table_schema='analytics' and table_name='national_team_etf_daily'", ())}
    assert {'trade_date', 'scale_key', 'spec_version', 'share_units', 'size_yuan',
            'net_creation_units', 'float_mv_yuan', 'ratio_float_pct',
            'ratio_total_pct', 'members', 'missing'} <= cols, cols
    assert not (cols & {'threshold', 'band', 'is_anomaly', 'pctile'}), \
        '阈值型判据一进表，改阈值就会让历史行挂上旧含义'


def test_真库占比与净申赎自洽():
    """两把尺的当期读数 + 一条事后自证：Δ份额 == 当日净申赎（同一个母体、同一批 LAG）。

    不打桩。这一条专门抓"单位又变了"和"清单换了母体没跟着换"那两类静默错 ——
    10× 那一版如果跑了这条，占比会从 6.94% 掉回 0.694%，而规模/份额的比例当场不对。
    """
    if not _db_up():
        pytest.fail('库不通：这条真库核对不许兜成跳过')
    d = NT.panel(range='2y')
    assert d['available'] is True, d.get('reason')
    for s in d['scales']:
        pts = [p for p in s['points'] if p.get('share_yi') is not None]
        assert len(pts) > 100, f'{s["key"]} 的份额线太短：{len(pts)}'
        # 占比 = 规模 ÷ 分母（万亿 → 亿元），逐格对一遍
        for p in s['points']:
            if None in (p['ratio_float_pct'], p['size_yi'], p['float_mv_wy']):
                continue
            calc = p['size_yi'] / (p['float_mv_wy'] * 1e4) * 100
            assert abs(calc - p['ratio_float_pct']) < 0.005, p
        # 份额的相邻有效差 == 那一天净申赎（视图口径）
        by_date = {p['trade_date']: p for p in s['points']}
        dates = sorted(by_date)
        matched = 0
        for a, b in zip(dates, dates[1:]):
            pb, pa = by_date[b], by_date[a]
            if None in (pb['share_yi'], pa['share_yi'], pb['net_yi']):
                continue
            if abs((pb['share_yi'] - pa['share_yi']) - pb['net_yi']) < 0.02:
                matched += 1
        assert matched > 50, f'Δ份额与净申赎对不上的太多（只对上 {matched} 天）—— 母体或视图口径变了'
        assert s['series_view']['flow']['anomaly_meta']['threshold'], '净申赎阈值算不出来 = 样本底不够'
        # 2026-10-31 分母换尺（快照求和 → 当日日线自算）之后，主尺占比线有全史 679 场 ⇒
        # 历史档位第一次真的能切（p10/p90 存在、跨度句自带"几场、跨多久"）。
        # 换尺之前这一格只能读"判不了"（只有 46 场），那两句断言一起搬到这里，判据没放宽：
        # 仍然是"够场数才给档、档位必须带着跨度一起念"。
        bands = s['series_view']['ratio_float']['bands']
        assert bands['available'] is True, bands
        assert bands['samples'] >= NT.MIN_SAMPLES_BAND, bands['samples']
        assert bands['cuts'] and bands['cuts']['p10'] < bands['cuts']['p90'], bands['cuts']
        assert '跨' in (bands.get('span_note') or ''), bands
        # 备用尺（占总市值）的分母仍然只从快照来 ⇒ 那一格必须照旧读"判不了"，
        # 且把"只有几场、跨多久"顶在最前（这条是 10-29 那处误读的解药，不能因为主尺能切了就一起撤）
        tb = s['series_view']['ratio_total']['bands']
        assert tb['available'] is False, tb
        assert tb['reason'].startswith('只有 '), tb['reason']
        assert str(tb['samples']) in tb['reason'], tb['reason']
        # 阈值句自带跨度：p95 不许再被念成"历史位置"
        note = s['series_view']['ratio_float']['anomaly_meta']['span_note']
        assert note and '跨' in note, s['series_view']['ratio_float']['anomaly_meta']
    # 单位没跑偏：四大这一把尺的当期占比应该是零点几个百分点，不是 6.9%
    big4 = next(s for s in d['scales'] if s['key'] == 'big4')
    cur = big4['series_view']['ratio_float']['current']
    assert 0.1 < cur < 5.0, f'当期占比 {cur}% 不在量级内（10× 单位错的形状就是它 > 5）'


# ============================================================ 四、趋势线（均线 + 转折点）
# 用户 2026-10-31 点名「图表做的连贯一点，然后明确一些转折点」。
# 连贯那一半是分母换尺（写侧 nt-2）解决的；这一节钉的是趋势那一半的形状：
# 判不了要说得出为什么判不了、未确认的极值不许当拐点、两个屏不许各算一套拐点。

def _pts(vals):
    return [{'trade_date': f'2026-01-{i + 1:02d}', NT.SERIES[0]['field']: v} for i, v in enumerate(vals)]


def test_趋势线_样本不够就说判不了而且报出几场():
    spec = NT.SERIES[0]
    t = NT.trend_of(_pts([0.5] * (NT.MIN_SAMPLES_TREND - 1)), [], spec)
    assert t['available'] is False and t['ma'] == [] and t['rows'] == []
    assert str(NT.MIN_SAMPLES_TREND - 1) in t['reason'] and '不画' in t['reason'], t['reason']
    # 缺数的那几场不许算进样本底（否则一条只有 20 场有数的线也能凑够 60）
    sparse = _pts([0.5 if i % 3 else None for i in range(NT.MIN_SAMPLES_TREND + 10)])
    assert NT.trend_of(sparse, [], spec)['available'] is False


def test_趋势线_均线不满窗口不硬凑而且末端未确认的极值不交():
    """两件事一起钉：① 均线头 smooth-1 期必须是 null（拿现有几期硬凑＝造一条没发生过的线）；
    ② 涨上去之后只回头一点点（没走够门槛）⇒ 那个高点不许当拐点交出去。
    ②是这一族最容易蒙人的形状：图上看着就是个顶，但"是不是顶"要等它后面跌够才算。"""
    spec = NT.SERIES[0]
    k = spec['trend']['smooth']
    # 先涨 60 期，再跌 0.05（门槛 0.15 的一半）—— 这个高点还没被确认
    vals = [0.5 + i * 0.1 for i in range(60)] + [6.45 - i * 0.01 for i in range(5)]
    full = _pts(vals)
    t = NT.trend_of(full, full, spec)
    assert t['available'] is True
    assert all(v is None for v in t['ma'][:k - 1]), t['ma'][:k - 1]
    assert t['ma'][k - 1] is not None
    assert t['rows'] == [], f'只回头 0.045（门槛 {spec["trend"]["th"]}）就交拐点，等于把抖动说成转折：{t["rows"]}'
    # 同样的形状但反向走够门槛 ⇒ 那个顶就该交了（上一条不许是"永远不交"造成的假绿）
    deep = [0.5 + i * 0.1 for i in range(60)] + [6.45 - i * 0.05 for i in range(20)]
    t2 = NT.trend_of(_pts(deep), _pts(deep), spec)
    assert [r['kind'] for r in t2['rows']] == ['peak'], t2['rows']
    assert 'empty_note' in t and t['empty_note'], '一个拐点都没有时要有那句话（免得屏上静默）'


def test_趋势线_确认过的顶底按日期交回并且带落差():
    spec = NT.SERIES[0]
    # 先涨约 1.9 个点、再跌约 1.9 个、再涨约 1.9 个（都远超 0.15 的门槛）⇒ 中间那个是均线顶
    vals = ([0.5 + i * 0.065 for i in range(30)] + [2.39 - i * 0.065 for i in range(30)]
            + [0.5 + i * 0.065 for i in range(30)])
    full = _pts(vals)
    t = NT.trend_of(full, full, spec)
    kinds = [(r['kind'], r['date']) for r in t['rows']]
    assert any(k == 'peak' for k, _ in kinds), t['rows']
    assert all('近 10 场' in r['text'] for r in t['rows']), t['rows']
    assert all('%' in r['text'] for r in t['rows']), '措辞里的单位读这条序列自己的 unit'
    assert t['rows'][0]['prev_date'] is None and t['rows'][0]['step'] is None
    assert 'th_note' in t and str(spec['trend']['th']) in t['th_note'], t['th_note']
    # 窗口只决定画哪一段：整段算完再切片，同一个顶不许换个日期
    half = full[:40]
    t2 = NT.trend_of(full, half, spec)
    assert all(r['date'] <= half[-1]['trade_date'] for r in t2['rows']), '窗口外的拐点不许列进这一档的清单'
    assert len(t2['ma']) == len(half)


def test_趋势两把尺全站只有一处实现():
    """均线与 zigzag 搬进 `duanxian/trend.py`（2026-10-31）：两个屏对同一段走势必须报同一批拐点。"""
    src = (ROOT / 'duanxian' / 'national_team.py').read_text(encoding='utf-8')
    rs = (ROOT / 'duanxian' / 'risk_signal.py').read_text(encoding='utf-8')
    trend = (ROOT / 'duanxian' / 'trend.py').read_text(encoding='utf-8')
    for name, text in (('national_team', src), ('risk_signal', rs)):
        assert re.search(r'from duanxian\.trend import|from \.trend import', text), \
            f'{name} 没走共用那份（自己抄一份 zigzag 迟早报出不同的拐点）'
        assert 'def zigzag_turns' not in text, f'{name} 里还留着一份 zigzag 定义'
    assert 'def zigzag_turns' in trend and 'def moving_average' in trend
    # 后端把 smooth/th 随接口交出（界面不写死），且每条序列都配了
    assert all(s.get('trend') and s['trend']['smooth'] and s['trend']['th'] for s in NT.SERIES), \
        '每条趋势线的窗口与门槛都要在自己的描述符里，且单位不同不许共用一个数'


def test_真库主尺这一条真的画得出趋势():
    """不打桩：读真库确认「占比线现在有底了」这件事实真的成立（换尺没做成的话这里必红）。"""
    if not _db_up():
        pytest.fail('库不通：这条真库核对不许兜成跳过')
    d = NT.panel(range='all')
    for s in d['scales']:
        assert s['ratio_sessions'] > 400, f'{s["key"]} 占比线还是只有 {s["ratio_sessions"]} 场 ⇒ 分母没补上'
        assert s['ratio_total_sessions'] < s['ratio_sessions'], '备用尺应当明显短一截（总市值反推不出来）'
        t = s['series_view']['ratio_float']['trend']
        assert t['available'] is True and len(t['ma']) == len(s['points']), t.get('reason')
        assert len(t['rows']) >= 2, f'全史占比线上一个转折点都没确认出来，门槛定歪了：{t["rows"]}'
        dates = {p['trade_date'] for p in s['points']}
        assert all(r['date'] in dates for r in t['rows']), '转折点必须落在真存在的那一天'
        assert all(v is None or isinstance(v, float) for v in t['ma']), '均线值出 NaN 会把 JSON 打坏'
        tb = s['series_view']['ratio_total']['trend']
        assert tb['available'] is False and '不画' in tb['reason'], tb


def test_提示那一排的格子措辞由后端交_短版不丢阈值与单位(monkeypatch):
    """2026-10-31 用户点名「整体你好好检查，我感觉效果不佳」：旧版逐条把整句摊在图上方，
    一句里九成是每个事件都一样的「阈值样本：…」⇒ 八个事件 800 像素黄字，图被挤出第一屏。
    改成一行一个事件的格子之后，钉三件事：
     ① 短版（`phrase_short`）与整句（`phrase`）都在，缩法只住这一份描述符（页面自己截列名就是第二份真相源）；
     ② 短版不许把阈值与单位弄丢（少了阈值的那一行等于把判据说成"就是动得大"）；
     ③ 整句拆成 head/body/tail 交出去，那句每事件都一样的 `tail` 才谈得上让屏幕只念一遍。"""
    rows = _two_scale_rows(days=120)
    for r in rows[-3:]:                 # 最后三天各砸一笔大额 ⇒ 必然造出一个事件
        r['net_yi'] = 300.0
    _stub_rows(monkeypatch, rows)
    d = NT.panel(range='all')
    items = d['alert']['items']
    assert items, '这批桩必须至少造出一个事件，否则下面全是空对空'
    for it in items:
        assert it['head'] and it['body'], '三段都要在：屏上摆 body 的格子、抽屉念整句'
        assert it['text'].startswith(it['head'] + it['body'] + it['tail'])
        for p in it['parts']:
            long_, short_ = p['phrase'], p['phrase_short']
            assert short_ and len(short_) < len(long_), (short_, long_)
            assert ('pp' in short_) == ('pp' in long_), '单位两边必须是同一把（短版不许换算）'
            if p['threshold'] is not None:
                assert f"{p['threshold']:.3f}" in long_ + short_ or f"{p['threshold']:.2f}" in long_ + short_, \
                    '阈值必须还在句子里'
        if it['family'] != 'band':
            assert '阈值样本' in it['tail'], '那句跨度信息由后端交，页面不许自己拼样本数与日期'
    # 短名描述符：五条序列都要有，且不比重名长（长了就白缩）
    for s in NT.SERIES:
        assert s.get('compact'), s['key']
        assert len(s['compact']) <= len(s['label']), s['key']
    # 同一条序列的 tail 是固定那一串（页面按整串去重才能收成一份）：真库那一条在下面核
    for it in items:
        if it['tail']:
            assert it['tail'].startswith('｜阈值样本：'), it['tail']


def test_真库提示格子短版与整句对得上():
    """不打桩的一条：真库那批事件上，短版与整句必须讲同一件事（同一个读数、同一个阈值）。"""
    if not _db_up():
        pytest.fail('库不通就报红：这一条是验收"正在服务的措辞"，缺数不许兜成跳过')
    d = NT.panel(range='all')
    got = 0
    for it in d['alert']['items']:
        for p in it['parts']:
            if p.get('delta') is None:
                continue
            assert f"{p['delta']:+.3f}" in p['phrase'] and f"{p['delta']:+.3f}" in p['phrase_short']
            got += 1
    assert got >= 4, f'真库这一段应该至少核到 4 个读数，只核到 {got}'


def test_提示那一排的每一格都拆成五样(monkeypatch):
    """用户 2026-11-01 点名：「这里还是不够清晰，要显示：指标名、时间、数值、与阈值的比较结果、
    结果含义（如异动提醒）」，并且"其他类似的提醒也要按照这个标准来写"。

    钉四件事（这一份描述符只住 `_part_cells`，页面只负责把格子摆成列）：
     ① 三族（占比跳变／净申赎极值／当期档）每一格五样齐全，缺一样就红——缺的那一格在屏上
        会变成页面自己编的话；
     ② `compare_text` 必须同时含**阈值本身**和**越过的幅度**（只说"超了"等于把判据藏起来）；
     ③ `value_text` 带单位并说清"这是什么数"（当日读数／单日变动／当期读数三种单位不同）；
     ④ `alert_type` 只有两个合法名字，含义那句由它开头（判"算不算异动"的权力不许搬进页面）。
    """
    rows = _two_scale_rows(days=140)
    for r in rows[-3:]:
        r['net_yi'] = 300.0
    # 占比那一族要单独造形状：桩默认只有最后 10 场有分母 ⇒ 有效变动样本不够，那一族永远不会响，
    # 于是"五格"在占比这一型上从来没被核过。这里把 20 场起都铺上值，最后一天跳一个半点。
    for i in range(20, 140):
        val = round(0.60 + 0.001 * (i - 20) + (0.5 if i == 139 else 0.0), 4)
        for r in rows[i * 2:i * 2 + 2]:
            r['ratio_float_pct'] = val
            r['ratio_total_pct'] = round(val * 0.92, 4)
            r['float_mv_wy'] = 48.0
            r['missing'] = None
    _stub_rows(monkeypatch, rows)
    d = NT.panel(range='all')
    items = d['alert']['items']
    assert items, '这批桩必须至少造出一个事件'
    seen_families = set()
    for it in items:
        assert it['alert_type'] in ('异动提醒', '当期档提醒'), it['alert_type']
        seen_families.add(it['family'])
        for p in it['parts']:
            for k in ('metric', 'value_text', 'compare_text', 'meaning_text', 'alert_type'):
                assert p.get(k), f'{k} 那一格是空的：{p}'
            assert p['metric'].startswith(p['scale_short']), p['metric']
            assert p['alert_type'] == it['alert_type'], '一格一个类型，事件行与格子不许各说一套'
            assert p['meaning_text'].startswith(p['alert_type']) or it['family'] != 'band', p
            assert any(u in p['value_text'] for u in ('pp', '亿份', '%')), p['value_text']
            if p['threshold'] is not None:
                thr_txt = f"{p['threshold']:.3f}" if it['family'] != 'flow' else f"{p['threshold']:.2f}"
                assert thr_txt in p['compare_text'], (thr_txt, p['compare_text'])
    assert {'ratio', 'flow'} <= seen_families, f'三族里至少核到占比与净申赎两族，只核到 {seen_families}'


def test_当期档那一格也说清与哪个切点比():
    """`_part_cells` 直接打三型（不靠真库刚好落到极端档）：
    超高／超低／没有档（阈值算不出来）三种都要有自己的说法，且缺切点时不许念成"刚好在档外"。"""
    base = {'scale_key': 'big4', 'scale_short': '四大', 'series': 'ratio_float',
            'series_label': '占沪深300流通市值', 'family': 'band', 'kind': 'band',
            'value': 2.24, 'delta': None, 'gap': None}
    high = NT._part_cells({**base, 'threshold': 2.194, 'band_key': 'ultra_high', 'band_label': '超高'})
    assert high['alert_type'] == '当期档提醒' and high['metric'] == '四大·占流通市值'
    assert 'p90' in high['compare_text'] and '2.194' in high['compare_text']
    assert '0.046' in high['compare_text'], '高出多少必须算给读者看，不许只说"在超高档"'
    low = NT._part_cells({**base, 'value': 0.31, 'threshold': 0.48,
                          'band_key': 'ultra_low', 'band_label': '超低'})
    assert 'p10' in low['compare_text'] and '比切点低' in low['compare_text']
    none = NT._part_cells({**base, 'threshold': None, 'band_key': 'ultra_high'})
    assert '没建起历史档' in none['compare_text'], none['compare_text']
    assert '—' not in none['compare_text'], '缺切点要念"判不了"，不许念成一个像数的破折号'


def test_真库五格齐备_每一行都读得出五样():
    """不打桩的一条：正在服务的这批事件上，五格一律非空（真库的措辞分支比桩多，最容易漏的就是这里）。"""
    if not _db_up():
        pytest.fail('库不通就报红：这一条核的是正在服务的 payload')
    d = NT.panel(range='all')
    rows = 0
    for it in d['alert']['items']:
        for p in it['parts']:
            for k in ('metric', 'value_text', 'compare_text', 'meaning_text', 'alert_type'):
                assert p.get(k), f'{it["date"]} 的 {p.get("series")} 缺 {k} 这一格'
            rows += 1
    assert rows >= 4, f'真库这一段应该至少核到 4 格，只核到 {rows}'
