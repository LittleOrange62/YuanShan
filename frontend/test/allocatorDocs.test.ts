import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

/**
 * `/allocator`（分配者视角）这一屏的**说明出口**与**口径单一来源**（用户 2026-10-29 第 4 条：
 * 「每个展示模块都应该包括说明按钮」＋那一轮定的「不允许进弹窗的三型留屏上」）。
 *
 * 两条都是"写错了不报错、屏幕上却出问题"那一类：
 *  · 少一块说明按钮 ⇒ 那一块的分数怎么来的只能靠人猜（这一屏每一项都是形状代理，最不能猜）；
 *  · 列头手抄一遍权重 ⇒ 后端改了权重，列头仍念旧数，而且屏上同时存在两处不同口径。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(HERE, '../src/pages/Allocator.tsx'), 'utf8');
const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const PANEL_TITLES = ['这一屏在算什么', '① 收费站榜（位置分）', '② 拥挤度温度榜（钱正在往哪里灌）',
  '③ 位置 × 想象力 四象限', '④ 阶段分布（再分配生命周期）', '⑤ 故事的热度，与回报是两笔账（本地复算）'];

test('六块 Panel 每一块都有自己的说明出口（一块不落，落一块就是那一块的分数没人解释）', () => {
  // 用户 10-31 定稿：说明按钮统一摆在标题这一行的最右边 —— `Panel note` 那一颗（`DataPanel.tsx` 共用出口）。
  // 旧形状（出口长在正文里、由子组件挂 ExplainBox）已废除：③ 四象限那句「这张散点图怎么读」
  // 整句搬进了本块的 note，正文里不许再卧第二只同名按钮。
  for (const title of PANEL_TITLES) {
    const at = code.indexOf(`<Panel title="${title}"`);
    assert.ok(at >= 0, `屏上少了这一块：${title}`);
    const close = code.indexOf('</Panel>', at);
    assert.ok(close > at, `${title} 这一块没有闭合，判不了它有没有说明`);
    const head = code.slice(at, Math.min(at + 900, close));
    assert.match(head, /note=/, `「${title}」这一块没有 note ⇒ 标题行长不出那颗「这块的说明」`);
  }
  assert.doesNotMatch(code, /<ExplainBox/, '正文里的说明框已搬进标题行：留着就是同一张卡两颗同名按钮（10-29 定的一条一模块一颗）');
  assert.match(code, /docTitle="这些字段是什么意思"/,
    '字段字典那一块也是标题行一颗按钮（走 DocDrawer，内容仍是后端 calibers 那一份）');
});

test('列头的口径句子只从后端 calibers 拿，页面不再手抄权重与窗口', () => {
  // 10-29 之前这里写着「四项分位加权：股息率 35 + 稳定性 25 + 份额中位 20 + 市值占比 20」，
  // 那是 `calibers.toll` 那句的缩略抄本：后端一改，列头就念旧数。
  assert.doesNotMatch(code, /股息率 35 \+ 稳定性 25/, '权重数字不许抄在页面里');
  assert.doesNotMatch(code, /前 20 场（跳过最近 5 场）的中位 − 1/, '窗口口径不许抄在页面里');
  assert.doesNotMatch(code, /自身近 250 场的分位，不是名次/, '分位≠名次这句也读后端那一份');
  assert.match(code, /const calTitle = \(calibers: Record<string, string>, key: string\)/,
    '拿法要收在一个函数里，逐处各写一遍迟早漏一处');
  const uses = (code.match(/calTitle\(calibers?, "/g) ?? []).length;
  assert.ok(uses >= 7, `列头只有 ${uses} 处走后端那一份，说明还有手抄的没换完`);
  // 后端没交这个键时明说没有，不许退回手抄那一份（检测器坏了不许兜成合理读数）
  assert.match(code, /后端没交「\$\{key\}」这一项的口径原句/);
});

test('② 那一块的说明也读 crowding 原文，温度 100 那句话全站只有一份', () => {
  assert.match(code, /calTitle\(cal, "crowding"\)/,
    '「温度 100 = 所有人达成共识，不是该卖了」这句只住后端，页面引用而不是复述');
});

test('#104：象限那两条线只画接口交来的当日中位，不许再画 50/50', () => {
  // `code` 已经剥掉注释，所以这里查的是真会渲染出去的那段
  assert.doesNotMatch(code, /x\(50\)|y\(50\)/,
    '屏幕写「当日中位数」而线画在 50/50 ＝ 线与点两套尺（10-30 实测那一场中位是 51.57/45.68）');
  assert.match(code, /const hasLines = !!medians\?\.available && mx !== null && my !== null/,
    '画不画线由后端那个 available 决定，页面不自己判');
  assert.match(code, /x\(mx as number\)/, '竖线画在交来的想象力中位上');
  assert.match(code, /y\(my as number\)/, '横线画在交来的位置中位上');
  // 线上标的就是那两个数（只写「当日中位」不写数，读者还是无法核对）
  assert.match(code, /当日中位 位置 \{num\(my\)\}/);
  assert.match(code, /中位 想象力 \{num\(mx\)\}/);
  // 取不到分界线：线不画，并且那句为什么要在屏上（缺数类不进抽屉）
  assert.match(code, /!d\.medians\?\.available/);
  assert.match(code, /<NoticeBar kind="missing">/);
  assert.match(code, /不拿 50 顶上/);
});
