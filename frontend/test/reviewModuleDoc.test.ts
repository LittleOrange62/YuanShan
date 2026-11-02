import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

/**
 * 「这块的说明」搬到标题行（用户 2026-10-31 两点裁定）—— 复盘类与其他页这一组的源码级闸。
 *
 * 钉的四件事，全部是"写错了不报错、屏幕上却出问题"那一类：
 *  ① 每一块自己写的说明出口只许挂在**标题这一行的最右边**那颗 `DocDrawer` 上（那一行若还有
 *     as-of/时间戳/档位徽标，按钮排在它们前面）；正文里不许再卧 ExplainBox／正文折框，
 *     否则同一张卡长出两颗同名按钮（10-29 定的一条一模块一颗）；
 *  ② 屏上句子不因搬运出现第二遍：搬走的那一段在原位置必须已经消失；
 *  ③ 该留在屏上的没跟着搬：NoticeBar、截断上限、warning 那几行、`caliber_note` 一支不少；
 *  ④ 说明文字全是本来就在屏上说的口径／后端 payload 原文（caliber／rule／definitions／
 *     caveats／calibers 这些字段），中文串里不嵌 ASCII 双引号、渲染文字不带 markdown 星号。
 * 判前一律剥注释（注释里的中文会让正则假绿，`lazySection.test.ts` 那条教训）。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, '');
const read = (rel: string) => strip(fs.readFileSync(path.join(HERE, rel), 'utf8'));

// ── ① 标题行那颗：每块文件都要能在 `<div className="…flex…">` 的标题行里找到 DocDrawer，
//    而正文里不许留第二颗同名出口 ─────────────────────────────────────────────
const TITLE_ROW = /<span className="ml-auto[^"]*">[\s\S]{0,120}<DocDrawer/;

const groups: string[] = [
  // 页：每屏自己的标题行块（QdCompare 的 Section 一处管四块，FirstBoard 三处、Journal 六处…）
  ['../src/pages/FirstBoard.tsx', []],
  ['../src/pages/QdCompare.tsx', []],
  ['../src/pages/Intel.tsx', []],
  ['../src/pages/Backtest.tsx', []],
  ['../src/pages/Journal.tsx', []],
  // 复盘组件
  ['../src/components/review/FirstBoardDeep.tsx', []],
  ['../src/components/review/LimitUpBoard.tsx', []],
  ['../src/components/review/LimitDownBoard.tsx', []],
  // 首页那几块（MarketPanorama 不在本组）与并排的两张卡
  ['../src/components/home/AbnormalMoveBoard.tsx', []],
  ['../src/components/home/SectorRotationBoard.tsx', []],
  ['../src/components/home/FirstBoardDailyPanel.tsx', []],
  ['../src/components/home/RiskSignalPanel.tsx', []],
  ['../src/components/EmotionMetricsPanel.tsx', []],
];

test('① 每一颗「这块的说明」都挂在标题行最右边，正文不留第二颗同名出口', () => {
  for (const [rel] of groups) {
    const comp = read(rel);
    assert.match(comp, /<DocDrawer\b/, `${rel} 找不到那颗说明按钮`);
    assert.match(comp, TITLE_ROW, `${rel}：DocDrawer 没挂在 ml-auto 的标题行右侧`);
    assert.doesNotMatch(comp, /<ExplainBox\b/, `${rel}：正文里还卧着 ExplainBox ⇒ 同一张卡两颗同名按钮`);
    // CaliberFold 只许 NationalTeamPanel 留（Panel note 那颗已在标题行，它必须走 viaDrawer=false 的摘要行）
    if (!rel.includes('NationalTeamPanel')) {
      assert.doesNotMatch(comp, /<CaliberFold\b/, `${rel}：正文里的 CaliberFold 会把同名按钮长回正文`);
    }
  }
});

test('①-b 同一张卡不许两颗同名：每个文件的 DocDrawer 调用点数量钉住（多一颗就是搬家时漏拆了旧的）', () => {
  const want: Record<string, number> = {
    '../src/pages/FirstBoard.tsx': 3,           // 两张类别榜（同一组件两处标题行共用一份 JSX）＋概念榜＋短线情绪大块
    '../src/pages/QdCompare.tsx': 1,            // Section 一处，四块跟着
    '../src/pages/Intel.tsx': 1,                // 一档 tab 一颗，切 tab 跟着换
    '../src/pages/Backtest.tsx': 2,             // 曲线卡 ＋ 策略卡（每卡一颗，共用 JSX）
    '../src/pages/Journal.tsx': 6,              // 我这套打法／是不是跑了／最近还在状态上吗／看错了还是没执行好／交易费率／自我体检
    '../src/components/review/FirstBoardDeep.tsx': 1,
    '../src/components/review/LimitUpBoard.tsx': 1,
    '../src/components/review/LimitDownBoard.tsx': 1,
    '../src/components/home/AbnormalMoveBoard.tsx': 1,
    '../src/components/home/SectorRotationBoard.tsx': 1,
    '../src/components/home/FirstBoardDailyPanel.tsx': 1,
    '../src/components/home/RiskSignalPanel.tsx': 1,
    '../src/components/EmotionMetricsPanel.tsx': 1,
  };
  for (const [rel, ] of groups) {
    const comp = read(rel);
    const got = (comp.match(/<DocDrawer\b/g) ?? []).length;
    assert.equal(got, want[rel], `${rel} 的说明按钮数不对：应 ${want[rel]} 实际 ${got}`);
  }
});

test('①-c 抽屉标题拿的是屏上那一行字：逐颗都显式交 title=（不许落到「这一屏」兜底）', () => {
  for (const [rel] of groups) {
    const comp = read(rel);
    const noTitle = [...comp.matchAll(/<DocDrawer(?![^>]*title=)[\s>]/g)];
    assert.deepEqual(noTitle, [], `${rel} 有 DocDrawer 没交 title ⇒ 弹窗只能念兜底的「这一屏」`);
  }
});

test('①-d 没有说明依据的块不许长出一颗空按钮：条件渲染钉住', () => {
  assert.match(read('../src/components/EmotionMetricsPanel.tsx'), /\{caliber && \(\s*<span className="ml-auto">\s*<DocDrawer/);
  assert.match(read('../src/pages/Intel.tsx'), /\{cur\.desc && <DocDrawer/);
  assert.match(read('../src/pages/Backtest.tsx'), /\{s\.desc && \(\s*<span className="ml-auto">\s*<DocDrawer/);
  // Allocator：六块 Panel 全走 note（标题行那颗），字段字典那块没有 calibers 就整块不渲染
  const alloc = read('../src/pages/Allocator.tsx');
  assert.match(alloc, /Object\.keys\(d0\?\.calibers \?\? \{\}\)\.length > 0 &&/);
  assert.equal((alloc.match(/<Panel\b/g) ?? []).length, 6, '分配者视角那六块 Panel 一块不少');
});

// ── ② 搬走的话不在屏上出现第二遍 / 内容来源没换 ────────────────────────────
test('② 句子逐字搬：抽屉里的话在原位置不再摊第二遍，来源仍是后端那一份', () => {
  const up = read('../src/components/review/LimitUpBoard.tsx');
  assert.equal(up.split('这一张表怎么点').length - 1, 1, '「怎么点」只剩抽屉里一处（标题行的「口径」芯片已并进来）');
  assert.match(up, /母体＝本地库 analytics\.limit_up_pool_daily/, '涨停母体那句还在抽屉里（原 Caliber 的内容）');
  const down = read('../src/components/review/LimitDownBoard.tsx');
  assert.match(down, /整列不出现（不是摆 — 占位）/, '跌停那句少三列的理由原样搬进抽屉');
  const deep = read('../src/components/review/FirstBoardDeep.tsx');
  assert.match(deep, /data\?\.definitions\?\.parent/, '母体那句仍读后端 definitions.parent，页面没复写');
  assert.match(deep, /｜资料说：\{b\.external\} ｜本机：\{b\.local\}/, '九格阈值对照逐字留在抽屉里');
  const abn = read('../src/components/home/AbnormalMoveBoard.tsx');
  assert.equal(abn.split('这三块为什么分开摆').length - 1, 1, '正文 ExplainBox 已拆，只剩抽屉那一处');
  assert.match(abn, /取数口径（后端原句，页面未改写）：\$\{am\.caliber\}/, '后端 caliber 原句仍是唯一来源');
  const fbd = read('../src/components/home/FirstBoardDailyPanel.tsx');
  assert.match(fbd, /d\.rule, d\.caliber,/, 'rule 与 caliber 两句进抽屉的「母体与读法」，没被重抄');
  assert.equal(fbd.split('这一块怎么读 · 表分哪两段 · 这一张表怎么点').length - 1, 1);
  const risk = read('../src/components/home/RiskSignalPanel.tsx');
  assert.equal(risk.split('转折点只打在平滑后的水位线上').length - 1, 1, '读法句只剩抽屉一处');
  const rot = read('../src/components/home/SectorRotationBoard.tsx');
  assert.match(rot, /data\.calibers\?\.value \?\? ""/, '口径串仍逐条读后端 calibers，没重抄');
  assert.equal(rot.split('排掉的是哪一批').length - 1, 1);
});

// ── ③ 该留屏上的没跟进抽屉 ──────────────────────────────────────────────────
test('③ 缺数声明／截断上限／告警留在屏上：NoticeBar 与 warning 那几行一支没跟着搬', () => {
  const page = read('../src/pages/FirstBoard.tsx');
  assert.match(page, /\{data\?\.caliber_note && <NoticeBar kind="warning">\{data\.caliber_note\}<\/NoticeBar>/);
  const card = page.slice(page.indexOf('function ConceptCard'));
  assert.match(card, /<NoticeBar kind="truncated">/, '概念榜的截断声明留屏');
  assert.match(card, /分母是页面上这一批（\{pool \|\| "—"\} 只首板）/, '两列不是一把尺那句误读提醒留屏');
  const up = read('../src/components/review/LimitUpBoard.tsx');
  assert.match(up, /data\?\.caliber_note && <NoticeBar kind="warning">/);
  assert.match(up, /勾选只筛这张表/, '全场/筛后分开那句留屏');
  const down = read('../src/components/review/LimitDownBoard.tsx');
  assert.match(down, /data\?\.caliber_note && <NoticeBar kind="warning">/);
  const bt = read('../src/pages/Backtest.tsx');
  assert.match(bt, /<Info className="mt-0\.5 h-3\.5 w-3\.5 shrink-0" \/> \{CAVEAT\}/, '事后样本那句误读提醒留屏');
  assert.match(bt, /样本偏小/, '样本偏小的档位徽标没排进按钮右边之后又被删');
  const risk = read('../src/components/home/RiskSignalPanel.tsx');
  assert.match(risk, /不预测、不构成投资建议/, '免责句留屏（10-31 那条点名它不进抽屉）');
  const fbd = read('../src/components/home/FirstBoardDailyPanel.tsx');
  assert.match(fbd, /plain\(truncationSentence\(rows\.length, d\)\)/, '截断声明留屏');
  assert.match(fbd, /x\.popHint && <NoticeBar kind="warning">/);
  const abn = read('../src/components/home/AbnormalMoveBoard.tsx');
  assert.match(abn, /ri\.popHint && <NoticeBar kind="warning">/);
  assert.match(abn, /\{truncationNote\(am\)/, '上限声明留屏');
});

// ── ④ 中文串与渲染文本的卫生（本轮搬动过的每一段都过一遍） ─────────────────
test('④ 搬进抽屉的文字：中文串不嵌 ASCII 双引号、渲染处不带 markdown 星号（都走「」与 plain()）', () => {
  const bt = read('../src/pages/Backtest.tsx');
  assert.match(bt, /所以「晚」同时意味着/, '曲线卡那句改用「」');
  assert.doesNotMatch(bt.slice(bt.indexOf('DocDrawer'), bt.indexOf('</DocDrawer>')), /"晚"/);
  const alloc = read('../src/pages/Allocator.tsx');
  assert.match(alloc, /蓝点 = 落在「收费站」那一格/, '③四象限搬进 note 时把 ASCII 引号换成「」');
  assert.doesNotMatch(alloc, /落在"收费站"/);
  const jr = read('../src/pages/Journal.tsx');
  assert.match(jr, /没有「按某一列排序」这回事/);
  const abn = read('../src/components/home/AbnormalMoveBoard.tsx');
  assert.match(abn, /在库里就是「指数那天有这根线、这只票没有日线行」那种形状/);
  // plain() 的位置：后端散文进抽屉前都剥过成对星号（源串里的 ** 只许死在 plain 的那一个出口之前）
  const em = read('../src/components/EmotionMetricsPanel.tsx');
  assert.match(em, /<p className="whitespace-pre-line leading-relaxed">\{plain\(caliber\)\}<\/p>/,
    'caliber 进抽屉只在这一个出口过 plain');
  const up2 = read('../src/components/review/LimitUpBoard.tsx');
  assert.match(up2, /<p className="whitespace-pre-line leading-relaxed">\{plain\(/,
    '母体那段含后端 caliber/seal_rule，过唯一一处 plain');
});
