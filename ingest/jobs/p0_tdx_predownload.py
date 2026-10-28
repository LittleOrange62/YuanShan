# -*- coding: utf-8 -*-
"""
P0 · 通达信盘后数据下载 —— **程序同步数据之前的前置动作**。

这是"把通达信 GUI 那一套点击固化下来"的入口。以前这件事要人手动做：
关掉通达信 → 重开 → 全屏 → 顶部第 8 个菜单「TQ策略」→「TQ数据设置」→
「盘后数据下载」→ 第 1 个 tab 勾日线点开始 → 第 2 个 tab 勾 1 分钟线和
5 分钟线点开始 → 等它跑完。**2026-08-31 这条路整条实测跑通**
（日线 10s、1 分钟线 9,542 只 4.5min、5 分钟线 9,542 只 3.2min），
知识固化在 `app/tdx_client/dl_dialog.py`，这里只负责编排与盯进度。

---- 分几步、每步在等什么 ----

    1. prepare  s1_launch → s2_login → s3_popup → s4_autosync
                启动/复用通达信、登录、清弹窗、等登录后的自动同步跑完
    2. day      s5_dialog → s6_day
                打开「盘后数据下载」→ 沪深京日线页勾「日线和实时行情数据」
                → 开始下载 → 盯状态栏到「下载完毕」
    3. minute   s5_dialog → s7_minute
                重新打开对话框 → 切「沪深京分钟线」页 → 勾 1 分钟线 +
                5 分钟线 → 开始下载 → 盯状态栏到「下载完毕」

**为什么 minute 也要跑一次 s5_dialog**：每个 `run_agent` 子进程结束时
`finally` 里都会无条件 `s8_finish.cleanup()` 把对话框关掉、通达信最小化
（否则失败时通达信会最大化摊在屏幕上没人管）。所以下一步必须自己重开。
这也是分步的好处：任何一步单独重跑都成立。

---- 持续监测 ----

- 子进程输出**逐行透传**并加上耗时前缀，分钟线那一遍的
  `正在下载1分钟线数据..[20260831-20260831] 5850/9542` 会实时打出来；
- 另有一个守护线程每 `HEARTBEAT` 秒打一行心跳（已跑多久、最近一条输出多久前），
  **超过 `STALL_LIMIT` 秒没有任何输出**判为卡死并杀掉子进程 —— 这条比总超时
  有用得多：真卡住时状态栏文字是不变的，而总超时要等满一小时才发现；
- 每一步跑完都用**文件判据**再验一次（`.day` / `.lc1` / `.lc5` 尾部的日期，
  见 `app/tdx_client/readiness.py`），不信任"通达信说完成了"——
  实测出现过窗口消失而文件日期还是上一个交易日的情况。

---- 出问题就从头再来 ----

`--attempts N`（默认见 `DEFAULT_ATTEMPTS`）：任何一步失败就**先把通达信相关
进程全杀掉**（`app/tdx_client/reset.py`：`TdxW.exe` + `TPyth*`，等到真的消失
再走），然后从第 1 步整条重来。第 1 轮不杀 —— 常态是通达信已经登录好摊在那，
杀掉重来要多花两三分钟登录时间。

---- "下载到一半没成功"的三种判法，各自的动作与上限（2026-10-06 写死） ----

用户点名的这条，坏在**三种"没成"该做的事完全不一样**，混成一种就必然出错：

| 形状 | 怎么判（都是可观测的，不靠"感觉"） | 动作 | 上限 |
|---|---|---|---|
| **A 卡死** | 子进程 `STALL_LIMIT=600s` 没有任何输出（分钟线那遍状态栏每几秒就变，5 分钟不出声必是死了）；或窗口消失而 `.day` 尾部日期没前进 | 守护线程杀掉子进程 → 本轮判失败 → 走上面的"从头再来" | `--attempts`（默认 2 轮）**且**总预算 `--budget-sec`（默认 1800s），先到算哪个 |
| **B 慢，还在推进** | 每轮之后取一次**日线前沿日期**（`readiness` 抽样 max(`last_date`)）与 `TPyth*` 存活：前沿**在涨**或下载子进程**还在** | **不动界面、不重开对话框**，等；到点没到就交回链子按 hold 重试 | 本脚本不再另等（等是链子的事，见出口码 4） |
| **C 要人来** | 子进程交回 rc=2（GUI 定位失败 / 登录框弹出来了 / 广告吃掉焦点），现场包在 `var/stuck/` | 不再自动重来（重来只是把同一个失败再演一遍），直接出声 | 1 次，立即交人 |

A 与 B 的区别是这一条里最容易写错的：**把"晚到"读成"死了"会去重启客户端，
而重启会打断一次本来正在成功的下载**（实测 09-28 那次链判停时通达信还在下，
文件 17:2x 才落）。所以 B 一律不重启，只交回链子等。

**"上限"这一列必须落在自己头上**（2026-10-06 加的 `BUDGET_SEC` / `--budget-sec`）：
三步超时相加 6600s/轮，而最紧的上级 `daily_close_sync.py` 只给 `GUI_TIMEOUT_SEC=2100s`
—— 不自限就是上级到点按**进程树**把我们收掉，那一轮点没点完、vipdoc 有没有写一半
从此没人知道（没有出口码也没有归因）。现在每一步的超时是 `min(该步上限, 剩余预算)`，
剩余不够 `MIN_PHASE_SEC` 就不开新步、不够 `MIN_ROUND_SEC` 就不起新一轮，
带出口码自己回来。反面的一半是 C 与"快速失败的 A"：**不许在几十秒内把自己重试完**
（链的下一档是 +25 分钟，我们自己跑完了它就永远等不到），
所以子进程交回 rc=2 立刻停手，A 的两轮之间至少隔 `ROUND_SPACING_SEC=60s`。
完整三分类与"退化成晚到等到"写在 `docs/数据获取工作指南/03-故障排查.md`。

出口码：

    0  日线和分钟线都到目标交易日（含"本来就到位、什么都没做"）
    1  日线没拿到，而且**没有任何还在推进的证据** —— 后面的同步/入库不该继续
    2  需要人工/智能体接手（GUI 定位失败这类）；现场包路径在子进程输出里
    3  日线到位、只有分钟线没拿到（日常复盘吃日线，所以单列一档）
    4  **日线没到，但覆盖前沿在涨 / 下载子进程还在** —— 这是"晚到"，
       链子该按重试节奏等（对齐 `daily_close` 的 hold），**不该升级成"要人来"**；
       等满还是不到，下一轮自会以 1 或 2 报出来

用法：
    python -m ingest.jobs.p0_tdx_predownload                 # 日线 + 分钟线
    python -m ingest.jobs.p0_tdx_predownload --day-only      # 只要日线（快）
    python -m ingest.jobs.p0_tdx_predownload --attempts 3    # 最多重来 3 轮
    python -m ingest.jobs.p0_tdx_predownload --budget-sec 600  # 只给 10 分钟，到点自己回
    python -m ingest.jobs.p0_tdx_predownload --date 2026-08-28
    python -m ingest.jobs.p0_tdx_predownload --check         # 只判定，不动界面
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.tdx_client import locator, readiness, reset  # noqa: E402
from duanxian import proc  # noqa: E402  按进程树收子作业（唯一一份实现，绝不按映像名杀）

#: 心跳间隔（秒）。长时任务没有回显会被当成卡死然后被手动 kill。
HEARTBEAT = 60
#: 多久没有任何输出算卡死。分钟线那一遍状态栏每几秒就变一次，
#: 5 分钟不出声肯定不对了。s4 等自动同步是最沉默的一段，所以别调太小。
STALL_LIMIT = 600
#: 守护线程的检查周期。
GUARD_TICK = 5.0
DEFAULT_ATTEMPTS = 2

# ---- 自限预算（2026-10-06 加，缺了就会被上级"掉断开"）----
#
# 三个上级用不同的超时跑本脚本，最紧的是 `daily_close_sync.py` 的
# `GUI_TIMEOUT_SEC = 2100`（`e1_fill_all.py` 给 tdxdl 4200，`c10_catchup` 给 5400）。
# 而上面的 `PHASES` 超时相加是 6600s/轮、`--attempts 2` 最坏 13200s ——
# 也就是说**默认形状下，上级会先按进程树把我们收掉**（`proc.kill_tree`），
# 那一轮到底点没点完、对话框停在哪个状态、vipdoc 写没写一半，
# 从此没有任何人知道：没有出口码、没有归因、只有"父作业等满了"。
# 所以预算必须写在本脚本里，而不是指望调用方给够。
BUDGET_SEC = 1800
#: 剩余预算至少这么多才允许**再开一步**（秒）。开了也跑不完的那一步只会留下
#: 一个"被自己杀掉"的中间状态。
MIN_PHASE_SEC = 120
#: 剩余预算至少这么多才允许**再起一轮**（第 2 轮起要先 kill_all 再从 s1 点起）。
MIN_ROUND_SEC = 600
#: 两轮之间至少隔这么多秒。上一条硬要求的另一半：一轮只花几十秒就把自己重试完，
#: 链的下一档（+25 分钟）就永远等不到我们 —— 快速失败必须先停手、把等待交回链子。
ROUND_SPACING_SEC = 60

#: 三步。`(名字, 说明, 步骤 key 列表, 超时秒)`
#: 超时是**整步**的上限，按实测值留 3~5 倍余量（实测：prepare 约 2min、
#: day 10s、minute 7min41s）。
PHASES = (
    ('prepare', '启动/登录/清弹窗/等自动同步',
     ('s1_launch', 's2_login', 's3_popup', 's4_autosync'), 1200),
    ('day', '打开下载对话框 + 下载日线',
     ('s5_dialog', 's6_day'), 1200),
    ('minute', '重开对话框 + 下载 1/5 分钟线',
     ('s5_dialog', 's7_minute'), 4200),
)

#: run_agent 的"要人来接手"。A 类才该重来，C 类不该。
EXIT_HANDOFF = 2


def verdicts(target: dt.date, *, minute: bool) -> tuple[bool, bool, list[str]]:
    """
    读文件判定：`(日线到位吗, 分钟线到位吗, 逐条说明)`。

    **这是每一步之后的唯一硬判据。** 通达信状态栏说「下载完毕」只代表它自己
    那侧的流程走完了，实测出现过文件日期没动的情况。
    """
    r = readiness.Readiness()
    v_day = r.day_ready(target)
    lines = [v_day.report()]
    ok_min = True
    if minute:
        v1 = r.minute_ready(target, period=1)
        v5 = r.minute_ready(target, period=5)
        lines += [v1.report(), v5.report()]
        ok_min = v1.ok and v5.ok
    return v_day.ok, ok_min, lines


def run_phase(name: str, keys: tuple[str, ...], *, target: dt.date,
              timeout: float) -> tuple[int, str]:
    """
    跑一步 = 一个 `run_agent --only ...` 子进程。返回 `(出口码, 说明)`。

    输出逐行透传（不 capture 到文件），另开守护线程盯心跳和"多久没出声"。
    传 `--attempts 1`：重来这件事由本脚本在**步骤之上**做，两层都重来会
    让一次调用最坏情况变成小时级。
    """
    only: list[str] = []
    for k in keys:
        only += ['--only', k]
    cmd = [sys.executable, '-u', '-m', 'app.tdx_client.run_agent',
           '--date', target.isoformat(), '--attempts', '1', *only]
    if os.environ.get('PYTEST_CURRENT_TEST'):
        # 这是本脚本**唯一**能碰到用户桌面的地方：pytest 里没 monkeypatch `run_phase`
        # 就是测试写错了（2026-10-06 真起过一次，TdxW.exe 自己弹了出来）。
        # 挡在这里而不是挡在 run_agent 里，是因为只有这一层知道"该用桩"。
        return 1, ('PYTEST 硬挡：这一步没 monkeypatch p0.run_phase，'
                   '真起 run_agent 会在用户桌面启动通达信')
    print('  $ %s' % ' '.join(cmd[2:]), flush=True)

    t0 = time.time()
    try:
        p = subprocess.Popen(                                    # noqa: S603
            cmd, cwd=str(ROOT), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
            encoding='utf-8', errors='replace', bufsize=1)
    except OSError as exc:
        return 1, '子进程起不来：%s' % exc

    last = [time.time()]
    verdict = ['']
    stop = threading.Event()

    def guard() -> None:
        beat = time.time()
        while not stop.wait(GUARD_TICK):
            now = time.time()
            idle, total = now - last[0], now - t0
            if idle > STALL_LIMIT or total > timeout:
                # **按进程树收**：只 `p.kill()` 杀的是 p0 自己起的 run_agent，
                # 它再往下的子孙会变成孤儿 —— 表现是"这一步已经判失败了，
                # 通达信那边却还在动"，那天的数据到底算不算数没人说得清。
                # 用 `proc.kill_tree(p.pid)`（按 pid 收，**绝不按映像名**：
                # `/IM TdxW.exe` 会把用户桌面上正开着的客户端一起杀掉）。
                reason = ('**卡死**：%.0fs 没有任何输出（上限 %ds）' % (idle, STALL_LIMIT)
                          if idle > STALL_LIMIT else
                          '**超时**：跑了 %.0f 分钟（上限 %.0f 分钟）'
                          % (total / 60, timeout / 60))
                note = proc.kill_tree(p.pid)
                p.wait(timeout=60)
                verdict[0] = '%s，已收进程树：%s' % (reason, note)
                return
            if now - beat >= HEARTBEAT:
                beat = now
                print('  … 已跑 %.1f 分钟，最近一条输出 %.0fs 前（%s）'
                      % (total / 60, idle, name), flush=True)

    g = threading.Thread(target=guard, name=f'guard-{name}', daemon=True)
    g.start()
    try:
        for line in p.stdout:                       # type: ignore[union-attr]
            last[0] = time.time()
            print('  | %s' % line.rstrip(), flush=True)
        rc = p.wait()
    finally:
        stop.set()
        g.join(timeout=GUARD_TICK + 1)

    if verdict[0]:
        print('  %s' % verdict[0], flush=True)
        return 1, verdict[0]
    return rc, '出口码 %s，用时 %.0fs' % (rc, time.time() - t0)


def budget_sec(cli: int | None = None) -> int:
    """预算的三档来源：命令行 > 环境变量 `TDX_PRELOAD_BUDGET_SEC` > `BUDGET_SEC`。

    为什么用 env 而不进 `CLOSE_DEFAULTS`：那是收盘链的**时点/开关**默认值表，
    这里是一个作业的内部安全上限，不给人当旋钮用（要改就用 `--budget-sec` 显式传，
    调用方知道自己给了上级多少超时）。脏值一律回落到默认常量，不做"看起来能配"的兜底。
    """
    raw = cli if cli is not None else os.environ.get('TDX_PRELOAD_BUDGET_SEC', '')
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return BUDGET_SEC
    return n if n > 0 else BUDGET_SEC


def one_round(target: dt.date, *, day_only: bool, rnd: int, rounds: int,
              deadline: float | None = None
              ) -> tuple[bool, bool, int, str]:
    """
    跑一轮完整链路。返回 `(日线到位, 分钟线到位, 最后一步的出口码, 因预算停下的说明)`。

    **每一步之后都用文件判据看一次**，到位就不再往下点：日线已经是目标日时
    s6 那一步本来也会 precheck 跳过，但在这里就停下能少开一个子进程。

    `deadline`（单调时钟的绝对时刻）是这轮的**硬自限**：每一步的超时取
    `min(该步上限, 剩余预算)`，剩余不够 `MIN_PHASE_SEC` 就不开这一步、直接带着
    "预算用完"回来。没有这一段的话，上级只会用 `kill_tree` 把我们收掉 ——
    既不报告也不留出口码。
    """
    phases = [p for p in PHASES if not (day_only and p[0] == 'minute')]
    rc = 0
    stopped = ''
    for i, (name, what, keys, timeout) in enumerate(phases, 1):
        head = ('第 %d/%d 轮 · ' % (rnd, rounds)) if rounds > 1 else ''
        left = (deadline - time.time()) if deadline else None
        if left is not None and left < MIN_PHASE_SEC:
            stopped = ('预算只剩 %.0f 秒，不够再开一步「%s」（下限 %ds）'
                       % (left, name, MIN_PHASE_SEC))
            print('\n=== %s%d/%d %s（%s）：跳过 ===\n  %s' % (head, i, len(phases),
                                                            what, name, stopped),
                  flush=True)
            break
        eff = timeout if left is None else min(timeout, left)
        clamp = '' if (left is None or eff == timeout) else '，已被总预算收窄'
        print('\n=== %s%d/%d %s（%s，本步上限 %.1f 分钟%s） ==='
              % (head, i, len(phases), what, name, eff / 60, clamp), flush=True)
        rc, note = run_phase(name, keys, target=target, timeout=eff)
        print('  ← %s' % note, flush=True)

        ok_day, ok_min, lines = verdicts(target, minute=not day_only)
        for ln in lines:
            print('  %s' % ln.replace('\n', '\n  '), flush=True)
        if name == 'day' and not ok_day:
            # 日线这一步没把文件推到目标日，后面分钟线大概率也点不动 ——
            # 而且这一轮的结论已经定了，早退一步能给重来省 7 分钟。
            return False, False, rc, stopped
        if ok_day and (day_only or ok_min):
            return True, True, rc, stopped
    ok_day, ok_min, _ = verdicts(target, minute=not day_only)
    return ok_day, ok_min, rc, stopped


def frontier() -> dict:
    """下载前沿：日线抽样文件的**最新日期** + 下载子进程在不在。

    这两个数分开才有用：前沿在涨 = 还在推进（B 类，等）；前沿不动但 `TPyth*` 还在
    = 可能在换市场；两个都不动 = 真死了（A 类，重来）或要人来（C 类）。
    """
    v = readiness.Readiness().day_ready()
    dates = [p.last_date for p in v.probes if p.last_date]
    try:
        _syncing, who = locator.sync_running()
    except Exception:  # noqa: BLE001  枚举进程本身坏了不该把判定带崩
        _syncing, who = False, '枚举失败'
    return {'max_date': max(dates).isoformat() if dates else None,
            'sampled': len(v.probes), 'with_date': len(dates),
            'downloading': bool(_syncing), 'sync': who}


def still_moving(before: dict, after: dict) -> bool:
    """有没有"还在推进"的证据：前沿日期变新，或下载子进程还在。

    只认这两种可观测的推进 —— 不看"通达信说完成了没"，也不看退出码。
    """
    b, a = before.get('max_date'), after.get('max_date')
    if b and a and a > b:
        return True
    return bool(after.get('downloading'))


def main(argv: list[str] | None = None) -> int:
    """`argv` 只为测试/复用而存在；命令行不带就是 `sys.argv`（与 run_agent 同形）。"""
    ap = argparse.ArgumentParser(
        description='P0 · 通达信盘后数据下载（同步数据前的前置动作）')
    ap.add_argument('--date', help='目标交易日 YYYY-MM-DD，默认自动推算')
    ap.add_argument('--day-only', action='store_true',
                    help='只下日线，跳过分钟线那一遍（快得多）')
    ap.add_argument('--attempts', type=int, default=DEFAULT_ATTEMPTS,
                    help='失败重来几轮（每轮开头先杀干净通达信相关进程），'
                         '默认 %d' % DEFAULT_ATTEMPTS)
    ap.add_argument('--budget-sec', type=int, default=None,
                    help='本脚本的总预算（秒），默认 %d；每一步的超时取'
                         '「该步上限」与「剩余预算」的较小者，预算不够就不再开新步/新轮，'
                         '带着出口码回来（别让上级用 kill_tree 收我们）。'
                         '环境变量 TDX_PRELOAD_BUDGET_SEC 同义。' % BUDGET_SEC)
    ap.add_argument('--check', action='store_true',
                    help='只判定不动界面：报当前 vipdoc 到哪一天')
    args = ap.parse_args(argv)

    target = (dt.date.fromisoformat(args.date) if args.date
              else readiness.expected_trade_date())
    print('=== P0 通达信盘后下载 · 目标交易日 %s%s ==='
          % (target, '（只日线）' if args.day_only else ''), flush=True)

    ok_day, ok_min, lines = verdicts(target, minute=not args.day_only)
    for ln in lines:
        print('  %s' % ln.replace('\n', '\n  '), flush=True)
    alive = reset.snapshot()
    print('  当前通达信相关进程：%s'
          % ('、'.join(f'{n}({p})' for n, p in alive) or '无'), flush=True)

    if args.check:
        print('\n%s 日线%s；分钟线%s（--check 未动界面）'
              % ('DONE' if ok_day and ok_min else 'TODO',
                 '到位' if ok_day else '**没到 %s**' % target,
                 '不判' if args.day_only else ('到位' if ok_min else '没到位')))
        return 0 if (ok_day and ok_min) else 1
    if ok_day and ok_min:
        print('\nDONE 已经到 %s，不用动通达信。' % target)
        return 0

    rounds = max(1, args.attempts)
    budget = budget_sec(args.budget_sec)
    if budget < MIN_ROUND_SEC:
        # 不许"静悄悄换成默认值"：那等于给调用方一个看起来能配、实际不起作用的数。
        # 小到放不下轮的预算是真会发生的（上级只给 3 分钟），所以**明说再抬到能跑一轮**，
        # 抬完这一轮还是来不及，就会带着出口码回来而不是被 kill_tree。
        print('预算 %ds 连一轮都放不下（起一轮至少 %ds，要先登录再点两遍下载），'
              '按 %ds 用；只想要快就传 `--attempts 1`。'
              % (budget, MIN_ROUND_SEC, MIN_ROUND_SEC), flush=True)
        budget = MIN_ROUND_SEC
    deadline = time.time() + budget
    # 动手**之前**先记一次前沿：没有这个基线，事后就分不清"文件在涨但还没到目标日"
    # 和"根本没在动"——前者该等（出口码 4），后者才该重开或交人。
    f_before = frontier()
    print('起始前沿：日线抽样最新 %s（%d/%d 个文件有日期），下载子进程：%s'
          % (f_before['max_date'] or '—', f_before['with_date'], f_before['sampled'],
             f_before['sync'] or '不在'), flush=True)
    step_sum = sum(p[3] for p in PHASES
                   if not (args.day_only and p[0] == 'minute'))
    print('自限预算 %d 分钟（本轮各步上限相加 %.0f 分钟/轮 × %d 轮 = %.0f 分钟，'
          '取小者；上级给的更宽就用 `--budget-sec` 显式放宽）'
          % (budget // 60, step_sum / 60, rounds, step_sum * rounds / 60), flush=True)
    rc = 1
    stopped = ''
    prev_end: float | None = None
    for rnd in range(1, rounds + 1):
        if rnd > 1:
            left = deadline - time.time()
            if left < MIN_ROUND_SEC:
                stopped = ('第 %d 轮没起：预算只剩 %.0f 秒，不够"清进程再从第 1 步跑一遍"'
                           '（下限 %ds）' % (rnd, left, MIN_ROUND_SEC))
                print('\n%s —— 把等待交回链子按下一档重试，不在这里硬挤一轮。' % stopped,
                      flush=True)
                break
            if prev_end is not None:
                # 两轮之间至少隔 `ROUND_SPACING_SEC`。快速失败就连着 kill_all 重启客户端，
                # 既分不清是不是同一种坏，也真把"几十秒内把自己重试完"变成了现实 ——
                # 那样链的下一档（+25 分钟）永远等不到我们。
                gap = ROUND_SPACING_SEC - (time.time() - prev_end)
                if gap > 0:
                    print('\n上一轮结束才 %.0fs 前，先隔 %ds 再清进程重来'
                          % (time.time() - prev_end, gap), flush=True)
                    time.sleep(min(gap, max(0.0, left - MIN_ROUND_SEC)))
            print('\n=== 第 %d/%d 轮：先清掉通达信相关进程，再从第 1 步来过 ==='
                  % (rnd, rounds), flush=True)
            ok_kill, why = reset.kill_all(log=lambda m: print(m, flush=True))
            print('  %s' % why, flush=True)
            if not ok_kill:
                print('  进程没清干净，重来大概率还是同样的失败 —— 停在这里。')
                break
        ok_day, ok_min, rc, stopped = one_round(target, day_only=args.day_only,
                                                rnd=rnd, rounds=rounds,
                                                deadline=deadline)
        prev_end = time.time()
        if ok_day and (args.day_only or ok_min):
            break
        if rc == EXIT_HANDOFF:
            # C 类（子进程说"要人来"）：再起一轮只是把同一个失败再演一遍，
            # 而第 2 轮开头会把用户桌面上的客户端 kill_all 掉。上限 1 次，立刻出声。
            print('\n子进程判为**要人来接手**（出口码 2），不再自动起第 %d 轮 —— '
                  '看上面现场包（var\\stuck\\...）里的 screen.png。' % (rnd + 1), flush=True)
            break
        if rnd < rounds and not stopped:
            print('\n本轮没拿全（日线%s，分钟线%s），还有 %d 轮可试。'
                  % ('OK' if ok_day else '缺', 'OK' if ok_min else '缺',
                     rounds - rnd), flush=True)

    print()
    if ok_day and (args.day_only or ok_min):
        print('DONE vipdoc 已到 %s，可以往下跑同步/入库了'
              '（`python -m ingest.jobs.c10_catchup` 或整条 `python -m ingest.jobs.e1_fill_all`）。'
              % target)
        return 0
    if not ok_day:
        # 出口码 2 只在"子进程说需要接手"时给：把"GUI 定位失败"和"下载真失败"
        # 分开，前者要人看现场包，后者重跑就行。
        why_budget = ('（**是被自限预算收的**，不是下载本身坏了：要一次跑更久就传 '
                      '`--budget-sec`，前提是上级也给得更宽。%s）' % stopped
                      ) if stopped else ''
        if rc == EXIT_HANDOFF:
            print('HANDOFF 日线没拿到，而且 GUI 那侧需要人接手 —— '
                  '看上面输出里的现场包路径（var\\stuck\\...）里的 screen.png。')
            return 2
        # 三分类里的 B：有"还在推进"的证据就**不许**报成"要人来"或"死了"。
        # 实测形状：链 16:31 判停，而通达信 17:2x 才把日线落完 ——
        # 把晚到读成失败会让下游去重启客户端，正好打断一次本来会成功的下载。
        f_after = frontier()
        if still_moving(f_before, f_after):
            print('LATE 日线没到 %s，但**还在推进**：前沿 %s → %s，下载子进程 %s。'
                  '这是"晚到"，不是"没成" —— 请按重试节奏等，别重启客户端。%s'
                  % (target, f_before['max_date'] or '—', f_after['max_date'] or '—',
                     f_after['sync'] or '不在', why_budget))
            return 4
        print('FAIL 日线没到 %s（前沿 %s 且下载子进程不在，没有任何推进证据）。%s'
              '先手动跑一次 python -m app.tdx_client.run_agent --check 看卡在哪；'
              '实在等不了就让下游走网络兜底（`python -m ingest.jobs.c10_catchup --allow-web`），'
              '事后再 --overwrite 换回通达信口径。'
              % (target, f_after['max_date'] or '—', why_budget))
        return 1
    print('PARTIAL 日线已到 %s，分钟线没拿到 —— 日线链路可以继续，'
          '分钟线单独重跑：python -m app.tdx_client.run_agent '
          '--only s5_dialog --only s7_minute' % target)
    return 3


if __name__ == '__main__':
    raise SystemExit(main())
