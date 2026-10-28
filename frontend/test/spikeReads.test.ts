import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/**
 * 异动筛查那一屏（`pages/Spike.tsx`）的读侧接线。
 *
 * 钉的全是"写错了不报错"的那几类：
 *  ① 列键与列头 sortKey 必须同序 —— 点列头不报错、只是那一列永不亮；
 *  ② `getCellClass` 必须真的进 className（只挂 onClick 的话行列交叉高亮整块失效，且没有任何提示）；
 *  ③ sortKey 必须是那一行真有的字段（死键不排序，aria-sort 却照样亮）；
 *  ④ 筛条件要发进接口（`tags=`/`min_hi=`/`date=`/`days=`）：前端筛只能筛已交出的那些行；
 *  ⑤ 默认行序 = 后端交来的时间顺序，页面不给初始排序；
 *  ⑥ 全场那几个数（条数／封住／触板没封住）只能来自后端聚合，页面不许自己数 rows；
 *  ⑦ 后端散文（calibers、caveat）上屏要过 plain()，否则 ** 会直接落在屏幕上。
 */

const page = readFileSync(new URL('../src/pages/Spike.tsx', import.meta.url), 'utf8');

function ifaceKeys(name: string): string[] {
  const from = page.indexOf(`interface ${name}`);
  assert.ok(from >= 0, `找不到 interface ${name}`);
  const rest = page.slice(from);
  const body = rest.slice(0, rest.search(/\n\}/));
  return [...body.matchAll(/\b([a-z0-9_]+)\?:/g)].map(m => m[1]);
}

const EV_KEYS = ifaceKeys('Ev');

test('每一格的列键与列头的 sortKey 是同一份顺序', () => {
  const cellKeys = [...page.matchAll(/<Cell i=\{i\} k="([a-z0-9_]+)"/g)].map(m => m[1]);
  const sortKeys = [...page.matchAll(/sortKey="([a-z0-9_]+)"/g)].map(m => m[1]);
  assert.ok(cellKeys.length >= 10, `列格太少，抓取失败：${cellKeys.length}`);
  assert.deepEqual(sortKeys, cellKeys, `列头顺序与格子顺序不一致：${sortKeys} vs ${cellKeys}`);
});

test('sortKey 都是行里真有的字段（死键不报错也不排序）', () => {
  const sortKeys = [...page.matchAll(/sortKey="([a-z0-9_]+)"/g)].map(m => m[1]);
  for (const k of sortKeys) assert.ok(EV_KEYS.includes(k), `${k} 不在 Ev 里，点了不会排序`);
});

test('getCellClass 真的进了 className，onClick 用的是 handleCellClick', () => {
  // 这一条是 10-13 实测到的那一类：Td 挂了 onClick 却没把 className 走 getCellClass，
  // 于是行亮列不亮，且一行代码都不报错。
  assert.match(page, /td className=\{cell\.getCellClass\(/,
    '格子的 className 没走 getCellClass —— 列高亮会静默失效');
  assert.match(page, /onClick=\{\(e\) => cell\.handleCellClick\(e\.currentTarget, i, k\)\}/);
  assert.ok(!/className=\{cn\(/.test(page.split('function Cell')[1].split('export function')[0])
    || page.includes('cell.getCellClass(i, k, cn('),
    'Cell 里的 cn(...) 没被 getCellClass 包住');
});

test('四类筛条件都发进接口，而不是在前端筛已交出的行', () => {
  assert.ok(page.includes('q.set("date", date)'), '日期没发进接口');
  assert.ok(page.includes('q.set("tags", tags.join(","))'), '标签没发进接口');
  assert.ok(page.includes('q.set("min_hi", String(minHi))'), '幅度门槛没发进接口');
  assert.ok(page.includes('q.set("days", String(sessions))'), '复盘窗口没发进接口');
  assert.ok(page.includes('/quant/intraday-spike?'), '取数路径没走 /api 前缀由 probe 补');
  // 前端不再自己按标签筛一遍（那样只能筛到已交出的那几百行）
  assert.ok(!/\.filter\(.*tags\.includes/.test(page), '前端又筛了一遍标签');
});

test('默认不给初始排序：后端交来的就是点名的时间顺序', () => {
  assert.match(page, /const rowSort = useTableSort\(\);/,
    '给了初始排序就会覆盖后端的时刻顺序');
  assert.ok(page.includes('rowSort.apply(all)') || page.includes('rowSort.apply(rows)'));
});

test('点列头先取消选中（选中的是行号，行序一变就不是同一只）', () => {
  const sortBy = /const sortBy = \(k: string\) => \{[^}]*\}/.exec(page)?.[0] ?? '';
  assert.ok(sortBy.includes('cell.clearAll()') && sortBy.includes('rowSort.toggle(k)'),
    `sortBy 没收掉选中：${sortBy}`);
  assert.ok(page.includes('onSort={sortBy}'), '列头没接到 sortBy');
});

test('全场那几个数一律来自后端聚合', () => {
  for (const k of ['d.total', 'd.sealed', 'd.blown', 'd.minute_missing']) {
    assert.ok(page.includes(k), `${k} 没从后端读，页面在自己数全场`);
  }
  assert.ok(!/rows\.reduce|all\.filter\(.*sealed/.test(page), '页面在数 rows 当全场数');
});

test('后端散文上屏过 plain()，且控件在永远渲染的那一格', () => {
  assert.ok(page.includes('import { plain } from "@/lib/agent"'));
  assert.match(page, /\{plain\(cal\.mother \?\? ""\)\}/);
  assert.match(page, /\{plain\(cal\.peak_time \?\? ""\)\}/);
  assert.ok(page.includes('plain(review.reason'), '复盘不可用的原因没走 plain');
  assert.ok(page.includes('controls={controls}'), '筛控件没进 controls 槽（没数时就不见了）');
});

test('页面自己写的句子里不许有 markdown 星号（2026-10-13 浏览器实测抓到过两对）', () => {
  // 后端散文有 plain() 兜底；页面自写的那几句没人兜，** 会原样落在屏幕上。
  // 只剥块注释（JSDoc 的 `/**` 与 `**` 无关），正文里出现就是泄漏。
  const body = page.replace(/\/\*[\s\S]*?\*\//g, '');
  assert.ok(!body.includes('**'), '源码正文里有字面 ** —— 会被当正文渲染出来，去掉或改「」');
});

test('最新一场的次日写「还没走完」，不当 0 也不当缺数', () => {
  assert.ok(page.includes('r.has_next ? pct(r.next_pct)'),
    '次日读数没按 has_next 分支：没走完会被读成 0');
  assert.ok(page.includes('明天还没走完'));
});

test('缺分钟线那一格说明的是判据没生效，不是没有冲高', () => {
  assert.match(page, /分钟级读数/, '');
  assert.ok(page.includes('不是「这些票没冲高」'));
  assert.ok(page.includes('minute_missing'));
});

test('倍量形态页有到这一屏的入口（搬了能力就要有真入口）', () => {
  const vd = readFileSync(new URL('../src/pages/VolumeDouble.tsx', import.meta.url), 'utf8');
  assert.ok(vd.includes('to="/spike"'), '倍量形态页没有指向异动筛查的入口');
});

test('接口没有交出 rows 键给不可用态', () => {
  const view = readFileSync(new URL('../../duanxian/spike_view.py', import.meta.url), 'utf8');
  assert.ok(view.includes('return _fail('), '读侧没有不可用分支');
  assert.match(view, /if not b\.get\('available'\):\n\s*return b/,
    '不可用时整份照交 —— 会把"没数"读成"0 条异动"');
});
