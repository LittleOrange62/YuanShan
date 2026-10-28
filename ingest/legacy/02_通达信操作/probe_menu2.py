# -*- coding: utf-8 -*-
"""用 Win32 API 枚举通达信窗口菜单 (GetMenu)"""
import ctypes
import subprocess

user32 = ctypes.windll.user32
MF_BYPOSITION = 0x400


def get_tdx_pid():
    out = subprocess.check_output('tasklist /FI "IMAGENAME eq tdxw.exe" /FO CSV', shell=True).decode('gbk', errors='replace')
    for line in out.strip().splitlines()[1:]:
        parts = line.strip('"').split('","')
        if len(parts) >= 2 and 'tdxw' in parts[0].lower():
            return int(parts[1].strip('"'))
    return None


def get_all_windows(pid):
    """枚举进程的所有窗口 (hwnd, title, class)"""
    import ctypes.wintypes as wt
    result = []
    hwnds = []
    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(
        lambda h, l: (hwnds.append(h) or True)), 0)
    # 简单方式: 枚举所有顶层窗口后按进程过滤
    proc_ids = set()

    def cb(hwnd, _):
        pid_out = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_out))
        if pid_out.value == pid:
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            result.append((hwnd, buf.value, cls.value))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return result


def enum_menu(hwnd, depth=0):
    """递归枚举菜单"""
    hmenu = user32.GetMenu(hwnd)
    if not hmenu:
        return False
    count = user32.GetMenuItemCount(hmenu)
    print('  ' * depth + f'[窗口 {hwnd}] 菜单项数: {count}')
    for i in range(count):
        buf = ctypes.create_unicode_buffer(512)
        n = user32.GetMenuStringW(hmenu, i, buf, 512, MF_BYPOSITION)
        text = buf.value if n > 0 else f'<空/分隔符 {i}>'
        print('  ' * depth + f'  [{i}] "{text}"')
        # 子菜单
        hsub = user32.GetSubMenu(hmenu, i)
        if hsub:
            sub_count = user32.GetMenuItemCount(hsub)
            for j in range(sub_count):
                sub_buf = ctypes.create_unicode_buffer(512)
                n2 = user32.GetMenuStringW(hsub, j, sub_buf, 512, MF_BYPOSITION)
                sub_text = sub_buf.value if n2 > 0 else f'<空 {j}>'
                print('  ' * depth + f'    [{i}.{j}] "{sub_text}"')
                # 三级菜单
                hsub2 = user32.GetSubMenu(hsub, j)
                if hsub2:
                    sub2_count = user32.GetMenuItemCount(hsub2)
                    for k in range(sub2_count):
                        b3 = ctypes.create_unicode_buffer(512)
                        n3 = user32.GetMenuStringW(hsub2, k, b3, 512, MF_BYPOSITION)
                        t3 = b3.value if n3 > 0 else f'<空 {k}>'
                        print('  ' * depth + f'      [{i}.{j}.{k}] "{t3}"')
    return True


def main():
    pid = get_tdx_pid()
    print('tdxw PID:', pid)
    wins = get_all_windows(pid)
    print(f'进程窗口数: {len(wins)}')
    for hwnd, title, cls in wins:
        if title or 'TdxW' in cls or cls == '#32770':
            print(f'  hwnd={hwnd} title="{title}" class={cls}')
            if enum_menu(hwnd):
                print()


if __name__ == '__main__':
    main()
