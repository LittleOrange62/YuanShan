#!/usr/bin/env python3
"""
评分系统权重优化分析脚本
========================================
目标：对229只自选股的历史评分数据做全量回测，找到最优XYZ权重
  1. 隔日优化：composite(t) vs pct_change(t+1) → 最大化 Spearman ρ
  2. 中线优化：composite(t) vs pct_change(t+10) → 最大化 Spearman ρ

输出：
  - 隔日最优权重 + 分组收益表
  - 中线最优权重 + 分组收益表
  - 当前权重(0.40/0.35/0.25)在两个维度下的表现对比
"""

import sqlite3
import numpy as np
import os
import sys
from datetime import datetime

DATA_DIR = '/app/working/workspaces/data_in/data'
SCORE_DB = os.path.join(DATA_DIR, 'quant_score.db')
KLINE_DB = os.path.join(DATA_DIR, 'kline_2026.db')
BOSS_DB = os.path.join(DATA_DIR, 'boss_groups.db')

def log(msg):
    t = datetime.now().strftime('%H:%M:%S')
    print(f'[{t}] {msg}', flush=True)

def spearman_rho(x, y):
    """手动实现 Spearman 等级相关系数（无 scipy 依赖）"""
    n = len(x)
    if n < 10:
        return 0.0
    x_rank = np.argsort(np.argsort(x)) + 1
    y_rank = np.argsort(np.argsort(y)) + 1
    d = x_rank - y_rank
    rho = 1 - (6 * np.sum(d ** 2)) / (n * (n ** 2 - 1))
    return rho

def build_dataset():
    """构建分析数据集"""
    log('连接数据库...')
    score_conn = sqlite3.connect(SCORE_DB)
    kline_conn = sqlite3.connect(KLINE_DB)
    boss_conn = sqlite3.connect(BOSS_DB)

    # 1. 自选股列表
    log('获取自选股列表...')
    boss_stocks = set(r[0] for r in
                      boss_conn.execute("SELECT DISTINCT ts_code FROM boss_group_stocks"))
    log(f'自选股: {len(boss_stocks)} 只')
    boss_conn.close()

    # 2. 获取所有自选股评分数据
    log('加载评分数据...')
    placeholders = ','.join(['?'] * len(boss_stocks))
    scores = score_conn.execute(f"""
        SELECT ts_code, trade_date, x_raw, y_raw, z_raw, composite_score
        FROM quant_scores
        WHERE ts_code IN ({placeholders})
          AND x_raw IS NOT NULL AND y_raw IS NOT NULL AND z_raw IS NOT NULL
        ORDER BY ts_code, trade_date
    """, tuple(boss_stocks)).fetchall()
    log(f'评分记录: {len(scores)}')

    # 3. 获取这些股票的涨跌幅数据（全部历史）
    log('加载涨跌幅数据...')
    pct_changes = {}
    for r in kline_conn.execute("""
        SELECT ts_code, trade_date, pct_change
        FROM daily_kline
        WHERE pct_change IS NOT NULL
        ORDER BY ts_code, trade_date
    """):
        key = (r[0], r[1])
        pct_changes[key] = r[2]
    log(f'涨跌幅记录: {len(pct_changes)}')

    score_conn.close()
    kline_conn.close()

    # 4. 按股票分组
    log('构建时间序列...')
    from collections import OrderedDict
    stock_dates = OrderedDict()
    for ts_code, trade_date, x, y, z, cs in scores:
        if ts_code not in stock_dates:
            stock_dates[ts_code] = []
        stock_dates[ts_code].append((trade_date, x, y, z, cs))

    # 5. 构建分析记录
    records_1d = []   # (composite, pct_change) for next day
    records_10d = []  # (composite, pct_change) for 10 days later
    record_count = 0
    skip_no_1d = 0
    skip_no_10d = 0

    for ts_code, dates in stock_dates.items():
        dates.sort(key=lambda d: d[0])
        trade_dates = [d[0] for d in dates]

        for i, (trade_date, x, y, z, cs) in enumerate(dates):
            if x is None or y is None or z is None:
                continue
            record_count += 1

            # 隔日: 找 t+1 的涨跌幅（用同一只股票的下一个交易日）
            if i + 1 < len(dates):
                next_date = dates[i+1][0]
                key = (ts_code, next_date)
                if key in pct_changes:
                    records_1d.append((x, y, z, cs, pct_changes[key]))
                else:
                    skip_no_1d += 1

            # 中线: 找 t+10 的涨跌幅
            target_idx = i + 10
            if target_idx < len(dates):
                future_date = dates[target_idx][0]
                key = (ts_code, future_date)
                if key in pct_changes:
                    records_10d.append((x, y, z, cs, pct_changes[key]))
                else:
                    skip_no_10d += 1

    log(f'有效记录: {record_count}')
    log(f'隔日分析样本: {len(records_1d)} (跳过无数据: {skip_no_1d})')
    log(f'中线分析样本: {len(records_10d)} (跳过无数据: {skip_no_10d})')
    return records_1d, records_10d

def grid_search(records, label, weight_current=(0.40, 0.35, 0.25)):
    """
    网格搜索最优权重
    records: list of (x, y, z, cs, pct)
    label: '隔日' or '中线'
    """
    log(f'\n{"="*60}')
    log(f'{label}评分表 — 权重网格搜索')
    log(f'样本量: {len(records)}')
    log(f'{"="*60}')

    # 转 numpy 数组
    arr = np.array(records)
    xs, ys, zs, pcts = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 4]

    # 权重搜索空间
    w_range = np.arange(0.05, 0.85, 0.05)
    results = []

    total_combos = len(w_range) * len(w_range)
    combo_count = 0

    for wx in w_range:
        for wy in w_range:
            wz = 1 - wx - wy
            if wz < 0.05 or wz > 0.85:
                continue
            combo_count += 1

            # 计算复合评分
            composites = xs * wx + ys * wy + zs * wz
            rho = spearman_rho(composites, pcts)
            results.append((wx, wy, wz, rho))

    log(f'搜索组合: {combo_count} 组')

    # 找最优
    results.sort(key=lambda r: -r[3])
    best = results[0]
    log(f'\n🏆 最优权重: X={best[0]:.2f}, Y={best[1]:.2f}, Z={best[2]:.2f}')
    log(f'   Spearman ρ = {best[3]:.6f}')

    # 当前权重表现
    cs_current = xs * weight_current[0] + ys * weight_current[1] + zs * weight_current[2]
    rho_current = spearman_rho(cs_current, pcts)
    log(f'\n📊 当前权重({weight_current[0]:.0f}/{weight_current[1]:.0f}/{weight_current[2]:.0f})')
    log(f'   Spearman ρ = {rho_current:.6f}')

    # 按评分分10档统计收益
    log(f'\n{"="*60}')
    log(f'最优权重 {best[0]:.0f}/{best[1]:.0f}/{best[2]:.0f} — 分组收益')
    log(f'档位 | 样本数 | 平均涨跌幅 | 收益分(×10,0~100)')
    log(f'{"-"*50}')

    best_composites = xs * best[0] + ys * best[1] + zs * best[2]
    percentiles = np.percentile(best_composites, np.arange(0, 101, 10))
    bin_labels = list(range(1, 11))

    # 对pct进行10x缩放
    pct_min, pct_max = pcts.min(), pcts.max()
    if pct_max - pct_min > 0.001:
        pct_scaled = (pcts - pct_min) / (pct_max - pct_min) * 100
    else:
        pct_scaled = pcts * 10

    for i in range(10):
        lo = percentiles[i]
        hi = percentiles[i+1] if i < 9 else float('inf')
        mask = (best_composites >= lo) & (best_composites < hi)
        if i == 9:
            mask = (best_composites >= lo) & (best_composites <= hi)
        cnt = mask.sum()
        if cnt > 0:
            avg_pct = pcts[mask].mean()
            avg_scaled = pct_scaled[mask].mean()
            log(f'{i+1:2d}({"低" if i==0 else "高" if i==9 else " "}) | {cnt:5d} | {avg_pct:+.4f}% | {avg_scaled:6.2f}')
        else:
            log(f'{i+1:2d} | {cnt:5d} | {"N/A":>8} | {"N/A":>6}')

    # 同样展示当前权重的分组收益
    log(f'\n{"="*60}')
    log(f'当前权重 {weight_current[0]:.0f}/{weight_current[1]:.0f}/{weight_current[2]:.0f} — 分组收益')
    log(f'档位 | 样本数 | 平均涨跌幅 | 收益分(×10,0~100)')
    log(f'{"-"*50}')

    percentiles_c = np.percentile(cs_current, np.arange(0, 101, 10))
    for i in range(10):
        lo = percentiles_c[i]
        hi = percentiles_c[i+1] if i < 9 else float('inf')
        mask = (cs_current >= lo) & (cs_current < hi)
        if i == 9:
            mask = (cs_current >= lo) & (cs_current <= hi)
        cnt = mask.sum()
        if cnt > 0:
            avg_pct = pcts[mask].mean()
            avg_scaled = pct_scaled[mask].mean()
            log(f'{i+1:2d}({"低" if i==0 else "高" if i==9 else " "}) | {cnt:5d} | {avg_pct:+.4f}% | {avg_scaled:6.2f}')
        else:
            log(f'{i+1:2d} | {cnt:5d} | {"N/A":>8} | {"N/A":>6}')

    # 返回最优权重
    return best, rho_current, results

def main():
    log('='*60)
    log('远山评分系统 — 权重优化分析')
    log(f'开始时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    log('='*60)

    # 构建数据集
    records_1d, records_10d = build_dataset()

    # 隔日优化
    best_1d, rho_1d_current, _ = grid_search(records_1d, '隔日')

    # 中线优化
    best_10d, rho_10d_current, _ = grid_search(records_10d, '中线')

    # 输出总结
    print()
    print('='*70)
    print('📊 优化结果总结')
    print('='*70)
    print()
    print(f'{"维度":<12} {"最优权重(X/Y/Z)":<20} {"最优ρ":<12} {"当前ρ":<12} {"提升":<12}')
    print(f'{"-"*68}')
    print(f'{"隔日评分表":<12} {f"{best_1d[0]:.2f}/{best_1d[1]:.2f}/{best_1d[2]:.2f}":<20} {best_1d[3]:<12.6f} {rho_1d_current:<12.6f} {best_1d[3]-rho_1d_current:<+12.6f}')
    print(f'{"中线评分表":<12} {f"{best_10d[0]:.2f}/{best_10d[1]:.2f}/{best_10d[2]:.2f}":<20} {best_10d[3]:<12.6f} {rho_10d_current:<12.6f} {best_10d[3]-rho_10d_current:<+12.6f}')
    print()
    log(f'完成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

if __name__ == '__main__':
    main()
