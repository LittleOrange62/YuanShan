-- -*- coding: utf-8 -*-
-- 62_alloc_stock_daily.sql
-- 「分配者视角」下沉到个股：每只股票在每个交易日的位置分 / 想象力分 / 拥挤度温度 / 象限。
-- 判据与权重只在 `app/compute/alloc_stock.py` 一处（沿用 23_allocator.sql 那四味，搬到个股尺度）。
--
-- 为什么必须落库而不是接口现算（同 23 的理由，个股尺度更甚）：
-- 回答的是「**那一天**它是收费站还是故事区」，现算意味着每次请求重跑 40 场窗口 × 5,600 只；
-- 而且权重一改，历史上所有票的含义会被悄悄改写。`spec_version` 进主键 = 当时那一版永远原样在。
--
-- ⚠️ 一条被实测推翻的设想（2026-11-05，写这张表之前先量的，不改设计就会一路错到底）：
--   原本想「份额分母 = 所属行业板（一只票一个行业）」。**不成立**：
--   `sector_type='行业'` 是个混装桶 —— 一只票平均挂 **7.76** 个、最多 **12** 个，
--   里面同时有 `制造/信息/可选`（大类风格）、`商贸代理`（真行业）、`浙江/江苏`（地区）、
--   以及没名字的 `880595`；5,635 只有归属的票里**只有 58 只恰好一个**。
--   ⇒ 份额与市值占比的分母改用**全市场**（每只票唯一、可回算），板块那一维只做**继承列**（见②）。
--
-- 三条纪律（这一版最容易骗人的地方，比板块层更严重）：
--   ① 算不出就是 NULL，不是 0。缺分红的票 `div_yield` 是 NULL（近一年有除息的 4,104 只 / 在市 5,586 只
--      = 74%，其余是真不分红、不是没采到）、没进两融名单的 `margin_ratio` 是 NULL（4,118 只 = 74%）。
--      NULL 从**分母**里剔掉、不参与排名；0 分会参与排名 —— "没数据"被排成"很差"是这套分数最常见的读错。
--   ② **继承值必须在列名上分得开**：`board_*` 一律来自这只票所属的那些板块（板块层算好的读数），
--      回答的是"这块地气热不热"，不是"这只票热"。多对多的聚合一次不许只交一个数：
--      交**中位**（`board_*_med`）与**最热那一块**（`board_*_hot`）两个口径 + 参与聚合的板块数（`boards_used`），
--      页面两个都摆、都标"继承"。把它们合成一个"个股的板块分"就是假数。
--   ③ `bars_used` 与 `boards_used` 一起交：窗口内不满 40 根（次新/长期停牌）的票不许与满窗票并排排名，
--      没有板块归属（实测 1,732 只 = 31%）的票继承列全 NULL —— 这两件事界面上必须点名，不能默默少一列。
--
-- 与 23 的关系：`board_*` 那些继承值读的就是 `analytics.sector_allocator` 同一场的行（按当日在册成员关系 join），
-- 页面拿它做"个股 vs 所在板块"的对照，**不在前端重算一遍板块判据**。

create table if not exists analytics.alloc_stock_daily (
  security_code    text           not null,
  trade_date       date           not null,
  spec_version     text           not null,

  name             text,                        -- 当日名称（字典 join；字典没登记的票首字母本来就搜不到，页面另报点数）
  is_st            boolean,                     -- ST 影响涨跌停档位，也影响"这是不是同一把尺"
  bars_used        integer,                     -- 窗口内真实有几根日线（不满窗 ⇒ 页面报名字，不与满窗票并排）

  -- ---- 位置分（收费站分）：四味全是**个股自己**的，分母=全市场（见上面那条被推翻的设想）----
  div_yield        numeric(10,6),               -- TTM 现金分红 ÷ 当日不复权收盘（送转/配股不进分子）
  amount_stability numeric(10,4),               -- 1 − 日成交额的变异系数（窗口内）：长期稳定有现金流经过 = 像收费站
  share_mkt_med    numeric(10,8),               -- 本票成交额 ÷ 当日全市场成交额，窗口内中位（"份额"，不是净流入）
  mv_mkt_share     numeric(10,8),               -- 流通市值 ÷ 当日全市场流通市值合计
  toll_score       numeric(10,2),               -- 35/25/20/20 横截面分位加权（缺项从分母剔），0~100
  toll_pctile      numeric(10,4),               -- 位置分在全市场个股里的分位（象限分界用当日中位数，不用这条）

  -- ---- 想象力分：两味个股 + 两味继承（列名前缀分开）----
  mom_pctile       numeric(10,4),               -- 个股自身：窗口内涨幅的横截面分位
  turnover_pctile  numeric(10,4),               -- 个股自身：换手率的横截面分位（"注意力"代理）
  board_breadth_med  numeric(10,4),             -- 继承：所属板块广度分位的**中位**
  board_heat_hot     numeric(10,4),             -- 继承：所属板块里**最热那一块**的份额分位
  boards_used      integer,                     -- 上面这两个继承值各是几个板块聚合出来的（0 ⇒ 这只票没有在册归属）
  imagine_score    numeric(10,2),               -- 40/25/20/15 加权
  crowding_temp    numeric(10,2),               -- 45/25/20/10：**100 = 所有人终于达成共识**，不是"该卖了"

  -- ---- 拥挤度温度的输入（同样标清自身/继承）----
  inflow_speed     numeric(10,4),               -- 个股自身：近 5 场成交额 ÷ 窗口内中位
  margin_ratio     numeric(10,6),               -- 个股自身：融资余额 ÷ 流通市值（不在两融名单 = NULL，不是 0）
  board_member_delta_med integer,               -- 继承：所属板块成员数与约一年前差值的中位

  -- ---- 对照用（不参与合分）：这只票站在哪些板块上、这些板块自己几分 ----
  board_count      integer,                     -- 当日在册归属板块数（实测行业型平均 7.76、含概念更多）
  board_toll_med   numeric(10,2),               -- 所属板块位置分中位
  board_toll_hot   numeric(10,2),               -- 所属板块里位置分最高的那一块
  board_top_name   text,                        -- 那一块的名字（页面"个股 vs 板块"那一列的对照锚点）

  quadrant         text,                        -- toll / consensus / story / nowhere（当日个股横截面中位分界）
  missing          text,                        -- 哪几味算不出（div,stability,share,mv,mom,turnover,breadth,heat,margin,delta,board）。NULL 不参与合分，但必须看得见为什么
  calculated_at    timestamptz    not null default now(),

  primary key (security_code, trade_date, spec_version)
);

comment on table  analytics.alloc_stock_daily              is '分配者视角·个股层：每只票每个交易日的位置/想象/拥挤/象限（判据一处 app/compute/alloc_stock.py）';
comment on column analytics.alloc_stock_daily.spec_version is '判据版本（allocstock-1），在主键里：调阈值/权重不改写历史那批行';
comment on column analytics.alloc_stock_daily.bars_used    is '窗口内真实日线根数：不满窗（次新/长期停牌）不许与满窗票并排排名，界面按这一列报名字';
comment on column analytics.alloc_stock_daily.share_mkt_med is '成交额份额（本票 ÷ 全市场当日）窗口内中位。是"份额"不是"净流入" —— 本库没有个股级资金流历史（只有新浪一条，一天不采就永久缺）';
comment on column analytics.alloc_stock_daily.div_yield    is 'TTM 现金股息率。可按历史日期回算（实测 2021-08-03 算得出 3,001 只、2026-11-04 3,737 只）；覆盖 74%，缺的是真不分红';
comment on column analytics.alloc_stock_daily.margin_ratio is '融资余额 ÷ 流通市值：只有两融标的有（实测 4,118 只 = 74%）⇒ 不在名单是 NULL，绝不当 0 参与排名';
comment on column analytics.alloc_stock_daily.board_breadth_med is '继承值：所属板块广度分位的中位，不是这只票自己的读数 —— 广度在个股层没有对应物';
comment on column analytics.alloc_stock_daily.board_heat_hot   is '继承值：所属板块里最热那一块的份额分位。与中位一起交：一只票平均挂 7.76 个"行业型"板块，合成一个数就是假数';
comment on column analytics.alloc_stock_daily.boards_used      is '参与上面那几个继承值聚合的板块数（实测 1,732 只 = 31% 一个都没有 ⇒ 继承列全 NULL，界面要念"这只票没有在册归属"）';
comment on column analytics.alloc_stock_daily.board_toll_med   is '对照用：所属板块位置分的中位（不参与任何合分）—— 用来回答"是票强还是它那块地强"';
comment on column analytics.alloc_stock_daily.crowding_temp    is '0~100 拥挤度温度。**100 = 所有人终于达成共识**，不是卖出信号；什么时候退是另一件事，本系统不给动作';
comment on column analytics.alloc_stock_daily.quadrant        is 'x=想象力分 y=位置分，分界线取**当日全市场个股的中位数**（不写死 50，否则分布一动象限全体搬家）';
comment on column analytics.alloc_stock_daily.missing         is '这一行哪几味算不出（如 div,margin,board）。NULL 不参与合分，但要在界面上看得见它为什么是 NULL';

create index if not exists alloc_stock_day_idx
  on analytics.alloc_stock_daily (trade_date, spec_version, toll_score desc);
create index if not exists alloc_stock_board_idx
  on analytics.alloc_stock_daily (trade_date, spec_version, board_toll_med desc);

grant select on analytics.alloc_stock_daily to public;
