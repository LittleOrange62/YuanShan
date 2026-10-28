import sqlite3

# Step 1: 备份当前 stock_daily（已清理）
db = r'D:\finance\data\tq_daily_2026.db'
conn = sqlite3.connect(db)
c = conn.cursor()
c.execute('CREATE TABLE IF NOT EXISTS stock_daily_bak_20260823_clean AS SELECT * FROM stock_daily')
print('tq_daily_2026 备份 stock_daily 完成:', c.execute('SELECT COUNT(*) FROM stock_daily_bak_20260823_clean').fetchone()[0])
conn.close()

# Step 2: 删除空备份表
tables_to_del = [
    (r'D:\finance\data\tq_core.db', [
        'sector_info_bak_20260823',
        'sector_member_bak_20260823',
        'security_info_bak_20260823',
        'stock_info_bak_20260823',
        'stock_info_bak_20260823_fix',
    ]),
    (r'D:\finance\data\tq_daily_2026.db', [
        'sector_daily_bak_20260823',
    ]),
]

for path, tables in tables_to_del:
    conn = sqlite3.connect(path)
    c = conn.cursor()
    for t in tables:
        # 检查是否存在
        exists = c.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
            (t,)
        ).fetchone()[0]
        if exists:
            c.execute(f'DROP TABLE IF EXISTS {t}')
            print(f'  已删除: {path} -> {t}')
        else:
            print(f'  不存在: {path} -> {t}')
    conn.commit()
    conn.close()

print('所有空备份表清理完成')
