import test from 'node:test';
import assert from 'node:assert/strict';
import { countCollapsed, staleVerdict } from '../src/lib/staleness.ts';

/**
 * 这条判据是 09-28 事故的直接产物：那天通达信盘后下载中途死掉，库里停在 09-24，
 * 首页五块卡片照平铺、看着像今天的数。用户要求"不是最新的一律默认收起"。
 * 判据错了不会报错，只会继续让人读旧数当新数。
 */

test('这一块就是最新一场时不收起', () => {
  const v = staleVerdict('2026-09-28', '2026-09-28');
  assert.equal(v.level, 'current');
  assert.equal(v.collapsed, false);
});

test('这一块比本页主数旧 → 收起，并把两个日子都写出来', () => {
  const v = staleVerdict('2026-09-24', '2026-09-28');
  assert.equal(v.level, 'behind');
  assert.ok(v.collapsed);
  assert.match(v.label, /2026-09-24/);
  assert.match(v.label, /2026-09-28/);
});

test('整库没跟上已收盘的那场 → 每一块都收起', () => {
  const v = staleVerdict('2026-09-24', '2026-09-24', true);
  assert.equal(v.level, 'libraryBehind');
  assert.ok(v.collapsed, '库里整体落后时，"和主数一致"不等于"是最新的"');
  assert.match(v.label, /还没加工/);
});

test('没有日期 = 不知道，不等于旧：照常展开并说明', () => {
  const v = staleVerdict('', '2026-09-28');
  assert.equal(v.level, 'unknown');
  assert.equal(v.collapsed, false);
  assert.match(v.label, /没给日期/);
});

/**
 * 基准日（本页主数）自己缺失时，**不能盖"最新一场"的章**。
 * 这条断言 09-30 改过一次：原来这里期望 `current` —— 名字写的是"不假装能判新旧"，
 * 断言的却是"判不出来就说它是最新的"，正是要修的那个 bug。
 * `expected-date` 一挂就整屏把旧数互相比对成"都最新"，所以必须单独一档、且不收起。
 */
test('主数本身缺失时不假装能判新旧', () => {
  const v = staleVerdict('2026-09-20', '');
  assert.equal(v.level, 'noNewest');
  assert.equal(v.collapsed, false, '判不出新旧不等于旧，不许悄悄把这块折起来');
  assert.match(v.label, /判断不了新旧/);
  assert.doesNotMatch(v.label, /最新一场/, '没有基准日就不能出现"最新"字样');
});

test('带时间的串只比日期部分，跨小时不误判', () => {
  assert.equal(staleVerdict('2026-09-28 15:00:00', '2026-09-28').level, 'current');
});

test('收起计数只数真收起的', () => {
  const vs = [staleVerdict('2026-09-24', '2026-09-28'), staleVerdict('2026-09-28', '2026-09-28'),
              staleVerdict('', '2026-09-28'), staleVerdict('2026-09-28', '2026-09-28', true)];
  assert.equal(countCollapsed(vs), 2);
});

/** 页顶那句"下面 N 块是旧数"的 N 只能数真被判成旧的。 */
test('基准日缺失既不收起，也不进"N 块旧数"的计数', () => {
  const vs = [staleVerdict('2026-09-20', ''), staleVerdict('2026-09-20', '2026-09-28')];
  assert.equal(countCollapsed(vs), 1, '只有那一条真的旧，判不出新旧的不许算进去');
});
