# -*- coding: utf-8 -*-
"""两档重试的语义锁（用户 2026-09-27 裁定）：

- **骨干步骤失败 → 就地立刻重试**（等几秒再来一次，本机 push2/新浪是"按频率断连、
  冷却几秒恢复"，就地等比等整条链跑完便宜）；
- **非骨干步骤失败 → 不在链子里重试，等全部步骤跑完再统一补一轮**
  （它们是龙虎榜/两融/资金流这类网络源，就地重试会把日线主链拖住）；
- "按设计停下"的退出码**不重试**：`daily` 的 2 是 vipdoc 没覆盖目标日，
  要人去点盘后下载，重试一百次也不会自己长出数据。

只测判定：`run_script` 换成脚本化的退出码序列，不连库、不起子进程、不真睡。
"""
from __future__ import annotations

import datetime as dt
import pathlib

import pytest

from ingest.jobs import e1_fill_all as e1

TARGET = dt.date(2026, 9, 24)


@pytest.fixture
def env(monkeypatch):
    calls: list[str] = []
    sleeps: list[float] = []
    budgets: dict[str, int | None] = {}   # 每一步实际拿到的超时
    # 每个模块给一串退出码：第一次跑取第 1 个，就地重试取第 2 个，链尾补跑取下一个
    script: dict[str, list[int]] = {}

    def fake_run_script(mod, *extra, timeout=None):
        # `timeout` 现在真的会传进来（每一步的硬超时，见 `STEP_TIMEOUTS`）。
        # ⚠️ 这里**不能**断言 `timeout == e1.step_timeout(mod)`：调用点按**步名**取预算
        # （`indicators`），而这里拿到的是**模块名**（`ingest.jobs.d1_indicators`），
        # 两者不同名 —— 那会是一条永远红的假断言。留到 budgets 里由具体测试去判。
        calls.append(mod)
        budgets[mod] = timeout
        seq = script.get(mod, [0])
        return (seq.pop(0) if len(seq) > 1 else seq[0]), '末尾输出'

    monkeypatch.setattr(e1, 'run_script', fake_run_script)
    monkeypatch.setattr(e1.time, 'sleep', lambda s: sleeps.append(s))
    monkeypatch.setattr(e1, 'sync_calendar', lambda: (TARGET, TARGET, '测试'))
    monkeypatch.setattr(e1.engine, 'scalar', lambda *a, **k: TARGET)
    monkeypatch.setattr(e1, 'verify', lambda _t: [])
    monkeypatch.setattr(e1.tradeday, 'missing_days', lambda _t: [TARGET])
    monkeypatch.setattr(e1.tradeday, 'back_window', lambda _t, _b: TARGET)
    monkeypatch.setattr(e1.tradeday, 'trade_days_between', lambda a, b: 1)
    monkeypatch.setattr(e1.sec_repo, 'latest_trade_date', lambda: TARGET)
    return type('Env', (), {'calls': calls, 'sleeps': sleeps,
                            'script': script, 'budgets': budgets})()


def _main(env, extra=()):
    import sys
    saved = sys.argv
    sys.argv = ['e1_fill_all', '--date', TARGET.isoformat(), *extra]
    try:
        return e1.main()
    finally:
        sys.argv = saved


def _positions(calls, mod):
    return [i for i, m in enumerate(calls) if m == mod]


def test_骨干失败就地重试并在本轮追平(env):
    daily = 'ingest.jobs.c10_catchup'
    env.script[daily] = [1, 0]              # 第一次红，重试就成
    assert _main(env) == 0
    pos = _positions(env.calls, daily)
    assert len(pos) == 2
    # 就地重试：紧跟在第一次之后，中间没插进别的步骤
    assert pos[1] == pos[0] + 1
    assert env.sleeps == [e1.RETRY_BACKOFF[0]], '重试前必须退避，不能立刻再打一次'


def test_骨干重试次数有上限且最终失败按骨干计(env):
    daily = 'ingest.jobs.c10_catchup'
    env.script[daily] = [1, 1, 1]           # 首跑 + CORE_RETRIES 次
    assert _main(env) == 1                  # 骨干失败 → 1
    assert len(_positions(env.calls, daily)) == 1 + e1.CORE_RETRIES
    # 下游依赖日线的三步必须被跳过，不能拿旧数据算出"看着成功"的读数
    for mod in ('ingest.jobs.d1_indicators', 'ingest.jobs.d2_scores',
                'ingest.jobs.d3_sector_trend'):
        assert mod not in env.calls, f'{mod} 在日线失败后仍被调用'


def test_非骨干不在链子里重试而是链尾补一轮(env):
    lhb = 'ingest.jobs.c6_lhb'
    env.script[lhb] = [1, 0]                # 首跑红，链尾补跑成
    assert _main(env) == 0
    pos = _positions(env.calls, lhb)
    assert len(pos) == 2
    # 第二次调用发生在**所有步骤之后**（链尾），不是紧跟第一次
    assert pos[1] > pos[0] + 1
    assert env.calls.index('ingest.jobs.e2_quality_audit') < pos[1] or \
        env.calls.index('app.compute.sector_trend_signal') < pos[1], \
        '非骨干补跑必须排在整条链之后'
    assert env.sleeps == [], '非骨干不该有就地退避'


def test_按设计停下的退出码不重试(env):
    daily = 'ingest.jobs.c10_catchup'
    env.script[daily] = [2]                 # vipdoc 没覆盖目标日，且没给 --allow-web
    assert _main(env) == 2
    assert len(_positions(env.calls, daily)) == 1, '按设计停下还重试 = 白等'
    assert env.sleeps == []


def test_非骨干补跑仍失败则退出码停在辅助档(env):
    lhb = 'ingest.jobs.c6_lhb'
    env.script[lhb] = [1, 1]
    assert _main(env) == 3                  # 只有非骨干失败 → 3
    assert len(_positions(env.calls, lhb)) == 2


def test_qd轨三步在链子里且天梯排在情绪周期之前():
    """2026-09-28 实测：那天日线/因子/评分都补上了，首页「情绪周期」「连板天梯」仍停在上一场 ——
    因为 `qd1/qd2/qd3` 此前没有任何调度入口，只有人手跑一次才算一次。
    这三步一旦从 STAGES 里掉出去，页面不会报错，只会天天少一场 —— 所以钉在结构上。
    """
    names = [s[0] for s in e1.STAGES]
    for want in ("qd_ladder", "qd_sentiment", "qd_sector"):
        assert want in names, f"{want} 掉出工序表了 —— qd 轨会重新开始天天落后"
    assert names.index("qd_ladder") < names.index("qd_sentiment"), \
        "qd1 读 qd2 写的天梯，顺序反了情绪周期会整批拒写（它自己会说「天梯未跑」）"
    # 排在因子/评分之前：评分那段挂了不该连情绪周期一起丢（与 breadth 同一条理由）
    assert names.index("qd_sentiment") < names.index("indicators")


def test_qd轨三步的入口指向真的作业而不是同名壳():
    """步骤名 → 真模块的那层映射写在 `main()` 内部（不是一函数，没法直接调），
    所以这里按源码字面钉一遍。指错模块不会报错，只会算出一张空表 —— 正是最难发现的一类。"""
    src = pathlib.Path(e1.__file__).read_text(encoding="utf-8")
    for name, mod in (("qd_ladder", "ingest.jobs.qd2_ladder"),
                      ("qd_sentiment", "ingest.jobs.qd1_sentiment"),
                      ("qd_sector", "ingest.jobs.qd3_sector")):
        assert f"'{name}': ('{mod}'" in src, f"{name} 的入口不是 {mod}（或这一步的映射被删了）"


def test_给因子步的超时容得下实测耗时(env):
    """加固本身不该变成新的故障源：调用点传进来的必须是 `STEP_TIMEOUTS` 里那一步的值，
    而不是默认值 —— 2026-09-30 实跑 `d1_indicators --recent 5` 要 1002 秒，
    拿默认 1800 秒凑合还行，但 `--recent 30`（`MAX_SPAN` 允许的上限）就会被掐死。"""
    e1.run_script = None            # 只是让读表成立，下面不看它
    assert e1.step_timeout('indicators') >= 5400
    assert e1.step_timeout('tdxdl') > e1.step_timeout('daily')   # 下载那一步自己就要等分钟线
    assert e1.step_timeout('unknown_step') == e1.STEP_TIMEOUT_SEC


def test_非交易日拒写的两条即时截面口不算失败也不补跑(env, capsys):
    """c21(新浪个股主力排行) rc=3 / c22(同花顺行业资金流) rc=2 都是**它们自己的口径在起作用**：
    报文里没有日期，休市时抓到的截面无法证明归属哪一场 ⇒ 拒写。
    它们的宿主循环把这种情况记成"非交易日跳过（ok）"，链这边若算失败，
    同一件事就有两种结论（2026-10-07 休市实测：链尾写"3 步失败"，面板上却是绿的）。
    而且不能进链尾补跑 —— 休市日重试一百次也还是拒写。"""
    c21, c22 = 'ingest.jobs.c21_fundflow_sina', 'ingest.jobs.c22_sector_flow'
    env.script[c21] = [3]
    env.script[c22] = [2]
    assert _main(env) == 0, '按口径拒写被算成了失败，PARTIAL 会把真故障一起埋掉'
    assert len(_positions(env.calls, c21)) == 1 and len(_positions(env.calls, c22)) == 1, \
        '拒写不该进链尾补跑'
    out = capsys.readouterr().out
    assert out.count('按口径拒写') == 2, out[-400:]
    assert '仍然失败' not in out


def test_真网络故障照样得算失败(env):
    """上一条开了白名单，这条守住白名单的边界：c11 大盘资金流的 rc=1 是真连不上上游，
    不许被"按口径"那句话一起吞掉。"""
    c11 = 'ingest.jobs.c11_fundflow'
    env.script[c11] = [1, 1]                # 首跑 + 链尾补跑都红
    assert _main(env) == 3
    assert len(_positions(env.calls, c11)) == 2


P0 = 'ingest.jobs.p0_tdx_predownload'
C16 = 'ingest.jobs.c16_minute'
SPIKE = 'app.compute.intraday_spike'


def test_下载成功当轮就补一次分钟线入库且排在吃分钟线的步之前(env):
    """分钟线的**下载**在加工段第一步、**入库**在获取段 —— 两段各自跑，
    所以"文件刚落地"这件事加工段自己必须收口（手动只跑 e1 时更没别人会做）。
    这条钉住顺序：c16 必须紧跟 `tdxdl` 成功，且**早于** `spike`，
    否则 `spike` 只会拿到空表并记 degraded（"速度那一刀判不了"）。"""
    assert _main(env) == 0
    pos_c16 = _positions(env.calls, C16)
    assert len(pos_c16) == 1, '每晚都会走这一路，只点一次就够（幂等，全部已入库时实测 6.5 秒）'
    assert pos_c16[0] == _positions(env.calls, P0)[0] + 1, \
        'c16 要紧跟在下载成功之后，中间不许插进别的步骤'
    assert pos_c16[0] < _positions(env.calls, SPIKE)[0], \
        '入库必须早于吃分钟线的步骤，否则那一屏当天又是 degraded'
    assert env.budgets[C16] == e1.MINUTE_INGEST_TIMEOUT_SEC, \
        '这一步的预算要显式给（与获取段给 c16 的同一个数），不能混进整步的默认预算'


def test_链尾才把下载跑成时补入库并让spike重跑拿到数(env, capsys):
    """2026-10-15 的真实形状：获取段与加工段首轮的下载都卡在"打开盘后数据下载"
    对话框（要人在桌面前），c16 扫空、`spike` 记 degraded；链尾补跑下载才成功。
    那时必须**就地补一次入库**，且排在同轮 `spike` 重跑之前，
    否则当天分钟线要等明天的链才进库（实测差了三小时：入库 17:44、落盘 18:52）。"""
    env.script[P0] = [1, 0]                 # 首轮红 → 链尾补跑成
    env.script[SPIKE] = [3, 0]              # 首轮"分钟线缺失"降级 → 链尾重跑成
    assert _main(env) == 0
    assert len(_positions(env.calls, C16)) == 1, '首轮下载没成不该点入库；链尾补跑成才点一次'
    assert _positions(env.calls, C16)[0] < _positions(env.calls, SPIKE)[1], \
        '入库要点在 spike 第二次之前，否则重跑读到的还是同一张空表'
    out = capsys.readouterr().out
    assert '分钟线入库已就地补跑（链尾）' in out, out[-400:]
