-- =============================================================================
-- 13 · qd 轨作业登记（quantdash 口径双轨计算，先挂手动触发）
--
-- 契约：D:\finance\数据加工工作指南\09-quantdash逻辑规格.md §5。
-- 建表在 12_qd_schema.sql，这里只登记"谁来算"。
--
-- 三个作业全部 trigger_kind='manual'、cron_expr=NULL：09 §5 明写"先做手动触发，
-- 跑稳了再排定时"。调度器总开关本身也还是关的（config.setting 的 scheduler_enabled
-- =false / dry_run=true），所以这里就算填了 cron 也不会自动跑 —— 不如照实写 manual，
-- 免得 /jobs 页面看起来像已经自动化了。
--
-- 依赖链（硬约束，写在 after_job 里）：
--   calc_qd_ladder  →  calc_qd_sentiment
-- 情绪轨道直接读 qd.board_ladder_member / board_ladder_leader / stock_strength
-- 的连板分布与触板未封池，不从原始日线另推一遍。两轨各推一遍迟早自相矛盾 ——
-- quantdash 自己就是三套阈值互相打架（09 §4.1）。所以单独触发 calc_qd_sentiment 而
-- ladder 没跑过那天时，**那一天整行不写**并打日志，不补一行假的。
-- calc_qd_sector 独立，只读 market_data.sector_daily。
--
-- recent 参数不是装饰：晋级率/涨停溢价/面板炸板率四列依赖 **T+1** 的日线
-- （09 §4.1，本轨 deviation 编码 L-3）。日常增量必须连着重算前一天，否则最新交易日
-- 这四列永久空着。sentiment/ladder 取 2；sector 的持续性窗口是末 5 个交易日故取 5。
--
-- ⚠️ 这三个作业还跑不起来：它们硬依赖 config.setting 的 qd_spec_version /
--    qd_turnover_board_pct / qd_sector_top_n，而 09 §6 把那张表的写入权划给
--    code：系统配置，所以本文件**不插值**。键缺失时作业直接抛 KeyError，
--    不会退回代码里的默认值 —— 那是要的失败方式。
--
-- ST 生效区间脚本（`python -m ingest.jobs.qd0_st_period`）**不登记成作业**：
-- 它是一次性反证回填，且每日增量并无意义（历史 ST 区间缺源，见 12 文件表注释）。
-- =============================================================================

INSERT INTO config.job
    (job_key, name, kind, caps, trigger_kind, cron_expr, ready_on, after_job,
     scope, params, only_trade_day, concurrency_key, timeout_sec)
VALUES
    ('calc_qd_ladder', 'quantdash 口径 · 连板天梯', 'compute', '{}', 'manual', NULL,
     ARRAY['market_data.stock_daily'], NULL,
     '{"universe":"all"}',
     '{"runner":"compute_qd_ladder","args":{"recent":2}}',
     true, 'compute', 1800),

    ('calc_qd_sector', 'quantdash 口径 · 板块周期', 'compute', '{}', 'manual', NULL,
     ARRAY['market_data.sector_daily'], NULL,
     '{"universe":"all"}',
     '{"runner":"compute_qd_sector","args":{"recent":5}}',
     true, 'compute', 900),

    ('calc_qd_sentiment', 'quantdash 口径 · 情绪周期', 'compute', '{}', 'manual', NULL,
     NULL, 'calc_qd_ladder',
     '{"universe":"all"}',
     '{"runner":"compute_qd_sentiment","args":{"recent":2}}',
     true, 'compute', 1800)
ON CONFLICT (job_key) DO UPDATE SET
    name = EXCLUDED.name,
    kind = EXCLUDED.kind,
    trigger_kind = EXCLUDED.trigger_kind,
    cron_expr = EXCLUDED.cron_expr,
    ready_on = EXCLUDED.ready_on,
    after_job = EXCLUDED.after_job,
    params = EXCLUDED.params,
    only_trade_day = EXCLUDED.only_trade_day,
    concurrency_key = EXCLUDED.concurrency_key,
    timeout_sec = EXCLUDED.timeout_sec,
    updated_at = now();
