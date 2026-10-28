import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { Caliber } from "@/components/ui/Caliber";
import { SortHeader } from "@/components/ui/SortHeader";
import { Caveats, Definitions, ExplainBox, Panel } from "@/components/ui/DataPanel";
import { useNonce, useProbe } from "@/lib/probe";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { pctColor } from "@/lib/colors";
import { cn } from "@/lib/utils";
import {
  COPY, DASH, JUMP_PCTS, LIMITS, MONTHS_FALLBACK, SEG4_PRESET_COPY, WINDOWS_FALLBACK,
  answerCellText, answerColumns, answerRows, caveatItems, cohortOptionsFor, coverageSentence,
  detailCellText, detailColumns, ex5AccountText, gapText, groupCellText, groupCellTitle,
  groupColumns, intText, isPrescreenedCohort, jointCellText, jointColumns, jointHeadline,
  jointRows, jumpCellText, jumpColumns, jumpDaysMeaning, jumpHeadline, jumpRows, optionsFor,
  queryParams, shortTitle, spikeHeadline, versionWarning, windowShort,
  type SsdColumn, type SsdOption, type SsdPayload, type SsdRow,
} from "@/lib/spikeDays";

/**
 * 倍量形态页 →「启动前 30 场爆量与突然拉高：落在量增的天还是量减的天」。
 *
 * 上面三屏读的是量（倍量天数）、形状（首板形状）与位置（名次走向）。这一块读的是**量与价在同一天上的联合**：
 * 倍量（爆量）那些天到底收涨还是收跌，以及那种一天之内突然拉高的日子是摊在倍量日上、还是摊在缩量日上。
 * 三张表按那一问的顺序排：① 联合对照（天数与收涨收跌必须在同一行）② 突拉落在哪一类天 ③ 分档 × 结果。
 *
 * 三条界面上不许糊掉的线（判读全在 `lib/spikeDays.ts`，这里只排版、不重算任何一个数）：
 *  · 这是**日线代理**（一天一个数），不是分时图上的爆量与急拉 —— 分钟线这一轮没做，两套判据不并表；
 *  · `jump_days` 的 0 与 `—` 是两件事：0 是窗口凑满而真没有突拉，`—` 是没凑满／那天没数据；
 *  · 超额（`ex5_med`／`outcome_ex5`）在北交所没有分类指数 ⇒ null 的行从分母里剔掉并点名剔了几条，
 *    不拿 0 当「没赚」。
 *
 * 母体默认 `anyfirst`（全部首板，不筛结果）：另外几档是按结果筛过的，那一档里「≥4 板率」只是把筛条件
 * 重复一遍（`seg4CellText` 把那一格换成那句话本身）。
 *
 * 交互只取全站那一份 `useStockRowInteraction` 的 `openReview` 与 `popHint`：这一块主体是三张聚合表
 * （一行是一类天 / 一档），按票的读数在最后一张明细里，那里不接交叉高亮（地址也不在这里拼）。
 */

const DEFAULTS = { cohort: "anyfirst", months: 6, window: 30, jumpPct: 7, limit: 400 };

/** 明细一页几只票（一次最多 600 行；分页不重新发请求）。 */
const PAGE = 40;

function Select({ label, value, onChange, options, title }: {
  label: string; value: string | number; onChange: (v: string) => void;
  options: readonly SsdOption[]; title?: string;
}) {
  return (
    <label className="flex items-center gap-1 text-[11px] text-muted-foreground" title={title}>
      {label}
      <select value={String(value)} onChange={e => onChange(e.target.value)}
        className="rounded border border-border/60 bg-background px-1.5 py-0.5 text-[11px] text-foreground">
        {options.map(o => <option key={String(o.value)} value={String(o.value)}>{o.label}</option>)}
      </select>
    </label>
  );
}

/** 表头格：`sortKey` 就是这一格真正读的字段名（写错了不报错，只是点了不换序）。 */
function Head({ col, def, dir, onSort }: {
  col: { key: string; label: string; sortable: boolean; title: string };
  def?: string; dir: "asc" | "desc" | null; onSort: (k: string) => void;
}) {
  const tip = [col.title, def && `接口 definitions 里 ${col.key} 的原文：${def}`].filter(Boolean).join("　");
  if (!col.sortable) {
    return (
      <th className="whitespace-normal px-1 py-1 text-left font-normal text-muted-foreground" title={tip}>
        {col.label}
      </th>
    );
  }
  return <SortHeader label={col.label} sortKey={col.key} dir={dir} onSort={() => onSort(col.key)} title={tip} className="" />;
}

export function StartupSpikeDaysBoard() {
  const [p, setP] = useState(DEFAULTS);
  const [nonce, refresh] = useNonce();
  const [openId, setOpenId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const state = useProbe<SsdPayload>("/quant/startup-spike-days", nonce, queryParams(p));
  /** 这一块只取「双击开个股深度复盘」与那条弹窗告警：聚合表没有行号可记，交叉高亮就不接 */
  const { openReview, popHint, clearAll } = useStockRowInteraction("启动前爆量与突拉");
  const jointSort = useTableSort();
  const jumpSort = useTableSort();
  const ansSort = useTableSort();
  const grpSort = useTableSort();

  const cohorts = cohortOptionsFor(state.kind === "ok" ? state.data.options?.cohorts : null);
  const monthsOpts = optionsFor(state.kind === "ok" ? state.data.options?.months : null, MONTHS_FALLBACK);
  const windowsOpts = optionsFor(state.kind === "ok" ? state.data.options?.windows : null, WINDOWS_FALLBACK);
  const jumpOpts = JUMP_PCTS.map(n => ({ value: n, label: `当日涨幅超 ${n}%` }));
  /** 换控件＝换一次取数：参数进 query，顺手清掉展开态与告警（行号会跟着漂到别的票身上） */
  const pick = (v: string) => { clearAll(); setOpenId(null); setPage(1); return v; };

  return (
    <Panel title={`启动前 ${p.window} 场爆量与突然拉高：落在量增的天还是量减的天`}
      note="母体＝启动事件（默认全部首板，不筛结果）；判据是日线代理（一天一个数）：倍量＝当日量 ÷ 上一根日线量（`vr-1` 那把尺，≥2.0 算倍量、1.8~2.0 算准倍量、≤0.5 算缩量），突拉＝当日后复权收益过那条线。天数、占比、中位数都由库里算好交出，这一屏只读 /api/quant/startup-spike-days，不新算判据，也不看分钟线。"
      slowHint="逐票逐日的倍量与突拉都在库里现数：第一发约 2~5 秒（近 6 个月、启动日前 30 场）"
      state={state} contract={
        <div className="text-[11px] leading-relaxed text-muted-foreground">
          <p>
            <b className="text-foreground">这一屏没拿到数据</b>：读的是 <code>/api/quant/startup-spike-days</code>。
            到底是「接口还没上」、「参数被拒」还是「库不通」，以上面那一行报错原文为准（那句是接口给的，不是这边猜的）——
            这里不替你断言状态码：把 422 说成 404，你就会去找一个根本不存在的「没接上」。
            改一下上面的控件、或点右上角「重算一次」。
          </p>
          <p className="mt-1">
            这一屏需要的字段（合同 <code>ssd-1</code>）：
            <code>joint</code>（倍量／准倍量／缩量各自的收涨收跌占比 + <code>dbl_total_days</code>）、
            <code>jumps</code>（突拉落在三类天的占比 + <code>jump_limit_share</code> +
            <code>jump_days_zero_events</code>／<code>jump_days_null_events</code>）、
            <code>buckets</code> 三档 × <code>base</code>、逐票 <code>rows</code>、
            <code>caveats</code>／<code>caliber</code>／<code>definitions</code>／<code>options</code>。
            缺哪一个就少画哪一张，格子上是 —，不拿别的数顶上。
          </p>
        </div>
      }
      controls={<div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <Select label="母体" value={p.cohort} options={cohorts}
          title={state.kind === "ok" ? state.data.definitions?.cohort : "母体换的是接口参数 cohort；默认那档「全部首板（不筛结果）」才是有区分度的那一档"}
          onChange={v => setP(s => ({ ...s, cohort: pick(v) }))} />
        <Select label="窗口月数" value={p.months} options={monthsOpts}
          title="只数最近这么多个月里启动的事件（1~12）"
          onChange={v => setP(s => ({ ...s, months: Number(pick(v)) }))} />
        <Select label="窗口根数" value={p.window} options={windowsOpts}
          title="启动日之前取几场：10/20/30。换根数以后每类天数都会跟着重算"
          onChange={v => setP(s => ({ ...s, window: Number(pick(v)) }))} />
        <Select label="突拉阈值" value={p.jumpPct} options={jumpOpts}
          title={state.kind === "ok" ? state.data.definitions?.jump_pct : "当日涨幅过这条线就算一次突然拉高（接口参数 jump_pct，单位 %）"}
          onChange={v => setP(s => ({ ...s, jumpPct: Number(pick(v)) }))} />
        <Select label="明细上限" value={p.limit} options={LIMITS.map(n => ({ value: n, label: `前 ${n} 行` }))}
          title="接口硬顶 600 行明细。触顶时明细那一张会写明这是截断，不是母体只有这么多"
          onChange={v => setP(s => ({ ...s, limit: Number(pick(v)) }))} />
        <button type="button" onClick={() => { clearAll(); setOpenId(null); refresh(); }}
          className="flex items-center gap-1 rounded border border-border/60 px-2 py-0.5 text-[11px] text-muted-foreground hover:border-primary hover:text-primary">
          <RefreshCw size={11} />重算一次
        </button>
      </div>}>
      {d => {
        // 后端打了 available:false 时 Panel 已经会把 reason 念在框外；这一句是 children 里的兜底，
        // 绝不用空表或假数据糊过去（展示规范 01 第一条：没数就说出「说不出」这件事）。
        if (d.available === false) {
          return <p className="text-[12px] leading-relaxed text-warning">
            {d.reason || "后端打了 available:false 却没给 reason —— 这一屏说不出原因，界面不猜。"}
          </p>;
        }
        const win = d.params?.window ?? p.window;
        // 母体以接口回显那一档为准（params.cohort）：界面 state 与后端真应用的值不一致时
        // （比如值被后端拒了退回默认），那句话必须跟着后端说，否则「≥4 板率」印不印百分比就成了界面猜
        const echoedCohort = d.params?.cohort ?? p.cohort;
        const jCols = jointColumns();
        const pCols = jumpColumns();
        const ansCols = answerColumns();
        const grpCols = groupColumns();
        const detCols = detailColumns();
        const jRows = jointSort.apply(jointRows(d.joint));
        const kRows = jumpSort.apply(jumpRows(d.jumps));
        const ansRows = answerRows(ansSort.apply(d.buckets ?? []), d.base);
        const rowsAll = d.rows ?? [];
        const sortedRows = grpSort.apply(rowsAll);
        const pages = Math.max(1, Math.ceil(sortedRows.length / PAGE));
        const cur = Math.min(Math.max(1, page), pages);
        const shown = sortedRows.slice((cur - 1) * PAGE, cur * PAGE);
        const truncated = !!d.params?.limit && rowsAll.length >= d.params.limit;
        const warn = versionWarning(d);
        const acct = ex5AccountText([...(d.buckets ?? []), ...(d.base ? [d.base] : [])], rowsAll);
        const def = (k: string) => d.definitions?.[k];

        return <>
          {/* 覆盖与不满窗那句必须在框外：它说的是"这一张表的样本从哪天起才有" */}
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{coverageSentence(d)}</p>
          {warn && <p className="mt-1 text-[11px] leading-relaxed text-warning">{warn}</p>}
          {/* 三条口径（日线代理 / 0 与 — / 超额剔除）都在框外：折进默认收起的框等于没写 */}
          <p className="mt-1 text-[11px] leading-relaxed text-foreground">{COPY.proxy}</p>
          <p className="mt-1 text-[11px] leading-relaxed text-foreground">{COPY.zeroOrNull}</p>
          <p className="mt-1 text-[11px] leading-relaxed text-foreground">{COPY.ex5}</p>
          {isPrescreenedCohort(echoedCohort) && (
            <p className="mt-1 text-[11px] leading-relaxed text-warning">
              当前母体是「{String(d.params?.cohort_text ?? echoedCohort)}」—— 这一档先按结果筛过，
              第三张表「≥4 板率」那一格就不印百分比（它只是把筛条件重复一遍）。
              要看出启动前的爆量与突拉是不是预示后来的结果，把母体换回「全部首板（不筛结果）」。
            </p>
          )}
          <p className="mt-1 text-[11px] leading-relaxed text-foreground">{jointHeadline(d.joint)}</p>
          <p className="mt-1 text-[11px] leading-relaxed text-foreground">{spikeHeadline(d.jumps)}</p>

          <h3 className="mt-3 text-[12px] font-bold">一、联合对照：倍量日里收涨的多还是收跌的多（旁边给全场合计天数）</h3>
          <div className="mt-1 overflow-x-auto">
            <table className="w-full border-collapse text-[11.5px]">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  {jCols.map(c => <Head key={c.key} col={c} def={def(c.key === "total_days" ? "dbl_total_days" : c.key)}
                    dir={jointSort.dirOf(c.key)} onSort={key => jointSort.toggle(key)} />)}
                </tr>
              </thead>
              <tbody>
                {jRows.map((r, i) => (
                  <tr key={`${r.kind}-${i}`} className="border-b border-border/40">
                    {jCols.map(c => (
                      <td key={c.key} className={cn("px-1 py-1 tabular-nums",
                        c.key === "label" && "whitespace-normal font-medium text-foreground",
                        c.key === "up_share" && "font-medium text-foreground",
                        c.key === "fields" && "whitespace-normal font-mono text-[10.5px] text-muted-foreground")}>
                        {jointCellText(c, r)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-1 text-[10.5px] leading-relaxed text-muted-foreground">
              这一张表是联合对照，不是两个独立天数：同一行里给「这类日子合计多少天」与「这些天里收涨／收跌的占比」。
              准倍量与缩量两类的合计天数后端没交（格子上是 {DASH}，不是 0 天）；
              收涨与收跌两个占比相加可以不满一，那是平盘与当天没收盘数的那些天。
            </p>
          </div>

          <h3 className="mt-3 text-[12px] font-bold">二、突然拉高落在哪一天：倍量日、准倍量日还是缩量日</h3>
          <p className="mt-1 text-[11px] leading-relaxed text-foreground">{jumpHeadline(d.jumps)}</p>
          <div className="mt-1 overflow-x-auto">
            <table className="w-full border-collapse text-[11.5px]">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  {pCols.map(c => <Head key={c.key} col={c} def={def(c.key)}
                    dir={jumpSort.dirOf(c.key)} onSort={key => jumpSort.toggle(key)} />)}
                </tr>
              </thead>
              <tbody>
                {kRows.map((r, i) => (
                  <tr key={`${r.field}-${i}`} className="border-b border-border/40">
                    {pCols.map(c => (
                      <td key={c.key} className={cn("px-1 py-1 tabular-nums",
                        c.key === "label" && "whitespace-nowrap font-medium text-foreground",
                        c.key === "share" && "font-medium text-foreground",
                        c.key === "field" && "font-mono text-[10.5px] text-muted-foreground",
                        c.key === "note" && "whitespace-normal text-[10.5px] text-muted-foreground")}>
                        {jumpCellText(c, r)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h3 className="mt-3 text-[12px] font-bold">三、分档 × 结果：突拉天数多的那批，后来走到几板、进场划不划算</h3>
          <div className="mt-1 overflow-x-auto">
            <table className="w-full border-collapse text-[11.5px]">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  {ansCols.map(c => <Head key={c.key} col={c} def={def(c.key)}
                    dir={ansSort.dirOf(c.key)} onSort={key => ansSort.toggle(key)} />)}
                </tr>
              </thead>
              <tbody>
                {ansRows.map((r, i) => {
                  const isBase = !!d.base && r === d.base;
                  return (
                    <tr key={`${r.label}-${i}`} className={cn("border-b border-border/40", isBase && "bg-muted/25")}>
                      {ansCols.map(c => (
                        <td key={c.key} className={cn("px-1 py-1 tabular-nums",
                          (c.key === "label" || c.key === "range") && "whitespace-nowrap",
                          c.key === "jump_days_med" && (r.jump_days_med === null ? "text-muted-foreground" : "text-foreground"),
                          c.key === "seg4_rate" && (isPrescreenedCohort(echoedCohort)
                            ? "whitespace-normal text-[10.5px] text-warning" : "font-medium text-foreground"))}>
                          {c.key === "label"
                            ? (isBase ? <b>全部母体事件（基线）</b> : <b>{r.label}</b>)
                            : answerCellText(c, r, echoedCohort)}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="mt-1 text-[10.5px] leading-relaxed text-muted-foreground">
              区间那一列把接口 <code>range</code> 的两个切点念出来了：切点按 <code>col &lt;= cut</code> 落地，
              所以是左开右闭（边界值只归上一档）。突拉天数那一列 — 与 0 是两件事（悬停各有一句）。
              「≥4 板率」在按结果筛过的那几档里上屏的是「{SEG4_PRESET_COPY}」这句话本身，不是百分比。
            </p>
            {acct && <p className="mt-1 text-[10.5px] leading-relaxed text-muted-foreground">{acct}</p>}
          </div>

          <h3 className="mt-3 text-[12px] font-bold">逐票的窗口天数账（展开看收涨收跌怎么摊在倍量／准倍量／缩量上）</h3>
          {/* 「这一张表怎么点」那句是描述，已收进本块下面那个默认收起的说明框
              （用户 2026-10-21：「把页面上的描述性内容全部收缩起来，避免过多占屏」）；
              这里只剩弹窗被拦那句**告警**，它不折 */}
          {!!popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{popHint}</p>}
          {truncated && (
            <p className="mt-1 text-[11px] leading-relaxed text-warning">
              明细已触上限：这一轮接口交的就是 <code>limit={intText(d.params?.limit)}</code> 行，
              下面是被截断后的前若干条事件，不是母体只有这么多。
            </p>
          )}
          <div className="mt-1 overflow-x-auto">
            <table className="w-full border-collapse text-[11.5px]">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="px-1 py-1 font-normal" title="展开看这一条事件逐日那些天数的全账">展开</th>
                  {grpCols.map(c => <Head key={c.key} col={c} def={def(c.key)}
                    dir={grpSort.dirOf(c.key)} onSort={key => { clearAll(); grpSort.toggle(key); }} />)}
                </tr>
              </thead>
              <tbody>
                {shown.map(r => {
                  const id = `${String(r.code ?? "")}|${String(r.launch_date ?? "")}`;
                  const short = windowShort(r.bars, win) === true;
                  const isOpen = openId === id;
                  return (
                    <EventRow key={id} r={r} short={short} win={win} isOpen={isOpen}
                      grpCols={grpCols} detCols={detCols} def={def} openReview={openReview}
                      onToggle={() => setOpenId(isOpen ? null : id)} />
                  );
                })}
              </tbody>
            </table>
            {!rowsAll.length && (
              <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                接口交出 0 行明细 —— 这一档母体在当前窗口里没有事件，不是接口坏了，也不是库里没数。
              </p>
            )}
          </div>
          {rowsAll.length > PAGE && (
            <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
              <span>共 {rowsAll.length} 条事件 · 每页 {PAGE} 条，现在是第 {cur}/{pages} 页</span>
              <button type="button" disabled={cur <= 1} onClick={() => setPage(cur - 1)}
                className="rounded border border-border/60 px-1.5 py-0.5 disabled:opacity-40">上一页</button>
              <button type="button" disabled={cur >= pages} onClick={() => setPage(cur + 1)}
                className="rounded border border-border/60 px-1.5 py-0.5 disabled:opacity-40">下一页</button>
              <span>翻页不重新发请求：明细是一次取回来的，翻页只是分开画。</span>
            </div>
          )}

          <Caveats items={caveatItems(d.caveats)} />
          <Caliber text={`这一块的取数口径（接口原句，页面未改写）：${d.caliber ?? "接口没交 caliber —— 这一屏没有出处可念"}`} />
          <Definitions map={d.definitions} />
          <ExplainBox title="这一块与上面三屏的分工 · 两种爆量的分别 · 这一张表怎么点">
            <p>上面「启动前量能画像」读的是量的形状（倍量天数、离 MA60 多远），「首板形状预示」读的是形状与段长的关系，
              「启动前象限轨迹」读的是名次的走向；这一块读的是<b>量与价在同一天的联合</b>：
              倍量那天到底收涨还是收跌，突然拉高那种日子摊在倍量、准倍量还是缩量上。</p>
            <p className="mt-1">「爆量」在这两个屏上是两套判据，不共用一个数：这一屏是<b>日线代理</b>
              （当日成交量对着这只票自己的上一根日线量算一次倍数＝vr-1 那把尺，一天一个数）；盘中那种几分钟内连续成交打上来的爆量与急拉
              在「异动筛查」那一屏（分钟线与逐笔）。分钟线这一轮没做进这一块，所以两边的数不对表，也不要拿这一块
              去证明「盘中是急拉」。</p>
            <p className="mt-1 text-muted-foreground">天数、占比、中位数与分档都由库里现算；界面只排版，
              不重算任何一个数。每列含义悬停即读接口原文（下方「这些字段是什么意思」）。</p>
            <p className="mt-1 text-muted-foreground">不满窗（窗口根数不够）的那些行仍进后端的中位数聚合，
              界面整行标灰；超额读不出的行从分母里剔掉并点名条数 —— 上面那三句口径写在框外，不在这里重复。</p>
            {/* 「这一张表怎么点」那句原先裸摊在明细表上面（用户 2026-10-21 点名把描述性内容收起来）；
                全站一份的 `STOCK_ROW_HINT` 原句未改，只是搬进这个默认收起的框，
                上面那条「弹窗被拦」的告警仍留在框外。 */}
            <p className="mt-1">{STOCK_ROW_HINT}</p>
          </ExplainBox>
        </>;
      }}
    </Panel>
  );
}

/**
 * 明细的一行 = 一只票的一次启动（接口 `rows` 里就是逐事件一行），展开以后是那些天数的全账。
 *
 * 双击一行开「个股深度复盘」走全站那一份 `lib/useStockRowInteraction.ts`（地址不在这里拼）；
 * 这一块**不接交叉高亮**：三张聚合表没有行号可记，硬接会让不相干的两块一起亮。
 */
function EventRow({ r, short, win, isOpen, grpCols, detCols, def, openReview, onToggle }: {
  r: SsdRow; short: boolean; win: number; isOpen: boolean;
  grpCols: ReturnType<typeof groupColumns>; detCols: ReturnType<typeof detailColumns>;
  def: (k: string) => string | undefined;
  openReview: (code: string | null | undefined) => void;
  onToggle: () => void;
}) {
  return (
    <>
      <tr onDoubleClick={() => openReview(r.code)}
        title={short ? shortTitle(r.bars, win) : undefined}
        className={cn("cursor-pointer border-b border-border/40",
          short ? "bg-muted/30 text-muted-foreground" : "hover:bg-primary/5")}>
        <td className="px-1 py-1">
          <button type="button" onClick={onToggle} aria-expanded={isOpen}
            title={isOpen ? "收起这一条事件的逐日天数账" : "展开看这一条事件逐日那些天数"}
            className="rounded border border-border/60 px-1 text-[10px] text-muted-foreground hover:border-primary hover:text-primary">
            {isOpen ? "收起" : "展开"}
          </button>
        </td>
        {grpCols.map(c => (
          <td key={c.key} title={groupCellTitleSafe(c, r, def)}
            className={cn("px-1 py-1 tabular-nums",
              (c.key === "code" || c.key === "launch_date") && "whitespace-nowrap",
              c.key === "code" && "font-mono",
              c.key === "outcome_r5" && pctColor((r.outcome_r5 ?? null) as number | null))}>
            {groupCellText(c, r)}
          </td>
        ))}
      </tr>
      {isOpen && (
        <tr className="border-b border-border/40 bg-muted/10">
          <td colSpan={grpCols.length + 1} className="px-2 py-1.5">
            <p className="text-[10.5px] leading-relaxed text-muted-foreground">
              {String(r.code ?? DASH)} {String(r.name ?? "")}　启动日 {String(r.launch_date ?? DASH)}
              　母体 {String(r.cohort ?? DASH)}｜窗口 {win} 场、它真有 {intText(r.bars)} 场
              {short ? "（不满窗：天数与满窗的行不是同一跨度，不与满窗并排读强弱）" : ""}。
              下面每一格都是这一条事件窗口里的天数：收涨/收跌/平盘/没收盘数四类相加应等于实有根数，
              倍量/准倍量/缩量是另一条切分（同一天的量类只归一类）。
              首场倍量隔 {gapText(r.first_dbl_gap)}、首场突拉隔 {gapText(r.first_jump_gap)}。
            </p>
            <table className="mt-1 w-full border-collapse text-[10.5px]">
              <thead>
                <tr className="text-left text-muted-foreground">
                  {detCols.map(c => (
                    <th key={c.key} className="whitespace-normal px-1 py-0.5 font-normal"
                      title={[c.title, def(c.key) && `接口 definitions 里 ${c.key} 的原文：${def(c.key)}`].filter(Boolean).join("　")}>
                      {c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  {detCols.map(c => (
                    <td key={c.key} className={cn("px-1 py-0.5 tabular-nums",
                      c.key === "jump_days" && r.jump_days === null && "text-muted-foreground")}
                      title={c.key === "jump_days" ? jumpDaysMeaning(r.jump_days) : undefined}>
                      {detailCellText(c, r)}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  );
}

/** 明细那一格的悬停：判读由 lib 交（0 与 — 各一句、超额缺数那句），组件只把接口原文拼在后面。 */
function groupCellTitleSafe(col: SsdColumn, r: SsdRow, def: (k: string) => string | undefined): string | undefined {
  const d = def(col.key);
  return [groupCellTitle(col, r), d && `接口 definitions 里 ${col.key} 的原文：${d}`]
    .filter(Boolean).join("　") || undefined;
}
