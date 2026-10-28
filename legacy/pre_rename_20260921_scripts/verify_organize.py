# -*- coding: utf-8 -*-
"""Verify the post-organization state of D:\\finance and its 脚本 tree."""
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

FINANCE = r'D:\finance'
SCRIPTS = os.path.join(FINANCE, '脚本')

print('=== D:\\finance top level ===')
for n in sorted(os.listdir(FINANCE)):
    p = os.path.join(FINANCE, n)
    kind = 'DIR ' if os.path.isdir(p) else 'FILE'
    extra = '' if os.path.isdir(p) else '  %d B' % os.path.getsize(p)
    print('[%s] %s%s' % (kind, n, extra))

loose = [n for n in os.listdir(FINANCE)
         if n.lower().endswith('.py') and os.path.isfile(os.path.join(FINANCE, n))]
print('\nloose .py at D:\\finance root => %d %s' % (len(loose), loose or ''))

print('\n=== D:\\finance\\脚本 ===')
loose_s = []
for n in sorted(os.listdir(SCRIPTS)):
    p = os.path.join(SCRIPTS, n)
    if os.path.isdir(p):
        py = sum(1 for _r, _d, fs in os.walk(p) for f in fs if f.lower().endswith('.py'))
        allf = sum(len(fs) for _r, _d, fs in os.walk(p))
        print('[DIR ] %-16s py=%-4d all=%d' % (n, py, allf))
    else:
        print('[FILE] %s  %d B' % (n, os.path.getsize(p)))
        if n.lower().endswith('.py'):
            loose_s.append(n)
print('\nloose .py at 脚本 root => %d %s' % (len(loose_s), loose_s or ''))

total_py = sum(1 for _r, _d, fs in os.walk(SCRIPTS) for f in fs if f.lower().endswith('.py'))
total_all = sum(len(fs) for _r, _d, fs in os.walk(SCRIPTS))
print('\n脚本 total: py=%d  all_files=%d' % (total_py, total_all))
