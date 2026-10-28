/**
 * 「近 30 根量价形状 + 历史最相近样本」的**纯判读层**（零 import，能被 `node --test` 直接钉住）。
 *
 * 接口是 `GET /api/stock/volume-similarity`；六个分量的权重与理由只住
 * `duanxian/volume_similarity.py` 一处（`WEIGHTS`）。这一层不算分、不改权，
 * 只把后端交出的读数说成人话。四条红线：
 *  1. **相近分高 ≠ 会涨**。样本库本身就是"近一年进入过启动母体的票"，读成"像它就一定会涨"
 *     是选择偏差 —— 那句声明必须在屏上，不能折进默认收起的说明框。
 *  2. **缺的那一维不算分**。`missing_parts` 非空时这一行的分是按剩下的维归一的，
 *     界面要说"少了哪几维"，不能让人以为是低分。
 *  3. **只在同板块档里比**。主板 10% 与双创 20% 的量能形状不可比，跨档不并榜。
 *  4. **启动后 5/10/20 日是"这只历史样本后来怎么走的"**，与"这只票今天"无关，列名要说清。
 */

export interface SimCounts {
  up_days?: number | null; down_days?: number | null; flat_days?: number | null;
  still_days?: number | null; dbl_days?: number | null; near_dbl_days?: number | null;
  calm_days?: number | null; long_gap_days?: number | null;
  range_16_18?: number | null; range_14_16?: number | null; range_12_14?: number | null;
  range_10_12?: number | null; range_08_10?: number | null;
  ratio_avg?: number | null; ratio_median?: number | null; ratio_p60?: number | null;
  ratio_p80?: number | null; ratio_p90?: number | null; ratio_max?: number | null;
  ratio_min?: number | null; std_days?: number | null;
}

export interface SimRow {
  security_code: string; name?: string | null; board?: string | null;
  launch_date?: string | null; cohort?: string | null; cohorts?: string[] | null;
  is_st?: boolean | null; board_group?: string | null; mood?: number | null;
  bars_used?: number | null;
  score?: number | null; parts?: Record<string, number | null> | null;
  weights_used?: Record<string, number> | null;
  missing_parts?: string[] | null;
  counts?: SimCounts | null;
  stats?: Record<"avg" | "median" | "p60" | "p80" | "p90" | "max" | "min" | "std", number | null> | null;
  trigger_len?: number | null; trigger_gain?: number | null; gain_first4?: number | null;
  equiv_limit_ups?: number | null;
  gain_points?: number | null; up_share?: number | null;
  late_vs_early?: number | null; volatility?: number | null;
  after_5?: number | null; after_10?: number | null; after_20?: number | null;
  sessions_after?: number | null;
}

export interface SimTarget {
  available?: boolean | null; code?: string; name?: string | null; board?: string | null;
  is_st?: boolean | null; window_from?: string | null; window_to?: string | null;
  counts?: SimCounts | null; bars_below_min?: boolean | null;
  ratios?: { d: string; v: number | null }[] | null;
  features?: {
    bars?: number | null; board_group?: string | null; mood?: number | null;
    avg?: number | null; median?: number | null; p60?: number | null; p80?: number | null;
    p90?: number | null; max?: number | null; min?: number | null; std?: number | null;
    rhythm?: { late_vs_early?: number | null; volatility?: number | null } | null;
    price_volume?: { gain_points?: number | null; up_share?: number | null } | null;
  } | null;
}

export interface SimilarityPayload {
  available: boolean; reason?: string | null; code: string;
  spec_version?: string | null; vr_spec?: string | null; window?: number | null;
  min_bars?: number | null; cohorts?: string[] | null; weights?: Record<string, number> | null;
  target?: SimTarget | null; neighbours?: SimRow[] | null;
  library_size?: number | null; candidates_after_filter?: number | null;
  same_code_dropped?: number | null; distinct_code?: boolean | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
  caliber?: string | null; definitions?: Record<string, string> | null;
  score_note?: string | null;
}

export const PART_LABEL: Record<string, string> = {
  shape: "量能形状", intensity: "强度分布", rhythm: "放量节奏",
  price_volume: "量价配合", mood: "大盘情绪", comparability: "可比性",
};

export function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function ratio(v: number | null | undefined, decimals = 2): string {
  const n = numOrNull(v);
  return n === null ? "—" : n.toFixed(decimals);
}

export function points(v: number | null | undefined, decimals = 1): string {
  const n = numOrNull(v);
  if (n === null) return "—";
  return `${n > 0 ? "+" : ""}${n.toFixed(decimals)} 点`;
}

/** 相近分档位：分是六个分量按权重合成的 0~100，档名只是给人个抓手，不是新判据。 */
export function scoreBand(score: number | null | undefined): { label: string; tone: "high" | "mid" | "low" | "missing" } {
  const n = numOrNull(score);
  if (n === null) return { label: "判不了", tone: "missing" };
  if (n >= 85) return { label: "很像", tone: "high" };
  if (n >= 70) return { label: "有点像", tone: "mid" };
  if (n >= 55) return { label: "一般", tone: "low" };
  return { label: "不太像", tone: "low" };
}

/** 缺了哪几维（这些维从权重里被剔掉了，不是 0 分）。 */
export function missingPartsSentence(row: SimRow): string {
  const miss = row.missing_parts ?? [];
  if (!miss.length) return "";
  return "没算的维：" + miss.map(k => PART_LABEL[k] ?? k).join("、")
    + " —— 这一行的分是按剩下的维归一的，不是扣分";
}

/** 像在哪：把六个分量里最高的两维念出来。 */
export function topPartsSentence(row: SimRow, max = 2): string {
  const entries = Object.entries(row.parts ?? {})
    .filter(([, v]) => numOrNull(v) !== null) as [string, number][];
  if (!entries.length) return "没有可比的分量";
  entries.sort((a, b) => b[1] - a[1]);
  return entries.slice(0, max).map(([k, v]) => `${PART_LABEL[k] ?? k} ${v.toFixed(0)}`).join("、");
}

/** 启动后走势那一句：这是"那个历史样本后来怎么走"，不是对本票的预测。 */
export function afterSentence(row: SimRow): string {
  const a5 = numOrNull(row.after_5), a10 = numOrNull(row.after_10), a20 = numOrNull(row.after_20);
  if (a5 === null && a10 === null && a20 === null) {
    return "启动日之后本机没有可读的日线（新股刚上市、或这场还没落库）—— 不是「没涨」";
  }
  const tail = numOrNull(row.sessions_after);
  return `5 日 ${points(a5)} / 10 日 ${points(a10)} / 20 日 ${points(a20)}`
    + (tail !== null && tail < 20 ? `（本机只数得出 ${tail} 场）` : "");
}

/** 母体归属那一句（board4 与 board4_45 会对同一窗口各交一行，这里并成一条）。 */
export function cohortLabel(row: SimRow): string {
  const cs = row.cohorts ?? (row.cohort ? [row.cohort] : []);
  const NAME: Record<string, string> = {
    board4: "连续 4 板及以上", board4_45: "4 板及以上且前 4 日涨幅>45%",
    board4_or_gain45: "4 板及以上或前 4 日涨幅>45%", jump40: "前 4 日涨幅跳空",
  };
  return cs.map(c => NAME[c] ?? c).join(" + ") || "母体未标注";
}

/** 这一屏的三句"读之前要知道的话"：库有多大、名额怎么算、分数不说明什么。 */
export function readingSentences(d: SimilarityPayload): string[] {
  const out: string[] = [];
  const lib = numOrNull(d.library_size), cand = numOrNull(d.candidates_after_filter);
  if (lib !== null) {
    out.push(`样本库 ${lib} 个「票·启动日」窗口，与这一只同板块档、过了硬过滤的有 ${cand ?? 0} 个`
      + `；榜取 ${numOrNull((d.neighbours ?? []).length)} 只（同票多窗口只留最像的一个）。`);
  }
  const dropped = numOrNull(d.same_code_dropped);
  if (dropped) out.push(`本次把 ${dropped} 个「同一只票的第二个启动窗口」合掉了 —— 十个名额是十只票。`);
  out.push(d.score_note ?? "相近分的口径见后端那句。");
  return out;
}

/**
 * 逐日倍数那根"走势"示意（与「启动前量能比对」那张表同一画法）：
 * `null` 那格画成缺口，不当 0 —— 那天没有效日线，画成 0 就是在形状图上造假。
 */
export function sparkPath(ratios: { d: string; v: number | null }[] | null | undefined,
  width = 92, height = 22): { path: string; gaps: number[] } {
  const vals = (ratios ?? []).map(r => numOrNull(r.v));
  if (!vals.length) return { path: "", gaps: [] };
  const present = vals.filter((v): v is number => v !== null);
  if (!present.length) return { path: "", gaps: vals.map((_v, i) => i) };
  const lo = Math.min(...present), hi = Math.max(...present);
  const span = hi - lo || 1;
  const step = width / Math.max(1, vals.length - 1);
  const gaps: number[] = [];
  let d = "";
  vals.forEach((v, i) => {
    if (v === null) { gaps.push(i); return; }
    const x = (i * step).toFixed(1);
    const y = (height - ((v - lo) / span) * height).toFixed(1);
    d += `${d ? " L" : "M"}${x},${y}`;
  });
  return { path: d, gaps };
}
