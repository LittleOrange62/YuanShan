-- =============================================================================
-- 14 · qd 轨阈值参数落 config.setting
--
-- 09 §6 原本把 config.setting 的写入划给 code：系统配置，但**那条划分不可执行**：
-- 它连 PG 用的是 ft_reader（实测 has_table_privilege(...,'INSERT')=false），写不了这张表。
-- 2026-09-24 总协调改口让本会话用 ft_etl 跑，所以这个文件由 code：数据加工 落。
-- 幂等 ON CONFLICT DO NOTHING：已有人先写过就以先写的为准，不覆盖。
--
-- ⚠️ 三个计算作业对这三个键是**硬只读依赖**（app/compute/qd_common.py:setting()，
--    读不到直接抛 KeyError，不退回代码内默认值）。键缺失时 /jobs 上这三个作业必失败，
--    这是要的失败方式：阈值属于口径，代码里写一份表里写另一份，两份不一致没人会发现。
--
-- config.setting 没有备注列，"逻辑来源"只能写进 label（该表列：key/value(jsonb)/
-- label/category/updated_at，PK 是 key）。category 用 'compute'，与 d1~d7 计算类同侧。
-- =============================================================================

INSERT INTO config.setting (key, value, label, category)
VALUES
    ('qd_spec_version', '"qd-1"'::jsonb,
     'qd 轨（quantdash 口径双轨）规格版本号。逻辑来源：quantdash-ai-stock-main'
     '（PolyForm Noncommercial），仅重实现算法口径。改阈值必须升版，不许静默改口径。'
     '规格见 数据加工工作指南/09-quantdash逻辑规格.md',
     'compute'),
    ('qd_turnover_board_pct', '5'::jsonb,
     '换手板阈值（百分数）：非一字 且 当日换手率>=此值 记为换手板。'
     '本轨扩展，quantdash 全库无此概念（09 §4.2 is_turnover_board），'
     '落库行的 source 因此标 ''quantdash-logic+local''',
     'compute'),
    ('qd_sector_top_n', '24'::jsonb,
     '板块轮动每日参与排名的板块数上限。照搬 quantdash 东财 clist 前 24'
     '（出处 fetch_sector_snapshots.py:16-45）。注意 quantdash 自己的 MCP 版取 20 '
     '且无名称 tie-break（D-20），本轨统一按 24 + (pct desc, name asc, code asc)',
     'compute')
ON CONFLICT (key) DO NOTHING;
