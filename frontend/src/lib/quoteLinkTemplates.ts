import { useEffect, useState } from "react";
import { apiUrl } from './base.ts';
import { authHeaders } from './api.ts';

/**
 * 按市场的行情外链地址表（系统配置里那张）在浏览器这一侧的**一份缓存**。
 *
 * 为什么要有这一层：规则本体在后端 `app/quote_link.py`，而全站有几十个渲染点在拼股票名的链接
 * （`StockLink` 以及各页面的表格里直接调 `emUrl`），**每处回后端要一次地址是不成立的**。
 * 所以：整页拉一次、存模块级缓存、拼链接时按行替换 `******`，拿不到就按出厂口径拼 ——
 * 少一次配置读取可以接受，股票名变成没有链接不行。
 *
 * 与后端的共同真值表仍是 `test/emUrl.fixtures.json`（钉的是**出厂**那一份，
 * 用户把某行改成别的站点不会把测试弄红）。
 */
export interface LinkRoute {
  id: string; label: string; default: string; template: string;
  configured?: boolean; changed?: boolean; bad?: string | null;
  sample_code: string; sample_url?: string | null;
}
export interface LinksState {
  placeholder: string; routes: LinkRoute[]; changed_any: boolean;
  warnings?: string[]; note?: string; explain?: string;
  changed_at?: string | null; changed_by?: string | null;
}

/** 后端没回之前 = null ⇒ 一律按出厂模板拼（`quoteLink.ts` 里的默认路径）。 */
let templates: Record<string, string> | null = null;
let snapshot: LinksState | null = null;
let inflight: Promise<LinksState | null> | null = null;
const listeners = new Set<() => void>();

function emit() { for (const l of Array.from(listeners)) l(); }

export function linkTemplates(): Record<string, string> | null { return templates; }
export function linksSnapshot(): LinksState | null { return snapshot; }

export function applyLinksState(s: LinksState | null) {
  snapshot = s;
  const map: Record<string, string> = {};
  for (const r of (s?.routes ?? [])) if (r?.id && r?.template) map[r.id] = r.template;
  // 整张表都没回（旧后端 / 接口挂了）就退回 null，让 `emUrl` 用出厂路径，
  // 而不是退成"每行都用默认值"——两者今天算出来的地址一样，但语义不一样：
  // 前者是"还不知道有没有人改过"，后者是"确认没人改过"。
  templates = Object.keys(map).length ? map : null;
  emit();
}

export async function loadLinks(force = false): Promise<LinksState | null> {
  // 已经拿到过就不再打：`inflight` 只在"同一拍里并发"时挡得住，而每个**新挂载的 StockLink**
  // 都会在 effect 里调一次（分页翻到第二页＝一整批新格子＝一整发配置请求）。
  // 失败时 `snapshot` 仍是 null ⇒ 下一发照样重试，不会把"没读到"缓存成长期结论。
  if (!force && snapshot) return snapshot;
  if (inflight && !force) return inflight;
  inflight = (async () => {
    try {
      const resp = await fetch(apiUrl('/api/config/quote-links'), { headers: authHeaders() });
      if (!resp.ok) return null;
      const body = await resp.json() as { data?: LinksState } | null;
      if (body?.data) applyLinksState(body.data);
      return body?.data ?? null;
    } catch {
      return null;          // 拿不到配置就按出厂口径拼，不影响任何数据显示
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

/** 保存一行。后端拒绝时把原因原样抛给调用方（"这一笔整条没写入，仍按原值运行"那句很重要）。 */
export async function saveLink(route: string, template: string): Promise<string> {
  const resp = await fetch(apiUrl('/api/config/quote-links'), {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ route, template }),
  });
  const body = await resp.json().catch(() => null) as
    { data?: LinksState; reason?: string; detail?: string } | null;
  if (!resp.ok) throw new Error(body?.detail || body?.reason || `后端没有采纳这次修改（HTTP ${resp.status}）`);
  if (body?.data) applyLinksState(body.data);
  return body?.reason || '已保存';
}

export function subscribeLinks(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

/** 拼链接的地方用这个：拿到表之后自动重渲染一次（改完配置不用刷新整页）。 */
export function useLinkTemplates(): Record<string, string> | null {
  const [, bump] = useState(0);
  useEffect(() => {
    const off = subscribeLinks(() => bump(n => n + 1));
    void loadLinks();
    return off;
  }, []);
  return templates;
}
