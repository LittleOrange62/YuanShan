# -*- coding: utf-8 -*-
"""选股汇总（`analytics.confluence_daily`）的写侧口径与读侧诚实性。

用户 2026-10-13 的裁定有两条会被写错而不报错，这里各自钉死：

* **源清单只住 `SOURCES` 一处**：`limit` 与那一屏页面的默认取数上限是同一件事，
  两边各写一遍一定会漂 —— 漂了这张表说的「重复出现」就是拿旧名单拼新数据。
* **赋分只有「命中榜数 + 各榜最好名次」，没有合成总分**：排序键必须先是 hits，
  第二键才是名次分位；谁把它改成"综合分优先"，第 1 名就会变成一只只进了一个榜的票。

外加三件本项目已经付过学费的事：缺源要记进 `sources_absent` 并让 `source_count` 缩、
jsonb 到 `write` 才 dumps（Python 侧一直是 dict）、读侧每一句都带 `spec_version`。
"""
import datetime as dt
import inspect
import re
from pathlib import Path

import pytest

from app.compute import confluence as CF
from app.db import engine
from duanxian import confluence_view as CV

DAY = dt.date(2026, 10, 12)
ROOT = Path(__file__).resolve().parents[1]


def _src(key):
    return next(s for s in CF.SOURCES if s.key == key)


# ------------------------------------------------------------------ 源清单与端点不许分叉

def test_各榜取数上限与那一屏的默认值逐条相等():
    """`SOURCES[*].limit` 就是那一屏默认交出的行数。漂了 = 汇总数的是另一份名单。

    读文件而不是 import：`vr.app` 一 import 就会去拉上游 `astock`（本机没有那个模块），
    而这条要钉的只是"两处写的数字一样"。
    """
    vr = (ROOT / 'vr' / 'app.py').read_text(encoding='utf-8')

    def default_limit(endpoint: str) -> int:
        at = vr.index(f'@app.get("{endpoint}")')
        head = vr[at:vr.index('"""', at)]        # 到 docstring 之前＝整段签名
        # 两种写法都有过：`Query(150, ge=…)` 与 `Query(default=200, ge=…)`；
        # 而签名里已经有别的 `Query(...)`，所以不能用"到第一个右括号为止"去截。
        m = re.search(r'limit: int = Query\((?:default=)?(\d+)', head)
        assert m, f'{endpoint} 的默认 limit 没抓到，端点签名改了？\n{head[:300]}'
        return int(m.group(1))

    assert _src('voldbl').limit == default_limit('/api/quant/volume-double')
    assert _src('ambush').limit == default_limit('/api/quant/ambush')
    assert _src('quadrant').limit == default_limit('/api/quant/quadrant')
    # 页面没有自己传 limit 的两屏：端点默认值就是屏幕上的榜长。
    for f in ('pages/Quadrant.tsx', 'components/quant/AmbushBoard.tsx'):
        src = (ROOT / 'frontend' / 'src' / f).read_text(encoding='utf-8')
        assert not re.search(r'[?&]limit=', src), f'{f} 开始自带 limit 了，这条钉法要跟着改'
    # 倍量那一屏 10-14 起了「取数上限」控件（用户要「≥5 次的都展示出来」，默认 150 会静默切掉后面 231 只），
    # 所以它**确实**发 limit。按这条测试自己写的处置办法：拿页面那个默认值来比，而不是把 SOURCES 改小。
    vd = (ROOT / 'frontend' / 'src' / 'pages' / 'VolumeDouble.tsx').read_text(encoding='utf-8')
    m = re.search(r'const \[limit, setLimit\] = useState\((\d+)\)', vd)
    assert m, '倍量那一屏的取数上限默认值没抓到（控件改了形状？这条要跟着改，别删）'
    assert int(m.group(1)) == default_limit('/api/quant/volume-double') == _src('voldbl').limit, \
        f'页面默认取 {m.group(1)} 行，而 confluence 数的是 {default_limit("/api/quant/volume-double")} 行的名单 —— 汇总与屏幕不是同一批票'
    assert re.search(r'&limit=\$\{limit\}', vd), '控件发了却没接进 URL（那就是一个假开关）'


def test_不截断的两个源必须是全场口径且写明原因():
    for key in ('first_break', 'dgt'):
        s = _src(key)
        assert s.limit is None, f'{key} 这一场该源有多少交多少，写死上限会漏掉同日上榜的票'
        assert '%(limit)s' not in s.sql


def test_每个源的选法都自带名次与判据原值():
    for s in CF.SOURCES:
        assert '%(day)s' in s.sql, f'{s.key} 没带日期条件'
        assert 'row_number()' in s.sql, f'{s.key} 的名次不是在这条链自己的排序下数出来的'
        assert 'metric' in s.sql, f'{s.key} 没把判据原值交回（榜就是按它排的）'
        assert s.board and s.label and s.meaning


def test_带版本的源必须写死自己那一版():
    """源 SQL 里的判据版本与那个模块现在的 SPEC_VERSION 不许漂 ——
    漂了汇总数到的是旧版名单，而屏幕上写的是新版那两栏。"""
    from app.compute import ambush_setup as amb
    from app.compute import breakout_signal as bs

    assert f"spec_version = '{amb.SPEC_VERSION}'" in _src('ambush').sql
    assert f"spec_version = '{bs.SPEC_VERSION}'" in _src('first_break').sql


# ------------------------------------------------------------------ merge 的排序口径

def _picks(*items):
    """items: (source_key, [(code, rank, metric)])"""
    return {k: [{'security_code': c, 'rn': r, 'metric': m, 'reasons': 1, 'strict': False}
                for c, r, m in v] for k, v in items}


def test_排序先是命中榜数其次才是名次():
    # A 只进象限第 1；B 进象限第 2 与倍量第 1（两个榜）
    per = _picks(('quadrant', [('A', 1, 90.0), ('B', 2, 80.0)]),
                 ('voldbl', [('B', 1, 6.0)]))
    rows = CF.merge(DAY, per)
    codes = [r['security_code'] for r in rows]
    assert codes == ['B', 'A'], f'命中 2 榜的必须排在只命中 1 榜的前面，哪怕它是象限第 1 名：{codes}'
    assert [r['hits'] for r in rows] == sorted([r['hits'] for r in rows], reverse=True)


def test_名次分位是名次除以该榜榜长_低才是好():
    per = _picks(('quadrant', [('A', 3, 9.0), ('B', 1, 8.0)]),
                 ('first_break', [('A', 1, 1.8)]))
    a = next(r for r in CF.merge(DAY, per) if r['security_code'] == 'A')
    assert a['sizes'] == {'quadrant': 2, 'first_break': 1}
    # 象限第 3/2 名=150%，首破第 1/1 名=100% —— 榜长只有 1 时那一名就是 100%，
    # 这是「名次 ÷ 榜长」的必然结果，所以读这一格要看分母（页面上写的是 1/1）。
    assert a['best_source'] == 'first_break'
    assert abs(a['best_rank_pct'] - 100.0) < 0.01
    b = next(r for r in CF.merge(DAY, per) if r['security_code'] == 'B')
    assert abs(b['best_rank_pct'] - 50.0) < 0.01


def test_没有合成总分这一格():
    rows = CF.merge(DAY, _picks(('quadrant', [('A', 1, 9.0)])))
    assert not any(k in rows[0] for k in ('score', 'total_score', 'combined', 'strength')), \
        '造一个合成分会把"只在象限第 1"和"四个榜都第 120"并排放'


def test_hit_sources的顺序就是名次由好到差():
    per = _picks(('dgt', [('A', 1, 5.0)]), ('voldbl', [('A', 20, 6.0)]),
                 ('quadrant', [('A', 5, 7.0)]))
    row = CF.merge(DAY, per)[0]
    assert row['hit_sources'] == ['dgt', 'quadrant', 'voldbl'], row['hit_sources']
    assert row['hits'] == len(row['hit_sources']), 'CHECK 约束就是这条，写进去会整场失败'


def test_缺源时_hits_的分母跟着缩并点名():
    # 真实形状：day_sources 只会把**成功**的源放进 per_source，失败的整包记在 __failed__
    per = {s.key: [] for s in CF.SOURCES if s.key != 'ambush'}
    per['quadrant'] = [{'security_code': 'A', 'rn': 1, 'metric': 9.0}]
    per['__failed__'] = [{'ambush': 'OperationalError: 表不存在'}]
    rows = CF.merge(DAY, per)
    r = rows[0]
    assert r['source_count'] == len(CF.SOURCES) - 1
    assert r['sources_absent'] == 'ambush'
    assert r['hits'] == 1, '缺席的源不参与 hits，也不填 0'


def test_全部源都失败时不是交一张空榜冒充没有():
    per = {'__failed__': [{s.key: 'boom' for s in CF.SOURCES}]}
    rows = CF.merge(DAY, per)
    assert rows == []          # 没有名单可并 —— 上层要拿 absent 去出声，不是靠这里造行


def test_fetch_source_失败要抛不许兜成空表():
    src = _src('voldbl')

    def boom(sql, params, role='reader'):
        raise RuntimeError('库不通')

    orig = engine.query
    engine.query = boom
    try:
        with pytest.raises(RuntimeError):
            CF.fetch_source(src, DAY)
    finally:
        engine.query = orig


def test_day_sources_一个源崩不带走其余五个(monkeypatch):
    calls = []

    def fake(sql, params, role='reader'):
        calls.append(params['day'])
        if 'dragon_tiger_detail' in sql:
            raise RuntimeError('龙虎榜那张表读不到')
        return [{'security_code': '000001.SZ', 'rn': 1, 'metric': 1.0, 'reasons': 1, 'strict': True}]

    monkeypatch.setattr(CF.engine, 'query', fake)
    got = CF.day_sources(DAY)
    # 2026-10-14 从"其余四个"改成"其余五个"：交叉源加了 sysentry（交易体系榜），
    # 崩掉的是龙虎榜那一个，所以活下来的从 4 个变 5 个 —— 这是事实变了，不是放宽断言。
    assert set(got) - {'__failed__'} == {'voldbl', 'quadrant', 'ambush', 'first_break', 'sysentry'}
    assert 'dgt' in got['__failed__'][0]
    assert len(calls) == len(CF.SOURCES), '所有源都要问过，失败的那个不许跳过其余'


# ------------------------------------------------------------------ 写侧形状

def test_jsonb_三格到_write_才转字符串(monkeypatch):
    rows = CF.merge(DAY, _picks(('quadrant', [('A', 1, 9.0)])))
    for k in ('ranks', 'sizes', 'metrics'):
        assert isinstance(rows[0][k], dict), f'{k} 在 Python 侧必须是 dict，dry-run 才读得出形状'

    sent = {}

    def fake_exec(sql, params, role='app'):
        sent['sql'] = sql
        return 1

    def fake_many(sql, payload, role='app'):
        sent['sql'], sent['payload'] = sql, payload
        return len(payload)

    monkeypatch.setattr(CF.engine, 'execute', fake_exec)
    monkeypatch.setattr(CF.engine, 'executemany', fake_many)
    CF.write(DAY, rows)
    p = sent['payload'][0]
    assert isinstance(p['ranks'], str) and 'quadrant' in p['ranks']
    assert '::jsonb' in sent['sql'], '不写 ::jsonb 会 cannot adapt type dict'
    assert 'spec_version = %s' in sent['sql'] or 'spec_version' in sent['sql']


def test_delete_只删自己那一版(monkeypatch):
    sent = {}

    def fake_exec(sql, params, role='app'):
        sent['sql'], sent['params'] = sql, params
        return 0

    monkeypatch.setattr(CF.engine, 'execute', fake_exec)
    CF.write(DAY, [])
    assert 'spec_version' in sent['sql'], 'DELETE 不带版本条件会把旧版本那批一起删掉，无从对账'
    assert CF.SPEC_VERSION in sent['params']


# ------------------------------------------------------------------ 读侧（库里筛 + 三态）

def test_源筛选认不得的值要点名不参与():
    picked, rejected = CV._sources_arg('voldbl,不存在的源, quadrant ，voldbl')
    assert picked == ['voldbl', 'quadrant']
    assert rejected == ['不存在的源']


def test_读侧每条查询都带判据版本():
    src = inspect.getsource(CV)
    # 去掉注释行再数：docstring 里"必须带版本条件"那句话不是代码
    code = '\n'.join(l for l in src.split('\n') if not l.strip().startswith('#'))
    for frag in re.findall(r'f?"""(.*?)"""', code, re.S):
        if 'confluence_daily' in frag and 'select' in frag.lower():
            assert 'spec_version' in frag, f'这条读侧查询漏了版本条件：{frag[:80]}'


def test_历史那一条只认表里真有的天(monkeypatch):
    """`history()` 交出的 repeat_days 只能来自 hits>=2 的行 —— 不能拿"进了两次同一个榜"冒充。"""
    fake = [{'day': '2026-10-12', 'hits': 2, 'hit_sources': 'voldbl,dgt',
             'best_source': 'voldbl', 'best_rank': 6, 'best_rank_pct': 4.0,
             'source_count': 5, 'sources_absent': None},
            {'day': '2026-10-11', 'hits': 1, 'hit_sources': 'quadrant',
             'best_source': 'quadrant', 'best_rank': 88, 'best_rank_pct': 44.0,
             'source_count': 5, 'sources_absent': 'ambush'}]
    # 打桩要打**用它名字的那个模块**：confluence_view 是 `from .quant import _rows`，
    # patch quant._rows 对它无效（那是本项目反复踩过一次的桩错）。
    monkeypatch.setattr(CV, '_rows', lambda sql, params=(): list(fake))
    monkeypatch.setattr(CV, '_latest', lambda table, spec=None: '2026-10-12')
    got = CV.history('000678')
    assert got['available'] is True
    assert got['repeat_days'] == ['2026-10-12'] and got['repeat_total'] == 1
    assert got['partial_source_days'] == ['2026-10-11'], '那天有源缺席，横比时要能被认出来'
    assert got['rows'][0]['hit_labels'] == [CF.SOURCE_LABELS['voldbl'], CF.SOURCE_LABELS['dgt']]


# ------------------------------------------------------------------ 真库（不打桩）

def _latest_real():
    r = engine.query(f"select max(trade_date) d from {CF.TABLE} where spec_version = %s",
                     (CF.SPEC_VERSION,), role='reader')
    d = (r or [{}])[0].get('d')
    if d is None:
        return None
    return d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d))


def test_真库这一场确实只捞重复出现的那些():
    try:
        day = _latest_real()
    except Exception as e:        # noqa: BLE001  没库就跑不了这条，别让它红成"判据错了"
        pytest.skip(f'本地库不可读：{type(e).__name__}: {e}')
    if day is None:
        pytest_skip_reason = '这张表还没有已算好的场次（回算未跑完）'
        pytest.skip(pytest_skip_reason)
    got = CV.summary(str(day))
    assert got['available'] is True, got
    assert got['spec_version'] == CF.SPEC_VERSION
    # 全场分布必须来自不带筛选的聚合，且与 day_total 对得上
    dist = got['hits_distribution']
    assert dist and sum(dist.values()) == got['day_total'], (dist, got['day_total'])
    assert (got['day_repeat_total'] or 0) == sum(v for k, v in dist.items() if int(k) >= 2)
    for r in got['rows']:
        assert r['hits'] == len(r['hit_sources'])
        assert r['hits'] >= 1
    assert isinstance(got['sources'], list) and len(got['sources']) == len(CF.SOURCES)
