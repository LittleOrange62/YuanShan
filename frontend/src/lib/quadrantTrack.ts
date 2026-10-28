/**
 * 「启动前 30 场四维象限轨迹」那一块的判读层：纯函数、零 import，可以 `node --test` 直接跑。
 *
 * 这一块读的是**启动日之前**那 N 个有效交易日里，每只票在七个分量上的全市场名次怎么走。
 * 界面最容易把读数说反的四处都只在这里判一次，组件只排版：
 *  ① `rank_delta = rank_first − rank_last`，rank 越小越靠前 ⇒ **正数是名次上升**。
 *     这一条最容易被读反（列头就叫「名次变化（正＝上升）」，见 `RANK_DELTA_HEADER`）；
 *  ② 象限那几列（`quad_*`）只在 `key='oper_score'` 那一族有值，其余六族是 null。
 *     null 必须显示 `—`：显示 0 会被读成"这一族翻转过 0 次"，而真意是这一族根本没有象限；
 *  ③ `bars < window` 的行（次新、长期停牌、或启动日早于判据表第一天 2026-01-27）
 *     **仍进后端的中位数聚合**（后端没有排除它们），但界面要整行标灰并写明它与满窗的行不同跨度；
 *  ④ 缺就是 `—`，不拿 0 顶上（0 是一条方向相反的结论）。
 *
 * 铁律与后端 `duanxian/*quadtrack*` 同一套：所有解释性文字里的数都来自接口
 * （`params`/`coverage`/`keys`/`buckets`/`base`），界面不自造口径、不重算中位数。
 *
 * 合同版本：`spec_version = 'sqt-1'`（对不上时 `versionWarning` 会在屏幕上念出来）。
 */

export type SqOption = { value: string | number; label: string };

/** 象限占比分布：形如 `{Q1: 0.31, Q2: 0.22, Q3: 0.19, Q4: 0.28}`；整族没有象限时是 null。 */
export type SqShares = Record<string, number | null> | null;

export type SqKeyRow = {
  key: string; label: string; n: number | null;
  rank_first_med: number | null; rank_last_med: number | null; rank_delta_med: number | null;
  rank_up_share: number | null; rank_down_share: number | null;
  pct_first_med: number | null; pct_last_med: number | null;
  value_first_med: number | null; value_last_med: number | null;
  /** 下面四个 `quad_*` 只在 `key === 'oper_score'` 有值，其余六族后端交 null */
  quad_switches_med: number | null; quad_last_switch_gap_med: number | null;
  quad_first: SqShares; quad_last: SqShares;
  [k: string]: unknown;
};

/** 表二（答案）的行：三个 `buckets` 档 + 那一行 `base`，字段名同一套。 */
export type SqAggRow = {
  label: string; range: (number | null)[] | null; n: number | null;
  rank_delta_med: number | null; seg4_rate: number | null; seg_len_med: number | null;
  r5_med: number | null; ex5_med: number | null; win5: number | null;
  quad_switches_med: number | null;
  [k: string]: unknown;
};

/** 明细行：一只票的一个分量。`quad_first`/`quad_last` 在这里是**象限码**（如 Q2），不是分布。 */
export type SqDetailRow = {
  code: string | null; name: string | null; board: string | null; launch_date: string | null;
  cohort: string | null; capability_key: string | null; capability_label: string | null;
  bars: number | null;
  value_first: number | null; value_last: number | null; value_delta: number | null;
  rank_first: number | null; rank_last: number | null; rank_delta: number | null;
  rank_best: number | null; rank_worst: number | null;
  quad_first: string | number | null; quad_last: string | number | null;
  quad_switches: number | null; quad_last_switch_gap: number | null;
  outcome_len: number | null; outcome_gain4: number | null;
  [k: string]: unknown;
};

/** 明细的展开单位：**一只票的一次启动**（同一事件的七个分量并在一起看）。 */
export type SqEventGroup = {
  code: string | null; name: string | null; board: string | null; launch_date: string | null;
  cohort: string | null; key_count: number; bars: number | null; window_short: boolean | null;
  /** 本事件各分量里名次变化最好/最差的那两个数（只在本页交出的行里取极值，不是后端算的指标） */
  rank_delta_best: number | null; rank_delta_worst: number | null;
  quad_switches: number | null;
  outcome_len: number | null; outcome_gain4: number | null;
  rows: SqDetailRow[];
  [k: string]: unknown;
};

export type SqCoverage = {
  from?: string | null; to?: string | null; events?: number | null; codes?: number | null;
  earliest_score_date?: string | null; window_full_events?: number | null;
  window_short_events?: number | null;
};

export type SqParams = {
  cohort?: string | null; cohort_text?: string | null; months?: number | null;
  window?: number | null; key?: string | null; limit?: number | null;
};

export type SqPayload = {
  available?: boolean | null; spec_version?: string | null; reason?: string | null;
  params?: SqParams | null; coverage?: SqCoverage | null;
  keys?: SqKeyRow[] | null; buckets?: SqAggRow[] | null; base?: SqAggRow | null;
  rows?: SqDetailRow[] | null;
  caveats?: string[] | null; caliber?: string | null;
  definitions?: Record<string, string> | null;
  options?: {
    cohorts?: SqOption[] | null; keys?: SqOption[] | null;
    months?: SqOption[] | null; windows?: SqOption[] | null;
  } | null;
};

export const DASH = "—";

/**
 * 母体名单（接口没交 options 时兜底用）。
 *
 * **`anyfirst` 排第一并且是默认选中**：另外四档（`board4`/`board4_45`/`board4_or_gain45`/`jump40`）
 * 都是「按结果筛过的」——母体本身就是「后来走到 4 板／涨过四成」的那批，所以「≥4 板率」那一列在它们
 * 那里恒等于母体定义（同义反复，读它等于读自己）。只有 `anyfirst`＝全部首板（不筛结果）能让
 * 启动前的走向与后来的结果分开，这一列才有区分度。见 `seg4CellText`。
 */
export const COHORTS_FALLBACK: SqOption[] = [
  { value: "anyfirst", label: "全部首板（不筛结果）" },
  { value: "board4", label: "4 板及以上" },
  { value: "board4_45", label: "4 板及以上 · 前 4 日涨幅>45%" },
  { value: "board4_or_gain45", label: "4 板及以上 或 4 日涨幅>45%" },
  { value: "jump40", label: "4 日涨幅超四成（双创／北证）" },
];

/**
 * 「母体已经按结果筛过」的那四档：它们的共同点就是段长或涨幅已经达标，
 * 所以里面的 `seg4_rate` 不是发现，是定义（`anyfirst` 不在这一列里）。
 */
export const PRESCREENED_COHORTS: string[] =
  ["board4", "board4_45", "board4_or_gain45", "jump40"];

/** 这一档母体是不是按结果筛过的（null／空＝不知道，按没筛处理，不把话说成结论）。 */
export function isPrescreenedCohort(cohort: unknown): boolean {
  if (cohort === null || cohort === undefined) return false;
  const s = String(cohort);
  return s !== "" && PRESCREENED_COHORTS.indexOf(s) >= 0;
}

/** 窗口根数只有三档（后端 `window` 会拒别的值，界面也就这三个按钮）。 */
export const WINDOWS_FALLBACK: SqOption[] = [
  { value: 10, label: "启动日前 10 场" },
  { value: 20, label: "启动日前 20 场" },
  { value: 30, label: "启动日前 30 场" },
];

/** 回溯月数 1~12（接口没交 options 时的兜底清单）。 */
export const MONTHS_FALLBACK: SqOption[] =
  Array.from({ length: 12 }, (_, i) => ({ value: i + 1, label: `近 ${i + 1} 个月` }));

/** 分量键的兜底：只留「全部」这一档 —— 七个键的名字由后端 `options.keys` 交，界面不抄一份。 */
export const KEYS_FALLBACK: SqOption[] = [{ value: "", label: "全部七个分量（键名由接口交出）" }];

export const LIMITS = [200, 400, 600];

/**
 * 三句必须原样上屏的口径（用户点名「措辞自己组织但事实不许变」）。
 * 摆在说明框外面：这三句说的是"这一张表要怎么读"，藏在折叠框里等于没写。
 */
export const COPY = {
  /** 口径一（前半）：窗口不含启动日当天 */
  window: "窗口取的是启动日之前 N 个该票的有效交易日，不含启动日当天 —— 当天已经涨停，"
    + "分数被启动本身污染，所以窗口最后一场是 T−1。",
  /** 口径一（后半）：rank_delta 的方向 */
  rankDelta: "名次变化 = 窗口首场名次 − T−1 场名次（rank_first − rank_last）。rank 越小越靠前，"
    + "所以正数＝名次上升（往前列走），负数＝名次下滑。这一条最容易被读反，列头就按这句写。",
  /** 口径二：2026-01-27 之前的启动必然攒不满窗 */
  coverage: "判据表（analytics.score）只从 2026-01-27 起有数，所以更早启动的事件窗口一定不满 N 根。"
    + "不满窗的那些仍参与后端的中位数聚合，界面把整行标灰并写明它与满窗的行不是同一跨度。",
  /** 口径三（前半）：为什么少一族 */
  moodFamily: "market_mood 这一族故意不进这张表：同一天全市场是同一个数，它的名次没有横截面意义，"
    + "所以这里比八分量少一族，只有七族。",
  /** 口径三（后半）：vol_stage 的量纲 */
  volStage: "vol_stage 这一族的 value 是档位 1~5，不是百分比，别当百分数读。",
  /** 口径四：母体这一档决定「≥4 板率」能不能读 */
  cohort: "母体这一档换成「全部首板（不筛结果）」，这一屏才有要答的那问："
    + "启动之前的走向是不是预示后来走成 4 板。另外四档（4 板及以上、4 板及以上且前 4 日涨幅超四成半、"
    + "两者的并、四日涨幅超四成）都是先按结果筛过再回头看启动前的样子，"
    + "所以它们那一列「≥4 板率」是母体定义给的（恒等于筛条件本身，读它等于读自己），"
    + "这一格界面上不印成一个像是结论的百分比。要区分度就留在 anyfirst 这一档。",
};

/** 表一那一列的列名：方向的读法必须长在列名里，不是长在悬停里。 */
export const RANK_DELTA_HEADER = "名次变化（正＝上升）";

/** 表三（明细）里同一列的列名。 */
export const RANK_DELTA_DETAIL_HEADER = "名次变化（正＝上升）";

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

/** 整数量（事件数、名次、根数）：缺是 `—`，0 老老实实写 0。 */
export function intText(v: unknown): string {
  const x = num(v);
  return x === null ? DASH : x.toLocaleString("zh-CN");
}

/** 名次：中位数可以是小数（.5），整数就原样。 */
export function rankText(v: unknown): string {
  const x = num(v);
  return x === null ? DASH : (Number.isInteger(x) ? String(x) : x.toFixed(1));
}

/**
 * 名次变化：**正数＝上升**，所以正数带 `+`、负数带 `-`、0 就是 0（持平）。
 * 缺数给 `—` —— 0 与 — 是两件事：0 是"名次没动"，— 是"这一格没数"。
 */
export function rankDeltaText(v: unknown): string {
  const x = num(v);
  if (x === null) return DASH;
  const body = Number.isInteger(x) ? String(Math.abs(x)) : Math.abs(x).toFixed(1);
  if (x > 0) return `+${body}`;
  if (x < 0) return `-${body}`;
  return "0";
}

export type DeltaTone = "up" | "down" | "flat" | "none";

/** 方向判一次：`rank_delta > 0` 是名次上升（rank 越小越靠前）。 */
export function deltaTone(v: unknown): DeltaTone {
  const x = num(v);
  if (x === null) return "none";
  if (x > 0) return "up";
  if (x < 0) return "down";
  return "flat";
}

/** 方向的人话。这里不许出现"改善/恶化"这种把方向与好坏混起来的词。 */
export function deltaWord(v: unknown): string {
  switch (deltaTone(v)) {
    case "up": return "上升";
    case "down": return "下降";
    case "flat": return "持平";
    default: return DASH;
  }
}

/** 带符号的收益类读数（`r5_med`/`ex5_med` 这类百分数，接口已按百分数交）。 */
export function signedText(v: unknown, digits = 2): string {
  const x = num(v);
  return x === null ? DASH : `${x >= 0 ? "+" : ""}${x.toFixed(digits)}%`;
}

/** 比率类读数（≥4 板率、胜率）：不带正号。 */
export function rateText(v: unknown, digits = 1): string {
  const x = num(v);
  return x === null ? DASH : `${x.toFixed(digits)}%`;
}

/**
 * **小数比例** → 百分数（一位小数）。
 *
 * 为什么不能就用 `rateText`：`rateText` 的入参已经是百分数（8.4 → `8.4%`），
 * 而后端 `outcome_gain4` 交的是**小数比例** —— 出处是接口 definitions 的原句
 * 「sv-2 的 trigger_gain（小数比例，0.4641＝+46.41%）」。拿 `rateText` 直接拼 `%`
 * 会把 +46.26% 印成 `0.5%`（实测屏上就是 `0.5%`、`0.6%`，少了 ×100）。
 * 反过来把 `rateText` 改成乘一百更糟：别处传进来的已经是百分数，那几列会一起放大。
 *
 * 所以这里单独一份，只给「接口明说交的是小数比例」的那些列用：
 * `0.4626 → 46.3%`、`0.21 → 21.0%`、`-0.035 → -3.5%`（负数带符号）、缺 → `—`（0 比例是 `0.0%`）。
 */
export function ratioText(v: unknown, digits = 1): string {
  const x = num(v);
  return x === null ? DASH : `${(x * 100).toFixed(digits)}%`;
}

/**
 * 「≥4 板率」这一格的读法。
 *
 * 母体按结果筛过的那四档（`PRESCREENED_COHORTS`）里，这一列的数**由母体定义决定**：
 * 那批票本来就是「走到 4 板及以上／涨过四成」才进来的，百分比再好看也不是发现。
 * 所以那一格不印百分数，印这一句；要看出区分度就换 `anyfirst`（全部首板，不筛结果）。
 */
export const SEG4_PRESET_COPY = "这一档按结果筛过，这个数恒等于母体定义";

export function seg4CellText(v: unknown, cohort: unknown): string {
  return isPrescreenedCohort(cohort) ? SEG4_PRESET_COPY : rateText(v);
}

/**
 * 占比读数（`rank_up_share`、象限分布那类）。
 *
 * 接口这里可能交 0~1 的分数，也可能直接交百分数：占比不可能大于 100，
 * 所以阈值 1.5 —— 把 0.31 印成 `0.31%` 或把 31 印成 `3100.0%` 都是把读数说错一个量级。
 */
export function shareText(v: unknown, digits = 1): string {
  const x = num(v);
  if (x === null) return DASH;
  const pct = Math.abs(x) <= 1.5 ? x * 100 : x;
  return `${pct.toFixed(digits)}%`;
}

/** 分量的 value（原始分值/档位）：**不加百分号** —— vol_stage 那一族是档位 1~5。 */
export function valueText(v: unknown, digits = 2): string {
  const x = num(v);
  if (x === null) return DASH;
  return Number.isInteger(x) ? String(x) : x.toFixed(digits);
}

/** 带符号的值变化（value_delta）。 */
export function valueDeltaText(v: unknown, digits = 2): string {
  const x = num(v);
  if (x === null) return DASH;
  const body = Math.abs(x) < 1e-9 ? "0" : (Number.isInteger(x) ? String(Math.abs(x)) : Math.abs(x).toFixed(digits));
  if (x > 0) return `+${body}`;
  if (x < 0) return `-${body}`;
  return "0";
}

/**
 * 象限那几列的格子（翻转次数、末次翻转距尾场）。
 *
 * 关键的一条：null 与 0 必须长得不一样。这一族没有象限时后端交 null，
 * 印成 0 就成了"窗口里一次都没翻转过"——那是一条实质结论，而且与真意相反。
 */
export function quadCountText(v: unknown): string {
  const x = num(v);
  // null 一律 `—`：这一族没有象限（0 才是"翻转过零次"那条实质结论）
  if (x === null) return DASH;
  return Number.isInteger(x) ? String(x) : x.toFixed(1);
}

/** 这一行（表一的某个分量）到底带不带象限读数。四列全 null 就是这一族没有象限。 */
export function familyHasQuadrant(row: SqKeyRow): boolean {
  return num(row.quad_switches_med) !== null || num(row.quad_last_switch_gap_med) !== null
    || !!row.quad_first || !!row.quad_last;
}

/** 这一族没有象限时那句悬停/说明（**不许**被写成"翻转过 0 次"）。 */
export function noQuadrantNote(row: SqKeyRow): string {
  return familyHasQuadrant(row)
    ? ""
    : `${row.label || row.key} 这一族没有象限读数（象限只由 oper_score 那一族给），`
      + "格子里是 — 不是 0：— 说的是这一族本来就没有象限，0 才是翻转过零次。";
}

/**
 * 象限分布。后端这里交的是**条数**（SQL 里 `count(*) filter (where quad_first='Q1')`），
 * 不是占比 —— 所以占比要拿条数÷本行 `n` 现算，并把两个数同时印出来：
 * 「Q1 534 条 · 19.2% · …」。直接把条数丢给 `shareText` 会印成「Q1 534.0%」
 * （10-18 实测就是这么错出来的：占比一栏出现大于 100% 的鬼数）。
 * 传不进分母（缺 n 或 n=0）时退回按占比印，但仍然不乘一百之外的东西。
 */
export function sharesText(map: SqShares, total?: unknown): string {
  if (!map || typeof map !== "object") return DASH;
  const keys = Object.keys(map).sort();
  if (!keys.length) return DASH;
  // 全是 null 的分布（后端给了空壳）不等于 0% —— 一句 — 才诚实
  if (keys.every(k => num(map[k]) === null)) return DASH;
  const t = num(total);
  const vals = keys.map(k => num(map[k]));
  const looksLikeCount = t !== null && t > 0 && vals.every(v => v !== null && v > 1.5);
  const parts = keys.map((k, i) => {
    const x = vals[i];
    if (x === null) return `${k} ${DASH}`;
    if (looksLikeCount) return `${k} ${intText(x)} 条 · ${((100.0 * x) / (t as number)).toFixed(1)}%`;
    // 没有分母（缺 n 或 n=0）就不算占比：把条数丢进占比格式化正是 10-18 那个「534.0%」的来源。
    return t === null && x > 1.5 ? `${k} ${intText(x)} 条（分母未交）` : `${k} ${shareText(x)}`;
  });
  return parts.join(" · ");
}

/** 窗口攒满了吗。缺 `bars` 或缺 `window` 都算"不知道"，不默认成满窗。 */
export function windowShort(bars: unknown, windowDays: unknown): boolean | null {
  const used = num(bars);
  const need = num(windowDays);
  if (used === null || need === null) return null;
  return used < need;
}

/** 灰显那一行的悬停（照「启动前量能画像」`min_bars` 那条纪律的写法：不满窗仍进聚合，但不与满窗并排读强弱）。 */
export function shortTitle(bars: unknown, windowDays: unknown): string {
  const used = num(bars);
  const need = num(windowDays);
  if (used === null || need === null) return "窗口根数或本行实有根数没交出：满不满窗无从判断，不当作满窗，也不当作不满。";
  if (used >= need) return `满窗：窗口 ${need} 场里它有 ${used} 个有效交易日。`;
  return `不满窗：往前只有 ${used} 个有效交易日（要 ${need} 场）—— 多为次新、长期停牌，`
    + "或启动日早于判据表第一天。它的起止不是同一跨度的两点，仍进后端的中位数聚合，但不与满窗的行并排读强弱。";
}

/** 不满窗条数那句话：必须**点名条数**，并说这是覆盖不是缺数。 */
export function shortCoverageText(c: SqCoverage): string {
  const short = num(c.window_short_events);
  const full = num(c.window_full_events);
  if (short === null && full === null) return "后端没交满窗/不满窗的条数 —— 这一屏说不出窗口攒满了几条，界面不自己数。";
  const earliest = c.earliest_score_date ?? DASH;
  return `满窗 ${intText(full)} 条、不满窗 ${intText(short)} 条：判据表最早算到 ${earliest}，`
    + "比它更早启动的那批本来就攒不满 N 场 —— 这是覆盖不够，不是库里缺数；"
    + "不满窗的行仍进中位数聚合（界面上整行标灰）。";
}

/** 覆盖那一行：日期、事件数、票数 + 判据表最早那天。 */
export function coverageSentence(p: SqPayload): string {
  const c = p.coverage ?? {};
  const pr = p.params ?? {};
  const head = `窗口 ${c.from ?? DASH} → ${c.to ?? DASH}：母体共 ${intText(c.events)} 条启动事件 / ${intText(c.codes)} 只票，`
    + `判据表最早一场 ${c.earliest_score_date ?? DASH}。`;
  const echo = `本轮接口按母体「${pr.cohort_text ?? pr.cohort ?? DASH}」、近 ${intText(pr.months)} 个月、`
    + `启动日前 ${intText(pr.window)} 场、分量键 ${pr.key && pr.key !== "" ? pr.key : "全部（不分键，七族都算）"}、`
    + `上限 ${intText(pr.limit)} 条取数。`;
  return `${head}${echo}${shortCoverageText(c)}`;
}

/** 接口版本对不上时说的那句（拼错字段/换了版本不会报错，只会永远显示 —，所以要主动念）。 */
export function versionWarning(p: SqPayload, expect = "sqt-1"): string {
  const got = p.spec_version;
  if (!got) return "接口没交 spec_version：这一屏不知道自己对的是哪一版合同，字段若改名只会显示 —，不会报错。";
  return got === expect ? "" : `接口版本 ${got} 与本页合同 ${expect} 不一致 —— 字段含义可能已经漂，读数以接口 definitions 为准。`;
}

/** 控件参数 → 查询参数。五个都要真的发出去（`key` 为空串时 probe 会不带这一参数 = 全部分量）。 */
export function queryParams(input: {
  cohort: string; months: number; window: number; key: string; limit: number;
}): Record<string, string | number> {
  return {
    cohort: input.cohort,
    months: clampMonths(input.months),
    window: clampWindow(input.window),
    key: input.key || "",
    limit: clampLimit(input.limit),
  };
}

export function clampMonths(v: unknown): number {
  const x = num(v);
  if (x === null) return 6;
  return Math.min(12, Math.max(1, Math.round(x)));
}

/** 窗口只有 10/20/30 三档（后端会拒别的值）。 */
export function clampWindow(v: unknown): number {
  const x = num(v);
  return x === 10 || x === 20 || x === 30 ? x : 30;
}

/** 明细上限后端硬顶 600。 */
export function clampLimit(v: unknown): number {
  const x = num(v);
  if (x === null) return 400;
  return Math.min(600, Math.max(1, Math.round(x)));
}

/**
 * 后端 `caveats` 是**字符串数组**，而 `Caveats` 组件要 `{code,text}`。
 * 编号在这里补一次：`sqt1`、`sqt2`…（空串与非字符串丢掉，不占一个空号）。
 */
export function caveatItems(list: (string | null | undefined)[] | null | undefined):
  { code: string; text: string }[] {
  const out: { code: string; text: string }[] = [];
  for (const raw of list ?? []) {
    if (typeof raw !== "string" || !raw.trim()) continue;
    out.push({ code: `sqt${out.length + 1}`, text: raw.trim() });
  }
  return out;
}

export type SqColumn = {
  key: string; label: string;
  /** 象限分布那两列印的是 map，不是可比的标量 —— 不给排序（点了不换序的列头就是死键） */
  sortable: boolean;
  title: string;
};

/** 表一（每个分量的轨迹）的列：`key` 必须等于那一格真正读的字段名。 */
export function keyColumns(): SqColumn[] {
  return [
    { key: "label", label: "分量", sortable: true, title: "capability_key 对应的人话标签，接口交的" },
    { key: "n", label: "事件数", sortable: true, title: "这一族里进了聚合的母体事件条数" },
    { key: "rank_first_med", label: "名次首场（中位）", sortable: true, title: "窗口第一场的全市场名次，中位数；rank 越小越靠前" },
    { key: "rank_last_med", label: "名次 T−1（中位）", sortable: true, title: "窗口最后一场（启动日前一天）的全市场名次，中位数" },
    { key: "rank_delta_med", label: RANK_DELTA_HEADER, sortable: true, title: "rank_first − rank_last 的中位：正数＝名次上升（往前列走）" },
    { key: "rank_up_share", label: "上升占比", sortable: true, title: "名次上升（rank_delta>0）的事件占比" },
    { key: "rank_down_share", label: "下降占比", sortable: true, title: "名次下降的事件占比；与上升占比相加可以不满一，那是持平的那批" },
    { key: "pct_first_med", label: "分位首场（中位）", sortable: true, title: "窗口首场的百分位中位，越大越靠前；单位按接口 definitions 原样，页面不加百分号" },
    { key: "pct_last_med", label: "分位 T−1（中位）", sortable: true, title: "窗口最后一场的百分位中位" },
    { key: "value_first_med", label: "值首场（中位）", sortable: true, title: "分量原值中位：不加百分号 —— vol_stage 那一族是档位 1~5" },
    { key: "value_last_med", label: "值 T−1（中位）", sortable: true, title: "分量原值在窗口最后一场的中位" },
    { key: "quad_switches_med", label: "象限翻转次数（中位）", sortable: true, title: "窗口内象限换了几个档位的中位。只有 oper_score 那一族有；别的族是 —，不是 0" },
    { key: "quad_last_switch_gap_med", label: "末次翻转距尾场（中位）", sortable: true, title: "最后一次象限翻转离窗口最后一场隔几场：越小＝越是刚刚才换的" },
    { key: "quad_first", label: "首场象限分布", sortable: false, title: "窗口最早那一场上母体落在 Q1~Q4 的条数与占比（后端交条数，占比＝条数÷本行事件数）；列印不是标量，所以这一列不给排序" },
    { key: "quad_last", label: "T−1 场象限分布", sortable: false, title: "窗口最后一场（启动日前一天）上母体落在 Q1~Q4 的条数与占比。窗口不含启动日当天，所以这一格不是启动日那天的象限" },
  ];
}

/** 表一某一格的文本：读的就是 `keyColumns()` 里那个 key（同一个字段名，两处不漂移）。 */
export function keyCellText(col: SqColumn, row: SqKeyRow): string {
  switch (col.key) {
    case "label": return row.label || row.key || DASH;
    case "n": return intText(row.n);
    case "rank_first_med": case "rank_last_med": return rankText(row[col.key]);
    case "rank_delta_med": return rankDeltaText(row.rank_delta_med);
    case "rank_up_share": case "rank_down_share": return shareText(row[col.key]);
    case "pct_first_med": case "pct_last_med": return valueText(row[col.key], 1);
    case "value_first_med": case "value_last_med": return valueText(row[col.key]);
    case "quad_switches_med": case "quad_last_switch_gap_med":
      return quadCountText(row[col.key]);
    case "quad_first": return sharesText(row.quad_first, row.n);
    case "quad_last": return sharesText(row.quad_last, row.n);
    default: return DASH;
  }
}

/** 表二（答案）的列。 */
export function answerColumns(): SqColumn[] {
  return [
    { key: "label", label: "档", sortable: true, title: "按窗口内名次变化分成的三档 + 全部母体那一行" },
    { key: "n", label: "事件数", sortable: true, title: "这一档进了聚合的事件条数" },
    { key: "rank_delta_med", label: RANK_DELTA_HEADER, sortable: true, title: "这一档的名次变化中位：正数＝上升" },
    { key: "seg4_rate", label: "≥4 板率", sortable: true, title: "这一档里后来走到 4 板及以上的比例。"
      + "母体换成按结果筛过的那四档时这一格不印百分数 —— 那批票本来就是走到 4 板才进来的，"
      + "这个数是母体定义不是发现；要读出区分度就选 anyfirst（全部首板，不筛结果）" },
    { key: "seg_len_med", label: "走到几板（中位）", sortable: true, title: "段长的中位数" },
    { key: "r5_med", label: "进场后 5 场（绝对）", sortable: true, title: "进场后 5 场的绝对收益中位" },
    { key: "ex5_med", label: "（超额）", sortable: true, title: "同一批的超额中位：北交所没有对应分类指数，缺的是 —，不是 0%" },
    { key: "win5", label: "胜率", sortable: true, title: "进场后 5 场为正的比例" },
    { key: "quad_switches_med", label: "象限翻转次数（中位）", sortable: true, title: "这一档窗口内象限翻转次数的中位" },
  ];
}

/** 表二某一格的文本（`base` 那一行与三个档同一份读法）。 */
export function answerCellText(col: SqColumn, row: SqAggRow, cohort?: unknown): string {
  switch (col.key) {
    case "label": return row.label || DASH;
    case "n": return intText(row.n);
    case "rank_delta_med": return rankDeltaText(row.rank_delta_med);
    // ≥4 板率那一格要先问一句母体：按结果筛过的那四档里它恒等于定义，印成百分比就像结论
    case "seg4_rate": return seg4CellText(row.seg4_rate, cohort);
    case "win5": return rateText(row[col.key]);
    case "seg_len_med": return rankText(row[col.key]);
    case "r5_med": case "ex5_med": return signedText(row[col.key]);
    case "quad_switches_med": return quadCountText(row.quad_switches_med);
    default: return DASH;
  }
}

/** 三档 + 并排的 `base` 那一行（基线钉在第一行，不参与排序，免得读者以为它在换档）。 */
export function answerRows(buckets: SqAggRow[] | null | undefined, base: SqAggRow | null | undefined): SqAggRow[] {
  const list = buckets ?? [];
  return base ? [base, ...list] : [...list];
}

/** 分档区间：切点按后端 `col <= cut` 的写法是**左开右闭**。 */
export function rangeText(row: Pick<SqAggRow, "range" | "label">): string {
  const r = row.range ?? null;
  if (!r || (r[0] === null && r[1] === null)) return row.label ? "全部" : DASH;
  const lo = num(r[0]);
  const hi = num(r[1]);
  const trim = (v: number) => (Number.isInteger(v) ? `${v}` : v.toFixed(1));
  if (lo === null && hi !== null) return `≤${trim(hi)}`;
  if (lo !== null && hi === null) return `>${trim(lo)}`;
  if (lo !== null && hi !== null) return `${trim(lo)}～${trim(hi)}（不含 ${trim(lo)}）`;
  return DASH;
}

/**
 * 超额那一列的"少了几条"的账。
 *
 * 合同里没有「被剔掉的条数」这一个字段，所以这里只念两个接口真交出来的数：
 * 聚合行里有几档没交 `ex5_med`，以及本轮明细里有多少行是北交所（那批没有对应分类指数）。
 * 说不准的绝不编成"已剔除 N 条"。
 */
export function ex5GapText(agg: SqAggRow[] | null | undefined, rows: SqDetailRow[] | null | undefined): string {
  const list = agg ?? [];
  const missingAgg = list.filter(r => num(r.ex5_med) === null).length;
  const bj = (rows ?? []).filter(r => String(r.board ?? "").includes("北交")).length;
  if (!list.length) return "";
  if (!missingAgg && !bj) return "本轮超额（ex5_med）各档都有数：没有因缺分类指数而空着的格子。";
  return `超额这一列本轮有 ${missingAgg} 档读不出中位（格子上是 —，不是 0%）：`
    + `已交出的 ${intText((rows ?? []).length)} 行明细里有 ${bj} 行是北交所 —— 那批票没有对应的分类指数，`
    + "超额算不出来的那些不进中位数的分母。这一句只数接口真交出来的字段：合同没有被剔掉的母体条数这一项，界面不编一个数。";
}

/** 表三（明细）按「一只票的一次启动」收拢：同一事件的七族并在一起，起止名次与象限才看得见。 */
export function groupRows(rows: SqDetailRow[] | null | undefined, windowDays: unknown): SqEventGroup[] {
  const out: SqEventGroup[] = [];
  const index = new Map<string, number>();
  for (const r of rows ?? []) {
    const id = `${String(r.code ?? "")}|${String(r.launch_date ?? "")}`;
    let at = index.get(id);
    if (at === undefined) {
      at = out.length;
      index.set(id, at);
      out.push({
        code: r.code ?? null, name: r.name ?? null, board: r.board ?? null,
        launch_date: r.launch_date ?? null, cohort: r.cohort ?? null,
        key_count: 0, bars: num(r.bars), window_short: windowShort(r.bars, windowDays),
        rank_delta_best: null, rank_delta_worst: null,
        quad_switches: num(r.quad_switches),
        outcome_len: num(r.outcome_len), outcome_gain4: num(r.outcome_gain4),
        rows: [],
      });
    }
    const g = out[at];
    g.rows.push(r);
    g.key_count = g.rows.length;
    const d = num(r.rank_delta);
    if (d !== null) {
      g.rank_delta_best = g.rank_delta_best === null ? d : Math.max(g.rank_delta_best, d);
      g.rank_delta_worst = g.rank_delta_worst === null ? d : Math.min(g.rank_delta_worst, d);
    }
    if (g.bars === null) g.bars = num(r.bars);
    if (g.quad_switches === null) g.quad_switches = num(r.quad_switches);
    if (g.outcome_len === null) g.outcome_len = num(r.outcome_len);
    if (g.outcome_gain4 === null) g.outcome_gain4 = num(r.outcome_gain4);
    // 只要有一行不满窗就算不满（同一事件的根数本该一致，不一致时按保守的读）
    const s = windowShort(r.bars, windowDays);
    if (s === true) g.window_short = true;
  }
  return out;
}

/** 明细那一张表（展开前的行）的列。 */
export function groupColumns(): SqColumn[] {
  return [
    { key: "code", label: "代码", sortable: true, title: "双击这一行开个股深度复盘" },
    { key: "name", label: "名称", sortable: true, title: "" },
    { key: "board", label: "板块", sortable: true, title: "" },
    { key: "launch_date", label: "启动日", sortable: true, title: "窗口取的是这一天之前的 N 场" },
    { key: "key_count", label: "分量数", sortable: true, title: "这一行接口交了几族（少于七族是那一族当天没数，不是界面漏画）" },
    { key: "bars", label: "用了几根", sortable: true, title: "窗口内它真有的有效交易日根数" },
    { key: "rank_delta_best", label: "名次变化最好（正＝上升）", sortable: true, title: "本页交出的这些分量里名次变化最大的那个数（极值只在本页行里取，不是后端指标）" },
    { key: "rank_delta_worst", label: "名次变化最差（正＝上升）", sortable: true, title: "同上，取最小的那个数；两个都为正才是七族一起往前列走" },
    { key: "quad_switches", label: "象限翻转次数", sortable: true, title: "只有 oper_score 那一族有象限：这一格是 — 就是这一行没有象限读数，不是翻转过 0 次" },
    { key: "outcome_len", label: "走到几板", sortable: true, title: "结果列：启动之后走到的段长" },
    { key: "outcome_gain4", label: "前 4 日涨幅", sortable: true, title: "结果列：启动日起 4 个交易日的涨幅。"
      + "接口交的是小数比例（0.4626＝+46.26%，见 definitions 原句），这一格走 ratioText 乘回一百，"
      + "不是把 0.5 当作 0.5%" },
  ];
}

/** 明细行（未展开）某一格的文本。 */
export function groupCellText(col: SqColumn, g: SqEventGroup): string {
  switch (col.key) {
    case "code": case "name": case "board": case "launch_date":
      return String(g[col.key] ?? "") || DASH;
    case "key_count": return intText(g.key_count);
    case "bars": return intText(g.bars);
    case "rank_delta_best": case "rank_delta_worst": return rankDeltaText(g[col.key]);
    case "quad_switches": return quadCountText(g.quad_switches);
    case "outcome_len": return rankText(g.outcome_len);
    // 这一列后端交的是小数比例（0.4626＝+46.26%）⇒ 只能走 ratioText；
    // 走 rateText 会把 +46.26% 印成 0.5%（屏上实测就是这么错的）。不许改回来。
    case "outcome_gain4": return ratioText(g.outcome_gain4);
    default: return DASH;
  }
}

/** 展开以后那一叠分量行的列（键名都住在 `SqDetailRow` 上）。 */
export function detailColumns(): SqColumn[] {
  return [
    { key: "capability_label", label: "分量", sortable: false, title: "capability_label（没有就退回 capability_key）" },
    { key: "bars", label: "用了几根", sortable: false, title: "这一族窗口内的根数" },
    { key: "value_first", label: "值首场", sortable: false, title: "分量原值：窗口第一场" },
    { key: "value_last", label: "值 T−1", sortable: false, title: "分量原值：窗口最后一场（启动日前一天）" },
    { key: "value_delta", label: "值变化", sortable: false, title: "value_last − value_first（这一族的值涨了就为正；vol_stage 是档位，不是百分数）" },
    { key: "rank_first", label: "名次首场", sortable: false, title: "全市场名次，越小越靠前" },
    { key: "rank_last", label: "名次 T−1", sortable: false, title: "窗口最后一场的名次" },
    { key: "rank_delta", label: RANK_DELTA_DETAIL_HEADER, sortable: false, title: "rank_first − rank_last：正数＝名次上升" },
    { key: "rank_best", label: "窗内最好", sortable: false, title: "窗口里最好的一场排到第几" },
    { key: "rank_worst", label: "窗内最差", sortable: false, title: "窗口里最差的一场排到第几" },
    { key: "quad_first", label: "首场象限", sortable: false, title: "窗口那一场它在哪个象限（只有 oper_score 那一族有）" },
    { key: "quad_last", label: "T−1 场象限", sortable: false, title: "窗口最后一场的象限：不是启动日当天（当天不进窗口）" },
    { key: "quad_switches", label: "翻转次数", sortable: false, title: "— 是这一族没有象限，0 才是窗口里一次都没翻" },
    { key: "quad_last_switch_gap", label: "末次翻转距尾场", sortable: false, title: "最后一次换象限离窗口最后一场隔几场" },
  ];
}

/** 展开行的格子文本：与 `detailColumns()` 的 key 同一份。 */
export function detailCellText(col: SqColumn, r: SqDetailRow): string {
  switch (col.key) {
    case "capability_label": return String(r.capability_label ?? r.capability_key ?? "") || DASH;
    case "bars": return intText(r.bars);
    case "value_first": case "value_last": return valueText(r[col.key]);
    case "value_delta": return valueDeltaText(r.value_delta);
    case "rank_first": case "rank_last": case "rank_best": case "rank_worst": return rankText(r[col.key]);
    case "rank_delta": return rankDeltaText(r.rank_delta);
    case "quad_first": case "quad_last": return String(r[col.key] ?? "") || DASH;
    case "quad_switches": case "quad_last_switch_gap":
      return quadCountText(r[col.key]);
    default: return DASH;
  }
}

/**
 * 一句话方向：七个分量里谁在往前列走、谁在往后列走。
 *
 * 只从接口交的 `rank_delta_med` 里挑（正＝上升），界面不重算中位数；
 * 一族都没交就明说"说不出"，不编一句好看的话。
 */
export function keyHeadline(keys: SqKeyRow[] | null | undefined): string {
  const rows = (keys ?? []).filter(k => num(k.rank_delta_med) !== null);
  if (!rows.length) return "接口没交任何一族的名次变化中位 —— 这一屏说不出方向，界面不凑一个。";
  const best = rows.reduce((a, b) => ((num(b.rank_delta_med) ?? 0) > (num(a.rank_delta_med) ?? 0) ? b : a));
  const worst = rows.reduce((a, b) => ((num(b.rank_delta_med) ?? 0) < (num(a.rank_delta_med) ?? 0) ? b : a));
  const word = (r: SqKeyRow) => `${r.label || r.key}（${rankDeltaText(r.rank_delta_med)}，${deltaWord(r.rank_delta_med)}）`;
  const upCount = rows.filter(r => deltaTone(r.rank_delta_med) === "up").length;
  return `${rows.length} 族里有 ${upCount} 族的中位名次在上升。上升最明显的是 ${word(best)}，`
    + `往后列走得最多的是 ${word(worst)}。这两句说的都是名次（位置）的变化，正数才是变前 —— 段长与收益的结果在下一张表。`;
}

/** 控件选项：接口交了就吃接口的，没交就退回这份兜底（兜底不含任何编造的键名）。 */
export function optionsFor(list: SqOption[] | null | undefined, fallback: SqOption[]): SqOption[] {
  const got = (list ?? []).filter(o => o && (typeof o.value === "string" || typeof o.value === "number") && o.label);
  return got.length ? got : fallback;
}

/**
 * 分量键那一档的选项：接口若没交「全部」这一档，界面补一条在最前 ——
 * 不补的话下拉的当前值（空串）在选项里找不到，读者看到的是第一族、发的却是全部。
 */
export function keyOptionsFor(list: SqOption[] | null | undefined): SqOption[] {
  const opts = optionsFor(list, KEYS_FALLBACK);
  return opts.some(o => o.value === "") ? opts : [{ value: "", label: "全部七个分量" }, ...opts];
}
