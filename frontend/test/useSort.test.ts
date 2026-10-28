import test from 'node:test';
import assert from 'node:assert/strict';
import { compareCells, sortRows } from '../src/lib/useSort.ts';

/**
 * 排序语义的钉子。最关键的一条是**缺数永远在后**：本项目的规矩 `NULL ≠ 0`，
 * 把"未获取"跟 0 一起排上去，读者会拿缺失当证据（"这些板块今天没量"）。
 */
test('缺数永远排最后，升序降序都一样；0 是有值不是缺值', () => {
  const rows = [{ n: 5 }, { n: null }, { n: 0 }, { n: undefined }, { n: '—' }, { n: 12 }];
  assert.deepEqual(sortRows(rows, { key: 'n', dir: 'desc' }).map(r => r.n),
    [12, 5, 0, null, undefined, '—']);
  assert.deepEqual(sortRows(rows, { key: 'n', dir: 'asc' }).map(r => r.n),
    [0, 5, 12, null, undefined, '—']);
});

test('数值按大小不按字典序，带千分位的串也认', () => {
  assert.deepEqual(sortRows([{ v: 2 }, { v: 10 }, { v: 1 }], { key: 'v', dir: 'asc' }).map(r => r.v),
    [1, 2, 10]);
  assert.equal(compareCells('1,234', 900) > 0, true);
  assert.equal(compareCells('12.5%', 12.6) < 0, true);
});

test('文字列按中文拼音排，同值保持后端给的原始顺序（稳定）', () => {
  // 只钉"中文之间"的顺序（半 bǎn 在 券 quàn 前）；中英混排的先后取决于运行环境的
  // ICU 排序规则，不在这条测试的断言范围里，别把它写死成期望值。
  assert.deepEqual(sortRows([{ s: '券商' }, { s: '半导体' }, { s: '钢铁' }], { key: 's', dir: 'asc' }).map(r => r.s),
    ['半导体', '钢铁', '券商']);
  const rows = [{ k: 1, tag: 'a' }, { k: 1, tag: 'b' }, { k: 1, tag: 'c' }];
  assert.deepEqual(sortRows(rows, { key: 'k', dir: 'desc' }).map(r => r.tag), ['a', 'b', 'c']);
});

test('key 支持嵌套字段，取不到按缺数处理', () => {
  const rows = [{ name: '甲', heat: { score: 90 } }, { name: '乙', heat: { score: 70 } }, { name: '丙' }];
  assert.deepEqual(sortRows(rows, { key: 'heat.score', dir: 'desc' }).map(r => r.name),
    ['甲', '乙', '丙']);
});

test('没选列时一个字都不动（默认仍是后端的榜序）', () => {
  const rows = [{ v: 3 }, { v: 1 }];
  assert.deepEqual(sortRows(rows, null), rows);
});
