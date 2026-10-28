import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const rd = (p: string) => fs.readFileSync(new URL(`../src/${p}`, import.meta.url), 'utf8');
const rdPy = (p: string) => fs.readFileSync(new URL(`../../${p}`, import.meta.url), 'utf8');

/**
 * 均线拐头条件（`ma_turn`，用户 2026-10-20 夜：「XX日线拐头的……长期下行 or 长期上行？
 * 叠加近 X 日横住了或者有所回升，回升率（低于）Y%」）在筛选器那一屏的接线。
 *
 * 这一屏的表单是**从接口生成的**（`/api/screen/rules` 的 param_schema 摊成控件），
 * 所以这里钉的不是"页面画了几个框"，而是四件写错不会报错的事：
 *  ① 发出去的参数键名与后端登记的字段名一字不差（FastAPI 丢不认识的键＝筛了个寂寞）；
 *  ② 默认值只住后端一处，组件里不许抄第二份（抄了就会有两边各指一个默认的日子）；
 *  ③ 最多叠 6 条的计数跟着这条走（它是清单里普通的一条，不另开快速通道也不被跳过）；
 *  ④ 中文串不嵌 ASCII 双引号、上屏文本无裸 `**`（项目红线），悬停写清后复权口径。
 */

const PAGE = rd('pages/Screen.tsx');
const VIEW = rdPy('duanxian/screen_view.py');
const MIG = rdPy('app/db/sql/47_ma_turn_screen.sql');
const APP = rdPy('vr/app.py');

/** 后端 param_schema 登记的七个参数名（从迁移文件的 properties 块里真解析出来） */
const PROPS_BLOCK = MIG.match(/"properties":\{([\s\S]*?)\}\}',/);
assert.ok(PROPS_BLOCK, '47 号迁移里要能读到 ma_turn 的 properties 块');
const BACKEND_KEYS = [...PROPS_BLOCK[1].matchAll(/"([a-z_]+)":\s*\{/g)].map(m => m[1]);

test('后端登记的七个参数名齐（n/长期段/方向/近期段/形态/阈值/比较方向）', () => {
  assert.deepEqual(BACKEND_KEYS, ['n', 'long_days', 'long_dir', 'recent_days',
    'recent_shape', 'threshold_pct', 'cmp']);
  assert.match(MIG, /'ma_turn'/, 'rule_key 就叫 ma_turn');
});

test('发出去的载荷键名就是后端字段名：控件按 p.name 建、按 p.name 发', () => {
  // 条件走 conds JSON（`{"rule_key":…,"params":{参数名:值}}`），页面不另起键名、不自己拼 URL
  assert.match(PAGE, /conds: JSON\.stringify\(spec\.conds\)/, '多条条件走 conds');
  assert.match(PAGE, /rule_key: active\[i\], params: r\.values/, '每条交自己的参数字典');
  assert.match(PAGE, /values: \{ \.\.\.r\.values, \[name\]: v \}/, '参数字典的键 = 登记的名字');
  assert.match(PAGE, /setRowValue\(r\.id, p\.name, e\.target\.value\)/, '控件写回也用 p.name 原键');
  // 后端单条老写法：除保留字之外的 query 键**原样**当参数名收（不认识的就交给 schema 点名）
  assert.match(APP, /request\.query_params\.items\(\)[\s\S]{0,120}if k not in \("rule", "conds", "logic", "universe", "date", "limit"\)/,
    '保留字之外全部原样传给读层——键名漂了就是筛了个寂寞');
  for (const k of BACKEND_KEYS) {
    assert.ok(!APP.includes(`"${k}"`), `参数名 ${k} 不许被 vr/app.py 的保留字吃掉`);
  }
});

test('默认值只住后端一处：组件里不许出现这份参数的具体值', () => {
  // 界面读 /api/screen/rules 的 default（placeholder / select 初值都来自接口），不抄第二份
  assert.match(PAGE, /placeholder=\{p\.default != null \? String\(p\.default\) : ""\}/);
  assert.match(PAGE, /r\.values\[p\.name\] \?\? String\(p\.default \?\? ""\)/);
  assert.doesNotMatch(PAGE, /threshold_pct[^0-9]{0,20}0\.5/, '组件里不许硬编码阈值默认');
  assert.doesNotMatch(PAGE, /"(down|up|recover|flat|fall|gte|lte)"/, '枚举值也不许在组件里出现第二遍');
  assert.match(MIG, /"n":\{"type":"integer","default":60/, '默认 60 住在登记的 param_schema 里');
});

test('这条参与最多叠 6 条的计数：上限是同一个数，加行不按 rule_key 特判', () => {
  const front = Number(PAGE.match(/const MAX_CONDS = (\d+)/)?.[1]);
  const back = Number(VIEW.match(/MAX_CONDS = (\d+)/)?.[1]);
  assert.ok(front && back, '两边都得写死这个常量');
  assert.equal(front, back, '界面放开到 7 条而后端退一句「最多 6 条」，那是一次白跑的请求');
  assert.match(PAGE, /disabled=\{rows\.length >= MAX_CONDS\}/, '加一条的按钮按行数夹，不按条件种类夹');
  const noComments = PAGE.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');
  assert.doesNotMatch(noComments, /rule_key === ['"]ma_turn['"]/,
    '表单逻辑里不许按 rule_key 特判——这条就是清单里普通的一条（提到它的只许是注释与口径散文）');
});

test('判据原文读接口上屏，悬停写清后复权口径', () => {
  // 登记行的 note 是判据原文的唯一宿主：短的那一档清单里直接可见，长的那一档默认收起
  // （用户 2026-10-21：「把选项留出，描述内容都收缩起来」）。两条分支都只渲染 `plain(r.note)`，
  // 页面不许自己另写一句判据，也不许把长段摊成一面墙。
  assert.match(PAGE, /r\.note && \(r\.note\.length > 160/,
    '长 note 走折叠分支（判据是"这一条有多长"，不是按 rule_key 特判）');
  assert.match(PAGE, /<ExplainBox title=\{`判据原文/,
    '长的那一份必须走共用 ExplainBox 默认收起，不许留一条硬编码可见的段落');
  assert.match(PAGE, /<p className="whitespace-pre-line">\{plain\(r\.note\)\}<\/p>/,
    '收起那份仍然是后端原句，只是套了折叠');
  assert.match(PAGE, /:\s*<p className="mt-0\.5 whitespace-pre-line[^"]*">\{plain\(r\.note\)\}<\/p>\)/,
    '短 note 照旧直接渲染，短的不许多点一次才看得到');
  assert.match(MIG, /判据一句话[：:]/, '后端 note 里就带着那句判据');
  assert.match(PAGE, /title=\{hfqTip/, '悬停提示挂在条件行与参数上');
  assert.match(PAGE, /这条用的是后复权均线/, '悬停文案写明后复权');
  assert.match(PAGE, /rule\?\.note\?\.includes\("后复权"\)/, '悬停按 note 里有没有后复权来判断，不按 rule_key 特判');
});

test('中文串不嵌 ASCII 双引号，渲染文本没有裸 **', () => {
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');
  const code = strip(PAGE);
  const hits = code.match(/[^\s]\*\*|\*\*[^\s]/g) ?? [];
  assert.deepEqual(hits, [], `这些地方有裸的 **，会原样显示：${hits.join(' ')}`);
  assert.doesNotMatch(code, /[\u4e00-\u9fff]"[\u4e00-\u9fff]/,
    '中文句子中间出现了 ASCII 双引号（要用「」）');
  // JSX 文本节点：先把表达式容器（含嵌套）剥干净，剩下的 `>…<` 才是真会上屏的散文
  let noExpr = code;
  for (let prev = ""; prev !== noExpr;) {
    prev = noExpr;
    noExpr = noExpr.replace(/\{[^{}]*\}/g, "");
  }
  for (const b of noExpr.match(/>([^<>]*[\u4e00-\u9fff][^<>]*)</g) ?? []) {
    assert.doesNotMatch(b, /"/, `JSX 文本节点里出现了 ASCII 双引号：${b}`);
  }
});
