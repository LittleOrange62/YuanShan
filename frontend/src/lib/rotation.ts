/**
 * 份额轮动榜那一屏的**纯读数层**（零 import，能被 `node --test` 钉住）。
 *
 * 收在这里是因为这几个格式化件件都踩过坑：
 *  · 份额本身是"百分之几"，累计是"百分点差"，两者都写成 `%` 就分不出哪个是量、哪个是变化；
 *  · `cum_N` 必须和 `days_N` 同屏 —— 攒不满窗口时那是「前 6 场的差」，与「10 场的差」不可比
 *    （DDL 的列注释专门写了这条，页面漏掉就等于把两个不可比的数摆进同一列）；
 *  · 空值给 `—`，0 是有效值：份额可以真的是 0，连续场次可以是 0（表示"不连续"），
 *    用 `if (v)` 判空会把它们一起吞掉。
 */

export const DASH = "—";

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

export function fmtShare(v: unknown, digits = 3): string {
  const n = num(v);
  return n === null ? DASH : `${(n * 100).toFixed(digits)}%`;
}

/** 份额差：单位写死 pp（百分点），不与"份额是百分之几"共用一个 `%` */
export function fmtPp(v: unknown, digits = 2): string {
  const n = num(v);
  if (n === null) return DASH;
  return `${n > 0 ? "+" : ""}${(n * 100).toFixed(digits)}pp`;
}

export function fmtBps(v: unknown): string {
  const n = num(v);
  if (n === null) return DASH;
  return `${n > 0 ? "+" : ""}${n.toFixed(1)}bp`;
}

/** 累计值 + 实有场次。窗口攒不满时把"只几场"跟着数一起给，不另起一列（另起一列就有人只读前一列）。 */
export function cumCell(value: unknown, days: unknown, window: number): string {
  const shown = fmtPp(value);
  if (shown === DASH) return DASH;
  const d = num(days);
  return d === null || d >= window ? shown : `${shown}（只 ${d} 场）`;
}

/* ---------------------------------------------------------------- 两把尺子的量纲（2026-10-12）
 *
 * `basis=share` 的 `value` 是**份额小数**（0.004 = 0.4%），`cum_N` 是**份额差**（0.0002 = 0.02pp）；
 * `basis=turnover` 的 `value` 已经是**百分数本身**（4.0 = 4% 当日换手率），`cum_N` 已经是**百分点**
 * （0.9 = 0.9pp）。同一套 `fmtShare/fmtPp` 都自带 ×100，直接拿去渲染换手那一档就会显示成
 * "400.000%" 与 "+90.00pp" —— 数没算错，但屏幕在说谎，而且说得非常像真的。
 * 所以量纲分支只住在这里这一处，界面按 basis 调，不许在组件里各自乘一遍。
 */

export type RotationBasis = "share" | "turnover";

/** 当日度量：份额走 ×100 的老路，换手已经是百分数、只补百分号 */
export function valueCell(v: unknown, basis: RotationBasis = "share"): string {
  const n = num(v);
  if (n === null) return DASH;
  return basis === "turnover" ? `${n.toFixed(2)}%` : fmtShare(n);
}

/** 累计位移：换手口径的单位本来就是 pp，不再乘 100 */
export function cumValue(v: unknown, basis: RotationBasis = "share"): string {
  const n = num(v);
  if (n === null) return DASH;
  if (basis !== "turnover") return fmtPp(n);
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}pp`;
}

/** 累计 + 实有场次（换手版；"只几场"那条规矩与份额口径完全一致，不能因为换了尺子就丢） */
export function cumCellB(value: unknown, days: unknown, window: number,
                        basis: RotationBasis = "share"): string {
  const shown = cumValue(value, basis);
  if (shown === DASH) return DASH;
  const d = num(days);
  return d === null || d >= window ? shown : `${shown}（只 ${d} 场）`;
}

/** 列名跟着尺子走：同一列在两条口径下读的是两件不同的事，写一个名字就是让读者猜 */
export function valueColumnLabel(basis: RotationBasis): string {
  return basis === "turnover" ? "当日换手率" : "今日份额";
}

/**
 * 连续场次怎么念。
 * ⚠️ 这里**不出现箭头、不出现"吸金/抢筹"这类词**：实测份额连续 n 场抬升后
 * 下一场仍抬升的概率从 46% 单调掉到 31%（基线 46%），是均值回复 ——
 * 一个向上的箭头就会被读成"还会继续"，那正是这张榜最不该说的话。
 */
export function streakText(up: unknown, dn: unknown): string {
  const u = num(up), d = num(dn);
  if (u !== null && u > 0) return `抬升 ${u} 场`;
  if (d !== null && d > 0) return `回落 ${d} 场`;
  return "不连续";
}

/**
 * 某一侧的连续场次（正数；这一侧没连着就是 0）。两端的榜**默认按各自那一侧排**
 * （用户 2026-10-08：抬升端按连续抬升、回落端按连续回落，都从多到少）。
 *
 * 为什么不是一列通用的"连续键"：早先这里有一个把抬升记正、回落记负的合并键，
 * 拿它排回落端会得到**反的**结果（降序时 −2 排在 −9 前面 = 回落得最少的排最前）。
 * 判据仍与 `streakText` 同源，只是各取一侧；`side` 传错当场就红，不会静默给个反序榜。
 */
export function sideStreak(up: unknown, dn: unknown, side: "up" | "dn"): number {
  const v = num(side === "up" ? up : dn);
  return v !== null && v > 0 ? v : 0;
}
