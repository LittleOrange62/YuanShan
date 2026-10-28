import { UnlockCalendarPanel } from "@/components/UnlockCalendarPanel";
import { SectorRotationBoard } from "@/components/home/SectorRotationBoard";
import { AbnormalMoveBoard } from "@/components/home/AbnormalMoveBoard";
import { FirstBoardDailyPanel } from "@/components/home/FirstBoardDailyPanel";
import { RiskSignalPanel } from "@/components/home/RiskSignalPanel";
import type { RotationBasis } from "@/lib/rotation";
import { SortHeader } from "@/components/ui/SortHeader";
import { LazySection } from "@/components/ui/LazySection";
import { useTableSort } from "@/lib/useSort";
import { PAGE_SIZES, canStep, clampPage, pageCount, pageSentence, slicePage } from "@/lib/paging";
import { pageMaterials } from "@/lib/page-materials";
import { useState, useEffect, useRef, Fragment } from "react";
import { pctColor } from "@/lib/colors";
import { Sparkles, Loader2, RefreshCw, Gauge, ArrowDownUp, TrendingUp, TrendingDown, X, Flame, BarChart3, Globe, ChevronDown, ChevronUp, Copy, Check } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { SentimentTrend, type TrendPoint } from "@/components/SentimentTrend";
import { CALIBER_COLUMNS, buildCompareRows, cellText, columnStamps, diffText } from "@/lib/sentimentCompare";
import { Caliber } from "@/components/ui/Caliber";
import { NodeCell, NodeWarn } from "@/components/ui/NodeCell";
import { usePageAskAi } from "@/lib/workspace/pageAskAi";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { finite, plain } from "@/lib/agent";
import { StockLink } from "@/components/ui/StockLink";
import { TemplatePicker } from "@/components/ui/TemplatePicker";
import { emUrl } from "@/lib/quoteLink";
import { useLinkTemplates } from "@/lib/quoteLinkTemplates";
import { api, type IndexQuote, type Quote, type MarketOverview, type ShortTermEmotion, type LianbanStock, type TurnoverTop, type SectorRotation, type GlobalIndex, type MarketSession, type OverseasSnapshot, type OverseasRow, type LiveEmotion } from "@/lib/api";
import { useDeepDive, DeepDivePanel, RunAllButton, type DiveItem } from "@/components/ui/DeepDive";
import { renderPrompt, useTemplatePicker, type PromptValues } from "@/lib/prompts";
import { cn } from "@/lib/utils";

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

// 大盘宽度档的分值 → 语义色（编码：冰点 −2 / 偏弱 −1 / 中性 0 / 偏强 +1 / 普涨 +2）。
// 这里只按分值上色，**不自己判档**：档位文字永远是后端交的那一个词，前端不重算分档。
const breadthTone = (v: number | null | undefined) =>
  v == null ? "text-muted-foreground/50"
    : v >= 1 ? "text-danger" : v <= -1 ? "text-info" : "text-foreground";

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
  const lbSort = useTableSort({ key: "boards", dir: "desc" });
  /** 连板股那一榜原来是**整表铺开**的（2 板以上有多少列多少，热市能到几十行 × 13 列）。
   *  首页的密度规矩：一屏最多 25 行，其余分页 —— 翻得完，且不是一屏数不到底。 */
  const [lbPage, setLbPage] = useState(1);
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
  const [liveEmo, setLiveEmo] = useState<LiveEmotion | null>(null);
  // 昨日连板股的**今日**实时行情。昨日那份的「涨停%」全是 +10%（都涨停了，
  // 没信息量），换成今天的实时涨跌才回答「昨天的高标今天怎么样」。
  const [lianbanQuotes, setLianbanQuotes] = useState<Record<string, Quote>>({});
  // 自动刷新开关：**默认关**（别替用户决定要不要一直打请求），选择记在本地
  const [autoRefresh, setAutoRefresh] = useState<boolean>(
    () => { try { return localStorage.getItem(AUTO_KEY) === "1"; } catch { return false; } });
  // 写 localStorage 失败（无痕模式 / 浏览器存储被拒）要落在页面上，不能静默吞掉：
  // 用户以为开关记住了，实际下次打开还是默认关。
  const [autoErr, setAutoErr] = useState<string | null>(null);

  // 各数据块请求是否已结束：区分「加载中」与「数据源暂不可用」（非交易时段/被限流时后端返回空）
  const [ovDone, setOvDone] = useState(false);
  const [emoDone, setEmoDone] = useState(false);
  const [toDone, setToDone] = useState(false);

  /** 拉这批代码的实时行情（昨日连板股用）。空名单直接返回，别打空请求。
   *  ⚠️ 必须定义在 `loadLive` **之前**：`const` 箭头函数没有提升，
   *  写在后面 `loadLive()` 首次调用就会 ReferenceError（TDZ）。 */
  const refreshLianban = (codes: string[]) => {
    if (!codes.length) return;
    api.quote(codes.join(",")).then(setLianbanQuotes).catch(() => {});
  };

  // 轻量实时组：全是腾讯批量行情，一次一个请求，5 秒一刷不吃力
  const loadLive = () => {
    api.indices().then(setIndices).catch(() => setIdxErr(true));
    api.overseas().then(setOversea).catch(() => {});
    api.marketSession().then(setSession).catch(() => {});
    api.liveEmotion().then(setLiveEmo).catch(() => {});
    // 连板股名单来自「昨日」那份，这里只刷它们的实时价
    refreshLianban((emotion?.lianban_stocks ?? []).map((s) => s.code));
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
    api.emotion().then(setEmotion).catch(() => {}).finally(() => setEmoDone(true));
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

  // 连板股名单是异步来的，首次 loadLive 时它还没回来 —— 名单一到补拉一次
  useEffect(() => {
    refreshLianban((emotion?.lianban_stocks ?? []).map((s) => s.code));
  }, [emotion?.date, emotion?.lianban_stocks?.length]);

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
    "昨日连板股当前行情": lianbanQuotes,
  });
  // 登记给右侧「AI分析」栏：这一屏每次都交**当下这份**材料，不缓存旧盘面
  usePageAskAi({
    label: "首页 · 盘面数据",
    context: dataSummary,
    suggestions: ["根据已有材料解释这场盘面", "哪些指数领涨领跌", "哪些判断还缺少证据"],
  });

  // 连板股「AI 深入分析」（与首板分析页共用 DeepDive 单元；结果本地存档，跨加载复用）
  const dd = useDeepDive("lianban", emotion?.date || "");
  // 话术不在这里写死：模板从库里读（内置槽位 + 用户在系统配置页自建的模板），这一行只带数据。
  // 选哪一份记在浏览器本地（按类型一个键）；取不到模板时 `render()` 交回 null → prompt 是空串
  // → DeepDive 会拦住并说明原因，绝不退回"代码里再抄一份话术"（那就是第二个真相源）。
  const dive = useTemplatePicker("lianban");
  const divePrompt = (s: LianbanStock) =>
    (dive.current ? renderPrompt(dive.current.template, lianbanValues(s)) : null);
  const copyPrompt = async (s: LianbanStock) => {
    const text = divePrompt(s);
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(s.code);
      setTimeout(() => setCopied(""), 1600);
    } catch { setCopied(`fail:${s.code}`); setTimeout(() => setCopied(""), 2400); }
  };
  const [copied, setCopied] = useState("");
  // 按市场的行情外链模板（系统配置那张表）；没拿到之前按出厂口径拼。
  const linkTpl = useLinkTemplates();
  const lianbanValues = (s: LianbanStock): PromptValues => {
    const q = lianbanQuotes[s.code];
    return {
      date: emotion?.date || "",
      name: s.name, code: s.code,
      em_line: emUrl(s.code, linkTpl) ? `东财行情页（公开榜单，可核对分笔与封单）：${emUrl(s.code, linkTpl)}\n` : "",
      close: s.price, pct: s.pct, boards: s.boards,
      quote_line: finite(q?.change_pct) !== null
        ? `另附行情：${q.price} 元（${q.change_pct > 0 ? "+" : ""}${q.change_pct}%），报价时点 ${q.quote_time || "未提供"}；按报价自身日期解释，不冒充研究日收盘或今日盘中值。`
        : "",
      amount: yi(s.amount), float_cap: yi(s.float_cap),
      industry: s.industry || "未知", reason: s.reason || "尚未核实",
    };
  };
  const lianbanCtx = (s: LianbanStock) => `连板股 ${s.name}(${s.code}) ${s.boards}连板 深入分析`;
  const lianbanItem = (s: LianbanStock): DiveItem => ({
    key: s.code, prompt: divePrompt(s) ?? "", context: lianbanCtx(s),
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
  /** 连板榜：排序在分页之前，页码夹回有效范围（热市 2 板以上能到几十行，首页不许一屏数不到底）。 */
  const lbSorted = lbSort.apply(emotion?.lianban_stocks ?? []);
  const lbSize = PAGE_SIZES[0];
  const lbPages = pageCount(lbSorted.length, lbSize);
  const lbCur = clampPage(lbPage, lbPages);
  const lbPaged = slicePage(lbSorted, lbCur, lbSize);
  const sortByLb = (key: string) => { setLbPage(1); lbSort.toggle(key); };

  return (
    <div id="market-panorama">
      {autoErr && <p role="alert" className="text-danger">{autoErr}</p>}
      <PageHeader
        level={2}
        title="盘面数据"
        subtitle={`${session?.label ?? `场次未探测到（这里不拿本机时钟冒充行情场次）`} · 指数 / 外围 / 板块资金一屏看全（复盘统计在「复盘报告」，首板样本在「首板分析」，自选行情在「分组自选」）`}
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
          <Caliber text={
            "涨跌幅对比前一交易日收盘。\n" +
        "「实时」是延时行情，页面上没标截至几点。"
          } />
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
        <button onClick={loadIndices} className="text-muted-foreground hover:text-primary" title="刷新"><RefreshCw className="h-3.5 w-3.5" /></button>
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
              <button type="button" onClick={() => setOverseaOpen(v => !v)} aria-expanded={overseaOpen}
                className="ml-auto flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11px] text-primary transition-colors hover:bg-muted/50">
                {overseaOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                {overseaOpen ? "收起" : "展开"}
              </button>
              <Caliber text={
                "美股港股的涨跌幅都是对比它们各自的前一交易日收盘。\n" +
            "港股在北京时间白天可能正在交易，所以会标「盘中」——那是抓取那一刻的延时行情，没标具体几点。"
              } />
              <span className="text-[11px] text-muted-foreground/50">A 股常看美股 / 港股脸色</span>
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
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground"><Gauge className="h-4 w-4" /> 市场情绪 · 三口径对照</h3>
        <Caliber text={
          "同一件事这里有三把尺：乐咕乐股（源站口径）、东财涨停池（盘中即时截面）、本地库（已收盘按本地日线判据自算）。\n" +
          "三列各数各的，谁也不顶替谁：数字不等是口径不同，不是谁算错（同一场 10-23 实测：封住 84 对 82、炸板 19 对 12、跌停 9 对 6）。\n" +
          "每一列的表头带着它自己的时间与分母；哪一列没读到就写 —，不拿别列的数顶上，也不填 0。\n" +
          "大盘宽度按涨跌家数机械分档：上涨不足 600 家为冰点，其余看 上涨÷下跌 的比值\n" +
          "（<0.7 偏弱 / 0.7-1.2 中性 / 1.2-2.5 偏强 / ≥2.5 普涨）。\n" +
          "题材投机只按真实涨停家数分档：<30 冰点 / 30-59 普通 / 60-99 活跃 / ≥100 亢奋。\n" +
          "下面那两条折线画的是**近 20 场的同一档读数**，0 轴分别是「中性」与「普通/活跃的中间」；" +
          "每日读数已入库（analytics.market_sentiment_daily），图读库、不是拿此刻现算。"
        } />
        {sentiment?.date && <span className="text-[11px] text-muted-foreground/50">{sentiment.date}</span>}
      </div>
      <GlassCard className="mb-6">
        <p className="mb-3 text-xs text-muted-foreground">三把尺分别统计，计数不一致时保留来源差异并排摆着；「本地 −」那两列是展示层算术，不是判据。</p>
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
            {/* 折线读库、不读现抓接口 —— 这一行要说出来，否则用户会以为图上的点是实时算的 */}
            {series?.available && (
              <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground/60">
                这两条线取自 `analytics.market_sentiment_daily`（近 {series.points?.length ?? 0} 场：
                乐咕现抓 {series.sources?.legu ?? 0} 场、由 market_breadth 回算 {series.sources?.local ?? 0} 场），
                每天收盘后由加工链的 `sentiment` 步续一格；回算那几格的涨停数是单边判据，
                题材那一档可能比乐咕口径乐观半档。
              </p>
            )}
          </>
        )}

        {/* 三口径对照表：不跟着乐咕那一档的门走 —— 源站没读到数时，
            实时与本地两列照旧摆出来，别让整张表陪绑（陪绑＝屏上读成"今天什么都没发生"）。 */}
        {(() => {
          const rows = buildCompareRows(sentiment, liveEmo, emotion);
          const stamps = columnStamps(sentiment, liveEmo, emotion);
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
          return (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border/50 text-xs text-muted-foreground">
                    <th className="px-2 py-2 text-left font-medium">指标</th>
                    {CALIBER_COLUMNS.map((c) => (
                      <th key={c.key} className="px-2 py-2 text-right font-medium whitespace-nowrap align-bottom">
                        <span className="flex items-center justify-end gap-1">
                          {c.label}<Caliber text={colCaliber[c.key] || c.sub} />
                        </span>
                        {stamps[c.key].stamp && <span className="mt-0.5 block text-[10px] font-normal text-muted-foreground/50">{stamps[c.key].stamp}</span>}
                        {stamps[c.key].warn && <span className="mt-0.5 block text-[10px] font-normal leading-tight text-warning/80">{plain(stamps[c.key].warn!)}</span>}
                      </th>
                    ))}
                    <th className="px-2 py-2 text-right font-medium whitespace-nowrap" title="本地库那一列减乐咕那一列：算不出来的格子写着为什么算不出来">本地 − 乐咕</th>
                    <th className="px-2 py-2 text-right font-medium whitespace-nowrap" title="本地库那一列减东财实时池那一列（同一场时才是同时刻之比）">本地 − 实时</th>
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
                        {CALIBER_COLUMNS.map((c) => r.values[c.key].note && (
                          <span key={c.key} className="ml-1 block text-[10px] text-muted-foreground/45">
                            {c.label.split(" ")[0]}：{r.values[c.key].note}
                          </span>
                        ))}
                      </td>
                      {CALIBER_COLUMNS.map((c) => (
                        <td key={c.key} className={cn("px-2 py-1.5 text-right font-mono",
                              r.values[c.key].v == null ? "text-muted-foreground/40"
                                                       : r.kind === "count" && c.key !== "legu" ? "text-foreground"
                                                                                                 : "text-foreground")}>
                          {cellText(r, c.key)}
                        </td>
                      ))}
                      <td className="px-2 py-1.5 text-right font-mono text-[11px] text-muted-foreground">{diffText(r, "vsLegu")}</td>
                      <td className="px-2 py-1.5 text-right font-mono text-[11px] text-muted-foreground">{diffText(r, "vsLive")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {/* 涨跌家数这一族只有乐咕一条来源，宽度那一档就是按它分的 —— 表里那三行会带两列 —，
                  这一句要说出来，否则看着像"另外两列漏了数" */}
              <p className="mt-1.5 text-[10px] leading-relaxed text-muted-foreground/60">
                行上的 — 有两种：那一把尺本来没有这个指标（乐咕不收炸板、也不数连板），或这一列这一刻没读到数 ——
                后者一定在表头写了为什么。晋级率那一行不算差：两列的「上一场」不是同一天，分母不同一批票。
              </p>
            </div>
          );
        })()}
      </GlassCard>

      {/* 4b. 短线情绪（连板梯队 / 打板情绪 + 2 板以上清单）
          头部那两组格子（涨停/跌停/最高连板/连板 + 封板率/炸板率/晋级率）10-27 已经并进上面那张
          三口径对照表，这里只留清单本身 —— 同一批数在屏上出现两遍，用户会先怀疑哪一遍是真的。 */}
      <div className="mb-3 flex items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground"><Flame className="h-4 w-4" /> 短线情绪 · {emotion?.date || "资料日待确认"}</h3>
        <Caliber text={
          "母体自 2026-10-26 起在本地库里（analytics.limit_up_pool_daily，加工段每日落库），这一块不再每次开页现抓东财池。\n" +
          (emotion?.caliber || "") + "\n" +
          "封板率 / 炸板率：分母是本场摸过涨停的家数（收盘封住 + 收盘没封住），按家数不按开板次数。\n" +
          (emotion?.seal_rule || "")
        } />
        <span className="text-[11px] text-muted-foreground/50">已收盘那一场 · 本地库判据（含北交所与 ST）· 客观公开榜单</span>
        {/* 话术模板选择：两处（这里与首板分析）共用 TemplatePicker，不各写一份。 */}
        <TemplatePicker templates={dive.templates} currentId={dive.currentId} onPick={dive.choose} scopeLabel="连板股" />
        {dive.error && <span className="text-[11px] text-danger">话术模板没取到：{dive.error}（去系统配置 → AI 提示词看这一档的生效来源）</span>}
        {/* 后端算出的日期口径（库里最近一场 ≠ 日历最近交易日时才有话），非空必须上屏 */}
        {emotion?.caliber_note && (
          <p className="mt-1 w-full rounded-lg bg-warning/10 px-2 py-1 text-[11px] leading-relaxed text-warning">
            {emotion.caliber_note}
          </p>
        )}
        {/* 这块锚在**已收盘那一场**（复盘口径），不跟上面的实时行情一起刷 ——
            不标出来会和上面的实时块混在一起，让人以为它也在跳 */}
        {emotion?.date && (
          <span className="ml-auto text-[11px] text-muted-foreground/50">
            {emotion.date} 收盘快照 · 只有表格里标（实时）的两列随盘刷新
          </span>
        )}
      </div>
      <GlassCard className="mb-6">
        {!emotion || emotion.zt_count === undefined ? (
          pending(emoDone)
        ) : emotion.available === false ? (
          /* 库里没有这一场的行 ≠ 今天没有涨停。这句"该跑哪一步"必须念出来，
             否则读者会以为今天盘面上风平浪静 */
          <p className="rounded-lg bg-warning/10 px-3 py-2 text-xs leading-relaxed text-warning">
            {emotion.reason || "这一块读不到数"}
          </p>
        ) : (
          <>
            {/* 连板股清单（2 板以上，客观公开榜单） */}
            <div className="mt-3">
              <div className="mb-1.5 flex flex-wrap items-center gap-2">
                <p className="text-[11px] text-muted-foreground">连板股（2 板以上连续封住）· 客观公开榜单，非推荐 / 非预测 · 默认序：连板由高到低，同连板按首封先后（首封是本地分钟线自算、存到秒，显示只到分钟；同一秒的看强度）</p>
                <span className="ml-auto">
                  <RunAllButton
                    dd={dd}
                    items={emotion.lianban_stocks.map(lianbanItem)}
                    nameOf={(k) => emotion.lianban_stocks.find((s) => s.code === k)?.name || k}
                  />
                </span>
              </div>
              {/* 「节点」这一列的 — 要怎么读：后端那句声明非空就摆出来，不折进任何默认收起的框 */}
              <NodeWarn text={emotion.node_warn} />
              {emotion.lianban_stocks.length === 0 ? (
                <p className="text-xs text-muted-foreground/50">今日无 2 板以上个股</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
                        <SortHeader label="名称" sortKey="name" dir={lbSort.dirOf("name")} onSort={sortByLb} className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                        <SortHeader label="连板" sortKey="boards" dir={lbSort.dirOf("boards")} onSort={sortByLb} className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                        {/* 「几天几板」的口径文字由后端交（判据只住 duanxian/board_span.py），
                            页面不自己复述"几天算断"的规则；这里只补一句它与「连板」的区别。 */}
                        <SortHeader label="几天几板" sortKey="board_ct" dir={lbSort.dirOf("board_ct")} onSort={sortByLb}
                          title={`${emotion.days_boards_rule || "这一列的口径文字由后端交：回溯若干个交易日、从今天往前数这一轮、连续几天没封住才算断。"}\n与「连板」的区别：那一列是东财池里的当前连板数，断板一天再封就回到 1；这一列仍算在同一轮里，所以会出现「5天4板」而连板只有 1。缺数显示 —（数不出来，不是 0 板）。${emotion.days_boards_warn ? `\n注意：${emotion.days_boards_warn}` : ""}`}
                          className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                        {/* 「首板日大盘情绪」：口径与缺档说明由后端交（判据只住 duanxian/pg_sources.py），
                            页面不重算分档、只按分值上色。排序键用的是格子里那个分值本身，不是档位文字。 */}
                        <SortHeader label="首板日大盘情绪" sortKey="first_board_breadth_score"
                          dir={lbSort.dirOf("first_board_breadth_score")} onSort={sortByLb}
                          title={`${emotion.first_board_rule || "这一列的口径文字由后端交：首板日取这一轮最早的封板日，档位取 analytics.market_sentiment_daily。"}${emotion.first_board_warn ? `\n注意：${emotion.first_board_warn}` : ""}`}
                          className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                        {/* 「节点」：判据只住 app/compute/board_node.py，读法与措辞只住
                            duanxian/node_view.py + lib/nodeCell.ts —— 与首板分析页同一份实现。 */}
                        <SortHeader label="节点" sortKey="node_hits"
                          dir={lbSort.dirOf("node_hits")} onSort={sortByLb}
                          title={`${emotion.node_rule || "这一列的判据原文后端没交（node_rule 为空），页面不自己复述规则。"}${emotion.node_warn ? `\n注意：${emotion.node_warn}` : ""}`}
                          className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                        <SortHeader label="首封" sortKey="seal_time" dir={lbSort.dirOf("seal_time")} onSort={sortByLb}
                          title={`${emotion.seal_rule || "这一列的算法文字后端没交（seal_rule 为空），页面不自己复述口径。"}\n标了〈源〉的那一行：这一场本地分钟线没读到，取的是同花顺池的秒级首封时刻。`}
                          className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                        {/* 「封板强度」的算法说明**由后端交过来**（判据只住 duanxian/seal_strength.py，
                            页面这里显示的就是那一份，不另写一套分档）。后端没给（旧进程）时老实说没有。 */}
                        <SortHeader label="封板强度" sortKey="seal_score"
                          dir={lbSort.dirOf("seal_score")} onSort={sortByLb}
                          title={emotion.seal_spec || "后端没交算法说明（这一列按强度排还是能点的；口径要看说明请刷新）"}
                          className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                        <th className="whitespace-nowrap px-2 py-2 font-medium" title="这两列的值来自另一路实时行情（不在榜行里），给排序就会拿样本日收盘价冒充实时值，所以不给点">现价（实时）</th>
                        <th className="whitespace-nowrap px-2 py-2 font-medium">今日涨跌（实时）</th>
                        <SortHeader label="样本日成交额" sortKey="amount" dir={lbSort.dirOf("amount")} onSort={sortByLb} title="样本日（不是今天）的成交额，来自本地日线 market_data.stock_daily" className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                        <SortHeader label="流通市值" sortKey="float_cap" dir={lbSort.dirOf("float_cap")} onSort={sortByLb} title="优先取同花顺池原值；池里没有的票（北交所、ST 居多）用当日日线反推（100 × 成交额 ÷ 换手率）。两把尺都在这张库里，行上标了用的是哪一种（悬停可看）" className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                        <SortHeader label="涨停原因" sortKey="reason" dir={lbSort.dirOf("reason")} onSort={sortByLb} title="同花顺涨停揭秘池的编辑归因（非公司确认）。池只含沪深主板/创业板/科创板、基本不含 ST ⇒ 空着多半是「源站不收这只票」，不是「本机没采到」。源站保留窗约一年，过期就补不回来" className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                        <SortHeader label="概念" sortKey="industry" dir={lbSort.dirOf("industry")} onSort={sortByLb} title="本地 reference_data.sector_member（板块类型=概念，按名字去重，成员数少的在前＝窄题材优先），列前 3 个并给总数；显示 — 是库里没有这一档，不等于它不属于任何题材（北交所基本无概念行）" className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                        <th className="whitespace-nowrap px-2 py-2 font-medium"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {lbPaged.map((s) => (
                        <Fragment key={s.code}>
                          <tr className="border-b border-border/30">
                            <td className="px-2 py-2"><StockLink code={s.code} name={s.name} className="font-medium hover:text-primary" /> <StockLink code={s.code} className="text-xs text-muted-foreground/50 underline decoration-dotted underline-offset-2 hover:text-primary">{s.code}</StockLink></td>
                            <td className="whitespace-nowrap px-2 py-2 font-mono font-bold text-primary">{s.boards} 板</td>
                            {/* 悬停给出这一轮真正的封板日，读者可以自己数一遍"几天几板"；
                                数不出来时显示 —，并在悬停里说是哪一天缺行，不写成 0 板。 */}
                            <td className="whitespace-nowrap px-2 py-2 font-mono text-muted-foreground"
                              title={s.days_boards
                                ? `这一轮的封板日：${(s.board_dates ?? []).map(x => String(x).slice(5)).join("、") || "—"}`
                                  + `${s.src_high_days ? `　｜　源站那份写的是「${s.src_high_days}」（两把尺都留着，本地这份不改它）` : ""}`
                                : "本地日线没数出这一轮（多半是 stock_daily 缺当天那行）—— 数不出 ≠ 没有板"}>
                              {s.days_boards || <span className="text-muted-foreground/50">—</span>}
                            </td>
                            {/* 首板日 = 这一轮最早的那个封板日（不是今天）。格子里显示的是宽度档，
                                档位由后端判、前端只按分值上色；悬停给完整输入（宽度/题材/涨停家数/涨跌家数/大盘情绪分注记）。
                                空着有两种原因，悬停要分开说 —— 都不是「那天没情绪」。 */}
                            <td className="whitespace-nowrap px-2 py-2 text-xs" title={s.first_board_note || ""}>
                              {s.first_board_breadth ? (
                                <span className="inline-flex items-baseline gap-1">
                                  <span className="font-mono text-[10px] text-muted-foreground/50">{String(s.first_board_day ?? "").slice(5)}</span>
                                  <span className={cn("font-medium", breadthTone(s.first_board_breadth_score))}>{s.first_board_breadth}</span>
                                  {typeof s.first_board_breadth_score === "number" && (
                                    <span className="font-mono text-[10px] text-muted-foreground/60">
                                      {s.first_board_breadth_score > 0 ? "+" : ""}{s.first_board_breadth_score.toFixed(1)}
                                    </span>
                                  )}
                                </span>
                              ) : (
                                <span className="text-muted-foreground/50"
                                  title={s.first_board_day
                                    ? `首板日 ${s.first_board_day} 在 analytics.market_sentiment_daily 里没有行 —— 不是「那天没情绪」，是这条记录缺`
                                    : "这一轮的封板日本地没数出来（原因看「几天几板」那一列），所以首板日无从定位"}>—</span>
                              )}
                            </td>
                            {/* 「节点」那一格与首板分析页调的是同一份组件（`components/ui/NodeCell.tsx`）。
                                「—」的三种来历（没这一行 / 这一场判不了 / 真没命中）由悬停与那句 node_warn 分开说。 */}
                            <td className="whitespace-nowrap px-2 py-2 text-center">
                              <NodeCell row={s} rule={emotion.node_rule} />
                            </td>
                            {/* 首封：本地分钟线自算。退回源站/两边都没有都要在格子里说出来 ——
                                同一个列名底下混两把尺而不标注，是这一屏最容易糊过去的一件事。 */}
                            <td className="whitespace-nowrap px-2 py-2 font-mono text-muted-foreground"
                              title={s.seal_basis === "source"
                                ? `这一场本地分钟线没读到，这一格取的是同花顺池的秒级首封 ${s.src_seal_time ?? "—"}（列头悬停有两条尺的差别）`
                                : s.seal_basis === "none"
                                  ? "分钟线里没有任何一根收盘封在档位上，源站那一份也没有 ⇒ 判不了，不是 00:00、也不是没封住"
                                  : `本地 1 分钟线自算${s.src_seal_time ? `（源站写的是 ${s.src_seal_time}，差一分钟以内属分钟收盘与秒级撮合的固有差）` : ""}${typeof s.at_limit_minutes === "number" ? `｜封在档位上 ${s.at_limit_minutes} 分钟／共 ${s.minute_bars ?? 0} 根` : ""}`}
                              >{s.seal_time || "—"}{s.seal_basis === "source" && <span className="ml-0.5 text-[10px] text-warning/80">源</span>}</td>
                            {/* 分数只说"这一场封得硬不硬"（口径与权重看列头悬停）；
                                这一格自己的悬停给出**这一行为是这个分**的三个输入。缺数显示 —，不当 0 分。 */}
                            <td className="whitespace-nowrap px-2 py-2 font-mono" title={s.seal_note || ""}>
                              {typeof s.seal_score === "number" ? (
                                <span className={cn("font-bold",
                                  s.seal_score >= 70 ? "text-danger" : s.seal_score >= 40 ? "text-foreground" : "text-muted-foreground/60")}>
                                  {s.seal_score}
                                </span>
                              ) : <span className="text-muted-foreground/50" title="封单、开板次数、首封时间三项都没给：算不出强度，不当成 0 分">—</span>}
                            </td>
                            <td className="px-2 py-2 font-mono">
                              {lianbanQuotes[s.code]?.price ?? (
                                <span className="text-muted-foreground/50" title="实时行情未取到，显示昨收">
                                  {s.price}
                                </span>
                              )}
                            </td>
                            <td className={cn("px-2 py-2 font-mono",
                                              pctColor(finite(lianbanQuotes[s.code]?.change_pct)))}>
                              {finite(lianbanQuotes[s.code]?.change_pct) !== null
                                ? `${lianbanQuotes[s.code].change_pct > 0 ? "+" : ""}${lianbanQuotes[s.code].change_pct}%`
                                : <span className="text-muted-foreground/50" title="实时行情未取到">—</span>}
                            </td>
                            <td className="whitespace-nowrap px-2 py-2 font-mono text-muted-foreground">{yi(s.amount)}</td>
                            <td className="whitespace-nowrap px-2 py-2 font-mono text-muted-foreground"
                              title={s.float_cap_basis === "daily_replay"
                                ? "这只票不在同花顺涨停池里（北交所、ST 居多），流通市值是当日日线反推的（100 × 成交额 ÷ 换手率）"
                                : "同花顺涨停池原值 currency_value"}>{yi(s.float_cap)}</td>
                            <td className="max-w-56 px-2 py-2 text-xs">
                              {s.reason ? <span className="text-foreground">{s.reason}</span> : <span className="text-muted-foreground/50">—</span>}
                            </td>
                            <td className="whitespace-nowrap px-2 py-2 text-xs text-muted-foreground">{s.industry || <span className="text-muted-foreground/50">—</span>}</td>
                            <td className="whitespace-nowrap px-2 py-2 text-right">
                              <div className="inline-flex items-center gap-1">
                                {/* 复制这一行按当前模板渲染好的提示词（用户 09-28 要的就是这两步：
                                    复制内容 + 选模板 → 一键 AI 分析）。复制不需要 AI 接入，随时可拿。 */}
                                <button
                                  onClick={() => void copyPrompt(s)}
                                  disabled={!dive.current}
                                  title={copied === s.code ? "已复制到剪贴板"
                                    : copied === `fail:${s.code}` ? "浏览器拒绝了剪贴板访问，请在页面里手动复制"
                                    : "按当前模板把这一行的提示词复制到剪贴板"}
                                  className="inline-flex items-center gap-1 rounded-lg border border-border/60 px-1.5 py-1 text-[11px] text-muted-foreground hover:border-primary hover:text-primary disabled:opacity-40">
                                  {copied === s.code ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                                  {copied === s.code ? "已复制" : copied === `fail:${s.code}` ? "复制失败" : "复制提示词"}
                                </button>
                                <button
                                  onClick={() => dd.toggle(lianbanItem(s))}
                                  className="inline-flex items-center gap-1 rounded-lg border border-primary/50 bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
                                >
                                  {dd.running === s.code ? <Loader2 className="h-3 w-3 animate-spin" /> : dd.open === s.code ? <X className="h-3 w-3" /> : <Sparkles className="h-3 w-3" />}
                                  {dd.open === s.code ? "收起" : dd.analysis[s.code] ? "展开" : "深入分析"}
                                </button>
                              </div>
                            </td>
                          </tr>
                          {dd.open === s.code && (
                            <DeepDivePanel
                              dd={dd}
                              stockKey={s.code}
                              colSpan={13}
                              noteTitle={`连板深析 · ${s.name} ${s.boards}板`}
                              onRerun={() => dd.rerun(lianbanItem(s))}
                            />
                          )}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                  {/* 分页那句与按钮全站一份算法；按钮只在真能翻的时候出现（点了没反应等于摆假控件） */}
                  <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px]">
                    <span className="text-muted-foreground">{pageSentence(lbSorted.length, lbSize, lbCur)}</span>
                    {canStep(lbCur, lbSize, lbSorted.length, -1) && (
                      <button type="button" onClick={() => setLbPage(lbCur - 1)}
                        className="rounded-lg bg-muted/40 px-2 py-0.5 hover:bg-muted/70">上一页</button>)}
                    {canStep(lbCur, lbSize, lbSorted.length, 1) && (
                      <button type="button" onClick={() => setLbPage(lbCur + 1)}
                        className="rounded-lg bg-primary/15 px-2 py-0.5 font-medium text-primary hover:bg-primary/25">下一页</button>)}
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </GlassCard>

      {/* 4d. 首板分析（用户 2026-10-21 点名：就摆在「全市场换手率 TOP20」上面）。
          原话：「全市场换手率 TOP20 上面加一个首板分析，分析首板个股的情况，已经近期的走势，
          分析涨停原因，特别是前一天的走势」—— 所以"前一天的走势"是这一块的主体（表里单独成组）。
          判据在加工段 `fbdaily` 步落 `analytics.first_board_daily`，这块只读 /api/quant/first-board-daily。
          2026-10-26 起包在 `LazySection` 里：这一块实测 2,645px／1,014 个节点，自己取数自己带控件。 */}
      <LazySection label="首板分析">
        <FirstBoardDailyPanel />
      </LazySection>
      <div className="mb-3 flex items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground"><BarChart3 className="h-4 w-4" /> 全市场换手率 TOP20</h3>
        <Caliber text={
          "沪深京 A 股按当日累计换手率从大到小排（换手率 = 成交量 ÷ 流通股本）。\n" +
          "排序在**取数那一侧**就定了（东财 clist 按 f8 取前 20 名）。先取成交额榜、再在前端按换手率重排，\n" +
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
          <details className="ml-auto text-[11px] text-muted-foreground/60">
            <summary className="cursor-pointer">这一场没拿到榜单（点开看为什么）</summary>
            <p className="mt-1 max-w-[46rem] leading-relaxed">
              {plain(turnover?.reason
                || (toDone ? "东财 clist 这一轮没返数据（限流、口径变了或网络不通）—— 没返数不等于今天没人换手。"
                           : "还在取数中：这张榜要现抓东财，回来之前这里不摆空表。"))}
            </p>
          </details>
        ) : (
          turnover?.updated && <span className="ml-auto text-[11px] text-muted-foreground/50">参考行情日 {turnover.quote_date || "未确认"} · 抓取于 {turnover.updated}</span>
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
        <Caliber text={[
          "「今日即时」取自同花顺行业资金流的**盘中即时值**，单位亿元，净流入 = 流入 − 流出。\n" +
          "⚠️ 那边没说明这是主力资金还是全部成交资金，所以**不能当作主力净流入**来读。",
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
        <Caliber text={
          "就是上面那张榜的两头：流入榜只放净流入为正的、流出榜只放为负的，各取前六。\n" +
          "某一边不足六个就只列够格的那几个 —— 普跌日往往只有两三个行业真净流入。\n" +
          "口径同上：同花顺行业资金流盘中即时值，那边没说明是主力还是全部成交资金，不能当主力净流入读。\n" +
          "这一档本地不留历史（每天覆盖），所以**它自己**拼不出连续几天；跨场的连续与累计在上面那张" +
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
