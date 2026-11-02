import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

/**
 * 首板四层过滤深析（用户 2026-10-29 点名「根据以下资料，深入分析下首板……把具体功能做在短线复盘最下方」）。
 *
 * 钉的是三件"写错了不报错、屏幕上却出问题"的事：
 *  · 块序：这一屏必须在**最下方**且在首板分析之后（用户点名"最下方"）；
 *  · 不做合成评分：界面上不许出现把四层加权出来的那一个数（也没有 AI 入口 —— 话术库没有这一型）；
 *  · 列头与口径说明同一份描述符：中文列名以后端 `bounds[].label` 为准，页面那份只在后端没交时兜底。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const page = fs.readFileSync(path.join(HERE, '../src/pages/FirstBoard.tsx'), 'utf8');
const cmpPath = path.join(HERE, '../src/components/review/FirstBoardDeep.tsx');
const cmpRaw = fs.readFileSync(cmpPath, 'utf8');
const cmp = cmpRaw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
const api = fs.readFileSync(path.join(HERE, '../src/lib/api.ts'), 'utf8');
const sys = fs.readFileSync(path.join(HERE, '../src/pages/System.tsx'), 'utf8');

test('这一块挂在短线复盘最下方、首板分析之后，并自己带锚点', () => {
  const daily = page.indexOf('id="first-board-daily"');
  const deep = page.indexOf('id="first-board-deep"');
  assert.ok(daily > 0 && deep > 0, '两个锚点都要在（首页的跳转入口指着 first-board-daily）');
  assert.ok(deep > daily, '用户点名的位置是"最下方"：它必须在首板分析之后');
  assert.ok(page.indexOf('<Disclaimer />') > deep, 'Disclaimer 之后不许再有内容块');
  assert.match(page, /<LazySection label="首板四层过滤">\s*<FirstBoardDeep day=\{day\} \/>/,
    '一开屏要现算概念近 20 场常态（约 5 秒）⇒ 必须滚近才挂载，也别把父页的数据塞进来延后');
});

test('不做合成评分：界面只有一个"命中几条"式读数，没有一个加权总分', () => {
  assert.doesNotMatch(cmp, /total_score|composite|weighted/, '出现合成分字段就是第二套真相源');
  // 外部阈值与本机分位必须并排念出来（差多远要让读者看见，不给"合不合格"的判词）
  assert.match(cmp, /｜资料说：\{b\.external\} ｜本机：\{b\.local\}/,
    '外部阈值与本机分位必须并排念（用的是全角｜，别改半角——那是另一套分隔符）');
  assert.match(cmp, /\{b\.layer\} · \{b\.label\}/, '每一格的层名与列名都来自后端那一条描述符');
  // 覆盖率那一族不能当筛子：句子上屏，不折进抽屉
  assert.match(cmp, /<NoticeBar kind="missing">[\s\S]{0,400}不许拿来当筛子/,
    '源池那一族的"不能当筛子"必须走公用声明条留在屏上（缺数类不许进抽屉）');
  assert.match(cmp, /b\.local}[\s\S]{0,80}<p[^>]*text-muted-foreground">\{data\?\.definitions\?\.missing\}/,
    '缺数那一句（null 不是 0）留在屏上的说明里，不藏进抽屉');
});

test('列头以后端 bounds 为一份描述符，页面只在缺的时候兜底', () => {
  assert.match(cmp, /const bt = \(key: string, fallback: string\) =>\s*bounds\.find\(x => x\.key === key\)\?\.label/);
  const uses = (cmp.match(/label=\{bt\("/g) ?? []).length;
  assert.ok(uses >= 5, `只有 ${uses} 个列头走后端那一份，其余还是页面抄的中文`);
  assert.match(cmp, /const btitle = \(key: string\) => bounds\.find\(x => x\.key === key\)\?\.local/,
    '列头悬停的口径文字与列名同一条：分开抄就会一个改了一个没改');
});

test('取数走 api.firstBoardDeep（不写 /api 前缀），并且登记进系统自检清单', () => {
  assert.match(api, /firstBoardDeep: \(date\?: string, limit\?: number\)/);
  assert.match(api, /`\/market\/first-board-deep\$\{s \? `\?\$\{s\}` : ""\}`/);
  // 注释里写全路径是给人看的（那条口径说明就在注释里），判"拼两次"要剥掉注释再查
  const apiCode = api.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.doesNotMatch(apiCode, /api\/market\/first-board-deep/, 'useProbe/api 层自己补前缀，写了就是拼两次');
  assert.match(sys, /"\/market\/first-board-deep\?date=&limit="/, '接口在跑却没进自检清单＝少一条会验它的用例');
  assert.match(sys, /首板分析那一块/, '首板分析搬家了，自检清单里的"在哪儿"要跟着改（旧文案指着首页）');
});

test('这一屏不给 AI 入口：话术库里没有"首板四层"这一型', () => {
  // 借首板/连板那两套话术会把四层读数当涨停原因问，交出去的 prompt 与这一屏不是一套东西
  assert.doesNotMatch(cmp, /useTemplatePicker|askAi|pageAskAi|DeepDive/);
});

test('SortHeader 自己就是那一格 th，全站不许再给它套一层 th', () => {
  /* 这条是 10-31 浏览器实测抓出来的：`<th><SortHeader/></th>` 是非法 HTML，
     解析器会把里层那个 th 顶成**兄弟**，于是表头 17 格、表体 12 格，列头与数字整排错位——
     `aria-sort` 照样翻、点击照样换序，所以排序测试看不出来，只有数格子才发现。 */
  const root = path.join(HERE, '../src');
  const bad: string[] = [];
  const walk = (dir: string) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) { walk(p); continue; }
      if (!/\.tsx$/.test(e.name)) continue;
      const text = fs.readFileSync(p, 'utf-8').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '');
      if (/<th[^>]*>\s*<SortHeader/.test(text)) bad.push(p);
    }
  };
  walk(root);
  assert.deepEqual(bad, [], '这些文件把 SortHeader 包进了 th：表头会比表体多出一倍格子');
});
