import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import {
  METRIC_BUTTONS, LIMIT_OPTIONS, axisRegression, medianGainPct, nameIsCode, pathMismatch,
  stepsSentence, truncationSentence, windowSentence, pathNumbers,
} from '../src/lib/steadyRank.ts';

/**
 * 名次稳步前进榜（近 6 场名次一直在往前走 · xy / 总分两档可切）。
 *
 * 用户 2026-10-18：「四维象限这边再做一个功能、就是用于展示这个 xy 名次近 6 个交易日稳步增长的个股，
 * **是名次**！可以手动切换成总分稳步增长的，可以选择。」
 *
 * 判据住在 `duanxian/quant.py::rank_momentum`（由 `tests/test_rank_momentum.py` 对真库钉），
 * 这里钉的是**屏幕会不会说谎**那一半：
 *  ① 表体只有一份实现，四维象限那一屏是调用方；
 *  ② 列头 `sortKey` 与格子读的字段逐列同名同序；**文本路径列不许挂排序**（按 '1→9' 排出来的先后没有读数意义）；
 *  ③ 门槛数字（几步算稳步、幅度下限）一律从接口 `thresholds` 读，页面一份都不写死；
 *  ④ 「榜被上限截断」与「中位幅度是本页按已交出的行数出来的」两句必须现于屏；
 *  ⑤ 0 是有效值：后退 0 步、持平 0 步都要印出 0，不能吞成 `—`；
 *  ⑥ 端点参数名与 FastAPI 的签名一致（键名打错就是筛了个寂寞，FastAPI 会静默丢掉不认识的键）。
 */

const comp = fs.readFileSync(new URL('../src/components/quant/SteadyRankBoard.tsx', import.meta.url), 'utf8');
const page = fs.readFileSync(new URL('../src/pages/Quadrant.tsx', import.meta.url), 'utf8');
const system = fs.readFileSync(new URL('../src/pages/System.tsx', import.meta.url), 'utf8');
const appPy = fs.readFileSync(new URL('../../vr/app.py', import.meta.url), 'utf8');
/** 真库那一份 payload 的样本（definitions / thresholds / 行字段名都是从接口现取的，不是手抄） */
const fixture = JSON.parse(fs.readFileSync(new URL('./steadyRank.defs.json', import.meta.url), 'utf8'));

// ---------------------------------------------------------------- 纯判读层

test('路径拆解：拆不出一个数字就整条交 null，不拿半截当真数', () => {
  assert.deepEqual(pathNumbers('3454→1049→388'), [3454, 1049, 388]);
  assert.equal(pathNumbers('486→—→15'), null, '有一个格子不是数字 ⇒ 整条不认，别说"前 2 段可信"');
  assert.equal(pathNumbers(''), null);
  assert.equal(pathNumbers(null), null);
  assert.equal(pathNumbers(undefined), null);
});

test('路径段数与场数对不上要点名是哪一条', () => {
  const row = { series_path: '1→2→3', x_path: '1→2→3→4→5→6', y_path: '6→5→4→3→2→1' };
  assert.deepEqual(pathMismatch(row, 6), ['名次路径 3 段']);
  assert.deepEqual(pathMismatch(row, null), [], '场数没交时说不出对不对得上，不硬判');
  assert.deepEqual(pathMismatch({ series_path: null, x_path: null, y_path: null }, 6), [],
    '整列没交不由这里报（那一格本身就是 —）');
});

test('步数句子：缺一个数就说没交，不按 0 补', () => {
  assert.equal(stepsSentence({ better_steps: 4, worse_steps: 1, tied_steps: 0 }, 5),
    '共 5 步里前进 4 步、后退 1 步、持平 0 步');
  assert.match(stepsSentence({ better_steps: 4 }, 5), /没交/);
  assert.equal(stepsSentence({ better_steps: 5, worse_steps: 0, tied_steps: 0 }, 5),
    '共 5 步里前进 5 步、后退 0 步、持平 0 步', '0 步后退是"真的一步没退"，与"不知道"是两句话');
  assert.match(stepsSentence({ better_steps: 3, worse_steps: 1, tied_steps: 0 }, 5), /对不上/,
    '三者相加不等于场数-1 时报出来，不圆过去');
});

test('截断声明：行数不等于全场符合判据的只数', () => {
  assert.match(truncationSentence({ total_matched: 372, limit: 80, rows: new Array(80).fill({}) }),
    /符合判据的共 372 只/);
  assert.match(truncationSentence({ total_matched: 372, limit: 80, rows: new Array(80).fill({}) }),
    /不等于/, '这一句是本屏的头号陷阱：80 行会被读成"全场只有 80 只在稳步前进"');
  assert.match(truncationSentence({ total_matched: 12, limit: 80, rows: new Array(12).fill({}) }), /没有截断/);
  assert.match(truncationSentence({ rows: new Array(40).fill({}) }), /说不清/,
    '没交总数时不能说"共 40 只"，也不能说"没截断"');
  assert.match(truncationSentence({ total_matched: 0, rows: [] }), /0 只/);
});

test('xy 档的自检只在真有一轴没前进时出声，总分档一律 null', () => {
  const good = { first_x: 100, latest_x: 50, first_y: 900, latest_y: 800 };
  const bad = { first_x: 100, latest_x: 120, first_y: 900, latest_y: 800 };
  assert.equal(axisRegression({ metric: 'xy', rows: [good, good] }), 0);
  assert.equal(axisRegression({ metric: 'xy', rows: [good, bad] }), 1);
  assert.equal(axisRegression({ metric: 'oper', rows: [bad] }), null,
    '总分档没有"另一轴"这条判据，拿它报错就是假缺陷');
  assert.equal(axisRegression({ metric: 'xy', rows: [{ first_x: null, latest_x: 50 }] }), 0,
    '缺数不算后退 —— 那一格本身显示 —，由 — 的存在去说没交，不在这里判成前进或后退');
});

test('中位幅度只按已交出的行数，全空时不给数', () => {
  assert.equal(medianGainPct([{ gain_frac: 10 }, { gain_frac: 30 }, { gain_frac: 20 }]), 20);
  assert.equal(medianGainPct([{ gain_frac: 10 }, { gain_frac: 30 }]), 20);
  assert.equal(medianGainPct([{ gain_frac: null }, {}]), null);
  assert.equal(medianGainPct([]), null);
});

test('名字就是代码本身的行要数得出来', () => {
  assert.deepEqual(nameIsCode([{ security_code: '920305.BJ', name: '920305.BJ' },
    { security_code: '600000.SH', name: '浦发银行' }, { security_code: '000001.SZ', name: '' }]),
  ['920305.BJ', '000001.SZ']);
});

test('窗口那句用的是接口交的数，不是页面写的 6', () => {
  const s = windowSentence({
    date: '2026-10-16', window: { sessions: 6, steps: 5, first_day: '2026-10-09', last_day: '2026-10-16', is_trading_days: true },
    universe: { per_session_min: 5558, per_session_max: 5563 },
  });
  assert.match(s, /2026-10-09 ~ 2026-10-16/);
  assert.match(s, /6 个交易日/);
  assert.match(s, /共 5 步/);
  assert.match(s, /5558~5563 只/);
  assert.match(s, /交易日数/);
});

test('两档按钮与三档上限就是后端认的那几样', () => {
  assert.deepEqual(METRIC_BUTTONS.map(b => b.key), ['xy', 'oper']);
  assert.deepEqual(LIMIT_OPTIONS, [50, 80, 150]);
  for (const n of LIMIT_OPTIONS) assert.ok(n >= 5 && n <= 200, '后端 limit 收 5~200，超出会被 FastAPI 拒');
});

// ---------------------------------------------------------------- 接线与措辞

test('表体只有一份实现，四维象限那一屏是调用方', () => {
  assert.match(page, /import \{ SteadyRankBoard \} from "@\/components\/quant\/SteadyRankBoard"/);
  assert.match(page, /<SteadyRankBoard state=\{rm\} metric=\{rmMetric\}/);
  assert.doesNotMatch(page, /series_path/, '本屏不留一份自己的格子（那就是第二张表体）');
  assert.doesNotMatch(comp, /useProbe/, '取数在页面那一层，表体只吃 state（两份 useProbe 会各拉一次全库聚合）');
});

test('端点路径不带 /api 前缀，参数名与 FastAPI 签名逐一对上', () => {
  assert.doesNotMatch(page, /["']\/api\//, 'probe 自己补 /api，页面里写了就是双前缀');
  const route = appPy.slice(appPy.indexOf('@app.get("/api/quant/rank-momentum")'),
    appPy.indexOf('@app.get("/api/quant/rank-momentum")') + 700);
  assert.match(route, /def quant_rank_momentum\(/, '路由写了却不调用实现＝假端点');
  for (const k of ['metric', 'limit', 'refresh']) {
    assert.match(route, new RegExp(`\\b${k}\\b`), `端点签名里没有 ${k}，页面发过去会被静默丢掉`);
    assert.match(page, new RegExp(`${k}:`), `页面没把 ${k} 拼进取数参数`);
  }
  assert.match(page, /"\/quant\/rank-momentum"/);
});

/** 表头与格子都由同一份列清单驱动，所以这里对的是**三份清单之间**的一致性：
 *  ① 组件里 NUM/PATH/TAIL 三个数组的字面量名，② 格子 `cellProps(i, "…")` 的名字，
 *  ③ 接口真库交出的字段名（fixture）。写错不报错，只是那一列永不亮或两列印错名。 */
function colNames(from: string, to: string): string[] {
  const block = comp.slice(comp.indexOf(from), comp.indexOf(to));
  return [...block.matchAll(/\["([\w_]+)",/g)].map(m => m[1]);
}

test('表头顺序与格子顺序逐列同名同序（16 列一一对上）', () => {
  const head = [...colNames('const NUM_COLS', 'const PATH_COLS'),
    ...colNames('const PATH_COLS', 'const TAIL_COLS'),
    ...colNames('const TAIL_COLS', 'export function')];
  const cells = [...comp.matchAll(/x\.cellProps\(i, "([\w_]+)"/g)].map(m => m[1]);
  assert.equal(head.length, 16, '列清单就是这一屏的表头顺序，少一格说明有人只在格子那边加了一列');
  assert.deepEqual(cells, head, '表头顺序与格子顺序必须完全一致（插错一格就让两列印错名）');
  assert.deepEqual([...fixture.row_keys].sort(), [...head, 'board', 'quadrant_label',
    'sessions_used', 'steps_total', 'first_x', 'latest_x', 'first_y', 'latest_y', 'matched_total'].sort(),
    '屏面列 + 只进 tooltip 的补充字段，要正好等于接口交出的那一份行');
});

test('路径三列是文本：不给排序按钮，并说明为什么不排', () => {
  const pathBlock = comp.slice(comp.indexOf('{PATH_COLS.map'), comp.indexOf('{TAIL_COLS.map'));
  assert.match(pathBlock, /<th /, '文本列用裸 <th>，不进 SortHeader');
  assert.doesNotMatch(pathBlock, /SortHeader/);
  assert.match(pathBlock, /不参与排序/, '要写明为什么不排，否则读者以为这一屏漏了排序');
  const sortableBlocks = comp.slice(0, comp.indexOf('{PATH_COLS.map'))
    + comp.slice(comp.indexOf('{TAIL_COLS.map'), comp.indexOf('</tr>'));
  assert.match(sortableBlocks, /SortHeader/, '数值列走 SortHeader');
  assert.doesNotMatch(sortableBlocks.match(/\{PATH_COLS[\s\S]*?\}\}/)?.[0] ?? '', /sortKey/,
    'sortKey 只能出现在数值列那两段里');
});

test('列字段的名字后端真交过 —— 每一列都有出处', () => {
  const declared = [...comp.matchAll(/^\s*\["([\w_]+)", "/gm)].map(m => m[1]);
  const defs = Object.keys(fixture.definitions);
  const missing = declared.filter(k => !defs.includes(k));
  assert.deepEqual(missing, [], `这些列在接口 definitions 里找不到出处：${missing.join('、')}`);
  assert.ok(defs.length >= declared.length,
    '接口交的解释至少要把每一列盖住（多出来的是首末值之类的补充字段，允许）');
  // 行上的字段与 definitions 的键必须同源（后端自己钉了一份，这里再对一次屏面用的那份）
  assert.deepEqual([...fixture.row_keys].sort(), [...defs].sort(),
    '行字段与定义清单不是同一份 —— 要么有列没出处，要么有解释对着不存在的字段');
});

test('门槛与步数一个都不写死：页面读 thresholds 与 window', () => {
  assert.match(comp, /d\.thresholds\?\.min_better_steps/);
  assert.match(comp, /d\.thresholds\?\.max_worse_steps/);
  assert.match(comp, /d\.thresholds\?\.min_gain_frac_pct/);
  assert.match(comp, /d\.window\?\.steps/);
  assert.match(comp, /d\.window\?\.sessions/);
  // 判据数字不许在页面里出现第二份（slowHint 里那句"5.3 秒"是实测耗时，不是判据）
  assert.doesNotMatch(comp, /前进 ≥ 3|后退 ≤ 1|≥ 10%|近 6 场稳步/, '判据门槛只住接口那一份');
  assert.doesNotMatch(comp, /stepsSentence\(r, 5\)|stepsSentence\([^,]+,\s*\d+\)/,
    '步数上限用交出的 steps，不写 5');
});

test('方向那句（名次越小越靠前）由后端 caveat 承担，页面不另写一遍', () => {
  // caveat 现在随共用折叠块上屏（用户 2026-10-19 夜：「这些内容都默认收缩起来」），
  // 但**句子还是后端那一条**，summary 把条数摆在外面 ⇒ 收起不等于没写。
  assert.match(comp, /<CaliberFold caveats=\{d\.caveats\}/);
  assert.match(fixture.caveats.map((c: { code: string; text: string }) => c.text).join('\n'),
    /1 = 全场第一，数字越小越靠前/);
  assert.doesNotMatch(comp, /稳步前进」= 名次/, '页面再写一份就是两处口径，改一处另一处仍亮');
});

test('截断声明与自检不折进任何折叠框', () => {
  // 这一屏有两处折叠：`<ExplainBox>`（判据原句）与 `<CaliberFold>`（声明/字段/覆盖/出处）。
  // 两处都不许带走告警，所以这里取的是**两个块之外**的那段源码。
  const eb = comp.indexOf('<ExplainBox'), ebEnd = comp.indexOf('</ExplainBox>');
  const cf = comp.indexOf('<CaliberFold'), cfEnd = comp.indexOf('/>', cf);
  const outside = comp.slice(0, eb) + comp.slice(ebEnd, cf) + comp.slice(cfEnd);
  assert.match(outside, /truncationSentence\(d\)/, '「榜被上限截断」这句要在两处折叠之外');
  assert.match(outside, /axisRegression/, '一轴没前进的行数是告警，不许藏');
  assert.match(outside, /pathMismatch/, '路径段数与场数不符也是告警，不许藏');
  assert.match(outside, /nameOnly\.length > 0/, '名称列里"名字就是代码"要点数，藏在折叠框里等于没人看');
  assert.match(comp.slice(cf, cfEnd), /notes=\{\[d\.universe\?\.note\]\}/,
    '母体那句话说的是"这一窗哪些票进得了榜"，属于可收的描述，跟着一起折');
});

/** 用户 2026-10-19 夜：「把选项留出，描述内容都收缩起来」——
 *  这一段判据描述（这一档排的是什么 / 进榜的门槛）收进默认收起的说明框；
 *  读数行与截断声明、中位读数留在外面。措辞一字未改，只换"默认可见 / 点开可见"的分层。 */
test('判据描述两段折进默认收起的说明框，读数与截断声明留在框外', () => {
  const boxStart = comp.indexOf('<ExplainBox');
  const boxEnd = comp.indexOf('</ExplainBox>');
  assert.ok(boxStart > 0 && boxEnd > boxStart, '这一屏要有说明框（ExplainBox 是原生 details、不带 open ⇒ 默认收起，闸在 explainBox.test.ts）');
  const box = comp.slice(boxStart, boxEnd);
  assert.match(box, /<Row label="这一档排的是"/, '「这一档排的是什么」折进框里（点开可见）');
  assert.match(box, /<Row label="进榜的门槛"/, '「进榜的门槛」折进框里');
  assert.match(box, /d\.thresholds\?\.min_better_steps/, '折进框里也逐项读接口 thresholds，一个数都不写死');
  assert.match(box, /d\.thresholds\?\.require_both_axes/, '两档差异那句仍读接口，不写死');
  assert.match(box, /d\.rule/, '判据原句在框里（后端原样转述，页面不改写）');
  assert.doesNotMatch(box, /truncationSentence/, '截断声明不许跟着这两段搬进框');
  const before = comp.slice(0, boxStart);
  assert.match(before, /windowSentence\(d\)/, '「看的是哪几天」是读数行，默认留在外面');
  assert.match(before, /truncationSentence\(d\)/, '截断声明渲染在框之前（默认可见位置）');
  assert.match(before, /前进幅度的中位是/, '中位读数（含"不是判据、也不是全场的中位"那半句防误读）留在外面');
  assert.match(before, /每页/, '每页行数的档位按钮在控件行里，控件永远默认可见');
});

test('System 那一格登记了这个端点（能力清单少一行＝以后没人知道有这一屏）', () => {
  assert.match(system, /"\/quant\/rank-momentum\?metric=xy&limit=80"/);
  assert.match(system, /名次稳步前进榜/);
  assert.match(system, /where: "\/quadrant"/);
});
