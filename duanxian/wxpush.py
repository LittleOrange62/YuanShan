"""「个人微信推送」的执行层：把配置里的步骤展开成动作序列，在本机桌面上按出来。

分工与 `notifyconfig.py` + `duanxian/notify.py` 那一套同形：

- 配置、校验、步骤表、预检闸的**形状**只住 `wxpushconfig.py`，这里不抄第二份；
- **纯计划与真副作用彻底分开**：`build_ops()` 只算出「要按哪些键、点哪里、等多久」，
  全程不碰桌面；真碰桌面的只有 `_desktop_apply()` 一个口子（外加 `run_script()` 起真进程）。
  理由与 `notify._post_json` / `jobs._spawn_stage` 同源（10-06 那次 pytest 在用户桌面
  启动了通达信窗口）：**跑一次全量测试绝不能在这台机器上真按一轮键**。
- **预检只读**：`preflight()` 全是查询（前台窗口在不在、屏幕有没有在渲染、
  微信窗口在不在、键盘空闲多久），没有任何一步为了"看看行不行"去注入按键。
  面板每次打开都会现算一遍，所以这个动作必须零副作用。

三条实测事实（2026-10-20，决定了这里的形状）：

1. 微信 4.1.8（Qt5 内核）对 UIA **一个子控件都不暴露** —— 前台激活＋点击之后
   `descendants()` 仍是 0。所以读不到输入框（被删的草稿还不回来），
   也点不到「第一个搜索结果」，只能按 Enter 走过去，或按固定坐标盲点。
2. RDP 客户端最小化/断开时这个会话**既没有渲染表面也没有输入桌面**：
   整屏 GDI 采样 5.6 万个点全为 0、`GetForegroundWindow()` 回 0、
   `SendInput` 一律回 `ERROR_ACCESS_DENIED`。所以那两道闸是必检项。
3. 主窗口只认得到句柄与矩形（`class='Qt51514QWindowIcon' title='微信'`），
   所以**点击偏移一律相对窗口左上角**：窗口一挪，屏幕绝对坐标就会点到别的应用上，
   那是会把按键打进你正在用的程序里的。

送达与否**这一侧读不回来**（没有回执、也没有可读的消息列表），所以状态只敢说
`injected`（按完了），从不说 `delivered` —— 唯一取证办法是到手机上看那一条。
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import (POINTER, Structure, Union, WinDLL, byref, c_bool, c_int, c_long,
                    c_uint, c_ulong, c_void_p, wintypes)
from datetime import datetime
from pathlib import Path

import wxpushconfig as WC

HISTORY_LIMIT = 40
#: 窗口匹配：3.9 与 4.x 的主窗口类名都是 `Qt<版本>QWindowIcon`，版本会变所以只认前后缀。
WIN_TITLES = ("微信", "WeChat", "Weixin")
USER32 = WinDLL("user32", use_last_error=True)
KERNEL32 = WinDLL("kernel32", use_last_error=True)
GDI32 = WinDLL("gdi32", use_last_error=True)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0004, 0x0008
SW_HIDE, SW_MAXIMIZE, SW_RESTORE = 0, 3, 9
SRCCOPY = 0x00CC0000
#: DWM 合成的桌面**必须带 CAPTUREBLT** 才截得到内容：不加它，`BitBlt` 调用成功、
#: 缓冲区却一片 0（10-20 实测：同一个进程里 .NET 的 `CopyFromScreen` 能截见任务栏，
#: 我们这条裸 GDI 路径却报"全黑"，于是这道闸把有桌面的机器误判成没桌面）。
CAPTUREBLT = 0x40000000
BLIT_ROP = SRCCOPY | CAPTUREBLT
#: 渲染面采样：步长 6 个像素取一次点。太少会碰巧全采到窗口外的黑边，太多把取色拖慢。
SAMPLE_STEP = 6
#: 非黑点比例低于这个值就判「没有渲染表面」。实测无桌面时是 0.0000，正常桌面远高于一。
RENDER_MIN_RATIO = 0.002


class _MOUSEINPUT(Structure):
    _fields_ = [("dx", c_long), ("dy", c_long), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", POINTER(c_ulong))]


class _KEYBDINPUT(Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", POINTER(c_ulong))]


class _INPUT(Structure):
    """x64 上必须正好 40 字节（union 按 MOUSEINPUT 取大）。

    尺寸给小了 `SendInput` 直接回 0 + `ERROR_INVALID_PARAMETER(87)`，一次键都发不出去。
    10-20 实测踩的就是这个：union 里只放 KEYBDINPUT 时算出 32 字节，
    现象是"代码看起来完全正确但桌面上什么都没发生"——这类错没有任何日志，只能靠断言拦。
    """
    class _U(Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]

    _fields_ = [("type", wintypes.DWORD), ("union", _U)]


assert ctypes.sizeof(_INPUT) == 40, ctypes.sizeof(_INPUT)


class _LASTINPUTINFO(Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class _BITMAPINFOHEADER(Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", c_long), ("biHeight", c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", c_long), ("biYPelsPerMeter", c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


#: 64 位下句柄必须显式声明 restype，否则被截成 32 位整数。
#: 症状是"读剪贴板时好时坏、GetDC 拿回来的句柄用不了"，而且换台机器就不复现。
_SIGNATURES = {
    "GetForegroundWindow": (c_void_p, None),
    "GetClassNameW": (c_int, [c_void_p, ctypes.c_wchar_p, c_int]),
    "GetWindowTextW": (c_int, [c_void_p, ctypes.c_wchar_p, c_int]),
    "GetWindowTextLengthW": (c_long, [c_void_p]),
    "IsWindowVisible": (c_bool, [c_void_p]), "IsWindow": (c_bool, [c_void_p]),
    "SetForegroundWindow": (c_bool, [c_void_p]), "BringWindowToTop": (c_bool, [c_void_p]),
    "ShowWindow": (c_int, [c_void_p, c_int]),
    "GetWindowRect": (c_bool, [c_void_p, POINTER(wintypes.RECT)]),
    "OpenClipboard": (c_bool, [c_void_p]), "CloseClipboard": (c_bool, None),
    "EmptyClipboard": (c_bool, None),
    "IsClipboardFormatAvailable": (c_bool, [wintypes.UINT]),
    "GetClipboardData": (c_void_p, [wintypes.UINT]),
    "SetClipboardData": (c_void_p, [wintypes.UINT, c_void_p]),
    "SendInput": (c_uint, [c_uint, POINTER(_INPUT), c_int]),
    "SetCursorPos": (c_bool, [c_int, c_int]), "GetLastInputInfo": (c_bool, [POINTER(None)]),
    "GetDC": (c_void_p, [c_void_p]), "ReleaseDC": (c_int, [c_void_p, c_void_p]),
    "EnumWindows": (c_bool, [POINTER(None), c_long]),
}
for _name, (_rest, _args) in _SIGNATURES.items():
    _fn = getattr(USER32, _name)
    if _rest is not None:
        _fn.restype = _rest
    if _args is not None:
        _fn.argtypes = _args
KERNEL32.GlobalLock.restype = c_void_p
KERNEL32.GlobalLock.argtypes = [c_void_p]
KERNEL32.GlobalUnlock.argtypes = [c_void_p]
KERNEL32.GlobalAlloc.restype = c_void_p
KERNEL32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
KERNEL32.GetTickCount.restype = wintypes.DWORD
GDI32.CreateCompatibleDC.restype = c_void_p
GDI32.CreateCompatibleDC.argtypes = [c_void_p]
GDI32.CreateCompatibleBitmap.restype = c_void_p
GDI32.CreateCompatibleBitmap.argtypes = [c_void_p, c_int, c_int]
GDI32.SelectObject.restype = c_void_p
GDI32.SelectObject.argtypes = [c_void_p, c_void_p]
GDI32.BitBlt.restype = c_bool
GDI32.BitBlt.argtypes = [c_void_p, c_int, c_int, c_int, c_int, c_void_p,
                         c_int, c_int, wintypes.DWORD]
GDI32.GetDIBits.restype = c_int
GDI32.GetDIBits.argtypes = [c_void_p, c_void_p, c_uint, c_uint, c_void_p,
                            POINTER(_BITMAPINFOHEADER), c_uint]
GDI32.DeleteObject.argtypes = [c_void_p]
GDI32.DeleteDC.argtypes = [c_void_p]


# ---------------------------------------------------------------- 只读探测
def find_weixin_window() -> int | None:
    """微信主窗口句柄。没启动/没登录就是 None，**不抛异常** —— 调用方要能说人话。

    ⚠️ 这里不许兜底成"随便找个标题带微信的窗口"：小程序容器、托盘消息窗都叫 Weixin，
    按到那些窗口上等于把正文粘进一个看不见的地方，比不发更坏。
    """
    found = []
    cb_type = ctypes.WINFUNCTYPE(c_bool, c_void_p, c_long)

    def _each(hwnd, _lparam):
        n = USER32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        title = ctypes.create_unicode_buffer(n + 1)
        USER32.GetWindowTextW(hwnd, title, n + 1)
        if title.value not in WIN_TITLES:
            return True
        cls = ctypes.create_unicode_buffer(256)
        USER32.GetClassNameW(hwnd, cls, 256)
        if cls.value.startswith("Qt") and cls.value.endswith("QWindowIcon"):
            found.append(int(hwnd))
        return True

    USER32.EnumWindows(cb_type(_each), 0)
    return found[0] if found else None


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    if not hwnd or not USER32.GetWindowRect(c_void_p(int(hwnd)), byref(rect)):
        return (0, 0, 0, 0)
    return (rect.left, rect.top, rect.right, rect.bottom)


def foreground_hwnd() -> int:
    return int(USER32.GetForegroundWindow() or 0)


def idle_seconds() -> float | None:
    """键盘/鼠标空闲多久。**读不到返回 None（判不了），不降级成"空闲很久"** ——
    判不了就不放行：这条通道会在桌面上真按键盘，宁可拒发。"""
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not USER32.GetLastInputInfo(byref(info)):
        return None
    return (KERNEL32.GetTickCount() - info.dwTime) / 1000.0


def render_surface_ratio(rect: tuple[int, int, int, int]) -> float | None:
    """那块矩形里非黑点的比例；None = 取不到像素（判不了）。

    为什么用"是不是全黑"而不是"调用成不成功"当判据：实测无桌面时 `BitBlt` 是**成功**的，
    只是取回来一片 0 —— 拿返回值当判据就会永远以为一切正常。
    """
    left, top, right, bottom = rect
    w, h = max(0, int(right) - int(left)), max(0, int(bottom) - int(top))
    if w < 8 or h < 8:
        return None
    # 只取窗口中心 320x320 那块：整窗取色要为一次只读预检分配几 MB 位图，
    # 而"有没有渲染表面"是整屏性质 —— 中心一块全黑与整窗全黑是同一个结论。
    cx, cy = int(left) + w // 2 - 160, int(top) + h // 2 - 160
    w = h = min(w, h, 320)
    left, top = max(0, cx), max(0, cy)
    right, bottom = left + w, top + h
    screen = USER32.GetDC(None)
    if not screen:
        return None
    mem = GDI32.CreateCompatibleDC(screen)
    bmp = GDI32.CreateCompatibleBitmap(screen, w, h)
    old = GDI32.SelectObject(mem, bmp)
    info = _BITMAPINFOHEADER()
    info.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    info.biWidth, info.biHeight, info.biPlanes, info.biBitCount = w, -h, 1, 32
    buf = ctypes.create_string_buffer(w * h * 4)
    rows = 0
    try:
        if not GDI32.BitBlt(mem, 0, 0, w, h, screen, int(left), int(top), BLIT_ROP):
            return None
        # ⚠️ `GetDIBits` 要求这张位图**此刻没有被 select 进任何 DC**。
        # 还 select 着就调它，返回值看着像成功、缓冲区却一片 0 —— 于是这道闸会把
        # "桌面好着"的机器误判成"没有渲染表面"（10-20 现场：截图里看得见任务栏和窗口，
        # 采样却报 0.0% 全黑）。所以先把位图摘下来，再读像素。
        GDI32.SelectObject(mem, old)
        rows = GDI32.GetDIBits(mem, bmp, 0, h, buf, byref(info), 0)
    finally:
        GDI32.DeleteObject(bmp)
        GDI32.DeleteDC(mem)
        USER32.ReleaseDC(None, screen)
    if rows == 0:
        return None        # 真取不到像素 ⇒「判不了」，与「取到了、确实全黑」是两件事
    data = buf.raw
    seen = lit = 0
    for y in range(0, h, SAMPLE_STEP):
        row = y * w * 4
        for x in range(0, w, SAMPLE_STEP):
            i = row + x * 4
            seen += 1
            if data[i] or data[i + 1] or data[i + 2]:
                lit += 1
    return lit / seen if seen else None


def clipboard_text() -> tuple[str | None, str]:
    """(文字, 形状)。形状 ∈ {'text','non_text','locked'}。

    ⚠️ 取句柄之后必须**先 `CloseClipboard` 再 `GlobalLock`**：那块内存属于剪贴板所有者，
    锁的顺序错了每次都回 NULL，表现成"原内容不是纯文本"，把用户的真剪贴板白白丢掉。
    """
    if not _clip_open():
        return None, "locked"
    try:
        if not USER32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None, "non_text"
        handle = USER32.GetClipboardData(CF_UNICODETEXT)
    finally:
        USER32.CloseClipboard()
    if not handle:
        return None, "non_text"
    ptr = KERNEL32.GlobalLock(handle)
    if not ptr:
        return None, "non_text"
    try:
        return ctypes.wstring_at(ptr), "text"
    finally:
        KERNEL32.GlobalUnlock(handle)


def _clip_open() -> bool:
    for _ in range(20):        # 别的程序正占着剪贴板时重试，别一上来就当"没内容"
        if USER32.OpenClipboard(None):
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------- 预检（只读）
def preflight(cfg: dict | None = None, *, hwnd: int | None = None) -> dict:
    """发送前那道口：**全部只读**，任何必检项不过就拒发，并把每一项的实情交出去。

    写成"每项都出一条"而不是"第一个失败就返回"：用户要问的是
    「现在这台机器为什么发不出去」，一句 `失败` 等于让人从头猜。
    """
    cfg = cfg or WC.load()
    guards = {**WC.default_guards(), **(cfg.get("guards") or {})}
    checks: list[dict] = []

    def add(key, label, ok, detail, *, required=True):
        checks.append({"key": key, "label": label, "ok": bool(ok), "detail": detail,
                       "required": required})

    if hwnd is None:
        hwnd = find_weixin_window() or 0
    if guards["require_window"]:
        add("window", "微信主窗口在不在", bool(hwnd and USER32.IsWindow(c_void_p(int(hwnd)))),
            f"在，句柄 {hwnd}" if hwnd else
            "没找到标题为「微信」的 Qt 主窗口 —— 客户端没启动，或没登录")
    else:
        add("window", "微信主窗口在不在", True,
            "这道闸被关掉了：窗口不在时按键会打进你当前正在用的程序里", required=False)

    fg = foreground_hwnd()
    if guards["require_input_desktop"]:
        add("input_desktop", "这个会话有没有输入桌面", fg != 0,
            f"有（前台窗口 {fg}）" if fg else
            "GetForegroundWindow() 回 0 —— 远程桌面最小化或断开了，"
            "这个会话现在没有输入桌面，任何按键都会被系统拒绝（实测 SendInput 回拒绝访问）")
    else:
        add("input_desktop", "这个会话有没有输入桌面", True,
            "这道闸被关掉了：无桌面时按键不会送达，只会留下一条「按过了」的假记录",
            required=False)

    if guards["require_render_surface"] and hwnd:
        # 采样本身是 GDI 调用，异常形状（句柄、位深、权限）都见过；这里**接住并如实写成"取不到"**，
        # 绝不让读数的 GET 接口因为一次取色失败而 500 —— 但那句 detail 会带上异常名，
        # 不是兜成"没问题"，也不是兜成"没问题但空值"。
        try:
            ratio = render_surface_ratio(window_rect(int(hwnd)))
            error = ""
        except Exception as exc:
            ratio, error = None, f"（{type(exc).__name__}）"
        if ratio is None:
            add("render_surface", "屏幕有没有在渲染", False,
                "取不到像素" + error + " —— 判不了就拒发（读不到渲染面还按下，等于盲按）")
        else:
            add("render_surface", "屏幕有没有在渲染", ratio >= RENDER_MIN_RATIO,
                f"窗口区域内非黑采样点 {ratio:.1%}（阈值 {RENDER_MIN_RATIO:.1%}）"
                + ("" if ratio >= RENDER_MIN_RATIO else " —— 全黑意味着这个会话没有渲染表面"))
    else:
        add("render_surface", "屏幕有没有在渲染", True,
            "这道闸关着（或窗口不在），本次没有采样", required=False)

    need = int(guards["min_idle_seconds"])
    idle = idle_seconds()
    if need <= 0:
        add("idle", "键盘空闲够不够", True, "设成 0 = 不查（发送会与人抢键盘）", required=False)
    elif idle is None:
        add("idle", "键盘空闲够不够", False, f"读不到空闲时长（判不了 ⇒ 不发），门槛 {need} 秒")
    else:
        add("idle", "键盘空闲够不够", idle >= need,
            f"现在空闲 {idle:.0f} 秒，门槛 {need} 秒"
            + ("" if idle >= need else " —— 这台电脑正有人在用，不插队"))

    cd = int(guards["cooldown_seconds"])
    gap = seconds_since_last_send()
    if cd <= 0:
        add("cooldown", "离上一条真发送够不够久", True, "设成 0 = 不查", required=False)
    elif gap is None:
        add("cooldown", "离上一条真发送够不够久", True, "还没有过真发送的记录", required=False)
    else:
        add("cooldown", "离上一条真发送够不够久", gap >= cd,
            f"离上一条 {gap:.0f} 秒，门槛 {cd} 秒"
            + ("" if gap >= cd else " —— 两条之间要隔开，免得两个调用方同时抢桌面"))

    add("enabled", "这条通道开没开", bool(cfg.get("enabled")),
        "开着" if cfg.get("enabled") else
        "「个人微信推送」的总开关是关着的（在系统设置里打开才会真发）")
    why = WC.validate_payload(cfg)
    add("config", "配置本身合不合法", why is None, why or "每一步、每道闸都读得通")

    blocked = [c for c in checks if c["required"] and not c["ok"]]
    return {"ok": not blocked, "blocked": [c["key"] for c in blocked], "checks": checks,
            "foreground": fg, "hwnd": int(hwnd), "idle_seconds": idle,
            "checked_at": WC.dt_now(),
            "reason": (None if not blocked else
                       "没发出去，因为：" + "；".join(f"{c['label']}——{c['detail']}"
                                                     for c in blocked))}


def seconds_since_last_send() -> float | None:
    """离最近一次**真按过键盘**有多少秒；None = 没有过。

    只数 `status=injected` 的行：拒发、预检没过、dry_run 都**不算一次发送** ——
    按失败次数算冷却会让"连着试了三次没出去"把第四次也锁住，那是自己把自己锁死。
    """
    for row in history(20):
        if str(row.get("status")) != "injected":
            continue
        try:
            prev = datetime.strptime(str(row.get("at") or ""), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        return max(0.0, (datetime.now() - prev).total_seconds())
    return None


# ---------------------------------------------------------------- 正文组装
def render_template(tpl: dict, *, date: str = "") -> dict:
    """内容档 → 要发的那段文字。**只算不发**，dry_run 与面板预览都走这里。

    scene 档直接复用四个渠道那份组装器（`duanxian.notify.compose`）：同一条
    「自选股当日明细」在企微/飞书/微信里必须是同一段字，两处各算一遍的话，
    第二天一定有人说"手机上和微信里对不上"。
    """
    from duanxian import notify
    src = str(tpl.get("source") or "text")
    day = date or notify._session_date("")
    if src == "text":
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        body = str(tpl.get("text") or "").replace("{now}", now).replace("{date}", day or "")
        return {"ok": True, "body": body.strip(), "meta": {"source": "text"}, "date": day}
    if src == "scene":
        title, body, meta = notify.compose(tpl.get("scenes") or {}, day,
                                           title_prefix=str(tpl.get("title") or ""))
        meta = dict(meta)
        meta["source"] = "scene"
        meta["title"] = title
        return {"ok": True, "body": body.strip(), "meta": meta, "date": day}
    out = run_script(str(tpl.get("script") or ""), timeout=int(tpl.get("timeout_seconds") or 30))
    if not out["ok"]:
        return {"ok": False, "reason": out["reason"], "body": "", "meta": out, "date": day}
    return {"ok": True, "body": out["text"],
            "meta": {"source": "script", "exit": out["exit"]}, "date": day}


def run_script(rel: str, *, timeout: int = 30) -> dict:
    """内容档 `source=script` 那条路：跑仓库内的一个 .py，把 stdout 当正文。

    ⚠️ 默认实现在 pytest 进程里硬挡（与 `_desktop_apply` 同一道缝）：
    跑一次全量测试就起一个真进程，而那个进程完全可能去动桌面。
    路径**只收仓库内相对路径**，`resolve()` 之后还要仍落在仓库里 —— 这条通道不该
    变成"读任意文件"的口子。
    """
    if os.environ.get("PYTEST_CURRENT_TEST") and run_script is _RUN_SCRIPT_DEFAULT:
        raise RuntimeError("pytest 进程里不许起真脚本 —— 请 monkeypatch duanxian.wxpush.run_script")
    root = Path(__file__).resolve().parent.parent
    target = (root / str(rel)).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        return {"ok": False, "reason": f"脚本不在仓库里（或不存在）：{rel}",
                "text": "", "exit": -1}
    try:
        proc = subprocess.run([sys.executable, str(target)], capture_output=True,
                              timeout=timeout, cwd=str(root))
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": f"脚本跑了 {timeout} 秒还没回（按失败算，不等第二次）",
                "text": "", "exit": -1}
    except OSError as exc:
        return {"ok": False, "reason": f"脚本起不来：{type(exc).__name__} {exc}",
                "text": "", "exit": -1}
    text = (proc.stdout or b"").decode("utf-8", "replace").strip()
    err = (proc.stderr or b"").decode("utf-8", "replace").strip()
    if proc.returncode != 0 or not text:
        tail = err.splitlines()[-1][:160] if err else "（stderr 为空）"
        return {"ok": False,
                "reason": f"脚本退出码 {proc.returncode}，stdout {'是空的' if not text else '有内容'}；"
                          f"stderr 尾行：{tail}",
                "text": "", "exit": int(proc.returncode)}
    return {"ok": True, "reason": None, "text": text, "exit": 0}


_RUN_SCRIPT_DEFAULT = run_script


def build_body(*, text: str = "", template_key: str = "", scenes: dict | None = None,
               title: str = "", date: str = "", cfg: dict | None = None) -> dict:
    """进正文的三条路收成一处：`template` 指内容档、`scenes` 直接点档、`text` 直给。"""
    cfg = cfg or WC.load()
    if template_key:
        tpl = WC.find_template(template_key, cfg)
        keys = "、".join(str(t.get("key")) for t in cfg.get("templates") or []) or "一条都还没配"
        if tpl is None:
            return {"ok": False, "reason": f"没有键为 {template_key!r} 的内容档（可用：{keys}）"}
        if not tpl.get("enabled", True):
            return {"ok": False, "reason": f"内容档 {template_key} 是关着的"}
        if title and tpl.get("source") == "scene":
            tpl = {**tpl, "title": title}
    elif scenes:
        tpl = {"key": "-", "label": "临时内容", "source": "scene",
               "scenes": scenes, "title": title}
    elif str(text or "").strip():
        tpl = {"key": "-", "label": "临时内容", "source": "text", "text": str(text)}
    else:
        return {"ok": False, "reason": "没有要发的内容：template / scenes / text 至少给一个"}
    out = render_template(tpl, date=date)
    if not out["ok"]:
        return out
    body = out["body"]
    cap = int({**WC.default_guards(), **(cfg.get("guards") or {})}.get("max_body_chars") or 1200)
    if not body:
        return {"ok": False, "reason": "内容算出来是空的（这一档今天没取到东西）⇒ 不发空白"}
    if len(body) > cap:
        return {"ok": False, "body_chars": len(body), "limit": cap,
                "reason": f"正文 {len(body)} 字，超过「正文长度上限」{cap} 字 —— "
                          "超长的那一段会被客户端**静默截断**（截在哪它不说），所以这里拒发。"
                          "少勾几档内容，或把那格调大"}
    return {"ok": True, "body": body, "meta": out.get("meta") or {},
            "date": out.get("date", ""), "chars": len(body)}


# ---------------------------------------------------------------- 动作序列（纯算）
def build_ops(cfg: dict, *, to: str, body: str, hwnd: int) -> tuple[list[dict], list[str]]:
    """把 12 步配置展开成动作序列。纯函数：不打键盘、不碰剪贴板、不动窗口。

    每一步之前插一个 `verify_foreground` —— 这不是装饰：中途焦点一旦被对话框或
    别人的鼠标动作抢走，后面的 Ctrl+A / Delete / 粘贴就会**作用在别人的输入框里**。
    序列自带 `label`，`dry_run` 原样交出去 ⇒ 界面上看得见"到底要按什么"。
    """
    steps = {str(s["key"]): s for s in cfg.get("steps") or []}
    ops: list[dict] = []
    notes: list[str] = []
    win_l, win_t, win_r, win_b = window_rect(int(hwnd)) if hwnd else (0, 0, 0, 0)
    if not hwnd:
        notes.append("微信窗口不在：坐标类步骤按 (0,0) 算，只能看流程、不能真发")

    def emit_wait(step: dict) -> None:
        ms = int(step.get("wait_ms", 0) or 0)
        if ms > 0:
            ops.append({"op": "wait", "ms": ms, "label": f"等 {ms} 毫秒"})

    def emit_keys(value: str, label: str) -> None:
        ops.append({"op": "keys", "seq": WC.to_vk_seq(value),
                    "label": f"{label}（{WC.combo_label(value)}）", "combo": value})

    def emit_check() -> None:
        ops.append({"op": "verify_foreground", "label": "核对前台就是微信窗口"})

    def step_of(key: str) -> dict | None:
        s = steps.get(key) or {}
        return s if s.get("enabled", True) else None

    s = step_of("open_window")
    if s:
        mode = s.get("mode")
        if mode in ("hotkey", "hotkey_then_restore"):
            emit_keys(s.get("value") or "alt+1", "按唤起快捷键")
            emit_wait(s)
        if mode in ("restore", "hotkey_then_restore"):
            ops.append({"op": "window", "action": "restore", "label": "把微信窗口唤出来"})
        ops.append({"op": "window", "action": "focus", "label": "置为前台窗口"})
        if mode == "hotkey_then_restore":
            notes.append("唤起走的是「先按快捷键、再直接唤窗」：结果里 notes 会说明做过哪一手")

    s = step_of("maximize")
    if s:
        if s.get("mode") == "maximize":
            ops.append({"op": "window", "action": "maximize", "label": "最大化窗口"})
            notes.append("最大化开着：窗口会挪位，坐标类那两格要跟着重量")
        elif s.get("mode") == "hotkey":
            emit_keys(s.get("value") or "f11", "按全屏快捷键")
        emit_wait(s)

    s = step_of("search")
    if s:
        emit_check()
        emit_keys(s.get("value") or "ctrl+f", "打开搜索框")
        emit_wait(s)

    s = step_of("ime_english")
    if s and s.get("mode") not in ("none", "skip"):
        # 这一档要的是纯修饰键串（`shift` / `alt+shift`），走的不是组合键那条展开
        ops.append({"op": "keys", "seq": WC.mods_seq(str(s["mode"])),
                    "label": f"切输入法（{WC.mods_label(str(s['mode']))}）",
                    "combo": str(s["mode"])})
        emit_wait(s)

    s = step_of("type_target")
    if s:
        emit_check()
        if s.get("mode") == "paste":
            ops.append({"op": "clipboard", "text": to, "label": f"把「{to}」放进剪贴板"})
            emit_keys("ctrl+v", "粘贴搜索词")
        else:
            ops.append({"op": "type", "text": to, "label": f"逐字键入「{to}」"})
        emit_wait(s)

    s = step_of("pick_first")
    if s:
        emit_check()
        mode = s.get("mode")
        if mode == "enter":
            emit_keys("enter", "Enter 选中第一个结果")
        elif mode == "down_enter":
            emit_keys("down", "先按 ↓ 走到第一项")
            emit_keys("enter", "再 Enter")
        elif mode == "click_point":
            dx, dy = _point_of(s)
            ops.append({"op": "click", "x": win_l + dx, "y": win_t + dy, "offset": [dx, dy],
                        "label": f"点第一个搜索结果（窗口内偏移 {dx},{dy}）"})
        emit_wait(s)

    s = step_of("focus_input")
    if s:
        if s.get("mode") == "click_point":
            dx, dy = _point_of(s)
            ops.append({"op": "click", "x": win_l + dx, "y": win_t + dy, "offset": [dx, dy],
                        "label": f"点输入框（窗口内偏移 {dx},{dy}）"})
        elif s.get("mode") == "tab":
            emit_keys("tab", "Tab 移到输入框")
        emit_wait(s)

    s_all, s_clear = step_of("select_all"), step_of("clear_old")
    clearing = s_clear and s_clear.get("mode") != "skip"
    if s_all and clearing:
        emit_check()
        emit_keys(s_all.get("value") or "ctrl+a", "全选输入框内容")
        emit_wait(s_all)
    if clearing:
        emit_keys(s_clear.get("value") or "delete", "删掉原有内容（⚠️ 含未发草稿）")
        emit_wait(s_clear)
    elif s_clear:
        notes.append("「删掉原内容」选了不清 ⇒ 正文会接在你原来那段草稿后面")

    s = step_of("paste_body")
    if s:
        emit_check()
        if s.get("mode") == "paste":
            ops.append({"op": "clipboard", "text": body, "label": "把正文放进剪贴板"})
            emit_keys("ctrl+v", "粘贴正文")
        else:
            ops.append({"op": "type", "text": body, "label": "逐字键入正文"})
        emit_wait(s)

    s = step_of("enter_send")
    if s:
        emit_check()
        emit_keys(s.get("value") or "enter", "回车发送")
        emit_wait(s)

    s = step_of("close_window")
    if s:
        mode = s.get("mode")
        if mode in ("hotkey", "hotkey_then_hide"):
            emit_keys(s.get("value") or "alt+1", "按收起快捷键")
        if mode in ("hide", "hotkey_then_hide"):
            ops.append({"op": "window", "action": "hide_if_was_hidden",
                        "label": "收起窗口（只在发送前本来就藏着时）"})
    ops.append({"op": "clipboard_restore", "label": "还原剪贴板"})
    return ops, notes


def _point_of(step: dict) -> tuple[int, int]:
    pt = step.get("point") or [0, 0]
    return int(pt[0]), int(pt[1])


# ---------------------------------------------------------------- 真副作用（唯一那道缝）
def _send_key(vk: int, up: bool) -> None:
    pkt = _INPUT(type=INPUT_KEYBOARD,
                 union=_INPUT._U(ki=_KEYBDINPUT(wVk=vk, wScan=0,
                                                dwFlags=(KEYEVENTF_KEYUP if up else 0),
                                                time=0, dwExtraInfo=None)))
    if USER32.SendInput(1, byref(pkt), ctypes.sizeof(_INPUT)) != 1:
        raise RuntimeError(f"SendInput 被系统拒绝（键码 {vk:#04x}，错误码 "
                           f"{ctypes.get_last_error()}）—— 会话没有输入桌面、"
                           "或前台是别人的窗口时就是这个形状")


def _send_text(text: str) -> None:
    """逐字键入用 `KEYEVENTF_UNICODE` 直接送 UTF-16 单元：**不经过输入法**，
    所以开着中文输入法时也不会把中文打成拼音。代理对（emoji）按两个单元连送。"""
    for ch in text:
        raw = ch.encode("utf-16-le")
        for i in range(0, len(raw), 2):
            unit = int.from_bytes(raw[i:i + 2], "little")
            down = _INPUT(type=INPUT_KEYBOARD,
                          union=_INPUT._U(ki=_KEYBDINPUT(wVk=0, wScan=unit,
                                                         dwFlags=KEYEVENTF_UNICODE,
                                                         time=0, dwExtraInfo=None)))
            if USER32.SendInput(1, byref(down), ctypes.sizeof(_INPUT)) != 1:
                raise RuntimeError(f"逐字输入 {ch!r} 被拒（错误码 {ctypes.get_last_error()}）")
            up = _INPUT(type=INPUT_KEYBOARD,
                        union=_INPUT._U(ki=_KEYBDINPUT(wVk=0, wScan=unit,
                                                       dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                                                       time=0, dwExtraInfo=None)))
            USER32.SendInput(1, byref(up), ctypes.sizeof(_INPUT))
        time.sleep(0.008)


def _send_click(x: int, y: int) -> None:
    if not USER32.SetCursorPos(int(x), int(y)):
        raise RuntimeError(f"光标移不到 ({x},{y})（错误码 {ctypes.get_last_error()}）")
    time.sleep(0.05)
    for flag in (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP):
        pkt = _INPUT(type=INPUT_MOUSE,
                     union=_INPUT._U(mi=_MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flag,
                                                    time=0, dwExtraInfo=None)))
        if USER32.SendInput(1, byref(pkt), ctypes.sizeof(_INPUT)) != 1:
            raise RuntimeError(f"SendInput(鼠标 flag={flag:#x}) 被拒（错误码 "
                               f"{ctypes.get_last_error()}）")
        time.sleep(0.03)


def _clip_write(text: str) -> None:
    if not _clip_open():
        raise RuntimeError("剪贴板被别的程序占着，打不开（不重试第二回，那是在抢桌面）")
    try:
        USER32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = KERNEL32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise RuntimeError("剪贴板内存分配失败")
        ptr = KERNEL32.GlobalLock(handle)
        if not ptr:
            raise RuntimeError("剪贴板内存锁不住")
        try:
            ctypes.memmove(ptr, data, len(data))
        finally:
            KERNEL32.GlobalUnlock(handle)
        if not USER32.SetClipboardData(CF_UNICODETEXT, handle):
            raise RuntimeError(f"SetClipboardData 失败（错误码 {ctypes.get_last_error()}）")
    finally:
        USER32.CloseClipboard()


def _window_action(action: str, hwnd: int, was_visible: bool) -> str:
    if action == "restore":
        USER32.ShowWindow(c_void_p(int(hwnd)), SW_RESTORE)
        return "已唤出窗口"
    if action == "maximize":
        USER32.ShowWindow(c_void_p(int(hwnd)), SW_MAXIMIZE)
        return "已最大化"
    if action == "focus":
        USER32.BringWindowToTop(c_void_p(int(hwnd)))
        USER32.SetForegroundWindow(c_void_p(int(hwnd)))
        return "已置前台"
    if action == "hide_if_was_hidden":
        if was_visible:
            return "发送前窗口本来就开着 ⇒ 保持开着"
        USER32.ShowWindow(c_void_p(int(hwnd)), SW_HIDE)
        return "已收起（发送前本来就是藏着的）"
    return action


def _desktop_apply(ops: list[dict], *, hwnd: int, was_visible: bool) -> dict:
    """**这台机器上唯一真按键盘/动鼠标/写剪贴板的函数**。测试必须 monkeypatch 这里。

    默认实现在 pytest 进程里硬挡：跑一次全量测试就在这台机器的桌面上真敲一轮键，
    与 10-06 那次「pytest 在用户桌面启动通达信」是同一类越界（见《06-自动执行机制自查表》）。
    """
    if os.environ.get("PYTEST_CURRENT_TEST") and _desktop_apply is _APPLY_DEFAULT:
        raise RuntimeError("pytest 进程里不许在桌面上真按键 —— 请 monkeypatch "
                           "duanxian.wxpush._desktop_apply")
    done, notes = [], []
    for op in ops:
        kind = op["op"]
        if kind == "wait":
            time.sleep(max(0, int(op["ms"])) / 1000.0)
        elif kind == "verify_foreground":
            now = foreground_hwnd()
            if now != int(hwnd):
                return {"ok": False, "aborted_at": op["label"],
                        "reason": f"前台窗口已经不是微信了（现在 {now}，要 {hwnd}）⇒ 当场停手："
                                  "后面的全选、删除、粘贴绝不能打进别的程序里",
                        "done": done, "notes": notes}
        elif kind == "keys":
            for vk, up in op["seq"]:
                _send_key(int(vk), bool(up))
        elif kind == "type":
            _send_text(str(op["text"]))
        elif kind == "click":
            _send_click(int(op["x"]), int(op["y"]))
        elif kind == "clipboard":
            _clip_write(str(op["text"]))
        elif kind == "clipboard_restore":
            pass          # 序列自带这一格是为了让 dry_run 看得见尾巴；真还原在 send() 的 finally
        elif kind == "window":
            notes.append(_window_action(op["action"], int(hwnd), was_visible))
        done.append(op["label"])
    return {"ok": True, "aborted_at": None, "reason": None, "done": done, "notes": notes}


_APPLY_DEFAULT = _desktop_apply


# ---------------------------------------------------------------- 发送
def send(*, to: str = "", text: str = "", template: str = "", scenes: dict | None = None,
         title: str = "", date: str = "", dry_run: bool = False,
         source: str = "api", via: str = "") -> dict:
    """这条通道对外的唯一入口：面板的「试发一条」与别的程序调 `/api/wxpush/send` 都走这里。

    `dry_run=True` ⇒ 只算正文 + 跑预检 + 展开动作序列，**一个键都不按**：
    这是"别的程序要不要接这条通道"的第一步，也是测试唯一允许的形状。
    """
    cfg = WC.load()
    started = time.time()
    target = str(to or cfg.get("default_to") or "").strip()
    if not target:
        return _journal({"ok": False, "status": "rejected", "stage": "收件人",
                         "reason": "没给收件人，「默认收件人」那一格也是空的 —— "
                                   "这条通道必须指名道姓（填谁就发给谁）",
                         "source": source}, via=via)
    body = build_body(text=text, template_key=template, scenes=scenes, title=title,
                      date=date, cfg=cfg)
    if not body["ok"]:
        return _journal({"ok": False, "status": "rejected", "stage": "正文",
                         "reason": body["reason"], "chars": body.get("body_chars"),
                         "to": target, "source": source}, via=via)
    pre = preflight(cfg)
    ops, notes = build_ops(cfg, to=target, body=body["body"], hwnd=pre["hwnd"])
    if dry_run:
        return {"ok": pre["ok"], "status": "dry_run", "to": target, "body": body["body"],
                "chars": len(body["body"]), "meta": body["meta"], "preflight": pre,
                "ops": [{"op": o["op"], "label": o["label"], "combo": o.get("combo"),
                         "offset": o.get("offset")} for o in ops],
                "op_count": len(ops), "notes": notes,
                "reason": None if pre["ok"] else pre["reason"],
                "elapsed_ms": int((time.time() - started) * 1000), "source": source}
    if not pre["ok"]:
        return _journal({"ok": False, "status": "blocked", "stage": "预检", "to": target,
                         "chars": len(body["body"]), "blocked": pre["blocked"],
                         "reason": pre["reason"], "source": source}, via=via)
    hwnd = pre["hwnd"]
    was_visible = bool(USER32.IsWindowVisible(c_void_p(int(hwnd))))
    saved, shape = clipboard_text()
    try:
        res = _desktop_apply(ops, hwnd=hwnd, was_visible=was_visible)
    except RuntimeError as exc:
        return _journal({"ok": False, "status": "failed", "stage": "注入", "to": target,
                         "chars": len(body["body"]), "reason": str(exc),
                         "window_was_visible": was_visible, "source": source}, via=via)
    restore = _restore_clipboard(cfg, saved, shape)
    out = {"ok": bool(res["ok"]),
           # 到这一步只敢说 injected：这一侧没有任何办法确认那条消息真进了会话
           "status": "injected" if res["ok"] else "aborted",
           "delivered": None, "to": target, "chars": len(body["body"]),
           "meta": body["meta"], "preflight": pre, "op_count": len(ops),
           "ops_done": res["done"], "aborted_at": res["aborted_at"],
           "notes": list(dict.fromkeys((res.get("notes") or []) + notes)),
           "window_was_visible": was_visible, "clipboard_restore": restore,
           "reason": res["reason"], "source": source,
           "elapsed_ms": int((time.time() - started) * 1000)}
    return _journal(out, via=via)


def _restore_clipboard(cfg: dict, saved: str | None, shape: str) -> dict:
    if not {**WC.default_guards(), **(cfg.get("guards") or {})}.get("restore_clipboard"):
        return {"done": False, "detail": "这一格关着 ⇒ 剪贴板里留的是刚发出去的那段正文"}
    if shape == "text" and saved is not None:
        try:
            _clip_write(saved)
            return {"done": True, "detail": f"已还原原来的 {len(saved)} 字"}
        except RuntimeError as exc:
            return {"done": False, "detail": f"没还原成功：{exc}"}
    if shape == "non_text":
        return {"done": False,
                "detail": "原剪贴板不是纯文本（图片/文件）⇒ **不动它**："
                          "覆盖掉你的图片比留一段粘贴文本更坏"}
    return {"done": False, "detail": "发送前就读不到剪贴板（被别的程序占着），没法还原"}


# ---------------------------------------------------------------- 留痕
def log_history(row: dict) -> None:
    """成败都记（照 `notify.log_history`）。**不记正文全文**，只记长度与头 40 字。"""
    path = Path(WC.history_path())
    slim = {k: v for k, v in row.items() if k not in ("body", "preflight")}
    slim["checks_failed"] = [c["key"] for c in (row.get("preflight") or {}).get("checks", [])
                             if not c["ok"]]
    if isinstance(row.get("body"), str):
        slim["body_head"] = row["body"][:40]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(slim, ensure_ascii=False) + "\n")
    except OSError:
        pass      # 留痕写不下去不该把发送本身弄成异常


def history(limit: int = HISTORY_LIMIT) -> list[dict]:
    try:
        lines = Path(WC.history_path()).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
        if len(out) >= limit:
            break
    return out


def _journal(out: dict, *, via: str = "") -> dict:
    out = {**out, "at": out.get("at") or WC.dt_now(), "via": via or None}
    log_history(out)
    return out
