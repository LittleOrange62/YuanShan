# -*- coding: utf-8 -*-
"""「加权分达标后的逐日涨跌幅」（`quant.energy_track` + `day_returns.series_by_code`）。

用户 2026-10-19 的原话拆成三件事，三件都要钉（母体措辞按当晚第二稿：前 N 名，不是分数门槛）：

1. 母体是**基准日这一场得分前 N 名**（默认 120；同分并列一起进来，只数可以 > N）；
   起算日是这只票**全史第一次**排进前 N 名那一场 —— 两个时点不许折成一个
   （界面上一眼看不出来，错了也没人发现）。
2. 逐日那几格是**该票起算日之后的每一场**，不同票同一列不是同一日历日；
   涨跌幅用后复权、按市场场次找上一场（停牌那天没有行就是空格，不拿更早一天顶上）。
3. **不限行数**（分页在前端），默认按整体涨跌幅**从低到高**，算不出的排最后 ——
   让 None 冒到最前会把"没有基期"念成"跌得最少"。

外加一条真库不打桩的：手算一只票的首格与末格，与接口逐位对上。
"""
import pytest

from duanxian import pg, quant
from app.compute import volume_energy as venc


@pytest.fixture
def stubbed(monkeypatch):
    cap = {}
    day_rows = {}

    def _rows(sql, params=()):
        cap.setdefault('sqls', []).append(sql)
        if 'min(trade_date)::text as first_date' in sql:
            cap['first_sql'] = sql
            cap['first_params'] = params
            return cap.get('firsts') or []
        cap['sql'] = sql
        cap['params'] = params
        return cap['hits']

    def _series(spans, target):
        cap['spans'] = spans
        return cap.get('ser') or {}

    monkeypatch.setattr(pg, "enabled", lambda: True)
    # 2026-10-19 第二稿：空态走 `_empty_after_filter`，它现查 `_alive()`（真库连通性）分流措辞；
    # 桩里钉成"库是通的、只是筛空了"，这条测的才是筛空那句话本身，不随本机库状态漂。
    monkeypatch.setattr(quant, "_alive", lambda: True)
    monkeypatch.setattr(quant.pg, "session_has", lambda t, d: True)
    monkeypatch.setattr(quant, "_latest", lambda t: "2026-10-19")
    monkeypatch.setattr(quant, "_rows", _rows)
    monkeypatch.setattr(quant, "_coverage",
                        lambda t: {'volume_double': {'from': '2025-12-02',
                                                     'to': '2026-10-19', 'sessions': 211}})
    monkeypatch.setattr(quant, "_caveats", lambda k: [])
    monkeypatch.setattr(quant.day_returns, "series_by_code", _series)
    day_rows.clear()
    cap['hits'] = [{'code': '600000.SH', 'name': '测试股', 'board': '主板', 'market': 'SH',
                    'dbl_energy': 1.07, 'bars': 40, 'win_days': 40}]
    cap['firsts'] = [{'code': '600000.SH', 'first_date': '2026-02-10'}]
    cap['ser'] = {'600000.SH': {'days': [{'day_no': 0, 'trade_date': '2026-02-10',
                                          'pct': 3.82, 'cum': 3.82},
                                         {'day_no': 1, 'trade_date': '2026-02-11',
                                          'pct': -1.0, 'cum': 2.78}],
                                'base_day': '2026-02-09', 'missing_base': False}}
    # 10-26 起这一发接口的三段取数走进程内缓存（`_agg`/`_agg_call`，键里带场次＋指纹＋名次线）。
    # 缓存跨用例不还原 ⇒ 不清的话，第二个用例拿到的是第一个用例那副桩的数，
    # 于是"桩换了、断言却还绿"或反过来——两种都是假证据。setup/teardown 各清一次。
    quant.clear_agg_cache('energy_track_')
    yield cap
    quant.clear_agg_cache('energy_track_')


def test_母体与起算日是两条查询各自钉指纹():
    """跨批混算会拿旧尺的达标行减新尺的榜 —— 两条 SQL 都必须带 `energy_fp`。"""
    src = quant._step_gap_source()
    assert 'and v0.energy_fp = %s' in src


# 2026-10-19 第二稿：次数×分值，无归一化 —— 母体从"加权分≥0.8 门槛"换成"当日名次 ≤ 前 N 名"，
# 参数序里门槛位改成 top_n，回显键 threshold_default 换成 top_n_default。
def test_参数序与母体默认值来自后端一处(stubbed):
    out = quant.energy_track()
    fp = venc.fingerprint()
    assert stubbed['params'] == ('2026-10-19', fp, venc.DEFAULT_TOP_N), stubbed['params']
    assert out['top_n'] == venc.DEFAULT_TOP_N
    assert out['top_n_default'] == venc.DEFAULT_TOP_N, '界面填的默认档读这里，不自己写 120'
    assert out['page_size'] == venc.PAGE_SIZE, '分页档也只住后端一处（60 个一页、两页正好 120）'
    assert 'rank_in_day' in stubbed['sql'], '母体按当日名次取，不按分数门槛'
    assert 'where r.rank_in_day <= %s' in stubbed['sql']
    assert 'v.dbl_energy is not null' in stubbed['sql'], '没有加权分的行不参与名次（NULL 不折成 0 分挤进榜）'
    assert 'v.energy_fp = %s' in stubbed['sql'], '母体钉在生效指纹那一批里，混批排名是拿旧尺排新队'
    assert 'order by r.rank_in_day, r.code' in stubbed['sql']
    assert 'v.dbl_step_gap' not in stubbed['sql'], '派生列不在这张表里，写了就是发一条注定报错的 SQL'
    assert stubbed['first_params'][0] == fp, '起算日那条也必须钉指纹'
    assert stubbed['first_params'][1] == ['600000.SH'], '起算日按母体这批票现算（any(%s::text[])）'
    assert stubbed['first_params'][2] == venc.DEFAULT_TOP_N
    assert 'rank() over (partition by trade_date' in stubbed['first_sql'], \
        '第一次进榜要**逐场**按当天名次判（公用组件的 rank：同分同名、顺延），不能拿今天的分数比历史'


# 2026-10-19 第二稿：次数×分值，无归一化 —— 现给的是名次线 top_n（不是分数门槛），原样回显。
def test_名次线可以现给并原样回显(stubbed):
    out = quant.energy_track(top_n=60)
    assert stubbed['params'][2] == 60
    assert out['top_n'] == 60
    # 同分并列一起进来 ⇒ 只数可以超过 N：SQL 不 limit，交出的行数如实回显（分页在前端）
    assert 'limit' not in stubbed['sql'], '母体不许用 limit 截 —— 截了就把并列边界那批人切没（用户要"115 名有 10 个就一起进"）'
    stubbed['hits'] = [{'code': f'{i}.SH', 'name': f'票{i}', 'board': '主板', 'market': 'SH',
                        'dbl_energy': 1.0 - 0.01 * i, 'bars': 40, 'win_days': 40}
                       for i in range(1, 13)]
    stubbed['firsts'] = [{'code': f'{i}.SH', 'first_date': '2026-02-10'} for i in range(1, 13)]
    out12 = quant.energy_track(top_n=10)
    assert stubbed['params'][2] == 10
    assert out12['rows_returned'] == 12 > out12['top_n'] == 10
    assert out12['rows_capped'] is True, '只数超过 N 要如实标出来（名次并列整批进来的直接后果）'


def test_起算日交给取序列那一步是按票各算各的(stubbed):
    out = quant.energy_track()
    assert stubbed['spans'] == {'600000.SH': '2026-02-10'}, '每票自己的起算日，不是一刀切同一个日期'
    row = out['rows'][0]
    assert row['first_date'] == '2026-02-10'
    assert row['sessions_since'] == 1, 'sessions_since 是"起算日之后有几场"，不是总场数'
    assert row['chg_cum'] == 2.78, '整体涨跌幅＝末格的 cum（基期＝起算日前一场收盘）'
    assert row['series'][0]['pct'] == 3.82, '第 0 格是起算日当天的涨幅'
    assert out['max_series_len'] == 2


def test_起算日落在库里最早那场时必须说截断(stubbed):
    stubbed['firsts'] = [{'code': '600000.SH', 'first_date': '2025-12-02'}]
    stubbed['ser'] = {'600000.SH': {'days': [{'day_no': 0, 'trade_date': '2025-12-02',
                                              'pct': 1.0, 'cum': 1.0}],
                                    'base_day': '2025-12-01', 'missing_base': False}}
    out = quant.energy_track()
    assert out['rows'][0]['first_censored'] is True
    txt = next(c['text'] for c in out['caveats'] if c['code'] == 'energy_track_censored')
    assert '不晚于' in txt and '更早' in txt, txt


def test_没有基期时整体涨跌幅交空并点名而不是当0(stubbed):
    stubbed['ser'] = {'600000.SH': {'days': [{'day_no': 0, 'trade_date': '2026-02-10',
                                              'pct': None, 'cum': None}],
                                    'base_day': None, 'missing_base': True}}
    out = quant.energy_track()
    r = out['rows'][0]
    assert r['chg_cum'] is None and r['missing_base'] is True
    codes = [c['code'] for c in out['caveats']]
    assert 'energy_track_no_base' in codes
    txt = next(c['text'] for c in out['caveats'] if c['code'] == 'energy_track_no_base')
    assert '不当 0%' in txt or '不是 0%' in txt or '—' in txt, txt


def test_排序按整体涨跌幅升序且算不出的排最后(stubbed):
    stubbed['hits'] = [{'code': 'A.SH', 'name': '甲', 'board': '主板', 'market': 'SH',
                        'dbl_energy': 1.2, 'bars': 40, 'win_days': 40},
                       {'code': 'B.SH', 'name': '乙', 'board': '主板', 'market': 'SH',
                        'dbl_energy': 1.1, 'bars': 40, 'win_days': 40},
                       {'code': 'C.SZ', 'name': '丙', 'board': '主板', 'market': 'SZ',
                        'dbl_energy': 1.0, 'bars': 40, 'win_days': 40}]
    stubbed['firsts'] = [{'code': c, 'first_date': '2026-02-10'} for c in ('A.SH', 'B.SH', 'C.SZ')]
    stubbed['ser'] = {c: {'days': [{'day_no': 0, 'trade_date': '2026-02-10', 'pct': 1.0, 'cum': v}],
                          'base_day': '2026-02-09', 'missing_base': v is None}
                      for c, v in (('A.SH', 5.0), ('B.SH', -9.0), ('C.SZ', None))}
    rows = quant.energy_track()['rows']
    assert [r['code'] for r in rows] == ['B.SH', 'A.SH', 'C.SZ'], \
        '从低到高；算不出的（None）排最后，不许冒到最前被读成"跌得最少"'


# 2026-10-19 第二稿：次数×分值，无归一化 —— 空态念「前 N 名」（母体是名次线），不再念分数门槛/倍量次数；
# 「这一场有数据」与「不是没回填」两句要原样钉住（筛空 ≠ 没回填，这条纪律不变）。
def test_筛完为空说的是前N名而不是没回填(stubbed):
    stubbed['hits'] = []
    out = quant.energy_track(top_n=60)
    assert out['available'] is False
    assert '前 60 名' in out['reason'] and '这一场是有数据的' in out['reason'], out['reason']
    assert '不是没回填' in out['reason'], '筛空与没回填是两句话 —— 这句否定必须逐字在'
    assert '可排名' in out['reason'], '要说清是"这一批没有可排名的行/还没按当前配置重算"，不是笼统没数'
    assert '倍量次数' not in out['reason'] and '加权分≥' not in out['reason'], \
        '旧尺（次数）与第一稿（分数门槛）的措辞都不许复活——现在筛的是名次'


# ------------------------------------------------------------------ 真库（不打桩）

def test_真库整体涨跌幅与手算逐位一致():
    if not pg.enabled():
        pytest.skip('本机库没通，这条要真库')
    out = quant.energy_track()
    if not out.get('available'):
        pytest.skip('本机库没有倍量场')
    row = out['rows'][0]
    assert row['series'], row['code']
    d0, d1 = row['series'][0], row['series'][1]
    px = pg.fetch_df("""select trade_date::text d, (close * coalesce(adjustment_factor, 1))::float hfq
                         from market_data.stock_daily
                        where security_code = %s and trade_date between %s and %s
                          and close is not null order by trade_date""",
                     (row['code'], row['base_day'], d1['trade_date']))
    assert px is not None and len(px) >= 3
    by = dict(zip(px['d'], px['hfq']))
    base, first, second = by[row['base_day']], by[d0['trade_date']], by[d1['trade_date']]
    assert d0['pct'] == pytest.approx(round((first / base - 1) * 100, 2), abs=0.02), '首格＝起算日÷前一场'
    assert d1['pct'] == pytest.approx(round((second / first - 1) * 100, 2), abs=0.02)
    assert d1['cum'] == pytest.approx(round((second / base - 1) * 100, 2), abs=0.05), \
        'cum 是相对基期（起算日前一场收盘）连乘，不是各格相加'


def test_真库每只票的行数等于它自己从起算日到基准日的行情天数():
    """`series` 的长度必须等于**这只票**在 [起算日, 基准日] 里真有行情的场数：
    多了说明拿别的日子凑了空格（界面会念出一串假平盘），少了说明截断了却没说。"""
    if not pg.enabled():
        pytest.skip('本机库没通')
    out = quant.energy_track()
    if not out.get('available'):
        pytest.skip('本机库没有倍量场')
    for r in out['rows'][:5]:
        own = pg.fetch_df("""select count(*)::int n FROM (
                               select distinct trade_date from market_data.stock_daily
                                where security_code = %s and trade_date between %s and %s
                                  and close is not null) z""",
                          (r['code'], r['first_date'], out['date']))
        assert own is not None and int(own.iloc[0, 0]) == len(r['series']),             f"{r['code']} 库里 {int(own.iloc[0, 0])} 天 / 序列 {len(r['series'])} 格"
        assert r['sessions_since'] + 1 == len(r['series'])
        assert r['series'][0]['trade_date'] == r['first_date'], '第 0 格必须就是起算日那一天'
