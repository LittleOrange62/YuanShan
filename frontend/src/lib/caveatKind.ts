/**
 * caveat 的"能不能折"分类 —— 全站一处。
 *
 * 背景：用户 2026-10-21 点名「类似问题到处都是，把描述性内容全部收缩起来，避免过多占屏」。
 * 但 `caveats` 这一路混着两类性质完全不同的句子：
 *  · **口径解释**（「名次 1 = 第一」「象限只在 `oper_score` 那行上有值」「各表起点不同，短历史是 caveat
 *    不是缺数」）—— 占屏的主力，该折；
 *  · **库不健康**（「榜被上限截断」「这一场没读全」「查询失败」「有 N 行 `is_stale`」「配置读不到走了兜底」）
 *    —— 这类一折就等于让屏幕说假话，必须留在屏上（同「检测器坏了不许返回空值」那条纪律）。
 *
 * 两条同时生效的规矩（缺一都会在未来某一天判错）：
 *  ① **白名单制**：只有下面明确点过名的 code 才允许折。**没见过的 code 一律留在屏上** ——
 *     后端随时能加新 code，默认折叠会把"某天多出来的一条截断声明"静默藏掉；
 *  ② **再加一道模式闸**：就算某 code 在白名单里，只要它的名字带着"坏/缺/断/截"那一族（`truncat`、
 *     `empty`、`missing`、`unread`、`failed`、`stale`、`no_`、`partial` …），也不折。
 *     这样将来有人往白名单里塞错东西，闸还在第二道。
 *  ③ **同名两用的一律不折**（10-21 验收时逐条读后端文案才看出来）：光看 code 名字会放错。
 *     `short_window`／`thin_window`／`min_bars` 读起来像"窗口口径解释"，实际是后端**按当下这批**
 *     append 的（「这些票的可用 K 线不足 40 日…：000001、000002…」「这一批里有 N 行凑不满 10 根」），
 *     折起来就是把"屏幕上这几行的数不能和别的行横比"这句话藏掉；`axis_weight_drift`（与
 *     `config.view_effective_binding` 不一致）和 `group_series`（快照还没长够）同属这一类。
 *     ⇒ 入白名单的资格不是"名字像解释"，而是"这条 code 在后端只出现在 `_NOTE`/`_caveats` 那种
 *     固定散文里，不带着当场的计数"。
 *     最险的一对：`entry_quadrant_null`（`_NOTE` 里那句"为空不代表斜率算不出"，可折）与
 *     `window_entry_quadrant_null`（后端按本次行数 append 的"这 N 场的入口象限为空"，不可折）
 *     —— 只差一个 `window_`，白名单认的是全名，别顺手合并。
 */

/** 后端已经点过名的"纯口径解释"类 code（逐条对照过文案才放进来）。 */
export const FOLDABLE_CAVEAT_CODES: ReadonlySet<string> = new Set([
  'quadrant_design', 'entry_quadrant', 'entry_quadrant_null', 'zero_axis', 'sector_breadth',
  'heat_two_calibers', 'quadrant_null_share', 'rank_direction', 'env_rank_levels',
  'relative_not_absolute', 'y_axis_levels', 'coverage', 'three_state',
  'null_not_zero', 'one_per_stock', 'one_source', 'st_vs_strict', 'roe_kind', 'q4_coarser',
  'universe', 'note', 'vs_sector_card', 'adjusted_shape', 'bj_excluded',
  'local_only', 'index_assumed_zero', 'depth', 'prior_not_law', 'selection_bias',
  'future_function', 'board_link',
]);

/** 名字里带"库不健康"语义的那些词根：命中就**不许折**（第二道闸，也兜住"新 code 但名字已经说明问题"） */
const UNHEALTHY = /(truncat|empty|missing|unread|fail|reject|absent|stale|fallback|mismatch|unknown|error|not_computed|not_located|no_|_off$|partial|censored|gap|degraded|rejected|shifted|excluded_out)/;

/** `note` 是 `_caveats(extra=…)` 的通用兜底 code：后端今天拿它装口径散文，明天拿它装告警也不会另行通知。
 *  所以对这**一条** code 再按句子本身判一次（其余 code 已逐条读过后端文案，不必也不该套这句式闸——
 *  那会把 `coverage`「短历史是 caveat 不是缺数」这类正常解释也一并顶回屏上，默默把收缩白做一遍）。 */
const NOTE_UNSAFE_TEXT = /(失败|读不到|没读到|取不到|截断|凑不满|点数不足|不一致|超时|已陈旧|回退)/;

export function caveatFoldable(code?: string | null, text?: string | null): boolean {
  const c = (code ?? '').trim();
  if (c === '') return false;
  if (!FOLDABLE_CAVEAT_CODES.has(c) || UNHEALTHY.test(c)) return false;
  if (c === 'note' && NOTE_UNSAFE_TEXT.test(text ?? '')) return false;
  return true;
}

export interface CaveatLike {
  code?: string | null;
  /** 上屏的中文标签（真源 `duanxian/quant.py::_CAV_LABEL`）；分流只看 `code`，不看它 */
  label?: string | null;
  text?: string | null;
}

/** 拆成"留在屏上的"和"可以收进折叠块的"两份，各自保持后端给的原顺序。 */
export function splitCaveats(items: CaveatLike[] | null | undefined): {
  visible: CaveatLike[]; foldable: CaveatLike[];
} {
  const visible: CaveatLike[] = [];
  const foldable: CaveatLike[] = [];
  for (const c of items ?? []) {
    if (!c || !c.text) continue;
    (caveatFoldable(c.code, c.text) ? foldable : visible).push(c);
  }
  return { visible, foldable };
}
