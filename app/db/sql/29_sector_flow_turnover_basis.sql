-- -*- coding: utf-8 -*-
-- 29_sector_flow_turnover_basis.sql
-- 给「资金轮动趋势」加第三条口径：basis='turnover'（换手强度）。
--
-- 为什么要这第三条（用户 2026-10-12 的问题原话：「这个流入流出也已经参照总市值去折算了吧」）：
--   **没有。** 现有 share 口径的分母是"当日 915 个板块成交额简单相加"，全程没有市值参与。
--   share 剔的是"全场共同放量/缩量"，它**不剔"这个板块盘子本来就大"** ——
--   所以份额累计天然偏向窄题材（小板块跳 0.1pp 只要几亿，大板块要几十亿）。
--   turnover 剔的正是后者：`板块成交额 ÷ 板块成分流通市值合计`，单位 %（当日换手率）。
--   两把尺子不能互相替换，只能并排：实测 2026-10-12 同一场里，`银行` 份额名次 57 → 换手名次 822，
--   `白银` 份额名次 738 → 换手名次 32，`光通信` 824 → 146（差 678 个名次）。
--
-- 分母（流通市值）从哪来 —— 这是这条判据的核心，别再被改回去：
--   ❌ 不用 `market_data.stock_snapshot.float_market_value_yuan`：**那张表只有 36 场**
--      （2026-07-22 → 2026-10-12，且稀疏：10-09 就没有），拿它当分母这条口径就只能覆盖 36 场，
--      近 10/20/30 场的累计与名次根本攒不满。
--   ✅ 用当日日线反推：`流通市值 = 100 × amount_yuan ÷ turnover_rate_percent`。
--      `stock_daily.turnover_rate_percent` 实测**全史 100% 覆盖**（2021→2026 各年非零率 100.0%），
--      所以这条口径能从 2021-08-03 起逐日算。
--      与快照同日逐票对照（2026-10-08，5,557 只可比）：**中位绝对差 0.911%**、p90 2.74%、
--      差<1% 的 2,986 只 —— 换手率本来就是源站按流通市值给的，反推与快照同源，
--      差的这点是四舍五入与快照抓取时点。这段实测必须留在注释里，因为它是"为什么不嫌麻烦反推"的唯一依据。
--   两条边界：① 换手率或成交额为 0/NULL（停牌、缺字段）的成员**从分子和分母同时剔掉**，
--      并交 `members_matched` / `members_total` 让读者看见剔了多少；② 全被剔 ⇒ 该板块这一行 `value` 交 NULL，
--      **不交 0**（0% 的意思是"今天没人交易它"，不是"算不出"）。
--
-- 分子用哪个成交额（也实测过才敢定）：
--   `market_data.sector_daily.amount_yuan` 与该板块成员成交额之和，2026-10-12 那场中位比值 **1.0000**
--   （概念 p10 0.9976），且中位匹配成员数 111.5 = 库记 `member_count` 111.5、行业 18 = 18 ——
--   板块日线就是成分加总，两边同源。但 turnover 这一行分子改用**匹配成员的和**：
--   否则被剔掉的那几只（停牌）的钱还留在分子里、市值却不在分母里，换手率会虚高。
--
-- 成员归属按 `in_date/out_date` **对齐到那一天**（as-of），不是"今天的成员表回看历史"：
--   后者是给历史塞未来信息。母体类型仍然只认 `reference_data.sector_info.sector_type`
--   （行业 + 概念，与 share 口径同一份参数），不是按代码段排除 ——
--   10-06 那批市场级统计指数（880001~880099，成交额=全A）就是靠改登记类型挡掉的。
--
-- `value` 的量纲随 basis 变（这一条最容易坑后来人，所以两处都写了注释）：
--   share    = 份额，0~1 的小数（界面 ×100 读成百分之几）
--   turnover = 换手率，**百分数本身**（1.79 就是 1.79%），不再 ×100
--   `day_delta_bps` 两种口径都是"bp"：share 是 (Δ份额)×1e4，turnover 是 (Δ百分点)×1e2，
--   数值上同一个意思 —— 本度量变了 1 个基点。

-- 1) 放开 basis 的 check：加 'turnover'
alter table analytics.sector_flow_trend
  drop constraint if exists sector_flow_trend_basis;
alter table analytics.sector_flow_trend
  add constraint sector_flow_trend_basis
  check (basis in ('share', 'main_net', 'turnover'));

-- 2) 换手口径专用的四列（share / main_net 那批行留 NULL，不回填）
alter table analytics.sector_flow_trend
  add column if not exists mv_yuan        numeric(24,4),
  add column if not exists amount_matched numeric(24,4),
  add column if not exists members_matched smallint,
  add column if not exists members_total   smallint;

comment on column analytics.sector_flow_trend.mv_yuan        is 'basis=turnover 专用：本板块**匹配成员**的流通市值合计（元），就是换手率的分母。市值是当日日线反推的（100×成交额÷换手率），不是 stock_snapshot —— 那张表只有 36 场，撑不起近 10/20/30 场的窗口';
comment on column analytics.sector_flow_trend.amount_matched is 'basis=turnover 专用：分子。只加"有成交额且有换手率"的那些成员的钱，与 mv_yuan 严格同一母体；sector_daily.amount_yuan 里含着被剔掉的停牌成员，直接拿来做分子会虚高换手率';
comment on column analytics.sector_flow_trend.members_matched is '分母里有几只成员凑得出流通市值（换手率/成交额都在）';
comment on column analytics.sector_flow_trend.members_total   is '那一天在册的成员总数（按 in_date/out_date 对齐，不是今天的成员表）。两列一起看才知道剔了多少：实测 2026-10-12 母体 915 个板块里中位匹配率 1.000、最低 0.800（5 个成员里 1 只停牌）';
comment on column analytics.sector_flow_trend.value           is '当日度量。**量纲随 basis 变**：share=份额(0~1 的小数)；turnover=当日换手率(百分数本身，1.79 就是 1.79%，别再乘 100)；main_net=净额(元)';
comment on column analytics.sector_flow_trend.day_delta_bps   is '当日与上一场之差，统一按基点交：share 口径 (Δ份额)×1e4，turnover 口径 (Δ百分点)×1e2。两边都是"这个度量变了 1bp"，界面别再各自换算一次';
comment on column analytics.sector_flow_trend.cum_10          is '近 N 场累计 = 末场 value − 首场 value 的位移幅度，不是流量、不是强度、也不计路径（冲到顶再回来与一路爬升是同一个数）。turnover 口径单位是百分点(pp)';
