# -*- coding: utf-8 -*-
"""摸底4: .lc1/.lc5 格式与日期范围"""
import struct
import os


def read_lc(f, n=5, tail=5):
    """读取 lc 文件前/后 n 条"""
    size = os.path.getsize(f)
    cnt = size // 32
    recs = []
    with open(f, 'rb') as fp:
        # 头部
        for _ in range(n):
            b = fp.read(32)
            if len(b) < 32:
                break
            d = struct.unpack('<IIIIIfII', b)
            recs.append(('head', d))
        # 尾部
        fp.seek(size - tail * 32)
        for _ in range(tail):
            b = fp.read(32)
            if len(b) < 32:
                break
            d = struct.unpack('<IIIIIfII', b)
            recs.append(('tail', d))
    return cnt, recs


for f in [r'D:\new_tdx64\vipdoc\sh\minline\sh600367.lc1',
          r'D:\new_tdx64\vipdoc\sh\fzline\sh600367.lc5',
          r'D:\new_tdx64\vipdoc\sz\minline\sz000001.lc1',
          r'D:\new_tdx64\vipdoc\sz\fzline\sz000001.lc5']:
    if os.path.exists(f):
        cnt, recs = read_lc(f)
        print(f'=== {os.path.basename(f)}: {cnt} 条 ===')
        for tag, d in recs:
            # date, time, open, high, low, close, amount, vol
            print(f'  {tag}: date={d[0]} time={d[1]:04d} o={d[2]/100:.2f} h={d[3]/100:.2f} '
                  f'l={d[4]/100:.2f} c={d[5]/100:.2f} amt={d[6]:.0f} vol={d[7]}')
    else:
        print(f'{f}: 不存在')
