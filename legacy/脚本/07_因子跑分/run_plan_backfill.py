#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, threading, logging
sys.path.append('D:/finance')
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S')

from core.worker_handlers import handle_run_factor_plan

class DummyTask:
    def __init__(self):
        self.id = 'dummy'
        self.meta = {}

def progress_cb(*args, **kwargs):
    if len(args) >= 2:
        print(f"[PROGRESS] {args[0]}% {args[1]}", flush=True)

cancel = threading.Event()
plan_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
backfill_days = int(sys.argv[2]) if len(sys.argv) > 2 else 300

print(f"=== Backfill plan_id={plan_id} backfill_days={backfill_days} ===", flush=True)
try:
    r = handle_run_factor_plan(DummyTask(), progress_cb, cancel, plan_id, backfill_days=backfill_days)
    print('Result:', r, flush=True)
except Exception as e:
    import traceback
    traceback.print_exc()
    print('ERROR:', e, flush=True)
