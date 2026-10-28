# -*- coding: utf-8 -*-
"""P2.1 对账：FinanceTool 原副本 vs YuanShan 新副本，跑同一批 vipdoc 文件逐条比。"""
import os
import sys

FT_ROOT = os.environ.get('FT_ROOT', '')   # 一次性对账脚本，只在参考仓库还在时能跑
if not FT_ROOT:
    raise SystemExit('需要 set FT_ROOT=<参考仓库路径>：这是对账用的历史脚本，不在产品链路上')
sys.path.insert(0, FT_ROOT)
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from ingest import tdx_binary as NEW
spec = importlib.util.spec_from_file_location(
    'ft_tb', os.path.join(FT_ROOT, 'app', 'ingest', 'tdx_binary.py'))
OLD = importlib.util.module_from_spec(spec)
sys.modules['ft_tb'] = OLD   # dataclass + `from __future__ import annotations` 需要模块已注册
spec.loader.exec_module(OLD)

VIP = Path(r'D:\new_tdx64\vipdoc')
def sample(files, n):
    step = max(1, len(files)//n); return files[::step][:n]

bad = tot = 0
for mk in ('sh', 'sz', 'bj'):
    days = sample(sorted((VIP/mk/'lday').glob('*.day')), 25)
    for f in days:
        a, b = list(OLD.parse_day(f)), list(NEW.parse_day(f))
        tot += len(a)
        # 必须比 repr 而不是 ==：两份是分属不同 module 的同名 dataclass，
        # dataclass.__eq__ 要求 other.__class__ is self.__class__，跨模块恒 False。
        # 用 == 对账会把"完全一致的解析"报成 135 个文件有差异。
        if [repr(x) for x in a] != [repr(y) for y in b] or not a:
            bad += 1; print('  [DIFF/EMPTY] %s 老%d 新%d' % (f.name, len(a), len(b)))
    for per, sub in ((1, 'minline'), (5, 'fzline')):
        fs = sample(sorted((VIP/mk/'fzline' if per == 5 else VIP/mk/'minline').glob('*.%s' % ('lc5' if per == 5 else 'lc1'))), 10)
        for f in fs:
            a, b = list(OLD.parse_minute(f)), list(NEW.parse_minute(f))
            tot += len(a)
            if [repr(x) for x in a] != [repr(y) for y in b]:
                bad += 1; print('  [DIFF] %s p%s 老%d 新%d' % (f.name, per, len(a), len(b)))
# 文件遍历器也要一致（它决定"哪些文件被摄取"）
for mk in ('sh', 'sz', 'bj'):
    o = [str(p) for p in OLD.iter_day_files(VIP, (mk,))]
    n = [str(p) for p in NEW.iter_day_files(VIP, (mk,))]
    print('  iter_day_files %s: 老 %d 个 / 新 %d 个 / 一致 %s' % (mk, len(o), len(n), o == n))
print('\n比对记录 %s 条，差异文件 %d 个 → %s' % (f'{tot:,}', bad, '完全一致 ✅' if bad == 0 else '有差异 ❌'))
