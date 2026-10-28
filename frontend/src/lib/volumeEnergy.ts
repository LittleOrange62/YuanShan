/**
 * 倍量**加权分档**（系统配置那一卡）在前端这一侧的纯算式与类型。
 *
 * 口径改于 2026-10-19 第二稿：算式是 `得分 = Σ 每档 ( 这一档在窗口里出现的次数 × 这一档的分值 )`。
 * 第一稿那套「占比＝分值÷Σ分值」「量纲 sum／top」已经被用户否掉
 * （原话：「其实不存在系数这个参数，系数这个说法是错的」），所以这里
 * **没有分母、没有占比、没有归一化** —— `shareDenominator`／`shareOf` 那一族整个删掉，
 * 不留没人调用的假出口。分值就是**一天落在这一档计几分**。
 *
 * 为什么单开一份：这一卡的算术只有一句 —— 每档的分值直接乘"这一档在窗口里出现了几场"再累加。
 * 它必须能在测试里直接拿函数算一遍（分值一改，那一档的一天上限与满窗上限跟着变），
 * 所以这里**不 import 任何带 IO 的模块**（`lib/api.ts` 那个 `ApiError` 用了构造函数参数属性，
 * node 的类型直 strip 不动它）。取数与保存住在 `components/VolumeEnergyPanel.tsx`。
 *
 * 三条界面上不许糊掉的线：
 *  1. **档表不在前端抄第二份**。18 档的档名与区间一律读接口 `bands`
 *     （判据只住 `app/compute/volume_energy.py` 的 `BANDS` 一处），这里只负责把读来的档摊开算。
 *  2. **缺数交 null，不当 0**。某一档没填分值就是 null（画面上是 —）；
 *     0 分是一条实质结论（这一档一分不计），与「没填」是两件事。
 *  3. **草稿不是生效**。界面一边改一边算的是「草稿会是什么样」，
 *     屏幕上生效那份的上限永远念接口 `scale.max_score`（保存并跑完重算才会换）。
 */

/** 一档的分值表：档名 → 分值（＝一天落在这一档计几分）。档名一律来自接口 `bands[].label`。 */
export type ScoreMap = Record<string, number | null | undefined>;

/** 接口 `bands`：档名与区间（`hi=null` 是开放尾档）。 */
export interface EnergyBand {
  label?: string | null;
  lo?: number | null;
  hi?: number | null;
}

/** `scale`／`status.scale`：这一份配置的数学上限与单位（第二稿：算式原文也在里面）。 */
export interface EnergyScale {
  window_days?: number | null;
  max_per_day?: number | null;
  max_score?: number | null;
  unit?: string | null;
  formula?: string | null;
  note?: string | null;
}

/** `status.distribution`：库里实测分布（不是理论上限）。 */
export interface EnergyDistribution {
  session?: string | null;
  rows?: number | null;
  with_value?: number | null;
  p50?: number | null;
  p90?: number | null;
  p99?: number | null;
  max?: number | null;
  min?: number | null;
  /** 得分 ≥ 0 的只数（门槛默认就是 0，见 `filter.min_energy`） */
  at_or_above_zero?: number | null;
}

/** `status.batches[]`：库里有哪几批、各多少行多少场。 */
export interface EnergyBatch {
  fp?: string | null;
  rows?: number | null;
  with_value?: number | null;
  sessions?: number | null;
  lo?: string | null;
  hi?: string | null;
  last_written?: string | null;
}

/** `status`：生效指纹 vs 库里批次。 */
export interface EnergyStatus {
  fingerprint?: string | null;
  in_sync?: boolean | null;
  profile_source?: string | null;
  broken?: boolean | null;
  batches?: EnergyBatch[] | null;
  /** 同一份批次的另一个名字：倍量榜那侧的 `energy` 块把它改名成了 `stored_batches`，
   *  配置卡这边后端交的是 `batches` —— 两个都读，读不到才说没读到。 */
  stored_batches?: EnergyBatch[] | null;
  scale?: EnergyScale | null;
  distribution?: EnergyDistribution | null;
  /** 生效那份的逐档读数（`score_global` 是存进去的分值，`points_global` 是实际用的那份） */
  bands?: { label?: string | null; lo?: number | null; hi?: number | null;
            score_global?: number | null; points_global?: number | null }[] | null;
}

/** `job`：后台全量重算的现场。 */
export interface EnergyJob {
  running?: boolean | null;
  started_at?: string | null;
  finished_at?: string | null;
  reason?: string | null;
  last?: Record<string, unknown> | null;
  error?: string | null;
}

/** `profile`：当前生效（或退回默认）的那份配置。第二稿里没有 `normalize` 这一层。 */
export interface EnergyProfile {
  scope?: string | null;
  global?: ScoreMap | null;
  by_market?: Record<string, ScoreMap> | null;
  profile_source?: string | null;
  broken?: boolean | null;
}

/** `GET /api/config/volume-energy` 的 `data`。 */
export interface EnergyConfig {
  profile?: EnergyProfile | null;
  fingerprint?: string | null;
  scale?: EnergyScale | null;
  status?: EnergyStatus | null;
  bands?: EnergyBand[] | null;
  default_scores?: ScoreMap | null;
  scopes?: string[] | null;
  markets?: string[] | null;
  /** 「进榜后逐日走势」那块母体的出厂档（＝当日前 N 名；出厂值只住后端一处，界面不写死） */
  top_n_default?: number | null;
  /** 分页每页行数的出厂档（同上，界面不写死 60） */
  page_size?: number | null;
  /** 名次规则原文：这张尺与名次是一套，所以配置卡也念一行 */
  rank_rule?: string | null;
  rank_rule_short?: string | null;
  job?: EnergyJob | null;
}

/** POST 体：形状由后端 `validate()` 定（少一档、多一档、非有限数都整笔拒；带 `normalize` 直接 422）。 */
export interface EnergyPayload {
  scope: string;
  global: Record<string, number>;
  by_market: Record<string, Record<string, number>>;
}

/** 只收有限数；其余（空、NaN、Infinity、文字）一律 null，交给界面念「这一档还没填」。 */
export function finiteScore(v: unknown): number | null {
  const n = typeof v === "number" ? v : Number(String(v ?? "").trim());
  if (v === null || v === undefined || v === "") return null;
  if (typeof n !== "number" || !Number.isFinite(n)) return null;
  return n < 0 ? null : n;
}

/**
 * 分值最大的那一档的分值（＝一天最多能加几分，满窗上限就是它 × 窗口场数）。
 * 读不到（一档都没填）⇒ null。**这一档是哪一档由分值决定，前端不写档名**：
 * 默认那份是 2.5-3.5，但用户把它改小，顶档就换人。
 */
export function scoreMax(scores?: ScoreMap | null): number | null {
  const vals = Object.values(scores ?? {}).map(finiteScore).filter((v): v is number => v !== null);
  return vals.length ? Math.max(...vals) : null;
}

/**
 * 一摊档位的实时读数（第二稿）：分值、这一档一天最多贡献、满窗（全落这一档）最多贡献。
 *
 * 「一天最多贡献」＝分值本身：某一天日量比落进这一档，得分就只加这么多。
 * 「满窗最多」＝分值 × `window_days`（40 场全落这一档），它才是这一档单独能顶到的天花板，
 * 与 `scale.max_score`（**分值最大的那一档** × 场数）是两件事，界面上分开念。
 * `isTop` 标出分值最大那一档（可能有并列，都算）——满窗上限的来源就是它。
 */
export interface BandLine {
  label: string;
  lo: number | null;
  hi: number | null;
  /** 分值＝一天落在这一档计几分 */
  score: number | null;
  /** 这一档一天最多贡献＝分值 */
  dayMax: number | null;
  /** 满窗（窗口场数全落在这一档）最多＝分值 × 场数 */
  windowMax: number | null;
  /** 这一档是不是当前草稿里分值最大的那一档（满窗上限的来源） */
  isTop: boolean;
}

export function bandLines(
  bands: EnergyBand[] | null | undefined,
  scores: ScoreMap | null | undefined,
  windowDays: number | null | undefined,
): BandLine[] {
  const win = typeof windowDays === "number" && Number.isFinite(windowDays) && windowDays > 0 ? windowDays : null;
  const top = scoreMax(scores);
  return (bands ?? []).map(b => {
    const label = String(b?.label ?? "");
    const score = finiteScore(scores?.[label]);
    return {
      label,
      lo: typeof b?.lo === "number" ? b.lo : null,
      hi: typeof b?.hi === "number" ? b.hi : null,
      score,
      dayMax: score,
      windowMax: score !== null && win !== null ? score * win : null,
      isTop: score !== null && top !== null && score === top,
    };
  });
}

/**
 * 草稿这份配置的满窗上限（＝分值最大的那一档 × 窗口场数）。
 * 只为「改完会是什么样」的实时预览；生效那份读的是接口 `scale.max_score`，两者在界面上分别标注。
 */
export function draftMaxScore(
  scores: ScoreMap | null | undefined,
  windowDays: number | null | undefined,
): number | null {
  const win = typeof windowDays === "number" && Number.isFinite(windowDays) && windowDays > 0 ? windowDays : null;
  const top = scoreMax(scores);
  if (top === null || win === null) return null;
  return top * win;
}

/** 区间念法：尾档（`hi` 没给）念「X 以上」，不自己补一个上界。 */
export function bandRange(lo: number | null, hi: number | null): string {
  if (lo === null && hi === null) return "—";
  if (lo !== null && hi === null) return `${lo} 以上`;
  if (lo === null) return `不到 ${hi}`;
  return `${lo}~${hi}`;
}

/**
 * 组一份 POST 体（第二稿：**没有 normalize 这一层**，带上就会被后端 422 拒）。
 * `scope` 不是 `market` 时后端不看 `by_market`，这里也照后端形状交空对象，
 * 免得界面上留着草稿就以为已经存进去了。
 * 分值那一层收 `unknown`：界面上那是输入框里的字符串，交给 `finiteScore` 判（非数＝这一档没填＝不进取值）。
 */
export function buildPayload(input: {
  scope: string;
  global: Record<string, unknown> | null | undefined;
  byMarket?: Record<string, Record<string, unknown>> | null;
  bands: EnergyBand[] | null | undefined;
  markets: string[] | null | undefined;
}): EnergyPayload {
  const labels = (input.bands ?? []).map(b => String(b?.label ?? "")).filter(Boolean);
  const pick = (src: Record<string, unknown> | null | undefined): Record<string, number> => {
    const out: Record<string, number> = {};
    for (const lab of labels) {
      const v = finiteScore(src?.[lab]);
      if (v !== null) out[lab] = v;
    }
    return out;
  };
  const by: Record<string, Record<string, number>> = {};
  if (input.scope === "market") {
    for (const m of input.markets ?? []) {
      const src = input.byMarket?.[m];
      // 没单配的市场就不进 by_market —— 后端按「缺席＝落回全局」处理，交空表反而是把落回写成了显式零分
      if (src && labels.some(lab => finiteScore(src[lab]) !== null)) by[m] = pick(src);
    }
  }
  return { scope: input.scope, global: pick(input.global), by_market: by };
}

/** 草稿里哪些档还没填成有限数（界面提前点名，但**能不能存仍由后端说了算**）。 */
export function missingBands(bands: EnergyBand[] | null | undefined, scores: ScoreMap | null | undefined): string[] {
  return (bands ?? []).map(b => String(b?.label ?? ""))
    .filter(lab => lab && finiteScore(scores?.[lab]) === null);
}
