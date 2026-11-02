import { UnlockCalendarPanel } from "@/components/UnlockCalendarPanel";
import { SectorRotationBoard } from "@/components/home/SectorRotationBoard";
import { AbnormalMoveBoard } from "@/components/home/AbnormalMoveBoard";
import { RiskSignalPanel } from "@/components/home/RiskSignalPanel";
import type { RotationBasis } from "@/lib/rotation";
import { SortHeader } from "@/components/ui/SortHeader";
import { LazySection } from "@/components/ui/LazySection";
import { useTableSort } from "@/lib/useSort";
import { pageMaterials } from "@/lib/page-materials";
import { useState, useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { pctColor } from "@/lib/colors";
import { RefreshCw, Gauge, ArrowDownUp, TrendingUp, TrendingDown, BarChart3, Globe, ChevronDown, ChevronUp, Layers } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { SentimentTrend, type TrendPoint } from "@/components/SentimentTrend";
import { CALIBER_COLUMNS, buildCompareRows, cellText, columnStamps, diffText, type CaliberKey } from "@/lib/sentimentCompare";
import { Caliber } from "@/components/ui/Caliber";
import { usePageAskAi } from "@/lib/workspace/pageAskAi";
import { StockLink } from "@/components/ui/StockLink";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { plain } from "@/lib/agent";
import { api, type IndexQuote, type MarketOverview, type ShortTermEmotion, type TurnoverTop, type SectorRotation, type GlobalIndex, type MarketSession, type OverseasSnapshot, type OverseasRow, type LiveEmotion } from "@/lib/api";
import { cn } from "@/lib/utils";
import { NoticeBar } from "@/components/ui/NoticeBar";
import { DocDrawer, docLabel } from "@/components/ui/DocDrawer";

// A股红涨绿跌。全球市场（美股/港股指数）**也沿用红涨**——与整个看板及东财等中国平台一致，
// A 股惯例：红涨绿跌。与国际绿涨惯例相反，是有意选择，全站必须一致，勿改。

const AUTO_KEY = "yuanshan-auto-refresh";
const LIVE_MS = 5_000;      // 轻量行情：腾讯批量，5 秒
const HEAVY_MS = 60_000;    // 板块资金 / 成交额榜：东财+akshare，60 秒
const TREND_SESSIONS = 20;  // 情绪两档折线的窗口（用户 2026-10-06："20 日差不多吧"）
// 板块累计净流入那张表的挂牌名（首页这行提示要指得准，与「板块热度与点火」页的标题一致）。
// 10-08 改掉"2024 年以来"：这张表 10-06 才建、当天还没写通，库里只有 1 场时那个名字
// 就是在承诺一个不存在的窗口（用户当场判"累计数肯定是错的"）。名字只说读得到的一段。
const FLOW_TABLE_LABEL = "覆盖以来累计净流入";

const fmt = (v: number) => v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
const yi = (v: number | null) => (v == null ? "—" : `${fmt(v / 1e8)} 亿`); // 元 → 亿

/**
 * 这一块自己的「这块的说明」那颗按钮（用户 2026-10-31：它要摆在**标题这一行的最右边**、
 * 时间戳/as-of 之前）。原先每一块用的是 `<Caliber>` 那颗悬停芯片 —— 口径写得再全，
 * 鼠标不去扫就永远看不见，等于没有；抽屉是点开才占地方，默认收起。
 *
 * 三条边界：① 抽屉里的文字**只搬屏上原有的那些**，不在这里新写一套口径（两处各写一遍迟早分叉）；
 * ② 一模块一颗，同名按钮不许在同一张卡里长出第二颗；
 * ③ 缺数声明／截断上限／告警那三类**不走这里**，它们必须摊在屏上（见 `NoticeBar`）。
 */
function BlockDoc({ title, text, label, className = "ml-auto" }: {
  title: string; text: string; label?: string; className?: string;
}) {
  return (
    <span className={cn("flex shrink-0 items-center gap-1.5", className)}>
      <DocDrawer title={title} label={label}>
        <p className="whitespace-pre-line leading-relaxed">{plain(text)}</p>
      </DocDrawer>
    </span>
  );
}

/**
 * 首页里的「盘面数据」那一屏（2026-09-27 用户裁定：原 `/daily-review` 独立页并入首页，
 * 取代首页原来的「板级梯队」块；页面本体、路由与导航条目一并删除）。
 *
 * 它是一块**纯展示组件**：不发导航、不套 PageHeader 的一级标题（首页自己有），
 * 自动刷新是内容的一部分；「问 AI · 本页上下文」的入口在右侧「AI分析」栏里（用户 2026-10-07 裁定：
 * 头部那个「问 AI」按钮取消），这一屏只把当前读数**登记**过去，不再自带按钮。
 */
export function MarketPanorama() {
  const [indices, setIndices] = useState<IndexQuote[]>([]);
  const [idxErr, setIdxErr] = useState(false);
  const [overview, setOverview] = useState<MarketOverview | null>(null);
  const secSort = useTableSort();
  const [emotion, setEmotion] = useState<ShortTermEmotion | null>(null);
  const [turnover, setTurnover] = useState<TurnoverTop | null>(null);
  const tvSort = useTableSort();
  // 份额轮动榜（本地按场存的统计量）：口径与上面那张"盘中即时净流入"不是一回事，
  // 所以它自己一张卡、自己一套空态，不混进 sectors。
  const [rotation, setRotation] = useState<SectorRotation | null>(null);
  const [rotDone, setRotDone] = useState(false);
  const [rotLoading, setRotLoading] = useState(false);
  /** 轮动榜的尺子：share=份额（默认）、turnover=换手强度。两档各有判据版本，切换要重新取数 */
  const [rotationBasis, setRotationBasisState] = useState<RotationBasis>("share");
  const [globalIdx, setGlobalIdx] = useState<GlobalIndex[]>([]);
  // 实时行情属于哪一场：盘前接口返回的是上一场收盘，不标出来会被当成今天的
  const [session, setSession] = useState<MarketSession | null>(null);
  // 隔夜外围：指数 + 七姐妹。走自己的接口是为了拿到**行情所属交易日**
  // （vr 的 /api/global/indices 不回时间，界面就只能按本机今天贴日期）
  const [oversea, setOversea] = useState<OverseasSnapshot | null>(null);
  // 隔夜外围默认收起：占一整屏的指数卡不是每天要看的，用户要的是"要看再点"。
  // 收起态仍交代有几条、哪几场，不留一个看不出有没有数的空洞。
  const [overseaOpen, setOverseaOpen] = useState(false);
  // 东财实时池 · 盘中读数。与下面那块「昨日短线情绪」是两回事：
  // 这块随盘变，那块是已收盘那一场的东财池快照。
  // ⚠️ 它**不再是默认就取**的：2026-10-28 用户点名「就按照本地的显示，其他源不要显示了……
  // 切换后再加载，页面加载的时候不加载」⇒ 只有下拉切到它那一档才发请求（见 `cmpRef` 与 `pickCmp`）。
  const [liveEmo, setLiveEmo] = useState<LiveEmotion | null>(null);
  /** 三口径对照表显示哪几列：none=只摆本地（默认）、legu/live=本地 + 那一把尺 + 它的差值列。
   *  列名与"缺数怎么读"仍只住 `lib/sentimentCompare.ts` 那份，这里只决定显示不显示。 */
  const [cmp, setCmp] = useState<"none" | "legu" | "live">("none");
  // 自动刷新开关：**默认关**（别替用户决定要不要一直打请求），选择记在本地
  const [autoRefresh, setAutoRefresh] = useState<boolean>(
    () => { try { return localStorage.getItem(AUTO_KEY) === "1"; } catch { return false; } });
  // 写 localStorage 失败（无痕模式 / 浏览器存储被拒）要落在页面上，不能静默吞掉：
  // 用户以为开关记住了，实际下次打开还是默认关。
  const [autoErr, setAutoErr] = useState<string | null>(null);

  // 各数据块请求是否已结束：区分「加载中」与「数据源暂不可用」（非交易时段/被限流时后端返回空）
  const [ovDone, setOvDone] = useState(false);
  const [toDone, setToDone] = useState(false);

  // 轻量实时组：全是腾讯批量行情，一次一个请求，5 秒一刷不吃力
  /** 5 秒定时器复用的 `loadLive` 会带着旧闭包 ⇒ 开关放 ref 上读，别读 state。 */
  const cmpRef = useRef(cmp);
  useEffect(() => { cmpRef.current = cmp; }, [cmp]);
  const loadLive = () => {
    api.indices().then(setIndices).catch(() => setIdxErr(true));
    api.overseas().then(setOversea).catch(() => {});
    api.marketSession().then(setSession).catch(() => {});
    // 盘中那一把尺默认不取：不切过去就不进这发请求（它本来是 5 秒组里的成员，
    // 一天开着页面就是上万发），切过去才取、并由定时器刷新。
    if (cmpRef.current === "live") api.liveEmotion().then(setLiveEmo).catch(() => {});
    // 连板股那张表 10-29 搬到「短线复盘」页（涨停分析）⇒ 这里不再为它刷实时价。
  };

  /** 切对照口径：切到「东财实时池」且手上还没有那份数才发第一发；已经取到过就不重打
   *  （要新数用页头「重新取数」—— 定时器在切过去之后每 5 秒也会自己刷）。 */
  const pickCmp = (k: "none" | "legu" | "live") => {
    setCmp(k);
    if (k === "live" && !liveEmo) api.liveEmotion().then(setLiveEmo).catch(() => {});
  };

  // 重量组：板块资金走 akshare + JS 引擎（90 个行业）、成交额榜走东财 clist。
  // **不能跟着 5 秒刷** —— 会撞限流，而且这两样本身变化慢。
  const loadHeavy = () => {
    api.globalIndices().then(setGlobalIdx).catch(() => {});
    api.marketOverview().then(setOverview).catch(() => {}).finally(() => setOvDone(true));
    api.turnoverTop().then(setTurnover).catch(() => {}).finally(() => setToDone(true));
  };

  /** 轮动榜：本地库按场存的统计量，一天只变一次，跟着轮询打就是白打一次全库排名。
   *  ⚠️ 定义在 `loadSettled` **之前**：const 箭头函数没有提升，写在后面首调会踩 TDZ。
   *  `b` 必须能显式传进来：换档那一刻 `rotationBasis` 这个 state 还没提交，
   *  只读 state 的话点了「换手强度」会把上一档的份额榜再刷一遍（屏幕上看着像没反应）。 */
  const loadRotation = (b?: RotationBasis) => {
    const use = b ?? rotationBasis;
    setRotLoading(true);
    api.sectorRotation(undefined, use).then(setRotation).catch(() => {}).finally(() => { setRotLoading(false); setRotDone(true); });
  };
  // 换口径 = 换一次取数（后端按 basis + 各自的判据版本查库），不是前端换个列名
  const setRotationBasis = (b: RotationBasis) => { setRotationBasisState(b); loadRotation(b); };

  // 短线情绪锚在已收盘那一场，只在进页面时取一次，不参与任何轮询
  const loadSettled = () => {
    api.emotion().then(setEmotion).catch(() => {});
    loadRotation();
  };

  const loadIndices = () => { loadLive(); loadHeavy(); loadSettled(); };

  // 数据块占位：请求没回来 = 加载中；回来了但为空 = 数据源暂不可用（别让用户干等）
  const pending = (done: boolean) => (
    <p className="py-4 text-center text-sm text-muted-foreground/60">
      {done ? "暂无数据：可能是非交易时段或数据源暂时不可用，可点「大盘指数」旁的刷新重试" : "加载中…"}
    </p>
  );

  useEffect(() => {
    loadIndices();
  }, []);

  // 时段探测独立于行情轮询，盘前/午休打开后才能自动发现开盘。
  useEffect(() => {
    if (!autoRefresh) return;
    const timer = setInterval(() => api.marketSession().then(setSession).catch(() => {}), 15000);
    return () => clearInterval(timer);
  }, [autoRefresh]);

  // 收盘和15:05定稿标签变化时补拉数据，避免停轮询后仍展示尾盘旧值。
  const observedSessionLabel = useRef<string | null>(null);
  useEffect(() => {
    if (!session?.label) return;
    const previous = observedSessionLabel.current;
    observedSessionLabel.current = session.label;
    if (previous === null || previous === session.label || !autoRefresh || session.phase_key !== 'closed') return;
    loadLive();
    loadHeavy();
  }, [autoRefresh, session?.label, session?.phase_key]);

  // ⭐ 自动刷新。三条不能写错的地方：
  //  ① **交易时段用后端给的 `session.phase` 判断**，不要用 `new Date().getHours()`
  //     —— 那是本机时区，人在海外会盘中不刷、半夜狂刷。后端算的是北京时间。
  //  ② 收盘 / 非交易日**不刷** —— 数字不会动，白打请求还可能撞限流。
  //  ③ cleanup 必须把两个句柄都清掉，且**句柄只属于这一次 effect**。
  //     共享句柄或忘了清，旧的定时器会活下来跟新的并行 → 实际频率翻倍。
  useEffect(() => {
    const live = Boolean(session?.poll);
    if (!autoRefresh || !live) return;

    const liveTimer = setInterval(() => {
      loadLive();
    }, LIVE_MS);
    const heavyTimer = setInterval(loadHeavy, HEAVY_MS);
    return () => { clearInterval(liveTimer); clearInterval(heavyTimer); };
  }, [autoRefresh, session?.phase]);

  const toggleAuto = () => {
    const next = !autoRefresh;
    try { localStorage.setItem(AUTO_KEY, next ? "1" : "0"); setAutoRefresh(next); }
    catch { setAutoErr("自动刷新设置未保存：请检查浏览器存储空间与权限"); }
  };

  // 是否处在会动的时段（后端判定，避免本机时区坑）
  const liveNow = Boolean(session?.poll);

  const dataSummary = pageMaterials({
    "当前场次": session, "指数": indices, "市场概览": overview,
    "盘中情绪": liveEmo, "已收盘情绪及梯队": emotion,
    "换手率榜": turnover, "隔夜外围": oversea, "份额轮动榜（本地逐日）": rotation,
  });
  // 登记给右侧「AI分析」栏：这一屏每次都交**当下这份**材料，不缓存旧盘面
  usePageAskAi({
    label: "首页 · 盘面数据",
    context: dataSummary,
    suggestions: ["根据已有材料解释这场盘面", "哪些指数领涨领跌", "哪些判断还缺少证据"],
  });


  const sentiment = overview?.sentiment;
  const sectors = overview?.sectors || [];
  // 情绪两档的逐场折线：读库的那份（`analytics.market_sentiment_daily`），不是现抓接口。
  // 后端一次给 30 场（多取的余量给"锐化"用的首尾平均），卡片只画最近 20 场。
  const series = overview?.sentiment_series;
  const seriesPoints: TrendPoint[] = series?.available ? series.points ?? [] : [];
  // 卡片那格是**此刻现抓**的乐咕读数，折线末点是**库里那一场**的存档读数。
  // 两个来源本来就会差半档（回算那批的涨停数是单边判据），差出来时必须自己说，
  // 否则用户看到"卡片：普通 / 线尾：活跃"会以为其中一个算错了。
  const lastTrend = seriesPoints.length ? seriesPoints[seriesPoints.length - 1] : null;
  const levelDrift = (which: "breadth" | "theme") => {
    if (!lastTrend || !sentiment) return "";
    const card = which === "breadth" ? sentiment.breadth : sentiment.speculation;
    const stored = which === "breadth" ? lastTrend.breadth_level : lastTrend.theme_level;
    if (!card || !stored || card === stored) return "";
    return `卡片是 ${card}、折线末点（${lastTrend.date}）是 ${stored} —— 两个来源本就差半档：`
      + "卡片现抓乐咕，末点是 market_breadth 回算（涨停数为单边判据），不是其中一个算错。";
  };
  // 板块资金趋势榜：累计列读 `market_data.sector_fund_flow_daily`（2026-10-06 起逐日落库），
  // 今日即时那一列仍是现抓截面，两边按**行业名**对上（同花顺同一套名字，不是本地 TDX 板块码）。
  const flow = overview?.sector_flow;
  const liveByName = new Map(sectors.map((s) => [s.name, s.net] as const));
  const flowMerged = (flow?.available ? flow.rows : []).map((r) => ({
    ...r, live_net: liveByName.get(r.sector_name) ?? null,
  }));

  return (
    <div id="market-panorama">
      {autoErr && <p role="alert" className="text-danger">{autoErr}</p>}
      <PageHeader
        level={2}
        title="盘面数据"
        subtitle={`${session?.label ?? `场次未探测到（这里不拿本机时钟冒充行情场次）`} · 指数 / 外围 / 板块资金一屏看全（复盘统计在「复盘报告」，涨停分析与跌停分析、首板分析在「短线复盘」，自选行情在「分组自选」）`}
        actions={
          <div className="flex items-center gap-2">
            {/* 开着但不在交易时段时要说清「为什么不动」——否则会被当成坏了 */}
            <button
              onClick={toggleAuto}
              title={autoRefresh
                ? `已开：指数/外围每 ${LIVE_MS / 1000} 秒、板块资金与成交额榜每 ${HEAVY_MS / 1000} 秒。只在盘中生效`
                : "开启后在交易时段自动刷新行情"}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm transition-colors",
                autoRefresh
                  ? "bg-primary/15 text-primary hover:bg-primary/25"
                  : "text-muted-foreground hover:text-primary",
              )}
            >
              <RefreshCw className={cn("h-3.5 w-3.5", autoRefresh && liveNow && "animate-spin")} />
              {autoRefresh ? (liveNow ? `每 ${LIVE_MS / 1000} 秒自动刷新` : "自动刷新（非交易时段暂停）") : "自动刷新"}
            </button>
          </div>
        }
      />

      {session && <p role="status" className="mb-4 text-sm text-warning">{session.label}。{["auction", "closing"].includes(session.phase_key || "") ? "以下个股为试撮合报价，不代表已成交或封板；成交榜与异动在连续交易时段确认。" : "各表按来源时点更新，未覆盖不等于零。"}</p>}
      {/* 1. 大盘指数（实时） */}
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-baseline gap-2">
          <h3 className="text-sm font-semibold text-muted-foreground">大盘指数</h3>
          {session && (
            <span className={cn("text-[11px]", session.is_today ? "text-muted-foreground/50" : "text-warning")}>
              {session.label}
            </span>
          )}
          {/* 集合竞价（09:15-09:25）还没成交，指数等于昨收、涨跌幅是 0 */}
          {session?.phase === "集合竞价" && (
            <span className="text-[11px] text-muted-foreground/50">还没成交，涨跌幅为 0 是正常的</span>
          )}
        </div>
        {/* 说明在刷新按钮左边、这一行最右侧（10-31 全站同一条位置规矩） */}
        <span className="flex shrink-0 items-center gap-2">
          <BlockDoc className="" title="大盘指数" text={
            "涨跌幅对比前一交易日收盘。\n" +
            "「实时」是延时行情，页面上没标截至几点。"
          } />
          <button onClick={loadIndices} className="text-muted-foreground hover:text-primary" title="刷新"><RefreshCw className="h-3.5 w-3.5" /></button>
        </span>
      </div>
      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {indices.length === 0
          ? [1, 2, 3, 4].map((i) => (
              <GlassCard key={i} className="p-3">
                <p className="text-xs text-muted-foreground">{idxErr ? "行情未接通" : "加载中…"}</p>
                <p className="mt-1 font-mono text-lg font-bold text-muted-foreground/40">—</p>
              </GlassCard>
            ))
          : indices.map((i) => (
              <GlassCard key={i.name} className="p-3">
                <p className="truncate text-xs text-muted-foreground">{i.name}</p>
                <p className={cn("mt-1 font-mono text-lg font-bold", pctColor(i.change_pct))}>{i.price}</p>
                <p className={cn("text-xs", pctColor(i.change_pct))}>{i.change_pct > 0 ? "+" : ""}{i.change_pct}%</p>
              </GlassCard>
            ))}
      </div>

      {/* 1b. 隔夜外围：美股 / 港股指数 + 美股七姐妹 */}
      {(oversea?.available || globalIdx.length > 0) && (() => {
        // 优先用带交易日的新接口；它挂了才退回 vr 那份（没有日期，只能不标）
        const rows: OverseasRow[] = oversea?.available && oversea.indices?.length
          ? oversea.indices
          : globalIdx.map((g) => ({ name: g.name, price: g.price ?? null, change_pct: g.change_pct ?? null,
                                    session: null, region: g.region }));
        const us = rows.filter((r) => r.region === "美股");
        const hk = rows.filter((r) => r.region !== "美股");
        const mag7 = oversea?.available ? (oversea.mag7 ?? []) : [];
        const cell = (r: OverseasRow, sub?: string) => (
          <GlassCard key={r.name} className="p-3">
            <p className="truncate text-xs text-muted-foreground">
              {r.name}{sub && <span className="ml-1 font-mono text-[10px] text-muted-foreground/40">{sub}</span>}
            </p>
            <p className={cn("mt-1 font-mono text-lg font-bold", r.price == null ? "text-muted-foreground/50" : pctColor(r.change_pct))}>
              {r.price ?? "—"}
            </p>
            <p className={cn("text-xs", pctColor(r.change_pct))}>
              {r.change_pct == null ? "涨跌未取到" : `${r.change_pct > 0 ? "+" : ""}${r.change_pct}%`}
            </p>
          </GlassCard>
        );
        return (
          <>
            <div className="mb-3 flex flex-wrap items-baseline gap-2">
              <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground"><Globe className="h-4 w-4" /> 隔夜外围</h3>
              <span className="text-[11px] text-muted-foreground/50">A 股常看美股 / 港股脸色</span>
              <BlockDoc title="隔夜外围" text={
                "美股港股的涨跌幅都是对比它们各自的前一交易日收盘。\n" +
                "港股在北京时间白天可能正在交易，所以会标「盘中」——那是抓取那一刻的延时行情，没标具体几点。"
              } />
              <button type="button" onClick={() => setOverseaOpen(v => !v)} aria-expanded={overseaOpen}
                className="flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11px] text-primary transition-colors hover:bg-muted/50">
                {overseaOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                {overseaOpen ? "收起" : "展开"}
              </button>
              {/* 标签由后端给（含盘前/盘中/收盘）—— 前端别自己拼"收盘"，
                  港股在北京白天可能正在交易，那时候标"收盘"就是错的 */}
              {oversea?.us_label && <span className="text-[11px] text-warning">{oversea.us_label}</span>}
              {oversea?.hk_label && <span className="text-[11px] text-warning">{oversea.hk_label}</span>}
            </div>

            {!overseaOpen && (
              <p className="mb-3 text-[11px] leading-relaxed text-muted-foreground/70">
                已收起：{us.length + hk.length} 个指数（美股 {us.length} / 亚太 {hk.length}）
                {mag7.length > 0 ? ` + ${mag7.length} 只七姐妹` : ""}，点右上角「展开」看点位与涨跌。
                收起只是因为这块天天看的人少，不是没取到数。
              </p>
            )}

            {overseaOpen && us.length > 0 && (
              <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-3">{us.map((r) => cell(r))}</div>
            )}

            {overseaOpen && mag7.length > 0 && (
              <>
                <p className="mb-2 text-[11px] text-muted-foreground/60">
                  美股七姐妹 · 权重股带指数走，看它们比看指数细
                </p>
                <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
                  {mag7.map((r) => cell(r, r.ticker))}
                </div>
              </>
            )}

            {overseaOpen && hk.length > 0 && (
              <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3">{hk.map((r) => cell(r))}</div>
            )}
          </>
        );
      })()}

      {/* 4. 市场情绪 · 三口径对照（10-27 用户点名：东财实时池那一块与短线情绪头部那两组格子并到这里，
          省空间的前提是三把尺各留各的时间戳，数字不等照摆） */}
      <div className="mb-3 flex items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground"><Gauge className="h-4 w-4" /> 市场情绪 · 口径对照（默认只摆本地库）</h3>
        <BlockDoc title="市场情绪 · 口径对照（默认只摆本地库）" text={
          "同一件事这里有三把尺：乐咕乐股（源站口径）、东财涨停池（盘中即时截面）、本地库（已收盘按本地日线判据自算）。\n" +
          "三列各数各的，谁也不顶替谁：数字不等是口径不同，不是谁算错（同一场 10-23 实测：封住 84 对 82、炸板 19 对 12、跌停 9 对 6）。\n" +
          "每一列的表头带着它自己的时间与分母；哪一列没读到就写 —，不拿别列的数顶上，也不填 0。\n" +
          "大盘宽度按涨跌家数机械分档：上涨不足 600 家为冰点，其余看 上涨÷下跌 的比值\n" +
          "（<0.7 偏弱 / 0.7-1.2 中性 / 1.2-2.5 偏强 / ≥2.5 普涨）。\n" +
          "题材投机只按真实涨停家数分档：<30 冰点 / 30-59 普通 / 60-99 活跃 / ≥100 亢奋。\n" +
          "下面那两条折线画的是近 20 场的同一档读数，0 轴分别是「中性」与「普通/活跃的中间」；" +
          "每日读数已入库（analytics.market_sentiment_daily），图读库、不是拿此刻现算。\n" +
          "每天收盘后由加工链的 `sentiment` 步续一格；库里那几格是 market_breadth 回算的，" +
          "涨停用的是单边判据（这一条会写得屏上，因为它改的是读数怎么信，不是口径注释）。"
        } />
        {sentiment?.date && <span className="text-[11px] text-muted-foreground/50">{sentiment.date}</span>}
      </div>
      <GlassCard className="mb-6">
        {/* 用户 2026-10-28：「就按照本地的显示，其他源不要显示了，做一个下拉菜单，有需要的时候自行切换，
            切换后再加载，页面加载的时候不加载」⇒ 默认只摆本地那一列，另一把尺切出来才显示、才去取。 */}
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            对照口径
            <select value={cmp} onChange={e => pickCmp(e.target.value as typeof cmp)}
              title="默认只摆「本地库 · 已收盘自算」这一列。切到另一把尺才去取那份数：东财实时池是盘中截面（切过去后每 5 秒随盘刷，且它才会进右侧 AI 分析的材料）；乐咕那一列不额外花请求——同一份 marketOverview 也喂上面那两格卡片与下面「资金轮动·今日即时」，所以它随页面本来就在取，下拉只决定显示不显示。"
              className="rounded border border-border bg-background px-1.5 py-1 text-[11px] text-foreground">
              <option value="none">不对照（只看本地库）</option>
              {/* 选项文字读 sentimentCompare 那一份列名，页面不另抄一套中文（两处各写一遍迟早走偏） */}
              {CALIBER_COLUMNS.filter((c) => c.key !== "local").map((c) => (
                <option key={c.key} value={c.key}>加一列：{c.label}</option>
              ))}
            </select>
          </label>
          <span className="text-xs text-muted-foreground">
            两把尺分别统计，计数不一致时保留来源差异并排摆着；「本地 −」那一列是展示层算术，不是判据。
            {cmp === "none" ? " 现在只摆本地一列，所以没有差值可算。" : ""}
          </span>
        </div>
        {!sentiment?.breadth ? (
          pending(ovDone)
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              {[
                {
                  k: "大盘宽度", v: sentiment.breadth, hint: "冰点 / 偏弱 / 中性 / 偏强 / 普涨",
                  field: "breadth_score" as const,
                  cal: "折线的 0 轴就是「中性」这一档。\n" +
                    "编码：冰点 −2 / 偏弱 −1 / 中性 0 / 偏强 +1 / 普涨 +2。\n" +
                    "分档判据（上涨不足 600 家为冰点，其余看 上涨÷下跌 的比值）只有一份实现，" +
                    "卡片上的字与图上取的是同一次判定，前端不重算。",
                },
                {
                  k: "题材投机", v: sentiment.speculation, hint: "冰点 / 普通 / 活跃 / 亢奋",
                  field: "theme_score" as const,
                  cal: "折线的 0 轴是用户指定的「普通 与 活跃 的中间值」。\n" +
                    "所以编码是半格起步：冰点 −1.5 / 普通 −0.5 / 活跃 +0.5 / 亢奋 +1.5，" +
                    "0 轴上下各半格正好把「够不够活跃」切开。\n" +
                    "分档只看真实涨停家数：<30 / 30-59 / 60-99 / ≥100。",
                },
              ].map((m) => (
                <div key={m.k} className="rounded-lg bg-muted/25 p-4">
                  <p className="flex items-center gap-1 text-xs text-muted-foreground">
                    {m.k}<Caliber text={m.cal} />
                  </p>
                  <p className="mt-1 text-2xl font-bold text-primary">{m.v}</p>
                  <p className="mt-1 text-[11px] text-muted-foreground/60">{m.hint}</p>
                  {series?.available === false && series.reason
                    ? <p className="mt-2 text-[11px] leading-relaxed text-warning/80">{plain(series.reason)}</p>
                    : <SentimentTrend points={seriesPoints} field={m.field} sessions={TREND_SESSIONS} />}
                  {levelDrift(m.field === "breadth_score" ? "breadth" : "theme") && (
                    <p className="mt-1 text-[10px] leading-relaxed text-warning/90">
                      {levelDrift(m.field === "breadth_score" ? "breadth" : "theme")}
                    </p>
                  )}
                </div>
              ))}
            </div>
            {/* 折线读库、不读现抓接口 —— 屏上只留这一行读数（几个字），
                148 字那段"取自哪张表／谁续的一格／判据差在哪"里：出处与算理进左边那颗抽屉，
                只把**会影响读数怎么信**那半句留在屏上（11-01「屏上只留读数」裁定 ＋ 三型声明不许进抽屉）。 */}
            {series?.available && (
              <p className="mt-2 text-[10px] text-muted-foreground/60">
                两条线读库：近 {series.points?.length ?? 0} 场（乐咕现抓 {series.sources?.legu ?? 0}／market_breadth 回算 {series.sources?.local ?? 0}）
              </p>
            )}
            {series?.available && (
              <NoticeBar kind="warning">回算那几格的涨停数是单边判据，题材那一档可能比乐咕口径乐观半档。</NoticeBar>
            )}
          </>
        )}

        {/* 三口径对照表：不跟着乐咕那一档的门走 —— 源站没读到数时，
            实时与本地两列照旧摆出来，别让整张表陪绑（陪绑＝屏上读成"今天什么都没发生"）。 */}
        {(() => {
          const rows = buildCompareRows(sentiment, liveEmo, emotion);
          const stamps = columnStamps(sentiment, liveEmo, emotion);
          // 显示哪几列由上面那个下拉决定；**列名与 sub 仍只住 sentimentCompare 那一份**。
          // 本地永远在第一列（它就是主数），另一把尺切出来才跟在后面。
          const wanted: CaliberKey[] = cmp === "none" ? ["local"] : ["local", cmp];
          const cols = wanted.map(k => CALIBER_COLUMNS.find(c => c.key === k)).filter(Boolean) as
            typeof CALIBER_COLUMNS;
          const diffKey = cmp === "legu" ? "vsLegu" as const : cmp === "live" ? "vsLive" as const : null;
          const colCaliber: Record<string, string> = {
            legu: "涨停 / 真实涨停 / 跌停 / 真实跌停 / 活跃度取自乐咕乐股，不是我们算的。\n" +
                  "「真实」与普通涨跌停的差额由它自己的口径决定，具体算法未公开。\n" +
                  "活跃度同样是源站自有算法，不能按上涨家数占比来理解。",
            live: "东财涨停池的盘中即时截面：轻量组每 5 秒随盘刷一次，表头那个时刻就是它的快照时刻。\n" +
                  "炸板按「开板事件」数；池只含沪深主板/创业板/科创板，基本不含 ST 与北交所。\n" +
                  "摸板家数 = 涨停 + 炸板，封板率与炸板率都按家数算、不按开板次数算。",
            local: (emotion?.caliber || "") +
                   "\n母体在本地库里（analytics.limit_up_pool_daily，加工段每日落库），这一块不再每次开页现抓东财池。\n" +
                   (emotion?.seal_rule || ""),
          };
          // 只摆一列数时**不摊成两列的表**（10-31 用户点名：「内容只有一点点，没点占一行，太占用空间了」）：
          // 14 行 × 实测 37px ≈ 518px 高，纵向空间全花在表格骨架上，而每行其实只有一个数字。
          // 对照开启（两列以上或要算差值）时才回到那张表 —— 那时并排比的是列与列的关系。
          const compact = cols.length === 1 && !diffKey;
          const compactView = (() => {
            const c = cols[0];
            const stamp = stamps[c.key];
            return (
              <>
                <p className="mb-1 text-[11px] text-muted-foreground">
                  {c.label} · {stamp.stamp || "日期未确认"}
                  <span className="text-muted-foreground/60"> · {c.sub}</span>
                </p>
                {/* 列级的那句"为什么没读到"属缺数声明：留在屏上，不进抽屉 */}
                {stamp.warn && <p className="mb-1 text-[11px] leading-relaxed text-warning">{plain(stamp.warn!)}</p>}
                <div className="grid gap-x-6 sm:grid-cols-2">
                  {rows.map((r) => (
                    <div key={r.key} className="border-b border-border/20 py-[3px]">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="flex min-w-0 items-baseline gap-1 text-[11px] text-muted-foreground">
                          <span className="truncate">{r.label}</span>
                          {r.hint && <Caliber text={r.hint} />}
                        </span>
                        <span className="shrink-0 font-mono text-[13px]">{cellText(r, c.key)}</span>
                      </div>
                      {/* 分子分母是哪一批（例：晋级率的分母）不许因为改紧凑就丢 */}
                      {r.values[c.key].note && (
                        <p className="text-[10px] leading-tight text-muted-foreground/45">{r.values[c.key].note}</p>
                      )}
                    </div>
                  ))}
                </div>
              </>
            );
          })();
          return (
            <div className="mt-3">
              {compact ? compactView : (
              <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border/50 text-xs text-muted-foreground">
                    <th className="px-2 py-2 text-left font-medium">指标</th>
                    {cols.map((c) => (
                      <th key={c.key} className="px-2 py-2 text-right font-medium whitespace-nowrap align-bottom">
                        <span className="flex items-center justify-end gap-1">
                          {c.label}<Caliber text={colCaliber[c.key] || c.sub} />
                        </span>
                        {stamps[c.key].stamp && <span className="mt-0.5 block text-[10px] font-normal text-muted-foreground/50">{stamps[c.key].stamp}</span>}
                        {/* 刚切过去、第一发还没回来：说清楚是"正在取"，不是"没有" */}
                        {c.key === "live" && !liveEmo && !stamps[c.key].stamp && (
                          <span className="mt-0.5 block text-[10px] font-normal text-muted-foreground/60">盘中截面取数中…</span>
                        )}
                        {stamps[c.key].warn && <span className="mt-0.5 block text-[10px] font-normal leading-tight text-warning/80">{plain(stamps[c.key].warn!)}</span>}
                      </th>
                    ))}
                    {diffKey && (
                      <th className="px-2 py-2 text-right font-medium whitespace-nowrap"
                        title={cmp === "legu"
                          ? "本地库那一列减乐咕那一列：算不出来的格子写着为什么算不出来"
                          : "本地库那一列减东财实时池那一列（同一场时才是同时刻之比）"}>
                        {cmp === "legu" ? "本地 − 乐咕" : "本地 − 实时"}
                      </th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.key} className="border-b border-border/30">
                      <td className="px-2 py-1.5 text-left text-[11px] whitespace-nowrap">
                        <span className="flex items-center gap-1">
                          <span className="text-muted-foreground">{r.label}</span>
                          {r.hint && <Caliber text={r.hint} />}
                        </span>
                        {cols.map((c) => r.values[c.key].note && (
                          <span key={c.key} className="ml-1 block text-[10px] text-muted-foreground/45">
                            {c.label.split(" ")[0]}：{r.values[c.key].note}
                          </span>
                        ))}
                      </td>
                      {cols.map((c) => (
                        <td key={c.key} className={cn("px-2 py-1.5 text-right font-mono",
                              r.values[c.key].v == null ? "text-muted-foreground/40"
                                                       : r.kind === "count" && c.key !== "legu" ? "text-foreground"
                                                                                                 : "text-foreground")}>
                          {cellText(r, c.key)}
                        </td>
                      ))}
                      {diffKey && (
                        <td className="px-2 py-1.5 text-right font-mono text-[11px] text-muted-foreground">
                          {diffText(r, diffKey)}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              )}
              {/* 缺数读法声明：属「有才显示、永不进抽屉」那一型，走共用 NoticeBar（kind=missing）。
                  两种形状都要有，所以它留在外层、不进那张表也不进紧凑那一栏。 */}
              <NoticeBar kind="missing">
                行上的 — 有两种：那一把尺本来没有这个指标（乐咕不收炸板、也不数连板），或这一列这一刻没读到数 ——
                后者一定在列名那一行写了为什么（对照开着时写在表头）。晋级率那一行不算差：两列的「上一场」不是同一天，分母不同一批票。
              </NoticeBar>
            </div>
          );
        })()}
      </GlassCard>

      {/* 10-29 用户裁定 ③：「首板分析」那一整块搬进「短线复盘」页（挂在短线情绪两张表之后）。
          这里不是一块空卡，也不是一句"已删除"——它是一个**能点到目的地的入口**（用户交付口径：
          功能搬走之后，最终用户不问人也要能找到它）。跳的是带锚点的那个地址，落地页会自己滚过去。 */}
      <GlassCard className="mb-6 flex flex-wrap items-center gap-x-3 gap-y-1.5 py-3">
        <Layers className="h-4 w-4 shrink-0 text-muted-foreground" />
        <p className="min-w-0 flex-1 text-sm text-muted-foreground">
          首板分析<b className="text-foreground">已并入「短线复盘」页</b>，在短线情绪那两张表之后。
        </p>
        <Link to="/first-board?to=first-board-daily"
          className="shrink-0 rounded-lg border border-primary/50 bg-primary/10 px-3 py-1.5 text-sm font-medium text-primary transition-colors hover:bg-primary/20">
          去短线复盘 · 首板分析
        </Link>
      </GlassCard>

      <div className="mb-3 flex items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground"><BarChart3 className="h-4 w-4" /> 全市场换手率 TOP20</h3>
        <BlockDoc title="全市场换手率 TOP20" text={
          "沪深京 A 股按当日累计换手率从大到小排（换手率 = 成交量 ÷ 流通股本）。\n" +
          "排序在取数那一侧就定了（东财 clist 按 f8 取前 20 名）。先取成交额榜、再在前端按换手率重排，\n" +
          "得到的是「成交额前 20 里的换手率榜」，那不是我给你看的这张榜。\n" +
          "盘中看到的是「到刷新那一刻为止」的累计值，不是收盘值；成交额与总市值同样按当前价、累计额算。\n" +
          "名字前面带 C 的是上市不满一年的次新股标记，不是名字的一部分。\n" +
          "⚠️ 这张榜只排「钱换手得多快」，不判断好坏：换手率高既可能是主升也可能是出货，\n" +
          "产品侧不做推荐、不评分、不给买卖点。"
        } />
        <span className="text-[11px] text-muted-foreground/50">客观公开榜单，非推荐 / 非预测 / 不构成投资建议</span>
        {/* 没数的时候整块默认收起（用户 2026-10-27 点名）：空表会被读成「今天没人换手」，
            而真实原因（限流／榜单口径变了／还在取数）收在标题右边那个折叠里，点开才占地方。 */}
        {!turnover || turnover.stocks.length === 0 ? (
          <details className="text-[11px] text-muted-foreground/60">
            <summary className="cursor-pointer">这一场没拿到榜单（点开看为什么）</summary>
            <p className="mt-1 max-w-[46rem] leading-relaxed">
              {plain(turnover?.reason
                || (toDone ? "东财 clist 这一轮没返数据（限流、口径变了或网络不通）—— 没返数不等于今天没人换手。"
                           : "还在取数中：这张榜要现抓东财，回来之前这里不摆空表。"))}
            </p>
          </details>
        ) : (
          turnover?.updated && <span className="text-[11px] text-muted-foreground/50">参考行情日 {turnover.quote_date || "未确认"} · 抓取于 {turnover.updated}</span>
        )}
      </div>
      <GlassCard className="mb-6">
        {!turnover || turnover.stocks.length === 0 ? (
          /* 空态只留一行指路的话，正文在标题右边那个折叠里（默认收起） */
          <p className="py-3 text-sm text-muted-foreground/70">
            {plain("这一场没有榜单可读，原因已收在标题右边「这一场没拿到榜单（点开看为什么）」里；"
                  + "这里不摆空表，也不摆一个大块占着屏幕。")}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
                  <th className="whitespace-nowrap px-2 py-2 font-medium" title="后端榜序的序号；排序后序号会跟着行走，不代表名次">#</th>
                  <SortHeader label="名称" sortKey="name" dir={tvSort.dirOf("name")} onSort={tvSort.toggle} />
                  <SortHeader label="现价" sortKey="price" dir={tvSort.dirOf("price")} onSort={tvSort.toggle} />
                  <SortHeader label="涨跌%" sortKey="pct" dir={tvSort.dirOf("pct")} onSort={tvSort.toggle} />
                  <SortHeader label="换手率" sortKey="turnover" dir={tvSort.dirOf("turnover")} onSort={tvSort.toggle}
                        title="本榜的排序键：当日累计换手率（%）。盘中是到刷新那一刻的累计值" />
                  <SortHeader label="成交额" sortKey="amount" dir={tvSort.dirOf("amount")} onSort={tvSort.toggle}
                        title="同一列内单位一致（后端给的元），不做亿/万换算" />
                  <SortHeader label="总市值" sortKey="mcap" dir={tvSort.dirOf("mcap")} onSort={tvSort.toggle} />
                  <SortHeader label="行业" sortKey="industry" dir={tvSort.dirOf("industry")} onSort={tvSort.toggle} />
                  <SortHeader label="概念板块" sortKey="concepts" dir={tvSort.dirOf("concepts")} onSort={tvSort.toggle}
                        title="按名字去重后取前 3 个概念（成员数少的在前＝窄题材优先）；括号外是前 3 个，+N 是还剩几个" />
                </tr>
              </thead>
              <tbody>
                {tvSort.apply(turnover.stocks).map((s, i) => (
                  <tr key={s.code} className="border-b border-border/30">
                    <td className="px-2 py-2 font-mono text-xs text-muted-foreground/50">{i + 1}</td>
                    <td className="px-2 py-2"><StockLink code={s.code} name={s.name} className="font-medium hover:text-primary" /> <StockLink code={s.code} className="text-xs text-muted-foreground/50 underline decoration-dotted underline-offset-2 hover:text-primary">{s.code}</StockLink></td>
                    <td className="px-2 py-2 font-mono">{s.price ?? "—"}</td>
                    <td className={cn("px-2 py-2 font-mono", s.pct == null ? "text-muted-foreground" : pctColor(s.pct))}>
                      {s.pct == null ? "—" : `${s.pct > 0 ? "+" : ""}${s.pct}%`}
                    </td>
                    <td className="whitespace-nowrap px-2 py-2 font-mono font-semibold tabular-nums">
                      {s.turnover == null ? "—" : `${s.turnover}%`}
                    </td>
                    <td className="whitespace-nowrap px-2 py-2 font-mono">{yi(s.amount)}</td>
                    <td className="whitespace-nowrap px-2 py-2 font-mono text-muted-foreground">{yi(s.mcap)}</td>
                    <td className="whitespace-nowrap px-2 py-2 text-xs text-muted-foreground">{s.industry}</td>
                    <td className="px-2 py-2 text-xs text-muted-foreground"
                      title={s.concepts ? `共属于 ${s.concept_count ?? 3} 个概念，这里列前 3 个（窄题材在前）` : (turnover.concept_note || "本地库里没有这只票的概念行")}>
                      {s.concepts ?? <span className="opacity-50">—</span>}
                      {s.concepts && (s.concept_count ?? 0) > 3 ? <span className="ml-1 opacity-60">+{(s.concept_count ?? 0) - 3}</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {turnover?.concept_missing ? (
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">{turnover.concept_note}</p>
        ) : null}
      </GlassCard>

      {/* 4c-0. 风险提示 · 宽度 × 阿姆斯（用户 2026-10-27 点名：从「盘面数据」那屏外面搬进来，
          摆在异动提醒**上面**）。判据、20 期分位、档位阈值只住 `duanxian/risk_signal.py` 一处
          （界面读 thresholds/levels/rule，不写死）；不进 `Block` 也不占 as-of ——
          它没有"今天最新一场"的挂靠点（窗口本来就是几个月），拿盘面的 as-of 去收起它
          反而会把"背离判不了"的原因藏掉。 */}
      <RiskSignalPanel />

      {/* 4c. 异动提醒（用户 2026-10-15 点名：就摆在短线情绪下面）——
          三块：明日涨停会不会踩到严重异动 / 热门股偏离值前 20 / 近期已踩到的（含次日停牌）。
          判据在加工段第 15 步 abnormalmove 落库，这块只读 /api/market/abnormal-move。
          2026-10-26 起包在 `LazySection` 里：实测 4,154px／1,018 个节点，是首页最长的一节。 */}
      <LazySection label="异动提醒">
        <AbnormalMoveBoard />
      </LazySection>

      {/* 5. 份额轮动 · 本地逐日（analytics.sector_flow_trend，统计量不是信号）。
          10-08 用户点名把它挪到"板块资金趋势榜"**前面**：这张是本地逐日算出来的份额口径
          （有 1,253 场历史），那张是同花顺即时榜逐日落库的净额（10-06 才开始攒）——
          先读得到连续与份额，再看净额，顺序反过来会把"刚建表的那张"当成主口径。 */}
      <SectorRotationBoard data={rotation} loading={!rotDone || rotLoading} onRefresh={() => loadRotation()}
        basis={rotationBasis} onBasis={setRotationBasis} />

      {/* 6. 板块资金趋势榜（行业）：累计两列**读库**，今日即时那一列是现抓截面 */}
      <div className="mb-3 flex items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground"><TrendingUp className="h-4 w-4" /> 板块资金趋势榜</h3>
        <BlockDoc title="板块资金趋势榜" text={[
          "「今日即时」取自同花顺行业资金流的盘中即时值，单位亿元，净流入 = 流入 − 流出。\n" +
          "⚠️ 那边没说明这是主力资金还是全部成交资金，所以不能当作主力净流入来读。",
          ...(flow?.calibers ? Object.values(flow.calibers) : [
            "累计两列读 `market_data.sector_fund_flow_daily`（这张表 2026-10-06 才建，逐日一场、由加工链的 `sectorflow` 步采一次）。",
          ])].join("\n")} />
        <span className="text-[11px] text-muted-foreground/50">
          行业 · 默认按窗口累计由大到小
          {flow?.available ? ` · 库内 ${flow.coverage?.sessions ?? 0} 场（${flow.coverage?.from ?? "—"} ~ ${flow.coverage?.to ?? "—"}）` : ""}
          {(flow?.coverage?.sessions ?? 0) > 0 && (flow?.coverage?.sessions ?? 0) < 60 &&
            ` · 这个源只给当天即时截面，库里才 ${flow?.coverage?.sessions ?? 0} 场 —— 两列累计是这几场的和，不是三个月的和`}
        </span>
      </div>
      <GlassCard className="mb-6">
        {/* 两个时刻的数不能合成一列：截面是"此刻"，累计是"逐日收盘落库"。 */}
        <p className="mb-2 text-[11px] leading-relaxed text-muted-foreground/70">
          左边两列累计读库；最右那列「今日即时」是现抓截面
          {flow?.live_stored_today === false ? "（本场还没落库，别和累计列相加）" :
            flow?.live_stored_today === true ? "（本场已落库，与末场那一列同源）" : ""}。
        </p>
        {flow?.available === false && (
          <p className="mb-2 text-[11px] leading-relaxed text-warning/80">{plain(flow.reason)}</p>
        )}
        {flow?.available && flowMerged.length === 0 && (
          <p className="mb-2 text-[11px] leading-relaxed text-muted-foreground">
            库里有场次，但按行业聚合后一行都没有（每一场的行业名都没落进来源），这一格按空处理。
          </p>
        )}
        {sectors.length === 0 && flowMerged.length === 0 ? (
          pending(ovDone)
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
                  <SortHeader label="行业" sortKey="sector_name" dir={secSort.dirOf("sector_name")} onSort={secSort.toggle} />
                  {flow?.available && <>
                    {/* 列名跟着库里真有几场走（用户 10-10：「近 3 个月累计」一眼是错的）。
                        这个源 2026-10-06 才建、只给当天即时截面，攒不满窗时列名不许承诺三个月。 */}
                    <SortHeader label={`窗口累计(亿)｜库内 ${flow.coverage?.sessions ?? 0} 场`} sortKey="net_sum_recent" dir={secSort.dirOf("net_sum_recent")} onSort={secSort.toggle}
                      title={`单位 亿。窗口按日历往前推 3 个月，库里真落了几场就加几场（现在 ${flow.coverage?.sessions ?? 0} 场：${flow.coverage?.from ?? "—"} ~ ${flow.coverage?.to ?? "—"}）；不满窗时那是这几场的和，不能与满窗的历史值横比`} />
                    <SortHeader label="覆盖以来累计(亿)" sortKey="net_sum_since" dir={secSort.dirOf("net_sum_since")} onSort={secSort.toggle}
                      title={`自 ${flow.window?.since ?? "—"} 起；能算多前取决于表里最早那天，场数见下一列`} />
                    <SortHeader label="库内场数" sortKey="days_recent" dir={secSort.dirOf("days_recent")} onSort={secSort.toggle}
                      title="窗口里落库的场数（不是日历天数）；这一列是几，上面那列累计就是几场的和" />
                    <SortHeader label="末场净流入(亿)" sortKey="net_last" dir={secSort.dirOf("net_last")} onSort={secSort.toggle}
                      title="库里最近一场的净流入（不是此刻现抓的那个数）" />
                  </>}
                  <SortHeader label="今日即时净流入" sortKey={flow?.available ? "live_net" : "net"} dir={secSort.dirOf(flow?.available ? "live_net" : "net")} onSort={secSort.toggle}
                    title="单位 亿，全列一致；现抓截面，未参与上面的累计" />
                  <SortHeader label="成分股数" sortKey="firms" dir={secSort.dirOf("firms")} onSort={secSort.toggle} />
                </tr>
              </thead>
              <tbody>
                {flow?.available
                  ? secSort.apply(flowMerged).slice(0, 15).map((r) => (
                    <tr key={r.sector_name} className="border-b border-border/30">
                      <td className="px-2 py-2 font-medium">{r.sector_name}</td>
                      <td className={cn("px-2 py-2 font-mono", pctColor(r.net_sum_recent))}>
                        {r.net_sum_recent == null ? "—" : `${r.net_sum_recent > 0 ? "+" : ""}${fmt(r.net_sum_recent)}`}
                      </td>
                      <td className={cn("px-2 py-2 font-mono", pctColor(r.net_sum_since))}>
                        {r.net_sum_since == null ? "—" : `${r.net_sum_since > 0 ? "+" : ""}${fmt(r.net_sum_since)}`}
                      </td>
                      <td className="px-2 py-2 font-mono text-muted-foreground">{r.days_recent ?? "—"}</td>
                      <td className={cn("px-2 py-2 font-mono", pctColor(r.net_last))}>
                        {r.net_last == null ? "—" : `${r.net_last > 0 ? "+" : ""}${fmt(r.net_last)}`}
                        <span className="ml-1 text-[10px] text-muted-foreground/60">{r.net_last_day ?? ""}</span>
                      </td>
                      <td className={cn("px-2 py-2 font-mono", pctColor(r.live_net))}>
                        {r.live_net == null ? "—" : `${r.live_net > 0 ? "+" : ""}${fmt(r.live_net)} 亿`}
                      </td>
                      <td className="px-2 py-2 font-mono text-muted-foreground">{r.firms_last ?? "—"}</td>
                    </tr>
                  ))
                  : secSort.apply(sectors).slice(0, 15).map((s) => (
                    <tr key={s.name} className="border-b border-border/30">
                      <td className="px-2 py-2 font-medium">{s.name}</td>
                      <td className={cn("px-2 py-2 font-mono", pctColor(s.net))}>{s.net > 0 ? "+" : ""}{fmt(s.net)} 亿</td>
                      <td className="px-2 py-2 font-mono text-muted-foreground">{s.firms}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {flow?.available && flowMerged.length > 15 && (
              <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground/60">
                表里只列前 15 行（共 {flowMerged.length} 个行业，其中窗口累计为正的是 {flow.positive_recent} 个）；
                全量与翻页在「板块热度与点火」页的那张「{FLOW_TABLE_LABEL}」表。
              </p>
            )}
          </div>
        )}
      </GlassCard>

      {/* 7. 今日即时净流入（同花顺盘中值）—— 与上面那张"份额轮动（本地逐日）"是两个口径 */}
      <div className="mb-3 flex items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground"><ArrowDownUp className="h-4 w-4" /> 资金轮动 · 今日即时</h3>
        <BlockDoc title="资金轮动 · 今日即时" label={docLabel('今日即时')} text={
          "就是上面那张榜的两头：流入榜只放净流入为正的、流出榜只放为负的，各取前六。\n" +
          "某一边不足六个就只列够格的那几个 —— 普跌日往往只有两三个行业真净流入。\n" +
          "口径同上：同花顺行业资金流盘中即时值，那边没说明是主力还是全部成交资金，不能当主力净流入读。\n" +
          "这一档本地不留历史（每天覆盖），所以它自己拼不出连续几天；跨场的连续与累计在上面那张" +
          "「份额轮动 · 本地逐日」里，那张读的是按场落库的份额统计，口径不同、不能对照着加减。"
        } />
        <span className="text-[11px] text-muted-foreground/50">板块级净流入 / 流出</span>
      </div>
      <div className="mb-2 grid gap-4 md:grid-cols-2">
        {[
          {
            title: "流入 Top", icon: TrendingUp, color: "text-danger",
            rows: sectors.filter((s) => s.net > 0).slice(0, 6),
            empty: "今日没有行业净流入",
          },
          {
            title: "流出 Top", icon: TrendingDown, color: "text-success",
            rows: sectors.filter((s) => s.net < 0)
              .sort((a, b) => a.net - b.net).slice(0, 6),
            empty: "今日没有行业净流出",
          },
        ].map((col) => (
          <GlassCard key={col.title}>
            <h4 className={cn("mb-3 flex items-center gap-1.5 text-sm font-semibold", col.color)}><col.icon className="h-4 w-4" /> {col.title}</h4>
            {col.rows.length === 0 ? (
              ovDone && sectors.length > 0
                ? <p className="py-4 text-center text-sm text-muted-foreground/60">{col.empty}</p>
                : pending(ovDone)
            ) : (
              <div className="space-y-1.5">
                {col.rows.map((s, i) => (
                  <div key={s.name} className="flex items-center gap-3 border-b border-border/30 pb-1.5 text-sm last:border-0">
                    <span className="w-5 text-xs text-muted-foreground/50">{i + 1}</span>
                    <span className="flex-1 truncate">{s.name}</span>
                    <span className={cn("font-mono text-xs", pctColor(s.pct))}>{s.pct > 0 ? "+" : ""}{s.pct}%</span>
                    <span className={cn("w-20 text-right font-mono text-xs", pctColor(s.net))}>{s.net > 0 ? "+" : ""}{fmt(s.net)} 亿</span>
                  </div>
                ))}
              </div>
            )}
          </GlassCard>
        ))}
      </div>

      <UnlockCalendarPanel />
      <Disclaimer />
    </div>
  );
}
