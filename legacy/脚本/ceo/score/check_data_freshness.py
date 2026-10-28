#!/usr/bin/env python3
"""
数据新鲜度检查 — 每天开盘前（09:01）运行

检查 kline_2026.db 最新交易日是否 >= 最近一个交易日
如果缺失 > 1 个交易日，推送飞书告警
"""
import sqlite3, os, sys
from datetime import datetime, timedelta

KLINE_DB = '/app/working/workspaces/data_in/data/kline_2026.db'
MANAGER_DIR = '/app/working/workspaces/manager'
FEISHU_SCRIPT = os.path.join(MANAGER_DIR, 'temp/push_feishu_now.sh')
FEISHU_CHANNEL = '9bdb_2bdfbc55'

def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}')

def get_latest_trade_date():
    conn = sqlite3.connect(KLINE_DB)
    r = conn.execute('SELECT MAX(trade_date) FROM daily_kline').fetchone()[0]
    conn.close()
    return r  # YYYYMMDD

def get_last_weekday():
    """获取最近的交易日（向前推到最近的工作日）"""
    now = datetime.now()
    # 周一~周五：用昨天
    # 周六：用周五（减1天）
    # 周日：用周五（减2天）
    weekday = now.weekday()
    if weekday == 0:  # 周一
        days_back = 3  # 周五
    elif weekday == 6:  # 周日
        days_back = 2  # 周五
    elif weekday == 5:  # 周六
        days_back = 1  # 周五
    else:
        days_back = 1  # 昨天
    
    last = now - timedelta(days=days_back)
    return last.strftime('%Y%m%d')

def main():
    latest_db = get_latest_trade_date()
    expected = get_last_weekday()
    
    log(f'数据库最新交易日: {latest_db}')
    log(f'预期最近交易日: {expected}')
    
    if not latest_db:
        msg = '⚠️ 数据新鲜度告警：kline_2026.db 无任何数据！'
        log(msg)
        _push(msg)
        return 1
    
    if latest_db >= expected:
        log('✅ 数据新鲜度正常')
        return 0
    
    # 计算缺失天数
    days_missing = 0
    d = int(latest_db)
    e = int(expected)
    while d < e:
        d += 1
        # 跳过周末
        dt = datetime.strptime(str(d), '%Y%m%d')
        if dt.weekday() < 5:
            days_missing += 1
    
    msg = (
        f'⚠️ 数据新鲜度告警\n'
        f'数据库最新: {latest_db}\n'
        f'预期最新: {expected}\n'
        f'缺失 {days_missing} 个交易日数据\n'
        f'请尽快补采！'
    )
    log(msg)
    _push(msg)
    
    # 自动触发补采
    log('正在自动触发补采...')
    backfill_script = '/app/working/workspaces/data_from/scripts/daily_pipeline.sh'
    if os.path.exists(backfill_script):
        import subprocess
        subprocess.Popen(['bash', backfill_script, expected[:4]+'-'+expected[4:6]+'-'+expected[6:8]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log('补采已后台启动')
    
    return 1

def _push(text):
    """推送飞书告警"""
    try:
        import subprocess
        cmd = [
            '/app/venv/bin/qwenpaw', 'channels', 'send',
            '--agent-id', 'manager',
            '--channel', 'feishu',
            '--target-user', 'ou_56057783def739f18b5721a28d68910c',
            '--target-session', FEISHU_CHANNEL,
            '--text', text
        ]
        subprocess.run(cmd, capture_output=True, timeout=15)
    except Exception as e:
        log(f'推送失败: {e}')

if __name__ == '__main__':
    sys.exit(main())
