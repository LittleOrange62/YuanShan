# -*- coding: utf-8 -*-
"""方案C: AttachThreadInput 强制焦点 + SendInput 模拟键盘
   测试 .901 公式管理器命令"""
import ctypes
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

SW_RESTORE = 9
WM_KEYDOWN = 0x0100
VK_RETURN = 0x0D

# SendInput 结构
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort),
                ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong),
                ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long),
                ('mouseData', ctypes.c_ulong), ('dwFlags', ctypes.c_ulong),
                ('time', ctypes.c_ulong), ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', ctypes.c_ulong), ('wParamL', ctypes.c_ushort),
                ('wParamH', ctypes.c_ushort)]

class _INPUTUNION(ctypes.Union):
    _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT), ('hi', HARDWAREINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [('type', ctypes.c_ulong), ('u', _INPUTUNION)]

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


def send_input_key(vk, scan=0, flags=0):
    """SendInput 发送按键"""
    ki = KEYBDINPUT(vk, scan, flags, 0, None)
    inp = INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=ki))
    return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def send_input_char(ch):
    """SendInput 发送字符 (unicode)"""
    ki = KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE, 0, None)
    inp = INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=ki))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    ki = KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)
    inp = INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=ki))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    time.sleep(0.1)


def send_input_text(text):
    for ch in text:
        send_input_char(ch)
    # 回车
    send_input_key(VK_RETURN)
    send_input_key(VK_RETURN, flags=KEYEVENTF_KEYUP)
    time.sleep(0.3)


def force_focus(hwnd):
    """AttachThreadInput 强制焦点"""
    current_thread = kernel32.GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    print(f'当前线程={current_thread}, 目标线程={target_thread}')
    # 附加输入
    user32.AttachThreadInput(current_thread, target_thread, True)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.2)
    ret = user32.SetForegroundWindow(hwnd)
    print(f'SetForegroundWindow 返回: {ret}')
    time.sleep(0.3)
    user32.AttachThreadInput(current_thread, target_thread, False)
    # 验证
    fg = user32.GetForegroundWindow()
    print(f'前台窗口: {fg} (==目标: {fg == hwnd})')
    return fg == hwnd


def main():
    hwnd, title, visible = find_tdx_main()
    print(f'主窗口: {title} hwnd={hwnd} visible={visible}')
    if not hwnd:
        print('未找到主窗口!')
        return

    user32.ShowWindow(hwnd, SW_RESTORE)
    time.sleep(0.8)

    ok = force_focus(hwnd)
    if not ok:
        # 再试一次
        print('第一次抢焦点失败, 重试...')
        time.sleep(1)
        ok = force_focus(hwnd)

    print(f'\n抢焦点结果: {ok}')
    print('发送 .901 + Enter (SendInput) ...')
    send_input_text('.901')
    time.sleep(3)

    wins = enum_windows()
    found = False
    for h, t, v in wins:
        if '公式' in t:
            print(f'[命中] hwnd={h} "{t}" visible={v}')
            found = True
    if not found:
        print('[结果] 公式管理器未出现')
        for h, t, v in wins:
            if v and ('通达信' in t or '下载' in t or '公式' in t):
                print(f'  可见窗口: hwnd={h} "{t}"')
    else:
        print('[结论] SendInput + 强制焦点 有效!')


if __name__ == '__main__':
    main()
