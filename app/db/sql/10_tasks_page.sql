-- ============================================================ 10 「调度任务大全」页
-- 加 `config.menu` 一行：/tasks 挂在**运维**分组，紧跟在「调度器」(135) 后面。
--
-- 为什么单独一个迁移而不是改 07_scheduler.sql：07 已经应用到实库并且
-- operations.schema_migration 里记了它的 sha256。改了它会让"库里已应用的版本"
-- 和"仓库里的文件"对不上，而 record_checksums() 是无条件 upsert，会把差异抹平 ——
-- 于是再也没人看得出 07 被事后动过。编号往后加才是这套"编号 SQL + checksum"
-- 机制本来的用法。
--
-- 列名用 `display_order` 而不是 `seq`：菜单表的排序列在 2026-09-22 的全库改名里
-- 换过名字（同批还有 md/ref/ops/fct → market_data/reference_data/operations/
-- analytics）。照着旧脚本抄会直接 INSERT 失败。
--
-- 页面本身能兜底：`pages.resolve()` 找不到专属页时走 generic 配置页，
-- 所以菜单和代码是"两边都得有"的关系 —— 这里只补菜单那一半。
INSERT INTO config.menu (menu_key, label, route, icon, display_order, group_name, need_role) VALUES
('tasks', '调度任务大全', '/tasks', 'list-checks', 136, '运维', NULL)
ON CONFLICT (menu_key) DO UPDATE SET
    label = EXCLUDED.label, route = EXCLUDED.route, icon = EXCLUDED.icon,
    display_order = EXCLUDED.display_order, group_name = EXCLUDED.group_name;
