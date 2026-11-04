import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

/**
 * 「我的股票」那一组（用户 2026-10-31 两点要求，源码级钉住；判前先剥注释）：
 *  ① 每一块模块的「这块的说明」按钮摆在该模块**标题这一行的最右边**；那一行若还有
 *    as-of／时间戳／刷新这类元信息，按钮排在它们**前面**。
 *  ② 一个模块一颗：同一张卡里不许长出两颗同名按钮；正文里原来摊着的 ExplainBox /
 *    CaliberFold 折框搬进标题行那颗抽屉，句子原样搬、屏上不出现第二遍。
 *  ③ 缺数声明、截断上限、误读提醒（NoticeBar 与 warning 色那几行）留在屏上，不进抽屉。
 *  ④ 抽屉里只放本来就在屏上说过的句子，或后端 payload 交出的口径字符串，页面不编。
 *
 * 例外（用户点名）：StockBriefBox 那一格「这家公司是做什么的（AI 说明）」是 AI 正文，不是说明按钮 ——
 * 既不许当重复按钮合并掉，也不许再给它套一颗同名按钮；PriceVolumeTrend 的「Panel 不挂 note」继续有效。
 */

const rd = (p: string) => fs.readFileSync(new URL(`../src/${p}`, import.meta.url), 'utf8');
/** 判前先剥注释：块注释与 JSX 的 {/\* … *\/} 都不算调用点 */
const code = (t: string) => t
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

const FACTS = code(rd('components/stock/StockFacts.tsx'));
const PV = code(rd('components/stock/PriceVolumeTrend.tsx'));
const LEDGER = code(rd('components/stock/StockMetricsLedger.tsx'));
const BRIEF = code(rd('components/stock/StockBriefBox.tsx'));
const GROUPED = code(rd('pages/Grouped.tsx'));
const WATCH = code(rd('pages/Watchlist.tsx'));
const PORTF = code(rd('pages/Portfolio.tsx'));
const MY = code(rd('pages/MyStocks.tsx'));
const SDATA = code(rd('pages/StockData.tsx'));

/** 标题行切片：从块标题那行字到下一个结构（顺序＝渲染顺序） */
function rowBetween(src: string, from: string, to: string, label: string): string {
  const a = src.indexOf(from);
  const b = src.indexOf(to, a);
  assert.ok(a >= 0 && b > a, `${label}：找不到从「${from}」到「${to}」的那一段`);
  return src.slice(a, b);
}

/** 通用一条：这一行最右那一叠里，说明按钮排在元信息（刷新／时间戳／搜索框）前面 */
function assertButtonFirst(row: string, metas: string[], label: string) {
  assert.ok(row.includes('<DocDrawer'), `${label}：标题这一行没有「这块的说明」按钮`);
  assert.ok(row.includes('ml-auto'), `${label}：按钮没落在标题行的最右（缺 ml-auto 那一叠）`);
  for (const m of metas) {
    assert.ok(row.indexOf('<DocDrawer') < row.indexOf(m),
      `${label}：说明按钮必须排在元信息「${m}」前面`);
  }
}

// ——— 个股信息 / 量价走势 / 全指标总账（components/stock）———

test('个股信息：一颗按钮在标题行最右、排在「重新取数」前；缺数与告警压成一行计数、原文在抽屉', () => {
  const row = rowBetween(FACTS, '个股信息</h2>', '<Panel title="这一只是什么', '个股信息');
  assertButtonFirst(row, ['onClick={refresh}'], '个股信息');
  assert.equal((FACTS.match(/<DocDrawer\b/g) ?? []).length, 1, '一个模块一颗');
  assert.match(FACTS, /<DocDrawer title="个股信息">/, '抽屉标题与屏上标题同一个词');
  // 11-03 用户定稿「界面要专注核心」：这一型是屏上留**一行计数**（`CaveatTally` 念有几条、各是哪类），
  // 原文进抽屉那一节。红线没撤 —— 撤的是"必须整段摊在屏上"，留的是"不许静默"。
  assert.match(FACTS, /<CaveatTally items=\{leftCaveats\(d\.caveats\)\} \/>/,
    '屏上那一行计数必须在（一条都不念＝屏幕说"这场没有读不到的地方"）');
  assert.ok(FACTS.indexOf('</DocDrawer>') < FACTS.indexOf('<CaveatTally'),
    '计数行必须排在抽屉之外（它在块内、不在那口弹窗里）');
  assert.match(FACTS, /<Caveats items=\{leftCaveats\(d\?\.caveats\)\} \/>/,
    '原句进抽屉那一节，用的是同一份名单，不是另挑一遍');
  // 11-03 分流：三条里只有「范围声明」`local_only` 进了抽屉（它不是缺数/截断/告警），
  // 另两条（主档两列为空、档案多日未刷新）仍必须摊在屏上。
  assert.match(FACTS, /const leftCaveats = [\s\S]{0,160}filter\(c => c\.code !== "local_only" && c\.text\)/,
    '这一族只筛掉那一条范围声明（它已在第一节念过原话），且没文字的行不摆空条');
  assert.match(FACTS, /\{localScope\(d\?\.caveats\)/,
    '抽屉里那一句念的是后端 `local_only` 原话，不是页面自己写的同义 paraphrase');
  assert.doesNotMatch(FACTS, /<ExplainBox\b|<CaliberFold\b|<Caliber\b/, '正文折框与问号角标都退场');
});

test('量价走势：按钮从表尾折框搬到标题行最右（刷新在后）；留在屏上的那一半仍走 Caveats 出口', () => {
  const row = rowBetween(PV, '量价走势（日线 · 周线 · 月线）</h2>', '<Panel title="左：日线', '量价走势');
  assertButtonFirst(row, ['onClick={refresh}'], '量价走势');
  assert.equal((PV.match(/<DocDrawer\b/g) ?? []).length, 1);
  assert.doesNotMatch(PV, /<CaliberFold\b|<Caliber\b/, '表尾那颗与问号角标只能有一份，且已退场');
  assert.match(PV, /splitCaveats\(d\?\.caveats \?\? \[\]\)/, '分流仍只住 lib/caveatKind.ts 一处');
  assert.match(PV, /<Caveats items=\{visible\} \/>/, '缺数／上限那一半仍摊在屏上');
  assert.ok(PV.indexOf('</DocDrawer>') < PV.indexOf('<Caveats items={visible}'),
    '留在屏上的 Caveats 不许落进抽屉里');
  assert.doesNotMatch(PV, /<Panel title="左：日线[^>]*note=/,
    'Panel 挂 note＝同一模块第二颗同名按钮（10-29 注释里那条「一个模块只留一颗」看牢）');
  // 搬进抽屉的句子一句没丢，也只有一份
  for (const s of ['上图下量；鼠标放到哪一根', '取数口径（后端原句，页面未改写）',
    '为什么图上那根「收」可能不是你记忆里的那个价', '周线与月线的「涨跌」是与上一期收比']) {
    assert.ok(PV.includes(s), `抽屉里少了那段：${s}`);
  }
});

test('全指标总账：h2 行最右一颗按钮，搜索框与刷新排在它后面；how_to_read 进抽屉、fields_note 留屏上', () => {
  const row = rowBetween(LEDGER, '全指标总账</h2>', '<Panel title=', '全指标总账');
  assertButtonFirst(row, ['<label', 'onClick={refresh}'], '全指标总账');
  assert.equal((LEDGER.match(/<DocDrawer\b/g) ?? []).length, 1);
  assert.doesNotMatch(LEDGER, /<Caliber\b/, '问号角标退场，句子搬进抽屉');
  assert.match(LEDGER, /<Panel title=\{`这块屏上一共 \$\{totalFields\} 个字段，分 \$\{blocks\.length\} 块`\} state=\{state\}>/,
    'Panel 不挂 note＝这张卡只剩标题行那一颗');
  assert.match(LEDGER, /heading="怎么读这一屏（后端原句）"[\s\S]{0,140}plain\(led\.how_to_read\)/,
    '后端 how_to_read 原句进抽屉且过 plain 闸');
  assert.doesNotMatch(LEDGER, /text-\[11px\] leading-relaxed text-muted-foreground">\{p\.how_to_read\}/,
    '这句话搬走了就不在屏上摊第二份');
  const after = LEDGER.slice(LEDGER.indexOf('</DocDrawer>'));
  assert.match(after, />\{b\.fields_note\}</, '每块的 fields_note 仍摊在屏上（不写会读成「这块没指标」）');
  assert.match(after, /<Caveats items=\{p\.caveats \?\? \[\]\} \/>/, 'caveats 仍走屏上出口');
});

test('StockBriefBox 那一格是 AI 正文不是说明按钮：没被并掉，也没被再套一颗同名', () => {
  assert.match(BRIEF, /这家公司是做什么的（AI 说明/, '那格标题行原样在（它是 AI 正文的标题）');
  assert.doesNotMatch(BRIEF, /<DocDrawer\b/, '没有给它再套一颗同名按钮');
  assert.doesNotMatch(FACTS, /heading="这家公司是做什么的/, '个股信息的抽屉没把这格 AI 正文当重复按钮合并掉');
});

// ——— 分组自选 ———

test('分组自选「分组」：覆盖与出处并进标题行那颗 note，正文不再长第二颗按钮；caveat 与告警留屏上', () => {
  assert.match(GROUPED, /<Panel title="分组" state=\{groups\} note=\{groupNote\}>/);
  assert.match(GROUPED, /const groupNote = "点组名把下面的明细筛到这一组[\s\S]{0,240}覆盖：\$\{coverageBits\.join/,
    '覆盖区间仍进这一颗抽屉，数字来自后端 coverage 字段');
  assert.match(GROUPED, /出处：\$\{plain\(String\(gd\.source\)\)\}/, '出处走后端 source 原句并过 plain');
  const block = rowBetween(GROUPED, '<Panel title="分组"', '<Panel title="每行一只票', '分组那一块');
  assert.doesNotMatch(block, /<CaliberFold\b|<ExplainBox\b/, '同一张卡里不许长出第二颗同名按钮');
  assert.match(block, /<CaveatText text=\{d\.caveat\} \/>/, '缺数类 caveat 留在屏上');
  assert.match(block, /<Measured>/, '「库里这份不一定最新」那段告警没跟进抽屉');
});

test('分组自选「自选明细」：标题行一颗按钮收四段；截断上限与弹窗告警留在表下', () => {
  const row = rowBetween(GROUPED, '自选明细（所选组）"}</h2>', '<Panel title="每行一只票', '自选明细');
  assert.ok(row.includes('<DocDrawer title={open == null ? "自选明细（全部组）" : "自选明细（所选组）"}>'),
    '按钮没摆在 h2 这一行的最右，或抽屉标题与屏上分了叉');
  assert.ok(row.includes('ml-auto'));
  for (const h of ['日均与区间 · 操作分怎么读', '字段义 · 操作分与象限怎么来的 · 单位',
    '这一张表怎么点 · 双击去哪里', '出处']) {
    assert.ok(row.includes(`heading="${h}"`), `标题行那颗抽屉里少了这一段：${h}`);
  }
  assert.doesNotMatch(GROUPED, /<ExplainBox\b|<CaliberFold\b/, '正文不许再自己长说明框');
  const after = GROUPED.slice(GROUPED.indexOf('</DocDrawer>'));
  assert.match(after, /<NoticeBar kind="truncated">[\s\S]{0,120}再截前 40 行/, '截断上限留在屏上');
  assert.match(after, /<NoticeBar kind="warning">\{popHint\}<\/NoticeBar>/, '弹窗被拦的告警留在屏上');
  assert.match(after, /<CaveatText text=\{d\.caveat\} \/>/, '明细 caveat 留在屏上');
});

test('分组自选「本机名单」：说明挂到「自选股」标题行那一格，孤票检查与只读接口告警留屏上', () => {
  assert.match(GROUPED, /<Watchlist embedded doc=\{<DocDrawer title="自选股">/,
    '这叠的按钮跟着这一叠自己的标题行走（doc 口），句子原样搬');
  // JSX 先后＝渲染先后：这两段排在 doc 那一串之前／之后，都在抽屉外面
  assert.ok(GROUPED.indexOf('不在库里的明细中') < GROUPED.indexOf('<Watchlist embedded doc='),
    '本机孤票检查（缺数类）留在屏上、排在抽屉之前');
  assert.ok(GROUPED.indexOf('一次性迁移导入') > GROUPED.indexOf('</DocDrawer>} />'),
    '「接口只读、不做假导入」的告警留在屏上、排在抽屉之后');
});

// ——— 本机名单组件（自选股）———

test('Watchlist：doc 口挂在标题行那一格；自选总览按钮在最右、排在刷新前；「怎么点」只在抽屉一处', () => {
  assert.match(WATCH, /actions=\{doc\}/, '宿主交来的按钮落在标题行最右（PageHeader actions 那一格）');
  const row = rowBetween(WATCH, '自选总览', '</div>', '自选总览');
  assertButtonFirst(row, ['onClick={() => refresh(codes)}'], '自选总览');
  assert.equal((WATCH.match(/<DocDrawer\b/g) ?? []).length, 1);
  const drawer = rowBetween(WATCH, '<DocDrawer title="自选总览">', '</DocDrawer>', '自选总览抽屉');
  assert.match(drawer, /\{STOCK_ROW_HINT\}/, '抽屉里就是那句「怎么点」');
  assert.doesNotMatch(WATCH.slice(WATCH.indexOf('</DocDrawer>')), /\{STOCK_ROW_HINT\}/,
    '同一句不许在屏上出现第二遍');
  assert.match(WATCH, /text-warning">\{reviewHint\}<\/p>/, '弹窗被拦的告警仍摊在表下、不进抽屉');
});

// ——— 重点关注（持仓）———

test('Portfolio「当前持仓」：h3 行最右一颗按钮收齐两枚问号角标与表下两段；按钮在刷新前，告警没跟进抽屉', () => {
  assert.doesNotMatch(PORTF, /<Caliber\b|<ExplainBox\b/, '问号角标与正文折框退场');
  const a = PORTF.indexOf('当前持仓</h3>');
  const row = PORTF.slice(a, PORTF.indexOf('{scopeErr', a));
  assert.ok(row.includes('<DocDrawer title="当前持仓">'), '按钮没在标题行');
  assert.ok(row.includes('ml-auto'));
  assert.ok(row.indexOf('<DocDrawer') < row.indexOf('onClick={load}'), '按钮排在「刷新」前面');
  for (const h of ['持仓是什么 · 每一格怎么读', '显示范围这几档在筛什么',
    '这一张表怎么点 · 双击去哪里', '要建仓、加仓或平仓去哪里改']) {
    assert.ok(PORTF.includes(`heading="${h}"`), `抽屉里少了那段屏上原来就有的话：${h}`);
  }
  assert.equal((PORTF.match(/<p className="whitespace-pre-wrap">\{plain\(/g) ?? []).length, 2,
    '两段悬停原文进抽屉时过 plain()（剥成对星号），换行用 whitespace-pre-wrap 保住');
  // 缺数／告警类全留在屏上
  assert.match(PORTF, /<span className="text-warning">行情不可用<\/span>/);
  assert.match(PORTF, /合计不完整：\{total\.of/, '合计不完整的缺数声明没进抽屉');
  assert.match(PORTF, /<p role="alert"[^>]*>\{scopeErr\}<\/p>/);
  assert.match(PORTF, /这一档筛不了/, '「名单没读到」那句仍摊在屏上');
  assert.doesNotMatch(PORTF.slice(PORTF.indexOf('</DocDrawer>', PORTF.indexOf('从旧持仓导入'))),
    /\{STOCK_ROW_HINT\}/);
});

test('Portfolio「从旧持仓导入」：按钮在 h3 行最右；「旧记录怎么补日期」的告警留屏上', () => {
  const row = rowBetween(PORTF, '从旧持仓导入</h3>', '<p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">',
    '从旧持仓导入');
  assert.ok(row.includes('<DocDrawer title="从旧持仓导入">'), '按钮没在这一块标题行');
  assert.ok(row.includes('ml-auto'));
  assert.ok(PORTF.indexOf('旧记录没有成交明细') > PORTF.indexOf('</DocDrawer>', PORTF.indexOf('从旧持仓导入')),
    '会读错的账没跟进抽屉，仍摊在屏上');
});

// ——— 重点关注页 ———

test('MyStocks：本机名单那颗按钮跟着「自选股」标题行走，正文不再摆折框', () => {
  assert.doesNotMatch(MY, /<ExplainBox\b/, '折框退场');
  assert.match(MY, /<Watchlist embedded doc=\{<DocDrawer title="自选股">/,
    '说明经 doc 口挂到这一叠标题行的最右');
  assert.match(MY, /同一份数据、同一个组件，不是两叠名单/, '关系那两句原样搬，一个字没删');
});

// ——— 个股研究（StockData）———

test('StockData：个股研究 h2 行最右一颗按钮；三张带口径的卡按钮在标题行、元信息在后', () => {
  const modRow = rowBetween(SDATA, '资金面）</h2>', '<div className="mb-3 flex items-center gap-2', '个股研究');
  assert.ok(modRow.includes('<DocDrawer title="个股研究">'), '模块标题行最右要有这颗按钮');
  assert.ok(modRow.includes('ml-auto'));
  assert.match(modRow, /这一块的代码取自页面上方选中的那一只/, '屏上原来那句代码来源搬进抽屉');
  assert.doesNotMatch(SDATA.slice(SDATA.indexOf('</DocDrawer>')),
    /这一块的代码取自页面上方选中的那一只/, '搬完不许在屏上出现第二遍');
  for (const [title, meta] of [
    ['关键财务指标', 'gstock.metrics.report_date'],
    ['估值历史分位', 'pctl.period'],
    ['财务关键指标', 'fin.period'],
  ] as const) {
    // 「{title}」这三个字还会出现在别处（如登记给 AI 栏的材料串），从抽屉锚点倒查标题行
    const dStart = SDATA.indexOf(`<DocDrawer title="${title}">`);
    assert.ok(dStart > 0, `${title}：按钮没摆在这块标题行`);
    const h3pos = SDATA.lastIndexOf('<h3', dStart);
    assert.ok(dStart - h3pos < 320, `${title}：按钮离这块 h3 太远——没在标题这一行里`);
    const metaPos = SDATA.indexOf(meta, dStart);
    assert.ok(metaPos > SDATA.indexOf('</DocDrawer>', dStart) && metaPos - dStart < 500,
      `${title}：时间戳/窗口元信息要紧跟在按钮后面（排在按钮之后）`);
  }
  // 误读提醒与出处 footer 留在屏上：不构成买卖建议那句不进抽屉
  assert.match(SDATA, /只显示当前处于历史什么位置，不构成买卖建议。<\/p>/,
    '估值分位的防误读半句留在屏上');
  assert.match(SDATA, /仅客观数据，不含买卖建议/, '美港股出处 footer 没被塞进任何抽屉');
  assert.match(SDATA, /本次取数失败/, '取数失败的告警仍摊在屏上');
});

// ——— 全站纪律（这一组范围内）———

test('这一组改动的文件：搬动的中文串不嵌 ASCII 双引号，抽屉正文不露 markdown 星号', () => {
  // 「分是负的/这只票要跌」那两句搬进抽屉时顺手换成「」；不许回退成 ASCII 引号
  assert.doesNotMatch(GROUPED, /所以"分是负的"不等于"这只票要跌"/);
  assert.match(GROUPED, /所以「分是负的」不等于「这只票要跌」/);
  // 带成对粗体的散文进抽屉一律过 plain()：剥掉 plain(...) 之后，抽屉 JSX 里不许残留 `**`
  for (const src of [PORTF, PV, GROUPED, LEDGER, WATCH, MY, SDATA, FACTS]) {
    for (const m of src.matchAll(/<DocDrawer[\s\S]*?<\/DocDrawer>/g)) {
      const noPlain = m[0].replace(/\{plain\([\s\S]*?\)\}/g, '');
      assert.doesNotMatch(noPlain, /\*\*/, '抽屉正文里有没剥星号的散文（过 plain()，或别写粗体标记）');
    }
  }
});

test('一个模块一颗：这一组每个自建标题行恰好一颗按钮；深度复盘页级那颗本就在标题行', () => {
  const expect: [string, number][] = [
    ['components/stock/StockFacts.tsx', 1],
    ['components/stock/PriceVolumeTrend.tsx', 1],
    ['components/stock/StockMetricsLedger.tsx', 1],
    ['components/stock/StockBriefBox.tsx', 0],
    ['pages/Grouped.tsx', 2],      // 自选明细一颗 + 经 doc 口挂到「自选股」标题行的一颗
    ['pages/Watchlist.tsx', 1],    // 自选总览；宿主的 doc 那颗写在宿主文件里，不在这里
    ['pages/Portfolio.tsx', 2],    // 当前持仓 + 从旧持仓导入
    ['pages/MyStocks.tsx', 1],     // 那颗写在传给 doc 口的 JSX 里
    ['pages/StockData.tsx', 4],    // 个股研究 + 三张带口径的卡
  ];
  for (const [file, n] of expect) {
    const src = code(rd(file));
    assert.equal((src.match(/<DocDrawer\b/g) ?? []).length, n, `${file} 的说明按钮数不对`);
  }
  const review = rd('pages/StockReview.tsx');
  // 11-01：这一行从 `flex items-center gap-2` 改成 `flex flex-wrap items-center justify-end gap-2` ——
  // 搜索框(w-72) + 「清掉这一只」+ 那颗「这块的说明」在 568px 宽的内容区里排成一行不折，
  // 实测把内容区顶出 53px 的**页面级**横向滚动条（与首页那两列"钉住却跟着滑走"同一类病）。
  // 这条断言要钉的语义没变：**页级那颗说明按钮必须挂在 PageHeader 的 actions（标题行）里**，
  // 所以只放开类名那一段（`flex…`），仍然要求 actions 的 JSX 内部出现 DocDrawer。
  assert.match(review, /actions=\{<div className="flex[^"]*"[\s\S]{0,1200}<DocDrawer title="个股深度复盘">/,
    '页级按钮挂在 PageHeader 的 actions（标题行）——10-31 这轮没破坏它，StockReview.tsx 因此不用动');
  assert.match(review, /actions=\{<div className="flex flex-wrap[^"]*justify-end/,
    '11-01：actions 那一行要能换行（不换就把内容区顶出页面级横向滚动条，实测溢出 53px）');
});
