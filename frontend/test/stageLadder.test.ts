import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  IN_FLIGHT_BATCH, STAGE_QUEUE_NOTE, STAGE_STEP_MS, stageOpen, stageReleaseAt,
} from '../src/lib/stageLadder.ts';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PAGE = fs.readFileSync(path.join(HERE, '../src/pages/Quadrant.tsx'), 'utf8');
const PROBE = fs.readFileSync(path.join(HERE, '../src/lib/probe.ts'), 'utf8');

/**
 * 一屏多块时的分批放行（用户 2026-11-01 点名 /quadrant：「每个模块都显示加载中，那就直接卡住了，
 * 能否逐步加载，先加载 2 个，再 3s 后继续加载」）。
 *
 * 钉的是四件会被悄悄改坏的事：
 *  ① 那两个数（一批两块、每约 3 秒放开下一批）是用户给这一屏的节奏，不许改回"五块一起发"；
 *  ② 第一块不许排队 —— 它一排，首屏就真的一个字都读不到；
 *  ③ 排队中那一块屏上念的必须是「现在还没发这一条请求」，不许念成「取数中…」——
 *     五个转圈与一个转圈读起来是同一件事，那正是"卡住了"的来源；
 *  ④ 也不许念成 `IDLE` 默认那句「这一屏还没有可查的对象（比如没选中标的）」——
 *     这一屏不是没选标的，是页面自己把它排在后面，说成前者就是谎话。
 */

test('放行节奏：第一批两块，之后每约 3 秒放开一批（用户 11-01 给的那两个数）', () => {
  assert.equal(IN_FLIGHT_BATCH, 2, '一次先取两块');
  assert.equal(STAGE_STEP_MS, 3_000, '剩下的每约 3 秒放开一批');
  assert.equal(stageReleaseAt(0), 0, '第 1 块（选股象限）立刻发');
  assert.equal(stageReleaseAt(1), 0, '第 2 块（名次稳定榜）同批立刻发');
  assert.equal(stageReleaseAt(2), 3_000, '第 3 块要等第一批放开');
  assert.equal(stageReleaseAt(3), 3_000, '一批是两块，不是一块排一队');
  assert.equal(stageReleaseAt(4), 6_000, '第 5 块排在第二批之后');
});

test('放行边界与单调性：到点才算开，开了不再关回去', () => {
  assert.equal(stageOpen(2, 2_999), false, '差一毫秒就是还没放行');
  assert.equal(stageOpen(2, 3_000), true, '到点即放行');
  assert.equal(stageOpen(0, 0), true, '第 1 块永远不排队');
  assert.equal(stageOpen(4, 5_999), false);
  assert.equal(stageOpen(4, 60_000), true, '计时器再怎么漂也不许把已经放行的关掉');
});

test('useProbe 收得到"为什么没发"那一句（默认那句讲的是没选中标的，与排队不是一件事）', () => {
  assert.match(PROBE, /skip = false,\s*idleNote\?: string/, '第 5 个参数：这一句由调用方交');
  assert.match(PROBE, /if \(skip\) \{ setState\(\{ kind: "idle", reason: idleReason \}\); return; \}/,
    '没放行时根本不发这条请求（也不许发出去再把结果丢掉）');
  assert.match(PROBE, /const idleReason = idleNote \?\? IDLE_REASON/,
    '没给句子才退回默认那句');
});

test('四维象限：四块要取数的走同一口闸，第一块不排队，归因那块不拦', () => {
  assert.match(PAGE, /const \[, openRs, openRm, openMf\] = useStageLadder\(4\)/,
    '这一屏四条独立请求（概念榜复用第一块的数），闸口号数要对得上');
  assert.match(PAGE, /useProbe<Quadrant>\("\/quant\/quadrant", nonce\)/,
    '第 1 块不排队：它一排队，首屏就真的什么都没有');
  for (const n of ['openRs', 'openRm', 'openMf']) {
    assert.match(PAGE, new RegExp(`!${n}, STAGE_QUEUE_NOTE\\)`),
      `${n} 那块没把 skip 与那句排队话一起交（只交 skip ⇒ 屏上念成"没选中标的"）`);
  }
  assert.equal((PAGE.match(/, STAGE_QUEUE_NOTE\)/g) ?? []).length, 3,
    '排队话正好用在三块上：多了就是有一块在抄句子，少了就是有一块还在跟着抢');
  assert.match(PAGE, /useProbe<Attribution>\("\/risk\/attribution", nonce\)/,
    '归因不进闸：实测 0.006 秒回，为它排到第六秒是浪费（拦的是秒级全库聚合那几条）');
});

test('排队那句自己要把话说全：还没发请求／不是没数据／不用点', () => {
  assert.ok(STAGE_QUEUE_NOTE.includes('还没发这一条请求'), '要说清是没发，不是发了没回');
  assert.ok(STAGE_QUEUE_NOTE.includes('不是这一屏没有数据'), '不许读成空屏');
  assert.ok(STAGE_QUEUE_NOTE.includes('不用点'), '不许把动作踢回给用户');
  assert.equal(STAGE_QUEUE_NOTE.includes('**'), false, '屏幕上不许出现 markdown 星号');
});
