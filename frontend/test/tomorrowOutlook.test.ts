import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/**
 * 明日展望上屏（用户 2026-11-05：「把任务推送 10 里面的内容展示出来，并针对提到的个股
 * 把这些个股的各类指标全部展示出来」）。钉的五件"写错了也不会报错"的事：
 *  ① 列头 sortKey 必须是那一格真读到的字段（死键不报错、不换序，aria-sort 还会亮）；
 *  ② **绝对价格那 15 项不许出现名次** —— 名次摆出来就是让屏幕说假话（高价股天然靠前）；
 *  ③ 缺值交 `—` 不写 0，中文档名不许在前端抄第二份；
 *  ④ 筛控件（日期、摆不摆指标）要挂在 Panel 的 `controls` 上，挂进 children 会在筛空时一起消失；
 *  ⑤ 两张榜的判据不许在前端重算一遍（页面里不该出现 indicator_value 式的排名逻辑）。
 */

const page = readFileSync(new URL('../src/pages/TomorrowOutlook.tsx', import.meta.url), 'utf8');
const lib = readFileSync(new URL('../src/lib/tomorrowOutlook.ts', import.meta.url), 'utf8');

const { charLevelText, hitSummary, indicatorCell, stockRows, headlineCards, keepText } =
  await import('../src/lib/tomorrowOutlook.ts');

const IND = { key: 'chg10', name: '10日涨幅', unit: 'pct', rankable: true, decimals: 2 };
const PRICE = { key: 'ma5', name: 'MA5（复权价）', unit: 'yuan', rankable: false, decimals: 2 };
const RANKK = { key: 'chg_rank', name: '涨幅排名', unit: 'count', rankable: false, decimals: 0 };

test('① 一、那一行的命中摘要只用后端字段，不自己数', () => {
  assert.equal(hitSummary({ security_code: '600001.SH', hits_today: 6, hits_family: 9,
                             days_in_top: 3, window_sessions: 3 }),
    '今日 6/9 项 · 近 3 场里 3 场上榜');
  // null 必须念 —，不许念成 0（"今日 0/9 项"会被读成"一项都没中"）
  assert.match(hitSummary({ security_code: 'x', hits_today: null, hits_family: 9,
                             days_in_top: null, window_sessions: 3 }),
    /今日 —\/9 项 · 近 3 场里 — 场上榜/);
});

test('② 绝对价格与"本身就是名次"那两族一律不给名次', () => {
  const price = indicatorCell(PRICE, 12.5, 8, 5571);
  assert.equal(price.rank, '', '单位 yuan 的那一项不许渲染名次 —— 只说明它贵');
  assert.match(price.hint, /绝对价格族/);
  const rk = indicatorCell(RANKK, 33, 8, 5571);
  assert.equal(rk.rank, '', '涨幅排名本身就是一名，不再排第二次');
  const ok = indicatorCell(IND, 12.5, 8, 5571);
  assert.equal(ok.rank, '第 8 名');
  assert.match(ok.hint, /母体＝当天这一项有值的 5,?571 只/, '名次必须带自己那一项的分母');
});

test('②b 有值才给名次；缺值说"这一项当天没有值"', () => {
  assert.equal(indicatorCell(IND, null, null, 5571).text, '—');
  assert.match(indicatorCell(IND, null, null, 5571).hint, /没有值 ⇒ 没有名次/);
});

test('③ 股性 NULL 念 —，不念"股性差"，中文档名不抄第二份', () => {
  assert.equal(charLevelText('strong'), '股性强');
  assert.equal(charLevelText('middle'), '股性中');
  assert.equal(charLevelText('weak'), '股性弱');
  assert.equal(charLevelText(null), '—');
  // 档位词只能出现在这一处映射里；页面/说明里再写一份就是两处口径
  assert.equal((lib.match(/股性强/g) ?? []).length, 1);
  assert.equal((lib.match(/股性中/g) ?? []).length, 1);
  assert.equal((lib.match(/股性弱/g) ?? []).length, 1);
});

test('③b 只留哪一档由后端参数说，页面不写死"强"', () => {
  assert.equal(keepText({ char_keep: ['strong'] }), '股性强');
  assert.equal(keepText({ char_keep: ['strong', 'middle'] }), '股性强、股性中');
  assert.equal(keepText({ char_keep: [] }), '不过滤');
});

test('④ 交集为 0 要说"一只都没有"，不许与"没读到"混成一格', () => {
  const d = { date: '2026-11-05', params: { family_size: 9, top_n: 100, min_hits: 4,
    window_sessions: 3, trend_streak_min: 5, trend_n: 6, hits_n: 8 },
    hits: [], trends: [], overlap: [], stocks: {}, universe: {}, cache_age_seconds: 3 };
  const cards = headlineCards(d);
  const both = cards.find(c => c.label === '两榜都进');
  assert.equal(both.value, '—');
  assert.match(both.hint, /实质读数/, '交集 0 只要说"这是实质读数"，否则与没读到分不开');
});

test('⑤ stockRows：一、在前二、在后，交集不重复列，顺序不在前端重排', () => {
  const d = {
    hits: [{ security_code: 'A', name: '甲', hits_today: 6, hits_family: 9, days_in_top: 3, window_sessions: 3 }],
    trends: [{ security_code: 'B', name: '乙', ma5_streak: 9 }, { security_code: 'A', name: '甲', ma5_streak: 2 }],
    stocks: {
      A: { code: 'A', name: '甲', in_hits: true, in_trends: true,
           indicators: [{ key: 'chg10', name: '10日涨幅', unit: 'pct', rankable: true, decimals: 2, value: 12.5, rank: 8, family_n: 5568 }] },
      B: { code: 'B', name: '乙', in_trends: true, indicators: [], missing_indicators: ['ma250'] },
    },
  };
  const rows = stockRows(d);
  assert.deepEqual(rows.map(r => r.code), ['A', 'B'], '同一只票在两榜里只列一次');
  assert.match(rows[0].summary, /站上 5 日线/, '两榜都进的票要把两边的理由并排念出来');
  assert.equal(rows[0].ind.chg10.v, 12.5);
  assert.equal(rows[0].ind.chg10.r, 8);
  assert.equal(rows[1].missing_count, 1, '缺那几项要数出来（那一项是 —，不是 0）');
});

test('⑥ 页面接线：sortKey 都在格子里读得到、控件在 controls、不重算判据', () => {
  // 名字与代码那一列在两张榜里都读 security_code，那格也确实是它
  assert.match(page, /sortKey=\{k\}/s, '列头统一由一个 Th 组件发 sortKey（不散写）');
  for (const k of ['hits_today', 'days_in_top', 'rank_sum', 'char_level', 'ma5_streak', 'pos250', 'pct_change'])
    assert.ok(page.includes(`k="${k}"`), `列头没挂 ${k} —— 死键不报错也不换序，只能靠这条钉住`);
  // 指标那一族按嵌套键排（useSort 支持 a.b.c），键名与格子里读的是同一个 indicator
  assert.match(page, /sortKey=\{`ind\.\$\{c\.key\}\.v`\}/);
  assert.match(page, /cellProps\(i \+ hits\.length \+ trends\.length, `ind\.\$\{c\.key\}`/,
    '交叉高亮的列键必须与渲染那一格的是同一个键');

  // 控件挂在 controls 上（永远渲染），不塞进 children
  const controlsAt = page.indexOf('controls={');
  const childrenAt = page.indexOf('{d => {');
  assert.ok(controlsAt > 0 && childrenAt > controlsAt, '日期与"摆不摆指标"要在 controls 那一行');
  assert.ok(page.indexOf('type="date"') < childrenAt, '日期框长在 controls 里');

  // 不重算判据：前端不该出现自己排一次名次的逻辑
  assert.doesNotMatch(page, /rank\(\)|percent_rank|sort\(\(a, b\) => a\.value/, '页面不许自己排名/重排指标');
  // 取数路径不带 /api 前缀（lib/probe.ts 自己补）
  assert.match(page, /useProbe<OutlookPayload>\("\/quant\/tomorrow-outlook"/);
  assert.doesNotMatch(page, /"\/api\/quant\/tomorrow-outlook"/);
  // 档案块引用共用实现，不在本屏另写一份
  assert.match(page, /import \{ StockDossier \} from "@\/components\/quant\/StockDossier"/);
  // 「订阅选中态」圈在 FocusPane 里：页面组件不许直接 useCellSelection（单击会把 35 列表整个重协调）
  const pageComp = page.slice(0, page.indexOf('/** 一个列头'));
  assert.doesNotMatch(pageComp, /useCellSelection\(/,
    'useCellSelection 提回页面组件 = 每次单击重排整张 35 列表（倍量页实测 461ms 那个形状）');
});

test('⑧ 两张榜与矩阵各自一份排序状态（11-05 浏览器实测抓到串档）', () => {
  // 一、与二、列名有重合（名称与代码／板块／股性）但是两张不同的榜：
  // 共用一份 useTableSort 时，点一、的"板块"会把二、那一列的表头也点亮、行也换序
  // （实测：只点 table[0] 的板块按钮，table[1] 的 th aria-sort 也变成 descending）。
  assert.equal((page.match(/useTableSort\(\)/g) ?? []).length, 3,
    '三张表要三份独立的排序状态（一、／二、／逐票矩阵）');
  assert.doesNotMatch(page, /sort=\{boardSort\}/, '不许再有"两张榜共用一份 boardSort"那个写法');
  assert.equal((page.match(/sort=\{hitSort\}/g) ?? []).length, 6, '一、的六个可排序列');
  assert.equal((page.match(/sort=\{trendSort\}/g) ?? []).length, 8, '二、的八个可排序列');
  assert.equal((page.match(/hitSort\.apply|trendSort\.apply|gridSort\.apply/g) ?? []).length, 3,
    '每张表只排自己那一份行');
});

test('⑦ 后端措辞里"仅留股性强"那一族词不在前端硬编码', () => {
  // "只留股性X"的 X 由 keepText(params) 给；直接写死"只留股性强"会让参数改了屏上还念旧的
  assert.doesNotMatch(page, /只留股性强/, '页面上不许出现写死的档位词');
  assert.match(page, /只留股性\{keepText/, '这一格读的是后端参数');
});
