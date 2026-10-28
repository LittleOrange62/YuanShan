import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * 「陈旧收起」这条规矩必须盖住**每一块**，包括以后新长出来的。
 * 用户 09-28 的原话是"所有不是最新的数据，默认把这个模块收起来"——
 * 只要还有第二套新鲜度判断存在（或某块压根没交 as-of），那句话就只覆盖了一半。
 */

const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'src');

const tsxFiles = (dir: string): string[] => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => {
  const p = path.join(dir, e.name);
  return e.isDirectory() ? tsxFiles(p) : (e.name.endsWith('.tsx') || e.name.endsWith('.ts') ? [p] : []);
});

const read = (rel: string) => fs.readFileSync(path.join(SRC, rel), 'utf8');

test('首页每一块都交了自己的 as-of（Block 的 asOf 是必填，漏一块编译就过不去）', () => {
  const sb = read('pages/SummaryBoard.tsx');
  assert.match(sb, /asOf: string \| null \| undefined/, 'asOf 一旦退回可选，新块就能悄悄不参与规矩');
  assert.doesNotMatch(sb, /asOf\?:/, '同一个壳不许再留一个可选的旧签名');
  const blocks = sb.match(/<Block\s/g) ?? [];
  // 2026-10-06 用户裁定「quantdash 口径对照 已经有单独的页面展示了，首页中这块内容拿掉」，
  // 首页的「情绪周期」那一块整块摘掉 → 六块变五块。这里跟着降到 5，
  // 并留"扫描式样失效就去改测试"的原话：数字变小要看得见是谁摘的，不能悄悄放宽。
  assert.ok(blocks.length >= 5, `首页至少应有 5 块，实际扫到 ${blocks.length} —— 扫描式样失效了要去改测试`);
  assert.equal(blocks.length, 5,
    `首页现在是 5 块（涨停与连板 / 盘面数据 / 温度类指标 / 板块周期与资金 / 今日复盘与入口）；扫到 ${blocks.length} 个说明有人加了块或摘了块却没回这里`);
  // 每个 <Block ...> 开头的这一段里必须出现 asOf=
  sb.split('<Block ').slice(1).forEach((chunk, i) =>
    assert.match(chunk.slice(0, 900), /asOf=/, `第 ${i + 1} 个 <Block> 没交 asOf`));
});

/**
 * 只允许一处判据：`lib/staleness.ts`。
 * 别的页面自己比日期、自己决定收不收起，就会有两套口径 ——
 * 那正是"首页收了、别的页还在平铺"的成因。
 */
test('新鲜度判据只有一份，没有第二套自造口径', () => {
  const users = tsxFiles(SRC).filter(f => fs.readFileSync(f, 'utf8').includes('staleVerdict('));
  const rel = users.map(f => path.relative(SRC, f).replace(/\\/g, '/')).sort();
  assert.deepEqual(rel, ['lib/staleness.ts', 'pages/SummaryBoard.tsx'],
    'staleVerdict 只应在判据本体与首页出现；出现第三处就要说明它是不是绕开了规矩');
});

/** 首页不许再用本机时钟当"今天"：行情场次只从接口来。 */
test('首页与盘面模块不拿本机时钟冒充行情日', () => {
  for (const rel of ['pages/SummaryBoard.tsx', 'components/home/MarketPanorama.tsx']) {
    const src = read(rel);
    assert.doesNotMatch(src, /new Date\(\)\.toLocaleDateString/,
      `${rel} 里出现了本机时钟生成的日期串 —— 场次必须来自接口，空就说不确定`);
  }
});
