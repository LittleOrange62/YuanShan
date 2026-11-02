-- -*- coding: utf-8 -*-
-- 56_index_market_value.sql
-- 「国家队借道沪深300ETF」这一屏要的两张表：
--   ① `market_data.index_market_value` —— 指数级总市值/流通市值的**每日快照**（比例的分母）
--   ② `analytics.national_team_etf_daily` —— 两把尺的 ETF 份额/规模/净申赎与占比（比例的分子与结果）
--
-- ── 分母为什么主尺走本地自算（这一段是**改过口的**，第一版结论是错的，留在这里当依据）──
-- 用户点名要"头部沪深300基金市值 ÷ 沪深300流通市值"。分母探了四条路，实测如下：
--   ① 东财指数盘口 `push2 .../stock/get?secid=1.000300&fields=f116,f117` 给**当日**值：
--     实测 2026-10-27 总市值 51,000,638,979,882、流通市值 48,415,042,212,523。
--     ⚠️ 这台机器对 push2 有频率封（本轮连打几次后 curl 直接连不上）⇒ **不能当主尺**，只当对照。
--   ② 中证官网历史行情接口（ak.stock_zh_index_hist_csindex）实测 2023-01 起 923 行，
--     列里有 收盘/成交量/成交金额/样本数量/滚动市盈率 —— **没有市值**。
--   ③ **本地自算 = 通**（这才是主尺）：`market_data.stock_snapshot` 有每票当日流通市值
--     （`float_market_value_yuan`，c15 从本机通达信插件库搬进来，字段 Ltsz），
--     对 `reference_data.sector_member.ZS_沪深300` 的 300 只求和：
--     实测 2026-10-27 流通 **48.38 万亿** / 总 **51.01 万亿**，299/299 只有值，
--     与东财当日 48.42 / 51.00 万亿差 **−0.08% / +0.02%** ⇒ 两条独立来源对上到千分之一以内。
--     ⚠️ 第一版我判"自算不通"（算出 6.23 万亿、差 −87%），错在拿 `reference_data.share_capital.float_share`
--     去乘收盘 —— 那列 28,271 行里 **25,251 行是 NULL**（c8/c9 只回算总股本），
--     300 只成分里当日之前有流通股本行的只有 61 只。**快照表里现成的流通市值才是对的路子。**
--   ④ 插件库那个 SQLite 按日落盘 ⇒ `stock_snapshot` 最早只到 **2026-07-22（47 场）**，
--     所以分母的历史深度 = 47 场，**往回补不了**，只能从今天起每天攒。
--     好消息：2026-07-22 至今正好落在中证一次成分调整（6 月生效）之内，
--     用现在这份名单回溯这 47 场是可辩护的；**下一次调整（12 月）起必须按月落成分快照**，
--     否则往后回溯就又变成"拿今天的名单冒充过去"。
-- ⇒ 结论：**主尺＝本地自算①＋③**（每天算得出来、可复核）；①那张表只存外部对照；
--    攒不到的日子在库里就是 null，界面显示 `—` 并现查 `min(trade_date)` 说明首日；
--    **不许插值、不许问模型**（模型没有逐日真值，让它"算给你"就是让它编）。
--
-- ── ②为什么存"事实"而不存"判据" ──
-- 份额、规模、净申赎、当日占比是**那一天就算出来的数**，落库后含义不随判据改动而变；
-- 而"骤增骤降"与"超高/超低分档"是**阈值型判据**，阈值一改历史行就挂着旧含义（本项目反复被教育过），
-- 所以那两样**不落库**，由读侧 `duanxian/national_team.py` 按当次窗口现算并把阈值随接口交出。
--
-- ── 两把尺（用户 2026-10-26 点名"1 和 3 都计算一次，但凡有一个报警都提示"）──
--   `big4` = 510300.SH(华泰柏瑞) + 510310.SH(易方达) + 510330.SH(华夏) + 159919.SZ(嘉实)，
--            实测 2021-06-01 起四只都在两所每日份额名单里（510300 当日 103.4 亿份 → 2026-10-26 3,544.7 亿份）；
--   `all`  = 全部跟踪沪深300的场内 ETF（名单住在 `reference_data.security` 里按名称/类型识别，
--            实测现认 10 只"跨市"类 + 深市若干）。
-- 同一场两把尺各有各的 `ratio_float_pct`，界面两条线并排画，**任一越界都要提示并写明是哪一把报的**。

create table if not exists market_data.index_market_value (
    trade_date        date           not null,
    index_code        text           not null,
    spec_version      text           not null,
    index_name        text,
    total_mv_yuan     numeric(24, 2),            -- 总市值（元）
    float_mv_yuan     numeric(24, 2),            -- 流通市值（元）＝这一屏比例的分母
    source            text           not null,   -- 'eastmoney_push2' / 以后可能有别把尺，必须标
    captured_at       timestamptz    not null default now(),
    source_batch_id   bigint,
    primary key (trade_date, index_code, spec_version)
);

comment on table market_data.index_market_value is
  '指数级市值的**外部对照快照**（主尺是本地自算，见文件头③）。源=东财指数盘口 f116/f117，只有当日值，'
  '且这台机器对 push2 有频率封 ⇒ 这张表允许有空日，读侧拿它只做"与自算差多少"这一件事，不当分母。'
  '缺数交 null，界面显示 —；不插值、不问模型。';
comment on column market_data.index_market_value.float_mv_yuan is
  '流通市值（元）。实测 2026-10-27 沪深300 = 48,415,042,212,523，同日本地自算（成分 × 快照流通市值求和）'
  ' = 48.38 万亿，差 −0.08% ⇒ 两边是同一件事的两条独立算法，谁都不许替谁补数。';
comment on column market_data.index_market_value.source is
  '哪一把尺给的市值。以后若接进中证官网或通达信插件库，同一 (trade_date,index_code) 会有两行不同 source，'
  '读侧必须按 source 取，不许默认第一行。';

create table if not exists analytics.national_team_etf_daily (
    trade_date          date           not null,
    scale_key           text           not null,   -- 'big4' | 'all'
    spec_version        text           not null,   -- 'nt-1'
    etf_count           integer        not null,   -- 当天这一把尺里真取到份额行的只数
    share_units         numeric(24, 2),            -- 份额合计（份）
    size_yuan           numeric(24, 2),            -- 规模合计（元）＝Σ(份额 × 当日收盘)
    net_creation_units  numeric(24, 2),            -- 当日净申赎（份），口径见下
    float_mv_yuan       numeric(24, 2),            -- 当日指数流通市值（分母，null＝那天还没入库）
    ratio_float_pct     numeric(10, 4),            -- 规模 ÷ 流通市值 × 100
    total_mv_yuan       numeric(24, 2),
    ratio_total_pct     numeric(10, 4),            -- 规模 ÷ 总市值 × 100（备用尺）
    members             jsonb,                     -- 当天这一把尺实际算了哪几只（代码数组）
    missing             text,                      -- 逐列记「这一格为什么判不了」，界面上那句 — 的理由读它
    captured_at         timestamptz    not null default now(),
    source_batch_id     bigint,
    primary key (trade_date, scale_key, spec_version)
);

comment on table analytics.national_team_etf_daily is
  '国家队代理指标：沪深300ETF 的份额/规模/净申赎与占指数市值比例，两把尺各一行。'
  '这里只存**当日算得出的事实**；异动与历史分档是阈值型判据，不落库（阈值一改历史行就挂旧含义），'
  '由读侧按窗口现算并随接口交阈值。';
comment on column analytics.national_team_etf_daily.share_units is
  '份额合计（**份**，不是亿元）。实测 2026-10-26 big4 = 807.09 亿份（510300 354.47 + 510310 216.02 '
  '+ 510330 132.55 + 159919 104.06）—— 界面换算成亿份要除 1e8，别把份当元。';
comment on column analytics.national_team_etf_daily.size_yuan is
  '规模合计（**元**）＝Σ(份额 × 当日收盘)。⚠️ `market_data.fund_daily.close` 的单位是 0.1 元（角），'
  '换算住在 `app/compute/national_team.py::PRICE_TICK_TO_YUAN`（依据：510300 存 41.79/公开 4.179、'
  '159915 存 26.51/2.651、512880 存 10.21/1.021，且全表 amount÷volume÷close 中位 0.0999）。'
  '第一版没换算，big4 曾算出 3.388 万亿（真值 3,388 亿）、占比 6.94%（真值 0.694%）—— '
  '这类 10 倍错看数字看不出来，所以计算侧带一道隐含均价 0.1~200 元的自检。';

comment on column analytics.national_team_etf_daily.net_creation_units is
  '当日净申赎＝当日总份额 − 前一日总份额，且**首发份额不计入**（新上市 ETF 首日那一笔是成立规模不是申购）。'
  '这条不是理论洁癖：`market_data.view_etf_flow` 实测 2026-08-26 深交所 4 只新 ETF 贡献 +11.31 亿份，'
  '把当天真实的 −28.05 亿份净赎回冲成 −16.73 亿份，失真 40%。所以这一列必须走那张视图的分区 LAG，'
  '不许在读侧自己减一遍前日。';
comment on column analytics.national_team_etf_daily.etf_count is
  '这一把尺当天实际取到份额行的只数。名单是 4 只而这里是 3 ⇒ 有一只那天两所文件里没给行，'
  '份额合计就会跳一档，界面要能分开"钱真出去了"与"名单少算了一只"。';
comment on column analytics.national_team_etf_daily.ratio_float_pct is
  '分子分母**同日**才有值。分母＝本地自算（`market_data.stock_snapshot.float_market_value_yuan` × 300 成分求和，'
  '实测与东财当日值差 −0.08%），最早只到 **2026-07-22**（快照表就是那天开始入库的）⇒ 早于它的场次是 null，'
  '界面显示 — 并把首日现查出来，不拿比例外推、不拿模型补。';
comment on column analytics.national_team_etf_daily.members is
  '当天这一把尺实际包含哪些代码（`all` 那把的名单会随上市/清盘变）。存下来是为了事后能回答'
  '"那天这一档是按哪几只算的"，否则口径变了就无从对账。';

grant select                       on market_data.index_market_value to ft_reader;
grant select                       on analytics.national_team_etf_daily to ft_reader;
-- delete 给写侧"重算一场先清这一场"用（与 53 同一套授权）：只给 insert/update 第一步就 permission denied。
grant select, insert, update, delete on market_data.index_market_value to ft_app, ft_etl;
grant select, insert, update, delete on analytics.national_team_etf_daily to ft_app, ft_etl;
