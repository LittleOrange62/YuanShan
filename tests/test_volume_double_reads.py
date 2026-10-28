# -*- coding: utf-8 -*-
"""倍量榜新加的两块读数：四维象限那一列 + 反向的概念板块出现次数。

用户 2026-10-09 两条：① 倍量榜加一列展示四维象限得分；② 把这 150 只票的概念板块
反过来按出现次数排一张新表。两条各自的坑不同，钉法也不同：

① 的坑是**跨日凑数**与**缺数当 0**：象限必须来自同一个 trade_date 的 `analytics.score`，
   缺行要交 None（界面显示 —），0 会被读成"中立"。
② 的坑是**分母**与**宽概念冒充题材**：只数最高的一定是 `融资融券`（全市场 3,872 只），
   所以除了只数还必须带全市场成员数与集中度，否则这张表会把"通道类标签"读成热点。
"""
import pytest

from duanxian import pg, quant
from app.compute import volume_energy as venc


@pytest.fixture
def stubbed(monkeypatch):
    """把 `volume_double` 外面的三样东西按住：这场有没有数、SQL 交出什么行、概念聚合交出什么。"""
    captured = {}

    def _rows(sql, params=()):
        # 象限那一格后来多了一条**全场按档计数**的聚合（10-13）。它走的也是 `_rows`，
        # 不分开记的话 `captured['sql']` 会被它覆盖掉，上面那批"钉 join"的断言就成空炮。
        # 10-19 又加了「分段差全场分布」那条聚合（带 `as hit`），同样要单独分流；
        # 2026-10-19 第二稿再加当日**名次分母（universe）**聚合（`count(dbl_energy) … as scored`），
        # 不分流就会把主 SQL 捕获盖掉——参数序/指纹位那批断言全体变空炮。
        if 'count(dbl_energy)::int as scored' in sql:
            captured['uni_sql'] = sql
            captured['uni_params'] = params
            return captured.get('uni_rows')
        if 'count(*) as n' in sql:
            captured['count_sql'] = sql
            captured['count_params'] = params
            return list(captured.get('counts') or [])
        if 'as hit' in sql:
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
    # `_agg` 的进程内缓存会让同一 (日期, 指纹, min_energy) 的桩结果串到下一条测试 —— 桩里直通。
    monkeypatch.setattr(quant, "_agg",
                        lambda key, sql, params=(), many=False: (_rows(sql, params) or [None])[0])
    monkeypatch.setattr(quant, "_labels", lambda: ({'Q1': '进攻', 'Q2': '持有不加仓', 'Q3': '试探', 'Q4': '回避'}, '测试'))
    monkeypatch.setattr(quant, "_coverage", lambda tables: {})
    monkeypatch.setattr(quant, "_comments", lambda table: {})
    monkeypatch.setattr(quant, "_cache_caveat", lambda keys: [])
    roll = {'box': {'pool_size': 2, 'covered': 2, 'rows': []}}
    monkeypatch.setattr(quant.pg_sources, "concept_rollup",
                        lambda codes: dict(roll['box']))
    # 榜尾那两列涨幅走的是另一条窗口查询：这里也按住，否则这 7 条会真打库（测试不许碰库）。
    pct = {'box': {}}
    calls = []

    def fake_returns(codes, day, span=None):
        calls.append((list(codes), str(day)))
        return dict(pct['box'])
    monkeypatch.setattr(quant.day_returns, "returns_by_code", fake_returns)
    captured['roll'] = roll
    captured['pct'] = pct
    captured['pct_calls'] = calls
    captured['rows'] = []
    return captured


def _row(code='600000.SH', name='测试股', dbl=9, quadrant='Q2', oper=-5.1, x=14.2, y=-18.0,
         step_gap=None, step_gap_reason=None):
    return {'code': code, 'name': name, 'board': '煤矿', 'win_days': 40, 'bars': 40,
            'ma60_days': 40, 'market': 'SH', 'dbl_energy': round(dbl * 0.1134, 4),
            'dbl_count': dbl, 'dbl_prev_count': 2, 'cross_count': 5,
            'dev_med': 1.2, 'dev_max': 4.0, 'above_ratio': 55.0, 'ma60_chg': 0.3,
            'avg_amt_yuan': 1.2e8, 'close_last': 7.7, 'ma60_last': 7.6,
            'dbl_step_gap': step_gap, 'dbl_step_gap_null_reason': step_gap_reason,
            'quadrant': quadrant, 'oper_value': oper, 'x_quality': x, 'y_env': y,
            'rank_in_market': 1200, 'percent_rank': 78.4, 'is_stale': False}


# ------------------------------------------------------------------ ① 象限那一列

# 2026-10-19 第二稿：次数×分值，无归一化 —— 参数序（基准日、基准日、**指纹在第三位**、基准日、加权分门槛、limit）
# 由桩里给 universe 聚合单独分流后，钉的才是主 SQL 本体。
def test_象限三条join都钉在同一场而不是取各自最新(stubbed):
    """10-19 起行集来自分段差的窗口化子查询：基准日在 SQL 里出现三次
    （回看子查询上沿、明细上沿、外层基准日），join 仍只准钉外层这一处。"""
    stubbed['rows'] = [_row()]
    out = quant.volume_double()
    sql = stubbed['sql']
    assert out['date'] == '2026-10-08'
    # 三个 join 都必须跟着 `v.trade_date`；写成 %s 或子查询 max() 就是跨日凑数
    assert sql.count('sc.trade_date = v.trade_date') == 1
    assert sql.count('xq.trade_date = v.trade_date') == 1
    assert sql.count('yv.trade_date = v.trade_date') == 1
    assert 'capability_key' in sql and sql.count('capability_key') >= 3
    # 参数：回看上沿 d、明细上沿 d、**生效指纹 fp**（10-19 换尺后窗口只在同一批里滑）、
    # 外层基准日 d、加权分门槛、limit
    from app.compute import volume_energy as venc
    assert stubbed['params'] == ('2026-10-08', '2026-10-08', venc.fingerprint(),
                                 '2026-10-08', 0.0, 150), stubbed['params']


def test_象限分原值带着标签与两轴一起交出(stubbed):
    stubbed['rows'] = [_row()]
    r = quant.volume_double()['rows'][0]
    assert r['quadrant'] == 'Q2' and r['quadrant_label'] == '持有不加仓'
    assert r['oper_value'] == -5.1 and r['x_quality'] == 14.2 and r['y_env'] == -18.0
    assert r['rank_in_market'] == 1200 and r['percent_rank'] == 78.4


def test_当天没有oper_score的行交回空白而不是零分(stubbed):
    stubbed['rows'] = [_row(quadrant=None, oper=None, x=None, y=None)]
    out = quant.volume_double()
    r = out['rows'][0]
    assert r['quadrant'] is None and r['oper_value'] is None
    assert r['quadrant_label'] is None, "没档就不许给人话标签"
    texts = "；".join(c['text'] for c in out['caveats'])
    assert '600000' in texts and "不当 0 分" in texts, texts


def test_象限的轴语义随payload交出并且不是一句话(stubbed):
    """页面悬停的内容必须来自这份，前端不另写一套合成式。"""
    stubbed['rows'] = [_row()]
    ax = quant.volume_double()['quadrant_axes']
    assert ax['oper_formula'] == quant.QUADRANT_AXES['oper_formula']
    assert set(ax['meaning']) == set(quant.QUADRANT_MEANING)
    assert '不是 0~100' in ax['score_note'], "可为负这一点必须随数据说清楚"


# ------------------------------------------------------------------ ② 概念反向榜

def test_概念榜按这批票的只数聚合并交出分母(stubbed):
    stubbed['rows'] = [_row('600000.SH'), _row('000001.SZ', '平安银行')]
    stubbed['roll']['box'] = {'pool_size': 2, 'covered': 2, 'rows': [
        {'concept': '融资融券', 'stocks': 2, 'members_total': 3872,
         'pool_share_pct': 100.0, 'lift_pctile': 0.1, 'sample_codes': '600000、000001'},
        {'concept': '机器人概念', 'stocks': 1, 'members_total': 1247,
         'pool_share_pct': 50.0, 'lift_pctile': 0.1, 'sample_codes': '600000'}]}
    out = quant.volume_double()
    assert out['concept_pool'] == {'size': 2, 'covered': 2, 'concepts_total': 2, 'single_stock': 1}
    assert [c['concept'] for c in out['concepts']] == ['融资融券', '机器人概念']
    assert out['concepts'][0]['members_total'] == 3872, "只数必须能和全市场成员数一起读"


def test_聚合读失败与真的没归属是两句话(stubbed):
    stubbed['rows'] = [_row()]
    stubbed['roll']['box'] = {'pool_size': 1, 'covered': None, 'rows': [], 'failed': True}
    texts = "；".join(c['text'] for c in quant.volume_double()['caveats'])
    assert "查询失败" in texts and "不是这批票没有概念" in texts, texts

    stubbed['roll']['box'] = {'pool_size': 1, 'covered': 0, 'rows': []}
    texts2 = "；".join(c['text'] for c in quant.volume_double()['caveats'])
    assert "一条概念归属都没有" in texts2, texts2


def test_概念榜的输入就是榜上那批票的代码(stubbed):
    """反向聚合的母体是"这张榜交出的票"，不是全市场 —— 传错池子整张表就是假的。"""
    seen = {}

    def spy(codes):
        seen['codes'] = list(codes)
        return {'pool_size': len(codes), 'covered': 0, 'rows': []}

    stubbed['rows'] = [_row('600000.SH'), _row('920003.BJ', '北证票')]
    import duanxian.pg_sources as pgs
    monkey = pytest.MonkeyPatch()
    monkey.setattr(pgs, "concept_rollup", spy)
    out = quant.volume_double()
    monkey.undo()
    assert sorted(seen['codes']) == ['600000', '920003']
    assert out['concept_pool']['size'] == 2


# ------------------------------------------------------------------ ③ 榜尾两列涨幅

def test_涨幅接到每一行并带着基期与口径一起交出(stubbed):
    from duanxian import day_returns
    stubbed['rows'] = [_row('600000.SH'), _row('000678.SZ', '襄阳轴承')]
    stubbed['pct']['box'] = {
        '600000.SH': {'chg_today': 1.23, 'chg_span': 38.68, 'bars': 16,
                      'base_day': '2026-09-10', 'has_row_on_target': True},
        '000678.SZ': {'chg_today': 3.1, 'chg_span': 38.68, 'bars': 16,
                      'base_day': '2026-09-10', 'has_row_on_target': True}}
    out = quant.volume_double()
    r = out['rows'][1]
    assert (r['chg_today'], r['chg_15']) == (3.1, 38.68)
    assert r['pct_base_day'] == '2026-09-10' and r['pct_bars'] == 16
    assert out['pct_span'] == day_returns.SPAN
    assert out['pct_base_day'] == '2026-09-10'
    # 口径文字必须是后端那一份（页面不许自己解释复权怎么算）
    assert out['pct_rule'] == day_returns.RULE_NOTE
    assert '**' not in out['pct_rule'], "这段要上界面（列头悬停），不许带 markdown 星号"


def test_整批一次查询且钉在榜单那一天(stubbed):
    stubbed['rows'] = [_row('600000.SH'), _row('000678.SZ')]
    stubbed['pct']['box'] = {'600000.SH': {'chg_today': 1.0, 'chg_span': 2.0, 'bars': 16,
                                           'base_day': '2026-09-10', 'has_row_on_target': True}}
    quant.volume_double()
    assert len(stubbed['pct_calls']) == 1, "150 只票不该逐行打库"
    codes, day = stubbed['pct_calls'][0]
    assert codes == ['600000.SH', '000678.SZ'], "传的是库里的全码，不是 6 位码（等值查询才走索引）"
    assert day == '2026-10-08', "窗口锚在榜单那一场，不是 stock_daily 的最新一天"


def test_整批读不到时说是这一列没读到而不是这批票没涨(stubbed):
    stubbed['rows'] = [_row()]
    stubbed['pct']['box'] = {}
    out = quant.volume_double()
    assert out['rows'][0]['chg_today'] is None and out['rows'][0]['chg_15'] is None
    codes = [c['code'] for c in out['caveats']]
    assert 'pct_failed' in codes, codes
    txt = next(c['text'] for c in out['caveats'] if c['code'] == 'pct_failed')
    assert '没读到' in txt and '不是这批票' in txt


def test_个别算不出时点名是哪几只并说不当零(stubbed):
    stubbed['rows'] = [_row('605577.SH', '有行但缺基期')]
    stubbed['pct']['box'] = {'605577.SH': {'chg_today': 0.5, 'chg_span': None, 'bars': 14,
                                           'base_day': None, 'has_row_on_target': True}}
    out = quant.volume_double()
    assert out['rows'][0]['chg_today'] == 0.5
    assert out['rows'][0]['chg_15'] is None, "缺基期 ⇒ None，不是 0%（0% 的意思是平盘）"
    txt = next(c['text'] for c in out['caveats'] if c['code'] == 'pct_missing')
    assert '605577' in txt and '不当 0%（平盘）' in txt


# ---------------------------------------------------- ③ 四维象限多选（用户 2026-10-13）

# 2026-10-19 第二稿：次数×分值，无归一化 —— 象限多选仍拼在 order by … limit 之前；
# 参数序里指纹占第三位、加权分门槛（0.0 兜底）占第四位，全部按第二稿现算钉死。
def test_象限多选拼在limit之前而不是筛完再截断(stubbed):
    """这一条钉的是这次功能唯一会糊过去的地方：在前端筛就只能筛"倍量次最高的那 150 只"，
    选 Q2 得 3 行会被读成全场只有 3 只 Q2（实测 2026-10-12 全场 1,151 只）。
    所以条件必须进 SQL，且位置在 `order by … limit` **之前**。"""
    stubbed['rows'] = [_row(), _row('600001.SH', '第二只')]
    out = quant.volume_double(quadrants='Q2,Q4')
    sql, params = stubbed['sql'], stubbed['params']
    assert 'sc.quadrant in (%s, %s)' in sql, sql[-400:]
    # 10-19 起 SQL 里先出现的是分段差子查询的 `order by … limit 90`，外层排序/截断要 rindex
    assert sql.index('sc.quadrant in') < sql.rindex('order by'), "筛在排序之后 = 只是把交出的行删了几条"
    assert sql.index('sc.quadrant in') < sql.rindex('limit'), "筛在 limit 之后 = 上限先砍掉了别的档"
    # 参数顺序：回看上沿日、明细上沿日、指纹、基准日、加权分门槛、两个象限档、limit
    from app.compute import volume_energy as venc
    assert params == ('2026-10-08', '2026-10-08', venc.fingerprint(),
                      '2026-10-08', 0.0, 'Q2', 'Q4', 150), params
    assert out['filter']['quadrants'] == ['Q2', 'Q4']
    assert out['filter']['quadrants_rejected'] == []


# 2026-10-19 第二稿：次数×分值，无归一化 —— none 档仍落 `sc.quadrant is null`；参数序含指纹（第三位）。
def test_没取到象限那一档用is_null而不是让它消失(stubbed):
    """榜上真有票在 score 里没有行（实测这场 5,526 只有 12 只）。
    多选里不给这一档，勾完剩下的就"凭空没了"；给了就要落成 `sc.quadrant is null`。"""
    stubbed['rows'] = [_row(quadrant=None, oper=None, x=None, y=None)]
    out = quant.volume_double(quadrants='none')
    assert 'sc.quadrant is null' in stubbed['sql']
    # 10-19 起 SQL 里有多个 where（分段差的回看子查询在前）：外层筛要 rsplit 取最后一段
    assert 'in (' not in stubbed['sql'].rsplit('where', 1)[1].split('order by')[0], "只勾 none 不该再有 in"
    assert out['filter']['quadrants'] == ['none']
    # 混合选：Q2 与 none 之间是 or，不是互相覆盖
    out2 = quant.volume_double(quadrants='Q2,none')
    seg = stubbed['sql'].rsplit('where', 1)[1].split('order by')[0]
    assert 'sc.quadrant in (%s)' in seg and ' or sc.quadrant is null' in seg, seg
    assert stubbed['params'] == ('2026-10-08', '2026-10-08', venc.fingerprint(),
                                 '2026-10-08', 0.0, 'Q2', 150), stubbed['params']


# 2026-10-19 第二稿：次数×分值，无归一化 —— 参数序（基准日、基准日、**指纹**、基准日、加权分门槛、命中的象限档、limit）。
def test_认不得的象限值不参与筛选并当场点名(stubbed):
    """拼错不静默丢：丢了你看到的是一份"像筛过了"的榜（与 `jobs._valid` 对 client_mode 同口径）。"""
    stubbed['rows'] = [_row()]
    out = quant.volume_double(quadrants='Q9,Q2,随便')
    assert out['filter']['quadrants'] == ['Q2'], "只有认得的参与"
    assert out['filter']['quadrants_rejected'] == ['Q9', '随便']
    assert stubbed['params'] == ('2026-10-08', '2026-10-08', venc.fingerprint(),
                                 '2026-10-08', 0.0, 'Q2', 150), stubbed['params']
    txt = next(c['text'] for c in out['caveats'] if c['code'] == 'quadrant_bad_token')
    assert 'Q9' in txt and '没有参与筛选' in txt and 'none' in txt


def test_每档实有几只走全场聚合且不受象限与limit影响(stubbed):
    """界面那几个数是**全场**的：没有这一格，"选 Q2 剩 150 行"就分不清是全场只有这么多
    还是被上限截到这么多。"""
    stubbed['rows'] = [_row()]
    stubbed['counts'] = [{'q': 'Q2', 'n': 1151}, {'q': 'Q4', 'n': 4363}, {'q': 'none', 'n': 12}]
    out = quant.volume_double(quadrants='Q2')
    csql = stubbed['count_sql']
    assert 'count(*) as n' in csql and 'group by' in csql
    assert 'sc.quadrant in' not in csql and 'sc.quadrant is null' not in csql, "计数不该带上被筛的那一档"
    assert 'limit' not in csql.lower().split('group by')[0], "计数不能被取数上限截"
    from app.compute import volume_energy as venc
    assert stubbed['count_params'] == ('none', '2026-10-08', venc.fingerprint(), 0.0), \
        stubbed['count_params']
    # 2026-10-19 第二稿新增的名次分母（universe）聚合：只数当日、只数同一批（指纹钉在参数里），
    # `count(dbl_energy)` 只数**有分的**行 —— NULL 不折成 0 分挤进分母。
    assert stubbed['uni_params'] == ('2026-10-08', venc.fingerprint()), stubbed['uni_params']
    assert 'where trade_date = %s and energy_fp = %s' in stubbed['uni_sql']
    assert 'count(dbl_energy)::int as scored' in stubbed['uni_sql'] \
        and 'count(*)::int as rows_total' in stubbed['uni_sql'], \
        'scored 与 rows_total 必须分开数 —— 只交一个数就分不清"没分"与"没读到"'
    assert 'v.dbl_energy >= %s' in stubbed['count_sql'], '全场计数也要按加权分这一档筛'
    assert 'coalesce(v.dbl_count' not in stubbed['count_sql'], '旧尺不许留在计数的筛子里'
    opts = {o['value']: o for o in out['quadrant_options']}
    assert opts['Q2']['count'] == 1151 and opts['none']['count'] == 12
    # 这一场真没有 Q1/Q3：聚合成功时"没出现"就是 0 只，不是"没读到"
    assert opts['Q1']['count'] == 0 and opts['Q3']['count'] == 0
    assert opts['Q2']['label'] == '持有不加仓', "标签用后端那份（config.setting.quadrant_labels）"
    assert 'X 正 Y 负' in opts['Q2']['meaning'], "四档含义仍只住 QUADRANT_MEANING 一处"


# 2026-10-19 第二稿：次数×分值，无归一化 —— 桩把名次分母（universe）聚合单独分流后，
# 这里钉的才是象限计数与主 SQL 本体；计数与主查询都钉指纹（参数序第三位）。
def test_计数那条读不到时交null而不是0(stubbed):
    """0 只与没读到是两句话：前者是这一场真的没有，后者会让人以为这一档消失了。"""
    stubbed['rows'] = [_row()]
    stubbed['counts'] = [{'code': '600000.SH'}]      # 形状不对（桩给的是股票行）
    out = quant.volume_double(quadrants='Q2')
    assert out['quadrant_counts'] == {}
    assert all(o['count'] is None for o in out['quadrant_options']), "读不到一律 null，不拿 0 顶"
    codes = [c['code'] for c in out['caveats']]
    assert 'quadrant_counts_unread' in codes, codes
    # 没勾象限时不该有 bad token 那条，也不该有"筛过"的回显
    plain = quant.volume_double()
    assert plain['filter']['quadrants'] == [] and plain['filter']['quadrants_rejected'] == []
    assert 'sc.quadrant in' not in stubbed['sql'], "不筛就不该把条件拼进 SQL"
    from app.compute import volume_energy as venc
    assert stubbed['params'] == ('2026-10-08', '2026-10-08', venc.fingerprint(),
                                 '2026-10-08', 0.0, 150), stubbed['params']
