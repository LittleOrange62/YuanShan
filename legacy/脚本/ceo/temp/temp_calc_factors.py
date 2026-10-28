import sqlite3
import os
import pandas as pd
import numpy as np
from datetime import datetime
import time

D = r'D:\finance\data'
SRC_DB = os.path.join(D, 'tq_daily_2026.db')
DST_DB = os.path.join(D, 'tq_computed.db')

print('Loading data from tq_daily_2026.db...')
start = time.time()

# 连接源数据库
src_conn = sqlite3.connect(SRC_DB)
src_cur = src_conn.cursor()

# 获取所有股票代码
src_cur.execute('SELECT DISTINCT ts_code FROM stock_daily')
all_codes = [row[0] for row in src_cur.fetchall()]
print(f'Total stocks: {len(all_codes)}')

# 连接目标数据库
dst_conn = sqlite3.connect(DST_DB)
dst_cur = dst_conn.cursor()

# 创建表（如果不存在）
dst_cur.execute('''
    CREATE TABLE IF NOT EXISTS kline_factors (
        ts_code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        ma5 REAL,
        ma10 REAL,
        ma20 REAL,
        ma60 REAL,
        pct_change_5d REAL,
        pct_change_10d REAL,
        pct_change_20d REAL,
        amplitude_10d REAL,
        amplitude_20d REAL,
        daily_pct_change REAL,
        daily_amplitude REAL,
        high20_prev REAL,
        low10 REAL,
        high10 REAL,
        clv REAL,
        slope20 REAL,
        ma10_ma20_dist REAL,
        price_ma20_dist REAL,
        pos_pct_52w REAL,
        hi52 REAL,
        lo52 REAL,
        dd52 REAL,
        overhang_share_60d REAL,
        hi60 REAL,
        dist_hi60 REAL,
        v5_avg REAL,
        v20_avg REAL,
        vr REAL,
        dayv REAL,
        v5prev REAL,
        dayr REAL,
        xmorph REAL,
        xtrend REAL,
        xpos REAL,
        xchip REAL,
        vhealth REAL,
        x_total REAL,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (ts_code, trade_date)
    )
''')

BATCH_SIZE = 100
success = 0
errors = []

for idx, code in enumerate(all_codes):
    try:
        # 读取该股票的日线数据
        df = pd.read_sql(
            f"SELECT * FROM stock_daily WHERE ts_code = ? ORDER BY trade_date",
            src_conn,
            params=(code,)
        )
        
        if len(df) < 5:
            continue
        
        # 计算各项因子
        factors = []
        
        for i in range(len(df)):
            row = df.iloc[i]
            trade_date = row['trade_date']
            
            # 基础数据
            close = row.get('close', None)
            high = row.get('high', None)
            low = row.get('low', None)
            vol = row.get('vol', None)
            pre_close = row.get('pre_close', None)
            
            if close is None or pd.isna(close):
                continue
            
            # 计算日涨跌幅
            if pre_close and pre_close > 0:
                daily_pct_change = (close - pre_close) / pre_close * 100
            else:
                daily_pct_change = None
            
            # 计算日振幅
            if pre_close and pre_close > 0 and high is not None and low is not None:
                daily_amplitude = (high - low) / pre_close * 100
            else:
                daily_amplitude = None
            
            # 计算均线
            ma5 = df.iloc[max(0, i-4):i+1]['close'].mean() if i >= 4 else None
            ma10 = df.iloc[max(0, i-9):i+1]['close'].mean() if i >= 9 else None
            ma20 = df.iloc[max(0, i-19):i+1]['close'].mean() if i >= 19 else None
            ma60 = df.iloc[max(0, i-59):i+1]['close'].mean() if i >= 59 else None
            
            # 计算5日/10日/20日涨跌（平均）
            if i >= 4:
                pct_changes_5d = []
                for j in range(max(1, i-4), i+1):
                    prev = df.iloc[j-1]['close'] if df.iloc[j-1]['close'] else None
                    curr = df.iloc[j]['close'] if df.iloc[j]['close'] else None
                    if prev and curr and prev > 0:
                        pct_changes_5d.append((curr - prev) / prev * 100)
                pct_change_5d = np.mean(pct_changes_5d) if pct_changes_5d else None
            else:
                pct_change_5d = None
            
            if i >= 9:
                pct_changes_10d = []
                for j in range(max(1, i-9), i+1):
                    prev = df.iloc[j-1]['close'] if df.iloc[j-1]['close'] else None
                    curr = df.iloc[j]['close'] if df.iloc[j]['close'] else None
                    if prev and curr and prev > 0:
                        pct_changes_10d.append((curr - prev) / prev * 100)
                pct_change_10d = np.mean(pct_changes_10d) if pct_changes_10d else None
            else:
                pct_change_10d = None
            
            if i >= 19:
                pct_changes_20d = []
                for j in range(max(1, i-19), i+1):
                    prev = df.iloc[j-1]['close'] if df.iloc[j-1]['close'] else None
                    curr = df.iloc[j]['close'] if df.iloc[j]['close'] else None
                    if prev and curr and prev > 0:
                        pct_changes_20d.append((curr - prev) / prev * 100)
                pct_change_20d = np.mean(pct_changes_20d) if pct_changes_20d else None
            else:
                pct_change_20d = None
            
            # 计算10日/20日振幅（平均）
            if i >= 9:
                amps_10d = []
                for j in range(max(1, i-9), i+1):
                    prev_c = df.iloc[j-1]['close'] if df.iloc[j-1]['close'] else None
                    h = df.iloc[j]['high'] if df.iloc[j]['high'] else None
                    l = df.iloc[j]['low'] if df.iloc[j]['low'] else None
                    if prev_c and h and l and prev_c > 0:
                        amps_10d.append((h - l) / prev_c * 100)
                amplitude_10d = np.mean(amps_10d) if amps_10d else None
            else:
                amplitude_10d = None
            
            if i >= 19:
                amps_20d = []
                for j in range(max(1, i-19), i+1):
                    prev_c = df.iloc[j-1]['close'] if df.iloc[j-1]['close'] else None
                    h = df.iloc[j]['high'] if df.iloc[j]['high'] else None
                    l = df.iloc[j]['low'] if df.iloc[j]['low'] else None
                    if prev_c and h and l and prev_c > 0:
                        amps_20d.append((h - l) / prev_c * 100)
                amplitude_20d = np.mean(amps_20d) if amps_20d else None
            else:
                amplitude_20d = None
            
            # 计算20日最高/10日最高/10日最低
            high20_prev = df.iloc[max(0, i-20):i]['high'].max() if i >= 1 else None
            low10 = df.iloc[max(0, i-9):i+1]['low'].min() if i >= 0 else None
            high10 = df.iloc[max(0, i-9):i+1]['high'].max() if i >= 0 else None
            
            # 计算CLV
            if high is not None and low is not None and high != low:
                clv = (2 * close - high - low) / (high - low) * 100
            else:
                clv = None
            
            # 计算52周最高/最低
            hi52 = df.iloc[max(0, i-251):i+1]['high'].max() if i >= 0 else None
            lo52 = df.iloc[max(0, i-251):i+1]['low'].min() if i >= 0 else None
            
            # 计算52周回撤
            if hi52 and hi52 > 0 and close:
                dd52 = (hi52 - close) / hi52 * 100
            else:
                dd52 = None
            
            # 计算60日最高
            hi60 = df.iloc[max(0, i-59):i+1]['high'].max() if i >= 0 else None
            
            # 计算距离60日最高幅度
            if hi60 and hi60 > 0 and close:
                dist_hi60 = (hi60 - close) / hi60 * 100
            else:
                dist_hi60 = None
            
            # 计算5日/20日平均成交量
            v5_avg = df.iloc[max(0, i-4):i+1]['vol'].mean() if i >= 4 else None
            v20_avg = df.iloc[max(0, i-19):i+1]['vol'].mean() if i >= 19 else None
            
            # 计算量比
            if v5_avg and v5_avg > 0 and vol:
                vr = vol / v5_avg
            else:
                vr = None
            
            # 计算5日前成交量
            v5prev = df.iloc[i-5]['vol'] if i >= 5 else None
            
            # 计算日振幅
            if pre_close and pre_close > 0 and high is not None and low is not None:
                dayr = (high - low) / pre_close * 100
            else:
                dayr = None
            
            # 计算X因子（简化版）
            # 形态分 - 基于均线排列
            xmorph = 0
            if ma5 and ma10 and ma20:
                if ma5 > ma10 > ma20:
                    xmorph = 100
                elif ma5 < ma10 < ma20:
                    xmorph = -100
                else:
                    xmorph = 0
            
            # 趋势分 - 基于20日斜率
            if i >= 19:
                x = np.arange(20)
                y = df.iloc[i-19:i+1]['close'].values
                if len(y) == 20 and not np.isnan(y).any():
                    slope = np.polyfit(x, y, 1)[0]
                    xtrend = slope / close * 100 * 20  # 20日预期收益率
                else:
                    xtrend = None
            else:
                xtrend = None
            
            # 位置分 - 基于52周位置
            if hi52 and lo52 and hi52 != lo52:
                xpos = (close - lo52) / (hi52 - lo52) * 100
            else:
                xpos = None
            
            # 量能健康度
            if vr and vr > 0:
                vhealth = min(vr, 3) / 3 * 100  # 0-100
            else:
                vhealth = None
            
            # X因子总分
            x_total = 0
            count = 0
            if xmorph is not None:
                x_total += xmorph * 0.25
                count += 0.25
            if xtrend is not None:
                x_total += xtrend * 0.25
                count += 0.25
            if xpos is not None:
                x_total += (xpos - 50) * 0.2  # 中性为50
                count += 0.2
            if vhealth is not None:
                x_total += (vhealth - 50) * 0.1  # 中性为50
                count += 0.1
            
            if count > 0:
                x_total = x_total / count
            else:
                x_total = None
            
            # 插入数据
            factors.append((
                code, trade_date, ma5, ma10, ma20, ma60,
                pct_change_5d, pct_change_10d, pct_change_20d,
                amplitude_10d, amplitude_20d, daily_pct_change, daily_amplitude,
                high20_prev, low10, high10, clv, None,  # slope20 placeholder
                None, None,  # ma10_ma20_dist, price_ma20_dist placeholder
                None,  # pos_pct_52w placeholder
                hi52, lo52, dd52, None,  # overhang_share_60d placeholder
                hi60, dist_hi60, v5_avg, v20_avg, vr,
                vol, v5prev, dayr,
                xmorph, xtrend, xpos, None,  # xchip placeholder
                vhealth, x_total, datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ))
        
        # 批量插入
        if factors:
            dst_cur.executemany('''
                INSERT OR REPLACE INTO kline_factors 
                (ts_code, trade_date, ma5, ma10, ma20, ma60,
                 pct_change_5d, pct_change_10d, pct_change_20d,
                 amplitude_10d, amplitude_20d, daily_pct_change, daily_amplitude,
                 high20_prev, low10, high10, clv, slope20,
                 ma10_ma20_dist, price_ma20_dist, pos_pct_52w,
                 hi52, lo52, dd52, overhang_share_60d,
                 hi60, dist_hi60, v5_avg, v20_avg, vr,
                 dayv, v5prev, dayr,
                 xmorph, xtrend, xpos, xchip,
                 vhealth, x_total, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', factors)
            success += 1
        
        # 每批提交
        if (idx + 1) % BATCH_SIZE == 0:
            dst_conn.commit()
            elapsed = time.time() - start
            print(f'Progress: {idx+1}/{len(all_codes)} ({elapsed:.1f}s, {success} processed)')
    
    except Exception as e:
        errors.append((code, str(e)))
        if len(errors) <= 5:
            print(f'Error processing {code}: {e}')

dst_conn.commit()
dst_conn.close()
src_conn.close()

print(f'Done. Processed: {success}, Errors: {len(errors)}')
if errors:
    print('First 5 errors:')
    for e in errors[:5]:
        print(e)
