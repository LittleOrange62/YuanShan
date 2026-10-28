import { RefreshCw } from "lucide-react";
import { useState, type ReactNode } from "react";
import { CaliberFold, Definitions, ExplainBox, Panel, Row } from "@/components/ui/DataPanel";
import { SortHeader } from "@/components/ui/SortHeader";
import { StockLink } from "@/components/ui/StockLink";
import { plain } from "@/lib/agent";
import { dash } from "@/lib/datastate";
import { useNonce, useProbe } from "@/lib/probe";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { useTableSort } from "@/lib/useSort";
import { cn } from "@/lib/utils";

/**
 * 交易体系榜（用户 2026-10-14 口述的三条件组合，摆在「选股汇总」下面）。
 *
 * 三条同时成立才算，所以这一屏天生是窄的（库里 242 场实测：观察池平均 49.6 只/场、三条全过平均 5.7 只/场）——
 * **窄是"组合"的意思，不是坏了**，所以空榜的措辞要分得清"没确认"与"链没跑到这一天"。
 *
 * 判据一块都不在这儿重算：三条的阈值、止损位、口径原文全读后端那一份
 * （`app/compute/system_entry.py`）。历史分布也必须与同期基线并排：
 * 这套筛法实测**右尾更肥、期望更差**，只端一句就是骗人。
 */

interface Entry {
  code?: string | null; name?: string | null; board?: string | null;
  close?: number | null; pct_change?: number | null; close_hfq?: number | null;
  c1_ok?: boolean | null; limit_up_count20?: number | null;
  last_limit_up_date?: string | null; limit_up_ago?: number | null;
  c2_ok?: boolean | null; gap_date?: string | null; gap_ago?: number | null;
  gap_pct?: number | null; gap_floor_hfq?: number | null; gap_unfilled?: number | null;
  gap_vol_shares?: number | null;
  c3_ok?: boolean | null; pullback_sessions?: number | null; pullback_down_cnt?: number | null;
  pullback_max_vol_ratio?: number | null; pullback_min_low_hfq?: number | null;
  confirm_yang?: boolean | null; vol_vs_prev?: number | null;
  entry_ok?: boolean | null; watch_ok?: boolean | null;
  stop_ref_hfq?: number | null; stop_dist_pct?: number | null;
  bars_used?: number | null; missing?: string | null; calculated_at?: string | null;
}
interface ColSpec {
  key?: string | null; label?: string | null; note?: string | null;
  signed?: boolean | null; versus_base?: boolean | null; bold?: boolean | null;
  denom_key?: string | null;
}
interface Bucket {
  [k: string]: unknown;
  bucket_key?: string | null; bucket?: string | null;
  n?: number | null; win_next?: number | null; avg_next?: number | null;
  up10_med?: number | null; up10_p90?: number | null; p20_up20?: number | null;
  d20_med?: number | null; hit_take?: number | null; hit_stop?: number | null;
  rule_expect?: number | null; missing?: string | null; bars_scanned?: number | null;
}
interface Data {
  available?: boolean | null; reason?: string | null; date?: string | null;
  rows?: Entry[] | null; total?: number | null; entries?: number | null; watching?: number | null;
  only_entry?: boolean | null; limit?: number | null; as_of?: string | null;
  spec_version?: string | null;
  thresholds?: Record<string, number> | null;
  calibers?: Record<string, string> | null;
  caliber_items?: { key?: string | null; title?: string | null; text?: string | null }[] | null;
  caveats?: { code?: string | null; label?: string | null; text?: string | null }[] | null;
  calendar?: { date?: string | null; rows?: number | null; entries?: number | null }[] | null;
  history?: { available?: boolean | null; reason?: string | null;
              window?: { from?: string | null; to?: string | null } | null;
              columns?: ColSpec[] | null; uncomputed_columns?: string[] | null;
              buckets?: Bucket[] | null } | null;
}

const numOf = (b: Bucket, k: string) => (typeof b[k] === "number" ? (b[k] as number) : null);
const intOf = (b: Bucket, k: string) => (typeof b[k] === "number" ? Math.trunc(b[k] as number) : null);

/** 下面这三个格式化件与 `Cell` 是这一屏与「近 6 个月名单」那一屏共用的一份（10-22）。
 *  `Cell` 的 `title` 是后者要加的：每格悬停里写"这一列是哪个口径"，不改高亮/点击那条链。 */
export const n2 = (v?: number | null, suffix = "") =>
  typeof v === "number" ? `${v}${suffix}` : dash(null);
export const pc = (v?: number | null, dp = 2) =>
  typeof v === "number" ? `${v >= 0 ? "+" : ""}${v.toFixed(dp)}%` : dash(null);
export const tone = (v?: number | null) =>
  typeof v === "number" ? (v > 0 ? "up" : v < 0 ? "down" : "") : "";

function hoverOf(r: Entry, cal: Record<string, string>) {
  return [
    `板块 ${dash(r.board)}｜后复权收盘 ${r.close_hfq?.toFixed(4) ?? dash(null)}`,
    `① 近 20 场里封住涨停 ${dash(r.limit_up_count20)} 天，最近一根 ${dash(r.last_limit_up_date)}`
      + `（距今 ${dash(r.limit_up_ago)} 场）`,
    `② 缺口 ${dash(r.gap_date)}（${dash(r.gap_ago)} 场前），幅度 ${n2(r.gap_pct, " 个点")}`
      + `｜下沿（后复权）${r.gap_floor_hfq?.toFixed(4) ?? dash(null)}，已连续 ${dash(r.gap_unfilled)} 场没回补`,
    `③ 回调段 ${dash(r.pullback_sessions)} 场、其中阴线 ${dash(r.pullback_down_cnt)} 根、`
      + `段内最大单日量 ÷ 缺口日量 = ${n2(r.pullback_max_vol_ratio, "×")}（判据要 <= 1）`,
    `今日 ${r.confirm_yang ? "收阳并站上昨收" : "没确认"}｜今日量 ÷ 昨日量 ${n2(r.vol_vs_prev, "×")}`,
    `止损参考位（缺口下沿）${r.stop_ref_hfq?.toFixed(4) ?? dash(null)}｜距现价 ${pc(r.stop_dist_pct)}`,
    `往前真有的日线根数 ${dash(r.bars_used)}${(r.bars_used ?? 0) < 20 ? "（不足 20 场，① 判不了）" : ""}`,
    r.missing ? `缺的读数：${r.missing}` : "",
    cal.stop ?? "",
  ].filter(Boolean).join("\n");
}

/**
 * 历史分布：三档并排（基线 / ①② / ①②③全过）。
 *
 * **列头文字、哪一列与基线比色、哪一列带正号、每一列是什么意思，全部读后端那一份**
 * （`app/compute/system_entry.py::STATS_COLUMNS_SPEC` → 接口 `history.columns`）。
 * 原先中文列名手抄在组件里、说明写在 `calibers` 里，两处各说各话；用户 2026-10-22 点名
 * 「后面那几列分别是什么意思，做个说明，说明默认收缩」，就把列头与说明合成一份并接到界面上。
 * 数来自 `analytics.system_entry_stats`（每日加工段算好落库）—— 现算要一分半，会被只读超时咬死。
 */
function HistoryTable({ hs, cal }: { hs: NonNullable<Data["history"]>; cal: Record<string, string> }) {
  const rows = hs.buckets ?? [];
  const cols = (hs.columns ?? []).filter(c => c && c.key);
  const base = rows.find(x => x.bucket_key === "baseline");
  const notes: Record<string, string> = {};
  for (const c of cols) if (c.key && c.label && c.note) notes[c.label] = c.note;
  // 列头上标"最短那一档的分母"（各档里最小的 N）：越长的窗口分母越小，这一眼就要看得到，
  // 不然"后5日均值"会被当成与"次日均值"同一批样本算出来的数。
  const denomMin = (c: ColSpec) => {
    if (!c.denom_key) return null;
    const vs = rows.map(x => intOf(x, c.denom_key as string)).filter((v): v is number => v !== null);
    return vs.length ? Math.min(...vs) : null;
  };
  const cellTitle = (c: ColSpec, x: Bucket) => {
    const parts = [c.note ?? ""];
    if (c.denom_key) {
      const d = intOf(x, c.denom_key);
      parts.push(d === null
        ? "这一档这一列没有分母（没数就是没数，不补 0）"
        : `这一档的分母＝${d.toLocaleString()} 个票·日（未来 ${c.denom_key.replace(/\D/g, "")} 根都在的那些）`);
    }
    return parts.filter(Boolean).join("｜");
  };
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full text-[11px]">
        <thead><tr className="text-left text-muted-foreground">
          {cols.map(c => {
            const d = denomMin(c);
            return (
              <th key={String(c.key)} className="whitespace-nowrap px-1.5 py-1 font-normal"
                title={plain(c.note ?? "")}>
                {c.label}
                {d !== null && <span className="ml-0.5 font-mono text-[9px] text-muted-foreground/70"
                  title="这一列的分母（各档里最小的那个；越长的窗口凑齐的样本越少）">N≥{d.toLocaleString()}</span>}
              </th>
            );
          })}
        </tr></thead>
        <tbody>
          {rows.map((x, i) => {
            const isBase = x.bucket_key === "baseline";
            return (
              <tr key={`${x.bucket_key}-${i}`} className={cn("border-t border-border/40",
                isBase && "bg-muted/40 font-semibold")}>
                {cols.map(c => {
                  const k = String(c.key);
                  if (k === "bucket") {
                    return <td key={k} className="whitespace-nowrap px-1.5 py-1"
                      title={plain(c.note ?? "")}>{x.bucket}</td>;
                  }
                  if (k === "n") {
                    return <td key={k} className="whitespace-nowrap px-1.5 py-1 font-mono tabular-nums"
                      title={plain(c.note ?? "")}>{(intOf(x, "n") ?? 0).toLocaleString()}</td>;
                  }
                  const v = numOf(x, k), b = base ? numOf(base, k) : null;
                  // 与基线比色只在"后端点名的那几列"上做，且相等就不着色：
                  // 同数说成"更高"就是把持平念成优势
                  const vs = !isBase && c.versus_base && v !== null && b !== null
                    ? (v > b ? "text-emerald-600 dark:text-emerald-400"
                      : v < b ? "text-destructive" : "") : "";
                  const cls = cn("px-1.5 py-1 font-mono tabular-nums",
                    c.bold && "font-semibold", vs);
                  return <td key={k} className={cls} title={cellTitle(c, x)}>
                    {c.signed ? pc(v) : n2(v, "%")}
                  </td>;
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      {/* 列头后面紧跟这句：哪几列在这一窗还没落过数（旧批次）。这条不折 —— 折了就会把
          "整列是 —"读成"持有 N 天一个都没凑齐"。 */}
      {(hs.uncomputed_columns ?? []).length > 0 && (
        <p className="mt-1 text-[11px] leading-relaxed text-warning">
          这几列在这一窗还没数：{hs.uncomputed_columns?.join("、")} ——
          那是这几列落库前算的旧批次，整列念 —；补一发在下面的说明里。这不是"持有那几天一根都没凑齐"。
        </p>
      )}
      {/* 逐列说明 + 两段口径原文（后端那份）收进一个默认收起的说明框（用户 2026-10-22：
          「分别是什么意思，做个说明，说明默认收缩」）；下面那条 text-warning（三档没数齐、
          上面那句没数）与列头上的 N≥ 都没跟着折。 */}
      <ExplainBox title={`这三档与每一列怎么读（后端原口径 · ${Object.keys(notes).length} 列）`}>
        <Definitions map={notes} bare />
        <p className="mt-1">{plain(cal.history ?? "")}</p>
        <p>{plain(cal.path ?? "")}</p>
      </ExplainBox>
      {rows.length < 3 && (
        <p className="mt-1 text-[11px] text-warning">
          这三档没数齐：那张历史分布的聚合表还没算到这个窗口（每日加工段 sysentry 那一步的末尾才算它）。
          这时候上面的期望对比不成立，别照着下结论 —— 补一发在功能点文档里。
        </p>
      )}
    </div>
  );
}

export function Cell({ i, k, cell, mono, strong, tn, title, children }: {
  i: number; k: string; cell: ReturnType<typeof useStockRowInteraction>;
  mono?: boolean; strong?: boolean; tn?: string; title?: string; children?: ReactNode;
}) {
  return (
    <td className={cell.getCellClass(i, k, cn("whitespace-nowrap px-1.5 py-1",
      mono && "font-mono tabular-nums", strong && "font-semibold",
      tn === "up" && "text-emerald-600 dark:text-emerald-400",
      tn === "down" && "text-destructive"))}
      onClick={(e) => cell.handleCellClick(e.currentTarget, i, k)}
      {...(title === undefined ? {} : { title })}>
      {children}
    </td>
  );
}

export function SystemEntryBoard() {
  const [nonce, refresh] = useNonce();
  const [date, setDate] = useState("");
  const [onlyEntry, setOnlyEntry] = useState(true);
  const [minGap, setMinGap] = useState(0);
  const rowSort = useTableSort();
  // 单击交叉高亮 / 双击开深度复盘：全站一份实现（`lib/useStockRowInteraction.ts`）。
  const cell = useStockRowInteraction("交易体系榜");
  const sortBy = (k: string) => { cell.clearAll(); rowSort.toggle(k); };

  const q = new URLSearchParams();
  if (date) q.set("date", date);
  q.set("only_entry", onlyEntry ? "true" : "false");
  if (minGap > 0) q.set("min_gap", String(minGap));
  const hits = useProbe<Data>(`/quant/system-entry?${q.toString()}`, nonce);

  const cur = hits.kind === "ok" ? hits.data : null;
  const cal = cur?.calibers ?? {};
  const th = cur?.thresholds ?? {};
  const calendar = cur?.calendar ?? [];
  const hs = cur?.history ?? null;

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
                {c.date}（全过 {c.entries ?? 0} 只／在榜 {c.rows ?? 0} 只）
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1 text-muted-foreground">
          <input type="checkbox" checked={onlyEntry} aria-label="只列三条全过"
            disabled={hits.kind === "loading"}
            onChange={e => { setOnlyEntry(e.target.checked); cell.clearAll(); }} />
          只列三条全过的（不勾就含观察池：缺口成立但没走完回调/确认）
        </label>
        <label className="flex items-center gap-1 text-muted-foreground">
          缺口幅度不低于
          <input type="number" min={0} max={30} step={0.5} value={minGap} aria-label="最小缺口幅度"
            disabled={hits.kind === "loading"}
            onChange={e => setMinGap(Math.max(0, Math.min(30, Number(e.target.value) || 0)))}
            className="w-14 rounded border border-border/60 bg-background px-1 py-0.5 text-[11px] font-mono" />
          个点
        </label>
        <button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>
      <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{plain(cal.mother ?? "")}</p>
    </>
  );

  return (
    <Panel title="交易体系榜（三条同时成立才算）" state={hits} controls={controls}
      note="用户 2026-10-14 口述的一套打法：有涨停 = 有主力在玩；向上跳空三天不回补 = 真金白银抢筹、顺便把你的成本抬高；缩量回调后一根阳线 = 货出不去、这才是上车点。三条是组合，不是任一单形态 —— 所以这一屏本来就是窄的。">
      {d => {
        const all = d.rows ?? [];
        const list = rowSort.apply(all);
        // 折叠块里那份字典念「中文标题」（`caliber_items`），不念 `mother`／`no_fish_pond` 这种机器键
        // （10-22「看不懂」的头号原因，见《页面展示工作指南/01》§十三之三）；
        // 三句有自己家的不重复摊：`mother` 在 controls 那行、`history`/`path` 在历史分布的说明框里、
        // `no_fish_pond` 在表尾那一格专门的说明框里。
        const foldDefs = Object.fromEntries((d.caliber_items ?? [])
          .filter(c => c.title && !["mother", "history", "path", "no_fish_pond"].includes(String(c.key)))
          .map(c => [String(c.title), String(c.text ?? "")]));
        return (
          <>
            <Row label="基准交易日" value={dash(d.date)} />
            <Row label="这一场"
              value={`三条全过 ${dash(d.entries)} 只｜观察池（缺口成立、没走完） ${dash(d.watching)} 只`
                + `｜本页交出 ${all.length} 行`} />
            <Row label="母体口径" tone="warn"
              value={`缺口日到今日限 ${th.gap_ago_min}~${th.gap_ago_max} 个场次（${th.gap_unfilled_days} 场确认不回补 + 1~3 场歇口气）；`
                + `回调段看往前 ${th.pullback_window} 场；跳空幅度门槛 >= ${th.gap_min_pct} 个点。价格一律后复权`} />
            {hs && !hs.available && (
              <p className="mt-1 text-[11px] text-warning">
                {plain(hs.reason ?? "历史分布既没交出 available:true，也没交出原因")}
              </p>
            )}
            {hs?.available && <HistoryTable hs={hs} cal={d.calibers ?? {}} />}
            {/* 声明 / 口径条文（`calibers`，原先摊在表下）/ 判据版本与出处收成一处默认收起的折叠块
                （用户 2026-10-21：「把页面上的描述性内容全部收缩起来，避免过多占屏」——
                尾巴那两块原先排在整张榜之后，读者得滚过十几行才收得到）。
                **没跟着折的**：上面「母体口径」那行读数（含门槛数字）、历史分布不可用与
                「已达取数上限」那两句 `text-warning`、`cell.popHint`；
                缺数／截断一族的 caveat 由折叠块自己留在屏上。 */}
            <CaliberFold caveats={d.caveats} definitions={foldDefs}
              provenance={[d.spec_version ? `判据版本 ${d.spec_version}` : null,
                           d.as_of ? `数出自 ${String(d.as_of)}` : null]} />
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead><tr className="text-left text-muted-foreground">
                  <SortHeader label="代码" sortKey="code" dir={rowSort.dirOf("code")} onSort={sortBy} />
                  <SortHeader label="名称" sortKey="name" dir={rowSort.dirOf("name")} onSort={sortBy} />
                  <SortHeader label="现价" sortKey="close" dir={rowSort.dirOf("close")} onSort={sortBy} />
                  <SortHeader label="当日" sortKey="pct_change" dir={rowSort.dirOf("pct_change")} onSort={sortBy} />
                  <SortHeader label="涨停距今" sortKey="limit_up_ago" dir={rowSort.dirOf("limit_up_ago")} onSort={sortBy}
                    title="① 最近一根封住涨停距今几个场次（20 场以内才算过门票）" />
                  <SortHeader label="缺口日" sortKey="gap_date" dir={rowSort.dirOf("gap_date")} onSort={sortBy} />
                  <SortHeader label="缺口幅度" sortKey="gap_pct" dir={rowSort.dirOf("gap_pct")} onSort={sortBy}
                    title={plain(cal.gap ?? "")} />
                  <SortHeader label="未回补" sortKey="gap_unfilled" dir={rowSort.dirOf("gap_unfilled")} onSort={sortBy}
                    title="缺口之后连续几个场次最低价都没回到下沿（判据要求 3 场以上，且今日仍未回补）" />
                  <SortHeader label="回调量比" sortKey="pullback_max_vol_ratio" dir={rowSort.dirOf("pullback_max_vol_ratio")} onSort={sortBy}
                    title="回调段最大单日量 ÷ 缺口日量，判据要 <= 1；越小越干净（>1 就是回调路上还在放量）。整行悬停里有那句歇口气的完整口径" />
                  <SortHeader label="阴线" sortKey="pullback_down_cnt" dir={rowSort.dirOf("pullback_down_cnt")} onSort={sortBy}
                    title="回调段（往前 3 场）里几根阴线 —— 一句「歇口气」的量尺" />
                  <SortHeader label="确认" sortKey="confirm_yang" dir={rowSort.dirOf("confirm_yang")} onSort={sortBy}
                    title="今日收阳并站上昨收 = 那句「再有一根阳线的确认」" />
                  <SortHeader label="止损距" sortKey="stop_dist_pct" dir={rowSort.dirOf("stop_dist_pct")} onSort={sortBy}
                    title={plain(cal.stop ?? "")} />
                  <SortHeader label="状态" sortKey="entry_ok" dir={rowSort.dirOf("entry_ok")} onSort={sortBy} />
                </tr></thead>
                <tbody>
                  {list.map((r, i) => (
                    <tr key={`${r.code}-${i}`} {...cell.rowProps(i, r.code, undefined, hoverOf(r, cal))}>
                      <Cell i={i} k="code" cell={cell} mono>
                        <StockLink code={r.code} name={null} className="hover:text-primary" />
                      </Cell>
                      <Cell i={i} k="name" cell={cell}>
                        <StockLink code={r.code} name={r.name} className="hover:text-primary" />
                      </Cell>
                      <Cell i={i} k="close" cell={cell} mono>{n2(r.close, "")}</Cell>
                      <Cell i={i} k="pct_change" cell={cell} mono tn={tone(r.pct_change)}>{pc(r.pct_change)}</Cell>
                      <Cell i={i} k="limit_up_ago" cell={cell} mono>{n2(r.limit_up_ago, " 场前")}</Cell>
                      <Cell i={i} k="gap_date" cell={cell} mono>{dash(r.gap_date)}</Cell>
                      <Cell i={i} k="gap_pct" cell={cell} mono strong>{n2(r.gap_pct, "%")}</Cell>
                      <Cell i={i} k="gap_unfilled" cell={cell} mono>{n2(r.gap_unfilled, " 场")}</Cell>
                      <Cell i={i} k="pullback_max_vol_ratio" cell={cell} mono
                        tn={typeof r.pullback_max_vol_ratio === "number"
                          ? (r.pullback_max_vol_ratio <= 1 ? "up" : "down") : ""}>
                        {n2(r.pullback_max_vol_ratio, "×")}
                      </Cell>
                      <Cell i={i} k="pullback_down_cnt" cell={cell} mono>{n2(r.pullback_down_cnt)}</Cell>
                      <Cell i={i} k="confirm_yang" cell={cell}>{r.confirm_yang ? "收阳确认" : "未确认"}</Cell>
                      <Cell i={i} k="stop_dist_pct" cell={cell} mono>{pc(r.stop_dist_pct, 1)}</Cell>
                      <Cell i={i} k="entry_ok" cell={cell} strong>
                        {r.entry_ok ? "三条全过" : (r.watch_ok ? "观察池" : "—")}
                      </Cell>
                    </tr>
                  ))}
                </tbody>
              </table>
              {all.length === 0 && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {minGap > 0
                    ? `交出 0 行是因为「缺口幅度不低于 ${minGap} 个点」把它们筛空了：上面「这一场」那行是全场的数（三条全过几只、观察池几只），库里都有数。把下限调低就出来了 —— 不是没算。`
                    : d.only_entry
                    ? "这一场没有三条全过的票。这套体系本来就是几天一只的量（库里 242 场实测平均 5.7 只/场，不少场次是 0）—— 勾掉「只列三条全过的」能看到缺口成立、还在等回调/确认的观察池；那里也是 0，就是加工链没跑到这一天。"
                    : "连观察池都是 0 条：不是市场里没有跳空，是这张表这一天没算（看上面「基准交易日」是不是你想要的日子）。"}
                </p>
              )}
              {typeof d.limit === "number" && all.length >= d.limit && (
                <p className="mt-1 text-[10px] text-warning">已达取数上限 {d.limit} 行。</p>
              )}
              {/* 怎么点这一张表：通用一句（实现全站一份）收进默认收起的说明框（用户 2026-10-19 夜：
                  「把选项留出，描述内容都收缩起来」）；弹窗被拦的告警留在框外单独念 */}
              <ExplainBox title="这一张表怎么点 · 单击高亮什么 · 双击去哪里">
                <p className="text-[11px] leading-relaxed text-muted-foreground">{STOCK_ROW_HINT}</p>
              </ExplainBox>
              {cell.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{cell.popHint}</p>}
            </div>
            <ExplainBox title="「有资金的鱼塘」那一维：量过了，没做进母体（后端原口径）">
              <p>{plain(cal.no_fish_pond ?? "")}</p>
            </ExplainBox>
          </>
        );
      }}
    </Panel>
  );
}
