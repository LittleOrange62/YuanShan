import test from 'node:test';
import assert from 'node:assert/strict';
import {
  registerPageAskAi, releasePageAskAi, readPageAskAi, subscribePageAskAi, samePageAskAi,
  type PageAskAi,
} from '../src/lib/workspace/pageAskAiStore.ts';

/**
 * 右侧「AI分析」栏那一格「问 AI · 本页上下文」的登记表。
 * 这一层唯一的价值是：**问的哪一屏，带的就必须是哪一屏的材料。**
 * 换页没换材料这件事不会报错，只会让模型答得看着很对 —— 所以钉死它。
 */
const mk = (label: string, context = 'ctx'): PageAskAi => ({ label, context });

test('一次只有一屏在册：后登记的顶掉前一个', () => {
  const a = {}, b = {};
  registerPageAskAi(a, mk('首页 · 盘面数据'));
  assert.equal(readPageAskAi()?.label, '首页 · 盘面数据');
  registerPageAskAi(b, mk('个股研究 · 600519'));
  assert.equal(readPageAskAi()?.label, '个股研究 · 600519');
  releasePageAskAi(b);
  assert.equal(readPageAskAi(), null, '释放干净，别让上一页的上下文悬着');
});

test('晚到的清理不许抹掉新页那份（只认自己的 token）', () => {
  const old = {}, fresh = {};
  registerPageAskAi(old, mk('自选股'));
  registerPageAskAi(fresh, mk('个股研究'));
  // 卸载顺序按 React 的实现走：新页先登记、旧页的清理后跑
  assert.equal(releasePageAskAi(old), false, '不是自己那份 → 不动');
  assert.equal(readPageAskAi()?.label, '个股研究');
  assert.equal(releasePageAskAi(fresh), true);
  assert.equal(readPageAskAi(), null);
});

test('内容没变不通知：首页每 30~60 秒刷新不能把栏和流式回答抖重挂', () => {
  const token = {};
  const calls: string[] = [];
  const off = subscribePageAskAi(() => calls.push('n'));
  registerPageAskAi(token, mk('首页 · 盘面数据', '同样的数'));
  registerPageAskAi(token, mk('首页 · 盘面数据', '同样的数'));
  assert.equal(calls.length, 1, '同 token 同内容第二次登记不该再通知');
  registerPageAskAi(token, mk('首页 · 盘面数据', '换了一场数'));
  assert.equal(calls.length, 2);
  off();
  registerPageAskAi(token, mk('首页 · 盘面数据', '再一场'));
  assert.equal(calls.length, 2, '退订之后不再被叫');
  releasePageAskAi(token);
});

test('传 null = 交回这一屏（这一屏此刻没有可带的材料）', () => {
  const token = {};
  registerPageAskAi(token, mk('个股研究'));
  registerPageAskAi(token, null);
  assert.equal(readPageAskAi(), null);
  // 手里没登记过的人传 null 也不该动别人的
  registerPageAskAi(token, mk('自选股'));
  registerPageAskAi({}, null);
  assert.equal(readPageAskAi()?.label, '自选股');
  releasePageAskAi(token);
});

test('快照引用稳定（useSyncExternalStore 拿不稳定的读函数会死循环）', () => {
  const token = {};
  const v = mk('首页 · 盘面数据', 'abc');
  registerPageAskAi(token, v);
  assert.equal(readPageAskAi(), readPageAskAi(), '两次读取必须是同一个对象');
  assert.ok(samePageAskAi(v, { label: '首页 · 盘面数据', context: 'abc', suggestions: undefined }));
  assert.ok(!samePageAskAi(v, { label: '首页 · 盘面数据', context: 'abc', suggestions: ['x'] }));
  assert.ok(!samePageAskAi(v, null));
  releasePageAskAi(token);
});
