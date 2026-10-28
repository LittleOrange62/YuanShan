// 本文件继承自上游 vibe-astock v1.1.0（Apache-2.0，见根 LICENSE），本机有实质性改动。
// 改动与归属清单：docs/上游改动与归属清单.md；本机自写部分：LICENSE.local.md。
let monitorWarning: string | undefined;
import { apiUrl } from "./base";
import { randomId } from "./random-id";
let monitorId: string | undefined;
let registeredWatch = "";
let registeredAt = 0;
// 后端 API 客户端。/api → vite 代理到本仓库的 FastAPI（server.py，默认 8910）。
// 后端未启动或数据源异常时抛 ApiError，页面据此优雅降级。

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

// 后端访问密钥（对应后端部署时的 VR_API_KEY，公网部署防蹭用）。只存本地浏览器。
const ACCESS_KEY = "vr-access-key";

export function loadAccessKey(): string {
  try {
    return localStorage.getItem(ACCESS_KEY) || "";
  } catch {
    return "";
  }
}

export function saveAccessKey(key: string) {
  try {
    if (key) localStorage.setItem(ACCESS_KEY, key);
    else localStorage.removeItem(ACCESS_KEY);
  } catch {
    throw new Error("未保存：浏览器禁止存储或空间不足，请检查权限后重试");
  }
}

export function authHeaders(): Record<string, string> {
  const k = loadAccessKey();
  return k ? { Authorization: `Bearer ${k}` } : {};
}

export interface MyReport {
  id: string; name: string; industry: string; size: number; ext: string; ts: number;
}

// 下载/预览研报：带鉴权头 fetch → blob → 触发浏览器下载（<a download> 无法带 Authorization，故走 blob）。
export async function downloadReport(id: string, name: string): Promise<void> {
  const resp = await fetch(apiUrl(`/api/myreports/file/${id}`), { headers: authHeaders() });
  if (!resp.ok) throw new ApiError(`下载失败 HTTP ${resp.status}`, resp.status);
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function request<T>(path: string, method: "GET" | "POST" | "DELETE" = "GET", body?: unknown): Promise<T> {
  let resp: Response;
  const headers: Record<string, string> = { ...authHeaders() };
  const opts: RequestInit = { method };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  if (Object.keys(headers).length > 0) opts.headers = headers;
  try {
    resp = await fetch(apiUrl(`/api${path}`), opts);
  } catch {
    throw new ApiError("连接不到后端，请先在项目根目录启动：.venv/bin/python server.py（默认 8910）", 0);
  }
  let payload: any = null;
  try {
    payload = await resp.json();
  } catch {
    /* 非 JSON 响应 */
  }
  if (!resp.ok) {
    if (resp.status === 401) {
      throw new ApiError("后端开启了访问鉴权（VR_API_KEY）：请在「AI 探索 · 接入设置」页底部填写后端访问密钥", 401);
    }
    throw new ApiError(payload?.detail || payload?.error || `HTTP ${resp.status}`, resp.status);
  }
  return (payload?.data ?? payload) as T;
}

const get = <T>(path: string) => request<T>(path, "GET");

export interface Quote {
  quote_time?: string | null; amount_wan?: number | null;
  name: string; price: number; last_close: number; change_pct: number;
  pe_ttm: number; pb: number; mcap_yi: number; turnover_pct: number;
  limit_up: number; limit_down: number;
}

export interface Valuation {
  quote_time?: string | null;
  name: string; code: string; price: number; mcap_yi: number;
  pe_ttm: number; pb: number;
  eps_26e: number | null; eps_27e: number | null; pe_26e: number | null;
  cagr_pct: number | null; peg: number | null; digest_years: number | null;
  analyst_count: number; forecast_note?: string;
}

export interface Report {
  title: string; publishDate: string; orgSName: string;
  emRatingName?: string; indvInduName?: string; pdfUrl?: string | null;
}

export interface ValMetric {
  as_of?: string;
  current: number; percentile: number; min: number; max: number;
  p20: number; p50: number; p80: number; n: number;
}
export interface ValPercentile {
  gaps?: string[];
  period: string; metrics: { pe_ttm?: ValMetric; pb?: ValMetric };
}

export interface Announcement {
  date: string; title: string; type: string; url: string;
}

export interface Financials {
  period: string | null;
  revenue: string | null; revenue_yoy: string | null;
  net_profit: string | null; net_profit_yoy: string | null;
  eps: string | null; bvps: string | null; roe: string | null;
  gross_margin: string | null; net_margin: string | null; op_cf_ps: string | null;
}

export interface NewsItem {
  新闻标题?: string; 发布时间?: string; 文章来源?: string; 新闻链接?: string;
}

export interface IndexQuote {
  name: string; price: number; change_pct: number; change_amt: number;
}

export interface MarketSentiment {
  up: number; down: number; flat: number; zt: number; zt_real: number; dt: number; dt_real: number;
  active: string; breadth: string; speculation: string; date: string;
  // 两档的数值编码（0 轴判据），与折线图共用同一份后端实现
  breadth_score?: number | null; theme_score?: number | null;
}

export interface SectorFlow {
  name: string; pct: number; net: number; inflow: number; outflow: number; firms: number;
}

/** 近若干场的情绪两档序列（读 `analytics.market_sentiment_daily`，不是现抓接口） */
export interface SentimentSeries {
  available: boolean; reason?: string;
  points?: {
    date: string; up_count: number | null; down_count: number | null; ratio: number | null;
    limit_up_real: number | null; breadth_level: string | null; breadth_score: number | null;
    theme_level: string | null; theme_score: number | null; source: string;
  }[];
  basis?: string; latest_date?: string; sources?: { legu: number; local: number };
}

/** 板块资金流的逐日累计（读 `market_data.sector_fund_flow_daily`） */
export interface SectorFlowStored {
  available: boolean; reason?: string;
  rows: {
    sector_name: string;
    net_sum_since: number | null; days_since: number | null;
    net_sum_recent: number | null; days_recent: number | null;
    net_last: number | null; net_last_day: string | null;
    pct_last: number | null; firms_last: number | null;
    /** 10-15 新增（用户：「有没有根据行业的总体市值去折算流入流出…还有就是涨跌幅？多加几列」） */
    net_avg_recent?: number | null; inflow_sum_recent?: number | null; outflow_sum_recent?: number | null;
    pos_days_recent?: number | null; chg_recent_pct?: number | null; chg_days?: number | null;
    float_mv_yi?: number | null; total_mv_yi?: number | null;
    mv_members?: number | null; mv_priced?: number | null;
    net_pct_float_recent?: number | null; net_pct_float_since?: number | null;
    /** 东财板块页那一档的码与真名（对表 `reference_data.em_board_link`；读法 `duanxian/em_board_link.py`） */
    em_board_code?: string | null; em_board_name?: string | null; em_match_kind?: string | null;
  }[];
  coverage?: { available: boolean; sessions?: number; sessions_recent?: number; from?: string | null; to?: string | null; requested_since?: string; months?: number; reason?: string };
  calibers?: Record<string, string>;
  /** 市值那一维的来源与覆盖（分母取自哪一场、90 个行业里拼得出几个、拼不出的名字） */
  market_value?: { as_of?: string | null; industries?: number; covered?: number; basis?: string;
                   missing?: string[]; missing_total?: number };
  definitions?: Record<string, string>;
  /** 板块名能不能点开那一列的对表现状。两个分母各说各的：这批行业 vs 整张对表 */
  board_link?: { available?: boolean; rows_linked?: number | null; rows_total?: number | null;
                 dict_linked?: number | null; dict_total?: number | null;
                 tried_at?: string | null; reason?: string | null; basis?: string };
  caveats?: { code?: string; text?: string }[];
  window?: { since: string; recent_from: string; recent_to: string; top_default: number };
  positive_recent?: number;
  live_stored_today?: boolean;
}

export interface MarketOverview {
  sentiment: MarketSentiment; sectors: SectorFlow[]; updated: string;
  sentiment_series?: SentimentSeries; sector_flow?: SectorFlowStored;
}

// 短线情绪：连板梯队 / 最高连板 / 炸板率 / 封板率 / 晋级率 / 涨跌停家数 + 连板股清单（客观公开榜单）
export interface EmotionTier { boards: number; count: number; plus: boolean }
export interface LianbanStock {
  code: string; name: string; boards: number;
  price: number | null; pct: number | null; amount: number | null; float_cap: number | null;
  /** 「概念」列：本地 reference_data.sector_member（板块类型=概念，窄题材在前）；库里没行 = null（显示 —），不等于没有题材 */
  industry: string | null;
  concept_count?: number | null;
  /** 流通市值用的是哪把尺：source＝同花顺池原值；daily_replay＝当日日线反推（北证/ST 不在池里） */
  float_cap_basis?: 'source' | 'daily_replay' | null;
  /** 首次封板时间 HH:MM。**2026-10-26 起是本地 1 分钟线自算**（第一根收盘仍封在档位上的分钟）；
   *  这一场没有分钟线时退回源站秒级值并由 seal_basis 标出来，两边都没有 = 空串（显示 —，不是 00:00） */
  seal_time?: string;
  /** 这一格实际用的哪把尺：local＝分钟线自算，source＝退回源站，none＝两边都没有 */
  seal_basis?: 'local' | 'source' | 'none' | null;
  src_seal_time?: string | null;      // 源站首封（对照用，不参与排序）
  /** 开板（炸板）次数：分钟线自算，封板强度的输入之一 */
  open_times?: number | null;
  src_reopen?: number | null;         // 源站开板次数：与自算是两把尺，一起给
  at_limit_minutes?: number | null;   // 封在档位上的分钟根数（240＝一字封死）
  minute_bars?: number | null;        // 本场这只票共有几根 1 分钟线（0/null＝这场没分钟线）
  board_kind?: string | null;         // 板型：换手板／一字板／T 字板（源站）
  streak_start?: string | null;       // 这一轮最早那一板的日子
  /** 封板强度 0~100（三项加权，判据只住后端 `duanxian/seal_strength.py`）；有数的少于两项 → null，**不是 0** */
  seal_score?: number | null;
  /** 这一行为是这个分：三个输入各自落在哪一档（缺了哪一项、首封用的是哪把尺都会说明） */
  seal_note?: string;
  /** 「几天几板」：本地日线按 30 场窗口数出来的涨停统计（5天4板）；数不出为 null，不是 0 板 */
  days_boards?: string | null; board_days?: number | null; board_ct?: number | null;
  board_dates?: string[] | null;
  /** 源站「几天几板」原文（只用来与上面那份本地口径当面比，不覆盖 boards） */
  src_high_days?: string | null;
  /** 「首板日大盘情绪」：这一轮最早那个封板日（= board_dates[0]）那天的大盘档位。
   *  主读数取 market_sentiment_daily 的宽度/题材档，mood 只是注记；缺数一律 null，不是 0 档 */
  first_board_day?: string | null;
  first_board_breadth?: string | null; first_board_breadth_score?: number | null;
  first_board_theme?: string | null; first_board_theme_score?: number | null;
  first_board_mood?: number | null; first_board_limit_up?: number | null;
  first_board_note?: string;
  /** 「节点分析」：'1' / '2' / '1, 2' / 空串（空串 = 屏幕上那个「—」，**不等于"没命中"**：
   *  这一场没算到、上证那天没数据、连板窗口被截断都是这一格。是哪一种由 `emotion.node_warn` 说） */
  node_hits?: string | null;
  node_reason?: string | null;
  node_detail?: {
    boards?: number | null; run_first?: string | null; run_second?: string | null;
    sh_pct?: number | null; vol_vs_prev?: number | null;
    prev_max_boards?: number | null; prev_top_count?: number | null;
    prev_top_sealed?: number | null; prev_top_halted?: number | null; prev_top_broke?: number | null;
  } | null;
  reason: string;  // 涨停原因题材串（同花顺涨停揭秘池，编辑归因、非公司确认；不在池里的票为空串）
}
/** 首页那块涨停母体表（`analytics.limit_up_pool_daily`）的覆盖坐标：读侧一次查出，界面照着念 */
export interface LimitUpCoverage {
  from_day?: string | null; to_day?: string | null; sessions?: number | null;
  rows_total?: number | null;
  minute_from?: string | null; minute_to?: string | null;   // 分钟线自算那一族铺到哪
  src_from?: string | null; src_to?: string | null;         // 源站那一族铺到哪
}
export interface ShortTermEmotion {
  date: string;
  /** 库里没有这一场的行时为 false：此时只有 date/reason/caliber_note/lianban_stocks，
   *  界面必须念 `reason`（该跑哪一步），不许念成"今天没有涨停" */
  available?: boolean;
  reason?: string | null;
  zt_count: number; dt_count: number | null; zb_count: number;
  max_boards: number; lianban_count: number;
  ladder: EmotionTier[];
  lianban_stocks: LianbanStock[];
  /** 「节点分析」这一场的诊断（判据与措辞只住 `duanxian/node_view.py`，页面不重述） */
  node?: Record<string, unknown> | null;
  node_rule?: string | null;
  node_warn?: string | null;
  seal_rate: number | null; break_rate: number | null; promotion_rate: number | null;
  /** 晋级率的分母：上一场封住几只 + 是哪一场（读同一张表的上一场，不拿在线昨涨停池顶） */
  prev_date?: string | null; prev_sealed?: number | null;
  /** 「封板强度」的算法说明（后端从那张档位表现拼）。列标题悬停显示这一段。 */
  seal_spec?: string;
  /** 「几天几板」的口径文字 / 回溯场次 / 连续几天没封算断 / 数不出来时的说明（后端交，页面不复述） */
  days_boards_rule?: string; days_boards_window?: number | null;
  days_boards_break_days?: number | null; days_boards_warn?: string | null;
  /** 「首板日大盘情绪」的口径文字 + 整批读不到的说明（后端交，页面不复述） */
  first_board_rule?: string; first_board_warn?: string | null;
  /** 这一轮封板日没数出来的只数：那些行的首板日格为空，原因在「几天几板」那一列 */
  first_board_no_span?: number | null;
  /** 上面这套分档的口径版本（改分档就升号，页面上跟着显示"按哪一版算"） */
  seal_spec_version?: string;
  /** 母体口径（2026-10-26 起＝本地日线判据）与两侧实测差，整段由后端交，页面不自己复述 */
  caliber?: string | null;
  /** 「首封」那一列的算法文字（分钟线自算、退回源站要标、没有分钟线是哪几天） */
  seal_rule?: string | null;
  coverage?: LimitUpCoverage | null;
  /** 母体口径版本号（写侧 `app/compute/limit_up_pool.py` 一份） */
  spec_version?: string | null;
  /** 这一场库里一共几行（= 触过板的全部，含收盘没封住的） */
  rows_total?: number | null;
  /** 缺数短码 → 人话（写侧只出短码，措辞只在这一份里） */
  gap_texts?: Record<string, string> | null;
  /** 日期口径说明（库里最近一场 ≠ 日历最近交易日时后端给的话），非空必须显示 */
  caliber_note?: string | null;
}

// 每日盯盘：3 秒实时快照（持仓/自选/500亿大票/三板+/昨日成交前十 + 异动流）
export interface WatchRow {
  code: string; name: string;
  price: number | null; pct: number | null; amount: number | null;
  cost?: number; shares?: number; pnl_pct?: number;   // 持仓行
  boards?: number; is_limit?: boolean;                 // 三板+行
}
export interface MonitorAlert {
  change_pct?: number | null;
  ts: string; code: string; name: string; kind: string; msg: string; sources: string[];
}
export interface YesterdayStock extends WatchRow { sector?: string; status: string; quote_ok: boolean; quote_time?: string | null; }
export interface YesterdayLadder {
  available: boolean; reason?: string; warning?: string; sample_date?: string | null; quote_date?: string | null;
  covered: number; stocks: YesterdayStock[];
}
export interface MonitorSnapshot {
  watch_warning?: string;
  warming_up?: boolean;
  ts: string; phase: "open" | "break" | "closed"; poll_seconds: number;
  holdings: WatchRow[]; holdings_error?: string; watchlist: WatchRow[];
  bigcap: { total: number; top: WatchRow[] };
  lianban3: WatchRow[];
  yesterday_ladder?: YesterdayLadder;
  turnover: { label: string; stocks: WatchRow[] };
  alerts: MonitorAlert[];
}

// 首板分析：今日首板涨停股（连板数=1）+ 涨停原因题材串（客观公开榜单）
export interface FirstBoardStock {
  code: string; name: string;
  price: number; pct: number; amount: number | null; float_cap: number | null;
  industry: string; seal_time: string; break_count: number; reason: string;
  /** 池内连板数（缺了按 1）—— 四档切换就看这一列 */
  boards?: number | null;
  /** 「节点分析」：'1' / '2' / '1, 2' / 空串（空串就是屏幕上那个「—」，不等于"没命中"） */
  node_hits?: string | null;
  /** 不成立时差在哪一条（后端短码）；成立为空串 */
  node_reason?: string | null;
  node_detail?: {
    boards?: number | null; run_first?: string | null; run_second?: string | null;
    sh_pct?: number | null; vol_vs_prev?: number | null;
    prev_max_boards?: number | null; prev_top_count?: number | null;
    prev_top_sealed?: number | null; prev_top_halted?: number | null; prev_top_broke?: number | null;
  } | null;
}
/** 四档之一（首板／二板／三板／四板及以上），行序由后端按各档规则排好 */
export interface FirstBoardTier {
  key: string; label: string; count: number; stocks: FirstBoardStock[];
}
export interface FirstBoardData {
  date: string; total_zt: number; first_count: number;
  reason_note: string | null;
  /** 库里那一场的涨停家数（判定与展示都只认这一个数） */
  db_zt?: number | null;
  /** 日期口径说明：为什么显示的不是日历上最近那个交易日。非空就必须上屏，不许吞 */
  caliber_note?: string | null;
  /** 库里真有的日子（倒序）。日期选择器只能用它，不许拿 new Date() 造候选项 */
  available_dates?: string[] | null;
  stocks: FirstBoardStock[];
  /** 四档名单。默认那一档（首板）与 `stocks` 是同一批对象，题材/行业统计的分母就是它 */
  tiers?: FirstBoardTier[] | null;
  /** 「节点分析」这一场的诊断（rows/命中只数/判不了的原因），措辞住在 duanxian/node_view.py */
  node?: Record<string, unknown> | null;
  node_rule?: string | null;
  node_warn?: string | null;
  /** 涨停原因拆成单个原因后的类别计数（只含重复≥2 的，计数在 vr/firstboard.py 里算） */
  reason_summary?: { name: string; count: number }[] | null;
  /** 同一口径的行业计数 */
  industry_summary?: { name: string; count: number }[] | null;
  summary_meta?: {
    scope?: string; stock_count?: number; no_reason?: number;
    dropped_single_reason?: number; dropped_single_industry?: number;
  } | null;
}

// 全市场换手率榜（客观公开榜单；09-27 由成交额榜改过来，排序键在取数侧就是 f8）
export interface TurnoverStock {
  code: string; name: string;
  price: number | null; pct: number | null;
  amount: number | null; mcap: number | null; float_cap: number | null; industry: string;
  turnover: number | null;            // 换手率 %，本榜的排序键
  concepts?: string | null;           // 概念板块前 3 个（窄题材在前），null = 库里没有概念行
  concept_count?: number | null;      // 去重后它属于几个不同概念
}
export interface TurnoverTop {
  stocks: TurnoverStock[]; updated: string; quote_date?: string; reason?: string;
  rank_basis?: string | null; concept_missing?: number | null; concept_note?: string | null;
}

/** 资金轮动（读 analytics.sector_flow_trend，两条口径 `basis`=share|turnover）。
 *  这里没有一个字段是"信号"：连续场次 / 累计份额差 / 名次都是统计量。
 *  实测份额连续 n 场抬升后下一场仍抬升的概率 46%(n=1)→31%(n=9)，基线 46% ——
 *  越连续越不延续（均值回复），所以界面不许画成红色箭头、不许写"主力连续吸金"。 */
export interface RotationRow {
  sector_code: string; name: string; sector_type: string | null;
  share: number | null; share_prev: number | null; delta_bps: number | null;
  streak_up: number | null; streak_dn: number | null;
  cum_10: number | null; cum_20: number | null; cum_30: number | null;
  days_10: number | null; days_20: number | null; days_30: number | null;
  rank_10: number | null; rank_20: number | null; rank_30: number | null;
  amount_yuan: number | null;
  /** 只有 basis=turnover 的行有值：本板块成分流通市值合计（分母）、匹配成员的成交额和（分子）、
   *  剔了多少成员。share 行这几格是 null —— 界面不许拿 0 填。 */
  mv_yuan?: number | null; amount_matched?: number | null;
  members_matched?: number | null; members_total?: number | null;
  has_series?: boolean;
}
export interface RotationBoard {
  window: number;                 // 10 / 20 / 30 场
  rising: RotationRow[];          // 该窗口份额累计抬升最多的一端（纯排序，不含好坏）
  falling: RotationRow[];         // 回落最多的一端（已与 rising 去重）
  ranked: number;                 // 有名次的板块数（可以小于 universe：新板块场次不够就没有名次）
  thin: boolean;
}
export interface SectorRotation {
  available?: boolean; reason?: string;
  date?: string; basis?: string; spec_version?: string; windows?: number[];
  /** 可选口径与当档的量纲说明（换手那一档的 value 已经是百分数，不能再乘 100） */
  basis_allowed?: string[]; unit_value?: string;
  universe?: number | null; universe_amount?: number | null;
  calculated_at?: string | null; age_seconds?: number | null;
  boards?: RotationBoard[]; top_per_window?: number | null;
  name_is_code?: number | null;
  // 母体按登记类型过滤的账目（2026-10-06：880001~880099 那批市场级统计指数改判为 '统计'）
  sector_types?: string[];
  excluded_types?: { sector_type: string; sectors: number }[];
  streak_stats?: { up: { days: number; sectors: number }[]; down: { days: number; sectors: number }[];
                   no_streak: number; total: number };
  series?: { code: string; name: string; points: { date: string; share: number | null }[] }[];
  series_per_board?: number; series_sessions?: number;
  evidence?: string | null; calibers?: Record<string, string>;
}

export interface RadarItem {  title: string; url: string; time: string; source: string; summary?: string; zh?: string;
}
export interface Industry {
  key: string; name: string; accent: string; total: number; items: RadarItem[];
}
export interface RadarData {
  generated_at: string | null; recent_days: number; industries: Industry[];
  stats: { industries: number; total_sources: number; failed_sources?: number };
}

export interface Holding {
  code: string; name: string; price: number; shares: number; cost: number;
  market_value: number; pnl: number; pnl_pct: number;
}
export interface ClosedPosition {
  code: string; name: string; date: string; price: number; shares: number; cost: number;
  pnl: number; pnl_pct: number;
}
export interface PortfolioData {
  holdings: Holding[];
  totals: { market_value: number; cost: number; pnl: number; pnl_pct: number };
  closed: ClosedPosition[];
  realized_pnl: number;
  updated: string;
}

// 资金面 / 筹码 / 信号（v3.3 并入，均为「用户查的那只股」的公开数据）
export interface MarginRow { date: string; rzye: number; rzmre: number; rzche: number; rqye: number; rqmcl: number; rzrqye: number }
export interface BlockTradeRow { date: string; price: number; close: number; premium_pct: number; vol: number; amount: number; buyer: string; seller: string }
export interface HolderRow { date: string; holder_num: number; change_ratio: number; avg_shares: number }
export interface DividendRow { date: string; bonus_rmb: number; transfer_ratio: number; bonus_ratio: number | null; plan: string }
export interface FundFlowRow { date: string; main_net: number; small_net: number; mid_net: number; large_net: number; super_net: number }
export interface DtSeat { name: string; buy_amt: number; sell_amt: number; net: number }
export interface DragonTiger {
  records: { date: string; reason: string; net_buy: number; turnover: number }[];
  seats: { buy: DtSeat[]; sell: DtSeat[] };
  institution: { buy_amt: number; sell_amt: number; net_amt: number };
}
export interface LockupRow { date: string; type: string; shares: number | null; able_shares: number | null; ratio: number | null }
export interface UnlockCalendar { start: string; end: string; fetched_at: string; truncated: boolean; events: (LockupRow & { code: string; name: string })[] }
export interface Lockup { history: LockupRow[]; upcoming: LockupRow[] }
export interface Board { name: string; code: string; change_pct: number | string; lead_stock: string }
export interface Blocks { total: number; boards: Board[]; concept_tags: string[] }
export interface HotConcept { concept: string; bk: string; hit: number }
export interface QaRow { company: string; question: string; answer: string | null; answerer: string; ask_time: string }
export interface IndustryRow { rank: number; name: string; change_pct: number; code: string; up_count: number; down_count: number }
export interface IndustryData { top: IndustryRow[]; bottom: IndustryRow[]; total: number }

// 全球市场（美股 / 港股，移植自 global-stock-data · 东财域内源）
export interface GlobalIndex {
  key: string; name: string; region: string;
  price: number | null; change_pct: number | null;
}
export interface GlobalQuote {
  code: string; name: string;
  price: number | null; open: number | null; high: number | null; low: number | null;
  prev_close: number | null; amount: number | null; mcap: number | null; change_pct: number | null;
}
export interface GlobalMetrics {
  report_date: string;
  revenue: number | null; revenue_yoy: number | null; net_profit: number | null;
  eps: number | null; roe: number | null; gross_margin: number | null;
  net_margin: number | null; debt_ratio: number | null;
}
export interface GlobalStock {
  code: string; name: string; market: string;
  quote: GlobalQuote; metrics: GlobalMetrics | null;
}

/** 此刻的「实时行情」属于哪一场 —— 盘前行情返回的是上一场收盘，UI 要如实标注 */
export interface MarketSession {
  now: string;
  today: string;
  /** 实时行情代表的交易日；取不到时为 null */
  quotes_of: string | null;
  is_today: boolean;
  poll?: boolean;
  phase_key?: string;
  phase: string;
  /** 直接可展示的一句话，如「盘前 · 显示 2026-07-29 收盘」 */
  label: string;
}

/** 隔夜外围快照：指数 + 美股七姐妹，各自带「属于哪一场」 */
export interface OverseasRow {
  name: string;
  /** 点位/涨跌都可能没有（源返回 '-' 或整行缺）—— null 就是 null，界面显示 `—`，
   *  原来这里定成 `number`，回退分支只能 `?? 0` 顶上，于是渲染出一个粗体的假点位 0 */
  price: number | null;
  change_pct: number | null;
  /** 该行行情所属交易日；取不到为 null（**不拿今天顶替**） */
  session: string | null;
  region?: string;
  ticker?: string;
}
export interface OverseasSnapshot {
  available: boolean;
  reason?: string;
  indices?: OverseasRow[];
  mag7?: OverseasRow[];
  us_session?: string | null;
  hk_session?: string | null;
  /** 可直接展示的一句话，如「美股 2026-07-29 收盘」「港股 2026-07-30 盘前」。
   *  别用 us_session 自己拼「XX 收盘」—— 港股在北京白天可能正在交易。 */
  us_label?: string | null;
  hk_label?: string | null;
}

/** 今日实时打板情绪（盘面数据页）—— 与 ShortTermEmotion（已收盘那一场）分开 */
export interface LiveEmotion {
  available: boolean;
  reason?: string;
  date?: string;
  /** 快照时刻 HH:MM */
  as_of?: string;
  phase?: string;
  zt_count?: number;
  dt_count?: number | null;
  zb_count?: number | null;
  max_boards?: number;
  lianban_count?: number;
  seal_rate?: number | null;
  break_rate?: number | null;
  promotion_rate?: number | null;
  /** 晋级率的分母：上一场的涨停家数 */
  promotion_base?: number | null;
  /** 分母是哪一场。两张卡都叫「晋级率」，而各自的「昨」不是同一天，所以把日期给出来写死 */
  promotion_base_date?: string | null;
}

export const api = {
  health: () => get<{ ok: boolean }>("/health"),
  indices: () => get<IndexQuote[]>("/indices"),
  marketSession: () => get<MarketSession>("/market/session"),
  overseas: () => get<OverseasSnapshot>("/market/overseas"),
  liveEmotion: () => get<LiveEmotion>("/market/live-emotion"),
  marketOverview: () => get<MarketOverview>("/market/overview"),
  /** 份额轮动榜（本地逐日，统计量非信号）。date 省略=库里最新一场；指定那场没数时后端给
   *  available:false，**不做邻近交易日回落** —— 页面要能把"那天没数"和"那天就是这样"分开说。 */
  sectorRotation: (date?: string, basis?: string) => {
    const q = new URLSearchParams();
    if (date) q.set("date", date);
    if (basis) q.set("basis", basis);
    const s = q.toString();
    return get<SectorRotation>(`/market/sector-rotation${s ? `?${s}` : ""}`);
  },
  emotion: () => get<ShortTermEmotion>("/market/emotion"),
  monitorSnapshot: async (watch: string) => {
    const codes = watch.split(',').filter(Boolean);
    const monitored = codes.slice(0, 100).join(',');
    if (!monitorId || monitored !== registeredWatch || Date.now() - registeredAt > 60000) {
      monitorId ??= randomId().replace(/-/g, "");
      registeredWatch = monitored; registeredAt = Date.now();
      try {
        await request('/monitor/watch', 'POST', {client_id: monitorId, codes: codes.slice(0, 100)});
        monitorWarning = undefined;
      } catch (e) {
        monitorWarning = `自选监控名单未更新：${e instanceof Error ? e.message : '注册失败'}；一分钟后重试，其余行情继续展示`;
      }
    }
    const snapshot = await get<MonitorSnapshot>(`/monitor/snapshot?watch=${encodeURIComponent(monitored)}`);
    return {...snapshot, watch_warning: monitorWarning || (codes.length > 100 ? '已有自选超过100只，本页仅监控前100只；原列表保留，可在持仓自选中整理' : undefined)};
  },
  firstBoard: (date?: string) =>
    get<FirstBoardData>(`/market/first-board${date ? `?date=${encodeURIComponent(date)}` : ""}`),
  turnoverTop: () => get<TurnoverTop>("/market/turnover-top"),
  globalIndices: () => get<GlobalIndex[]>("/global/indices"),
  globalStock: (symbol: string) => get<GlobalStock>(`/global/stock?symbol=${encodeURIComponent(symbol)}`),
  radar: () => get<RadarData>("/radar"),
  radarRefresh: () => request<RadarData>("/radar/refresh", "POST"),
  portfolio: () => get<PortfolioData>("/portfolio"),
  addHolding: (code: string, shares: number, cost: number) => request<PortfolioData>("/portfolio/holding", "POST", { code, shares, cost }),
  removeHolding: (code: string) => request<PortfolioData>(`/portfolio/holding?code=${code}`, "DELETE"),
  refreshPortfolio: () => request<PortfolioData>("/portfolio/refresh", "POST"),
  closePosition: (code: string, date: string, price: number, shares: number, cost: number) =>
    request<PortfolioData>("/portfolio/close", "POST", { code, date, price, shares, cost }),
  removeClosed: (index: number) => request<PortfolioData>(`/portfolio/close?index=${index}`, "DELETE"),
  valuation: (code: string) => get<Valuation>(`/valuation?code=${code}`),
  percentile: (code: string) => get<ValPercentile>(`/valuation/percentile?code=${code}`),
  financials: (code: string) => get<Financials>(`/financials?code=${code}`),
  announcements: (code: string) => get<Announcement[]>(`/announcements?code=${code}`),
  quote: (codes: string) => get<Record<string, Quote>>(`/quote?codes=${codes}`),
  reports: (code: string) => get<Report[]>(`/reports?code=${code}`),
  news: (code: string) => get<NewsItem[]>(`/news?code=${code}`),
  margin: (code: string) => get<MarginRow[]>(`/margin?code=${code}`),
  blockTrade: (code: string) => get<BlockTradeRow[]>(`/block-trade?code=${code}`),
  holders: (code: string) => get<HolderRow[]>(`/holders?code=${code}`),
  dividend: (code: string) => get<DividendRow[]>(`/dividend?code=${code}`),
  fundFlow: (code: string) => get<FundFlowRow[]>(`/fund-flow?code=${code}`),
  dragonTiger: (code: string) => get<DragonTiger>(`/dragon-tiger?code=${code}`),
  unlockCalendar: (window: "upcoming" | "recent") => get<UnlockCalendar>(`/lockup-calendar?window=${window}`),
  lockup: (code: string) => get<Lockup>(`/lockup?code=${code}`),
  blocks: (code: string) => get<Blocks>(`/blocks?code=${code}`),
  hotConcepts: (code: string) => get<HotConcept[]>(`/hot-concepts?code=${code}`),
  investorQa: (code: string) => get<QaRow[]>(`/investor-qa?code=${code}`),
  industry: (top = 20) => get<IndustryData>(`/industry?top=${top}`),
  myReports: () => get<MyReport[]>("/myreports"),
  // 「个股信息」那一格的 400 字公司说明：材料组装与结果回执都走后端一处（`duanxian/stock_brief.py`）。
  // 泛型在调用侧交（类型住 `lib/stockBrief.ts`，这里不再抄一份字段名，免得两边各漂各的）。
  briefMaterial: <T>(body: unknown) => request<T>("/stock/brief/material", "POST", body),
  briefResult: <T>(body: unknown) => request<T>("/stock/brief/result", "POST", body),
  uploadReport: (name: string, contentB64: string) =>
    request<MyReport>("/myreports", "POST", { name, content_b64: contentB64 }),
  deleteReport: (id: string) => request<{ ok: boolean }>(`/myreports/${id}`, "DELETE"),
};
