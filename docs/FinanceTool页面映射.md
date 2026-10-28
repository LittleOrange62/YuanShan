# FinanceTool 14 个页面 → YuanShan 的逐页映射

> 用户 2026-09-25："把数据采集、加工和页面展示都集成进 YuanShan 系统里啊……
> **参照 financetool 把它的功能（尤其是展示出来的部分）都集成进来！相同的功能点就合并，
> 如果数据和展示的内容有所不同，那就多做一个菜单页面出来即可。**"
>
> 权威菜单来源不是代码目录，是 **`config.menu` 表**（14 行、4 组，YuanShan 用 `ft_reader` 直接读得到）。
> 本页逐行给出判定。每条判定都注明"合并/新增/不搬"的**依据**，不写"已有类似物"这种话。

## 判定汇总

| # | FT 菜单 | route | 分组 | 它展示什么 | YuanShan 判定 | 落点 |
|---|---|---|---|---|---|---|
| 1 | 复盘首页 | `/` | 复盘 | 当日盘面总览 | **合并** | 已有首页 + `/summary` 汇总看板 |
| 2 | 板块热度 | `/sector` | 复盘 | "今天谁在领涨、这股热度在积累还是退潮" | **新增页** —— YuanShan 的 `/heat` 是**涨停热度**（`/api/weekly`），板块热度只有个股维度的 `/api/hot-concepts`；**没有全市场板块热度榜** | `/sector-heat`（接口：`analytics.sector_heat_daily`+`ignition`+`sector_trend`，已派数据加工） |
| 3 | 个股详情 | `/stock` | 复盘 | "这只票现在什么状态" | **合并** | 已有 `/stock-data`；⚠️ 但 `/api/stock/indicators`（35 项技术指标）**运行时 200；**10-18 现查已由 `components/quant/IndicatorGrid.tsx` 消费**（挂在 `/stock-review`；`StockData.tsx` 里那句「此前一直 200 却零页面调用」就是这件事的收口记录）**，属"合并不完整"，必须补进这一页 |
| 4 | 筛选器 | `/screen` | 选股 | 5 种登记条件（日期价格比较 + N 日涨停 + 振幅…） | **新增页** —— YuanShan **完全没有筛选器** | `/screen`（条件在 `config.screen_rule`/`screen_preset`） |
| 5 | 自选股组分析 | `/watchlist` | 选股 | 观察/重点两组心智、三段表单 + 两张只读表、**组热度与组分数趋势曲线** | **拆两半**：读出来展示 = **合并**（我已交付 `/api/watchlist/groups`、`/api/watchlist/stocks`，45 组 1283 条实测通）；**组级热度/趋势分析 = 新增页** | 明细进 `/my-stocks`；组分析 → `/grouped` 顶部（端点 `/api/watchlist/group-analytics`，10-18 现查 `vr/app.py:1334`）|
| 6 | 全市场个股分析 | `/scores` | 选股 | 四象限（四维象限）+ 右上点阵 | **新增页** —— YuanShan 只有复盘用的"判对错×盈亏"归因象限，**没有选股象限** | `/quadrant`（我已交付 `/api/quant/quadrant`，实测 Q1 4/Q2 1625/Q3 16/Q4 3911） |
| 7 | 任务与调度 | `/jobs` | 配置 | 可运行/已自动/未启用 | **合并（且是补完）** —— YuanShan 的 `/jobs` 页**已存在 363 行但后端 404 是死页** | 后端 `/api/jobs` 已派系统配置 |
| 8 | 数据加工 | `/data` | 运维 | "拿到数据之后怎么变成分数"：d1~d7 提交与状态回显 | **新增页** —— YuanShan 侧无任何加工可见性 | `/data-process`（并入"数据链路"模块） |
| 9 | 数据获取 | `/datasource` | 运维 | 30+ 源逐个"现在能不能用/坏了/没权限"，可手动触发全量补齐 | **新增页** —— YuanShan 没有。⚠️ 我实测 `ft_reader` 对 `config.datasource` **有 SELECT**，直接读现成表即可，**不需要自建探测器** | `/datasource` |
| 10 | 调度器 | `/schedule` | 运维 | 什么时候该跑、为什么没跑、跑过什么 | **合并到 #7 一页多标签**（拆两页会让"为什么没跑"和"可不可运行"分处两地） | `/jobs` 的标签页 |
| 11 | 调度任务大全 | `/tasks` | 运维 | 39 条作业的登记与"填错会怎样" | **合并到 #7** | `/jobs` 的标签页 |
| 12 | 数据质量 | （`/data` 内标签） | 运维 | 24 条规则、"静默的数据错误"、缺几天逐天补 | **新增页** —— 语义正交于 YuanShan 的 `preflight`（那是**复盘前**体检、这是**入库后**校验），两套都留 | `/quality` |
| 13 | SQL 查询台 | `/sql` | 运维 | 手填 SQL 跑（校验是不是 select） | ❌ **不搬** —— 给一个只读成品项目开自由 SQL 入口，等于自己给自己放大权限；真要查就用库客户端 | 不搬，理由写进本表 |
| 14 | 系统管理 / 积木管理 | `/admin` `/blocks` | 运维/配置 | 端口访问控制；自研"积木城堡"动态拼页器 | ❌ **不搬** —— 访问控制属部署层；拼页器是 Dash 特化产物，YuanShan 是 React 固定路由，**能力上等价于"改代码"** | 不搬，理由写进本表 |

**净结果：合并 5 项、新增 8 页、合并到同页 2 项、明确不搬 3 项。**

## YuanShan 导航目标态

```
首页 · 汇总看板
盯盘 │ 复盘 │ 资讯雷达 │ 个股研究 │ 回测
量化选股   ← 新增模块：/quadrant 四象限 · /screen 筛选器 · /scores 评分明细 · /sector-heat 板块热度
                                            · /grouped 顶部 自选组分析（原 /watchlist-analytics 已撤）
数据链路   ← 新增模块：/datasource 数据获取 · /data-process 数据加工 · /quality 质量闸
                                   · /coverage 覆盖体检 · /tdx 通达信 · /pipeline 链路总览
我的股票   ← 接上 /api/watchlist/*（45 组 / 1283 条，已可用）；老 localStorage 一次性迁移
运维区     · /jobs（+ 调度器 + 任务大全 三个标签）· /settings · /qd-compare
```

## 三个"看着像重复、其实不是"的地方（按用户规则 c/d 都保留，且都要上页面）

| 二者 | 为什么不是重复 |
|---|---|
| `/heat`（已有，涨停热度）vs `/sector-heat`（板块热度） | 一个数的是**涨停家数热度**，一个数的是**板块资金/广度热度与点火阶段** |
| `attribution.quadrants`（已有，复盘）vs `/quadrant`（新增，选股） | 前者是"我判对没有 × 盈亏"，后者是"个股质量 × 环境共振"。语义正交 |
| `analytics.ignition` 五档阶段 vs qd 轨轮动矩阵 | 本轨回答"处在启动哪一段"，qd 轨回答"领涨换了几手、谁连庄"。已在 `/qd-compare` 双轨 |

## 现状进度（实测，不是计划）

| 需要的后端 | 状态 |
|---|---|
| `/api/watchlist/groups`、`/api/watchlist/stocks` | ✅ 我已交付，200，45 组 1283 条无截断 |
| `/api/quant/quadrant`、`/api/quant/scores` | ✅ 我已交付，200 |
| `/api/quant/score-trend`、`/api/quant/volume-double` | ✅ 数据加工已交付，200 |
| `/api/quant/sector-heat` | ✅ 我已交付，200。**默认 `stats=day`：进程冷启动 2.16 秒、之后 1.95 秒**（首屏用这个）；`stats=full` 现算三项全库聚合要 8.6 秒，算过一次进一小时缓存，之后默认请求顺带带出，`freshness.cached_or_absent` 写它是几秒前算的。慢的从来不是榜单——当日榜本身 0.35 秒 |
| `/api/quant/sector-trend` | ✅ 数据加工已交付计算与落库（`app/compute/sector_trend_signal.py` → `analytics.sector_trend_signal`，2026-09-26 全量重算 **543,844 行 / 1,244 场 / 564 板块 / `spec_version=sts-2`**，3 分 40 秒），读端 `sector_trend()` + 路由已由页面展示接上：实测 `?date=2026-09-23&limit=5` **2.13 秒**（冷启动）、`&regime=overheated` 1.08 秒、`?scope=group` 0.59 秒并如实回 `available=false`（成员快照只有 1 场，长窗口 21 场，实盘序列还没长出来）。⚠️ `overheated`=「在自己 21 日区间顶部」，**不是**「今天全场最热」，界面念这档要连 `reasons.heat_rank/universe` 一起念（口径纠正见《数据加工工作指南》08 坑 10） |
| `/api/jobs` | 🔄 已派系统配置（前端壳已就位，修好后端即活） |
| `/api/datasource` | 🔄 已派系统配置（`ft_reader` 实测可读 `config.datasource`，30 源健康态已登记） |
| `/api/coverage` | 🔄 已派数据获取 |
| `/api/quality`、`/api/data-process`、`/api/tdx`、`/api/pipeline` | ⏳ 未派单 |
| `/screen` 筛选器所需条件表读取 | ⏳ 未派单 |

## 两条硬纪律（本轮反复踩到）

1. **不许"合并"变成"少做一半"**：`/stock` 判合并，但 35 项技术指标接口存在却无页面调用 —— 这种就是**没合并完**。判定写"合并"的，必须逐字段核对页面上真的看得到了才算完。
2. **不搬的必须写理由**，不许沉默地略过。SQL 台与积木管理器我是**主动拒绝**，理由在上面表里 —— 不是"忘了搬"。
