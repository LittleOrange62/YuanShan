import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { CaliberFold, Panel, Row } from "@/components/ui/DataPanel";
import { DrawerSection } from "@/components/ui/DocDrawer";
import { NoticeBar } from "@/components/ui/NoticeBar";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { useNonce, useProbe } from "@/lib/probe";
import { dash } from "@/lib/datastate";
import { plain, type NoteSection } from "@/lib/agent";
import { pct } from "@/lib/qd";
import { pctColor } from "@/lib/colors";
import { cn } from "@/lib/utils";

/**
 * 历史回溯第 2 块 ·「近期倍量表走势分析」（`GET /api/quant/energy-trend`）。
 * 用户 2026-11-08 推翻上一版逐票热力表后定的形状：名次每 10 名一档，看各档在名次日之后
 * 1/2/3/5/10 场的累计涨跌怎么走，按名次日那天的加权分配权，并与同窗全市场基准并排。
 *
 * 判据、列名、说明分节全部只住后端 `duanxian/energy_trend.py`（`COLUMNS` / `GUIDE` /
 * `WEIGHT_RULE` 那几份），这一块只做三件事：把描述符映射到当前所选尺子的真实字段、
 * 把三类声明留在屏上、把每一格的分母念出来。
 *
 * 两处最容易写错、各有钉：
 *  · `sortKey` 必须等于那一格真读的字段名 —— 所以行先摊平成 `h1/h2/h3/h5/h10`，
 *    列头与格子读同一个键（描述符里的 `excess`/`step` 不是真实字段，真字段带尺子后缀）。
 *  · 缺数三态不许合并：`pending`（还没走到）、`missing`（走到了读不到）、算得出的 `n_ok`
 *    各念各的；格子是 — 时不是 0%，加权分母为 0 时后端交 null，界面也不退回等权。
 */

const PANEL_TITLE = "近期倍量表走势分析";

interface TrendCell {
  k?: number | null;
  n_all?: number | null;
  n_ok?: number | null;
  n_pending?: number | null;
  n_missing?: number | null;
  n_w?: number | null;
  weight_sum?: number | null;
  weighted?: number | null;
  equal?: number | null;
  win_pct?: number | null;
  excess_weighted?: number | null;
  excess_equal?: number | null;
  step_weighted?: number | null;
  step_equal?: number | null;
}
interface TrendBucket {
  index?: number | null;
  label?: string | null;
  rank_lo?: number | null;
  rank_hi?: number | null;
  events?: number | null;
  codes?: number | null;
  cells?: TrendCell[] | null;
}
interface TrendPayload {
  available?: boolean | null;
  reason?: string | null;
  date?: string | null;
  top_n?: number | null;
  window?: number | null;
  window_used?: number | null;
  window_from?: string | null;
  window_to?: string | null;
  fingerprint?: string | null;
  buckets?: TrendBucket[] | null;
  baseline?: {
    label?: string | null; events?: number | null; codes?: number | null;
    stock_days?: number | null; no_base?: number | null; cells?: TrendCell[] | null;
  } | null;
  dropped_events?: number | null;
  coverage?: { lo?: string | null; hi?: string | null; sessions?: number | null } | null;
  horizons?: number[] | null;
  bucket_size?: number | null;
  top_n_default?: number | null;
  window_default?: number | null;
  window_min?: number | null;
  window_max?: number | null;
  top_n_min?: number | null;
  top_n_max?: number | null;
  rank?: { rule?: string | null; rule_short?: string | null; by?: string | null } | null;
  energy?: { scale?: unknown; fingerprint?: string | null } | null;
  weight_rule?: string | null;
  baseline_rule?: string | null;
  sample_rule?: string | null;
  pct_rule?: string | null;
  guide?: NoteSection[] | null;
  columns?: { key?: string | null; label?: string | null; note?: string | null }[] | null;
  caveats?: { code?: string | null; label?: string | null; text?: string | null }[] | null;
  generated_at?: string | null;
  cached?: boolean | null;
  cache_age_seconds?: number | null;
  elapsed_ms?: number | null;
  source?: string | null;
}

/** 两把尺子：加权（按名次日那天的倍量分配权）与等权。真字段名带后缀，描述符里的 key 不带。 */
type Scale = "weighted" | "equal";
/** 四种读数：累计涨跌幅 / 超额 / 每场增量 / 上涨占比。 */
type View = "cum" | "excess" | "step" | "win";

/** 当前尺子＋读数 → 格子里真读的那个字段（列头 sortKey 用的也是它摊平后的键）。 */
function cellField(scale: Scale, view: View): keyof TrendCell {
  if (view === "cum") return scale;
  if (view === "excess") return `excess_${scale}` as keyof TrendCell;
  if (view === "step") return `step_${scale}` as keyof TrendCell;
  return "win_pct";
}
/** 当前读数 → 后端列描述符的 key（中文列名与口径只从那份描述符取）。 */
function descKey(scale: Scale, view: View): string {
  return view === "cum" ? scale : view === "win" ? "win_pct" : view;
}
/** 摊平后的行：`h{k}` 就是那一格真读的数，列头 sortKey 与它同名。 */
interface FlatRow {
  index: number;
  label: string;
  events: number | null;
  codes: number | null;
  cells: TrendCell[];
  [key: string]: unknown;
}

const LINE_COLORS = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2", "#be185d", "#65a30d"];

export function EnergyTrendBoard() {
  const [date, setDate] = useState("");
  /** null＝不给参数、吃后端默认（出厂值只住后端一处，界面不写死 120）。 */
  const [windowN, setWindowN] = useState<number | null>(null);
  const [topN, setTopN] = useState<number | null>(null);
  const [scale, setScale] = useState<Scale>("weighted");
  const [view, setView] = useState<View>("cum");
  /** null＝默认画前四档；点档位标签之后按点过的那份画。 */
  const [picked, setPicked] = useState<number[] | null>(null);
  const [nonce, bump] = useNonce();
  const probe = useProbe<TrendPayload>(
    `/quant/energy-trend?date=${encodeURIComponent(date)}`
    + (windowN !== null ? `&window=${encodeURIComponent(String(windowN))}` : "")
    + (topN !== null ? `&top_n=${encodeURIComponent(String(topN))}` : "")
    // 按过「重取这一块」就必须把 refresh 发出去：不带它，那颗按钮会亮着却交回同一小时缓存。
    + (nonce > 0 ? "&refresh=1" : ""),
    nonce);
  const sort = useTableSort();
  const payload = probe.kind === "ok" ? probe.data : null;
  const field = cellField(scale, view);

  return (
    <Panel title={PANEL_TITLE} state={probe}
      slowHint="回看窗口里每一场各排一次名次、取前 N 名，再把每一笔往后 1/2/3/5/10 场的后复权涨跌算出来，全市场基准同一趟一起算：实测（2026-11-08，近 120 场 × 前 120 名）冷取 15.9 秒，之后一小时内同一组参数约 10 毫秒；按「重取这一块」连那层缓存一起绕过去。"
      controls={<div className="flex flex-wrap items-center gap-2 text-[11px]">
        <label className="flex items-center gap-1 text-muted-foreground"
          title="基准日＝窗口右端贴到哪一场。留空＝倍量表里最新的一场。窗口右端贴着它，所以越往右（后 10 场）越多笔样本还没走到。">
          基准日：<input type="date" value={date}
            onChange={e => { setDate(e.target.value); setPicked(null); }}
            className="rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]" />
        </label>
        <label className="flex items-center gap-1 text-muted-foreground"
          title="回看几场：窗口里每一场各取一次前 N 名，所以场数越多每档的样本笔数越多。清空＝吃接口回显的 window_default；上下夹读接口的 window_min/window_max，界面不写死。">
          回看场数：
          <input type="number" min={payload?.window_min ?? undefined} max={payload?.window_max ?? undefined} step={10}
            value={windowN ?? payload?.window_default ?? ""}
            onChange={e => {
              const raw = e.target.value.trim();
              setPicked(null);
              if (raw === "") { setWindowN(null); return; }
              const n = Number(raw);
              if (!Number.isFinite(n)) return;
              const lo = payload?.window_min ?? 1;
              const hi = payload?.window_max ?? n;
              setWindowN(Math.min(hi, Math.max(lo, Math.round(n))));
            }}
            className="w-20 rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]" />
        </label>
        <label className="flex items-center gap-1 text-muted-foreground"
          title="每一场取加权分前 N 名。N 是上限、不是恰好 N 只：边界那一名并列几只就一起进来，所以最后一档的笔数可能比前面各档少一截或多几笔。清空＝吃接口回显的 top_n_default。">
          名次上限：
          <input type="number" min={payload?.top_n_min ?? undefined} max={payload?.top_n_max ?? undefined} step={10}
            value={topN ?? payload?.top_n_default ?? ""}
            onChange={e => {
              const raw = e.target.value.trim();
              setPicked(null);
              if (raw === "") { setTopN(null); return; }
              const n = Number(raw);
              if (!Number.isFinite(n)) return;
              const lo = payload?.top_n_min ?? 1;
              const hi = payload?.top_n_max ?? n;
              setTopN(Math.min(hi, Math.max(lo, Math.round(n))));
            }}
            className="w-20 rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]" />
        </label>
        <span className="ml-1 text-muted-foreground">尺子</span>
        {(["weighted", "equal"] as Scale[]).map(s => (
          <button key={s} type="button" aria-pressed={view !== "win" && scale === s}
            disabled={view === "win"}
            onClick={() => setScale(s)}
            title={view === "win"
              ? "上涨占比只有等权那一份口径（后端按算得出的样本数上涨的笔数），这一档不起作用；要看加权请先切回累计涨跌幅、超额或每场增量"
              : plain(descOf(payload, s)?.note ?? "")}
            className={cn("rounded-lg border px-2 py-0.5 disabled:opacity-40", scale === s && view !== "win"
              ? "border-primary/70 bg-primary/10 font-medium text-primary" : "border-border hover:bg-muted/50")}>
            {labelOf(payload, s)}
          </button>
        ))}
        <span className="ml-1 text-muted-foreground">读数</span>
        {(["cum", "excess", "step", "win"] as View[]).map(v => (
          <button key={v} type="button" aria-pressed={view === v}
            onClick={() => setView(v)}
            title={plain(descOf(payload, descKey(scale, v))?.note ?? "")}
            className={cn("rounded-lg border px-2 py-0.5", view === v
              ? "border-primary/70 bg-primary/10 font-medium text-primary" : "border-border hover:bg-muted/50")}>
            {labelOf(payload, descKey(scale, v))}
          </button>
        ))}
        <button type="button" onClick={() => { setPicked(null); bump(); }}
          className="ml-1 flex items-center gap-1 rounded-lg border border-border/60 px-2 py-0.5 text-muted-foreground hover:border-primary hover:text-primary">
          <RefreshCw className="h-3 w-3" />重取这一块
        </button>
      </div>}>
      {d => {
        const horizons = (d.horizons ?? []).filter(k => typeof k === "number" && Number.isFinite(k));
        const buckets = d.buckets ?? [];
        const base = d.baseline ?? null;
        const rows: FlatRow[] = buckets.map(b => flatRow(String(b.label ?? ""), Number(b.index ?? 0),
          b.events ?? null, b.codes ?? null, b.cells ?? [], horizons, field));
        const baseRow = base
          ? flatRow(String(base.label ?? ""), -1, base.events ?? null, base.codes ?? null,
            base.cells ?? [], horizons, field)
          : null;
        const sorted = sort.apply(rows);
        const nullCells = rows.reduce((a, r) => a + horizons.filter(k => r[`h${k}`] == null).length, 0);
        const pending = buckets.reduce((a, b) => a + (b.cells ?? []).reduce((s, c) => s + (c.n_pending ?? 0), 0), 0);
        const missing = buckets.reduce((a, b) => a + (b.cells ?? []).reduce((s, c) => s + (c.n_missing ?? 0), 0), 0);
        const visible = picked ?? buckets.slice(0, 4).map(b => Number(b.index ?? 0));
        const series = buckets.filter(b => visible.includes(Number(b.index ?? 0))).map((b, i) => ({
          key: String(b.index ?? i), label: String(b.label ?? ""), color: LINE_COLORS[i % LINE_COLORS.length],
          values: horizons.map(k => num((b.cells ?? []).find(c => c.k === k)?.[field])),
        }));
        const baseSeries = baseRow
          ? [{ key: "base", label: String(base?.label ?? ""), color: "#64748b",
            values: horizons.map(k => num(baseRow[`h${k}`])) }]
          : [];
        const unit = view === "win" ? "%" : view === "cum" ? "%" : view === "excess" ? "个百分点" : "百分点/场";

        return (
          <>
            <Row label="基准日（窗口右端）" value={dash(d.date)} />
            <Row label="回看窗口"
              value={`${dash(d.window_from)} ~ ${dash(d.window_to)}：要了近 ${dash(d.window)} 场，实际用了 ${dash(d.window_used)} 场`} />
            <Row label="每一场取前几名"
              value={`前 ${dash(d.top_n)} 名（上限，边界同分并列一起进来）· 每 ${dash(d.bucket_size)} 个名次一档 · 共 ${rows.length} 档`} />
            <Row label="这一批的档表指纹" value={dash(d.fingerprint)} />
            <Row label="这份数什么时候算的"
              value={d.generated_at
                ? `${d.generated_at}${d.cached ? `（读的一小时缓存，已存 ${dash(d.cache_age_seconds)} 秒）` : "（这一趟现算）"}`
                : "后端没交生成时刻"} />

            {/* 三类声明留在屏上：缺数计数与「— 不是 0」这件事折起来就等于让屏幕说假话。
                后端 caveats 里带当场计数的那几条（窗口不够长/右端还没走到/有笔读不到/边界并列）
                由 CaliberFold 按白名单制自动留屏，这里不重复一遍，只补每一格分母的读法。 */}
            <NoticeBar kind="missing" when={nullCells > 0 || pending > 0 || missing > 0}>
              这张表有 {nullCells} 格是 —（不是 0%）：其中还没走到那一天 {pending} 笔·格、
              走到了却读不到 {missing} 笔·格。每一格下面那个 n 就是它真正进了分子分母的笔数与总笔数，
              两把尺子各读各的分母；加权分母为 0 时那一格也是 —，不会悄悄换成等权。
            </NoticeBar>

            <CaliberFold caveats={d.caveats}
              coverage={{ volume_double: { from: d.coverage?.lo ?? null, to: d.coverage?.hi ?? null, sessions: d.coverage?.sessions ?? null } }}
              provenance={[d.source, d.fingerprint]}
              drawerTitle={`${PANEL_TITLE} · 这块怎么读`}
              notes={[plain(d.rank?.rule ?? "")]}
              extra={(
                <>
                  {(d.guide ?? []).map(s => (
                    <DrawerSection key={s.heading} heading={s.heading}>{plain(s.body)}</DrawerSection>
                  ))}
                  <DrawerSection heading="每一列的名字与口径（后端描述符原句）">
                    {(d.columns ?? []).map(c => (
                      <p key={String(c.key)} className="mt-1">
                        <b className="text-foreground">{String(c.label ?? "")}</b>：{plain(c.note ?? "")}
                      </p>
                    ))}
                  </DrawerSection>
                  <DrawerSection heading="这张表怎么点">
                    <p>点列头按那一列排（缺数恒排最后，两个方向都是）；不点列头就是后端给的档位顺序（名次从前往后）。
                      尺子与读数那两组按钮只换看哪一份数，不重新取数；改基准日、回看场数、名次上限才会重取。
                      基准那一行钉在最后不参与排序：它只有等权那一份，选加权时它整行是 —。</p>
                  </DrawerSection>
                </>
              )} />

            <p className="mt-2 text-[11px] text-muted-foreground">
              现在读的是<b className="text-foreground">{labelOf(d, descKey(scale, view))}</b>
              （单位：{unit}），横着是一档往后越远、竖着是同一时点不同名次档。
            </p>
            <div className="mt-1 overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-left text-muted-foreground">
                    {/* 名次档是这张表的横轴本身（后端已按名次从前往后给），不做成可排序列：
                        它是「1-10」这样的区间文本，按字符串排会把 101-110 排到 11-20 前面。
                        要按表现排就点右边那几列（那些键都是格子里真读的数）。 */}
                    <th className="whitespace-nowrap px-2 py-1 font-medium"
                      title={plain(descOf(d, "label")?.note ?? "")}>{labelOf(d, "label")}</th>
                    <SortHeader label={labelOf(d, "events")} sortKey="events" dir={sort.dirOf("events")}
                      onSort={() => sort.toggle("events")} title={plain(descOf(d, "events")?.note ?? "")} />
                    <SortHeader label={labelOf(d, "codes")} sortKey="codes" dir={sort.dirOf("codes")}
                      onSort={() => sort.toggle("codes")} title={plain(descOf(d, "codes")?.note ?? "")} />
                    {horizons.map(k => (
                      <SortHeader key={k} label={`后 ${k} 场`} sortKey={`h${k}`} dir={sort.dirOf(`h${k}`)}
                        onSort={() => sort.toggle(`h${k}`)}
                        title={`名次日之后第 ${k} 个市场场次的${labelOf(d, descKey(scale, view))}（单位：${unit}）。${plain(descOf(d, descKey(scale, view))?.note ?? "")}`} />
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sorted.map(r => (
                    <tr key={r.index} className="border-t border-border/30">
                      <td className="whitespace-nowrap px-2 py-1.5 font-medium">{r.label}</td>
                      <td className="px-2 py-1.5 font-mono tabular-nums">{dash(r.events)}</td>
                      <td className="px-2 py-1.5 font-mono tabular-nums">{dash(r.codes)}</td>
                      {horizons.map(k => {
                        const c = r.cells.find(x => x.k === k) ?? {};
                        return <Cell key={k} c={c} v={num(r[`h${k}`])} view={view} scale={scale} />;
                      })}
                    </tr>
                  ))}
                  {baseRow && (
                    <tr className="border-t border-border/60 bg-muted/20">
                      <td className="whitespace-nowrap px-2 py-1.5 font-medium">{baseRow.label}</td>
                      <td className="px-2 py-1.5 font-mono tabular-nums">{dash(baseRow.events)}</td>
                      <td className="px-2 py-1.5 font-mono tabular-nums">{dash(baseRow.codes)}</td>
                      {horizons.map(k => {
                        const c = baseRow.cells.find(x => x.k === k) ?? {};
                        return <Cell key={k} c={c} v={num(baseRow[`h${k}`])} view={view} scale={scale} isBase />;
                      })}
                    </tr>
                  )}
                </tbody>
              </table>
              {!rows.length && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  接口交出 0 档：这一段窗口里没有可排名的加权分，原因看上面那句原话，不是接口坏了。
                </p>
              )}
            </div>

            {!!series.length && (
              <div className="mt-3">
                <p className="text-[11px] text-muted-foreground">
                  趋势对照（{labelOf(d, descKey(scale, view))}，单位：{unit}）：默认画前四档与基准，点下面的档位标签加减线。
                  算不出的那一段线断开，不跨过去连成一条看着连续的曲线。
                </p>
                <div className="mt-1 flex flex-wrap items-center gap-1">
                  {buckets.map((b, i) => {
                    const idx = Number(b.index ?? i);
                    const on = visible.includes(idx);
                    return (
                      <button key={idx} type="button" aria-pressed={on}
                        onClick={() => {
                          const cur = picked ?? buckets.slice(0, 4).map(x => Number(x.index ?? 0));
                          setPicked(cur.includes(idx) ? cur.filter(x => x !== idx) : [...cur, idx]);
                        }}
                        className={cn("rounded-lg border px-2 py-0.5 text-[10px]", on
                          ? "border-primary/70 bg-primary/10 font-medium text-primary" : "border-border text-muted-foreground hover:bg-muted/50")}>
                        {String(b.label ?? "")}
                      </button>
                    );
                  })}
                </div>
                <TrendChart horizons={horizons} series={[...series, ...baseSeries]} unit={unit} />
              </div>
            )}
          </>
        );
      }}
    </Panel>
  );
}

function descOf(d: TrendPayload | null, key: string) {
  return (d?.columns ?? []).find(c => c.key === key);
}
/** 列名只从后端描述符取；没交出来就老实说没交，不自己编一个中文名。 */
function labelOf(d: TrendPayload | null, key: string): string {
  return descOf(d, key)?.label ?? "列名后端没交";
}
function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
function flatRow(label: string, index: number, events: number | null, codes: number | null,
  cells: TrendCell[], horizons: number[], field: keyof TrendCell): FlatRow {
  const row: FlatRow = { index, label, events, codes, cells };
  for (const k of horizons) row[`h${k}`] = num(cells.find(c => c.k === k)?.[field]);
  return row;
}
function fmt(v: number | null, view: View): string {
  if (v === null) return "—";
  if (view === "win") return `${v.toFixed(2)}%`;
  if (view === "cum") return pct(v, 2);
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}`;
}

/** 一格：读数＋它自己的分母。三种「没有数」在悬停里分开念，不合并成一个 —。 */
function Cell({ c, v, view, scale, isBase }: {
  c: TrendCell; v: number | null; view: View; scale: Scale; isBase?: boolean;
}) {
  const why = v !== null ? ""
    : isBase && scale === "weighted" && view !== "win"
      ? "基准只有等权那一份（全市场没有「按倍量分配权」这件事）：选加权时这一行是 —，不是 0"
      : (c.n_pending ?? 0) !== 0 && (c.n_ok ?? 0) === 0
        ? "这一格的样本都还没走到那一天（窗口右端贴着基准日）：不是 0%，是那几天还没发生"
        : (c.n_missing ?? 0) !== 0 && (c.n_ok ?? 0) === 0
          ? "走到了那一天却算不出涨跌：名次日缺基期，或第 k 场它没有行（停牌/未上市）。不是 0%"
          : scale === "weighted" && view !== "win" && (c.n_w ?? 0) === 0
            ? "加权分母为 0（这一格没有一笔既算得出涨跌又读得到加权分）：交 —，不悄悄退回等权"
            : "这一格没有读数：后端交的是空，界面不拿 0 顶";
  return (
    <td className={cn("px-2 py-1.5 text-right font-mono tabular-nums", pctColor(v))}
      title={`${why ? `${why}｜` : ""}分母 n_ok=${dash(c.n_ok)} / 总笔数 n_all=${dash(c.n_all)}`
        + `｜还没走到 ${dash(c.n_pending)} 笔、读不到 ${dash(c.n_missing)} 笔`
        + (scale === "weighted" ? `｜进加权分母的 ${dash(c.n_w)} 笔、权重合计 ${dash(c.weight_sum)}` : "")}>
      {v === null ? <span className="text-muted-foreground/50">—</span> : fmt(v, view)}
      <span className="ml-1 text-[10px] font-normal text-muted-foreground/70">
        n={dash(c.n_ok)}/{dash(c.n_all)}
      </span>
    </td>
  );
}

/** 趋势折线：x＝往后第几场，y＝当前读数。缺值断线（不桥接），基准画虚线。 */
function TrendChart({ horizons, series, unit }: {
  horizons: number[];
  series: { key: string; label: string; color: string; values: (number | null)[] }[];
  unit: string;
}) {
  const vals = series.flatMap(s => s.values).filter((v): v is number => v !== null);
  if (!horizons.length || !vals.length) {
    return <p className="mt-1 text-[11px] text-muted-foreground">这一份读数一格都算不出，画不了线（不是平盘）。</p>;
  }
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  const span = hi - lo || 1;
  const W = 640;
  const H = 200;
  const x = (i: number) => 44 + (horizons.length === 1 ? 0 : i * (W - 60) / (horizons.length - 1));
  const y = (v: number) => 12 + (hi - v) * (H - 40) / span;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="mt-1 w-full" role="img"
      aria-label={`各名次档往后各场的走势对照，单位${unit}`}>
      <line x1={40} x2={W - 12} y1={y(0)} y2={y(0)} stroke="currentColor" strokeOpacity={0.25} strokeDasharray="3 3" />
      <text x={36} y={y(0) + 3} textAnchor="end" fontSize={9} fill="currentColor" fillOpacity={0.6}>0</text>
      <text x={36} y={y(hi) + 3} textAnchor="end" fontSize={9} fill="currentColor" fillOpacity={0.6}>{hi.toFixed(1)}</text>
      <text x={36} y={y(lo) + 3} textAnchor="end" fontSize={9} fill="currentColor" fillOpacity={0.6}>{lo.toFixed(1)}</text>
      {horizons.map((k, i) => (
        <text key={k} x={x(i)} y={H - 8} textAnchor="middle" fontSize={9} fill="currentColor" fillOpacity={0.6}>后{k}场</text>
      ))}
      {series.map(s => {
        // 缺值处分段：把连续的有数段各自连成一条 path，绝不跨过 — 连一条看着完整的线。
        const paths: string[] = [];
        let cur: string[] = [];
        s.values.forEach((v, i) => {
          if (v === null) { if (cur.length > 1) paths.push(cur.join(" ")); cur = []; return; }
          cur.push(`${cur.length ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`);
        });
        if (cur.length > 1) paths.push(cur.join(" "));
        return (
          <g key={s.key}>
            {paths.map((p, i) => (
              <path key={i} d={p} fill="none" stroke={s.color} strokeWidth={s.key === "base" ? 1.5 : 2}
                strokeDasharray={s.key === "base" ? "5 4" : undefined} />
            ))}
            {s.values.map((v, i) => v === null ? null : (
              <circle key={i} cx={x(i)} cy={y(v)} r={2.4} fill={s.color}>
                <title>{`${s.label}｜后 ${horizons[i]} 场：${v.toFixed(2)}（${unit}）`}</title>
              </circle>
            ))}
          </g>
        );
      })}
      <g>
        {series.map((s, i) => (
          <g key={s.key} transform={`translate(${44 + i * 92}, 4)`}>
            <line x1={0} x2={12} y1={4} y2={4} stroke={s.color} strokeWidth={2}
              strokeDasharray={s.key === "base" ? "5 4" : undefined} />
            <text x={16} y={7} fontSize={9} fill="currentColor" fillOpacity={0.75}>{s.label}</text>
          </g>
        ))}
      </g>
    </svg>
  );
}