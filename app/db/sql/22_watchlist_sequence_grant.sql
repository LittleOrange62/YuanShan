-- 22 · 把 `config.watchlist_group_group_id_seq` 的使用权交给 ft_etl
--
-- 症状（2026-09-29/30 实测）：常驻的「自选同步」作业（`duanxian/watchlist_sync.py`，
-- 每天 20:40 那一轮）**从未成功过、连败 91 次**，`last_error` 先是
-- `NotNullViolation: added_at`（已在代码侧改为 `coalesce(%s, now())`，
-- 因为 `SelfStockInfo.json` 只覆盖 1,008 个代码而 ini 里有 1,093 个），
-- 修掉那一条之后接着暴露出这一条：
--
--     InsufficientPrivilege: permission denied for sequence watchlist_group_group_id_seq
--
-- 根因：`config.watchlist_group` 的建表授权给了 ft_etl 表级 `arwd`，
-- 但**序列是另一类对象**，默认 ACL 只给属主 ft_admin，`ft_app` 单独被授过 `rU`，
-- 而 `ft_etl` 一次都没授过 —— 于是"能插表、不能取号"。
-- 只在 ini 里出现**新组名**的那一天才会走到取号这一步，所以这条错平时看不见，
-- 一旦撞上就是整笔事务回滚：一票失败 = 整份自选没同步。
--
-- 只授 `usage, update`（nextval 需要 update），**不授 select/alter/owner 之外的东西**，
-- 也不批量对所有序列开权：库里另有 8 个序列同样没有 ft_etl 的权限，
-- 但那几张表（config.binding / field_spec / job / page_layout / screen_preset / script、
-- operations.schedule_log、ys.reminder）的写入方是 ft_app 或 ft_reader，
-- 没有证据说明 etl 要动它们 —— 没有调用点的授权就是白扩攻击面。
-- 要等哪条作业真的撞上它自己的序列，再照这一条的样子单独补。
grant usage, update on sequence config.watchlist_group_group_id_seq to ft_etl;

comment on sequence config.watchlist_group_group_id_seq is
    '自选组主键。ft_etl 有 usage+update（迁移 22，2026-09-30）：'
    '同花顺 ini 同步在"出现新组名"那天必须取号，缺这一步整笔事务回滚。';
