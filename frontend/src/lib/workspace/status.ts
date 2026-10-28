export type ConnectionStatus = 'checking' | 'missing' | 'saved' | 'authenticated' | 'invalid' | 'uninstalled' | 'error';
/** 引擎健康口径只有一个来源：`GET /api/review-agent/status` 的 `installed`（后端实测跑过一遍
 * `node codex.js --version`）。前端不许另判一套 —— 半截装时"文件在"与"跑得起来"会背离，
 * 而背离时页面若自己按"有登录态吗"说话，就会把没装引擎说成"未登录"（2026-09-25 的误判）。*/
export function engineHealth(value: unknown): { status: ConnectionStatus; reason: string } {
  if (!value || typeof value !== 'object') return { status: 'error', reason: '' };
  const data = value as Record<string, unknown>;
  if (typeof data.installed !== 'boolean' || typeof data.subscription_ready !== 'boolean' || typeof data.models_error !== 'string') return { status: 'error', reason: '' };
  if (!data.installed) return { status: 'uninstalled', reason: typeof data.engine_reason === 'string' ? data.engine_reason : '' };
  if (!data.subscription_ready) return { status: 'invalid', reason: '' };
  if (data.models_error) return { status: 'error', reason: data.models_error };
  return { status: 'authenticated', reason: '' };
}
/** Read-only status cannot establish a successful paid model connection. */
export function subscriptionStatus(value: unknown): ConnectionStatus {
  return engineHealth(value).status;
}
export function connectionLabel(status: ConnectionStatus, name: string, previouslyTested = false, reason = ''): string {
  if (status === 'checking') return '正在检测 AI 接入…';
  if (status === 'missing') return '未接入AI，请设置';
  if (status === 'error') return '无法确认 AI 状态，请检查设置';
  if (status === 'uninstalled') return `产品引擎实测跑不起来${reason ? `：${reason}` : ''}（不是登录问题，需重跑 npm ci --prefix runtime）`;
  if (status === 'invalid') return `${name} 未登录或登录失效，请设置`;
  if (previouslyTested) return `${name}（此前连接实测通过）`;
  if (status === 'saved') return `已保存AI：${name}（待实测）`;
  return `已登录：${name}（连接待实测）`;
}
