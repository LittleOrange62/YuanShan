# -*- coding: utf-8 -*-
"""「个股信息」那一格里的 400 字 AI 公司说明：材料组装、字数与越界的闸、落库与读回。

两条不打桩的用例（`test_落库_*` / `test_失败不动已有正文*` / `test_端点_*`）走**真库真端点**：
这条链最容易出的事是"代码写着有、其实没接上"（写入方签名与列名对不上、read 回来是 None 而屏幕
念成"没生成过"），而这些只有真跑一次才看得见。测试用自己的假代码并在 finally 里删干净。
"""
from __future__ import annotations

import pytest

from duanxian import stock_brief as SB

FAKE = 'TEST01.SH'


def _segments(**over):
    """一份"像真返回"的各段数据（字段名照 2026-10-22 现查 600519 的真实返回抄，不是编的形状）。"""
    base = {
        'profile': {'available': True, 'hq_date': '2026-09-24', 'main_business': '茅台酒',
                    'staff_num': 34992, 'board': '主板', 'name': '贵州茅台', 'is_st': False},
        'valuation': {'data': {'name': '贵州茅台', 'code': '600519', 'price': 1309.69,
                               'pe_ttm': 20.1, 'pb': 6.52, 'mcap_yi': 16372.19,
                               'analyst_count': 46, 'eps_26e': 67.16, 'eps_27e': 71.08,
                               'pe_26e': 19.5, 'peg': 3.34, 'cagr_pct': 6.0}},
        'quote': {'data': {'600519': {'name': '贵州茅台', 'quote_time': '20261021161453',
                                      'price': 1309.69, 'change_pct': -1.67, 'amount_wan': 440382.0,
                                      'turnover_pct': 0.27, 'limit_up': 1465.07, 'limit_down': 1198.69}}},
        'percentile': {'data': {'period': '近5年', 'metrics': {
            'pe_ttm': {'current': 20.1, 'percentile': 15.6, 'min': 17.66, 'max': 54.97, 'n': 914},
            'pb': {'current': 6.52, 'percentile': 9.2, 'min': 5.39, 'max': 15.81, 'n': 914}}}},
        'financials': {'data': {'period': '2026-06-30', 'revenue': '922.78亿', 'revenue_yoy': '1.30%',
                                'net_profit': '445.17亿', 'net_profit_yoy': '-1.95%', 'eps': '35.57',
                                'roe': '16.75%', 'gross_margin': '89.56%', 'net_margin': '50.75%'}},
        'reports': [{'title': f'贵州茅台研报标题 {i}', 'orgSName': '某证券', 'emRatingName': '买入',
                     'publishDate': '2026-08-21 00:00:00'} for i in range(200)],
        'news': [{'新闻标题': f'新闻 {i}', '发布时间': '2026-10-21 16:26', '文章来源': '证券时报网'}
                 for i in range(10)],
        'announcements': [{'date': '2026-08-15', 'title': f'公告 {i}', 'type': '其他'} for i in range(15)],
        'blocks': {'data': {'total': 29, 'boards': [{'name': f'板块{i}', 'change_pct': -0.6,
                                                    'lead_stock': '某股'} for i in range(29)],
                                   'concept_tags': [f'概念{i}' for i in range(29)]}},
        'hotConcepts': [{'concept': '白酒', 'bk': 'BK0896', 'hit': 5280}],
        'margin': [{'date': '2026-10-20', 'rzye': 1.66e10, 'rqye': 2.7e8, 'rzrqye': 1.69e10}],
        'blockTrade': [], 'holders': [], 'dividend': [], 'lockup': {'history': [], 'upcoming': []},
        'dragonTiger': {'records': [], 'seats': {'buy': [], 'sell': []},
                        'institution': {'buy_amt': 0.0, 'sell_amt': 0.0, 'net_amt': 0.0}},
        'fundFlow': [], 'investorQa': [],
    }
    base.update(over)
    return base


def _one(segs, key='reports'):
    return next(s for s in segs if s['key'] == key)


# ───────────────────────── 材料组装：取舍回执必须说得出 ─────────────────────────

def test_compose_逐段给出源几条与喂了几条():
    out = SB.compose('600519.SH', _segments())
    assert out['available'] is True and out['chars'] <= SB.MATERIAL_CAP
    rep = _one(out['segments'])
    # 这一条是整块的核心：屏幕上必须能看出"研报 200 条只喂了 8 条"，而不是"读完了全部研报"
    assert rep['total'] == 200 and rep['taken'] == 8 and rep['truncated'] is False
    tags = _one(out['segments'], 'conceptTags')
    assert tags['taken'] == 29 and tags['total'] == 29          # 概念标签从 blocks 那一份回落取到
    boards = _one(out['segments'], 'blocks')
    assert boards['taken'] == 12 and boards['total'] == 29      # 板块按 cap 取，total 是源本轮条数


def test_compose_空返回的段落进_missing_不编一行():
    out = SB.compose('600519.SH', _segments(fundFlow=[], investorQa=[]))
    labels = out['missing']
    assert '每日资金流（东财）' in labels and '互动问答（东财互动易）' in labels
    assert '资金流' not in out['material'] and '互动问答' not in out['material']
    # 一个占位句都不许写进材料：那会被模型读成"这段确实没有"以外的东西
    assert '未提供' not in out['material']


def test_compose_认不出的形状一律算没读到():
    # FastAPI 的 422 错误体不是行情：把它当 Quote 用就会摆出一格假价格
    out = SB.compose('600519.SH', _segments(quote={'detail': [{'type': 'missing'}]}))
    assert _one(out['segments'], 'quote')['taken'] == 0
    assert '行情（腾讯，即时值）' in out['missing']
    assert '价 None 元' not in out['material']


def test_compose_一份材料都没组出来时说明原因并不发起请求():
    out = SB.compose('600519.SH', {})
    assert out['available'] is False and out['chars'] <= SB.MATERIAL_CAP
    assert out['reason'] and '不发起 AI 请求' in out['reason']
    assert all(s['taken'] == 0 for s in out['segments'])


def test_compose_超上限从段尾削并留下痕迹(monkeypatch):
    # 现行 SEGMENTS 的 cap×clip 预算（2026-10-22 按 600519 实测约 3.6k 字）本来就够不着 8,000 硬顶，
    # 所以这一条是**把预算临时放大**来验那道保险本身：削行必须削得到上限以下，且被削的段自己要说出来。
    wide = [dict(s, clip=4000) if s['key'] in ('reports', 'news') else dict(s) for s in SB.SEGMENTS]
    monkeypatch.setattr(SB, 'SEGMENTS', wide)
    big = _segments(reports=[{'title': '长' * 1200 + str(i), 'orgSName': '某证券',
                              'emRatingName': '买入', 'publishDate': '2026-08-21'}
                             for i in range(400)],
                    news=[{'新闻标题': '闻' * 300 + str(i), '发布时间': '2026-10-21',
                           '文章来源': '源'} for i in range(60)])
    out = SB.compose('600519.SH', big)
    assert out['chars'] <= SB.MATERIAL_CAP, f'材料 {out["chars"]} 字，超了硬顶还没削下来'
    assert any(s['truncated'] for s in out['segments']), '削过段尾却没有任何一段被标记'
    assert any(s['taken'] < s['total'] for s in out['segments'])
    assert 'dropped' in out['segments'][0] and 'note' in out['segments'][0]


def test_compose_材料里每一段都带自己的小标题():
    out = SB.compose('600519.SH', _segments())
    assert '【本机个股档案（主营标签/员工数/板块）】' in out['material']
    assert '【研报标题（东财）】' in out['material']
    # 段与段的先后＝优先级先后（越靠前越重要），换序要换 SPEC_VERSION
    assert out['material'].index('本机个股档案') < out['material'].index('估值与机构预期')


# ───────────────────────── 写入侧的闸 ─────────────────────────

def test_闸_空正文与超容忍上限都拒():
    with pytest.raises(ValueError, match='空正文'):
        SB.check_brief_text('   \n ')
    with pytest.raises(ValueError, match='容忍上限'):
        SB.check_brief_text('字' * int(SB.WORD_LIMIT * SB.OVER_TOLERANCE + 1))


def test_闸_契约到容忍之间照实入库由界面报实际字数():
    body = SB.check_brief_text('字' * 460)
    assert len(body) == 460          # 不截断：截一半上屏会变成"公司说明就这么短"


@pytest.mark.parametrize('bad', ['当前建议买入，目标价 1800 元。', '明天可以低吸介入。',
                                 '止损价设在 1250 元。', '推荐标的：贵州茅台。'])
def test_闸_出现交易动作或点位的正文拒收(bad):
    with pytest.raises(ValueError, match='交易动作或点位'):
        SB.check_brief_text(bad)


def test_闸_陈述历史动作不算越界():
    ok = '2024 年该公司宣布增资扩产，市场当时反应平淡。'
    assert SB.check_brief_text(ok) == ok


# ───────────────────────── 落库与读回（真库，假代码，跑完删干净） ─────────────────────────

@pytest.fixture()
def clean_row():
    from app.db import engine
    engine.execute('delete from analytics.stock_brief where security_code = %s', (FAKE,), role='app')
    yield
    engine.execute('delete from analytics.stock_brief where security_code = %s', (FAKE,), role='app')
    assert engine.query_one('select count(*) n from analytics.stock_brief where security_code = %s',
                            (FAKE,), role='reader')['n'] == 0


def test_落库_写进去就要能读回来且不假定已生效(clean_row):
    from app.db import engine
    receipt = SB.compose('600519.SH', _segments())
    saved = SB.save_brief(FAKE, '这家公司做酒。' * 3,
                          material_chars=receipt['chars'], segments=receipt['segments'],
                          missing=receipt['missing'], model='m1', ai_provider='p1',
                          prompt_slot='stock_brief', prompt_source='内置默认（还没存过）',
                          input_fetched_at='2026-10-22T00:00:00+08:00')
    assert saved['brief'].startswith('这家公司做酒') and saved['brief_chars'] == len(saved['brief'])
    assert saved['error'] is None and saved['failed_at'] is None
    assert saved['word_limit'] == SB.WORD_LIMIT
    assert _one(saved['material_segments'])['total'] == 200          # 研报那段回执原样存回来
    assert saved['input_fetched_at'] and 'T' in saved['input_fetched_at']
    # 同一 (票, 版本) 只许一行：重跑是覆盖，不是又长一条
    SB.save_brief(FAKE, '重写一遍。' * 4, material_chars=1, segments=[], missing=[])
    n = engine.query_one('select count(*) n from analytics.stock_brief where security_code = %s',
                         (FAKE,), role='reader')['n']
    assert n == 1


def test_失败不动已有正文(clean_row):
    SB.save_brief(FAKE, '上一次成功生成的结论。', material_chars=10, segments=[], missing=[])
    row = SB.record_failure(FAKE, '本次 AI 调用超时', material_chars=20,
                            segments=[{'key': 'reports', 'taken': 0}], missing=['研报标题（东财）'])
    assert row['brief'] == '上一次成功生成的结论。', '失败那一路把已有结论抹掉了'
    assert row['error'] == '本次 AI 调用超时' and row['failed_at']
    back = SB.read_brief(FAKE)
    assert back['brief'] == '上一次成功生成的结论。'


def test_没有这一行时读回_none_而不是空壳(clean_row):
    assert SB.read_brief(FAKE) is None


def test_越界正文不入库且屏幕拿得到原因(clean_row):
    before = SB.read_brief(FAKE)
    assert before is None
    with pytest.raises(ValueError, match='交易动作或点位'):
        SB.save_brief(FAKE, '建议买入，目标价 1800 元。')
    assert SB.read_brief(FAKE) is None


# ───────────────────────── 端点与那一卡的接线 ─────────────────────────

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    import server
    # client=(...) 必须写成回环：白名单是唯一那道门，否则满屏 403（见《网络边界》那条经验）
    return TestClient(server.app, base_url="http://127.0.0.1:8910", client=("127.0.0.1", 1))


def test_端点_材料组装把回执交回(client):
    body = client.post('/api/stock/brief/material',
                       json={'code': '600519.SH', 'segments': _segments(),
                             'fetched_at': '2026-10-22 00:20'}).json()['data']
    assert body['available'] is True and body['spec_version'] == SB.SPEC_VERSION
    assert body['word_limit'] == SB.WORD_LIMIT and body['material_cap'] == SB.MATERIAL_CAP
    assert _one(body['segments'])['total'] == 200
    assert body['material_sha'] and len(body['material_sha']) == 12
    assert 'caliber' in body and 'definitions' in body


def test_端点_越界正文返回未保存与原因而不是静默(client, clean_row):
    r = client.post('/api/stock/brief/result', json={
        'code': FAKE, 'ok': True, 'brief': '建议买入，目标价 1800 元。'}).json()['data']
    assert r['saved'] is False and '交易动作或点位' in r['reason']
    assert SB.read_brief(FAKE) is None


def test_端点_失败回执落进库里(client, clean_row):
    r = client.post('/api/stock/brief/result', json={
        'code': FAKE, 'ok': False, 'error': 'AI 未接入', 'material_chars': 12,
        'segments': [], 'missing': ['全部']}).json()['data']
    assert r['error'] == 'AI 未接入' and r['brief'] is None


def test_端点_成功那一路落库并读回同一份(client, clean_row):
    r = client.post('/api/stock/brief/result', json={
        'code': FAKE, 'ok': True, 'brief': '这是一家做蒸馏酒的公司。', 'material_chars': 30,
        'segments': [], 'missing': [], 'model': 'm', 'ai_provider': 'p',
        'prompt_slot': 'stock_brief', 'prompt_source': '内置默认（还没存过）',
        'input_fetched_at': '2026-10-22T00:00:00+08:00'}).json()['data']
    assert r['brief'] == '这是一家做蒸馏酒的公司。' and r['brief_chars'] == 12


def test_个股信息那一卡把结论带出来(client, clean_row):
    """`/api/stock/facts` 是那一卡的唯一读侧：结论必须在同一个 payload 里，
    否则「双击开新窗」与「从导航进来」两种入口会看到不一样的上面那块。"""
    from duanxian import stock_facts as FK
    d = FK.board('600519.SH')
    assert d['available'] is True and 'brief' in d and 'brief_spec_version' in d
    SB.save_brief('600519.SH', '真代码上的一次接线验证。', material_chars=1, segments=[], missing=[])
    try:
        got = FK.board('600519.SH')
        assert got['brief']['brief'] == '真代码上的一次接线验证。'
        assert got['brief']['caliber'] == SB.CALIBER
        assert got['brief']['definitions']['material_segments']
    finally:
        from app.db import engine
        engine.execute('delete from analytics.stock_brief where security_code = %s',
                       ('600519.SH',), role='app')
        assert SB.read_brief('600519.SH') is None
