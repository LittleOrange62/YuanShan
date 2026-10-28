

/** 涨（正向、强）用的文字色 */
export const UP_TEXT = "text-danger";
/** 跌（负向、弱）用的文字色 */
export const DOWN_TEXT = "text-success";
/** 平/无值 */
export const FLAT_TEXT = "text-muted-foreground";

/** 涨（正向）用的**背景**色 —— 条形图、色块都走它，别直接写 bg-success/bg-danger。
 *  ⚠️ 文字用 UP_TEXT、背景用 UP_BG，两者必须同向：曾经条形用绿表示正、
 *  而同一行的数字用红表示正，一行里两套配色打架，读的人得停下来想一秒。 */
export const UP_BG = "bg-danger";
/** 跌（负向）用的背景色 */
export const DOWN_BG = "bg-success";

export function barColor(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "bg-muted";
  return v > 0 ? UP_BG : v < 0 ? DOWN_BG : "bg-muted";
}

export function pctColor(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return FLAT_TEXT;
  return v > 0 ? UP_TEXT : v < 0 ? DOWN_TEXT : FLAT_TEXT;
}

/**
 * 涨停/跌停**家数**这类计数的颜色。方向是写死的，不看数值正负
 * （跌停 30 家，30 是正数，但它表达的是"跌"）。
 *
 * @param direction "up" = 涨停/晋级/收红一类；"down" = 跌停/跌超/大面一类
 */
export function countColor(direction: "up" | "down"): string {
  return direction === "up" ? UP_TEXT : DOWN_TEXT;
}

/**
 * 强弱类指标（走强/转弱、没炸过、延续率高低）的颜色。
 *
 * 严格说这不是"涨跌"，但中文语境里**强=红、弱=绿**是同一套色觉习惯，
 * 跟涨跌配色分开会让同一屏出现两种语义的红绿，更容易看错。
 */
export function strengthColor(strong: boolean | null | undefined): string {
  if (strong == null) return FLAT_TEXT;
  return strong ? UP_TEXT : DOWN_TEXT;
}

/**
 * quantdash 板块周期矩阵的四级热度配色（涨幅 ≥4 深红 / ≥2 红 / ≥0 浅红 / <0 绿）。
 *
 * 阈值是照搬 quantdash 的，但**配色方向必须由本文件反转**：A 股红涨绿跌，
 * 直接写 `bg-danger/bg-success` 会绕开这条约定，所以收在这里。
 *
 * ⚠️ 这是**纯涨幅**配色，不含资金流。任何用它上色的格子都要带这个提示，
 * 否则用户会把红色读成"资金流入"。
 */
export function heatBg(pct: number | null | undefined): string {
  if (pct == null || !Number.isFinite(pct)) return "bg-muted";
  if (pct >= 4) return UP_BG;
  if (pct >= 2) return `${UP_BG}/70`;
  if (pct >= 0) return `${UP_BG}/35`;
  return DOWN_BG;
}

/**
 * 「大小」用**同色系深浅**（用户 2026-10-15 选的口径：方向留给红绿，高低用深浅）。
 *
 * 为什么不用红橙黄绿青蓝紫：本项目里红＝涨、绿＝跌是全站共用的方向语义
 * （`UP_TEXT`/`DOWN_TEXT`），再拿同一套颜色表示"数值大小"，一屏里红色就有两个意思。
 * 所以这里只走 primary 的透明度阶梯 —— 它不碰方向色。
 *
 * ⚠️ 归一是**在本屏这几行之间**比（同一列的 min~max 五档），跨屏、跨日都不构成同一个深浅。
 * 用它上色的格子必须把这句话写在旁边，否则"深色"会被读成"强"这种绝对结论。
 * null / 非有限值 → 不涂色（不给 0 顶上）。
 */
export function heatText(v: number | null | undefined, lo: number, hi: number): string {
  if (v == null || !Number.isFinite(v) || !Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) {
    return FLAT_TEXT;
  }
  const t = (v - lo) / (hi - lo);            // 0~1，在本屏这一列里的位置
  if (t >= 0.8) return "text-primary font-semibold";
  if (t >= 0.6) return "text-primary/85";
  if (t >= 0.4) return "text-primary/65";
  if (t >= 0.2) return "text-primary/45";
  return "text-primary/30";
}

/**
 * 任务调度页（`/jobs`）的状态样式（契约 03 §6）。
 *
 * ⚠️ 这里**刻意不用 UP/DOWN 那套**：`success/danger` 在本项目里是涨跌 token，
 * 拿它们画调度状态会让同一个"红"在一屏里既表示"涨"又表示"故障"。
 * 故障走 `destructive`（UI 错误色）、可疑走 `warning`、停用走 `muted`。
 */
export function jobCardClass(tone: "normal" | "warn" | "bad" | "quiet"): string {
  if (tone === "bad") return "border-destructive/60";
  if (tone === "warn") return "border-warning/60";
  if (tone === "quiet") return "border-border/60 opacity-75";
  return "border-border";
}

export function jobLabelClass(tone: "normal" | "warn" | "bad" | "quiet"): string {
  if (tone === "bad") return "bg-destructive/15 text-destructive";
  if (tone === "warn") return "bg-warning/15 text-warning";
  if (tone === "quiet") return "bg-muted text-muted-foreground";
  return "bg-primary/12 text-primary";
}
