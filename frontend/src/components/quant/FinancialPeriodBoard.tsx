import { RefreshCw } from "lucide-react";
import { Caliber } from "@/components/ui/Caliber";
import { CaliberFold, ExplainBox, Panel } from "@/components/ui/DataPanel";
import { GlassCard } from "@/components/ui/GlassCard";
import { SortHeader } from "@/components/ui/SortHeader";
import { useNonce, useProbe } from "@/lib/probe";
import { useTableSort } from "@/lib/useSort";
import { usePageAskAi } from "@/lib/workspace/pageAskAi";
import { dash } from "@/lib/datastate";
import { cn } from "@/lib/utils";
import {
  fetchedAgeSentence, missingKindSentence, nextQuarterReading, nextYearReading, periodLabel,
  pct, thisYearReading, yi, type ForecastMethod, type FundamentalsPayload, type PeriodRow,
} from "@/lib/financialPeriod";

/**
 * 财务多期与外推（用户 2026-10-16：「近 5 年的都分析一下，季报和年报，营业额和利润，
 * 然后预测各下一季度或者当年、明年的情况，根据之前的加权计算预测一下」）。
 *
 * 判据一条都不在这一屏：三条外推只住 `app/compute/financial_forecast.py`（`finfc-1`），
 * 字段义与口径只住 `duanxian/stock_fundamentals.py`。界面负责守住四件事：
 *  · 累计档与单季档**不并排、不相减**（两档各自的同比，两个尺子）；
 *  · 外推**不是分析师预期**，两法分歧时并排念两法，不平均成一个数；
 *  · 缺数是「—」并带后端的理由，不出现 0；
 *  · 那份财务有多旧按小时/天念出来，源断供时它停在最后一场。
 * 列头排序的 `sortKey` 一律等于这一格里真的读出来的字段名。
 */

const METRICS = [
  { key: "revenue", label: "营业总收入" },
  { key: "net_profit", label: "净利润" },
] as const;

export function FinancialPeriodBoard({ code }: { code: string | null }) {
  const [nonce, refresh] = useNonce();
  const state = useProbe<FundamentalsPayload>("/stock/fundamentals", nonce, { code: code ?? "" }, !code);
  const sort = useTableSort({ key: "period", dir: "desc" });
  const d = state.kind === "ok" ? state.data : null;

  const rows = d?.quarters ?? [];
  usePageAskAi(rows.length ? {
    label: `财务多期与外推（${code}，最近报告期 ${dash(d?.latest_report_date)}）`,
    context: [
      `本机读到 ${dash(d?.rows_available)} 行（单季 ${dash(d?.quarters_available)} 期、年报 ${d?.annual?.length ?? 0} 份），`
      + `档位 ${(d?.kinds ?? []).join("+") || "—"}。${fetchedAgeSentence(d?.fetched_age_hours)}。`,
      "金额单位亿元；累计档（年初到报告期）与单季档不可混用，同比也是各档各自的。",
      rows.slice(0, 12).map(r => `${periodLabel(r.period)}(${r.period}) 营收 ${yi(r.revenue)} `
        + `同比 ${pct(r.revenue_yoy)}（源口径 ${pct(r.revenue_yoy_src)}）环比 ${pct(r.revenue_qoq)} `
        + `净利 ${yi(r.net_profit)} 同比 ${pct(r.net_profit_yoy)} 扣非 ${yi(r.deduct_profit)} `
        + `毛利率 ${pct(r.gross_margin)} 净利率 ${pct(r.net_margin)} 单季ROE ${pct(r.roe_single)}`).join("\n"),
      METRICS.map(m => {
        const b = d?.forecast?.[m.key];
        if (!b) return "";
        return `${m.label}外推：${nextQuarterReading(b.next_quarter)}；`
          + `${thisYearReading(b.this_year)}；${nextYearReading(b.next_year)}`;
      }).filter(Boolean).join("\n"),
      (d?.caveats ?? []).map(c => `· ${c.text ?? ""}`).join("\n"),
      "这三格是历史加权外推，不是券商一致预期、不是公司公告、更不是买卖依据。",
    ].filter(Boolean).join("\n"),
    suggestions: ["近几季增速与往年这一季的季节形状是不是在打架", "按这两条腿的分歧看，哪一格最值得盯披露"],
  } : null);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold">财务多期与外推</h2>
        <Caliber text={
          "近 5 份年报 + 近 20 个单季逐期看营业额与利润，再按加权外推三格：下一季 / 当年 / 明年。\n"
          + "外推不是分析师预期，也不是买卖依据；两法分歧大就不给单一数。完整口径见表下那句「取数口径（后端原句）」。"
        } />
        <button type="button" onClick={refresh} title="重新读本机财务表"
          className="ml-auto flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>

      <Panel title="逐期 · 累计档与单季档 · 三格外推" state={state}
        note="金额一律亿元、比率百分数；缺的格子是「—」并带后端给的原因，不补 0。">
        {p => {
          const qRows = sort.apply(p.quarters ?? []);
          const kindsMiss = missingKindSentence(p.kinds);
          const Th = (label: string, key: keyof PeriodRow & string) => (
            <SortHeader label={label} sortKey={key} dir={sort.dirOf(key)}
              onSort={() => sort.toggle(key)} title={p.definitions?.[key] ?? `后端 definitions 里没有 ${key} 这一条，页面不自己编`} />
          );
          return (
            <>
              <div className="mb-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[
                  { k: "最近报告期", v: periodLabel(p.latest_report_date), t: p.definitions?.period },
                  { k: "单季期数", v: `${dash(p.quarters_available)} 期`, t: p.definitions?.quarters_available },
                  { k: "年报份数", v: `${p.annual?.length ?? 0} 份（这一屏只摆近 5 份）`, t: "逐期表交近 5 份年报；外推用的年报序列也是最近这几份" },
                  { k: "这份财务多旧", v: dash(p.fetched_at)?.slice(0, 10), t: p.definitions?.fetched_at },
                ].map(c => (
                  <GlassCard key={c.k} className="py-2 text-center">
                    <div className="text-[11px] text-muted-foreground" title={c.t ?? ""}>{c.k}</div>
                    <div className="mt-0.5 font-mono text-base font-bold text-primary">{c.v}</div>
                  </GlassCard>
                ))}
              </div>
              <p className="mb-2 text-[11px] leading-relaxed text-muted-foreground">
                {fetchedAgeSentence(p.fetched_age_hours)}。{p.period_kind_note} 单位：{p.unit}
              </p>
              {kindsMiss && (
                <p className="mb-2 rounded-lg border border-warning/50 bg-warning/10 p-2 text-[11px] leading-relaxed text-warning">
                  {kindsMiss}
                </p>
              )}

              {/* 三格外推：营收一叠、净利一叠，各自三格 */}
              {METRICS.map(m => {
                const b = p.forecast?.[m.key];
                if (!b) return null;
                const nq = nextQuarterReading(b.next_quarter);
                const legs: [string, ForecastMethod | null | undefined][] = [
                  ["A 同比外推", b.next_quarter.method_a], ["B 季节环比", b.next_quarter.method_b]];
                return (
                  <section key={m.key} className="mb-3 rounded-lg border border-border/60 bg-muted/20 p-2">
                    <h3 className="text-[12px] font-bold text-foreground">{m.label}的三格外推</h3>
                    <p className={cn("mt-1 text-[11px] leading-relaxed",
                      nq.tone === "warn" ? "text-warning" : nq.tone === "missing" ? "text-muted-foreground" : "text-foreground")}>
                      {nq.sentence}
                    </p>
                    {(b.next_quarter.method_a || b.next_quarter.method_b) && (
                      <div className="mt-1 grid gap-1 sm:grid-cols-2">
                        {legs.map(([lab, v]) => {
                          if (!v) return null;
                          return (
                            <div key={lab} className="rounded border border-border/50 bg-background/40 px-2 py-1 text-[11px]">
                              <b className="text-foreground">{lab}</b>：{yi(v.value)}
                              <span className="text-muted-foreground">
                                {" "}（基期 {periodLabel(v.base_period)}，
                                {v.growth_percent != null ? `增速 ${pct(v.growth_percent)}`
                                  : v.season_ratio != null ? `季节比 ${v.season_ratio}` : ""}）
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    )}
                    <p className="mt-1 text-[11px] leading-relaxed text-foreground">{thisYearReading(b.this_year)}</p>
                    {b.this_year.q4_caveat && (
                      <p className="mt-1 rounded border border-warning/50 bg-warning/10 p-1.5 text-[11px] leading-relaxed text-warning">
                        {b.this_year.q4_caveat}
                      </p>
                    )}
                    <p className="mt-1 text-[11px] leading-relaxed text-foreground">{nextYearReading(b.next_year)}</p>
                    {(b.next_year.band_note || b.next_quarter.band_note) && (
                      <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
                        {[b.next_quarter.band_note, b.next_year.band_note].filter(Boolean).join("　|　")}
                      </p>
                    )}
                  </section>
                );
              })}

              <h3 className="mb-1 text-[12px] font-bold text-foreground">
                近 {qRows.length} 个单季（本屏交出的这一段）
                <span className="ml-1 font-normal text-muted-foreground">
                  排序点列头；缺数那一档两个方向都排最后
                </span>
              </h3>
              <div className="overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-left text-muted-foreground">
                      {Th("报告期", "period")}{Th("营收(亿)", "revenue")}{Th("同比", "revenue_yoy")}
                      {Th("源同比", "revenue_yoy_src")}{Th("环比", "revenue_qoq")}
                      {Th("净利(亿)", "net_profit")}{Th("净利同比", "net_profit_yoy")}
                      {Th("扣非(亿)", "deduct_profit")}{Th("毛利率", "gross_margin")}
                      {Th("净利率", "net_margin")}{Th("单季ROE", "roe_single")}
                    </tr>
                  </thead>
                  <tbody>
                    {qRows.map(r => (
                      <tr key={r.period} className="border-t border-border/40">
                        <td className="whitespace-nowrap px-2 py-1">{periodLabel(r.period)}</td>
                        <td className="px-2 py-1 font-mono tabular-nums">{yi(r.revenue)}</td>
                        <td className={cn("px-2 py-1 font-mono tabular-nums", (r.revenue_yoy ?? 0) > 0 ? "text-primary"
                          : (r.revenue_yoy ?? 0) < 0 ? "text-success" : "")}>{pct(r.revenue_yoy)}</td>
                        <td className="px-2 py-1 font-mono tabular-nums text-muted-foreground">{pct(r.revenue_yoy_src)}</td>
                        <td className="px-2 py-1 font-mono tabular-nums">{pct(r.revenue_qoq)}</td>
                        <td className="px-2 py-1 font-mono tabular-nums">{yi(r.net_profit)}</td>
                        <td className={cn("px-2 py-1 font-mono tabular-nums", (r.net_profit_yoy ?? 0) > 0 ? "text-primary"
                          : (r.net_profit_yoy ?? 0) < 0 ? "text-success" : "")}>{pct(r.net_profit_yoy)}</td>
                        <td className="px-2 py-1 font-mono tabular-nums">{yi(r.deduct_profit)}</td>
                        <td className="px-2 py-1 font-mono tabular-nums">{pct(r.gross_margin)}</td>
                        <td className="px-2 py-1 font-mono tabular-nums">{pct(r.net_margin)}</td>
                        <td className="px-2 py-1 font-mono tabular-nums">{pct(r.roe_single)}</td>
                      </tr>
                    ))}
                    {!qRows.length && (
                      <tr><td colSpan={11} className="px-2 py-2 text-[11px] text-muted-foreground">
                        这一叠是空的 —— 本机只有累计档、没有单季档（源那一发没拿到）时就会这样，
                        不等于这只票没季报。
                      </td></tr>
                    )}
                  </tbody>
                </table>
              </div>

              <h3 className="mb-1 mt-3 text-[12px] font-bold text-foreground">年报（累计档，近 {p.annual?.length ?? 0} 份）</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-left text-muted-foreground">
                      <th className="px-2 py-1 font-medium">报告期</th><th className="px-2 py-1 font-medium">营收(亿)</th>
                      <th className="px-2 py-1 font-medium">同比</th><th className="px-2 py-1 font-medium">净利(亿)</th>
                      <th className="px-2 py-1 font-medium">净利同比</th><th className="px-2 py-1 font-medium">扣非(亿)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(p.annual ?? []).map(r => (
                      <tr key={r.period} className="border-t border-border/40">
                        <td className="whitespace-nowrap px-2 py-1">{periodLabel(r.period)}</td>
                        <td className="px-2 py-1 font-mono tabular-nums">{yi(r.revenue)}</td>
                        <td className="px-2 py-1 font-mono tabular-nums">{pct(r.revenue_yoy)}</td>
                        <td className="px-2 py-1 font-mono tabular-nums">{yi(r.net_profit)}</td>
                        <td className="px-2 py-1 font-mono tabular-nums">{pct(r.net_profit_yoy)}</td>
                        <td className="px-2 py-1 font-mono tabular-nums">{yi(r.deduct_profit)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* 后端声明 + 表下那两句散文（取数口径原句、字段义）收成一处默认收起的折叠块
                  （用户 2026-10-21：「把页面上的描述性内容全部收缩起来，避免过多占屏」——
                  这两段是口径解释，不是读数，原先各占一整行摊在两叠表下面）。
                  **没跟着折的**：上面那四格读数、「这份财务多旧」那句、`kinds` 缺档与 Q4 更粗那两句
                  `text-warning`；缺数／截断一族的 caveat 由折叠块自己留在屏上。 */}
              <CaliberFold caveats={p.caveats ?? []} definitions={p.definitions}
                notes={[p.caliber ? `取数口径（后端原句，页面未改写）：${p.caliber}` : null]} />
              <ExplainBox title="这三格外推到底是什么、不是什么">
                <p>
                  三条腿各自可反驳：
                  <b className="text-foreground">下一季</b>＝上年同季 ×（1 + 近 4 季单季同比的半衰加权），
                  再用近 3 年同一季的季节环比校验，两法相差超过 25% 就<b className="text-foreground">不给单一数</b>；
                  <b className="text-foreground">当年</b>＝已披露累计原样保留 + 未披露的各季逐季补（含 Q4 时那一格更粗）；
                  <b className="text-foreground">明年</b>＝0.4×近 5 年年报 CAGR + 0.6×近 3 年 CAGR，
                  旁边的带子是<b className="text-foreground">近 5 份年报同比的 25/75 分位情景带</b>，
                  与点估计取并，<b className="text-foreground">不是置信区间</b>。
                  它不是券商一致预期、不是公司公告：历史里没出现过的那种变化（政策、大额减值、并表口径调整）外推看不出来。
                </p>
              </ExplainBox>
            </>
          );
        }}
      </Panel>
    </div>
  );
}
