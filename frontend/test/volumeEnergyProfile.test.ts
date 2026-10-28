import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  bandLines, bandRange, buildPayload, draftMaxScore, finiteScore, missingBands, scoreMax,
} from '../src/lib/volumeEnergy.ts';

/**
 * 系统配置那一卡「倍量加权分档」（`components/VolumeEnergyPanel.tsx` + `lib/volumeEnergy.ts`）。
 *
 * ⚠️ 口径改于 2026-10-19 第二稿（不是为了让旧断言通过而放宽，是旧断言钉的那把尺已经被否了）：
 *  · 第一稿那句「占比＝分值÷Σ分值」＋量纲 `sum`／`top` 整族作废，
 *    用户原话是「其实不存在系数这个参数，系数这个说法是错的」；
 *  · 算式改成 `得分 = Σ 每档 ( 这一档在窗口里出现的次数 × 这一档的分值 )`，
 *    分值就是**一天落在这一档计几分**，不除以任何东西；
 *  · 于是这一族旧断言（Σ＝649、首档占比 0.001541、占比合计＝1、草稿满窗上限 9.8613、
 *    normalize_options 下拉、min_energy_default、ge_default_min）逐条换成第二稿断言：
 *    「一天最多贡献＝分值」「满窗＝分值 × 窗口场数」「上限来源＝草稿里分值最大的那一档（动态）」。
 *
 * 钉的两族东西：
 *  ① **算式**（可以真跑函数的部分）：分值一改，只有落在该档的那些天受影响；
 *     缺分值/缺窗口场数交 null，画面上是 —，**绝不落到 0**（0 分＝这一档一分不计，是另一件事）。
 *  ② **界面契约**（读源码的部分）：档表读接口 `bands`（前端不抄第二份 0.1 档表、也不抄默认分值），
 *     算式与名次规则都念接口原文，「保存了≠已生效」必须写在回显里，422 的原因原样带出，
 *     `**` 与 ASCII 双引号不许上屏，lib 里不许留没人调用的假出口。
 */

const panel = readFileSync(new URL('../src/components/VolumeEnergyPanel.tsx', import.meta.url), 'utf8');
const lib = readFileSync(new URL('../src/lib/volumeEnergy.ts', import.meta.url), 'utf8');
const system = readFileSync(new URL('../src/pages/System.tsx', import.meta.url), 'utf8');
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');

/** 后端 2026-10-19 第二稿那一刻接口 `default_scores` 那份（分值＝一天落在这一档计几分）。
 *  测试里这份是**接口 payload 的替身**，不是前端的第二份档表：档名到区间的摊开由 parseBand 现拆。 */
const LIVE = {
  '1.0-1.1': 0.05, '1.1-1.2': 0.08, '1.2-1.3': 0.12, '1.3-1.4': 0.18, '1.4-1.5': 0.25,
  '1.5-1.6': 0.35, '1.6-1.7': 0.45, '1.7-1.8': 0.55, '1.8-1.9': 0.68, '1.9-2.0': 0.80,
  '2.0-2.1': 0.88, '2.1-2.2': 0.93, '2.2-2.3': 0.96, '2.3-2.4': 0.98, '2.4-2.5': 0.99,
  '2.5-3.5': 1.00, '3.5-5.0': 0.90, '5.0+': 0.70,
};
function parseBand(label: string): { label: string; lo: number | null; hi: number | null } {
  if (label.endsWith('+')) return { label, lo: Number(label.slice(0, -1)), hi: null };
  const [lo, hi] = label.split('-');
  return { label, lo: Number(lo), hi: Number(hi) };
}
const BANDS = Object.keys(LIVE).map(parseBand);

test('第二稿算式：一天最多贡献＝分值本身，满窗＝分值 × 窗口场数（没有分母可除）', () => {
  const lines = bandLines(BANDS, LIVE, 40);
  assert.equal(lines.length, 18, '摊开的行数只跟接口 bands 走（这里给的替身是 18 档）');
  const first = lines.find(l => l.label === '1.0-1.1');
  assert.ok(first, '首档要在');
  assert.equal(first!.score, 0.05);
  assert.equal(first!.dayMax, 0.05, '这一档一天最多贡献＝分值本身，不是分值÷什么东西');
  assert.equal(first!.windowMax, 0.05 * 40, '满窗（40 场全在这一档）＝分值 × 场数');

  // 分值一改，只有落在该档的那些天受影响：别的档一格都不动（第一稿的÷Σ 会把全表都牵动）
  const raised = { ...LIVE, '1.0-1.1': 0.10 };
  const again = bandLines(BANDS, raised, 40);
  assert.equal(again.find(l => l.label === '1.0-1.1')!.dayMax, 0.10);
  assert.equal(again.find(l => l.label === '2.4-2.5')!.dayMax, LIVE['2.4-2.5'],
    '没动的那一档「一天最多贡献」必须一格都不变（旧那把÷Σ 的尺会把它一起拉低）');
  assert.equal(again.find(l => l.label === '2.4-2.5')!.windowMax, LIVE['2.4-2.5'] * 40);

  // 顶档不是档名写死的那一档：是**草稿里分值最大**的那一档
  assert.equal(scoreMax(LIVE), 1.0, '默认那份顶档分值＝1.0 ⇒ 后端 scale.max_per_day＝1.0');
  assert.equal(draftMaxScore(LIVE, 40), 40.0, '满窗上限＝40 × 1.0＝40（后端 scale.max_score 就是这句）');
  assert.deepEqual(lines.filter(l => l.isTop).map(l => l.label), ['2.5-3.5'],
    '上限来源标在分值最大的那一档上');

  // 用户把尾档抬到 1.2 ⇒ 顶档换人、上限跟着换，界面不许还认 2.5-3.5
  const tailHigh = { ...LIVE, '5.0+': 1.2 };
  assert.equal(scoreMax(tailHigh), 1.2);
  assert.equal(draftMaxScore(tailHigh, 40), 48);
  assert.deepEqual(bandLines(BANDS, tailHigh, 40).filter(l => l.isTop).map(l => l.label), ['5.0+'],
    '顶档跟着分值走，不是按档名或区间位置认的');
  // 并列最大 ⇒ 都标出来（不许只认第一个，那等于又写死了一档）
  const tie = { ...LIVE, '3.5-5.0': 1.0 };
  assert.deepEqual(bandLines(BANDS, tie, 40).filter(l => l.isTop).map(l => l.label),
    ['2.5-3.5', '3.5-5.0'], '并列最大的两档都要标成上限来源，顺序按接口 bands');
});

test('窗口场数或分值缺了就是 null，不落到 0；非数与负数不进取值', () => {
  const lines = bandLines([{ label: '1.0-1.1', lo: 1, hi: 1.1 }], { '1.0-1.1': '' }, 40);
  assert.equal(lines[0].score, null, '这一档没填＝null，不是 0 分');
  assert.equal(lines[0].dayMax, null);
  assert.equal(lines[0].windowMax, null);
  assert.equal(bandLines([{ label: '1.0-1.1', lo: 1, hi: 1.1 }], { '1.0-1.1': 0.5 }, null)[0].windowMax, null,
    '接口没交 window_days 就不算满窗，不拿 40 顶');
  assert.equal(draftMaxScore({}, 40), null, '一档都没填就没有顶档，也就没有上限（不是上限 0）');
  assert.equal(draftMaxScore(LIVE, 0), null, '场数是 0 就当没读到，不给出 0 这个假上限');
  assert.equal(scoreMax({}), null);

  assert.equal(finiteScore(''), null);
  assert.equal(finiteScore('abc'), null);
  assert.equal(finiteScore(-3), null, '负分值不是合法输入（后端也拒）');
  assert.equal(finiteScore('0.5'), 0.5, '输入框来的是字符串');
  assert.equal(finiteScore(Infinity), null, 'Infinity 不能进档表');
  assert.deepEqual(missingBands(BANDS, { ...LIVE, '5.0+': null }), ['5.0+'],
    '没填的档要被点名（少一档等于偷偷把这档算成 0 分）');
  assert.equal(bandRange(5.0, null), '5 以上', '尾档不自己补一个上界');
  assert.equal(bandRange(1, 1.1), '1~1.1');
});

test('POST 体第二稿形状：只有 scope／global／by_market，没有 normalize', () => {
  const p = buildPayload({
    scope: 'global', global: LIVE, byMarket: { SH: LIVE },
    bands: BANDS, markets: ['SH', 'SZ', 'BJ'],
  });
  assert.deepEqual(Object.keys(p).sort(), ['by_market', 'global', 'scope'],
    `体里只许这三个键（带 normalize 就会被后端 422 拒）：${Object.keys(p)}`);
  assert.equal('normalize' in p, false, '第一稿那个 normalize 键不许复活');
  assert.equal(p.scope, 'global');
  assert.deepEqual(p.by_market, {}, 'scope=global 时不交 by_market：留着草稿会被读成已经单配');
  assert.deepEqual(Object.keys(p.global).sort(), Object.keys(LIVE).sort());
  assert.equal(p.global['2.5-3.5'], 1.0, '分值按原样交（不折算、不摊平）');
  // 认不得的档名不进体（后端会整笔拒：档名由 BANDS 给，界面不许自己造）
  const withExtra = buildPayload({
    scope: 'global', global: { ...LIVE, '9.9-10.0': 5 }, bands: BANDS, markets: ['SH'],
  });
  assert.equal('9.9-10.0' in withExtra.global, false, '前端自己造的档名不许进请求');
  // market 作用域：只有真填了的才单配，空表＝落回全局（不是显式零分）
  const byMkt = buildPayload({
    scope: 'market', global: LIVE,
    byMarket: { SH: LIVE, SZ: {}, BJ: { '1.0-1.1': '' } }, bands: BANDS, markets: ['SH', 'SZ', 'BJ'],
  });
  assert.deepEqual(Object.keys(byMkt.by_market), ['SH'], '没单配的市场不能以空表形式混进请求');
  assert.ok(missingBands(BANDS, p.global).length === 0);
});

test('第一稿那一族（占比／分母／量纲／归一）在前端已经拆干净', () => {
  const code = strip(lib);
  for (const gone of ['shareDenominator', 'shareOf', 'scoreSum', 'normalize', 'denominator', '占比', '归一', '量纲'])
    assert.ok(!code.includes(gone), `lib 里还剩「${gone}」—— 那一层已经取消，留着就是没人调用的假出口或第二把尺`);
  const view = strip(panel);
  for (const gone of ['normalize_options', 'normalizeLabel', 'share', '占比', '量纲', 'min_energy_default', 'ge_default_min'])
    assert.ok(!view.includes(gone), `配置卡界面里还剩「${gone}」：这一层后端已经不交了`);
  // lib 的导出必须真有人调用（不许留一个没人调用的假出口）
  const exported = [...code.matchAll(/export function ([A-Za-z]+)\(/g)].map(m => m[1]);
  assert.ok(exported.length >= 6, `lib 该导出那几个纯函数，实到 ${exported.join('、')}`);
  const callers = strip(panel) + readFileSync(new URL('../test/volumeEnergyProfile.test.ts', import.meta.url), 'utf8');
  for (const fn of exported)
    assert.ok(new RegExp(`\\b${fn}\\b`).test(callers), `lib/volumeEnergy.ts 导出的 ${fn} 没有人调用（要么用要么删）`);
});

test('界面那一卡：算式、上限、实测分位、名次规则全部读接口，前端不抄第二份', () => {
  // 档名/区间只能来自接口 bands —— 前端抄一份 0.1 档表就是第二把尺（会漂）
  assert.doesNotMatch(panel, /['"]1\.0-1\.1['"]/, '界面里出现了硬编码档名');
  assert.doesNotMatch(lib, /['"]1\.0-1\.1['"]/, '算式模块里也不许有硬编码档名');
  assert.doesNotMatch(panel, /['"]2\.5-3\.5['"]|['"]5\.0\+['"]/);
  // 「恢复默认」用的是接口交回的 default_scores，不是界面自己的一份默认分值表
  assert.match(panel, /restore = \(\) => \{[\s\S]{0,300}global: cfg\?\.default_scores/);
  assert.doesNotMatch(panel, /const DEFAULT[A-Z_]*\s*=/, '界面里不许有一份本地默认分值常量');
  assert.doesNotMatch(panel.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, ''), /18 档/,
    '屏幕上不许抄档位条数（读接口 bands 的长度）');
  // 算式原文与单位逐字念接口 scale（第二稿：formula 是接口给的，界面不自己拼一句）
  assert.match(panel, /cfg\?\.scale\?\.formula/, '算式读 scale.formula');
  assert.match(panel, /scale\?\.formula \?\? "后端没交 formula/);
  assert.match(panel, /plain\(cfg\.scale\.note\)/, 'scale.note 那句「没有系数」的原文也要原样透出');
  // 上限与实测分布并排（都是接口那份）
  assert.match(panel, /trimNum\(cfg\?\.scale\?\.max_score\)/);
  assert.match(panel, /trimNum\(cfg\?\.scale\?\.max_per_day\)/);
  assert.match(panel, /trimNum\(dist\?\.p50\)[\s\S]{0,320}trimNum\(dist\?\.max\)/);
  assert.match(panel, /≥0 分的 \{dash\(dist\?\.at_or_above_zero\)\} 只/,
    '第二稿把 ge_default_min 换成了 at_or_above_zero：那个数读接口，门槛默认就是 0');
  // 顶档动态：界面上不许出现"最高档＝2.5-3.5"那种写死，也不许自称固定的第几档
  assert.match(panel, /l\.isTop/, '上限来源那一档由分值判');
  assert.match(panel, /上限来源/);
  // 名次规则念接口（rank_rule），配置卡不自造那句话
  assert.match(panel, /cfg\?\.rank_rule \?\?/);
  assert.doesNotMatch(strip(panel), /同分同名|按位置顺延|不占号/, '配置卡自己写了一句名次规则');
  // 作用域仍只有 global／market，选项来自接口 scopes；没单配的市场落回全局这句还在
  assert.match(panel, /cfg\?\.scopes \?\? \[\]/);
  assert.match(panel, /cfg\?\.markets \?\? \[\]/);
  assert.match(panel, /没单配的市场落回全局/);
  // 指纹 / 批次 / 同批 / 后台作业
  assert.match(panel, /dash\(cfg\?\.fingerprint\)/);
  assert.match(panel, /status\?\.batches \?\? status\?\.stored_batches/);
  assert.match(panel, /各多少行多少场|条批次都没读到/);
  assert.match(panel, /dash\(b\.rows\)[\s\S]{0,120}dash\(b\.sessions\)/);
  assert.match(panel, /status\?\.in_sync === false/);
  assert.match(panel, /job\.running === true/);
  assert.match(panel, /正在重算/);
});

test('保存了不等于已生效：回显必须念出「跑完之前倍量榜筛的还是旧批」并给 reason 原文', () => {
  assert.match(panel, /正在按新档表重算历史场，跑完之前倍量榜筛的还是旧批/);
  assert.match(panel, /energy_batch_stale/, '要指路到倍量榜那一侧的那句 caveat');
  assert.match(panel, /后端回的原话：\$\{plain\(r\.reason\)/, 'reason 原文逐字透出（只剥粗体标记）');
  assert.match(panel, /rebuild\?\.started === true/, '起没起重量读后端那一格，不靠界面猜');
  assert.match(panel, /没有起新重算/, '没起也要说清是哪种情况（口径没变 / 有人在跑）');
  assert.match(panel, /这一笔整条没存，生效的还是原来那份档表/, '422 之后不能留下「已保存」的错觉');
  assert.match(panel, /errorText\(parsed\)/, '后端那句原因原样带出，不许折叠成保存失败');
  assert.match(panel, /保存了不等于已生效|保存了≠已生效/);
  // 恢复默认是**组一份 payload 再 POST**，不是本地改数字
  assert.match(panel, /const restore = \(\) => \{[\s\S]{0,400}buildPayload\(/);
  assert.match(panel, /恢复默认分值/);
});

test('取数与保存两条路都在 /api/config/volume-energy 上，读的是 data 信封', () => {
  assert.match(panel, /apiUrl\("\/api\/config\/volume-energy"\)/);
  assert.match(panel, /method: "POST"/);
  assert.match(panel, /body\?\.data/, 'GET 走 {data:…} 信封');
  assert.match(panel, /rec\.reason \?\? ""/, 'POST 的 reason 在信封**顶层**，不在 data 里');
  assert.match(panel, /连不到后端，这一笔档表没写进去/, '连不上要说没写进去，不是「已保存」');
  assert.match(panel, /不假定已生效/);
});

test('这一卡挂在系统配置页，端点也登记进自检清单', () => {
  assert.match(system, /import \{ VolumeEnergyPanel \} from "@\/components\/VolumeEnergyPanel"/);
  assert.match(system, /<VolumeEnergyPanel \/>/);
  assert.match(system, /path: "\/config\/volume-energy"/, '自检清单里 path 不带 /api 前缀');
  assert.match(system, /path: "\/config\/volume-energy", label: [^\n]*where: "\/system"/,
    '落点要写系统配置这一屏的路由');
  assert.match(system, /path: "\/quant\/energy-track"/);
  // 自检清单的说明文字也不许还写着第一稿那两件东西
  const line = (system.match(/path: "\/config\/volume-energy"[^\n]*/) ?? [''])[0];
  assert.doesNotMatch(line, /量纲|sum／top|占比/, '自检清单里还写着第一稿的量纲');
  assert.match(line, /一天落在这一档算几分|分值/);
});

test('中文串里不嵌 ASCII 双引号，渲染文本没有裸 **（界面与算式两份都查）', () => {
  for (const [name, code0] of [['VolumeEnergyPanel', panel], ['volumeEnergy', lib]] as const) {
    const code = strip(code0);
    const hits = code.match(/[^\s]\*\*|\*\*[^\s]/g) ?? [];
    assert.deepEqual(hits, [], `${name}：这些地方有裸的 **，会原样显示：${hits.join(' ')}`);
    assert.doesNotMatch(code, /[\u4e00-\u9fff]"[\u4e00-\u9fff]/,
      `${name}：中文句子中间出现了 ASCII 双引号（要用「」）`);
    for (const b of code.match(/>([^<>{}]*[\u4e00-\u9fff][^<>{}]*)</g) ?? []) {
      assert.doesNotMatch(b, /"/, `${name}：JSX 文本节点里出现了 ASCII 双引号：${b}`);
    }
  }
});

test('赋分这一格必须看着就能改、也真的能改（10-19 用户点名「这里没办法让我进行赋分啊」）', () => {
  const code = strip(panel);
  const table = code.slice(code.indexOf('function BandTable'), code.indexOf('function BandTable') + 6200);
  assert.ok(table.length > 2000, '没截到档表那一段实现');
  // ① 每一档都渲染一个输入框，且这个输入框没有 disabled／readOnly（整卡里只有按钮才许 disabled）
  assert.match(table, /<input value=\{scores\[l\.label\] \?\? ""\}/, '分值那一格不是输入框');
  assert.match(table, /onChange=\{e => onChange\(l\.label, e\.target\.value\)\}/, '输入框没接到草稿状态');
  assert.doesNotMatch(table, /<input[^>]*\b(disabled|readOnly)=/, '分值输入框被 disabled/readOnly 了，用户就赋不了分');
  assert.match(table, /aria-label=\{`\$\{title\} \$\{l\.label\} 的分值`\}/, '输入框没有可寻名的 aria-label');
  // ② 一眼看出"这一列能改"：列头有「可改」标记，表上方明写改哪一格、改完按哪个按钮
  assert.match(table, /可改/, '列头没有"可改"这一标记（那 18 格长得和只读列一样，用户找不到赋分的入口）');
  assert.match(table, /改分就动/, '没有一句"改哪一格"的话');
  assert.match(table, /保存这份档表/, '没有把"改完按哪个按钮"说清');
  // ③ 与输入框数值完全重复的那一列（"这一档一天最多贡献"＝分值本身）已从表上删掉：
  //    两列同一个数，用户读不出哪一列是能改的（dayMax 还在 lib 里，改成输入框的悬停）
  assert.doesNotMatch(table, /^\s*这一档一天最多贡献\s*$/m, '那一列与输入框数值相同，留着就是把"哪个能改"糊掉');
  assert.match(table, /trimNum\(l\.dayMax\)/, '分值本身的读数应从悬停给出（它不再占一列）');
  // ④ 按交易所单配的那几张表共用同一个 BandTable ⇒ 每一档同样可改，不是只能改全局那张
  assert.match(code, /scores=\{byMarket\[m\] \?\? \{\}\}/, '单配那张没共用档表（改不了每一档）');
  assert.match(code, /onChange=\{\(lab, v\) => setByMarket/, '单配那张没接 onChange');
  assert.match(code, /单配这一市场/, '没有"给某个市场单配"的入口');
});
