# -*- coding: utf-8 -*-
"""枚举通达信 UI 结构(菜单/按钮), 用于定位'盘后数据下载'入口"""
import subprocess
import sys
from pywinauto import Application


def get_tdx_pid():
    """获取 tdxw.exe 进程 PID"""
    out = subprocess.check_output('tasklist /FI "IMAGENAME eq tdxw.exe" /FO CSV', shell=True).decode('gbk', errors='replace')
    for line in out.strip().splitlines()[1:]:
        parts = line.strip('"').split('","')
        if len(parts) >= 2 and 'tdxw' in parts[0].lower():
            return int(parts[1].strip('"'))
    return None


def main():
    pid = get_tdx_pid()
    print('tdxw PID:', pid)
    if not pid:
        return

    backend = sys.argv[1] if len(sys.argv) > 1 else 'uia'
    print(f'backend: {backend}')
    try:
        app = Application(backend=backend).connect(process=pid)
    except Exception as e:
        print('connect 失败:', e)
        return

    wins = app.windows()
    print(f'找到 {len(wins)} 个窗口')
    for w in wins:
        print(f'  窗口: title="{w.window_text()}" class={w.class_name()}')

    # 取主窗口
    for w in wins:
        t = w.window_text()
        if '通达信' in t and '信息' not in t:
            print(f'\n=== 枚举窗口: {t} ===')
            try:
                w.print_control_identifiers(depth=3)
            except Exception as e:
                print('print_control_identifiers 失败:', e)
            break


if __name__ == '__main__':
    main()
