import test from 'node:test';
import assert from 'node:assert/strict';
import { renderPrompt, unfilledPlaceholders, NULLABLE_FIELDS } from '../src/lib/promptRender.ts';

/**
 * 渲染规则必须与后端 `duanxian/prompt_slots.py:render()` 一字不差 ——
 * 两边漂了就会出现"系统配置页看着对、发出去的提示词不是那份"。
 * 后端有同名测试 `tests/test_prompt_slots.py`，改这一边就去把那边一起改。
 */

test('已知占位符换成值，一个不留', () => {
  const out = renderPrompt('{date} 的 {name}（{code}）收 {close} 元', {
    date: '2026-09-24', name: '华康洁净', code: '301235.SZ', close: 46.2,
  });
  assert.equal(out, '2026-09-24 的 华康洁净（301235.SZ）收 46.2 元');
  assert.doesNotMatch(out, /\{[a-z]/);
});

test('缺的值交 未提供，不给空串也不给 0', () => {
  assert.match(renderPrompt('题材：{reason}。', { reason: null }), /题材：未提供。/);
  assert.match(renderPrompt('题材：{reason}。', { reason: '' }), /题材：未提供。/);
  assert.equal(renderPrompt('炸板 {break_count} 次', { break_count: 0 }), '炸板 0 次');
});

test('整句开关允许空：没有实时报价就该整句消失', () => {
  assert.ok(NULLABLE_FIELDS.has('quote_line') && NULLABLE_FIELDS.has('em_line'));
  const tpl = '客观数据：\n{em_line}收盘 {close}，{quote_line}成交额 {amount}';
  assert.equal(renderPrompt(tpl, { em_line: '', close: 11.0, quote_line: '', amount: '1.9亿' }),
    '客观数据：\n收盘 11，成交额 1.9亿');
  assert.equal(renderPrompt(tpl, { em_line: '', close: 11.0, quote_line: null, amount: '1.9亿' }),
    '客观数据：\n收盘 11，未提供成交额 1.9亿');
});

test('认不出的花括号不动：那是给模型的输出约定', () => {
  const tpl = '只返回 {"answer":"正文"}，标的 {name}';
  assert.equal(renderPrompt(tpl, { name: '某股' }), '只返回 {"answer":"正文"}，标的 某股');
});

test('没登记的占位符原样留着，登记了但没值才交 未提供', () => {
  // 分清两种"没有"：字段名打错（没登记）→ 留着让人看见；字段存在但这一行没值 → 未提供。
  assert.equal(renderPrompt('位置 {pice} 元', { name: 'x' }), '位置 {pice} 元');
  assert.equal(renderPrompt('题材 {reason}', { reason: undefined }), '题材 未提供');
  assert.deepEqual(unfilledPlaceholders('位置 {pice} 元', { name: 'x' }), ['pice']);
  assert.deepEqual(unfilledPlaceholders('{name} 与 {name}', { name: 'x' }), []);
});

test('未填清单给配置页提前报警用', () => {
  const fields = { name: '名称', code: '代码' };
  const draft = '{name}（{code}）的 {pos}';
  const missing = unfilledPlaceholders(draft, fields);
  assert.deepEqual(missing, ['pos'], '只该点名那一个写错的');
});
