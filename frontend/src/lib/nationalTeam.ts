/**
 * 复盘页顶部「国家队代理指标」的读法层（两把尺并排的长线折线）。
 *
 * 判据、阈值、档位文字、异动那句话**全部只住后端 `duanxian/national_team.py`**，
 * 随 `thresholds`/`levels`/`rule`/`definitions`/`series_options` 交出 —— 这一层只做
 * 格式化与"缺数怎么说话"，不分档、不算分位、不写死 p95/0.02pp（写了就是第二份真相源，
 * 改阈值时屏幕会悄悄说谎），也不抄一份中文列名（图例与列头只从 `series_options` 读）。
 *
 * 四条界面上的硬规矩（与后端一一对应）：
 *  · null 一律 `—`：占比算不出就是算不出，不补 0、不插值、不把断线连过去；
 *  · 分母 2026-10-31 起改成**当日日线自算**（成交÷换手率反推流通股本 × 未复权收盘，对 300 成分求和），
 *    主尺「占流通市值」从 2024-01-02 起逐场有底；备用尺「占总市值」仍然只从快照来
 *    （总股本不在日线里），必然短一截 ⇒ 这一屏要把"哪几条线在这个窗口真的有数"现算出来给读者看
 *    （`lineAvailability`），均线与转折点判不判得了由后端每条序列自己报（`series_view[].trend`）；
 *    一屏画几场由 `viewLen` 按**档与宽度**共同决定（旧版写死 130 期，五个档按钮画出来是同一根线）；
 *  · 净申赎那一族**没有历史档位**（有正负的量级取分位不成立），屏上读 `bands.not_applicable`，
 *    不许落成"中性"；
 *  · 断线、异常标记、转折标记、悬停读数都按**索引**对齐两个尺各自的点列（同一批交易日），
 *    所以 `pairScales` 先把两把尺拉成同一根 x 轴，避免两条线各画各的日期。
 */

// ⚠️ 这一层**不许在运行时 import `@/lib/*`**：`test/nationalTeam.test.ts` 用
// `node --experimental-strip-types` 直接 import 本文件，别名 `@/` 在那儿解析不出来
// （只允许 `import type`，它会被剥掉）。与 `src/lib/riskSignal.ts` 同一份做法：
// 三个小工具自带一份，判据仍然全部在后端。

export const NULL_TEXT = "—";

/** 只认真数：null / NaN / Infinity 都按"判不了"处理（不是 0） */
export function fin(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** 缺数是 `—`，不是 0、不是空串 */
export function num(v: unknown, digits = 1): string {
  const n = fin(v);
  return n === null ? NULL_TEXT : n.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

export interface NtPoint {
  trade_date: string;
  etf_count: number | null;
  share_yi: number | null;
  size_yi: number | null;
  net_yi: number | null;
  float_mv_wy: number | null;
  ratio_float_pct: number | null;
  ratio_total_pct: number | null;
  missing: string[] | null;
  /** 这一天的分母是哪把尺给的：daily_derived（当日日线自算）／snapshot（快照求和，旧批） */
  denom_source?: string | null;
  /** 求和时真正有数的成分只数 / 当天成分名单总数 —— 不相等就是"分母少算了票" */
  denom_covered?: number | null;
  denom_members?: number | null;
}

export interface NtBands {
  available: boolean;
  not_applicable?: boolean;
  samples: number;
  reason?: string;
  from?: string | null;
  to?: string | null;
  /** 后端交来的跨度句（"45 个样本（46 个读数日），日期 … ~ …（跨 45 天）"）——档位必须带着它念 */
  span_note?: string | null;
  cuts?: { p10: number | null; p25: number | null; p50: number | null; p75: number | null; p90: number | null };
  labels?: Record<string, string>;
}

export interface NtAnomaly {
  date: string;
  kind: string;
  value: number | null;
  delta?: number | null;
  delta_pct?: number | null;
  gap?: number | null;
  threshold: number | null;
  span_note?: string | null;
  text: string;
}

/** 趋势块（后端 `trend_of` 一处算）：均线值逐期摆在 `ma` 里（与 `scale.points` 同长同序），
 *  顶/底在 `rows`。`available:false` 时 `ma` 是空数组、`reason` 说要紧的那句（判不了 ≠ 走得平）。 */
export interface NtTurn {
  date: string; kind: "peak" | "trough" | string; value: number | null;
  prev_date?: string | null; prev_value?: number | null; step?: number | null; text: string;
}

export interface NtTrend {
  smooth: number | null; th: number | null; unit?: string; samples: number;
  available: boolean; ma: (number | null)[]; rows: NtTurn[];
  reason?: string; th_note?: string | null; empty_note?: string | null;
}

export interface NtSeriesView {
  series: NtSeriesOption;
  bands: NtBands;
  current: number | null;
  band_key: string | null;
  band_label: string | null;
  anomaly_meta: { judge: string; threshold: number | null; samples: number;
                  span_note?: string | null; rule?: string | null };
  anomalies: NtAnomaly[];
  anomaly_count_all: number;
  anomaly_count_window: number;
  trend?: NtTrend;
}

export interface NtSeriesOption {
  key: string; label: string; unit: string; field: keyof NtPoint; kind: string;
  judge: string; stock: boolean; read: string;
}

export interface NtScale {
  key: string; label: string; short: string; note: string;
  etf_count_latest: number | null;
  share_sessions: number; share_from: string | null; share_to: string | null;
  ratio_sessions: number; ratio_from: string | null;
  ratio_total_sessions?: number; ratio_total_from?: string | null;
  points: NtPoint[];
  series_view: Record<string, NtSeriesView>;
}

/** 提示条的一行＝一个**事件**（日期 × 动作族），不是一行一个读数：
 *  两把尺 × 两条占比线量的是同一件事，合并成一条，各把尺的读数摆在 `parts` 里。
 *  每个 part 带后端措辞好的那一截 `phrase`（含单位与阈值），屏上直接摆格子 ——
 *  页面不自己拼「pp」「亿份」（拼了就是第二份真相源）。 */
export interface NtAlertItem {
  scale_key: string; scale_label: string; series: string; series_label: string;
  kind: string; date: string | null; value: number | null; threshold: number | null;
  delta?: number | null; text: string;
  family?: string; family_label?: string; scale_keys?: string[];
  parts_count?: number;
  /** 整句拆开的三段：`head` 那句"说的是同一件事只列一遍"、`body` 逐条读数、
   *  `tail` 是 `｜阈值样本：…` —— 同一条序列每个事件都一模一样，逐条复读八遍就是那 800 像素黄字 */
  head?: string; body?: string; tail?: string;
  /** 用户 2026-11-01 点名的五格：这一排每一个读数要能逐格读出来（措辞全在后端） */
  alert_type?: string;
  parts?: { scale_key: string; scale_short: string; series: string; series_label: string;
            value: number | null; delta?: number | null; gap?: number | null;
            threshold: number | null; phrase?: string; phrase_short?: string;
            metric?: string; value_text?: string; compare_text?: string;
            meaning_text?: string; alert_type?: string }[];
}

export interface NtPanel {
  available: boolean;
  reason?: string;
  spec?: string; table?: string;
  range?: string; range_label?: string;
  range_options?: { key: string; label: string; months: number | null }[];
  series_options?: NtSeriesOption[];
  anchor?: string;
  window?: { from: string; to: string; requested_from: string; sessions: number };
  coverage?: {
    share_sessions: number; share_from: string | null; share_to: string | null;
    ratio_sessions: number; ratio_from: string | null; ratio_to: string | null;
    denom_sessions: number; denom_from: string | null; denom_to: string | null;
    lag_note: [string, string] | null;
  };
  thresholds?: { anomy_q: number; band_cuts: Record<string, string>;
                 min_samples: Record<string, number>; floor_pp: number; floor_pct: number };
  levels?: Record<string, string>;
  rule?: string;
  definitions?: Record<string, string>;
  units?: Record<string, string>;
  scales?: NtScale[];
  alert?: { fired: boolean; count_all: number; raw_entries?: number;
            items: NtAlertItem[]; truncated: number; text: string };
  generated_at?: string;
  caveats?: { code: string; text: string; hard?: boolean }[];
}

/** 一屏默认的序列：用户点名的就是「占沪深300流通市值」 */
export const DEFAULT_SERIES = "ratio_float";
/** 一屏默认的时间档：用户 2026-11-01 点名「历史数据只到 2026-04-14 还欠早，至少 2 年」
 *  ⇒ 首屏就给整段（这一档的场数由后端 `RANGES` 定，页面不抄一份长度）。
 *  键名必须与后端 `range_options` 里那五个之一对得上，对不上后端会退回 1y 并写进 caveats。 */
export const DEFAULT_RANGE = "all";
/** 一场最少要占几像素：比这个还密就只用滑块平移、不硬画。
 *  0.5 是用户 2026-11-01 点名「历史数据只到 2026-04-14，还欠早，多找点数据回来，至少 2 年」之后定的：
 *  库里主尺实有 680 场（2024-01-02..2026-10-30 一场不缺，那是显示窗口不是数据），而按上一版 1.5 算，
 *  窄列绘图区 404 像素一屏只画得下 269 场 ⇒ 点「全部」画出来的还是最近一年多，
 *  读者收到的信息就成了"这份数据只有这么短"。
 *  0.5 让 404 像素装得下 808 场 ⇒ 现有 680 场在窄列也能整段画完，一根都不用藏。
 *  代价是密到这种程度时逐日淡线已经糊成一条带，所以那一层由页面 `RAW_MIN_STEP` 撤掉，
 *  形状交给那条粗均线（10 场均值，本身就把日间噪声抹平了）＋档带＋转折点来说。 */
export const MIN_PX_PER_SESSION = 0.5;
/** 一屏最少画几场：窗口太窄也要给出一个能看趋势的底（少于这个数就天天拖） */
export const VIEW_MIN = 60;
/** 一场至少占几像素才画逐日那一条淡线（与逐日小点）：比这个还密时淡线会糊成一团锯齿、
 *  把负责说方向的粗均线盖住。撤掉的只有"画"这一层 —— 读数、判据、点选都不受影响，
 *  且屏下必须当场说破"这一屏没画逐日线"，不许让读者自己猜线怎么不见了。 */
export const RAW_MIN_STEP = 1.2;

/** 一屏画几场：由「这一档一共多少场」与「绘图区现在多宽」共同决定。
 *  用户点哪一档就画哪一档的全部场次，只有密到画不开时才退回平移。
 *  旧版写死 130 那一版（用户 2026-10-31 点名「图表做的连贯一点」的根因）：
 *  点「全部（库里有多长就多长）」和点「近 3 个月」画出来是同一根 130 场的线 —— 五个档按钮等于没接。
 *  `drawWidth` 传绘图区的像素宽（不含左右留白）；拿不到宽度时调用方交一个兜底正值。 */
export function viewLen(total: number, drawWidth: number): number {
  if (total <= 0) return 0;
  const byWidth = Math.floor(Math.max(0, drawWidth) / MIN_PX_PER_SESSION);
  return Math.min(total, Math.max(VIEW_MIN, byWidth));
}

/** 提示那一排的一格（用户 2026-11-01：「这里还是不够清晰，要显示：指标名、时间、数值、
 *  与阈值的比较结果、结果含义（如异动提醒）」）。
 *  五个名字都由后端交（`metric`／`value_text`／`compare_text`／`alert_type`／`meaning_text`），
 *  这里只做两件事：把 `alert_type` 与 `meaning_text` 接成一句（"异动提醒：…"），
 *  以及在后端还没交齐这几格时**明说没交齐** —— 宁可屏幕上出现这一句，
 *  也不许页面自己拼一个"看起来像"的读数（那正是把缺数读成 0 的同一种错）。 */
export interface NtAlertCell {
  metric: string; valueText: string; compareText: string; meaningText: string;
  /** 后端那句短版整句（老接口没交五格时用它兜底，屏上要能看出是兜底） */
  phraseShort: string;
}

export function alertCells(it: NtAlertItem): {
  date: string; family: string; type: string; cells: NtAlertCell[];
  /** 一个事件里几条读数的"结果含义"往往是同一句（同一条判据）⇒ 整格合并成一份，
   *  四行复读同一句就是把当年那 800 像素黄字搬进了表格。 */
  sharedMeaning: string; incomplete: boolean } {
  const raw = it.parts ?? [];
  const cells: NtAlertCell[] = raw.map(p => ({
    metric: p.metric ?? "",
    valueText: p.value_text ?? "",
    compareText: p.compare_text ?? "",
    meaningText: p.alert_type && p.meaning_text ? `${p.alert_type}：${p.meaning_text}` : "",
    phraseShort: p.phrase_short || p.phrase || "",
  }));
  const first = cells[0]?.meaningText ?? "";
  return {
    date: it.date ?? NULL_TEXT,
    family: it.family_label ?? it.kind ?? NULL_TEXT,
    type: it.alert_type ?? NULL_TEXT,
    cells,
    sharedMeaning: cells.length > 1 && first && cells.every(c => c.meaningText === first) ? first : "",
    incomplete: cells.some(c => !c.metric || !c.valueText || !c.compareText || !c.meaningText),
  };
}

/** `｜阈值样本：…` 是同一条序列的固定信息，每个事件都念一遍＝八个事件复读八遍。
 *  这里把它收成整屏一份（去重后逐条摆），读数格子只摆当天那件事。 */
export function alertSampleNotes(items: NtAlertItem[]): string[] {
  const out: string[] = [];
  for (const it of items) {
    const t = (it.tail ?? "").replace(/^｜阈值样本：/, "").trim();
    if (t && !out.includes(t)) out.push(t);
  }
  return out;
}

/** 收起时那一行摘要（用户 2026-11-01：「以下内容收缩起来」）。
 *  收缩不等于把话说小：这一行必须仍然报出**有几个事件**与**最近一次是哪一天、越了多少**，
 *  所以默认折叠只改可见层数，不许改读数。
 *  「一条都没有」/「判不了」那两句本来就短，而且是最该直接看见的 ⇒ 原样交出去，折不折由调用方看 `fired`。 */
export function alertHeadline(d: NtPanel | null): string {
  const a = d?.alert;
  if (!a) return "接口还没交提示判定 —— 这一屏不猜有没有报警。";
  const items = a.items ?? [];
  if (!a.fired || !items.length) return a.text;
  const c = alertCells(items[0]);
  const one = c.cells[0];
  // 摘要那一行也讲五件事（时间／指标名／数值／跟谁比／什么类型），只是把"含义"那半句留给展开后
  const lead = one && one.metric
    ? `${one.metric} ${one.valueText}，${one.compareText}`
    : (one?.phraseShort || c.family);
  const tags = [c.family, c.type].filter(t => t && t !== NULL_TEXT).join(' ');
  const more = items.length > 1 ? `；这一排共交了 ${items.length} 个` : "";
  return `提示 ${a.count_all ?? items.length} 个事件（原始条目 ${a.raw_entries ?? "—"} 条）：`
    + `最近一次 ${c.date} ${tags}——${lead}${more}。`
    + "点开看逐条（每一行的五格：时间／指标名／数值／与阈值的比较／结果含义）。";
}

/** 缺数那些天的**连接线**（用户 2026-11-01：「没有数据的日子，也用曲线连贯的连接起来」，
 *  并且当场选定了"只动显示、不动判据"那一条）。两种形状一起管：
 *   · 两端都有读数、中间断了几场 ⇒ 把那两个读数直连；
 *   · 窗口某一头整段没有读数 ⇒ 把最近那一个读数**水平延伸**到头
 *     （备用尺「占总市值」就是这个形状：2026-07-22 之前库里根本没有当日总股本那一格）。
 *  画出来一律是虚线，而且它**不是读数**：分档、异动、均线、转折点走的仍然只有真有数的那些天，
 *  点上面对应的日子仍然交「这一天库里没有行（缺采，不是 0）」。 */
export function bridgeSegments(vals: (number | null)[], x: (i: number) => number,
                               y: (v: number) => number): string[] {
  const idx: number[] = [];
  vals.forEach((v, i) => { if (fin(v) !== null) idx.push(i); });
  if (idx.length === 0) return [];
  const at = (i: number, v: number) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`;
  const val = (i: number) => vals[i] as number;
  const out: string[] = [];
  if (idx[0] > 0) out.push(`${at(idx[0], val(idx[0]))} ${at(0, val(idx[0]))}`);
  for (let k = 1; k < idx.length; k += 1) {
    if (idx[k] - idx[k - 1] > 1) out.push(`${at(idx[k - 1], val(idx[k - 1]))} ${at(idx[k], val(idx[k]))}`);
  }
  const tail = idx[idx.length - 1];
  if (tail < vals.length - 1) out.push(`${at(tail, val(tail))} ${at(vals.length - 1, val(tail))}`);
  return out;
}

export function pointValue(p: NtPoint | undefined, field: keyof NtPoint): number | null {
  return p ? fin(p[field] as unknown) : null;
}

/** 两把尺拉成同一根 x 轴（并集、按日期升序）。返回日期与每把尺在每一天上的点。 */
export function pairScales(scales: NtScale[]): {
  dates: string[]; byScale: Record<string, (NtPoint | undefined)[]>;
} {
  const dates = Array.from(new Set(scales.flatMap(s => s.points.map(p => p.trade_date)))).sort();
  const byScale: Record<string, (NtPoint | undefined)[]> = {};
  for (const s of scales) {
    const map = new Map(s.points.map(p => [p.trade_date, p]));
    byScale[s.key] = dates.map(d => map.get(d));
  }
  return { dates, byScale };
}

/** 这一档序列在这个窗口里到底有几分之几：没有数的线不该装作在画 */
export function lineAvailability(pts: (NtPoint | undefined)[], field: keyof NtPoint):
  { have: number; total: number; first: string | null } {
  let have = 0;
  let first: string | null = null;
  pts.forEach((p, i) => {
    if (fin(p?.[field] as unknown) !== null) {
      have += 1;
      if (first === null) first = String(p?.trade_date ?? i);
    }
  });
  return { have, total: pts.length, first };
}

/** 横轴日期刻度：均匀取 max 个，首尾必留 */
export function dateTicks(dates: string[], max = 6): { i: number; text: string }[] {
  const n = dates.length;
  if (!n) return [];
  if (n === 1) return [{ i: 0, text: dates[0] }];
  const k = Math.min(Math.max(2, max), n);
  const out: { i: number; text: string }[] = [];
  for (let t = 0; t < k; t += 1) {
    const i = Math.round((t * (n - 1)) / (k - 1));
    if (!out.length || out[out.length - 1].i !== i) out.push({ i, text: dates[i] });
  }
  return out;
}

/** 档位文字 + 样本底：判不了就是 `—`，不许落成"中性"。
 *  那句"为什么不建档"读后端 `bands.reason` 原话，页面不复述（复述的那份会跟后端分叉）。 */
export function bandText(v: NtSeriesView | undefined): string {
  if (!v) return NULL_TEXT;
  if (v.bands.not_applicable) return v.bands.reason ?? "不建档";
  if (!v.bands.available) return v.bands.reason ?? "样本不够，判不了";
  if (!v.band_label) return NULL_TEXT;
  // 跨度句由后端交（"45 个样本（46 个读数日），日期 … ~ …（跨 45 天）"）；
  // 只念"历史 45 场"会被读成历史位置，那正是 10-29 被点名的那处误读。
  const span = v.bands.span_note;
  return span ? `${v.band_label}（这一条 ${span} 的分位）`
              : `${v.band_label}（这把尺历史 ${num(v.bands.samples, 0)} 场）`;
}

/** 分档临界线的图例：五个档名全部来自后端 `levels`，页面一个中文档名都不抄 */
export function bandLegend(v: NtSeriesView | undefined, levels?: Record<string, string>): string {
  const cuts = v?.bands?.cuts;
  const l = levels || {};
  if (!cuts || !v || !l.ultra_low || !l.ultra_high) return "";
  const f = (k: keyof typeof cuts) => num(cuts[k], 3);
  return `${l.ultra_low} ≤${f("p10")}｜${l.low} ≤${f("p25")}｜${l.mid} ≤${f("p75")}`
    + `｜${l.high} ≤${f("p90")}｜${l.ultra_high} >${f("p90")}`
    + `（${[l.ultra_low, l.low, l.mid, l.high, l.ultra_high].join("/")} 五档，`
    + '切点＝这一条全史的 p10/p25/p75/p90）';
}

/** 提示条那一句：先说有没有越界，再说这条序列本身判得了判不了 */
export function alertTitle(d: NtPanel | null): { fired: boolean; text: string } {
  const a = d?.alert;
  if (!a) return { fired: false, text: "接口还没交提示判定 —— 这一屏不猜有没有报警。" };
  return { fired: !!a.fired, text: a.text };
}

/** 悬停/点选那一天的完整读数（两条占比线都摆，另一把尺的同一天也摆） */
export function dayReadout(d: NtPanel, dates: string[], i: number,
                           seriesKey: string): { date: string; lines: string[] } | null {
  if (!d?.scales || i < 0 || i >= dates.length) return null;
  const date = dates[i];
  const lines: string[] = [];
  for (const s of d.scales) {
    const map = new Map(s.points.map(p => [p.trade_date, p]));
    const p = map.get(date);
    if (!p) { lines.push(`${s.short}：这一天库里没有行（缺采，不是 0）`); continue; }
    const v = s.series_view[seriesKey];
    const spec = v?.series;
    const val = spec ? fin(p[spec.field] as unknown) : null;
    const parts = [
      `${spec?.label ?? seriesKey} ${val === null ? NULL_TEXT : `${num(val, 3)}${spec?.unit ?? ""}`}`,
      `份额 ${num(p.share_yi, 2)} 亿份`,
      `净申赎 ${p.net_yi === null || p.net_yi === undefined ? NULL_TEXT : `${p.net_yi > 0 ? "+" : ""}${num(p.net_yi, 2)}`} 亿份`,
      `分母 ${p.float_mv_wy === null || p.float_mv_wy === undefined ? NULL_TEXT : `${num(p.float_mv_wy, 2)} 万亿元`}`,
      `清单 ${p.etf_count === null || p.etf_count === undefined ? NULL_TEXT : `${p.etf_count} 只`}`,
    ];
    const mt = missingText(p.missing);
    if (mt) parts.push(mt);
    lines.push(`${s.short}：${parts.join("｜")}`);
  }
  return { date, lines };
}

/** 把后端 `missing` 那几个码翻成读者能懂的三种缺法（码名与判据只住后端，这里只做词表）。
 *  屏幕上不许念机器码：未知码原样交出来（那说明后端加了一格而词表没跟上，要看得见）。 */
export const MISSING_TEXT: Record<string, string> = {
  float_mv_yuan: "那天 300 成分的日线整段没取到 ⇒ 反推不出流通市值，占比算不出",
  "float_mv_yuan.no_implied": "那天成分有日线，但换手率/成交量没给 ⇒ 反推不出流通股本，占比算不出",
  "float_mv_yuan.partial": "300 成分里有票当天反推不出流通股本（停牌或换手率没给）⇒ 分母偏小",
  "size_yuan.partial": "清单里有成员当天没收盘价 ⇒ 规模偏小",
};

export function missingText(codes: string[] | null): string | null {
  if (!codes?.length) return null;
  return `缺数标记 ${codes.map(c => MISSING_TEXT[c] ?? c).join("；")}`;
}

/** 异动标记：按日期回到索引上（图上画菱形用） */
export function anomalyIndex(dates: string[], v: NtSeriesView | undefined): Map<number, NtAnomaly> {
  const at = new Map<string, number>();
  dates.forEach((d, i) => at.set(d, i));
  const out = new Map<number, NtAnomaly>();
  (v?.anomalies ?? []).forEach(a => {
    const i = at.get(a.date);
    if (i !== undefined) out.set(i, a);
  });
  return out;
}

/** 转折点同样按日期回到索引（图上的 ▼/▲ 与下面那张清单读同一份，不各数一遍）。
 *  `unmatched` 是后端点了日期但这一屏的横轴上没有那一天（窗口边界）——屏上要说，
 *  否则"图上只有两个标记"会被读成"这段只有两个拐点"。 */
export function turnMarks(dates: string[], v: NtSeriesView | undefined):
  { marks: { i: number; turn: NtTurn }[]; unmatched: number } {
  const at = new Map<string, number>();
  dates.forEach((d, i) => at.set(d, i));
  const marks: { i: number; turn: NtTurn }[] = [];
  let unmatched = 0;
  (v?.trend?.rows ?? []).forEach(t => {
    const i = at.get(t.date);
    if (i === undefined) { unmatched += 1; return; }
    marks.push({ i, turn: t });
  });
  return { marks, unmatched };
}

/** 趋势那一行怎么说：判不了就说判不了（后端 `reason` 原话），
 *  有条件就念后端那句阈值原文（`th_note`）与"这一段一个拐点都没确认"的理由（`empty_note`）。
 *  页面一个字都不自己编 —— 窗口与门槛都在后端，抄一份就会在下次改阈值时变成谎话。 */
export function trendNote(v: NtSeriesView | undefined): string {
  const t = v?.trend;
  if (!t) return "后端没交趋势块（`series_view[].trend`）⇒ 页面不自己画均线、不自己找拐点";
  if (!t.available) return t.reason ?? "这一条的趋势判不了（后端没交原因）";
  const head = t.th_note ?? `均线近 ${t.smooth} 场；门槛读后端`;
  // `th_note` 后端已经把"整段共几个 / 本窗口内几个"两个数都写在里面了，这里不再数第二遍
  return (t.rows ?? []).length === 0
    ? `${head}。${t.empty_note ?? "这一段窗口里没有已确认的转折点。"}`
    : `${head}。点图上的标记或下面清单里的一行，就把那一天钉住。`;
}
