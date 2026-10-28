import { apiUrl } from "./base";
import { authHeaders } from "./api";
import { safeArray } from "./agent";

/**
 * quantdash 口径轨（PG 新 schema `qd`）的取数层与显示规则。
 *
 * 契约出处：`页面展示工作指南/02-quantdash逻辑双轨展示.md` §8（接口形状）、
 * §1–§7（显示规则里与取数相关的部分）。算法侧契约在
 * `数据加工工作指南/09-quantdash逻辑规格.md`。
 *
 * 三条永不违反的规则（都在这里收口，组件不再各自判断）：
 * 1. `own`（本地口径）与 `qd`（quantdash 口径）**永不合并**；`own === null`
 *    表示本轨没有这个能力，只能显示"本轨暂无"，绝不允许拿 qd 的值顶上。
 * 2. 空值一律渲染成 `—`（见 `dash`），**不许渲染成 0 / 空串 / "无"** ——
 *    `0` 会被下游 AI 当成真实数值继续推理。
 * 3. 取数失败就是失败：`{available:false, reason}` 原样显示，
 *    **不许回落到"今天"、也不许拿上一个有数据的交易日冒充**。
 */

/** 02 §8：`deviation[]` —— 我们与 quantdash 原实现哪里不一样，必须原样透出可展开 */
export interface Deviation {
  code?: string;
  note?: string;
}

export interface QdMetrics {
  [key: string]: unknown;
}

/** 02 §2：qd 轨情绪周期。三个 stage 列并存是事实，不是 bug */
export interface QdSentiment {
  stage_ui?: string | null;
  stage_mcp?: string | null;
  stage_feishu?: string | null;
  /** 契约只说"数组，≤3 条中文"，没规定怎么标归属；见 stageReasons() 的兼容策略 */
  stage_reasons?: unknown;
  /** ⚠️ 字段名叫 confidence，但它是查表加和（阶段基数+风险加成+量能加成，上限 89），
   *  不是统计置信度。**界面上必须显示为「判定强度」**（02 §2 第 4 条）。 */
  confidence?: number | null;
  risk_level?: string | null;
  volume_state?: string | null;
  metrics?: QdMetrics;
  deviation?: Deviation[];
}

/** 本轨情绪周期。02 §2 第 3 条：本轨目前没有阶段算法，所以通常是 null */
export interface OwnSentiment {
  [key: string]: unknown;
}

/**
 * 后端在 2026-09-25 起给 qd 轨端点加了"这屏是谁算的"信封（原先只有 `source`/`spec_version`）。
 * 只登记字段、不做推断：没有 `computed_by` 就是没有，不默认成本项目算的。
 */
export interface QdEnvelope {
  computed_by?: string | null;
  source?: string | null;
  spec_version?: string | null;
  scope?: string | null;
  universe?: string | null;
}

export interface SentimentCycle extends QdEnvelope {
  available: true;
  date?: string | null;
  own?: OwnSentiment | null;
  qd?: QdSentiment | null;
}

/** 一句人话：让用户分得清这张卡上的数是本项目算的、还是读对照基准表读来的 */
export function computedNote(env?: QdEnvelope | null): string {
  if (!env) return "";
  const raw = typeof env.computed_by === "string" ? env.computed_by.trim() : "";
  if (!raw) return "后端未标注 computed_by —— 无法判断这屏的数是本项目算的还是读表得来的";
  if (raw === "yuanshan") return "本屏数值由本项目计算（PG 的 qd 表只作对照基准与兜底）";
  if (raw === "qd_table" || raw === "pg") return "本屏数值读自对照基准表（未走本项目实算）";
  return `后端标注 computed_by=${raw}（未在本项目登记过这个取值，原样显示）`;
}

/**
 * 02 §3：天梯成员卡片。字段名以 2026-09-24 实查的 `qd.board_ladder_member` 为准
 * （`security_code` / `stock_name`），同时保留 `code` / `name` 两个别名 ——
 * 只读端点若做重命名，两种都能接上，但这不是猜测：两套都是明确命名的列。
 */
export interface LadderMember {
  security_code?: string | null;
  stock_name?: string | null;
  code?: string | null;
  name?: string | null;
  board_count?: number | null;
  pct_change_actual?: number | null;
  limit_up_pct_regime?: number | null;
  is_one_word?: boolean | null;
  /** 本轨扩展字段，quantdash 没有 —— 角标必须注明不是它的口径（02 §3） */
  is_turnover_board?: boolean | null;
  turnover_rate_pct?: number | null;
  first_seal_time?: string | null;
  /** DDL 里这一列是**逐行**的，比屏级更准：行内给了就以行内为准 */
  seal_time_available?: boolean | null;
  industry_name?: string | null;
  concept_name?: string | null;
}

export interface LadderTier {
  board_tier?: number | null;
  /** DDL 列名 tier_label；label 是端点重命名后的别名 */
  tier_label?: string | null;
  label?: string | null;
  stock_count?: number | null;
  is_highest_tier?: boolean | null;
  members?: LadderMember[];
}

/** 02 §4：龙头是怎么选出来的，四种 tie-break 各有文案 */
export type LeaderTieBreak =
  | "seal_time"
  | "amount_no_seal_time"
  | "code_asc"
  | "no_candidate";

export interface LadderLeader {
  leader_security_code?: string | null;
  leader_stock_name?: string | null;
  leader_board_count?: number | null;
  code?: string | null;
  name?: string | null;
  board_count?: number | null;
  leader_tie_break?: LeaderTieBreak | string | null;
  leader_status_label?: string | null;
  leader_status_code?: string | null;
  leader_next_open_pct?: number | null;
  leader_next_close_pct?: number | null;
}

export interface BoardLadder extends QdEnvelope {
  available: true;
  date?: string | null;
  tiers?: LadderTier[];
  leader?: LadderLeader | null;
  /** 02 §3：这一屏最重要的字段。false 时封板时间列必须灰底 + 顶部横幅 */
  seal_time_available?: boolean;
  deviation?: Deviation[];
}

/**
 * 轮动矩阵的一格（02 §5）。字段名对齐 2026-09-24 实查的 `qd.sector_rotation`：
 * `rotation: [{trade_date, items: [{sector_name, pct_change, day_rank}]}]`
 * `strength_delta_pct` **不在这一层**（它在 `qd.sector_persistence`，一天一个值），
 * 所以"新晋领涨"的标注归到持续性那块，不在格子里猜。
 */
export interface RotationCell {
  sector_name?: string | null;
  name?: string | null;
  pct_change?: number | null;
  day_rank?: number | null;
  up_count?: number | null;
  down_count?: number | null;
}

export interface RotationRow {
  trade_date?: string | null;
  items?: RotationCell[];
}

export interface SectorPersistence {
  current_leader_name?: string | null;
  current_leader_code?: string | null;
  streak_days?: number | null;
  top_three_appearances?: number | null;
  /** NULL 的语义是"新晋领涨"（quantdash 原 UI 语义），不是缺数据 */
  strength_delta_pct?: number | null;
  strongest_repeat_name?: string | null;
  window_days?: number | null;
  window_start?: string | null;
}

/**
 * qd 轨板块周期。**这一端点只返回 qd 轨**：本轨走同一项目内的
 * `/api/market/sector-cycle`（见 `fetchOwnSectorCycle`），一处数据只留一个入口
 * —— 2026-09-24 定，所以这里不声明 `own` 键。
 */
export interface SectorCycle extends QdEnvelope {
  available: true;
  date?: string | null;
  rotation?: RotationRow[];
  persistence?: SectorPersistence | null;
  /** 分母必须随数据交出（01 规范第三条） */
  scope?: string | null;
  deviation?: Deviation[];
}

/** 前三甲里某一场的那一格：名次与当日涨幅，缺行时 `rank` 为 null（界面空着，不是 0） */
export interface FollowCell {
  trade_date?: string | null;
  rank?: number | null;
  pct_change?: number | null;
}

/** 一个"轨迹与某领涨板块错开两场同形"的候选板块 */
export interface SimilarSector {
  sector_code: string;
  sector_name?: string | null;
  sector_type?: string | null;
  /** 候选窗口第一场（＝当日）的名次 */
  current_rank?: number | null;
  gap_mean_rank?: number | null;
  gap_pct_of_universe?: number | null;
  rank_corr?: number | null;
  candidate_ranks?: (number | null)[];
  template_ranks?: (number | null)[];
}

export interface FollowBlock {
  items: SimilarSector[];
  template?: (number | null)[];
  template_days?: string[];
  candidate_days?: string[];
  /** 实际参与比对的板块数（分母，必须随数据交出） */
  matched?: number | null;
  skipped_incomplete?: number | null;
  incomplete_template?: boolean;
}

export interface LeaderFollow {
  day_rank: number;
  sector_code: string;
  sector_name?: string | null;
  sector_type?: string | null;
  pct_change?: number | null;
  universe_size?: number | null;
  trajectory?: FollowCell[];
  similar?: FollowBlock;
}

/**
 * 前三甲 + 前五场名次轨迹 + 错开两场的相似板块（`/api/qd/sector-follow`）。
 * 判据只住 `duanxian/qd_follow.py` 一份，前端**不重算名次、不算相似**。
 */
export interface SectorFollow extends QdEnvelope {
  available: true;
  date?: string | null;
  lag?: number | null;
  universe_size?: number | null;
  /** 三个榜各自的比对数与跳过数在后端加好（前端不做加法，免得长出第二份实现） */
  match_total?: number | null;
  skip_total?: number | null;
  sessions?: string[];
  leaders?: LeaderFollow[];
  calibers?: Record<string, string>;
  caveats?: { code?: string; label?: string; note?: string }[];
  trajectory_points?: number | null;
  shape_points?: number | null;
  k_similar?: number | null;
  generated_at?: string | null;
  cache_age_seconds?: number | null;
  cached?: boolean;
}

/** `available:false` 分支。`reason` 缺失本身就是契约违规，要让它可见 */
export interface QdUnavailable {
  available: false;
  date?: string | null;
  reason?: string | null;
}

export type QdPayload<T> = (T & { available: true }) | QdUnavailable;

/** 取数状态。`loading` / `error` 与"数据确实没有"是三种不同状态，不合并显示 */
export type QdState<T> =
  | { kind: "loading" }
  | { kind: "error"; reason: string }
  | { kind: "ok"; payload: QdPayload<T> };

/**
 * 统一 GET。把"接口还没上线"与"后端说没有"分开报，前者是排障信息、后者是事实。
 * 不缓存、不做任何兜底值。
 */
async function qdGet<T>(path: string, query: Record<string, string | undefined>): Promise<QdState<T>> {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v) search.set(k, v);
  const qs = search.toString();
  const url = apiUrl(`/api${path}${qs ? `?${qs}` : ""}`);
  let resp: Response;
  try {
    resp = await fetch(url, { headers: authHeaders() });
  } catch {
    return { kind: "error", reason: "连不到后端（127.0.0.1:8910）。先启动服务再看这一屏。" };
  }
  let body: unknown = null;
  try {
    body = await resp.json();
  } catch {
    /* 非 JSON：下面按状态码归类，不猜内容 */
  }
  if (resp.status === 404) {
    return { kind: "error", reason: `接口未提供（HTTP 404：${path}）。本地计算尚未覆盖该项，所以这里没有数。` };
  }
  if (!resp.ok) {
    const detail = (body as { detail?: string; error?: string } | null)?.detail
      || (body as { error?: string } | null)?.error;
    return { kind: "error", reason: detail || `HTTP ${resp.status}` };
  }
  const record = (body as { data?: unknown } | null)?.data ?? body;
  if (!record || typeof record !== "object") {
    return { kind: "error", reason: "后端返回的形状不是对象（契约要求 {available, …}），已按不可用处理，不做任何猜测性渲染。" };
  }
  return { kind: "ok", payload: record as QdPayload<T> };
}

export const fetchSentimentCycle = (date?: string) =>
  qdGet<SentimentCycle>("/qd/sentiment-cycle", { date });
export const fetchBoardLadder = (date?: string) =>
  qdGet<BoardLadder>("/qd/board-ladder", { date });
export const fetchSectorCycle = (date?: string) =>
  qdGet<SectorCycle>("/qd/sector-cycle", { date });
/** 前三甲与相似板块。`lag` 不传就走后端默认（错开两场，用户 2026-10-25 选的口径） */
export const fetchLeaderFollow = (date?: string, lag?: number) =>
  qdGet<SectorFollow>("/qd/sector-follow", { date, lag: lag === undefined ? undefined : String(lag) });

/**
 * 区间分布（02 §2/§4 要求上界面）。
 *
 * 存在的理由：只看某一天，"天天退潮"像故障；把上千天摊开才看得见 quantdash
 * 优先级链自身的偏置。界面只呈现分布与优先级顺序，**不改判定、不做取舍**。
 */
export interface Distribution {
  available: true;
  days?: number | null;
  from_date?: string | null;
  to_date?: string | null;
  /** 每列一份，键已按优先级排好；零值档后端会显式交出（冰点 0 天是结论本身） */
  stage_distribution?: Record<string, Record<string, number>>;
  stage_priority?: Record<string, string[]>;
  never_seen_stages?: Record<string, string[]>;
  tie_break_distribution?: Record<string, number>;
  sample_total?: { stage?: number | null; tie_break?: number | null };
  /** 统计口径出自哪里 —— 目前 stage 结果仍来自 PG 基准表，本地重算只覆盖单场次 */
  basis?: string | null;
  observed_sources?: string[];
}

export const fetchDistribution = (days = 0) =>
  qdGet<Distribution>("/qd/distribution", days > 0 ? { days: String(days) } : {});

/** 本地快讯（02 §6）。`sentiment`/`level` 为 null 就是"没做/没给"，不是"中性" */
export interface NewsItemRow {
  item_id?: string | null;
  title?: string | null;
  content?: string | null;
  url?: string | null;
  source?: string | null;
  published_at?: string | null;
  captured_at?: string | null;
  level?: string | number | null;
  sentiment?: string | null;
  news_type?: string | null;
  logic_source?: string | null;
  spec_version?: string | null;
  deviation?: Deviation[];
}

export interface QdNews {
  available: true;
  date?: string | null;
  items?: NewsItemRow[];
  count?: number | null;
  truncated?: boolean | null;
  corrupt_lines?: number | null;
  file?: string | null;
  /** 存储里没有个股关联字段 → 按股过滤做不到（做不到要出声，不用文本匹配硬凑） */
  supports_code_filter?: boolean | null;
  /** available:true 时为 null；0 条时后端会把 available 打成 false 并给原因 */
  reason?: string | null;
}

export const fetchQdNews = (date?: string, limit = 200) =>
  qdGet<QdNews>("/qd/news", { date, limit: String(limit) });

/**
 * 本轨板块周期 —— 走的是**已上线**的 `/api/market/sector-cycle`（见展示规范 01 §7）。
 *
 * 它和 qd 轨不是同一个东西：本轨是五档生命周期阶段（回答"处在启动的哪个阶段"），
 * qd 轨是轮动矩阵 + 持续性统计（回答"换了几手、谁连庄最久"）。所以两块分开渲染，
 * 合并会迫使其中一套字段留空、看起来像数据缺失（02 §5）。
 */
export interface OwnSector {
  code?: string | null;
  name?: string | null;
  stage?: string | null;
  sector_type?: string | null;
  score?: number | null;
  /** 五要素：squeeze / position / ignition / volume / breadth */
  elements?: Record<string, unknown>;
  heat?: Record<string, unknown>;
  term_count?: number | null;
  /** 打标不裁：风格/行为统计板块交界面决定默认展示什么（01 规范第五条） */
  synthetic?: boolean | null;
}

export interface OwnSectorCycle {
  available: true;
  date?: string | null;
  /** 后端已按 ignite > follow > coil > fade > none、同级按 score 降序排好，前端不要重排 */
  sectors?: OwnSector[];
  /** 要原样显示的口径告警（藏起来读者就会以为没轮动） */
  caveat?: string | null;
}

export const fetchOwnSectorCycle = (date?: string) =>
  qdGet<OwnSectorCycle>("/market/sector-cycle", { date });

/** 空 → `—`。null/undefined/空串一视同仁；0 是有效值，绝不吞掉 */
export function dash(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string" && v.trim() === "") return "—";
  return String(v);
}

/** 只有真数字才格式化；NaN/Infinity/字符串一律 `—`，不许悄悄变成 0 */
export function num(v: unknown, digits = 2): string {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : "—";
}

/** 百分比：NULL 不是 0%（01 规范第二条） */
export function pct(v: unknown, digits = 2): string {
  return typeof v === "number" && Number.isFinite(v) ? `${v > 0 ? "+" : ""}${v.toFixed(digits)}%` : "—";
}

/** 02 §2 第 2 条：三者不一致要显式提示，别让用户自己比对 */
export function stageMismatch(qd?: QdSentiment | null): boolean {
  if (!qd) return false;
  const cols = [qd.stage_ui, qd.stage_mcp, qd.stage_feishu].filter(x => x != null) as string[];
  return new Set(cols).size > 1;
}

export const STAGE_COLUMNS = [
  { key: "stage_ui", column: "stage_ui", label: "quantdash·页面" },
  { key: "stage_mcp", column: "stage_mcp", label: "quantdash·AI" },
  { key: "stage_feishu", column: "stage_feishu", label: "quantdash·飞书" },
] as const;

/** 归属只认后端标出来的 column，所以 paired 只剩一种取值 */
export type StageReasonSet = { map: Record<string, string>; paired: "by-name" };

/**
 * 形状（2026-09-24 实查 `qd.sentiment_cycle.reasons` 得到）：
 * `[{"stage":"stage_ui","reason":"…","hit_rule":1}, …]`
 * 库里用的键是 **`stage`**，契约 §8 写的是 `column` —— 两个都是"后端明确标了归属"，
 * 所以都收；**按位置猜**那条路径已删除。
 */
export function stageReasons(qd?: QdSentiment | null): StageReasonSet | null {
  const raw = qd?.stage_reasons;
  if (raw == null) return null;
  const order = ["stage_ui", "stage_mcp", "stage_feishu"];
  const map: Record<string, string> = {};

  if (Array.isArray(raw)) {
    for (const item of safeArray<unknown>(raw)) {
      if (!item || typeof item !== "object") continue;
      const rec = item as Record<string, unknown>;
      const column = String(rec.column ?? rec.stage ?? "").trim();
      const reason = String(rec.reason ?? "").trim();
      if (reason && order.includes(column)) map[column] = reason;
    }
  } else if (typeof raw === "object") {
    const rec = raw as Record<string, unknown>;
    for (const column of order) {
      const text = rec[column];
      if (typeof text === "string" && text.trim()) map[column] = text.trim();
    }
  }
  return Object.keys(map).length ? { map, paired: "by-name" } : null;
}

/** 02 §4：四种 tie-break 的固定文案；后两种要视觉降级，因为那不是 quantdash 的口径 */
export const TIE_BREAK_COPY: Record<string, { text: string; degraded: boolean }> = {
  seal_time: { text: "按最早封板时间选出（quantdash 原口径）", degraded: false },
  amount_no_seal_time: {
    text: "⚠ 无封板时间，改按成交额最大选出 —— 与 quantdash 原口径不等价", degraded: true,
  },
  code_asc: {
    text: "⚠ 成交额也并列，按代码升序取首个（确定性兜底，无业务含义）", degraded: true,
  },
  no_candidate: { text: "当日无涨停，无龙头", degraded: true },
};
export const TIE_BREAK_UNKNOWN = {
  text: "⚠ 未知的 tie-break 取值，不能替你猜它对应哪种选法", degraded: true,
};

/** 02 §1 第 3 条：法律要求也是诚实要求，每个 qd 轨首次出现的卡片都要带上 */
export const QD_PROVENANCE =
  "逻辑来源：quantdash-ai-stock-main（PolyForm Noncommercial），本项目仅重实现其算法口径，未复制源码";

export const OWN_TRACK_LABEL = "本地口径";
export const QD_TRACK_LABEL = "quantdash 口径";
