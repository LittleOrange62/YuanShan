import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/**
 * 2026-10-07 用户要的两格（收盘前「提前拉起通达信」/「只在交易日执行」，以及
 * 「判全不全的偏离值」）在页面上的三条纪律。
 *
 * 这里读源码而不是渲染组件：这三条都是**结构性**的（谁算那个时刻、一次改几件事、
 * 出处有没有报出来），跑一遍 tsc 与浏览器实测都测不出"哪天有人把它改回自己减"。
 */

const panel = readFileSync(new URL('../src/components/CloseChainPanel.tsx', import.meta.url), 'utf8');
const lib = readFileSync(new URL('../src/lib/closeChain.ts', import.meta.url), 'utf8');

test('拉起时刻读后端算好的 launch_at，不许页面自己从时点减提前量', () => {
  assert.match(panel, /dc\.launch_at/, '要显示后端交出的实际拉起时刻');
  // 页面一旦自己写 `slot` 的算术，跨小时/跨零点就会算错，而且没人会发现
  assert.doesNotMatch(panel, /slot\s*[).]?\s*[-+]\s*lead|parseInt\((s|dc)?\.?slot/,
    '拉起时刻必须由后端 `_hhmm_plus` 一处算');
});

test('「只在交易日执行」要说清关掉是"每天都发一次"而不是"每天都会有数"', () => {
  assert.match(panel, /只在交易日执行/);
  assert.match(panel, /段脚本自己会把非交易日拒写/);
  // 判据来源必须点名，否则用户会以为它是"看周六日"
  assert.match(panel, /trade_calendar/);
});

test('偏离值一次只发一件，且出处（默认还是系统配置里填的）必须上屏', () => {
  // 写入面：一次一个键（后端 `set_completeness` 也是这条规矩，两边不一致就会半改完）
  assert.match(lib, /body: JSON\.stringify\(\{ key,\s*\[what\]: value \}\)/);
  assert.match(lib, /deviation_from\?: string \| null; deviation_minute_from\?: string \| null/);
  assert.match(panel, /取自 \{from \?\? "后端没说"\}/,
    '只报数字的话，下一次判错时会去查一个并不存在的改动');
});
