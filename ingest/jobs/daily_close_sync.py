# -*- coding: utf-8 -*-
"""
每日收盘补数据 · 唯一入口（计划任务调它，人也只该跑它）。

用户定的四步逻辑，本文件就是那四步，一步不多一步不少：

    ① 判时机   今天是不是交易日、过了收盘线没有
    ② 查库     PG 里目标交易日有没有 → **有就跳过取数与入库**（但计算类照跑，见 ③′）
    ③ 获取     三条腿按**代价从低到高**排，一步拿不到才走下一步（顺序不许调）：
                ③a 本地 vipdoc（0 秒，只读文件）：尾部记录日期到没到那天、**下全了没有**
                ③b TQ 直取（几秒，不碰桌面）：`c12_tq_day` 问已登录的客户端要数并自己写库
                ③c 点窗口（要重启客户端、8~10 分钟、可能点不开）：
                   (i)  客户端在跑但数据没到 → 重启它（关掉→重开→登录→等登录后自动同步）
                   (ii) 等满 10 分钟仍没到 → 才去开「盘后数据下载」下日线
                   (iii) 同一次里切第二个 tab 下 1/5 分钟线
                ③c 另有两道闸（见 `gui_allowed_now`）：`--no-gui` 不碰桌面；链自己那一轮里
                时点后头几十分钟**只等不动桌面** —— 那时客户端的自动同步还在写文件。
                三条腿都没到而③c 只是"时刻还早、不该动桌面" → 退出码 **4（hold）**，不是 3；
                三条腿都没到而③c 是**人明确不让动**（`--no-gui`）→ 仍回 3，因为机器侧确实没路了。
    ③′ 快照    自选成员快照（非骨干，且不受②的"已有"短路影响）。
                板块趋势档位**不在这条链上**：它要的热度表由段2 `e1_fill_all` 晚些时候才写，
                在这儿算每天必然 exit 1 —— 见 `step_signal` 的说明。
    ④ 回验     入库之后再查一次库，**只认 PG 里有没有那天的数**

③ 的"下全了没有"是必要的一道门：`readiness.MIN_FRESH_RATIO` 是 0.6、每市场只有
3 只锚定票，等于"3 只里 2 只新"就算就绪 —— 下到一半会全绿，然后拿半截数据算因子，
错到覆盖体检页上都看不出来。所以这里额外要求：后台同步进程 `TPyth*` 已退、
**整目录条数 ≥ 前第 2 个交易日**（`duanxian/completeness.py`，2026-10-06 换掉的硬规则，
原先这里是"放大抽样后覆盖 ≥90%"，抽到的票恰好都停牌就会误判）、且条数比上一轮不再增长。
**没下全就一直等，绝不进库。**

第 ② 步那条"没有才获取"是用户原话：「你获取数据的时候，肯定是先查一下有没有，
没有你才去获取，获取到之后才写进库里，不是说你每次运行都要写一遍进库里面」。
所以本文件里每个动作前面都有一次判定，判定不过就跳过并说明跳过的理由 ——
重复运行的代价必须是零，否则没人敢让计划任务每天跑。

**判据只认数据里的日期，不认窗口状态栏、不认文件 mtime、不认时间。**
（为什么：下载窗口消失而 `.day` 尾部还是上一个交易日，实测发生过；
只看退出码会把"通达信自己那侧失败但没报错"当成成功。）判据全在
`app/tdx_client/readiness.py`，本文件不自己写判据。

用法：
    python -m ingest.jobs.daily_close_sync --check      # 只判定：今天该不该跑、缺什么、该走哪条腿
    python -m ingest.jobs.daily_close_sync              # 完整四步（③c 会动桌面）
    python -m ingest.jobs.daily_close_sync --no-gui     # 绝不碰桌面：vipdoc + TQ，都没到就回 3 转人工
    python -m ingest.jobs.daily_close_sync --date 2026-09-23
    python -m ingest.jobs.daily_close_sync --force      # 跳过①的时机守卫（补历史日用）

退出码：
    0 成功，或"库里已经有了"按设计什么都没做
    1 骨干（个股日线入库 + 回验）没成
    2 非交易日 / 未到收盘线，按设计跳过
    3 数据没到位且**点窗口那条也试过/有资格试** —— 要人来处理了（`_tell_human` 那句）
    4 数据还没到，但**今天还没绝望**：便宜的腿走完了、贵的腿按时刻还不该动 → 宿主会按
      `duanxian/jobs.py:DAILY_CLOSE_RETRY_MIN` 的节奏再敲本脚本一次（09-28/29 实测
      通达信的盘后文件到 17:2x 才落盘，缺的就是这一段等待，不是缺一次人工）
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.db.repo import schedule as sched_repo  # noqa: E402
from app.tdx_client import readiness  # noqa: E402

# 用户给的数：重启后"过 10 分钟还是没有数据"才转人工。做成常量并带轮询，
# 不是 sleep(600) —— 中间每次都在重新探 vipdoc 尾部日期，到位就立刻往下走。
RESTART_WAIT_SEC = 600
#: **没看到同步进程时**只等这么久（秒）。2026-09-27 实测：复用已登录会话时登录后
#: 自动同步根本不会再跑（s4 同一轮里就报了"本次登录未触发自动同步"，指数仍停在
#: 2026-09-23），这时等满 600s 是纯白等 —— 用户裁定的"重启后过 10 分钟还没数据
#: 就去手动下载"针对的是**同步在跑但没跑完**，不是"压根没同步"。
RESTART_ABSENT_WAIT = 120
RESTART_POLL_SEC = 60
#: 盘后下载之后的等待上限。分钟线实测 7′41″，但"转档"那段会在 p0 返回之后
#: 继续写文件，所以这里给到 25 分钟。**超时只是上限，不是判据** ——
#: 判据是 `TPyth*` 退出 + 覆盖过阈 + 覆盖数不再涨（见 `_wait_settled`）。
DOWNLOAD_WAIT_SEC = 1500

#: 每一个子作业的**硬超时**（秒）。为什么必须有：这里原先用 `subprocess.call` 无上限等待，
#: 一条卡住的 `c16`/`p0` 就能把整条收盘链钉死；而宿主那一层虽然有 2400 秒的段超时，
#: 它 `kill()` 的只是**直接子进程**（本脚本），本脚本再起的 `c1/c3/c16/p0/run_agent`
#: 会变成孤儿继续跑 —— 表现是"链断了但通达信还在动"，没人说得清那天到底补没补上。
#: 数值按 2026-09-29 实测给：c12 全市场约 90s、c16 扫 1.9 万个文件 91s、成员快照几秒。
STEP_TIMEOUT_SEC = 1200
#: ③b 那条腿（TQ 全市场 + 回算派生列）：实测约 90 秒，给十几倍余量够它自己重试一轮。
TQ_TIMEOUT_SEC = 1200
#: 桌面那条腿（p0 三段 + 分钟线 7′41″ + 转档 + run_agent 每步重试）。
GUI_TIMEOUT_SEC = DOWNLOAD_WAIT_SEC + 600


def _run(argv: list[str], *, what: str,
         timeout: int = STEP_TIMEOUT_SEC) -> int:
    """起一个子作业并等它，**有上限**。超时按进程树收（`duanxian/proc.py::kill_tree`，
    只收自己起的这一支，绝不按映像名杀），退出码交回 124 —— 124 从来不属于段脚本自己的
    语义，所以不会被 `close_verdict` 误读成"按设计跳过"。"""
    from duanxian.proc import kill_tree

    print('\n--- %s ---' % what, flush=True)
    print('$ %s   （超时上限 %d 分钟）' % (' '.join(argv), timeout // 60), flush=True)
    proc = subprocess.Popen([sys.executable, '-u', *argv], cwd=str(ROOT))
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        why = kill_tree(proc.pid)
        print('  **%s 超过 %d 分钟未完成 → 已收掉（%s）**' % (what, timeout // 60, why),
              flush=True)
        print('  这一场没有完成，按失败处理（不要拿半截产出往下算）', flush=True)
        return 124
    print('  ← 退出码 %s' % rc, flush=True)
    return rc


def _mod(mod: str, *args: str) -> list[str]:
    """本项目的作业一律 `-m 模块名`。

    这里**绝不能再写脚本路径**：`scripts/ops|ingest|compute/` 是参考仓库的目录结构，
    本项目对应文件在 `ingest/jobs/`，照抄旧路径的写法会让每一步都 `can't open file`
    —— `e1_fill_all.py` 就正踩着这个坑（已单独派修）。
    """
    return ['-m', mod, *args]


# ---------------------------------------------------------------- ① 时机

def step_timing(target: dt.date, now: dt.datetime) -> tuple[bool, str]:
    """今天是不是该补的那一天。"""
    if not sched_repo.is_trade_day(target):
        return False, ('%s 不是交易日（日历说不是），按设计不跑。'
                       '要强制跑用 --force。' % target)
    if now.date() == target and now.time() < readiness.POST_CLOSE_CUTOFF:
        return False, ('现在 %s，还没过收盘线 %s —— 当天的盘后数据不可能有，'
                       '目标日应当退回上一交易日。'
                       % (now.strftime('%H:%M'), readiness.POST_CLOSE_CUTOFF))
    return True, '交易日判定通过（目标 %s）' % target


# ---------------------------------------------------------------- ② 查库

def step_db(has: readiness.Readiness, target: dt.date) -> tuple[bool, str]:
    """库里有没有那一天。**有就不去取数** —— 这是幂等的根。"""
    v = has.db_ready(target)
    print(v.report(), flush=True)
    if v.ok:
        return True, ('PG 里 %s 已有全市场量级的日线 → 不取数、不重入库'
                      '（计算类照跑，它们自己有幂等门）' % target)
    return False, v.note or '库里没有 %s 的日线，需要往上游取' % target


# ---------------------------------------------------------------- ③ 查本地

# ③ 查本地：判"到没到那天"
def _vipdoc_state(has: readiness.Readiness, target: dt.date) -> dict:
    """把 vipdoc 三类数据一次探清，返回 {键: Verdict}。"""
    return {
        '个股日线': has.day_ready(target),
        '指数日线': has.index_ready(target),
        '1 分钟线': has.minute_ready(target, period=1),
        '5 分钟线': has.minute_ready(target, period=5),
    }


def _cov(v: readiness.Verdict) -> tuple[int, int]:
    """抽样里"尾部日期已到目标日"的个数。分母是**被抽到的文件数**，不是全市场。

    留着是因为 `_wait_settled` 之外没人再用它，而条数门（`completeness`）才是主判据；
    如果哪天要退回抽样，改的应该是这一处而不是再写第二份。
    """
    total = len(v.probes)
    fresh = sum(1 for p in v.probes if p.fresh(v.target))
    return fresh, total


# 用户点出的漏洞：`readiness.MIN_FRESH_RATIO = 0.6` 且每市场只有 3 只锚定票，
# 等于"3 只里 2 只新"就判就绪 —— 下到一半照样全绿，然后拿半截数据去算因子和评分，
# 错到 /api/coverage 上都看不出来。所以这里另加一道门，只判一件事：**下全了没有**。
# 2026-10-06 这道门从"放大抽样 ≥90%"换成**整目录条数**（用户裁定，见 `duanxian/completeness.py`）：
# 抽样会碰上"抽到的一排恰好全停牌"，而条数规则会跟着市场自己长（新股只增不减）。
SETTLE_POLLS = 2       # 覆盖数连续这么多轮不再增长 —— 作为"写完"的第二个证据


def step_complete(has: readiness.Readiness, target: dt.date, *, prev: int | None = None,
                  n: int | None = None) -> tuple[bool, str]:
    """判"下载完成了没有"，三个证据缺一不可。

    1. **后台同步进程 `TPyth*` 已经退了** —— 用户指的路子，也是最硬的那条：
       进程还在写文件，条数就还在涨，这时候入库等于跟下载抢文件。
       （`locator.sync_running()` 特意用前缀匹配：见过 `TPyth.exe` 也见过 `TPython.exe`，
       精确匹配会漏判成"以为同步完了"，然后拿旧数据往下跑。）
    2. **整目录条数过了动态基线** —— `completeness.vipdoc_counts` 数的是**全部** `.day`
       里尾部日期已到那天的个数（实测 2026-09-30：9,490 个 / 目录共 9,564 个），
       判据是 `本场条数 ≥ 前一天登记的条数 − 偏离值`（默认 30 条、分钟类 200，
       系统配置 → 条数完整性 改得动）。`day_ready` 那套锚定票只回答
       "新不新"，不回答"全不全"。
    3. **条数不再增长**（`prev` 是上一轮的值）—— 防止"进程恰好 momentarily 不在"
       或者半截停住被当成完成。

    返回 `(下全了吗, 说明)`。**没下全时调用方必须继续等或转人工，绝不进入库/加工。**
    """
    from app.tdx_client import locator
    from duanxian import completeness

    running, rwhy = locator.sync_running()
    if n is None:
        n = completeness.vipdoc_counts(target, kinds=('vipdoc_day',))['vipdoc_day']
    v = completeness.judge('vipdoc_day', target, n, record_it=False, source='close-chain')
    growing = prev is not None and n > prev
    detail = ('整目录 %s 个 .day 已到 %s｜基线 %s（前第 %s 场，%s）· 同步进程 %s'
              % (f'{n:,}', target, f"{v['baseline']:,}" if v['baseline'] else '无',
                 v['sessions_back'] or '—', v['baseline_basis'],
                 '还在跑：%s' % rwhy if running else '已退出'))
    if running:
        return False, '未下全 —— ' + detail
    if not v['complete']:
        return False, ('没下全 —— %s：%s。进程已退但条数没过基线：要么真下坏了，'
                       '要么这一场本来就缺一截，按没下全处理（宁可转人工也不拿半截数据算）'
                       % (detail, v['why']))
    if growing:
        return False, '还在长 —— 上一轮 %s 个、这一轮 %s 个，文件仍在写入。%s' % (f'{prev:,}', f'{n:,}', detail)
    return True, '下全了：%s' % detail


def _register_counts(target: dt.date) -> None:
    """**每判成一次就登记一次条数**（用户 2026-10-06 要的"每次记录总条数"）。

    只在这一步之前所有证据都过了门时调用：基线池里进半截那一天，第二天跟它比就双双通过。
    登记本身坏了不许带崩整条链 —— 数已经落盘了，明天还有下一次。
    """
    from duanxian import completeness

    try:
        r = completeness.check_vipdoc(target)
        for kind, v in r['verdicts'].items():
            print('  [登记条数] %s' % v['why'], flush=True)
    except Exception as exc:  # noqa: BLE001
        print('  [登记条数] 没登记上：%s: %s（不影响本轮入库，下一场再记）'
              % (type(exc).__name__, str(exc)[:120]), flush=True)


def step_vipdoc(has: readiness.Readiness, target: dt.date, *, prev: int | None = None,
                count: int | None = None) -> tuple[bool, str, int]:
    """③ 查本地：既要"到没到那天"，也要"下全了没有"。返回 `(可用, 说明, 条数)`。

    条数带出去是给等待循环当"还在长吗"的上一轮值用的，所以**判定没过也照数**。
    """
    from duanxian import completeness

    st = _vipdoc_state(has, target)
    for kind, v in st.items():
        print('  [%s] %s' % (kind, '就绪' if v.ok else '未就绪')
              + ('' if v.ok else '（落后 %s）'
                 % (v.stale_days if v.stale_days is not None else '?')), flush=True)
    # 取的是**已到这一天的文件个数**，不是目录里的文件总数：总数永远不变，
    # 拿它比"还长不涨"等于没判。整目录一次实测热态 ~6 秒、冷态 ~50 秒。
    cov_n = (completeness.vipdoc_counts(target, kinds=('vipdoc_day',))['vipdoc_day']
             if count is None else count)
    if not (st['个股日线'].ok and st['指数日线'].ok):
        return False, 'vipdoc 里个股/指数日线没到 %s → 要去通达信下载' % target, cov_n
    cok, cwhy = step_complete(has, target, prev=prev, n=cov_n)
    if not cok:
        return False, ('日期到了但**没下全**：%s → 继续等，不进库不算加工' % cwhy), cov_n
    if not (st['1 分钟线'].ok and st['5 分钟线'].ok):
        _register_counts(target)
        return True, ('日线齐且下全，但分钟线缺 → 仍要跑第二个 tab 补分钟线（%s）'
                      % cwhy), cov_n
    _register_counts(target)
    return True, 'vipdoc 日线齐、分钟线齐、条数过了门（%s）→ 不必下载，直接入库' % cwhy, cov_n


def _client_running() -> bool:
    """通达信在不在。用 `reset.snapshot()`，不在这里另写一份进程名清单
    （`TdxW/TdxSP/TdxNonSP` 加 `Pyth*` 前缀那份只在 `app/tdx_client/reset.py:29-30` 有）。"""
    from app.tdx_client import reset
    return bool(reset.snapshot())


def step_tq(target: dt.date, *, check_only: bool, no_gui: bool = False) -> tuple[bool, str]:
    """**第二条腿：不碰界面，直接问已登录的通达信客户端要日线**（`c12_tq_day`）。

    为什么它排在点窗口之前：2026-09-29 16:43 实测 —— vipdoc 一个 `.day` 都还没写
    （`[个股日线] 未就绪（落后 1）`），而 TQ 的 `--dry-run` 当场取到当天
    **个股 5,559 / 指数 174 / 板块 1,119 行**。也就是说"文件还没落盘"≠"客户端没有这一天的数"，
    中间隔着一段下载与写盘的延时；这几天天天缺一天，缺的就是这一段。
    而点窗口那条路要重启客户端、要活动输入桌面、要 8~10 分钟，还可能点不开。

    口径不混：TQ 的报文里没有换手率与涨跌停价，`c12` 自己把它们留给
    `recompute_turnover` / `recompute_limits` 按 `share_capital` 与板块规则回算，
    成交额万元→元 的换算它内部已做（历史上错过一次就是差 10000 倍，`trg_daily_sanity` 会拦）。

    返回 `(PG 里现在有这一天了吗, 说明)`。判据仍然只认**库里的行数**，
    不认 TQ 的返回值 —— `ErrorId 0` 只证明"调用没报错"，昨天那两次就是这么骗过链的。

    **前置**：客户端没开着/没登录就先自动拉起并登录（`step_launch`，用户 10-06 点头）。
    这一步不是可选的装饰：10-06 实测客户端关着的那天 TQ 直接是零，而链原先会一路走到 ③c
    去点窗口 —— 那条腿要 8~10 分钟还未必点得开。
    """
    if check_only:
        _ready, lwhy = step_launch(target, check_only=True, no_gui=no_gui)
        return False, ('`--check`：不实跑。真跑会先做客户端前置（%s），'
                       '再走这条无 GUI 的路：python -m ingest.jobs.c12_tq_day --date %s'
                       % (lwhy, target))
    ready, rwhy = step_launch(target, check_only=False, no_gui=no_gui)
    print('  [③b 前置 · 拉起并登录] %s' % rwhy, flush=True)
    if not ready:
        # 客户端起不来就别去问 TQ 了：`tq_bridge` 连不上是必然，白等一轮超时。
        return False, ('客户端起不来（%s）→ TQ 这条腿走不了' % rwhy)
    rc = _run(_mod('ingest.jobs.c12_tq_day', '--date', target.isoformat()),
              what='TQ 第二通道（无 GUI，问客户端要日线）', timeout=TQ_TIMEOUT_SEC)
    have, why = step_db(readiness.Readiness(), target)
    if have:
        return True, 'TQ 取到并已入库：%s（p0/点窗口那一条路今天不必走）' % why
    return False, ('TQ 这条路也没拿到（c12 退出码 %s）：%s' % (rc, why))


#: 拉起 + 登录 + 清弹窗这一段的预算。实测 s1~s3 一轮 2~3 分钟，给 10 分钟是三倍余量，
#: 不是"没上限"（超时按进程树收，见 `_run`）。
LAUNCH_TIMEOUT_SEC = 600


def _tdx_probe() -> dict:
    """客户端此刻的处境（`watchman.probe()` 那一份，本文件不另写进程名清单）。"""
    from app.tdx_client import watchman

    try:
        return watchman.probe() or {}
    except Exception as exc:  # noqa: BLE001  探不到就当"判不了"，交给后面的动作去撞
        return {'probe_error': '%s: %s' % (type(exc).__name__, str(exc)[:120])}


def step_launch(target: dt.date, *, check_only: bool,
                no_gui: bool) -> tuple[bool, str]:
    """**③b 的前置**：客户端没在跑、或开着但停在登录框上 → 自己拉起来并登录。

    用户 2026-10-06 20:5x 的裁定原文：「这个必须要你自动执行啊！太需要你去动界面了，帮我搞定就行」
    —— 之前"要不要允许系统收盘时自己拉起并登录"这一问是挂着等人的状态，而 10-06 实测
    客户端关着的那天 **TQ 是零**（`tq_bridge.available()` 的判据就是 `TdxW.exe` 在不在），
    于是 ③b 这条最便宜的腿直接失效，链只能等到 ③c 或转人工。

    同一段话里还有一条同等硬的条件：**每一步都要留下人能补的地方**。所以这里每一次动手
    （以及每一次"按闸门故意没动手"）都进 `duanxian/gui_trail.py` 的待人工清单，
    页面上是 `/api/jobs/chain` 的 `tdx_trail.pending`。

    判据不认"我发了启动命令"，只认**探针复验**：`running` 且不是 `login_box` 才算成。
    """
    from duanxian import gui_trail
    from duanxian import jobs as jstore

    st = _tdx_probe()
    if st.get('running') and not st.get('login_box'):
        return True, ('客户端已经在跑且登录着（pid %s）→ 不必拉起' % st.get('pid'))
    where = ('开着但停在登录框上' if st.get('running') else '没在跑')
    if check_only:
        return False, ('`--check`：不实跑。真跑会先自动拉起并登录（现在%s），'
                       '命令 `python -m app.tdx_client.run_agent --only s1_launch '
                       '--only s2_login --only s3_popup`' % where)
    if no_gui:
        gui_trail.add(step='s1_launch', what='收盘时自动拉起并登录通达信（③b 的前置）',
                      status='skipped', detail='`--no-gui`：机器侧不让碰桌面',
                      target=str(target), source='daily_close_sync')
        return False, ('`--no-gui`：不自动拉起客户端（现在%s）—— ③b 取不到数，'
                       '要么人自己开一次，要么去掉 `--no-gui`' % where)
    merged, _w = jstore.settings('tdx_watch', jstore.TDX_WATCH_DEFAULTS)
    if not merged.get('auto_launch', True):
        gui_trail.add(step='s1_launch', what='收盘时自动拉起并登录通达信（③b 的前置）',
                      status='skipped', detail='tdx_watch.auto_launch=false',
                      target=str(target), source='daily_close_sync')
        return False, ('`tdx_watch.auto_launch=false`：不让自动拉起（现在%s）'
                       '—— 人来点一下通达信，③b 立刻就有数' % where)

    rc = _run(_mod('app.tdx_client.run_agent', '--only', 's1_launch',
                   '--only', 's2_login', '--only', 's3_popup'),
              what='自动拉起并登录通达信（③b 前置，用户 10-06 点头）',
              timeout=LAUNCH_TIMEOUT_SEC)
    after = _tdx_probe()
    ok = bool(after.get('running')) and not after.get('login_box')
    # run_agent 的出口码 2 就是"要人接手"（定位失败、"已在别处登录"这类），
    # 与"做完了但探针不过"要分开写 —— 人看到的第一句得是处境，不是笼统的失败。
    status = 'done' if ok else ('need_human' if rc == 2 else 'failed')
    gui_trail.add(step='s2_login', what='自动拉起并登录通达信（③b 前置）', status=status,
                  detail='现在%s → 复验：%s（run_agent 退出码 %s）'
                         % (where, after.get('reason') or
                            ('登录着 pid %s' % after.get('pid') if ok else '还是没登录'), rc),
                  target=str(target), source='daily_close_sync')
    if ok:
        return True, '已自动拉起并登录（原来%s）→ ③b 可以问了' % where
    return False, ('自动拉起没成（现在%s、复验没登录、run_agent 退出码 %s）'
                   '—— 清单里已经写了人怎么补' % (where, rc))


def _hhmm_add(hhmm: str, n: int) -> str:
    m = int(hhmm[:2]) * 60 + int(hhmm[3:5]) + n
    return '%02d:%02d' % (m // 60 % 24, m % 60)


def _now() -> dt.datetime:
    """现在几点。**所有**取当前时刻的地方都走这一层，只为一件事：③c 该不该动桌面
    是按"离链的时点多少分钟"判的，用真时钟的话，同一份测试会在下午四点整突然变绿变红
    —— 而那种"偶尔红"的测试等于没有测试。"""
    return dt.datetime.now()


def gui_allowed_now(now: dt.datetime, target: dt.date, *, no_gui: bool) -> tuple[bool, str]:
    """最后一条腿（点窗口）该不该在这一轮走。**两道闸，理由都是实测来的**：

    1. `--no-gui`：不碰桌面。这一轮剩下的只有"等上游自己把文件写出来"，
       而 2026-09-28 那天 17:22 就是这么到的（没人点过一次鼠标）。
    2. 时点后头 `DAILY_CLOSE_GUI_AFTER_MIN` 分钟内不碰：那时候客户端的登录后自动同步
       正往 vipdoc 写文件，`reset` + 重开等于把正在跑的下载打断 —— 用户看到的
       「通达信跑到一半死机、入库只有部分股票日线」就是这个形状。
       链外的时刻（早上、`--date` 补历史）没有"正在自动同步"可打断，直接放行。
    """
    from duanxian import jobs as jstore

    if no_gui:
        return False, ('`--no-gui`：这一轮绝不碰桌面。%s 只靠本地 vipdoc 与 TQ 两条腿，'
                       '都没到就等下一档再看一次' % target)
    slot, whence = jstore.run_at()
    mins = (now.hour * 60 + now.minute) - (int(slot[:2]) * 60 + int(slot[3:5]))
    if mins < 0:
        return True, ('现在 %s 早于链的时点 %s（%s）—— 这是人工跑/补历史，'
                      '没有自动同步正在写文件，可以直接去点窗口'
                      % (now.strftime('%H:%M'), slot, whence))
    if mins < jstore.DAILY_CLOSE_GUI_AFTER_MIN:
        nxt = jstore.close_next_attempt_min(mins)
        return False, ('现在离 %s 只有 %d 分钟（阈值 %d）：这一段客户端的自动同步常常还在写 '
                       'vipdoc，重启就是打断它。%s'
                       % (slot, mins, jstore.DAILY_CLOSE_GUI_AFTER_MIN,
                          ('下一档在时点后第 %d 分钟（约 %s）。'
                           % (nxt, _hhmm_add(slot, nxt))) if nxt is not None else
                          '今天的重试档已用完，往后由体检红灯说话。'))
    return True, ('已过 %s +%d 分（现在 %s）→ 自动同步那一段早该完了，'
                  '还没到就该去点窗口' % (slot, jstore.DAILY_CLOSE_GUI_AFTER_MIN,
                                          now.strftime('%H:%M')))


def _hold(target: dt.date, why: str) -> int:
    """数据没到、但**今天还没绝望** —— 退出码 4，宿主按节奏表再敲一次，不转人工。"""
    print('\n[③ 结束] 便宜的腿走完了，%s 还没到 —— 按设计这**不叫要人来**：' % target, flush=True)
    print('  %s' % why, flush=True)
    print('  通达信的盘后文件实测到 17:2x 才落盘（09-28 那天链 16:31 就判了 stop，'
          '文件 17:22 才到齐）。缺的是这段等待，不是缺一次人工。', flush=True)
    print('  → 退出码 4（hold）：宿主会在下一档重新跑本脚本，届时 vipdoc/TQ 任一条到就入库。',
          flush=True)
    return 4


def step_download(target: dt.date, has: readiness.Readiness, *,
                  gui: bool, check_only: bool) -> tuple[bool, str]:
    """下载环节，严格按用户给的 (a)(b)(c) 顺序。

    (a) 客户端在跑但数据没到 → **重启它**（关掉→重新起→登录→等登录后的自动同步）。
        注意顺序：重启之后必须**先把客户端拉回来**再等，杀掉干等是等不到的
        （自动同步是登录成功后才跑的那一段，进程没了就永远不会到 —— 这版先前踩过）。
    (b) 等满 10 分钟仍没到 → 才去开「盘后数据下载」下日线。
    (c) 同一次里切第二个 tab 下 1/5 分钟线（**不传 `--day-only`**，传了就等于砍掉这一环）。

    返回 `(能不能继续, 说明)`。`False` 时调用方必须停下来转人工，不许继续入库。
    """
    prepare = _mod('app.tdx_client.run_agent', '--date', target.isoformat(),
                   '--only', 's1_launch', '--only', 's2_login',
                   '--only', 's3_popup', '--only', 's4_autosync')
    # p0 自己就是 prepare→day→minute 三段，且每段跑完都用文件判据再验一次。
    download = _mod('ingest.jobs.p0_tdx_predownload', '--date', target.isoformat())

    if check_only:
        if not gui:
            return False, ('`--check --no-gui`：最后一条腿本来就不走。真跑到这一步会回'
                           '退出码 4（等下一档），不是 3（要人来）—— 见 `gui_allowed_now`')
        return True, ('`--check`：不去点窗口。真跑会依次执行\n'
                      '         (a) %s\n'
                      '             然后每 %d 秒探一次 vipdoc 尾部日期，最多等 %d 分钟\n'
                      '         (b)(c) %s'
                      % (' '.join(prepare), RESTART_POLL_SEC, RESTART_WAIT_SEC // 60,
                         ' '.join(download)))
    if not gui:
        # 调用方（`main`）已经用 `gui_allowed_now` 拦过一遍，这里是最后一道门：
        # 无桌面模式一旦误开这条腿就是**在用户的机器上动鼠标**，宁可回 False。
        return False, ('`--no-gui`：不碰窗口，而本地 vipdoc 与 TQ 都没到 %s，'
                       '这一轮拿不到这一天。要么到电脑前不带 `--no-gui` 跑一次'
                       '（会去点「盘后数据下载」），要么先在通达信里手工做完盘后下载再回来入库'
                       % target)

    # ---- (a) 在跑但没数据 → 重启，让它的登录后自动同步补一段 ----
    if _client_running():
        print('\n  (a) 客户端在跑但 vipdoc 没到目标日 → 重启它'
              '（自动同步是登录成功后才跑的那一段）', flush=True)
        _run(_mod('app.tdx_client.reset'), what='(a) 关掉当前通达信进程',
             timeout=300)
        _run(prepare, what='(a) 重新启动 + 登录 + 等自动同步', timeout=GUI_TIMEOUT_SEC)
        # 只有"同步进程确实在跑"才值得等满 10 分钟；没看到进程就短等，到点进 (b)。
        from app.tdx_client import locator as _loc
        running, who = _loc.sync_running()
        budget = RESTART_WAIT_SEC if running else RESTART_ABSENT_WAIT
        print('  (a) 同步进程：%s → 这一轮最多等 %ds' % (who, budget), flush=True)
        ok, why, _n = _wait_settled(has, target, '(a) 重启后等自动同步', budget=budget)
        if ok:
            return True, why
    else:
        print('\n  客户端不在跑 —— 直接起它并走下载', flush=True)
        _run(prepare, what='(a) 启动 + 登录（客户端本来没开）', timeout=GUI_TIMEOUT_SEC)

    # ---- (b)(c) 盘后数据下载：日线 tab + 第二个 tab 的 1/5 分钟线 ----
    rc = _run(download, what='(b)(c) 盘后数据下载（日线 + 1/5 分钟线）',
              timeout=GUI_TIMEOUT_SEC)
    ok, why, _n = _wait_settled(has, target, '(b)(c) 下载后回验',
                                budget=DOWNLOAD_WAIT_SEC)
    if ok:
        return True, why + '（p0 退出码 %s）' % rc
    if rc == 3:
        # 日线到位、只有分钟线没拿到。日线是骨干，够往下入库；分钟线要单独补。
        return True, ('日线到位、分钟线没到（p0 退出码 3）→ 先入库日线，'
                      '分钟线回头单独补：run_agent --only s5_dialog --only s7_minute')
    return False, ('自动手段用尽：重启过、等满了、盘后下载也跑过 —— %s' % why)


def _wait_settled(has: readiness.Readiness, target: dt.date, tag: str,
                  *, budget: int | None = None) -> tuple[bool, str, int]:
    """等到"日期到了 **且** 下全了"，不是等到"看起来有数据"。

    判据是 `step_vipdoc`（内含覆盖门），**超时只是上限**：
    真正的完成信号是后台同步进程 `TPyth*` 退出 + 抽样覆盖过阈 + 覆盖数不再涨。
    每轮把上一轮覆盖数带进去，用来识别"还在写文件"。

    `budget` 不给就**在调用时**读模块常量 —— 写成默认参数值会在定义时绑定，
    之后改 `RESTART_WAIT_SEC`（或测试里 monkeypatch 它）都不生效。
    """
    if budget is None:
        budget = RESTART_WAIT_SEC
    deadline = time.monotonic() + budget
    prev: int | None = None
    last = ''
    while True:
        ok, why, cov = step_vipdoc(has, target, prev=prev)
        last = why
        if ok:
            return True, '%s：%s' % (tag, why), cov
        left = int(deadline - time.monotonic())
        if left <= 0:
            return False, '%s 等满 %d 分钟仍未过门 —— %s' % (tag, budget // 60, last), cov
        print('  %s 还剩 %ds：%s（上一轮覆盖 %s）'
              % (tag, left, why, '—' if prev is None else prev), flush=True)
        prev = cov
        time.sleep(min(RESTART_POLL_SEC, left))


# ---------------------------------------------------------------- ④ 入库与回验

def step_ingest(target: dt.date, *, check_only: bool,
                skip_bars: bool = False) -> dict[str, int]:
    """入库：个股/指数/板块 + 分钟线。逐个报，不拼成一步。

    `skip_bars`：个股/指数/板块日线已经由 **③b 的 `c12_tq_day` 直接写进库**了（它连
    前收/涨跌停/换手率/涨跌家数都自己回算）。这时候再跑 c1/c3，等于拿"还没下全的
    vipdoc"去 upsert 覆盖一份已经齐的数 —— 与 ② 那条"有就不重入库"是同一条理由，
    只是对象换成了 TQ 那条腿的产物。分钟线 TQ 给不了，`c16` 照旧要去 vipdoc 看一眼
    （没到它自己报缺，不影响日线）。
    """
    steps = [
        ('个股/指数/基金日线', _mod('ingest.jobs.c1_day'), 1800),
        ('板块日线', _mod('ingest.jobs.c3_sector'), 900),
        # 分钟线以前没人入（s9 只跑 c1/c3/b6）：下回来的 7 分钟白下。
        # 2026-09-29 实测 c16 扫 19,121 个文件 = 91 秒，给 20 分钟是十倍余量。
        ('1/5 分钟线', _mod('ingest.jobs.c16_minute', '--date', target.isoformat()), 1200),
    ]
    out: dict[str, int] = {}
    for what, argv, budget in steps:
        if skip_bars and what != '1/5 分钟线':
            print('\n跳过 %s：TQ 那条腿已经把它写进库了，不拿半截 vipdoc 覆盖'
                  '（本应执行 %s）' % (what, ' '.join(argv)), flush=True)
            out[what] = 0
            continue
        if check_only:
            print('\n跳过 %s（本轮不入库 —— --check 只判定，或库里已有这一场。'
                  '本应执行 %s）' % (what, ' '.join(argv)), flush=True)
            out[what] = 0
            continue
        out[what] = _run(argv, what='入库 · %s' % what, timeout=budget)
    return out


def step_signal(*, check_only: bool) -> dict[str, int]:
    """链尾的成员快照。**非骨干**：失败要报，但不把整链判死（日线才是骨干）。

    **这里只剩 `--snapshot-only` 一步**，档位（`--date`）不在本文件里跑 ——
    2026-09-29 实测到它每天都必然 exit 1：`analytics.sector_heat_daily` 那一天还没有行
    （热度是段2 `e1_fill_all` 的 `heat`/`sectortrend` 写出来的，段1 跑在它之前），
    而段1 一旦进 `ok` 集合，宿主当天不会再回来补它。
    `e1_fill_all` 的 `signal` 步正排在热度之后（它自己的注释就写着"启动点必须紧跟在
    sectortrend 之后"），所以档位该只在**那里**算一次，在这儿算第二次等于每天给汇总
    制造一行假故障。

    快照为什么必须留在这条链上：这是 `W=21` 实盘序列的**唯一起点**，少跑一天就永久少一天
    （实盘序列补不回来），所以它不能挂在段2 上 —— 段2 因为别的原因停一天，
    快照就会跟着缺一天。非交易日整条跳过（①已经把门），别把 `2026-09-26` 周六写进
    `watchlist_member_snapshot`，那会让 21 场的窗口起点落在一个没有行情的日子上。
    """
    argv = _mod('app.compute.sector_trend_signal', '--snapshot-only')
    if check_only:
        print('\n`--check`：跳过 自选成员快照（本应执行 %s）' % ' '.join(argv), flush=True)
        return {'自选成员快照': 0}
    # 快照只读 ini 与库里成员，实测几秒钟；给 10 分钟是余量，不是"没上限"。
    return {'自选成员快照': _run(argv, what='计算 · 自选成员快照', timeout=600)}


def step_verify(target: dt.date, has: readiness.Readiness) -> tuple[bool, str]:
    """用户四步里的第 ④ 步：**过一会儿再看本地数据有了没有**。"""
    v = has.db_ready(target)
    print(v.report(), flush=True)
    if not v.ok:
        return False, '入库后回验：PG 里 %s 仍不是全市场量级 —— 别信前面的退出码，以这条为准' % target
    m = has.minute_ready(target)
    note = '' if m.ok else '（注意：分钟线回验未过，日线是好的但 9-22 那种"只有一天"的缺口会延续）'
    return True, '回验通过：PG 里 %s 有数了%s' % (target, note)


# ---------------------------------------------------------------- 编排

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='目标交易日 YYYY-MM-DD，默认自动推算')
    ap.add_argument('--check', action='store_true',
                    help='只判定：跑完 ①②③ 报"缺什么、三条腿各会怎么走"，不下载不入库')
    ap.add_argument('--no-gui', action='store_true',
                    help='绝不碰桌面：只用 vipdoc + TQ 两条腿，都没到就回 3 说明要人做什么')
    ap.add_argument('--force', action='store_true', help='跳过 ① 的时机守卫（补历史日）')
    ap.add_argument('--launch-only', action='store_true',
                    help='只做"拉起并登录通达信"这一件事就退出（宿主按 `daily_close.launch_lead_min` '
                         '提前发的那一次）。不查库、不取数、不入库 —— 那些仍是到点那条链的活。')
    args = ap.parse_args()

    now = _now()
    target = (dt.date.fromisoformat(args.date) if args.date
              else readiness.expected_trade_date(now))
    if args.launch_only:
        # 这一支**不走 ① 的时机守卫**：它的存在意义就是"比链早几分钟"。
        # 也不走 ②③：拉起之后客户端自己还要同步几分钟数据，那正是我们要等的。
        ok, why = step_launch(target, check_only=False, no_gui=args.no_gui)
        print('[只拉起 · 现在 %s · 目标 %s] %s —— %s'
              % (now, target, '客户端已登录' if ok else '没成', why), flush=True)
        # 0 = 可以问了；3 = 要人来（`auto_launch=false`、`--no-gui`、或 run_agent 判要接手）
        return 0 if ok else 3
    has = readiness.Readiness()
    print('=== 每日收盘补数据 · 现在 %s · 目标交易日 %s%s ==='
          % (now, target, '（--check 只判定）' if args.check else ''))

    # ① 时机
    ok, why = step_timing(target, now)
    print('\n[① 时机] %s' % why, flush=True)
    if not ok:
        if not args.force:
            return 2
        print('  --force 已给，继续（请自己确认 %s 确实是交易日）' % target, flush=True)

    # ② 查库
    print('\n[② 查库] 先看 PG 有没有 —— 有就不去取数、不重入库', flush=True)
    have, why = step_db(has, target)
    print('  %s' % why, flush=True)

    # 数据是从哪条腿来的，决定 ④ 要不要重跑入库（②③ 两条路都会把 `have` 置真，
    # 但只有 TQ 那条**不能**连分钟线一起跳过 —— 见下面 `step_ingest` 的两个参数）。
    tq_landed = False
    if not have:
        # ③ 获取：三条腿按代价从低到高。③a 只读文件（0 秒）→ ③b 问客户端要数（几秒，
        # 不碰桌面，`c12_tq_day` 自己写库）→ ③c 点窗口（要重启客户端、8~10 分钟、可能点不开）。
        # 这个顺序是 2026-09-29 复盘定的：老顺序里 ③b 根本不存在，于是"文件还没落盘"
        # 被当成"今天没数据"，而 TQ 在 16:43 的 dry-run 里当场就取到了当天全场。
        print('\n[③ 获取] 三条腿按代价排：③a 本地 vipdoc → ③b TQ 直取（不碰桌面）'
              '→ ③c 点窗口（重启客户端）', flush=True)
        usable, why, _cov0 = step_vipdoc(has, target)
        print('  [③a 本地 vipdoc] %s —— %s' % ('可用' if usable else '不可用', why), flush=True)

        if not usable:
            got, why = step_tq(target, check_only=args.check, no_gui=args.no_gui)
            print('  [③b TQ 直取] %s —— %s' % ('已入库' if got else '未成', why), flush=True)
            if got:
                have = True            # c12 自己就写库并回算了派生列，④ 不必再让 c1/c3 上一遍
                tq_landed = True
            else:
                allow, why_allow = gui_allowed_now(now, target, no_gui=args.no_gui)
                if args.check:
                    ok, why = step_download(target, has, gui=not args.no_gui,
                                            check_only=True)
                    print('  [③c 点窗口] %s' % why, flush=True)
                    if not allow:
                        print('  但按现在的时刻**这一轮不会**去点窗口：%s' % why_allow, flush=True)
                    print('\n--check：到此为止。上面几句"本该怎么补"就是真跑会做的事。', flush=True)
                    return 0
                if not allow:
                    if args.no_gui:
                        # 人工带的旗标，转人工是对的：这一轮**没有人授权动桌面**，
                        # 机器侧的腿已经走完，剩下的只有"人去点一下"或"等上游自己写盘"。
                        print('  [③c 点窗口] 未走 —— %s' % why_allow, flush=True)
                        _tell_human(target)
                        return 3
                    # 宿主自己那一轮、时刻还早 → 不是失败，是"上游晚到"，等下一档。
                    return _hold(target, why_allow)
                ok, why = step_download(target, has, gui=True, check_only=False)
                print('  [③c 点窗口] %s —— %s' % ('成' if ok else '未成', why), flush=True)
                if not ok:
                    _tell_human(target)
                    return 3

    # ③′ 计算类 —— **故意放在 ② 的早退之外**。
    # "行情已经有了、只是还没算"正是手动重补最常见的一天：如果因为②判定已有就
    # 直接 return，挂在链尾的档位与成员快照在这种日子里永远不会跑，
    # 而成员快照少一天就永久少一天（W=21 的实盘序列补不回来）。
    if args.check:
        step_signal(check_only=True)
        print('\n[④ ] --check：不入库、不算档、不回验。要真跑去掉 --check。', flush=True)
        return 0

    # `tq_landed` 时不能整段跳过入库：TQ 只给得了日线三张表，**分钟线仍要 c16 去 vipdoc 试一次**。
    results = step_ingest(target, check_only=(have and not tq_landed),
                          skip_bars=tq_landed)
    results.update(step_signal(check_only=False))

    # ④ 回验
    print('\n[④ 回验] 只认 PG 里有没有那天的数', flush=True)
    vok, vwhy = step_verify(target, has)
    print('  %s' % vwhy, flush=True)

    print('\n=== 汇总 ===')
    for k, v in results.items():
        print('  %-16s %s' % (k, 'OK' if v == 0 else 'exit %s' % v))
    print('  回验：%s' % ('过' if vok else '不过'))
    daily_ok = results.get('个股/指数/基金日线', 1) == 0 and vok
    print('  退出码', 0 if daily_ok else 1)
    return 0 if daily_ok else 1


def _tell_human(target: dt.date) -> None:
    """转人工时要说清"你去做什么"，不是只说失败了。

    ⚠️ 走到这里意味着**三条腿都试过了**（含重启客户端那一条）。如果只是"时刻还早、
    便宜的腿没拿到"，那是 `_hold` 的退出码 4，不该出现在这段里 —— 两者处置不同，
    把它印成"要人来"会让人白跑一趟电脑前。
    """
    print('\n' + '=' * 58)
    print('需要人工介入（vipdoc、TQ、重启客户端点窗口三条腿都用尽了）')
    print('=' * 58)
    print('  0. 先看一眼无 GUI 的那条为什么没成，它最省事：'
          'python -m ingest.jobs.c12_tq_day --date %s --dry-run' % target)
    print('     （它报"TQ 接口不可用"= 客户端没登录；报取到 0 行 = 客户端自己也没这场数）')
    print('  1. 打开通达信并登录（自动登录在 s1/s2，若它自己起不来通常是主站连接掉了）')
    print('  2. 系统 → 数据下载 → 盘后数据下载：勾「日线」（含 sh/sz/bj）→ 开始下载')
    print('  3. **切到第二个标签页**，勾「1 分钟线」和「5 分钟线」→ 开始下载（约 8 分钟）')
    print('  4. 下完不用管入库，重跑一次即可：python -m ingest.jobs.daily_close_sync'
          ' --date %s' % target)
    print('  为什么是你手动：本机的 GUI 自动化就是点这三下（steps/s5_dialog.py、s6_day.py、')
    print('  s7_minute.py），它没点成一般是控件定位失败，--probe 会 dump 控件树到 var/stuck/。')


if __name__ == '__main__':
    raise SystemExit(main())
