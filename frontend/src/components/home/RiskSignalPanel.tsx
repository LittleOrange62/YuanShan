import { RefreshCw, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { Caliber } from "@/components/ui/Caliber";
import { CaliberFold, QuietFold } from "@/components/ui/DataPanel";
import { GlassCard } from "@/components/ui/GlassCard";
import { useNonce, useProbe } from "@/lib/probe";
import { describeState } from "@/lib/datastate";
import { plain } from "@/lib/agent";
import { cn } from "@/lib/utils";
import {
  GROUP_LABELS, NULL_TEXT, caveatText, densityScale, divergenceScale, extremeLine, extremeMarks,
  fin, hoverLine, latestDivergence, levelTextTone, levelTone, numText, optionSources, periodTooltip,
  thresholdsText, trendLine, trendText, turnLine, turnMarks, xTicks,
  type RiskExtremeRow, type RiskPanel, type RiskPeriod, type RiskTurn,
} from "@/lib/riskSignal";

/**
 * 首页「风险提示」（用户 2026-10-20 夜点名要这么一块，原话"放在首页——盘面数据下面"；
 * 2026-10-27 用户点名**搬进「盘面数据」那一屏、摆在异动提醒上面**，挂载点在
 * `MarketPanorama.tsx` 的 4c-0，`SummaryBoard` 里不留第二份）：
 * 市场宽度（涨跌家数）× 阿姆斯（上涨票成交额 ÷ 下跌票成交额）两条折线 + 区间背离条。
 *
 * 这一屏只读 `/api/quant/risk-signal`（读数层与判据只住 `duanxian/risk_signal.py`，
 * 数据出处 `analytics.market_breadth`，写侧由加工链的 `breadth`/d6 步落库）。
 * 界面上不许糊掉的线：
 *  · 档位阈值与档位文字读 `thresholds`/`levels` —— **页面不写死 40/20/−40、不自己分档**；
 *  · 分位/背离算不出（样本不满 20 期、成员日缺数、列没回填）一律 `—`：
 *    不补 50、不把 null 落成"无明显背离"，并在旁边说清是哪种"判不了"；
 *  · 阿姆斯读不到时宽度线照常画（不陪绑），黄条原句转 `arms.reason`
 *    （里面就是 `python -m app.compute.breadth --backfill` 那句自救命令）；
 *  · **横轴必须有日期刻度，且悬停那两行的完整读数要停在屏上**（用户 2026-10-21：
 *    "图表中要能看得到具体的日期啊"）—— 原生 `<title>` 只算补充，不算交付；
 *  · **极端点位是后端判据交出来的**（`extremes`：量价背离 / 宽度崩塌两类），
 *    页面只按 `period` 找位置画标记、只拼句子，不自己筛、不自己算阈值，
 *    命中多少 / 被上限挤掉多少 / 判不了的期数，三样都摆在折叠外面；
 *  · **趋势与转折点同样只认后端交的**（用户 2026-10-26：「这个图看不出趋势和转折点」）：
 *    上半加一条 `up_ratio_ma` 平滑线并在它上面标 `trend.rows` 的 zigzag 拐点，
 *    背离条改成按窗口实际极值取量程（原来按 ±100 硬映射，实测取值只有 −40~+45 ⇒ 半高中位 2.1 像素，
 *    一整条被压成一根线），下面再加一条 `risk_density` 密度线 ——
 *    页面**不对背离自己做均线、不自己找拐点**（后端量过：背离逐期独立，lag1 自相关 0.01~0.03）。
 */

const W = 760, PH = 140, GAP = 10, DH = 46, DD = 40, PADX = 10, AXIS = 12;
/** 三条带子的纵向偏移（背离条 / 密度线 / 横轴刻度），改高度只动这一处 */
const TOP_D = PH + GAP;
const TOP_N = PH + GAP + DH + GAP;
const TOP_AX = TOP_N + DD + 3;
const PLOT_H = TOP_N + DD;

function segPoints(rows: RiskPeriod[], get: (p: RiskPeriod) => number | null,
                   x: (i: number) => number, y: (v: number) => number): string[] {
  // null 断开成多段折线（读不到 ≠ 上一个点连过来）
  const segs: string[] = [];
  let cur: string[] = [];
  rows.forEach((p, i) => {
    const v = fin(get(p));
    if (v === null) {
      if (cur.length > 1) segs.push(cur.join(" "));
      cur = [];
    } else {
      cur.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`);
    }
  });
  if (cur.length > 1) segs.push(cur.join(" "));
  return segs;
}

/** 清单里的一行：标记 + 后端交的那句话 + 悬停全文（原始数永远点得到） */
function ExtremeRow({ row, onJump }: { row: RiskExtremeRow; onJump: (p: string) => void }) {
  return (
    <li className="flex items-start gap-1.5">
      <span aria-hidden
        className={cn("mt-1 inline-block h-2 w-2 shrink-0 rotate-45",
          row.kind === "collapse" ? "bg-danger" : "bg-warning")} />
      <button type="button" onClick={() => onJump(row.period)}
        title={`跳到图上这一期：${extremeLine(row)}`}
        className="text-left text-[11px] leading-relaxed text-muted-foreground hover:text-foreground">
        {extremeLine(row)}
      </button>
    </li>
  );
}

/** 转折点清单里的一行：小三角 + 后端交的那句话（点一下把悬停钉到那一期） */
function TurnRow({ row, smooth, boundary, onJump }:
  { row: RiskTurn; smooth?: number; boundary?: boolean; onJump: (p: string) => void }) {
  const line = turnLine(row, smooth, boundary);
  return (
    <li className="flex items-start gap-1.5">
      <span aria-hidden className={cn("mt-0.5 shrink-0 text-[9px] leading-none",
        row.kind === "peak" ? "text-danger" : "text-success")}>{row.kind === "peak" ? "▼" : "▲"}</span>
      <button type="button" onClick={() => onJump(row.period)} title={`跳到图上这一期：${line}`}
        className="text-left text-[11px] leading-relaxed text-muted-foreground hover:text-foreground">
        {line}
      </button>
    </li>
  );
}

export function RiskSignalPanel() {
  const [nonce, refresh] = useNonce();
  const [months, setMonths] = useState(3);
  const [group, setGroup] = useState<"day" | "week" | "month">("day");
  const [hover, setHover] = useState<number | null>(null);
  const st = useProbe<RiskPanel>("/quant/risk-signal", nonce, { months, group });
  const d = st.kind === "ok" && st.data.available ? st.data : null;
  const rows = (d?.series ?? []) as RiskPeriod[];
  const n = rows.length;

  const th = d?.thresholds;
  const legend = d ? thresholdsText(th, d.levels) : "";
  const trendLegend = trendText(d?.trend);
  const reading = d ? latestDivergence(d) : null;
  const armsOff = d && d.arms && d.arms.available === false;
  const opts = optionSources(d);
  const ticks = xTicks(rows, 6);
  const marks = d ? extremeMarks(rows, d) : { marks: [], unmatched: 0 };
  const turns = d ? turnMarks(rows, d) : { marks: [], unmatched: 0, boundary: null };
  const shown = hover === null ? null : (rows[hover] ?? null);
  const atPeriod = (p: string) => {
    const i = rows.findIndex(r => String(r.period) === String(p));
    if (i >= 0) setHover(i);
  };

  // 宽度线 0~100 固定标尺；阿姆斯线自算上下界（含 1.0 这根"钱不偏不倚"参照线）
  const x = (i: number) => PADX + (i * (W - 2 * PADX)) / Math.max(1, n - 1);
  const yW = (v: number) => PH - (v / 100) * PH;
  const armsVals = rows.map(r => fin(r.arms_ratio)).filter((v): v is number => v !== null);
  const aLo = armsVals.length ? Math.min(1, ...armsVals) : 0;
  const aHi = armsVals.length ? Math.max(...armsVals) * 1.08 : 1;
  const yA = (v: number) => PH - ((v - aLo) / ((aHi - aLo) || 1)) * PH;
  // 背离条按窗口实际极值对称取量程（下限容得下后端那三条档位线）；一期都算不出 ⇒ 不画条、改念后端那句
  const divScale = divergenceScale(rows, th);
  const yD = (v: number) => DH / 2 - (v / (divScale?.bound || 1)) * (DH / 2 - 2);
  const denScale = densityScale(rows);
  const yN = (v: number) => DD - 3 - (v / (denScale?.max || 1)) * (DD - 15);

  const widthSegs = segPoints(rows, p => fin(p.up_ratio), x, yW);
  const maSegs = segPoints(rows, p => fin(p.up_ratio_ma), x, yW);
  const armsSegs = segPoints(rows, p => fin(p.arms_ratio), x, yA);
  const denSegs = segPoints(rows, p => fin(p.risk_density), x, yN);
  const barW = Math.max(1.2, ((W - 2 * PADX) / Math.max(1, n)) * 0.62);
  const hitW = (W - 2 * PADX) / Math.max(1, n);

  const divRows = d?.extremes?.divergence?.rows ?? [];
  const colRows = d?.extremes?.collapse?.rows ?? [];
  const eTh = d?.extremes?.thresholds;
  const trendRows = d?.trend?.rows ?? [];
  const smooth = fin(d?.trend?.smooth);
  const zig = fin(d?.trend?.zigzag_pp);

  return (
    <div id="risk-signal">
    <GlassCard className="mt-4">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
          <ShieldAlert className="h-4 w-4" /> 风险提示 · 宽度 × 阿姆斯 · {d?.date ?? NULL_TEXT}
        </h3>
        <Caliber text={d?.rule
          ? plain(d.rule)
          : "档位规则由后端交（rule 字段）—— 后端没交时页面不自己复述阈值，免得出现第二份口径。"} />
        <div className="flex items-center gap-1" role="group" aria-label="展示窗口">
          {opts.months.map(m => (
            <button key={m} type="button" aria-pressed={months === m} onClick={() => { setMonths(m); setHover(null); }}
              className={cn("rounded-lg px-2 py-1 text-[11px]",
                months === m ? "bg-primary/20 text-primary" : "text-muted-foreground hover:bg-muted/40")}>
              {m} 个月
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1" role="group" aria-label="聚合档">
          {opts.groups.map(g => (
            <button key={g} type="button" aria-pressed={group === g} onClick={() => { setGroup(g as "day"); setHover(null); }}
              className={cn("rounded-lg px-2 py-1 text-[11px]",
                group === g ? "bg-primary/20 text-primary" : "text-muted-foreground hover:bg-muted/40")}>
              {GROUP_LABELS[g] ?? g}
            </button>
          ))}
        </div>
        <button type="button" onClick={() => { setHover(null); refresh(); }} title="重新取数"
          className="ml-auto flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>

      {!opts.monthsFromBackend && st.kind === "ok" && d && (
        <p className="mb-1.5 text-[11px] leading-relaxed text-warning">
          后端这次没交 `month_options`，上面那排月份是页面兜的底，可能与后端白名单不一致
          （点了被退回会在 caveats 里看见）。
        </p>
      )}

      {st.kind === "loading" && (
        <p className="text-[11px] text-muted-foreground">取数中…（读库里逐日落好的宽度与涨跌额，不现算判据）</p>
      )}
      {st.kind !== "ok" && st.kind !== "loading" && (
        <p className="rounded-lg border border-warning/50 bg-warning/10 p-2 text-[11px] leading-relaxed text-warning">
          {describeState(st)}
        </p>
      )}
      {/* 用户 2026-10-22：后端说这一项本机没数（`available:false`）⇒ 整块收成一行，原因原句留在 summary 上；
          取到数（换一只/补跑后点「重新取数」）这一支就不再渲染，等于自动展开。
          上面那支 `kind!=="ok"`（坏了／接口没接上）**照旧摊着**，那种"没有"不是这一型。 */}
      {st.kind === "ok" && !st.data.available && (
        <QuietFold
          text={plain(st.data.reason ?? "风险提示读不到数，后端也没交原因。")} />
      )}

      {d && n > 0 && (
        <>
          {/* 当前读数：背离值 + 档位 + 两侧分位；判不了就 — 并说是哪种"判不了" */}
          <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[12px]">
            <span className="text-muted-foreground">最新一期背离</span>
            <span className={cn("font-mono text-lg font-bold", reading?.levelClass)}>{reading?.value}</span>
            <span className={cn("font-medium", reading?.levelClass)}>{reading?.level}</span>
            <span className="text-[11px] text-muted-foreground/70">{reading?.note}</span>
            {group !== "day" && (
              <span className="text-[11px] text-muted-foreground/70">
                （{group === "week" ? "周" : "月"}值按成员日合计重算，家数为期内日均）
              </span>
            )}
          </div>

          {/* 趋势那一行：平滑水位 + 背离密度（措辞由 lib 按后端 trend 参数拼，页面不写死 10 期／20 期） */}
          <div className="mb-1.5 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
            <span>{trendLine(d)}</span>
            {turns.boundary && (
              <span className="text-muted-foreground/70" title="它前面没有形态可比，所以不算「转折点」，只作为量程参照">
                窗口第一期 {turns.boundary.row.period} 就是这一段的{turns.boundary.row.kind === "peak" ? "顶" : "底"}
                （均线 {numText(turns.boundary.row.ma_value, 2)}%）
              </span>
            )}
          </div>

          {/* 悬停读数停在屏上：日期打头，家数/涨停/家数比/阿姆斯/额占比/差值一排读完 */}
          <p role="status" aria-live="polite"
            className="mb-1.5 rounded-lg border border-border/50 bg-muted/30 px-2 py-1 font-mono text-[11px] leading-relaxed">
            {hoverLine(shown, group, d)}
          </p>

          {armsOff && (
            <p className="mb-2 rounded-lg border border-warning/50 bg-warning/10 p-2 text-[11px] leading-relaxed text-warning">
              {plain(d.arms?.reason ?? "阿姆斯读不到（后端没交原因）。")}
            </p>
          )}

          <svg viewBox={`0 0 ${W} ${TOP_AX + AXIS}`} className="w-full" role="img"
            aria-label={`近 ${n} 期（${d.window?.from_actual ?? ""} ~ ${d.window?.to_actual ?? ""}）：上涨占比（0~100）与阿姆斯比值两条折线，宽度上另有一条 ${smooth ?? "—"} 期均线并标了 ${turns.marks.length} 个已确认转折点；中间是背离条（量程 ±${divScale ? numText(divScale.bound, 0) : "—"}，按窗口实际极值取），下面一条是近 ${numText(fin(d.trend?.density?.window), 0)} 期背离密度；横轴日期刻度 ${ticks.map(t => t.text).join("、")}；图上另标了 ${marks.marks.length} 个极端点位`}>
            {/* 宽度轴网格 25/50/75 */}
            {[25, 50, 75].map(v => (
              <g key={v}>
                <line x1={PADX} x2={W - PADX} y1={yW(v)} y2={yW(v)} stroke="currentColor"
                  className={cn("text-border", v === 50 && "text-muted-foreground/50")}
                  strokeWidth={v === 50 ? 0.8 : 0.5} strokeDasharray="3 4" />
                <text x={W - PADX} y={yW(v) - 2} textAnchor="end"
                  className="fill-current text-[7px] text-muted-foreground/70">{v}%</text>
              </g>
            ))}
            <text x={PADX + 2} y={9} className="fill-current text-[8px] text-primary">
              上涨占比（左轴 0~100%）{maSegs.length > 0 ? `　粗线是 ${smooth ?? "—"} 期均线（趋势看它）` : ""}
            </text>
            {armsSegs.length > 0 && (
              <text x={PADX + 2} y={20} className="fill-current text-[8px] text-success">
                阿姆斯 = 涨额 ÷ 跌额（右轴 {aLo.toFixed(2)}~{aHi.toFixed(2)}）
              </text>
            )}
            {/* 阿姆斯的 1.0 参照线：钱在两边走平的那一格 */}
            {armsVals.length > 0 && 1 >= aLo && 1 <= aHi && (
              <g>
                <line x1={PADX} x2={W - PADX} y1={yA(1)} y2={yA(1)} stroke="currentColor"
                  className="text-success/60" strokeWidth={0.6} strokeDasharray="6 3" />
                <text x={W - PADX} y={yA(1) - 2} textAnchor="end"
                  className="fill-current text-[7px] text-success/80">1.0</text>
              </g>
            )}
            {widthSegs.map((pts, i) => (
              <polyline key={`w${i}`} points={pts} fill="none"
                strokeWidth={maSegs.length ? 0.9 : 1.6}
                className={maSegs.length ? "stroke-primary/45" : "stroke-primary"} />
            ))}
            {/* 均线：趋势读它（原始那条淡下来，否则日频毛刺会把均线盖住） */}
            {maSegs.map((pts, i) => (
              <polyline key={`m${i}`} points={pts} fill="none" strokeWidth={1.8} className="stroke-primary" />
            ))}
            {armsSegs.map((pts, i) => (
              <polyline key={`a${i}`} points={pts} fill="none" strokeWidth={1.4} className="stroke-success" />
            ))}
            {/* 转折点标记（后端 zigzag 判据）：贴在均线上方／下方的小三角，颜色分红（顶）绿（底） */}
            {turns.marks.map(({ i, row }) => {
              const v = fin(row.ma_value);
              const cy = v === null ? PH : yW(v);
              const peak = row.kind === "peak";
              const ty = peak ? cy - 7 : cy + 11;
              return (
                <g key={`tn-${row.kind}-${row.period}`}>
                  <text x={x(i)} y={ty} textAnchor="middle"
                    className={cn("fill-current text-[9px]", peak ? "text-danger" : "text-success")}>
                    {peak ? "▼" : "▲"}
                    <title>{turnLine(row, smooth ?? undefined)}</title>
                  </text>
                  <text x={x(i)} y={peak ? ty - 7 : ty + 8} textAnchor="middle"
                    className="fill-current text-[6px] text-muted-foreground">{row.label}</text>
                </g>
              );
            })}
            {/* 极端点位标记（后端判据）：菱形贴在宽度线上，颜色分两类 */}
            {marks.marks.map(({ i, row }) => {
              const v = fin(rows[i].up_ratio);
              const cy = v === null ? PH : yW(v);
              return (
                <g key={`ex-${row.kind}-${row.period}`}>
                  <rect x={x(i) - 2.4} y={cy - 2.4} width={4.8} height={4.8}
                    transform={`rotate(45 ${x(i)} ${cy})`}
                    className={row.kind === "collapse" ? "fill-danger" : "fill-warning"}
                    stroke="currentColor" strokeWidth={0.4}>
                    <title>{extremeLine(row)}</title>
                  </rect>
                  <text x={x(i)} y={cy - 5} textAnchor="middle"
                    className="fill-current text-[6px] text-muted-foreground">{row.label}</text>
                </g>
              );
            })}
            {/* 悬停导引线 + 两个点：让"鼠标在哪一天"与屏上那行字对得上 */}
            {shown && (
              <g>
                <line x1={x(hover as number)} x2={x(hover as number)} y1={0} y2={PLOT_H}
                  stroke="currentColor" className="text-muted-foreground/60" strokeWidth={0.6} />
                {fin(shown.up_ratio) !== null && (
                  <circle cx={x(hover as number)} cy={yW(shown.up_ratio as number)} r={2.2} className="fill-primary" />
                )}
                {fin(shown.up_ratio_ma) !== null && (
                  <circle cx={x(hover as number)} cy={yW(shown.up_ratio_ma as number)} r={2.6}
                    className="fill-primary" stroke="currentColor" strokeWidth={0.8} />
                )}
                {fin(shown.arms_ratio) !== null && (
                  <circle cx={x(hover as number)} cy={yA(shown.arms_ratio as number)} r={2.2} className="fill-success" />
                )}
              </g>
            )}
            {/* 末点加粗，方便"现在在哪"一眼看到 */}
            {fin(rows[n - 1]?.up_ratio) !== null && (
              <circle cx={x(n - 1)} cy={yW(rows[n - 1].up_ratio as number)} r={2.6} className="fill-primary" />
            )}
            {fin(rows[n - 1]?.arms_ratio) !== null && (
              <circle cx={x(n - 1)} cy={yA(rows[n - 1].arms_ratio as number)} r={2.6} className="fill-success" />
            )}
            {fin(rows[n - 1]?.up_ratio_ma) !== null && (
              <circle cx={x(n - 1)} cy={yW(rows[n - 1].up_ratio_ma as number)} r={3}
                className="fill-background stroke-primary" strokeWidth={1.4} />
            )}

            {/* 背离条：中线 0，色块按后端档位上色；量程按窗口实际极值取（原来按 ±100 硬映射 ⇒ 半高中位 2.1 像素）；
                null 不画（不是 0）；一期都算不出 ⇒ 不画条，改念后端那句「判不了」 */}
            <g transform={`translate(0,${TOP_D})`}>
              <text x={PADX + 2} y={8} className="fill-current text-[8px] text-muted-foreground">
                区间背离（宽度分位 − 阿姆斯分位；背离越大越危险）
                {divScale ? `　量程 ±${numText(divScale.bound, 0)}（本窗口实际最大 ${numText(divScale.maxAbs, 1)}）` : ""}
              </text>
              <line x1={PADX} x2={W - PADX} y1={DH / 2} y2={DH / 2} stroke="currentColor"
                className="text-border" strokeWidth={0.6} />
              {divScale && th && [th.high, th.moderate, th.reverse].map(v => (
                <g key={v}>
                  <line x1={PADX} x2={W - PADX} y1={yD(v)} y2={yD(v)} stroke="currentColor"
                    className="text-muted-foreground/30" strokeWidth={0.5} strokeDasharray="2 4" />
                  <text x={W - PADX} y={yD(v) - 1.5} textAnchor="end"
                    className="fill-current text-[6px] text-muted-foreground/60">{v}</text>
                </g>
              ))}
              {divScale && rows.map((p, i) => {
                const v = fin(p.divergence);
                if (v === null) return null;
                const h = Math.abs(yD(v) - DH / 2);
                return <rect key={p.period} x={x(i) - barW / 2} y={Math.min(DH / 2, yD(v))}
                  width={barW} height={Math.max(0.8, h)} className={levelTone(p.level)} />;
              })}
              {!divScale && (
                <text x={PADX + 2} y={DH / 2 + 10} className="fill-current text-[8px] text-warning">
                  {plain(caveatText(d, "pct_window")
                    ?? `这一窗口 ${n} 期都算不出背离（分位要满 ${numText(fin(th?.window), 0)} 期样本）—— 条子整条不画，不是「这段没有背离」。`)}
                </text>
              )}
            </g>

            {/* 背离密度：近 N 期里有几期达到中度及以上。背离本身逐期独立，密度是它唯一站得住的趋势形式 */}
            <g transform={`translate(0,${TOP_N})`}>
              <text x={PADX + 2} y={8} className="fill-current text-[8px] text-muted-foreground">
                {`背离密度（近 ${numText(fin(d.trend?.density?.window), 0)} 期里有几期达到 ${numText(fin(d.trend?.density?.threshold), 0)} 以上）`}
                {denScale ? `　本窗口最高 ${numText(denScale.max, 0)} 期` : ""}
              </text>
              <line x1={PADX} x2={W - PADX} y1={DD - 3} y2={DD - 3} stroke="currentColor"
                className="text-border" strokeWidth={0.5} />
              {denScale && (
                <>
                  <text x={W - PADX} y={yN(denScale.max) + 2} textAnchor="end"
                    className="fill-current text-[6px] text-muted-foreground/70">{denScale.max}</text>
                  <text x={W - PADX} y={DD - 4} textAnchor="end"
                    className="fill-current text-[6px] text-muted-foreground/70">0</text>
                </>
              )}
              {denSegs.map((pts, i) => (
                <polyline key={`n${i}`} points={pts} fill="none" strokeWidth={1.3} className="stroke-warning" />
              ))}
              {!denScale && (
                <text x={PADX + 2} y={DD / 2 + 6} className="fill-current text-[8px] text-warning">
                  {plain(caveatText(d, "density_unjudged")
                    ?? `密度要满 ${numText(fin(d.trend?.density?.window), 0)} 期滚动样本，这一窗口判不了 —— 不是「这段一点都不危险」。`)}
                </text>
              )}
            </g>

            {/* 横轴日期刻度：按日均有 MM-DD，跨年那格带上年份（按周/月直接用后端的 label） */}
            <g transform={`translate(0,${TOP_AX})`}>
              <line x1={PADX} x2={W - PADX} y1={0} y2={0} stroke="currentColor"
                className="text-border" strokeWidth={0.5} />
              {ticks.map(({ i, text }) => (
                <text key={`t${i}-${text}`} x={x(i)} y={AXIS - 3}
                  textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}
                  className="fill-current text-[7px] text-muted-foreground">{text}</text>
              ))}
            </g>

            {/* 每期的透明命中条：悬停/点击把那一期的完整读数钉在屏上那行里（title 只作补充） */}
            {rows.map((p, i) => (
              <rect key={`hit-${p.period}`} x={x(i) - hitW / 2} y={0}
                width={hitW} height={PLOT_H} fill="transparent"
                onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
                onClick={() => setHover(i)}>
                <title>{periodTooltip(p, d.group, d)}</title>
              </rect>
            ))}
          </svg>

          {/* 极端点位清单（判据在外部，这里只列不改）：命中数、被上限挤掉数、判不了期数都在外面 */}
          <div className="mt-1.5 rounded-lg border border-border/50 bg-muted/20 p-2">
            <p className="mb-1 text-[11px] font-semibold text-muted-foreground">
              窗口内的极端点位（后端判据自动筛，页面不自己挑）· 共 {numText(d.extremes?.periods_in_window, 0)} 期
            </p>
            {divRows.length === 0 && colRows.length === 0 ? (
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                这一窗口两类判据都没有命中（{eTh ? `背离：家数比钱多 ≥${eTh.share_gap_pp}pp 且宽度 ≥${eTh.width_min_pct}%；崩塌：上一期宽度 ≥${eTh.prev_width_min_pct}% 而一期之内掉 ≥${eTh.collapse_drop_pp}pp` : "阈值后端没交，页面不自己写"}）—— 没有命中不等于「没有风险」，窗口外的点换窗口或拉长时间档再看。
              </p>
            ) : (
              <ul className="space-y-1">
                {divRows.map(r => <ExtremeRow key={`d-${r.period}`} row={r} onJump={atPeriod} />)}
                {colRows.map(r => <ExtremeRow key={`c-${r.period}`} row={r} onJump={atPeriod} />)}
              </ul>
            )}
            <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground/70">
              量价背离命中 {numText(d.extremes?.divergence?.matched, 0)} 期（这里列 {divRows.length} 期
              {fin(d.extremes?.divergence?.dropped_by_cap) ? `，另有 ${numText(d.extremes?.divergence?.dropped_by_cap, 0)} 期既排不进前 ${numText(eTh?.max_marks, 0)} 也不在最近 20 期内` : ""}）
              · 宽度崩塌命中 {numText(d.extremes?.collapse?.matched, 0)} 期（这里列 {colRows.length} 期
              {fin(d.extremes?.collapse?.dropped_by_cap) ? `，另有 ${numText(d.extremes?.collapse?.dropped_by_cap, 0)} 期同上被挤掉` : ""}）
              {fin(d.extremes?.skipped_no_gap)
                ? ` · ${numText(d.extremes?.skipped_no_gap, 0)} 期算不出成交额占比，背离那一类对它们没发言权（不是「没背离」）` : ""}
              {marks.unmatched > 0 ? ` · ${marks.unmatched} 个点位在现序列里找不到对应期（窗口边界，图上没画）` : ""}
              。阈值只住后端 `EXTREME_THRESHOLDS`
              {eTh ? `：背离 ≥${eTh.share_gap_pp}pp 且宽度 ≥${eTh.width_min_pct}%、崩塌 ≥${eTh.collapse_drop_pp}pp 且上期宽度 ≥${eTh.prev_width_min_pct}%、每类至少标 ${eTh.max_marks} 个（最近 20 期内的命中一律保留）` : ""}。
            </p>
          </div>

          {/* 趋势转折点清单（判据在后端 zigzag，页面只按 period 找位置、只拼句子） */}
          <div className="mt-1.5 rounded-lg border border-border/50 bg-muted/20 p-2">
            <p className="mb-1 text-[11px] font-semibold text-muted-foreground">
              窗口内的趋势转折点（{smooth === null ? "均线" : `${smooth} 期均线`}上幅度
              {zig === null ? "" : ` ≥${zig}pp`} 的已确认反转，后端判据自动筛）· 共 {numText(trendRows.length, 0)} 个
            </p>
            {trendRows.length === 0 && !turns.boundary ? (
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                {plain(caveatText(d, "turns_none")
                  ?? "这一窗口没有已确认的转折点：可能这一段确实走得平，也可能末端那个极值还没被反向走够门槛（未确认的拐点一律不画）。")}
              </p>
            ) : (
              <ul className="space-y-1">
                {trendRows.map(r => <TurnRow key={`t-${r.kind}-${r.period}`} row={r} smooth={smooth ?? undefined} onJump={atPeriod} />)}
                {turns.boundary && (
                  <TurnRow key={`b-${turns.boundary.row.period}`} row={turns.boundary.row} boundary
                    smooth={smooth ?? undefined} onJump={atPeriod} />
                )}
              </ul>
            )}
            <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground/70">
              转折点只打在平滑后的水位线上：背离那一族是逐期独立的（后端量过全史 1,193 期，相邻两期的相关只有
              0.01~0.03，把分位窗口放大到 250 期也只到 0.09），对它做均线得到的拐点是噪声的平均，
              所以那一族只交密度线、不交拐点。
              {turns.unmatched > 0 ? ` · ${turns.unmatched} 个点位在现序列里找不到对应期（窗口边界，图上没画）` : ""}
            </p>
          </div>

          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
            <span>{d.window?.from_actual ?? d.window?.from ?? ""} ~ {d.window?.to_actual ?? d.window?.to ?? ""} · {n} 期</span>
            {d.coverage && <span>库里共 {numText(d.coverage.sessions, 0)} 场（{d.coverage.from} ~ {d.coverage.to}）</span>}
            {d.arms?.available && d.arms.coverage && (
              <span title="本窗口有几行阿姆斯、全库回填到哪天">
                阿姆斯本窗口 {numText(d.arms.coverage.rows_in_window, 0)} 行有数（全库回填到 {d.arms.coverage.last_backfilled ?? NULL_TEXT}）
              </span>
            )}
            {legend && <span className="w-full leading-relaxed">{legend}</span>}
            {trendLegend && <span className="w-full leading-relaxed">{trendLegend}</span>}
            {/* 档位图例的小色块与背离条同一份 level 键，不另起一套判据 */}
            {th && d.levels && (
              <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                {(["high", "moderate", "reverse", "none"] as const).map(k => (
                  <span key={k} className="flex items-center gap-1">
                    <span className={cn("inline-block h-2 w-2 rounded-sm", levelTone(k).replace("fill-", "bg-"))} />
                    <span className={levelTextTone(k)}>{d.levels?.[k]}</span>
                  </span>
                ))}
                <span className="flex items-center gap-1">
                  <span className="inline-block h-2 w-2 rotate-45 bg-warning" />极端点位·量价背离
                </span>
                <span className="flex items-center gap-1">
                  <span className="inline-block h-2 w-2 rotate-45 bg-danger" />极端点位·宽度崩塌
                </span>
                <span className="flex items-center gap-1">
                  <span className="inline-block h-0.5 w-4 bg-primary" />宽度均线（趋势看它）
                </span>
                <span className="flex items-center gap-1">
                  <span className="text-[9px] leading-none text-danger">▼</span>均线顶
                </span>
                <span className="flex items-center gap-1">
                  <span className="text-[9px] leading-none text-success">▲</span>均线底
                </span>
                <span className="flex items-center gap-1">
                  <span className="inline-block h-0.5 w-4 bg-warning" />背离密度（近 N 期几期中度以上）
                </span>
              </span>
            )}
            {th && (
              <span title="分位是相对量不是概率：只说当期在近 20 期样本里的位置">
                分位取近 {th.window} 期滚动样本；样本不足 ⇒ —
              </span>
            )}
          </div>

          <CaliberFold caveats={d.caveats} definitions={d.definitions}
            notes={[d.window ? `窗口 ${d.window.from} ~ ${d.window.to}（${d.months} 个月 · ${GROUP_LABELS[d.group ?? "day"] ?? d.group}聚合），本屏生成于 ${d.generated_at ?? "—"}。` : null]} />

          {/* 备注（用户原话）：这两条线回答的是"量能与家数是否合拍"，不给买卖动作 */}
          <p className="mt-1.5 text-[10px] leading-relaxed text-muted-foreground/60">
            看法：家数在走强（宽度分位高）而涨额没跟上（阿姆斯分位低）⇒ 背离条发红、越红越危险；
            反过来钱在进、家数没跟上 ⇒ 蓝条。图上黄菱形是「家数比钱多」的极端日、红菱形是「上一期还很宽、这一期直接掉下去」的崩塌日 ——
            两件事各有一条判据，和上面那条分位差是三把不同的尺（同一天的家数 vs 钱、各自的历史分位、期与期之间的落差），谁也不顶替谁。
            趋势怎么读：日频那条淡线毛刺很密，看不出方向，所以另画一条均线（粗线）；
            红▼／绿▲ 是均线上已被反向走够门槛的顶与底（末端还没确认的拐点一律不画，画上去就是事后诸葛）。
            背离条本身逐期独立、没有趋势可言，所以它下面另给一条密度线：近 N 期里有几期达到中度及以上 ——
            读法是「这一段密不密集」，不是「明天会不会跌」。
            这是量价配合的形状读数，不预测、不构成投资建议。
          </p>
        </>
      )}

      {d && n === 0 && (
        <p className="text-[11px] leading-relaxed text-muted-foreground">这一窗口一行都没有（不是"市场没涨跌"）：换个窗口或等回填。</p>
      )}
    </GlassCard>
    </div>
  );
}
