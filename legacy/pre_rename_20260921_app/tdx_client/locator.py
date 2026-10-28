"""
四级降级定位。

通达信的界面不是标准 Win32 对话框：顶部菜单是自绘的，`TQ数据设置` 里的
Tab 页和复选框有的有窗口句柄有的没有，同一个版本升级一次坐标就变。
所以定位不能只有一条路，任何一条都会在某个版本上失效。

优先级从高到低：

1. **UIA 按名字找** —— 最稳，界面改版只要文字没变就还认得。慢（每次
   200~600ms 起 PowerShell）。
2. **Win32 控件树按 class + 文字 + ctrl_id 找** —— 快，能拿到 hwnd，
   拿到之后 `BM_CLICK`/`BM_GETCHECK` 都能用，是热路径首选。
3. **键盘序列** —— 菜单快捷键。**但绝不用盲发** ——
   历史上 `trigger_download.py` 用 `Alt+S` → `Enter` 盲触发，
   一旦焦点不在主窗口上，这两下就打到别的程序上去了。
   这一级必须先确认前台窗口是通达信，且执行后必须 verify。
4. **坐标点击** —— 最后兜底，只在前三级全挂且窗口确认没被遮挡时用。
   坐标写死是所有 GUI 自动化里最脆的东西，用了就要在日志里显式喊出来。

**每一级都要 verify。** 定位成功 ≠ 操作成功。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.tdx_client import ps, win32
from app.tdx_client.win32 import Win

# 通达信主窗口标题里稳定出现的片段。版本升级过 `通达信金融终端`、
# `通达信金融终端(开心果整合版)` 等等，共同前缀就这五个字。
#
# **但标题不足以区分主窗口和登录框。** 实测 V7.73 的登录框标题也是
# `通达信金融终端V7.73`，而且它的 class 是标准对话框 `#32770`。
# 第一版只按标题找，结果把登录框当成了主窗口，接着去里面找 `TQ策略` 菜单，
# 自然找不到 —— 而真正的原因是根本没登录。所以主窗口判定必须加 class 排除。
MAIN_TITLE = '通达信金融终端'
DIALOG_CLASS = '#32770'
TDX_EXE = 'tdxw.exe'
# 后台同步进程。名字带版本尾巴，所以用前缀匹配。
SYNC_EXE_PREFIX = 'TPyth'

# 按钮类控件的 class 名。通达信混用标准控件和 MFC 自绘控件。
BUTTON_CLASSES = ('Button', 'TButton', 'AfxWnd')
# `SafeEdit` 是通达信自己的"安全输入框"，登录框的用户名/密码用的就是它，
# **不是标准 `Edit`**。少了这一项登录框里一个编辑框都找不到。
EDIT_CLASSES = ('SafeEdit', 'Edit', 'RichEdit20W', 'TEdit')


class LocateError(RuntimeError):
    """四级全挂。message 里必须写清楚每一级为什么挂，否则没法排查。"""


@dataclass
class Attempt:
    tier: str
    ok: bool
    detail: str


@dataclass
class Located:
    """定位结果。`hwnd` 可能为 0 —— UIA 命中时拿不到句柄，只能继续用 UIA 操作。"""

    tier: str
    hwnd: int = 0
    point: tuple[int, int] | None = None
    text: str = ''
    trail: list[Attempt] = field(default_factory=list)

    @property
    def has_hwnd(self) -> bool:
        return self.hwnd > 0 and bool(win32.user32.IsWindow(self.hwnd))

    def summary(self) -> str:
        return f'{self.tier}: hwnd={self.hwnd or "-"} text={self.text!r}'


# ---- 主窗口 -------------------------------------------------------------

def main_window(*, timeout: float = 0) -> Win | None:
    """
    找通达信**主框架**窗口（不是登录框）。

    两条判据一起用：

    - 标题含 `通达信金融终端`；
    - class **不是** `#32770`。

    第二条是踩坑换来的：V7.73 的登录框标题也是 `通达信金融终端V7.73`，
    只按标题找、再"取面积最大的那个"，在只有登录框存在时就会把登录框
    认成主窗口。后果是 s2 以为已经登录、s5 在登录框里找 `TQ策略` 菜单，
    报出来的错是"找不到菜单"，跟真实原因（没登录）完全不沾边。

    同时排除掉最小化到托盘之后残留的 0×0 隐藏窗口。
    """
    deadline = time.monotonic() + timeout
    while True:
        pids = set(win32.pids_by_name(TDX_EXE))
        best: Win | None = None
        for w in win32.top_windows(visible_only=False):
            if pids and w.pid not in pids:
                continue
            if MAIN_TITLE not in w.text:
                continue
            if w.cls == DIALOG_CLASS:
                continue          # 登录框/提示框，不是主框架
            if best is None or w.w * w.h > best.w * best.h:
                best = w
        if best is not None or time.monotonic() >= deadline:
            return best
        time.sleep(1.0)


def sync_running() -> tuple[bool, str]:
    """
    TPyth 同步进程还在不在。

    **前缀匹配而不是精确匹配** —— 这个进程的镜像名带版本尾巴，
    见过 `TPyth.exe` 也见过 `TPython.exe`。精确匹配漏判的后果是
    "以为同步完了"，然后拿着旧数据往下跑。
    """
    hits = win32.pids_by_prefix(SYNC_EXE_PREFIX)
    if not hits:
        return False, f'没有 {SYNC_EXE_PREFIX}* 进程'
    return True, '、'.join(f'{n}({p})' for n, p in hits)


# ---- 通用查找 -----------------------------------------------------------

def _matches(w: Win, *, text: str | None, classes: tuple[str, ...] | None,
             ctrl_id: int | None, exact: bool) -> bool:
    if ctrl_id is not None and w.ctrl_id != ctrl_id:
        return False
    if classes and not any(w.cls.startswith(c) for c in classes):
        return False
    if text is not None:
        t = w.text.strip()
        if exact:
            if t != text:
                return False
        elif text not in t:
            return False
    return True


def find_child(root: int, *, text: str | None = None,
               classes: tuple[str, ...] | None = None,
               ctrl_id: int | None = None, exact: bool = False,
               visible_only: bool = True, max_depth: int = 6) -> list[Win]:
    """
    在控件树里按条件找。返回全部命中，**按 (top, left) 排序**。

    排序不是可选的：登录框里账号和口令两个 `Edit` 的枚举顺序不保证，
    早期脚本按枚举顺序取 `[0]` 当账号框，在某些版本上正好反了 ——
    口令被填进账号栏，账号被当口令，然后报"用户名或密码错误"，
    查半天以为是密码记错了。**按 Y 坐标排序才是稳定的。**
    """
    hits = [c for c in win32.children(root, max_depth=max_depth)
            if (not visible_only or c.visible)
            and _matches(c, text=text, classes=classes,
                         ctrl_id=ctrl_id, exact=exact)]
    hits.sort(key=lambda w: (w.rect[1], w.rect[0]))
    return hits


def wait_window(title_sub: str, *, timeout: float = 30, interval: float = 0.5,
                pid: int | None = None) -> Win | None:
    deadline = time.monotonic() + timeout
    while True:
        for w in win32.top_windows():
            if pid is not None and w.pid != pid:
                continue
            if title_sub in w.text:
                return w
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def wait_gone(hwnd: int, *, timeout: float = 60, interval: float = 0.5) -> bool:
    """
    等一个窗口消失。**注意这只是"窗口没了"，不等于"活干完了"** ——
    下载对话框关掉了但数据还是旧的，这种情况实测出现过。
    调用方必须再加一条数据层判据（见 readiness.py）。
    """
    deadline = time.monotonic() + timeout
    while win32.user32.IsWindow(hwnd) and win32.user32.IsWindowVisible(hwnd):
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)
    return True


# ---- 四级降级 -----------------------------------------------------------

def locate(label: str, *, root: Win | None = None,
           uia_title: str | None = None,
           classes: tuple[str, ...] | None = None,
           ctrl_id: int | None = None,
           exact: bool = False,
           keys: str | None = None,
           point: tuple[int, int] | None = None,
           allow_uia: bool = True,
           allow_keys: bool = False,
           allow_point: bool = False,
           nth: int = 0) -> Located:
    """
    按四级顺序定位 `label` 指向的控件。

    `keys` / `point` 是三、四级的兜底，**默认都关着** ——
    调用方必须显式打开，因为这两级会打到别的窗口上去。
    """
    trail: list[Attempt] = []

    # 二级放在最前面试：它最快，而且拿到 hwnd 之后所有操作都不需要前台。
    if root is not None:
        hits = find_child(root.hwnd, text=label, classes=classes,
                          ctrl_id=ctrl_id, exact=exact)
        if len(hits) > nth:
            w = hits[nth]
            trail.append(Attempt('win32', True, f'命中 {len(hits)} 个，取第 {nth}'))
            return Located('win32', hwnd=w.hwnd, point=w.center,
                           text=w.text, trail=trail)
        trail.append(Attempt('win32', False,
                             f'控件树里没有 text~{label!r} class~{classes}'))
    else:
        trail.append(Attempt('win32', False, '没给 root，跳过'))

    if allow_uia:
        title = uia_title or (root.text if root else MAIN_TITLE)
        nodes, err = ps.dump_uia(title)
        if err:
            trail.append(Attempt('uia', False, f'dump 失败：{err}'))
        else:
            cand = [n for n in nodes
                    if label in (n.get('name') or '')
                    and not n.get('offscreen')
                    and (n.get('w') or 0) > 0]
            if len(cand) > nth:
                n = sorted(cand, key=lambda x: (x.get('y', 0), x.get('x', 0)))[nth]
                trail.append(Attempt('uia', True,
                                     f'命中 {len(cand)} 个 → {n.get("name")!r}'))
                cx = int(n.get('x', 0) + (n.get('w', 0) or 0) / 2)
                cy = int(n.get('y', 0) + (n.get('h', 0) or 0) / 2)
                return Located('uia', hwnd=0, point=(cx, cy),
                               text=n.get('name') or '', trail=trail)
            trail.append(Attempt('uia', False,
                                 f'{len(nodes)} 个节点里没有名字含 {label!r} 的'))

    if allow_keys and keys:
        fg = win32.user32.GetForegroundWindow()
        expect = root.hwnd if root else (main_window().hwnd if main_window() else 0)
        if expect and win32.root_of(fg) == win32.root_of(expect):
            trail.append(Attempt('keys', True, f'前台确认，可发 {keys!r}'))
            return Located('keys', hwnd=expect, text=keys, trail=trail)
        trail.append(Attempt('keys', False,
                             f'前台不是目标窗口（fg={fg} expect={expect}），'
                             '拒绝盲发按键'))

    if allow_point and point:
        trail.append(Attempt('point', True,
                             f'**降级到写死坐标 {point}** —— 界面一改版就会点错，'
                             '这条记录出现说明前三级都失效了，需要更新定位规则'))
        return Located('point', hwnd=0, point=point, trail=trail)

    raise LocateError(f'定位 {label!r} 失败：\n' +
                      '\n'.join(f'  [{a.tier}] {"ok" if a.ok else "×"} {a.detail}'
                                for a in trail))


def click(loc: Located, *, root: Win | None = None) -> tuple[bool, str]:
    """
    按定位结果点下去。**能用 hwnd 就用 hwnd** ——
    `BM_CLICK` 不要求前台、不要求鼠标位置，通达信被别的程序盖住也生效。
    """
    if loc.has_hwnd:
        if win32.bm_click(loc.hwnd):
            return True, f'BM_CLICK hwnd={loc.hwnd}'
        return False, f'BM_CLICK 投递失败 hwnd={loc.hwnd}'
    if loc.tier == 'uia' and loc.text:
        title = root.text if root else MAIN_TITLE
        ok, detail = ps.invoke_uia(title, loc.text)
        return ok, f'UIA Invoke：{detail}'
    if loc.point:
        return win32.click_point(*loc.point)
    return False, '定位结果里既没有句柄也没有坐标'


def send_keys(hwnd: int, keys: str) -> tuple[bool, str]:
    """
    发按键。**必须先抢前台** —— SendKeys 打的是前台窗口，
    不抢前台就是往当前活动窗口里乱敲。抢不到就直接失败，不要硬发。
    """
    if not win32.set_foreground(hwnd):
        return False, f'抢不到前台 hwnd={hwnd}，拒绝发送按键（会打到别的窗口上）'
    time.sleep(0.2)
    esc = keys.replace('"', '`"')
    rc, out, err = ps.run(
        'Add-Type -AssemblyName System.Windows.Forms;'
        f'[System.Windows.Forms.SendKeys]::SendWait("{esc}")', timeout=30)
    if rc != 0:
        return False, (err or out).strip()
    return True, f'已发送 {keys!r}'
