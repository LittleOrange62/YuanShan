import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/**
 * 「系统配置与运维」那一组模块的说明按钮形状钉子（用户 2026-10-31 两点要求）：
 *  ① 每块「这块的说明」摆在该模块**标题这一行**的最右边，且排在时间戳／as-of／状态标记
 *    这类元信息**之前**（原先长在正文折框 ExplainBox 里，看起来像"数下面的一段话"）；
 *  ② 一模块一颗，同一张卡不许两颗同名；没有真说明就不许长出一颗空按钮；
 *  ③ 缺数声明／截断上限／告警与危险操作确认必须留在屏上，不许收进抽屉；
 *  ④ 搬进抽屉的句子原样带走，屏上不许出现第二遍。
 *
 * 判前一律剥注释（`docDrawer.test.ts` 同一条教训：注释里本来就要讲被禁的那件事，
 * 整份文本比会把解释本身当成违规）。
 */

const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8');

/** 剥 /* *\/ 与 // 注释（含 JSX 的 {/* *\/}）；行尾 `// …` 只剥"前面是空白"的那种，
 *  不碰 https:// 这类串里的斜杠。 */
function decomment(src: string): string {
  return src
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^[ \t]*\/\/.*$/gm, '')
    .replace(/[^\S\n]\/\/[^'"\n]*$/gm, '');
}

const countOf = (hay: string, needle: string) => hay.split(needle).length - 1;

const SYS = decomment(read('../src/pages/System.tsx'));
const JOBS = decomment(read('../src/pages/Jobs.tsx'));
const PIPE = decomment(read('../src/pages/Pipeline.tsx'));
const COV = decomment(read('../src/pages/Coverage.tsx'));
const QUAL = decomment(read('../src/pages/Quality.tsx'));
const DS = decomment(read('../src/pages/Datasource.tsx'));
const DP = decomment(read('../src/pages/DataProcess.tsx'));
const TDX = decomment(read('../src/pages/Tdx.tsx'));
const WATCH = decomment(read('../src/components/TdxWatchPanel.tsx'));
const WX = decomment(read('../src/components/WxPushPanel.tsx'));
const EARN = decomment(read('../src/components/ui/EarningsSnapshot.tsx'));
const UNLOCK = decomment(read('../src/components/UnlockCalendarPanel.tsx'));
const QUOTE = decomment(read('../src/components/QuoteLinksPanel.tsx'));
const LOOPS = decomment(read('../src/components/LoopSwitchboard.tsx'));

const ALL: [string, string][] = [
  ['System.tsx', SYS], ['Jobs.tsx', JOBS], ['Pipeline.tsx', PIPE], ['Coverage.tsx', COV],
  ['Quality.tsx', QUAL], ['Datasource.tsx', DS], ['DataProcess.tsx', DP], ['Tdx.tsx', TDX],
  ['TdxWatchPanel.tsx', WATCH], ['WxPushPanel.tsx', WX], ['EarningsSnapshot.tsx', EARN],
  ['UnlockCalendarPanel.tsx', UNLOCK], ['QuoteLinksPanel.tsx', QUOTE], ['LoopSwitchboard.tsx', LOOPS],
];

test('整组统一：这一组手写的卡不再有正文折框（ExplainBox 全部退场），一块至多一颗说明按钮', () => {
  for (const [name, src] of ALL) {
    assert.doesNotMatch(src, /<ExplainBox\b/, `${name}：说明还长在正文折框里，就是用户说的那句「像数下面的一段话」`);
    // 一模块一颗：这一组每个文件手写标题的卡数 ≤ DocDrawer 数 + Panel 数；这里钉上限（同一块不许两颗）
    const drawers = (src.match(/<DocDrawer\b/g) ?? []).length;
    const panels = (src.match(/<Panel\b/g) ?? []).length;
    const h2s = (src.match(/<h2\b/g) ?? []).length + (src.match(/<h3\b/g) ?? []).length;
    assert.ok(drawers + panels >= 1 || /<Panel\b/.test(src), `${name}：一块说明按钮都没有`);
    assert.ok(drawers <= h2s + h2s, `${name}：DocDrawer ${drawers} 颗对 ${h2s} 行标题，同一张卡长出两颗同名按钮`);
  }
});

test('System.tsx：能力接入自检与运维入口两颗按钮都在标题行最右，抽屉里的句子屏上只剩一遍', () => {
  assert.match(SYS, /<span className="ml-auto"><DocDrawer title="能力接入自检">/);
  assert.equal(countOf(SYS, '两半各答一件事'), 1, 'Measured 那句搬进抽屉后正文不许再摊一遍');
  assert.match(SYS, /不回答「数据对不对」/, '搬进来的中文串不许带 ASCII 双引号');
  assert.doesNotMatch(SYS, /不回答"[^"]*"/);
  assert.match(SYS, /<span className="ml-auto"><DocDrawer title="运维入口">/);
  assert.equal(countOf(SYS, '三条刻意没做入口的'), 1);
  // 自检按钮旁的操作指引（带「正在逐个探测…」实时状态）留在屏上
  assert.match(SYS, /正在逐个探测/);
  // 三块 Panel（服务身份／AI 引擎／环境体检）的 note 走共用出口，不许这一页自己再拼一颗按钮
  assert.equal(countOf(SYS, '<Panel title='), 3);
});

test('Jobs.tsx：循环读数的抽屉排在快照时间戳前；作业卡抽屉排在状态标签前；缺数与告警全留屏上', () => {
  assert.match(JOBS, /\{hasValue\(sched\?\.note\) && \(\s*<DocDrawer title="本进程宿主的循环读数">/);
  assert.ok(JOBS.indexOf('<DocDrawer title="本进程宿主的循环读数">') < JOBS.indexOf('快照生成于'),
    '按钮排在时间戳这类元信息前面');
  assert.equal(countOf(JOBS, '后端原话（不美化）'), 1, 'sched.note 那句屏上只许出现一遍（抽屉标题里）');
  // 作业卡：说明＝后端交出的 entry 行，排在状态标签（view.label）之前；没交就不长按钮
  assert.match(JOBS, /const docEntry = job\.available !== false && !unreadable && hasValue\(job\.entry\)/);
  assert.ok(JOBS.indexOf('<DocDrawer title={hasValue(job.name)') < JOBS.indexOf('{view.label}'),
    '作业卡的按钮排在状态标记前面');
  assert.equal(countOf(JOBS, '说明（仅供人读，不是可执行命令）'), 1);
  // 红线：会不会动到机器的确认与各类 Banner 全部留在屏上
  assert.match(JOBS, /确认手动触发/);
  assert.match(JOBS, /出网目标/);
  assert.match(JOBS, /总开关为关：本页所有/);
  assert.match(JOBS, /作业清单被截断/);
});

test('Pipeline.tsx：还没接上的两环——说明进抽屉、Contract 验收单留屏上', () => {
  assert.match(PIPE, /<span className="ml-auto"><DocDrawer title="还没接上的两环">/);
  assert.equal(countOf(PIPE, '这两环的数在库里'), 1);
  assert.ok(PIPE.indexOf('还没接上的两环') < PIPE.indexOf('<Contract path="GET /api/pipeline"'),
    'Contract（缺哪些字段的验收单）必须留在抽屉外面');
});

test('Coverage.tsx：列口径并进标题行那颗 Panel 抽屉；整卡缺数声明那一块不长按钮、一句都不搬', () => {
  assert.match(COV, /note="「应有 \/ 实有」.*行数为什么标「估」、判据为什么不用写入时间戳：行数标「估」的是 pg_class\.reltuples/,
    '列口径要在 Panel note 里（那颗按钮由共用组件摆到标题行）');
  assert.equal(countOf(COV, '不当精确条数用'), 1, '搬过之后正文不许再摊第二遍');
  // 判读告警与缺数声明留在表格读数旁边
  assert.match(COV, /算不出（不是零缺口）/);
  assert.match(COV, /覆盖极薄/);
  assert.match(COV, /1 分钟线只有 2026-09-22 一天/, '整卡都是缺数声明——按纪律留在屏上，不收抽屉');
  assert.match(COV, /<h2 className="text-sm font-bold">这些缺口会直接决定哪些页面永远是空的<\/h2>/);
});

test('Quality.tsx：分工与口径进标题行抽屉；「审 0 条是闸没落下」那句告警与 Contract 留屏上', () => {
  assert.match(QUAL, /<span className="ml-auto"><DocDrawer title="这一页拦的是什么，以及现在拦不动的部分">/);
  assert.equal(countOf(QUAL, '与 preflight 的分工'), 1);
  assert.match(QUAL, /审 0 条不是「货没问题」，是闸没落下/);
  assert.ok(QUAL.indexOf('</DocDrawer>') < QUAL.indexOf('<Contract path="POST /api/quality/audit"'),
    '缺触发端点那张 Contract 不许收进抽屉');
});

test('Datasource.tsx：三件事整段进抽屉且中文串不再嵌 ASCII 双引号；历史值告警留在 Panel 屏上', () => {
  assert.match(DS, /<span className="ml-auto"><DocDrawer title="这一屏还不能回答的三件事">/);
  assert.equal(countOf(DS, '能不能用</b>：要么当场重探'), 1);
  assert.match(DS, /「现在」能不能用/, '搬进来的句子把 ASCII 双引号换成「」');
  assert.doesNotMatch(DS, /"现在"能不能用/);
  assert.match(DS, /所以这一屏的 <code>health<\/code> 是<b className="text-foreground">历史值<\/b>/,
    '这一屏哪里不可信那句是告警，必须留在屏上');
});

test('DataProcess.tsx：列口径并进 Panel note；不摆重跑按钮的理由进抽屉，Contract 与跳转留屏上', () => {
  assert.match(DP, /note="只列派生层.*这一张表的列怎么读：行数标「估」的是 pg_class\.reltuples/);
  assert.doesNotMatch(DP, /不参与判据\。\s*<\/p>/, 'ExplainBox 搬走后正文不许残留半句');
  assert.match(DP, /<span className="ml-auto"><DocDrawer title="提交与重算：这一屏刻意没有的按钮">/);
  assert.equal(countOf(DP, '仍然不摆重跑按钮'), 1);
  assert.match(DP, /理由已经不是「没有端点」/, 'ASCII 双引号换「」');
  assert.ok(DP.indexOf('</DocDrawer>') < DP.indexOf('<Contract path="POST /api/data-process/run"'),
    '缺重跑端点的验收单留在屏上');
});

test('Tdx.tsx：自动动作边界那段人工实查进抽屉，出处声明跟着走；真开关不在这一块、没被收掉', () => {
  assert.match(TDX, /<span className="ml-auto"><DocDrawer title="自动动作的边界在哪">/);
  assert.ok(TDX.indexOf('<DocDrawer') < TDX.indexOf('<Measured>'), 'Measured 整段在抽屉里');
  assert.match(TDX, /<Measured>[\s\S]{0,40}?这一页没有「一键启动通达信」的按钮/);
  assert.equal(countOf(TDX, '绝不动主客户端进程'), 1);
  assert.match(TDX, /note="探测结果必须来自进程与窗口的实际状态/);
});

test('TdxWatchPanel.tsx：口径牌换成标题行那颗全站统一的说明按钮，排在 GET 出处标记前面', () => {
  assert.doesNotMatch(WATCH, /<Caliber\b/, '这块的说明改走全站那颗按钮，不留第二种形状');
  assert.match(WATCH, /<DocDrawer title="通达信客户端看护（只读探测）">/);
  assert.ok(WATCH.indexOf('<DocDrawer') < WATCH.indexOf('GET /api/tdx/watch'),
    '按钮排在出处标记前面');
  assert.equal(countOf(WATCH, '未探测到不等于不在'), 1);
  assert.match(WATCH, /<Contract path="GET \/api\/tdx\/watch"/, '契约留在屏上');
});

test('WxPushPanel.tsx：说明按钮在标题行、排在总开关/预检/计数元信息之前；三处必须留屏的都在', () => {
  const row = WX.slice(WX.indexOf('个人微信推送（在这台机器的桌面上按键盘）</h2>'), WX.indexOf('发送前的检查（只读'));
  assert.ok(row.indexOf('<DocDrawer') > 0, '标题行没有那颗按钮');
  assert.ok(row.indexOf('<DocDrawer') < row.indexOf('总开关开'), '按钮排在状态标记前面');
  assert.equal(countOf(WX, '这条通道是什么、为什么不敢做得更稳'), 1);
  // 「会不会真的动到机器」那一族：预检清单、拦住清单、两个发送按钮、警告行
  assert.match(WX, /发送前的检查（只读/);
  assert.match(WX, /真发这一条/);
  assert.match(WX, /没发 —— 这次我没把请求发出去/);
  assert.match(WX, /s\?\.warning &&/);
});

test('EarningsSnapshot.tsx：口径句进标题行抽屉、排在报告期时间戳前，屏上不再出现第二遍', () => {
  assert.match(EARN, /<DocDrawer title="财报速览">/);
  assert.ok(EARN.indexOf('<DocDrawer') < EARN.indexOf('{fin.period}'), '按钮排在报告期那枚时间戳前面');
  assert.equal(countOf(EARN, '不构成买卖建议'), 1);
});

test('UnlockCalendarPanel.tsx：来源与口径那句进标题行按钮；截断声明与取数失败提示留屏上', () => {
  assert.match(UNLOCK, /<span className="ml-auto"><DocDrawer title="限售解禁日历">/);
  assert.equal(countOf(UNLOCK, '来源：东方财富公开解禁计划'), 1);
  assert.match(UNLOCK, /仅覆盖前 500 条/);
  assert.match(UNLOCK, /解禁日历取数失败/);
  assert.match(UNLOCK, /slicePage\(/, '分页那套功能没被这轮搬家碰坏');
});

test('QuoteLinksPanel.tsx：口径进标题行按钮、排在占位符图例前；报错/警告行与每行按钮全留屏上', () => {
  assert.match(QUOTE, /<DocDrawer title="行情外链地址（按市场）">/);
  assert.ok(QUOTE.indexOf('<DocDrawer') < QUOTE.indexOf('= 股票代码'), '按钮排在图例这类元信息前面');
  assert.equal(countOf(QUOTE, '只改「去哪儿看行情」'), 1);
  assert.doesNotMatch(QUOTE, /只改"去哪儿看行情"/);
  assert.match(QUOTE, /⚠ \{s\.warnings\.map\(plain\)/, '配置警告行留在屏上');
  assert.match(QUOTE, /保存/);
  assert.match(QUOTE, /恢复出厂/);
});

test('LoopSwitchboard.tsx：整段说明（后端原句+命名表+步数提醒）进标题行按钮、排在「线程在 N 条」前', () => {
  assert.match(LOOPS, /<DocDrawer title="常驻循环与定时任务">/);
  assert.ok(LOOPS.indexOf('<DocDrawer') < LOOPS.indexOf('线程在 ${'), '按钮排在状态标记前面');
  assert.equal(countOf(LOOPS, '这一屏的说明（后端原句'), 1, '抽屉里一份，卡底不许残留第二份');
  assert.match(LOOPS, /plain\(s\.note ??/);
  // 逐条警示与操作全部在屏上：missing_loops、连败、⚠ warnings、开关本身
  assert.match(LOOPS, /进程自报里没有这几条线程/);
  assert.match(LOOPS, /连败 \{l\.consecutive_failures\} 次/);
  assert.match(LOOPS, /switchBadge/);
});
