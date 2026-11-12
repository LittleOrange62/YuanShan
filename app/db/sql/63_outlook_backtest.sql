-- -*- coding: utf-8 -*-
-- 63_outlook_backtest.sql
-- 「⑩ 明日展望」两张短名单的隔日表现样本表（工作空间 → 策略回测 里那一屏读它）。
--
-- 为什么要落表而不是每次现算（用户 2026-11-05 的原话：这个每天计算的结果都固定入库可以支持复查的吧）：
--   1) 现算一遍要按场次调用选取函数 106 次、约 8 分钟，界面不能等；
--   2) 更要紧的是**口径会漂**：那个脚本还在被人改，今天算出来的"当时的名单"和明天算的不一样。
--      只有把每次算的结果连参数快照、连脚本指纹一起落下来，事后才问得出"这组胜率是哪版判据给的"。
--   3) 主键含 spec_version：判据改版就换版本号，旧行留着对照，不覆盖 —— 覆盖掉就再也没法比较两版谁好。
--
-- 尺子（与 duanxian/day_returns.py 那两列同一把，不许另起）：
--   `gap_pct`/`hi_pct`/`cl_pct` 一律用**后复权价**（价 × coalesce(adjustment_factor,1)，同一天内同因子、
--   跨日连续），**不是**未复权的 `pct_change`；基准是信号日收价。原始价另留三列（base_close/next_*），
--   是为了让人能跟行情软件逐格对得上，但**判据与聚合只读后复权那三列**。
--   实测口径差（11-05 量）：1461 笔里只有 3 笔跨除权，raw 与后复权的均值差 <0.01 个百分点 ——
--   差得小不等于可以混着写：混了以后哪天多几笔派息就没人对得出来了。
--
-- 缺数纪律：判不出来写 NULL 并在 `missing` 里点名，**不写 0**（0 会被读成"平盘"，NULL 才是"没有"）。
--   隔日停牌/没有行 ⇒ 三个涨幅全 NULL + missing='next_row'，这条仍留在表里（它是样本的一部分，
--   统计侧按各自分母算，见视图里那句"分母各算各的"）。

create table if not exists analytics.outlook_pick_daily (
    trade_date      date        not null,               -- 信号日：那天 19:00 那一栏点出的名单
    next_date       date,                               -- 隔日场次（还没到就是 NULL）
    spec_version    text        not null,               -- 'outlook-pick-1'；判据改版换版本号，旧行留着
    section         text        not null,               -- 'hits'＝一、多指标靠前｜'trend'＝二、趋势最正
    security_code   text        not null,               -- '600001.SH' 形状，与 stock_daily 一致
    security_name   text,
    board           text,                               -- 主板/创业板/科创板（档位不同不互比）
    rank_in_section smallint,                           -- 榜内名次（1 起，按当时那一栏的排序）
    hits_today      smallint,                           -- 靠前榜：当日命中家族里几项
    days_in_top     smallint,                           -- 靠前榜：近 N 场里有几场命中（重复上榜）
    ma5_streak      smallint,                           -- 趋势榜：站上 5 日线的天数
    char_level      text,                               -- 股性档（strong/...）；选取时按 t10_char_keep 筛过
    in_both         boolean     not null default false, -- 两榜都进（交集单独一档，别当"更强信号"用）
    base_close      numeric(12,4),                      -- 信号日收价（原始，只对表用）
    base_close_hfq  numeric(16,4),                      -- 后复权基准价＝判据的分母
    next_open       numeric(12,4),                      -- 隔日开/高/收（原始）
    next_high       numeric(12,4),
    next_close      numeric(12,4),
    gap_pct         numeric(10,4),                      -- 后复权：隔日开盘 / 基准 − 1（百分数）
    hi_pct          numeric(10,4),                      -- 后复权：隔日最高 / 基准 − 1（"有没有冲高"）
    cl_pct          numeric(10,4),                      -- 后复权：隔日收盘 / 基准 − 1（"收红没有"）
    open_to_hi_pct  numeric(10,4),                      -- 可交易口径：隔日开盘买入 → 当日最高
    open_to_cl_pct  numeric(10,4),                      -- 可交易口径：隔日开盘买入 → 当日收盘
    missing         text,                               -- 哪格判不了与为什么（键的 jsonb 文本）
    params          text,                               -- 当时的参数快照（t10_* 那一族，jsonb 文本）
    algo_stamp      text,                               -- 被重算那个脚本的内容指纹（sha256 前 12）：口径漂移看得见
    calculated_at   timestamptz not null default now(),
    primary key (trade_date, spec_version, section, security_code)
);

create table if not exists analytics.outlook_baseline_daily (
    trade_date      date        not null,               -- 同一批信号场次
    next_date       date,
    spec_version    text        not null,
    n_stocks        integer,                            -- 这一场配对上的全市场票数（等权，不按市值）
    gap_pct         numeric(10,4),                      -- 下面六列与名单表同一把尺子（后复权）
    hi_pct          numeric(10,4),
    cl_pct          numeric(10,4),
    gap_win_pct     numeric(10,4),                      -- 胜率＝涨幅 > 0 的占比（百分数）
    hi_win_pct      numeric(10,4),
    cl_win_pct      numeric(10,4),
    calculated_at   timestamptz not null default now(),
    primary key (trade_date, spec_version)
);

comment on table analytics.outlook_pick_daily is
  '「⑩ 明日展望」两张短名单逐票逐场的隔日表现样本（策略回测屏那一块读它）。'
  '一行的含义是"那天那榜点了这只票，隔天它开/高/收相对当天收价动了多少"。';
comment on table analytics.outlook_baseline_daily is
  '同批场次的全市场等权基线（每天一行）。没有它，名单那句"平均冲高 4.7%"这三个字没有意义 —— '
  '大盘自己那天盘中均值就有 2.3%，差值才是这套判据的信息量。';

grant select on analytics.outlook_pick_daily, analytics.outlook_baseline_daily to ft_reader;
-- delete 是给"重算一段场次先清这一段这一版"的（与 61 那张表同一套授权）：
-- 只给 insert/update 的话，写侧第一步 delete 就 permission denied。
grant select, insert, update, delete on analytics.outlook_pick_daily to ft_app, ft_etl;
grant select, insert, update, delete on analytics.outlook_baseline_daily to ft_app, ft_etl;
