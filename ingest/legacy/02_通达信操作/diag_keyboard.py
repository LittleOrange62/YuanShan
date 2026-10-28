# -*- coding: utf-8 -*-
"""诊断: 键盘输入是否到达通达信键盘精灵. 测试 .901 公式管理器"""
import ctypes
import time

user32 = ctypes.windll.user32

VK_MENU = 0x12
VK_RETURN = 0x0D
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
        if '通达信金融终端V7.73' in title:
            return hwnd, title, visible
    return None, None, None


def send_key(vk):
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)
    time.sleep(0.08)


def send_text(text):
    for ch in text:
        if ch == '.':
            send_key(0xBE)
        else:
            send_key(ord(ch.upper()))
        time.sleep(0.08)
    time.sleep(0.3)


def main():
    hwnd, title, visible = find_tdx_main()
    print(f'主窗口: {title} hwnd={hwnd} visible={visible}')
    if not hwnd:
        return
    user32.ShowWindow(hwnd, SW_RESTORE)
    time.sleep(0.5)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    # Alt 键抢焦点
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, 2, 0)
    time.sleep(0.3)

    # 确认前台窗口
    fg = user32.GetForegroundWindow()
    print(f'前台窗口: hwnd={fg} (是否主窗口: {fg == hwnd})')

    print('发送 .901 + Enter ...')
    send_text('.901')
    send_key(VK_RETURN)
    time.sleep(3)

    wins = enum_windows()
    print('当前窗口(含隐藏):')
    for h, t, v in wins:
        if '公式' in t or '盘后' in t or '下载' in t:
            print(f'  [命中] hwnd={h} "{t}" visible={v}')
    print('--- 全部可见窗口 ---')
    for h, t, v in wins:
        if v:
            print(f'  hwnd={h} "{t}"')


if __name__ == '__main__':
    main()
