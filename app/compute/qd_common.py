# -*- coding: utf-8 -*-
"""
qd 轨公共层：参数、连板池推导、ST 生效区间。

**本文件零行 quantdash 源码。** 来源仓库 `D:\\finance\\quantdash-ai-stock-main`
是 PolyForm Noncommercial 1.0.0（不是 Apache-2.0），这里只有按
`D:\\finance\\数据加工工作指南\\09-quantdash逻辑规格.md` 重写的阈值与公式。
阈值改动一律升 `spec_version`，不许静默改口径。

与自家轨道（analytics.*）的关系是**双轨并存**：`analytics.market_breadth` 用单边
判据 `close >= limit_up - 0.01`（实测 2026-09-22 给 70），本轨用双边严格
`abs(close - limit_up) <= 0.005`（给 65）。两个数都对，是两个口径。

---- deviation 编码 ----
`D-*`  本行没有复现 quantdash 的该缺陷（42 处清单见 09 §7）
`L-*`  本轨与原口径的已知差异 / 本机缺输入的局限（不是缺陷，不许和 D-* 混用）
  L-1  volume_state：成交额环比 NULL 时归入"存量震荡"（照搬行为，显式标注）
  L-2  ST 档位无历史源：主板且当前 is_st 的票，早于其可证 ST 起点的行按当前快照判定
  L-3  晋级率族四列依赖 T+1 日线，最新交易日必为 NULL（quantdash 在那儿写常量，D-34）
  L-4  concept_name 恒 NULL：一只票挂 5~57 个概念板块，单值列承载不了
  L-5  industry_name 取通达信三级行业里成员数最少者（quantdash 用东财涨停池 hybk）
  L-6  stock_name 是 reference_data.security 的当前名称，不随历史日期回溯
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import ROUND_HALF_UP, Decimal

from app.db import engine

# ---- 口径常量（改这些必须同时升 spec_version）----
# 容差只有一份，住在 limit_prices（`--check` 哨兵就是按它判越界的）；这里引用而不是重抄
from app.compute.limit_prices import LIMIT_EPS               # 0.005 元，双边严格判据
POOL_LOOKBACK_DAYS = 90         # 连板回看自然日，与 YuanShan zt_pool() 一致
#: 一批最多多少场。`SQL_LBC` 的连板计数是"每个涨停行 × 段内行"的相关子查询，
#: 2026-10-07 实测 1,253 场一把跑会在 300 秒被 statement_timeout 掐掉；250 场一片
#: 约 60~90 秒，且每片打一行进度（判"卡住"至少看得见）。
POOL_CHUNK = 250
TIER_TOP = 7                    # 板级分桶上限（7 = "七板以上"）
SENT_B_MIN, SENT_B_MAX = 2, 14  # sentiment_value 的 prev 板数求和区间
SECTOR_WINDOW = 5               # 板块持续性回看的交易日数（quantdash 取末 5 天）
ONE_WORD_TOL = 0.001            # 一字板四价相等容差
A_KILL_PCT = -8.0               # 高板 A 杀
BIG_FACE_PCT = -5.0             # 大面（炸板且当日跌幅）起算
HIGH_BOARD = 4                  # "高板"起算
ST_PROOF_MOVE_PCT = 5.5         # 涨幅绝对值超此值 ⇒ 该日绝非 ST 五档
SH_INDEX = '000001.SH'          # 上证指数
SZ_INDEX = '399001.SZ'          # 深证成指
BOARD_SCOPE = 'all_boards'      # 涨跌家数统计范围：含北交所

SETTINGS_KEYS = ('qd_spec_version', 'qd_turnover_board_pct', 'qd_sector_top_n')


# =============================================================================
# 参数
# =============================================================================

def setting(key: str, as_type: type = str):
    """
    读 config.setting。**读不到就抛，不给默认值。**

    换手板阈值 5% 与板块 top 24 虽然是我方定的，但它们属于口径：代码里写一份、
    表里写另一份，两份不一致时不会有人发现。09 §6 把 config.setting 的写入权
    划给 code：系统配置，所以本模块只做**硬只读依赖**。
    """
    row = engine.query_one('SELECT value FROM config.setting WHERE key = %s',
                           (key,), role='reader')
    if not row or row['value'] is None:
        raise KeyError('config.setting 缺 key=%s（三个键由 app/db/sql/14_qd_settings.sql 落库）。'
                       '需要的三个键：%s' % (key, ' / '.join(SETTINGS_KEYS)))
    val = row['value']
    # psycopg 已经把 jsonb 解成 Python 值了：'"qd-1"'::jsonb → 字符串 qd-1、
    # '24'::jsonb → 整数 24。**不能再 json.loads 一次** —— 对字符串 qd-1 再解会抛
    # JSONDecodeError（只在真读库时暴露，测试注入参数走不到这条）。
    if isinstance(val, dict) and 'value' in val:
        val = val['value']
    if val is None:
        raise KeyError('config.setting key=%s 存在但取不到标量值：%r' % (key, row['value']))
    return as_type(val)


def spec_version() -> str:
    return setting('qd_spec_version', str)


def params() -> dict:
    """一次跑批要用的全部参数。开局读一遍：中途被改的话宁可这一批用旧值。"""
    return {'sv': spec_version(),
            'turnover_pct': setting('qd_turnover_board_pct', Decimal),
            'sector_top_n': setting('qd_sector_top_n', int)}


def dev(*items: dict) -> str:
    """deviation jsonb 的字面量。空列表而不是 NULL —— 09 §2 明确要求。"""
    return json.dumps(list(items), ensure_ascii=False)


def rnd(x, n: int):
    """
    按契约位数定值。None 原样传出去 —— 这就是 NULL ≠ 0 的落点。

    用 ROUND_HALF_UP 与 PostgreSQL `round(numeric, n)` 对齐：09 §2 要求舍入显式
    做在落库前而不是留给前端，那这条就是唯一该走的口子。
    """
    if x is None:
        return None
    return Decimal(str(x)).quantize(Decimal(1).scaleb(-n), rounding=ROUND_HALF_UP)


def universe_break(vals: list) -> str | None:
    """跨日名次可比性守卫：各日参与排名的板块数偏离窗口众数超两成 ⇒ 返回断点描述，否则 None。

    按**相对偏离**判而不是"总数完全相等"判：实测宇宙本来就有自然浮动（概念 266~268、
    行业 186~188 逐日不同），用相等会把 398/1169 天误判成断点、白白把跨日字段清空。
    阈值取窗口众数的 ±20%：真实断点 2026-09-23 行业 188→561（+198%）必中，
    自然浮动（±2 只）绝不中。

    两处用它：`qd_sector.compute_persistence`（连庄天数与前三次数）、
    `qd_sector_follow`（轨迹与错开档形状匹配）—— 同一件事不能有两把尺子。
    """
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return None
    mode = max(set(vals), key=vals.count)
    if mode <= 0:
        return None
    out = [v for v in vals if abs(v - mode) > 0.2 * mode]
    return '%s→%s（窗口众数 %s）' % (mode, sorted(set(out)), mode) if out else None


# =============================================================================
# 连板池：双边判据 + 连板数 + 一次性富化
# =============================================================================
# 全部落 TEMP TABLE，让后续五张表的 INSERT 都只是简单 SELECT：
# 判定逻辑（连板数怎么数）只写一处，避免天梯/龙头/板高度/强弱各推一遍后自相矛盾。

SQL_DAY = """
CREATE TEMP TABLE qd_day ON COMMIT DROP AS
SELECT z.security_code, z.trade_date,
       z.open, z.high, z.low, z.close, z.pre_close, z.pct_change,
       z.amount_yuan, z.turnover_rate_percent, z.limit_up, z.limit_down,
       z.limit_basis,
       s.board, s.name AS stock_name, coalesce(s.is_st, false) AS is_st_now,
       -- 档位一律取**当日**：库里那一行的 `limit_up/pre_close` 就是交易所当天的真实幅度
       -- （唯一写者 `app/compute/limit_prices.py`，出处写在 `limit_basis` 里）。
       -- 这里**不再**用 `board_limit_ratio(board, is_st)` 兜底：那个函数答的是
       -- "按 2026-08-27 的当前快照属于哪一档"，拿它补历史正是本轮修掉的病根；
       -- `pre_close` 缺位算不出比值时宁可给 NULL（`qd_day` 已经要求 limit_up 非空，
       -- 真为 NULL 的行本来就进不来）。
       round(z.limit_up / nullif(z.pre_close, 0) - 1, 4)                       AS ratio,
       (abs(z.close - z.limit_up) <= %(eps)s::numeric)                        AS is_zt,
       (z.limit_down IS NOT NULL
          AND abs(z.close - z.limit_down) <= %(eps)s::numeric)                AS is_dt,
       (z.high >= z.limit_up - %(eps)s::numeric)                              AS is_touched,
       (z.open IS NOT NULL AND z.close IS NOT NULL
          AND z.high IS NOT NULL AND z.low IS NOT NULL
          AND abs(z.open - z.close) < %(ow)s::numeric
          AND abs(z.open - z.high)  < %(ow)s::numeric
          AND abs(z.open - z.low)   < %(ow)s::numeric)                        AS is_one_word
  FROM market_data.stock_daily z
  JOIN reference_data.security s USING (security_code)
 WHERE s.asset_type = 'stock'
   AND z.trade_date BETWEEN %(warm)s AND %(thru)s
   AND z.close IS NOT NULL AND z.limit_up IS NOT NULL
"""

SQL_LBC = """
CREATE TEMP TABLE qd_lbc ON COMMIT DROP AS
WITH flags AS (
    -- **必须先在全集上算分组，再过滤 is_zt**。窗口函数在同一查询层级里是在
    -- WHERE 之后求值的：写成 `FROM qd_day WHERE is_zt` 再叠窗口，"非涨停日"
    -- 根本进不了窗口，grp 恒为 0，连板数退化成"回看窗内的涨停密度"
    -- —— 实测 2026-09-22 最高板被算成 19 板，真值 6 板。
    SELECT security_code, trade_date, is_zt,
           sum(CASE WHEN NOT is_zt THEN 1 ELSE 0 END) OVER
               (PARTITION BY security_code ORDER BY trade_date
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS grp
      FROM qd_day
),
g AS (
    SELECT security_code, trade_date, grp FROM flags WHERE is_zt
)
SELECT g.security_code, g.trade_date,
       -- 相关子查询而不是窗口函数：要带"90 自然日内"这个日期条件，
       -- 段内最多几十行，成本可以忽略，读起来比 RANGE 窗口清楚
       (SELECT count(*) FROM g x
         WHERE x.security_code = g.security_code AND x.grp = g.grp
           AND x.trade_date <= g.trade_date
           AND x.trade_date >  g.trade_date - %(look)s::int) AS board_count
  FROM g
"""

SQL_POOL = """
CREATE TEMP TABLE qd_pool ON COMMIT DROP AS
SELECT d.*, l.board_count,
       least(l.board_count, %(top)s) AS board_tier,
       ind.industry_name,
       seal.first_seal_time,
       (seal.first_seal_time IS NOT NULL) AS seal_time_available,
       st.valid_from AS st_snapshot_from
  FROM qd_lbc l
  JOIN qd_day d USING (security_code, trade_date)
  LEFT JOIN LATERAL (
        -- L-5：三级行业里成员数最少的节点 = 最细的那个
        SELECT i.name AS industry_name
          FROM reference_data.sector_member m
          JOIN reference_data.sector_info i USING (sector_code)
         WHERE m.security_code = d.security_code AND i.sector_type = '行业'
           AND m.sector_code LIKE 'TDX_881%%' AND m.out_date IS NULL
         GROUP BY i.name, m.sector_code
         ORDER BY (SELECT count(*) FROM reference_data.sector_member x
                    WHERE x.sector_code = m.sector_code AND x.out_date IS NULL) ASC,
                  m.sector_code ASC
         LIMIT 1
  ) ind ON true
  LEFT JOIN LATERAL (
        -- 封板时间：本机分钟线只有 2026-09-22 一天，其余日期这里必然是 NULL。
        -- quantdash 在同样情况下用哈希函数编造时分秒（D-28）—— 不跟。
        SELECT min(m.dt AT TIME ZONE 'Asia/Shanghai')::time AS first_seal_time
          FROM market_data.stock_minute m
         WHERE m.period = 1 AND m.security_code = d.security_code
           AND m.dt >= d.trade_date AND m.dt < d.trade_date + 1
           AND m.high >= d.limit_up - %(eps)s::numeric
  ) seal ON true
  LEFT JOIN LATERAL (
        SELECT sp.valid_from
          FROM reference_data.security_st_period sp
         WHERE sp.security_code = d.security_code
           AND sp.method = 'snapshot_current'
  ) st ON true
 WHERE d.is_zt
"""

SQL_BROKEN = """
CREATE TEMP TABLE qd_broken ON COMMIT DROP AS
SELECT d.security_code, d.trade_date, d.close, d.pre_close, d.pct_change,
       d.stock_name, d.board
  FROM qd_day d WHERE d.is_touched AND NOT d.is_zt
"""


def trade_days(end: dt.date | None = None, recent: int = 1) -> list[dt.date]:
    """最近 N 个**有日线数据**的交易日（升序）。判据不用交易日历，理由同 breadth。"""
    rows = engine.query(
        """
        SELECT DISTINCT trade_date FROM market_data.stock_daily
         WHERE (%(end)s::date IS NULL OR trade_date <= %(end)s::date)
         ORDER BY trade_date DESC LIMIT %(lim)s
        """, {'end': end, 'lim': max(1, int(recent))})
    return sorted(r['trade_date'] for r in rows)


def chunked(items: list, size: int = POOL_CHUNK) -> list:
    """按固定片长切块（**不是** `zip(iter,*n)` 那种分组陷阱惯犯 —— 见 05 号清单第 16 条）。
    跨片的天必须带上前一片末尾 `POOL_LOOKBACK_DAYS` 个自然日：连板数是往前数的，
    片头那几天的"上一场"如果在片外就会断成 1 板。调用方只写本片日期的行。"""
    return [items[i:i + size] for i in range(0, len(items), size)]


def build_pool(cur, dates: list[dt.date], p: dict) -> None:
    """在当前连接上重建连板池 TEMP 表。TEMP 是会话级的 —— 整个作业必须共用一个连接。

    ⚠️ **一批别超过 `POOL_CHUNK` 场**：2026-10-07 实测 `--from 2021-08-02` 一次喂 1,253 场，
    `SQL_LBC` 那条（每个涨停行都要在 `g` 上做一次相关计数求连板数）在 300 秒被
    `statement_timeout` 掐掉。调用方按片循环即可，每片自己带 90 天预热窗口，
    跨片不共享 TEMP —— 所以切片不影响连板计数（回看窗在片内是完整的）。
    """
    if not dates:
        raise ValueError('dates 为空')
    warm = dates[0] - dt.timedelta(days=POOL_LOOKBACK_DAYS)
    args = {'eps': LIMIT_EPS, 'ow': ONE_WORD_TOL, 'look': POOL_LOOKBACK_DAYS,
            'top': TIER_TOP, 'warm': warm, 'thru': dates[-1], 'sv': p['sv']}
    for name in ('qd_pool', 'qd_broken', 'qd_lbc', 'qd_day'):
        cur.execute('DROP TABLE IF EXISTS %s' % name)
    cur.execute(SQL_DAY, args)
    cur.execute('CREATE INDEX ON qd_day (trade_date, security_code)')
    cur.execute('CREATE INDEX ON qd_day (trade_date) WHERE is_zt')
    cur.execute(SQL_LBC, args)
    cur.execute(SQL_POOL, args)
    cur.execute('CREATE INDEX ON qd_pool (trade_date, board_count)')
    cur.execute(SQL_BROKEN, args)
    cur.execute('CREATE INDEX ON qd_broken (trade_date)')


# =============================================================================
# ST 生效区间：只装可证的部分
# =============================================================================
# 本机**没有 ST 历史真源**（2026-09-24 查尽，逐条见 12_qd_schema.sql 的表注释）。
# 所以本表只写两类可复算的行，缺的那块等 code：数据获取 接入名称变更源。
# 旧版这里写过"**在拿到源之前不动 market_data.stock_daily.limit_up**"，
# 2026-10-07 已被推翻并执行：不动手的原因（"无源重算等于用猜测覆盖现值"）只适用于
# 一刀切；现在档位是分层判据（当日快照反推 / 当日行情可证 / 档期 / 规则），
# **哪一层生效写在 `stock_daily.limit_basis` 里**，只有 `rule_st_snapshot` 那一层是猜的、
# 且读数侧与 `qd.board_ladder_member` 的 L-2 偏差标注都看得见它。

SQL_ST_PROOF = """
WITH mb AS (
    SELECT s.security_code
      FROM reference_data.security s
     WHERE s.asset_type = 'stock' AND s.board = '主板' AND coalesce(s.is_st, false)
),
moves AS (
    SELECT z.security_code, z.trade_date, z.pct_change, z.close, z.pre_close
      FROM market_data.stock_daily z JOIN mb USING (security_code)
     WHERE z.pct_change IS NOT NULL AND abs(z.pct_change) > %(proof)s
       AND z.trade_date >= %(since)s
),
anchor AS (   -- 最后一次可证"非 ST 五档"的那一天：它的次日才是可证的最早 ST 起点
    SELECT DISTINCT ON (security_code)
           security_code, trade_date, pct_change, close, pre_close
      FROM moves ORDER BY security_code, trade_date DESC
)
INSERT INTO reference_data.security_st_period
       (security_code, valid_from, valid_to, is_st, method, evidence)
SELECT security_code, trade_date, trade_date, false, 'non_st_proved_by_move',
       jsonb_build_object(
         'max_abs_pct', round(pct_change::numeric, 2),
         'close', close, 'pre_close', pre_close,
         'rule', 'abs(pct_change) > 5.5 ⇒ 该日绝非 5%% 档（可独立复算）',
         'implication', '该日之前 ST 状态未知，不是"已知非 ST"')
  FROM anchor
ON CONFLICT (security_code, valid_from, method) DO UPDATE SET
       valid_to = EXCLUDED.valid_to, is_st = EXCLUDED.is_st,
       evidence = EXCLUDED.evidence, calculated_at = now()
"""

SQL_ST_SNAPSHOT = """
INSERT INTO reference_data.security_st_period
       (security_code, valid_from, valid_to, is_st, method, evidence)
SELECT s.security_code, s.updated_at::date, NULL, true, 'snapshot_current',
       jsonb_build_object('snapshot_updated_at', s.updated_at::text,
                          'note', '当前 is_st 快照，只对其后日期有效，不向回套历史')
  FROM reference_data.security s
 WHERE s.asset_type = 'stock' AND coalesce(s.is_st, false)
ON CONFLICT (security_code, valid_from, method) DO UPDATE SET
       valid_to = EXCLUDED.valid_to, is_st = EXCLUDED.is_st,
       evidence = EXCLUDED.evidence, calculated_at = now()
"""


def refresh_st_period(since: dt.date = dt.date(2021, 12, 1)) -> dict:
    """回填 ST 表。返回各方法行数，供简报核对。"""
    with engine.conn('etl') as c:
        proved = c.execute(SQL_ST_PROOF, {'proof': ST_PROOF_MOVE_PCT,
                                          'since': since}).rowcount
        snap = c.execute(SQL_ST_SNAPSHOT).rowcount
        tot = c.execute('SELECT count(*) FROM reference_data.security_st_period'
                        ).fetchone()[0]
    return {'non_st_proved': proved, 'snapshot': snap, 'total': tot}
