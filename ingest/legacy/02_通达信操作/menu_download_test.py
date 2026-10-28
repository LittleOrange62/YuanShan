# -*- coding: utf-8 -*-
"""测试: Alt+S 打开通达信系统菜单, Enter 触发第一项, 验证是否出现盘后数据下载对话框"""
import ctypes
import time

user32 = ctypes.windll.user32

VK_MENU = 0x12  # Alt
VK_S = 0x53
VK_RETURN = 0x0D
VK_DOWN = 0x28
VK_UP = 0x26
SW_RESTORE = 9

ENUM_CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def enum_windows():
    result = []

    def cb(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            result.append((hwnd, buf.value, bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(ENUM_CB(cb), 0)
    return result


def find_tdx_main():
    wins = enum_windows()
    for hwnd, title, visible in wins:
        if '通达信' in title and '信息' not in title and '64' not in title:
            return hwnd, title, visible
    return None, None, None


def send_key(vk, hold_alt=False):
    if hold_alt:
        user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)
    if hold_alt:
        user32.keybd_event(VK_MENU, 0, 2, 0)
    time.sleep(0.3)


def main():
    # 1. 恢复主窗口
    hwnd, title, visible = find_tdx_main()
    print(f'主窗口: {title} hwnd={hwnd} visible={visible}')
    if not hwnd:
        print('未找到主窗口')
        return
    user32.ShowWindow(hwnd, SW_RESTORE)
    time.sleep(0.5)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)

    # 2. Alt+S 打开系统菜单
    print('发送 Alt+S ...')
    send_key(VK_S, hold_alt=True)
    time.sleep(1.5)

    # 3. 枚举窗口, 看有没有菜单窗口出现
    wins = enum_windows()
    print('当前可见窗口:')
    for h, t, v in wins:
        if v:
            print(f'  hwnd={h} "{t}"')

    # 4. 按 Enter (赌第一项)
    print('按 Enter ...')
    send_key(VK_RETURN)
    time.sleep(3)

    # 5. 再枚举, 找"盘后数据下载"
    wins = enum_windows()
    found = False
    for h, t, v in wins:
        if '盘后数据下载' in t or '下载' in t:
            print(f'[命中] 对话框: "{t}" hwnd={h} visible={v}')
            found = True
    if not found:
        print('未找到"盘后数据下载"对话框')
        print('当前窗口:')
        for h, t, v in wins:
            if v:
                print(f'  hwnd={h} "{t}"')


if __name__ == '__main__':
    main()
