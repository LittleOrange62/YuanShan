"""
纯 ctypes 的 Win32 窗口/控件原语。

**为什么不用 pywinauto。** `pyproject.toml` 里没有它，也不打算加：
pywinauto 会拖进 comtypes + pywin32 一大串二进制依赖，而这里真正需要的
只有十几个 user32 导出函数。UIA 那部分走 PowerShell 子进程（见 locator.py），
比在 Python 里搭 COM 桥省事得多。

**为什么优先 `PostMessage`/`SendMessage` 而不是模拟鼠标。**
`BM_CLICK` 不要求窗口在前台、不要求鼠标指到坐标上，所以通达信被别的程序
盖住、或者用户正在别的窗口上打字，点击照样生效。模拟鼠标点坐标是最后的兜底，
一旦窗口被盖住就会点到别人身上 —— 那比失败更糟。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

# ---- 消息常量 -----------------------------------------------------------
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_SYSCOMMAND = 0x0112
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
BM_CLICK = 0x00F5
BM_GETCHECK = 0x00F0
BM_SETCHECK = 0x00F1
BST_UNCHECKED, BST_CHECKED, BST_INDETERMINATE = 0, 1, 2

SC_CLOSE = 0xF060
SC_MINIMIZE = 0xF020
SC_RESTORE = 0xF120

SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_MAXIMIZE = 3
SW_MINIMIZE = 6
SW_RESTORE = 9

SC_MAXIMIZE = 0xF030

TCM_GETITEMCOUNT = 0x1304
TCM_SETCURSEL = 0x130C
TCM_GETCURSEL = 0x130B

#: `BM_CLICK` 之后勾选状态不是立刻可读的，`ensure_checked` 要轮询这么多次。
#: 实测「盘后数据下载」的分钟线两个框要 100ms 量级才反映出来。
CHECK_POLL = 6
CHECK_INTERVAL = 0.15


# 键盘消息。**在拿不到输入桌面的环境里，这是唯一能"打字"的办法** ——
# SendInput/SendKeys 走的是真实输入队列，被拒绝访问时一点反应都没有。
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_CLEAR = 0x0303
EM_SETSEL = 0x00B1
EM_REPLACESEL = 0x00C2

VK_BACK = 0x08
VK_END = 0x23
VK_HOME = 0x24
VK_DELETE = 0x2E
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B

VK_MENU = 0x12          # ALT
VK_CONTROL = 0x11
VK_A = 0x41

# 键盘消息的 lParam。**不能一直传 0。**
# 实测 2026-08-28：往 SafeEdit 里投 `WM_CHAR(数字)` 是生效的（账号确实被打
# 进去了），但同样方式投的 `VK_BACK` 一个都没删掉 —— 于是新账号追加在旧账号
# 后面，字段变成 `1896996969618969969696`，通达信报"用户名或密码错误"。
# 差别在 lParam：自绘控件对可见字符只看 wParam，处理编辑类按键时却会读
# lParam 里的重复次数与扫描码，重复次数为 0 的按键被当成无效直接丢掉。
# 所以这里按真实键盘的样子构造 lParam。
SCAN = {VK_BACK: 0x0E, VK_DELETE: 0x53, VK_END: 0x4F, VK_HOME: 0x47}
KF_EXTENDED = 0x0100    # 小键盘外的方向/编辑键要带扩展位
KF_UP = 0xC000          # keyup 的 transition + previous-state


def key_lparam(vk: int, *, up: bool = False) -> int:
    """按真实键盘的样子构造键盘消息的 lParam（重复次数 1 + 扫描码 + 标志位）。"""
    scan = SCAN.get(vk, 0)
    flags = KF_EXTENDED if vk in (VK_DELETE, VK_END, VK_HOME) else 0
    if up:
        flags |= KF_UP
    return (flags << 16) | (scan << 16) | 1


GA_ROOT = 2

SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
SPIF_SENDCHANGE = 0x0002

# SendInput：真实输入队列。**SafeEdit 只认这条路** ——
# 通达信的安全输入框在键盘驱动/钩子层取值，`PostMessage(WM_CHAR)`
# 递到窗口过程时它根本不看，所以投递成功也等于没写进去
# （实测：账号口令都"投递成功"，点登录后登录框一动不动）。
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


# SendMessageTimeout 的 flags：目标线程卡死时不要把我们也拖死。
SMTO_ABORTIFHUNG = 0x0002
SMTO_NORMAL = 0x0000
DEFAULT_TIMEOUT_MS = 3000

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# 起子进程时别闪黑框。
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                            ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD


# ---- SendInput 的结构体 --------------------------------------------------
# 手写而不是 import pywin32：全项目不引入 GUI 三方库，只用 ctypes。

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD),
                ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))]


class _INPUTunion(ctypes.Union):
    # 只用键盘那一支，但 union 的大小必须按最大成员算，
    # 少写成员会让 SendInput 因 cbSize 不匹配静默返回 0。
    _fields_ = [('ki', KEYBDINPUT), ('_pad', ctypes.c_byte * 24)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', wintypes.DWORD), ('u', _INPUTunion)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT),
                             ctypes.c_int]
user32.SendInput.restype = wintypes.UINT



@dataclass(frozen=True)
class Win:
    """一个窗口/控件的快照。拿到之后 hwnd 随时可能失效，用前 `alive()` 一下。"""

    hwnd: int
    cls: str
    text: str
    rect: tuple[int, int, int, int]   # left, top, right, bottom
    visible: bool
    enabled: bool
    pid: int
    ctrl_id: int
    depth: int = 0

    @property
    def w(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def h(self) -> int:
        return self.rect[3] - self.rect[1]

    @property
    def center(self) -> tuple[int, int]:
        return (self.rect[0] + self.w // 2, self.rect[1] + self.h // 2)

    def alive(self) -> bool:
        return bool(user32.IsWindow(self.hwnd))

    def __str__(self) -> str:
        flag = ('V' if self.visible else '-') + ('E' if self.enabled else '-')
        return (f'{" " * self.depth * 2}[{flag}] {self.cls}#{self.ctrl_id} '
                f'{self.rect} {self.text!r}')


# ---- 只读探测 -----------------------------------------------------------

def session_ok() -> tuple[bool, str]:
    """
    **开机第一件事。** Session 0 里 `EnumWindows` 只能看到自己会话的窗口，
    也就是啥都看不到，后面每一步都会以"找不到控件"的形式失败，
    而真正的原因跟控件毫无关系。宁可在这里直接拒绝启动。
    """
    sid = wintypes.DWORD()
    ok = kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(),
                                       ctypes.byref(sid))
    if not ok:
        return False, '拿不到当前进程的会话 ID'
    if sid.value == 0:
        return False, ('当前跑在 Session 0（服务/计划任务未勾"只在用户登录时运行"），'
                       '看不见任何窗口。计划任务请改成"只在用户登录时运行"。')
    if not user32.GetForegroundWindow() and not top_windows():
        return False, f'会话 {sid.value} 里枚举不到任何顶层窗口，桌面可能未就绪'
    return True, f'session={sid.value}'


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_text(hwnd: int) -> str:
    """
    先试 `GetWindowTextW`；拿不到再用 `WM_GETTEXT`。
    第二条路对某些自绘控件有效 —— 通达信里不少标签属于这一类。
    """
    n = user32.GetWindowTextLengthW(hwnd)
    if n > 0:
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value:
            return buf.value
    n = send_message(hwnd, WM_GETTEXTLENGTH, 0, 0)
    if n <= 0:
        return ''
    buf = ctypes.create_unicode_buffer(int(n) + 1)
    res = ctypes.c_size_t(0)
    user32.SendMessageTimeoutW(hwnd, WM_GETTEXT, int(n) + 1,
                               ctypes.addressof(buf),
                               SMTO_ABORTIFHUNG, DEFAULT_TIMEOUT_MS,
                               ctypes.byref(res))
    return buf.value


def rect_of(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def tid_of(hwnd: int) -> int:
    return user32.GetWindowThreadProcessId(hwnd, None)


def snapshot(hwnd: int, depth: int = 0) -> Win:
    return Win(hwnd=hwnd, cls=class_name(hwnd), text=window_text(hwnd),
               rect=rect_of(hwnd), visible=bool(user32.IsWindowVisible(hwnd)),
               enabled=bool(user32.IsWindowEnabled(hwnd)), pid=pid_of(hwnd),
               ctrl_id=user32.GetDlgCtrlID(hwnd), depth=depth)


def top_windows(*, visible_only: bool = True) -> list[Win]:
    out: list[Win] = []

    def cb(hwnd, _lparam):
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        out.append(snapshot(hwnd))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def children(hwnd: int, *, max_depth: int = 6, _depth: int = 0) -> list[Win]:
    """
    深度优先展开控件树。通达信的对话框控件常常嵌到三四层
    （对话框 → 分组框 → Tab 页 → 复选框），只枚举直接子窗口会漏掉一半。
    """
    out: list[Win] = []
    if _depth >= max_depth:
        return out

    def cb(child, _lparam):
        out.append(snapshot(child, depth=_depth + 1))
        out.extend(children(child, max_depth=max_depth, _depth=_depth + 1))
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    return out


def root_of(hwnd: int) -> int:
    return user32.GetAncestor(hwnd, GA_ROOT)


def is_iconic(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def is_window(hwnd: int) -> bool:
    """句柄还活着吗。等待循环里必须先问这个 —— 窗口被关掉之后所有
    `SendMessage` 都会静默返回 0，看起来跟"状态没变"一模一样。"""
    return bool(user32.IsWindow(hwnd))


def is_visible(hwnd: int) -> bool:
    """
    控件当前可见吗。

    **多 Tab 页对话框里这是唯一可靠的"当前在哪一页"判据。**
    通达信「盘后数据下载」五个 tab 的控件同时挂在控件树上，
    枚举得到不等于点得到；`TCM_GETCURSEL` 只反映标签高亮，不反映内容区。
    """
    return bool(user32.IsWindowVisible(hwnd))


def window_hung(hwnd: int) -> bool | None:
    """这个窗口的消息线程**还转得动吗**（假死判据）。

    `IsHungAppWindow` 返回非 0 = 该窗口所属线程已经不处理消息（系统给它挂上"无响应"）。
    它是 `BOOL`，这里交**三态**：`None` = 句柄已经没了 / API 报错 = **判不了**，
    调用点不许把"判不了"当成"假死"去重启（探不到 ≠ false，同 `jobs` 那套三态纪律）。
    """
    if not hwnd or not is_window(hwnd):
        return None
    try:
        user32.IsHungAppWindow.restype = wintypes.BOOL
        user32.IsHungAppWindow.argtypes = [wintypes.HWND]
        return bool(user32.IsHungAppWindow(hwnd))
    except OSError:
        return None


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def input_idle_sec() -> int | None:
    """这台机器**多久没有人碰过键盘鼠标**了（秒）。判不了交 `None`，不许当 0。

    为什么需要它（用户 10-06 那句条件到今天才有落点）："万一我当时也在操作电脑……" ——
    「需要时才运行」那一档要在时间带外主动关掉客户端，而如果人正好在客户端里查东西，
    顺手关掉就是动用户的现场。所以：**刚有人动过（空闲时间不够长）就不关**，改记待人工清单。
    `GetLastInputInfo` 给的是**全机**最后一次输入（不是本窗口），够用且无需权限。
    """
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        user32.GetLastInputInfo.restype = wintypes.BOOL
        user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        kernel32.GetTickCount.restype = wintypes.DWORD
        return max(0, int((kernel32.GetTickCount() - info.dwTime) // 1000))
    except OSError:
        return None



def windows_of_pid(pid: int, *, visible_only: bool = False) -> list[Win]:
    return [w for w in top_windows(visible_only=visible_only) if w.pid == pid]


# ---- 写操作 -------------------------------------------------------------

def send_message(hwnd: int, msg: int, wparam: int = 0, lparam: int = 0,
                 *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> int:
    """
    带超时的 SendMessage。**不用裸 `SendMessageW`** —— 通达信在下载数据时
    主线程会长时间不抽消息队列，裸 Send 会把我们一起挂住，
    看门狗线程也就跟着停摆了。
    """
    res = ctypes.c_size_t(0)
    ok = user32.SendMessageTimeoutW(hwnd, msg, wparam, lparam,
                                    SMTO_ABORTIFHUNG, timeout_ms,
                                    ctypes.byref(res))
    if not ok:
        return -1
    return int(res.value)


def post_message(hwnd: int, msg: int, wparam: int = 0, lparam: int = 0) -> bool:
    return bool(user32.PostMessageW(hwnd, msg, wparam, lparam))


def bm_click(hwnd: int) -> bool:
    """
    点按钮/复选框。用 Post 而不是 Send：点"开始下载"之后通达信会同步跑
    很久才返回，Send 会阻塞在这里；Post 投递完就走，让状态机去 verify。
    """
    return post_message(hwnd, BM_CLICK, 0, 0)


def bm_getcheck(hwnd: int) -> int:
    """0 未勾 / 1 已勾 / 2 灰。返回 -1 表示这个控件不认这条消息。"""
    return send_message(hwnd, BM_GETCHECK, 0, 0)


def ensure_checked(hwnd: int, want: bool = True) -> tuple[bool, str]:
    """
    **只在状态不对时才点。** 复选框是 toggle：已经勾上了再点一次就取消了。
    早期脚本无脑点一遍，遇到通达信记住了上次勾选状态的情况就正好反过来，
    下载了个空。

    **点完要轮询几次再判**（2026-08-31 实测）：`BM_CLICK` 之后立刻 `BM_GETCHECK`
    读到的还是旧值 —— 「盘后数据下载」里的 1 分钟线/5 分钟线两个框都报
    `点击无效：check 0 → 0`，但过一会儿再读是 1，下载也确实按勾选跑了。
    只读一次就把成功判成失败，状态机会去重试，而重试会把刚勾上的又点掉。
    """
    cur = bm_getcheck(hwnd)
    if cur < 0:
        return False, '控件不响应 BM_GETCHECK，无法确认勾选状态'
    if (cur == BST_CHECKED) == want:
        return True, f'已是目标状态（check={cur}）'
    bm_click(hwnd)
    new = cur
    for _ in range(CHECK_POLL):
        time.sleep(CHECK_INTERVAL)
        new = bm_getcheck(hwnd)
        if (new == BST_CHECKED) == want:
            return True, f'点击后 check={new}'
    return False, f'点击无效：check {cur} → {new}（等了 {CHECK_POLL * CHECK_INTERVAL:.1f}s）'



def set_text(hwnd: int, text: str, *, verify: bool = True) -> bool:
    """
    直接塞文本，绕过键盘。**这是登录框首选路径** ——
    通达信会记住上次的账号，用键盘"打进去"会追加到残留内容后面变成
    `1896996969618969969696`。`WM_SETTEXT` 是整体替换，天然没有这个问题。

    `verify=False` 用于口令框：`ES_PASSWORD` 的编辑框跨进程 `WM_GETTEXT`
    读回来是空的（系统有意为之），回读比对必然失败，不代表没写进去。
    """
    buf = ctypes.create_unicode_buffer(text)
    res = ctypes.c_size_t(0)
    ok = user32.SendMessageTimeoutW(hwnd, WM_SETTEXT, 0, ctypes.addressof(buf),
                                    SMTO_ABORTIFHUNG, DEFAULT_TIMEOUT_MS,
                                    ctypes.byref(res))
    if not ok:
        return False
    if not verify:
        return True
    return window_text(hwnd) == text


def close_window(hwnd: int, *, syscommand: bool = False) -> bool:
    if syscommand:
        return post_message(hwnd, WM_SYSCOMMAND, SC_CLOSE, 0)
    return post_message(hwnd, WM_CLOSE, 0, 0)


def minimize(hwnd: int) -> bool:
    """
    `ShowWindow(SW_MINIMIZE)` 需要跨进程调用能生效；不行就退回
    `WM_SYSCOMMAND/SC_MINIMIZE`，让目标进程自己最小化自己。
    """
    if user32.ShowWindow(hwnd, SW_MINIMIZE):
        return True
    return post_message(hwnd, WM_SYSCOMMAND, SC_MINIMIZE, 0)


def restore(hwnd: int) -> bool:
    """把最小化（或被挪到 -32000,-32000 那种「隐藏式最小化」）的主窗口放回来。

    为什么需要：**最小化状态下的主窗口常常直接无视 `SC_CLOSE`** —— 2026-10-13 实测
    「需要时才运行」那一档第一次请通达信退出就没退掉，窗口当时正停在
    `(-32000,-32000,-31840,-31972)`（最小化位）。所以关之前先恢复，再发退出请求。
    """
    if user32.ShowWindow(hwnd, SW_RESTORE):
        return True
    return post_message(hwnd, WM_SYSCOMMAND, SC_RESTORE, 0)


def send_input_key(vk: int, *, up: bool = False) -> bool:
    """按/放一个虚拟键，走真实输入队列。"""
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.u.ki = KEYBDINPUT(wVk=vk, wScan=0,
                          dwFlags=KEYEVENTF_KEYUP if up else 0,
                          time=0, dwExtraInfo=None)
    return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) == 1


def send_input_tap(vk: int, *, ctrl: bool = False) -> bool:
    """完整敲一下某个键，可带 Ctrl。"""
    if ctrl:
        send_input_key(VK_CONTROL)
    ok = send_input_key(vk) and send_input_key(vk, up=True)
    if ctrl:
        send_input_key(VK_CONTROL, up=True)
    return ok


def send_input_text(text: str, *, per_char_delay: float = 0.02) -> tuple[bool, str]:
    """
    用**真实键盘事件**打字（`KEYEVENTF_UNICODE`，不受当前键盘布局影响）。

    这是 `SafeEdit` 唯一认的输入方式。前提是当前进程连到了交互式输入桌面
    （见 `input_desktop_ok()`），且目标窗口已经在前台 —— 真实按键打的是
    前台窗口，调用方必须先 `set_foreground(root)` 再 `focus_control(ctrl)`。

    用 UNICODE 而不是 VK：账号是数字、口令可能含符号，走 VK 就要自己
    维护 Shift 和键盘布局映射，UNICODE 直接给码点，一步到位。
    """
    sent = 0
    for ch in text:
        code = ord(ch)
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.u.ki = KEYBDINPUT(wVk=0, wScan=code, dwFlags=flags,
                                  time=0, dwExtraInfo=None)
            if user32.SendInput(1, ctypes.byref(inp),
                                ctypes.sizeof(INPUT)) != 1:
                return False, f'SendInput 第 {sent + 1} 个字符被拒'
        sent += 1
        time.sleep(per_char_delay)
    return True, f'SendInput 真实按键 {sent} 个字符'


def clear_by_input(max_clear: int = 40) -> None:
    """
    用真实按键清空当前焦点控件：End 到末尾，再连按退格。

    不用 Ctrl+A —— SafeEdit 这类自绘控件对全选的处理不可预期，
    而退格是每个编辑控件都必然正确处理的操作。
    """
    send_input_tap(VK_END)
    for _ in range(max_clear):
        send_input_tap(VK_BACK)
        send_input_tap(VK_DELETE)


def focus_control(hwnd: int) -> tuple[bool, str]:

    """
    让某个控件拿到键盘焦点，**不动鼠标、不抢前台**。

    做法是 `AttachThreadInput` 把自己的输入队列挂到目标线程上，
    然后跨进程 `SetFocus`。挂上之后 `SetFocus` 才对别的进程的窗口有效。

    为什么必须有这条路：`SafeEdit`（通达信的安全输入框）拒收
    `WM_SETTEXT`，只能靠键盘消息填；而键盘消息要生效就得先有焦点。
    唯一的替代方案是点一下控件 —— 但在拿不到输入桌面的环境里
    （`GetCursorPos` 返回 ACCESS_DENIED，见 `input_desktop_ok()`）
    鼠标根本用不了。所以这条是那种环境下的唯一出路。
    """
    if not user32.IsWindow(hwnd):
        return False, '控件已失效'
    target_tid = tid_of(hwnd)
    my_tid = kernel32.GetCurrentThreadId()
    attached = bool(user32.AttachThreadInput(my_tid, target_tid, True))
    try:
        user32.SetFocus(hwnd)
        got = user32.GetFocus()
    finally:
        if attached:
            user32.AttachThreadInput(my_tid, target_tid, False)
    if got == hwnd:
        return True, f'SetFocus 成功（attach={attached}）'
    return False, (f'SetFocus 后焦点在 {got}，不是目标 {hwnd}'
                   f'（AttachThreadInput={attached}）')


def _tap(hwnd: int, vk: int, *, char: int | None = None) -> None:
    """按一下某个键：keydown → （可选）WM_CHAR → keyup，lParam 按真实键盘构造。"""
    post_message(hwnd, WM_KEYDOWN, vk, key_lparam(vk))
    if char is not None:
        post_message(hwnd, WM_CHAR, char, key_lparam(vk))
    post_message(hwnd, WM_KEYUP, vk, key_lparam(vk, up=True))


def clear_edit(hwnd: int, *, max_clear: int = 64) -> str:
    """
    把编辑框清空。**三条路全都走一遍，因为清没清干净读不出来。**

    1. `EM_SETSEL(0, -1)` + `WM_CLEAR` —— 标准编辑框一步到位；
    2. `EM_SETSEL(0, -1)` + `WM_CHAR(VK_BACK)` —— 选中后退格删掉整段；
    3. `VK_END` + N 次退格，再 `VK_HOME` + N 次 Delete —— 不知道光标落在
       哪一头就两头都刮一遍。

    **为什么要这么啰嗦**：SafeEdit 双向都封，写完读不回来，
    "清空成功"没法验证。2026-08-28 就是清空静默失效，新账号追加到旧账号后面
    变成 `1896996969618969969696`，报出来的却是"用户名或密码错误"，
    错误信息把人往密码上引。既然验不了，就把所有能用的清空手段都用上。
    """
    send_message(hwnd, EM_SETSEL, 0, -1)
    send_message(hwnd, WM_CLEAR, 0, 0)
    send_message(hwnd, EM_SETSEL, 0, -1)
    post_message(hwnd, WM_CHAR, VK_BACK, key_lparam(VK_BACK))
    _tap(hwnd, VK_END)
    for _ in range(max_clear):
        _tap(hwnd, VK_BACK, char=VK_BACK)
    _tap(hwnd, VK_HOME)
    for _ in range(max_clear):
        _tap(hwnd, VK_DELETE)
    time.sleep(0.05 + 0.002 * max_clear)
    return f'EM_SETSEL+WM_CLEAR、选中退格、{max_clear} 次退格与 Delete 各一轮'


def type_text(hwnd: int, text: str, *, clear: bool = True,
              max_clear: int = 64) -> tuple[bool, str]:
    """
    用键盘消息往控件里打字。**每个字符一条 `WM_CHAR`。**

    清空这一步不能省也不能只发退格：通达信会记住上次的账号，不清就是追加，
    结果是 `1896996969618969969696`，然后报"用户名或密码错误"。
    清空细节见 `clear_edit`。
    """
    if not user32.IsWindow(hwnd):
        return False, '控件已失效'
    how = clear_edit(hwnd, max_clear=max_clear) if clear else ''
    for ch in text:
        post_message(hwnd, WM_CHAR, ord(ch), 1)
    # 消息是投递的，要给对方一点时间处理完再回读。
    time.sleep(0.05 + 0.004 * len(text))
    return True, (f'已投递 {len(text)} 个字符的 WM_CHAR'
                  + (f'（清空：{how}）' if clear else ''))



def input_desktop_ok() -> tuple[bool, str]:
    """
    能不能用鼠标 / 抢前台 / 发真实按键。

    **这跟"能不能枚举窗口"是两件事。** 实测本机的情况：
    `EnumWindows` 正常、能读到通达信的全部控件，但
    `GetCursorPos` 返回 ACCESS_DENIED(5)、`GetForegroundWindow()` 返回 0
    —— 进程在会话里，却没连到交互式输入桌面。

    这种环境下：

    - `SetCursorPos` / `mouse_event` / `SendKeys` 全部无效（而且是静默无效
      或返回 0，不抛异常，最容易被当成"点了但没反应"）；
    - `PostMessage` / `SendMessage` / `AttachThreadInput`+`SetFocus` 照常有效。

    所以定位策略里的"坐标点击"和"发按键"两级在这里必须直接放弃，
    走纯消息的路。提前判出来，比在第四级兜底时莫名失败好得多。
    """
    pt = wintypes.POINT()
    ctypes.set_last_error(0)
    if not user32.GetCursorPos(ctypes.byref(pt)):
        err = ctypes.get_last_error()
        return False, (f'GetCursorPos 失败（错误码 {err}）—— '
                       '当前进程没连到交互式输入桌面，'
                       '**鼠标点击、抢前台、SendKeys 都不可用**，'
                       '只能走 PostMessage/SetFocus 这类消息路径')
    if not user32.GetForegroundWindow():
        return False, ('GetForegroundWindow 返回 0 —— 拿不到前台窗口，'
                       '坐标点击不可靠，优先走消息路径')
    return True, f'输入桌面可用（光标在 {pt.x},{pt.y}）'


def restore(hwnd: int) -> bool:
    return bool(user32.ShowWindow(hwnd, SW_RESTORE))


def maximize(hwnd: int) -> bool:
    """
    最大化。

    **操作顶部菜单之前应该先最大化。** 两个好处是实打实的：
    菜单栏一定在屏幕上（窗口被拖到屏幕边缘时菜单可能有一半在屏幕外，
    坐标点击必然点空），而且别的程序不会盖在上面。
    代价是会改变用户的窗口布局，所以只在需要动菜单时做。
    """
    if user32.ShowWindow(hwnd, SW_MAXIMIZE):
        return True
    return post_message(hwnd, WM_SYSCOMMAND, SC_MAXIMIZE, 0)


def is_zoomed(hwnd: int) -> bool:
    return bool(user32.IsZoomed(hwnd))


def click_center(hwnd: int, *, dx: int = 0, dy: int = 0) -> tuple[bool, str]:
    """
    点某个控件的中心点 —— **坐标是运行时从控件自己的 rect 算出来的，
    不是写死的数字**。

    这一条区别很重要。"登录框固定在屏幕正中间"在同一台机器上确实成立，
    但换分辨率、换 DPI 缩放、多显示器、或者通达信记住了上次被拖动的位置，
    写死的 (x, y) 就全错了 —— 而且错的时候是"点到了别的东西上"，
    比报错更难查。`GetWindowRect` 拿到的是当前真实位置，
    既有坐标点击的通用性，又没有写死的脆性。

    什么时候真的需要它：`SafeEdit` 这类拒绝 `WM_SETTEXT` 的控件必须先
    点一下拿到焦点，才能用键盘输入。纯消息的路子在它身上走不通。
    """
    if not user32.IsWindow(hwnd):
        return False, '控件已失效'
    left, top, right, bottom = rect_of(hwnd)
    if right <= left or bottom <= top:
        return False, f'控件尺寸为 0（{left},{top},{right},{bottom}），无法点击'
    x = (left + right) // 2 + dx
    y = (top + bottom) // 2 + dy
    return click_point(x, y)


def click_point(x: int, y: int) -> tuple[bool, str]:
    """
    真的动鼠标。会打断用户操作，且窗口被盖住时会点到别人身上，
    所以调用方要先 `set_foreground()`。
    """
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    if not user32.SetCursorPos(int(x), int(y)):
        return False, f'SetCursorPos({x},{y}) 失败'
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    return True, f'鼠标点击 ({x},{y})'


def tab_select(hwnd: int, index: int) -> bool:
    """
    切 Tab 页。**在通达信上不要用它** —— 见下。

    `TCM_SETCURSEL` 会改选中项但**不发 TCN_SELCHANGE 通知**，对话框不刷新内容区。
    2026-08-31 实测「盘后数据下载」：调完 `TCM_GETCURSEL` 返回 1（标签也高亮了），
    但 `IsWindowVisible` 显示还是第 1 页的控件可见、第 2 页的不可见 ——
    **控件状态和对话框内部状态就此不一致，随后通达信整个进程崩了**
    （「通达信应用程序 已停止工作」）。

    正确做法是**用鼠标点 Tab 标签的坐标**：实测点完 `1424=0 1425=1 1426=1`，
    换页干净生效。判据用 `IsWindowVisible` 而不是 `TCM_GETCURSEL` ——
    后者只反映标签，不反映内容区。
    """
    if send_message(hwnd, TCM_SETCURSEL, index, 0) < 0:
        return False
    return send_message(hwnd, TCM_GETCURSEL, 0, 0) == index



def set_foreground(hwnd: int) -> bool:
    """
    抢前台。Windows 限制非前台进程调 `SetForegroundWindow`，
    `AttachThreadInput` 把自己的输入队列挂到目标线程上来绕过这条限制。
    **只在不得不用键盘时才调这个** —— 抢前台会打断用户正在做的事。

    传进来的 hwnd 会**先归到顶层窗口**：`SetForegroundWindow` 对子控件
    无效（MSDN 明确要求顶层窗口）。之前 `locator.send_keys` 直接把
    `SafeEdit` 子控件的 hwnd 传进来，于是永远返回"抢不到前台"，
    真实按键那条路被这个参数错误整条堵死。
    """
    hwnd = root_of(hwnd) or hwnd
    if is_iconic(hwnd):
        restore(hwnd)
    target_tid = tid_of(hwnd)
    my_tid = kernel32.GetCurrentThreadId()
    attached = bool(user32.AttachThreadInput(my_tid, target_tid, True))
    try:
        # 前台锁超时不清零的话，SetForegroundWindow 会"成功"但只是闪任务栏。
        user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, None,
                                    SPIF_SENDCHANGE)
        user32.BringWindowToTop(hwnd)
        user32.ShowWindow(hwnd, SW_SHOWNORMAL)
        ok = bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(my_tid, target_tid, False)
    if ok or user32.GetForegroundWindow() == hwnd:
        return True
    # 最后一招：模拟一次 ALT 抖动。Windows 只在"有输入事件发生"时
    # 才放行前台切换，空按一下 ALT 就是造这个事件。
    send_input_key(VK_MENU, up=False)
    send_input_key(VK_MENU, up=True)
    time.sleep(0.05)
    user32.SetForegroundWindow(hwnd)
    return user32.GetForegroundWindow() == hwnd



# ---- 进程 ---------------------------------------------------------------

def pids_by_name(name: str) -> list[int]:
    """
    按镜像名找进程。没有 psutil，走 `tasklist` 的 CSV 输出。
    name 大小写不敏感，例如 `tdxw.exe`、`TPyth.exe`。
    """
    out = _tasklist(['/FI', f'IMAGENAME eq {name}'])
    pids: list[int] = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == name.lower():
            try:
                pids.append(int(parts[1]))
            except ValueError:
                continue
    return pids


def pids_by_prefix(prefix: str) -> list[tuple[str, int]]:
    """
    按镜像名前缀找进程，返回 `[(镜像名, pid)]`。

    TPyth 同步进程的镜像名带版本尾巴（见过 `TPyth.exe`、`TPython.exe`），
    精确匹配会漏。等它退出是"自动同步完成"的判据之一，漏判就等于不等。
    """
    hits: list[tuple[str, int]] = []
    for line in _tasklist([]).splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2:
            continue
        if parts[0].lower().startswith(prefix.lower()):
            try:
                hits.append((parts[0], int(parts[1])))
            except ValueError:
                continue
    return hits


def _tasklist(extra: list[str]) -> str:
    try:
        return subprocess.run(
            ['tasklist', *extra, '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, timeout=25,
            encoding='utf-8', errors='replace',
            creationflags=NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def process_alive(name: str) -> bool:
    return bool(pids_by_name(name))


def kill(name: str) -> bool:
    try:
        r = subprocess.run(['taskkill', '/F', '/IM', name],
                           capture_output=True, text=True, timeout=30,
                           encoding='utf-8', errors='replace',
                           creationflags=NO_WINDOW)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def screenshot(path) -> bool:
    """
    卡住时留一张图。用 PowerShell + System.Drawing 抓全屏 ——
    比在 ctypes 里搭 BitBlt + GDI+ 编码短得多，而且这条路径只在出事时走一次，
    多花几百毫秒无所谓。
    """
    from app.tdx_client import ps

    p = str(path).replace("'", "''")
    code = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size);"
        f"$bmp.Save('{p}',[System.Drawing.Imaging.ImageFormat]::Png);"
        "$g.Dispose();$bmp.Dispose()"
    )
    rc, _out, _err = ps.run(code, timeout=30)
    return rc == 0 and os.path.exists(path)
