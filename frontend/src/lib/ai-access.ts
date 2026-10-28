// UI presets share the existing Responses adapter; saved backend identities stay compatible.
type Connection = { provider: string; model: string; baseURL: string; apiKey: string };
export type AccessDraft = Connection;
const bailian = 'https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1';
const hosted = '通过阿里云百炼接入，请使用百炼工作空间的地址和密钥。';
export const SUBSCRIPTION_PROVIDERS = [
  { id: 'codex-private', name: 'Codex 订阅', detail: '使用 ChatGPT 账户登录产品专用空间' },
  { id: 'claude', name: 'Claude 订阅', detail: '使用本机 Claude Code 登录' },
  { id: 'codebuddy', name: 'WorkBuddy 订阅', detail: '使用 WorkBuddy 内置 CodeBuddy 登录；macOS 已完成单日复盘流程实测' },
];
const preset = (id: string, name: string, baseURL: string, models: string[], detail = '填写该服务商的 API 密钥，可按账户权限修改模型。', apiKey = '') =>
  ({ id, name, baseURL, models: models.map(id => ({ id, name: id })), detail, apiKey });
// 本机自用默认值。密钥会随构建进入 frontend/dist 的静态文件：
// 分享源码、提交仓库或换机器之前，把 LOCAL_GATEWAY.key 清空再打包。
const LOCAL_GATEWAY = {
  provider: 'new-api',
  baseURL: 'http://192.168.176.68:3000/v1',
  model: 'allmlmlh',
  key: '',
};
export const DEFAULT_ACCESS_PROVIDER = LOCAL_GATEWAY.provider;
// Same starting choices as Research. A preset is not a successful connection test.
export const CHAT_PROVIDERS = ['new-api', 'openai-compat'];
export const API_PROVIDERS = [
  preset('deepseek', 'DeepSeek', 'https://api.deepseek.com', ['deepseek-v4-flash', 'deepseek-v4-pro']),
  preset('mimo', 'MiMo', 'https://token-plan-cn.xiaomimimo.com/v1', ['mimo-v2.5', 'mimo-v2.5-pro']),
  preset('glm', '智谱 GLM', bailian, ['glm-5.2'], hosted),
  preset('kimi', 'Kimi', bailian, ['kimi-k2.7-code'], hosted),
  preset('qwen', '通义千问', bailian, ['qwen3.8-max'], hosted),
  preset('openai', 'OpenAI', 'https://api.openai.com/v1', ['gpt-4o']),
  preset('silicon', '硅基流动', 'https://api.siliconflow.cn/v1', ['deepseek-ai/DeepSeek-V3']),
  preset('minimax', 'MiniMax', 'https://api.minimaxi.com/v1', ['MiniMax-M2']),
  preset('openrouter', 'OpenRouter', 'https://openrouter.ai/api/v1', ['openai/gpt-4o']),
  preset('groq', 'Groq', 'https://api.groq.com/openai/v1', ['llama-3.3-70b-versatile']),
  preset('together', 'Together', 'https://api.together.xyz/v1', ['meta-llama/Llama-3.3-70B-Instruct-Turbo']),
  preset('new-api', 'new-api 网关', LOCAL_GATEWAY.baseURL, [LOCAL_GATEWAY.model],
    '自建 new-api / One API 网关：本机或内网填 http://192.168.x.x:3000/v1，公网地址必须用 https；模型标识以该渠道为准。已按本机网关预填，改完仍需自己点一次连接测试。', LOCAL_GATEWAY.key),
  preset('openai-compat', 'OpenAI 兼容模式', '', [], '任何只提供 Chat Completions 的 OpenAI 兼容端点：地址通常以 /v1 结尾，明文 http 仅限本机与内网 IP；模型标识以账户为准。'),
  preset('api-compatible', '自定义', '', [], '填写支持 Responses 与工具调用的服务地址和模型标识。'),
];
export function isSubscription(provider: string): boolean {
  return SUBSCRIPTION_PROVIDERS.some(p => p.id === provider);
}
export function isChatBridge(provider: string): boolean {
  return CHAT_PROVIDERS.includes(provider);
}
// Mirror of private_host() in review_agent/runtime.py; keep both in step.
export function privateHost(host: string): boolean {
  const name = host.replace(/^\[|\]$/g, '').toLowerCase();
  if (name === 'localhost') return true;
  const v4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(name);
  if (v4 && v4.slice(1).every(part => Number(part) < 256)) {
    const [a, b] = [Number(v4[1]), Number(v4[2])];
    return a === 127 || a === 10 || a === 169 && b === 254 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
  }
  return /^f[cd][0-9a-f:]+$/.test(name);
}
// Only ids connectionFor can actually produce; the local store must not accept a raw preset id.
export const SAVED_PROVIDERS = ['codex-private', 'claude', 'codebuddy', 'openai', 'mimo', 'api-compatible', ...CHAT_PROVIDERS];
function cleanURL(base: string): string {
  return base.trim().replace(/\/+$/, '');
}
export function providerFor(connection: Connection): string {
  if (isSubscription(connection.provider)) return connection.provider;
  if (isChatBridge(connection.provider)) return connection.provider;
  const base = cleanURL(connection.baseURL);
  const exact = API_PROVIDERS.find(p => p.baseURL && p.baseURL !== bailian && base === p.baseURL);
  if (exact) return exact.id;
  if (/^https:\/\/[a-zA-Z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com\/compatible-mode\/v1$/.test(base)) {
    if (connection.model.startsWith('glm-')) return 'glm';
    if (connection.model.startsWith('kimi-')) return 'kimi';
    if (connection.model.startsWith('qwen')) return 'qwen';
  }
  return 'api-compatible';
}
export function draftFor(provider: string, saved?: Connection | null): AccessDraft {
  if (saved && providerFor(saved) === provider) return { provider, model: saved.model, baseURL: saved.baseURL, apiKey: isSubscription(provider) ? '' : saved.apiKey };
  const entry = API_PROVIDERS.find(p => p.id === provider);
  return { provider, model: entry?.models[0]?.id ?? (['claude', 'codebuddy'].includes(provider) ? 'default' : ''), baseURL: entry?.baseURL ?? '', apiKey: entry?.apiKey ?? '' };
}
export function connectionFor(draft: AccessDraft): Connection {
  if (isSubscription(draft.provider)) return { provider: draft.provider, model: draft.model.trim(), baseURL: '', apiKey: '' };
  const baseURL = cleanURL(draft.baseURL);
  // Chat-compatible gateways have no fixed host, so the chosen option is the identity.
  const provider = isChatBridge(draft.provider) ? draft.provider
    : baseURL === 'https://api.openai.com/v1' ? 'openai' : baseURL === 'https://token-plan-cn.xiaomimimo.com/v1' ? 'mimo' : 'api-compatible';
  return { provider, model: draft.model.trim(), baseURL, apiKey: draft.apiKey.trim() };
}
export function sourceLabel(connection: Connection): string {
  const provider = providerFor(connection);
  return SUBSCRIPTION_PROVIDERS.find(p => p.id === provider)?.name ?? `${API_PROVIDERS.find(p => p.id === provider)?.name ?? '自定义'} API`;
}
export function draftError(draft: AccessDraft): string {
  if (!/^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$/.test(draft.model.trim())) return '请填写有效的模型标识';
  if (isSubscription(draft.provider)) return '';
  const chat = isChatBridge(draft.provider);
  const base = draft.baseURL.trim();
  if (/[{}<>]|%7b|%7d|%3c|%3e/i.test(base)) return '请将地址中的 WorkspaceId 等占位符替换为实际工作空间信息';
  try {
    const url = new URL(base);
    // WHATWG parsing repairs 'https:/example.com' into a valid host, so the
    // literal scheme separator still has to be checked.
    if (!/^https?:\/\//.test(base)) throw new Error();
    // Self-hosted gateways may be plain http on this machine or the LAN; anything else needs TLS.
    const plainAllowed = chat && url.protocol === 'http:' && privateHost(url.hostname);
    if (!url.hostname || url.username || url.password || url.search || url.hash || /\s|\\/.test(base)) throw new Error();
    if (!plainAllowed && (url.protocol !== 'https:' || url.port)) throw new Error();
  } catch { return chat ? '明文 http 只允许本机或内网 IP 地址，公网服务请填写 HTTPS 地址；地址中不要带账号、查询参数' : '请填写 HTTPS API 基础地址，不含账号、查询参数或非标准端口'; }
  const key = draft.apiKey.trim();
  if (!key || key.length > 1024 || /\s/.test(key)) return '请填写该服务商的有效 API 密钥';
  return '';
}
