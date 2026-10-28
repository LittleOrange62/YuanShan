import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

/**
 * 首页滚动/挂载提速这一轮的四件事（2026-10-26，用户：「首页这个页面滚动起来很卡…
 * 界面功能点从上到下陆续加载和显示，或者你有没有什么办法优化」）。
 *
 * 每条都对应一个**已经量过的**成因，不是"顺手加个优化"：
 *  · 21 个毛玻璃面里 15 个套在另一个毛玻璃面里（DOM 现扫）⇒ 嵌套那层不再 blur；
 *  · `backdrop-filter` 每帧要重取背后快照 ⇒ 只在正在滚的时候摘掉；
 *  · 异动提醒 4,154px／首板分析 2,645px 两节占首页 5,700 节点里的 2,032 个 ⇒ 滚近才画；
 *  · 打开首页主线程长任务合计 521ms、滚动时强制重排只花 0.1ms ⇒ 布局不是瓶颈，
 *    所以这里**没有** `content-visibility`（那条会在读者眼皮底下挪行位置）。
 */

const LAZY = fs.readFileSync(new URL('../src/components/ui/LazySection.tsx', import.meta.url), 'utf8');
const PANO = fs.readFileSync(new URL('../src/components/home/MarketPanorama.tsx', import.meta.url), 'utf8');
const LAYOUT = fs.readFileSync(new URL('../src/components/layout/Layout.tsx', import.meta.url), 'utf8');
const CSS = fs.readFileSync(new URL('../src/index.css', import.meta.url), 'utf8');

test('嵌套毛玻璃不再各自糊一遍，外层与侧栏顶栏保留', () => {
  assert.match(CSS, /\.glass \.glass \{\s*backdrop-filter: none;/,
    '内层 .glass 还带着自己的 blur：嵌套 backdrop-filter 是光栅最贵的一种');
  assert.match(CSS, /\.glass \{[^}]*backdrop-filter: blur\(14px\)/,
    '外层毛玻璃不许一起删掉 —— 用户要撤的是重复那一层，不是这套质感');
});

test('滚动期间摘掉毛玻璃：样式那条在场，且静止后原样回来', () => {
  assert.match(CSS, /html\[data-vscroll\] \.glass,[\s\S]{0,200}backdrop-filter: none;/);
  assert.match(LAYOUT, /root\.dataset\.vscroll = "1"/);
  assert.match(LAYOUT, /delete root\.dataset\.vscroll; \}, 160\)/,
    '没有「停手就恢复」＝滚动完之后整站再没有毛玻璃');
  assert.match(LAYOUT, /addEventListener\('scroll', onScroll, \{ passive: true \}\)/,
    '滚动监听必须 passive：非 passive 会把滚动本身挂在等 JS 上');
  // 一个滚动帧 setState = 每帧重协调整棵树，比它治的病更重。只看这一个 effect 的函数体
  const at = LAYOUT.indexOf('const onScroll');
  const body = LAYOUT.slice(at, LAYOUT.indexOf('}, []);', at));
  assert.doesNotMatch(body, /set(?!Timeout)[A-Z]\w*\(/,
    '滚动回调里不许 setState（只往 <html> 写一个属性，样式表跟着开关）');
});

test('LazySection：只延后、不卸载、认不出观察者就直接画', () => {
  assert.match(LAZY, /typeof IntersectionObserver !== "function"/,
    '没有 IntersectionObserver 的浏览器必须直接渲染（宁可多画，不许永远不画）');
  assert.doesNotMatch(LAZY, /setOn\(false\)/,
    '往回滚不许重新卸载：那会让同一块数据两次「取数中」、并多打一发接口');
  assert.match(LAZY, /rootMargin: `\$\{near\}px 0px \$\{near\}px 0px`/);
  assert.match(LAZY, /这不是「没有数据」/,
    '占位行必须说清「还没画 ≠ 没数」，空框会被读成这一块今天空');
  assert.doesNotMatch(LAZY, /reserve|minHeight|contain-intrinsic-size/,
    '不预留高度：靠猜的预留值会在读者眼皮底下把行挪走，位置跳动只许发生在看不见的地方');
});

test('首页只把「自己取数、自己带控件」的整块延后', () => {
  const wraps = [...PANO.matchAll(/<LazySection label="([^"]+)">\s*<(\w+)\s*\/>\s*<\/LazySection>/g)];
  assert.deepEqual(wraps.map(m => m[2]), ['FirstBoardDailyPanel', 'AbnormalMoveBoard'],
    '接进来的只能是这两个整块（实测 2,645px／4,154px，两个 2,032 个节点）');
  for (const name of ['FirstBoardDailyPanel', 'AbnormalMoveBoard']) {
    const f = fs.readFileSync(new URL(`../src/components/home/${name}.tsx`, import.meta.url), 'utf8');
    assert.match(f, /useProbe</, `${name} 不自己取数的话，延后它就等于延后父页共用的一份数据`);
  }
  // 父页取好、别处共用的那些块（rotation / turnover / emotion）一律不许被包进来
  assert.doesNotMatch(PANO, /<LazySection[^>]*>\s*<(SectorRotationBoard|UnlockCalendarPanel)/,
    '包进别人共用的块＝下面那些块会读到空的');
});

test('闸：这两节的取数入口没被搬进 LazySection 内部', () => {
  // 面板自己 useProbe ⇒ 挂载即取数，滚动到才画；把请求塞进 LazySection 的子层里就会变成"到了才发"
  assert.match(PANO, /<FirstBoardDailyPanel \/>\s*<\/LazySection>/);
  // 文档注释里会提到 `useProbe`（那是解释"接进来的块必须自己取数"），所以只看函数体
  const lazyCode = LAZY.slice(LAZY.indexOf('export function LazySection'));
  assert.doesNotMatch(lazyCode, /useProbe|fetch\(|probe\(/,
    'LazySection 只管画不画，不许自己碰取数');
});
