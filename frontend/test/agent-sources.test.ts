import test from 'node:test';
import assert from 'node:assert/strict';

// agent-sources 读写浏览器存储，先把最小环境装上再导入。
const store = new Map<string, string>();
Object.defineProperty(globalThis, 'localStorage', { value: {
  getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
  setItem: (key: string, value: string) => { store.set(key, String(value)); },
  removeItem: (key: string) => { store.delete(key); },
} });
Object.defineProperty(globalThis, 'window', { value: { addEventListener() {}, removeEventListener() {}, dispatchEvent() {} }, configurable: true });
const { readSources, saveAgentConnection, setActiveSource, activeSourceId, failoverEnabled, setFailoverEnabled,
  loadAgentConnection, noteSourceResult, clearSourceMark, moveAgentSource, removeAgentSource, reportAgentOutcome, FAIL_LIMIT } =
  await import('../src/lib/agent-sources.ts');

const first = { provider: 'new-api', model: 'allmlmlh', baseURL: 'http://192.168.176.68:3000/v1', apiKey: 'KEY_A' };
const second = { provider: 'openai-compat', model: 'deepseek-chat', baseURL: 'https://gw.example.com/v1', apiKey: 'KEY_B' };
const reset = () => { store.clear(); };
const model = () => loadAgentConnection()!.model;
const failures = (id: string) => readSources().find(row => row.id === id)!.failures;

test('the old single saved connection becomes the first entry of the list', () => {
  reset();
  localStorage.setItem('astock-agent-connection', JSON.stringify({ ...first, verifiedAt: 1234 }));
  const rows = readSources();
  assert.equal(rows.length, 1);
  assert.equal(rows[0].testState, 'passed');
  assert.equal(rows[0].failures, 0);
  assert.equal(rows[0].testedAt, 1234);
  assert.equal(localStorage.getItem('astock-agent-connection'), null, '旧键迁移后必须移除，避免两份真相');
  assert.deepEqual(loadAgentConnection(), { provider: first.provider, model: first.model, baseURL: first.baseURL, apiKey: 'KEY_A' });
});

test('auto switch is on by default and can be turned off', () => {
  reset();
  assert.equal(failoverEnabled(), true);
  setFailoverEnabled(false);
  assert.equal(failoverEnabled(), false);
  setFailoverEnabled(true);
});

test('a source keeps its place until it fails twice, then the next one takes over', () => {
  reset();
  const a = saveAgentConnection(first);
  const b = saveAgentConnection(second);
  setActiveSource(a);
  assert.equal(FAIL_LIMIT, 2);
  noteSourceResult(a, false, 'AI 请求限流，请稍后重试');
  assert.equal(failures(a), 1);
  assert.equal(model(), 'allmlmlh', '第一次失败先原地重试，不换来源');
  assert.equal(readSources().find(row => row.id === a)!.testState, 'untested');
  noteSourceResult(a, false, 'AI 请求限流，请稍后重试');
  assert.equal(failures(a), 2);
  assert.equal(readSources().find(row => row.id === a)!.testState, 'failed');
  assert.equal(model(), 'deepseek-chat', '连续两次不通才让位给下一条');
  noteSourceResult(a, true);
  assert.equal(failures(a), 0);
  assert.equal(model(), 'allmlmlh', '一旦成功，计数清零并回到它');
  assert.equal(b, saveAgentConnection(second));
  clearSourceMark(a);
  assert.equal(readSources().find(row => row.id === a)!.testState, 'untested');
});

test('when every source is down the list keeps cycling instead of giving up', () => {
  reset();
  const a = saveAgentConnection(first);
  const b = saveAgentConnection(second);
  setActiveSource(a);
  for (const id of [a, b]) { noteSourceResult(id, false, 'AI 网络连接中断'); noteSourceResult(id, false, 'AI 网络连接中断'); }
  assert.equal(failures(a), 2);
  assert.notEqual(model(), undefined);
  const picked = model();
  noteSourceResult(readSources().find(row => row.model === picked)!.id, false, 'AI 网络连接中断');
  assert.notEqual(model(), picked, '都不通时按顺序换一条再试，不会永远卡在同一条上');
});

test('only source-side faults count as a failure', () => {
  reset();
  saveAgentConnection(first);
  assert.equal(model(), 'allmlmlh', '先读一次，让本次任务记住用的是哪条');
  reportAgentOutcome('AI 上下文超过模型上限，请新建会话并缩小问题范围');
  assert.equal(failures(activeSourceId()), 0, '上下文超限不是来源的毛病');
  reportAgentOutcome('请在接入 AI 页面填写后端访问密钥（VR_API_KEY）');
  assert.equal(failures(activeSourceId()), 0);
  reportAgentOutcome('AI 网络连接中断，请稍后重试；此前成功结果已保留');
  assert.equal(failures(activeSourceId()), 1);
  reportAgentOutcome('');
  assert.equal(failures(activeSourceId()), 0);
  assert.equal(readSources()[0].testState, 'passed', '任务真实跑通即证明这条来源可用');
});

test('saving dedupes, keeps order and never claims a test passed', () => {
  reset();
  const a = saveAgentConnection(first);
  const b = saveAgentConnection(second);
  assert.equal(readSources().map(row => row.id).join(), [a, b].join());
  assert.equal(readSources()[1].testState, 'untested');
  assert.equal(readSources()[1].failures, 0);
  assert.equal(saveAgentConnection({ ...second, apiKey: 'KEY_B2' }), b, '同一份配置再保存应原位更新');
  assert.equal(readSources().length, 2);
  assert.equal(activeSourceId(), b, '新保存的来源成为当前项');
  moveAgentSource(b, -1);
  assert.equal(readSources()[0].id, b);
  removeAgentSource(a);
  assert.equal(readSources().length, 1);
});

test('a list written by an older build keeps its rows after the failures field appeared', () => {
  reset();
  const a = saveAgentConnection(first);
  const legacy = JSON.parse(localStorage.getItem('astock-agent-sources')!);
  legacy[0].testState = 'failed';
  delete legacy[0].failures;
  localStorage.setItem('astock-agent-sources', JSON.stringify(legacy));
  const rows = readSources();
  assert.equal(rows.length, 1);
  assert.equal(rows[0].id, a);
  assert.equal(rows[0].failures, 2, '旧的 failed 标记等价于已到达失败上限');
});
