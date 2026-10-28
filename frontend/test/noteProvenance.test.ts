import test from 'node:test';
import assert from 'node:assert/strict';
import { MARK_LABEL, markOf, tallyMarks } from '../src/lib/noteProvenance.ts';

/**
 * 「这一格里的字是谁写的」判定的回归（用户 09-28 选定：草稿预填进正文格，但每格留来源标记）。
 *
 * 判错的后果不是报错，是**这份记录以后问不出真相**：把用户改过的格子说成"机器填入·未改"，
 * 三个月后回看会以为那天自己什么都没想。
 */

const DRAFT = '【这一场已经发生的客观读数】\n· 赚钱效应：+0.55%';

test('草稿一字未动是「机器填入·未改」', () => {
  assert.equal(markOf({ value: DRAFT, draft: DRAFT }), 'machine_untouched');
  assert.equal(markOf({ value: DRAFT, draft: DRAFT, baseline: DRAFT }), 'machine_untouched');
});

test('库里留过痕就用留痕判，改过一个字翻成「你已改」', () => {
  assert.equal(markOf({ value: DRAFT + '，另外我觉得…', baseline: DRAFT }), 'machine_edited');
  assert.equal(markOf({ value: '完全不同的一段', baseline: DRAFT }), 'machine_edited');
});

test('没有留痕也没有草稿时，有字就是「你写的」', () => {
  assert.equal(markOf({ value: '华菱线缆今天放微量下跌' }), 'human');
  // 草稿换了（重算得到不一样的读数）而格子还是老内容：不算"机器填入"，那本来就是人写的
  assert.equal(markOf({ value: '华菱线缆今天放微量下跌', draft: DRAFT }), 'human');
});

test('空格子区分「有草稿可填」与「真的什么都没有」', () => {
  assert.equal(markOf({ value: '   ', draft: DRAFT }), 'empty_with_draft');
  assert.equal(markOf({ value: '', draft: '' }), 'empty');
  // 大V 那一格永远没有草稿：它空着就是空着，不能显示成"有草稿可填"
  assert.equal(markOf({ value: '', draft: undefined }), 'empty');
});

test('把草稿清空仍然算空格子，不冒充未改', () => {
  assert.equal(markOf({ value: '', draft: DRAFT, baseline: DRAFT, }), 'empty_with_draft');
});

test('每个档位都有中文标签，且统计不丢格子', () => {
  const marks = ['machine_untouched', 'machine_edited', 'human', 'empty_with_draft', 'empty', 'human'] as const;
  for (const m of marks) assert.ok(MARK_LABEL[m].length > 0, `${m} 没有中文标签`);
  const tally = tallyMarks([...marks]);
  assert.equal(tally.human, 2);
  assert.equal(Object.values(tally).reduce((a, b) => a + b, 0), marks.length);
});
