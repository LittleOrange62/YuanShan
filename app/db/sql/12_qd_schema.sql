-- =============================================================================
-- 12 · qd schema：quantdash 算法口径的双轨落库（第二轨，不替换 analytics）
--
-- 背景（用户裁定 2026-09-24）：此前判定"quantdash 五项功能 YuanShan 已有实现、
-- 不必集成"，被用户否掉。原话："实际上我的逻辑跟它的逻辑是不一样的，我需要它的
-- 逻辑也能够展示出来……甚至 pg 数据库新建一张表或者一个模式，标注来源于
-- quantdash 的计算方法。" 所以这里是**双轨并存**：自家口径继续走 analytics.*，
-- quantdash 口径逐条照搬其阈值/公式落到 qd.*，前端并排显示让用户看差异。
--
-- 契约全文：D:\finance\数据加工工作指南\09-quantdash逻辑规格.md
--
-- 法律边界：来源仓库 D:\finance\quantdash-ai-stock-main 是 **PolyForm
-- Noncommercial 1.0.0**，不是 Apache-2.0。本文件与配套计算脚本里
-- **零行来源码**：只有阈值、公式、字段语义的重述。每张表的 comment 都写明这点。
--
-- 三条写库纪律（09 §1、§2）：
--   1. 口径照搬、缺陷不搬。09 §7 列了 42 处 quantdash 的伪造/兜底（随机数、
--      常量 5100 缩放、哈希编造封板时间、失败写空列表、缺数据当低风险），
--      一律不复现：真算不出来写 NULL，并在该行 deviation 记一条 code。
--   2. NULL ≠ 0。比率字段一律 `_pct` 后缀、存百分数；分母写进字段名。
--   3. spec_version 与 source 由脚本从 config.setting 传入，**这里不给默认值**
--      —— 给了默认值就等于允许"忘了传"这种静默口径漂移。
--
-- source 列的取值约定（09 §2 只说"混了实测数据写 +local"，这里定死才不会被写成常量）：
--   'quantdash-logic'       该行全部输入都来自 quantdash 也有的那类数据（日线推涨停池、指数额）
--   'quantdash-logic+local' 该行至少用了 quantdash **没有**的本地独有输入
--                           （板块级 up_count/down_count、is_turnover_board、sector_daily 家数）
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS qd;

-- 授权放在建表之后（文件末尾），这里先确保 usage
GRANT USAGE ON SCHEMA qd TO ft_reader, ft_app, ft_etl;


-- =============================================================================
-- 一、qd.sentiment_cycle · 情绪周期（每日一行）
-- =============================================================================
-- 一个必须如实呈现的事实：quantdash **自己有三套互相矛盾的阶段阈值**
-- （前端总览版 / MCP 版 / 飞书机器人版）。这不是我们要修的 bug，是它的既成事实
-- —— 它的页面和它的 AI 看到的"当前阶段"可能不一样。所以三套都算、存三列。

CREATE TABLE IF NOT EXISTS qd.sentiment_cycle (
    trade_date                  date        NOT NULL,
    spec_version                text        NOT NULL,
    calculated_at               timestamptz NOT NULL DEFAULT now(),
    source                      text        NOT NULL
                                CHECK (source IN ('quantdash-logic','quantdash-logic+local')),
    deviation                   jsonb       NOT NULL DEFAULT '[]'::jsonb,

    -- 三套阶段（if-elif 自上而下短路，顺序不可换）
    stage_ui                    text CHECK (stage_ui   IN ('冰点','试错','主升','分歧','修复','退潮')),
    stage_mcp                   text CHECK (stage_mcp  IN ('冰点','试错','主升','分歧','修复','退潮')),
    stage_feishu                text CHECK (stage_feishu IN ('分歧震荡','冰点','主升','修复','退潮','试错')),
    reasons                     jsonb NOT NULL DEFAULT '[]'::jsonb,
    confidence                  smallint CHECK (confidence BETWEEN 0 AND 100),

    -- 判据输入（三套 stage 都从这几列读，便于逐条复算）
    risk_level                  text   CHECK (risk_level IN ('high','medium','low')),
    volume_state                text   CHECK (volume_state IN ('持续放量','缩量再缩量','放量滞涨','存量震荡')),
    sentiment_value             numeric(6,2),
    board_height                smallint,
    leader_board_count          smallint,
    high_board_count            integer,
    limit_up_count              integer,
    limit_down_count            integer,
    first_board_ratio_pct       numeric(6,1),
    broken_rate_sealed_pct      numeric(6,1),
    broken_repair_rate_lag1_pct numeric(6,1),
    success_rate_pct            numeric(6,2),
    a_kill_count_lag1           integer,
    leader_next_close_pct       numeric(6,2),

    -- 其余逐指标（09 §4.1 表）
    first_board_count           integer,
    high_board_ratio_pct        numeric(6,1),
    relay_count                 integer,
    avg_board_gain              numeric(6,3),
    premium_score               numeric(6,2),
    broken_rate_touched_pct     numeric(6,1),
    touched_count               integer,
    broken_touched_count        integer,
    big_face_count_lag1         integer,
    big_face_repair_rate_lag1_pct numeric(6,1),
    weak_count_lag1             integer,
    rise_count                  integer,
    fall_count                  integer,
    flat_count                  integer,
    scope                       text,
    amount_yi                   integer,
    amount_change_pct           numeric(8,2),

    PRIMARY KEY (trade_date, spec_version)
);

COMMENT ON TABLE qd.sentiment_cycle IS
  '逻辑来源：quantdash-ai-stock-main（PolyForm Noncommercial），仅重实现其算法口径，'
  '未复制源码。规格见 数据加工工作指南/09-quantdash逻辑规格.md（§9 是实现回执与勘误）。'
  '情绪周期每日一行。与 analytics.market_breadth 是两套口径，并排展示不互相替换。'
  '⚠️ 阶段分布严重偏斜是照搬它优先级链的必然结果、不是本表算错：退潮排第一且条件含'
  '"修复率<20"（修复率低于 20 是常态），冰点虽列第二但要求最高板<=2 且涨停<20，'
  '永远先被退潮截走 —— 实测 1169 天：退潮 936 / 修复 125 / 试错 88 / 主升 19 / 分歧 1 / '
  '冰点 0 次（退潮占 80%）；飞书版只出两档'
  '（退潮 / 分歧震荡），等于只区分"防守 / 震荡"。每行 deviation 带 D-43 说明，'
  '前端按这个分布设计，不要以为六档会均匀出现。';

COMMENT ON COLUMN qd.sentiment_cycle.reasons IS
  '形状：[{"stage":"stage_ui"|"stage_mcp"|"stage_feishu","hit_rule":<该套判据链里命中的优先级序号>,'
  '"reason":"中文"}]，**三个 stage 各一条**（不是最多 3 条不分口径的理由）。'
  '三套 stage 一致时看不出差别，不一致的日子前端要把理由贴到对应那颗胶囊上。'
  'quantdash 兜底"分歧"时不给理由（D-9），此处照契约给一条"未命中任何阶段条件，兜底为分歧"。';

COMMENT ON COLUMN qd.sentiment_cycle.stage_ui IS
  '前端总览版阶段。判据顺序短路：1 退潮=risk_level=high OR leader_next_close_pct<=-5 OR 修复率<20；'
  '2 冰点=board_height<=2 AND limit_up_count<20；3 修复=修复率>=35 AND leader_next_close_pct>=0 AND volume_state<>缩量再缩量；'
  '4 主升=sentiment_value>=5 AND high_board_count>=3 AND leader_board_count>=5 AND volume_state<>缩量再缩量；'
  '5 试错=first_board_ratio_pct>=60 OR leader_board_count<=4；6 兜底分歧。'
  '出处 services/sentimentCycle/overview.ts:46-76';
COMMENT ON COLUMN qd.sentiment_cycle.stage_mcp IS
  'MCP 版（AI 实际读到的那套）。与 stage_ui 只差两处：冰点用 leader_board_count<=2 AND limit_up_count<20；'
  '主升不要求 sentiment_value>=5。出处 scripts/mcp-server.js:611-646';
COMMENT ON COLUMN qd.sentiment_cycle.stage_feishu IS
  '飞书机器人版，默认 分歧震荡。退潮=risk_level=high OR broken_rate>=35 OR 修复率<20；'
  '修复=修复率>=35 AND success_rate>=65 AND leader_next_close_pct>=0；'
  '主升=leader_board_count>=5 AND success_rate>=70 AND a_kill_count=0。'
  '出处 scripts/integrations/feishu/bot-server.js:173-212';
COMMENT ON COLUMN qd.sentiment_cycle.confidence IS
  '确定性查表加和，**不是统计置信度**（D-8）。base：主升72/修复68/退潮75/冰点70/其他62；'
  'risk_adj：low+6/high+8/其他+3；vol_adj：持续放量或缩量再缩量+6 否则 0。'
  'quantdash 外面套了 min(95,·)，理论最大 89 故恒不触发 —— 本实现去掉这个死上限直接存加和。'
  '出处 fetch_sentiment_cycle_snapshots.py:607-629、mcp-server.js:646';
COMMENT ON COLUMN qd.sentiment_cycle.risk_level IS
  'high ⇐ a_kill>=2 OR broken_rate_touched_pct>=35；medium ⇐ a_kill>=1 OR '
  'broken_rate_touched_pct>=20 OR weak>=2；否则 low。'
  '**输入全 NULL 时写 NULL 不写 low** —— quantdash 把无数据当低风险（D-7），不搬。'
  '⚠️ 判据的炸板率输入是 **触板口径**（broken_rate_touched_pct），不是契约 09 §4.1 '
  '字面写的 broken_rate_sealed_pct=(1-晋级率)×100：后者是 quantdash performance 面板'
  '的独立曲线（services/sentimentCycle/performance.ts:88,124），两者不等价；'
  '喂 risk_level 的那条在 scripts/fetch_sentiment_cycle_snapshots.py:496-503 写的是 '
  '炸板池/涨停池（services/sentimentCycle/highRisk.ts:32 同）。按字面接会因 A 股晋级率'
  '常年 20~50%% 而天天判 high、stage 恒为退潮。本轨 deviation 记 L-19 说明此事。'
  '阈值与档名出处 fetch_sentiment_cycle_snapshots.py:496-503';
COMMENT ON COLUMN qd.sentiment_cycle.volume_state IS
  '输入=两市成交额日环比。持续放量=近两日均>0；缩量再缩量=均<0；放量滞涨=最新>0 AND sentiment_value[T]<=sentiment_value[T-1]；'
  '存量震荡=其余（**含 change 为 NULL**，这条照搬 quantdash，但会在 deviation 记 L-1 说明 NULL 被归入存量震荡）。'
  '出处 fetch_sentiment_cycle_snapshots.py:558-568';
COMMENT ON COLUMN qd.sentiment_cycle.sentiment_value IS
  '10 × Σ_{b=2..14, prev[b]>0} (curr[b+1]/prev[b]) ÷ 参与求和的 b 个数，2 位。'
  '退化时 quantdash 写常量 5（涨停>50）或 2、TS 路径从不算公式（D-2）→ 本实现写 NULL。'
  '出处 fetch_market_core_snapshots.py:173';
COMMENT ON COLUMN qd.sentiment_cycle.board_height IS
  'max{b : counts[b]>0}。无涨停写 0（这个 0 是真值不是缺数据）。';
COMMENT ON COLUMN qd.sentiment_cycle.leader_board_count IS
  '当日最高连板数（来自 qd.board_ladder_leader）。三套 stage 条件都直接用它。';
COMMENT ON COLUMN qd.sentiment_cycle.limit_up_count IS
  '涨停家数，**双边严格判据** abs(close-limit_up)<=0.005，且 asset_type=''stock''。'
  '实测 2026-09-22：双边 65 / 单边 70。analytics.market_breadth.limit_up_count 是单边判据、虚高，禁止直接取。';
COMMENT ON COLUMN qd.sentiment_cycle.success_rate_pct IS
  '晋级率 = #(T日涨停 且 T+1日连板数 > T日连板数) / limit_up_count × 100，2 位。分母 0 → NULL。'
  'quantdash 存的是分数，此处统一 ×100。';
COMMENT ON COLUMN qd.sentiment_cycle.avg_board_gain IS
  'Σ min(next_board - board, 3) / limit_up_count，**单股封顶 3**，不舍入。分母 0 → NULL。';
COMMENT ON COLUMN qd.sentiment_cycle.premium_score IS
  '涨停溢价 = success_rate(分数)×60 + avg_board_gain×10，值域 0~90，2 位。输入 NULL → NULL。';
COMMENT ON COLUMN qd.sentiment_cycle.broken_rate_sealed_pct IS
  'quantdash 面板口径 = (1 - success_rate)×100，1 位。分母语义是"曾封板数"。';
COMMENT ON COLUMN qd.sentiment_cycle.broken_rate_touched_pct IS
  '**分母＝触板数（盘中摸到涨停价的家数，含封住的）**，分子＝触板未封。1 位。'
  'quantdash 原口径分子是"炸板池"（曾封板后炸开，含收盘回封），日线推不出来只能近似（D-3）：'
  '实测 2026-09-22 触板未封 37 / 触板 102 = 36.3%，而东财炸板池口径 18/(63+18)=22.2%，系统性抬高 64%。';
COMMENT ON COLUMN qd.sentiment_cycle.broken_repair_rate_lag1_pct IS
  'T-1 日触板未封的股票中，T 日收盘涨幅>0 的占比 ×100，1 位。'
  '**池空 → NULL 不是 0**（quantdash 写 0 与真实 0% 不可区分，D-6）。';
COMMENT ON COLUMN qd.sentiment_cycle.big_face_count_lag1 IS
  'T-1 触板未封池中"当日涨跌幅<=-5"的家数。契约原文叫 big_face_count；'
  '因为它和 big_face_repair_rate_lag1_pct 是同一个 T-1 场次，此处改名对齐日期，避免误读成 T 日。';
COMMENT ON COLUMN qd.sentiment_cycle.a_kill_count_lag1 IS
  'T-1 日板数>=4 的高板股中，T 日收盘涨幅<=-8 的家数。T 日无 K 线的个股跳过并把跳过家数记进 deviation。'
  '契约原文 a_kill_count，同 lag 改名理由同上。';
COMMENT ON COLUMN qd.sentiment_cycle.leader_next_close_pct IS
  '= T-1 日龙头在 T 日的收盘涨幅。契约原文 leader_next_close_pct（语义随 _lag1 族，此处沿用原名）。'
  '**缺失时不参与比较**：quantdash 用哨兵 -99 参与判断等于强行判成退潮方向（D-5），不搬。';
COMMENT ON COLUMN qd.sentiment_cycle.scope IS
  '涨跌家数的统计范围标注，取值如 all_boards / excl_bj。rise/fall/flat 一律本地日线实算，'
  '禁止 quantdash 的 clamp(round(2000+指数涨幅×650+(涨停-跌停)×4),200,4500) 估算模型（D-1）。';
COMMENT ON COLUMN qd.sentiment_cycle.amount_yi IS
  '(上证指数 amount + 深证成指 amount)/1e8，整数（照抄 Python banker 舍入）。'
  '任一缺失 → NULL。**不等于全 A 成交额**（漏北交所与深市非成分股），这条口径缺陷是 D-37，如实保留并标注。'
  '出处 fetch_sentiment_cycle_snapshots.py:523-547';


-- =============================================================================
-- 二、qd.board_ladder / board_ladder_member / board_ladder_leader / board_height
--     连板天梯
-- =============================================================================

CREATE TABLE IF NOT EXISTS qd.board_ladder (
    trade_date      date        NOT NULL,
    board_tier      smallint    NOT NULL CHECK (board_tier BETWEEN 1 AND 7),
    spec_version    text        NOT NULL,
    calculated_at   timestamptz NOT NULL DEFAULT now(),
    source          text        NOT NULL CHECK (source IN ('quantdash-logic','quantdash-logic+local')),
    deviation       jsonb       NOT NULL DEFAULT '[]'::jsonb,
    tier_label      text        NOT NULL,
    stock_count     integer     NOT NULL,
    is_highest_tier boolean     NOT NULL,
    PRIMARY KEY (trade_date, board_tier, spec_version)
);

COMMENT ON TABLE qd.board_ladder IS
  '逻辑来源：quantdash-ai-stock-main（PolyForm Noncommercial），仅重实现其算法口径，'
  '未复制源码。规格见 数据加工工作指南/09-quantdash逻辑规格.md。'
  '连板天梯：每日一行一板级。tier 7 收纳 lbc>=7，tier 2~6 精确等于该板数，tier 1 首板。';
COMMENT ON COLUMN qd.board_ladder.board_tier IS
  'quantdash 固定 6 行（七板以上/六/五/四/三/二连板），出处 fetch_market_core_snapshots.py:249-256。'
  '**它没有首板行**，而且找不到对应行时默认塞第 0 行（首板股出现在"七板以上"，D-12；'
  'TS 版 stockService.ts:150-153 则静默丢弃，两端行为相反且都错）。此处补 board_tier=1。';
COMMENT ON COLUMN qd.board_ladder.stock_count IS
  '该板级家数。**0 也出行**（quantdash 值为 0 时置空列表/缺行，会让前端天梯层级跳号）。';


CREATE TABLE IF NOT EXISTS qd.board_ladder_member (
    trade_date          date        NOT NULL,
    security_code       text        NOT NULL,
    spec_version        text        NOT NULL,
    calculated_at       timestamptz NOT NULL DEFAULT now(),
    source              text        NOT NULL CHECK (source IN ('quantdash-logic','quantdash-logic+local')),
    deviation           jsonb       NOT NULL DEFAULT '[]'::jsonb,
    board_tier          smallint    NOT NULL CHECK (board_tier BETWEEN 1 AND 7),
    board_count         smallint    NOT NULL,
    stock_name          text,
    industry_name       text,
    concept_name        text,
    limit_up_pct_regime smallint,
    pct_change_actual   numeric(6,2),
    is_one_word         boolean,
    is_turnover_board   boolean,
    turnover_rate_pct   numeric(8,4),
    first_seal_time     time,
    seal_time_available boolean     NOT NULL DEFAULT false,
    price_close         numeric(12,4),
    amount_yuan         numeric(20,2),
    PRIMARY KEY (trade_date, security_code, spec_version)
);

COMMENT ON TABLE qd.board_ladder_member IS
  '逻辑来源：quantdash-ai-stock-main（PolyForm Noncommercial），仅重实现其算法口径，'
  '未复制源码。规格见 数据加工工作指南/09-quantdash逻辑规格.md。天梯成分股，每日一股一行。';
COMMENT ON COLUMN qd.board_ladder_member.board_count IS
  '真实连板数，**不封顶**：lbc=9 就存 9（board_tier 才是 7 档分桶）。';
COMMENT ON COLUMN qd.board_ladder_member.industry_name IS
  '出处涨停池字段 hybk。quantdash 缺失填 ''市场热点''、概念缺失填 ''连板''（编造分类，D-13），'
  'MCP 版又填 ''Unknown''（mcp-server.js:157）→ 本实现缺失一律 NULL，不编行业。';
COMMENT ON COLUMN qd.board_ladder_member.limit_up_pct_regime IS
  '真实涨跌幅档位（10/20/30/5），来自**当日** limit_up/pre_close 的比值'
  '（唯一写者 app/compute/limit_prices.py，出处在 stock_daily.limit_basis；'
  '2026-10-07 之前这里取的是 board_limit_ratio(board, is_st)＝当前快照，套历史会错判 ±5% 档）。'
  'quantdash 由代码前缀硬推假涨幅（30/68→20 其余→10，Python 版还漏北交所 30 档，D-14）→ '
  '本实现存档位、真实涨幅另存 pct_change_actual。出处 fetch_market_core_snapshots.py:311 vs stockService.ts:130-134';
COMMENT ON COLUMN qd.board_ladder_member.is_one_word IS
  '一字板：|open-close|<0.001 AND |open-high|<0.001 AND |open-low|<0.001（照抄）。'
  '出处 fetch_sentiment_cycle_snapshots.py:178-182';
COMMENT ON COLUMN qd.board_ladder_member.is_turnover_board IS
  '**本轨扩展，quantdash 全库无此概念** → source 标 ''quantdash-logic+local''。'
  '判据：非一字 且 turnover_rate_percent >= config.setting 的 qd_turnover_board_pct（当前 5）。';
COMMENT ON COLUMN qd.board_ladder_member.first_seal_time IS
  '首次触及涨停价的分钟时刻。**历史不可得**：market_data.stock_minute 只有 2026-09-22 一天，'
  '其余全 NULL。quantdash 用 derivePseudoTime() 哈希编造时分秒（D-28，出处 limitUpPoolService.ts:57-84）'
  '并因此污染龙头判定 → 本实现不跟。';
COMMENT ON COLUMN qd.board_ladder_member.seal_time_available IS
  '明示这行的封板时间是不是真有。前端必须用它决定要不要显示时间，不许把 NULL 显示成 -- 当没事。';
COMMENT ON COLUMN qd.board_ladder_member.price_close IS
  '真实收盘价。quantdash 在天梯里塞 price=0 / pe=0 / volume 与 turnover 填横杠字符串 这类占位常量，'
  '与真实个股快照同形导致下游无法区分（D-15）→ 不搬。出处 fetch_market_core_snapshots.py:310-317';


CREATE TABLE IF NOT EXISTS qd.board_ladder_leader (
    trade_date              date        NOT NULL,
    spec_version            text        NOT NULL,
    calculated_at           timestamptz NOT NULL DEFAULT now(),
    source                  text        NOT NULL CHECK (source IN ('quantdash-logic','quantdash-logic+local')),
    deviation               jsonb       NOT NULL DEFAULT '[]'::jsonb,
    leader_board_count      smallint,
    leader_count            integer,
    second_highest_board    smallint,
    three_plus_count        integer,
    continued_count         integer,
    leader_security_code    text,
    leader_stock_name       text,
    leader_tie_break        text NOT NULL CHECK (leader_tie_break IN ('seal_time','amount_no_seal_time','code_asc','no_candidate')),
    leader_is_one_word      boolean,
    leader_continued        boolean,
    leader_next_open_pct    numeric(6,2),
    leader_next_close_pct   numeric(6,2),
    leader_status_label     text,
    leader_status_code      text,
    PRIMARY KEY (trade_date, spec_version)
);

COMMENT ON TABLE qd.board_ladder_leader IS
  '逻辑来源：quantdash-ai-stock-main（PolyForm Noncommercial），仅重实现其算法口径，'
  '未复制源码。规格见 数据加工工作指南/09-quantdash逻辑规格.md。最高标/龙头，每日一行。';
COMMENT ON COLUMN qd.board_ladder_leader.second_highest_board IS
  'max{lbc < leader_board_count}，无则 **NULL** —— quantdash 写 0 与"真有一板"不可区分（D-16）。'
  '出处 fetch_sentiment_cycle_snapshots.py:424-436';
COMMENT ON COLUMN qd.board_ladder_leader.continued_count IS
  '#{同高度龙头中，次日 lbc > leader_board_count 的家数}。';
COMMENT ON COLUMN qd.board_ladder_leader.leader_tie_break IS
  '龙头是怎么选出来的，**必须落库供前端显示**。quantdash 原口径"并列取最早封板者"'
  'sort(key=str(limitUpTime or "99:99:99"))[0]，且排序键未归一化（lbt 可能是 int 93000 或非补零串，'
  '按字符串比得到 "142500"<"93000"）→ 它的龙头判定本身是错的（D-17，出处 '
  'fetch_sentiment_cycle_snapshots.py:210-215,429 vs limitUpPoolService.ts:23-55）。'
  '本实现裁定：有 first_seal_time 按其升序（seal_time）；否则按 amount_yuan 降序（amount_no_seal_time）；'
  '仍并列按 security_code 升序（code_asc）；无涨停股（no_candidate）。';
COMMENT ON COLUMN qd.board_ladder_leader.leader_next_open_pct IS
  '次日开盘涨幅 (open_T+1 - close_T)/close_T×100，2 位。'
  '下一交易日用 reference_data.trade_calendar.next_open_date 显式取，'
  '不像 quantdash 那样"取最后一个 date<=目标日"在非交易日静默前移（D-18，出处 mcp-server.js:122-131）。';
COMMENT ON COLUMN qd.board_ladder_leader.leader_status_label IS
  '9 值 if-elif 顺序短路，出处 fetch_sentiment_cycle_snapshots.py:346-368。'
  'next_close_pct 为 NULL 时：leader_count>=2 AND leader_board_count>=4→高标抱团；一字→一字观察；其余→待次日确认。'
  '有值时：一字且晋级→一字加速；晋级且>=0→强势晋级；>=3→断板承接；>=0→高位分歧；>-5→分歧转弱；其余→退潮承压。';
COMMENT ON COLUMN qd.board_ladder_leader.leader_status_code IS
  'MCP 版英文码：high_leader_cluster / one_word_watch / await_confirmation / one_word_acceleration / '
  'strong_continuation / accepted_after_break / high_level_divergence / weakening / retreat_pressure';


CREATE TABLE IF NOT EXISTS qd.board_height (
    trade_date                      date        NOT NULL,
    spec_version                    text        NOT NULL,
    calculated_at                   timestamptz NOT NULL DEFAULT now(),
    source                          text        NOT NULL CHECK (source IN ('quantdash-logic','quantdash-logic+local')),
    deviation                       jsonb       NOT NULL DEFAULT '[]'::jsonb,
    main_board_highest              smallint,
    main_board_highest_names        text[],
    main_board_highest_codes        text[],
    main_board_second_highest       smallint,
    main_board_second_highest_names text[],
    main_board_second_highest_codes text[],
    chinext_highest                 smallint,
    chinext_highest_names           text[],
    chinext_highest_codes           text[],
    star_highest                    smallint,
    star_highest_names              text[],
    star_highest_codes              text[],
    bj_highest                      smallint,
    bj_highest_names                text[],
    bj_highest_codes                text[],
    PRIMARY KEY (trade_date, spec_version)
);

COMMENT ON TABLE qd.board_height IS
  '逻辑来源：quantdash-ai-stock-main（PolyForm Noncommercial），仅重实现其算法口径，'
  '未复制源码。规格见 数据加工工作指南/09-quantdash逻辑规格.md。板高度分市场，每日一行。'
  '前 3 组按 quantdash 的前缀白名单；star_highest/bj_highest 是本轨补齐它忽略的市场。';
COMMENT ON COLUMN qd.board_height.main_board_highest IS
  '主板最高板。quantdash 白名单：主板 600/601/603/605/000/001/002/003、创业板 300/301，'
  '**排除科创板 688/689 与北交所 8xx/4xx**（D-19，出处 fetch_sentiment_cycle_snapshots.py:22-27、boardHeight.ts:10-11）。'
  '值为 0 时对应的 _names/_codes 保持空数组（这条不是缺陷，是它的显示约定，照搬）。';
COMMENT ON COLUMN qd.board_height.star_highest IS
  '科创板（688/689）最高板 —— 本轨新增，quantdash 完全忽略该市场。deviation 记 D-19。';
COMMENT ON COLUMN qd.board_height.bj_highest IS
  '北交所（8xx/4xx）最高板 —— 本轨新增，quantdash 完全忽略该市场。deviation 记 D-19。';


-- =============================================================================
-- 三、qd.sector_rotation / sector_persistence · 板块周期
-- =============================================================================
-- ⚠️ quantdash 的"板块周期"**没有阶段枚举**（阶段枚举属于情绪周期）。它的实质是
-- 轮动矩阵 + 持续性统计。所以 analytics.ignition 的五档（ignite/follow/coil/fade/none）
-- 不是重复实现，是另一套东西 —— 两轨都要展示，谁也不替换谁。

CREATE TABLE IF NOT EXISTS qd.sector_rotation (
    trade_date      date        NOT NULL,
    sector_code     text        NOT NULL,
    spec_version    text        NOT NULL,
    calculated_at   timestamptz NOT NULL DEFAULT now(),
    source          text        NOT NULL CHECK (source IN ('quantdash-logic','quantdash-logic+local')),
    deviation       jsonb       NOT NULL DEFAULT '[]'::jsonb,
    sector_name     text,
    sector_type     text        CHECK (sector_type IN ('概念','行业')),
    day_rank        smallint    NOT NULL CHECK (day_rank > 0),
    pct_change      numeric(8,4),
    up_count        integer,
    down_count      integer,
    member_count    integer,
    window_size     smallint,
    concept_candidates smallint,
    industry_candidates smallint,
    PRIMARY KEY (trade_date, sector_code, spec_version)
);

COMMENT ON TABLE qd.sector_rotation IS
  '逻辑来源：quantdash-ai-stock-main（PolyForm Noncommercial），仅重实现其算法口径，'
  '未复制源码。规格见 数据加工工作指南/09-quantdash逻辑规格.md。板块轮动矩阵：'
  '仅落 day_rank<=8（轮动矩阵每日取前 8）的 概念/行业 板块。';
COMMENT ON COLUMN qd.sector_rotation.day_rank IS
  '当日按 (pct_change DESC, sector_name ASC) 的名次。样本＝sector_type IN (概念,行业) 里按涨幅倒序前 '
  'config.setting 的 qd_sector_top_n（当前 24）个 —— 出处 fetch_sector_snapshots.py:16-45（东财 clist 取前 24，无地域板块）。'
  'quantdash 的 MCP 版取的是 top 20 且无名称 tie-break，与它自己的 Python/TS 版不一致（D-20，'
  '出处 mcp-server.js:397,418）→ 本实现统一 24 + 名称 tie-break。';
COMMENT ON COLUMN qd.sector_rotation.up_count IS
  '板块内上涨家数。**quantdash 只有市场级涨跌家数、没有板块级** → 本列是本轨实算，'
  '所在行 source 标 ''quantdash-logic+local''。出处 market_data.sector_daily（从成员回算）。';
-- CREATE TABLE IF NOT EXISTS 不会给已存在的表补列，所以宇宙指纹两列单独走一遍 ALTER。
-- 幂等：新库建表时已带这两列，这里 no-op；老库（2026-09-24 首建）补上。
ALTER TABLE qd.sector_rotation ADD COLUMN IF NOT EXISTS concept_candidates smallint;
ALTER TABLE qd.sector_rotation ADD COLUMN IF NOT EXISTS industry_candidates smallint;

COMMENT ON COLUMN qd.sector_rotation.window_size IS
  '当日参与排名的板块总数（剔掉解析垃圾之后的干净样本），供前端说明名次分母。';
COMMENT ON COLUMN qd.sector_rotation.concept_candidates IS
  '当日参与排名的**概念**板块数；industry_candidates 是**行业**板块数。'
  '分类型存而不是只存总数，是因为跨日名次只在宇宙一致时才可比，而宇宙的日间自然浮动'
  '（实测概念 266~268、行业 186~188）不该被判成断点：qd.sector_persistence 的守卫'
  '按"同类型计数相对窗口众数偏离超过两成"判，实测唯一断点是 2026-09-23：'
  '参与排名的通达信行业约 180 → 561、原始行 228 → 609，概念数 268 全程不变'
  '（上游 c3/c17 那天起补了 381 个通达信三级行业）。见 deviation L-20。';
COMMENT ON COLUMN qd.sector_rotation.industry_candidates IS
  '见 concept_candidates：当日参与排名的行业板块数。';

CREATE TABLE IF NOT EXISTS qd.sector_persistence (
    trade_date               date        NOT NULL,
    spec_version             text        NOT NULL,
    calculated_at            timestamptz NOT NULL DEFAULT now(),
    source                   text        NOT NULL CHECK (source IN ('quantdash-logic','quantdash-logic+local')),
    deviation                jsonb       NOT NULL DEFAULT '[]'::jsonb,
    current_leader_name      text,
    current_leader_code      text,
    streak_days              smallint    CHECK (streak_days >= 1),
    top_three_appearances    smallint,
    strength_delta_pct       numeric(8,4),
    strongest_repeat_name    text,
    strongest_repeat_code    text,
    window_days              smallint,
    window_start             date,
    PRIMARY KEY (trade_date, spec_version)
);

COMMENT ON TABLE qd.sector_persistence IS
  '逻辑来源：quantdash-ai-stock-main（PolyForm Noncommercial），仅重实现其算法口径，'
  '未复制源码。规格见 数据加工工作指南/09-quantdash逻辑规格.md。板块领涨持续性，每日一行，'
  '窗口＝qd.sector_rotation 里按日期升序的末 5 个交易日。';
COMMENT ON COLUMN qd.sector_persistence.streak_days IS
  '自最新日向前回溯**同一 sector_code** 连续居首的天数，最小 1（照搬）。'
  '不按板块名回溯：TDX 三级行业有同名不同码的板块（实测 2026-09-23 两个「房产服务」'
  '同时占第 4 与第 12 名），按名字会把两个板块当成一个在连庄（deviation L-21）。'
  '窗口内各日参与排名的板块数（window_size）不一致时**本列连同 top_three_appearances / '
  'strength_delta_pct / strongest_repeat 一律 NULL** —— 跨日名次不可比 '
  '(实测断点 2026-09-23：通达信行业数 228→609)，见 deviation L-20。';
COMMENT ON COLUMN qd.sector_persistence.window_days IS
  '窗口内实际可用的交易日数。**每行都带 window_size 是有用途的**：跨日排名对比要先看'
  '它是否一致，不一致说明板块宇宙变了（上游改过行业口径），那几天的名次不能互比。';
COMMENT ON COLUMN qd.sector_persistence.top_three_appearances IS
  '5 日窗口内进入 day_rank<=3 的天数。quantdash 缺数据时默认 1（把无数据当"出现过一天"，D-21，'
  '出处 fetch_sector_snapshots.py:131-199）→ 本实现写 0。';
COMMENT ON COLUMN qd.sector_persistence.strength_delta_pct IS
  '当日 leader 涨幅 − 前日 leader 涨幅，**仅两日是同一板块（按 sector_code 判）时才算**，'
  '否则 NULL（前端显示"新晋领涨"）。NULL 显式保留（这条 quantdash 做对了，照搬）。'
  '窗口宇宙不一致时整列留 NULL，见 streak_days 注释与 deviation L-20。';
COMMENT ON COLUMN qd.sector_persistence.strongest_repeat_name IS
  'argmax(top_three_appearances DESC, sector_name ASC)。quantdash 缺失时回落成当前 leader'
  '（编造"最强连庄"，D-22，出处 sectorService.ts:274-275）→ 本实现写 NULL。';


-- =============================================================================
-- 四、qd.news_item · 信息采集落库结构
-- =============================================================================
-- 采集（抓取/限流/去重游标）归 code：数据获取；这里只给结构与清洗约定。
-- 本表**故意不带 trade_date**：新闻是事件粒度不是交易日粒度，时间轴用 published_at。
-- provenance 列改名 logic_source —— §2 的 source 列名在这里已被"新闻来源"占用。
-- 研报表 qd.research_report 按 09 §4.4 裁定**先不做**（PDF 要过 __tst_status/EO_Bot_Ssid
-- cookie 挑战，属对抗性爬取；且 quantdash published_at 解析失败会回填当前时间 D-26）。
-- NewsFilter（Playwright 接管本机 Chrome 抓 DOM）那一路**不搬**：依赖本机常开调试端口，
-- 且 created_at 用 datetime.now() 冒充发布时刻（D-25），数据本身不可信。

CREATE TABLE IF NOT EXISTS qd.news_item (
    item_id         text        PRIMARY KEY,
    published_at    timestamptz,
    captured_at     timestamptz NOT NULL DEFAULT now(),
    source          text        NOT NULL,
    level           text        CHECK (level IN ('A','B','C','D')),
    title           text        NOT NULL,
    content         text,
    url             text,
    sentiment       text,
    news_type       text,
    logic_source    text        NOT NULL DEFAULT 'quantdash-logic',
    spec_version    text        NOT NULL,
    deviation       jsonb       NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_qd_news_published ON qd.news_item (published_at DESC NULLS LAST);

COMMENT ON TABLE qd.news_item IS
  '逻辑来源：quantdash-ai-stock-main（PolyForm Noncommercial），仅重实现其算法口径，'
  '未复制源码。规格见 数据加工工作指南/09-quantdash逻辑规格.md。'
  '财联社电报入库结构。端点 https://www.cls.cn/nodeapi/telegraphList，'
  'sign=md5(md5(query)) 属公开接口签名算法非其独创，照搬。';
COMMENT ON COLUMN qd.news_item.item_id IS
  '去重键：API 的 id；缺失则 ''cls-''||ctime||''-''||md5(title)前 8 位（照搬 quantdash 规则）。';
COMMENT ON COLUMN qd.news_item.published_at IS
  '**必须与 captured_at 分列**（quantdash 的 NewsFilter 那一路用 datetime.now() 冒充发布时刻，D-25，'
  '出处 newsfilter_to_dingtalk.py:138-141）。发布时刻拿不到就 NULL，不许用抓取时刻顶。';
COMMENT ON COLUMN qd.news_item.level IS
  '财联社电报级别。quantdash 只保留 A/B（出处 cls_telegraph_to_dingtalk_single.py）；'
  '本表**不过滤掉 C/D**，全部落库、靠 level 列让下游决定，避免"过滤即丢数据"。';
COMMENT ON COLUMN qd.news_item.sentiment IS
  '情感标签。quantdash 这条路**根本没做情感**（sentiment=null）→ 照搬为 NULL。'
  '禁止用前端那套硬编码分数（RECENT_SENTIMENT）或 5 条逼真的 MOCK_NEWS（D-24，'
  '出处 newsService.ts:44-49、constants.ts:14-32）。';


-- =============================================================================
-- 五、qd.stock_strength · 个股强弱分档（喂 AI 用）
-- =============================================================================
-- 09 §4.4 留了个二选一（落库 or 前端算）。**裁定：落库。**
-- 理由：09 §4.5(a) 第 6 条要求"若该数据将被 AI 使用，MCP/服务层必须能读到同一份
-- 结构化结果"，而 YuanShan 的 review_agent 走冻结证据包 → 只有落库才能被人看和
-- 被 AI 读保持同一份数；前端另算一份会违反这条。
-- 关注池＝当日涨停封住 + 触板未封（约百只/日），不是全市场 5500 只。

CREATE TABLE IF NOT EXISTS qd.stock_strength (
    trade_date    date        NOT NULL,
    security_code text        NOT NULL,
    pct_change    numeric(6,2),
    strength      text        NOT NULL CHECK (strength IN ('强','中性','弱')),
    pool_kind     text        NOT NULL CHECK (pool_kind IN ('sealed','touched_not_sealed')),
    spec_version  text        NOT NULL,
    calculated_at timestamptz NOT NULL DEFAULT now(),
    source        text        NOT NULL CHECK (source IN ('quantdash-logic','quantdash-logic+local')),
    deviation     jsonb       NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (trade_date, security_code)
);

COMMENT ON TABLE qd.stock_strength IS
  '逻辑来源：quantdash-ai-stock-main（PolyForm Noncommercial），仅重实现其算法口径，'
  '未复制源码。规格见 数据加工工作指南/09-quantdash逻辑规格.md。个股强弱分档，喂 AI 复盘用。';
COMMENT ON COLUMN qd.stock_strength.strength IS
  '阈值照搬：pct_change >= +3 强 / <= -2 弱 / 其余中性。出处 services/aiDailyReview/promptBuilders.ts:260-266。'
  'pct_change 为 NULL 时**不出行**（quantdash 的 Number(x)||0 会把无数据判成中性，D-4）。';


-- =============================================================================
-- 六、reference_data.security_st_period · ST 生效区间维度表
-- =============================================================================
-- 08 §二.3 的根治表。**实测本机没有 ST 历史真源**（2026-09-24 查尽）：
--   · reference_data.security.is_st / name 是 2026-08-27 单日快照，无生效日期；
--   · 通达信 ST板块（TDX_880516）成员 212 只，sector_member.in_date **全部**是 2026-08-03、
--     out_date **全部** NULL —— 是快照不是历史；
--   · stock_profile.raw 的 TPFlag 是"停牌"标志不是 ST（212 只 is_st 票全为 0）；
--   · akshare 1.18.88 只有 stock_zh_a_st_em（当前 ST 列表），无名称变更历史接口；
--   · 价格反证不可用：主板 3199 只里 3197 只在 2021-12 起出现过 |涨幅|>5.5%（证明那些天非 ST），
--     但 2800 只出现过"收盘精确等于 round(pre_close*1.05,2)"——低价股+5.00% 很常见，
--     这条**不能**当 ST 正证据。只能证"某天不是 5% 档"，不能证"某天是"。
-- 所以本表先建结构、只装**可证的部分**，不拿猜测填满历史：
--   method='snapshot_current'  当前快照，valid_from=is_st 的 updated_at，只对其后有效
--   method='non_st_proved_by_move'  用 |涨幅|>5.5% 反证的"该日非 5% 档"单日区间
-- 缺的那一块（每天到底是 5% 还是 10% 档）要等 code：数据获取 接进名称变更/ST 历史源。
-- **在拿到源之前不动 market_data.stock_daily.limit_up** —— 无源重算 637 万行等于用猜测覆盖现值。

CREATE TABLE IF NOT EXISTS reference_data.security_st_period (
    security_code text        NOT NULL,
    valid_from    date        NOT NULL,
    valid_to      date,
    is_st         boolean     NOT NULL,
    method        text        NOT NULL
                CHECK (method IN ('snapshot_current','non_st_proved_by_move','external_feed')),
    evidence      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    calculated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (security_code, valid_from, method),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE INDEX IF NOT EXISTS ix_st_period_lookup ON reference_data.security_st_period (valid_from, valid_to);

COMMENT ON TABLE reference_data.security_st_period IS
  'ST 生效区间维度表。根治 08-跨源口径契约 §二.3：`market_data.stock_daily.limit_up` 以前是用'
  '**当前** is_st 快照套全历史回算的（reference_data.board_limit_ratio(board,is_st)），'
  '两个方向都错：被 ST 之前的行错算成 5% 档、已摘帽的票仍按 5% 算涨停价（把非涨停判成涨停）。'
  '2026-10-07 起 `app/compute/limit_prices.py` 把它当分层判据的**第 3 层**用（确证的区间才认）。'
  '本表**尚无完整历史**：见 method 列，只装可证部分，缺源待 code：数据获取 接入。';
COMMENT ON COLUMN reference_data.security_st_period.method IS
  'snapshot_current＝当前快照（只对其后日期有效）；non_st_proved_by_move＝由 |涨幅|>5.5% 反证的'
  '"该日绝非 5% 档"单日（is_st=false，valid_from=valid_to）；external_feed＝外部名称变更源接入后的真区间。';
COMMENT ON COLUMN reference_data.security_st_period.evidence IS
  '证据。例 {"max_abs_pct":7.31,"close":10.53,"pre_close":9.81,"rule":"abs(pct)>5.5"}。'
  '每行必须能被独立复算 —— 没有证据的行不许写。';


-- =============================================================================
-- 七、授权（09 §5：YuanShan 侧只有只读的 ft_reader，写入走 ft_etl）
-- =============================================================================
GRANT USAGE ON SCHEMA qd TO ft_reader, ft_app, ft_etl;
GRANT SELECT ON ALL TABLES IN SCHEMA qd TO ft_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA qd TO ft_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA qd TO ft_etl;
GRANT SELECT ON reference_data.security_st_period TO ft_reader, ft_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON reference_data.security_st_period TO ft_etl;

-- 本 schema 之后新建的表自动继承同样权限（防止后续加表时忘记 grant 而出现"页面查不到"）
ALTER DEFAULT PRIVILEGES IN SCHEMA qd GRANT SELECT ON TABLES TO ft_reader, ft_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA qd GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ft_etl;

-- ⚠️ ALTER DEFAULT PRIVILEGES 是**按执行角色**记录的，实测（2026-09-24）两点：
--   1. 上面两条只记在 ft_admin 名下（pg_default_acl.defaclrole=ft_admin）。
--      本仓库所有 DDL 都是 init_db.py 用 ft_admin 跑的，所以"下次在 qd 建表"必然
--      走 ft_admin → 覆盖成立。实测：ft_admin 在 qd 建一张表，ft_reader 立刻能 select、
--      ft_etl 立刻能 insert，不需要再 grant。
--   2. **换别的角色建表就不覆盖了**。而且 ft_etl 连 `CREATE` on schema qd 都没有
--      （实测报 permission denied for schema qd），所以它本来也建不了表。
--      真要允许 ft_etl 在 qd 建表，得由超级用户补
--      `ALTER DEFAULT PRIVILEGES FOR ROLE ft_etl IN SCHEMA qd ...`——
--      ft_admin 不是 superuser、也不在任何其它角色的成员里，自己加不了。
-- 结论：**qd 的建表动作固定走 ft_admin**，这条就是上面那个覆盖成立的前提。
