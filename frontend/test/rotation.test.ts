import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { cumCell, cumCellB, cumValue, fmtBps, fmtPp, fmtShare, sideStreak, streakText, valueCell, valueColumnLabel, DASH } from '../src/lib/rotation.ts';

/**
 * 份额榜的读数层。三条规矩各对应一个真踩过的坑：
 *  ① 空值 `—`、0 与 false 是有效值；
 *  ② `cum_N` 必须与 `days_N` 同屏（攒不满窗口的差与攒满的差不可比）；
 *  ③ 连续场次那一列**不许出现箭头/吸金字样**（实测越连续越不延续，箭头会被读成"还会继续"）。
 */

test('份额是百分比、份额差是百分点，两者不共用一个单位', () => {
  assert.equal(fmtShare(0.046), '4.600%');
  assert.equal(fmtPp(0.0183), '+1.83pp');
  assert.equal(fmtPp(-0.0032), '-0.32pp');
  assert.equal(fmtBps(-11.062), '-11.1bp');
});

test('0 是有效值：份额 0 与连续 0 场不能被当成空', () => {
  assert.equal(fmtShare(0), '0.000%');
  assert.equal(fmtPp(0), '0.00pp');
  assert.equal(streakText(0, 0), '不连续');
});

test('真的没给值才给 —', () => {
  assert.equal(fmtShare(null), DASH);
  assert.equal(fmtPp(undefined), DASH);
  assert.equal(fmtBps(NaN), DASH, 'NaN 也是没数 —— 出口带 NaN 会印成字面量 NaN');
  assert.equal(cumCell(null, 10, 10), DASH);
});

/** DDL 的列注释就是这条：days_N 必须和 cum_N 一起交出去。 */
test('窗口攒不满时把"只几场"跟着数一起给', () => {
  assert.equal(cumCell(0.0077, 10, 10), '+0.77pp');
  assert.equal(cumCell(0.0077, 20, 10), '+0.77pp');
  assert.equal(cumCell(0.0077, 6, 10), '+0.77pp（只 6 场）');
  assert.equal(cumCell(0.0077, null, 10), '+0.77pp',
    '后端没交 days 就只给数，不许编一个"满窗"的暗示');
});

test('连续场次的说法只讲已经发生的事', () => {
  assert.equal(streakText(3, 0), '抬升 3 场');
  assert.equal(streakText(0, 2), '回落 2 场');
  for (const t of [streakText(3, 0), streakText(0, 2), streakText(0, 0)]) {
    assert.doesNotMatch(t, /[↑↓▶▲▼➜→←]/, '不许出现箭头');
    assert.doesNotMatch(t, /吸金|抢筹|看好|将/);
  }
});

test('各侧连续场次只取自己那一侧：默认序两端都要"由多到少"排得动', () => {
  // 合并键（抬升正 / 回落负）排回落端会得到反的：降序时 −2 排在 −9 前面。
  assert.equal(sideStreak(3, 0, "up"), 3);
  assert.equal(sideStreak(3, 0, "dn"), 0, "抬升中的板块在回落端榜里就是没连着");
  assert.equal(sideStreak(0, 9, "dn"), 9);
  assert.equal(sideStreak(0, 2, "dn"), 2);
  assert.equal(sideStreak(null, null, "up"), 0);
  assert.equal(sideStreak(undefined, 4, "dn"), 4);
  const sorted = [[0, 9], [1, 0], [2, 4]]
    .map(([u, d]) => sideStreak(u, d, "dn"))
    .sort((a, b) => b - a);
  assert.deepEqual(sorted, [9, 4, 0], "回落端默认序：回落场次由多到少");
});

test('两端的默认序真的接到了调用点（不是只写了个函数）', () => {
  // 排序键写错不报错、也不换序，列头的 aria-sort 还会照样亮 —— 所以这里钉源码：
  // ① 表真的带了默认序；② 两侧各传自己的 side；③ 列名随侧走。
  const src = readFileSync(new URL('../src/components/home/SectorRotationBoard.tsx', import.meta.url), 'utf8');
  assert.match(src, /useTableSort\(\{ key: "streak", dir: "desc" \}\)/);
  assert.match(src, /side="up"/);
  assert.match(src, /side="dn"/);
  assert.match(src, /sideStreak\(r\.streak_up, r\.streak_dn, side\)/);
  assert.match(src, /连续抬升场次/);
  assert.match(src, /连续回落场次/);
  assert.doesNotMatch(src, /streakSortKey/, '合并键已从库里删掉：调用点不许再引用它');
});

test('首页顺序：份额轮动（本地逐日）在板块资金趋势榜之前（用户 10-08 点名）', () => {
  // 方向性措辞也要跟着改：搬完之后还说"在下面那张"就是指着空气解释口径。
  const home = readFileSync(new URL('../src/components/home/MarketPanorama.tsx', import.meta.url), 'utf8');
  // 比的是**渲染顺序**（那一屏的 h3），不是文件里第一次出现这个词 ——
  // 顶上那段取数注释也写着"板块资金趋势榜"，按第一次出现比会永远判成"没搬"。
  const board = home.indexOf('<SectorRotationBoard ');
  const flow = home.indexOf('板块资金趋势榜</h3>');
  assert.ok(board > 0 && flow > 0, '两块都还在首页上');
  assert.ok(board < flow, '份额轮动必须排在板块资金趋势榜之前');
  assert.equal(home.match(/<SectorRotationBoard /g)?.length, 1, '只能挂一次');
  assert.match(home, /跨场的连续与累计在上面那张/);
});

/* ---------------------------------------------------- 2026-10-12 第二把尺子：量纲不许混 */

test('换手的 value 已经是百分数，再乘一次就会印成 400%', () => {
  assert.equal(valueCell(4.0, 'turnover'), '4.00%');
  assert.equal(valueCell(4.0, 'share'), '400.000%',
    '同一数在份额那档就该是 ×100 —— 两条路各走各的，不许共用一个乘 100');
  assert.equal(valueCell(0.004, 'share'), '0.400%');
  assert.equal(valueCell(null, 'turnover'), DASH, '空是 —，不是 0.00%');
  assert.equal(valueCell(0, 'turnover'), '0.00%', '0 是有效值（今天真没人换它），不许吞成 —');
});

test('换手的累计单位本来就是 pp，不再过 fmtPp', () => {
  assert.equal(cumValue(0.9, 'turnover'), '+0.90pp');
  assert.equal(cumValue(-0.35, 'turnover'), '-0.35pp');
  assert.equal(cumValue(0.0002, 'share'), '+0.02pp', '份额那档仍要 ×100');
  // 攒不满窗口的"只几场"标注在两条口径下都不许丢
  assert.equal(cumCellB(0.9, 6, 30, 'turnover'), '+0.90pp（只 6 场）');
  assert.equal(cumCellB(0.9, 30, 30, 'turnover'), '+0.90pp');
  assert.equal(cumCellB(null, 6, 30, 'turnover'), DASH);
});

test('两把尺子的列名不同，界面不许共用一个标题', () => {
  assert.equal(valueColumnLabel('share'), '今日份额');
  assert.equal(valueColumnLabel('turnover'), '当日换手率');
});
