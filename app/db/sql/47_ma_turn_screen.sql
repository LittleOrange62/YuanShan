-- -*- coding: utf-8 -*-
-- 47_ma_turn_screen.sql
-- 新增一条参数化筛选条件 `ma_turn`（均线拐头）：均线周期 N + 长期段（M 日、方向 down/up）
-- + 近期段（X 日、形态 flat/recover/fall）+ 阈值 Y%（比较方向 gte/lte）。
--
-- 用户 2026-10-20 夜的原话拆成**一条参数化规则**（不是四条硬编码规则）：
--   「跑这一次筛选（条件可以叠 6 条）……就是XX日线拐头的，比如说参数60，那就是60日线拐头的，
--     并且怎么定义拐头，也可以使用参数，比如说长期下行 or 长期上行？如果选了 X 日前长期下行，
--     叠加近 X 日 横住了或者有所回升，回升率（降低率）低于（高于）Y%」
--
-- 判据四件事同时成立才算拐头（本体住在 `app/compute/ma_turn.py`，测试钉的就是那一份）：
--   1. 长期段：ma_N(t−M) → ma_N(t) 净变化方向 = long_dir（down＝<0，up＝>0）；
--   2. 拐头确认：ma_N 在基准日的一阶差分（今日−昨日）与长期方向**反号**；
--   3. 近期段：ma_N(t−X) → ma_N(t) 净变化符合 recent_shape（recover>0 / fall<0 / flat 幅度≤Y）；
--   4. 阈值：|近期净变化| 与 Y% 按 cmp 比（gte ≥ / lte ≤）；**flat 档天然是 lte**，不再叠第二道比较。
--   只有斜率翻号、幅度不够，不算。
--
-- 为什么均线在 SQL 里**现算后复权**，不读 `analytics.indicator_value`（三条，缺一不可）：
--   · 那张表只留最近 94 个交易日的因子行（2026-10-21 实测 min 2026-06-02 / max 2026-10-20），
--     long_days 一超出就静默查无此行 —— 空名单在页面上和「真筛出 0 只」长得一模一样
--     （同 `app/compute/pattern.py` 头部那条教训：现算不依赖 d1 有没有跑到基准日）；
--   · 表里只有 ma5/10/20/30/60/120/250 七档固定均线，**任意 N 本来就得现算**——
--     取舍：开 N 为 5..250 的任意整数（现算），而不是只放开库里那几档；
--     `vol_double.py` 自算 MA60 是同一路线的先例；
--   · **绝不允许未复权均线**：除息日会把「下行」判成「走平」（2026-10-13 实测
--     133/4,933 只因复不复权把年线上下判定判反，见 35 号脚本头部）。复权写法与
--     35_ma_hfq_screen.sql 一字一条：`close × 因子`，因子缺失或 ≤0 退回不复权
--     （全表 61,295 行/115 只没因子，裸乘会让它们的均线整条空掉、从筛选范围消失）。
--
-- 条件模板的三条硬规矩（沿用 35 号脚本与 screener 的既有约束，别改）：
--   · 模板里不出现 `%`（psycopg 把 SQL 文本里的 % 当占位符起手）——偏离度一律 `* 100`；
--   · 模板里不出现 `::`（占位符正则会把 ::text 的后半截当参数吃掉）；
--   · 数值/枚举参数**全部走绑定**（param_schema 不带 literal），非 literal 的 enum 与
--     minimum/maximum 由执行引擎 `_coerce` 在 2026-10-21 起同样点名退回 ——
--     long_dir/recent_shape/cmp 在模板里是 CASE 的比较对象，不是文本替换，
--     所以没有注入面，也不必披 literal 那层白名单外衣。
--
-- 缺数不判 0：不足 N+M+1 根 K 线、或四个取值点（t / t−1 / t−X / t−M）任一根均线凑不齐的票，
-- SQL 里 NULL 传播 → 判不了、不进结果，不会被当成「判它不合格」。
--
-- 默认参数（只住这一处 param_schema，界面读 `/api/screen/rules` 拿默认，不再抄第二份）：
--   n=60、long_days=60、recent_days=10、long_dir=down、recent_shape=recover、
--   threshold_pct=0.5、cmp=gte —— 2026-10-21 实测（基准日 2026-10-20，全市场 5,560 只母体）：
--   默认档命中 1,442 只（自选池档要看自选里有什么，不背这个数）；
--   N=20 命中 175 只、N=60 命中 1,442 只、N=120 命中 94 只；单次实跑全市场约 6~8 秒。
--   ⚠️ `GET /api/screen` 目前不交全场命中数（只有 returned/limit/truncated），所以这几个数是
--   这一次 CLI 实跑的底档，不是接口读数；要接口也交命中总数得先给 screener 加一份 count 查询。

INSERT INTO config.screen_rule (rule_key, name, param_schema, sql_template, builtin, note) VALUES
('ma_turn', '均线拐头（N 日线拐头，后复权现算）',
    '{"type":"object","required":["n","long_days","long_dir","recent_days","recent_shape","threshold_pct","cmp"],
      "properties":{
        "n":{"type":"integer","default":60,"minimum":5,"maximum":250},
        "long_days":{"type":"integer","default":60,"minimum":10,"maximum":250},
        "long_dir":{"enum":["down","up"],"default":"down"},
        "recent_days":{"type":"integer","default":10,"minimum":2,"maximum":120},
        "recent_shape":{"enum":["recover","flat","fall"],"default":"recover"},
        "threshold_pct":{"type":"number","default":0.5,"minimum":0,"maximum":50},
        "cmp":{"enum":["gte","lte"],"default":"gte"}}}',
    'EXISTS (SELECT 1 FROM ( '
    '        SELECT max(CASE WHEN rn = 0 AND bars >= :n THEN ma END) AS a0, '
    '               max(CASE WHEN rn = 1 AND bars >= :n THEN ma END) AS a1, '
    '               max(CASE WHEN rn = :recent_days AND bars >= :n THEN ma END) AS ar, '
    '               max(CASE WHEN rn = :long_days AND bars >= :n THEN ma END) AS al '
    '          FROM (SELECT avg(px) OVER (ORDER BY trade_date ROWS BETWEEN (:n - 1) PRECEDING AND CURRENT ROW) AS ma, '
    '                       count(*) OVER (ORDER BY trade_date ROWS BETWEEN (:n - 1) PRECEDING AND CURRENT ROW) AS bars, '
    '                       row_number() OVER (ORDER BY trade_date DESC) - 1 AS rn '
    '                  FROM (SELECT x.trade_date AS trade_date, '
    '                               x.close * CASE WHEN x.adjustment_factor > 0 THEN x.adjustment_factor ELSE 1 END AS px '
    '                          FROM market_data.stock_daily x '
    '                         WHERE x.security_code = d.security_code AND x.trade_date <= :d '
    '                         ORDER BY x.trade_date DESC '
    '                         LIMIT (:long_days + :n + 1)) h) m) p '
    '         WHERE a0 IS NOT NULL AND a1 IS NOT NULL AND al > 0 AND ar > 0 '
    '           AND (CASE WHEN :long_dir = ''down'' THEN al - a0 ELSE a0 - al END) > 0 '
    '           AND (CASE WHEN :long_dir = ''down'' THEN a0 - a1 ELSE a1 - a0 END) > 0 '
    '           AND (CASE WHEN :recent_shape = ''recover'' THEN a0 - ar '
    '                     WHEN :recent_shape = ''fall'' THEN ar - a0 '
    '                     WHEN :recent_shape = ''flat'' THEN CASE WHEN abs(a0 - ar) / ar * 100 <= :threshold_pct THEN 1 ELSE 0 END '
    '                     ELSE 0 END) > 0 '
    '           AND (CASE WHEN :recent_shape = ''flat'' THEN 1 '
    '                     WHEN :cmp = ''gte'' THEN CASE WHEN abs(a0 - ar) / ar * 100 >= :threshold_pct THEN 1 ELSE 0 END '
    '                     WHEN :cmp = ''lte'' THEN CASE WHEN abs(a0 - ar) / ar * 100 <= :threshold_pct THEN 1 ELSE 0 END '
    '                     ELSE 0 END) > 0)', true,
    '这条答的问题是：哪只票的 N 日均线（后复权）在长期走势的末端翻向，且近期变化的幅度够得上 Y。'
    '判据一句话：一根后复权均线，长期段按 long_dir 走了净趋势，且基准日的日均线一阶差分翻出反号（拐头），'
    '且近期段净变化符合 recent_shape、其幅度按 cmp 与阈值 Y% 比得成立 —— '
    '长期方向、末端翻号、近期形态、幅度四件同时满足才算命中；只有斜率翻号、幅度不够，不算。'
    '参数：n 均线周期（现算，5..250 任意整数）；long_days 长期段回看 M；long_dir 长期方向（down 下行/up 上行）；'
    'recent_days 近期段回看 X（必须小于 M）；recent_shape 近期形态（recover 回升/flat 横住/fall 回落）；'
    'threshold_pct 阈值 Y（0..50 的百分数，负数或大于 50 点名退回）；cmp 比较方向（gte 幅度≥Y/lte 幅度≤Y，flat 档天然 lte）。'
    '口径：均线是 SQL 里用后复权收盘价现算的（close × adjustment_factor，缺因子退回不复权，实测 61,295 行/115 只）—— '
    '不读 analytics.indicator_value（那张表只留最近 94 场因子行，长期段超出就静默给空名单），'
    '除息日不会把下行判成走平。缺数不判 0：不足 N+M+1 根或四个取值点均线凑不齐的票判不了、不进结果。'
    '默认参数（N=60、M=60、X=10、Y=0.5、down+recover+gte）2026-10-21 实测基准日 2026-10-20 全市场命中 1,442 只'
    '（N=20 命中 175、N=120 命中 94）—— 这是那一天用默认参数在 CLI 实跑的记录，'
    '接口一次只交前 N 行、不交全场命中数，别把它当成今天的数')
ON CONFLICT (rule_key) DO UPDATE SET
    name = EXCLUDED.name, param_schema = EXCLUDED.param_schema,
    sql_template = EXCLUDED.sql_template, note = EXCLUDED.note,
    builtin = EXCLUDED.builtin;
