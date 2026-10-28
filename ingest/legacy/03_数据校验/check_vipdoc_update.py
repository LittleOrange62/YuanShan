# -*- coding: utf-8 -*-
"""排查 vipdoc 目录 08-22 之后更新的日线文件"""
import os, glob, time, datetime

files = []
for d in [r'D:\new_tdx64\vipdoc\sh\lday', r'D:\new_tdx64\vipdoc\sz\lday',
          r'D:\new_tdx64\vipdoc\sh\minline', r'D:\new_tdx64\vipdoc\sz\minline']:
    if not os.path.isdir(d):
        print('目录不存在:', d)
        continue
    for f in glob.glob(os.path.join(d, '*.*')):
        try:
            if os.path.getmtime(f) > datetime.datetime(2026, 8, 22).timestamp():
                files.append((f, time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(f)))))
        except OSError:
            pass

print('08-22 之后更新的文件总数:', len(files))
# 按目录分组统计
from collections import Counter
c = Counter(os.path.dirname(f) for f, _ in files)
for d, n in c.items():
    print(f'  {d}: {n} 个')
print('--- 样例(最多20个) ---')
for f, t in files[:20]:
    print(t, os.path.basename(f))
