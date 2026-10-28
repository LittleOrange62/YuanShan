-- -*- coding: utf-8 -*-
-- 35_ma_hfq_screen.sql
-- 均线族换成复权价、补上年线（MA250），再把「价 vs 均线 / 均线 vs 均线 / 均线密集 / 均线排列」
-- 登记成可组合的筛选条件。
--
-- 为什么要动这一族（用户 2026-10-14 的需求原话：「250线以下，其他均线密集的这些个股」
--   「最好能兼容其他的，比如说年线以下，月线比10日线高，等等各种条件可以多个组合筛选」）：
--   1. 登记表 config.screen_rule 当时只有 5 条条件（amp10_over / intraday_limit_up / limit_up /
--      price_compare / rising_closes），**没有一条读均线** —— 这一句筛选在页面上一次都筛不出来。
--   2. 更要紧的一件：`config.indicator` 里 ma5..ma120 与 bias5/10/20 用的是**未复权收盘价**。
--      这不是精度问题，是判定方向问题 —— 2026-10-13 那场实测：5,560 只里 **133 只**
--      （够 250 根的 4,933 只里的 2.7%）「在年线上方还是下方」会因为复不复权而**翻转**。
--      603093 南华期货最典型：不复权年线 18.545（现价比它低 26.8%）vs 复权年线 13.397
--      （现价比它高 1.4%）—— 两把年线差 27.8%，因为它在窗口里除权过一次（因子 0.687）。
--      本项目别的模块早就定了规矩：`system_entry.py` 的「价格一律后复权（× adjustment_factor）」、
--      埋伏榜同样用 `close * adjustment_factor`。只有这一族是漏的。
--      用户 2026-10-14 裁定：**均线一律复权**（不是两把都给，也不是维持不复权）。
--
-- 复权系数怎么取（三条实测，缺一条都会静默出错）：
--   · 基准：`adjustment_factor` 归一到最新一天 —— 2026-10-14 当日 5,562 行里 5,448 行 =1、
--     114 行 <1、没有 >1 也没有 NULL；603093 在该窗口里只有两档（2025-12-19 起 0.687、
--     2026-08-10 起 1.0）。所以 `close × 因子` 就是那条能跟今天的价格连起来读的序列。
--   · 兜底：全表有 **61,295 行因子为 NULL**，涉及 115 只票（688071 一只占 1,246 行 ——
--     整条历史基本没因子）。裸写 `close * adjustment_factor` 会让这些票的均线**整条 NaN**，
--     落库时被跳过，页面上就成了「这只票不存在」。实测对照：裸乘写法下
--     MA10/20/30/60/120/250 分别少 110/105/98/83/64/48 只票。
--     所以一律写成「因子 > 0 才用，否则当 1」：缺因子的票退回不复权价，票留在筛选范围内，
--     口径上如实降级（这一条要在页面上说出来，不能藏着）。
--   · 窗口：250 根是硬要求。当日有行情的 5,577 只里 **627 只不足 250 根**（上市晚、长期停牌），
--     它们的年线是空值，不补 0 —— 补 0 会被读成「价在年线上方无限远」。
--
-- 密集/排列两条**刻意不落成派生指标行**，而是在条件模板里就地聚合那几行均线：
--   求值器的 MAX/MIN 走 `np.fmax/np.fmin`，那两个函数**忽略 NaN**（实测：
--   `MAX(MA(close,5)=NaN, MA(close,3)=3)` 给出 3 而不是 NaN）。所以「五条均线的极差」
--   写在公式语言里，会把「缺一条均线」的票算成「四条线的极差」——极差假小、密集条件假通过。
--   埋伏榜那条纪律是「缺的那一根不按 0 参与」（ambush_setup.ma_spread_pct 的 docstring）。
--   SQL 这边可以用 `HAVING count(*) = 5` 把缺线的票**整个判不了**，这才是诚实的写法。
--   顺带：均线定义仍然只有一份（config.indicator），这里只是读它那几行。
--
-- 条件模板的三条硬规矩（沿用 screener 的既有约束，别改）：
--   · 模板里不出现 `%`：psycopg 把 SQL 文本里的 `%` 当占位符起手（screener.py `_price_cols`
--     那段注释记着这个坑），偏离度一律写 `* 100`。
--   · 模板里不出现 `::`：占位符正则 `:([a-z_]\w*)` 会把 `::text` 的后半截当参数吃掉。
--     布尔转整数用 `CAST(... AS int)`，类型转换写 `CAST(x AS numeric)` 而不是 `x::numeric`。
--   · 均线名与比较符是**文本替换**，必须带 `literal: true` + `enum` 白名单；数值参数一律绑定。
--   · 比较的两侧同底：价用 `close × 因子`，均线也是复权均线。拿不复权现价去比复权均线，
--     比未复权年线本身错得更隐蔽。Postgres 没有 IF()，所以这里写 CASE ——
--     与 config.indicator 里那句 `IF(adjustment_factor > 0, …, 1)` 是同一条规矩的两种写法，
--     改的时候两处一起改（本文件末尾的实跑回验会逐条打）。

-- =============================================================================
-- ① 均线族换底 + 补年线（这一段由 app/compute/expr.py 那个受限求值器算，用 IF）
-- =============================================================================
UPDATE config.indicator SET
    expr = 'MA(close * IF(adjustment_factor > 0, adjustment_factor, 1), 5)',
    name = 'MA5（复权价）',
    note = '复权收盘价的近 5 场均值（× adjustment_factor，缺因子按不复权）。2026-10-14 起换底：'
           '原来是未复权价，除权日附近会把均线抬高，判定方向能翻（实测 133/4,933 只年线上下翻转）。'
           '不足 5 根给空值，不补 0。',
    updated_at = now()
 WHERE indicator_key = 'ma5';

UPDATE config.indicator SET
    expr = 'MA(close * IF(adjustment_factor > 0, adjustment_factor, 1), 10)',
    name = 'MA10（复权价）',
    note = '复权收盘价的近 10 场均值。缺复权因子的票按不复权算（115 只，缘由见 35 号脚本注释）。'
           '不足 10 根给空值。',
    updated_at = now()
 WHERE indicator_key = 'ma10';

UPDATE config.indicator SET
    expr = 'MA(close * IF(adjustment_factor > 0, adjustment_factor, 1), 20)',
    name = 'MA20（复权价·月线）',
    note = '复权收盘价的近 20 场均值。界面按 A 股习惯叫它月线（20 个交易日≈一个自然月）。'
           '不足 20 根给空值。',
    updated_at = now()
 WHERE indicator_key = 'ma20';

UPDATE config.indicator SET
    expr = 'MA(close * IF(adjustment_factor > 0, adjustment_factor, 1), 30)',
    name = 'MA30（复权价）',
    note = '复权收盘价的近 30 场均值。不足 30 根给空值。',
    updated_at = now()
 WHERE indicator_key = 'ma30';

UPDATE config.indicator SET
    expr = 'MA(close * IF(adjustment_factor > 0, adjustment_factor, 1), 60)',
    name = 'MA60（复权价·季线）',
    note = '复权收盘价的近 60 场均值。倍量榜/突破信号那一族的 MA60 各自在 SQL 里自算，不读这一行'
           '（它们要的是「不依赖 d1 有没有跑到基准日」）。不足 60 根给空值。',
    updated_at = now()
 WHERE indicator_key = 'ma60';

UPDATE config.indicator SET
    expr = 'MA(close * IF(adjustment_factor > 0, adjustment_factor, 1), 120)',
    name = 'MA120（复权价·半年线）',
    note = '复权收盘价的近 120 场均值。不足 120 根给空值。',
    updated_at = now()
 WHERE indicator_key = 'ma120';

-- 年线：这一档原来根本不存在（表里最大只到 ma120），而用户要的第一个条件就是它。
-- 回溯窗口不用放大：hhv250/llv250/pos250 已经把 required_lookback 撑到 250+12=262 场，
-- 加这一条不多读一行面板。
INSERT INTO config.indicator (indicator_key, name, kind, expr, output_unit, decimals, builtin, note) VALUES
('ma250', 'MA250（复权价·年线）', 'window',
    'MA(close * IF(adjustment_factor > 0, adjustment_factor, 1), 250)', 'yuan', 4, true,
    '复权收盘价的近 250 场均值。要正好 250 根才给值：当日有行情的 5,577 只里 627 只不足 250 根'
    '（上市晚、长期停牌），它们的年线是空值、不是 0，也不参加年线类筛选')
ON CONFLICT (indicator_key) DO UPDATE SET
    name = EXCLUDED.name, kind = EXCLUDED.kind, expr = EXCLUDED.expr,
    output_unit = EXCLUDED.output_unit, decimals = EXCLUDED.decimals,
    note = EXCLUDED.note, updated_at = now();

-- 偏离度三条跟着换底：bias 的定义就是「价 vs 均线」，价与均线必须同底；
-- 换了均线不换 bias，等于同一张表里放两把互相矛盾的读数。
UPDATE config.indicator SET
    expr = '(close * IF(adjustment_factor > 0, adjustment_factor, 1)'
    ' - MA(close * IF(adjustment_factor > 0, adjustment_factor, 1), 5))'
    ' / MA(close * IF(adjustment_factor > 0, adjustment_factor, 1), 5) * 100',
    name = 'BIAS5（复权价）',
    note = '（复权价 − 复权MA5）÷ 复权MA5 ×100。2026-10-14 随均线族一起换底，价与均线同底才成立',
    updated_at = now()
 WHERE indicator_key = 'bias5';

UPDATE config.indicator SET
    expr = '(close * IF(adjustment_factor > 0, adjustment_factor, 1)'
    ' - MA(close * IF(adjustment_factor > 0, adjustment_factor, 1), 10))'
    ' / MA(close * IF(adjustment_factor > 0, adjustment_factor, 1), 10) * 100',
    name = 'BIAS10（复权价）',
    note = '（复权价 − 复权MA10）÷ 复权MA10 ×100。随均线族一起换底',
    updated_at = now()
 WHERE indicator_key = 'bias10';

UPDATE config.indicator SET
    expr = '(close * IF(adjustment_factor > 0, adjustment_factor, 1)'
    ' - MA(close * IF(adjustment_factor > 0, adjustment_factor, 1), 20))'
    ' / MA(close * IF(adjustment_factor > 0, adjustment_factor, 1), 20) * 100',
    name = 'BIAS20（复权价）',
    note = '（复权价 − 复权MA20）÷ 复权MA20 ×100。随均线族一起换底',
    updated_at = now()
 WHERE indicator_key = 'bias20';


-- =============================================================================
-- ② 六条筛选条件登记（这一段是发给 PostgreSQL 的 WHERE 片段）
--    价那一侧的复权写法记作 PX = d.close * CASE WHEN d.adjustment_factor > 0
--    THEN d.adjustment_factor ELSE 1 END —— 与 ① 里那条 IF 同一条规矩。
--
--    默认阈值不是拍的，是 2026-10-14 全市场分位（够 250 根的 5,391 只）：
--      价距年线 4,357 只在下方（中位 -13.2%）；短五线极差 P25 3.99 / 中位 6.75；
--      多头排列层数 0/1/2/3/4 = 1,424/1,618/1,145/641/563；
--      「年线下方 且 短五线极差<=2%」= 306 只，放宽到 3% = 744 只。
-- =============================================================================

-- 1) 价 vs 一条均线
INSERT INTO config.screen_rule (rule_key, name, param_schema, sql_template, builtin, note) VALUES
('ma_position', '价在指定均线的上方／下方',
    '{"type":"object","required":["ma","op"],"properties":{
        "ma":{"enum":["ma5","ma10","ma20","ma30","ma60","ma120","ma250"],"literal":true,"default":"ma250"},
        "op":{"enum":[">","<"],"literal":true,"default":">"},
        "pct":{"type":"number","default":0}}}',
    'EXISTS (SELECT 1 FROM analytics.indicator_value i '
    '          WHERE i.security_code = d.security_code AND i.trade_date = :d '
    '            AND i.indicator_key = '':ma'' AND i.value > 0 '
    '            AND (d.close * CASE WHEN d.adjustment_factor > 0 THEN d.adjustment_factor ELSE 1 END '
    '                 - i.value) / i.value * 100 :op :pct)', true,
    '复权收盘价与该条复权均线的偏离度（%），与 pct 比大小。'
    '「250 线以下」＝ ma + ma250、op + < 、pct 0；「深跌破年线 10% 以上」＝op < 且 pct -10；'
    '「站上年线 3% 以上」＝op > 且 pct 3。'
    '两侧同底（都乘复权因子）。库里没有这条均线行的票判不了、不进结果 —— 那不是判它不合格'),

-- 2) 两条均线比高低
('ma_pair', '两条均线比高低（如月线在 10 日线上方）',
    '{"type":"object","required":["ma_a","ma_b","op"],"properties":{
        "ma_a":{"enum":["ma5","ma10","ma20","ma30","ma60","ma120","ma250"],"literal":true,"default":"ma20"},
        "ma_b":{"enum":["ma5","ma10","ma20","ma30","ma60","ma120","ma250"],"literal":true,"default":"ma10"},
        "op":{"enum":[">","<"],"literal":true,"default":">"},
        "gap_pct":{"type":"number","default":0}}}',
    'EXISTS (SELECT 1 FROM analytics.indicator_value a, analytics.indicator_value b '
    '          WHERE a.security_code = d.security_code AND a.trade_date = :d '
    '            AND b.security_code = d.security_code AND b.trade_date = :d '
    '            AND a.indicator_key = '':ma_a'' AND b.indicator_key = '':ma_b'' '
    '            AND a.value > 0 AND b.value > 0 '
    '            AND (a.value - b.value) / b.value * 100 :op :gap_pct)', true,
    '两条复权均线的差 ÷ 后一条（%），与 gap_pct 比大小。'
    '「月线比 10 日线高」＝ma_a=ma20、ma_b=ma10、op > 、gap_pct 0；「要高过 2% 才算」＝gap_pct 2。'
    '谁在上方由 op 说，不靠参数名猜（两个参数共用同一份 enum）。两条均线都得有行才进结果，'
    '缺一条就是判不了 —— 不是判它不合格'),

-- 3) 短五线密集
('dense_short5', '短五线密集（MA5/10/20/30/60 的极差 ≤ X）',
    '{"type":"object","required":["max_pct"],"properties":{
        "max_pct":{"type":"number","default":3}}}',
    'EXISTS (SELECT 1 FROM analytics.indicator_value i '
    '          WHERE i.security_code = d.security_code AND i.trade_date = :d '
    '            AND i.indicator_key IN (''ma5'', ''ma10'', ''ma20'', ''ma30'', ''ma60'') '
    '          GROUP BY i.security_code '
    '         HAVING count(*) = 5 '
    '            AND (max(i.value) - min(i.value)) / nullif(min(i.value), 0) * 100 <= :max_pct)', true,
    '五条短周期复权均线里最高与最低的差 ÷ 最低那根（%），小于等于 max_pct 算密集 —— '
    '各周期持仓成本趋同。默认 3.0%（2026-10-14 全市场 P25 3.99、中位 6.75；'
    '<=2% 330 只、<=3% 810 只）。'
    '那句 `HAVING count(*) = 5` 是这条条件的命门：五条线必须都在，缺一条就整只判不了。'
    '少了它，只有 3 条线的票会因为线少而极差假小，被当成最密集的票排到最前面 —— '
    '与埋伏榜那条「缺的那一根不按 0 参与」（ambush_setup.ma_spread_pct）是同一条纪律'),

-- 4) 中长四线密集
('dense_mid4', '中长四线密集（MA20/60/120/250 的极差 ≤ X）',
    '{"type":"object","required":["max_pct"],"properties":{
        "max_pct":{"type":"number","default":15}}}',
    'EXISTS (SELECT 1 FROM analytics.indicator_value i '
    '          WHERE i.security_code = d.security_code AND i.trade_date = :d '
    '            AND i.indicator_key IN (''ma20'', ''ma60'', ''ma120'', ''ma250'') '
    '          GROUP BY i.security_code '
    '         HAVING count(*) = 4 '
    '            AND (max(i.value) - min(i.value)) / nullif(min(i.value), 0) * 100 <= :max_pct)', true,
    '月线/季线/半年线/年线这一层的四根复权均线拧在一起。与短五线那一组是两件事：'
    '短密长散＝短线整理；短长都密＝大级别蓄势。'
    '默认 15%（2026-10-14 全市场 P25 12.12、中位 19.01）。同样要求四根都在'),

-- 5) 多头排列层数
('align_bull', '多头排列的层数不少于 N',
    '{"type":"object","required":["min_layers"],"properties":{
        "min_layers":{"type":"integer","default":3}}}',
    'EXISTS (SELECT 1 FROM analytics.indicator_value m5, analytics.indicator_value m10, '
    '               analytics.indicator_value m20, analytics.indicator_value m60 '
    '          WHERE m5.security_code = d.security_code AND m5.trade_date = :d '
    '            AND m5.indicator_key = ''ma5'' '
    '            AND m10.security_code = d.security_code AND m10.trade_date = :d '
    '            AND m10.indicator_key = ''ma10'' '
    '            AND m20.security_code = d.security_code AND m20.trade_date = :d '
    '            AND m20.indicator_key = ''ma20'' '
    '            AND m60.security_code = d.security_code AND m60.trade_date = :d '
    '            AND m60.indicator_key = ''ma60'' '
    '            AND CASE WHEN m5.value > m10.value THEN 1 ELSE 0 END '
    '              + CASE WHEN m10.value > m20.value THEN 1 ELSE 0 END '
    '              + CASE WHEN m20.value > m60.value THEN 1 ELSE 0 END '
    '              + CASE WHEN d.close * CASE WHEN d.adjustment_factor > 0 THEN d.adjustment_factor '
    '                                         ELSE 1 END > m20.value THEN 1 ELSE 0 END '
    '                >= :min_layers)', true,
    '四层各算一分（MA5>MA10、MA10>MA20、MA20>MA60、复权价>MA20），满分 4，筛「层数 ≥ N」。'
    '读的是趋势的完整度，不是强弱名次：4 层＝全多头，2 层＝半多头。'
    '与 app/compute/builtin/quality_qushi.py 那四层同口径（那份进分数、这一份进筛选），'
    '改层数定义要两处一起改。2026-10-14 全市场分布 0/1/2/3/4 层 = 1,424/1,618/1,145/641/563 只'),

-- 6) 空头排列层数
('align_bear', '空头排列的层数不少于 N',
    '{"type":"object","required":["min_layers"],"properties":{
        "min_layers":{"type":"integer","default":3}}}',
    'EXISTS (SELECT 1 FROM analytics.indicator_value m5, analytics.indicator_value m10, '
    '               analytics.indicator_value m20, analytics.indicator_value m60 '
    '          WHERE m5.security_code = d.security_code AND m5.trade_date = :d '
    '            AND m5.indicator_key = ''ma5'' '
    '            AND m10.security_code = d.security_code AND m10.trade_date = :d '
    '            AND m10.indicator_key = ''ma10'' '
    '            AND m20.security_code = d.security_code AND m20.trade_date = :d '
    '            AND m20.indicator_key = ''ma20'' '
    '            AND m60.security_code = d.security_code AND m60.trade_date = :d '
    '            AND m60.indicator_key = ''ma60'' '
    '            AND CASE WHEN m5.value < m10.value THEN 1 ELSE 0 END '
    '              + CASE WHEN m10.value < m20.value THEN 1 ELSE 0 END '
    '              + CASE WHEN m20.value < m60.value THEN 1 ELSE 0 END '
    '            + CASE WHEN d.close * CASE WHEN d.adjustment_factor > 0 THEN d.adjustment_factor '
    '                                         ELSE 1 END < m20.value THEN 1 ELSE 0 END '
    '                >= :min_layers)', true,
    '与多头那四条完全对称（四条全部反向），给「年线下方 + 短五线密集 + 空头排列」这类组合用。'
    '两条独立登记、不合成一个带正负的数：4 层多头与 4 层空头是两件事，'
    '压成一个数之后就筛不动了')
ON CONFLICT (rule_key) DO UPDATE SET
    name = EXCLUDED.name, param_schema = EXCLUDED.param_schema,
    sql_template = EXCLUDED.sql_template, note = EXCLUDED.note,
    builtin = EXCLUDED.builtin;

grant select on config.indicator to ft_reader, ft_app;
grant select on config.screen_rule to ft_reader, ft_app;
