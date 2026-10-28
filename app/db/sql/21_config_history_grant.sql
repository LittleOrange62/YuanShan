-- -*- coding: utf-8 -*-
-- 21_config_history_grant.sql
-- 补一个漏掉的授权：`operations.config_history` 不给 `ft_etl` 写。
--
-- 症状（2026-09-28 20:40 实测）：自选同步作业整条失败，报
--   `InsufficientPrivilege: permission denied for table config_history`
-- 而不是"没同步到"。原因不在自选同步自己：`config.*` 每一张表都挂着 `confighist`
-- 触发器（`operations.trg_config_history()`），任何写入都会顺带往
-- `operations.config_history` 插一行审计历史。触发器是** SECURITY INVOKER **，
-- 所以插历史用的是**发起写入的那个角色**的权限。etl 能写 `config.watchlist_*`，
-- 却不能写历史表 → 一条 INSERT 直接把整个事务顶掉。
--
-- 为什么是授权而不是改代码把触发器跳过：审计历史正是这套 config 表的设计目的
-- （改自选分组是同花顺 ini 与本库之间的镜像动作，没有历史就无法回答"这一组什么时候变成这样的"）。
-- 跳过触发器等于为了跑通作业而把记录撕掉。
--
-- 只给 insert + select（写与自查），不给 delete：审计表不该被 ETL 角色删。
-- ft_app 早已有 arwd，这也是为什么只有走 etl 的那几条作业会红、页面改配置不红。

grant insert, select on operations.config_history to ft_etl;
grant usage on schema operations to ft_etl;

comment on table operations.config_history is
  'config.* 的写入审计历史（由 trg_config_history 触发器代写）。'
  '⚠️ 触发器是 SECURITY INVOKER：任何写 config.* 的角色都必须同时能往这张表 INSERT，'
  '否则那条写入会整体失败 —— ft_etl 缺授权就是这么把自选同步顶掉的（见 21_config_history_grant.sql）';
