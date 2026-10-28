-- =============================================================================
-- FinanceTool 05 · 注册表种子数据
-- 执行身份：ft_admin，连接库 financetool
-- 幂等：全部 ON CONFLICT DO UPDATE / DO NOTHING
--
-- 种子的意义：装完就能跑。所有数值都是"默认值"而非"硬编码"，
-- 页面上随时可改（这是与旧系统把权重写死在 factor_engine.py 里的根本区别）。
-- =============================================================================

-- =============================================================================
-- ① 数据源
-- =============================================================================
INSERT INTO config.data_source (source_key, name, kind, locator, parser_key, options, health_check, priority, note) VALUES
('vipdoc_day',        '通达信日线文件',   'file_binary', 'D:\new_tdx64\vipdoc\{market}\lday\{market}{code}.day',   'tdx_day',
    '{"markets":["sh","sz","bj"],"record_size":32}',
    '{"kind":"path","path":"D:\\new_tdx64\\vipdoc\\sh\\lday"}', 10,
    '32 字节定长 <IIIIIfII；amount 为 float32 单位元；vol 单位股'),
('vipdoc_lc1',        '通达信1分钟线',    'file_binary', 'D:\new_tdx64\vipdoc\{market}\minline\{market}{code}.lc1', 'tdx_lc',
    '{"markets":["sh","sz","bj"],"period":1,"record_size":32}', NULL, 20,
    '32 字节定长 <IfffffII；价格已是元；vol 单位股'),
('vipdoc_lc5',        '通达信5分钟线',    'file_binary', 'D:\new_tdx64\vipdoc\{market}\fzline\{market}{code}.lc5', 'tdx_lc',
    '{"markets":["sh","sz","bj"],"period":5,"record_size":32}', NULL, 21,
    NULL),
('tdx_tnf',           '通达信代码名称表', 'file_binary', 'D:\new_tdx64\T0002\hq_cache\{market}s.tnf',              'tdx_tnf',
    '{"header":50,"record":360,"code_offset":0,"name_offset":31,"name_encoding":"gbk"}', NULL, 30,
    '离线取股票名，配 pypinyin 生成拼音与首字母索引'),
('tqcenter_snapshot', 'tqcenter 行情快照','jsonrpc',     'http://127.0.0.1:17709',                                 'tqcenter',
    '{"method":"get_market_snapshot","markets":[5],"batch_interval_ms":20}',
    '{"kind":"tcp","host":"127.0.0.1","port":17709,"timeout":2}', 40,
    'PE/PB/市值/EPS/ROE；需 TdxW.exe 运行，探活失败即降级'),
('tqcenter_divid',    'tqcenter 分红送配','jsonrpc',     'http://127.0.0.1:17709',                                 'tqcenter',
    '{"method":"get_divid_factors"}',
    '{"kind":"tcp","host":"127.0.0.1","port":17709,"timeout":2}', 41,
    '用于计算真实 adjustment_factor，替代旧系统硬编码 1.0'),
('tqcenter_gb',       'tqcenter 股本变动','jsonrpc',     'http://127.0.0.1:17709',                                 'tqcenter',
    '{"method":"get_gb_info_by_date"}',
    '{"kind":"tcp","host":"127.0.0.1","port":17709,"timeout":2}', 42, NULL),
('tqcenter_calendar', 'tqcenter 交易日历','jsonrpc',     'http://127.0.0.1:17709',                                 'tqcenter',
    '{"method":"get_trading_dates"}',
    '{"kind":"tcp","host":"127.0.0.1","port":17709,"timeout":2}', 43,
    '灌 reference_data.trade_calendar，让调度器真正认节假日'),
('tqcenter_sector',   'tqcenter 板块成分','jsonrpc',     'http://127.0.0.1:17709',                                 'tqcenter',
    '{"method":"get_sector_list","market_codes":{"全部A股":5,"沪深A":50,"创业板":51,"科创板":52,"北交所":53}}',
    '{"kind":"tcp","host":"127.0.0.1","port":17709,"timeout":2}', 44,
    '市场码同时用于填 reference_data.security.board'),
-- 通达信客户端里的 Python 插件 PYPlugins\user\Get_DayEnd.py 每天落的 SQLite。
-- 一天一张 day_end_YYYYMMDD 表、115 个字段。**表名是采集日期不是交易日**，
-- 解析器按 (Open,Max,Min,Now) 回 market_data.stock_daily 投票定交易日，盘中采的表跳过。
('tdx_snapshot_db',   '通达信收盘快照库', 'sqlite',      'D:\new_tdx64\daily_close_data.db',                       'tdx_snapshot',
    '{"table_pattern":"day_end_%YYYYMMDD%","match_min":0.8,"lookback_days":5}',
    '{"kind":"path","path":"D:\\new_tdx64\\daily_close_data.db"}', 45,
    '市值/PE/PB/内外盘/量比/涨跌停价的唯一本地来源；六个定型列之外全进 extra jsonb'),

('derived',           '系统派生',         'db',          'internal',                                               NULL,
    '{}', NULL, 900,
    '伪数据源：由本系统计算得出的字段（涨跌幅、复权因子、评分等）都归到这里')
ON CONFLICT (source_key) DO UPDATE SET
    name = EXCLUDED.name, kind = EXCLUDED.kind, locator = EXCLUDED.locator,
    parser_key = EXCLUDED.parser_key, options = EXCLUDED.options,
    health_check = EXCLUDED.health_check, note = EXCLUDED.note, updated_at = now();


-- =============================================================================
-- ③ 数据集与就绪标准（阈值可改，旧系统"≥1000 行"写死在代码里且太松）
-- =============================================================================
INSERT INTO config.dataset (dataset_key, name, grain, source_keys, ready_rule, retention) VALUES
('market_data.stock_daily',   '个股日线', 'daily', ARRAY['vipdoc_day'],
    '{"min_rows":4800,"require_codes":["000001.SH","399001.SZ"],"max_null_pct":0.1,
      "amount_median_range":[1000000,10000000000]}',
    '{"partition":"year","keep_years":null}'),
('market_data.index_daily',   '指数日线', 'daily', ARRAY['vipdoc_day'],
    '{"min_rows":5,"require_codes":["000001.SH"]}', NULL),
('market_data.sector_daily',  '板块日线', 'daily', ARRAY['vipdoc_day'],
    '{"min_rows":100}', NULL),
('market_data.stock_minute',  '分钟线',   'minute', ARRAY['vipdoc_lc1','vipdoc_lc5'],
    '{"min_rows":500000}', '{"partition":"month","keep_months":36}'),
('market_data.stock_snapshot','行情快照', 'snapshot', ARRAY['tqcenter_snapshot'],
    '{"min_rows":4000}', NULL),
('reference_data.security',     '证券基础信息', 'static', ARRAY['tdx_tnf','tqcenter_sector'],
    '{"min_rows":5000}', NULL)
ON CONFLICT (dataset_key) DO UPDATE SET
    name = EXCLUDED.name, ready_rule = EXCLUDED.ready_rule, updated_at = now();


-- =============================================================================
-- ④ 校验规则
-- =============================================================================
INSERT INTO config.quality_rule (rule_key, name, scope, severity, target, check_kind, check_body, threshold, blocking, description) VALUES
-- 行级（由 CHECK 约束与触发器在库层强制，这里登记以便页面展示与审计）
('ohlc_order',          'OHLC 顺序',      'row', 'error',   'market_data.stock_daily', 'builtin', 'ck_daily_ohlc', NULL, true,
    'low <= open,close <= high；由 CHECK 约束强制'),
('price_positive',      '价格为正',       'row', 'error',   'market_data.stock_daily', 'builtin', 'ck_daily_price_positive', NULL, true, NULL),
('amount_sane',         '成交额量级',     'row', 'error',   'market_data.stock_daily', 'builtin', 'ck_daily_amount_sane', NULL, true,
    '元/千元混淆会撞穿 1e13 上限'),
('vol_amount_consistent','量额一致性',    'row', 'error',   'market_data.stock_daily', 'builtin', 'daily_sanity', NULL, true,
    'amount_yuan/volume_shares 必须落在 [low*0.5, high*2]。单位错 100 或 1000 倍必然被抓'),
('not_sector_code',     '板块码不入个股表','row','error',   'market_data.stock_daily', 'builtin', 'ck_daily_not_sector', NULL, true, NULL),
('code_format',         '代码格式',       'row', 'error',   'market_data.stock_daily', 'builtin', 'ck_daily_code_fmt', NULL, true, NULL),

-- 表级
('daily_row_count',     '当日行数下限',   'table','error',  'market_data.stock_daily', 'sql_bool',
    'SELECT count(*) >= 4800 FROM market_data.stock_daily WHERE trade_date = :d', '{"min":4800}', true, NULL),
('index_present',       '指数当日存在',   'table','error',  'market_data.index_daily', 'sql_bool',
    'SELECT count(*) = 2 FROM market_data.index_daily WHERE trade_date = :d AND security_code IN (''000001.SH'',''399001.SZ'')',
    NULL, true, NULL),
('null_rate',           '收盘空值率',     'table','error',  'market_data.stock_daily', 'sql_bool',
    'SELECT coalesce(count(*) FILTER (WHERE close IS NULL)::numeric / nullif(count(*),0), 0) < 0.001
     FROM market_data.stock_daily WHERE trade_date = :d', '{"max_pct":0.1}', true, NULL),
('board_coverage',      '板块字段覆盖',   'table','error',  'reference_data.security',   'sql_bool',
    'SELECT count(*) = 0 FROM market_data.stock_daily d LEFT JOIN reference_data.security s USING (security_code)
     WHERE d.trade_date = :d AND s.board IS NULL', NULL, false,
    'board 缺失则涨跌停算不出来'),
('date_is_trading_day', '日期是交易日',   'table','error',  'market_data.stock_daily', 'sql_bool',
    'SELECT exists(SELECT 1 FROM reference_data.trade_calendar WHERE calendar_date = :d AND is_open)', NULL, true, NULL),
-- 这一天的日线是不是通达信 vipdoc 写的。
-- **不 blocking，但必须报出来**：2026-08-27 用新浪补了 08-26/08-27 的全市场
-- 日线，库里当时没有任何地方能看出这两天口径不同（复权、成交额单位、板块加权
-- 都有差），混在同一条曲线上没人发现。现在只要来源不是通达信就亮一条提醒，
-- 等 vipdoc 补下来跑 `c10_catchup.py --overwrite` 换回主源，提醒自然消失。
('daily_source_is_tdx',  '日线来自通达信', 'table','warning','market_data.stock_daily', 'sql_bool',
    'SELECT coalesce(bool_or(source IN (''通达信vipdoc'', ''通达信TQ接口'', ''旧库迁移'')), false)
     FROM market_data.view_daily_source WHERE trade_date = :d', NULL, false,
    '日线主源是通达信 vipdoc，次源是 TQ 接口（同一个客户端）；网络兜底源写的行口径与它们不完全可比，要用 --overwrite 换回'),



-- 跨日
('pre_close_chain',     '前收盘链一致',   'cross_day','error','market_data.stock_daily','sql_bool',
    'SELECT count(*) = 0 FROM market_data.stock_daily d
     JOIN reference_data.trade_calendar c ON c.calendar_date = d.trade_date
     JOIN market_data.stock_daily p ON p.security_code = d.security_code AND p.trade_date = c.prev_open_date
     WHERE d.trade_date = :d AND abs(d.pre_close - p.close) > 0.011
       AND NOT exists(SELECT 1 FROM reference_data.dividend v WHERE v.security_code = d.security_code AND v.ex_date = d.trade_date)',
    NULL, false, '除权日按分红记录豁免'),
('row_count_drift',     '行数偏离',       'cross_day','warning','market_data.stock_daily','sql_bool',
    'SELECT abs(cur - avg20) / nullif(avg20,0) < 0.05 FROM (
        SELECT (SELECT count(*) FROM market_data.stock_daily WHERE trade_date = :d)::numeric AS cur,
               (SELECT avg(c) FROM (SELECT count(*) c FROM market_data.stock_daily
                 WHERE trade_date < :d GROUP BY trade_date ORDER BY trade_date DESC LIMIT 20) t)::numeric AS avg20) x',
    '{"max_drift":0.05}', false, NULL),
('amount_magnitude_drift','成交额量级漂移','cross_day','error','market_data.stock_daily','sql_bool',
    'SELECT cur BETWEEN med20 / 10 AND med20 * 10 FROM (
        SELECT (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY amount_yuan)
                FROM market_data.stock_daily WHERE trade_date = :d)::numeric AS cur,
               (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY amount_yuan)
                FROM market_data.stock_daily WHERE trade_date < :d
                  AND trade_date >= :d - interval ''40 days'')::numeric AS med20) x',
    NULL, true, '专抓"某天起单位变了"这类回归'),
('no_gap',              '交易日无缺口',   'cross_day','error','market_data.stock_daily','sql_bool',
    'SELECT count(*) = 0 FROM reference_data.trade_calendar c
     WHERE c.is_open AND c.calendar_date < :d AND c.calendar_date >= :d - interval ''30 days''
       AND NOT exists(SELECT 1 FROM market_data.stock_daily d WHERE d.trade_date = c.calendar_date)',
    NULL, false, NULL),

-- 跨表
('minute_daily_consistent','分钟线聚合对日线','cross_table','warning','market_data.stock_minute','sql_bool',
    'SELECT coalesce(count(*) = 0, true) FROM (
        SELECT m.security_code, sum(m.volume_shares) v, d.volume_shares dv
        FROM market_data.stock_minute m JOIN market_data.stock_daily d
          ON d.security_code = m.security_code AND d.trade_date = (m.dt AT TIME ZONE ''Asia/Shanghai'')::date
        WHERE m.period = 1 AND d.trade_date = :d
        GROUP BY m.security_code, d.volume_shares
        LIMIT 100) t WHERE abs(v - dv)::numeric / nullif(dv,0) > 0.005',
    '{"tolerance":0.005}', false,
    '两个独立数据源的交叉验证。旧系统从未做过，所以两套 ETL 差 1.8% 三个月无人发现'),
('sector_member_valid', '板块成分有效',   'cross_table','warning','reference_data.sector_member','sql_bool',
    'SELECT count(*) = 0 FROM reference_data.sector_member m
     LEFT JOIN reference_data.security s USING (security_code) WHERE s.security_code IS NULL', NULL, false, NULL),
-- 这条规则是**踩坑之后补的**。上面那条只校验 security_code 认不认识，
-- 不校验 sector_code 两张表对不对得上 —— 于是
-- `reference_data.sector_member` 用 `GN_880501`、`market_data.sector_daily` 用 `TDX_880501`
-- 这种主键分裂，交集为 0，板块热度四个分项**全是空值**，
-- 却没有任何一条规则报警，藏了很久。归一之后要有东西盯住它别再退回去。
('sector_key_aligned',  '板块主键对齐',   'cross_table','error',  'reference_data.sector_member','sql_bool',
    'SELECT count(*) = 0 FROM (
        SELECT DISTINCT m.sector_code FROM reference_data.sector_member m
         WHERE m.sector_code ~ ''[0-9]{6}$''
           AND NOT EXISTS (SELECT 1 FROM market_data.sector_daily d
                            WHERE d.sector_code = m.sector_code)
           AND EXISTS (SELECT 1 FROM market_data.sector_daily d
                        WHERE d.sector_code = ''TDX_'' || substring(m.sector_code from ''[0-9]{6}$''))
     ) t', NULL, false,
    '成分表里的板块主键在日线表里找不到、但换成 TDX_ 前缀就能找到 —— 就是主键分裂'),
('score_coverage',      '评分覆盖率',     'cross_table','warning','analytics.score','sql_bool',
    'SELECT coalesce(
        (SELECT count(DISTINCT security_code) FROM analytics.score WHERE trade_date = :d)::numeric /
        nullif((SELECT count(*) FROM market_data.stock_daily WHERE trade_date = :d), 0), 0) > 0.95',
    '{"min_coverage":0.95}', false, NULL),

-- 评分级
('score_range',         '评分值域',       'score','error',  'analytics.score', 'sql_bool',
    'SELECT count(*) = 0 FROM analytics.score WHERE trade_date = :d AND (value < -100 OR value > 100)',
    NULL, false, NULL),
-- 分项失效探测器。**判据是"横截面恒定 + 跨日也恒定"，两个都成立才算失效** ——
-- 2026-08-30 修：第一版只看横截面 `stddev_pop = 0`，于是每天必然报
-- `market_mood` / `env_mood` 未通过（阻断级）：大盘情绪本来就是**全市场同一个值**
-- （操作分 = 0.40·X + 0.60·Y，Y 里的大盘情绪对所有票相同），横截面标准差
-- 恒为 0 是设计，不是故障。实测 08-28 的 market_mood = 23.56、08-27 = 43.01，
-- 跨日是活的。改成"横截面恒定的分项，再看它最近 40 天的日值有没有变化"，
-- 既不误报全市场分项，又照样抓得住旧系统 Y 轴恒为 0（那种情况跨日也不变）。
('score_not_constant',  '子分项非恒定',   'score','error',  'analytics.score_detail','sql_bool',
    'WITH cs AS (
        SELECT component_key FROM analytics.score_detail
         WHERE trade_date = :d GROUP BY component_key
        HAVING coalesce(stddev_pop(component_value), 0) = 0
     ), daily AS (
        SELECT component_key, trade_date, avg(component_value) AS v
          FROM analytics.score_detail
         WHERE component_key IN (SELECT component_key FROM cs)
           AND trade_date <= :d AND trade_date > :d - interval ''40 days''
         GROUP BY component_key, trade_date
     )
     SELECT count(*) = 0 FROM (
        SELECT component_key FROM daily GROUP BY component_key
        HAVING coalesce(stddev_pop(v), 0) = 0) t',
    NULL, true,
    '横截面与跨日**同时**恒定才算该分项失效。正是旧系统 Y 轴板块热度恒为 0（漏传 block_stocks）的探测器；全市场同值的分项（大盘情绪）只看跨日'),
('weight_sum',          '权重归一化',     'score','error',  'config.binding','sql_bool',
    'SELECT count(*) = 0 FROM (
        SELECT capability_key, sum(eff_weight) s FROM config.view_effective_binding GROUP BY capability_key) t
     WHERE abs(s - 1) > 0.0001', NULL, false, NULL),
('score_stale_check',   'stale 评分提醒', 'score','warning','analytics.score','sql_bool',
    'SELECT count(*) = 0 FROM analytics.score WHERE trade_date = :d AND is_stale', NULL, false, NULL)
ON CONFLICT (rule_key) DO UPDATE SET
    name = EXCLUDED.name, check_body = EXCLUDED.check_body, severity = EXCLUDED.severity,
    blocking = EXCLUDED.blocking, description = EXCLUDED.description, updated_at = now();


-- =============================================================================
-- ⑤ 内置指标（builtin=true 不可删，但可停用、可改参数）
-- =============================================================================
INSERT INTO config.indicator (indicator_key, name, kind, expr, output_unit, decimals, builtin, note) VALUES
('ma5',    'MA5',    'window', 'MA(close, 5)',   'yuan', 4, true, NULL),
('ma10',   'MA10',   'window', 'MA(close, 10)',  'yuan', 4, true, NULL),
('ma20',   'MA20',   'window', 'MA(close, 20)',  'yuan', 4, true, NULL),
('ma30',   'MA30',   'window', 'MA(close, 30)',  'yuan', 4, true, NULL),
('ma60',   'MA60',   'window', 'MA(close, 60)',  'yuan', 4, true, NULL),
('ma120',  'MA120',  'window', 'MA(close, 120)', 'yuan', 4, true, NULL),
('bias5',  'BIAS5',  'window', '(close - MA(close, 5)) / MA(close, 5) * 100',   'pct', 4, true, NULL),
('bias10', 'BIAS10', 'window', '(close - MA(close, 10)) / MA(close, 10) * 100', 'pct', 4, true, NULL),
('bias20', 'BIAS20', 'window', '(close - MA(close, 20)) / MA(close, 20) * 100', 'pct', 4, true, NULL),
('atr14',  'ATR14',  'window', 'MA(MAX(high - low, ABS(high - REF(close,1)), ABS(low - REF(close,1))), 14)',
    'yuan', 4, true, NULL),
('macd_dif',  'MACD DIF',  'window', 'EMA(close, 12) - EMA(close, 26)', 'yuan', 4, true, NULL),
('macd_dea',  'MACD DEA',  'window', 'EMA(EMA(close, 12) - EMA(close, 26), 9)', 'yuan', 4, true, NULL),
('macd_hist', 'MACD 柱',   'window', '2 * (EMA(close,12) - EMA(close,26) - EMA(EMA(close,12) - EMA(close,26), 9))',
    'yuan', 4, true, NULL),
('cci14',  'CCI14',  'window',
    '((high + low + close) / 3 - MA((high + low + close) / 3, 14)) / (0.015 * STD((high + low + close) / 3, 14))',
    'none', 4, true, NULL),
('amp',       '振幅',      'expr',   '(high - low) / pre_close * 100', 'pct', 4, true,
    '分母为前收盘而非开盘 —— 沿用旧系统口径，保证历史可比'),
('vol_ratio', '量比',      'window', 'volume_shares / MA(volume_shares, 5)', 'ratio', 4, true, NULL),
('std20',     '20日波动',  'window', 'STD(pct_change, 20)', 'pct', 4, true, NULL),
('amp_std20', '20日振幅标准差','window','STD((high - low) / pre_close * 100, 20)', 'pct', 4, true, NULL),
('hhv250',    '250日最高', 'window', 'HHV(high, 250)', 'yuan', 4, true, NULL),
('llv250',    '250日最低', 'window', 'LLV(low, 250)',  'yuan', 4, true, NULL),
('pos250',    '250日位置', 'window', '(close - LLV(low,250)) / (HHV(high,250) - LLV(low,250)) * 100',
    'pct', 4, true, '0=最低 100=最高'),
('chg10',     '10日涨幅',  'window', '(close - REF(close,10)) / REF(close,10) * 100', 'pct', 4, true, NULL),
('chg20',     '20日涨幅',  'window', '(close - REF(close,20)) / REF(close,20) * 100', 'pct', 4, true, NULL),
-- 下面五条是「旧系统自选股表」那几列的口径（2026-08-29 从《20260829整合原》移植）。
-- 与上面两条 chg10/chg20 **同名不同义，别混用**：
--   chg10      = 区间涨幅 = (今收 − 10日前收) / 10日前收
--   chg10_avg  = 日均涨跌 = 近10个交易日每日涨跌幅的平均
-- 旧系统页面上那一列叫「10日涨跌」，算的是后者（Σ每日涨跌幅 / 10），
-- 而它自己的分组得分只累加了 9 个涨跌幅却也除以 10，系统性偏小约 1/10。
-- 这里用 MA 表达：MA 的窗口是「该票最近 N 个有实际行情的交易日」，
-- 不足 N 根直接给空值，而不是拿固定分母把新股/长期停牌股稀释成一个偏小的数。
('chg10_avg',  '10日日均涨跌', 'window', 'MA(pct_change, 10)', 'pct', 4, true,
    '近10个交易日每日涨跌幅的均值。等价于旧系统 Σ(近10日涨跌幅)/10'),
('amp10_avg',  '10日日均振幅', 'window', 'MA((high - low) / pre_close * 100, 10)', 'pct', 4, true,
    '近10个交易日每日振幅的均值。振幅分母是前收，与 amp 一致'),
('chg20_avg',  '20日日均涨跌', 'window', 'MA(pct_change, 20)', 'pct', 4, true, NULL),
('amp20_avg',  '20日日均振幅', 'window', 'MA((high - low) / pre_close * 100, 20)', 'pct', 4, true, NULL),
-- 明日振幅预测：对近 20 日的每日振幅做一元线性回归，外推第 21 个点。
-- 闭式解，不需要 polyfit：预测值 = ȳ + b·(21 − x̄)，x̄ = (1+20)/2 = 10.5。
-- 旧系统用 np.polyfit(x, y, 1) 再算 a + b×(n+1)，两者代数等价（n=20 满窗时）。
('amp_pred',   '明日振幅预测', 'window',
    'MA((high - low) / pre_close * 100, 20) + SLOPE((high - low) / pre_close * 100, 20) * 10.5',
    'pct', 4, true, '近20日振幅回归外推第21日。不足20根交易日给空值，旧系统这种情况给 0'),
('avg_price',  '均价',      'expr',   'amount_yuan / volume_shares', 'yuan', 4, true,
    '成交额 ÷ 成交股数。旧系统写作 amount/(volume×100)，因为那边 volume 的单位是手'),
('mid_price',  '中价',      'expr',   '(high + low) / 2', 'yuan', 4, true, NULL),
('slope20',   '20日斜率',  'window', 'SLOPE(close, 20)', 'none', 6, true, NULL),
('amt_rank',  '成交额分位','rank',   'PCTRANK(amount_yuan)', 'ratio', 4, true, NULL),
('chg_rank',  '涨幅排名',  'rank',   'RANK(pct_change, desc)',  'count', 0, true, NULL),
('rel_sector','相对板块强度','crosssec','pct_change - MEAN(pct_change BY sector)', 'pct', 4, true, NULL)
ON CONFLICT (indicator_key) DO UPDATE SET
    name = EXCLUDED.name, expr = EXCLUDED.expr, output_unit = EXCLUDED.output_unit,
    note = EXCLUDED.note, updated_at = now();


-- =============================================================================
-- ⑥ 功能点
-- =============================================================================
INSERT INTO config.capability (capability_key, name, category, output_kind, output_spec, depends_on, requires, description) VALUES
('base_factors',  '基础因子',        'derive', 'table',
    '{"columns":["indicator_key","value"]}', '{}',
    '{"datasets":["market_data.stock_daily"],"lookback_days":260}',
    '按 config.indicator 计算全部启用的指标'),
('stock_quality', '个股质量(X轴)',   'factor', 'score',
    '{"range":[-100,100],"parts":["xingtai","qushi","weizhi","chouma","liangneng"]}',
    ARRAY['base_factors'],
    '{"datasets":["market_data.stock_daily"],"lookback_days":250}',
    '形态/趋势/位置/筹码/量能 加权；权重在 config.binding'),
('sector_heat',   '板块热度',        'factor', 'score',
    '{"range":[-100,100],"parts":["zhangfu","guangdu","dongliang","chengjiao"]}',
    ARRAY['base_factors'],
    '{"datasets":["market_data.stock_daily","market_data.sector_daily"],"lookback_days":60}',
    '涨幅/广度/动量/成交额 加权'),
('market_mood',   '大盘情绪',        'factor', 'score',
    '{"range":[-100,100]}', ARRAY['base_factors'],
    '{"datasets":["market_data.stock_daily"],"lookback_days":20}',
    '全市场个股统计（非指数），反映赚钱效应'),
('env_resonance', '环境共振(Y轴)',   'factor', 'score',
    '{"range":[-100,100],"parts":["sector_heat","market_mood"]}',
    ARRAY['sector_heat','market_mood'],
    '{"datasets":["market_data.stock_daily"],"lookback_days":20}',
    '板块热度 + 大盘情绪。旧系统此处 65% 权重因漏传参数而失效'),
('oper_score',    '操作评分',        'factor', 'score',
    '{"range":[-100,100],"parts":["stock_quality","env_resonance"],"quadrant":true}',
    ARRAY['stock_quality','env_resonance'],
    '{"datasets":["market_data.stock_daily"],"lookback_days":250}',
    'X/Y 加权 + 四象限分类 Q1买入/Q2持有/Q3试探/Q4回避'),
('temp_score',    '温度',            'factor', 'score',
    '{"range":[0,100],"parts":["weizhi","liangbi","qushi","weixian"]}',
    ARRAY['base_factors'],
    '{"datasets":["market_data.stock_daily"],"lookback_days":250}',
    '阈值按流通市值插值'),
-- 量能两件套。和 temp_score 分开：那个混了振幅/换手/乖离，这里只问量。
-- 温度是连续量（多热），阶段是离散量（在生命周期哪一段），不能合成一个数 ——
-- 温度 85 度可能是主升高潮（持有）也可能是天量后缩量新高（跑），方向相反。
('vol_temp',      '量能温度',        'factor', 'score',
    '{"range":[0,100],"parts":["liangbi","jiezou","chixu","weizhi"]}',
    ARRAY['base_factors'],
    '{"datasets":["market_data.stock_daily"],"lookback_days":60}',
    '量比/放量节奏/持续性/位置 加权；权重在 config.binding.params'),
('vol_stage',     '量能阶段',        'factor', 'score',
    '{"range":[1,5],"labels":{"1":"冷却","2":"酝酿","3":"高潮","4":"背离","5":"退潮"}}',
    ARRAY['base_factors'],
    '{"datasets":["market_data.stock_daily"],"lookback_days":60}',
    '离散码。判定顺序 4→3→2→5→1，背离必须最先判：缩量新高同时满足"价在高位"，先判高潮会把最该警告的一类标成持有'),
('review_report', '复盘报告',        'report', 'table',
    '{}', ARRAY['oper_score'],
    '{"datasets":["market_data.stock_daily"],"lookback_days":20}', NULL)
ON CONFLICT (capability_key) DO UPDATE SET
    name = EXCLUDED.name, output_spec = EXCLUDED.output_spec,
    depends_on = EXCLUDED.depends_on, requires = EXCLUDED.requires,
    description = EXCLUDED.description, updated_at = now();


-- =============================================================================
-- ⑦ 内置脚本
-- =============================================================================
INSERT INTO config.script (script_key, name, path, version, provides, builtin, param_schema) VALUES
('base_factors_v1', '基础因子计算', 'builtin/base_factors.py', '1.0', ARRAY['base_factors'], true, '{}'),
('xingtai_v1',  '形态分',   'builtin/quality_xingtai.py',  '1.0', ARRAY['stock_quality'], true,
    '{"type":"object","properties":{"neutral_penalty":{"type":"number","default":-50}}}'),
('qushi_v1',    '趋势分',   'builtin/quality_qushi.py',    '1.0', ARRAY['stock_quality'], true,
    '{"type":"object","properties":{"fit_days":{"type":"integer","default":20}}}'),
('weizhi_v1',   '位置分',   'builtin/quality_weizhi.py',   '1.0', ARRAY['stock_quality'], true,
    '{"type":"object","properties":{"lookback":{"type":"integer","default":250}}}'),
('chouma_v1',   '筹码分',   'builtin/quality_chouma.py',   '1.0', ARRAY['stock_quality'], true, '{}'),
('liangneng_v1','量能分',   'builtin/quality_liangneng.py','1.0', ARRAY['stock_quality'], true, '{}'),
('sector_zhangfu_v1',  '板块涨幅',  'builtin/sector_zhangfu.py',  '1.0', ARRAY['sector_heat'], true, '{}'),
('sector_guangdu_v1',  '板块广度',  'builtin/sector_guangdu.py',  '1.0', ARRAY['sector_heat'], true, '{}'),
('sector_dongliang_v1','板块动量',  'builtin/sector_dongliang.py','1.0', ARRAY['sector_heat'], true, '{}'),
-- 成交分项的上下界量的是**相对全市场**的放量倍数（板块倍数 ÷ 当日全板块中位数），
-- 不是绝对倍数。绝对倍数会把"全市场缩量"记到每个板块头上：实测 2026-08-25
-- 的 649 个板块，amount/MA20 中位数只有 0.796、p95 只有 1.195，全压在原来的
-- 下界 0.8 以下，于是 5,413 只票的成交分项**全部为负**（-93.71 ~ -0.91）。
-- 默认值写在脚本里，这里只登记可调的旋钮，前端照这个渲染表单。
('sector_chengjiao_v1','板块成交额','builtin/sector_chengjiao.py','1.0', ARRAY['sector_heat'], true,
    '{"type":"object","properties":{
        "w_abs":{"type":"number","default":0.40,"title":"绝对成交额分位权重"},
        "w_ratio":{"type":"number","default":0.60,"title":"相对放量权重"},
        "ma_n":{"type":"integer","default":20,"title":"放量基准窗口"},
        "rel_lo":{"type":"number","default":0.70,"title":"相对放量下界"},
        "rel_hi":{"type":"number","default":1.50,"title":"相对放量上界"},
        "min_ref":{"type":"integer","default":30,"title":"中位数可信所需板块数"}}}'),
('market_mood_v1',  '大盘情绪',  'builtin/market_mood.py',  '1.0', ARRAY['market_mood'],   true, '{}'),
-- 这四个名字会直接出现在「评分归因」面板的每一行上（面板取 config.script.name），
-- 所以**必须是人话**。原来叫 Y-板块分量 / Oper-X分量，页面上看着像内部代号。
('env_sector_v1',   '板块热度分量','builtin/env_sector.py', '1.0', ARRAY['env_resonance'], true, '{}'),
('env_mood_v1',     '大盘情绪分量','builtin/env_mood.py',   '1.0', ARRAY['env_resonance'], true, '{}'),
('oper_x_v1',       'X 个股质量','builtin/oper_x.py',       '1.0', ARRAY['oper_score'],    true, '{}'),
('oper_y_v1',       'Y 环境共振','builtin/oper_y.py',       '1.0', ARRAY['oper_score'],    true, '{}'),
('temp_score_v1',   '温度',      'builtin/temp_score.py',   '1.0', ARRAY['temp_score'],    true, '{}'),
-- 两个脚本共用 vol_phase.metrics() 的中间量，阈值只有一份，口径不会分叉
('vol_phase_v1',    '量能温度',  'builtin/vol_phase.py',    '1.0', ARRAY['vol_temp'],      true,
    '{"type":"object","properties":{"weights":{"type":"object"}}}'),
('vol_stage_v1',    '量能阶段',  'builtin/vol_stage.py',    '1.0', ARRAY['vol_stage'],     true,
    '{"type":"object","properties":{
        "incub_vr_lo":{"type":"number","default":1.05},
        "incub_vr_hi":{"type":"number","default":2.0},
        "incub_solid":{"type":"number","default":0.45},
        "climax_vr":{"type":"number","default":2.0},
        "climax_days":{"type":"number","default":3},
        "climax_high":{"type":"number","default":0.95},
        "div_high":{"type":"number","default":0.995},
        "div_peak":{"type":"number","default":0.60},
        "ebb_vr20":{"type":"number","default":0.90}}}'),
('review_report_v1','复盘报告',  'builtin/review_report.py','1.0', ARRAY['review_report'], true, '{}')
ON CONFLICT (script_key) DO UPDATE SET
    name = EXCLUDED.name, path = EXCLUDED.path, version = EXCLUDED.version,
    provides = EXCLUDED.provides, param_schema = EXCLUDED.param_schema;


-- =============================================================================
-- ⑧ 绑定：默认权重迁自旧系统 factor_engine.py，但现在是"数据"不是"代码"
--    旧：X = 0.25*形态 + 0.25*趋势 + 0.20*位置 + 0.20*筹码 + 0.10*量能  (:148)
--        Y = 0.65*板块热度 + 0.35*大盘情绪                            (:281)
--        oper = 0.40*X + 0.60*Y                                      (:300)
-- =============================================================================
INSERT INTO config.binding (capability_key, script_id, weight, display_order)
SELECT v.capability_key, s.script_id, v.weight, v.display_order
FROM (VALUES
    ('base_factors',  'base_factors_v1',      1.00, 0),
    ('stock_quality', 'xingtai_v1',           0.25, 1),
    ('stock_quality', 'qushi_v1',             0.25, 2),
    ('stock_quality', 'weizhi_v1',            0.20, 3),
    ('stock_quality', 'chouma_v1',            0.20, 4),
    ('stock_quality', 'liangneng_v1',         0.10, 5),
    ('sector_heat',   'sector_zhangfu_v1',    0.30, 1),
    ('sector_heat',   'sector_guangdu_v1',    0.30, 2),
    ('sector_heat',   'sector_dongliang_v1',  0.25, 3),
    ('sector_heat',   'sector_chengjiao_v1',  0.15, 4),
    ('market_mood',   'market_mood_v1',       1.00, 1),
    ('env_resonance', 'env_sector_v1',        0.65, 1),
    ('env_resonance', 'env_mood_v1',          0.35, 2),
    ('oper_score',    'oper_x_v1',            0.40, 1),
    ('oper_score',    'oper_y_v1',            0.60, 2),
    ('temp_score',    'temp_score_v1',        1.00, 1),
    ('vol_temp',      'vol_phase_v1',         1.00, 1),
    ('vol_stage',     'vol_stage_v1',         1.00, 1),
    ('review_report', 'review_report_v1',     1.00, 1)
) AS v(capability_key, script_key, weight, display_order)
JOIN config.script s ON s.script_key = v.script_key
ON CONFLICT (capability_key, script_id) DO UPDATE SET
    weight = EXCLUDED.weight, display_order = EXCLUDED.display_order, updated_at = now();


-- =============================================================================
-- ⑨ 作业
--
-- `params.runner` 是"这个作业用哪个脚本"的唯一绑定处（白名单在
-- app/orchestrator/runner.py 的 RUNNERS 里）。配置只能挑名字，不能写路径 ——
-- 表里能写任意命令等于把库当 shell 用。
--
-- **trigger_kind='manual' 是照实写的**：本机没有常驻调度进程，也没有注册
-- Windows 计划任务，所以除了 `/datasource`、`/jobs` 上点一下，没有任何东西会
-- 自动跑它们。下面几行历史 cron 值保留成"计划"，页面上明确标注未接调度器。
-- =============================================================================
-- 2026-08-29：'sync_reference'（同步参考数据）拆成 sync_dividend / sync_share /
-- sync_sector_name 三个有执行器的作业，旧的空壳作业删掉，别留在清单里当摆设
DELETE FROM config.job WHERE job_key = 'sync_reference';

INSERT INTO config.job (job_key, name, kind, caps, trigger_kind, cron_expr, ready_on, after_job,
                     scope, params, only_trade_day, concurrency_key, timeout_sec) VALUES
-- ---- 编排：一键追平（一个按钮把下面这些按顺序全跑一遍）----
-- only_trade_day = false 是有意的：周末点它照样该干活 —— 目标日由脚本自己算成
-- "最后一个已收盘交易日"（周六点＝周五那天），拦在"今天不是交易日"上等于
-- 周末永远补不了数据，而复盘恰恰多在周末做。
-- timeout_sec 给 4 小时：串了 9 步，光日线全量重扫就要十几分钟。
('fill_all',       '一键追平到最新收盘日', 'ingest', '{}',            'manual', NULL, NULL, NULL,
    '{"universe":"all"}', '{"runner":"fill_all"}',           false, 'ingest', 14400),
-- ---- 采集：日线与补齐 ----
-- P0 前置：通达信 GUI 盘后数据下载。**它不写任何一张表**，只把 vipdoc 文件
-- （.day + .lc1 + .lc5）推到目标交易日 —— 是 sync_daily / sync_catchup 的前置。
-- only_trade_day=false：目标日由脚本按"最后一个已收盘交易日"算，周末点它照样
-- 该把周五那天补上。timeout 2 小时：实测日线 10s、1 分钟线 4.5min、
-- 5 分钟线 3.2min，加上登录和失败重来一轮的余量。
-- **需要一个能操作鼠标的交互式桌面**：无人登录的会话里它会在会话自检那一步
-- 直接退出（脚本会照实说，不会假装成功）。
('tdx_predownload', '通达信盘后下载(前置)', 'ingest', '{}',           'manual', NULL, NULL, NULL,
    '{"universe":"all"}', '{"runner":"tdx_predownload"}',    false, 'ingest', 7200),
('sync_daily',     '同步日线(通达信全量)', 'ingest', '{}',        'cron', '10 15,16,17 * * 1-5', NULL, NULL,
    '{"universe":"all"}', '{"runner":"ingest_day"}',         true, 'ingest', 7200),
('sync_tq_day',    '同步日线(TQ接口)',  'ingest',  '{}',          'manual', NULL, NULL, NULL,
    '{"universe":"all"}', '{"runner":"ingest_tq_day"}',      true, 'ingest', 7200),
('sync_catchup',   '当日补齐(按源优先级)', 'ingest', '{}',        'manual', NULL, NULL, NULL,
    '{"universe":"all"}', '{"runner":"ingest_catchup"}',     true, 'ingest', 5400),
('sync_minute',    '同步分钟线',   'ingest',  '{}',               'chain', NULL, NULL, 'sync_daily',
    '{"universe":"all"}', '{}',                              true, 'ingest', 7200),
-- ---- 采集：板块 ----
('sync_sector',    '同步板块行情', 'ingest',  '{}',               'manual', NULL, NULL, NULL,
    '{"universe":"all"}', '{"runner":"ingest_sector"}',      true, 'ingest', 3600),
('sync_sector_name', '同步板块中文名', 'ingest', '{}',            'manual', NULL, NULL, NULL,
    '{"universe":"all"}', '{"runner":"ingest_sector_name"}', false, 'ingest', 900),
-- 同花顺本地板块。only_trade_day=false：读的是本地 ini，跟交易日无关，
-- 周末点它照样该把最新的概念清单读进来。
-- ★2026-09-22 更新（见 09_ths_reference.sql）：本作业已改 cron 定时；
-- 自选分组镜像（c14）也登记成了 sync_ths_watchlist 定时作业 —— 用户明确
-- 要求"定时抓"，删组建的兜底是 import_watchlist() 覆盖前的强制 JSON 备份。
('sync_ths_block', '同步同花顺板块', 'ingest', '{}',              'manual', NULL, NULL, NULL,
    '{"universe":"all"}', '{"runner":"ingest_ths_block"}',   false, 'ingest', 1800),
-- ---- 采集：资金与情绪面 ----
('sync_etf_share', '同步ETF份额',  'ingest',  '{}',               'manual', NULL, NULL, NULL,
    '{"universe":"all"}', '{"runner":"ingest_etf_share"}',   true, 'ingest', 1800),
('sync_margin',    '同步两融',     'ingest',  '{}',               'manual', NULL, NULL, NULL,
    '{"universe":"all"}', '{"runner":"ingest_margin"}',      true, 'ingest', 1800),
('sync_lhb',       '同步龙虎榜',   'ingest',  '{}',               'manual', NULL, NULL, NULL,
    '{"universe":"all"}', '{"runner":"ingest_lhb"}',         true, 'ingest', 1800),
('sync_fundflow',  '同步资金流',   'ingest',  '{}',               'manual', NULL, NULL, NULL,
    '{"universe":"all"}', '{"runner":"ingest_fundflow"}',    true, 'ingest', 1800),
-- ---- 采集：参考数据（复权因子依赖分红，先分红后股本）----
('sync_dividend',  '同步分红与复权因子', 'ingest', '{}',           'manual', NULL, NULL, NULL,
    '{"universe":"all"}', '{"runner":"ingest_dividend"}',    false, 'ingest', 5400),
('sync_share',     '同步股本',     'ingest',  '{}',               'manual', NULL, NULL, NULL,
    '{"universe":"all"}', '{"runner":"ingest_share"}',       false, 'ingest', 5400),
-- 通达信收盘快照库。only_trade_day=false：读的是本机 SQLite，什么时候点都该把
-- 已经落地的日表搬进来；哪几天有效由脚本按数据回推（盘中采集的表自己会跳过）。
('sync_snapshot',  '同步收盘快照(市值/PE)', 'ingest', '{}',        'manual', NULL, NULL, NULL,
    '{"universe":"all"}', '{"runner":"ingest_snapshot"}',    false, 'ingest', 1800),

-- ---- 质量与计算 ----
-- 质量审计。**only_trade_day = false 是有意的**：它审的是"最近一个已收盘交易日"，
-- 周末点开页面照样该把周五那天补上；拦在"今天不是交易日"上等于周末永远看不到
-- 最新一天的体检结果，而复盘恰恰多在周末做。
-- 执行器 e2_quality_audit.py **自己算缺口**（差 2 天就跑 2 次），所以这里不传交易日。
('quality_audit',  '数据质量审计', 'quality', '{}',               'on_ready', NULL,
    ARRAY['market_data.stock_daily'], NULL, '{"universe":"all"}',
    '{"runner":"quality_audit"}', false, NULL, 1800),
('calc_base',      '计算基础因子', 'compute', ARRAY['base_factors'], 'on_ready', NULL,
    ARRAY['market_data.stock_daily'], NULL, '{"universe":"all"}', '{"runner":"compute_indicators"}',
    true, 'compute', 3600),
('calc_scores',    '计算评分',     'compute',
    ARRAY['stock_quality','sector_heat','market_mood','env_resonance','oper_score','temp_score',
          'vol_temp','vol_stage'],
    'chain', NULL, NULL, 'calc_base', '{"universe":"all"}', '{"runner":"compute_scores"}',
    true, 'compute', 3600),
('build_review',   '生成复盘报告', 'report',  ARRAY['review_report'], 'chain', NULL, NULL, 'calc_scores',
    '{"universe":"all"}', '{"runner":"compute_scores"}',     true, NULL, 1800),
('daily_digest',   '每日摘要推送', 'notify',  '{}',               'cron', '0 20 * * 1-5', NULL, NULL,
    '{"universe":"all"}', '{}',                              true, NULL, 300),
-- cron 兜底：即使就绪事件丢失，当天也会算。执行器用 (job_key, trade_date) 唯一约束保证只成功一次
('calc_scores_fallback', '评分兜底(定时)', 'compute',
    ARRAY['stock_quality','sector_heat','market_mood','env_resonance','oper_score','temp_score',
          'vol_temp','vol_stage'],
    'cron', '30 19 * * 1-5', NULL, NULL, '{"universe":"all"}', '{"runner":"compute_scores"}',
    true, 'compute', 3600),
-- 板块热度的日趋势 + N 日趋势（斜率）。**必须排在 calc_scores 之后**：
-- 趋势是从 analytics.score[sector_heat] 聚出来的，评分没算完先跑只会把昨天的再写一遍。
-- 真正每天会跑到它的是「一键追平」那条链（e1_fill_all 的第 10 步）——
-- 本系统没有调度器进程，trigger_kind/cron_expr 只是声明，见 app/orchestrator/runner.py
('calc_sector_trend', '计算板块热度趋势', 'compute', '{}',
    'chain', NULL, NULL, 'calc_scores', '{"universe":"all"}',
    '{"runner":"compute_sector_trend"}', true, 'compute', 1800),
-- 趋势启动点（蓄势/点火/延续/退潮）。**排在 calc_sector_trend 之后**：它读
-- analytics.sector_heat_daily，热度没写今天就只是把昨天的再算一遍。不依赖个股评分。
-- 每天真正跑到它的同样是「一键追平」那条链（e1_fill_all 的 ignition 步）。
('calc_ignition', '计算趋势启动点', 'compute', '{}',
    'chain', NULL, NULL, 'calc_sector_trend', '{"universe":"all"}',
    '{"runner":"compute_ignition"}', true, 'compute', 1800),
-- 个股操作分的窗口趋势（/scores 的「四象限趋势分」）。**排在 calc_scores 之后**：
-- 它读 analytics.score 的 oper_score / stock_quality / env_resonance 三个 capability_key，
-- 评分没写今天，这一步只会把昨天的再算一遍。
-- 2026-08-31 从"页面每次现算"改成落库：现算 1.0s 直接加在 /scores 首屏上，
-- 而趋势是当天收盘后就定死的历史值。库里没有当天时页面**自动回退现算**
-- （app/web/queries.py 的 _trend_is_stored），所以这条作业漏跑只是慢，不是空表。
('calc_score_trend', '计算个股趋势分', 'compute', '{}',
    'chain', NULL, NULL, 'calc_scores', '{"universe":"all"}',
    '{"runner":"compute_score_trend"}', true, 'compute', 1800),
-- 全市场情绪读数（涨跌停 / 触板炸板 / 连板梯队 / 晋级率 / 中位涨幅）。
-- **触发挂在 market_data.stock_daily 就绪上，不挂 calc_scores**：它只读日线，不碰评分也
-- 不碰板块，日线一入库就能算。挂到评分后面等于凭空加一层依赖 —— 评分挂了会连
-- 情绪读数一起丢，而次日的「明日验证表」正是靠它打勾的。
-- 漏跑的后果和别的计算作业不同：次日核验会显示"数据不足"而不是判错，
-- 缺一天就永久缺一天的核验结论，补不回来。
('calc_breadth', '计算全市场情绪读数', 'compute', '{}',
    'on_ready', NULL, ARRAY['market_data.stock_daily'], NULL, '{"universe":"all"}',
    '{"runner":"compute_breadth"}', true, 'compute', 1800),
-- 倍量震荡（近 40 日频繁倍量 + 沿 60 日线震荡）。同样只读 market_data.stock_daily，
-- MA60 在 SQL 里自算（analytics.indicator_value 的 ma60 只有 23 个交易日，40 天窗口
-- 过半取不到，筛出来是空的）。
-- **一天约 10 秒，是所有计算作业里最慢的**：只要它自己慢，不拖别人 ——
-- 所以给了 3600 秒超时，并且不进 concurrency_key='compute' 之外的任何链。
('calc_vol_double', '计算倍量震荡', 'compute', '{}',
    'on_ready', NULL, ARRAY['market_data.stock_daily'], NULL, '{"universe":"all"}',
    '{"runner":"compute_vol_double"}', true, 'compute', 3600)




ON CONFLICT (job_key) DO UPDATE SET
    name = EXCLUDED.name, caps = EXCLUDED.caps, trigger_kind = EXCLUDED.trigger_kind,
    cron_expr = EXCLUDED.cron_expr, ready_on = EXCLUDED.ready_on,
    after_job = EXCLUDED.after_job, params = EXCLUDED.params,
    only_trade_day = EXCLUDED.only_trade_day, concurrency_key = EXCLUDED.concurrency_key,
    timeout_sec = EXCLUDED.timeout_sec, updated_at = now();



-- =============================================================================
-- ⑩ 筛选条件类型（沿用旧 /watchlist_filter 已验证的 5 种语义）
-- =============================================================================
-- **一行 = 一种筛选器真能跑的条件**。2026-08-29 把原来 6 行换成这 5 行：
-- 原来那批（between/topn/cmp/consecutive/belongs/had_limit_up）从来没有引擎读过，
-- 而且写不对 —— `consecutive` 的模板是 `NOT EXISTS (... LIMIT n) IS FALSE`，
-- 里面根本没有用到 field/op/value；`belongs` 依赖一张不存在的 config.v_universe。
-- 注册表里留着"点了不动"的条目，比明写"还没实现"更贵。
--
-- 模板是 **WHERE 片段**，由 app/compute/screener.py 组装，占位符规则：
--   :d      引擎注入的目标交易日
--   :start  引擎按参数 days 从 reference_data.trade_calendar 倒数算出的窗口起始交易日
--   其它    条件自己的参数；schema 里带 "literal":true 的按枚举白名单**文本**替换
--           （列名和比较符不能当绑定参数用），其余一律参数化绑定
-- 主查询里个股当日行情的别名固定是 `d`，模板要引用某只票就写 `d.security_code`。
DELETE FROM config.screen_rule
 WHERE rule_key IN ('between','topn','cmp','consecutive','belongs','had_limit_up');

INSERT INTO config.screen_rule (rule_key, name, param_schema, sql_template, builtin, note) VALUES
('price_compare', '指定日价格比较',
    '{"type":"object","required":["on_date","field","op","value"],"properties":{
        "on_date":{"type":"string","format":"date"},
        "field":{"enum":["open","close","high","low"],"literal":true,"default":"close"},
        "op":{"enum":[">","<"],"literal":true,"default":">"},
        "value":{"type":"number"}}}',
    'EXISTS (SELECT 1 FROM market_data.stock_daily x
              WHERE x.security_code = d.security_code AND x.trade_date = :on_date
                AND x.:field :op :value)', true,
    '某一天的开/收/高/低与阈值比较，严格大于或小于'),
('limit_up', 'N 日内涨停过',
    '{"type":"object","required":["days"],"properties":{
        "days":{"type":"integer","default":5},
        "pct":{"type":"number","default":9.9}}}',
    'EXISTS (SELECT 1 FROM market_data.stock_daily x
              WHERE x.security_code = d.security_code AND x.trade_date BETWEEN :start AND :d
                AND x.pre_close > 0
                AND (x.close - x.pre_close) / x.pre_close * 100 >= :pct)', true,
    '窗口内任一日收盘涨幅 ≥ pct（默认 9.9）。前收用 market_data.stock_daily.pre_close，
     不在窗口内取前一日 —— 旧系统那样取会让窗口第一天永远参与不了判定'),
('intraday_limit_up', 'N 日内盘中冲板',
    '{"type":"object","required":["days"],"properties":{
        "days":{"type":"integer","default":5},
        "pct":{"type":"number","default":9.9}}}',
    'EXISTS (SELECT 1 FROM market_data.stock_daily x
              WHERE x.security_code = d.security_code AND x.trade_date BETWEEN :start AND :d
                AND x.pre_close > 0
                AND (x.high - x.pre_close) / x.pre_close * 100 >= :pct)', true,
    '同上但用最高价算，收盘没封住也算'),
('amp10_over', '10 日日均振幅大于',
    '{"type":"object","required":["value"],"properties":{
        "value":{"type":"number","default":5}}}',
    'EXISTS (SELECT 1 FROM analytics.indicator_value i
              WHERE i.security_code = d.security_code AND i.trade_date = :d
                AND i.indicator_key = ''amp10_avg'' AND i.value > :value)', true,
    '读 analytics.indicator_value 的 amp10_avg（近10个交易日每日振幅均值），
     不足 10 根交易日的票没有值、自然不入选'),
('rising_closes', 'N 日内收阳天数不少于',
    '{"type":"object","required":["days","min_days"],"properties":{
        "days":{"type":"integer","default":5},
        "min_days":{"type":"integer","default":3}}}',
    '(SELECT count(*) FROM market_data.stock_daily x
        WHERE x.security_code = d.security_code AND x.trade_date BETWEEN :start AND :d
          AND x.pre_close IS NOT NULL AND x.close > x.pre_close) >= :min_days', true,
    '窗口内 close > 前收 的天数 ≥ min_days')
ON CONFLICT (rule_key) DO UPDATE SET
    name = EXCLUDED.name, param_schema = EXCLUDED.param_schema,
    sql_template = EXCLUDED.sql_template, note = EXCLUDED.note;


-- =============================================================================
-- ⑪ 图表
-- =============================================================================
-- **params 是给命名查询的入参，options 是给渲染的。** 两者以前混着写：
-- `mood_series` 的窗口写成 options 里的 `{"days":20}`，而渲染器只把 params
-- 透传给查询、函数的参数名也叫 lookback —— 于是那个旋钮既没被读到、名字也不对，
-- 图一直画的是默认窗口。凡是"改了配置图没变"，先看是不是写在了 options 里。
INSERT INTO config.chart (chart_key, name, chart_type, data_query, encoding, options, params) VALUES
('kline_vol', 'K线+成交量', 'candlestick', 'q_stock_kline',
    '{"x":"trade_date","open":"open","high":"high","low":"low","close":"close","volume":"volume_shares"}',
    '{"up_color":"#d32f2f","down_color":"#2e7d32","subplot_volume":true,"adjust_selector":true}',
    '{}'),
('index_line', '指数走势', 'line', 'q_index_daily',
    '{"x":"trade_date","y":["close"],"color":"security_code"}',
    '{"legend":true}', '{}'),
('pct_hist', '涨跌幅分布', 'hist', 'q_daily_pct',
    '{"x":"label","y":["cnt"]}',
    '{"bins":[-21,-10,-7,-5,-3,-1,0,1,3,5,7,10,21],"fixed_bins":true,
      "note":"固定分箱边界，保证跨日可比"}', '{}'),
-- 40 个交易日 ≈ 两个月；第二条线是 5 日均线，日线跳得厉害时靠它看趋势
('mood_series', '情绪时序（近两个月）', 'line', 'q_market_mood',
    '{"x":"trade_date","y":["value","ma5"]}',
    '{"legend":true,"note":"窗口 40 个交易日≈两个月；粗看趋势看 5 日均线"}',
    '{"lookback":40}'),
-- 温度趋势的两个短窗口，首页左右并排。**y 轴都钉在 -100~100**：纵轴自适应的话
-- 同一段 20 分的波动在 7 日图里画成满屏震荡、在 20 日图里画成一条平线，
-- 两张并排看得出的强弱印象是反的。zero_line 画 0 基准 —— -100~100 里 0 是中性。
('mood_7d', '大盘温度（近 7 个交易日）', 'bar', 'q_market_mood',
    '{"x":"trade_date","y":["value"]}',
    '{"yrange":[-100,100],"zero_line":true,
      "note":"逐日一根柱：红＝零上（偏暖）、绿＝零下（偏冷）。温度 = 涨跌家数比×0.35 ＋ 涨停家数×0.25 ＋ 中位涨幅×0.25 ＋ 成交额环比×0.15，四项各自线性截断映射到 -100~100（口径见 app/compute/builtin/market_mood.py 的 EXPLAIN）。y 轴钉在 -100~100，和右边那张同刻度，可以直接比。"}',
    '{"lookback":7}'),
('mood_20d', '大盘温度（近 20 个交易日）', 'line', 'q_market_mood',
    '{"x":"trade_date","y":["value","ma5"]}',
    '{"legend":true,"yrange":[-100,100],"zero_line":true,
      "note":"折线 ＋ 5 日均线。7 日那张看这几天是震还是走，这张看在抬头还是掉头 —— 单看日线在 ±10 分之间来回跳，趋势得靠均线。y 轴同样钉在 -100~100。没算过情绪分的日子会退回上涨占比（0~100），两个量纲不同，mood_source 列能看出这一段是哪种。"}',
    '{"lookback":20}'),
('sector_rank_bar', '板块热度榜', 'bar', 'q_sector_heat',
    '{"x":"value","y":"name"}',
    '{"orient":"h","no_truncate_label":true}', '{"limit":20}'),
('sector_heat_trend', '板块热度趋势', 'line', 'q_sector_heat_trend',
    '{"x":"trade_date","y":["value"],"color":"name"}',
    '{"legend":true,
      "note":"当天热度前 6 名板块的近 20 日热度曲线，看在升还是在降"}',
    '{"lookback":20,"top":6}'),

('sector_rotation_heat', '板块轮动热力图', 'heatmap', 'q_sector_rotation',
    '{"x":"trade_date","y":"name","z":"pct_change"}',
    '{"color_scale":"RdYlGn_r","zmin":-5,"zmax":5,"fixed_scale":true}', '{}'),
('quadrant_scatter', '四象限散点', 'scatter', 'q_quadrant',
    '{"x":"x_value","y":"y_value","size":"float_market_value_yuan","color":"quadrant"}',
    '{"quadrant_lines":true,"hover":["security_code","name","pct_change","oper"]}', '{}'),
('coverage_heat', '数据覆盖度', 'heatmap', 'q_coverage',
    '{"x":"trade_date","y":"dataset_key","z":"deviation"}',
    '{"color_scale":"Blues","fixed_scale":true}', '{}'),
-- 自选组的加权操作分曲线：和上面那张「自选组热度」表同源（共用一段 CTE），
-- 表给的是今天这一个点和一条斜率，图给的是这 20 天到底怎么走过来的。
-- **所有够长度的组都在图里**，但只有趋势最好的 3 个默认画出来（visible_col
-- 指向查询返回的 show 列 → Plotly 的 legendonly）。24 条线一起画是毛线团，
-- 但"只查 6 条"等于把第 7 名从图里删掉，想看得改配置 —— 现在点图例就行。
('group_score_trend', '自选组趋势（加权操作分）', 'line', 'q_group_trend',
    '{"x":"trade_date","y":["wscore"],"color":"group_name"}',
    '{"legend":true,"visible_col":"show",
      "note":"默认只画趋势最好的 3 个组，其余点图例展开；纵轴＝组的加权操作分"}',
    '{"window_days":20,"top":3}')
ON CONFLICT (chart_key) DO UPDATE SET
    name = EXCLUDED.name, chart_type = EXCLUDED.chart_type,
    encoding = EXCLUDED.encoding, options = EXCLUDED.options,
    params = EXCLUDED.params, updated_at = now();


-- =============================================================================
-- ⑫ 推送（默认只落本地文件，webhook 需自行配置且口令走 secret_ref）
-- =============================================================================
INSERT INTO config.notify_channel (channel_key, name, kind, endpoint, template, enabled) VALUES
('local_file', '本地文件', 'local_file', 'D:\finance\YuanShan\var\notify',
    '[{trade_date}] {title}\n{body}', true)
ON CONFLICT (channel_key) DO UPDATE SET name = EXCLUDED.name, updated_at = now();

INSERT INTO config.notify_rule (rule_key, name, trigger, channel_key, enabled) VALUES
('on_quality_error', '质量校验失败告警', 'quality_error', 'local_file', true),
('on_job_failed',    '作业失败告警',     'job_failed',    'local_file', true)
ON CONFLICT (rule_key) DO UPDATE SET name = EXCLUDED.name, updated_at = now();


-- =============================================================================
-- ⑬ 菜单与首页布局
-- =============================================================================
INSERT INTO config.menu (menu_key, label, route, icon, display_order, group_name, need_role) VALUES
-- 「每日复盘」原来是 display_order=20 的一项，2026-08-29 整页并进复盘首页后菜单也撤了
-- （下面 ⑬ 那段里有一条 DELETE 负责把库里已有的那行清掉）。
('home',      '复盘首页',   '/',             'house',        10, '复盘',   NULL),
-- 「大盘与资金」（display_order=30, /market）2026-08-30 也整页并进复盘首页：它只剩四张卡
-- （指数走势 / ETF 净申赎 / 两融 / 龙虎榜），而这四张回答的都是"今天钱在往哪走"
-- —— 那是复盘的一部分，不该单开一页再点一次。下面 ⑬ 有 DELETE 清库里的旧行。
('sector',    '板块热度',   '/sector',       'grid',         40, '复盘',   NULL),
('stock',     '个股详情',   '/stock',        'search',       50, '复盘',   NULL),
('screen',    '筛选器',     '/screen',       'funnel',       60, '选股',   NULL),
-- 2026-08-30 从「自选与关注」改名为「自选股组分析」：这一页的主体是自选组的
-- 加权分、趋势、振幅三张分析卡，加票改组只是它的维护入口。
('watchlist', '自选股组分析', '/watchlist',    'star',         70, '选股',   NULL),
-- 2026-08-30 从「评分与因子」改名为「全市场个股分析」：这一页不是给人看权重
-- 配置的，是按四象限把全市场 5,500 只票筛出来逐票看的地方（双高个股、个股明细
-- 两张表也在这次搬了进来）。原名让人以为它是配置页。
('scores',    '全市场个股分析', '/scores',   'calculator',   80, '选股',   NULL),
('blocks',    '积木管理',   '/blocks',       'puzzle',       90, '配置',   NULL),
('jobs',      '任务与调度', '/jobs',         'list-task',   100, '配置',   NULL),
-- 2026-08-31 从「数据管理」改名为「数据加工」：这一页做的是**入库之后**的事
-- （批次就绪、覆盖度、血缘、质量规则），取数在 /datasource。原名和「数据源」
-- 并排放着分不出谁管取、谁管加工。
('data',      '数据加工',   '/data',         'database',    110, '运维',   NULL),
-- 「数据质量」（display_order=120, /quality）2026-08-30 并进 /data，成为页内第二个标签：
-- "数据到齐了吗"和"到的数据对吗"是同一次盘后检查的上下半场，分两页的结果是
-- 每天点两个地方、还要先想一下该去哪一页看。下面 ⑬ 有 DELETE 清库里的旧行。
('sql',       'SQL 查询台', '/sql',          'terminal',    130, '运维',   NULL),
-- 2026-08-30 从「数据源监控」改名成「数据源」：页内两个标签，
-- ① 监控与采集（现在能不能用）+ ② 数据源管理（该先用谁），不只是监控了。
-- 2026-08-31 再改成「数据获取」：这一页是**把数据取进来**的地方（采集按钮都在
-- 这），和「数据加工」成对，比「数据源」更说得清点进去能干什么。
('datasource','数据获取',    '/datasource',   'plug',        125, '运维',   NULL),
('admin',     '系统管理',   '/admin',        'gear',        140, '运维',   'admin')
ON CONFLICT (menu_key) DO UPDATE SET
    label = EXCLUDED.label, route = EXCLUDED.route, icon = EXCLUDED.icon, display_order = EXCLUDED.display_order;

-- **撤掉的卡片必须显式删。** 下面那条 INSERT 是 UPSERT，只会覆盖同 (page_key, slot)
-- 的行，撤掉的槽位留在库里就是"幽灵卡片"，页面照旧渲染它们，看着像改了没生效。
-- 首页现在到 20 号槽（并入了原「每日复盘」与原「大盘与资金」的全部卡片），
-- 而 page_key='review' / 'market' 两整页作废 —— 那两页已经不存在了。
DELETE FROM config.page_layout WHERE page_key = 'home'   AND slot > 20;
DELETE FROM config.page_layout WHERE page_key = 'review';
DELETE FROM config.menu        WHERE menu_key  = 'review';
DELETE FROM config.page_layout WHERE page_key = 'market';
DELETE FROM config.menu        WHERE menu_key  = 'market';
DELETE FROM config.menu        WHERE menu_key  = 'quality';
-- 2026-09-01：/scores 的 slot 3「X>30 且 Y>30 的双高个股」撤掉 ——
-- 上半截的「选票结果」默认就是双高（Q1 ＋ X≥30 ＋ Y≥30 ＋ 按 X+Y 排序），
-- 同一个问题不摆两张表。**必须显式删**：下面那条 INSERT 是按 (page_key, slot)
-- UPSERT 的，不删的话库里那一行留着，页面照旧把双高卡渲染出来。
DELETE FROM config.page_layout WHERE page_key = 'scores' AND slot = 3;
-- 整页说明也要跟着撤：留着的话「本页说明」还在讲一个点不进去的页面。
DELETE FROM config.help_note   WHERE note_key  IN
    ('page.market', 'page.quality', 'card.home.16');

INSERT INTO config.page_layout (page_key, slot, width, height, card_kind, card_ref, title, options) VALUES
('home', 1,  2, 130, 'stat',  'k_mood',        '大盘温度',   '{"unit":"","color":"primary"}'),
('home', 2,  2, 130, 'stat',  'k_up_count',    '上涨家数',   '{"unit":"家"}'),
('home', 3,  2, 130, 'stat',  'k_limit_up',    '涨停家数',   '{"unit":"家"}'),
-- k_amount 自己就把元换算成亿并带了千位分隔（'21,177 亿'），所以这里**不能再配
-- scale/suffix** —— 渲染器会拿这个字符串去做 float(val)/scale，直接
-- `could not convert string to float: '21,177 亿'`，整张卡片变成红色报错。
-- /market 那行一直是 '{}'，两边现在一致。
('home', 4,  2, 130, 'stat',  'k_amount',      '成交额',     '{}'),
('home', 5,  2, 130, 'stat',  'k_score_avg',   '平均操作分', '{}'),
('home', 6,  2, 130, 'stat',  'k_health',      '系统健康',   '{"kind":"health"}'),
-- 下面这一串原来是 page_key='review'（每日复盘）的卡片。2026-08-29 整页并进首页：
-- 两页卡片高度重叠，一天要点两个地方看同一批数字，而首页只剩指标块之后就成了
-- 一个必须再点一次的中转站。现在首页自己带交易日下拉，**上面六个指标块也跟着
-- 所选日期变**（k_* 都收 trade_date；只有 k_health 例外，它说的是库的新鲜度）。
('home', 7,  6, 340, 'chart', 'pct_hist',      '涨跌幅分布', '{}'),
-- 情绪看三层：40 日折线是中期趋势，下面 7 日 / 20 日两张短窗口图是近况，
-- 再下面那张表是逐日的日变化（正负交替＝震荡，连着同号＝单边）
('home', 8,  6, 340, 'chart', 'mood_series',   '情绪时序（近两个月趋势）','{}'),
-- 左右并排的两个短窗口：7 日柱状看"这几天是震还是走"，20 日折线看
-- "在抬头还是掉头"。y 轴都钉在 -100~100，两张才可比（见 config.chart 那两行）
('home', 9,  6, 320, 'chart', 'mood_7d',       '大盘温度（近 7 个交易日）','{}'),
('home', 10, 6, 320, 'chart', 'mood_20d',      '大盘温度（近 20 个交易日）','{}'),
('home', 11, 12,300, 'table', 'q_mood_recent', '近期情绪震荡（最近 7 个交易日）',
    '{"params":{"days":7}}'),
('home', 12, 12,400, 'table', 'q_sector_heat', '板块热度榜（含趋势列）',
    '{"drilldown":"sector"}'),
('home', 13, 12,360, 'chart', 'sector_heat_trend','板块热度趋势（前6名近20日）','{}'),
('home', 14, 12,420, 'chart', 'sector_rotation_heat','板块轮动','{}'),
('home', 15, 12,460, 'chart', 'quadrant_scatter','四象限散点','{}'),
-- 16~19 是原「大盘与资金」（page_key='market'）整页搬过来的四张卡，2026-08-30。
-- 那一页只剩这四张，回答的都是"今天钱在往哪走"——那本来就是复盘的一部分，
-- 不该单开一页让人再点一次。放在板块与四象限之后：先看大盘温度与板块，
-- 再看资金流向，最后看今天的任务跑没跑。
('home', 16, 12,340, 'chart', 'index_line',    '指数走势',   '{}'),
('home', 17, 6, 340, 'table', 'q_etf_flow',    'ETF 净申赎（仅股票型）', '{}'),
('home', 18, 6, 320, 'table', 'q_margin_trend','两融余额趋势（亿元）', '{}'),
('home', 19, 12,320, 'table', 'q_lhb_recent',  '龙虎榜',     '{}'),
-- 「X>30 且 Y>30 的双高个股」和「个股明细」2026-08-30 从这里搬到了 scores 页
-- （见下面 'scores' 那一段）。复盘首页回答"今天大盘和板块怎么样"，
-- 逐票筛选是「全市场个股分析」那一页的事，两件事混在一屏要往下翻半天。
-- ★「今日任务」这张卡没有 card_ref，说明在 config.help_note 里是按「页面.槽位」
--   定位的（`card.home.20`）。**改了它的槽位号就要同步改那条说明的 target**，
--   否则说明找不到卡、缺口清单会报出来（tests/test_help_note.py 会红）。
('home', 20, 12,300, 'task_list', NULL,        '今日任务',   '{"limit":10}'),


-- 下面这些页以前只在 config.menu 里有一行、没有任何卡片，于是侧栏点开是
-- "还没实现"。页面壳子现在由 app/web/pages/generic.py 统一渲染，
-- 所以**这里配上卡片就等于把页面做出来了**，不需要为每一页写一个模块。
-- page_key 必须等于 config.menu.menu_key，generic 就是按这个对上的。
-- 2026-08-30：原来 market#2 / sector#2 / sector#3 / scores#2 这四张卡在
-- options.note 里各挂一句短说明。布局层的 note 优先级高于 config.help_note 之外的
-- 兜底，会把补全后的详细说明（脚本 / 库表 / 分项含义）挡掉，所以清空 ——
-- 说明统一收在 config.help_note，短句的内容已全部并进去。
-- 想给某张卡写页面专属说明时再往 options.note 放，但必须写全数据来源，
-- 否则 tests/test_card_notes.py 会红。
-- market 那四张卡（index_line / q_etf_flow / q_margin_trend / q_lhb_recent）
-- 已经整页搬进首页 16~19 号槽，这里不再有 page_key='market' 的行。
-- 板块页 2026-09-01 重排（用户反馈："整个板块热度模块就是无脑展示，
-- 缺少数据加工"、"缺少日期"）。两条改动都在这几行里：
--   ① **加工排在展示之前**：阶段分布 + 启动候选（读 analytics.ignition，四档
--      点火/延续/蓄势/退潮）摆 1~3 槽，原来的热度榜/趋势/轮动/明细往后挪。
--      这个模型 d4_ignition.py 每天都在算，之前**全站一处都没显示**。
--   ② 标题里的 `{d}` 由 `ui._title_date()` 换成这张卡实际用的数据日期 ——
--      静态标题看不出"这是哪天的热度"。
-- 趋势图窗口 20→10：10 个点看得清最近的斜率，长窗口的斜率在明细表里有。
('sector', 1, 4, 260, 'table', 'q_sector_stage_dist','板块阶段分布（{d} · 与上一交易日比）',
    '{"page_size":5,"color":{"pct":["变化"]}}'),
('sector', 2, 8, 420, 'chart', 'sector_rank_bar','板块热度榜（{d}）','{}'),
('sector', 3, 12,420, 'table', 'q_sector_ignite',
    '启动候选：① 点火 ／ ② 延续 ／ ③ 蓄势（{d}）',
    '{"page_size":20,"color":{"pct":["pct_change","日趋势"],"flag":{"cols":["阶段"],"value":"① 点火"}}}'),
('sector', 4, 12,360, 'chart', 'sector_heat_trend','板块热度趋势（前6名近10日 · 截至{d}）', '{}'),
('sector', 5, 12,460, 'table', 'q_sector_parts', '热度四分项拆解（{d}）', '{}'),
('sector', 6, 12,440, 'chart', 'sector_rotation_heat','板块轮动（近15日 · 截至{d}）','{}'),
('sector', 7, 12,420, 'table', 'q_sector_heat', '板块明细（含阶段与趋势列 · {d}）',   '{}'),

('screen', 1, 12,120, 'markdown', NULL, '怎么用',
    '{"text":"筛选器分两层：**条件类型**登记在 config.screen_rule（5 种语义），**方案**存在 config.screen_preset。下面两张表就是这两层的当前内容。加条件类型属于改注册表，加方案属于存自己的组合。"}'),
('screen', 2, 6, 320, 'table', 'q_screen_rules','条件类型（config.screen_rule）','{}'),
('screen', 3, 6, 320, 'table', 'q_screen_presets','我的方案（config.screen_preset）','{}'),
('screen', 4, 12,420, 'table', 'q_top_movers',  '当日涨幅前列（快速筛选）','{}'),
('watchlist', 1, 12,460, 'table', 'q_group_heat','自选组热度（加权分 · 趋势 · 振幅）', '{}'),
-- 趋势曲线夹在两张表中间：先看组的横截面（谁强谁弱），再看这几个组的走势，
-- 最后才落到个股明细。顺序反过来的话，看完 224 只票已经忘了组的状态。
('watchlist', 2, 12,420, 'chart', 'group_score_trend','自选组趋势（加权操作分）','{}'),
-- page_size=100 / 高度 1040（2026-08-30 按用户要求拉长）：这张表是 1,100 多行的
-- 自选池明细，默认 15 行要翻七十几页，30 行也还要翻。「一页看多少」和「多高」
-- 都写在这一行，改它不用动代码。表头是冻结的，拉长不会变成一片认不出列的数字。
('watchlist', 3, 12,1040, 'table', 'q_watchlist', '自选池明细', '{"page_size":100}'),
('scores', 1, 8, 460, 'chart', 'quadrant_scatter','四象限散点','{}'),
('scores', 2, 4, 460, 'table', 'q_vol_stage_dist','量能阶段分布', '{}'),
-- ★ slot 3 原来是「X>30 且 Y>30 的双高个股」（`q_quadrant_strong`），
--   **2026-09-01 删掉**：它和上半截的「选票结果」同一天、同一批票、同一个口径，
--   只是门槛与排序写死；现在选票结果**默认就是双高**（Q1 ＋ X≥30 ＋ Y≥30，
--   按 X+Y 排序，见 app/web/pages/scores.py 的 DEFAULT_MIN_X/Y/SORT），
--   同一个问题不摆两张表。下面 ⑬ 有 DELETE 清库里的旧行。
--   `q_quadrant_strong` 这个查询**保留注册**：它仍是一块可用的积木，
--   想单独摆一张写死 40/40 门槛的表，在「积木管理」里加一行就行。
-- 4：**合表**（2026-08-31）。原来 4「个股明细」+ 5「操作分排行」是两张卡，
-- 同一天、同一批票、同一个排序键（操作分从高到低），区别只是一张多量能列、
-- 一张多板块列和名次 —— 同一只票要在上下两个表格里各找一次，才能同时看到
-- "它的量能阶段"和"它所在的板块热不热"，而这两件事是同一个判断的两半。
-- page_size=30（用户指定"默认显示前 30 个，后面翻页"），一屏铺完 30 行。
-- limit 300 → **666**（2026-09-01）：下面 ⑤ 的统计范围放大到前 666 只，
-- 两张卡必须取同一批票，否则统计里会有一批票在这张表上根本找不到。
-- 实测 0.32s（300 行时 0.28s），page_size 不变，多出来的行走翻页。
('scores', 4, 12,840, 'table', 'q_stock_scan',
    '个股扫描（明细 ＋ 操作分排行 · 一张表）',
    '{"page_size":30,"drilldown":"stock","params":{"limit":666,"sectors":3},"color":{"pct":["oper","x_quality","y_env","pct_change"],"amp":["turnover_rate_percent"]}}'),
-- 5：前 666 只票里板块反复出现几次。"第 3、7、12 名都在同一个概念里"这件事
-- 靠眼睛在几百行里扫是扫不出来的，而它正是"今天是不是板块行情"的直接答案。
-- **2026-09-01 从前 100 只放大到 666 只**（用户要求"扩大范围"）：100 只只占
-- 全市场 5,545 只的 1.8%，一个真在扩散的板块在里面往往只露 3~5 只，跟噪声
-- 分不开；666 只是 12%。实测 0.17s，代价可以忽略。
-- ⚠️「其它板块」那一列是**逐个板块计数**（一只票贡献第 2~3 个板块），
-- 所以那一列的总数必然超过 666（实测 1,328），与「最热板块」那一列不同量纲。
('scores', 5, 12,380, 'table', 'q_hot_sector_stats',
    '前 666 只票的板块出现次数 Top10（最热板块 ／ 其它板块）',
    '{"page_size":10,"params":{"top_n":666,"sectors":3,"limit":10}}'),
-- 6：两榜前三交叉（2026-09-01 用户点名要的）。上一张卡只给次数，看不出
-- "智能家居 32 次"和"机器人概念 23 次"是不是同一批票撑起来的。3×3＝9 行
-- **一行都不省**（0 只票也占一行），高度 620：个股那一列最长实测 88 个字，
-- 12 宽的卡里会折成 2~3 行，按 fit_height 的 23px 单行算会矮一半、表格自己滚。
('scores', 6, 12,620, 'table', 'q_hot_sector_cross',
    '两榜前三交叉：最热板块 × 其它板块（9 种组合 · 各自的票）',
    '{"page_size":9,"params":{"top_n":666,"sectors":3,"top_k":3},"color":{"pct":["cross_avg_chg"]}}'),
-- 7：权重表 2026-08-31 补上「判据 / 子项 / 合成方式」三列（来自每个 builtin 的
-- EXPLAIN，见 app/compute/explain.py）—— 原来只看得出"形态占 0.25"，
-- 看不出"形态"在算什么。高度 300 → 560：一页 20 行要 76+23×20+12=548
-- （见 ui.fit_height），520 差出一行半、表格自己滚，页面上看不出是高度算错了。
('scores', 7, 12,560, 'table', 'q_effective_binding',
    '当前有效权重（config.binding）· 含每个脚本的内容说明', '{"page_size":20}'),
-- 8：四象限**趋势**（2026-08-31 新增）。上面几张卡都是"今天的切片"，这一张回答
-- "近 10 个交易日在往哪走" —— 同样 60 分的票，从 20 分爬上来的和从 90 分掉下来的，
-- 在切片里长得一模一样。放在最后是因为要先知道今天什么样，才谈得上方向。
-- 高度 1070 ≈ 一屏铺完 40 行（抬头 32 + 过滤行 32 + 23×40 + 12 + 卡片抬头 60；
-- 行高 23 是收窄 padding 之后的值，见 ui.fit_height）。
-- options.color 让趋势分、距最高、各项分按正负染红绿，`quad_up` 的 `↑`（升格）
-- 红字加粗，`r2` < 0.5 压灰当"这条别信"（见 ui._table_color）。
-- ⚠️ flag 的列只能是**值恰好等于那个符号**的列：一开始把 `quad_path` 也写进去了，
-- 它的值是 `Q2→Q1`，永远匹配不上 —— 配色匹配不上是静默失效，页面不报错。
('scores', 8, 12,1070,'table', 'q_quadrant_trend',
    '四象限趋势分（近 10 个交易日 · 按趋势分从高到低）',
    '{"page_size":40,"params":{"window_days":10,"min_n":8,"limit":300},"color":{"pct":["trend_score","gap_from_max","pred_oper","oper_score","x_score","y_score","xy_total"],"flag":{"cols":["quad_up"],"value":"↑"},"dim":{"cols":["r2"],"below":0.5}}}'),
-- 9：倍量震荡榜（2026-08-31 加，用户点名要的）。近 40 个交易日频繁倍量、
-- 且价格沿 60 日线震荡的票，按倍量次数从高到低前 50。
-- **读 analytics.volume_double 不现算**：现算 10 秒（MA60 要 60 根暖机 + 40 天观察窗，
-- 一次扫 55 万行），放页面上就是点一下卡死。
-- 入选阈值不写在 options 里而是走 config.setting['vol_double.filter']：
-- 这几个阈值（倍量次数、偏离度、穿越次数、60 线变化、成交额）是一组要一起调的
-- 判据，散在两处会出现"页面写 8%、库里按 10% 筛"这种两边都像对的错。
-- 高度 1300：表头 54 + 50 行 × 23 + 尾 12 = 1216，再给翻页条留 ~84（同 slot 8 的算法）。
('scores', 9, 12,1300,'table', 'q_vol_double',
    '倍量震荡（近 40 日频繁倍量 ＋ 沿 60 日线震荡 · 按倍量次数前 50）',
    '{"page_size":50,"drilldown":"stock","color":{"pct":["dbl_count","dbl_prev_count","cross_count"],"amp":["dev_med","dev_max","ma60_chg"]}}'),





('jobs',   1, 12,300, 'task_list', NULL,        '最近任务',   '{"limit":20}')
ON CONFLICT (page_key, slot) DO UPDATE SET
    width = EXCLUDED.width, height = EXCLUDED.height, card_kind = EXCLUDED.card_kind,
    card_ref = EXCLUDED.card_ref, title = EXCLUDED.title, options = EXCLUDED.options;


-- =============================================================================
-- ⑭ 外部数据源
-- =============================================================================
-- health 的初值全部写 'unknown'：**播种时不预设结论**，跑一次探测再说。
-- 唯一例外是两条已经查清楚"不是故障"的：
--   Tushare 的 6 个接口在本项目 token 下是 40203 无权限（买权限或换源，不是修网络）
--   akshare 的 stock_margin_szse 自身抛 Length mismatch（等 akshare 修，重试无用）
-- 这两类先标出来，避免每次探测都在页面上冒红。
--
-- **auth_env_name 只写环境变量名**。密钥在 .env 里，库里一个字符都不存 ——
-- 库要备份、要给别人看、SQL 查询台能读它，密钥不能进去。
--
-- probe_spec.args 里 "{date}" 是占位符，探测时替换成最近交易日。
INSERT INTO config.datasource
    (datasource_key, name, kind, provider, endpoint, auth_env_name, quota_limit, quota_reset,
     health, probe_spec, display_order, note) VALUES

-- ---- akshare（交易所与东财转发，无 token） ----
('ak_pkg',         'akshare 包',        'package','akshare','__version__', NULL, NULL, NULL,
    'unknown', '{"kind":"version","module":"akshare"}', 10,
    '所有 akshare 子接口的前提，包本身装没装'),
('ak_etf_sse',     'ETF份额·上交所',    'package','akshare','fund_etf_scale_sse', NULL, NULL, NULL,
    'unknown', '{"kind":"df","fn":"fund_etf_scale_sse","args":{"date":"{date}"}}', 20,
    '897 只，含 跨市/单市/跨境/其他 分类；→ market_data.etf_share'),
('ak_etf_szse',    'ETF份额·深交所',    'package','akshare','fund_scale_daily_szse', NULL, NULL, NULL,
    'unknown', '{"kind":"df","fn":"fund_scale_daily_szse","args":{"start_date":"{date}","end_date":"{date}","symbol":"ETF"}}', 21,
    '724 只；第三个参数名是 symbol 不是 category'),
('ak_margin_sse',  '融资融券明细·上交所','package','akshare','stock_margin_detail_sse', NULL, NULL, NULL,
    'unknown', '{"kind":"df","fn":"stock_margin_detail_sse","args":{"date":"{date}"}}', 30,
    '1999 只 9 列，给偿还额不给融券余额；→ market_data.margin_detail'),
('ak_margin_szse', '融资融券明细·深交所','package','akshare','stock_margin_detail_szse', NULL, NULL, NULL,
    'unknown', '{"kind":"df","fn":"stock_margin_detail_szse","args":{"date":"{date}"}}', 31,
    '2101 只 8 列；当日盘中才发布，早间查当天会返回 0 行'),
('ak_margin_sum_sse','融资融券汇总·上交所','package','akshare','stock_margin_sse', NULL, NULL, NULL,
    'unknown', '{"kind":"df","fn":"stock_margin_sse","args":{}}', 32, '市场总量 18 行'),
('ak_margin_sum_szse','融资融券汇总·深交所','package','akshare','stock_margin_szse', NULL, NULL, NULL,
    'broken', '{"kind":"df","fn":"stock_margin_szse","args":{"date":"{date}"}}', 33,
    'akshare 1.18.88 内部抛 Length mismatch，接口自身 bug，不要接；用 market_data.margin_detail 求和代替'),
('ak_dragon_tiger_detail',  '龙虎榜明细',        'package','akshare','stock_lhb_detail_em', NULL, NULL, NULL,
    'unknown', '{"kind":"df","fn":"stock_lhb_detail_em","args":{"start_date":"{date}","end_date":"{date}"}}', 40,
    '21 列；一票一天可多条原因；→ market_data.dragon_tiger_detail'),
('ak_dragon_tiger_inst',    '龙虎榜机构统计',    'package','akshare','stock_lhb_jgmmtj_em', NULL, NULL, NULL,
    'unknown', '{"kind":"df","fn":"stock_lhb_jgmmtj_em","args":{"start_date":"{date}","end_date":"{date}"}}', 41,
    '16 列；只统计机构专用席位；→ market_data.dragon_tiger_institution'),
('ak_concept_em',  '东财概念板块列表',  'package','eastmoney','stock_board_concept_name_em', NULL, NULL, NULL,
    'degraded', '{"kind":"df","fn":"stock_board_concept_name_em","args":{}}', 50,
    '实测同一时段连续 3 次 RemoteDisconnected；概念热度依赖它，必须带重试与降级'),
('ak_fundflow_em', '东财个股资金流',    'package','eastmoney','stock_individual_fund_flow', NULL, NULL, NULL,
    'degraded', '{"kind":"df","fn":"stock_individual_fund_flow","args":{"stock":"600519","market":"sh"}}', 51,
    '同上，实测 3/3 全败；概念热度的资金流向分量缺口就在这里'),

-- ---- 其它 Python 源 ----
('baostock_pkg',   'baostock 包',       'package','baostock','baostock', NULL, NULL, NULL,
    'unknown', '{"kind":"version","module":"baostock"}', 60, '复权因子与财务数据备用源'),
('mootdx_pkg',     'mootdx 包',         'package','mootdx','mootdx', NULL, NULL, NULL,
    'unknown', '{"kind":"version","module":"mootdx"}', 61, '通达信协议直连，vipdoc 之外的兜底'),

-- ---- Tushare（需 token；本项目权限受限） ----
('ts_pkg',         'Tushare 包与 token','package','tushare','tushare', 'FT_TUSHARE_TOKEN', NULL, NULL,
    'unknown', '{"kind":"tushare","api":"trade_cal","args":{"start_date":"{date}","end_date":"{date}"}}', 70,
    'trade_cal 有权限，可用来回补 reference_data.trade_calendar'),
('ts_margin',      'Tushare 融资融券',  'package','tushare','margin', 'FT_TUSHARE_TOKEN', NULL, NULL,
    'no_permission', '{"kind":"tushare","api":"margin","args":{"trade_date":"{date}"}}', 71,
    '40203 无权限。融资融券改走 ak_margin_sse / ak_margin_szse'),
('ts_margin_detail','Tushare 融资融券明细','package','tushare','margin_detail','FT_TUSHARE_TOKEN', NULL, NULL,
    'no_permission', '{"kind":"tushare","api":"margin_detail","args":{"trade_date":"{date}"}}', 72,
    '40203 无权限'),
('ts_top_list',    'Tushare 龙虎榜',    'package','tushare','top_list', 'FT_TUSHARE_TOKEN', NULL, NULL,
    'no_permission', '{"kind":"tushare","api":"top_list","args":{"trade_date":"{date}"}}', 73,
    '40203 无权限。龙虎榜改走 ak_dragon_tiger_detail'),
('ts_top_inst',    'Tushare 龙虎榜机构','package','tushare','top_inst', 'FT_TUSHARE_TOKEN', NULL, NULL,
    'no_permission', '{"kind":"tushare","api":"top_inst","args":{"trade_date":"{date}"}}', 74,
    '40203 无权限'),
('ts_moneyflow',   'Tushare 资金流向',  'package','tushare','moneyflow','FT_TUSHARE_TOKEN', NULL, NULL,
    'no_permission', '{"kind":"tushare","api":"moneyflow","args":{"trade_date":"{date}"}}', 75,
    '40203 无权限。概念热度的资金流向分量因此缺口'),
('ts_ths_index',   'Tushare 同花顺板块','package','tushare','ths_index','FT_TUSHARE_TOKEN', NULL, NULL,
    'no_permission', '{"kind":"tushare","api":"ths_index","args":{}}', 76, '40203 无权限'),

-- ---- HTTP / MCP / 本地 ----
('em_push2',       '东财 push2 行情',   'http','eastmoney',
    'https://push2.eastmoney.com/api/qt/ulist.np/get', NULL, NULL, NULL,
    'unknown', '{"kind":"http","url":"https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids=1.000001&fields=f2,f3,f12,f14"}', 80,
    '实时快照，盘中用。注意 URL 里不能出现裸 + 号（会被解成空格），要 %2B'),
('miaoxiang',      '妙想金融大模型',    'http','miaoxiang', 'https://gpt.cn', 'FT_MIAOXIANG_KEY', NULL, NULL,
    'unknown', '{"kind":"http","url":"https://gpt.cn"}', 90, '研报与舆情摘要'),
('alphafeed',      'AlphaFeed 资讯',    'http','alphafeed', NULL, 'FT_ALPHAFEED_KEY', NULL, NULL,
    'unknown', '{"kind":"env","var":"FT_ALPHAFEED_KEY"}', 91, '只校验密钥在不在环境里'),
('tushare_mcp',    'Tushare MCP',       'mcp','tushare', NULL, 'FT_TUSHARE_TOKEN', NULL, NULL,
    'unknown', '{"kind":"noop"}', 95,
    '需 Accept: text/event-stream；交互式鉴权的 MCP 在无人值守跑批时可能不可用'),
('tdx_vipdoc',     '通达信 vipdoc',     'local','tdx', NULL, NULL, NULL, NULL,
    'unknown', '{"kind":"vipdoc"}', 5,
    '日线与分钟线的权威源，判据是文件里的最新日期而不是文件时间戳'),
-- 通达信 TQ 的本地 JSON-RPC。官方 Skill 文档（tdx-tq-local）明文要求走这条
-- HTTP 路，禁止 import tqcenter / tq.initialize()；旧的 tq_bridge 子进程路
-- 降级为备用。实测分钟线 0.03s、日线到当日收盘，且**不出网**，所以是 P0。
('tq_http',        '通达信 TQ 本地接口','http','tdx','http://127.0.0.1:17709/',
    NULL, NULL, NULL,
    'unknown', '{"kind":"tqhttp"}', 4,
    'JSON-RPC；要开着支持 TQ 的通达信客户端。Amount 单位是万元；不复权数据带 ForwardFactor'),

-- 同花顺本地 ini。和 vipdoc 不冲突而是**互补**：vipdoc 的 231 个行业板块
-- 有行情没成分股，同花顺的 90 个行业有名有成分股没行情，缝起来才是完整的。
-- 三条分开登记而不是合成一条"同花顺本地"：板块清单每天变、行业清单跟安装包走、
-- 自选是用户自己改的，坏起来的原因完全不同，合成一条就看不出坏在哪一块。
('ths_local_block','同花顺本地板块',    'local','ths', NULL, NULL, NULL, NULL,
    'unknown', '{"kind":"ths","what":"block"}', 6,
    'BlockUpdate\\block_*.ini：概念/地域/特色指数/指标股 + 成分股'),
('ths_local_industry','同花顺本地行业',  'local','ths', NULL, NULL, NULL, NULL,
    'unknown', '{"kind":"ths","what":"industry"}', 7,
    'industry.ini：881* 二级行业带中文名，补通达信"有行情没成员"的行业板块'),
('ths_local_watchlist','同花顺自选分组','local','ths', NULL, NULL, NULL, NULL,
    'unknown', '{"kind":"ths","what":"watchlist"}', 8,
    'stockblock.ini：自选分组名与成员（通达信 zxg.blk 没有分组这一层）'),

-- 通达信收盘快照库。跟 tdx_vipdoc 分开登记：vipdoc 坏是"文件没下载"，
-- 这个坏是"客户端里的插件没跑"，判据和修法都不一样。
-- 探活看的是**最新那张 day_end_* 表的日期**，不是文件 mtime —— 通达信一开
-- 就会摸这个文件，看 mtime 会把"插件一个月没落新表"判成健康。
('tdx_snapshot_db','通达信收盘快照库',  'local','tdx', NULL, NULL, NULL, NULL,
    'unknown', '{"kind":"snapdb"}', 9,
    'PYPlugins\\user\\Get_DayEnd.py 每天落的 SQLite：市值/PE/PB/内外盘/涨跌停价')

ON CONFLICT (datasource_key) DO UPDATE SET
    name = EXCLUDED.name, kind = EXCLUDED.kind, provider = EXCLUDED.provider,
    endpoint = EXCLUDED.endpoint, auth_env_name = EXCLUDED.auth_env_name,
    probe_spec = EXCLUDED.probe_spec, display_order = EXCLUDED.display_order,
    note = EXCLUDED.note, updated_at = now();


-- ---------------------------------------------------------------------------
-- 数据源的层级与定位（用户 2026-08-30 给的口径，逐条对号入座）
--
-- 为什么跟上面那个 INSERT 分开写：上面那批是**探测怎么做**（endpoint、
-- probe_spec、auth_env_name），这一批是**该先用谁**。前者改一次要连带改探测代码，
-- 后者是纯口径，人在 /datasource → ② 数据源管理 里就能改。分开之后
-- 加一个子接口不用重新想它属于哪一层 —— 按 provider 归到同一层就行。
--
-- 层级（就是 P0/主力/基座/辅助）：
--   p0     通达信本地 vipdoc —— 有本地文件就不出网，这是硬优先级
--   main   主力层：日常生产真正在跑的（AKShare / 东财 push2 / 妙想）
--   base   基座层：历史底座、回测口径的参照（Baostock）
--   assist 辅助层：交叉验证、随手查（Tushare SDK / Tushare MCP）
--
-- ★这里写的是**口径**不是状态：ts_* 现在 health=no_permission、em_push2 现在
-- down，层级照样按"它本该担什么角色"填。两者混在一列里，一次网络抖动就会
-- 把"主力源"降级成"辅助源"，下次谁都不知道原本该先用谁。
--
-- 重跑 init_db 会用这里的值盖掉页面上的手工修改（跟 name/note 一样）——
-- 所以口径调整要改到这个文件里，别只在页面上改。
UPDATE config.datasource d
   SET layer = v.layer, ds_role = v.ds_role, cost = v.cost,
       tagline = v.tagline, updated_at = now()
  FROM (VALUES
    -- ---- P0：本地文件，优先级最高 ----
    ('tdx_vipdoc',        'p0',    '本地权威源',   '免费',
     '日线与分钟线的第一顺位，能读到本地文件就不出网'),
    -- TQ 本地接口也是 P0：回环调用不出网，且能给 vipdoc 给不了的东西
    -- （涨跌停状态与打开次数、股本、板块成分、当日盘中快照）
    ('tq_http',           'p0',    '本地权威源',   '免费',
     '开着通达信就能取：涨跌停/股本/板块成分/分钟线，回环不出网'),

    -- 同花顺本地也是 P0：明文 ini、每天刷新、不出网。它不跟 vipdoc 抢日线，
    -- 抢的是"板块成分股"和"自选分组"——那两块 vipdoc 给不全。
    ('ths_local_block',   'p0',    '本地板块与分组', '免费',
     '明文 ini，每日刷新，通达信解不出的板块成分股'),
    ('ths_local_industry','p0',    '本地板块与分组', '免费',
     '明文 ini，每日刷新，通达信解不出的板块成分股'),
    ('ths_local_watchlist','p0',   '本地板块与分组', '免费',
     '明文 ini，每日刷新，通达信解不出的板块成分股'),

    -- 收盘快照库也是 P0，但角色不同：它是**市值与估值的唯一本地来源**。
    -- reference_data.share_capital 只有股本变动日，算不出每天的流通市值；这里每天一行。
    ('tdx_snapshot_db',   'p0',    '本地市值与估值', '免费',
     '每日 115 字段快照：市值/PE/PB/内外盘/量比，别处只能靠网络凑'),



    -- ---- 主力层：日常生产 ----
    ('ak_pkg',            'main',  '日常生产主力', '免费',   '覆盖面最广'),
    ('ak_etf_sse',        'main',  '日常生产主力', '免费',   '覆盖面最广'),
    ('ak_etf_szse',       'main',  '日常生产主力', '免费',   '覆盖面最广'),
    ('ak_margin_sse',     'main',  '日常生产主力', '免费',   '覆盖面最广'),
    ('ak_margin_szse',    'main',  '日常生产主力', '免费',   '覆盖面最广'),
    ('ak_margin_sum_sse', 'main',  '日常生产主力', '免费',   '覆盖面最广'),
    ('ak_margin_sum_szse','main',  '日常生产主力', '免费',   '覆盖面最广'),
    ('ak_dragon_tiger_detail',     'main',  '日常生产主力', '免费',   '覆盖面最广'),
    ('ak_dragon_tiger_inst',       'main',  '日常生产主力', '免费',   '覆盖面最广'),
    ('ak_concept_em',     'main',  '日常生产主力', '免费',   '覆盖面最广'),
    ('ak_fundflow_em',    'main',  '日常生产主力', '免费',   '覆盖面最广'),
    ('em_push2',          'main',  '历史K线补充', '免费',
     '历史日K线质量最高'),
    ('miaoxiang',         'main',  'AI智能查询',   '免费·Key已配',
     '自然语言查行情、财务、选股、研报'),

    -- ---- 基座层：历史底座 ----
    ('baostock_pkg',      'base',  '历史底座',     '免费',
     '量化回测最稳定，匿名免注册'),

    -- ---- 辅助层：交叉验证 / 随手查 ----
    ('ts_pkg',            'assist','辅助校准',     '免费额度', '日线交叉验证'),
    ('ts_margin',         'assist','辅助校准',     '免费额度', '日线交叉验证'),
    ('ts_margin_detail',  'assist','辅助校准',     '免费额度', '日线交叉验证'),
    ('ts_top_list',       'assist','辅助校准',     '免费额度', '日线交叉验证'),
    ('ts_top_inst',       'assist','辅助校准',     '免费额度', '日线交叉验证'),
    ('ts_moneyflow',      'assist','辅助校准',     '免费额度', '日线交叉验证'),
    ('ts_ths_index',      'assist','辅助校准',     '免费额度', '日线交叉验证'),
    ('tushare_mcp',       'assist','随手查',       '免费',
     '仅非复权日线，50 次/分钟'),
    -- 用户给的表里没这两个，按它们实际担的活归到辅助层
    ('mootdx_pkg',        'assist','vipdoc 兜底',  '免费',
     '通达信协议直连，本地文件缺日期时才用'),
    ('alphafeed',         'assist','资讯补充',     '免费·需Key',
     '只做舆情/资讯，不参与行情口径')
  ) AS v(datasource_key, layer, ds_role, cost, tagline)
 WHERE d.datasource_key = v.datasource_key;


-- =============================================================================
-- 通用设置
-- =============================================================================
INSERT INTO config.setting (key, value, label, category) VALUES
('tdx_dir',           '"D:\\new_tdx64"',  '通达信安装目录', 'path'),
('ths_dir',           '"D:\\hanchen\\同花顺"', '同花顺安装目录', 'path'),
('trade_day_cutoff',  '"15:00"',          '交易日切换时刻', 'calendar'),
('worker_concurrency','3',                '任务并发度',     'runtime'),
('web_port',          '18420',            'Web 端口',       'runtime'),
('log_keep_days',     '30',               '日志保留天数',   'runtime'),
('default_adjust',    '"none"',           '默认复权方式',   'display'),
('amount_display',    '{"scale":100000000,"suffix":"亿","decimals":2}', '金额展示', 'display'),
('quadrant_labels',   '{"Q1":"买入","Q2":"持有","Q3":"试探","Q4":"回避"}', '象限标签', 'display'),
-- 板块热度趋势的窗口（交易日）。1 是"日趋势分值"（今日热度 − 昨日热度），
-- 其余是 N 个点的最小二乘斜率。**加窗口只改这个数组**：analytics.sector_trend 是长表，
-- q_sector_heat 的趋势列按这个数组自动生成，不用改 DDL、不用改页面。
('sector_trend.windows', '[1,3,5,7,9,11,15,21]', '板块热度趋势的窗口（交易日）', 'compute')

ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();


-- 访问控制两项**故意用 DO NOTHING**：上面那一段是 DO UPDATE，重跑 seed 会把值
-- 拍回默认；白名单和监听地址是用户在 /admin 上改的，被 seed 悄悄改回去等于
-- 「昨天还能连，今天同事就 403 了」。所以这两项只负责"第一次给个安全默认"。
-- loopback 不写进白名单：代码里永久放行（app/web/access.py），写进来反而像"可以删"。
INSERT INTO config.setting (key, value, label, category) VALUES
('web_allowlist',  '["192.168.176.67"]',
 'Web 访问白名单（IP 或 CIDR；本机 loopback 永久放行，不受此表影响）', 'security'),
('web_bind_host',  '"127.0.0.1"',
 'Web 监听地址（127.0.0.1=仅本机；0.0.0.0=局域网可达，仍受白名单限制）', 'security')
ON CONFLICT (key) DO NOTHING;


-- 启动点旋钮：**故意存一个空对象，也故意用 DO NOTHING**。
-- 全套默认值在 app/compute/ignition.py 的 DEFAULTS 里（阈值是样本外回测反推的，
-- 不是拍的）。这里存的是"覆盖项"，只写你想改的那几个 key，比如
--     {"ig_ignition": 70, "ig_volume": 65, "ig_breadth": 65}
-- 更严的一档：样本外 n=157 胜率 58.4 T+5 超额 +0.526（当前默认 n=196/57.5/+0.455）。
-- 为什么不把整份默认值抄进这里：抄进来之后代码里改默认就再也生效不了 ——
-- 库里那份会一直赢，变成"改了没反应"。空对象让两边只有一个真相来源。
-- 为什么 DO NOTHING：这是人调过的参数，重跑 seed 不能拍回去。
INSERT INTO config.setting (key, value, label, category) VALUES
('ignition.params', '{}',
 '趋势启动点参数覆盖（只写要改的键；默认值见 app/compute/ignition.py 的 DEFAULTS）',
 'compute')
ON CONFLICT (key) DO NOTHING;


-- 倍量震荡的入选阈值：同样**存空对象 + DO NOTHING**，理由同上（默认值在
-- app/compute/vol_double.py 的 FILTER_DEFAULTS，是按 2026-08-28 全市场 5,510 只
-- 票的实测分布定的）。只写要改的键，比如放宽到"倍量 ≥3 次、允许偏离 10%"：
--     {"min_dbl": 3, "dev_med_max": 10}
-- 这些阈值只影响**榜单怎么筛**，不影响 analytics.volume_double 里存的读数 ——
-- 全市场每票一行都落了库，改阈值立刻生效，不用重算那 10 秒。
INSERT INTO config.setting (key, value, label, category) VALUES
('vol_double.filter', '{}',
 '倍量震荡榜的入选阈值覆盖（只写要改的键；默认值见 app/compute/vol_double.py 的 FILTER_DEFAULTS）',
 'compute')
ON CONFLICT (key) DO NOTHING;


-- 通达信状态探测（2026-09-23 加，/admin → 「通达信状态探测」卡片维护）。
-- 代码默认值在 app/tdx_client/watchman.py 的 _DEFAULTS；这里只给"第一次的可见性"，
-- DO NOTHING：总开关是人在页面上按下的决定，重跑 seed 不该把它拍回默认。
INSERT INTO config.setting (key, value, label, category) VALUES
('tdx_watch.enabled',       'false', '通达信探测 · 总开关',              'tdx_watch'),
('tdx_watch.interval_min',  '10',    '通达信探测 · 间隔（分钟）',        'tdx_watch'),
('tdx_watch.auto_start',    'true',  '通达信探测 · 没在跑就自动开启并登录', 'tdx_watch'),
('tdx_watch.minimize',      'true',  '通达信探测 · 自愈后最小化主窗口',  'tdx_watch'),
('tdx_watch.restart_at',    '""',    '通达信探测 · 每日定时重启 HH:MM（空=不设）', 'tdx_watch')
ON CONFLICT (key) DO NOTHING;





-- 取数限流阈值（2026-08-31 加）。一个键一个限流组，值三项：
--   min_interval 两次请求最小间隔（秒）、per_min 1 分钟上限、per_5min 5 分钟上限
-- 三道一起用而不是只留一道：东财风控是多维度同时看的，只压 QPS 仍会被 5 分钟
-- 总量掀掉。东财这一组的数字是社区实测封禁线（5 QPS / 200 / 300）的**七折**——
-- 被封一次要等几十分钟，跑批慢十几秒和被封一小时之间没什么可犹豫的。
-- 本地文件与腾讯归 free 组、**刻意不限流**（不出网 / 实测不封 IP）。
-- 代码里的默认值在 app/datasource/registry.py 的 LIMITS；这里配了以库为准。
-- DO NOTHING：这是人可能调过的运行参数，重跑 seed 不该拍回去。
INSERT INTO config.setting (key, value, label, category) VALUES
('rate_limit.eastmoney', '{"min_interval":0.25,"per_min":140,"per_5min":210}',
 '东财限流（实测封禁线 5次/秒·200次/分·300次/5分 的七折）', 'datasource'),
('rate_limit.sina',      '{"min_interval":0.15,"per_min":200}',
 '新浪限流', 'datasource'),
('rate_limit.ths',       '{"min_interval":0.30,"per_min":120}',
 '同花顺网络接口限流（本地 ini 解析不受此限）', 'datasource'),
('rate_limit.tushare',   '{"min_interval":1.0,"per_min":1}',
 'Tushare 限流（2026-08-31 实测：配额按接口算，每接口 1 次/分钟，不是账号 50 次/分钟）',
 'datasource'),

('rate_limit.free',      '{}',
 '不限流的一组（本地文件 / 腾讯 / baostock）', 'datasource')
ON CONFLICT (key) DO NOTHING;


-- =============================================================================
-- ② 字段口径（核心表全字段登记；文档与 COMMENT 由此生成）
-- =============================================================================

INSERT INTO config.field_spec (table_name, column_name, label_cn, unit, dtype, value_range, sane_range, formula, source_key, display) VALUES
('market_data.stock_daily','security_code','证券代码','text','text',NULL,NULL,'6位数字.市场后缀，如 600367.SH','vipdoc_day','{}'),
('market_data.stock_daily','trade_date','交易日期','date','date',NULL,NULL,'来自 .day 第1字段 uint32(20260825)，转 date','vipdoc_day','{}'),
('market_data.stock_daily','open','开盘价','yuan','numeric','[0,null]','[0.01,10000]','.day 第2字段 uint32(分) ÷ 100','vipdoc_day','{"decimals":2}'),
('market_data.stock_daily','high','最高价','yuan','numeric','[0,null]','[0.01,10000]','.day 第3字段 ÷ 100','vipdoc_day','{"decimals":2}'),
('market_data.stock_daily','low','最低价','yuan','numeric','[0,null]','[0.01,10000]','.day 第4字段 ÷ 100','vipdoc_day','{"decimals":2}'),
('market_data.stock_daily','close','收盘价','yuan','numeric','[0,null]','[0.01,10000]','.day 第5字段 ÷ 100','vipdoc_day','{"decimals":2}'),
('market_data.stock_daily','pre_close','前收盘价','yuan','numeric','[0,null]','[0.01,10000]','上一交易日 close（除权日按 adjustment_factor 调整）','derived','{"decimals":2}'),
('market_data.stock_daily','change_yuan','涨跌额','yuan','numeric',NULL,'[-1000,1000]','close - pre_close','derived','{"decimals":2}'),
('market_data.stock_daily','pct_change','涨跌幅','pct','numeric','[-100,1000]','[-31,31]','(close - pre_close) / pre_close * 100','derived','{"decimals":2,"suffix":"%"}'),
('market_data.stock_daily','volume_shares','成交量','shares','bigint','[0,10000000000000]','[0,1000000000000]','.day 第7字段 uint32，原始单位即股；显示为手时 ÷100','vipdoc_day','{"scale":100,"suffix":"手"}'),
('market_data.stock_daily','amount_yuan','成交额','yuan','numeric','[0,10000000000000]','[10000,1000000000000]','.day 第6字段 float32，原始单位即元，不做任何换算','vipdoc_day','{"scale":100000000,"suffix":"亿","decimals":2}'),
('market_data.stock_daily','turnover_rate_percent','换手率','pct','numeric','[0,10000]','[0,100]','volume_shares / reference_data.security.float_share * 100','derived','{"decimals":2,"suffix":"%"}'),
('market_data.stock_daily','limit_up','涨停价','yuan','numeric','[0,null]',NULL,'pre_close × (1 + 当日档位)，档位的权威与出处见 reference_data.limit_price_daily、app/compute/limit_prices.py、本表 limit_basis 列；NULL 表示 board 未知或当日不受涨跌停限制','derived','{"decimals":2}'),
('market_data.stock_daily','limit_down','跌停价','yuan','numeric','[0,null]',NULL,'pre_close × (1 - 当日档位)，同上；board_limit_ratio 只作最后兜底，不许拿当前 is_st 快照套历史','derived','{"decimals":2}'),
('market_data.stock_daily','adjustment_factor','后复权因子','ratio','numeric','[0,null]','[0.01,1000]','由 reference_data.dividend 从最新交易日往前累乘；NULL 表示未计算','derived','{"decimals":6}'),
('market_data.stock_daily','source_batch_id','来源批次','count','bigint',NULL,NULL,'→ operations.ingest_batch，强制血缘，NOT NULL','derived','{}'),

('market_data.stock_minute','security_code','证券代码','text','text',NULL,NULL,NULL,'vipdoc_lc1','{}'),
('market_data.stock_minute','dt','时间戳','date','timestamptz',NULL,NULL,'由 packed 高16位(当日分钟数) + 低16位(1899-12-30 起算日序) 解出','vipdoc_lc1','{}'),
('market_data.stock_minute','period','周期','count','smallint','[1,5]',NULL,'1=lc1 5=lc5','vipdoc_lc1','{}'),
('market_data.stock_minute','open','开盘价','yuan','numeric','[0,null]','[0.01,10000]','float32，已是元，不需 ÷100','vipdoc_lc1','{"decimals":2}'),
('market_data.stock_minute','high','最高价','yuan','numeric','[0,null]','[0.01,10000]',NULL,'vipdoc_lc1','{"decimals":2}'),
('market_data.stock_minute','low','最低价','yuan','numeric','[0,null]','[0.01,10000]',NULL,'vipdoc_lc1','{"decimals":2}'),
('market_data.stock_minute','close','收盘价','yuan','numeric','[0,null]','[0.01,10000]',NULL,'vipdoc_lc1','{"decimals":2}'),
('market_data.stock_minute','volume_shares','成交量','shares','bigint','[0,null]',NULL,'原始单位股','vipdoc_lc1','{"scale":100,"suffix":"手"}'),
('market_data.stock_minute','amount_yuan','成交额','yuan','numeric','[0,null]',NULL,'float32，单位元','vipdoc_lc1','{"scale":10000,"suffix":"万"}'),

('market_data.index_daily','security_code','指数代码','text','text',NULL,NULL,NULL,'vipdoc_day','{}'),
('market_data.index_daily','close','收盘点位','none','numeric','[0,null]','[1,100000]',NULL,'vipdoc_day','{"decimals":2}'),
('market_data.index_daily','pct_change','涨跌幅','pct','numeric','[-100,1000]','[-15,15]',NULL,'derived','{"decimals":2,"suffix":"%"}'),
('market_data.index_daily','amount_yuan','成交额','yuan','numeric','[0,null]',NULL,'单位元','vipdoc_day','{"scale":100000000,"suffix":"亿"}'),

('market_data.sector_daily','sector_code','板块代码','text','text',NULL,NULL,'通达信板块伪代码 880*/881*','vipdoc_day','{}'),
('market_data.sector_daily','pct_change','板块涨幅','pct','numeric','[-100,1000]','[-20,20]',NULL,'derived','{"decimals":2,"suffix":"%"}'),
('market_data.sector_daily','up_count','上涨家数','count','int','[0,null]',NULL,NULL,'derived','{}'),
('market_data.sector_daily','down_count','下跌家数','count','int','[0,null]',NULL,NULL,'derived','{}'),
('market_data.sector_daily','member_count','成分数','count','int','[0,null]',NULL,NULL,'derived','{}'),
('market_data.sector_daily','amount_yuan','成交额','yuan','numeric','[0,null]',NULL,'单位元','derived','{"scale":100000000,"suffix":"亿"}'),

('market_data.stock_snapshot','pe_ttm','PE(TTM)','ratio','numeric',NULL,'[-1000,1000]',NULL,'tqcenter_snapshot','{"decimals":2}'),
('market_data.stock_snapshot','pb','PB','ratio','numeric',NULL,'[0,100]',NULL,'tqcenter_snapshot','{"decimals":2}'),
('market_data.stock_snapshot','total_market_value_yuan','总市值','yuan','numeric','[0,null]',NULL,'单位元','tqcenter_snapshot','{"scale":100000000,"suffix":"亿"}'),
('market_data.stock_snapshot','float_market_value_yuan','流通市值','yuan','numeric','[0,null]',NULL,'单位元','tqcenter_snapshot','{"scale":100000000,"suffix":"亿"}'),
('market_data.stock_snapshot','eps','每股收益','yuan','numeric',NULL,'[-100,100]',NULL,'tqcenter_snapshot','{"decimals":4}'),
('market_data.stock_snapshot','roe_percent','ROE','pct','numeric',NULL,'[-200,200]',NULL,'tqcenter_snapshot','{"decimals":2,"suffix":"%"}'),

('reference_data.security','security_code','证券代码','text','text',NULL,NULL,NULL,NULL,'{}'),
('reference_data.security','symbol','股票代码','text','text',NULL,NULL,'6 位数字','tdx_tnf','{}'),
('reference_data.security','market','市场','text','text',NULL,NULL,'以文件所在市场目录为唯一判据：sh→SH sz→SZ bj→BJ','vipdoc_day','{}'),
('reference_data.security','name','名称','text','text',NULL,NULL,'.tnf offset 31，GBK 解码','tdx_tnf','{}'),
('reference_data.security','pinyin','全拼','text','text',NULL,NULL,'pypinyin.lazy_pinyin(name)','tdx_tnf','{}'),
('reference_data.security','pinyin_abbr','拼音首字母','text','text',NULL,NULL,'全拼各字首字母拼接','tdx_tnf','{}'),
('reference_data.security','board','板块','text','text',NULL,NULL,'主板|创业板|科创板|北交所。决定涨跌停幅度 10/20/20/30%','tqcenter_sector','{}'),
('reference_data.security','asset_type','资产类型','text','text',NULL,NULL,'stock|index|etf|sector|other，按代码前缀判定','derived','{}'),
('reference_data.security','is_st','是否ST','none','boolean',NULL,NULL,'ST 主板涨跌停限 5%','derived','{}'),
('reference_data.security','total_share','总股本','shares','bigint','[0,null]',NULL,'单位股','tqcenter_gb','{"scale":100000000,"suffix":"亿股"}'),
('reference_data.security','float_share','流通股本','shares','bigint','[0,null]',NULL,'单位股，换手率分母','tqcenter_gb','{"scale":100000000,"suffix":"亿股"}'),

('reference_data.trade_calendar','calendar_date','日期','date','date',NULL,NULL,NULL,'tqcenter_calendar','{}'),
('reference_data.trade_calendar','is_open','是否交易日','none','boolean',NULL,NULL,'表驱动，认节假日。旧系统用 weekday()<5 是错的','tqcenter_calendar','{}'),

('reference_data.dividend','cash_per_share','每股现金红利','yuan','numeric','[0,null]',NULL,'税前','tqcenter_divid','{"decimals":6}'),
('reference_data.dividend','bonus_ratio','每股送股比','ratio','numeric','[0,null]',NULL,NULL,'tqcenter_divid','{"decimals":6}'),
('reference_data.dividend','rights_ratio','每股配股比','ratio','numeric','[0,null]',NULL,NULL,'tqcenter_divid','{"decimals":6}'),

('analytics.score','capability_key','功能点','text','text',NULL,NULL,'→ config.capability','derived','{}'),
('analytics.score','value','评分','none','numeric','[-100,100]',NULL,'按 config.view_effective_binding 归一化权重加权合成','derived','{"decimals":2}'),
('analytics.score','quadrant','象限','text','text',NULL,NULL,'Q1买入 Q2持有 Q3试探 Q4回避','derived','{}'),
('analytics.score','is_stale','结果过期','none','boolean',NULL,NULL,'脚本 checksum 或权重变更后自动置 true','derived','{}'),
('analytics.score_detail','component_value','子项分值','none','numeric','[-100,100]',NULL,NULL,'derived','{"decimals":2}'),
('analytics.score_detail','weight','权重','ratio','numeric','[0,1]',NULL,'来自 config.binding，按 enabled 归一化','derived','{"decimals":4}'),
('analytics.score_detail','contribution','贡献值','none','numeric',NULL,NULL,'component_value * weight','derived','{"decimals":2}'),
('analytics.indicator_value','indicator_key','指标','text','text',NULL,NULL,'→ config.indicator','derived','{}'),
('analytics.indicator_value','value','指标值','none','numeric',NULL,NULL,'由 config.indicator.expr 经受限求值器计算','derived','{"decimals":4}')
ON CONFLICT (table_name, column_name) DO UPDATE SET
    label_cn = EXCLUDED.label_cn, unit = EXCLUDED.unit, dtype = EXCLUDED.dtype,
    value_range = EXCLUDED.value_range, sane_range = EXCLUDED.sane_range,
    formula = EXCLUDED.formula, source_key = EXCLUDED.source_key,
    display = EXCLUDED.display, updated_at = now();


-- ================================================================ 通达信弹窗规则
-- 看门狗（app/tdx_client/watchdog.py）按这张表处理弹窗。
-- **priority 小的先匹配**：ignore/alert 必须排在 close 前面，
-- 否则 `提示` 这种宽泛规则会先把 `TQ数据设置` 捞走然后关掉它，
-- 下载还没点对话框就没了，日志上完全看不出是自己关的。
INSERT INTO config.popup_rule
    (rule_key, match_type, pattern, action, button_text, priority, note) VALUES
-- ① 绝不能关的（关掉等于破坏流程）
('keep_tq_setting','title','TQ数据设置','ignore',NULL,1,
 '**千万不能关**：手动下载的主对话框，关掉整条链路就断'),
('keep_download','title','数据下载','ignore',NULL,1,
 '下载进度窗口，它自己消失是完成判据之一'),
-- 「您确定取消下载吗？」决定框。实测标题是 `'TdxW'`（通达信没给它起名字），
-- 标题规则只有这一种窗口会命中。**配 alert 不配 close**：这个框两个分支
-- 一个继续下载、一个放弃整轮，按 X 走哪个没人知道；由 `s8_finish.answer_confirm()`
-- 按"这一轮判没判完成/判没判死"来点它自己那两个按钮之一。
-- `watchdog._close` 里另有一道**看正文**的硬闸（标题不是稳定信息，两道都要在）。
('cancel_download_confirm','title','TdxW','alert',NULL,1,
 '**只报不关**：这是"要不要放弃这一轮下载"的决定框，关它等于替用户赌默认分支'),
('keep_main','title','通达信金融终端','ignore',NULL,2,
 '主窗口。注意登录框标题跟主窗口一样（都是 通达信金融终端Vx.xx），'
 '所以这条也会盖住登录框 —— 正合意，登录框由 s2_login 自己处理'),
-- ② 只报不关的（关了也解决不了，得人来定）
('login_elsewhere','title','别处登录','alert',NULL,5,
 '账号在别的机器登录了，关掉行情也不恢复，必须重新登录'),
('disk_full','title','空间不足','alert',NULL,5,'磁盘满了，关掉下载照样失败'),
('net_broken','title','连接失败','alert',NULL,6,'行情连不上，下载必然失败'),
('pwd_hint','title','密码','alert',NULL,6,
 '口令相关提示，可能是密码错了，交给 s2_login 判断'),
-- ③ 关掉的
('tdx_info','title','通达信信息','close',NULL,10,'登录后的公告窗口，每次都有'),
('notice','title','公告','close',NULL,10,NULL),
('upgrade','title','升级','close',NULL,10,'版本升级提示，不升'),
('news','title','资讯','close',NULL,20,NULL),
('tips','title','提示','close',NULL,30,
 '兜底的宽泛规则，priority 必须排在所有 ignore/alert 之后'),
-- ④ 需要点具体按钮的（模态框不吃 WM_CLOSE）
('modal_ok','class','#32770','button','确定',80,
 '默认关闭；只有当某个模态框连着 3 次 WM_CLOSE 都关不掉时才靠它，'
 '默认 is_active=false 是因为它太宽泛，会误点到别的对话框上')
ON CONFLICT (rule_key) DO UPDATE SET
    match_type = EXCLUDED.match_type, pattern = EXCLUDED.pattern,
    action = EXCLUDED.action, button_text = EXCLUDED.button_text,
    priority = EXCLUDED.priority, note = EXCLUDED.note, updated_at = now();

UPDATE config.popup_rule SET is_active = false WHERE rule_key = 'modal_ok';


-- 用 field_spec 生成库注释：文档、注释、校验单一来源
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT table_name, column_name, label_cn, unit, formula FROM config.field_spec LOOP
        BEGIN
            EXECUTE format('COMMENT ON COLUMN %s.%I IS %L',
                r.table_name, r.column_name,
                r.label_cn || '（单位:' || r.unit || '）' ||
                coalesce(' | ' || r.formula, ''));
        EXCEPTION WHEN undefined_table OR undefined_column THEN
            RAISE NOTICE 'field_spec 登记了不存在的列: %.%', r.table_name, r.column_name;
        END;
    END LOOP;
END $$;
