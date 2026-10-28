-- -*- coding: utf-8 -*-
-- 43_startup_quadrant_track.sql
-- 「启动前 30 场四维象限轨迹」：把每一类启动母体在**启动日之前那 30 场**的四维象限读数
-- （分数、全市场名次、象限位置）压成一行轨迹。用户 2026-10-17 的原话：
--   「你把这几类票前 30 个交易日的四维象限分数、名次、象限位置变化情况分析一下」。
--
-- 与 42_first_board_shape.sql / 26_startup_volume_profile.sql 的分工（别在后面被人合并掉）：
--   · 26 那张表回答「启动前量是怎么放的」，母体是 len>=4（回头看才成立，带未来函数）；
--   · 42 那张表回答「启动前什么形状预示走到几板」，母体是当天可构造的首板事件；
--   · 本表回答的是**另一根轴**：启动前那 30 场里，这只票在四维象限坐标系里是怎么挪的
--     （分数往上走没有、名次往上爬没有、象限换了几次、最后一次离启动日多远）。
--     一行 = (票, 启动日, cohort, capability_key)，所以每一行是「一条轨迹」而不是一个读数。
--
-- 数据源与三条实测事实（2026-10-17 现查 analytics.score）：
--   ① `quadrant` 只在 capability_key='oper_score' 的行上有值（其余七键恒为 NULL 是**设计**，
--      见 app/compute/scoring.py 与 duanxian/quant.py 那条纪律）。⇒ 本表里 quad_* / q1_days..q4_days /
--      quad_switches / quad_last_switch_gap **只有 oper_score 那一行有值**，其余六个键**必须是 NULL、
--      不许写 0**（0 会被读成「一次都没换过象限」，而那六个键根本没有象限这回事）。
--   ② market_mood 这一族的值是「同一天全市场同一个数」（实测 rank_in_market 恒为 1、percent_rank 恒为
--      50.0090）⇒ 它的「名次 / 分位」没有横截面意义，**这一族键不进本表**（这是显式排除，不是漏采；
--      读侧 caveat 与模块 TRACK_KEYS 同一口径）。进来的七个键：oper_score、temp_score、vol_temp、
--      sector_heat、stock_quality、env_resonance、vol_stage。
--   ③ vol_stage 的 value 是**档位**（1 冷却 / 2 酝酿 / 3 高潮 / 4 背离 / 5 退潮，config.capability 原文），
--      不是连续分数 ⇒ 这一行读的是「档位差」（value_delta=+2 ＝从酝酿走到高潮），把它读成百分比就是错。
--   ④ analytics.score 里 is_stale=true 的那 227,985 行是**旧算法**迁入批（列注释原话：算法已改，
--      不能与新结果混着比；实测它横跨 2026-01-27..2026-07-27 共 119 场，且 quadrant 全为 NULL）。
--      ⇒ 本表只取 is_stale=false 的那一批（2026-07-29 起 47 场）：窗口里混进旧算法会把 value_delta /
--      rank_delta 变成「旧算法减新算法」，那一列差正是本表的卖点，混了就不是数错一格而是整句反了。
--      代价写在读侧：满窗（30 场）只可能在启动日足够靠后时才成立，窗口不满是**覆盖事实**不是缺陷。
--
-- 窗口口径（八条，写进表注释与列注释，免得下一个人重新踩）：
--   · 窗口 = 启动日**之前** window_days 个该票自己的有效交易日（不含启动日当天：当天已经涨停，
--     分数被启动本身污染，把它算进「启动前」等于自己解释自己）。
--     「一场」= 该票该键在 analytics.score 里有读数的那一天，所以各键的场次起点不同、bars 也不同。
--   · win_from = 窗口里最早那一场，win_to = 紧邻启动日的那一场（T-1）。
--   · first = 窗口最早那一场的读数，last = T-1 那一场的读数；value_delta = last - first。
--   · rank_delta = rank_first - rank_last。⚠️ **正数 = 名次上升**（rank 越小越靠前，
--     第一场 3000 名、T-1 场 200 名 ⇒ 3000-200 = +2800 是往上爬）。这一条不写死，所有人都会读反。
--   · rank_best / rank_worst = 窗口内的最小 / 最大名次（同一天有多只票同名次时 rank(method='min') 的语义）。
--   · pct_first / pct_last 照 analytics.score.percent_rank 原样存：库里列注释写「0~1」，
--     **实测刻度是 0~100**（min 0.0180 / max 100.0000，scoring.py 那一条 `rank(pct=True)*100`）⇒ 按百分数读。
--   · quad_switches = 窗口内**相邻两场象限不同**的次数（只有两边都取到象限时才比，NULL 参与不算一次翻转）；
--     quad_last_switch_gap = 最后一次翻转那一场合到 T-1 之间隔了几场（0 = 翻转就发生在 T-1，
--     一次都没翻转 = NULL，不是 0）。
--   · q1_days..q4_days = 窗口内各象限的场数（同一键各档相加 <= bars，因为可能有象限为空的场次）。
--   · 缺就是 NULL，任何一列都不许用 0 顶（0 在这张表里是一个真实读数：第 0 名、0 次翻转、0 分都是数）。
--
-- 结果两列（把「后来走到几板」带进同一行，省掉读侧再回表）：
--   · outcome_len = analytics.first_board_shape（spec fb-2，同 (security_code, launch_date)）的 seg_len；
--     fb-2 里没有那一行（例如 jump40 的触发日不是任何一个连板段的第一天）就是 NULL。
--   · outcome_gain4 = 母体表 analytics.startup_volume_profile（sv-2，同 (code, launch_date, cohort)）的
--     trigger_gain —— 注意 fb-2 那张表**没有** trigger_gain 这一列，它是 sv-2 的列；单位是**小数比例**
--     （0.4641 = +46.41%），与 fb-2 那些按百分数存的 r*/ex* 不是一把尺子。
--
-- 主键带 spec_version（全项目约定：改判据是新写一批行，历史那批留着对账）。

create table if not exists analytics.startup_quadrant_track (
  security_code         text          not null,
  launch_date           date          not null,       -- 启动日（母体 sv-2 的那一段第一天 / 触发日）
  cohort                text          not null,       -- board4 / board4_45 / board4_or_gain45 / jump40
  capability_key        text          not null,       -- 七个键之一（market_mood 不进表，见头部 ②）
  spec_version          text          not null,
  name                  text,
  board                 text,
  window_days           smallint      not null,       -- 本次跑的窗口（10/20/30），是判据的一部分
  bars                  smallint      not null,       -- 实际拿到几场（<window_days 就是不满窗）
  win_from              date,                         -- 窗口最早那一场
  win_to                date,                         -- 紧邻启动日那一场（T-1）
  value_first           numeric(12,4),
  value_last            numeric(12,4),
  value_delta           numeric(12,4),                -- last - first（vol_stage 那一行是档位差）
  value_med             numeric(12,4),                -- 窗口内中位（与首末两读数并看，防单点噪声）
  rank_first            int,
  rank_last             int,
  rank_delta            int,                          -- rank_first - rank_last，正数＝名次上升
  rank_best             int,                          -- 窗口内最小名次（最靠前到过哪）
  rank_worst            int,                          -- 窗口内最大名次
  pct_first             numeric(8,4),                 -- 照库里的 percent_rank 原样存，实测刻度 0~100
  pct_last              numeric(8,4),
  quad_first            text,                         -- 以下七列只在 capability_key='oper_score' 有值
  quad_last             text,
  quad_switches         smallint,                     -- 相邻两场象限不同的次数
  quad_last_switch_gap  smallint,                     -- 最后一次翻转距 T-1 几场（0＝就在 T-1；没翻转＝NULL）
  q1_days               smallint,
  q2_days               smallint,
  q3_days               smallint,
  q4_days               smallint,
  outcome_len           int,                          -- fb-2 的 seg_len（走到几板；fb-2 没那行＝NULL）
  outcome_gain4         numeric(8,4),                 -- sv-2 的 trigger_gain，小数比例（0.4641＝+46.41%）
  calculated_at         timestamptz   not null default now(),
  primary key (security_code, launch_date, cohort, capability_key, window_days, spec_version)
);

comment on table analytics.startup_quadrant_track is
  '启动前 N 场四维象限轨迹：一行 = (票, 启动日, cohort, capability_key)，交那 N 场首末读数、名次变化、象限翻转。'
  '窗口不含启动日当天；只取 analytics.score 里 is_stale=false 的那一批（旧算法迁入批不可混比）；'
  'quad_* 与 q1_days..q4_days 只在 oper_score 行有值，其余六键恒为 NULL 是设计不是缺数；'
  'market_mood 一族（同日全市场同一个数）不进本表';

comment on column analytics.startup_quadrant_track.window_days is
  '本次跑的窗口场数（10/20/30，默认 30）。它与 bars 一起决定这一行说的是哪一段：bars<window_days 就是不满窗';
comment on column analytics.startup_quadrant_track.win_from  is '窗口最早那一场（first 那一读的日期）';
comment on column analytics.startup_quadrant_track.win_to   is '紧邻启动日那一场＝T-1（last 那一读的日期）。启动日当天不进窗口';
comment on column analytics.startup_quadrant_track.value_delta is
  'value_last - value_first。oper_score/stock_quality/env_resonance/temp_score/vol_temp/sector_heat 是分数差'
  '（同一场里比强弱的尺子，跨场比较只能用名次）；vol_stage 那一行是档位差（1~5 的档位，不是百分比）';
comment on column analytics.startup_quadrant_track.rank_delta is
  'rank_first - rank_last。⚠️ 正数 = 名次上升（rank 越小越靠前：第一场 3000、T-1 场 200 ⇒ +2800 是往上爬）。'
  '符号反了整句结论就反了，这一条写死在主表注释里';
comment on column analytics.startup_quadrant_track.pct_first is
  '窗口最早那一场的全市场分位，照 analytics.score.percent_rank 原样存。库里列注释写「0~1」，'
  '实测刻度是 0~100（第 1 名 = 100.0）⇒ 按百分数读';
comment on column analytics.startup_quadrant_track.quad_switches is
  '窗口内相邻两场象限不同的次数。只在 oper_score 行有值，其余六键是 NULL（那些键在库里就没有象限）。'
  '两边都取到象限才比：象限为空的那一场不参与，也不算一次翻转';
comment on column analytics.startup_quadrant_track.quad_last_switch_gap is
  '最后一次象限翻转那一场合到 T-1 之间隔了几场：0 = 翻转就发生在启动日前一天（离启动最近），'
  'bars-1 = 只在窗口最开头翻过一次。一次都没翻转 = NULL，不是 0（0 是「刚刚翻过」）';
comment on column analytics.startup_quadrant_track.q1_days is
  '窗口内落在 Q1（X 质量正、Y 环境正）的场数。q1..q4 相加 <= bars（可能有象限为空的场次）';
comment on column analytics.startup_quadrant_track.outcome_len is
  '同一个 (security_code, launch_date) 在 analytics.first_board_shape（fb-2）里的 seg_len，'
  '即「后来走到几板」。那是结果变量，不是筛子。fb-2 没有那一行（触发日不是连板段首日）就是 NULL';
comment on column analytics.startup_quadrant_track.outcome_gain4 is
  '母体表 analytics.startup_volume_profile（sv-2，同 (security_code, launch_date, cohort)）的 trigger_gain：'
  'board4 一族＝连板段累计涨幅，jump40＝四日累计涨幅。单位是小数比例（0.4641＝+46.41%），'
  '不是百分数 —— 与 fb-2 的 r*/ex* 不是同一把尺子';

comment on column analytics.startup_quadrant_track.security_code is '证券代码（带市场后缀），与母体 sv-2 同一份';
comment on column analytics.startup_quadrant_track.launch_date    is '启动日：board4 一族＝连板段第一天，jump40＝四日窗口的触发日。窗口不含这一天';
comment on column analytics.startup_quadrant_track.cohort         is '母体档位：board4 / board4_45 / board4_or_gain45 / jump40（sv-2 四档全要；board4_45 是 board4 的子集，不是另一批票）';
comment on column analytics.startup_quadrant_track.capability_key is 'analytics.score 的功能点键，七个之一：oper_score / temp_score / vol_temp / sector_heat / stock_quality / env_resonance / vol_stage。market_mood 一族不进本表（同日全市场同一个数，名次没有横截面意义）';
comment on column analytics.startup_quadrant_track.spec_version   is '判据版本 sqt-1：改窗口口径、改 is_stale 那一道筛、或改象限列的 NULL 纪律，都要换号并回算，旧批留着对账';
comment on column analytics.startup_quadrant_track.name           is '票名（取自母体表 sv-2，缺则 NULL）';
comment on column analytics.startup_quadrant_track.board          is '主板 / 创业板 / 科创板 / 北交所（取自 sv-2，最终来源 reference_data.security）';
comment on column analytics.startup_quadrant_track.bars is
  '窗口里实际拿到的场数（该票该键在 analytics.score 里有读数的日子数）。不满窗是覆盖事实：'
  'score 的可用批次（is_stale=false）比母体晚得多，更早的启动日往前凑不满';
comment on column analytics.startup_quadrant_track.value_first    is '窗口最早那一场（win_from 那天）的读数；缺那一场就是 NULL，不补 0';
comment on column analytics.startup_quadrant_track.value_last     is '紧邻启动日那一场（win_to＝T-1）的读数';
comment on column analytics.startup_quadrant_track.value_med      is '窗口内读数的中位（percentile_cont(0.5)）：与首末两读并看，防单点噪声把「一路向上」说成两个端点的事';
comment on column analytics.startup_quadrant_track.rank_first     is '窗口最早那一场的全市场名次（rank_in_market，越小越靠前）';
comment on column analytics.startup_quadrant_track.rank_last      is 'T-1 那一场的全市场名次';
comment on column analytics.startup_quadrant_track.rank_best      is '窗口内到过的最好名次 ＝ min(rank_in_market)。首末两读之外的极值就在这两列';
comment on column analytics.startup_quadrant_track.rank_worst     is '窗口内跌到过的最差名次 ＝ max(rank_in_market)。与 rank_best 一起分得开「一路稳」与「冲进来看均值」两种形状';
comment on column analytics.startup_quadrant_track.pct_last       is 'T-1 那一场的全市场分位，刻度同 pct_first（实测 0~100）';
comment on column analytics.startup_quadrant_track.quad_first     is '窗口最早那一场的四维象限（Q1 买入 / Q2 持有 / Q3 试探 / Q4 回避）。只在 oper_score 行有值，其余六键 NULL';
comment on column analytics.startup_quadrant_track.quad_last      is 'T-1 那一场的四维象限。只在 oper_score 行有值，其余六键 NULL';
comment on column analytics.startup_quadrant_track.q2_days        is '窗口内落在 Q2（X 正 Y 负，持有不加仓）的场数';
comment on column analytics.startup_quadrant_track.q3_days        is '窗口内落在 Q3（X 负 Y 正，只算试探）的场数';
comment on column analytics.startup_quadrant_track.q4_days        is '窗口内落在 Q4（X 负 Y 负，回避）的场数';
comment on column analytics.startup_quadrant_track.calculated_at  is '这一行最后一次重算的时刻（默认 now()，重算必须刷新它，读侧靠这句判断「这批数多久没动过」）';

create index if not exists startup_quadrant_track_date_idx
  on analytics.startup_quadrant_track (spec_version, launch_date desc);
create index if not exists startup_quadrant_track_code_idx
  on analytics.startup_quadrant_track (security_code, launch_date desc);

grant select, insert, update, delete on analytics.startup_quadrant_track to ft_etl, ft_app;
grant select                          on analytics.startup_quadrant_track to ft_reader;

-- 10-18 修正：window_days 进主键（与 44 同一课）—— 换窗口深度重算不许盖掉另一档的读数。
