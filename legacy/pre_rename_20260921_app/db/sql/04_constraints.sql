-- =============================================================================
-- FinanceTool 04 · 库层约束、触发器、视图、权限
-- 执行身份：ft_admin，连接库 financetool
-- 幂等：可重复执行
--
-- 为什么这些逻辑放在数据库而不是应用层：
--   本项目取消了 MCP/HTTP 网关，智能体直连 PG。应用层校验可以被下一个脚本
--   忽略，数据库约束不行 —— 绕不过去。这是去掉封装层后校验的落脚点。
-- =============================================================================

-- =============================================================================
-- 一、CHECK 约束
-- =============================================================================

DO $$
BEGIN
    -- OHLC 顺序
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_daily_ohlc') THEN
        ALTER TABLE md.stock_daily ADD CONSTRAINT ck_daily_ohlc CHECK (
            low IS NULL OR high IS NULL OR (
                low <= high
                AND (open  IS NULL OR (open  >= low AND open  <= high))
                AND (close IS NULL OR (close >= low AND close <= high))));
    END IF;

    -- 价格必须为正
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_daily_price_positive') THEN
        ALTER TABLE md.stock_daily ADD CONSTRAINT ck_daily_price_positive CHECK (
            (open IS NULL OR open > 0) AND (close IS NULL OR close > 0)
            AND (high IS NULL OR high > 0) AND (low IS NULL OR low > 0));
    END IF;

    -- 成交额量级：单位若误按千元/万元写入会撞穿上限
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_daily_amount_sane') THEN
        ALTER TABLE md.stock_daily ADD CONSTRAINT ck_daily_amount_sane CHECK (
            amount_yuan IS NULL OR (amount_yuan >= 0 AND amount_yuan <= 1e13));
    END IF;

    -- 成交量量级
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_daily_vol_sane') THEN
        ALTER TABLE md.stock_daily ADD CONSTRAINT ck_daily_vol_sane CHECK (
            vol_shares IS NULL OR (vol_shares >= 0 AND vol_shares <= 1e13));
    END IF;

    -- 涨跌幅区间
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_daily_pct') THEN
        ALTER TABLE md.stock_daily ADD CONSTRAINT ck_daily_pct CHECK (
            pct_chg IS NULL OR (pct_chg >= -100 AND pct_chg <= 1000));
    END IF;

    -- 板块伪代码不得进入个股表（旧系统靠事后 clean_880.py 删）
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_daily_not_sector') THEN
        ALTER TABLE md.stock_daily ADD CONSTRAINT ck_daily_not_sector CHECK (
            left(ts_code, 3) NOT IN ('880', '881'));
    END IF;

    -- 代码格式
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_daily_code_fmt') THEN
        ALTER TABLE md.stock_daily ADD CONSTRAINT ck_daily_code_fmt CHECK (
            ts_code ~ '^[0-9]{6}\.(SH|SZ|BJ)$');
    END IF;

    -- 分钟线
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_minute_period') THEN
        ALTER TABLE md.stock_minute ADD CONSTRAINT ck_minute_period CHECK (period IN (1, 5));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_minute_ohlc') THEN
        ALTER TABLE md.stock_minute ADD CONSTRAINT ck_minute_ohlc CHECK (
            low IS NULL OR high IS NULL OR low <= high);
    END IF;

    -- 证券板块取值
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_security_board') THEN
        ALTER TABLE ref.security ADD CONSTRAINT ck_security_board CHECK (
            board IS NULL OR board IN ('主板', '创业板', '科创板', '北交所'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_security_market') THEN
        ALTER TABLE ref.security ADD CONSTRAINT ck_security_market CHECK (
            market IN ('SH', 'SZ', 'BJ'));
    END IF;
END $$;


-- =============================================================================
-- 二、单位一致性触发器
--
-- 成交额 / 成交量 = 当日均价，必须落在 [low*0.5, high*2]。
-- 单位错 100 倍（股/手）或 1000 倍（元/千元）必然撞出这个区间。
-- 旧系统这两个偏差存在了三个月无人发现 —— 有这一条它们活不过第一次写入。
-- =============================================================================

CREATE OR REPLACE FUNCTION md.trg_daily_sanity() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE avg_price numeric;
BEGIN
    IF NEW.vol_shares IS NOT NULL AND NEW.vol_shares > 0
       AND NEW.amount_yuan IS NOT NULL AND NEW.amount_yuan > 0
       AND NEW.low IS NOT NULL AND NEW.high IS NOT NULL AND NEW.low > 0 THEN
        avg_price := NEW.amount_yuan / NEW.vol_shares;
        IF avg_price < NEW.low * 0.5 OR avg_price > NEW.high * 2.0 THEN
            RAISE EXCEPTION
                '单位口径异常 % %：amount_yuan/vol_shares=% 超出价格区间 [%, %]。'
                '请确认 amount_yuan 单位为元、vol_shares 单位为股。',
                NEW.ts_code, NEW.trade_date, round(avg_price, 4), NEW.low, NEW.high;
        END IF;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS daily_sanity ON md.stock_daily;
CREATE TRIGGER daily_sanity
    BEFORE INSERT OR UPDATE ON md.stock_daily
    FOR EACH ROW EXECUTE FUNCTION md.trg_daily_sanity();


-- 涨跌停按 board 计算（NULL 优于错误值）
CREATE OR REPLACE FUNCTION ref.board_limit_ratio(p_board text, p_is_st boolean)
RETURNS numeric LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN p_board IS NULL THEN NULL
        WHEN p_board = '北交所' THEN 0.30
        WHEN p_board IN ('创业板', '科创板') THEN 0.20
        WHEN p_board = '主板' AND p_is_st THEN 0.05
        WHEN p_board = '主板' THEN 0.10
        ELSE NULL
    END
$$;
COMMENT ON FUNCTION ref.board_limit_ratio(text, boolean) IS
    '按板块返回涨跌停幅度。旧系统一刀切 pre_close*1.1，创业板/科创板/北交所全错。';


-- =============================================================================
-- 三、配置变更审计与历史（FOR EACH STATEMENT，无逐行开销）
-- =============================================================================

CREATE OR REPLACE FUNCTION ops.trg_audit_stmt() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO ops.audit(actor, channel, action, target, params, ok)
    VALUES (current_user, 'direct', TG_OP,
            TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
            jsonb_build_object('app', coalesce(current_setting('application_name', true), ''),
                               'pid', pg_backend_pid()),
            true);
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION ops.trg_config_history() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE k text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        k := coalesce((to_jsonb(OLD) ->> 'id'), (to_jsonb(OLD) #>> '{}'));
        INSERT INTO ops.config_history(actor, table_name, row_key, op, before, after)
        VALUES (current_user, TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
                left(coalesce(k, ''), 200), TG_OP, to_jsonb(OLD), NULL);
        RETURN OLD;
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO ops.config_history(actor, table_name, row_key, op, before, after)
        VALUES (current_user, TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
                NULL, TG_OP, to_jsonb(OLD), to_jsonb(NEW));
        RETURN NEW;
    ELSE
        INSERT INTO ops.config_history(actor, table_name, row_key, op, before, after)
        VALUES (current_user, TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
                NULL, TG_OP, NULL, to_jsonb(NEW));
        RETURN NEW;
    END IF;
END $$;

-- 给所有 config.* 表挂历史触发器（行级，因为要存变更前后完整行以支持回滚）
DO $$
DECLARE t record;
BEGIN
    FOR t IN
        SELECT c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'config' AND c.relkind = 'r'
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS confighist ON config.%I', t.relname);
        EXECUTE format(
            'CREATE TRIGGER confighist AFTER INSERT OR UPDATE OR DELETE ON config.%I '
            'FOR EACH ROW EXECUTE FUNCTION ops.trg_config_history()', t.relname);
    END LOOP;
END $$;


-- =============================================================================
-- 四、视图
-- =============================================================================

-- 字段口径字典：智能体查表结构时能直接看到单位与计算口径
CREATE OR REPLACE VIEW ops.v_field_dict AS
SELECT f.table_name, f.column_name, f.label_cn, f.unit, f.dtype,
       f.value_range, f.sane_range, f.formula, f.source_key, f.display
FROM config.field_spec f
ORDER BY f.table_name, f.column_name;

-- 生效权重：按 enabled 归一化，停用一个子因子后剩下的自动补齐
CREATE OR REPLACE VIEW config.v_effective_binding AS
SELECT b.cap_key,
       s.script_key,
       s.name AS script_name,
       s.path,
       s.version,
       s.checksum,
       b.binding_id,
       b.script_id,
       b.seq,
       b.params,
       b.weight AS raw_weight,
       CASE WHEN sum(b.weight) OVER (PARTITION BY b.cap_key) = 0 THEN 0
            ELSE b.weight / sum(b.weight) OVER (PARTITION BY b.cap_key)
       END AS eff_weight
FROM config.binding b
JOIN config.script s ON s.script_id = b.script_id
WHERE b.enabled AND s.enabled
ORDER BY b.cap_key, b.seq;

-- 评分拆解
CREATE OR REPLACE VIEW fct.v_score_detail AS
SELECT d.ts_code, sec.name, d.trade_date, d.cap_key,
       s.value AS total_value, s.quadrant, s.is_stale,
       d.part_key, d.part_label, d.part_value, d.weight, d.contribution,
       d.script_key, d.script_ver
FROM fct.score_detail d
JOIN fct.score s USING (ts_code, trade_date, cap_key)
LEFT JOIN ref.security sec ON sec.ts_code = d.ts_code;

-- 数据覆盖度：/data 页面热力图数据源
CREATE OR REPLACE VIEW ops.v_coverage AS
SELECT 'md.stock_daily'::text AS dataset_key, trade_date, count(*) AS row_count
FROM md.stock_daily GROUP BY trade_date
UNION ALL
SELECT 'md.index_daily', trade_date, count(*) FROM md.index_daily GROUP BY trade_date
UNION ALL
SELECT 'md.sector_daily', trade_date, count(*) FROM md.sector_daily GROUP BY trade_date;

-- 最新交易日（走日历表，不是 weekday()<5）
CREATE OR REPLACE VIEW ref.v_latest_trade_date AS
SELECT max(cal_date) AS latest
FROM ref.trade_calendar
WHERE is_open
  AND (cal_date < current_date
       OR (cal_date = current_date AND localtime >= time '15:00'));


-- =============================================================================
-- 五、权限
-- =============================================================================

GRANT USAGE ON SCHEMA md, ref, fct, config, ops TO ft_app, ft_etl, ft_reader;

-- 只读角色
GRANT SELECT ON ALL TABLES    IN SCHEMA md, ref, fct, config, ops TO ft_reader;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA md, ref, fct, config, ops TO ft_reader;

-- 主程序：业务表读写，无 DDL
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA md, ref, fct, config, ops TO ft_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA md, ref, fct, config, ops TO ft_app;

-- ETL：md/ref 可写，其余只读
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA md, ref TO ft_etl;
GRANT SELECT, INSERT, UPDATE ON ops.ingest_batch, ops.data_ready, ops.quality_result,
                                ops.audit, ops.migration_baseline TO ft_etl;
GRANT SELECT ON ALL TABLES IN SCHEMA fct, config TO ft_etl;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA md, ref, ops TO ft_etl;

-- 以后新建的表自动授权，不用每次补
ALTER DEFAULT PRIVILEGES IN SCHEMA md, ref, fct, config, ops GRANT SELECT ON TABLES TO ft_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA md, ref, fct, config, ops
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ft_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA md, ref GRANT SELECT, INSERT, UPDATE ON TABLES TO ft_etl;
ALTER DEFAULT PRIVILEGES IN SCHEMA md, ref, fct, config, ops
    GRANT USAGE, SELECT ON SEQUENCES TO ft_app, ft_etl;
ALTER DEFAULT PRIVILEGES IN SCHEMA md, ref, fct, config, ops GRANT SELECT ON SEQUENCES TO ft_reader;
