"""qd.sentiment_cycle 成交额环比基期勘误：快照 → 重算 → 等价性自检 → 差异报告 → 写回。

**默认不写库**（不带 `--write` 就是 dry-run）。写回前先落一份可逆材料：

  · `.local/qd_sentiment_fix/snapshot_<ts>.jsonl` —— 受影响列的**当前值**逐行原样导出

回滚用 `--restore <那份文件>`：ft_etl 在任何 schema 都没有 CREATE 权限、建不了备份表，
而 reasons/deviation 是 jsonb（里面全是引号），手拼 SQL 字面量迟早把回滚脚本拼坏，
所以回滚走同一个脚本的参数化 UPDATE，一条都不靠转义。

还有一道**移植等价性闸**：除了勘误波及的 7 列，其余每一列都必须与库里现值完全一致；
只要有一列对不上，说明 YuanShan 侧这份移植走样了，整批拒绝写入 —— 否则一次勘误会
顺带把二十几个无关列悄悄改掉，那比原来的 bug 更糟。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from compute import db, qd_sentiment as S    # noqa: E402

AFFECTED = ['amount_change_pct', 'volume_state', 'stage_ui', 'stage_mcp',
            'confidence', 'reasons', 'deviation']
# compute() 返回的键 → 列名（其余同名）
KEY2COL = {'dev': 'deviation', 'sv': 'spec_version'}


def _cols(row: dict) -> dict:
    return {KEY2COL.get(k, k): v for k, v in row.items() if k != 'trade_date'}


def _canon(v):
    """jsonb 有两种到达形态：库里的 Python 对象、本次算出的 JSON 字符串。统一成后者。"""
    if isinstance(v, str) and v[:1] in '[{':
        try:
            v = json.loads(v)
        except ValueError:
            return v
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    return v


def _same(a, b) -> bool:
    a, b = _canon(a), _canon(b)
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)):
        return Decimal(str(a)) == Decimal(str(b))
    return str(a) == str(b)


def restore(path: str) -> int:
    """用快照把 7 列改回勘误前的值。参数化执行，不拼 SQL 字面量。"""
    sql = """UPDATE qd.sentiment_cycle
                SET amount_change_pct=%s, volume_state=%s, stage_ui=%s, stage_mcp=%s,
                    confidence=%s, reasons=%s::jsonb, deviation=%s::jsonb
              WHERE trade_date=%s AND spec_version=%s"""
    conn = db.connect(readonly=False)
    n = 0
    try:
        with open(path, encoding='utf-8') as fh:
            with conn.cursor() as c:
                for line in fh:
                    r = json.loads(line)
                    vals = []
                    for k in AFFECTED:
                        v = r.get(k)
                        # psycopg2 不会自动把 dict/list 变成 jsonb，得自己序列化
                        vals.append(json.dumps(v, ensure_ascii=False)
                                    if k in ('reasons', 'deviation') and v is not None else v)
                    c.execute(sql, vals + [dt.date.fromisoformat(r['trade_date']),
                                           r['spec_version']])
                    n += c.rowcount
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print('回滚失败，整批回退：%s' % exc)
        return 4
    finally:
        conn.close()
    print('已回滚 %d 行（读自 %s）' % (n, path))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true', help='真的写回 qd.sentiment_cycle')
    ap.add_argument('--restore', metavar='SNAPSHOT.jsonl',
                    help='用快照文件把这 7 列改回写回之前的值（然后退出）')
    ap.add_argument('--limit', type=int, default=0, help='只处理最近 N 场（调试用）')
    ap.add_argument('--out', default=os.path.join('.local', 'qd_sentiment_fix'))
    args = ap.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
        sys.stdout.reconfigure(encoding='utf-8')

    if args.restore:
        return restore(args.restore)

    sv = S.spec_version()
    cur = {r['trade_date']: r for r in db.query(
        'SELECT * FROM qd.sentiment_cycle WHERE spec_version = %s ORDER BY trade_date', (sv,))}
    dates = sorted(cur)
    if args.limit:
        dates = dates[-args.limit:]
    print('spec_version=%s  库里 %d 场，本次重算 %d 场' % (sv, len(cur), len(dates)))

    rows = S.recompute(dates)
    print('重算得到 %d 行（天梯未入库的日子本来就没有行）' % len(rows))
    missing = set(dates) - {r['trade_date'] for r in rows}
    if missing:
        print('!! 重算没有覆盖到 %d 场：%s' % (len(missing), sorted(str(x) for x in missing)[:5]))
        return 2

    changed = {c: 0 for c in AFFECTED}
    drift = {}
    samples = []
    for r in rows:
        d = r['trade_date']
        stored, new = cur[d], _cols(r)
        diff = {col for col in AFFECTED if not _same(stored.get(col), new.get(col))}
        for col in diff:
            changed[col] += 1
        if diff and len(samples) < 6:
            samples.append((str(d), {c: (stored.get(c), new.get(c)) for c in
                                     ('amount_change_pct', 'volume_state', 'stage_ui')
                                     if c in diff}))
        for col, val in new.items():
            if col in AFFECTED or col == 'calculated_at':
                continue
            if not _same(stored.get(col), val):
                drift.setdefault(col, []).append((str(d), stored.get(col), val))

    # ---- 移植等价性闸：无关列必须一列不差 ----
    if drift:
        print('\n移植走样：以下**不该变**的列与库里现值不一致，拒绝写入')
        for col, items in sorted(drift.items()):
            print('  %s  差 %d 行，样例：%s' % (col, len(items), items[:3]))
        return 3
    print('等价性自检通过：除受波及的 %d 列，其余 %d 列逐行与库里一致'
          % (len(AFFECTED), len(rows[0]) - len(AFFECTED)))

    print('\n受影响行数：' + '  '.join('%s=%d' % (k, v) for k, v in sorted(changed.items())))
    print('样例（库里现值 → 修正值）：')
    for d, vals in samples:
        print('  %s  %s' % (d, vals))

    ts = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    os.makedirs(args.out, exist_ok=True)
    snap_path = os.path.join(args.out, 'snapshot_%s.jsonl' % ts)
    with open(snap_path, 'w', encoding='utf-8') as fh:
        for d in dates:
            row = {k: cur[d].get(k) for k in AFFECTED}
            row['trade_date'] = str(d)
            row['spec_version'] = sv
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
    print('快照：%s\n回滚：python scripts/qd_fix_amount_change.py --restore %s'
          % (snap_path, snap_path))

    if not args.write:
        print('\ndry-run 结束（没写库）。确认差异后加 --write')
        return 0

    sql = """UPDATE qd.sentiment_cycle
                SET amount_change_pct=%(amount_change_pct)s,
                    volume_state=%(volume_state)s, stage_ui=%(stage_ui)s,
                    stage_mcp=%(stage_mcp)s, confidence=%(confidence)s,
                    reasons=%(reasons)s::jsonb, deviation=%(deviation)s::jsonb,
                    calculated_at=now()
              WHERE trade_date=%(trade_date)s AND spec_version=%(sv)s"""
    conn = db.connect(readonly=False)
    try:
        with conn.cursor() as c:
            for r in rows:
                p = _cols(r)
                c.execute(sql, {'trade_date': r['trade_date'], 'sv': r['sv'],
                                'amount_change_pct': p['amount_change_pct'],
                                'volume_state': p['volume_state'],
                                'stage_ui': p['stage_ui'], 'stage_mcp': p['stage_mcp'],
                                'confidence': p['confidence'],
                                'reasons': p['reasons'], 'deviation': p['deviation']})
        conn.commit()
        print('已写回 %d 行' % len(rows))
    except Exception as exc:
        conn.rollback()
        print('写入失败，整批回滚：%s' % exc)
        return 4
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
