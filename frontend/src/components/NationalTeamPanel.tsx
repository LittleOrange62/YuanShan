import { useEffect, useMemo, useRef, useState } from "react";
import { Hand, RefreshCw, TriangleAlert } from "lucide-react";
import { Caliber } from "@/components/ui/Caliber";
import { CaliberFold, Contract, Panel } from "@/components/ui/DataPanel";
import { useNonce, useProbe } from "@/lib/probe";
import { plain, finite } from "@/lib/agent";
import { NULL_TEXT, num } from "@/lib/datastate";
import { cn } from "@/lib/utils";
import {
  DEFAULT_SERIES, alertCells, alertHeadline, alertSampleNotes, alertTitle, anomalyIndex,
  bandLegend, bandText, DEFAULT_RANGE,
  dateTicks, dayReadout, lineAvailability, pairScales, pointValue, bridgeSegments,
  RAW_MIN_STEP, trendNote, turnMarks, viewLen,
  type NtPanel, type NtPoint, type NtScale, type NtSeriesOption,
} from "@/lib/nationalTeam";

/**
 * 复盘页顶部「国家队代理指标」（用户 2026-10-26 点名：「头部沪深300基金市值占沪深300流通市值
 * 比例的变化……做成图表」，五条需求逐条落在这里；落点由用户选在复盘报告 `/agent/review` 顶部）：
 * 两把尺（四大 / 全部被动 300ETF）上下两条长线，共用一根日期轴。
 *
 * 这一屏只读 `/api/market/national-team`。判据、阈值、档位、异动那句话、序列名与单位
 * **只住 `duanxian/national_team.py` 一处**：页面按 `series_options` 渲染按钮与图例，
 * 按 `thresholds`/`levels` 说话，不写死 p95/0.02pp，不自己分档、不自己算分位。
 *
 * 界面上不许糊掉的六条：
 *  · 缺数一律 `—`，真读数那两条实线**该断就断**（不插值、不拿 0 顶上）：
 *    主尺占比的分母 2026-10-31 起改成当日日线自算 ⇒ 679 场全史有底；
 *    备用尺「占总市值」仍然只从快照来（总股本不在日线里）⇒ 那一条早期必然一串 `—`。
 *    这一屏现算"这条线在本窗口有几分之几"摆在图上方（`lineAvailability`）。
 *    11-01 用户点名「没有数据的日子也用曲线连贯地连接起来」，并当场选了"只动显示"那一条 ⇒
 *    底下另画一条**虚线**：中间断的开两端直连、某一头整段没有就把最近那个读数水平延伸过去。
 *    两条界线不能糊：虚线只是给眼睛一个形状，**不是那些天的读数**——点上去仍交
 *    「这一天库里没有行（缺采，不是 0）」，分档/异动/均线/转折点走的永远只有真有数的那些天；
 *  · 净申赎那一族**没有历史档位**（`bands.not_applicable`）——屏上读那句原话，不落"中性"；
 *  · 提示那一排是后端判出来的（`alert.items` 每个事件带 `date`、`alert_type` 与 `parts[]` 的
 *    `metric`／`value_text`／`compare_text`／`meaning_text` 原句），页面只按日期找位置画点、
 *    只把后端那几截原句摆进格子，不拼单位、不改措辞；
 *    11-01 用户点名「这里还是不够清晰，要显示：指标名、时间、数值、与阈值的比较结果、结果含义（如异动提醒）」
 *    ⇒ 打开之后是一张**五列表**（时间／指标名／数值／与阈值的比较／结果含义），一个读数一行、
 *    日期那一格按事件跨行合并；同一件事几条读数共用的那句"含义"也只念一份（`alertCells().sharedMeaning`）——
 *    四行复读同一句就是当年那 800 像素黄字的表格版。五格里任何一样没交，屏上就出现那句黄字声明，
 *    页面绝不自己补一个看起来像的读数（"其他类似的提醒也要按这个标准写"是全站口径，见功能点文档 §纪律）；
 *    「任一越界都提示」＝两把尺里任何一条序列任何一天越界都在这一排里点名；
 *    10-31 起这一排在**图下方**（用户原话是"图上下有提示"，而旧版图上方那 800 像素黄字让读者
 *    滚两屏还看不见图），每个事件一行、同一条序列复读的「阈值样本」那句整屏只念一遍；
 *    11-01 用户点名「以下内容收缩起来」⇒ 整排**默认收成一行摘要**（`alertHeadline`）：
 *    仍然在屏上、仍然有才显示、仍然不进抽屉，但摘要自己必须把"几个事件、最近一天哪天、越了多少"说完——
 *    收起状态把"有 40 个事件"说成"没事"就是谎话；「一条都没有／判不了」那两句本来才一行，不折；
 *    格子区给的是**有界的上下滚窗口**（不是折叠、不是抽屉）：这一列的宽度要和右侧栏抢空间，
 *    四个读数一行摆不下时它照样要能读全，但不许再把图顶出第一屏；
 *  · 时间档按钮只认后端 `range_options`；拖动/平移只改**这一屏画哪一段**，不改判据的样本底
 *    （样本底永远是整段可用历史，所以同一个点在 3 个月和 2 年两档下的档位与转折点是同一个数）；
 *  · 点一个点（或悬停）必须把**完整日期**钉在屏上——原生 `<title>` 只算补充，不算交付；
 *  · **趋势与转折点只认后端交的**（用户 2026-10-31：「图表做的连贯一点，然后明确一些转折点」）：
 *    逐日线画淡、后端 `trend.ma` 画粗，▼顶/▲底按日期落在同一根轴上（符号＋虚线 stem＋日期一行，
 *    只有符号就读不出"哪天拐的"），图下每把尺一份清单。
 *    图的宽度是现量的（1 单位＝1 像素）：旧版 viewBox 写死 780 又 `width=100%`，容器一窄整张图
 *    按比例缩成 76 像素高一格，均线、转折点、档带全糊在一起——那才是"不连贯"的第一真因。
 *    页面**不自己算均线、不自己找拐点、不自己定窗口与门槛**（都在 `SERIES[].trend` 与 `trend.smooth/th`）；
 *    某一条判不了（有效点不够）就把后端那句 reason 摊在屏上——"图上没有标记"有两种成因，
 *    说成"这一段走得平"就是谎话。
 */

/** 兜底绘图宽度：量到容器真实宽度之前先用它（`cw`），量到了就按 1 像素=1 单位画 */
const W = 780, PH = 150, GAP = 16, PADX = 44, PADR = 10, AXIS = 18;
const PANE2 = PH + GAP;
const H = PANE2 + PH + AXIS;
/** 一场至少占几像素才画实心小点：更密的时候点会糊成一片，那一段靠线本身说话 */
const DOT_MIN_STEP = 2.2;

/** 序列的取值：按 `series_options.field` 读，页面不写死字段名 */
const valOf = (p: NtPoint | undefined, s: NtSeriesOption) => pointValue(p, s.field);

/** 一个尺的画线量程：可见点 ∪ 可见均线值 ∪（能建档时的 p10/p90），保证"临界线"和那条粗均线都落在图内。
 *  把 `ma` 一起算进来是必需的：均线是用整段历史算的，窗口左边缘那几期的均值可能来自窗外的高/低点，
 *  只按可见点取量程就会把粗线裁掉一截（裁掉的那截正是"这一段在往下走"的那半句话）。 */
function yDomain(pts: (NtPoint | undefined)[], s: NtSeriesOption, scale: NtScale,
                 ma: (number | null)[] = []) {
  const vs = pts.map(p => valOf(p, s)).filter((v): v is number => v !== null);
  vs.push(...ma.filter((v): v is number => v !== null));
  const cuts = scale.series_view[s.key]?.bands;
  if (cuts?.available && cuts.cuts) {
    const lo = finite(cuts.cuts.p10), hi = finite(cuts.cuts.p90);
    if (lo !== null) vs.push(lo);
    if (hi !== null) vs.push(hi);
  }
  if (!vs.length) return null;
  const lo = Math.min(...vs), hi = Math.max(...vs);
  const pad = (hi - lo || Math.abs(hi) || 1) * 0.08;
  return { lo: lo - pad, hi: hi + pad };
}

/** 断线分段的 polyline points（null 断开成多段：读不到 ≠ 连到上一个点） */
function segs(pts: (NtPoint | undefined)[], s: NtSeriesOption,
              x: (i: number) => number, y: (v: number) => number): string[] {
  const out: string[] = [];
  let cur: string[] = [];
  pts.forEach((p, i) => {
    const v = valOf(p, s);
    if (v === null) {
      if (cur.length > 1) out.push(cur.join(" "));
      cur = [];
    } else {
      cur.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`);
    }
  });
  if (cur.length > 1) out.push(cur.join(" "));
  return out;
}

/** 均线那条粗线同一个画法：null 断成多段。
 *  均线在缺数处本来就交 null（后端 `moving_average` 不满窗口不硬凑），
 *  所以这里连着画过去 = 屏幕自己造了一段没发生过的走势。 */
function segsVals(vals: (number | null)[], x: (i: number) => number,
                  y: (v: number) => number): string[] {
  const out: string[] = [];
  let cur: string[] = [];
  vals.forEach((v, i) => {
    const n = finite(v);
    if (n === null) {
      if (cur.length > 1) out.push(cur.join(" "));
      cur = [];
    } else {
      cur.push(`${x(i).toFixed(1)},${y(n).toFixed(1)}`);
    }
  });
  if (cur.length > 1) out.push(cur.join(" "));
  return out;
}

function Btn({ on, children, onClick, title }:
  { on: boolean; children: React.ReactNode; onClick: () => void; title?: string }) {
  return (
    <button type="button" aria-pressed={on} title={title} onClick={onClick}
      className={cn("rounded-lg px-2 py-1 text-[11px]",
        on ? "bg-primary/20 text-primary" : "text-muted-foreground hover:bg-muted/40")}>
      {children}
    </button>
  );
}

export function NationalTeamPanel() {
  const [nonce, refresh] = useNonce();
  const [range, setRange] = useState(DEFAULT_RANGE);
  const [seriesKey, setSeriesKey] = useState(DEFAULT_SERIES);
  const [start, setStart] = useState<number | null>(null);   // null＝跟着最新一段
  const [pinned, setPinned] = useState<number | null>(null); // 点选钉住的那一天（全轴索引）
  const drag = useRef<{ x0: number; s0: number } | null>(null);

  const st = useProbe<NtPanel>("/market/national-team", nonce, { range });
  const d = st.kind === "ok" && st.data.available ? st.data : null;
  const scales = (d?.scales ?? []) as NtScale[];
  const opts = (d?.series_options ?? []) as NtSeriesOption[];
  const spec = opts.find(o => o.key === seriesKey) ?? opts[0];
  const { dates, byScale } = useMemo(() => pairScales(scales), [scales]);
  const total = dates.length;
  // 绘图区宽度现量：旧版写死 viewBox 780 + width=100%，容器一窄整张图就按比例缩——
  // 两把尺各剩 76 像素高，均线、转折点、档带全糊在一起（用户 10-31「效果不佳」的第一条真因）。
  // 现在 1 单位＝1 像素，高度由 PH 说了算，不再被宽度顶。
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [cw, setCw] = useState(W);
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return undefined;
    const measure = () => {
      const w = Math.round(el.getBoundingClientRect().width);
      if (w > 0) setCw(w);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [total]);
  // 一屏画几场＝这一档的场数与宽度的交集：点「近 3 个月」就画那 66 场，点「全部」画到画不开为止
  // （旧版一律 130 场 ⇒ 五个档按钮画出来是同一根线，看着就像"切了档没反应"）
  const len = viewLen(total, cw - PADX - PADR);
  const maxStart = Math.max(0, total - len);
  const from = Math.min(maxStart, start === null ? maxStart : Math.max(0, start));
  const view = useMemo(() => dates.slice(from, from + len), [dates, from, len]);
  const viewByScale: Record<string, (NtPoint | undefined)[]> = {};
  scales.forEach(s => { viewByScale[s.key] = (byScale[s.key] ?? []).slice(from, from + len); });

  const step = (cw - PADX - PADR) / Math.max(1, len - 1);
  /** 逐日淡线这一层在这一屏画不画（太密就撤，理由与屏下那句声明都在 `RAW_MIN_STEP` 的注释里） */
  const showRaw = step >= RAW_MIN_STEP;
  const x = (i: number) => PADX + i * step;
  const alert0 = alertTitle(d);
  const alertItems = d?.alert?.items ?? [];
  // 阈值样本那句同一条序列每个事件都一模一样 ⇒ 整屏只念一份（旧版逐条复读，八条＝800 像素黄字）
  const sampleNotes = alertSampleNotes(alertItems);
  const ticks = dateTicks(view, 6);
  const rangeOpts = d?.range_options ?? [];
  const pinnedInView = pinned !== null && pinned >= from && pinned < from + len;
  const reading = d && spec && pinned !== null ? dayReadout(d, dates, pinned, spec.key) : null;

  const jumpTo = (date: string | null) => {
    if (!date) return;
    const i = dates.indexOf(date);
    if (i < 0) return;
    setPinned(i);
    setStart(Math.min(maxStart, Math.max(0, i - Math.floor(len / 2))));
  };

  return (
    <Panel<NtPanel>
      title={`国家队代理指标 · 被动沪深300 ETF 占沪深300市值（两把尺）${d?.anchor ?? ""}`}
      slowHint="读的是库里逐日落好的两把尺（份额 T+1），占比的分位与异动按整段可用历史现算"
      state={st}
      contract={(
        <Contract
          path="/api/market/national-team"
          source="事实＝analytics.national_team_etf_daily（写侧 app/compute/national_team.py，加工链第 29 步 national_team）；判据＝duanxian/national_team.py"
          fields={[
            ["scales[].points[]", "逐场：合计份额（亿份）、合计规模（亿元）、当日净申赎（亿份）、分母（万亿元）、两个占比（%）、清单只数、缺数标记"],
            ["scales[].series_view[key]", "每条序列一档：当期值、历史分档切点与样本数、异动名单与阈值"],
            ["alert", "两把尺任一越界的逐条点名（含阈值、日期、后端原句），以及「一条都没有」时那句为什么判不了"],
            ["coverage", "份额线覆盖到哪天、占比线从哪天起（分母那张快照表的入库日）、份额 T+1 是否滞后一场"],
            ["thresholds / levels / rule / definitions", "阈值、五档档名、判据一句话、四个口径 —— 界面只转述，不自己写"],
          ]}
        />
      )}
      controls={(
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1" role="group" aria-label="时间档">
            {rangeOpts.map(o => (
              <Btn key={o.key} on={range === o.key} title={`窗口 ${o.key}：判据样本底不变，只改画哪一段`}
                onClick={() => { setRange(o.key); setStart(null); setPinned(null); }}>{o.label}</Btn>
            ))}
          </div>
          <div className="flex items-center gap-1" role="group" aria-label="看哪条线">
            {opts.map(o => (
              <Btn key={o.key} on={spec?.key === o.key} title={o.read}
                onClick={() => setSeriesKey(o.key)}>{o.label}（{o.unit}）</Btn>
            ))}
          </div>
          <button type="button" onClick={() => { setStart(null); setPinned(null); refresh(); }}
            title="重新取数"
            className="ml-auto flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
            <RefreshCw className="h-3.5 w-3.5" /> 重新取数
          </button>
        </div>
      )}>
      {() => (
        <>
          {/* ── 这条线在本窗口到底有几分之几（缺数那些天的连接线在这里说破）── */}
          {spec && scales.map(s => {
            const av = lineAvailability(viewByScale[s.key] ?? [], spec.field);
            const v = s.series_view[spec.key];
            return (
              <p key={s.key} className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                {s.short}：{spec.label}在本窗口 {av.have}/{av.total} 场有读数
                {av.have === 0 ? " —— 这一段一条都没有，图上这一尺就是空的（缺数不是 0，也没有可连的两端）"
                               : (av.have < av.total
                                 ? "，缺的那几场画的是虚线：中间断的就连两端、某一头整段没有就把最近那个读数水平延伸过去——那是给你看形状的连接线，不是那些天的读数"
                                 : "")}
                {av.have > 0 && av.first ? `，本窗口最早一个在 ${av.first}` : ""}
                ｜当期档位 {bandText(v)}
              </p>
            );
          })}

          {/* ── 图：上下两尺共用一根日期轴；横向可拖、可点 ── */}
          {total > 0 && spec && (
            <div ref={boxRef} className="mt-2 overflow-x-auto">
              <svg viewBox={`0 0 ${cw} ${H}`} width="100%" height={H} role="img"
                aria-label={`被动沪深300 ETF 的${spec.label}（${spec.unit}），两把尺上下并排，横轴 ${view[0] ?? ""} 到 ${view[view.length - 1] ?? ""}；`
                  + `淡线是逐日读数、粗线是近 ${scales[0]?.series_view[spec.key]?.trend?.smooth ?? "—"} 场均线，`
                  + `▼ 是均线顶、▲ 是均线底（后端确认过的才标）；`
                  + `虚线段是缺数那些天的连接线（中间断的连两端、某一头整段没有就水平延伸最近读数），不是当日读数`}
                style={{ touchAction: "none", cursor: drag.current ? "grabbing" : "crosshair" }}>
                {scales.map((s, si) => {
                  const pts = viewByScale[s.key] ?? [];
                  const sv = s.series_view[spec.key];
                  // 均线与转折点都由后端按**整段历史**算好（`trend`），这里只切出当前这一段来画：
                  // 换一次时间档不该让同一个顶换个地方（判据样本底与分档、异动同一口径）。
                  const trend = sv?.trend;
                  const maAll: (number | null)[] = trend?.available ? trend.ma : [];
                  const maView = maAll.slice(from, from + len);
                  const dom = yDomain(pts, spec, s, maView);
                  if (!dom) return null;
                  const top = si === 0 ? 0 : PANE2;
                  const y = (v: number) => top + PH - ((v - dom.lo) / ((dom.hi - dom.lo) || 1)) * PH;
                  const cuts = sv?.bands?.cuts;
                  const marks = new Map(anomalyIndex(view, sv).entries());
                  const turns = turnMarks(view, sv);
                  return (
                    <g key={s.key}>
                      {/* 分档带：只画落在本窗口量程内的档（用户第 4 条：标出超高/超低的范围） */}
                      {cuts && s.series_view[spec.key]?.bands?.available && (
                        <g aria-hidden>
                          {[
                            { lo: null, hi: cuts.p10, key: 'ultra_low' as const },
                            { lo: cuts.p90, hi: null, key: 'ultra_high' as const },
                          ].map((b, bi) => {
                            const lo = b.lo === null ? dom.lo : Math.max(dom.lo, finite(b.lo) ?? dom.lo);
                            const hi = b.hi === null ? dom.hi : Math.min(dom.hi, finite(b.hi) ?? dom.hi);
                            if (!(hi > lo)) return null;
                            return (
                              <rect key={bi} x={PADX} y={y(hi)} width={cw - PADX - PADR}
                                height={Math.max(1, y(lo) - y(hi))}
                                className="bg-warning/10" opacity={0.9} />
                            );
                          })}
                          {([["p10", cuts.p10, "ultra_low"], ["p90", cuts.p90, "ultra_high"]] as const)
                            .map(([k, cv, levelKey]) => {
                              const n = finite(cv);
                              if (n === null || n < dom.lo || n > dom.hi) return null;
                              // 档名读后端 levels（页面抄一份中文就是第二份真相源）
                              const label = d?.levels?.[levelKey] ?? levelKey;
                              return (
                                <g key={k}>
                                  <line x1={PADX} x2={cw - PADR} y1={y(n)} y2={y(n)}
                                    className="stroke-warning" strokeWidth={0.9} strokeDasharray="4 3" />
                                  <text x={cw - PADR - 2} y={y(n) - 2} textAnchor="end"
                                    className="fill-warning" fontSize={9}>
                                    {`${label}线 ${num(n, 3)}`}
                                  </text>
                                </g>
                              );
                            })}
                        </g>
                      )}
                      <line x1={PADX} x2={cw - PADR} y1={top + PH} y2={top + PH} className="stroke-border" />
                      <text x={2} y={top + 10} fontSize={9} className="fill-muted-foreground">
                        {s.short}
                      </text>
                      <text x={2} y={top + PH - 2} fontSize={9} className="fill-muted-foreground">
                        {num(dom.lo, 2)}
                      </text>
                      <text x={2} y={top + 22} fontSize={9} className="fill-muted-foreground">
                        {num(dom.hi, 2)}
                      </text>
                      {/* 缺数那些天的连接线（用户 11-01 点名要连贯，选的是"只动显示"）：
                          画在最底下、淡、虚线；真读数那两条实线压在它上面，一眼分得清哪段是量的、哪段是连的。 */}
                      {bridgeSegments(pts.map(p => valOf(p, spec)), x, y).map((p, i) => (
                        <polyline key={`br${i}`} points={p} fill="none" strokeWidth={1.1}
                          strokeDasharray="4 4" opacity={0.3}
                          className={si === 0 ? "stroke-primary" : "stroke-warning"}>
                          <title>这一段是连接线（那些天没有分母，算不出占比），不是当天的读数</title>
                        </polyline>
                      ))}
                      {/* 逐日那一条画淡一点：方向由下面那条粗均线负责说（用户 10-31「连贯一点」）。
                          密到一场不足 RAW_MIN_STEP 像素时整层不画 —— 那时候它是一团锯齿带而不是一条线，
                          还会把粗均线盖住；撤掉的只有这一层画，读数、判据、点选都不受影响，
                          屏下那句会明说"这一屏没画逐日线"。 */}
                      {showRaw && segs(pts, spec, x, y).map((p, i) => (
                        <polyline key={i} points={p} fill="none" strokeWidth={1.1} opacity={0.62}
                          className={si === 0 ? "stroke-primary" : "stroke-warning"} />
                      ))}
                      {/* 均线（后端 `trend.ma`，近 smooth 场）：判不了时后端交空数组，这里就什么都不画 */}
                      {segsVals(maView, x, y).map((p, i) => (
                        <polyline key={`ma${i}`} points={p} fill="none" strokeWidth={2.6}
                          className={si === 0 ? "stroke-primary" : "stroke-warning"} />
                      ))}
                      {/* 转折点：只标后端确认过的顶/底（▼ 顶 / ▲ 底），点它就把那一天钉住。
                          用户 10-31「明确一些转折点」⇒ 从"一个 9 号字"升级成 stem＋号＋日期三件套：
                          一根虚线从均线上的那一天拉到这一格的顶/底边，符号钉在末端，日期写在旁边
                          （只有符号的话，屏幕上看得出"这里拐了"，读不出"哪天拐的"）。 */}
                      {turns.marks.map(({ i, turn }) => {
                        const mv = maView[i];
                        if (mv === null || mv === undefined) return null;
                        const peak = turn.kind === "peak";
                        const yy = y(mv);
                        const cls = peak ? "stroke-danger fill-danger" : "stroke-success fill-success";
                        return (
                          <g key={`t${i}`} className={cls} style={{ cursor: "pointer" }}
                            onClick={() => jumpTo(turn.date)}
                            aria-label={`${s.short}·${spec.label}：${turn.text}`}>
                            <title>{`${s.short}·${spec.label}：${turn.text}（点一下钉住这一天）`}</title>
                            <line x1={x(i)} x2={x(i)} y1={yy} y2={peak ? top + 3 : top + PH - 1}
                              strokeWidth={0.8} strokeDasharray="2 2" opacity={0.75} />
                            <text x={x(i)} y={peak ? yy - 6 : yy + 11} textAnchor="middle" fontSize={11}>
                              {peak ? "▼" : "▲"}
                            </text>
                            <text x={x(i)} y={peak ? yy - 16 : yy + 21} textAnchor="middle" fontSize={8.5}>
                              {turn.date}
                            </text>
                          </g>
                        );
                      })}
                      {/* 有读数的那一天画一颗小点：断口与"这一天没有"一眼能分开。
                          密到一场不足 DOT_MIN_STEP 像素时只留异动/钉住那几颗（否则点糊成一条粗带，
                          线本身已经把形状说完了） */}
                      {pts.map((p, i) => {
                        const v = valOf(p, spec);
                        if (v === null) return null;
                        const hot = marks.has(i) || from + i === pinned;
                        if (!hot && step < DOT_MIN_STEP) return null;
                        return (
                          <circle key={i} cx={x(i)} cy={y(v)} r={marks.has(i) ? 3.4 : 1.5}
                            className={marks.has(i) ? "fill-danger" : (si === 0 ? "fill-primary" : "fill-warning")}
                            strokeWidth={marks.has(i) ? 1 : 0} />
                        );
                      })}
                      {/* 命中区：整条带宽，一次点/悬停对准一天 */}
                      <rect x={PADX} y={top} width={cw - PADX - PADR} height={PH} fill="transparent"
                        onPointerDown={(e) => {
                          drag.current = { x0: e.clientX, s0: from };
                          (e.target as Element).setPointerCapture?.(e.pointerId);
                        }}
                        onPointerMove={(e) => {
                          const dr = drag.current;
                          if (!dr) return;
                          // 一格 = rectWidthPx / (len-1) 个 CSS 像素；往右拖 = 窗口往更早走
                          const pxPerStep = e.currentTarget.getBoundingClientRect().width / Math.max(1, len - 1);
                          const k = (e.clientX - dr.x0) / pxPerStep;
                          setStart(Math.min(maxStart, Math.max(0, Math.round(dr.s0 - k))));
                        }}
                        onPointerUp={() => { drag.current = null; }}
                        onPointerLeave={() => { drag.current = null; }}
                        onClick={(e) => {
                          const box = (e.currentTarget as SVGRectElement).getBoundingClientRect();
                          const rel = (e.clientX - box.left) / box.width;
                          const i = Math.round(rel * Math.max(1, len - 1));
                          setPinned(from + Math.min(len - 1, Math.max(0, i)));
                        }} />
                    </g>
                  );
                })}
                <g>
                  {ticks.map(t => (
                    <text key={t.i} x={x(t.i)} y={H - 4} textAnchor="middle" fontSize={9}
                      className="fill-muted-foreground">{t.text}</text>
                  ))}
                  {pinnedInView && (
                    <line x1={x(pinned - from)} x2={x(pinned - from)} y1={0} y2={PANE2 + PH}
                      className="stroke-primary" strokeWidth={0.9} strokeDasharray="3 2" />
                  )}
                </g>
              </svg>

              <div className="mt-1 flex flex-wrap items-center gap-2">
                <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <Hand className="h-3.5 w-3.5" aria-hidden /> 图上按住左右拖＝平移这一段
                </span>
                <input type="range" min={0} max={maxStart} value={from} step={1}
                  aria-label="窗口起点（拖动看更早的场次）"
                  className="h-1.5 w-40 accent-primary"
                  onChange={(e) => setStart(Number(e.target.value))} />
                <span className="text-[11px] text-muted-foreground">
                  画的是 {view[0]} 到 {view[view.length - 1]}（{view.length} 场 / 这一档共 {total} 场）
                  {/* 撤掉那一层画要在屏上说破，不能让读者自己猜"这条线怎么不见了"（11-01 要「至少 2 年」
                      ⇒ 全部那一档一场只剩约 0.5 像素，逐日淡线与逐日小点这两层都到了不画的门槛）*/}
                  {!showRaw && `｜这一屏一场不足 ${RAW_MIN_STEP} 像素，逐日那一条淡线不画（太密时它是一团锯齿，还会盖住粗均线），小点只留异动与钉住那几颗——走势的形状由那条粗均线说，每一天照样点得出来`}
                </span>
                {(start !== null || pinned !== null) && (
                  <button type="button" onClick={() => { setStart(null); setPinned(null); }}
                    className="rounded-lg bg-primary/15 px-2 py-1 text-[11px] text-primary hover:bg-primary/25">
                    回到最新一段
                  </button>
                )}
              </div>
            </div>
          )}

          {/* ── 提示那一排（用户第 3 条需求：突然增长或降低要在图上下有提示）──────────
              11-01 用户点名「以下内容收缩起来」⇒ 这一整排（含逐条点名与阈值样本）默认**收成一行**：
               · 仍然在屏上、仍然"有才显示"、仍然不进抽屉——只是外面那一行摘要，点开才摊开格子表；
               · 摘要那一行必须自己就把话说全：几个事件、最近一次是哪天、越的是哪条线、越了多少，
                 收起状态把"有 40 个事件"说成"没事"就是谎话；
               · 「一条都没有 / 判不了」那两句本来才一行，不折（折起来反而要点两下才知道没数）。
              10-31 那两处（挪到图下方、逐条整句改格子）保留。 ── */}
          {alertItems.length === 0 ? (
            <div className={cn("mt-2 rounded-lg border p-2 text-[11px] leading-relaxed",
              alert0.fired ? "border-warning/60 bg-warning/10 text-warning"
                           : "border-border bg-muted/20 text-muted-foreground")}>
              <div className="flex items-start gap-1.5">
                <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                <span>{plain(alert0.text)}</span>
              </div>
            </div>
          ) : (
            <details className={cn("mt-2 rounded-lg border p-2 text-[11px] leading-relaxed",
              alert0.fired ? "border-warning/60 bg-warning/10 text-warning"
                           : "border-border bg-muted/20 text-muted-foreground")}>
              <summary className="flex cursor-pointer items-start gap-1.5">
                <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                <span>{plain(alertHeadline(d))}</span>
              </summary>
              <p className="mt-1.5">{plain(alert0.text)}</p>
              {/* 五格一张表（用户 2026-11-01 点名：指标名／时间／数值／与阈值的比较结果／结果含义）。
                  一个事件最多四条读数（两把尺 × 两条占比线），日期那一格跨行合并——
                  同一件事不必念四遍日期。列宽要跟右侧栏抢空间（实测 460 像素）⇒ 有界的上下滚窗口，
                  这一排整排已经默认收起，打开之后就摆后端交出的全部事件，不再在里面套第二层折叠。 */}
              <div className="mt-1.5 max-h-72 overflow-y-auto">
                <table className="w-full border-collapse">
                <thead>
                  <tr className="text-left text-[10px] text-muted-foreground">
                    <th className="py-0.5 pr-2 text-left font-normal">时间</th>
                    <th className="py-0.5 pr-2 text-left font-normal">指标名</th>
                    <th className="py-0.5 pr-2 text-left font-normal">数值</th>
                    <th className="py-0.5 pr-2 text-left font-normal">与阈值的比较</th>
                    <th className="py-0.5 text-left font-normal">结果含义</th>
                  </tr>
                </thead>
                <tbody>
                  {alertItems.map((it, i) => {
                    const c = alertCells(it);
                    const parts = c.cells.length ? c.cells : [null];
                    return parts.map((cell, k) => (
                      <tr key={`${it.scale_key}-${it.series}-${it.date}-${i}-${k}`}
                        className="border-t border-border/40 align-top">
                        {k === 0 && (
                          <td rowSpan={parts.length} className="py-1 pr-2 align-top">
                            {c.date === NULL_TEXT ? (
                              <span title="当期档说的是现在坐在哪个位置，不对应某一天">当期（不是某一天）</span>
                            ) : (
                              <button type="button" onClick={() => jumpTo(it.date)}
                                title={`在图上钉住 ${it.date}`}
                                className="underline-offset-2 hover:underline">{c.date}</button>
                            )}
                            <p className="mt-0.5 text-[10px] leading-tight text-muted-foreground">
                              {c.family}
                            </p>
                          </td>
                        )}
                        {cell === null && (
                          <td colSpan={4} className="py-1">
                            {NULL_TEXT}（后端这个事件没交出读数格子——不是"没有读数"，是这一格没送到）
                          </td>
                        )}
                        {cell && (
                          <>
                            <td className="py-1 pr-2">{plain(cell.metric) || NULL_TEXT}</td>
                            <td className="py-1 pr-2 tabular-nums">{plain(cell.valueText) || NULL_TEXT}</td>
                            <td className="py-1 pr-2">{plain(cell.compareText) || NULL_TEXT}</td>
                            {/* 同一件事的几条读数，"结果含义"往往是那一句（判据同一条）⇒ 只念一份、跨行合并。
                                四行复读同一句就是当年那 800 像素黄字的表格版。 */}
                            {!(k > 0 && c.sharedMeaning) && (
                              <td rowSpan={c.sharedMeaning ? parts.length : 1} className="py-1">
                                {plain(c.sharedMeaning || cell.meaningText)
                                  /* 后端还没交齐五格时念这一格的老短版，并当场说破是哪一格的什么没来 */
                                  || `（这一格的"结果含义"后端没交，暂念短句：${plain(cell.phraseShort) || NULL_TEXT}）`}
                              </td>
                            )}
                          </>
                        )}
                      </tr>
                    ));
                  })}
                </tbody>
                </table>
              </div>
              {alertItems.some(it => alertCells(it).incomplete) && (
                <p className="mt-1 text-[11px] leading-relaxed text-warning">
                  上面这一排里有格子缺了后端该交的那五样之一（指标名／数值／与阈值的比较／结果含义）——
                  屏上念的是兜底短句，不是算出来的读法。这一句在补齐之前会一直显示。
                </p>
              )}
              {/* 阈值样本那句：每条序列自己的历史跨度，同一条在每个事件里都一样 ⇒ 整屏只念这一遍 */}
              {sampleNotes.map((t, i) => (
                <p key={i} className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
                  阈值样本（各条序列自己的历史，上面每一行用的就是它）：{plain(t)}
                </p>
              ))}
              {(d?.alert?.truncated ?? 0) > 0 && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  另有 {d?.alert?.truncated} 个事件后端没有列在这一排（这一排交了 {alertItems.length} 个，
                  这一段里共 {d?.alert?.count_all ?? NULL_TEXT} 个）；图上每一颗加深的红点都是一个事件，点它就把那天钉住。
                </p>
              )}
            </details>
          )}

          {/* ── 转折点清单（用户 2026-10-31：「明确一些转折点」）──────────────────
              这一屏的分工按 10-31 那条「屏上只留图与数」定：阈值怎么定的、均线几个场，
              全部进标题行那颗「这块的说明」（下面 `CaliberFold notes` 里那句 `trendNote`）；
              屏上只留**数据**（哪一天是顶、哪一天是底、值多少）和**判不了那句**（缺数类声明按红线不许进抽屉）。 ── */}
          {total > 0 && spec && scales.map(s => {
            const sv = s.series_view[spec.key];
            const turns = turnMarks(view, sv);
            const t = sv?.trend;
            if (!t?.available) {
              return (
                <p key={`tr-${s.key}`}
                  className="mt-1.5 rounded-lg border border-warning/50 bg-warning/10 p-2 text-[11px] leading-relaxed text-warning">
                  {s.short}·{spec.label}这一条的趋势判不了：{plain(trendNote(sv))}
                  —— 所以图上这一条没有粗均线，也没有顶/底标记。
                </p>
              );
            }
            return (
              <div key={`tr-${s.key}`} className="mt-1.5 rounded-lg border border-border/50 bg-muted/20 p-2">
                <p className="text-[11px] leading-relaxed text-muted-foreground">
                  <b className="text-foreground">{s.short}·{spec.label}</b>
                  这一段里后端确认的转折点 {turns.marks.length} 个（▼ 顶 ▲ 底，点一行钉住那天）
                </p>
                {turns.marks.length === 0 ? (
                  <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
                    {plain(t.empty_note ?? "这一段窗口里没有已确认的转折点。")}
                  </p>
                ) : (
                  <ul className="mt-1 space-y-0.5">
                    {turns.marks.map(({ turn }) => (
                      <li key={`${turn.date}-${turn.kind}`}>
                        <button type="button" onClick={() => jumpTo(turn.date)}
                          title={`在图上钉住 ${turn.date}`}
                          className={cn("text-left text-[11px] leading-relaxed underline-offset-2 hover:underline",
                            turn.kind === "peak" ? "text-danger" : "text-success")}>
                          {turn.kind === "peak" ? "▼ " : "▲ "}{turn.text}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {turns.unmatched > 0 && (
                  <p className="mt-0.5 text-[10px] leading-relaxed text-muted-foreground/70">
                    另有 {turns.unmatched} 个转折点在这根日期轴上找不到对应期（落在画出来的这一段之外，图上没画）。
                  </p>
                )}
              </div>
            );
          })}

          {/* ── 点一个点显示具体日期（用户第 2 条需求）＋ 两把尺同一天并排 ── */}
          {pinned !== null && (
            <div className="mt-2 rounded-lg border border-primary/40 bg-primary/5 p-2">
              <div className="flex items-center gap-2">
                <span className="text-[12px] font-semibold">
                  {reading?.date ?? dates[pinned] ?? NULL_TEXT}
                </span>
                <span className="text-[11px] text-muted-foreground">（点图上任意一处换一天；再点一次还是这一天）</span>
                <button type="button" onClick={() => setPinned(null)}
                  className="ml-auto text-[11px] text-muted-foreground underline-offset-2 hover:underline">
                  取消钉住
                </button>
              </div>
              {(reading?.lines ?? []).map((l, i) => (
                <p key={i} className="mt-1 text-[11px] leading-relaxed">{plain(l)}</p>
              ))}
              {pinned !== null && scales.map(s => {
                const hit = (s.series_view[spec.key]?.anomalies ?? []).find(a => a.date === dates[pinned]);
                return hit ? (
                  <p key={s.key} className="mt-1 text-[11px] leading-relaxed text-warning">
                    {s.short}：{plain(hit.text)}
                  </p>
                ) : null;
              })}
            </div>
          )}

          {/* ── 分档图例（用户第 4 条：历史临界范围的数值全部交出）── */}
          {spec && scales.map(s => {
            const v = s.series_view[spec.key];
            const legend = bandLegend(v, d?.levels);
            if (!legend) return null;
            return (
              <p key={s.key} className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                {s.short}·{spec.label}的历史临界：{legend}
                {v?.bands?.from ? `；样本区间 ${v.bands.from} 到 ${v.bands.to}` : ""}
              </p>
            );
          })}
          {/* 不建档那句由后端交（净申赎＝有正负取分位不成立；份额/规模＝趋势量，
              理由走 `bandText` 在每一把尺那一行原样念出来，页面不自己判该不该建档）。 */}
          {spec && !spec.stock && (
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
              {spec.label}这一条不建历史档：{scales[0]?.series_view[spec.key]?.bands?.reason ?? NULL_TEXT}
            </p>
          )}

          {/* 用户 2026-10-29/31 那两条并起来读就是一句话：**一个模块一颗「这块的说明」，说明点开才弹**，
              而缺数／截断／告警那三类不许进弹窗。10-31 那一版为了"不长第二颗同名按钮"把这坨口径字典
              退回就地摘要行（`viaDrawer={false}`），于是同一个模块出现两种形状（上面一颗按钮 + 下面一行折叠）。
              现在收成一个形状：`Panel note` 撤掉，全部（后端 `rule` 原句 + 母体读法 + 字段义 + 覆盖 + 出处）
              进下面这一颗抽屉，`drawerTitle` 就用屏幕上这行块名；缺数／告警仍由 `CaliberFold` 自己分流摆在屏上。
              内容来源一个字没换，只是不再有两处出口。 */}
          <CaliberFold
            drawerTitle={`国家队代理指标 · 被动沪深300 ETF 占沪深300市值（两把尺）${d?.anchor ?? ""}`}
            caveats={d?.caveats}
            definitions={d?.definitions}
            coverage={{
              份额线: { from: d?.coverage?.share_from, to: d?.coverage?.share_to,
                       sessions: d?.coverage?.share_sessions },
              占比线: { from: d?.coverage?.ratio_from, to: d?.coverage?.ratio_to,
                       sessions: d?.coverage?.ratio_sessions },
              分母快照: { from: d?.coverage?.denom_from, to: d?.coverage?.denom_to,
                         sessions: d?.coverage?.denom_sessions },
            }}
            provenance={[
              "分子＝market_data.etf_share（上交所/深交所每日份额文件）＋ market_data.fund_daily（收盘价，单位换算只住写侧）",
              "分母＝market_data.stock_snapshot 每票流通市值 × reference_data.sector_member 的 ZS_沪深300 成分求和（本地自算，主尺）",
              "外部对照＝market_data.index_market_value（东财 push2 f116/f117，只给当天，实测与本地自算差 −0.08%）",
            ]}
            notes={[
              // 判据一句话（后端 `rule` 原句）原来在标题行那颗按钮里，现在并到这一颗抽屉的第一段
              d?.rule ? plain(d.rule) : null,
              ...scales.map(s => `${s.short}：${s.note}`),
              // 趋势怎么读（均线几个场、门槛多少、整段确认了几个）——2026-10-31 用户点名屏上只留图与数，
              // 这句进抽屉；判不了那一句仍然留在屏上（缺数类声明不进抽屉）。
              ...scales.map(s => `${s.short}·${spec?.label ?? ""}的趋势：${trendNote(s.series_view[spec?.key ?? ""] ?? undefined)}`),
              // 分母是哪把尺给的：换尺（nt-1 快照求和 → nt-2 当日日线自算）之后必须说清，
              // 否则"占比线突然有了全史"会被读成之前那段是缺采。
              `分母口径（${d?.spec ?? "—"}）：主尺「占沪深300流通市值」的分母＝当日日线自算`
              + '（300 成分每票「成交量 ÷ 换手率」反推流通股本 × 当日未复权收盘，再求和）；'
              + '10-31 之前那一版（`nt-1`）用的是 `stock_snapshot` 求和，那张表最早只到 2026-07-22，'
              + '所以 679 场里只有 47 场有占比 —— 换尺后同一天的两把尺实测差 −0.19%~−0.26%（旧批留在库里对账，没覆盖）。'
              + '备用尺「占总市值」的分母仍然只从快照来（总股本不在日线里），覆盖照旧短一截，界面上那行"有几分之几"会如实报。',
              ...(scales[0]?.series_view[spec?.key ?? ""]?.anomaly_meta?.rule
                ? [`${scales[0]?.short}·${spec?.label}：${scales[0]?.series_view[spec?.key ?? ""].anomaly_meta.rule}`] : []),
              ...(d?.coverage?.lag_note ? [d.coverage.lag_note[1]] : []),
            ]}
          />
          <Caliber text={d?.units ? Object.entries(d.units).map(([k, v]) => `${k}＝${v}`).join("｜")
            : "单位由后端 units 字段交，页面不换算" } />
        </>
      )}
    </Panel>
  );
}
