-- 51 · 交易体系榜的历史分布：加「后 2 / 3 / 4 / 5 日均值」四列，以及它们各自的样本数
--
-- 谁点的：用户 2026-10-22「交易体系榜（三条同时成立才算）除了次日均值，再加几列：
--          2日均值、3日均值、4日均值、5日均值」。
-- 语义（与写侧 app/compute/system_entry.py 的 STATS_SQL 逐字对齐，改判据只改那一处）：
--   从当日收盘起**连续 N 个场次的涨跌幅按复利累乘**后的平均值 %，
--   即 `100 × (Π(1 + pct_i/100) − 1)`，i 跑过未来第 1..N 根。
--   ⚠️ 不是"第 N 天那一天的涨幅"：那样一半单日、一半累计，四个数摆在一排就会被读成
--   "持有越久越赚"。这样定义才与「次日均值」（N=1 时二者恒等）和「后20复利中位」同一把尺。
-- 为什么连样本数一起落：`bar_no` 是这只票自己的日线序号，凑不齐 N 根的锚点会被剔掉，
--   所以**窗口越长分母越小**。只交均值不交分母，"后5日均值"就会被当成与"次日均值"同一批样本；
--   分母是"这一列从多少票·日里平均出来的"，与 n（该档样本数）不是一回事。
-- 可重复执行（add column if not exists），跑法见《数据加工工作指南/04》；
--   落库后补一发数：python -m app.compute.system_entry --stats-only（约 261 秒）。

alter table analytics.system_entry_stats
  add column if not exists avg_2d numeric(8,3),
  add column if not exists avg_3d numeric(8,3),
  add column if not exists avg_4d numeric(8,3),
  add column if not exists avg_5d numeric(8,3),
  add column if not exists n_2d  integer,
  add column if not exists n_3d  integer,
  add column if not exists n_4d  integer,
  add column if not exists n_5d  integer;

comment on column analytics.system_entry_stats.avg_2d is
  '后 2 日均值 %：从当日收盘起连续 2 个场次涨跌幅的**复利累计**平均值（不是第 2 天单日的涨幅）。'
  'NULL＝算不出（这一窗里没一根凑齐 2 根，或这一批是这几列落库前算的旧批次 —— 后者读侧会自己念出来）。'
  '分母看 n_2d。"日"是场次、不是日历日。';
comment on column analytics.system_entry_stats.avg_3d is '后 3 日均值 %，口径同 avg_2d（连续 3 个场次复利累计）';
comment on column analytics.system_entry_stats.avg_4d is '后 4 日均值 %，口径同 avg_2d（连续 4 个场次复利累计）';
comment on column analytics.system_entry_stats.avg_5d is
  '后 5 日均值 %，口径同 avg_2d（连续 5 个场次复利累计）。'
  '这四列与「次日均值」连成一串，回答的是"拿 N 天平均几个点"；横向比时要连 n_2d…n_5d 一起看。';
comment on column analytics.system_entry_stats.n_2d is
  '这一档里凑齐后 2 根的锚点票·日数（＝ avg_2d 的分母）。窗口越长这个数越小，'
  '所以它必须与均值一起落库：只交均值就会把不同批样本摆成一排比大小';
comment on column analytics.system_entry_stats.n_3d is 'avg_3d 的分母，口径同 n_2d';
comment on column analytics.system_entry_stats.n_4d is 'avg_4d 的分母，口径同 n_2d';
comment on column analytics.system_entry_stats.n_5d is 'avg_5d 的分母，口径同 n_2d';

grant select, insert, update, delete on analytics.system_entry_stats to ft_etl;
grant select on analytics.system_entry_stats to ft_app, ft_reader;
