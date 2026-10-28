import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { Caliber } from "@/components/ui/Caliber";
import { SortHeader } from "@/components/ui/SortHeader";
import { CaliberFold, ExplainBox, Panel } from "@/components/ui/DataPanel";
import { StockLink } from "@/components/ui/StockLink";
import { useNonce, useProbe } from "@/lib/probe";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { usePageAskAi } from "@/lib/workspace/pageAskAi";
import { dash } from "@/lib/datastate";
import { plain } from "@/lib/agent";
import { cn } from "@/lib/utils";
import {
  coverageSentence, groupReading, groupRows, pctText, sectorTags, stateSentence,
  truncationSentence, yiText, featureTags, type TrendPayload, type TrendRow,
} from "@/lib/trendStocks";

/**
 * 趋势股（用户 2026-10-15 点名：把那份说明里我们没有的那一屏补上）。
 *
 * 三条由取数侧定下、界面上不许糊掉的红线（判读逻辑都在 `lib/trendStocks.ts`，这里只画）：
 *  · 主板／创业板／科创板**分三组摆**，档位不同不并榜；空组保留标题写「当日暂无」，
 *    少了标题读者会以为页面把这一组滤掉了。
 *  · 入选条件（近 N 日每天收盘都严格站上当日 5 日线 × 当天不涨停）由后端判，
 *    页面只显示 `ma5_streak` 这个读数，不拿它当入选条件复述。
 *  · 榜被上限截断时，组内计数与整组汇总只代表已交出的那一段 —— 那句声明摆在大号数字下面，不折。
 * 每列的悬停读的都是接口 `definitions`；没有的那条直说「后端 definitions 里没给」，页面不自编。
 */

const LOOKBACKS = [5, 10];

function Tag({ label, on }: { label: string; on: boolean }) {
  return (
    <span className={cn("mr-1 rounded px-1 py-0.5 text-[10px]",
      on ? "bg-primary/15 text-primary" : "text-muted-foreground/45 line-through decoration-muted-foreground/30")}>
      {label}
    </span>
  );
}

export function TrendStockBoard() {
  const [nonce, refresh] = useNonce();
  const [lookback, setLookback] = useState(5);
  const [limit, setLimit] = useState(300);
  const ts = useStockRowInteraction("趋势股");
  const tsSort = useTableSort();
  const state = useProbe<TrendPayload>("/quant/trend-stocks", nonce, { lookback, limit });

  // 右侧「AI分析」栏带的是这一屏此刻这份读数。登记放在组件顶层：
  // `Panel` 那个 render-prop 只在有数时才画，把 hook 放进去等于按数据有无改变 hook 调用次数。
  const cur = state.kind === "ok" ? state.data : null;
  const askRows = cur?.rows ?? [];
  usePageAskAi(askRows.length ? {
    label: `趋势股（近 ${lookback} 日站上 5 日线，${dash(cur?.date)}）`,
    context: [
      `交易日 ${dash(cur?.date)}，周期近 ${dash(cur?.lookback)} 个交易日，母体 ${dash(cur?.universe_rows)} 行，`
      + `这一场实际入选 ${dash(cur?.selected_total)} 只，本次交出 ${askRows.length} 行。`,
      "入选四条同时成立：周期内每天收盘严格高于当日 5 日均线 × 当天没涨停 × 涨幅算得出来 × 主板/创业板/科创板（北交所不纳入）。",
      askRows.slice(0, 40).map(r => `${r.name ?? ""}(${r.security_code}) ${r.board ?? ""} `
        + `现价 ${dash(r.close)} 涨幅 ${pctText(r.pct_change)} 成交额 ${yiText(r.amount_yi)} `
        + `流通市值 ${yiText(r.float_mv_yi)} 连续站上 MA5 ${dash(r.ma5_streak)} 天 `
        + `特征 ${featureTags(r, lookback).filter(t => t.on).map(t => t.label).join("·") || "无"} `
        + `相关板块 ${sectorTags(r.sectors)}`).join("\n"),
      "这是客观公开统计，不打分、不预测、不给买卖建议；价位与止损由材料里的数字推得，风险自担。",
    ].join("\n"),
    suggestions: ["这一屏里哪些票的板块合力最清楚", "大市值与小市值的两端分别是什么形态"],
  } : null);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold">趋势股</h2>
        <Caliber text={
          "入选四条同时成立：所选周期内每一个交易日收盘都严格高于「截止这一天的 5 日收盘均线」；"
          + "当天收盘没有涨停；当日涨幅算得出来；能归进主板／创业板／科创板（北交所不纳入）。\n"
          + "完整口径是表下那句「取数口径（后端原句）」，页面不改写。"
        } />
        <button type="button" onClick={() => { ts.clearAll(); refresh(); }} title="重新取数"
          className="ml-auto flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>

      <Panel title="近 N 日站上 5 日线 · 当天没封涨停" state={state}
        note="周期、上限都是后端参数；行数、母体、组内只数都由后端交出，页面不自己数。"
        controls={
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] text-muted-foreground">选股周期</span>
              {LOOKBACKS.map(n => (
                <button key={n} type="button" aria-pressed={lookback === n}
                  onClick={() => { setLookback(n); ts.clearAll(); }}
                  className={cn("rounded-lg px-2 py-1 text-[11px]",
                    lookback === n ? "bg-primary/20 text-primary" : "text-muted-foreground hover:bg-muted/40")}>
                  最近 {n} 个交易日
                </button>
              ))}
            </div>
            <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
              取数上限
              <select value={limit} onChange={e => { setLimit(Number(e.target.value)); ts.clearAll(); }}
                className="rounded bg-muted/40 px-1 py-0.5 text-[11px] text-foreground">
                {[100, 300, 600, 1000].map(v => <option key={v} value={v}>{v} 行</option>)}
              </select>
            </label>
          </div>
        }>
        {d => {
          const rows = d.rows ?? [];
          const st = stateSentence(d);
          const reading = groupReading(d.summary);
          const cut = truncationSentence(d);
          const groups = groupRows(rows);
          // 三张表共用一份交叉高亮：行号必须是**跨组的连续序号**，否则三个组的第 1 行会一起亮。
          let seq = -1;
          const ordered = groups.map(g => ({ board: g.board, rows: tsSort.apply(g.rows) }));
          const idx = new Map<TrendRow, number>();
          ordered.forEach(g => g.rows.forEach(r => idx.set(r, ++seq)));
          const noDef = (k: string) => (d.definitions ?? {})[k]
            ?? `后端 definitions 里没有 ${k} 这一条 —— 这一列的含义没有出处，页面不自己编`;
          const Th = (label: string, key: string) => (
            <SortHeader label={label} sortKey={key} dir={tsSort.dirOf(key)}
              onSort={() => { ts.clearAll(); tsSort.toggle(key); }} title={noDef(key)} />
          );

          return (
            <>
              <div className="mb-2 grid grid-cols-3 gap-2">
                {[
                  { label: "交易日", value: dash(d.date) },
                  { label: "选股周期", value: `近 ${dash(d.lookback)} 个交易日` },
                  { label: "三组合计命中", value: `${dash(d.selected_total)} 只` },
                ].map(c => (
                  <GlassCard key={c.label} className="py-2 text-center">
                    <div className="text-[11px] text-muted-foreground">{c.label}</div>
                    <div className="mt-0.5 font-mono text-base font-bold text-primary">{c.value}</div>
                  </GlassCard>
                ))}
              </div>

              {st.text && (
                <p className={cn("mb-1.5 rounded-lg border p-2 text-[11px] leading-relaxed",
                  st.tone === "warn" ? "border-warning/50 bg-warning/10 text-warning"
                    : "border-border/60 bg-muted/20 text-muted-foreground")}>{st.text}</p>
              )}
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                {coverageSentence(d)}　判据版本 {dash(d.spec_version)}，这一场算于 {dash(d.calculated_at)}。
              </p>
              {cut && (
                <p className="mt-1 rounded-lg border border-warning/50 bg-warning/10 p-2 text-[11px] leading-relaxed text-warning">
                  {cut}
                </p>
              )}

              <div className="mt-1 rounded-lg border border-border/60 bg-muted/20 p-2">
                <p className={cn("text-[12px] leading-relaxed",
                  reading.tone === "warn" ? "text-warning" : "text-foreground")}>{reading.text}</p>
              </div>

              {/* 声明与字段含义收成一处默认收起的折叠块（用户 2026-10-21：
                  「把页面上的描述性内容全部收缩起来，避免过多占屏」—— 原先那九条口径解释摊在三张分组榜
                  中间，读者先撞见一屏文字才看到有没有票）。
                  **没跟着折的**：上面那句覆盖与判据版本（读数）、榜被上限截断那句 `text-warning`、
                  组内汇总那句、每组「一只都没有」的说明、`ts.popHint`；
                  缺数／截断一族的 caveat 由折叠块自己留在屏上。 */}
              <CaliberFold caveats={d.caveats ?? []} definitions={d.definitions} />

              {ordered.map(g => (
                <section key={g.board} className="mt-3">
                  <h3 className="text-[12px] font-bold text-foreground">
                    {g.board}
                    <span className="ml-1 font-normal text-muted-foreground">
                      {g.rows.length ? `${g.rows.length} 只（这一屏已交出的一组）` : "当日暂无符合条件的趋势股"}
                    </span>
                  </h3>
                  {!g.rows.length ? (
                    <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                      这一组今天一只都没有 —— 后端把这一组的标题留着并交 0，
                      不是页面把它滤掉了，也不是这一组被并进了别组。
                    </p>
                  ) : (
                    <div className="mt-1 overflow-x-auto">
                      <table className="w-full text-[11px]">
                        <thead>
                          <tr className="text-left text-muted-foreground">
                            {Th("名称与代码", "security_code")}{Th("现价", "close")}{Th("涨幅", "pct_change")}
                            {Th("成交额", "amount_yi")}{Th("流通市值", "float_mv_yi")}
                            {Th("连续站上", "ma5_streak")}{Th("量价特征", "nh_10")}
                            <th className="whitespace-nowrap px-2 py-1 font-medium"
                              title={noDef("sectors")}>相关板块</th>
                          </tr>
                        </thead>
                        <tbody>
                          {g.rows.map(r => {
                            const i = idx.get(r) ?? 0;
                            return (
                              <tr key={r.security_code} {...ts.rowProps(i, r.security_code)}>
                                <td {...ts.cellProps(i, "security_code", "whitespace-nowrap px-2 py-1")}>
                                  <StockLink code={r.security_code} name={r.name} className="font-medium hover:text-primary" />{" "}
                                  <span className="font-mono text-[10px] text-muted-foreground/60">{r.security_code}</span>
                                </td>
                                <td {...ts.cellProps(i, "close", "px-2 py-1 font-mono tabular-nums")}>{dash(r.close)}</td>
                                <td {...ts.cellProps(i, "pct_change", cn("px-2 py-1 font-mono font-semibold tabular-nums",
                                  (r.pct_change ?? 0) > 0 ? "text-primary" : (r.pct_change ?? 0) < 0 ? "text-success" : ""))}>
                                  {pctText(r.pct_change)}
                                </td>
                                <td {...ts.cellProps(i, "amount_yi", "px-2 py-1 font-mono tabular-nums")}>{yiText(r.amount_yi)}</td>
                                <td {...ts.cellProps(i, "float_mv_yi", "px-2 py-1 font-mono tabular-nums")}>{yiText(r.float_mv_yi)}</td>
                                <td {...ts.cellProps(i, "ma5_streak", "px-2 py-1 font-mono tabular-nums")}>{dash(r.ma5_streak)} 天</td>
                                <td {...ts.cellProps(i, "nh_10", "px-2 py-1")}>
                                  {featureTags(r, d.lookback ?? null).map(t => <Tag key={t.label} {...t} />)}
                                </td>
                                <td className="px-2 py-1 text-muted-foreground">{sectorTags(r.sectors)}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>
              ))}

              {/* 弹窗被拦这句不许折进说明框：收着等于没说（且要排在任何折叠框之前，
                  `test/trendStocks.test.ts` 按源码先后钉这一条）。
                  "怎么点"与取数口径两句描述收进下面的说明框（用户 2026-10-19 夜：「把选项留出，描述内容都收缩起来」） */}
              {ts.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{ts.popHint}</p>}
              <ExplainBox title="这一张表怎么点 · 取数口径（后端原句）">
                <p className="text-[11px] leading-relaxed text-muted-foreground">{STOCK_ROW_HINT}</p>
                {d.caliber && (
                  <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
                    取数口径（后端原句，页面未改写）：{plain(d.caliber)}
                  </p>
                )}
              </ExplainBox>
              <ExplainBox title="这一屏与板块卡片里的「趋势强势股」不是同一份名单">
                <p>
                  那一块<b className="text-foreground">固定看 5 天，而且可以包含当天涨停的票</b>；
                  这一屏可选 5 或 10 天，并<b className="text-foreground">排除当天涨停</b>。
                  两份数对不上是口径不同，不是有一边算错。跨板块也不并成一列排名 ——
                  主板 10%、创业板与科创板 20%，档位不同，所以这里始终分三组。
                </p>
              </ExplainBox>
            </>
          );
        }}
      </Panel>
    </div>
  );
}

