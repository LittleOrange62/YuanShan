import os

root = r'D:\QwenPaw\QwenPaw Desktop\binaries\qwenpaw-backend\_internal\qwenpaw\console\assets'
kws = ['scroll_config', 'history_retention_days', 'summarize_unheadlined', 'offload_dialog', 'light_context_config', 'compact_threshold_ratio', 'strategy']
hits = []
for fn in os.listdir(root):
    if not fn.endswith('.js'):
        continue
    p = os.path.join(root, fn)
    try:
        with open(p, encoding='utf-8', errors='ignore') as fh:
            s = fh.read()
    except Exception:
        continue
    for kw in kws:
        if kw in s:
            hits.append((kw, fn, len(s)))
print('TOTAL HITS:', len(hits))
seen = set()
for kw, fn, sz in sorted(hits):
    key = (kw, fn)
    if key in seen:
        continue
    seen.add(key)
    print(f'{kw:28s} -> {fn} ({sz})')
