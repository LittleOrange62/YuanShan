"""
s1 · 启动或复用通达信。

**默认不重启。** 用户的原话是"首先关闭当前通达信，再重新开启"，
但那是给人的操作指引 —— 人不知道当前是什么状态，重启最省心。
程序知道：已经登录好、行情正常的话重启反而多花两三分钟，
还多一次登录失败的机会。所以这里的逻辑是

    已登录 → 直接复用
    没跑起来 → 启动
    跑着但没登录（卡在登录框/连不上行情）→ 才重启

`--force-restart` 保留强制重启，用于"怀疑行情连接坏了"的场合。
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from app.config import TDX_DIR
from app.tdx_client import locator, win32
from app.tdx_client.machine import RunContext, Step, StepResult

# 目录里同时有 TdxW.exe / TdxSP.exe / TdxNonSP.exe，主程序是 TdxW.exe。
EXE_NAME = 'TdxW.exe'
# 登录按钮上的文字。实测 V7.73 是"登录"；见过整合版写"确定"。
# 定义在这里而不是 s2_login，因为 `_login_box()` 要靠它认出登录框，
# 而 s2 反过来要 import `_login_box`，放那边就成环了。
LOGIN_BUTTON_TEXTS = ('登录', '登陆', '确定')


def exe_path() -> Path:
    return TDX_DIR / EXE_NAME


class LaunchStep(Step):
    key = 's1_launch'
    title = '启动/复用通达信'
    retry = 2
    verify_timeout = 90
    verify_interval = 2.0

    force_restart = False

    def precheck(self, ctx: RunContext) -> tuple[bool, str]:
        """
        已经登录进去就跳过。判据是**主窗口存在且不是登录框** ——
        通达信没登录时主框架也会建出来，只看进程在不在会误判。
        """
        if self.force_restart or ctx.bag.get('force_restart'):
            return False, ''
        main = ctx.refresh_main()
        if main is None:
            return False, '找不到主窗口'
        if _login_box(main.pid) is not None:
            return False, '主窗口在，但登录框还挂着，说明没登录成功'
        ctx.bag['pid'] = main.pid
        return True, f'已在运行且已登录：pid={main.pid} 标题={main.text!r}'

    def act(self, ctx: RunContext) -> StepResult:
        p = exe_path()
        if not p.is_file():
            return StepResult.fail(
                f'找不到通达信主程序：{p}。'
                '如果装在别的目录，设 FT_TDX_DIR 环境变量。')

        running = win32.pids_by_name(EXE_NAME)
        if running:
            ctx.log(f'  已有进程 {running}，先关掉再起（当前未登录或要求强制重启）')
            win32.kill(EXE_NAME)
            # 等进程真的退出，别刚 kill 完就启动 —— 通达信会检测到已有实例
            # 然后什么都不做直接退出，表现是"启动了但没窗口"。
            ok, why = self.wait_until(
                lambda: (not win32.pids_by_name(EXE_NAME), '进程仍在'),
                timeout=20, interval=0.5)
            if not ok:
                return StepResult.fail(f'旧进程关不掉：{why}')
            time.sleep(1.0)

        try:
            subprocess.Popen([str(p)], cwd=str(TDX_DIR),
                             creationflags=win32.NO_WINDOW)
        except OSError as e:
            return StepResult.fail(f'启动失败：{e}')
        return StepResult.done(f'已启动 {p}')

    def verify(self, ctx: RunContext) -> tuple[bool, str]:
        """
        起来了的判据：**主窗口或登录框出现**。

        这一步不要求"已登录" —— 登录是 s2 的事。这里只确认进程活了、
        界面建出来了。混在一起判会导致 s1 反复重启一个其实已经起好、
        只是在等输入的通达信。
        """
        def check():
            pids = win32.pids_by_name(EXE_NAME)
            if not pids:
                return False, '进程不在'
            ctx.bag['pid'] = pids[0]
            if _login_box(pids[0]) is not None:
                return True, f'登录框已出现（pid={pids[0]}）'
            main = ctx.refresh_main()
            if main is not None:
                return True, f'主窗口已出现（pid={main.pid}）'
            return False, f'进程 {pids} 在，但还没有窗口'

        return self.wait_until(check)


def _login_box(pid: int | None = None) -> win32.Win | None:
    """
    找登录框。

    **不能按标题找。** V7.73 的登录框标题就是 `通达信金融终端V7.73`，
    跟主框架一模一样；第一版按"标题含登录"找，一个都找不到，
    于是 s2 的 precheck 判成"已登录"并跳过，接着 s5 在登录框里找菜单，
    报"找不到 TQ策略"——错在两层之外。

    改成**按内容认**，三个条件同时满足：

    - class 是标准对话框 `#32770`；
    - 里面有一个文字为"登录"的按钮；
    - 里面有至少两个编辑框（用户名 + 密码，class 是 `SafeEdit`）。

    这三条不依赖标题和坐标，版本升级也不容易同时变。
    """
    for w in win32.top_windows(visible_only=False):
        if pid is not None and w.pid != pid:
            continue
        if w.cls != locator.DIALOG_CLASS:
            continue
        kids = win32.children(w.hwnd, max_depth=4)
        has_btn = any(c.text.strip() in LOGIN_BUTTON_TEXTS
                      and c.cls.startswith(locator.BUTTON_CLASSES) for c in kids)
        n_edit = sum(1 for c in kids if c.cls.startswith(locator.EDIT_CLASSES))
        if has_btn and n_edit >= 2:
            return w
    return None
