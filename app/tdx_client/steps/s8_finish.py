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

import os
import time

from app.tdx_client import dl_dialog, locator, win32
from app.tdx_client.machine import RunContext, Step, StepResult
from app.tdx_client.steps.s5_dialog import find_dialog

#: 只点这几个**全文相等**的按钮标签。
#:
#: 10-06 18:3x 实测掉数据的那一条：原来这里是 `find_child(text='取消')`，
#: 而 `find_child` 是**子串匹配** —— 「盘后数据下载」窗口上有一个按钮就叫
#: **「取消下载」**（id=1，正在下的时候 `开始下载` 变成它）。于是 `close_dialog()`
#: 回了 `["点了 '取消' 按钮", '对话框仍在']`：它按的是"放弃这一轮下载"，
#: 通达信随即弹「您确定取消下载吗？」—— 用户 18:30 截图里那个框就是这么来的。
#: 关窗的按钮按 ctrl_id 找反而不稳（id=2 实测是**最小化**，不是关闭），
#: 所以这里按文字，但**必须精确匹配 + 黑名单双保险**。
CLOSE_LABELS = ('关闭', '取消', '退出')

#: 这些文字**永不点**（精确与包含都算）。它们不是"关窗"，是"放弃这一轮正在下的数据"。
NEVER_CLICK = ('取消下载', '停止下载', '终止下载', '放弃下载', '删除', '清空')

#: 「您确定取消下载吗？」那类决定框的正文特征统一住在 `dl_dialog.CONFIRM_MARKS`
#: （标题不可用 —— 18:5x 实测标题是 `'TdxW'`，正文 Static id=65535，按钮 id=1「确定」/
#: id=2「取消」）。看门狗和这里读同一份，别两处各写一套字。

#: 确认框上两个按钮的确切文字：点「确定」= 真的取消下载，点「取消」= 不取消、继续下。
CONFIRM_YES, CONFIRM_NO = '确定', '取消'


def confirm_boxes(pid: int | None = None) -> list[win32.Win]:
    """
    枚举"要不要放弃这一轮下载"的决定框。

    两个都不能省的识别细节（都来自 18:5x 实测）：
    ① **标题不可用** —— 标题是 `'TdxW'`，零信息，任何按标题写的规则都认不出它；
    ② 只认**正文（Static）**，不认按钮文字 —— 否则「盘后数据下载」那个窗口会因为
       它自己的按钮写着「取消下载」而被当成决定框（按钮文字不是内容）。
    """
    out: list[win32.Win] = []
    for w in win32.top_windows():
        if pid is not None and w.pid != pid:
            continue
        if not w.visible or w.cls != '#32770':
            continue
        body = [button_text(c) for c in win32.children(w.hwnd, max_depth=4)
                if c.cls.startswith('Static')]
        if any(m in t for m in dl_dialog.CONFIRM_MARKS for t in body):
            out.append(w)
    return out


def button_text(w: win32.Win) -> str:
    return (win32.window_text(w.hwnd) or '').strip()


def download_running(dlg_hwnd: int) -> tuple[bool, str]:
    """
    这个窗口**是不是还在下**。两条可观测的证据，任一条成立就算在下：

    1. id=1 那个按钮写的是「取消下载」（现场实测：正在下的时候才会这样）；
    2. 状态栏有 `正在下载…` / `正在转档…` 且还没出现 `下载完毕`。

    为什么要单独有这个函数而不是"关窗时顺手看看"：判错方向的代价不对称 ——
    还在下却去关窗 = 掉这一轮数据；下完了没关 = 下一轮叠两层窗，
    后者只是难看，前者是真丢东西。
    """
    ctl = dl_dialog.controls(dlg_hwnd)
    btn = ctl.get(dl_dialog.CTRL['start'])
    label = button_text(btn) if btn else ''
    if label in dl_dialog.CANCEL_LABELS:
        return True, f'id={dl_dialog.CTRL["start"]} 写着 {label!r}（正在下）'
    status = dl_dialog.status_text(dlg_hwnd)
    if dl_dialog.DONE_MARK in status:
        return False, f'状态栏已报 {dl_dialog.DONE_MARK}'
    if any(m in status for m in dl_dialog.BUSY_MARKS):
        return True, f'状态栏 {status!r}'
    if label and label != dl_dialog.START_LABEL:
        return True, f'id={dl_dialog.CTRL["start"]} 写的是 {label!r}，不是 {dl_dialog.START_LABEL!r} —— 认不出就别动'
    return False, f'没看到在下的证据（按钮 {label!r}、状态栏 {status!r}）'


def files_settled(target, *, period: int | None = None) -> tuple[bool, str]:
    """文件侧判据（`readiness`）。拿不到或没给 target 就当**没过**（保守：不关窗）。"""
    if target is None:
        return False, '没给目标交易日，文件侧不过'
    try:
        from app.tdx_client import readiness
        r = readiness.Readiness()
        v = (r.day_ready(target) if period is None
             else r.minute_ready(target, period=period))
    except Exception as exc:  # noqa: BLE001  判据读不出来就别关窗
        return False, f'文件侧判据读不出来：{exc}'
    return (v.ok, f'文件侧{"已到" if v.ok else "未到"} {target}（{v.note}）')


def answer_confirm(pid: int | None = None, *, allow_cancel: bool) -> list[str]:
    """
    如果挂着「您确定取消下载吗？」这类决定框，**按判据答**，绝不 WM_CLOSE 它。

    - `allow_cancel=True` → 点「确定」（这一轮已经判完成/判卡死，放弃它是对的）；
    - `False` → 点「取消」，意思是**不取消下载、继续等**（这才是还在下时该做的）。

    为什么不"顺手关掉"：模态框按 X 走的是它自己的默认分支，我们不知道是哪一个，
    而这个框的两个分支一个继续下载、一个放弃整轮 —— 拿默认分支赌运气就是上一条缺陷。
    """
    notes: list[str] = []
    for box in confirm_boxes(pid):
        want = CONFIRM_YES if allow_cancel else CONFIRM_NO
        hits = [c for c in win32.children(box.hwnd, max_depth=4)
                if c.visible and c.cls.startswith(locator.BUTTON_CLASSES)
                and button_text(c) == want]
        if not hits:
            # 找不到**确切**那个按钮就原样留着，交人 —— 不猜相邻按钮，也不 WM_CLOSE。
            notes.append(f'决定框（{box.text!r}）在，但找不到按钮 {want!r}，'
                         '**原样留着不动**')
            continue
        win32.bm_click(hits[0].hwnd)
        time.sleep(0.5)
        gone = not win32.user32.IsWindow(box.hwnd)
        notes.append(f'决定框点了 {want!r}（{"已消失" if gone else "仍在"}）'
                     + ('' if gone else ' —— **不盲关，交人**'))
    return notes


def close_dialog(pid: int | None = None, *, target=None,
                 force: bool = False) -> list[str]:
    """
    关掉 TQ数据设置 / 盘后数据下载。三级：WM_CLOSE → 系统菜单关闭 → 点**精确标签**按钮。

    `force=False` 时**先看还在不在下**（`download_running`），还在下且文件侧也没过
    就**不关窗**（只由 `cleanup()` 最小化主窗口）—— 关掉/点到「取消下载」等于放弃
    这一轮正在下的数据。`force=True` 只有一条路进来：`wait_done` 按"状态栏不动 +
    抽样覆盖不涨"判过卡住，那意味着这一轮已经死了，关它是止损。
    """
    d = find_dialog(pid)
    if d is None:
        return ['对话框本来就没开']
    if os.environ.get('PYTEST_CURRENT_TEST') and not os.environ.get('TDX_ALLOW_TEST_CLICK'):
        # 这里是**真的会发 BM_CLICK / WM_CLOSE 的地方**。pytest 里没打桩就走到这儿，
        # 动的就是用户桌面上那只真窗口（10-06 已经因为"假 subprocess 不再被调用"
        # 把通达信起起来过一次，见 memory）。要测这条路径：monkeypatch `win32` 的
        # 那几个函数，并 `setenv('TDX_ALLOW_TEST_CLICK','1')`。
        return ['PYTEST 硬挡：close_dialog 没被 monkeypatch，不在用户桌面上点窗']
    notes: list[str] = []
    busy, why_busy = download_running(d.hwnd)
    settled, why_settled = files_settled(target)
    if busy and not force:
        # 故意不关：话要说成"保留"，不能说成"关不掉"，否则 `tdx_close_verify`
        # 会把一次正确的保守判断记成收尾失败。
        # **文件侧过了也不关**：日线到位不代表这一遍完了 —— 现场就是分钟线还在下
        # （9560/9604）而日线早到了，这时候关窗掉的是分钟线那一整轮。
        return [f'下载还在跑，**故意不关窗**：{why_busy}；{why_settled}']
    # `allow_cancel`：只有"没在下"或"已经判过死"才允许答"确定"（放弃这一轮）。
    allow_cancel = force or not busy
    notes += answer_confirm(pid, allow_cancel=allow_cancel)

    win32.close_window(d.hwnd)
    time.sleep(0.5)
    if win32.user32.IsWindow(d.hwnd):
        # 有些对话框吃掉 WM_CLOSE，得走系统菜单的关闭命令，
        # 再不行才找按钮 —— 而且只找**文字完全等于**这几个的。
        win32.close_window(d.hwnd, syscommand=True)
        time.sleep(0.5)
    if win32.user32.IsWindow(d.hwnd):
        for label in CLOSE_LABELS:
            hits = [c for c in locator.find_child(d.hwnd, text=label,
                                                  classes=locator.BUTTON_CLASSES,
                                                  exact=True)
                    if not any(bad in button_text(c) for bad in NEVER_CLICK)]
            if hits:
                win32.bm_click(hits[0].hwnd)
                notes.append(f'点了 {label!r} 按钮')
                time.sleep(0.5)
                break
        else:
            notes.append('没有可精确匹配的安全关闭按钮，**不强点**')
    # 点完可能又冒出一次确认框：同样按判据答，不放过。
    notes += answer_confirm(pid, allow_cancel=allow_cancel)
    if win32.user32.IsWindow(d.hwnd):
        notes.append('对话框仍在')
    else:
        notes.append('对话框已关')
    return notes


def cleanup(pid: int | None = None, *, target=None,
            force: bool = False) -> str:
    """
    尽力收尾：关对话框 + 最小化主窗口。**任何异常都自己吞掉。**

    这个函数会在链路失败后被调用，那时候界面处于什么状态都有可能
    （窗口没了、进程没了、对话框卡着）。收尾失败不该盖掉真正的失败原因，
    所以这里只返回一句能打进日志的说明，不抛、不改出口码。

    ⚠️ 但"吞异常"不等于"不出声"（2026-10-06，用户点名"下载完之后关闭这个窗口"）：
    吞掉之后必须留一份**页面和体检看得见**的 not-ok 记录。`TQ数据设置` 没关掉不是
    洁癖问题 —— 下一轮 s5 再点就是两层窗口，日志上看着像"源坏了"，其实是上一层没关。
    只 print 进 `.local/startup.log` 等于没发生。

    `target` 是给 `close_dialog` 的文件侧判据用的：**没它就只能靠状态栏认，
    认不出来时保守不关窗**（宁可在场面上难看一点，也不放弃正在下的数据）。
    """
    notes: list[str] = []
    try:
        notes += close_dialog(pid, target=target, force=force)
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
    out = '；'.join(notes)
    _journal(out)
    return out


#: 收尾没做到位的几种形状（`close_dialog` 三级手段全走完仍在 / 最小化没生效 / 自己抛了）。
#: 注意**没有**"故意不关窗"那一条 —— 那是正确的保守判断，记成失败会把真失败淹掉。
_NOT_OK_MARKS = ('对话框仍在', '最小化没生效', '收尾时出错')


def journal_stall(reason: str) -> None:
    """判"卡住"（状态栏不动 **且** 抽样覆盖不涨）之后必须留一条 not-ok。

    这是用户点名那条"后续没动静了你就结束了吗"的落点：结束可以，但**出声**，
    而且键和关窗复验同一个（`tdx_close_verify`），页面与体检只看这一处。
    """
    _journal(f'对话框仍在（下载卡住，已按判据收口）：{reason}')


def reap_stalled(pid: int | None = None, *, reason: str,
                 target=None) -> str:
    """
    唯一一条 `force=True` 的路：等待方按"状态栏 `STATUS_STALL_SEC` 没变 **且** 抽样覆盖
    隔 20 秒一个字节都没动"判过这一轮已经死了，才允许关窗止损。

    关窗可能冒出「您确定取消下载吗？」—— 这时 `allow_cancel` 为真（点确定放弃这一轮），
    与"还在下就只点取消"是同一条判据的两面。
    """
    notes = close_dialog(pid, target=target, force=True)
    journal_stall(reason)
    return '；'.join(notes)



def _journal(notes: str) -> None:
    """只在**没做干净**时往 `jobs` 状态记一条 not-ok（键 `tdx_close_verify`）。

    干净时不记：每轮都写一条会把"最后一次没关掉"淹掉，而要看的就是那一次。
    归因文字放 `reason` 键 —— `jobs.record()` 只认这个键名（系统配置的约定）。
    记账自己坏了不许再带崩收尾：这里是 `finally` 位置，抛出去就盖掉了真失败原因。
    """
    bad = [m for m in _NOT_OK_MARKS if m in notes]
    if not bad:
        return
    try:
        from duanxian import jobs
        jobs.record('tdx_close_verify', ok=False,
                    detail={'reason': notes[:300], 'marks': bad})
    except Exception:  # noqa: BLE001
        pass


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
        d = find_dialog(pid)
        if d is None and main is not None and _minimized(main):
            return True, '对话框已关、主窗口已最小化'
        if d is not None:
            busy, why = download_running(d.hwnd)
            if busy:
                # 还在下 → 这一步本来就不该关窗（关掉等于放弃这一轮），
                # 但也不能"跳过"，因为最小化主窗口还是要做的。
                ctx.bag['s8_keep_open'] = why
        return False, ''

    def act(self, ctx: RunContext) -> StepResult:
        pid = ctx.bag.get('pid')
        steps = close_dialog(pid, target=ctx.target)

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
                busy, why = download_running(d.hwnd)
                if busy or ctx.bag.get('s8_keep_open'):
                    # **故意留着** 不是失败：还在下就去关窗，关的是这一轮的数据。
                    # 上一版的形状是"必须关掉"，于是 retry 到第二次就有人去点按钮，
                    # 而那个按钮当时正写着「取消下载」—— 见 close_dialog 的说明。
                    return True, f'下载还在跑，s8 故意不关窗（{why}）；主窗口已最小化即可'
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

        return self.wait_until(check, ctx=ctx)


def _minimized(w: win32.Win) -> bool:
    """
    最小化判据取"或"。

    `Min2Tray=1`（`T0002\\user.ini`）时通达信缩到托盘，窗口不可见但
    **不是 iconic**；没开 Min2Tray 时是 iconic 但仍"可见"。
    只判一个的话，总有一半机器上判错。
    """
    return win32.is_iconic(w.hwnd) or not win32.user32.IsWindowVisible(w.hwnd)
