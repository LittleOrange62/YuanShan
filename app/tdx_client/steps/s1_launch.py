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

2026-11-11 追加一条：**残影条目不算「跑着」**。`tasklist` 还列着但 0 线程的进程已经死了，
杀不掉也等不掉；旧判据把它当"在跑"，于是每轮都先关它、关不掉、判「旧进程关不掉」转人工，
一整天的日线一根都没落。现在判活走 `win32.split_live`，收进程按 pid 精确收
（`/IM` 群杀会把这一轮刚拉起的新实例一起收掉，当天实测过一次）。
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

        live, ghost = win32.split_live(EXE_NAME)
        if ghost:
            # 残影＝0 线程、进程已死但 tasklist 还列着。**不杀也不等它消失**：
            # taskkill 对它无能为力，等它就是白等 20s 然后判「旧进程关不掉」。
            # 它也不挡新实例（2026-11-11 23:50:09 实测：残影 45956 在场时新实例 17880 照样起来了）。
            ctx.log(f'  残影条目 {ghost}（进程已死、条目未回收）→ 不杀、不等，只记账')
        if live:
            ctx.log(f'  活着的实例 {live} 当前不可用（未登录/假死/要求强制重启）'
                    f'→ 按 pid 收掉再起')
            win32.kill_pids(live)
            # 等进程真的退出，别刚 kill 完就启动 —— 通达信会检测到已有实例
            # 然后什么都不做直接退出，表现是"启动了但没窗口"。
            # 判据只看**活着的**实例：残影永远等不掉，拿它当判据就是必超时。
            ok, why = self.wait_until(
                lambda: (not win32.live_pids(EXE_NAME), '进程仍在'),
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
            live, ghost = win32.split_live(EXE_NAME)
            if not live:
                return False, ('进程不在'
                               + (f'（只剩残影条目 {ghost}，它不算在跑）' if ghost else ''))
            # pid 取**真有窗口的那个**：按镜像名拿到的第一个可能是残影，
            # 后面每一步都拿它当句柄主人，就会一路报"控件没反应"。
            main = ctx.refresh_main()
            pid = main.pid if main is not None else live[0]
            ctx.bag['pid'] = pid
            if _login_box(pid) is not None:
                return True, f'登录框已出现（pid={pid}）'
            if main is not None:
                return True, f'主窗口已出现（pid={main.pid}）'
            return False, f'进程 {live} 在，但还没有窗口'

        return self.wait_until(check, ctx=ctx)


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
