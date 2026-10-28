"""
s8 · 收尾：关闭 TQ数据设置 并最小化通达信。

用户原话："再关闭'TQ数据设置'并最小化通达信软件"。

**明确不做的事：不杀通达信进程。**
下次跑的时候进程还在就能直接复用（见 s1 的 precheck），
省掉登录和自动同步那几分钟。杀掉纯属自找麻烦。

最小化有个细节：`D:\\new_tdx64\\T0002\\user.ini` 里如果
`Min2Tray=1`，最小化会缩到托盘 —— 这时候窗口是"存在但不可见"，
`IsWindowVisible` 返回假。所以判据用 `IsIconic` **或**"窗口不可见"，
两者取或，不然在 Min2Tray 开着的机器上会一直判失败。

**`cleanup()` 是给"链路中途失败"用的。**
状态机在某一步 handoff/失败之后会把后面所有步骤标成 aborted，
s8 自然也不会跑 —— 于是通达信被最大化摊在屏幕上没人收。
2026-08-28 s5 打不开菜单就是这样，最后要人手动去最小化。
所以 `run_agent` 的 `finally` 里无条件调一次 `cleanup()`：
它跟 s8 共用同一套动作，但不判定、不抛异常、不影响出口码。
"""

from __future__ import annotations

import time

from app.tdx_client import locator, win32
from app.tdx_client.machine import RunContext, Step, StepResult
from app.tdx_client.steps.s5_dialog import find_dialog


def close_dialog(pid: int | None = None) -> list[str]:
    """关掉 TQ数据设置。三级：WM_CLOSE → 系统菜单关闭 → 找按钮点。"""
    d = find_dialog(pid)
    if d is None:
        return ['对话框本来就没开']
    notes: list[str] = []
    win32.close_window(d.hwnd)
    time.sleep(0.5)
    if win32.user32.IsWindow(d.hwnd):
        # 有些对话框吃掉 WM_CLOSE，得走系统菜单的关闭命令，
        # 再不行就找"关闭"/"取消"按钮点一下。
        win32.close_window(d.hwnd, syscommand=True)
        time.sleep(0.5)
    if win32.user32.IsWindow(d.hwnd):
        for label in ('关闭', '取消', '退出'):
            hits = locator.find_child(d.hwnd, text=label,
                                      classes=locator.BUTTON_CLASSES)
            if hits:
                win32.bm_click(hits[0].hwnd)
                notes.append(f'点了 {label!r} 按钮')
                time.sleep(0.5)
                break
    notes.append('对话框已关' if not win32.user32.IsWindow(d.hwnd)
                 else '对话框仍在')
    return notes


def cleanup(pid: int | None = None) -> str:
    """
    尽力收尾：关对话框 + 最小化主窗口。**任何异常都自己吞掉。**

    这个函数会在链路失败后被调用，那时候界面处于什么状态都有可能
    （窗口没了、进程没了、对话框卡着）。收尾失败不该盖掉真正的失败原因，
    所以这里只返回一句能打进日志的说明，不抛、不改出口码。
    """
    notes: list[str] = []
    try:
        notes += close_dialog(pid)
        main = locator.main_window()
        if main is None:
            notes.append('主窗口枚举不到，跳过最小化')
        elif _minimized(main):
            notes.append('主窗口已是最小化')
        else:
            win32.minimize(main.hwnd)
            time.sleep(0.6)
            main = locator.main_window()
            notes.append('已最小化' if main is None or _minimized(main)
                         else f'最小化没生效（{main.rect}）')
    except Exception as e:
        notes.append(f'收尾时出错（忽略）：{e}')
    return '；'.join(notes)


class FinishStep(Step):

    key = 's8_finish'
    title = '关对话框并最小化'
    retry = 2
    verify_timeout = 20
    verify_interval = 1.0
    fatal = False

    def precheck(self, ctx: RunContext) -> tuple[bool, str]:
        pid = ctx.bag.get('pid')
        main = ctx.refresh_main()
        if find_dialog(pid) is None and main is not None and _minimized(main):
            return True, '对话框已关、主窗口已最小化'
        return False, ''

    def act(self, ctx: RunContext) -> StepResult:
        pid = ctx.bag.get('pid')
        steps = close_dialog(pid)

        main = ctx.refresh_main()
        if main is None:
            return StepResult.fail('找不到主窗口，没法最小化')
        if _minimized(main):
            steps.append('主窗口本来就是最小化的')
        else:
            win32.minimize(main.hwnd)
            steps.append('已发最小化')
        return StepResult.done('；'.join(steps))


    def verify(self, ctx: RunContext) -> tuple[bool, str]:
        pid = ctx.bag.get('pid')

        def check():
            d = find_dialog(pid)
            if d is not None:
                return False, f'对话框还在：{d.text!r}'
            main = locator.main_window()
            if main is None:
                # 最小化到托盘时按标题也可能找不到 —— 只要进程还活着就算过。
                if win32.pids_by_name('TdxW.exe'):
                    return True, '主窗口枚举不到但进程在（Min2Tray 缩到托盘了）'
                return False, '**通达信进程也没了** —— 收尾时不该杀进程'
            if _minimized(main):
                return True, '对话框已关、主窗口已最小化'
            return False, f'主窗口还没最小化（{main.rect}）'

        return self.wait_until(check)


def _minimized(w: win32.Win) -> bool:
    """
    最小化判据取"或"。

    `Min2Tray=1`（`T0002\\user.ini`）时通达信缩到托盘，窗口不可见但
    **不是 iconic**；没开 Min2Tray 时是 iconic 但仍"可见"。
    只判一个的话，总有一半机器上判错。
    """
    return win32.is_iconic(w.hwnd) or not win32.user32.IsWindowVisible(w.hwnd)
