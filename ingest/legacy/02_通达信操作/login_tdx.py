# -*- coding: utf-8 -*-
"""自动点击通达信登录按钮, 等待主窗口出现"""
import subprocess
import time
import ctypes
from pywinauto import Application

user32 = ctypes.windll.user32
BM_CLICK = 0x00F5
WM_CLOSE = 0x0010


def get_tdx_pid():
    out = subprocess.check_output('tasklist /FI "IMAGENAME eq tdxw.exe" /FO CSV', shell=True).decode('gbk', errors='replace')
    for line in out.strip().splitlines()[1:]:
        parts = line.strip('"').split('","')
        if len(parts) >= 2 and 'tdxw' in parts[0].lower():
            return int(parts[1].strip('"'))
    return None


def enum_titles():
    wins = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        n = user32.GetWindowTextLengthW(h)
        if n > 0:
            b = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, b, n + 1)
            wins.append((h, b.value, bool(user32.IsWindowVisible(h))))
        return True
    user32.EnumWindows(CB(cb), 0)
    return wins


def main():
    pid = get_tdx_pid()
    print('tdxw PID:', pid)
    app = Application(backend='win32').connect(process=pid)

    # 找登录窗口
    login_win = None
    for w in app.windows():
        t = w.window_text()
        if '通达信' in t and 'V7.73' in t:
            login_win = w
            print('登录窗口:', repr(t), 'class=', w.class_name())
            break
    if not login_win:
        print('未找到登录窗口')
        return

    # 枚举按钮, 找"登录"
    target_btn = None
    for c in login_win.descendants():
        try:
            if c.friendly_class_name() == 'Button':
                txt = c.window_text()
                print(f'  Button: {repr(txt)}')
                if txt == '登录':
                    target_btn = c
        except Exception:
            pass

    if not target_btn:
        print('[错误] 未找到"登录"按钮')
        return

    print(f'点击登录按钮 (hwnd={target_btn.handle}) ...')
    user32.SendMessageW(target_btn.handle, BM_CLICK, 0, 0)
    print('已发送 BM_CLICK')

    # 等待主窗口出现 (最多 90 秒)
    print('等待主窗口...')
    for i in range(90):
        time.sleep(1)
        wins = enum_titles()
        for h, t, v in wins:
            if '通达信金融终端V7.73' in t and ('-' in t or '行情' in t or '报价' in t) and '登录' not in t:
                print(f'[成功] 主窗口已出现: "{t}" hwnd={h} visible={v}')
                return
        if i % 10 == 0 and i > 0:
            print(f'  等待中... {i}s (当前窗口: {[t for h,t,v in wins if "通达信" in t]})')
    print('[超时] 90秒内主窗口未出现')


if __name__ == '__main__':
    main()
