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
  COPY, COHORTS_FALLBACK, DASH, LIMITS, MONTHS_FALLBACK, RANK_DELTA_HEADER, SEG4_PRESET_COPY,
  WINDOWS_FALLBACK, answerCellText, answerColumns, answerRows, caveatItems, coverageSentence,
  detailCellText, detailColumns, ex5GapText, familyHasQuadrant, groupCellText,
  groupColumns, groupRows, intText, isPrescreenedCohort, keyCellText, keyColumns, keyHeadline,
  keyOptionsFor, noQuadrantNote,
  optionsFor, queryParams, rangeText, shortTitle, versionWarning,
  type SqOption, type SqPayload,
} from "@/lib/quadrantTrack";

/**
 * 倍量形态页 →「启动前 30 场四维象限轨迹」（挂在「启动前量能画像」那一屏下面）。
 *
 * 上面两屏答的是"启动前长什么样"（倍量天数、离 MA60 多远）与"首板形状能不能预示段长"。
 * 这一块答的是**走向**：启动日之前那 N 场里，这票在七个分量上的全市场名次是往前列走还是往后列走，
 * 走到 T−1 那场时它站在哪个象限、窗口里翻转过几次 —— 名次与象限都是库里算好的，
 * 这一屏只读 `analytics.score`，不新算判据。
 *
 * 母体默认是 **`anyfirst`＝全部首板（不筛结果）**：另外四档（board4 / board4_45 /
 * board4_or_gain45 / jump40）都是**先按结果筛过**再回头看启动前，那四档里「≥4 板率」这一格
 * 只是把筛条件重复一遍（恒等于母体定义），读不出"启动前的走向预示后来走成 4 板"这一问。
 * 所以这一格在那些档上屏的是那句话本身、不是一个像结论的百分比（判读在 `seg4CellText`）。
 *
 * 五条界面上不许糊掉的线（判读都在 `lib/quadrantTrack.ts`，这里只排版）：
 *  · 窗口是**启动日之前** N 个有效交易日，不含启动日当天（当天已涨停，分数被启动本身污染），
 *    所以「象限分布」那一格读的是 T−1 那天，不是启动日那天；
 *  · `rank_delta = rank_first − rank_last`，rank 越小越靠前 ⇒ **正数＝名次上升**，列头直接这么写；
 *  · `quad_*` 只有 oper_score 那一族有值，其余六族是 null ⇒ 格子里是 `—`，
 *    显示 0 会被读成"翻转过 0 次"（那是一条实质结论，且与真意相反）；
 *  · 明细那一列「前 4 日涨幅」后端交的是**小数比例**（0.4626＝+46.26%），走 `ratioText` 乘回一百，
 *    走 `rateText` 会把 +46.26% 印成 `0.5%`（屏上实测错过的那次就是这么错的）；
 *  · 判据表只有 2026-01-27 起有数 ⇒ 更早启动的事件必然不满 N 根：不满窗的仍进后端中位数聚合，
 *    界面整行标灰并点名条数（那是覆盖，不是缺数）。
 *
 * 所有参数都走接口（名次、象限、中位数、分档都在库里算）：改一个控件就重发一次请求，界面不做二次计算。
 */

const DEFAULTS = { cohort: "anyfirst", months: 6, window: 30, key: "", limit: 400 };

/** 明细一页几个母体事件（一次最多 600 条明细行，摊开七族会很长；分页不重新发请求）。 */
const PAGE = 40;

function Select({ label, value, onChange, options, title }: {
  label: string; value: string | number; onChange: (v: string) => void;
  options: readonly SqOption[]; title?: string;
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

/**
 * 表头格：可排序的那几列走 `SortHeader`，`sortKey` 就是那一格真正读的字段名（写错了不报错、
 * 只是点了不换序 —— 本项目踩过 `sortKey="sector"` 那一类坑）。
 * 象限分布那两列印的是分布不是标量，老老实实做普通表头：按它排序没有意义。
 */
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

export function StartupQuadrantTrackBoard() {
  const [p, setP] = useState(DEFAULTS);
  const [nonce, refresh] = useNonce();
  const [openId, setOpenId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const state = useProbe<SqPayload>("/quant/startup-quadrant-track", nonce, queryParams(p));
  const keySort = useTableSort();
  const ansSort = useTableSort();
  const grpSort = useTableSort();
  /** 明细是「一只票一行」，所以单击交叉高亮与双击开深度复盘都用全站那一份实现，不自己拼地址 */
  const x = useStockRowInteraction("启动前象限轨迹");

  const cohorts = optionsFor(state.kind === "ok" ? state.data.options?.cohorts : null, COHORTS_FALLBACK);
  const monthsOpts = optionsFor(state.kind === "ok" ? state.data.options?.months : null, MONTHS_FALLBACK);
  const windowsOpts = optionsFor(state.kind === "ok" ? state.data.options?.windows : null, WINDOWS_FALLBACK);
  const keyOpts = keyOptionsFor(state.kind === "ok" ? state.data.options?.keys : null);
  /** 换控件＝换一次取数：参数进 query，顺手清掉高亮与展开态（行号会跟着漂到别的票身上） */
  const pick = (v: string) => { x.clearAll(); setOpenId(null); setPage(1); return v; };

  return (
    <Panel title={`启动前 ${p.window} 场四维象限轨迹：七个分量的名次走向`}
      note="母体＝启动事件（五档母体切换，默认「全部首板（不筛结果）」）；每一行读的是它启动日之前那一串有效交易日里、该分量在全市场的名次怎么走。名次、象限、中位数都由库里的打分表现算现交，这一屏不新算判据。"
      slowHint="七族各自的名次轨迹与中位数都在库里现算：第一发约 2~5 秒（近 6 个月、启动日前 30 场）"
      state={state} contract="/api/quant/startup-quadrant-track"
      controls={<div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <Select label="母体" value={p.cohort} options={cohorts}
          title={state.kind === "ok" ? state.data.definitions?.cohort : "五档母体换的是接口参数 cohort，榜与聚合都跟着重算；默认那档「全部首板（不筛结果）」才是有区分度的那一档"}
          onChange={v => setP(s => ({ ...s, cohort: pick(v) }))} />
        <Select label="窗口月数" value={p.months} options={monthsOpts}
          title="只数最近这么多个月里启动的事件（1~12）"
          onChange={v => setP(s => ({ ...s, months: Number(pick(v)) }))} />
        <Select label="窗口根数" value={p.window} options={windowsOpts}
          title="启动日之前取几场：10/20/30。换根数之后同一族的满窗条数会变"
          onChange={v => setP(s => ({ ...s, window: Number(pick(v)) }))} />
        <Select label="分量键" value={p.key} options={keyOpts}
          title={state.kind === "ok" ? state.data.definitions?.key : "七族全选时表一逐族给一行；只看一族时明细每票一行"}
          onChange={v => setP(s => ({ ...s, key: pick(v) }))} />
        <Select label="明细上限" value={p.limit} options={LIMITS.map(n => ({ value: n, label: `前 ${n} 行` }))}
          title="接口硬顶 600 行明细。触顶时明细那一张会写明这是截断，不是母体只有这么多"
          onChange={v => setP(s => ({ ...s, limit: Number(pick(v)) }))} />
        <button type="button" onClick={() => { x.clearAll(); setOpenId(null); refresh(); }}
          className="flex items-center gap-1 rounded border border-border/60 px-2 py-0.5 text-[11px] text-muted-foreground hover:border-primary hover:text-primary">
          <RefreshCw size={11} />重算一次
        </button>
      </div>}>
      {d => {
        // 后端打了 available:false 时 Panel 已经会把 reason 念在框外；这一句是 children 里的兜底，
        // 绝不用空表或假数据糊过去（展示规范 01 第一条：没数就说不出的那件事）。
        if (d.available === false) {
          return <p className="text-[12px] leading-relaxed text-warning">
            {d.reason || "后端打了 available:false 却没给 reason —— 这一屏说不出原因，界面不猜。"}
          </p>;
        }
        const win = d.params?.window ?? p.window;
        /** 母体以**接口回显的那一档**为准（`params.cohort`）：界面自己的 state 与后端真应用的
         *  值不一致时（比如值被后端拒了退回默认），那句话必须跟着后端说，否则「≥4 板率」这一格
         *  到底印不印百分比就成了界面自己猜。回显缺失才退回界面 state。 */
        const echoedCohort = d.params?.cohort ?? p.cohort;
        const keyCols = keyColumns();
        const ansCols = answerColumns();
        const grpCols = groupColumns();
        const detCols = detailColumns();
        const keys = keySort.apply(d.keys ?? []);
        const ansRows = answerRows(ansSort.apply(d.buckets ?? []), d.base);
        const groups = grpSort.apply(groupRows(d.rows, win));
        const pages = Math.max(1, Math.ceil(groups.length / PAGE));
        const cur = Math.min(Math.max(1, page), pages);
        const shown = groups.slice((cur - 1) * PAGE, cur * PAGE)
          .map((g, i) => ({ g, gi: (cur - 1) * PAGE + i }));
        const truncated = !!d.params?.limit && (d.rows ?? []).length >= d.params.limit;
        const warn = versionWarning(d);
        const gap = ex5GapText([...(d.buckets ?? []), ...(d.base ? [d.base] : [])], d.rows);
        const def = (k: string) => d.definitions?.[k];

        return <>
          {/* 覆盖与不满窗那句必须在框外：它说的是"这一张表的样本从哪天起才有" */}
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{coverageSentence(d)}</p>
          {warn && <p className="mt-1 text-[11px] leading-relaxed text-warning">{warn}</p>}
          <p className="mt-1 text-[11px] leading-relaxed text-foreground">{COPY.window}　{COPY.rankDelta}</p>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{COPY.coverage}</p>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{COPY.moodFamily}　{COPY.volStage}</p>
          {/* 母体这一档决定「≥4 板率」读不读得出来，所以这句在框外：藏在折叠框里等于没写 */}
          <p className="mt-1 text-[11px] leading-relaxed text-foreground">{COPY.cohort}</p>
          {isPrescreenedCohort(echoedCohort) && (
            <p className="mt-1 text-[11px] leading-relaxed text-warning">
              当前母体是「{String(d.params?.cohort_text ?? echoedCohort)}」—— 这一档先按结果筛过，
              下面那张表「≥4 板率」那一格就不印百分比（它只是把筛条件重复一遍）。
              要看出启动前走向与后来结果的分别，把母体换回「全部首板（不筛结果）」。
            </p>
          )}
          <p className="mt-1 text-[11px] leading-relaxed text-foreground">{keyHeadline(d.keys)}</p>

          <div className="mt-2 overflow-x-auto">
            <table className="w-full border-collapse text-[11.5px]">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  {keyCols.map(c => <Head key={c.key} col={c} def={def(c.key)}
                    dir={keySort.dirOf(c.key)} onSort={key => keySort.toggle(key)} />)}
                </tr>
              </thead>
              <tbody>
                {keys.map((r, i) => (
                  <tr key={`${r.key}-${i}`} className="border-b border-border/40">
                    {keyCols.map(c => {
                      const isQuad = c.key.startsWith("quad_");
                      const noQuad = isQuad && !familyHasQuadrant(r);
                      return (
                        <td key={c.key}
                          title={noQuad ? noQuadrantNote(r) : undefined}
                          className={cn("px-1 py-1 tabular-nums",
                            c.key === "label" && "whitespace-nowrap font-medium text-foreground",
                            c.key === "rank_delta_med" && pctColor(r.rank_delta_med ?? null),
                            noQuad && "text-muted-foreground/70")}>
                          {keyCellText(c, r)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-1 text-[10.5px] leading-relaxed text-muted-foreground">
              表一「名次变化」那一列的读法：{RANK_DELTA_HEADER}。象限那四列只有 oper_score 那一族有值，
              其余六族接口交的是 null，格子里就是 {DASH}（不是 0）。
            </p>
          </div>

          <h3 className="mt-3 text-[12px] font-bold">答案：名次往前列走的那批，后来走到几板、进场划不划算</h3>
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
                          c.key === "label" && "whitespace-nowrap",
                          c.key === "seg4_rate" && (isPrescreenedCohort(echoedCohort)
                            ? "whitespace-normal text-[10.5px] text-warning"
                            : "font-medium text-foreground"))}>
                          {c.key === "label"
                            ? (isBase ? <b>全部母体事件（基线）</b> : <b>{r.label} <span className="text-muted-foreground">{rangeText(r)}</span></b>)
                            : answerCellText(c, r, echoedCohort)}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="mt-1 text-[10.5px] leading-relaxed text-muted-foreground">
              这一张表里「≥4 板率」的读法分两种：母体是「全部首板（不筛结果）」时它才是一个结果读数；
              母体换成按结果筛过的那四档时，这一格上屏的是「{SEG4_PRESET_COPY}」那句话本身，
              不是一个像结论的百分比。段长与收益那四列（走到几板、进场后 5 场、超额、胜率）在两种母体下都是结果。
            </p>
            {gap && <p className="mt-1 text-[10.5px] leading-relaxed text-muted-foreground">{gap}</p>}
          </div>

          <h3 className="mt-3 text-[12px] font-bold">逐票逐分量的起止名次与象限</h3>
          {/* `popHint` 是弹窗被拦的**告警**，留在屏上单独念；「这一张表怎么点」那句是描述，
              已收进本块下面那个默认收起的说明框（用户 2026-10-21：
              「把页面上的描述性内容全部收缩起来，避免过多占屏」） */}
          {!!x.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{x.popHint}</p>}
          {/* 「前 4 日涨幅」的量纲必须写在框外：接口交的是小数比例，界面乘回一百才是屏上那个数 */}
          <p className="mt-1 text-[11px] leading-relaxed text-foreground">
            明细里「前 4 日涨幅」这一列后端交的是小数比例（definitions 原句：0.4641＝+46.41%），
            这一屏把它乘回一百来画 —— 屏上的 46.3% 与库里的 0.4626 是同一个数；
            缺的那一格是 {DASH}，不是 0.0%（0.0% 是「一分钱没涨」这条实质结论）。
          </p>
          {truncated && (
            <p className="mt-1 text-[11px] leading-relaxed text-warning">
              明细已触上限：这一轮接口交的就是 <code>limit={intText(d.params?.limit)}</code> 行，
              下面是被截断后的前若干只母体事件，不是母体只有这么多。
            </p>
          )}
          <div className="mt-1 overflow-x-auto">
            <table className="w-full border-collapse text-[11.5px]">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="px-1 py-1 font-normal" title="展开看这一只票每一分量的起止名次与象限">展开</th>
                  {grpCols.map(c => <Head key={c.key} col={c} def={def(c.key)}
                    dir={grpSort.dirOf(c.key)}
                    onSort={key => { x.clearAll(); grpSort.toggle(key); }} />)}
                </tr>
              </thead>
              <tbody>
                {shown.map(({ g, gi }) => {
                  const id = `${String(g.code ?? "")}|${String(g.launch_date ?? "")}`;
                  const short = g.window_short === true;
                  const isOpen = openId === id;
                  return (
                    <GroupRow key={id} g={g} gi={gi} short={short} win={win} isOpen={isOpen}
                      grpCols={grpCols} detCols={detCols} def={def} x={x}
                      onToggle={() => setOpenId(isOpen ? null : id)} />
                  );
                })}
              </tbody>
            </table>
            {!groups.length && (
              <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                接口交出 0 行明细 —— 这一档母体在当前窗口里没有事件，不是接口坏了，也不是库里没数。
              </p>
            )}
          </div>
          {groups.length > PAGE && (
            <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
              <span>共 {groups.length} 个母体事件 · 每页 {PAGE} 个，现在是第 {cur}/{pages} 页</span>
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
          <ExplainBox title="这一块与上面两屏的分工 · 象限那几列要怎么读 · 这一张表怎么点">
            <p>上面「启动前量能分布」读的是量（倍量天数、离 MA60 多远），「首板形状预示」读的是形状与段长的关系；
              这一块读的是<b>位置的变化</b>：同一只票在窗口第一场与最后一场（T−1）各自的全市场名次，
              以及 oper_score 那一族在窗口里换过几次象限。</p>
            <p className="mt-1">窗口刻意不含启动日当天（当天已经涨停，分数被启动本身污染），
              所以「象限分布」那一格说的是 T−1 那天，不是启动日那天；要看启动日当天的象限去「四维象限」那一屏。</p>
            <p className="mt-1 text-muted-foreground">market_mood 这一族故意不在表里 —— 同一天全市场是同一个数，
              它的名次没有横截面意义。vol_stage 的值是档位 1~5，不是百分比。</p>
            <p className="mt-1 text-muted-foreground">名次中位数、分档与占比都由库里现算；界面只排版，
              不重算任何一个中位数。每列含义悬停即读接口原文（下方「这些字段是什么意思」）。</p>
            {/* 「这一张表怎么点」那句原先裸摊在明细表上面（用户 2026-10-21 点名要把描述性内容收起来）；
                它是全站一份的 `STOCK_ROW_HINT`，原句未改，只是搬进这个默认收起的框。
                上面那条「弹窗被拦」的告警是告警，留在框外。 */}
            <p className="mt-1">{STOCK_ROW_HINT}</p>
          </ExplainBox>
        </>;
      }}
    </Panel>
  );
}

/**
 * 明细的一行 = 一个母体事件（一只票的一次启动），展开以后是它逐分量的起止名次与象限。
 *
 * 交互（单击交叉高亮、双击开个股深度复盘、弹窗被拦的告警）全部走全站那一份
 * `lib/useStockRowInteraction.ts`，这里只把行号与列键交进去 —— 行号用整批的序号 `gi`，
 * 不然翻到第 2 页时第 1 页的同一行会跟着亮。
 */
function GroupRow({ g, gi, short, win, isOpen, grpCols, detCols, def, x, onToggle }: {
  g: ReturnType<typeof groupRows>[number]; gi: number; short: boolean; win: number; isOpen: boolean;
  grpCols: ReturnType<typeof groupColumns>; detCols: ReturnType<typeof detailColumns>;
  def: (k: string) => string | undefined;
  x: ReturnType<typeof useStockRowInteraction>;
  onToggle: () => void;
}) {
  return (
    <>
      <tr {...x.rowProps(gi, g.code, short ? "bg-muted/30 text-muted-foreground" : "hover:bg-primary/5",
        short ? shortTitle(g.bars, win) : undefined)}>
        <td className="px-1 py-1">
          <button type="button" onClick={onToggle} aria-expanded={isOpen}
            title={isOpen ? "收起这一只票的逐分量起止" : "展开看这一只票每一分量的起止名次与象限"}
            className="rounded border border-border/60 px-1 text-[10px] text-muted-foreground hover:border-primary hover:text-primary">
            {isOpen ? "收起" : `展开 ${g.key_count} 族`}
          </button>
        </td>
        {grpCols.map(c => {
          const cp = x.cellProps(gi, c.key);
          return (
            <td key={c.key} {...cp}
              title={c.key === "quad_switches" && g.quad_switches === null
                ? "这一行没有象限读数（象限只由 oper_score 那一族给）：— 不是翻转过 0 次" : undefined}
              className={cn(cp.className,
                (c.key === "rank_delta_best" || c.key === "rank_delta_worst")
                && pctColor((g[c.key] as number | null) ?? null))}>
              {groupCellText(c, g)}
            </td>
          );
        })}
      </tr>
      {isOpen && (
        <tr className="border-b border-border/40 bg-muted/10">
          <td colSpan={grpCols.length + 1} className="px-2 py-1.5">
            <p className="text-[10.5px] leading-relaxed text-muted-foreground">
              {String(g.code ?? DASH)} {String(g.name ?? "")}　启动日 {String(g.launch_date ?? DASH)}
              　母体 {String(g.cohort ?? DASH)}｜窗口 {win} 场、它真有 {intText(g.bars)} 场
              {short ? "（不满窗：起止不是同一跨度的两点，不与满窗的票并排读强弱）" : ""}。
              下面每行是一个分量；名次三列里正数＝上升。
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
                {g.rows.map((r, i) => {
                  const noQuad = r.quad_switches === null && r.quad_first === null && r.quad_last === null;
                  return (
                    <tr key={`${String(r.capability_key)}-${i}`} className="border-t border-border/30">
                      {detCols.map(c => (
                        <td key={c.key} className={cn("px-1 py-0.5 tabular-nums",
                          c.key === "rank_delta" && pctColor(r.rank_delta ?? null),
                          c.key.startsWith("quad_") && noQuad && "text-muted-foreground/70")}
                          title={c.key.startsWith("quad_") && noQuad
                            ? "这一族没有象限读数：格子里是 —，不是 0" : undefined}>
                          {detailCellText(c, r)}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  );
}
