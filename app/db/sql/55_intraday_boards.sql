-- -*- coding: utf-8 -*-
-- 55_intraday_boards.sql
-- 盘中三张"算好的榜"：09:25 竞价榜、概念计数（竞价/热度/午间共用）、12:10 午间复盘快照。
-- 判据出处全部是外部 `intraday_zt/` 那三个脚本 2026-10-19~10-23 实测定型的版本，
-- 搬迁时**判据一字不改**，只改两件事：① 缺数不再拿默认值顶；② 涨幅单位统一到百分数。

-- ---------------------------------------------------------------- 一、竞价榜
-- 09:25:30 涨/跌两榜 + 「竞价一字」块（首封时刻恰好 09:25＝集合竞价即封死）。
-- 主源东财 push2 clist（**风控域**，串行每页间歇 2s），挂了整个榜切新浪备源，日志记降级。
create table if not exists analytics.auction_board (
    trade_date        date        not null,
    board             text        not null,           -- up=涨幅榜 | down=跌幅榜 | yizi=竞价一字
    rank              integer     not null,           -- 榜内序号（1 起，按当刻涨幅序）
    spec_version      text        not null,

    security_code     text        not null,
    security_name     text,
    pct               numeric(10,4),                  -- 竞价涨幅 %（9:25 撮合价 vs 昨收）
    amount_yuan       numeric(24,2),                  -- 成交额（元）。**新浪备源没有这一列 ⇒ null**，不填 0
    board_count       smallint,                       -- 仅 yizi：连板数（含本场），缺 ⇒ null
    seal_fund_yuan    numeric(24,2),                  -- 仅 yizi：封单额（元）
    first_seal_time   text,                           -- 仅 yizi：'HH:MM'，判据就是它 == '09:25'
    industry          text,

    listed_n          integer,                        -- 该榜全场命中只数（榜只交前 top_n，其余"未列"要留数）
    truncated_n       integer,                        -- 超出未列的只数 = listed_n - 行数
    source            text        not null,           -- eastmoney_clist | sina_market_center
    unavailable       boolean     not null default false, -- 整侧没取到（旧版明标"未取到"，这里留结构位）
    reason            text,                           -- 没取到/未就绪的原因原文
    captured_at       timestamptz not null default now(),
    calculated_at     timestamptz not null default now(),

    constraint auction_board_pkey primary key (trade_date, board, rank, spec_version)
);
create index if not exists ix_auction_board_code on analytics.auction_board (security_code, trade_date desc);

comment on table analytics.auction_board is
    '09:25 竞价速览的双榜与一字块。涨幅榜/跌幅榜分侧独立取数：一侧挂了不影响另一侧出榜。';
comment on column analytics.auction_board.pct is
    '竞价涨幅口径＝9:25 集合竞价价对昨收。页脚原文："竞价价按9:25开盘价"；非 9 点手工跑要带"样机演示"标记。';
comment on column analytics.auction_board.amount_yuan is
    '新浪备源这一列恒 null（源里就没有）。旧实现取了从不渲染，搬进来留列、缺就报缺。';

-- ---------------------------------------------------------------- 二、概念计数（三处共用一张表）
-- 「几只涨停/跌停股命中同一个概念」这一把尺，竞价（9:25 双榜）、热度（10:05/10:55/14:18 实时池）、
-- 午间复盘（12:10 当日涨停归组）用的是**同一个 SQL、同一个剔除门槛**，所以只留一张表、
-- 一个实现（`app/intraday/concepts.py`）。分开三张必然出现某一处偷懒改门槛。
create table if not exists analytics.intraday_concept_count (
    trade_date        date        not null,
    asof_ts           timestamptz not null,           -- 这一批计数对应的时点（9:25 / 10:05 / 12:10 …）
    bucket            text        not null,           -- auction_up|auction_down|heat_zt|heat_dt|noon_zt
    sector_name       text        not null,           -- 概念名（sector_info.name）
    spec_version      text        not null,

    sector_code       text,                           -- 能唯一定位时给；一名多码时留 null 并在 multi_code 记
    multi_code        boolean     not null default false,
    hit_n             integer     not null,           -- 命中只数（组内涨停/跌停股数）
    rank              integer,                        -- 组内降序序号（heat_top 截断前）
    share_pct         numeric(8,4),                   -- 仅 noon_zt：占当日涨停家数的 %（分母＝该桶全部家数）
    tier              text,                           -- 仅 noon_zt：mainline(>=40%)|sub(>=20%)|spot
    top_board_count   smallint,                       -- 仅 noon_zt：梯队高度＝组内最高连板
    reps              text,                           -- 仅 noon_zt：代表股（连板高、封单大优先，前 3）
    sector_pct        numeric(10,4),                  -- 该板块**上一交易日收盘**涨跌幅 %
    sector_pct_asof   date,                           -- 上面那个数是几号的（9:25 时点当日板块指数无有效价，不造）
    unmapped_n        integer,                        -- 该组个股里没映射到任何概念的只数（诚实项，不是 0）
    calculated_at     timestamptz not null default now(),

    constraint intraday_concept_count_pkey primary key (trade_date, asof_ts, bucket, sector_name, spec_version)
);
create index if not exists ix_intraday_concept_bucket on analytics.intraday_concept_count (trade_date desc, bucket);

comment on table analytics.intraday_concept_count is
    '一组个股命中哪些概念、各几只。竞价/热度/午间三处共用，SQL 与剔除门槛只有一处实现。';
comment on column analytics.intraday_concept_count.sector_pct is
    '存**百分数**。库里 market_data.sector_daily.pct_change 也是百分数（10-26 复测同场 avg|1.79|、max 9.30，与 stock_daily 同量纲）；'
    '迁移过程中我先误判成"库内是小数要 ×100"，渲染出 -232% 才发现，已撤销 —— 别拿单行样本定单位。';
comment on column analytics.intraday_concept_count.tier is
    '阈值 0.40/0.20 是外部 10-23 定版（noon_review.py:182-192），不是统计出来的分位 —— 换判据要换 spec_version。';

-- ---------------------------------------------------------------- 三、12:10 午间复盘（一场一行）
create table if not exists analytics.noon_review (
    trade_date        date        not null,
    spec_version      text        not null,

    -- 一、情绪温度
    limit_up_n        integer,                        -- 上午收盘终态涨停家数（池子挂了 ⇒ null）
    limit_down_n      integer,
    unseal_n          integer,                        -- 炸板家数
    unseal_rate_pct   numeric(8,4),                   -- 炸板率 % = 炸板 ÷ (涨停+炸板)
    max_board_text    text,                           -- 最高板原文，如 '5天4板'；数据有缺口退化成 '4连板'
    max_board_count   smallint,
    board2_codes      text,                           -- 二板名单（原文串）
    idx_sh_pct        numeric(10,4),                  -- 三大指数上午收盘涨跌幅 %
    idx_sz_pct        numeric(10,4),
    idx_bj_pct        numeric(10,4),
    up_count          integer,                        -- 全市场涨家数。**0 是真实读数**，旧版 `or None` 会把 0 吞成缺数
    down_count        integer,
    premium_n         integer,                        -- 昨日涨停今日溢价的样本数
    premium_pos_n     integer,
    premium_pos_rate_pct numeric(8,4),                -- 正溢价占比 %
    premium_avg_pct   numeric(10,4),                  -- 平均涨幅 %
    mood_label        text,                           -- 阈值规则贴的标签（冰点偏退潮/退潮分歧/中性…）
    mood_tags         text,                           -- 命中的条件原文（倒挂/炸板率高/涨跌比极端弱势…）

    -- 二、三、文本段（页面上要能原样看见"发了什么"）
    section_mood      text,
    section_cluster   text,
    section_watch     text,
    body              text,                           -- 实际发出的整条

    -- 出处与缺数（午间复盘是三个源拼的：东财池 + push2 ulist + PG 日线 + 腾讯行情）
    pool_source       text,
    index_source      text,                           -- eastmoney_push2 | tencent_qq（降级要看得见）
    ladder_basis      text,                           -- stock_daily 回算的基准日范围说明
    delivered         boolean,                        -- 业务 code==0 才算送达（不是 HTTP 200）
    missing           jsonb       not null default '{}'::jsonb,
    calculated_at     timestamptz not null default now(),

    constraint noon_review_pkey primary key (trade_date, spec_version)
);

comment on table analytics.noon_review is
    '12:10 午间复盘快照。12:10 是午间休市，池子＝上午收盘终态；analytics.market_breadth 那时点还是昨日的数，不能拿来报今日。';
comment on column analytics.noon_review.mood_label is
    '阈值规则标签（炸板率>=0.35、up*5<down 等硬编码，外部 10-23 定版），**不是预测、不含买卖建议**。';
comment on column analytics.noon_review.premium_n is
    '样本＝昨日涨停名单（PG stock_daily 判 close>=limit_up-0.005，容差 0.005 与本地日线口径同源），其今日涨幅走腾讯批量行情。';

grant select on analytics.auction_board, analytics.intraday_concept_count, analytics.noon_review to ft_reader;
grant select, insert, update, delete on analytics.auction_board, analytics.intraday_concept_count, analytics.noon_review to ft_etl, ft_app;
grant select on analytics.auction_board, analytics.intraday_concept_count, analytics.noon_review to ft_app;
