# -*- coding: utf-8 -*-
"""
qd 轨 · 连板天梯族：qd.board_ladder / board_ladder_member / board_ladder_leader /
qd.board_height / qd.stock_strength。

算法契约：`数据加工工作指南/09-quantdash逻辑规格.md` §4.2。
零行 quantdash 源码（PolyForm Noncommercial，见 qd_common 模块头）。

这一族同时是 qd1_sentiment 的**唯一连板数据源**：情绪轨道不再从原始日线
另推一遍连板数，否则两轨迟早自相矛盾（quantdash 自己就有三套 stage 阈值）。
"""
from __future__ import annotations

import datetime as dt
import time

from app.compute import qd_common as Q
from app.db import engine

# ---------------------------------------------------------------- 成分股
SQL_MEMBER = """
INSERT INTO qd.board_ladder_member
       (trade_date, security_code, spec_version, source, deviation,
        board_tier, board_count, stock_name, industry_name, concept_name,
        limit_up_pct_regime, pct_change_actual, is_one_word, is_turnover_board,
        turnover_rate_pct, first_seal_time, seal_time_available,
        price_close, amount_yuan)
SELECT p.trade_date, p.security_code, %(sv)s,
       -- 本行只要落地了 quantdash 没有的本地输入（真实行业/换手率/分钟线封板时刻），
       -- 就必须标 +local，读的人才会知道这一列不是它的口径
       CASE WHEN p.industry_name IS NOT NULL OR p.turnover_rate_percent IS NOT NULL
              OR p.seal_time_available
            THEN 'quantdash-logic+local' ELSE 'quantdash-logic' END,
       -- 每个偏差是数组里的**一个元素**（jsonb_build_object 直接 || 会合并成对象，
       -- 那就只剩最后一条，等于把偏差藏起来了）
       jsonb_build_array(
         jsonb_build_object('code', 'L-4',
           'note', 'concept_name 留空：一只票挂 5~57 个概念板块，单值列承载不了，不做任意挑选'),
         jsonb_build_object('code', 'L-5',
           'note', 'industry_name 取通达信三级行业里成员数最少者；quantdash 用东财 hybk'),
         jsonb_build_object('code', 'L-6',
           'note', 'stock_name 是 reference_data.security 当前快照，不回溯历史名称'),
         jsonb_build_object('code', 'L-7',
           'note', '行业取自 sector_member 当前快照（该表无生效区间），历史行可能是今天的行业'))
       -- L-2 现在按**出处**点名，不按日期猜：`limit_basis='rule_st_snapshot'` 是
       -- `app/compute/limit_prices.py` 里"只有当前 is_st 快照支持 ±5 个点那一档、当日无证据"那一层。
       -- 以前这条写的是 `trade_date < st_snapshot_from`（用快照生效日反推哪几天可疑），
       -- 2026-10-07 档位改按当日判据重刷之后它已经不对了——当日有证据的行不再该报，
       -- 只有那一层猜出来的行该报。
       || CASE WHEN p.limit_basis = 'rule_st_snapshot'
               THEN jsonb_build_array(jsonb_build_object('code', 'L-2',
                     'note', '本行涨跌停按当前 is_st 快照猜的 5%% 档（当日没有证据）',
                     'st_valid_from', p.st_snapshot_from))
               ELSE '[]'::jsonb END,
       p.board_tier, p.board_count, p.stock_name, p.industry_name, NULL,
       round((p.ratio * 100)::numeric, 0)::smallint, p.pct_change,
       p.is_one_word,
       (NOT p.is_one_word
         AND p.turnover_rate_percent IS NOT NULL
         AND p.turnover_rate_percent >= %(turn)s),
       p.turnover_rate_percent, p.first_seal_time, p.seal_time_available,
       p.close, p.amount_yuan
  FROM qd_pool p
 WHERE p.trade_date = ANY (%(dates)s::date[])
ON CONFLICT (trade_date, security_code, spec_version) DO UPDATE SET
    source = EXCLUDED.source, deviation = EXCLUDED.deviation,
    board_tier = EXCLUDED.board_tier, board_count = EXCLUDED.board_count,
    stock_name = EXCLUDED.stock_name, industry_name = EXCLUDED.industry_name,
    limit_up_pct_regime = EXCLUDED.limit_up_pct_regime,
    pct_change_actual = EXCLUDED.pct_change_actual,
    is_one_word = EXCLUDED.is_one_word, is_turnover_board = EXCLUDED.is_turnover_board,
    turnover_rate_pct = EXCLUDED.turnover_rate_pct,
    first_seal_time = EXCLUDED.first_seal_time,
    seal_time_available = EXCLUDED.seal_time_available,
    price_close = EXCLUDED.price_close, amount_yuan = EXCLUDED.amount_yuan,
    calculated_at = now()
"""

# ---------------------------------------------------------------- 天梯层级
SQL_LADDER = """
-- 七个层级**每天都出行**，含 0。quantdash 值为 0 就缺行/置空列表，
-- 前端层级会跳号；而且它没有首板行（D-12）。
INSERT INTO qd.board_ladder
       (trade_date, board_tier, spec_version, source, deviation,
        tier_label, stock_count, is_highest_tier)
SELECT d.trade_date, t.tier, %(sv)s, 'quantdash-logic',
       jsonb_build_array(jsonb_build_object('code', 'D-12',
         'note', '补上 quantdash 缺失的首板行；它找不到分桶行时默认塞第 0 行')),
       CASE t.tier WHEN 7 THEN '七板以上' WHEN 6 THEN '六连板' WHEN 5 THEN '五连板'
                   WHEN 4 THEN '四连板' WHEN 3 THEN '三连板' WHEN 2 THEN '二连板'
                   ELSE '首板' END,
       coalesce(c.n, 0), (coalesce(c.n, 0) > 0 AND t.tier = h.mx)
  FROM (SELECT dd AS trade_date FROM unnest(%(dates)s::date[]) dd) d
 CROSS JOIN generate_series(1, %(top)s) AS t(tier)
  LEFT JOIN LATERAL (SELECT count(*) n FROM qd_pool p
                      WHERE p.trade_date = d.trade_date AND p.board_tier = t.tier) c ON true
  LEFT JOIN LATERAL (SELECT max(board_tier) mx FROM qd_pool p
                      WHERE p.trade_date = d.trade_date) h ON true
ON CONFLICT (trade_date, board_tier, spec_version) DO UPDATE SET
    source = EXCLUDED.source, deviation = EXCLUDED.deviation,
    tier_label = EXCLUDED.tier_label, stock_count = EXCLUDED.stock_count,
    is_highest_tier = EXCLUDED.is_highest_tier, calculated_at = now()
"""

# ---------------------------------------------------------------- 龙头
SQL_LEADER = """
WITH days AS (SELECT d AS trade_date FROM unnest(%(dates)s::date[]) d),
mx AS (   -- 当日最高板 + 三板及以上家数（stage 条件要直接用它）
    SELECT p.trade_date, max(p.board_count) AS hb,
           count(*) FILTER (WHERE p.board_count >= 3) AS three_plus
      FROM qd_pool p WHERE p.trade_date = ANY (%(dates)s::date[])
     GROUP BY 1
),
cand AS (
    -- **只留最高板那一档**：龙头候选、并列家数、同额家数都在这一档里数，
    -- 窗口分区如果建在全体涨停股上会把低板股算进并列数
    SELECT p.trade_date, p.security_code, p.stock_name, p.board_count,
           p.amount_yuan, p.close, p.is_one_word, p.first_seal_time,
           m.hb, m.three_plus,
           count(*) OVER (PARTITION BY p.trade_date)                AS leader_count,
           count(*) OVER (PARTITION BY p.trade_date, p.amount_yuan) AS same_amount,
           -- 三级 tie-break 一次排完（09 §4.2 裁定）。不搬 quantdash 那个未归一化
           -- 的封板时间字符串排序键（"142500" < "93000"），那是 D-17
           row_number() OVER (PARTITION BY p.trade_date ORDER BY
               (p.first_seal_time IS NULL)::int,
               p.first_seal_time NULLS LAST,
               p.amount_yuan DESC NULLS LAST,
               p.security_code ASC) AS rn
      FROM qd_pool p JOIN mx m USING (trade_date)
     WHERE p.board_count = m.hb
),
pick AS (
    SELECT d.trade_date, c.security_code, c.stock_name, c.board_count, c.close,
           c.is_one_word, c.hb, c.three_plus, c.same_amount, c.leader_count,
           c.first_seal_time, tc.next_open_date AS nd,
           (SELECT max(q.board_count) FROM qd_pool q
             WHERE q.trade_date = d.trade_date AND q.board_count < c.hb) AS second_highest,
           (SELECT count(*) FROM cand y
             JOIN qd_lbc n ON n.security_code = y.security_code
                          AND n.trade_date = tc.next_open_date
            WHERE y.trade_date = d.trade_date
              AND n.board_count > y.board_count)                         AS continued_count
      FROM days d
      LEFT JOIN (SELECT * FROM cand WHERE rn = 1) c USING (trade_date)
      LEFT JOIN reference_data.trade_calendar tc
             ON tc.calendar_date = d.trade_date AND tc.is_open
),
enr AS (
    SELECT pk.*,
           (SELECT n.open       FROM qd_day n
             WHERE n.trade_date = pk.nd AND n.security_code = pk.security_code) AS next_open,
           (SELECT n.close      FROM qd_day n
             WHERE n.trade_date = pk.nd AND n.security_code = pk.security_code) AS next_close,
           (SELECT l.board_count FROM qd_lbc l
             WHERE l.trade_date  = pk.nd AND l.security_code = pk.security_code) AS next_board
      FROM pick pk
),
calc AS (
    SELECT e.*,
           round(100.0 * (next_open  - close) / NULLIF(close, 0), 2) AS nxt_open_pct,
           round(100.0 * (next_close - close) / NULLIF(close, 0), 2) AS nxt_close_pct,
           (coalesce(next_board, 0) > hb)                            AS continued
      FROM enr e
)
INSERT INTO qd.board_ladder_leader
       (trade_date, spec_version, source, deviation,
        leader_board_count, leader_count, second_highest_board, three_plus_count,
        continued_count, leader_security_code, leader_stock_name, leader_tie_break,
        leader_is_one_word, leader_continued,
        leader_next_open_pct, leader_next_close_pct,
        leader_status_label, leader_status_code)
SELECT c.trade_date, %(sv)s, 'quantdash-logic',
       jsonb_build_array(jsonb_build_object('code', 'D-17',
             'note', '未复现 quantdash 未归一化的封板时间排序键'),
           jsonb_build_object('code', 'D-18',
             'note', '下一交易日显式查 trade_calendar，不静默前移到最近有数据日'),
           jsonb_build_object('code', 'D-16',
             'note', 'second_highest_board 无值时写 NULL 而不是 0')),
       c.hb, c.leader_count, c.second_highest, c.three_plus, c.continued_count,
       c.security_code, c.stock_name,
       CASE WHEN c.hb IS NULL                  THEN 'no_candidate'
            WHEN c.first_seal_time IS NOT NULL  THEN 'seal_time'
            WHEN c.same_amount > 1              THEN 'code_asc'
            ELSE 'amount_no_seal_time' END,
       c.is_one_word, c.continued, c.nxt_open_pct, c.nxt_close_pct,
       lbl.label, map.code
  FROM calc c
  LEFT JOIN LATERAL (
        -- 9 值标签，if-elif 顺序短路（09 §4.2，出处 fetch_sentiment_cycle_snapshots.py:346-368）
        SELECT CASE
          WHEN c.hb IS NULL                                THEN NULL
          WHEN c.nxt_close_pct IS NULL THEN
            CASE WHEN c.leader_count >= 2 AND c.hb >= 4  THEN '高标抱团'
                 WHEN c.is_one_word                      THEN '一字观察'
                 ELSE '待次日确认' END
          WHEN c.is_one_word AND c.continued             THEN '一字加速'
          WHEN c.continued     AND c.nxt_close_pct >= 0  THEN '强势晋级'
          WHEN c.nxt_close_pct >= 3                      THEN '断板承接'
          WHEN c.nxt_close_pct >= 0                      THEN '高位分歧'
          WHEN c.nxt_close_pct >  -5                     THEN '分歧转弱'
          ELSE '退潮承压' END AS label
  ) lbl ON true
  LEFT JOIN (VALUES ('高标抱团',     'high_leader_cluster'),
                    ('一字观察',     'one_word_watch'),
                    ('待次日确认',   'await_confirmation'),
                    ('一字加速',     'one_word_acceleration'),
                    ('强势晋级',     'strong_continuation'),
                    ('断板承接',     'accepted_after_break'),
                    ('高位分歧',     'high_level_divergence'),
                    ('分歧转弱',     'weakening'),
                    ('退潮承压',     'retreat_pressure')) AS map(label, code)
         ON map.label = lbl.label
ON CONFLICT (trade_date, spec_version) DO UPDATE SET
    source = EXCLUDED.source, deviation = EXCLUDED.deviation,
    leader_board_count = EXCLUDED.leader_board_count,
    leader_count = EXCLUDED.leader_count,
    second_highest_board = EXCLUDED.second_highest_board,
    three_plus_count = EXCLUDED.three_plus_count,
    continued_count = EXCLUDED.continued_count,
    leader_security_code = EXCLUDED.leader_security_code,
    leader_stock_name = EXCLUDED.leader_stock_name,
    leader_tie_break = EXCLUDED.leader_tie_break,
    leader_is_one_word = EXCLUDED.leader_is_one_word,
    leader_continued = EXCLUDED.leader_continued,
    leader_next_open_pct = EXCLUDED.leader_next_open_pct,
    leader_next_close_pct = EXCLUDED.leader_next_close_pct,
    leader_status_label = EXCLUDED.leader_status_label,
    leader_status_code = EXCLUDED.leader_status_code,
    calculated_at = now()
"""

# ---------------------------------------------------------------- 板高度分市场
SQL_HEIGHT = """
WITH g AS (
    SELECT p.trade_date,
           CASE p.board WHEN '主板' THEN 'main' WHEN '创业板' THEN 'chinext'
                        WHEN '科创板' THEN 'star' WHEN '北交所' THEN 'bj'
                        ELSE 'other' END AS mkt,
           p.board_count, p.security_code, p.stock_name
      FROM qd_pool p WHERE p.trade_date = ANY (%(dates)s::date[])
),
h AS (
    SELECT trade_date, mkt, max(board_count) AS hi FROM g GROUP BY 1, 2
),
h2 AS (   -- 主板第二名：严格小于最高板的那一档
    SELECT g.trade_date, g.mkt, max(g.board_count) AS hi
      FROM g JOIN h USING (trade_date, mkt)
     WHERE g.mkt = 'main' AND g.board_count < h.hi
     GROUP BY 1, 2
),
arr AS (
    SELECT g.trade_date, g.mkt, g.board_count,
           array_agg(g.stock_name    ORDER BY g.security_code) AS names,
           array_agg(g.security_code ORDER BY g.security_code) AS codes
      FROM g JOIN h ON h.trade_date = g.trade_date AND h.mkt = g.mkt
          AND h.hi = g.board_count
     GROUP BY 1, 2, 3
),
arr2 AS (
    SELECT g.trade_date, g.mkt, g.board_count,
           array_agg(g.stock_name    ORDER BY g.security_code) AS names,
           array_agg(g.security_code ORDER BY g.security_code) AS codes
      FROM g JOIN h2 ON h2.trade_date = g.trade_date AND h2.mkt = g.mkt
          AND h2.hi = g.board_count
     GROUP BY 1, 2, 3
),
piv AS (
    SELECT d.trade_date,
           coalesce(max(h.hi) FILTER (WHERE h.mkt = 'main'),    0) AS main_hi,
           coalesce(max(h.hi) FILTER (WHERE h.mkt = 'chinext'), 0) AS cx_hi,
           coalesce(max(h.hi) FILTER (WHERE h.mkt = 'star'),    0) AS star_hi,
           coalesce(max(h.hi) FILTER (WHERE h.mkt = 'bj'),      0) AS bj_hi,
           coalesce(max(h2.hi) FILTER (WHERE h2.mkt = 'main'),  0) AS main_2nd
      FROM (SELECT dd AS trade_date FROM unnest(%(dates)s::date[]) dd) d
      LEFT JOIN h  ON h.trade_date  = d.trade_date
      LEFT JOIN h2 ON h2.trade_date = d.trade_date
     GROUP BY 1
)
INSERT INTO qd.board_height
       (trade_date, spec_version, source, deviation,
        main_board_highest, main_board_highest_names, main_board_highest_codes,
        main_board_second_highest, main_board_second_highest_names,
        main_board_second_highest_codes,
        chinext_highest, chinext_highest_names, chinext_highest_codes,
        star_highest, star_highest_names, star_highest_codes,
        bj_highest, bj_highest_names, bj_highest_codes)
SELECT v.trade_date, %(sv)s, 'quantdash-logic',
       jsonb_build_array(jsonb_build_object('code', 'D-19',
         'note', 'quantdash 前缀白名单忽略科创板 688/689 与北交所 8xx/4xx，'
                 '本表用 star_highest/bj_highest 补上')),
       v.main_hi, coalesce(a1.names, ARRAY[]::text[]), coalesce(a1.codes, ARRAY[]::text[]),
       v.main_2nd, coalesce(b1.names, ARRAY[]::text[]), coalesce(b1.codes, ARRAY[]::text[]),
       v.cx_hi, coalesce(a2.names, ARRAY[]::text[]), coalesce(a2.codes, ARRAY[]::text[]),
       v.star_hi, coalesce(a3.names, ARRAY[]::text[]), coalesce(a3.codes, ARRAY[]::text[]),
       v.bj_hi, coalesce(a4.names, ARRAY[]::text[]), coalesce(a4.codes, ARRAY[]::text[])
  FROM piv v
  LEFT JOIN arr a1 ON a1.trade_date = v.trade_date AND a1.mkt = 'main'
                  AND a1.board_count = v.main_hi
  LEFT JOIN arr a2 ON a2.trade_date = v.trade_date AND a2.mkt = 'chinext'
                  AND a2.board_count = v.cx_hi
  LEFT JOIN arr a3 ON a3.trade_date = v.trade_date AND a3.mkt = 'star'
                  AND a3.board_count = v.star_hi
  LEFT JOIN arr a4 ON a4.trade_date = v.trade_date AND a4.mkt = 'bj'
                  AND a4.board_count = v.bj_hi
  LEFT JOIN arr2 b1 ON b1.trade_date = v.trade_date AND b1.mkt = 'main'
                   AND b1.board_count = v.main_2nd
                  AND v.main_2nd > 0
ON CONFLICT (trade_date, spec_version) DO UPDATE SET
    source = EXCLUDED.source, deviation = EXCLUDED.deviation,
    main_board_highest = EXCLUDED.main_board_highest,
    main_board_highest_names = EXCLUDED.main_board_highest_names,
    main_board_highest_codes = EXCLUDED.main_board_highest_codes,
    main_board_second_highest = EXCLUDED.main_board_second_highest,
    main_board_second_highest_names = EXCLUDED.main_board_second_highest_names,
    main_board_second_highest_codes = EXCLUDED.main_board_second_highest_codes,
    chinext_highest = EXCLUDED.chinext_highest,
    chinext_highest_names = EXCLUDED.chinext_highest_names,
    chinext_highest_codes = EXCLUDED.chinext_highest_codes,
    star_highest = EXCLUDED.star_highest,
    star_highest_names = EXCLUDED.star_highest_names,
    star_highest_codes = EXCLUDED.star_highest_codes,
    bj_highest = EXCLUDED.bj_highest,
    bj_highest_names = EXCLUDED.bj_highest_names,
    bj_highest_codes = EXCLUDED.bj_highest_codes,
    calculated_at = now()
"""

# ---------------------------------------------------------------- 个股强弱
SQL_STRENGTH = """
-- 阈值照搬 promptBuilders.ts:260-266：>=+3 强 / <=-2 弱 / 其余中性。
-- **pct_change 为 NULL 的票不出行**：quantdash 的 Number(x)||0 会把无数据判成
-- 中性（D-4），那是把"不知道"伪装成"知道"。
-- 关注池 = 当日封住涨停 + 触板未封，不是全市场 5500 只。
INSERT INTO qd.stock_strength
       (trade_date, security_code, pct_change, strength, pool_kind,
        spec_version, source, deviation)
SELECT u.trade_date, u.security_code, u.pct_change,
       CASE WHEN u.pct_change >= 3 THEN '强'
            WHEN u.pct_change <= -2 THEN '弱'
            ELSE '中性' END,
       u.pool_kind, %(sv)s, 'quantdash-logic',
       jsonb_build_array(jsonb_build_object('code', 'L-8',
         'note', 'quantdash 该分档喂 AI 前的候选集是它自己的涨停池+炸板池；'
                 '本表用本地日线推导的 封住涨停 + 触板未封，两个池的口径见 D-3'))
  FROM (SELECT p.trade_date, p.security_code, p.pct_change, 'sealed' AS pool_kind
          FROM qd_pool p WHERE p.trade_date = ANY (%(dates)s::date[])
         UNION ALL
        SELECT b.trade_date, b.security_code, b.pct_change, 'touched_not_sealed'
          FROM qd_broken b WHERE b.trade_date = ANY (%(dates)s::date[])) u
 WHERE u.pct_change IS NOT NULL
ON CONFLICT (trade_date, security_code) DO UPDATE SET
    pct_change = EXCLUDED.pct_change, strength = EXCLUDED.strength,
    pool_kind = EXCLUDED.pool_kind, source = EXCLUDED.source,
    deviation = EXCLUDED.deviation, calculated_at = now()
"""

STATEMENTS = (('member', SQL_MEMBER), ('ladder', SQL_LADDER),
              ('leader', SQL_LEADER), ('height', SQL_HEIGHT),
              ('strength', SQL_STRENGTH))


#: 连板回看是 90 个**自然日**，换算成场次取 70（一场约 60 个自然日 40 多个交易日，
#: 再加长假余量）。切片的片头要往前多带这么多场，否则片头那几天的"上一场"落在片外，
#: 连板数会凭空断成 1 板 —— 而且不报错。
POOL_BACK_SESSIONS = 70


def _refresh_chunk(pool_dates: list[dt.date], write_dates: list[dt.date],
                   *, dry_run: bool) -> dict:
    p = Q.params()
    args = {'dates': write_dates, 'sv': p['sv'], 'turn': p['turnover_pct'],
            'top': Q.TIER_TOP}
    out = {}
    with engine.conn('etl') as c:
        # dry_run 用真执行 + 强制回滚，而不是"跳过写库"：跳过的代码路径
        # 和真实路径不是一条，跑通了也不能证明真的能跑
        with c.transaction(force_rollback=dry_run):
            Q.build_pool(c.cursor(), pool_dates, p)
            for name, sql in STATEMENTS:
                out[name] = c.execute(sql, args).rowcount
    return out


def refresh(dates: list[dt.date], *, dry_run: bool = False) -> dict:
    """
    算天梯族五张表并落库，返回每张表的写入行数。

    失败时**一片一回滚**（`engine.conn` 的上下文管理器负责），所以不会出现
    "成员表写了一半、龙头表是旧的" —— quantdash 就是死在这种地方（D-3 把
    抓取的异常吞成空列表写进缓存，同日全部指标静默变 0）。

    超过一片就按 `POOL_CHUNK` 切片跑：2026-10-07 实测 1,253 场一把过 `SQL_LBC`
    会在 300 秒被 `statement_timeout` 掐掉。片头多带 70 场做回看（见上），
    但**只写本片的日期**。
    """
    chunks = Q.chunked(dates)
    if len(chunks) == 1:
        out = _refresh_chunk(dates, dates, dry_run=dry_run)
        out['days'] = len(dates)
        out['dry_run'] = dry_run
        return out

    out: dict = {}
    size = Q.POOL_CHUNK
    for k, seg in enumerate(chunks):
        i0 = k * size
        pool = dates[max(0, i0 - POOL_BACK_SESSIONS):i0] + seg
        t0 = time.time()
        r = _refresh_chunk(pool, seg, dry_run=dry_run)
        for name, n in r.items():
            out[name] = out.get(name, 0) + n
        print('  片 %s/%s  %s..%s  写 %s 行（池含回看 %s 场）  %.0fs'
              % (k + 1, len(chunks), seg[0], seg[-1],
                 f"{sum(v for kk, v in r.items() if kk != 'days'):,}", len(pool),
                 time.time() - t0), flush=True)
    out['days'] = len(dates)
    out['chunks'] = len(chunks)
    out['dry_run'] = dry_run
    return out
