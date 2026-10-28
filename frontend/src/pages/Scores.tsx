import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { StockPicker } from "@/components/ui/StockPicker";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { IndicatorGrid, useIndicators } from "@/components/quant/IndicatorGrid";
import { TrendQuantForPage } from "@/components/quant/TrendQuant";
import { useNonce } from "@/lib/probe";

/**
 * 趋势评分（旧屏，2026-10-14 起**不再挂路由**）。
 *
 * 用户那天的裁定：这一屏本来就是针对个股的，所以「八项能力加权明细 + 窗口斜率」并进了
 * 「我的股票 · 分组自选」—— 选中分组、点一格个股，量化档案下面接着就是这两块；
 * 「自选股历史新高」是清单级，也搬进了那一页；35 项技术指标一直在「个股数据」。
 * 文件与 `/api/quant/scores`、`/api/quant/score-trend` 都留着（新那两块就靠它们取数），
 * 这一页只作为老书签的落点保留 —— 渲染的都是同一批组件，不存在第二套口径。
 */
export function Scores() {
  const [nonce, refresh] = useNonce();
  const [code, setCode] = useState("");
  const inds = useIndicators(code, nonce);

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="趋势评分（已并入分组自选）" level={1}
        subtitle="这一屏不再挂导航：加权明细与窗口斜率就在「我的股票 · 分组自选」的量化档案下面，新高板也在那里。这里留着只为老书签有个落点。"
        actions={<button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>} />

      <div className="space-y-4">
        <StockPicker prefsKey="scores.last_code" code={code} onPick={setCode} />
        {!code && (
          <GlassCard>
            <p className="text-[12px] leading-relaxed text-muted-foreground">
              要看这一块就读 <b className="text-foreground">我的股票 · 分组自选</b>：
              选分组 → 点表里一格个股 → 档案下面就是这一只票的加权明细与窗口斜率。
              这一页不再默认给你看 000001 —— 那看着像「有数据」，其实是你没选过的票。
            </p>
          </GlassCard>
        )}
        {!!code && (
          <>
            <TrendQuantForPage code={code} nonce={nonce} />
            <IndicatorGrid state={inds} />
          </>
        )}
      </div>

      <Disclaimer compact />
    </div>
  );
}
