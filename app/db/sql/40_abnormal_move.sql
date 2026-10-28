-- 「异动提醒」的读数落库：偏离值累计 + 异常波动/严重异常波动两条公开监管判据 + 明日涨停的前瞻
--
-- 规则来源（沪深交易所《交易规则》5.4.x，2026-10-15 网上核对；本机只做客观复算，不代表交易所已公告）：
--   · 单日偏离值 = 个股当日涨跌幅 − **对应分类指数**当日涨跌幅。本机按板块各取一条官方基准：
--     沪主板 → 000002.SH Ａ股指数；深主板 → 399107.SZ 深证Ａ指；创业板 → 399102.SZ 创业板综；
--     科创板 → **000680.SH 科创综指**（⚠️ 不是 000688.SH 科创50 —— 名字差一个字，整列会算歪）。
--     指数当日涨跌幅本机**用收盘价现算**，不读 index_daily.pct_change：2026-10-15 实测那一列
--     在最近 28 场里缺 18 场，而窗口 sum() 会静默跳过 NULL ⇒ 累计偏离偏低却看不出来。
--   · 异常波动：连续 3 个交易日偏离值累计 ≥ ±20%（主板）/ ≥ ±30%（创业板、科创板）。
--   · 严重异常波动（任一）：① 连续 10 个交易日内 4 次（主板）/ 3 次（双创）同向异常波动；
--     ② 连续 10 个交易日内偏离值累计 ≥ +100% 或 ≤ −50%；③ 连续 30 个交易日内 ≥ +200% 或 ≤ −70%。
--
-- 本机做不到的一件事，必须写在屏幕上而不是藏起来：真实规则里「异常波动指标自交易所公告之日（或复牌之日）
-- 起重新计算」，而本机**没有公告日**这份数据 ⇒ 这里的计数是纯滚动窗口，同一段连续上涨会比交易所的口径
-- 更容易反复判成"踩到"。所以这一屏是「按公开规则算出来踩到了」，不是「交易所已认定为严重异常波动」。
--
-- 判据与写入：app/compute/abnormal_move.py（每日加工段步骤名 abnormalmove）。
-- 客观监管口径复算，不打分、不预测、不给买卖建议。

create table if not exists analytics.abnormal_move_daily (
  trade_date        date        not null,
  security_code     text        not null,
  spec_version      text        not null,

  board             text,                       -- 沪主板 / 深主板 / 创业板 / 科创板（北交所整行不写，理由见表注释）
  is_st             boolean,                    -- 当日 ST 档（按 reference_data.security_st_period 的档期，不是当前快照）
  close             numeric(16,6),
  pct_change        numeric(8,3),               -- 个股当日涨跌幅 %
  index_pct         numeric(8,3),               -- 对应分类指数当日涨跌幅 %（收盘价现算）
  dev_1d            numeric(8,3),               -- 当日偏离值 = 上两列之差（百分点）
  bars_used         int,                        -- 截止今天它一共有几根日线（新股/停牌复牌后不足窗口时判不了）
  listed_days       int,                        -- 上市后的第几个竞价交易日（≤5 不纳入异动指标计算，交易所明文）

  -- ── 累计偏离（三条窗口）── 口径随 spec_version 换过一把尺：abn-1 = 逐日相加（交易所"偏离值累计"
  --    的公开算法），abn-2 = 区间复利（个股区间涨幅 − 指数区间涨幅，行情软件那种对法）。
  --    两批并存于同一张表、靠 spec_version 分开，读侧永远只读当前那一版（见 duanxian/coverage.py）
  dev_3             numeric(10,3),              -- 连续 3 场累计偏离（百分点）；不足 3 根为 NULL
  dev_10            numeric(10,3),
  dev_30            numeric(10,3),
  dev_3_complete    boolean,                    -- 窗口够不够根数（false = 判不了，不是"没达到"）
  dev_10_complete   boolean,
  dev_30_complete   boolean,

  -- ── 异常波动（作为"同向次数"的原子）──
  ab_up             boolean,                    -- 今日这 3 日窗构成一次**向上**异常波动
  ab_down           boolean,                    -- 同上，向下
  up_cnt_10         int,                        -- 近 10 场内向上异常波动出现几次
  down_cnt_10       int,                        -- 近 10 场内向下几次

  -- ── 严重异常波动：今天踩没踩到 ──
  severe            boolean,                    -- 三条任一成立（且当日算得出来）
  severe_how        text,                       -- 踩到哪几条：times / dev10 / dev30（逗号分隔；没踩到 NULL）
  severe_dir        text,                       -- up / down —— 涨着踩到与跌着踩到是两件事，不合成一个数
  severe_reason     text,                       -- 没踩到的原因短码：none / bars_lt_window / new_listing_lt5 / no_index_pct / no_pct

  -- ── 前瞻：明日**涨停**会不会踩到严重异动（用户点名的那一栏）──
  band_next         numeric(6,4),               -- 明日涨跌幅限制（0.10 / 0.20 / 0.05；由当日 limit_up÷pre_close 反推，档位表 10-07 起停更）
  limit_price_next  numeric(16,4),              -- 明日涨停价 = 今收 × (1 + band_next)，四舍五入到分
  need_dev_to_100   numeric(10,3),              -- 距 +100 档还差几个偏离点（=100 − dev_10_pre，明日窗口去掉明日的那 9 场累计）；≤ band_next×100 即"明日涨停即触发"
  need_dev_to_200   numeric(10,3),              -- 距 +200 档还差几个点（=200 − dev_30_pre，去掉明日的 29 场累计）
  dev_10_if_limit   numeric(10,3),              -- 明日涨停后那个 10 日窗的累计偏离 = dev_10_pre + band_next×100（指数按 0% 假设，见屏上声明）
  dev_30_if_limit   numeric(10,3),
  trigger_pct       numeric(8,3),               -- 触发所需的最小涨幅（三条档各算各的缺口，取最小；> band_next×100 表示明天涨停也够不着）
  trigger_price     numeric(16,4),              -- 触发价 = 今收 × (1 + trigger_pct/100)
  hit_if_limit_up   boolean,                    -- 明日涨停即踩到严重异动（三条款任一被补足）。⚠️ 今天已踩到涨向严重异动的票这一列给 false：它问的是"明天新触发"，已触发的在"近期严重异动"那一块出现
  fwd_clauses       text,                       -- 明日涨停会补足哪几条：dev10 / dev30 / times（逗号分隔；没补足 NULL）
  dev_prev2         numeric(10,3),              -- **昨日起两场（T−1、T）的偏离之和**：明日那个 3 日窗 = 这两场 + 明日，缺这两场就没法判"次数档"会不会被凑够
  times_if_limit    boolean,                    -- 明日涨停 ⇒ 3 日窗累计偏离达阈值，即凑出下一次**同向异常波动**（主板第 4 次 / 双创第 3 次即踩严重异动）

  -- ── 事后：已踩到的那一天的次日读数（用户点名的"停牌/次日涨幅"）──
  next_trade_date   date,                       -- 指数在场的下一个交易日（停牌判定以它为锚）
  next_pct          numeric(8,3),               -- 次日涨幅；停牌或还没到 ⇒ NULL
  next_halted       boolean,                    -- 有证据的缺席：指数有 next_trade_date 这根线、它没有日线行 ⇒ 停牌/停牌核查
  next_missing      boolean,                    -- 次日整场缺席（含停牌；与 next_halted 同向，分开留是给回算看的）

  universe_hit      boolean,                    -- 进本表母体的哪一条（见下），没进母体就不写行
  universe_by       text,                       -- sealed10 / dev10_ge25 / dev30_ge60 / ab_flag / severe / forward_hit
  calculated_at     timestamptz default now(),

  primary key (security_code, trade_date, spec_version)
);

-- 本文件要能重复执行：表已存在时 create table if not exists 不会补列，所以新加的三列各带一条 alter。
-- ⚠️ alter 必须排在下面的 comment 之前 —— 否则 `comment on column` 会先于列的存在而执行，
--    报 "column ... does not exist"（10-15 实跑踩过：列加上了、注释全丢，而 psql 只是几行 ERROR）。
alter table analytics.abnormal_move_daily add column if not exists fwd_clauses     text;
alter table analytics.abnormal_move_daily add column if not exists dev_prev2       numeric(10,3);
alter table analytics.abnormal_move_daily add column if not exists times_if_limit  boolean;

comment on table  analytics.abnormal_move_daily is '异动提醒（每日加工段步骤 abnormalmove）：个股对**对应分类指数**的偏离值与累计偏离（3/10/30 场）、异常波动、严重异常波动三条公开监管判据的本机复算，外加"明日涨停会不会踩到严重异动"的前瞻与已踩到那天的次日涨幅/停牌。⚠️ 本机没有"交易所公告日"这份数据，指标不能按规则在公告日归零重算 ⇒ 这是"按公开规则算出来踩到了"，不是"交易所已认定"。北交所不纳入：北证50 在本机 1,080 场里只有 13 场有涨跌幅，偏离值算不出来。⚠️ 累计偏离那三列的口径随 spec_version 换过一把尺：abn-1 逐日相加、abn-2（2026-10-27）区间复利，两批并存不覆盖，读侧一律带版本条件';
comment on column analytics.abnormal_move_daily.index_pct       is '对应分类指数当日涨跌幅（%），由 index_daily 的收盘价现算（close ÷ 前一场 close − 1）。**不读 pct_change 列** —— 2026-10-15 实测那一列近 28 场缺 18 场，窗口 sum() 会静默跳过 NULL，累计偏离偏低却看不出';
comment on column analytics.abnormal_move_daily.dev_10          is '连续 10 个交易日的累计偏离（百分点）。**口径看 spec_version**：abn-1 = 日偏离值逐日相加（交易所"偏离值累计"的公开算法），abn-2 = 区间复利（个股这 10 场逐日复利的区间涨幅 − 对应分类指数同样复利的区间涨幅，10-27 用户点名换的这把，因为连板段两把尺差 20~30 个点、旧那把会漏报已触发的人）。窗口不足 10 根、或那 10 场里有哪天缺读数 ⇒ NULL 且 dev_10_complete=false，此时 +100 那条判不了，不算"没达到"';
comment on column analytics.abnormal_move_daily.up_cnt_10       is '近 10 场里"向上异常波动"（3 日累计偏离 ≥ +20% 主板 / ≥ +30% 双创）出现几次：主板 4 次、双创 3 次即构成严重异常波动的一条。⚠️ 真实规则里指标自公告日归零重算，本机没有公告日 ⇒ 纯滚动，连续上涨的票会比交易所口径更容易反复计到';
comment on column analytics.abnormal_move_daily.band_next       is '明日涨跌幅限制：由**当日** limit_up ÷ pre_close − 1 反推后归到最近档位 {0.05,0.10,0.20}。不用 reference_data.limit_price_daily —— 2026-10-15 实测那张表 10-07 起没有行（比日线晚 7 场），拿它算会把整列前瞻判成 NULL';
comment on column analytics.abnormal_move_daily.need_dev_to_100 is '距 +100% 档还差的**偏离点数** = 100 − dev_10_pre（基准是"明日那个 10 日窗去掉明日"的 9 场累计，不是今天看到的 10 场 —— 明天会把最早那场挤出去）。⚠️ 它是点数缺口，**不是明日涨幅**：涨幅看 trigger_pct，abn-2 复利口径下两者不相等（每涨 1 个点会把已涨的那段放大 (100+S)/100 倍 ⇒ trigger_pct = 本列 × 100 ÷ (100 + 个股那 9 场的区间涨幅)）。负数 = 这一档已经过了';
comment on column analytics.abnormal_move_daily.next_halted     is '次日**有证据的缺席**：指数在 next_trade_date 有这根线，而这只票没有日线行 ⇒ 停牌（严重异动后的停牌核查就是这种形状）。缺席不等于停牌一天，也可能数据缺 —— 所以同时留 next_missing 与 bars_used';
comment on column analytics.abnormal_move_daily.fwd_clauses     is '明日涨停会**新**补足哪几条严重异动条款（dev10 / dev30 / times，逗号分隔），与 severe_how 同一套短码；已经踩到的那几条不重复出现在这里，一条都没补足为 NULL。屏上那一格读的是 abnormal_view 拼好的 fwd_state_label（已踩到哪条 + 明日涨停踩到哪条），别在界面自己重算';
comment on column analytics.abnormal_move_daily.trigger_pct     is '明日要涨这么多个**点**才踩到还差着的那条档（三档各算各的、取最小者）。abn-2 复利口径下它 ≠ need_dev_to_100／need_dev_to_200 那个点数缺口：缺口 × 100 ÷ (100 + 个股区间已涨点数) 才是涨幅，已涨得越多的票要的涨幅越小。> band_next×100 = 明天涨停也够不着（不进"会触发"那一支，但已踩到某一条的票仍进榜，读 severe_how）';
comment on column analytics.abnormal_move_daily.trigger_price   is '触发价 = 今收 × (1 + trigger_pct/100)。⚠️ 明日指数按 0% 假设（这一条上屏，见 caveat index_assumed_zero）';
comment on column analytics.abnormal_move_daily.dev_10_if_limit is '明日涨停后那个 10 日窗的累计偏离（百分点）= dev_10_pre + 涨停幅度 × (100 + 个股那 9 场区间涨幅) ÷ 100 —— 个股那半边按复利放大，指数明日按 0% 假设。abn-1 那版这里是"缺口直接加涨幅"，两把尺差出来的数就是漏报的那批票';
comment on column analytics.abnormal_move_daily.hit_if_limit_up is '明日涨停会**新**踩到一条还没踩到的严重异动。null = 还有档没踩到却算不出来（新股／长停牌／档位取不到），不是"不会触发"。三条全踩过了才是 false';
comment on column analytics.abnormal_move_daily.dev_prev2       is '昨日起两场（T−1、T）的累计偏离（abn-2 = 这两场复利差，abn-1 = 两场日偏离值之和）。明日的 3 日窗 = 这两场 + 明日，所以**缺这一列就没法判"次数档"会不会被明日涨停凑够**（主板 4 次 / 双创 3 次）；不足两场为 NULL，判不了就说判不了';
comment on column analytics.abnormal_move_daily.times_if_limit  is '明日涨停 ⇒ 明日那个 3 日窗（T−1、T、T+1）累计偏离达到异常波动线（+20% 主板 / +30% 双创），**并且**明日这一场凑满第 N 次同向异常波动（主板 4 / 双创 3）。次数用的是"明日那个 10 日窗去掉明日"里的已发生次数 + 1（不是近 10 场次数 + 1 —— 明天会把今天窗口里最早那场挤出去）。它是 hit_if_limit_up 的三个入口之一，单列出来是为了让"差一次"这种状态看得见';
comment on column analytics.abnormal_move_daily.listed_days     is '上市后的第几个竞价交易日（日历窗口只取近 45 场，老票这里给的是"至少 45"，仅用于界面说"这票刚上市"；精确判前 5 日走 security_session_span.fifth_session）。交易所明文：无价格涨跌幅限制的股票（新股上市前 5 日等）不纳入异常波动指标计算 ⇒ 这一档不是"没踩到"，是"不算",severe_reason=new_listing_lt5';
comment on column analytics.abnormal_move_daily.universe_by     is '进母体的哪一条：近 10 场封过板 / 10 日累计偏离 ≥25 点 / 30 日 ≥60 点 / 有异常波动 / 已踩严重异动 / 明日涨停可触发。**没进母体的票整行不写** —— 本屏是提醒榜，不是全市场日线快照（实测 2026-10-15：母体约 400 只，全场 5,214 只）';
comment on column analytics.abnormal_move_daily.severe          is '今天踩到严重异常波动（三条任一）。⚠️ 判不了的情况**不算踩到**，但也不能算"没踩到"：窗口根数不够、上市前 5 场、当日指数涨幅取不到 ⇒ severe=NULL + severe_reason 说清是哪一条，false 只留给"三条件都不成立且都算得出来"';
comment on column analytics.abnormal_move_daily.severe_how      is '踩到哪几条：times（10 日内同向异常波动 4 次主板 / 3 次双创）、dev10（10 日累计偏离 ≥+100% 或 ≤−50%）、dev30（30 日 ≥+200% 或 ≤−70%），逗号分隔';
comment on column analytics.abnormal_move_daily.dev_30          is '连续 30 个交易日的累计偏离（百分点），口径随 spec_version 与 dev_10 同一把尺（abn-1 逐日相加 / abn-2 区间复利）。30 根窗口意味着**上市不足 30 场的票这一列是 NULL**，此时"30 日档"判不了 —— 与"没达到"是两件事';

create index if not exists abnormal_move_date_idx  on analytics.abnormal_move_daily (trade_date, spec_version);
create index if not exists abnormal_move_sev_idx   on analytics.abnormal_move_daily (trade_date, severe, dev_10 desc nulls last);
create index if not exists abnormal_move_fwd_idx   on analytics.abnormal_move_daily (trade_date, hit_if_limit_up, need_dev_to_100);
create index if not exists abnormal_move_code_idx  on analytics.abnormal_move_daily (security_code, trade_date desc, spec_version);

grant select, insert, update, delete on analytics.abnormal_move_daily to ft_etl;
grant select on analytics.abnormal_move_daily to ft_app, ft_reader;
