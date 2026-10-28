# -*- coding: utf-8 -*-
"""查看指定 hwnd 窗口详情 + 当前前台窗口"""
import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.windll.user32

hwnd_target = int(sys.argv[1], 0) if len(sys.argv) > 1 else None

# 前台窗口
fg = user32.GetForegroundWindow()
print(f'前台窗口: {fg}')

def get_info(hwnd):
    if not hwnd:
        return
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    print(f'  hwnd={hwnd} title="{buf.value}" class={cls.value} pid={pid.value} '
          f'rect=({rect.left},{rect.top})-({rect.right},{rect.bottom}) visible={bool(user32.IsWindowVisible(hwnd))}')

print('前台窗口信息:')
get_info(fg)
if hwnd_target:
    print(f'目标窗口 {hwnd_target} 信息:')
    get_info(hwnd_target)

# 前台窗口的子窗口
if fg:
    print('\n前台窗口的子窗口:')
    ENUM_CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    children = []
    def cb(h, _):
        children.append(h)
        return True
    user32.EnumChildWindows(fg, ENUM_CB(cb), 0)
    for h in children[:20]:
        get_info(h)
