-- =============================================================================
-- FinanceTool 01 · 角色与数据库
-- 执行身份：postgres（超级用户），连接 maintenance 库 postgres
-- 幂等：可重复执行
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ft_admin') THEN
        CREATE ROLE ft_admin  LOGIN PASSWORD 'FtAdmin#2026!Tata'  CREATEDB;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ft_app') THEN
        CREATE ROLE ft_app    LOGIN PASSWORD 'FtApp#2026!Tata';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ft_etl') THEN
        CREATE ROLE ft_etl    LOGIN PASSWORD 'FtEtl#2026!Tata';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ft_reader') THEN
        CREATE ROLE ft_reader LOGIN PASSWORD 'FtRead#2026!Tata';
    END IF;
END $$;

COMMENT ON ROLE ft_admin  IS '库 owner，可 DDL，用于迁移建表';
COMMENT ON ROLE ft_app    IS 'FinanceTool 主程序，业务 schema 读写，无 DDL';
COMMENT ON ROLE ft_etl    IS '同步与批量导入，md/ref 可写，其余只读';
COMMENT ON ROLE ft_reader IS '全库只读，智能体默认账号';

-- 角色级超时：防长事务拖死 autovacuum，防失控查询占满连接
ALTER ROLE ft_reader SET statement_timeout = '60s';
ALTER ROLE ft_reader SET idle_in_transaction_session_timeout = '5min';
ALTER ROLE ft_app    SET statement_timeout = '300s';
ALTER ROLE ft_app    SET idle_in_transaction_session_timeout = '10min';
ALTER ROLE ft_etl    SET statement_timeout = '30min';
ALTER ROLE ft_etl    SET idle_in_transaction_session_timeout = '30min';

-- 统一时区，避免 timestamptz 显示口径不一致
ALTER ROLE ft_admin  SET timezone = 'Asia/Shanghai';
ALTER ROLE ft_app    SET timezone = 'Asia/Shanghai';
ALTER ROLE ft_etl    SET timezone = 'Asia/Shanghai';
ALTER ROLE ft_reader SET timezone = 'Asia/Shanghai';
