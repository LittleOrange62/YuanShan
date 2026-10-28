import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { sortRows } from '../src/lib/useSort.ts';

/**
 * 倍量榜「加权分稳步提升」＋「倍量次改加权分」＋「全场名次」这三件事的钉子。
 *
 * ⚠️ 口径改于 2026-10-19 第二稿（本文件同日跟着改，不是为了让旧断言通过而放宽）：
 *  · 主列是 `dbl_energy`（加权分），算式换成 `得分 = Σ 每档 ( 这一档在窗口里出现的次数 × 这一档的分值 )`；
 *    第一稿那句「占比＝分值÷Σ分值」连同量纲 `sum`／`top` 一起被用户否掉
 *    （原话：其实不存在系数这个参数），所以旧断言里钉 0.2 与÷Σ 的那几句换成钉 1.5 与 scale.formula。
 *  · 「稳步提升」阈值 `STEP_GAP_MIN` 随换尺从 **0.2**（占比量纲下的数）改成 **1.5**（分的量纲，
 *    读作"比前一整窗多约 1.5 个顶档放量日"）；端点参数仍是 `float|None = Query(None, ge=-40, le=40)`。
 *  · 新增**全场名次**列 `rank_in_day`：排序键必须等于格子真读的字段，且两个方向都要真的换序。
 *  · 每页行数改读接口 `thresholds.page_size`（旧断言钉的 `const PAGE = 60` 换成钉这个）。
 *
 * 钉的是这几件最容易被写成假的事：
 *  ① 列排序键必须等于格子里真读到的字段（sortKey=sector 那一类空转只有点过才露）；
 *  ② 没有可比前窗的行渲染 —，绝不折成 0（0 的意思是「本窗比前窗一点没多」）；
 *  ③ 阈值只住后端 STEP_GAP_MIN 一处：界面读接口 thresholds.step_gap_min，
 *     「只看稳步提升」发的是它，不是页面自己写的数字；
 *  ④ 小数显示统一走去尾零那把尺（`lib/ranking.ts` 的 trimNum），不再出现 1.000000 那种念法；
 *  ⑤ 渲染文本里没有裸 `**`，中文串里不嵌 ASCII 双引号（项目红线，要引号用「」）。
 */

const src = readFileSync(new URL('../src/pages/VolumeDouble.tsx', import.meta.url), 'utf8');
const quantPy = readFileSync(new URL('../../duanxian/quant.py', import.meta.url), 'utf8');
const vrApp = readFileSync(new URL('../../vr/app.py', import.meta.url), 'utf8');

test('较前窗增减那一列：sortKey 就是格子里读的那个字段，小数走去尾零', () => {
  const table = src.slice(src.indexOf('<SortHeader label="代码"'), src.indexOf('</tbody>'));
  assert.match(table, /label="较前窗增减" sortKey="dbl_step_gap" dir=\{vdSort\.dirOf\("dbl_step_gap"\)\}/,
    '列头键与格子里印的值必须是同一个字段');
  assert.match(table, /r\.dbl_step_gap/, '格子里真的读它');
  assert.match(table, /title=\{d\.definitions\?\.dbl_step_gap/,
    '悬停逐字读接口 definitions 原文，组件不另写一句口径');
  // 键在行类型上登记过（缺字段＝点了不换序、aria-sort 照样亮，骗得过 DOM 骗不过点击）
  assert.match(src, /dbl_step_gap\?: number \| null;/);
  // 2026-10-19 第二稿：这一格是浮点分段差（两窗的加权分之差），显示层去尾零（8.37 不念成 8.3700）
  assert.match(table, /trimNum\(r\.dbl_step_gap\)/);
  assert.doesNotMatch(table, /num\(r\.dbl_step_gap/, '分段差不许再走 toFixed 那把旧尺');
  // 悬停那句算式跟着换尺：减的是前窗的**加权分**，不再是次数
  assert.match(table, /本窗加权分/);
  assert.match(table, /trimNum\(r\.dbl_energy\)/);
  assert.doesNotMatch(table, /本窗 \$\{dash\(r\.dbl_count\)\} 次/, '旧口径那句「次数相减」的悬停不许复活');
});

test('主列是加权分 dbl_energy，算式念接口 scale.formula（旧硬计数只进悬停）', () => {
  const table = src.slice(src.indexOf('<SortHeader label="代码"'), src.indexOf('</tbody>'));
  assert.match(table, /label="加权分" sortKey="dbl_energy" dir=\{vdSort\.dirOf\("dbl_energy"\)\}/,
    '列头键必须就是格子里读的那个字段（写成 dbl_count 会点了不换序）');
  assert.match(table, /r\.dbl_energy/, '格子里真的读它');
  assert.doesNotMatch(table, /sortKey="dbl_count"/, '退役的硬计数不许再当排序键');
  assert.doesNotMatch(table, /label="倍量次"/, '「倍量次」那一列 2026-10-19 起换成「加权分」');
  // 显示走去尾零那把尺（第二稿：分值就是几分，不念成 11.6100）
  assert.match(table, /trimNum\(r\.dbl_energy\)/);
  // 缺数＝—，不当 0 分
  assert.match(table, /r\.dbl_energy == null[\s\S]{0,320}<span className="text-muted-foreground\/50">—<\/span>/);
  assert.doesNotMatch(src, /dbl_energy\s*\?\?\s*0/, '?? 0 会把「这一场没分」说成「这 40 天一次量都没放过」');
  // 算式原文读接口，界面不抄第二份（第一稿那句÷Σ 也不许在界面上复活）
  assert.match(src, /d\.energy\?\.scale\?\.formula/, '列头与那一行念的都是 scale.formula');
  assert.doesNotMatch(src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, ''),
    /分值÷Σ|占比＝|除以|归一|normalize|量纲/, '界面里还留着第一稿那把÷Σ 的尺');
  // 旧口径只在悬停里作对照，并说明它是硬计数
  assert.match(table, /dbl_count_legacy/, '悬停要给出旧口径计数作对照');
  assert.match(table, /旧口径硬计数 dbl_count_legacy/);
  assert.match(table, /退役/);
  // 前端筛那一格也跟着换字段，并且不给 null 当 0
  assert.match(src, /\{ label: "加权分", type: "range", key: "dbl_energy", step: 0\.05 \}/);
  assert.match(src, /filters\.dbl_energy_min !== undefined && \(r\.dbl_energy \?\? -Infinity\)/);
});

test('NULL 渲染成 —，与真 0 长得不一样，且绝不 ?? 0', () => {
  const table = src.slice(src.indexOf('<SortHeader label="代码"'), src.indexOf('</tbody>'));
  assert.match(table, /r\.dbl_step_gap == null[\s\S]{0,320}<span className="text-muted-foreground\/50">—<\/span>/,
    '空的那格走灰化的 —，与数字列同款');
  assert.doesNotMatch(src, /dbl_step_gap\s*\?\?\s*0/, '?? 0 会把「没有可比前窗」说成「一次都没多」');
  assert.doesNotMatch(src, /dbl_step_gap\s*\|\|\s*0/, '同上，|| 0 连真 0 都会一起骗过去');
  // 排序层面 NULL 也永远排最后（两个方向都是），不被折进 0 那一档
  const rows = [{ dbl_step_gap: 0 }, { dbl_step_gap: null }, { dbl_step_gap: 1.5 }, { dbl_step_gap: undefined }];
  for (const dir of ['asc', 'desc'] as const) {
    const order = sortRows(rows, { key: 'dbl_step_gap', dir }).map(r => r.dbl_step_gap);
    assert.deepEqual(order.slice(0, 2), dir === 'asc' ? [0, 1.5] : [1.5, 0], `${dir}：有数的排前、方向只翻有数的部分`);
    assert.ok(order.slice(2).every(v => v === null || v === undefined), `${dir}：— 恒在尾部，不参与比大小`);
  }
  // 真 0 与 — 是两种读数：0 也要画得出（上面那格判的就是 0 排在有数那批的最前）
  assert.deepEqual(sortRows(rows, { key: 'dbl_step_gap', dir: 'asc' }).map(r => r.dbl_step_gap)[0], 0);
});

test('名次那一列：sortKey＝rank_in_day，两个方向真的换序，缺分恒在最后', () => {
  const table = src.slice(src.indexOf('<SortHeader label="代码"'), src.indexOf('</tbody>'));
  assert.match(table, /label="名次" sortKey="rank_in_day"/, '列头键必须等于格子读的字段');
  assert.match(src, /rank_in_day\?: number \| null;/, '行类型上得登记，否则点了不换序');
  const rows = [{ rank_in_day: 1 }, { rank_in_day: 40 }, { rank_in_day: null }, { rank_in_day: 3 }];
  assert.deepEqual(sortRows(rows, { key: 'rank_in_day', dir: 'asc' }).map(r => r.rank_in_day),
    [1, 3, 40, null], '升序＝名次数字小的在前（第 1 名在最上）；— 恒在最后');
  assert.deepEqual(sortRows(rows, { key: 'rank_in_day', dir: 'desc' }).map(r => r.rank_in_day),
    [40, 3, 1, null], '降序只翻有数的部分，不许把没分的顶到最前');
  // 规则原文与分母都读接口（实现只住 lib/ranking.ts，见 rankingShared.test.ts）
  assert.match(table, /d\.rank\?\.rule/);
  assert.match(table, /d\.rank\?\.universe/);
});

test('阈值只住一处：界面读 thresholds.step_gap_min＝1.5（第二稿的分），界面不发自己写的数字', () => {
  // ① 后端：STEP_GAP_MIN = 1.5 只出现一次定义，SQL 里的下限是 %s＋参数
  //    （2026-10-19 第二稿：÷Σ 那把尺作废之后，0.2 这个"占比量纲"的数跟着作废，换成 1.5 分）
  const defs = quantPy.match(/^STEP_GAP_MIN = 1\.5$/gm) ?? [];
  assert.equal(defs.length, 1, 'STEP_GAP_MIN 的定义只准一处');
  assert.equal((quantPy.match(/^STEP_GAP_MIN = 2$/gm) ?? []).length, 0, '旧的整数阈值不许复活');
  assert.equal((quantPy.match(/^STEP_GAP_MIN = 0\.2$/gm) ?? []).length, 0, '第一稿那个 0.2 已经随÷Σ 一起作废');
  assert.match(quantPy, /dbl_step_gap >= %s/, '筛参是占位符，不是拼死的字面量');
  assert.match(quantPy, /'step_gap_min': STEP_GAP_MIN/, 'thresholds 交在常量上');
  assert.match(quantPy, /lag\(v0\.dbl_energy, coalesce\(v0\.win_days, 40\)\)/,
    '前一窗按行偏移取（lag 偏移 = win_days 行 ⇒ 两窗不重叠；2026-10-19 起滑的是 dbl_energy 不是 dbl_count）');
  // ② 界面：勾上的值来自接口，不发页面自己写的数字
  assert.match(src, /setStepGapMin\(e\.target\.checked \? \(d\.thresholds\?\.step_gap_min \?\? null\) : null\)/);
  assert.doesNotMatch(src, /step_gap_min=\$\{2\}|step_gap_min=2|step_gap_min=0\.2|step_gap_min=1\.5/,
    'URL 里出现字面量＝页面自己抄了阈值（1.5 现在是后端的数，界面只转述）');
  assert.doesNotMatch(src, /增减 ≥\{?2\b/, '上屏文案里写死「≥2」＝没读接口');
  assert.match(src, /&step_gap_min=\$\{stepGapMin\}/, '筛参进了取数 URL（后端拼在 limit 之前，见 vr/app.py）');
  assert.match(src, /const \[stepGapMin, setStepGapMin\] = useState<number \| null>\(null\)/,
    '默认不筛：不给参数就不进 URL');
  assert.match(src, /d\.filter\?\.step_gap_min != null &&/, '回显读后端真应用的值，不读界面 state');
  assert.match(src, /只看稳步提升（较前窗增减 ≥\{Number\.isFinite\(d\.thresholds\?\.step_gap_min \?\? NaN\)/,
    '那一格旁边的数字与 checkbox 用的是同一个读数（读写不一致就是两把尺）');
  // ③ 端点边界：给了才筛、不给不筛；分段差可正可负 ⇒ float 且 ge=-40
  assert.match(vrApp, /step_gap_min: float \| None = Query\(None, ge=-40, le=40/, '端点参数边界要与界面一致');
  assert.match(vrApp, /step_gap_min=step_gap_min/, '端点真的把参数传给取数函数，不是挂着不用');
  // ④ 全场只数与「没读到」：null 不折成 0（与象限计数同一条规矩）
  assert.match(src, /typeof d\.step_gap_pool\?\.hits === "number"/, '聚合没读到时不许说 0 只');
  assert.match(src, /不拿 0 顶/);
});

test('取数参数改名 min_energy（浮点加权分），min_count 与第一稿的 min_energy_default 都退场', () => {
  // ① URL 里的参数名必须是 min_energy，且发的是浮点 state。
  //    （注释里留一句「2026-10-19 前这一格是 min_count」是历史坐标，不算残留 —— 这里钉的是**发出去的东西**）
  assert.match(src, /&min_energy=\$\{minEnergy\}/, '取数参数名要与端点一致');
  assert.doesNotMatch(src, /[&?]min_count=|min_dbl_count/, '旧参数名与旧回显字段不许还在请求或读数里');
  assert.match(src, /const \[minEnergy, setMinEnergy\] = useState\(0\)/);
  assert.match(src, /Math\.min\(40, Math\.max\(0, n\)\)/, '越界在界面夹紧，不发注定 422 的请求');
  // ② 标签与那句"单位是分不是次数"（第二稿：顶档一天记 1 分，所以单位就是分）
  assert.match(src, /最少加权分：/, '那一格的名字改了');
  assert.doesNotMatch(src, /最少倍量次：/, '旧名字不许复活（它说的单位已经不成立）');
  assert.match(src, /单位是分、不是次数，满窗上限见 <code>energy\.scale\.max_score<\/code>/);
  assert.match(src, /trimNum\(hits\.kind === "ok" \? hits\.data\.energy\?\.scale\?\.max_score : null\)/,
    '上限读接口那份并走去尾零（1.000000 那种念法就是这一格漏的）');
  assert.doesNotMatch(src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, ''),
    /min_energy_default/, '第一稿那个出厂门槛默认值已经从接口里删掉了（注释里作为历史坐标提一句不算残留）');
  // ③ 端点那侧同族：float、ge=0、le=40
  assert.match(vrApp, /min_energy: float = Query\(0\.0, ge=0, le=40/, '端点参数边界要与界面一致');
  assert.match(vrApp, /min_energy=min_energy/, '端点真的把参数传给取数函数');
  assert.doesNotMatch(vrApp, /min_count: |min_count=/, '端点签名与调用点上都不再有旧参数（界面发它就是一句 422）');
  // ④ 回显读后端应用的值
  assert.match(src, /d\.filter\?\.min_energy/, '榜尾那句「满足…全场实有几只」用的是回显的 min_energy');
  // ⑤ 函数签名与调用点一致
  assert.match(quantPy, /def volume_double\(date: str = '', limit: int = 150, min_energy: float \| None = None/);
  assert.doesNotMatch(quantPy, /def volume_double\([^)]*min_count/, '取数函数也不再收旧参数');
});

test('每页行数读接口 thresholds.page_size，页脚那两个数也跟着它', () => {
  assert.match(src, /const PAGE = Number\.isFinite\(d\.thresholds\?\.page_size \?\? NaN\)/,
    '第二稿起 page_size 随体交出：每页行数读接口，不写死 60');
  assert.match(src, /const PAGE_FALLBACK = 60/, '兜底值要有名字，且只在接口没交时用');
  assert.match(src, /thresholds\?: \{ step_gap_min\?: number \| null; top_n_default\?: number \| null;/,
    'thresholds 那格的形状跟着第二稿（没有 min_energy_default）');
  assert.match(src, /每页 \{PAGE\} 行，现在是第 \{cur\}\/\{pages\} 页/);
  assert.match(src, /第 2 页仍是这一批里的第 \{PAGE \+ 1\}~\{Math\.min\(sorted\.length, 2 \* PAGE\)\} 名/,
    '页脚那句"第 61~120 名"以前是写死的，现在跟着每页行数算');
  assert.doesNotMatch(src, /第 61~120 名/);
});

test('清除筛选一次清掉三类（本地 filters、象限、稳步提升）', () => {
  const clears = src.match(/setFiltersState\(\{\}\); setQuadrants\(\[\]\); setStepGapMin\(null\);/g) ?? [];
  assert.equal(clears.length, 2, '两处「清除筛选」（按钮与面板）都要一起清，清一半就是"屏上显示清了其实还在筛"');
});

test('渲染文本里没有裸 **（中文串也不嵌 ASCII 双引号）', () => {
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');
  const code = strip(src);
  const hits = code.match(/[^\s]\*\*|\*\*[^\s]/g) ?? [];
  assert.deepEqual(hits, [], `这些地方有裸的 **，会原样显示：${hits.join(' ')}`);
  assert.doesNotMatch(code, /[\u4e00-\u9fff]"[\u4e00-\u9fff]/,
    '中文句子中间出现了 ASCII 双引号（要用「」）');
  for (const b of code.match(/>([^<>{}]*[\u4e00-\u9fff][^<>{}]*)</g) ?? []) {
    assert.doesNotMatch(b, /"/, `JSX 文本节点里出现了 ASCII 双引号：${b}`);
  }
});
