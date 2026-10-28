# -*- coding: utf-8 -*-
r"""
B6 · 迁移后回算与对账。

**回算**（迁移时被置 NULL 的字段，在这里按正确规则算出来）：
    limit_up / limit_down  由 app/compute/limit_prices.py 按**那一天**的档位算
                           （通达信当日反推 → 当日行情可证 → ST 档期 → 规则，出处写进
                           limit_basis）。⚠️ 这里以前写的是 board_limit_ratio(board, is_st)
                           —— 那个函数只答"按当前快照属于哪一档"，拿它套历史正是 2026-10-07
                           消掉的那一类错（16,074 行越界），别再把它当规则源
                           旧系统一刀切 pre_close*1.1，创业板科创板北交所全错
    turnover_rate_percent      volume_shares / float_share × 100
                           依赖 B5 补进来的股本，缺股本的那部分保持 NULL
    pre_close / pct_change    按交易日历的 prev_open_date 补首日之外的缺口

**对账**（拿 operations.migration_baseline 的迁移前行数逐项比对）。
⚠️ 旧 SQLite 仓 `D:inance\data` 已在本机删除（2026-09-25 确认），所以
"抽样比对"这一项现在**必然报"无法验证"而不是通过** —— 行数类比对仍走
operations.migration_baseline，那是迁移时留在 PG 里的基线，不依赖旧库文件。
    行数：旧 stock_daily 的行数要等于 新 stock_daily + index + sector + 排除的固收
    抽样：随机若干 (代码, 日期) 比对 close 与 amount，确认没错位
    覆盖：每个交易日的个股数量，突降说明那天迁漏了

对账结果写进 operations.quality_result，不通过就非零退出，好挂进 CI 或计划任务。

用法：
    python -m ingest.jobs.b6_reconcile                 # 回算 + 对账
    python -m ingest.jobs.b6_reconcile --check-only    # 只对账
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402
from app.db.repo import quote as quote_repo  # noqa: E402

YEARS = (2021, 2022, 2023, 2024, 2025, 2026)
SAMPLE_PER_YEAR = 40
PRICE_TOL = 0.005      # 收盘价允许的绝对误差（numeric(12,4) 舍入）
AMOUNT_TOL_PCT = 0.01  # 成交额允许的相对误差 %


def ro(p: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f'file:{p.as_posix()}?mode=ro', uri=True, timeout=20)
    c.execute('PRAGMA busy_timeout=20000')
    return c


# ------------------------------------------------------------------ 回算

def recompute() -> dict:
    """
    三步之间有顺序依赖：涨跌停价 = pre_close × (1 ± 幅度)，所以必须先补 pre_close。

    第一版把 limit 放在最前面，结果 C1 刚从 vipdoc 摄入的 25.4 万行北交所数据
    全部拿不到涨跌停 —— 那些行当时还没有 pre_close，回算条件不成立，
    而且不会报错，只是静默地少更新 25 万行。

    ⚠️ 这里的 `recompute_limits()` **不带日期＝整库重刷**（640 万行，2026-10-07 实测约 25 分钟），
    而且每跑一次会建一张当日备份表 `operations.limit_price_backup_YYYYMMDD(_b/_c…)`。
    它是"迁移后一次性对账"的工步，**不要排进每日链**；日常单日回填走 c10/c12 那种带日期的调用。
    """
    out = {}
    for label, fn in (('pre_close/pct_change', quote_repo.recompute_pre_close),
                      ('limit_up/limit_down', quote_repo.recompute_limits),
                      ('turnover_rate_percent', quote_repo.recompute_turnover)):
        t0 = time.time()
        n = fn(role='admin')
        out[label] = n
        print('  %-22s %10s 行  %.1fs' % (label, f'{n:,}', time.time() - t0))
    return out


# ------------------------------------------------------------------ 对账

def check_rowcount() -> dict:
    """旧库每张表的行数要能在新库里找到归属，一行都不能不知去向。"""
    base = {(r['source_db'], r['source_table']): r['row_count']
            for r in engine.query(
                'SELECT source_db, source_table, row_count FROM operations.migration_baseline')}
    old_stock = sum(v or 0 for (db, t), v in base.items()
                    if t == 'stock_daily' and db.startswith('tq_daily_'))
    old_index = sum(v or 0 for (db, t), v in base.items()
                    if t == 'index_daily' and db.startswith('tq_daily_'))
    old_sector = sum(v or 0 for (db, t), v in base.items()
                     if t == 'sector_daily' and db.startswith('tq_daily_'))

    new_stock = engine.scalar('SELECT count(*) FROM market_data.stock_daily')
    new_index = engine.scalar('SELECT count(*) FROM market_data.index_daily')
    new_sector = engine.scalar('SELECT count(*) FROM market_data.sector_daily')

    def latest(rule_key: str) -> dict:
        r = engine.query(
            """
            SELECT actual FROM operations.quality_result
             WHERE rule_key = %s AND target = 'market_data.stock_daily'
             ORDER BY run_at DESC LIMIT 1
            """, (rule_key,))
        return (r[0]['actual'] or {}) if r else {}

    rejected = latest('vol_amount_consistent').get('rejected_rows', 0) or 0
    cls = latest('migration_classification')

    out = {
        'old_stock_daily': old_stock,
        'old_index_daily': old_index,
        'old_sector_daily': old_sector,
        'new_stock_daily': new_stock,
        'new_index_daily': new_index,
        'new_sector_daily': new_sector,
        'unit_rejected': rejected,
    }
    # 旧 stock_daily 一行的去向必须能穷举。B3 把分类计数写进了
    # operations.quality_result('migration_classification')，这里直接核对恒等式，
    # 而不是再花六分钟重扫 9,531,333 行。
    if cls:
        cats = ('stock', 'index', 'sector', 'bond', 'fund', 'bshare',
                'pseudo', 'unknown', 'bad_date', 'no_price')
        out['classified_sum'] = sum(cls.get(k, 0) or 0 for k in cats)
        out['classified_read'] = cls.get('read', 0) or 0
        out['classification_balanced'] = out['classified_sum'] == out['classified_read']
        # 进个股表的行 = 分类为个股的行 - 预检清掉的行
        out['stock_expected'] = (cls.get('stock', 0) or 0) - rejected
        out['stock_matched'] = new_stock == out['stock_expected']
    else:
        out['classification_balanced'] = None
        out['stock_matched'] = None
    # 迁移基线是**当时**旧库的快照数；板块日线随每日同步只增不减，
    # 所以判据是"新库至少包含迁移的行"（>=），不是相等。写成 == 会让
    # 每天正常同步后都误报一次 FAIL（本函数 docstring 就是这个意思）。
    out['sector_daily_matched'] = (new_sector >= old_sector)
    return out


def check_sample() -> dict:
    """随机抽样比对，防止字段错位、单位换算漏乘。

    ⚠️ **一个样本都没抽到不算通过。** 旧库 `D:\\finance\\data\\tq_daily_*.db`
    在本机已经不存在（用户 2026-09-25 确认该目录已彻底删除）。原来的写法对缺失的
    `.db` 直接 `continue`，于是 `checked=0`、`mismatch=0`，`main()` 只看 mismatch
    就报"对账通过" —— 那是**把"没比过"报成"比过且一致"**，比对不上还危险。
    现在把可用性与缺失清单如实交出去，由 `main()` 判成"无法验证"。
    """
    rng = random.Random(20260826)
    checked = mismatch_price = mismatch_amount = 0
    examples = []
    missing = []

    for y in YEARS:
        p = config.LEGACY_DATA / f'tq_daily_{y}.db'
        if not p.exists():
            missing.append(str(p))
            continue
        c = ro(p)
        try:
            codes = [r[0] for r in c.execute(
                "SELECT DISTINCT security_code FROM stock_daily "
                "WHERE security_code LIKE '6%.SH' OR security_code LIKE '00%.SZ' "
                "OR security_code LIKE '30%.SZ' LIMIT 3000")]
            if not codes:
                continue
            for _ in range(SAMPLE_PER_YEAR):
                ts = rng.choice(codes)
                row = c.execute(
                    'SELECT trade_date, close, vol, amount FROM stock_daily '
                    'WHERE security_code = ? ORDER BY random() LIMIT 1', (ts,)).fetchone()
                if not row:
                    continue
                td, close, vol, amount = row
                d = dt.date(int(td[:4]), int(td[4:6]), int(td[6:8]))
                new = engine.query_one(
                    'SELECT close, volume_shares, amount_yuan FROM market_data.stock_daily '
                    'WHERE security_code = %s AND trade_date = %s', (ts, d))
                if not new:
                    continue
                checked += 1
                if close is not None and abs(float(new['close']) - close) > PRICE_TOL:
                    mismatch_price += 1
                    examples.append({'security_code': ts, 'date': str(d), 'field': 'close',
                                     'old': close, 'new': float(new['close'])})
                # vol 单位换算：新库应是旧值 ×100
                if vol and new['volume_shares']:
                    ratio = new['volume_shares'] / (vol * 100)
                    if not 0.999 <= ratio <= 1.001:
                        mismatch_amount += 1
                        examples.append({'security_code': ts, 'date': str(d),
                                         'field': 'volume_shares', 'old_hand': vol,
                                         'new_shares': new['volume_shares'],
                                         'ratio': round(ratio, 6)})
                if amount and new['amount_yuan']:
                    diff = abs(float(new['amount_yuan']) - amount) / amount * 100
                    if diff > AMOUNT_TOL_PCT:
                        mismatch_amount += 1
                        examples.append({'security_code': ts, 'date': str(d),
                                         'field': 'amount_yuan', 'old': amount,
                                         'new': float(new['amount_yuan']),
                                         'diff_pct': round(diff, 4)})
        finally:
            c.close()
    present = len(YEARS) - len(missing)
    return {'checked': checked, 'mismatch_price': mismatch_price,
            'mismatch_vol_amount': mismatch_amount, 'examples': examples[:10],
            # 如实交出"这次到底比了没有"：0 个样本不是通过，是没验证
            'sources_present': present, 'sources_missing': missing,
            'available': checked > 0,
            'reason': (None if checked else
                       '无法抽样：旧库不存在（找过 %s%s）。抽样这一项**没做过**，'
                       '不能记成通过' % (config.LEGACY_DATA,
                                      '，缺失：%s' % '、'.join(missing) if missing else ''))}


def check_coverage() -> dict:
    """
    每个交易日的个股覆盖数。突降的那些日子要么真停牌多，要么迁漏了。

    走 app 角色而不是 reader：这几个聚合要全表扫 625 万行，reader 的
    statement_timeout 是 60 秒，扫不完就被掐掉，对账反而成了假失败。
    """
    rows = engine.query(
        """
        WITH d AS (
            SELECT trade_date, count(*) AS n FROM market_data.stock_daily GROUP BY trade_date),
        m AS (SELECT avg(n) AS avg_n, min(n) AS min_n, max(n) AS max_n FROM d)
        SELECT (SELECT count(*) FROM d)                                  AS days,
               round((SELECT avg_n FROM m))                              AS avg_per_day,
               (SELECT min_n FROM m)                                     AS min_per_day,
               (SELECT max_n FROM m)                                     AS max_per_day,
               (SELECT count(*) FROM d, m WHERE d.n < m.avg_n * 0.5)     AS thin_days
        """, role='app')
    out = dict(rows[0]) if rows else {}
    if out.get('thin_days'):
        out['thin_sample'] = [dict(r) for r in engine.query(
            """
            WITH d AS (SELECT trade_date, count(*) n FROM market_data.stock_daily
                        GROUP BY trade_date),
                 m AS (SELECT avg(n) a FROM d)
            SELECT trade_date, n FROM d, m WHERE n < a * 0.5
             ORDER BY n LIMIT 10
            """, role='app')]
    # 日线里的交易日是否都在交易日历里
    out['dates_not_in_calendar'] = engine.scalar(
        """
        SELECT count(*) FROM (SELECT DISTINCT trade_date FROM market_data.stock_daily) d
         WHERE NOT EXISTS (SELECT 1 FROM reference_data.trade_calendar c
                            WHERE c.calendar_date = d.trade_date AND c.is_open)
        """)
    return out


def check_fields() -> dict:
    """回算后该有值的字段有没有值，不该有的有没有乱填。"""
    q = engine.query_one(
        """
        SELECT count(*)                                        AS total,
               count(*) FILTER (WHERE limit_up IS NULL)         AS no_limit,
               count(*) FILTER (WHERE pre_close IS NULL)        AS no_pre_close,
               count(*) FILTER (WHERE turnover_rate_percent IS NULL) AS no_turnover,
               count(*) FILTER (WHERE adjustment_factor IS NULL)       AS no_adj,
               count(*) FILTER (WHERE volume_shares IS NULL)       AS no_vol,
               count(*) FILTER (WHERE source_batch_id IS NULL)     AS no_lineage
          FROM market_data.stock_daily
        """)
    return dict(q or {})


# ------------------------------------------------------------------ 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--check-only', action='store_true')
    ap.add_argument('--no-sample', action='store_true')
    args = ap.parse_args()

    n_daily = engine.scalar('SELECT count(*) FROM market_data.stock_daily')
    if not n_daily:
        print('market_data.stock_daily 为空，先跑 b3_daily.py')
        return 1

    if not args.check_only:
        print('=== 回算被置空的字段 ===')
        recompute()

    print('\n=== 对账 1 · 行数归属 ===')
    rc = check_rowcount()
    for k, v in rc.items():
        print('  %-22s %s' % (k, f'{v:,}' if isinstance(v, int) else v))

    print('\n=== 对账 2 · 字段完整度 ===')
    fields = check_fields()
    total = fields.get('total') or 1
    for k, v in fields.items():
        if k == 'total':
            continue
        print('  %-22s %10s  (%.2f%%)' % (k, f'{v:,}', v / total * 100))

    print('\n=== 对账 3 · 覆盖度 ===')
    cov = check_coverage()
    for k, v in cov.items():
        print('  %-22s %s' % (k, v))

    sample = {'skipped': True}
    if not args.no_sample:
        print('\n=== 对账 4 · 随机抽样比对 ===')
        sample = check_sample()
        for k, v in sample.items():
            if k != 'examples':
                print('  %-22s %s' % (k, v))
        for e in sample.get('examples', []):
            print('    差异 %s' % e)

    problems = []
    if rc['new_stock_daily'] == 0:
        problems.append('market_data.stock_daily 为空')
    if rc.get('classification_balanced') is False:
        problems.append('分类计数不平：%s vs read %s'
                        % (rc['classified_sum'], rc['classified_read']))
    if rc.get('stock_matched') is False:
        problems.append('个股表行数与分类计数不符：%s vs 预期 %s'
                        % (rc['new_stock_daily'], rc['stock_expected']))
    if not rc['sector_daily_matched']:
        problems.append('板块日线行数与基线不符：%s vs %s'
                        % (rc['new_sector_daily'], rc['old_sector_daily']))
    if cov.get('dates_not_in_calendar'):
        problems.append('有 %s 个交易日不在 reference_data.trade_calendar 里'
                        % cov['dates_not_in_calendar'])
    if fields.get('no_lineage'):
        problems.append('有 %s 行缺 source_batch_id 血缘' % fields['no_lineage'])
    if not sample.get('skipped') and not sample.get('available'):
        # 「没比过」必须成为一条问题，而不是让 mismatch=0 冒充一致
        problems.append('抽样对账未执行：%s' % sample.get('reason'))
    if sample.get('mismatch_price'):
        problems.append('抽样收盘价不一致 %s 处' % sample['mismatch_price'])
    if sample.get('mismatch_vol_amount'):
        problems.append('抽样量额不一致 %s 处' % sample['mismatch_vol_amount'])

    passed = not problems
    print('\n=== 结论 ===')
    if passed:
        print('  对账通过')
    else:
        for p in problems:
            print('  [FAIL] %s' % p)

    try:
        ops_repo.save_quality_result(
            None, 'migration_reconcile', 'market_data.stock_daily',
            'info' if passed else 'error', passed,
            actual={'rowcount': rc, 'fields': fields, 'coverage': cov,
                    'sample': sample},
            expected={'rule': '行数归属可解释、抽样一致、血缘完整、日期在日历内'})
    except Exception as exc:  # noqa: BLE001
        print('  [warn] 未能写入 operations.quality_result：%s' % exc)

    engine.close_all()
    return 0 if passed else 2


if __name__ == '__main__':
    raise SystemExit(main())
