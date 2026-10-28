-- 「节点分析」：把打板那一天所处的**市场结构**编码成 0/1 的两条组合判据（来源口径见下）
-- 口径出自同事那份《涨停分析、板块分析、趋势股》三页说明（docs/涨停分析-板块分析-趋势股——from洪杰.md），
-- 本机的差别是：**全部按本地日线自己数连板**，不读东方财富涨停池，所以能回算全史、能对账、不依赖外部接口的存活。
--
-- 两条判据各自判断，同时命中写 `1, 2`：
--   节点 1 —— 三件事同时成立：① 这一天这只票是**首板**；② 上证指数当日跌幅 >= 1 个点（涨跌幅 <= -1.0）；
--            ③ 当日成交量 > 前一交易日成交量。它问的是「大盘砸下来那天，谁逆势封住了第一板」。
--   节点 2 —— 三件事同时成立：① 这一天是**首板或二板**（二板就是一进二）；
--            ② **前一交易日**全场最高连板高度 >= 6；③ 站在那个最高高度的那批票，**今天全部没有涨停**。
--            第③条包含两种情况，都会让节点 2 成立：断板（当天有成交但收盘没封住）与停牌
--            （上证已有当天这根日线，这只票却没有当天那行）。它问的是「高度被清空之后的第一天，谁敢接」。
--
-- 三板及以上**不看当天**，改看这只票**本轮连板的首板日与二板日**，那两天仍按上面两条判，命中的编号取并集
-- （见 run_first_date / run_second_date 两列与 hits 的算法）。
--
-- 最高连板的样本先剔三类（与来源口径一致，理由也一致）：
--   ① 当日 ST（本机用 reference_data.security_st_period 的**当日档期**，没有档期记录的才退回当前快照）；
--   ② 新股：名称是「N/C + 汉字」开头，**或**截止统计日这根票的日线不足 6 根；
--   ③ 名称表里没有名字的股票 —— 不能拿代码冒充名称，否则它会被当成非 ST 混进梯队。
-- 剔除是有读数的：excluded_count 记下剔了几只，st_fallback_count 记下有几只是退回当前快照判的 ST。
--
-- ⚠️ 这张表落的是**事实与判据**，不是买入信号，也不给方向。
--    「命中之后表现如何」本机没算过，界面上一句话都不许往那个方向说。
--
-- 判据与写入：app/compute/board_node.py（每日加工段步骤名 boardnode）。

create table if not exists analytics.board_node_daily (
  trade_date        date        not null,
  security_code     text        not null,

  -- ── 这只票这一天的板数（本地日线数出来的，收盘 >= 涨停价 - 0.005 记封住）──
  sealed            boolean     not null default false,
  boards            int,                        -- 本轮连板的第几板（收盘没封住则 NULL，不是 0）
  run_first_date    date,                       -- 本轮首板日（boards>=1 时才有）
  run_second_date   date,                       -- 本轮二板日（本轮真走到二板才有）
  bars_used         int,                        -- 截止这一天它一共有几根日线（不足 6 根 = 新股，不进梯队）

  -- ── 大盘与环境 ──
  sh_pct            numeric(8,3),               -- 上证指数当日涨跌幅 %（读不到则 NULL，节点 1 随之不成立）
  vol_vs_prev       numeric(8,3),               -- 当日成交量 ÷ 前一交易日成交量
  prev_volume_ok    boolean,                    -- 前一交易日有没有量（停牌/首根日线时不可判）

  -- ── 前一交易日的梯队状态（节点 2 的第②③条）──
  prev_max_boards   int,                        -- 前一交易日全场最高连板（剔除后的口径）
  prev_top_count    int,                        -- 站在那个最高高度的只数
  prev_top_sealed   int,                        -- 其中今天仍封住的只数（节点 2 要求它 = 0）
  prev_top_halted   int,                        -- 其中今天没有日线行的只数（按口径算「没涨停」，与断板同向）
  prev_top_broke    int,                        -- 其中今天有成交但收盘没封住的只数

  -- ── 判据结果 ──
  node1             boolean,                    -- 只在"这一天是首板"时才可能为 true
  node2             boolean,                    -- 只在"这一天是首板或二板"时才可能为 true
  hits              text,                       -- 页面上那一格：'1' / '2' / '1, 2' / NULL（NULL 显示成 —）
  hit_reason        text,                       -- 不成立时是"哪一条差了"（逗号分隔的短码），成立则 NULL

  -- ── 全场诊断（读侧用来判断"这一场能不能读"）──
  sh_row_missing    boolean not null default false,   -- 上证当天没有这根日线 ⇒ 整场判据不可信
  st_fallback_count int,                            -- 这一场有几只票的 ST 是按当前快照判的（无当日档期记录）
  spec_version      text        not null,
  calculated_at     timestamptz not null default now(),

  primary key (security_code, trade_date, spec_version)
);

create index if not exists board_node_date_idx   on analytics.board_node_daily (trade_date, spec_version);
create index if not exists board_node_code_idx     on analytics.board_node_daily (security_code, trade_date);

comment on table  analytics.board_node_daily is '节点分析（组合判据，落库可回算）：节点1=当日首板 × 上证当日跌幅>=1个点 × 当日量大于前一交易日；节点2=当日首板/二板 × 前一交易日全场最高连板>=6 × 那批最高板今天全部没涨停（断板与停牌都算）。三板及以上回看本轮连板的首板日与二板日取并集。连板全部由本地日线数出（收盘>=涨停价-0.005），不读东方财富涨停池。判据与写入 app/compute/board_node.py，每日加工段步骤名 boardnode。这是客观结构描述，不是买入信号';
comment on column analytics.board_node_daily.boards           is '本轮连板的第几板：从当天往前数连续封住涨停的根数（收盘 >= limit_up - 0.005 记封住）。除权形态那根（最低价涨破涨停价 / 最高价跌破跌停价）当成不可信，**直接截断连板**，不参与数。收盘没封住则 NULL —— 不是 0 板';
comment on column analytics.board_node_daily.sh_pct           is '上证指数（market_data.index_daily 的 000001.SH）当日涨跌幅 %。节点 1 要求 <= -1.0。读不到留 NULL，节点 1 随之不成立并把差的那条写进 hit_reason —— 不许把"没读到"当成"大盘没跌"';
comment on column analytics.board_node_daily.prev_top_sealed  is '前一交易日那个最高连板高度上的票，今天仍封住的只数。节点 2 要求它等于 0（全场高度被清空）。注意分母 prev_top_count 是剔除 ST/新股/无名之后的';
comment on column analytics.board_node_daily.prev_top_halted  is '其中今天**没有日线行**的只数（上证已有当天这根 K 线才算停牌，否则整场不可判）。按来源口径它与断板同向：都算"当天没有涨停"，都会让节点 2 成立';
comment on column analytics.board_node_daily.hits             is '页面上那一格的值：''1'' / ''2'' / ''1, 2''，NULL 显示成「—」。三板及以上这一格来自本轮首板日与二板日的并集，不是当天那两条';
comment on column analytics.board_node_daily.hit_reason       is '没命中时是哪一条差了（短码，逗号分隔）：sh_pct_missing/sh_pct_not_down/first_board_only/not_1st_or_2nd/prev_top_below_6/no_prev_top/prev_top_still_sealed/vol_not_up/no_prev_volume/bad_adj_bar/run_window_broken。成立则 NULL。有这一列是因为"没命中"和"判不了"必须在界面上分得开';
comment on column analytics.board_node_daily.sh_row_missing   is '这一场上证没有当天那根日线 ⇒ 节点 1 与"停牌"判定全部不可信。读侧要把这一场整场标成"判不了"，而不是逐票说"没命中"';
comment on column analytics.board_node_daily.st_fallback_count is '这一场梯队样本里，有几只票的 ST 身份没有当日档期记录、退回当前快照判的。这是本机与来源口径的唯一分岔处（来源直接用今天的名称），所以要把量报出来';

grant select, insert, update, delete on analytics.board_node_daily to ft_etl;
grant select on analytics.board_node_daily to ft_app, ft_reader;
