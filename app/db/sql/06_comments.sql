-- =====================================================================
-- 06_comments.sql · 表与字段的中文注释
--
-- 为什么放在库里而不是只写文档：注释跟着表走。pgAdmin、DBeaver、
-- \d+ 都能直接看到，字段改名时注释一起改，不会像外部文档那样悄悄过期。
-- 也是 /blocks 页面字段字典（operations.view_field_dict）之外的第二道说明。
--
-- 幂等：COMMENT ON 是覆盖式的，重复执行结果一致。
-- 分区子表（market_data.stock_daily_2021 等）不逐个写，末尾的 DO 块从父表传播。
-- 暂存表（market_data.stg_*）是跑批期间临时建的，用完即删，不注释。
-- =====================================================================

-- ---------------------------------------------------------------- ref
COMMENT ON TABLE reference_data.security IS '证券主数据：一个代码一行，全库的代码维度都外键到这里';
COMMENT ON COLUMN reference_data.security.security_code IS '标准代码，六位数字加交易所后缀，如 600519.SH';
COMMENT ON COLUMN reference_data.security.symbol IS '六位数字代码，不含交易所后缀';
COMMENT ON COLUMN reference_data.security.market IS '交易所：SH 上交所 / SZ 深交所 / BJ 北交所';
COMMENT ON COLUMN reference_data.security.name IS '证券简称，来自通达信 hq_cache\*.tnf（旧库没有名称数据）';
COMMENT ON COLUMN reference_data.security.pinyin IS '简称全拼，供搜索框按拼音匹配';
COMMENT ON COLUMN reference_data.security.pinyin_abbr IS '简称拼音首字母缩写，如 GZMT';
COMMENT ON COLUMN reference_data.security.board IS '板块归属：主板 / 创业板 / 科创板 / 北交所。决定涨跌停比例，按代码段判定';
COMMENT ON COLUMN reference_data.security.asset_type IS '品种：stock 个股 / index 指数 / etf 基金 / sector 通达信板块 / other 其他';
COMMENT ON COLUMN reference_data.security.is_st IS '是否 ST/*ST/S*ST。ST 股主板涨跌停为 5% 而非 10%，由简称前缀判定';
COMMENT ON COLUMN reference_data.security.list_date IS '上市日期';
COMMENT ON COLUMN reference_data.security.delist_date IS '退市日期，仍在市为空';
COMMENT ON COLUMN reference_data.security.total_share IS '总股本（股）。旧库该列 9,064 行全为 0，故一律留空不填 0，避免下游算出无穷大换手率';
COMMENT ON COLUMN reference_data.security.float_share IS '流通股本（股）。换手率的分母，仅个股填充；旧库按六位代码抓数不分交易所，非个股的值不可信已清空';
COMMENT ON COLUMN reference_data.security.is_active IS '是否仍在有效清单内，退市或代码回收后置假';
COMMENT ON COLUMN reference_data.security.updated_at IS '本行最后更新时间';

COMMENT ON TABLE reference_data.trade_calendar IS '交易日历：自然日一行，标注是否开市并预存前后交易日，省掉窗口函数';
COMMENT ON COLUMN reference_data.trade_calendar.calendar_date IS '自然日';
COMMENT ON COLUMN reference_data.trade_calendar.is_open IS '是否交易日';
COMMENT ON COLUMN reference_data.trade_calendar.prev_open_date IS '上一个交易日，用于回算 pre_close';
COMMENT ON COLUMN reference_data.trade_calendar.next_open_date IS '下一个交易日';

COMMENT ON TABLE reference_data.sector_info IS '板块字典：通达信行业/概念/地域板块';
COMMENT ON COLUMN reference_data.sector_info.sector_code IS '板块代码，880xxx 行业 / 881xxx 概念';
COMMENT ON COLUMN reference_data.sector_info.name IS '板块名称';
COMMENT ON COLUMN reference_data.sector_info.sector_type IS '板块类型：industry 行业 / concept 概念 / region 地域';
COMMENT ON COLUMN reference_data.sector_info.level IS '层级，1 为一级板块';
COMMENT ON COLUMN reference_data.sector_info.parent_code IS '上级板块代码';
COMMENT ON COLUMN reference_data.sector_info.is_active IS '板块是否仍在使用';

COMMENT ON TABLE reference_data.sector_member IS '板块成员：板块与个股的多对多关系，带进出日期便于回溯历史成分';
COMMENT ON COLUMN reference_data.sector_member.sector_code IS '板块代码';
COMMENT ON COLUMN reference_data.sector_member.security_code IS '成员证券代码';
COMMENT ON COLUMN reference_data.sector_member.in_date IS '纳入日期';
COMMENT ON COLUMN reference_data.sector_member.out_date IS '剔除日期，仍在板块内为空';

COMMENT ON TABLE reference_data.dividend IS '分红送配：复权因子的唯一依据。旧系统 adjustment_factor 硬编码 1.0 等于未复权，跨除权日指标全错';
COMMENT ON COLUMN reference_data.dividend.security_code IS '证券代码';
COMMENT ON COLUMN reference_data.dividend.ex_date IS '除权除息日';
COMMENT ON COLUMN reference_data.dividend.cash_per_share IS '每股现金分红（元，税前）';
COMMENT ON COLUMN reference_data.dividend.bonus_ratio IS '每股送股比例，10 送 3 记 0.3';
COMMENT ON COLUMN reference_data.dividend.rights_ratio IS '每股配股比例，10 配 2 记 0.2';
COMMENT ON COLUMN reference_data.dividend.rights_price IS '配股价（元）';

COMMENT ON TABLE reference_data.share_capital IS '股本变动历史：算历史市值和历史换手率要用当期股本，不能拿最新股本套全历史';
COMMENT ON COLUMN reference_data.share_capital.security_code IS '证券代码';
COMMENT ON COLUMN reference_data.share_capital.change_date IS '变动生效日';
COMMENT ON COLUMN reference_data.share_capital.total_share IS '变动后总股本（股）';
COMMENT ON COLUMN reference_data.share_capital.float_share IS '变动后流通股本（股）';

COMMENT ON VIEW reference_data.view_latest_trade_date IS '最新交易日：页面默认日期取这里，避免各处各算一遍';
COMMENT ON COLUMN reference_data.view_latest_trade_date.latest IS 'market_data.stock_daily 中的最大交易日';

-- ----------------------------------------------------------------- md
COMMENT ON TABLE market_data.stock_daily IS '个股日线，按交易年份 RANGE 分区。只放 A 股：债/基金/B股/伪代码计量口径不兼容，一律不进本表';
COMMENT ON COLUMN market_data.stock_daily.security_code IS '证券代码';
COMMENT ON COLUMN market_data.stock_daily.trade_date IS '交易日，同时是分区键';
COMMENT ON COLUMN market_data.stock_daily.open IS '开盘价（元，未复权）';
COMMENT ON COLUMN market_data.stock_daily.high IS '最高价（元，未复权）';
COMMENT ON COLUMN market_data.stock_daily.low IS '最低价（元，未复权）';
COMMENT ON COLUMN market_data.stock_daily.close IS '收盘价（元，未复权）';
COMMENT ON COLUMN market_data.stock_daily.pre_close IS '前收盘价（元），按交易日历的上一交易日收盘回算；每只票在库内首日为空';
COMMENT ON COLUMN market_data.stock_daily.change_yuan IS '涨跌额（元）= close - pre_close';
COMMENT ON COLUMN market_data.stock_daily.pct_change IS '涨跌幅（%）= change_yuan / pre_close × 100';
COMMENT ON COLUMN market_data.stock_daily.volume_shares IS '成交量（股）。旧库该字段单位是「手」，迁移时已 ×100；通达信 .day 原生即为股';
COMMENT ON COLUMN market_data.stock_daily.amount_yuan IS '成交额（元）。与 volume_shares 的比值必须落在当日价格区间内，由 market_data.trg_daily_sanity() 逐行强制';
COMMENT ON COLUMN market_data.stock_daily.turnover_rate_percent IS '换手率（%）= volume_shares / reference_data.security.float_share × 100。缺流通股本的留空而不填 0';
COMMENT ON COLUMN market_data.stock_daily.limit_up IS '涨停价（元）= pre_close × (1 + 当日档位)，四舍五入到分。档位按**那一天**判：通达信当日反推 > 当日行情可证 > ST 档期 > 规则；出处写在 limit_basis。唯一写入口 app/compute/limit_prices.py。旧系统一刀切 1.1 是错的，2026-10-07 之前用 board_limit_ratio(board, is_st) 那套"当前快照套历史"也是错的';
COMMENT ON COLUMN market_data.stock_daily.limit_down IS '跌停价（元），同 limit_up 用同一天的档位反向计算';
-- limit_basis（出处）的注释住在建它的那份 DDL 里：`app/db/sql/26_limit_price_daily.sql`
COMMENT ON COLUMN market_data.stock_daily.adjustment_factor IS '复权因子，由 reference_data.dividend 自最新交易日向前累乘。缺分红数据时留空，不填 1.0 假装已复权';
COMMENT ON COLUMN market_data.stock_daily.source_batch_id IS '写入批次号，外键 operations.ingest_batch。NOT NULL 是硬要求，任何一行都能回答「谁、何时、从哪写入」';
COMMENT ON COLUMN market_data.stock_daily.updated_at IS '本行最后更新时间';

COMMENT ON TABLE market_data.index_daily IS '指数日线。指数无股本、无涨跌停，故不设换手率与涨跌停列';
COMMENT ON COLUMN market_data.index_daily.security_code IS '指数代码，如 000001.SH 上证指数';
COMMENT ON COLUMN market_data.index_daily.trade_date IS '交易日';
COMMENT ON COLUMN market_data.index_daily.open IS '开盘点位';
COMMENT ON COLUMN market_data.index_daily.high IS '最高点位';
COMMENT ON COLUMN market_data.index_daily.low IS '最低点位';
COMMENT ON COLUMN market_data.index_daily.close IS '收盘点位';
COMMENT ON COLUMN market_data.index_daily.pre_close IS '前收盘点位';
COMMENT ON COLUMN market_data.index_daily.pct_change IS '涨跌幅（%）';
COMMENT ON COLUMN market_data.index_daily.volume_shares IS '成分股合计成交量（股）';
COMMENT ON COLUMN market_data.index_daily.amount_yuan IS '成分股合计成交额（元）';
COMMENT ON COLUMN market_data.index_daily.source_batch_id IS '写入批次号';
COMMENT ON COLUMN market_data.index_daily.updated_at IS '本行最后更新时间';

COMMENT ON TABLE market_data.sector_daily IS '板块日线：通达信 880/881 板块的行情与涨跌家数，是「板块轮动」和市场情绪的数据底座';
COMMENT ON COLUMN market_data.sector_daily.sector_code IS '板块代码';
COMMENT ON COLUMN market_data.sector_daily.trade_date IS '交易日';
COMMENT ON COLUMN market_data.sector_daily.open IS '板块开盘点位';
COMMENT ON COLUMN market_data.sector_daily.high IS '板块最高点位';
COMMENT ON COLUMN market_data.sector_daily.low IS '板块最低点位';
COMMENT ON COLUMN market_data.sector_daily.close IS '板块收盘点位';
COMMENT ON COLUMN market_data.sector_daily.pre_close IS '板块前收盘点位';
COMMENT ON COLUMN market_data.sector_daily.pct_change IS '板块涨跌幅（%）';
COMMENT ON COLUMN market_data.sector_daily.volume_shares IS '板块成交量（股）';
COMMENT ON COLUMN market_data.sector_daily.amount_yuan IS '板块成交额（元）';
COMMENT ON COLUMN market_data.sector_daily.up_count IS '板块内上涨家数';
COMMENT ON COLUMN market_data.sector_daily.down_count IS '板块内下跌家数';
COMMENT ON COLUMN market_data.sector_daily.member_count IS '板块内成员总数';
COMMENT ON COLUMN market_data.sector_daily.source_batch_id IS '写入批次号';
COMMENT ON COLUMN market_data.sector_daily.updated_at IS '本行最后更新时间';

COMMENT ON TABLE market_data.fund_daily IS 'ETF/LOF/分级基金日线。单独一张表而不塞进个股表：计量单位是「份」不是「股」，最小交易单位各品种也不同。数据从通达信 .day 重解析，不迁旧库（旧库这批 amount/vol 落在价格的 5 倍或 15 倍上，单位本身是错的）';
COMMENT ON COLUMN market_data.fund_daily.security_code IS '基金代码';
COMMENT ON COLUMN market_data.fund_daily.trade_date IS '交易日';
COMMENT ON COLUMN market_data.fund_daily.open IS '开盘价（元）';
COMMENT ON COLUMN market_data.fund_daily.high IS '最高价（元）';
COMMENT ON COLUMN market_data.fund_daily.low IS '最低价（元）';
COMMENT ON COLUMN market_data.fund_daily.close IS '收盘价（元）';
COMMENT ON COLUMN market_data.fund_daily.pre_close IS '前收盘价（元）';
COMMENT ON COLUMN market_data.fund_daily.pct_change IS '涨跌幅（%）';
COMMENT ON COLUMN market_data.fund_daily.volume_units IS '成交量（份）。命名上刻意区别于个股表的 volume_shares，防止以后混用';
COMMENT ON COLUMN market_data.fund_daily.amount_yuan IS '成交额（元）';
COMMENT ON COLUMN market_data.fund_daily.source_batch_id IS '写入批次号';
COMMENT ON COLUMN market_data.fund_daily.updated_at IS '本行最后更新时间';

COMMENT ON TABLE market_data.stock_minute IS '个股分钟线。源头是通达信 vipdoc 的 .lc1/.lc5，不从旧库搬（旧 37 GB 分钟线库已页面损坏）';
COMMENT ON COLUMN market_data.stock_minute.security_code IS '证券代码';
COMMENT ON COLUMN market_data.stock_minute.dt IS 'K 线时间戳（带时区），用完整时间而不用通达信的打包日序';
COMMENT ON COLUMN market_data.stock_minute.period IS '周期（分钟）：1 或 5';
COMMENT ON COLUMN market_data.stock_minute.open IS '开盘价（元）';
COMMENT ON COLUMN market_data.stock_minute.high IS '最高价（元）';
COMMENT ON COLUMN market_data.stock_minute.low IS '最低价（元）';
COMMENT ON COLUMN market_data.stock_minute.close IS '收盘价（元）';
COMMENT ON COLUMN market_data.stock_minute.volume_shares IS '成交量（股）';
COMMENT ON COLUMN market_data.stock_minute.amount_yuan IS '成交额（元）';
COMMENT ON COLUMN market_data.stock_minute.source_batch_id IS '写入批次号';

COMMENT ON TABLE market_data.stock_snapshot IS '个股每日估值快照：市值、估值、每股指标。与日线分表是因为来源和更新频率都不同';
COMMENT ON COLUMN market_data.stock_snapshot.security_code IS '证券代码';
COMMENT ON COLUMN market_data.stock_snapshot.trade_date IS '交易日';
COMMENT ON COLUMN market_data.stock_snapshot.pe_ttm IS '市盈率（滚动近四季），亏损时为空而非负数占位';
COMMENT ON COLUMN market_data.stock_snapshot.pb IS '市净率';
COMMENT ON COLUMN market_data.stock_snapshot.total_market_value_yuan IS '总市值（元）= close × 当期总股本';
COMMENT ON COLUMN market_data.stock_snapshot.float_market_value_yuan IS '流通市值（元）= close × 当期流通股本';
COMMENT ON COLUMN market_data.stock_snapshot.eps IS '每股收益（元）';
COMMENT ON COLUMN market_data.stock_snapshot.bps IS '每股净资产（元）';
COMMENT ON COLUMN market_data.stock_snapshot.roe_percent IS '净资产收益率（%）';
COMMENT ON COLUMN market_data.stock_snapshot.extra IS '其他指标，键值对形式，新增指标不必改表结构';
COMMENT ON COLUMN market_data.stock_snapshot.source_batch_id IS '写入批次号';

-- ---------------------------------------------------------------- config
COMMENT ON TABLE config.indicator IS '指标注册表：指标公式存成表里的一行，页面上改公式不用改代码。「积木城堡」的第一块积木';
COMMENT ON COLUMN config.indicator.indicator_key IS '指标键，代码和配置里引用它';
COMMENT ON COLUMN config.indicator.name IS '指标中文名';
COMMENT ON COLUMN config.indicator.kind IS '类型：price 价格类 / volume 量能类 / breadth 广度类 / derived 派生';
COMMENT ON COLUMN config.indicator.expr IS '计算表达式，由受限求值器解释，不是 eval：只放开白名单里的函数和字段';
COMMENT ON COLUMN config.indicator.params IS '默认参数，如均线周期';
COMMENT ON COLUMN config.indicator.depends_on IS '依赖的其他指标键，用于排计算顺序';
COMMENT ON COLUMN config.indicator.output_unit IS '输出单位：元 / % / 股 / 倍 等';
COMMENT ON COLUMN config.indicator.decimals IS '展示保留小数位';
COMMENT ON COLUMN config.indicator.builtin IS '是否内置。内置项不允许在页面上删除，避免把系统拆坏';
COMMENT ON COLUMN config.indicator.enabled IS '是否启用';
COMMENT ON COLUMN config.indicator.note IS '备注';
COMMENT ON COLUMN config.indicator.updated_at IS '最后修改时间';

COMMENT ON TABLE config.capability IS '功能点注册表：系统「能算什么」的清单，如个股质量、环境共振、操作评分。与具体实现脚本解耦';
COMMENT ON COLUMN config.capability.capability_key IS '功能点键';
COMMENT ON COLUMN config.capability.name IS '功能点中文名';
COMMENT ON COLUMN config.capability.category IS '分类';
COMMENT ON COLUMN config.capability.output_kind IS '输出形态：score 评分 / list 名单 / table 表格 / signal 信号';
COMMENT ON COLUMN config.capability.output_spec IS '输出结构约定，供前端与校验使用';
COMMENT ON COLUMN config.capability.depends_on IS '依赖的其他功能点';
COMMENT ON COLUMN config.capability.requires IS '前置条件，如所需数据集与交易日就绪状态';
COMMENT ON COLUMN config.capability.enabled IS '是否启用';
COMMENT ON COLUMN config.capability.description IS '说明';
COMMENT ON COLUMN config.capability.updated_at IS '最后修改时间';

COMMENT ON TABLE config.script IS '脚本注册表：一个功能点可以由多个脚本实现，脚本在这里登记路径、入口与校验和';
COMMENT ON COLUMN config.script.script_id IS '脚本自增主键';
COMMENT ON COLUMN config.script.script_key IS '脚本键';
COMMENT ON COLUMN config.script.name IS '脚本中文名';
COMMENT ON COLUMN config.script.path IS '脚本文件路径';
COMMENT ON COLUMN config.script.entry IS '入口函数名';
COMMENT ON COLUMN config.script.version IS '版本号，评分明细会记下算它的版本，便于解释历史结果';
COMMENT ON COLUMN config.script.param_schema IS '参数结构定义，页面据此自动生成表单';
COMMENT ON COLUMN config.script.provides IS '本脚本能产出的功能点键';
COMMENT ON COLUMN config.script.checksum IS '文件 sha256。变了说明脚本被改过，历史结果的可复现性随之改变';
COMMENT ON COLUMN config.script.builtin IS '是否内置';
COMMENT ON COLUMN config.script.enabled IS '是否启用';
COMMENT ON COLUMN config.script.registered_at IS '登记时间';
COMMENT ON COLUMN config.script.last_ok_at IS '最近一次成功执行时间';
COMMENT ON COLUMN config.script.last_error IS '最近一次错误信息';

COMMENT ON TABLE config.binding IS '功能点与脚本的绑定及权重：改权重是改表里的数，不是改代码。同一功能点多脚本时按权重加权';
COMMENT ON COLUMN config.binding.binding_id IS '绑定自增主键';
COMMENT ON COLUMN config.binding.capability_key IS '功能点键';
COMMENT ON COLUMN config.binding.script_id IS '脚本主键';
COMMENT ON COLUMN config.binding.weight IS '原始权重。不要求各行加总为 1，config.view_effective_binding 会在启用行内归一化';
COMMENT ON COLUMN config.binding.display_order IS '执行顺序';
COMMENT ON COLUMN config.binding.params IS '本次绑定覆盖的脚本参数';
COMMENT ON COLUMN config.binding.enabled IS '是否启用。停用行不参与归一化';
COMMENT ON COLUMN config.binding.note IS '备注';
COMMENT ON COLUMN config.binding.updated_at IS '最后修改时间';

COMMENT ON VIEW config.view_effective_binding IS '生效绑定：在启用行内把权重归一化后的结果。页面和计算都读这个视图，避免各处各归一化一遍';
COMMENT ON COLUMN config.view_effective_binding.capability_key IS '功能点键';
COMMENT ON COLUMN config.view_effective_binding.script_key IS '脚本键';
COMMENT ON COLUMN config.view_effective_binding.script_name IS '脚本中文名';
COMMENT ON COLUMN config.view_effective_binding.path IS '脚本路径';
COMMENT ON COLUMN config.view_effective_binding.version IS '脚本版本';
COMMENT ON COLUMN config.view_effective_binding.checksum IS '脚本校验和';
COMMENT ON COLUMN config.view_effective_binding.binding_id IS '绑定主键';
COMMENT ON COLUMN config.view_effective_binding.script_id IS '脚本主键';
COMMENT ON COLUMN config.view_effective_binding.display_order IS '执行顺序';
COMMENT ON COLUMN config.view_effective_binding.params IS '生效参数';
COMMENT ON COLUMN config.view_effective_binding.raw_weight IS '配置里的原始权重';
COMMENT ON COLUMN config.view_effective_binding.eff_weight IS '归一化后的生效权重，同一功能点下加总为 1';

COMMENT ON TABLE config.job IS '任务注册表：定时任务、依赖关系、并发键都在这里配，加一个定时任务是插一行';
COMMENT ON COLUMN config.job.job_id IS '任务自增主键';
COMMENT ON COLUMN config.job.job_key IS '任务键';
COMMENT ON COLUMN config.job.name IS '任务中文名';
COMMENT ON COLUMN config.job.kind IS '任务类型：ingest 摄取 / compute 计算 / quality 质检 / maintain 运维';
COMMENT ON COLUMN config.job.caps IS '本任务要执行的功能点键';
COMMENT ON COLUMN config.job.trigger_kind IS '触发方式：cron 定时 / ready 数据就绪 / after 前置任务完成 / manual 手动';
COMMENT ON COLUMN config.job.cron_expr IS 'cron 表达式，trigger_kind 为 cron 时使用';
COMMENT ON COLUMN config.job.ready_on IS '依赖的数据集键，这些数据集就绪才触发';
COMMENT ON COLUMN config.job.after_job IS '前置任务键';
COMMENT ON COLUMN config.job.scope IS '作用范围，如指定代码集合或板块';
COMMENT ON COLUMN config.job.params IS '任务参数';
COMMENT ON COLUMN config.job.only_trade_day IS '是否仅交易日执行';
COMMENT ON COLUMN config.job.max_retry IS '最大重试次数';
COMMENT ON COLUMN config.job.timeout_sec IS '超时秒数';
COMMENT ON COLUMN config.job.concurrency_key IS '并发键：同键任务串行执行，防止两个批次同时写一张表';
COMMENT ON COLUMN config.job.enabled IS '是否启用';
COMMENT ON COLUMN config.job.updated_at IS '最后修改时间';

COMMENT ON TABLE config.dataset IS '数据集注册表：定义「什么算这一天的数据齐了」，是任务编排的闸门';
COMMENT ON COLUMN config.dataset.dataset_key IS '数据集键';
COMMENT ON COLUMN config.dataset.name IS '数据集中文名';
COMMENT ON COLUMN config.dataset.grain IS '数据粒度：daily 日 / minute 分钟 / snapshot 快照 / static 静态';
COMMENT ON COLUMN config.dataset.source_keys IS '来源数据源键';
COMMENT ON COLUMN config.dataset.ready_rule IS '就绪判定规则，如最小行数、必须覆盖的代码数';
COMMENT ON COLUMN config.dataset.retention IS '保留策略';
COMMENT ON COLUMN config.dataset.enabled IS '是否启用';
COMMENT ON COLUMN config.dataset.updated_at IS '最后修改时间';

COMMENT ON TABLE config.data_source IS '数据源注册表：通达信目录、旧 SQLite、外部接口都在这里登记，换源是改配置不是改代码';
COMMENT ON COLUMN config.data_source.source_key IS '数据源键';
COMMENT ON COLUMN config.data_source.name IS '数据源中文名';
COMMENT ON COLUMN config.data_source.kind IS '类型：tdx_file 通达信文件 / sqlite 旧库 / http 接口 / manual 人工录入';
COMMENT ON COLUMN config.data_source.locator IS '定位串：目录、文件路径或 URL';
COMMENT ON COLUMN config.data_source.parser_key IS '解析器键，对应 app/ingest 下的实现';
COMMENT ON COLUMN config.data_source.options IS '解析选项';
COMMENT ON COLUMN config.data_source.health_check IS '健康检查配置，如文件最小尺寸、最大陈旧时长';
COMMENT ON COLUMN config.data_source.priority IS '优先级，多源同字段时数字小的优先';
COMMENT ON COLUMN config.data_source.enabled IS '是否启用';
COMMENT ON COLUMN config.data_source.note IS '备注';
COMMENT ON COLUMN config.data_source.updated_at IS '最后修改时间';

COMMENT ON TABLE config.field_spec IS '字段字典：字段的中文名、单位、合理区间、计算公式。质检规则和页面表头都读它，口径只有一个出处';
COMMENT ON COLUMN config.field_spec.id IS '自增主键';
COMMENT ON COLUMN config.field_spec.table_name IS '所属表，含 schema';
COMMENT ON COLUMN config.field_spec.column_name IS '字段名';
COMMENT ON COLUMN config.field_spec.label_cn IS '中文名，页面表头直接用';
COMMENT ON COLUMN config.field_spec.unit IS '单位：元 / 股 / 份 / % / 手';
COMMENT ON COLUMN config.field_spec.dtype IS '逻辑类型';
COMMENT ON COLUMN config.field_spec.value_range IS '物理取值范围，越界即数据错误';
COMMENT ON COLUMN config.field_spec.sane_range IS '业务合理范围，越界是可疑但不一定错，交给质检提示';
COMMENT ON COLUMN config.field_spec.formula IS '计算口径说明';
COMMENT ON COLUMN config.field_spec.source_key IS '数据来源键';
COMMENT ON COLUMN config.field_spec.display IS '展示配置：小数位、千分位、颜色规则';
COMMENT ON COLUMN config.field_spec.updated_at IS '最后修改时间';

COMMENT ON TABLE config.quality_rule IS '质检规则注册表：加一条校验是插一行 SQL，不是改代码。blocking 为真的规则不通过就阻断下游加工';
COMMENT ON COLUMN config.quality_rule.rule_key IS '规则键';
COMMENT ON COLUMN config.quality_rule.name IS '规则中文名';
COMMENT ON COLUMN config.quality_rule.scope IS '适用范围：daily 每日 / batch 每批 / static 静态数据';
COMMENT ON COLUMN config.quality_rule.severity IS '级别：info 提示 / warn 警告 / error 错误';
COMMENT ON COLUMN config.quality_rule.target IS '检查对象，一般是表名';
COMMENT ON COLUMN config.quality_rule.check_kind IS '检查方式：sql 查询 / rowcount 行数 / range 区间 / custom 自定义';
COMMENT ON COLUMN config.quality_rule.check_body IS '检查主体，SQL 或表达式';
COMMENT ON COLUMN config.quality_rule.threshold IS '阈值配置';
COMMENT ON COLUMN config.quality_rule.blocking IS '是否阻断：为真且未通过时不写 operations.data_ready，下游任务不触发';
COMMENT ON COLUMN config.quality_rule.enabled IS '是否启用';
COMMENT ON COLUMN config.quality_rule.description IS '说明';
COMMENT ON COLUMN config.quality_rule.updated_at IS '最后修改时间';

COMMENT ON TABLE config.chart IS '图表注册表：图表类型、取数查询名、编码映射都在表里，页面上换图形不用改代码';
COMMENT ON COLUMN config.chart.chart_key IS '图表键';
COMMENT ON COLUMN config.chart.name IS '图表中文名';
COMMENT ON COLUMN config.chart.chart_type IS '图形：line 折线 / bar 柱状 / candlestick K线 / hist 直方 / scatter 散点';
COMMENT ON COLUMN config.chart.data_query IS '取数查询的**名字**，不是 SQL 原文。存名字是刻意的：否则页面就成了远程执行任意 SQL 的入口';
COMMENT ON COLUMN config.chart.encoding IS '字段到视觉通道的映射，如 x/y/color 各取哪一列';
COMMENT ON COLUMN config.chart.options IS '图形选项：标题、图例、坐标轴';
COMMENT ON COLUMN config.chart.params IS '查询参数默认值';
COMMENT ON COLUMN config.chart.enabled IS '是否启用';
COMMENT ON COLUMN config.chart.updated_at IS '最后修改时间';

COMMENT ON TABLE config.page_layout IS '页面布局：某个页面第几格放什么卡片、多宽多高。调页面是拖表里的行，不是改前端代码';
COMMENT ON COLUMN config.page_layout.layout_id IS '布局自增主键';
COMMENT ON COLUMN config.page_layout.page_key IS '页面键，如 home';
COMMENT ON COLUMN config.page_layout.slot IS '槽位序号，决定先后';
COMMENT ON COLUMN config.page_layout.width IS '宽度（12 栅格制）';
COMMENT ON COLUMN config.page_layout.height IS '高度（像素）。只对图表/文本类卡片生效；表格卡不看这一列 —— 表格高度由 options.page_size 决定（一页几行就画多高），要限高在 options 里写 max_height';
COMMENT ON COLUMN config.page_layout.card_kind IS '卡片类型：chart 图表 / stat 指标数 / table 表格 / text 文本';
COMMENT ON COLUMN config.page_layout.card_ref IS '卡片引用的对象键，如 chart_key';
COMMENT ON COLUMN config.page_layout.title IS '卡片标题，留空则用被引用对象的名称';
COMMENT ON COLUMN config.page_layout.options IS '卡片级选项';
COMMENT ON COLUMN config.page_layout.visible IS '是否显示。渲染失败的卡片会显示错误提示而不是整页白屏';

COMMENT ON TABLE config.menu IS '导航菜单：菜单项、路由、分组、排序、可见角色。改菜单是改表，加页面才要改代码';
COMMENT ON COLUMN config.menu.menu_key IS '菜单键';
COMMENT ON COLUMN config.menu.label IS '菜单显示名';
COMMENT ON COLUMN config.menu.route IS '路由路径，如 /market';
COMMENT ON COLUMN config.menu.icon IS '图标名';
COMMENT ON COLUMN config.menu.display_order IS '同组内排序';
COMMENT ON COLUMN config.menu.group_name IS '分组名，侧边栏据此分块';
COMMENT ON COLUMN config.menu.need_role IS '可见所需角色，留空为所有人可见';
COMMENT ON COLUMN config.menu.visible IS '是否显示';

COMMENT ON TABLE config.screen_rule IS '选股条件注册表：一个条件一行 SQL 模板，页面上勾选组合，不用为每种筛法写代码';
COMMENT ON COLUMN config.screen_rule.rule_key IS '条件键';
COMMENT ON COLUMN config.screen_rule.name IS '条件中文名';
COMMENT ON COLUMN config.screen_rule.param_schema IS '参数结构定义，页面据此生成输入控件';
COMMENT ON COLUMN config.screen_rule.sql_template IS 'SQL 片段模板，参数以占位符传入，不做字符串拼接';
COMMENT ON COLUMN config.screen_rule.enabled IS '是否启用';
COMMENT ON COLUMN config.screen_rule.builtin IS '是否内置';
COMMENT ON COLUMN config.screen_rule.note IS '备注';

COMMENT ON TABLE config.screen_preset IS '选股方案：用户把一组条件存成方案，可共享给其他人';
COMMENT ON COLUMN config.screen_preset.preset_id IS '方案自增主键';
COMMENT ON COLUMN config.screen_preset.name IS '方案名';
COMMENT ON COLUMN config.screen_preset.owner IS '创建人';
COMMENT ON COLUMN config.screen_preset.conditions IS '条件组合及参数';
COMMENT ON COLUMN config.screen_preset.is_shared IS '是否共享给其他用户';
COMMENT ON COLUMN config.screen_preset.created_at IS '创建时间';

COMMENT ON TABLE config.watchlist_group IS '自选股分组';
COMMENT ON COLUMN config.watchlist_group.group_id IS '分组自增主键';
COMMENT ON COLUMN config.watchlist_group.name IS '分组名';
COMMENT ON COLUMN config.watchlist_group.owner IS '归属用户';
COMMENT ON COLUMN config.watchlist_group.is_focus IS '是否为重点关注组';
COMMENT ON COLUMN config.watchlist_group.display_order IS '排序';
COMMENT ON COLUMN config.watchlist_group.note IS '备注';
COMMENT ON COLUMN config.watchlist_group.created_at IS '创建时间';

COMMENT ON TABLE config.watchlist_stock IS '自选股成员';
COMMENT ON COLUMN config.watchlist_stock.group_id IS '所属分组';
COMMENT ON COLUMN config.watchlist_stock.security_code IS '证券代码';
COMMENT ON COLUMN config.watchlist_stock.added_at IS '加入时间';
COMMENT ON COLUMN config.watchlist_stock.cost IS '成本价（元），用于算持仓浮盈';
COMMENT ON COLUMN config.watchlist_stock.note IS '备注';

COMMENT ON TABLE config.setting IS '系统设置：键值对形式，避免为每个开关加一列。旧系统的评分方案也归档在这里';
COMMENT ON COLUMN config.setting.key IS '设置键';
COMMENT ON COLUMN config.setting.value IS '设置值';
COMMENT ON COLUMN config.setting.label IS '中文说明';
COMMENT ON COLUMN config.setting.category IS '分类，页面按此分组展示';
COMMENT ON COLUMN config.setting.updated_at IS '最后修改时间';

COMMENT ON TABLE config.notify_channel IS '通知渠道：邮件、企业微信、Webhook 等。密钥只存引用名，不存明文';
COMMENT ON COLUMN config.notify_channel.channel_key IS '渠道键';
COMMENT ON COLUMN config.notify_channel.name IS '渠道中文名';
COMMENT ON COLUMN config.notify_channel.kind IS '渠道类型';
COMMENT ON COLUMN config.notify_channel.endpoint IS '发送地址';
COMMENT ON COLUMN config.notify_channel.secret_ref IS '密钥的**引用名**，真实密钥放环境变量，不入库';
COMMENT ON COLUMN config.notify_channel.template IS '消息模板';
COMMENT ON COLUMN config.notify_channel.enabled IS '是否启用';
COMMENT ON COLUMN config.notify_channel.updated_at IS '最后修改时间';

COMMENT ON TABLE config.notify_rule IS '通知规则：什么事件、满足什么条件、发到哪个渠道';
COMMENT ON COLUMN config.notify_rule.rule_key IS '规则键';
COMMENT ON COLUMN config.notify_rule.name IS '规则中文名';
COMMENT ON COLUMN config.notify_rule.trigger IS '触发事件：如任务失败、质检不通过、指标越界';
COMMENT ON COLUMN config.notify_rule.condition IS '附加条件';
COMMENT ON COLUMN config.notify_rule.channel_key IS '发送渠道键';
COMMENT ON COLUMN config.notify_rule.payload_query IS '消息内容取数查询名';
COMMENT ON COLUMN config.notify_rule.enabled IS '是否启用';
COMMENT ON COLUMN config.notify_rule.updated_at IS '最后修改时间';

COMMENT ON TABLE config.app_user IS '系统用户。当前 Web 尚未接入登录，故服务默认只绑 127.0.0.1';
COMMENT ON COLUMN config.app_user.username IS '登录名';
COMMENT ON COLUMN config.app_user.password_hash IS '口令哈希（argon2），绝不存明文';
COMMENT ON COLUMN config.app_user.role IS '角色：admin 管理员 / analyst 分析员 / viewer 只读';
COMMENT ON COLUMN config.app_user.display_name IS '显示名';
COMMENT ON COLUMN config.app_user.enabled IS '是否启用';
COMMENT ON COLUMN config.app_user.created_at IS '创建时间';
COMMENT ON COLUMN config.app_user.last_login_at IS '最近登录时间';

-- ---------------------------------------------------------------- fct
COMMENT ON TABLE analytics.indicator_value IS '指标计算结果：一只票一天一个指标一行。窄表形式，加指标不用改表结构';
COMMENT ON COLUMN analytics.indicator_value.security_code IS '证券代码';
COMMENT ON COLUMN analytics.indicator_value.trade_date IS '交易日';
COMMENT ON COLUMN analytics.indicator_value.indicator_key IS '指标键';
COMMENT ON COLUMN analytics.indicator_value.value IS '指标值，单位见 config.indicator.output_unit';
COMMENT ON COLUMN analytics.indicator_value.task_id IS '产出它的任务号，外键 operations.task';

COMMENT ON TABLE analytics.score IS '功能点评分结果：一只票一天一个功能点一行，含排名与象限';
COMMENT ON COLUMN analytics.score.security_code IS '证券代码';
COMMENT ON COLUMN analytics.score.trade_date IS '评分日';
COMMENT ON COLUMN analytics.score.capability_key IS '功能点键';
COMMENT ON COLUMN analytics.score.value IS '得分';
COMMENT ON COLUMN analytics.score.rank_in_market IS '全市场排名';
COMMENT ON COLUMN analytics.score.percent_rank IS '百分位排名（0~1）';
COMMENT ON COLUMN analytics.score.quadrant IS '象限归属，如个股质量与环境共振交叉出的四象限';
COMMENT ON COLUMN analytics.score.task_id IS '产出它的任务号';
COMMENT ON COLUMN analytics.score.is_stale IS '是否为陈旧结果。从旧系统迁入的 227,985 行全部标真：算法已改，不能与新结果混着比';
COMMENT ON COLUMN analytics.score.calculated_at IS '计算时间';

COMMENT ON TABLE analytics.score_detail IS '评分明细：一个得分由哪些部分、各占多少权重、各贡献多少分构成。「为什么是这个分」要能答出来';
COMMENT ON COLUMN analytics.score_detail.security_code IS '证券代码';
COMMENT ON COLUMN analytics.score_detail.trade_date IS '评分日';
COMMENT ON COLUMN analytics.score_detail.capability_key IS '功能点键';
COMMENT ON COLUMN analytics.score_detail.component_key IS '分项键';
COMMENT ON COLUMN analytics.score_detail.component_label IS '分项中文名';
COMMENT ON COLUMN analytics.score_detail.component_value IS '分项原始值';
COMMENT ON COLUMN analytics.score_detail.weight IS '分项生效权重';
COMMENT ON COLUMN analytics.score_detail.contribution IS '分项对总分的贡献 = component_value × weight';
COMMENT ON COLUMN analytics.score_detail.script_key IS '算它的脚本键';
COMMENT ON COLUMN analytics.score_detail.script_version IS '算它的脚本版本，脚本改版后旧结果仍可解释';

COMMENT ON TABLE analytics.score_trend IS '个股操作分的窗口趋势：/scores「四象限趋势分」的落库预算，页面只读表；库里没有当天时页面自动回退现算';
COMMENT ON COLUMN analytics.score_trend.security_code IS '证券代码';
COMMENT ON COLUMN analytics.score_trend.trade_date IS '基准交易日（窗口的最后一天）';
COMMENT ON COLUMN analytics.score_trend.window_days IS '窗口长度，单位有评分的交易日，不是日历日';
COMMENT ON COLUMN analytics.score_trend.point_count IS '窗口内真有分的天数；不足不补齐，页面的 min_n 拿它过滤';
COMMENT ON COLUMN analytics.score_trend.slope IS '操作分的最小二乘斜率，单位分/天；页面上的「趋势分数」= slope × window_days';
COMMENT ON COLUMN analytics.score_trend.intercept IS '最小二乘截距，配 point_count 外推「预测操作分」';
COMMENT ON COLUMN analytics.score_trend.corr_coef IS 'corr(操作分, 天序)；存 r 不存 r² —— 符号是信息，开方就丢了。页面显示 r²';
COMMENT ON COLUMN analytics.score_trend.x_plus_y_max IS '窗口内 X+Y 的最高值';
COMMENT ON COLUMN analytics.score_trend.x_plus_y_max_date IS '最高值出现在哪天；出现在第 2 天还是今天，含义完全相反';
COMMENT ON COLUMN analytics.score_trend.up_streak_days IS '末尾连续上行的天数';
COMMENT ON COLUMN analytics.score_trend.first_quadrant IS '窗口第一天的象限，配当天象限就是「象限迁移」';
COMMENT ON COLUMN analytics.score_trend.sparkline IS '▁▂▃▄▅▆▇ 拼的迷你走势，只表示形状不表示绝对高低';
COMMENT ON COLUMN analytics.score_trend.task_id IS '算它的任务号';
COMMENT ON COLUMN analytics.score_trend.calculated_at IS '计算时间';

COMMENT ON TABLE analytics.market_breadth IS '全市场情绪读数，一天一行：涨跌停 / 触板炸板 / 连板梯队 / 晋级率 / 中位涨幅。「明日验证表」的事实来源';
COMMENT ON COLUMN analytics.market_breadth.trade_date IS '交易日（主键）';
COMMENT ON COLUMN analytics.market_breadth.stocks IS '当天参与统计的票数；pct_change 为空的行（新股首日 / pre_close 缺失）已排除，所以小于当天日线行数';
COMMENT ON COLUMN analytics.market_breadth.limit_up_count IS '涨停家数，判据 close >= limit_up - 0.01（limit_up 是涨停价不是布尔标记）；用涨幅阈值会把创业板 20%、北交所 30%、ST 5% 判错';
COMMENT ON COLUMN analytics.market_breadth.limit_down_count IS '跌停家数，判据 close <= limit_down + 0.01';
COMMENT ON COLUMN analytics.market_breadth.touched_count IS '盘中摸到涨停价的家数（含最终封住的），炸板率的分母';
COMMENT ON COLUMN analytics.market_breadth.broken_count IS '炸板家数：触板但收盘没封住。库里有涨停价，所以是精确值不是近似';
COMMENT ON COLUMN analytics.market_breadth.broken_rate IS '炸板率 %；没人触板时是 NULL 不是 0 —— 「都封住了」和「没人摸涨停」不是一回事';
COMMENT ON COLUMN analytics.market_breadth.highest_board IS '最高连板高度：往前 20 个交易日算末尾连续涨停天数';
COMMENT ON COLUMN analytics.market_breadth.board2_count IS '2 连板家数';
COMMENT ON COLUMN analytics.market_breadth.board3_count IS '3 连板家数';
COMMENT ON COLUMN analytics.market_breadth.board4plus_count IS '4 连板及以上家数；梯队断层比最高高度更能说明接力资金在不在';
COMMENT ON COLUMN analytics.market_breadth.prev_limit_up IS '上一个有数据交易日的涨停家数（不用 d-1 日，周末长假会把分母算成 0）';
COMMENT ON COLUMN analytics.market_breadth.promotion_rate IS '晋级率 %：昨涨停且今日继续涨停 / 昨涨停；低于 30% 通常是退潮';
COMMENT ON COLUMN analytics.market_breadth.red_rate IS '昨涨停红盘率 %：宽一档的判据，没封板但收红说明还有人接';
COMMENT ON COLUMN analytics.market_breadth.up_count IS '上涨家数（pct_change > 0）';
COMMENT ON COLUMN analytics.market_breadth.down_count IS '下跌家数（pct_change < 0）';
COMMENT ON COLUMN analytics.market_breadth.up_ratio IS '上涨家数占比 %，赚钱效应的面';
COMMENT ON COLUMN analytics.market_breadth.median_pct IS '中位涨幅 %；用中位数不用均值，几只妖股拉不动中位数';
COMMENT ON COLUMN analytics.market_breadth.task_id IS '算它的任务号';
COMMENT ON COLUMN analytics.market_breadth.calculated_at IS '计算时间';

COMMENT ON TABLE analytics.volume_double IS '倍量震荡：近 40 个交易日「频繁倍量 + 沿 60 日线震荡」的原始读数，全市场每票一行。现算 8.4 秒所以落库；入选阈值在 config.setting[''vol_double.*'']，页面上改立刻生效';
COMMENT ON COLUMN analytics.volume_double.trade_date IS '基准交易日（观察窗的最后一天）';
COMMENT ON COLUMN analytics.volume_double.security_code IS '证券代码';
COMMENT ON COLUMN analytics.volume_double.win_days IS '观察窗天数（默认 40 个交易日）';
COMMENT ON COLUMN analytics.volume_double.bars IS '窗口内真有行情的天数；停牌多的票会明显小于 win_days';
COMMENT ON COLUMN analytics.volume_double.ma60_days IS '窗口内算得出 MA60 的天数；不满 60 根 K 线的那天记 NULL（次新股不凑数）';
COMMENT ON COLUMN analytics.volume_double.dbl_count IS '倍量次数：成交量 ≥ 前 5 个交易日均量（不含当日）×2 的天数。榜单按这一列排序';
COMMENT ON COLUMN analytics.volume_double.dbl_prev_count IS '对昨日量的倍量次数，只作对照口径；昨天缩量会让这个数虚高，所以不用它排序';
COMMENT ON COLUMN analytics.volume_double.cross_count IS '收盘价与 MA60 上下关系翻转的次数；0 次是单边，不算震荡';
COMMENT ON COLUMN analytics.volume_double.dev_med IS '|收盘/MA60 − 1| 的中位数（%）；用中位数，中间一根涨停拉不动它';
COMMENT ON COLUMN analytics.volume_double.dev_max IS '|收盘/MA60 − 1| 的最大值（%）；中位小而最大值大＝中间拉过一次又回来';
COMMENT ON COLUMN analytics.volume_double.above_ratio IS '收在 MA60 上方的天数占比（%）；100 或 0 都不是震荡';
COMMENT ON COLUMN analytics.volume_double.ma60_chg IS '窗口首尾 MA60 的变化率（%）；60 线自己陡涨陡跌时「贴着线」是趋势不是震荡';
COMMENT ON COLUMN analytics.volume_double.avg_amt_yuan IS '窗口日均成交额（元），流动性门槛';
COMMENT ON COLUMN analytics.volume_double.close_last IS '基准日收盘价';
COMMENT ON COLUMN analytics.volume_double.ma60_last IS '基准日 MA60';
COMMENT ON COLUMN analytics.volume_double.task_id IS '算它的任务号';
COMMENT ON COLUMN analytics.volume_double.calculated_at IS '计算时间';



COMMENT ON VIEW analytics.view_score_detail IS '评分明细视图：把总分、名称、象限与各分项拼在一起，页面直接展示归因';
COMMENT ON COLUMN analytics.view_score_detail.security_code IS '证券代码';
COMMENT ON COLUMN analytics.view_score_detail.name IS '证券简称';
COMMENT ON COLUMN analytics.view_score_detail.trade_date IS '评分日';
COMMENT ON COLUMN analytics.view_score_detail.capability_key IS '功能点键';
COMMENT ON COLUMN analytics.view_score_detail.total_value IS '总分';
COMMENT ON COLUMN analytics.view_score_detail.quadrant IS '象限归属';
COMMENT ON COLUMN analytics.view_score_detail.is_stale IS '是否陈旧结果';
COMMENT ON COLUMN analytics.view_score_detail.component_key IS '分项键';
COMMENT ON COLUMN analytics.view_score_detail.component_label IS '分项中文名';
COMMENT ON COLUMN analytics.view_score_detail.component_value IS '分项原始值';
COMMENT ON COLUMN analytics.view_score_detail.weight IS '分项权重';
COMMENT ON COLUMN analytics.view_score_detail.contribution IS '分项贡献';
COMMENT ON COLUMN analytics.view_score_detail.script_key IS '脚本键';
COMMENT ON COLUMN analytics.view_score_detail.script_version IS '脚本版本';

-- ---------------------------------------------------------------- ops
COMMENT ON TABLE operations.ingest_batch IS '入库批次：每次写数据开一个批次，md.* 各表的 source_batch_id 指向它。血缘的根';
COMMENT ON COLUMN operations.ingest_batch.batch_id IS '批次自增主键';
COMMENT ON COLUMN operations.ingest_batch.source IS '来源标识，如 legacy_sqlite、tdx_vipdoc';
COMMENT ON COLUMN operations.ingest_batch.source_path IS '来源路径';
COMMENT ON COLUMN operations.ingest_batch.source_mtime IS '来源文件的修改时间，用于判断数据是否更新过';
COMMENT ON COLUMN operations.ingest_batch.target IS '写入目标表';
COMMENT ON COLUMN operations.ingest_batch.row_count IS '本批写入行数';
COMMENT ON COLUMN operations.ingest_batch.actor IS '执行者';
COMMENT ON COLUMN operations.ingest_batch.started_at IS '开始时间';
COMMENT ON COLUMN operations.ingest_batch.finished_at IS '结束时间';
COMMENT ON COLUMN operations.ingest_batch.status IS '状态：running 进行中 / ok 成功 / failed 失败。失败批次留下的行要清理，按 source_batch_id 分组即可发现';
COMMENT ON COLUMN operations.ingest_batch.message IS '失败原因或备注';

COMMENT ON TABLE operations.task IS '任务实例：调度器产生、执行器消费。带重试、取消、并发键和依赖';
COMMENT ON COLUMN operations.task.task_id IS '任务自增主键';
COMMENT ON COLUMN operations.task.job_key IS '来自哪个任务配置';
COMMENT ON COLUMN operations.task.capability_key IS '执行的功能点键';
COMMENT ON COLUMN operations.task.script_id IS '执行的脚本主键';
COMMENT ON COLUMN operations.task.trade_date IS '处理的交易日';
COMMENT ON COLUMN operations.task.scope IS '作用范围';
COMMENT ON COLUMN operations.task.params IS '运行参数';
COMMENT ON COLUMN operations.task.state IS '状态：pending 待跑 / running 运行中 / ok 成功 / failed 失败 / canceled 已取消';
COMMENT ON COLUMN operations.task.progress IS '进度百分比';
COMMENT ON COLUMN operations.task.message IS '进度或结果说明';
COMMENT ON COLUMN operations.task.attempt IS '当前第几次尝试';
COMMENT ON COLUMN operations.task.max_retry IS '最大重试次数';
COMMENT ON COLUMN operations.task.cancel_requested IS '是否已请求取消，执行器在检查点响应';
COMMENT ON COLUMN operations.task.concurrency_key IS '并发键，同键任务串行';
COMMENT ON COLUMN operations.task.depends_on IS '依赖的任务号';
COMMENT ON COLUMN operations.task.actor IS '触发者';
COMMENT ON COLUMN operations.task.created_at IS '创建时间';
COMMENT ON COLUMN operations.task.started_at IS '开始时间';
COMMENT ON COLUMN operations.task.finished_at IS '结束时间';
COMMENT ON COLUMN operations.task.result_meta IS '结果摘要，如影响行数、产出对象';
COMMENT ON COLUMN operations.task.error IS '错误信息';

COMMENT ON TABLE operations.data_ready IS '数据就绪标记：某数据集某交易日的数据齐了才写一行。下游任务只看这张表，不猜';
COMMENT ON COLUMN operations.data_ready.dataset_key IS '数据集键';
COMMENT ON COLUMN operations.data_ready.trade_date IS '交易日';
COMMENT ON COLUMN operations.data_ready.row_count IS '当日行数';
COMMENT ON COLUMN operations.data_ready.ready_at IS '判定就绪的时间';
COMMENT ON COLUMN operations.data_ready.batch_id IS '对应的入库批次';

COMMENT ON TABLE operations.quality_result IS '质检结果：每条规则每次执行的结论与证据。不通过的阻断级规则会拦住下游加工';
COMMENT ON COLUMN operations.quality_result.id IS '自增主键';
COMMENT ON COLUMN operations.quality_result.run_at IS '执行时间';
COMMENT ON COLUMN operations.quality_result.trade_date IS '被检查的交易日，与具体日期无关的检查为空';
COMMENT ON COLUMN operations.quality_result.rule_key IS '规则键';
COMMENT ON COLUMN operations.quality_result.target IS '检查对象';
COMMENT ON COLUMN operations.quality_result.severity IS '级别：info / warn / error';
COMMENT ON COLUMN operations.quality_result.passed IS '是否通过';
COMMENT ON COLUMN operations.quality_result.actual IS '实测值。写入前统一把 Decimal 与日期转成可序列化类型，否则留不下痕迹';
COMMENT ON COLUMN operations.quality_result.expected IS '期望值或判据说明';
COMMENT ON COLUMN operations.quality_result.sample IS '不通过的样本，便于直接定位到具体行';
COMMENT ON COLUMN operations.quality_result.batch_id IS '关联的入库批次';

COMMENT ON TABLE operations.audit IS '操作审计：谁在什么入口做了什么、影响多少行。由触发器写入，应用层绕不过去';
COMMENT ON COLUMN operations.audit.id IS '自增主键';
COMMENT ON COLUMN operations.audit.occurred_at IS '发生时间';
COMMENT ON COLUMN operations.audit.actor IS '操作者，默认取数据库当前用户';
COMMENT ON COLUMN operations.audit.channel IS '入口：web 页面 / script 脚本 / agent 智能体 / sql 手工';
COMMENT ON COLUMN operations.audit.action IS '动作';
COMMENT ON COLUMN operations.audit.target IS '操作对象';
COMMENT ON COLUMN operations.audit.params IS '参数';
COMMENT ON COLUMN operations.audit.rows_affected IS '影响行数';
COMMENT ON COLUMN operations.audit.ok IS '是否成功';
COMMENT ON COLUMN operations.audit.error IS '错误信息';

COMMENT ON TABLE operations.config_history IS '配置变更历史：config.* 各表的改动前后快照，由触发器写入。改坏了能一键回退';
COMMENT ON COLUMN operations.config_history.id IS '自增主键';
COMMENT ON COLUMN operations.config_history.occurred_at IS '变更时间';
COMMENT ON COLUMN operations.config_history.actor IS '变更人';
COMMENT ON COLUMN operations.config_history.table_name IS '被改的配置表';
COMMENT ON COLUMN operations.config_history.row_key IS '被改行的主键值';
COMMENT ON COLUMN operations.config_history.op IS '操作：INSERT / UPDATE / DELETE';
COMMENT ON COLUMN operations.config_history.before IS '改动前整行内容';
COMMENT ON COLUMN operations.config_history.after IS '改动后整行内容';

COMMENT ON TABLE operations.migration_baseline IS '迁移基线：迁移前逐表记下旧库行数，迁完拿它对账。没有基线就无法证明「一行都没丢」';
COMMENT ON COLUMN operations.migration_baseline.id IS '自增主键';
COMMENT ON COLUMN operations.migration_baseline.captured_at IS '采集时间';
COMMENT ON COLUMN operations.migration_baseline.source_db IS '旧库文件名';
COMMENT ON COLUMN operations.migration_baseline.source_table IS '旧表名';
COMMENT ON COLUMN operations.migration_baseline.row_count IS '旧表行数';
COMMENT ON COLUMN operations.migration_baseline.extra IS '附加信息，如日期区间';

COMMENT ON TABLE operations.schema_migration IS 'schema 版本记录：记下每个 SQL 文件的 sha256。不引入 Alembic，避免两套 schema 真相互相打架';
COMMENT ON COLUMN operations.schema_migration.filename IS 'SQL 文件名';
COMMENT ON COLUMN operations.schema_migration.checksum IS '文件内容 sha256，与磁盘上的文件比对即知库是否落后';
COMMENT ON COLUMN operations.schema_migration.applied_at IS '应用时间';
COMMENT ON COLUMN operations.schema_migration.applied_by IS '应用者';

COMMENT ON VIEW operations.view_coverage IS '数据覆盖度视图：各数据集各交易日的行数，用于发现某天数据缺失或异常稀疏';
COMMENT ON COLUMN operations.view_coverage.dataset_key IS '数据集键';
COMMENT ON COLUMN operations.view_coverage.trade_date IS '交易日';
COMMENT ON COLUMN operations.view_coverage.row_count IS '当日行数';

COMMENT ON VIEW operations.view_field_dict IS '字段字典视图：config.field_spec 的只读展示层，供页面和智能体查字段口径';
COMMENT ON COLUMN operations.view_field_dict.table_name IS '表名';
COMMENT ON COLUMN operations.view_field_dict.column_name IS '字段名';
COMMENT ON COLUMN operations.view_field_dict.label_cn IS '中文名';
COMMENT ON COLUMN operations.view_field_dict.unit IS '单位';
COMMENT ON COLUMN operations.view_field_dict.dtype IS '逻辑类型';
COMMENT ON COLUMN operations.view_field_dict.value_range IS '物理取值范围';
COMMENT ON COLUMN operations.view_field_dict.sane_range IS '业务合理范围';
COMMENT ON COLUMN operations.view_field_dict.formula IS '计算口径';
COMMENT ON COLUMN operations.view_field_dict.source_key IS '数据来源';
COMMENT ON COLUMN operations.view_field_dict.display IS '展示配置';

-- =====================================================================
-- 分区子表继承父表注释。PostgreSQL 不会自动传播 COMMENT，
-- 但在 pgAdmin 里点开 market_data.stock_daily_2024 时看不到注释一样会让人困惑。
-- =====================================================================
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT pn.nspname AS child_schema, pc.relname AS child,
               obj_description(parc.oid, 'pg_class') AS cmt
          FROM pg_inherits i
          JOIN pg_class pc       ON pc.oid = i.inhrelid
          JOIN pg_namespace pn   ON pn.oid = pc.relnamespace
          JOIN pg_class parc     ON parc.oid = i.inhparent
          JOIN pg_namespace parn ON parn.oid = parc.relnamespace
         WHERE pn.nspname IN ('market_data', 'analytics')
           AND obj_description(parc.oid, 'pg_class') IS NOT NULL
    LOOP
        EXECUTE format('COMMENT ON TABLE %I.%I IS %L',
                       r.child_schema, r.child, r.cmt || '（分区）');
    END LOOP;
END $$;

DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT pn.nspname AS child_schema, pc.relname AS child,
               a.attname, col_description(parc.oid, a.attnum) AS cmt
          FROM pg_inherits i
          JOIN pg_class parc     ON parc.oid = i.inhparent
          JOIN pg_namespace parn ON parn.oid = parc.relnamespace
          JOIN pg_class pc       ON pc.oid = i.inhrelid
          JOIN pg_namespace pn   ON pn.oid = pc.relnamespace
          JOIN pg_attribute a    ON a.attrelid = parc.oid
         WHERE parn.nspname IN ('market_data', 'analytics')
           AND a.attnum > 0 AND NOT a.attisdropped
           AND col_description(parc.oid, a.attnum) IS NOT NULL
    LOOP
        EXECUTE format('COMMENT ON COLUMN %I.%I.%I IS %L',
                       r.child_schema, r.child, r.attname, r.cmt);
    END LOOP;
END $$;


-- ================================================================ 2026-09-22 增补：调度器 / 画像 / 同花顺参考 新表列注释
COMMENT ON COLUMN operations.task.claimed_by IS '领取该任务的 worker 标识（pid@host），防重复执行';
COMMENT ON COLUMN operations.task.next_retry_at IS '失败后计划重试时间；为空表示不再重试';
COMMENT ON COLUMN operations.schedule_log.id IS '自增主键';
COMMENT ON COLUMN operations.schedule_log.job_key IS '被触发的任务键，对应 config.job.job_key';
COMMENT ON COLUMN operations.schedule_log.slot IS '调度槽位标识（本次 cron 命中的时间槽）';
COMMENT ON COLUMN operations.schedule_log.action IS '本次动作：fire 入队 / skip 跳过 / duplicate 去重丢弃 等';
COMMENT ON COLUMN operations.schedule_log.dry_run IS '是否干跑（只记日志不入队）';
COMMENT ON COLUMN operations.schedule_log.occurred_at IS '记录时间';
COMMENT ON COLUMN operations.schedule_log.reason IS '触发或跳过的原因文字，页面直接展示';
COMMENT ON COLUMN operations.schedule_log.task_id IS '入队成功时关联的 operations.task 任务号';
COMMENT ON COLUMN operations.schedule_state.job_key IS '任务键（主键），对应 config.job.job_key';
COMMENT ON COLUMN operations.schedule_state.cron_used IS '当前生效的 cron 表达式';
COMMENT ON COLUMN operations.schedule_state.next_fire_at IS '下一次应触发时间';
COMMENT ON COLUMN operations.schedule_state.last_fire_at IS '上一次实际触发时间';
COMMENT ON COLUMN operations.schedule_state.last_fire_slot IS '上一次触发的槽位标识，防同槽重复入队';
COMMENT ON COLUMN operations.schedule_state.last_result IS '上一次触发结果：success / failed / skipped / duplicate';
COMMENT ON COLUMN operations.schedule_state.last_success_date IS '最近一次跑成功对应的交易日';
COMMENT ON COLUMN operations.schedule_state.consecutive_fail IS '连续失败次数，超阈值可停表';
COMMENT ON COLUMN operations.schedule_state.missed_count IS '累计错过触发次数（机器休眠等）';
COMMENT ON COLUMN operations.schedule_state.note IS '备注（人工标注，如"暂停原因"）';
COMMENT ON COLUMN operations.schedule_state.updated_at IS '本行最后更新时间';
COMMENT ON COLUMN operations.scheduler_heartbeat.role IS '进程角色：scheduler 调度器 / worker 执行器';
COMMENT ON COLUMN operations.scheduler_heartbeat.pid IS '进程号';
COMMENT ON COLUMN operations.scheduler_heartbeat.hostname IS '主机名，多机部署时区分';
COMMENT ON COLUMN operations.scheduler_heartbeat.heartbeat_at IS '最近一次心跳时间，判断进程是否活着';
COMMENT ON COLUMN operations.scheduler_heartbeat.meta IS '附加状态 jsonb（当前任务、队列深度等）';
COMMENT ON COLUMN reference_data.stock_profile.security_code IS '证券代码';
COMMENT ON COLUMN reference_data.stock_profile.source_batch_id IS '写入批次号，外键 operations.ingest_batch';
COMMENT ON COLUMN reference_data.stock_profile.ipo_price IS 'IPO 发行价（元）';
COMMENT ON COLUMN reference_data.stock_profile.his_high IS '历史最高价（元，不复权原值）';
COMMENT ON COLUMN reference_data.stock_profile.his_low IS '历史最低价（元，不复权原值）';
COMMENT ON COLUMN reference_data.stock_profile.limit_up IS '当日涨停价（元）';
COMMENT ON COLUMN reference_data.stock_profile.limit_down IS '当日跌停价（元）';
COMMENT ON COLUMN reference_data.stock_profile.pe_dyn IS '动态市盈率（倍）';
COMMENT ON COLUMN reference_data.stock_profile.pe_static_ttm IS '市盈率 TTM（倍）';
COMMENT ON COLUMN reference_data.stock_profile.pb_mrq IS '市净率 PB（倍，最近报告期）';
COMMENT ON COLUMN reference_data.stock_profile.turnover_pct IS '换手率（%）';
COMMENT ON COLUMN reference_data.stock_profile.staff_num IS '员工人数（人）';
COMMENT ON COLUMN reference_data.stock_profile.main_business IS '主营业务（原文）';
COMMENT ON COLUMN reference_data.stock_profile.beta IS '贝塔系数（相对大盘）';
COMMENT ON COLUMN reference_data.stock_profile.hq_date IS '行情快照基准日';
COMMENT ON COLUMN reference_data.stock_profile.raw IS '源接口返回的原始 jsonb，留作对账与补解析';
COMMENT ON COLUMN reference_data.stock_profile.updated_at IS '本行最后更新时间';
COMMENT ON COLUMN config.ths_user_formula.formula_key IS '公式主键（同花顺本地文件里的公式标识）';
COMMENT ON COLUMN config.ths_user_formula.name IS '公式名称';
COMMENT ON COLUMN config.ths_user_formula.source IS '同步来源（同花顺本地目录下的源文件类别）';
COMMENT ON COLUMN config.ths_user_formula.synced_at IS '最近一次从本地文件同步的时间';
COMMENT ON COLUMN reference_data.ths_hk_broker.broker_id IS '港股席位编号';
COMMENT ON COLUMN reference_data.ths_hk_broker.name IS '席位名称（同花顺原始名）';
COMMENT ON COLUMN reference_data.ths_hk_broker.updated_at IS '本行最后更新时间';
