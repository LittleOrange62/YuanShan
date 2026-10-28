import { useState } from "react";
import { CaliberFold, ExplainBox, Panel, Row } from "@/components/ui/DataPanel";
import { SortHeader } from "@/components/ui/SortHeader";
import { StockLink } from "@/components/ui/StockLink";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { dash, type DataState } from "@/lib/datastate";
import { num } from "@/lib/qd";
import { cn } from "@/lib/utils";

/**
 * 名次稳定榜（近 N 场**全市场名次之和**从低到高，前 50）。
 *
 * 用户 2026-10-15 点名：「显示近3天、7天、11天的名次之和。按照和的总和从低到高显示前50位。
 * 总和越小，证明这几天他的名次一直保持越高。」
 *
 * 三条读法跟着实现走，不在这里重述判据（那些句子住在 `duanxian/quant.py::rank_stability`，
 * 由接口的 `rule`/`definitions`/`universe.note` 交出）：
 *  · 窗口只有 **3 / 7 / 11** 三档（后端 `RANK_WINDOWS` 会拒别的值，界面也就这三个按钮）；
 *  · **凑不满 N 场不进榜**，被排除多少只当场念出来（`universe.excluded_partial`）；
 *  · 逐日名次路径（`rank_path`）是判"一直稳"还是"某天冲进来看坏了均值"的那一列，
 *    只看「名次之和」会把它抹平。
 */

export interface RsRow {
  security_code?: string | null; name?: string | null; board?: string | null;
  board_rank?: number | null; sessions_used?: number | null; rank_sum?: number | null;
  avg_rank?: number | null; best_rank?: number | null; worst_rank?: number | null;
  rank_spread?: number | null; latest_rank?: number | null; rank_path?: string | null;
  quadrant?: string | null; quadrant_label?: string | null; oper_value?: number | null;
}
export interface RankStabilityData {
  available?: boolean | null; reason?: string | null; date?: string | null; source?: string | null;
  capability_key?: string | null; limit?: number | null; total_ranked?: number | null;
  window?: { days?: number | null; sessions?: number | null; first_day?: string | null;
             last_day?: string | null; is_trading_days?: boolean | null } | null;
  universe?: { scored_codes?: number | null; fully_covered?: number | null; excluded_partial?: number | null;
               per_session_min?: number | null; per_session_max?: number | null;
               stale_rows?: number | null; note?: string | null } | null;
  rows?: RsRow[] | null; rule?: string | null; rejected?: string | null;
  definitions?: Record<string, string> | null;
  coverage?: Record<string, { from?: string | null; to?: string | null; sessions?: number | null }> | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
}

/** 与后端 `RANK_WINDOWS` 同一份清单（这里只决定界面有几个按钮；真拒的是后端）。 */
export const RANK_WINDOW_OPTIONS = [3, 7, 11];

const COLS: [string, string, string][] = [
  ["board_rank", "榜内", "榜内第几行（按名次之和升序，并列时先比这段时间的最好名次、再比代码）"],
  ["security_code", "代码", ""],
  ["name", "名称", ""],
  ["rank_sum", "名次之和", ""],
  ["avg_rank", "平均名次", ""],
  ["best_rank", "最好名次", "这段时间里最好的一天排到第几"],
  ["worst_rank", "最差名次", "这段时间里最差的一天排到第几（和相同的两只，看这一列与极差分高低）"],
  ["rank_spread", "极差", "最差名次 − 最好名次：0＝每天都是同一个位置"],
  ["latest_rank", "最近一场", "窗口最后一天（最新那场）的当日名次；和很靠前而这一格靠后＝正在掉队"],
  ["rank_path", "逐日名次路径", "早→晚，用 → 连；一眼看出是稳还是大起大落"],
  ["quadrant", "档", "最近一场的当日象限（与上面「选股象限」那一屏同一列，不是新判据）"],
  ["oper_value", "oper", "最近一场的综合分原值"],
];

export function RankStabilityBoard({ state, onDaysChange, days, onRefresh }: {
  state: DataState<RankStabilityData>;
  days: number;
  onDaysChange: (d: number) => void;
  /** 换窗口＝换一份缓存，顺手清掉行选中（行号会变，高亮跟着漂到别的票身上） */
  onRefresh?: () => void;
}) {
  const srt = useTableSort();
  const x = useStockRowInteraction("名次稳定榜");
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(1);

  return (
    <Panel title="名次稳定榜（近 N 场全市场名次之和 · 从低到高）" state={state}
      slowHint="三档窗口实测 1.1~3.6 秒（11 场最慢）；窗口与母体那两发全库聚合命中缓存时不到 0.1 秒。"
      controls={
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          <span className="text-muted-foreground">窗口</span>
          {RANK_WINDOW_OPTIONS.map(n => (
            <button key={n} type="button" aria-pressed={days === n}
              onClick={() => { onDaysChange(n); x.clearAll(); setPage(1); }}
              className={cn("rounded-lg border px-2 py-0.5", days === n
                ? "border-primary/70 bg-primary/10 font-medium text-primary"
                : "border-border hover:bg-muted/50")}>{n} 场</button>
          ))}
          <span className="ml-2 text-muted-foreground">每页</span>
          {[25, 50].map(n => (
            <button key={n} type="button" aria-pressed={pageSize === n}
              onClick={() => { setPageSize(n); x.clearAll(); setPage(1); }}
              className={cn("rounded-lg border px-2 py-0.5", pageSize === n
                ? "border-primary/70 bg-primary/10 font-medium text-primary" : "border-border hover:bg-muted/50")}>
              {n} 行</button>
          ))}
          {onRefresh && (
            <button type="button" onClick={() => { x.clearAll(); onRefresh(); }}
              className="ml-2 rounded-lg bg-primary/15 px-2 py-0.5 font-medium text-primary hover:bg-primary/25">
              清缓存重算
            </button>
          )}
        </div>
      }
      note="名次不是新算的：每天每票的全市场名次早就写在打分表里（`rank_in_market`），这一屏只是把窗口内每天的名次相加。">
      {d => {
        const rows = d.rows ?? [];
        const pages = Math.max(1, Math.ceil(rows.length / pageSize));
        const cur = Math.min(Math.max(1, page), pages);
        const shown = srt.apply(rows).slice((cur - 1) * pageSize, cur * pageSize);
        const u = d.universe ?? {};
        return (
          <>
            <Row label="看的是哪几天"
              value={`截至 ${dash(d.date)} 的最近 ${dash(d.window?.days)} 个交易日：${dash(d.window?.first_day)} ~ ${dash(d.window?.last_day)}（共 ${dash(d.window?.sessions)} 场；「近 N 天」按交易日数，长假里日历日会更多）`} />
            <Row label="进榜的票"
              value={`${dash(d.total_ranked)} 只凑满 ${dash(d.window?.days)} 场（这一窗有打分的共 ${dash(u.scored_codes)} 只代码，其中 ${dash(u.excluded_partial)} 只因缺场次被排除）；每场母体 ${dash(u.per_session_min)}~${dash(u.per_session_max)} 只`}
              tone={typeof u.excluded_partial === "number" && u.excluded_partial > 0 ? "warn" : undefined} />
            <Row label="这张榜" value={`按名次之和升序取前 ${dash(d.limit)} 名，现在是第 ${cur}/${pages} 页 · 本页 ${shown.length} 行`} />
            {/* 用户 2026-10-19 夜：「把选项留出，描述内容都收缩起来」——
                上面三行 Row 是读数（哪天、几只、被排除几只、取前几名）留在外面；
                后端那句母体描述（`universe.note`，口径原句一类）与判据原句一起收进下面的说明框 */}
            <ExplainBox title="这张榜怎么读（口径原句 · 母体那句话说的是哪些票 · 每一列是什么）">
              {u.note && <p>{u.note}</p>}
              <p className={u.note ? "mt-2" : undefined}>{d.rule ?? "（这一列的口径文字后端没交，页面不自己编）"}</p>
              <p className="mt-2">{STOCK_ROW_HINT}从「四维象限」双击过来时，来源会写在这张榜的名字里。</p>
            </ExplainBox>
            {/* 弹窗被拦是**告警**，不折进上面那个默认收起的说明框 */}
            {x.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{x.popHint}</p>}
            {/* 声明 / 字段含义 / 覆盖 / 出处收成一处默认收起的折叠块（用户 2026-10-19 夜：
                「这些内容都默认收缩起来」）。summary 把每一类的条数摆在外面 ⇒ 收起不等于藏掉；
                告警（`popHint`）与上面那三行读数不折 —— 那条规矩没变。 */}
            <CaliberFold caveats={d.caveats} definitions={d.definitions}
              coverage={d.coverage} provenance={[d.source, d.capability_key]} />
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead><tr className="text-left text-muted-foreground">
                  {COLS.map(([k, label, title]) => (
                    <SortHeader key={k} label={label} sortKey={k} dir={srt.dirOf(k)}
                      onSort={() => { x.clearAll(); srt.toggle(k); }}
                      title={title || `点列头排序用的是接口交出的那一份数；缺值恒排最后`} />
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
                      <td {...x.cellProps(i, "rank_sum", "font-mono font-semibold tabular-nums")}>{dash(r.rank_sum)}</td>
                      <td {...x.cellProps(i, "avg_rank", "font-mono tabular-nums")}>{num(r.avg_rank, 1)}</td>
                      <td {...x.cellProps(i, "best_rank", "font-mono tabular-nums")}>{dash(r.best_rank)}</td>
                      <td {...x.cellProps(i, "worst_rank", "font-mono tabular-nums")}>{dash(r.worst_rank)}</td>
                      <td {...x.cellProps(i, "rank_spread", cn("font-mono tabular-nums",
                        typeof r.rank_spread === "number" && r.rank_spread > 1000 && "text-warning"))}>{dash(r.rank_spread)}</td>
                      <td {...x.cellProps(i, "latest_rank", "font-mono tabular-nums")}>{dash(r.latest_rank)}</td>
                      <td {...x.cellProps(i, "rank_path", "whitespace-nowrap font-mono text-[10px] leading-tight")}
                        title={`逐日名次（早→晚）：${r.rank_path ?? "接口没交这一列"}`}>
                        {dash(r.rank_path)}
                      </td>
                      <td {...x.cellProps(i, "quadrant", "font-mono")}
                        title={`${r.quadrant ?? "—"}${r.quadrant_label ? `｜${r.quadrant_label}` : ""}`}>{dash(r.quadrant)}</td>
                      <td {...x.cellProps(i, "oper_value", "font-mono tabular-nums")}>{num(r.oper_value, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                <span className="text-muted-foreground">接口交出 {rows.length} 行（前 {dash(d.limit)} 名）· 每页 {pageSize} 行</span>
                {cur > 1 && <button type="button" onClick={() => setPage(cur - 1)}
                  className="rounded-lg bg-muted/40 px-2 py-0.5 hover:bg-muted/70">上一页</button>}
                {cur < pages && <button type="button" onClick={() => setPage(cur + 1)}
                  className="rounded-lg bg-primary/15 px-2 py-0.5 font-medium text-primary hover:bg-primary/25">下一页</button>}
              </div>
              {/* 两页是同一次取数切开的，不是两次请求 —— 不写这句会被读成"第二页是刚拉的数" */}
              <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
                翻页不重新发请求：接口一次交前 {dash(d.limit)} 名，页内点列头只换本页先后。
                要更深的名次得调大「前 N」重新取数，不是往后翻了就还有数。
              </p>
              {rows.length === 0 && (
                <p className="mt-1 text-[11px] leading-relaxed text-warning">
                  凑满 {dash(d.window?.days)} 场的票是 0 只 —— 按接口那句原话，是这一窗的场次没读全（多半回填中途），
                  不是市场没有票。换一档窗口或点「清缓存重算」。
                </p>
              )}
            </div>
          </>
        );
      }}
    </Panel>
  );
}
