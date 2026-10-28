import test from 'node:test';
import assert from 'node:assert/strict';
import { retryVerdict } from '../src/lib/chainRetry.ts';

/**
 * 收盘链"今天还会不会自己再试"的三种情况。
 * 判错的代价不是显示不好看，而是 09-28 那种：数据 17:22 才到、链 16:31 放弃，
 * 页面一句"还会再试"没说，人就手动重跑了一遍；反过来档用完了还默不作声，就是等第二天发现永久缺。
 */

const HEALTHY = { last_run_at: '2026-09-30T15:59:01+08:00', consecutive_failures: 0,
                  attempts_used: 1, attempts_max: 5, next_attempt_at: null };

test('还有档没用完 → 什么都不说（天天喊"正常"就等于没人看）', () => {
  assert.equal(retryVerdict(HEALTHY), null);
});

test('数据晚到但还会再试 → 说清下一次时间，并说明这不是坏了', () => {
  const v = retryVerdict({ ...HEALTHY, next_attempt_at: '16:24' });
  assert.equal(v?.tone, 'warn');
  assert.match(v!.text, /16:24/);
  assert.match(v!.text, /会自动再试/);
  assert.match(v!.text, /不是坏了/);
});

test('档用完且没有下一次 → 说"不会再自动试"并给手动补跑的路', () => {
  const v = retryVerdict({ ...HEALTHY, attempts_used: 5, next_attempt_at: null });
  assert.equal(v?.tone, 'bad');
  assert.match(v!.text, /不会再自动试/);
  assert.match(v!.text, /e1_fill_all/);
});

/** 最坏的一种以前是**一句都没有**：一天都没跑过（连败 0），却已经把档用完。 */
test('从没跑过 + 档用完，同样要报', () => {
  const v = retryVerdict({ last_run_at: null, consecutive_failures: 0, attempts_used: 3, attempts_max: 3 });
  assert.equal(v?.tone, 'bad');
});

/**
 * 后端没交节奏 ≠ 没事，也 ≠ 出事。
 * 只在"看起来已经不行了"的时候说出来 —— 正常跑过的段本来就没有下一次，
 * 给每一段都挂一句"判断不了"就是把警告喊废（09-30 自查后加的约束）。
 */
test('没交节奏：跑过且没连败 → 不喊；没跑过或正连败 → 老实说判断不了', () => {
  assert.equal(retryVerdict({ ...HEALTHY, attempts_max: null, attempts_used: null }), null);
  const never = retryVerdict({ last_run_at: null, attempts_max: null });
  assert.equal(never?.tone, 'warn');
  assert.match(never!.text, /判断不了/);
  const failing = retryVerdict({ ...HEALTHY, attempts_max: null, consecutive_failures: 2 });
  assert.match(failing!.text, /判断不了/);
});

/** `attempts_max = 0` 是"明确没有重试档"，不是"字段没给"（01 第三条：0 是有效值） */
test('attempts_max=0 按"没有重试档"处理，不当成缺失', () => {
  const v = retryVerdict({ ...HEALTHY, attempts_used: 0, attempts_max: 0 });
  assert.equal(v?.tone, 'bad');
  assert.match(v!.text, /没有重试档/);
});
