import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { connectionLabel, engineHealth, subscriptionStatus } from '../src/lib/workspace/status.ts';
import { FEATURE_GROUPS, FEATURES } from '../src/lib/workspace/features.ts';

test('preserve approved feature groups, merged stocks and research notes', () => {
  /**
   * 这两条等值断言原来是"批准的首页卡片清单"。它的作用是防止卡片漂走，
   * 但副作用是**每加一个页面都要来改一次**，而改的时候看不出是"有意新增"还是"漏了谁"。
   * 2026-09-25 加「数据与选股」那一组（规划 §2 的 9 个新页 + 已存在的 /qd-compare），
   * 这里同步成 6 组 / 24 条；同时保留下面那条"逐个路由都在 router 里注册"的检查 ——
   * 那才是真正会咬人的部分（卡片指向没注册的路由 = 点了掉回首页）。
   */
  // 09-27：「盯盘与核验」那一组随模块一起下线（实时动态 / 盘中核验两页删除）；
  // 「盘面数据」同日第二次裁定：整屏并入首页，独立页删除，所以也不在这张清单里。
  // 10-06：新增「分配者视角」`/allocator`（用户当天点名要做这块功能，判据文档
  // `docs/分配者视角工作指南.md`）。这张清单就是"有意新增"的登记处，改这里要写清是谁批准的。
  // 10-14：撤下「趋势评分」`/scores` 独立页（用户当天裁定：这一屏本来就是针对个股的）——
  // 加权明细与窗口斜率并入 `/grouped` 的量化档案下方，新高板也在那一页；页面文件与接口都留着。
  // 10-15：「个股研究」`/stock-data` 与「多空辩论」`/agent/deepdive` 两张卡合成一张
  // 「个股深度复盘」`/stock-review`（用户当天点名：把这两块功能移进新页，主页那个按钮跳新页、
  // **不带传参**；带代码的那一路改由明细「双击」开新窗口）。老地址在 router 里留跳转。
  // 10-26：`/first-board` 改名「日间复盘及首板」（路由不变，上半新增盘中涨跌停事件流）。
  //        这张清单是登记处，下面那条标题断言把新名字钉住 —— 不是放宽判据。
  assert.equal(FEATURE_GROUPS.length, 5);
  const expected = [
    '/', '/agent/review', '/first-board', '/heat',
    '/stock-review', '/intel', '/my-stocks', '/notes', '/journal', '/backtest', '/settings',
    '/pipeline', '/coverage', '/tdx', '/quadrant', '/sector-heat', '/volume-double', '/volume-breakout', '/grouped',
    '/qd-compare', '/allocator', '/confluence', '/spike', '/trend-stocks', '/jobs',
  ];
  assert.deepEqual(FEATURES.map(x => x.to).sort(), expected.sort());
  const routes = fs.readFileSync(new URL('../src/router.tsx', import.meta.url), 'utf8');
  for (const route of expected) assert.ok(routes.includes(`path: "${route}"`), `首页卡片 ${route} 在 router 里没注册`);
  // 首页 = 原汇总看板（工作台首页已取消，功能格搬进系统配置）；`/` 必须还挂着这一屏。
  assert.ok(routes.includes('{ path: "/", element: <SummaryBoard /> }'), '首页没挂在汇总看板上');
  assert.ok(!routes.includes('<Home />'), '被取消的工作台首页不该还挂在路由上');
  assert.equal(FEATURES.find(x => x.to === '/')?.title, '首页（汇总看板）');
  assert.equal(FEATURES.find(x => x.to === '/backtest')?.title, '涨停样本统计');
  // 2026-10-26 用户裁定：「首板分析」→「日间复盘及首板」（`/first-board` 不变，上半新挂盘中事件流）。
  assert.equal(FEATURES.find(x => x.to === '/first-board')?.title, '日间复盘及首板');
  // 卡片标题与 detail 都要有：只有图标没文字的卡片在窄屏上读不出来
  for (const f of FEATURES) assert.ok(f.title.length > 0 && f.detail.length > 0, `卡片 ${f.to} 缺标题或说明`);
});
for (const [name, input, status] of [
  ['local login only', { installed: true, subscription_ready: true, models_error: '' }, 'authenticated'],
  ['catalog/network failure despite local login', { installed: true, subscription_ready: true, models_error: 'network failed' }, 'error'],
  ['engine missing', { installed: false, subscription_ready: false, models_error: '' }, 'uninstalled'],
  ['logged out', { installed: true, subscription_ready: false, models_error: '' }, 'invalid'],
  ['missing fields', { subscription_ready: true }, 'error'],
  ['wrong shapes', { installed: 'yes', subscription_ready: 'true', models_error: '' }, 'error'],
  ['null response', null, 'error'],
] as const) test(`read-only source state: ${name}`, () => assert.equal(subscriptionStatus(input), status));
test('read-only authentication and saved API never claim verified connection', () => {
  assert.match(connectionLabel('authenticated', 'Codex订阅版'), /连接待实测/);
  assert.match(connectionLabel('saved', 'MiMo API'), /待实测/);
  assert.doesNotMatch(connectionLabel('uninstalled', ''), /登录失效/);
});
test('a half-installed engine is reported as an engine failure, not as a login problem', () => {
  // 判据只有一个来源：后端实测的 installed + engine_reason（半截装时 launcher 在、平台包没了）。
  const half = { installed: false, subscription_ready: false, models_error: '',
    engine_reason: 'Error: Missing optional dependency @openai/codex-win32-x64' };
  const view = engineHealth(half);
  assert.equal(view.status, 'uninstalled');
  assert.match(view.reason, /Missing optional dependency/);
  const label = connectionLabel(view.status, 'Codex订阅版', false, view.reason);
  assert.match(label, /实测跑不起来/);
  assert.match(label, /Missing optional dependency/);   // 原文要上屏，"未就绪"这种废话不能顶替
  assert.doesNotMatch(label, /未登录|登录失效/);            // 不许把半截装说成登录问题
  assert.match(label, /npm ci --prefix runtime/);       // 给出的必须是能做的动作
});
test('a working engine with no login is still a login problem', () => {
  const view = engineHealth({ installed: true, subscription_ready: false, models_error: '' });
  assert.equal(view.status, 'invalid');
  assert.match(connectionLabel(view.status, 'Codex订阅版'), /未登录或登录失效/);
});
test('past probe success is disclosed without overriding current failures', () => {
  assert.match(connectionLabel('authenticated', 'Codex', true), /此前连接实测通过/);
  assert.match(connectionLabel('saved', 'API', true), /此前连接实测通过/);
  for (const status of ['missing', 'checking', 'invalid', 'uninstalled', 'error'] as const) {
    assert.equal(connectionLabel(status, 'Codex', true), connectionLabel(status, 'Codex'));
  }
});
