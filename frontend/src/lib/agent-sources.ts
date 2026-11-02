import { SAVED_PROVIDERS, sourceLabel } from "./ai-access.ts";

export type AgentConnection = { provider: string; model: string; baseURL: string; apiKey: string;
  /** 这一条来源的整条回答总时长（秒，30~600）。留空按 DEFAULT_TIMEOUT_SECONDS。 */
  timeoutSeconds?: number; verifiedAt?: number };
export type TestState = 'untested' | 'passed' | 'failed';
export type SavedSource = { id: string; savedAt: number; testState: TestState; testNote: string; testedAt: number; failures: number } & AgentConnection;

// 连续失败到第 FAIL_LIMIT 次才判定"不通"并让位；默认开启，全部不通时按顺序循环再试。
export const FAIL_LIMIT = 2;
export const DEFAULT_TIMEOUT_SECONDS = 180;
export const TIMEOUT_FLOOR = 30;
export const TIMEOUT_CEILING = 600;
/** 超时口径（用户 2026-10-27 拍）：连接 10 秒、上游 45 秒不吐字节就断、整条回答默认 180 秒。
 *  前两段住在后端 responses_bridge，这一条只说明界面填的是什么，别读成"60 秒一刀切"。 */
export const TIMEOUT_SEGMENTS = "连接 10 秒 · 上游 45 秒不吐字节就断 · 整条回答默认 180 秒（每条可改 30~600）";
export type OrderMode = 'cycle' | 'random';

// 本机保存的多条 AI 来源；列表顺序就是自动切换的优先级。
const legacyKey = "astock-agent-connection";
const listKey = "astock-agent-sources";
const activeKey = "astock-agent-active";
const failoverKey = "astock-agent-failover";
const restartKey = "astock-agent-restart";     // ①每次任务都从列表第一条开始用（默认开）
const orderKey = "astock-agent-order";         // 列表循环 / 随机（默认循环）
const giveUpKey = "astock-agent-giveup";       // ③走完一圈仍不通就报「无法连接」（默认开）
let servedId = "";
let blocked = "";              // 一圈走完都不通时那句给人看的话
let randomOrder: string[] | null = null;   // 本轮的随机顺序；定了就不再变，免得界面每渲染一次就换一个

function isConnection(value: unknown): value is AgentConnection {
  const row = value as AgentConnection;
  return !!row && typeof row === "object" && SAVED_PROVIDERS.includes(row.provider)
    && typeof row.model === "string" && typeof row.baseURL === "string" && typeof row.apiKey === "string";
}
/** 容忍旧版本缺字段：不能因为少一个 failures 就把用户已保存的来源整条丢掉。 */
function normalize(value: unknown): SavedSource | null {
  const row = value as SavedSource;
  if (!isConnection(row) || typeof row.id !== "string") return null;
  const state: TestState = ['untested', 'passed', 'failed'].includes(row.testState) ? row.testState : 'untested';
  const failures = typeof row.failures === 'number' && row.failures > 0 ? Math.floor(row.failures) : (state === 'failed' ? FAIL_LIMIT : 0);
  const seconds = typeof row.timeoutSeconds === 'number' && row.timeoutSeconds >= TIMEOUT_FLOOR && row.timeoutSeconds <= TIMEOUT_CEILING
    ? Math.floor(row.timeoutSeconds) : DEFAULT_TIMEOUT_SECONDS;
  return { provider: row.provider, model: row.model, baseURL: row.baseURL, apiKey: row.apiKey, timeoutSeconds: seconds,
    id: row.id, savedAt: typeof row.savedAt === 'number' ? row.savedAt : Date.now(),
    testState: failures >= FAIL_LIMIT ? 'failed' : (failures > 0 ? 'untested' : state),
    testNote: typeof row.testNote === 'string' ? row.testNote : '', testedAt: typeof row.testedAt === 'number' ? row.testedAt : 0,
    failures };
}
function persist(rows: SavedSource[]) {
  try { localStorage.setItem(listKey, JSON.stringify(rows)); } catch { /* 存储被禁用时本次只在内存生效 */ }
  window.dispatchEvent(new Event("astock-connection-changed"));
}
export function readSources(): SavedSource[] {
  try {
    const stored = JSON.parse(localStorage.getItem(listKey) || "null");
    if (Array.isArray(stored)) return stored.map(normalize).filter((row): row is SavedSource => row !== null);
    const legacy = JSON.parse(localStorage.getItem(legacyKey) || "null");
    if (!isConnection(legacy)) return [];
    const verified = typeof legacy.verifiedAt === 'number' && legacy.verifiedAt > 0 ? legacy.verifiedAt : 0;
    const row: SavedSource = { provider: legacy.provider, model: legacy.model, baseURL: legacy.baseURL, apiKey: legacy.apiKey,
      timeoutSeconds: DEFAULT_TIMEOUT_SECONDS,
      id: crypto.randomUUID(), savedAt: Date.now(), testState: verified ? 'passed' : 'untested', testNote: '', testedAt: verified, failures: 0 };
    localStorage.setItem(activeKey, row.id);
    persist([row]);
    localStorage.removeItem(legacyKey);
    return [row];
  } catch { return []; }
}
export function activeSourceId(): string {
  const rows = readSources();
  const stored = localStorage.getItem(activeKey) || "";
  return rows.some(row => row.id === stored) ? stored : (rows[0]?.id ?? "");
}
export function setActiveSource(id: string) {
  if (!readSources().some(row => row.id === id)) return;
  try { localStorage.setItem(activeKey, id); } catch { return; }
  window.dispatchEvent(new Event("astock-connection-changed"));
}
function on(key: string, fallback: boolean): boolean {
  const stored = localStorage.getItem(key);
  return stored === null ? fallback : stored !== "off";
}
function mark(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { return; }
  window.dispatchEvent(new Event("astock-connection-changed"));
}
export function failoverEnabled(): boolean { return on(failoverKey, true); }
export function setFailoverEnabled(value: boolean) { mark(failoverKey, value ? "on" : "off"); }
/** ①每次重头使用：新任务从列表第一条开始，而不是粘在上一条用熟的那条。默认开。 */
export function restartEachRun(): boolean { return on(restartKey, true); }
export function setRestartEachRun(value: boolean) { randomOrder = null; mark(restartKey, value ? "on" : "off"); }
/** ③走完一圈仍不通就停住并报「无法连接」。默认开。 */
export function giveUpAfterCycle(): boolean { return on(giveUpKey, true); }
export function setGiveUpAfterCycle(value: boolean) { blocked = ""; mark(giveUpKey, value ? "on" : "off"); }
/** 顺序方式：列表循环（默认）/ 随机。与①同时开时随机优先，界面会说清。 */
export function orderMode(): OrderMode { return localStorage.getItem(orderKey) === "random" ? "random" : "cycle"; }
export function setOrderMode(value: OrderMode) { randomOrder = null; mark(orderKey, value); }
const identity = (row: AgentConnection) => [row.provider, row.model, row.baseURL].join("\u0000");
export const sourceIdentity = identity;

/** 保存只写入本机列表；能不能用由「测试连接」和任务结果决定。 */
export function saveAgentConnection(value: AgentConnection): string {
  const rows = readSources();
  const connection: AgentConnection = { provider: value.provider, model: value.model, baseURL: value.baseURL, apiKey: value.apiKey,
    timeoutSeconds: value.timeoutSeconds && value.timeoutSeconds >= TIMEOUT_FLOOR && value.timeoutSeconds <= TIMEOUT_CEILING
      ? Math.floor(value.timeoutSeconds) : DEFAULT_TIMEOUT_SECONDS };
  const existing = rows.find(row => identity(row) === identity(connection));
  if (existing) {
    persist(rows.map(row => row.id === existing.id ? { ...row, ...connection, apiKey: connection.apiKey || row.apiKey } : row));
    return existing.id;
  }
  const row: SavedSource = { ...connection, id: crypto.randomUUID(), savedAt: Date.now(), testState: 'untested', testNote: '', testedAt: 0, failures: 0 };
  persist([...rows, row]);
  try { localStorage.setItem(activeKey, row.id); } catch { /* 只影响下一次读取的默认选中 */ }
  return row.id;
}
export function findSavedSourceId(value: AgentConnection): string {
  return readSources().find(row => identity(row) === identity(value))?.id ?? "";
}
export function removeAgentSource(id: string) {
  const rows = readSources().filter(row => row.id !== id);
  persist(rows);
  if (localStorage.getItem(activeKey) === id) setActiveSource(rows[0]?.id ?? "");
}
export function moveAgentSource(id: string, offset: -1 | 1) {
  const rows = readSources();
  const at = rows.findIndex(row => row.id === id);
  const to = at + offset;
  if (at < 0 || to < 0 || to >= rows.length) return;
  const next = [...rows];
  [next[at], next[to]] = [next[to], next[at]];
  persist(next);
}
/** 一次成败记在对应那条上：成功清零并结束本轮，失败累计一次并登记"这条本轮试过了"。 */
export function noteSourceResult(id: string, ok: boolean, note = "") {
  const rows = readSources();
  if (!rows.some(row => row.id === id)) return;
  persist(rows.map(row => {
    if (row.id !== id) return row;
    const failures = ok ? 0 : row.failures + 1;
    return { ...row, failures, testState: ok ? 'passed' : (failures >= FAIL_LIMIT ? 'failed' : 'untested'),
      testNote: ok ? '' : note.slice(0, 300), testedAt: Date.now() };
  }));
  if (ok) { blocked = ""; randomOrder = null; }                    // 成了就停住状态与随机起点都重来
}
export function clearSourceMark(id: string) {
  persist(readSources().map(row => row.id === id ? { ...row, testState: 'untested', testNote: '', testedAt: 0, failures: 0 } : row));
}
/** 每条来源自己的总时长闸（秒）。超出 30~600 或填不进数字时返回 false，由界面如实说。 */
export function setSourceTimeout(id: string, seconds: number): boolean {
  if (!Number.isFinite(seconds) || seconds < TIMEOUT_FLOOR || seconds > TIMEOUT_CEILING) return false;
  const rows = readSources();
  if (!rows.some(row => row.id === id)) return false;
  persist(rows.map(row => row.id === id ? { ...row, timeoutSeconds: Math.floor(seconds) } : row));
  return true;
}

function shuffled(rows: SavedSource[]): SavedSource[] {
  const out = [...rows];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}
/** 新任务开始时重新起一轮：清掉上一轮"都不通"的停住状态，并让随机重新抽起点。 */
export function beginRound() { blocked = ""; randomOrder = null; }

function cycleRows(rows: SavedSource[]): SavedSource[] {
  if (orderMode() === "random") {
    // 随机只定"这一轮从哪条开始往后走"，定了就不再变：界面每次渲染都会读一次当前来源，
    // 跟着变的话屏幕上那条"本次任务会用"会闪。列表换人（增删）时重排。
    const ids = rows.map(row => row.id);
    if (!randomOrder || randomOrder.length !== ids.length || !ids.every(id => randomOrder!.includes(id))) {
      randomOrder = shuffled(rows).map(row => row.id);
    }
    return randomOrder.map(id => rows.find(row => row.id === id)!);
  }
  if (restartEachRun()) return rows;                          // 每次重头：第 1 条开始
  const at = Math.max(0, rows.findIndex(row => row.id === activeSourceId()));
  return [...rows.slice(at), ...rows.slice(0, at)];           // 否则从当前那条往后顺位
}

/** 本次该用哪一条。读的时候不改状态（界面渲染也会调它），所以"本轮试过谁"只由
 *  `reportAgentOutcome()` 在真失败时登记、成功或新任务开始时清零。 */
export function currentSource(): SavedSource | null {
  const rows = readSources();
  if (!rows.length) { servedId = ""; blocked = ""; return null; }
  const active = rows.find(row => row.id === activeSourceId()) ?? rows[0];
  if (!failoverEnabled()) { servedId = active.id; blocked = ""; return active; }
  const order = cycleRows(rows);
  const next = order.find(row => row.failures < FAIL_LIMIT);   // 只跳过"已判定不通"的
  if (next) { servedId = next.id; blocked = ""; return next; }
  if (!giveUpAfterCycle()) {
    // 关掉③就退回旧行为：不猜死，挑累计失败最少、最久没被试的那条继续轮转。
    const oldest = [...rows].sort((a, b) => a.failures - b.failures || a.testedAt - b.testedAt || a.savedAt - b.savedAt)[0];
    servedId = oldest.id; blocked = "";
    return oldest;
  }
  servedId = "";
  blocked = `${rows.length} 条来源已各试过一圈，都不通：`
    + rows.map((row, index) => `第 ${index + 1} 条 ${sourceLabel(row)}（${row.testNote || '连接失败'}）`).join('、')
    + `。已停住，不再自动重试；请逐条「测试连接」确认地址、密钥与模型，或用「清除测试标记」把某条重新放回轮换。`;
  return null;
}
/** 一圈走完都不通时给人看的那句话；没有停住时是空串。 */
export function blockedReason(): string { return blocked; }
/** 这一条来源的总时长闸（秒），交后端 `connection()` 校验与透传。 */
export function servedTimeoutSeconds(): number {
  const row = servedId ? readSources().find(item => item.id === servedId) : null;
  return row?.timeoutSeconds ?? DEFAULT_TIMEOUT_SECONDS;
}
// Verification metadata is opt-in for the status display, never a model request field.
export function loadAgentConnection(includeVerification = false): AgentConnection | null {
  const row = currentSource();
  if (!row) return null;
  const connection: AgentConnection = { provider: row.provider, model: row.model, baseURL: row.baseURL,
    apiKey: row.apiKey, timeoutSeconds: row.timeoutSeconds ?? DEFAULT_TIMEOUT_SECONDS };
  return includeVerification && row.testState === 'passed' && row.testedAt > 0 && row.testedAt <= Date.now()
    ? { ...connection, verifiedAt: row.testedAt } : connection;
}
export function servedSourceId() { return servedId; }

// 与 review_agent/runtime.py 的 safe_engine_error()、responses_bridge.status_message()
// 同步：只有"这个来源本身没服务成功"才算故障；上下文超限、本机存储与后端访问密钥不换来源。
const SOURCE_FAULT = /(限流|额度|密钥验证失败|网络连接中断|暂时无法访问|接口拒绝了请求|AI 请求失败|渠道不可用|暂时不可用|没有该模型|未提交通过校验|未通过检查|Agent 超时)/;
const NOT_SOURCE_FAULT = /(上下文超过模型上限|后端访问密钥|浏览器禁止会话存储|释放存储空间|请求过大|任务已取消|已取消)/;

/** 任务或测试结束回报：来源故障累计一次，成功则清零。 */
export function reportAgentOutcome(error = "") {
  if (!servedId) return;
  if (!error) return noteSourceResult(servedId, true);
  if (NOT_SOURCE_FAULT.test(error) || !SOURCE_FAULT.test(error)) return;
  noteSourceResult(servedId, false, error);
}
