# -*- coding: utf-8 -*-
"""列出所有可见窗口 + tdxw 进程的全部窗口(含隐藏)"""
import ctypes
import ctypes.wintypes as wt
import subprocess

user32 = ctypes.windll.user32
ENUM_CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def enum_all():
    result = []
    def cb(hwnd, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        result.append((hwnd, pid.value, buf.value, cls.value, bool(user32.IsWindowVisible(hwnd))))
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


def main():
    pid = get_tdx_pid()
    print('tdxw PID:', pid)
    wins = enum_all()
    print(f'总窗口数: {len(wins)}')

    print('\n=== tdxw 进程的窗口 ===')
    for h, p, t, c, v in wins:
        if p == pid:
            print(f'  hwnd={h} visible={v} class={c} title="{t}"')

    print('\n=== 所有可见窗口(标题非空) ===')
    for h, p, t, c, v in wins:
        if v and t:
            print(f'  hwnd={h} pid={p} "{t}"')


if __name__ == '__main__':
    main()
