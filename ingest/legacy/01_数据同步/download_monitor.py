# -*- coding: utf-8 -*-
"""监控通达信"盘后数据下载中..."窗口 + vipdoc 文件变化
   用法: python download_monitor.py [最大监控秒数]
"""
import ctypes
import ctypes.wintypes as wt
import time
import os
import sys

user32 = ctypes.windll.user32
ENUM_CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

DAY_FILE = r'D:\new_tdx64\vipdoc\sh\lday\sh600367.day'
KEYWORDS = ['盘后数据下载', '下载完成']


def enum_titles():
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


def get_day_info():
    if not os.path.isfile(DAY_FILE):
        return '文件不存在'
    t = time.localtime(os.path.getmtime(DAY_FILE))
    return f'{time.strftime("%Y-%m-%d %H:%M:%S", t)} ({os.path.getsize(DAY_FILE)} bytes)'


def main():
    max_seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    print(f'监控开始: 每5秒检查一次, 最多 {max_seconds} 秒')
    print(f'目标文件: {DAY_FILE}')
    print(f'初始状态: {get_day_info()}')

    start = time.time()
    last_mtime = None
    last_print = 0
    while time.time() - start < max_seconds:
        wins = enum_titles()
        dl = [w for w in wins if '盘后数据下载' in w[1]]
        done = [w for w in wins if '下载完成' in w[1]]
        day_info = get_day_info()

        # 提取 mtime
        mtime = None
        if os.path.isfile(DAY_FILE):
            mtime = os.path.getmtime(DAY_FILE)

        now = time.time()
        if mtime != last_mtime or now - last_print > 15:
            print(f'[{time.strftime("%H:%M:%S")}] '
                  f'下载窗口: {len(dl)}个 {" | ".join(f"\"{t}\"(visible={v})" for _, t, v in dl)} | '
                  f'完成窗口: {len(done)}个 | '
                  f'day文件: {day_info}')
            last_print = now
            last_mtime = mtime

        if not dl:
            # 下载窗口消失
            if done:
                print(f'\n[完成] 出现"下载完成"窗口, 下载结束!')
            else:
                print(f'\n[状态] "盘后数据下载中..."窗口已消失')
            print(f'最终 day文件: {get_day_info()}')
            return

        time.sleep(5)

    print(f'\n[超时] 监控 {max_seconds} 秒结束')
    print(f'最终 day文件: {get_day_info()}')


if __name__ == '__main__':
    main()
