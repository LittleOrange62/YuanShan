-- =============================================================================
-- FinanceTool 02 · schema 与业务表
-- 执行身份：ft_admin，连接库 financetool
-- 幂等：可重复执行
--
-- 命名约定（重要）
--   字段名自带单位：amount_yuan / vol_shares / turnover_rate_pct
--   这是消灭"文档写千元、代码写元"1000× 偏差的第一道防线
--   日期一律用 date 类型，不用 '20260825' 字符串
-- =============================================================================

COMMENT ON DATABASE financetool IS 'FinanceTool 股票复盘系统';

CREATE SCHEMA IF NOT EXISTS md;    COMMENT ON SCHEMA md  IS 'market data 行情';
CREATE SCHEMA IF NOT EXISTS ref;   COMMENT ON SCHEMA ref IS 'reference 证券基础信息/日历/板块/股本/分红';
CREATE SCHEMA IF NOT EXISTS fct;   COMMENT ON SCHEMA fct IS 'factor 指标/因子/评分';
CREATE SCHEMA IF NOT EXISTS config;   COMMENT ON SCHEMA config IS 'config 注册表/自选/用户';
CREATE SCHEMA IF NOT EXISTS ops;   COMMENT ON SCHEMA ops IS 'operations 任务/审计/血缘/质量';


-- =============================================================================
-- ref 域：基础信息（先建，md 的 board/涨跌停依赖它）
-- =============================================================================

CREATE TABLE IF NOT EXISTS ref.security (
    ts_code       text PRIMARY KEY,               -- '600367.SH'
    symbol        text NOT NULL,                  -- '600367'
    market        text NOT NULL,                  -- SH | SZ | BJ
    name          text,
    pinyin        text,                           -- 全拼，来自 .tnf
    pinyin_abbr   text,                           -- 首字母
    board         text,                           -- 主板|创业板|科创板|北交所  ← 决定涨跌停幅度
    asset_type    text NOT NULL DEFAULT 'stock',  -- stock|index|etf|sector|other
    is_st         boolean NOT NULL DEFAULT false, -- ST 股主板限 5%
    list_date     date,
    delist_date   date,
    total_share   bigint,                         -- 总股本（股）
    float_share   bigint,                         -- 流通股本（股）
    is_active     boolean NOT NULL DEFAULT true,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_security_symbol ON ref.security (symbol);
CREATE INDEX IF NOT EXISTS ix_security_type   ON ref.security (asset_type, is_active);
CREATE INDEX IF NOT EXISTS ix_security_board  ON ref.security (board);
CREATE INDEX IF NOT EXISTS ix_security_pinyin ON ref.security (pinyin_abbr);

CREATE TABLE IF NOT EXISTS ref.trade_calendar (
    cal_date  date PRIMARY KEY,
    is_open   boolean NOT NULL,
    prev_open date,
    next_open date
);

CREATE TABLE IF NOT EXISTS ref.sector_info (
    sector_code text PRIMARY KEY,                 -- '880301'
    name        text NOT NULL,
    sector_type text,                             -- 行业|概念|地区|风格
    level       int,
    parent_code text,
    is_active   boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS ref.sector_member (
    sector_code text NOT NULL REFERENCES ref.sector_info(sector_code) ON DELETE CASCADE,
    ts_code     text NOT NULL,
    in_date     date,
    out_date    date,
    PRIMARY KEY (sector_code, ts_code)
);
CREATE INDEX IF NOT EXISTS ix_sector_member_code ON ref.sector_member (ts_code);

-- 板块主键别名。B7 归一之前，同一个通达信板块在库里有两个主键：
-- 成员关系写成 `GN_880501`（概念）/ `FG_880xxx`（风格），而板块日线写成
-- `TDX_880501` —— 数字段完全相同，只有前缀不同，于是
-- `md.sector_daily` 与 `ref.sector_member` 的 sector_code 交集为 0，
-- 板块热度四个分项全部算不出来，且不报错（`ctx.broadcast()` 静默返回空值）。
--
-- B7 把成员关系迁到 `TDX_` 口径，旧主键在这里留一条别名记录：
-- 页面上的旧书签、旧导出文件里可能还带着 `GN_` 前缀，能查回来。
CREATE TABLE IF NOT EXISTS ref.sector_alias (
    alias_code  text PRIMARY KEY,                 -- 旧主键，如 GN_880501
    sector_code text NOT NULL,                    -- 归一后的主键，如 TDX_880501
    src         text,                             -- 旧前缀语义：概念 / 风格 / 指数 / 自定义
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_sector_alias_target ON ref.sector_alias (sector_code);


CREATE TABLE IF NOT EXISTS ref.dividend (
    ts_code       text NOT NULL,
    ex_date       date NOT NULL,                  -- 除权除息日
    cash_per_share numeric(16,6) DEFAULT 0,       -- 每股现金红利（元，税前）
    bonus_ratio   numeric(16,6) DEFAULT 0,        -- 每股送股比
    rights_ratio  numeric(16,6) DEFAULT 0,        -- 每股配股比
    rights_price  numeric(16,6),                  -- 配股价（元）
    PRIMARY KEY (ts_code, ex_date)
);

CREATE TABLE IF NOT EXISTS ref.share_capital (
    ts_code     text NOT NULL,
    change_date date NOT NULL,
    total_share bigint,                           -- 股
    float_share bigint,                           -- 股
    PRIMARY KEY (ts_code, change_date)
);


-- =============================================================================
-- md 域：行情
-- =============================================================================

CREATE TABLE IF NOT EXISTS md.stock_daily (
    ts_code           text        NOT NULL,
    trade_date        date        NOT NULL,
    open              numeric(12,4),
    high              numeric(12,4),
    low               numeric(12,4),
    close             numeric(12,4),
    pre_close         numeric(12,4),
    change_yuan       numeric(12,4),
    pct_chg           numeric(10,4),
    vol_shares        bigint,                     -- 单位：股
    amount_yuan       numeric(20,2),              -- 单位：元
    turnover_rate_pct numeric(10,4),
    limit_up          numeric(12,4),              -- 按 board 计算，NULL 表示未知
    limit_down        numeric(12,4),
    adj_factor        numeric(16,8),              -- 后复权因子，NULL 表示未计算
    src_batch_id      bigint      NOT NULL,       -- → ops.ingest_batch，强制血缘
    updated_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ts_code, trade_date)
) PARTITION BY RANGE (trade_date);

DO $$
DECLARE y int;
BEGIN
    FOR y IN 2015..2035 LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS md.stock_daily_%s PARTITION OF md.stock_daily '
            'FOR VALUES FROM (%L) TO (%L)', y, y || '-01-01', (y + 1) || '-01-01');
    END LOOP;
    EXECUTE 'CREATE TABLE IF NOT EXISTS md.stock_daily_default '
            'PARTITION OF md.stock_daily DEFAULT';
END $$;

CREATE INDEX IF NOT EXISTS ix_stock_daily_date  ON md.stock_daily (trade_date);
CREATE INDEX IF NOT EXISTS ix_stock_daily_brin  ON md.stock_daily USING brin (trade_date);
CREATE INDEX IF NOT EXISTS ix_stock_daily_batch ON md.stock_daily (src_batch_id);


CREATE TABLE IF NOT EXISTS md.index_daily (
    ts_code      text NOT NULL,
    trade_date   date NOT NULL,
    open         numeric(14,4),
    high         numeric(14,4),
    low          numeric(14,4),
    close        numeric(14,4),
    pre_close    numeric(14,4),
    pct_chg      numeric(10,4),
    vol_shares   bigint,
    amount_yuan  numeric(20,2),
    src_batch_id bigint NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS ix_index_daily_date ON md.index_daily (trade_date);


-- ETF / LOF / 分级基金日线。单独一张表而不是塞进 stock_daily，原因是口径不同：
--   vol 的单位是「份」不是「股」，且不同品种的最小交易单位不一致（100 份 / 1000 份）。
--   B3 迁移时预检发现旧库 51*/50*/56*/58* 共 90 万行的 amount/vol 落在
--   价格区间的 5 倍或 15 倍上 —— 旧库这批数据的单位本身就是错的，不能迁。
--   本表由 C 阶段从通达信 vipdoc 的 .day 重新解析填充（那里 vol/amount 的单位
--   在文件格式中是确定的），不从旧 SQLite 迁。
-- 不挂 md.trg_daily_sanity()：那个触发器按「股」的口径校验均价，对份不成立。
CREATE TABLE IF NOT EXISTS md.fund_daily (
    ts_code      text NOT NULL,
    trade_date   date NOT NULL,
    open         numeric(14,4),
    high         numeric(14,4),
    low          numeric(14,4),
    close        numeric(14,4),
    pre_close    numeric(14,4),
    pct_chg      numeric(10,4),
    vol_units    bigint,              -- 份，命名上就和 stock_daily.vol_shares 区分开
    amount_yuan  numeric(20,2),
    src_batch_id bigint NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS ix_fund_daily_date ON md.fund_daily (trade_date);


-- ETF 每日总份额（沪深两所官方口径）。存的是**存量份额**这个原始事实，
-- 净申赎是派生量，交给下面的视图算 —— 口径以后要改（比如剔除跨境、按类型拆）
-- 只动视图，不用重刷历史。
--
-- 为什么值得单独采：份额变动 = 一级市场净申购 − 净赎回，是二级市场成交额看不到的
-- 增量资金。实测 2026-08-26 沪深两所合计 −120.2 亿份，而当天指数是涨的 ——
-- 这种背离只有份额数据能看出来。
--
-- share_units 单位是「份」，不是万份也不是亿份（实测 510010 返回 130524400.0）。
-- 和 fund_daily.vol_units 同单位，可以直接乘 close 得金额。
CREATE TABLE IF NOT EXISTS md.etf_share (
    ts_code      text NOT NULL,          -- 带市场后缀，和 md.fund_daily 对齐
    trade_date   date NOT NULL,
    fund_name    text,
    etf_type     text,                   -- 上交所给：跨市 / 单市 / 跨境 / 其他；深交所不给，为 NULL
    share_units  numeric(20,2) NOT NULL, -- 份
    src_batch_id bigint NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ts_code, trade_date),
    CONSTRAINT ck_etf_share_nonneg CHECK (share_units >= 0)
);
CREATE INDEX IF NOT EXISTS ix_etf_share_date ON md.etf_share (trade_date);

-- 逐只算净申赎。**按 ts_code 分区做 LAG 是这里的关键**：新上市 ETF 首日
-- 没有前值，delta 自然是 NULL 而被排除。若改成「当日总份额 − 前日总份额」，
-- 首发份额会被计成申购 —— 实测 2026-08-26 深交所 4 只新 ETF 带来 +11.31 亿份，
-- 把当天真实的 −28.05 亿份净赎回冲成 −16.73 亿份，幅度失真 40%。
--
-- prev_trade_date 一并输出，便于识别中间是否漏采（漏一天则 delta 实际是两天累计）。
--
-- **asset_class 必须用来过滤**，否则算出来的不是股市资金流：
-- 实测 2026-08-25 净申赎 +85.88 亿份 / +2629 亿元，平均 30.6 元/份 ——
-- 因为里面混了 1000 元/份的债券 ETF（511130 国债30年、551500 科创债）
-- 和 100 元/份的货币 ETF。货币 ETF 是现金管理工具，它的份额进出跟股市方向无关，
-- 甚至常常反向（避险时申购货币 ETF）。看股市增量资金要 `asset_class = 'equity'`。
-- 交易所给的 etf_type 只有 跨市/单市/跨境/其他，分不出股/债/货币，只能按名称认。
-- CREATE OR REPLACE VIEW 不允许在中间插列（会报 cannot change name of view column），
-- 而这个视图的列还会随口径演进增删，所以每次先 DROP。视图无数据，重建代价为零。
DROP VIEW IF EXISTS md.v_etf_flow;
CREATE OR REPLACE VIEW md.v_etf_flow AS
SELECT s.ts_code,
       s.trade_date,
       s.fund_name,
       s.etf_type,
       CASE
           WHEN s.fund_name ~ '(货币|添益|现金|保证金|理财)'          THEN 'money'
           WHEN s.fund_name ~ '(国债|地债|政金|信用债|城投|公司债|可转债|债ETF|短融|债)' THEN 'bond'
           WHEN s.etf_type = '跨境'
             OR s.fund_name ~ '(恒生|恒指|港股|纳指|纳斯达克|标普|道琼斯|日经|德国|法国|沙特|东南亚|亚太|中概|美国)' THEN 'qdii'
           WHEN s.fund_name ~ '(黄金|白银|有色|豆粕|能化|原油|商品)'   THEN 'commodity'
           ELSE 'equity'
       END                                              AS asset_class,
       s.share_units,
       lag(s.share_units) OVER w                        AS prev_share_units,
       lag(s.trade_date)  OVER w                        AS prev_trade_date,
       s.share_units - lag(s.share_units) OVER w        AS delta_units,
       d.close,
       (s.share_units - lag(s.share_units) OVER w) * d.close AS delta_amount_yuan
  FROM md.etf_share s
  LEFT JOIN md.fund_daily d
         ON d.ts_code = s.ts_code AND d.trade_date = s.trade_date
WINDOW w AS (PARTITION BY s.ts_code ORDER BY s.trade_date);


-- 大盘资金流（东财口径）。**这是"资金去哪了"这个模块的骨架数据**：
-- 按单笔委托金额把成交拆成 超大单/大单/中单/小单，主力 = 超大单 + 大单。
--
-- 为什么值得单独存一张表：`md.stock_daily` 只有成交额，看不出"这笔钱是谁的"。
-- 同样 2 万亿成交，主力净流入 +538 亿（2026-08-27）和主力净流出 −200 亿，
-- 复盘结论完全相反。
--
-- 净占比是"净额 ÷ 当日总成交额"，跨日可比；净额单位是元，不要在库里换算成亿，
-- 展示层再折。
CREATE TABLE IF NOT EXISTS md.market_fund_flow (
    trade_date      date NOT NULL PRIMARY KEY,
    sh_close        numeric(14,4),          -- 上证收盘，用于对齐"钱进来了指数涨没涨"
    sh_pct          numeric(10,4),
    sz_close        numeric(14,4),
    sz_pct          numeric(10,4),
    main_net        numeric(20,2),          -- 主力净流入，元（= 超大单 + 大单）
    main_pct        numeric(10,4),          -- 净占比 %
    xl_net          numeric(20,2),          -- 超大单
    xl_pct          numeric(10,4),
    lg_net          numeric(20,2),          -- 大单
    lg_pct          numeric(10,4),
    md_net          numeric(20,2),          -- 中单
    md_pct          numeric(10,4),
    sm_net          numeric(20,2),          -- 小单
    sm_pct          numeric(10,4),
    src_batch_id    bigint NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- 个股与板块的主力资金流。东财 push2 域名 2026-08-27 实测掐连接，
-- 所以这张表可能是空的 —— 空表不影响"资金去哪了"页面，页面上的板块/个股
-- 去向用成交额份额变化这个**代理口径**先跑（见 app/web/queries.py 里的
-- q_fund_sector_* / q_fund_stock_*）。等域名恢复，c11_fundflow.py 一跑就有真数据。
CREATE TABLE IF NOT EXISTS md.fund_flow_rank (
    trade_date      date NOT NULL,
    scope           text NOT NULL,          -- stock / sector
    obj_code        text NOT NULL,          -- ts_code 或 sector_code
    obj_name        text,
    window_days     smallint NOT NULL,      -- 1 / 3 / 5 / 10：东财的今日/3日/5日/10日
    main_net        numeric(20,2),
    main_pct        numeric(10,4),
    pct_chg         numeric(10,4),
    src_batch_id    bigint NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_date, scope, obj_code, window_days),
    CONSTRAINT ck_fund_flow_scope CHECK (scope IN ('stock', 'sector')),
    CONSTRAINT ck_fund_flow_window CHECK (window_days IN (1, 3, 5, 10, 20))
);
CREATE INDEX IF NOT EXISTS ix_fund_flow_rank_lookup
    ON md.fund_flow_rank (trade_date, scope, window_days, main_net DESC);


CREATE TABLE IF NOT EXISTS md.sector_daily (

    sector_code  text NOT NULL,
    trade_date   date NOT NULL,
    open         numeric(14,4),
    high         numeric(14,4),
    low          numeric(14,4),
    close        numeric(14,4),
    pre_close    numeric(14,4),
    pct_chg      numeric(10,4),
    vol_shares   bigint,
    amount_yuan  numeric(20,2),
    up_count     int,
    down_count   int,
    member_count int,
    src_batch_id bigint NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sector_code, trade_date)
);
CREATE INDEX IF NOT EXISTS ix_sector_daily_date ON md.sector_daily (trade_date);


CREATE TABLE IF NOT EXISTS md.stock_minute (
    ts_code      text        NOT NULL,
    dt           timestamptz NOT NULL,            -- 完整时间戳，不用魔法日序
    period       smallint    NOT NULL,            -- 1 | 5
    open         numeric(12,4),
    high         numeric(12,4),
    low          numeric(12,4),
    close        numeric(12,4),
    vol_shares   bigint,                          -- 股
    amount_yuan  numeric(20,2),                   -- 元
    src_batch_id bigint      NOT NULL,
    PRIMARY KEY (ts_code, dt, period)
) PARTITION BY RANGE (dt);

DO $$
DECLARE y int; m int; d1 text; d2 text;
BEGIN
    FOR y IN 2024..2027 LOOP
        FOR m IN 1..12 LOOP
            d1 := format('%s-%s-01', y, lpad(m::text, 2, '0'));
            d2 := to_char((d1::date + interval '1 month')::date, 'YYYY-MM-DD');
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS md.stock_minute_%s%s PARTITION OF md.stock_minute '
                'FOR VALUES FROM (%L) TO (%L)', y, lpad(m::text, 2, '0'), d1, d2);
        END LOOP;
    END LOOP;
    EXECUTE 'CREATE TABLE IF NOT EXISTS md.stock_minute_default '
            'PARTITION OF md.stock_minute DEFAULT';
END $$;

CREATE INDEX IF NOT EXISTS ix_stock_minute_dt ON md.stock_minute USING brin (dt);


-- 快照类（PE/PB/市值等，来自 tqcenter）
CREATE TABLE IF NOT EXISTS md.stock_snapshot (
    ts_code        text NOT NULL,
    trade_date     date NOT NULL,
    pe_ttm         numeric(16,4),
    pb             numeric(16,4),
    total_mv_yuan  numeric(24,2),
    float_mv_yuan  numeric(24,2),
    eps            numeric(16,4),
    bps            numeric(16,4),
    roe_pct        numeric(16,4),
    extra          jsonb,
    src_batch_id   bigint NOT NULL,
    PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS ix_snapshot_date ON md.stock_snapshot (trade_date);


-- 融资融券逐股明细。**两个交易所给的列不一样**，本表是并集，缺的列留空：
--   上交所（stock_margin_detail_sse）给 融资余额/融资买入额/融资偿还额 /
--       融券余量/融券卖出量/融券偿还量，**不给融券余额(元)、不给融资融券合计**
--   深交所（stock_margin_detail_szse）给 融资买入额/融资余额/融券卖出量/
--       融券余量/融券余额/融资融券余额，**不给融资偿还额、不给融券偿还量**
-- 不为了"整齐"去自己补算对侧缺的列：融券余额要用当日券价折算，猜出来的数会被
-- 下游当成实测值用。
--
-- **深交所是 T+1 发布**：实测 2026-08-26 当天查 08-26 返回 0 行，查 08-25 正常。
-- 所以采集脚本默认同时拉「目标日」和「目标日-1」，空表跳过而不算失败。
--
-- Tushare 的 margin / margin_detail 在本项目的 token 下是 40203 无权限，
-- 这条链路只能走 akshare（交易所官网转发）。
CREATE TABLE IF NOT EXISTS md.margin_detail (
    ts_code       text NOT NULL,
    trade_date    date NOT NULL,
    rz_balance    numeric(20,2),        -- 融资余额（元）
    rz_buy        numeric(20,2),        -- 融资买入额（元）
    rz_repay      numeric(20,2),        -- 融资偿还额（元），仅上交所
    rq_volume     numeric(20,2),        -- 融券余量（股）
    rq_sell       numeric(20,2),        -- 融券卖出量（股）
    rq_repay      numeric(20,2),        -- 融券偿还量（股），仅上交所
    rq_balance    numeric(20,2),        -- 融券余额（元），仅深交所
    rzrq_balance  numeric(20,2),        -- 融资融券余额（元），仅深交所
    src_batch_id  bigint NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ts_code, trade_date),
    CONSTRAINT ck_margin_nonneg CHECK (
        coalesce(rz_balance, 0) >= 0 AND coalesce(rz_buy, 0) >= 0
        AND coalesce(rq_volume, 0) >= 0)
);
CREATE INDEX IF NOT EXISTS ix_margin_detail_date ON md.margin_detail (trade_date);

-- 融资余额趋势。用户要的是"变化趋势"，那就是环比 + 5/10/20 日变动率，
-- 以及余额占流通市值的比例（绝对金额没有可比性：茅台 100 亿融资余额和
-- 小盘股 100 亿完全不是一件事）。
--
-- LAG 的位移单位是「本表里的行」= 交易日，所以不用管自然日跨周末。
-- 但**前提是这只票每个交易日都采到了**；漏采会让 lag(5) 实际跨 6 个交易日，
-- 因此把 d5_date 一并输出，异常可自查。
DROP VIEW IF EXISTS md.v_margin_trend;
CREATE OR REPLACE VIEW md.v_margin_trend AS
SELECT m.ts_code,
       m.trade_date,
       m.rz_balance,
       lag(m.rz_balance, 1)  OVER w AS rz_d1,
       lag(m.rz_balance, 5)  OVER w AS rz_d5,
       lag(m.rz_balance, 10) OVER w AS rz_d10,
       lag(m.rz_balance, 20) OVER w AS rz_d20,
       lag(m.trade_date,  5) OVER w AS d5_date,
       CASE WHEN lag(m.rz_balance, 1) OVER w > 0
            THEN (m.rz_balance / lag(m.rz_balance, 1) OVER w - 1) * 100 END AS chg1_pct,
       CASE WHEN lag(m.rz_balance, 5) OVER w > 0
            THEN (m.rz_balance / lag(m.rz_balance, 5) OVER w - 1) * 100 END AS chg5_pct,
       CASE WHEN lag(m.rz_balance, 10) OVER w > 0
            THEN (m.rz_balance / lag(m.rz_balance, 10) OVER w - 1) * 100 END AS chg10_pct,
       CASE WHEN lag(m.rz_balance, 20) OVER w > 0
            THEN (m.rz_balance / lag(m.rz_balance, 20) OVER w - 1) * 100 END AS chg20_pct,
       -- 占流通市值比。float_share 缺失的票（北交所与部分老票）留空，不填 0
       CASE WHEN s.float_share > 0 AND d.close > 0
            THEN m.rz_balance / (s.float_share * d.close) * 100 END AS rz_of_float_pct
  FROM md.margin_detail m
  LEFT JOIN ref.security s ON s.ts_code = m.ts_code
  LEFT JOIN md.stock_daily d
         ON d.ts_code = m.ts_code AND d.trade_date = m.trade_date
WINDOW w AS (PARTITION BY m.ts_code ORDER BY m.trade_date);


-- 龙虎榜。**一只票同一天可能因多条原因上榜**（比如"日涨幅偏离值达 7%"
-- 与"连续三个交易日内涨幅达 20%"同时成立），所以主键必须带 reason，
-- 否则同批次内 ON CONFLICT 自己撞自己，只留下一条。
CREATE TABLE IF NOT EXISTS md.lhb_detail (
    ts_code           text NOT NULL,
    trade_date        date NOT NULL,
    reason            text NOT NULL,
    close             numeric(14,4),
    pct_chg           numeric(10,4),
    net_buy           numeric(20,2),   -- 龙虎榜净买额（元）
    buy_amount        numeric(20,2),
    sell_amount       numeric(20,2),
    lhb_amount        numeric(20,2),   -- 龙虎榜成交额（元）
    market_amount     numeric(20,2),   -- 该股当日市场总成交额（元）
    net_buy_ratio     numeric(10,4),   -- 净买额占总成交比（%）
    amount_ratio      numeric(10,4),
    turnover_rate_pct numeric(10,4),
    float_mv_yuan     numeric(24,2),
    interpret         text,            -- 东财的"解读"，如「3家机构买入，成功率 47.89%」
    src_batch_id      bigint NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ts_code, trade_date, reason)
);
CREATE INDEX IF NOT EXISTS ix_lhb_detail_date ON md.lhb_detail (trade_date);

-- 龙虎榜机构买卖统计。和 lhb_detail 分开是因为口径不同：这张只统计席位里
-- 的机构专用席位，游资/营业部不在内。判"是不是机构在买"要看这张。
CREATE TABLE IF NOT EXISTS md.lhb_inst (
    ts_code         text NOT NULL,
    trade_date      date NOT NULL,
    reason          text NOT NULL,
    buy_inst_count  int,
    sell_inst_count int,
    inst_buy        numeric(20,2),
    inst_sell       numeric(20,2),
    inst_net        numeric(20,2),
    market_amount   numeric(20,2),
    net_ratio       numeric(10,4),     -- 机构净买额占总成交额比（%）
    src_batch_id    bigint NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ts_code, trade_date, reason)
);
CREATE INDEX IF NOT EXISTS ix_lhb_inst_date ON md.lhb_inst (trade_date);


-- =============================================================================
-- fct 域：指标与评分
-- =============================================================================

CREATE TABLE IF NOT EXISTS fct.indicator_value (
    ts_code    text        NOT NULL,
    trade_date date        NOT NULL,
    ind_key    text        NOT NULL,
    value      numeric(24,8),
    task_id    bigint,
    PRIMARY KEY (ts_code, trade_date, ind_key)
) PARTITION BY RANGE (trade_date);

DO $$
DECLARE y int;
BEGIN
    FOR y IN 2015..2035 LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS fct.indicator_value_%s PARTITION OF fct.indicator_value '
            'FOR VALUES FROM (%L) TO (%L)', y, y || '-01-01', (y + 1) || '-01-01');
    END LOOP;
    EXECUTE 'CREATE TABLE IF NOT EXISTS fct.indicator_value_default '
            'PARTITION OF fct.indicator_value DEFAULT';
END $$;
CREATE INDEX IF NOT EXISTS ix_indval_date_key ON fct.indicator_value (trade_date, ind_key);


CREATE TABLE IF NOT EXISTS fct.score (
    ts_code        text        NOT NULL,
    trade_date     date        NOT NULL,
    cap_key        text        NOT NULL,
    value          numeric(16,4),
    rank_in_market int,
    pct_rank       numeric(8,4),
    quadrant       text,                          -- Q1|Q2|Q3|Q4
    task_id        bigint,
    is_stale       boolean     NOT NULL DEFAULT false,
    calculated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ts_code, trade_date, cap_key)
);
CREATE INDEX IF NOT EXISTS ix_score_date_cap ON fct.score (trade_date, cap_key);
CREATE INDEX IF NOT EXISTS ix_score_stale    ON fct.score (is_stale) WHERE is_stale;


-- 评分拆解：让评分不再是黑盒，页面上能看到每个子项的分值/权重/贡献
CREATE TABLE IF NOT EXISTS fct.score_detail (
    ts_code      text NOT NULL,
    trade_date   date NOT NULL,
    cap_key      text NOT NULL,
    part_key     text NOT NULL,                   -- 子项标识，如 'xingtai'
    part_label   text,                            -- '形态'
    part_value   numeric(16,4),
    weight       numeric(8,4),
    contribution numeric(16,4),
    script_key   text,
    script_ver   text,
    PRIMARY KEY (ts_code, trade_date, cap_key, part_key)
);
CREATE INDEX IF NOT EXISTS ix_score_detail_date ON fct.score_detail (trade_date, cap_key);


-- 个股操作分的窗口趋势（/scores 的「四象限趋势分」那张卡）。
-- 2026-08-31 从"页面每次现算"改成落库：现算一次 1.0s，而 /scores 是服务端
-- 一次性渲染，这 1.0s 直接加在首屏上；落库后那张卡只剩一次索引扫。
--
-- 口径与 fct.sector_trend 对齐：**存斜率（分/天）不存"累计变化分"**。
-- 页面上的 `趋势分数` = slope × win，是展示层的换算；表里存原始斜率，
-- 换窗口长度不用重刷历史，也不会出现"同一个数在两张表里量纲不同"。
--   win         窗口长度，单位**有评分的交易日**（不是日历日：长假一压，
--               10 个自然日里可能只剩 5 个交易日，斜率量纲就变了）
--   n_points    窗口内真有分的天数。**不足不补齐、不置 0**，如实记 ——
--               3 天数据也能拟合出一条斜率，但那不是 10 日趋势。
--               页面的 `min_n` 就是拿这一列过滤的
--   r           corr(oper, rn)，**存 r 不存 r²**：r 的符号是信息（和 slope
--               同号才自洽），r² 一开方就把它丢了。页面显示 r²
--   intercept   最小二乘截距，配 n_points 才能外推"下一天的预测分"
--   xy_max /
--   xy_max_date 窗口内 X+Y 的最高值与它出现在哪天。光有最大值会误导 ——
--               最大值出现在第 2 天还是今天，含义完全相反
--   up_streak   末尾连续上行的天数
--   quad_first  窗口第一天的象限，配当天的象限就是"象限迁移"
--   spark       ▁▂▃▄▅▆▇ 拼的迷你走势，**只表示形状不表示绝对高低**
--               （两只票的 ▇ 不是同一个分数）。它是窗口内逐日归一化的结果，
--               页面上重算等于把整段序列再拉一遍，所以跟着落库
CREATE TABLE IF NOT EXISTS fct.score_trend (
    ts_code       text        NOT NULL,
    trade_date    date        NOT NULL,
    win           smallint    NOT NULL,
    n_points      smallint,
    slope         numeric(16,6),
    intercept     numeric(16,6),
    r             numeric(10,6),
    xy_max        numeric(16,4),
    xy_max_date   date,
    up_streak     smallint,
    quad_first    text,
    spark         text,
    task_id       bigint,
    calculated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ts_code, trade_date, win),
    CONSTRAINT ck_score_trend_win CHECK (win >= 2)
);
CREATE INDEX IF NOT EXISTS ix_score_trend_date ON fct.score_trend (trade_date, win);


-- 板块级日热度。热度分算在**个股**上（sector_heat 由板块值广播到成员股），
-- 页面原来每次都反着从成员股聚回板块 —— 板块热度趋势那张卡 2.2s 全花在这上面，
-- 而且同一个数在三处 SQL 里各聚一遍。这里落一次，谁要用查这张表。
-- member_scored 是当天真有评分的成员数：热度是均值，3 只票的均值和 300 只票的
-- 均值可信度差一个量级，不把这个数摆出来，看到的人无法判断该不该信。
CREATE TABLE IF NOT EXISTS fct.sector_heat_daily (
    sector_code    text        NOT NULL,
    trade_date     date        NOT NULL,
    heat           numeric(16,4),
    member_scored  int,
    task_id        bigint,
    calculated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sector_code, trade_date)
);
-- 2026-08-30：热度口径从"成员股评分聚回板块"改成"直接从 md.sector_daily 算板块级"。
-- 原因：前者的历史被个股评分卡在 23 个交易日（2026-07-29 起），而"趋势启动点"
-- 要的是 60/120 日分位 —— 没有基线就没有"相对自己算不算异常"。四个分项脚本
-- 读的全是 md.sector_daily，所以板块级可以一次算到 2021（1,231 个交易日）。
--   src       'sector'     = 板块级直算（app/compute/sector_heat_hist.py，现行）
--             'member_agg' = 旧的成员股聚合口径（历史遗留行）
--   n_parts   四个分项里真有值的个数。1 项撑起来的热度和 4 项齐全的不是一回事，
--             必须能分辨；member_scored 只对 member_agg 口径有意义。
-- 用 ADD COLUMN IF NOT EXISTS 而不是直接改上面的 CREATE：现库已有 12,414 行，
-- CREATE TABLE IF NOT EXISTS 对已存在的表不补列。
ALTER TABLE fct.sector_heat_daily
    ADD COLUMN IF NOT EXISTS src     text     NOT NULL DEFAULT 'member_agg',
    ADD COLUMN IF NOT EXISTS n_parts smallint;
CREATE INDEX IF NOT EXISTS ix_sector_heat_daily_date

    ON fct.sector_heat_daily (trade_date);


-- 板块热度的多窗口趋势。**长表不是宽表**：窗口天数是配置
-- （config.setting['sector_trend.windows']），想加一个 13 日趋势只要改那个数组，
-- 做成 trend_3/trend_5/... 一堆列的话，每加一个窗口都要改 DDL + 改查询 + 改页面。
--   win      窗口长度，单位交易日。win=1 就是"日趋势分值"（今日热度 − 昨日热度）
--   slope    最小二乘斜率，单位**分/天**，向上为正、向下为负、0 为走平
--   n_points 真正参与拟合的点数。不足窗口长度时**不补齐、不置 0**，
--            只如实记下用了几个点 —— 3 个点也能算出斜率，但那不是 21 日趋势
CREATE TABLE IF NOT EXISTS fct.sector_trend (
    sector_code    text        NOT NULL,
    trade_date     date        NOT NULL,
    win            smallint    NOT NULL,
    slope          numeric(16,4),
    n_points       smallint,
    task_id        bigint,
    calculated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sector_code, trade_date, win),
    CONSTRAINT ck_sector_trend_win CHECK (win >= 1)
);
CREATE INDEX IF NOT EXISTS ix_sector_trend_date ON fct.sector_trend (trade_date, win);


-- 趋势启动点。**一张表管两种标的**（scope='sector' 板块 / 'group' 自选组）：
-- 两边的要素定义、阈值、分级完全一样，分两张表迟早漂移成两套口径。
--   stage   'coil'   蓄势 —— 压缩 + 低位都成立，还没点火（提前观察名单）
--           'ignite' 点火 —— 蓄势在**前几天**成立，今天量能/广度/拐头同时到位
--           'follow' 延续 —— 点火后仍在走（回答"错过第一天还能不能上"）
--           'fade'   退潮 —— 高位 + 斜率转负（反向清单，专治追高）
--           'none'   都不是
--   五个要素分各 0~100，越高越符合该要素；score 是加权合成
--   detail  存算分用到的原始值（放量倍数、成交额份额、上涨占比、3 日斜率、
--           热度分位…）。**必须留**：页面要能解释"凭什么说它点火了"，
--           人也要能拿这些数自己核一遍，光给一个分数没法验证
-- 判据里最容易错的一处：ignite 的前置条件读的是**信号日之前**的蓄势状态
-- （见 app/compute/ignition.py 的 PRE_DAYS）。用当天的压缩去筛"刚启动"永远筛不出来
-- —— 启动那一刻压缩本身已经被打破了。
CREATE TABLE IF NOT EXISTS fct.ignition (
    scope          text        NOT NULL,
    obj_key        text        NOT NULL,
    trade_date     date        NOT NULL,
    stage          text        NOT NULL,
    score          numeric(8,2),
    squeeze        numeric(8,2),
    pos            numeric(8,2),
    ignition       numeric(8,2),

    volume         numeric(8,2),
    breadth        numeric(8,2),
    n_terms        smallint,
    detail         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    task_id        bigint,
    calculated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, obj_key, trade_date),
    CONSTRAINT ck_ignition_scope CHECK (scope IN ('sector', 'group')),
    CONSTRAINT ck_ignition_stage
        CHECK (stage IN ('coil', 'ignite', 'follow', 'fade', 'none'))
);
CREATE INDEX IF NOT EXISTS ix_ignition_date_stage
    ON fct.ignition (trade_date, stage, score DESC);


-- 全市场情绪读数，**一天一行**（不是每票一行）。
-- 为什么不塞进 fct.score：这些是市场层面的标量，摊给每只票存一份取的时候要先
-- DISTINCT，而且"连板梯队"这种一天三列的读数在长表里没有位置。
-- 它是「明日验证表」（ops.review_check）唯一的事实来源 —— 昨天写下"预期涨停
-- 家数上升"，今天拿这张表逐条打勾，所以每列都必须可复算、口径写死在 SQL 里。
--   stocks            当天参与统计的票数。pct_chg 为空的行（新股首日 / pre_close
--                     缺失）已排除，所以这个数**小于**当天日线行数，如实记下来
--                     才能判断"涨停 87 家"是在多大的分母上
--   limit_up_count    涨停家数。判据是 `close >= limit_up - 0.01`（涨停**价**，
--                     md.stock_daily.limit_up 是 numeric 价格不是布尔标记）。
--                     用"涨幅 > 9.8%"会把创业板 20%、北交所 30%、ST 5% 全判错
--   touched_count     盘中摸到涨停价的家数（含最终封住的），炸板率的分母
--   broken_count      触板但收盘没封住。有涨停价所以是**精确值不是近似**
--   broken_rate       炸板率 %。分母为 0 时是 NULL 而不是 0 —— "都封住了"(0%)
--                     和"今天没人摸涨停"(NULL) 是两回事，混成一个值验证表会判错
--   highest_board     最高连板高度，往前 20 个交易日算末尾连续涨停天数
--   board2/3/4plus    连板梯队。**断层比高度更重要**：只有一个高标、中间空着，
--                     说明接力资金已经没了，所以分档存不存一个 avg
--   prev_limit_up     昨日（上一个有数据交易日，不是 d-1）的涨停家数
--   promotion_rate    晋级率 %：昨涨停且今日继续涨停 / 昨涨停
--   red_rate          昨涨停红盘率 %：宽一档的判据，没封板但没砸也算有人接
--   median_pct        中位涨幅。用中位数不用均值 —— 几只妖股拉不动中位数
CREATE TABLE IF NOT EXISTS fct.market_breadth (
    trade_date       date        NOT NULL,
    stocks           int,
    limit_up_count   int,
    limit_down_count int,
    touched_count    int,
    broken_count     int,
    broken_rate      numeric(8,2),
    highest_board    smallint,
    board2_count     int,
    board3_count     int,
    board4plus_count int,
    prev_limit_up    int,
    promotion_rate   numeric(8,2),
    red_rate         numeric(8,2),
    up_count         int,
    down_count       int,
    up_ratio         numeric(8,2),
    median_pct       numeric(10,4),
    task_id          bigint,
    calculated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_date)
);


-- 倍量震荡：近 40 个交易日"频繁倍量 + 沿 60 日线震荡"的原始读数，全市场每票一行。
-- **为什么落库**：现算实测 8.4 秒（5,453 只票 × 100 个交易日的窗口函数，MA60 要
-- 60 根暖机再往前推 40 天观察窗）。放页面上就是点一下卡住，所以照 fct.sector_trend
-- 的先例预算落库，页面只读表。
-- **为什么全市场都存、不只存入选的**：入选阈值在 config.setting['vol_double.*']，
-- 页面上一改就该立刻生效；只存入选行的话，每调一次阈值都得重算 8 秒。
--   win_days       观察窗天数（默认 40），跟着配置走，改了要重算
--   bars           窗口内真有行情的天数。停牌多的票会明显小于 win_days
--   ma60_days      窗口内算得出 ma60 的天数。**不满 60 根 K 线的那天记 NULL**
--                  （次新股不凑数），所以这个数小于 bars 时该怀疑是次新
--   dbl_count      倍量次数。基准是**前 5 个交易日均量**（不含当日）×2 ——
--                  用"昨日量"当基准的话，昨天缩量就凭空多一次倍量
--   dbl_prev_count 对昨日量的倍量次数，只作对照口径，排序不用它
--   cross_count    close 与 ma60 上下关系翻转的次数。0 = 单边，不是震荡
--   dev_med        |close/ma60 − 1| 的中位数（%）。用中位数，一根涨停拉不动它
--   dev_max        同一比值的最大值。中位小而最大值大 = 中间拉过一次又回来
--   above_ratio    收在 60 线上方的天数占比（%）。100 或 0 都不是震荡
--   ma60_chg       窗口首尾 ma60 的变化率（%）。60 线自己陡涨时"贴着线"是趋势
--   avg_amt_yuan   窗口日均成交额（元）。流动性门槛，北交所小票形态漂亮但买不进
CREATE TABLE IF NOT EXISTS fct.vol_double (
    trade_date     date        NOT NULL,
    ts_code        text        NOT NULL,
    win_days       smallint    NOT NULL,
    bars           smallint,
    ma60_days      smallint,
    dbl_count      smallint,
    dbl_prev_count smallint,
    cross_count    smallint,
    dev_med        numeric(10,2),
    dev_max        numeric(10,2),
    above_ratio    numeric(6,2),
    ma60_chg       numeric(10,2),
    avg_amt_yuan   numeric(20,2),
    close_last     numeric(16,4),
    ma60_last      numeric(16,4),
    task_id        bigint,
    calculated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_date, ts_code)
);
CREATE INDEX IF NOT EXISTS ix_vol_double_rank
    ON fct.vol_double (trade_date, dbl_count DESC);





-- =============================================================================
-- ops 域：运维
-- =============================================================================

CREATE TABLE IF NOT EXISTS ops.ingest_batch (
    batch_id     bigserial PRIMARY KEY,
    source       text        NOT NULL,            -- 'vipdoc/sh/lday' | 'sqlite_migration'
    source_path  text,
    source_mtime timestamptz,
    target       text        NOT NULL,            -- 'md.stock_daily'
    row_count    bigint,
    actor        text        NOT NULL DEFAULT current_user,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    status       text        NOT NULL DEFAULT 'running',  -- running|ok|failed
    message      text
);
CREATE INDEX IF NOT EXISTS ix_batch_target ON ops.ingest_batch (target, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_batch_source ON ops.ingest_batch (source, source_path);


-- 每个交易日的日线到底是哪个源写的。
--
-- **为什么必须有这个视图**：2026-08-27 我用新浪补了 08-26/08-27 的全市场
-- 日线，库里没有任何地方能看出这两天和别的日子口径不同 —— 复权、成交额单位、
-- 板块加权算法都有差，混在同一条曲线上谁也发现不了。用户当场纠正：
-- 「日线等数据优先用通达信 vipdoc」。现在来源可查，`daily_source_is_tdx`
-- 质量规则就能把非通达信来源的日子报出来，等 vipdoc 补下来再覆盖。
--
-- 通达信有**两条**通道，分开标注：`tdx_vipdoc` 是本地 .day 文件（精度最高，
-- 主源），`tdx_tq_api` 是 TQ 插件直连客户端取的（同源，float32 精度差 1e-8，
-- 在"盘后数据下载"菜单点不开时用）。两者都算通达信口径。
CREATE OR REPLACE VIEW md.v_daily_source AS
SELECT d.trade_date,
       CASE
           WHEN b.source IS NULL              THEN '未知'
           WHEN b.source = 'tdx_tq_api'       THEN '通达信TQ接口'
           WHEN b.source LIKE 'tdx%'          THEN '通达信vipdoc'
           WHEN b.source LIKE '%sqlite%'      THEN '旧库迁移'
           WHEN b.source LIKE 'sina%'
             OR b.source LIKE 'akshare%'
             OR b.source LIKE '%_em'          THEN '网络兜底'
           ELSE b.source
       END                       AS source,
       b.source                  AS raw_source,
       count(*)                  AS row_count,
       max(b.started_at)         AS ingested_at
  FROM md.stock_daily d
  LEFT JOIN ops.ingest_batch b ON b.batch_id = d.src_batch_id
 GROUP BY d.trade_date, 2, b.source;




CREATE TABLE IF NOT EXISTS ops.task (
    task_id          bigserial PRIMARY KEY,
    job_key          text,
    cap_key          text,
    script_id        bigint,
    trade_date       date        NOT NULL,
    scope            jsonb,
    params           jsonb,
    state            text        NOT NULL DEFAULT 'pending',
    progress         int         NOT NULL DEFAULT 0,
    message          text,
    attempt          int         NOT NULL DEFAULT 0,
    max_retry        int         NOT NULL DEFAULT 2,
    cancel_requested boolean     NOT NULL DEFAULT false,
    concurrency_key  text,
    depends_on       bigint[],
    actor            text        NOT NULL DEFAULT current_user,
    created_at       timestamptz NOT NULL DEFAULT now(),
    started_at       timestamptz,
    finished_at      timestamptz,
    result_meta      jsonb,
    error            text,
    CONSTRAINT ck_task_state CHECK (state IN
        ('pending','running','succeeded','failed','cancelled','skipped'))
);
CREATE INDEX IF NOT EXISTS ix_task_state ON ops.task (state, created_at);
CREATE INDEX IF NOT EXISTS ix_task_date  ON ops.task (trade_date, job_key);
CREATE UNIQUE INDEX IF NOT EXISTS ux_task_job_cap_date_attempt
    ON ops.task (job_key, cap_key, trade_date, attempt)
    WHERE job_key IS NOT NULL AND cap_key IS NOT NULL;


CREATE TABLE IF NOT EXISTS ops.audit (
    id            bigserial PRIMARY KEY,
    at            timestamptz NOT NULL DEFAULT now(),
    actor         text        NOT NULL DEFAULT current_user,
    channel       text        NOT NULL DEFAULT 'direct',   -- direct|web|worker|scheduler
    action        text        NOT NULL,
    target        text,
    params        jsonb,
    rows_affected bigint,
    ok            boolean     NOT NULL DEFAULT true,
    error         text
);
CREATE INDEX IF NOT EXISTS ix_audit_at     ON ops.audit (at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_target ON ops.audit (target, at DESC);


CREATE TABLE IF NOT EXISTS ops.config_history (
    id         bigserial PRIMARY KEY,
    at         timestamptz NOT NULL DEFAULT now(),
    actor      text        NOT NULL DEFAULT current_user,
    table_name text        NOT NULL,
    row_key    text,
    op         text        NOT NULL,
    before     jsonb,
    after      jsonb
);
CREATE INDEX IF NOT EXISTS ix_cfghist ON ops.config_history (table_name, at DESC);


CREATE TABLE IF NOT EXISTS ops.quality_result (
    id         bigserial PRIMARY KEY,
    run_at     timestamptz NOT NULL DEFAULT now(),
    trade_date date,
    rule_key   text        NOT NULL,
    target     text        NOT NULL,
    severity   text        NOT NULL,
    passed     boolean     NOT NULL,
    actual     jsonb,
    expected   jsonb,
    sample     jsonb,
    batch_id   bigint
);
CREATE INDEX IF NOT EXISTS ix_quality_date ON ops.quality_result (trade_date, passed);


CREATE TABLE IF NOT EXISTS ops.data_ready (
    dataset_key text        NOT NULL,
    trade_date  date        NOT NULL,
    row_count   bigint,
    ready_at    timestamptz NOT NULL DEFAULT now(),
    batch_id    bigint,
    PRIMARY KEY (dataset_key, trade_date)
);


CREATE TABLE IF NOT EXISTS ops.migration_baseline (
    id          bigserial PRIMARY KEY,
    captured_at timestamptz NOT NULL DEFAULT now(),
    src_db      text NOT NULL,
    src_table   text NOT NULL,
    row_count   bigint,
    extra       jsonb,
    UNIQUE (src_db, src_table)
);
COMMENT ON TABLE ops.migration_baseline IS '旧 SQLite 库迁移前的行数基线，用于 B6 对账';


-- schema 版本化：记录每个 SQL 文件的应用时间与 sha256。
-- 不用 Alembic —— 建库脚本本身就是幂等 SQL，再叠一层 ORM 迁移框架
-- 会出现两个 schema 事实来源。校验和能发现"文件改了但库没重跑"。
CREATE TABLE IF NOT EXISTS ops.schema_migration (
    filename    text PRIMARY KEY,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    applied_by  text NOT NULL DEFAULT current_user
);
COMMENT ON TABLE ops.schema_migration IS 'app/db/sql/*.sql 的应用记录，checksum 变了说明建库脚本需重跑';

