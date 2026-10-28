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


出口码：

    0  日线和分钟线都到目标交易日（含"本来就到位、什么都没做"）
    1  日线没拿到 —— 后面的同步/入库不该继续
    2  需要人工/智能体接手（GUI 定位失败这类）；现场包路径在子进程输出里
    3  日线到位、只有分钟线没拿到（日常复盘吃日线，所以单列一档）

用法：
    python scripts\\ops\\p0_tdx_predownload.py                 # 日线 + 分钟线
    python scripts\\ops\\p0_tdx_predownload.py --day-only      # 只要日线（快）
    python scripts\\ops\\p0_tdx_predownload.py --attempts 3    # 最多重来 3 轮
    python scripts\\ops\\p0_tdx_predownload.py --date 2026-08-28
    python scripts\\ops\\p0_tdx_predownload.py --check         # 只判定，不动界面
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.tdx_client import readiness, reset  # noqa: E402

#: 心跳间隔（秒）。长时任务没有回显会被当成卡死然后被手动 kill。
HEARTBEAT = 60
#: 多久没有任何输出算卡死。分钟线那一遍状态栏每几秒就变一次，
#: 5 分钟不出声肯定不对了。s4 等自动同步是最沉默的一段，所以别调太小。
STALL_LIMIT = 600
#: 守护线程的检查周期。
GUARD_TICK = 5.0
DEFAULT_ATTEMPTS = 2

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
            if idle > STALL_LIMIT:
                verdict[0] = ('**卡死**：%.0fs 没有任何输出（上限 %ds），已杀掉子进程'
                              % (idle, STALL_LIMIT))
                p.kill()
                return
            if total > timeout:
                verdict[0] = ('**超时**：这一步跑了 %.0f 分钟（上限 %.0f 分钟），'
                              '已杀掉子进程' % (total / 60, timeout / 60))
                p.kill()
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


def one_round(target: dt.date, *, day_only: bool,
              rnd: int, rounds: int) -> tuple[bool, bool, int]:
    """
    跑一轮完整链路。返回 `(日线到位, 分钟线到位, 最后一步的出口码)`。

    **每一步之后都用文件判据看一次**，到位就不再往下点：日线已经是目标日时
    s6 那一步本来也会 precheck 跳过，但在这里就停下能少开一个子进程。
    """
    phases = [p for p in PHASES if not (day_only and p[0] == 'minute')]
    rc = 0
    for i, (name, what, keys, timeout) in enumerate(phases, 1):
        head = ('第 %d/%d 轮 · ' % (rnd, rounds)) if rounds > 1 else ''
        print('\n=== %s%d/%d %s（%s） ==='
              % (head, i, len(phases), what, name), flush=True)
        rc, note = run_phase(name, keys, target=target, timeout=timeout)
        print('  ← %s' % note, flush=True)

        ok_day, ok_min, lines = verdicts(target, minute=not day_only)
        for ln in lines:
            print('  %s' % ln.replace('\n', '\n  '), flush=True)
        if name == 'day' and not ok_day:
            # 日线这一步没把文件推到目标日，后面分钟线大概率也点不动 ——
            # 而且这一轮的结论已经定了，早退一步能给重来省 7 分钟。
            return False, False, rc
        if ok_day and (day_only or ok_min):
            return True, True, rc
    ok_day, ok_min, _ = verdicts(target, minute=not day_only)
    return ok_day, ok_min, rc


def main() -> int:
    ap = argparse.ArgumentParser(
        description='P0 · 通达信盘后数据下载（同步数据前的前置动作）')
    ap.add_argument('--date', help='目标交易日 YYYY-MM-DD，默认自动推算')
    ap.add_argument('--day-only', action='store_true',
                    help='只下日线，跳过分钟线那一遍（快得多）')
    ap.add_argument('--attempts', type=int, default=DEFAULT_ATTEMPTS,
                    help='失败重来几轮（每轮开头先杀干净通达信相关进程），'
                         '默认 %d' % DEFAULT_ATTEMPTS)
    ap.add_argument('--check', action='store_true',
                    help='只判定不动界面：报当前 vipdoc 到哪一天')
    args = ap.parse_args()

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
    rc = 1
    for rnd in range(1, rounds + 1):
        if rnd > 1:
            print('\n=== 第 %d/%d 轮：先清掉通达信相关进程，再从第 1 步来过 ==='
                  % (rnd, rounds), flush=True)
            ok_kill, why = reset.kill_all(log=lambda m: print(m, flush=True))
            print('  %s' % why, flush=True)
            if not ok_kill:
                print('  进程没清干净，重来大概率还是同样的失败 —— 停在这里。')
                break
        ok_day, ok_min, rc = one_round(target, day_only=args.day_only,
                                       rnd=rnd, rounds=rounds)
        if ok_day and (args.day_only or ok_min):
            break
        if rnd < rounds:
            print('\n本轮没拿全（日线%s，分钟线%s），还有 %d 轮可试。'
                  % ('OK' if ok_day else '缺', 'OK' if ok_min else '缺',
                     rounds - rnd), flush=True)

    print()
    if ok_day and (args.day_only or ok_min):
        print('DONE vipdoc 已到 %s，可以往下跑同步/入库了'
              '（scripts\\ingest\\c10_catchup.py 或 scripts\\ops\\e1_fill_all.py）。'
              % target)
        return 0
    if not ok_day:
        # 出口码 2 只在"子进程说需要接手"时给：把"GUI 定位失败"和"下载真失败"
        # 分开，前者要人看现场包，后者重跑就行。
        if rc == 2:
            print('HANDOFF 日线没拿到，而且 GUI 那侧需要人接手 —— '
                  '看上面输出里的现场包路径（var\\stuck\\...）里的 screen.png。')
            return 2
        print('FAIL 日线没到 %s。先手动跑一次 '
              'python -m app.tdx_client.run_agent --check 看卡在哪；'
              '实在等不了就让下游走网络兜底（c10_catchup --allow-web），'
              '事后再 --overwrite 换回通达信口径。' % target)
        return 1
    print('PARTIAL 日线已到 %s，分钟线没拿到 —— 日线链路可以继续，'
          '分钟线单独重跑：python -m app.tdx_client.run_agent '
          '--only s5_dialog --only s7_minute' % target)
    return 3


if __name__ == '__main__':
    raise SystemExit(main())
