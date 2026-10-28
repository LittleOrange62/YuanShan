-- -*- coding: utf-8 -*-
-- 23_allocator.sql
-- 「分配者视角」四块读数的落点（来源与判据：docs/分配者视角工作指南.md，
-- 原始出处是用户 2026-10-06 发来的一段《A股找分配者》视频原文）。
--
-- 为什么要落库而不是接口现算（同 16_sector_trend_signal 的理由）：
-- 这几块回答的是「**那一天**它算不算共识区/收费站」。现算意味着每次请求重跑一遍
-- 250 场窗口，而且阈值一改，历史上所有板块的阶段含义就跟着被悄悄改写。
-- 落库 + `spec_version` 进主键 = 当时那一版判据算出的结论永远原样在。
--
-- 三条纪律（这套分数最容易骗人的地方）：
--   ① 算不出就是 NULL，不是 0。缺分红的板块 `div_yield_med` 是 NULL、缺市值快照的
--      板块 `mv_share` 是 NULL —— 0 分会参与排名，NULL 不参与，这是两件事。
--   ② 每一项都同时交出**覆盖度**（`div_coverage`/`mv_coverage`），不然"板块股息率中位"
--      会被读成"整个板块的股息率"，而它可能只算了三分之一成员。
--   ③ `stage_reason` 必须写清是哪几个数触发的，`missing` 写清哪几项没进总分。

create table if not exists analytics.sector_allocator (
  sector_code     text           not null,
  trade_date      date           not null,
  spec_version    text           not null,       -- 判据版本，在主键里：调阈值不覆盖旧行
  sector_name     text,
  sector_type     text,                          -- 行业 / 概念（母体只这两类，见 universe）
  universe        integer,                       -- 当日参与排名的板块数：分母不写出来，分位没有意义

  -- ---- 资金往哪儿走（第一块：位置 = 收费站）----
  share           numeric(12,6),                 -- 当日成交额占全场板块成交额之比（份额，不是金额）
  share_pctile    numeric(10,3),                 -- 该份额在自己近 250 场里的分位（自身分位 ≠ 名次）
  share_med250    numeric(12,6),                 -- 近 250 场份额中位：长期"必须经过"的体量
  amount_stability numeric(10,3),                -- 1 − 变异系数（近 250 场日成交额）；越接近 1 越稳
  div_yield_med   numeric(10,4),                 -- 板块内 A 股成员 TTM 现金股息率中位（小数，0.045=4.5%）
  div_coverage    numeric(10,3),                 -- 有多少比例的成员算得出股息率（缺了就交 NULL）
  mv_share        numeric(12,6),                 -- 板块成分总市值占全市场之比
  mv_coverage     numeric(10,3),
  toll_score      numeric(10,2),                 -- 位置分 0~100（权重见模块文档，是可商量的先验）
  toll_rank       integer,

  -- ---- 资源正在往哪里灌（第二块：拥挤度温度）----
  share_slope20   numeric(12,6),                 -- 当日份额 ÷ 前 20 场（跳过最近 5 场）均值 − 1：涌入速度
  member_count    integer,                       -- 板块成员数（当日）
  member_delta    integer,                       -- 与约一年前比：项目/公司同时在进场 = 产能进入的本地证据
  margin_crowd    numeric(10,3),                 -- 板块成分融资余额 ÷ 流通市值（本地只有 2026-06 起）
  crowding_temp   numeric(10,2),                 -- 0~100 温度；100 不是卖出信号，是"所有人都信了"
  crowding_rank   integer,

  -- ---- 第三种读数：阶段（政策扶持→繁荣→涌入→疯狂→过剩）----
  mom60           numeric(10,3),                 -- 板块指数 60 场涨幅（%）
  breadth20       numeric(10,3),                 -- 近 20 场上涨家数占比均值（普涨还是权重股拉的）
  stage           text,                          -- s1_seed / s2_boom / s3_inflow / s4_consensus / s5_overhang / none
  stage_reason    text,                          -- 是哪几个数触发的（事后必须能复算出同一句话）

  -- ---- 第四块的两个坐标（位置 vs 想象力）----
  imagine_score   numeric(10,2),                 -- 想象力分 0~100（涨幅 + 广度 + 份额热度 + 概念属性）
  quadrant        text,                          -- toll(收费站) / consensus(共识区) / story(故事区) / nowhere
  missing         text,                          -- 这一行哪几项算不出（不写=全有）
  calculated_at   timestamptz    not null default now(),

  primary key (sector_code, trade_date, spec_version)
);

comment on table  analytics.sector_allocator            is '分配者视角：每个板块在每个交易日的「位置/拥挤/阶段/象限」四块读数（判据与来源见 docs/分配者视角工作指南.md）';
comment on column analytics.sector_allocator.spec_version is '判据版本，在主键里：调阈值/权重不会改写历史那批行';
comment on column analytics.sector_allocator.share      is '成交额份额（本板块 ÷ 全场板块），不是金额也不是净流入 —— 绝对成交额会跟着全场放量一起涨，份额才是"钱往哪儿走"';
comment on column analytics.sector_allocator.share_pctile is '自身近 250 场的分位（不是名次）：份额 0.4% 在历史上可能是 98 分位；方向与"名次"相反容易读反，界面必须写口径';
comment on column analytics.sector_allocator.amount_stability is '1 − 日成交额的变异系数（近 250 场）。这是"只要社会运转钱就必须经过我"的本地代理，不等于制度性垄断 —— 后者本库没有数据';
comment on column analytics.sector_allocator.div_yield_med is 'TTM 现金股息率中位（reference_data.dividend 近 365 天除息 ÷ market_data.stock_snapshot 市值）。分母只有约 34 场快照，所以这是**当下**的读数，不能回算历史';
comment on column analytics.sector_allocator.member_delta is '与约一年前相比板块成员数的变化：产业政策落地时"所有人同时上项目"在本地唯一看得见的形状（不是产能数据，本库没有产能）';
comment on column analytics.sector_allocator.crowding_temp is '0~100 的拥挤度温度。**100 的意思是"所有人终于达成共识"，不是"该卖了"** —— 视频原话：A股最危险的时刻不是没人相信故事，而是所有人终于达成共识';
comment on column analytics.sector_allocator.stage        is '再分配生命周期五段（互斥、按顺序判）：s1_seed→s2_boom→s3_inflow→s4_consensus→s5_overhang，都不满足是 none。判据是资金与价格的形状，不含政策文本 —— 本地没有政策库';
comment on column analytics.sector_allocator.quadrant     is 'x=想象力分 y=位置分，分界线取**当日中位数**（不写死 50，否则分布一动象限就全体搬家）';
comment on column analytics.sector_allocator.missing      is '这一行哪几项算不出（如 div,mv,margin）。NULL 不参与排名，但必须让人看得见它为什么是 NULL';

create index if not exists sector_allocator_day_idx
  on analytics.sector_allocator (trade_date, spec_version, crowding_temp desc);
create index if not exists sector_allocator_sector_idx
  on analytics.sector_allocator (sector_code, trade_date desc);

-- 唯一写入方是本项目的加工链（`ingest/jobs/e1_fill_all.py` 的 `allocator` 步）。
grant select, insert, update, delete on analytics.sector_allocator to ft_etl, ft_app;
grant select on analytics.sector_allocator to ft_reader;
