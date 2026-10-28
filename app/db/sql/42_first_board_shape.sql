-- -*- coding: utf-8 -*-
-- 42_first_board_shape.sql
-- 「首板形状预示」：把**每一个连板段的第一天**（首板日，段长≥1）连同它启动前 30 根的量能形状、
-- 当天的环境读数、以及"后来走到几板 / 次日开盘进场能不能赚"一起落库。
--
-- 为什么要这么一张表（与 26_startup_volume_profile 的分工，别在后面被人合并掉）：
--   · 26 那张表的母体是 `len >= 4`，那是**回头看**才成立的条件 ⇒ 它的"启动后收益"带未来函数
--     （《倍量形态.md》§8 实测：同一批样本，复盘口径 5 场 +32.22%，能买到口径 −6.31%、胜率 37%）。
--   · 本表的母体是**当天就能构造**的事件：那天收在涨停价上、且上一场不是连板段的一部分。
--     于是 `seg_len`（走到几板）在这里是**结果变量**，不是筛选条件 —— 这一列差才能回答
--     用户 2026-10-16 点名的那句「启动前什么形状预示能走到 4 板」。
--   · 同一件事还给了第二个结果：`entry_*`/`r*`/`ex*` 是**首板次日开盘进场**（真买得到的价位）之后
--     1/3/5/10 场的绝对与超额收益，`entry_locked` 标出"次日一字、根本买不进"那 3.1%。
--
-- 五条读数口径（写在列旁边，免得下一个人重新踩）：
--   ① 涨停判定 ＝ `close >= limit_up - 0.01`（当日档位由 `app/compute/limit_prices.py` 定），
--      连板段的岛号用**未过滤日线序列**的 `bar_no` 去减 —— 用过滤后的行号会把
--      `limit_up IS NOT NULL` 那 1.2 万行"没有涨停价"的日子接成一段假连板（26 那边同一课）。
--   ② 启动前倍数读 `analytics.volume_ratio_daily`（vr-1，10-16 已回补到 2021-08-02 全史）；
--      窗口不足 30 根就存实际根数 `bars`，**不补齐、不填 1**。
--   ③ 价格一律后复权：`价 × coalesce(adjustment_factor,1)`（同 `duanxian/day_returns.py:76`）。
--      超额基准按号段配对应分类指数（沪主板 000002.SH / 深主板 399107.SZ / 创业板 399102.SZ /
--      科创板 000680.SH，与 `app/compute/abnormal_move.py:86` 同一张表）；**基准起点＝进场当日该指数的开盘**
--      （fb-2；fb-1 用的是当日收盘 ⇒ `ex1` 恒等于 `r1`，那一列写着"超额"却不含任何基准，其余 ex* 统一少算一站）；
--      **北交所没有对应指数** ⇒
--      `bench_code` 为 NULL、`ex*` 为 NULL，只报绝对收益，不许拿上证指数冒充。
--   ④ 财务三列取"启动日往前 60 天已披露"的那一期（`reference_data.stock_financial_period` 只有报告期、
--      没有披露日 ⇒ 60 天是保守滞后补偿）。宁可取到旧一期，也不许用未来的数。
--   ⑤ 段还没走完的日子（末板日＝库里最新一场，之后还没出现过非涨停日）`seg_complete = false`：
--      这种行的 `seg_len` 是**下界**，读侧算"走到几板率"时必须要么剔掉、要么把它算进"还在走"。
--
-- 主键带 spec_version（全项目约定：改判据是新写一批行，历史那批留着对账）。

create table if not exists analytics.first_board_shape (
  security_code      text          not null,
  launch_date        date          not null,       -- 首板日（该连板段的第一天）
  spec_version       text          not null,
  name               text,
  board              text,                         -- 主板 / 创业板 / 科创板 / 北交所（来自 reference_data.security）

  -- 结果一：后来走到几板
  seg_len            smallint      not null,       -- 段长（已知部分；seg_complete=false 时是下界）
  seg_complete       boolean       not null default true,
  last_board_date    date          not null,       -- 这一段最后一天收在涨停价上的日子

  -- 结果二：首板次日开盘进场（entry）之后的走势，全部后复权、单位是百分数
  entry_date         date,                         -- 首板日之后第一场
  entry_open         numeric(20,6),                -- 那一场的开盘价（后复权），进场参考价
  entry_locked       boolean       not null default false,   -- 那一场一字板（high==low）＝买不进
  r1                 numeric(12,4),
  r3                 numeric(12,4),
  r5                 numeric(12,4),
  r10                numeric(12,4),
  maxup5             numeric(12,4),                -- 进场后 5 场内最高价相对进场价
  low5               numeric(12,4),                -- 进场后 5 场内最低价相对进场价
  bench_code         text,                         -- 对应分类指数（北交所为 NULL）
  ex1                numeric(12,4),
  ex3                numeric(12,4),
  ex5                numeric(12,4),
  ex10               numeric(12,4),

  -- 启动前的量能形状（只用首板日之前的根）
  bars               smallint      not null,       -- 实际拿到几根倍数（<30 就是不满窗）
  dbl_days           smallint      not null,       -- 倍数 ≥2.0 的天数
  near_dbl_days      smallint      not null,       -- 1.6 ≤ 倍数 < 2.0
  calm_days          smallint      not null,       -- 倍数 < 0.6
  last10_quiet       smallint      not null,       -- 最近 10 根里倍数 < 0.6 的天数
  r_avg              numeric(12,4),
  r_med              numeric(12,4),
  r_p80              numeric(12,4),
  r_p90              numeric(12,4),
  r_max              numeric(12,4),
  r_std              numeric(12,4),                -- 量能抖不抖（与"有没有放量"是两回事）
  last5_avg          numeric(12,4),
  base25_avg         numeric(12,4),
  pre5               numeric(12,4),                -- 最后 5 根均值 ÷ 前 25 根均值＝启动前夜有没有抢跑

  -- 首板日当天的读数
  launch_pct         numeric(12,4),                -- 当天涨幅（未复权的原始 pct_change）
  launch_turnover    numeric(12,4),                -- 当天换手率
  launch_amount_yuan numeric(20,2),
  launch_is_1word    boolean       not null default false,   -- 首板当天就一字（次日更难买到）

  -- 当天的大盘环境（全市场同一场的那个值，只能读环境、没有横截面意义）
  limit_up_real      smallint,
  breadth_score      numeric(8,4),
  theme_score        numeric(8,4),
  breadth_level      text,
  theme_level        text,

  -- 当时已披露的财务（60 天滞后补偿；缺就 NULL，不填 0）
  fin_report_date    date,
  rev_yoy            numeric(20,4),
  np_yoy             numeric(20,4),
  gross_margin       numeric(20,4),
  roe                numeric(20,4),

  calculated_at      timestamptz   not null default now(),
  primary key (security_code, launch_date, spec_version)
);

comment on table analytics.first_board_shape is
  '首板形状预示：每个连板段首日的启动前量能形状 ＋ 走到几板（结果）＋ 次日开盘进场的绝对/超额收益。与 startup_volume_profile（母体是 len≥4，带未来函数）分工不同，别合并';
comment on column analytics.first_board_shape.seg_len      is '这一段连续涨停的天数。seg_complete=false 时它是下界（段还在走），算"走到 N 板率"要么剔这些行、要么单列一档';
comment on column analytics.first_board_shape.entry_locked is '首板次日一字板＝开盘买不进。占比约 3.1%（10-16 五年实测），聚合时"剔掉买不进"要另交一列，不许混进胜率';
comment on column analytics.first_board_shape.r5           is '进场（entry_date 开盘）后第 5 场收盘的绝对涨幅（后复权，百分数；对应的超额在 ex5）';
comment on column analytics.first_board_shape.ex5          is '同一场相对对应分类指数的超额；基准起点＝进场当日该指数的开盘（fb-2，与「次日开盘进场」同一时刻）。北交所无对应指数 ⇒ NULL，只读 r*';
comment on column analytics.first_board_shape.pre5         is '启动前最后 5 根均值 ÷ 前 25 根均值。>1 ＝启动前夜已经在放量抢跑';
comment on column analytics.first_board_shape.r_std        is '启动前 30 根倍数的样本标准差：衡量抖不抖，与 dbl_days（有没有倍量日）是两回事';
comment on column analytics.first_board_shape.fin_report_date is '财务三列所依据的报告期（启动日往前 60 天内已披露的那一期）';

create index if not exists first_board_shape_date_idx
  on analytics.first_board_shape (spec_version, launch_date desc);
create index if not exists first_board_shape_code_idx
  on analytics.first_board_shape (security_code, launch_date desc);

grant select, insert, update, delete on analytics.first_board_shape to ft_etl, ft_app;
grant select                          on analytics.first_board_shape to ft_reader;
