import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { verdictOf } from '../src/lib/probeVerdict.ts';

/**
 * 系统配置页那一列的判定：四种"没有"必须是四种样子。
 * 这一屏的全部意义在于 —— 让人一眼看出"没实现"和"实现了但坏了"和"今天没数"不是一回事。
 */

test('404 = 端点没实现，不是"今天没数"', () => {
  const v = verdictOf({ kind: 'not_wired', path: '/api/quant/foo' });
  assert.equal(v.code, '404');
  assert.equal(v.kind, 'missing');
  assert.match(v.note, /\/api\/quant\/foo/);
});

test('报错与连不上都算坏着，且把原因带出来', () => {
  assert.equal(verdictOf({ kind: 'error', reason: 'HTTP 500' }).kind, 'broken');
  assert.equal(verdictOf({ kind: 'offline', reason: '端口没人听' }).code, '连不上');
  assert.equal(verdictOf({ kind: 'loading' }).kind, 'pending');
});

test('idle = 这一轮没发请求，不许被排成 200 或坏着', () => {
  const v = verdictOf({ kind: 'idle', reason: '还没选中标的' });
  assert.equal(v.code, '未发');
  assert.equal(v.kind, 'pending');
  assert.match(v.note, /还没选中标的/);
});

test('available:false 走"今天没数"，reason 缺失要自己出声', () => {
  const withReason = verdictOf({ kind: 'ok', data: { available: false, reason: '本场还没加工' } });
  assert.equal(withReason.kind, 'unavailable');
  assert.match(withReason.note, /本场还没加工/);

  const noReason = verdictOf({ kind: 'ok', data: { available: false } });
  assert.match(noReason.note, /没给 reason/);
});

/**
 * 09-30 自查抓到的第 3 类：接口 200、整个响应里既没有 available:true 也没有 false。
 * 以前"没说不行"被读成"行"，忘了表态的一排端点集体显绿。
 */
test('200 但没交 available:true —— 不算可用', () => {
  const v = verdictOf({ kind: 'ok', data: {} });
  assert.equal(v.code, '200?');
  assert.notEqual(v.kind, 'ok', '这是这条判据的核心：不许把沉默读成肯定');
  assert.equal(v.kind, 'unavailable');
  assert.doesNotMatch(v.note, /`/, '界面文案不放 markdown 记号');
});

test('只有明确的 available:true 才盖"可用"', () => {
  assert.equal(verdictOf({ kind: 'ok', data: { available: true } }).kind, 'ok');
  // 0 与 false 是有效值：available:0 / "true" 都不算表态为真，别顺手做强真转换
  assert.notEqual(verdictOf({ kind: 'ok', data: { available: 0 as unknown as boolean } }).kind, 'ok');
  assert.notEqual(verdictOf({ kind: 'ok', data: { available: 'true' as unknown as boolean } }).kind, 'ok');
});

/**
 * 取数那一层的"连不上"要补两拍再说（2026-10-06 用户报「自选组分析 连不到后端」，
 * 而同一接口实测 3.3 秒回 200 —— 差的是服务重启那几十秒，不是页面坏了）。
 * 这条只能按源码读：probe.ts 的内部 import 不带 .ts 后缀，node 侧加载不了它
 * （vite 能），所以断言落在文本上 —— 判据本身仍是硬的。
 */
test('连不上有界补试，超时仍是"慢"不是"没有"', () => {
  const src = fs.readFileSync(new URL('../src/lib/probe.ts', import.meta.url), 'utf8');
  assert.match(src, /const OFFLINE_RETRY_MS = \[[\d_]+, [\d_]+\]/,
    "补试次数必须是写死的有界表，不能是 while 重试到成功");
  assert.match(src, /for \(const ms of OFFLINE_RETRY_MS\)/);
  assert.match(src, /已自动补试/, "界面要说清补试过几次，否则用户不知道下面多等的几秒发生了什么");
  // 超时（AbortError 那条路）绝不能被归类成"连不上" —— 那是慢，判据不同
  assert.match(src, /e instanceof Aborted[\s\S]{0,400}kind: "error"/);
  assert.doesNotMatch(src, /kind: "offline"[\s\S]{0,120}超过/, "超时不许走 offline 分支");
});
