/**
 * 「节点分析」那一格的读数与措辞（**全站一份**：首板页与首页连板榜都调这里）。
 *
 * 判据住在 `app/compute/board_node.py`，读侧的口径原句与"这一场读不读得动"住在
 * `duanxian/node_view.py`；这里只负责把后端交出的 `hits` / `hit_reason` 短码翻成人话，
 * **不重新判断任何东西**。
 *
 * 一条红线：`—` 有三种来历（库里没这一行 / 这一场判不了 / 真没命中），
 * 屏幕必须分得开 —— 所以空串一律走 `titleOf` 里那句"没命中"以外的说明，
 * 并在格子旁边把整场的 `node_warn` 摆出来。
 */

export interface NodeDetail {
  boards?: number | null; run_first?: string | null; run_second?: string | null;
  sh_pct?: number | null; vol_vs_prev?: number | null;
  prev_max_boards?: number | null; prev_top_count?: number | null;
  prev_top_sealed?: number | null; prev_top_halted?: number | null; prev_top_broke?: number | null;
}
export interface NodeRow {
  node_hits?: string | null; node_reason?: string | null; node_detail?: NodeDetail | null;
}

/** 后端短码 → 人话。没有这条的一律照原样念出来（不猜、不编），并在前面标一句「短码」。 */
export const REASON_TEXT: Record<string, string> = {
  "n1:sh_pct_missing": "上证那天没读到涨跌幅 ⇒ 节点 1 判不了",
  "n1:sh_pct_not_down": "上证当天跌幅没到 1 个点",
  "n1:no_prev_volume": "前一交易日没有可比的那根量（首根日线或中间停牌）",
  "n1:vol_not_up": "当天量没大过前一交易日",
  "n1:first_board_only": "节点 1 只认首板，这一天不是首板",
  "n2:no_prev_day": "再往前没有交易日可对照",
  "n2:no_prev_top": "前一交易日没有可进梯队的票（全被剔除或没涨停）",
  "n2:prev_top_below_6": "前一交易日全场最高连板没到 6",
  "n2:prev_top_still_sealed": "那批最高板里还有票今天继续封住 ⇒ 高度没被清空",
  "n2:prev_top_state_unknown": "那批最高板里有票今天的状态判不了（上证也没有当天这根线）",
  "n2:not_1st_or_2nd": "节点 2 只认首板与二板（一进二），这一天不是",
  "run_window_broken": "本轮连板的起始日落在计算窗口之外 ⇒ 不猜，留 —",
  "run_days_no_hit": "本轮的首板日与二板日那天都没命中",
  "no_db_row": "这一场库里没有这只票的行",
  "not_computed": "判据表没算到这一天",
  "db_unavailable": "库里读不到这一场（不是没有命中）",
};

export function reasonText(raw?: string | null): string {
  const codes = String(raw ?? "").split(",").map(s => s.trim()).filter(Boolean);
  if (!codes.length) return "";
  return codes.map(c => REASON_TEXT[c] ?? `短码 ${c}（后端没给这句的中文说明，照原样念）`).join("；");
}

/** 格子里显示什么：只有 `1` / `2` / `1, 2` / `—` 四种，照后端原样，不四舍五入、不合并。 */
export function nodeText(row: NodeRow): string {
  const v = String(row.node_hits ?? "").trim();
  return v === "" ? "—" : v;
}

function pct(n?: number | null): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "没读到";
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}%`;
}
function num(n?: number | null): string {
  return n === null || n === undefined ? "没读到" : String(n);
}

/**
 * 悬停那一段：判据原句（后端那句，不改）+ 这一格的依据 + 没命中的原因。
 *
 * 三板及以上要说清"这一格不是当天判的"，否则读者会拿当天的大盘去核这一列。
 */
export function titleOf(row: NodeRow, rule?: string | null): string {
  const d = row.node_detail ?? null;
  const head = String(rule ?? "").trim() || "判据原文后端没交（node_rule 为空），这里不自己复述规则。";
  const parts: string[] = [head];
  const hits = String(row.node_hits ?? "").trim();
  const boards = d?.boards;
  if (typeof boards === "number" && boards >= 3) {
    parts.push(`这一天是第 ${boards} 板：按口径**不看当天**，这一格来自本轮首板日 ${d?.run_first || "?"}`
      + ` 与二板日 ${d?.run_second || "（本轮没走到二板）"} 那两天的并集。`);
  }
  if (d) {
    parts.push(`这一格的依据：上证当日 ${pct(d.sh_pct)}；当天量 ÷ 前一交易日量 `
      + `${d.vol_vs_prev === null || d.vol_vs_prev === undefined ? "判不了" : d.vol_vs_prev.toFixed(3)}；`
      + `前一交易日全场最高连板 ${num(d.prev_max_boards)} 板，站在那上的 ${num(d.prev_top_count)} 只里`
      + `今天仍封住 ${num(d.prev_top_sealed)}／断板 ${num(d.prev_top_broke)}／停牌 ${num(d.prev_top_halted)}。`);
  }
  if (!hits) {
    const why = reasonText(row.node_reason);
    parts.push(why ? `这一格是「—」：${why}` : "这一格是「—」，后端没给原因（hit_reason 为空）。");
  } else {
    parts.push(`命中：${hits}。这是当天所处的市场结构描述，不是买入信号。`);
  }
  return parts.join("\n");
}

/** 这一格该用什么颜色：命中才上色，`—` 一律灰（灰不等于"差"，只是没有结论）。 */
export function nodeTone(row: NodeRow): "hit" | "none" {
  return String(row.node_hits ?? "").trim() ? "hit" : "none";
}

/**
 * 整场那一句声明：后端 `node_warn` 非空就必须上屏，且**不许折进默认收起的说明框**。
 * 它是"这一列的 — 要怎么读"的唯一出处，页面不自己补一句"今天没命中"。
 */
export function warnText(warn?: string | null): string {
  return String(warn ?? "").trim();
}
