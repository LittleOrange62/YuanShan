# -*- coding: utf-8 -*-
"""
B7 · 板块主键归一 + 板块类型纠正 + 广度字段回填。

**问题**（实测确认，不是猜的）：
    md.sector_daily   653 个板块，全是 `TDX_880xxx`
    ref.sector_member 748 个板块，`GN_880xxx`(268) / `FG_880xxx`(154) /
                      `ZS_中证800` 这类(109) / `TDX_CSI_xxx`(217)
    两者 sector_code 交集 **0**。

    但把 6 位数字抽出来比对，`GN_` 268 个、`FG_` 154 个 **全部 100% 命中**
    md.sector_daily 里的 `TDX_<同一 6 位数字>`。也就是说这不是"两套板块体系"，
    而是**同一个板块被写了两个主键，只差前缀**。所以归一是机械可证的，
    不需要按名称做模糊匹配。

**第二个问题**：`ref.sector_info.sector_type` 的标注是错的。
    `ZS_中证800` / `ZS_上证380` / `ZS_创业小盘` 这 109 个被标成"行业"，
    它们其实是**指数成分股**，不是行业板块。而 `sector_map()` 恰好按
    `sector_type = '行业'` 取"主行业"，于是每只票的 `sector` 被填成
    `ZS_中证800` 这种指数代码 —— 它在 md.sector_daily 里不存在，
    `ctx.broadcast()` 于是静默返回全空，**板块热度四个分项全是空值，
    Y 轴静默退化成只剩大盘情绪，页面不报任何错**。

**第三个问题**：md.sector_daily 的 up_count / down_count / member_count
    在全部 717,875 行里都是 NULL，板块广度分项无论主键对不对都算不出来。

本脚本按顺序做四件事，每一步都可 `--dry-run` 预演：
    1. 归一：`GN_`/`FG_` 的成员关系迁到 `TDX_<数字>`，旧主键写入 ref.sector_alias
    2. 补名：`ref.sector_info` 里 `TDX_880xxx` 有 144 个 name 就是数字本身
       （毫无意义），用 `GN_`/`FG_` 那边的真名回填
    3. 纠型：`ZS_*` 从"行业"改成"指数"；被迁入的 `TDX_` 按来源改成"概念"/"风格"
    4. 回填：按成员关系 join md.stock_daily.pct_chg 算 up/down/member_count

**已知口径缺口，不隐瞒**：真正的通达信行业板块成分（880001-880499 那一段）
库里一条成员记录都没有 —— 从来没人采过。归一之后能用的是概念(268)与风格(154)
两类共 422 个板块。要补行业成分得另开一条通达信 block 文件解析，不在本脚本范围。

**成分是快照不是历史**：ref.sector_member 的 out_date 全为 NULL，
库里只有"当前成分"。回填 up/down_count 时对所有历史日期都用当前成分，
这会让很久以前的广度略有偏差。可接受，但不能假装它是真实历史成分。

用法：
    python scripts\\migrate\\b7_sector_key.py --dry-run          # 只出报告
    python scripts\\migrate\\b7_sector_key.py                    # 归一 + 纠型
    python scripts\\migrate\\b7_sector_key.py --backfill         # 再回填广度字段
    python scripts\\migrate\\b7_sector_key.py --backfill --since 2026-01-01
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402

TYPE_INDUSTRY = '行业'
TYPE_CONCEPT = '概念'
TYPE_STYLE = '风格'
TYPE_INDEX = '指数'
TYPE_CUSTOM = '自定义'

# 旧前缀 → 归一后应标的类型
PREFIX_TYPE = {'GN': TYPE_CONCEPT, 'FG': TYPE_STYLE}

# 能归一的判据：旧 code 以 6 位数字结尾，且 TDX_<该数字> 在 md.sector_daily 里有行
MATCH_CTE = """
WITH src AS (
    SELECT DISTINCT m.sector_code AS alias_code,
           split_part(m.sector_code, '_', 1) AS pre,
           substring(m.sector_code from '[0-9]{6}$') AS num
      FROM ref.sector_member m
),
        tgt AS (SELECT DISTINCT sector_code FROM md.sector_daily)
        SELECT s.alias_code, s.pre, s.num, 'TDX_' || s.num AS sector_code
          FROM src s
          JOIN tgt t ON t.sector_code = 'TDX_' || s.num
         WHERE s.num IS NOT NULL
           -- **必须排除"已经归一好"的行。** 少了这一条，第二次运行时
           -- `TDX_880501` 自己也满足"以 6 位数字结尾且在 sector_daily 里"，
           -- 于是 alias_code == sector_code，接着 INSERT 因主键冲突写入 0 行、
           -- DELETE 却把这批刚迁好的成员关系全删了 —— 实测第二次跑
           -- 直接删掉 66,933 条，并把 422 个 TDX_ 字典行置成不活跃。
           -- 脚本必须能重复跑而不自毁。
           AND s.alias_code <> 'TDX_' || s.num
        """



def report() -> list[dict]:
    print('=== 1. 归一可行性 ===')
    rows = engine.query(MATCH_CTE)
    by_pre: dict[str, int] = {}
    for r in rows:
        by_pre[r['pre']] = by_pre.get(r['pre'], 0) + 1

    total = engine.query(
        """
        SELECT split_part(sector_code, '_', 1) pre,
               count(DISTINCT sector_code) n,
               count(*) rows_
          FROM ref.sector_member GROUP BY 1 ORDER BY 1
        """)
    for t in total:
        m = by_pre.get(t['pre'], 0)
        verdict = ('全部可归一' if m == t['n']
                   else ('部分可归一' if m else '无日线可对应，保持原样'))
        print('  %-4s 板块 %4d 个 / 成员 %7s 条 → 命中 %4d 个（%s）'
              % (t['pre'], t['n'], f"{t['rows_']:,}", m, verdict))

    print('  md.sector_daily 板块数 %s，其中已有成员关系的 %s'
          % (engine.scalar('SELECT count(DISTINCT sector_code) FROM md.sector_daily'),
             engine.scalar('SELECT count(DISTINCT d.sector_code) FROM md.sector_daily d '
                           'JOIN ref.sector_member m USING (sector_code)')))
    return rows


def normalize(rows: list[dict]) -> None:
    """把 GN_/FG_ 的成员关系搬到 TDX_ 口径，旧主键留别名。"""
    print('\n=== 2. 归一成员关系 ===')
    if not rows:
        print('  没有可归一的板块')
        return

    # 双保险：MATCH_CTE 已经排掉了自指，这里再挡一次。
    # 自指对（alias == target）一旦漏进来，下面的 DELETE 会删掉刚迁好的数据。
    pairs = [(r['alias_code'], r['sector_code'],
              PREFIX_TYPE.get(r['pre'], r['pre'])) for r in rows
             if r['alias_code'] != r['sector_code']]
    if not pairs:
        print('  没有需要搬动的板块（可能已经归一过）')
        return

    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute('DROP TABLE IF EXISTS ref.stg_b7_alias')
        cur.execute('CREATE UNLOGGED TABLE ref.stg_b7_alias '
                    '(alias_code text, sector_code text, src text)')
    engine.copy_rows('ref.stg_b7_alias', ['alias_code', 'sector_code', 'src'],
                     iter(pairs), role='admin')

    n_alias = engine.execute(
        """
        INSERT INTO ref.sector_alias (alias_code, sector_code, src, note)
        SELECT alias_code, sector_code, src,
               'B7 归一：数字段相同，仅前缀不同'
          FROM ref.stg_b7_alias
        ON CONFLICT (alias_code) DO UPDATE SET
            sector_code = EXCLUDED.sector_code, src = EXCLUDED.src
        """, role='admin')
    print('  别名登记 %s 条' % f'{n_alias:,}')

    # 先插新行再删旧行。反过来会因为 sector_info 的 ON DELETE CASCADE
    # 在中途丢掉成员关系。ON CONFLICT DO NOTHING 处理"这只票已在 TDX_ 下"的情况
    n_ins = engine.execute(
        """
        INSERT INTO ref.sector_member (sector_code, ts_code, in_date, out_date)
        SELECT a.sector_code, m.ts_code, m.in_date, m.out_date
          FROM ref.sector_member m
          JOIN ref.stg_b7_alias a ON a.alias_code = m.sector_code
        ON CONFLICT (sector_code, ts_code) DO NOTHING
        """, role='admin')
    n_del = engine.execute(
        """
        DELETE FROM ref.sector_member m
         USING ref.stg_b7_alias a
         WHERE a.alias_code = m.sector_code
           AND a.alias_code <> a.sector_code
        """, role='admin')
    print('  成员迁入 %s 条，删除旧主键下 %s 条' % (f'{n_ins:,}', f'{n_del:,}'))

    # 补名：TDX_ 那边 name 就是数字本身的，用旧主键那边的真名替换
    n_name = engine.execute(
        """
        UPDATE ref.sector_info t
           SET name = o.name
          FROM ref.stg_b7_alias a
          JOIN ref.sector_info o ON o.sector_code = a.alias_code
         WHERE t.sector_code = a.sector_code
           AND t.name ~ '^[0-9]+$'
           AND o.name !~ '^[0-9]+$'
        """, role='admin')
    print('  回填板块名 %s 个（原来 name 就是数字）' % f'{n_name:,}')

    n_type = engine.execute(
        """
        UPDATE ref.sector_info t
           SET sector_type = a.src
          FROM ref.stg_b7_alias a
         WHERE t.sector_code = a.sector_code
           AND t.sector_type IS DISTINCT FROM a.src
        """, role='admin')
    print('  按来源改标类型 %s 个' % f'{n_type:,}')

    # 旧主键的字典行不删，留着可追溯，只置为不活跃
    n_off = engine.execute(
        """
        UPDATE ref.sector_info i SET is_active = false
          FROM ref.stg_b7_alias a
         WHERE i.sector_code = a.alias_code AND i.is_active
        """, role='admin')
    print('  旧主键字典行置为不活跃 %s 个（不删，保留可追溯）' % f'{n_off:,}')

    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute('DROP TABLE IF EXISTS ref.stg_b7_alias')


def fix_index_type() -> None:
    """ZS_* 是指数成分股，不是行业板块。标错会让 sector_map() 取到指数代码。"""
    print('\n=== 3. 纠正 ZS_ 的板块类型 ===')
    n = engine.execute(
        """
        UPDATE ref.sector_info
           SET sector_type = %s
         WHERE sector_code LIKE 'ZS\\_%%' AND sector_type <> %s
        """, (TYPE_INDEX, TYPE_INDEX), role='admin')
    print('  %s 个 ZS_ 板块从"%s"改为"%s"' % (f'{n:,}', TYPE_INDUSTRY, TYPE_INDEX))
    for r in engine.query(
            'SELECT sector_type, count(*) n FROM ref.sector_info '
            'WHERE is_active GROUP BY 1 ORDER BY 2 DESC'):
        print('    %-6s %s' % (r['sector_type'], r['n']))


def backfill(since: str | None) -> None:
    """按成员关系算板块广度。成分是当前快照，历史日期上会有偏差。"""
    print('\n=== 4. 回填 up_count / down_count / member_count ===')
    where = 'WHERE d.trade_date >= %s' if since else ''
    params = (since,) if since else None
    t0 = time.time()
    n = engine.execute(
        f"""
        WITH agg AS (
            SELECT d.sector_code, d.trade_date,
                   count(*) FILTER (WHERE s.pct_chg > 0)  AS up_count,
                   count(*) FILTER (WHERE s.pct_chg < 0)  AS down_count,
                   count(*)                               AS member_count
              FROM md.sector_daily d
              JOIN ref.sector_member m ON m.sector_code = d.sector_code
              JOIN md.stock_daily s
                    ON s.ts_code = m.ts_code AND s.trade_date = d.trade_date
            {where}
             GROUP BY d.sector_code, d.trade_date
        )
        UPDATE md.sector_daily t
           SET up_count = a.up_count, down_count = a.down_count,
               member_count = a.member_count, updated_at = now()
          FROM agg a
         WHERE t.sector_code = a.sector_code AND t.trade_date = a.trade_date
           AND (t.member_count IS DISTINCT FROM a.member_count
                OR t.up_count IS DISTINCT FROM a.up_count
                OR t.down_count IS DISTINCT FROM a.down_count)
        """, params, role='admin')
    print('  更新 %s 行  %.1fs' % (f'{n:,}', time.time() - t0))


def verify() -> None:
    print('\n=== 验收 ===')
    print('  sector_daily × sector_member 主键交集  %s 个板块'
          % engine.scalar('SELECT count(*) FROM (SELECT DISTINCT sector_code '
                          'FROM md.sector_daily INTERSECT SELECT DISTINCT sector_code '
                          'FROM ref.sector_member) x'))
    tot = engine.scalar('SELECT count(*) FROM md.sector_daily') or 0
    filled = engine.scalar('SELECT count(member_count) FROM md.sector_daily') or 0
    print('  member_count 非空                      %s / %s'
          % (f'{filled:,}', f'{tot:,}'))
    for r in engine.query(
            """
            SELECT trade_date, count(*) n, count(member_count) filled,
                   round(avg(member_count), 1) avg_member,
                   round(avg(up_count::numeric / nullif(up_count + down_count, 0)) * 100, 1) breadth
              FROM md.sector_daily
             GROUP BY 1 ORDER BY 1 DESC LIMIT 3
            """):
        print('  %s  板块 %4d 个 / 有成分 %4d 个 / 平均成分 %6s 只 / 平均上涨占比 %s%%'
              % (r['trade_date'], r['n'], r['filled'], r['avg_member'], r['breadth']))
    print('  ref.sector_alias                       %s 条'
          % engine.scalar('SELECT count(*) FROM ref.sector_alias'))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--backfill', action='store_true',
                    help='同时回填 up/down/member_count（较慢）')
    ap.add_argument('--since', help='只回填该日期起的板块日线，如 2026-01-01')
    args = ap.parse_args()

    rows = report()
    if args.dry_run:
        print('\n--dry-run，未改任何数据')
        engine.close_all()
        return 0

    batch_id = ops_repo.begin_batch('b7_sector_key', 'ref.sector_member')
    print('\n批次 %s' % batch_id)
    try:
        normalize(rows)
        fix_index_type()
        if args.backfill:
            backfill(args.since)
        ops_repo.end_batch(batch_id, status='ok', row_count=len(rows))
    except Exception as exc:
        ops_repo.end_batch(batch_id, status='failed', message=str(exc)[:2000])
        raise

    verify()
    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
