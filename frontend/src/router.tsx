// 本文件继承自上游 vibe-astock v1.1.0（Apache-2.0，见根 LICENSE），本机有实质性改动。
// 改动与归属清单：docs/上游改动与归属清单.md；本机自写部分：LICENSE.local.md。
import { createBrowserRouter, Navigate } from "react-router-dom";
import { StrategyBacktest } from "@/pages/StrategyBacktest";
import { MyStocks } from "@/pages/MyStocks";
import { Layout } from "@/components/layout/Layout";
import { AgentReview } from "@/pages/AgentReview";
import { FirstBoard } from "@/pages/FirstBoard";
import { AgentWeekly } from "@/pages/AgentWeekly";
import { ResearchNotes } from "@/pages/ResearchNotes";
import { Journal } from "@/pages/Journal";
import { Backtest } from "@/pages/Backtest";
import { QdCompare } from "@/pages/QdCompare";
import { SummaryBoard } from "@/pages/SummaryBoard";
import { Jobs } from "@/pages/Jobs";
import { Pipeline } from "@/pages/Pipeline";
import { Coverage } from "@/pages/Coverage";
import { Allocator } from "@/pages/Allocator";
import { Tdx } from "@/pages/Tdx";
import { Quadrant } from "@/pages/Quadrant";
import { SectorHeat } from "@/pages/SectorHeat";
import { VolumeDouble } from "@/pages/VolumeDouble";
import { VolumeBreakout } from "@/pages/VolumeBreakout";
import { Confluence } from "@/pages/Confluence";
import { Spike } from "@/pages/Spike";
import { TrendStocks } from "@/pages/TrendStocks";
import { Grouped } from "@/pages/Grouped";
import { Datasource } from "@/pages/Datasource";
import { Quality } from "@/pages/Quality";
import { Screen } from "@/pages/Screen";
import { DataProcess } from "@/pages/DataProcess";
import { System } from "@/pages/System";
// `WatchlistAnalytics`（原 `/watchlist-analytics`）2026-10-14 不再挂路由：整块「分组整体分析」
// 搬进「我的股票 · 分组自选」的最上方（用户：先分析分组的整体情况，再分析分组自选）。
// 表格/折线/口径那一份实现住在 `components/quant/GroupAnalyticsBoard.tsx`，由 `/grouped` 顶部调用；
// 页面文件留着（本项目不是 git 仓库），老书签由末尾 `path: "*"` 兜底回首页。

import { StockReview } from "@/pages/StockReview";
import { Intel } from "@/pages/Intel";
import { Settings } from "@/pages/Settings";

// basename 跟着构建时的 --base 走，这样挂在子路径下内部跳转才不会掉回站点根目录
export const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      // 「首页」就是原汇总看板（跨模块一屏看完），原工作台首页已取消。
      { path: "/", element: <SummaryBoard /> },
      { path: "/agent/review", element: <AgentReview /> },
      // 「盘面数据」不再是独立页（09-27 裁定整屏并入首页，见 `components/home/MarketPanorama.tsx`）。
      // 老书签/外部链接由下面的 `path: "*"` 兜底回首页，不报错、不留空页。
      { path: "/first-board", element: <FirstBoard /> },
      { path: "/heat", element: <AgentWeekly /> },
      { path: "/journal", element: <Journal /> },
      { path: "/notes", element: <ResearchNotes /> },
      { path: "/backtest", element: <Backtest /> },
      { path: "/qd-compare", element: <QdCompare /> },
      { path: "/pipeline", element: <Pipeline /> },
      { path: "/coverage", element: <Coverage /> },
      { path: "/allocator", element: <Allocator /> },
      { path: "/tdx", element: <Tdx /> },
      { path: "/quadrant", element: <Quadrant /> },
      // 「趋势评分」这一屏 2026-10-14 撤下（用户裁定：它本来就是针对个股的）——
      // 加权明细与窗口斜率并进 `/grouped` 的量化档案下方（`components/quant/TrendQuant.tsx`），
      // 「自选股历史新高」（清单级）也搬进那一页；35 项技术指标一直在 `/stock-data`。
      // 页面文件与 `/api/quant/scores`、`/api/quant/score-trend`、`/api/quant/new-high` 都留着
      // （新那两块就是靠它们取数）；老书签由末尾 `path: "*"` 兜底回首页。
      { path: "/sector-heat", element: <SectorHeat /> },
      { path: "/volume-double", element: <VolumeDouble /> },
      { path: "/volume-breakout", element: <VolumeBreakout /> },
      { path: "/confluence", element: <Confluence /> },
      { path: "/spike", element: <Spike /> },
      { path: "/trend-stocks", element: <TrendStocks /> },
      { path: "/grouped", element: <Grouped /> },
      { path: "/datasource", element: <Datasource /> },
      { path: "/quality", element: <Quality /> },
      { path: "/screen", element: <Screen /> },
      { path: "/data-process", element: <DataProcess /> },
      { path: "/system", element: <System /> },
      { path: "/jobs", element: <Jobs /> },
      { path: "/backtest-agent", element: <StrategyBacktest /> },
      // 「自选股」独立页保留（09-28 我一度把它并进「分组自选」，那是我没被要求就删页，已撤回）。
      // 「自选股」不再是独立页（09-28 裁定：本机 localStorage 那叠并入「分组自选」，
      // 由 Grouped 里 `<Watchlist embedded />` 渲染；`/my-stocks` 里那块嵌入照旧）。
      // 老书签由末尾的 `path: "*"` 兜底回首页。
      { path: "/my-stocks", element: <MyStocks /> },
      // 「持仓股」与「持仓自选」09-28 合成一项「重点关注（持仓）」= /my-stocks；
      // 旧地址留一条跳转，不丢书签、也不让同一本账有两个入口。
      { path: "/portfolio", element: <Navigate to="/my-stocks" replace /> },
      // 个股深度复盘（用户 2026-10-15 点名新做一屏，从明细/持仓「双击」带 `?symbol=` 进来）。
      // 「个股研究」`/stock-data` 与「多空辩论」`/agent/deepdive` 两屏合进本页：老书签不白屏，
      // 但地址里的 symbol 不跟着跳（react-router 的 Navigate 不保留查询串），落到本页的空 picker 上
      // 由人重新选一只 —— 页面里明写「不替你挑」。
      // 两个页面文件都留着，只是不再独立挂路由：StockData / DeepDive 由本页按同一份取数代码嵌入。
      { path: "/stock-review", element: <StockReview /> },
      { path: "/intel", element: <Intel /> },
      { path: "/stock-data", element: <Navigate to="/stock-review" replace /> },
      { path: "/agent/deepdive", element: <Navigate to="/stock-review" replace /> },
      { path: "/settings", element: <Settings /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
], { basename: import.meta.env.BASE_URL });
