import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  LIMIT_OPTIONS, PAGE_SIZES, boolText, boardsWindowCell, boardsWindowTitle, breakCountText,
  dblCell, dblTitle,
  industrySide, intText, limitCountCell, limitCountTitle, maStackCell, maStackTitle, num, pctText,
  prevSealedHits, quadCell, quadTitle, ratioText, reasonCell, reasonCoverage,
  consecCell, consecTitle, pos2yCell, pos2yTitle,
  repeatedWords, sealText, shapeButtonLabel, shapeQuery, spikeTitle, thresholdsText, toggleShapeSel,
  truncationSentence, unjudgedShape, wanText, yiText,
  type FbdRow, type FirstBoardDaily,
} from '../src/lib/firstBoardDaily.ts';
import { splitCaveats } from '../src/lib/caveatKind.ts';

/**
 * 首页「首板分析」（用户 2026-10-21 原话：「全市场换手率 TOP20 上面加一个首板分析，分析首板个股
 * 的情况，已经近期的走势，分析涨停原因，**特别是前一天的走势**」）。
 *
 * 每条断言都对应一句"屏幕会说谎"的情形：
 *  · 缺数被摆成 0（炸板次数 null→0 次没炸、前一日涨幅 null→平盘、名次 null→第 0 名）；
 *  · 涨停原因空着被统一说成"没有原因"（源站没收这只 ≠ 本场整池没取到，后者才是本机漏采）；
 *  · 阈值在页面里写死第二份（改判据那晚屏幕悄悄说谎）；
 *  · 全场聚合被读成"筛完剩下的数"；
 *  · caveat／截断声明被折进 `<details>`（藏起来等于让屏幕说假话）；
 *  · 列头 `sortKey` 与行上的字段名对不上（点了不换序、什么错都不报）；
 *  · 又长出一份自己的 `window.open`／名次格式化（全站一份实现的闸）。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = (rel: string) => fs.readFileSync(path.join(HERE, rel), 'utf8');
const lib = read('../src/lib/firstBoardDaily.ts');
const comp = read('../src/components/home/FirstBoardDailyPanel.tsx');
const home = read('../src/components/home/MarketPanorama.tsx');
const system = read('../src/pages/System.tsx');

/** 2026-10-21 实测那一场（34 只首板 / 当日封住 50 只）的形状，字段名逐字照接口。 */
const TH = {
  deep_pullback_pct: -5.0, surge_unsealed_pct: 3.5, flat_abs_pct: 2.0,
  shrink_vol_ratio: 0.77, mild_up_vol_ratio: 1.40,
};
const row = (o: Partial<FbdRow>): FbdRow => ({
  code: '603332.SH', name: '苏州龙杰', board: '主板', market: 'SH',
  launch_pct: 9.9653, launch_turnover: 1.7321, launch_amount_yi: 0.474,
  is_one_word: false, reason: '三季报增长+差别化涤纶长丝', reason_missing: null,
  first_seal_at: '09:30:00', last_seal_at: '09:30:00', break_count: null,
  order_amount_wan: 5798.1, source_high_days: '首板', boards: 1, board_days: 1,
  board_label: '1天1板', source_boards_agree: true,
  prev_date: '2026-10-20', prev_pct: 1.7637, prev_turnover: 1.5785, prev_amount_yi: 0.39,
  prev_vol_ratio: 1.0145, prev_touched: false, prev_sealed: false,
  prev_shape: 'mild_up', prev_shape_label: '温和放量', prev_shape_note: '前一日收涨、量比不到 1.40',
  chg5: 9.59, chg10: 5.66, chg15: 6.55, vol_grab_ratio: 1.0313,
  dbl_energy: 3.25, dbl_rank: 4105, energy_universe: 5519,
  missing: { break_count: 'source_field_null' }, ...o,
});
const panel = (o: Partial<FirstBoardDaily>): FirstBoardDaily => ({
  available: true, date: '2026-10-21', spec_version: 'fbd-1', limit: 80,
  first_count: 34, universe: 50, rows_returned: 10, rows: [row({})], ...o,
});

// ---------- 一、读数：缺数是 —，0 与 false 是实质读数 ----------

test('null／NaN／Infinity 一律 —，真 0 与 false 照常念（绝不 ?? 0）', () => {
  assert.equal(num(null), null);
  assert.equal(num(Number.NaN), null);
  assert.equal(num(Number.POSITIVE_INFINITY), null);
  assert.equal(intText(null), '—');
  assert.equal(intText(0), '0', '0 只一字板是实质读数');
  assert.equal(pctText(null), '—');
  assert.equal(pctText(0), '0.00%', '真平盘才念 0.00%');
  assert.equal(pctText(-4.576), '-4.58%');
  assert.equal(pctText(1.7637), '+1.76%');
  assert.equal(ratioText(null), '—');
  assert.equal(ratioText(1.0145), '1.0145', '去尾零走公用件那把尺');
  assert.equal(breakCountText(null), '—', '炸板次数空＝源站没给这个字段，不是 0 次没炸');
  assert.equal(breakCountText(0), '0', '真给了 0 就是 0 次没炸');
  assert.equal(boolText(null, '触板', '没触'), '—', '三态：null 不许落进"没触"');
  assert.equal(boolText(false, '触板', '没触'), '没触');
  assert.equal(boolText(true, '触板', '没触'), '触板');
  assert.equal(sealText(null), '—');
  assert.equal(sealText('09:30:07'), '09:30:07');
  assert.equal(yiText(0.474), '0.474 亿');
  assert.equal(yiText(null), '—');
  assert.equal(wanText(5798.1), '5798.1 万');
  // 读法层自己不许出现 `?? 0`（把没数说成零）——注释里写的是这条规矩本身，所以只查代码
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');
  assert.doesNotMatch(strip(lib), /\?\?\s*0\b/, '缺数兜成 0＝屏幕会说谎');
  assert.doesNotMatch(strip(comp), /\?\?\s*0\b/, '组件里不许把缺数兜成 0');
});

test('近 20 场几板：次数读不到给 —，窗口根数不足要把根数写进格子（绝不 ?? 0）', () => {
  assert.equal(boardsWindowCell(row({ boards_20d: 3, boards_20d_bars: 20 }), 20), '3 板');
  assert.equal(boardsWindowCell(row({ boards_20d: 0, boards_20d_bars: 20 }), 20), '0 板',
    '真 0 次是实质读数（这 20 场一次没封住），与"没读到"是两件事');
  assert.equal(boardsWindowCell(row({ boards_20d: 2, boards_20d_bars: 6 }), 20), '2 板（6 根）',
    '新股只有 6 根时必须念出来，否则「2 次」被读成「20 场里 2 次」');
  assert.equal(boardsWindowCell(row({ boards_20d: 4, boards_20d_bars: null }), 20), '4 板',
    '根数没读到不许兜成 0 根');
  assert.equal(boardsWindowCell(row({ boards_20d: 4, boards_20d_bars: 12 }), null), '4 板',
    '接口没交窗口长度时不判「不足」，也不自己顶一个 20');
  assert.equal(boardsWindowCell(row({ boards_20d: null, boards_20d_bars: null }), 20), '—');
  assert.match(boardsWindowTitle(row({ boards_20d: null, boards_20d_bars: null }), 20),
    /不是「这些场里一次都没封住涨停」/);
  const t = boardsWindowTitle(row({ boards_20d: 2, boards_20d_bars: 6 }), 20, '后端那句封住判据（含 board_span）');
  assert.match(t, /20 场/);
  assert.match(t, /6 根/);
  assert.match(t, /后端那句封住判据/, '封住判据与容差那句是后端交的，读法层只拼接');
});

test('近 10 / 20 场倍量：两个数各自独立，缺的那个不许念 0，分母必须点名 vr-1', () => {
  assert.equal(dblCell(row({ dbl10: { bars: 10, dbl: 2, near: 1 } }), 10), '2 倍量·1 准倍量');
  assert.equal(dblCell(row({ dbl10: { bars: 10, dbl: 0, near: 0 } }), 10), '0 倍量·0 准倍量',
    '真 0 次照常念，缺数才念 —');
  assert.equal(dblCell(row({ dbl10: { bars: 7, dbl: 3, near: 0 } }), 10), '3 倍量·0 准倍量（7 根）');
  assert.equal(dblCell(row({ dbl20: { bars: 20, dbl: 4, near: 2 } }), 20), '4 倍量·2 准倍量');
  assert.equal(dblCell(row({ dbl10: { bars: 10, dbl: null, near: null } }), 10), '—',
    '两个数都没读到＝没读到，不许摆一个「0 倍量」上去');
  assert.equal(dblCell(row({ dbl10: null }), 10), '—');
  assert.equal(dblCell(row({ dbl10: { bars: 10, dbl: 1, near: 1 } }), 30), '—',
    '接口没交这个窗口号就不猜它对应哪一列');
  const t = dblTitle(row({ dbl_unit: '对昨日量倍数', dbl10: { bars: 10, dbl: 1 } }), 10, '后端 dbl_windows 那句');
  assert.match(t, /对昨日量倍数/, '分母的名字读接口 dbl_unit，界面不自己造');
  assert.match(t, /vr-1/);
  assert.match(t, /前 5 场均量/, '与同屏另一把尺的分别必须写在悬停里');
  assert.match(t, /后端 dbl_windows 那句/);
  assert.match(dblTitle(row({ dbl10: null }), 10), /不是「一次倍量都没有」/);
});

test('近 5 场四维象限：一段序列，缺场次就少几段；旧基准那一段要点名', () => {
  assert.equal(quadCell(row({ quad: [] })), '—');
  assert.equal(quadCell(row({ quad: [{ day: '2026-10-16', quadrant: 'Q1', rank: 12 },
    { day: '2026-10-20', quadrant: 'Q2', rank: null }] })), '10-16 Q1 12 → 10-20 Q2',
    '名次没读到就留空，不写第 0 名');
  assert.match(quadCell(row({ quad: [{ day: '2026-10-16', quadrant: null, rank: 5, stale: true }] })),
    /— 5〈旧〉/);
  assert.match(quadTitle(row({ quad: [] })), /不是「排名第 0」/);
  const t = quadTitle(row({ quad: [{ day: '2026-10-16', quadrant: 'Q1', rank: null, value: 0.4, stale: true }] }),
    '后端 quad 那句');
  assert.match(t, /名次 —/);
  assert.match(t, /旧基准，名次不参与对比/);
  assert.match(t, /后端 quad 那句/);
});

test('盘中信号：「这一族没读到」「没进榜」「有读数」是三种说法（探不到不许说成没冲高）', () => {
  assert.match(spikeTitle(row({ spike_checked: null })), /这次没读到/);
  const absent = spikeTitle(row({ spike_checked: true }));
  assert.match(absent, /没进异动榜/);
  assert.match(absent, /不等于「盘中没冲高」/);
  const on = spikeTitle(row({ spike_checked: true, spike_tier: '急拉', spike_above5_min: 12,
    spike_tags: ['急拉', '封板'] }));
  assert.match(on, /标签：急拉、封板/);
  assert.match(on, /\+5% 之上持续 12 分钟/);
  assert.match(spikeTitle(row({ spike_checked: true, spike_tier: '急拉' }), '后端 spike 那句'), /后端 spike 那句/);
});

// ---------- 二、涨停原因：空着的两种说法必须不一样 ----------

test('原因串原样上屏（数据值不包 plain），空串按 reason_missing 分开念', () => {
  assert.equal(reasonCell(row({ reason: 'AI**应用+数据要素' })).text, 'AI**应用+数据要素',
    '数据值不许被 plain() 剥星号（星号可能是词本身）');
  assert.equal(reasonCell(row({ reason: 'AI应用' })).isReason, true);
  const absent = reasonCell(row({ reason: null, reason_missing: 'source_row_absent' }));
  const noRec = reasonCell(row({ reason: null, reason_missing: 'source_no_records' }));
  assert.match(absent.text, /源站没收这只/);
  assert.match(absent.text, /不是本机漏采/);
  assert.match(noRec.text, /本场整池没取到/);
  assert.match(noRec.text, /本机漏采/);
  assert.notEqual(absent.text, noRec.text, '两种"空着"说成同一句就是把漏采说成不收');
  assert.equal(noRec.isReason, false);
  const unknown = reasonCell(row({ reason: '', reason_missing: null }));
  assert.match(unknown.text, /没交这一行为什么空/);
  assert.doesNotMatch(unknown.text, /本机漏采|源站没收这只/, '接口没说原因时页面不替它挑一种');
});

test('原因覆盖＝stock_count − no_reason；no_reason 单列，不并进「没有原因」', () => {
  const c = reasonCoverage({ scope: '本场全部 34 只首板', stock_count: 34, no_reason: 0, tag_total: 99 });
  assert.equal(c.covered, '34');
  assert.equal(c.noReason, '0');
  assert.match(c.note, /34 只首板里 0 只没有原因串/);
  const miss = reasonCoverage({ stock_count: null, no_reason: 3 });
  assert.equal(miss.covered, '—', '分母没读到就说没读到，不拿剩下的行数顶上');
  assert.match(miss.note, /没读到/);
  assert.doesNotMatch(miss.covered, /^0$/, '没读到 ≠ 0 只有原因');
});

test('题材词榜只数出现两次以上的；行业那侧交 null 就念后端那句', () => {
  const list = [{ name: 'AI应用', count: 4 }, { name: '数据库', count: 2 }, { name: '孤词', count: 1 }];
  assert.deepEqual(repeatedWords(list).map(w => w.name), ['AI应用', '数据库']);
  assert.deepEqual(repeatedWords(null), []);
  const note = '本屏不交行业汇总：库里 reference_data.security 没有行业列';
  const off = industrySide({ industry_summary: null, summary_meta: { industry_note: note } });
  assert.equal(off.words, null);
  assert.equal(off.note, note, '那句原话端出去，页面不改写');
  const on = industrySide({ industry_summary: [{ name: '软件', count: 3 }] });
  assert.deepEqual(on.words?.map(w => w.name), ['软件']);
  assert.equal(on.note, null);
});

// ---------- 三、阈值与母体自检 ----------

test('阈值文字全读 thresholds：换个数就换一句话，页面一个都不写死', () => {
  const t = thresholdsText(TH);
  assert.match(t, /大跌 ≤ -5%/);
  assert.match(t, /冲高未封 ≥ 3.5%/);
  assert.match(t, /横盘 \|涨幅\| ≤ 2%/);
  assert.match(t, /缩量 量比 ≤ 0.77/);
  assert.match(t, /温和放量上界 量比 1.4/);
  assert.match(thresholdsText({ ...TH, shrink_vol_ratio: 0.55 }), /≤ 0.55/);
  assert.equal(thresholdsText(undefined), '');
  assert.equal(thresholdsText(null), '');
  assert.match(thresholdsText({ flat_abs_pct: 2.0 }), /没交/, '缺的那个键点名说没交，不补默认值');
  // 后端那五个数在两份源码里只许作为**读出来的键**出现，不许当字面量写死
  // （`(?<![-\d.])` 是为了把 Tailwind 那个 `h-3.5` 尺寸类排除掉——它不是阈值）
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');
  const HARD = /(?<![-\d.])3\.5(?![\d])|(?<![-\d.])0\.77(?![\d])|(?<![-\d.])1\.40(?![\d])|(?<![-\d.])1\.4(?![\d%])|deep_pullback_pct\s*[:=]\s*-?5/;
  for (const [name, code] of [['lib', lib], ['组件', comp]] as const) {
    assert.equal(HARD.test(strip(code)), false,
      `${name} 里写死了后端那把阈值的数字：那就是第二份真相源`);
  }
});

test('形态筛：空选＝不带参数；判不了那一档筛不了（它不是七档之一）', () => {
  assert.equal(shapeQuery([]), undefined);
  assert.equal(shapeQuery(['quiet_shrink', 'mild_up']), 'quiet_shrink,mild_up');
  assert.deepEqual(toggleShapeSel(['mild_up'], 'mild_up'), []);
  assert.deepEqual(toggleShapeSel(['mild_up'], 'other'), ['mild_up', 'other']);
  assert.equal(shapeButtonLabel({ key: 'mild_up', label: '温和放量', n: 7 }), '温和放量 7');
  assert.equal(shapeButtonLabel({ key: 'x', label: '某档', n: null }), '某档 —', '每档只数读不到不许念 0');
  assert.equal(unjudgedShape([{ key: 'mild_up', label: '温和放量', n: 7 },
    { key: null, label: '判不了', n: 3 }])?.n, 3);
  assert.equal(unjudgedShape([{ key: 'mild_up', label: '温和放量', n: 7 }]), null);
});

test('母体自检：前一日收盘封住应恒假，真冒出来就数给出声（null 不算）', () => {
  assert.equal(prevSealedHits([row({}), row({ prev_sealed: true }), row({ prev_sealed: null })]), 1);
  assert.equal(prevSealedHits([row({ prev_sealed: false })]), 0);
});

test('截断声明分得开「被上限切」与「被形态筛挡」，总数读不到时不拿行数当总数', () => {
  const cut = truncationSentence(10, panel({ first_count: 34, limit: 10 }));
  assert.match(cut, /本场首板共 34 只/);
  assert.match(cut, /取数上限 10 只切掉的/);
  assert.match(cut, /不是「今天没有」/);
  const filtered = truncationSentence(3, panel({ first_count: 34, limit: 80, shape_filter: ['mild_up'] }));
  assert.match(filtered, /形态筛（mild_up）/);
  assert.doesNotMatch(filtered, /取数上限 80 只切掉的/, '筛过之后少了不能说成被上限切的');
  const unread = truncationSentence(10, panel({ first_count: null }));
  assert.match(unread, /没读到/);
  assert.doesNotMatch(unread, /共 10 只|共 0 只/);
  const full = truncationSentence(34, panel({ first_count: 34, limit: 80 }));
  assert.match(full, /已全部列出/);
});

// ---------- 四、接线 ----------

test('接口路径不带 /api 前缀，参数只有 date／limit／shapes 那几档', () => {
  assert.match(comp, /useProbe<FirstBoardDaily>\("\/quant\/first-board-daily"/,
    'useProbe 自己补 /api，页面里绝不写两次前缀');
  assert.doesNotMatch(comp, /["'`]\/api\/|\/api\/quant/, '写了 /api 会拼成 /api/api/…');
  assert.match(comp, /date: date \|\| undefined, limit, shapes: shapeQuery\(shapes\)/);
  assert.deepEqual(LIMIT_OPTIONS, [80, 150, 240],
    '后端 Query(80, ge=10, le=240)：界面只交这三档，夹更宽每次都是 422');
  assert.deepEqual(PAGE_SIZES, [25, 50]);
  assert.doesNotMatch(comp, /export const LIMIT_OPTIONS|\[\s*80\s*,\s*150\s*,\s*240\s*\]/,
    '档位清单只在 lib 有一份');
  assert.doesNotMatch(comp, /limit:\s*(?!80\b)(\d{2,4})/, '界面不许发一个后端不认的 limit');
});

test('真的挂在首页「4b 短线情绪」收口之后、「全市场换手率 TOP20」之前', () => {
  assert.match(home, /import \{ FirstBoardDailyPanel \} from "@\/components\/home\/FirstBoardDailyPanel"/);
  const at = home.indexOf('<FirstBoardDailyPanel />');
  assert.ok(at > 0, '首页没有渲染这一块的入口');
  assert.ok(at > home.indexOf('{/* 4b. 短线情绪'), '首板分析要在连板股那张表之后');
  assert.ok(at < home.indexOf('全市场换手率 TOP20</h3>'), '用户点名摆在换手率 TOP20 上面');
  assert.match(system, /"\/quant\/first-board-daily/, '自检清单 CAPABILITIES 里要有这一条，否则接口在跑却没人验');
});

test('共用件全部复用，没有第二份实现', () => {
  for (const must of [
    /import \{ rankText, rankTitle, trimNum \} from "@\/lib\/ranking"/,
    /import \{ StockLink \} from "@\/components\/ui\/StockLink"/,
    /import \{ STOCK_ROW_HINT, useStockRowInteraction \} from "@\/lib\/useStockRowInteraction"/,
    /import \{ CaliberFold, Definitions, ExplainBox[^}]*\} from "@\/components\/ui\/DataPanel"/,
    /import \{ useTableSort \} from "@\/lib\/useSort"/,
    /import \{ Disclaimer \} from "@\/components\/ui\/Disclaimer"/,
  ]) assert.match(comp, must, `没接上共用件：${must}`);
  assert.match(comp, /rankText\(r\.dbl_rank\)/, '名次那一格走公用件（缺分念 —，不是第 0 名）');
  assert.match(comp, /rankTitle\(th\("energy"\), \{ scored: num\(r\.energy_universe\) \}/,
    '名次悬停的规则＋分母都读接口（分母＝有分的那批，不写成"全场"）');
  assert.match(comp, /useStockRowInteraction\("首板分析"\)/);
  assert.match(comp, /<StockLink code=\{r\.code\} name=\{r\.name\}/);
  assert.match(comp, /<Definitions map=\{d\.definitions\} \/>/);
  assert.match(comp, /<CaliberFold[^>]*caveats=\{d\.caveats\}/s,
    '整份 caveats 交给共用件（属性顺序不钉，`foldAll` 是用户点名的那一屏才开的开关）');
  assert.match(comp, /\{STOCK_ROW_HINT\}/);
  assert.match(comp, /<Disclaimer compact \/>/);
});

test('caveats 渲染在折叠框外面：这块自己不写 details，只把整份交给 CaliberFold', () => {
  assert.doesNotMatch(comp, /<details|<summary/, '组件自己写 details＝多一处折叠，告警会被顺手折掉');
  assert.match(comp, /caveats=\{d\.caveats\}/, '整份 caveats 交给共用件分流（不许只传折得动的那一半）');
  // 这一屏那八个 code 都不在折叠白名单里 ⇒ 全部留在屏上（含永远在外的四条与按需追加的四条）。
  // `prev_day_daily_only` 已改名 `recent_sources`（新那四列各有各的源表）；`boards_tolerance`
  // 随「几天几板」那一列一起撤掉了——屏上没有指对象的数字不算声明。
  const codes = ['one_ruler', 'reason_attribution', 'recent_sources', 'break_count_null', 'not_signal',
    'bad_shape_filter', 'shape_filter', 'agg_unread'];
  const { visible, foldable } = splitCaveats(codes.map(c => ({ code: c, text: `${c} 的句子` })));
  assert.equal(foldable.length, 0, `${foldable.map(c => c.code)} 被折进折叠框了`);
  assert.equal(visible.length, codes.length);
  // 描述类（口径/字段义/覆盖/出处）确实收起来了：rule 与 caliber 走 CaliberFold 的 notes、definitions 走自身折叠
  assert.match(comp, /notes=\{\[d\.rule, d\.caliber,/);
  assert.match(comp, /text=\{d\?\.rule/);
});

test('交互控件没有被 disabled／readOnly 锁住，开复盘只走共用 hook', () => {
  assert.doesNotMatch(comp, /\bdisabled\b|\breadOnly\b/i, '交互控件不许锁死');
  assert.doesNotMatch(comp, /window\.open\(/, '要开深度复盘走 useStockRowInteraction（地址只住 lib/openStockReview.ts）');
  assert.match(comp, /\{x\.popHint && <p role="status"/, '弹窗被拦的告警渲染在折叠框外面');
});

test('列头 sortKey 与格子读的字段同名同序（写错不报错，只是那一列永不亮）', () => {
  const cols = [...comp.slice(comp.indexOf('const COLS'), comp.indexOf('const GROUPS'))
    .matchAll(/\["([a-z0-9_]+)", "([^"]*)"/g)].map(m => m[1]);
  assert.ok(cols.length >= 20, `列清单没解析出来：${cols.length}`);
  const body = comp.slice(comp.indexOf('<tbody>'), comp.indexOf('</tbody>'));
  const cells = [...body.matchAll(/x\.cellProps\(i, "([a-z0-9_]+)"/g)].map(m => m[1]);
  assert.deepEqual(cells, cols, `格子与列头不同序＝点那一列不亮：${cells}`);
  // 每个键都得是行上真有的字段（照 lib 的 FbdRow 逐条核）
  const fields = new Set([...lib.slice(lib.indexOf('export interface FbdRow'), lib.indexOf('/** 接口顶层'))
    .matchAll(/^\s{2}([a-z0-9_]+)\??:/gm)].map(m => m[1]));
  for (const k of cols) assert.ok(fields.has(k), `sortKey="${k}" 在 FbdRow 上没有这个字段`);
  // 默认序点亮在首封上（后端就是按它排的，列头要有箭头）
  assert.match(comp, /useTableSort\(\{ key: "first_seal_at", dir: "asc" \}\)/);
});

test('10-27 用户点名的那十列真的撤下了：整组 prev_*、几天几板、当日涨幅都不在列清单里', () => {
  const colKeys = [...comp.slice(comp.indexOf('const COLS'), comp.indexOf('const GROUPS'))
    .matchAll(/\["([a-z0-9_]+)", "([^"]*)", "(day|prev|trend|history)"/g)].map(m => m[1]);
  const gone = ['prev_date', 'prev_pct', 'prev_vol_ratio', 'prev_touched', 'prev_shape_label',
    'prev_turnover', 'prev_amount_yi', 'prev_sealed', 'board_label', 'launch_pct'];
  for (const k of gone) assert.equal(colKeys.includes(k), false, `${k} 点名撤掉了，还留在列清单里`);
  assert.equal(colKeys.filter(k => k.startsWith('prev_')).length, 0, '整组一列都不许留（留着就是"主体"又回来了）');
  assert.doesNotMatch(comp, /const PREV_CELL/, '那一组的底色只服务撤掉的列，留着就是死码');
  assert.doesNotMatch(comp, /前一交易日那一根 · 这一屏的主体/);
  // 撤下不等于这块没接：新加的那几列必须在列清单里
  for (const k of ['boards_20d', 'quad', 'dbl10', 'dbl20', 'spike_tier_rank', 'spike_first5_at', 'spike_speed15_pct'])
    assert.ok(colKeys.includes(k), `${k} 没进列清单＝这一格根本没接`);
  // 母体自检（「前一日收盘封住」为真）不随列消失：它是写侧口径的哨兵，不是给用户看的那一列
  assert.match(comp, /sealedHits > 0/);
  // 形态档筛还在（筛的是库里的 prev_shape，与屏上有没有那一列无关）
  assert.match(comp, /shapeButtonLabel\(t\)/);
});

test('10-27 点名的四列历史位置：五格都在、两个口径并排、缺数不念 0', () => {
  const hist = [...comp.matchAll(/\["([a-z0-9_]+)", "[^"]*", "history"/g)].map(m => m[1]);
  // 涨/跌停拆成两列各排各的：用户要"封与触两个都给"，给的是格子里两个数；
  // 合成一列就没有可比的单一大小（sortKey 必须是格子里读到的那一个数）。
  assert.deepEqual(hist, ['lu_sealed_3m', 'ld_sealed_3m', 'max_consec_6m', 'pos_2y', 'ma_stack_rank']);
  assert.match(comp, /limitCountCell\(r, "lu"\)/);
  assert.match(comp, /limitCountCell\(r, "ld"\)/);
  // 均线那一格念中文档名、排序走后端交的 `ma_stack_rank`（与急拉档同一族写法）
  assert.match(comp, /maStackCell\(r\)/);
  assert.equal(hist.includes('ma_stack'), false, '档名不能当排序键：点了不亮');

  // 0 是真读数，null 才是 —
  assert.equal(limitCountCell({ lu_sealed_3m: 0, lu_touched_3m: 3 }, 'lu'), '封0／触3');
  assert.equal(limitCountCell({ ld_sealed_3m: 2 }, 'ld'), '封2／触—');
  assert.equal(limitCountCell({}, 'ld'), '—');
  assert.equal(consecCell({ max_consec_6m: 0 }), '0 板');
  assert.equal(consecCell({}), '—');
  assert.equal(pos2yCell({ pos_2y: 33.94, bars_2y: 489 }, { pos_min_bars: 250 }), '33.9%（489 根）');
  // 根数不够时那一格写"窗口只有 N 根"，不报一个偏小的位置
  assert.equal(pos2yCell({ bars_2y: 120 }, { pos_min_bars: 250 }), '窗口只有 120 根');
  assert.equal(pos2yCell({}, {}), '—');
  assert.equal(maStackCell({ ma_stack_label: '四线多头' }), '四线多头');
  assert.equal(maStackCell({ ma_stack: 'tangled' }), 'tangled');   // 后端没交档名时退回短码，不空着
  assert.equal(maStackCell({}), '—');

  // 悬停必须说清"这一列按哪一个数排"与"这不是涨幅/分位"，否则两把尺并排就是让人猜
  assert.match(limitCountTitle({ lu_sealed_3m: 1, lu_touched_3m: 4 }, 'lu', { sessions_3m: 63 }),
    /按涨停封住那个数排序/);
  assert.match(limitCountTitle({ ld_sealed_3m: 1, ld_touched_3m: 4 }, 'ld', { sessions_3m: 63 }),
    /近 63 个市场场次/);
  assert.match(limitCountTitle({}, 'lu', undefined), /窗口场数后端没交/);
  assert.match(pos2yTitle({ close_adj: 11, hi_2y: 15, lo_2y: 5, bars_2y: 300 },
    { sessions_2y: 489, pos_min_bars: 250 }), /不是涨幅、也不是分位数/);
  assert.match(consecTitle({ max_consec_6m: 3 }, { sessions_6m: 126 }), /lup-1/);
  assert.match(maStackTitle({ ma5: 11, ma10: 10, ma20: 9, ma60: 8 }, { ma_spans: [5, 10, 20, 60] }),
    /MA5 11｜MA10 10｜MA20 9｜MA60 8/);
  assert.match(maStackTitle({}, { ma_spans: [5, 10, 20, 60] }), /四条均线没凑齐/);
});

test('用到的后端字段都在屏上（少一个就说明那一格根本没接）', () => {
  // 组件直接读的（表格格子与卡片区）
  const onScreen = ['first_count', 'universe', 'one_word', 'prev_touched', 'prev_pct_med', 'shape_tally',
    'reason_tally', 'dropped_single_reason', 'tag_total', 'thresholds', 'coverage', 'calculated_at',
    'spec_version', 'shape_filter', 'limit', 'missing', 'launch_turnover', 'first_seal_at',
    'last_seal_at', 'break_count', 'order_amount_wan', 'reason', 'reason_missing', 'is_one_word',
    'chg5', 'chg10', 'chg15', 'vol_grab_ratio', 'dbl_energy',
    'dbl_rank', 'energy_universe', 'caliber', 'rule', 'definitions', 'caveats', 'date', 'available', 'rows',
    // 10-27 新加的这六列 + 它们读的元数据
    'boards_20d', 'quad', 'dbl10', 'dbl20', 'spike_tier', 'spike_first5_at', 'spike_speed15_pct', 'recent',
    'boards_window'];
  for (const k of onScreen) assert.ok(comp.includes(k), `接口交出的 ${k} 没有上屏`);
  // 撤下要有撤下的断言（不是把清单里那一行删掉就完事）：行上的字段一律不再被组件读
  const offRow = ['prev_date', 'prev_pct', 'prev_vol_ratio', 'prev_touched', 'prev_turnover',
    'prev_amount_yi', 'prev_sealed', 'prev_shape_label', 'prev_shape_note', 'board_label',
    'source_boards_agree', 'launch_pct', 'boards', 'calm_days'];
  for (const k of offRow) assert.equal(new RegExp(`r\\.${k}\\b`).test(comp), false,
    `r.${k} 已经撤下，组件里还留着引用（那一格还在读它，或读的是不存在的列）`);
  for (const k of ['boards_disagree', 'boards_null'])
    assert.equal(new RegExp(`d\\.${k}\\b`).test(comp), false, `${k} 是撤掉那列的对账数，屏上没有指对象了`);
  // 聚合与词榜那几句由读法层收（分母、"本屏不交行业汇总"、窗口根数、倍量分母的名字），组件只端结果
  for (const k of ['stock_count', 'no_reason', 'industry_summary', 'industry_note', 'scope',
    'boards_20d_bars', 'dbl_unit', 'spike_checked', 'board_span'])
    assert.ok(lib.includes(k), `读法层没读 ${k}——分母、"本屏不交行业汇总"与新列的窗口/分母就没人管了`);
});

test('available:false 时原样念 reason（那句里就是补跑命令），不摆空表', () => {
  assert.match(comp, /st\.kind === "ok" && !st\.data\.available/);
  assert.match(comp, /plain\(st\.data\.reason \?\?/);
  // 有数那一支整块挂在 available 上（rows 在这时确实不存在）
  assert.ok(comp.indexOf('st.data.available') < comp.indexOf('const rows = '), '窄化要在取 rows 之前');
  assert.match(comp, /st\.data\.available \? st\.data : null/);
});

// ---------- 五、文案卫生 ----------

test('屏幕上念出来的句子没有成对星号、中文串里不嵌 ASCII 双引号', () => {
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');
  for (const [name, src0] of [['lib', lib], ['组件', comp]] as const) {
    const code = strip(src0);
    const hits = code.match(/[^\s]\*\*|\*\*[^\s]/g) ?? [];
    assert.deepEqual(hits, [], `${name}：这些地方有裸的 **，会原样显示：${hits.join(' ')}`);
    assert.doesNotMatch(code, /[\u4e00-\u9fff]"[\u4e00-\u9fff]/,
      `${name}：中文句子中间出现了 ASCII 双引号（引用要用「」）`);
    for (const b of code.match(/>([^<>{}]*[\u4e00-\u9fff][^<>{}]*)</g) ?? []) {
      assert.doesNotMatch(b, /"/, `${name}：JSX 文本节点里出现了 ASCII 双引号：${b}`);
    }
  }
  // 自写的句子过 plain()，数据值（原因串、形态长句）不过
  const plainCount = (comp.match(/plain\(/g) ?? []).length;
  assert.ok(plainCount >= 15, `自写句子没过 plain()（现在只有 ${plainCount} 处）`);
  assert.doesNotMatch(comp, /plain\(r\.reason|plain\(\s*\{?\s*r\.prev_shape_note/, '数据值不许被 plain() 包');
});

// ---------- 六、全站闸：不许把「除 lib 外不许 window.open」那条改红 ----------

const srcFiles = (dir: string, out: string[] = []): string[] => {
  for (const e of fs.readdirSync(path.join(HERE, dir), { withFileTypes: true })) {
    const rel = `${dir}${path.sep}${e.name}`;
    if (e.isDirectory()) srcFiles(rel, out);
    else if (/\.(ts|tsx)$/.test(e.name)) out.push(rel);
  }
  return out;
};

test('开复盘新窗口的动作仍只住 lib/openStockReview.ts 那一份', () => {
  const openers = srcFiles('../src')
    .filter(f => /window\.open\(/.test(read(f)))
    .map(f => f.split(path.sep).join('/'));
  assert.deepEqual(openers, ['../src/lib/openStockReview.ts'],
    `又长出第二份开窗动作（要开深度复盘走 useStockRowInteraction）：${openers.join(', ')}`);
});
