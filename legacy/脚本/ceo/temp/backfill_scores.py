#!/usr/bin/env python3
"""
backfill_scores.py — 补算2026年所有自选股（含重点股）每日评分

对每个交易日，计算股票池中全部股票的 X/Y/Z 三轴评分 + 象限 + 复合分
跳过已有评分记录，只补缺失的
"""
import sqlite3
import os
import sys
from datetime import datetime, timezone, timedelta

DATA_DIR = '/app/working/workspaces/data_in/data'

SCORE_DB = os.path.join(DATA_DIR, 'quant_score.db')
SIG_DB = os.path.join(DATA_DIR, 'quant_signals.db')
BOSS_DB = os.path.join(DATA_DIR, 'boss_groups.db')
STOCK_DB = os.path.join(DATA_DIR, 'stock_basics.db')
VIEWS_DB = os.path.join(DATA_DIR, 'quant_views.db')

W_X, W_Y, W_Z = 0.4, 0.35, 0.25

def log(msg):
    t = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{t}] {msg}', flush=True)

def get_stock_pool():
    """获取全量股票池（与 daily_score_update.py 一致）"""
    pool = set()
    conn = sqlite3.connect(BOSS_DB)
    for r in conn.execute("SELECT DISTINCT ts_code FROM boss_group_stocks"):
        pool.add(r[0])
    conn.close()

    # 也加上 stocks_master.watchlist 中的股票
    try:
        watch_db = os.path.join(DATA_DIR, 'stocks_master.db')
        if os.path.exists(watch_db):
            conn = sqlite3.connect(watch_db)
            r = conn.execute("SELECT ts_code FROM master.watchlist WHERE removed_date IS NULL")
            pool.add(r[0])
            conn.close()
    except:
        pass

    return pool

def get_stock_name(ts_code):
    try:
        conn = sqlite3.connect(STOCK_DB)
        r = conn.execute('SELECT name FROM stock_info WHERE ts_code=?', (ts_code,)).fetchone()
        conn.close()
        return r[0] if r else ts_code
    except:
        return ts_code

def get_all_trading_dates():
    """获取2026年所有有日线数据的交易日"""
    conn = sqlite3.connect(VIEWS_DB)
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM daily_kline_all WHERE trade_date >= '20260101' AND trade_date <= '20260703' ORDER BY trade_date"
    ).fetchall()]
    conn.close()
    return dates

def compute_z(w, vol):
    """Z轴计算（与 daily_score_update.py 一致）"""
    risk = max(10, min(90, 90 - (w or 0) * 5.33))
    vol_safety = 50.0
    if vol is not None:
        vol_safety = max(20, min(90, 90 - (vol - 6) / 22 * 70))
    return risk * 0.5 + vol_safety * 0.5

def normalize_r001(v):
    return max(0, min(100, (v or 0) / 5 * 100))

def normalize_r006(v):
    if v is None: return 50.0
    return max(0, min(100, (v - 100) / 300 * 100))

def normalize_r004(v):
    if v is None: return 50.0
    return max(0, min(100, (v - 30) / 65 * 100))

def compute_quadrant(x, y, z):
    """四象限判定 + Z轴修正"""
    if x >= 50 and y >= 50:
        q_raw = 'Q1'
    elif x < 50 and y >= 50:
        q_raw = 'Q2'
    elif x < 50 and y < 50:
        q_raw = 'Q3'
    else:
        q_raw = 'Q4'

    q = q_raw
    if z < 30:
        downgrade = {'Q1': 'Q2', 'Q2': 'Q3', 'Q4': 'Q3'}
        q = downgrade.get(q, q)
    elif z >= 70:
        upgrade = {'Q2': 'Q1', 'Q3': 'Q4'}
        q = upgrade.get(q, q)
    return q_raw, q

def backfill():
    log("=" * 60)
    log("开始补算历史评分")
    log("=" * 60)

    # 1. 获取股票池
    pool = get_stock_pool()
    log(f"股票池: {len(pool)}只")

    # 2. 获取所有交易日
    all_dates = get_all_trading_dates()
    log(f"2026年交易日: {len(all_dates)}个 ({all_dates[0]} ~ {all_dates[-1]})")

    # 3. 加载全部信号到内存
    log("加载信号数据...")
    sig = sqlite3.connect(SIG_DB)

    r001 = {}
    for r in sig.execute("SELECT ts_code, trade_date, strength FROM signal_r001_gap_up ORDER BY ts_code, trade_date"):
        r001.setdefault(r[0], []).append((r[1], r[2]))
    log(f"  R001: {len(r001)}只股票, {sum(len(v) for v in r001.values())}条信号")

    r006 = {}
    for r in sig.execute("SELECT ts_code, trade_date, score FROM signal_r006_dragon_head ORDER BY ts_code, trade_date"):
        r006.setdefault(r[0], []).append((r[1], r[2]))
    log(f"  R006: {len(r006)}只, {sum(len(v) for v in r006.values())}条")

    r004 = {}
    for r in sig.execute("SELECT ts_code, trade_date, score_total, grade FROM signal_r004_volume_price_score"):
        r004.setdefault(r[0], {})[r[1]] = (r[2], r[3])
    log(f"  R004: {len(r004)}只, {sum(len(v) for v in r004.values())}条")

    r003 = {}
    for r in sig.execute("SELECT ts_code, trade_date FROM signal_r003_stop_loss ORDER BY ts_code, trade_date"):
        r003.setdefault(r[0], []).append(r[1])
    log(f"  R003: {len(r003)}只, {sum(len(v) for v in r003.values())}条")

    r004_vol = {}
    for r in sig.execute("SELECT ts_code, AVG(score_vol_ratio) FROM signal_r004_volume_price_score GROUP BY ts_code"):
        r004_vol[r[0]] = r[1]
    sig.close()

    # 4. 加载各股全部交易日
    kline = sqlite3.connect(VIEWS_DB)
    stock_all_dates = {}
    for ts_code in pool:
        dates = [r[0] for r in kline.execute(
            "SELECT DISTINCT trade_date FROM daily_kline_all WHERE ts_code=? ORDER BY trade_date", (ts_code,)
        ).fetchall()]
        stock_all_dates[ts_code] = dates
    kline.close()
    log(f"各股交易日索引加载完成")

    # 5. 逐日计算
    score_conn = sqlite3.connect(SCORE_DB)
    total_inserted = 0
    total_skipped = 0
    total_no_data = 0

    for di, target_date in enumerate(all_dates):
        # 检查这个日期已有多少评分
        existing_count = score_conn.execute(
            "SELECT COUNT(*) FROM quant_scores WHERE trade_date=?", (target_date,)
        ).fetchone()[0]

        if existing_count >= len(pool):
            total_skipped += len(pool)
            if (di + 1) % 20 == 0:
                log(f"  [{di+1}/{len(all_dates)}] {target_date}: 已全部计算({existing_count}只), 跳过")
            continue

        if (di + 1) % 10 == 0 or existing_count < len(pool):
            log(f"  [{di+1}/{len(all_dates)}] {target_date}: 已有{existing_count}只, 需补{len(pool)-existing_count}只")

        date_inserted = 0
        for ts_code in pool:
            # 已计算则跳过
            if score_conn.execute(
                "SELECT 1 FROM quant_scores WHERE ts_code=? AND trade_date=?", (ts_code, target_date)
            ).fetchone():
                continue

            stock_dates = stock_all_dates.get(ts_code, [])
            if target_date not in stock_dates:
                continue

            # 股票名称
            name = get_stock_name(ts_code)

            # 信号查找
            r001_sigs = r001.get(ts_code, [])
            r006_sigs = r006.get(ts_code, [])
            r004_dict = r004.get(ts_code, {})
            r003_dates_list = r003.get(ts_code, [])

            # R001: 取最近信号（<= target_date）
            r001_strength = None
            r001_date = None
            for d, s in r001_sigs:
                if d <= target_date:
                    r001_date, r001_strength = d, s

            # R006: 取最近信号（<= target_date）
            r006_score_val = None
            r006_date = None
            for d, s in r006_sigs:
                if d <= target_date:
                    r006_date, r006_score_val = d, s

            # R004: 当日
            r004_info = r004_dict.get(target_date)

            # R003: 60日内止损次数
            td_idx = stock_dates.index(target_date)
            window_start = stock_dates[max(0, td_idx - 60)]
            warnings_60d = sum(1 for d in r003_dates_list if window_start <= d <= target_date)

            # 计算三轴
            x = normalize_r001(r001_strength) * 0.6 + normalize_r006(r006_score_val) * 0.4
            y = normalize_r004(r004_info[0] if r004_info else None)
            vol_avg = r004_vol.get(ts_code)
            z = compute_z(warnings_60d, vol_avg)

            # 象限
            q_raw, q = compute_quadrant(x, y, z)
            composite = x * W_X + y * W_Y + z * W_Z

            # 插入
            score_conn.execute("""
                INSERT OR IGNORE INTO quant_scores 
                (ts_code, trade_date, name,
                 r001_strength, r001_signal_date,
                 r006_score, r006_signal_date,
                 r004_score_total, r004_grade,
                 r003_warnings_60d,
                 x_raw, y_raw, z_raw,
                 quadrant_raw, quadrant,
                 composite_score, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
            """, (
                ts_code, target_date, name,
                r001_strength, r001_date,
                r006_score_val, r006_date,
                r004_info[0] if r004_info else None,
                r004_info[1] if r004_info else None,
                warnings_60d,
                round(x, 2), round(y, 2), round(z, 2),
                q_raw, q,
                round(composite, 2),
            ))
            date_inserted += 1

        score_conn.commit()
        total_inserted += date_inserted

        if date_inserted > 0:
            log(f"    → 本日新增 {date_inserted} 条评分 ({existing_count}+{date_inserted}={existing_count+date_inserted}只)")

    score_conn.close()
    log("=" * 60)
    log(f"补算完成! 总览:")
    log(f"  交易日: {len(all_dates)}个")
    log(f"  新增评分: {total_inserted}条")
    log(f"  跳过(已存在): {total_skipped}条")
    log("=" * 60)

if __name__ == '__main__':
    backfill()
