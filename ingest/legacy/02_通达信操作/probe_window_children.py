# -*- coding: utf-8 -*-
"""枚举指定窗口的控件树 (win32 backend)"""
import subprocess
import sys
from pywinauto import Application


def get_tdx_pid():
    out = subprocess.check_output('tasklist /FI "IMAGENAME eq tdxw.exe" /FO CSV', shell=True).decode('gbk', errors='replace')
    for line in out.strip().splitlines()[1:]:
        parts = line.strip('"').split('","')
        if len(parts) >= 2 and 'tdxw' in parts[0].lower():
            return int(parts[1].strip('"'))
    return None


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else '刷新行情'
    pid = get_tdx_pid()
    print('tdxw PID:', pid)
    app = Application(backend='win32').connect(process=pid)

    # 找指定标题的窗口
    target = None
    for w in app.windows():
        t = w.window_text()
        if title in t:
            target = w
            print('找到窗口:', t, 'class=', w.class_name())
            break
    if not target:
        print(f'未找到窗口: {title}')
        return

    # 枚举子控件
    try:
        for d in target.descendants():
            print(f'  [{d.friendly_class_name()}] "{d.window_text()}" rect={d.rectangle()}')
    except Exception as e:
        print('descendants 失败:', e)
        # 备用: children
        try:
            for c in target.children():
                print(f'  [child] [{c.friendly_class_name()}] "{c.window_text()}" rect={c.rectangle()}')
        except Exception as e2:
            print('children 也失败:', e2)


if __name__ == '__main__':
    main()
