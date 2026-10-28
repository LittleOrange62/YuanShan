/**
 * 「财务多期与外推」的**纯判读层**（零 import，能被 `node --test` 直接钉住）。
 *
 * 接口是 `GET /api/stock/fundamentals`；三条外推的判据只住
 * `app/compute/financial_forecast.py` 一处（`finfc-1`），字段义只住
 * `duanxian/stock_fundamentals.py` 一处。这一层**不算任何数**，只把后端交出的读数
 * 说成一句人话。四件最容易糊的事：
 *  1. `cum`（年初到报告期累计）与 `single`（本季度单季）**不可混用**，同比也是各档各自的；
 *  2. 外推**不是分析师预期**：两法分歧超阈值时后端不给单一数，只给 `method_a`/`method_b`，
 *     界面这时必须并排念两法，不许自己平均；
 *  3. 缺数一律 null（不是 0），且要说为什么缺（期数不足 / 基期缺 / 分歧大）；
 *  4. 那份财务有多旧按 `fetched_age_hours` 念，源断供时它停在最后一场，不冒充当期。
 */

export interface PeriodRow {
  period: string;
  revenue?: number | null; revenue_yoy?: number | null; revenue_yoy_src?: number | null;
  revenue_qoq?: number | null;
  net_profit?: number | null; net_profit_yoy?: number | null; net_profit_qoq?: number | null;
  deduct_profit?: number | null;
  gross_margin?: number | null; net_margin?: number | null; roe_single?: number | null;
}

export interface ForecastMethod {
  growth_percent?: number | null; season_ratio?: number | null;
  base_period?: string | null; value?: number | null;
}

export interface ForecastBlock {
  next_quarter: {
    label?: string | null; basis?: string | null; target?: string | null; value?: number | null;
    method_a?: ForecastMethod | null; method_b?: ForecastMethod | null;
    divergence?: number | null; band_low?: number | null; band_high?: number | null;
    band_note?: string | null; quarters_used?: number | null; reason?: string | null;
  };
  this_year: {
    year?: number | null; reported_through?: string | null; disclosed?: number | null;
    missing_quarters?: { period: string; value?: number | null; basis?: string | null;
      fail_reason?: string | null }[] | null;
    forecast_value?: number | null; basis?: string | null; reason?: string | null;
    q4_caveat?: string | null;
  };
  next_year: {
    year?: number | null; growth_percent?: number | null; value?: number | null;
    scenario_low?: number | null; scenario_high?: number | null; band_note?: string | null;
    years_used?: number | null; scenario_years_used?: number | null;
    cagr_basis?: string | null; reason?: string | null;
  };
}

export interface FundamentalsPayload {
  available: boolean; reason?: string | null; code?: string;
  unit?: string | null; period_kind_note?: string | null;
  latest_report_date?: string | null; fetched_at?: string | null;
  fetched_age_hours?: number | null; quarters_available?: number | null;
  rows_available?: number | null; kinds?: string[] | null;
  quarters?: PeriodRow[] | null; annual?: PeriodRow[] | null;
  forecast?: Record<"revenue" | "net_profit", ForecastBlock> | null;
  caliber?: string | null; definitions?: Record<string, string> | null;
  caveats?: { code?: string; text?: string }[] | null;
}

export const DIVERGENCE_MAX = 25;      // 与后端 `financial_forecast.DIVERGENCE_MAX` 同一个数，只用于念法

export function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function yi(v: number | null | undefined, decimals = 2): string {
  const n = numOrNull(v);
  return n === null ? "—" : `${n.toFixed(decimals)} 亿`;
}

export function pct(v: number | null | undefined, decimals = 1): string {
  const n = numOrNull(v);
  if (n === null) return "—";
  return `${n > 0 ? "+" : ""}${n.toFixed(decimals)}%`;
}

/** 报告期 → 「2026 年中报 / 2026 一季报 / 2025 年报」这种读者认得的叫法。 */
export function periodLabel(period: string | null | undefined): string {
  if (!period) return "—";
  const m = period.slice(5, 7);
  const y = period.slice(0, 4);
  return { "03": `${y} 一季`, "06": `${y} 中报`, "09": `${y} 三季`, "12": `${y} 年报` }[m] ?? period;
}

/** 这一格到底给了什么：单值 / 两法打架 / 判不了。措辞由后端给的理由决定，页面不编。 */
export function nextQuarterReading(b?: ForecastBlock["next_quarter"] | null):
  { tone: "ok" | "warn" | "missing"; value: number | null; sentence: string } {
  if (!b) return { tone: "missing", value: null, sentence: "后端没交这一格" };
  const v = numOrNull(b.value);
  if (v !== null) {
    const band = b.band_low != null && b.band_high != null
      ? `，同法回放区间 ${yi(b.band_low)}~${yi(b.band_high)}` : "";
    return { tone: "ok", value: v, sentence: `${b.label ?? "下一季"}：${yi(v)}（${b.basis ?? "依据未标注"}）${band}` };
  }
  const a = b.method_a?.value ?? null, bb = b.method_b?.value ?? null;
  if (a !== null || bb !== null) {
    return { tone: "warn", value: null,
      sentence: `${b.label ?? "下一季"}不给单一数：同比外推 ${yi(a)}、季节环比 ${yi(bb)}，`
        + `相差 ${numOrNull(b.divergence) ?? "—"}%（超过 ${DIVERGENCE_MAX}% 就不合成一个数）。${b.reason ?? ""}` };
  }
  return { tone: "missing", value: null,
    sentence: `${b.label ?? "下一季"}判不了：${b.reason ?? "后端没给原因"}` };
}

/** 「当年」那一格：已披露部分原样保留 + 补上去的季，一句念清。 */
export function thisYearReading(t?: ForecastBlock["this_year"] | null): string {
  if (!t) return "后端没交「当年」这一格";
  if (numOrNull(t.forecast_value) === null) return `当年判不了：${t.reason ?? "后端没给原因"}`;
  const miss = (t.missing_quarters ?? []).filter(q => q && q.period);
  const done = numOrNull(t.disclosed) ?? 0;
  const failed = miss.filter(q => numOrNull(q.value) === null);
  const parts = [
    `${t.year ?? ""} 年：${yi(t.forecast_value)}`,
    `已披露到 ${periodLabel(t.reported_through)}（本机读到 ${done} 期）`,
    miss.length ? `补了 ${miss.length - failed.length} 个未披露季${failed.length ? `，${failed.length} 个季补不下去` : ""}`
      : "四季都已披露，这一格不是预测",
    t.basis ? `口径：${t.basis}` : "",
  ];
  return parts.filter(Boolean).join("；");
}

/** 明年那一格：点估计 + 情景带（不是置信区间）。 */
export function nextYearReading(n?: ForecastBlock["next_year"] | null): string {
  if (!n) return "后端没交「明年」这一格";
  if (numOrNull(n.value) === null) return `明年判不了：${n.reason ?? "后端没给原因"}`;
  const band = n.scenario_low != null && n.scenario_high != null
    ? `，情景带 ${yi(n.scenario_low)}~${yi(n.scenario_high)}（含点估计，不是置信区间）` : "";
  return `${n.year ?? "明年"} 年：${yi(n.value)}，按 ${pct(n.growth_percent)} 外推`
    + `（${n.cagr_basis ?? "加权口径未标注"}）${band}`;
}

/** 财务有多旧：小时数换成"几天"，>4 天就提示可能是源断供。 */
export function fetchedAgeSentence(hours: number | null | undefined): string {
  const n = numOrNull(hours);
  if (n === null) return "这份财务没交采集时间，新旧不知道（不是「刚采过」）";
  if (n <= 24) return `这份财务采于 ${n.toFixed(1)} 小时前`;
  const days = n / 24;
  if (days <= 4) return `这份财务采于 ${days.toFixed(1)} 天前`;
  return `这份财务已 ${days.toFixed(1)} 天没重采（本机只有一个源，它断供时这里就停在最后一场）`;
}

/** 逐期表里哪一列该念源给的同比、哪一列该念自己重算的。两列并排不是算错，是口径可当面比。 */
export function yoyPair(row: PeriodRow): { mine: string; src: string; differ: boolean } {
  const mine = numOrNull(row.revenue_yoy);
  const src = numOrNull(row.revenue_yoy_src);
  return { mine: pct(mine), src: pct(src), differ: mine !== null && src !== null && Math.abs(mine - src) > 0.5 };
}

/** 缺档（只有 cum 没有 single，或反之）要出声，不能只显示一张空表。 */
export function missingKindSentence(kinds: string[] | null | undefined): string | null {
  const have = new Set(kinds ?? []);
  if (!have.has("single") && !have.has("cum")) return "本机对这只票一次财务都没采到";
  if (!have.has("single")) return "只有累计档、没有单季档 ⇒ 环比与季节性全判不了，逐季那一叠会是空的";
  if (!have.has("cum")) return "只有单季档、没有累计档 ⇒ 年报那一叠与「明年」那一格判不了";
  return null;
}
