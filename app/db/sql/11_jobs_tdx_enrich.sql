-- =============================================================================
-- 11 · 通达信分类增强作业登记（c17~c20 挂进 /jobs 可手动触发）
--
-- 2026-09-22/23 补的四个通达信本地数据入库脚本，登记成作业方便在 /jobs 点触。
-- 全部 kind='ingest'、trigger_kind='manual'、only_trade_day=false：
--   c17 读 tdxzs*.cfg 文件、c18~c20 走 TQ 取通达信口径的分类/成分/除权/档案，
--   都跟"今天是不是交易日"无关，周末复盘时点它照样该把最新档案刷进来。
-- c18~c20 依赖通达信客户端已登录（TQ 是连到运行中客户端的本地 RPC）；
--   没登录时脚本会在 tq_bridge.available() 那一步照实退出、不假装成功。
-- runner 绑定写在 params.runner，对应 app/orchestrator/runner.py 的 RUNNERS。
-- =============================================================================

INSERT INTO config.job
    (job_key, name, kind, caps, trigger_kind, cron_expr, ready_on, after_job,
     scope, params, only_trade_day, concurrency_key, timeout_sec)
VALUES
    ('sync_tdx_sector', '通达信板块全集+分类', 'ingest', '{}', 'manual', NULL, NULL, NULL,
     '{"universe":"all"}', '{"runner":"ingest_tdx_sector"}', false, 'ingest', 900),
    ('sync_tdx_membership', '通达信板块成分(TQ)', 'ingest', '{}', 'manual', NULL, NULL, NULL,
     '{"universe":"all"}', '{"runner":"ingest_tdx_membership"}', false, 'ingest', 1800),
    ('sync_tdx_dividend', '通达信除权除息(TQ)', 'ingest', '{}', 'manual', NULL, NULL, NULL,
     '{"universe":"all"}', '{"runner":"ingest_tdx_dividend"}', false, 'ingest', 3600),
    ('sync_tdx_profile', '通达信个股档案(TQ)', 'ingest', '{}', 'manual', NULL, NULL, NULL,
     '{"universe":"all"}', '{"runner":"ingest_tdx_profile"}', false, 'ingest', 3600)
ON CONFLICT (job_key) DO UPDATE SET
    name = EXCLUDED.name,
    kind = EXCLUDED.kind,
    trigger_kind = EXCLUDED.trigger_kind,
    params = EXCLUDED.params,
    only_trade_day = EXCLUDED.only_trade_day,
    concurrency_key = EXCLUDED.concurrency_key,
    timeout_sec = EXCLUDED.timeout_sec,
    updated_at = now();
