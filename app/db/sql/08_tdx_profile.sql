-- =============================================================================
-- 08 · 通达信个股档案（get_more_info 快照）
--
-- 与 market_data.stock_snapshot 的分工：那张是 c15 收盘插件落的「估值/市值」快照，
-- 本表存的是通达信 TQ get_more_info 独有的「档案 + 盘面统计」——主营业务、
-- 涨跌停价、历史高低价、IPO 价、Beta、员工数、涨停/连板统计等，snapshot 里没有。
-- 全量字段另存 raw JSONB，需要时再挑列，不为了没查过的字段预先建列。
--
-- 一行 = 一只票的一个快照日（get_more_info 只给当前值，HqDate 即当日）。
-- =============================================================================

CREATE TABLE IF NOT EXISTS reference_data.stock_profile (
    security_code        text        NOT NULL,
    hq_date        date        NOT NULL,          -- HqDate，快照日
    main_business  text,                          -- MainBusiness 主营业务
    limit_up       numeric(14,4),                 -- ZTPrice 涨停价（元）
    limit_down     numeric(14,4),                 -- DTPrice 跌停价（元）
    turnover_pct   numeric(10,4),                 -- fHSL 换手率（%）
    pe_dyn         numeric(14,4),                 -- DynaPE 动态市盈率
    pe_static_ttm  numeric(14,4),                 -- StaticPE_TTM 静态市盈率TTM
    pb_mrq         numeric(14,4),                 -- PB_MRQ 市净率
    beta           numeric(10,4),                 -- BetaValue
    ipo_price      numeric(14,4),                 -- IPO_Price 发行价
    his_high       numeric(14,4),                 -- HisHigh 历史最高
    his_low        numeric(14,4),                 -- HisLow 历史最低
    staff_num      integer,                       -- StaffNum 员工数
    raw            jsonb       NOT NULL,          -- get_more_info 全字段
    source_batch_id   bigint      NOT NULL,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (security_code, hq_date)
);

CREATE INDEX IF NOT EXISTS ix_stock_profile_date ON reference_data.stock_profile (hq_date);

COMMENT ON TABLE reference_data.stock_profile IS
    '通达信个股档案快照（TQ get_more_info）：主营/涨跌停/历史高低/IPO/Beta/员工数等，'
    '与 stock_snapshot 互补。全字段存 raw JSONB。';
