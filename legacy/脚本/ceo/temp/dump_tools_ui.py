import os

root = r'D:\QwenPaw\QwenPaw Desktop\binaries\qwenpaw-backend\_internal\qwenpaw\console\assets'
p = os.path.join(root, 'index-BgAgmp-n.js')
with open(p, encoding='utf-8', errors='ignore') as fh:
    s = fh.read()
for kw in ['禁用工具', '启用全部', '工具已', 'toggleTool', 'updateTool', 'builtin_tools']:
    i = s.find(kw)
    print(f'--- {kw} @ {i} ---')
    if i >= 0:
        print(s[max(0, i-200):i+300][:600])
    print()
