/**
 * 首页「风险提示」的读法层（折线图 + 区间背离）。
 *
 * 判据、阈值、档位文字、周期重算口径**全部只住后端 `duanxian/risk_signal.py`**，
 * 随 `thresholds`/`levels`/`rule`/`definitions` 交出 —— 这一层只做格式化和"缺数怎么说话"，
 * 不重算分档、不写死 40/20/−40（写了就是第二份真相源，改阈值时屏幕会悄悄说谎）。
 *
 * 三条界面上的硬规矩（与后端一一对应）：
 *  · null 一律 `—`：分位/背离/档位算不出就是算不出，不补 50、不落成"无明显背离"；
 *  · 阿姆斯那一族列没回填时 `arms` 只有 `{available:false, reason}`（没有 `series` 键），
 *    页面把 reason 原句摆出来（里面就有 `python -m app.compute.breadth --backfill`），
 *    宽度线照常画 —— 它不陪绑；
 *  · 周/月的比值是按成员日**合计重算**的，悬停里要带上 `bars_in_period`（数了几天）。
 *  · 趋势与转折点只画在后端认下来能站得住的地方：转折点读 `trend.rows`（平滑水位线上的 zigzag），
 *    背离那一族只读 `trend.density`/每期的 `risk_density` —— **页面不对背离自己做均线、不自己找拐点**
 *    （后端量过：背离逐期独立，lag1 自相关 0.01~0.03，对它做均线得到的拐点是噪声的平均）。
 */

export interface RiskPeriod {
  period: string;
  label: string;
  bars_in_period: number;
  up_count: number | null;
  down_count: number | null;
  net: number | null;
  up_ratio: number | null;
  limit_up_count?: number | null;
  up_amount: number | null;
  down_amount: number | null;
  arms_ratio: number | null;
  // 10-21 加的第二把尺：同一天的"家数占比 vs 成交额占比"（判据只住后端，这里只格式化）
  count_ratio?: number | null;
  amount_share_up?: number | null;
  share_gap?: number | null;
  width_pctile: number | null;
  arms_pctile: number | null;
  divergence: number | null;
  level: string | null;
  level_label: string | null;
  // 10-26 加的趋势两列（判据仍只住后端 `TREND_SMOOTH`/`DENSITY_WINDOW`）
  up_ratio_ma?: number | null;
  risk_density?: number | null;
}

/** 后端 `trend.rows` 的一行：平滑水位线上一个**已确认**的反转 */
export interface RiskTurn {
  period: string;
  label: string;
  kind: "peak" | "trough";
  ma_value: number | null;
  raw_up_ratio?: number | null;
  risk_density?: number | null;
  swing_pp?: number | null;
}

export interface RiskTrend {
  smooth?: number;
  zigzag_pp?: number;
  rows?: RiskTurn[];
  /** 窗口第一期正好是这一段最高/最低时交出来（它没有前置形态，不算转折点，但也不许悄悄丢掉） */
  boundary?: RiskTurn | null;
  density?: { window?: number; threshold?: number; computable?: number; judged?: number; periods?: number };
}

/** 图上标出来的极端点位（后端 `find_extremes()` 的行，两类共用一个形状） */
export interface RiskExtremeRow {
  period: string;
  label: string;
  kind: "divergence" | "collapse";
  width_pct: number | null;
  amount_share_up?: number | null;
  share_gap?: number | null;
  count_ratio?: number | null;
  arms_ratio?: number | null;
  limit_up_count?: number | null;
  severity: number;
  // 只有 collapse 那一类有：点名上一期（"权重松手之前"那一天）
  prev_period?: string;
  prev_label?: string;
  prev_width_pct?: number | null;
  prev_share_gap?: number | null;
  drop_pp?: number | null;
}

export interface RiskExtremes {
  divergence?: { matched?: number; dropped_by_cap?: number; rows?: RiskExtremeRow[] };
  collapse?: { matched?: number; dropped_by_cap?: number; rows?: RiskExtremeRow[] };
  thresholds?: Record<string, number>;
  kind_labels?: Record<string, string>;
  skipped_no_gap?: number;
  periods_in_window?: number;
}

export interface RiskThresholds {
  window: number;
  high: number;
  moderate: number;
  reverse: number;
  extremes?: Record<string, number>;
}

export interface RiskPanel {
  available: boolean;
  reason?: string;
  date?: string | null;
  months?: number;
  group?: string;
  window?: { from?: string; to?: string; from_actual?: string; to_actual?: string };
  coverage?: { sessions?: number; from?: string; to?: string };
  thresholds?: RiskThresholds;
  levels?: Record<string, string>;
  rule?: string;
  definitions?: Record<string, string>;
  arms?: {
    available: boolean;
    reason?: string;
    series?: unknown;
    coverage?: { rows_in_window?: number; rows_total?: number; last_backfilled?: string | null };
  };
  series?: RiskPeriod[];
  latest?: Partial<RiskPeriod> | null;
  extremes?: RiskExtremes;
  trend?: RiskTrend;
  month_options?: number[];
  group_options?: string[];
  caveats?: { code?: string | null; text?: string | null; hard?: boolean | null }[];
  generated_at?: string;
}

export const NULL_TEXT = "—";

/** 只认真数：null / NaN / Infinity 都按"判不了"处理（不是 0） */
export function fin(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function numText(v: unknown, digits = 1): string {
  const n = fin(v);
  return n === null ? NULL_TEXT : n.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

export function signedText(v: unknown, digits = 1): string {
  const n = fin(v);
  if (n === null) return NULL_TEXT;
  return `${n > 0 ? "+" : ""}${n.toLocaleString("zh-CN", { maximumFractionDigits: digits })}`;
}

/** 成交额：库里单位是元，屏幕按亿说（与首页其余成交额格子一致） */
export function yiText(v: unknown, digits = 2): string {
  const n = fin(v);
  return n === null ? NULL_TEXT : `${(n / 1e8).toLocaleString("zh-CN", { maximumFractionDigits: digits })} 亿`;
}

/** 档位 → 折线条颜色。**只按后端给的 level 键上色，不自己判阈值** */
export function levelTone(level: string | null | undefined): string {
  switch (level) {
    case "high": return "fill-danger";
    case "moderate": return "fill-warning";
    case "reverse": return "fill-info";
    case "none": return "fill-muted-foreground/40";
    default: return "fill-transparent";
  }
}

export function levelTextTone(level: string | null | undefined): string {
  switch (level) {
    case "high": return "text-danger font-semibold";
    case "moderate": return "text-warning";
    case "reverse": return "text-info";
    case "none": return "text-muted-foreground";
    default: return "text-muted-foreground/60";
  }
}

/**
 * 档位图例：从 `thresholds`/`levels` 拼一句人话。后端没交 ⇒ 交空串，
 * 页面**不许**退回一份自己写死的 "≥40 高危" —— 那正是"阈值住两处"的下场。
 */
export function thresholdsText(t?: RiskThresholds, levels?: Record<string, string>): string {
  if (!t || !levels) return "";
  const miss = [t.high, t.moderate, t.reverse, t.window].some(v => fin(v) === null);
  if (miss) return "";
  return `背离 = 宽度分位 − 阿姆斯分位（近 ${t.window} 期滚动样本）：`
    + `≥${t.high}「${levels.high ?? "high"}」 · ${t.moderate}~${t.high}「${levels.moderate ?? "moderate"}」 · `
    + `≤${t.reverse}「${levels.reverse ?? "reverse"}」 · 其余「${levels.none ?? "none"}」`;
}

/** 当前读数那一格的背离数字（latest 为 null / 全窗口判不了时给一句原因，不硬摆 0） */
export function latestDivergence(d: RiskPanel): { value: string; level: string; levelClass: string; note: string } {
  const lv = d.latest?.divergence;
  const n = fin(lv);
  if (n !== null) {
    return {
      value: signedText(lv, 1),
      level: d.latest?.level_label ?? NULL_TEXT,
      levelClass: levelTextTone(d.latest?.level),
      note: `宽度分位 ${numText(d.latest?.width_pctile, 1)} − 阿姆斯分位 ${numText(d.latest?.arms_pctile, 1)}`,
    };
  }
  if (d.arms && d.arms.available === false) {
    return { value: NULL_TEXT, level: NULL_TEXT, levelClass: "text-muted-foreground/60",
      note: "阿姆斯那一族列读不到，背离判不了（原因见下方黄条）—— 不是 0，也不是\"无背离\"。" };
  }
  return { value: NULL_TEXT, level: NULL_TEXT, levelClass: "text-muted-foreground/60",
    note: `这一窗口算得出背离的期数不够（分位要满 ${d.thresholds?.window ?? "—"} 期样本，不硬凑）。` };
}

/** 悬停全文：宽度三个原始数 + 阿姆斯两个合计与比值 + 分位/背离/档位 */
export function periodTooltip(p: RiskPeriod, group: string | undefined, d: RiskPanel): string {
  const g = group === "week" ? "周" : group === "month" ? "月" : "日";
  const head = `${p.period}（按${g}，${p.bars_in_period ?? NULL_TEXT} 个成员日${g !== "日" ? "，家数为期内日均、比值为期内合计重算" : ""}）`;
  const sm = fin(d.trend?.smooth);
  const dw = fin(d.trend?.density?.window);
  const width = `涨 ${numText(p.up_count, 1)} / 跌 ${numText(p.down_count, 1)} / 净 ${signedText(p.net, 1)} 家　上涨占比 ${p.up_ratio == null ? NULL_TEXT : `${p.up_ratio}%`}`
    + `　均线${sm === null ? "" : sm}期 ${numText(p.up_ratio_ma, 2)}%`;
  const arms = d.arms?.available === false
    ? `阿姆斯：读不到（${d.arms.reason ?? "见说明"}）`
    : `涨额 ${yiText(p.up_amount)} ÷ 跌额 ${yiText(p.down_amount)} = ${p.arms_ratio == null ? NULL_TEXT : p.arms_ratio.toFixed(3)}`;
  const div = p.divergence == null
    ? `分位/背离 —（宽度 ${numText(p.width_pctile, 1)}、阿姆斯 ${numText(p.arms_pctile, 1)}，样本不满 ${d.thresholds?.window ?? NULL_TEXT} 期或缺数就是判不了，不补 50）`
    : `宽度 ${numText(p.width_pctile, 1)} − 阿姆斯 ${numText(p.arms_pctile, 1)} = ${signedText(p.divergence, 1)}　${p.level_label ?? NULL_TEXT}`;
  const dens = `近${dw === null ? "" : dw}期背离密度 ${numText(p.risk_density, 0)} 期（达到中度及以上）`;
  return `${head}\n${width}\n${arms}\n${div}\n${dens}`;
}

/**
 * 横轴刻度（用户 2026-10-21：「图表中要能看得到具体的日期啊」）。
 * 均分取 `max` 个（含最后一期，因为"现在在哪"最该看见），标签规则：
 *  · 按日 → `MM-DD`；**跨年那一年带年份**（`2024-10-21`），因为 24/60 个月那一档的 `09-21` 到底是哪一年看不出来；
 *  · 按周/月 → 直接用后端的 `label`（`2026-W38` / `2026-09`，本来就没歧义）。
 * 一期都没有 ⇒ 空数组（页面不自己编刻度）。
 */
export function xTicks(rows: RiskPeriod[], max = 6): { i: number; text: string }[] {
  const n = rows.length;
  if (n === 0) return [];
  if (n === 1) {
    const only = String(rows[0].period);
    return [{ i: 0, text: /^\d{4}-\d{2}-\d{2}$/.test(only) ? only : (rows[0].label || only) }];
  }
  const cap = Math.min(Math.max(2, max), n);
  const idx: number[] = [];
  for (let k = 0; k < cap; k++) idx.push(Math.round((k * (n - 1)) / (cap - 1)));
  const uniq = [...new Set(idx)];
  let prevYear = "";
  return uniq.map(i => {
    const p = rows[i];
    const year = String(p.period).slice(0, 4);
    const cross = year !== prevYear && prevYear !== "";
    prevYear = year;
    if (/^\d{4}-\d{2}-\d{2}$/.test(String(p.period))) {
      return { i, text: cross ? String(p.period) : String(p.period).slice(5) };
    }
    return { i, text: p.label || String(p.period) };
  });
}

/** 悬停那一行的屏上读数（不是原生 title —— title 看不见摸不着，日期必须直接摆在图上/图边） */
export function hoverLine(p: RiskPeriod | null, group: string | undefined, d: RiskPanel): string {
  if (!p) return "把鼠标移到图上任意一处：那一期的完整日期与原始读数会停在这里（图下的日期刻度是横轴位置）。";
  const g = group === "week" ? "周" : group === "month" ? "月" : "日";
  const head = group === "day" || !p.bars_in_period
    ? `${p.period}（按${g}）`
    : `${p.period}（按${g} · ${p.bars_in_period} 个成员日，家数是期内日均）`;
  const sm = fin(d.trend?.smooth);
  const dw = fin(d.trend?.density?.window);
  const bits = [
    head,
    `宽度 ${p.up_ratio == null ? NULL_TEXT : `${p.up_ratio}%`}`,
    `均线${sm ?? NULL_TEXT}期 ${numText(p.up_ratio_ma, 2)}%`,
    `近${dw ?? NULL_TEXT}期背离 ${numText(p.risk_density, 0)} 期`,
    `涨停 ${numText(p.limit_up_count, 0)} 家`,
    `涨/跌 ${numText(p.up_count, 0)} / ${numText(p.down_count, 0)} 家`,
    `家数比 ${p.count_ratio == null ? NULL_TEXT : p.count_ratio.toFixed(2)}`,
    `阿姆斯 ${p.arms_ratio == null ? NULL_TEXT : p.arms_ratio.toFixed(3)}`,
    `涨票吃掉 ${p.amount_share_up == null ? NULL_TEXT : `${p.amount_share_up}%`} 的成交`,
    `家数−钱 ${signedText(p.share_gap, 2)}pp`,
  ];
  if (d.arms?.available === false) bits.push("（阿姆斯读不到，见下方黄条）");
  return bits.filter(Boolean).join(" · ");
}

/** 极端点位 → 图上标记的索引。找不到对应期（窗口换了/数据补齐中）不硬贴，报 `unmatched` 让界面念。 */
export function extremeMarks(rows: RiskPeriod[], d: RiskPanel):
  { marks: { i: number; row: RiskExtremeRow }[]; unmatched: number } {
  const at = new Map(rows.map((p, i) => [String(p.period), i]));
  const out: { i: number; row: RiskExtremeRow }[] = [];
  let unmatched = 0;
  for (const kind of ["divergence", "collapse"] as const) {
    for (const row of d.extremes?.[kind]?.rows ?? []) {
      const i = at.get(String(row.period));
      if (i === undefined) { unmatched += 1; continue; }
      out.push({ i, row });
    }
  }
  return { marks: out, unmatched };
}

/** 极端点位清单里那一行的话（数值全读后端，页面只拼句子、不重算） */
export function extremeLine(row: RiskExtremeRow): string {
  if (row.kind === "collapse") {
    return `${row.prev_label ?? NULL_TEXT} 宽度 ${numText(row.prev_width_pct, 2)}% `
      + `→ ${row.label} ${numText(row.width_pct, 2)}%（掉 ${numText(row.drop_pp, 2)}pp）`
      + `｜上一期家数−钱 ${signedText(row.prev_share_gap, 2)}pp`;
  }
  return `${row.label}：宽度 ${numText(row.width_pct, 2)}%（涨停 ${numText(row.limit_up_count, 0)} 家）`
    + `｜上涨票的家数比 ${row.count_ratio == null ? NULL_TEXT : row.count_ratio.toFixed(2)} 倍 `
    + `而成交额只 ${row.arms_ratio == null ? NULL_TEXT : row.arms_ratio.toFixed(2)} 倍`
    + `｜涨票吃掉 ${numText(row.amount_share_up, 2)}% 的成交 ⇒ 家数比钱多 ${signedText(row.share_gap, 2)}pp`;
}

/**
 * 背离条的量程（用户 2026-10-26：「这个图看不出趋势和转折点」）。
 *
 * 原来那根条按 ±100 硬映射，而实测窗口内背离的取值只有 −40~+45 ⇒ 半高中位 2.1 像素，
 * 一整条被压成一根线，档位虚线全落在图外。这里改成**按窗口实际极值对称取量程**，
 * 但下限要容得下后端交的那几条档位线（否则「高危线」画到图外，看着像从来没到过高危）。
 * 一期都算不出 ⇒ 返回 null：页面**不画条**、改念后端那句「判不了」，不拿 0 顶。
 */
export function divergenceScale(rows: RiskPeriod[], th?: RiskThresholds):
  { bound: number; maxAbs: number; computable: number } | null {
  const vals = rows.map(r => fin(r.divergence)).filter((v): v is number => v !== null);
  if (!vals.length) return null;
  const maxAbs = Math.max(...vals.map(v => Math.abs(v)));
  const lines = [th?.high, th?.moderate, th?.reverse]
    .map(v => fin(v)).filter((v): v is number => v !== null).map(v => Math.abs(v));
  const bound = Math.max(maxAbs, ...lines, 1) * 1.06;
  return { bound: Math.round(bound * 10) / 10, maxAbs, computable: vals.length };
}

/** 密度线的量程：0 ~ 窗口内最大密度（单位是「期数」，整数）。一期都算不出 ⇒ null（不是 0）。 */
export function densityScale(rows: RiskPeriod[]): { max: number; computable: number } | null {
  const vals = rows.map(r => fin(r.risk_density)).filter((v): v is number => v !== null);
  if (!vals.length) return null;
  return { max: Math.max(...vals, 1), computable: vals.length };
}

/**
 * 转折点 → 图上索引。与 `extremeMarks` 同形状：**只按后端交的 `period` 找位置**，
 * 找不到就记 `unmatched` 让界面念出来，页面绝不自己补一个拐点。
 */
export function turnMarks(rows: RiskPeriod[], d: RiskPanel):
  { marks: { i: number; row: RiskTurn }[]; unmatched: number; boundary: { i: number; row: RiskTurn } | null } {
  const at = new Map(rows.map((p, i) => [String(p.period), i]));
  const marks: { i: number; row: RiskTurn }[] = [];
  let unmatched = 0;
  for (const row of d.trend?.rows ?? []) {
    const i = at.get(String(row.period));
    if (i === undefined) { unmatched += 1; continue; }
    marks.push({ i, row });
  }
  const b = d.trend?.boundary ?? null;
  const bi = b ? at.get(String(b.period)) : undefined;
  if (b && bi === undefined) unmatched += 1;
  return { marks, unmatched, boundary: b && bi !== undefined ? { i: bi, row: b } : null };
}

/** 清单里那一行：日期打头，说清是顶还是谷、平滑读数与当日原值、与前一个转折点差多少 */
export function turnLine(row: RiskTurn, smooth?: number, isBoundary = false): string {
  const ma = `${smooth ? `${smooth} 期` : ""}平滑`;
  const kind = row.kind === "peak" ? "顶" : "谷";
  if (isBoundary) {
    return `窗口第一期 ${row.period} 就是这一段的${kind}（${ma}宽度 ${numText(row.ma_value, 2)}%，当日 ${numText(row.raw_up_ratio, 2)}%）`
      + ` —— 它前面没有形态可比，所以不算「转折点」，只作为量程的参照摆在这里。`;
  }
  const swing = fin(row.swing_pp) === null ? "" : `，与前一个转折点相差 ${numText(row.swing_pp, 1)}pp`;
  return `${row.period} ${ma}宽度${kind} ${numText(row.ma_value, 2)}%（当日 ${numText(row.raw_up_ratio, 2)}%${swing}）`;
}

/** 当前读数下面那一行：平滑水位 + 背离密度（两样都可能是「判不了」，各自说清） */
export function trendLine(d: RiskPanel): string {
  const smooth = fin(d.trend?.smooth);
  const ma = fin(d.latest?.up_ratio_ma);
  const den = fin(d.latest?.risk_density);
  const win = fin(d.trend?.density?.window);
  const bits: string[] = [];
  bits.push(ma === null
    ? `${smooth === null ? "平滑" : `平滑 ${smooth} 期`}宽度 —（窗口内有效样本不满 ${smooth ?? "—"} 期，不拿几期硬凑）`
    : `${smooth === null ? "平滑" : `平滑 ${smooth} 期`}宽度 ${numText(ma, 2)}%（当日 ${numText(d.latest?.up_ratio, 2)}%）`);
  bits.push(den === null
    ? `近 ${win ?? "—"} 期高危密度 —（这一档判不了，不是「这段一点都不危险」）`
    : `近 ${win ?? "—"} 期里 ${numText(den, 0)} 期达到中度及以上背离`);
  return bits.join(" · ");
}

/** 后端 caveat 的原句（页面念它，不自己复述判据）；没交 ⇒ null */
export function caveatText(d: RiskPanel, code: string): string | null {
  const hit = (d.caveats ?? []).find(c => c?.code === code);
  return hit?.text ? String(hit.text) : null;
}

/**
 * 趋势那一档的图例：参数全读后端 `trend`（平滑窗口／幅度门槛／密度窗口与门槛）。
 * 后端没交齐 ⇒ 空串，页面**不许**退回自己写死的一份「MA10 / 10pp / 近20期」。
 */
export function trendText(t?: RiskTrend): string {
  if (!t) return "";
  const sm = fin(t.smooth), zz = fin(t.zigzag_pp);
  const dw = fin(t.density?.window), dth = fin(t.density?.threshold);
  if (sm === null || zz === null) return "";
  const head = `趋势：宽度取近 ${sm} 期均线，只记幅度 ≥${zz}pp 且已被反向走够的反转`
    + `（末端未确认的拐点一律不画）；本窗口确认了 ${numText(fin(t.rows?.length), 0)} 个`;
  return dw === null || dth === null
    ? `${head}。`
    : `${head}；背离密度 = 近 ${dw} 期里有几期背离 ≥${dth}（达到中度及以上）。`;
}

/**
 * 窗口/聚合档的按钮列表：**只认后端交的 `month_options`/`group_options`**。
 * 后端没交时页面用自己的默认档顶上并把"这是页面兜的底、可能与后端白名单不一致"说出来 ——
 * 静默拿一份自己抄的列表去按后端白名单筛，就是"筛了个寂寞"那一族。
 */
export function optionSources(d: RiskPanel | null):
  { months: number[]; groups: string[]; monthsFromBackend: boolean; groupsFromBackend: boolean } {
  return {
    months: d?.month_options && d.month_options.length ? d.month_options : [3, 6, 12, 24, 36, 60],
    groups: d?.group_options && d.group_options.length ? d.group_options : ["day", "week", "month"],
    monthsFromBackend: Boolean(d?.month_options?.length),
    groupsFromBackend: Boolean(d?.group_options?.length),
  };
}

export const GROUP_LABELS: Record<string, string> = { day: "按日", week: "按周", month: "按月" };
