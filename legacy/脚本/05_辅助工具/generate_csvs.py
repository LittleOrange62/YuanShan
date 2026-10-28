#!/usr/bin/env python3
import sqlite3, csv, os
BASE = r"D:\\finance\\data"
# 1. 重点前5 from plan 6 (10日振幅) latest date
factor_db = os.path.join(BASE, 'fin_factor.db')
conn = sqlite3.connect(factor_db)
cur = conn.cursor()
# get latest date for plan 6
latest = cur.execute("SELECT MAX(trade_date) FROM score_results WHERE plan_id=6").fetchone()[0]
cur.execute("SELECT symbol, total_score FROM score_results WHERE plan_id=6 AND trade_date=? ORDER BY total_score DESC LIMIT 5", (latest,))
focus_top5 = cur.fetchall()
# write csv
with open(os.path.join(BASE, 'focus_top5.csv'), 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(['symbol','total_score'])
    writer.writerows(focus_top5)
# 2. 自选前10 from watchlist_stocks (no score, just list)
watch_db = os.path.join(BASE, 'fin_watchlist.db')
conn2 = sqlite3.connect(watch_db)
cur2 = conn2.cursor()
cur2.execute("SELECT symbol, name FROM watchlist_stocks ORDER BY id LIMIT 10")
watch_top10 = cur2.fetchall()
with open(os.path.join(BASE, 'watchlist_top10.csv'), 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(['symbol','name'])
    writer.writerows(watch_top10)
# 3. 全市场前20 from plan 6 (same source) top 20
cur.execute("SELECT symbol, total_score FROM score_results WHERE plan_id=6 AND trade_date=? ORDER BY total_score DESC LIMIT 20", (latest,))
market_top20 = cur.fetchall()
with open(os.path.join(BASE, 'market_top20.csv'), 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(['symbol','total_score'])
    writer.writerows(market_top20)
# 4. 板块前24 – assume sector scores stored in plan 5? (10日涨跌) as placeholder
# get top 24 by total_score from plan 5 latest date
latest5 = cur.execute("SELECT MAX(trade_date) FROM score_results WHERE plan_id=5").fetchone()[0]
cur.execute("SELECT symbol, total_score FROM score_results WHERE plan_id=5 AND trade_date=? ORDER BY total_score DESC LIMIT 24", (latest5,))
sector_top24 = cur.fetchall()
with open(os.path.join(BASE, 'sector_top24.csv'), 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(['symbol','total_score'])
    writer.writerows(sector_top24)
print('CSV files generated:')
print(' - focus_top5.csv rows:', len(focus_top5))
print(' - watchlist_top10.csv rows:', len(watch_top10))
print(' - market_top20.csv rows:', len(market_top20))
print(' - sector_top24.csv rows (plan5):', len(sector_top24))
conn.close()
conn2.close()
