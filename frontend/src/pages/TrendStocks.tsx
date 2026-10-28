import { Disclaimer } from "@/components/ui/Disclaimer";
import { PageHeader } from "@/components/ui/PageHeader";
import { TrendStockBoard } from "@/components/quant/TrendStockBoard";

/**
 * 趋势股（`/trend-stocks`）。
 *
 * 一屏只做一件事：把「近 5 或 10 个交易日每一个交易日收盘都严格站上当日 5 日线、
 * 而且当天没有封涨停」的那批票，按主板／创业板／科创板分三组摆出来。
 *
 * 判据、落库、每日加工段都不在这一层（只住 `app/compute/trend_stock.py`，
 * 加工段步骤名 `trendstock`）；读法只住 `lib/trendStocks.ts`；这一层连字段名都不重新解释一遍。
 * 与板块卡片里那块「趋势强势股」的差别（固定 5 天、可以含当天涨停）由组件里那句说明框说，
 * 不在标题里含糊。
 */
export function TrendStocks() {
  return (
    <div className="space-y-3">
      <PageHeader
        title="趋势股"
        subtitle="近 5 或 10 个交易日每个交易日收盘都严格高于当日 5 日线 · 当天没封涨停 · 按主板／创业板／科创板分组"
      />
      <TrendStockBoard />
      <Disclaimer />
    </div>
  );
}
