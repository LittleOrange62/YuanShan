# 页面展示工作指南

> 管：数据到了界面上的**呈现纪律** —— 可用性怎么标、单位和口径怎么随数交出、什么该打标、什么算造假。
> 不管数据从哪来（[数据获取](../数据获取工作指南/README.md)）、口径怎么算（[数据加工](../数据加工工作指南/README.md)）。

## 阅读顺序

| 文档 | 内容 |
|---|---|
| [01-数据口径展示规范.md](01-数据口径展示规范.md) | 十二条口径纪律 + 反面教材 + 新增端点的前端契约（§九 首页连板榜「封板强度」、§十 倍量榜跨屏引用：同场 join、缺数不补 0、反向概念榜的分母与宽窄、§十一 连板榜「首板日大盘情绪」：逐行按各自的日子读另一张表、§十二 倍量榜末尾「当日涨幅 / 15 日涨幅」：同一屏两把尺子与后复权） |
| [02-quantdash逻辑双轨展示.md](02-quantdash逻辑双轨展示.md) | **双轨并排**：本地口径 vs quantdash 口径怎么摆、三个 stage 列一起显示、`seal_time_available` 与 `leader_tie_break` 必须暴露、`deviation[]` 原样透出、接口契约 |

## 为什么这套纪律值得单独立一本指南

`D:\finance` 下同类项目对同一件事的处理差别极大：YuanShan（yuanshan）把"宁缺毋假"写成了纪律并有测试守着；
quantdash 在缺数时**编数据**（把昨日高一档的票整体下移填空白天、用代码哈希伪造封板时间、失败时生成"模拟股份600xxx"）；
TradingAgents 缺字段时输出 `+%`、`换手%` 这种**连 0 都不是的空值且不报错**。
差别不在 UI，在于**读者能不能从页面上看出来这个数不能信**。

## 现有实现的落点（YuanShan）

| 位置 | 承担什么 |
|---|---|
| `frontend/src/components/ui/Caliber.tsx` | 内嵌的"这个数怎么算的"口径说明 |
| `EmotionMetricsPanel.tsx` / `MarketFactsPanel.tsx` / `BreadthPanel.tsx` | 判 `available` + `reason`，`ratioOf` 强制同分母 |
| `frontend/src/lib/colors.ts` | **A股红涨绿跌**靠 token 反转实现，新组件必须走 `pctColor/barColor/countColor`，不得直写 `success/danger` |
| `lib/agent.ts` 的 `safeArray/safeRecord/finite` | 后端脏字段进不来 |
| `DataFreshnessBadge`（quantdash 的思路） | 值得借鉴的**唯一一件**：新鲜度/来源徽标，见 01 §五 |
