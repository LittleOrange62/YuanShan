-- -*- coding: utf-8 -*-
-- 62_index_volume_snap.sql
-- 五大指数（上证/深证/创业板/科创/北证）+ 两市的**量能快照**：一天两针、一针六行。
--
-- 为什么单独落这一张（用户 2026-11-05 定）：
--   「这个脚本最好写进数据库里。每天五大指数和整体指数的量能变化趋势，每天需要有两次入库：
--     一次是中午复盘，一次是晚上复盘。」
--   推送正文只有"当时那一屏"，事后要回答"11 月量能一路怎么走、那天的位置分位是多少、
--   贴的是低位反转还是高位反转"——正文里没有序列可查，必须有一张自己的账。
--
-- 两针的时点与口径（⚠️ 同一天两行口径**不同**，读侧不许混）：
--   · snap_kind='noon'  12:10 午间复盘（t05）：amount=今早累计（腾讯分钟 11:30），
--     yoy=前一交易日**同一 11:30** 累计；北证 50 无分钟源 ⇒ 那行 amount 永远 NULL。
--   · snap_kind='close' 18:18 每日复盘（t09）：amount=全天（本地 .day 尾行是当天用 .day，
--     否则腾讯分钟 15:00；北证 50 只有 .day），yoy=前一交易日全天。
--
-- 位置与判断（11-05 复盘后定参，锚点＝2026-06-22 两市 37,372 亿峰值一路缩到 10-22 的
-- 15,287 亿、近 5 日回升到 20,377 亿那段形态）：
--   · pctile250 = 当前值在近 **250 个全天**里的**加权分位**（0~1）：权重按距尾行天数
--     指数衰减、**半衰期 60 天**（近端权重更大——6 月的峰值随时间淡出，位置说的是
--     "相对最近的常态"，不是相对三年前）。窗尾 = asof_date。
--   · trend_5d_pct = 近 5 个全天 末值/初值 − 1（百分数）。
--   · label = 位置 × 趋势 九宫（位置档 低<30/中30~70/高>70，趋势带 ±5%）：
--     低位反转｜低位徘徊｜低位下行｜中位上行｜中位徘徊｜中位下行｜高位放量｜高位徘徊｜高位反转
--     —— **阈值规则贴的标签，不是预测**（与情绪档位同一纪律）。
--
-- 口径纪律（沿用 53/54 号 DDL）：
--   · 缺数一律 NULL 并在 missing 里点名，**不写 0**；0 是真实读数，NULL 才是"判不了"。
--   · asof_date = 分位窗口用到的**最后一个完整全天**（.day 未必落到当天；noon 时最迟是
--     前一交易日）。界面上"位置 48%"是哪一个交易日的口径，看这一列，不猜。
--   · spec_version 进主键：口径改版换号，旧针留着对账，不覆盖。
--   · 金额单位**元**（amount_yuan 是"那一针到那时刻的累计成交额"）；*pct 一律百分数。

create table if not exists analytics.index_volume_snap (
    trade_date        date        not null,           -- 快照所属交易日
    snap_kind         text        not null check (snap_kind in ('noon','close')),
    index_name        text        not null,           -- 上证/深证/创业板/科创/北证/两市
    spec_version      text        not null,           -- 口径版本（当前 'v1'：窗250/半衰60/档30-70/带±5%）

    asof_date         date,                           -- 分位窗尾（.day 可能未落到 trade_date）；窗不足 ⇒ null
    amount_yuan       numeric(24,2),                  -- 那一针到那时刻的累计成交额（元）；取不到 ⇒ null（≠0）
    yoy_pct           numeric(8,2),                   -- 对前一交易日同一针：百分数（noon=前日11:30；close=前日全天）
    pctile250         numeric(6,3),                   -- 加权 250 日全天分位 0~1（半衰期 60 天）；窗不足 ⇒ null
    trend_5d_pct      numeric(8,2),                  -- 近 5 全天 末/初 − 1（百分数）
    label            text,                           -- 九宫标签（低位反转/中位上行/高位反转…）；判不了 ⇒ null
    source            text,                           -- 来源串（腾讯分钟/.day/both…）；写侧留痕在留痕表，行内可为 null
    missing           jsonb       not null default '{}'::jsonb,  -- 键名＝上面列名，值＝为什么缺
    calculated_at     timestamptz not null default now(),

    constraint index_volume_snap_pkey primary key (trade_date, snap_kind, index_name, spec_version)
);

create index if not exists ix_index_volume_snap_day
    on analytics.index_volume_snap (trade_date desc, snap_kind, index_name);

comment on table analytics.index_volume_snap is
    '五大指数+两市量能快照：一天两针（noon=12:10 午间复盘早间累计 / close=18:18 每日复盘全天），一针六行。位置=250 日加权分位（半衰 60 天），label=位置×趋势九宫（阈值规则，非预测）。';
comment on column analytics.index_volume_snap.snap_kind is
    'noon 的 amount/yoy 是**早间口径**（11:30 累计），close 是**全天口径**——同一天两行不许混读（北证 50 行在 noon 永远 amount=null）。';
comment on column analytics.index_volume_snap.asof_date is
    '分位/趋势/label 用的是截至该交易日的 .day 全天序列——.day 没落到当天时它就是前一日或更早，界面念"位置"要连带这一列。';
comment on column analytics.index_volume_snap.label is
    '九宫：位置(低<30/中30~70/高>70，250 日加权分位)×趋势(近5全天 ±5%)。低位反转/低位徘徊/低位下行/中位上行/中位徘徊/中位下行/高位放量/高位徘徊/高位反转。阈值规则标签，不是预测。';
comment on column analytics.index_volume_snap.amount_yuan is
    'null=这一格取不到（missing 点名），不是 0——0 是"零成交"的真实读数，本项目里不存在。';

grant select on analytics.index_volume_snap to ft_reader;
grant select, insert, update, delete on analytics.index_volume_snap to ft_etl, ft_app;
grant select on analytics.index_volume_snap to ft_app;
