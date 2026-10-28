import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const rd = (p: string) => fs.readFileSync(new URL(`../src/${p}`, import.meta.url), 'utf8');

/**
 * 用户 2026-10-14：「趋势评分这个功能点主要是针对个股的」—— 并进「我的股票 · 分组自选」，
 * 独立屏撤掉。这几条钉的是**这次合并没做歪**：新块真的接到了调用点、旧入口一处不剩、
 * 清单级的那一块（新高）也真的有新家。
 */

test('趋势量化那一叠搬到了个股深度复盘，并且吃的是地址里那一只票', () => {
  // 10-14 它住在 /grouped 的档案下面；10-15 用户点名把这几叠个股功能点合进「个股深度复盘」。
  // 断言跟着换宿主（**不删**）：位置关系、"只吃现在这一只"这两件事一条没少。
  const r = rd('pages/StockReview.tsx');
  assert.match(r, /import \{ TrendQuant \} from "@\/components\/quant\/TrendQuant"/);
  assert.ok(r.indexOf('<StockDossier') < r.indexOf('<TrendQuant code='), '趋势量化没排在档案下面');
  assert.match(r, /<TrendQuant code=\{code \|\| null\} \/>/,
    '这一块的母体必须是地址里那一只（页首选中的、或双击带进来的），不是页面自己另猜一只');
  assert.match(r, /const code = query\.get\('symbol'\) \|\| ''/,
    '一只票一个真源：两处各存一份"当前票"就会出现高亮的行与档案不是同一只票');
  // 双击那一路带过来的来源要能在屏上说出来（不然人不知道这一只是从哪一叠开的）
  assert.match(r, /const from = query\.get\('from'\) \|\| ''/);
  assert.match(r, /双击那一行带过来的/);
});

test('自选股新高（清单级）搬进了分组自选，仍带着它自己的接口', () => {
  const g = rd('pages/Grouped.tsx');
  assert.match(g, /import \{ NewHighBoard \} from "@\/components\/quant\/NewHighBoard"/);
  assert.ok(g.includes('<NewHighBoard />'), '新高板没接到页面上 —— 那一屏就随旧页一起没了');
  const nh = rd('components/quant/NewHighBoard.tsx');
  assert.match(nh, /useProbe<NewHigh>\("\/quant\/new-high"/, '页面里绝不写 /api 前缀，probe 自己补');
});

test('没选中票的时候两个请求都不发', () => {
  const t = rd('components/quant/TrendQuant.tsx');
  assert.match(t, /const sec = \(code \?\? ""\)\.trim\(\);/);
  assert.match(t, /if \(!sec\) \{/, '没选票时要有出口，不能拿空 code 去打后端');
  assert.match(t, /useProbe<Scores>\("\/quant\/scores", nonce, \{ code \}\)/);
  assert.match(t, /useProbe<Trend>\("\/quant\/score-trend", nonce, \{ code \}\)/);
});

test('趋势评分那一屏的入口一处不剩（路由、卡片、导航、跳转都撤了）', () => {
  const files = ['router.tsx', 'lib/workspace/features.ts', 'lib/workspace/modules.ts',
    'pages/System.tsx', 'pages/Grouped.tsx', 'pages/StockReview.tsx', 'pages/SectorHeat.tsx', 'pages/DataProcess.tsx',
    'components/quant/IndicatorGrid.tsx', 'components/quant/StockDossier.tsx',
    'components/quant/TrendQuant.tsx', 'components/quant/NewHighBoard.tsx'];
  for (const f of files) {
    assert.ok(!/to=["']\/scores["']|path: "\/scores"/.test(rd(f)), `${f} 里还指着 /scores`);
  }
  const r = rd('router.tsx');
  assert.match(r, /\{ path: "\*", element: <Navigate to="\/" replace \/> \}/,
    '老书签要有兜底；撤页后落回首页是这一仓的既有做法');
});

test('这一屏只有一份 35 项技术指标、一份加权明细，不重复摆', () => {
  const t = rd('components/quant/TrendQuant.tsx');
  assert.ok(!t.includes('IndicatorGrid'),
    '35 项技术指标留在「个股研究」那一块，档案这块再放一份就是两张同源表');
  // 10-15 搬家后那句指向变成"同页下面那一块"（原来指的是另一个页面）。
  assert.match(t, /35 项技术指标在同页下面/, '要指得出技术指标在哪一块，且不许自己重算一份');
  assert.match(t, /「个股研究」/, '指向的名字要跟那一块的标题对上');
});

test('页面自己写的句子里不许有 markdown 星号', () => {
  const body = rd('components/quant/TrendQuant.tsx').replace(/\/\*[\s\S]*?\*\//g, '');
  assert.ok(!body.includes('**'), '源码正文里有字面 ** —— 会被当正文渲染出来');
});


/** 2026-10-15 新加的一列：「高点在窗口第几天」（用户点名要数字＋词，大小用同色系深浅）。 */
test('高点位置那一列：键是真字段、词来自后端、深浅不碰红绿', () => {
  const tq = rd('components/quant/TrendQuant.tsx');
  const card = tq.slice(tq.indexOf('function Windows'));
  assert.ok(card.length > 500, '没找到窗口趋势那一段');
  // ① sortKey 必须等于格子里读的字段（写错不报错、也不换序，aria-sort 会照样亮着骗人）
  assert.match(card, /label="高点在第几天" sortKey="x_plus_y_max_day_no"/);
  assert.match(card, /r\.x_plus_y_max_day_no/, '列头键与格子里读的是同一个字段');
  assert.match(card, /x_plus_y_max_position/, '那一句词读后端给的，不是页面自己比大小拼的');
  assert.doesNotMatch(card, /刚创新高|中途见顶|起点附近/, '页面不许抄一份阈值词表（阈值只住后端 `_peak_position`）');
  assert.match(card, /d\.peak_rule/, '列头悬停与说明框都转述后端那句 peak_rule');
  // ② 深浅走同色系（primary 阶梯），方向的红绿留给斜率与贡献
  assert.match(card, /heatText\(r\.x_plus_y_max \?\? null, lo, hi\)/, 'X+Y 最高那一列走深浅');
  assert.match(card, /pctColor\(r\.slope \?\? null\)/, '斜率仍是方向色，两种语义不混');
  const colors = rd('lib/colors.ts');
  const heat = colors.slice(colors.indexOf('export function heatText'), colors.indexOf('export function jobCardClass'));
  assert.ok(heat.length > 100, '没找到 heatText');
  assert.doesNotMatch(heat, /UP_TEXT|DOWN_TEXT|text-danger|text-success/, '深浅里不许出现涨跌方向色');
  assert.match(heat, /text-primary\/(?:30|45|65|85)/, '同色系透明度阶梯');
  // ③ 归一范围必须写在屏幕上：不然「深色」会被读成绝对意义的「强」
  assert.match(card, /本屏这几行之间/, '深浅要说清是在本屏这几行之间比的');
});

/** 用户 2026-10-22 点名窗口趋势那一块「把以下内容收缩起来」。这几条钉的是**分层**，
 *  不是"折了几个框"：描述进框，而截断／缺数／缓存年龄那一族必须还在屏上 ——
 *  折掉「交出 …；表里列前 40 行」，屏幕就能把"只列 40 行"念成"这票只有 40 个窗口"。 */
test('窗口趋势那一块：描述收进框，截断与缺数摊在外面', async () => {
  const { FOLDABLE_CAVEAT_CODES } = await import('../src/lib/caveatKind.ts');
  const card = rd('components/quant/TrendQuant.tsx').slice(
    rd('components/quant/TrendQuant.tsx').indexOf('function Windows'));
  const boxStart = card.indexOf('<ExplainBox title="这一屏看的是谁的窗口');
  const boxEnd = card.indexOf('</ExplainBox>', boxStart);
  assert.ok(boxStart > 0 && boxEnd > boxStart, '那两句描述要有一处默认收起的框（不带 open 属性就是收起）');
  const box = card.slice(boxStart, boxEnd);
  assert.match(box, /<Row label="这是谁的窗口"/, '「这是谁的窗口」折进框（点开可见）');
  assert.match(box, /<Row label="窗口 \/ 门槛"/, '「窗口 / 门槛」折进框');
  assert.doesNotMatch(box, /text-warning|role="status"|<Caveats /, '框里不许出现告警');

  const rows = card.slice(0, boxStart);
  assert.match(rows, /<Row label="交出" value=\{`\$\{dash\(d\.sessions_returned\)\} 场/,
    '「交出 59 场…；表里列前 40 行」是截断声明，必须排在任何折叠框之前');
  assert.ok(card.indexOf('<Row label="交出"') < card.indexOf('<CaliberFold'),
    '截断声明不许被 CaliberFold 收走');
  assert.doesNotMatch(card.slice(0, card.indexOf('<Row label="交出"')), /<ExplainBox|<CaliberFold/,
    '交出那一行前面不能再有折叠框——排在框里就等于藏了');
  assert.match(card, /<CaliberFold caveats=\{d\.caveats\} definitions=\{d\.definitions\} \/>/,
    '声明与字段义仍走共用折叠块（缺数那一族由它自己留在屏上）');

  for (const code of ['cached_aggregates', 'thin_window', 'window_entry_quadrant_null'])
    assert.equal(FOLDABLE_CAVEAT_CODES.has(code), false,
      `${code} 是缓存年龄／缺数计数，被折进框就等于让屏幕说假话`);
});
