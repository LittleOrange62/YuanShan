# -*- coding: utf-8 -*-
"""枚举通达信登录窗口控件 + 自动点击登录"""
import subprocess
import sys
import time
from pywinauto import Application


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
    app = Application(backend='win32').connect(process=pid)

    wins = app.windows()
    target = None
    for w in wins:
        t = w.window_text()
        print(f'窗口: "{t}" class={w.class_name()}')
        if '通达信' in t and 'V7.73' in t:
            target = w
    if not target:
        print('未找到通达信窗口')
        return

    print(f'\n=== 控件树 (depth=2) ===')
    try:
        for c in target.descendants():
            try:
                print(f'  [{c.friendly_class_name()}] "{c.window_text()}" rect={c.rectangle()}')
            except Exception:
                pass
    except Exception as e:
        print('枚举失败:', e)
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
