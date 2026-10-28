import { ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { useState } from "react";
import { CaliberFold, Definitions, ExplainBox, Panel, Row } from "@/components/ui/DataPanel";
import { SortHeader } from "@/components/ui/SortHeader";
import { StockLink } from "@/components/ui/StockLink";
import { plain } from "@/lib/agent";
import { dash } from "@/lib/datastate";
import { useNonce, useProbe } from "@/lib/probe";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { useTableSort } from "@/lib/useSort";
import { Cell, n2, pc, tone } from "@/components/quant/SystemEntryBoard";

/**
 * 交易体系榜下面那一叠（用户 2026-10-22 点名）：近 N 个月里走完三条的个股名单（N 读接口）。
 *
 * 三件事都读后端那一份，一块都不在这儿算：涨幅与位次轨迹的算法在
 * `duanxian/system_entry_cohort.py`，交叉源清单在 `app/compute/confluence.py::SOURCES`，
 * 涨幅几档的名字与说明在 `SPAN_COLUMNS` 那一份 —— 页面不抄中文列名（同上面那块历史分布）。
 *
 * 密度是用户指定的一格一段（「q3-q3-q1-… 具体分数也换行罗列」）：
 * 首排是这 8 个场次的日期（符合日打头 ▸），每个交叉源两排 —— 上排名次
 * （象限那一源念档位 Q1~Q4，名次留在同一格的悬停里），下排该榜排序用的原值。
 * 空位念 `·`：那一场它没进这个榜，与"那天没有行情"是两件事（悬停里分得开）。
 * 源的名字一个都不在这里出现 —— 读接口交出的那一份（`CF.SOURCES` 经 `duanxian` 转述）。
 *
 * 分页在前端切（后端一次交全批、`page_size` 读接口那一份），所以点列头排的是整批
 * 而不是"这一页里谁第一"。密度按用户口述保持一格一段。
 */

interface SeqSource {
  key?: string; label?: string; metric?: string; board?: string;
  tier?: (string | null)[]; rank?: (number | null)[]; value?: (number | null)[];
}
interface Seq {
  i?: number; n?: number; signal_at?: number;
  hits?: (number | null)[]; in_conf?: (boolean | null)[]; sources?: SeqSource[];
}
interface RowT {
  code?: string; name?: string; board?: string; date?: string;
  gap_pct?: number; gap_date?: string; gap_ago?: number; gap_unfilled?: number;
  stop_dist_pct?: number; close?: number; pct_change?: number; limit_up_ago?: number;
  pullback_max_vol_ratio?: number;
  c1?: number; day1?: number; c2?: number; day2?: number; c3?: number; day3?: number;
  c5?: number; day5?: number; c10?: number; day10?: number; c15?: number; day15?: number;
  fwd_at?: number[]; fwd_sessions?: number; fwd_bars?: number; has_base?: boolean;
  seq?: Seq;
}
interface ColSpec { n?: number; label?: string; cum?: string; day?: string; note?: string }
interface Data {
  available?: boolean; reason?: string; date?: string;
  spec_version?: string; confluence_spec_version?: string; months?: number;
  window?: { from?: string; to?: string; sessions?: number };
  page_size?: number; total?: number; codes?: number; rows?: RowT[]; sessions?: string[];
  sizes?: Record<string, Record<string, number>>; universe?: Record<string, number>;
  stale_days?: string[]; conf_days?: string[]; span_columns?: ColSpec[];
  seq?: { back?: number; fwd?: number; cells?: number };
  sources?: { key?: string; label?: string; metric?: string; board?: string;
              limit?: number | null; meaning?: string }[];
  calibers?: Record<string, string>;
  caliber_items?: { key?: string; title?: string; text?: string }[];
  caveats?: { code?: string; label?: string; text?: string }[];
  self_check?: string[]; generated_at?: string; cached?: boolean;
  cache_age_seconds?: number; as_of?: string;
}

const num = (v: unknown) => (typeof v === "number" ? v : null);
const cellNum = (r: RowT, k?: string) => (k ? num(r[k as keyof RowT]) : null);

/** 一串位次/分数收成人眼能扫的一段（用户口述的「q3-q3-q1-…」那种写法）：缺数念 `·`，不是 0。
 *  分隔符可换：名次/档位那一段用 `-`，带负号的分数那一段用空格 —— 否则 `5.8--12.26`
 *  会把"负号"读成"分隔符"（屏幕上真的会看错）。 */
function tokens(arr: (string | number | null)[] | undefined, n: number, sep = '-') {
  const out: string[] = [];
  for (let k = 0; k < n; k++) {
    const v = arr?.[k];
    out.push(v === null || v === undefined || v === "" ? "·" : String(v));
  }
  return out.join(sep);
}

/** 象限那一源：档位与全市场名次并在一起念（`Q4(926)`）—— 两者都是"它那天排到哪"，
 *  分成两排就把每一行的密度翻倍，而名次单独一排又没人看得完。 */
function tierRankLine(s: SeqSource, n: number) {
  const bits: string[] = [];
  for (let k = 0; k < n; k++) {
    const t = s.tier?.[k] ?? null;
    const rank = num(s.rank?.[k]);
    if (t === null && rank === null) bits.push("·");
    else bits.push(`${t ?? "·"}${rank === null ? "" : `(${rank})`}`);
  }
  return bits.join("-");
}

/** 每行的第一排：这 8 个场次是谁（符合日打 ▸）。日期从响应里那一份 `sessions` 按下标取。
 *  用空格分隔而不是 `-`：日期自己就带横杠（`10-19`），再拿横杠当分隔符就成 `10-19-10-20` 了。 */
function dayLine(seq: Seq | undefined, sessions: string[]) {
  const n = seq?.n ?? 0;
  const bits: string[] = [];
  for (let k = 0; k < n; k++) {
    const d = sessions[(seq?.i ?? 0) + k] ?? "";
    bits.push((k === seq?.signal_at ? "▸" : "") + (d ? d.slice(5) : "·"));
  }
  return bits.join(" ");
}

/** 一格的悬停：把「哪一天、档位、名次/榜长、原值、是不是沿用旧场」逐天写全。 */
function seqTitle(s: SeqSource, seq: Seq | undefined, days: string[],
                  sizes: Record<string, number> | undefined, stale: Set<string>) {
  const n = seq?.n ?? 0;
  const lines: string[] = [];
  for (let k = 0; k < n; k++) {
    const day = days[k] ?? "";
    const tier = s.tier?.[k] ?? null;
    const rank = num(s.rank?.[k]);
    const size = num(sizes?.[day]);
    const val = num(s.value?.[k]);
    const bits = [day || "·"];
    if (tier) bits.push(`档位 ${tier}`);
    bits.push(rank === null ? "没进这个榜" : `名次 ${rank}${size ? `／共 ${size}` : ""}`);
    if (val !== null) bits.push(`${s.metric ?? "值"} ${val}`);
    if (stale.has(day)) bits.push("象限分沿用旧场");
    lines.push(bits.join("｜"));
  }
  return `${s.label ?? ""}（下面那一排是 ${s.metric ?? "原值"}）\n${lines.join("\n")}`;
}

function hoverOf(r: RowT, cal: Record<string, string>, cols: ColSpec[]) {
  const cum = cols.map(c => `${c.label} ${pc(cellNum(r, c.cum))}`).join("｜");
  return [
    `符合日 ${dash(r.date)}｜缺口 ${dash(r.gap_date)}（${dash(r.gap_ago)} 场前）、`
      + `幅度 ${n2(r.gap_pct, " 个点")}、连续 ${dash(r.gap_unfilled)} 场没回补`,
    `涨停距今 ${dash(r.limit_up_ago)} 场｜回调量比 ${n2(r.pullback_max_vol_ratio, "×")}`
      + `｜止损距 ${pc(r.stop_dist_pct, 1)}`,
    `累计涨幅：${cum}`,
    `右端能数到 ${dash(r.fwd_sessions)} 场，其中它有行情 ${dash(r.fwd_bars)} 场`
      + `${r.has_base ? "" : "｜符合日那天它没有行情，所以两把尺都不该有数"}`,
    `命中榜数（逐格）：${tokens(r.seq?.hits, r.seq?.n ?? 0)}`,
    cal.forward ?? "", cal.seq ?? "",
  ].filter(Boolean).join("\n");
}

/**
 * 位次那一格：不走 `Cell`（它不是一格一个数，交叉高亮到这里没有意义），
 * 但整行仍在 `rowProps` 的高亮与双击跳转里 —— 行列交叉高亮管的是别的列。
 */
function SeqCell({ r, sessions, sizes, universe, stale }: {
  r: RowT; sessions: string[]; sizes?: Data["sizes"]; universe?: Data["universe"];
  stale: Set<string>;
}) {
  const seq = r.seq ?? {};
  const n = seq.n ?? 0;
  const days = Array.from({ length: n }, (_, k) => sessions[(seq.i ?? 0) + k] ?? "");
  const srcs = seq.sources ?? [];
  return (
    <td className="px-1.5 py-1 align-top">
      <div className="whitespace-nowrap font-mono text-[9px] leading-4 text-muted-foreground/80"
        title={`这 ${n} 格是哪几天（▸＝符合日那一天）`}>{dayLine(seq, sessions)}</div>
      {srcs.map(s => (
        <div key={String(s.key)} className="leading-4">
          <div className="whitespace-nowrap font-mono text-[10px]"
            title={plain(seqTitle(s, seq, days,
              s.key === "quadrant" ? universe ?? {} : sizes?.[String(s.key)] ?? {}, stale))}>
            <span className="mr-1 font-sans text-muted-foreground">{s.label}</span>
            {s.tier ? tierRankLine(s, n) : tokens(s.rank, n)}
          </div>
          <div className="whitespace-nowrap font-mono text-[9px] text-muted-foreground">
            {tokens(s.value, n, ' ')}
          </div>
        </div>
      ))}
      {n === 0 && <span className="text-[10px] text-muted-foreground">没有位次数据</span>}
      {n > 0 && srcs.length === 0 && (
        <div className="text-[10px] text-muted-foreground"
          title="这几场它一次都没进过任何一份名单（不代表那几天没有行情）">这一段没进任何榜</div>
      )}
    </td>
  );
}

const monthsHint = (m?: number) => (m ? `近 ${m} 个月` : "近几个月");

export function SystemEntryCohort() {
  const [nonce, refresh] = useNonce();
  const [page, setPage] = useState(0);
  const rowSort = useTableSort();
  const cell = useStockRowInteraction("交易体系榜的六个月名单");
  const sortBy = (k: string) => { cell.clearAll(); setPage(0); rowSort.toggle(k); };

  const q = new URLSearchParams();
  // 点「重新取数」必须真的绕过后端那一小时缓存，否则按钮是假的（同一族坑见任务 #105）
  if (nonce > 0) q.set("refresh", "1");
  const hits = useProbe<Data>(`/quant/system-entry-cohort?${q.toString()}`, nonce);

  const cur = hits.kind === "ok" ? hits.data : null;
  const cal = cur?.calibers ?? {};
  const cols = cur?.span_columns ?? [];
  const sessions = cur?.sessions ?? [];
  const stale = new Set(cur?.stale_days ?? []);

  const controls = (
    <>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
        <button type="button" onClick={() => { setPage(0); refresh(); }}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数（现算，约 8 秒）
        </button>
        {cur?.generated_at && (
          <span className="text-muted-foreground">
            这批数算于 {cur.generated_at}
            {cur.cached ? `（缓存里，${Math.round(cur.cache_age_seconds ?? 0)} 秒前实算）` : "（刚刚现算）"}
          </span>
        )}
      </div>
      <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{plain(cal.cohort ?? "")}</p>
    </>
  );

  const sorted = rowSort.apply(cur?.rows ?? []);
  const pageSize = cur?.page_size ?? 20;
  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const cur0 = Math.min(page, pages - 1);
  const slice = sorted.slice(cur0 * pageSize, cur0 * pageSize + pageSize);

  return (
    <Panel title={`走完三条的个股 · ${monthsHint(cur?.months)}名单（按符合日从近到远）`}
      state={hits} controls={controls}
      note={
        "用户 2026-10-22 点名：把这段时间里出现过这套参数的个股都列出来，每行再看两件事 —— "
        + "符合日之后几档场次的涨幅（档就是列头那几格，口径在同一格的悬停里），"
        + "以及符合日前后那一段里它在交叉命中榜各源每天排第几、那个榜排序用的原值是多少"
        + "（具体分数换行罗列；几场到几场写在位次那一列的列头上）。"
        + "这一块一块判据都不重算，只读上面那张榜与交叉命中榜落好的行。"
      }>
      {d => {
        // 字典的头念中文标题（`caliber_items` 那一份）；`calibers` 的机器键只留给内部引用与悬停
        const items = d.caliber_items ?? [];
        const noteMap: Record<string, string> = Object.fromEntries(
          items.filter(c => c.key !== "cohort" && c.title).map(c => [String(c.title), String(c.text)]));
        return (
          <>
            <Row label="窗口"
              value={`${d.months} 个自然月：${dash(d.window?.from)} ~ ${dash(d.window?.to)}`
                + `｜共 ${dash(d.total)} 行（${dash(d.codes)} 只票 —— 行数多于只数，因为一只票走完过两次）`
                + `｜这窗市场场次 ${dash(d.window?.sessions)} 场`} />
            <Row label="每页行数" tone="warn"
              value={`${d.page_size ?? 20} 行一页，现在第 ${cur0 + 1}/${pages} 页`
                + `｜翻页与点列头排的都是整批 ${d.total} 行（后端一次交全，前端只切屏）`} />
            <CaliberFold caveats={d.caveats}
              provenance={[d.spec_version ? `母体判据版本 ${d.spec_version}` : null,
                d.confluence_spec_version ? `交叉榜版本 ${d.confluence_spec_version}` : null,
                d.as_of ? `数出自 ${String(d.as_of)}` : null]} />
            {(d.self_check ?? []).length > 0 && (
              <p className="mt-1 text-[11px] leading-4 text-warning">
                这一块自检没过：{d.self_check?.join("；")} —— 下面的格子先别照着下结论。
              </p>
            )}
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead><tr className="text-left text-muted-foreground">
                  <SortHeader label="符合日" sortKey="date" dir={rowSort.dirOf("date")} onSort={sortBy} />
                  <SortHeader label="代码" sortKey="code" dir={rowSort.dirOf("code")} onSort={sortBy} />
                  <SortHeader label="名称" sortKey="name" dir={rowSort.dirOf("name")} onSort={sortBy} />
                  <SortHeader label="现价" sortKey="close" dir={rowSort.dirOf("close")} onSort={sortBy} />
                  <SortHeader label="当日" sortKey="pct_change" dir={rowSort.dirOf("pct_change")} onSort={sortBy} />
                  <SortHeader label="缺口幅度" sortKey="gap_pct" dir={rowSort.dirOf("gap_pct")} onSort={sortBy}
                    title="符合日那天的缺口幅度（个点）—— 母体榜内就是按它排的" />
                  {cols.map(c => (
                    <SortHeader key={String(c.cum)} label={String(c.label)}
                      sortKey={String(c.cum)} dir={rowSort.dirOf(String(c.cum))} onSort={sortBy}
                      title={plain(c.note ?? "")} />
                  ))}
                  <th className="whitespace-nowrap px-1.5 py-1 font-normal"
                    title={plain(d.calibers?.seq ?? "")}>
                    交叉命中榜的位次（前 {d.seq?.back ?? 2} 场 ~ 后 {d.seq?.fwd ?? 5} 场；上排名次、下排原值）
                  </th>
                </tr></thead>
                <tbody>
                  {slice.map((r, i) => (
                    <tr key={`${r.code}-${r.date}-${i}`}
                      {...cell.rowProps(i, r.code, undefined, hoverOf(r, cal, cols))}>
                      <Cell i={i} k="date" cell={cell} mono>{(r.date ?? "").slice(5)}</Cell>
                      <Cell i={i} k="code" cell={cell} mono>
                        <StockLink code={r.code} name={null} className="hover:text-primary" />
                      </Cell>
                      <Cell i={i} k="name" cell={cell}>
                        <StockLink code={r.code} name={r.name} className="hover:text-primary" />
                      </Cell>
                      <Cell i={i} k="close" cell={cell} mono>{n2(r.close, "")}</Cell>
                      <Cell i={i} k="pct_change" cell={cell} mono tn={tone(r.pct_change)}>
                        {pc(r.pct_change)}
                      </Cell>
                      <Cell i={i} k="gap_pct" cell={cell} mono strong>{n2(r.gap_pct, "%")}</Cell>
                      {cols.map(c => {
                        const k = String(c.cum);
                        const cum = cellNum(r, k);
                        const one = cellNum(r, String(c.day));
                        return (
                          <Cell key={k} i={i} k={k} cell={cell} mono tn={tone(cum)}
                            title={`${c.label}：累计 ${pc(cum)}｜那一天单日 ${pc(one)}`
                              + "（只有次日那一格两个数恒等）"}>
                            {pc(cum)}
                          </Cell>
                        );
                      })}
                      <SeqCell r={r} sessions={sessions} sizes={d.sizes} universe={d.universe}
                        stale={stale} />
                    </tr>
                  ))}
                </tbody>
              </table>
              {sorted.length === 0 && (
                <p className="mt-1 text-[11px] leading-4 text-warning">
                  这一窗一行都没交出 —— 上面「窗口」那行写的月数就是查的月数。
                  要么这一段确实没人走完三条，要么那张母体表没跑到这些日子（上面 caveat 已点名是哪一种）。
                </p>
              )}
              <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
                <button type="button" disabled={cur0 <= 0}
                  onClick={() => { cell.clearAll(); setPage(Math.max(0, cur0 - 1)); }}
                  className="flex items-center gap-1 rounded border border-border/60 px-1.5 py-0.5 disabled:opacity-40">
                  <ChevronLeft className="h-3.5 w-3.5" /> 上一页
                </button>
                <span className="text-muted-foreground">
                  第 {cur0 + 1} / {pages} 页（每页 {pageSize} 行，整批 {sorted.length} 行）
                </span>
                <button type="button" disabled={cur0 >= pages - 1}
                  onClick={() => { cell.clearAll(); setPage(Math.min(pages - 1, cur0 + 1)); }}
                  className="flex items-center gap-1 rounded border border-border/60 px-1.5 py-0.5 disabled:opacity-40">
                  下一页 <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </div>
              <ExplainBox title={`这些格子怎么读（后端原口径 · ${Object.keys(noteMap).length} 条）`}>
                <Definitions map={noteMap} bare />
              </ExplainBox>
              <ExplainBox title="这一张表怎么点 · 单击高亮什么 · 双击去哪里">
                <p className="text-[11px] leading-relaxed text-muted-foreground">{STOCK_ROW_HINT}</p>
              </ExplainBox>
              {cell.popHint && (
                <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{cell.popHint}</p>
              )}
            </div>
          </>
        );
      }}
    </Panel>
  );
}
