-- 选股汇总（交叉命中榜）：把各条量化链**当天选出来的票**收到一张表上，看谁被多个判据同时点到。
-- 用户 2026-10-13 点名：「把倍量形态筛出来的 150 个和四维象限筛出来的 200 个，看下有没有重复出现的，
-- 附上趋势评分」+「把量化、复盘等各种结果汇总展示和交叉赋分，重点关注重复出现的票」。
-- 裁定：源的清单＝倍量150 / 四维象限200 / 埋伏150 / 突破首破 / 龙虎榜；
--       赋分口径＝**命中榜数优先，其次各榜里最好的名次**（不做单一合成分 —— 把"只在象限第 1 名"
--       和"四个榜都第 120 名"排到一起的那种数，看着整齐但会骗人）。
--
-- 一物一行：一行 = 一只票在一场的交叉命中情况。**只落命中的票**（没被任何一个榜选中的票不入表），
-- 所以这张表数不出"全场有几只票"，母体那句话必须由读侧从各源表去问（见 duanxian/confluence_view.py）。
--
-- 各榜的**选法**（表、版本条件、排序、取前几名）只住 app/compute/confluence.py 的 SOURCES 一处，
-- 各页与自己都用这一份：两处各写一遍就会漂，漂了这张表说的"重复出现"就是假的。
-- 判据与写入：app/compute/confluence.py（步骤名 confluence，每日加工段，排在各源之后）。

create table if not exists analytics.confluence_daily (
  trade_date        date        not null,
  security_code     text        not null,

  hits              int         not null,          -- 命中几个榜（>=1，否则这行不该存在）
  hit_sources       text[]      not null,          -- 命中了哪几个榜（源键，顺序按命中榜里最好的名次）
  ranks             jsonb       not null default '{}',   -- {"voldbl": 12, "quadrant": 88, ...} 各榜里的名次
  sizes             jsonb       not null default '{}',   -- {"voldbl": 150, ...} 各榜这一场的榜长（分母）
  metrics           jsonb       not null default '{}',   -- 各榜自己的判据原值（榜是按它排的，就交它）

  best_source       text,                              -- 名次最好的那个榜是哪个源键
  best_rank         int,                               -- 它在的那个名次
  best_rank_pct     numeric(8,3),                      -- 该名次 / 该榜这一场的榜长 ×100，越小越靠前

  source_count      int         not null,              -- 这一场**有几个源真取到了数**（hits 的分母）
  sources_absent    text,                              -- 这一场哪几个源没数（逗号分隔源键），全有就 null

  missing           text,                              -- 这只票缺了哪些读数（逗号分隔源键），全有就 null
  spec_version      text        not null,

  calculated_at     timestamptz not null default now(),

  primary key (security_code, trade_date, spec_version),
  check (hits >= 1),
  check (hits = coalesce(array_length(hit_sources, 1), 0))
);

comment on table  analytics.confluence_daily            is '选股汇总（交叉命中榜）：各条量化链当天选出的票收到一张表上，看谁被多个判据同时点到。一物一行、只落命中的票；各榜选法只住 app/compute/confluence.py 的 SOURCES。排序口径＝命中榜数优先、其次各榜最好名次，没有单一合成分。每日加工段步骤名 confluence';
comment on column analytics.confluence_daily.hits       is '命中几个榜（1~source_count）。这是这一屏的主尺子：几个**独立**判据同时点到这只票。注意各榜不是互相独立的（倍量榜的票本来就常排在象限前面），所以榜数多不等于概率高，只等于"值得多看一眼"';
comment on column analytics.confluence_daily.hit_sources is '命中了哪几个榜的源键数组，按各榜名次从好到差排。源键含义：voldbl=倍量形态前 150、quadrant=四维象限前 200、ambush=埋伏票前 150、first_break=突破首破、dgt=龙虎榜';
comment on column analytics.confluence_daily.ranks      is '各榜里的名次 jsonb（1 = 该榜第一名）。名次是在**该榜自己的选法**（同一版本条件、同一排序、同一取数上限）下数出来的，与那一屏页面上看到的是同一个名次';
comment on column analytics.confluence_daily.sizes      is '各榜这一场的榜长 jsonb，即名次那一格的分母。榜长小于取数上限时说明该场真的只有这么多票（不是被截断）';
comment on column analytics.confluence_daily.metrics    is '各榜自己的判据原值 jsonb（榜就是按它排的）：voldbl→倍量次数 dbl_count、quadrant→oper 综合分、ambush→埋伏分与是否严档、first_break→突破放量倍数、dgt→净买额占成交额比';
comment on column analytics.confluence_daily.best_source is '名次最好的那个榜的源键。两只票命中数相同时靠它定先后，所以它必须是**同榜可比**的名次而不是跨榜的分数';
comment on column analytics.confluence_daily.best_rank_pct is '最好那名 / 该榜这一场榜长 ×100（%）。越小越靠前。跨榜比较只用它，不用名次本身 —— 象限榜 200 长的第 30 名和首破榜 37 长的第 30 名不是一回事';
comment on column analytics.confluence_daily.source_count is '这一场取到数的源个数（hits 的分母）。某条链当天没跑时它会变小，页面必须把它念出来 —— 否则「只命中 2 个榜」会被读成"判据不认同它"，其实是有两个榜当天没算';
comment on column analytics.confluence_daily.sources_absent is '这一场没数的源键（逗号分隔），全有则 null。缺席的源不参与 hits，也不填 0';
comment on column analytics.confluence_daily.missing    is '这只票在某源被选中但那一格的判据原值是 NULL 时，把源键记在这里。缺的读数一律 NULL，不按 0 参与排序';
comment on column analytics.confluence_daily.spec_version is '汇总口径版本（含各榜的选法与取数上限）。改了任何一个榜的选法就换版本号，旧行留着对账';

create index if not exists confluence_daily_date_idx  on analytics.confluence_daily (trade_date);
create index if not exists confluence_daily_hits_idx  on analytics.confluence_daily (trade_date, hits desc, best_rank_pct asc nulls last);
create index if not exists confluence_daily_src_idx   on analytics.confluence_daily using gin (hit_sources);
create index if not exists confluence_daily_code_idx  on analytics.confluence_daily (security_code, trade_date desc);

grant select, insert, update, delete on analytics.confluence_daily to ft_etl, ft_app;
grant select on analytics.confluence_daily to ft_reader;
