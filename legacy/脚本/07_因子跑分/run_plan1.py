#!/usr/bin/env python3
import sys, threading
sys.path.append('D:/finance')
from core.worker_handlers import handle_run_factor_plan

class DummyTask:
    id='dummy'
    meta={}

def progress_cb(*a, **k):
    pass

cancel=threading.Event()
plan_id=1
result=handle_run_factor_plan(DummyTask(), progress_cb, cancel, plan_id, backfill_days=1)
print('Result for plan_id',plan_id,':',result)
