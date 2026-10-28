import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { MODULES, moduleFor, chatPageFor } from '../src/lib/workspace/modules.ts';
import { mergeStockCodes } from '../src/lib/workspace/stock-list.ts';

/** 源码路径按本文件位置解析：从仓库根 `node --test frontend/test/*.test.ts`
 *  与从 frontend/ 里跑，都必须同一条命令成立（README 记的是前者）。 */
const SRC = new URL('../src/', import.meta.url).pathname.replace(/^\//, '');

/** `router.tsx` 里注册的真实路由（`{ path: "/x" }`），`*` 兜底不算页面 */
function routerPaths(): string[] {
  const src = readFileSync(SRC + 'router.tsx', 'utf8');
  return [...src.matchAll(/path:\s*"([^"]+)"/g)].map(m => m[1]).filter(p => p !== '*');
}
/** 侧栏底部运维区（`Layout.tsx`）直挂的入口，不走 MODULES */
function opsPaths(): string[] {
  const src = readFileSync(SRC + 'components/layout/Layout.tsx', 'utf8');
  return [...src.matchAll(/to="(\/[^"]*)"/g)].map(m => m[1]);
}
/** 首页那一屏底部的跳转链接 —— 首页本身就是导航项，它链出去的页面算"有入口"。
 *  盘面数据 `/daily-review` 就靠这条可达（09-27 裁定：内容并到首页，导航里不再单列）。 */
function homePaths(): string[] {
  const src = readFileSync(SRC + 'pages/SummaryBoard.tsx', 'utf8');
  return [...src.matchAll(/to="(\/[^"]*)"/g)].map(m => m[1]);
}
const navPaths = () => MODULES.flatMap(m => m.pages.map(p => p.to));

/**
 * 这里原来是一条 `deepEqual(MODULES.map(title), [7 个名字])`。
 * 它在 2026-09-25 是红的 —— 加「汇总看板」时没人同步它。但**只把 8 个名字填回去是错的修法**：
 * 那种断言既挡不住"加了页忘挂导航"（`/portfolio`、`/watchlist` 就孤儿了两天，
 * 页面能跑、没人进得去），也挡不住"挂了导航但路由没注册"（点了跳 404 兜底回首页）。
 * 它唯一真正起的作用是"多一个就得改一次测试"，于是每次加页都要动它、改完就等于放行，
 * 红的时候反而看不出是"有意新增"还是"漏挂"。
 *
 * 换成三条可核对的不变量：顺序与旧模块不丢（防悄悄删导航）+ 双向覆盖（防死链与孤儿页）。
 * 新增页面**不再需要改这条测试**，只需要真的把导航和路由都写对。
 */
test('primary order is pinned and the seven approved modules never disappear', () => {
  assert.equal(MODULES[0].title, '首页');
  assert.equal(MODULES[0].to, '/');
  // 2026-09-27 用户裁定取消「盯盘」一级模块（盘面数据并入首页，另外三页下线），
  // 所以这里不再要求它存在；其余六个一级模块仍然一个都不许悄悄消失。
  for (const t of ['首页', '复盘', '资讯雷达', '个股研究', '回测', '我的股票'])
    assert.ok(MODULES.some(m => m.title === t), `一级模块「${t}」被删掉了 —— 这会让老用户找不到入口`);
  assert.ok(!MODULES.some(m => m.title === '盯盘'), '「盯盘」已按 09-27 裁定下线，不该又长回来');
  assert.ok(!MODULES.flatMap(m => m.pages).some(x => ['/watch', '/yesterday-ladder', '/agent/intraday'].includes(x.to)),
    '被取消的三页不该又以导航条目的形式回来');
  // 盘面数据 09-27 第二次裁定：**整屏并入首页，独立页与路由删除** —— 它既不占导航项，
  // 也不该再被任何链接指着（指着就是一个 404 兜底回首页的死链）。
  assert.ok(!MODULES.flatMap(m => m.pages).some(x => x.to === '/daily-review'),
    '「盘面数据」已并入首页，不该又回到导航里');
  assert.ok(!homePaths().includes('/daily-review'),
    '首页不该再放一条指向已删除页面的链接 —— 内容本身就在首页那一屏');
  const titles = MODULES.map(m => m.title);
  assert.equal(new Set(titles).size, titles.length, '模块标题不得重复');
  const tops = MODULES.map(m => m.to);
  assert.equal(new Set(tops).size, tops.length, '模块入口路由不得重复');
});

/** 纯跳转路由（`{ path: "/x", element: <Navigate to="/y" replace /> }`）：
 *  它们不是页面，不该被要求有导航入口 —— 老书签合并后要有地方落，落点由下一段断言查。 */
function routerRedirects(): Map<string, string> {
  const src = readFileSync(SRC + 'router.tsx', 'utf8');
  return new Map([...src.matchAll(/path:\s*"([^"]+)",\s*element:\s*<Navigate\s+to="([^"]+)"/g)].map(m => [m[1], m[2]]));
}

test('every navigation entry has a registered route, and every route is reachable', () => {
  const routes = new Set(routerPaths());
  for (const to of navPaths()) assert.ok(routes.has(to), `导航指向未注册的路由 ${to}（点了只会掉回首页）`);
  // 一级模块入口必须同时出现在自己的 pages 里，否则 moduleFor() 认不出高亮
  for (const m of MODULES) assert.ok(m.pages.some(p => p.to === m.to), `模块「${m.title}」的入口 ${m.to} 不在自己的 pages 里`);
  const reachable = new Set([...navPaths(), ...opsPaths(), ...homePaths(), '/']);
  const redirects = routerRedirects();
  for (const r of routes) {
    if (r === '/' || redirects.has(r)) continue;
    assert.ok(reachable.has(r), `路由 ${r} 没有任何导航入口（孤儿页）—— 要么挂进 MODULES/运维区，要么删掉`);
  }
  // 跳转的目标必须真的注册着，否则"老链接不丢"只是句好话
  for (const [from, to] of redirects) {
    assert.ok(routes.has(to), `${from} 跳去的 ${to} 没注册，等于跳进 404 兜底`);
    assert.ok(!reachable.has(from), `${from} 既是跳转又有导航入口 —— 合并没做干净`);
  }
});

test('old deep links resolve to their parent, rather than becoming extra primary entries', () => {
  for (const route of ['/heat','/first-board','/backtest','/agent/review']) assert.equal(moduleFor(route)?.title, '复盘');
  // 10-15：「个股研究」与「多空辩论」两屏合进 `/stock-review`（用户点名）。老 pathname
  // `/stock-data`、`/agent/deepdive` 不再是页面，只做跳转 —— 它们「有跳转、无导航入口」
  // 由上面那条不变量管（既是跳转又有入口＝合并没做干净），这里查的是新页归在哪一个模块下。
  assert.equal(moduleFor('/stock-review')?.title, '个股研究');
  // 09-28 用户裁定：交易日志从「我的股票」里拿出来独立成一级模块，排在它后面；
  // 复盘笔记与提醒挂在同一页的三个 tab 上（`?tab=`），所以导航里仍只有 `/journal` 这一条。
  assert.equal(moduleFor('/journal')?.title, '交易日志');
  assert.equal(MODULES.findIndex(m => m.title === '交易日志'),
    MODULES.findIndex(m => m.title === '我的股票') + 1, '「交易日志」要紧跟在「我的股票」后面');
  assert.equal(moduleFor('/notes')?.title, '我的股票');
  assert.equal(moduleFor('/settings'), undefined);
});
test('holdings and watchlist overlap becomes one row without modifying either source', () => {
  const holds = [{code:'000001'}, {code:'000002'}], watch = ['000002','000003'];
  assert.deepEqual(mergeStockCodes(holds, watch), ['000001','000002','000003']);
  assert.deepEqual(watch, ['000002','000003']); assert.equal(holds.length, 2);
});

test('renamed navigation preserves legacy conversation and recovery identities', () => {
  const expected = {'/watch':'盯盘','/agent/review':'复盘看板','/agent/intraday':'盘中核验','/stock-data':'个股数据','/agent/deepdive':'个股深挖','/backtest':'涨停样本统计','/portfolio':'持仓股','/watchlist':'自选股'};
  for (const [route, identity] of Object.entries(expected)) assert.equal(chatPageFor(route), identity);
});

test('watch pages and retained legacy links belong to correct modules',()=>{
  // 盘面数据不再属于任何一级模块（09-27 裁定：内容并到首页，入口是首页里那条链接）
  assert.equal(moduleFor('/daily-review'), undefined);
  assert.equal(moduleFor('/backtest-agent')?.title, '回测');
});
