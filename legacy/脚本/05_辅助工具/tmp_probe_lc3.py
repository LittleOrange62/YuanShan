# -*- coding: utf-8 -*-
"""验证 .lc1 记录格式: 每隔 240 条看日期/分钟规律"""
import struct

f = open(r'D:\new_tdx64\vipdoc\sh\minline\sh600367.lc1', 'rb')
size = 0
f.seek(0, 2)
size = f.tell()
cnt = size // 32
print(f'总记录: {cnt}')

# 每隔 240 条读一条, 共读 20 个点
for i in range(0, cnt, 240):
    f.seek(i * 32)
    b = f.read(32)
    d, o, h, l, c, amt, vol, rsv = struct.unpack('<IfffffII', b)
    hi = d >> 16
    lo = d & 0xFFFF
    print(f'#{i:6d} date=0x{d:08X} hi={hi}(min {hi//60}:{hi%60:02d}) lo={lo} '
          f'o={o:.2f} h={h:.2f} l={l:.2f} c={c:.2f} amt={amt:.0f} vol={vol}')
f.close()
