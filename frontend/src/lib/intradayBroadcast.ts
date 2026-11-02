import { apiUrl } from './base.ts';
import { authHeaders } from './api.ts';
import type { BroadcastConfig, BroadcastState } from './intradayBroadcastReads.ts';

/**
 * 「盘中播报」那一屏的 I/O 层（判据与类型在 `intradayBroadcastReads.ts`，那份不接 I/O、
 * 所以 `node --test` 钉得住）。这里只做两件事：拉状态、发修改。
 *
 * 后端拒绝时的原话要原样抛给调用方 —— "这一笔整条没写入，仍按原值运行"那句很重要，
 * 折成"保存失败"就等于让人以为改动可能生效了一半。
 */

export async function loadBroadcast(): Promise<BroadcastState | null> {
  try {
    const resp = await fetch(apiUrl('/api/intraday/broadcast'), { headers: authHeaders() });
    if (!resp.ok) return null;
    const body = await resp.json() as { data?: BroadcastState } | null;
    return body?.data ?? null;
  } catch {
    return null;
  }
}

/**
 * 保存一组键并（默认）按新配置重注册计划任务。
 * `reregister=false` 只留一个正当用途：先定配置、等收盘后再对齐机器 —— 返回的
 * `reason` 会当场写明"这一笔没有动任务计划程序"，不许让人以为时点已经生效。
 */
export async function saveBroadcast(settings: BroadcastConfig, reregister = true):
  Promise<{ reason: string; data: BroadcastState | null }> {
  const resp = await fetch(apiUrl('/api/intraday/broadcast'), {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ settings, reregister }),
  });
  const body = await resp.json().catch(() => null) as
    { data?: BroadcastState; reason?: string; detail?: string } | null;
  if (!resp.ok) {
    throw new Error(body?.detail || body?.reason || `后端没有采纳这次修改（HTTP ${resp.status}）`);
  }
  return { reason: body?.reason || '已保存', data: body?.data ?? null };
}
