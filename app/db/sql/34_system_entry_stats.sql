-- 交易体系榜的**历史分布**（三档 × 一套前瞻读数，滚动窗口）。
--
-- 为什么要单独落一张表，而不是页面读的时候现算：
--   那一组读数要扫全市场票·日（250 场窗口 ≈ 135 万行）并取每一根的未来 20 根。
--   2026-10-14 本机实测：**250 场 261 秒、60 场 40 秒**，而只读角色有 60 秒
--   `statement_timeout` —— 现算的第一后果不是慢，是**查询被超时咬死、页面把"没算出来"说成
--   "这一场没有数据"**（假读数）。第一版就是把分布放在读侧现算，浏览器实测 62 秒后拿到的正是
--   available:false ＋ 一句错误的理由。所以这里沿用本仓一贯的"判据落库 + 只读接口"：
--   每天加工段算一次（约 261 秒），页面只查这张表（这一条查询 ~0.1 秒；那一屏整包 1.1~1.2 秒）。
--
-- 母体就是 `analytics.system_entry`（①② 同时成立才进那张表），所以：
--   「只满足①」那一档在本表数不出来 —— 它的数字在 33_system_entry.sql 的列注释与功能点文档 §9.2 里，
--   由独立探针 `.local/system3_*.py` 量得。两处的窗口/口径不同，数字**不要求相等**，
--   但方向必须一致；本表是三档同窗并排的唯一权威。
--
-- ⚠️ 这张表的列**住在两份文件里**：本文件建表，`51_system_entry_avg_days.sql`（2026-10-22）
--   加「后 2/3/4/5 日均值」四列与它们各自的分母。回验列数要两份一起读
--   （`tests/test_system_entry.py::test_分布列名与DDL对得上` 就是按这个口径写的）。
--
-- 三档的口径（`bucket_key` 稳定，`bucket` 是给人看的句子、只住 app/compute/system_entry.py 一份）：
--   baseline  同期全部票·日（分母 = 窗口内所有满足"价量健全"的票·日）
--   c12       ①②：门票 + 缺口三天不回补（= system_entry 全表，含观察池）
--   entry     ①②③ 全过 + 今日阳线确认（entry_ok）
--
-- 每档都算同一套前瞻读数（**一律 lead，不是 lag**；帧 rows between 1 following and 20 following）：
--   n            该档样本（票·日）
--   win_next     次日上涨占比 %（分母 = 次日有数的那些行）
--   avg_next     次日均值 %
--   up10_med     后 10 场内盘中最高 ÷ 当日收盘 −1 的中位 %
--   up10_p90     同一量的 90 分位 %（右尾有多肥）
--   p20_up20     后 20 场内盘中冲出 +20% 的占比 %
--   d20_med      后 20 场复利收益的中位 %
--   hit_take     20 场内先碰 +20% 的占比 %（那句"带好止损，冲"的路径）
--   hit_stop     20 场内先碰 −7% 的占比 %
--   rule_expect  按这条规则走的期望 %（先到止损记 −7、先到止盈记 +20、两个都没碰到按第 20 场收盘算）

create table if not exists analytics.system_entry_stats (
  bucket_key    text        not null,      -- baseline / c12 / entry
  bucket        text        not null,      -- 给人看的档位名（单源于计算模块）
  window_days   smallint    not null,      -- 滚动窗口长度（场次）
  window_from   date        not null,      -- 窗口第一场
  window_to     date        not null,      -- 窗口最后一场（= 加工日）
  spec_version  text        not null,      -- 与判据同版本：判据改版，分布自然跟着换版

  n             integer,
  win_next      numeric(8,3),
  avg_next      numeric(8,3),
  up10_med      numeric(8,3),
  up10_p90      numeric(8,3),
  p20_up20      numeric(8,3),
  d20_med       numeric(8,3),
  hit_take      numeric(8,3),
  hit_stop      numeric(8,3),
  rule_expect   numeric(8,3),

  missing       text,                      -- 哪些读数缺（逗号分隔列名）；缺的留 NULL 不按 0
  bars_scanned  integer,                   -- 窗口内参与计算的票·日（透明化分母）
  calculated_at timestamptz not null default now(),

  primary key (bucket_key, window_to, window_days, spec_version)
);

comment on table  analytics.system_entry_stats is
  '交易体系榜的历史分布：三档（基线 / ①② / ①②③全过）在同一个滚动窗口里并排的前瞻读数。'
  '每天由加工段 sysentry 步算一次（250 场窗口实测 261 秒），页面只读这张表。'
  '次日/后 N 日一律看未来根（lead 语义，按 bar_no 自连接取未来 20 根，不是在窗口帧里自比）；'
  '本表不落判据本身，判据在 analytics.system_entry。';
comment on column analytics.system_entry_stats.bucket_key is '稳定档标识：baseline / c12 / entry。前端与接口都按它着色，不靠中文串匹配';
comment on column analytics.system_entry_stats.window_days is '滚动窗口（场次）。当前每日链只算 WINDOW_DAYS=250 一档；加档要在 app/compute/system_entry.py 改常量，页面不自己拼窗口';
comment on column analytics.system_entry_stats.window_to   is '窗口最后一场。主键含它 ⇒ 每天的滚动分布都留着，能看这套体系的边际随时间变化，不是只存"最新一份"';
comment on column analytics.system_entry_stats.p20_up20    is '后 20 场内的**盘中最高**相对当日收盘冲出 +20% 的占比 %。2026-10-14 首批落库（窗口 2025-09-24~2026-10-13 的 250 场）：基线 17.1、①② 30.5、①②③全过 28.0 —— 右尾变肥这一刀主要来自 ①②，第三条（缩量回调＋阳线确认）在本窗口里没有再加肥';
comment on column analytics.system_entry_stats.hit_take    is '20 场内**先**碰 +20%（后复权盘中最高）的占比 %：基线 14.0、①② 20.7、全过 19.3';
comment on column analytics.system_entry_stats.hit_stop    is '20 场内**先**碰 −7%（后复权盘中最低）的占比 %：基线 51.3、①② 69.7、全过 69.2。与 hit_take 同窗并排读 —— 那句"带好止损"的量在这里：先止损比先止盈常见得多';
comment on column analytics.system_entry_stats.rule_expect is '按 +20%/−7% 这条规则走的期望 %（先到止损记 −7、先到止盈记 +20、都没碰到按第 20 场收盘算）：基线 +0.21、①② −0.52、全过 −0.83 ⇒ 这套组合在本机数据上是**波动筛选器**，不是优势筛选器';
comment on column analytics.system_entry_stats.bars_scanned is '窗口内参与计算的票·日（基线分母，三档共用同一个数）。首批落库 1,346,760；自连接要求"未来至少还有一根"，所以每票最后一根不进分母 —— 现算要 261 秒的就是这一批行，这张聚合表存在的理由';

create index if not exists system_entry_stats_latest_idx on analytics.system_entry_stats (window_days desc, window_to desc, spec_version);
create index if not exists system_entry_stats_bucket_idx on analytics.system_entry_stats (bucket_key, window_to desc);

grant select, insert, update, delete on analytics.system_entry_stats to ft_etl;
grant select on analytics.system_entry_stats to ft_app, ft_reader;
