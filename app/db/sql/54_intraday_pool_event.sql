-- -*- coding: utf-8 -*-
-- 54_intraday_pool_event.sql
-- 盘中**涨跌停池事件流**落库：每 5 分钟一针，把东财三个池子（涨停／炸板／跌停）与上一针
-- 做差，逐条记下「谁在几点几分新封了、谁开板又回封、谁炸板、谁跌停回封／开板」。
--
-- 为什么要落这一张（用户 2026-10-26 定：外部播报系统整体迁进来并入库）：
-- 这套判据原来只在外部脚本 `intraday_zt/zt_watcher.py` 里活着，产出**只有飞书群的一条文本**
-- 和一份 `var/zt_events.jsonl`；库里同一天有什么票在几点几分封住、开过几次，
-- 页面一关就答不上来，而池子是取数时点快照，**事后补不回来**。
--
-- ⚠️ 与 `analytics.limit_up_pool_daily`（DDL 53）的分工，这条必须先看：
--   · 53 = **一场一行的日级母体**，家数/封住/触板以**本地日线自算**为准（用户 10-26 裁的口径①），
--     首封/开板次数优先本地分钟线（10-26 实测与同花顺可比 84% 落在 1 分钟内）。
--   · 54 = **一日多行的盘中时序**，判据是"相邻两针池子快照之差"，出处只能是东财池
--     （分钟线要到收盘才齐，盘中算不了"这一针新出现"）。
--   两边**可以不一致，而且必须都留着**：同一天 53 报"封住 84 只"、54 报"盘中共 96 只次进过池"
--   不是谁算错了。所以每一行都带 `basis`，读侧与界面不许把两边的数混成一个。
--
-- 口径纪律（全部沿用 53 与项目既定）：
--   · 缺数一律 NULL 并在 `missing` 里点名，**不写 0** —— 0 在这里是真实读数
--     （`open_count=0`＝从头封到尾没开过板），null 才是"这一格判不了"。
--     外部脚本原来有两处拿默认值顶（连板数缺→1、封单缺→"封单缺数"文案），搬迁时**改成 NULL**，
--     理由：连板数缺了填 1 会把一只 3 板票在第一针上记成首板，事后无从发现。
--   · `spec_version` 进主键：判据改版换号，旧行留着对账，不覆盖。
--   · 涨幅单位统一**百分数**（`pct` 10.06536 → 存 10.0654），价格单位**元**，封单额单位**元**。
--     出处换算（现查 10-26）：池行 `p` 是 价×1000，`zdp` 已是百分数，`fbt` 是 hhmmss 整数。
--   · 板块/概念涨幅另有一处坑：`market_data.sector_daily.pct_change` 实测存的是**小数**
--     （2026-10-26 现查 TDX_880347 = -0.0619 ＝ −6.19%），外部脚本直接 `'+.1f%'` 渲染会**错 100 倍**。
--     本表所有 `*_pct` 列一律存百分数，写入前 ×100，换算在 `app/intraday/sources.py` 一处做完。

create table if not exists analytics.intraday_pool_event (
    trade_date        date        not null,           -- 交易日（本机墙钟 + PG 交易日历判，非交易日不写）
    captured_at       timestamptz not null,           -- 这一针取数的时刻（东财池读到的那一瞬）
    event_key         text        not null,           -- 幂等键：task|captured_at|kind|code，重播不重插
    spec_version      text        not null,

    -- 一、事件本体
    side              text        not null check (side in ('zt','dt')),   -- 涨停轨 / 跌停轨
    kind              text        not null,           -- 机器名：seal_new/reseal/reseal_after_open/unseal/...
    kind_label        text        not null,           -- 界面用中文（新封/回封/开板又回封/炸板/跌停回封/…）
    security_code     text        not null,
    security_name     text,                           -- 原样带空格的商品名（实测有「生 意 宝」）不改写
    pct               numeric(10,4),                  -- 涨幅 %（zdp 原值，跌停为负）
    price             numeric(12,3),                  -- 现价（元，池行 p ÷ 1000）
    board_count       smallint,                       -- 连板数（含本场）；缺 ⇒ null，不填 1
    open_count        smallint,                       -- 开板次数（zbc）；缺 ⇒ null，不填 0
    first_seal_time   text,                           -- 首封时刻 'HH:MM'（fbt 换算）；跌停池没有这一列 ⇒ null
    seal_fund_yuan    numeric(24,2),                  -- 封单额（元）；缺 ⇒ null
    industry          text,                           -- 池行 hybk 的行业文本（不是板块代码，只作展示）

    -- 二、这一针的整池读数（事后对账用：单看事件会分不清"没变化"还是"池子挂了"）
    pool_zt_n         integer,                        -- 本针涨停池家数；取数失败 ⇒ null（≠0）
    pool_zb_n         integer,                        -- 本针炸板池家数
    pool_dt_n         integer,                        -- 本针跌停池家数
    round_seq         integer,                        -- 当日第几针（1 起）
    is_baseline       boolean     not null default false, -- 首轮/换日重建基线：只报摘要，不逐条播
    basis             text        not null default 'source_pool_diff'
                    check (basis in ('source_pool_diff','local_minute')),

    source            text        not null default 'eastmoney_push2ex',
    missing           jsonb       not null default '{}'::jsonb,  -- 键名＝上面列名，值＝为什么缺
    calculated_at     timestamptz not null default now(),

    constraint intraday_pool_event_pkey primary key (trade_date, captured_at, event_key, spec_version)
);

create index if not exists ix_intraday_pool_event_code
    on analytics.intraday_pool_event (security_code, trade_date desc);
create index if not exists ix_intraday_pool_event_day
    on analytics.intraday_pool_event (trade_date desc, captured_at);

comment on table analytics.intraday_pool_event is
    '盘中涨跌停池事件流（相邻两针池子快照之差）。日级母体在 analytics.limit_up_pool_daily，两边口径不同、都要留。';
comment on column analytics.intraday_pool_event.kind is
    'seal_new=新封｜reseal=曾炸过又回封｜reseal_after_open=开板又回封(zbc 变大)｜unseal=炸板出池｜dt_seal_new=新跌停｜dt_reseal=跌停回封｜dt_reseal_after_open=跌停开板又回封｜dt_open=跌停开板(跌停池无炸板对应物，出池即判开板)';
comment on column analytics.intraday_pool_event.board_count is
    '连板数（含本场）。源站缺这一格时写 null —— 外部旧实现填 1，会把 3 板票记成首板且事后看不出来。';
comment on column analytics.intraday_pool_event.pool_zt_n is
    '本针整池家数。null=这一针没取到（连挂 3 针会发一条报警并进静默等待），0 是"池子真空"的真实读数。';
comment on column analytics.intraday_pool_event.is_baseline is
    'true=首轮或换日重置后的基线摘要，不是变化事件。盘前池为空时不建"0 家"假基线（外部实测教训）。';

-- 播报留痕：几点发了什么、发没发出去、失败原因。四条播报（watcher/auction/heat/noon）共用。
-- `sysentry` 那一路不建新表 —— 它的判据与母体本来就是 `analytics.system_entry`（DDL 33/34），
-- 这里只留"这条消息几点发过"的一行，避免一个东西两张表。
create table if not exists analytics.intraday_broadcast (
    trade_date        date        not null,
    broadcast_key     text        not null,           -- task|at，重播同一时刻不重插
    spec_version      text        not null,

    task              text        not null,           -- watcher|auction|heat|noon|sysentry
    what              text        not null,           -- digest|auction|heat|noon|sent|poll_fail|stale|crash|...
    at_ts             timestamptz not null,
    ok                boolean,                        -- null=这一条不涉及发送结果（只记数据事件）
    channel_key       text,                           -- notify 渠道键（飞书盯盘群…）；URL/密钥**绝不入库**
    http_status       integer,                        -- 只在"能确定"时写。旧实现只发出去过就记 200 → 假读数
    delivered         boolean,                        -- **业务判据**：飞书响应 code==0（10-16 实测无效 token 也回 HTTP 200）
    err               text,
    body              text,                           -- 实际发出去的文本（改文案前能看见原文）
    counts            jsonb       not null default '{}'::jsonb,  -- 该条的结构化摘要（家数/只数/命中数…）
    source_detail     jsonb       not null default '{}'::jsonb,  -- 用了哪个源、是否降级、重试第几次
    calculated_at     timestamptz not null default now(),

    constraint intraday_broadcast_pkey primary key (trade_date, broadcast_key, spec_version)
);

create index if not exists ix_intraday_broadcast_day
    on analytics.intraday_broadcast (trade_date desc, at_ts);
create index if not exists ix_intraday_broadcast_task
    on analytics.intraday_broadcast (task, trade_date desc);

comment on table analytics.intraday_broadcast is
    '盘中播报的发送留痕与失败记账（宁缺毋假：取不到就记"未就绪"占位，不拿旧数顶）。';
comment on column analytics.intraday_broadcast.delivered is
    '以响应体的业务 code 为准，不以 HTTP 状态为准 —— 无效 token 也回 200（10-16 实测）。';

grant select on analytics.intraday_pool_event, analytics.intraday_broadcast to ft_reader;
grant select, insert, update, delete on analytics.intraday_pool_event, analytics.intraday_broadcast to ft_etl, ft_app;
grant select on analytics.intraday_pool_event, analytics.intraday_broadcast to ft_app;
