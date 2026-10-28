#!/usr/bin/env python3
"""
daily_score_update.py — 鑫鑫版评分读取器（manager用）
从 data_in 的 stocks_master.db 读取已计算的鑫鑫评分
触发data_in重新计算（如需要），然后拉取结果出报告

用法: python3 daily_score_update.py [YYYY-MM-DD]
"""

import sqlite3
import os
import sys
import subprocess
from datetime import datetime, timezone, timedelta

DATA_DIR = '/app/working/workspaces/data_in/data'
MASTER_DB = os.path.join(DATA_DIR, 'stocks_master.db')
CAL_DB = os.path.join(DATA_DIR, 'stock_data.db')
STOCK_DB = os.path.join(DATA_DIR, 'stock_basics.db')
BOSS_DB = os.path.join(DATA_DIR, 'boss_groups.db')

# 焦点股列表（与远山评分卡一致）
FOCUS_STOCKS = [
    '001208.SZ', '000600.SZ', '920808.BJ', '688820.SH', '688008.SH',
    '603893.SH', '000988.SZ', '300666.SZ', '002409.SZ', '000021.SZ',
    '002138.SZ', '600487.SH', '688106.SH', '300346.SZ', '002182.SZ',
]


def log(msg):
    t = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{t}] {msg}', flush=True)


def get_stock_name(ts_code):
    """从 stock_basics 取股票名称"""
    try:
        conn = sqlite3.connect(STOCK_DB)
        r = conn.execute("SELECT name FROM stock_info WHERE ts_code=?", (ts_code,)).fetchone()
        conn.close()
        return r[0] if r else ts_code.split('.')[0]
    except Exception:
        return ts_code.split('.')[0]


def get_latest_trading_day():
    """获取最新交易日"""
    try:
        conn = sqlite3.connect(CAL_DB, timeout=5)
        r = conn.execute(
            "SELECT cal_date FROM trade_calendar WHERE is_open=1 AND cal_date <= ? ORDER BY cal_date DESC LIMIT 1",
            (datetime.now().strftime('%Y%m%d'),)
        ).fetchone()
        conn.close()
        if r:
            d = r[0]
            return f'{d[:4]}-{d[4:6]}-{d[6:8]}'
    except Exception:
        pass
    return datetime.now().strftime('%Y-%m-%d')


def check_scores_exist(date_str):
    """检查指定日期的评分是否已计算"""
    try:
        conn = sqlite3.connect(MASTER_DB, timeout=5)
        cnt = conn.execute("SELECT COUNT(*) FROM score WHERE date=?", (date_str,)).fetchone()[0]
        conn.close()
        return cnt > 0
    except Exception:
        return False


def trigger_xinxin_compute(date_str):
    """触发data_in的鑫鑫评分计算"""
    log(f'  触发data_in鑫鑫评分计算: {date_str}')
    script = '/app/working/workspaces/data_in/tasks/compute_xinxin_scores.py'
    if not os.path.exists(script):
        log(f'  ❌ 脚本不存在: {script}')
        return False
    try:
        # 先确保板块热度数据
        heat_script = '/app/working/workspaces/data_from/tasks/fetch_sector_heat.py'
        if os.path.exists(heat_script):
            log(f'  同步板块热度数据...')
            subprocess.run(['python3', heat_script, date_str],
                           capture_output=True, timeout=120)

        # 算评分
        log(f'  计算鑫鑫评分...')
        result = subprocess.run(['python3', script, date_str],
                                capture_output=True, timeout=300, text=True)
        if result.returncode == 0:
            log(f'  ✅ 评分计算成功')
            return True
        else:
            log(f'  ❌ 评分计算失败: {result.stderr[-200:]}')
            return False
    except subprocess.TimeoutExpired:
        log(f'  ❌ 评分计算超时')
        return False
    except Exception as e:
        log(f'  ❌ 评分计算异常: {e}')
        return False


def load_scores(date_str):
    """从stocks_master.db读取评分数据"""
    conn = sqlite3.connect(MASTER_DB, timeout=10)
    c = conn.cursor()

    # 读取所有评分
    rows = c.execute(
        "SELECT s.symbol, s.x, s.y, s.final, s.oper, xy.quadrant, "
        "f.xmorph, f.xtrend, f.xpos, f.xchip, f.vhealth "
        "FROM score s "
        "LEFT JOIN x_y xy ON s.date=xy.date AND s.symbol=xy.symbol "
        "LEFT JOIN factors f ON s.date=f.date AND s.symbol=f.symbol "
        "WHERE s.date=? ORDER BY s.final DESC",
        (date_str,)
    ).fetchall()
    conn.close()

    result = []
    for r in rows:
        symbol = r[0]
        name = get_stock_name(symbol)
        result.append({
            'ts_code': symbol,
            'name': name,
            'x': r[1], 'y': r[2],
            'final': r[3], 'oper': r[4],
            'quadrant': r[5],
            'xmorph': r[6], 'xtrend': r[7],
            'xpos': r[8], 'xchip': r[9], 'vhealth': r[10],
        })

    return result


def build_report(date_str, all_scores):
    """生成远山评分卡文本"""
    if not all_scores:
        return f"⚠️ {date_str} 暂无评分数据"

    # 按final排序
    sorted_all = sorted(all_scores, key=lambda x: x['final'], reverse=True)

    # 分类：focus股 vs 其他
    focus_results = [s for s in sorted_all if s['ts_code'] in FOCUS_STOCKS]
    other_results = [s for s in sorted_all if s['ts_code'] not in FOCUS_STOCKS]

    # 象限统计
    q1 = sum(1 for s in sorted_all if s['quadrant'] == 'Q1_buy')
    q2 = sum(1 for s in sorted_all if s['quadrant'] == 'Q2_hold')
    q3 = sum(1 for s in sorted_all if s['quadrant'] == 'Q3_probe')
    q4 = sum(1 for s in sorted_all if s['quadrant'] == 'Q4_avoid')

    quad_icon = {
        'Q1_buy': '🟢', 'Q2_hold': '🟡',
        'Q3_probe': '🔵', 'Q4_avoid': '🔴',
    }
    quad_label = {
        'Q1_buy': 'Q1', 'Q2_hold': 'Q2',
        'Q3_probe': 'Q3', 'Q4_avoid': 'Q4',
    }

    lines = []
    lines.append(f"📊 远山评分卡 — {date_str} 📅")
    lines.append("")
    lines.append(f"📌 总评分 {len(sorted_all)}只 | Q1 {q1} | Q2 {q2} | Q3 {q3} | Q4 {q4}")
    lines.append("")

    # ⭐ 重点股前5
    lines.append("⭐ 重点股前5：股票 | 评分 | 象限 | X/Y/Z")
    top5 = focus_results[:5]
    for s in top5:
        icon = quad_icon.get(s['quadrant'], '⚪')
        ql = quad_label.get(s['quadrant'], s['quadrant'])
        lines.append(
            f"{icon} {s['name']}({s['ts_code'].split('.')[0]}) | "
            f"Final={s['final']:.1f} | {ql} | "
            f"X={s['x']:.0f} Y={s['y']:.0f}"
        )

    if len(focus_results) < 5:
        # 用全市场补足
        remaining = [s for s in sorted_all if s not in top5]
        for s in remaining[:5 - len(top5)]:
            icon = quad_icon.get(s['quadrant'], '⚪')
            ql = quad_label.get(s['quadrant'], s['quadrant'])
            lines.append(
                f"{icon} {s['name']}({s['ts_code'].split('.')[0]}) | "
                f"Final={s['final']:.1f} | {ql} | "
                f"X={s['x']:.0f} Y={s['y']:.0f}"
            )
    lines.append("")

    # 📋 自选股前10（排除重点股前5后取前10）
    lines.append("📋 自选股前10：")
    top5_codes = set(s['ts_code'] for s in top5)
    watchlist = [s for s in sorted_all if s['ts_code'] not in top5_codes][:10]
    for s in watchlist:
        icon = quad_icon.get(s['quadrant'], '⚪')
        ql = quad_label.get(s['quadrant'], s['quadrant'])
        lines.append(
            f"{icon} {s['name']}({s['ts_code'].split('.')[0]}) | "
            f"Final={s['final']:.1f} | {ql} | "
            f"X={s['x']:.0f} Y={s['y']:.0f}"
        )
    lines.append("")

    # 🌐 全市场前20
    lines.append("🌐 全市场前20：股票 | 评分 | 象限 | X/Y/Z")
    for s in sorted_all[:20]:
        icon = quad_icon.get(s['quadrant'], '⚪')
        ql = quad_label.get(s['quadrant'], s['quadrant'])
        is_focus = " ⭐" if s['ts_code'] in FOCUS_STOCKS else ""
        lines.append(
            f"{icon} {s['name']}({s['ts_code'].split('.')[0]}) | "
            f"Final={s['final']:.1f} | {ql}{is_focus} | "
            f"X={s['x']:.0f} Y={s['y']:.0f}"
        )
    lines.append("")
    lines.append("🔄 评分口径：鑫鑫版 X/Y(环境) → Final=0.8X+0.2Subj Oper=0.4X+0.6Y")
    lines.append("💡 Q1=买  Q2=持有  Q3=试探  Q4=回避（阈值 X>0, Y>0）")

    return '\n'.join(lines)


def main():
    override = sys.argv[1] if len(sys.argv) > 1 else None
    target_date = override or get_latest_trading_day()
    log(f'鑫鑫评分读取器 | 目标日期: {target_date}')

    # 检查评分是否已存在
    if not check_scores_exist(target_date):
        log(f'  评分不存在，触发data_in计算...')
        ok = trigger_xinxin_compute(target_date)
        if not ok:
            log(f'  ❌ 计算失败')
            return

    # 读取评分
    scores = load_scores(target_date)
    log(f'  读取评分: {len(scores)}条')

    if not scores:
        print(f'⚠️ {target_date} 暂无评分数据')
        return

    # 生成报告
    report = build_report(target_date, scores)
    print(report)

    # 保存到文件
    out_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(out_dir, '..', 'temp', f'远山评分卡_{target_date}.txt')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report)
    log(f'  报告已保存: {out_path}')


if __name__ == '__main__':
    main()
