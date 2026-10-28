# -*- coding: utf-8 -*-
"""
关闭通达信弹窗/信息窗口工具
================================
用途：查找并关闭通达信弹出的各种窗口（如"通达信信息"、"系统提示"等）
用法：python close_tdx_popup.py ["窗口标题关键词"]
      默认关闭标题包含"通达信信息"的窗口
示例：
  python close_tdx_popup.py                    # 关闭"通达信信息"窗口
  python close_tdx_popup.py "通达信"           # 关闭标题含"通达信"的窗口
  python close_tdx_popup.py "提示"             # 关闭标题含"提示"的窗口
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

user32 = ctypes.windll.user32

WM_CLOSE = 0x0010
WM_SYSCOMMAND = 0x0112
SC_CLOSE = 0xF060

# 按键虚拟码
VK_ESCAPE = 0x1B
VK_RETURN = 0x0D


def find_windows_by_title_keyword(keyword):
    """
    枚举所有可见/不可见窗口，返回标题包含关键词的窗口列表
    返回: [(hwnd, title, pid, is_visible), ...]
    """
    results = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def enum_callback(hwnd, lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if keyword.lower() in title.lower():
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                visible = user32.IsWindowVisible(hwnd)
                results.append((hwnd, title, pid.value, visible))
        return True

    user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
    return results


def get_window_title(hwnd):
    """获取窗口标题"""
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ''
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def is_window_alive(hwnd):
    """检查窗口是否还存在且有效"""
    return user32.IsWindow(hwnd) != 0


def method_send_close(hwnd, method_name):
    """方法1/2: 发送 WM_CLOSE 或 WM_SYSCOMMAND SC_CLOSE"""
    if method_name == 'WM_CLOSE':
        result = user32.SendMessageW(hwnd, WM_CLOSE, 0, 0)
    else:
        result = user32.SendMessageW(hwnd, WM_SYSCOMMAND, SC_CLOSE, 0)
    return result


def method_keyboard_esc_enter(hwnd):
    """方法3: 激活窗口 + 发送 ESC 和 回车"""
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.5)
    # 发送 ESC
    user32.keybd_event(VK_ESCAPE, 0, 0, 0)
    user32.keybd_event(VK_ESCAPE, 0, 2, 0)  # KEYEVENTF_KEYUP
    time.sleep(0.5)
    # 发送回车
    user32.keybd_event(VK_RETURN, 0, 0, 0)
    user32.keybd_event(VK_RETURN, 0, 2, 0)  # KEYEVENTF_KEYUP
    time.sleep(0.5)
    return True


def method_alt_f4(hwnd):
    """方法4: 通过 AppActivate + Alt+F4（需要 PowerShell）"""
    import subprocess
    title = get_window_title(hwnd)
    if not title:
        return False
    ps_cmd = (
        "$wshell = New-Object -ComObject wscript.shell;"
        f"$r = $wshell.AppActivate('{title}');"
        "Start-Sleep -Milliseconds 500;"
        "$wshell.SendKeys('%{F4}');"
    )
    subprocess.run(['powershell', '-Command', ps_cmd], capture_output=True, timeout=10)
    return True


def close_window(hwnd, title):
    """用多种方法依次尝试关闭窗口，返回是否成功"""
    print(f"  尝试关闭窗口: hwnd={hwnd}, title='{title}'")

    methods = [
        ("SendMessage WM_CLOSE", method_send_close, 'WM_CLOSE'),
        ("SendMessage WM_SYSCOMMAND SC_CLOSE", method_send_close, 'SC_CLOSE'),
        ("键盘 ESC+回车", method_keyboard_esc_enter, None),
        ("Alt+F4", method_alt_f4, None),
    ]

    for name, func, arg in methods:
        print(f"    方法: {name}")
        try:
            if arg is not None:
                func(hwnd, arg)
            else:
                func(hwnd)
            time.sleep(1)
            if not is_window_alive(hwnd):
                print(f"    ✅ {name} 成功，窗口已销毁")
                return True
            # 窗口还在但可能已隐藏
            if not user32.IsWindowVisible(hwnd):
                print(f"    ✅ {name} 成功，窗口已隐藏 (visible=0)")
                return True
        except Exception as e:
            print(f"    ❌ {name} 异常: {e}")

    return False


def main():
    # 默认关键词
    keyword = '通达信信息'
    if len(sys.argv) > 1:
        keyword = sys.argv[1]

    print(f"===== 关闭通达信弹窗工具 =====")
    print(f"搜索关键词: '{keyword}'")

    windows = find_windows_by_title_keyword(keyword)
    print(f"找到 {len(windows)} 个匹配窗口")

    if not windows:
        print("✅ 没有找到匹配窗口（可能本来就不存在，或已关闭）")
        return

    success_count = 0
    for hwnd, title, pid, visible in windows:
        print(f"\n窗口: hwnd={hwnd}, pid={pid}, visible={visible}, title='{title}'")
        if close_window(hwnd, title):
            success_count += 1

    print(f"\n===== 结果: {success_count}/{len(windows)} 个窗口已关闭 =====")

    # 最终确认
    time.sleep(1)
    remaining = find_windows_by_title_keyword(keyword)
    if remaining:
        print(f"⚠️ 仍有 {len(remaining)} 个窗口未关闭:")
        for hwnd, title, pid, visible in remaining:
            print(f"    hwnd={hwnd}, visible={visible}, title='{title}'")
    else:
        print("✅ 所有目标窗口已全部关闭")


if __name__ == '__main__':
    main()
