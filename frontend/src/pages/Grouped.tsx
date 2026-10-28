import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { RefreshCw, Microscope } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { CaliberFold, CaveatText, ExplainBox, Measured, Panel, Row } from "@/components/ui/DataPanel";
import { useNonce, useProbe } from "@/lib/probe";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useCellSelection, useStockRowInteraction, type CellStore } from "@/lib/useStockRowInteraction";
import { NewHighBoard } from "@/components/quant/NewHighBoard";
import { GroupAnalyticsBoard, type Analytics, type GroupStat } from "@/components/quant/GroupAnalyticsBoard";
import { dash, describeState, num } from "@/lib/datastate";
import { plain } from "@/lib/agent";
import { pctColor } from "@/lib/colors";
import { cn } from "@/lib/utils";
import { loadWatch } from "@/lib/watchlist";
import { Watchlist } from "./Watchlist";
import { StockLink } from "@/components/ui/StockLink";

/**
 * 分组自选（规划 §2 维度三）。
 *
 * 数据在库里早就有（`config.watchlist_group` 45 组 / `watchlist_stock` 1283 条），
 * 之前这页看不到是因为 `lib/watchlist.ts` 只读浏览器 localStorage 的一个扁平代码数组
 * —— 上限 100 只、没有分组、没有成本、没有备注。所以这一屏是**把已有的读出来**，不新算指标。
 *
 * 三条实测口径直接写在屏幕上，不藏 hover：
 *  · `is_focus` 45 组无一为真 —— 是用户没勾过，不是同步坏了，所以**不造默认组**；
 *  · `chg10_avg` / `amp10_avg` 是**日均**（每天平均动几个点），与同键去掉 `_avg` 的 `chg10`
 *    （区间合计）同名不同义 —— 两把尺子各占一列并排放着，2026-10-14 用户问"为什么数值这么小"之后；
 *  · 同一只票可以属多个组，所以各组票数之和大于去重数不是 bug。
 */

interface GroupRow {
  group_id?: number | null; name?: string | null; is_focus?: boolean | null; display_order?: number | null;
  note?: string | null; stock_count?: number | null; avg_cost?: number | null; cost_filled?: number | null;
  avg_pct?: number | null; up?: number | null; down?: number | null; missing?: number | null;
}
interface Groups {
  available?: boolean | null; reason?: string | null; date?: string | null; groups?: GroupRow[] | null;
  total_stocks?: number | null; focus_groups?: number | null; caveat?: string | null; source?: string | null;
  coverage?: Record<string, { from?: string | null; to?: string | null; sessions?: number | null }> | null;
}
interface StockRow {
  group_name?: string | null; group_id?: number | null; security_code?: string | null; name?: string | null;
  close?: number | null; pct_change?: number | null; turnover_rate_percent?: number | null;
  chg10_avg?: number | null; amp10_avg?: number | null; chg20_avg?: number | null; amp20_avg?: number | null;
  chg10?: number | null; chg20?: number | null;
  amp_pred?: number | null; oper?: number | null; quadrant?: string | null; cost?: number | null; note?: string | null;
}
interface Stocks {
  available?: boolean | null; reason?: string | null; date?: string | null; rows?: StockRow[] | null;
  count?: number | null; sort?: string | null; caveat?: string | null; source?: string | null;
  indicator_meaning?: Record<string, string> | null; unit?: Record<string, string> | null;
  score_meaning?: Record<string, string> | null;
}

/** 带单位的数值列：表头把"日均"与"区间"分开写，因为 `chg10_avg=2.89` 极易被读成"涨了 2.89%"，
 *  而它其实是"这 10 天平均每天动 2.89 个点"；紧挨着放的 `chg10` 才是这 10 天合起来的涨跌。 */
const COLUMNS: [keyof StockRow, string, string][] = [
  ["pct_change", "当日涨跌 %", "当日"],
  ["chg10_avg", "10日日均 %", "日均"], ["chg10", "10日区间 %", "区间"],
  ["chg20_avg", "20日日均 %", "日均"], ["chg20", "20日区间 %", "区间"],
  ["amp10_avg", "10日日均振幅 %", "日均"], ["amp20_avg", "20日日均振幅 %", "日均"],
  ["amp_pred", "明日振幅预测 %", "预测"], ["turnover_rate_percent", "换手 %", "当日"],
  ["oper", "操作分", "合成分"],
];
const SIGNED = new Set<string>(["pct_change", "chg10_avg", "chg20_avg", "chg10", "chg20"]);

/** 明细表的一格：点它＝选中这一行（同行同列一起加亮）；双击这一行＝另开一窗看这一只的深度复盘。
 *  列键就是 td 的先后名，与 `useTableCellHighlight` 的交叉高亮共用一份。 */
function Td({ i, k, cell, className, tone, mono, muted, title, children }: {
  i: number; k: string;
  cell: { handleCellClick: (target: HTMLTableCellElement, row: number, col: string) => void;
          getCellClass: (row: number, col: string, base?: string) => string };
  className?: string; tone?: string; mono?: boolean; muted?: boolean;
  title?: string; children?: ReactNode;
}) {
  return (
    <td title={title} onClick={(e) => cell.handleCellClick(e.currentTarget, i, k)}
      className={cell.getCellClass(i, k,
        cn("whitespace-nowrap", mono && "font-mono tabular-nums",
           muted && "text-muted-foreground", tone, className))}>
      {children}
    </td>
  );
}

/** 「已经选中哪一只」那一行提示单独成一个组件，只为一件事：**把选中态的订阅圈在这里**。
 *  10-26 之前这句是在页面组件里读 `cell.selectedCell` 的，于是每一次单击都要把这张
 *  150 行 × 十几列的明细表整个再协调一遍（与倍量页那张 800 格的表同一族：单击一次 461 毫秒的长任务）。
 *  交叉高亮现在写在 DOM 上、页面组件不因点击而渲染，只有这一行跟着换标的。
 *  ⚠️ 别把 `useCellSelection` 提回页面组件 —— 那等于把这条链又接回整张表。
 *  行序只算一份：`rowAt` 读的就是表格渲染的同一个 `shownRows`，高亮的那一行与这里说的不是两只票的可能没有。 */
function SelectedHint({ store, rowAt }: {
  store: CellStore; rowAt: (row: number) => StockRow | null;
}) {
  const sel = useCellSelection(store);
  const row = sel ? rowAt(sel.row) : null;
  if (!sel || !row) return <>{STOCK_ROW_HINT}</>;
  return <>已选中 <b className="text-foreground">{dash(row.security_code)} {dash(row.name)}</b>
    （同一格再点一次取消）—— 双击这一行另开一个窗口看它的深度复盘。</>;
}

/** 组级趋势/打分那一份读数：类型与 `GroupAnalyticsBoard` 共用一份定义（同一接口，两处各写一份接口迟早对不上）。 */
type TrendRow = GroupStat;

/** 同值时的稳定序：先按库里的 display_order，再按组名（拼音） —— 同一份数每次进来必须是同一个序。 */
const sameRank = (a: GroupRow, b: GroupRow) =>
  (a.display_order ?? 0) - (b.display_order ?? 0)
  || String(a.name ?? "").localeCompare(String(b.name ?? ""), "zh");

/**
 * 「分组」那一叠可选的排法（用户 2026-10-14：默认还是按当日涨幅从低到高，
 * 另外要能按组名、组内成员数量这类指标排）。三条纪律：
 *  · **缺值一律沉底，不按 0 参与比较** —— 0 是真实读数（平盘、0 只成员），
 *    与"当天没行情/是空组/还没聚合"不是一回事，混排会把后者读成一个假的极值；
 *  · 要现场聚合才有数的那几把尺子（预测分/加权分/趋势分/近10·20日），
 *    在页面最上方那块「分组整体分析」还没出数时是 **disabled**，不是"能选但整屏排成一片空"；
 *  · 方向只作用于主键，同值永远交给 `sameRank` 兜 ⇒ 降序时并列的组仍按库里原序、组名排。
 *  卡片是**一行一个组**（10-14 用户点名：原来 `sm:grid-cols-2` 一行塞两个组、每组又占两行，
 *  一屏数不出几个组），所以数组序就是"第 1 行→第 2 行→…"从上往下读。
 */
interface GroupSortOpt {
  v: string; label: string; desc: string;
  key: 'avg_pct' | 'stock_count' | 'name' | 'display_order' | 'trend';
  dir: 1 | -1; trendKey?: keyof TrendRow;
}
const GROUP_SORTS: GroupSortOpt[] = [
  { v: 'avg_pct:asc', label: '当日涨跌 从低到高（默认）', key: 'avg_pct', dir: 1,
    desc: '当日组内平均涨跌从低到高（跌得最多的在最上面一行，从上往下读）' },
  { v: 'avg_pct:desc', label: '当日涨跌 从高到低', key: 'avg_pct', dir: -1,
    desc: '当日组内平均涨跌从高到低（涨得最多的在最上面一行）' },
  { v: 'stock_count:desc', label: '成员数 从多到少', key: 'stock_count', dir: -1,
    desc: '组内成员数从多到少（大组在前）' },
  { v: 'stock_count:asc', label: '成员数 从少到多', key: 'stock_count', dir: 1,
    desc: '组内成员数从少到多（空组自然落到最前，卡片上写「0 只」）' },
  { v: 'name:asc', label: '组名（拼音序）', key: 'name', dir: 1,
    desc: '组名按拼音字典序排（同音再按库里 display_order）' },
  { v: 'display_order:asc', label: '库里的原顺序', key: 'display_order', dir: 1,
    desc: '按库里 display_order（同花顺同步下来的顺序；卡片末尾那个「序 N」就是它）' },
  { v: 'pred_score:asc', label: '预测操作分 从低到高', key: 'trend', dir: 1, trendKey: 'pred_score',
    desc: '接口算的预测操作分从低到高' },
  { v: 'w_score:asc', label: '加权分 从低到高', key: 'trend', dir: 1, trendKey: 'w_score',
    desc: '接口算的加权分从低到高' },
  { v: 'trend_score:asc', label: '趋势分 从低到高', key: 'trend', dir: 1, trendKey: 'trend_score',
    desc: '窗口斜率（趋势分，分/10 场）从低到高' },
  { v: 'chg10_bp:asc', label: '近 10 日日均涨跌 从低到高', key: 'trend', dir: 1, trendKey: 'chg10_bp',
    desc: '近 10 日日均涨跌（bp/日）从低到高' },
  { v: 'chg20_bp:asc', label: '近 20 日日均涨跌 从低到高', key: 'trend', dir: 1, trendKey: 'chg20_bp',
    desc: '近 20 日日均涨跌（bp/日）从低到高' },
];

/** 某一组在某一排法下的读数：缺就交 null（沉底），不补 0。 */
function groupValue(g: GroupRow, o: GroupSortOpt, tr: Map<number, TrendRow>): number | string | null {
  if (o.key === 'trend') {
    const r = g.group_id != null ? tr.get(g.group_id) : undefined;
    const v = r && o.trendKey ? r[o.trendKey] : null;
    return typeof v === 'number' ? v : null;
  }
  const raw = o.key === 'name' ? g.name : o.key === 'stock_count' ? g.stock_count
    : o.key === 'display_order' ? g.display_order : g.avg_pct;
  if (raw == null) return null;
  return o.key === 'name' ? String(raw) : Number(raw);
}

function sortGroups(gs: GroupRow[], o: GroupSortOpt, tr: Map<number, TrendRow>): GroupRow[] {
  return gs.map(g => ({ g, v: groupValue(g, o, tr) })).sort((x, y) => {
    if (x.v == null || y.v == null) {
      return x.v == null && y.v == null ? sameRank(x.g, y.g) : (x.v == null ? 1 : -1);
    }
    const c = typeof x.v === 'string'
      ? String(x.v).localeCompare(String(y.v), "zh")
      : (x.v as number) - (y.v as number);
    return c * o.dir || sameRank(x.g, y.g);
  }).map(e => e.g);
}

export function Grouped() {
  const [nonce, refresh] = useNonce();
  const [open, setOpen] = useState<number | null>(null);
  const groups = useProbe<Groups>("/watchlist/groups", nonce);
  const stocks = useProbe<Stocks>("/watchlist/stocks", nonce, open == null ? { limit: 2000 } : { group_id: open, limit: 2000 });
  const rowSort = useTableSort();
  const cell = useStockRowInteraction("分组自选");
  const local = useMemo(() => loadWatch(), []);

  /** 排序在截断**之前**（点列头要排整张表，不是只排前 150 行），所以行序在这儿统一算一次：
   *  表格渲染与「哪一行为选中行」必须读同一份，否则高亮的行与说明文字不是同一只票。 */
  const sortedRows = stocks.kind === "ok" ? rowSort.apply(stocks.data.rows ?? []) : [];
  const shownRows = sortedRows.slice(0, 150);
  // 换排序就取消选中：选中的是**行号**，行序一变那个号指向另一只票，高亮与说明就不是同一只了。
  const pickSort = (key: string) => { cell.clearAll(); rowSort.toggle(key); };

  /** 单击交叉高亮 / 双击另开一窗看这一只的「个股深度复盘」—— 实现全站一份
   *  （`lib/useStockRowInteraction.ts`，用户 2026-10-15 点名「但凡有用到的地方都应该引用它」）。
   *  本页只负责把 `popHint`（弹窗被拦的告警）念在屏幕上，且不许折进说明框。 */
  const openReview = cell.openReview;
  const popHint = cell.popHint;

  /** 组级趋势/打分：**默认就取**（用户 2026-10-14：先把分组的整体情况分析完，再看组里的票，
   *  所以那一整块「自选组分析」搬到了本页最上方）。
   *  这一块原来做成一个显式按钮，理由是它无缓存、现场聚合约 4 秒，挂默认加载会让每次进页慢四秒；
   *  搬到顶部之后这个代价变成**看得见的**：由那块表自己的 `slowHint` 与「聚合耗时 N ms」那一格明说，
   *  而不是让用户多点一下才能读到数。
   *  ⚠️ **一份请求**：顶部的表/图/口径、以及下面每张组卡片那 6 格，读的都是这同一个 `DataState`；
   *  原来那个手发动作（`probe()` 一发）已经删掉，页面里没有第二条通往同一接口的手。
   *  数字一律用接口交出的那一份（前端不重算）。 */
  const trend = useProbe<Analytics>("/watchlist/group-analytics", nonce);
  const trendByGroup = useMemo(() => {
    const m = new Map<number, TrendRow>();
    if (trend.kind === "ok") for (const r of trend.data.rows ?? []) if (r.group_id != null) m.set(r.group_id, r);
    return m;
  }, [trend]);
  const cal = (key: string) => (trend.kind === "ok" ? trend.data.calibers?.[key] : undefined) ?? "";
  const unitOf = (key: string) => (trend.kind === "ok" ? trend.data.unit?.[key] : undefined) ?? "";

  // 排法：默认当日涨跌从低到高（用户 2026-10-14 点名），其余指标见 `GROUP_SORTS`。
  // 取不到 option 时（老 URL/以后删项）退回默认那一条，不静默变成"不排序"。
  const [groupSort, setGroupSort] = useState("avg_pct:asc");
  const gso = GROUP_SORTS.find(o => o.v === groupSort) ?? GROUP_SORTS[0];
  const trendReady = trend.kind === "ok";

  const localCodes = useMemo(() => {
    if (stocks.kind !== "ok") return null;
    const inDb = new Set((stocks.data.rows ?? []).map(r => String(r.security_code ?? "").slice(0, 6)));
    return { missing: local.filter(c => !inDb.has(c)), total: local.length };
  }, [stocks, local]);

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader title="分组自选" level={1}
        subtitle="读库里已有的分组与明细：组、票数、当日涨跌、日均振幅、操作分象限、备注。这一叠不新算任何指标；双击明细的某一行，另开一个窗口看那只票的深度复盘（档案 / 趋势量化 / 个股研究 / 多空辩论）。"
        actions={<button type="button" onClick={() => { cell.clearAll(); refresh(); }}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>} />

      <div className="space-y-4">
        {/* 用户 2026-10-14：「自选组分析这个页面的功能放到 我的股票-分组自选 里面去，放在这个页面的最上方。
            也就是先分析分组的整体情况，再分析分组自选。」原独立屏整块搬到这里、路由与导航条目一并撤掉，
            表/图/口径那一份实现住在 `components/quant/GroupAnalyticsBoard.tsx`（只有一份，见 `sortKeys.test.ts`）。 */}
        <GroupAnalyticsBoard got={trend} />

        <Panel title="分组" state={groups} note="点组名把下面的明细筛到这一组；回到全部要么再点一次那个组名，要么点下面的「全部组」。">
          {d => <>
            <Row label="行情日" value={dash(d.date)} />
            <Row label="组数 / 明细条数" value={`${(d.groups ?? []).length} 组 / ${dash(d.total_stocks)} 条`} />
            <Row label="这一叠的顺序"
              value={`${gso.desc}；缺这个读数的组排在最后（不按 0 参与比较），同值按库里 display_order、再按组名兜底`} />
            <Row label="焦点组（is_focus 为真）"
              value={d.focus_groups === 0 ? "0 个 —— 全库没有一组勾过重点。这是没勾过，不是同步坏了，所以这里不凭空造一个默认组。" : dash(d.focus_groups)}
              tone={d.focus_groups === 0 ? "warn" : undefined} />
            <CaveatText text={d.caveat} />
            {/* 覆盖区间与出处是"这一屏从哪来"的描述，收进一处折叠（用户 2026-10-21）；
                上面那句 caveat（库里这份分组可能落后于客户端）是缺数类，留在屏上没有折 */}
            <CaliberFold coverage={d.coverage} provenance={[d.source]} />
            <Measured>
              库里这份分组<b className="text-foreground">可能落后于同花顺客户端的当前状态</b>：
              分组的真源是同花顺本地文件 <code>stockblock.ini</code>，同步作业要靠调度触发，而本项目目前无调度。
              接口没有交出"这份快照是哪一刻写进库的"，所以本页无法判断它新不新 ——
              需要 <code>synced_at</code>（库内写入时间）与 <code>source_mtime</code>（ini 的修改时间）两个字段才能把这格变成实数。
              <b className="text-foreground">在拿到这两个字段之前，请按"库里这份不一定最新"理解下面所有票数。</b>
            </Measured>
            {/* 两个显式控件：筛到一组之后要能一键回到"全部组"（原来只能"再点一次那个组名"，
                而那个动作不明显 —— 少了入口的取消筛选等于让人以为只能一直看着一组）；
                组卡片那 6 格的数已经由最上面那块表取回来了，所以这里**不再有"聚合"按钮**（用户 10-14 裁定默认就显示）。 */}
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button type="button" onClick={() => setOpen(null)} aria-pressed={open === null}
                className={cn("rounded-lg border px-2.5 py-1 text-[12px]",
                  open === null ? "border-primary/70 bg-primary/15 text-primary" : "border-border text-muted-foreground hover:bg-muted/50")}>
                全部组
              </button>
              {/* 排法做成一个下拉而不是十一个按钮：这一叠只有 48 张卡片，切换是纯显示层的事。
                  那五把要靠组级聚合才有数的尺子，在顶上那块表还没出数时（loading/error）是 disabled，
                  不是"能选但整屏排成一片空"。 */}
              <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
                组卡片排序
                <select value={gso.v} aria-label="组卡片按哪一列排序" onChange={e => setGroupSort(e.target.value)}
                  className="rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]">
                  {GROUP_SORTS.map(o => (
                    <option key={o.v} value={o.v} disabled={o.key === "trend" && !trendReady}
                      title={o.key === "trend" && !trendReady
                        ? "这一列的数由页面最上方「分组整体分析」现场聚合而来（约 4 秒）；它还没出数，所以这把尺子暂时不能用" : o.desc}>
                      {o.label}{o.key === "trend" && !trendReady ? "（等上方聚合出数）" : ""}
                    </option>
                  ))}
                </select>
              </label>
              {open !== null && (
                <span className="text-[11px] text-muted-foreground">已筛到一组，点「全部组」回到全部 {String((d.groups ?? []).length)} 组</span>
              )}
              {trend.kind !== "ok" && (
                <button type="button" onClick={refresh}
                  className="inline-flex items-center gap-1 rounded-lg border border-border/60 px-2.5 py-1 text-[12px] text-muted-foreground hover:border-primary hover:text-primary">
                  <RefreshCw className="h-3 w-3" /> 重新聚合组级读数
                </button>
              )}
            </div>
            {trend.kind !== "loading" && (
              <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
                {trend.kind === "ok"
                  ? <>下面每一组多出的是<b className="text-foreground">预测操作分 / 加权分 / 趋势分 / 近 10·20 日日均涨跌</b>，
                      与最上方那张「分组整体分析」是<b className="text-foreground">同一次聚合</b>（不是又发了一遍请求）：
                      窗口 {dash(trend.data.window?.sessions)} 场（{dash(trend.data.window?.from)} → {dash(trend.data.window?.to)}）、
                      聚合耗时 {dash(trend.data.elapsed_ms)} ms；接口聚合了 {dash(trend.data.group_count)} 组，
                      本页列 {(d.groups ?? []).length} 组，<b className="text-foreground">差的 {String((d.groups ?? []).length - (trend.data.group_count ?? 0))} 组是没有成员的空组</b>（不是聚合失败）。
                      数字全部来自接口，本页不重算。</>
                  : `组级趋势没取到，所以上面那张表和卡片上那 6 格现在都没有数：${describeState(trend)}`}
              </p>
            )}
            {/* 一行一个组（用户 10-14：原来一行两个组、每组又占两行，一屏看不了几个组）。
                窄屏也不回两列 —— 组这一行的内容本来就靠 flex-wrap 自己缩。 */}
            <ul className="mt-2 grid gap-1">
              {sortGroups(d.groups ?? [], gso, trendByGroup).map(g => (
                <li key={`${g.group_id}`}>
                  <button type="button" onClick={() => setOpen(open === g.group_id ? null : (g.group_id ?? null))} aria-pressed={open === g.group_id}
                    className={cn("flex w-full flex-wrap items-baseline gap-x-2 rounded-lg border px-2 py-1.5 text-left text-[12px]",
                      open === g.group_id ? "border-primary/70 bg-primary/10" : "border-border hover:bg-muted/50")}>
                    <span className="font-medium">{dash(g.name)}</span>
                    {/* 组名后面直接跟来源标记，不再写「备注：」这个标签（用户 09-28）：
                        库里这批组的 note 全是「同花顺导入」，它是**这一组从哪来**，不是给人读的备注。
                        （组数不写死在这里：10-14 现查 48 组，而文字曾长期写着 45 —— 数字一律当场读。）
                        哪天有组 note 不一样（比如手工建的），这一格就跟着变，不会把话固化成"都来自同花顺"。 */}
                    {!!(g.note ?? "").trim() && (
                      <span className="text-[11px] text-muted-foreground/80">{g.note!.trim()}</span>
                    )}
                    {g.is_focus === true && <span className="rounded bg-primary/15 px-1 text-[10px] text-primary">焦点</span>}
                    <span className="text-muted-foreground">{dash(g.stock_count)} 只</span>
                    <span className={cn("font-mono tabular-nums", pctColor(g.avg_pct ?? null))}>{dash(g.avg_pct)}%</span>
                    <span className="text-[11px] text-muted-foreground">涨 {dash(g.up)} / 跌 {dash(g.down)} / 当日无行情 {dash(g.missing)}</span>
                    <span className="text-[11px] text-muted-foreground">成本均价 {dash(g.avg_cost)}（{dash(g.cost_filled)}/{dash(g.stock_count)} 只填了成本）</span>
                    <span className="text-[10px] text-muted-foreground/60">序 {dash(g.display_order)}</span>
                    {/* 组级趋势/打分：**默认就显示**（这份数由页面最上方那次聚合交出来，不是另发一发）。
                        数字、单位、口径全部用接口交出的，本页不重算，也不给缺值的组补 0
                        （不够场次拟合趋势的组就该是空）。 */}
                    {trend.kind === "ok" && (() => {
                      const r = g.group_id != null ? trendByGroup.get(g.group_id) : undefined;
                      if (!r) {
                        // 接口只聚合**有成员**的组（10-14 实测：库里 48 组、接口交出 44 行，缺的那 4 组 stock_count 都是 0，
                        // 它自己的 caveat 写明"当前 44 个有成员的组"）。空组要说成空组，
                        // 不能说成"成员当日全部无行情" —— 那是给一个没有成员的组编一个数据缺失的理由。
                        const empty = (g.stock_count ?? 0) === 0;
                        return <span className="text-[10px] text-warning/80">
                          {empty ? "本组没有成员，接口不聚合空组" : "这一组没进本次聚合"}
                        </span>;
                      }
                      // 单位一律取接口交出的那一份（chg10_bp 的 unit 是 "bp/日"）：
                      // 自己写死 "%" 会把 −25bp 读成 −25%，量级差 100 倍，正是接口 caliber 里警告过的那件事。
                      const signed = (v?: number | null, key?: string) => (v == null ? "—"
                        : `${v > 0 ? "+" : ""}${num(v, 1)}${key ? ` ${unitOf(key)}` : ""}`);
                      return (
                        <>
                          {/* 后缀只在"单位不在标签里"时才写（趋势=分/10场、涨跌=bp/日 都不自明）；
                              预测分/加权分的单位就是「分」，标签已经说了，再跟一个"分"是重复。 */}
                          <span className="font-mono tabular-nums text-[11px]" title={cal("pred_score")}>预测分 <b className="text-foreground">{num(r.pred_score, 1)}</b></span>
                          <span className="font-mono tabular-nums text-[11px]" title={cal("w_score")}>加权分 <b className="text-foreground">{num(r.w_score, 1)}</b></span>
                          <span className="font-mono tabular-nums text-[11px]" title={cal("trend_score")}>趋势 <b className={pctColor(r.trend_score ?? null)}>{r.trend_score == null ? "—" : `${r.trend_score > 0 ? "+" : ""}${num(r.trend_score, 2)}`}</b><span className="text-muted-foreground/60"> {unitOf("trend_score")}{r.trend_days != null ? ` · 用了 ${r.trend_days} 场` : ""}</span></span>
                          <span className={cn("font-mono tabular-nums text-[11px]", pctColor(r.chg10_bp ?? null))} title={cal("chg10_bp")}>近10日 {signed(r.chg10_bp, "chg10_bp")}</span>
                          <span className={cn("font-mono tabular-nums text-[11px]", pctColor(r.chg20_bp ?? null))} title={cal("chg20_bp")}>近20日 {signed(r.chg20_bp, "chg20_bp")}</span>
                          <span className="text-[10px] text-muted-foreground/70" title={cal("dt_times")}>上榜 {dash(r.dt_times)} 次 · 上涨占比 {dash(r.up_ratio)}%</span>
                        </>
                      );
                    })()}
                  </button>
                </li>
              ))}
            </ul>
          </>}
        </Panel>

        <Panel title={open == null ? "自选明细（全部组）" : "自选明细（所选组）"} state={stocks}
          note={'「日均」与「区间」是两把尺子，现在各占一列并排放着：000636.SZ 这一行 10日日均 -2.81 '
            + '读作「这 10 个交易日平均每天跌 2.81 个点」，旁边那一列 10日区间 -25.46 才是这 10 天合起来的涨跌。'
            + '日均小得多是正负天相互抵消的结果，不是算错。'
            + '操作分是另一回事：0.40×个股质量 + 0.60×环境共振 的合成分，带符号、值域 -100~100，'
            + '只在同一场里比强弱，既不是百分位也不是涨幅。'}>
          {d => <>
            <Row label="行情日" value={dash(d.date)} />
            <Row label="明细条数 / 去重只数" value={`${dash(d.count)} 条 / ${new Set((d.rows ?? []).map(r => r.security_code)).size} 只`} />
            <Row label="排序" value={dash(d.sort)} />
            <CaveatText text={d.caveat} />
            <CaliberFold provenance={[d.source]} />
            {/* 说明与定义折进通用说明框（用户 10-14：这一屏原来把字段义、操作分构成、单位
                整摊摊开在表上方，读数被挤到第二屏）。**caveat 与来源没跟着折** —— 那两句是
                "这一屏哪里不全"，藏在折叠框里等于没写。 */}
            <ExplainBox title="字段义 · 操作分与象限怎么来的 · 单位"
              count={Object.keys(d.indicator_meaning ?? {}).length
                + Object.keys(d.score_meaning ?? {}).length
                + Object.keys(d.unit ?? {}).length}>
              <p>条数大于只数是因为同一只票可以属多个组（选了某一组时两者会接近）。</p>
              {d.indicator_meaning && Object.keys(d.indicator_meaning).length > 0 && (
                <div className="space-y-0.5">
                  <p>字段义（后端原样给出，本页不重算也不改写）：</p>
                  {Object.entries(d.indicator_meaning).map(([k, v]) => (
                    <p key={k} className="pl-2">· <b className="font-mono text-foreground/80">{k}</b> ＝ {plain(v)}</p>
                  ))}
                </div>
              )}
              {/* 操作分的构成只住在后端那一份 `score_meaning` 里（权重出处是 config.binding）。
                  原来这一列的表头提示把这一格说成"百分位量"—— 那是错的：这一格交的是
                  `analytics.score.value`，带符号的合成分原值（当日实测 -77.8~+16.2），
                  同表的 `percent_rank` 才是百分位、这一屏没取。 */}
              {d.score_meaning && Object.keys(d.score_meaning).length > 0 && (
                <div className="space-y-0.5">
                  <p>操作分与象限怎么来的（权重改在 `config.binding`，这几行跟着那份数说话）：</p>
                  {Object.entries(d.score_meaning).map(([k, v]) => (
                    <p key={k} className="pl-2">· <b className="text-foreground/80">{k}</b> ＝ {plain(v)}</p>
                  ))}
                  <p className="pl-2">· <b className="text-foreground/80">负分</b> ＝ 正常的日常读数：环境(Y)占 0.60 且经常整片为负，
                    所以"分是负的"不等于"这只票要跌"，它是<strong>同一场里比强弱</strong>的尺子，不是涨幅。</p>
                </div>
              )}
              {d.unit && Object.keys(d.unit).length > 0 && (
                <p>单位（后端原样给出）：{Object.entries(d.unit).map(([k, v]) => `${k}:${v}`).join(" · ")}</p>
              )}
            </ExplainBox>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead><tr className="text-left text-muted-foreground">
                  {/* 换排序时取消选中：选中是按**行号**记的，行序一变那个号就指向另一只票，
                      高亮的行与下面的档案会说的不是同一只 —— 与其错着，不如收掉。 */}
                  <SortHeader label="代码" sortKey="security_code" dir={rowSort.dirOf("security_code")} onSort={pickSort} />
                  <SortHeader label="名称" sortKey="name" dir={rowSort.dirOf("name")} onSort={pickSort} />
                  {open == null && <SortHeader label="组" sortKey="group_name" dir={rowSort.dirOf("group_name")} onSort={pickSort} />}
                  <SortHeader label="收盘" sortKey="close" dir={rowSort.dirOf("close")} onSort={pickSort} />
                  <SortHeader label="成本" sortKey="cost" dir={rowSort.dirOf("cost")} onSort={pickSort}
                    title="加入自选时登记的成本价；没填就是空、不填 0 —— 空值恒排最后" />
                  {COLUMNS.map(([k, label, kind]) => <SortHeader key={k} label={label} sortKey={String(k)} dir={rowSort.dirOf(String(k))} onSort={pickSort}
                    title={kind === "日均" ? "日均：每天平均动几个点（正负天会抵消），不是这段一共涨跌多少 —— 合计数在旁边的「区间」那一列"
                      : kind === "区间" ? "区间合计：这 N 个交易日合起来的涨跌（未复权收盘价首尾相比）。窗口里有除权日会跳出一个大负数，先去核对除权"
                      : kind === "合成分" ? "带符号的合成分原值（0.40×个股质量 + 0.60×环境共振，值域 -100~100），不是百分位、不是涨幅"
                      : kind === "预测" ? "20 日日均振幅 + 该序列斜率 × 10.5 的外推一步：预测的是振幅（动多大），不是涨跌方向；不足 20 根给空"
                      : undefined} />)}
                  <SortHeader label="象限" sortKey="quadrant" dir={rowSort.dirOf("quadrant")} onSort={pickSort}
                    title="当日象限；没算出象限的行恒排最后" />
                  <th className="font-normal">备注</th>
                </tr></thead>
                <tbody>
                  {shownRows.map((r, i) => (
                    <tr key={`${r.security_code}-${r.group_id}-${i}`}
                      className={cn("border-t border-border/40", cell.getRowClass(i, ""))}
                      onDoubleClick={() => openReview(r.security_code)}
                      title="双击这一行：另开一个浏览器窗口看这一只的「个股深度复盘」">
                      <Td i={i} k="security_code" cell={cell}>
                        <StockLink code={r.security_code} className="font-mono hover:text-primary">{dash(r.security_code)}</StockLink>
                      </Td>
                      <Td i={i} k="name" cell={cell}><StockLink code={r.security_code} name={r.name} className="hover:text-primary" /></Td>
                      {open == null && <Td i={i} k="group_name" cell={cell} muted>{dash(r.group_name)}</Td>}
                      <Td i={i} k="close" cell={cell} mono>{dash(r.close)}</Td>
                      <Td i={i} k="cost" cell={cell} mono title="点这一格也算选中这一只票">{dash(r.cost)}</Td>
                      {COLUMNS.map(([k]) => {
                        const v = r[k] as number | null | undefined;
                        return (
                          <Td key={k} i={i} k={String(k)} cell={cell} mono
                            tone={SIGNED.has(k as string) ? pctColor(v ?? null) : undefined}>
                            {dash(v)}
                          </Td>
                        );
                      })}
                      <Td i={i} k="quadrant" cell={cell} mono>{dash(r.quadrant)}</Td>
                      <Td i={i} k="note" cell={cell} muted>{dash(r.note)}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {sortedRows.length > 150 && (
                <p className="mt-1 text-[10px] text-muted-foreground">先按所选列排序（未选列时用后端榜序）、再截前 150 行；交出 {sortedRows.length} 行。</p>
              )}
              <ExplainBox title="这一张表怎么点 · 双击去哪里">
                <p>
                  <SelectedHint store={cell.store} rowAt={(row) => shownRows[row] ?? null} />
                </p>
                <p>
                  <b className="text-foreground">双击任意一行</b> = 另开一个浏览器窗口打开「个股深度复盘」，
                  地址里带着这一只的代码。那一屏住着量化档案、趋势量化与窗口趋势、个股研究（公开资料）与多空辩论
                  （用户 2026-10-15：这几叠个股功能点原来散在本页与两个独立屏，现在合成一屏；
                  本页只留明细这一张表，好把两边并排放着对照）。
                  增删本机名单与持仓仍在 <Link className="text-primary underline" to="/my-stocks">重点关注（持仓）</Link>。
                </p>
              </ExplainBox>
              {/* 弹窗被拦这件事是**告警**，不折进上面那个默认收起的说明框（收着就等于没说）。 */}
              {popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{popHint}</p>}
            </div>
          </>}
        </Panel>

        {/* 2026-10-15 用户裁定：本页那一叠「要分析哪一只 / 这只票的量化档案 / 该支票的趋势量化计算 /
            窗口趋势」全部搬进「个股深度复盘」（`pages/StockReview.tsx`），进入方式改成**双击明细的一行**
            （带代码开新窗口）。原来那个手输选股的框跟着搬走 —— 同一屏两处「选一只票」必然分叉，
            而新页顶部那一个 `StockSearch` 才是这一件事的唯一实现。
            本页因此不再写 `ys.ui_pref` 的 `grouped.dossier_code`（那个键仍在后端登记着，
            库里最近一次的选择也还留着，只是再也没有写入方；要不要撤登记由用户定）。
            下面这块清单级的「自选股历史新高」不跟着走：它的母体是整个自选清单，不是一只票。 */}
        <GlassCard>
          <h2 className="flex items-center gap-1.5 text-sm font-bold">
            <Microscope className="h-4 w-4 text-primary" /> 要看某一只票的档案与趋势：双击上面明细的那一行
          </h2>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            新窗口里是「个股深度复盘」：量化档案、趋势量化与窗口趋势、个股研究（公开资料）、多空辩论集中在同一屏，
            代码由地址带过去（<code>symbol</code>），这一叠明细留在原处可以并排对着看。
            想换一只就在新窗口里重新选，或者回来双击另一行 —— 两个窗口各自独立，不会互相顶掉。
          </p>
        </GlassCard>

        {/* 「自选股历史新高」原来挂在趋势评分那一屏：它的母体是**整个自选清单**（可限定一个分组），
            不跟着上面选中的那只票走，所以搬进来后仍带它自己的范围选择器（用户 2026-10-14 选定搬进本页）。 */}
        <NewHighBoard />

        <GlassCard>
          {/* 标题由嵌入组件自己出（`Watchlist` 在 embedded 下渲染 level=2「自选股」，与
              「我的股票」页那一块一致）—— 这里不再叠一个 h2，同一件事两个标题会被读成两叠数据。
              09-28 用户裁定：这叠原本独占一页 `/watchlist`，并进本页，页面与导航条目一并删除。 */}
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            <code>lib/watchlist.ts</code> 存在键 <code>vr-watchlist</code> 的是一份扁平代码数组（上限 100 只、无分组、无成本），
            与上面库里那 45 组是两套数据，只存在这台浏览器里、不上传。
            当前有 <b className="text-foreground">{local.length}</b> 只；列在一起是为了看得清"哪只只在本地、库里没有"。
          </p>
          {localCodes && localCodes.total > 0 && (
            <p className={cn("mt-1 text-[11px] leading-relaxed", localCodes.missing.length ? "text-warning" : "text-muted-foreground")}>
              {localCodes.missing.length === 0
                ? "本机每一只都能在库里的明细中找到 —— 没有只在本机存在的孤票。"
                : <>本机有 {localCodes.missing.length} 只不在库里的明细中：<span className="font-mono">{localCodes.missing.join(" ")}</span></>}
            </p>
          )}
          {local.length === 0 && (
            <p className="mt-1 text-[11px] text-muted-foreground">这台浏览器里还没有本机名单（<code>vr-watchlist</code> 为空）—— 下面这叠是空的，不是同步坏了。</p>
          )}
          {/* 名单本体走嵌入组件：批量添加、刷新价格、逐只移除都在它里面，不在这里重写一份。 */}
          <div className="mt-3 border-t border-border/50 pt-3"><Watchlist embedded /></div>
          <p className="mt-2 text-[11px] leading-relaxed text-warning">
            真正的"一次性迁移导入"需要一个写入口（把这批代码落进某个组，并定清楚：导进哪个组、重复代码怎么去重、原来的 localStorage 留不留、
            全量覆盖前先给现有分组留整份副本 —— 那是用户手改的唯一副本）。目前 <code>/api/watchlist/groups</code> 只读，
            所以本页不做假导入，也不改库里的数据。
          </p>
        </GlassCard>
      </div>

      <Disclaimer compact />
    </div>
  );
}
