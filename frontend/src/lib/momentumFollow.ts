import { NULL_TEXT } from "./datastate.ts";
import { truncationSentence, type SteadyData } from "./steadyRank.ts";

/**
 * 「上一场名次稳步前进榜 · 后续表现」的读侧类型与纯判读（用户 2026-10-20 凌晨点名：
 * 「昨日前 80 名的进入股价表现（把各种名次都显示出来，包括最近倍量名次，突破信号等等）」）。
 *
 * 判据一个字都不在这里：母体（哪张榜、前几名、xy/oper 两档）住接口那一份
 * （`duanxian/quant.py::rank_momentum`，由 `duanxian/momentum_follow.py` 调用），
 * 涨跌幅的尺子住 `duanxian/day_returns.py`，名次规则文案读接口的 `rank.rule`
 * （`duanxian/ranking.py` 那一份）。这里只做两件零 IO 的事：把交出的数读成一句人话、
 * 把「缺数」与「0/否」这两类格子分开放到屏幕上（缺＝ —，0 与「否」是实质读数）。
 *
 * 零依赖 import（`datastate` 的常量与 `steadyRank` 的纯函数），`node --test` 直接跑得动。
 *
 * 2026-10-27 用户点名的两处改名：母体默认是**前一场**那张榜 ⇒ 母体之后的第一场行情就是今天，
 * 所以那一列在屏上叫「今日涨跌」（旧名「次日涨跌」让人以为要等到明天才有数）；
 * 「至今累计」撤下（母体是前一场时它与「今日涨跌」是同一个数，摆两列是假的两把尺），
 * 换成「近 5 日涨跌」＝读至日往回 5 个市场场次的累计，**不以母体日为起点** ⇒ 两列不许相减。
 */

/** 与母体榜同一档 metric（界面上就是那两个按钮；坏值由后端 `_reject` 拒掉，不在这里兜）。 */
export type FollowMetric = "xy" | "oper";

/** 行上的每一个键都是接口 `definitions` 的键（后端有真库测试钉两边一一对应）。全部可空。 */
export interface FollowRow {
  code?: string | null;
  name?: string | null;
  board?: string | null;
  /** 母体那场榜里它排第几行 —— **不是全场名次** */
  board_rank?: number | null;
  first_series?: number | null;
  latest_series?: number | null;
  gain_abs?: number | null;
  gain_frac?: number | null;
  oper_value?: number | null;
  quadrant?: string | null;
  quadrant_label?: string | null;
  matched_total?: number | null;
  next_day?: string | null;
  chg_next_day?: number | null;
  chg_5d?: number | null;
  bars_5d?: number | null;
  base_5d_day?: string | null;
  perf5_missing_reason?: string | null;
  perf5_missing_note?: string | null;
  bars_after?: number | null;
  perf_missing_reason?: string | null;
  perf_missing_note?: string | null;
  dbl_energy?: number | null;
  dbl_rank?: number | null;
  dbl_missing_reason?: string | null;
  dbl_missing_note?: string | null;
  oper_rank?: number | null;
  breakout_type?: string | null;
  breakout_type_label?: string | null;
  /** 埋伏**榜内**名次（那张表只落命中的票）—— 与全场名次不是同一把尺 */
  ambush_rank_in_board?: number | null;
  ambush_type_labels?: string | null;
  ambush_score?: number | null;
  /** 三态：true=那天有榜且它在、false=那天有榜而它没进（实质读数）、null=那天没跑/没读到 */
  abnormal_hit?: boolean | null;
  abnormal_universe_by_label?: string | null;
  trend_selected5?: boolean | null;
  trend_selected10?: boolean | null;
}

export interface FollowData {
  available?: boolean | null;
  reason?: string | null;
  /** 表现读到哪一场（库里最新一场行情），与 parent_date 分开 */
  date?: string | null;
  /** 母体榜那一场（默认＝库里最新有名次场次的前一场；这句原话随 default_parent_rule 交出） */
  parent_date?: string | null;
  default_parent_rule?: string | null;
  metric?: string | null;
  metric_label?: string | null;
  series_label?: string | null;
  /** 母体榜的判据原句（`rank_momentum` 那一份，逐字转述、不另写） */
  rule?: string | null;
  window?: { sessions?: number | null; steps?: number | null; first_day?: string | null;
             last_day?: string | null; next_day?: string | null; to_date?: string | null;
             sessions_after?: number | null } | null;
  limit?: number | null;
  page_size?: number | null;
  total_matched?: number | null;
  thresholds?: Record<string, unknown> | null;
  fingerprint?: string | null;
  rank?: { rule?: string | null; rule_short?: string | null;
           universe?: { scored?: number | null; parent_matched?: number | null;
                        board_rows?: number | null; dbl_scored?: number | null;
                        breakout_rows?: number | null; ambush_rows?: number | null;
                        abnormal_rows?: number | null; trend_rows?: number | null;
                        note?: string | null } | null;
           by?: Record<string, string> | null } | null;
  pct_rule?: string | null;
  indicator_counts?: Record<string, number | string | null> | null;
  rows?: FollowRow[] | null;
  definitions?: Record<string, string> | null;
  coverage?: Record<string, { from?: string | null; to?: string | null; sessions?: number | null }> | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
  source?: string | null;
}

/** 三态布尔格子的读法：**null 是「没读到」**，与 false（那天有榜而它没进）必须是两句话。 */
export function hitText(v: boolean | null | undefined): string {
  if (v === true) return "在";
  if (v === false) return "不在";
  return NULL_TEXT;
}

/** 这一格是 — 还是 `x.x%`。缺数绝不落到 0（0% 是一分钱没动）。 */
export function fracText(v: unknown): string {
  return typeof v === "number" && Number.isFinite(v) ? `${v.toFixed(1)}%` : NULL_TEXT;
}

/** 按接口交出的行数一遍：某列有几格是 —（**页面分布读数，不是判据**，措辞必须带「已交出行」）。 */
export function nullTally(rows: FollowRow[], key: keyof FollowRow): number {
  return (rows ?? []).filter(r => r[key] === null || r[key] === undefined).length;
}

/**
 * 「看的是哪几天」那一句：母体场次日与读至日都读接口，一个日期都不自己算。
 * 没有基准后第一场时老实说「今日涨跌这一栏整栏是 —」，不许说成 0 场或「跌无可跌」；
 * 近 5 日那一栏不依赖母体日（它看读至日往回 5 场），所以这一栏可能是 — 而那一栏有数。
 */
export function windowSentence(d: FollowData): string {
  const w = d.window ?? {};
  const base = `母体＝${d.parent_date ?? "—"} 那场稳步前进榜（窗口 ${w.first_day ?? "—"}~${w.last_day ?? "—"}，`
    + `共 ${w.sessions ?? "—"} 场 ${w.steps ?? "—"} 步）交出的前 ${d.limit ?? "—"} 名；`
    + `今日＝${w.next_day ?? "—"}（母体之后的第一场行情），近 5 日读到 ${w.to_date ?? d.date ?? "—"}（母体之后共 `
    + `${w.sessions_after ?? "读不到"} 场）`;
  if (w.next_day == null) return `${base}。库里母体场之后还没有下一场行情 —— 今日涨跌整栏是 —，不是 0%；近 5 日按它自己那 5 场算，两栏各读各的`;
  return base;
}

/**
 * 截断那句复用母体榜的同一份实现（`steadyRank.truncationSentence`）：本块的行数就是母体榜交出的行数，
 * 「符合判据共几只、接口交出前几名」两句话在两屏上必须是同一个算法。
 */
export function parentTruncationSentence(d: FollowData): string {
  return truncationSentence({
    total_matched: d.total_matched,
    limit: d.limit,
    rows: d.rows as SteadyData["rows"],
  } as SteadyData);
}
