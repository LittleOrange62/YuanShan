import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

/**
 * 「个股深度复盘」新页（用户 2026-10-15 点名，且明确区分了两种进入方式）：
 *  · 从分组自选明细 / 持仓 / 本机名单 **双击** → 浏览器**新窗口**，地址带 `symbol`；
 *  · 导航与主界面那个入口 → 同一屏，**不带传参**（进去先选一只，页面不许替人挑）。
 *
 * 这几条钉的都是"写错了不报错"的那一类：跳转没带参数、新窗口没开成、
 * 四块各存一份"当前票"、全站那个搜索控件又长出第二份实现 —— 屏幕上都看不出来。
 */

const rd = (p: string) => fs.readFileSync(new URL(`../src/${p}`, import.meta.url), 'utf8');
const router = rd('router.tsx');
const review = rd('pages/StockReview.tsx');
const grouped = rd('pages/Grouped.tsx');
const portfolio = rd('pages/Portfolio.tsx');
const watchlist = rd('pages/Watchlist.tsx');
const helper = rd('lib/openStockReview.ts');
const features = rd('lib/workspace/features.ts');
const modules = rd('lib/workspace/modules.ts');

test('新页真的挂在路由上，两条老地址只做跳转、不再各自是一份页面', () => {
  assert.match(router, /\{ path: "\/stock-review", element: <StockReview \/> \}/);
  assert.match(router, /import \{ StockReview \} from "@\/pages\/StockReview"/);
  // 老书签不白屏，但同一件事只留一个入口（`modules.test.ts` 那条"既是跳转又有导航入口＝合并没做干净"也在管这件事）
  assert.match(router, /\{ path: "\/stock-data", element: <Navigate to="\/stock-review" replace \/> \}/);
  assert.match(router, /\{ path: "\/agent\/deepdive", element: <Navigate to="\/stock-review" replace \/> \}/);
  assert.doesNotMatch(router, /path: "\/stock-data", element: <StockData/, '老地址还在独立渲染一份页面＝两个入口指同一份内容');
  assert.doesNotMatch(router, /path: "\/agent\/deepdive", element: <DeepDive/);
});

test('导航与主界面那个入口不带传参（双击那一路才带）', () => {
  // 一级模块的入口与它唯一的页面条目都是 /stock-review（`modules.test.ts` 那条不变量也在管）
  assert.match(modules, /title: '个股研究', to: '\/stock-review'/);
  assert.match(modules, /\{ to: '\/stock-review', title: '个股深度复盘' \}/);
  assert.match(features, /\{ to: '\/stock-review', title: '个股深度复盘'/);
  // 「按钮不带传参」= 导航/卡片给的就是裸路径，不带 `?symbol=`
  assert.doesNotMatch(features, /stock-review\?/);
  assert.doesNotMatch(modules, /stock-review\?/);
  // 老的稳定标识不许删（历史记录与浏览器恢复键存的是 pathname）
  assert.match(modules, /'\/stock-data': '个股数据', '\/agent\/deepdive': '个股深挖'/);
  assert.match(modules, /'\/stock-review': '个股深度复盘'/);
});

test('双击开新窗这件事只有一份实现，三处调用', () => {
  // 10-15 后半段用户把这条扩成全站纪律（「单击高亮行和列，双击跳转到个股深度复盘的新页面 ——
  // 这个也应该是一个全软件的通用脚本或者插件或者代码块哦。但凡有用到的地方，都应该引用他」）：
  // 三屏不再直接接 `useOpenStockReview`，改接把它与交叉高亮合成一份的 `useStockRowInteraction`。
  // 断言跟着改指向，**没放宽**：底层那份仍然只有一处（下面第二条 + `lib/openStockReview.ts` 的独占 `window.open`）。
  for (const [name, src, from] of [
    ['分组自选', grouped, '分组自选'],
    ['重点关注（持仓）', portfolio, '重点关注（持仓）'],
    ['本机名单', watchlist, '本机名单'],
  ] as const) {
    assert.match(src, /import \{[^}]*useStockRowInteraction[^}]*\} from "@\/lib\/useStockRowInteraction"/,
      `${name} 没引那份共用实现`);
    assert.match(src, new RegExp(`useStockRowInteraction\\("${from}"\\)`), `${name} 没调用到点上`);
    // 每个调用方说得出自己是谁（新页要能把来源念给人看）
    assert.doesNotMatch(src, /from "@\/lib\/openStockReview"/,
      `${name} 绕过组合块直接接底层 ⇒ 弹窗告警的 state 会又各写一份`);
  }
  // 页面里不许自己再拼一份地址（拼两份就会有一份忘记 basename 或忘记 noopener）
  for (const src of [grouped, portfolio, watchlist]) {
    assert.doesNotMatch(src, /window\.open\(/, '开新窗只准走 lib/openStockReview.ts 那一份');
    assert.doesNotMatch(src, /["']\/stock-review\?symbol=/, '地址只在共用实现里拼一次');
  }
});

test('那份共用实现：带 symbol 与 from、noopener、被拦时交出一句能照做的告警', () => {
  assert.match(helper, /useHref\("\/stock-review"\)/, 'basename 由 router 算，别硬拼 "/"');
  assert.match(helper, /symbol=\$\{encodeURIComponent\(c\)\}&from=\$\{encodeURIComponent\(from\)\}/);
  assert.match(helper, /window\.open\(url, "_blank", "noopener"\)/, '新窗口不许拿到 opener（本机服务在端口上听着）');
  // 判据在坏的时候不许装没事：被拦必须交出那句话，而不是返回空串让人以为开成功了
  assert.match(helper, /return win \? "" : /);
  assert.match(helper, /浏览器拦下了新窗口/, '要给人工兜底：告诉他放行或自己开这条地址');
  assert.match(helper, /这一行没有代码/, '空代码那一行也要说出来，不是静默返回');
});

test('四块吃同一个真源：只有地址里的 symbol，没有第二份"当前票"', () => {
  assert.match(review, /const code = query\.get\('symbol'\) \|\| ''/);
  assert.ok(review.includes('<StockDossier code={code || null} date={undefined} />'), '档案读地址那一只');
  assert.ok(review.includes('<TrendQuant code={code || null} />'), '趋势量化与窗口趋势读同一只');
  assert.match(review, /<StockData \/>/, '个股研究是同一份实现嵌进来，不是复制一份取数');
  assert.match(review, /<DeepDive \/>/, '多空辩论同上');
  // 嵌进来的两块不再带"独立页/嵌入页"两种形状（独立路由撤了，留着就是 dead branch）
  assert.doesNotMatch(review, /embedded/, '新页不该再传 embedded：那两个组件只剩一种形状');
  const stockData = rd('pages/StockData.tsx');
  const deepDive = rd('pages/DeepDive.tsx');
  assert.doesNotMatch(stockData, /\{ embedded/, 'StockData 的两种形状要收掉一种');
  assert.doesNotMatch(deepDive, /\{ embedded/);
  // 两块都不留自己的选票框：同一屏两个"选标的"必然分叉
  assert.doesNotMatch(stockData, /setCode\(/, '个股研究不许自己写地址');
  assert.doesNotMatch(deepDive, /setStock\(/, '多空辩论同上');
});

test('不带传参进来就明说没选一只，绝不自动挑一只', () => {
  assert.match(review, /还没选一只，这一页不替你挑/);
  assert.doesNotMatch(review, /next\.set\('symbol', "(600519|000001|\d{6})"\)/, '不许写死一只默认票');
  assert.doesNotMatch(review, /commit\("(600519|000001|\d{6})"\)/);
  // 谁自动跑、谁要点一下，屏上必须说清（10-15 实测：个股研究是**自动**打十来个只读接口的，
  // 而多空辩论只有点「开始多空辩论」那一发才出网付费 —— 一句话把两块都说成"不自动跑"就是假话）
  assert.match(review, /只读本机库，选中就出数/, '本机库那几块要说清进页就出数');
  assert.match(review, /个股研究<\/b> 也自动跑/, '个股研究是自动跑的，不许写成"要点了才取数"');
  assert.match(review, /多空辩论<\/b> 不自动跑/, '辩论那一块才是要人点的');
});

test('多空辩论那块把"辩哪一只"与"现在显示的是哪一份"分开念', () => {
  // 10-15 实测撞到的：进页读的是**最近一次已生成**的报告（可能是另一只票），
  // 而页首要辩的是当前选中的那一只。两件事混成一句「对象＝上方选中的那一只」，
  // 人就会把别人的多空结论读成这一只的 —— 所以两种情况各有一句，且不同票时是告警色。
  const dd = rd('pages/DeepDive.tsx');
  assert.match(dd, /点「开始多空辩论」辩的是上方选中的那一只/);
  assert.match(dd, /最近一次已生成的那份报告/);
  assert.match(dd, /text-warning/, '显示的不是同一只时必须用告警色，不是普通小字');
  assert.match(dd, /String\(data\.code \?\? ""\)\.slice\(0, 6\) !== String\(stock\)\.slice\(0, 6\)/,
    '比的是"报告那一只 vs 现在这一只"，不是拿整串字符串硬比（一边带 .SH 一边不带）');
  assert.doesNotMatch(dd, /^\s*对象＝上方选中的那一只/m, '不许再留那句会把两件事说成一件的裸话');
});

test('选一只票全站只剩一只控件：各处都引 StockSearch，没留自己认格式的框', () => {
  const users = ['pages/StockReview.tsx', 'pages/Portfolio.tsx', 'pages/Watchlist.tsx',
    'pages/Journal.tsx', 'components/journal/Reminders.tsx', 'components/ReviewAgentChat.tsx'];
  for (const u of users) {
    const src = rd(u);
    assert.match(src, /import \{ StockSearch \} from "@\/components\/ui\/StockSearch"/, `${u} 没用那只共用控件`);
    assert.match(src, /<StockSearch /, `${u} 引了没接到调用点`);
  }
  // 判据只在后端那一处：组件里除了"剔掉非数字"这类格式夹，不许自己写一份候选过滤
  const comp = rd('components/ui/StockSearch.tsx');
  assert.match(comp, /\/quant\/security-search/, '取候选走那一处接口');
  assert.doesNotMatch(comp, /rows\?\.filter|\.filter\(\(h\) => h\.(name|security_code)/,
    '组件不自己筛字典（各页各筛一份 ⇒ 一样的框三种行为）');
  // 可交易入口限定 stock/etf（不然选中一个指数或板块，档案与辩论都取不到东西）
  for (const u of users) assert.match(rd(u), /<StockSearch[^>]*kinds="stock(,etf)?"/, `${u} 没限定可交易范围`);
});
