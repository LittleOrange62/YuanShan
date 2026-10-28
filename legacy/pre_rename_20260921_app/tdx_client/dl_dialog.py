# -*- coding: utf-8 -*-
"""
「盘后数据下载」对话框的操作原语。**这一个文件是这条链路的全部实测知识。**

2026-08-31 把整条 GUI 路跑通了（日线 10s、1 分钟线 9,542 只 4.5min、
5 分钟线 9,542 只 3min），把当时管用的做法固化在这里，s6/s7 都调它，
不各写一遍。

---- 五条硬结论，改代码前先读 ----

1. **必须先最大化主窗口。** 窗口不满屏时通达信的自绘菜单栏会把后面几项
   折叠进 `▼`，`TQ策略` 就消失了。之前误判成"菜单里没有 TQ策略"，
   实际是折叠了。最大化后菜单栏是
   `行情 市场 资讯 数据 发现 问小达 财富圈 TQ策略 交易`，TQ策略 是第 8 项。

2. **顶部菜单只能用坐标点。** 菜单栏是自绘的：`GetMenu()` 返回 0、
   1,481 个子控件里没有一个带菜单文字、UIA dump 是空的。
   但**下拉出来的是标准 `#32768` 菜单窗口**，所以"点开了没有"这件事
   可以用"有没有多出 `#32768` 顶层窗口"来判，不用猜。

3. **对话框里的控件全是标准 Win32，有稳定 ctrl_id。** 所以从
   「盘后数据下载」这一层开始就不再依赖坐标和文字，全部按 id 操作
   （见 `CTRL`）。文字匹配留给菜单那两下，那是没办法的地方。

4. **切 Tab 页只能用鼠标点标签，`TCM_SETCURSEL` 会把通达信搞崩。**
   实测：`TCM_SETCURSEL` 之后 `TCM_GETCURSEL` 返回 1、标签也高亮了，
   但 `IsWindowVisible` 显示内容区还是第 1 页 —— 控件状态和对话框内部
   状态不一致，随后整个进程崩了（「通达信应用程序 已停止工作」）。
   **判据用 `IsWindowVisible(目标复选框)`**，不是 `TCM_GETCURSEL`：
   后者只反映标签高亮。

5. **完成判据读状态栏（id=1224），不是等窗口消失。** 状态栏会依次给出
   `正在下载当天行情数据..` → `正在转档盘后数据...` → `下载完毕.`，
   分钟线还带进度 `正在下载1分钟线数据..[20260831-20260831] 5850/9542`。
   有了这个就不需要"等 10 分钟"这种经验值，也能实时回显进度。
"""
from __future__ import annotations

import time

from app.tdx_client import win32

#: 对话框标题。**只认这一个** —— 「TQ数据设置」是它的上一层，别混。
TITLE = '盘后数据下载'

#: 上一层的设置窗，`盘后数据下载` 按钮在它里面。
SETTINGS_TITLE = 'TQ数据设置'
SETTINGS_BUTTON = '盘后数据下载'

#: 对话框里的控件 id。2026-08-31 实测，五个 tab 的控件**同时存在于控件树里**，
#: 靠 `IsWindowVisible` 区分当前在哪一页 —— 所以枚举得到不等于点得到。
CTRL = {
    'day': 1424,            # 沪深京日线页：日线和实时行情数据
    'day_all': 1420,        # 同页：下载所有A股类品种的日线数据（默认已勾且置灰）
    'min1': 1425,           # 沪深京分钟线页：1分钟线数据
    'min5': 1426,           # 同页：5分钟线数据
    'intraday': 1423,       # 沪深京分时图页：当日分时图数据
    'ext_day': 1427,        # 扩展市场行情日线页：日线数据
    'ext_min1': 1428,       # 扩展市场行情分钟线页：1分钟线数据
    'ext_min5': 1429,       # 同页：5分钟线数据
    'date_from': 1237,      # 起始日期
    'date_to': 1238,        # 结束日期
    'start': 1,             # 开始下载
    'close': 2,             # 关闭
    'status': 1224,         # 状态栏（进度文字在这里）
    'progress': 501,        # 进度条
    'tab': 32954,           # SysTabControl32
}

#: 状态栏里代表"这一遍跑完了"的字样。
DONE_MARK = '下载完毕'

#: Tab 标签行相对 tab 控件顶边的偏移。标准 tab 标签高 ~22px，取中间。
TAB_LABEL_DY = 12
#: 扫描标签时的横向步长。标签宽度随文字长度变，所以宁可多点几下。
TAB_SCAN_STEP = 20


def find(pid: int | None = None) -> win32.Win | None:
    """找「盘后数据下载」对话框。"""
    for w in win32.top_windows():
        if pid is not None and w.pid != pid:
            continue
        if w.text == TITLE and w.w > 200 and w.h > 120:
            return w
    return None


def find_settings(pid: int | None = None) -> win32.Win | None:
    """找上一层的「TQ数据设置」窗口。"""
    for w in win32.top_windows():
        if pid is not None and w.pid != pid:
            continue
        if w.text == SETTINGS_TITLE:
            return w
    return None


def controls(hwnd: int) -> dict[int, win32.Win]:
    """
    `{ctrl_id: Win}`。**每次都重新枚举** —— 切页之后 visible 会变，
    缓存下来的快照会骗人。
    """
    return {k.ctrl_id: k for k in win32.children(hwnd, max_depth=8)
            if k.ctrl_id}


def status_text(hwnd: int) -> str:
    """状态栏当前文字。对话框已经关掉时返回空串而不是抛。"""
    c = controls(hwnd).get(CTRL['status'])
    return win32.window_text(c.hwnd) if c else ''


def page_of(hwnd: int) -> str | None:
    """
    当前停在哪一页。判据是**哪个复选框可见**，不是 `TCM_GETCURSEL`。
    认不出来返回 None（比猜一个页名安全）。
    """
    ctl = controls(hwnd)
    for name in ('day', 'min1', 'intraday', 'ext_day', 'ext_min1'):
        c = ctl.get(CTRL[name])
        if c is not None and win32.is_visible(c.hwnd):
            return {'min1': 'minute', 'ext_min1': 'ext_minute'}.get(name, name)
    return None


def switch_tab(hwnd: int, want: str, *, log=None) -> tuple[bool, str]:
    """
    切到含 `want`（`CTRL` 的键名）那个控件的页，**用鼠标点 Tab 标签**。

    先扫标签行找到能让目标控件可见的那一格。扫描而不是写死坐标：
    标签宽度随文字长度变，写死的 x 换个版本就偏了。
    在标签行上点的副作用只有"切到别的页"，不会触发下载 —— 所以扫得起。

    `TCM_SETCURSEL` 那条路**刻意不实现**，理由见模块说明第 4 条。
    """
    cid = CTRL[want]

    def landed() -> bool:
        c = controls(hwnd).get(cid)
        return c is not None and win32.is_visible(c.hwnd)

    if landed():
        return True, f'已经在 {want} 所在的页'

    tab = controls(hwnd).get(CTRL['tab'])
    if tab is None:
        return False, f'对话框里没有 id={CTRL["tab"]} 的 Tab 控件'
    left, top, right, _ = win32.rect_of(tab.hwnd)
    y = top + TAB_LABEL_DY

    tried = []
    for x in range(left + 10, right - 10, TAB_SCAN_STEP):
        ok, why = win32.click_point(x, y)
        if not ok:
            return False, f'点 ({x},{y}) 失败：{why}'
        time.sleep(0.35)
        if landed():
            return True, f'点 Tab 标签 ({x},{y}) 后 {want} 可见'
        tried.append(x)
    return False, (f'把标签行 y={y} 上的 {len(tried)} 个位置都点过了，'
                   f'{want}(id={cid}) 始终不可见；'
                   f'Tab 控件 rect={win32.rect_of(tab.hwnd)}')


def set_boxes(hwnd: int, wants: dict[str, bool], *, log=None) -> tuple[bool, str]:
    """
    按 `{键名: 要不要勾}` 设复选框。**只动指定的那些**，别的不碰。

    复选框是 toggle，所以走 `win32.ensure_checked`（先读状态再决定点不点）。
    不可见的控件直接拒绝：它属于别的 tab 页，勾了不算数，
    而"勾了不算数"比"报错"难查得多。
    """
    ctl = controls(hwnd)
    msgs = []
    for name, want in wants.items():
        c = ctl.get(CTRL[name])
        if c is None:
            return False, f'找不到 {name}(id={CTRL[name]})'
        if not win32.is_visible(c.hwnd):
            return False, (f'{name}(id={CTRL[name]}) 当前不可见 —— '
                           f'它在别的 tab 页上，先 switch_tab')
        ok, why = win32.ensure_checked(c.hwnd, want)
        msgs.append(f'{name}={"勾" if want else "不勾"}({why})')
        if log:
            log(f'    {name}(id={CTRL[name]})：{why}')
        if not ok:
            return False, f'{name} 设不上：{why}'
    return True, '；'.join(msgs)


def start(hwnd: int) -> tuple[bool, str]:
    """点「开始下载」。用 `BM_CLICK`，不需要前台。"""
    c = controls(hwnd).get(CTRL['start'])
    if c is None:
        return False, f'找不到开始下载按钮(id={CTRL["start"]})'
    return win32.bm_click(c.hwnd), f'已点开始下载(hwnd={c.hwnd})'


def wait_done(hwnd: int, *, timeout: float, interval: float = 5.0,
              log=None) -> tuple[bool, str]:
    """
    盯状态栏直到出现「下载完毕」。

    **每次文字变化都回显**（分钟线那一遍会打出 `5850/9542` 这样的进度），
    这条链路一跑就是几分钟，没有回显的等待会被当成卡死。

    对话框中途被关掉 → 直接判失败并说清楚，不要continue 等到超时：
    人手动关窗和下载失败是两件事，混在一起会误导。
    """
    deadline = time.monotonic() + timeout
    last = None
    while True:
        if not win32.is_window(hwnd):
            return False, '对话框在下载过程中被关掉了'
        txt = status_text(hwnd)
        if txt != last:
            if log:
                log(f'    {txt or "(状态栏空)"}')
            last = txt
        if txt and DONE_MARK in txt:
            return True, txt
        if time.monotonic() >= deadline:
            return False, f'等待 {timeout:.0f}s 超时；状态栏停在 {txt!r}'
        time.sleep(interval)
