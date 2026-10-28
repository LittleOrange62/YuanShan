-- -*- coding: utf-8 -*-
-- 52_qd_sector_follow.sql
-- 首页「轮动矩阵与持续性（quantdash 口径轨）」下面那一块：当日前三甲 · 它们前五场名次 ·
-- 与它们轨迹最像的板块。用户 2026-10-25 点名要这三件事，2026-10-25 晚裁定改为**算好落库**
-- （原先在读侧现算 + 一小时缓存，一句 SQL 约两秒，首屏每次都可能付这两秒）。
--
-- 为什么这一族非得落一张新表，而不是复用 qd.sector_rotation：
--   rotation 每天只落 day_rank<=8（它自己的 L-16 条偏差就写着这事）。而"谁现在走的位置像
--   领涨板块两天前走的位置"要把**全母体**（实测 914~916 个通达信概念/行业板块）逐场名次都拿到，
--   第 8 名之外在 rotation 里根本查不到 ⇒ 读侧只能回到 market_data.sector_daily 重排。
--   重排这件事每天只做一次、算完就是死的，放在读侧等于每个访客重付一遍。
--
-- 四条结构纪律（都有反面例子）：
--   ① 主键带 `spec_version`：排名母体、脏数据守卫、错开档、榜长任一口径改版＝换号，
--      旧行留着可对照。号由写侧 `'follow-' + qd_common.spec_version()` 拼出（当前 follow-qd-1），
--      所以全局 qd 版本一升，这一轨跟着升 —— 不存在"rotation 换了版、follow 还在读旧批次"。
--   ② `lag` 进主键：0/1/2/3 四档一起落（接口 /api/qd/sector-follow 的 lag 参数本来就开这四档，
--      只落一档就会让另外三档"参数能填、永远读不到行"）。同一天四档并存互不覆盖。
--   ③ 名次与涨幅**缺就是 null，不折成 0**：`trajectory` 里某天没有行情就交 rank=null，
--      界面显示 `—`。折成 0 会被读成"它当时排第 0 名"，而 0 恰好是名次判据里最抢眼的值。
--      `similar.items` 里那些"这几天缺行"的板块根本不进榜，只进 `skipped_incomplete` 计数 ——
--      缺行不等于不像，把它当不像混进榜，榜就变成"在有数据的那批里挑最像"，那是另一件事。
--   ④ 分母随数据交出：`universe_size`（当日参与排名的板块数）与 `similar.candidate_pool`
--      （这几场里出现过行的板块并集，可比当日母体大）是两个数，逐行落库；
--      只交一个"母体"就会让"占母体 %"与"比对了几个人次"互相冒充。

create table if not exists qd.sector_follow (
    trade_date        date        not null,
    lag               smallint    not null check (lag between 0 and 3),
    sector_code       text        not null,
    spec_version      text        not null,
    calculated_at     timestamptz not null default now(),
    source            text        not null check (source in ('quantdash-logic','quantdash-logic+local')),
    deviation         jsonb       not null default '[]'::jsonb,
    sector_name       text,
    sector_type       text        check (sector_type in ('概念','行业')),
    day_rank          smallint    not null check (day_rank > 0),
    pct_change        numeric(8,4),
    universe_size     smallint    check (universe_size > 0),
    top_n             smallint    not null check (top_n > 0),
    trajectory_points smallint    not null check (trajectory_points > 0),
    shape_points      smallint    not null check (shape_points > 0),
    k_similar         smallint    not null check (k_similar > 0),
    sessions          jsonb       not null,
    trajectory        jsonb       not null,
    similar_block     jsonb       not null,
    constraint sector_follow_pkey primary key (trade_date, lag, sector_code, spec_version)
);

comment on table qd.sector_follow is
  '一行 = (基准日, 错开档 lag, 某个当日领涨板块, 口径版本) 的整块读数：它的前五场名次轨迹，'
  '以及全母体里轨迹与它最像的 k_similar 个板块。逻辑只住 app/compute/qd_sector_follow.py 一处，'
  '读侧 duanxian/qd_follow.py 只读行不重算。排名母体＝market_data.sector_daily 里通达信 TDX_ 一族、'
  'sector_type 为概念或行业的板块，与 qd.sector_rotation 同一条尺（同一份 SQL 常量）。'
  '形状匹配（错开若干场、平均名次差、秩相关）是本机自写判据，quantdash 没有这一族。';

comment on column qd.sector_follow.lag is
  '模板相对基准日错开几场。lag=2 是用户 2026-10-25 按自己写的日期选的口径：'
  '模板取领涨板块 t-2、t-3、t-4、t-5 四个名次，候选取它自己 t、t-1、t-2、t-3 四个名次，'
  '回答的是「谁现在走的位置，像领涨板块两天前走的位置」。0/1/2/3 四档全部落库（接口只开这四档）。';

comment on column qd.sector_follow.day_rank is
  '该领涨板块在基准日**全母体**里的名次（不是 rotation 的前 8 位次，两边同一把尺算出，'
  'tests/test_qd_follow.py 拿落库行逐名反查）。并列涨幅按 sector_name、sector_code 定序，'
  '所以第 3 名与第 4 名换了顺序是真实变化，不是随机。';

comment on column qd.sector_follow.universe_size is
  '基准日参与排名的板块数（剔掉无名板块与|涨幅|越界的脏行之后）。实测 2026-10-23 为 914，'
  '全史范围 799~916。它是「占母体 %」那一列的分母，与 similar.candidate_pool 是两个数。';

comment on column qd.sector_follow.top_n is
  '当天取前几名各落一行（当前 3 = 领涨那一名 + 用户点名要的后面两名）。契约值随批次落库，'
  '日后调档还能说清"这一批是按几名写的"，不必猜。';

comment on column qd.sector_follow.trajectory_points is
  '轨迹回看几场（当前 5，不含基准日那场）。契约值随批次落库，同上。';

comment on column qd.sector_follow.shape_points is
  '参与形状比对的名次个数（当前 4）。模板取 t-lag 起连续 shape_points 场，'
  '候选取 t 起同样个数；模板不齐 ⇒ items 交空数组且 incomplete_template=true，'
  '不给一张空榜冒充"没有相似板块"。';

comment on column qd.sector_follow.k_similar is
  '每位领涨板块交出的相似板块条数上限（当前 6）。榜按平均名次差升序，'
  '距离并列时才比秩相关（None 排最后，不当 0）。';

comment on column qd.sector_follow.sessions is
  '这一批实际用到的场次序列（ISO 日期数组，下标 0 = 基准日）。留着它，事后才能核对'
  '"模板那四个名次到底是哪四天"，也才能发现某一批是在缺日窗口上算的。';

comment on column qd.sector_follow.trajectory is
  '[{trade_date, rank, pct_change}]，长度 = trajectory_points（缺场时更短）。'
  'rank=null = 那天这个板块没有行情行，界面显示 —；**不是第 0 名，也不是没涨没跌**。';

comment on column qd.sector_follow.similar_block is
  '列名叫 similar_block 而不叫 similar：SIMILAR 是 PostgreSQL 的保留字（SIMILAR TO），'
  '不做引号就建不出来。接口交出的键仍是 similar（前端契约不变），只有库里的列名换字。'
  '内容：{template, template_days, candidate_days, candidate_pool, matched,'
  ' skipped_incomplete, incomplete_template, items:[{sector_code, sector_name, sector_type,'
  ' current_rank, gap_mean_rank, gap_pct_of_universe, rank_corr, candidate_ranks, template_ranks}]}。'
  'items 只在"这一场这一档这个领涨板块的相似榜"里被整叠读走，没有按板块反查的需求 ⇒ 不另立一张表。'
  'candidate_pool 是候选池（这几场出现过行的板块并集，减掉领涨板块自己），'
  'matched + skipped_incomplete 必须正好等于它 —— 少一个就是有一边没算进来。';

comment on column qd.sector_follow.deviation is
  '本行的偏差数组，与 qd 轨同一套编号：L-22 窗口跨母体扩容（各日参与排名的板块数偏离窗口众数'
  '超两成 ⇒ 跨日名次不可比，实测断点 2026-09-23 行业 188→561）；L-23 候选池与当日母体是两个分母；'
  'L-24 模板缺场次 ⇒ 整块不比对。没有就是空数组，不是 null。';

comment on column qd.sector_follow.calculated_at is
  '这一批算完落库的时刻。界面上的"本批算于 …"读的是它，不是请求时刻 —— '
  '落库之后每一屏看到的都是同一批，年龄要如实交出。';

grant select                 on qd.sector_follow to ft_reader;
grant select, insert, update on qd.sector_follow to ft_app, ft_etl;
