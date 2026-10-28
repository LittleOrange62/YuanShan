-- ys 影子 schema：calc_base 的对账落点
--
-- 与 analytics.indicator_value 的结构一致，但**不分区**：双跑期只写对照用的那几场，
-- 22 个分区对几千行没有意义，而结构差异要在对账里显式说明而不是藏起来。
-- 等连续 ≥10 个交易日对账无差异、真要接管生产时再按 RANGE(trade_date) 重建。
create table if not exists ys.indicator_value (
    security_code text   not null,
    trade_date    date   not null,
    indicator_key text   not null,
    value         numeric(24,8),
    task_id       bigint,
    primary key (security_code, trade_date, indicator_key)
);
create index if not exists ix_ys_indval_date_key on ys.indicator_value (trade_date, indicator_key);

comment on table ys.indicator_value is
  'YuanShan 侧 calc_base 的影子输出，用于与 analytics.indicator_value 双跑对账。'
  '生产者 compute/indicators.py（移植自 FinanceTool app/compute/indicators.py，'
  '公式求值器 compute/expr.py 与 FT 那份逐字节相同）。'
  '对账判据：同 (security_code, trade_date, indicator_key) 键集相等、'
  '同键 value 完全相等（不是近似相等 —— 两边都是 round(float(v), decimals) 的产物）。'
  '通过前本表不是任何页面的数据源。';
