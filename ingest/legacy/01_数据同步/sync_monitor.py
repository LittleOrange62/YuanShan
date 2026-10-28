# -*- coding: utf-8 -*-
"""
通达信数据同步自动监控脚本
- 每5分钟检查一次 vipdoc 目录中 600367 的数据文件
- 检查4次（20分钟）无结果则自动重启通达信后继续监控
- 发现数据文件后自动调用 tdx_data.py 导入本地数据库

用法: python sync_monitor.py
"""
import os
import sys
import time
import subprocess
import datetime

# ===== 配置 =====
VIPDOC_DIR = r"D:\new_tdx64\vipdoc"
TDX_EXE = r"D:\new_tdx64\tdxw.exe"
SYMBOL = "600367"
YEAR = "2026"
CHECK_INTERVAL = 300      # 5分钟
MAX_CHECKS = 4            # 每轮4次
WORKSPACE = r"C:\Users\Administrator\.qwenpaw\workspaces\tq"
LOG_FILE = os.path.join(WORKSPACE, "docs", "sync_monitor.log")
SCRIPT_DIR = os.path.join(WORKSPACE, "scripts")

# 可能的文件名
POSSIBLE_FILES = [
    os.path.join(VIPDOC_DIR, f"sh{SYMBOL}.day"),
    os.path.join(VIPDOC_DIR, f"sh{SYMBOL}.dat"),
    os.path.join(VIPDOC_DIR, f"sz{SYMBOL}.day"),
    os.path.join(VIPDOC_DIR, f"sz{SYMBOL}.dat"),
    os.path.join(VIPDOC_DIR, f"{SYMBOL}.day"),
    os.path.join(VIPDOC_DIR, f"{SYMBOL}.dat"),
]


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def find_data_file():
    """在 vipdoc 目录下查找 600367 的数据文件"""
    if not os.path.isdir(VIPDOC_DIR):
        return None
    for f in os.listdir(VIPDOC_DIR):
        if SYMBOL in f:
            return os.path.join(VIPDOC_DIR, f)
    # 检查明确文件名
    for p in POSSIBLE_FILES:
        if os.path.exists(p):
            return p
    return None


def kill_tdx_processes():
    """结束所有通达信相关进程"""
    for proc in ["tdxw.exe", "TPyth.exe", "TdxW.exe", "tdx.exe"]:
        subprocess.run(f"taskkill /F /IM {proc} 2>nul",
                       shell=True, capture_output=True)
    log("已结束所有通达信相关进程")


def restart_tdx():
    """重启通达信"""
    kill_tdx_processes()
    time.sleep(2)
    subprocess.run(f'start "" /D "{os.path.dirname(TDX_EXE)}" "{TDX_EXE}"',
                   shell=True)
    log("已重新启动通达信")
    # 处理弹窗（后台执行 bat）
    popup_bat = os.path.join(SCRIPT_DIR, "handle_popup.bat")
    if os.path.exists(popup_bat):
        subprocess.Popen(f'cmd /c "{popup_bat}"', shell=True)
    time.sleep(10)


def import_data():
    """调用 tdx_data.py 导入数据到数据库"""
    tdx_data = os.path.join(WORKSPACE, "tdx_data.py")
    cmd = f'python "{tdx_data}" daily --symbol {SYMBOL} --year {YEAR}'
    log(f"执行导入: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    log(f"导入 stdout: {result.stdout[-2000:]}")
    if result.stderr:
        log(f"导入 stderr: {result.stderr[-2000:]}")
    return result.returncode == 0


def verify_in_db():
    """验证数据库中是否有 600367 最新数据"""
    import sqlite3
    db = os.path.join(r"D:\finance\data", f"tq_daily_{YEAR}.db")
    try:
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("SELECT ts_code, trade_date, close FROM stock_daily WHERE ts_code=? ORDER BY trade_date DESC LIMIT 3", (SYMBOL,))
        rows = cur.fetchall()
        conn.close()
        log(f"数据库最新记录: {rows}")
        return len(rows) > 0
    except Exception as e:
        log(f"验证数据库失败: {e}")
        return False


def main():
    log("========== 同步监控脚本启动 ==========")
    log(f"目标: {SYMBOL}, 检查间隔: {CHECK_INTERVAL}s, 每轮: {MAX_CHECKS}次")

    round_no = 1
    while True:
        log(f"===== 第 {round_no} 轮监控 =====")
        for i in range(1, MAX_CHECKS + 1):
            log(f"第 {i}/{MAX_CHECKS} 次检查...")
            f = find_data_file()
            if f:
                log(f"发现数据文件: {f}")
                if import_data():
                    log("导入命令执行完成")
                    if verify_in_db():
                        log("★★★★★ 数据已成功入库，监控结束 ★★★★★")
                        return
                    else:
                        log("导入完成但数据库验证未通过，稍后重试")
                else:
                    log("导入失败，稍后重试")
                time.sleep(CHECK_INTERVAL)
                continue

            log(f"第 {i} 次检查: 未发现 {SYMBOL} 数据文件")
            if i < MAX_CHECKS:
                time.sleep(CHECK_INTERVAL)

        # 一轮结束仍未找到
        log(f"第 {round_no} 轮监控未完成，重启通达信后继续")
        restart_tdx()
        round_no += 1
        if round_no > 5:
            log("已达到最大重启轮数，请手动检查！")
            return


if __name__ == "__main__":
    main()
