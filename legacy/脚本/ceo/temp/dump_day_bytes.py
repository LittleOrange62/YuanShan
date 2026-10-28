# -*- coding: utf-8 -*-
import struct

path = r'D:\new_tdx64\vipdoc\sz\lday\sz000001.day'
with open(path, 'rb') as f:
    data = f.read()

print(f'文件大小: {len(data)} 字节, 条数: {len(data)//32}')
print('前3条原始字节:')
for i in range(3):
    rec = data[i*32:(i+1)*32]
    print(f'  条{i}: {rec.hex()}')

print()
print('=== 尝试格式1: <IffffIII (date int + OHLC float + amount float + vol int + reserved int) ===')
for i in range(3):
    rec = data[i*32:(i+1)*32]
    date_int, o, h, l, c, amount, vol, res = struct.unpack('<IffffIII', rec)
    print(f'  {date_int} O={o} H={h} L={l} C={c} amount={amount} vol={vol} res={res}')

print()
print('=== 尝试格式2: <IIIIIfII (date int + OHLC int(价格*100) + amount float + vol int + reserved int) ===')
for i in range(3):
    rec = data[i*32:(i+1)*32]
    date_int, o, h, l, c, amount, vol, res = struct.unpack('<IIIIIfII', rec)
    print(f'  {date_int} O={o/100} H={h/100} L={l/100} C={c/100} amount={amount} vol={vol} res={res}')

print()
print('=== 尝试格式3: <IffffII (28字节? 不对) 末条 ===')
last = data[-32:]
date_int, o, h, l, c, amount, vol, res = struct.unpack('<IffffIII', last)
print(f'  末条: {date_int} O={o} H={h} L={l} C={c} amount={amount} vol={vol} res={res}')
