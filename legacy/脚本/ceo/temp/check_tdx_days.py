# -*- coding: utf-8 -*-
"""只读：检查通达信 .day 文件最新日期 + MCP worker 缓存状态"""
import struct, os, glob

# 1. 读取 sh600000.day 最后几条记录
f = r'D:\new_tdx64\vipdoc\sh\lday\sh600000.day'
if os.path.exists(f):
    size = os.path.getsize(f)
    recs = size // 32
    print(f'sh600000.day: {size} bytes, {recs} 条记录')
    with open(f, 'rb') as fh:
        fh.seek(max(0, (recs - 5) * 32))
        for i in range(5):
            data = fh.read(32)
            if len(data) < 32:
                break
            date, open_, high, low, close, amount, vol, _ = struct.unpack('<IIIIIfII', data)
            print(f'  {date} open={open_/100:.2f} high={high/100:.2f} low={low/100:.2f} close={close/100:.2f}')
else:
    print('sh600000.day 不存在')

# 2. 检查所有 .day 文件的最后修改时间分布
print('\n=== .day 文件修改时间分布（抽样）===')
for pat in [r'D:\new_tdx64\vipdoc\sh\lday\*.day', r'D:\new_tdx64\vipdoc\sz\lday\*.day']:
    files = glob.glob(pat)
    if files:
        import datetime
        mt = sorted([os.path.getmtime(x) for x in files], reverse=True)[:3]
        for t in mt:
            print(f'  {pat}: {datetime.datetime.fromtimestamp(t)}')
        print(f'  {pat}: 共{len(files)}个文件')
