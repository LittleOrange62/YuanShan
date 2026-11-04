-- -*- coding: utf-8 -*-
-- 61_stock_character_daily.sql
-- 「股性」与「近 10 场日均振幅」落库：一行 = (这只票, 这一场, 口径版本) 的三条腿读数 + 当日全场分位 + 一个档位。
--
-- 为什么单独落一张（用户 2026-11-03 点名，看的是突破信号页左栏「埋伏票·左侧机会」）：
-- 那一屏原先只有 `range20_pct`（20 日**箱体有多大**），回答不了两个短线最关心的事——
--   ① 埋伏对了，这一天摆动够不够吃肉（＝日均振幅）；
--   ② 这只票历史上到不到位得了板（＝涨停基因）。
-- 两张屏（埋伏票榜、首板分析）都要用"涨停封住/触及了几次"这一个数，所以那把尺**只许有一处实现**
-- （`app/compute/limit_gene.py`），这张表落它的 250 场窗口，首板榜落它自己的 63/126/489 场窗口。
--
-- 四条写在这里就定死的口径（都进列注释，界面从注释取字段义，组件里不抄第二份）：
--   ① **窗口按市场场次取起点**（`since = 第 N 个最近场次`），不是按这只票自己的根数：
--      停牌那天它本来就没有行，"近 10 场"要说的是全市场的 10 场，否则长期停牌的票会拿半年前的
--      10 根冒充"近 10 场"。真有的行数一并交出（`bars_10`/`bars_250`），不足门槛就不给数。
--   ② **日均振幅＝逐日 (high − low) ÷ 前收盘 × 100 取算术平均**（不是收盘价涨跌幅、不是复权价）。
--      分母 `pre_close` 用的是当日档位同源的未复权原值；`pre_close ≤ 0` 或 high/low 缺 ⇒ 那一天不进分子分母。
--      ⚠️ 它是**事后描述量**，不是预测量：高振幅里既有连板王也有一地鸡毛，屏上那句必须原样念出来。
--   ③ **涨停基因两把尺并存**（封住 / 触及），与首板榜、涨停池同一判据（同一份实现、同一个 eps）。
--      只留一个数就会把"冲板没封住"那批藏掉（本机实测同一场触板 103 对 封住 84）。
--      一只票近 250 场从没触过板 ⇒ `seal_250 = 0`、`touch_250 = 0`，**这是真实读数不是缺数**；
--      缺（判不了）只发生在窗口行数不足或档位价缺失，那种一律 NULL 并在 `missing` 里点名。
--   ④ **档位只由三条腿的当日全场分位取中位数得到**，不给加权总分：
--      三腿分位的中位数 ≥ 70 记 strong、40~70 记 middle、< 40 记 weak；
--      任一条腿是 NULL ⇒ 整档 NULL（不拿两腿硬凑一个"看起来像"的档）。
--      中位数写死在这里，是为了"某一条腿特别差"不被另外两条抬上去（那正是加权分被质疑过的老问题）。

create table if not exists analytics.stock_character_daily (
    security_code     text        not null,
    trade_date        date        not null,
    spec_version      text        not null,

    -- 一、近 10 个市场场次的日均振幅（用户点名的那一列，快变量）
    amp_10            numeric(8,3),                  -- 逐日振幅 % 的算术平均；判不了 ⇒ NULL
    amp_10_pctile     numeric(8,2),                  -- 当日全场分位 0~100（同分同名，缺数不占号）
    bars_10           smallint    not null default 0,-- 窗口里真有的天数（分母），不足门槛就是判不了

    -- 二、近 250 个市场场次的三条腿（慢变量，"股性"说的是这个）
    amp_250           numeric(8,3),
    amp_250_pctile    numeric(8,2),
    turn_250          numeric(10,4),                 -- 日均换手率 %（原值，来自 stock_daily.turnover_rate_percent）
    turn_250_pctile   numeric(8,2),
    seal_250          smallint,                      -- 封住口径：收盘贴在涨停档上的天数（0 是真的没有）
    touch_250         smallint,                      -- 触及口径：盘中冲过档位的天数（含后来炸开的）
    consec_max_250   smallint,                       -- 最高连板（读 lup-1 的 consec，见下注）
    gene_pctile       numeric(8,2),                  -- 涨停基因分位：按 seal_250 排（触板多而封不住不算基因好）

    -- 三、档位（三腿分位取中位数；NULL＝判不了，不是"弱"）
    char_mid_pctile   numeric(8,2),                  -- 那个中位数本身，屏上悬停要能看见它是按三条腿算的
    char_level        text check (char_level in ('strong','middle','weak')),
    char_level_label  text,                          -- 中文档位名，只由写侧交（界面不自己翻译）

    bars_250          smallint    not null default 0,
    missing           jsonb       not null default '{}'::jsonb,   -- 键名＝上面列名，值＝为什么判不了
    calculated_at     timestamptz not null default now(),

    constraint stock_character_daily_pkey primary key (security_code, trade_date, spec_version)
);

create index if not exists stock_character_daily_day_idx
    on analytics.stock_character_daily (spec_version, trade_date desc, amp_10 desc);

comment on table  analytics.stock_character_daily is
  '股性（慢变量）与近 10 场日均振幅（快变量）：一行 = (这只票, 这一场, 口径版本)。'
  '判据只住 app/compute/stock_character.py 一处，其中涨停封住/触过的次数调 app/compute/limit_gene.py（与首板分析共用同一件实现）。'
  '读侧 duanxian/quant.py::ambush() 只做左连接与出口清洗，不重算分位、不自己定档。';

comment on column analytics.stock_character_daily.amp_10 is
  '近 10 个市场场次里逐日振幅 (high − low) ÷ pre_close × 100 的算术平均（%）。'
  '窗口起点＝全市场倒数第 10 场，不是这只票自己的 10 根（长期停牌的票否则会拿半年前的行冒充"近 10 场"）。'
  '停牌缺行的那天不进分母，真有的天数交在 bars_10；不足门槛 ⇒ NULL 并在 missing 点名，不补 0、不拿更少天数硬算。'
  '⚠️ 事后描述量：它说"最近摆动多大"，不说"明天会涨多少"。';

comment on column analytics.stock_character_daily.amp_10_pctile is
  '当日全场横截面分位（0~100，"有多少只票比它小或相等"÷ 有值的只数 ×100）；同分同名，缺数不占号。'
  '母体是当日有这一列读数的票，不是当日全部有行情的票——榜上那句"全场"要按这个母体说。';

comment on column analytics.stock_character_daily.seal_250 is
  '近 250 个市场场次里收盘还封在涨停档上的天数（close ≥ 档位 − eps，eps 与首板榜、涨停池同一个常量）。'
  '0 ＝这 250 场一次都没封住（真实读数）；NULL ＝判不了（窗口行数不足或那几天的档位价缺失），在 missing 里点名。';

comment on column analytics.stock_character_daily.touch_250 is
  '近 250 个市场场次里盘中冲过涨停档的天数（high ≥ 档位 − eps，含后来炸开的）。'
  '与 seal_250 是两把尺：本机实测同一场触板 103 只对封住 84 只，只留一个数就会把"冲板没封住"那批藏掉。';

comment on column analytics.stock_character_daily.consec_max_250 is
  '近 250 场里最高的那一轮连板（第几板）——读 analytics.limit_up_pool_daily 的 consec（spec lup-1、只数封住的行）。'
  '这里再数一遍就会有两把尺：停牌那天算不算断、ST 档位容差取几分，两处各判一次必分叉。'
  '⚠️ NULL 有两种，别混：这段时间没有过可数的连板轮次（含从没封住板）是真实读数、不进 missing；'
  '只有整批读不到库（lup_rows_unavailable）才点名——"没读到"不能写成"没有连板"。';

comment on column analytics.stock_character_daily.gene_pctile is
  '涨停基因的分位，按 seal_250 排（不是 touch_250）：只冲板封不住的票不算基因好。'
  '封住次数完全相同的票同分同名，落在同一个分位上，不因为代码顺序被拆开。';

comment on column analytics.stock_character_daily.char_level is
  '股性档位：三条腿（涨停基因 / 摆动性 / 钱活跃度）的当日全场分位取中位数，'
  '≥70 strong、40~70 middle、<40 weak；任一条腿判不了 ⇒ 整档 NULL（不拿两条腿硬凑一个看起来像的档）。'
  '取中位数而不是加权总分，是为了让"一条腿特别差"能留在结论里——加权会把那个短板抹平。';

comment on column analytics.stock_character_daily.char_level_label is
  '档位的中文名，只由写侧交（strong→「股性强」…）：中文与键的对应关系不许在界面里再抄一份。';

comment on column analytics.stock_character_daily.missing is
  '哪几列判不了与为什么（键＝列名）。缺数一律 NULL + 在这里点名：屏上 `—` 的那一格必须能在悬停里读到原因，'
  '否则"没有振幅"会被读成"这只票不活跃"。';

grant select                 on analytics.stock_character_daily to ft_reader;
-- delete 是给"重算一场先清这一场这一版"的（与 53_limit_up_pool_daily 同一套授权）：
-- 只给 insert/update 的话，写侧第一步 delete 就 permission denied。
grant select, insert, update, delete on analytics.stock_character_daily to ft_app, ft_etl;
