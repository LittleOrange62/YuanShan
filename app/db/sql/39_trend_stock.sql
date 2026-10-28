-- 「趋势股」的形状判据 + 两把新高尺子（来源口径见下，本机全部按本地日线算，可回算全史）
-- 口径出自同事那份《涨停分析、板块分析、趋势股》三页说明（docs/涨停分析-板块分析-趋势股——from洪杰.md）：
--   入选 = ① 所选周期内**每一个**交易日的收盘价都**严格**高于「截止这一天的 5 日收盘均线」；
--         ② **当天收盘没有涨停**；③ 当日涨幅算得出来；④ 能归到主板/创业板/科创板之一（北交所不纳入）。
--   他那边另有两把尺子这次一起落：
--   ③ 10/20 日新高 = 当日收盘价 **高于前 10（或前 20）根 K 线的最高价**，比较的是前 N 根的 high、**不含当天**；
--   ④ 放量两口径 = 当日成交量 > 前一交易日成交量、当日成交量 > 前 5 个交易日成交量的平均值（同样不含当日）。
--
-- ⚠️ 三条界面上最容易糊过去的区别，写在这里就是为了别糊：
--   · 「连续站上 MA5 的天数」（ma5_streak）与「周期内每天都站上」（shape5/shape10）不是一回事：
--     前者只看截止今天的这段，后者要求整段窗口一天都不许掉 —— 掉一天就整档不成立。
--   · 本表**不排除**"当天涨停"这件事进入 ma5_streak，但入选标记 selected 排除当天涨停。
--     板块卡片那种"固定看 5 天、可以包含当天涨停"的读法与本页不是一份名单，别互相顶。
--   · 新高比的是**前 N 根最高价**，不是"历史新高"，也不是库里那种含当日的自然日新高。
--
-- 判据与写入：app/compute/trend_stock.py（每日加工段步骤名 trendstock）。
-- 这是客观公开统计，不打分、不预测、不给买卖建议。

create table if not exists analytics.trend_stock_daily (
  trade_date        date        not null,
  security_code     text        not null,

  board             text,                       -- 主板 / 创业板 / 科创板（北交所不纳入，整行不写）
  close             numeric(16,6),
  pct_change        numeric(8,3),               -- (今收 - 昨收) ÷ 昨收，单位 %
  amount_yuan       numeric(20,4),              -- 当日成交额（元）
  float_mv_yuan     numeric(20,4),              -- 流通市值（元）= 当日有效流通股本 × 今收；股本按 change_date 分段取
  bars_used         int,                        -- 截止今天它一共有几根日线（不足 9 根时 MA5 形状判不了）

  -- ── 五日线趋势（形状，不是斜率）──
  above_ma5         boolean,                    -- 当日收盘**严格** > 当日 MA5（收盘简单平均，不复权，与来源口径一致）
  ma5_streak        int,                        -- 截止今天连续多少天收盘严格站上当日 MA5（掉下来那天记 0，不是 NULL）
  shape5            boolean,                    -- 近 5 个交易日**每一天**都严格站上当日 MA5
  shape10           boolean,                    -- 近 10 个交易日同上

  -- ── 两把新高尺子（前 N 根最高价，不含当日）──
  nh_10             boolean,                    -- 收盘 > 前 10 根的 max(high)
  nh_20             boolean,                    -- 收盘 > 前 20 根的 max(high)

  -- ── 放量两口径 ──
  vol_gt_prev       boolean,                    -- 当日量 > 前一交易日量
  vol_gt_vma5       boolean,                    -- 当日量 > 前 5 个交易日量的平均（不含当日）

  -- ── 当天涨停与周期内涨停次数（界面「近 N 日 X 板」那一枚标签用后者）──
  sealed            boolean,                    -- 收盘 >= 涨停价 - 0.005（涨停价读 market_data.stock_daily.limit_up）
  boards            int,                        -- 当天是本轮连板的第几板（没封住则 NULL）
  limit_ups_5       int,                        -- 近 5 个交易日里收盘涨停的天数
  limit_ups_10      int,                        -- 近 10 个交易日同上

  -- ── 入选 ──
  selected5         boolean not null default false,   -- shape5 × 当天不涨停 × 涨幅算得出来
  selected10        boolean not null default false,   -- shape10 × 同上
  reject            text,                           -- 没入选是哪一条差了（短码逗号分隔），入选则 NULL

  spec_version      text        not null,
  calculated_at     timestamptz not null default now(),

  primary key (security_code, trade_date, spec_version)
);

create index if not exists trend_stock_date_idx   on analytics.trend_stock_daily (trade_date, spec_version);
create index if not exists trend_stock_sel5_idx   on analytics.trend_stock_daily (trade_date, selected5, board, pct_change desc nulls last);
create index if not exists trend_stock_sel10_idx  on analytics.trend_stock_daily (trade_date, selected10, board, pct_change desc nulls last);

comment on table  analytics.trend_stock_daily is '趋势股形状榜（落库可回算）：入选 = 所选周期内每个交易日收盘都严格高于当日 MA5 × 当天收盘没涨停 × 涨幅算得出来 × 能归进主板/创业板/科创板（北交所不纳入）。同一行还带 10/20 日新高（收盘对前 N 根最高价，不含当日）与放量两口径。判据与写入 app/compute/trend_stock.py，每日加工段步骤名 trendstock。客观统计，不打分不预测不给买卖建议';
comment on column analytics.trend_stock_daily.shape5   is '近 5 个交易日**每一天**收盘都严格 > 当日 MA5（简单算术平均，不复权）。掉一天就整档不成立 —— 它和 ma5_streak 不是同一个问题：streak 问"最近连着几天"，shape 问"整个窗口一天都没掉过"';
comment on column analytics.trend_stock_daily.nh_10    is '当日收盘 > **前 10 根 K 线的最高价**（max(high)，不含当日）。与"历史新高"、与库里那种含当日的自然日新高都不是一个尺子';
comment on column analytics.trend_stock_daily.float_mv_yuan is '流通市值（元）= 当日有效流通股本 × 当日收盘。股本按 reference_data.share_capital 的 change_date 分段取"最后一个不晚于交易日的那段"，没有分段记录的票退回当前快照 float_share —— 退回时算的是"按当前股本折算"，是近似';
comment on column analytics.trend_stock_daily.reject   is '没入选是哪一条差了（短码，逗号分隔）：shape5_not/shape10_not/sealed_today/no_pct/board_unclassified/bars_lt_9。入选则 NULL —— 有这一列，界面才分得开"这只票形状不对"与"这台机器没算它"';

grant select, insert, update, delete on analytics.trend_stock_daily to ft_etl;
grant select on analytics.trend_stock_daily to ft_app, ft_reader;
