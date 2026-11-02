-- 58_allocator_medians.sql：把「当日中位数」这条**分界线本身**落库
--
-- 起因（#104，2026-10-30）：`/allocator` 屏幕上写着「分界线是当日中位数（不是写死的 50）」，
-- 而 `frontend/src/pages/Allocator.tsx` 那两条虚线画在 `x(50)`/`y(50)` 上，还标着「当日中位」。
-- 判据用的是当日中位数（`app/compute/allocator.py::quadrant_of`），落库的 `quadrant` 是对的，
-- **只有图上那两条线是假的** —— 因为中位数从前只在内存里用一次就丢了，界面拿不到那个数。
--
-- 这两列是「同一场所有行都相同」的当日标量，与已有的 `universe` 同一先例。
-- 老场次由**同一场的存量行**还原：`_median`（两个中间值取平均）与 `percentile_cont(0.5)` 是同一把尺，
-- 所以这一步不重算判据、不改 `quadrant`，只把当时那条线补写回去；写完当场自证（见文件末尾）。

alter table analytics.sector_allocator
  add column if not exists toll_median    numeric(10,6),
  add column if not exists imagine_median numeric(10,6);

comment on column analytics.sector_allocator.toll_median is
  '当日位置分中位数 = 象限图的 y 轴分界线。由写侧 `_median` 算出后随行落库，屏幕画的就是这个数（从前画在 50 上是假的）';
comment on column analytics.sector_allocator.imagine_median is
  '当日想象力分中位数 = 象限图的 x 轴分界线。与 `quadrant` 用的是同一个数，所以点与线的归属必须一致';

-- 回算老场次（幂等：重跑只会写回同一个数）
update analytics.sector_allocator a
   set toll_median = round(t.m::numeric, 6), imagine_median = round(i.m::numeric, 6)
  from (select trade_date, spec_version,
               percentile_cont(0.5) within group (order by toll_score) as m
          from analytics.sector_allocator
         where toll_score is not null
         group by 1,2) t
  join (select trade_date, spec_version,
               percentile_cont(0.5) within group (order by imagine_score) as m
          from analytics.sector_allocator
         where imagine_score is not null
         group by 1,2) i using (trade_date, spec_version)
 where a.trade_date = t.trade_date and a.spec_version = t.spec_version;

-- 自证：补回去的分界线必须与**已落库的 quadrant 归属**一致（不一致就是这把尺不是当时那把）。
-- 期望 0 行；非 0 就停下来查，不要把屏幕改成"看起来对"。
-- select count(*) as 归属与线不一致的行数
--   from analytics.sector_allocator
--  where toll_score is not null and imagine_score is not null
--    and ((toll_score >= toll_median) <> (quadrant in ('toll','consensus'))
--      or (imagine_score >= imagine_median) <> (quadrant in ('consensus','story')));
