# -*- coding: utf-8 -*-
"""明日展望·读侧（`duanxian/tomorrow_outlook.py`，11-05 新建）的钉住用例。

用户 11-05 的原话是「在量化选股中增加一个明日展望，就是把任务推送 10 里面的内容展示出来，
并针对提到的个股，把这些个股的各类指标全部展示出来」。这一族的形状是：
两张榜的判据从 `ingest/jobs/push/t10_tomorrow_outlook.py` **搬**到读侧，那一炉反过来 import 这一份。
所以这里钉的是搬家这件事本身，以及逐票全指标那一块的诚实性：

① **一处实现**：那一炉的 `_hits_shortlist` 就是这里的 `hits_shortlist`（同一个函数对象），
   而且那一炉的源码里再没有第二份榜 SQL —— 两份迟早变成"屏上八只、群里七只"。
② **参数只有一个真源**：那 8 个 `t10_*` 键读 `app/intraday/broadcast_config.json`；
   读不到时用的是代码默认值，**并且当场一条硬声明**（静默兜底＝屏上与群里两把尺）。
③ **缺数不补 0**：某只票当日没有那一项的行 ⇒ 交不出键、换算成"缺哪几项"，界面是 `—`；
   名次只给能跨票比的那几族（`config.indicator.output_unit` 决定），绝对价格给名次就是假话。
④ **推送与屏读同一张榜**（真库、不打桩）：两边取到的代码集合必须逐只相等。

`tests/test_tomorrow_outlook.py` 钉的是壳（默认关着、宁缺毋假、先落库再发、补发只读库），
那一份不动；这一份只管搬过来的判据与读侧出口。
"""
from __future__ import annotations

import datetime as dt
import inspect
import json
import math
from pathlib import Path

import pytest

from app.db import engine
from duanxian import tomorrow_outlook as TO

ROOT = Path(__file__).resolve().parents[1]
T10_SRC = (ROOT / 'ingest' / 'jobs' / 'push' / 't10_tomorrow_outlook.py').read_text(encoding='utf-8')
VIEW_SRC = (ROOT / 'duanxian' / 'tomorrow_outlook.py').read_text(encoding='utf-8')


# ---------------------------------------------------------------- ① 判据只住一处

def test_那一炉的取数就是读侧这一个函数对象():
    """别名搬家：`inspect.getsource` 顺着别名拿到的是真 SQL，
    所以 `test_tomorrow_outlook.py::test_两榜SQL都把股性判据钉在查询里` 那条钉子还指着实际跑的那句话。"""
    from ingest.jobs.push import t10_tomorrow_outlook as T
    assert T._hits_shortlist is TO.hits_shortlist
    assert T._trend_shortlist is TO.trend_shortlist
    assert T._top_boards is TO.top_boards
    assert T._family is TO.family
    assert TO.hits_shortlist.__module__ == 'duanxian.tomorrow_outlook'


def test_那一炉的源码里不留第二份榜SQL():
    """搬干净了才算一处实现：留一份"看着像备份、其实没人调"的 SQL 是下一轮分叉的起点。"""
    assert 'WITH d AS' not in T10_SRC, 't10 里还留着指标榜那份 CTE'
    assert 'selected5 AND t.above_ma5' not in T10_SRC, 't10 里还留着趋势榜那份条件'
    assert 'FROM analytics.limit_up_pool_daily l' not in T10_SRC, 't10 里还留着最高标那份 SQL'


def test_读侧搬过来的两份榜仍把股性判据钉在查询里():
    """搬家不许把"SQL 层筛档"退化成"Python 事后删"：
    事后删会把 LIMIT 先取走的 middle/weak 换成别人，名单语义就变了（原来那条钉的同一件事）。"""
    for fn in (TO.hits_shortlist, TO.trend_shortlist):
        src = inspect.getsource(fn)
        assert 'stock_character_daily' in src, f'{fn.__name__} 没接股性表'
        assert 'char_level' in src and 't10_char_keep' in src, f'{fn.__name__} 没用 t10_char_keep 判据'


def test_两张榜的股性与涨停池join都带判据版本条件():
    """主键含 spec_version 的表，join 不带版本条件＝"同一天两个版本的行数加在一起"，
    榜会按扇出重复出现在榜上的票（11-05 搬家时顺手补的那两条，这里是它的账）。"""
    for fn in (TO.hits_shortlist, TO.trend_shortlist):
        assert 'c.spec_version' in inspect.getsource(fn), f'{fn.__name__} 的股性 join 没带版本条件'
    pool = inspect.getsource(TO.top_boards)
    assert pool.count('spec_version') >= 2, '涨停池那一族两处（子查询与主查询）都要限版本'


# ---------------------------------------------------------------- ② 参数一个真源

def test_配置里那八个键一个都不缺且有兜底():
    cfg, unread = TO.load_config()
    assert unread is None, f'配置文件现在就该读得通（读不到是环境问题，别让它悄悄溜过）：{unread}'
    assert set(cfg) == set(TO.PARAM_DEFAULTS)
    # 默认值与配置一致 ⇒ 万一哪天读不到，兜底不会换一把尺（这一条是"兜底无害"的证据）
    for k, v in TO.PARAM_DEFAULTS.items():
        assert cfg[k] == v, f'{k}：配置 {cfg[k]} ≠ 代码默认 {v} ⇒ 读不到时静默换尺'


def test_配置文件读不到时走默认并交一条硬声明(monkeypatch, tmp_path):
    """检测器坏了不许兜成"看起来正常"：那种时候屏上摆的门槛可能不是 19:00 那一炉用的。"""
    monkeypatch.setattr(TO, 'CFG_FILE', tmp_path / '没有这个文件.json')
    cfg, unread = TO.load_config()
    assert unread and 'FileNotFound' in unread
    block = TO.params_block(cfg, unread)
    assert block['config_read'] is False and block['family_size'] == 9
    assert block['char_keep'] == ['strong'], '兜底默认必须与在册配置同一条口径'


def test_家族清单只从配置读一处写死都没有():
    fam, _ = TO.load_config()
    assert TO.family(fam) == fam['t10_family']
    assert TO.family({'t10_family': []}) == list(TO.FAMILY_DEFAULT)
    # 兜底那份与配置那份必须同字（改了配置忘了这里＝推送与屏读分叉的第一个入口）
    assert list(TO.FAMILY_DEFAULT) == fam['t10_family'], 'FAMILY_DEFAULT 与 broadcast_config 分叉了'


# ---------------------------------------------------------------- ③ 出口诚实性

def test_出口清洗收得下_Decimal_日期并且_NaT_变_null():
    import decimal
    assert TO._num(decimal.Decimal('94.6646'), 4) == 94.6646
    assert TO._num(dt.date(2026, 11, 5)) == '2026-11-05'
    assert TO._num(float('nan')) is None and TO._num(float('inf')) is None
    import pandas as pd
    assert TO._num(pd.NaT) is None, 'NaT 是 datetime 实例：闸排在日期判断之后就带着它进 JSON 了'
    assert TO._num(pd.Timestamp('2026-11-05 19:00')) .startswith('2026-11-05')


@pytest.mark.parametrize('bad', ['', '{ not json'])
def test_配置文件坏内容也走同一条出声的路(monkeypatch, tmp_path, bad):
    p = tmp_path / 'cfg.json'
    p.write_text(bad, encoding='utf-8')
    monkeypatch.setattr(TO, 'CFG_FILE', p)
    cfg, unread = TO.load_config()
    assert unread, '坏内容不许当"没这一档"静默走过'
    assert cfg['t10_min_hits'] == TO.PARAM_DEFAULTS['t10_min_hits']


def test_可排名单位那一份常量与SQL里的字面量同源():
    """`unit in (...)` 那半句是从常量拼出来的，不许有人改常量不改 SQL（改了就是两处口径）。"""
    assert TO._UNITS_SQL == ', '.join(f"'{u}'" for u in TO.RANKABLE_UNITS)
    for u in TO.RANKABLE_UNITS:
        assert f"unit in ({TO._UNITS_SQL})" in TO.INDICATOR_SQL
    # 绝对价格与"本身就是名次"那两族一律不在名单里
    assert 'yuan' not in TO.RANKABLE_UNITS and 'count' not in TO.RANKABLE_UNITS


def test_读不到库时不拿上一次的值顶(monkeypatch):
    """engine.query 返回 None（连不上）时交空表，并且**不写进缓存** ——
    写进去下次就变成"这批票一个指标都没有"那句假读数。"""
    TO._CACHE.clear()
    calls: list[int] = []

    def boom(sql, params, role='reader'):
        calls.append(1)
        return None

    monkeypatch.setattr(TO.engine, 'query', boom)
    assert TO.indicators_for(['600000.SH'], '2026-11-05') == {}
    assert TO.indicators_for(['600000.SH'], '2026-11-05') == {}
    assert len(calls) == 2, '读失败那一趟被钉进缓存了：库修好后一小时内还念旧的'
    TO._CACHE.clear()


def test_整份读数的缓存要能当场追平(monkeypatch):
    TO._CACHE.clear()
    built: list[int] = []
    monkeypatch.setattr(TO, '_build', lambda date='', expand=True, refresh=False: (
        built.append(1) or {'available': True, 'date': '2026-11-05', 'hits': [], 'trends': []}))
    TO.view()
    TO.view()
    assert len(built) == 1, '第二次没走缓存'
    out = TO.view(refresh=True)
    assert len(built) == 2 and out['cached'] is False, '带 refresh 必须重扫并老实说"这不是缓存"'
    TO.view()
    assert TO.view()['cached'] is True
    TO._CACHE.clear()


def test_不可用的那一趟不钉进缓存(monkeypatch):
    """库不通／两榜都缺场次时交的 available:false 不许留在缓存里 ——
    补跑完还得等一小时才看得见，那就是"页面说没有、其实有"。"""
    TO._CACHE.clear()
    monkeypatch.setattr(TO, '_build', lambda date='', expand=True, refresh=False:
                        {'available': False, 'reason': '两榜的输入表那天都没场次'})
    for _ in range(2):
        assert TO.view()['available'] is False
    assert not [k for k in TO._CACHE if k.startswith('view|')], '失败结果进了缓存'
    TO._CACHE.clear()


# ---------------------------------------------------------------- ④ 真库（不打桩）

def _latest() -> dt.date:
    d = engine.scalar('select max(trade_date) from analytics.indicator_value', (), role='reader')
    if d is None:
        pytest.fail('指标表一行都没有 —— 这一族测试要的是本机真库，环境不对就是红')
    return d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])


def test_真库_逐票全部指标_绝对价格那族一条都不给名次():
    day = _latest()
    codes = [r['security_code'] for r in (engine.query(
        'select distinct security_code from analytics.indicator_value where trade_date = %s limit 6',
        (day,), role='reader') or [])]
    assert codes, '这一场没有指标行，环境不对'
    by = TO.indicators_for(codes, day.isoformat(), refresh=True)
    got = [i for rows in by.values() for i in rows]
    assert got, '有代码却没读到指标行'
    bad = [i for i in got if not i['rankable'] and i['rank'] is not None]
    assert not bad, f'单位不跨票可比的那几项被排了名次（高价股天然靠前）：{bad[:3]}'
    yuan = [i for i in got if i['unit'] == 'yuan']
    assert yuan, '这一场连一条均线族都没读到，母体不对'
    assert all(i['rank'] is None for i in yuan)
    # 给名次的那几项必须逐项带分母（各项母体不一样，5,571 与 5,393 不能混用一个数解释）
    ranked = [i for i in got if i['rank'] is not None]
    assert ranked and all(isinstance(i['family_n'], int) and i['family_n'] > 0 for i in ranked)
    assert len({i['family_n'] for i in ranked}) > 1 or len(ranked) < 3, \
        '所有名次共用一个分母 —— 各项母体本就不一样，八成是 SQL 把分母写死了'


def test_真库_在册指标都有出处且中文名来自_config():
    keys = TO.all_indicator_keys()
    assert keys, 'config.indicator 读不到'
    names = TO.indicator_names(keys)
    missing = [k for k in keys if k not in names]
    assert not missing, f'这些指标键在 config.indicator 里没有中文名（界面会念机器码）：{missing}'
    # 家族那 9 项也必须有出处
    fam, _ = TO.load_config()
    nofam = [k for k in TO.family(fam) if k not in names]
    assert not nofam, f'家族里这几项没登记在 config.indicator：{nofam}'


def test_真库_推送与屏读拿到的是同一张榜():
    """这一条是本族的核心：两边共用一份判据 ⇒ 代码集合必然逐只相等。
    分开算一遍再"对得上"是假的对账，这里比的是同一份函数产出的两份形状。"""
    day = _latest()
    cfg, _ = TO.load_config()
    hits, _ = TO.hits_shortlist(day, cfg)
    trends, _ = TO.trend_shortlist(day, cfg)
    if hits is None and trends is None:
        pytest.skip(f'{day} 这一场两榜都缺场次（加工链没跑到）—— 没有可比的对象')
    view = TO.view(day.isoformat(), refresh=True)
    assert view['available'] is True, view.get('reason')
    assert [r['security_code'] for r in view['hits']] == [r['security_code'] for r in (hits or [])], \
        '屏上一、的名单与判据直接产出的名单不一致（读侧又排了一次？）'
    assert [r['security_code'] for r in view['trends']] == [r['security_code'] for r in (trends or [])]


def test_真库_并集里每只票都摊到指标且缺项点名():
    day = _latest()
    v = TO.view(day.isoformat(), refresh=True)
    if not v['available']:
        pytest.skip(f'{day} 没有可用榜')
    codes = {r['security_code'] for r in v['hits']} | {r['security_code'] for r in v['trends']}
    assert set(v['stocks']) <= codes
    keys = set(TO.all_indicator_keys())
    for code, s in v['stocks'].items():
        got = {i['key'] for i in s['indicators']}
        assert got <= keys, f'{code} 读到了 config.indicator 里没登记的指标键'
        assert s['indicator_count'] == len(s['indicators'])
        # 缺的那几项必须真的没有行，且不许被写成 0
        assert set(s['missing_indicators']) == keys - got, f'{code} 缺项点名与实读对不上'
        for i in s['indicators']:
            assert i['value'] is None or isinstance(i['value'], (int, float)), f'{code}/{i["key"]} 值不是数'
    # 交集只念一只（两榜都有不算两次）
    both = {c for c in codes if any(r['security_code'] == c for r in v['hits'])
            and any(r['security_code'] == c for r in v['trends'])}
    assert {o['code'] for o in v['overlap']} == both


def test_真库_每一条声明都配了中文标签():
    """屏幕那一格读的是 `label ?? code`：漏配就等于在界面上念机器码。"""
    day = _latest()
    v = TO.view(day.isoformat(), refresh=True)
    for c in v['caveats']:
        assert c.get('label') and c['label'] != c['code'], f'{c["code"]} 没配上屏的中文标签'
        assert c.get('text')
        assert '**' not in c['text'], f'{c["code"]} 的声明里带着裸 markdown 星号'
    # 硬声明必须是"库不健康"语义那一族（前端白名单才会留在屏上）
    for c in v['caveats']:
        if c.get('hard'):
            assert any(w in c['code'] for w in ('missing', 'unread', 'truncated', 'absent')), c['code']


def test_真库_基准日与母体口径都交得出来():
    v = TO.view(refresh=True)
    assert v['available'] is True
    assert v['date'] == _latest().isoformat()
    assert isinstance(v['generated_at'], str) and v['ttl_seconds'] > 0
    assert v['universe']['indicator_codes_day'] and v['universe']['indicator_codes_day'] > 1000, \
        '当日母体只数交不出来或明显不对 —— "榜上 8 只"没有分母就是黑话'
    assert v['rank_note'], '名次规则文案只住 duanxian/ranking.py 一份，读侧必须转交'
