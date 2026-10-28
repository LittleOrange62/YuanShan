-- =============================================================================
-- 09 · 同花顺本地补充参考数据 + 同花顺类作业的定时化
--
-- 2026-09-22 全量摸底同花顺本地文件后，还"可用但没入库"的明文只剩两样：
--   1. BrokerInfoUpdate\hkbrokerinfo —— 港股席位(经纪商)代码→名称，给以后
--      做港股龙虎榜/参与者分析当名字字典（现在没有港股本数据，先存档）。
--   2. <用户名>\syncConfig\function\*.hxf —— 用户在客户端攒的自定义指标公式，
--      带 hf1.0 魔数的能抽出公式原文，云端下载的没有魔数（加密），照实存 NULL。
--
-- 明确**不要**的：stocknametable.txt（只有 1654 条，远不全，名称以通达信管线
-- 为准）；alertlist.txt（预警代码不带市场号，认不出是哪只票，宁可不要）；
-- history\/realtime\ 的 hd1.0 行情缓存（专有压缩没破解，且只缓存看过的票）。
--
-- 定时化（用户 2026-09-22 要求"后期定时抓"）：本地 ini 随客户端每天刷新，
-- 三个同花顺作业从 manual 改成每天 20:20–20:40 的 cron；自选覆盖有
-- var\backup\watchlist_*.json 兜底，且库里目前没有任何手工备注会丢。
-- =============================================================================

CREATE TABLE IF NOT EXISTS reference_data.ths_hk_broker (
    broker_id   text        NOT NULL,     -- 席位代码（'0014'）
    name        text        NOT NULL,     -- 经纪商名（GBK 明文搬来）
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (broker_id)
);
COMMENT ON TABLE reference_data.ths_hk_broker IS
    '同花顺本地港股席位(经纪商)代码→名称字典，来源 BrokerInfoUpdate\hkbrokerinfo，随客户端刷新';

CREATE TABLE IF NOT EXISTS config.ths_user_formula (
    formula_key  text       NOT NULL,     -- hxf 文件名（同花顺内部指标 id）
    name         text       NOT NULL,     -- 文件头里的中文指标名
    source       text,                    -- hf1.0 后的公式原文；NULL=加密读不出
    synced_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (formula_key)
);
COMMENT ON TABLE config.ths_user_formula IS
    '同花顺客户端用户自定义指标档案（*.hxf 提取的公式原文），只存档不参与计算';

-- ---- 同花顺作业定时化。cron 挑 20 点后：客户端白天在线刷过 ini，晚上入库。
-- only_trade_day=false：读的是本地文件，跟交易日无关，周末编辑的自选照样该同步。
UPDATE config.job
   SET trigger_kind = 'cron', cron_expr = '20 20 * * *'
 WHERE job_key = 'sync_ths_block';

INSERT INTO config.job (job_key, name, kind, caps, trigger_kind, cron_expr, ready_on, after_job,
                        scope, params, only_trade_day, concurrency_key, timeout_sec) VALUES
('sync_ths_reference', '同步同花顺席位与指标档案', 'ingest', '{}',    'cron', '30 20 * * *', NULL, NULL,
    '{"universe":"all"}', '{"runner":"ingest_ths_reference"}', false, 'ingest', 600),
-- ★删组建成员的老问题（05_seed 注释里"故意不登记成作业"）在定时化之后依然成立：
-- 靠 import_watchlist() 的强制备份兜底 —— 每次覆盖前都落一份
-- var\backup\watchlist_*.json，误删照文件恢复。用户 2026-09-22 明确要定时镜像。
('sync_ths_watchlist', '镜像同花顺自选分组(全量覆盖)', 'ingest', '{}', 'cron', '40 20 * * *', NULL, NULL,
    '{"universe":"all"}', '{"runner":"ingest_ths_watchlist","args":{"yes":true}}', false, 'ingest', 600)
ON CONFLICT (job_key) DO UPDATE SET
    name = EXCLUDED.name, kind = EXCLUDED.kind, trigger_kind = EXCLUDED.trigger_kind,
    cron_expr = EXCLUDED.cron_expr, scope = EXCLUDED.scope, params = EXCLUDED.params,
    only_trade_day = EXCLUDED.only_trade_day, concurrency_key = EXCLUDED.concurrency_key,
    timeout_sec = EXCLUDED.timeout_sec, updated_at = now();
