/**
 * 新页面（数据链路 / 量化选股）的**纯判定层** —— 零 import，所以能被 `node --test` 钉住。
 *
 * 这里收口的是一条硬纪律（`docs/新增页面规划.md` §5 第 3 条）：
 * **后端没接上的页面不许放示例数字，但也不许塌成一句"暂无数据"。**
 * 下面六种情况在屏幕上是六件不同的事，混起来就会重演 FinanceTool 那次
 * "note 全写调度器没开、页面看不出来、三天后才发现一条都没落库"。
 */

export interface Availability {
  available?: boolean | null;
  reason?: string | null;
}

export type DataState<T> =
  | { kind: "loading" }
  /** 页面这一轮**根本没发请求**（比如还没选中标的、这一屏没有可查的对象）。
   *  它既不是"后端坏了"，也不是"没有数"，所以不许借用 `error`/`ok` 表达。 */
  | { kind: "idle"; reason: string }
  /** 连不到进程：服务没起 / 端口不对 */
  | { kind: "offline"; reason: string }
  /** HTTP 404：这个接口在本项目里还没实现 —— 不是"今天没数" */
  | { kind: "not_wired"; path: string }
  /** 5xx / 形状不对：实现了但坏了，要出声 */
  | { kind: "error"; reason: string }
  /** 拿到了对象；`available:false` 仍然走这一支，由 `describeState` 判成"未覆盖" */
  | { kind: "ok"; data: T };

/** 统一措辞：终态只有 YuanShan 一个项目，所以不说"等某某侧接上" */
export const NOT_WIRED_COPY = "本地计算尚未覆盖该项";

/** 屏幕顶部/面板里那句人话。返回空串表示"有数，正常渲染"。
 * ⚠️ 出口一律去掉 `**`：后端这些句子（`reason` / 422 的 detail / `available:false` 的说明）
 * 是 markdown 写的，而屏幕上它们是**纯文本**，星号会原样显示成 `**库里没数**` 这种样子。
 * 10-07 实测：首页那一格就是这么漏的。措辞本身由后端负责，这里只保证渲染层不带标记。 */
export function describeState<T>(state: DataState<T>): string {
  return noMarks(rawDescribeState(state));
}

const noMarks = (s: string) => s.replace(/\*\*/g, "");

function rawDescribeState<T>(state: DataState<T>): string {
  if (state.kind === "loading") return "取数中…";
  if (state.kind === "idle") return state.reason;
  if (state.kind === "offline") return state.reason;
  if (state.kind === "not_wired") return `${NOT_WIRED_COPY} —— 接口 ${state.path} 尚未实现（HTTP 404）。这不是"今天没有"，是这一屏还没有数据来源，页面不放任何示例数字。`;
  if (state.kind === "error") return `取数失败：${state.reason}`;
  const data = state.data as Availability;
  if (data.available === false) {
    return `${NOT_WIRED_COPY}${typeof data.reason === "string" && data.reason.trim() ? `：${data.reason}` : "（后端打了 available:false 却没给 reason —— 违反展示规范 01 第一条，不猜原因）"}`;
  }
  return "";
}

export function hasData<T>(state: DataState<T>): state is { kind: "ok"; data: T } {
  return state.kind === "ok" && (state.data as Availability).available !== false;
}

/**
 * 「后端说这一项本机没有数」——只有这一型该把整块默认收起（用户 2026-10-22：
 * 「以上这些没有获取到数据的，都默认把各自的板块默认收缩起来，点击重新获取如果获取到了再展开」）。
 *
 * ⚠️ 与它相邻的三型**故意不算**，因为那是三件不同的事、屏幕上必须仍然看得见：
 *  · `error` / `offline`：坏了或连不上（读数不可信，必须红着摊在屏上）；
 *  · `not_wired`：接口 404，这一屏还没有数据来源（是"没实现"，不是"今天没数"）；
 *  · `idle` / `loading`：还没选一只 / 正在取（引导句与等待句都只有一行，收它反而藏控件）。
 * 判据放在这一层（零 import 的叶子）而不是组件里，是为了让 `node --test` 能直接钉住这六种分法。
 */
export function isQuiet<T>(state: DataState<T>): boolean {
  return state.kind === "ok" && (state.data as Availability).available === false;
}

/** 空值一律 `—`；0 与 false 是有效值，绝不吃掉（展示规范 01 第三条） */
export const NULL_TEXT = "—";

export function dash(v: unknown): string {
  if (v === null || v === undefined) return NULL_TEXT;
  if (typeof v === "string" && v.trim() === "") return NULL_TEXT;
  return String(v);
}

/**
 * 把后端的错误体压成一句**能渲染成文本**的话。
 *
 * 为什么单独收口：FastAPI 的 422 把 `detail` 交成**对象数组**
 * （`[{type, loc, msg, input, ctx}]`），直接 `String(rec.detail)` 或当 React 子节点渲染，
 * 轻则显示 `[object Object]`，重则整页抛 "Objects are not valid as a React child"。
 * 参数写错不该变成白屏。
 */
export function errorText(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const rec = body as Record<string, unknown>;
  for (const key of ["detail", "error", "reason", "message"]) {
    const v = rec[key];
    if (typeof v === "string" && v.trim()) return v.trim();
    if (Array.isArray(v) && v.length) {
      const parts = v.map(item => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object") {
          const it = item as Record<string, unknown>;
          const loc = Array.isArray(it.loc) ? it.loc.filter(x => x !== "query" && x !== "body").join(".") : "";
          const msg = typeof it.msg === "string" ? it.msg : "";
          if (!msg) return "";
          return loc ? `${loc}：${msg}` : msg;
        }
        return "";
      }).filter(Boolean);
      if (parts.length) return parts.join("；");
    }
  }
  return null;
}

/**
 * 「这份结果多久了」的统一说法：接口普遍交 `age_seconds` / `stale_seconds`（秒）。
 *
 * 为什么要收在一处：慢接口与缓存接口（覆盖体检、数据源健康、体检、板块热度）
 * 都必须把**年龄**摆在屏幕上，否则几小时前的缓存会被读成"刚刚算的"。
 * 三个页面各写一份换算，就会有一个页面悄悄把 8790 秒印成 `8790`。
 * 这只用于展示，不参与任何判定。
 */
export function ageText(seconds: number | null | undefined): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return NULL_TEXT;
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} 小时`;
  return `${(seconds / 86400).toFixed(1)} 天`;
}

/** 数值按后端给的 decimals 显示；没给就原样，不做"看起来更干净"的四舍五入 */
export function num(value: unknown, decimals?: number | null): string {
  if (value === null || value === undefined || value === "") return NULL_TEXT;
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return typeof decimals === "number" && decimals >= 0 ? n.toFixed(decimals) : String(n);
}

/** `unit` 是后端给的，前端不换算单位（01 第三条：单位换算只允许发生在上游） */
const UNIT_SUFFIX: Record<string, string> = { pct: "%", yuan: "元", count: "个", ratio: "" };

/**
 * `unit:"none"` 是后端明确说"这个指标没有单位"（斜率、CCI 这类），
 * 不是"我不知道" —— 所以后缀为空，绝不能把它当未知原样拼到数字后面变成 `…0046none`。
 */
export function withUnit(value: unknown, unit?: string | null, decimals?: number | null): string {
  const shown = num(value, decimals);
  if (shown === NULL_TEXT) return NULL_TEXT;
  if (!unit || unit === "none") return shown;
  return `${shown}${UNIT_SUFFIX[unit] ?? unit}`;
}
