// Research v1.1.0 compact category structure; YuanShan routes remain authoritative.
import { Flame, Layers, CalendarRange, LayoutDashboard, Swords, LineChart, Radio, Briefcase, NotebookPen, TrendingDown, TrendingUp, Cog, Workflow, MonitorCog, Sigma, FolderTree, Scale, ListChecks, Zap } from 'lucide-react';
export const FEATURE_GROUPS = [
  { title: '首页与复盘', features: [
    { to: '/', title: '首页（汇总看板）', icon: LayoutDashboard, detail: '盯盘、情绪梯队、板块资金并到一屏；不新算指标、不合并口径' },
    { to: '/agent/review', title: '复盘看板', icon: Swords, detail: '完整复盘、依据与报告历史' },
    // 「盘面数据」不再单列：09-27 裁定整屏并入首页（`components/home/MarketPanorama.tsx`），
    // 再摆一个入口等于指到一个已经删掉的页面。
    // 2026-10-26 用户裁定：「首板分析」改名「日间复盘及首板」，并把盘中涨跌停事件流挂在这屏上半部分
    // （路由 `/first-board` 不变，老书签照用）。detail 要说清上下两半各是什么，只写「首板样本」会漏掉新那半。
    { to: '/first-board', title: '日间复盘及首板', icon: Flame, detail: '上半是盘中涨跌停事件流（每 5 分钟一针的逐条事件差）、下半是首板样本与结构' },
    { to: '/heat', title: '近5天热度', icon: CalendarRange, detail: '已有归档的热度变化' },
  ] },
  { title: '标的与资讯', features: [
    // 2026-10-15 用户裁定：「个股研究」与「多空辩论」两屏合成一屏「个股深度复盘」，
    // 这张卡（也就是主界面那个「个股研究」按钮）**不带传参**进新页 —— 带代码的那一路走明细双击开新窗口。
    { to: '/stock-review', title: '个股深度复盘', icon: LineChart, detail: '一只票的档案、趋势量化、公开资料与多空辩论集中一屏' },
    { to: '/intel', title: '资讯雷达', icon: Radio, detail: '公开资讯与行业线索' },
  ] },
  { title: '记录与验证', features: [
    { to: '/my-stocks', title: '重点关注（持仓）', icon: Briefcase, detail: '持仓与关注标的，本机私人记录（原「持仓股」已并到这一页）' },
    { to: '/notes', title: '研究记录', icon: NotebookPen, detail: '已保存的分析，支持搜索与导出' },
    { to: '/journal', title: '交易日志', icon: NotebookPen, detail: '流水 · 复盘笔记 · 关注提醒，三个 tab 同一屏；私人记录，不自动进入聊天' },
    { to: '/backtest', title: '涨停样本统计', icon: TrendingDown, detail: '历史样本统计，不是完整收益回测' },
  ] },
  { title: "数据与选股", features: [
    { to: "/pipeline", title: "链路总览", icon: Workflow, detail: "每一环最后成功到哪天、卡在哪" },
    { to: "/coverage", title: "覆盖体检", icon: CalendarRange, detail: "各类数据覆盖区间与缺的日子" },
    { to: "/tdx", title: "通达信控制", icon: MonitorCog, detail: "客户端状态、自动动作与待人工清单（系统自己点的那几下也在这）" },
    { to: "/quadrant", title: "四维象限", icon: Sigma, detail: "质量×环境 与 判对错×盈亏 两套并排" },
    { to: "/sector-heat", title: "板块热度与点火", icon: Flame, detail: "钱今天实际去了哪些板块" },
    { to: "/volume-double", title: "倍量形态", icon: TrendingUp, detail: "放量倍数与离 MA60 的距离；同一屏还挂启动前量能画像（四类母体）、首板形状预示、启动前象限轨迹（默认全部首板，不筛结果）、启动前爆量与突然拉高" },
    { to: "/volume-breakout", title: "突破信号", icon: TrendingUp, detail: "缩量收敛后首次突破 MA60 + 放量" },
    { to: "/confluence", title: "选股汇总", icon: Layers, detail: "各条链当天选出的票并到一起，看谁被多个判据重复点到" },
    { to: "/spike", title: "异动筛查", icon: Zap, detail: "盘中瞬间冲高／摸到涨停的逐事件榜：几点冲的、多快、之后守不守得住" },
    { to: "/trend-stocks", title: "趋势股", icon: TrendingUp, detail: "近 5／10 个交易日每天收盘都严格站上当日 5 日线、且当天没封涨停的票，按主板／创业板／科创板分组" },
    { to: "/grouped", title: "分组自选", icon: FolderTree, detail: "组、焦点组、成本价、买入理由" },
    { to: "/qd-compare", title: "quantdash 口径对照", icon: Scale, detail: "两套算法并排，差异可见" },
    { to: "/allocator", title: "分配者视角", icon: Sigma, detail: "位置分、拥挤度温度、再分配阶段、位置×想象力四象限" },
  ] },
  { title: "工作台设置", features: [
    { to: "/settings", title: "AI 探索", icon: Cog, detail: "与 AI 对话、订阅与 API 接入状态" },
    { to: "/jobs", title: "任务调度", icon: ListChecks, detail: "作业状态、锁、落库日期与手动触发" },
  ] },
] as const;
export const FEATURES = FEATURE_GROUPS.flatMap(group => [...group.features]);
export const SUBSCRIPTIONS = [
  { id: 'codex-private', label: 'Codex订阅版', detail: '产品专用登录 · 已有复盘与追问接入' },
  { id: 'claude', label: 'Claude订阅', detail: '本机 Claude Code · 官方订阅登录' },
  { id: 'codebuddy', label: 'WorkBuddy CLI', detail: 'WorkBuddy 内置 CodeBuddy · macOS 已完成单日复盘流程实测' },
] as const;
