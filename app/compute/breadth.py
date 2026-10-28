# -*- coding: utf-8 -*-
"""
全市场情绪读数（`analytics.market_breadth`）—— 短线复盘真正看的那几个数。

**为什么要单独一张表**：这些是**市场层面**的标量（涨停家数、连板高度、晋级率、
炸板率…），不是个股属性。摊给每只票存一份（`market_mood` 那种做法）在评分体系里
说得通，但"明日验证表"要拿它们逐日比对，长表里取一个标量得先 DISTINCT 一次，
而且没法给"梯队分布"这种一天多列的读数留位置。

**它是「明日验证表」的数据源**（见 `app/compute/verification.py`）：昨天定下
"预期涨停家数上升"，今天拿这张表逐条打勾。所以这里的每个数都必须**可复算**，
口径写死在 SQL 里、不允许展示层再算一遍。

---- 口径（三条容易搞错的） ----

1. **涨停用 `limit_up` 价格判，不用"涨幅 > 9.8%"**。库里 `market_data.stock_daily.limit_up`
   是**当日档位下的涨停价**（numeric，不是布尔标记；唯一写入口 `app/compute/limit_prices.py`）。
   用涨幅阈值会把创业板 20%、北交所 30% 的涨停全漏掉，也会把 ST 的 5% 算错。
   容半分（`LIMIT_EPS = 0.005`，全项目一份）：2026-10-07 之前这里写 0.01，
   把"差一分钱没封住"也算成封住 —— 09-30 给 57，而通达信 `sh880006` 与严格双边都是 56。
2. **炸板是"盘中摸到涨停价、收盘没封住"**：`high >= limit_up - eps AND
   close < limit_up - eps`（eps 同上）。**不是近似** —— 有涨停价就能精确判，
   实测 08-28 涨停 87 家、炸板 28 家 → 炸板率 24.3%。
3. **`pct_change IS NULL` 的行全部排除**：新股首日、`pre_close` 缺失的行（08-28 有
   3 行）算不出涨跌幅，留着会把"上涨家数占比"的分母悄悄放大。
4. **涨跌成交额与涨跌家数同判据、同母体**（2026-10-21 加，用户 10-20 夜的「风险提示」）：
   `up_amount = sum(amount_yuan) FILTER (WHERE pct_change > 0)`、下跌那边 `< 0`，
   都在同一个 `today` CTE（已排除 `pct_change IS NULL`）上算，所以 `arms_ratio`
   的分子分母与 `up_count` / `down_count` 数的是同一批票。
   **算不出就是 NULL**（分母 0 或任一边缺），绝不写 0 —— 0 在这里是一个真实读数。
   周 / 月不落在库里：库里只留日频两个合计，周期比值由读侧 `duanxian/risk_signal.py`
   用期内合计重算（**不是把日比值取平均**）。
"""
from __future__ import annotations

import datetime as dt

from app.db import engine

# 涨停价与收盘价的容差（元）：**唯一来源是 `app/compute/limit_prices.py::LIMIT_EPS`**（半分）。
# 以前这里自己写 0.01，而 qd 轨写 0.005 —— 同一件事两个答案，实测 2026-09-30 涨停家数
# 这边给 57、通达信 sh880006 与严格双边都是 56：那 1 只是"差一分钱没封住"被算成封住。
# 现在 `--check` 哨兵保证收盘不会越过涨停价，所以单边 `close >= limit_up - eps`
# 与契约里的双边 `abs(close - limit_up) <= eps` 在这一列上等价（08 号契约 坑 1）。
from app.compute.limit_prices import LIMIT_EPS as EPS_YUAN    # noqa: E402
#                      ⚠️ SQL 里必须写成 `%(eps)s::numeric`：psycopg3 会把 Python float 绑成
#                      `double precision`，`numeric - float8` 被提升到浮点后
#                      `3.55 >= 3.56 - 0.01` 会算成 `3.55 >= 3.5500000000000003` 而判 False。
#                      2026-09-23 实测因此少算 2 只触板（600339.SH / 600818.SH，都是差一分钱）。
#                      同一个 SQL 换 psycopg2（参数按字面量发出、被当 numeric 解析）就是 94 而不是 92。

# 连板高度往前看几个交易日。20 个足够 —— A 股历史最高连板不到 20，
# 往前扫太多只是白扫分区。
BOARD_LOOKBACK = 20

# 每个读数的口径说明。⚠️ 这一摊**目前没有任何调用点**（写侧与 `ingest.jobs.d6_breadth` 都不读它，
# 屏幕上念的口径另有真源：首页「风险提示」读 `duanxian/risk_signal.py:DEFINITIONS`，
# 盘面数据那块的字段解释来自库里的列注释）。留着只当写侧的算法注记，别当成"页面从这里读"——
# 要上屏必须先接到某个读侧，接上就得同批改 `app/compute/breadth.py` 这一份与那一处的措辞。
EXPLAIN = {
    'basis': '全市场情绪的读数，全部由 `market_data.stock_daily` 当日截面 + '
             '最近 20 个交易日的涨停序列算出，不含任何个股观点。'
             '涨跌家数（宽度）与涨跌成交额（阿姆氏）用的是同一批票、同一个判据'
             '（`pct_change > 0` / `< 0`，都先排除 `pct_change IS NULL` 的行）。',
    'terms': (
        {'name': '涨停家数', 'key': 'limit_up_count',
         # 容差从代码里取，不手抄：手抄的 0.01 已经和真判据漂过一次（见 EPS_YUAN 那段）
         'expr': "count(close >= limit_up - %s)" % EPS_YUAN,
         'note': '情绪的赚钱意愿。用涨停价判而不是涨幅阈值，'
                 '否则创业板 20%、北交所 30%、ST 5% 全部判错'},
        {'name': '跌停家数', 'key': 'limit_down_count',
         'expr': "count(close <= limit_down + %s)" % EPS_YUAN,
         'note': '恐慌的直接读数。和涨停家数一起看才有意义'},
        {'name': '触板家数', 'key': 'touched_count',
         'expr': "count(high >= limit_up - %s)" % EPS_YUAN,
         'note': '炸板率的分母：盘中摸到涨停价的家数（含最终封住的）'},
        {'name': '炸板家数', 'key': 'broken_count',
         'expr': "count(high >= limit_up - %s AND close < limit_up - %s)" % (EPS_YUAN, EPS_YUAN),
         'note': '摸到涨停又没封住。这是「精确值」不是近似 —— 库里有涨停价'},
        {'name': '炸板率', 'key': 'broken_rate',
         'expr': '炸板家数 / 触板家数 × 100',
         'note': '情绪转弱最灵敏的一项。升 = 承接变差'},
        {'name': '最高连板高度', 'key': 'highest_board',
         'expr': '末尾连续涨停天数的最大值（往前看 20 个交易日）',
         'note': '情绪的天花板。高度被压缩通常意味着周期见顶'},
        {'name': '连板梯队', 'key': 'board2_count / board3_count / board4plus_count',
         'expr': '按连板天数分档计数（2 板 / 3 板 / 4 板及以上）',
         'note': '「断层比高度更重要」：只有一个高标、中间断层，说明接力资金没了'},
        {'name': '晋级率', 'key': 'promotion_rate',
         'expr': '昨日涨停且今日继续涨停 / 昨日涨停 × 100',
         'note': '接力意愿。低于 30% 通常是退潮'},
        {'name': '昨涨停红盘率', 'key': 'red_rate',
         'expr': '昨日涨停且今日收红 / 昨日涨停 × 100',
         'note': '比晋级率宽的一档判据：没封板但没砸，说明还有人接'},
        {'name': '中位涨幅', 'key': 'median_pct',
         'expr': 'percentile_cont(0.5) WITHIN GROUP (ORDER BY pct_change)',
         'note': '用中位数不用均值：几只妖股拉不动中位数'},
        {'name': '上涨家数占比', 'key': 'up_ratio',
         'expr': 'count(pct_change > 0) / count(*) × 100',
         'note': '赚钱效应的面。和中位涨幅同向才算真普涨'},
        # ↓ 阿姆氏（用户 2026-10-20 夜点名要上首页「风险提示」的两个指标之一）：
        #   判据必须与上面 `up_count` / `down_count` **一模一样**，只是把"数票"换成"加成交额"。
        {'name': '上涨成交额', 'key': 'up_amount',
         'expr': 'sum(amount_yuan) FILTER (WHERE pct_change > 0)',
         'note': '单位元。母体与 `up_count` 是同一批票（`pct_change IS NULL` 的行两边都排除）'},
        {'name': '下跌成交额', 'key': 'down_amount',
         'expr': 'sum(amount_yuan) FILTER (WHERE pct_change < 0)',
         'note': '单位元。母体与 `down_count` 是同一批票'},
        {'name': '阿姆氏指标', 'key': 'arms_ratio',
         'expr': 'up_amount / down_amount（分母为 0 或任一边缺 → NULL，不写 0、不写 Infinity）',
         'note': '「上涨的这些票子的成交额，除以下跌票子的成交额」—— 家数走强而这一项不涨，'
                 '就是量没跟上。库里存日频原值：周 / 月的比值由读侧用期内两个合计重算，'
                 '不是把日比值取平均'},
    ),
    'caveat': 'pct_change 为空的行（新股首日 / pre_close 缺失）全部排除，'
              '否则分母被悄悄放大；`stocks` 列如实记参与统计的票数。'
              '涨跌成交额同理：实测全库 `amount_yuan` 为 NULL / 0 的行 0 条（2026-10-21，'
              '6,446,917 行），所以"按判据筛出的家数"与"同一批票的成交额之和"是同一个母体。',
}

# 一天一行。参数只有 `%(d)s`（基准交易日）。
#
# 三处刻意的写法：
# 1. 涨停/跌停/触板/炸板**都在同一次扫描里用 FILTER 算完**，不是四个子查询 ——
#    同一天的截面扫一遍就够。
# 2. 连板序列单独一个 CTE 并**限定 20 个交易日**：不限的话会顺着分区一路扫到 2015 年。
# 3. 昨日涨停那两个比率用 `prev` CTE 取"上一个有数据的交易日"，
#    不用 `d - 1 day`：周末和长假会把分母算成 0。
CORE_SQL = """
        WITH prev AS (
            SELECT max(trade_date) AS d
              FROM market_data.stock_daily
             WHERE trade_date < %(d)s
        ),
        today AS (
            SELECT security_code, close, high, pre_close, pct_change, limit_up, limit_down,
                   amount_yuan
              FROM market_data.stock_daily
             WHERE trade_date = %(d)s
               AND pct_change IS NOT NULL
        ),
        cut AS (
            -- 连板要用的交易日下界：往前 BOARD_LOOKBACK 个**有数据的**交易日
            SELECT min(trade_date) AS d
              FROM (SELECT DISTINCT trade_date
                      FROM market_data.stock_daily
                     WHERE trade_date <= %(d)s
                     ORDER BY trade_date DESC
                     LIMIT %(look)s) z
        ),
        seq AS (
            SELECT security_code, trade_date,
                   (close >= limit_up - %(eps)s::numeric) AS is_lu,
                   row_number() OVER (PARTITION BY security_code
                                      ORDER BY trade_date DESC) AS rn
              FROM market_data.stock_daily
             WHERE trade_date <= %(d)s
               AND trade_date >= (SELECT d FROM cut)
               AND limit_up IS NOT NULL AND close IS NOT NULL
        ),
        streak AS (
            -- 连板天数 = 第一个"不涨停"的名次 − 1。全窗口都涨停的票没有
            -- "不涨停"那一行，用窗口内行数兜底（这种票现实里没有，但不兜就是 NULL）
            SELECT s.security_code,
                   coalesce(min(s.rn) FILTER (WHERE NOT s.is_lu) - 1,
                            max(s.rn)) AS boards
              FROM seq s
             GROUP BY 1
        ),
        prev_lu AS (
            SELECT p.security_code
              FROM market_data.stock_daily p
             WHERE p.trade_date = (SELECT d FROM prev)
               AND p.limit_up IS NOT NULL
               AND p.close >= p.limit_up - %(eps)s::numeric
        )
        SELECT
            (SELECT d FROM prev)                                  AS prev_date,
            count(*)                                              AS stocks,
            count(*) FILTER (WHERE t.close >= t.limit_up - %(eps)s::numeric)
                                                                  AS limit_up_count,
            count(*) FILTER (WHERE t.close <= t.limit_down + %(eps)s::numeric)
                                                                  AS limit_down_count,
            count(*) FILTER (WHERE t.high >= t.limit_up - %(eps)s::numeric) AS touched_count,
            count(*) FILTER (WHERE t.high >= t.limit_up - %(eps)s::numeric
                               AND t.close < t.limit_up - %(eps)s::numeric) AS broken_count,
            count(*) FILTER (WHERE t.pct_change > 0)                  AS up_count,
            count(*) FILTER (WHERE t.pct_change < 0)                  AS down_count,
            -- 阿姆氏的两个分量：**与上面两个 count 同一个 FILTER 判据**（同一批票），
            -- 只是把"数一行"换成"加这一行的成交额"。缺行不会补 0：
            -- 这一边一票都没有时 sum(...) FILTER 返回 NULL，比值在下游落成 NULL。
            sum(t.amount_yuan) FILTER (WHERE t.pct_change > 0)        AS up_amount,
            sum(t.amount_yuan) FILTER (WHERE t.pct_change < 0)        AS down_amount,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY t.pct_change) AS median_pct,
            (SELECT max(boards) FROM streak)                       AS highest_board,
            (SELECT count(*) FROM streak WHERE boards = 2)         AS board2_count,
            (SELECT count(*) FROM streak WHERE boards = 3)         AS board3_count,
            (SELECT count(*) FROM streak WHERE boards >= 4)        AS board4plus_count,
            (SELECT count(*) FROM prev_lu)                         AS prev_limit_up,
            (SELECT count(*) FROM prev_lu l JOIN today x ON x.security_code = l.security_code
                       WHERE x.close >= x.limit_up - %(eps)s::numeric)      AS promoted,
            (SELECT count(*) FROM prev_lu l JOIN today x ON x.security_code = l.security_code
                       WHERE x.pct_change > 0)                        AS stayed_red
          FROM today t
"""


def trade_days(end: dt.date | None = None, recent: int = 1) -> list[dt.date]:
    """
    最近 `recent` 个**有日线数据**的交易日（升序）。

    判据是 `market_data.stock_daily` 而不是交易日历：按日历取会把还没采集的日子带进来，
    算出来是一行 0，页面上分不清"当天真的没涨停"还是"数据没到"。
    """
    rows = engine.query(
        """
        SELECT DISTINCT trade_date FROM market_data.stock_daily
         WHERE (%(end)s::date IS NULL OR trade_date <= %(end)s::date)
         ORDER BY trade_date DESC
         LIMIT %(lim)s
        """, {'end': end, 'lim': max(1, int(recent))})
    return sorted(r['trade_date'] for r in rows)


def compute(trade_date: dt.date) -> dict | None:
    """算一天，返回原始读数（不落库）。给测试和 `--dry-run` 用。"""
    return engine.query_one(CORE_SQL, {'d': trade_date, 'eps': EPS_YUAN,
                                       'look': BOARD_LOOKBACK})


def refresh(dates: list[dt.date], *, task_id: int | None = None) -> int:
    """
    把每一天的读数算出来落库，返回写入行数。

    比率列（炸板率/晋级率/红盘率/上涨占比）在 SQL 里用 `NULLIF` 兜分母：
    没有票触板的那天炸板率是 **NULL 而不是 0** —— 0% 的意思是"都封住了"，
    和"今天没人摸涨停"是两回事，混成同一个值会让验证表判错。
    """
    rows = 0
    for d in dates:
        rows += engine.execute(
            """
            INSERT INTO analytics.market_breadth
                   (trade_date, stocks, limit_up_count, limit_down_count,
                    touched_count, broken_count, broken_rate,
                    highest_board, board2_count, board3_count, board4plus_count,
                    prev_limit_up, promotion_rate, red_rate,
                    up_count, down_count, up_ratio, median_pct,
                    up_amount, down_amount, arms_ratio, task_id)
            SELECT %(d)s, c.stocks, c.limit_up_count, c.limit_down_count,
                   c.touched_count, c.broken_count,
                   round(100.0 * c.broken_count
                         / NULLIF(c.touched_count, 0), 2),
                   c.highest_board, c.board2_count, c.board3_count,
                   c.board4plus_count, c.prev_limit_up,
                   round(100.0 * c.promoted / NULLIF(c.prev_limit_up, 0), 2),
                   round(100.0 * c.stayed_red / NULLIF(c.prev_limit_up, 0), 2),
                   c.up_count, c.down_count,
                   round(100.0 * c.up_count / NULLIF(c.stocks, 0), 2),
                   round(c.median_pct::numeric, 4),
                   c.up_amount, c.down_amount,
                   -- 阿姆斯＝上涨额 ÷ 下跌额。**NULLIF 兜分母**：下跌票一分钱没成交的那天
                   -- 是"判不出来"，不是"比值 0"——0 会被读成"下跌侧彻底没量＝极度强势"，
                   -- 而 NULL 在界面上是 `—`、折线在那里断开。任一边 NULL 时这一式也是 NULL。
                   round(c.up_amount / NULLIF(c.down_amount, 0), 6),
                   %(task)s
              FROM (""" + CORE_SQL + """) c
            ON CONFLICT (trade_date) DO UPDATE SET
                stocks           = EXCLUDED.stocks,
                limit_up_count   = EXCLUDED.limit_up_count,
                limit_down_count = EXCLUDED.limit_down_count,
                touched_count    = EXCLUDED.touched_count,
                broken_count     = EXCLUDED.broken_count,
                broken_rate      = EXCLUDED.broken_rate,
                highest_board    = EXCLUDED.highest_board,
                board2_count     = EXCLUDED.board2_count,
                board3_count     = EXCLUDED.board3_count,
                board4plus_count = EXCLUDED.board4plus_count,
                prev_limit_up    = EXCLUDED.prev_limit_up,
                promotion_rate   = EXCLUDED.promotion_rate,
                red_rate         = EXCLUDED.red_rate,
                up_count         = EXCLUDED.up_count,
                down_count       = EXCLUDED.down_count,
                up_ratio         = EXCLUDED.up_ratio,
                median_pct       = EXCLUDED.median_pct,
                up_amount        = EXCLUDED.up_amount,
                down_amount      = EXCLUDED.down_amount,
                arms_ratio       = EXCLUDED.arms_ratio,
                task_id          = EXCLUDED.task_id,
                calculated_at    = now()
            """, {'d': d, 'eps': EPS_YUAN, 'look': BOARD_LOOKBACK,
                  'task': task_id}, role='app')
    return rows


def run(dates: list[dt.date], *, task_id: int | None = None) -> dict:
    return {'rows': refresh(dates, task_id=task_id), 'days': len(dates)}


# ---------------------------------------------------------------- 全史回算（集合式一条 SQL）

#: 阿姆斯的常识区间（**故意宽**，只抓"量级错了"，不抓"差一点"）。
#: 依据是 2026-10-21 现查本库 1,262 场的全史分布：
#: min 0.0672（2025-04-07，涨 106 家 / 跌 5,250 家）、p05 0.307、p50 1.135、p95 4.512、
#: max 2039.48（2024-09-30，涨 5,291 家 / 跌 8 家 —— 真数不是脏数）。
#: 上界 5,000 是能容忍"哪天再来一次 9-30 再极端三倍"的最宽口子；越界必是 SQL 写错。
ARMS_SANE = (0.005, 5000.0)


def arms_agg_sql() -> str:
    """按 `trade_date` 聚合的涨跌成交额 —— 与 `CORE_SQL` 的 `today` CTE 同一判据。

    单独一个函数是为了**写侧与回算只有一份 SQL**：每日链和 `--backfill` 走同一个式子，
    两边各写一遍迟早漂（漂了的结果是"回填过的历史"和"每天新落的那行"不同源）。
    """
    return """
        SELECT trade_date,
               sum(amount_yuan) FILTER (WHERE pct_change > 0) AS up_amount,
               sum(amount_yuan) FILTER (WHERE pct_change < 0) AS down_amount
          FROM market_data.stock_daily
         WHERE pct_change IS NOT NULL
           AND trade_date BETWEEN %(frm)s AND %(to)s
         GROUP BY trade_date
    """


def backfill(frm: dt.date | None = None, to: dt.date | None = None) -> dict:
    """把 [frm, to] 每一场的涨跌成交额与阿姆斯**按当前口径重算并覆盖**（一条集合式 SQL）。

    为什么一条而不是逐日跑 `refresh()`：逐日那条要把连板序列、昨涨停名单全算一遍
    （约 1s/场 ⇒ 1,262 场要 20 分钟），而这三列只需要一次全表按日聚合（实测 ~7 秒）。
    写法照 `app/compute/volume_energy.py::rebuild()`：**驱动表是 `market_breadth` 自己**
    （LEFT JOIN 算出来的值），不是"只更新算到了的那些" —— 反过来写的话，
    某场在 `stock_daily` 里聚不出行（例如 2021-08-02 那种 `pct_change` 全空的第一场）
    会永远停在 NULL，而 `--status` 每轮都报"没追平"。

    `calculated_at` **不动**：这一列说的是"整行读数算于何时"，这里只补了其中三列，
    把它刷成 now() 等于谎称整行重算过。
    """
    started = dt.datetime.now()
    if to is None:
        to = engine.scalar('SELECT max(trade_date) FROM analytics.market_breadth')
    if frm is None:
        frm = engine.scalar('SELECT min(trade_date) FROM analytics.market_breadth')
    if frm is None or to is None:
        return {'ok': False, 'reason': 'analytics.market_breadth 是空的，没东西可算'}
    frm, to = _asdate(frm), _asdate(to)
    want = engine.scalar(
        'SELECT count(*) FROM analytics.market_breadth WHERE trade_date BETWEEN %s AND %s',
        (frm, to)) or 0
    written = engine.execute(
        """WITH agg AS (""" + arms_agg_sql() + """)
        UPDATE analytics.market_breadth b
           SET up_amount   = s.up_amount,
               down_amount = s.down_amount,
               arms_ratio  = round(s.up_amount / NULLIF(s.down_amount, 0), 6)
          FROM (SELECT b2.trade_date, a.up_amount, a.down_amount
                  FROM analytics.market_breadth b2
                  LEFT JOIN agg a ON a.trade_date = b2.trade_date
                 WHERE b2.trade_date BETWEEN %(frm)s AND %(to)s) s
         WHERE b.trade_date = s.trade_date""",
        {'frm': frm, 'to': to}, role='app')
    return {'ok': True, 'from': str(frm), 'to': str(to),
            'sessions_target': int(want), 'rows_written': int(written or 0),
            'seconds': round((dt.datetime.now() - started).total_seconds(), 1)}


def _asdate(v):
    return v.date() if hasattr(v, 'date') else (
        dt.date.fromisoformat(str(v)[:10]) if not isinstance(v, dt.date) else v)


def status(with_totals: bool = True) -> dict:
    """库里这一族的实有情况 —— CLI、对账、单测都读这一份，别各写一套判据。

    `with_totals=False` 跳过那条要扫全表成交额的恒等式核对（实测 6,446,917 行、约 7 秒）：
    只要场数与分布的快路径用这一档，CLI 与对账默认付这 7 秒。
    """
    r = engine.query_one(
        """SELECT count(*)::int AS sessions,
                  count(*) FILTER (WHERE up_amount IS NOT NULL)::int   AS has_up_amount,
                  count(*) FILTER (WHERE down_amount IS NOT NULL)::int AS has_down_amount,
                  count(*) FILTER (WHERE arms_ratio IS NOT NULL)::int  AS has_arms,
                  count(*) FILTER (WHERE arms_ratio IS NULL)::int      AS arms_null,
                  min(trade_date) AS lo, max(trade_date) AS hi,
                  min(arms_ratio)::numeric AS arms_min,
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY arms_ratio)::numeric AS arms_p50,
                  max(arms_ratio)::numeric AS arms_max
             FROM analytics.market_breadth""") or {}
    # 恒等式核对：涨额 + 跌额 ≤ 当日全市场成交额合计（同一张 stock_daily，含 pct 为空的行）。
    # 这条抓的是"FILTER 判据写错成另一个母体"（比如把全天的额都加进分子）。
    # ⚠️ 写成 `LEFT JOIN (按 trade_date 聚合)` 而不是逐行相关子查询：后者要给外层每一场
    #    重扫一次日线，2026-10-21 实测直接被 ft_reader 的 statement_timeout 掐掉
    #    （`QueryCanceled: canceling statement due to statement timeout`）。
    n_bad = None
    if with_totals:
        bad = engine.query_one(
            """WITH tot AS (
                   SELECT trade_date, sum(amount_yuan) AS total_amount
                     FROM market_data.stock_daily GROUP BY 1
               )
               SELECT count(*)::int AS n
                 FROM analytics.market_breadth b
                 LEFT JOIN tot t ON t.trade_date = b.trade_date
                WHERE coalesce(b.up_amount, 0) + coalesce(b.down_amount, 0)
                      > coalesce(t.total_amount, 0)""") or {}
        n_bad = bad.get('n')
    return {'sessions': r.get('sessions'),
            'has_up_amount': r.get('has_up_amount'),
            'has_down_amount': r.get('has_down_amount'),
            'has_arms': r.get('has_arms'),
            'arms_null': r.get('arms_null'),
            'from': str(r['lo']) if r.get('lo') else None,
            'to': str(r['hi']) if r.get('hi') else None,
            'arms_min': float(r['arms_min']) if r.get('arms_min') is not None else None,
            'arms_p50': float(r['arms_p50']) if r.get('arms_p50') is not None else None,
            'arms_max': float(r['arms_max']) if r.get('arms_max') is not None else None,
            'arms_sane': list(ARMS_SANE),
            'up_plus_down_gt_total': n_bad}


def latest(n: int = 2) -> list[dict]:
    """最近 n 天的读数（升序）。验证表拿它做"昨天 → 今天"的比对。"""
    rows = engine.query(
        'SELECT * FROM analytics.market_breadth ORDER BY trade_date DESC LIMIT %s',
        (max(1, int(n)),))
    return sorted(rows, key=lambda r: r['trade_date'])


def on(trade_date: dt.date) -> dict | None:
    return engine.query_one(
        'SELECT * FROM analytics.market_breadth WHERE trade_date = %s', (trade_date,))


# ---------------------------------------------------------------- CLI
#
#     python -m app.compute.breadth --status                 # 这一族实有几何
#     python -m app.compute.breadth --backfill               # 全史回算（一条 SQL，约十几秒）
#     python -m app.compute.breadth --backfill --from 2025-01-01 --to 2025-06-30
#
# 退出码：0 正常 / 1 没跑成（没数可算、区间不合法、或恒等式核对不过）。
def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys as _sys

    _sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(
        prog='app.compute.breadth',
        description='市场宽度 + 阿姆氏指标：每日读数落库、全史回算、对账（口径只住本模块）')
    ap.add_argument('--backfill', action='store_true',
                    help='按当前口径把 [from, to] 的 up_amount / down_amount / arms_ratio 重算并覆盖')
    ap.add_argument('--from', dest='frm', default=None, help='起始场次 YYYY-MM-DD（缺省＝库里最早一场）')
    ap.add_argument('--to', default=None, help='结束场次 YYYY-MM-DD（缺省＝库里最晚一场）')
    ap.add_argument('--status', action='store_true', help='打印库里这一族的场数 / 有值数 / 分布 / 核对结果')
    args = ap.parse_args(argv)

    if args.backfill:
        _p = dt.date.fromisoformat
        out = backfill(_p(args.frm) if args.frm else None, _p(args.to) if args.to else None)
        print(json.dumps(out, ensure_ascii=False))
        if not out.get('ok'):
            return 1
        print(json.dumps(status(), ensure_ascii=False, indent=2, default=str))
        return 0

    st = status()
    print(json.dumps(st, ensure_ascii=False, indent=2, default=str))
    # 追平的定义说得出、可核：一场不漏（NULL 的那场要在 --status 里看得见），
    # 且 `up_amount + down_amount > 当日总成交额` 这条恒等式 0 场违反。
    return 0 if st.get('sessions') and not st.get('up_plus_down_gt_total') else 1


if __name__ == '__main__':
    raise SystemExit(main())
