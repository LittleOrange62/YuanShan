/** 常驻循环面板的纯判据层（单独一个文件是因为它要能被 `node --test` 直接驱动：
 * `closeChain.ts` 里有 class 与 fetch，类型擦除那一层跑不动）。 */

export const WEEK_DAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

/** 面板要的几格都在这里，全部可选：旧版后端没交时按"不知道"处理。 */
export interface LoopSwitchView {
  live_switch?: boolean | null;
  has_freq?: boolean | null;
  run_freq?: string | null;
  weekly_days?: string | null;
}

/** `weekly_days`（"0,2,4"，0=周一）→ 勾选状态。坏值不当空：界面要能说"读不懂"。 */
export function parseWeeklyDays(raw?: string | null): { days: number[]; bad: boolean } {
  const text = String(raw ?? "").trim();
  if (text === "") return { days: [], bad: false };
  const parts = text.split(",").map((p) => p.trim()).filter((p) => p !== "");
  const days = parts.map((p) => Number(p));
  const bad = days.some((d) => !Number.isInteger(d) || d < 0 || d > 6);
  return { days: bad ? [] : days, bad };
}

/** 这一行的开关**当场关不关得动** —— 逐条事实，不用统一口径。
 * `live_switch` 缺失（还没装载新读数的服务）时必须说"未知"，默认成 true 就是
 * 把最难查的那类假生效说成已经生效。 */
export function switchBadge(row: LoopSwitchView): { label: string; tone: "ok" | "warn" | "unknown" } {
  if (row.live_switch === true) return { label: "关掉即时生效", tone: "ok" };
  if (row.live_switch === false) return { label: "要重启才停得下来", tone: "warn" };
  return { label: "生效时机未知（这一版服务还没交这一格）", tone: "unknown" };
}

/** 选了每周却一个周几都没勾 ⇒ 宿主按"判不了就放行"天天跑，与用户选的完全相反。 */
export function freqProblem(row: LoopSwitchView): string | null {
  if (!row.has_freq || String(row.run_freq ?? "daily") !== "weekly") return null;
  const { days, bad } = parseWeeklyDays(row.weekly_days);
  if (bad) return "weekly_days 读不懂（要 0~6 用逗号分隔），现在按每天跑处理";
  return days.length ? null : "每周但没勾周几 ⇒ 现在实际是天天跑";
}
