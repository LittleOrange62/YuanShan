# -*- coding: utf-8 -*-
"""枚举通达信主窗口的 Win32 菜单结构"""
import subprocess
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
    w = app.window(class_name='TdxW_MainFrame_Class')
    print('主窗口:', w.window_text())

    try:
        menu = w.menu()
        items = menu.items()
        print(f'\n菜单项 ({len(items)}):')
        for i, item in enumerate(items):
            text = item.text()
            print(f'  [{i}] "{text}"')
            # 展开子菜单
            try:
                sub_items = item.sub_menu().items() if item.is_string() else []
                for j, sub in enumerate(sub_items):
                    print(f'      [{i}.{j}] "{sub.text()}"')
            except Exception as e:
                print(f'      (子菜单获取失败: {e})')
    except Exception as e:
        print('菜单枚举失败:', e)
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
