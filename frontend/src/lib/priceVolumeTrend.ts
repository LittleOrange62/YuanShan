/**
 * 「量价走势（日 / 周 / 月）」的**纯判读层**（零 import，能被 `node --test` 直接钉住）。
 *
 * 接口 `GET /api/stock/price-volume-trend`，聚合与均线只住 `duanxian/price_volume_trend.py` 一处。
 * 这一层只负责"画得对不对、话说得对不对"，不算任何指标：
 *  1. 价格轴留边（不然最高/最低那根贴着边框，看起来像被裁掉了）；
 *  2. **缺数不补 0**：`pct:null` 与 `pct:0` 是两句话（第一期没有基期 vs 真的平盘）；
 *  3. 最后一根 `partial` 要说"未走完"，不能让它与一整期比高低；
 *  4. 点数 <6 就不连线（3 个点连出来的"趋势"是骗人的），这一句由后端 `too_few` 决定，这里只照做。
 */

export interface PvBar {
  date: string;
  open?: number | null; high?: number | null; low?: number | null; close?: number | null;
  close_raw?: number | null;
  pct?: number | null;
  volume_wan_shares?: number | null; amount_yi?: number | null; turnover_sum?: number | null;
  bars?: number | null; partial?: boolean | null;
  ma3?: number | null; ma5?: number | null; ma6?: number | null; ma10?: number | null; ma20?: number | null;
}

export interface PvSeries {
  bars_total?: number | null; drawn?: number | null; rows?: PvBar[] | null;
  ma_keys?: string[] | null; min_bars?: number | null; too_few?: boolean | null;
  label_of?: string | null;
}

export interface PvPayload {
  available: boolean; reason?: string | null; code: string; name?: string | null; spec?: string | null;
  as_of?: string | null;
  daily?: PvSeries | null; weekly?: PvSeries | null; monthly?: PvSeries | null;
  want?: { daily?: number; weekly?: number; monthly?: number } | null;
  caliber?: string | null; definitions?: Record<string, string> | null;
  caveats?: { code?: string; text?: string }[] | null;
}

export const MIN_DRAW = 6;            // 与后端 MIN_BUCKETS 同一个门槛：只用于"画不画线"，不用于算指标

export function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function price(v: number | null | undefined): string {
  const n = numOrNull(v);
  return n === null ? "—" : n.toFixed(2);
}

export function pctText(v: number | null | undefined): string {
  const n = numOrNull(v);
  if (n === null) return "—";            // 第一期没基期：不是 0%
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}%`;
}

export function volText(v: number | null | undefined): string {
  const n = numOrNull(v);
  if (n === null) return "—";
  return n >= 1e4 ? `${(n / 1e4).toFixed(2)} 亿股` : `${n.toFixed(0)} 万股`;
}

export function amtText(v: number | null | undefined): string {
  const n = numOrNull(v);
  return n === null ? "—" : `${n.toFixed(2)} 亿`;
}

/** 期间标签：日线给日期，周线给"起于哪天的那一周"，月线给年月。 */
export function barLabel(b: PvBar, kind: "day" | "week" | "month"): string {
  const d = String(b.date ?? "");
  if (kind === "month") return `${d.slice(0, 4)}-${d.slice(5, 7)}`;
  if (kind === "week") return `${d.slice(5, 7)}-${d.slice(8, 10)} 那周`;
  return `${d.slice(5, 7)}-${d.slice(8, 10)}`;
}

export function isUp(b: PvBar): boolean | null {
  const o = numOrNull(b.open), c = numOrNull(b.close);
  if (o === null || c === null) return null;
  return c >= o;
}

/** 价格轴的上下界（含均线，否则均线会画到框外）。全缺时返回 null，调用方就不画。 */
export function priceExtent(rows: PvBar[], maKeys: string[] = []): { lo: number; hi: number } | null {
  const vals: number[] = [];
  for (const r of rows) {
    for (const k of ["high", "low", "open", "close", ...maKeys]) {
      const n = numOrNull((r as unknown as Record<string, unknown>)[k]);
      if (n !== null) vals.push(n);
    }
  }
  if (vals.length < 2) return null;
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo || hi || 1) * 0.06;
  return { lo: lo - pad, hi: hi + pad };
}

export function volMax(rows: PvBar[]): number | null {
  const vals = rows.map(r => numOrNull(r.volume_wan_shares)).filter((v): v is number => v !== null && v > 0);
  return vals.length ? Math.max(...vals) : null;
}

/** 一根柱子的 y 坐标序列（画 MA 折线用）。缺那格跳过，不连成穿过 0 的假线。 */
export function maPoints(rows: PvBar[], key: string, x: (i: number) => number,
  y: (v: number) => number): string {
  const pts: string[] = [];
  rows.forEach((r, i) => {
    const v = numOrNull((r as unknown as Record<string, unknown>)[key]);
    if (v === null) return;
    pts.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  });
  return pts.join(" ");
}

/**
 * 悬停那一格的话。两条纪律：
 *  · 同时给复权与不复权两个收价（历史那根差得远，只给一个就会被当成成交价读）；
 *  · 最后一根必须带"未走完（只含 N 根日线）"。
 */
export function barTooltip(b: PvBar, kind: "day" | "week" | "month", label: string): string {
  const head = kind === "month" ? `月线 ${label}` : kind === "week" ? `周线 起于 ${b.date}` : `日线 ${b.date}`;
  const lines = [
    head,
    `开 ${price(b.open)} 高 ${price(b.high)} 低 ${price(b.low)} 收 ${price(b.close)}`,
    `不复权收 ${price(b.close_raw)}（图上那根是前复权形状，以最新一场为基准）`,
    `涨跌 ${pctText(b.pct)}（与上一期收比）`,
    `量 ${volText(b.volume_wan_shares)} 额 ${amtText(b.amount_yi)} 换手相加 ${b.turnover_sum == null ? "—" : `${b.turnover_sum.toFixed(2)}%`}`,
    `含 ${numOrNull(b.bars) ?? "—"} 根日线`,
  ];
  if (b.partial) lines.push("⚠️ 本期未走完：这一根的收盘与量只到最新一场，别与上一整期比涨跌");
  return lines.join("\n");
}

/** 一块下面那行读数：最后一根 + 根数不足时的那句。 */
export function seriesCaption(s: PvSeries | null | undefined, kind: "day" | "week" | "month"): string {
  const rows = s?.rows ?? [];
  if (!rows.length) return "这一块一根都没有 —— 本机日线没覆盖到它";
  const last = rows[rows.length - 1];
  const bits = [
    `${barLabel(last, kind)} 收 ${price(last.close)}`,
    `涨跌 ${pctText(last.pct)}`,
    `量 ${volText(last.volume_wan_shares)}`,
    `画 ${numOrNull(s?.drawn) ?? rows.length} 根（本机共 ${numOrNull(s?.bars_total) ?? "—"} 根）`,
  ];
  if (s?.too_few) bits.push(`不足 ${MIN_DRAW} 根 ⇒ 只列读数不画线`);
  else if (last.partial) bits.push("最后一根未走完");
  return bits.join(" ｜ ");
}
