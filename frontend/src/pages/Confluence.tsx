import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { CaliberFold, ExplainBox, Panel, Row } from "@/components/ui/DataPanel";
import { SortHeader } from "@/components/ui/SortHeader";
import { StockLink } from "@/components/ui/StockLink";
import { SystemEntryBoard } from "@/components/quant/SystemEntryBoard";
import { SystemEntryCohort } from "@/components/quant/SystemEntryCohort";
import { StockDossier } from "@/components/quant/StockDossier";
import { useNonce, useProbe } from "@/lib/probe";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useCellSelection, useStockRowInteraction, type CellStore } from "@/lib/useStockRowInteraction";
import { dash } from "@/lib/datastate";

/**
 * 选股汇总（用户 2026-10-13）：把各条量化链**当天选出来的票**并到一张榜上，
 * 重点看被多个判据**重复**点到的那几只，并附上趋势评分。
 *
 * 三条不能糊过去的口径：
 *  ① 排序是「命中榜数优先，其次各榜里最好的名次」—— 用户选定的口径，**没有合成总分**。
 *     造一个 0~100 的"综合强度"会把「只在象限第 1 名」和「每个榜都第 120 名」排到一起；
 *  ② 每源后面那个数是**全场**的（`sources[].count`，来自不带筛选的聚合）；
 *     榜数分布同理走 `hits_distribution`。在前端筛就只能筛到已交出的那些行；
 *  ③ 某个源这一场没跑，`confluence_source_absent` 会点名 —— 那时"只命中 1 榜"不等于
 *     "判据不认同它"。这条必须看得见，否则缺席会被读成否定；
 *  ④ 点一行之后下面那叠「这只票的量化档案」**不是这一屏另写的一份**，它与「分组自选」
 *     共用 `components/quant/StockDossier.tsx`（同一组件、同一接口）；本屏对它只交两个参数：
 *     点中的那一行是哪只票、以及这一场是哪一天。所以档案的界面改一处，两屏一起改。
 *
 * 行序只算一次（`shown`）：表格体与"现在分析的是哪一只"读同一份数组，
 * 换排序、换筛条件、重新取数都取消选中 —— 否则高亮的行与下面说的不是同一只票。
 *
 * 各榜的选法（表、判据版本、排序、上限）只住 `app/compute/confluence.py` 的 `SOURCES`；
 * 页面只读 `sources[]` 交出来的标签与名次，不自己再数一遍。
 */

interface RowX {
  code?: string | null; name?: string | null; board?: string | null;
  hits?: number | null; hit_sources?: string[] | null; hit_labels?: string[] | null;
  ranks?: Record<string, number> | null; sizes?: Record<string, number> | null;
  metrics?: Record<string, { metric?: number | null; reasons?: number | null; strict?: boolean }> | null;
  best_source?: string | null; best_source_label?: string | null;
  best_rank?: number | null; best_rank_pct?: number | null;
  source_count?: number | null; sources_absent?: string | null; missing?: string | null;
  oper_value?: number | null; oper_pctile?: number | null; oper_rank?: number | null;
  quadrant?: string | null; oper_stale?: boolean | null;
  pct_change?: number | null; close?: number | null; has_daily?: boolean | null;
  spec_version?: string | null; calculated_at?: string | null;
}
interface SourceOpt {
  key?: string | null; label?: string | null; meaning?: string | null; board?: string | null;
  limit?: number | null; count?: number | null; size?: number | null;
}
interface Board {
  available?: boolean | null; reason?: string | null; date?: string | null; source?: string | null;
  spec_version?: string | null; rows?: RowX[] | null;
  filter?: { min_hits?: number | null; sources?: string[] | null; sources_rejected?: string[] | null;
             rows_returned?: number | null; limit?: number | null } | null;
  sources?: SourceOpt[] | null; hits_distribution?: Record<string, number> | null;
  day_total?: number | null; day_repeat_total?: number | null; source_count?: number | null;
  source_total?: number | null;
  caliber?: string | null; definitions?: Record<string, string> | null;
  coverage?: Record<string, { from?: string | null; to?: string | null; sessions?: number | null }> | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
}
interface Hist {
  available?: boolean | null; reason?: string | null; code?: string | null; date?: string | null;
  sessions_read?: number | null; spec_version?: string | null;
  repeat_total?: number | null; repeat_days?: string[] | null;
  partial_source_days?: string[] | null; caliber?: string | null;
  rows?: { day?: string | null; hits?: number | null; hit_labels?: string[] | null;
           best_source_label?: string | null; best_rank?: number | null;
           best_rank_pct?: number | null }[] | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
}

const pct = (v?: number | null, dp = 2) =>
  typeof v === "number" ? `${v >= 0 ? "+" : ""}${v.toFixed(dp)}%` : dash(null);

function hover(r: RowX, src: SourceOpt[]) {
  const bits = src.filter(o => (r.ranks ?? {})[String(o.key)] !== undefined)
    .map(o => {
      const k = String(o.key);
      const m = (r.metrics ?? {})[k] ?? {};
      const mv = typeof m.metric === "number"
        ? (k === "dgt" ? `净买额 ${(m.metric / 1e8).toFixed(2)} 亿` : `${m.metric.toFixed(2)}`)
        : "原值没读到";
      return `${o.label}：第 ${r.ranks?.[k]}/${r.sizes?.[k] ?? "?"} 名｜${mv}`
        + (m.reasons ? `｜上榜原因 ${m.reasons} 条` : "")
        + (k === "ambush" && m.strict ? "｜严档" : "");
    });
  bits.push(`趋势评分：${typeof r.oper_value === "number" ? r.oper_value.toFixed(2) : "—"} 原值`
    + `｜当日分位 ${typeof r.oper_pctile === "number" ? r.oper_pctile.toFixed(1) : "—"}%`
    + `｜全场第 ${dash(r.oper_rank)} 名`
    + (r.oper_stale ? "（沿用旧场，不与新算横比）" : ""));
  if (r.missing) bits.push(`缺的读数（源键）：${r.missing}`);
  if (r.sources_absent) bits.push(`这一场没算的源：${r.sources_absent}`);
  bits.push(`价格 ${dash(r.close)}${r.has_daily ? "" : "（当天没有日线行）"}｜涨幅 ${pct(r.pct_change)}`);
  return bits.join("\n");
}

export function Confluence() {
  const [nonce, refresh] = useNonce();
  const [minHits, setMinHits] = useState(2);
  const [picked, setPicked] = useState<string[]>([]);
  const [date, setDate] = useState("");
  const hits = useProbe<Board>(`/quant/confluence?date=${encodeURIComponent(date)}`
    + `&min_hits=${minHits}&sources=${encodeURIComponent(picked.join(","))}`, nonce);
  const srt = useTableSort();
  const cell = useStockRowInteraction("选股汇总");
  /** 列序＝表格里 td 的先后。它同时是交叉高亮的列键，`test/confluenceReads.test.ts` 钉住两边不许分叉。 */
  const COLS = ["code", "name", "hits", "hit_labels", "best_rank", "best_rank_pct", "oper_value", "pct_change"];
  const cur = hits.kind === "ok" ? hits.data : null;
  /** 排好的行**只算这一份**：表格渲染、以及「现在分析的是哪一只」都读它。
   *  与「分组自选」同一个规矩 —— 两处各排一次，换排序后高亮的那一行与下面档案说的就不是同一只票了。 */
  const shown = srt.apply(cur?.rows ?? []);
  const src = cur?.sources ?? [];
  const rejected = (cur?.filter?.sources_rejected ?? []).join("、");
  const rejectedText = rejected
    ? `｜这几个值没被认出来、没有参与筛选：${rejected}`
    : "";

  /** 换排法要取消选中：行号一漂，「高亮的那一行」与「下面档案与历史在分析的那一只」就错开了。
   *  与「分组自选」同一条规矩（`test/groupedDossier.test.ts` 在那一屏钉过，这里补一条）。 */
  const pickSort = (key: string) => { cell.clearAll(); srt.toggle(key); };

  const controls = (
    <>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
        <label className="flex items-center gap-1 text-muted-foreground"
          title="至少命中几个榜。默认 2 = 只看重复出现的票；调到 1 就把只被一个榜选中的票也列出来">
          至少命中：
          <select value={String(minHits)} aria-label="至少命中几个榜"
            disabled={hits.kind === "loading"}
            onChange={e => { setMinHits(Number(e.target.value)); cell.clearAll(); }}
            className="rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]">
            {Array.from({ length: src.length || 5 }, (_, i) => i + 1)
              .map(n => <option key={n} value={String(n)}>{n} 个榜</option>)}
          </select>
        </label>
        <span className="text-muted-foreground">交叉源（多选，库里筛）：</span>
        {src.length === 0 && <span className="text-muted-foreground">（这一场的源清单没取到）</span>}
        {src.map(o => (
          <label key={String(o.key)} className="flex items-center gap-1"
            title={[o.meaning,
                    o.count === undefined || o.count === null
                      ? "这一源这一场进了几只：没读到"
                      : `这一源这一场进了 ${o.count} 只（榜长 ${o.size ?? "—"}）`]
                      .filter(Boolean).join("｜")}>
            <input type="checkbox" className="h-3.5 w-3.5 accent-primary" disabled={hits.kind === "loading"}
              checked={picked.includes(String(o.key ?? ""))} aria-label={`交叉源 ${o.label}`}
              onChange={() => {
                const k = String(o.key ?? "");
                setPicked(p => (p.includes(k) ? p.filter(x => x !== k) : [...p, k]));
                cell.clearAll();
              }} />
            {o.label}
            <span className="tabular-nums text-muted-foreground">
              {o.count === undefined || o.count === null ? "—" : o.count}
            </span>
          </label>
        ))}
        <label className="flex items-center gap-1 text-muted-foreground">
          日期：
          <input type="date" value={date} onChange={e => setDate(e.target.value)}
            className="w-28 rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]" />
        </label>
        {(picked.length > 0 || date || minHits !== 2) && (
          <button type="button" className="text-muted-foreground hover:text-foreground"
            onClick={() => { setPicked([]); setDate(""); setMinHits(2); cell.clearAll(); }}>
            回到默认（至少 2 榜、日期回到最近一场）
          </button>
        )}
        <button type="button" onClick={() => { cell.clearAll(); refresh(); }}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>
      <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
        源后面那个数与下面的命中分布都是全场的（那条聚合不带任何筛条件）；
        勾选在库里生效、发生在取数上限之前，所以是真筛，不是在截断后的名单里挑。
        {rejectedText}
      </p>
    </>
  );

  return (
    <div className="mx-auto max-w-[1720px]">
      <PageHeader title="选股汇总" level={1}
        subtitle="各条量化链当天选出的票并到一起，重点看被多个判据重复点到的那几只。排序＝命中榜数优先，其次各榜里最好的名次；没有合成总分。" />

      <Panel title="交叉命中榜" state={hits} controls={controls}
        note="这一屏不重新判任何东西，只把各条量化链当天选出的名单并到一起（这一场有哪几个源、每个源进了几只，读上面那一排勾选与每行「重复出现在哪些榜」，页面不自己数）。各榜彼此不独立，所以「命中 3 榜」只等于值得多看一眼，不等于涨跌概率。点任意一行：下面两块（这只票的量化档案、它历史上重复出现的那些天）一起换到那一票；再点同一格取消。">
        {d => {
          const all = d.rows ?? [];
          const dist = d.hits_distribution;
          return (
            <>
              <Row label="基准交易日" value={dash(d.date)} />
              <Row label="判据版本" value={dash(d.spec_version)} />
              <Row label="进过至少一个榜"
                value={`${dash(d.day_total)} 只｜其中重复出现（>=2 榜）${dash(d.day_repeat_total)} 只`
                  + `｜本页交出 ${all.length} 行`} />
              <Row label="命中榜数分布（全场）"
                value={dist
                  ? Object.entries(dist).map(([k, v]) => `${k} 榜 ${v} 只`).join("、")
                  : "没读到（那条聚合失败了）"} />
              <Row label="这一场有数的源"
                value={`${dash(d.source_count)} 个（hits 的分母；有源没跑时会小于 ${dash(d.source_total)} 个）`} />
              {/* 用户 2026-10-21：「描述性内容全部收缩起来」。缺数／截断那一族由 CaliberFold 自己留在屏上，
                  这里不需要每屏再判一次（分类只住 `lib/caveatKind.ts` 一处）。 */}
              <CaliberFold caveats={d.caveats} definitions={d.definitions}
                coverage={d.coverage} provenance={[d.source, d.caliber]} />
              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead><tr className="text-left text-muted-foreground">
                    <SortHeader label="代码" sortKey="code" dir={srt.dirOf("code")} onSort={pickSort} />
                    <SortHeader label="名称" sortKey="name" dir={srt.dirOf("name")} onSort={pickSort} />
                    <SortHeader label="命中榜数" sortKey="hits" dir={srt.dirOf("hits")} onSort={pickSort}
                      title="几个独立判据在同一天点到了这只票。这是本屏的主尺子。" />
                    <SortHeader label="重复出现在哪些榜" sortKey="hit_labels"
                      dir={srt.dirOf("hit_labels")} onSort={pickSort}
                      title="顿号连接的是这一场命中了哪几个榜（按各榜名次从好到差排）。要按具体名次排，点「最好名次分位」。" />
                    <SortHeader label="最好名次" sortKey="best_rank" dir={srt.dirOf("best_rank")} onSort={pickSort}
                      title="它在自己最好的那个榜里排第几（榜名一起给出）。名次分位才是跨榜可比的量。" />
                    <SortHeader label="名次分位" sortKey="best_rank_pct" dir={srt.dirOf("best_rank_pct")} onSort={pickSort}
                      title="该榜名次 ÷ 该榜这一场的榜长 ×100。越小 = 在越多的榜里越靠前。象限榜 200 长的第 30 名和首破榜 37 长的第 30 名不是一回事，所以同榜比名次、跨榜只比它。" />
                    <SortHeader label="趋势评分" sortKey="oper_value" dir={srt.dirOf("oper_value")} onSort={pickSort}
                      title="analytics.score 的 oper_score 原值（不是本页算的）：oper = 0.40×个股质量 + 0.60×环境共振。八分量明细在「趋势评分」那一屏。" />
                    <SortHeader label="当日涨跌" sortKey="pct_change" dir={srt.dirOf("pct_change")} onSort={pickSort}
                      title="当日收盘相对前一日的涨跌幅（stock_daily 原值）。当天没有日线行（停牌/未入库）显示 —，不按 0% 平盘处理。" />
                  </tr></thead>
                  <tbody>
                    {shown.map((r, i) => (
                      <tr key={`${r.code}-${i}`} className={cell.getRowClass(i, "border-t border-border/40 cursor-pointer")}
                        onDoubleClick={() => cell.openReview(String(r.code ?? "").split(".")[0])}
                        title="双击这一行：另开一个浏览器窗口看这一只的「个股深度复盘」"
                        onClick={e => {
                        const td = (e.target as HTMLElement).closest("td") as HTMLTableCellElement | null;
                        if (!td) return;
                        const idx = Array.prototype.indexOf.call(td.parentElement?.children ?? [], td);
                        cell.handleCellClick(td, i, COLS[idx] ?? COLS[0]);
                      }}>
                        <td className={cell.getCellClass(i, "code", "whitespace-nowrap font-mono p-2")}>
                          <Link className="hover:text-primary"
                            to={`/stock-data?symbol=${encodeURIComponent(String(r.code ?? "").split(".")[0])}`}>
                            {dash(r.code)}
                          </Link>
                        </td>
                        <td className={cell.getCellClass(i, "name", "whitespace-nowrap p-2")}
                          title={hover(r, src)}>
                          <StockLink code={r.code} name={r.name} className="hover:text-primary" />
                        </td>
                        <td className={cell.getCellClass(i, "hits", "font-mono font-semibold tabular-nums p-2")}>
                          {dash(r.hits)}
                          {(r.hits ?? 0) >= 3 && <span className="ml-1 rounded bg-primary/15 px-1 text-[10px] text-primary">多</span>}
                        </td>
                        <td className={cell.getCellClass(i, "hit_labels", "whitespace-nowrap p-2")} title={hover(r, src)}>
                          {(r.hit_labels ?? []).join("、") || dash(null)}
                        </td>
                        <td className={cell.getCellClass(i, "best_rank", "whitespace-nowrap font-mono tabular-nums p-2")}>
                          {dash(r.best_rank)}
                          <span className="ml-1 text-muted-foreground">{dash(r.best_source_label)}</span>
                        </td>
                        <td className={cell.getCellClass(i, "best_rank_pct", "font-mono tabular-nums p-2")}>
                          {typeof r.best_rank_pct === "number" ? `${r.best_rank_pct.toFixed(2)}` : dash(null)}
                        </td>
                        <td className={cell.getCellClass(i, "oper_value", "font-mono tabular-nums p-2")}>
                          {typeof r.oper_value === "number" ? r.oper_value.toFixed(2) : dash(null)}
                          <span className="ml-1 text-muted-foreground tabular-nums">
                            {typeof r.oper_pctile === "number" ? `${r.oper_pctile.toFixed(1)}%位` : ""}
                          </span>
                        </td>
                        <td className={cell.getCellClass(i, "pct_change", "font-mono tabular-nums p-2")}>
                          {pct(r.pct_change)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {all.length === 0 && (
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {minHits > 1
                      ? `这一场没有票同时进过 ${minHits} 个榜（不是没数：上面「进过至少一个榜」那行是全场的数）。把「至少命中」调到 ${minHits - 1} 看看。`
                      : "这一场后端交出 0 行 —— 汇总链没跑到这一天，还是各条链当天都没选出票？看上面的基准交易日。"}
                  </p>
                )}
                {typeof d.filter?.limit === "number" && all.length >= d.filter.limit && (
                  <p className="mt-1 text-[10px] text-warning">
                    已达取数上限 {d.filter.limit} 行：下面看到的不是这一场的全部，
                    上面「进过至少一个榜」那个数才是全场。
                  </p>
                )}
                {/* 怎么点这一张表：句子与实现都全站一份，收进默认收起的说明框（用户 2026-10-19 夜：
                    「把选项留出，描述内容都收缩起来」）；弹窗被拦的告警单独念、不折进说明框。 */}
                <ExplainBox title="这一张表怎么点 · 单击展开什么 · 双击去哪里">
                  <p className="text-[11px] leading-relaxed text-muted-foreground">{STOCK_ROW_HINT}</p>
                </ExplainBox>
                {cell.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{cell.popHint}</p>}
              </div>
            </>
          );
        }}
      </Panel>

      {/* 档案与历史那两块换到一个订阅选中态的组件里（`FocusPane`）：
          交叉高亮写在 DOM 上，页面组件不因单击重渲染，表格（最多 300 行 × 8 列）就不必再协调一遍。 */}
      <FocusPane store={cell.store} date={cur?.date} nonce={nonce}
        codeAt={(row) => shown[row]?.code ?? null} />

      <SystemEntryBoard />

      {/* 走完三条的个股近六个月名单（同一张母体表的历史视图，单独一份只读接口） */}
      <SystemEntryCohort />

      <Disclaimer compact />
    </div>
  );
}

/**
 * 点中某一行之后跟着换标的这两块：量化档案 ＋ 历史上重复出现过哪些天。
 *
 * 它单独成一个组件，只为一件事：**把「选中了哪一行」的订阅圈在这两块里**。
 * 10-26 之前 `focus` 是在页面组件里读的，于是每一次单击都要把上面那张最多 300 行 × 8 列的表
 * 整个再协调一遍 —— 与倍量页那张 800 格的表实测同一个形状（一次单击 461 毫秒的长任务，
 * 而 React 把第一格改动提交出去只要 6~10 毫秒）。交叉高亮现在写在 DOM 上，
 * 页面组件不再因为点击而渲染，只有这里通过 `useCellSelection` 换标的。
 * ⚠️ 别把 `useCellSelection` 提回页面组件 —— 那等于把这条链又接回整张表。
 *
 * 行序仍只算一份：`codeAt` 由页面交进来，读的就是表格渲染的那同一个 `shown`，
 * 所以高亮的那一行、档案说的那一只、历史查的那一只，永远是同一只票。
 */
function FocusPane({ store, date, nonce, codeAt }: {
  store: CellStore;
  date: string | null | undefined;
  nonce: number;
  codeAt: (row: number) => string | null;
}) {
  const sel = useCellSelection(store);
  const focus = sel ? codeAt(sel.row) : null;
  const hist = useProbe<Hist>(`/quant/confluence-history?code=${encodeURIComponent(focus ?? "")}`,
    // 没选中任何一只票时**根本不发请求**（后端会拿一个假代码去查），交回 idle 由 Panel 说明
    nonce, undefined, !focus);

  return (
    <>
      {/* 用户 2026-10-14：「这里单击某一个个股，也要在下面展开个股的针对性全面分析」——
          这一块**不是本屏另写的一份**：它与「个股深度复盘」（10-15 起的主家）共用 `components/quant/StockDossier.tsx`
          那一份实现（八块读数全读 /api/quant/stock-dossier，一个判据都不在页面重算），
          所以那边改界面、这边跟着一起改。对象就是上面点中的那一行；没点中时组件根本不发请求。 */}
      <StockDossier code={focus} date={date} />

      <Panel title="这只票历史上重复出现过哪些天" state={hist}
        note="选中上面任意一行就换到那只票。这一列只回答时间维度：它过去多少天被两个及以上判据同时点到过、最近一次是哪天。">
        {d => (
          <>
            <Row label="代码" value={dash(d.code || focus)} />
            <Row label="读到的一场数" value={`${dash(d.sessions_read)} 场（判据版本 ${dash(d.spec_version)}）`} />
            <Row label="重复出现（>=2 榜）"
              value={`${dash(d.repeat_total)} 场${(d.repeat_days ?? []).length ? `｜最近：${(d.repeat_days ?? []).slice(0, 6).join("、")}` : ""}`} />
            <CaliberFold caveats={d.caveats} provenance={[d.caliber]} />
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead><tr className="text-left text-muted-foreground">
                  {["交易日", "命中榜数", "重复出现在哪些榜", "最好名次", "名次分位"].map(h => (
                    <th key={h} className="whitespace-nowrap px-2 py-1.5 font-normal">{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {(d.rows ?? []).slice(0, 60).map((r, i) => (
                    <tr key={`${r.day}-${i}`} className="border-t border-border/40">
                      <td className="whitespace-nowrap font-mono p-2">{dash(r.day)}</td>
                      <td className="font-mono tabular-nums p-2">{dash(r.hits)}</td>
                      <td className="whitespace-nowrap p-2">{(r.hit_labels ?? []).join("、") || dash(null)}</td>
                      <td className="whitespace-nowrap font-mono tabular-nums p-2">
                        {dash(r.best_rank)}<span className="ml-1 text-muted-foreground">{dash(r.best_source_label)}</span>
                      </td>
                      <td className="font-mono tabular-nums p-2">
                        {typeof r.best_rank_pct === "number" ? `${r.best_rank_pct.toFixed(2)}` : dash(null)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {(d.rows ?? []).length === 0 && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  这只票在被汇总覆盖的这些天里，没有一天进过两个及以上的榜（一次都没进过任何榜时也长这样 ——
                  分辨方法看上面「读到的一场数」是不是 0 场）。
                </p>
              )}
            </div>
          </>
        )}
      </Panel>
    </>
  );
}

