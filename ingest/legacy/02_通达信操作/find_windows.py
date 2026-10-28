# -*- coding: utf-8 -*-
"""查找并关闭标题为'通达信信息'的窗口"""
import ctypes
import ctypes.wintypes as wt

user32 = ctypes.windll.user32

# 枚举窗口回调
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

found_windows = []

def enum_callback(hwnd, lparam):
    # 获取窗口标题
    length = user32.GetWindowTextLengthW(hwnd)
    if length > 0:
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        # 检查是否可见
        is_visible = user32.IsWindowVisible(hwnd)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found_windows.append((hwnd, title, pid.value, is_visible))
    return True

user32.EnumWindows(WNDENUMPROC(enum_callback), 0)

print(f"共找到 {len(found_windows)} 个有标题的窗口")
print("=== 包含'通达信'的窗口 ===")
for hwnd, title, pid, visible in found_windows:
    if '通达信' in title or '信息' in title:
        print(f"  hwnd={hwnd}, pid={pid}, visible={visible}, title='{title}'")
