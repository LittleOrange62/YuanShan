"""条数完整性（`duanxian/completeness.py`）的回归 —— 用户定的那条硬规则。

守的几件事（2026-10-07 用户把"≥ 前第 2 场带百分比容差"改成"**与前一天比偏离值 ≤ N 条**"，
理由是原方法没考虑退市/停牌，N 默认 30 且要在系统配置里改得动）：
1. 判据是**条数 ≥ 基线条数 − 偏离值（条）**，不是写死的占比；
2. **半截那天不许进基线池** —— 否则第二天跟它比就"双双通过"，规则自己废掉；
   也正因为这一条，基线取"最近一个判为全的场次"就够了，不必再跳过一场；
3. 没有历史时按**绝对下线**判，并且要说清"这是按量级判的"；
4. 数没查出来是"查不到"，**不许伪装成全、也不许伪装成半截**；
5. 偏离值**分钟类比日线宽一档**（实测停牌那天能少 107~143 个文件），
   两个数都从作业配置读、能改、`0` 是合法值（= 完全不打折）。

测试里的数字除特别说明外都取自本机实测（2026-09-21..09-30 各场条数，
以及 10-07 逐日量的相邻差），所以这些用例同时也是那份实测的存档。
"""

from __future__ import annotations

import datetime as dt
import struct

import pytest

from duanxian import completeness as C
from duanxian import paths


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """登记表必须落在测试自己的目录：否则会用真 `~\\.duanxian-agents` 里的历史判，
    绿灯和代码无关，还会把真表写脏。

    `ASTOCK_DATA_HOME` 这一句是 2026-10-07 补的：偏离值改由 `jobs.load_config()` 读
    （为了说清"这个数是默认还是有人在系统配置里填的"），而 `jobs` 在 import 时就把
    `data_path` 绑成了自己那份引用 —— 只 patch 上面两行**管不到它**，
    于是判据测试会去读真机那份 config.json：真配置一改偏离值，绿灯就跟着变绿变红。"""
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(paths, "data_path", lambda *p: str(tmp_path.joinpath(*p)))
    monkeypatch.setattr(C, "data_path", lambda *p: str(tmp_path.joinpath(*p)))
    return tmp_path


def _rec(kind, day, n, **kw):
    C.record(kind, day, n, **kw)


# ---- 规则本身 --------------------------------------------------------------

def test_条数等于基线算全_新股只会让数往上走():
    _rec('stock_daily', '2026-09-24', 5557)
    _rec('stock_daily', '2026-09-28', 5557)
    v = C.judge('stock_daily', '2026-09-29', 5557, source='test')
    assert v['complete'] is True
    assert v['baseline'] == 5557 and v['baseline_date'] == '2026-09-28'
    assert v['sessions_back'] == 1                      # 2026-10-07 起基线就是"前一天"
    assert '隔 1 场' in v['why'] and '偏离值' in v['why']


def test_基线取最近一个判为全的场次():
    """跳过一场这件事由"半截天不落表"负责，不必再跳第 2 场（用户 10-07 改的）。"""
    _rec('stock_daily', '2026-09-22', 5554)
    _rec('stock_daily', '2026-09-23', 5556)
    b = C.baseline('stock_daily', '2026-09-24')
    assert b == {'basis': 'baseline', 'count': 5556, 'date': '2026-09-23',
                 'sessions_back': 1}


def test_半截那天绝不进基线池():
    """09-23 分钟线只有 351 只（实测）：判为不全之后**不许被登记**，
    否则第二天 351 就成了基线，连 200 只都算全。"""
    _rec('stock_minute_1m', '2026-09-22', 9527)
    _rec('stock_minute_1m', '2026-09-24', 9388)
    v = C.judge('stock_minute_1m', '2026-09-23', 351, source='test')
    assert v['complete'] is False
    assert '351' in v['why'] and '9,527' in v['why']
    assert '2026-09-23' not in C.history('stock_minute_1m')     # 没进表
    # 于是 09-25 的基线是 09-24 那场（池里最新），而不是被 351 拉低
    assert C.baseline('stock_minute_1m', '2026-09-25') == {
        'basis': 'baseline', 'count': 9388, 'date': '2026-09-24', 'sessions_back': 1}


def test_出事那几天基线隔了几场必须写在结论里():
    """连续两天事故 → 池里最新一场已是三天前。说"昨天"就是假话，`sessions_back` 才是实情。"""
    _rec('stock_daily', '2026-09-22', 5554)
    v = C.judge('stock_daily', '2026-09-25', 5556, source='test')
    assert v['complete'] is True
    assert '隔 1 场' in v['why']                       # 09-22 之后一场都没登记过
    assert v['baseline_date'] == '2026-09-22'


def test_头几次没有历史时按绝对下线判并说明():
    v = C.judge('vipdoc_day', '2026-09-30', 9490, source='test')
    assert v['complete'] is True and v['baseline_basis'] == 'floor'
    assert '绝对下线' in v['why'] and '还没有可比基线' in v['why']
    # 过了下线的那一场就此进池，下一场改跟它比（这是规则该有的行为，不是漏判）
    nxt = C.judge('vipdoc_day', '2026-10-08', 4000, source='test')
    assert nxt['complete'] is False and nxt['sessions_back'] == 1
    # 完全没历史的那个口径，量级不够时同样判不全
    low = C.judge('vipdoc_lc1', '2026-09-30', 4000, source='test')
    assert low['complete'] is False and '绝对下线' in low['why']
    assert '2026-09-30' not in C.history('vipdoc_lc1')


def test_偏离值是条数且分钟类比日线宽一档():
    """2026-10-07 逐日量的相邻差：`.day` 最大 −11、分钟线最大 −107（PG 那边 −143）。
    所以 30 给日线类刚好，给分钟类会天天误报 —— 两个数分开，且都在配置里改得动。"""
    assert C.KINDS['vipdoc_day']['deviation'] == 30
    assert C.KINDS['stock_minute_1m']['deviation'] == 200
    assert C.KINDS['vipdoc_lc1']['deviation'] == 200
    _rec('stock_minute_1m', '2026-09-22', 9531)
    # `record_it=False`：这两次是**边界探针**，不是真的判过一场。
    # 记进表的话第二次的基线就变成第一次那一场，测的就不是"和 9531 差多少"了。
    assert C.judge('stock_minute_1m', '2026-09-24', 9388,     # −143，真发生过
                   record_it=False)['complete'] is True
    assert C.judge('stock_minute_1m', '2026-09-28', 9200,     # −331，超偏离值
                   record_it=False)['complete'] is False
    _rec('stock_daily', '2026-09-22', 5557)
    assert C.judge('stock_daily', '2026-09-23', 5527, record_it=False)['complete'] is True   # −30 算全
    assert C.judge('stock_daily', '2026-09-24', 5526, record_it=False)['complete'] is False  # −31 不算


def test_偏离值改得动且_0_是合法值(monkeypatch):
    """用户原话："这个偏离值可以在系统配置中手动设置和修改"。
    `0` 必须收（= 完全不打折），否则他最想试的那一档反而填不进去。"""
    from duanxian import jobs

    assert jobs._valid('deviation', 0, 30) is None
    assert jobs._valid('deviation', -1, 30) is not None
    assert jobs._valid('deviation', 999999, 30) is not None, "没有上限就等于可以取消判据"
    assert jobs.valid('deviation_minute', 200) is None

    def fake_settings(name, defaults):
        return dict(defaults, deviation=0, deviation_minute=0), []

    monkeypatch.setattr(jobs, 'settings', fake_settings)
    # `written` 那一路读的是 `load_config()`：不一起桩掉，来源串就会说"没人改过"
    monkeypatch.setattr(jobs, 'load_config', lambda: {"completeness": {"deviation": 0}})
    _rec('stock_daily', '2026-09-22', 5557)
    strict = C.judge('stock_daily', '2026-09-23', 5556, source='test')
    assert strict['deviation'] == 0 and strict['complete'] is False
    assert '偏离值 0 条' in strict['why']
    assert strict['deviation_from'] == '系统配置 completeness.deviation'


def test_偏离值的出处要分得清默认与有人填(monkeypatch):
    """`jobs.settings()` 是按默认补齐后交回来的，只看它就分不清 30 是"用户填的 30"
    还是"压根没填"。这一格存在的意义就是说清阈值从哪来 —— 判错一场时按"系统配置里
    有人写了 30"去查，会白找一个不存在的改动。"""
    from duanxian import jobs

    assert C.deviation_for('stock_daily') == (30, '默认（系统配置里没改过这一项）')
    jobs.set_setting('completeness', via='cli:test', deviation=30)   # 有人显式填了同一个数
    assert C.deviation_for('stock_daily')[1] == '系统配置 completeness.deviation'


def test_偏离值大到不小于基线时结论里要出声(monkeypatch):
    """上限（10 万）只防数量级手滑：日线基线 5,557，填 6,000 就合法地**永久判全**。
    这种"门等于没有"不许静默 —— 必须在 `why` 里当场说破。"""
    from duanxian import jobs

    jobs.set_setting('completeness', via='cli:test', deviation=6000)
    _rec('stock_daily', '2026-09-22', 5557)
    v = C.judge('stock_daily', '2026-09-23', 100, source='test')
    assert v['complete'] is True and v['min_required'] == 0
    assert '等于不再判不全' in v['why']
    # 没到那一步时不许有这句（否则天天喊"门失效"就没人看了）
    jobs.set_setting('completeness', via='cli:test', deviation=30)
    assert '等于不再判不全' not in C.judge('stock_daily', '2026-09-24', 5557,
                                           record_it=False)['why']


def test_查不到数不许伪装成全也不伪装成半截():
    _rec('stock_daily', '2026-09-24', 5557)
    _rec('stock_daily', '2026-09-28', 5557)
    v = C.judge('stock_daily', '2026-09-29', None, source='test')
    assert v['complete'] is False
    assert '没数出来' in v['why']
    assert '2026-09-29' not in C.history('stock_daily')


def test_只登记判为全的那些场():
    _rec('sector_daily', '2026-09-22', 1114)
    _rec('sector_daily', '2026-09-23', 1115)
    C.judge('sector_daily', '2026-09-24', 300, source='test')          # 不全 → 不落表
    assert '2026-09-24' not in C.history('sector_daily')
    C.judge('sector_daily', '2026-09-28', 1114, source='test')         # -0.09% → 全 → 落表
    assert C.history('sector_daily')['2026-09-28'] == 1114


def test_登记表按天覆盖_同一天多次判只留最后一次():
    """下载过程中同一个交易日会被判很多次（阶梯 10 分钟一次），
    表里必须是一天一格，不然基线池会被同一场刷屏。"""
    C.judge('stock_daily', '2026-09-30', 5000, source='test')          # 没过下线，不落表
    _rec('stock_daily', '2026-09-30', 5561, source='test')
    _rec('stock_daily', '2026-09-30', 5562, source='test')
    assert C.history('stock_daily') == {'2026-09-30': 5562}


# ---- 计数：整目录，不是抽样 ------------------------------------------------

def _write_day(path, ymd: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack('<IIIIIfII', ymd, 1, 1, 1, 1, 1.0, 1, 1) * 2)


def _write_lc1(path, didx: int, minute: int = 900):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack('<HHfffffII', didx, minute,
                                 1.0, 1.0, 1.0, 1.0, 1.0, 1, 1) * 2)


def test_整目录只数尾部日期已到那天的文件(monkeypatch, tmp_path):
    import app.config as config

    vip = tmp_path / 'vipdoc'
    _write_day(vip / 'sh' / 'lday' / 'sh600001.day', 20260930)      # 到那天
    _write_day(vip / 'sh' / 'lday' / 'sh600002.day', 20260929)      # 停牌/半截 → 不计
    _write_day(vip / 'sz' / 'lday' / 'sz000001.day', 20261008)      # 更晚也算到
    # 2026-09-30 → (2026-2004)*2048 + 930
    _write_lc1(vip / 'sh' / 'minline' / 'sh600001.lc1', 22 * 2048 + 930)
    _write_lc1(vip / 'sh' / 'minline' / 'sh600002.lc1', 22 * 2048 + 929)
    monkeypatch.setattr(config, 'VIPDOC_DIR', vip)

    got = C.vipdoc_counts(dt.date(2026, 9, 30), kinds=('vipdoc_day', 'vipdoc_lc1'),
                          markets=('sh', 'sz', 'bj'))
    assert got == {'vipdoc_day': 2, 'vipdoc_lc1': 1}


def test_check_vipdoc_把没过的一起报出来(monkeypatch, tmp_path):
    monkeypatch.setattr(C, 'vipdoc_counts',
                        lambda day, **kw: {'vipdoc_day': 9490, 'vipdoc_lc1': 351})
    _rec('vipdoc_day', '2026-09-28', 9490, source='test')
    _rec('vipdoc_lc1', '2026-09-28', 9398, source='test')
    r = C.check_vipdoc('2026-09-30')
    assert r['complete'] is False
    assert len(r['missing']) == 1 and '1 分钟线' in r['missing'][0]
    assert r['verdicts']['vipdoc_day']['complete'] is True


# ---- 基线从哪来：库里补登记，别等两天才开始判 --------------------------------

def test_seed_from_pg_只补没有历史的口径(monkeypatch):
    from duanxian import pg

    _rec('stock_daily', '2026-09-28', 5557, source='test')
    seen = []

    def fetch_df(sql, params=()):
        seen.append(sql)
        import pandas as pd
        return pd.DataFrame([{'d': dt.date(2026, 9, 29), 'n': 5559}])

    monkeypatch.setattr(pg, 'enabled', lambda: True)
    monkeypatch.setattr(pg, 'fetch_df', fetch_df)
    out = C.seed_from_pg(days=6)
    assert '跳过' in out['stock_daily']                             # 已有 → 一个查询都不发
    assert '已登记 1 场' in out['stock_minute_1m']
    assert C.history('stock_minute_1m') == {'2026-09-29': 5559}
    assert len(seen) == len(C.PG_SEED) - 1                      # 跳过的一个查询都不发


def test_summary_没登记过时交出_has_baseline_false():
    s = C.summary()
    row = next(r for r in s['kinds'] if r['kind'] == 'vipdoc_lc5')
    assert row['latest'] is None and row['has_baseline'] is False


def test_summary_每行的键必须一致(monkeypatch):
    """`--summary` 的打印在"有历史"那一支取 `sessions_back`/`deviation`/`deviation_from`，
    而空历史那一支原先**根本没这两个键** —— 真跑一次 CLI 就 KeyError（10-06 实测踩过）。
    所以钉住"两支的键集合一致"，而不是只测有数据的那一支。"""
    _rec('stock_daily', '2026-09-28', 5557, source='test')
    rows = C.summary()['kinds']
    filled = next(r for r in rows if r['kind'] == 'stock_daily')
    empty = next(r for r in rows if r['kind'] == 'stock_snapshot')
    assert filled['latest'] is not None and empty['latest'] is None
    assert set(empty) >= set(filled), set(filled) - set(empty)


def test_summary_交得出偏离值与基线出处():
    _rec('stock_minute_1m', '2026-09-18', 9520, source='test')
    _rec('stock_minute_1m', '2026-09-22', 9527, source='test')
    _rec('stock_minute_1m', '2026-09-24', 9531, source='test')
    row = next(r for r in C.summary()['kinds'] if r['kind'] == 'stock_minute_1m')
    assert row['latest'] == '2026-09-24' and row['count'] == 9531
    assert row['baseline'] == 9527 and row['baseline_date'] == '2026-09-22'
    assert row['sessions_back'] == 1 and row['deviation'] == 200
    assert '默认' in row['deviation_from'] or '系统配置' in row['deviation_from'], \
        "页面要能说出这个偏离值是从哪一层取的（默认 / 有人在系统配置里填的数）"
