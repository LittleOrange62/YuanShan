# -*- coding: utf-8 -*-
"""聚焦通达信窗口(模拟鼠标点击) 并测试键盘命令 .901 公式管理器"""
import ctypes
import time

user32 = ctypes.windll.user32

VK_MENU = 0x12
VK_RETURN = 0x0D
SW_RESTORE = 9
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

ENUM_CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


class RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


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


def click_window(hwnd):
    """鼠标点击窗口中心"""
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    cx = (rect.left + rect.right) // 2
    cy = (rect.top + rect.bottom) // 2
    print(f'窗口矩形: ({rect.left},{rect.top})-({rect.right},{rect.bottom}), 点击点: ({cx},{cy})')
    user32.SetCursorPos(cx, cy)
    time.sleep(0.3)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.1)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.8)


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
        print('未找到主窗口!')
        return

    # 1. 恢复并显示
    user32.ShowWindow(hwnd, SW_RESTORE)
    time.sleep(0.8)

    # 2. 鼠标点击窗口(抢焦点)
    click_window(hwnd)

    # 3. 验证前台
    fg = user32.GetForegroundWindow()
    print(f'点击后前台窗口: hwnd={fg} (==主窗口: {fg == hwnd})')
    if fg != hwnd:
        print('[警告] 点击后通达信仍未获得焦点!')
        # 再点一次标题栏(顶部)
        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        user32.SetCursorPos((rect.left + rect.right) // 2, rect.top + 10)
        time.sleep(0.3)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.1)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.8)
        fg = user32.GetForegroundWindow()
        print(f'再次点击标题栏后前台: hwnd={fg} (==主窗口: {fg == hwnd})')

    # 4. 测试键盘命令 .901
    print('发送 .901 + Enter ...')
    send_text('.901')
    send_key(VK_RETURN)
    time.sleep(3)

    # 5. 查找公式管理器窗口
    wins = enum_windows()
    found = False
    for h, t, v in wins:
        if '公式' in t:
            print(f'[命中] hwnd={h} "{t}" visible={v}')
            found = True
    if not found:
        print('[结果] 未出现公式管理器窗口 - 键盘输入可能仍无效')
        print('当前可见窗口:')
        for h, t, v in wins:
            if v:
                print(f'  hwnd={h} "{t}"')
    else:
        print('[结论] 键盘输入有效! 可以继续尝试盘后数据下载命令')


if __name__ == '__main__':
    main()
