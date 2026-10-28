import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/**
 * 四维象限那一屏（`pages/Quadrant.tsx`）里「概念板块 · 这批票里的出现次数」那一张反向榜。
 *
 * 2026-10-14 这一张表从本屏搬进了共用那块（`components/quant/ConceptBoard.tsx`），
 * 因为倍量形态那一屏有一份几乎一样的，两份已经漂到「集中度」的悬停文字各说一套。
 * 钉的是搬家之后最容易悄悄断的四件事：
 *  ① 调用点还在（页面里留着一份旧表体 = 两份实现，漂回原样）；
 *  ② 本屏专属的那句话（分母是哪一批票）没有一起搬走、变成组件里的通用措辞；
 *  ③ caveats 的挑选口径没变（整屏的 caveats 混着别的判据，全贴到这张表上会误导）；
 *  ④ 页面自己不留第二份排序/分页状态（行序与分页归那块管，本地留一份就是各排一次）。
 */

const page = readFileSync(new URL('../src/pages/Quadrant.tsx', import.meta.url), 'utf8');
const board = readFileSync(new URL('../src/components/quant/ConceptBoard.tsx', import.meta.url), 'utf8');

test('反向概念榜走的是共用那一份，本屏不另写一张表', () => {
  assert.match(page, /import \{ ConceptBoard, type ConceptRow \} from "@\/components\/quant\/ConceptBoard"/);
  assert.match(page, /<ConceptBoard state=\{q\} pageSize=\{20\}/,
    '本屏的每页条数从 20 变了就要连文档一起改（倍量那一屏是 40）');
  assert.doesNotMatch(page, /概念板块 · 这批票里的出现次数/,
    '本屏不许再留一份这张表的标题（那就是两份实现）');
  assert.doesNotMatch(page, /const cdSort|const \[cPage/,
    '排法与分页归那块组件管；本屏留一份本地状态就是"表格与档案各排一次"那一族');
  assert.match(page, /interface Quadrant \{[\s\S]*?concepts\?: ConceptRow\[\] \| null/,
    '行形状用组件那一份，不在本屏重定义（重定义就会漂）');
});

test('分母那句话留在本屏，且说清"不是全市场，也不是这一场全部有象限的票"', () => {
  assert.match(page, /poolLine=\{d => `上面那张榜交出的 \$\{dash\(d\.concept_pool\?\.size \?\? \(d\.rows \?\? \[\]\)\.length\)\} 只（不是全市场，也不是这一场全部有象限的票）`\}/);
  // 组件那边：这一行必须由调用方给（组件不猜分母是谁）
  assert.match(board, /poolLine: \(d: T\) => string/, '分母那句话是调用方的责任，不许写进组件');
  assert.match(board, /<Row label="统计的是哪些票" value=\{poolLine\(d\)\} \/>/);
});

test('caveats 只挑这张表相关的两条前缀，其余不贴', () => {
  assert.match(page, /pickCaveats=\{d => \(d\.caveats \?\? \[\]\)\.filter\(c => String\(c\.code \?\? ""\)\.startsWith\("concept"\) \|\| c\.code === "pool_truncated"\)/,
    '整屏的 caveats 里混着象限判据那几条，全贴到这张表上会被读成"这张表失败了"');
  assert.match(board, /pickCaveats\?: \(d: T\) =>/);
  assert.match(board, /\{pickCaveats && <Caveats items=\{pickCaveats\(d\)\} \/>\}/,
    '没交挑选函数就不贴 caveats —— 不许兜成"空 caveats 也算说过"');
});

test('这张表自己的列键与列头同序，且默认序点亮在只数上', () => {
  const head = board.slice(board.indexOf('概念板块 · 这批票里的出现次数'), board.indexOf('</thead>'));
  const sortKeys = [...head.matchAll(/sortKey="([a-z_]+)"/g)].map(m => m[1]);
  assert.deepEqual(sortKeys, ['concept', 'stocks', 'pool_share_pct', 'members_total', 'lift_pctile'],
    `列头顺序或键名变了：${sortKeys}`);
  const cellKeys = [...board.slice(board.indexOf('<tbody>'), board.indexOf('</tbody>'))
    .matchAll(/<(?:td className="[^"]*"|)[^>]*>\{(?:dash|pctCell)\(c\.([a-z_]+)/g)].map(m => m[1]);
  assert.deepEqual(cellKeys, ['concept', 'stocks', 'pool_share_pct', 'members_total', 'lift_pctile'],
    `格子读的字段与列头不同序（点了不报错，只是那一列永不亮）：${cellKeys}`);
  assert.match(board, /useTableSort\(\{ key: "stocks", dir: "desc" \}\)/,
    '默认排法与列头上亮着的那支箭头必须是同一件事');
});

test('分页说清翻的是排过之后的第几个，不许硬说"第 1 页就是重复次数最高的"', () => {
  // 10-14 之前那一版在按「集中度」排序时还说这句话 —— 那是句假话
  assert.doesNotMatch(board, /第 1 页就是重复次数最高的/);
  assert.match(board, /srt\.sort\s*\?[\s\S]{0,400}已按「\$\{srt\.sort\.key\} \$\{srt\.sort\.dir === "desc" \? "从高到低" : "从低到高"\}」重排/,
    '排过序之后页脚要说清这一页是按哪一列排过的第几个');
  assert.match(board, /这一页是后端交出的序（榜上只数从高到低）里的第/,
    '没排序时读后端序，那句话不能少');
  assert.match(board, /Math\.min\(Math\.max\(1, page\), pages\)/,
    '概念数变少时（换一批票）页码要夹回范围内，不然停在空白页');
});

test('上屏的文案里没有裸的 markdown 星号', () => {
  for (const [name, src] of [['Quadrant.tsx', page], ['ConceptBoard.tsx', board]] as const) {
    const rendered = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');
    const hits = rendered.match(/[^\s]\*\*|\*\*[^\s]/g) ?? [];
    assert.deepEqual(hits, [], `${name} 里有裸的 **，会原样显示在屏幕上：${hits.join(' ')}`);
  }
  // 后端散文（concept_rule 是原句）上屏前过 plain
  assert.match(board, /plain\(d\.concept_rule\)/);
});
