#!/usr/bin/env python3
import sqlite3, os, sys, json
db = r"D:\\finance\\data\\fin_factor.db"
conn = sqlite3.connect(db)
c = conn.cursor()
plans = c.execute("SELECT id, name FROM scoring_plans").fetchall()
print('Scoring plans:', plans)
if not plans:
    sys.exit(0)
plan_id = plans[0][0]
print('Using plan_id', plan_id)
# Run factor plan via core worker_handler function
sys.path.append('D:/finance')
from core.worker_handlers import handle_run_factor_plan
# Mock task and callbacks
class DummyTask:
    def __init__(self):
        self.id = 'dummy'
        self.meta = {}

def progress_cb(*args, **kwargs):
    pass

import threading
cancel_event = threading.Event()
result = handle_run_factor_plan(DummyTask(), progress_cb, cancel_event, plan_id, backfill_days=1)
print('Result:', result)
conn.close()
