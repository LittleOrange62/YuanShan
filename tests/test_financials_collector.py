# -*- coding: utf-8 -*-
"""财务多期采集器（`ingest/jobs/c25_financials_ths.py`）的口径与自证。

这个作业是唯一往 `reference_data.stock_financial_period` 写数的地方，也是本机唯一的财务源。
钉的都是 10-16 那天真炸过的形状（源给 NaN ⇒ 整只票写不进去；预算用完 ⇒ main 汇总 KeyError），
不是格式洁癖。测试一律 monkeypatch `_fetch_raw`，pytest 进程里真出网会被那道硬闸挡掉。
"""
from __future__ import annotations

import datetime as dt
import json
import math

import pytest

from ingest.jobs import c25_financials_ths as c25


def raw_row(**kw):
    base = {'报告期': '2026-03-31', '营业总收入': '547.03亿', '净利润': '11.56亿',
            '扣非净利润': False, '营业总收入同比增长率': '10.23%',
            '销售毛利率': '20.56%', '净资产收益率': '10.00%', '每股经营现金流': '--'}
    base.update(kw)
    return base


# ------------------------------------------------------------------ 单位解析

@pytest.mark.parametrize('v, want', [
    ('547.03亿', 547.03e8),
    ('1.2万', 1.2e4),
    ('2,050.67', 2050.67),          # 千分位吃掉
    ('2050.67元', None),                # 「元」这个单位源里没出现过；认不出就给 None，不猜
    ('11.56%', 11.56),
    (123.45, 123.45),
    (0, 0.0),                       # 源真给 0 才是 0
    (False, None), (True, None), (None, None), ('', None),
    ('--', None), ('-', None), ('未披露', None), ('不适用', None),
    ('nan', None), (float('nan'), None), (float('inf'), None),
])
def test_to_num_只在源真给数时才给数(v, want):
    got = c25._to_num(v)
    if want is None:
        assert got is None
    else:
        assert got == pytest.approx(want)


def test_金额列没带单位就不猜数量级():
    """裸数可能是元、可能是万、可能是亿 —— 猜错是百倍误差，宁缺。"""
    assert c25._to_num('205.67', money=True) is None
    assert c25._to_num('205.67亿', money=True) == pytest.approx(205.67e8)


# ------------------------------------------------------------------ NaN 与 raw

def test_源里的NaN会变成null而不是裸NaN():
    """10-16 实测：`json.dumps` 默认写出裸 `NaN`，Python 读得回去、PG 的 jsonb 读不回，
    于是那一整只票的全部行都写不进去（本次回填 313 只就是这么折的）。"""
    got = c25._json_safe({'a': float('nan'), 'b': [float('inf'), 1.0], 'c': {'d': float('-nan')}})
    assert got == {'a': None, 'b': [None, 1.0], 'c': {'d': None}}
    s = json.dumps(got, ensure_ascii=False, allow_nan=False)   # 不带 allow_nan=False 才是事故现场
    assert 'NaN' not in s and 'Infinity' not in s


def test_parse_rows留下的raw一定能进jsonb():
    rows = c25.parse_rows('600519.SH', 'cum', [raw_row(营业周期=float('nan'))])
    assert len(rows) == 1
    blob = rows[0]['raw']
    assert isinstance(blob, str) and 'NaN' not in blob
    assert json.loads(blob)['营业周期'] is None


def test_解析不出来的列是NULL不是零():
    rows = c25.parse_rows('600519.SH', 'cum', [raw_row()])
    r = rows[0]
    assert r['deduct_profit_yuan'] is None          # 源给了 False
    assert r['revenue_yuan'] == pytest.approx(547.03e8)
    assert r['revenue_yoy_percent'] == pytest.approx(10.23)
    assert r['period_kind'] == 'cum' and r['source'] == c25.SOURCE


def test_报告期解析不出来的那一行整行丢掉():
    """主键没有它就没法幂等写；留着只会写出一条 report_date=NULL 的孤行。"""
    assert c25.parse_rows('600519.SH', 'cum', [raw_row(报告期='')]) == []
    assert c25.parse_rows('600519.SH', 'cum', [raw_row(报告期='2026年报')]) == []   # 认不出的形状不硬凑
    assert c25.parse_rows('600519.SH', 'cum',
                          [raw_row(报告期='20260331')])[0]['report_date'] == dt.date(2026, 3, 31)


@pytest.mark.parametrize('code, want', [
    ('600519', '600519.SH'), ('000001', '000001.SZ'), ('300750', '300750.SZ'),
    ('688137', '688137.SH'), ('920270', '920270.BJ'), ('430047', '430047.BJ'),
    ('600519.SH', '600519.SH'), ('abc', ''), ('12345', ''), ('', ''),
])
def test_认不出的号段直接丢不猜市场(code, want):
    """猜错市场＝把数写到另一只票身上，比缺一格坏得多。"""
    assert c25._full(code) == want


# ------------------------------------------------------------------ 出网闸与 run 的返回形状

def test_pytest进程里真出网被硬挡():
    import os
    assert os.environ.get('PYTEST_CURRENT_TEST'), '这条只能在 pytest 里跑'
    with pytest.raises(RuntimeError):
        c25._fetch_raw('600519.SH', '按报告期')


def test_run在预算用完时照样交齐汇总要的键(monkeypatch):
    """`main()` 最后一句读 `res["seconds"]`。缺了就是 KeyError + rc 1 ——
    "按设计收工"会被屏幕读成"跑到一半崩了"（10-16 实测）。"""
    seen = []
    monkeypatch.setattr(c25, 'collect',
                        lambda code, kinds=('cum', 'single'), max_periods=0:
                        ([raw_row_dict(code)], []) if not seen.append(code) else ([], []))
    monkeypatch.setattr(c25, 'write_rows', lambda rows: len(rows))
    ticks = iter([0.0, 0.0, 1e7, 1e7, 1e7, 1e7])               # t0=0，第二只之前已经过了 10⁷ 秒
    monkeypatch.setattr(c25.time, 'time', lambda: next(ticks))
    res = c25.run(['600519.SH', '000001.SZ'], budget_min=0.0001)
    assert res['budget_out'] is True and res['left'] >= 1
    for k in ('ok', 'fail', 'written', 'notes', 'left', 'budget_out', 'seconds'):
        assert k in res, f'汇总缺 {k}：main 会 KeyError'
    assert isinstance(res['seconds'], float) or isinstance(res['seconds'], int)


def raw_row_dict(code):
    return {'security_code': code, 'report_date': dt.date(2026, 3, 31), 'period_kind': 'cum',
            'revenue_yuan': 1.0, 'raw': '{}'}


def test_dry_run不写库也报得出行数(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(c25, 'collect',
                        lambda code, kinds=('cum', 'single'), max_periods=0: ([raw_row_dict(code)], []))
    monkeypatch.setattr(c25, 'write_rows', lambda rows: calls.append(rows) or len(rows))
    res = c25.run(['600519.SH'], write=False)
    assert res['ok'] == 1 and res['written'] == 0 and calls == [], '--dry-run 不该碰写库那一发'


# ------------------------------------------------------------------ SQL 的形状与真实覆盖

def test_UPSERT的列数与占位符数一致_冲突键就是主键():
    sql = c25.UPSERT
    cols = sql.split('values')[0]
    assert cols.count(',') + 1 == sql.split('values (')[1].split(')')[0].count(',') + 1, \
        '列数与占位符数不一致：executemany 会整批报错'
    assert 'on conflict (security_code, report_date, period_kind)' in sql, \
        '主键就是这三列；冲突键写窄了会把另一档的数盖掉'


def test_库里真的写进去过():
    """不打桩的一条：回查 min/max/只数/两档（rc=0 与日志都会说谎）。"""
    from app.db import engine
    r = engine.query_one(
        f'select count(distinct security_code) codes, count(*) n, min(report_date)::text a, '
        f'max(report_date)::text z, '
        f'count(*) filter (where period_kind=%s) cum, count(*) filter (where period_kind=%s) single '
        f'from {c25.TABLE}', ('cum', 'single'), role='reader')
    assert r and r['n'] > 1000, f'表里只有 {r["n"] if r else 0} 行 —— 回填没跑还是写库坏了？'
    assert r['single'] > 0, '单季档一行都没有：两档只写了一档'
    assert r['codes'] > 500, f'只写了 {r["codes"]} 只'
    bad = c25.verify()
    assert not bad, '覆盖体检报了：' + '；'.join(bad)


# ============================================================ 闭环三件：批账 / 覆盖率分母 / 缺口优先


def test_母体顺序本机名单在前缺口尾巴在后且去重():
    """`order_refresh` 是纯函数（不碰库），钉的是"缺口不会把当天新鲜挤掉、也不会重复刷"。"""
    assert c25.order_refresh(['600519.SH', '000001.SZ'],
                             ['000001.SZ', '920575.BJ', '', None]) == \
        ['600519.SH', '000001.SZ', '920575.BJ']
    assert c25.order_refresh([], ['A']) == ['A']
    assert c25.order_refresh(['A'], []) == ['A'], '尾巴空时必须原样交名单，不能交空表'


def test_预算分两档缺口先拿钱名单不会被挤死(monkeypatch):
    """10-16 实测的算术：本机名单 1,122 只 × 1.3 秒 ≈ 24 分钟，而每日预算只有 6 分钟。

    所以"排成一条队"无论谁在前都是错的：名单在前 ⇒ 缺口一辈子轮不到（我第一版就是这样，
    跑起来才发现尾巴一行都没执行）；缺口在前 ⇒ 名单几天刷不到新披露。
    这条钉的是**两档各自拿一份预算**，两档都按"最旧优先"往前推。
    """
    monkeypatch.setattr(c25, 'watchlist_codes', lambda: ['600519.SH', '000001.SZ'])
    monkeypatch.setattr(c25, 'coverage_gap', lambda: {'master': 6, 'have': 5, 'missing': ['000936.SZ']})
    monkeypatch.setattr(c25, 'stale_codes',
                        lambda days, limit=0: {'60': ['000936.SZ', '301131.SZ', '600519.SH'],
                                               '1': ['600519.SH', '000001.SZ']}[str(int(days))])
    plan = c25.refresh_plan(6.0)
    assert [p[0] for p in plan] == ['缺口/名单外', '本机名单']
    gap_ph, watch_ph = plan
    assert gap_ph[1][0] == '000936.SZ', '表里一行都没有的必须排第一（新股与写失败的先吃）'
    assert '301131.SZ' in gap_ph[1] and '600519.SH' not in gap_ph[1], '名单里的票不该混进名单外那一档'
    assert watch_ph[1] == ['600519.SH', '000001.SZ']
    assert gap_ph[2] == round(6.0 * c25.GAP_SHARE, 2) and watch_ph[2] == round(6.0 * (1 - c25.GAP_SHARE), 2)
    assert gap_ph[2] > 0 and watch_ph[2] > 0, '两份预算都必须 >0，否则那一档永远不执行'
    # 没给预算时不分档（显式跑 --all / --code 的路径），但仍要给出两列队列
    assert [p[0] for p in c25.refresh_plan(None)] == ['缺口/名单外', '本机名单']
    assert all(len(p) == 2 for p in c25.refresh_plan(0))


def test_run_plan把两档合成一份汇总且逐档报剩多少(monkeypatch, capsys):
    calls = []

    def fake_run(codes, **kw):
        calls.append((list(codes), kw.get('budget_min')))
        return {'ok': len(codes), 'fail': 0, 'written': 2 * len(codes), 'notes': [],
                'failed_codes': [], 'left': 0, 'budget_out': False, 'seconds': 1.0}

    monkeypatch.setattr(c25, 'run', fake_run)
    res = c25.run_plan([('缺口/名单外', ['A', 'B'], 2.1), ('本机名单', ['C'], 3.9)])
    assert calls == [(
        ['A', 'B'], 2.1), (['C'], 3.9)], calls
    assert res['ok'] == 3 and res['written'] == 6 and res['seconds'] == 2.0
    assert [p['label'] for p in res['phases']] == ['缺口/名单外', '本机名单']
    out = capsys.readouterr().out
    assert '阶段「缺口/名单外」：要刷 2 只' in out and '这一档跑完了' in out



def test_覆盖率闸的分母是主档不是表自己(monkeypatch):
    """10-16 真炸的那条：缺 11 只时旧 verify() 打印的是「5566 只（占 5566 只的 100%）」。"""
    seen = {}

    def q_one(sql, params=(), **kw):
        seen['master'] = sql
        return {'n': 5577}

    def q_all(sql, params=(), **kw):
        seen['gap'] = sql
        return [{'c': '000408.SZ'}, {'c': '600201.SH'}]

    monkeypatch.setattr(c25.engine, 'query_one', q_one)
    monkeypatch.setattr(c25.engine, 'query', q_all)
    g = c25.coverage_gap()
    assert g['master'] == 5577 and g['have'] == 5575 and g['missing'] == ['000408.SZ', '600201.SH']
    assert "asset_type = 'stock'" in seen['master'] and 'reference_data.security' in seen['gap'], \
        '分母与差集都必须来自主档，不是来这张表自己数一遍'


def test_覆盖率闸读不到时抛错不交空缺口(monkeypatch):
    """空缺口会被下游念成"全齐了"，所以库不通必须是异常（`detectors must not lie on error`）。"""
    monkeypatch.setattr(c25.engine, 'query_one', lambda *a, **k: None)
    with pytest.raises(RuntimeError):
        c25.coverage_gap()

    monkeypatch.setattr(c25.engine, 'query_one', lambda *a, **k: {'n': 100})
    monkeypatch.setattr(c25.engine, 'query', lambda *a, **k: None)
    with pytest.raises(RuntimeError):
        c25.coverage_gap()


def test_有缺口时verify必须点名并给出现成的补法(monkeypatch):
    monkeypatch.setattr(c25, 'coverage_gap',
                        lambda: {'master': 100, 'have': 99, 'missing': ['000408.SZ']})
    monkeypatch.setattr(c25.engine, 'query_one', lambda sql, params=(), **kw: {
        'codes': 99, 'periods': 200, 'a': '2001-12-31', 'z': '2026-09-30', 'n': 5000,
        'cum': 2600, 'single': 2400, 'no_rev': 0, 'fresh': '2026-10-16', 'c': 99})
    bad = c25.verify()
    assert any('主档' in b and '1 只' in b for b in bad), bad
    assert any('--refresh-gap' in b for b in bad), '报缺口要给现成的补法，不然等于让人重新发明'


def test_逐只失败必须带着代码回来并进批账(monkeypatch):
    """只打进 stdout 的失败等于没有失败：这两件事（returned list + 批账行）是那 11 只的兜底。"""
    monkeypatch.setattr(c25, 'collect', lambda code, kinds=('cum', 'single'), max_periods=0:
                        ([], ['cum:RuntimeError 源挂了']))
    res = c25.run(['000408.SZ', '600201.SH'])
    assert res['failed_codes'] == ['000408.SZ', '600201.SH'] and res['ok'] == 0

    done = {}
    import app.db.repo.ops as ops
    monkeypatch.setattr(ops, 'end_batch',
                        lambda bid, **kw: done.update(dict(bid=bid, **kw)))
    c25._write_batch(9, res, {'master': 100, 'have': 98, 'missing': ['000408.SZ', '600201.SH']},
                     ('cum', 'single'), 6)
    assert done['bid'] == 9 and done['status'] == 'failed'
    assert '缺 2 只' in done['message'] and '000408.SZ' in done['message'], done['message']
    assert done['row_count'] == 0


def test_批账写不进也不能把采集判成失败(monkeypatch, capsys):
    """数据已经落库了，账本坏了要出声但不能改结果 —— 无声的坏账本下一步就没人知道跑没跑。"""
    import app.db.repo.ops as ops

    def boom(*a, **k):
        raise RuntimeError('permission denied for table ingest_batch')

    monkeypatch.setattr(ops, 'end_batch', boom)
    c25._write_batch(3, {'ok': 5, 'fail': 0, 'written': 10, 'notes': [], 'failed_codes': [],
                         'left': 0, 'budget_out': False, 'seconds': 1.0},
                     {'master': 5, 'have': 5, 'missing': []}, ('cum',), 6)
    assert '批账没写成' in capsys.readouterr().out


def test_开账前会先收掉上一跑没写结束的账(monkeypatch):
    """`main()` 里真的调到了这一句，而不是只把函数写在文件里。"""
    seen = {'n': 0}

    def fake_reap():
        seen['n'] += 1
        return 2

    monkeypatch.setattr(c25, '_reap_open_batches', fake_reap)
    monkeypatch.setattr(c25, 'watchlist_codes', lambda: ['600519.SH'])
    monkeypatch.setattr(c25, 'run', lambda codes, **kw: {
        'ok': 1, 'fail': 0, 'written': 2, 'notes': [], 'failed_codes': [],
        'left': 0, 'budget_out': False, 'seconds': 0.1})
    monkeypatch.setattr(c25, 'coverage_gap',
                        lambda: {'master': 1, 'have': 1, 'missing': []})
    monkeypatch.setattr(c25, 'verify', lambda: [])
    import app.db.repo.ops as ops
    monkeypatch.setattr(ops, 'begin_batch', lambda *a, **k: 77)
    monkeypatch.setattr(ops, 'end_batch', lambda *a, **k: None)
    rc = c25.main(['--max-minutes', '1'])
    assert seen['n'] == 1, '开新账之前没收上一跑的 open 账'
    assert rc in (0, 3), rc


def test_收不掉上一跑的账不许把这一跑判成失败(monkeypatch, capsys):
    """账本那边坏了（权限/列名），这一边数据照样该写 —— 出声但不改结果。"""
    def boom(*a, **k):
        raise RuntimeError('relation operations.ingest_batch does not exist')

    monkeypatch.setattr(c25.engine, 'execute', boom)
    assert c25._reap_open_batches() == 0
    assert '收不掉' in capsys.readouterr().out


def test_真库上一跑留下的running确实被收成aborted():
    """不打桩的一条（列名写错只有真库能抓到）。
    10-16 实测：我 Ctrl+C 停掉那一跑之后 `batch_id 641` 就挂在 `running`、`row_count` 是空的 ——
    这一条要证明的是"下一次开账会自己把它收掉"，不是靠人记得去 UPDATE。
    只有当场上有**刚刚开跑**（15 分钟内）的同 target 账时才让路（那可能是每日链那一步真在跑），
    挂着几小时不动的那种正是被杀掉的死账，收掉它就是要修的东西。
    """
    import app.db.repo.ops as ops
    from app.db import engine
    source = f'{c25.SOURCE}:stock_financial_abstract'
    others = engine.query(
        "select batch_id, now() - started_at > interval '15 minutes' AS stale "
        "from operations.ingest_batch "
        "where target = %s and source = %s and status = 'running'",
        (c25.TABLE, source), role='reader')
    if [o for o in (others or []) if not o['stale']]:
        pytest.skip('有 15 分钟内才开的同 target 批账（每日链那一步在跑），测试不动别人的账')
    stale_before = len(others or [])
    bid = ops.begin_batch(source, c25.TABLE, source_path='test:reap', role='etl')
    try:
        assert engine.scalar(
            "select status from operations.ingest_batch where batch_id = %s", (bid,), role='reader') == 'running'
        assert c25._reap_open_batches() == stale_before + 1, \
            '收口的条数对不上：要么漏收了死账，要么 WHERE 太宽把别人的账也收了'
        row = engine.query_one(
            "select status, message, (finished_at is not null) as closed "
            "from operations.ingest_batch where batch_id = %s", (bid,), role='reader')
        assert row and row['status'] == 'aborted', row
        assert row['closed'], '收口必须同时写 finished_at，否则看账的人还是分不清跑没跑完'
        assert '没写结束' in (row['message'] or ''), row['message']
        live = engine.query(
            "select batch_id from operations.ingest_batch "
            "where target = %s and source = %s and status = 'running'",
            (c25.TABLE, source), role='reader')
        assert not live, f'收过一轮之后还挂着 open 账：{live}'
    finally:
        # `ft_etl` 对账本只有 SELECT/INSERT/UPDATE（10-16 实测 DELETE 报 permission denied），
        # 而测试造的这一行不该留在真账本里（`_rejected` 188 份就是测试泄漏的前例）⇒ 用 admin 收干净。
        deleted = engine.execute("delete from operations.ingest_batch where batch_id = %s",
                                 (bid,), role='admin')
        assert deleted == 1, f'测试造的批账没删干净（batch_id {bid}），手工清一次'


def test_每日链那一步真的带上了缺口尾巴():
    """读参数不等于接上：这一步之前只传 `--max-minutes`，所以缺口永远没人捞。"""
    import pathlib
    src = pathlib.Path('ingest/jobs/e1_fill_all.py').read_text(encoding='utf-8')
    line = [l for l in src.splitlines() if "'financials': ('ingest.jobs.c25_financials_ths'" in l]
    assert line and "--refresh-gap" in line[0] and "--max-minutes" in line[0], line


def test_本机名单与母体那两条SQL真的打得通():
    """不打桩的一条。10-16 实测：`watchlist_codes()` 里写了 `snapshot_at`，而这张表的列是
    `captured_at` + `trade_date` —— 打桩的测试永远绿，每日链那一步（母体＝本机名单）会直接 rc 1 崩在
    `UndefinedColumn` 上。凡是"只在本机 schema 上才会露出来"的 SQL，都要留一条真库直跑的。
    ⚠️ 同一天傍晚又被这条抓到第二形态：**SQL 打得通、却交出空表**（列名对上、条件写错也一样是空）。
    只断言"是个 list、里面的号带后缀"对空表恒真 ⇒ 现在拿表里最新一场的只数当尺子交叉核一遍。
    """
    from app.db import engine
    codes = c25.watchlist_codes()
    assert isinstance(codes, list)
    assert all('.' in c for c in codes), f'母体里出现了没带市场后缀的号：{codes[:5]}'
    in_table = engine.scalar(
        "select count(distinct security_code) from reference_data.watchlist_member_snapshot "
        "where trade_date = (select max(trade_date) from reference_data.watchlist_member_snapshot)",
        (), role='reader') or 0
    assert len(codes) == in_table, \
        f'表里最新一场有 {in_table} 只，母体只交出 {len(codes)} 只 ⇒ SQL 打得通但筛没了'
    tail = c25.stale_codes(45, 5)
    assert isinstance(tail, list)
    plan = c25.refresh_plan(None)                # 不切预算，只看两档的队列拼得出来
    assert sum(len(p[1]) for p in plan) >= 1 or c25.coverage_gap()['missing'] == [], \
        '两档都空意味着名单为空且库里没有缺口（那不是失败），但函数本身不许抛'
    assert c25.coverage_gap()['master'] >= 5000, '主档只数读出来这么小，先看 reference_data.security 还在不在'


def test_没票可刷那句必须点名三档不许假称名单为空(monkeypatch, capsys):
    """10-16 傍晚实测：全市场当天刚刷完 ⇒ 两档都是 0 只、rc 2（按设计不写，是对的），
    而那句打印写的是「本机名单为空」—— 表里最新一场有 1,121 只，那是假话。
    假话会把人支去查自选同步，而真答案是「库里都还新」。
    """
    monkeypatch.setattr(c25, 'watchlist_codes', lambda: ['600519.SH', '000001.SZ', '300750.SZ'])
    monkeypatch.setattr(c25, 'coverage_gap', lambda: {'master': 5577, 'have': 5577, 'missing': []})
    monkeypatch.setattr(c25, 'stale_codes', lambda days, limit=0: [])
    assert c25.main(['--refresh-gap', '60', '--max-minutes', '6']) == 2
    out = capsys.readouterr().out
    assert '本机名单 3 只里超过 1 天没采的 0 只' in out, out
    assert '①表里一行都没有的 0 只' in out and '②名单外且 60 天没采的 0 只' in out, out
    assert '本机名单为空' not in out and '本机名单是 0 只' not in out, out


def test_名单真的为空时那一句要指向自选同步(monkeypatch, capsys):
    """同一个分支的另一头：这回真是 0 只，就得说清去哪一步看。"""
    monkeypatch.setattr(c25, 'watchlist_codes', lambda: [])
    monkeypatch.setattr(c25, 'coverage_gap', lambda: {'master': 5577, 'have': 5577, 'missing': []})
    monkeypatch.setattr(c25, 'stale_codes', lambda days, limit=0: [])
    assert c25.main(['--refresh-gap', '60', '--max-minutes', '6']) == 2
    out = capsys.readouterr().out
    assert '本机名单是 0 只' in out and '自选同步' in out, out

