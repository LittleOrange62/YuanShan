"""
步骤状态机。

**设计的出发点是"一定会卡住"。** 通达信的界面自动化不可能做到 100%，
版本升级、弹窗形态变化、下载服务器抽风，任何一样都能让某一步失败。
所以这里的目标不是"永不失败"，而是：

1. 失败必须**能被发现**（每步 `verify()` 是硬要求，不许只有 `act()`）；
2. 失败时必须**留下足够的现场**，让人或智能体接手时不用从零开始查
   —— 这就是 `StuckContext`：窗口树、UIA dump、截图、就绪报告、每一级
   定位的尝试记录，全部落到 `var/stuck/<时间戳>/`；
3. 已经做完的步骤**不重做**（`precheck()` 返回 done 就跳过），
   这样人工介入处理掉卡住的那一步之后，重跑能从断点继续。

第 2 点是用户那套"交给智能体做"的流程能跑起来的前提：
脚本负责把确定的部分做掉、把不确定的部分连同现场一起交出来，
智能体拿着现场去做判断，而不是从"打开通达信"重新摸一遍。
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from app.config import VAR_DIR
from app.tdx_client import locator, ps, win32

STUCK_DIR = VAR_DIR / 'stuck'


@dataclass
class StepResult:
    ok: bool
    message: str = ''
    detail: dict = field(default_factory=dict)
    skipped: bool = False
    # 需要人/智能体接手时置 True。跟 ok=False 的区别是：
    # 这个不算"程序错了"，是"程序知道自己该让位了"。
    handoff: bool = False

    @classmethod
    def done(cls, message: str = '', **detail) -> StepResult:
        return cls(True, message, detail)

    @classmethod
    def skip(cls, message: str = '', **detail) -> StepResult:
        return cls(True, message, detail, skipped=True)

    @classmethod
    def fail(cls, message: str, **detail) -> StepResult:
        return cls(False, message, detail)

    @classmethod
    def ask_human(cls, message: str, **detail) -> StepResult:
        return cls(False, message, detail, handoff=True)


class Step:
    """
    一个步骤。子类必须实现 `act()` 和 `verify()`，`precheck()` 可选。

    三个方法的分工要卡死，不许混：

    - `precheck()` → `(done, why)`：**这一步是不是已经做完了**。
      重跑时靠它跳过。它不许有副作用。
    - `act()` → `StepResult`：真正动手。
    - `verify()` → `(ok, why)`：**从数据/系统状态确认结果**。
      不许在这里 `sleep` 然后返回 True。

    `act()` 成功但 `verify()` 失败是最常见的情况（点了按钮但没生效），
    状态机会按 `retry` 重试整个 act+verify，而不是只重试 verify。
    """

    key: str = ''
    title: str = ''
    retry: int = 1
    # verify 的等待上限（秒）。下载类步骤要给足，点击类步骤给几秒就够。
    verify_timeout: float = 30
    verify_interval: float = 1.0
    # 这一步失败要不要中止后面的步骤。分钟线下载失败不该拦住日线入库。
    fatal: bool = True

    def precheck(self, ctx: RunContext) -> tuple[bool, str]:
        return False, ''

    def act(self, ctx: RunContext) -> StepResult:
        raise NotImplementedError

    def verify(self, ctx: RunContext) -> tuple[bool, str]:
        raise NotImplementedError

    # -- 给子类用的小工具 --
    #: 长等待期间每隔多少秒打一行进度。父进程（p0 的心跳/卡死判定）只看 stdout，
    #: 一声不吭地等 10 分钟会被当成卡死杀掉 —— 2026-09-27 s4 就是这么白等满 600s 的。
    WAIT_LOG_EVERY = 30.0

    def wait_until(self, fn, *, timeout: float | None = None,
                   interval: float | None = None, ctx=None) -> tuple[bool, str]:
        """
        轮询 `fn() -> (ok, why)` 直到成立或超时。

        **这是这个包里唯一允许的等待形式。** 裸 `time.sleep(600)` 不许出现：
        它既浪费时间又会在真失败时给出假成功。
        """
        timeout = self.verify_timeout if timeout is None else timeout
        interval = self.verify_interval if interval is None else interval
        started = time.monotonic()
        deadline = started + timeout
        next_note = started + self.WAIT_LOG_EVERY
        why = ''
        while True:
            ok, why = fn()
            now = time.monotonic()
            if ok:
                return True, why
            if now >= deadline:
                return False, f'等待 {timeout:.0f}s 超时；最后一次检查：{why}'
            if ctx is not None and now >= next_note:
                ctx.log('  … 还在等（%d/%ds）：%s' % (int(now - started), int(timeout), why))
                next_note = now + self.WAIT_LOG_EVERY
            time.sleep(interval)


@dataclass
class RunContext:
    """
    一次运行的共享状态。

    步骤之间**只通过这里传东西**，不许用模块级全局变量 ——
    否则重跑、并发两个交易日就会互相踩。
    """

    target: dt.date
    readiness: object                     # Readiness 实例
    dry_run: bool = False
    task_id: int | None = None
    main: win32.Win | None = None
    creds: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    bag: dict = field(default_factory=dict)

    def log(self, msg: str) -> None:
        stamp = dt.datetime.now().strftime('%H:%M:%S')
        line = f'[{stamp}] {msg}'
        self.notes.append(line)
        print(line, flush=True)

    def refresh_main(self, *, timeout: float = 0) -> win32.Win | None:
        self.main = locator.main_window(timeout=timeout)
        return self.main


@dataclass
class StepRecord:
    key: str
    title: str
    state: str            # skipped / ok / failed / handoff / aborted
    message: str
    seconds: float
    attempts: int = 1
    detail: dict = field(default_factory=dict)


class Machine:
    """
    按顺序跑一串 Step。

    失败处理：
    - `fatal=True` 的步骤失败 → 停下，后面的标 aborted；
    - `fatal=False` 的失败 → 记下来继续；
    - `handoff=True` → 一定停下，并且**出口码跟普通失败区分开**，
      让调用方（页面 / 智能体）知道这是"需要你来看一眼"而不是"程序崩了"。
    """

    def __init__(self, steps: list[Step], *, capture_on_fail: bool = True):
        self.steps = steps
        self.capture_on_fail = capture_on_fail
        self.records: list[StepRecord] = []

    def run(self, ctx: RunContext, *, only: list[str] | None = None,
            skip: list[str] | None = None) -> tuple[bool, list[StepRecord]]:
        aborted = False
        handoff = False
        for step in self.steps:
            if aborted:
                self.records.append(StepRecord(step.key, step.title, 'aborted',
                                               '前一步失败，未执行', 0.0))
                continue
            if only and step.key not in only:
                self.records.append(StepRecord(step.key, step.title, 'skipped',
                                               '不在 --only 里', 0.0))
                continue
            if skip and step.key in skip:
                self.records.append(StepRecord(step.key, step.title, 'skipped',
                                               '在 --skip 里', 0.0))
                continue

            rec = self._run_one(step, ctx)
            self.records.append(rec)
            if rec.state == 'handoff':
                handoff = True
                aborted = True
            elif rec.state == 'failed' and step.fatal:
                aborted = True
        ok = all(r.state in ('ok', 'skipped') for r in self.records)
        if handoff:
            ok = False
        return ok, self.records

    def _run_one(self, step: Step, ctx: RunContext) -> StepRecord:
        t0 = time.monotonic()
        ctx.log(f'——— {step.key} · {step.title}')
        try:
            done, why = step.precheck(ctx)
        except Exception as e:
            done, why = False, f'precheck 抛异常（当作未完成继续）：{e}'
        if done:
            ctx.log(f'  已完成，跳过：{why}')
            return StepRecord(step.key, step.title, 'skipped', why,
                              time.monotonic() - t0)

        last = StepResult.fail('未执行')
        for attempt in range(1, step.retry + 1):
            if attempt > 1:
                ctx.log(f'  第 {attempt}/{step.retry} 次重试')
            if ctx.dry_run:
                ctx.log('  dry-run：只做 precheck 和 verify，不动界面')
                ok, why = step.verify(ctx)
                state = 'ok' if ok else 'failed'
                return StepRecord(step.key, step.title, state,
                                  f'dry-run verify：{why}',
                                  time.monotonic() - t0, attempt)
            try:
                last = step.act(ctx)
            except locator.LocateError as e:
                last = StepResult.ask_human(f'定位失败，需要人工/智能体接手：\n{e}')
            except Exception as e:
                last = StepResult.fail(f'{type(e).__name__}: {e}',
                                       traceback=traceback.format_exc())
            if last.skipped:
                ctx.log(f'  跳过：{last.message}')
                return StepRecord(step.key, step.title, 'skipped', last.message,
                                  time.monotonic() - t0, attempt, last.detail)
            if last.handoff:
                break
            if not last.ok:
                ctx.log(f'  act 失败：{last.message}')
                continue

            ctx.log(f'  act：{last.message}')
            try:
                ok, why = step.verify(ctx)
            except Exception as e:
                ok, why = False, f'verify 抛异常：{e}'
            if ok:
                ctx.log(f'  verify 通过：{why}')
                return StepRecord(step.key, step.title, 'ok', why,
                                  time.monotonic() - t0, attempt, last.detail)
            ctx.log(f'  **verify 没过**：{why}')
            last = StepResult.fail(f'act 成功但 verify 没过：{why}', **last.detail)

        state = 'handoff' if last.handoff else 'failed'
        if self.capture_on_fail:
            d = capture_stuck(ctx, step, last)
            last.detail['stuck_dir'] = str(d)
            ctx.log(f'  现场已保存到 {d}')
        return StepRecord(step.key, step.title, state, last.message,
                          time.monotonic() - t0, step.retry, last.detail)

    def summary(self) -> str:
        w = max((len(r.title) for r in self.records), default=8)
        lines = []
        for r in self.records:
            mark = {'ok': '√', 'skipped': '-', 'failed': '×',
                    'handoff': '!', 'aborted': '·'}.get(r.state, '?')
            lines.append(f'  {mark} {r.title:<{w}}  {r.seconds:6.1f}s  {r.message}')
        return '\n'.join(lines)


def capture_stuck(ctx: RunContext, step: Step, result: StepResult) -> Path:
    """
    把卡住那一刻的现场全都存下来。

    **这是"智能体接手"的交接包。** 里面每一项都是接手时会问的问题：

    - `windows.txt`：有哪些窗口？是不是有个没预料到的弹窗挡着？
    - `tree_*.txt`：目标对话框里到底有哪些控件、叫什么、在哪？
    - `uia_*.json`：Win32 看不到的自绘控件（菜单项）叫什么？
    - `screen.png`：肉眼看一下就知道卡在哪的情况占一半以上。
    - `readiness.txt`：数据到底新到哪一天了 —— 决定"要不要重来"。
    - `step.json`：这一步试了哪几级定位、每一级为什么没成。

    存这些东西要花几秒，但只在出事时走这条路，值得。
    """
    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    d = STUCK_DIR / f'{stamp}_{step.key}'
    d.mkdir(parents=True, exist_ok=True)

    # **只 dump 通达信自己的窗口。** 第一版按"面积够大且有标题"挑，
    # 结果把微信、飞书、浏览器的控件树也写进了现场包 —— 既慢（每个窗口
    # 一次 UIA dump 就是几百毫秒起）又是隐私问题：现场包是给人和智能体翻的，
    # 别人的聊天窗口标题不该出现在里面。
    tdx_pids = set(win32.pids_by_name('TdxW.exe'))
    main = ctx.main or locator.main_window()
    if main:
        tdx_pids.add(main.pid)

    # windows.txt 同理：通达信的窗口写全（标题+控件 ID），
    # 别人的只记 class 和位置 —— "是不是被别的程序盖住了"靠位置就能判断，
    # 不需要知道人家窗口叫什么。
    try:
        lines = []
        for w in win32.top_windows(visible_only=False):
            if w.pid in tdx_pids:
                lines.append(f'[TDX] {w}')
            elif w.visible and w.w > 100 and w.h > 50:
                lines.append(f'[其它进程 pid={w.pid}] {w.cls} {w.rect}')
        if not tdx_pids:
            lines.insert(0, '**通达信进程不存在** —— 可能已退出或从未启动\n')
        (d / 'windows.txt').write_text('\n'.join(lines), encoding='utf-8')
    except Exception as e:
        (d / 'windows.txt').write_text(f'枚举失败：{e}', encoding='utf-8')

    targets: list[win32.Win] = []
    if main:
        targets.append(main)
    for w in win32.top_windows():
        if main and w.hwnd == main.hwnd:
            continue
        if w.pid not in tdx_pids:
            continue
        if w.w > 100 and w.h > 50:
            targets.append(w)
    for i, w in enumerate(targets[:4]):
        safe = ''.join(c for c in w.text if c.isalnum() or c in '（）()_-')[:40]
        try:
            tree = win32.children(w.hwnd, max_depth=8)
            (d / f'tree_{i}_{safe}.txt').write_text(
                f'{w}\n' + '\n'.join(str(c) for c in tree), encoding='utf-8')
        except Exception as e:
            (d / f'tree_{i}_{safe}.txt').write_text(f'失败：{e}', encoding='utf-8')
        try:
            nodes, err = ps.dump_uia(w.text[:20])
            (d / f'uia_{i}_{safe}.json').write_text(
                json.dumps(nodes or {'error': err}, ensure_ascii=False, indent=2),
                encoding='utf-8')
        except Exception as e:
            (d / f'uia_{i}_{safe}.json').write_text(
                json.dumps({'error': str(e)}, ensure_ascii=False), encoding='utf-8')

    with contextlib.suppress(Exception):
        win32.screenshot(d / 'screen.png')

    try:
        r = ctx.readiness
        parts = [r.index_ready(ctx.target).report(),      # type: ignore[attr-defined]
                 r.day_ready(ctx.target).report(),        # type: ignore[attr-defined]
                 r.minute_ready(ctx.target).report(),     # type: ignore[attr-defined]
                 r.db_ready(ctx.target).report()]         # type: ignore[attr-defined]
        (d / 'readiness.txt').write_text('\n\n'.join(parts), encoding='utf-8')
    except Exception as e:
        (d / 'readiness.txt').write_text(f'判定失败：{e}', encoding='utf-8')

    payload = {
        'step': step.key, 'title': step.title, 'target': str(ctx.target),
        'message': result.message, 'handoff': result.handoff,
        'detail': {k: str(v) for k, v in result.detail.items()},
        'notes': ctx.notes[-80:],
        'next': ('看 screen.png 和 tree_*.txt 判断当前实际停在哪一步，'
                 '然后用 --only 只重跑那一步；'
                 '如果是控件名/位置变了，改 app/tdx_client/steps/ 里对应的定位参数。'),
    }
    (d / 'step.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return d
