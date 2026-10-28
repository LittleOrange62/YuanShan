#!/usr/bin/env python3
"""
全量因子IC+IR诊断分析 v2
对R001-R009每个因子，用"最新可用信号"方式（与评分系统一致），计算：
  1. 截面IC（与次日收益的Spearman ρ）
  2. IR（IC均值/IC标准差）
  3. 分组收益（10档）
  4. 因子相关性矩阵
"""
import sqlite3
import numpy as np
import os
from datetime import datetime

DATA_DIR = '/app/working/workspaces/data_in/data'
SIG_DB = os.path.join(DATA_DIR, 'quant_signals.db')
KLINE_DB = os.path.join(DATA_DIR, 'kline_2026.db')
BOSS_DB = os.path.join(DATA_DIR, 'boss_groups.db')

def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)

def spearman_rho(x, y):
    n = len(x)
    if n < 10:
        return 0.0
    x_rank = np.argsort(np.argsort(x)) + 1
    y_rank = np.argsort(np.argsort(y)) + 1
    d = x_rank - y_rank
    return 1 - (6 * np.sum(d ** 2)) / (n * (n ** 2 - 1))

# ============================================================
# 1. 数据加载
# ============================================================
log('='*60)
log('因子IC+IR诊断分析 v2')
log('='*60)

boss_conn = sqlite3.connect(BOSS_DB)
stocks = [r[0] for r in boss_conn.execute("SELECT DISTINCT ts_code FROM boss_group_stocks")]
boss_conn.close()
stocks_set = set(stocks)
log(f'自选股: {len(stocks)} 只')

kline_conn = sqlite3.connect(KLINE_DB)
pct_data = {}
for r in kline_conn.execute("""
    SELECT ts_code, trade_date, pct_change FROM daily_kline
    WHERE pct_change IS NOT NULL
    ORDER BY ts_code, trade_date
"""):
    pct_data[(r[0], r[1])] = r[2]

all_dates = sorted(set(d for (_, d) in pct_data.keys()))
log(f'交易日: {len(all_dates)} 天 ({all_dates[0]} ~ {all_dates[-1]})')

# 每只股票的交易日序列
stock_dates_map = {}
for ts_code in stocks:
    dates_for_stock = sorted([d for (c, d) in pct_data.keys() if c == ts_code])
    # 只保留在 all_dates 中的日期
    stock_dates_map[ts_code] = [d for d in dates_for_stock if d in all_dates]

def get_next_pct(ts_code, trade_date):
    dates = stock_dates_map.get(ts_code, [])
    try:
        idx = dates.index(trade_date)
        if idx + 1 < len(dates):
            return pct_data.get((ts_code, dates[idx + 1]))
    except ValueError:
        pass
    return None

def get_future_pct(ts_code, trade_date, n=10):
    """获取n个交易日后的涨跌幅"""
    dates = stock_dates_map.get(ts_code, [])
    try:
        idx = dates.index(trade_date)
        if idx + n < len(dates):
            return pct_data.get((ts_code, dates[idx + n]))
    except ValueError:
        pass
    return None

# ============================================================
# 2. 构建各因子信号映射（最新可用信号向前填充）
#    signal_map: {(ts_code, trade_date): value}
# ============================================================

def build_signal_map(conn, table, value_col, date_col='trade_date',
                     filter_col=None, filter_val=None, stocks_only=True):
    """构建每只股票每日最新可用信号"""
    if filter_col and filter_val:
        rows = conn.execute(f"""
            SELECT ts_code, {date_col}, {value_col} FROM {table}
            WHERE {value_col} IS NOT NULL AND {filter_col} = ?
            ORDER BY ts_code, {date_col}
        """, (filter_val,)).fetchall()
    else:
        rows = conn.execute(f"""
            SELECT ts_code, {date_col}, {value_col} FROM {table}
            WHERE {value_col} IS NOT NULL
            ORDER BY ts_code, {date_col}
        """).fetchall()

    stock_signals = {}
    for r in rows:
        ts = r[0]
        if stocks_only and ts not in stocks_set:
            continue
        # 尝试转换为数值，文本则跳过
        try:
            val = float(r[2])
        except (ValueError, TypeError):
            continue
        stock_signals.setdefault(ts, []).append((r[1], val))

    result = {}
    for ts_code, signals in stock_signals.items():
        signals.sort(key=lambda x: x[0])
        sig_dates = [s[0] for s in signals]
        sig_vals = [s[1] for s in signals]
        for td in all_dates:
            if td < sig_dates[0]:
                continue
            idx = -1
            for j, sd in enumerate(sig_dates):
                if sd <= td:
                    idx = j
                else:
                    break
            if idx >= 0:
                result[(ts_code, td)] = sig_vals[idx]
    return result

sig_conn = sqlite3.connect(SIG_DB)

log('构建因子信号映射（向前填充）...')

signal_maps = {}

# R001: signal_r001_gap_up, strength 字段，筛选 '涨停不破高_买入' 类型
log('  R001...')
signal_maps['R001'] = build_signal_map(sig_conn, 'signal_r001_gap_up', 'strength',
                                        filter_col='signal_type', filter_val='涨停不破高_买入')
log(f'    构建完成: {len(signal_maps["R001"])} 条')

# R002: signal_r002_chip_concentration, strength
log('  R002...')
signal_maps['R002'] = build_signal_map(sig_conn, 'signal_r002_chip_concentration', 'strength')
log(f'    构建完成: {len(signal_maps["R002"])} 条')

# R003: signal_r003_stop_loss, level
log('  R003...')
signal_maps['R003'] = build_signal_map(sig_conn, 'signal_r003_stop_loss', 'level')
log(f'    构建完成: {len(signal_maps["R003"])} 条')

# R004: signal_r004_volume_price_score, score_total
log('  R004...')
signal_maps['R004'] = build_signal_map(sig_conn, 'signal_r004_volume_price_score', 'score_total')
log(f'    构建完成: {len(signal_maps["R004"])} 条')

# R005: signal_r005_no_chase, is_triggered
log('  R005...')
signal_maps['R005'] = build_signal_map(sig_conn, 'signal_r005_no_chase', 'is_triggered')
log(f'    构建完成: {len(signal_maps["R005"])} 条')

# R006: signal_r006_dragon_head, score
log('  R006...')
signal_maps['R006'] = build_signal_map(sig_conn, 'signal_r006_dragon_head', 'score')
log(f'    构建完成: {len(signal_maps["R006"])} 条')

# R007: signal_r007_empty_position_check, would_buy
log('  R007...')
signal_maps['R007'] = build_signal_map(sig_conn, 'signal_r007_empty_position_check', 'would_buy')
log(f'    构建完成: {len(signal_maps["R007"])} 条')

sig_conn.close()
kline_conn.close()

# ============================================================
# 3. 截面IC分析（每日）
# ============================================================

FACTOR_NAMES = ['R001', 'R002', 'R003', 'R004', 'R005', 'R006', 'R007']
FACTOR_LABELS = {
    'R001': '涨停不破高',
    'R002': '筹码集中度',
    'R003': '止损信号',
    'R004': '量价评分',
    'R005': '不追高',
    'R006': '龙头信号',
    'R007': '空仓检查',
}

def compute_daily_ic(factor_name, signal_map, trade_dates, lookahead=1):
    """计算日度截面IC"""
    ic_list = []
    total = len(trade_dates)
    label = FACTOR_LABELS.get(factor_name, factor_name)

    for i, d in enumerate(trade_dates):
        if i % 30 == 0 and i > 0:
            log(f'  [{label}] IC计算 {i}/{total}...')

        pairs = []
        for ts_code in stocks_set:
            key = (ts_code, d)
            if key not in signal_map:
                continue
            f_val = signal_map[key]
            if lookahead == 1:
                ret = get_next_pct(ts_code, d)
            else:
                ret = get_future_pct(ts_code, d, lookahead)
            if ret is not None:
                pairs.append((f_val, ret))

        if len(pairs) < 10:
            continue

        f_arr = np.array([p[0] for p in pairs])
        r_arr = np.array([p[1] for p in pairs])
        rho = spearman_rho(f_arr, r_arr)
        ic_list.append((d, rho, len(pairs)))

    return ic_list

def compute_pooled_ic(factor_name, signal_map, trade_dates, lookahead=1):
    """
    计算合并截面IC（所有交易日所有股票合并）
    更稳健，适合事件型因子（R001/R006）
    """
    all_pairs = []
    for d in trade_dates:
        for ts_code in stocks_set:
            key = (ts_code, d)
            if key not in signal_map:
                continue
            f_val = signal_map[key]
            if lookahead == 1:
                ret = get_next_pct(ts_code, d)
            else:
                ret = get_future_pct(ts_code, d, lookahead)
            if ret is not None:
                all_pairs.append((f_val, ret, d))

    if len(all_pairs) < 100:
        return None, 0, all_pairs

    f_arr = np.array([p[0] for p in all_pairs])
    r_arr = np.array([p[1] for p in all_pairs])
    rho = spearman_rho(f_arr, r_arr)
    return rho, len(all_pairs), all_pairs

def compute_group_returns(all_pairs, n_groups=10):
    """分组收益"""
    if not all_pairs or len(all_pairs) < 100:
        return None, 0

    f_arr = np.array([p[0] for p in all_pairs])
    r_arr = np.array([p[1] for p in all_pairs])

    percentiles = np.percentile(f_arr, np.arange(0, 101, 10))
    groups = []
    for i in range(10):
        lo = percentiles[i]
        hi = percentiles[i+1] if i < 9 else float('inf')
        mask = (f_arr >= lo) & (f_arr < hi)
        if i == 9:
            mask = (f_arr >= lo) & (f_arr <= hi)
        cnt = mask.sum()
        avg_ret = r_arr[mask].mean() if cnt > 0 else 0
        groups.append((i+1, cnt, avg_ret))

    top_mask = (f_arr >= percentiles[9])
    bot_mask = (f_arr < percentiles[1])
    ls = r_arr[top_mask].mean() - r_arr[bot_mask].mean() if (top_mask.sum() > 0 and bot_mask.sum() > 0) else 0

    return groups, ls

# ============================================================
# 4. 执行分析
# ============================================================

results = {}

for fname in FACTOR_NAMES:
    label = FACTOR_LABELS[fname]
    smap = signal_maps.get(fname, {})
    log(f'\n{"-"*60}')
    log(f'▶ {fname} — {label}')
    log(f'   信号覆盖: {len(smap)} 条')

    if len(smap) < 50:
        log(f'   ⚠️ 数据过少，跳过')
        results[fname] = None
        continue

    # 合并截面IC（pooled，更稳健）
    rho_all, n_all, all_pairs = compute_pooled_ic(fname, smap, all_dates, lookahead=1)
    if rho_all is None:
        log(f'   ⚠️ 分析样本不足')
        results[fname] = None
        continue

    # 分组收益
    groups, ls = compute_group_returns(all_pairs)

    log(f'   📊 合并截面分析 (n={n_all})')
    log(f'      Spearman ρ = {rho_all:.6f}')

    if groups:
        log(f'      分组收益:')
        for idx, cnt, avg_r in groups:
            flag = ' ← 最低' if idx == 1 else ' ← 最高' if idx == 10 else ''
            log(f'       {idx:2d} | {cnt:5d}只 | {avg_r:+.4f}%{flag}')
        log(f'      多空(高-低): {ls:+.4f}%')

    # 日度IC序列
    ic_list = compute_daily_ic(fname, smap, all_dates, lookahead=1)
    if ic_list:
        ic_vals = np.array([ic[1] for ic in ic_list])
        ic_mean = ic_vals.mean()
        ic_std = ic_vals.std()
        ir = ic_mean / ic_std if ic_std > 0 else 0
        ic_pos = (ic_vals > 0).mean()
        ic_t = ic_mean / (ic_std / np.sqrt(len(ic_vals))) if ic_std > 0 else 0

        log(f'   📊 日度IC序列 ({len(ic_list)} 个交易日)')
        log(f'      IC均值: {ic_mean:.6f} | IC标准差: {ic_std:.6f}')
        log(f'      IR: {ir:.4f} | 正IC占比: {ic_pos:.1%}')
        log(f'      t统计量: {ic_t:.4f} {"***" if abs(ic_t)>2 else "**" if abs(ic_t)>1.5 else "*" if abs(ic_t)>1 else ""}')

        results[fname] = {
            'rho_pooled': rho_all,
            'n_pooled': n_all,
            'ic_mean': ic_mean,
            'ic_std': ic_std,
            'ir': ir,
            'ic_pos': ic_pos,
            'ic_t': ic_t,
            'n_dates': len(ic_list),
            'ls_ret': ls,
            'groups': groups,
        }
    else:
        log(f'   ⚠️ 日度IC无有效截面（可能是事件型因子只在特定日期有数据）')
        results[fname] = {
            'rho_pooled': rho_all,
            'n_pooled': n_all,
            'ic_mean': 0, 'ic_std': 0, 'ir': 0, 'ic_pos': 0, 'ic_t': 0,
            'n_dates': 0, 'ls_ret': ls, 'groups': groups,
        }

# ============================================================
# 5. 汇总对比
# ============================================================
log(f'\n\n{"="*70}')
log('📊 全因子IC诊断 — 汇总对比')
log(f'{"="*70}')
log(f' {"因子":<5} {"标签":<16} {"ρ合并":<9} {"N":<7} {"IC均值":<9} {"IR":<7} {"正IC%":<7} {"t值":<7} {"多空%":<7}')
log(f' {"-"*70}')
for fname in FACTOR_NAMES:
    r = results.get(fname)
    if r is None:
        log(f' {fname:<5} {FACTOR_LABELS[fname]:<16} {"❌":<9} {"数据不足":<24}')
        continue
    sig = ''
    if 'ic_t' in r and r['ic_t'] != 0:
        if abs(r['ic_t']) > 2: sig = '***'
        elif abs(r['ic_t']) > 1.5: sig = '**'
        elif abs(r['ic_t']) > 1: sig = '*'
    log(f' {fname:<5} {FACTOR_LABELS[fname]:<16} {r["rho_pooled"]:<+9.6f} {r["n_pooled"]:<7} {r["ic_mean"]:<+9.6f} {r["ir"]:<+7.4f} {r["ic_pos"]:<7.0%} {r["ic_t"]:<+7.4f}{sig} {r["ls_ret"]:<+7.4f}%')

log(f'\n{"="*70}')
log('✅ 因子IC+IR诊断完成')
log(f'{"="*70}')
