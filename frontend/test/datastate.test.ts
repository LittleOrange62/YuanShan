import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { ageText, dash, describeState, errorText, hasData, NOT_WIRED_COPY, num, withUnit, NULL_TEXT } from '../src/lib/datastate.ts';

/** 源码路径按本文件位置解析：从仓库根 `node --test frontend/test/*.test.ts`
 *  与从 frontend/ 里跑，都必须同一条命令成立（README 记的是前者）。 */
const SRC = new URL('../src/', import.meta.url).pathname.replace(/^\//, '');

/**
 * 这一份是「新页面上那些格子为什么长这样」的钉子。
 * 全部打在零 import 的纯函数上 —— 页面组件可以随便改样式，这几条语义不能漂。
 */

test('dash：空值成 —，但 0 与 false 是有效值', () => {
  assert.equal(dash(null), NULL_TEXT);
  assert.equal(dash(undefined), NULL_TEXT);
  assert.equal(dash('  '), NULL_TEXT);
  assert.equal(dash(0), '0');
  assert.equal(dash(false), 'false');
});

test('withUnit：单位只按后端给的翻译，none 是"明确无单位"不是未知', () => {
  assert.equal(withUnit(2.89, 'pct', 2), '2.89%');
  assert.equal(withUnit(12.5, 'yuan', 2), '12.50元');
  assert.equal(withUnit(3, 'count', 0), '3个');
  assert.equal(withUnit(93.5, 'ratio', 1), '93.5');
  assert.equal(withUnit(-0.0046, 'none', 4), '-0.0046', 'none 不能拼成 "-0.0046none"');
  assert.equal(withUnit(1, '手', 0), '1手', '后端给了没见过的单位时原样带出，不猜换算');
  assert.equal(withUnit(null, 'pct'), NULL_TEXT);
});

test('describeState：六种情况是六件不同的事，不许塌成一句"暂无数据"', () => {
  assert.equal(describeState({ kind: 'loading' }), '取数中…');
  // idle = 页面这一轮根本没发请求（没选中标的）。它必须原样交出那句人话，
  // 不许被读成"取数失败"，也不许被读成"这一屏没有数据"。
  const idle = describeState({ kind: 'idle', reason: '这一屏还没有可查的对象' });
  assert.equal(idle, '这一屏还没有可查的对象');
  assert.doesNotMatch(idle, /取数失败|暂无数据/);
  // 出口去掉 markdown 粗体标记：后端那些句子是 markdown 写的，屏幕上却是纯文本，
  // 原样渲染就成了 `**这张表才建**`（10-07 首页实测撞到）。
  assert.equal(describeState({ kind: 'ok', data: { available: false, reason: '这是**库内新高**不是上市新高' } }),
    `${NOT_WIRED_COPY}：这是库内新高不是上市新高`);
  assert.doesNotMatch(describeState({ kind: 'error', reason: '**要人看的**：intraday 从未成功' }), /\*\*/);
  assert.match(describeState({ kind: 'offline', reason: '连不到后端' }), /连不到后端/);
  const nw = describeState({ kind: 'not_wired', path: '/api/coverage' });
  assert.ok(nw.startsWith(NOT_WIRED_COPY));
  assert.match(nw, /\/api\/coverage/);
  assert.match(nw, /不是"今天没有"/, '404 必须与"今天确实没数"区分开');
  assert.match(describeState({ kind: 'error', reason: 'HTTP 500' }), /取数失败：HTTP 500/);
  // available:false 走 ok 分支但要报"未覆盖" + reason 原文
  const pend = describeState({ kind: 'ok', data: { available: false, reason: '归档只有 0 天' } });
  assert.ok(pend.startsWith(NOT_WIRED_COPY));
  assert.match(pend, /归档只有 0 天/);
  assert.match(describeState({ kind: 'ok', data: { available: false } }), /没给 reason/, 'available:false 不带 reason 要自己出声');
  assert.equal(describeState({ kind: 'ok', data: { available: true } }), '', '有数时不再叠一层提示');
});

test('hasData：available:false 不算"有数"', () => {
  assert.equal(hasData({ kind: 'ok', data: { available: true, x: 1 } }), true);
  assert.equal(hasData({ kind: 'ok', data: { available: false, reason: 'x' } }), false);
  assert.equal(hasData({ kind: 'ok', data: {} as never }), true, '没标 available 就当有数');
  assert.equal(hasData({ kind: 'not_wired', path: '/api/x' }), false);
});

test('errorText：FastAPI 的 422 是对象数组，不能原样交给 React 渲染', () => {
  assert.equal(errorText({ detail: '未知接口 /api/jobs' }), '未知接口 /api/jobs');
  const forty_two = { detail: [{ type: 'less_than_equal', loc: ['query', 'limit'], msg: 'Input should be less than or equal to 2000', input: '3000', ctx: { le: 2000 } }] };
  const t = errorText(forty_two);
  assert.ok(t && t.includes('limit'), '要把出错的参数名带出来');
  assert.ok(t && t.includes('2000'), '要把上限带出来');
  assert.ok(t && !t.includes('[object Object]'), t);
  assert.equal(errorText({ reason: '表不存在' }), '表不存在');
  assert.equal(errorText(null), null);
  assert.equal(errorText({ detail: [] }), null, '空数组不能编成一句话');
});

test('num：非数字与 null 都不许变成 0', () => {
  assert.equal(num(null, 2), NULL_TEXT);
  assert.equal(num('abc', 2), 'abc');
  assert.equal(num(0, 2), '0.00');
  // 1.005 在二进制浮点里略小于 1.005，toFixed 给 1.00 —— 断言的是"按 decimals 显示"，不是四舍五入方向
  assert.equal(num(1.005, 2), '1.00');
  assert.equal(num(1.006, 2), '1.01');
});

test('ageText：缓存年龄统一说法，缺值不假装很新', () => {
  assert.equal(ageText(45), '45 秒');
  assert.equal(ageText(252), '4 分钟');
  assert.equal(ageText(8790), '2.4 小时');
  assert.equal(ageText(137649), '1.6 天');
  assert.equal(ageText(null), NULL_TEXT, '后端没给 age_seconds 时只能说 —，不能显示 0 秒');
  assert.equal(ageText(undefined), NULL_TEXT);
  assert.equal(ageText(-1), NULL_TEXT);
  assert.equal(ageText(Number.NaN), NULL_TEXT);
});

/**
 * 慢接口（实测过 53.6s 的覆盖体检、14s 的板块热度）不能把页面吊成无限转圈：
 * 无限 loading 与"后端坏了"在屏幕上长得一样，而且没有任何可重试的落点。
 */
test('probe 必须带硬超时，超时要说成可重试而不是"没有数据"', () => {
  const src = readFileSync(SRC + 'lib/probe.ts', 'utf8');
  assert.match(src, /SLOW_TIMEOUT_MS = 45_000/);
  assert.match(src, /signal: ctrl\.signal/, 'fetch 没接 AbortSignal 就等于没有超时');
  assert.match(src, /e instanceof Aborted[\s\S]{0,260}点「重新取数」可重试/,
    '超时（AbortError）必须走 error 那支并说成可重试 —— 2026-10-06 起取数层给连不上加了有界补试，超时与连不上分得更清了');
  assert.doesNotMatch(src, /fetch\(apiUrl\(full\), \{ headers: authHeaders\(\) \}\)/, '不能留一条没有 signal 的 fetch');
});
