-- -*- coding: utf-8 -*-
-- 53_limit_up_pool_daily.sql
-- 首页「短线情绪 · 涨停与连板」那一块的**母体**落库：每一场谁触板了、谁封住了、
-- 连到第几板、几点首封、开过几次板、封单多深、题材串是什么。
--
-- 为什么要落这一张（用户 2026-10-26 点名）：这一块屏幕上的家数、梯队、封板率、炸板率、
-- 晋级率、连板清单，母体一直是**每次请求现抓东财 push2ex 四个池子**（`vr/market.py::_emotion`），
-- 库里只有补充列（几天几板／首板日大盘情绪／节点／概念）。所以页面一关就什么都没有：
-- "哪一天有哪些票封住、几点首封"这类事后要复盘的问题，一个也答不了；东财接口一封或改了口径，
-- 这一屏就整块空掉，而且**历史补不回来**（池子是取数时点快照）。
--
-- 落库后读侧只读这张表。三条口径上的决定，都是用户 2026-10-26 在两组实测数之间选的：
--   ① **家数与比率以库为准（本地日线自算）**，不再以东财池为准。同一场 2026-10-23 实测：
--      本地日线判出封住 84 只（严格口径 close 与当日档位相差 ≤0.005）、触板未封 19 只
--      （high 冲过档位但收盘没封住）、跌停 11 只；东财池给的是 82／12／6。
--      两边不是谁算错了，是**判据不同**：本地含北交所与 ST（同花顺整池 filter 是 HS,GEM2STAR，
--      实测池内 0 只北证；日线判出的 4 只只在本地：600187、600745、920091、920207），
--      而东财炸板池按"开板事件"数、本地按"收盘没封住的只数"。所以界面上必须同时念出两侧数，
--      见 `duanxian/limit_up_board.py` 的 caliber 那一段。
--   ② **首封/回封/开板次数优先本地分钟线自算**，源站那一份原样留着做对照（两列并存、各标出处）。
--      实测 2026-10-23：84 只封住股**全部**有 1 分钟行；本地自算首封与同花顺 first_limit_up_time
--      可比 80 只里 84% 落在 1 分钟内、中位差 +1.0 分（分钟收盘口径必然比秒级撮合晚一根）。
--      ⚠️ 分钟线只从 **2026-08-14** 起有（实测 stock_minute 的 202608/202609/202610 三个分区
--      有行、202607 及更早为空；10-20/10-21 两天整天没采到）。所以更早的场次这三列只能是 null，
--      界面显示 `—` 并说明"这一场没有分钟线"，**不拿秒级源站值冒充自算、也不拿 09:25 兜底**。
--   ③ 缺数一律 NULL + 在 `missing` 里点名，**不写 0**：0 在这里是真实读数（"开板 0 次"＝从头封到尾），
--      null 才是"这一格判不了"。

create table if not exists analytics.limit_up_pool_daily (
    security_code     text        not null,
    trade_date        date        not null,
    spec_version      text        not null,

    -- 一、母体与当日读数（本地日线，全史可算）
    touched           boolean     not null,          -- 盘中冲过当日档位（high ≥ 档位 − 0.005）
    sealed            boolean     not null,          -- 收盘还封在档位上（|close − 档位| ≤ 0.005）
    limit_up_price    numeric(12,4),                 -- 当日涨停档位（未复权，来自 stock_daily.limit_up）
    close             numeric(12,4),
    pct_change        numeric(10,4),                 -- 当日涨幅 %（未复权原值）
    turnover_pct      numeric(10,4),
    amount_yuan       numeric(24,2),                 -- 当日成交额（元）
    consec            smallint,                      -- 连板数：**连续**封住的场次（含本场）；触板未封 ⇒ null
    streak_start      date,                          -- 这一轮最早那一板的日子

    -- 二、盘中时刻（本地 1 分钟线自算，只有 2026-08-14 起）
    first_seal_at     time,                          -- 第一根"收盘仍在档位上"的分钟
    last_seal_at      time,                          -- 最后一根仍在档位上的分钟（没回封过就等于首封）
    reopen_count      smallint,                      -- 开板次数：从档位上掉回档位下的次数
    at_limit_minutes  smallint,                      -- 封在档位上的分钟根数（240 ＝ 一字封死）
    minute_bars       smallint,                      -- 这只票本场共有几根 1 分钟线（0/null ⇒ 这一场没分钟数据）

    -- 三、源站那一组（同花顺涨停揭秘整池；加工段 fbdaily 每天本来就要拉这一遍，同一次出网）
    src_has_row       boolean,                       -- 池子里有没有这只票（北交所/ST 必然没有 ⇒ false 不是异常）
    src_first_seal_at time,
    src_last_seal_at  time,
    src_reopen        smallint,                      -- 源站 open_num
    src_high_days     text,                          -- 源站「6天6板」原文，只用来对账
    order_amount      numeric(24,2),                 -- 封单额（元）—— 分钟线推不出，只有源站给
    order_volume      numeric(20,2),
    board_kind        text,                          -- 板型：换手板／一字板／T 字板
    float_cap_yuan    numeric(24,2),                 -- 流通市值（元），按下面 float_cap_basis 那把尺
    float_cap_basis   text check (float_cap_basis in ('source','daily_replay')),
    reason            text,                          -- 题材串（源站编辑归因、非公司确认）
    reason_source     text,                          -- 出处：ths_limit_up_pool / iwencai / null
    reason_captured_at timestamptz,

    -- 四、这一行的出处与记账
    seal_basis        text check (seal_basis in ('local','source','none')),
    missing           jsonb       not null default '{}'::jsonb,   -- 键名＝上面列名，值＝为什么缺
    calculated_at     timestamptz not null default now(),

    constraint limit_up_pool_daily_pkey primary key (security_code, trade_date, spec_version)
);

create index if not exists limit_up_pool_daily_day_idx
    on analytics.limit_up_pool_daily (spec_version, trade_date desc, sealed desc, consec desc);

-- 这一列是 2026-10-26 第一次实跑之后补的（表已建、0 行）：留着这条 alter，
-- 已经建过旧版表的环境重跑本文件也能追平，而不是只有新库才对。
alter table analytics.limit_up_pool_daily
    add column if not exists float_cap_basis text check (float_cap_basis in ('source','daily_replay'));

comment on table  analytics.limit_up_pool_daily is
  '首页「涨停与连板」的母体：一行 = (这只票, 这一场, 口径版本) 的触板/封板读数。'
  '判据只住 app/compute/limit_up_pool.py 一处，读侧 duanxian/limit_up_board.py 只读行不重算。'
  '母体是**当日触过板的全部**（含收盘没封住的那些），所以炸板率的分母在这里，不在封住行里。';

comment on column analytics.limit_up_pool_daily.touched is
  '盘中冲过当日档位：high ≥ limit_up_price − 0.005。它是这张表每一行的存在理由 —— '
  'touched=true 且 sealed=false 就是"炸板"（收盘没封住）。';

comment on column analytics.limit_up_pool_daily.consec is
  '连板数：**连续**封住的场次个数，含本场。与「几天几板」（duanxian/board_span.py，允许中间断）'
  '是两把尺子，屏幕上也是两列，别合并成一件。sealed=false 的行没有连板可言 ⇒ null 不是 0。';

comment on column analytics.limit_up_pool_daily.first_seal_at is
  '本地 1 分钟线自算：第一根 close ≥ limit_up_price − 0.005 的分钟（Asia/Shanghai 钟表时间）。'
  '09:25/09:30 那批是集合竞价撮出来的一字，"早"不等于早盘承接强（界面上那一列必须注明）。'
  '分钟线只从 2026-08-14 起有 ⇒ 更早的场次与 10-20/10-21 那两天是 null，界面显示 —。';

comment on column analytics.limit_up_pool_daily.order_amount is
  '封单额（元），**只有源站给**（排队买一在分钟线里看不出来）。它是封板强度三输入之一，'
  '缺了不扣成 0 分：duanxian/seal_strength.py 把这一项从分母里摘掉、权重在其余项上归一。';

comment on column analytics.limit_up_pool_daily.src_has_row is
  '同花顺整池里有没有这只票。false 是常态而不是异常：池 filter=HS,GEM2STAR 不含北交所，'
  '实测也基本不含 ST（2026-10-23 本地判出 84 只、池内 80 只，差的正是 2 只北证 + 2 只沪市）。';

comment on column analytics.limit_up_pool_daily.seal_basis is
  '屏幕上"首封"那一格实际用的是哪一把尺：local＝本地分钟线，source＝没有分钟线时退回源站秒级值，'
  'none＝两边都没有。这一列必须跟着上屏，否则同一个列名底下混两把尺，事后无从分开。';

comment on column analytics.limit_up_pool_daily.missing is
  '逐列记「这一格为什么判不了」（键名＝列名）。缺数交 null 并在这里点名，界面上那句「这一场没有分钟线」'
  '就是读它拼出来的，不是页面自己猜的。';

comment on column analytics.limit_up_pool_daily.float_cap_basis is
  '流通市值用的是哪把尺：source＝源站 currency_value 原值；daily_replay＝当日日线反推'
  '（100 × 成交额 ÷ 换手率，与 analytics.sector_flow_trend 的 turnover 分母同一条）。'
  '池子不含北交所/ST，那批票只能走 daily_replay —— 不标出来就等于屏幕上一列底下混两把尺。';

grant select                 on analytics.limit_up_pool_daily to ft_reader;
-- delete 是给"重算一场先清这一场"的（与 49_first_board_daily 同一套授权）：
-- 只给 insert/update 的话，写侧第一步 delete 就 permission denied。
grant select, insert, update, delete on analytics.limit_up_pool_daily to ft_app, ft_etl;
