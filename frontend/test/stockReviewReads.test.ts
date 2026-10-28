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
  amtText as pvAmtText, barLabel as pvBarLabel, barTooltip as pvBarTooltip, isUp as pvIsUp,
  maPoints as pvMaPoints, pctText as pvPctText, price as pvPrice, priceExtent as pvPriceExtent,
  seriesCaption as pvSeriesCaption, volText as pvVolText, type PvBar,
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
  const order = ['StockFacts', 'StockDossier', 'TrendQuant', 'FinancialPeriodBoard',
    'PriceVolumeTrend', 'VolumeSimilarityBoard', 'StockData', 'DeepDive', 'StockMetricsLedger'];
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

test('10-19 夜「描述收缩」自查（近 30 根量价相似样本）：怎么点那句折进说明框，caveat 与截断留在外面', () => {
  // 用户 2026-10-19：「把选项留出，描述内容都收缩起来」。
  const boxStart = simComp.indexOf('<ExplainBox title="这一张表怎么点');
  const boxEnd = simComp.indexOf('</ExplainBox>', boxStart);
  assert.ok(boxStart > 0 && boxEnd > boxStart, '相似样本榜要有默认收起的"怎么点"说明框');
  const box = simComp.slice(boxStart, boxEnd);
  assert.match(box, /\{STOCK_ROW_HINT\}/);
  assert.doesNotMatch(box, /popHint|text-warning|text-destructive|<Caveats/);
  const before = simComp.slice(0, boxStart);
  assert.match(before, /<CaliberFold caveats=\{p\.caveats \?\? \[\]\}/,
    'caveat 交给共用折叠块，且排在"怎么点"框之前＝缺数类仍在默认可见位置');
  assert.ok(simComp.indexOf('it.popHint') < boxStart, '弹窗被拦的告警排在说明框之前、不在框内');
});
