# -*- coding: utf-8 -*-
"""最小化通达信窗口 v3 - 多种方式尝试"""
import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.windll.user32

SW_MINIMIZE = 6
WM_SYSCOMMAND = 0x0112
SC_MINIMIZE = 0xF020
SC_CLOSE = 0xF060


def find_windows_by_title_keyword(keyword):
    results = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def enum_callback(hwnd, lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if keyword.lower() in title.lower():
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                visible = user32.IsWindowVisible(hwnd)
                results.append((hwnd, title, pid.value, visible))
        return True

    user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
    return results


def main():
    print("===== 最小化通达信 v3 =====")
    windows = find_windows_by_title_keyword('通达信')
    for hwnd, title, pid, visible in windows:
        print(f"  hwnd={hwnd}, visible={visible}, title='{title}'")

    # 找到主窗口（金融终端）
    main_hwnds = [w[0] for w in windows if '金融终端' in w[1] and w[3]]
    if not main_hwnds:
        print("未找到可见的通达信主窗口")
        return

    for hwnd in main_hwnds:
        print(f"\n处理主窗口 hwnd={hwnd}")
        # 方式A: ShowWindow SW_MINIMIZE（最小化到任务栏）
        r1 = user32.ShowWindow(hwnd, SW_MINIMIZE)
        print(f"  ShowWindow SW_MINIMIZE 返回: {r1}")
        time.sleep(1)
        visible = user32.IsWindowVisible(hwnd)
        print(f"  最小化后 visible={visible}")

        if visible:
            # 方式B: SC_MINIMIZE
            r2 = user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_MINIMIZE, 0)
            print(f"  PostMessageW SC_MINIMIZE 返回: {r2}")
            time.sleep(1)
            visible = user32.IsWindowVisible(hwnd)
            print(f"  SC_MINIMIZE 后 visible={visible}")

        if visible:
            # 方式C: 直接最小化（IsIconic 检查）
            iconic = user32.IsIconic(hwnd)
            print(f"  IsIconic（是否已最小化）: {iconic}")

    time.sleep(2)
    print("\n===== 最终状态 =====")
    windows2 = find_windows_by_title_keyword('通达信')
    for hwnd, title, pid, visible in windows2:
        iconic = user32.IsIconic(hwnd)
        print(f"  hwnd={hwnd}, visible={visible}, iconic={iconic}, title='{title}'")


if __name__ == '__main__':
    main()
