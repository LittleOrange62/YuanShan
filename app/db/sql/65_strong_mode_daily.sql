-- -*- coding: utf-8 -*-
-- 65_strong_mode_daily.sql
-- 「⑪ 最强模式」（每天 19:19 那一栏）的逐场逐档读数：今天这一档命中几只、占比多少、
-- 它自己的常态占比是多少、强在哪个板上。判据只住 `duanxian/strong_mode.py` 一份。
--
-- 为什么要落这一张（用户 2026-11-06 的原话：「加个推送 11……并且列出最近 5 天最强模式的变化和趋势」）：
--   1) 「最近 5 场的变化与趋势」是**每天要说的第二句话**，光有当天那一行答不了「上周说高位反复，
--      这周还在吗」。表里逐档留一行，隔几天问得出「哪一档在扩、哪一档在收」。
--   2) 更要紧的是**事后能对账**：这一栏往群里说的是「今天最强是断板反包」，第二天那些票走得
--      怎么样，只有把当时的名单与阈值快照一起落下来才答得出（同 ⑩ 的 `outlook_pick_daily` 那条理由）。
--   3) 主键含 spec_version：模式定义或阈值改版就换版本号，旧行留着对照，**不覆盖** ——
--      覆盖掉就再也没法比较两版谁判得准。
--
-- 三条口径上的说明（都写在判据那份文档串里，这里只留读数侧要的三件）：
--   ① `share_pct` 的分母是**当日封住的只数**（`n_sealed`），不是全市场：这一栏比的是「今天这批涨停
--      里哪种打法占多少」，拿 5,500 只当分母会把 58 只的结构性读数稀释成 1% 这种没法读的数。
--   ② `base_share_pct` 是该档**近 sm_baseline_sessions 场占比的中位数（不含当日）**，
--      `lift_pp = share_pct − base_share_pct`。强度按这个排而不是按只数排：
--      `first_spread`（首板扩散）每天都是只数最多的一档（2026-11-04 实测 77/123），
--      按只数排等于天天报同一个词。中位数刻意**不用均值**：那 20 场里只要有一场停牌潮/涨停潮
--      就把均值拖走，中位数不会。
--   ③ 一只票可以同时进好几档（模式不互斥，样例那句「C+D 高位反复二波/异动规避反包」本来就是两档并列），
--      所以 `sum(n_stocks) > n_sealed` 是正常读数，不是重复计数 bug。
--
-- 缺数纪律：判不了交 NULL 并在 `missing` 里点名（`pos250` 缺 ⇒ 该票不进高位反复那一档；
-- 当日库里没有涨停池 ⇒ 整场不落库，由推送那一炉记 `strong_mode_empty`，不写一行假的 0）。

create table if not exists analytics.strong_mode_daily (
    trade_date       date        not null,        -- 场次（涨停池里真有行的那一天，不是日历日）
    spec_version     text        not null,        -- 'strong-mode-1'；模式定义/阈值口径改版换它
    mode_key         text        not null,        -- 六档之一：repeat_high/reversal/second_wave/wave_single/first_spread/one_word
    mode_label       text        not null,        -- 屏上那六个词（改词要跟着 `MODE_LABELS` 走）

    n_sealed         integer     not null,        -- 当日封住涨停的只数（所有 share_pct 的分母）
    n_stocks         integer     not null,        -- 这一档今天命中几只（0 是真实读数，不是缺数）
    share_pct        numeric(6,2),                -- n_stocks ÷ n_sealed × 100；n_sealed=0 时 NULL
    base_share_pct   numeric(6,2),                -- 该档近 N 场占比中位数（不含当日）；一场都没凑到 ⇒ NULL
    base_sessions    smallint,                    -- 上面那个中位数是真有几场算出来的（新股潮/停更时会少于 N）
    lift_pp          numeric(7,2),                -- share_pct − base_share_pct：今天比自己的常态强几个点
    max_consec       smallint,                    -- 这一档里今日最高连板（0 只时 NULL）
    order_amount_yi  numeric(14,3),               -- 这一档封单额合计（亿元）：源站没给全部 ⇒ NULL；部分没给 ⇒ 这是下限，缺的只数在 `missing` 里

    is_winner        boolean     not null default false,  -- 当日判出的最强模式（复合时两档同为 true）
    stocks           jsonb       not null default '[]'::jsonb,  -- 命中名单（≤12 只：代码/名/今板/窗口次数/最长连板/几天几板/pos250）
    params           jsonb       not null default '{}'::jsonb,  -- 那 14 个 sm_* 阈值的快照（事后问得出「这是哪套尺给的」）
    missing          jsonb       not null default '{}'::jsonb,  -- 这一档/这一场判不了的那几格：未归类只数、pos250 缺只数、连板缺只数、封单额缺只数
    calculated_at    timestamptz not null default now(),

    constraint strong_mode_daily_pkey primary key (trade_date, spec_version, mode_key)
);

create index if not exists strong_mode_daily_day_idx
    on analytics.strong_mode_daily (spec_version, trade_date desc, lift_pp desc);
-- 「这一档最近五场都是第几名」是趋势那一节的读法：按 (mode_key, trade_date desc) 走得动才有索引可用
create index if not exists strong_mode_daily_mode_idx
    on analytics.strong_mode_daily (mode_key, spec_version, trade_date desc);

comment on table  analytics.strong_mode_daily is
  '⑪ 最强模式：一行 = (这一场, 这一档, 判据版本) 的命中只数/占比/常态基准/强度。'
  '判据只住 duanxian/strong_mode.py（写侧 collect/rank_modes），推送在 ingest/jobs/push/t11_strong_mode.py。'
  '模式不互斥 ⇒ sum(n_stocks) 大于 n_sealed 是正常读数。这是客观结构描述，不是买入信号。';
comment on column analytics.strong_mode_daily.share_pct is
  '分母是当日封住涨停的只数 n_sealed（不是全市场）：这一栏比的是今天这批涨停里哪种打法占多少。';
comment on column analytics.strong_mode_daily.base_share_pct is
  '该档近 sm_baseline_sessions 场占比的**中位数（不含当日）**；base_sessions 记下真有几场算进来了 —— '
  '凑不满 N 场时（库里场次不够）这一格仍然给数，但那一行自己带着「只有 X 场」的底气。';
comment on column analytics.strong_mode_daily.lift_pp is
  'share_pct − base_share_pct，百分点。「最强模式」按它排，不按只数排（首板扩散天天只数最多）。';
comment on column analytics.strong_mode_daily.stocks is
  '命中名单最多留 12 只（按今日连板↓、窗口次数↓），整张窗口统计不落库——每场都能由判据重算出来，'
  '落一份就会在改版之后分不清这份名单是旧尺还是新尺数的。';

grant select                 on analytics.strong_mode_daily to ft_reader;
-- delete 是给「重算一场先清这一场」的（与 53_limit_up_pool_daily 同一套授权）
grant select, insert, update, delete on analytics.strong_mode_daily to ft_app, ft_etl;
