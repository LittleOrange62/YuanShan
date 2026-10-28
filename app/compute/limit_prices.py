# -*- coding: utf-8 -*-
"""涨跌停档位的**当日**权威源与 `stock_daily.limit_up/limit_down` 的唯一写入口。

为什么要有这个模块（2026-10-06 实测，全部可复算）
--------------------------------------------------
原先有**两套**写 limit 的路径，且档位来自"这只票现在是什么属性"而不是"那一天是什么属性"：

1. `app/db/repo/quote.py::recompute_limits()` 用 `reference_data.security.is_st`
   —— 一个 updated_at 停在 2026-08-27 的**当前快照** —— 套全部历史。
   后果：主板 + 现在是 ST 的票，在它**还不是 ST 的年份**被判成 ±5%。
   越界行 **16,074 行 / 1,698 只**（2021-08-03~2026-09-30 全史哨兵实测），
   越界行当日涨幅多在 5.5%~10.5%。
2. `ingest/jobs/c15_snapshot.py` 把通达信 `day_end_*.ZTPrice` 直接填进 `stock_daily`
   （`coalesce(d.limit_up, v.zt)`）。后果：除权日那个价与库里 pre_close **不同基**。
   实测 2026 年 limit_up/pre_close 不落在 5/10/20/30 任何一档的行 6,088 个，
   其中 **5,895 个是复权因子变化日**。

两头一起坏的结果具体到读数：2026-09-30 涨停家数，严格口径（`abs(close-limit_up)<=0.005`）
只给 **52** 只，而通达信本地统计指数 `sh880006.day`（停板家数）与乐咕乐股都是 **56** 只 ——
差的 4 只全是"主板 + 当前 is_st"、close 恰为 round(pre_close×1.1,2) 的票。

本模块定的权威（分层，谁生效写进 `limit_basis`，不假装都是权威）
--------------------------------------------------------------
1. `tdx` —— 通达信当日实报涨停价反推出的档位（只反推**档位**，价格本身仍旧现算）。
   覆盖 `reference_data.limit_price_daily` 里那 22 场（2026-09-01~10-06，12.2 万行）：
   再早的 `day_end_*` 表只有客户端内部序号 `ItemNum`、没有 `Code`，对不回票，一律不灌。
2. `proved_non_st` —— **当日行情可证**：主板当日涨跌超过 ±5% 档**算出来的那个价**
   （或涨幅超 5.2 个百分点）⇒ 那天绝不可能是 ±5% 档。这一层把 16,074 行越界一次性改对，
   且不依赖任何快照。用"比价格"而不是"比百分点"是因为涨停价是分位四舍五入的，
   低价股 5% 档实际只能到 +4.89%（3.68→3.86），拿百分点判会把这类行漏掉。
3. `st_period` —— `reference_data.security_st_period` 确证的**当日 ST 区间**（只给 0.05）。
   非 ST 那一侧不再 join：它给的是 0.10，而主板默认规则本来就是 0.10，多一次 join
   只换来一个更漂亮的出处标签，实测单日多花 4 秒 —— 不值得。
   （次新那条判据读 `reference_data.security_session_span`，见 `app/db/sql/27_...sql`：
   它要的是"这只票**全历史**的头 5 场"，按单日/区间重刷时不该每行回扫分区表。）
4. `rule_st_snapshot` —— 只有当前 `is_st` 快照支持 5% 的行：仍然按 5% 算，
   但**出处标成猜的**，读数侧与体检能看见（这是历史欠账，不是修好的值）。
5. `rule` —— 按板块规则（创业/科创 20%、北交所 30%、主板 10%）。
6. `unbounded` / `no_limit_first5` / `contradicted` —— 这三类给 **NULL，不硬凑**：
   前两类是"那天确实没有涨跌停价"（可证幅度超过本板块合法档 +2pp、或次新前 5 场）；
   `contradicted` 是**收口层**：档已经选定，但这一天收盘价仍然越过按该档算出的上下限
   （多半是 `pre_close` 与行情不同基的除权行）—— 留着一个必然越界的价，不如留 NULL 并写明原因。
   有了收口层，`--check` 那条哨兵才是**结构上**归零的。

口径与行情**同基**这一条是硬约束：`limit = round(pre_close × (1 ± band), 2)`，
`pre_close` 与 OHLC 同基（除权日它是除权参考价），所以除权日照现算是对的、
通达信的未复权价一律不采信其数值。

用法
----
    python -m app.compute.limit_prices --seed-tdx              # 从本机通达信快照灌档位
    python -m app.compute.limit_prices --recompute --dry-run   # 只报会改多少行
    python -m app.compute.limit_prices --recompute             # 备份 + 全库重刷（一条语句，慢但只看一次窗口）
    python -m app.compute.limit_prices --recompute --since 2021-08-03 --until 2026-09-30 --months 6
                                                               # 按片重刷：一片一提交一行的进度，断了重跑幂等
    python -m app.compute.limit_prices --recompute --date 2026-09-30
    python -m app.compute.limit_prices --restore operations.limit_price_backup_20261007
    python -m app.compute.limit_prices --check                 # 哨兵：还有没有 close 越过当日涨跌停价
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db import engine  # noqa: E402

#: 每个板块**合法的最大**档位；可证幅度超过它 +2pp 就是"这天的限不受这一套管"
BOARD_MAX_BAND = {'主板': 0.10, '创业板': 0.20, '科创板': 0.20, '北交所': 0.30}
#: 全库日线起点：早于它的不算"次新"（否则每只老票的头 5 天都会被误判成无限制日）
DATA_START = dt.date(2021, 8, 3)
EPS_BAND = 0.004          # 反推档位时的容差
EPS_UNBOUNDED = 0.02      # 超过本板块合法档这么多才判"无限制/不同基"
PROVED_NON_ST = 0.052     # 主板可证非 ±5 个点档的百分点门槛（>5.2 个点，与 security_st_period 的判据一致）
#: **全项目唯一**的"收盘价与涨跌停价容差"（元）：半分。读 limit_up/limit_down 的地方都从这里取，
#: 不许再各自写 0.01 / 0.005。实测代价：单边 1 分容差把"差一分钱没封住"算进涨停家数
#: （2026-09-30 给 57，通达信 sh880006 与严格双边都是 56）。
#: `--check` 这条哨兵保证收盘不越过涨停价，所以此时
#: `close >= limit_up - 0.005` 与双边 `abs(close - limit_up) <= 0.005` 等价（08 号契约 坑 1）。
LIMIT_EPS = 0.005
#: 通达信盘后快照库：逐票逐日给 ZTPrice/DTPrice，一张表一场
TDX_SNAPSHOT_DB = Path(r'D:\new_tdx64\daily_close_data.db')

SEED_UPSERT = """
insert into reference_data.limit_price_daily
       (security_code, trade_date, band, tdx_limit_up, tdx_limit_down, source, captured_at)
values (%(code)s, %(day)s, %(band)s, %(zt)s, %(dt)s, 'tdx_snapshot', now())
on conflict (security_code, trade_date) do update set
  band = excluded.band, tdx_limit_up = excluded.tdx_limit_up,
  tdx_limit_down = excluded.tdx_limit_down, source = excluded.source,
  captured_at = excluded.captured_at
"""


def _band_of(zt, pre_close) -> float | None:
    """从通达信当日涨停价反推档位，只认四个合法值。"""
    if not zt or not pre_close or float(pre_close) <= 0:
        return None
    r = float(zt) / float(pre_close) - 1.0
    for cand in (0.05, 0.10, 0.20, 0.30):
        if abs(r - cand) <= EPS_BAND:
            return cand
    return None


#: 通达信盘后快照里"这一天的表能不能用"的判据：要有代码列、要有当日涨停/跌停价与昨收
_NEEDED_COLS = {'Code', 'ZTPrice', 'DTPrice', 'LastClose'}


def tdx_snapshots(db: Path | None = None) -> tuple[list[tuple[dt.date, str]], int]:
    # 默认值在函数体里取，不写在签名上：写成 `db: Path = TDX_SNAPSHOT_DB` 会在导入时
    # 就把路径钉死，测试换成临时库根本不生效（真踩到过一次，它读的是用户的通达信库）。
    """列出可用的 `(交易日, 表名)`，以及**被跳过的表数**。

    跳过是必需的而不是偷懒：2026-07-21 起的一批表只有 `ItemNum`（客户端内部序号）、
    没有 `Code`，拿它对不回任何一只票 —— 硬猜 ItemNum=行号会灌出一整批错档位。
    实测可用的是 `day_end_20260901` 起 22 张。
    """
    db = db or TDX_SNAPSHOT_DB
    if not db.is_file():
        return [], 0
    ok, skipped = [], 0
    with sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True) as c:
        for (t,) in c.execute("select name from sqlite_master where type='table' "
                              "and name like 'day\\_end\\_%' escape '\\' order by name"):
            m = re.search(r'(\d{8})$', t)
            if not m:
                skipped += 1
                continue
            cols = {r[1] for r in c.execute('pragma table_info("%s")' % t)}
            n = c.execute(f'select count(*) from "{t}"').fetchone()[0]                  # noqa: S608
            if not n or not _NEEDED_COLS <= cols:
                skipped += 1                                     # 空表或缺列，不猜
                continue
            ok.append((dt.datetime.strptime(m.group(1), '%Y%m%d').date(), t))
    return ok, skipped


def seed_from_tdx(day: dt.date | None = None, *, dry_run: bool = False,
                  db: Path | None = None) -> dict:
    """把通达信当日涨停价**反推成档位**灌进 `reference_data.limit_price_daily`。

    注意这里存的是 band，不是价格 —— 价格仍旧由 `pre_close×(1+band)` 现算，
    这样除权日不会出现"复权后的行情 + 未复权的涨停价"这种混基。
    """
    db = db or TDX_SNAPSHOT_DB
    if not db.is_file():
        return {'rows': 0, 'skipped': f'找不到通达信快照库 {db}（只读，不动界面）'}
    all_days, skipped_tables = tdx_snapshots(db)
    days = [(d, t) for d, t in all_days if day is None or d == day]
    rows, skipped = [], 0
    for d, t in days:
        with sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True) as c:
            for code, zt, dtp, last in c.execute(
                    f'select Code, ZTPrice, DTPrice, LastClose from "{t}"'):        # noqa: S608
                band = _band_of(zt, last)
                if band is None or not code:
                    skipped += 1
                    continue
                rows.append({'code': code, 'day': d, 'band': band, 'zt': zt, 'dt': dtp})
    if dry_run:
        return {'rows': len(rows), 'unrecognized': skipped, 'days': len(days),
                'tables_unusable': skipped_tables, 'dry_run': True}
    for i in range(0, len(rows), 3000):
        engine.executemany(SEED_UPSERT, rows[i:i + 3000], role='app')
    return {'rows': len(rows), 'unrecognized': skipped, 'days': len(days),
            'tables_unusable': skipped_tables,
            'table_rows': engine.scalar('select count(*) from reference_data.limit_price_daily')}


# 分层定档的一段 SQL：算出每行的 (band, basis)，价格仍旧现算。
# **只有这一份**：范围（单日 / 区间 / 全库）只体现在 `px` 的 WHERE 上，不换 SQL 形状。
#
# 以前这里有三份 `seq`（全表窗口 / 单日相关子查询 / 区间相关子查询）。两份快路实测都更快不了：
# `stock_daily` 按 `trade_date` RANGE 分了 22 个分区，而相关子查询里的比较值是"外层这一行"，
# **分区裁剪用不上**，每行都展开成 22 次索引探测 ⇒ 单日 5,546 行 = 4.6 秒、一个月 = 566 秒；
# 全量那条窗口则是 640 万行一条 UPDATE、40 分钟看不见进度。同一个坑两条路各自踩过一次。
# 现在"这只票的头 5 场"物化在 `reference_data.security_session_span`（一码一行，见那份 DDL）。
_RESOLVE = """
with s as (
    select security_code, board, coalesce(is_st, false) is_st
      from reference_data.security where asset_type = 'stock'
), lp as (
    select security_code, trade_date, band from reference_data.limit_price_daily
), st as (
    select security_code, valid_from, coalesce(valid_to, date '2038-01-01') valid_to
      from reference_data.security_st_period where is_st
), fs as (
    select security_code, first_session, fifth_session
      from reference_data.security_session_span
), px as (
    select d.security_code, d.trade_date, d.pre_close, d.limit_up, d.limit_down,
           d.close as cl,
           greatest(coalesce(d.high, d.close) / nullif(d.pre_close, 0) - 1,
                    1 - coalesce(d.low, d.close) / nullif(d.pre_close, 0)) as reach,
           -- ±5 个点那一档"算不出今天这个价"的**精确**判据：涨停价是分位四舍五入的，
           -- 低价股 5 个点的涨停价与 pre_close×1.05 差的不止一分钱（3.68→3.86=+4.89 个点），
           -- 所以"涨了多少个百分点"那种阈值判不干净，直接比**算出来的价格**才不冤枉人。
           -- ⚠️ 这段注释里不许出现裸的百分号：psycopg 带参数时把 SQL 里任何一处
           --    百分号当占位符起始，**注释里也算**（这条有测试扫组装后的 SQL）。
           (round(d.pre_close * 1.05, 2) < coalesce(d.high, d.close)
             or round(d.pre_close * 0.95, 2) > coalesce(d.low, d.close)) as not5,
           s.board, s.is_st, lp.band as tdx_band,
           (st.security_code is not null) st_day,
           -- 头 5 场：还不满 5 场的票（fifth_session 为 NULL）任何一天都算"在头 5 场里"
           coalesce(d.trade_date <= coalesce(fs.fifth_session, date 'infinity'), false) in_first5,
           fs.first_session as first_day,
           case s.board when '北交所' then 0.30 when '创业板' then 0.20
                        when '科创板' then 0.20 else 0.10 end as rule_band
      from market_data.stock_daily d
      join s on s.security_code = d.security_code
      left join lp on lp.security_code = d.security_code and lp.trade_date = d.trade_date
      left join st on st.security_code = d.security_code
                  and d.trade_date between st.valid_from and st.valid_to
      left join fs on fs.security_code = d.security_code
     where d.pre_close is not null and d.pre_close > 0
       and (%(day)s::date is null or d.trade_date = %(day)s::date)
       and (%(since)s::date is null or d.trade_date >= %(since)s::date)
       and (%(until)s::date is null or d.trade_date <= %(until)s::date)
), lay as (
    select px.*,
           case
             -- 6) 不受这一套管的：可证幅度超过本板块合法档 +2 个百分点（无限制日/混基），给 NULL
             when px.reach > (case px.board when '北交所' then 0.30
                                            when '创业板' then 0.20
                                            when '科创板' then 0.20 else 0.10 end) + %(eps_unb)s
                  then null
             -- 6b) 次新股前 5 场（首日明显晚于全库起点才算次新，否则会把数据边界当上市日）
             when px.in_first5 and px.first_day > %(data_start)s::date then null
             -- 2) 主板当日证明不是 ±5 个点那一档：要么冲过 5.2 个百分点，要么那个价根本算不出来
             when px.board = '主板' and (px.reach > %(proved)s or px.not5) then 0.10
             -- 1) 通达信当日反推的档位，且不与当日行情矛盾
             when px.tdx_band is not null and px.reach <= px.tdx_band + %(eps)s then px.tdx_band
             -- 3) ST 档期确证（只作用于主板）。**不再 join 非 ST 那一侧**：
             --    它给的是 0.10，而主板默认规则本来就是 0.10（下面第 5 层），多一次 join
             --    只换来一个更漂亮的出处标签，实测单日多花 4 秒——不值得
             when px.board = '主板' and px.st_day then 0.05
             -- 4) 只有当前 is_st 快照支持 0.05：值照算，出处标成猜的
             when px.board = '主板' and px.is_st then 0.05
             -- 5) 板块规则
             else px.rule_band
           end as band,
           case
             when px.reach > (case px.board when '北交所' then 0.30
                                            when '创业板' then 0.20
                                            when '科创板' then 0.20 else 0.10 end) + %(eps_unb)s
                  then 'unbounded'
             when px.in_first5 and px.first_day > %(data_start)s::date then 'no_limit_first5'
             when px.board = '主板' and (px.reach > %(proved)s or px.not5) then 'proved_non_st'
             when px.tdx_band is not null and px.reach <= px.tdx_band + %(eps)s then 'tdx'
             when px.board = '主板' and px.st_day then 'st_period'
             when px.board = '主板' and px.is_st then 'rule_st_snapshot'
             else 'rule'
           end as basis
      from px
), res as (
    -- **收口层**：选好档之后，如果这一天的收盘价仍然越过按这一档算出来的上下限，
    -- 说明没有任何一档能解释这天（多半是 pre_close 与行情不同基的除权行）——
    -- 两列给 NULL 并把出处写成 `contradicted`，而不是留着一个必然越界的价。
    -- 有了这一层，`--check` 那条哨兵才是**结构上**归零的，而不是"刷完刚好没人踩线"。
    select security_code, trade_date, pre_close, limit_up, limit_down, board, is_st,
           case when band is not null
                 and (cl > round(pre_close * (1 + band), 2) + %(eps)s::numeric
                   or cl < round(pre_close * (1 - band), 2) - %(eps)s::numeric)
                then null else band end as band,
           case when band is null then basis
                when (cl > round(pre_close * (1 + band), 2) + %(eps)s::numeric
                   or cl < round(pre_close * (1 - band), 2) - %(eps)s::numeric)
                then 'contradicted' else basis end as basis
      from lay
)"""

#: 尾部片段都拼在同一条 WITH 链后面 —— 参数一律走 psycopg 的命名占位，
#: 绝不用 Python 的 % 格式化去"填" SQL（那会把 %(day)s 提前吃掉）。
_TAIL_PREVIEW = """
select basis, count(*) rows,
       count(*) filter (where band is not null
                        and limit_up is distinct from round(pre_close * (1 + band), 2)) 价格会变,
       count(*) filter (where band is null) 将清空
  from res group by 1 order by 2 desc
"""

_TAIL_UPDATE = """
update market_data.stock_daily d
   set limit_up   = round(r.pre_close * (1 + r.band), 2),
       limit_down = round(r.pre_close * (1 - r.band), 2),
       limit_basis = r.basis
  from (select security_code, trade_date, pre_close, band, basis,
               round(pre_close * (1 + band), 2) as new_up,
               round(pre_close * (1 - band), 2) as new_dn
          from res
         where band is not null
           and (not %(only_missing)s or limit_up is null)) r
 where d.security_code = r.security_code and d.trade_date = r.trade_date
   and (d.limit_up is distinct from r.new_up or d.limit_down is distinct from r.new_dn
        or d.limit_basis is distinct from r.basis)
"""

_TAIL_UPDATE_NULL = """
update market_data.stock_daily d
   set limit_up = null, limit_down = null, limit_basis = r.basis
  from (select security_code, trade_date, basis from res where band is null) r
 where d.security_code = r.security_code and d.trade_date = r.trade_date
   and (d.limit_up is not null or d.limit_down is not null
        or d.limit_basis is distinct from r.basis)
"""

_TAIL_BACKUP = """
select security_code, trade_date, limit_up as old_limit_up, limit_down as old_limit_down,
       round(pre_close * (1 + band), 2) as new_limit_up,
       round(pre_close * (1 - band), 2) as new_limit_down,
       band as new_band, basis, now() as backed_up_at
  from res
 where band is not null
   and (limit_up is distinct from round(pre_close * (1 + band), 2)
        or limit_down is distinct from round(pre_close * (1 - band), 2))
"""


def _sql(tail: str, day: dt.date | None = None, *,
         since: dt.date | None = None, until: dt.date | None = None) -> str:
    """一条 WITH 链 + 不同尾巴 = 同一个判据的四种用法，绝不各写一套口径。

    `day/since/until` 只是绑参数（范围体现在 `px` 的 WHERE 上），**SQL 形状只有一个** ——
    以前有三个（全量窗口 / 单日 / 区间各一份 `seq`），漂成两个答案只是时间问题。
    """
    return _RESOLVE + tail


#: 次新判据的物化：全刷一次，日常只补"缺行的"与"还不满 5 场的"两类
SPAN_UPSERT = """
insert into reference_data.security_session_span
       (security_code, first_session, fifth_session, sessions, refreshed_at)
select security_code, min(trade_date),
       (array_agg(trade_date order by trade_date))[5], count(*), now()
  from market_data.stock_daily
 where pre_close is not null and pre_close > 0
   and (%(all)s::boolean or security_code = any(%(codes)s::text[]))
 group by security_code
on conflict (security_code) do update set
  first_session = excluded.first_session, fifth_session = excluded.fifth_session,
  sessions = excluded.sessions, refreshed_at = excluded.refreshed_at
"""

#: 需要补的那批票：表里没有行的（新上市），以及第 5 场还没定下来的（不足 5 场）
SPAN_STALE = """
select d.security_code
  from market_data.stock_daily d
  left join reference_data.security_session_span s using (security_code)
 where d.trade_date = %(day)s::date
   and (s.security_code is null or s.fifth_session is null)
 group by d.security_code
"""


def refresh_first_session(*, all_codes: bool = False, day: dt.date | None = None) -> int:
    """维护 `reference_data.security_session_span`。返回受影响票数。

    为什么不能省：`no_limit_first5` 那一层要的是"这只票**全历史**的头 5 场"，
    而重刷按单日/区间跑 —— 没有这张表，要么每行回扫全表（分区表上还裁不掉），
    要么就只能用 `security.list_date`（全库 0/5,577 有值，指望不上）。
    """
    codes = None
    if not all_codes:
        if not day:
            return 0
        codes = [r['security_code'] for r in
                 engine.query(SPAN_STALE, {'day': day}, role='admin')]
        if not codes:
            return 0
    return engine.execute(SPAN_UPSERT, {'all': all_codes, 'codes': codes or []}, role='admin')


#: 范围谓词：单日 / 区间 / 全库三种调用共用一份
_SCOPE = """(%(day)s::date is null or trade_date = %(day)s::date)
       and (%(since)s::date is null or trade_date >= %(since)s::date)
       and (%(until)s::date is null or trade_date <= %(until)s::date)"""


def _next_month(d: dt.date, n: int = 1) -> dt.date:
    y, m = divmod(d.year * 12 + (d.month - 1) + n, 12)
    return dt.date(y, m + 1, 1)


def _backup_name(day: dt.date) -> str:
    """今天的备份表名；同一天跑第二遍就往后找空位，**绝不覆盖已有的那份**。"""
    base = 'operations.limit_price_backup_%s' % day.strftime('%Y%m%d')
    for i in range(0, 27):
        tbl = base if i == 0 else '%s_%s' % (base, chr(ord('b') + i - 1))
        if not (engine.query_one('select to_regclass(%s) r', (tbl,), role='admin') or {}).get('r'):
            return tbl
    raise SystemExit('备份表名占满（同一天跑了 27 遍？）—— 手动清一下再刷，不替你覆盖')


def recompute(day: dt.date | None = None, *, since: dt.date | None = None,
              until: dt.date | None = None, only_missing: bool = False,
              dry_run: bool = False, backup: bool = True, span: str = 'auto') -> dict:
    """按分层权威重刷 `limit_up/limit_down/limit_basis`。**幂等**：只动真变了的行。

    `only_missing` 的语义**变了**，三点必须说清：
    1. 它以前只看 `limit_up IS NULL` —— 于是"值错但不为空"的行（那 16,074 行主板 ST）
       永远修不到；**修错值只能走不带这个开关的全量**（单日全量 = `--date` 不带 `--only-missing`）。
    2. 它只管**填值那一趟**；判为"这天没有涨跌停价/档位解释不了这天"的行
       （`unbounded`/`no_limit_first5`/`contradicted`）照样会被清空并记下出处 ——
       那是纠正，不是补空，别指望它只碰空行。
    3. 范围只体现在 WHERE 上（`day` / `since..until` / 全库**同一份 SQL**），成本与范围内
       行数同级；不再有"每天扫一遍 640 万行窗口"那种事。

    `span='auto'` 在写之前顺手补一次 `security_session_span`（单日只补缺的那几支，
    全库/区间整表重算一次）；`brush()` 自己补过就传 `span='skip'`。
    **dry-run 一个字都不写**，包括这张表。
    """
    p = {'day': day, 'since': since, 'until': until,
         'eps': EPS_BAND, 'eps_unb': EPS_UNBOUNDED,
         'proved': PROVED_NON_ST, 'data_start': DATA_START, 'only_missing': only_missing}
    scope = (str(day) if day else
             ('%s..%s' % (since, until) if since or until else '全库'))
    out: dict = {'day': scope, 'dry_run': dry_run, 'only_missing': only_missing}
    if dry_run:
        # 只读预览：不建备份表、不写任何行，也不动次新那张表
        out['by_basis'] = [dict(r) for r in engine.query(
            _sql(_TAIL_PREVIEW, day, since=since, until=until), p, role='admin')]
        return out

    engine.execute('set statement_timeout = 0', role='admin')
    if span == 'auto':
        out['span_written'] = refresh_first_session(all_codes=not day, day=day)
    if backup:
        tbl = _backup_name(dt.date.today())
        engine.execute('create table %s as%s' % (
            tbl, _sql(_TAIL_BACKUP, day, since=since, until=until)), p, role='admin')
        out['backup'] = tbl
        out['backup_rows'] = engine.scalar('select count(*) from %s' % tbl, role='admin')

    out['nulls_written'] = engine.execute(
        _sql(_TAIL_UPDATE_NULL, day, since=since, until=until), p, role='admin')
    out['values_written'] = engine.execute(
        _sql(_TAIL_UPDATE, day, since=since, until=until), p, role='admin')
    # 回读也跟着范围走：单日增量去 group by 全表（640 万行）等于白扫一遍
    out['basis_now'] = [dict(r) for r in engine.query(
        """select coalesce(limit_basis,'(未刷过)') basis, count(*) rows,
                  count(*) filter (where limit_up is null) null_limits
             from market_data.stock_daily
            where """ + _SCOPE + """
            group by 1 order by 2 desc""", p, role='admin')]
    return out


def brush(start: dt.date, end: dt.date, *, months: int = 6,
          only_missing: bool = False, backup: bool = True) -> dict:
    """全库重刷按时间切片跑：一片一条语句、一片一次提交、一片打一行进度。

    为什么要有它：整库一条 UPDATE 吃掉 640 万行时**外部看不见它到底在不在动**
    （`--check` 那条链的自查判据第 7 条就是这个），而且中途断了只能整条重来。
    切片之后每片几分钟，幂等门是"这一行的档位与出处对不对"，重跑代价为零。
    """
    t0 = time.time()
    n = refresh_first_session(all_codes=True)      # 次新那张表：整趟只重算一次
    print('次新跨度表 %s 支  %.0fs' % (f'{n:,}', time.time() - t0), flush=True)
    totals = {'nulls_written': 0, 'values_written': 0}
    cur, parts = start, []
    while cur <= end:
        hi = min(end, _next_month(cur, months) - dt.timedelta(days=1))
        t0 = time.time()
        r = recompute(since=cur, until=hi, only_missing=only_missing,
                      backup=backup, span='skip')
        if backup:
            totals['backup'] = r.get('backup')
            totals['backup_rows'] = r.get('backup_rows')
        backup = False                      # 备份只在第一片建一次
        totals['nulls_written'] += r['nulls_written']
        totals['values_written'] += r['values_written']
        print('  %s..%s  填值 %s  清空 %s  %.0fs'
              % (cur, hi, f"{r['values_written']:,}", f"{r['nulls_written']:,}",
                 time.time() - t0), flush=True)
        parts.append('%s..%s' % (cur, hi))
        cur = _next_month(cur, months)
    totals['parts'] = len(parts)
    return totals


_SENTINEL = """
select count(*) 越界行, count(distinct security_code) 只票, min(trade_date) 首见, max(trade_date) 最近
  from market_data.stock_daily
 where limit_up is not null and close is not null and pre_close is not null
   and (close > limit_up + 0.005 or close < limit_down - 0.005)
"""

_DETAIL = """
select d.security_code, d.trade_date, d.pre_close, d.close, d.limit_up, d.limit_basis,
       s.board, s.is_st, round((d.close / nullif(d.pre_close,0) - 1) * 100, 2) pct
  from market_data.stock_daily d join reference_data.security s on s.security_code = d.security_code
 where d.limit_up is not null and d.close is not null
   and (d.close > d.limit_up + 0.005 or d.close < d.limit_down - 0.005)
 order by d.trade_date desc limit 12
"""


def check(*, detail: bool = True) -> dict:
    """哨兵：**不许有收盘价越过当日的涨跌停价**（±0.5 分钱容差）。"""
    engine.execute('set statement_timeout = 0', role='admin')
    out = dict(engine.query_one(_SENTINEL, role='admin') or {})
    if detail and out.get('越界行'):
        out['样本'] = [dict(r) for r in engine.query(_DETAIL, role='admin')]
    return out


def restore(backup_table: str) -> dict:
    """按备份表回退。**只回填备份里有的那些行的 limit 两列**，limit_basis 一并退回 'restored'。"""
    # 表名要拼进 SQL，psycopg 的占位符不能用在标识符位置上 —— 那就把它钉死成一个
    # 形状（schema.table 全小写下划线），不像就拒，别拿"操作员不会乱输"当护栏。
    if not re.fullmatch(r'[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*', backup_table or ''):
        raise SystemExit('备份表名形状不对（要 schema.table 全小写）：%r —— 不拼进 SQL'
                         % (backup_table,))
    r = engine.query_one('select to_regclass(%s) t', (backup_table,), role='admin')
    if not r or not r['t']:
        raise SystemExit('备份表不存在：%s（不猜名字，不替你造）' % backup_table)
    n = engine.execute(
        """update market_data.stock_daily d
              set limit_up = b.old_limit_up, limit_down = b.old_limit_down,
                  limit_basis = 'restored_from_' || to_char(now(), 'YYYYMMDD')
             from %s b
            where d.security_code = b.security_code and d.trade_date = b.trade_date
              and (d.limit_up is distinct from b.old_limit_up
                   or d.limit_down is distinct from b.old_limit_down)""" % backup_table,
        role='admin')
    return {'restored': n, 'from': backup_table}


def main(argv: list[str] | None = None) -> int:
    import json
    ap = argparse.ArgumentParser(description='涨跌停档位当日权威与 limit 价唯一写入口')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--seed-tdx', action='store_true', help='从本机通达信快照反推档位，灌 limit_price_daily')
    g.add_argument('--recompute', action='store_true', help='按分层权威重刷 limit_up/limit_down')
    g.add_argument('--check', action='store_true', help='哨兵：还有没有 close 越过当日涨跌停价')
    g.add_argument('--restore', metavar='TABLE', help='从备份表回退（先 --seed 前的那次备份）')
    ap.add_argument('--date', default=None, help='YYYY-MM-DD，只处理这一天')
    ap.add_argument('--since', default=None, help='YYYY-MM-DD，配合 --until 按时间切片刷一段区间')
    ap.add_argument('--until', default=None, help='YYYY-MM-DD，区间右端（含）')
    ap.add_argument('--months', type=int, default=6,
                    help='区间模式每片几个月（默认 6；一片一条语句、一片一提交、一片一行进度）')
    ap.add_argument('--dry-run', action='store_true', help='只算不写，且不留任何写库副作用')
    ap.add_argument('--only-missing', action='store_true',
                    help='只补 limit_up 为空的行（语义已变，见 recompute 文档）')
    ap.add_argument('--no-backup', action='store_true', help='跳过备份（**默认不给你走**，除非明确带上）')
    a = ap.parse_args(argv)
    day = dt.date.fromisoformat(a.date) if a.date else None
    since = dt.date.fromisoformat(a.since) if a.since else None
    until = dt.date.fromisoformat(a.until) if a.until else None
    if bool(since) != bool(until):
        print('--since 与 --until 要一起给（半开区间没人替你猜右端）')
        return 2
    if day and (since or until):
        print('--date 与 --since/--until 互斥')
        return 2

    if a.seed_tdx:
        print(json.dumps(seed_from_tdx(day, dry_run=a.dry_run), ensure_ascii=False, default=str))
        return 0
    if a.recompute:
        if not a.dry_run and a.no_backup and not day:
            print('不带备份不许刷区间/全库：库里对象没有独立验证过的备份不改（硬规矩）。去掉 --no-backup 再来。')
            return 2
        if since and not a.dry_run:
            print('=== 区间重刷 %s..%s，每片 %d 个月 ===' % (since, until, a.months))
            print(json.dumps(brush(since, until, months=a.months,
                                   only_missing=a.only_missing,
                                   backup=not a.no_backup), ensure_ascii=False, default=str))
            return 0
        print(json.dumps(recompute(day, since=since, until=until,
                                  only_missing=a.only_missing, dry_run=a.dry_run,
                                  backup=not a.no_backup), ensure_ascii=False,
                         default=str, indent=1))
        return 0
    if a.check:
        r = check()
        print(json.dumps(r, ensure_ascii=False, default=str, indent=1))
        return 0 if not (r.get('越界行') or 0) else 1
    if a.restore:
        print(json.dumps(restore(a.restore), ensure_ascii=False, default=str))
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
