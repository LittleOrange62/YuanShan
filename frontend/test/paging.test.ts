import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { PAGE_SIZES, canStep, clampPage, pageSentence, pageCount, slicePage } from '../src/lib/paging.ts';

const panel = readFileSync(new URL('../src/components/ui/DataPanel.tsx', import.meta.url), 'utf8');
const board = readFileSync(new URL('../src/components/home/AbnormalMoveBoard.tsx', import.meta.url), 'utf8');
const startup = readFileSync(new URL('../src/components/quant/StartupVolumeBoard.tsx', import.meta.url), 'utf8');
const cycle = readFileSync(new URL('../src/components/qd/SectorCyclePanel.tsx', import.meta.url), 'utf8');
const unlock = readFileSync(new URL('../src/components/UnlockCalendarPanel.tsx', import.meta.url), 'utf8');
const panorama = readFileSync(new URL('../src/components/home/MarketPanorama.tsx', import.meta.url), 'utf8');

/** 用户 2026-10-22：「异动提醒③那块内容太多了，分页显示」。分页算法收在一处（全站十几块榜各写一份会漂）。 */
test('分页算法：先夹页再切片，句子里永远带共多少行', () => {
  assert.equal(pageCount(0, 25), 1, '0 行也算一页（不然页脚会写"第 1/0 页"）');
  assert.equal(pageCount(101, 25), 5);
  assert.equal(clampPage(9, 3), 3, '换了筛档让页数变小 ⇒ 夹回末页，不给空页');
  assert.equal(clampPage(0, 3), 1);
  const rows = Array.from({ length: 60 }, (_, i) => i);
  assert.deepEqual(slicePage(rows, 3, 25).length, 10);
  assert.deepEqual(slicePage(rows, 1, 25)[0], 0);
  assert.equal(slicePage(rows, 99, 25).length, 10, '越界的页码先夹再切');
  assert.equal(pageSentence(60, 25, 2), '共 60 行 · 每页 25 行 · 现在是第 2/3 页');
  assert.equal(canStep(1, 25, 60, -1), false, '第一页不许出现"上一页"');
  assert.equal(canStep(3, 25, 60, 1), false, '末页不许出现"下一页"');
  assert.deepEqual(PAGE_SIZES, [25, 50, 100]);
});

test('异动提醒③那张表：排序在切片之前，页脚那句带总数', () => {
  assert.match(board, /const recAll = recSort\.apply\(am\?\.recent_severe\?\.rows \?\? \[\]\);/,
    '先排序（全站一条纪律：排序在截断之前，否则每页各自换口径）');
  assert.match(board, /const recShown = slicePage\(recAll, recPage, recSize\);/);
  assert.match(board, /\{recShown\.map\(\(r: AbnRecentRow\) =>/, 'tbody 切的是这一页那几行');
  assert.doesNotMatch(board, /recSort\.apply\(am\.recent_severe\?\.rows \?\? \[\]\)\.map/,
    '不许再回到"整表 map"（那正是这次要分页的那一块）');
  assert.match(board, /pageSentence\(recAll\.length, recSize, recPage\)/,
    '页脚必须说共多少行 —— 只说"第 1/4 页"会被读成"全场 25 只"');
  assert.match(board, /ri\.clearAll\(\)/, '换页/换每页要清交叉高亮的行选中（行号记的是渲染数组）');
});

test('首页密度：一屏读到底的那几块全部改为分页（用户 2026-10-26 定"第一页最多 30 行，多余的分页"）', () => {
  // 全站那一组档位的第一档必须真的不超过用户给的那个数，否则下面几块的"≤30"是空话
  assert.ok(PAGE_SIZES[0] <= 30, `首页每页档位在 lib/paging 里是 ${PAGE_SIZES[0]}，超过 30 就违背这一条`);
  for (const [名称, src, 排好的数组, tbody] of [
    ['启动前量能比对（31 列 × 200 行＝一次单击重建约 6,200 格）', startup, 'sorted', 'paged'],
    ['生命周期阶段（本轨，约 1,068 个板块）', cycle, 'sorted', 'paged'],
    ['限售解禁日历', unlock, 'sorted', 'paged'],
    ['连板股榜', panorama, 'lbSorted', 'lbPaged'],
  ] as const) {
    assert.match(src, /slicePage\(/, `${名称}：tbody 要走 lib/paging 那一份切片，不要各写一遍 ceil/slice`);
    assert.match(src, new RegExp(`const ${排好的数组} = [\\w.]+\\.apply\\(`),
      `${名称}：排序要在切片之前（先截断后排序会让每一页各自换口径）`);
    assert.match(src, new RegExp(`\\{${tbody}\\.map\\(`), `${名称}：tbody 渲染的是这一页那几行`);
    assert.match(src, /pageSentence\(/, `${名称}：页脚那句要带"共多少行"`);
  }
  // 老写法不许回来：整表 map / 硬截前 N 行还不给翻页
  assert.doesNotMatch(startup, /\{svSort\.apply\(rows\)\.map\(/, '启动前量能那张表不许再整表铺开');
  assert.doesNotMatch(cycle, /scSort\.apply\(shown\)\.slice\(0, 40\)/, '生命周期那张表原来硬截 40 行，第 41 行往后根本翻不到');
  assert.doesNotMatch(panorama, /lbSort\.apply\(emotion\.lianban_stocks\)\.map\(/, '连板榜不许再整表铺开（热市几十行 × 13 列）');
});

test('foldAll（用户点名把缺数那几条也收）：条数仍然写在 summary 上，框外那份不再出现', () => {
  assert.match(panel, /const held = foldAll \? \[\.\.\.foldable, \.\.\.visible\] : foldable;/);
  assert.match(panel, /\{!foldAll && <Caveats items=\{visible\} \/>\}/,
    'foldAll 时框外那份没了 ⇒ 但下面那条必须补上，否则"有东西没读到"这件事就彻底不见了');
  assert.match(panel, /heldUnhealthy \? `\$\{heldUnhealthy\} 条缺数与告警/,
    'summary 里要按类别报出缺数条数（数字仍在屏上，原句点开看）');
  assert.match(panel, /const heldUnhealthy = foldAll \? visible\.length : 0;/);
  assert.equal(panel.split('<Caveats items={visible} />').length - 1, 1,
    '框外那份 visible 只能出现一次，且必须被 `!foldAll` 门住（两处都摆＝同一条 caveat 上屏两次）');
  assert.match(panel, /\{!foldAll && <Caveats items=\{visible\} \/>\}/);
});
