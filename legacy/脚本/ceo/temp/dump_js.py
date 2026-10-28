import os

root = r'D:\QwenPaw\QwenPaw Desktop\binaries\qwenpaw-backend\_internal\qwenpaw\console\assets'
for fn in ['ReactAgentCard-e5sBsBmY.js', 'useAgentConfig-C5M2PK0z.js', 'useAgentConfig-CYDcIJj8.js']:
    p = os.path.join(root, fn)
    with open(p, encoding='utf-8', errors='ignore') as fh:
        print('=====', fn, '=====')
        print(fh.read())
        print()
