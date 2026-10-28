-- 工单：更正 analytics.ignition.term_count 的列注释（YuanShan 侧写好，请总协调用 FT 的 admin DSN 执行）
--
-- 为什么要改：这句注释正在被下游引用并给出**错的数字**。
--   原文：「当日五个要素分中有值的个数（0~5）。230个行业板块无涨跌家数最多4项，故永远不会判为 ignite」
--   2026-09-25 实测（复核 SQL 见文件末尾，可直接重跑）：
--     · 行业板块是 **296** 个，不是 230 个（行业分类扩过三级；09-23 那天 sector_daily 里
--       一下多出 381 个 `TDX_881xxx` 三级行业行，而全库只有那一天有）
--     · 行业共 229,221 行记录，其中只有 **1,086 行** `breadth` 非空 —— 分散在 **12 个场次**，
--       不是"集中在三天"：08-27 前每场 18 个板块有值、9 月起 103 个、09-23 才 293 个
--     · 行业里有 **406 行 `term_count = 5`**，所以"最多 4 项"已经不成立
--     · "行业从来没有 ignite"这条结论**仍然成立**：历史 `stage='ignite'` 行业 0 次、
--       概念 330 次（138 个板块）、风格 318 次（64 个板块）
--   也就是说：注释的结论对、数字与机理都不对。而"数字不对"是有代价的 ——
--   页面/接口若照注释写"230 个行业板块"，就是一个可被证伪的假事实挂在成品上。
--
-- 改法取向：不塞新常数。把"具体数字"从注释里拿掉、只留**结论 + 去哪拿现值**，
-- 因为注释不会随回填更新，而任何写进注释的数字迟早再过期一次。

comment on column analytics.ignition.term_count is
  '当日五个要素分中有值的个数（0~5）。空要素不计权重，所以这一列本身就是"这条信号建不建得起来"的分母。'
  '已知结构性缺口：行业板块的涨跌家数（md.sector_daily.up_count）大面积没采到，广度项因此算不出，'
  '行业历史上从未被判为 ignite —— 这是"算不出"，不是"那天没轮动"。'
  '具体家数/行数会随回填变化，故不写进注释；要当下的数以 /api/quant/sector-heat?stats=full 返回的 '
  'breadth_reality 为准，或用本文件末尾那两条复核 SQL 直接查。';

-- 顺带一句不用改注释、但要知道的事：sector_daily 里 49 个"名字就是编号"的板块
-- （TDX_880004 的 name 是 '880004'）在热度榜上排得很靠前（09-22 实测第 2）。
-- 这是数据侧的活（板块名册补全），不属本工单，读接口已用 unnamed 标记 + 母体计数交出。

-- ─── 复核 SQL（任何人可以重跑，跑完再决定注释该怎么写）───────────────────
-- 1) 各类型板块的广度覆盖与点火次数
--    select coalesce(si.sector_type,'(未登记)') 类型,
--           count(distinct i.object_key) 板块数,
--           count(*) 行数, count(i.breadth) 有广度值的行,
--           max(i.term_count) 最大要素数,
--           count(*) filter (where i.term_count=5) 满五项的行,
--           count(*) filter (where i.stage='ignite') 点火行
--      from analytics.ignition i
--      left join reference_data.sector_info si on si.sector_code = i.object_key
--     where i.scope='sector' group by 1 order by 3 desc;
--
-- 2) 行业板块有广度值的那些行，落在哪几个场次
--    select i.trade_date, count(*) 行, count(distinct i.object_key) 板块
--      from analytics.ignition i
--      join reference_data.sector_info si on si.sector_code = i.object_key
--     where i.scope='sector' and si.sector_type='行业' and i.breadth is not null
--     group by 1 order by 1 desc;
