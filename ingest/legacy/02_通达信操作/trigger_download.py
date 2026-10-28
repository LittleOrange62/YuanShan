# -*- coding: utf-8 -*-
"""
trigger_download.py — 主动触发通达信"盘后数据下载"

流程:
  1. 强制置顶通达信主窗口 (BringWindowToTop + SetForegroundWindow)
  2. 验证前台焦点
  3. Alt+S 打开系统菜单 → Enter (触发第一项, 通常为"盘后数据下载")
  4. 验证"盘后数据下载"窗口出现
  5. 如果 tdxw 进程退出(误触发退出), 自动重启

用法: python trigger_download.py
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import time
import os

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

SW_RESTORE = 9
VK_MENU = 0x12
VK_S = 0x53
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B

TDX_EXE = r'D:\new_tdx64\tdxw.exe'
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


def get_tdx_pid():
    out = subprocess.check_output('tasklist /FI "IMAGENAME eq tdxw.exe" /FO CSV', shell=True).decode('gbk', errors='replace')
    for line in out.strip().splitlines()[1:]:
        parts = line.strip('"').split('","')
        if len(parts) >= 2 and 'tdxw' in parts[0].lower():
            return int(parts[1].strip('"'))
    return None


def find_main_window():
    wins = enum_windows()
    for hwnd, title, visible in wins:
        if '通达信金融终端V7.73' in title and '-' in title:
            return hwnd, title, visible
    return None, None, None


def send_key(vk, hold_alt=False):
    if hold_alt:
        user32.keybd_event(VK_MENU, 0, 0, 0)
        time.sleep(0.1)
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.1)
    user32.keybd_event(vk, 0, 2, 0)
    if hold_alt:
        time.sleep(0.1)
        user32.keybd_event(VK_MENU, 0, 2, 0)
    time.sleep(0.3)


def force_foreground(hwnd):
    """强制置顶"""
    current = kernel32.GetCurrentThreadId()
    target = user32.GetWindowThreadProcessId(hwnd, None)
    user32.AttachThreadInput(current, target, True)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.2)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    user32.AttachThreadInput(current, target, False)
    time.sleep(0.5)
    fg = user32.GetForegroundWindow()
    print(f'前台窗口: {fg} (==目标: {fg == hwnd})')
    return fg == hwnd


def main():
    # 检查通达信进程
    pid = get_tdx_pid()
    print('tdxw PID:', pid)
    if not pid:
        print('[重启] tdxw 未运行, 启动...')
        subprocess.Popen(['cmd', '/c', 'start', '', '/D', r'D:\new_tdx64', TDX_EXE], shell=True)
        time.sleep(15)
        pid = get_tdx_pid()
        print('重启后 PID:', pid)
        if not pid:
            print('[错误] 通达信无法启动')
            return

    hwnd, title, visible = find_main_window()
    print(f'主窗口: "{title}" hwnd={hwnd} visible={visible}')
    if not hwnd:
        print('[错误] 主窗口未找到, 可能还在登录')
        return

    # 强制置顶
    user32.ShowWindow(hwnd, SW_RESTORE)
    time.sleep(0.5)
    ok = force_foreground(hwnd)

    # Alt+S 打开系统菜单
    print('发送 Alt+S ...')
    send_key(VK_S, hold_alt=True)
    time.sleep(1.5)

    # 按 Enter
    print('发送 Enter ...')
    send_key(VK_RETURN)
    time.sleep(4)

    # 验证
    wins = enum_windows()
    dl = [w for w in wins if '盘后数据下载' in w[1]]
    done = [w for w in wins if '下载完成' in w[1]]
    print(f'盘后数据下载窗口: {len(dl)}个 {[t for _, t, _ in dl]}')
    print(f'下载完成窗口: {len(done)}个')

    # 检查进程是否还活着
    pid2 = get_tdx_pid()
    if not pid2:
        print('[警告] tdxw 进程已退出! (可能误触退出) 自动重启...')
        subprocess.Popen(['cmd', '/c', 'start', '', '/D', r'D:\new_tdx64', TDX_EXE], shell=True)
        time.sleep(15)
        print('已重启, PID:', get_tdx_pid())
    else:
        print('tdxw 进程存活: PID', pid2)

    if dl:
        print('\n[成功] 盘后数据下载窗口已出现!')
    else:
        print('\n[未触发] 盘后数据下载窗口未出现')


if __name__ == '__main__':
    main()
