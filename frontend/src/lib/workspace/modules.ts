// 本文件继承自上游 vibe-astock v1.1.0（Apache-2.0，见根 LICENSE），本机有实质性改动。
// 改动与归属清单：docs/上游改动与归属清单.md；本机自写部分：LICENSE.local.md。
import { Radio, Swords, LineChart, Briefcase, FlaskConical, LayoutDashboard, Database, Sigma, NotebookPen } from 'lucide-react';
// Primary navigation follows daily workflows; existing deep links remain valid.
export const MODULES = [
  // 首页就是原「汇总看板」那一屏（工作台首页已取消），所以入口图标用仪表盘而不是原来的房子。
  // 09-27 用户裁定：取消「盯盘」一级模块，实时动态 / 昨日梯队 / 盘中核验三页下线；
  // 「盘面数据」同日第二次裁定：**整屏并入首页，独立页与路由一并删除**（内容现在住在
  // `components/home/MarketPanorama.tsx`，占掉首页原「板级梯队」那块的位置），所以导航、
  // features 清单、首页底部链接三处都不再指向 `/daily-review` —— 老书签由路由兜底回首页。
  { title: '首页', to: '/', icon: LayoutDashboard, pages: [{ to: '/', title: '首页' }]},
  // 汇总看板已改名成首页（上面那条），不再单列一个入口 —— 一个页面两个导航项会让人以为有两处数据
  // 数据链路：回答"今天的数据到位了吗、卡在哪一环"。这一组页面本身不算指标，
  // 只把各环的落库日期与缺口摆出来 —— 判据是数据日期，不是"上次跑过"。
  // 顺序照映射表：先"源能不能用"，再"拿到之后算到哪一天"，再"入库后坏没坏"，再"缺哪几天"，最后才是"整体进度"。
  //  landing 选 /datasource 而不是 /pipeline —— 后者四环里两环还没接上，进来先看到半屏契约没意义。
  { title: '数据链路', to: '/datasource', icon: Database, pages: [
    { to: '/datasource', title: '数据获取' }, { to: '/data-process', title: '数据加工' },
    { to: '/quality', title: '质量闸' },
    { to: '/coverage', title: '覆盖体检' }, { to: '/tdx', title: '通达信控制' },
    { to: '/pipeline', title: '链路总览' },
  ] },
  // 量化选股：FinanceTool 那套打分/象限/热度/形态的呈现位。与「复盘」里的
  // 归因四象限是两件事（一个判"该不该买"，一个判"看对了没"），两边都保留、各自标口径。
  { title: '量化选股', to: '/quadrant', icon: Sigma, pages: [
    { to: '/quadrant', title: '四维象限' }, { to: '/screen', title: '筛选器' },
    { to: '/sector-heat', title: '板块热度与点火' },
    { to: '/volume-double', title: '倍量形态' }, { to: '/volume-breakout', title: '突破信号' },
    { to: '/confluence', title: '选股汇总' }, { to: '/spike', title: '异动筛查' },
    { to: '/trend-stocks', title: '趋势股' },
    { to: '/allocator', title: '分配者视角' },
    // 「自选组分析」2026-10-14 从「量化选股」这一组里撤掉：整块搬进「我的股票 · 分组自选」的**最上方**
    // （用户：先分析分组的整体情况，再分析分组自选）。判据与表格实现住在
    // `components/quant/GroupAnalyticsBoard.tsx`，功能点文档跟着搬到 `我的股票/分组整体分析.md`。
  ] },
  { title: '复盘', to: '/agent/review', icon: Swords, pages: [
    // 2026-10-26 用户裁定：「首板分析」改名「日间复盘及首板」（这屏上半挂盘中事件流、下半是首板样本）。
    { to: '/agent/review', title: '复盘报告' }, { to: '/first-board', title: '日间复盘及首板' },
    { to: '/heat', title: '近5天热度' }, { to: '/backtest', title: '历史统计' },
    { to: '/qd-compare', title: 'quantdash 口径对照' },
  ] },
  { title: '资讯雷达', to: '/intel', icon: Radio, pages: [{ to: '/intel', title: '资讯雷达' }] },
  // 2026-10-15 用户裁定：把「个股研究」与「多空辩论」两屏**合**成一个个股深度复盘页
  // （`/stock-review`：档案 · 趋势量化 · 窗口趋势 · 公开资料 · 多空辩论集中在同一屏，可从明细双击带代码开新窗口）。
  // 老地址 `/stock-data`、`/agent/deepdive` 在 `router.tsx` 里留跳转，不再占导航条目 ——
  // 两处入口指同一份内容会让人以为库里是两套数。
  { title: '个股研究', to: '/stock-review', icon: LineChart, pages: [
    { to: '/stock-review', title: '个股深度复盘' },
  ] },
  { title: '回测', to: '/backtest-agent', icon: FlaskConical, pages: [{ to: '/backtest-agent', title: '回测' }] },
  { title: '我的股票', to: '/my-stocks', icon: Briefcase, pages: [
    { to: '/my-stocks', title: '重点关注（持仓）' },
    { to: '/grouped', title: '分组自选（含本机名单）' },
    { to: '/notes', title: '研究记录' },
  ] },
  // 交易日志 09-28 从「我的股票」里拿出来独立成一级模块（用户裁定），排在它后面。
  // 复盘笔记与提醒**不另开一级模块**：同一件事的三个视图挂在这一页的三个 tab 上
  // （`pages` 只有一条，所以 tab 用 `?tab=` 而不是路由 —— 挂两条导航条目会指向同一个路由，
  //  而那条测试要求"每个导航条目都有注册路由"）。
  { title: '交易日志', to: '/journal', icon: NotebookPen, pages: [
    { to: '/journal', title: '交易日志（流水 · 复盘笔记 · 提醒）' },
  ] },
];
export function navigationPath(pathname: string) {
  // 这里刻意"原样返回"：以前要靠映射把 `/portfolio` 之类高亮回「我的股票」，
  // 那是给"页面写了没人挂导航"擦屁股。09-28 之后 `/portfolio` 只剩一条跳转
  // （`router.tsx` 里 Navigate 到 `/my-stocks`），真实页面都在 MODULES 里有入口，
  // 所以不再需要任何别名映射 —— 加映射等于允许孤儿页存在。
  return pathname;
}
export function moduleFor(pathname: string) {
  return MODULES.find(module => module.pages.some(page => page.to === navigationPath(pathname)));
}

// These legacy identities are persisted in conversation records and browser recovery keys.
// Keep them stable when navigation labels change; never migrate by display title.
const CHAT_PAGES: Record<string, string> = {
  '/notes': '研究记录',
  '/': '首页', '/intel': '资讯雷达', '/agent/review': '复盘看板',
  '/daily-review': '盘面数据',
  // 2026-10-26 用户裁定页面改名「日间复盘及首板」，这一条跟着改（用户点名的第二处登记点）。
  // ⚠️ 本文件顶部那条"标识要稳"的纪律在这里被有意让了一步：会话按 page 名在服务端与
  // localStorage 里归档，改名后**旧会话仍挂在 `首板分析` 那个名下**、新屏默认看不到，
  // 需要时按旧名检索。路由 `/first-board` 与判据都没动。
  '/first-board': '日间复盘及首板', '/heat': '近5天热度',
  '/backtest': '涨停样本统计', '/watch': '盯盘', '/agent/intraday': '盘中核验',
  '/stock-data': '个股数据', '/agent/deepdive': '个股深挖',
  // 2026-10-15 新页（上面两条老地址从此只做跳转，会话标识照旧留着，别删 ——
  // 历史记录与浏览器恢复键里存的就是这两个 pathname）。
  '/stock-review': '个股深度复盘',
  '/portfolio': '持仓股', '/watchlist': '自选股', '/journal': '交易日志',
  '/settings': '接入 AI', '/my-stocks': '我的股票',
  '/yesterday-ladder': '昨日梯队', '/backtest-agent': '策略回测',
  '/qd-compare': 'quantdash 口径对照',
  '/pipeline': '链路总览', '/coverage': '覆盖体检', '/tdx': '通达信控制',
  '/quadrant': '四维象限', '/sector-heat': '板块热度与点火',
  '/volume-double': '倍量形态', '/volume-breakout': '突破信号', '/confluence': '选股汇总', '/spike': '异动筛查', '/trend-stocks': '趋势股', '/grouped': '分组自选', '/allocator': '分配者视角',
  '/datasource': '数据获取', '/quality': '质量闸', '/screen': '筛选器',
  '/data-process': '数据加工', '/system': '系统配置', '/jobs': '任务调度',
  // '/watchlist-analytics' 的标题映射 2026-10-14 一并撤掉：路由没了（整块搬进 /grouped 顶部），
  // 留着一个到不了的 pathname 标识只会让"这一屏还在不在"重新变成要靠读代码才能回答的问题。
};
export function chatPageFor(pathname: string) { return CHAT_PAGES[pathname] ?? '工作空间'; }
