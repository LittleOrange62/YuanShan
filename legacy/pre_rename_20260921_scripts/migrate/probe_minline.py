# -*- coding: utf-8 -*-
"""
探查 tq_minline_2026.db 的实际状态。

B1 基线里这张表报 "database disk image is malformed" —— 这不是锁，是页面损坏。
先确认损坏范围，再决定是抢救还是从 vipdoc 重解析。

只读打开，不写源库。quick_check 只扫有限页，不做 37 GB 全量校验。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402

DB = config.LEGACY_DATA / 'tq_minline_2026.db'


def probe():
    print('文件：%s  (%.1f GB)' % (DB, DB.stat().st_size / 1024**3))
    c = sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True, timeout=5)
    c.execute('PRAGMA busy_timeout=5000')

    def attempt(label, sql):
        try:
            rows = c.execute(sql).fetchall()
            print('  [ok]   %-24s %s' % (label, str(rows)[:200]))
            return rows
        except sqlite3.Error as exc:
            print('  [ERR]  %-24s %s' % (label, exc))
            return None

    attempt('sqlite_master', "SELECT sql FROM sqlite_master WHERE name='stock_minline'")
    attempt('page_size/count', 'PRAGMA page_size; ')
    attempt('quick_check(20)', 'PRAGMA quick_check(20)')
    attempt('前 3 行', 'SELECT * FROM stock_minline LIMIT 3')
    attempt('最小 rowid', 'SELECT MIN(rowid), MAX(rowid) FROM stock_minline')
    attempt('索引区间', 'SELECT MIN(trade_date), MAX(trade_date) FROM stock_minline')
    c.close()


if __name__ == '__main__':
    probe()
