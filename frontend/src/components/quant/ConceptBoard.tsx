import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Caveats, ExplainBox, Panel, Row } from "@/components/ui/DataPanel";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { dash, type DataState } from "@/lib/datastate";
import { plain } from "@/lib/agent";

/**
 * 「概念板块 · 这批票里的出现次数」那张反向计数榜（共用那一份）。
 *
 * 2026-10-14 抽成一份代码块：这段界面原先在四维象限（`pages/Quadrant.tsx`）与
 * 倍量形态（`pages/VolumeDouble.tsx`）各写了一遍，两份已经漂了 ——
 * 「集中度」的悬停文字一份写"同样 30 只"、一份写"同样 20 只"，
 * 而它们是同一列、同一个后端字段。调用方只交三样：整份接口读数、
 * "分母是哪一批票"那句话、以及可选的尾注；表头、六格、分页、caveats 的写法都只有一份。
 *
 * 三条口径不许破：
 *  ① 分母是**上面这张榜交出的这批票**，不是全市场。哪一批只有调用方说得清（`poolLine`），
 *     组件不猜 —— 「融资融券 130 只」被读成「全市场 130 只融资融券进了这一榜」就是这句话没写；
 *  ② `concept_pool.covered` 缺（查询失败）必须说"没读到"，不许缩成"这批票没有概念"；
 *     缺归属的那几只＝参考数据没覆盖，不是"它不属于任何题材"；
 *  ③ 这张表只数出现次数，不含热度、资金与涨跌。想知道"这批票在往哪个题材堆"，
 *     读「集中度」（榜上只数 ÷ 该概念全市场成员数）而不是「榜上只数」——
 *     只数第一名往往是宽概念（融资融券实测 3,872 只成员）。
 */

export interface ConceptRow {
  concept?: string | null; stocks?: number | null; members_total?: number | null;
  pool_share_pct?: number | null; lift_pctile?: number | null; sample_codes?: string | null;
}
export interface ConceptPool {
  size?: number | null; covered?: number | null;
  concepts_total?: number | null; single_stock?: number | null;
}
export interface ConceptBoardData {
  concepts?: ConceptRow[] | null;
  concept_pool?: ConceptPool | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
  /** 概念那一列与这张反向榜的口径文字（后端原句，页面不改写） */
  concept_rule?: string | null;
}

const pctCell = (v?: number | null) =>
  typeof v === "number" ? `${v}%` : dash(null);

export function ConceptBoard<T extends ConceptBoardData>({ state, note, poolLine, pickCaveats, pageSize, footer }: {
  state: DataState<T>;
  /** 这张表数的是什么、同名多条板块码怎么算 —— 各屏的取数链不同，这句话由调用方说 */
  note: string;
  /** 「统计的是哪些票」那一行的 value：分母是谁只有调用方知道 */
  poolLine: (d: T) => string;
  /** 只把与这张表有关的 caveat 挑出来（整屏的 caveats 混着别的判据，全贴在这儿会误导） */
  pickCaveats?: (d: T) => { code?: string | null; text?: string | null }[] | null;
  pageSize: number;
  footer?: ReactNode;
}) {
  // 默认按榜上只数从高到低：这一列才是"这批票共同挂在哪些题材上"的直接读法。
  const srt = useTableSort({ key: "stocks", dir: "desc" });
  const [page, setPage] = useState(1);
  return (
    <Panel title="概念板块 · 这批票里的出现次数" state={state} note={note}>
      {d => {
        const cs = d.concepts ?? [];
        const pool = d.concept_pool ?? {};
        const pages = Math.max(1, Math.ceil(cs.length / pageSize));
        const cur = Math.min(Math.max(1, page), pages);
        const shown = srt.apply(cs).slice((cur - 1) * pageSize, cur * pageSize);
        return (
          <>
            <Row label="统计的是哪些票" value={poolLine(d)} />
            <Row label="有概念归属的只数"
              value={typeof pool.covered === "number"
                ? `${pool.covered} / ${dash(pool.size ?? 0)} 只（缺的那几只库里没有概念行 = 参考数据没覆盖，不是「它不属于任何题材」）`
                : "没读到（查询失败，不是这批票没有概念）"} />
            <Row label="涉及多少个不同概念"
              value={`${dash(pool.concepts_total ?? cs.length)} 个（其中 ${dash(pool.single_stock ?? 0)} 个只有 1 只票挂着）`} />
            {pickCaveats && <Caveats items={pickCaveats(d)} />}
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead><tr className="text-left text-muted-foreground">
                  <SortHeader label="概念板块" sortKey="concept" dir={srt.dirOf("concept")} onSort={srt.toggle} />
                  <SortHeader label="榜上只数" sortKey="stocks" dir={srt.dirOf("stocks")} onSort={srt.toggle}
                    title={`这批票里挂了这个概念的只数；分母是本榜交出的 ${dash(pool.size ?? 0)} 只`} />
                  <SortHeader label="占榜比例" sortKey="pool_share_pct" dir={srt.dirOf("pool_share_pct")} onSort={srt.toggle}
                    title="榜上只数 ÷ 榜上总只数（%）" />
                  <SortHeader label="全市场成员" sortKey="members_total" dir={srt.dirOf("members_total")} onSort={srt.toggle}
                    title="这个概念在全市场有多少只票 —— 只数第一名往往是宽概念（融资融券实测 3,872 只），不是题材共性" />
                  <SortHeader label="集中度" sortKey="lift_pctile" dir={srt.dirOf("lift_pctile")} onSort={srt.toggle}
                    title="榜上只数 ÷ 该概念全市场成员数（%）：同样 30 只，占本身成员 40% 与 2% 是两回事。想知道「这批票在往哪个题材堆」，看这一列比看只数准" />
                  <th className="whitespace-nowrap font-medium">样例票（榜上的，最多 6 只）</th>
                </tr></thead>
                <tbody>
                  {shown.map((c, i) => (
                    <tr key={`${c.concept}-${i}`} className="border-t border-border/40">
                      <td className="whitespace-nowrap">{dash(c.concept)}</td>
                      <td className="font-mono font-semibold tabular-nums">{dash(c.stocks)}</td>
                      <td className="font-mono tabular-nums">{pctCell(c.pool_share_pct)}</td>
                      <td className="font-mono tabular-nums text-muted-foreground">{dash(c.members_total)}</td>
                      <td className="font-mono tabular-nums">{pctCell(c.lift_pctile)}</td>
                      <td className="whitespace-nowrap font-mono text-[10px] text-muted-foreground">
                        {(c.sample_codes ?? "").split("、").filter(Boolean).map(code => (
                          <Link key={code} className="mr-1 hover:text-primary"
                            to={`/stock-review?symbol=${encodeURIComponent(code)}`}>{code}</Link>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                <span className="text-muted-foreground">
                  共 {cs.length} 个概念 · 每页 {pageSize} 个，现在是第 {cur}/{pages} 页
                  （{srt.sort
                    ? `已按「${srt.sort.key} ${srt.sort.dir === "desc" ? "从高到低" : "从低到高"}」重排，这一页是排过之后的第 ${(cur - 1) * pageSize + 1}~${Math.min(cs.length, cur * pageSize)} 个`
                    : `这一页是后端交出的序（榜上只数从高到低）里的第 ${(cur - 1) * pageSize + 1}~${Math.min(cs.length, cur * pageSize)} 个`}）
                </span>
                {cur > 1 && (
                  <button type="button" onClick={() => setPage(cur - 1)}
                    className="rounded-lg bg-muted/40 px-2 py-0.5 hover:bg-muted/70">上一页</button>
                )}
                {cur < pages && (
                  <button type="button" onClick={() => setPage(cur + 1)}
                    className="rounded-lg bg-primary/15 px-2 py-0.5 font-medium text-primary hover:bg-primary/25">
                    加载第 {cur + 1} 页（第 {cur * pageSize + 1}~{Math.min(cs.length, (cur + 1) * pageSize)} 个）
                  </button>
                )}
              </div>
              {cs.length === 0 && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  这一屏没交出概念行 —— 上面 caveats 里那条说的是为什么（读不到 / 真的没归属），两者不能混。
                </p>
              )}
            </div>
            {d.concept_rule && (
              <ExplainBox title="概念口径（后端原句，页面未改写）">
                <p>{plain(d.concept_rule)}</p>
              </ExplainBox>
            )}
            {footer}
          </>
        );
      }}
    </Panel>
  );
}
