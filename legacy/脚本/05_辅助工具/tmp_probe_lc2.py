# -*- coding: utf-8 -*-
"""检查 .lc1 原始字节"""
import struct

f = open(r'D:\new_tdx64\vipdoc\sh\minline\sh600367.lc1', 'rb')
b = f.read(128)
print('前128字节 hex:')
print(b.hex(' '))
print()

# 尝试不同偏移解析
for off in [0, 1, 2, 4, 8, 512]:
    f.seek(off)
    rec = f.read(32)
    if len(rec) < 32:
        continue
    vals = struct.unpack('<IIIIIfII', rec)
    print(f'offset={off}: {vals}')

print()
# 尝试大端
f.seek(0)
rec = f.read(32)
vals = struct.unpack('>IIIIIfII', rec)
print(f'big-endian off=0: {vals}')

# 尾部最后 32 字节原始
f.seek(0, 2)
size = f.tell()
f.seek(size - 32)
rec = f.read(32)
print(f'尾部最后32字节 hex: {rec.hex(" ")}')
vals = struct.unpack('<IIIIIfII', rec)
print(f'尾部 little-endian: {vals}')
f.close()
