# -*- coding: utf-8 -*-
"""方案B: 用 PostMessage 直接发送键盘消息到通达信窗口(不依赖前台焦点)
   测试 .901 公式管理器命令是否被通达信接收"""
import ctypes
import time

user32 = ctypes.windll.user32

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
VK_RETURN = 0x0D

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


def post_key(hwnd, vk, ch=None):
    """发送 WM_KEYDOWN + WM_CHAR"""
    # 构造 lParam: repeat=1, scan=0, extended=0
    lParam = 1
    user32.PostMessageW(hwnd, WM_KEYDOWN, vk, lParam)
    if ch is not None:
        user32.PostMessageW(hwnd, WM_CHAR, ord(ch), lParam)
    user32.PostMessageW(hwnd, WM_KEYUP, vk, lParam | 0xC0000000)
    time.sleep(0.1)


def post_text(hwnd, text):
    for ch in text:
        vk = ord(ch.upper())
        post_key(hwnd, vk, ch)
    # 回车
    post_key(hwnd, VK_RETURN, '\r')
    time.sleep(0.5)


def main():
    # 找到通达信主窗口和子窗口
    wins = enum_windows()
    main_hwnd = None
    child_hwnds = []
    for h, t, v in wins:
        if '通达信金融终端V7.73' in t:
            main_hwnd = h
    print(f'主窗口: {main_hwnd}')

    if not main_hwnd:
        print('未找到主窗口')
        return

    # 发送 .901 到主窗口
    print('PostMessage .901 到主窗口 ...')
    post_text(main_hwnd, '.901')
    time.sleep(3)

    wins2 = enum_windows()
    found = False
    for h, t, v in wins2:
        if '公式' in t:
            print(f'[命中] hwnd={h} "{t}" visible={v}')
            found = True
    if not found:
        print('[结果] 主窗口未响应 .901')

    # 也试试所有可见子窗口(通达信相关)
    print('\n尝试发送到所有通达信相关窗口 ...')
    for h, t, v in wins:
        if '通达信' in t and '信息' not in t:
            print(f'  发送到: "{t}" hwnd={h}')
            post_text(h, '.901')
            time.sleep(2)
            wins3 = enum_windows()
            for h3, t3, v3 in wins3:
                if '公式' in t3:
                    print(f'  [命中] hwnd={h3} "{t3}" visible={v3}')
                    found = True
            if found:
                break

    if not found:
        print('[最终结果] PostMessage 方案也未触发公式管理器')
    else:
        print('[结论] PostMessage 有效!')


if __name__ == '__main__':
    main()
