# -*- coding: utf-8 -*-
"""
qd 轨 · 板块周期 qd.sector_rotation / qd.sector_persistence
（末尾一并带上 qd.sector_follow —— 前三甲轨迹与相似板块，见 qd_sector_follow 模块头）。

算法契约：`数据加工工作指南/09-quantdash逻辑规格.md` §4.3。
零行 quantdash 源码（PolyForm Noncommercial，见 qd_common 模块头）。

⚠️ 这一族**没有阶段枚举** —— quantdash 的"板块周期"实质是轮动矩阵 + 持续性统计，
阶段枚举属于它的情绪周期。所以本轨的 `analytics.ignition`（五档
ignite/follow/coil/fade/none）不是重复实现，是另一套东西，两轨都保留。

板块体系只取 **TDX_（通达信）** 一族：sector_daily 里 TDX_ 与 THS_ 两套体系并存
（概念 36 vs 232、行业 612 vs 86），同义板块会在同一个涨幅排行榜上互相挤位；
quantdash 取的是东财单一体系故无此问题 —— 记进 deviation L-15。
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from psycopg.rows import dict_row

from app.compute import qd_common as Q
from app.db import engine

MATRIX_KEEP_RANK = 8        # 轮动矩阵每日保留前 8（quantdash 口径）

# 整批共用一份偏差说明：这些都是"本表与原口径的差异"，逐行相同
ROTATION_DEV = json.dumps([
    {'code': 'D-20',
     'note': '统一按 top_n + (pct_change DESC, sector_name ASC) 排名；'
             'quantdash 的 MCP 版取 top 20 且无名称 tie-break，与它自己的 Python/TS 版不一致'},
    {'code': 'L-15',
     'note': '只取通达信 TDX_ 板块体系。TDX_ 与 THS_ 并存会让同义板块在同一个'
             '涨幅榜上互相挤位；quantdash 用东财单一体系故无此问题'},
    {'code': 'L-16',
     'note': '矩阵只落 day_rank<=8（quantdash 每日取前 8）；当日参与排名的板块'
             '总数记在 window_size 列'},
], ensure_ascii=False)

# 脏数据守卫。实测（2026-09-24）：sector_daily 里 |涨跌幅|>100 的 1994 行中 1963 行
# 落在 49 个"无名板块"上（sector_info.name 就等于代码数字本身，是 c17 解析通达信
# 板块指数留下的产物，最大一条 TDX_880046 在 2024-02-08 是 +429900%）。
# 让它们参与排名 = 每天领涨板块被解析垃圾霸榜。排除数按天如实落进 deviation，
# 而不是静默丢掉 —— 那正是 quantdash 的 D-36/D-41 犯过的错。
MAX_SECTOR_PCT = 30

#: 排名母体只此一份定义。`qd_sector_follow` 拼的是同一段（前三甲与相似榜的名次
#: 必须与这一族的 day_rank 是同一把尺，否则首页两块会自相矛盾 —— 第 1 名不是同一个板块）。
#: ⚠️ 这条必须是**普通字符串**，不能加 r 前缀：raw 串里的 `\\_` 到 SQL 是两个反斜杠，
#: `like 'TDX\\_%%'` 就变成"要求代码里真有两个反斜杠"，零行命中且不报错
#: （10-25 实测：接口照样 available:false，理由是"没有任何板块行"）。
MOTHER_POOL = """
    SELECT d.trade_date, d.sector_code, i.name AS sector_name, i.sector_type,
           d.pct_change, d.up_count, d.down_count, d.member_count,
           (i.name = split_part(d.sector_code, '_', 2)
            OR abs(d.pct_change) > %(cap)s) AS dirty
      FROM market_data.sector_daily d
      JOIN reference_data.sector_info i USING (sector_code)
     WHERE i.sector_type IN ('概念', '行业')
       AND d.sector_code LIKE 'TDX\\_%%'
       AND d.trade_date = ANY (%(dates)s::date[])
       AND d.pct_change IS NOT NULL
"""

#: 名次尺子（三级 tie-break）。名称与代码两级是写给 quantdash 的 D-20/L-21 补的：
#: 并列涨幅谁排前面会改变 day_rank<=3 的入选项；TDX 三级行业有同名同涨幅的不同板块，
#: 只按名字排仍然不确定。
MOTHER_RANK_ORDER = "pct_change DESC, sector_name ASC, sector_code ASC"

ROTATION_SQL = f"""
WITH pool AS ({MOTHER_POOL}),
size AS (   -- 当日"宇宙指纹"：分类型的干净样本数。跨日名次只有在宇宙一致时才可比
    SELECT trade_date,
           count(*) FILTER (WHERE sector_type = '概念') AS concept_candidates,
           count(*) FILTER (WHERE sector_type = '行业') AS industry_candidates,
           count(*)                                    AS candidates
      FROM pool WHERE NOT dirty GROUP BY 1
),
ranked AS (
    SELECT *,
           -- 名称 tie-break 必须有：并列涨幅谁排前面会改变 day_rank<=3 的入选项，
           -- 而 quantdash 的 MCP 版恰恰没有 tie-break（D-20），与它自己另外两版不一致。
           -- 末位再补 sector_code：TDX 三级行业有同名同涨幅的不同板块，
           -- 只按名字排会不确定（L-21）
           row_number() OVER (PARTITION BY trade_date
                              ORDER BY {MOTHER_RANK_ORDER}) AS day_rank
      FROM pool WHERE NOT dirty
),
excl AS (
    SELECT trade_date, count(*) FILTER (WHERE name_is_code) AS n_unnamed,
           count(*) FILTER (WHERE NOT name_is_code)         AS n_over
      FROM (SELECT trade_date,
                   (sector_name = split_part(sector_code, '_', 2)) AS name_is_code
              FROM pool WHERE dirty) q
     GROUP BY 1
)
INSERT INTO qd.sector_rotation
       (trade_date, sector_code, spec_version, source, deviation,
        sector_name, sector_type, day_rank, pct_change,
        up_count, down_count, member_count, window_size,
        concept_candidates, industry_candidates)
SELECT r.trade_date, r.sector_code, %(sv)s,
       -- 板块级涨跌家数/成分家数是 quantdash 没有的输入（它只有市场级涨跌家数）
       CASE WHEN r.up_count IS NOT NULL THEN 'quantdash-logic+local'
            ELSE 'quantdash-logic' END,
       %(dev)s::jsonb
       || CASE WHEN coalesce(e.n_unnamed, 0) + coalesce(e.n_over, 0) = 0
               THEN '[]'::jsonb
               ELSE jsonb_build_array(jsonb_build_object(
                     'code', 'L-18',
                     'note', '当日排名排除 '
                             || (coalesce(e.n_unnamed, 0) + coalesce(e.n_over, 0))::text
                             || ' 行：无名板块（sector_info.name 即代码，'
                                'c17 解析通达信板块指数的产物）+ 涨跌幅绝对值越界的行；'
                                '不排除会让解析垃圾霸占领涨板块',
                     'excluded_unnamed', coalesce(e.n_unnamed, 0),
                     'excluded_over_cap', coalesce(e.n_over, 0)))
            END,
       r.sector_name, r.sector_type, r.day_rank, r.pct_change,
       r.up_count, r.down_count, r.member_count,
       sz.candidates, sz.concept_candidates, sz.industry_candidates
  FROM ranked r
  JOIN size sz ON sz.trade_date = r.trade_date
  -- 这里不能用 USING (trade_date)：左边 r×sz 已经有两个 trade_date，
  -- PG 会报 "common column name appears more than once in left table"
  LEFT JOIN excl e ON e.trade_date = r.trade_date
 WHERE r.day_rank <= %(keep)s AND r.day_rank <= %(top)s
ON CONFLICT (trade_date, sector_code, spec_version) DO UPDATE SET
    source = EXCLUDED.source, deviation = EXCLUDED.deviation,
    sector_name = EXCLUDED.sector_name, sector_type = EXCLUDED.sector_type,
    day_rank = EXCLUDED.day_rank, pct_change = EXCLUDED.pct_change,
    up_count = EXCLUDED.up_count, down_count = EXCLUDED.down_count,
    member_count = EXCLUDED.member_count, window_size = EXCLUDED.window_size,
    concept_candidates = EXCLUDED.concept_candidates,
    industry_candidates = EXCLUDED.industry_candidates,
    calculated_at = now()
"""

WINDOW_SQL = """
-- 末 5 个交易日的窗口，back=1 即 T 日本身。
-- window_size 一起带回来是刻意的：它是当日参与排名的板块总数，
-- 跨日排名只在 window_size 相同的前提下才可比（见 compute_persistence 的 L-20 守卫）
WITH win AS (
    SELECT trade_date AS wd,
           row_number() OVER (ORDER BY trade_date DESC) AS back
      FROM (SELECT DISTINCT trade_date FROM qd.sector_rotation
             WHERE trade_date <= %(d)s AND spec_version = %(sv)s) a
     ORDER BY trade_date DESC
     LIMIT %(w)s
)
SELECT w.wd, w.back, r.sector_code, r.sector_name, r.day_rank, r.pct_change,
       r.window_size, r.concept_candidates, r.industry_candidates
  FROM win w
  JOIN qd.sector_rotation r ON r.trade_date = w.wd AND r.spec_version = %(sv)s
 ORDER BY w.back, r.day_rank
"""

PERSIST_SQL = """
INSERT INTO qd.sector_persistence
       (trade_date, spec_version, source, deviation,
        current_leader_name, current_leader_code, streak_days,
        top_three_appearances, strength_delta_pct,
        strongest_repeat_name, strongest_repeat_code, window_days, window_start)
VALUES (%(trade_date)s, %(sv)s, 'quantdash-logic', %(dev)s::jsonb,
        %(current_leader_name)s, %(current_leader_code)s, %(streak_days)s,
        %(top_three_appearances)s, %(strength_delta_pct)s,
        %(strongest_repeat_name)s, %(strongest_repeat_code)s,
        %(window_days)s, %(window_start)s)
ON CONFLICT (trade_date, spec_version) DO UPDATE SET
    source = EXCLUDED.source, deviation = EXCLUDED.deviation,
    current_leader_name = EXCLUDED.current_leader_name,
    current_leader_code = EXCLUDED.current_leader_code,
    streak_days = EXCLUDED.streak_days,
    top_three_appearances = EXCLUDED.top_three_appearances,
    strength_delta_pct = EXCLUDED.strength_delta_pct,
    strongest_repeat_name = EXCLUDED.strongest_repeat_name,
    strongest_repeat_code = EXCLUDED.strongest_repeat_code,
    window_days = EXCLUDED.window_days, window_start = EXCLUDED.window_start,
    calculated_at = now()
"""


def compute_persistence(d: dt.date, sv: str, rows: list[dict]) -> dict | None:
    """
    窗口内领涨持续性。rows = WINDOW_SQL 结果（含 back / day_rank / window_size）。

    两个守卫：

    1. **按 sector_code 认领领涨，不按板块名**。通达信 2026-09-23 起新补的 381 个
       三级行业里有同名不同码的板块（实测该日「房产服务」同时占第 4 与第 12 名），
       按名字回溯会把两个不同板块当成同一个在连庄。quantdash 用东财体系、名称唯一，
       所以它按名字没事 —— 记 L-21。
    2. **排名宇宙必须跨日一致**，否则 streak_days / top_three_appearances 是拿不同
       母体算出来的。全历史 1169 天里唯一的断点在 2026-09-23：sector_daily 的通达信
       行业数 228→609、window_size 448→829，新补的三级行业涨幅更高还把概念挤出了前 8。
       跨日字段一律留 NULL 并记 L-20 —— 只保留当日自洽的领涨板块与涨幅，
       宁可空着也不用一个不同宇宙的名次去数连庄天数。

    窗口为空则整行不写（quantdash 会在残缺样本上照算不误且不标记，D-41）。
    """
    if not rows:
        return None
    dev = []
    by_day: dict[int, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r['back'], []).append(r)
    n = len(by_day)
    leader = {b: min(v, key=lambda r: r['day_rank']) for b, v in by_day.items()}
    cur = leader[1]
    # 宇宙一致性守卫：判据只住 `qd_common.universe_break`（板块轨迹那一族用的是同一条），
    # 这里只负责把窗口内各日的分类型样本数交进去。
    def _broken(kind: str) -> str | None:
        return Q.universe_break([r[kind] for r in rows])

    broken = [f'{k}: {v}' for k, v in
              (('概念样本数', _broken('concept_candidates')),
               ('行业样本数', _broken('industry_candidates'))) if v]
    stable = not broken
    if not stable:
        dev.append({'code': 'L-20',
                    'note': '窗口内各日参与排名的板块宇宙与窗口众数偏离超两成（%s）→ '
                            '跨日名次不可比，streak_days / top_three_appearances / '
                            'strength_delta_pct / strongest_repeat 全部留 NULL。'
                            '实测断点：2026-09-23 起上游 c3/c17 把通达信行业从原始行 228 只补到 609 只、'
                            '参与排名的从约 180 只涨到 561 只（窗口众数已写在括号里），新补的细行业涨幅更高，'
                            '把概念整体挤出了前 8'
                            % '；'.join(broken)})
    streak = appear = strongest = delta = None
    if stable:
        streak = 1
        for b in range(2, n + 1):
            if leader[b]['sector_code'] != cur['sector_code']:
                break
            streak += 1
        # 前三席位按板块代码累计。当前 leader 一次都没进前三 → 0（quantdash 默认 1，D-21）
        seen: dict[str, dict] = {}
        for r in rows:
            if r['day_rank'] <= 3:
                v = seen.setdefault(r['sector_code'],
                                    {'name': r['sector_name'], 'code': r['sector_code'],
                                     'n': 0})
                v['n'] += 1
        appear = seen.get(cur['sector_code'], {'n': 0})['n']
        if appear == 0:
            dev.append({'code': 'D-21',
                        'note': '窗口内该领涨板块没有 day_rank<=3 的记录 → 写 0；'
                                'quantdash 缺数据时默认写 1（把无数据当"出现过一天"）'})
        strongest = (sorted(seen.values(), key=lambda v: (-v['n'], v['name']))[0]
                     if seen else None)
        if strongest is None:
            dev.append({'code': 'D-22',
                        'note': '窗口内无任何前三记录 → strongest_repeat 留 NULL；'
                                'quantdash 缺失时回落成当前 leader（编造"最强连庄"）'})
        if n >= 2 and leader[2]['sector_code'] == cur['sector_code']:
            delta = Q.rnd(Decimal(str(cur['pct_change']))
                          - Decimal(str(leader[2]['pct_change'])), 4)
        else:
            dev.append({'code': 'L-17',
                        'note': '前一日领涨板块与今日不是同一个板块（按代码判）→ '
                                'strength_delta_pct 留 NULL，前端应显示"新晋领涨"'
                                '（这条 quantdash 做对了，照搬）'})
    dev.append({'code': 'L-16',
                'note': '窗口应取末 %d 个交易日，实际可用 %d 天' % (Q.SECTOR_WINDOW, n)})
    return {
        'trade_date': d, 'sv': sv,
        'dev': json.dumps(dev, ensure_ascii=False),
        'current_leader_name': cur['sector_name'],
        'current_leader_code': cur['sector_code'],
        'streak_days': streak, 'top_three_appearances': appear,
        'strength_delta_pct': delta,
        'strongest_repeat_name': strongest['name'] if strongest else None,
        'strongest_repeat_code': strongest['code'] if strongest else None,
        'window_days': n, 'window_start': min(r['wd'] for r in rows),
    }


def refresh(dates: list[dt.date], progress=None) -> dict:
    sv = Q.spec_version()
    out = {'rotation_rows': 0, 'persistence': 0, 'skipped': []}
    with engine.conn('etl') as c:
        r = c.execute(ROTATION_SQL, {'dates': dates, 'sv': sv, 'dev': ROTATION_DEV,
                                     'keep': MATRIX_KEEP_RANK, 'cap': MAX_SECTOR_PCT,
                                     'top': Q.setting('qd_sector_top_n', int)})
        out['rotation_rows'] = r.rowcount
        # 连接上的默认 cursor 返回元组；compute_persistence 按列名取值，必须换 dict_row
        with c.cursor(row_factory=dict_row) as cur:
            for d in dates:
                rows = cur.execute(WINDOW_SQL, {'d': d, 'sv': sv,
                                                'w': Q.SECTOR_WINDOW}).fetchall()
                v = compute_persistence(d, sv, rows)
                if v is None:
                    out['skipped'].append(d)      # 宁可缺行，不可假行
                    continue
                c.execute(PERSIST_SQL, v)
                out['persistence'] += 1
    # 前三甲轨迹与相似板块（qd.sector_follow）接在这一步里一起跑：读的是同一张
    # sector_daily、用的是同一份母体常量，另开一步只会多一次对账（且步数被
    # test_volume_ratio 与 STAGES 钉着）。这里是**函数级 import**：本模块提供母体常量，
    # 顶层互相 import 会在半初始化时拿不到 MAX_SECTOR_PCT。
    from app.compute import qd_sector_follow
    out.update(qd_sector_follow.refresh(dates))
    out['days'] = len(dates)
    return out
