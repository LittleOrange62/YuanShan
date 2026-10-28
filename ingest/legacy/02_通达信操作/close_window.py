# -*- coding: utf-8 -*-
"""关闭指定标题的窗口"""
import ctypes
import ctypes.wintypes as wt

user32 = ctypes.windll.user32

WM_CLOSE = 0x0010

def find_window_by_title(target):
    """查找标题完全匹配的窗口"""
    results = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def enum_callback(hwnd, lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value == target:
                results.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
    return results

target = '通达信信息'
hwnds = find_window_by_title(target)
print(f"找到 {len(hwnds)} 个标题为 '{target}' 的窗口: {hwnds}")

for hwnd in hwnds:
    # 发送 WM_CLOSE
    result = user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    print(f"已向 hwnd={hwnd} 发送 WM_CLOSE, 返回: {result}")

# 等待1秒后确认是否关闭
import time
time.sleep(1)

hwnds_after = find_window_by_title(target)
print(f"关闭后剩余: {len(hwnds_after)} 个窗口 {hwnds_after}")
if not hwnds_after:
    print("✅ '通达信信息' 窗口已成功关闭")
else:
    print("⚠️ 窗口仍在，可能需要其他方式关闭")
