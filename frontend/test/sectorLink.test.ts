import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

const link = readFileSync(new URL('../src/components/ui/SectorLink.tsx', import.meta.url), 'utf8');
const heat = readFileSync(new URL('../src/pages/SectorHeat.tsx', import.meta.url), 'utf8');
const cum = readFileSync(new URL('../src/components/SectorFlowCumulative.tsx', import.meta.url), 'utf8');

/**
 * 板块名 → 东财板块页这一条（用户 2026-10-15 点名：「热度榜与五要素 里面的板块也要有超链接可以点开，
 * 比如『油气开采』对应 https://quote.eastmoney.com/bk/90.BK1276.html」）。
 *
 * 钉两件事：① 地址与"给不给链接"只住 `ui/SectorLink.tsx` 一处（两块屏幕都引它）；
 * ② 没有码就退回纯文本 —— 猜一个地址出去等于把用户送到一个不存在的板块页。
 */

// ---------------------------------------------------------------- 地址只有一处

test('东财板块页的地址只在这一个文件里拼', () => {
  assert.match(link, /https:\/\/quote\.eastmoney\.com\/bk\//);
  const hits: string[] = [];
  for (const dir of ['../src/pages/', '../src/components/']) {
    const found = readdirSync(new URL(dir, import.meta.url), { recursive: true, encoding: 'utf8' } as never) as string[];
    for (const rel of found.filter(f => f.endsWith('.tsx'))) {
      const norm = rel.replace(/\\/g, '/');
      if (norm.endsWith('ui/SectorLink.tsx')) continue;
      const src = readFileSync(new URL(dir + norm, import.meta.url), 'utf8');
      if (src.includes('quote.eastmoney.com/bk/')) hits.push(norm);
    }
  }
  assert.deepEqual(hits, [], '第二处硬编码板块页地址 —— 改一处漏一处，界面上就会一半能点一半不能点');
});

test('emBoardUrl 只认 BK 四位码，别的形状一律 null', () => {
  assert.match(link, /export function emBoardUrl/);
  assert.match(link, /\^BK\\d\{4\}\$\/i/);
  assert.match(link, /if \(!c\) return null|String\(code \?\? ""\)\.trim\(\)/);
});

test('外链一律新标签打开并切断 window 反向引用', () => {
  assert.match(link, /target="_blank"/);
  assert.match(link, /rel="noopener noreferrer"/);
});

// ---------------------------------------------------------------- 两块屏幕共用一份

test('热度榜与净流入两张表都引这一块', () => {
  assert.match(heat, /import \{ SectorLink[,}]/, '热度榜没引共用块');
  assert.match(cum, /import \{ SectorLink \}/, '净流入那张表没引共用块');
  assert.match(heat, /<SectorLink name=\{r\.name\} code=\{r\.em_board_code\}/);
  assert.match(cum, /<SectorLink name=\{r\.sector_name\} code=\{r\.em_board_code\}/);
  // 不是自己又写一个 <a>
  assert.doesNotMatch(heat, /href=\{`https:\/\/quote\.eastmoney/);
});

test('没有码的那一行还是纯文本（不猜地址）', () => {
  const body = link.slice(link.indexOf('if (!url)'), link.indexOf('const renamed'));
  assert.ok(body.length > 60, '没找到"没有链接"那一段');
  assert.match(body, /<span/);
  assert.doesNotMatch(body, /<a /);
  assert.match(body, /NO_FAMILY_TYPES\.includes/, '风格/统计档要单独说"东财没有这一族"，不能混成"没对上名"');
});

test('名字差一个后缀时把东财那一侧的真名说出来', () => {
  // 点开页眉写的是「油气开采Ⅱ」而屏上是「油气开采」—— 这个差要看得见
  assert.match(link, /点开后页眉写的是东财那一侧的名字/);
  assert.match(link, /emName !== label|emName && emName !== label/);
});

// ---------------------------------------------------------------- 屏上要把覆盖数说得出、且分母不是这一页

test('两张表都有一格"能点开多少个"，分母来自后端不是界面行数', () => {
  assert.match(heat, /label="东财板块页"/);
  assert.match(heat, /d\.board_link\?\.available/);
  assert.match(heat, /linked_today/, '这一榜的数要后端交，界面不自己 filter rows 数一边');
  assert.match(cum, /label="东财板块页"/);
  assert.match(cum, /dash\(d\.board_link\.rows_linked\)\}\/\$\{dash\(d\.board_link\.rows_total\)/,
    '这张表自己那批的分母要单独念（90 个行业旁边念 613 会被读成"这张表缺了一大片链接"）');
  assert.match(cum, /dash\(d\.board_link\.dict_linked\)\}\/\$\{dash\(d\.board_link\.dict_total\)/,
    '整张对表的数也要交，两个分母各说各的');
});

test('对表读不到时是告警措辞，不是静默少一片链接', () => {
  assert.match(heat, /对表读不到/);
  assert.match(cum, /对表读不到|一律纯文本/);
});

test('链接的规矩折进说明框，那一格读数不折', () => {
  assert.match(heat, /<ExplainBox title="板块名能不能点开[^"]*">/);
  assert.match(heat, /SECTOR_LINK_CALIBER/);
  // 说明框在表之后或之前都行，但"能点开几个"那一格必须在框外面（默认就在屏上）
  const row = heat.indexOf('label="东财板块页"');
  const box = heat.indexOf('<ExplainBox title="板块名能不能点开');
  assert.ok(row > 0 && box > 0 && row < box, '那一格是读数，不许折进默认收起的框里');
  const boxBody = heat.slice(box, heat.indexOf('</ExplainBox>', box));
  assert.doesNotMatch(boxBody, /<Caveats |<CaveatText |text-warning|text-destructive/);
});
