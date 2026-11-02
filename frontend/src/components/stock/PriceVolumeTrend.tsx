import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { Caveats, Definitions, Panel } from "@/components/ui/DataPanel";
import { DocDrawer, DrawerSection } from "@/components/ui/DocDrawer";
import { splitCaveats } from "@/lib/caveatKind";
import { plain } from "@/lib/agent";
import { GlassCard } from "@/components/ui/GlassCard";
import { useNonce, useProbe } from "@/lib/probe";
import { usePageAskAi } from "@/lib/workspace/pageAskAi";
import { dash } from "@/lib/datastate";
import { cn } from "@/lib/utils";
import {
  barDate, barLabel, barTooltip, isUp, maPoints, numOrNull, pctText, pickBarIndex, price,
  priceExtent, readoutCells, readoutSource, seriesCaption, volMax, volText, amtText,
  type PvBar, type PvPayload, type PvSeries, type ReadoutTone,
} from "@/lib/priceVolumeTrend";

/**
 * 量价走势（用户 2026-10-16：「日线、周线、月线分别为一块，占界面左中右三块」）。
 *
 * 三块并排 = `md:grid-cols-3`（这一屏中间列最多只有 1024px，再用 `lg` 就等于在常见宽度下永远叠成一列；
 * 窄到 768px 以下自动叠成上下，不横向滚）。每块上图下量。
 * 读数行（用户 2026-10-28：「做到跟股票软件那样，鼠标点上去可以有显示当天的高点低点开盘收盘 涨跌幅。
 * 可以点击任何一根k线之后再下方显示数字」）：原来只挂原生 `<title>`，要按住一秒才飘出一张卡片、
 * 鼠标一移开就没了 —— 现在改成图下方常驻那一行，悬停即时切换、点一根钉住、再点取消，
 * 谁都没选时停在最新一根（`pickBarIndex` 那一档优先级住在判读层，可单测）。
 * 三条不许糊掉的线（都由后端交出，页面只照做）：
 *  · 图是**前复权形状**（以最新一场为基准），不是历史名义价 —— 每根另给 `close_raw`；
 *  · 最后一根常常"本期未走完"，图上虚线 + 文字标出来；
 *  · 根数不足（<6）那一块不连线，只列读数 —— 三个点连出来的"趋势"是骗人的。
 */

const W = 320, PH = 118, VH = 34;     // viewBox 尺寸：价格区 + 量区（单位是 viewBox，不是像素）

const MA_LABEL: Record<string, string> = { ma3: "MA3", ma5: "MA5", ma6: "MA6", ma10: "MA10", ma20: "MA20" };

/** A 股习惯是红涨绿跌，与"好坏"无关：这一处映射只准住在这一个文件里（`ReadoutTone` 三态由判读层交）。 */
const TONE_CLS: Record<ReadoutTone, string> = { up: "text-danger", down: "text-success", none: "text-muted-foreground" };

/**
 * 图下方那一行：日期 + 开/高/低/收/涨跌（+ 不复权收、量、额、换手、含几根日线）。
 * 单独成一个函数是因为三块共用同一个形状，而这一块最容易被改成"三块各写一行"。
 */
function ReadoutRow({ bar, kind, source }: { bar: PvBar | null; kind: "day" | "week" | "month"; source: string }) {
  if (!bar) {
    return <p className="mt-1 rounded border border-dashed border-border/60 px-1.5 py-1 text-[10px] text-muted-foreground">
      {source}：这一块一根都没有，没有可显示的读数。
    </p>;
  }
  return (
    <div className="mt-1 rounded border border-border/60 bg-muted/25 px-1.5 py-1"
      title={barTooltip(bar, kind, barLabel(bar, kind))}>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[10px] leading-relaxed">
        <span className="shrink-0 text-[9px] text-muted-foreground">{source}</span>
        <span className="font-mono text-[10px] font-semibold text-foreground">{barDate(bar, kind)}</span>
        {readoutCells(bar).map(c => (
          <span key={c.k} className="whitespace-nowrap text-muted-foreground">
            {c.k} <span className={cn("font-mono font-medium", TONE_CLS[c.tone])}>{c.v}</span>
          </span>
        ))}
        {bar.partial && <span className="whitespace-nowrap text-warning">本期未走完</span>}
      </div>
    </div>
  );
}

function Chart({ series, kind }: { series: PvSeries | null | undefined; kind: "day" | "week" | "month" }) {
  // 悬停与点选各存一份：鼠标移开时悬停归零、点选那根要留着（用户要的就是"点一根之后下面还看得见"）
  const [hover, setHover] = useState<number | null>(null);
  const [pinned, setPinned] = useState<number | null>(null);
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
  const at = pickBarIndex(n, hover, pinned);
  const active = at === null ? null : rows[at];
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
        {/* 十字光标：跟着"现在看的那一根"走（悬停 > 点选 > 最新一根），画在柱子上面、透明命中条下面 */}
        {at !== null && (
          <g pointerEvents="none">
            <line x1={x(at)} x2={x(at)} y1="0" y2={PH + VH + 6} stroke="currentColor"
              className="text-muted-foreground/70" strokeWidth="0.5"
              strokeDasharray={typeof pinned === "number" && pinned === at ? undefined : "2 2"} />
            {numOrNull(active?.close) !== null && (
              <circle cx={x(at)} cy={y(numOrNull(active?.close) ?? 0)} r="1.7"
                className="text-foreground" fill="currentColor" fillOpacity="0.85" />
            )}
          </g>
        )}
        {/* 每一根的悬停与点选（整列宽的一条透明矩形，好点）：
            悬停 → 下面那一行立刻换；点一下 → 钉住（鼠标移开也不走）；再点同一根 → 取消。
            原来只挂原生 `<title>`，等于"要按住一秒才飘出一张卡片"，与股票软件那种即时的读数行不是一回事。 */}
        {rows.map((r, i) => (
          <rect key={`hit-${r.date}`} x={i * step} y="0" width={step} height={PH + VH + 6}
            fill="transparent" className="cursor-crosshair" role="button"
            aria-label={`${barDate(r, kind)} 那一根的读数`}
            onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
            onFocus={() => setHover(i)} onBlur={() => setHover(null)}
            onClick={() => setPinned(p => (p === i ? null : i))} />
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
      <ReadoutRow bar={active} kind={kind} source={readoutSource(hover, pinned)} />
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
  // 分流仍走全站那一处 `lib/caveatKind.ts`（白名单＋不健康词根两道闸）：纯口径散文进抽屉，
  // `partial_last`／运行时 `too_few` 那族留在屏上、有才出现（10-29 用户第 4 条那条红线没动）。
  const { visible, foldable } = splitCaveats(d?.caveats ?? []);

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
      {/* 用户 2026-10-31（全站一起改，个股组）：「这块的说明」必须摆在这块标题这一行的最右边，
          同一行还有刷新这类元信息时，按钮排在它们前面。原先说明散在两处 —— 标题行那枚
          `Caliber` 问号角标（悬停才念）与表尾 `CaliberFold` 那颗长在正文里的按钮（看起来像
          "数下面的一段话"）—— 现在一处收齐：这一行右侧先按钮、后「重新取数」。
          `Caliber` 问号角标按「个股信息」那一块的先例一起退场（句子原样搬进抽屉，不是删掉）；
          其中「鼠标放在哪一根…」那一句与抽屉里「读法」那段说的是同一件事，只留一份。
          ⚠️ 一个模块只留一颗（这一行原有注释里 10-29 写的那条例外没作废）：Panel 不挂 `note`
          （挂了就是同一张卡片第二颗同名按钮，10-29 已经撤过一次）。 */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold">量价走势（日线 · 周线 · 月线）</h2>
        <span className="ml-auto flex flex-wrap items-center gap-1.5">
          <DocDrawer title="量价走势（日线 · 周线 · 月线）">
            <DrawerSection heading="三块共用的口径">
              <p>
                三块都只读本机日线表，周线与月线由日线聚合（开=本期首根开、高=最高、低=最低、收=末根收，量额相加）。
                价格是前复权形状（以最新一场为基准），图下面那一行同时给不复权收；区间涨跌比的是上一期的复权收。
                最后一根常常是「至今」（本周未收 / 本月未满），图上用虚线框标出来。
              </p>
            </DrawerSection>
            <DrawerSection heading="读法">
              <p>
                上图下量；鼠标放到哪一根，图下面那一行就现出那期的开/高/低/收/涨跌幅
                （复权与不复权两个收价都给），点一根钉住、再点取消，移开鼠标读数仍留在图上。窄屏自动叠成一列。
              </p>
            </DrawerSection>
            <DrawerSection heading="为什么图上那根「收」可能不是你记忆里的那个价">
              <p>
                三块共用前复权形状 —— 本机把每根日线乘上复权因子（最新一场＝1，越老越小），
                于是历史上那根的复权收会明显低于它当天真成交的价，这样几十个月的涨跌才连得起来读；
                分红除权不折的话，月线会被砸出一个根本不存在的「暴跌」。
                要看当天真成交的价，图下面那一行里的「不复权收」就是它；
                周线与月线的「涨跌」是与上一期收比，不是与本期开盘比。
              </p>
            </DrawerSection>
            <DrawerSection heading="取数口径（后端原句，页面未改写）">
              <p>{d?.caliber ? plain(d.caliber) : '后端这一次没交出口径原句（还没选一只或没读到数时本来就没有）。'}</p>
            </DrawerSection>
            {foldable.length > 0 && (
              <DrawerSection heading="口径声明（后端 caveats 原句，页面未改写）">
                <Caveats items={foldable} />
              </DrawerSection>
            )}
            {!!d?.definitions && Object.keys(d.definitions).length > 0 && (
              <DrawerSection heading="这一列是什么（字段含义，后端 definitions）">
                <Definitions map={d.definitions} bare />
              </DrawerSection>
            )}
          </DocDrawer>
          <button type="button" onClick={refresh} title="重新读本机日线"
            className="flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
            <RefreshCw className="h-3.5 w-3.5" /> 重新取数
          </button>
        </span>
      </div>

      <Panel title="左：日线 中：周线 右：月线（同一只票、同一个最新一场）" state={state}>
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

            {/* 用户 2026-10-29 第 4 条把规矩说死了：「缺数声明、截断上限、告警是应该留，有的时候才显示
                （并且精简一下），没有的时候就不显示」⇒ 这一块**不再开 `foldAll`**。
                分流只住 `lib/caveatKind.ts` 一处：`adjusted_shape`／`depth` 这类纯口径散文进抽屉，
                `partial_last`（名字里带 partial）与运行时 `too_few` 留在屏上、有才出现。
                10-31 起抽屉那颗按钮搬到了标题行（见上面那段），这里只剩留在屏上的那一半 ——
                正文不再长出第二颗「这块的说明」。 */}
            <Caveats items={visible} />
          </>
        )}
      </Panel>
    </div>
  );
}
