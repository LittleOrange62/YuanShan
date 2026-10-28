import { useEffect, useState } from 'react';
import { authHeaders } from './api.ts';
import { apiUrl } from './base.ts';

// 渲染规则住在叶子模块 `promptRender.ts`（那边能被 node --test 直接测），这里只转出口。
export { renderPrompt, unfilledPlaceholders } from './promptRender.ts';
export type { PromptValues } from './promptRender.ts';

/**
 * 页面上那些「一键运行」按钮的**话术从库里读**，本模块只做两件事：取模板 + 把这一行的数据代进去。
 *
 * 两类模板走同一个入口（`id` 认，页面下拉不必区分它们存在哪）：
 *  · `builtin` —— 代码里注册的槽位（`duanxian/prompt_slots.py::SLOTS`），库里可覆盖，能「恢复默认」；
 *  · `custom`  —— 用户在系统配置页自己加的模板（`config.setting` 的 `ai_prompt.custom.<id>`），
 *                 选一个「类型」决定能拿到哪些字段，删就是真删。
 *
 * 一条不许越界的规矩：**模板没取到时不许在代码里再抄一份话术顶上**。那是第二个真相源，
 * 早晚和库里那份漂。取不到就亮错误、按钮禁用，让人去系统配置页看一眼。
 *
 * 另一条：话术能改，**红线改不动**。这一档走 `task_kind='page'`，服务端的 `PRODUCT_POLICY`
 * 与 `check_report_text` 照旧拦交易动作与点位；自建模板同样过服务端那道校验。
 */
export interface PromptTemplate {
  id: string; kind: 'builtin' | 'custom'; title: string;
  scope: string; scope_label: string; page: string; notes: string;
  fields: Record<string, string>;
  template: string; default_template?: string;
  source?: string; edited: boolean; updated_at: string | null;
}
export interface PromptScope { id: string; label: string; page: string; fields: Record<string, string> }

interface Payload { slots: PromptTemplate[]; scopes: PromptScope[] }

let cache: Payload | null = null;
let inflight: Promise<Payload> | null = null;
const listeners = new Set<() => void>();

/** 一次拉全部（内置 + 自建 + 类型表），页面上多个按钮共用，不各发一次请求。 */
export function loadPrompts(refresh = false): Promise<Payload> {
  if (!refresh && cache) return Promise.resolve(cache);
  if (!inflight) {
    inflight = (async () => {
      const r = await fetch(apiUrl('/api/prompts'), { headers: authHeaders() });
      if (!r.ok) throw new Error(`提示词未取到：HTTP ${r.status}`);
      const body = await r.json() as { data?: Partial<Payload> };
      cache = { slots: body?.data?.slots ?? [], scopes: body?.data?.scopes ?? [] };
      listeners.forEach(fn => fn());
      return cache;
    })().finally(() => { inflight = null; });
  }
  return inflight;
}

/** 旧调用名保留（配置页与两个使用点都在用），返回的还是整份负载。 */
export const loadSlots = (refresh = false) => loadPrompts(refresh).then(p => p.slots);

async function send<T>(path: string, method: 'POST' | 'DELETE', body?: unknown): Promise<T> {
  const r = await fetch(apiUrl(path), {
    method,
    headers: { ...authHeaders(), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const parsed = await r.json().catch(() => null) as { data?: T; detail?: string; error?: string } | null;
  if (!r.ok) throw new Error(parsed?.detail || parsed?.error || `保存失败 HTTP ${r.status}`);
  if (parsed?.data === undefined) throw new Error('接口没返回生效后的内容，不假定已写入');
  cache = null;
  await loadPrompts();
  return parsed.data;
}

/** 覆盖内置槽位的话术（走 `POST /api/prompts/{id}`，返回库里那份）。 */
export const savePromptSlot = (id: string, template: string) =>
  send<PromptTemplate>(`/api/prompts/${encodeURIComponent(id)}`, 'POST', { template });
/** 恢复内置默认 = 删掉库里那条覆盖。 */
export const resetPromptSlot = (id: string) =>
  send<PromptTemplate>(`/api/prompts/${encodeURIComponent(id)}`, 'DELETE');

export const saveTemplate = (t: { title: string; scope: string; template: string; id?: string }) =>
  send<PromptTemplate>('/api/prompts/custom', 'POST',
    { title: t.title, scope: t.scope, template: t.template, id: t.id ?? '' });
export const deleteTemplate = (id: string) =>
  send<{ deleted: string }>(`/api/prompts/custom/${encodeURIComponent(id)}`, 'DELETE');

export function usePrompts(): { loading: boolean; error: string; slots: PromptTemplate[]; scopes: PromptScope[] } {
  const [box, setBox] = useState<{ loading: boolean; error: string; slots: PromptTemplate[]; scopes: PromptScope[] }>(
    { loading: true, error: '', slots: [], scopes: [] });
  useEffect(() => {
    let alive = true;
    const pull = () => {
      loadPrompts().then(p => { if (alive) setBox({ loading: false, error: '', slots: p.slots, scopes: p.scopes }); })
        .catch(e => { if (alive) setBox({ loading: false, error: e instanceof Error ? e.message : '提示词未取到', slots: [], scopes: [] }); });
    };
    listeners.add(pull);
    pull();
    return () => { alive = false; listeners.delete(pull); };
  }, []);
  return box;
}

/**
 * 某个页面上该选哪份话术：返回该类型的全部模板（内置在前），外加"当前选中"的读写。
 * 选择记在 localStorage（按类型一个键）—— 换页面、刷新都不该把它弹回默认。
 */
export function useTemplatePicker(scope: string): {
  templates: PromptTemplate[]; current: PromptTemplate | null;
  currentId: string; choose: (id: string) => void; loading: boolean; error: string;
} {
  const { slots, loading, error } = usePrompts();
  const key = `yuanshan-prompt-choice-${scope}`;
  const [picked, setPicked] = useState<string>(() => {
    try { return localStorage.getItem(key) || ''; } catch { return ''; }
  });
  const templates = slots.filter(s => s.scope === scope);
  const current = templates.find(s => s.id === picked) ?? templates[0] ?? null;
  return {
    templates, current, currentId: current?.id ?? '', loading, error,
    choose: (id: string) => {
      setPicked(id);
      try { localStorage.setItem(key, id); } catch { /* 存不下只影响下次记忆的选中 */ }
    },
  };
}
