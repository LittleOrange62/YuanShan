import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  cohortLabel, coverageSentence, definitionOf, equivText, fmtDays, gainText, groupReading,
  launchRange, moodText, moodTone, nameIsCodeCount, numOrNull, ratioExtent, ratioSegments, ratioText,
  rowCountLine, windowShort,
} from '../src/lib/startupVolume.ts';

/**
 * 「启动前量能分布」判读层 + 组件接线。
 * 每条断言都对应一句"屏幕会说谎"的情形，不是格式洁癖。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const comp = fs.readFileSync(path.join(HERE, '../src/components/quant/StartupVolumeBoard.tsx'), 'utf8');
const helper = fs.readFileSync(path.join(HERE, '../src/lib/startupVolume.ts'), 'utf8');
/** 交叉高亮的状态现在住在共用那两份里（组合块 + 低一层那份 hook），断言跟着指到这儿 */
const highlight = fs.readFileSync(path.join(HERE, '../src/lib/useTableInteraction.ts'), 'utf8');

// ---------------------------------------------------------------- 纯判读层

test('窗口不满只在两个数都给准时才判定，缺数不默认成满窗', () => {
  assert.equal(windowShort({ code: 'x', bars_used: 10, window_days: 30 }), true);
  assert.equal(windowShort({ code: 'x', bars_used: 30, window_days: 30 }), false);
  assert.equal(windowShort({ code: 'x', bars_used: null, window_days: 30 }), null);
  assert.equal(windowShort({ code: 'x', bars_used: 30 }), null, '没交 window_days 时说"不知道"，不说"满窗"');
});

test('0 是有效值：涨幅 0%、倍数 0、持平 0 天都不许吞成 —', () => {
  assert.equal(gainText(0), '0.00%');
  assert.equal(ratioText(0), '0.000');
  assert.equal(equivText(0), '0.00');
  // 真没给值才是 —
  assert.equal(gainText(null), '—');
  assert.equal(ratioText(undefined), '—');
  assert.equal(equivText(NaN), '—', 'NaN 出口不许印成字面量 NaN');
});

test('trigger_gain 是小数读数，按百分比讲但不换算别的量纲', () => {
  assert.equal(gainText(0.4648), '46.48%');
  assert.equal(gainText(0.4379), '43.79%');
});

/** 这条是最坏的一种造假：把"那天没有有效日线"画成"那天的量是 0"，形状图立刻多一根假尖峰。 */
test('ratios 里的 null 断线，不补 0、不连线', () => {
  const seq = [{ v: 1.2 }, { v: null }, { v: null }, { v: 0.8 }, { v: 2.4 }];
  assert.deepEqual(ratioSegments(seq), [{ from: 0, to: 0 }, { from: 3, to: 4 }]);
  assert.deepEqual(ratioExtent(seq), { lo: 0.8, hi: 2.4 }, '取值范围只算有值的点');
  assert.equal(ratioExtent([{ v: null }, { v: null }]), null, '全空时不给范围 —— 组件据此画"无序列"而不是 0 线');
  assert.deepEqual(ratioSegments([{ v: 1 }]), [{ from: 0, to: 0 }]);
  assert.deepEqual(ratioSegments(null), []);
});

test('母体说明缺失时说出来，不顶一句编的', () => {
  assert.equal(cohortLabel('board4', '近 3 个月出现过 4 板及以上的票'), '近 3 个月出现过 4 板及以上的票');
  assert.match(cohortLabel('board4', '   '), /后端没交/);
});

test('交出行数与母体总数分开说，被截断时不许读成全部', () => {
  assert.equal(rowCountLine({ rows_returned: 85, rows_total: 85, limit: 200 }), '交出 85 行 / 母体共 85 行');
  assert.match(rowCountLine({ rows_returned: 200, rows_total: 340, limit: 200 }), /榜尾没在这里[\s\S]*limit=200[\s\S]*别读成"全部"/);
  assert.match(rowCountLine({ rows_returned: 10, rows_total: null }), /母体总数未交出/);
  assert.match(rowCountLine({}), /交出行数未交出/);
});

test('numOrNull 只认有限数值', () => {
  assert.equal(numOrNull('1.5'), null, '字符串不做强真转换 —— 那是后端该给的类型');
  assert.equal(numOrNull(Infinity), null);
  assert.equal(numOrNull(0), 0);
});

// ---------------------------------------------------------------- 组件接线

/** 死键（sortKey 在行上取不到值）不报错、不换序，aria-sort 却会变 —— 只能按字段名逐条核对。 */
/**
 * 覆盖行。sv-2 起后端交出 `window_from` 与 `profile_earliest_launch`，所以这一行要说准话；
 * 老批没这两个字段时必须退回"分不出"，不许假装知道库里算到哪一天。
 */
const RNG = { from: '2026-06-29', to: '2026-09-24', days: 47 };

test('后端给了窗口起点与表内最早行时，说出差在哪段、并指向补算', () => {
  const line = coverageSentence(RNG, { window_from: '2025-09-24', profile_earliest_launch: '2026-01-27',
    rows_returned: 355, rows_total: 355 });
  assert.match(line, /2026-06-29 ~ 2026-09-24，共 47 个启动日/);
  assert.match(line, /这一档窗口从 2025-09-24 起/);
  assert.match(line, /判据表没算到/);
  assert.match(line, /2025-09-24 ~ 2026/, '要点名缺的是哪一段，不能只说"可能没算"');
});

test('窗口整段都在覆盖内时不喊缺口', () => {
  const line = coverageSentence(RNG, { window_from: '2026-06-25', profile_earliest_launch: '2025-09-24',
    rows_returned: 82, rows_total: 82 });
  assert.match(line, /整段都在判据表的覆盖之内/);
  assert.doesNotMatch(line, /没算到/);
});

test('老批没给这两个字段时退回"分不出"，不硬编一个覆盖范围', () => {
  const line = coverageSentence(RNG, { rows_returned: 85, rows_total: 85 });
  assert.match(line, /分不出/);
  assert.doesNotMatch(line, /窗口从/, '没有出处就不许出现"窗口从 X 起"这种断言');
  // 0 行时不许编一个"覆盖到某天"
  assert.equal(coverageSentence(null, { window_from: '2026-06-25', profile_earliest_launch: '2025-09-24' }),
    '交出 0 行，没有启动日可说；这一档窗口从 2026-06-25 起，判据表最早算到 2025-09-24。 '
    + '窗口整段都在判据表的覆盖之内，母体行数的多少可以当成这类票的疏密来读。');
});

test('被 limit 截断时，范围与母体总数同时说清（有覆盖字段的那一支也要带这句）', () => {
  const cut = coverageSentence(RNG, { window_from: '2025-09-30', profile_earliest_launch: '2025-09-23',
    rows_returned: 200, rows_total: 627 });
  assert.match(cut, /榜被上限截到 200 行，这个范围只代表已交出的那一段/);
  assert.doesNotMatch(cut, /母体共 627 行/, '母体总数上一行已经报过，这里不再重复一遍');
  const cutOld = coverageSentence(RNG, { rows_returned: 200, rows_total: 627 });
  assert.match(cutOld, /榜被上限截到 200 行/);
  const whole = coverageSentence(RNG, { window_from: '2026-06-25', profile_earliest_launch: '2025-09-24',
    rows_returned: 82, rows_total: 82 });
  assert.doesNotMatch(whole, /截到/, '没截断就不许出现截断那句');
  assert.match(whole, /疏密来读/);
});

test('读侧改名后的列解释要按别名回查（code ← security_code）', () => {
  const defs = { security_code: '库里的证券代码', name: '名称' };
  assert.equal(definitionOf(defs, 'code'), '库里的证券代码');
  assert.equal(definitionOf(defs, 'name'), '名称');
  assert.equal(definitionOf(defs, 'ratio_avg'), undefined, '没有条目就说没有，不拼一句近似的话');
  assert.equal(definitionOf(null, 'code'), undefined);
});

test('每个 SortHeader 的 sortKey 都是行上真有的字段名', () => {
  const keys = [...comp.matchAll(/Th\("([^"]+)",\s*"([^"]+)"\)/g)].map(m => ({ label: m[1], key: m[2] }));
  assert.ok(keys.length >= 20, `应扫到 20 个以上列头，实际 ${keys.length} —— 式样变了要去改测试`);
  const block = helper.slice(helper.indexOf('export interface StartupRow'), helper.indexOf('\n}', helper.indexOf('export interface StartupRow')));
  const fields = [...block.matchAll(/([a-z_0-9]+)\??:/g)].map(m => m[1]);
  assert.ok(fields.includes('code') && fields.length >= 25, `StartupRow 字段没扫全（${fields.length} 个）`);
  const dead = keys.filter(k => !fields.includes(k.key));
  assert.deepEqual(dead, [], '这些列头排序键在 StartupRow 上不存在，点上去不会换序');
  assert.equal(new Set(keys.map(k => k.key)).size, keys.length, '同一个 sortKey 不该被两列共用');
});

/** 页面里绝不写 /api：前缀由 lib/probe.ts 补，写了就变成 /api/api。 */
test('取数路径不带 /api 前缀', () => {
  assert.match(comp, /useProbe<StartupPayload>\("\/quant\/startup-volume"/);
  assert.doesNotMatch(comp, /["'`]\/api\//, '组件里出现了 /api/ 前缀');
});

test('母体是切换项，不是合并榜', () => {
  assert.match(comp, /setCohort/, '要有 cohort 切换');
  assert.match(comp, /\{\s*cohort,\s*months,\s*limit:\s*200\s*\}/, '一次只按一个 cohort 取数');
  // 只发一次请求：第二个 useProbe 就意味着两批行可能被拼到一起
  assert.equal((comp.match(/useProbe</g) ?? []).length, 1);
  assert.doesNotMatch(comp, /rows[^\n]*\.concat\(|\.\.\.a\s*,\s*\.\.\.b/, '两批行不许拼');
});

test('档位存疑那条摆在表前，其余 caveat 原样列出', () => {
  assert.match(comp, /c\.code === "limit_band"/, 'limit_band 要单独挑出来摆在表前');
  // 用户 2026-10-21 要密度之后，其余 caveat 走共用折叠块：缺数/截断类仍渲染在 `<details>` 之前
  assert.match(comp, /<CaliberFold caveats=/, '其余 caveats 走共用出口（不是被折掉）');
});

test('字段含义从 definitions 读，没有的明说没出处', () => {
  assert.match(comp, /definitionOf\(d\.definitions, k\)/);
  assert.match(comp, /后端 definitions 里没有/, 'definitions 缺条目时要说明没有出处，不自己编');
  assert.match(comp, /definitions=\{d\.definitions\}/,
    '字段义交给 CaliberFold（它内部用的就是同一个 Definitions，不再摊四行）');
});

test('窗口不满的行标出来，且给出不可并排排名的原话', () => {
  assert.match(comp, /windowShort\(r\) === true/);
  assert.match(comp, /不可与满窗票并排排名/);
});

test('股票名走 StockLink（新标签、认不出的代码退回纯文本）', () => {
  assert.match(comp, /<StockLink code=\{r\.code\} name=\{r\.name\}/);
});

test('月份与窗口天数不写死在句子里', () => {
  assert.match(comp, /dash\(d\.window_days\)/);
  assert.match(comp, /dash\(d\.months\)/);
  assert.match(comp, /dash\(d\.latest_launch\)/);
  // 回溯月份作为切换项可以有默认值，但句子里出现的数必须来自接口
  assert.doesNotMatch(comp, /近 30 根|30 根有效日线/, '窗口根数不该被写死进文案');
});

// ---------------------------------------------------------------- 整组汇总（后端 summary）

test('整组结论三种翻向都从 summary 来，缺数不站任何一边', () => {
  const below = groupReading({ n: 82, med_of_median: 0.9642, mean_dbl_days: 1.73 }, 30);
  assert.equal(below.tone, 'warn');
  assert.match(below.text, /启动前 30 根有效日线/);
  assert.match(below.text, /「启动前普遍放量」这个直觉在本机这批票上不成立/);
  assert.match(below.text, /放量没用/, '同时要拦住反向结论');
  assert.doesNotMatch(below.text, /\*\*/, '屏幕句子不带 markdown');

  const above = groupReading({ n: 10, med_of_median: 1.21, mean_dbl_days: 4.5 }, 30);
  assert.equal(above.tone, 'flat');
  assert.match(above.text, /高于 1/);
  assert.doesNotMatch(above.text, /不成立/);

  const missing = groupReading({ n: 10, med_of_median: null, mean_dbl_days: 1.73 }, 30);
  assert.equal(missing.tone, 'unknown');
  assert.match(missing.text, /不猜它们启动前是放量还是缩量/);
  assert.equal(groupReading(null, null).tone, 'unknown');

  // 0 是有效值：整组真的一个倍量天都没有时，不许被当成"没给数"
  const zero = groupReading({ n: 10, med_of_median: 0.9, mean_dbl_days: 0 }, 30);
  assert.match(zero.text, /平均只有 0 天出现倍量/);
  assert.match(zero.text, /这 10 只票/);
});

test('天数读数不四舍五入、空给 —', () => {
  assert.equal(fmtDays(1.73), '1.73');
  assert.equal(fmtDays(0), '0');
  assert.equal(fmtDays(null), '—');
});

test('整组汇总接在组件里：读 summary、缺 summary 时说出来、不自己按行算', () => {
  assert.match(comp, /groupReading\(d\.summary, numOrNull\(d\.window_days\)\)/,
    '整组结论必须走判读层，不在组件里现场拼');
  assert.match(comp, /Object\.entries\(d\.summary\)/, '原键原值列出，页面不自编标签');
  assert.match(comp, /后端这次没交 summary/, '缺 summary 时要明说，不许按行凑一个');
  // 行是被 limit 截断过的：页面自己"归并"出来的不是母体，这条把这条路堵死
  assert.doesNotMatch(comp, /reduce\s*\(/, '组件里出现了自己归并/求均值的影子');
});

test('标题不预设"放量启动"，也不反过来宣称放量没用', () => {
  assert.match(comp, /启动前量能分布/, '标题按用户 2026-10-07 定的名字');
  assert.doesNotMatch(comp, /放量启动|启动特征/);
});

test('名称列里"名字就是代码"的行数得数出来（读者会以为页面截断了名字）', () => {
  assert.deepEqual(nameIsCodeCount([{ code: '920305.BJ', name: '920305.BJ' }, { code: '301560.SZ', name: '众捷汽车' }]),
    ['920305.BJ']);
  assert.deepEqual(nameIsCodeCount([{ code: '301560.SZ', name: '' }]), ['301560.SZ'], '空名字同样算"没有名字"');
  assert.deepEqual(nameIsCodeCount([{ code: '301560.SZ', name: ' 301560.sz ' }]), ['301560.SZ'], '大小写与空格不算两个名字');
  assert.deepEqual(nameIsCodeCount([{ code: '000678.SZ' }]), ['000678.SZ']);
  assert.match(comp, /本地股票字典里没登记它的中文名/, '点名之后要说清不是界面截断');
});

// --------------------------------------------- definitions 补齐（后端 34/34）后的回归守卫

/** 库里实际交出的 definitions 键（2026-10-15 从 /api/quant/startup-volume 重新落盘，不是手抄。
 *  这一轮新增：`gain_first4`（第三类母体那道门槛的读数）与 `mood_score`/`mood_breadth`/
 *  `mood_theme`/`mood_parts`（启动日那天的情绪列，依据要能在列名悬停里读到）） */
const LIVE_DEFS: string[] = JSON.parse(fs.readFileSync(path.join(HERE, 'startupVolume.defs.json'), 'utf8'));

test('界面上 26 个列头全部查得到 definitions 出处（补齐之后不许退回兜底句）', () => {
  const keys = [...comp.matchAll(/Th\("([^"]+)",\s*"([^"]+)"\)/g)].map(m => m[2]);
  assert.equal(keys.length, 26, `列头应有 26 个，实际 ${keys.length}`);
  const defs = Object.fromEntries(LIVE_DEFS.map(k => [k, `${k} 的解释`])) as Record<string, string>;
  const missing = keys.filter(k => definitionOf(defs, k) === undefined);
  assert.deepEqual(missing, [], '这些列在后端 definitions 里查不到出处（改名要带上 DEFINITION_ALIAS 一起对）');
  // 兜底分支留着当异常路径，但这次不该被任何一列走到
  assert.match(comp, /后端 definitions 里没有/);
});

// --------------------------------------------- 情绪列与第三类母体（用户 2026-10-15）

test('情绪列排在启动日之后，且缺值显式区别于 0', () => {
  const head = comp.slice(comp.indexOf('{Th("代码"'), comp.indexOf('{Th("连板数"'));
  assert.ok(head.includes('{Th("启动日", "launch_date")}') && head.includes('{Th("情绪", "mood_score")}'),
    '情绪那一格必须紧跟启动日（用户点名的位置）');
  assert.equal(moodText(null), "—");
  assert.equal(moodText(undefined), "—");
  assert.equal(moodText(0), "0", '0 是「中性」这个有效读数，不许吞成 —');
  assert.equal(moodText(42), "+42");
  assert.equal(moodText(-17), "-17");
  assert.equal(moodTone(null), "none", '着色也要把"没读到"与"很冷"分开');
  assert.equal(moodTone(-60), "cold");
  assert.equal(moodTone(80), "hot");
  assert.equal(moodTone(10), "flat");
});

test('第三类母体是一个独立切换项，且文字说它是 board4 的子集', () => {
  assert.match(comp, /key: "board4_45"/);
  assert.match(comp, /key: "board4_or_gain45"/, '第四类母体（或）必须是独立切换项');
  assert.match(helper, /export type Cohort = "board4" \| "board4_45" \| "board4_or_gain45" \| "jump40"/);
  // 界面不许把 45% 这个门槛自己写死一遍（判据在后端，屏幕只读 cohort_text 与那一列读数）
  assert.doesNotMatch(comp.slice(comp.indexOf('const COHORT'), comp.indexOf('export function')), /45%.*门槛/,
    '按钮标签里可以写"前 4 日涨幅>45%"作为人名提示，但界面不许拿这个数去筛或去算');
});

test('profile_rows_total 进了覆盖行（"这一档库里共 N 行"），缺席时不硬造', () => {
  const line = coverageSentence({ from: '2026-06-29', to: '2026-09-24', days: 47 },
    { window_from: '2026-06-24', profile_earliest_launch: '2025-09-24', profile_rows_total: 356,
      rows_returned: 82, rows_total: 82 });
  assert.match(line, /这一档库里共 356 行/);
  assert.doesNotMatch(line, /库里共 null|库里共 undefined/);
  const noPool = coverageSentence({ from: '2026-06-29', to: '2026-09-24', days: 47 },
    { window_from: '2026-06-24', profile_earliest_launch: '2025-09-24', rows_returned: 82, rows_total: 82 });
  assert.doesNotMatch(noPool, /库里共/);
  assert.match(noPool, /窗口整段都在判据表的覆盖之内/);
});

test('窗口深于覆盖时说"判据表没算到"并给出缺的那一段（profile_window 的界面一侧）', () => {
  const line = coverageSentence({ from: '2026-01-05', to: '2026-09-24', days: 9 },
    { window_from: '2025-10-08', profile_earliest_launch: '2026-01-05', rows_returned: 30, rows_total: 30 });
  assert.match(line, /2025-10-08 ~ 2026-01-05 那一段是判据表没算到/);
  assert.match(line, /补算出来/);
  assert.doesNotMatch(line, /疏密来读/, '有缺口时不能说"整段都在覆盖内"');
});

test('"分布列为空是口径"那句只在后端给了 min_bars 时才引用', () => {
  assert.match(comp, /c\.code === "min_bars"/);
  assert.match(comp, /\{hasMinBars &&/, '条件渲染，不是无条件替后端说话');
});

// --------------------------------------------- 倍量区间统计（用户 2026-10-11）

test('StartupRow 包含 5 个倍量区间统计字段', () => {
  const block = helper.slice(helper.indexOf('export interface StartupRow'), helper.indexOf('\n}', helper.indexOf('export interface StartupRow')));
  const fields = [...block.matchAll(/([a-z_0-9]+)\??:/g)].map(m => m[1]);
  const rangeFields = ['range_16_18', 'range_14_16', 'range_12_14', 'range_10_12', 'range_08_10'];
  const missing = rangeFields.filter(f => !fields.includes(f));
  assert.deepEqual(missing, [], `这 5 个区间字段在 StartupRow 里不存在：${missing.join(', ')}`);
});

test('组件渲染了 5 个倍量区间列（表头 + 数据格）', () => {
  // 表头应该有 5 个带区间的 th（第一个的 title 有额外说明）
  const headers = [...comp.matchAll(/title="(\d+\.\d+~\d+\.\d+)/g)].map(m => m[1]);
  assert.equal(headers.length, 5, `应有 5 个区间列表头，实际 ${headers.length}：${headers.join(', ')}`);
  // 数据格应该绑定对应的字段
  const bindings = [...comp.matchAll(/\{dash\(r\.(range_\d+_\d+)\)\}/g)].map(m => m[1]);
  assert.equal(bindings.length, 5, `应有 5 个区间数据格绑定，实际 ${bindings.length}：${bindings.join(', ')}`);
  assert.deepEqual(bindings.sort(), ['range_08_10', 'range_10_12', 'range_12_14', 'range_14_16', 'range_16_18'].sort());
});

test('选中单元格时高亮整行和整列', () => {
  // 10-15 用户点名「单击高亮行和列 这个应该是全软件通用的一块」⇒ 这一屏原来内联那份
  // `selectedCell` state + 本地 `cellCls(...)` 已收进共用 hook。**六条断言一条没删，只是指到状态现在住的那个文件**：
  // 组件必须引共用 hook 并把行/列键交出去，深浅两档高亮仍在共用实现里。
  assert.match(comp, /import \{ STOCK_ROW_HINT, useStockRowInteraction \} from "@\/lib\/useStockRowInteraction"/);
  assert.match(comp, /const x = useStockRowInteraction\("启动前量能画像"\)/, '必须用共用那一份，不许页面另造状态');
  assert.match(comp, /x\.rowProps\(i, r\.code/, '整行的高亮与双击由共用块交出');
  assert.match(comp, /x\.cellProps\(i, "code"/, '每一格要把自己的列键交给共用块（漏一格就那一列永不亮）');
  assert.doesNotMatch(comp, /useState<\{ row: number; col: string \}\|null>|const \[selectedCell/,
    '内联那份第二实现不许回来');
  // 10-26：选中态从 React 状态搬到 DOM 上（一次单击要让整表重新协调一遍，
  // 800 格那张表实测 461 毫秒的长任务）。六条断言一条没删，只是指到"亮"这件事现在住的地方：
  // 行／列／交叉那一格各由一个类标出来，深浅两档的颜色只住 `src/index.css`。
  assert.match(highlight, /classList\.add\(XL_ROW\)/, '整行要一起亮（这一行的每一格标一个类）');
  assert.match(highlight, /classList\.add\(XL_COL\)/, '整列要一起亮');
  assert.match(highlight, /classList\.add\(XL_CELL\)/, '交叉那一格单独一档');
  assert.doesNotMatch(highlight, /const \[selectedCell, setSelectedCell\]/,
    '选中态不许再进 React —— 那等于把整表重渲染换回来');
  const css = fs.readFileSync(path.join(HERE, '../src/index.css'), 'utf8');
  assert.match(css, /td\.xl-row \{ background-color: hsl\(var\(--primary\) \/ \.05\)/,
    '行那一档的深浅只住样式表一处');
  assert.match(css, /td\.xl-cell \{ background-color: hsl\(var\(--primary\) \/ \.10\)/,
    '交叉那一格比行/列深一档');
  assert.match(highlight, /hover:bg-primary\/5/, '每格的可点与悬停样式还在（与选中态无关）');
});

// --------------------------------------------- 说明文字折起来（用户 2026-10-15：调用统一说明框、默认收起）

test('三处说明文字走通用说明框，而告警与读数留在框外', () => {
  // 折起来的三块
  assert.match(comp, /<ExplainBox title="这一屏的取数口径（后端原句，页面未改写）">/,
    'caliber 那句要收进说明框（用户点名的正是这一类长段说明）');
  assert.match(comp, /<ExplainBox title="上面那些整组读数的键是什么/, 'summary 键值那段小字同样折起');
  assert.match(comp, /<ExplainBox title="这一张表怎么点/, 'STOCK_ROW_HINT 折起（与 /grouped 同一形状）');
  assert.equal((comp.match(/<ExplainBox/g) ?? []).length, 4,
    '母体切换那块 + 口径 + 键说明 + 怎么点这张表，共四块');
  // 不许折的：告警、缺料声明、被拦提示、以及那两行读数
  for (const box of [...comp.matchAll(/<ExplainBox[\s\S]{0,2500}?<\/ExplainBox>/g)]) {
    assert.doesNotMatch(box[0], /text-warning|text-destructive|<Caveats |x\.popHint/,
      '说明框里出现了告警 —— 折起来等于没写');
  }
  const gap = comp.indexOf('{x.popHint && <p role="status"');
  assert.ok(gap > comp.lastIndexOf('<ExplainBox title="这一张表怎么点'),
    '被拦提示必须排在折叠框之后（否则它是跟着提示一起被折进去的）');
  assert.match(comp, /\{x\.popHint && <p role="status"/, '被拦告警留在框外');
  assert.match(comp, /<CaliberFold caveats=\{\(d\.caveats \?\? \[\]\)\.filter/,
    'caveats 走共用出口（limit_band 那条单独摆在表前，其余进折叠块的 visible 份＝仍在屏上）');
  assert.match(comp, /coverageSentence\(launchRange\(all\), d\)/, '覆盖那行是读数，留在屏上');
});
