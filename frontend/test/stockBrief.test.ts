import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  AUTO_RETRY_COOLDOWN_MIN, autoRunDecision, contractSentence, mergeMissing, minutesSince,
  missingPrerequisites, numericOf, receiptLines,
  rowMissingSentence, rowReceiptSentence,
} from '../src/lib/stockBrief.ts';
import type { BriefSegment, FactsBrief } from '../src/lib/stockFacts.ts';

const lib = readFileSync(new URL('../src/lib/stockBrief.ts', import.meta.url), 'utf8');
const run = readFileSync(new URL('../src/lib/stockBriefRun.ts', import.meta.url), 'utf8');
const box = readFileSync(new URL('../src/components/stock/StockBriefBox.tsx', import.meta.url), 'utf8');
const facts = readFileSync(new URL('../src/components/stock/StockFacts.tsx', import.meta.url), 'utf8');
const llm = readFileSync(new URL('../src/lib/llm.ts', import.meta.url), 'utf8');
const py = readFileSync(new URL('../../duanxian/stock_brief.py', import.meta.url), 'utf8');

const seg = (o: Partial<BriefSegment>): BriefSegment => ({
  key: 'k', label: '某段', taken: 1, total: 1, truncated: false, dropped: false, note: '', ...o,
});

/** 冷却与"距今几分钟"都要一个不会自己走的钟。 */
const NOW = new Date('2026-10-27T14:00:00+08:00').getTime();
const at = (minAgo: number) => new Date(NOW - minAgo * 60000).toISOString();
const READY = {
  hasBrief: false, error: null, failedAt: null,
  prereq: [] as string[], templateReady: true, promptsLoading: false, now: NOW,
};

test('2026-10-27 用户点名翻转：库里没有就自动打，有就直接显示（旧那条「只有点按钮才发起」作废）', () => {
  // 全新一只票（库里没这一行）⇒ 进页面自己发一趟
  assert.equal(autoRunDecision(READY).fire, true);
  assert.match(autoRunDecision(READY).why, /库里还没有这一份/);
  assert.match(autoRunDecision(READY).why, /出网/);
  // ① 库里已经有成功的正文 ⇒ 绝不再打（这就是「后续页面加载先看数据库有没有」那一条）
  const shown = autoRunDecision({ ...READY, hasBrief: true });
  assert.equal(shown.fire, false);
  assert.match(shown.why, /库里已经有这一份/);
  // ② 前置不齐 / 话术没到位 ⇒ 不打，而且那句要说清差哪样（不发注定被拒的请求）
  assert.match(autoRunDecision({ ...READY, prereq: ['还没接入 AI'] }).why, /还差：还没接入 AI/);
  assert.equal(autoRunDecision({ ...READY, templateReady: false }).fire, false);
  assert.match(autoRunDecision({ ...READY, templateReady: false }).why, /话术没取到/);
  assert.match(autoRunDecision({ ...READY, promptsLoading: true }).why, /话术还在从接口取/);
  // ③ 刚失败过 ⇒ 冷却内不自动重打（每次进页面白烧一次额度是最容易翻的车）
  const cool = autoRunDecision({ ...READY, error: 'AI 请求限流', failedAt: at(5) });
  assert.equal(cool.fire, false);
  assert.match(cool.why, /5 分钟前失败/);
  assert.match(cool.why, /不再自动打/);
  assert.match(cool.why, /那一发不受这个限制/, '手动那一发要照样打得动，这话得说在屏上');
  assert.equal(autoRunDecision({ ...READY, error: 'AI 请求限流', failedAt: at(AUTO_RETRY_COOLDOWN_MIN + 1) }).fire, true,
    '过了冷却 ⇒ 下一次进页面允许再自动打一趟');
  // ④ 有失败记录但那个时间读不出分钟 ⇒ 判不了就不自动花钱（探测器不许在坏的时候装成没事）
  const blind = autoRunDecision({ ...READY, error: 'AI 请求限流', failedAt: '不是个时间' });
  assert.equal(blind.fire, false);
  assert.match(blind.why, /读不出分钟/);
  // 只有 error、没有 failed_at（旧行）同样不许自动打
  assert.equal(autoRunDecision({ ...READY, error: 'AI 请求限流', failedAt: null }).fire, false);
  assert.equal(minutesSince(null), null);
  assert.equal(minutesSince(at(30), NOW), 30);
});

test('自动那一发的接线只照那一处判定走，闸本身不在组件里重写第二套', () => {
  assert.match(box, /autoRunDecision\(\{/, '组件只调 lib 那一份判定，不在这里再数一遍冷却');
  assert.match(box, /useEffect\(\(\) => \{[\s\S]{0,200}decision\.fire/, '进页面按判定自己发起这一趟');
  assert.match(box, /firedFor\.current = code/, '同一只票一次挂载只发一次（StrictMode 会把 effect 跑两遍）');
  assert.match(box, /const code = String\(facts\.code \?\? ""\);/, '切票要重新判，不能拿上一只的闸挡住这一只');
  assert.match(box, /onClick=\{\(\) => \{ void go\(false\); \}\}/, '手动那一发（重新生成）还在');
  assert.doesNotMatch(box, /setTimeout\([^)]*runBrief/, '延时补一发是第二条自动路径，判定只准住 autoRunDecision');
  // 「这一轮为什么没自动打」必须上屏，不许只当内部日志
  assert.match(box, /whyNotAuto/, '拦下来要说得出原因');
  assert.match(box, /库里没有这一份时，进这一页会自己出网打一趟/, '自动出网这件事摆在标题旁边，不折进说明框');
  // 旧口径那句措辞不许留在会渲染出去的代码里（口径改了词必须跟着改）
  const code = box.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '').replace(/"""[\s\S]*?"""/g, '');
  assert.doesNotMatch(code, /点右上角那个按钮才发起|只有点按钮才发起/, '那一句是作废的纪律，留在屏幕上就是假话');
  // 进页面读的还是库里那一份（唯一真源没换）
  assert.match(box, /const row = facts\.brief \?\? null;/, '结论的唯一真源是 facts 里那一行');
});

test('这一趟走红线档（page），不是允许给价位的那一档', () => {
  // 右侧「AI分析」那一档按设计允许给参考价位与止损；这一格只放"这是一家什么公司"
  assert.match(run, /await chatStream\(\[\{ role: "user", content: prompt \}\], mat\.material, \{\}\)/,
    '不传 taskKind ⇒ 后端按 page 档办（PRODUCT_POLICY + check_report_text 照旧拦）');
  assert.doesNotMatch(run, /taskKind/, '这一条链不许改成 analysis 档');
  // 入库前还有一道：写入侧自己再过一遍，浏览器交回来的文本不免检
  assert.match(py, /def check_brief_text/, '写入侧的闸在后端一处');
  assert.match(py, /from review_agent\.product_policy import has_trade_recommendation/,
    '越界判据复用全站那一处正则，不在这里写第二套');
});

test('判读层是叶子：不 import 任何有运行时代码的东西，所以 node --test 能直接跑它', () => {
  const imports = lib.split('\n').filter(l => l.startsWith('import '));
  assert.ok(imports.length >= 2, '这半份至少要有两个类型 import');
  for (const l of imports) assert.match(l, /^import type /, `叶子层不该出现会真去解析 "@/…" 的运行时 import：${l}`);
  assert.doesNotMatch(lib, /chatStream|api\./, '出网的那半份在 stockBriefRun.ts，不在这份里');
});

test('缺数与失败摊在框外，材料明细才进折叠框', () => {
  const missAt = box.indexOf('rowMissingSentence(row)');
  const errAt = box.indexOf('row?.error &&');
  const foldAt = box.indexOf('<ExplainBox title="写这一份时看了哪些材料');
  assert.ok(missAt > 0 && errAt > 0 && foldAt > 0, '三块都在');
  assert.ok(missAt < foldAt && errAt < foldAt,
    '「写这一份时哪几段没读到」与「最近一次重跑失败」必须排在折叠框之前 —— 折起来等于让屏幕说假话');
  const fold = box.slice(foldAt, box.indexOf('</ExplainBox>', foldAt));
  assert.doesNotMatch(fold, /text-warning|role="status"/, '折叠体里不许出现告警');
  assert.match(box, /text-warning">\s*\{rowMissingSentence\(row\)\}/, '缺数那句是告警配色，摊着');
});

test('主按钮不静默 disabled：拦下来要把"还差哪几样"列出来', () => {
  assert.doesNotMatch(box, /<button[^>]*disabled/, '禁掉而不说原因＝用户以为按钮坏了');
  assert.match(box, /这一趟发不出去，还差：/, '点下去先列清单，且不发注定被拒的请求');
  assert.match(box, /现在点它也会拦下来，还差：\{prereq\.join\("；"\)\}/, '没点的时候也要先看得见差什么');
  const noLlm = missingPrerequisites({ hasLlm: false, available: true, numeric: '600519' });
  assert.equal(noLlm.length, 1);
  assert.match(noLlm[0], /AI 探索 · 接入设置/, '要说清去哪一样配齐，不只说"没接入"');
  assert.deepEqual(missingPrerequisites({ hasLlm: true, available: true, numeric: '600519' }), []);
  assert.match(missingPrerequisites({ hasLlm: true, available: true, numeric: '' })[0], /还没选到一只票/);
});

test('AI 正文原样摆、只剥成对粗体，不改写不截断', () => {
  assert.match(box, /\{plain\(row\?\.brief \?\? ""\)\}/, '后端散文与模型正文一样过 plain()');
  assert.match(box, /whitespace-pre-wrap/, '换行要留着：模型按四件事分段');
  assert.doesNotMatch(box, /(brief|plain\(row[^)]*\))[^}\n]*\.slice\(/,
    '页面不许自己截正文（截一半上屏就变成"说明就这么短"）—— 时间戳那种截display不算，所以只查 brief');
});

test('取舍明细的形状只在 lib 一处拼，页面不自数一遍', () => {
  assert.doesNotMatch(box, /\.filter\(s => s\.taken/, '页面自己数"进了几段"就是第二份真相');
  assert.match(box, /receiptLines\(receipt\)\.map/, '逐段那一行走 receiptLines');
  assert.match(box, /rowReceiptSentence\(row\)/, '「几段进了材料」那句走 helper');
  const lines = receiptLines([
    seg({ key: 'reports', label: '研报标题', taken: 8, total: 200 }),
    seg({ key: 'news', label: '新闻', taken: 10, total: 10 }),
    seg({ key: 'lockup', label: '限售解禁', taken: 0, total: 0 }),
    seg({ key: 'margin', label: '融资融券', taken: 2, total: 30, truncated: true }),
  ]);
  assert.match(lines[0], /喂 8 条/, '源给了 200 条、只喂 8 条，必须说出来');
  assert.match(lines[0], /源本轮共 200 条，按预算取 8 条/);
  assert.match(lines[1], /全取/, '取满的段说"全取"，别让人以为还删了东西');
  assert.match(lines[2], /本轮 0 条/);
  assert.match(lines[3], /材料到上限后这一段落过/);
  assert.equal(rowReceiptSentence({ material_segments: [seg({})], material_chars: 3632 } as FactsBrief),
    '1/1 段进了材料，共 3632 字');
});

test('没读到 ≠ 读到了但为空 ≠ 旧版没记：三件事三句话', () => {
  assert.equal(rowMissingSentence(null), '');
  assert.equal(rowMissingSentence({ material_segments: [], material_missing: [] } as FactsBrief), '');
  // 空数组且真有回执 ⇒ 真的每段都读到了，不出声
  assert.equal(rowMissingSentence({ material_segments: [seg({})], material_missing: [] } as FactsBrief), '');
  assert.match(rowMissingSentence({ material_segments: [seg({})], material_missing: ['龙虎榜（东财）'] } as FactsBrief),
    /写这一份时，这几段没读到：龙虎榜（东财）/);
  // null 是"那一版没记这个数"，不能念成"每段都读到了"
  assert.match(rowMissingSentence({ material_segments: [seg({})], material_missing: null } as FactsBrief),
    /旧版回执/);
  assert.match(mergeMissing(['A'], ['B']).join('|'), /B（这一段的请求失败，不是源里没东西）/);
  assert.deepEqual(mergeMissing(['A'], []), ['A']);
});

test('字数契约那句话三态齐全，且把超出的部分如实报出来', () => {
  assert.equal(contractSentence(null, null), '');
  assert.equal(contractSentence(380, 400), '380 字（契约 400 字以内）');
  assert.equal(contractSentence(455, 400), '实际 455 字，比 400 字的契约多了 55 字 —— 这一格照实摆，不截');
});

test('代码形状：带后缀的 6 位 A 股码才给公开源，其余一律空串', () => {
  assert.equal(numericOf('600519.SH'), '600519');
  assert.equal(numericOf('600519'), '600519');
  assert.equal(numericOf('158005.sz'), '158005');
  assert.equal(numericOf('AAPL'), '');
  assert.equal(numericOf('00700.HK'), '');
  assert.equal(numericOf(null), '');
});

test('段名与后端 SEGMENTS 的键必须逐字对上（错一个字母就永远"没读到"）', () => {
  const backendKeys = [...py.matchAll(/\{'key': '([a-zA-Z]+)', 'label':/g)].map(m => m[1]);
  const pageCalls = [...run.matchAll(/\["([a-zA-Z]+)", c => api\./g)].map(m => m[1]);
  assert.ok(backendKeys.length >= 15, `后端只扫到 ${backendKeys.length} 个段键，读取方式坏了`);
  // profile 与 conceptTags 是页面/后端各自的特例：profile 由 facts 那一格带过来，
  // conceptTags 不另发请求（后端从 blocks 那一份回落取）
  const need = backendKeys.filter(k => k !== 'profile' && k !== 'conceptTags');
  assert.deepEqual(need, pageCalls,
    `页面取的段与后端要喂的段不是同一批：页面 ${pageCalls.join(',')} ｜后端 ${backendKeys.join(',')}`);
  assert.ok(backendKeys.includes('conceptTags') && backendKeys.includes('profile'),
    'compose 里这两段还在，页面侧的回落/带入就得跟着在');
});

test('材料硬顶只有一处数字：后端常量必须等于这条 AI 通道的那个 8000', () => {
  const cap = /MATERIAL_CAP = (\d+)/.exec(py)?.[1];
  assert.ok(cap, '后端 MATERIAL_CAP 没扫到');
  assert.match(llm, /context\.length > 8000/, '这条通道自己写着 8,000 硬顶');
  assert.equal(Number(cap), 8000,
    '两个数必须一致：不一致时要么材料被后端削过头、要么 chatStream 直接抛而屏幕还在等结果');
});

test('那一卡的说明句跟着改口：现在是六张表，其中一张存 AI 正文', () => {
  assert.match(facts, /读本机六张表/, '五张表那句已经不实');
  assert.match(facts, /analytics\.stock_brief/, '要说清多出来的那张是什么');
  assert.match(facts, /<StockBriefBox facts=\{d\} refresh=\{refresh\} \/>/,
    '结论摆在这一格里（用户点名的位置），生成完靠 refresh 从库里读回来');
});
