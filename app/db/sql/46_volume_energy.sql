-- 46_volume_energy.sql
-- 倍量加权分：给 `analytics.volume_double` 加两列，把「倍量次」从硬计数换成可配的分档加权。
--
-- 为什么要这一列（用户 2026-10-19 的裁定）：老 `dbl_count` 只数「日量比 ≥ 2.0」的日子，
-- 1.8 倍与 1.0 倍同样算 0 次。本机实测（近 25 场全市场 149,938 个票·日）日量比分布：
-- <1.0 占 60.6%、1.0–1.5 占 28.6%、1.5–2.0 占 6.4%、2.0–2.5 只占 2.2%、≥5 占 0.28%、p99=3.33
-- ——「≥2 才算一次」这把尺只截到了最尾部 3.5% 的日子，中间那批温和放量全被抹平成 0。
-- 所以改成：每 0.1 一档（1.0–1.1 … 2.4–2.5）+ 尾三档 2.5–3.5 / 3.5–5.0 / ≥5.0，
-- 每档一个分值，占比＝分值 ÷ Σ分值，加权分＝40 场窗口内逐日累加。
-- 档表与默认分值只住 `app/compute/volume_energy.py`（BANDS / DEFAULT_SCORES）一处。
--
-- 三条设计决定，都别再顺手改：
--   ① **旧列不删、不再上屏**。`dbl_count` 仍在库里、每日链仍写（有 210 场存量与若干下游引用：
--      confluence / 个股档案 / 推送 / 突破信号）。界面与筛参改读 `dbl_energy`，
--      "退役"退的是展示与判据，不是数据 —— 非 git 项目里删列等于把证据也删了。
--   ② **`energy_fp` 不是装饰**。它是生效配置的指纹，落库时一起写；
--      改一次档表＝换指纹＝必须重算（用户明确要"以后每次更新都要回算覆盖"）。
--      读侧与对账循环现比指纹，对不上就老实说"这一档筛的还是旧批"。
--   ③ 本表主键 **缺 spec_version**（02_schema.sql 时代遗留，台账里挂着这条债）。
--      所以这里不加版本号、只加指纹：`energy_fp` 承担"这批数是按哪份口径算的"这件事。
--      真要同时留两份口径对照，那是另一场结构性改造（主键要重建、写入方全改），不在这次范围。

alter table analytics.volume_double add column if not exists dbl_energy numeric(10, 4);
alter table analytics.volume_double add column if not exists energy_fp  text;

comment on column analytics.volume_double.dbl_energy is
    '倍量加权分：40 个交易日内逐日按「日量比所在档的占比」累加。'
    '日量比＝当日成交量 ÷ 前 5 日均量（不含当日，与 dbl_count 同一把基准）。'
    '档表与分值比例只住 app/compute/volume_energy.py，配置在 config.setting[''volume_double.energy_profile'']。'
    '⚠️ 单位不是「次数」：占比是 分值÷Σ分值，所以满窗上限＝40×最高档占比（默认配置约 5.6），'
    '照旧口径填「≥6」会筛出 0 只 —— 界面必须交 scale.max_score 与实测分布。';

comment on column analytics.volume_double.energy_fp is
    '算这一行时生效的配置指纹（含量纲、作用域、每个市场摊平后的档表）。'
    '与当前指纹不等 ⇒ 这一行是旧批，界面与对账循环都要说出来；改配置后由 '
    'app.compute.volume_energy.rebuild() 覆盖。';

-- 新榜「达标后逐日走势」与倍量榜都按加权分筛/排，所以按 (场, 加权分) 建降序索引；
-- NULLS LAST 与读侧的 order by 保持一致，否则优化器不会用它。
create index if not exists ix_volume_double_energy
    on analytics.volume_double (trade_date, dbl_energy desc nulls last);

-- 应用角色读侧要能读、写侧要能写（与 25 号同一套 grant 姿势）
grant select, insert, update, delete on analytics.volume_double to ft_etl, ft_app;
grant select on analytics.volume_double to ft_reader;
