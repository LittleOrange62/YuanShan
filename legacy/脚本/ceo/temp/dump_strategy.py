import os

root = r'D:\QwenPaw\QwenPaw Desktop\binaries\qwenpaw-backend\_internal\qwenpaw\console\assets'
targets = ['index-BgAgmp-n.js', 'index-CfpiNiXc.js', 'index-DUjFOjkk.js']

def extract_context(s, kw, span=1200):
    out = []
    start = 0
    while True:
        i = s.find(kw, start)
        if i < 0:
            break
        out.append(s[max(0, i-400):i+span])
        start = i + len(kw)
    return out

for fn in targets:
    p = os.path.join(root, fn)
    with open(p, encoding='utf-8', errors='ignore') as fh:
        s = fh.read()
    print('####################', fn, '####################')
    # 1) strategy options
    for kw in ['contextStrategyScroll', 'contextStrategyNative', 'contextStrategyTooltip']:
        for chunk in extract_context(s, kw, 700)[:3]:
            print(f'----- [{kw}] -----')
            print(chunk.replace('\\n', ' ')[:1400])
            print()
