# -*- coding: utf-8 -*-
"""倍量榜「加权分较前一整窗增减」（#171 → 2026-10-19 换尺）的回归。

判据：`dbl_step_gap = 本窗 dbl_energy − 紧邻前一整窗的 dbl_energy`，
前一窗＝同一只票按 trade_date 排序往前第 win_days（40）**行**那场
（按行不按日期：停牌会让两窗重叠）。加权分本身由
`app/compute/volume_energy.py` 的档表算出（每 0.1 一档；2026-10-19 第二稿：
得分 = Σ 每档 ( 出现次数 × 分值 )，没有系数/占比/归一化）。

四条钉法对应四个最容易糊的地方：

1. **三种 NULL**（① 往前数不到 40 行 ② 本窗或前窗不满 40 场 ③ 窗的加权分本身空着）
   —— 用假数据把生产 SQL 里的那两段 CASE 片段（`_STEP_GAP_EXPR`/`_STEP_GAP_REASON_EXPR`）
   直接套在 (VALUES ...) 上跑：钉的是生产那一份片段，不是测试里的抄本。
   为什么必须 NULL 而不是 0：0 会被读成「本窗与前窗一样」，而事实是「没有可比的前窗」。
2. **单位是加权分（分）、不是次数也不是占比**：阈值 `STEP_GAP_MIN = 1.5`（浮点；
   第一稿的 0.2 是"占比"量纲下的数，随 ÷Σ 一起作废，不许复活；旧口径的整数 2 同理）。
   ⚠️ 出口必须是浮点：`_int` 会把 1.5 截成 1，等于把"提升幅度不够"和"刚过线"念成同一个数。
3. **阈值只住 STEP_GAP_MIN 一处**：接口交 `thresholds.step_gap_min`，全场聚合 SQL 里
   那个下限必须是 `%s`＋参数等于常量，不许是拼进文本的字面量。
4. **窗口只在同一批（`energy_fp`）里滑**：改档表但还没重算完时库里混着两批，
   跨批做差就是拿旧尺减新尺 ⇒ 片段里必须带 `energy_fp = %s`，参数序也钉死。
"""
import pytest

from duanxian import pg, quant
from app.compute import volume_energy as venc


@pytest.fixture
def stubbed(monkeypatch):
    """与 `test_volume_double_reads.py` 同一套桩：主 SQL、象限计数、分段差聚合、名次分母（universe）四路分流。

    2026-10-19 第二稿：`volume_double` 体里多了当日名次分母那条聚合（`count(dbl_energy) … as scored`），
    不单独分流就会把主 SQL 的桩捕获盖掉 —— 上面那批"钉参数序/筛在 limit 之前"的断言会全体变空炮。
    """
    captured = {}

    def _rows(sql, params=()):
        if 'count(dbl_energy)::int as scored' in sql:  # 名次分母（universe）：只数有分的行
            captured['uni_sql'] = sql
            captured['uni_params'] = params
            return captured.get('uni_rows')
        if 'count(*) as n' in sql:                      # 象限那档的全场计数
            captured['count_sql'] = sql
            captured['count_params'] = params
            return list(captured.get('counts') or [])
        if 'as hit' in sql:                             # 「稳步提升」的全场分布聚合
            captured['step_sql'] = sql
            captured['step_params'] = params
            return captured.get('step_pool')
        captured['sql'] = sql
        captured['params'] = params
        return captured['rows']

    monkeypatch.setattr(pg, "enabled", lambda: True)
    # 空态那条走 `_empty_after_filter`，它现查 `_alive()`（真库连通性）分流措辞；
    # 桩里钉成"库是通的、只是筛空了"，测的才是筛空那句话本身，不随本机库状态漂。
    monkeypatch.setattr(quant, "_alive", lambda: True)
    monkeypatch.setattr(quant.pg, "session_has", lambda table, d: True)
    monkeypatch.setattr(quant, "_latest", lambda table: "2026-10-08")
    monkeypatch.setattr(quant, "_rows", _rows)
    # `_agg` 的进程内缓存会把同一 (日期, 阈值) 的桩结果串到下一条测试 —— 桩里直通。
    monkeypatch.setattr(quant, "_agg",
                        lambda key, sql, params=(), many=False: (_rows(sql, params) or [None])[0])
    monkeypatch.setattr(quant, "_labels", lambda: ({'Q1': '进攻', 'Q2': '持有不加仓',
                                                    'Q3': '试探', 'Q4': '回避'}, '测试'))
    monkeypatch.setattr(quant, "_coverage", lambda tables: {})
    monkeypatch.setattr(quant, "_comments", lambda table: {})
    monkeypatch.setattr(quant, "_cache_caveat", lambda keys: [])
    monkeypatch.setattr(venc, "status", lambda: {'in_sync': True, 'profile_source': '测试',
                                                 'batches': [], 'distribution': {},
                                                 'scale': venc.scale()})
    monkeypatch.setattr(quant.pg_sources, "concept_rollup",
                        lambda codes: {'pool_size': 1, 'covered': 1, 'rows': []})
    monkeypatch.setattr(quant.day_returns, "returns_by_code", lambda codes, d, span=None: {})
    captured['rows'] = []
    return captured


def _row(code='600000.SH', energy=1.25, step_gap=0.25, step_gap_reason=None):
    return {'code': code, 'name': '测试股', 'board': '主板', 'market': 'SH', 'win_days': 40,
            'bars': 40, 'ma60_days': 40,
            'dbl_energy': energy, 'dbl_count': 9, 'dbl_prev_count': 2, 'cross_count': 5,
            'dev_med': 1.2, 'dev_max': 4.0, 'above_ratio': 55.0, 'ma60_chg': 0.3,
            'avg_amt_yuan': 1.2e8, 'close_last': 7.7, 'ma60_last': 7.6,
            'dbl_step_gap': step_gap, 'dbl_step_gap_null_reason': step_gap_reason,
            'quadrant': 'Q2', 'oper_value': -5.1, 'x_quality': 14.2, 'y_env': -18.0,
            'rank_in_market': 1200, 'percent_rank': 78.4, 'is_stale': False}


# ------------------------------------------------------------------ ① 三种 NULL（假数据钉生产 CASE）

def _gap_on_values(rows):
    """把生产的两段 CASE 片段套在 (VALUES ...) 上跑 —— 跑的就是线上那一份文本。"""
    if not pg.enabled():
        pytest.skip('本机库没通，这条要真库跑生产 SQL 片段')
    cells = []
    for r in rows:
        one = []
        for v in r:
            if v is None:
                one.append('null::numeric')
            elif isinstance(v, int):
                one.append(f'{v}::int')
            else:
                one.append(f'{float(v)}::numeric')
        cells.append('(' + ', '.join(one) + ')')
    vals = ', '.join(cells)
    df = pg.fetch_df(f"""select {quant._STEP_GAP_EXPR} as gap,
                  {quant._STEP_GAP_REASON_EXPR} as reason
             from (values {vals})
               as w(win_rn, win_days, bars, prev_win_bars, dbl_energy, prev_win_energy)""")
    assert df is not None, '生产 SQL 片段在真库上跑不通'
    return [dict(r) for r in df.to_dict('records')]


# (win_rn, win_days, bars, prev_win_bars, dbl_energy, prev_win_energy)
NO_PREV = (40, 40, 40, None, 1.2, None)          # ① 往前数不到 40 行（上市晚/长期停牌）
SHORT_THIS = (100, 40, 39, 40, 1.2, 1.0)         # ② 本行不满窗
SHORT_PREV = (100, 40, 40, 39, 1.2, 1.0)        # ② 前行不满窗（两把尺不等长）
PREV_EMPTY = (100, 40, 40, 40, 1.2, None)       # ③ 前行存在但加权分是空的
OK_UP = (100, 40, 40, 40, 1.2, 1.0)             # 正常读数 +0.2
OK_FLAT = (100, 40, 40, 40, 1.0, 1.0)           # 真 0：本窗与前窗一样
OK_DOWN = (100, 40, 40, 40, 0.8, 1.0)           # 负数：真读数，不是缺数


def test_三种没有可比前窗的情况都交null并各带一个原因():
    got = _gap_on_values([NO_PREV, SHORT_THIS, SHORT_PREV, PREV_EMPTY])
    assert [g['gap'] for g in got] == [None, None, None, None], \
        '①②③ 三种都必须 NULL —— 0 会被读成「本窗与前窗一样」，实际是「没有可比的前窗」'
    assert [g['reason'] for g in got] == ['no_prev_row', 'window_short', 'window_short',
                                          'count_missing']
    for g in got:
        assert g['reason'] in quant.STEP_GAP_NULL_NOTES, '每个原因码都要有一句能给界面读的话'


def test_真读数照常给包括0和负数它们不是缺数():
    got = _gap_on_values([OK_UP, OK_FLAT, OK_DOWN])
    assert [float(g['gap']) for g in got] == [0.2, 0.0, -0.2], '差值必须按浮点交回，不许被截成整数'
    assert [g['reason'] for g in got] == [None, None, None], '有读数就不该编一个原因'


def test_行偏移保证两窗不重叠而日期偏移会():
    """lag 偏移 = win_days 行 ⇒ 前一窗正好是本窗整体往前 40 行（各 40 行、相接零重叠）。
    按日期（日历日或市场场数）往前推 40 天则不然：停牌票的 40 根 K 线摊在超过 40 个市场日里，
    两窗会重叠，重叠部分被减两遍。生产 SQL 必须写成行偏移，也不许用 interval/日期做偏移。"""
    src = quant._step_gap_source()
    assert 'lag(v0.dbl_energy, coalesce(v0.win_days, 40))' in src
    assert 'lag(v0.bars,      coalesce(v0.win_days, 40))' in src
    assert 'partition by v0.security_code order by v0.trade_date' in src
    assert 'make_interval' not in src and ' - interval' not in src
    assert 'row_number() over wsw as win_rn' in src
    # 旧尺不许复活：窗口里读的是 dbl_energy，不是硬计数 dbl_count
    assert 'lag(v0.dbl_count' not in src, '2026-10-19 换尺：分段差按加权分算，不按次数'


def test_窗口只在同一批里滑跨批做差等于拿旧尺减新尺():
    """改档表 → 指纹变 → 重算要 1~2 分钟。这期间库里同一场混着两批数：
    不加 `energy_fp` 约束的话，`lag(...40 行)` 会落到另一批的行上，差值没有意义。"""
    src = quant._step_gap_source()
    assert 'and v0.energy_fp = %s' in src
    assert src.count('%s') == 3, '基准日两次 + 指纹一次，参数序由主查询那条测试钉'


# ------------------------------------------------------------------ ② 阈值与量纲

# 2026-10-19 第二稿：次数×分值，无归一化 —— 阈值 0.2 是"占比"量纲下的数，随 ÷Σ 一起作废；
# 现值 1.5（单位＝分，读作"比前一窗多约 1.5 个顶档放量日"），旧整数 2（次数口径）同样不许复活。
def test_阈值是1_5分旧口径的0_2与整数2都不许复活(stubbed):
    stubbed['rows'] = [_row()]
    out = quant.volume_double()
    assert quant.STEP_GAP_MIN == 1.5 == out['thresholds']['step_gap_min'], \
        'STEP_GAP_MIN 是唯一的阈值；单位是加权分（不是次数，也不是占比）'
    # 三档阈值/母体/分页的回显字典整个钉住：界面读 thresholds.*，任何一个键自己写数就红
    assert out['thresholds'] == {'step_gap_min': quant.STEP_GAP_MIN,
                                 'top_n_default': venc.DEFAULT_TOP_N,
                                 'page_size': venc.PAGE_SIZE}, out['thresholds']
    # 全场聚合 SQL 里那个下限必须是参数（%s），不是拼死的字面量
    assert 'dbl_step_gap >= %s' in stubbed['step_sql'], stubbed['step_sql'][:400]
    assert stubbed['step_params'][0] == quant.STEP_GAP_MIN, stubbed['step_params']
    assert 'dbl_step_gap >= 0.2' not in stubbed['step_sql'] \
        and 'dbl_step_gap >= 1.5' not in stubbed['step_sql'], '字面量出现＝有第二处阈值'
    assert out['definitions']['dbl_step_gap'] == quant.STEP_GAP_DEFINITION
    assert '**' not in quant.STEP_GAP_DEFINITION, 'definitions 纯文本上屏，不许带 markdown 星号'
    assert '倍量次数' not in quant.STEP_GAP_DEFINITION, \
        '口径原文还写着"倍量次数"就是在给界面发一把旧尺（10-19 代理验收抓到的那条）'


def test_默认不筛分段差不拼条件不给筛参回显(stubbed):
    stubbed['rows'] = [_row()]
    out = quant.volume_double()
    assert 'v.dbl_step_gap >= %s' not in stubbed['sql'], '没给 step_gap_min 就不该拼进 SQL'
    assert out['filter']['step_gap_min'] is None


# 2026-10-19 第二稿：次数×分值，无归一化 —— 参数序里 _step_gap_source 的三个占位是（基准日、基准日、指纹），
# 指纹必须在**第三位**（窗口只在同一批里滑）；外层是（基准日、加权分门槛，[象限…]，分段差，limit）。
def test_筛参拼在order_by_limit之前拼在后面就是另一种谎(stubbed):
    """拼在 limit 之后 = 先把上限砍掉再删几行，"筛完剩 3 只"会被读成全场只有 3 只
    （10-13 象限那轮钉过同一条错）。"""
    stubbed['rows'] = [_row()]
    out = quant.volume_double(step_gap_min=quant.STEP_GAP_MIN)
    sql, params = stubbed['sql'], stubbed['params']
    assert 'v.dbl_step_gap >= %s' in sql, sql[-500:]
    at = sql.index('v.dbl_step_gap >= %s')
    assert at < sql.rindex('order by'), '筛必须排在排序之前'
    assert at < sql.rindex('limit'), '筛必须在取数上限之前'
    # 参数序：片段的 (基准日, 基准日, 指纹) → 外层 (基准日, 加权分门槛, [象限…], 分段差, limit)
    fp = venc.fingerprint()
    assert params == ('2026-10-08', '2026-10-08', fp, '2026-10-08', 0.0,
                      quant.STEP_GAP_MIN, 150), params
    assert out['filter']['step_gap_min'] == quant.STEP_GAP_MIN, '界面回显以后端真应用的值为准'


# 2026-10-19 第二稿：次数×分值，无归一化 —— min_energy 是"加权分（分）"门槛，浮点，拼在 limit 之前；
# 桩里 universe 那条聚合已单独分流，这里钉的才是主 SQL 本体。
def test_加权分门槛也拼在limit之前参数名不再是min_count(stubbed):
    """2026-10-19：`min_count`（次数）换成 `min_energy`（加权分，浮点）。
    参数名留在测试里钉一遍 —— 端点与界面用的键必须是同一个，否则筛了个不存在的键＝没筛。"""
    stubbed['rows'] = [_row()]
    out = quant.volume_double(min_energy=1.1)
    sql, params = stubbed['sql'], stubbed['params']
    assert 'v.dbl_energy >= %s' in sql and 'dbl_count' not in sql.split('where', 1)[1]
    assert sql.index('v.dbl_energy >= %s') < sql.rindex('limit')
    assert params[3:5] == ('2026-10-08', 1.1), params
    assert out['filter']['min_energy'] == 1.1
    assert 'min_dbl_count' not in out['filter'], '旧回显键不许留着——两个键并存迟早有人读错那个'


# 2026-10-19 第二稿：次数×分值，无归一化 —— 阈值换 1.5 后空态那句念「加权分≥0、加权分较前窗增减≥1.5」；
# "这一场有数据、只是筛空了"与"没回填"分开说（这条纪律不随换尺变）。
def test_筛完为空时那句怎么说要带上这一档的量纲(stubbed):
    """`_empty_after_filter` 与 `_fail` 是两句话：这场有数、只是被筛空了。
    措辞必须念"加权分"，念"倍量次数"会让人去填 6 这种次数值。"""
    stubbed['rows'] = []
    out = quant.volume_double(step_gap_min=quant.STEP_GAP_MIN)
    assert out['available'] is False
    assert '加权分≥0' in out['reason'] and '加权分较前窗增减≥1.5' in out['reason'], out['reason']
    assert '这一场是有数据的' in out['reason'] and '不是没回填' in out['reason'], \
        '筛空与没回填是两句话，这两句否定必须逐字在'
    assert '倍量次数' not in out['reason']


# ------------------------------------------------------------------ ③ 行的空/0 与全场分布

def test_榜上的空保持为空并带着那一句原因出门(stubbed):
    stubbed['rows'] = [_row('920117.BJ', energy=None, step_gap=None, step_gap_reason='no_prev_row'),
                       _row('600000.SH', step_gap=0.0)]
    out = quant.volume_double()
    r0, r1 = out['rows']
    assert r0['dbl_energy'] is None, '加权分 NULL 一路到出口都不许折成 0'
    assert r0['dbl_step_gap'] is None, 'NULL 不许在出口前被折成 0'
    assert r0['dbl_step_gap_null_reason'] == 'no_prev_row'
    assert r0['dbl_step_gap_null_note'] == quant.STEP_GAP_NULL_NOTES['no_prev_row'], \
        '原因句子只住后端这一处，界面逐字读'
    assert r1['dbl_step_gap'] == 0.0, '真 0 是读数：本窗与前窗一样'
    assert r1['dbl_step_gap_null_note'] is None, '有读数就不该带"为什么是空"'
    codes = [c['code'] for c in out['caveats']]
    assert 'step_gap_missing' in codes and 'energy_missing' in codes, codes
    txt = next(c['text'] for c in out['caveats'] if c['code'] == 'step_gap_missing')
    assert '920117' in txt and '不当 0' in txt and '取不到 40 行前' in txt, txt


def test_原因码认不得时不编一句假原因(stubbed):
    stubbed['rows'] = [_row('600000.SH', step_gap=None, step_gap_reason='外星分支')]
    r = quant.volume_double()['rows'][0]
    assert r['dbl_step_gap'] is None and r['dbl_step_gap_null_reason'] is None
    assert r['dbl_step_gap_null_note'] is None, '没登记的原因码宁可不说，不猜一个'


def test_全场分布交实数且聚合失败时交null不交0(stubbed):
    stubbed['rows'] = [_row()]
    stubbed['step_pool'] = [{'hit': 1466, 'uncomparable': 22, 'total': 5514}]
    out = quant.volume_double()
    assert out['step_gap_pool'] == {'min': quant.STEP_GAP_MIN, 'hits': 1466,
                                    'uncomparable': 22, 'total': 5514}
    assert 'step_gap_pool_unread' not in [c['code'] for c in out['caveats']]
    # 那条聚合不带 limit、不带分段差筛参本身（它是"这一档实有几只"的分母）
    assert 'limit 150' not in stubbed['step_sql'].lower()
    assert 'dbl_step_gap >= %s' in stubbed['step_sql']
    assert 'v.dbl_step_gap >= %s' not in stubbed['step_sql']

    stubbed['step_pool'] = None          # 聚合失败（_rows 交回 None）
    out2 = quant.volume_double()
    assert out2['step_gap_pool'] is None, '没读到是 null，不是"0 只"'
    txt = next(c['text'] for c in out2['caveats'] if c['code'] == 'step_gap_pool_unread')
    assert '不拿 0 顶' in txt


def test_缓存键带指纹改档表后不能沿用上一批的只数(stubbed):
    """配置一改，同一 (场, 门槛) 的聚合结果就作废。缓存键里没指纹 ⇒ 改完档表
    界面上还是旧批的只数，而那句"筛的还是旧批"永远不会出现。"""
    seen = []
    real_agg = quant._agg

    def spy(key, sql, params=(), many=False):
        seen.append(key)
        return real_agg(key, sql, params, many)

    stubbed['rows'] = [_row()]
    stubbed['step_pool'] = [{'hit': 1, 'uncomparable': 0, 'total': 1}]
    monkey = pytest.MonkeyPatch()
    monkey.setattr(quant, '_agg', spy)
    quant.volume_double()
    monkey.undo()
    assert any(venc.fingerprint() in k for k in seen), seen


# ------------------------------------------------------------------ ④ 尺子自己的体检

def test_库里批次与生效指纹不一致要出声(stubbed):
    """改配置 → 重算要 1~2 分钟。这期间榜上筛的是旧批 ⇒ 必须显式说，
    不能让用户以为改了档表数就立刻换了。"""
    stubbed['rows'] = [_row()]
    monkey = pytest.MonkeyPatch()
    monkey.setattr(venc, 'status', lambda: {'in_sync': False, 'profile_source': '测试',
                                            'batches': [{'fp': 'ev-old'}], 'distribution': {},
                                            'scale': venc.scale()})
    out = quant.volume_double()
    monkey.undo()
    assert out['energy']['in_sync'] is False
    txt = next(c['text'] for c in out['caveats'] if c['code'] == 'energy_batch_stale')
    assert '旧一批' in txt and venc.fingerprint() in txt, txt


def test_体检读不到时既不判绿也不编原因(stubbed):
    stubbed['rows'] = [_row()]
    monkey = pytest.MonkeyPatch()

    def boom():
        raise RuntimeError('config 读不到')

    monkey.setattr(venc, 'status', boom)
    out = quant.volume_double()
    monkey.undo()
    assert out['energy']['in_sync'] is None, '读不到就是读不到（null），不等于"没问题"（True）'
    txt = next(c['text'] for c in out['caveats'] if c['code'] == 'energy_status_unread')
    assert 'config 读不到' in txt, txt


# ------------------------------------------------------------------ ⑤ 真库（不打桩）

def test_真库最新一场确实有取不到前窗的票并且空的不等于0():
    """这条不靠桩：桩能证明"代码按写的走"，只有真库能证明"写的是对的"。
    ① 类 NULL 在最新一场就有（上市没满 40 场的北交所/新股）；
    ②③ 类当日的真实计数也一并钉出来 —— 库里 `bars < win_days` 与 `dbl_energy is null`
    若真有行，那②③就不许只出现在假数据里。"""
    if not pg.enabled():
        pytest.skip('本机库没通，这条要真库')
    d = quant._latest('analytics.volume_double')
    fp = venc.fingerprint()
    assert d and fp
    src = quant._step_gap_source()
    rows = pg.fetch_df(f"""select q.security_code, q.win_rn, q.dbl_energy, q.prev_win_energy,
                                  q.dbl_step_gap, q.dbl_step_gap_null_reason
             from (select w.*, {quant._STEP_GAP_EXPR} as dbl_step_gap,
                          {quant._STEP_GAP_REASON_EXPR} as dbl_step_gap_null_reason
                     from ({src}) w) q
            where q.trade_date = %s and q.dbl_step_gap is null
            order by q.win_rn limit 60""", (d, d, fp, d))
    assert rows is not None and len(rows) > 0, '最新一场必有上市未满 40 场的票，一格空都没有就是坏了'
    assert rows['dbl_step_gap'].isna().all(), 'NULL 不许在出口前被折成 0'
    reasons = set(rows['dbl_step_gap_null_reason'])
    assert reasons <= {'no_prev_row', 'window_short', 'count_missing'}, \
        f'冒出了没登记的原因码：{reasons}'
    assert 'no_prev_row' in reasons, '最新一场必有上市没攒够 40 场的新票'
    # 原因码要与它宣称的那个事实对上 —— 只对分布不核对成因，就等于发了张像是真的标签
    def _blank(v):
        return v is None or v != v          # pandas 把 SQL 的 NULL 读成 NaN

    for r in rows.to_dict('records'):
        why = r['dbl_step_gap_null_reason']
        if why == 'no_prev_row':
            assert r['win_rn'] <= 40, '① 的定义就是往前数不到 win_days 行'
        elif why == 'count_missing':
            assert _blank(r['dbl_energy']) or _blank(r['prev_win_energy']), \
                f'③ 说「加权分本身是空的」，可这行本窗与前一窗都有数：{r}'


def test_真库分段差阈值这一档的只数与全场聚合一致():
    """`step_gap_pool.hits` 与"自己按片段现数一遍"必须相等：
    一个是页面上那句"实有 N 只"，一个是判据本体，两边不一致就是聚合写错了。"""
    if not pg.enabled():
        pytest.skip('本机库没通，这条要真库')
    out = quant.volume_double(limit=3)
    if not out.get('available'):
        pytest.skip('本机库没有这一场')
    d, fp = out['date'], venc.fingerprint()
    src = quant._step_gap_source()
    mine = pg.fetch_df(f"""select count(*) filter (where g >= %s)::int as hit,
                                  count(*) filter (where g is null)::int as nul,
                                  count(*)::int as total
                           from (select w.*, {quant._STEP_GAP_EXPR} as g from ({src}) w) q
                          where q.trade_date = %s and q.dbl_energy >= %s""",
                       (quant.STEP_GAP_MIN, d, d, fp, d, 0.0))
    assert mine is not None
    row = mine.to_dict('records')[0]
    pool = out['step_gap_pool']
    assert pool is not None, '那条聚合失败了，界面会念"没读到" —— 这条测试也就没法钉'
    assert (pool['hits'], pool['uncomparable'], pool['total']) == \
        (row['hit'], row['nul'], row['total']), (pool, row)
