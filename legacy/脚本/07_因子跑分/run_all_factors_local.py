#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sqlite3, sys, threading, logging
sys.path.append('D:/finance')
from core.worker_handlers import handle_run_factor_plan

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S')

db = r"D:\finance\data\fin_factor.db"
conn = sqlite3.connect(db)
c = conn.cursor()
plans = c.execute("SELECT id, name FROM scoring_plans ORDER BY id").fetchall()
conn.close()

class DummyTask:
    id = 'dummy'
    meta = {}

cancel = threading.Event()

results = []
for pid, name in plans:
    print(f"\n===== 跑分 plan_id={pid} ({name}) =====")
    try:
        r = handle_run_factor_plan(DummyTask(), lambda *a, **k: None, cancel, pid, backfill_days=5)
        results.append((pid, name, r))
        print(f"  结果: {r}")
    except Exception as e:
        results.append((pid, name, f"ERROR: {e}"))
        print(f"  异常: {e}")

print("\n========== 汇总 ==========")
for pid, name, r in results:
    print(f"  plan_id={pid} {name}: {r}")
