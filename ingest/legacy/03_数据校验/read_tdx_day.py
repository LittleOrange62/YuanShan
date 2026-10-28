# -*- coding: utf-8 -*-
"""读取通达信day文件并打印最后N条记录"""
import struct
import datetime
import sys

def read_tdx_day(filepath, tail=8):
    with open(filepath, 'rb') as f:
        data = f.read()
    n = len(data) // 32
    print(f'文件: {filepath}')
    print(f'总记录数: {n}, 文件大小: {len(data)} bytes')
    if n == 0:
        print('无数据')
        return
    print(f'最后 {tail} 条记录:')
    for i in range(max(0, n - tail), n):
        rec = data[i*32:(i+1)*32]
        date_int, open_p, high_p, low_p, close_p, amount, vol, reserved = struct.unpack('<IIIIIfII', rec)
        d = datetime.datetime.strptime(str(date_int), '%Y%m%d')
        print(f'{d.date()}  开:{open_p/100:.2f}  高:{high_p/100:.2f}  低:{low_p/100:.2f}  收:{close_p/100:.2f}  量:{vol}  额:{amount:.0f}')

if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else r'D:\new_tdx64\vipdoc\sh\lday\sh600367.day'
    tail = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    read_tdx_day(path, tail)
