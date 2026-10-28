import { Link } from "react-router-dom";
import { GlassCard } from "@/components/ui/GlassCard";
import { ExplainBox, QuietFold } from "@/components/ui/DataPanel";
import { describeState, dash, isQuiet, withUnit, type DataState } from "@/lib/datastate";
import { useProbe } from "@/lib/probe";

/**
 * 35 项技术指标读数（`/api/stock/indicators`，库里实算）。
 *
 * 抽成共用组件是因为映射表判 FT 的 `/stock`（个股详情）为**合并**，而"合并"的验收标准是
 * **页面上真的看得见这些字段** —— 这个端点此前 200 可用却零页面调用，属于"判了合并、只做一半"。
 * 现在个股研究与趋势评分共用同一份渲染，不出现两处口径不一致。
 */

export interface Indicator { name?: string | null; unit?: string | null; value?: number | null; decimals?: number | null }
export interface IndicatorPayload {
  available?: boolean | null; reason?: string | null;
  code?: string | null; date?: string | null; count?: number | null;
  source?: string | null; indicators?: Record<string, Indicator> | null;
}

/** 6 位代码补成接口要的形态由后端做（实测传 `000001` 即可，它自己回 `000001.SZ`） */
export function useIndicators(code: string, nonce: number): DataState<IndicatorPayload> {
  const trimmed = code.trim();
  return useProbe<IndicatorPayload>("/stock/indicators", nonce,
    trimmed ? { code: trimmed } : undefined, !trimmed);
}

export function IndicatorGrid({ state, title = "技术指标" }: { state: DataState<IndicatorPayload>; title?: string }) {
  const note = describeState(state);
  const quiet = isQuiet(state);
  const desc = "库里实算的原始读数，不含任何权重。数值与单位按后端给的来，前端不做换算；缺值显示 —，不是 0。";
  return (
    <GlassCard>
      {/* 用户 2026-10-22：后端说这一项本机没数（`available:false`）⇒ 整块收成一行，原因原句留在 summary 上；
          取到数（换一只票或补跑后重取）这一支不再渲染，等于自动展开。标题一起进 summary，不再单占一行 */}
      {!quiet && (
        <h3 className="text-sm font-bold">
          {title}{state.kind === "ok" && state.data.count ? `（${state.data.count} 项）` : ""}
        </h3>
      )}
      {quiet && <QuietFold title={title} text={note} note={desc} />}
      {!quiet && <p className="mb-2 mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{desc}</p>}
      {!quiet && note && <p className="text-[12px] leading-relaxed text-warning">{note}</p>}
      {state.kind === "ok" && state.data.available !== false && (
        <>
          <p className="mb-2 font-mono text-[11px] text-muted-foreground">
            {dash(state.data.code)} · {dash(state.data.date)}
            {state.data.source ? ` · ${state.data.source}` : ""}
            {" · "}日频收盘后算，盘中看到的是上一场的值
          </p>
          <div className="grid gap-x-6 gap-y-0.5 sm:grid-cols-2">
            {/* 顺序按后端交出的来，不重排也不按名字前缀分组 —— 那种类目是我造的，不是数据自带的分类 */}
            {Object.entries(state.data.indicators ?? {}).map(([key, v]) => (
              <p key={key} className="text-[11px] leading-relaxed">
                <span className="text-muted-foreground">{dash(v.name)}：</span>
                <span className="font-mono tabular-nums" title={`原始字段 ${key}${v.unit && v.unit !== "none" ? ` · unit=${v.unit}` : ""}`}>
                  {withUnit(v.value, v.unit, v.decimals)}
                </span>
              </p>
            ))}
          </div>
          <ExplainBox title="去哪看加权分 · 窗口斜率 · 所属分组">
            <p>
              八项能力加权分与它的窗口斜率在
              <Link className="text-primary underline" to="/grouped">分组自选</Link>
              —— 选中一只票后，量化档案下面那一叠就是（原「趋势评分」独立屏 10-14 已并进去）；
              它属于哪一组也在同一页。
            </p>
          </ExplainBox>
        </>
      )}
    </GlassCard>
  );
}
