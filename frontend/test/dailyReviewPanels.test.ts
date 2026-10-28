import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

/**
 * 首页「盘面数据」这一屏（`components/home/MarketPanorama.tsx`；2026-09-27 起取代了
 * 原独立页 `pages/DailyReview.tsx`）的面板形态。
 *
 * 2026-10-06 用户裁定：**「自选股 · 实时行情」那一块取消**。
 * 于是这里从"两块都默认收起"改成三条规矩：
 *  1. 隔夜外围仍默认收起（`useState(false)`），且收起态要交代"有几条"——这两条不许回退。
 *  2. 自选行情卡不许悄悄长回来：组件里不能再有 watch 那套本地状态/表单/折叠。
 *     自选的实时行情在「分组自选」页看，首页不重复一块。
 *  3. 解禁日历的「只看自选」原来读的是那块卡的 localStorage，卡没了就必须改读库 ——
 *     否则在一个从没存过本地名单的浏览器上，那个勾选框永远筛出空集。
 *
 * 顺带钉住情绪两档折线的接法（用户 2026-10-06 要的东西）：卡片里必须真的渲染图、
 * 0 轴的口径必须写在说明里、窗口是 20 场。少一条就是"导入了但没接调用点"。
 */

const root = new URL('../src/', import.meta.url);
const read = (p: string) => fs.readFileSync(new URL(p, root), 'utf8');
const src = read('components/home/MarketPanorama.tsx');

test('隔夜外围默认收起（useState(false)）', () => {
  assert.match(src, /const \[overseaOpen, setOverseaOpen\] = useState\(false\)/);
});

test('收起的是数据，不是功能：展开按钮带 aria-expanded，展开才渲染三组', () => {
  assert.equal((src.match(/aria-expanded=\{overseaOpen\}/g) ?? []).length, 1,
    '折叠按钮要带 aria-expanded，读屏才知道这是一块可展开的区域');
  assert.match(src, /\{overseaOpen && us\.length > 0 &&/);
  assert.match(src, /\{overseaOpen && mag7\.length > 0 &&/);
  assert.match(src, /\{overseaOpen && hk\.length > 0 &&/);
});

test('收起态给的是"有几条"，不是空白', () => {
  assert.match(src, /已收起：\{us\.length \+ hk\.length\} 个指数/);
  assert.match(src, /不是没取到数|不是没有数据/);
});

test('自选行情卡已下线：本地名单状态、增删表单、折叠全部不许回来', () => {
  for (const gone of ['watchOpen', 'watchCodes', 'watchInput', 'setWatchQuotes',
    'loadWatch', 'saveWatch', 'addCodes', 'refreshWatch']) {
    assert.ok(!src.includes(gone), `首页仍在用自选本地状态的 ${gone}（这块已取消）`);
  }
  assert.ok(!src.includes('placeholder="加自选"'), '加自选的表单应只存在于「分组自选」页');
  assert.ok(!/自选股 \/ 外围/.test(src), '副标题不能再承诺已经拿掉的自选行情');
});

test('自选名单的读法改成了库：解禁日历自己取 /watchlist/stocks', () => {
  const uk = read('components/UnlockCalendarPanel.tsx');
  assert.match(uk, /useProbe<[^>]*>\("\/watchlist\/stocks"/);
  assert.ok(!/localStorage\.(get|set)Item\(/.test(uk), '解禁日历不再读浏览器本地名单，跨浏览器一致');
  // DataState 是可辨识联合，直接 .data 会红（也说明没写窄化）
  assert.match(uk, /watch\.kind === "ok"/);
});

test('情绪两档各配一张折线，0 轴口径写进说明，窗口 20 场', () => {
  assert.match(src, /const TREND_SESSIONS = 20/);
  assert.match(src, /<SentimentTrend points=\{seriesPoints\} field=\{m\.field\} sessions=\{TREND_SESSIONS\} \/>/);
  assert.match(src, /breadth_score" as const/);
  assert.match(src, /theme_score" as const/);
  assert.match(src, /const seriesPoints: TrendPoint\[\] = series\?\.available \? series\.points \?\? \[\] : \[\];/,
    '图的数据要先判 available —— 后端明写"读不出"时不能当成"这几天没有情绪"');
  const trend = read('components/SentimentTrend.tsx');
  assert.match(trend, /const MIN_POINTS = 6/);
  assert.match(trend, /折线先不画/);
});
