# -*- coding: utf-8 -*-
"""在通达信前台状态下测试键盘精灵命令触发盘后数据下载"""
import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

SW_RESTORE = 9
VK_MENU = 0x12
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B

ENUM_CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


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


def find_main_window():
    wins = enum_windows()
    for hwnd, title, visible in wins:
        if '通达信金融终端V7.73' in title and '-' in title:
            return hwnd, title, visible
    return None, None, None


def force_foreground(hwnd):
    current = kernel32.GetCurrentThreadId()
    target = user32.GetWindowThreadProcessId(hwnd, None)
    user32.AttachThreadInput(current, target, True)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.2)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    user32.AttachThreadInput(current, target, False)
    time.sleep(0.3)
    return user32.GetForegroundWindow() == hwnd


def send_key(vk):
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)
    time.sleep(0.08)


def send_text(text):
    for ch in text:
        if ch == '.':
            send_key(0xBE)
        elif ch.isdigit():
            send_key(ord(ch))
        else:
            send_key(ord(ch.upper()))
        time.sleep(0.08)
    time.sleep(0.3)


def try_cmd(hwnd, cmd, check='盘后数据下载'):
    print(f'--- 尝试: {cmd} ---')
    send_text(cmd)
    send_key(VK_RETURN)
    time.sleep(3)
    wins = enum_windows()
    for h, t, v in wins:
        if check in t:
            print(f'  [命中] "{t}" hwnd={h} visible={v}')
            return True
    print(f'  [未命中] 无"{check}"窗口')
    # 按 ESC 清理可能的残留
    send_key(VK_ESCAPE)
    time.sleep(0.5)
    return False


def main():
    hwnd, title, visible = find_main_window()
    print(f'主窗口: "{title}" hwnd={hwnd} visible={visible}')
    if not hwnd:
        print('未找到主窗口')
        return
    user32.ShowWindow(hwnd, SW_RESTORE)
    time.sleep(0.5)
    ok = force_foreground(hwnd)
    print(f'前台焦点: {ok} (fg={user32.GetForegroundWindow()})')

    cmds = ['.921', '.920', '.922', 'phsjx', 'sjx', '.921']
    for c in cmds:
        if try_cmd(hwnd, c):
            print(f'\n[结论] 命令 "{c}" 成功触发盘后数据下载!')
            return
    print('\n[结论] 以上命令均未触发')


if __name__ == '__main__':
    main()
