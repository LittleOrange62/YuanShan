-- -*- coding: utf-8 -*-
-- 21_sts_comment_caliber_fix.sql
-- `analytics.sector_trend_signal.heat_pctile` 的口径纠正注释（判据文档 docs/页面展示工作指南/05 §2 第 6 档）
--
-- 为什么需要这张工单：表是 ft_admin 建的（实测 pg_class.relowner），
-- `COMMENT ON COLUMN` 要 owner 权限，ft_etl/ft_app 都写不进去 —— 计算侧只能把语句准备好。
-- 而这条注释是**防误读的第一道闸**：列名 `heat_pctile` 和 `reasons.heat_rank` 看起来是一件事的两个说法，
-- 实际上方向相反（名次 1=最热，rank/universe 的 1.0 是最冷）。上一版就是这么错的。
--
-- 数据本身已经改完了（sts-2 全量 543,844 行 / 1,244 场已重算并逐行对过），
-- 这份工单只动注释，不动列、不动行、不动索引 —— 可以在任意时刻重跑，幂等。

comment on column analytics.sector_trend_signal.heat_pctile is
  '热度在**自身**长窗口（默认 21 场）里的滚动分位，0~1，1.0=正处于自身区间最高；与 analytics.ignition.detail->''_q_heat'' 同口径，只看过去、窗口含当日。'
  '⚠️ 不是「当日全场第几名」：横截面名次在 reasons.heat_rank + universe 里，两者方向相反 —— 名次 1=最热，所以 rank/universe 的 1.0 是最冷。'
  'sts-1 把 rank/universe 当分位喂给过热判据（>=0.9），实测把 369 行热度低于当日中位的板块判成过热（09-23 最冷的 TDX_880328 heat=-85.27 在其中），sts-2 已改。'
  '所以 regime=overheated 的含义是「在自己 21 日区间的顶部」，不等于「今天最热」；界面念这一档时必须把 reasons.heat_rank/universe 一起念出来。';

comment on column analytics.sector_trend_signal.regime is
  '互斥、按序判：insufficient→downtrend→turn_up_seed→forming→established→overheated，最后兜在 no_shape。'
  '两条顺序上的反直觉：(1) overheated 排在 established 之前判 —— 位置 0.95 恰恰是左侧交易最该避开的，先说不该伸手；'
  '(2) no_shape 与 insufficient 是两件事：点数够但方向/位置/广度没凑成形状，不是「数据不足」';

comment on column analytics.sector_trend_signal.dip_score is
  '低吸分。权重是**可商量的先验**（35/25/20/10/10），不是市场规律；只在 turn_up_seed/forming 下有值。'
  '缺项不补 0、也不归一化放大 —— 上限按在场权重求和，交在 reasons.dip_score_ceiling：'
  '一行 62 分而上限 80，读起来是「满分只有 80」，不是「它分数低」';

-- ------------------------------------------------------------------ 验收（跑完自己贴这四条的输出）

-- 1) 注释确实落上了
-- select a.attname, col_description(a.attrelid, a.attnum) from pg_attribute a
--   join pg_class c on c.oid = a.attrelid join pg_namespace n on n.oid = c.relnamespace
--  where n.nspname='analytics' and c.relname='sector_trend_signal'
--    and a.attname in ('heat_pctile','regime','dip_score');

-- 2) 口径落地：过热行里没有「既不是自身高位、也不是量比爆表」的
-- select count(*) from analytics.sector_trend_signal
--  where spec_version='sts-2' and regime='overheated'
--    and not ((coalesce(position,0)>=85 and coalesce(heat_pctile,0)>=0.9)
--             or (reasons->>'vol_ratio')::numeric>3);
-- 期望 0

-- 3) 自身分位与纯 SQL 窗口函数对账（8 万余行，期望 mismatch=0）
--    见 .local/sts_digest.py 与本次回报里的 with base/win 那条

-- 4) 覆盖
-- select count(*) rows, count(distinct trade_date) days, min(trade_date), max(trade_date)
--   from analytics.sector_trend_signal where spec_version='sts-2';

-- ------------------------------------------------------------------ 另一件事，先不动、等裁定
-- `ys.watchlist_member_snapshot`（owner ft_etl，实测 0 行）与
-- `reference_data.watchlist_member_snapshot`（owner ft_admin，09-26 起 1,283 行 / 41 组）是同一件事的两张表。
-- 裁定以 reference_data 那张为唯一落点（和 `sector_member` 同 schema，且每日追平链已经在写它）。
-- ys 那张要 DROP 的话请先由《数据获取》确认 `duanxian/watchlist_sync.py` 里的 SNAPSHOT_TABLE 已改指向
-- 或那段已停用 —— 这里刻意不写 drop：删表不在计算侧的授权范围内。
