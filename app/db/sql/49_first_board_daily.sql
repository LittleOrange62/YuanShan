-- -*- coding: utf-8 -*-
-- 49_first_board_daily.sql
-- 「首板分析」：首页「全市场换手率 TOP20」上面那一屏的落库面。一次算完那屏要的三件事 ——
--   ① 今天哪些票是**首板**（连板段的第一天）；
--   ② 它**近期的走势**（截止首板日的近 5/10/15 场后复权涨幅、启动前的量能抢跑比、倍量天数、加权分）；
--   ③ **为什么涨停**（同花顺题材串、首封/末封时间、炸板次数、封单额与封单量、几天几板），
--      以及**前一天是怎么走的**（涨幅、换手、成交额、量比、有没有冲过板又炸开、形状分档）。
-- 用户 2026-10-21 原话：「全市场换手率 TOP20 上面加一个首板分析，分析首板个股的情况，以及近期的走势，
-- 分析涨停原因，特别是前一天的走势」。
--
-- 六条口径（与写侧 `app/compute/first_board_daily.py` 逐字对齐，改那边要一起改这里）：
--   ① **母体只有 fb-2 这一把尺**：`analytics.first_board_shape`（`spec_version='fb-2'`）里
--      `launch_date` 等于本场的那批行 ＝ 本地日线 `close >= limit_up - 0.01` **且**是连板段第一天，
--      当天收盘就能构造、不筛结果（段长在那张表里是结果变量）。本表**不另起一把「首板」判据**，
--      所有当日读数都从母体行出发。母体那张表的日期列叫 `launch_date`，本表按复盘习惯叫
--      `trade_date` —— 同一个时刻，只是这一屏还要和前一日、和源站池对齐，别再留第三种名字。
--   ② 涨停原因/封板时间来自同花顺涨停揭秘池（`duanxian/fetchers.fetch_zt_reasons`，
--      本机没有 IWENCAI_API_KEY 时落 `duanxian/historical_sources.public_limit_reasons`，
--      逐行核对封板时间戳必须落在目标日，日期不符直接抛错）。
--      ⚠️ **保留窗实测约一年**（2026-10-21 回填 40 场时现探）：08-19…10-21 每场都拉得到、
--      2025-11-03 还有 66 只、**2025-09-22 起 status_code 非 0** ⇒ 超出窗是真取不到，
--      漏采仍按**永久缺**处理（留 NULL + `missing` 点名，不许拿别日的题材串冒充）。
--      先前那条「20260925 返回 0 行」不是过期，是**那天不是交易日**（中秋，本地日线那天也 0 行）。
--      ⚠️ **源站不收的要分开认**（同一次回填 1,911 行实测）：北交所 23 只 0 只有题材串
--      （`filter=HS,GEM2STAR` 不覆盖）、ST 82 只只有 1 只有（源站基本不收 ST 涨停）、
--      非 ST 的 1,806 只里 1,792 只有（99.2%）⇒ 题材串空着绝大多数是 `source_row_absent`（源站不收），
--      不是 `source_no_records`（漏采）—— 屏幕上这是两句话，别并成一句。
--      首封时间/炸板次数走这一条源，**不依赖本地分钟线**（`market_data.stock_minute` 只到 2026-10-19，
--      10-20/10-21 整天没有分钟数据）。
--   ③ 「几天几板」只住 `duanxian/board_span.py`（回溯 30 场、连续 2 场没封住才算断、触板不算板），
--      与 fb-2 的 `seg_len`（**严格连续**的段长）不是同一把尺子：首板当天 `seg_len` 恒为 1，
--      而 `boards` 可以是 2（断了 1 天又封上的那种）。所以两列都留着；`source_boards_agree`
--      只把**本地数出来的**与**源站 `high_days` 写的**不一致处点出来，**谁也不覆盖谁**。
--   ④ 近 5/10/15 场涨幅只住 `duanxian/day_returns.py`：后复权（`close × adjustment_factor`）、
--      窗口按**市场场次数**而不是这只票自己的 K 线根数，基期那天没行（停牌/未上市）就交 NULL。
--      ⚠️ 方向要说清：这三列是**截止首板日往回看**的涨幅（"启动之前已经涨了多少"），
--      不是首板日之后的走势 —— 后者在母体那张表里是 `r1/r3/r5/r10`，最新一场恒为 NULL（次日还没发生）。
--   ⑤ 前一日读数取 `market_data.stock_daily` 原值（涨幅/换手/成交额是未复权的当日读数，
--      与母体 `launch_pct` 同尺）。量比＝前一日成交量 ÷ **前一日往前 5 场的均量（不含前一日）**。
--      阈值就定在这串实测分位上（2026-10-21 现查近 25 场、1,094 只首板）：
--        前日涨幅 p10 −5.05 / p25 −2.94 / p50 −0.51 / p75 +1.55 / p90 +3.51；
--        前日量比 p25 0.77 / p50 1.00 / p75 1.40 / p90 2.04；前日换手中位 3.84%；
--        前日触板炸开 34/1,094（3.1%）；前日 ≤−5% 113 只（10.3%）；前日 ≥+5% 未封 64 只（5.8%）；
--        **前日收盘封住 0 只**（首板的定义使然 ⇒ `prev_sealed` 为 true 的行数必须恒等于 0，
--        不为 0 就说明母体串了，`--verify` 当场红）。
--   ⑥ 算不出就是 **NULL，不写 0**：0 在这里是一个真实读数（"前一日平盘"、"炸板 0 次"），
--      NULL 才是"这一格判不了"。哪一列因为什么缺，逐列记进 `missing`（键名＝列名）。

create table if not exists analytics.first_board_daily (
  security_code       text          not null,               -- 带后缀全码（000518.SZ），与全库一致
  trade_date          date          not null,               -- 首板日（＝母体的 launch_date）
  spec_version        text          not null,

  -- 一、当日读数（首板这一场本身）
  launch_pct          numeric(10,4),                        -- 当日涨幅 %（未复权原值，母体同名列）
  launch_close        numeric(12,4),                        -- 当日收盘价（**未复权**，与 limit_up_price 同尺）
  launch_turnover     numeric(10,4),                        -- 当日换手率 %
  launch_amount_yuan  numeric(24,2),                        -- 当日成交额（元）
  limit_up_price      numeric(12,4),                        -- 当日涨停价档位（stock_daily.limit_up，未复权）
  is_one_word         boolean,                              -- 当日一字板（high==low），母体 launch_is_1word
  float_cap_yuan      numeric(24,2),                        -- 流通市值（元），源站 currency_value 原值

  -- 二、源站读数（同花顺涨停揭秘池；漏采一天永久缺）
  reason              text,                                 -- 涨停原因题材串（源站编辑归因，非公司确认）
  first_seal_at       time,                                 -- 首次封板时刻（Asia/Shanghai 钟表时间）
  last_seal_at        time,                                 -- 最后（回封）时刻
  break_count         smallint,                             -- 炸板次数，源站 open_num 原值
  order_amount        numeric(24,2),                        -- 封单额（元），源站 order_amount
  order_volume        numeric(20,2),                        -- 封单量（股），源站 order_volume
  limit_up_kind       text,                                 -- 板型：换手板 / 一字板 / T 字板（源站 limit_up_type）
  source_high_days    text,                                 -- 源站"几天几板"文本，**只用来比对**，不覆盖本地 boards
  reason_source       text,                                 -- 题材串出处标记（见下方 comment）
  reason_captured_at  timestamptz,                          -- 这一格的抓取时刻（reason 为 NULL 时同样 NULL）

  -- 三、这一段走到哪（本地口径 board_span，与源站 high_days 各留一份）
  boards              smallint,                             -- 这一轮封住的次数（本地数出来）
  board_days          smallint,                             -- 这一轮的跨度（几天）
  board_label         text,                                 -- 「3天2板」这种人话
  source_boards_agree boolean,                              -- 本地 boards 与源站 high_days 拆出来的板数是否一致

  -- 四、前一日的走势（用户点名"特别是前一天的走势"）
  prev_date           date,                                 -- 前一场交易日（市场场次，不是自然日）
  prev_pct            numeric(10,4),                        -- 前一日涨幅 %（未复权）
  prev_turnover       numeric(10,4),                        -- 前一日换手率 %
  prev_amount_yuan    numeric(24,2),                        -- 前一日成交额（元）
  prev_vol_ratio      numeric(10,4),                        -- 前一日量 ÷ 前一日往前 5 场的均量（不含前一日）
  prev_touched        boolean,                              -- 前一日盘中冲过涨停价（high ≥ 档位 − eps）
  prev_sealed         boolean,                              -- 前一日收盘还封在涨停价上 ⇒ 恒应为 false（母体自检）
  prev_shape          text,                                 -- 前一日形状分档（七档，判不了 NULL）
  prev_shape_note     text,                                 -- 这一档的人话说明（与 prev_shape 同一处出）

  -- 五、近期走势（截止首板日往回看，后复权）
  chg5                numeric(10,4),                        -- 近 5 场涨幅 %（day_returns，span=5）
  chg10               numeric(10,4),                        -- 近 10 场涨幅 %
  chg15               numeric(10,4),                        -- 近 15 场涨幅 %
  vol_grab_ratio      numeric(10,4),                        -- 量能抢跑比＝母体 pre5（前 5 根均量 ÷ 前 25 根均量）
  dbl_days            smallint,                             -- 启动前 30 根里量比 ≥2.0 的天数（母体同名列）
  calm_days           smallint,                             -- 启动前 30 根里量比 <0.6 的天数（母体同名列）
  dbl_energy          numeric(12,4),                        -- 倍量加权分（analytics.volume_double 当日行）
  energy_fp           text,                                 -- 那一行的口径指纹（分数只在同指纹里可比）

  -- 六、诚实性
  missing             jsonb         not null default '{}'::jsonb,
  universe            smallint,                             -- 当日本地判定的涨停（封住）家数：分母随行交出

  calculated_at       timestamptz   not null default now(),
  primary key (security_code, trade_date, spec_version)
);

comment on table analytics.first_board_daily is
  '首板分析（fbd-1）：母体＝analytics.first_board_shape 的 fb-2 当日行（连板段第一天，当天就能构造、不筛结果），'
  '每只首板一行，带当日读数、源站涨停原因与封板细节、几天几板、**前一日走势**、截止首板日的近 5/10/15 场复权涨幅。'
  '与 first_board_shape 的分工：那张表回答「启动前什么形状预示走到几板」（跨年样本、含未来结果 r*），'
  '本表回答「今天这批首板是什么情况、为什么涨停、昨天怎么走的」（每日一场、只放当天与过去）。'
  '写侧 python -m app.compute.first_board_daily。涨停原因源站只留最近几场 ⇒ 漏采永久缺，别指望回补。';

comment on column analytics.first_board_daily.security_code is
  '带交易所后缀的全码（000518.SZ），与全库同一条列名/同一种写法；与母体 first_board_shape.security_code 逐字相同'
  '（对账就靠这一列 + 下面那一列，别 left(...,6) 去 join，那个写法实测慢十倍）';
comment on column analytics.first_board_daily.spec_version is
  '本表判据的版本号（当前 fbd-1）。全库约定：改判据是新写一批行、历史那批留着对账，不是就地覆盖；'
  '所以**每一句读它的 SQL 都要带版本条件**，否则会把两把尺子的行混在一起数';
comment on column analytics.first_board_daily.trade_date is
  '首板日。等于母体 `analytics.first_board_shape.launch_date`（那张表的日期列叫 launch_date，没有 trade_date 列）；'
  '本表按"每日一场"的复盘习惯统一叫 trade_date，两者是同一个时刻';
comment on column analytics.first_board_daily.launch_pct is
  '首板当日涨幅 %（未复权的 stock_daily.pct_change，逐字搬运母体同名列）。主板约 10、创业板/科创板约 20、'
  '北交所 30；⚠️ 不是判据意义上的"封住"，封没封住由 limit_up_price 与 launch_close 那两列说';
comment on column analytics.first_board_daily.launch_turnover is
  '首板当日换手率 %（母体 launch_turnover 原值）。这一屏就在「全市场换手率 TOP20」下面，'
  '两列同尺才有对照意义：换手榜看的是当天谁换得多，这里看的是首板那天换了多少';
comment on column analytics.first_board_daily.launch_amount_yuan is
  '首板当日成交额（元，母体 launch_amount_yuan 原值）。与 float_cap_yuan 相除就是"当天换掉了几成流通盘"';
comment on column analytics.first_board_daily.limit_up_price is
  '当日涨停价档位（stock_daily.limit_up，**未复权**，由 app/compute/limit_prices.py 定档）。'
  '母体的封板判据是 launch_close >= 这一列 − 0.01 —— 那 1 分钱的容差不是随手写的：'
  '实测本地档位列有 1 分的取整差（25/1,911 行的收盘价正好＝档位 −0.01），'
  'board_span 用的是 0.001 容差，所以那批行在那里数不出"这一轮"（boards 交 NULL）';
comment on column analytics.first_board_daily.launch_close is
  '首板日收盘价，**未复权**原值（market_data.stock_daily.close）。与 limit_up_price 同尺，两列一起看才知道离档位还差几分；'
  '跨除权日不可比 —— 复权口径的走势在 chg5/chg10/chg15';
comment on column analytics.first_board_daily.is_one_word is
  '当日一字板（high==low），逐字搬运母体 launch_is_1word。一字板当天买不进，这一格是给"这板能不能吃到"用的，不是给涨幅用的';
comment on column analytics.first_board_daily.float_cap_yuan is
  '流通市值（元），源站涨停池 currency_value 原值。源站没这只（北交所、或整场漏采）⇒ NULL，不拿本地股本折算一个顶上';

comment on column analytics.first_board_daily.reason is
  '涨停原因题材串，来自同花顺涨停揭秘池（无 IWENCAI_API_KEY 时 duanxian/historical_sources.public_limit_reasons，'
  '有密钥时走问财）。⚠️ 空着分两种，必须分开显示：`source_row_absent`＝源站不收这只（实测 40 场 118 只空行里 '
  'ST 81 只、北交所 23 只、其余 14 只），`source_no_records`＝那一场整场没拉回来（超出保留窗就是**永久缺**，'
  '实测窗约一年：2025-11-03 还拉得到、2025-09-22 起已空）。'
  '是源站编辑归因，不是公司公告';
comment on column analytics.first_board_daily.first_seal_at is
  '首次封板时刻（Asia/Shanghai 钟表时间，由源站 first_limit_up_time 那串 unix 秒换算）。'
  '⚠️ 走源站而不是本地分钟线：market_data.stock_minute 只到 2026-10-19，10-20/10-21 整天没有分钟数据。'
  '换算前逐行核对过那个时间戳落在本场，落不上就整场判为取数失败（同 public_limit_reasons 的纪律）';
comment on column analytics.first_board_daily.last_seal_at is
  '最后一次封板时刻（源站 last_limit_up_time）。比 first_seal_at 晚得越多＝中间炸过又回封，与 break_count 一起读';
comment on column analytics.first_board_daily.break_count is
  '炸板次数，源站 open_num 原值（单位：次）。0 是「一次没炸」这个真实读数，NULL 才是「源站没给这一格」。'
  '⚠️ 实测 2026-10-21 池内 50 只里 **27 只 open_num 整格为空**，而那些行的 change_tag 一律是 FIRST_LIMIT'
  '（另 23 只带数字的行是 LIMIT_BACK）—— 空看着就是 0，但那是源站没写、不是源站说 0，'
  '本表**不折算**：留 NULL 并记 missing.break_count＝source_field_null。'
  '这一格也没有与本地分钟线交叉核对过（10-20/10-21 本地整场没有分钟数据）';
comment on column analytics.first_board_daily.order_amount is
  '收盘时封单额（元，源站 order_amount）。是**抓取时刻**的快照，不是全日最大值；NULL＝源站没这只';
comment on column analytics.first_board_daily.order_volume is
  '收盘时封单量（股，源站 order_volume）。与 order_amount 同一次快照，两列除一下就是当时封单价';
comment on column analytics.first_board_daily.limit_up_kind is
  '板型（源站 limit_up_type，如「换手板」「一字板」「T字板」）。与 is_one_word 是两条来源：一个是源站标签、一个是本地日线量出来的';
comment on column analytics.first_board_daily.source_high_days is
  '源站写的「几天几板」**原文字符串**（实测取值：「首板」28 只、「2天2板」8 只、「5天3板」「9天7板」……）。'
  '留着是为了能对账，不是为了显示 —— 本表的几天几板走 duanxian/board_span.py（30 场窗口、连续 2 场没封住才算断、触板不算板）。'
  '板数解析认两种写法：「N天M板」与「首板」（＝1 天 1 板），认不出的写法交 None，不猜';
comment on column analytics.first_board_daily.reason_source is
  '题材串的出处：iwencai＝问财结构化查询；ths_limit_up_pool＝同花顺涨停揭秘池（本机当前走这条，'
  '没有 IWENCAI_API_KEY）。口径原文住在 app/compute/first_board_daily.REASON_SOURCE_NOTES，屏幕念那一份。'
  'NULL＝这一场根本没抓到东西（源站已过保留期 / 网络失败），为什么缺写在 missing.reason';
comment on column analytics.first_board_daily.reason_captured_at is
  '这一格题材串的抓取时刻（带时区）。只有真拿到题材串的行才有值 —— 抓失败的行留 NULL，'
  '免得屏幕把"我来过、我没查到"读成"我查到了空"';

comment on column analytics.first_board_daily.boards is
  '本地数出来的这一轮封板次数（duanxian/board_span.boards_by_code，回溯 30 场、触板炸掉那天不算板）。'
  '⚠️ 与母体 seg_len 不是一把尺子：seg_len 要求**严格连续**，首板当天恒为 1；'
  'boards 允许中间空 1 天，所以首板当天完全可能是 2（断了 1 天又封上）。两列都要，别合并';
comment on column analytics.first_board_daily.board_days is
  '本地这一轮的跨度（几天），与 boards 同一次计算、同一套判据；NULL＝本地数不出这一轮（当日日线缺行等）';
comment on column analytics.first_board_daily.board_label is
  '「3天2板」这种人话，board_span 现成的 label 字段，屏幕直接念，前端不要自己拼';
comment on column analytics.first_board_daily.source_boards_agree is
  '本地 boards 与源站 source_high_days 拆出来的板数是否一致。true/false 都要出声 —— '
  '不一致是常态（两把尺子：源站把断了 1 天又封上的写成「3天2板」，board_span 也数得出 2，'
  '而母体 seg_len 只认严格连续、恒为 1），要能看见，**不许偷偷取其一**；'
  '任一边解析不出来 ⇒ NULL（判不了），不是 false。实测 2026-10-21 那场 34 只：源站写「首板」28 只';

comment on column analytics.first_board_daily.prev_date is
  '前一场交易日（market_data.stock_daily 里早于 trade_date 的最近一个**市场场次**，不是自然日）。NULL＝这天没有更早的日线';
comment on column analytics.first_board_daily.prev_pct is
  '前一日涨幅 %（未复权 pct_change）。近 25 场 1,094 只首板实测分位：p10 −5.05 / p25 −2.94 / p50 −0.51 / p75 +1.55 / p90 +3.51'
  '（2026-10-21 现查）⇒ 半数首板前一天其实是**跌的或平盘**，"昨天已经在拉"的是那一小撮';
comment on column analytics.first_board_daily.prev_turnover is
  '前一日换手率 %。实测中位 3.84%（同一样本）';
comment on column analytics.first_board_daily.prev_amount_yuan is
  '前一日成交额（元，stock_daily.amount_yuan 原值）。与首板日的 launch_amount_yuan 并排读＝'
  '「昨天做了多少量、今天做了多少量」；实测量比与它是两条独立的证据（量比看量、这一列看钱的规模）';
comment on column analytics.first_board_daily.prev_vol_ratio is
  '前一日成交量 ÷ **前一日往前 5 场的均量（不含前一日）**。实测分位 p25 0.77 / p50 1.00 / p75 1.40 / p90 2.04。'
  '⚠️ 分母那 5 场里真有的行不足 3 根（次新股/长停牌）⇒ NULL，判不了就不给数，不拿 2 根均量凑一个看着像量的比值';
comment on column analytics.first_board_daily.prev_touched is
  '前一日盘中冲过涨停价（high ≥ 当日档位 − 同一个容差）又没封住 ＝ 上一天试过板、当天才真封。'
  '实测近 25 场 1,094 只首板里 34 只（3.1%）是这一档，是 prev_shape 的第一档';
comment on column analytics.first_board_daily.prev_sealed is
  '前一日收盘还封在涨停价上。⚠️ **按首板定义必须恒为 false**（前一天也封着就不是连板段的第一天了）⇒ '
  '全表 true 的行数必须为 0，--verify 钉这一条；不为 0 说明母体串了（或容差/档位取数变了），要查因不要改判据';
comment on column analytics.first_board_daily.prev_shape is
  '前一日形状七档（阈值只住 app/compute/first_board_daily.PREV_SHAPE_THRESHOLDS 一处）：'
  'broke_at_limit 触板炸开 / deep_pullback 深水区回撤（≤−5%）/ surge_unsealed 已大涨未封（≥+3.5%）/ '
  'quiet_shrink 平盘缩量 / pullback_shrink 回撤缩量 / mild_up 温和放量上行 / other。'
  '按这个优先级逐档判；**所需输入任一为 NULL 且更高档还排除不掉时交 NULL**（不许硬猜），缺了什么记在 missing.prev_shape';
comment on column analytics.first_board_daily.prev_shape_note is
  'prev_shape 那一档的人话说明（一句话讲清为什么这么画）。与 prev_shape 同时为空';

comment on column analytics.first_board_daily.chg5 is
  '截止首板日的近 5 场后复权涨幅 %（duanxian/day_returns.returns_by_code(span=5) 的 chg_span，'
  '基期＝往前第 5 个市场场次那天）。⚠️ 方向：这是**启动之前**已经涨了多少，不是首板日之后的走势'
  '（之后的在母体的 r1/r3/r5/r10，最新一场恒 NULL 因为次日还没发生）。基期那天停牌/未上市 ⇒ NULL，不是 0%';
comment on column analytics.first_board_daily.chg10 is '截止首板日的近 10 场后复权涨幅 %，口径同 chg5';
comment on column analytics.first_board_daily.chg15 is '截止首板日的近 15 场后复权涨幅 %，口径同 chg5。与倍量榜那两列同尺（day_returns 一处）';
comment on column analytics.first_board_daily.vol_grab_ratio is
  '量能抢跑比＝母体 pre5 逐字搬运（启动前最后 5 根量比均值 ÷ 前 25 根量比均值）。'
  '⚠️ 它是**量**能比值，不是涨幅：>1 ＝首板之前那 5 天已经在放量抢跑。窗口不满 30 根的次新股母体自己就是 NULL';
comment on column analytics.first_board_daily.dbl_days is
  '启动前 30 根里量比 ≥2.0 的天数（母体 dbl_days，窗口是首板日之前）。与 analytics.volume_double.dbl_count'
  '不是一把尺子：那一张是"近 40 场"的另一个窗口，两张都要就都读，别互换';
comment on column analytics.first_board_daily.calm_days is
  '启动前 30 根里量比 <0.6 的天数（母体 calm_days）。安静了多久 vs 有没有倍量日是两回事，两列并排才有信息';
comment on column analytics.first_board_daily.dbl_energy is
  '倍量加权分，取 analytics.volume_double **当日那一行**（40 场窗口）。分数只在同 energy_fp 的批里可比，'
  '所以指纹一起落库；当日 volume_double 没有这只票的行 ⇒ NULL（那一张本来就只收全市场有行情的票）';
comment on column analytics.first_board_daily.energy_fp is
  'dbl_energy 所依据的配置指纹（app/compute/volume_energy.fingerprint）。改一档分值就换一个号，跨指纹做差等于拿旧尺减新尺';

comment on column analytics.first_board_daily.missing is
  '这一行哪些格判不了、因为什么缺：对象，**键名＝列名**，值是短码。空对象＝一样都不缺。'
  '源站那一族：source_no_records 整场漏采（**永久缺，事后补不回来**）、source_row_absent 池子里没这只票'
  '（多为北交所，源站 filter 不收）、source_date_mismatch 时间戳落在别的日子、source_paging_incomplete 名单不完整、'
  'source_http_error 当场网络/解析失败（可重试，与过期是两件事）、source_reason_empty 池子有而题材串整场空、'
  'source_field_null 这只票在池里但那一格源站没给（实测 open_num 常空）。'
  '前一日与本地那一族：no_prev_row 前一日没日线、no_prev_volume 前一日量缺、prev_base_bars_lt3 均量分母根数不够、'
  'limit_up_missing_on_prev 前一日没有档位、chg_base_day_missing 复权基期没行、no_volume_double_row 当日倍量表没行、'
  'prev_sealed_true_not_first_board 与首板定义冲突、board_source_unavailable / chg_source_unavailable 整批读回空'
  '（那是"没读到库"，不是"这批票都没有"，两句话在界面上必须分开）。'
  '屏幕念这一格，不要靠"这列是 NULL"自己猜原因 —— 漏采永久缺与源站不收北交所，是两句话';
comment on column analytics.first_board_daily.universe is
  '当日本地判定的涨停（收盘封住）家数：market_data.stock_daily 那天 close ≥ limit_up − 0.01 的行数。'
  '分母随行交出 —— 源站池只 50 只而本地封板 68 家时，"覆盖率 50/68"必须能在这一行上直接算出来，'
  '不能只住在某个查询里，否则隔天源站过期就再也对不上账';

-- 这一屏一场一清（重算前先删这一场这一版本），所以 calculated_at 就是这一次重算的时刻；
-- 页面上"这批数几点做的"读它，不要读 reason_captured_at（那个是源站抓取时刻，两者可以差几秒也可以差几天）。
comment on column analytics.first_board_daily.calculated_at is
  '这一行被写进库的时刻（默认 now()）。一场重算会把这一场的行全部删掉再插，所以同一场的这一列基本相同；'
  '它与 reason_captured_at 的差就是"本地算数"与"出网抓源站"之间的耗时';

create index if not exists first_board_daily_date_idx
  on analytics.first_board_daily (spec_version, trade_date desc);
create index if not exists first_board_daily_code_idx
  on analytics.first_board_daily (security_code, trade_date desc);

grant select, insert, update, delete on analytics.first_board_daily to ft_etl, ft_app;
grant select                         on analytics.first_board_daily to ft_reader;

-- ──────────────────────────────────────────────────────────────────────────────
-- 10-27 用户点名加的四列历史位置（`fbd-2`）：3 个月涨跌停次数 / 6 个月连板最高 /
-- 2 年区间位置 / 均线排列。四列都是**这只票自己的历史**，与源站无关 ⇒ 源站过期补不回来
-- 那一条不影响它们。加列不改旧列口径，但读侧要能分清"这批场次有没有这四列" ⇒ 升 spec_version。
-- ──────────────────────────────────────────────────────────────────────────────
alter table analytics.first_board_daily add column if not exists lu_sealed_3m   int;
alter table analytics.first_board_daily add column if not exists lu_touched_3m  int;
alter table analytics.first_board_daily add column if not exists ld_sealed_3m   int;
alter table analytics.first_board_daily add column if not exists ld_touched_3m  int;
alter table analytics.first_board_daily add column if not exists max_consec_6m  int;
alter table analytics.first_board_daily add column if not exists close_adj      numeric(14,4);
alter table analytics.first_board_daily add column if not exists hi_2y          numeric(14,4);
alter table analytics.first_board_daily add column if not exists lo_2y          numeric(14,4);
alter table analytics.first_board_daily add column if not exists pos_2y         numeric(6,2);
alter table analytics.first_board_daily add column if not exists bars_2y        int;
alter table analytics.first_board_daily add column if not exists ma5            numeric(14,4);
alter table analytics.first_board_daily add column if not exists ma10           numeric(14,4);
alter table analytics.first_board_daily add column if not exists ma20           numeric(14,4);
alter table analytics.first_board_daily add column if not exists ma60           numeric(14,4);
alter table analytics.first_board_daily add column if not exists ma_stack       text;

comment on column analytics.first_board_daily.lu_sealed_3m  is '近 63 场（≈3 个月）里**收盘封住涨停**几天：close ≥ limit_up − 0.01（与母体、与「前一日封住」同一条容差，只住写侧常量 `LIMIT_SEAL_EPS`）。含当日那一根（首板当天必然封住，所以这一列最少是 1）';
comment on column analytics.first_board_daily.lu_touched_3m is '近 63 场里**盘中摸到过涨停价**几天：high ≥ limit_up − 0.01（含后来炸开的）。与上一列是两把尺：本机实测同一场 触板 103 对 封住 84，差出来的那 19 只是"冲板没封住"，屏幕上两数并排给，不许只留一个';
comment on column analytics.first_board_daily.ld_sealed_3m  is '近 63 场里收盘封住跌停几天：close ≤ limit_down + 0.01。0 是真读数（这三个月一次都没被按死过），不是缺数 —— 缺数走 missing 短码、这一列是 NULL';
comment on column analytics.first_board_daily.ld_touched_3m is '近 63 场里盘中摸到跌停价几天：low ≤ limit_down + 0.01（含尾盘打开的）';
comment on column analytics.first_board_daily.max_consec_6m is '近 126 场（≈6 个月）**连板最高到过几板**：读 `analytics.limit_up_pool_daily.consec`（lup-1）里这只票 sealed 行的最大值 —— 连板数只住那一处，本表不另数一遍（自己数会把"停牌那天算不算断"再判一次）。NULL = 这半年 lup 里没有这只票的封板行（首板当天必然有一行，所以真为 NULL 时先怀疑 lup 那一场没跑）';
comment on column analytics.first_board_daily.close_adj     is '当日**后复权**收盘价 = close × coalesce(adjustment_factor, 1)。下面那三列（区间高低、位置、均线）全部与它同底 —— 用未复权价算两年最高，一次 10 送 10 就会造出一个假顶';
comment on column analytics.first_board_daily.hi_2y         is '近 489 场（≈2 年）后复权最高价（取 high × 因子，不是收盘：打板看的是"离历史顶还有多远"，盘中顶也是顶）';
comment on column analytics.first_board_daily.lo_2y         is '近 489 场后复权最低价（取 low × 因子）';
comment on column analytics.first_board_daily.pos_2y       is '两年区间位置% = (现价 − 两年最低) ÷ (两年最高 − 两年最低) × 100。0 = 正趴在两年最低点，100 = 正顶在两年最高点。窗口含当日 ⇒ 天然落在 0~100。**不是涨幅也不是分位**（分位要数有多少天比它低，这一列只看区间里的位置）。窗口内真有的行不足 250 根 ⇒ NULL + missing=bars_2y_lt250（次新股没有两年可说）';
comment on column analytics.first_board_daily.bars_2y       is '那一发两年窗口里这只票**真有多少根日线**（停牌/上市晚的日子不占位）。它同时是 pos_2y 与 ma_stack 两列的可信度底：不满 60 根 ⇒ 均线排列判不了，不满 250 根 ⇒ 两年位置判不了';
comment on column analytics.first_board_daily.ma5           is '后复权收盘价的近 5 场均值。与 `config.indicator` 的「MA5（复权价）」同底，但**本模块 SQL 自算**（沿用倍量榜/突破信号那一族的理由：不依赖 d1 指标步当天有没有跑到基线）。不足 5 根 ⇒ NULL';
comment on column analytics.first_board_daily.ma10          is '后复权收盘价的近 10 场均值。同上，自算';
comment on column analytics.first_board_daily.ma20          is '后复权收盘价的近 20 场均值（界面按 A 股习惯叫月线）。同上，自算';
comment on column analytics.first_board_daily.ma60          is '后复权收盘价的近 60 场均值（季线）。同上，自算 —— 这一列是均线排列那一档的下界，缺它就只能判三档';
comment on column analytics.first_board_daily.ma_stack      is '均线排列档位短码（五档，阈值与判档顺序只住 `first_board_daily.py`）：bull_strong 四线多头（5>10>20>60）/ bull_short_long_flat 短多长未顺（5>10>20 但 60 还压在 20 上）/ tangled 纠缠（四线有交叉）/ bear_short_long_flat 短空长未顺 / bear_strong 四线空头（5<10<20<60）。中文标签由写侧 MA_STACK_NAMES 交，界面不许自己再翻一遍；不足 60 根 ⇒ NULL + missing=ma_bars_lt60';
