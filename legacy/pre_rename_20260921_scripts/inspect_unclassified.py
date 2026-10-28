# -*- coding: utf-8 -*-
"""Inventory the unclassified python scripts so they can be routed to the right folder."""
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

TARGETS = [
    r'D:\finance',
    r'D:\finance\脚本',
    r'D:\finance\data\scripts',
]


def head_summary(path, max_lines=40):
    """Return a compact signal set: docstring/comment head + notable keywords."""
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            lines = [next(f, '') for _ in range(max_lines)]
    except Exception as exc:  # noqa: BLE001
        return '<READ ERROR %s>' % exc, []
    text = ''.join(lines)
    desc = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith('#') or s.startswith('"""') or s.startswith("'''"):
            s = s.strip('#').strip('"').strip("'").strip()
            if s and not s.startswith('-*-') and 'coding' not in s:
                desc.append(s)
        if len(desc) >= 3:
            break
    keys = []
    for kw in ('sqlite3', 'tq_daily', 'tq_core', 'tq_minline', 'fin_data', 'fin_config',
               'fin_factor', 'vipdoc', '.day', '.lc1', '.lc5', 'DELETE', 'DROP',
               'INSERT', 'UPDATE', 'CREATE TABLE', 'requests', 'pywinauto',
               'win32', 'pyautogui', 'subprocess', 'argparse', 'tnf', '17709',
               'PRAGMA', 'ATTACH', 'pandas', 'numpy'):
        if kw in text:
            keys.append(kw)
    return ' | '.join(desc[:3])[:180], keys


def main():
    for base in TARGETS:
        if not os.path.isdir(base):
            print('MISSING DIR: %s' % base)
            continue
        names = sorted(n for n in os.listdir(base) if n.lower().endswith('.py'))
        print('=' * 100)
        print('DIR %s   (%d py files at this level)' % (base, len(names)))
        print('=' * 100)
        for n in names:
            p = os.path.join(base, n)
            size = os.path.getsize(p)
            desc, keys = head_summary(p)
            print('- %-38s %7d B' % (n, size))
            print('    desc: %s' % (desc or '(no docstring/comment)'))
            print('    keys: %s' % (', '.join(keys) or '-'))
        print()


if __name__ == '__main__':
    main()
