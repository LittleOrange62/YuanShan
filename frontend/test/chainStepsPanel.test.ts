import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/**
 * 「加工段逐步结果 + 单步补跑 + 夜间重试阶梯」这一格的接线证据（源码级）。
 *
 * 为什么用读源码这种钉子而不是渲染测试：这一格最容易坏的方式是**后端交出了、界面没接**
 * （`retry`/`times`/`refused` 三个字段都在载荷里，页面上却看不见也改不动）——
 * 那正是"看着有其实没接"。断言只查"有没有接到调用点"，不查样式。
 */
const src = (p: string) => readFileSync(new URL(`../src/${p}`, import.meta.url), 'utf8');

test('阶梯的档期在常驻循环那一格里改得动（不是只读的文字）', () => {
  const board = src('components/LoopSwitchboard.tsx');
  const lib = src('lib/closeChain.ts');
  // 这一段用**子串**而不是正则：`?? ` 里的问号在正则里是量词，写正则的钉子会自己先骗自己。
  const has = (text: string, needle: string, why: string) =>
    assert.ok(text.includes(needle), `${why}（缺 ${needle}）`);
  has(board, 'l.times != null', "没在册的行不许渲染出一个改了也不生效的框");
  has(board, 'onTimes(timesDraft.trim())', "保存要真的提交");
  has(board, 'onTimes(l.times_default ?? "")', "删过头要能一键退回默认档期");
  has(board, '重试档期', "控件要有名字，不能只是一个裸输入框");
  has(lib, "'weekly_days' | 'times'", "写入面要认 times 这个键");
  has(lib, 'times_default?: string | null', "类型要跟上后端交出的字段");
  has(lib, 'retry?: RetryStatus | null', "阶梯读数要有出口");
});

test('阶梯读数在补跑那一格看得见：待补、下一档、档期、放弃各有一说', () => {
  const panel = src('components/ChainStepsPanel.tsx');
  assert.match(panel, /s\?\.retry/);
  for (const phrase of ["待补", "下一档", "档期", "已放弃", "关着"]) {
    assert.ok(panel.includes(phrase), `阶梯读数里少了「${phrase}」这一说`);
  }
  // 时点改在常驻循环那一格，这里要指得准（不能让人以为本格也能改）
  assert.match(panel, /常驻循环与定时任务/);
});

test('按口径拒写不等于故障：三态都要在界面上分得开', () => {
  const panel = src('components/ChainStepsPanel.tsx');
  assert.match(panel, /st\.status === "refused"/);
  assert.match(panel, /按口径拒写 rc=/);
  assert.match(panel, /不算故障/);
  // 没成的那一格仍然给"补跑这一步"，但 refused 的按钮要说清归属日的事
  assert.match(panel, /归属日/);
  const lib = src('lib/chainSteps.ts');
  assert.match(lib, /refused_steps\?: string\[\]/);
  assert.match(lib, /merged\?: string \| null/);
});

test('「链收工后自动关客户端」这一档接得住（默认关要说清为什么是关）', () => {
  const panel = src('components/CloseChainPanel.tsx');
  const lib = src('lib/closeChain.ts');
  const has = (text: string, needle: string, why: string) =>
    assert.ok(text.includes(needle), `${why}（缺 ${needle}）`);
  has(panel, 'patchChain("daily_close", "close_client_after"', "复选框要真写到后端");
  has(panel, 'dc.close_client_default === true ? "开" : "关"', "默认值要说实话，别让人以为默认是开");
  has(panel, '不强杀主程序', "默认关的理由（保持登录、误杀代价）要写在旁边");
  has(lib, "'trading_days_only'\n        | 'close_client_after'", "写入面的键名要跟上后端");
  has(lib, 'close_client_after?: boolean | null', "类型要跟上后端交出的字段");
});
