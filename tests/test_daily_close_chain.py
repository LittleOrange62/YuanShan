# -*- coding: utf-8 -*-
"""每日收盘补数据链 `ingest/jobs/daily_close_sync.py` 的闸门测试。

只测**判定**：不连数据库、不起通达信、不动鼠标。所有 subprocess 入口被假 `_run` 录下来，
断言的是"什么情况下允许动手、什么情况下必须停手"。

这些门挡的是本项目真栽过的两类错：
① "看着在跑其实一步没执行" —— `e1_fill_all` 的 15 步指向本项目不存在的 `scripts/` 目录；
② "看着好了其实只下一半" —— `MIN_FRESH_RATIO=0.6` + 每市场 3 只锚定票 = 2/3 到位就算就绪。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.tdx_client import locator
from app.tdx_client import readiness as rd
from ingest.jobs import daily_close_sync as dcs

TARGET = dt.date(2026, 9, 24)
#: 默认钉在 17:30：链时点 15:59 之后 91 分钟 —— 已过 `DAILY_CLOSE_GUI_AFTER_MIN`，
#: 所以"该动桌面"是默认情形，转等待的那条另有测试点名把时钟拨回 16:10。
#: 用真时钟不行：同一份断言会在下午四点整突然变绿变红，而"偶尔红"的测试等于没测试。
NOW_DEFAULT = dt.datetime(2026, 9, 24, 17, 30)


class FakeProbe:
    def __init__(self, fresh):
        self.fresh_flag = fresh
        self.market, self.symbol, self.path = 'sh', '600000', 'x'
        self.last_date, self.exists, self.error = TARGET, True, ''

    def fresh(self, _target):
        return self.fresh_flag


class FakeVerdict:
    def __init__(self, ok, kind='个股日线', n_fresh=0, n_total=129):
        self.ok, self.kind, self.note = ok, kind, ''
        self.target = TARGET
        self.stale_days = 0 if ok else 1
        self.probes = ([FakeProbe(True)] * n_fresh
                       + [FakeProbe(False)] * (n_total - n_fresh))

    def report(self):
        return '[%s] %s' % (self.kind, '就绪' if self.ok else '未就绪')


class FakeReadiness:
    """四个探针 + 覆盖数都由测试点着。"""

    state = {}
    fresh = (129, 129)

    def __init__(self, vipdoc=None, *, extra_samples=0, seed=None):
        pass

    def db_ready(self, target=None):
        return FakeVerdict(self.state['db'], 'PG 入库')

    def day_ready(self, target=None, *, markets=None):
        return FakeVerdict(self.state['day'], '个股日线', *self.fresh)

    def index_ready(self, target=None):
        return FakeVerdict(self.state['index'], '指数')

    def minute_ready(self, target=None, *, period=1, markets=None):
        return FakeVerdict(self.state['min%d' % period], '%d 分钟线' % period)


ALL_MISSING = {'db': False, 'day': False, 'index': False, 'min1': False, 'min5': False}
VIPDOC_FULL = {'db': False, 'day': True, 'index': True, 'min1': True, 'min5': True}


@pytest.fixture
def env(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    clock = {'at': NOW_DEFAULT}
    lands = {'c1': True, 'tq': False}
    counts: list[str] = []

    def fake_run(argv, *, what, timeout=None):
        # `_run` 现在带硬超时（加固）。假桩必须收得下这个关键字，
        # 否则"没下全绝不进库"这类测试会因为签名不匹配而红 —— 那是测试自己的坏，不是判据的坏。
        calls.append(list(argv))
        assert timeout, '每个子作业都要有上限：%s' % what
        # 入库跑完，库里就该有那天的数 —— 让 ④ 回验能真的过门，
        # 否则测出来的是"回验永远失败"这种假象。
        # TQ 那条腿（c12）**也自己写库**，但它默认判"没拿到"：不然每条老测试都会在
        # ③b 就短路，再也走不到 ③c，"三条腿的顺序"这件事就没人验得到。
        if ('ingest.jobs.c1_day' in argv and lands['c1']) \
                or ('ingest.jobs.c12_tq_day' in argv and lands['tq']):
            FakeReadiness.state = {**FakeReadiness.state, 'db': True}
        return 0

    monkeypatch.setattr(dcs, '_run', fake_run)
    monkeypatch.setattr(dcs, '_now', lambda: clock['at'])
    monkeypatch.setattr(dcs, '_client_running', lambda: True)
    monkeypatch.setattr(rd, 'Readiness', FakeReadiness)
    monkeypatch.setattr(rd, 'expected_trade_date', lambda now=None: TARGET)
    monkeypatch.setattr(locator, 'sync_running', lambda: (False, '没有 TPyth* 进程'))
    trade = {'day': True}
    monkeypatch.setattr(dcs.sched_repo, 'is_trade_day', lambda _d: trade['day'])
    monkeypatch.setattr(dcs.time, 'sleep', lambda _s: None)   # 等待不许真睡
    # 「没看到同步进程」那一档的预算默认 120 秒：`sleep` 被换成空操作后它会原地刷
    # 两分钟墙钟（2026-09-29 实测这条测试跑了 120.5s）。测试要的是"等满就出局"，
    # 不是"真等 120 秒"，所以把预算也钉成 0。
    monkeypatch.setattr(dcs, 'RESTART_ABSENT_WAIT', 0)
    # **条数门（2026-10-06）也要打桩**：`vipdoc_counts` 数的是真 vipdoc 整目录
    # （一万来个文件、冷态几十秒），测试里既不现实也不确定。让"到位条数"跟着
    # `FakeReadiness.fresh` 走，一份假数据同时喂日期判据和条数判据。
    from duanxian import completeness as comp
    monkeypatch.setattr(comp, 'data_path', lambda *p: str(tmp_path / 'completeness'))
    # 真下线是量级数（8,500 个文件），测试的假条数只有 129 这一档 → 把下线压到 100，
    # 这样"四成/六成/全齐"三种情形仍然各自落在判据的两侧。
    for kind in ('vipdoc_day', 'vipdoc_lc1', 'vipdoc_lc5'):
        monkeypatch.setitem(comp.KINDS[kind], 'floor', 100)

    def fake_counts(day, **kw):
        counts.append(str(day))
        return {'vipdoc_day': FakeReadiness.fresh[0],
                'vipdoc_lc1': FakeReadiness.fresh[0],
                'vipdoc_lc5': FakeReadiness.fresh[0]}

    monkeypatch.setattr(comp, 'vipdoc_counts', fake_counts)
    # ③b 的前置是"客户端在跑且已登录"（用户 10-06 点头让系统自己拉起）。
    # 探针必须打桩：`watchman.probe()` 枚举的是**真桌面**上的窗口，
    # 测试机上通达信在不在是不确定的，而默认情形要的是"已经登录着"（老测试才不用改写）。
    # 队列里给多个状态时按次消费（拉起前一个、复验一个），只给一个就一直用它。
    from duanxian import gui_trail
    monkeypatch.setattr(gui_trail, 'data_path', lambda *p: str(tmp_path / 'trail'))
    probes = [{'running': True, 'login_box': False, 'pid': 4321}]

    def fake_probe():
        st = probes[0] if len(probes) == 1 else probes.pop(0)
        return dict(st)

    monkeypatch.setattr(dcs, '_tdx_probe', fake_probe)
    FakeReadiness.state = dict(ALL_MISSING)
    FakeReadiness.fresh = (129, 129)
    calls.clear()
    return type('Env', (), {'calls': calls, 'trade': trade, 'clock': clock,
                             'lands': lands, 'counts': counts,
                            'probes': probes, 'trail': gui_trail})()


def _main(argv):
    import sys
    saved = sys.argv
    sys.argv = ['daily_close_sync', *argv]
    try:
        return dcs.main()
    finally:
        sys.argv = saved


def joined(calls):
    return ' || '.join(' '.join(c) for c in calls)


# ---------------------------------------------------------------- ① 时机

def test_非交易日判不过(env):
    env.trade['day'] = False
    ok, why = dcs.step_timing(TARGET, dt.datetime(2026, 9, 26, 16, 0))
    assert not ok and '不是交易日' in why


def test_交易日当天未过收盘线判不过(env):
    """14:00 跑当天的数 = 一定等不到，必须退回上一交易日。"""
    ok, why = dcs.step_timing(dt.date(2026, 9, 28), dt.datetime(2026, 9, 28, 14, 0))
    assert not ok and '收盘线' in why


def test_非交易日整链一步都不执行(env):
    env.trade['day'] = False
    assert _main(['--date', TARGET.isoformat()]) == 2
    assert env.calls == [], '节假日也起通达信空跑就是扰民'


# ---------------------------------------------------------------- ② 幂等

def test_库里已有就不取数不入库但仍存成员快照(env):
    """钉死我犯过的真错：② 判"已有"时原先直接 return，
    链尾的**成员快照**在这种日子里永远不跑 —— 快照少一天就永久少一天。
    （档位不在这条链上：它要的热度表晚些时候才由段2 写，见 `step_signal`。）"""
    FakeReadiness.state = {**ALL_MISSING, 'db': True}
    code = _main(['--date', TARGET.isoformat()])
    assert code == 0
    j = joined(env.calls)
    assert 'c1_day' not in j, '库里已有还入库 = 每次运行都重写一遍'
    assert 'p0_tdx_predownload' not in j and 'run_agent' not in j
    assert '--snapshot-only' in j, '成员快照被"已有"短路吃掉'
    sts = [c for c in env.calls if 'app.compute.sector_trend_signal' in c]
    assert len(sts) == 1 and '--snapshot-only' in sts[0] \
        and '--date' not in sts[0], '档位不该在这条链上跑（那时热度表还是空的，每天必然 exit 1）'


def test_缺数据时才取数入库(env):
    FakeReadiness.state = dict(VIPDOC_FULL)
    assert _main(['--date', TARGET.isoformat()]) == 0
    j = joined(env.calls)
    assert '--snapshot-only' in j and 'c1_day' in j and 'c16_minute' in j
    # vipdoc 已齐 → 不该再去点窗口
    assert 'p0_tdx_predownload' not in j, '本地已有还去下载 = 白动鼠标'


# ---------------------------------------------------------------- ③ 下全门

def test_同步进程还在跑判未下全(env, monkeypatch):
    monkeypatch.setattr(locator, 'sync_running', lambda: (True, 'TPython.exe(4321)'))
    ok, why = dcs.step_complete(FakeReadiness(), TARGET)
    assert not ok and '未下全' in why


def test_条数还在涨判未下全(env):
    ok, why = dcs.step_complete(FakeReadiness(), TARGET, prev=100)
    assert not ok and '还在长' in why


def test_条数没过基线判未下全(env):
    """进程退了但只下了四成 —— 这一条是"2/3 锚定票"老判据会放行的情形。"""
    FakeReadiness.fresh = (52, 129)
    ok, why = dcs.step_complete(FakeReadiness(), TARGET)
    assert not ok and '没下全' in why


def test_条数与进程都过关才放行(env):
    FakeReadiness.fresh = (127, 129)
    ok, why = dcs.step_complete(FakeReadiness(), TARGET, prev=127)
    assert ok, why


def test_没下全时绝不进库(env, monkeypatch):
    """整链层面锁死：日期到了但条数不过门 → 不许 c1_day，也不许算档位。"""
    FakeReadiness.state = dict(VIPDOC_FULL)
    FakeReadiness.fresh = (60, 129)
    monkeypatch.setattr(dcs, 'RESTART_WAIT_SEC', 0)
    monkeypatch.setattr(dcs, 'DOWNLOAD_WAIT_SEC', 0)
    code = _main(['--date', TARGET.isoformat()])
    j = joined(env.calls)
    assert code == 3, '自动手段用尽要转人工，不能闷头入库'
    assert 'c1_day' not in j and 'sector_trend_signal' not in j


def test_下全门数的是整目录条数而不是抽样(env):
    """2026-10-06 用户把抽样比例否了：**抽到的一排恰好全停牌就会误判**，
    而整目录条数会跟着市场自己长（新股只增不减）。这条测试钉的就是"门走的是哪份实现"。"""
    FakeReadiness.fresh = (129, 129)
    del env.counts[:]
    ok, why = dcs.step_complete(FakeReadiness(), TARGET)
    assert ok, why
    assert env.counts == [str(TARGET)]
    assert '整目录 129 个' in why


# ---------------------------------------------------------------- ③ 三条腿的顺序

def test_TQ那条腿排在点窗口之前(env, monkeypatch):
    """**顺序本身就是这次修的东西**：vipdoc 没落盘 ≠ 客户端没有这一天的数。
    2026-09-29 16:43 实测 `[个股日线] 未就绪（落后 1）` 的同时，TQ 的 dry-run 当场
    取到当天 5,559 只 —— 而老链条里没有 ③b 这一步，只有"重启客户端 + 点窗口"。"""
    monkeypatch.setattr(dcs, 'RESTART_WAIT_SEC', 0)
    monkeypatch.setattr(dcs, 'DOWNLOAD_WAIT_SEC', 0)
    assert _main(['--date', TARGET.isoformat()]) == 3    # 两条都没拿到 → ③c 也试过 → 转人工
    j = joined(env.calls)
    names = [' '.join(c) for c in env.calls]
    assert 'ingest.jobs.c12_tq_day' in j, '无 GUI 的那条腿根本没走'
    assert 'p0_tdx_predownload' in j
    assert (names.index(next(n for n in names if 'c12_tq_day' in n))
            < names.index(next(n for n in names if 'p0_tdx_predownload' in n))), \
        '先动鼠标再问客户端 = 把最贵的一条排在最前面'


def test_客户端没开着会先自动拉起再问TQ(env, monkeypatch):
    """**用户 10-06 20:5x 点头的那件事**：「这个必须要你自动执行啊！太需要你去动界面了」。

    10-06 实测客户端关着的那天 **TQ 是零**（`tq_bridge.available()` 的判据就是 `TdxW.exe`
    在不在），于是最便宜的那条腿直接失效，链一路走到要点鼠标的 ③c。所以 ③b 之前先补前置。
    """
    monkeypatch.setattr(dcs, 'RESTART_WAIT_SEC', 0)
    monkeypatch.setattr(dcs, 'DOWNLOAD_WAIT_SEC', 0)
    env.probes[:] = [{'running': False}, {'running': True, 'login_box': False, 'pid': 777}]
    env.lands['tq'] = True
    assert _main(['--date', TARGET.isoformat()]) == 0
    names = [' '.join(c) for c in env.calls]
    launch = next(n for n in names if 'run_agent' in n and 's1_launch' in n)
    assert 's2_login' in launch and 's3_popup' in launch
    assert names.index(launch) < names.index(next(n for n in names if 'c12_tq_day' in n)), \
        '先问 TQ 再拉起 = 客户端没登录时那次问一定是空手回来'
    assert 'c12_tq_day' in joined(env.calls)


def test_已经在跑就不重复拉起(env):
    """前置的判据是探针，不是"每次都起一遍"：已经在跑还起一次就是抢用户的窗口。"""
    env.lands['tq'] = True          # 让 ③b 拿到，免得 ③c 那条腿本来就要起 run_agent
    assert _main(['--date', TARGET.isoformat()]) == 0
    assert 'run_agent' not in joined(env.calls)


def test_停在登录框也算没准备好要补登录(env, monkeypatch):
    monkeypatch.setattr(dcs, 'RESTART_WAIT_SEC', 0)
    monkeypatch.setattr(dcs, 'DOWNLOAD_WAIT_SEC', 0)
    env.probes[:] = [{'running': True, 'login_box': True},
                     {'running': True, 'login_box': False, 'pid': 1}]
    ok, why = dcs.step_launch(TARGET, check_only=False, no_gui=False)
    assert ok, why
    assert '停在登录框' in why or '登录框' in why or '已自动拉起' in why


def test_拉起后还是没登录就别去白问TQ(env, monkeypatch):
    """`step_tq` 的前置不过 → 直接不调 c12：TQ 连不上是必然，白等一轮超时。"""
    monkeypatch.setattr(dcs, 'RESTART_WAIT_SEC', 0)
    monkeypatch.setattr(dcs, 'DOWNLOAD_WAIT_SEC', 0)
    env.probes[:] = [{'running': False}]
    ok, why = dcs.step_tq(TARGET, check_only=False)
    assert not ok and '客户端起不来' in why
    assert 'ingest.jobs.c12_tq_day' not in joined(env.calls)


def test_auto_launch关掉就只记清单不碰桌面(env, monkeypatch):
    from duanxian import jobs as jstore
    monkeypatch.setattr(jstore, 'settings',
                        lambda key, defaults=None: ({'auto_launch': False}, []))
    env.probes[:] = [{'running': False}]
    ok, why = dcs.step_launch(TARGET, check_only=False, no_gui=False)
    assert not ok and 'auto_launch=false' in why
    assert env.calls == [], '关了开关还起 run_agent = 开关是假的'
    pend = env.trail.pending()
    assert pend and pend[0]['status'] == 'skipped'
    assert 's1_launch' in pend[0]['step'] and '双击桌面通达信' in pend[0]['manual']


def test_每一步界面动作都进待人工清单并带手动做法(env, monkeypatch):
    """这是用户那句"万一我当时也在操作电脑……也可以我主动的去找出来，哪些没执行的我手动执行下"
    唯一的落点：清单里必须有**没做成的那几条**，且每条写着人怎么补。"""
    monkeypatch.setattr(dcs, 'RESTART_WAIT_SEC', 0)
    monkeypatch.setattr(dcs, 'DOWNLOAD_WAIT_SEC', 0)
    env.probes[:] = [{'running': False}]          # 拉起前后都不在跑 → 这次没成
    ok, why = dcs.step_launch(TARGET, check_only=False, no_gui=False)
    assert not ok
    pend = env.trail.pending()
    assert len(pend) == 1 and pend[0]['status'] in ('failed', 'need_human')
    assert pend[0]['target'] == str(TARGET)
    assert 'run_agent --only s2_login' in pend[0]['manual']


def test_no_gui时前置也不动桌面(env, monkeypatch):
    env.probes[:] = [{'running': False}]
    ok, why = dcs.step_launch(TARGET, check_only=False, no_gui=True)
    assert not ok and '--no-gui' in why
    assert env.calls == []
    assert env.trail.pending()[0]['status'] == 'skipped'


def test_TQ拿到就不让c1拿半截vipdoc覆盖但分钟线照试(env):
    """c12 自己写库**并且回算派生列**，所以 c1/c3 再跑一遍等于用"还没下全的 vipdoc"
    去 upsert 覆盖一份已经齐的数。但 TQ 给不了分钟线，`c16` 必须照跑。"""
    env.lands['tq'] = True
    assert _main(['--date', TARGET.isoformat()]) == 0
    j = joined(env.calls)
    assert 'ingest.jobs.c12_tq_day' in j
    assert 'ingest.jobs.c1_day' not in j and 'ingest.jobs.c3_sector' not in j, \
        'TQ 已入库还重跑 c1/c3 = 拿半截文件覆盖齐的数'
    assert 'ingest.jobs.c16_minute' in j, 'TQ 没有分钟线，跳过 c16 就是把缺口一直留着'
    assert 'p0_tdx_predownload' not in j and 'run_agent' not in j, '已经拿到还去动桌面'


def test_时刻还早不动桌面而是退出4(env):
    """15:59 后头几十分钟客户端的自动同步还在写 vipdoc，重启就是打断它
    （09-28 用户看到的「通达信跑到一半死机、入库只有部分股票日线」）。
    这种情形**不是**"要人来"：回 4 让宿主按节奏表再敲一次。"""
    env.clock['at'] = dt.datetime(2026, 9, 24, 16, 10)
    assert _main(['--date', TARGET.isoformat()]) == 4
    j = joined(env.calls)
    assert 'p0_tdx_predownload' not in j and 'run_agent' not in j and 'reset' not in j, \
        '还没到该动桌面的时刻就动 = 打断客户端自己正在跑的同步'
    assert 'ingest.jobs.c12_tq_day' in j, '等之前至少要把便宜的 TQ 那条走完'
    assert 'ingest.jobs.c1_day' not in j, '没拿到数还入库 = 写 0 行还自称成功'


def test_no_gui即使时刻早就转人工而不是空等(env):
    """`--no-gui` 是人明确不让动桌面：机器侧确实没路了，回 3 说清要人做什么，
    而不是回 4 让宿主空转一整天。"""
    env.clock['at'] = dt.datetime(2026, 9, 24, 16, 10)
    assert _main(['--date', TARGET.isoformat(), '--no-gui']) == 3
    assert 'p0_tdx_predownload' not in joined(env.calls)


def test_人工早上跑允许直接点窗口(env):
    """链外的时刻（`now` 早于时点）没有"正在自动同步"可打断 —— 补历史/白天手动跑要能
    立刻走 ③c，否则只能等到明天。"""
    env.clock['at'] = dt.datetime(2026, 9, 28, 9, 5)
    allow, why = dcs.gui_allowed_now(env.clock['at'], TARGET, no_gui=False)
    assert allow, why
    assert '补历史' in why or '人工' in why


def test_过了阈值就该去点窗口(env):
    env.clock['at'] = dt.datetime(2026, 9, 24, 17, 5)
    assert dcs.gui_allowed_now(env.clock['at'], TARGET, no_gui=False)[0]
    env.clock['at'] = dt.datetime(2026, 9, 24, 16, 5)
    ok, why = dcs.gui_allowed_now(env.clock['at'], TARGET, no_gui=False)
    assert not ok and '下一档' in why       # 没到阈值时要说清什么时候再来


# ---------------------------------------------------------------- 等待行为

def test_等待中一到就走不睡满(env, monkeypatch):
    ticks = {'n': 0}
    base = dict(VIPDOC_FULL)

    class Flippy(FakeReadiness):
        def day_ready(self, target=None, *, markets=None):
            ticks['n'] += 1
            ready = ticks['n'] >= 3          # 第三轮才到齐
            self.state = {**base, 'day': ready, 'index': ready}
            return FakeVerdict(ready, '个股日线', *self.fresh)

        def db_ready(self, target=None):
            return FakeVerdict(False, 'PG 入库')

        def index_ready(self, target=None):
            return FakeVerdict(True, '指数')

        def minute_ready(self, target=None, *, period=1, markets=None):
            return FakeVerdict(True, '分钟线')

    monkeypatch.setattr(rd, 'Readiness', Flippy)
    ok, why, _n = dcs._wait_settled(Flippy(), TARGET, 'T', budget=600)
    assert ok, why
    assert ticks['n'] < 10, '一到就该走，不是把 10 分钟睡满'


def test_等满预算就报未过门(env, monkeypatch):
    monkeypatch.setattr(dcs, 'RESTART_POLL_SEC', 60)
    ok, why, _n = dcs._wait_settled(FakeReadiness(), TARGET, 'T', budget=1)
    assert not ok and '等满' in why


# ---------------------------------------------------------------- 模式与入口形式

def test_check模式零副作用(env):
    """`--check` 是给人夜里敢跑的凭据：不起窗口、不入库、不写 PG。"""
    assert _main(['--date', TARGET.isoformat(), '--check']) == 0
    assert env.calls == [], '--check 不该执行任何作业：' + joined(env.calls)


def test_no_gui不去点窗口也不许把没下载说成成功(env):
    """本地缺这一天而无桌面模式拿不到它 → 停在 3 转人工。

    2026-09-27 实测抓到旧写法：`--no-gui` 分支无条件回 True，于是链子把
    "没下载"打印成"[③ 下载] 成"，接着去入库、写 0 行、还得再靠回验拦。
    """
    assert _main(['--date', TARGET.isoformat(), '--no-gui']) == 3
    j = joined(env.calls)
    assert 'p0_tdx_predownload' not in j, '--no-gui 还去下载 = 动了用户桌面'
    assert 'run_agent' not in j
    assert 'ingest.jobs.c1_day' not in j, '本地没这一天还去入库 = 写 0 行还自称成功'


def test_作业一律模块名不写脚本路径(env):
    """`e1_fill_all` 的教训：指向不存在的 `scripts/*.py` 时每一步 `can't open file`，
    看着在跑其实一行没执行。"""
    FakeReadiness.state = dict(VIPDOC_FULL)
    _main(['--date', TARGET.isoformat()])
    assert env.calls, '至少要真的调了作业'
    for c in env.calls:
        assert c[0] == '-m', '作业入口必须是 -m：' + ' '.join(c)
        assert not any('.py' in a and 'scripts' in a for a in c)


def test_分钟线入库挂在链上(env):
    """历史上 s9 只跑 c1/c3/b6：第二 tab 下回来的分钟线**没人入库**，7 分钟白下。"""
    FakeReadiness.state = dict(VIPDOC_FULL)
    _main(['--date', TARGET.isoformat()])
    assert 'c16_minute' in joined(env.calls)
