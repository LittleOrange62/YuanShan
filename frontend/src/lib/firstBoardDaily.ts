import { NULL_TEXT } from "./datastate.ts";
import { trimNum } from "./ranking.ts";

/**
 * 首页「首板分析」的读法层（用户 2026-10-21 原话：「全市场换手率 TOP20 上面加一个首板分析，
 * 分析首板个股的情况，已经近期的走势，分析涨停原因，**特别是前一天的走势**」）。
 *
 * 判据、阈值、形态档的名字与口径句子**全部只住后端**（写侧 `app/compute/first_board_daily.py`、
 * 读数侧 `duanxian/first_board_daily.py`），随 `thresholds` / `definitions` / `rule` / `caliber`
 * 交出。这一层只做两件事：格式化，以及"缺数怎么说话"。**不重算分档、不写死 5.0／3.5／2.0／0.77／1.40**
 * ——写死就是第二份真相源，改阈值那一晚屏幕会悄悄说谎。
 *
 * 四条界面上不许糊掉的线（每条都对着后端一句 caveat）：
 *  · null 一律 `—`：`break_count` 空是「源站这一行没给这个字段」，不是「0 次没炸」；
 *    `prev_*` 空是「没有前一交易日那一行」，不是「平盘」；`boards` 空是算不出几天几板，不拿源站那句顶上。
 *  · 涨停原因串空着分两种，**措辞必须不一样**：`source_row_absent`＝源站没收这只（ST／北交所基本不收），
 *    `source_no_records`＝那场整池没取到（这才是本机漏采）。
 *  · 全场聚合（`shape_tally`／`reason_tally`／`universe`／`first_count`）永远是**这一场全部首板**的数，
 *    不随形态筛与取数上限变 —— 所以每档后面那个数不许被读成"筛完剩下的只数"。
 *  · `available:false` 时后端**不交 `rows` 键**、只交 `reason`（里面写着该跑哪条补跑命令）：
 *    界面原样念出来，不许摆一张空表。
 *
 * 零 IO import（只碰 `datastate` 与 `ranking` 那两份纯显示层），所以 `node --test` 能直接把这些
 * 函数拿来跑真值，不只看源码。
 */

/** 后端 `Query(80, ge=10, le=240)`：界面只交这三档，**不夹更宽**（10-18 那次前端夹得比后端松，
 *  每次 422，而面板把 422 说成"这一屏没接上"）。 */
export const LIMIT_OPTIONS = [80, 150, 240];
/** 每页行数只有这两档：一屏读完一场首板（实测 30~60 只）不需要第三档。 */
export const PAGE_SIZES = [25, 50];

export interface FbdThresholds {
  deep_pullback_pct?: number | null;
  surge_unsealed_pct?: number | null;
  flat_abs_pct?: number | null;
  shrink_vol_ratio?: number | null;
  mild_up_vol_ratio?: number | null;
}

/** 一行的全部读数（键名与接口 `rows[]` 逐字一致；列头 `sortKey` 也用它，写错点了不换序）。 */
export interface FbdRow {
  code: string;
  name?: string | null;
  board?: string | null;
  market?: string | null;
  launch_pct?: number | null;
  launch_close?: number | null;
  launch_turnover?: number | null;
  launch_amount_yi?: number | null;
  limit_up_price?: number | null;
  is_one_word?: boolean | null;
  float_cap_yi?: number | null;
  reason?: string | null;
  reason_missing?: string | null;
  first_seal_at?: string | null;
  last_seal_at?: string | null;
  break_count?: number | null;
  order_amount_wan?: number | null;
  limit_up_kind?: string | null;
  source_high_days?: string | null;
  boards?: number | null;
  board_days?: number | null;
  board_label?: string | null;
  source_boards_agree?: boolean | null;
  prev_date?: string | null;
  prev_pct?: number | null;
  prev_turnover?: number | null;
  prev_amount_yi?: number | null;
  prev_vol_ratio?: number | null;
  prev_touched?: boolean | null;
  prev_sealed?: boolean | null;
  prev_shape?: string | null;
  prev_shape_label?: string | null;
  prev_shape_note?: string | null;
  chg5?: number | null;
  chg10?: number | null;
  chg15?: number | null;
  vol_grab_ratio?: number | null;
  dbl_days?: number | null;
  calm_days?: number | null;
  dbl_energy?: number | null;
  dbl_rank?: number | null;
  energy_universe?: number | null;
  /** 10-27 新加：近 20 交易日封住涨停的次数与窗口里真有的根数（新股/停牌会不足） */
  boards_20d?: number | null;
  boards_20d_bars?: number | null;
  /** 近 5 场四维象限与全市场名次（由早到晚；`stale`＝旧系统迁入的基准，名次不参与对比） */
  quad?: { day: string; quadrant?: string | null; rank?: number | null;
           value?: number | null; stale?: boolean }[];
  /** 近 10 / 近 20 场的倍量分布（分母是「对昨日量倍数」vr-1，与倍量加权分那把尺不同）；
   *  一格两个数（倍量次／准倍量次），没有单一大小，所以这两列不给排序。后端平铺交，不嵌一层。 */
  dbl10?: { bars?: number | null; dbl?: number | null; near?: number | null } | null;
  dbl20?: { bars?: number | null; dbl?: number | null; near?: number | null } | null;
  /** 那两列的分母名字（后端交，界面不自己造） */
  dbl_unit?: string | null;
  /** 窗口截至哪一天（后端交）：取错年代时这个数当场露馅，格子里也念它 */
  dbl_last_day?: string | null;
  /** 盘中信号（异动筛查 spk-1）：档位显示中文、`spike_tier_rank` 才是排序用的那一个数 */
  spike_tier?: string | null;
  spike_tier_rank?: number | null;
  spike_first5_at?: string | null;
  spike_speed15_pct?: number | null;
  spike_above5_min?: number | null;
  spike_tags?: string[];
  /** true＝这一族探过了、这只票没进榜；false/缺＝这一族没读到（两种 — 不一样） */
  spike_checked?: boolean | null;
  // ── 这只票自己的历史位置（10-27 用户点名的四列；判据只住写侧，这里只念）──
  /** 近 3 个月（63 场）涨停：封住＝收盘贴在板上，触及＝盘中摸到过（含炸开的）。两个口径都给 */
  lu_sealed_3m?: number | null;
  lu_touched_3m?: number | null;
  ld_sealed_3m?: number | null;
  ld_touched_3m?: number | null;
  /** 近 6 个月连板最高（读 lup-1 的 consec，本屏不自己数） */
  max_consec_6m?: number | null;
  /** 两年区间位置%：现价在「两年最低—两年最高」这根区间里的位置，全部后复权 */
  pos_2y?: number | null;
  close_adj?: number | null;
  hi_2y?: number | null;
  lo_2y?: number | null;
  bars_2y?: number | null;
  /** 均线排列四条线原值（复权收盘）+ 档位短码 + 中文档名 + 排序用的那一个数 */
  ma5?: number | null;
  ma10?: number | null;
  ma20?: number | null;
  ma60?: number | null;
  ma_stack?: string | null;
  ma_stack_label?: string | null;
  ma_stack_note?: string | null;
  ma_stack_rank?: number | null;
  missing?: Record<string, string> | null;
}

export interface ShapeTallyItem {
  key: string | null;
  label: string;
  n: number | null;
}

export interface ReasonSummaryItem {
  name: string;
  count: number;
}

export interface SummaryMeta {
  scope?: string | null;
  stock_count?: number | null;
  no_reason?: number | null;
  tag_total?: number | null;
  dropped_single_reason?: number | null;
  dropped_single_industry?: number | null;
  industry_note?: string | null;
}

export interface ReasonTally {
  reason_summary?: ReasonSummaryItem[] | null;
  industry_summary?: ReasonSummaryItem[] | null | null;
  summary_meta?: SummaryMeta | null;
}

/** 接口顶层（`available:false` 那一支只有 `available`/`what`/`date`/`reason`，**没有 `rows`**） */
export interface FirstBoardDaily {
  available: boolean;
  what?: string | null;
  reason?: string | null;
  date?: string | null;
  spec_version?: string | null;
  rule?: string | null;
  rows?: FbdRow[];
  rows_returned?: number | null;
  limit?: number | null;
  shape_filter?: string[] | null;
  first_count?: number | null;
  universe?: number | null;
  one_word?: number | null;
  prev_touched?: number | null;
  prev_pct_med?: number | null;
  boards_disagree?: number | null;
  boards_null?: number | null;
  shape_tally?: ShapeTallyItem[] | null;
  reason_tally?: ReasonTally | null;
  coverage?: { sessions?: number | null; from?: string | null; to?: string | null } | null;
  caliber?: string | null;
  definitions?: Record<string, string> | null;
  thresholds?: FbdThresholds | null;
  caveats?: { code?: string | null; text?: string | null; hard?: boolean | null }[] | null;
  calculated_at?: string | null;
  source?: string | null;
  /** 10-27 新增那四列的元数据（窗口/场次/倍数/判据版本）：窗口长度读这里，界面不自己写死 */
  recent?: {
    boards_window?: number | null;
    quad_sessions?: number | null;
    dbl_windows?: number[] | null;
    dbl_mult?: number | null;
    dbl_near?: number | null;
    dbl_spec?: string | null;
    spike_spec?: string | null;
    spike_tier_labels?: Record<string, string> | null;
    spike_thresholds?: { fast_pct?: number | null; speed_min_pct?: number | null;
                         speed_window?: number | null } | null;
  } | null;
  /** 四列历史位置的窗口与档位（10-27）：窗口长度与档名一律读这里，界面不抄第二份 */
  history?: {
    sessions_3m?: number | null;
    sessions_6m?: number | null;
    sessions_2y?: number | null;
    pos_min_bars?: number | null;
    ma_spans?: number[] | null;
    stack_names?: Record<string, string> | null;
    stack_labels?: Record<string, string> | null;
    stack_order?: string[] | null;
    seal_eps?: number | null;
  } | null;
}

/** 只认真数：null／undefined／NaN／Infinity 都是"没读到"，绝不当成 0。 */
export function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** 只数（只数、行数、场数）：缺数是 —，真 0 照常念 0。 */
export function intText(v: unknown): string {
  const n = num(v);
  return n === null ? NULL_TEXT : Math.trunc(n).toLocaleString("zh-CN");
}

/**
 * 百分比：带符号，缺数是 —。
 * 涨幅 0 是一条实质读数（平盘），与"没有前一交易日那一行"是两件事，所以这里不共用一个空壳。
 */
export function pctText(v: unknown, digits = 2): string {
  const n = num(v);
  if (n === null) return NULL_TEXT;
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

/** 比值／量能比（量比、抢跑比）：去尾零走公用件那把尺，缺数是 —。 */
export function ratioText(v: unknown): string {
  return trimNum(v, 4);
}

/** 金额（亿元）：单位已由后端定死，界面不换算（01 第三条：单位换算只允许发生在上游）。 */
export function yiText(v: unknown): string {
  const n = num(v);
  return n === null ? NULL_TEXT : `${trimNum(n, 3)} 亿`;
}

/** 封单额（万元）：同上，万是后端交的单位。 */
export function wanText(v: unknown): string {
  const n = num(v);
  return n === null ? NULL_TEXT : `${trimNum(n, 1)} 万`;
}

/** 形态档多选：点一个档就重发一次请求，非法档由后端退回并出声（这里不校对档名）。 */
export function toggleShapeSel(sel: string[], key: string): string[] {
  return sel.includes(key) ? sel.filter(k => k !== key) : [...sel, key];
}

/** 空选＝不筛：交 undefined，让 `useProbe` 干脆不带这个参数（不带与带空串在后端同义，但少一次噪音）。 */
export function shapeQuery(sel: string[]): string | undefined {
  return sel.length ? sel.join(",") : undefined;
}

/** 每档的按钮文字：档名＋全场只数（`—` 时不许念成 0 只）。 */
export function shapeButtonLabel(t: ShapeTallyItem): string {
  return `${t.label ?? NULL_TEXT} ${intText(t.n)}`;
}

/** 判不了那一档（`key` 为 null）：它不是七档里的某一档，是形态算不出来。 */
export function unjudgedShape(tally?: ShapeTallyItem[] | null): ShapeTallyItem | null {
  return (tally ?? []).find(t => t && t.key === null) ?? null;
}

/**
 * 阈值那句文字：**每个数字都从 `thresholds` 读**，一个都不写死。
 * 后端没交（或某一键没交）就点名说没交，页面不补一份自己的阈值。
 */
export function thresholdsText(th?: FbdThresholds | null): string {
  if (!th) return "";
  // 不加正号：`flat_abs_pct` 是 |涨幅| 的上界，写成 "+2%" 会被读成"涨到 2% 才算横盘"。
  const p = (v: unknown, unit: string) => {
    const n = num(v);
    return n === null ? "没交" : `${trimNum(n, 2)}${unit}`;
  };
  const r = (v: unknown) => {
    const n = num(v);
    return n === null ? "没交" : trimNum(n, 2);
  };
  return `前一日分档的阈值（后端 thresholds，界面一个都不写死）：大跌 ≤ ${p(th.deep_pullback_pct, "%")}`
    + `、冲高未封 ≥ ${p(th.surge_unsealed_pct, "%")}、横盘 |涨幅| ≤ ${p(th.flat_abs_pct, "%")}`
    + `、缩量 量比 ≤ ${r(th.shrink_vol_ratio)}、温和放量上界 量比 ${r(th.mild_up_vol_ratio)}。`;
}

/** 涨停原因串空着的两种说法（措辞必须不一样；`reason_missing` 的取值只住写侧）。 */
export const GAP_SOURCE_ROW_ABSENT = "source_row_absent";
export const GAP_NO_RECORDS = "source_no_records";

export function reasonGapText(code?: string | null): string {
  if (code === GAP_SOURCE_ROW_ABSENT) {
    return "这一格空着＝源站没收这只：同花顺涨停揭秘池不收北交所、也几乎不收 ST —— "
      + "说的是「源站没有这只票」，不是本机漏采。";
  }
  if (code === GAP_NO_RECORDS) {
    return "这一格空着＝本场整池没取到：源站这一场的涨停揭秘池整场没有记录 —— "
      + "这才是本机漏采，补跑走采集侧（不是这一屏能算出来的）。";
  }
  return "这一格空着，而接口没交这一行为什么空（`missing.reason` 没给）—— "
    + "上面两种说法页面不替它挑一种，只把「没读到原因」这件事原样报出来。";
}

/** 原因那一格：有串就原样念串（数据值不包 plain），没串念"为什么没串"。 */
export function reasonCell(row: FbdRow): { text: string; isReason: boolean } {
  const r = (row.reason ?? "").trim();
  if (r !== "") return { text: r, isReason: true };
  return { text: reasonGapText(row.reason_missing), isReason: false };
}

/**
 * 原因覆盖：`stock_count − no_reason` 只，`no_reason` 单列。
 * 两个数少任何一个都算不出覆盖 —— 那时念「读不到」，绝不把缺数说成「0 只有原因」。
 */
export function reasonCoverage(meta?: SummaryMeta | null): {
  covered: string; noReason: string; stockCount: string; note: string;
} {
  const stock = num(meta?.stock_count);
  const noR = num(meta?.no_reason);
  const covered = stock === null || noR === null ? null : stock - noR;
  return {
    covered: covered === null ? NULL_TEXT : Math.trunc(covered).toLocaleString("zh-CN"),
    noReason: intText(meta?.no_reason),
    stockCount: intText(meta?.stock_count),
    note: covered === null
      ? "覆盖算不出：接口的 `summary_meta.stock_count` 或 `no_reason` 有一个没读到（不是 0 只）。"
      : `本场 ${Math.trunc(stock!).toLocaleString("zh-CN")} 只首板里 ${Math.trunc(noR!).toLocaleString("zh-CN")} 只没有原因串`
        + "（那一格上面分开念：源站没收这只／本场整池没取到是两件事，没读到的那些不叫「没有原因」）。",
  };
}

/** 题材词榜：只列出现 2 次及以上的词（1 次的不叫重复出现）。 */
export function repeatedWords(list?: ReasonSummaryItem[] | null): ReasonSummaryItem[] {
  return (list ?? []).filter(x => x && num(x.count) !== null && (num(x.count) as number) > 1);
}

/** 行业那一侧：后端今天不交汇总（交 null），界面念它那一句，不摆一个空的行业榜。 */
export function industrySide(tally?: ReasonTally | null): { words: ReasonSummaryItem[] | null; note: string | null } {
  const raw = tally?.industry_summary;
  if (Array.isArray(raw)) return { words: repeatedWords(raw), note: null };
  if (raw === null || raw === undefined) {
    const note = (tally?.summary_meta?.industry_note ?? "").trim();
    return { words: null, note: note === "" ? "接口这一场没交行业汇总，也没交为什么没交 —— 页面不猜。" : note };
  }
  return { words: null, note: "接口交来的行业汇总不是列表（形状对不上），这一格不渲染。" };
}

/** 「几天几板」那一列 2026-10-27 用户点名撤掉（换成 `boardsWindowCell` 那把近 20 场的次数），
 *  源站那句 `source_high_days` 因此在这屏不再有格子可对照，页面不再念它。
 *  封住判据与容差仍只住 `duanxian/board_span.py` 一份，近 20 场那一列用的就是它。 */

/**
 * 「交出的行」与「本场首板总数」的关系：这一句是**截断声明**，按规矩渲染在折叠框外面。
 * 筛了档与被上限切是两种"少了"，都不许被读成「今天没有」。
 */
export function truncationSentence(returned: number, d: FirstBoardDaily): string {
  const total = num(d.first_count);
  if (total === null) {
    return "本场首板总只数没读到（`first_count` 是 —）—— 下面这张表是不是全场，界面说不出，"
      + "不拿已交出的行数当总数。";
  }
  if (returned >= total) {
    return `接口交出 ${returned.toLocaleString("zh-CN")} 行，本场首板共 ${total.toLocaleString("zh-CN")} 只 —— 已全部列出`
      + `（取数上限 ${intText(d.limit)} 只没用满）。`;
  }
  const filtered = (d.shape_filter ?? []).length > 0;
  return `接口交出 ${returned.toLocaleString("zh-CN")} 行，本场首板共 ${total.toLocaleString("zh-CN")} 只 —— 少的那些是`
    + (filtered
      ? `被现在的形态筛（${d.shape_filter!.join("、")}）挡在外面的`
      : `被取数上限 ${intText(d.limit)} 只切掉的`)
    + "，不是「今天没有」；上面每档与每格的全场口径数都不随这两个参数变。";
}

/** 已交出行里「前一日收盘封住」为真的只数：母体定义下应恒 0，冒出真值就是自检出事，必须出声。
 *  （10-27 起这一列不在屏上摆了，自检仍照着跑——它是写侧口径的哨兵，不是给用户看的。） */
export function prevSealedHits(rows: FbdRow[]): number {
  return rows.filter(r => r.prev_sealed === true).length;
}

/** 近 5 场四维象限：一格摆「MM-DD 象限 名次」的序列，缺场次就少几段（不拿 — 凑满五段）。 */
export function quadCell(row: FbdRow): string {
  const q = row.quad || [];
  if (!q.length) return "—";
  return q.map(x => `${String(x.day).slice(5)} ${x.quadrant || "—"}${x.rank == null ? "" : ` ${x.rank}`}${x.stale ? "〈旧〉" : ""}`)
    .join(" → ");
}

/** 悬停里逐场给全日期、名次与分值；`stale` 那一段必须点名（旧基准的名次不参与对比）。 */
export function quadTitle(row: FbdRow, base?: string): string {
  const q = row.quad || [];
  const head = q.length
    ? q.map(x => `${x.day} ${x.quadrant || "象限未给"}｜名次 ${x.rank ?? "—"}｜分值 ${x.value ?? "—"}${x.stale ? "（旧基准，名次不参与对比）" : ""}`)
      .join("\n")
    : "这一格是 —：这批场次在 analytics.score 里没有这只票的行（没算到），不是「排名第 0」。";
  return base ? `${head}\n${base}` : head;
}

/** 近 N 场有过几天收盘封住涨停：次数读不到就 `—`（不是「一场都没封」）；
 *  窗口根数不足（新股、停牌）时把根数写进格子，根数没读到不许兜成 0。 */
export function boardsWindowCell(row: FbdRow, window: number | null): string {
  const n = num(row.boards_20d);
  if (n === null) return NULL_TEXT;
  const bars = num(row.boards_20d_bars);
  const short = window !== null && bars !== null && bars < window;
  return `${intText(n)} 板${short ? `（${intText(bars)} 根）` : ""}`;
}

export function boardsWindowTitle(row: FbdRow, window: number | null, base?: string): string {
  if (num(row.boards_20d) === null) {
    return "这一格是 —：窗口里的日线没读到这一只，不是「这些场里一次都没封住涨停」。";
  }
  const bars = num(row.boards_20d_bars);
  const head = `窗口（${window === null ? "接口没交长度" : `${window} 场`}）里这一只真有的日线根数：`
    + (bars === null ? "没读到" : `${intText(row.boards_20d_bars)} 根`)
    + "。新股与停牌会不足，所以次数要按实际根数读。";
  return base ? `${head}｜${base}` : head;
}

/** 近 N 场的倍量分布：接口平铺交 dbl10／dbl20，别的窗口号没交过 ⇒ 这一格没有对应字段，念 —。 */
function dblWindow(row: FbdRow, window: number) {
  return window === 20 ? row.dbl20 : window === 10 ? row.dbl10 : undefined;
}

/** 近 N 场的倍量分布格子：`2 倍量·1 准倍量`；窗口不足 N 根时把根数写出来（新股不是「缩量安静」）。
 *  两个数都没读到就整格 `—`，读到的那个数才念 —— 缺的那半不许兜成 0（0 次倍量是实质读数）。 */
export function dblCell(row: FbdRow, window: number): string {
  const w = dblWindow(row, window);
  if (!w) return "—";
  const parts: string[] = [];
  if (w.dbl != null) parts.push(`${w.dbl} 倍量`);
  if (w.near != null) parts.push(`${w.near} 准倍量`);
  if (parts.length === 0) return "—";
  const bars = num(w.bars);
  return parts.join("·") + (bars != null && bars < window ? `（${bars} 根）` : "");
}

/** 这一列的分母必须点名：它与「倍量加权分」那把（≥2×前 5 场均量）不是同一件事。 */
export function dblTitle(row: FbdRow, window: number, base?: string): string {
  const head = `近 ${window} 场里量 ≥2×${row.dbl_unit || "对昨日量"} 的日子数；准倍量是 1.6~2 那一段。`
    + "这一把的分母是「对昨日量」（vr-1，加工段每日算），与同一屏「倍量加权分 / 名次」那把"
    + "（量 ≥2×前 5 场均量，40 场窗口）不是一件事。";
  const w = dblWindow(row, window);
  const none = w ? "" : "\n这一格是 —：这批场次里没读到这一只的量能行（没算到），不是「一次倍量都没有」。";
  const last = row.dbl_last_day ? `\n这个窗口里这一只最近的一根量能行在 ${row.dbl_last_day}（窗口取的就是离它最近的那 ${window} 场）。` : "";
  return `${head}${last}${none}${base ? `\n${base}` : ""}`;
}

/** 盘中信号格的悬停：「没进榜」「这一族没读到」「分钟线没到这天」是三件事，分开说。 */
export function spikeTitle(row: FbdRow, base?: string): string {
  let head: string;
  if (!row.spike_checked) head = "这一格是 —：盘中异动那一族这次没读到（不等于「没冲高」）。";
  else if (!row.spike_tier) head = "没进异动榜：入表要先过最高涨幅或 15 分钟拉升的阈值，没过就没有分时读数 —— 这不等于「盘中没冲高」。";
  else head = `标签：${(row.spike_tags || []).join("、") || "无"}｜+5% 之上持续 ${row.spike_above5_min ?? "—"} 分钟`;
  return base ? `${head}\n${base}` : head;
}

// ───────────────────────── 这只票自己的历史位置（10-27 四列）─────────────────────────

/** 近 3 个月涨/跌停那一格：封住与触及两个口径并排（只留一个就会把"冲板没封住"那批藏掉）。
 *  缺数交 —，绝不当 0 —— 0 在这里是真读数（这三个月一次都没贴过板）。 */
export function limitCountCell(row: FbdRow, kind: "lu" | "ld"): string {
  const sealed = kind === "lu" ? row.lu_sealed_3m : row.ld_sealed_3m;
  const touched = kind === "lu" ? row.lu_touched_3m : row.ld_touched_3m;
  if (sealed == null && touched == null) return NULL_TEXT;
  return `封${sealed ?? NULL_TEXT}／触${touched ?? NULL_TEXT}`;
}

export function limitCountTitle(row: FbdRow, kind: "lu" | "ld",
                                hist?: FirstBoardDaily["history"], base?: string): string {
  const n = hist?.sessions_3m ?? null;
  const which = kind === "lu" ? "涨停" : "跌停";
  const sealed = kind === "lu" ? row.lu_sealed_3m : row.ld_sealed_3m;
  const touched = kind === "lu" ? row.lu_touched_3m : row.ld_touched_3m;
  const head = `近 ${n ?? "?"} 个市场场次（≈3 个月）里有几天${which}：`
    + `这只票封住 ${sealed ?? "—"} 天、盘中摸到过 ${touched ?? "—"} 天。`
    + "封＝收盘贴在板上，触＝摸到过那个价（含后来打开的），两把尺差出来的就是那批冲板没封住的。"
    + `按${kind === "lu" ? "涨停封住" : "跌停封住"}那个数排序，另一个数只在格子里。`
    + (n == null ? "（窗口场数后端没交，这句里的数字是 ?，别当成 3 个月）" : "");
  return base ? `${head}\n${base}` : head;
}

/** 近 6 个月连板最高：一个数；0/— 分得开（— 是读不到，不是没连过板）。 */
export function consecCell(row: FbdRow): string {
  return row.max_consec_6m == null ? NULL_TEXT : `${intText(row.max_consec_6m)} 板`;
}

export function consecTitle(row: FbdRow, hist?: FirstBoardDaily["history"], base?: string): string {
  const head = `近 ${hist?.sessions_6m ?? "?"} 个市场场次（≈6 个月）里最远走到第几板：`
    + `这只票是 ${consecCell(row)}。`
    + "读涨停母体表（lup-1）的连板数，本屏不自己数一遍。"
    + "它与「近 20 场几板」不是一把尺：那一列数的是这个窗口里封住过几回，这一列看连续的一段走到第几板。";
  return base ? `${head}\n${base}` : head;
}

/** 两年区间位置%：不足根数时说"窗口只有 N 根，判不了"，不报一个偏小的位置。 */
export function pos2yCell(row: FbdRow, hist?: FirstBoardDaily["history"]): string {
  const bars = row.bars_2y;
  const min = hist?.pos_min_bars;
  if (row.pos_2y != null) return `${trimNum(row.pos_2y, 1)}%（${intText(bars)} 根）`;
  if (bars != null && min != null && bars < min) return `窗口只有 ${intText(bars)} 根`;
  return NULL_TEXT;
}

export function pos2yTitle(row: FbdRow, hist?: FirstBoardDaily["history"], base?: string): string {
  const min = hist?.pos_min_bars;
  const head = `两年区间位置% =（现价 − 两年最低）÷（两年最高 − 两年最低）×100，全部后复权。`
    + `现价 ${trimNum(row.close_adj)}｜两年顶 ${trimNum(row.hi_2y)}｜两年底 ${trimNum(row.lo_2y)}`
    + `｜窗口（${hist?.sessions_2y ?? "?"} 场）里真有 ${intText(row.bars_2y)} 根`
    + (min != null ? `，不足 ${intText(min)} 根就判不了` : "") + "。"
    + "它不是涨幅、也不是分位数（分位要数有多少天比它低）。";
  return base ? `${head}\n${base}` : head;
}

/** 均线排列那一格念后端交的中文档名；排序走 `ma_stack_rank`（显示值与排序键不是一个字段）。 */
export function maStackCell(row: FbdRow): string {
  return row.ma_stack_label || row.ma_stack || NULL_TEXT;
}

export function maStackTitle(row: FbdRow, hist?: FirstBoardDaily["history"], base?: string): string {
  const spans = hist?.ma_spans ?? [];
  const vals = [row.ma5, row.ma10, row.ma20, row.ma60];
  const head = spans.length && vals.every(v => v != null)
    ? `${spans.map((s, i) => `MA${s} ${trimNum(vals[i])}`).join("｜")}（后复权收盘）`
    : "四条均线没凑齐（不足 60 根就判不了，不拿三档冒充五档）";
  const note = row.ma_stack_note || "";
  return [head, note, base].filter(Boolean).join("\n");
}

/** 三态布尔格子：真是「是」、假是「否」、null 是 —（null 不是「否」）。 */export function boolText(v: unknown, yes = "是", no = "否"): string {
  if (v === true) return yes;
  if (v === false) return no;
  return NULL_TEXT;
}

/** `missing` 里那一键的原因码（没有就 null，由调用方念"接口没交原因"）。 */
export function missingCode(row: FbdRow, key: string): string | null {
  const m = row.missing;
  if (!m || typeof m !== "object") return null;
  const v = (m as Record<string, unknown>)[key];
  return typeof v === "string" && v.trim() !== "" ? v : null;
}

/** 炸板次数那一格：null 是「源站这一行没给这个字段」，不是 0 次没炸。 */
export function breakCountText(v: unknown): string {
  const n = num(v);
  return n === null ? NULL_TEXT : Math.trunc(n).toLocaleString("zh-CN");
}

/** 首封时间：没有戳就是「源站没给」，排在最后（后端 `order by first_seal_at asc nulls last`）。 */
export function sealText(v: unknown): string {
  if (typeof v === "string" && v.trim() !== "") return v;
  return NULL_TEXT;
}
