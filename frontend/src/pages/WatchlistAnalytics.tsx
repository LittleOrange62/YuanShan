import { RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { useNonce, useProbe } from "@/lib/probe";
import { GroupAnalyticsBoard, type Analytics } from "@/components/quant/GroupAnalyticsBoard";

/**
 * 原「自选组分析」独立屏（`/watchlist-analytics`）。
 *
 * 2026-10-14 用户裁定：这一屏的功能整块搬进「我的股票 · 分组自选」的**最上方**
 * —— 先分析分组的整体情况，再看组里的票。导航条目、功能清单卡片与路由一并撤掉。
 * 页面文件按用户当时选的那条留着（本项目不是 git 仓库，删掉就回不来），而现在只剩一层壳：
 * 表格、折线图、口径、缺席念法全在 `components/quant/GroupAnalyticsBoard.tsx` **那一份实现**里，
 * 这里不再长第二张同样的表（`frontend/test/sortKeys.test.ts` 钉这条纪律）。
 *
 * ⚠️ 这一屏**不再挂路由**：`router.tsx` 里没有 `/watchlist-analytics`，老书签由末尾那条
 * `{ path: "*", element: <Navigate to="/" replace /> }` 兜回首页。要读这一叠数请走 `/grouped`
 * —— 顶部第一块就是它，而且默认就聚合，不用再点按钮。
 */
export function WatchlistAnalytics() {
  const [nonce, refresh] = useNonce();
  const g = useProbe<Analytics>("/watchlist/group-analytics", nonce);

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="自选组分析（已并入分组自选）" level={1}
        subtitle="这一屏 2026-10-14 起不再挂路由：整块内容住在「我的股票 · 分组自选」的最上方。这个文件只留作对照，要读这些数请走 /grouped。"
        actions={<button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>} />

      <div className="space-y-4">
        <GroupAnalyticsBoard got={g} />
      </div>

      <Disclaimer compact />
    </div>
  );
}
