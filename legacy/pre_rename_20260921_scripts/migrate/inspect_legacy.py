# -*- coding: utf-8 -*-
"""
只读 dump 旧 SQLite 的建表语句与样本行，用来对齐字段映射。

用法：
    python scripts\\migrate\\inspect_legacy.py tq_core.db
    python scripts\\migrate\\inspect_legacy.py tq_daily_2026.db --sample 2
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('db')
    ap.add_argument('--table', action='append', default=None)
    ap.add_argument('--sample', type=int, default=1)
    args = ap.parse_args()

    path = config.LEGACY_DATA / args.db
    if not path.exists():
        print('不存在：%s' % path)
        return 1

    c = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True, timeout=5)
    c.execute('PRAGMA busy_timeout=5000')
    rows = c.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()

    for name, ddl in rows:
        if args.table and name not in args.table:
            continue
        print('\n' + '=' * 70)
        print(ddl)
        if args.sample > 0:
            try:
                cur = c.execute('SELECT * FROM "%s" LIMIT %d' % (name, args.sample))
                cols = [d[0] for d in cur.description]
                for r in cur.fetchall():
                    print('-- 样本：')
                    for k, v in zip(cols, r, strict=False):
                        print('     %-20s %r' % (k, v))
            except sqlite3.Error as exc:
                print('-- 取样失败：%s' % exc)
    c.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
