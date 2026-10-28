/**
 * 「启动前 30 场爆量与突然拉高：落在量增的天还是量减的天」那一块的判读层：纯函数、零 import，
 * 可以 `node --test` 直接跑（与 `firstBoardShape.ts`／`quadrantTrack.ts` 同一份纪律）。
 *
 * 这一屏要答的那问很具体：**倍量（爆量）的那些天到底是收涨还是收跌，而那种一天之内突然拉高的
 * 日子，是落在倍量日上、还是落在缩量日上**。所以界面把三张表按这个顺序排：
 *  ① 联合对照（倍量日 × 收涨/收跌、准倍量日、缩量日各一行）—— 不是两个独立天数的并列，
 *     天数与收涨收跌必须在**同一行**里读，否则读者会拿"倍量天数多"当成"倍量天在涨"；
 *  ② 突然拉高落在哪一天（突拉当天是倍量／准倍量／缩量，以及其中封住涨停的比例）；
 *  ③ 分档 × 结果（按突拉天数分三档，并排全部母体那一行，看后来走到几板、进场划不划算）。
 *
 * 三处最容易把读数说反的地方只在这里判一次，组件只排版：
 *  · **这是日线代理**：一天一个数（当日量对着这只票自己上一根日线的量算倍数＝vr-1、当日后复权收益算突拉），
 *    分时图上那种"几分钟里连续成交打上来"的爆量与急拉**这一屏没有做**（分钟线不在这次的取数里）；
 *  · `jump_days` 等于 0 与 null 是两件事：0 是窗口里真没有突拉（一条实质结论），
 *    null 是窗口没凑满或那天没数据（没有这一回事）。格子里 0 与 `—` 必须长得不一样，
 *    悬停两句也不同（`jumpDaysZeroCopy` / `jumpDaysNullCopy`）；
 *  · 超额（`ex5_med`/`outcome_ex5`）在北交所没有对应分类指数 ⇒ 后端交 null。
 *    那些行从分母里剔掉，并且要**点名剔了几条**，不拿 0 当"没赚"（`ex5AccountText`）。
 *
 * 铁律与后端 `duanxian/*spikedays*` 同一套：所有解释性文字里的数都来自接口
 * （`params`/`coverage`/`joint`/`jumps`/`buckets`/`base`），界面不自造口径、不重算中位数与占比。
 *
 * 合同版本：`spec_version = 'ssd-1'`（对不上时 `versionWarning` 会在屏幕上念出来）。
 */

export type SsdOption = { value: string | number; label: string };

export type SsdParams = {
  cohort?: string | null; cohort_text?: string | null; months?: number | null;
  window?: number | null; jump_pct?: number | null; limit?: number | null;
};

export type SsdCoverage = {
  from?: string | null; to?: string | null; events?: number | null; codes?: number | null;
  vr_earliest?: string | null; window_full_events?: number | null; window_short_events?: number | null;
};

/** 第一张表的原料：倍量／准倍量／缩量这三类日子各自收涨收跌的占比（接口 `joint`）。 */
export type SsdJoint = {
  dbl_total_days?: number | null;
  dbl_up_share?: number | null; dbl_down_share?: number | null;
  near_up_share?: number | null; near_down_share?: number | null;
  calm_up_share?: number | null; calm_down_share?: number | null;
  [k: string]: unknown;
};

/** 第二张表的原料：突然拉高那些天落在哪一类量上（接口 `jumps`）。 */
export type SsdJumps = {
  jump_days_med?: number | null;
  jump_on_dbl_share?: number | null; jump_on_near_share?: number | null;
  jump_on_calm_share?: number | null; jump_limit_share?: number | null;
  first_jump_gap_med?: number | null;
  jump_days_zero_events?: number | null; jump_days_null_events?: number | null;
  [k: string]: unknown;
};

/** 第三张表的行：三个 `buckets` 档 + 那一行 `base`，字段名同一套。 */
export type SsdAggRow = {
  label: string; range?: (number | null)[] | null; n?: number | null;
  dbl_up_days_med?: number | null; dbl_down_days_med?: number | null;
  jump_days_med?: number | null; jump_on_dbl_share?: number | null;
  seg4_rate?: number | null; r5_med?: number | null; ex5_med?: number | null; win5?: number | null;
  [k: string]: unknown;
};

/** 明细行：一只票的一次启动，窗口内逐日那些天数的账（接口 `rows`，一行一个事件）。 */
export type SsdRow = {
  code?: string | null; name?: string | null; board?: string | null;
  launch_date?: string | null; cohort?: string | null; bars?: number | null;
  up_days?: number | null; down_days?: number | null; flat_days?: number | null;
  still_days?: number | null;
  dbl_days?: number | null; near_dbl_days?: number | null; calm_days?: number | null;
  dbl_up_days?: number | null; dbl_down_days?: number | null;
  near_up_days?: number | null; near_down_days?: number | null;
  calm_up_days?: number | null; calm_down_days?: number | null;
  jump_days?: number | null; jump_dbl_days?: number | null;
  jump_near_days?: number | null; jump_calm_days?: number | null;
  jump_limit_days?: number | null; limit_days?: number | null;
  first_dbl_gap?: number | null; first_jump_gap?: number | null;
  outcome_len?: number | null; outcome_r5?: number | null; outcome_ex5?: number | null;
  [k: string]: unknown;
};

export type SsdPayload = {
  available?: boolean | null; spec_version?: string | null; reason?: string | null;
  params?: SsdParams | null; coverage?: SsdCoverage | null;
  joint?: SsdJoint | null; jumps?: SsdJumps | null;
  buckets?: SsdAggRow[] | null; base?: SsdAggRow | null;
  rows?: SsdRow[] | null;
  caveats?: string[] | null; caliber?: string | null;
  definitions?: Record<string, string> | null;
  options?: {
    cohorts?: SsdOption[] | null; months?: SsdOption[] | null; windows?: SsdOption[] | null;
  } | null;
};

export const DASH = "—";

/**
 * 母体兜底三档，**`anyfirst`（全部首板，不筛结果）排第一并且是默认选中**：
 * `board4`／`jump40` 都是先按结果筛过的母体，那一档里「≥4 板率」这个数只是把筛条件重复一遍
 * （见 `seg4CellText`）。真实名单以接口 `options.cohorts` 为准，这里只兜接口没交的那一档情况。
 */
export const COHORTS_FALLBACK: SsdOption[] = [
  { value: "anyfirst", label: "全部首板（不筛结果）" },
  { value: "board4", label: "4 板及以上" },
  { value: "jump40", label: "4 日涨幅超四成（双创／北证）" },
];

/** 「母体已按结果筛过」的那几档：它们的 `seg4_rate` 不是发现，是定义。 */
export const PRESCREENED_COHORTS: string[] = ["board4", "board4_45", "board4_or_gain45", "jump40"];

export const WINDOWS_FALLBACK: SsdOption[] = [
  { value: 10, label: "启动日前 10 场" },
  { value: 20, label: "启动日前 20 场" },
  { value: 30, label: "启动日前 30 场" },
];

export const MONTHS_FALLBACK: SsdOption[] =
  Array.from({ length: 12 }, (_, i) => ({ value: i + 1, label: `近 ${i + 1} 个月` }));

/** 突拉阈值的兜底档位（%）：接口若交了自己的清单就以接口为准。 */
export const JUMP_PCTS = [5, 7, 9];

/** 明细上限（后端硬顶 600）。 */
export const LIMITS = [200, 400, 600];

/**
 * 三句必须原样上屏的口径（用户点名「措辞自己组织但事实不许变」）。
 * 摆在说明框外面：这三句说的是"这一张表能不能这么读"，藏在折叠框里等于没写。
 */
export const COPY = {
  /** 口径一：日线代理，不是分时爆量/急拉 */
  proxy: "这一屏的判据是日线代理：一天只出一个数 —— 当日成交量对着这只票自己的上一根日线量算倍量"
    + "（vr-1 那把尺：≥2.0 倍量、1.8~2.0 准倍量、≤0.5 缩量），突拉是当日后复权收益过那条线（不读 pct_change）。"
    + "收盘收在涨的那一侧就算收涨，平盘归跌那一头。它答的是"
    + "「量放出来那天是不是也在涨、突然拉高那种日子摊在哪一类量上」；"
    + "它不是分时图上那种几分钟内连续成交打上去的爆量与急拉 —— 分钟线这一轮没做，"
    + "要看那种形状去「异动筛查」那一屏，它读的是盘中逐笔与分钟线，两套判据不并表。",
  /** 口径二：jump_days 的 0 与 null 是两件事 */
  zeroOrNull: "突拉天数（jump_days）这一列：0 与 — 是两件事，界面上长得也不一样。"
    + "0 是窗口凑满了、里面确实一天突拉都没有（这是一条实质结论）；"
    + "— 是这一条压根没凑满窗口，或那几天没有行情（这是没有这一回事，不是零次）。",
  /** 口径三：超额在北交所算不出来，null 的行从分母里剔掉 */
  ex5: "进场后 5 场的超额（ex5_med／outcome_ex5）在北交所没有对应的分类指数，后端交的是 null。"
    + "那些行从超额的中位数分母里剔掉，下面会点名本轮剔了几条；"
    + "空着的格子是 —，不是 0%——把缺数摆成 0% 就是把它读成「没赚也没亏」。",
};

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

/** 整数量（天数、条数）：缺是 `—`，0 老老实实写 0（0 是一条读数，不是没数）。 */
export function intText(v: unknown): string {
  const x = num(v);
  return x === null ? DASH : x.toLocaleString("zh-CN");
}

/** 天数中位：可以是 .5（后端的中位），整数就原样。 */
export function daysText(v: unknown): string {
  const x = num(v);
  if (x === null) return DASH;
  return Number.isInteger(x) ? String(x) : x.toFixed(1);
}

/** 比率类读数（≥4 板率、胜率）：入参**已经是百分数**，这里只加百分号，不乘一百。 */
export function rateText(v: unknown, digits = 1): string {
  const x = num(v);
  return x === null ? DASH : `${x.toFixed(digits)}%`;
}

/**
 * 占比归一到百分数口径（接口可能交 0~1 也可能交 0~100）。
 *
 * 全文件只有这一处乘一百：`shareText` 与那两句方向用的都是它。
 * 再多一处就等于长出第二份量纲（象限轨迹那屏的 `outcome_gain4` 就是这么把 +46.26% 印成 0.5% 的）。
 */
function pctOf(x: number): number {
  return Math.abs(x) <= 1.5 ? x * 100 : x;
}

/**
 * 占比读数（`dbl_up_share` 那一族）。
 *
 * 接口这里可能交 0~1 的分数，也可能直接交百分数：占比不可能大于 100，所以阈值 1.5 ——
 * 把 0.62 印成 `0.62%` 或把 62 印成 `6200.0%` 都是把读数说错一个量级。
 */
export function shareText(v: unknown, digits = 1): string {
  const x = num(v);
  if (x === null) return DASH;
  return `${pctOf(x).toFixed(digits)}%`;
}

/** 带符号的收益类读数（`r5_med`/`ex5_med`/`outcome_r5`，接口已按百分数交）。 */
export function signedText(v: unknown, digits = 2): string {
  const x = num(v);
  return x === null ? DASH : `${x >= 0 ? "+" : ""}${x.toFixed(digits)}%`;
}

/** 隔了几场（`first_dbl_gap`/`first_jump_gap`）：不带百分号，缺是 —。 */
export function gapText(v: unknown): string {
  const x = num(v);
  if (x === null) return DASH;
  return Number.isInteger(x) ? `${x} 场` : `${x.toFixed(1)} 场`;
}

/**
 * 突拉天数那一格的两种说法：0 与 null 必须分开（`COPY.zeroOrNull` 那句的落点）。
 * 界面拿 `jumpDaysMeaning` 做悬停，读者不用猜 0 到底是"没有"还是"没数"。
 */
export const JUMP_DAYS_ZERO_COPY = "0 天：窗口凑满了，里面确实一天突然拉高都没有";
export const JUMP_DAYS_NULL_COPY = "—：窗口没凑满／那几天没有行情，这一条算不出天数（不是 0 次）";

export function jumpDaysText(v: unknown): string {
  const x = num(v);
  return x === null ? DASH : `${x}`;
}

export function jumpDaysMeaning(v: unknown): string {
  const x = num(v);
  if (x === null) return JUMP_DAYS_NULL_COPY;
  if (x === 0) return JUMP_DAYS_ZERO_COPY;
  return `窗口里真有 ${x} 天突然拉高（当日涨幅过阈值的那些天）`;
}

/** 这一档母体是不是按结果筛过的（没交／空＝不知道，不下那句同义反复的结论）。 */
export function isPrescreenedCohort(cohort: unknown): boolean {
  if (cohort === null || cohort === undefined) return false;
  const s = String(cohort);
  return s !== "" && PRESCREENED_COHORTS.indexOf(s) >= 0;
}

/** 「≥4 板率」：母体已按结果筛过时，这一格印这句话本身，不印一个像结论的百分比。 */
export const SEG4_PRESET_COPY = "这一档按结果筛过，这个数恒等于母体定义";

export function seg4CellText(v: unknown, cohort: unknown): string {
  return isPrescreenedCohort(cohort) ? SEG4_PRESET_COPY : rateText(v);
}

/** 窗口攒满了吗：缺 `bars` 或缺 `window` 都算"不知道"，不默认成满窗。 */
export function windowShort(bars: unknown, windowDays: unknown): boolean | null {
  const used = num(bars);
  const need = num(windowDays);
  if (used === null || need === null) return null;
  return used < need;
}

/** 不满窗那一行的悬停（同「启动前象限轨迹」那屏的纪律：不满窗仍进聚合，但不与满窗并排读强弱）。 */
export function shortTitle(bars: unknown, windowDays: unknown): string {
  const used = num(bars);
  const need = num(windowDays);
  if (used === null || need === null) return "窗口根数或本行实有根数没交出：满不满窗无从判断，不当作满窗，也不当作不满。";
  if (used >= need) return `满窗：窗口 ${need} 场里它有 ${used} 个有效交易日。`;
  return `不满窗：往前只有 ${used} 个有效交易日（要 ${need} 场）—— 多为次新、长期停牌，`
    + "或启动日早于日线判据第一天。它的天数与满窗的行不是同一跨度，仍进后端的中位数聚合，但不与满窗并排读强弱。";
}

/** 覆盖那句：日期区间、事件数、票数、日线判据最早那天 + 满窗/不满窗的条数（点名，不含糊）。 */
export function coverageSentence(p: SsdPayload): string {
  const c = p.coverage ?? {};
  const pr = p.params ?? {};
  const head = `窗口 ${c.from ?? DASH} → ${c.to ?? DASH}：母体共 ${intText(c.events)} 条启动事件 / `
    + `${intText(c.codes)} 只票，日线判据最早一场 ${c.vr_earliest ?? DASH}。`;
  const echo = `本轮接口按母体「${pr.cohort_text ?? pr.cohort ?? DASH}」、近 ${intText(pr.months)} 个月、`
    + `启动日前 ${intText(pr.window)} 场、突拉阈值 ${ratioToPctText(pr.jump_pct)}（当日涨幅过这条线就算一次突拉）、`
    + `明细上限 ${intText(pr.limit)} 条取数。`;
  const full = num(c.window_full_events);
  const short = num(c.window_short_events);
  const span = (full === null && short === null)
    ? "后端没交满窗/不满窗的条数 —— 这一屏说不出窗口攒满了几条，界面不自己数。"
    : `满窗 ${intText(full)} 条、不满窗 ${intText(short)} 条：比日线判据最早那场更早启动的那批本来就攒不满 N 场 ——`
      + "这是覆盖不够，不是库里缺数；不满窗的那些仍进中位数聚合（界面上整行标灰）。";
  return `${head}${echo}${span}`;
}

/** 接口版本对不上就当场念出来：字段改名不会报错，只会永远显示 —。 */
export function versionWarning(p: SsdPayload, expect = "ssd-1"): string {
  const got = p.spec_version;
  if (!got) return "接口没交 spec_version：这一屏不知道自己对的是哪一版合同，字段若改名只会显示 —，不会报错。";
  return got === expect ? "" : `接口版本 ${got} 与本页合同 ${expect} 不一致 —— 字段含义可能已经漂，读数以接口 definitions 为准。`;
}

/** 控件参数 → 查询参数：六个键（五个控件 + 固定的 jump_pct）都要真的进 URL。 */
export function queryParams(input: {
  cohort: string; months: number; window: number; jumpPct: number; limit: number;
}): Record<string, string | number> {
  return {
    cohort: input.cohort,
    months: clampMonths(input.months),
    window: clampWindow(input.window),
    // 控件上那格给人看的是「当日涨幅超 7%」，接口那把尺收的是**比例**（0.03~0.15）。
    // 直接把 7 发出去会被 FastAPI 判 422，屏上就变成「取数失败」—— 10-18 首发实测就是这么挂的。
    jump_pct: jumpPctParam(input.jumpPct),
    limit: clampLimit(input.limit),
  };
}

export function clampMonths(v: unknown): number {
  const x = num(v);
  if (x === null) return 6;
  return Math.min(12, Math.max(1, Math.round(x)));
}

export function clampWindow(v: unknown): number {
  const x = num(v);
  return x === 10 || x === 20 || x === 30 ? x : 30;
}

/** 突拉阈值只认 3~15（%）：那是接口 `jump_pct` 的合法区间（0.03~0.15），别的值接口直接拒。 */
export function clampJumpPct(v: unknown): number {
  const x = num(v);
  if (x === null) return 7;
  return Math.min(15, Math.max(3, Math.round(x)));
}

/** 回显用：接口给的是比例（0.07），屏上要念「7.0%」。少乘/多乘一百都是这一族的老坑。 */
export function ratioToPctText(v: unknown, digits = 1): string {
  const x = num(v);
  return x === null ? DASH : `${pctOf(x).toFixed(digits)}%`;
}

/**
 * 发给接口的值：比例。界面这一族只认 3~15（%）—— 那两个数是 `Query(ge=0.03, le=0.15)` 的边界，
 * 夹在小数点前一位就等于让接口回一个 422，界面上的"重算一次"点了必红。
 */
export function jumpPctParam(v: unknown): number {
  return clampJumpPct(v) / 100.0;
}

export function clampLimit(v: unknown): number {
  const x = num(v);
  if (x === null) return 400;
  return Math.min(600, Math.max(1, Math.round(x)));
}

/**
 * 后端 `caveats` 是字符串数组，而 `Caveats` 组件要 `{code,text}`。
 * 编号在这里补一次：`ssd1`、`ssd2`…（空串与非字符串丢掉，不占一个空号）。
 */
export function caveatItems(list: (string | null | undefined)[] | null | undefined):
  { code: string; text: string }[] {
  const out: { code: string; text: string }[] = [];
  for (const raw of list ?? []) {
    if (typeof raw !== "string" || !raw.trim()) continue;
    out.push({ code: `ssd${out.length + 1}`, text: raw.trim() });
  }
  return out;
}

export type SsdColumn = {
  key: string; label: string;
  /** 占比那几列的值来自合成行（`jointRows`/`jumpRows` 拼的那几行），键名就是那一格读的字段名 */
  sortable: boolean;
  title: string;
};

/**
 * 第一张表：联合对照。
 *
 * 一行是**一类日子**（倍量／准倍量／缩量），上面同时给「全场合计天数」与「这类日子里收涨／收跌的占比」——
 * 天数与占比必须在同一行：分开两张表就会被读成"倍量天数多 ⇒ 倍量天在涨"，那是两件事。
 * 字段名逐个照接口 `joint` 抄（`dbl_total_days`/`dbl_up_share`/`dbl_down_share`／
 * `near_up_share`/`near_down_share`／`calm_up_share`/`calm_down_share`）。
 */
export type SsdJointRow = {
  label: string; kind: string;
  total_days: number | null; up_share: number | null; down_share: number | null;
  /** 这一行的三个数各自出自合同里哪一个字段名（屏上念出来：拼错字段会当场露出来） */
  fields: string;
};

export function jointRows(j: SsdJoint | null | undefined): SsdJointRow[] {
  const x = j ?? {};
  return [
    {
      label: "倍量日（当日量 ≥ 上一根日线量的两倍）", kind: "dbl",
      total_days: num(x.dbl_total_days), up_share: num(x.dbl_up_share), down_share: num(x.dbl_down_share),
      fields: "dbl_total_days · dbl_up_share · dbl_down_share",
    },
    {
      label: "准倍量日（上一根日线量的 1.8~2.0 倍）", kind: "near",
      total_days: null, up_share: num(x.near_up_share), down_share: num(x.near_down_share),
      fields: "near_up_share · near_down_share（合计天数合同没交，格子上是 —）",
    },
    {
      label: "缩量日（当日量 ≤ 上一根日线量的一半）", kind: "calm",
      total_days: null, up_share: num(x.calm_up_share), down_share: num(x.calm_down_share),
      fields: "calm_up_share · calm_down_share（合计天数合同没交，格子上是 —）",
    },
  ];
}

/** 第一张表的列：`key` 就是 `jointRows` 那一格读的字段名。 */
export function jointColumns(): SsdColumn[] {
  return [
    { key: "label", label: "哪一类天", sortable: true, title: "按当日量与上一根日线量的比分成倍量（≥2.0）／准倍量（1.8~2.0）／缩量（≤0.5）三类（vr-1 那把尺，日线代理一天一个数）" },
    { key: "total_days", label: "全场合计天数", sortable: true, title: "接口 dbl_total_days：这一类日子在母体里合计有多少天（准倍量与缩量那两类后端没给合计，格子是 —，不是 0 天）" },
    { key: "up_share", label: "这类天里收涨占比", sortable: true, title: "倍量日是 dbl_up_share、准倍量是 near_up_share、缩量是 calm_up_share：这一类日子里收盘收在涨那一侧的占比" },
    { key: "down_share", label: "这类天里收跌占比", sortable: true, title: "对应的 dbl_down_share／near_down_share／calm_down_share；与收涨占比相加可以不满一，那是平盘与当天没收盘数的那几类" },
    { key: "fields", label: "出自合同哪几个字段", sortable: false, title: "这一格印的是接口原始字段名：屏上每个数都能对回 ssd-1 合同，界面不自己改名" },
  ];
}

export function jointCellText(col: SsdColumn, row: SsdJointRow): string {
  switch (col.key) {
    case "label": return row.label || DASH;
    case "total_days": return intText(row.total_days);
    case "up_share": case "down_share": return shareText(row[col.key]);
    case "fields": return row.fields || DASH;
    default: return DASH;
  }
}

/**
 * 第二张表：突然拉高落在哪一天。
 *
 * 四行都是「占突拉手数的比例」：`jump_on_dbl_share`／`jump_on_near_share`／`jump_on_calm_share`
 * 说的是当日涨过 `jump_pct` 的那一天同时是倍量／准倍量／缩量；`jump_limit_share` 是其中封住涨停的那批。
 * 三个占比加在一起不一定等于一 —— 同一天的量类只归一类，但分母与四舍五入会留下缝，界面不硬凑成 100%。
 */
export type SsdJumpRow = { label: string; field: string; share: number | null; note: string };

export function jumpRows(j: SsdJumps | null | undefined): SsdJumpRow[] {
  const x = j ?? {};
  return [
    {
      label: "突拉那天是倍量日", field: "jump_on_dbl_share", share: num(x.jump_on_dbl_share),
      note: "放量与拉高同一天：量增把价推上去（这一行才是用户那一问的正脸）",
    },
    {
      label: "突拉那天是准倍量日", field: "jump_on_near_share", share: num(x.jump_on_near_share),
      note: "量放了但没到两倍：拉高发生在量增的半路上",
    },
    {
      label: "突拉那天是缩量日", field: "jump_on_calm_share", share: num(x.jump_on_calm_share),
      note: "缩量也能拉高：那种日子靠的是惜售，不是买盘堆量",
    },
    {
      label: "其中封住涨停", field: "jump_limit_share", share: num(x.jump_limit_share),
      note: "拉高以后当天封住涨停的比例（涨停档位随板块不同，跨板块不并榜）",
    },
  ];
}

/** 第二张表的列。 */
export function jumpColumns(): SsdColumn[] {
  return [
    { key: "label", label: "突拉落在哪一类天", sortable: true, title: "当日涨幅过 jump_pct 那条线就算一次突拉；这一行说的是这些突拉日里摊在哪一类量上" },
    { key: "share", label: "占突拉手数的比例", sortable: true, title: "接口字段名就在下一列；三行的分母都是这一档母体里的突拉手数，相加不一定等于一（四舍五入与口径留缝，界面不硬凑 100%）" },
    { key: "field", label: "出自哪个字段", sortable: false, title: "这一格印的是接口原始字段名：屏上每个比例都能对回合同，界面不自己改名" },
    { key: "note", label: "这一行怎么读", sortable: false, title: "同一件事的三种量：这一列是解释，不是读数" },
  ];
}

export function jumpCellText(col: SsdColumn, row: SsdJumpRow): string {
  switch (col.key) {
    case "label": return row.label || DASH;
    case "share": return shareText(row.share);
    case "field": return row.field || DASH;
    case "note": return row.note || DASH;
    default: return DASH;
  }
}

/** 突拉那一行的两句总账（`jump_days_med`／`first_jump_gap_med`），组件只排版、不再算一次。 */
export function jumpHeadline(j: SsdJumps | null | undefined): string {
  const x = j ?? {};
  if (!Object.keys(x).length) return "接口没交 jumps —— 这一屏说不出突拉摊在哪一类量上，界面不凑一个比例。";
  const zero = num(x.jump_days_zero_events);
  const nul = num(x.jump_days_null_events);
  const tail = (zero === null && nul === null)
    ? "后端没交「真 0 天」与「算不出」各自的条数，所以这一屏不能说 0 与 — 各占几条。"
    : `其中窗口凑满但一次突拉都没有的 ${intText(zero)} 条（格子上是 0），`
      + `窗口没凑满／那天没数据而算不出的 ${intText(nul)} 条（格子上是 —）：这两件事在明细里长得不一样。`;
  return `每个事件平均有 ${daysText(x.jump_days_med)} 天突然拉高，第一次突拉离窗口开场 ${gapText(x.first_jump_gap_med)}；`
    + `${tail}`;
}

/** 第三张表的列：分档 × 结果（键名逐个照接口 `buckets`／`base` 抄）。 */
export function answerColumns(): SsdColumn[] {
  return [
    { key: "label", label: "档", sortable: true, title: "按突拉天数分的三档 + 全部母体那一行；区间在下一列念出来（左开右闭）" },
    { key: "range", label: "区间（左开右闭）", sortable: false, title: "接口 range 的两个切点：切点按 col <= cut 落地，所以是左开右闭，边界值只归上一档" },
    { key: "n", label: "事件数", sortable: true, title: "这一档进了聚合的母体事件条数" },
    { key: "dbl_up_days_med", label: "倍量收涨天数（中位）", sortable: true, title: "这一档里倍量日收在涨那一侧的天数中位" },
    { key: "dbl_down_days_med", label: "倍量收跌天数（中位）", sortable: true, title: "同一批里倍量日收跌的天数中位：两个都是中位数，各自独立取整，加起来不必等于倍量总天数" },
    { key: "jump_days_med", label: "突拉天数（中位）", sortable: true, title: "这一档的突拉手数中位；— 是这一档算不出（窗口没凑满／没行情），0 是真的一天都没有" },
    { key: "jump_on_dbl_share", label: "突拉落在倍量的占比", sortable: true, title: "这一档的突拉日里当天同时是倍量日的比例 —— 这一列是那一问的答案" },
    { key: "seg4_rate", label: "≥4 板率", sortable: true, title: "这一档后来走到 4 板及以上的比例。母体是按结果筛过的那几档时这一格不印百分数：那个数是母体定义不是发现，要区分度就选 anyfirst" },
    { key: "r5_med", label: "进场后 5 场（绝对）", sortable: true, title: "进场后 5 场的绝对收益中位" },
    { key: "ex5_med", label: "（超额）", sortable: true, title: "同一批的超额中位：北交所没有对应分类指数，缺的是 —，不是 0%" },
    { key: "win5", label: "胜率", sortable: true, title: "进场后 5 场为正的比例" },
  ];
}

export function answerCellText(col: SsdColumn, row: SsdAggRow, cohort?: unknown): string {
  switch (col.key) {
    case "label": return row.label || DASH;
    case "range": return rangeText(row);
    case "n": return intText(row.n);
    case "dbl_up_days_med": case "dbl_down_days_med": return daysText(row[col.key]);
    case "jump_days_med": return jumpDaysText(row[col.key]);
    case "jump_on_dbl_share": return shareText(row[col.key]);
    case "seg4_rate": return seg4CellText(row.seg4_rate, cohort);
    case "r5_med": case "ex5_med": return signedText(row[col.key]);
    case "win5": return rateText(row[col.key]);
    default: return DASH;
  }
}

/** 三档 + 并排的 `base` 那一行（基线钉在第一行，不参与排序，免得读者以为它在换档）。 */
export function answerRows(buckets: SsdAggRow[] | null | undefined, base: SsdAggRow | null | undefined): SsdAggRow[] {
  const list = buckets ?? [];
  return base ? [base, ...list] : [...list];
}

/** 分档区间：切点按后端 `col <= cut` 的写法是**左开右闭**，必须把两个切点念到屏上。 */
export function rangeText(row: Pick<SsdAggRow, "range" | "label">): string {
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
 * 超额那一列的账：**点名剔了几条**。
 *
 * 合同里没有「被剔掉的条数」这一个字段，所以这里只数接口真交出来的东西：
 * 聚合行里有几档没交 `ex5_med`、本轮明细里有几行读不出 `outcome_ex5`、其中几行是北交所
 * （那批票没有对应分类指数）。被剔除的是分母里的行，不是把它们当成 0 赚。
 * 说不准的绝不编成"已剔除 N 条"。
 */
export function ex5AccountText(agg: SsdAggRow[] | null | undefined, rows: SsdRow[] | null | undefined): string {
  const list = agg ?? [];
  const rowList = rows ?? [];
  if (!list.length && !rowList.length) return "";
  const missingAgg = list.filter(r => num(r.ex5_med) === null).length;
  const missingRows = rowList.filter(r => num(r.outcome_ex5) === null).length;
  const bj = rowList.filter(r => String(r.board ?? "").indexOf("北交") >= 0).length;
  const head = `超额这一列：本轮 ${intText(rowList.length)} 行明细里有 ${intText(missingRows)} 行读不出 outcome_ex5`
    + `（这一轮交出的明细里另有 ${intText(bj)} 行是北交所——那批票没有对应的分类指数），`
    + `聚合那一侧有 ${intText(missingAgg)} 档读不出 ex5_med。`;
  const rule = (missingRows || missingAgg)
    ? "这些行是从超额的中位数分母里剔掉的，不是按 0% 计——把缺数摆成 0% 就成了「没赚也没亏」那条相反的结论；"
      + "格子上是 —，绝对收益那一列（r5_med／outcome_r5）不受这件事影响。"
    : "本轮超额各档、各行都有数：没有因缺分类指数而空着的格子。";
  return `${head}${rule}`;
}

/** 明细（可展开）主表那一排的列。 */
export function groupColumns(): SsdColumn[] {
  return [
    { key: "code", label: "代码", sortable: true, title: "双击这一行开个股深度复盘" },
    { key: "name", label: "名称", sortable: true, title: "" },
    { key: "board", label: "板块", sortable: true, title: "涨停档位与有没有分类指数都随板块不同：跨板块不并榜" },
    { key: "launch_date", label: "启动日", sortable: true, title: "窗口取的是这一天之前的 N 场（不含当天）" },
    { key: "cohort", label: "母体", sortable: true, title: "接口回显这一条事件属于哪一档母体" },
    { key: "bars", label: "用了几根", sortable: true, title: "窗口内它真有的有效交易日根数；小于窗口根数就是不满窗（整行标灰）" },
    { key: "dbl_days", label: "倍量天数", sortable: true, title: "窗口里当日量 ≥ 上一根日线量两倍的天数（vr-1 那把尺）" },
    { key: "dbl_up_days", label: "其中收涨", sortable: true, title: "倍量日里收盘收在涨那一侧的天数" },
    { key: "dbl_down_days", label: "其中收跌", sortable: true, title: "倍量日里收跌的天数：与收涨那列各自独立数，加起来不必等于倍量天数（还有平盘与没收盘数的那天）" },
    { key: "jump_days", label: "突拉天数", sortable: true, title: "窗口里当日涨幅过阈值的天数。0 是真没有，— 是算不出：两件事，悬停各有一句" },
    { key: "jump_dbl_days", label: "突拉落在倍量", sortable: true, title: "这些突拉日里当天同时是倍量日的天数（那一问的逐票答案）" },
    { key: "outcome_len", label: "走到几板", sortable: true, title: "结果列：启动之后走到的段长" },
    { key: "outcome_r5", label: "进场后 5 场（绝对）", sortable: true, title: "结果列：绝对收益，接口已按百分数交" },
    { key: "outcome_ex5", label: "（超额）", sortable: true, title: "结果列：超额。北交所没有对应分类指数 ⇒ —，不是 0%（那是「没赚」）" },
  ];
}

export function groupCellText(col: SsdColumn, r: SsdRow): string {
  switch (col.key) {
    case "code": case "name": case "board": case "launch_date": case "cohort":
      return String(r[col.key] ?? "") || DASH;
    case "bars": case "dbl_days": case "dbl_up_days": case "dbl_down_days":
    case "jump_dbl_days": case "outcome_len":
      return intText(r[col.key]);
    case "jump_days": return jumpDaysText(r.jump_days);
    case "outcome_r5": case "outcome_ex5": return signedText(r[col.key]);
    default: return DASH;
  }
}

/** 明细悬停：0 与 — 分开说，超额缺数不解释成「没赚」。 */
export function groupCellTitle(col: SsdColumn, r: SsdRow): string | undefined {
  if (col.key === "jump_days") return jumpDaysMeaning(r.jump_days);
  if (col.key === "outcome_ex5" && num(r.outcome_ex5) === null) {
    return "这一行读不出超额：北交所没有对应的分类指数（或那天没有可比的指数收盘）。"
      + "格子上是 —，不是 0.00%——0.00% 是「没赚也没亏」，那是另一条结论。";
  }
  return undefined;
}

/** 展开以后那一叠：逐日那些天数的全账（键名都住在接口 `rows` 的行上）。 */
export function detailColumns(): SsdColumn[] {
  return [
    { key: "up_days", label: "收涨天", sortable: false, title: "窗口里收盘收在涨那一侧的天数" },
    { key: "down_days", label: "收跌天", sortable: false, title: "窗口里收跌的天数" },
    { key: "flat_days", label: "平盘天", sortable: false, title: "收得不涨不跌的天数" },
    { key: "still_days", label: "没收盘数天", sortable: false, title: "窗口里那天没有可用收盘数的天数（停牌或没上市）；它不是平盘" },
    { key: "dbl_days", label: "倍量天", sortable: false, title: "当日量 ≥ 上一根日线量的两倍" },
    { key: "near_dbl_days", label: "准倍量天", sortable: false, title: "当日量是上一根日线量的 1.8~2.0 倍" },
    { key: "calm_days", label: "缩量天", sortable: false, title: "当日量 ≤ 上一根日线量的一半" },
    { key: "dbl_up_days", label: "倍量收涨", sortable: false, title: "倍量天里收涨的那些（联合对照那一行落到逐票）" },
    { key: "dbl_down_days", label: "倍量收跌", sortable: false, title: "倍量天里收跌的那些" },
    { key: "near_up_days", label: "准倍量收涨", sortable: false, title: "准倍量天里收涨的那些" },
    { key: "near_down_days", label: "准倍量收跌", sortable: false, title: "准倍量天里收跌的那些" },
    { key: "calm_up_days", label: "缩量收涨", sortable: false, title: "缩量天里收涨的那些" },
    { key: "calm_down_days", label: "缩量收跌", sortable: false, title: "缩量天里收跌的那些" },
    { key: "jump_days", label: "突拉天", sortable: false, title: "当日涨幅过阈值的天数：0 是真没有，— 是算不出" },
    { key: "jump_dbl_days", label: "突拉在倍量", sortable: false, title: "突拉天里当天同时是倍量日的" },
    { key: "jump_near_days", label: "突拉在准倍量", sortable: false, title: "突拉天里当天是准倍量日的" },
    { key: "jump_calm_days", label: "突拉在缩量", sortable: false, title: "突拉天里当天是缩量日的：这一列大就说明拉高不靠放量" },
    { key: "jump_limit_days", label: "突拉且封涨停", sortable: false, title: "突拉以后当天封住涨停的天数" },
    { key: "limit_days", label: "涨停天", sortable: false, title: "窗口里涨停的天数（对照用：突拉里封住的比例分母是突拉手数，不是这一列）" },
    { key: "first_dbl_gap", label: "首场倍量隔几场", sortable: false, title: "窗口开场到第一个倍量日隔了几场；— 是没有倍量日或窗口没凑满" },
    { key: "first_jump_gap", label: "首场突拉隔几场", sortable: false, title: "窗口开场到第一次突然拉高隔了几场" },
  ];
}

export function detailCellText(col: SsdColumn, r: SsdRow): string {
  if (col.key === "jump_days") return jumpDaysText(r.jump_days);
  return daysText(r[col.key]);
}

/** 控件选项：接口交了就吃接口的，没交才退回这份兜底（兜底不含任何编造的键名）。 */
export function optionsFor(list: SsdOption[] | null | undefined, fallback: SsdOption[]): SsdOption[] {
  const got = (list ?? []).filter(o => o && (typeof o.value === "string" || typeof o.value === "number") && o.label);
  return got.length ? got : fallback;
}

/**
 * 母体选项：接口交了就吃接口的，但**`anyfirst` 必须排在第一**。
 *
 * 后端换了名单也不能把「全部首板（不筛结果）」挤到中间去 —— 那档是唯一没按结果筛过的母体，
 * 这一屏那一问（启动前的爆量与突拉是不是预示后来的结果）只有在它上面才有区分度。
 */
export function cohortOptionsFor(list: SsdOption[] | null | undefined): SsdOption[] {
  const opts = optionsFor(list, COHORTS_FALLBACK);
  const any = opts.filter(o => o.value === "anyfirst");
  const rest = opts.filter(o => o.value !== "anyfirst");
  return any.length ? [...any, ...rest]
    : [{ value: "anyfirst", label: "全部首板（不筛结果）" }, ...rest];
}

/**
 * 一句话方向：突拉到底摊在倍量日上还是缩量日上（只读接口 `jumps` 那两个占比，界面不重算）。
 * 两个数都没交就明说"说不出"，不编一句好看的话。
 */
export function spikeHeadline(j: SsdJumps | null | undefined): string {
  const x = j ?? {};
  const onDbl = num(x.jump_on_dbl_share);
  const onCalm = num(x.jump_on_calm_share);
  if (onDbl === null && onCalm === null) {
    return "接口没交突拉落在倍量／缩量的占比 —— 这一屏答不了「拉高摊在哪一类量上」，界面不凑一个方向。";
  }
  if (onDbl === null || onCalm === null) {
    const one = onDbl !== null ? `落在倍量日 ${shareText(onDbl)}` : `落在缩量日 ${shareText(onCalm)}`;
    return `只交出其中一个占比（${one}），另一半没数：两个都没有对照就说不出摊在哪一类量上。`;
  }
  const word = onDbl > onCalm ? "倍量日" : onCalm > onDbl ? "缩量日" : "两类天一样多";
  return `突拉更常落在${word}上：落在倍量日 ${shareText(onDbl)}、落在缩量日 ${shareText(onCalm)}，`
    + `差 ${Math.abs(pctOf(onDbl) - pctOf(onCalm)).toFixed(1)} 个百分点；`
    + `封住涨停的那批占 ${shareText(x.jump_limit_share)}。`
    + "这一句读的是日线代理（一天一个数），不是分时图上那种几分钟的急拉。";
}

/** 倍量日收涨还是收跌（只读 `joint` 那两列，界面不重算）。 */
export function jointHeadline(j: SsdJoint | null | undefined): string {
  const x = j ?? {};
  const up = num(x.dbl_up_share);
  const down = num(x.dbl_down_share);
  if (up === null || down === null) {
    return "接口没交倍量日的收涨或收跌占比 —— 这一屏说不出倍量那天在涨还是在跌。";
  }
  const word = up > down ? "收涨" : down > up ? "收跌" : "两边一样";
  return `倍量日里合计 ${intText(x.dbl_total_days)} 天，其中${word}的多：收涨 ${shareText(up)}、收跌 ${shareText(down)}；`
    + `对照缩量日收涨 ${shareText(x.calm_up_share)}、收跌 ${shareText(x.calm_down_share)}。`
    + "这两个占比读的是同一批日子吗？不是——倍量与缩量的分母各是自己的天数，所以必须并排看，不能只挑一边。";
}
