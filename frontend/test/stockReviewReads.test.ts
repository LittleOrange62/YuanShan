import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  fetchedAgeSentence, missingKindSentence, nextQuarterReading, nextYearReading,
  periodLabel, pct, thisYearReading, yi, type ForecastBlock,
} from '../src/lib/financialPeriod.ts';
import {
  groupNames, identitySentence, profileAgeSentence, sectorLabels, sessionProfileSameDay,
  yi as factsYi, type FactsPayload,
} from '../src/lib/stockFacts.ts';
import {
  PART_LABEL, afterSentence, cohortLabel, missingPartsSentence, points, ratio,
  readingSentences, scoreBand, sparkPath, topPartsSentence, type SimRow, type SimilarityPayload,
} from '../src/lib/volumeSimilarity.ts';
import {
  amtText as pvAmtText, barDate as pvBarDate, barLabel as pvBarLabel, barTooltip as pvBarTooltip,
  isUp as pvIsUp, maPoints as pvMaPoints, pctText as pvPctText, pickBarIndex as pvPickBarIndex,
  price as pvPrice, priceExtent as pvPriceExtent, readoutCells as pvReadoutCells,
  readoutSource as pvReadoutSource, seriesCaption as pvSeriesCaption, volText as pvVolText,
  type PvBar,
} from '../src/lib/priceVolumeTrend.ts';

/**
 * 个股深度复盘这一轮新加的读数块（个股信息 / 财务多期与外推 / 近 30 根量价与相似样本 / 量价走势日周月）的
 * 判读层 + 页面接线。每条断言都对应一句「屏幕会说谎」的情形，不是格式洁癖。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = (rel: string) => fs.readFileSync(path.join(HERE, rel), 'utf8');
const factsComp = read('../src/components/stock/StockFacts.tsx');
const finComp = read('../src/components/quant/FinancialPeriodBoard.tsx');
const simComp = read('../src/components/quant/VolumeSimilarityBoard.tsx');
const pvComp = read('../src/components/stock/PriceVolumeTrend.tsx');
const page = read('../src/pages/StockReview.tsx');
const system = read('../src/pages/System.tsx');

const facts = (o: Partial<FactsPayload>): FactsPayload => ({ available: true, code: '600519.SH', ...o });
const sim = (o: Partial<SimilarityPayload>): SimilarityPayload => ({ available: true, code: '600519.SH', ...o });
const row = (o: Partial<SimRow>): SimRow => ({ security_code: '000001.SZ', ...o });

// ============================================================ 一、个股信息

test('缺数是「—」，0 就写 0（0 亿是真的 0）', () => {
  assert.equal(factsYi(null), '—');
  assert.equal(factsYi(Number.NaN), '—');
  assert.equal(factsYi(0), '0.00 亿');
  assert.equal(factsYi(16413.19), '16413.19 亿');
  assert.equal(pct(null), '—');
  assert.equal(pct(2.0972), '+2.1%');
  assert.equal(pct(2.0972, 2), '+2.10%');
  assert.equal(pct(-0.5), '-0.5%');
  assert.equal(pct(-0.5, 2), '-0.50%');
});

test('涨跌配色：缺数不给颜色，平盘不给绿', () => {
  assert.equal(sessionProfileSameDay({ trade_date: '2026-10-15' }, { hq_date: '2026-10-15' }), true);
  assert.equal(sessionProfileSameDay({ trade_date: '2026-10-15' }, { hq_date: '2026-09-24' }), false);
  assert.equal(sessionProfileSameDay(null, { hq_date: '2026-09-24' }), null);   // 不知道 ≠ 不同日
});

test('档案年龄三档措辞，没交时间就说不知道', () => {
  assert.match(profileAgeSentence(3), /更新于 3 天前/);
  assert.match(profileAgeSentence(45), /45 天没刷新/);
  assert.match(profileAgeSentence(400), /只能当旧闻读/);
  assert.match(profileAgeSentence(null), /不知道/);
});

test('板块名单带成分只数，空表说的是没登记不是没板块', () => {
  assert.equal(sectorLabels([]), '本机字典里没登记它的板块归属');
  assert.equal(sectorLabels([{ sector_code: 'A', name: '白酒', members: 19 }]), '白酒(19只)');
  assert.equal(sectorLabels([{ sector_code: 'A', name: '白酒', members: null }]), '白酒(—只)');
  const many = Array.from({ length: 12 }, (_x, i) => ({ sector_code: `S${i}`, name: `板${i}`, members: i }));
  assert.match(sectorLabels(many, 10), /共 12 个/);
});

test('身份那句把 ST 与本机名单两件事分开说', () => {
  const d = facts({ board: '主板', is_st: true, groups: [{ group_name: '核心跟踪' }] });
  assert.equal(identitySentence(d), '主板 · ST 档（涨跌停与异动判据都按 ST 走） · 在本机名单：核心跟踪');
  assert.match(identitySentence(facts({ board: '创业板', groups: [] })), /不在本机自选名单里/);
  assert.deepEqual(groupNames(null), []);
});

// ============================================================ 二、财务多期与外推

test('报告期叫法：季末日期不等于披露日，界面说「几季/年报」', () => {
  assert.equal(periodLabel('2026-03-31'), '2026 一季');
  assert.equal(periodLabel('2026-06-30'), '2026 中报');
  assert.equal(periodLabel('2026-12-31'), '2026 年报');
  assert.equal(periodLabel(null), '—');
});

test('两法分歧时不给单一数，并把两法各自的数念出来', () => {
  const b: ForecastBlock['next_quarter'] = {
    label: '下一季（2026-09-30）', value: null, divergence: 72.2,
    method_a: { value: 250, base_period: '2025-09-30', growth_percent: 150 },
    method_b: { value: 900, base_period: '2026-06-30', season_ratio: 1 },
    reason: 'A（同比外推）与 B（季节环比）相差 72%',
  };
  const r = nextQuarterReading(b);
  assert.equal(r.tone, 'warn');
  assert.equal(r.value, null);
  assert.match(r.sentence, /不给单一数/);
  assert.match(r.sentence, /250\.00 亿/);
  assert.match(r.sentence, /900\.00 亿/);
});

test('判不了与"两法打架"是两句不同的话', () => {
  assert.equal(nextQuarterReading({ value: null, reason: '单季只有 5 期' }).tone, 'missing');
  assert.match(nextQuarterReading({ value: null, reason: '单季只有 5 期' }).sentence, /判不了：单季只有 5 期/);
  assert.equal(nextQuarterReading(null).sentence, '后端没交这一格');
});

test('「当年」那一格把已披露期数与补的季分开念', () => {
  const t: ForecastBlock['this_year'] = {
    year: 2026, reported_through: '2026-06-30', disclosed: 2, forecast_value: 1699.83,
    basis: '已披露累计 + 外推季',
    missing_quarters: [
      { period: '2026-09-30', value: 382.1, basis: '同比外推' },
      { period: '2026-12-31', value: null, fail_reason: '缺季节样本' },
    ],
  };
  const s = thisYearReading(t);
  assert.match(s, /本机读到 2 期/);
  assert.match(s, /补了 1 个未披露季，1 个季补不下去/);
  assert.match(s, /1699\.83 亿/);
  assert.match(thisYearReading({ year: 2025, disclosed: 4, forecast_value: 100, missing_quarters: [] }),
    /四季都已披露，这一格不是预测/);
});

test('明年那一句说明情景带不是置信区间', () => {
  const s = nextYearReading({ year: 2027, value: 1851.6, growth_percent: 8.93,
    scenario_low: 1851.6, scenario_high: 1980.8, cagr_basis: '5年×0.4 + 3年×0.6' });
  assert.match(s, /情景带 1851\.60 亿~1980\.80 亿（含点估计，不是置信区间）/);
  assert.match(s, /5年×0\.4 \+ 3年×0\.6/);
  assert.match(nextYearReading({ value: null, reason: '年报只有 2 份' }), /明年判不了/);
});

test('财务多旧：没交采集时间就说不知道', () => {
  assert.match(fetchedAgeSentence(2), /2\.0 小时前/);
  assert.match(fetchedAgeSentence(48), /2\.0 天前/);
  assert.match(fetchedAgeSentence(24 * 30), /30\.0 天没重采/);
  assert.match(fetchedAgeSentence(null), /不知道/);
});

test('缺档要出声：只有累计档时逐季那叠空是有原因的', () => {
  assert.equal(missingKindSentence(['cum', 'single']), null);
  assert.match(missingKindSentence(['cum']) ?? '', /没有单季档/);
  assert.match(missingKindSentence(['single']) ?? '', /没有累计档/);
  assert.match(missingKindSentence([]) ?? '', /一次财务都没采到/);
});

test('金额与增速的单位（亿元 / 带符号百分点）', () => {
  assert.equal(yi(263.32), '263.32 亿');
  assert.equal(yi(null), '—');
  assert.equal(pct(-4.02), '-4.0%');
});

// ============================================================ 三、量价形状与相似样本

test('相近分档位只是抓手，缺分说判不了', () => {
  assert.equal(scoreBand(91.7).label, '很像');
  assert.equal(scoreBand(78).label, '有点像');
  assert.equal(scoreBand(60).label, '一般');
  assert.equal(scoreBand(40).label, '不太像');
  assert.equal(scoreBand(null).label, '判不了');
});

test('缺的那一维说的是「按剩下的维算」，不是扣分', () => {
  const r = row({ missing_parts: ['mood', 'intensity'] });
  assert.match(missingPartsSentence(r), /大盘情绪、强度分布/);
  assert.match(missingPartsSentence(r), /不是扣分/);
  assert.equal(missingPartsSentence(row({ missing_parts: [] })), '');
});

test('像在哪：只念最高的两维，全缺时说明没有可比分量', () => {
  const r = row({ parts: { shape: 96.1, intensity: 21.5, rhythm: 85.4, mood: null } });
  assert.equal(topPartsSentence(r), '量能形状 96、放量节奏 85');
  assert.equal(topPartsSentence(row({ parts: {} })), '没有可比的分量');
  assert.ok(PART_LABEL.shape && PART_LABEL.price_volume);
});

test('启动后走势：读不到就说没有日线，不写成 0 点', () => {
  assert.equal(afterSentence(row({})), '启动日之后本机没有可读的日线（新股刚上市、或这场还没落库）—— 不是「没涨」');
  assert.match(afterSentence(row({ after_5: 14.98, after_10: 10.02, after_20: null, sessions_after: 12 })),
    /5 日 \+15\.0 点 .*本机只数得出 12 场/);
  assert.equal(points(null), '—');
});

test('母体归属并成一条（同票两类母体不占两个名额）', () => {
  assert.equal(cohortLabel(row({ cohorts: ['board4', 'board4_45'] })),
    '连续 4 板及以上 + 4 板及以上且前 4 日涨幅>45%');
  assert.equal(cohortLabel(row({ cohort: 'jump40' })), '前 4 日涨幅跳空');
  assert.equal(cohortLabel(row({})), '母体未标注');
});

test('那三句声明里必须有样本库大小与选择偏差', () => {
  const d = sim({ library_size: 1010, candidates_after_filter: 311, same_code_dropped: 2,
    neighbours: [row({ score: 85 })], score_note: '相近分 0~100…' });
  const lines = readingSentences(d);
  assert.match(lines.join('\n'), /样本库 1010 个/);
  assert.match(lines.join('\n'), /本次把 2 个/);
  assert.match(lines.at(-1) ?? '', /相近分 0~100/);
});

test('逐日倍数走势：null 留缺口，不画成 0', () => {
  const { path: p, gaps } = sparkPath([{ d: '1', v: 1.2 }, { d: '2', v: null }, { d: '3', v: 2.4 }]);
  assert.match(p, /^M/);
  assert.deepEqual(gaps, [1]);
  assert.equal(sparkPath([]).path, '');
  assert.equal(sparkPath([{ d: '1', v: null }]).path, '');
});

test('倍数读数：缺就是 —', () => {
  assert.equal(ratio(null), '—');
  assert.equal(ratio(1.3517), '1.35');
});

// ============================================================ 四、量价走势（日 / 周 / 月）

test('缺数是「—」，0 就写 0.00%（第一期没有基期与真的平盘是两句话）', () => {
  assert.equal(pvPctText(null), '—');
  assert.equal(pvPctText(undefined), '—');
  assert.equal(pvPctText(Number.NaN), '—');
  assert.equal(pvPctText(0), '0.00%');
  assert.equal(pvPctText(2.5), '+2.50%');
  assert.equal(pvPctText(-2.5), '-2.50%');
  assert.equal(pvPrice(null), '—');
  assert.equal(pvPrice(2.5), '2.50');
});

test('量的单位换算是按万股读的（1 亿股 = 10000 万股，别写成 1 亿股 = 1e8 万股）', () => {
  assert.equal(pvVolText(9876), '9876 万股');
  assert.equal(pvVolText(10000), '1.00 亿股');
  assert.equal(pvVolText(null), '—');
  assert.equal(pvAmtText(12.3456), '12.35 亿');
});

test('期间标签：日线给日、周线给「起于哪天那周」、月线给年月', () => {
  const b = { date: '2026-10-12' };
  assert.equal(pvBarLabel(b, 'day'), '10-12');
  assert.equal(pvBarLabel(b, 'week'), '10-12 那周');
  assert.equal(pvBarLabel(b, 'month'), '2026-10');
});

test('价格轴含均线，且不足两个值就不画（一根柱子撑不起一条轴）', () => {
  const rows = [{ high: 20, low: 10 }, { high: 25, low: 8, ma5: 30 }];
  const e = pvPriceExtent(rows, ['ma5']);
  assert.ok(e && e.hi >= 30 && e.lo <= 8, '均线不进气泡，MA 线会画到框外');
  assert.ok(e.hi - 30 > 0 && 8 - e.lo > 0, '上下没留边，最高那根贴着边框像被裁掉');
  assert.equal(pvPriceExtent([{ high: 20 }]), null, '凑不出上下界就不画轴（页面拿 null 走"这块不画"分支）');
  assert.equal(pvPriceExtent([]), null);
});

test('均线折线缺格是跳过，不是连到 0（连过去就是一条穿底的假线）', () => {
  const rows: PvBar[] = [{ ma5: 10 }, { ma5: null }, { ma5: 14 }];
  const pts = pvMaPoints(rows, 'ma5', i => i * 10, v => 100 - v);
  const segs = pts.split(' ');
  assert.equal(segs.length, 2, '缺的那格被画成点了？');
  assert.ok(!pts.includes('100.0'), 'y=100 是 0 价位：缺格被当成 0 连进线里');
});

test('悬停那格必须同时给复权收与不复权收，最后一根要点名「未走完」', () => {
  const t = pvBarTooltip({ date: '2026-10-12', open: 1, high: 2, low: 0.5, close: 1.5,
    close_raw: 1200, pct: 1.2, volume_wan_shares: 100, amount_yi: 1, bars: 4,
    partial: true } as PvBar, 'week', '10-12 那周');
  assert.ok(t.includes('1.50') && t.includes('1200.00'), '两个收价只给一个，历史那根会被当成真成交价');
  assert.ok(t.includes('不复权'), '没说不复权那一列是什么');
  assert.ok(t.includes('本期未走完'), 'partial 那根没有警告');
  assert.ok(t.includes('含 4 根日线'), '周线没交代吃掉几根日线');
  const calm = pvBarTooltip({ date: '2026-10-12', close: 1, open: 1 } as PvBar, 'week', '');
  assert.ok(!calm.includes('未走完'), '整周收完还挂警告');
});

test('块底那行读数：根数不足说「只列读数不画线」，够根数才说未走完，空块说没覆盖', () => {
  const rows: PvBar[] = [{ date: '2026-10', close: 9, pct: 1, volume_wan_shares: 5, partial: true }];
  const cap = pvSeriesCaption({ rows, drawn: 1, bars_total: 1, too_few: true }, 'month');
  assert.ok(cap.includes('只列读数不画线') && cap.includes('画 1 根（本机共 1 根）'), cap);
  const okCap = pvSeriesCaption({ rows, drawn: 1, bars_total: 48, too_few: false }, 'month');
  assert.ok(okCap.includes('最后一根未走完') && !okCap.includes('只列读数不画线'), okCap);
  assert.ok(pvSeriesCaption({ rows: [] }, 'day').includes('一根都没有'));
  assert.ok(pvSeriesCaption(null, 'day').includes('一根都没有'), '后端没交这一块也不能空白着不说');
});

test('涨跌方向缺开或缺收时是「不知道」，不是判成阴线', () => {
  assert.equal(pvIsUp({ open: 1, close: 2 }), true);
  assert.equal(pvIsUp({ open: 2, close: 1 }), false);
  assert.equal(pvIsUp({ open: null, close: 1 }), null);
  assert.equal(pvIsUp({} as PvBar), null);
});

// ============================================================ 五、页面接线（漏一处就是屏幕少一块）

test('这一轮新加的五块都挂在个股深度复盘上，且顺序是定下来的那个', () => {
  // 2026-10-28 用户点名「量价走势移动到这只票的量化档案的前面」⇒ pv 从第 5 位提到第 2 位。
  // 块序的另一份真源是 `duanxian/stock_metrics_ledger.py::BLOCKS`，后端那条测试钉它，两处必须一起改。
  const order = ['StockFacts', 'PriceVolumeTrend', 'StockDossier', 'TrendQuant',
    'FinancialPeriodBoard', 'VolumeSimilarityBoard', 'StockData', 'DeepDive', 'StockMetricsLedger'];
  const pos = order.map(n => page.indexOf(`<${n}`));
  pos.forEach((v, i) => assert.ok(v >= 0, `页面里没有 <${order[i]}，调用点没接上`));
  assert.deepEqual([...pos], [...pos].sort((a, b) => a - b),
    `块序变了：${order.join(' → ')}`);
});

test('五个新接口都登记进了系统自检清单（接口在跑却没人验＝假绿）', () => {
  for (const p of ['/stock/facts?code=', '/stock/fundamentals?code=', '/stock/volume-similarity?code=',
                 '/stock/metrics-ledger?code=', '/stock/price-volume-trend?code=']) {
    assert.ok(system.includes(p), `System.tsx 的 CAPABILITIES 里没有 ${p}`);
  }
  assert.ok(system.includes('"/stock/volume-similarity?code=600519.SH&k=10"')
    && /slow: true }/.test(system), '量能相似样本第一次约 6 秒，要标 slow');
});

test('每一块都真发了那一条接口（不是只写了组件）', () => {
  assert.match(factsComp, /useProbe<FactsPayload>\("\/stock\/facts"/);
  assert.match(finComp, /useProbe<FundamentalsPayload>\("\/stock\/fundamentals"/);
  assert.match(simComp, /useProbe<SimilarityPayload>\("\/stock\/volume-similarity"/);
  assert.match(pvComp, /useProbe<PvPayload>\("\/stock\/price-volume-trend"/);
});

test('量价走势三块是并排的（用户要的就是左日中周右月），且未走完那根在图上标出来', () => {
  // 钉 JSX 里那一串 className，不钉注释：上一版用 /lg:grid-cols-3/ 匹配到了文件头注释里的那个词，
  // 而真正的 className 已经改成 md: —— 断言照样绿，等于永真。
  assert.match(pvComp, /<div className="grid gap-2 md:grid-cols-3">/, '三块不是并排，是叠成一列');
  assert.ok(!/className="grid[^"]*lg:grid-cols-3/.test(pvComp),
    '并排档又退回 lg：这一屏中间列最宽 1024px，lg 在常见宽度下永远不触发，用户看到的是一列');
  assert.match(pvComp, /r\.partial/, '最后一根「本期未走完」没有在 K 线上标出来');
});

test('后端交出的措辞原样上屏，页面不重抄口径', () => {
  for (const comp of [factsComp, finComp, simComp, pvComp]) {
    assert.match(comp, /取数口径（后端原句，页面未改写）/, '少了那句后端原句');
    // 2026-10-21 起这两件可以走共用折叠块（缺数类仍渲在 details 之前），也可以仍是裸出口
    assert.match(comp, /<Caveats items=|<CaliberFold caveats=/,
      'caveats 没上屏 —— 裸出口或 CaliberFold 都算，唯独不许自己拼一份');
    assert.match(comp, /<Definitions map=|definitions=\{/,
      '字段义没上屏，列头悬停就没出处');
  }
});

test('相似样本榜的 sortKey 等于格子里真读出来的字段', () => {
  // 这些键都在 neighbour 行上；写错不报错、也不换序，只能靠钉 + 浏览器点一遍
  for (const k of ['"score"', '"bars_used"', '"mood"', '"gain_points"', '"security_code"',
                   'stats.\${k}', 'counts.\${k}']) {
    assert.ok(simComp.includes(k), `列头里没有排序键 ${k}`);
  }
  assert.match(simComp, /\["avg", "均值"\]/, '八列分布统计的键名要和后端 stats 对得上');
  assert.match(simComp, /\["up_days", "增"\]/, '天数那一排的键名要和后端 counts 对得上');
  assert.match(simComp, /launch_date/, '窗口末/启动日那一列要用 launch_date');
});

test('财务那一叠的排序默认按报告期倒序，且缺数排最后这条规矩没被绕开', () => {
  assert.match(finComp, /useTableSort\(\{ key: "period", dir: "desc" \}\)/);
  assert.match(finComp, /sort\.apply\(p\.quarters \?\? \[\]\)/,
    '排序必须在交出整叠之后、渲染之前（先截断再排＝只在片段里换序）');
});

test('相似样本榜本票那一行不参与排序（第一行钉住）', () => {
  assert.match(simComp, /const sorted = sort\.apply\(nb\)/);
  assert.match(simComp, /rows: Row\[\] = \[targetRow, \.\.\.sorted/, '本票必须在排完序的榜之前拼进去');
});

test('10-19 夜「描述收缩」自查（近 30 根量价相似样本）：怎么点那句收进抽屉，caveat 与截断留在外面', () => {
  // 用户 2026-10-19：「把选项留出，描述内容都收缩起来」；11-01 又点名「一模块一颗说明按钮」
  // ⇒ 这块原来三处出口（标题行 note + 表前折叠 + 表尾两个 ExplainBox）合成一颗抽屉。
  // 判的还是同一件事：描述收起来、告警与缺数摊在外面。
  const foldStart = simComp.indexOf('<CaliberFold caveats={p.caveats ?? []}');
  assert.ok(foldStart > 0, 'caveat 与字段义仍要交给全站那一处共用件（不许自己拼一份）');
  const boxStart = simComp.indexOf('<DrawerSection heading="这一张表怎么点', foldStart);
  const boxEnd = simComp.indexOf('</DrawerSection>', boxStart);
  assert.ok(boxStart > foldStart && boxEnd > boxStart, '相似样本榜要有收起来的"怎么点"一节，而且在这一颗折叠/抽屉体里');
  const box = simComp.slice(boxStart, boxEnd);
  assert.match(box, /\{STOCK_ROW_HINT\}/);
  assert.doesNotMatch(box, /popHint|text-warning|text-destructive|<Caveats/, '告警不许被收进抽屉');
  // 标题行那颗 `Panel note` 撤了、表尾那两个 ExplainBox 也没了 ⇒ 整块只剩一颗出口
  assert.doesNotMatch(simComp, /<ExplainBox\b/, '还留着第二颗说明出口（合并没做完）');
  assert.doesNotMatch(simCodeWithoutNotes(), /\bnote=\{/,
    '`Panel note` 那颗要撤——留着就是同一块两颗同名按钮（`notes=` 是抽屉里的读法段，不算出口）');
  // 找的是**渲染那一处**（`it.popHint` 这四个字在注释里也出现过，按第一次出现位置判会读到解释）
  const pop = simComp.indexOf('it.popHint && <NoticeBar');
  assert.ok(pop > 0, '弹窗被拦那句告警必须还在屏上，而且走公用组件那一种形状（不散着写）');
  // 那句"本票排第一行"要在 `notes={[…]}` 这一段里（这四个字在上面的解释里也出现过，按全文找会读到自己写的注释）
  const notesStart = simComp.indexOf('notes={[', foldStart);
  const notesEnd = simComp.indexOf('extra={', notesStart);
  assert.ok(notesStart > foldStart && notesEnd > notesStart, '共用件要还带着 notes 与 extra 两段');
  assert.match(simComp.slice(notesStart, notesEnd), /本票排第一行/,
    '那句"本票排第一行、点列头只排这十只"并进了抽屉的 notes，不再占标题行一颗按钮');
});

/** `notes={` 里也有 "note=" 这三个字符的前缀，判 `Panel note` 之前先把 `notes={` 摘掉。 */
function simCodeWithoutNotes() {
  return simComp.replace(/notes=\{\[[\s\S]*?\]\}/g, '');
}

// ——— 2026-10-28 用户点名：三块 K 线要做到跟股票软件那样（悬停出开高低收与涨跌幅、点一根在图下显数字）———

test('读数行：悬停 > 点选钉住 > 最新一根，且换票后旧下标一律作废', () => {
  assert.equal(pvPickBarIndex(10, 3, 7), 3, '鼠标在哪儿就看哪儿，钉住的那根要让位');
  assert.equal(pvPickBarIndex(10, null, 7), 7, '鼠标移开要回到钉住的那根（"点一下之后下面还看得见"就是这一条）');
  assert.equal(pvPickBarIndex(10, null, null), 9, '谁都没选时停在最新一根，与股票软件一致');
  assert.equal(pvPickBarIndex(10, 20, 5), 5, '悬停下标越界（换了一只票、根数变少）要作废，不许拿旧下标指新数组');
  assert.equal(pvPickBarIndex(10, null, 20), 9,
    '钉住的下标越界（换了一只票）要退回默认那一档＝最新一根；拿着 20 去指只有 10 根的新数组会读成 undefined，'
    + '而有柱子时把读数行留空比停在最新一根更让人找不到北');
  assert.equal(pvPickBarIndex(0, null, null), null, '一根都没有就没有可显示的读数（不硬编成第 0 根）');
  assert.equal(pvReadoutSource(3, 7), '悬停这一根');
  assert.equal(pvReadoutSource(null, 7), '已钉住这一根（再点同一根取消）');
  assert.match(pvReadoutSource(null, null), /最新一根/, '默认那一档要说出这是最新一根，不能让人以为是选中的');
});

test('读数行那一排：开高低收与涨跌一个都不能少，缺数给「—」不给 0，不复权收永远给', () => {
  const cells = pvReadoutCells({
    date: '2026-10-28', open: 10, high: 12, low: 9, close: 11.5, close_raw: 13, pct: 2.5,
    volume_wan_shares: 20000, amount_yi: 3.4, turnover_sum: 1.2, bars: 1,
  } as PvBar);
  const g = (k: string) => cells.find(c => c.k === k)?.v;
  assert.deepEqual([g('开'), g('高'), g('低'), g('收')], ['10.00', '12.00', '9.00', '11.50']);
  assert.equal(g('涨跌'), '+2.50%');
  assert.equal(g('不复权收'), '13.00', '图上那根是前复权形状，只给一个价就会被当成当天真成交的价');
  assert.equal(cells.find(c => c.k === '收')?.tone, 'up', '涨要用涨的那一档（红涨绿跌的映射住在组件，这里只交三态）');
  const hole = pvReadoutCells({ date: '2026-10-28' } as PvBar);
  assert.equal(hole.find(c => c.k === '收')?.v, '—', '缺数不是 0.00');
  assert.equal(hole.find(c => c.k === '涨跌')?.tone, 'none', '没有 pct 时不许蒙一个方向');
  assert.deepEqual(pvReadoutCells(null), [], '没有那一根就交空数组，组件那一行自己说"没有可显示的读数"');
  assert.equal(pvBarDate({ date: '2026-10-28' } as PvBar, 'day'), '2026-10-28', '日线要念到完整日期');
  assert.match(pvBarDate({ date: '2026-10-26' } as PvBar, 'week'), /那一周/);
  assert.equal(pvBarDate({ date: '2026-10-01' } as PvBar, 'month'), '2026年10月');
});

test('三块图真的接上了悬停与点选（不是只写了判读函数没挂调用点）', () => {
  assert.match(pvComp, /const \[hover, setHover\] = useState/, '没有悬停状态＝鼠标放上去什么也不会变');
  assert.match(pvComp, /const \[pinned, setPinned\] = useState/, '没有点选状态＝"点一根在下方显示"没实现');
  assert.match(pvComp, /onMouseEnter=\{\(\) => setHover\(i\)\}/, '透明命中条要挂上悬停');
  assert.match(pvComp, /onMouseLeave=\{\(\) => setHover\(null\)\}/, '移开要清悬停，否则读数卡在最后一根上');
  assert.match(pvComp, /setPinned\(p => \(p === i \? null : i\)\)/, '再点同一根要能取消钉住');
  assert.match(pvComp, /<ReadoutRow bar=\{active\} kind=\{kind\} source=\{readoutSource\(hover, pinned\)\} \/>/);
  assert.match(pvComp, /pickBarIndex\(n, hover, pinned\)/, '组件自己不许再写一套优先级');
  // 原生 `<title>` 从命中条挪到了读数行：整块信息仍在（悬停那一格的完整话没被丢掉）
  assert.match(pvComp, /title=\{barTooltip\(bar, kind, barLabel\(bar, kind\)\)\}/);
  assert.doesNotMatch(pvComp, /<title>\{barTooltip/, '命中条上再挂原生 tooltip＝同一件事两种控件，鼠标移开就没了的那个才是用户要的');
  assert.match(pvComp, /本期未走完/, 'partial 那一根在读数行上也要说，不能只在图上的虚线框里');
});

// ——— 2026-10-28 用户点名：个股信息那几段说明进统一的那个弹窗（`DocDrawer`）———

test('个股信息的五段说明都进了统一说明抽屉，缺数与告警仍然摊在块内（红线）', () => {
  assert.match(factsComp, /import \{ DocDrawer, DrawerSection \} from "@\/components\/ui\/DocDrawer"/,
    '要用全站那一只统一组件，不是自己搭一个 details');
  assert.match(factsComp, /<DocDrawer title="个股信息">/,
    '抽屉标题要与屏上那块标题同一个词，两处不同名会让人不确定他说的是哪一块');
  const drawer = factsComp.slice(factsComp.indexOf('<DocDrawer'), factsComp.indexOf('</DocDrawer>'));
  assert.equal((drawer.match(/<DrawerSection heading=/g) ?? []).length, 6,
    '六段说明（本场要留意的声明 / 口径 / 格子怎么读 / 为什么没有那四格 / 后端原句 / 字段字典）少一段就是没搬完');
  for (const h of ['本场要留意的声明（缺数／上限／告警，后端原句）', '这一块读什么、为什么可能晚一场', '屏幕上这些格子怎么读',
    '为什么这一块不显示每股收益、ROE、上市日、总股本', '取数口径（后端原句，页面未改写）',
    '这些字段是什么意思']) {
    assert.ok(drawer.includes(`heading="${h}"`), `抽屉里少了那一段：${h}`);
  }
  // 搬进弹窗不等于把后端的原句重抄一遍：口径与字段义仍来自 payload
  assert.match(drawer, /\{d\?\.caliber \? plain\(d\.caliber\)/, '口径那一格仍要念后端原句（只剥成对粗体）');
  assert.match(drawer, /<Definitions map=\{d\?\.definitions\} bare \/>/,
    '字段字典仍用那一份共用 markup（bare＝抽屉自己已有标题，不再套一层摘要行）');
  // 红线：`<Caveats>` 必须在抽屉**外面**
  assert.ok(factsComp.indexOf('</DocDrawer>') < factsComp.indexOf('<CaveatTally'),
    '屏上那一行计数必须在抽屉之外 —— 计数可以短，但不能不出声（11-03 改的是版面，不是诚实）');
  assert.match(factsComp, /local_only/, '那一条范围声明现在住在抽屉第一节（屏上不再摊）');
  assert.match(factsComp, /const leftCaveats = /, '屏上计数与抽屉原文用的是同一份名单');
  assert.doesNotMatch(factsComp.slice(factsComp.indexOf('</Panel>')), /Caveats/, 'Panel 之后不该再有第二次 Caveats');
  // 旧的三处裸说明要真的退场，不能新旧两份并存（同一件事两个地方念，迟早分叉）
  assert.doesNotMatch(factsComp, /<Caliber\b|<ExplainBox\b/, '口径问号角标与裸说明框该一起撤掉');
  assert.doesNotMatch(factsComp, /note=\{<p className="mt-2 border-t/, '表下那句后端原句不再单独占一行');
});

// ——— 2026-10-29 用户第 4 条（三型的规矩）＋ 2026-10-31 第 1、2 条（按钮摆标题行、一模块一颗）———

test('量价走势：说明按钮搬到了标题行最右（刷新在它后面），缺数／上限／告警仍留屏上，一个模块只一颗', () => {
  // 用户 2026-10-29 第 4 条「缺数声明、截断上限、告警应该留，有的时候才显示，没有的时候就不显示」
  // ——`foldAll` 那一闸不许重开；10-31 再把长在表尾的 `CaliberFold` 按钮搬进标题行（正文不许再长一颗），
  // 分流仍只住 `lib/caveatKind.ts` 一处：纯口径散文进抽屉，`partial_last`／`too_few` 留屏上、有才出现。
  // 只看代码，不看注释：这一块的解释里本来就要写「不再开 foldAll」这句纪律话，
  // 按整份文本比会把解释本身当成违规（同 `docDrawer.test.ts`/`lazySection.test.ts` 那条教训）。
  const pvCode = pvComp.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.doesNotMatch(pvCode, /\bfoldAll\b/, '这一块不许再开 foldAll：纯口径进抽屉，坏消息留屏上');
  assert.doesNotMatch(pvCode, /<CaliberFold\b|<Caliber\b/,
    '10-31：表尾那颗长在正文里的按钮与标题行那枚问号角标都退场，句子原样搬进抽屉（不是删话）');
  assert.match(pvCode, /splitCaveats\(d\?\.caveats \?\? \[\]\)/,
    '留在屏上与进抽屉的分流仍只走 `lib/caveatKind.ts` 那一处，页面不自拼一份');
  assert.match(pvCode, /<Caveats items=\{visible\} \/>/,
    '缺数／上限那一半仍在屏上（走共用出口 `Caveats`，不是自己写 <p>）');
  // 标题行那一叠：h2 之后、`<Panel` 之前，说明按钮必须排在「重新取数」前面
  const row = pvCode.slice(pvCode.indexOf('量价走势（日线 · 周线 · 月线）</h2>'), pvCode.indexOf('<Panel title="左：日线'));
  assert.ok(row.includes('<DocDrawer title="量价走势（日线 · 周线 · 月线）">'),
    '按钮没摆在这块标题这一行（或抽屉标题与屏上标题分了叉）');
  assert.ok(row.indexOf('<DocDrawer') < row.indexOf('onClick={refresh}'),
    '用户 10-31：按钮排在刷新这类元信息**前面**');
  assert.ok(row.includes('ml-auto'), '按钮要落在这一行的最右（`ml-auto` 那一叠）');
  // 一个模块一颗按钮：Panel 那颗 note（会自己长出第二颗同名「这块的说明」）仍然不许挂回来
  assert.doesNotMatch(pvCode, /<Panel title="左：日线[^>]*note=/, '同一张卡片里不许有两颗同名说明按钮');
  // 搬进抽屉的句子一句不许在屏上出现第二遍，也不许丢
  assert.match(pvComp, /上图下量；鼠标放到哪一根/, '那句读法没被删，只是搬进抽屉');
  assert.match(pvComp, /<Definitions map=\{d\.definitions\} bare \/>/, '字段义仍在同一口抽屉里（bare＝不再套一层摘要）');
  assert.match(pvComp, /取数口径（后端原句，页面未改写）/, '后端口径原句仍在这口抽屉里');
  // 三型"有才显示"的出口本身仍全站一份（这条是防有人把 NoticeBar 拆回散 <p>）
  const notice = read('../src/components/ui/NoticeBar.tsx');
  assert.match(notice, /if \(!when \|\| children == null/, '条件为假整条消失：没有就不显示');
});
