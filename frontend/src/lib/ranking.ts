import { NULL_TEXT } from "./datastate.ts";

/**
 * 「名次」与「这一格的小数怎么念」在前端只准有一处实现
 * （2026-10-19 第二稿；与后端 `duanxian/ranking.py` 同一条纪律）。
 *
 * 为什么单开一份：名次看数字看不出对错。每屏各写一句规则、各自给缺分补一个 0，
 * 就会出现"并列到底跳不跳号""没分算不算最后一名"的好几种答案，而界面全长得一样。
 *
 * 三条线都收在这里：
 *  1. **规则原文一律读接口**（`rank.rule` / `rank_rule` / `rank_rule_short`）。
 *     那句话只住后端一处，组件里抄一份早晚走偏 —— 所以这里只把它转述出来，不自造。
 *  2. **没有分就没有名次**：行上的名次是 null 时格子念 —，不是 0。
 *     把"没数据"念成"最差"是本项目反复被教育过的那一条。
 *  3. **分母要说清是哪一批**：同一个"第 40 名"，在"当日有分的那 5,514 只"里
 *     与在"取前 120 名"里是两句话，所以悬停同时给出规则与分母。
 *
 * 另外把 `trimNum` 也放这儿（同一条纪律的另一半）：加权分、分位、上限这几列以前是
 * `toFixed(4)` 直出，屏幕上就是 `1.000000 / 40.0000 / 4.2100`。第二稿的分值就是"一天计几分"，
 * 尾随零没有任何信息量，所以显示层统一去尾零；**数值本身仍按后端原样交回，这里只动显示**。
 *
 * 零 IO import（只碰 `datastate` 那个常量），所以 `node --test` 能直接把这几个函数拿来跑：
 * `lib/api.ts` 那种带构造函数参数属性的模块，node 的类型直 strip 不动它。
 */

/** 接口 `rank.universe`：名次这一把尺的分母是哪一批。缺字段就是没读到，不默认成 0。 */
export interface RankUniverse {
  /** 有分的只数（＝参与排名的母体） */
  scored?: number | null;
  /** 这一场库里的总行数（含没有分的那些） */
  rows_total?: number | null;
  /** 没有分的只数（不参与排名） */
  unscored?: number | null;
  /** 最高分（第 1 名那一档的分数） */
  top_score?: number | null;
}

/** 接口没交规则原文时的那句占位：这一列的名次规则就没有出处，页面不自己编一句。 */
export const NO_RANK_RULE_TEXT = "名次规则的原文后端没交（接口 rank.rule 是空的）—— 页面不自己编一句规则";

/** 接口没交分母时的那句占位：不许把"没读到"说成"0 只参与排名"。 */
export const NO_UNIVERSE_TEXT = "分母是哪一批：没读到（不是 0 只）";

/** 千分位只给"只数/行数"这种计数用（名次本身不加，加了反而读不出第几名）。 */
function groupInt(v: number): string {
  const int = Math.trunc(Math.abs(v)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return (v < 0 ? "-" : "") + int;
}

function finite(v: unknown): number | null {
  if (typeof v !== "number" || !Number.isFinite(v)) return null;
  return v;
}

/**
 * 名次那一格的文本：null／没数 → —（**不是 0，也不是"最后一名"**）。
 *
 * 名次是整数，这里只把它念出来：后端交回什么就念什么（0 万一真交来也是读数，不替它改口），
 * 但"没有名次"与"名次是 0"在屏幕上必须长得不一样，所以缺数一律走 —。
 */
export function rankText(rank: unknown): string {
  const n = finite(rank);
  if (n === null) return NULL_TEXT;
  return String(Math.trunc(n));
}

/**
 * 名次那一格／那一列的悬停：**规则原文 ＋ 分母是哪一批**，两句拼在一起。
 *
 * @param ruleText 接口 `rank.rule`（或配置卡的 `rank_rule`、列头那种放不下的 `rank_rule_short`）。
 *                 组件不许自己写这句话，也不许在这里塞默认文案。
 * @param universe 接口 `rank.universe`；列头那种没有"这一格"的场合不传 rank。
 * @param rank     这一格自己的名次，给了就在末尾补一句「当日 X 只有分里的第 Y 名」。
 */
export function rankTitle(ruleTextArg?: string | null, universe?: RankUniverse | null, rank?: unknown): string {
  const rule = typeof ruleTextArg === "string" && ruleTextArg.trim() !== "" ? ruleTextArg.trim() : NO_RANK_RULE_TEXT;
  const scored = finite(universe?.scored);
  const rowsTotal = finite(universe?.rows_total);
  const unscored = finite(universe?.unscored);
  const parts: string[] = [rule];

  if (scored === null) {
    parts.push(NO_UNIVERSE_TEXT);
  } else {
    let denom = `分母＝当日有分的 ${groupInt(scored)} 只`;
    if (rowsTotal !== null) denom += `（这一场库里一共 ${groupInt(rowsTotal)} 行）`;
    if (unscored !== null) denom += `，没有分的 ${groupInt(unscored)} 只不在这把尺上`;
    parts.push(denom);
  }

  const own = finite(rank);
  if (own !== null) {
    parts.push(scored === null
      ? `这一格是第 ${rankText(own)} 名（分母没读到）`
      : `这一格＝当日 ${groupInt(scored)} 只有分里的第 ${rankText(own)} 名`);
  } else if (rank === null) {
    // 格子那一侧显式交了 null（这一行没有得分）：要说"这一格没有名次"，
    // 而参数整个没给（列头那种）就不提这一格，免得列头悬停冒出一句和列头无关的话。
    parts.push("这一格没有名次：这一行没有得分（这一格是 —，不是 0）");
  }
  return parts.join("｜");
}

/**
 * 小数显示统一去尾零：`4.2100→4.21`、`1.000000→1`、`40.0→40`。
 *
 * 只动显示层：后端交回的还是那个数，这里不改精度、不四舍五入到"整数感觉"（`digits` 给到几就几位）。
 * 缺数（null／undefined／NaN／Infinity）一律 —，**绝不落到 0**：0 是一条实质读数（"一分没得"），
 * 与"没数"是两件事。真 0 照常念成 `0`。
 */
export function trimNum(v: unknown, digits: number = 4): string {
  const n = finite(v);
  if (n === null) return NULL_TEXT;
  const d = finite(digits) ?? 4;
  const places = Math.max(0, Math.min(12, Math.trunc(d)));
  let s = n.toFixed(places);
  if (s.includes(".")) s = s.replace(/0+$/, "").replace(/\.$/, "");
  return s === "-0" ? "0" : s;
}

/**
 * 「八项能力」那一屏的名次格子（2026-10-22 用户点名：`market_mood -46.2 分 全市场第 1（前 50.0%`
 * 怎么就第 1 了）。判据与那句解释**只住后端 `duanxian/quant.py::_rank_view`**，
 * 这里只把后端交出的 `rank_kind`／`rank_note`／分布计数转成格子里的几段文字。
 *
 * 页面**不许**自己判"几个取值算粗"、也不许自己写"这项全场同一个值"那句话 ——
 * 那些是判据，写第二遍就会分叉。
 */
export interface ScoreRankShape {
  rank_kind?: string | null;
  rank_note?: string | null;
  rank_in_market?: number | null;
  percent_rank?: number | null;
  universe?: number | null;
  distinct_values?: number | null;
  tie_at_value?: number | null;
  biggest_tie?: number | null;
}
export interface ScoreRankCell {
  /** 「全市场第 210」／「名次 —」 */
  head: string;
  /** 「前 12.3%」；后端判不了时是空串（格子不提这一项） */
  pct: string;
  /** 并列只数、档位个数、分母这类计数（按后端给的数念，一个都不自己算） */
  extras: string[];
  /** 悬停全文：后端那句 `rank_note` 打头 */
  title: string;
  /** 名次这一格不可信／没有横截面意义 ⇒ 用告警色 */
  warn: boolean;
}

export function scoreRankCell(c: ScoreRankShape | null | undefined): ScoreRankCell | null {
  if (!c) return null;
  const kind = typeof c.rank_kind === "string" ? c.rank_kind : "";
  const rank = finite(c.rank_in_market);
  const pctN = finite(c.percent_rank);
  const scored = finite(c.universe);
  const tie = finite(c.tie_at_value);
  const dv = finite(c.distinct_values);

  const extras: string[] = [];
  if (scored !== null) extras.push(`${groupInt(scored)} 只有分`);
  if (tie !== null && tie > 1) extras.push(`并列 ${groupInt(tie)} 只`);
  if (kind === "coarse" && dv !== null) extras.push(`这项全场 ${groupInt(dv)} 个取值`);

  const note = typeof c.rank_note === "string" && c.rank_note.trim() !== ""
    ? c.rank_note.trim()
    : "后端没交这一项的分布判据（rank_note 是空的）：名次照原样念，但没有对照过并列只数";
  return {
    head: rank === null ? `名次 ${NULL_TEXT}` : `全市场第 ${rankText(rank)}`,
    pct: pctN === null ? "" : `前 ${trimNum(100 - pctN, 1)}%`,
    extras,
    title: [note, kind === "" ? "rank_kind 缺字段" : `rank_kind=${kind}`].join("｜"),
    warn: rank === null || kind === "degenerate" || kind === "unknown" || kind === "",
  };
}
