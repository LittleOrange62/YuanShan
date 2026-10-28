/**
 * 「首板形状预示」那一块的判读层：纯函数、零 import，可以 `node --test` 直接跑。
 *
 * 为什么单独一份（不散在组件里）：这一屏最容易被界面"顺手解释"错的地方是三处 ——
 * 分档区间到底包不包括端点、`year_agree` 那串 "6/6" 是不是"6 年都赚钱"、
 * 以及**结果为空/为缺**的时候该说什么。这三处都只在这里判一次，组件只排版。
 *
 * 铁律（与后端 `duanxian/first_board_view.py` 同一套）：
 *  · 缺数交 `—`，**不拿 0 顶上**：0 是"平盘"，一条方向相反的结论；
 *  · 所有解释性文字里的数都来自接口（`params`/`cuts`/`base`/`buckets`），界面不自造口径。
 */

export type FbOption = { value: string | number; label: string };

export type FbYearRow = {
  year: string; n: number; seg4_rate?: number | null; seg2_rate?: number | null;
  [k: string]: unknown;
};

export type FbSample = {
  launch_date: string; code: string; name?: string | null; board?: string | null;
  seg_len: number; seg_complete?: boolean | null; value?: number | null;
  r5?: number | null; ex5?: number | null; locked?: boolean | null;
};

export type FbRow = {
  label: string; n: number;
  range?: (number | null)[] | null; lo_obs?: number | null; hi_obs?: number | null;
  seg2_rate?: number | null; seg3_rate?: number | null; seg4_rate?: number | null; seg5_rate?: number | null;
  locked_rate?: number | null; no_bench?: number | null; win5?: number | null;
  year_agree?: string | null; metric_year_agree?: string | null;
  samples?: FbSample[] | null; per_year?: FbYearRow[] | null;
  r1_med?: number | null; r3_med?: number | null; r5_med?: number | null; r10_med?: number | null;
  ex1_med?: number | null; ex3_med?: number | null; ex5_med?: number | null; ex10_med?: number | null;
  maxup5_med?: number | null; low5_med?: number | null;
  [k: string]: unknown;
};

export type FbPayload = {
  available?: boolean | null; reason?: string | null; spec_version?: string | null;
  params?: { months?: number | null; bucket?: string | null; bucket_label?: string | null;
             n_buckets?: number | null; board?: string | null; complete_only?: boolean | null;
             exclude_locked?: boolean | null; min_bars?: number | null; metric?: string | null } | null;
  coverage?: { from?: string | null; to?: string | null; events?: number | null;
               codes?: number | null; latest_launch?: string | null; bucket_values?: number | null } | null;
  cuts?: (number | null)[] | null;
  base?: FbRow | null; buckets?: FbRow[] | null;
  years?: FbYearRow[] | null;
  caveats?: string[] | null; caliber?: string | null;
  definitions?: Record<string, string> | null;
  options?: { buckets?: FbOption[] | null; metrics?: FbOption[] | null; boards?: FbOption[] | null;
              months?: FbOption[] | null; n_buckets?: FbOption[] | null } | null;
};

export const DASH = "—";

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

/** 带符号的百分数（收益类）：缺就是 `—`，不是 0.00%。 */
export function signedText(v: unknown, digits = 2): string {
  const x = num(v);
  return x === null ? DASH : `${x >= 0 ? "+" : ""}${x.toFixed(digits)}%`;
}

/** 比例类读数（≥4 板率、胜率、买不进占比）：不带正号，一位小数。 */
export function rateText(v: unknown): string {
  const x = num(v);
  return x === null ? DASH : `${x.toFixed(1)}%`;
}

export function intText(v: unknown): string {
  const x = num(v);
  return x === null ? DASH : x.toLocaleString("zh-CN");
}

/**
 * 分档区间。切点 SQL 用的是 `col <= cut` ⇒ **左开右闭**：
 * `≤1` / `1～2（不含 1）` / `>2`。写成闭区间会把边界值重复计一次，
 * 而倍量天数那种整数列的边界值恰恰是最常见的那个值。
 */
export function rangeText(row: Pick<FbRow, "range" | "label">): string {
  const r = row.range ?? null;
  if (!r || (r[0] === null && r[1] === null)) return row.label === "全部首板" ? "全部" : DASH;
  const lo = num(r[0]);
  const hi = num(r[1]);
  const trim = (v: number) => (Number.isInteger(v) ? `${v}` : v.toFixed(2));
  if (lo === null && hi !== null) return `≤${trim(hi)}`;
  if (lo !== null && hi === null) return `>${trim(lo)}`;
  if (lo !== null && hi !== null) return `${trim(lo)}～${trim(hi)}（不含 ${trim(lo)}）`;
  return DASH;
}

/** 这一档实测到的最小/最大，用来说明"切点是切出来的、数据实际长什么样是另一回事"。 */
export function observedText(row: Pick<FbRow, "lo_obs" | "hi_obs">): string {
  const lo = num(row.lo_obs);
  const hi = num(row.hi_obs);
  if (lo === null || hi === null) return "";
  const f = (v: number) => (Number.isInteger(v) ? `${v}` : v.toFixed(2));
  return lo === hi ? `实测都是 ${f(lo)}` : `实测 ${f(lo)}～${f(hi)}`;
}

/** "6/6" 这种读法必须翻译成人话：这是**与整族同向**的年份数，不是"6 年都赚钱"。 */
export function agreeText(a: unknown): string {
  if (typeof a !== "string" || !a.includes("/")) return DASH;
  const [same, total] = a.split("/").map(s => Number(s));
  if (!Number.isFinite(same) || !Number.isFinite(total) || !total) return DASH;
  return `逐年与整族同向 ${same}/${total}`;
}

export function agreeTone(a: unknown): "good" | "mid" | "bad" {
  if (typeof a !== "string" || !a.includes("/")) return "bad";
  const [same, total] = a.split("/").map(s => Number(s));
  if (!Number.isFinite(same) || !total) return "bad";
  const r = same / total;
  return r >= 0.8 ? "good" : r >= 0.5 ? "mid" : "bad";
}

export type Direction = { word: string; span: number | null; n: number };

/**
 * 一档一档看方向：同一列从第一档到最后一档，指标是**递增 / 递减 / 看不出**。
 *
 * 只数两端差值（不拟合），并且缺值直接跳过 —— 中间缺档时说"单调"是假话，
 * 所以缺档时降为"看不出方向"并交出实到档数，界面把那句一起念出来。
 */
export function direction(rows: FbRow[] | null | undefined, key: string): Direction {
  const list = rows ?? [];
  const vals = list.map(r => num(r[key] as unknown)).filter((v): v is number => v !== null);
  if (vals.length < 2) return { word: "看不出", span: null, n: vals.length };
  const span = vals[vals.length - 1] - vals[0];
  // 中间缺档时说"单调"是假话：先按"档数不全"报，再谈方向
  if (list.length !== vals.length) return { word: "档数不全，看不出", span, n: vals.length };
  const pairs: [number, number][] = [];
  for (let i = 0; i + 1 < list.length; i++) {
    const a = num(list[i][key] as unknown);
    const b = num(list[i + 1][key] as unknown);
    if (a !== null && b !== null) pairs.push([a, b]);
  }
  if (!pairs.length) return { word: "看不出", span, n: vals.length };
  if (pairs.every(([a, b]) => b >= a)) return { word: "递增", span, n: vals.length };
  if (pairs.every(([a, b]) => b <= a)) return { word: "递减", span, n: vals.length };
  return { word: Math.abs(span) < 1e-9 ? "持平" : "不单调", span, n: vals.length };
}

/** 一句话结论：最好/最差那一档 + 与整族的差 + 逐年同不同向。数字全部来自接口，界面不做二次计算。 */
export function headline(buckets: FbRow[] | null | undefined, base: FbRow | null | undefined,
                         key: string, label: string): string {
  const rows = (buckets ?? []).filter(b => num(b[key] as unknown) !== null);
  if (rows.length < 2 || !base) return "";
  const bv = num(base[key] as unknown);
  const best = rows.reduce((a, b) => ((num(b[key] as unknown) ?? 0) > (num(a[key] as unknown) ?? 0) ? b : a));
  const worst = rows.reduce((a, b) => ((num(b[key] as unknown) ?? 0) < (num(a[key] as unknown) ?? 0) ? b : a));
  const d = direction(buckets, key);
  const delta = (x: FbRow) => {
    const v = num(x[key] as unknown);
    return (v === null || bv === null) ? "" : `，比整族${v >= bv ? "高" : "低"} ${Math.abs(v - bv).toFixed(key.endsWith("_rate") || key === "win5" || key === "locked_rate" ? 1 : 2)}${key.endsWith("_rate") || key === "win5" ? " 个百分点" : "%"}`;
  };
  return `${label}从第一档到最后一档${d.word}${d.span !== null ? `（头尾差 ${d.span.toFixed(2)}）` : ""}。`
    + `最突出的是 ${best.label}（${rangeText(best)}）：${fmtKeyValue(key, best[key])}${delta(best)}，`
    + `最弱的是 ${worst.label}（${rangeText(worst)}）：${fmtKeyValue(key, worst[key])}${delta(worst)}。`
    + `两条都还要看逐年同不同向（${agreeText(best.year_agree)} / ${agreeText(worst.year_agree)}）。`;
}

export function fmtKeyValue(key: string, v: unknown): string {
  if (key.endsWith("_rate") || key === "win5" || key === "locked_rate") return rateText(v);
  return signedText(v);
}

export function coverageText(p: FbPayload): string {
  const c = p.coverage ?? {};
  const pr = p.params ?? {};
  const parts = [
    `窗口 ${c.from ?? DASH} → ${c.to ?? DASH}`,
    `共 ${intText(c.events)} 条首板事件 / ${intText(c.codes)} 只票`,
    `库里最新首板日 ${c.latest_launch ?? DASH}`,
  ];
  if (num(c.bucket_values) !== null) parts.push(`这一列在当前窗口有 ${c.bucket_values} 个不同取值`);
  if (pr.board && pr.board !== "全部") parts.push(`只看${pr.board}`);
  if (pr.complete_only === false) parts.push("含还在走的段（段长是下界）");
  if (pr.exclude_locked === false) parts.push("含次日一字买不进的那些");
  return parts.join("，");
}

/** 参数变了要重发请求：这一屏没有"本地再算一遍"这条路，切点与分档都在库里算。 */
export function queryParams(input: {
  months: number; bucket: string; nBuckets: number; metric: string;
  board: string; completeOnly: boolean; excludeLocked: boolean; minBars: number;
}): Record<string, string | number> {
  return {
    months: input.months, bucket: input.bucket, buckets: input.nBuckets, metric: input.metric,
    board: input.board || "", complete_only: String(input.completeOnly),
    exclude_locked: String(input.excludeLocked), min_bars: input.minBars,
  };
}

/** 表头：列名固定几列 + 当前所选结果指标那一列（换指标就换列，键名必须等于行里的字段名）。 */
export function columnsFor(metric: string): { key: string; label: string }[] {
  const mk = `${metric}_med`;
  return [
    { key: "label", label: "档" },
    { key: "n", label: "样本" },
    { key: "seg2_rate", label: "≥2 板" },
    { key: "seg3_rate", label: "≥3 板" },
    { key: "seg4_rate", label: "≥4 板" },
    { key: "seg5_rate", label: "≥5 板" },
    { key: mk, label: "所选指标中位" },
    { key: "win5", label: "胜率" },
    { key: "r5_med", label: "5 场绝对" },
    { key: "low5_med", label: "5 场内最低" },
    { key: "locked_rate", label: "买不进" },
  ];
}

export const METRIC_IS_RATE = (key: string): boolean =>
  key === "win5" || key.endsWith("_rate");
