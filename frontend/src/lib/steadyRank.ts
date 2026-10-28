/**
 * 名次稳步前进榜的读侧判读 —— 用户 2026-10-18 点名：「展示这个 xy 名次近 6 个交易日稳步增长的个股，
 * **是名次**！可以手动切换成总分稳步增长的，可以选择。」
 *
 * 这一层零依赖、纯判读：判据（哪几场、前进几步算稳步、门槛多少）全部住在
 * `duanxian/quant.py::rank_momentum`，接口用 `rule` / `definitions` / `thresholds` / `caveats`
 * 交出来，界面只转述。这里写的每一个函数都只做两件事：把交出的数**读成一句人话**，
 * 或者**自检交出的数自己对不对得上**（对不上就说对不上，不许替它圆）。
 */

export type SteadyMetric = "xy" | "oper";

/** 行上每一个字段都是接口交出的原样（键名与后端 `definitions` 一一对应，页面不改名）。
 *  全部可空：接口哪天少交一格，界面就是 `—`，不是 0。 */
export interface SteadyRow {
  board_rank?: number | null;
  security_code?: string | null;
  name?: string | null;
  board?: string | null;
  sessions_used?: number | null;
  first_series?: number | null;
  latest_series?: number | null;
  gain_abs?: number | null;
  gain_frac?: number | null;
  better_steps?: number | null;
  worse_steps?: number | null;
  tied_steps?: number | null;
  steps_total?: number | null;
  biggest_step_gain?: number | null;
  first_x?: number | null;
  latest_x?: number | null;
  first_y?: number | null;
  latest_y?: number | null;
  series_path?: string | null;
  x_path?: string | null;
  y_path?: string | null;
  quadrant?: string | null;
  quadrant_label?: string | null;
  oper_value?: number | null;
  matched_total?: number | null;
}

export interface SteadyData {
  available?: boolean | null;
  reason?: string | null;
  date?: string | null;
  source?: string | null;
  metric?: string | null;
  metric_label?: string | null;
  series_key?: string | null;
  series_label?: string | null;
  window?: { sessions?: number | null; fixed?: number | null; steps?: number | null;
             first_day?: string | null; last_day?: string | null;
             is_trading_days?: boolean | null } | null;
  thresholds?: { min_better_steps?: number | null; max_worse_steps?: number | null;
                 min_gain_frac_pct?: number | null; require_both_axes?: boolean | null } | null;
  universe?: { per_session_min?: number | null; per_session_max?: number | null;
               y_levels_min?: number | null; y_levels_max?: number | null;
               x_levels_min?: number | null; x_levels_max?: number | null;
               biggest_y_tie?: number | null; stale_rows?: number | null;
               covered_codes?: number | null; note?: string | null } | null;
  limit?: number | null;
  total_matched?: number | null;
  rows?: SteadyRow[] | null;
  rule?: string | null;
  definitions?: Record<string, string> | null;
  quadrant_labels?: Record<string, string> | null;
  labels_source?: string | null;
  coverage?: Record<string, { from?: string | null; to?: string | null; sessions?: number | null }> | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
}

/** 界面上的两个按钮。**只有名字是页面写的**，两档能问什么由后端 `STEADY_METRICS` 定，
 *  传别的值那边会 `_reject`（不悄悄换成 xy 代答）。 */
export const METRIC_BUTTONS: { key: SteadyMetric; label: string }[] = [
  { key: "xy", label: "xy 名次" },
  { key: "oper", label: "总分名次" },
];

/** 取数上限的三档（后端 `limit` 收 5~200）。榜是按幅度排的，上限决定"看得多深"。 */
export const LIMIT_OPTIONS = [50, 80, 150];

/** 路径串（`3454→1049→…`）拆成数字；有一个拆不出来就整体交 null，不拿半截当真数。 */
export function pathNumbers(path?: string | null): number[] | null {
  const raw = (path ?? "").trim();
  if (raw === "") return null;
  const parts = raw.split("→");
  const out: number[] = [];
  for (const p of parts) {
    const n = Number(p.trim());
    if (!Number.isFinite(n)) return null;
    out.push(n);
  }
  return out;
}

/** 三条路径里哪几条的段数不等于场数。
 *  路径与步数是同一次聚合数出来的，段数不对就是接口自己前后不一致 —— 屏幕上要说"对不上"，
 *  不能继续按"6 场"念（本项目犯过的反倒是拿注释里的常数当现值）。 */
export function pathMismatch(row: SteadyRow, sessions: number | null | undefined): string[] {
  if (typeof sessions !== "number" || sessions <= 0) return [];
  const pairs: [string, string | null | undefined][] = [
    ["名次路径", row.series_path], ["X 轴路径", row.x_path], ["Y 轴路径", row.y_path],
  ];
  const bad: string[] = [];
  for (const [label, path] of pairs) {
    const nums = pathNumbers(path);
    if (nums !== null && nums.length !== sessions) bad.push(`${label} ${nums.length} 段`);
  }
  return bad;
}

/** 这一行的步数读法（「5 步里前进 4 步、后退 1 步、持平 0 步」）。
 *  三个数有一个没交就说"没交"，不按 0 补 —— 0 步后退与"不知道有没有后退"是两句不同的话。 */
export function stepsSentence(row: SteadyRow, steps: number | null | undefined): string {
  const b = row.better_steps, w = row.worse_steps, t = row.tied_steps;
  if (typeof b !== "number" || typeof w !== "number" || typeof t !== "number") {
    return "步数这一格接口没交，这一行读不出稳不稳";
  }
  const head = typeof steps === "number" ? `共 ${steps} 步里` : "";
  const sum = b + w + t;
  const tail = typeof steps === "number" && sum !== steps
    ? `（三者相加 ${sum}，与场数-1 的 ${steps} 对不上）` : "";
  return `${head}前进 ${b} 步、后退 ${w} 步、持平 ${t} 步${tail}`;
}

/** xy 档的自检：有没有哪一行其实有一轴在退。
 *  判据 ⑤ 写在 SQL 的 where 上，理论上是 0；这里是**万一漏了要当场说出来**的那道闸，
 *  不是拿来"再筛一遍"的 —— 前端筛就变成"接口给的榜 + 页面自己的榜"两张。 */
export function axisRegression(d: SteadyData): number | null {
  if (d.metric !== "xy") return null;
  let n = 0;
  for (const r of d.rows ?? []) {
    const xBack = typeof r.first_x === "number" && typeof r.latest_x === "number" && r.latest_x >= r.first_x;
    const yBack = typeof r.first_y === "number" && typeof r.latest_y === "number" && r.latest_y >= r.first_y;
    if (xBack || yBack) n += 1;
  }
  return n;
}

/** 榜被上限截断这件事怎么说。**行数不等于"全场稳步前进的票数"** —— 不说这句，
 *  80 行会被读成"只有 80 只在稳步前进"，而接口明明交了 `total_matched`。 */
export function truncationSentence(d: SteadyData): string {
  const matched = d.total_matched, rows = (d.rows ?? []).length, lim = d.limit;
  if (typeof matched !== "number") {
    return `接口没交「符合判据共几只」这个数，这一屏有几只说不清（现在是 ${rows} 行）`;
  }
  if (matched === 0) return "符合判据的票是 0 只 —— 为什么是 0 只见上面那句接口原话";
  if (rows < matched) {
    return `符合判据的共 ${matched} 只，接口按前 ${lim ?? "—"} 名交出 ${rows} 行 —— `
      + "榜被上限截断过，这一屏的行数不等于「全场稳步前进的票数」；"
      + "调大「前 N」才能看更深，翻页不会挖出新的票";
  }
  return `符合判据的 ${matched} 只全部交出（${rows} 行），没有截断`;
}

/** 窗口那一行：哪几天、共几场、几档母体。数字全是接口交的，这里只把它们连成一句。 */
export function windowSentence(d: SteadyData): string {
  const w = d.window ?? {}, u = d.universe ?? {};
  const span = `${w.first_day ?? "—"} ~ ${w.last_day ?? "—"}`;
  return `截至 ${d.date ?? "—"} 的最近 ${w.sessions ?? "—"} 个交易日（${span}，共 ${w.steps ?? "—"} 步）；`
    + `每场有名次的票 ${u.per_session_min ?? "—"}~${u.per_session_max ?? "—"} 只。`
    + (w.is_trading_days ? "「近 N 天」按交易日数，长假里日历日会更多。" : "");
}

/** 榜内中位前进幅度 —— 用来回答"第一名 94% 是异常值还是一整批都这样"。
 *  这是**本页自己按已交出的行算的一个读数**，不是判据：判据那套住在接口，
 *  这里只数已交出这几行的分布，且行数变化时这个数也跟着变（所以措辞带"已交出的这几行"）。 */
export function medianGainPct(rows: SteadyRow[]): number | null {
  const v = rows.map(r => r.gain_frac).filter((x): x is number => typeof x === "number").sort((a, b) => a - b);
  if (v.length === 0) return null;
  const mid = Math.floor(v.length / 2);
  return v.length % 2 === 1 ? v[mid] : (v[mid - 1] + v[mid]) / 2;
}

/** 名字列里"名字就是代码本身"的行 —— 和启动前量能那一屏同一个道理：
 *  读者会以为页面截断了，而真实情况是股票字典里没登记中文名。 */
export function nameIsCode(rows: SteadyRow[]): string[] {
  return rows.filter(r => {
    const n = String(r.name ?? "").trim();
    return n === "" || n.toUpperCase() === String(r.security_code ?? "").toUpperCase();
  }).map(r => String(r.security_code));
}
