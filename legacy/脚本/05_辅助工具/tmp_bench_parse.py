# -*- coding: utf-8 -*-
"""测 parse 速度"""
import struct
import time
from datetime import date, timedelta

LO_BASE = 45881
DATE_BASE = date(2026, 8, 25)

_LO_CACHE = {}


def _lo_to_date_cached(lo):
    d = _LO_CACHE.get(lo)
    if d is None:
        d = DATE_BASE - timedelta(days=LO_BASE - lo)
        _LO_CACHE[lo] = d
    return d


def parse_lc_file(filepath):
    with open(filepath, 'rb') as f:
        data = f.read()
    recs = []
    for d, o, h, l, c, amt, vol, rsv in struct.iter_unpack('<IfffffII', data):
        lo = d & 0xFFFF
        minute = d >> 16
        if lo == 0 or minute > 1439:
            continue
        day = _lo_to_date_cached(lo)
        dt_str = f'{day:%Y%m%d} {minute//60:02d}:{minute%60:02d}'
        recs.append((dt_str, o, h, l, c, amt, float(vol)))
    return recs


# 测 100 个文件
import os
files = []
for m in ['sh', 'sz']:
    d = rf'D:\new_tdx64\vipdoc\{m}\minline'
    for fn in sorted(os.listdir(d))[:50]:
        files.append(os.path.join(d, fn))

t0 = time.time()
total = 0
for fp in files:
    recs = parse_lc_file(fp)
    total += len(recs)
el = time.time() - t0
print(f'parse {len(files)} 文件 {total} 条: {el:.1f}s → {total/el:.0f} 条/秒')
