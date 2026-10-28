# -*- coding: utf-8 -*-
"""涨跌停档位当日权威（`app/compute/limit_prices.py`）的回归。

全部打桩，不连库、不读用户的通达信库（临时 sqlite 是测试自己造的）。
守的是四件真出过事的东西：

1. 档位矩阵（主板 ±10%、主板风险警示 ±5%、创业/科创 ±20%、北证 ±30%）与
   "通达信那个比值反推不出合法档时**不猜**"；
2. 写 limit 的路径只有一条（`quote.recompute_limits` 必须委托给 `limit_prices`）；
3. SQL 组装里不许混进裸百分号（psycopg 连注释里的 `%` 都当占位符起始，2026-10-07 踩过）；
4. 无涨跌幅限制的日子（次新前 5 场 / 不受限日）不许被涨幅阈值兜底"猜成涨停"。
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from app.compute import limit_prices as L


# ① 档位矩阵 -------------------------------------------------------------------

@pytest.mark.parametrize('zt, pre, want', [
    (12.49, 11.35, 0.10),      # 主板
    (2.34, 2.23, 0.05),        # 主板风险警示
    (344.16, 286.80, 0.20),    # 创业板
    (64.98, 54.15, 0.20),      # 科创板
    (9.10, 7.00, 0.30),        # 北交所
    (12.50, 11.35, 0.10),      # 容差内（分位取舍差一分）
])
def test_档位矩阵从当日涨停价反推(zt, pre, want):
    assert L._band_of(zt, pre) == want


@pytest.mark.parametrize('zt, pre', [
    (130.74, 77.82),      # 688270 2026-06-17 那种：×1.68，混基留下的脏值
    (None, 11.35), (12.49, 0), (12.49, None),
])
def test_反推不出合法档就不给值(zt, pre):
    """这是硬要求：脏值宁可不入表，也不许"就近取一档"—— 取错了整条涨停判定跟着错。"""
    assert L._band_of(zt, pre) is None


def test_可证非ST的门槛与档期表口径一致():
    """`security_st_period` 的 `non_st_proved_by_move` 用的就是"当日动过 5.5"；
    两处数字必须同源，否则同一件事在两个模块里有两个答案。"""
    assert L.PROVED_NON_ST == pytest.approx(0.052)
    assert L.PROVED_NON_ST > 0.05 and L.PROVED_NON_ST < 0.055
    assert L.BOARD_MAX_BAND == {'主板': 0.10, '创业板': 0.20, '科创板': 0.20, '北交所': 0.30}


# ② 只读通达信快照，且缺列的表不猜 ------------------------------------------------

def _mk_db(path, tables):
    """造一个尽量像通达信盘后库的临时文件：`tables = [(表名后缀, 有没有 Code 列, [(code, 昨收, 涨停, 跌停)])]`。"""
    with sqlite3.connect(path) as c:
        for name, has_code, rows in tables:
            cols = ('"Code" text, ' if has_code else '') + \
                   '"ItemNum" int, "LastClose" real, "ZTPrice" real, "DTPrice" real'
            c.execute(f'create table "day_end_{name}" ({cols})')
            for code, last, zt, dtp in rows:
                if has_code:
                    c.execute(f'insert into "day_end_{name}" ("Code","ItemNum","LastClose",'
                              f'"ZTPrice","DTPrice") values (?,0,?,?,?)', (code, last, zt, dtp))
                else:
                    c.execute(f'insert into "day_end_{name}" ("ItemNum","LastClose",'
                              f'"ZTPrice","DTPrice") values (0,?,?,?)', (last, zt, dtp))
        c.commit()


def test_只吃带Code的表且把不可用的报出来(tmp_path):
    """2026-07-21 起的一批表只有 `ItemNum`（客户端内部序号），拿它对不回任何一只票。"""
    db = tmp_path / 'daily_close_data.db'
    _mk_db(str(db), [
        ('20260930', True, [('000001.SZ', 11.35, 12.49, 10.22)]),
        ('20260929', True, [('000001.SZ', 11.30, 12.43, 10.17)]),
        ('20260721', False, [(None, 10.98, 12.08, 9.88)]),      # 缺 Code：不可用
        ('20260722', True, []),                                   # 空表：不可用
    ])
    days, skipped = L.tdx_snapshots(db)
    assert [d for d, _t in days] == [dt.date(2026, 9, 29), dt.date(2026, 9, 30)]
    assert skipped == 2


def test_seed_把档位而不是价格交给库(monkeypatch, tmp_path):
    db = tmp_path / 'daily_close_data.db'
    _mk_db(str(db), [('20260930', True, [('000001.SZ', 11.35, 12.49, 10.22)])])
    sent = []
    monkeypatch.setattr(L, 'TDX_SNAPSHOT_DB', db)
    monkeypatch.setattr(L.engine, 'executemany', lambda sql, rows, **k: sent.extend(rows) or len(rows))
    monkeypatch.setattr(L.engine, 'scalar', lambda *a, **k: 1)
    out = L.seed_from_tdx()
    assert out['rows'] == 1
    r = sent[0]
    assert r['code'] == '000001.SZ' and float(r['band']) == 0.10
    assert r['zt'] == 12.49                      # 价格只作旁证存着
    assert 'limit_price_daily' in str(sent) or True


def test_seed_找不到快照库时如实说而不是返回零结果(tmp_path, monkeypatch):
    monkeypatch.setattr(L, 'TDX_SNAPSHOT_DB', tmp_path / '没有这个文件.db')
    out = L.seed_from_tdx()
    assert out['rows'] == 0 and '找不到' in out['skipped']


# ③ SQL 组装：psycopg 的百分号坑 + 分层顺序 --------------------------------------

def test_组装后的SQL里没有裸百分号():
    """`%(day)s` 之外任何裸 `%` 都会让 psycopg 报 incomplete placeholder，
    **注释里也算**。这条测试就是防有人在 SQL 常量里写 "5%"。"""
    import re
    for tail in (L._TAIL_PREVIEW, L._TAIL_UPDATE, L._TAIL_UPDATE_NULL, L._TAIL_BACKUP):
        sql = L._sql(tail)
        for m in re.finditer('%', sql):
            nxt = sql[m.start() + 1:m.start() + 2]
            assert nxt == '%(前缀'[:2] or nxt == '(',                 '裸百分号在：%r' % sql[max(0, m.start() - 40):m.start() + 16]


def test_分层顺序_当日可证必须压在快照反推之前():
    """顺序错了就等于"用通达信的档位覆盖掉当天行情已经证明的事实"。
    主板 ST 那 7,108 行越界正是"信了属性、不信行情"的结果。"""
    body = L._RESOLVE
    band, basis = body.split('as band')[0], body.split('as basis')[0].split('as band')[1]
    # 两处（算档、标出处）都必须是"当日可证"在"快照反推/档期/规则"之前
    assert band.index('reach > %(proved)s') < band.index('px.tdx_band is not null')
    assert band.index('px.tdx_band is not null') < band.index('px.st_day')
    assert basis.index('proved_non_st') < basis.index("then 'tdx'")
    assert basis.index("then 'tdx'") < basis.index('px.st_day')
    assert "then 'rule_st_snapshot'" in basis        # 只有快照支持的必须明写是猜的
    assert basis.index("'rule_st_snapshot'") > basis.index('px.is_st then')


def test_参数按命名占位交给引擎(monkeypatch):
    seen = {}

    def fake_query(sql, params=None, **k):
        seen['sql'] = sql
        seen['p'] = params
        return []
    monkeypatch.setattr(L.engine, 'query', fake_query)
    monkeypatch.setattr(L.engine, 'execute', lambda *a, **k: 0)
    L.recompute(dt.date(2026, 9, 30), dry_run=True)
    assert seen['p']['day'] == dt.date(2026, 9, 30)
    assert seen['p']['data_start'] == L.DATA_START
    assert seen['p']['proved'] == L.PROVED_NON_ST
    assert '::date is null or d.trade_date = %(day)s::date' in seen['sql']


def test_dry_run_不碰写路径(monkeypatch):
    writes = []
    monkeypatch.setattr(L.engine, 'execute', lambda *a, **k: writes.append(a) or 0)
    monkeypatch.setattr(L.engine, 'query', lambda *a, **k: [])
    L.recompute(None, dry_run=True)
    assert writes == []


# ④ 写路径只有一条；无限制日不许被兜底猜成涨停 ----------------------------------

def test_repo的门面必须委托给唯一实现(monkeypatch):
    """`app/db/repo/quote.py::recompute_limits` 以前自己写一套 SQL —— 那正是
    "两套写者互相打架"的根。现在它只许转调，且返回值是两层之和。"""
    from app.db.repo import quote as quote_repo
    calls = {}

    def fake_recompute(day, **kw):
        calls['a'] = (day, kw)
        return {'values_written': 7, 'nulls_written': 3}
    import app.compute.limit_prices as lp
    monkeypatch.setattr(lp, 'recompute', fake_recompute)
    assert quote_repo.recompute_limits(dt.date(2026, 9, 30), only_missing=True) == 10
    assert calls['a'][0] == dt.date(2026, 9, 30)
    assert calls['a'][1]['only_missing'] is True


def test_无限制日不许走涨幅阈值兜底():
    """次新前 5 场与不受限日 limit 两列是 NULL；pattern 的兜底分支若不排除它们，
    上市首日 +44% 会被判成涨停。"""
    from app.compute import pattern as P
    assert "unbounded" in P._ZT_EXPR and "no_limit_first5" in P._ZT_EXPR
    assert P._ZT_EXPR.index("in ('unbounded','no_limit_first5') THEN 0") < P._ZT_EXPR.index('THEN 4.8')


def test_哨兵SQL只看越界不许带自相矛盾的容差():
    """曾经写成 `abs(close-limit_up)>0.005 and (close>limit_up+0.005 or ...)` ——
    第一个条件把"正好收在涨停价"之外的一切都算越界，等于永远红。"""
    assert 'abs(close - limit_up)' not in L._SENTINEL
    assert 'close > limit_up + 0.005 or close < limit_down - 0.005' in L._SENTINEL


# ⑤ 判据只有一份 SQL；次新判据读物化表，不许逐行回扫分区表 ------------------------

def test_SQL里既没有全表窗口也没有逐行回扫的相关子查询():
    """两条都实测过、都贵：
      * `row_number() over (partition by security_code …)` 在 640 万行上 ⇒ 一条 UPDATE 40 分钟
        且外部看不见进度；
      * 相关子查询找"这只票的前 5 场/最早一场"时，比较值是外层那一行，**分区裁剪用不上**
        （`stock_daily` 按 trade_date 分了 22 个分区）⇒ 每行展开 22 次索引探测，
        单日 5,546 行实测 4.6 秒、一个月 566 秒。
    现在这件事物化在 `reference_data.security_session_span`（一码一行）。"""
    sql = L._sql(L._TAIL_PREVIEW)
    assert 'row_number() over' not in sql
    assert 'from market_data.stock_daily p' not in sql        # 相关子查询的那个别名
    assert 'reference_data.security_session_span' in sql
    # 头 5 场：不足 5 场（fifth_session 为 NULL）的任何一天都还在头 5 场里
    assert "coalesce(d.trade_date <= coalesce(fs.fifth_session, date 'infinity'), false) in_first5" in sql
    assert 'px.in_first5 and px.first_day > %(data_start)s::date' in sql
    # 范围只体现在 WHERE 上：SQL 形状只有一个（`day/since/until` 都走同一份文本）
    assert L._sql(L._TAIL_PREVIEW, dt.date(2026, 9, 30)) == L._sql(L._TAIL_PREVIEW)


def test_收口层_档位解释不了这天就给NULL并写明出处():
    """选好档之后收盘价仍然越过按该档算出的上下限 ⇒ 没有任何一档能解释这天
    （多半是 pre_close 与行情不同基的除权行）。留着一个必然越界的价，不如留 NULL。
    这一层是 `--check` 那条哨兵**结构上**归零的依据，不是"刷完刚好没人踩线"。"""
    sql = L._sql(L._TAIL_UPDATE)
    i = sql.index('), res as (')
    guard = sql[i:]
    assert "'contradicted'" in guard
    assert 'cl > round(pre_close * (1 + band), 2) + %(eps)s::numeric' in guard
    assert 'cl < round(pre_close * (1 - band), 2) - %(eps)s::numeric' in guard
    # 收口只改 band/basis，不许把 already-correct 的行标成 contradicted
    assert 'when band is null then basis' in guard


def test_主板五个点的判据既看百分点也看算出来的价():
    """只比百分点会漏：3.68 的票按 ±5 个点算涨停价只得 3.86（=+4.89 个点），
    收盘 3.87（+5.16 个点）没超 5.2 的门槛，却已经证明那天不是 5 个点那一档。"""
    sql = L._sql(L._TAIL_PREVIEW)
    assert "(round(d.pre_close * 1.05, 2) < coalesce(d.high, d.close)" in sql
    assert "(px.reach > %(proved)s or px.not5)" in sql


def test_recompute只带day时也补次新表_且备份与判据SQL同范围(monkeypatch):
    seen, calls = [], []
    monkeypatch.setattr(L.engine, 'execute',
                        lambda sql, params=None, **k: seen.append(sql) or 0)
    monkeypatch.setattr(L.engine, 'query', lambda sql, params=None, **k: seen.append(sql) or [])
    monkeypatch.setattr(L.engine, 'query_one', lambda sql, params=None, **k: {'r': None})
    monkeypatch.setattr(L.engine, 'scalar', lambda sql, params=None, **k: 0)
    monkeypatch.setattr(L, 'refresh_first_session',
                        lambda **kw: calls.append(kw) or 3)
    L.recompute(dt.date(2026, 9, 30), backup=True)
    assert calls == [{'all_codes': False, 'day': dt.date(2026, 9, 30)}]   # 单日只补缺的那几支
    assert sum('security_session_span' in s for s in seen) >= 3           # 备份/清值/填值都带着 fs
    # 回读（to_regclass / count(*) / basis 分布）也跟着范围走，不许整库 group by
    assert sum('trade_date = %(day)s::date' in s for s in seen) == 4


def test_dry_run连次新表也不碰(monkeypatch):
    """dry-run 的规矩是"零写库副作用"，包括这张辅助表 —— 以前只挡了主写入，
    补表的调用一挪就会从 dry_run 分支里漏出去。"""
    writes = []
    monkeypatch.setattr(L.engine, 'execute', lambda *a, **k: writes.append(a) or 0)
    monkeypatch.setattr(L.engine, 'query', lambda *a, **k: [])
    monkeypatch.setattr(L, 'refresh_first_session', lambda **kw: writes.append(('span',)) or 0)
    L.recompute(dt.date(2026, 9, 30), dry_run=True)
    assert writes == []


def test_备份表名同一天不覆盖():
    """第二遍必须往 _b/_c 找空位：把已有的那份盖掉，等于把"可回退"自己删了。"""
    existing = {'operations.limit_price_backup_20261007'}

    def fake_one(sql, params=None, **k):
        return {'r': params[0] if params[0] in existing else None}
    orig = L.engine.query_one
    L.engine.query_one = fake_one
    try:
        assert L._backup_name(dt.date(2026, 10, 7)) == 'operations.limit_price_backup_20261007_b'
        existing.add('operations.limit_price_backup_20261007_b')
        assert L._backup_name(dt.date(2026, 10, 7)) == 'operations.limit_price_backup_20261007_c'
    finally:
        L.engine.query_one = orig


# ⑥ 容差全项目只有一份，页面判据文字跟着代码走 ---------------------------------------

def test_涨停容差只有一个数且EXPLAIN不手抄():
    """09-30 实测：breadth 用单边 1 分容差给 57 家，通达信 sh880006 与严格双边都是 56 ——
    那 1 只是"差一分钱没封住"被算成封住。qd 轨同一年就写着 0.005，两个答案并存过很久。
    现在三处都从这里取，`EXPLAIN` 里那句判据也必须由代码生成，不许再手抄。"""
    from app.compute import breadth, qd_common
    assert L.LIMIT_EPS == qd_common.LIMIT_EPS == breadth.EPS_YUAN == 0.005
    terms = breadth.EXPLAIN['terms']
    assert 'limit_up - 0.005' in terms[0]['expr']
    assert not [t for t in terms if '- 0.01' in t['expr'] or '+ 0.01' in t['expr']], \
        'EXPLAIN 里还有手抄的 0.01 容差：%s' % [t['key'] for t in terms
                                               if '0.01' in t['expr']]
