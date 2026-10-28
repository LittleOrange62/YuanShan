-- -*- coding: utf-8 -*-
-- 26_startup_volume_profile.sql
-- 「启动前量能画像」：把"涨起来的票，启动之前 30 根日线里量是怎么放的"这件事落库。
-- 用户 2026-10-07 的原话拆成两类母体：
--   A `board4` —— 最近 3 个月出现过 **4 板及以上**（连续涨停 ≥4 天）的票，从**首板日之前**往前数 30 根；
--   B `jump40` —— 创业板(300/301/302)、科创板(688/689)、北交所(8x/4x/920)里
--                  最近 3 个月存在 **4 日累计涨幅 > 40%** 的票，从**触发日之前**往前数 30 根。
--
-- 为什么要两类母体（这条是设计的核心，别在后面被人合并掉）：
--   "4 板"这个说法只在 10% 档成立。创业板/科创板是 20% 档、北交所 30% 档，
--   4 板在那边等于 4 个交易日翻倍甚至更多 —— 拿同一个门槛去数，只会数出个位数样本（实测：
--   近 3 个月 4 板及以上 76 只里，主板 72、创业板 3、北证 1、科创 0）。
--   所以 B 类改用「4 日涨幅 > 40%」当触发，并同时存
--   `equiv_limit_ups` = ln(1+涨幅) / ln(1+档位) —— 把 40% 换成"等效几个涨停"
--   （主板 3.53 个、双创 1.85 个、北证 1.28 个（现算：ln1.4/ln1.1 等）），这样 A/B 两类的强度才能并排看。
--
-- 三条读数口径：
--   ① 窗口是**这只票自己的**有效交易日（往前 30 根日线，停牌的日子不占名额），
--      但停牌复牌那根会带 `prev_gap_days`，所以另存 `long_gap_days`＝窗口内隔了 >10 个自然日的根数；
--   ② 倍数读的是 `analytics.volume_ratio_daily`（今日成交量 ÷ 上一根日线成交量，spec_version='vr-1'），
--      窗口内缺量的票记 `bars_used` < 30，**不用 0 或 1 补齐**；
--   ③ `flat_days` 是精确等于 1.0000（四位收口后），不是"接近 1"——
--      想读"基本没变"用 `still_days`（0.95~1.05）那一档。
--
-- 主键带 spec_version（全项目约定：改判据是新写一批行，历史那批留着对账）。

create table if not exists analytics.startup_volume_profile (
  security_code     text          not null,
  launch_date       date          not null,       -- A=首板日；B=触发 4 日窗口的最后一天
  cohort            text          not null,       -- board4 / jump40
  spec_version      text          not null,       -- 判据版本（当前值见 app/compute/startup_volume.py 的 SPEC_VERSION）
  board             text,                         -- 主板 / 创业板 / 科创板 / 北交所
  name              text,                         -- 证券简称（只作展示，判据不依赖它）
  trigger_len       smallint,                     -- A=这段连板天数；B=固定 4（四日窗口）
  trigger_gain      numeric(10,4),                -- B=四日累计涨幅（0.40=+40%）；A=连板段累计涨幅
  equiv_limit_ups   numeric(8,3),                 -- ln(1+涨幅)/ln(1+档位)：等效几个涨停，跨板块可比
  window_days       smallint      not null default 30,
  bars_used         smallint,                     -- 窗口内实际有倍数的根数（<30 就是数据不够，页面必须标）
  up_days           smallint,                     -- 倍数 > 1.0 的根数
  down_days         smallint,                     -- 倍数 < 1.0
  flat_days         smallint,                     -- 精确 = 1.0
  dbl_days          smallint,                     -- 倍数 ≥ 2.0（倍量）
  near_dbl_days     smallint,                     -- ≥ 1.8 且 < 2.0（准倍量，给"是不是刚起步"留一档）
  calm_days         smallint,                     -- ≤ 0.5（缩量到一半以下）
  still_days        smallint,                     -- 0.95~1.05：读作"量基本没变"的那一档
  ratio_avg         numeric(10,4),
  ratio_median      numeric(10,4),
  ratio_p60         numeric(10,4),
  ratio_p80         numeric(10,4),
  ratio_p90         numeric(10,4),
  ratio_max         numeric(12,4),
  ratio_min         numeric(10,4),
  std_days          numeric(10,4),                -- 倍数的标准差（样本<2 交 NULL）
  long_gap_days     smallint,                     -- 窗口内 prev_gap_days>10 的根数（停牌复牌那种跳空量）
  ratios            jsonb,                        -- 逐日序列 [{d:"2026-08-03", v:1.23}, ...]，页面画迷你走势
  calculated_at     timestamptz   not null default now(),
  primary key (security_code, launch_date, cohort, spec_version)
);

comment on table  analytics.startup_volume_profile            is '启动前 30 根有效日线的量能画像（A=近3月 4 板及以上；B=近3月 双创/北证 4 日涨幅>40%）。判据与写入：app/compute/startup_volume.py；每日加工段步骤名 `startupvol`。倍量的原料来自 analytics.volume_ratio_daily';
comment on column analytics.startup_volume_profile.cohort      is 'board4=主板口径的连续涨停≥4；jump40=双创/北证口径的 4 日累计涨幅>40%。两类不可合并成一个榜：档位不同（10%/20%/30%），合并就等于用 10% 的尺子量 30% 的票';
comment on column analytics.startup_volume_profile.equiv_limit_ups is '等效涨停数 = ln(1+区间涨幅)/ln(1+该板块涨跌停档位)。B 类没有"几板"概念，靠这一列与 A 类比强度；档位按 board 取（主板 10%、创业/科创 20%、北证 30%），不给 ST 单独降档 —— 2026-10-07 行情实测主板 ST 走的仍是 ±10%（定档见 app/compute/limit_prices.py）';
comment on column analytics.startup_volume_profile.bars_used   is '窗口内实际参与统计的倍数根数。< window_days 说明这只票往前数不够 30 根（次新/长期停牌），页面必须标出来，不许和满窗的票并排排名';
comment on column analytics.startup_volume_profile.flat_days   is '倍数精确等于 1.0000（四位收口后）的根数，不是"接近 1"；要读"基本没动"看 still_days（0.95~1.05）';
comment on column analytics.startup_volume_profile.still_days     is '倍数落在 0.95~1.05 的根数 = "量基本没变"。与 flat_days 分开是因为成交量是整数，精确相等的日子本来就没几天，只报 flat_days 会把"没动"读成"0 天"';
comment on column analytics.startup_volume_profile.near_dbl_days  is '≥1.8 且 <2.0 的准倍量：倍量门槛(2.0)是用户给的，但只差一点的量在启动前很常见，单列出来免得被当成"没有"';
comment on column analytics.startup_volume_profile.calm_days      is '≤0.5 的缩量根数（与倍量对称的一头）';
comment on column analytics.startup_volume_profile.long_gap_days is '窗口内上一根隔了 >10 个自然日的根数（停牌复牌）。这类倍数的分母是很久以前的量，读数偏大，不是"今天突然放量"';
comment on column analytics.startup_volume_profile.spec_version is '判据版本在主键里：改窗口长度/触发门槛/等效涨幅公式都是新写一批行，旧行留着对账。sv-2 是一次特例：判据一个字没改，但它读的 market_data.stock_daily.limit_up 在 2026-10-07 按"当日证据"全史重灌过（app/compute/limit_prices.py），重灌后有 6 条 ST 样本不再是 4 板，那批行留在 sv-1 里对账，不与新批并排排名';
comment on column analytics.startup_volume_profile.security_code is '证券代码（带 .SZ/.SH/.BJ 后缀）。一行＝"这只票在这一天启动过"，同一只票在窗口里第二波启动是另一行';
comment on column analytics.startup_volume_profile.launch_date   is '启动日：board4＝这段连板的首板日，jump40＝触发"4 日累计涨幅>40%"那个窗口的最后一天。它是窗口锚点，往前 30 根的"前"以它为界（不含当天）';
comment on column analytics.startup_volume_profile.board         is '板块（主板/创业板/科创板/北交所），取自 reference_data.security 的当前归属；等效涨停数就按这一列的档位换算';
comment on column analytics.startup_volume_profile.name          is '证券简称，只作展示。判据不依赖它 —— 包括名字里的 ST：ST 那档的争议全在 limit_up 那一列（见 spec_version 与 cohort 两条注释）';
comment on column analytics.startup_volume_profile.trigger_len   is 'board4＝这段连板的天数（≥4 才进母体）；jump40＝固定 4（四日窗口），这一列对 B 类不表示板数';
comment on column analytics.startup_volume_profile.trigger_gain  is '触发段累计涨幅：段内逐日 close/基价 连乘再减 1。基价优先取 pre_close（除权日它与上一根收盘不同基，这才是真涨幅），只有它为空/为零的那几天才退回上一根收盘（停牌复牌日库里 pre_close 是空的）。仍有一天缺基价就整段交 NULL，不静默少乘一天';
comment on column analytics.startup_volume_profile.window_days   is '窗口长度（本表固定 30 根有效日线）。与 bars_used 一起读才知道这一行是不是满窗';
comment on column analytics.startup_volume_profile.up_days       is '倍数 > 1.0 的根数（比上一根放量）。与 down_days 加起来一般等于 bars_used，因为倍数只有"大于一/小于一/正好等于"三种';
comment on column analytics.startup_volume_profile.down_days     is '倍数 < 1.0 的根数（比上一根缩量）。启动前这段的实测中位数小于 1，所以这一列普遍比 up_days 大，不是数据反了';
comment on column analytics.startup_volume_profile.dbl_days      is '倍数 ≥ 2.0 的根数（倍量）。门槛 2.0 是用户 2026-10-07 给的原话；一天都凑不出也照实记 0，不因为"启动前应该放量"就补';
comment on column analytics.startup_volume_profile.ratio_avg     is '窗口倍数的算术平均。⚠️ bars_used < 10（判据下限 MIN_BARS）时整列 NULL —— 六个样本的平均数与三十个的并排排名没有意义，宁可空着';
comment on column analytics.startup_volume_profile.ratio_median  is '窗口倍数的中位数（线性插值，同 PG percentile_cont）。这一列是"启动前长什么样"最抗极端值的读数：倍量日会把均值拉上去，中位数不会被拉';
comment on column analytics.startup_volume_profile.ratio_p60     is '60 分位倍数：六成日子放量在这个数以下。介于中位与 80 分位之间，用来看"普通的一天"离倍量还差多少';
comment on column analytics.startup_volume_profile.ratio_p80     is '80 分位倍数：窗口内最凶的那 20% 日子大概放到几倍。页面拿它与 dbl_days 一起看 —— p80 已经接近 2.0 而 dbl_days=0 说明量在憋着但没到门槛';
comment on column analytics.startup_volume_profile.ratio_p90     is '90 分位倍数（比 p80 更靠尾部，样本只有 30 根时抖动比 p80 大，横比优先用 p80）';
comment on column analytics.startup_volume_profile.ratio_max     is '窗口内最大倍数（单日放到几倍）。分母是"上一根"而不是"近 20 日均量"，所以一根地量后面接一根正常量就能出大数，读它要看 long_gap_days';
comment on column analytics.startup_volume_profile.ratio_min     is '窗口内最小倍数（单日缩到上一根的几分之几）';
comment on column analytics.startup_volume_profile.std_days      is '窗口倍数的样本标准差（Welford 之前的朴素式在这里够用；根数 < 2 时 NULL）。它衡量的是"量能抖不抖"，不是"有没有放量"，与 dbl_days 是两回事';
comment on column analytics.startup_volume_profile.ratios        is '逐日序列 jsonb：[{d:"2026-08-03", v:1.23}, …]，按日期升序、v 可能为 null（那天没有量）。页面画迷你走势读它，不要自己再除一遍';
comment on column analytics.startup_volume_profile.calculated_at is '这一行最后一次被算的时刻。判据重跑是按主键 upsert 覆盖，所以它是"这一版读数新鲜不新鲜"的唯一线索（步骤没跑的行会一直停在旧时刻）';

create index if not exists startup_volume_profile_cohort_idx
  on analytics.startup_volume_profile (cohort, spec_version, launch_date desc);
create index if not exists startup_volume_profile_code_idx
  on analytics.startup_volume_profile (security_code, launch_date desc);

grant select, insert, update, delete on analytics.startup_volume_profile to ft_etl, ft_app;
grant select                          on analytics.startup_volume_profile to ft_reader;
