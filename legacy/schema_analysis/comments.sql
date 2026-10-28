\set ON_ERROR_STOP on
-- ================================================================ config.datasource
comment on table config.datasource is '外部数据源注册表。登记每个数据源的接入方式、配额、健康度与探测参数；密钥本身只存环境变量名，绝不入库';
comment on column config.datasource.ds_key is '数据源稳定标识（主键），如 ak_etf_sse、ts_margin';
comment on column config.datasource.name is '数据源显示名';
comment on column config.datasource.kind is '接入方式：package Python包 / http HTTP接口 / mcp MCP工具 / local 本地文件';
comment on column config.datasource.provider is '供给方，如 akshare / tushare / eastmoney';
comment on column config.datasource.endpoint is '探测用函数名或 URL，如 fund_etf_scale_sse、__version__';
comment on column config.datasource.auth_ref is '密钥所在环境变量名（如 FT_TUSHARE_TOKEN）。密钥值只在 .env，库里只登记变量名';
comment on column config.datasource.quota_limit is '配额上限。无配额概念的源留空，页面不显示余量条而非 0%';
comment on column config.datasource.quota_used is '本期已用配额';
comment on column config.datasource.quota_reset is '配额重置周期：daily 每日 / monthly 每月 / NULL 无配额';
comment on column config.datasource.health is '可用性状态：ok 通 / degraded 通但不稳 / no_permission 有接口未买权限（页面显示灰不显示红）/ broken 接口自身有bug / down 连不上 / unknown 未探测';
comment on column config.datasource.last_ok_at is '最近一次成功时间';
comment on column config.datasource.last_check is '最近一次探测时间';
comment on column config.datasource.last_err is '最近一次错误摘要';
comment on column config.datasource.latency_ms is '最近一次探测往返耗时（毫秒）';
comment on column config.datasource.row_count is '最近一次探测返回行数。0 行也是信号（如节假日无数据 vs 接口失效）';
comment on column config.datasource.probe_spec is '探测参数 jsonb，按 kind 形如 {"kind":"df","fn":"...","args":{"date":"{date}"}}，{date} 运行时替换为最近交易日。改它等于改代码，维护页不允许就地编辑';
comment on column config.datasource.seq is '展示顺序，小的在前';
comment on column config.datasource.is_active is '是否启用。停用不等于删除，保留历史探测记录';
comment on column config.datasource.note is '备注';
comment on column config.datasource.updated_at is '本行最后更新时间';
comment on column config.datasource.layer is '优先级层级：p0 通达信本地文件（有本地数据就不出网）/ main 主力层（日常生产）/ base 基座层（历史底座）/ assist 辅助层（交叉验证，缺了不影响跑批）';
comment on column config.datasource.ds_role is '职责一句话，如 日常生产主力 / 本地权威源 / 辅助校准。不叫 role 是因为该词已被数据库连接角色占用';
comment on column config.datasource.cost is '费用性质文字（免费 / 免费额度 / 免费·Key已配），不写价格数字';
comment on column config.datasource.tagline is '一句话定位：凭什么在这一层，如 覆盖面最广、匿名免注册';

-- ================================================================ config.help_note（表注释已有，补列）
comment on column config.help_note.note_key is '说明正文主键，如 page_sector、card_k_mood';
comment on column config.help_note.scope is '说明挂载层级：page 整页 / card 卡片 / stat 指标块 / snippet 公共片段（只被引用不直接显示）';
comment on column config.help_note.target is '定位目标：page=路由（/sector）、card=卡片稳定别名、stat=指标键（k_mood）、snippet 留空';
comment on column config.help_note.title is '标题';
comment on column config.help_note.purpose is '为什么有这个窗口——回答什么问题，不写"显示XX"';
comment on column config.help_note.content is '看什么、怎么读';
comment on column config.help_note.source_query is '取数用的命名查询名（app/web/queries.py 的 q_*）';
comment on column config.help_note.source_tables is '直接依赖的库表数组';
comment on column config.help_note.script is '产出该数据的脚本';
comment on column config.help_note.derive is '二次加工链：上游哪些数据、谁算的';
comment on column config.help_note.formula is '参数与得分口径：每个分项的含义与取值范围';
comment on column config.help_note.caveat is '坑与已知缺口，渲染时自动前缀警示图标';
comment on column config.help_note.includes is '引用的 snippet note_key 数组，片段可嵌套且带环检测';
comment on column config.help_note.params is 'jsonb 参数，供正文 {名字} 占位符取值，避免写死数字';
comment on column config.help_note.seq is '片段内渲染顺序';
comment on column config.help_note.enabled is '是否启用';
comment on column config.help_note.updated_at is '本行最后更新时间';

-- ================================================================ config.popup_rule
comment on table config.popup_rule is '通达信客户端弹窗看门狗规则表（app/tdx_client/watchdog.py）。处理启动/登录/下载阶段弹出的窗口；遇到新弹窗加一行即可，不改代码';
comment on column config.popup_rule.rule_key is '规则主键，如 keep_tq_setting、login_elsewhere';
comment on column config.popup_rule.match_type is '匹配方式：title 按窗口标题 / class 按 Win32 类名';
comment on column config.popup_rule.pattern is '匹配字符串，包含匹配（非正则）';
comment on column config.popup_rule.action is '处置动作：ignore 放过（绝不能关）/ alert 只报不关 / close 关闭窗口 / button 点指定按钮（模态框不认 WM_CLOSE 时）';
comment on column config.popup_rule.button_text is 'action=button 时要点击的按钮文字';
comment on column config.popup_rule.priority is '匹配优先级，数值小的先试。ignore/alert 必须小于 close，否则宽泛规则会先抢走关键窗口';
comment on column config.popup_rule.is_active is '是否启用';
comment on column config.popup_rule.note is '备注：为什么有这条规则';
comment on column config.popup_rule.updated_at is '本行最后更新时间';

-- ================================================================ fct.ignition
comment on table fct.ignition is '趋势启动点信号（app/compute/ignition.py）。板块与自选组共用同一套五要素评分与阈值；判据只用截至当日收盘的信息，不含未来数据';
comment on column fct.ignition.scope is '标的类别：sector 板块 / group 自选组';
comment on column fct.ignition.obj_key is '标的键：scope=sector 时为 ref.sector_info.sector_code（TDX_880xxx），scope=group 时为自选组键';
comment on column fct.ignition.trade_date is '信号交易日';
comment on column fct.ignition.stage is '五档状态，判定优先级 ignite>follow>fade>coil>none。coil 蓄势（波动放大+低位，仅前置观察非买入清单）/ ignite 点火（前5日内曾蓄势且今日点火·量能·广度三项齐达标）/ follow 延续（近3日点过火且热度3日斜率仍正）/ fade 退潮（热度120日分位>=0.80 且斜率转负，反向清单）/ none 无信号';
comment on column fct.ignition.score is '综合分 0~100，五要素按权重加权平均（squeeze .15 / pos .20 / ignition .30 / volume .20 / breadth .15），空要素不计权重';
comment on column fct.ignition.squeeze is '波动状态分 0~100：热度20日标准差与日内振幅20日均值各取120日分位后求均。方向为波动已在放大得高分（回测推翻"越沉寂越高分"的原直觉）';
comment on column fct.ignition.pos is '位置分 0~100：当日热度在自身120日滚动分位的低位程度 =(1-分位)x100，专治追高';
comment on column fct.ignition.ignition is '点火分 0~100：热度3日斜率由负转正40分 + 斜率仍加速30分 + 热度MA5上穿MA20 30分';
comment on column fct.ignition.volume is '量能分 0~100：板块成交额/自身20日均额（占一半）+ 板块占全市场成交额份额的20日倍数（占一半）。看份额是为避免大盘普涨日全体误报';
comment on column fct.ignition.breadth is '广度分 0~100：板块内上涨家数占比水平（占一半）+ 相对10日均值的抬升（占一半），数据取 md.sector_daily';
comment on column fct.ignition.n_terms is '当日五个要素分中有值的个数（0~5）。230个行业板块无涨跌家数最多4项，故永远不会判为 ignite';
comment on column fct.ignition.detail is 'jsonb 算分原始量（heat_pctile/vol_pctile/slope3/vol_ratio/share_ratio/up_ratio等及三个布尔），供页面解释"凭什么说它点火"';
comment on column fct.ignition.task_id is '产出该行的任务号，外键 ops.task';
comment on column fct.ignition.calculated_at is '本行计算/覆盖时间（upsert 时置 now()）';

-- ================================================================ fct.sector_heat_daily
comment on table fct.sector_heat_daily is '板块级日热度分。现行口径为板块直算（sector_heat_hist.py，可回溯到2021），member_agg 为已废弃的成员股聚合旧口径';
comment on column fct.sector_heat_daily.sector_code is '板块代码，如 TDX_880501';
comment on column fct.sector_heat_daily.trade_date is '交易日';
comment on column fct.sector_heat_daily.heat is '板块热度分 0~100，由涨幅/广度/动力/成交四分项按 config.binding 权重合成，缺失项不计权重';
comment on column fct.sector_heat_daily.member_scored is '当天真有评分的成员股数。仅对旧口径 src=member_agg 有意义，现行 sector 口径写 NULL';
comment on column fct.sector_heat_daily.task_id is '产出该行的任务号';
comment on column fct.sector_heat_daily.calculated_at is '本行计算/覆盖时间';
comment on column fct.sector_heat_daily.src is '热度口径来源：sector 板块级直算（现行）/ member_agg 成员股聚合（历史遗留，口径已证实错误：摊下去用唯一主板块、聚回来用全部归属）';
comment on column fct.sector_heat_daily.n_parts is '四分项里真有值的个数（1~4）。1 项撑起的热度与 4 项齐全不可同等采信；member_agg 行为空';

-- ================================================================ fct.sector_trend
comment on table fct.sector_trend is '板块热度多窗口趋势，长表非宽表：一行=一个板块x一个日期x一个窗口';
comment on column fct.sector_trend.sector_code is '板块代码';
comment on column fct.sector_trend.trade_date is '交易日（窗口右端点）';
comment on column fct.sector_trend.win is '窗口长度，单位交易日（非日历日，长假不改变量纲）。取值 1/3/5/7/9/11/15/21，默认见 config.setting；win=1 是特例，存日趋势分值（今日热度-昨日热度）';
comment on column fct.sector_trend.slope is '热度最小二乘回归斜率 regr_slope(heat, 序号)，单位分/天，向上为正。页面显示的趋势分 = slope x win';
comment on column fct.sector_trend.n_points is '窗口内真正参与拟合的点数，不足窗口不补齐不置0，页面用 n_points>=win 过滤';
comment on column fct.sector_trend.task_id is '产出该行的任务号';
comment on column fct.sector_trend.calculated_at is '本行计算/覆盖时间';

-- ================================================================ md.etf_share
comment on table md.etf_share is 'ETF 每日总份额（上交所/深交所公告，c4_etf_share.py 采集），与份额变动一起构成 ETF 净申赎资金流';
comment on column md.etf_share.ts_code is 'ETF 代码，如 510300.SH';
comment on column md.etf_share.trade_date is '交易日';
comment on column md.etf_share.fund_name is '基金简称（交易所公告口径）。也是 md.v_etf_flow 判 asset_class 的唯一依据，etf_type 分不出股/债/货币';
comment on column md.etf_share.etf_type is '交易所给的分类：跨市 / 单市 / 跨境 / 其他；深交所不提供，为 NULL';
comment on column md.etf_share.share_units is '基金份额总数，单位份（不是万份/亿份），与 md.fund_daily.vol_units 同单位可直接乘 close 得金额';
comment on column md.etf_share.src_batch_id is '写入批次号，外键 ops.ingest_batch';
comment on column md.etf_share.updated_at is '本行最后更新时间';

-- ================================================================ md.fund_flow_rank
comment on table md.fund_flow_rank is '个股/板块主力资金净流入榜（东财 push2，c11_fundflow.py），按日x窗口x对象存储。常为空表，按需手动采集';
comment on column md.fund_flow_rank.trade_date is '榜单日期';
comment on column md.fund_flow_rank.scope is '榜单口径：stock 个股 / sector 板块';
comment on column md.fund_flow_rank.obj_code is '对象代码：scope=stock 时为 ts_code，sector 时由板块名称回查 sector_code';
comment on column md.fund_flow_rank.obj_name is '东财原始名称';
comment on column md.fund_flow_rank.window_days is '统计窗口天数：1/3/5/10，对应东财"今日/3日/5日/10日"榜';
comment on column md.fund_flow_rank.main_net is '主力净流入净额（超大单+大单），单位元，折算留给展示层';
comment on column md.fund_flow_rank.main_pct is '主力净流入净占比（%）= 净额 / 当期总成交额';
comment on column md.fund_flow_rank.pct_chg is '涨跌幅（%），取自东财榜单原列；3/5/10日榜该列可能是当日涨跌幅而非区间累计';
comment on column md.fund_flow_rank.src_batch_id is '写入批次号';
comment on column md.fund_flow_rank.updated_at is '本行最后更新时间';

-- ================================================================ md.lhb_detail
comment on table md.lhb_detail is '龙虎榜个股明细（akshare 转东财，c6_lhb.py）。全部金额列单位为元';
comment on column md.lhb_detail.ts_code is '证券代码';
comment on column md.lhb_detail.trade_date is '交易日';
comment on column md.lhb_detail.reason is '上榜原因原文。一票一日可多因上榜，故主键必含此列';
comment on column md.lhb_detail.close is '收盘价（元）';
comment on column md.lhb_detail.pct_chg is '涨跌幅（%）';
comment on column md.lhb_detail.net_buy is '龙虎榜净买额（元）';
comment on column md.lhb_detail.buy_amount is '龙虎榜买入额（元）';
comment on column md.lhb_detail.sell_amount is '龙虎榜卖出额（元）';
comment on column md.lhb_detail.lhb_amount is '龙虎榜成交额（元）';
comment on column md.lhb_detail.market_amount is '该股当日市场总成交额（元）';
comment on column md.lhb_detail.net_buy_ratio is '净买额占总成交比（%）';
comment on column md.lhb_detail.amount_ratio is '龙虎榜成交额占总成交比（%）';
comment on column md.lhb_detail.turnover_rate_pct is '换手率（%）';
comment on column md.lhb_detail.float_mv_yuan is '流通市值（元）';
comment on column md.lhb_detail.interpret is '东财解读原文，如 3家机构买入，成功率47.89%';
comment on column md.lhb_detail.src_batch_id is '写入批次号';
comment on column md.lhb_detail.updated_at is '本行最后更新时间';

-- ================================================================ md.lhb_inst
comment on table md.lhb_inst is '龙虎榜机构席位汇总（仅"机构专用"席位，游资/营业部不在内）。金额单位元';
comment on column md.lhb_inst.ts_code is '证券代码';
comment on column md.lhb_inst.trade_date is '交易日';
comment on column md.lhb_inst.reason is '上榜原因原文，与 md.lhb_detail 同源';
comment on column md.lhb_inst.buy_inst_count is '买入方向机构席位家数';
comment on column md.lhb_inst.sell_inst_count is '卖出方向机构席位家数';
comment on column md.lhb_inst.inst_buy is '机构席位买入总额（元）';
comment on column md.lhb_inst.inst_sell is '机构席位卖出总额（元）';
comment on column md.lhb_inst.inst_net is '机构席位净买额（元）';
comment on column md.lhb_inst.market_amount is '该股当日市场总成交额（元）';
comment on column md.lhb_inst.net_ratio is '机构净买额占总成交额比（%）';
comment on column md.lhb_inst.src_batch_id is '写入批次号';
comment on column md.lhb_inst.updated_at is '本行最后更新时间';

-- ================================================================ md.margin_detail
comment on table md.margin_detail is '融资融券逐股明细（akshare 转交易所，c5_margin.py）。沪深两交所列不一样，本表为并集，缺列留空不补算；深交所 T+1 发布';
comment on column md.margin_detail.ts_code is '证券代码';
comment on column md.margin_detail.trade_date is '交易日';
comment on column md.margin_detail.rz_balance is '融资余额（元）';
comment on column md.margin_detail.rz_buy is '融资买入额（元）';
comment on column md.margin_detail.rz_repay is '融资偿还额（元，仅上交所提供）';
comment on column md.margin_detail.rq_volume is '融券余量（股）';
comment on column md.margin_detail.rq_sell is '融券卖出量（股）';
comment on column md.margin_detail.rq_repay is '融券偿还量（股，仅上交所提供）';
comment on column md.margin_detail.rq_balance is '融券余额（元，仅深交所提供）';
comment on column md.margin_detail.rzrq_balance is '融资融券余额合计（元，仅深交所提供）';
comment on column md.margin_detail.src_batch_id is '写入批次号';
comment on column md.margin_detail.updated_at is '本行最后更新时间';

-- ================================================================ md.market_fund_flow
comment on table md.market_fund_flow is '大盘主力资金流向（东财，一日一行）。净额单位元，净占比 = 净额/当日总成交额（%），跨日可比';
comment on column md.market_fund_flow.trade_date is '交易日';
comment on column md.market_fund_flow.sh_close is '上证指数收盘（点）';
comment on column md.market_fund_flow.sh_pct is '上证涨跌幅（%），用于对齐"钱进来了指数涨没涨"';
comment on column md.market_fund_flow.sz_close is '深证成指收盘（点）';
comment on column md.market_fund_flow.sz_pct is '深证涨跌幅（%）';
comment on column md.market_fund_flow.main_net is '主力净流入净额（元）= 超大单+大单';
comment on column md.market_fund_flow.main_pct is '主力净流入净占比（%）';
comment on column md.market_fund_flow.xl_net is '超大单净流入净额（元）';
comment on column md.market_fund_flow.xl_pct is '超大单净占比（%）';
comment on column md.market_fund_flow.lg_net is '大单净流入净额（元）';
comment on column md.market_fund_flow.lg_pct is '大单净占比（%）';
comment on column md.market_fund_flow.md_net is '中单净流入净额（元）';
comment on column md.market_fund_flow.md_pct is '中单净占比（%）';
comment on column md.market_fund_flow.sm_net is '小单净流入净额（元）';
comment on column md.market_fund_flow.sm_pct is '小单净占比（%）';
comment on column md.market_fund_flow.src_batch_id is '写入批次号';
comment on column md.market_fund_flow.updated_at is '本行最后更新时间';

-- ================================================================ ops.datasource_probe
comment on table ops.datasource_probe is '数据源探测历史（只追加快照）。config.datasource 存最新值，本表存全过程，用于区分"昨天好今天坏"与"一直坏"';
comment on column ops.datasource_probe.probe_id is '自增主键';
comment on column ops.datasource_probe.ds_key is '被探测的数据源，外键 config.datasource.ds_key';
comment on column ops.datasource_probe.checked_at is '探测完成时间';
comment on column ops.datasource_probe.health is '本次结论，取值同 config.datasource.health';
comment on column ops.datasource_probe.latency_ms is '本次往返耗时（毫秒）';
comment on column ops.datasource_probe.row_count is '本次返回行数，0 行也是信号';
comment on column ops.datasource_probe.message is '结果摘要：成功时常为版本号或状态串，失败为错误摘要';

-- ================================================================ ref.sector_alias
comment on table ref.sector_alias is '板块主键别名表（B7 归一遗留）。归一前 sector_daily 与 sector_member 的 sector_code 交集为 0，热度四分项全部静默算不出';
comment on column ref.sector_alias.alias_code is '归一前的旧主键，如 GN_880501（概念）/ FG_880xxx（风格），供旧书签与旧导出回查';
comment on column ref.sector_alias.sector_code is '归一后的现行主键，如 TDX_880501';
comment on column ref.sector_alias.src is '旧前缀的语义类别：概念 / 风格 / 行业';
comment on column ref.sector_alias.note is '备注';
comment on column ref.sector_alias.created_at is '入库时间';

-- ================================================================ 视图 md.v_daily_source
comment on view md.v_daily_source is '每个交易日的日线由哪个源写入。一个日期可多行；质量规则 daily_source_is_tdx 据此报警';
comment on column md.v_daily_source.trade_date is '交易日';
comment on column md.v_daily_source.source is '归并后的中文口径：通达信vipdoc（本地 .day，主源，精度最高）/ 通达信TQ接口（同源，float32 精度差1e-8）/ 旧库迁移 / 网络兜底（sina、akshare、*_em）/ 未知';
comment on column md.v_daily_source.raw_source is 'ops.ingest_batch.source 原值';
comment on column md.v_daily_source.row_count is '当日该源写入的日线行数';
comment on column md.v_daily_source.ingested_at is '该批最近开始时间';

-- ================================================================ 视图 md.v_etf_flow
comment on view md.v_etf_flow is '逐只 ETF 每日净申赎流水：份额 LAG 差分 x 收盘估算金额。看股市增量资金必须过滤 asset_class=equity，否则混入 1000元/份的债券ETF';
comment on column md.v_etf_flow.ts_code is 'ETF 代码';
comment on column md.v_etf_flow.trade_date is '交易日';
comment on column md.v_etf_flow.fund_name is '基金简称';
comment on column md.v_etf_flow.etf_type is '交易所分类原值';
comment on column md.v_etf_flow.asset_class is '按名称与 etf_type 现推的类别：money 货币 / bond 债 / qdii 跨境 / commodity 商品 / equity 股票型';
comment on column md.v_etf_flow.share_units is '当日份额（份）';
comment on column md.v_etf_flow.prev_share_units is '前一记录日份额（份）';
comment on column md.v_etf_flow.prev_trade_date is '前一记录日，用于识别漏采：漏一天则 delta 实为两天累计';
comment on column md.v_etf_flow.delta_units is '份额净申赎（份）= 当日 - 前日';
comment on column md.v_etf_flow.close is '当日基金收盘价（元，LEFT JOIN md.fund_daily）';
comment on column md.v_etf_flow.delta_amount_yuan is '净申赎金额估算（元）= delta_units x close';

-- ================================================================ 视图 md.v_margin_trend
comment on view md.v_margin_trend is '逐股融资余额多窗口趋势，LAG 按本表行数（即交易日）位移';
comment on column md.v_margin_trend.ts_code is '证券代码';
comment on column md.v_margin_trend.trade_date is '交易日';
comment on column md.v_margin_trend.rz_balance is '当日融资余额（元）';
comment on column md.v_margin_trend.rz_d1 is '1个交易日前融资余额（元）';
comment on column md.v_margin_trend.rz_d5 is '5个交易日前融资余额（元）。漏采会让 lag(5) 实际跨 6 日';
comment on column md.v_margin_trend.rz_d10 is '10个交易日前融资余额（元）';
comment on column md.v_margin_trend.rz_d20 is '20个交易日前融资余额（元）';
comment on column md.v_margin_trend.d5_date is '5个交易日前的日期，用于自查漏采';
comment on column md.v_margin_trend.chg1_pct is '较1日变化率（%），基期<=0 时留空不填0';
comment on column md.v_margin_trend.chg5_pct is '较5个交易日变化率（%）';
comment on column md.v_margin_trend.chg10_pct is '较10个交易日变化率（%）';
comment on column md.v_margin_trend.chg20_pct is '较20个交易日变化率（%）';
comment on column md.v_margin_trend.rz_of_float_pct is '融资余额占流通市值比（%）= rz_balance/(流通股本x收盘)，缺流通股本或无收盘价时留空';
