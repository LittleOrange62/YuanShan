# -*- coding: utf-8 -*-
"""多种方式关闭窗口"""
import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.windll.user32

WM_CLOSE = 0x0010
WM_SYSCOMMAND = 0x0112
SC_CLOSE = 0xF060

hwnd = 2955174  # '通达信信息' 窗口

# 方法1: SendMessage 同步发送 WM_CLOSE
result = user32.SendMessageW(hwnd, WM_CLOSE, 0, 0)
print(f"方法1 SendMessage WM_CLOSE: {result}")

time.sleep(1)
# 检查窗口是否还存在
length = user32.GetWindowTextLengthW(hwnd)
if length == 0:
    print("✅ 窗口已关闭 (GetWindowTextLength=0)")
else:
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    print(f"窗口仍在, 标题='{buf.value}'")

    # 方法2: 发送 WM_SYSCOMMAND SC_CLOSE (系统关闭命令，等效于点X按钮)
    result2 = user32.SendMessageW(hwnd, WM_SYSCOMMAND, SC_CLOSE, 0)
    print(f"方法2 SendMessage WM_SYSCOMMAND SC_CLOSE: {result2}")
    time.sleep(1)

    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        print("✅ 方法2成功，窗口已关闭")
    else:
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        print(f"窗口仍在, 标题='{buf.value}'")

        # 方法3: 尝试激活窗口并模拟回车/ESC
        print("方法3: 尝试激活窗口...")
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        time.sleep(0.5)
        # 发送 ESC
        user32.keybd_event(0x1B, 0, 0, 0)  # ESC
        user32.keybd_event(0x1B, 0, 2, 0)  # ESC release
        time.sleep(0.5)
        # 发送回车
        user32.keybd_event(0x0D, 0, 0, 0)  # ENTER
        user32.keybd_event(0x0D, 0, 2, 0)  # ENTER release
        time.sleep(1)

        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            print("✅ 方法3成功，窗口已关闭")
        else:
            print(f"⚠️ 所有方法均未能关闭窗口, 标题仍为 '{ctypes.create_unicode_buffer(length+1).value}'")

# 再次确认
import subprocess
r = subprocess.run(['python', r'C:\Users\Administrator\.qwenpaw\workspaces\tq\scripts\find_windows.py'], capture_output=True, text=True)
print("\n=== 重新枚举窗口 ===")
print(r.stdout)
