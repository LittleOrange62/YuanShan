-- -*- coding: utf-8 -*-
-- 44_startup_spike_days.sql
-- 「启动前 N 场爆量／突然拉高 × 量增／量缩（日线代理）」：用户 10-18 追加的原话——
--   「分析前 30 个交易日，看看有多少爆量，或者突然拉高，这种情况分别出现在量增的天数还是量减的天数」。
-- 分钟线那一层用户已明确取消，所以这一屏只按日线代理判据（一根交易日一个数），
-- 屏上（读侧 caliber/caveats）必须写明这是日线代理、不冒充分时图上的爆量/急拉。
--
-- 为什么要联合计数（这一屏的核心）：只报「倍量 8 天 / 量增 17 天」两个独立天数答不了
-- 「倍量落在哪一边」——所以每个倍量/准倍量/缩量日再按当日涨跌分一次堆：
--   dbl_up_days + dbl_down_days = dbl_days（这条自证判据由写侧 verify 与测试两边盯着）。
--
-- 与 42/43/26 三张表的分工（别在后面被人合并掉）：
--   · 26（sv-2）回答「启动前量是怎么放的」，母体按「回头看 ≥4 板」筛过；
--   · 42（fb-2）回答「启动前什么形状预示走到几板」，母体＝每一个首板（不筛结果）；
--   · 43（sqt-1）回答「启动前那 N 场四维象限名次怎么挪」；
--   · 本表回答的是「倍量/急拉这些天，落在量增那一边还是量减那一边」，母体两档来源：
--     `anyfirst`＝fb-2 全部首板（不筛结果，能回答预示那一问）＋ sv-2 四类（对照用）。
--
-- 判据尺子（与既有实现同一条，不许再写第三份）：
--   · vr 尺子（up/down/flat/still/dbl/near/calm）复用 `app/compute/startup_volume.py` 的 `classify()`
--     与它的常量 `DBL=2.0`、`NEAR_DBL`、`CALM=0.5`、`STILL_LO/HI=0.95/1.05`——写侧 import，不抄数。
--     up＝vr>1、down＝vr<1、flat＝四位收口后精确 1.0000、still＝0.95~1.05（与 sv-2 完全同尺）。
--   · 当日涨跌一律**后复权重算**：close × coalesce(adjustment_factor,1) 相对前一根同式
--     （同 `duanxian/day_returns.py` 的式子），**不读 `stock_daily.pct_change`**（除权日错）。
--   · 突然拉高＝当日后复权收益 >= JUMP_PCT（写侧模块常量 0.07，可 CLI --jump-pct 覆盖）；
--     阈值下闭：正好等于 JUMP_PCT 算拉高。jump_dbl_days＝同日还满足倍量 ⇒
--     「突然拉高落在倍量还是缩量」那一问读的就是 jump_dbl/jump_near/jump_calm 这三个数。
--   · 涨停判定容差引用 `app/compute/limit_prices.py::LIMIT_EPS`（全项目唯一，半分），不新写 0.01。
--   · vr 只读 `analytics.volume_ratio_daily`（vr-1），这里不再除一遍量。
--
-- 窗口口径（三条，和 43 同一族）：
--   · 窗口＝启动日**之前** window_days 个该票自己的有效交易日（不含启动日当天）；
--     取数多取一场只当首根收益的基价。win_from＝窗口最早一场，win_to＝T-1。
--   · 缺的格子不参与、不补 0：vr 为 NULL 的那场不进 vr 尺子；ret 为 NULL 的那场不进涨跌判定。
--     真数据里倍量日必有前一根（vr 行存在即证明前一根存在），所以 dbl_up+dbl_down=dbl 在库里应恒成立，
--     verify 数「例外」并要求为 0。
--   · 窗口不满（bars<window_days）时 jump 那一族整组交 NULL 不是 0：0 的意思是「凑满了 N 场、
--     一次都没拉高」，不满窗没有资格说这句话。jump_days=0（真没有）与 NULL（窗口不满）是两件事。
--
-- 结果四列一律从 fb-2 同 (票, 启动日) join：outcome_len/outcome_seg_complete/outcome_r5/outcome_ex5。
-- 北交所没有对应分类指数 ⇒ outcome_ex5 为 NULL，读侧从超额分母剔掉并点名条数，不拿 0 当「没赚」。
-- 主键不带 window_days ⇒ 换 --window 重灌是**覆盖同一批行**（window_days 列说这一行按几场判的）；
-- 改判据（尺子/收益式/jump 族的 NULL 纪律）要换 spec_version，旧批留着对账。

create table if not exists analytics.startup_spike_days (
  security_code     text          not null,
  launch_date       date          not null,       -- 启动日（anyfirst＝首板日；sv-2 四档＝段首日/触发日）
  cohort            text          not null,       -- anyfirst / board4 / board4_45 / board4_or_gain45 / jump40
  spec_version      text          not null,
  name              text,
  board             text,
  window_days       smallint      not null,       -- 本次跑的窗口（10/20/30），是判据的一部分
  bars              smallint      not null,       -- 窗口实际拿到几场（<window_days 就是不满窗）
  win_from          date,                         -- 窗口最早一场
  win_to            date,                         -- 紧邻启动日那一场（T-1）
  up_days           smallint,                     -- vr 尺子四档（与 sv-2 classify 同一条）
  down_days         smallint,
  flat_days         smallint,
  still_days        smallint,
  dbl_days          smallint,                     -- vr >= DBL（2.0）倍量
  near_dbl_days     smallint,                     -- NEAR_DBL <= vr < DBL 准倍量
  calm_days         smallint,                     -- vr <= CALM（0.5）缩量
  dbl_up_days       smallint,                     -- 联合计数：倍量日里当日涨（后复权收益 >0）几天
  dbl_down_days     smallint,                     -- 倍量日里当日跌（<=0，平盘算这头）几天
  near_up_days      smallint,
  near_down_days    smallint,
  calm_up_days      smallint,
  calm_down_days    smallint,
  jump_days         smallint,                     -- 突然拉高：当日后复权收益 >= JUMP_PCT（下闭，==阈值算）
  jump_up_days      smallint,
  jump_down_days    smallint,
  jump_dbl_days     smallint,                     -- 同日还满足倍量 ⇒ 「急拉落在哪一边」的那三个数
  jump_near_days    smallint,
  jump_calm_days    smallint,
  jump_limit_days   smallint,                     -- 同日还封住涨停（close >= limit_up - LIMIT_EPS）
  limit_days        smallint,                     -- 窗口内涨停场数（同一容差，引用 limit_prices 那个常量）
  first_dbl_gap     smallint,                     -- 窗口内第一次倍量距 T-1 有几场（0＝就发生在 T-1；没有＝NULL）
  first_jump_gap    smallint,                     -- 第一次急拉距 T-1 有几场；窗口不满＝NULL
  outcome_len       int,                          -- fb-2 的 seg_len（走到几板，结果变量）
  outcome_seg_complete bool,                       -- fb-2：段走完了没有（false＝还在走，段长是下界）
  outcome_r5        numeric(8,3),                 -- fb-2 首板次日开盘进场后第 5 场绝对收益（百分数）
  outcome_ex5       numeric(8,3),                 -- 同一场相对分类指数的超额；北交所无基准＝NULL
  calculated_at     timestamptz   not null default now(),
  primary key (security_code, launch_date, cohort, window_days, spec_version)
);

comment on table analytics.startup_spike_days is
  '启动前 N 场爆量/突然拉高 × 量增/量缩（日线代理，一根交易日一个数，不冒充分时图）：'
  '联合计数 dbl_up_days + dbl_down_days = dbl_days 是自证判据；vr 尺子与 app/compute/startup_volume.py '
  '的 classify 同一条（up=vr>1、down=vr<1、flat=四位收口精确 1.0000、still=0.95~1.05）；'
  '当日涨跌一律后复权重算，不读 stock_daily.pct_change；窗口不满时 jump 那一族整组 NULL 不是 0';

comment on column analytics.startup_spike_days.security_code is '证券代码（带市场后缀），与母体同一份';
comment on column analytics.startup_spike_days.launch_date    is '启动日：anyfirst＝首板日（fb-2 母体当天可构造），sv-2 四档＝连板段第一天/四日窗口触发日。窗口不含这一天';
comment on column analytics.startup_spike_days.cohort         is '母体档位：anyfirst（全部首板，不筛结果——能回答预示那一问）/ board4 / board4_45 / board4_or_gain45 / jump40（后四档＝sv-2 那批按结果筛过的对照）';
comment on column analytics.startup_spike_days.spec_version   is '判据版本 ssd-1：改尺子、改收益口径、改 jump 族的 NULL 纪律都要换号并回算，旧批留着对账';
comment on column analytics.startup_spike_days.name           is '票名（来自母体表，缺则 NULL）';
comment on column analytics.startup_spike_days.board          is '主板 / 创业板 / 科创板 / 北交所（最终来源 reference_data.security）';
comment on column analytics.startup_spike_days.window_days    is '本次跑的窗口场数（10/20/30）。它与 bars 一起决定这一行说的是哪一段：bars<window_days 就是不满窗';
comment on column analytics.startup_spike_days.bars           is '窗口里实际拿到几场（该票启动日之前、最多 window_days 个交易日）。不满窗是覆盖事实（上市太近/停牌），读侧要数出来';
comment on column analytics.startup_spike_days.win_from       is '窗口最早那一场（该票真实交易日，可能早于 window_days 个场次之前的日历位置）';
comment on column analytics.startup_spike_days.win_to         is '紧邻启动日那一场＝T-1。启动日当天不进窗口（当天往往已涨停，读数被启动本身污染）';
comment on column analytics.startup_spike_days.up_days        is 'vr 尺子：窗口里 vr>1 的天数（量增）。与 down/flat/still 同一把尺子，复用 sv-2 的 classify，缺 vr 的那场不参与';
comment on column analytics.startup_spike_days.down_days      is 'vr 尺子：vr<1 的天数（量减）';
comment on column analytics.startup_spike_days.flat_days      is 'vr 尺子：四位收口后精确等于 1.0000 的天数（不是「没变」，「量基本没变」看 still_days）';
comment on column analytics.startup_spike_days.still_days     is 'vr 尺子：0.95~1.05 的天数（与 up/down 有重叠，那是 sv-2 尺子的原样，不是数错）';
comment on column analytics.startup_spike_days.dbl_days       is '爆量的日线代理：vr >= DBL（2.0，引用 startup_volume.py 常量）的天数';
comment on column analytics.startup_spike_days.near_dbl_days  is '准爆量的日线代理：NEAR_DBL <= vr < DBL 的天数（等号归下界，同一档只被数一次）';
comment on column analytics.startup_spike_days.calm_days      is '缩量：vr <= CALM（0.5，引用 startup_volume.py 常量）的天数';
comment on column analytics.startup_spike_days.dbl_up_days    is '联合计数（这一屏的核心）：倍量日里当日涨（后复权收益 >0）的天数。恒有 dbl_up_days + dbl_down_days = dbl_days（写侧 verify 数例外并要求为 0）';
comment on column analytics.startup_spike_days.dbl_down_days  is '倍量日里当日跌（后复权收益 <=0，平盘算这一头）的天数。它与 dbl_up_days 相加必须等于 dbl_days，这条对不上就是写坏了';
comment on column analytics.startup_spike_days.near_up_days   is '准倍量日里当日涨的天数（near_up + near_down = near_dbl_days）';
comment on column analytics.startup_spike_days.near_down_days is '准倍量日里当日跌（<=0）的天数';
comment on column analytics.startup_spike_days.calm_up_days   is '缩量日里当日涨的天数（calm_up + calm_down = calm_days）';
comment on column analytics.startup_spike_days.calm_down_days is '缩量日里当日跌（<=0）的天数';
comment on column analytics.startup_spike_days.jump_days      is '突然拉高的日线代理：当日后复权收益 >= JUMP_PCT（默认 0.07，阈值下闭，正好等于也算）的天数。0＝凑满窗口一次都没拉高；NULL＝窗口不满（bars<window_days），没资格说 0——两件事';
comment on column analytics.startup_spike_days.jump_up_days   is '急拉日里当日涨（收益 >0）的天数；jump_up + jump_down = jump_days';
comment on column analytics.startup_spike_days.jump_down_days is '急拉日里当日跌（<=0）的天数（收益 >= 阈值后这一列正常就该是 0）';
comment on column analytics.startup_spike_days.jump_dbl_days  is '同日还满足倍量的急拉天数。「突然拉高落在倍量还是缩量」这一问读 jump_dbl/jump_near/jump_calm 三个数，三者相加 <= jump_days（差额是既非倍量也非缩量的普通量日子）';
comment on column analytics.startup_spike_days.jump_near_days is '同日为准倍量（NEAR_DBL<=vr<DBL）的急拉天数';
comment on column analytics.startup_spike_days.jump_calm_days is '同日还缩量（vr<=CALM）的急拉天数——拉高配缩量是「没量配合的拉高」的日线代理读数';
comment on column analytics.startup_spike_days.jump_limit_days is '急拉且当日封住涨停（close >= limit_up - LIMIT_EPS，容差引用 app/compute/limit_prices.py 的全项目唯一常量）的天数';
comment on column analytics.startup_spike_days.limit_days     is '窗口内涨停场数（同一容差判据）。缺 limit_up 的那些天不参与、不补 0';
comment on column analytics.startup_spike_days.first_dbl_gap  is '窗口内第一次倍量距 T-1 有几场：0＝就发生在启动日前一天；NULL＝整个窗口没有倍量（不是 0）';
comment on column analytics.startup_spike_days.first_jump_gap is '第一次急拉距 T-1 有几场；没有急拉＝NULL；窗口不满＝NULL（jump 族整组随不满窗交 NULL）';
comment on column analytics.startup_spike_days.outcome_len    is '同一个 (security_code, launch_date) 在 analytics.first_board_shape（fb-2）里的 seg_len，即「后来走到几板」。结果变量不是筛子；anyfirst 档恒有值（就是该行自己的段长），jump40 的触发日多半不是段首日＝NULL';
comment on column analytics.startup_spike_days.outcome_seg_complete is 'fb-2 的 seg_complete：false＝段还在走，outcome_len 是下界（读侧不许把在走的段当已走完）';
comment on column analytics.startup_spike_days.outcome_r5     is 'fb-2：首板次日开盘进场后第 5 场收盘的绝对收益（后复权，百分数）。NULL＝凑不满场次或 fb-2 没有那一行，0 才是真平盘';
comment on column analytics.startup_spike_days.outcome_ex5    is 'fb-2：同一场相对对应分类指数的超额（基准起点＝进场当日指数开盘）。北交所没有对应指数＝NULL，读侧从超额分母剔掉并点名条数，不拿 0 当「没赚」';
comment on column analytics.startup_spike_days.calculated_at  is '这一行最后一次重算的时刻（默认 now()，重算必须刷新它，读侧靠这句判断「这批数多久没动过」）';

create index if not exists startup_spike_days_date_idx
  on analytics.startup_spike_days (spec_version, launch_date desc);
create index if not exists startup_spike_days_code_idx
  on analytics.startup_spike_days (security_code, launch_date desc);

grant select, insert, update, delete on analytics.startup_spike_days to ft_etl, ft_app;
grant select                          on analytics.startup_spike_days to ft_reader;

-- 10-18 修正：window_days 进了主键。之前同一 (票·启动日·cohort) 换窗口深度重算会**盖掉**上一档（实测跑完 --window 20 后 --window 30 那批整批消失、界面切窗口只剩一个深度有数）。
-- 窗口深度是行与行的区分维度，和 spec_version 同一类教训 —— 主键少了它就等于一表只留最后一次口径。
