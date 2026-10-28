/** 个人微信推送的取数层：只有 fetch 与信封拆装，判据都在 `wxpushRules.ts`。
 *
 * 分成两个文件的原因与 `notify.ts` / `notifyRules.ts` 一样：
 * 这个文件引 `api.ts`，判据层不引 —— 那样 `node --test` 才能直接驱动判据而不拖进浏览器依赖。
 */
import { apiUrl } from './base.ts';
import { authHeaders } from './api.ts';
import { normalizeState, type WxState } from './wxpushRules.ts';

export * from './wxpushRules.ts';

export interface SendResult { data: Record<string, unknown>; reason: string }

async function jsonOrThrow(resp: Response): Promise<Record<string, unknown>> {
  const body = await resp.json().catch(() => null) as Record<string, unknown> | null;
  if (!resp.ok) {
    throw new Error(typeof body?.detail === 'string' ? body.detail
      : `后端拒绝（HTTP ${resp.status}）`);
  }
  if (!body || typeof body !== 'object') throw new Error('回体不是对象，按读不到处理');
  return body;
}

export async function loadWxPush(): Promise<WxState> {
  let resp: Response;
  try {
    resp = await fetch(apiUrl('/api/wxpush'), { headers: authHeaders() });
  } catch {
    // 「连不到后端」与「一条都没配」是两件事，混起来就会让人去检查配置而问题在服务没起
    throw new Error('连不到后端，读不到微信推送配置（不是「没配」，是读不到）');
  }
  const body = await jsonOrThrow(resp);
  if (!body.data || typeof body.data !== 'object') throw new Error('回体里没有 data 对象，按读不到处理');
  return normalizeState(body.data as Partial<WxState>);
}

/** 一次只改一件事（后端 `wxpush_update` 会拒一次改多件，与 `/api/notify`、`/api/net` 同一套规矩）。
 *  后端那句 `reason` 原样带回去显示，不在前端重抄一句「已保存」。 */
export async function patchWxPush(what: string, value: unknown):
  Promise<{ data: WxState; reason: string }> {
  const resp = await fetch(apiUrl('/api/wxpush'), {
    method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ [what]: value }),
  });
  const body = await jsonOrThrow(resp);
  if (!body.data) throw new Error('改完了但后端没回新状态，按读不到处理（别在页面上假装成功）');
  return { data: normalizeState(body.data as Partial<WxState>),
    reason: typeof body.reason === 'string' ? body.reason : '已保存' };
}

/** 发送功能点：`dryRun=true` 后端**一个键都不按**，只回正文 + 预检 + 动作序列。
 *  这也是给其他程序用的那一个入口（POST /api/wxpush/send）。 */
export async function sendWxPush(payload: Record<string, unknown>, dryRun: boolean):
  Promise<SendResult> {
  const resp = await fetch(apiUrl('/api/wxpush/send'), {
    method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, dry_run: dryRun }),
  });
  const body = await jsonOrThrow(resp);
  return { data: (body.data || {}) as Record<string, unknown>,
    reason: typeof body.reason === 'string' ? body.reason : '' };
}
