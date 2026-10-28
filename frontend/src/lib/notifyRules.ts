/** 消息推送面板的**纯判据层**（单独一个文件是因为它要能被 `node --test` 直接驱动：
 * `lib/notify.ts` 里 import 了 `api.ts`，那边有 class 的构造参数属性，
 * 类型擦除那一层跑不动 —— 同一个坑在 `hostLoops.ts` 已经踩过一次）。
 *
 * 这里只放"把后端交回来的读数说成人话"的判据；配置、校验、到点判定都在后端
 * （`notifyconfig.py` / `duanxian/notify.py`），这里一份规则都不重复。
 */
export const KIND_LABELS: Record<string, string> = {
  feishu: "飞书群助手", dingtalk: "钉钉群助手", wecom: "企业微信群助手", mail: "邮件（SMTP）",
};
export const SCENE_LABELS: Record<string, string> = {
  confluence: "策略共振榜", screen: "条件筛选结果", watchlist: "自选股当日明细",
  dossier: "个股量化档案", positions: "持仓与浮动盈亏", market: "市场概览",
};
export const WEEK_DAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

export interface NotifyChannel {
  key?: string | null; kind?: string | null; kind_label?: string | null;
  label?: string | null; url_masked?: string | null; url_env?: string | null;
  has_secret?: boolean | null; secret_source?: string | null; missing_reason?: string | null;
  // 加签密钥那一格：后端只交"有没有、来自哪"，**永远不交内容**
  kind_supports_sign?: boolean | null; has_sign?: boolean | null;
  sign_source?: string | null; sign_warning?: string | null;
  /** `.env` 里的**变量名**（名字本身不是凭据，屏幕上 `sign_source` 早就写着它）。
   *  编辑已有渠道时必须回填它 —— 不回填就等于"用户改个备注，把这条指回 .env 的引用清了"。 */
  secret_env?: string | null;
  enabled?: boolean | null; note?: string | null; invalid_reason?: string | null;
}

/** 渠道表单的当前值（**新增与编辑共用一份形状** —— 原先只有"新增"那一份，
 *  于是已配好的渠道点不开、改不了，只能删了重填，而删重填会把凭据抄一遍）。
 *  ⚠️ `url` / `secret` 两格**永远不许从服务端回填**：读接口只有掩码，
 *  把掩码填回输入框再保存＝把 `******` 当凭据写回去。空着就是"不改这一条"。 */
export interface ChannelDraft {
  key: string; kind: string; label: string; url: string; url_env: string;
  secret: string; secret_env: string; note: string; editing: boolean;
}
export const emptyChannelDraft = (): ChannelDraft => ({
  key: "", kind: "feishu", label: "", url: "", url_env: "", secret: "", secret_env: "",
  note: "", editing: false,
});
/** 已配置的渠道 → 表单：只回填屏幕上本来就看得见的东西（种类/名字/键/两个变量名/备注）。 */
export function channelDraft(c: NotifyChannel): ChannelDraft {
  return {
    key: String(c.key || ""), kind: String(c.kind || "feishu"), label: String(c.label || ""),
    url: "", url_env: String(c.url_env || ""), secret: "", secret_env: String(c.secret_env || ""),
    note: String(c.note || ""), editing: true,
  };
}
/** 表单 → 请求体：新增与编辑走同一条构造，键名与后端 `upsert_channel` 认的一字不差。 */
export function channelPayload(d: ChannelDraft): Record<string, unknown> {
  const body: Record<string, unknown> = { key: d.key.trim(), kind: d.kind,
    label: d.label.trim(), url_env: d.url_env.trim(), secret_env: d.secret_env.trim(),
    note: d.note.trim() };
  if (d.url.trim()) body.url = d.url.trim();          // 留空 = 不改凭据（后端也保旧值）
  if (d.secret.trim()) body.secret = d.secret.trim();  // 同上：留空不改已存的加签密钥
  return body;
}
/** `kind_choices` 的一行：后端顺手把"这类能不能直接粘贴官方地址、要不要密钥"带出来。 */
export interface NotifyChannelChoice {
  value: string; label?: string; must?: string[] | null;
  official?: string | null; supports_sign?: boolean | null;
}
export interface NotifyPlan {
  id?: string | null; name?: string | null; enabled?: boolean | null;
  run_at?: string | null; run_freq?: string | null; weekly_days?: string | null;
  trade_days_only?: boolean | null; scenes?: Record<string, unknown> | null;
  /** 分条还是合并（后端把默认值算好了再交，所以这里不会是 null 也照样给缺省）。 */
  send_mode?: string | null;
  note?: string | null; invalid_reason?: string | null; sendable?: boolean | null;
  // 「今天这一档」的读数由后端算（判据与执行同住 `notifyconfig.slot_state`），前端只渲染。
  today_state?: string | null; today_label?: string | null;
  channels?: { key?: string | null; label?: string | null; exists?: boolean | null;
               usable?: boolean | null }[] | null;
}
/** `catalog.scenes[].fields` 的一格：后端 `notifyconfig.SCENE_FIELDS` 那条描述符解出来的形状。
 *  面板**只按 `type` 渲染**，不认识档名也不知道默认值 —— 默认值/上下限/候选都在这条里，
 *  所以后端加一档参数就自动多一个框，前端不需要跟着改（改一次就多一处会与后端分叉的地方）。 */
export interface SceneField {
  key: string; label?: string;
  type: "int" | "num" | "text" | "bool" | "select" | "multi" | "codes" | string;
  unit?: string | null; min?: number | null; max?: number | null;
  default?: number | string | null; numeric?: boolean | null;
  as_objects?: boolean | null; required?: boolean | null;
  empty_means?: string | null; empty_error?: string | null; note?: string | null;
  bit_prefix?: string | null; bit_suffix?: string | null;
  choices_from?: string | null;
  choices?: { value: string | number; label?: string | null }[] | null;
}
export interface NotifyCatalog {
  date?: string | null;
  /** 分条/合并那一排的选项与措辞（措辞只住后端 `SEND_MODE_MEANING`，前端不抄第二份）。 */
  send_mode_choices?: { value: string; label?: string; hint?: string }[] | null;
  scenes?: { value: string; label?: string; hint?: string | null; default_limit?: number | null;
             max_limit?: number | null; limit_unit?: string | null;
             fields?: SceneField[] | null }[] | null;
  confluence_sources?: { key: string; label: string }[] | null;
  confluence_max_hits?: number | null;
  screen_rules?: { rule_key: string; name?: string; enabled?: boolean;
                   params?: { name: string; type?: string; default?: unknown }[] }[] | null;
  screen_universes?: { key: string; means?: string }[] | string[] | null;
  watchlist_groups?: { group_id?: number | null; name?: string | null; count?: number | null }[] | null;
  reasons?: Record<string, string> | null;
  body_limit_bytes?: number | null;
}
/** 写回后端的计划形状：`channels` 是**键的字符串数组**，
 *  与读回来的 `NotifyPlan.channels`（带 exists/usable 的判定结果）不是一回事 ——
 *  把两者混成一个类型，就会出现"读的时候能用、写的时候类型对不上"的假象。 */
export interface PlanPayload {
  id: string; name: string; enabled?: boolean; run_at: string; run_freq: string;
  weekly_days: string; trade_days_only: boolean; channels: string[];
  scenes: Record<string, unknown>; note: string;
  /** 分条还是合并（用户 2026-10-19 点名）。缺省由后端按 `per_scene` 走。 */
  send_mode?: string;
}

export interface NotifyState {
  enabled?: boolean | null; config_path?: string | null;
  channels?: NotifyChannel[] | null; plans?: NotifyPlan[] | null;
  kind_choices?: NotifyChannelChoice[] | null;
  counts?: Record<string, number | null> | null;
  catalog?: NotifyCatalog | null; history?: Record<string, unknown>[] | null;
  last_run?: { last_run_at?: string | null; last_ok_at?: string | null; last_status?: string | null;
               consecutive_failures?: number | null; reason?: string | null } | null;
  warning?: string | null; can_edit?: boolean | null;
  late_window_min?: number | null;
  note?: string | null; explain?: string | null; generated_at?: string | null;
}

// ---------------------------------------------------------------- 纯判据（面板上那些格子）
export function parseWeeklyDays(raw?: string | null): { days: number[]; bad: boolean } {
  const text = String(raw ?? "").trim();
  if (text === "") return { days: [], bad: false };
  const parts = text.split(",").map((p) => p.trim()).filter((p) => p !== "");
  const days = parts.map((p) => Number(p));
  const bad = days.some((d) => !Number.isInteger(d) || d < 0 || d > 6);
  return { days: bad ? [] : days, bad };
}

/** 这一条计划"什么时候会发"的人话。**不许在这里判"发过没有"** —— 那是宿主的事。 */
export function scheduleText(plan: NotifyPlan): string {
  const at = String(plan.run_at || "").trim() || "没填时刻";
  const freq = String(plan.run_freq || "daily");
  const days = parseWeeklyDays(plan.weekly_days);
  let head = freq === "weekly"
    ? (days.bad ? `每周（周几写坏了：${plan.weekly_days}）`
      : days.days.length ? `每周 ${days.days.map((d) => WEEK_DAY_LABELS[d]).join("、")}` : "每周（没勾周几）")
    : "每天";
  const scope = plan.trade_days_only === false ? "（含周末与休市日）" : "（只交易日）";
  if (freq === "weekly" && !days.days.length && !days.bad) {
    return `${at} ${head}${scope} —— ⚠ 没勾周几，实际会天天发`;
  }
  return `${at} ${head}${scope}`;
}

/** 一条计划现在到底发得出去吗。三种"发不出去"各有各的修法，不许合成一句"未就绪"。 */
export function planHealth(plan: NotifyPlan): { label: string; tone: "ok" | "warn" | "bad" } {
  if (plan.invalid_reason) return { label: `配置不合法：${plan.invalid_reason}`, tone: "bad" };
  const refs = plan.channels || [];
  if (!refs.length) return { label: "没选渠道 ⇒ 到点无处可发", tone: "bad" };
  if (refs.some((r) => !r.exists)) {
    return { label: `指着一条已删除的渠道：${refs.filter((r) => !r.exists).map((r) => r.key).join("、")}`, tone: "bad" };
  }
  if (!refs.some((r) => r.usable)) {
    return { label: `所选渠道都不可用：${refs.map((r) => r.key).join("、")}`, tone: "bad" };
  }
  if (!refs.every((r) => r.usable)) {
    return { label: `只有部分渠道能发（${refs.filter((r) => !r.usable).map((r) => r.key).join("、")} 不可用）`, tone: "warn" };
  }
  if (!Object.keys(plan.scenes || {}).length) return { label: "没勾任何内容 ⇒ 会发一条空正文", tone: "bad" };
  return plan.enabled ? { label: "就绪", tone: "ok" } : { label: "已停用", tone: "warn" };
}

/** 渠道那一行该怎么说：区分"没填"、"填了但变量不在"、"URL 不合法"、"可用"。 */
export function channelHealth(ch: NotifyChannel): { label: string; tone: "ok" | "warn" | "bad" } {
  if (ch.invalid_reason) return { label: ch.invalid_reason, tone: "bad" };
  if (!ch.has_secret) return { label: ch.missing_reason || "还没有凭据", tone: "bad" };
  if (ch.enabled === false) return { label: "已停用", tone: "warn" };
  return { label: "可用", tone: "ok" };
}

/** 某一档的参数格清单（目录没读到就交空数组 ⇒ 那一档只剩条数框，不会凭空多出格子）。 */
export function sceneFieldList(catalog?: NotifyCatalog | null, scene?: string): SceneField[] {
  const hit = (catalog?.scenes || []).find((s) => s.value === scene);
  return (hit?.fields || []) as SceneField[];
}
/** 一格的候选：后端已经把 `choices_from` 解好了；老形状（`{key,label}`）也收，免得目录一改版界面就空。 */
export function fieldChoices(fd: SceneField): { value: string; label: string }[] {
  return (fd.choices || []).map((c) => ({
    value: String(c?.value ?? ""),
    label: String(c?.label ?? c?.value ?? ""),
  }));
}
/** 那一档「条数」框的标题：后端给了 `limit` 描述符就用它的中文（天梯那一格叫"连板天梯列几层"），
 *  没有就退回通用的"条数上限"。单位永远取 `limit_unit`（只/层/组/条）。 */
export function limitLabel(catalog: NotifyCatalog | null | undefined, scene: string,
                           fallback: string): string {
  const fd = sceneFieldList(catalog, scene).find((x) => x.key === "limit");
  return fd?.label || fallback;
}

/** 一条计划"发什么"那一行摘要。格子从描述符来，**这里不再按档名写 if**：
 *  17 档 × 每档几格，按档名写就是三十多段会与后端分叉的代码。 */
export function scenesText(plan: NotifyPlan, limitUnit?: Record<string, string>,
                           catalog?: NotifyCatalog | null): string {
  const keys = Object.keys(plan.scenes || {});
  if (!keys.length) return "（没选内容）";
  return keys.map((k) => {
    const scene = (catalog?.scenes || []).find((s) => s.value === k);
    const label = scene?.label || SCENE_LABELS[k] || k;
    const p = (plan.scenes || {})[k] as Record<string, unknown> | undefined || {};
    const bits: string[] = [];
    for (const fd of sceneFieldList(catalog, k)) {
      if (fd.key === "limit" || !(fd.key in p)) continue;
      const bit = fieldBit(fd, p[fd.key]);
      if (bit) bits.push(bit);
    }
    // 目录**给了**、里面却没有这一格（人手改过配置文件）⇒ 原样点名内部键，宁可丑不能少；
    // 目录整份没读到（`catalog` 没传）时不点名 —— 那时把 `min_hits=3` 印上屏等于甩锅给用户。
    if (catalog) {
      for (const [key, value] of Object.entries(p)) {
        if (key === "limit" || sceneFieldList(catalog, k).some((fd) => fd.key === key)) continue;
        bits.push(Array.isArray(value) ? `${key} ${value.length} 项` : `${key}=${String(value)}`);
      }
    }
    const cnt = Number(p?.limit || 0);
    if (cnt) bits.push(`${cnt} ${limitUnit?.[k] || scene?.limit_unit || "条"}`);
    return label + (bits.length ? `（${bits.join("，")}）` : "");
  }).join("、");
}

/** 一格值说成人话。`bit_prefix`/`bit_suffix` 由后端描述符给（"≥3"、"只发其中 2 个策略"）。
 *  句尾里写了 `%s` 就当**整句**用（值嵌进句子里，如"偏离榜 %s 只"），没写就把值接在前后之间。
 *  这条区分是 10-17 浏览器实测抓出来的：数字格原先把模板原样印上了屏（"3偏离榜 %s 只"）。 */
function fieldBit(fd: SceneField, value: unknown): string {
  const pre = fd.bit_prefix ?? "";
  const tail = fd.bit_suffix ?? "";
  const wrap = (shown: string) => (tail.includes("%s") ? pre + tail.replace("%s", shown) : pre + shown + tail);
  const noneText = () => (fd.empty_means ? `${fd.label || fd.key}＝${fd.empty_means}` : "");
  if (fd.type === "bool") return value ? (tail || fd.label || fd.key) : "";
  if (fd.type === "multi" || fd.type === "codes") {
    const list = Array.isArray(value) ? value : String(value ?? "").split(",");
    const n = list.map((x) => String(x).trim()).filter(Boolean).length;
    if (!n) return noneText();
    return tail ? wrap(String(n)) : `${fd.label || fd.key} ${n} 项`;
  }
  if (value === "" || value === undefined || value === null) return noneText();
  if (fd.type === "select") {
    const hit = fieldChoices(fd).find((c) => c.value === String(value));
    return wrap(hit?.label || String(value));
  }
  // 单位不进摘要：`unit` 那一格是给**输入框**看的（"次/周/%"），摘要里要的是
  // `bit_prefix/bit_suffix` 那句人话（"倍量≥3"、"涨幅≥1%"）。两个都拼就会写成"≥3个个"。
  return wrap(String(value));
}

/** 「今天这一档」的配色。**文案一律用后端那句**（判据与执行同住 `notifyconfig.slot_state`），
 *  这里只决定颜色的轻重。
 *  - `in_window` 是黄不是红：到点没发但还在补发窗口里，下一轮心跳就补；
 *  - `missed` 是红：窗口已经过了，宿主不会自己追，要人点「立刻发一次」；
 *  - 读不出的键也判黄并原样把后端那句交出去 —— 不许把"没见过"显示成"没事"。 */
export function todayTone(state?: string | null): "ok" | "warn" | "bad" {
  const key = String(state || "").trim();
  if (key === "sent" || key === "waiting") return "ok";
  if (key === "missed" || key === "bad") return "bad";
  return "warn";
}

/** 这一类渠道要不要「加密密钥」那一格。**优先读后端 `kind_choices.supports_sign`**，
 *  后端没交这份信息才退回内置的两种（飞书/钉钉）—— 抄一份在界面里必然会有人改漏。 */
export function supportsSign(kind: string, choices?: NotifyChannelChoice[] | null): boolean {
  const hit = (choices || []).find((c) => c.value === kind);
  if (hit && typeof hit.supports_sign === "boolean") return hit.supports_sign;
  return kind === "feishu" || kind === "dingtalk";
}

/** 凭据框的提示语：机器人一律**先说"直接粘贴群里复制的那条地址"**，再补内部简写。
 *  （10-16 用户就是拿 `https://open.feishu.cn/…` 来填，被旧文案的 `feishu://` 带偏了。） */
export function urlPlaceholder(kind: string, choices?: NotifyChannelChoice[] | null): string {
  const hit = (choices || []).find((c) => c.value === kind);
  if (kind === "mail") {
    return "mailto://发件人:授权码@smtp.域名/收件人（或直接留空，改用下面的变量名）";
  }
  const official = hit?.official;
  const short = { feishu: "feishu://群机器人 token", wecom: "wecombot://群机器人 key",
                  dingtalk: "dingtalk://机器人 token" } as Record<string, string>;
  return official
    ? `直接粘贴机器人地址（如 ${official}…）；也可写 ${short[kind] || ""}${hit?.supports_sign ? "，加签密钥填下面那一格" : ""}`
    : (short[kind] || "把机器人地址粘进来");
}
