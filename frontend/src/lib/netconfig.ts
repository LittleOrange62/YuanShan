import { apiUrl } from './base.ts';
import { authHeaders } from './api.ts';

/**
 * 局域网访问（网络边界）的取数层。判定与落盘都在后端 `netconfig.py`，这里只转述。
 *
 * 两条必须原样带到界面上的事实（少一条就会误判"我设了但没生效"）：
 *  · **配置说的 ≠ 进程实际绑的**：绑定地址在启动那一刻定死，勾完开关必须重启；
 *    而**加白/删白每个请求现读，立刻生效**。两种"生效时机"不一样，不能都写成"重启后生效"。
 *  · **只有回环能改这道闸**（`can_edit`）：被放行的那台机器如果能自己加白，白名单形同没有。
 */
export interface NetState {
  generated_at: string | null;
  lan_enabled: boolean;
  allowlist: string[];
  config_path: string | null;
  config_warning: string | null;
  /** 白名单最后一次改动：谁、从哪条路。多出没人认领的地址时，这是唯一线索。 */
  changed_at: string | null;
  changed_by: string | null;
  running_host: string | null;
  running_port: number | null;
  running_lan_open: boolean;
  should_bind: string | null;
  needs_restart: boolean;
  lan_urls: string[];
  loopback_url: string | null;
  can_edit: boolean;
  edit_reason: string | null;
  note: string | null;
  recent: { client: string; reason: string; path: string; at: string }[];
  total: number;
}

export const emptyNetState = (): NetState => ({
  generated_at: null, lan_enabled: false, allowlist: [], config_path: null,
  config_warning: null, changed_at: null, changed_by: null,
  running_host: null, running_port: null, running_lan_open: false,
  should_bind: null, needs_restart: false, lan_urls: [], loopback_url: null,
  can_edit: false, edit_reason: null, note: null, recent: [], total: 0,
});

export async function loadNetState(): Promise<NetState> {
  let resp: Response;
  try {
    resp = await fetch(apiUrl('/api/net'), { headers: authHeaders() });
  } catch {
    throw new Error('连不到后端，读不到网络边界现状（不是"没开放"，是读不到）');
  }
  const body = await resp.json().catch(() => null) as { data?: NetState } | null;
  if (!resp.ok) throw new Error(body && typeof (body as { detail?: string }).detail === 'string'
    ? (body as { detail?: string }).detail as string : `HTTP ${resp.status}`);
  const data = body?.data;
  if (!data || typeof data !== 'object') throw new Error('后端返回的形状不是对象，按读不到处理');
  return { ...emptyNetState(), ...data, recent: Array.isArray(data.recent) ? data.recent : [],
    allowlist: Array.isArray(data.allowlist) ? data.allowlist : [] };
}

/** 一次只改一件事（后端也这么要求）：开关 / 加一条 / 删一条。 */
export async function patchNet(what: 'lan_enabled' | 'add' | 'remove',
  value: boolean | string): Promise<NetState> {
  const payload = what === 'lan_enabled' ? { lan_enabled: value as boolean } : { [what]: value };
  const resp = await fetch(apiUrl('/api/net'), {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await resp.json().catch(() => null) as { data?: NetState; detail?: string } | null;
  if (!resp.ok) {
    throw new Error(body?.detail || `后端拒绝本次修改（HTTP ${resp.status}）`);
  }
  return { ...emptyNetState(), ...(body?.data as NetState),
    recent: Array.isArray(body?.data?.recent) ? body.data.recent : [],
    allowlist: Array.isArray(body?.data?.allowlist) ? body.data.allowlist : [] };
}
