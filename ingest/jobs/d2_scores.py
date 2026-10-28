# -*- coding: utf-8 -*-
"""
D2 · 算评分。

用法：
    python -m ingest.jobs.d2_scores --dry-run
    python -m ingest.jobs.d2_scores                    # 最新交易日
    python -m ingest.jobs.d2_scores --date 2026-08-25
    python -m ingest.jobs.d2_scores --recent 20
    python -m ingest.jobs.d2_scores --cap stock_quality
    python -m ingest.jobs.d2_scores --verify           # 与迁入的历史评分对照
    python -m ingest.jobs.d2_scores --since 2026-01-27 --skip-done --max-minutes 90 \
                                     --overwrite-legacy   # 回算一段历史（断点续跑）

**回算历史这一档为什么必须带 --overwrite-legacy。** 每一行写下去都同时带
`rank_in_market`/`percent_rank`（`app/compute/scoring.py` 那道闸：缺名次就整场不落库），
所以重跑一场就是给它补上名次 —— 而 `UPSERT_SCORE` 按主键**原地覆盖**，
`analytics.score` 又没有 `spec_version` 可以让新旧并存，覆盖掉的那批
`is_stale=true` 行正是 `--verify` 的对照基准。带这个参数会先把基准整批留档进
`analytics.score_legacy_baseline`（幂等，只补没进去的行）再写，并打一行留档计数。
**旧行不删**：2026-07 那几场里混着的 3,700+ 个指数码会留在表上带着 `is_stale=true`，
读侧两张名次榜都按 `rank_in_market is not null` 取数，自然把它们挡在榜外。

`--skip-done` 认的是"该场非 stale 的 oper_score 行 > 0 且每一条都带名次"，
**不认行数**（legacy 行一直留在表里，按行数判会把已经重跑过的场误判成没算）。
退出码沿用 `scripts/backfill_history.py` 那套：0 全算完 / 4 预算用完还有剩 /
3 有场次失败 / 1、2 停下来要人看（2 = 没带 --overwrite-legacy 却被要求覆盖基准）。

**--verify 一律不落库。** 迁入的 227,985 行历史评分（is_stale=true）就是
对照基准，而 UPSERT 会按 (security_code, trade_date, capability_key) 原地覆盖它并把
is_stale 置为 false —— 先写再比，基准就没了。所以对照跑 dry-run，
并把结论写进 operations.quality_result，这样等真正回算覆盖掉历史行之后，
证据还在库里。
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app.compute import scoring  # noqa: E402
from app.db import engine  # noqa: E402
from app.compute import target_guard as tguard  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402
from app.db.repo import security as sec_repo  # noqa: E402

LEGACY_CAPS = ('stock_quality', 'env_resonance', 'oper_score')
# 相关性低于此值就认为"新旧不是同一个东西"，需要给出解释而不是放过
CORR_WARN = 0.30


def legacy_window() -> tuple[dt.date | None, dt.date | None]:
    r = engine.query(
        'SELECT min(trade_date) AS mn, max(trade_date) AS mx '
        'FROM analytics.score WHERE is_stale')
    return (r[0]['mn'], r[0]['mx']) if r else (None, None)


# ---------------------------------------------------------------- 回算历史用的三件

BASELINE_TABLE = 'analytics.score_legacy_baseline'


def snapshot_legacy(*, create: bool = True) -> dict:
    """把迁入那批 `is_stale=true` 的历史评分留档到一张表里（幂等：只补还没进去的行）。

    为什么必须有这一步：`UPSERT_SCORE` 按 `(security_code, trade_date, capability_key)`
    **原地覆盖**，而那批行是 `--verify` 的对照基准（旧系统算的，量纲与分档规则和现行判据不同）。
    一旦回算覆盖，基准就没了 —— `analytics.score` 主键里又没有 `spec_version`，
    新旧不能并存对账。所以覆盖前先把基准搬一张表，证据（`operations.quality_result` 里的
    对照结论 + 这张原始行）就都留得下来。

    只复制、不删除：回算之后那些没被现行判据覆盖到的旧行（如 2026-07 那几场里混着的
    3,700+ 个指数码）仍留在 `analytics.score` 上并带着 `is_stale=true`，读侧按
    `rank_in_market is not null` 自然把它们挡在名次榜外。
    """
    if not engine.scalar(f"SELECT to_regclass('{BASELINE_TABLE}')"):
        if not create:
            return {'exists': False, 'rows': 0}
        with engine.conn('admin') as c, c.cursor() as cur:
            cur.execute(f'CREATE TABLE {BASELINE_TABLE} (LIKE analytics.score INCLUDING DEFAULTS)')
            cur.execute(f'CREATE INDEX ON {BASELINE_TABLE} (trade_date, capability_key)')
    n = engine.execute(f"""
        INSERT INTO {BASELINE_TABLE} (security_code, trade_date, capability_key, value,
                                      rank_in_market, percent_rank, quadrant, task_id,
                                      is_stale, calculated_at)
        SELECT s.security_code, s.trade_date, s.capability_key, s.value,
               s.rank_in_market, s.percent_rank, s.quadrant, s.task_id,
               s.is_stale, s.calculated_at
          FROM analytics.score s
         WHERE s.is_stale
           AND NOT EXISTS (SELECT 1 FROM {BASELINE_TABLE} b
                            WHERE b.security_code = s.security_code
                              AND b.trade_date = s.trade_date
                              AND b.capability_key = s.capability_key)
    """, role='admin')
    rows = engine.scalar(f'SELECT count(*) FROM {BASELINE_TABLE}')
    left = engine.scalar('SELECT count(*) FROM analytics.score WHERE is_stale')
    return {'exists': True, 'added': int(n or 0), 'rows': int(rows or 0), 'legacy_left': int(left or 0)}


def legacy_hit(dates: list[dt.date], window: tuple[dt.date | None, dt.date | None]) -> list[dt.date]:
    """本次要写的日期里，有哪些落在迁入历史评分（对照基准）的窗口内。

    抽出来是因为这条判断决定"要不要先留档才能写" —— 判错一次就会静默覆盖掉基准，
    而 `analytics.score` 主键没有 `spec_version`，覆盖了不能并存对账。
    """
    mn, mx = window
    if not mn or not mx:
        return []
    return [d for d in dates if mn <= d <= mx]


def ranked_dates(days: list[dt.date]) -> set[dt.date]:
    """`--skip-done` 眼里的"这一场已经回填过"。

    两条都要满足，缺一条就重跑那一场：
    ① **该场非 stale 的 `oper_score` 行 > 0，且这些行每一条都带 `rank_in_market`**；
    ② 该场在 `score_detail` 里**有分项行**。

    ② 是 2026-10-19 撞出来的：`compute_date` 分两笔写（先 `write_scores` 再 `write_details`），
    两笔之间库断了（实测：20:1x 连不上、新连接报 `the database system is in recovery mode`），
    于是 **2026-05-21 留下 5,500 行带名次的分数 + 0 行分项明细**。只认 ① 会把这一场判成"已做完"，
    再没人回去补它的明细 —— `/scores` 那一屏的分量解释从此读不到这一天，而且没有一处会报错。
    **一笔写不完，就必须让"没写完"可被检出**（做完不等于写了一半）。

    判据刻意**不看行数**（legacy 行会一直留在表里，按行数判会把已经重跑过的场误判成没算），
    也不看"有没有名次的行"（一场写坏了会有，必须整场重算）。
    """
    if not days:
        return set()
    rows = engine.query("""
        SELECT trade_date,
               count(*) FILTER (WHERE NOT is_stale)::int AS fresh,
               count(*) FILTER (WHERE NOT is_stale AND rank_in_market IS NULL)::int AS broken
          FROM analytics.score
         WHERE capability_key = 'oper_score' AND trade_date = ANY(%s)
         GROUP BY 1""", ([d for d in days],))
    with_detail = {r['trade_date'] for r in engine.query(
        'SELECT DISTINCT trade_date FROM analytics.score_detail WHERE trade_date = ANY(%s)',
        ([d for d in days],))}
    return {r['trade_date'] for r in rows
            if r['fresh'] and not r['broken'] and r['trade_date'] in with_detail}


def verify(sample: int) -> int:
    """
    在历史评分覆盖的日期里抽样，新旧同日同票做相关性对照。

    用 Spearman 秩相关而不是 Pearson：旧系统的量纲和分档规则跟新的不一样
    （比如涨停一刀切 1.1、位置用 60 日而非 250 日），数值不可能对得上，
    但**排序**应该大体一致 —— 复盘选票看的是排序。
    Pearson 一并算出来只作参考。
    """
    import pandas as pd

    mn, mx = legacy_window()
    if not mn:
        print('  analytics.score 里没有 is_stale=true 的历史评分，无法对照')
        return 1

    # 抽样前先按"这一天的迁入行够不够比"筛一遍。
    # 2026-10-19 实测：105 个 legacy 交易日里 96 天只有 4 只票（000001/000858/600000/600519，
    # 开发遗留），均匀抽 5 天会有 4 天在循环里被 `[SKIP] 可比对样本只有 4 只` 掉 ——
    # 白花四次 200 秒的重算，还让屏幕上留下一句"对照抽了 5 天"的假充实感。
    per_day = engine.query("""
        SELECT trade_date, count(*)::int AS codes FROM analytics.score
         WHERE is_stale AND capability_key = 'oper_score'
         GROUP BY 1 ORDER BY trade_date DESC""")
    if not per_day:
        print('  历史评分里没有 oper_score')
        return 1
    comparable = [r['trade_date'] for r in per_day if r['codes'] >= 30]
    thin = len(per_day) - len(comparable)
    print('  历史评分窗口 %s .. %s，共 %s 个交易日；其中 %s 天的迁入行不足 30 只'
          '（不参与对照，它们是整场只有 4 只票的开发遗留），可比对 %s 天'
          % (mn, mx, len(per_day), thin, len(comparable)))
    if not comparable:
        print('  没有任何一天的样本够做秩相关对照 —— 别把"没跑成"念成"新旧一致"')
        return 1
    step = max(1, len(comparable) // sample)
    picked = comparable[::step][:sample]
    print('  抽 %s 天对照：%s' % (len(picked), '、'.join(str(d) for d in picked)))

    ok = True
    summary: list[dict] = []
    for d in sorted(picked):
        st = scoring.compute_date(d, dry_run=True, collect=True)
        if 'error' in st:
            print('  %s  %s' % (d, st['error']))
            ok = False
            continue
        new = pd.DataFrame(st['rows'], columns=[
            'security_code', 'trade_date', 'capability_key', 'value', 'rank', 'pct', 'q', 'task'])
        old = pd.DataFrame(engine.query(
            'SELECT security_code, capability_key, value FROM analytics.score '
            'WHERE trade_date = %s AND is_stale AND capability_key = ANY(%s)',
            (d, list(LEGACY_CAPS))))
        if old.empty:
            print('  %s  历史评分缺这一天' % d)
            continue

        print('  %s  新算 %s 只 / %s 个功能点  %.1fs'
              % (d, st['codes'], len(st['caps']), st['seconds']))
        for cap in LEGACY_CAPS:
            a = new[new['capability_key'] == cap].set_index('security_code')['value']
            b = old[old['capability_key'] == cap].set_index('security_code')['value']
            common = a.index.intersection(b.index)
            if len(common) < 30:
                print('    [SKIP] %-14s 可比对样本只有 %s 只' % (cap, len(common)))
                continue
            x = a[common].astype(float)
            y = b[common].astype(float)
            # Spearman 走「先取秩、再算 Pearson」，不用 `corr(method='spearman')`：
            # 后者内部 import scipy.stats，而本机 venv 没有 scipy —— 2026-10-19 实测这一发
            # 就是 ModuleNotFoundError 当场死的，所以 `operations.quality_result` 里
            # 从来没有 `score_legacy_correlation` 这条记录（不是没人想对照，是对照工具在本机跑不起来）。
            # `.rank()` 对并列取平均秩，与 tie-corrected Spearman 同值，且不需要新依赖。
            sp = float(x.rank().corr(y.rank(), method='pearson'))
            pe = float(x.corr(y, method='pearson'))
            # 旧值全场同一个数（2026-10-19 实测 2026-07-27 那天的 env_resonance 旧值区间 0.0~0.0）
            # ⇒ 秩相关没定义，numpy 回 NaN。这不是"新旧不一致"，也不是"一致"，是**没法比**：
            # 既不能折成 0（0 是一个真实的读数），也不能让它进 jsonb（NaN 不是合法 JSON，
            # 会把整条 quality_result 落库打挂 —— 这次就是这么死的）。
            # 头部一致性：新旧各取前 10%，看重叠比例。相关性看整体，
            # 这个看真正会被推到页面上的那批票
            k = max(10, len(common) // 10)
            top_new = set(x.nlargest(k).index)
            top_old = set(y.nlargest(k).index)
            overlap = len(top_new & top_old) / k * 100
            comparable = sp == sp and pe == pe      # NaN != NaN
            flag = ('OK  ' if sp >= CORR_WARN else 'WARN') if comparable else 'N/A '
            if comparable and sp < CORR_WARN:
                ok = False
            print('    [%s] %-14s %s 只  Spearman %s  Pearson %s  '
                  '前10%%重叠 %.1f%%  新区间 %.1f~%.1f  旧区间 %.1f~%.1f%s'
                  % (flag, cap, f'{len(common):,}',
                     ('%+.3f' % sp) if comparable else '算不出',
                     ('%+.3f' % pe) if comparable else '算不出',
                     overlap, x.min(), x.max(), y.min(), y.max(),
                     '' if comparable else
                     '  ← 旧值全场相同（标准差 0），秩相关没定义；这不是通过也不是失败，是没数'))
            summary.append({'trade_date': str(d), 'capability_key': cap,
                            'n': len(common),
                            'spearman': round(sp, 4) if comparable else None,
                            'pearson': round(pe, 4) if comparable else None,
                            'top_decile_overlap_pct': round(overlap, 2),
                            'new_min': round(float(x.min()), 2),
                            'new_max': round(float(x.max()), 2),
                            'old_min': round(float(y.min()), 2),
                            'old_max': round(float(y.max()), 2)})

    if summary:
        by_cap = {}
        for s in summary:
            by_cap.setdefault(s['capability_key'], []).append(s['spearman'])
        print('\n  === 各功能点平均秩相关（算不出的那些不进平均）===')
        means = {}
        for cap, vals in sorted(by_cap.items()):
            got = [v for v in vals if v is not None]
            means[cap] = round(sum(got) / len(got), 4) if got else None
            print('    %-14s %s  （可比 %s / %s 天）'
                  % (cap, ('%+.3f' % means[cap]) if got else '算不出（旧值全场相同）',
                     len(got), len(vals)))
        ops_repo.save_quality_result(
            None, 'score_legacy_correlation', 'analytics.score',
            'warn' if not ok else 'info', ok,
            actual=means,
            expected={'spearman_min': CORR_WARN}, sample=summary)
        print('  已记入 operations.quality_result / score_legacy_correlation'
              '（passed=%s 说的是"可比对的那几项是否一致"，'
              '算不出的项在 actual 里是 null，不是 0）' % ok)
    return 0 if ok else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--recent', type=int, default=1)
    ap.add_argument('--cap', action='append', default=None)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--sample', type=int, default=5,
                    help='--verify 抽多少个交易日对照')
    ap.add_argument('--since', default='',
                    help='回算起点（含）。给了它就跑 起点..终点 一整段，配 --skip-done 断点续跑')
    ap.add_argument('--skip-done', action='store_true',
                    help='跳过"非 stale 行全都带名次"的交易日（续跑不重算已经回填好的场）')
    ap.add_argument('--max-minutes', type=float, default=None,
                    help='自限预算：用完就退 4，剩下没轮到的场次下一趟再来（scripts/backfill_history.py 传这个）')
    ap.add_argument('--overwrite-legacy', action='store_true',
                    help='允许覆盖迁入的历史评分基准；带这个参数会先把那批行留档进 %s' % BASELINE_TABLE)
    args = ap.parse_args()

    if args.verify:
        print('=== 新算评分 vs 迁入的历史评分 ===')
        rc = verify(args.sample)
        engine.close_all()
        return rc

    end = (dt.date.fromisoformat(args.date) if args.date
           else sec_repo.latest_trade_date())
    if not end:
        print('拿不到交易日')
        return 1
    # 目标日闸门：`--date D` 要的是 D。输入不齐就出声，不退回算别的日子。
    _no = tguard.missing_target_reason(args.date, 'market_data.stock_daily', 'trade_date',
                                      where='', min_rows=1000)
    if _no:
        print(_no)
        engine.close_all()
        return 1

    if args.since:
        # 回算模式：从起点到今天，按交易日一段一段来。这里取够大的根数，
        # 真跑多少由 --max-minutes 与 --skip-done 决定，不靠调用方数日子。
        since = dt.date.fromisoformat(args.since)
        dates = [d for d in sec_repo.recent_trade_dates(1200, end) if d >= since]
    else:
        dates = sec_repo.recent_trade_dates(args.recent, end)
    print('=== 算评分：%s 个交易日 %s .. %s ==='
          % (len(dates), dates[0], dates[-1]))

    mn, mx = legacy_window()
    hit = legacy_hit(dates, (mn, mx))
    if hit and not args.dry_run:
        if not args.overwrite_legacy:
            print('  ⛔ 本次要算的 %s 场落在迁入历史评分窗口 %s .. %s 内，'
                  '而 UPSERT 会原地覆盖那批 is_stale=true 的对照基准。'
                  % (len(hit), mn, mx))
            print('  确认要覆盖就带 --overwrite-legacy（它会先把基准整批留档进 %s 再写）；'
                  '只想先看差异就跑 --verify。' % BASELINE_TABLE)
            engine.close_all()
            return 2
        snap = snapshot_legacy()
        print('  基准留档：%s 本次新增 %s 行、表内共 %s 行（库里仍带 is_stale 的 %s 行不删，'
              '读侧按 rank_in_market is not null 自然把它们挡在名次榜外）'
              % (BASELINE_TABLE, f"{snap.get('added', 0):,}", f"{snap.get('rows', 0):,}",
                 f"{snap.get('legacy_left', 0):,}"))

    if args.skip_done:
        done = ranked_dates(dates)
        if done:
            print('  已回填 %s 场（非 stale 行整场带名次），这一遍只补剩下 %s 场'
                  % (len(done), len(dates) - len(done)))
        dates = [d for d in dates if d not in done]
        if not dates:
            print('  这一批都已经带名次的了，不需要重跑（要重来就去掉 --skip-done）')
            engine.close_all()
            return 0

    started = dt.datetime.now()
    written = failed = 0
    last_written = None

    for i, d in enumerate(dates, 1):
        if args.max_minutes and (dt.datetime.now() - started).total_seconds() / 60.0 >= args.max_minutes:
            print('  ⏸ 自限预算 %s 分钟用完：%s 场没轮到（从 %s 起）。'
                  '再跑同一条带 --skip-done 的命令就会接着这几场补，不重算已经写好的 %s 场。'
                  % (int(args.max_minutes), len(dates) - i + 1, d, written), flush=True)
            engine.close_all()
            return 4
        st = scoring.compute_date(d, cap_keys=args.cap, dry_run=args.dry_run)
        if 'error' in st:
            failed += 1
            print('  [%s/%s] %s  失败：%s —— 跳过这一场继续' % (i, len(dates), d, st['error']), flush=True)
            continue
        written += 1
        last_written = d
        print('  [%s/%s] %s  %s 只  回溯 %s 天  评分 %s 行 / 分项 %s 行  '
              '名次 %s 行（缺 %s）  象限空 %s  %.1fs'
              % (i, len(dates), d, f"{st['codes']:,}", st['lookback'],
                 f"{st['score_rows']:,}", f"{st['detail_rows']:,}",
                 f"{st.get('ranks_written', 0):,}", st.get('rank_missing', 0),
                 st.get('quadrant_null', 0), st['seconds']), flush=True)
        for cap, info in st['caps'].items():
            if isinstance(info, str):
                print('    [SKIP] %-14s %s' % (cap, info))
                continue
            print('    %-14s %6s 只  分项 %s'
                  % (cap, f"{info['scored']:,}", ','.join(info['parts'])))
            for k in info.get('empty_parts') or []:
                print('      [EMPTY] %s 一个值都没算出来，该分项的权重被静默摊到'
                      '其他分项上了' % k)
            for k, msg in (info.get('failed') or {}).items():
                print('      [FAIL] %s: %s' % (k, msg))

    if not args.dry_run and last_written:
        print('\n=== 验收（看的是最后写成的那一场 %s）===' % last_written)
        for r in engine.query(
                'SELECT capability_key, count(*) AS n, round(min(value),2) AS mn, '
                'round(max(value),2) AS mx, count(DISTINCT quadrant) AS nq, '
                'count(*) FILTER (WHERE rank_in_market IS NULL) AS no_rank '
                'FROM analytics.score WHERE trade_date = %s AND NOT is_stale '
                'GROUP BY 1 ORDER BY 1', (last_written,)):
            print('  %-14s %6s 只  区间 %s ~ %s  象限 %s 种  缺名次 %s 行'
                  % (r['capability_key'], f"{r['n']:,}", r['mn'], r['mx'], r['nq'],
                     f"{r['no_rank']:,}"))
        print('  analytics.score          %s' % engine.scalar(
            'SELECT count(*) FROM analytics.score'))
        print('  analytics.score_detail   %s' % engine.scalar(
            'SELECT count(*) FROM analytics.score_detail'))
        for r in engine.query(
                "SELECT quadrant, count(*) AS n FROM analytics.score "
                "WHERE trade_date = %s AND capability_key = 'oper_score' "
                "GROUP BY 1 ORDER BY 1", (last_written,)):
            print('    象限 %-4s %6s 只' % (r['quadrant'], f"{r['n']:,}"))
        # 名次覆盖度是这两张名次榜的"能看多久"，每天回算完都打一行，
        # 这样再出现"库里只有 47 场带名次"那种事，日志里当天就能看见，不用等到有人在界面上问。
        cov = engine.query_one("""
            SELECT count(*) FILTER (WHERE ranked)::int ranked_sessions,
                   count(*)::int all_sessions,
                   min(CASE WHEN ranked THEN d END)::text first_ranked,
                   max(CASE WHEN ranked THEN d END)::text last_ranked
              FROM (select trade_date d,
                           bool_and(rank_in_market is not null) ranked
                      from analytics.score
                     where capability_key='oper_score' and not is_stale
                     group by 1) x""")
        if cov:
            print('  名次覆盖：全场 %s 场，其中带名次 %s 场（%s .. %s）'
                  % (cov['all_sessions'], cov['ranked_sessions'],
                     cov['first_ranked'], cov['last_ranked']))
    engine.close_all()
    if failed:
        print('  本趟有 %s 场失败（下一趟 --skip-done 会再来一次）' % failed)
        return 3
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
