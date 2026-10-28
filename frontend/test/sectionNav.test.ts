import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/**
 * 长页目录（`SectionNav`）。钉的是 10-16 用户那一句：
 * 「系统设置里为什么我还是没有看到这个推送功能相关的按钮」——
 * 那一屏约 14 屏高，「消息推送」排在 12,646px，功能做完了但人翻不到。
 *
 * 所以这里守的三件事：
 *  ① 页面必须**挂了**目录（不是文档里写"往下滚"）；
 *  ② 目录的条目必须**从屏幕上的标题现扫**，不是再抄一份写死的清单
 *     —— 写死的清单第二天就会少一行，而"目录里少一行"与"那块不存在"在用户眼里是同一件事；
 *  ③ 点了要真跳（`scrollIntoView`），不是摆设。
 */

const nav = readFileSync(new URL('../src/components/SectionNav.tsx', import.meta.url), 'utf8');
const sys = readFileSync(new URL('../src/pages/System.tsx', import.meta.url), 'utf8');

test('系统配置这一屏必须挂上目录，而且挂在正文第一块之前', () => {
  assert.match(sys, /import \{ SectionNav \} from/, '没 import 目录组件');
  assert.match(sys, /<SectionNav \/>/, 'System.tsx 里没挂载');
  const atNav = sys.indexOf('<SectionNav />');
  const atFirst = sys.indexOf('id="home-features"');
  assert.ok(atNav > 0 && atFirst > atNav, '目录必须在第一块卡片前面（在后面就等于没有）');
});

test('目录条目是现扫标题，不是抄一份写死清单', () => {
  assert.match(nav, /querySelectorAll\("h2, h3"\)/, '没有现扫屏幕上的标题');
  // 写死任何一块的名字都会随着改动漂：代码里应当一个具体卡片名都没有。
  // ⚠️ 只看代码：这个组件的**注释**里要讲清"为什么要有这一条"（用户翻不到那一屏），
  //    所以把块名写在注释里是有意的，不能一并算成违规。
  const code = nav.replace(/\/\*[\s\S]*?\*\//g, "").split("\n").filter((l) => !l.trim().startsWith("//")).join("\n");
  for (const banned of ['消息推送', '收盘链', '局域网', '常驻循环', '运维入口']) {
    assert.ok(!code.includes(banned), `目录代码里出现了写死的块名「${banned}」—— 会漏、会过期`);
  }
});

test('点了要真跳：给标题补 id、滚动、并短暂标记目标', () => {
  assert.match(nav, /h\.id = `sec-/, '标题没有 id 时要在扫描时补上，否则跳不过去');
  assert.match(nav, /scrollIntoView\(/, '只是渲染了一排按钮，点了不动');
  assert.match(nav, /classList\.add\(/, '跳过去要短暂标一下目标，不然长页面里看不出来落在哪');
  assert.match(nav, /closest\("main"\)|querySelector\("main"\)/, '扫描范围要限定在本屏，不然把右侧栏标题也收进来');
});

test('短屏不显示目录，块数要写在标题上（让人能核对是不是漏了）', () => {
  assert.match(nav, /entries\.length < 4\) return null/, '不足 4 块的短屏不该占一行');
  assert.match(nav, /本页目录（\{entries\.length\} 块）/, '要说清数到几块，否则人无从判断有没有漏');
});
