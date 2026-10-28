import { RefreshCw } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { CaliberFold, ExplainBox, Panel, Row } from "@/components/ui/DataPanel";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { PageHeader } from "@/components/ui/PageHeader";
import { SortHeader } from "@/components/ui/SortHeader";
import { StockLink } from "@/components/ui/StockLink";
import { plain } from "@/lib/agent";
import { dash } from "@/lib/datastate";
import { useNonce, useProbe } from "@/lib/probe";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { useTableSort } from "@/lib/useSort";
import { cn } from "@/lib/utils";

/**
 * 异动筛查（用户 2026-10-13 点名的新功能，第一块是「最近两个月盘中瞬间冲高 / 冲到涨停之后怎么样」）。
 *
 * 三条不许破的：
 *  · 判据一块都不在这儿重算 —— 母体、阈值、标签、口径原文全读后端那一份
 *    （`app/compute/intraday_spike.py`）；
 *  · 默认行序就是用户要的**时间顺序**（日内高点出现的时刻）。所以 `useTableSort()` 不给初始排序，
 *    后端交来的那份就是它；点了列头才接管，点列头同时取消选中（选中的是行号，行序一变就不是同一只）；
 *  · 每个统计档与**同期基线**并排读，颜色只表示"高于/低于同一窗口的基线"。
 *    09-22~10-12 那 9 场基线是次日上涨 37.9%，08-14~10-13 是 46.0% —— 同一个"35%"在两个窗口里
 *    意思相反，所以只交档内数字不交基线就是骗人。
 */

interface Ev {
  code?: string | null; name?: string | null; board?: string | null;
  peak_time?: string | null; first5_time?: string | null; peak_min_no?: number | null;
  hi_pct?: number | null; cl_pct?: number | null; open_pct?: number | null;
  fade_pct?: number | null; upper_shadow_pct?: number | null; board_pct?: number | null;
  touched?: boolean | null; sealed?: boolean | null;
  touch_min?: number | null; sealed_min?: number | null; board_runs?: number | null;
  first_seal_time?: string | null; speed15_pct?: number | null; above5_min?: number | null;
  after5_dd_pct?: number | null; above_vwap_pct?: number | null; tail_above_vwap?: number | null;
  vol_above5_share?: number | null; fade_vol_ratio?: number | null;
  gain20_pct?: number | null; is_hi60_break?: boolean | null;
  bars_used?: number | null; minute_available?: boolean | null;
  tags?: string[] | null; tag_labels?: string[] | null; missing?: string | null;
  next_pct?: number | null; next3_pct?: number | null; next5_pct?: number | null;
  has_next?: boolean | null;
}
interface Bucket {
  grp?: string | null; n?: number | null; avg_next?: number | null; med_next?: number | null;
  win_next?: number | null; p10_next?: number | null; p90_next?: number | null;
  avg_next3?: number | null; avg_next5?: number | null; win_next5?: number | null;
  avg_sealed_min?: number | null;
}
interface Review {
  available?: boolean | null; reason?: string | null;
  window?: { from?: string | null; to?: string | null; sessions?: number | null } | null;
  events?: number | null; minute_missing?: number | null;
  baseline?: Record<string, number | null> | null; buckets?: Bucket[] | null;
}
interface Data {
  available?: boolean | null; reason?: string | null; date?: string | null;
  rows?: Ev[] | null; total?: number | null; minute_missing?: number | null;
  sealed?: number | null; blown?: number | null; limit?: number | null;
  rejected_tags?: string[] | null; as_of?: string | null; spec_version?: string | null;
  thresholds?: Record<string, number | string> | null;
  tag_labels?: Record<string, string> | null;
  calibers?: Record<string, string> | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
  coverage?: Record<string, { from?: string | null; to?: string | null; sessions?: number | null }> | null;
  review?: Review | null;
  calendar?: { date?: string | null; events?: number | null;
               minute_missing?: number | null; computed?: boolean | null }[] | null;
}

const pct = (v: number | null | undefined, dp = 2) =>
  typeof v === "number" ? `${v >= 0 ? "+" : ""}${v.toFixed(dp)}%` : dash(null);
const num = (v: number | null | undefined, suffix = "") =>
  typeof v === "number" ? `${v}${suffix}` : dash(null);
const tone = (v: number | null | undefined) =>
  typeof v === "number" ? (v > 0 ? "up" : v < 0 ? "down" : "") : "";

/** 表里放不下的读数进整行悬停；口径原文一律来自后端 calibers，这里不重写一遍。 */
function hoverOf(r: Ev, cal: Record<string, string>) {
  return [
    `板块：${dash(r.board)}｜涨停档位 ${num(r.board_pct, "%")}`,
    `开盘 ${pct(r.open_pct)}｜收盘 ${pct(r.cl_pct)}｜最高 ${pct(r.hi_pct)}`,
    `第一次站上 +5% 的时刻：${dash(r.first5_time)}｜峰值是当天第 ${dash(r.peak_min_no)} 分钟`,
    `触板分钟 ${dash(r.touch_min)}｜首次封住 ${dash(r.first_seal_time)}`,
    `上影占振幅 ${num(r.upper_shadow_pct, "%")}｜收盘距高点 ${pct(r.fade_pct)}`,
    `全天在均价线上方 ${num(r.above_vwap_pct, "%")}｜尾盘 30 分 ${num(r.tail_above_vwap, "%")}`,
    `冲高段量占比 ${num(r.vol_above5_share, "%")}｜峰值后÷峰值前每分钟量 ${num(r.fade_vol_ratio, "×")}`,
    `前 20 场累计 ${pct(r.gain20_pct, 1)}｜创 60 日新高：${r.is_hi60_break === null || r.is_hi60_break === undefined ? "—" : (r.is_hi60_break ? "是" : "否")}`,
    `当天分钟线 ${dash(r.bars_used)} 根${r.minute_available ? "" : "（没入库 ⇒ 分钟级判据没生效）"}`,
    r.missing ? `缺的读数：${r.missing}` : "",
    `后 3 日 ${pct(r.next3_pct, 1)}｜后 5 日 ${pct(r.next5_pct, 1)}`,
    cal.next_pct ?? "",
  ].filter(Boolean).join("\n");
}

/** 复盘统计表：基线钉在第一行，下面是各档（同一把尺子内互比，跨尺子不横比）。 */
function ReviewTable({ rv, cal }: { rv: Review; cal: Record<string, string> }) {
  const b = rv.baseline ?? {};
  const buckets = rv.buckets ?? [];
  const groups = useMemo(() => {
    const seen: string[] = [];
    for (const x of buckets) {
      const g = String(x.grp ?? "").split(" ")[0];
      if (!seen.includes(g)) seen.push(g);
    }
    return seen;
  }, [buckets]);
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full text-[11px]">
        <thead><tr className="text-left text-muted-foreground">
          <th className="whitespace-nowrap">档</th>
          <th className="whitespace-nowrap text-right">n</th>
          <th className="whitespace-nowrap text-right">次日均值</th>
          <th className="whitespace-nowrap text-right">次日中位</th>
          <th className="whitespace-nowrap text-right">次日上涨</th>
          <th className="whitespace-nowrap text-right">次日 p10</th>
          <th className="whitespace-nowrap text-right">次日 p90</th>
          <th className="whitespace-nowrap text-right">后 3 日均值</th>
          <th className="whitespace-nowrap text-right">后 5 日均值</th>
          <th className="whitespace-nowrap text-right">后 5 日上涨</th>
          <th className="whitespace-nowrap text-right">平均封住分钟</th>
        </tr></thead>
        <tbody>
          <tr className="border-t border-border bg-muted/40 font-semibold">
            <td className="whitespace-nowrap">同期基线（全部票·日）</td>
            <td className="text-right font-mono tabular-nums">{(b.n ?? 0).toLocaleString()}</td>
            <td className="text-right font-mono tabular-nums">{pct(b.avg_next)}</td>
            <td className="text-right font-mono tabular-nums">{pct(b.med_next)}</td>
            <td className="text-right font-mono tabular-nums">{num(b.win_next, "%")}</td>
            <td className="text-right font-mono tabular-nums">{pct(b.p10_next)}</td>
            <td className="text-right font-mono tabular-nums">{pct(b.p90_next)}</td>
            <td className="text-right font-mono tabular-nums">—</td>
            <td className="text-right font-mono tabular-nums">{pct(b.avg_next5, 1)}</td>
            <td className="text-right font-mono tabular-nums">{num(b.win_next5, "%")}</td>
            <td className="text-right font-mono tabular-nums">—</td>
          </tr>
          {buckets.map((x, i) => {
            const g = String(x.grp ?? "").split(" ")[0];
            const firstOfGroup = i > 0 && g !== String(buckets[i - 1].grp ?? "").split(" ")[0];
            return (
              <tr key={`${x.grp}-${i}`}
                className={cn("border-t border-border/40", firstOfGroup && "border-t-border/90")}>
                <td className="whitespace-nowrap" title={`${g} 这一把尺子（与同期基线并排读）`}>{x.grp}</td>
                <td className="text-right font-mono tabular-nums">{(x.n ?? 0).toLocaleString()}</td>
                <td className="text-right font-mono tabular-nums">{pct(x.avg_next)}</td>
                <td className="text-right font-mono tabular-nums">{pct(x.med_next)}</td>
                <td className={cn("text-right font-mono tabular-nums font-semibold",
                  typeof x.win_next === "number" && typeof b.win_next === "number"
                    ? (x.win_next > b.win_next ? "text-emerald-600 dark:text-emerald-400"
                      : x.win_next < b.win_next ? "text-destructive" : "") : "")}>
                  {num(x.win_next, "%")}
                </td>
                <td className="text-right font-mono tabular-nums">{pct(x.p10_next)}</td>
                <td className="text-right font-mono tabular-nums">{pct(x.p90_next)}</td>
                <td className="text-right font-mono tabular-nums">{pct(x.avg_next3, 1)}</td>
                <td className="text-right font-mono tabular-nums">{pct(x.avg_next5, 1)}</td>
                <td className="text-right font-mono tabular-nums">{num(x.win_next5, "%")}</td>
                <td className="text-right font-mono tabular-nums">{num(x.avg_sealed_min)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {buckets.length === 0 && (
        <p className="mt-1 text-[11px] text-warning">
          这一窗口一条统计都没数出来 —— 通常是窗口内的事件还没回算，
          或右端正好是最新一场（没有「次日」可比）。看下面那份日历：0 条是还没算，不是没有冲高。
        </p>
      )}
      {/* 两句读法收进默认收起的说明框（用户 10-14 点名）；上面那条 text-warning 没跟着折。 */}
      <ExplainBox title="这张统计表怎么读 · 与基线并排 · 只与同一把尺子比">
        <p>{plain(cal.baseline ?? "")}</p>
        {groups.length > 0 && (
          <p>
            尺子有这几把：{groups.join("、")}。每组的第一行是母体档（幅度），其余各组是同一批事件按不同维度切开的看法，
            所以同一档只与同一把尺子的其他档比 —— 不同尺子之间的 n 是重叠的，横着比会重复计数。
          </p>
        )}
      </ExplainBox>
    </div>
  );
}

/** 一格：点它 = 整行与整列一起加亮（与「选股汇总」「分组自选」同一个 hook，手感不许分叉）。 */
function Cell({ i, k, cell, mono, strong, tn, children }: {
  i: number; k: string; cell: ReturnType<typeof useStockRowInteraction>;
  mono?: boolean; strong?: boolean; tn?: string; children?: ReactNode;
}) {
  return (
    <td className={cell.getCellClass(i, k, cn("whitespace-nowrap px-0.5 py-0.5",
      mono && "font-mono tabular-nums", strong && "font-semibold",
      tn === "up" && "text-emerald-600 dark:text-emerald-400",
      tn === "down" && "text-destructive"))}
      onClick={(e) => cell.handleCellClick(e.currentTarget, i, k)}>
      {children}
    </td>
  );
}

export function Spike() {
  const [nonce, refresh] = useNonce();
  const [date, setDate] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [minHi, setMinHi] = useState(0);
  const [sessions, setSessions] = useState(43);
  const rowSort = useTableSort();
  // 单击交叉高亮 / 双击开深度复盘：全站一份实现（`lib/useStockRowInteraction.ts`）。
  const cell = useStockRowInteraction("异动筛查");
  const sortBy = (k: string) => { cell.clearAll(); rowSort.toggle(k); };

  const q = new URLSearchParams();
  if (date) q.set("date", date);
  if (tags.length) q.set("tags", tags.join(","));
  if (minHi > 0) q.set("min_hi", String(minHi));
  q.set("days", String(sessions));
  const hits = useProbe<Data>(`/quant/intraday-spike?${q.toString()}`, nonce);

  const cur = hits.kind === "ok" ? hits.data : null;
  const cal = cur?.calibers ?? {};
  const th = cur?.thresholds ?? {};
  const tagLabels = cur?.tag_labels ?? {};
  const calendar = cur?.calendar ?? [];
  const filtered = tags.length > 0 || minHi > 0;

  const toggleTag = (k: string) => {
    cell.clearAll();
    setTags(prev => (prev.includes(k) ? prev.filter(x => x !== k) : [...prev, k]));
  };

  const controls = (
    <>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
        <label className="flex items-center gap-1 text-muted-foreground">
          交易日（只列这张表有数的日子）：
          <select value={date} aria-label="交易日" disabled={hits.kind === "loading"}
            onChange={e => { setDate(e.target.value); cell.clearAll(); }}
            className="rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]">
            <option value="">最新一场</option>
            {calendar.map(c => (
              <option key={String(c.date)} value={String(c.date)}>
                {c.date}（{(c.events ?? 0).toLocaleString()} 条{c.computed ? "" : "｜还没算"}）
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1 text-muted-foreground">
          复盘窗口：
          <select value={sessions} aria-label="复盘窗口场次" disabled={hits.kind === "loading"}
            onChange={e => setSessions(Number(e.target.value))}
            className="rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]">
            {[10, 21, 43, 63, 120].map(n => <option key={n} value={n}>{n} 场</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1 text-muted-foreground">
          最高较前收不低于
          <input type="number" min={0} max={30} step={0.5} value={minHi} aria-label="最低冲高幅度"
            disabled={hits.kind === "loading"}
            onChange={e => setMinHi(Math.max(0, Math.min(30, Number(e.target.value) || 0)))}
            className="w-14 rounded border border-border/60 bg-background px-1 py-0.5 text-[11px] font-mono" />
          个点
        </label>
        <button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1">
        <span className="text-[11px] text-muted-foreground">标签（库里筛，发生在取数上限之前）：</span>
        {Object.entries(tagLabels).map(([k, label]) => (
          <button key={k} type="button" onClick={() => toggleTag(k)} aria-pressed={tags.includes(k)}
            className={cn("rounded-full border px-1.5 py-0.5 text-[10px]",
              tags.includes(k)
                ? "border-primary/60 bg-primary/15 font-medium text-primary"
                : "border-border/60 bg-background text-muted-foreground hover:text-foreground")}>
            {label}
          </button>
        ))}
        {(tags.length > 0 || minHi > 0) && (
          <button type="button" className="text-[10px] text-muted-foreground hover:text-foreground"
            onClick={() => { setTags([]); setMinHi(0); cell.clearAll(); }}>
            回到全部
          </button>
        )}
      </div>
      <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
        {plain(cal.mother ?? "")}
        {(cur?.rejected_tags ?? []).length > 0
          ? `｜这些标签没被认出来、没有参与筛选：${cur?.rejected_tags?.join("、")}` : ""}
      </p>
    </>
  );

  return (
    <div className="mx-auto max-w-[1720px]">
      <PageHeader title="异动筛查" level={1}
        subtitle="盘中「一瞬间冲上去」或「摸到涨停」的票，逐事件列出它什么时候冲的、冲得有多快、之后守没守住，以及这类形状接下来几天的真实分布。" />

      <Panel title="复盘：冲高之后怎么样（与同期基线并排）" state={hits} controls={controls}
        note="一行 = 一次事件，不是「一票一天」：同一只票连着两天冲高就是两条。母体与阈值都在后端一份，这里一块都不重算。">
        {d => {
          const review = d.review ?? null;
          return (
            <>
              <Row label="复盘窗口"
                value={review?.available
                  ? `${dash(review.window?.from)} ~ ${dash(review.window?.to)}｜窗口内 ${dash(review.window?.sessions)} 场有数、事件 ${dash(review.events)} 条`
                  : "没取到"} />
              {review && !review.available && (
                <p className="mt-1 text-[11px] text-warning">
                  {plain(review.reason ?? "复盘统计既没交出 available:true，也没交出原因")}
                </p>
              )}
              {review?.available && <ReviewTable rv={review} cal={d.calibers ?? {}} />}
              {/* 用户 2026-10-21：描述性内容收进一处折叠；缺数/截断类由 CaliberFold 分流留在屏上 */}
              <CaliberFold caveats={d.caveats} definitions={d.calibers} coverage={d.coverage}
                provenance={[d.spec_version ? `判据版本 ${d.spec_version}` : null,
                  d.as_of ? `数出自 ${String(d.as_of)}` : null]} />
            </>
          );
        }}
      </Panel>

      <Panel title="事件名单（默认按日内高点出现的时刻排）" state={hits}
        note={`默认排序就是点名要的时间顺序。${plain(cal.peak_time ?? "")}`}>
        {d => {
          const all = d.rows ?? [];
          const list = rowSort.apply(all);
          const lim = d.limit;
          return (
            <>
              <Row label="基准交易日" value={dash(d.date)} />
              <Row label="这一场异动"
                value={`${(d.total ?? 0).toLocaleString()} 条｜本页交出 ${all.length} 行`
                  + `｜收盘封住涨停 ${dash(d.sealed)} 条、触板没封住 ${dash(d.blown)} 条`} />
              {(d.minute_missing ?? 0) > 0 && (
                <Row label="分钟线缺失" tone="warn"
                  value={`${d.minute_missing} 条没有分钟级读数（当天 1 分钟线没入库）：速度、时刻、承接、封住时长都是空的，`
                    + '母体里「15 分钟内 >=4 个点」那一刀对它们没生效，按水平档收了进来。不是「这些票没冲高」'} />
              )}
              {all.length === 0 && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {filtered
                    ? "选了这些条件之后是空的 —— 是筛空的，不是库里没数（点「回到全部」再看）。"
                    : "这一场一条异动都没有：多半是加工链还没跑到这一天（看上面「基准交易日」与上面那份日历里这一天的条数）。"}
                </p>
              )}
              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead><tr className="text-left text-muted-foreground">
                    <SortHeader label="高点时刻" sortKey="peak_time" dir={rowSort.dirOf("peak_time")} onSort={sortBy}
                      title={plain(cal.peak_time ?? "")} />
                    <SortHeader label="代码" sortKey="code" dir={rowSort.dirOf("code")} onSort={sortBy} />
                    <SortHeader label="名称" sortKey="name" dir={rowSort.dirOf("name")} onSort={sortBy} />
                    <SortHeader label="最高较前收" sortKey="hi_pct" dir={rowSort.dirOf("hi_pct")} onSort={sortBy} />
                    <SortHeader label="收盘" sortKey="cl_pct" dir={rowSort.dirOf("cl_pct")} onSort={sortBy} />
                    <SortHeader label="15 分钟拉升" sortKey="speed15_pct" dir={rowSort.dirOf("speed15_pct")} onSort={sortBy}
                      title={`任意 ${th.speed_window_min ?? 15} 根连续分钟线里的最大拉升幅度；母体要求 >= ${th.speed_min_pct ?? 4} 个点。实测拉得越急次日越差`} />
                    <SortHeader label="站住分钟" sortKey="above5_min" dir={rowSort.dirOf("above5_min")} onSort={sortBy}
                      title="日内最高保持在 +5% 上方的分钟根数" />
                    <SortHeader label="峰值后 5 分" sortKey="after5_dd_pct" dir={rowSort.dirOf("after5_dd_pct")} onSort={sortBy}
                      title="峰值之后 5 根里最低收盘相对日高的回撤。复盘里区分度最强的一刀：守住（高于 -1 个点）的次日明显好于没守住的" />
                    <SortHeader label="封住分钟" sortKey="sealed_min" dir={rowSort.dirOf("sealed_min")} onSort={sortBy}
                      title={plain(cal.sealed ?? "")} />
                    <SortHeader label="开板段数" sortKey="board_runs" dir={rowSort.dirOf("board_runs")} onSort={sortBy}
                      title=">=2 就是开了又回封。实测全程没开板的次日上涨 52.2%，回封只有 39.4%" />
                    <SortHeader label="标签" sortKey="tag_labels" dir={rowSort.dirOf("tag_labels")} onSort={sortBy} />
                    <SortHeader label="次日" sortKey="next_pct" dir={rowSort.dirOf("next_pct")} onSort={sortBy}
                      title={plain(cal.next_pct ?? "")} />
                  </tr></thead>
                  <tbody>
                    {list.map((r, i) => (
                      <tr key={`${r.code}-${i}`} {...cell.rowProps(i, r.code, undefined, hoverOf(r, cal))}>
                        <Cell i={i} k="peak_time" cell={cell} mono>{dash(r.peak_time)}</Cell>
                        <Cell i={i} k="code" cell={cell} mono>
                          <StockLink code={r.code} name={null} className="hover:text-primary" />
                        </Cell>
                        <Cell i={i} k="name" cell={cell}>
                          <StockLink code={r.code} name={r.name} className="hover:text-primary" />
                        </Cell>
                        <Cell i={i} k="hi_pct" cell={cell} mono strong>{pct(r.hi_pct)}</Cell>
                        <Cell i={i} k="cl_pct" cell={cell} mono tn={tone(r.cl_pct)}>{pct(r.cl_pct)}</Cell>
                        <Cell i={i} k="speed15_pct" cell={cell} mono>{pct(r.speed15_pct)}</Cell>
                        <Cell i={i} k="above5_min" cell={cell} mono>{dash(r.above5_min)}</Cell>
                        <Cell i={i} k="after5_dd_pct" cell={cell} mono
                          tn={typeof r.after5_dd_pct === "number" ? (r.after5_dd_pct > -1 ? "up" : "down") : ""}>
                          {pct(r.after5_dd_pct)}
                        </Cell>
                        <Cell i={i} k="sealed_min" cell={cell} mono>{dash(r.sealed_min)}</Cell>
                        <Cell i={i} k="board_runs" cell={cell} mono>{dash(r.board_runs)}</Cell>
                        <Cell i={i} k="tag_labels" cell={cell}>
                          <span className="flex flex-wrap gap-0.5">
                            {(r.tag_labels ?? []).map(l => (
                              <span key={l} className="rounded bg-muted px-1 text-[10px] text-muted-foreground">{l}</span>
                            ))}
                          </span>
                        </Cell>
                        <Cell i={i} k="next_pct" cell={cell} mono tn={tone(r.next_pct)}>
                          {r.has_next ? pct(r.next_pct) : <span className="text-muted-foreground">明天还没走完</span>}
                        </Cell>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {typeof lim === "number" && all.length >= lim && (
                  <p className="mt-1 text-[10px] text-warning">
                    已达取数上限 {lim} 行：上面「这一场异动」那个数才是这一天的全部条数。
                  </p>
                )}
                <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{STOCK_ROW_HINT}</p>
                {cell.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{cell.popHint}</p>}
              </div>
            </>
          );
        }}
      </Panel>
      <Disclaimer compact />
    </div>
  );
}
