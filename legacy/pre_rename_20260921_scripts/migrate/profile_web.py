# -*- coding: utf-8 -*-
"""
体检 D:\\financeWeb\\data —— 评分结果与自选股在这里，不在 D:\\finance\\data。

B1 基线已证明 D:\\finance\\data 下 fin_* 七个库全是 0 行空壳，真正有内容的
评分（score_results）与自选股（watchlist_*）在旧 Web 系统的目录里。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

WEB_DATA = Path(r'D:\financeWeb\data')
TARGETS = ('fin_factor.db', 'fin_watchlist.db', 'fin_data.db', 'fin_config.db')


def ro(p: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f'file:{p.as_posix()}?mode=ro', uri=True, timeout=10)
    c.execute('PRAGMA busy_timeout=10000')
    return c


for name in TARGETS:
    p = WEB_DATA / name
    if not p.exists():
        print('\n### %s 不存在' % name)
        continue
    print('\n### %s  (%.2f MB) ###' % (name, p.stat().st_size / 1024 / 1024))
    c = ro(p)
    try:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for t in tables:
            try:
                n = c.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
            except sqlite3.Error as exc:
                print('  %-28s 失败 %s' % (t, exc))
                continue
            print('  %-28s %10s' % (t, f'{n:,}'))
            if n == 0:
                continue
            ddl = c.execute(
                'SELECT sql FROM sqlite_master WHERE name=?', (t,)).fetchone()[0]
            print('      ' + ' '.join(ddl.split())[:400])
            cur = c.execute('SELECT * FROM "%s" LIMIT 1' % t)
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
            if row:
                print('      样本 ' + ', '.join(
                    '%s=%r' % (k, v) for k, v in zip(cols, row, strict=False))[:400])
    finally:
        c.close()
