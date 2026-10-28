# -*- coding: utf-8 -*-
"""检查连续记录, 找每天边界和日期跳跃"""
import struct

f = open(r'D:\new_tdx64\vipdoc\sh\minline\sh600367.lc1', 'rb')
cnt = 19920
# 打印第 700-980 条
prev_lo = None
prev_hi = None
for i in range(700, 985):
    f.seek(i * 32)
    b = f.read(32)
    d, o, h, l, c, amt, vol, rsv = struct.unpack('<IfffffII', b)
    hi = d >> 16
    lo = d & 0xFFFF
    mark = ''
    if prev_lo is not None and lo != prev_lo:
        mark = f'  <<< 日期序号变化: {prev_lo} -> {lo} (diff={lo-prev_lo})'
    if prev_hi is not None and hi < prev_hi:
        mark += '  <<< 分钟回绕(新的一天开始?)'
    print(f'#{i}: lo={lo} hi={hi}({hi//60}:{hi%60:02d}) o={o:.2f} c={c:.2f}{mark}')
    prev_lo = lo
    prev_hi = hi
f.close()
