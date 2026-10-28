# -*- coding: utf-8 -*-
"""
tdx_download_data.py — 通达信盘后数据「一键完整流程」

流程:
  1. 重启通达信 (taskkill → start)
  2. 自动登录 (复用 scripts/login_tdx.py)
  3. 关闭弹窗 (复用 scripts/close_tdx_popup.py)
  4. 等待盘后下载 / vipdoc 更新 (轮询 .day 文件 mtime + 最后交易日期)
  5. 增量同步 vipdoc → 数据库 (复用 tdx_data.py sync, 按当前日期/交易日期判断)
  6. 最小化通达信到托盘 (复用 scripts/minimize_tdx.py)

用法:
  python tdx_download_data.py                    # 完整流程, 默认全市场同步
  python tdx_download_data.py 600367            # 完整流程, 只同步 600367
  python tdx_download_data.py --sync-only        # 不重启/不登录, 只做增量同步(适合已登录场景)
  python tdx_download_data.py 600367 --sync-only
  python tdx_download_data.py --no-wait          # 重启登录后不等待下载, 直接同步(适合 vipdoc 已更新)

依赖脚本(同目录):
  login_tdx.py / close_tdx_popup.py / minimize_tdx.py / read_tdx_day.py
  tdx_data.py (工作区根目录)
"""
import os
import sys
import time
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(SCRIPT_DIR)  # 工作区根目录
TDX_DIR = r'D:\new_tdx64'
TDX_EXE = os.path.join(TDX_DIR, 'tdxw.exe')
VIPDOC_DIR = os.path.join(TDX_DIR, 'vipdoc')

# 默认监控目标: 600367 (上证主板); 全市场同步时也会检查该文件作为"下载进行中"的信号
DAY_SH = os.path.join(VIPDOC_DIR, 'sh', 'lday', 'sh600367.day')
DAY_SZ = os.path.join(VIPDOC_DIR, 'sz', 'lday', 'sz000001.day')


def run(cmd, timeout=600):
    """运行子进程并回显输出"""
    print(f'\n>>> {cmd}')
    proc = subprocess.run(cmd, shell=True, cwd=WORKSPACE, timeout=timeout)
    return proc.returncode


def py_script(name, args=''):
    """拼 python 脚本调用命令"""
    return f'python "{os.path.join(SCRIPT_DIR, name)}" {args}'


def restart_tdx():
    """重启通达信进程"""
    print('\n===== 1/6 重启通达信 =====')
    run('taskkill /F /IM tdxw.exe', timeout=30)
    time.sleep(2)
    run(f'start "" /D "{TDX_DIR}" "{TDX_EXE}"', timeout=30)
    # 等待 tdxw 进程出现
    for _ in range(30):
        out = subprocess.check_output('tasklist /FI "IMAGENAME eq tdxw.exe" /FO CSV', shell=True).decode('gbk', errors='replace')
        if 'tdxw' in out.lower():
            print('[OK] tdxw.exe 进程已启动')
            return True
        time.sleep(1)
    print('[警告] 30秒内未检测到 tdxw.exe 进程')
    return False


def wait_login(timeout=120):
    """等待登录窗口出现(供 login_tdx.py 点击)"""
    print('\n===== 2/6 自动登录 =====')
    # 给登录窗口一点出现时间
    time.sleep(5)
    return run(py_script('login_tdx.py'), timeout=timeout) == 0


def close_popups():
    """关闭通达信弹窗"""
    print('\n===== 3/6 关闭弹窗 =====')
    run(py_script('close_tdx_popup.py', '"通达信"'), timeout=60)


def get_day_last_date(day_file):
    """读取 .day 文件最后一条记录的日期(YYYYMMDD), 失败返回 None"""
    try:
        import struct
        size = os.path.getsize(day_file)
        if size % 32 != 0:
            return None
        with open(day_file, 'rb') as f:
            f.seek(size - 32)
            rec = f.read(32)
        return str(struct.unpack('<I', rec[:4])[0])
    except Exception:
        return None


def wait_download(max_wait=600, check_interval=10):
    """等待盘后下载: 轮询 vipdoc .day 文件 mtime 与最后日期, 直到停止变化或超时
    返回: True=检测到更新; False=超时"""
    print(f'\n===== 4/6 等待盘后下载 (最多 {max_wait}s) =====')
    files = [f for f in (DAY_SH, DAY_SZ) if os.path.isfile(f)]
    if not files:
        print('[提示] 未找到监控文件, 跳过等待')
        return False
    print('监控文件: ' + ', '.join(files))
    mtimes = {f: os.path.getmtime(f) for f in files}
    dates = {f: get_day_last_date(f) for f in files}
    print(f'初始状态: mtime={ {os.path.basename(f): time.strftime("%H:%M:%S", time.localtime(m)) for f, m in mtimes.items()} } '
          f'最后日期={dates}')

    stable_rounds = 0
    start = time.time()
    while time.time() - start < max_wait:
        time.sleep(check_interval)
        changed = False
        for f in files:
            m = os.path.getmtime(f)
            d = get_day_last_date(f)
            if m != mtimes[f] or d != dates[f]:
                print(f'[{time.strftime("%H:%M:%S")}] {os.path.basename(f)} 更新: '
                      f'mtime={time.strftime("%H:%M:%S", time.localtime(m))} 最后日期={d}')
                mtimes[f] = m
                dates[f] = d
                changed = True
        if changed:
            stable_rounds = 0
        else:
            stable_rounds += 1
            if stable_rounds >= 2:  # 连续 2 轮无变化 → 下载完成
                print('[OK] vipdoc 文件连续 2 轮无变化, 认为下载完成')
                return True
    print('[超时] 等待下载超时')
    return False


def sync_data(symbol=None):
    """调用 tdx_data.py sync 增量同步"""
    print('\n===== 5/6 增量同步 vipdoc → 数据库 =====')
    if symbol:
        return run(f'python tdx_data.py sync {symbol}', timeout=600) == 0
    return run('python tdx_data.py sync --all', timeout=900) == 0


def minimize():
    """最小化通达信到托盘"""
    print('\n===== 6/6 最小化通达信 =====')
    run(py_script('minimize_tdx.py'), timeout=60)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = set(a for a in sys.argv[1:] if a.startswith('--'))
    symbol = args[0] if args else None
    sync_only = '--sync-only' in flags
    no_wait = '--no-wait' in flags

    if symbol:
        print(f'[目标] 只同步 {symbol}')
    else:
        print('[目标] 全市场同步')

    if sync_only:
        ok = sync_data(symbol)
        print('\n[完成] 仅执行同步, 未重启通达信')
        sys.exit(0 if ok else 1)

    # 1. 重启
    restart_tdx()
    # 2. 登录
    if not wait_login():
        print('[错误] 登录失败, 中止流程 (可手动登录后重跑 --sync-only)')
        sys.exit(1)
    # 3. 弹窗
    close_popups()
    # 4. 等待下载
    if not no_wait:
        wait_download()
    # 5. 同步
    ok = sync_data(symbol)
    # 6. 最小化
    minimize()
    print('\n[完成] 一键流程结束')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
