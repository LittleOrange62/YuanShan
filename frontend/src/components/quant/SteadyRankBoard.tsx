import { useState } from "react";
import { CaliberFold, ExplainBox, Panel, Row } from "@/components/ui/DataPanel";
import { SortHeader } from "@/components/ui/SortHeader";
import { StockLink } from "@/components/ui/StockLink";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { dash, num, type DataState } from "@/lib/datastate";
import { plain } from "@/lib/agent";
import { cn } from "@/lib/utils";
import {
  LIMIT_OPTIONS, METRIC_BUTTONS, axisRegression, medianGainPct, nameIsCode, pathMismatch,
  stepsSentence, truncationSentence, windowSentence,
  type SteadyData, type SteadyMetric,
} from "@/lib/steadyRank";

/**
 * 名次稳步前进榜（近 6 场**名次**一直在往前走的票）。
 *
 * 用户 2026-10-18 点名：「展示这个 xy 名次近 6 个交易日稳步增长的个股，是名次！
 * 可以手动切换成总分稳步增长的，可以选择。」
 *
 * 与上面那张「名次稳定榜」的分工摆在这儿，两屏都不许被读成同一张榜：
 *  · 稳定榜答「这几天谁**一直靠前**」（名次之和越小越好）；
 *  · 这一屏答「这几天谁**一直在往前走**」（看方向，不看位置）——
 *    所以榜顶完全可能是从 5,000 名走过来的票，它现在仍然不在前排。
 *
 * 判据、门槛、字段含义、caveat 全在接口那一份（`duanxian/quant.py::rank_momentum`），
 * 这个组件只做三件事：把数摆上屏、把接口的原话转述出来、自检交出的数对不对得上自己。
 * 「稳步」在这里是**名次数字变小**（1 = 全场第一），不是数字上涨 —— 这句由后端
 * `rank_direction` 那条 caveat 原句上屏，页面不再自己写第二遍。
 */

/** 列的一份清单：表头与格子都从这同一个数组走（两处各写一份就会错位，全站栽过一次）。 */
const NUM_COLS: [string, string][] = [
  ["board_rank", "榜内"],
  ["security_code", "代码"],
  ["name", "名称"],
  ["gain_frac", "前进幅度"],
  ["first_series", "首场名次"],
  ["latest_series", "最近名次"],
  ["gain_abs", "净前进"],
  ["better_steps", "前进一步"],
  ["worse_steps", "后退步"],
  ["tied_steps", "持平步"],
  ["biggest_step_gain", "最大单步"],
];
const PATH_COLS: [string, string][] = [
  ["series_path", "名次路径"],
  ["x_path", "X 轴路径"],
  ["y_path", "Y 轴路径"],
];
const TAIL_COLS: [string, string][] = [
  ["quadrant", "档"],
  ["oper_value", "oper"],
];

export function SteadyRankBoard({ state, metric, onMetricChange, limit, onLimitChange, onRefresh }: {
  state: DataState<SteadyData>;
  metric: SteadyMetric;
  onMetricChange: (m: SteadyMetric) => void;
  limit: number;
  onLimitChange: (n: number) => void;
  onRefresh?: () => void;
}) {
  const srt = useTableSort();
  const x = useStockRowInteraction("名次稳步前进榜");
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(1);
  /** 换档/换上限/换页长：清行选中并回到第 1 页。**列名两档完全相同，所以页内排序保留**
   *  （切一档就丢排序，等于逼读者再点一遍同一列）。 */
  const pick = (fn: () => void) => { x.clearAll(); setPage(1); fn(); };

  return (
    <Panel title="名次稳步前进榜（近 6 场名次一直在往前走 · xy / 总分两档可切）" state={state}
      slowHint="实测（2026-10-19）：首次约 6.2 秒（窗口与母体那一发全库聚合也算在内），窗口命中缓存后约 2.4 秒。"
      controls={
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          <span className="text-muted-foreground">排哪一列名次</span>
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
      note="名次不是新算的：每天每票的全市场名次早就写在打分表里（rank_in_market），这一屏只是把相邻两场的名次比个方向。">
      {d => {
        const rows = d.rows ?? [];
        const pages = Math.max(1, Math.ceil(rows.length / pageSize));
        const cur = Math.min(Math.max(1, page), pages);
        const shown = srt.apply(rows).slice((cur - 1) * pageSize, cur * pageSize);
        const steps = d.window?.steps ?? null;
        const med = medianGainPct(rows);
        const back = axisRegression(d);
        const mismatched = rows.filter(r => pathMismatch(r, d.window?.sessions).length > 0);
        const nameOnly = nameIsCode(rows);
        const th = (k: string) => d.definitions?.[k] ?? "这一列的口径文字后端没交，页面不自己编";
        return (
          <>
            {/* 用户 2026-10-19 夜：「把选项留出，描述内容都收缩起来」——
                「这一档排的是什么」「进榜的门槛」这两段判据描述收进下面的说明框（点开可见，
                数字仍逐项读接口 thresholds/window，一份都不写死）；
                下面这句截断声明**不折**：80 行会被读成"全场只有 80 只在稳步前进"，而接口明明交了总数 */}
            <Row label="看的是哪几天" value={windowSentence(d)} />
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{truncationSentence(d)}</p>
            {med !== null && (
              <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
                已交出的这 {rows.length} 行里，前进幅度的中位是 <b className="font-mono text-foreground">{num(med, 1)}%</b>
                —— 这是本页按已交出的行数出来的分布读数（不是判据、也不是全场的中位），
                用来看榜顶那个数是一整批都这样，还是只有一只异常值撑着。
              </p>
            )}
            {/* 两道自检：只在真对不上时出声。对得上什么都不说，不是"检查过了没问题"。 */}
            {back !== null && back > 0 && (
              <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">
                这一档有 {back} 行其实有一轴并没有净前进（两轴首末值对不上判据 ⑤）——
                接口那句「两轴都要净前进」没兜住，这一屏的榜要按可疑处理，请回报取数侧。
              </p>
            )}
            {mismatched.length > 0 && (
              <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">
                有 {mismatched.length} 行的路径段数与窗口场数（{dash(d.window?.sessions)} 场）不一致，
                例如 {mismatched[0].security_code}：{pathMismatch(mismatched[0], d.window?.sessions).join("、")} ——
                路径与步数应当是同一次聚合数出来的，对不上就别按「近 6 场」读这几行。
              </p>
            )}
            <ExplainBox title="这张榜怎么读（这一档排的是什么 · 进榜的门槛 · 判据原句 · 与名次稳定榜的分工）">
              <Row label="这一档排的是"
                value={`${dash(d.metric_label)}（${dash(d.series_label)}）—— `
                  + (d.thresholds?.require_both_axes
                    ? '进榜要求两轴各自都净前进'
                    : '单一序列（综合分那一条名次），没有另一轴可查')
                  + '；「前进」是什么意思见下面那条 rank_direction 原话（页面不另写一遍）'} />
              <Row label="进榜的门槛"
                value={`前进一步 ≥ ${dash(d.thresholds?.min_better_steps)} 步、后退 ≤ `
                  + `${dash(d.thresholds?.max_worse_steps)} 步、净前进 ≥ 首场名次的 `
                  + `${dash(d.thresholds?.min_gain_frac_pct)}%（共 ${dash(steps)} 步，门槛原句见下面「这张榜怎么读」）`} />
              <p className="mt-1">{d.rule ? plain(d.rule) : "（判据原句后端没交，页面不自己编）"}</p>
              <p className="mt-2">{STOCK_ROW_HINT}从「四维象限」双击过来时，来源会写在这张榜的名字里。</p>
            </ExplainBox>
            {x.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{x.popHint}</p>}
            {/* 声明 / 母体那句话 / 字段含义 / 覆盖 / 出处收成一处默认收起的折叠块
                （用户 2026-10-19 夜：「这些内容都默认收缩起来」）。summary 摆条数 ⇒ 收起不等于藏掉。
                **不进这里的三件**：弹窗被拦 `popHint`、两道自检（一轴没前进 / 路径段数对不上）、
                榜被上限截断那句 —— 那些是把"屏幕会说谎"那一类折起来的东西。 */}
            <CaliberFold caveats={d.caveats} definitions={d.definitions}
              coverage={d.coverage} provenance={[d.source, d.series_key, d.labels_source]}
              notes={[d.universe?.note]} />
            {nameOnly.length > 0 && (
              <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                名称列里有 {nameOnly.length} 只的「名字」就是代码本身
                （{nameOnly.slice(0, 5).join("、")}{nameOnly.length > 5 ? " 等" : ""}）——
                股票字典里没登记中文名，不是页面截断了。
              </p>
            )}
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead><tr className="text-left text-muted-foreground">
                  {NUM_COLS.map(([k, label]) => (
                    <SortHeader key={k} label={label} sortKey={k} dir={srt.dirOf(k)}
                      onSort={() => { x.clearAll(); srt.toggle(k); }}
                      title={`${th(k)}｜点列头排序用的是接口交出的那一份数；缺数恒排最后`} />
                  ))}
                  {/* 路径三列是文本：按 '1→9' 与 '10→2' 排出来的先后没有读数意义，
                      所以这三格不给排序按钮，要排就点前面的数值列（全站同一条规矩）。 */}
                  {PATH_COLS.map(([k, label]) => (
                    <th key={k} className="whitespace-nowrap px-2 py-1.5 font-normal"
                      title={`${th(k)}｜这一列是文本，不参与排序`}>{label}</th>
                  ))}
                  {TAIL_COLS.map(([k, label]) => (
                    <SortHeader key={k} label={label} sortKey={k} dir={srt.dirOf(k)}
                      onSort={() => { x.clearAll(); srt.toggle(k); }}
                      title={`${th(k)}｜点列头排序用的是接口交出的那一份数；缺数恒排最后`} />
                  ))}
                </tr></thead>
                <tbody>
                  {shown.map((r, i) => (
                    <tr key={`${r.security_code}-${i}`} {...x.rowProps(i, r.security_code)}>
                      <td {...x.cellProps(i, "board_rank", "font-mono tabular-nums text-muted-foreground")}>{dash(r.board_rank)}</td>
                      <td {...x.cellProps(i, "security_code", "whitespace-nowrap font-mono")}>
                        <StockLink code={r.security_code} className="font-mono hover:text-primary">{dash(r.security_code)}</StockLink>
                      </td>
                      <td {...x.cellProps(i, "name", "whitespace-nowrap")}>
                        <StockLink code={r.security_code} name={r.name} className="hover:text-primary" />
                        {r.board && <span className="ml-1 text-[10px] text-muted-foreground">{r.board}</span>}
                      </td>
                      <td {...x.cellProps(i, "gain_frac", "font-mono font-semibold tabular-nums")}
                        title={`${r.gain_abs ?? "—"} 名 ÷ 首场 ${r.first_series ?? "—"} = 前进幅度`}>
                        {num(r.gain_frac, 1)}%
                      </td>
                      <td {...x.cellProps(i, "first_series", "font-mono tabular-nums")}>{dash(r.first_series)}</td>
                      <td {...x.cellProps(i, "latest_series", "font-mono tabular-nums")}>{dash(r.latest_series)}</td>
                      <td {...x.cellProps(i, "gain_abs", "font-mono tabular-nums")}>{dash(r.gain_abs)}</td>
                      <td {...x.cellProps(i, "better_steps", "font-mono tabular-nums")}
                        title={stepsSentence(r, steps)}>{dash(r.better_steps)}</td>
                      <td {...x.cellProps(i, "worse_steps", cn("font-mono tabular-nums",
                        typeof r.worse_steps === "number" && r.worse_steps > 0 && "text-warning"))}
                        title={stepsSentence(r, steps)}>{dash(r.worse_steps)}</td>
                      <td {...x.cellProps(i, "tied_steps", "font-mono tabular-nums")}
                        title={stepsSentence(r, steps)}>{dash(r.tied_steps)}</td>
                      <td {...x.cellProps(i, "biggest_step_gain", "font-mono tabular-nums")}>{dash(r.biggest_step_gain)}</td>
                      <td {...x.cellProps(i, "series_path", "whitespace-nowrap font-mono text-[10px] leading-tight")}
                        title={`逐场名次（早→晚）：${r.series_path ?? "接口没交这一列"}`}>{dash(r.series_path)}</td>
                      <td {...x.cellProps(i, "x_path", "whitespace-nowrap font-mono text-[10px] leading-tight")}
                        title={`X 轴名次（早→晚）：${r.x_path ?? "接口没交这一列"}`}>{dash(r.x_path)}</td>
                      <td {...x.cellProps(i, "y_path", "whitespace-nowrap font-mono text-[10px] leading-tight")}
                        title={`Y 轴名次（早→晚）：${r.y_path ?? "接口没交这一列"}`
                          + "；这一轴全场只有几十个不同取值，连着几个数字相同是并列、不是没动"}>
                        {dash(r.y_path)}
                      </td>
                      <td {...x.cellProps(i, "quadrant", "font-mono")}
                        title={`${r.quadrant ?? "—"}${r.quadrant_label ? `｜${r.quadrant_label}` : ""}`}>{dash(r.quadrant)}</td>
                      <td {...x.cellProps(i, "oper_value", "font-mono tabular-nums")}>{num(r.oper_value, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                <span className="text-muted-foreground">接口交出 {rows.length} 行（前 {dash(d.limit)} 名）· 每页 {pageSize} 行 · 现在是第 {cur}/{pages} 页</span>
                {cur > 1 && <button type="button" onClick={() => { x.clearAll(); setPage(cur - 1); }}
                  className="rounded-lg bg-muted/40 px-2 py-0.5 hover:bg-muted/70">上一页</button>}
                {cur < pages && <button type="button" onClick={() => { x.clearAll(); setPage(cur + 1); }}
                  className="rounded-lg bg-primary/15 px-2 py-0.5 font-medium text-primary hover:bg-primary/25">下一页</button>}
              </div>
              {/* 翻页不重新发请求 —— 不写这句会被读成"第二页是刚拉的数" */}
              <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
                翻页不重新发请求：一次取数交前 {dash(d.limit)} 名，页内点列头只换本页先后。
                要看更深的名次得调大上面那个「前 N」，不是往后翻了就还有数。
              </p>
              {rows.length === 0 && (
                <p className="mt-1 text-[11px] leading-relaxed text-warning">
                  这一档交回 0 行（符合判据的只数就是上面那一句说的 {dash(d.total_matched)} 只）——
                  「没有一只满足这套判据」与「这一窗的场次没读全」是两件事，
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
