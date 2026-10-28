#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
拆分 tq_minline.db 为按年份的6个数据库文件
"""
import sqlite3
import os
import shutil
from datetime import datetime

SOURCE_DB = r'D:\finance\data\tq_minline.db'
BACKUP_DB = r'D:\finance\data\tq_minline.db.bak'
DATA_DIR = r'D:\finance\data'

YEARS = [2021, 2022, 2023, 2024, 2025, 2026]

def split_minline():
    print("=" * 60)
    print("开始拆分 tq_minline.db")
    print("=" * 60)
    
    # 检查源文件
    if not os.path.exists(SOURCE_DB):
        print(f"错误：源文件不存在 {SOURCE_DB}")
        return False
    
    print(f"源文件: {SOURCE_DB}")
    print(f"源文件大小: {os.path.getsize(SOURCE_DB) / (1024*1024):.2f} MB")
    
    # 复制到备份文件（避免锁定问题）
    print(f"\n复制源文件到备份...")
    shutil.copy2(SOURCE_DB, BACKUP_DB)
    print(f"备份完成: {BACKUP_DB}")
    
    # 连接备份文件
    print(f"\n连接备份数据库...")
    src_conn = sqlite3.connect(BACKUP_DB)
    src_conn.execute("PRAGMA journal_mode=WAL")
    src_conn.execute("PRAGMA synchronous=NORMAL")
    
    # 获取所有表
    tables = src_conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    table_names = [t[0] for t in tables]
    print(f"源数据库表: {', '.join(table_names)}")
    
    # 检查 stock_minline 表
    table_name = 'stock_minline'
    if table_name not in table_names:
        print(f"错误：源数据库中没有 {table_name} 表")
        src_conn.close()
        return False
    
    # 获取表结构
    table_schema = src_conn.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table_name}'").fetchone()[0]
    print(f"\n{table_name} 表结构:\n{table_schema}")
    
    # 获取表的索引
    indexes = src_conn.execute(f"SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='{table_name}' AND sql IS NOT NULL").fetchall()
    print(f"\n{table_name} 表索引:")
    for idx in indexes:
        print(f"  {idx[0]}")
    
    # 获取表的触发器
    triggers = src_conn.execute(f"SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name='{table_name}'").fetchall()
    print(f"\n{table_name} 表触发器:")
    for trg in triggers:
        print(f"  {trg[0]}")
    
    # 获取 meta_tables 结构
    meta_table_name = 'meta_tables'
    meta_table_schema = src_conn.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{meta_table_name}'").fetchone()[0]
    print(f"\n{meta_table_name} 表结构:\n{meta_table_name}")
    
    # 获取总行数
    total_rows = src_conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    print(f"\n{table_name} 总行数: {total_rows:,}")
    
    # 获取时间范围
    time_range = src_conn.execute(f"SELECT MIN(trade_datetime), MAX(trade_datetime) FROM {table_name}").fetchone()
    print(f"时间范围: {time_range[0]} ~ {time_range[1]}")
    
    # 按年份拆分
    results = {}
    for year in YEARS:
        print(f"\n{'='*60}")
        print(f"处理年份: {year}")
        print(f"{'='*60}")
        
        target_db = os.path.join(DATA_DIR, f'tq_minline_{year}.db')
        
        # 如果目标文件已存在，先删除
        if os.path.exists(target_db):
            os.remove(target_db)
            print(f"删除已存在的目标文件: {target_db}")
        
        # 创建目标数据库
        tgt_conn = sqlite3.connect(target_db)
        tgt_conn.execute("PRAGMA journal_mode=WAL")
        tgt_conn.execute("PRAGMA synchronous=NORMAL")
        
        # 创建 stock_minline 表结构
        tgt_conn.execute(table_schema)
        
        # 创建 meta_tables 表结构
        tgt_conn.execute(meta_table_schema)
        
        # 创建索引
        for idx in indexes:
            if idx[0]:
                tgt_conn.execute(idx[0])
        
        # 创建触发器
        for trg in triggers:
            if trg[0]:
                tgt_conn.execute(trg[0])
        
        # 复制 meta_tables 数据
        print(f"  复制 {meta_table_name} 数据...")
        meta_data = src_conn.execute(f"SELECT * FROM {meta_table_name}").fetchall()
        if meta_data:
            meta_cols = src_conn.execute(f"PRAGMA table_info({meta_table_name})").fetchall()
            col_names = [col[1] for col in meta_cols]
            placeholders = ','.join(['?' for _ in col_names])
            for row in meta_data:
                tgt_conn.execute(f"INSERT INTO {meta_table_name} VALUES ({placeholders})", row)
            tgt_conn.commit()
        
        # 按年份筛选数据并复制
        year_start = f'{year}0101 00:00'
        year_end = f'{year+1}0101 00:00'
        
        print(f"  查询 {year_start} 到 {year_end} 的数据...")
        cursor = src_conn.execute(
            f"SELECT * FROM {table_name} WHERE trade_datetime >= ? AND trade_datetime < ?",
            (year_start, year_end)
        )
        
        # 批量插入
        batch = []
        batch_size = 10000
        count = 0
        
        # 获取列名
        col_names = [desc[0] for desc in cursor.description]
        placeholders = ','.join(['?' for _ in col_names])
        
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            
            batch.extend(rows)
            if len(batch) >= batch_size:
                tgt_conn.executemany(f"INSERT INTO {table_name} VALUES ({placeholders})", batch)
                tgt_conn.commit()
                count += len(batch)
                print(f"  已插入 {count:,} 行...", end='\r')
                batch = []
        
        # 插入剩余数据
        if batch:
            tgt_conn.executemany(f"INSERT INTO {table_name} VALUES ({placeholders})", batch)
            tgt_conn.commit()
            count += len(batch)
        
        # 获取目标数据库的行数和股票数
        tgt_rows = tgt_conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        tgt_stocks = tgt_conn.execute(f"SELECT COUNT(DISTINCT ts_code) FROM {table_name}").fetchone()[0]
        
        tgt_conn.close()
        
        print(f"\n  完成: {count:,} 行, {tgt_stocks:,} 只股票")
        print(f"  目标文件: {target_db}")
        print(f"  目标大小: {os.path.getsize(target_db) / (1024*1024):.2f} MB")
        
        results[year] = {
            'rows': tgt_rows,
            'stocks': tgt_stocks,
            'file': target_db,
            'size': os.path.getsize(target_db)
        }
    
    src_conn.close()
    
    # 验证总计
    print(f"\n{'='*60}")
    print("验证结果")
    print(f"{'='*60}")
    total_split_rows = sum(r['rows'] for r in results.values())
    print(f"原数据库行数: {total_rows:,}")
    print(f"拆分后总行数: {total_split_rows:,}")
    print(f"数据完整性: {'✅ 一致' if total_rows == total_split_rows else '❌ 不一致'}")
    
    print(f"\n各年份详情:")
    for year in YEARS:
        r = results[year]
        print(f"  {year}: {r['rows']:,} 行, {r['stocks']:,} 只股票, {r['size']/(1024*1024):.2f} MB")
    
    # 删除备份文件
    print(f"\n删除备份文件...")
    os.remove(BACKUP_DB)
    print(f"备份已删除: {BACKUP_DB}")
    
    print(f"\n{'='*60}")
    print("拆分完成！")
    print(f"{'='*60}")
    return True

if __name__ == '__main__':
    success = split_minline()
    if not success:
        exit(1)
