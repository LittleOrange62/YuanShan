# -*- coding: utf-8 -*-
"""
板块热度的**历史回补**：绕开个股评分，直接从 `market_data.sector_daily` 算板块级热度。

## 为什么要有这个模块

`analytics.sector_heat_daily` 原来是从 `analytics.score[sector_heat]` 按成员股聚回板块的
（见 `sector_trend.refresh_heat`），于是它的历史长度被个股评分卡住 ——
实测只有 **23 个交易日**（2026-07-29 ~ 08-28）。而"趋势启动点"的本质是
"相对自己历史的异常"：23 天连 60 日分位都算不出来，位置、压缩这些要素
全都没有基线可比。

关键事实：`sector_heat` 的四个分项脚本（`sector_zhangfu / sector_guangdu /
sector_dongliang / sector_chengjiao`）读的**全是 `market_data.sector_daily`**
（`ctx.sector_today / sector_roll`），既不依赖 `analytics.indicator_value`（45 天）
也不依赖个股评分（142 天）。个股那一步只是 `ctx.broadcast()` 把板块值原样
摊给成员。所以板块级热度完全可以直接算，而 `market_data.sector_daily` 有
**2021-08-02 起 1,231 个交易日、652 板块/天、成交额全非空**。

## 口径必须与四个分项脚本一字不差

这里是**第二实现**，最容易犯的错是"差不多就行"—— 两套口径一旦漂移，页面上
的热度和这里算的分位就不是一个东西。所以：

- 权重从 `config.view_effective_binding` **现取**，不写死（改注册表这里跟着变）；
- 每一项的归一化都复刻 `ScoreCtx.pct_raw / clip_raw`（见下面的 `_pct` / `_clip`）；
- 滚动窗口复刻 `ctx.sector_roll` 的 `min_periods=1`（点数不足也给值）；
- 缺失分项**不计权重**（`加权和 ÷ 有值项权重和`），与评分驱动一致；
- `reconcile()` 拿现有 23 天逐日逐板块对账，差异分布打出来给人看。

## 顺带查出一个真缺陷：旧口径本身是错的

回补前拿旧行对账，9,558 行重叠里平均绝对差 **18.67 分**、最大 **128.16**、
相关系数只有 **0.708**。查下来不是这里算错，而是 `refresh_heat()` 的聚合方式错：

- `ctx.broadcast()` 摊下去时，每只票用的是 `indicators.sector_map()` 给的
  **唯一**主板块（行业优先，否则概念里代码最小的那个）；
- 聚回来时用的却是 `reference_data.sector_member` 的**全部**归属，而一只票平均属于
  **8.76** 个板块（最多 169 个）。

于是每个板块的 heat 实际是"成员股各自**主**板块热度的平均"。最刺眼的例子：
`TDX_880082`（「板块趋势」，风格板块，3 只成分）2026-08-12 旧 heat = 35.56，
可那 3 只票的主板块分别是 880540 / 880599 / 880645 —— 这个 35.56 是三个**别的
板块**的热度均值，而「板块趋势」当天自己是 `pct_change=-0.22%`、1 涨 2 跌。

所以本模块不是"给旧口径补历史"，是**换掉一个错口径**，并把全历史统一成一种算法
（`source='sector'`）。口径验收看 `verify_against_builtin()`。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from app.db import engine

CAP_KEY = 'sector_heat'

#: 四个分项在 `config.binding` 里的 script_key → 本模块的列名
PART_OF_SCRIPT = {
    'sector_zhangfu_v1': 'zhangfu',
    'sector_guangdu_v1': 'guangdu',
    'sector_dongliang_v1': 'dongliang',
    'sector_chengjiao_v1': 'chengjiao',
}

#: 权重取不到时的兜底（= 05_seed.sql 里的登记值）。**只是兜底**，
#: 正常路径一定走 config.view_effective_binding。
FALLBACK_WEIGHTS = {'zhangfu': 0.30, 'guangdu': 0.30,
                    'dongliang': 0.25, 'chengjiao': 0.15}

#: 成交分项的默认参数，与 builtin/sector_chengjiao.py 的 DEFAULTS 对齐
CHENGJIAO = {'w_abs': 0.40, 'w_ratio': 0.60, 'ma_n': 20,
             'rel_lo': 0.70, 'rel_hi': 1.50, 'min_ref': 30}

#: 广度分项：成分不足这么多只，广度没有统计意义 → 置空（不是给 0）
MIN_MEMBER = 5

STG = 'market_data.stg_sector_heat'

#: 板块指数单日涨跌幅的**物理上限**。成分股再怎么涨跌停，加权后也到不了 21%
#: （20% 是创业板/科创板的单日限制）。超过它只能是数据不可信。
MAX_ABS_PCT = 21.0

#: 「可用板块」过滤片段。**三处共用**（本模块、`scoring.sector_panel`、
#: `app/compute/ignition.py`），所以只在这里定义一次。两个条件：
#: 涨跌幅在物理极限内（下面这段实测）+ 历史天数够（见 `MIN_HISTORY`）。
#:
#: 实测（2026-08-30）：653 个有日线的板块里，**46 个**在历史上出现过
#: `|pct_change| > 21%`，最离谱的 `TDX_880046` 有 +429,900%（close 在 0.01 / 1 / 2 / 3
#: 之间乱跳，high=2.0 而 open=0.01，成交额还写着 2,975 亿）。这 46 个分两类：
#:
#: - **无名板块**（`reference_data.sector_info.name` 就是 '880046' 这种纯数字，49 个里有 18 个
#:   在此列）—— 板块名都没采到，日线自然也是错位解析的产物；
#: - **「昨日XX」类风格板块**（昨日连板 / 昨日涨停 / 昨日跌停 / 活跃股…）——
#:   这类板块的成分**每天重新筛**，所谓"指数点位"本来就没有连续性，
#:   涨跌幅天然乱跳。不是数据错，是这种板块不存在连续指数这回事。
#:
#: 排除之后剩 607 个板块 / 672,942 行，涨跌幅极值 **−20.81% ~ +20.77%** ——
#: 正好落在涨跌停极限内，这才是可以拿来算分位和回测的数据。
#: （`q_sector_heat` 早就用 `i.name !~ '^[0-9]+$'` 在页面层挡过一部分，
#: 但计算层一直没挡。这里换成**数据自证**的判据，不用维护名单。）
#:
#: 写成 `NOT IN (GROUP BY ... HAVING max(...))` 而不是相关子查询
#: `NOT EXISTS (... bad.sector_code = d.sector_code ...)`：后者每行都要回表扫一遍
#: market_data.sector_daily（72 万行），2026-08-31 实测同一个 60 天面板查询 **32.4s vs 1.4s**，
#: 而 reader 角色的 statement_timeout 是 1 分钟 —— d2_scores 当天就是这么超时挂掉的。
#: 聚合版一次算出 46 个不可信板块再哈希，行数与结果完全一致（36,296 行）。
#: NULL 也等价：sector_code 是主键非空，全 NULL 的 pct_change 两种写法都判为"可信"。
USABLE_SECTOR = """
    {alias}.sector_code NOT IN (
        SELECT bad.sector_code FROM market_data.sector_daily bad
         GROUP BY bad.sector_code
        HAVING max(abs(bad.pct_change)) > {limit}
            OR count(*) < {min_days})
"""

#: 一个板块至少要有这么多天日线才进热度截面。**= WARMUP**（MA20 要 20 天，多给 5 天
#: 余量），下面 `WARMUP = MIN_HISTORY` 把两者绑成一个数。
#:
#: 2026-09-01 加这一条，起因是同花顺板块首次入库：`market_data.sector_daily` 当天多出
#: **86 个 `THS_*` 板块，历史只有 1 天**。它们照样算出了 heat（`min_periods=1`
#: 点数不足也给值），于是当天的截面从 652 个板块变成 738 个 —— 而热度里三个分项
#: 是**截面百分位**，分母一变，08-31 的热度就和 08-28 的热度不是同一把尺子量的，
#: 时间序列、60/120 日分位、启动点全部失去可比性。这 86 个板块自己的动量更是拿
#: 1 个点算 MA5/MA20 的产物（实测 `analytics.sector_trend` 里它们 `point_count >= 20` 的
#: 行数是 **0**）。
#:
#: 判据写成"天数下限"而不是"排除 THS_ 前缀"：攒够 25 天它们自己就进来了，
#: 不用维护名单，也不用第二次改代码。


MIN_HISTORY = 25



def usable_clause(alias: str = 'd') -> str:
    """
    「可用板块」的 SQL 片段 = 数据可信（涨跌幅在物理极限内）**且**历史够长。
    **三处共用**（本模块、`scoring.sector_panel`、`ignition.load_sector`）。

    两个条件塞进**同一个聚合子查询**：分成两个 `NOT IN` 就要扫 market_data.sector_daily
    两遍（72 万行），而 reader 角色的 statement_timeout 只有 1 分钟 —— 见
    `MAX_ABS_PCT` 上面记的 32.4s vs 1.4s 那笔账。
    """
    return USABLE_SECTOR.format(alias=alias, limit=MAX_ABS_PCT,
                                min_days=MIN_HISTORY)



# ---------------------------------------------------------------- 归一化
# 与 app/compute/scoring.py 的 ScoreCtx 同名方法逐字对应，但作用在**整段面板**
# 上（按 trade_date 分组），而不是单日截面 —— 这是"一次算完 1,231 天"的关键。

def _pct(s: pd.Series, dates: pd.Index) -> pd.Series:
    """截面百分位 → -100 ~ +100。等价于 `ScoreCtx.pct_raw` 逐日调用。"""
    v = pd.to_numeric(s, errors='coerce')
    r = v.groupby(dates).rank(pct=True, ascending=True)
    return r * 200 - 100


def _clip(s: pd.Series, lo: float, hi: float) -> pd.Series:
    """截断映射 → -100 ~ +100。等价于 `ScoreCtx.clip_raw`。"""
    v = pd.to_numeric(s, errors='coerce').clip(lo, hi)
    return (v - lo) / (hi - lo) * 200 - 100


def _roll(panel: pd.DataFrame, col: str, n: int) -> pd.Series:
    """
    板块级滚动均值，复刻 `ctx.sector_roll(col, n, 'mean')`。

    `min_periods=1` 是**照抄**，不是笔误：原实现点数不足也给值（MA5 只有
    2 个点也算），这里若改成 min_periods=n，回补出来的早期热度就和线上口径
    不是一回事了。点数够不够由 `n_days` 列如实记录，交给下游判断。
    """
    s = pd.to_numeric(panel[col], errors='coerce')
    out = (s.groupby(level='sector_code', sort=False)
             .rolling(n, min_periods=1).mean())
    if out.index.nlevels > s.index.nlevels:
        out = out.droplevel(0)
    return out.reindex(panel.index)


# ---------------------------------------------------------------- 权重
def weights() -> dict[str, float]:
    """从注册表现取四个分项的生效权重（已归一）。"""
    rows = engine.query(
        'SELECT script_key, eff_weight FROM config.view_effective_binding '
        ' WHERE capability_key = %s', (CAP_KEY,))
    out: dict[str, float] = {}
    for r in rows:
        part = PART_OF_SCRIPT.get(r['script_key'])
        if part and r['eff_weight'] is not None:
            out[part] = float(r['eff_weight'])
    return out or dict(FALLBACK_WEIGHTS)


# ---------------------------------------------------------------- 读取
def load(start: dt.date | None = None, end: dt.date | None = None,
         *, sectors: list[str] | None = None) -> pd.DataFrame:
    """
    读板块日线面板，索引 (sector_code, trade_date)，按时间升序。

    `start` 要比真正需要的首日**再往前 20 个交易日**，否则 MA20 的头部会退化
    成短窗口（`min_periods=1` 不报错，只是悄悄变短）—— 调用方用
    `run(..., warmup=)` 处理，这里只负责老实取数。
    """
    where, params = ['1=1', usable_clause('d')], {}
    if start:
        where.append('d.trade_date >= %(start)s')
        params['start'] = start
    if end:
        where.append('d.trade_date <= %(end)s')
        params['end'] = end
    if sectors:
        where.append('d.sector_code = ANY(%(secs)s)')
        params['secs'] = list(sectors)
    rows = engine.query(
        'SELECT d.sector_code, d.trade_date, d.pct_change, d.amount_yuan, '
        '       d.up_count, d.down_count, d.member_count '
        '  FROM market_data.sector_daily d WHERE ' + ' AND '.join(where) +
        ' ORDER BY d.sector_code, d.trade_date', params)

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for c in ('pct_change', 'amount_yuan', 'up_count', 'down_count',
              'member_count'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df.set_index(['sector_code', 'trade_date']).sort_index()


# ---------------------------------------------------------------- 四个分项
def parts(panel: pd.DataFrame) -> pd.DataFrame:
    """
    一次算完全历史的四个分项。返回列：zhangfu / guangdu / dongliang / chengjiao。

    不返回"这个板块有多少历史天数"这类元信息：本函数拿到的面板可能带暖机段
    （增量更新时只读最近几十天），在那种情况下算出来的天数是**面板内的**天数，
    不是板块真实历史长度 —— 落一个会骗人的数比不落更糟。下游要判断分位可不可信
    时，直接对 `analytics.sector_heat_daily` 数行数。
    """

    if panel.empty:
        return pd.DataFrame()
    dates = panel.index.get_level_values('trade_date')
    out = pd.DataFrame(index=panel.index)

    # ---- 涨幅：0.45 当日 + 0.55 五日均（都是截面分位）
    ma5 = _roll(panel, 'pct_change', 5)
    out['zhangfu'] = (_pct(panel['pct_change'], dates) * 0.45
                      + _pct(ma5, dates) * 0.55)

    # ---- 广度：0.6 当日 + 0.4 五日；分母用"涨+跌"，停牌不进分母
    up, dn = panel['up_count'], panel['down_count']
    traded = (up + dn).replace(0, pd.NA)
    ratio = up / traded * 100
    up5, dn5 = _roll(panel, 'up_count', 5), _roll(panel, 'down_count', 5)
    ratio5 = up5 / (up5 + dn5).replace(0, pd.NA) * 100
    guangdu = _clip(ratio, 20.0, 80.0) * 0.6 + _clip(ratio5, 30.0, 70.0) * 0.4
    thin = panel['member_count'].fillna(0) < MIN_MEMBER
    out['guangdu'] = guangdu.mask(thin)

    # ---- 动量：0.45 二十日动量 + 0.55 加速度（5 日 − 20 日）
    ma20 = _roll(panel, 'pct_change', 20)
    out['dongliang'] = (_pct(ma20, dates) * 0.45
                        + _pct(ma5 - ma20, dates) * 0.55)

    # ---- 成交：绝对分位 + 相对放量（**必须先除掉全市场当日中位数**）
    p = CHENGJIAO
    amount = panel['amount_yuan']
    abs_score = _pct(amount, dates)
    amt_n = _roll(panel, 'amount_yuan', int(p['ma_n']))
    vol_ratio = amount / amt_n.replace(0, pd.NA)
    med = vol_ratio.groupby(dates).transform('median')
    cnt = vol_ratio.groupby(dates).transform('count')
    rel_ok = (cnt >= int(p['min_ref'])) & med.notna() & (med > 0)
    rel_score = _clip(vol_ratio / med.where(med > 0),
                      float(p['rel_lo']), float(p['rel_hi']))
    w = float(p['w_abs']) + float(p['w_ratio'])
    blended = (abs_score * float(p['w_abs'])
               + rel_score * float(p['w_ratio'])) / w
    # 有数板块不足 min_ref 的那些天，中位数是噪声 → 退回只用绝对分位
    out['chengjiao'] = blended.where(rel_ok, abs_score)

    return out



def heat(part_df: pd.DataFrame, w: dict[str, float] | None = None
         ) -> pd.DataFrame:
    """
    四项加权成板块热度。**空值项不计权重**（与评分驱动一致）。

    返回列 `heat` / `part_count`。四项全空 → heat 为空、part_count=0，
    这一行不会落库（"没算出来"和"算出来是 0 分"必须分开）。
    """
    if part_df.empty:
        return pd.DataFrame()
    w = w or weights()
    num = pd.Series(0.0, index=part_df.index)
    den = pd.Series(0.0, index=part_df.index)
    cnt = pd.Series(0, index=part_df.index)
    for part, weight in w.items():
        if part not in part_df.columns:
            continue
        # **必须 to_numeric**：某一天四项全空时那一列是 object dtype，
        # 直接 fillna(0.0) 出来还是 object，`num / den` 会变成 object，
        # 最后 .round() 抛 "Expected numeric dtype"。这条路径真的会走到 ——
        # 板块日线缺一天，四个分项就全空。
        v = pd.to_numeric(part_df[part], errors='coerce')
        ok = v.notna()
        num = num + (v.fillna(0.0) * float(weight)).where(ok, 0.0)
        den = den + pd.Series(float(weight), index=part_df.index).where(ok, 0.0)
        cnt = cnt + ok.astype(int)
    out = pd.DataFrame({'heat': (num / den.where(den > 0)).round(4),
                        'part_count': cnt.astype(int)})
    return out



# ---------------------------------------------------------------- 落库
UPSERT = f"""
INSERT INTO analytics.sector_heat_daily
       (sector_code, trade_date, heat, member_scored_count, part_count, source, task_id)
SELECT sector_code, trade_date, heat, NULL, part_count, 'sector', task_id
  FROM {STG}
ON CONFLICT (sector_code, trade_date) DO UPDATE SET
    heat          = EXCLUDED.heat,
    member_scored_count = EXCLUDED.member_scored_count,
    part_count       = EXCLUDED.part_count,
    source           = EXCLUDED.source,
    task_id       = EXCLUDED.task_id,
    calculated_at = now()
"""


def write(df: pd.DataFrame, *, task_id: int | None = None) -> int:
    """COPY 进 UNLOGGED 临时表再一条 INSERT 合并 —— 沿用 scoring._write 的范式。"""
    rows = [(sec, d, None if pd.isna(h) else float(h), int(n), task_id)
            for (sec, d), h, n in zip(df.index, df['heat'], df['part_count'],
                                      strict=True)
            if not pd.isna(h)]
    if not rows:
        return 0
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {STG}')
        cur.execute(f'CREATE UNLOGGED TABLE {STG} (sector_code text, '
                    'trade_date date, heat numeric(16,4), part_count smallint, '
                    'task_id bigint)')
    engine.copy_rows(STG, ['sector_code', 'trade_date', 'heat', 'part_count',
                           'task_id'], iter(rows), role='admin')
    n = engine.execute(UPSERT, role='admin')
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {STG}')
    return n


# ---------------------------------------------------------------- 清理
#: 「当前口径认的板块」。判据与 `usable_clause` 一字对应，只是方向相反 ——
#: 那边是"哪些行能读进来"，这边是"哪些板块该留在结果表里"。
#:
#: `coalesce(max(...), 0)`：`pct_change` 全为 NULL 的板块，`max(abs(...))` 是 NULL，
#: `NULL <= 21` 是 NULL 而不是 true —— 不 coalesce 就会把它判成"不认"，
#: 和 `usable_clause`（`NOT IN`，NULL 那边判为可用）**方向相反**，
#: 于是 prune 删掉的正是下一次 run 又会写回来的行，两个动作互相拆台。
USABLE_SET = """
    SELECT ok.sector_code FROM market_data.sector_daily ok
     GROUP BY ok.sector_code
    HAVING coalesce(max(abs(ok.pct_change)), 0) <= %(limit)s
       AND count(*) >= %(min_days)s
"""


#: 要跟着口径一起清的**派生表**：(表名, 板块列, 额外条件)。
#:
#: 三张表都清，不只是热度那张：`analytics.ignition` 读的是热度，热度里没了的板块
#: 它自己不会消失（实测 2026-09-01 清完热度之后 `analytics.ignition` 还留着 THS_ 的
#: 86 行 08-31 数据，而页面上"启动点"和"板块热度"是同一屏的两张卡）。
#:
#: heat 只清 `source='sector'`：`member_agg` 那批旧行是 `reconcile()` 的对照基准，
#: 页面早就不读了，留着当历史记录。trend 没有 source 列，非可用板块的行整删。
PRUNE_TARGETS = (
    ('analytics.sector_heat_daily', 'sector_code', "source = 'sector'"),
    ('analytics.sector_trend', 'sector_code', None),
    ('analytics.ignition', 'object_key', "scope = 'sector'"),
)


def prune(*, dry_run: bool = False) -> dict:
    """
    删掉**当前口径不认的板块**在派生表里的历史行，返回 {表: 行数}。

    为什么必须有这一步：热度是 `ON CONFLICT DO UPDATE` 写的，只会覆盖，
    **不会删**。所以每次口径收紧，之前那次口径写进去的行就永久留在库里，
    而页面读 `source='sector'` 是不带判据的 —— 表现为"08-28 有 652 个板块、
    08-31 只有 604 个"，同一列在不同日子含义不同。实测这次清掉的是：

    - 46 个不可信板块：heat 46,883 行（2021-08-02 ~ 2026-08-28）+ trend 374,880 行
      —— 全历史回补跑在 `MAX_ABS_PCT` 判据**之前**（都是 2026-08-30 那天），
      判据加上之后没人回头清过；
    - 在 `market_data.sector_daily` 里根本没有日线的板块（`TDX_SP_*` 那批旧行）；
    - 历史不足 `MIN_HISTORY` 天的新板块（THS_ 那 86 个）。

    ⚠️ **清完要重算**：留下来的历史行里，三个截面分位分项当初是按"当时那个
    板块宇宙"排的名，删几个板块并不会把它们的分位改回来。所以口径一变，
    正确做法是 `d3_sector_trend.py --recent <全历史>` + `d4_ignition.py --full`
    （2026-09-01 实测 324s + 99s），只清不算＝库里两种尺子并存。

    删的全是**派生数据**，源头 `market_data.sector_daily` 一行不动，所以这里不做备份、
    不做软删。
    """
    p = {'limit': MAX_ABS_PCT, 'min_days': MIN_HISTORY}
    out: dict[str, int] = {}
    for tbl, col, extra in PRUNE_TARGETS:
        where = f'{col} NOT IN ({USABLE_SET})'
        if extra:
            where = f'{extra} AND {where}'
        out[tbl] = (engine.scalar(f'SELECT count(*) FROM {tbl} WHERE {where}',
                                  p)
                    if dry_run else
                    engine.execute(f'DELETE FROM {tbl} WHERE {where}', p,
                                   role='admin'))
    return out


# ---------------------------------------------------------------- 对账
def reconcile(new: pd.DataFrame, *, limit: int = 12) -> dict:
    """
    拿库里 `source='member_agg'` 的旧行和新算的板块级口径比。

    **实测结论（2026-08-30，9,558 行重叠）：两者对不上，而且是旧口径错。**
    平均绝对差 18.67 分、p95 = 49.28、最大 128.16、相关系数只有 0.708。

    原因查清了，不是这里算错：

    - `ctx.broadcast()` 用的是 `indicators.sector_map()` 给每只票的**唯一**主板块
      （行业优先、否则概念里代码最小的那个）；
    - 而 `sector_trend.refresh_heat()` 是用 `reference_data.sector_member` 的**全部**归属把
      个股分聚回板块，一只票平均属于 **8.76** 个板块（最多 169 个）。

    于是每个板块的 heat 变成了"成员股各自**主**板块热度的平均"。最极端的例子：
    `TDX_880082`（「板块趋势」，风格板块，3 只成分）2026-08-12 的旧 heat = 35.56，
    而这 3 只票的主板块分别是 880540 / 880599 / 880645 —— 那个 35.56 是三个**别的
    板块**的热度均值，与「板块趋势」当天 `pct_change=-0.22%`、1 涨 2 跌毫无关系。

    所以本模块**不是**"给旧口径补历史"，而是替换掉一个错口径。差异大在这里是
    预期结果；真正的口径验收看 `verify_against_builtin()`（逐板块对线上脚本，
    实测最大差 0.17 分＝一个排名位的浮点抖动）。
    """

    old = engine.query(
        "SELECT sector_code, trade_date, heat FROM analytics.sector_heat_daily "
        " WHERE source = 'member_agg' AND heat IS NOT NULL")
    if not old:
        return {'compared': 0, 'note': '库里没有 member_agg 口径的行，跳过对账'}
    o = pd.DataFrame(old).set_index(['sector_code', 'trade_date'])['heat']
    o = pd.to_numeric(o, errors='coerce')
    n = new['heat'].reindex(o.index)
    both = pd.DataFrame({'old': o, 'new': n}).dropna()
    if both.empty:
        return {'compared': 0, 'note': '两边没有重叠的 (板块, 日期)'}
    diff = (both['new'] - both['old']).abs()
    worst = diff.sort_values(ascending=False).head(limit)
    return {
        'compared': int(len(both)),
        'missing_in_new': int(o.notna().sum() - len(both)),
        'mean_abs_diff': round(float(diff.mean()), 4),
        'p50': round(float(diff.quantile(0.50)), 4),
        'p95': round(float(diff.quantile(0.95)), 4),
        'max': round(float(diff.max()), 4),
        'within_1': int((diff <= 1).sum()),
        'within_5': int((diff <= 5).sum()),
        'corr': round(float(both['old'].corr(both['new'])), 6),
        'worst': [{'sector_code': i[0], 'trade_date': str(i[1]),
                   'old': round(float(both.loc[i, 'old']), 2),
                   'new': round(float(both.loc[i, 'new']), 2),
                   'diff': round(float(v), 2)}
                  for i, v in worst.items()],
    }


# ---------------------------------------------------------------- 主流程
def verify_against_builtin(trade_date: dt.date, *, lookback: int = 72) -> dict:
    """
    口径验收：拿**线上四个 builtin 脚本**跑同一天，逐板块比本模块的向量化结果。

    这是唯一有意义的对账对象。做法是让 `ScoreContext` 正常跑 builtin，拿到摊到
    个股上的分值，再按 `ctx.today['sector']` 反查每个板块的值（同一板块的成员
    拿到的是同一个板块值，取第一个即可）—— 于是比较的是"板块级原值 vs 板块级原值"。

    实测（2026-08-28）：广度、成交最大差 0.00005（纯舍入）；涨幅、动量最大差
    0.1687 —— 那正好是 `0.55 × 一个排名位（200/652）`，来源是 MA5/MA20 的浮点
    尾差让两个相邻板块的排名互换。**不是口径差**，所以容差按"半个排名位"设。
    """
    import pandas as _pd

    from app.compute import indicators as ind_mod
    from app.compute import scoring as sc_mod
    from app.compute.builtin import (
        sector_chengjiao,
        sector_dongliang,
        sector_guangdu,
        sector_zhangfu,
    )


    panel = ind_mod.load_panel(trade_date, lookback)
    sd = sc_mod.sector_panel(trade_date, lookback)
    if panel.empty or sd.empty:
        return {'error': f'{trade_date} 没有面板或板块日线'}
    ctx = sc_mod.ScoreContext(trade_date, panel, sd)
    secmap = ctx.today.get('sector')
    if secmap is None:
        return {'error': '面板里没有 sector 列（indicators.sector_map 返回空？）'}
    mine = parts(load(None, trade_date)).xs(trade_date, level='trade_date')

    out: dict[str, dict] = {}
    for name, mod in (('zhangfu', sector_zhangfu), ('guangdu', sector_guangdu),
                      ('dongliang', sector_dongliang),
                      ('chengjiao', sector_chengjiao)):
        s = _pd.to_numeric(mod.run(ctx, {}), errors='coerce')
        by = (_pd.DataFrame({'s': secmap, 'v': s}).dropna()
                 .groupby('s')['v'].first())
        a = _pd.to_numeric(mine[name], errors='coerce').reindex(by.index)
        both = _pd.DataFrame({'builtin': by, 'mine': a}).dropna()
        diff = (both['builtin'] - both['mine']).abs()
        out[name] = {'sectors': int(len(both)),
                     'max_diff': round(float(diff.max()), 6) if len(both) else None,
                     'mean_diff': round(float(diff.mean()), 6) if len(both) else None}
    return out


#: 回补时往前多读几天做暖机。**和 `MIN_HISTORY` 是同一个数**：要暖机 25 天，
#: 就等于"历史不足 25 天的板块本来也算不出可信的 MA20"，两个常量各写一遍
#: 迟早对不上。
WARMUP = MIN_HISTORY



def run(start: dt.date | None = None, end: dt.date | None = None, *,
        task_id: int | None = None, dry_run: bool = False,
        echo=None) -> dict:
    """
    回补 [start, end] 的板块热度。

    `start` 之前会自动多读 `WARMUP` 个交易日做暖机，但**只写 start 之后的行** ——
    暖机行的 MA20 本身是残缺的，写进去等于污染。
    """
    say = echo or (lambda *_a, **_k: None)
    lo = None
    if start:
        prev = engine.query(
            'SELECT DISTINCT trade_date FROM market_data.sector_daily '
            ' WHERE trade_date < %s ORDER BY trade_date DESC LIMIT %s',
            (start, WARMUP))
        lo = prev[-1]['trade_date'] if prev else start
    say(f'读板块日线：{lo or "全部"} ~ {end or "最新"}（含 {WARMUP} 天暖机）')
    panel = load(lo, end)
    if panel.empty:
        return {'rows': 0, 'error': 'market_data.sector_daily 没有取到数据'}
    days = panel.index.get_level_values('trade_date').nunique()
    secs = panel.index.get_level_values('sector_code').nunique()
    say(f'  面板 {len(panel):,} 行 / {secs} 个板块 / {days} 个交易日')

    w = weights()
    say('  权重（config.view_effective_binding）：'
        + '  '.join(f'{k}={v:.2f}' for k, v in sorted(w.items())))
    pdf = parts(panel)
    hdf = heat(pdf, w)
    full = pdf.join(hdf)
    if start:
        keep = full.index.get_level_values('trade_date') >= start
        full = full[keep]
    ok = full['heat'].notna()
    say(f'  算出热度 {int(ok.sum()):,} 行（四项全空 {int((~ok).sum()):,} 行不落库）')

    rec = reconcile(full)
    say('  对账（vs 现有 member_agg 口径）：'
        + (f"{rec['compared']:,} 行重叠，平均绝对差 {rec.get('mean_abs_diff')}，"
           f"p95={rec.get('p95')}，最大 {rec.get('max')}，"
           f"相关系数 {rec.get('corr')}"
           if rec.get('compared') else rec.get('note')))

    if dry_run:
        return {'rows': 0, 'computed': int(ok.sum()), 'dry_run': True,
                'weights': w, 'reconcile': rec}
    n = write(full, task_id=task_id)
    say(f'  落库 {n:,} 行 → analytics.sector_heat_daily（source=sector）')
    return {'rows': n, 'computed': int(ok.sum()), 'weights': w,
            'reconcile': rec, 'days': days, 'sectors': secs}
