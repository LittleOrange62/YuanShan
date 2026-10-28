-- 交易体系榜（组合判据，不是单形态）：近 20 场有涨停 × 向上跳空缺口三天不回补 × 缩量回调后一根阳线确认
-- 用户 2026-10-14 口述的三条件体系，原话要点：
--   ①「二十天之内一定有一个涨停的」——涨停代表有主力参加，「有资金的地方、有鱼的鱼塘，我们才玩」，
--      没这条「全都是浪费时间」；
--   ②「向上挑空的缺口，三天不回补」——两种主力行为：真金白银抢筹推高、以及快速抬高你的成本不给你上车机会；
--      「回调了都是弱势股」；
--   ③「他一直在涨你能追吗？他得歇口气」——回调一天两天三天都行，**只要回调是缩量的**，货就出不去；
--      「再有一根阳线的确认，这个时候带好你的止损，冲」，接下来才是主升浪。
--
-- 口径全部按用户 2026-10-14 选定的那一份：整段跳空（今日最低 > 昨日最高）且缺口 >= 1%；
-- 「不回补」与「仍未回补」都按**最低价**对缺口下沿（= 缺口前一日最高）判；止损参考位就是这条下沿。
-- 价格一律后复权（close × adjustment_factor）：拿未复权价判跳空，除权日会造出一堆假缺口。
--
-- ⚠️ 这张表落的是**事实与判据**，不替你担保方向。两组数都在本机量得，**窗口不同、不要求相等**，
--    但方向必须一致，读的时候别把它们串成一个：
--    ① 写判据之前的独立探针（`.local/system3_probe.py`／`system3_wave.py`／`system3_stop.py`，
--       2025-01-06 起的一年半 —— 三支的右端分别是 2026-09-10 与 2026-10-08，全市场，
--       后复权，无成本，且**缺口不限 4~7 场**，所以母体比这张表宽）：三条件全过 + 今日阳线 2,835 例
--       ≈ 平均每天 6.8 只；相对基线 —— 右尾更肥（后 20 日内冲出两成 17.9% vs 基线 9.1%），
--       但胜率与期望更差（次日上涨 45.3% vs 48.7%、后 20 日复利中位 −2.91% vs +0.51%）；
--       带上止损也没救回来：扫过 +15/−5、+20/−7、+30/−12、+50/−15 四档，
--       「先到止盈」每一档都高于基线，「先到止损」高得更多，扣完的规则期望每一档都不如基线。
--    ② 落库后同窗并排的三档（页面/接口显示的就是这一份，权威）：
--       `analytics.system_entry_stats`（DDL 34，每日由 sysentry 那一步算一次），
--       2026-10-14 首批实测窗口 2025-09-24 ~ 2026-10-13 的 250 场：
--       基线 n=1,346,760／次日上涨 47.3%／后 20 场冲出两成 17.1%／先碰+20% 14.0%／先碰−7% 51.3%／
--       规则期望 +0.21%；①② n=11,997／46.4%／30.5%／20.7%／69.7%／−0.52%；
--       ①②③全过 n=1,369／45.1%／28.0%／19.3%／69.2%／−0.83%。
--       ⇒ 结论同一句话：**它筛的是「有浪」，不是「会涨」**。
--       探针那一档「只满足①」（359,471 个票·日）在表里数不出来 —— 这张表的母体就是 ①② 同时成立。
--
-- 判据与写入：app/compute/system_entry.py（每日加工段步骤名 sysentry）。

create table if not exists analytics.system_entry (
  trade_date        date        not null,
  security_code     text        not null,

  -- ── 价与量（未复权那几列是为了跟屏幕上、跟通达信对得上；判据一律用后复权）──
  open              numeric(16,6),
  high              numeric(16,6),
  low               numeric(16,6),
  close             numeric(16,6),
  pre_close         numeric(16,6),
  pct_change        numeric(8,3),
  volume_shares     numeric(20,4),
  adjustment_factor numeric(16,6),
  close_hfq         numeric(16,6),          -- 当日后复权收盘（判据与止损位都挂在这个尺度上）

  -- ── ① 近 20 个场次内有涨停 ──
  c1_ok             boolean not null default false,
  limit_up_count20  int,                    -- 这 20 场里有几天封住涨停（数的是 close=涨停价）
  last_limit_up_date date,                  -- 最近那一根涨停是哪天
  limit_up_ago      int,                    -- 距今几个场次（0 = 就是今天）

  -- ── ② 向上整段跳空 + 三天不回补 + 今日仍未回补 ──
  c2_ok             boolean not null default false,
  gap_date          date,                   -- 缺口日（窗口内取最近一个满足条件的）
  gap_ago           int,                    -- 缺口日距今几个场次（判据要求 4~7：3 场用来确认不回补 + 1~3 场回调）
  gap_pct           numeric(8,3),           -- 缺口幅度 %：缺口日最低价 ÷ 前一日最高价 - 1（母体要求 >= 1）
  gap_floor_hfq     numeric(16,6),          -- 缺口下沿 = 缺口日前一日最高（后复权）。跌破即证伪
  gap_unfilled      int,                    -- 缺口之后到今天，连续多少个场次最低价都没回到下沿
  gap_vol_shares    numeric(20,4),          -- 缺口日成交量（③的缩量就是跟它比）

  -- ── ③ 回调段：缩量 + 有阴线（看今天往前 3 个场次）──
  c3_ok             boolean not null default false,
  pullback_sessions int,                    -- 缺口后到今日之间的场次数（1~6）
  pullback_down_cnt int,                    -- 其中阴线几根（判据要求 >= 1）
  pullback_max_vol_ratio numeric(8,3),      -- 段内最大单日量 ÷ 缺口日量（判据要求 <= 1，越小越干净）
  pullback_min_low_hfq   numeric(16,6),     -- 段内最低价（后复权）：离下沿还有多远

  -- ── 今日确认（那句「再有一根阳线的确认」）──
  confirm_yang      boolean not null default false,   -- 阳线且收在前收之上
  vol_vs_prev       numeric(8,3),                     -- 今日量 ÷ 昨日量

  -- ── 合体与止损 ──
  entry_ok          boolean not null default false,   -- ①②③ + 今日阳线：这一下才是"上车机会"
  watch_ok          boolean not null default false,   -- ①② 成立、③/确认还差一点：观察池
  stop_ref_hfq      numeric(16,6),                    -- 止损参考位 = 缺口下沿（用户选定：与判据同源）
  stop_dist_pct     numeric(8,3),                     -- 它距今日收盘的百分比（负数 = 在下方）

  -- ── 覆盖与缺数 ──
  bars_used         int,                    -- 往前真有几根日线（不足 20 场时 ① 不可判，凑数最坏）
  missing           text,                   -- 缺了哪些读数（逗号分隔列名），全有则 NULL
  spec_version      text        not null,
  calculated_at     timestamptz not null default now(),

  primary key (security_code, trade_date, spec_version)
);

comment on table  analytics.system_entry is '交易体系榜（组合判据）：近 20 场有涨停 × 向上整段跳空缺口三天不回补 × 缩量回调后一根阳线确认。一物一行（一票一场），只收 ①② 同时成立的票（watch_ok），entry_ok 是那三条全过的"上车"档。判据与写入 app/compute/system_entry.py，每日加工段步骤名 sysentry。价格判据一律后复权（× adjustment_factor）';
comment on column analytics.system_entry.c1_ok     is '① 近 20 个场次内出现过封住涨停（close >= limit_up - 0.005）。实测 2025-01-06~2026-10-08 全市场 359,471 个票·日满足，次日均值 +0.05%／上涨 46.9%、后 5 日复利 −0.48%（同期基线 +0.10%／48.7%／+0.21%）—— 它是"有主力参与"的门票，本身不构成优势';
comment on column analytics.system_entry.gap_pct   is '向上跳空缺口的幅度 %：缺口日最低价 ÷ 前一日最高价 − 1，母体要求 >= 1 个点（0.2% 那种贴线不算）。探针口径（缺口不限 4~7 场）下全市场 130,426 个票·日、加 1% 这一刀后 ①② 同时成立 68,870 个（≈164 只/场）；**本表按 4~7 场的择时窗口收，落库后实测 12,014 行 / 242 场 ≈ 49.6 只/场**（2025-10-14~2026-10-13）';
comment on column analytics.system_entry.gap_floor_hfq is '缺口下沿 = 缺口日**前一日最高**（后复权）。今日最低价必须还在这条线之上（仍未回补），跌破就是这套体系的证伪点 —— 也是止损位的定义';
comment on column analytics.system_entry.gap_unfilled is '缺口之后到今天，连续多少个场次最低价都没回到下沿。判据要求 >= 3（用户原话"三天不回补"）；缺口日到今日限 4~7 个场次：3 场用来确认不回补，剩下 1~3 场就是他说的"歇口气"'
;
comment on column analytics.system_entry.pullback_max_vol_ratio is '回调段（今天往前 3 场）最大单日量 ÷ 缺口日量，判据要求 <= 1。这是那句"回调是缩量的，货就出不去"的量尺。>1 说明回调路上放量，那就是出货而不是缩量洗盘 —— 实测这句在本机看不出差别（回落段缩量 34.6% 与放量 1~2 倍 34.4% 次日几乎一样），所以它只是**记录**，不当卖点用';
comment on column analytics.system_entry.confirm_yang is '今日阳线（后复权收盘 > 开盘）且收在前收之上 —— 用户说的"再有一根阳线的确认"。探针口径（1.7 年）：①②③ 全过 + 今日阳线 2,835 例 ≈ 平均每天 6.8 只；本表 242 场实测 1,370 行 ≈ 5.7 只/场。再加"今日量 > 昨日量"只剩 1,597 例 ≈ 3.8 只';
comment on column analytics.system_entry.entry_ok  is '三条全过 + 今日阳线 = 这套体系认的下车机会。**必须与基线并排读**，那三行的权威在 analytics.system_entry_stats（同窗、同一句查询算出）。2026-10-14 首批 250 场窗口实测：次日上涨 45.1%（基线 47.3%）、后 20 场冲出两成 28.0%（基线 17.1%）、按 +20%/−7% 走的期望 −0.83%（基线 +0.21%）—— 它筛的是"有浪"，不是"会涨"';
comment on column analytics.system_entry.stop_ref_hfq  is '止损参考位 = 缺口下沿（用户 2026-10-14 选定：与判据同源，跌破就是证据被推翻）。另外两档（回调段最低、固定百分比）实测过，救不回来：独立探针那一段（2025-01~2026-09）带 +20%/−7% 跑，先到止损 60.3%（基线 44.4%）、规则期望 +0.52%（基线 +1.45%）；落库后的同窗三档以 analytics.system_entry_stats 为准（250 场窗口：全过档 69.2% vs 基线 51.3%、期望 −0.83% vs +0.21%）—— 两段的窗口不同，方向一致：止损没有把期望救回来';
comment on column analytics.system_entry.watch_ok  is '①② 成立但 ③/确认还差一点的观察池。榜单默认只列 entry_ok，切到观察池才有这些行 —— 分开是因为"有缺口没回调"和"回调完确认"是两种不同的等法';
comment on column analytics.system_entry.bars_used is '往前真有几根日线。不足 20 场时 ① 判不了（不是"没涨停"），c1_ok 直接 false 并把 limit_up_count20 留 NULL；次新凑满窗的那批不参与这套判据';
comment on column analytics.system_entry.missing   is '这一行缺了哪些读数（逗号分隔列名），全有则 NULL。缺的列一律留 NULL 不按 0 —— 0 会被读成"回调段没量"或"缺口 0%"，方向是反的';

create index if not exists system_entry_date_idx  on analytics.system_entry (trade_date, entry_ok, gap_pct desc nulls last);
create index if not exists system_entry_watch_idx on analytics.system_entry (trade_date, watch_ok);
create index if not exists system_entry_ok_idx    on analytics.system_entry (trade_date, security_code) where entry_ok;

grant select, insert, update, delete on analytics.system_entry to ft_etl;
grant select on analytics.system_entry to ft_app, ft_reader;
