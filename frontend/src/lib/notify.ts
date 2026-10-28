/** 消息推送的取数与判据层（纯函数在这里，`node --test` 能直接驱动 —— 所以这个文件不写 class）。
 *
 * 分工：配置、校验、到点判定都在后端 `notifyconfig.py`；内容组装与真发送在
 * `duanxian/notify.py`（那里有"pytest 进程里硬挡真发"那道闸）。
 * 这里只做三件事：读、按后端给的目录渲染控件、把"能不能发/下一次什么时候发"说成人话。
 *
 * ⚠️ 两条不能省的纪律：
 *  · 凭据**只写不读** —— 后端交回来的永远是 `url_masked`，这里也不要有任何地方把真串拼回去；
 *  · 渠道/场景/规则/分组的候选项**全部来自后端 catalog**，不在前端写死一份 ——
 *    写死那份迟早与库里不同，用户勾的就是个不存在的东西。
 */
import { apiUrl } from './base.ts';
import { authHeaders } from './api.ts';

export * from './notifyRules.ts';
import {
  type NotifyPlan, type NotifyState, type PlanPayload,
} from './notifyRules.ts';

function normalize(data: NotifyState): NotifyState {
  return { ...data, channels: Array.isArray(data.channels) ? data.channels : [],
    plans: Array.isArray(data.plans) ? data.plans : [],
    history: Array.isArray(data.history) ? data.history : [] };
}

export async function loadNotify(): Promise<NotifyState> {
  let resp: Response;
  try {
    resp = await fetch(apiUrl('/api/notify'), { headers: authHeaders() });
  } catch {
    throw new Error('连不到后端，读不到推送配置（不是"没配渠道"，是读不到）');
  }
  const body = await resp.json().catch(() => null) as { data?: NotifyState } | null;
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  if (!body?.data || typeof body.data !== 'object') throw new Error('形状不是对象，按读不到处理');
  return normalize(body.data);
}

/** 一次只改一件事（后端 `notify_update` 会拒一次改多件，与 `/api/net`、收盘链同一套规矩）。
 * 返回里带上后端那句 `reason`：面板要把"到底改成了什么、还要不要重启"原话说给用户，
 * 自己在前端编一句"已保存"就是第二份措辞 —— 那种句子迟早和后端不一致。 */
export async function patchNotify(what: string, value: unknown):
  Promise<{ data: NotifyState; reason: string }> {
  const resp = await fetch(apiUrl('/api/notify'), {
    method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ [what]: value }),
  });
  const body = await resp.json().catch(() => null) as
    { data?: NotifyState; detail?: string; reason?: string } | null;
  if (!resp.ok) throw new Error(body?.detail || `后端拒绝本次修改（HTTP ${resp.status}）`);
  if (!body?.data) throw new Error('改完了但后端没回新状态，按读不到处理（别在页面上假装成功）');
  return { data: normalize(body.data), reason: body.reason || '已保存' };
}

export async function previewPlan(plan: NotifyPlan | PlanPayload | Record<string, unknown>,
                                  date = ""): Promise<Record<string, unknown>> {
  // 面板把**计划行**原样传进来，而那行的 `channels` 是带 `exists/usable` 的引用对象；
  // 发出去前先换成键 —— 否则预览会拿对象当渠道名去查，回一句"没有这条渠道"。
  const raw = (plan as { channels?: unknown[] }).channels;
  const payload = Array.isArray(raw)
    ? { ...plan, channels: raw.map((c) => (typeof c === 'object' && c ? (c as { key?: string }).key : c)) }
    : plan;
  const resp = await fetch(apiUrl('/api/notify/preview'), {
    method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan: payload, date }),
  });
  const body = await resp.json().catch(() => null) as { data?: Record<string, unknown> } | null;
  if (!resp.ok) throw new Error((body as { detail?: string } | null)?.detail || `HTTP ${resp.status}`);
  return body?.data || {};
}
