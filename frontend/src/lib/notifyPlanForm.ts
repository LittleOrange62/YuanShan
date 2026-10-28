/**
 * 消息推送「一条计划」表单的**纯逻辑层**（读回 / 写回 / 每一档的参数格）。
 *
 * 为什么单独一个文件、而且不放在 `NotifyPanel.tsx` 里：这一层必须能被 `node --test` 直接驱动
 * （组件那边 import 了 CSS 与 React）。更重要的是形状 —— 17 档内容 × 每档几格参数，
 * 如果按档名一段一段手写（原先就是这样的：confluence 一段 if、screen 一段 if），
 * 加一档就得改四处，漏掉的那一处表现为"面板上填了、发出去没变"，而那种半截功能不报错。
 *
 * 现在唯一的真源是后端目录里那格 `scenes[].fields`（描述符住在 `notifyconfig.SCENE_FIELDS`）：
 * 这里只按 `type` 渲染与回收值，**不认档名、不写默认值、不猜单位**。
 */
import {
  parseWeeklyDays, sceneFieldList, type NotifyCatalog, type NotifyPlan, type PlanPayload, type SceneField,
} from './notifyRules.ts';

/** 一格参数的当前值。数字与文字都按字符串存（受控输入框要字符串），多选是键数组，开关是布尔。 */
export type SceneValue = string | string[] | boolean;

export interface Form {
  id: string; name: string; run_at: string; run_freq: string; days: number[];
  trade_days_only: boolean; channels: string[]; scenes: string[];
  /** 分条还是合并（`send_mode`，用户 2026-10-19：「按照对应的点，分条推送」）。
   *  默认 `per_scene`；选项与措辞都从后端 `send_mode_choices` 来，这里不抄第二份。 */
  send_mode: string;
  /** 每一档自己的条数上限。**没填的那一档不写进配置**，由后端 `DEFAULT_LIMITS` 兜。 */
  limits: Record<string, string>;
  /** `scenes.<档>.<格子>` 的当前值（条数除外，那条单独走 `limits`）。 */
  params: Record<string, Record<string, SceneValue>>;
  note: string;
}

export const emptyForm = (): Form => ({
  id: "", name: "", run_at: "16:20", run_freq: "daily", days: [], trade_days_only: true,
  channels: [], scenes: [], limits: {}, params: {}, note: "", send_mode: "per_scene",
});

/** 用户在这一档上填过条数才写进配置；留空＝用后端的 `DEFAULT_LIMITS`（不在界面重抄默认值）。 */
export function limitOf(f: Form, scene: string): Record<string, number> {
  const raw = (f.limits[scene] || "").trim();
  const n = Number(raw);
  return raw !== "" && Number.isFinite(n) && n > 0 ? { limit: Math.trunc(n) } : {};
}

function blankValue(fd: SceneField): SceneValue {
  if (fd.type === "bool") return false;
  if (fd.type === "multi" || fd.type === "codes") return fd.type === "codes" ? "" : [];
  return "";
}

/** 后端读回来的值 → 控件值。认不了的形状**原样转成字符串**（宁可显示得丑，不要静默丢掉用户配的东西）。 */
function toControl(fd: SceneField, raw: unknown): SceneValue {
  if (raw === undefined || raw === null) return blankValue(fd);
  if (fd.type === "bool") return raw === true || ["1", "true", "yes", "on"].includes(String(raw).toLowerCase());
  if (fd.type === "multi") {
    const list = Array.isArray(raw) ? raw : String(raw).replace("，", ",").split(",");
    return list.map((x) => String(typeof x === "object" && x
      ? ((x as { rule_key?: string; key?: string }).rule_key
        ?? (x as { key?: string }).key ?? "") : x)).map((s) => s.trim()).filter(Boolean);
  }
  if (fd.type === "codes") return (Array.isArray(raw) ? raw : []).map(String).join(",");
  return String(raw);
}

/** 已存在的计划 → 表单。`catalog` 决定"读回哪些格子"，所以目录没 load 成功时只回名字/时刻。 */
export function formFromPlan(p: NotifyPlan, catalog?: NotifyCatalog | null): Form {
  const scenes = (p.scenes || {}) as Record<string, Record<string, unknown>>;
  const f = emptyForm();
  f.id = String(p.id || ""); f.name = String(p.name || ""); f.run_at = String(p.run_at || "16:20");
  f.run_freq = String(p.run_freq || "daily");
  f.days = parseWeeklyDays(p.weekly_days).days;
  f.trade_days_only = p.trade_days_only !== false;
  f.send_mode = String(p.send_mode || "per_scene");
  f.channels = (p.channels || []).map((c) => String(c.key));
  f.scenes = Object.keys(scenes);
  f.note = String(p.note || "");
  for (const key of f.scenes) {
    const pr = scenes[key] || {};
    if (pr.limit !== undefined && pr.limit !== null) f.limits[key] = String(pr.limit);
    for (const fd of sceneFieldList(catalog, key)) {
      // `limit` 那一格走 `limits`（后端另有一张默认/上限表），不与普通参数混在一个袋子里
      if (fd.key === "limit" || !(fd.key in pr)) continue;
      f.params[key] = { ...(f.params[key] || {}), [fd.key]: toControl(fd, pr[fd.key]) };
    }
  }
  return f;
}

/** 控件值 → 写回后端的那一格。**留空一律不写**（＝用后端默认 / 那格的 `empty_means` 含义）。 */
function encode(fd: SceneField, value: SceneValue): unknown | undefined {
  const empty = value === "" || value === false || value === undefined || value === null
    || (Array.isArray(value) && value.length === 0);
  if (empty) return undefined;
  if (fd.type === "bool") return true;
  if (fd.type === "codes") {
    const codes = String(value).split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean);
    return codes.length ? codes : undefined;
  }
  if (fd.type === "multi") {
    const list = Array.isArray(value) ? value : String(value).replace("，", ",").split(",");
    const keys = list.map((s) => String(s).trim()).filter(Boolean);
    if (!keys.length) return undefined;
    return fd.as_objects ? keys.map((k) => ({ rule_key: k, params: {} })) : keys;
  }
  if (fd.numeric) return Number(value);
  if (fd.type === "int" || fd.type === "num") {
    const n = Number(value);
    return Number.isFinite(n) ? n : undefined;
  }
  return String(value).trim() || undefined;
}

/** 表单 → 请求体。只登记**已勾选**的那些档；每档带上条数（填过才带）与描述符里那些格子。 */
export function formToPlan(f: Form, catalog?: NotifyCatalog | null): PlanPayload {
  const scenes: Record<string, unknown> = {};
  for (const key of f.scenes) {
    const one: Record<string, unknown> = { ...limitOf(f, key) };
    for (const fd of sceneFieldList(catalog, key)) {
      if (fd.key === "limit") continue;
      const encoded = encode(fd, (f.params[key] || {})[fd.key] ?? blankValue(fd));
      if (encoded !== undefined) one[fd.key] = encoded;
    }
    scenes[key] = one;
  }
  return {
    id: f.id || `p-${Date.now().toString(36)}`, name: f.name, run_at: f.run_at,
    run_freq: f.run_freq, weekly_days: f.days.join(","), trade_days_only: f.trade_days_only,
    channels: f.channels, scenes, note: f.note, enabled: true, send_mode: f.send_mode,
  };
}

/**
 * 点「保存计划」时**还差什么**（10-17 用户点名："点击保存计划，没反应"）。
 *
 * 原本按钮是 `disabled={!form.name.trim()}` —— 名字空着就灰掉，而灰掉的按钮点下去
 * **一句话都不说**：屏幕上"没反应"，用户无法知道是没保存还是保存坏了。
 * 现在按钮永远点得动，点下去把缺的几样按名字列出来（同一份判据也用来标必填）。
 *
 * 这三样与后端 `notifyconfig.validate_plan` 的前三条一字对应：
 * 名字非空、`channels` 非空、`scenes` 非空（勾了内容但每档都留空是合法的，那条不算缺）。
 */
export function missingParts(f: Form): string[] {
  const out: string[] = [];
  if (!f.name.trim()) out.push("计划名字（空的：历史与面板都靠它认出这一条是哪条）");
  if (!f.scenes.length) out.push("至少勾一档内容（一条都不勾，发出去就是一句「没勾选任何内容」）");
  if (!f.channels.length) out.push("至少选一条渠道（到点无处可发；渠道不可用时那一格是点不动的）");
  return out;
}
