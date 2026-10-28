import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const rd = (p: string) => fs.readFileSync(new URL(`../src/${p}`, import.meta.url), 'utf8');
const rdPy = (p: string) => fs.readFileSync(new URL(`../../${p}`, import.meta.url), 'utf8');

/**
 * 用户 2026-10-14：「250线以下，其他均线密集的这些个股」
 *             「最好能兼容其他的，比如说年线以下，月线比10日线高，等等各种条件可以多个组合筛选」。
 *
 * 这一屏原来一次只能跑**一条**条件，而执行引擎 `screener.build(conds, logic=…)` 一直是收数组的 ——
 * 所以缺的是接线，不是重写。这几条钉的是接线没接歪，以及均线那一族真进了这一屏。
 */

const PAGE = rd('pages/Screen.tsx');
const VIEW = rdPy('duanxian/screen_view.py');

test('条件是叠着来的：清单能加能删，关系能并且能或者', () => {
  assert.match(PAGE, /const \[rows, setRows\] = useState<CondRow\[\]>\(\[\{ id: 1/, '条件行是一张清单，不是单选');
  assert.match(PAGE, /再加一条条件/, '页面上要有"再加一条"这个动作');
  assert.match(PAGE, /去掉这条/, '能加就得能删，否则加错了只能刷新页面');
  assert.match(PAGE, /name="cond-logic"/, '并且/或者做成一组同名 radio，才点得动、也才读得出当前是哪个');
  assert.match(PAGE, /\["and", "or"\] as const/, '只认这两种关系，不发明第三种');
  // 只有一条条件时这个开关必须**禁用**而不是"看着能点"：
  // 单选下 and/or 是同一个名单，亮着会让人以为它改了什么。
  assert.match(PAGE, /disabled=\{rows\.length < 2\}/, '一条条件时开关要 disabled');
  assert.match(PAGE, /只有一条条件时/, '禁用这件事要写在屏上，不能只藏在 disabled 里');
});

test('条件与参数按 JSON 交给 conds，页面不自己拼 SQL', () => {
  assert.match(PAGE, /conds: JSON\.stringify\(spec\.conds\)/, '多条条件走 conds（JSON 数组）');
  assert.match(PAGE, /logic: spec\.logic/, '关系随行交出');
  assert.doesNotMatch(PAGE, /sql_template/, '模板原文不外显，更不在前端拼');
  assert.doesNotMatch(PAGE, /fetch\(|axios/, '取数只有 useProbe 一条路（页面里绝不写 /api 前缀）');
  assert.match(PAGE, /useProbe<ScreenRun>\("\/screen", nonce/, 'path 不带 /api，probe 自己补');
});

test('每条条件实际生效的参数都要在屏上，且带第几条', () => {
  // 一份合并后的 params 字典读不出"哪一条按什么筛的"（四条各有 pct）
  assert.match(VIEW, /"conditions": \[\{"index": c\["index"\]/, '每条交回 index + name + params');
  assert.match(VIEW, /第 \{i\} 条|第 \{\{?i/, '报缺参要说第几条');
  assert.match(PAGE, /第 \$\{c\.index\} 条 · \$\{c\.name\}/, '逐条列出，不折叠成一行');
});

test('再点一次「跑一次」必须真的再发一次请求', () => {
  // useProbe 的三个依赖是 (path, query, nonce)，结果组件自己那份 nonce 从不加一 ——
  // 同参数再点就是一次静默的不动作，屏上留着上一份名单，人会读成"后端查了一遍，没变"。
  assert.match(PAGE, /seq: \(s\?\.seq \?\? 0\) \+ 1/, '每次点都加一个 seq');
  assert.match(PAGE, /<RunResult key=\{spec\.seq\} spec=\{spec\} \/>/, 'seq 要当 key 用，否则只是改了个数字');
});

test('均线那一族在本屏看得见，且口径写明是复权价', () => {
  assert.match(PAGE, /ma_position|dense_short5/, '均线条件要在"能给什么"那一块点名，不能只说"条件很多"');
  assert.match(PAGE, /复权价/, '这一族的口径必须写在屏上');
  assert.match(PAGE, /133\/4,933/, '换底的依据要带那个实测数，否则像一句口号');
  const sql = rdPy('app/db/sql/35_ma_hfq_screen.sql');
  assert.ok(sql.includes('35_ma_hfq_screen.sql'), '屏幕指的那个脚本得真的存在');
});

test('前后端那道"最多几条"的夹是同一个数', () => {
  const front = Number(PAGE.match(/const MAX_CONDS = (\d+)/)?.[1]);
  const back = Number(VIEW.match(/MAX_CONDS = (\d+)/)?.[1]);
  assert.ok(front && back, '两边都得写死这个常量');
  assert.equal(front, back, '界面放开到 7 条而后端退一句"最多 6 条"，那是一次白跑的请求');
});

test('空条件不许被当成"全部命中"跑出去', () => {
  // 执行引擎里空 conds = `where true` = 整个候选集，页面会读成"全过了"
  assert.match(VIEW, /一条条件都没有/, '读层要挡');
  assert.match(PAGE, /每一条都得先选一个条件/, '界面也要说清是自己没选，不是后端坏了');
});

test('结果表那一列代码仍然链到行情，排序键还是格子里读到的字段名', () => {
  assert.match(PAGE, /<StockLink code=\{String\(r\[c\] \?\? ""\)\}/, '个股外链不许在改造里掉');
  assert.match(PAGE, /cols\.map\(c => <SortHeader key=\{c\} label=\{c\} sortKey=\{c\}/,
    'sortKey 必须等于列名本身（这一屏的列是后端交的中文列名）');
});
