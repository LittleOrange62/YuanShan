-- -*- coding: utf-8 -*-
-- 20_ui_pref.sql
-- 界面选择的落库位置（用户 2026-09-28 点名：「默认最近一次看过的票，不要总是变来变去，
-- 可以在 pg 建一张临时表记录最近一次的选择」）。
--
-- 为什么放库里而不是 localStorage：localStorage 只在这台浏览器里，换浏览器/清缓存/
-- 换机器就会回到默认值 —— 而"回到默认值"在这种页面上等于**换了一只票**，
-- 用户看到的表就和他上次看的不是同一只，这正是他说的"变来变去"。
--
-- 为什么是一张键值表而不是 `scores` 页专用的一列：这套"上次选的是什么"的诉求
-- 不只趋势评分一处（自选股选股、板块页选板块都一样）。一张表 + 键名，
-- 判据与校验都留在 `duanxian/ui_pref.py` 一处；页面里各存一份 localStorage 键名
-- 才是真的会漂。
--
-- ⚠️ 这张表存的是**界面状态**，不是用户手写的记录：清库可以随便清，
-- 但别和 `ys.review_note` 那种"人写的东西"混在一张表里 —— 后者一条都不能丢。

create table if not exists ys.ui_pref (
  key         text           not null,
  value       text           not null default '',
  note        text           not null default '',
  updated_at  timestamptz    not null default now(),
  constraint ui_pref_pk primary key (key)
);

comment on table  ys.ui_pref is '界面状态（最近选中的标的/分组等）。键的合法形状与取值校验只在 duanxian/ui_pref.py 一处';
comment on column ys.ui_pref.key   is '形如 scores.last_code —— 页面.用途。新键必须先在 PREFS 注册表里登记，未登记的键写入直接拒';
comment on column ys.ui_pref.value is '存的是字符串值（代码/键名一类），按各键自己的判据校验；空串表示"没有选择"';
comment on column ys.ui_pref.note  is '谁在什么时候为什么写的一行说明，排"这个值是哪来的"时用';

grant select, insert, update, delete on ys.ui_pref to ft_app, ft_etl;
grant select on ys.ui_pref to ft_reader;
