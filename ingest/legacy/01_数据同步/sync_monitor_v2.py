# -*- coding: utf-8 -*-
"""
通达信数据同步自动监控脚本 v2
- 监控 vipdoc\sh\lday\sh600367.day 是否更新到目标日期
- 每2分钟检查一次，检查15次（30分钟）
- 发现目标日期数据后，自动导入本地数据库 tq_daily_2026.db
- 30分钟未完成，重启通达信继续

用法: python sync_monitor_v2.py
"""
import os
import time
import struct
import datetime
import subprocess
import sqlite3

# ===== 配置 =====
SYMBOL = "600367"
YEAR = "2026"
TARGET_DATE = "20260824"        # 目标日期 YYYYMMDD
DAY_FILE = r"D:\new_tdx64\vipdoc\sh\lday\sh600367.day"   # 注意: 沪市日线在 sh\lday 目录
CHECK_INTERVAL = 120            # 2分钟
MAX_CHECKS = 15                 # 每轮15次 = 30分钟
TDX_EXE = r"D:\new_tdx64\tdxw.exe"
WORKSPACE = r"C:\Users\Administrator\.qwenpaw\workspaces\tq"
LOG_FILE = os.path.join(WORKSPACE, "docs", "sync_monitor.log")
SCRIPT_DIR = os.path.join(WORKSPACE, "scripts")
DB_FILE = os.path.join(r"D:\finance\data", f"tq_daily_{YEAR}.db")


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_day_last_date(filepath):
    """读取day文件最后一条记录的日期，返回 YYYYMMDD 字符串或 None"""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'rb') as f:
            data = f.read()
        n = len(data) // 32
        if n == 0:
            return None
        rec = data[(n-1)*32:n*32]
        date_int = struct.unpack('<I', rec[:4])[0]
        return str(date_int)
    except Exception as e:
        log(f"读取day文件失败: {e}")
        return None


def kill_tdx_processes():
    """结束所有通达信相关进程（保留python.exe，避免杀掉自己）"""
    for proc in ["tdxw.exe", "TdxW.exe", "TPyth.exe", "tdxcef.exe"]:
        subprocess.run(f"taskkill /F /IM {proc} 2>nul", shell=True, capture_output=True)
    log("已结束通达信相关进程")


def restart_tdx():
    """重启通达信"""
    kill_tdx_processes()
    time.sleep(3)
    subprocess.run(f'start "" /D "{os.path.dirname(TDX_EXE)}" "{TDX_EXE}"', shell=True)
    log("已重新启动通达信")
    # 处理弹窗
    popup_bat = os.path.join(SCRIPT_DIR, "handle_popup.bat")
    if os.path.exists(popup_bat):
        subprocess.Popen(f'cmd /c "{popup_bat}"', shell=True)
    time.sleep(15)


def import_data():
    """调用 tdx_data.py 导入数据到数据库"""
    tdx_data = os.path.join(WORKSPACE, "tdx_data.py")
    cmd = f'python "{tdx_data}" daily --symbol {SYMBOL} --year {YEAR}'
    log(f"执行导入: {cmd}")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
        log(f"导入 stdout: {result.stdout[-1500:]}")
        if result.stderr:
            log(f"导入 stderr: {result.stderr[-1500:]}")
        return result.returncode == 0
    except Exception as e:
        log(f"导入异常: {e}")
        return False


def verify_in_db():
    """验证数据库中是否有目标日期数据"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute(
            "SELECT ts_code, trade_date, close FROM stock_daily WHERE ts_code=? ORDER BY trade_date DESC LIMIT 3",
            (SYMBOL,))
        rows = cur.fetchall()
        conn.close()
        log(f"数据库最新记录: {rows}")
        for r in rows:
            if str(r[1]).replace('-', '') >= TARGET_DATE:
                return True
        return len(rows) > 0
    except Exception as e:
        log(f"验证数据库失败: {e}")
        return False


def main():
    log("========== 同步监控脚本 v2 启动 ==========")
    log(f"目标: {SYMBOL}, 目标日期: {TARGET_DATE}, 检查间隔: {CHECK_INTERVAL}s, 每轮: {MAX_CHECKS}次")
    log(f"监控文件: {DAY_FILE}")

    round_no = 1
    while True:
        log(f"===== 第 {round_no} 轮监控 =====")
        for i in range(1, MAX_CHECKS + 1):
            last = get_day_last_date(DAY_FILE)
            mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(DAY_FILE))) if os.path.exists(DAY_FILE) else "N/A"
            log(f"第 {i}/{MAX_CHECKS} 次检查: day文件最新日期={last}, 修改时间={mtime}")

            if last and last >= TARGET_DATE:
                log(f"★★★★★ 发现目标日期 {TARGET_DATE} 数据 ★★★★★")
                if import_data():
                    log("导入命令执行完成")
                    if verify_in_db():
                        log("===== 数据已成功入库，监控结束 =====")
                        return
                    else:
                        log("数据库验证未通过，重试导入")
                else:
                    log("导入失败，稍后重试")
            else:
                log(f"尚未更新到 {TARGET_DATE}，继续等待")

            if i < MAX_CHECKS:
                time.sleep(CHECK_INTERVAL)

        log(f"第 {round_no} 轮未完成，重启通达信后继续")
        restart_tdx()
        round_no += 1
        if round_no > 5:
            log("已达最大重启轮数，请手动检查！")
            return


if __name__ == "__main__":
    main()
