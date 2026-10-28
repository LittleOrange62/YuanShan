import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { nodeText, nodeTone, reasonText, titleOf, warnText, REASON_TEXT } from '../src/lib/nodeCell.ts';

/**
 * 「节点分析」那一格的判读层 + 两个调用点接线。
 * 核心那条：`—` 有三种来历（没这一行 / 这一场判不了 / 真没命中），屏幕必须分得开。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const firstBoard = fs.readFileSync(path.join(HERE, '../src/pages/FirstBoard.tsx'), 'utf8');
const panorama = fs.readFileSync(path.join(HERE, '../src/components/home/MarketPanorama.tsx'), 'utf8');
const comp = fs.readFileSync(path.join(HERE, '../src/components/ui/NodeCell.tsx'), 'utf8');
const readSide = fs.readFileSync(path.join(HERE, '../../duanxian/node_view.py'), 'utf8');
const judge = fs.readFileSync(path.join(HERE, '../../app/compute/board_node.py'), 'utf8');

test('格子里只有 1 / 2 / 1, 2 / — 四种，照后端原样', () => {
  assert.equal(nodeText({ node_hits: '1' }), '1');
  assert.equal(nodeText({ node_hits: '1, 2' }), '1, 2');
  assert.equal(nodeText({ node_hits: '' }), '—');
  assert.equal(nodeText({ node_hits: null }), '—', '后端给 null 也是 —，不四舍五入成 0');
  assert.equal(nodeTone({ node_hits: '2' }), 'hit');
  assert.equal(nodeTone({ node_hits: '' }), 'none', '没命中不上色：灰不是"差"，是没有结论');
});

test('短码逐条翻成人话；后端新加的短码照原样念并点名没有说明', () => {
  assert.equal(reasonText(''), '');
  assert.match(reasonText('n1:sh_pct_missing'), /上证那天没读到涨跌幅/);
  assert.match(reasonText('n2:prev_top_still_sealed'), /高度没被清空/);
  assert.match(reasonText('n1:vol_not_up,n2:prev_top_below_6'), /；/, '两条原因要并成一句');
  const unknown = reasonText('n9:brand_new_code');
  assert.match(unknown, /短码 n9:brand_new_code/, '没有说明的短码要显出来，不静默吞掉');
  assert.match(unknown, /后端没给这句的中文说明/);
});

test('每一条短码都有出处（后端真会交出的那些）', () => {
  // 后端发出的短码必须在这张表里，否则界面上就是一条没人翻译的机器话
  const emitted = [...judge.matchAll(/'(n[12]:[a-z_0-9]+|run_window_broken|run_days_no_hit)'/g)].map(m => m[1]);
  assert.ok(emitted.length >= 10, `后端短码没扫全：${emitted.length}`);
  for (const code of new Set(emitted)) assert.ok(REASON_TEXT[code], `前端没有 ${code} 这句中文说明`);
});

test('三板及以上那一格要说清"看的不是当天"', () => {
  const t = titleOf({
    node_hits: '2',
    node_detail: { boards: 5, run_first: '2026-10-08', run_second: '2026-10-09',
      sh_pct: -1.4, vol_vs_prev: 1.8, prev_max_boards: 6, prev_top_count: 2,
      prev_top_sealed: 0, prev_top_broke: 1, prev_top_halted: 1 },
  }, '判据原文');
  assert.match(t, /判据原文/);
  assert.match(t, /第 5 板[\s\S]*不看当天/);
  assert.match(t, /本轮首板日 2026-10-08/);
  assert.match(t, /仍封住 0／断板 1／停牌 1/);
});

test('没命中时把差的那一条念出来；后端连原因都没给时不许编', () => {
  const withWhy = titleOf({ node_hits: '', node_reason: 'n1:sh_pct_not_down', node_detail: { boards: 1 } }, '规则');
  assert.match(withWhy, /这一格是「—」：上证当天跌幅没到 1 个点/);
  const noWhy = titleOf({ node_hits: '', node_reason: '' }, '规则');
  assert.match(noWhy, /后端没给原因/);
  assert.match(titleOf({ node_hits: '' }, null), /判据原文后端没交/);
});

test('命中那句要带上"这不是买入信号"', () => {
  assert.match(titleOf({ node_hits: '1', node_detail: { boards: 1 } }, '规则'), /不是买入信号/);
});

test('整场那句声明：非空才画，页面不自己补一句"今天没命中"', () => {
  assert.equal(warnText(''), '');
  assert.equal(warnText(null), '');
  assert.equal(warnText('   '), '', '只有空格也不算一句声明');
  assert.match(warnText('这一场上证没有当天的那根日线'), /上证/);
});

/* ── 接线：两个调用点真的用同一份 ── */

test('首板页与首页连板榜调的是同一份组件，不各写一格', () => {
  for (const [who, src] of [['首板页', firstBoard], ['首页连板榜', panorama]] as const) {
    assert.match(src, /from "@\/components\/ui\/NodeCell"/, `${who} 没引共用块`);
    assert.match(src, /<NodeCell row=\{/, `${who} 没画那一格`);
    assert.doesNotMatch(src, /node_hits === |renderNode|NODE_LABEL/, `${who} 又自己写了一份翻译`);
  }
  // 整场声明两处都要摆，而且不能只在一处摆
  assert.match(firstBoard, /<NodeWarn text=\{data\?\.node_warn\} \/>/);
  assert.match(panorama, /<NodeWarn text=\{emotion\.node_warn\} \/>/);
});

test('列头的判据原文读后端 node_rule；后端没给就明说没有', () => {
  assert.match(firstBoard, /data\?\.node_rule/);
  assert.match(panorama, /emotion\.node_rule/);
  assert.match(firstBoard, /后端没交 node_rule/);
  assert.match(panorama, /node_rule 为空/);
});

test('读侧那一份才是口径出处：前端没有第二条判据文案', () => {
  assert.match(readSide, /RULE_NOTE = \(/);
  assert.match(readSide, /断板与停牌都算没涨停/);
  // 判据阈值只住判据模块（前端不许出现 1.0 个点 / 6 板 这种数字判断）
  assert.doesNotMatch(comp, />= 6|<= -1\.0|prev_top_sealed === 0/);
});

test('那一格的悬停不吃 title 属性以外的亏：span 上带 title 就够（不是 svg）', () => {
  assert.match(comp, /title=\{titleOf\(row, rule\)\}/);
});
