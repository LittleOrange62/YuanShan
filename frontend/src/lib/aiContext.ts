/**
 * 「AI分析」这道题的**素材拼装**：把页面接口返回的 JSON 压成模型读得懂的文本。
 *
 * 单独一个叶子模块（不 import 任何带副作用的东西）有两个原因：
 *  1. 这一段是纯逻辑，最容易也最值得逐条测；
 *  2. `node --test` 只能剥类型、不能解析别名路径与参数属性，链上挂一个 `lib/api.ts`
 *     就把测试拖死了 —— 取数交给调用方注入，这里只管拼装。
 */

export interface Expansion { from: string; pick: string; top: number; then: string; limit?: number }
export interface ContextSpec { sources: string[]; expand?: Expansion }
/** 读一个只读接口，读不到就抛错（错误消息会原样进素材，如实写"未取到"）。 */
export type Reader = (path: string) => Promise<unknown>

/** 后端 `chatStream` 的 context 上限是 8000，留 500 余量。 */
export const CONTEXT_BUDGET = 7500;
/** 单个页面级源最多占这么多字，超了砍自己，不许挤掉后面的个股明细。 */
export const HEAD_CAP = 600;

/**
 * 预算怎么分的（2026-09-27 实测踩过）：原来一律从头截 7500 字，于是排在**末尾**的
 * 个股明细整段被砍光，模型回一句"材料里没有任何个股数字"，一次真调用就这么白烧掉。
 * 现在：页面级源每条只占一小截，**剩下的预算整块留给个股明细**，按板块均分 ——
 * 宁可每段少列几只，也不许整段消失。
 */
export async function buildContext(spec: ContextSpec, read: Reader): Promise<string> {
  const head: string[] = [];
  const raw = new Map<string, unknown>();
  for (const path of spec.sources) {
    try {
      const data = await read(path);
      raw.set(path, data);
      head.push(`【${path}】\n${summarize(data).slice(0, HEAD_CAP)}`);
    } catch (error) {
      head.push(`【${path}】未取到：${reason(error)}`);
    }
  }
  let body = '';
  const exp = spec.expand;
  if (exp) {
    const rows = (raw.get(exp.from) as { rows?: Record<string, unknown>[] } | undefined)?.rows ?? [];
    const keys = rows.slice(0, exp.top).map(r => String(r[exp.pick] ?? '')).filter(Boolean);
    if (keys.length === 0) {
      body = `【${exp.then}…】上一级榜没给出可展开的板块，个股明细未取到`;
    } else {
      const per = Math.max(600, Math.floor((CONTEXT_BUDGET - head.join('\n\n').length) / keys.length));
      const chunks: string[] = [];
      for (const key of keys) {
        const label = `【${exp.then}${key}】`;
        try {
          chunks.push(`${label}\n${renderMembers(await read(`${exp.then}${encodeURIComponent(key)}&limit=${exp.limit ?? 40}`)).slice(0, per)}`);
        } catch (error) {
          chunks.push(`${label}未取到：${reason(error)}`);
        }
      }
      body = chunks.join('\n\n');
    }
  }
  return `${head.join('\n\n')}\n\n${body}`.slice(0, CONTEXT_BUDGET);
}

function reason(error: unknown): string {
  return error instanceof Error && error.message ? error.message : '接口无响应';
}

/** 只留读得懂的事实：日期、榜上前若干名与关键字段，砍掉 trace/空数组这类噪音。 */
export function summarize(value: unknown, depth = 0): string {
  if (value === null || typeof value !== 'object') return String(value ?? '');
  if (Array.isArray(value)) {
    const keep = value.slice(0, 12).map(v => summarize(v, depth + 1)).filter(s => s !== '');
    return `${value.length} 条：${keep.join(' | ')}${value.length > 12 ? ' …' : ''}`;
  }
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, v]) => v !== null && v !== '' && !(Array.isArray(v) && v.length === 0))
    .map(([k, v]) => `${k}=${Array.isArray(v) || typeof v === 'object' ? summarize(v, depth + 1) : String(v)}`);
  return entries.join(depth > 1 ? ', ' : '\n  ');
}

/** 模型分层要用的就这几列，全量字段（15 列 × 40 行）会把预算吃空。 */
const MEMBER_FIELDS = ['code', 'name', 'close', 'pct_change', 'pos60', 'gain60',
  'turnover_rate_percent', 'float_mcap_yi', 'sealed', 'bars'] as const;

export function renderMembers(data: unknown): string {
  const d = data as { date?: string; sector_name?: string; total_members?: number;
    rows?: Record<string, unknown>[]; caveats?: { text?: string }[] };
  const rows = d.rows ?? [];
  const head = `${d.sector_name ?? '板块名未给'}（${d.date ?? '日期未给'}，成分 ${d.total_members ?? rows.length} 只，本次交出 ${rows.length} 行，按 60 日位置从低到高排）`;
  const lines = rows.map(r => MEMBER_FIELDS.map(k => `${k}=${r[k] ?? 'null'}`).join(' '));
  const caveat = (d.caveats ?? []).map(c => c.text).filter(Boolean).join('；').slice(0, 160);
  return [head, ...lines, caveat ? `口径：${caveat}` : '口径：后端未给 caveats'].join('\n');
}
