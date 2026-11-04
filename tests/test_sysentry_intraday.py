# -*- coding: utf-8 -*-
"""盘中「交易体系榜」搬到仓库后（`app/intraday/sysentry.py` + 两个作业）的钉住用例。

用户 11-03 的两条原话在这里各是一组判据：
①「判断与取数属于数据加工环节，是取数落地的，跟推送到飞书的那些行为分开，不要互相影响」
   ⇒ A 只写库不发、B 只读库不碰源站（1、2、9 三条）；
②「把它移动过来」⇒ 判据仍是同一把尺子 `sys-1`，且与 `analytics.system_entry` 逐票对得过（10、11）。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.intraday import store, sysentry as S, tasks
from ingest.jobs.push import t06_sysentry_fill as A, t07_sysentry_report as B


# ---------------------------------------------------------------- 分层：谁取数、谁只推送

def test_取数那一炉的参数里没有_send():
    assert tasks.task_args('sysentry_fill', tasks.DEFAULTS) == \
        '-X utf8 -m ingest.jobs.push.t06_sysentry_fill'
    assert tasks.task_args('sysentry', tasks.DEFAULTS) == \
        '-X utf8 -m ingest.jobs.push.t07_sysentry_report --send'
    assert tasks.SPEC['sysentry_fill']['mode'] == 'record'


def test_取数侧不发送_发送侧不碰源站():
    """两边各守一半：A 里出现 `notify`/`send_to` 或 B 里出现取数函数，分层当场就废了。
    B 一旦顺手取数，"重跑内容一致"这条就不成立 —— 那种差别没人能察觉。"""
    import inspect

    src_a = inspect.getsource(S) + inspect.getsource(A)
    for forbidden in ('notify.send_to', 'send_channels'):
        assert forbidden not in src_a, f'A 侧出现了 {forbidden}：取数那一炉不许发送'
    src_b = inspect.getsource(B)
    for forbidden in ('urlopen', 'quotes_sina', 'quotes_tq', 'fetch_pools', 'collect('):
        assert forbidden not in src_b, f'B 侧出现了 {forbidden}：推送这一炉只许读库'


def test_测试进程里硬挡真出网():
    """闸自己的取证：闸坏了，跑一次测试就往新浪/通达信打一轮。"""
    with pytest.raises(RuntimeError, match='pytest'):
        S.no_test_net('quotes_http')


def test_cfg_from_只搬短名不抄第二份默认值():
    cfg = S.cfg_from(tasks.DEFAULTS)
    assert cfg['quote_source'] == tasks.DEFAULTS['sysentry_quote_source']
    assert cfg['preclose_tol_pct'] == tasks.DEFAULTS['sysentry_preclose_tol_pct']
    assert 'sysentry_quote_batch' not in cfg            # 作业侧看到的是短名
    # 配置里写了非法值也别静默：`settings()` 那侧会出声，这里只负责"空值按默认补"
    assert S.cfg_from({'sysentry_quote_batch': ''})['quote_batch'] == \
        tasks.DEFAULTS['sysentry_quote_batch']


# ---------------------------------------------------------------- 落库的形状

def test_table_rows_的列序与_store_的列名一一对齐():
    res = {'entry': [{'security_code': '600001.SH', 'spec_version': 'sys-1', 'close': 10.0,
                      'pre_close': 9.0, 'pct_change': 11.1, 'gap_date': dt.date(2026, 10, 26),
                      'gap_pct': 2.4, 'gap_unfilled': 6, 'pullback_max_vol_ratio': 0.758,
                      'pullback_down_cnt': 2, 'stop_ref_hfq': 9.59, 'adjustment_factor': 1.3,
                      '_name': '甲', '_rank': 1, '_board': '主板'}],
           'watch': []}
    rows = S.table_rows(res, dt.datetime(2026, 11, 3, 14, 39, 0), '通达信TQ', '2026-11-03',
                        dt.date(2026, 11, 3))
    assert len(rows) == 1 and len(rows[0]) == len(store.SYSENTRY_COLUMNS)
    d = dict(zip(store.SYSENTRY_COLUMNS, rows[0]))
    assert (d['trade_date'], d['list_kind'], d['security_code'], d['rank']) == \
        (dt.date(2026, 11, 3), 'entry', '600001.SH', 1)
    assert d['quote_source'] == '通达信TQ' and d['quote_day'] == '2026-11-03'
    assert d['spec_version'] == 'sys-1'                 # 与收盘后那一榜同一把尺子


def test_库里今天没有行时不许说成_今天只():
    """`sysentry_latest_run` 读不到 ⇒ None（调用方播"未就绪"）。
    兜成空表就成了"今天 0 只上榜"—— 那是把"没跑"说成"跑了且没结果"。
    ⚠️ PG 的空集聚合照样回一行（`max` 是 NULL），所以判据必须是那一格的值，不是"有没有回行"。"""
    from app.db import engine
    orig = engine.query_one
    engine.query_one = lambda sql, params=None, role=None: {'captured_at': None,
                                                            'quote_source': None, 'quote_day': None}
    try:
        assert store.sysentry_latest_run(dt.date(2026, 11, 3)) is None
    finally:
        engine.query_one = orig
    engine.query_one = lambda sql, params=None, role=None: {
        'captured_at': dt.datetime(2026, 11, 3, 14, 39), 'quote_source': '通达信TQ',
        'quote_day': '2026-11-03'}
    try:
        got = store.sysentry_latest_run(dt.date(2026, 11, 3))
        assert got and f"{got['captured_at']:%H:%M}" == '14:39'
    finally:
        engine.query_one = orig


def test_pytest_进程里硬挡真写那张表():
    with pytest.raises(RuntimeError, match='analytics 表|intraday_sysentry'):
        store.upsert_sysentry_rows([('x',)])


# ---------------------------------------------------------------- 渲染（B 侧）

def _row(**kw):
    base = {'security_code': '000411.SZ', 'security_name': '某票', 'rank': 1,
            'close': 9.83, 'pre_close': 9.82, 'pct': 0.10, 'gap_date': dt.date(2026, 10, 26),
            'gap_pct': 2.40, 'gap_unfilled': 6, 'pullback_max_vol_ratio': 0.758,
            'pullback_down_cnt': 2, 'stop_ref_hfq': 12.47, 'adjustment_factor': 1.3}
    return {**base, **kw}


def test_fmt_stock_缺的那一格写缺数不写零():
    t = B.tpl()
    assert '缺数' in B.fmt_stock({'security_code': '1.SH', 'close': 1.0, 'pre_close': 1.0}, 1, t)
    line = B.fmt_stock(_row(), 1, t)
    assert '000411.SZ' in line and '9.83' in line and '缺口10-26日' in line
    assert '0.758' in line                              # 库里 numeric(10,4) 回 0.7580，文案按定版三位


def test_摘要读不到时那句必须出声():
    """A 的留痕行没读到时，榜尾不能把"未判几只"当成 0 —— 那是缺数，不是读数。"""
    text = B.build_message({'entry': [_row()], 'watch': []}, None,
                           {'quote_source': '通达信TQ', 'quote_day': '2026-11-03',
                            'captured_at': dt.datetime(2026, 11, 3, 14, 39)},
                           dt.datetime(2026, 11, 3, 14, 40, 30), dict(tasks.DEFAULTS))
    assert '摘要缺数' in text and '未判只数不能当成 0' in text


def test_正文里不许有markdown星号():
    """飞书那侧是纯文本，屏上/群里带 `**` 是这个项目的红线。"""
    text = B.build_message({'entry': [_row()], 'watch': [_row(security_code='2.SZ', rank=1)]},
                           {'no_quote': 3, 'exright': 0, 'mismatch': 2, 'last_day': '2026-11-02',
                            'proven': '49/50只标2026-11-03'},
                           {'quote_source': '通达信TQ', 'quote_day': '2026-11-03',
                            'captured_at': dt.datetime(2026, 11, 3, 14, 39)},
                           dt.datetime(2026, 11, 3, 14, 40, 30), dict(tasks.DEFAULTS))
    assert '**' not in text
    assert '停牌/无报价未判3只' in text and '除权' not in text      # 0 的那一格不念
    assert '涨停价与东财池不一致2只' in text


def test_没有榜那一针时不发空榜(monkeypatch):
    """库里没今天的行 ⇒ 记 `sysentry_no_data` 且 rc=1；带 --send 才播"未就绪"那一句。"""
    codes: list[str] = []
    sent: list[str] = []
    monkeypatch.setattr(B, 'latest_run', lambda day, w: None)
    monkeypatch.setattr(B, 'last_fill_reason', lambda day: 'sysentry_tq_fail:TQ 不可用')
    monkeypatch.setattr(B, '_log', lambda now, what, **kw: codes.append(what))
    monkeypatch.setattr(B, '_send', lambda now, text, keys, **kw: sent.append(text))
    monkeypatch.setattr(tasks, 'send_channels', lambda c=None: ['feishu-main'])
    rc = B.one_round(dry_run=False, send=True, force=True, channel=None, at=None)
    assert rc == 1 and codes == ['sysentry_no_data']
    assert sent and '未就绪' in sent[0] and 'TQ 不可用' in sent[0]


# ---------------------------------------------------------------- 判据（A 侧，纯算）

_BARS = [[f'2026-10-{i + 1:02d}', 10.0, 10.5, 9.8, 10.2, 10.0, 2.0, None, 1000.0, 1.0]
         for i in range(29)]


def _quote(**kw):
    #: `pre_close` 与 `_BARS` 那根的收盘对齐（10.2）—— 不然用例还没走到判据就被"除权基不符"剔掉了
    base = {'name': '甲', 'open': 10.0, 'high': 10.5, 'low': 9.8, 'price': 10.2,
            'pre_close': 10.2, 'vol_shares': 1000.0, 'day': '2026-11-03'}
    return {**base, **kw}


def test_候选预筛与判据的口径不能各写一份():
    """`gap_candidate` 的阈值全部从 `system_entry` 那里 import —— 出现字面量就是抄了第二份。"""
    import inspect

    src = inspect.getsource(S.gap_candidate)
    for name in ('GAP_AGO_MIN', 'GAP_AGO_MAX', 'GAP_MIN_PCT', 'GAP_UNFILLED_DAYS',
                 'LIMIT_LOOKBACK'):
        assert name in src


def test_除权基不符逐票剔除_不拼进序列(monkeypatch):
    """快照昨收与库昨收差过阈值就当除权，拼进去会造出一个假缺口（判据会多出一只上榜票）。"""
    monkeypatch.setattr(S, 'candidates', lambda hist: sorted(hist))
    res = S.build_rows({'600001.SH': _BARS}, {'600001.SH': _quote(pre_close=9.0)},
                       today=dt.date(2026, 11, 3), offline=False, infos={}, zt=set(),
                       cfg=S.cfg_from(tasks.DEFAULTS))
    assert res['exright'] == 1 and res['cand_n'] == 1 and not res['entry'] and not res['watch']


def test_单票判据抛异常不带走整场(monkeypatch):
    """一只票的脏数据不许让今天的榜整场消失（外部那版踩过，靠这道 try 保住其余）。"""
    seen: list[dict] = []
    monkeypatch.setattr(S, 'candidates', lambda hist: sorted(hist))
    quotes = {c: _quote(name=c) for c in ('600001.SH', '600002.SH')}
    orig = S.judge

    def boom(code, series):
        if code == '600001.SH':
            raise ValueError('脏数据')
        return None
    S.judge = boom
    try:
        res = S.build_rows({'600001.SH': _BARS, '600002.SH': _BARS}, quotes,
                           today=dt.date(2026, 11, 3), offline=False, infos={}, zt=set(),
                           cfg=S.cfg_from(tasks.DEFAULTS), note=lambda d: seen.append(d))
    finally:
        S.judge = orig
    assert res['cand_n'] == 2 and [d['what'] for d in seen] == ['sysentry_judge_fail']


# ---------------------------------------------------------------- 补发：按墙钟找那一针

def test_按墙钟找最近那一针_不拿整秒等值匹配():
    """库里存的是带微秒的取数时刻（本机实测 `18:01:36.991056`），人记的是"18:01"。
    等值匹配会匹配不到，然后**正确地把那一针说成不存在** —— 那种"看着像 bug"的坑必须在读侧堵掉。"""
    from app.db import engine
    tz = dt.timezone(dt.timedelta(hours=8))
    seen: list[tuple] = []

    def fake(sql, params=None, role=None):
        seen.append(sql)
        return [{'captured_at': dt.datetime(2026, 11, 3, 18, 1, 36, 991056, tzinfo=tz),
                 'quote_source': '库内日线（对账/复盘）', 'quote_day': '2026-11-03',
                 'drift_s': 36.991}]
    orig = engine.query
    engine.query = fake
    try:
        got = store.sysentry_run_at(dt.date(2026, 11, 3),
                                    dt.datetime(2026, 11, 3, 18, 1, tzinfo=tz))
    finally:
        engine.query = orig
    assert got and f"{got['captured_at']:%H:%M:%S}" == '18:01:36'
    assert 'BETWEEN' in seen[0] and 'ORDER BY drift_s' in seen[0]


def test_超出容差就当真没有那一针():
    from app.db import engine
    orig = engine.query
    engine.query = lambda sql, params=None, role=None: []
    try:
        assert store.sysentry_run_at(dt.date(2026, 11, 3),
                                     dt.datetime(2026, 11, 3, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))) is None
    finally:
        engine.query = orig


def test_补发指定那一针但库里没有时不发空榜(monkeypatch):
    """`--at 09:00` 找不到那一针 ⇒ 记 `sysentry_no_data`、rc=1，绝不拿别的时针冒充。"""
    codes: list[str] = []
    monkeypatch.setattr(store, 'sysentry_run_at', lambda day, want, **kw: None)
    monkeypatch.setattr(B, '_log', lambda now, what, **kw: codes.append(what))
    monkeypatch.setattr(tasks, 'load_cfg', lambda: (dict(tasks.DEFAULTS), []))
    rc = B.one_round(dry_run=False, send=False, force=True, channel=None, at='09:00')
    assert rc == 1 and codes == ['sysentry_no_data']


# ---------------------------------------------------------------- 真库回验（不打桩）

def test_表真的在库里_且列名与插入列序一致():
    """DDL 60 没执行、或执行的是旧版 ⇒ 这条红。读侧与写侧的列名必须与库里现查的一致。"""
    from app.db import engine

    cols = [r['column_name'] for r in engine.query(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = 'analytics' AND table_name = 'intraday_sysentry'"
        ' ORDER BY ordinal_position', role='reader')]
    assert cols, 'analytics.intraday_sysentry 不存在（app/db/sql/60_intraday_sysentry.sql 没执行）'
    for name in store.SYSENTRY_COLUMNS:
        assert name in cols, f'插入要写的列 {name} 库里没有'
    assert {'trade_date', 'captured_at', 'security_code', 'spec_version'} <= set(cols)
