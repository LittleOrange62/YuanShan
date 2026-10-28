-- -*- coding: utf-8 -*-
-- 45_score_legacy_archive.sql
-- 评分旧读数归档表。存在的理由只有一句：**重算会就地吃掉对照基准**。
--
-- 背景（2026-10-19 现查）：`analytics.score` 里 `is_stale=true` 的 227,985 行是旧算法迁入批
-- （列注释原话「算法已改，不能与新结果混着比」，实测横跨 2026-01-27..2026-07-27 共 119 场）。
-- 而写侧 `app/compute/scoring.py::SCORE_UPSERT` 是 `ON CONFLICT DO UPDATE` **且无条件把
-- is_stale 写成 false** —— 这张表**没有 spec_version 那一维**（对照 42/43/44 号 DDL 都带版本，
-- 它是最早的 02_schema.sql 建的，没补上）。⇒ 一旦按现行算法回算历史场，那批旧读数不是"被标旧"，
-- 是**逐行被覆盖掉、再也拿不回来**；而绝大多数读端根本不过滤 `is_stale`
-- （confluence / 名次稳定榜 / d5 趋势 / 个股档案 / screen / watchlist…），改数会一起变。
--
-- 用户 2026-10-19 的裁定是「先把 stale 批归档，再重算」（不是加 spec_version 那条结构性改造，
-- 也不是就地覆盖不留档）。所以这里只建两张**只进不出**的影子表：
--   · `score_legacy_archive`        ＝ 主表同一批行的快照 + 批次标签 + 归档时刻；
--   · `score_detail_legacy_archive` ＝ 分项明细同理（它连 is_stale 都没有，覆盖得更彻底）。
-- 归档动作本身不在这里跑（那是 `INSERT … SELECT … WHERE trade_date BETWEEN …`，一条一次性的话），
-- 本文件只保证：**结构在、权限在、"为什么要有它"这句话在**。
--
-- 三条纪律：
--   ① `archived_batch` 必填且进主键 —— 同一 (票, 日, 键) 允许被不同批次各存一次，
--      第二批不许把第一批盖掉（那正是本表要避免的那种"覆盖"）。
--   ② 归档表**只 INSERT、不 UPDATE**：它是证据不是工作表。ft_etl 有 delete 权限只是因为
--      运维要能撤错批，不代表可以随便清（清了这句话就又变成"没验证过的备份"那种形状）。
--   ③ 归档完必须回查 `min/max(trade_date)`、场数、行数三项并写进台账 ——
--      「我 INSERT 了」不等于「归档成功了」（这条纪律在本项目被验证过太多次）。

create table if not exists analytics.score_legacy_archive (
    security_code    text        not null,
    trade_date       date        not null,
    capability_key   text        not null,
    value            numeric(16,4),
    rank_in_market   integer,
    percent_rank     numeric(8,4),
    quadrant         text,
    task_id          bigint,
    is_stale         boolean,
    calculated_at    timestamptz,
    archived_batch   text        not null,
    archived_at      timestamptz not null default now(),
    constraint score_legacy_archive_pkey
        primary key (security_code, trade_date, capability_key, archived_batch)
);

create table if not exists analytics.score_detail_legacy_archive (
    security_code    text        not null,
    trade_date       date        not null,
    capability_key   text        not null,
    component_key    text        not null,
    component_label  text,
    component_value  numeric(16,4),
    weight           numeric(8,4),
    contribution     numeric(16,4),
    script_key       text,
    script_version   text,
    archived_batch   text        not null,
    archived_at      timestamptz not null default now(),
    constraint score_detail_legacy_archive_pkey
        primary key (security_code, trade_date, capability_key, component_key, archived_batch)
);

create index if not exists score_legacy_archive_date_idx
    on analytics.score_legacy_archive (archived_batch, trade_date desc);
create index if not exists score_detail_legacy_archive_date_idx
    on analytics.score_detail_legacy_archive (archived_batch, trade_date desc);

comment on table analytics.score_legacy_archive is
    '评分重算前的旧读数快照（只进不出）。一行 = (票, 日, 能力键, 归档批次)；主表没有 spec_version，'
    '重算会就地覆盖，所以对照基准只能靠这张表留着';
comment on table analytics.score_detail_legacy_archive is
    '分项明细的旧读数快照。主表 score_detail 连 is_stale 都没有 ⇒ 覆盖后无任何字段能看出它是旧算法的';
comment on column analytics.score_legacy_archive.archived_batch is
    '哪一批归档（如 pre_recompute_20261019）。进主键，第二批不许盖第一批';
comment on column analytics.score_legacy_archive.is_stale is
    '归档当时主表里那一行的 is_stale 原值（true=旧算法迁入批）。留着它，以后才分得清"哪些场本来就被标旧"；'
    '归档动作本身按 trade_date 范围取，不靠这一格筛（范围外若有 stale 行是另一件事，要另起一批）';
comment on column analytics.score_legacy_archive.calculated_at is
    '被归档那一行在主表里的最后计算时刻（原样抄，不重算）—— 判断"这份旧读数是哪一批跑出来的"就靠它';

grant select, insert, delete on analytics.score_legacy_archive to ft_etl, ft_app;
grant select                 on analytics.score_legacy_archive to ft_reader;
grant select, insert, delete on analytics.score_detail_legacy_archive to ft_etl, ft_app;
grant select                 on analytics.score_detail_legacy_archive to ft_reader;
