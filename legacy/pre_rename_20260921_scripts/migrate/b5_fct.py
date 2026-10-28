# -*- coding: utf-8 -*-
"""
B5 · 迁移 fct 域与自选股，并补上股本。

来源是 **D:\\financeWeb\\data**，不是 D:\\finance\\data。
B1 基线已证明 D:\\finance\\data 下 fin_* 七个库全是 0 行空壳，评分与自选股
一直躺在旧 Web 系统目录里。

四件事：

1. **补股本** ← fin_data.db.stock_basic（9,064 行，含 total_shares/float_shares）
   这是之前缺的那一块。没股本就算不出换手率，而换手率是因子体系的核心输入。
   代码格式是 'SH000001' 前缀式，要转成 '000001.SH'。

2. **自选股** ← fin_watchlist.db.watchlist_groups(23) + watchlist_stocks(222)
   旧库 symbol 是裸 6 位（'603296'），没有市场后缀，按前缀补全市场。
   focus_stocks(2) 并成一个 is_focus=true 的分组。

3. **评分结果** ← fin_factor.db.score_results（75,995 行）
   x_score / y_score / oper_score 分别落到 stock_quality / env_resonance /
   oper_score 三个功能点。**全部标 is_stale=true** —— 新系统的权重与公式
   与旧系统不同，这些值只作历史参考，不能当成当前有效评分展示。
   total_score 不迁：实测它与 oper_score 完全相同，迁两遍等于制造重复口径。

4. **旧权重配置存档** ← scoring_plans(1) + scoring_factors(15)
   旧的 X 轴 0.4 / Y 轴 0.6 与 15 个子因子权重存进 config.setting，
   方便后面在 /blocks 页面上对照着调，不直接覆盖新系统的默认绑定。

用法：
    python scripts\\migrate\\b5_fct.py --dry-run
    python scripts\\migrate\\b5_fct.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402

WEB_DATA = Path(r'D:\financeWeb\data')

# 旧 score_results 的三列 → 新系统的功能点
SCORE_CAP = {'x_score': 'stock_quality',
             'y_score': 'env_resonance',
             'oper_score': 'oper_score'}


def ro(p: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f'file:{p.as_posix()}?mode=ro', uri=True, timeout=15)
    c.execute('PRAGMA busy_timeout=15000')
    return c


def norm_code(raw: str, market_hint: str | None = None) -> str | None:
    """
    统一成 '600367.SH'。旧库里有三种写法：
        'SH600519'  前缀式（score_results / stock_basic）
        '603296'    裸 6 位（watchlist_stocks）
        '600367.SH' 已是标准式
    """
    if not raw:
        return None
    s = str(raw).strip().upper()
    if '.' in s:
        sym, _, mkt = s.partition('.')
        return f'{sym}.{mkt}' if mkt in ('SH', 'SZ', 'BJ') else None
    if s[:2] in ('SH', 'SZ', 'BJ') and s[2:].isdigit() and len(s) == 8:
        return f'{s[2:]}.{s[:2]}'
    if s.isdigit() and len(s) == 6:
        if market_hint in ('SH', 'SZ', 'BJ'):
            return f'{s}.{market_hint}'
        # 按交易所代码段判市场
        if s.startswith(('600', '601', '603', '605', '688', '689',
                         '510', '511', '512', '513', '515', '518', '588', '900')):
            return f'{s}.SH'
        if s.startswith(('000', '001', '002', '003', '300', '301', '159', '200')):
            return f'{s}.SZ'
        if s.startswith(('43', '83', '87', '88', '92')):
            return f'{s}.BJ'
        return None
    return None


def to_date(s) -> dt.date | None:
    if not s:
        return None
    t = str(s).strip()[:10].replace('-', '').replace('/', '')
    if len(t) != 8 or not t.isdigit():
        return None
    try:
        return dt.date(int(t[:4]), int(t[4:6]), int(t[6:8]))
    except ValueError:
        return None


# ------------------------------------------------------------------ 1 股本

def migrate_shares(*, dry_run: bool) -> dict:
    """
    从 D:\\financeWeb\\data\\fin_data.db 的 stock_basic 补股本。

    两条实测结论，决定了只补个股、只补流通股：
    1. `total_shares` 9,064 行**全是 0** —— 这一列旧系统从来没填过。所以总股本
       在本系统里保持 NULL，不填 0（填 0 会让下游算出无穷大的换手率）。
    2. 旧库按 6 位代码抓数据，不区分交易所，同一个 6 位号在 SH/SZ 两边都写了
       **同一个值**：'SH000001'(上证指数) 和 'SZ000001'(平安银行) 的 float_shares
       都是 19,405,601,200 —— 那是平安银行的流通股，指数不可能有股本。
       所以非个股的行拿到的股本是这个 bug 的产物，一律不写。
    """
    p = WEB_DATA / 'fin_data.db'
    if not p.exists():
        return {'skipped': '文件不存在'}
    # 只认个股：见上面第 2 条，非个股的股本是旧库串市场串出来的
    stocks = {r['ts_code'] for r in engine.query(
        "SELECT ts_code FROM ref.security WHERE asset_type = 'stock'")}
    c = ro(p)
    try:
        rows_raw = c.execute(
            'SELECT symbol, code, name, market, total_shares, float_shares '
            'FROM stock_basic').fetchall()
    finally:
        c.close()

    upd, not_stock, zero = [], 0, 0
    for symbol, _code, _name, market, total, float_ in rows_raw:
        ts = norm_code(symbol, market)
        if not ts or ts not in stocks:
            not_stock += 1
            continue
        t = int(total) if total and total > 0 else None
        f = int(float_) if float_ and float_ > 0 else None
        if not t and not f:
            zero += 1
            continue
        upd.append((t, f, ts))

    stat = {'read': len(rows_raw), 'will_update': len(upd),
            'both_zero': zero, 'not_stock_skipped': not_stock}
    if upd and not dry_run:
        stat['updated'] = engine.executemany(
            """
            UPDATE ref.security
               SET total_share = coalesce(%s, total_share),
                   float_share = coalesce(%s, float_share),
                   updated_at  = now()
             WHERE ts_code = %s AND asset_type = 'stock'
            """, upd, role='etl')
    return stat


# ------------------------------------------------------------------ 2 自选股

def migrate_watchlist(*, dry_run: bool) -> dict:
    p = WEB_DATA / 'fin_watchlist.db'
    if not p.exists():
        return {'skipped': '文件不存在'}
    c = ro(p)
    try:
        groups = c.execute(
            'SELECT id, name, description FROM watchlist_groups ORDER BY id'
        ).fetchall()
        stocks = c.execute(
            'SELECT group_id, symbol, name, sort_order FROM watchlist_stocks'
        ).fetchall()
        focus = c.execute('SELECT symbol, name, notes FROM focus_stocks').fetchall()
    finally:
        c.close()

    stat = {'groups': len(groups), 'stocks': len(stocks), 'focus': len(focus),
            'bad_code': 0, 'inserted_groups': 0, 'inserted_stocks': 0}
    if dry_run:
        return stat

    id_map: dict[int, int] = {}
    for old_id, name, desc in groups:
        gid = engine.scalar(
            """
            INSERT INTO config.watchlist_group (name, owner, note, seq)
            VALUES (%s, 'fin', %s, %s)
            ON CONFLICT (owner, name) DO UPDATE SET note = EXCLUDED.note
            RETURNING group_id
            """, (name, desc, old_id), role='app')
        id_map[old_id] = gid
        stat['inserted_groups'] += 1

    pairs = []
    for old_gid, symbol, _name, _seq in stocks:
        gid = id_map.get(old_gid)
        ts = norm_code(symbol)
        if not gid or not ts:
            stat['bad_code'] += 1
            continue
        pairs.append((gid, ts))
    if pairs:
        stat['inserted_stocks'] = engine.executemany(
            'INSERT INTO config.watchlist_stock (group_id, ts_code) VALUES (%s, %s) '
            'ON CONFLICT (group_id, ts_code) DO NOTHING', pairs, role='app')

    if focus:
        fid = engine.scalar(
            """
            INSERT INTO config.watchlist_group (name, owner, is_focus, seq, note)
            VALUES ('重点关注', 'fin', true, 0, '迁自旧 focus_stocks')
            ON CONFLICT (owner, name) DO UPDATE SET is_focus = true
            RETURNING group_id
            """, role='app')
        fpairs = [(fid, norm_code(s), n) for s, _nm, n in
                  ((f[0], f[1], f[2]) for f in focus)]
        fpairs = [(g, t, note) for g, t, note in fpairs if t]
        if fpairs:
            stat['focus_inserted'] = engine.executemany(
                'INSERT INTO config.watchlist_stock (group_id, ts_code, note) '
                'VALUES (%s, %s, %s) ON CONFLICT (group_id, ts_code) DO NOTHING',
                fpairs, role='app')
    return stat


# ------------------------------------------------------------------ 3 评分

def migrate_scores(*, dry_run: bool) -> dict:
    p = WEB_DATA / 'fin_factor.db'
    if not p.exists():
        return {'skipped': '文件不存在'}
    c = ro(p)
    try:
        raw = c.execute(
            'SELECT symbol, trade_date, x_score, y_score, oper_score, total_score '
            'FROM score_results').fetchall()
    finally:
        c.close()

    rows, bad_code, bad_date = [], 0, 0
    dates: set[dt.date] = set()
    for symbol, td, x, y, oper, _total in raw:
        ts = norm_code(symbol)
        d = to_date(td)
        if not ts:
            bad_code += 1
            continue
        if not d:
            bad_date += 1
            continue
        dates.add(d)
        for col, val in (('x_score', x), ('y_score', y), ('oper_score', oper)):
            if val is None:
                continue
            rows.append((ts, d, SCORE_CAP[col], float(val)))

    stat = {'source_rows': len(raw), 'score_rows': len(rows),
            'bad_code': bad_code, 'bad_date': bad_date,
            'distinct_dates': len(dates),
            'date_range': f'{min(dates)} .. {max(dates)}' if dates else '-'}
    if rows and not dry_run:
        stat['inserted'] = engine.executemany(
            """
            INSERT INTO fct.score (ts_code, trade_date, cap_key, value, is_stale)
            VALUES (%s, %s, %s, %s, true)
            ON CONFLICT (ts_code, trade_date, cap_key) DO UPDATE
               SET value = EXCLUDED.value, is_stale = true,
                   calculated_at = now()
            """, rows, role='app')
    return stat


# ------------------------------------------------------------------ 4 旧权重存档

def archive_legacy_weights(*, dry_run: bool) -> dict:
    p = WEB_DATA / 'fin_factor.db'
    if not p.exists():
        return {'skipped': '文件不存在'}
    c = ro(p)
    try:
        plans = c.execute(
            'SELECT id, name, description FROM scoring_plans').fetchall()
        factors = c.execute(
            'SELECT plan_id, field, field_name, weight, calc_type, params, '
            'sort_order FROM scoring_factors ORDER BY plan_id, sort_order'
        ).fetchall()
    finally:
        c.close()

    payload = {
        'plans': [{'id': i, 'name': n, 'description': d} for i, n, d in plans],
        'factors': [{'plan_id': pid, 'field': f, 'label': fn, 'weight': w,
                     'calc_type': ct, 'params': pr, 'seq': so}
                    for pid, f, fn, w, ct, pr, so in factors],
        'archived_at': dt.datetime.now().isoformat(timespec='seconds'),
        'note': '旧 financeWeb 的评分方案原样存档，供 /blocks 页面对照调参，'
                '不自动覆盖新系统默认绑定',
    }
    stat = {'plans': len(plans), 'factors': len(factors)}
    if not dry_run:
        engine.execute(
            """
            INSERT INTO config.setting (key, value, label, category)
            VALUES ('legacy_scoring_plan', %s, '旧系统评分方案存档', '迁移')
            ON CONFLICT (key) DO UPDATE
               SET value = EXCLUDED.value, updated_at = now()
            """, (json.dumps(payload, ensure_ascii=False),), role='app')
        stat['saved'] = 'config.setting.legacy_scoring_plan'
    return stat


# ------------------------------------------------------------------ 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if not WEB_DATA.exists():
        print('源目录不存在：%s' % WEB_DATA)
        return 1

    batch_id = -1
    if not args.dry_run:
        batch_id = ops_repo.begin_batch(
            'legacy_financeweb', 'fct+config', source_path=str(WEB_DATA))
        print('批次 %d' % batch_id)

    total = 0
    try:
        for label, fn in (('1. 补股本 → ref.security', migrate_shares),
                          ('2. 自选股 → config.watchlist_*', migrate_watchlist),
                          ('3. 评分 → fct.score', migrate_scores),
                          ('4. 旧权重存档 → config.setting', archive_legacy_weights)):
            print('\n=== %s ===' % label)
            st = fn(dry_run=args.dry_run)
            for k, v in st.items():
                print('    %-20s %s' % (k, v))
            total += st.get('inserted', 0) or st.get('updated', 0) or 0
        if not args.dry_run:
            ops_repo.end_batch(batch_id, status='ok', row_count=total)
    except Exception as exc:
        if not args.dry_run:
            ops_repo.end_batch(batch_id, status='failed', message=str(exc)[:2000])
        raise

    if args.dry_run:
        print('\n--dry-run，未写库')
        return 0

    print('\n=== 验收 ===')
    for label, sql in (
            ('个股有流通股', "SELECT count(*) FROM ref.security "
                             "WHERE asset_type='stock' AND float_share IS NOT NULL"),
            ('个股缺流通股', "SELECT count(*) FROM ref.security "
                             "WHERE asset_type='stock' AND float_share IS NULL"),
            ('非个股误带股本', 'SELECT count(*) FROM ref.security '
                               "WHERE asset_type <> 'stock' "
                               'AND (float_share IS NOT NULL OR total_share IS NOT NULL)'),
            ('总股本（旧库全 0）', "SELECT count(*) FROM ref.security "
                                   'WHERE total_share IS NOT NULL'),
            ('自选分组', 'SELECT count(*) FROM config.watchlist_group'),
            ('自选个股', 'SELECT count(*) FROM config.watchlist_stock'),
            ('fct.score', 'SELECT count(*) FROM fct.score'),
            ('  其中 stale', 'SELECT count(*) FROM fct.score WHERE is_stale'),
            ('评分按功能点', "SELECT string_agg(cap_key||':'||n, ' ') FROM "
                             '(SELECT cap_key, count(*) n FROM fct.score '
                             'GROUP BY cap_key ORDER BY cap_key) t'),
            ('评分日期区间', "SELECT min(trade_date)||' .. '||max(trade_date) "
                             'FROM fct.score'),
    ):
        print('  %-16s %s' % (label, engine.scalar(sql)))

    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
