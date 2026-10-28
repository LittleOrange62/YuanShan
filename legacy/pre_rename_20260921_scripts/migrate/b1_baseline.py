# -*- coding: utf-8 -*-
"""
B1 · 迁移基线快照。

在动任何数据之前，把 D:\\finance\\data 下每个 SQLite 库的每张表的行数记进
ops.migration_baseline。B6 对账时用它判断"迁完了没、迁丢了没"。

只读打开（`file:...?mode=ro`），不碰源库。tq_minline_2026.db 实测过整表
LOCKED，拿不到就记 row_count=NULL 并在 extra 里写下原因，不让它挡住流程。

用法：
    python scripts\\migrate\\b1_baseline.py
    python scripts\\migrate\\b1_baseline.py --only tq_core.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402

# 这些不是数据，跳过
SKIP_SUFFIX = ('-shm', '-wal')
SKIP_NAME = ('tq_daily_2026_backup_20260823_165557.db',)

BUSY_TIMEOUT_MS = 5000


def sqlite_ro(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True, timeout=5)
    c.execute('PRAGMA busy_timeout=%d' % BUSY_TIMEOUT_MS)
    return c


def tables(c: sqlite3.Connection) -> list[str]:
    rows = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return [r[0] for r in rows]


def count_rows(c: sqlite3.Connection, table: str) -> tuple[int | None, str | None]:
    try:
        return c.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0], None
    except sqlite3.Error as exc:
        return None, str(exc)


def date_span(c: sqlite3.Connection, table: str) -> dict:
    """有 trade_date 列的表顺手记下日期区间，B6 对账好用。"""
    try:
        cols = {r[1] for r in c.execute('PRAGMA table_info("%s")' % table)}
    except sqlite3.Error:
        return {}
    if 'trade_date' not in cols:
        return {}
    try:
        lo, hi, nd = c.execute(
            'SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT trade_date) '
            'FROM "%s"' % table).fetchone()
        return {'date_min': lo, 'date_max': hi, 'distinct_dates': nd}
    except sqlite3.Error:
        return {}


UPSERT = """
INSERT INTO ops.migration_baseline (src_db, src_table, row_count, extra)
VALUES (%s, %s, %s, %s)
ON CONFLICT (src_db, src_table) DO UPDATE
   SET row_count = excluded.row_count,
       extra     = excluded.extra,
       captured_at = now()
"""


def snapshot(db_path: Path) -> list[tuple]:
    out = []
    try:
        c = sqlite_ro(db_path)
    except sqlite3.Error as exc:
        print('  [ERR ] 打不开：%s' % exc)
        return [(db_path.name, '<database>', None,
                 json.dumps({'error': str(exc)}, ensure_ascii=False))]
    try:
        tbs = tables(c)
        if not tbs:
            print('  （无表）')
        for t in tbs:
            n, err = count_rows(c, t)
            extra = date_span(c, t) if n else {}
            if err:
                extra['error'] = err
            out.append((db_path.name, t, n,
                        json.dumps(extra, ensure_ascii=False) if extra else None))
            span = ''
            if extra.get('date_min'):
                span = '  %s..%s (%s 个交易日)' % (
                    extra['date_min'], extra['date_max'], extra['distinct_dates'])
            print('  %-28s %14s%s%s' % (
                t, '锁住/失败' if n is None else f'{n:,}', span,
                '  ! ' + err[:60] if err else ''))
    finally:
        c.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', action='append', default=None,
                    help='只处理指定文件名，可重复')
    args = ap.parse_args()

    src = config.LEGACY_DATA
    if not src.exists():
        print('源目录不存在：%s' % src)
        return 1

    files = sorted(p for p in src.glob('*.db')
                   if not p.name.endswith(SKIP_SUFFIX) and p.name not in SKIP_NAME)
    if args.only:
        wanted = set(args.only)
        files = [p for p in files if p.name in wanted]
    if not files:
        print('没有匹配的库文件')
        return 1

    rows: list[tuple] = []
    for p in files:
        size_mb = p.stat().st_size / 1024 / 1024
        print('\n=== %s  (%.1f MB) ===' % (p.name, size_mb))
        rows.extend(snapshot(p))

    engine.executemany(UPSERT, rows, role='app')

    total = sum(r[2] or 0 for r in rows)
    locked = [f'{r[0]}.{r[1]}' for r in rows if r[2] is None]
    print('\n--- 汇总 ---')
    print('库文件 %d 个，表 %d 张，可读行数合计 %s' % (len(files), len(rows), f'{total:,}'))
    if locked:
        print('读不到行数的表 %d 张：%s' % (len(locked), ', '.join(locked)))
    print('已写入 ops.migration_baseline')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
