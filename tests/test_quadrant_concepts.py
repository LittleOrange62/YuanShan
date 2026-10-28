# -*- coding: utf-8 -*-
"""四维象限榜新加的两块：逐票「概念板块」列 + 反向聚合的概念榜 + 分页名次。

用户 2026-10-12 三条要点，钉法各不同：

① 一页 100 行、手动加载第二页（名次 101~200）：分页依据必须是**后端交出的名次**，
   不是页面渲染序号 —— 否则页内点一次列头，"第 2 页"就变成了"按那一列排完之后的第 101 名"，
   而屏幕上还写着名次。所以 rank_in_pool 必须从 1 连续、且随行走。
② 概念列 + 概念榜的坑是**分母**与**"读不到"冒充"没题材"**：口径文字只住
   `duanxian/pg_sources.CONCEPT_RULE_NOTE` 一份，页面悬停读接口那份原话。
③ 榜被 limit 截断时必须说清"界面上那两页是在前 200 名里翻页"，否则翻页会被读成还有数。
"""
import pytest

from duanxian import pg, pg_sources, quant


@pytest.fixture
def stubbed(monkeypatch):
    """把取数按住：counts 一次、pick 一次，其余聚合给固定值。"""
    captured = {'calls': []}

    def _rows(sql, params=()):
        # 按 SQL 内容分派，不按调用次数：同一条测试里会调两次 quadrant()，
        # 用"第几次"会在第二次把分布那次喂成榜行（KeyError 'n' 那种红就是这么来的）。
        if 'group by 1 order by 1' in sql:
            return [{'quadrant': q, 'n': 10} for q in ('Q1', 'Q2', 'Q3', 'Q4')]
        return list(captured['pick'])

    monkeypatch.setattr(pg, "enabled", lambda: True)
    monkeypatch.setattr(quant.pg, "session_has", lambda table, d: True)
    monkeypatch.setattr(quant, "_latest", lambda table: "2026-10-09")
    monkeypatch.setattr(quant, "_dash", lambda s: "2026-10-09")
    monkeypatch.setattr(quant, "_rows", _rows)
    monkeypatch.setattr(quant, "_agg", lambda sql, params=(): {'n': 100, 'matched': 100})
    monkeypatch.setattr(quant, "_labels", lambda: ({'Q1': '买入', 'Q2': '持有不加仓',
                                                   'Q3': '试探', 'Q4': '回避'}, '测试'))
    monkeypatch.setattr(quant, "_axis_weights", lambda: {'drift': None, 'x': 1, 'y': 1})
    monkeypatch.setattr(quant, "_coverage", lambda tables: {})
    monkeypatch.setattr(quant, "_cache_caveat", lambda keys: [])
    monkeypatch.setattr(quant, "_caveats", lambda *codes: [])
    monkeypatch.setattr(quant, "_null_coverage",
                        lambda d, n: {'score_selected_session': {'date': d, 'quadrant_null': 0}})

    cons = {'box': {}}
    monkeypatch.setattr(pg_sources, "concepts_by_code", lambda codes: dict(cons['box']))
    roll = {'box': {'pool_size': 0, 'covered': 0, 'rows': []}}
    monkeypatch.setattr(pg_sources, "concept_rollup", lambda codes: dict(roll['box']))
    captured['pick'] = []
    captured['cons'] = cons
    captured['roll'] = roll
    return captured


def _row(code='600887.SH', name='伊利股份', quadrant='Q1', oper=48.49):
    return {'code': code, 'name': name, 'quadrant': quadrant, 'oper_value': oper,
            'rank_in_market': 1, 'percent_rank': 100.0, 'is_stale': False,
            'x_quality': 57.8, 'y_env': 42.3, 'slope': -0.07, 'up_streak_days': 3,
            'window_days': 10, 'point_count': 10, 'first_quadrant': 'Q2'}


# ---------------------------------------------------------------- ① 分页依据

def test_名次从1开始连续并随行走而不是让页面按渲染序号数(stubbed):
    stubbed['pick'] = [_row('600887.SH'), _row('002873.SZ', '新天药业'), _row('001328.SZ', '登康口腔')]
    rows = quant.quadrant()['rows']
    assert [r['rank_in_pool'] for r in rows] == [1, 2, 3]
    # 榜的序就是接口交出的序（SQL 里按 value 降序），行上的名次与它同源
    assert rows[0]['oper_value'] == 48.49 and rows[2]['code'] == '001328.SZ'


def test_榜被上限截断时说明白两页只是前两百名里的翻页(stubbed):
    stubbed['pick'] = [_row(f'6000{i:02d}.SH') for i in range(200)]
    out = quant.quadrant()          # 默认 limit=200，正好撞上上限
    txt = next((c['text'] for c in out['caveats'] if c['code'] == 'pool_truncated'), None)
    assert txt and 'limit=200' in txt and '前 200 名里翻页' in txt, txt
    assert '40' in txt, "要说清这一场带象限的总票数，否则不知道被截掉了多少"


# ---------------------------------------------------------------- ② 概念那一列

def test_概念列接到每一行且口径文字只有后端那一份(stubbed):
    stubbed['pick'] = [_row('600887.SH'), _row('002873.SZ', '新天药业')]
    stubbed['cons']['box'] = {
        '600887': {'concepts': '乳品深加工、乳品、奶粉', 'concept_count': 18},
        '002873': {'concepts': '免疫治疗、细胞免疫治疗、仿制药', 'concept_count': 8}}
    out = quant.quadrant()
    r0, r1 = out['rows']
    assert (r0['concepts'], r0['concept_count']) == ('乳品深加工、乳品、奶粉', 18)
    assert r1['concept_count'] == 8
    assert out['concept_rule'] == pg_sources.CONCEPT_RULE_NOTE
    assert '**' not in out['concept_rule'], "这段要进列头悬停（native title），带星号就是脏字符"


def test_没有概念行的票交回None并点名而不是当成零个题材(stubbed):
    stubbed['pick'] = [_row('600887.SH'), _row('920003.BJ', '北证票')]
    stubbed['cons']['box'] = {'600887': {'concepts': '乳品', 'concept_count': 1}}
    # 反向榜有行（那只 600887 挂上了），才轮到"个别说不出"这一支；
    # 整批为空时说的是另一句（concept_empty），两条不许并成一条
    stubbed['roll']['box'] = {'pool_size': 2, 'covered': 1, 'rows': [
        {'concept': '乳品', 'stocks': 1, 'members_total': 40,
         'pool_share_pct': 50.0, 'lift_pctile': 2.5, 'sample_codes': '600887'}]}
    out = quant.quadrant()
    assert out['rows'][1]['concepts'] is None and out['rows'][1]['concept_count'] is None
    txt = next((c['text'] for c in out['caveats'] if c['code'] == 'concept_missing'), None)
    assert txt and '920003' in txt and '不是 0 个题材' in txt.replace('「', '').replace('」', ''), txt


def test_聚合读不到与真的没归属仍然是两句话(stubbed):
    stubbed['pick'] = [_row('600887.SH')]
    stubbed['cons']['box'] = {'600887': {'concepts': '乳品', 'concept_count': 1}}
    stubbed['roll']['box'] = {'pool_size': 1, 'covered': None, 'rows': [], 'failed': True}
    texts = "；".join(c['text'] for c in quant.quadrant()['caveats'])
    assert "查询失败" in texts and "不是这批票没有概念" in texts, texts

    stubbed['roll']['box'] = {'pool_size': 1, 'covered': 0, 'rows': []}
    texts2 = "；".join(c['text'] for c in quant.quadrant()['caveats'])
    assert "一条概念归属都没有" in texts2, texts2


def test_概念榜的输入就是这张榜的票且分母随数交出(stubbed):
    seen = {}

    def spy(codes):
        seen['codes'] = list(codes)
        return {'pool_size': len(codes), 'covered': len(codes),
                'rows': [{'concept': '融资融券', 'stocks': len(codes), 'members_total': 3872,
                          'pool_share_pct': 100.0, 'lift_pctile': 0.1, 'sample_codes': ''}]}

    stubbed['pick'] = [_row('600887.SH'), _row('002873.SZ', '新天药业')]
    monkey = pytest.MonkeyPatch()
    monkey.setattr(pg_sources, "concept_rollup", spy)
    out = quant.quadrant()
    monkey.undo()
    assert sorted(seen['codes']) == ['002873', '600887'], "传的是 6 位码，与 sector_member 的 left() 对齐"
    assert out['concept_pool'] == {'size': 2, 'covered': 2, 'concepts_total': 1, 'single_stock': 0}
    assert out['concepts'][0]['members_total'] == 3872, "只数必须能和全市场成员数一起读"
