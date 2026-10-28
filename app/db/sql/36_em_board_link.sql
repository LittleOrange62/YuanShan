-- -*- coding: utf-8 -*-
-- 36_em_board_link.sql
-- 板块名 → 东方财富板块页（BK 码）的对表。屏幕上每一个板块名能不能点开东财那一页，只看这张表。
--
-- 为什么要这张表（用户 2026-10-15 点名：「热度榜与五要素 里面的板块也要有超链接可以点开，
--   比如『油气开采』对应 https://quote.eastmoney.com/bk/90.BK1276.html，多测试几个把这个功能完善」）：
--   本机的板块名有三套体系，彼此**没有码级通道**：热度榜母体是通达信（行业 502 / 概念 268 /
--   风格 128 / 市场级统计 30），「覆盖以来累计净流入」那张表是同花顺行业榜（90 个），
--   而用户给的那个例子是**东财自己的板块码**（90.BK1276）。要发外链就必须先回答
--   「这一个名字在东财对应哪一个板块页」，而这个问题只能拿东财的清单去对。
--
-- 东财清单从哪来（2026-10-15 实测，三条路只有这一条通）：
--   · `push2.eastmoney.com/api/qt/clist/get`、`push2delay…/clist/get` —— 本机连不上
--     （ConnectionError，与 `06-跨项目数据源对照` 里那条「push2 clist 已被封」一致）；
--   · `searchapi.eastmoney.com/api/suggest/get` —— 更阴：HTTP 200、`rc=0`，但对任何 input
--     都回同一段 2023 年的 JSONP 缓存（`input=油气开采/银行/半导体` 三次返回逐字节相同、
--     `searchId` 也一样）。**这不是"这个板块没有"，是这条接口在这台机器上已经不回真数**，
--     拿它做对表会静默造出一张全是 miss 的表；
--   · ✅ `data.eastmoney.com/dataapi/bkzj/getbkzj?code=m:90+t:2`（行业 496 个）与
--     `code=m:90+t:3`（概念 506 个）—— 两发请求拿到全量，字段就是 `f12`（BK 码）+ `f14`（名）。
--     实测两个清单内部**名字唯一**（496 行 496 个唯一名、506 行 506 个唯一名），
--     所以「按名字精确同名」这一步不存在多义，不需要挑一个。
--
-- 对表规矩（三条，一条都不许放宽）：
--   1. **同类才对表**：我方「行业」只查东财行业榜、我方「概念」只查东财概念榜。
--      跨类同名（东财概念里确实有个『白酒』BK0896，而我方那是行业档）一律不链 ——
--      名字撞上不等于说的是同一件事，链错了点开是另一个族。
--   2. **只认精确同名，再加一条罗马后缀**：先按屏上名字精确比；比不上再试「名+Ⅱ」「名+Ⅲ」
--      （东财把二级/三级行业写成『油气开采Ⅱ』，用户举的例子正是这一族：
--      实测 `油气开采 → BK1276`，页面标题 `油气开采Ⅱ(BK1276)_行情中心_东方财富网`）。
--      Ⅱ 优先于 Ⅲ（二级比三级更接近我方「行业」那一档）。除此之外没有任何模糊规则 ——
--      `09_ths_reference` 那条纪律同样适用这里：按名字猜等于造数。
--      东财侧真实叫什么**必须落 `em_board_name` 交到屏幕上**，点开的页眉是那个名字，不能藏。
--   3. **对不上要留痕**：`match_kind='miss'` 的行照样写（试过、没对上），
--      与"这颗名字从来没被试过"是两件事。代码留 NULL，界面退回纯文本 ——
--      少一个链接可以接受，拼一条死链不行。
--
-- 命中率实测（2026-10-15，按上面两条规则现算，别背这个数字，每天随清单变）：
--   同花顺行业榜 90 个 → 命中 73（精确 56 + 罗马 16 + 跨类 1，跨类那条已按规则 1 撤掉）；
--   通达信行业 502 个 → 命中 286（精确 248 + 罗马 33 + 跨类 5，同上）；
--   通达信概念 268 个 → 命中 158（精确 151 + 跨类 7）；
--   风格 128 / 市场级统计 30 两档**刻意不对表**：东财没有对应的族，硬对只会产出一屏假链接。
--
-- 谁写：`ingest/jobs/c24_em_board_link.py`（每日加工段 `boardlink` 那一步，两发请求、秒级）。
-- 谁读：`duanxian/quant.py::sector_heat()` 与 `duanxian/sector_flow.py::aggregate()` 左连这张表，
--       把 `em_board_code` 一并交出；前端 `components/ui/SectorLink.tsx` 一份实现决定点不点得开。
-- 主键不含 trade_date：这是"名字→页面"的对表，不是逐日读数。`tried_at` 就是新鲜度。

create table if not exists reference_data.em_board_link (
  sector_kind   text    not null,        -- 我方口径：'行业' / '概念'。风格与市场级统计不进这张表
  sector_name   text    not null,        -- 屏上那个名字（通达信体系名 / 同花顺行业名）
  em_board_code text,                    -- 东财板块码 BK1276；地址是 https://quote.eastmoney.com/bk/90.<码>.html
  em_board_name text,                    -- 东财那一侧的名字（油气开采Ⅱ）—— 点开的页眉就是它，必须交出去
  match_kind    text    not null,        -- exact / roman_ii / roman_iii / miss
  source        text    not null,        -- 哪个接口对的表（换源要能查是谁给的）
  tried_at      timestamptz not null default now(),

  primary key (sector_kind, sector_name)
);

comment on table  reference_data.em_board_link is
  '板块名 → 东方财富板块页（BK 码）的对表。同类才对表、只认精确同名与 Ⅱ/Ⅲ 后缀、对不上留 miss 行。'
  '每天由加工段 boardlink 步刷新（两发请求），页面按它决定板块名是链接还是纯文本。';
comment on column reference_data.em_board_link.sector_kind   is '我方那一屏的口径：行业 / 概念。东财行业榜与概念榜是两张清单，跨类同名不链';
comment on column reference_data.em_board_link.em_board_code is '东财板块码（BK1276）。NULL = 没对上，界面退回纯文本，不拼死链';
comment on column reference_data.em_board_link.em_board_name is '东财侧真名（油气开采Ⅱ）。罗马后缀那条规则换来的链接必须把这个名字交出去';
comment on column reference_data.em_board_link.match_kind    is 'exact=屏上名与东财名逐字相同；roman_ii / roman_iii = 差一个级别后缀；miss = 试过没对上';
comment on column reference_data.em_board_link.tried_at      is '这一行最近一次被解析的时刻。屏幕要说"对表是哪天做的"，不能说成实时';

grant select, insert, update, delete on reference_data.em_board_link to ft_etl;
grant select on reference_data.em_board_link to ft_app, ft_reader;
