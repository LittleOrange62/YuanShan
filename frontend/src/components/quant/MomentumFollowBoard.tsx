import { useState } from "react";
import { CaliberFold, ExplainBox, Panel, Row } from "@/components/ui/DataPanel";
import { SortHeader } from "@/components/ui/SortHeader";
import { StockLink } from "@/components/ui/StockLink";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { dash, type DataState } from "@/lib/datastate";
import { plain } from "@/lib/agent";
import { pct } from "@/lib/qd";
import { pctColor } from "@/lib/colors";
import { rankText, rankTitle, trimNum } from "@/lib/ranking";
import { METRIC_BUTTONS, LIMIT_OPTIONS } from "@/lib/steadyRank";
import {
  fracText, hitText, nullTally, parentTruncationSentence, windowSentence,
  type FollowData, type FollowMetric,
} from "@/lib/momentumFollow";
import { cn } from "@/lib/utils";

/**
 * 上一场名次稳步前进榜 · 后续表现（`GET /api/quant/momentum-follow`，用户 2026-10-20 凌晨点名：
 * 「昨日前 80 名的进入股价表现（把各种名次都显示出来，包括最近倍量名次，突破信号等等）」）。
 *
 * 这块答的问题：上一场那张稳步前进榜交出的前 N 名，从那天之后股价走没走出来，外加母体场次
 * 当天各路判据各自的名次/在榜。三个形状上的决定都随接口交出、这里只转述：
 *  · **母体**是「上一场」那张榜（接口 `parent_date`，默认基准日＝库里最新有名次场次的前一场 ——
 *    要有「母体之后第一场」才算得出后续表现，那句在接口 caveats 里）；
 *  · 表现只有「今日涨跌 + 近 5 日涨跌」两栏，**不逐日全铺**（这张榜每天换人，逐日铺是几百列；
 *    逐日那一族是倍量页的「进榜后逐日涨跌幅」，两个问题、两个形状，不合并）。
 *    2026-10-27 用户点名：旧名「次日涨跌」是错的 —— 母体是前一场，它之后的第一场就是今天；
 *    「至今累计」换成「近 5 日」（读至日往回 5 场，与母体日无关，两列不许相减）；
 *  · 档位与上面那张榜**共享同一个状态**（页面把 `metric`/`limit` 交进来）：切上面那张，这块的母体跟着换，
 *    不会出现「上面看总分、下面追 xy」的错配。
 *
 * 格子的三条红线（每一格都按这个长）：缺任何一边是 —，**0 与「不在」都是实质读数**；
 * 名次列的规则原文与分母都走 `lib/ranking.ts` 那两份（`rank.rule` 是后端 `ranking.RULE_NOTE`
 * 对象本身，页面一个字不抄）；埋伏那一列是**榜内**名次（那张表只落命中的票），列名与悬停
 * 都点破它与全场名次不是同一把尺。
 */

/** 列的一份清单：表头与格子从同一个数组走（两处各写一份就会错位，全站栽过一次）。 */
const COLS: [string, string][] = [
  ["board_rank", "榜内"],
  ["code", "代码"],
  ["name", "名称"],
  ["latest_series", "昨日名次"],
  ["gain_frac", "前进幅度"],
  ["chg_next_day", "今日涨跌"],
  ["chg_5d", "近 5 日涨跌"],
  ["bars_after", "期间场数"],
  ["oper_rank", "综合分名次"],
  ["dbl_rank", "倍量名次"],
  ["dbl_energy", "倍量分"],
  ["quadrant", "档"],
  ["breakout_type_label", "突破信号"],
  ["ambush_rank_in_board", "埋伏榜内"],
  ["abnormal_hit", "异动母体"],
  ["trend_selected5", "趋势5场"],
  ["trend_selected10", "趋势10场"],
  ["oper_value", "oper"],
];

export function MomentumFollowBoard({ state, metric, onMetricChange, limit, onLimitChange, onRefresh }: {
  state: DataState<FollowData>;
  metric: FollowMetric;
  onMetricChange: (m: FollowMetric) => void;
  limit: number;
  onLimitChange: (n: number) => void;
  onRefresh?: () => void;
}) {
  const srt = useTableSort();
  const x = useStockRowInteraction("稳步前进榜后续表现");
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(1);
  /** 换档/换前 N/换每页：清行选中并回第 1 页（与上面那张榜同一条规矩；行号记的是渲染数组）。 */
  const pick = (fn: () => void) => { x.clearAll(); setPage(1); fn(); };

  return (
    <Panel title="上一场稳步前进榜前 N 名 · 今日与近 5 日股价表现（母体档位跟着上面那张榜）" state={state}
      slowHint="母体那张榜本身要现算（近 6 场全库聚合），再加六族名次/在榜与一只票一发的后复权行情：实测（2026-10-20）首次约 19 秒，窗口与覆盖命中缓存后仍要逐族现读。"
      controls={
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          <span className="text-muted-foreground">母体读哪一列名次（与上面那张榜同一个状态）</span>
          {METRIC_BUTTONS.map(b => (
            <button key={b.key} type="button" aria-pressed={metric === b.key}
              onClick={() => pick(() => onMetricChange(b.key))}
              className={cn("rounded-lg border px-2 py-0.5", metric === b.key
                ? "border-primary/70 bg-primary/10 font-medium text-primary"
                : "border-border hover:bg-muted/50")}>{b.label}</button>
          ))}
          <span className="ml-2 text-muted-foreground">前 N</span>
          {LIMIT_OPTIONS.map(n => (
            <button key={n} type="button" aria-pressed={limit === n}
              onClick={() => pick(() => onLimitChange(n))}
              className={cn("rounded-lg border px-2 py-0.5", limit === n
                ? "border-primary/70 bg-primary/10 font-medium text-primary"
                : "border-border hover:bg-muted/50")}>{n}</button>
          ))}
          <span className="ml-2 text-muted-foreground">每页</span>
          {[25, 50].map(n => (
            <button key={n} type="button" aria-pressed={pageSize === n}
              onClick={() => pick(() => setPageSize(n))}
              className={cn("rounded-lg border px-2 py-0.5", pageSize === n
                ? "border-primary/70 bg-primary/10 font-medium text-primary"
                : "border-border hover:bg-muted/50")}>{n} 行</button>
          ))}
          {onRefresh && (
            <button type="button" onClick={() => { x.clearAll(); onRefresh(); }}
              className="ml-2 rounded-lg bg-primary/15 px-2 py-0.5 font-medium text-primary hover:bg-primary/25">
              清缓存重算
            </button>
          )}
        </div>
      }
      note="两栏涨跌幅都是后复权（尺子只住 day_returns 一处）：「今日涨跌」看母体之后的那一场，「近 5 日涨跌」看读至日往回 5 个市场场次，起点不同、不许相减；各路名次读的是母体场次当天各表已落好的行，这一屏不重算任何判据。">
      {d => {
        const rows = d.rows ?? [];
        const pages = Math.max(1, Math.ceil(rows.length / pageSize));
        const cur = Math.min(Math.max(1, page), pages);
        const shown = srt.apply(rows).slice((cur - 1) * pageSize, cur * pageSize);
        const th = (k: string) => d.definitions?.[k] ?? "这一列的口径文字后端没交，页面不自己编";
        const ruleText = plain(d.rank?.rule ?? "");
        const uni = d.rank?.universe ?? null;
        const missNext = nullTally(rows, "chg_next_day");
        const miss5 = nullTally(rows, "chg_5d");
        const missDbl = nullTally(rows, "dbl_rank");
        return (
          <>
            <Row label="看的是哪几天" value={windowSentence(d)} />
            {/* 截断声明、— 与 0 的分工、空栏计数：三条都不许折进说明框（藏起来等于让屏幕说假话） */}
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
              {parentTruncationSentence(d)}
            </p>
            <p className="mt-0.5 text-[11px] leading-relaxed text-warning">
              — 与 0 是两种读数：缺任何一边（母体后第一场停牌、那天没行、没有基期、那一族那天没跑）
              都是 —，<b className="text-foreground">不是 0%、不是 0 分、不是「没进过榜」</b>。
              已交出的这 {rows.length} 行里：今日读不到 {missNext} 只、近 5 日读不到 {miss5} 只、
              倍量名次没数 {missDbl} 只（每格悬停有那句原因原文；两栏各读各的，一栏有数另一栏是 — 是常态）。
            </p>
            <ExplainBox title="这块怎么读（母体判据原句 + 复权尺子原文 + 三把名次尺的分母）">
              <p>{d.rule ? plain(d.rule) : "（母体判据原句后端没交，页面不自己编）"}</p>
              <p className="mt-2">{plain(d.pct_rule ?? "接口没交 pct_rule —— 这两栏的复权口径就没有出处，页面不自己解释什么叫后复权。")}</p>
              <p className="mt-2">{uni?.note ?? "各路名次分母是哪一批：接口没交，页面不猜。"}</p>
              <p className="mt-2">{STOCK_ROW_HINT}</p>
            </ExplainBox>
            {x.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{x.popHint}</p>}
            {/* 声明 / 覆盖 / 字段含义 / 出处收成一处默认收起的折叠块（用户 2026-10-21：
                「把页面上的描述性内容全部收缩起来，避免过多占屏」）。
                **留在屏上没跟着折的**：上面那句母体榜被上限截断（`parentTruncationSentence`）、
                「— 与 0 是两种读数」那格空栏计数、`x.popHint`；缺数／截断一族的 caveat 也由折叠块自己分回屏上。 */}
            <CaliberFold caveats={d.caveats} definitions={d.definitions}
              coverage={d.coverage} provenance={[d.source, d.fingerprint]} />
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead><tr className="text-left text-muted-foreground">
                  {COLS.map(([k, label]) => (
                    <SortHeader key={k} label={label} sortKey={k} dir={srt.dirOf(k)}
                      onSort={() => { x.clearAll(); srt.toggle(k); }}
                      title={`${th(k)}｜点列头排序用的是接口交出的那一份数；缺数恒排最后`} />
                  ))}
                </tr></thead>
                <tbody>
                  {shown.map((r, i) => (
                    <tr key={`${r.code}-${i}`} {...x.rowProps(i, r.code)}>
                      <td {...x.cellProps(i, "board_rank", "font-mono tabular-nums text-muted-foreground")}>{dash(r.board_rank)}</td>
                      <td {...x.cellProps(i, "code", "whitespace-nowrap font-mono")}>
                        <StockLink code={r.code} className="font-mono hover:text-primary">{dash(r.code)}</StockLink>
                      </td>
                      <td {...x.cellProps(i, "name", "whitespace-nowrap")}>
                        <StockLink code={r.code} name={r.name} className="hover:text-primary" />
                        {r.board && <span className="ml-1 text-[10px] text-muted-foreground">{r.board}</span>}
                      </td>
                      <td {...x.cellProps(i, "latest_series", "font-mono tabular-nums")}
                        title={`${th("latest_series")}｜窗口首场 ${dash(r.first_series)} → 昨日 ${dash(r.latest_series)}，净前进 ${dash(r.gain_abs)} 名`}>
                        {rankText(r.latest_series)}
                      </td>
                      <td {...x.cellProps(i, "gain_frac", "font-mono font-semibold tabular-nums")}>
                        {fracText(r.gain_frac)}
                      </td>
                      <td {...x.cellProps(i, "chg_next_day", "whitespace-nowrap font-mono tabular-nums")}
                        title={r.chg_next_day == null
                          ? (r.perf_missing_note ?? "今日涨跌幅没读到：原因接口没交，这一格是 —，不是 0%")
                          : `今日 ${dash(r.next_day)}（母体场之后的第一场行情）：${r.chg_next_day}%（后复权）｜${th("chg_next_day")}`}>
                        {r.chg_next_day == null
                          ? <span className="text-muted-foreground/50">—</span>
                          : <span className={pctColor(r.chg_next_day)}>{pct(r.chg_next_day, 2)}</span>}
                      </td>
                      <td {...x.cellProps(i, "chg_5d", "whitespace-nowrap font-mono tabular-nums")}
                        title={r.chg_5d == null
                          ? (r.perf5_missing_note ?? "近 5 日涨跌幅没读到：原因接口没交，这一格是 —，不是 0%")
                          : `近 5 日（基期 ${dash(r.base_5d_day)} 收盘 → 读至日，窗口里有 ${dash(r.bars_5d)} 根日线）：`
                            + `${r.chg_5d}%（后复权）｜起点不是母体日，与「今日涨跌」不许相减`}>
                        {r.chg_5d == null
                          ? <span className="text-muted-foreground/50">—</span>
                          : <span className={pctColor(r.chg_5d)}>{pct(r.chg_5d, 2)}</span>}
                      </td>
                      <td {...x.cellProps(i, "bars_after", "font-mono tabular-nums")}
                        title={`${th("bars_after")}｜读至日 ${dash(d.date)}`}>{dash(r.bars_after)}</td>
                      <td {...x.cellProps(i, "oper_rank", "font-mono tabular-nums")}
                        title={rankTitle(ruleText, { scored: uni?.scored ?? null }, r.oper_rank ?? null)}>
                        {rankText(r.oper_rank)}
                      </td>
                      <td {...x.cellProps(i, "dbl_rank", "font-mono tabular-nums")}
                        title={r.dbl_rank == null
                          ? (r.dbl_missing_note ?? "倍量名次没读到，这一格是 —（没有分就没有名次，不是「最后一名」）")
                          : rankTitle(ruleText, { scored: uni?.dbl_scored ?? null }, r.dbl_rank)}>
                        {rankText(r.dbl_rank)}
                      </td>
                      <td {...x.cellProps(i, "dbl_energy", "font-mono tabular-nums")}
                        title={`${th("dbl_energy")}｜没数时：${r.dbl_missing_note ?? "这一格是 —，不是 0 分"}`}>
                        {r.dbl_energy == null
                          ? <span className="text-muted-foreground/50">—</span>
                          : trimNum(r.dbl_energy)}
                      </td>
                      <td {...x.cellProps(i, "quadrant", "font-mono")}
                        title={`${dash(r.quadrant)}${r.quadrant_label ? `｜${r.quadrant_label}` : ""}｜${th("quadrant")}`}>
                        {dash(r.quadrant)}
                      </td>
                      <td {...x.cellProps(i, "breakout_type_label", "whitespace-nowrap")}
                        title={`${th("breakout_type_label")}｜那天整族没数时接口会另行声明，不拿 — 当「没有信号」`}>
                        {dash(r.breakout_type_label)}
                      </td>
                      <td {...x.cellProps(i, "ambush_rank_in_board", "font-mono tabular-nums")}
                        title={rankTitle(ruleText, { scored: uni?.ambush_rows ?? null }, r.ambush_rank_in_board ?? null)
                          + `｜${th("ambush_rank_in_board")}`
                          + (r.ambush_type_labels ? `｜形态：${r.ambush_type_labels}` : "")
                          + (r.ambush_score != null ? `｜分 ${trimNum(r.ambush_score, 3)}` : "")}>
                        {rankText(r.ambush_rank_in_board)}
                      </td>
                      <td {...x.cellProps(i, "abnormal_hit", "font-mono")}
                        title={`${th("abnormal_hit")}｜${r.abnormal_universe_by_label
                          ? `进母体的哪一条：${r.abnormal_universe_by_label}`
                          : "进母体的哪一条没有读数 —— 是「没进这一族榜」还是「那天没跑」，上面接口的声明分得开"}`}>
                        {hitText(r.abnormal_hit)}
                      </td>
                      <td {...x.cellProps(i, "trend_selected5", "font-mono")}>{hitText(r.trend_selected5)}</td>
                      <td {...x.cellProps(i, "trend_selected10", "font-mono")}>{hitText(r.trend_selected10)}</td>
                      <td {...x.cellProps(i, "oper_value", "font-mono tabular-nums")} title={th("oper_value")}>
                        {trimNum(r.oper_value, 2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                <span className="text-muted-foreground">
                  接口交出 {rows.length} 行（母体榜前 {dash(d.limit)} 名）· 每页 {pageSize} 行（接口默认 {dash(d.page_size)}）· 现在是第 {cur}/{pages} 页
                </span>
                {cur > 1 && <button type="button" onClick={() => { x.clearAll(); setPage(cur - 1); }}
                  className="rounded-lg bg-muted/40 px-2 py-0.5 hover:bg-muted/70">上一页</button>}
                {cur < pages && <button type="button" onClick={() => { x.clearAll(); setPage(cur + 1); }}
                  className="rounded-lg bg-primary/15 px-2 py-0.5 font-medium text-primary hover:bg-primary/25">下一页</button>}
              </div>
              <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
                翻页不重新发请求：一次取数交母体榜前 {dash(d.limit)} 名的全部后续表现行，页内点列头只换本页先后；
                要挖更深得调大「前 N」。基准日（母体场）跟着接口走 —— 每个交易日它都会往前挪一场。
              </p>
              {rows.length === 0 && (
                <p className="mt-1 text-[11px] leading-relaxed text-warning">
                  母体那场榜交回 0 行 —— 「没有一只满足稳步前进判据」与「那一窗的场次没读全」是两件事，
                  接口那条 empty_board 声明分了，页面不替它挑一种说法。
                </p>
              )}
            </div>
          </>
        );
      }}
    </Panel>
  );
}
