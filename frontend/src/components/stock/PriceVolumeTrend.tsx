import { RefreshCw } from "lucide-react";
import { Caliber } from "@/components/ui/Caliber";
import { Caveats, Definitions, ExplainBox, Panel } from "@/components/ui/DataPanel";
import { GlassCard } from "@/components/ui/GlassCard";
import { useNonce, useProbe } from "@/lib/probe";
import { usePageAskAi } from "@/lib/workspace/pageAskAi";
import { dash } from "@/lib/datastate";
import { cn } from "@/lib/utils";
import {
  barLabel, barTooltip, isUp, maPoints, numOrNull, pctText, price, priceExtent,
  seriesCaption, volMax, volText, amtText,
  type PvBar, type PvPayload, type PvSeries,
} from "@/lib/priceVolumeTrend";

/**
 * 量价走势（用户 2026-10-16：「日线、周线、月线分别为一块，占界面左中右三块」）。
 *
 * 三块并排 = `md:grid-cols-3`（这一屏中间列最多只有 1024px，再用 `lg` 就等于在常见宽度下永远叠成一列；
 * 窄到 768px 以下自动叠成上下，不横向滚）。每块上图下量、
 * 悬停给那一根的完整读数（复权与不复权两个收价都给），下面一行读数不悬停也看得见。
 * 三条不许糊掉的线（都由后端交出，页面只照做）：
 *  · 图是**前复权形状**（以最新一场为基准），不是历史名义价 —— 每根另给 `close_raw`；
 *  · 最后一根常常"本期未走完"，图上虚线 + 文字标出来；
 *  · 根数不足（<6）那一块不连线，只列读数 —— 三个点连出来的"趋势"是骗人的。
 */

const W = 320, PH = 118, VH = 34;     // viewBox 尺寸：价格区 + 量区（单位是 viewBox，不是像素）

const MA_LABEL: Record<string, string> = { ma3: "MA3", ma5: "MA5", ma6: "MA6", ma10: "MA10", ma20: "MA20" };

function Chart({ series, kind }: { series: PvSeries | null | undefined; kind: "day" | "week" | "month" }) {
  const rows = (series?.rows ?? []) as PvBar[];
  const ext = priceExtent(rows, series?.ma_keys ?? []);
  const vmax = volMax(rows);
  if (!rows.length) {
    return <p className="text-[11px] leading-relaxed text-muted-foreground">这一块一根都没有 —— 本机日线没覆盖到它。</p>;
  }
  if (series?.too_few || !ext || !vmax) {
    return (
      <div className="space-y-1">
        <p className="text-[11px] leading-relaxed text-warning">
          只有 {rows.length} 根（&lt;{numOrNull(series?.min_bars) ?? 6} 根）⇒ 不画线，只把读数按序列出来。
        </p>
        <ul className="max-h-[150px] overflow-y-auto text-[10px] leading-relaxed text-muted-foreground">
          {rows.map(r => (
            <li key={r.date} className="font-mono">
              {barLabel(r, kind)} 收 {price(r.close)} 涨跌 {pctText(r.pct)} 量 {volText(r.volume_wan_shares)}
            </li>
          ))}
        </ul>
      </div>
    );
  }
  const n = rows.length;
  const step = W / n;
  const bw = Math.max(1.4, step * 0.62);
  const x = (i: number) => i * step + step / 2;
  const y = (v: number) => PH - ((v - ext.lo) / (ext.hi - ext.lo || 1)) * PH;
  const vy = (v: number) => VH - Math.min(1, v / (vmax || 1)) * (VH - 2);
  const last = rows[n - 1];

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${PH + VH + 6}`} className="w-full" role="img"
        aria-label={`${kind === "day" ? "日线" : kind === "week" ? "周线" : "月线"}量价图，${rows.length} 根`}>
        {/* 价格区：先画网格与均线，再画柱子（K 线要压在线上面） */}
        {[0.25, 0.5, 0.75].map(f => (
          <line key={f} x1="0" x2={W} y1={PH * f} y2={PH * f} stroke="currentColor"
            className="text-border" strokeWidth="0.4" strokeDasharray="2 3" opacity="0.5" />
        ))}
        {(series?.ma_keys ?? []).map((k, i) => (
          <polyline key={k} fill="none" strokeWidth="0.9"
            className={i === 0 ? "text-primary" : "text-warning"}
            points={maPoints(rows, k, x, y)} />
        ))}
        {rows.map((r, i) => {
          const up = isUp(r);
          const bodyTop = y(Math.max(numOrNull(r.open) ?? 0, numOrNull(r.close) ?? 0));
          const bodyBot = y(Math.min(numOrNull(r.open) ?? 0, numOrNull(r.close) ?? 0));
          const stroke = up === null ? "text-muted-foreground/50" : up ? "text-danger" : "text-success";
          return (
            <g key={r.date} className="text-foreground">
              <line x1={x(i)} x2={x(i)} y1={y(numOrNull(r.high) ?? 0)} y2={y(numOrNull(r.low) ?? 0)}
                stroke="currentColor" className={stroke} strokeWidth="0.6" />
              <rect x={x(i) - bw / 2} y={bodyTop} width={bw}
                height={Math.max(0.6, bodyBot - bodyTop)}
                className={cn(stroke, r.partial && "opacity-70")}
                fill="currentColor" fillOpacity={up === false ? 0.9 : 0.25}
                stroke="currentColor" strokeWidth="0.5"
                strokeDasharray={r.partial ? "1.6 1.2" : undefined} />
            </g>
          );
        })}
        {/* 量柱：与上面同一列对齐，颜色跟那根 K 线的涨跌一致 */}
        <line x1="0" x2={W} y1={PH + 6} y2={PH + 6} stroke="currentColor" className="text-border" strokeWidth="0.5" />
        {rows.map((r, i) => {
          const up = isUp(r);
          const v = numOrNull(r.volume_wan_shares) ?? 0;
          return (
            <rect key={r.date} x={x(i) - bw / 2} width={bw}
              y={PH + 6 + vy(v)} height={Math.max(0.4, VH - vy(v))}
              className={cn(up === null ? "text-muted-foreground" : up ? "text-danger" : "text-success")}
              fill="currentColor" fillOpacity={0.42} />
          );
        })}
        {/* 每根的悬停（整列宽的一条透明矩形，好点） */}
        {rows.map((r, i) => (
          <rect key={`hit-${r.date}`} x={i * step} y="0" width={step} height={PH + VH + 6}
            fill="transparent">
            <title>{barTooltip(r, kind, barLabel(r, kind))}</title>
          </rect>
        ))}
        {/* 左右两个日期 + 最新一根的价位刻度，让人不用悬停也有坐标感 */}
        <text x="1" y={PH + VH + 4} className="fill-current text-[7px] text-muted-foreground">{barLabel(rows[0], kind)}</text>
        <text x={W - 1} y={PH + VH + 4} textAnchor="end"
          className="fill-current text-[7px] text-muted-foreground">{barLabel(last, kind)}</text>
        <text x={W - 1} y={9} textAnchor="end" className="fill-current text-[7px] text-muted-foreground">
          高 {price(ext.hi)}
        </text>
        <text x={W - 1} y={PH - 2} textAnchor="end" className="fill-current text-[7px] text-muted-foreground">
          低 {price(ext.lo)}
        </text>
      </svg>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-muted-foreground">
        {(series?.ma_keys ?? []).map((k, i) => (
          <span key={k} className="flex items-center gap-1">
            <span className={cn("inline-block h-0.5 w-3", i === 0 ? "bg-primary" : "bg-warning")} />
            {MA_LABEL[k] ?? k}
          </span>
        ))}
        <span>{seriesCaption(series, kind)}</span>
      </div>
    </div>
  );
}

export function PriceVolumeTrend({ code }: { code: string | null }) {
  const [nonce, refresh] = useNonce();
  const state = useProbe<PvPayload>("/stock/price-volume-trend", nonce, { code: code ?? "" }, !code);
  const d = state.kind === "ok" ? state.data : null;

  // 登记放组件顶层：`Panel` 的 render-prop 只在有数时画，hook 放进去等于按数据改变调用次数。
  const ctx = d?.available ? [
    `${d.name ?? code}（${d.code}）的量价走势，最新一场 ${dash(d.as_of)}。`,
    (["daily", "weekly", "monthly"] as const).map(k => {
      const s = d[k]; const rows = s?.rows ?? []; const last = rows[rows.length - 1];
      return `${k === "daily" ? "日线" : k === "weekly" ? "周线" : "月线"}：画 ${rows.length} 根（本机共 ${dash(s?.bars_total)} 根），`
        + `末根 ${dash(last?.date)} 收 ${price(last?.close)}（不复权 ${price(last?.close_raw)}）涨跌 ${pctText(last?.pct)}、`
        + `量 ${volText(last?.volume_wan_shares)}、额 ${amtText(last?.amount_yi)}${last?.partial ? "（本期未走完）" : ""}`;
    }).join("\n"),
    `区间共 ${dash(d.monthly?.bars_total)} 根月线（本机日线最早 2021-08-02）；图上价是前复权形状。`,
    (d.caveats ?? []).map(c => `· ${c.text ?? ""}`).join("\n"),
    "这是客观历史走势读数，不给买卖建议。",
  ].join("\n") : null;
  usePageAskAi(ctx ? {
    label: `量价走势（${d?.name ?? code}：日 / 周 / 月）`,
    context: ctx,
    suggestions: ["月线形状与周线均线现在的关系是什么", "最后一根未走完的那期，量比上一整期放大还是缩小"],
  } : null);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold">量价走势（日线 · 周线 · 月线）</h2>
        <Caliber text={
          "三块都只读本机日线表，周线与月线由日线聚合（开=本期首根开、高=最高、低=最低、收=末根收，量额相加）。\n"
          + "价格是前复权形状（以最新一场为基准），历史那根的悬停里另给不复权收；区间涨跌比的是上一期的复权收。\n"
          + "最后一根常常是「至今」（本周未收 / 本月未满），图上用虚线框标出来。完整口径见表下那句「取数口径（后端原句）」。"
        } />
        <button type="button" onClick={refresh} title="重新读本机日线"
          className="ml-auto flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>

      <Panel title="左：日线 中：周线 右：月线（同一只票、同一个最新一场）" state={state}
        note="上图下量；悬停给那一根的完整读数（复权与不复权两个收价都给）。窄屏自动叠成一列。">
        {p => (
          <>
            <div className="grid gap-2 md:grid-cols-3">
              {([
                { key: "daily", title: "日线", sub: `近 ${dash(p.want?.daily)} 根`, series: p.daily },
                { key: "weekly", title: "周线", sub: `近 ${dash(p.want?.weekly)} 根`, series: p.weekly },
                { key: "monthly", title: "月线", sub: `近 ${dash(p.want?.monthly)} 根`, series: p.monthly },
              ] as const).map(b => (
                <GlassCard key={b.key} className="p-2">
                  <div className="mb-1 flex items-baseline gap-1.5">
                    <h3 className="text-[12px] font-bold text-foreground">{b.title}</h3>
                    <span className="text-[10px] text-muted-foreground">
                      {b.sub}（本机共 {dash(b.series?.bars_total)} 根）
                    </span>
                  </div>
                  <Chart series={b.series} kind={b.key === "daily" ? "day" : b.key === "weekly" ? "week" : "month"} />
                </GlassCard>
              ))}
            </div>

            <Caveats items={p.caveats ?? []} />
            {p.caliber && (
              <p className="mt-2 border-t border-dashed border-border pt-2 text-[10px] leading-relaxed text-muted-foreground">
                取数口径（后端原句，页面未改写）：{p.caliber}
              </p>
            )}
            <Definitions map={p.definitions} />
            <ExplainBox title="为什么图上那根「收」可能不是你记忆里的那个价">
              <p>
                三块共用<b className="text-foreground">前复权</b>形状：本机把每根日线乘上
                <b className="text-foreground">复权因子</b>（最新一场＝1，越老越小），
                于是历史上那根的复权收会明显低于它当天真成交的价 —— 这样几十个月的涨跌才连得起来读；
                分红除权不折的话，月线会被砸出一个根本不存在的「暴跌」。
                要看当天真成交的价，悬停里那一列是不复权的收价；
                周线与月线的"涨跌"是<b className="text-foreground">与上一期收比</b>，不是与本期开盘比。
              </p>
            </ExplainBox>
          </>
        )}
      </Panel>
    </div>
  );
}
