-- 异动筛查（盘中事件）：一瞬间冲高与触及涨停的逐事件读数
-- 用户 2026-10-13 点名：在量化选股里新增「异动筛查」，第一块功能是复盘最近两个月盘中
-- 「瞬间冲高 5 个点以上」或「冲到涨停」的票，之后走势分别怎么样，并按最近一天冲高的时间顺序排。
--
-- 一行 = 一次事件（一只票一场）。母体口径（用户选定的「水平＋速度组合」）：
--   日内最高较前收 >= 5% **且**（15 分钟滚动窗口内拉升 >= 4 个点 **或** 触及涨停价）。
--   实测 2026-08-14~10-13：只按水平（>=5%）是 19,588 条 / 37 场 = 平均每天 529 只，
--   加速度这一刀后每天约 350~400 条 —— 不是嫌多，是「慢涨一天」与「瞬间拉起来」根本两回事，
--   混在一张榜上，后面那三个有区分度的读数（承接、速度、时刻）就被冲淡了。
--
-- 同日多峰 v1 不切：只取「日内最高那一次」。要切多峰得先定义「回落多少算一个新峰」，
-- 那是另一套判据，没定之前不硬做（v1 一行一票，读侧统计才不会重复计数）。
--
-- 这里**不存次日/3日/5日走势**：那些是查出来的，不是算出来的（见下面「为什么」）。
-- 判据与写入：app/compute/intraday_spike.py（每日加工段步骤名 spike）。

create table if not exists analytics.intraday_spike (
  trade_date        date        not null,
  security_code     text        not null,

  -- ── 幅度（日线口径，与通达信「盘中最高折合涨幅」同一算法）──
  pre_close         numeric(16,6),
  day_high          numeric(16,6),
  day_low           numeric(16,6),
  open_price        numeric(16,6),
  close_price       numeric(16,6),
  hi_pct            numeric(8,3),           -- 最高较前收 %（母体就是它 >= 5）
  cl_pct            numeric(8,3),           -- 收盘较前收 %（当日涨跌）
  open_pct          numeric(8,3),           -- 开盘较前收 %：低开造成的"阴线"与盘中被打下来是两件事
  fade_pct          numeric(8,3),           -- 收盘距最高 %（<=0）。实测：这个数本身对次日几乎没有区分度
  upper_shadow_pct  numeric(8,3),           -- 上影占当日振幅 %

  -- ── 板（触板与封住的时长是这次复盘里最强的一组读数）──
  limit_up          numeric(16,6),          -- 当日涨停价（收口层 stock_daily.limit_up，底表 reference_data.limit_price_daily + limit_basis）
  board_pct         numeric(6,2),           -- 档位 %（10/20/30），跨档比强度时必须带着它
  touched           boolean not null default false,   -- 盘中最高够到涨停价
  sealed            boolean not null default false,   -- 收盘就在涨停价上（|close-limit_up| <= 0.005）
  touch_min         int,                    -- 触板的分钟根数（high 够到涨停价）
  sealed_min        int,                    -- 封住的分钟根数 = 「保持在那里」的时长
  board_runs        int,                    -- 封住段数：>=2 就是开了又回封
  first_seal_time   time,                   -- 第一次封住的时刻

  -- ── 时刻与速度（只有当天有分钟线才算得出来；算不出就 NULL，不许拿日线冒充）──
  peak_time         time,                   -- 日内最高出现在几点几分（榜单的排序键）
  first5_time       time,                   -- 第一次站上 +5% 的时刻
  peak_min_no       int,                    -- 峰值是当天第几分钟（1~240）
  speed15_pct       numeric(8,3),           -- 15 分钟滚动窗口内的最大拉升 %（「瞬间」的量尺）
  speed15_time      time,                   -- 那一段的结束时刻
  above5_min        int,                    -- 站上 +5% 的分钟根数（持续多久）

  -- ── 承接（复盘里区分度最强的一刀：峰值之后守不守得住）──
  after5_low_close  numeric(16,6),          -- 峰值后 5 根里最低的收盘
  after5_dd_pct     numeric(8,3),           -- 它相对日高的回撤 %（> -1 就是「守住了」）
  above_vwap_pct    numeric(8,3),           -- 全天收在当日均价线上方的分钟占比 %
  tail_above_vwap   numeric(8,3),           -- 14:30 后收在均价线上方的占比 %

  -- ── 量能（冲高段是不是有钱在推、回落段是不是被砸）──
  day_vol_shares    numeric(20,4),
  vol_above5_share  numeric(8,3),           -- 站上 +5% 那几分钟的量 ÷ 全日量 %
  pre_peak_min_vol  numeric(20,4),          -- 峰值前每分钟均量（峰值在第一根时没有"前"，留 NULL）
  post_peak_min_vol numeric(20,4),          -- 峰值后每分钟均量
  fade_vol_ratio    numeric(8,3),           -- 后 ÷ 前。任一侧没有就是 NULL —— 0 会被读成「没人卖」

  -- ── 位置（复盘要能回答「低位冲高 vs 高位冲高」）──
  gain20_pct        numeric(8,3),           -- 前 20 场累计涨幅 %（判「低位/横盘/中位/高位」）
  is_hi60_break     boolean,                -- 当日盘中最高是否创 60 日新高

  -- ── 标签与记账 ──
  spike_tags        text[]  not null default '{}',   -- fast / quick / touched / sealed / held5 / late / reopen / slow
  bars_used         int,                    -- 当天真有几根 1 分钟线（正常 240；停牌、盘中长时间无成交会少）
  minute_available  boolean not null default false,  -- 当天分钟线在不在库（不在 ⇒ 上面分钟类字段全 NULL）
  missing           text,                   -- 缺了哪些读数（逗号分隔列名），全有则 NULL
  spec_version      text        not null,
  calculated_at     timestamptz not null default now(),

  primary key (security_code, trade_date, spec_version)
);

comment on table  analytics.intraday_spike is '异动筛查（盘中事件）：一瞬间冲高与触及涨停的逐事件读数。母体 = 最高较前收 >= 5% 且（15 分钟内拉升 >= 4 个点 或 触及涨停价）。一物一行（一票一场），同日多峰 v1 不切。判据与写入 app/compute/intraday_spike.py，每日加工段步骤名 spike。次日/3日/5日走势不入表 —— 由读侧用 lead+复利现算，并与同期基线一起交（没有基线的分布数会被读反）';
comment on column analytics.intraday_spike.hi_pct    is '日内最高较前收 %。母体门槛就是它 >= 5。实测 2026-08-14~10-13 全市场 19,588 条（每天 529 只）：5~7 档 9,060 条、7~10 档 5,314、10~15 档 1,336、15~20 档 428、触板未封 1,060、触板封住 2,327。冲高幅度与次日表现单调负相关（次日上涨 41.6% → 36.3%，同期基线 46.0%）—— 唯一例外是收盘封住涨停（51.0%），但溢价只有次日一天，3 日归零、5 日转负';
comment on column analytics.intraday_spike.fade_pct  is '收盘距当日最高 %（<=0）。复盘结论：这个数本身对次日几乎没有区分度（按它分五档，次日上涨 39.3/40.3/40.9/44.8/43.0%，无单调关系）。"回落越深越危险"那句口诀在本机数据里不成立，别拿它当卖出判据';
comment on column analytics.intraday_spike.sealed_min is '封在涨停价上的分钟根数 = 上影"保持在那里"的时长。判据：abs(close - limit_up) <= 0.005（A 股最小变动 0.01，用 0.5% 容差会把接近板误算成封住）。实测 2026-09-30 全市场 72 只盘中触板：封 0 分钟 6 只（其中 5 只全场只有 09:31 那一根摸到过）、1~30 分钟 8、31~120 分钟 14、121~200 分钟 10、200 分钟以上 34；中位 191 分钟、均值 147.5。封住时长与次日表现单调正相关（9 场分钟样本：封 0 分钟 33.7% → 封 200 分钟以上 54.9%）';
comment on column analytics.intraday_spike.board_runs is '封住段数（由非封转成封的次数）。>=2 就是"开了又回封"。实测 9 场 861 次触板：全程没开板 270 次 次日 +1.46%/52.2%，开过板又回封 180 次 次日 -0.94%/39.4% —— 这是全部维度里仅次于"封住时长"的第二强区分，且与网上"回封板分歧加大"那句一致';
comment on column analytics.intraday_spike.peak_time is '日内最高出现在几点几分（1 分钟 bar 是右端标注：09:35 那根覆盖 09:30~09:35）。榜单排序键 = trade_date 倒序、同日按这一列升序（用户点名要的时间顺序）。实测 9 场：高点在 10:00 前 36.9%、10:00~11:30 35.1%、13:00~14:30 32.7%、14:30 后 36.7%（同期基线 37.9%）—— 越晚冲高越差，但"尾盘冲"那档因样本仅 316 条不作结论';
comment on column analytics.intraday_spike.speed15_pct is '任意 15 根连续分钟线里的最大拉升幅度 %（当日最低点起算的滚动窗口），是"瞬间"两个字的量尺。母体的速度那一刀 = 它 >= 4。实测 9 场：慢拉（<4）37.6%、4~7 个点 35.6%、>7 个点（急拉/尖刀）33.9% —— 网上"脉冲式急拉不要追"这句被数据支持，拉得越急次日越差';
comment on column analytics.intraday_spike.after5_dd_pct is '峰值后 5 根分钟线里最低收盘 ÷ 日高 - 1，单位 %。复盘里区分度最强的一刀：> -1（守住了）n=1,224 次日 -0.46%/上涨 40.0%，回撤 1~3 个点 n=2,011 次日 -1.21%/33.1%，回撤 >3 个点 n=759 次日 -1.09%/34.4%（9 场基线 -0.56%/37.9%）。与网上"拉升后看 5 分钟能不能待在高点附近"一致';
comment on column analytics.intraday_spike.above_vwap_pct is '全天收在当日均价线（累计成交额 ÷ 累计成交量）上方的分钟占比 %。实测三档次日上涨 36.0 / 35.3 / 35.2% —— 无区分度。网上"回落不破均价线 = 试盘"那句在日均口径上看不出来，别再给它单独加列伪装成信号';
comment on column analytics.intraday_spike.fade_vol_ratio is '峰值后每分钟均量 ÷ 峰值前每分钟均量。任一侧无分钟 ⇒ NULL，不写 0（0 会被读成"没人卖"，是反的）。实测 9 场：缩量 34.6%、放量 1~2 倍 34.4%、巨量 >2 倍 31.2%（n=125）—— "回落缩量=洗盘"这句基本看不出差别，只有巨量砸回落才更差';
comment on column analytics.intraday_spike.gain20_pct is '前 20 场累计涨幅 %（判低位/横盘/中位/高位）。实测两个月未触板的冲高：低位 44.1%、横盘 39.6%、中位 37.5%、高位 43.8%（基线 46.0%）—— 方向上"低位比中位好"成立，但低位那档仍低于基线 ⇒ 不能当买入信号。当日创 60 日新高的冲高 37.9%，比没创新的 41.1% 更差';
comment on column analytics.intraday_spike.spike_tags is '事件标签数组（判据只住 app/compute/intraday_spike.py，页面不自己判）：fast 急拉>=7 / quick 4~7 / touched 触板 / sealed 封住 / held5 峰值后5分钟守住 / late 高点在14:30后 / reopen 开了又回封 / slow 慢拉。筛选走数组重叠（&&），且必须拼在 limit 之前';
comment on column analytics.intraday_spike.bars_used is '当天真有几根 1 分钟线。正常 240；实测全市场平均 238.0（停牌、次新、盘中长时间无成交会少）。峰值在第一根（一字板、开盘即最高）时"峰值前"不存在 ⇒ speed/量能前后对比留 NULL';
comment on column analytics.intraday_spike.minute_available is '当天该票的 1 分钟线在不在库。false 时 peak_time/speed15_pct/after5_dd_pct/sealed_min 等分钟级字段一律 NULL，读侧必须写成"这一天分钟线没入库"，不许说"没有冲高"，也不许用日线读数顶替';

create index if not exists intraday_spike_date_idx  on analytics.intraday_spike (trade_date, peak_time);
create index if not exists intraday_spike_hi_idx    on analytics.intraday_spike (trade_date, hi_pct desc);
create index if not exists intraday_spike_tags_idx  on analytics.intraday_spike using gin (spike_tags);

grant select, insert, update, delete on analytics.intraday_spike to ft_etl;
grant select on analytics.intraday_spike to ft_app, ft_reader;
