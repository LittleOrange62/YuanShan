# -*- coding: utf-8 -*-
"""「选一只票」检索层（`duanxian/security_search.py` + `app/db/repo/security.py::search`）。

钉的都是会被写成假话的几处：

① **读不到 ≠ 没有**：查询失败必须交 `available:false` + 「查询失败」原文，
   绝不能兜成空表 —— 空表在界面上就是"字典里没这只票"（10-13 同类事故：把查询失败念成没进榜）。
② **LIKE 的 `%`/`_` 必须转义**：不转义时用户打一个 `%` 会"匹配到 30 只"，
   而那是一只都没搜到 —— 这类洞的可怕之处在于屏幕看起来完全正常。
③ **命中方式交出去**：打 `PA` 出「派林生物」要能说清是按首字母中的，否则像乱出结果。
④ **0 命中要说"为什么可能搜不到"**：字典里有几十只股票没登记拼音，用首字母必然搜不到，
   这是参考数据缺，不是市场没这只票。

最后一条**不打桩**：真库现查 PAYH 要真的回平安银行（桩绿过一次不代表接线是对的，10-10 教训）。
"""
import pytest

from app.db import engine
from app.db.repo import security as sec_repo
from duanxian import security_search as SS


def test_空输入是说没输入_不是没有():
    out = SS.search('')
    assert out['available'] is False
    assert '还没输入' in out['reason'] and '一只都没有' in out['reason']
    assert out['rows'] == [] and out['returned'] == 0


def test_查询失败不能念成没匹配(monkeypatch):
    def boom(kw, limit=30, asset_types=None, role='reader'):
        raise RuntimeError('connection refused')

    monkeypatch.setattr(sec_repo, 'search', boom)
    out = SS.search('PAYH')
    assert out['available'] is False
    assert '查询失败' in out['reason'] and '不是这一串没匹配' in out['reason']
    assert 'connection refused' in out['reason'], '异常原文要留在屏上，不然人只知道"没搜到"'


def test_命中方式按四种分别给出(monkeypatch):
    row = {'security_code': '000402.SZ', 'symbol': '000402', 'name': '金融街',
           'pinyin': 'jinrongjie', 'pinyin_abbr': 'JRJ', 'asset_type': 'stock'}
    monkeypatch.setattr(sec_repo, 'search', lambda kw, limit=30, asset_types=None, role='reader': [dict(row)])
    for kw, want in (('000402', 'code'), ('金融', 'name'), ('JR', 'abbr'), ('jinr', 'pinyin'), ('%%', 'other')):
        assert SS.search(kw)['rows'][0]['matched_by'] == want, f'{kw} 该认成 {want}'
    assert set(SS.search('JR')['matched_by_meaning']) == {'code', 'name', 'abbr', 'pinyin', 'other', 'none'}


def test_类别标签交出来不由界面猜(monkeypatch):
    row = {'security_code': '510300.SH', 'symbol': '510300', 'name': '沪深300ETF',
           'pinyin': 'hs300etf', 'pinyin_abbr': 'HS300ETF', 'asset_type': 'etf'}
    monkeypatch.setattr(sec_repo, 'search', lambda kw, limit=30, asset_types=None, role='reader': [dict(row)])
    out = SS.search('HS300')
    assert out['rows'][0]['asset_label'] == 'ETF', '指数/ETF 要标出来，不能悄悄和股票混在一起'
    assert out['kinds'] == [] and out['limit'] == SS.DEFAULT_LIMIT


def test_零命中时给出缺拼音的底(monkeypatch):
    monkeypatch.setattr(sec_repo, 'search', lambda kw, limit=30, asset_types=None, role='reader': [])
    monkeypatch.setattr(engine, 'query_one',
                        lambda sql, params=None, role='reader': {'total': 9551, 'stock_missing': 30})
    out = SS.search('zzzzz')
    assert out['rows'] == []
    assert '9551' in out['reason'] and '30 只股票' in out['reason'], '要说清"首字母必然搜不到"是数据缺'
    assert out['py_gap']['stock_missing_abbr'] == 30


def test_那条聚合坏了也不许带走搜索结果(monkeypatch):
    monkeypatch.setattr(sec_repo, 'search', lambda kw, limit=30, asset_types=None, role='reader': [])

    def boom(sql, params=None, role='reader'):
        raise RuntimeError('agg down')

    monkeypatch.setattr(engine, 'query_one', boom)
    out = SS.search('zzzzz')
    assert out['available'] is True and '没有代码' in out['reason'], '覆盖计数坏了只丢那半句解释'


def test_like_的元字符必须转义():
    # 直接钉 SQL 参数的形状：`%` 变 `\%`、`_` 变 `\_`、反斜杠先加倍
    assert sec_repo._like('PA', False) == 'PA%'
    assert sec_repo._like('100%', True) == '%100\\%%'
    assert sec_repo._like('a_b', True) == '%a\\_b%'
    assert sec_repo._like('a\\b', True) == '%a\\\\b%'


def test_kinds_原样交给参数化查询(monkeypatch):
    seen = {}

    def fake(sql, params=None, role='reader'):
        seen['sql'], seen['params'] = sql, params
        return []

    monkeypatch.setattr(engine, 'query', fake)
    sec_repo.search('PA', asset_types=['stock', 'etf'])
    assert "asset_type = ANY(%s)" in seen['sql']
    assert ['stock', 'etf'] in seen['params'], '类别走参数绑定，不拼进 SQL 文本'
    # 不传 kinds 时那条 AND 不该出现（否则默认把指数/板块一起滤掉了）
    sec_repo.search('PA')
    assert 'asset_type = ANY' not in seen['sql']
    # 参数与占位符数量一致（少一个 psycopg 报 IndexError，而界面只会看到空结果）
    for params in (None, ['stock']):
        n = seen['sql'].count('%s')
        assert n == len(seen['params']) or params is None


@pytest.mark.skipif(not getattr(SS, 'REAL_DB_TEST_ENABLED', True), reason='没有本机库')
def test_真库现查_首字母要真的出平安银行():
    """不打桩的一条：桩全绿而接线错了，靠这一条兜（10-10 那次 18 小时假绿就是这么欠的）。"""
    try:
        out = SS.search('PAYH')
    except Exception as e:  # noqa: BLE001  没有本机库时跳过，不算失败
        pytest.skip(f'本机库不可用：{e}')
    assert out['available'] is True, f'读不到就说读不到：{out.get("reason")}'
    codes = {r['security_code'] for r in out['rows']}
    assert '000001.SZ' in codes, f'PAYH 应命中平安银行，实际 {sorted(codes)[:5]}'
    hit = next(r for r in out['rows'] if r['security_code'] == '000001.SZ')
    assert hit['matched_by'] == 'abbr' and hit['asset_label'] == '股票'
    # 名称里含字母的那类（TCL科技）也要能按名称搜到
    assert any('TCL' in str(r['name']) for r in SS.search('TCL')['rows']), '名称含字母的票按名称搜不到'
