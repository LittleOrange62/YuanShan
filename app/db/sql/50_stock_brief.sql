-- -*- coding: utf-8 -*-
-- 50_stock_brief.sql
-- 「个股深度复盘 · 个股信息」那块里那份 400 字公司说明（AI 生成）存在这里。
--
-- 为什么要一张表而不是只留在浏览器里（用户 2026-10-22 的裁定）：这一屏的入口有两种
-- （双击某一行另开一窗带 symbol / 从导航直接进来），而结论要摆在最上面那张卡里。
-- 只存 localStorage 的话，换浏览器、清缓存、或者从别的屏双击过来，那块就空了 ——
-- 而屏幕上明明写着「这一只是什么」，读的人分不清"没生成过"与"生成过但这里看不见"。
-- 存进库之后，`/api/stock/facts`（那一卡的唯一读侧）顺带把它交出来，任何入口都能看到同一份。
--
-- 四条结构纪律（都有反面例子）：
--   ① 主键带 `spec_version`：材料边界（哪几段、每段取几条）或话术契约改版＝换号，
--      旧行留着可对照，而不是覆盖后无从追溯（同 42/43/44/46 号 DDL 的做法）。
--   ② **成功与失败分字段**：重新生成失败时只写 `error`/`failed_at`，**不动 `brief`** ——
--      一次网络抖动不该把上一次已经生成的结论抹掉；屏幕于是能同时说清
--      「这份结论是哪天生成的」＋「最近一次重跑失败了，原因是什么」。
--   ③ `generated_at` 与 `input_fetched_at` 是两个时刻，不许并成一个：
--      材料是那一刻从公开源抓的（出网、会过期），结论是那一刻让模型写的。
--      公开源今天的价格、新闻明天就变了 ⇒ 屏幕上要说这份结论是按哪一刻的材料写的。
--   ④ `material_segments` / `material_missing` 必须落库：这份结论"看了哪些材料、每段取了几条、
--      哪段没读到"只有当时那份回执说得出。没有它，事后只剩一句"AI 说的"。
--      ⚠️ 它是**回执**不是证据：条数由组装侧当场算出并交回屏幕，模型看不到这段元数据。

create table if not exists analytics.stock_brief (
    security_code     text        not null,
    spec_version      text        not null,
    brief             text,
    brief_chars       integer,
    word_limit        integer,
    material_chars    integer,
    material_segments jsonb,
    material_missing  jsonb,
    model             text,
    ai_provider       text,
    prompt_slot       text,
    prompt_source     text,
    input_fetched_at  timestamptz,
    generated_at      timestamptz,
    error             text,
    failed_at         timestamptz,
    constraint stock_brief_pkey primary key (security_code, spec_version)
);

create index if not exists stock_brief_code_idx
    on analytics.stock_brief (security_code, generated_at desc);

comment on table analytics.stock_brief is
    '一只票一份 AI 公司说明（做什么/核心竞争力/想象空间/当前基本面，400 字以内）。'
    '一行 = (票, 材料口径版本) 的**最新一份**；重跑覆盖同一行。内容不是本机判据，也不入库算任何东西，'
    '存下来只为让任何一个入口打开这一屏都能看到同一份结论与它的生成时刻';
comment on column analytics.stock_brief.spec_version is
    '材料边界＋话术契约的版本（brief-1）。改版＝换号留旧行，不许原地覆盖，否则两版说法混在一格里无从对照';
comment on column analytics.stock_brief.brief is
    '模型交回的正文，原样存（页面渲染前只剥成对粗体标记，不改写、不摘要）。为空＝这一版还没成功生成过';
comment on column analytics.stock_brief.brief_chars is
    '存下来那一刻按 len() 数的字数（含标点与换行）。屏幕上的「400 字以内」比的是这个数，'
    '不是模型的自我声明；介于契约与容忍上限（契约 ×1.2，见 `stock_brief.OVER_TOLERANCE`）之间的照实入库并由界面报出实际字数，'
    '超过容忍上限的正文写入侧直接拒收，不进这一列';
comment on column analytics.stock_brief.word_limit is
    '生成那一刻生效的字数契约（来自后端一处常量）。留着它，日后契约改了还能说清"这份是按几字写的"';
comment on column analytics.stock_brief.material_chars is
    '喂给模型的材料有多少字（含每段小标题）。这条通道硬顶 8,000 字（超过就抛，不会静默截断）';
comment on column analytics.stock_brief.material_segments is
    '逐段回执数组：[{key,label,taken,total,truncated,note}]。'
    '`total` 是源本轮给出的条数、`taken` 是喂进去的条数 ⇒ "研报源给了 200 条、喂了 8 条"这件事必须在库里留着。'
    'note 说的是这段怎么读的（例：这个源只交标题与评级、正文不在返回里），不是给模型看的话';
comment on column analytics.stock_brief.material_missing is
    '本轮没读到的段名数组（查询失败／源空返回／接口没接上）。**空数组是真的没有缺，null 是没记下**，两者不许并成一件事';
comment on column analytics.stock_brief.model is
    '哪一个模型写的（AI 栏那份来源配置里带的 model）。没有它，"AI 说的"这句话就没有可追责对象';
comment on column analytics.stock_brief.ai_provider is
    '走的是哪家接入（订阅/API 网关/服务商）。密钥本身不入库，只留这个名字';
comment on column analytics.stock_brief.prompt_slot is
    '用的是哪一份话术（内置槽位 id 或自建模板 id）。话术归用户可改 ⇒ 结论必须能回溯到当时那一份';
comment on column analytics.stock_brief.prompt_source is
    '这份话术是"内置默认"还是"config.setting 里页面配的那份"（与读侧 effective() 同一句说法）';
comment on column analytics.stock_brief.input_fetched_at is
    '材料是哪一刻抓的（公开源即时值）。它比 generated_at 早，两个时刻都要摆得出';
comment on column analytics.stock_brief.error is
    '最近一次重跑失败的原因（原样，不含堆栈）。**成功一次就清空**；它不为空而 brief 也不为空时，'
    '屏幕说的是"这份结论还在、但最近一次重跑没成"';
comment on column analytics.stock_brief.failed_at is
    '那次失败发生在什么时候。与 error 同进同出，不单独留一个（只有时间没有原因等于没交代）';

grant select                 on analytics.stock_brief to ft_reader;
grant select, insert, update on analytics.stock_brief to ft_app, ft_etl;
