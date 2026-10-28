import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { NO_RANK_RULE_TEXT, NO_UNIVERSE_TEXT, rankText, rankTitle, trimNum } from '../src/lib/ranking.ts';

/**
 * 「名次」与「小数尾零」这两件事的全站一份实现（`src/lib/ranking.ts`）。
 *
 * 口径改于 2026-10-19 第二稿：加权分要排名（同分的合并、下一名按位置顺延那一句由后端
 * `duanxian/ranking.py` 负责并随接口交出），而得分/分位/上限那一族数字以前是 `toFixed` 直出，
 * 屏幕上就是 `1.000000 / 40.0000 / 4.2100`。两头都收在这一个文件里，所以这里钉的是：
 *  ① **函数真的按契约算**（能直接跑的纯函数就真跑，不只看源码）；
 *  ② **组件不许自造规则那句话、不许另写一把去尾零的尺**（读源码钉）；
 *  ③ **缺数一律 —，绝不落到 0**（0 名、0 分都是实质结论，与"没数"是两件事）。
 */

const ranking = readFileSync(new URL('../src/lib/ranking.ts', import.meta.url), 'utf8');
const vd = readFileSync(new URL('../src/pages/VolumeDouble.tsx', import.meta.url), 'utf8');
const track = readFileSync(new URL('../src/components/quant/EnergyTrackBoard.tsx', import.meta.url), 'utf8');
const panel = readFileSync(new URL('../src/components/VolumeEnergyPanel.tsx', import.meta.url), 'utf8');
// 第三处调用（10-19）：个股量化档案里那块「倍量形态」，名次与小数显示也必须走同一把尺。
const dossierSrc = readFileSync(new URL('../src/components/quant/StockDossier.tsx', import.meta.url), 'utf8');
// 第五处调用（2026-10-26）：分组整体分析「近 N 场排名」那一列（组之间的名次，不是票）。
const groupBoardSrc = readFileSync(new URL('../src/components/quant/GroupAnalyticsBoard.tsx', import.meta.url), 'utf8');

const srcFiles = (dir: string, out: string[] = []): string[] => {
  for (const e of readdirSync(new URL(dir, import.meta.url), { withFileTypes: true })) {
    const rel = `${dir}${e.name}`;
    if (e.isDirectory()) srcFiles(`${rel}/`, out);
    else if (/\.(ts|tsx)$/.test(e.name)) out.push(rel);
  }
  return out;
};
const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), 'utf8');

test('rankText：没有名次就是 —，不是 0、也不是别的一个数', () => {
  assert.equal(rankText(null), "—", '缺分的那只票没有名次：念 —');
  assert.equal(rankText(undefined), "—", '字段整个没交也是 —');
  assert.equal(rankText(NaN), "—", 'NaN 不算名次');
  assert.equal(rankText("40" as unknown), "—", '只认数值列交来的数：字符串一律 —（与 num/dash 同规矩，不静默换算）');
  assert.equal(rankText(40), "40");
  assert.equal(rankText(1), "1", '第 1 名就是 1，不写成 "01"、也不加"第"字（列头已经说了这是名次）');
  assert.equal(rankText(0), "0", '真交来 0 也照念：0 是读数，不该被当成缺数吞掉');
});

test('trimNum：只动显示层（去尾零），缺数还是 —', () => {
  assert.equal(trimNum(1.000000), "1", '顶档一天记 1 分以前念成 1.000000');
  assert.equal(trimNum(40.0), "40", '满窗上限念 40，不是 40.0000');
  assert.equal(trimNum(4.2100), "4.21", 'p50 念 4.21');
  assert.equal(trimNum(11.61), "11.61");
  assert.equal(trimNum(0.05), "0.05", '小分值不能被 trim 成 0 或 .05');
  assert.equal(trimNum(1.5, 4), "1.5", 'step_gap_min 那种一位小数不多不少');
  assert.equal(trimNum(0), "0", '真 0 照常是 0（与 — 是两种读数）');
  assert.equal(trimNum(null), "—", '没数就是 —，绝不落到 0');
  assert.equal(trimNum(undefined), "—");
  assert.equal(trimNum(NaN), "—");
  assert.equal(trimNum(Infinity), "—");
  assert.equal(trimNum("4.21" as unknown), "—", '只认数值（与 lib/qd.ts 的 num 同边界，不在显示层猜字符串）');
  assert.equal(trimNum(4.23456, 2), "4.23", 'digits 给了就按它截，不给默认 4 位');
});

test('rankTitle：规则原文逐字用接口那份，分母是哪一批一起给出', () => {
  const rule = "名次＝同分同名、下一名按位置顺延（后端那句话的替身）";
  const t = rankTitle(rule, { scored: 5514, rows_total: 5527, unscored: 13, top_score: 11.61 }, 40);
  assert.ok(t.startsWith(rule), `规则原文必须一字不改地排在最前，实得：${t}`);
  assert.match(t, /分母＝当日有分的 5,514 只/, '分母要说清是哪一批（千分位）');
  assert.match(t, /这一场库里一共 5,527 行/);
  assert.match(t, /没有分的 13 只不在这把尺上/);
  assert.equal(t.includes("这一格＝当日 5,514 只有分里的第 40 名"), true, `悬停要给"哪一批里的第几名"，实得：${t}`);

  // 接口没交规则 / 没交分母：念占位句，不拿 0 顶、也不自己编一句规则
  assert.ok(rankTitle("", { scored: 5514 }).startsWith(NO_RANK_RULE_TEXT),
    '接口没交规则原文时要念那句占位，不许界面自己补一句规则');
  const noRuleNoUniv = rankTitle(null, null);
  assert.ok(noRuleNoUniv.includes(NO_RANK_RULE_TEXT) && noRuleNoUniv.includes(NO_UNIVERSE_TEXT), noRuleNoUniv);
  assert.doesNotMatch(noRuleNoUniv, /分母＝当日有分的 0/, '没读到不许说成 0 只');
  // 逐日走势那块只交规则、不交 universe（rank.universe 只在倍量榜那侧）：分母就念没读到
  const noUniv = rankTitle(rule, null, 12);
  assert.ok(noUniv.includes(NO_UNIVERSE_TEXT), `分母没读到时要说那句占位，实得：${noUniv}`);
  assert.ok(noUniv.startsWith(rule), `规则原文还是要排在最前，实得：${noUniv}`);
  // 这一格没有名次（缺分）：说清是没名次，不是 0
  const none = rankTitle(rule, { scored: 5514 }, null);
  assert.doesNotMatch(none, /第 0 名/);
  assert.match(none, /这一格没有名次/);
});

test('名次实现全站只有一份：三个屏都调它，组件里不许自造规则那句话', () => {
  // 倍量榜（名次列）、逐日块（今日名次列）、个股量化档案（倍量那一块）都从 `lib/ranking.ts` 取
  assert.match(vd, /import \{ rankText, rankTitle, trimNum \} from "@\/lib\/ranking"/);
  assert.match(track, /import \{ rankText, rankTitle, trimNum \} from "@\/lib\/ranking"/);
  for (const [name, code] of [["倍量榜", vd], ["逐日块", track]] as const) {
    assert.ok(code.includes("rankText(r.rank_in_day)"), `${name} 的名次那一格没走公用件`);
    assert.ok(code.includes("rankTitle("), `${name} 的名次悬停没走公用件`);
    // 规则那句话只住后端（随接口交出），组件源码里一个都不许出现
    assert.doesNotMatch(code, /同分同名|按位置顺延|不占号/, `${name} 里自己写了一句名次规则（那就是第二份判据）`);
    // 缺分不许折成 0：名次那一格只能是公用件给的 —
    assert.doesNotMatch(code, /rank_in_day\s*\?\?\s*0/, `${name} 把没有名次说成了第 0 名`);
  }
  // 配置卡也不自造那句话，且它念的是接口 `rank_rule`
  assert.doesNotMatch(panel, /同分同名|按位置顺延|不占号/, "配置卡里自己写了一句名次规则");
  assert.match(panel, /cfg\?\.rank_rule \?\?/);

  // 第三处调用（2026-10-19）：个股量化档案里那块「倍量形态」的名次必须与倍量榜同一条规则，
  // 所以它也只准转述接口那一份 —— 同一个名字在两个屏上必须是一个数（后端 SQL 也已改成同分同名）。
  // 2026-10-22 起档案那一叠还多接了一件：`scoreRankCell`（八分量那一格的名次），
  // 所以导入清单按"包含"判，不再钉逐字顺序 —— 加一个函数不该让这条纪律测试变红。
  assert.match(dossierSrc, /import \{[^}]*rankText[^}]*rankTitle[^}]*trimNum[^}]*\} from "@\/lib\/ranking"/,
    '档案那块没接公用件');
  assert.ok(dossierSrc.includes('rankText(d.rank_in_market)'), '档案那格名次没走公用件');
  assert.ok(dossierSrc.includes('rankTitle(d.rank_rule'), '档案那格的名次悬停没读接口那句规则');
  assert.doesNotMatch(dossierSrc, /同分同名|按位置顺延|不占号/, '档案里自己写了一句名次规则');
  assert.doesNotMatch(dossierSrc, /rank_in_market\s*\?\?\s*0/, '档案把没有名次说成了第 0 名');

  // 第五处调用（2026-10-26）：分组整体分析「近 N 场排名」那一列 —— 组之间的名次，同一把尺、同一句规则。
  assert.match(groupBoardSrc, /import \{ rankText \} from "@\/lib\/ranking";/,
    '组热度表的名次那一格没接公用件');
  assert.ok(groupBoardSrc.includes('rankText(r.rank_now)'), '组热度表那格名次没走公用件');
  assert.doesNotMatch(groupBoardSrc, /同分同名|按位置顺延|不占号/,
    '组热度表里自己写了一句名次规则（那就是第二份判据 —— 规则只住后端 `ranking.RULE_NOTE`）');
  assert.doesNotMatch(groupBoardSrc, /rank_now\s*\?\?\s*0|rank_gain\s*\?\?\s*0/,
    '组热度表把"那场排不出名次"说成了第 0 名');
  assert.match(groupBoardSrc, /\[rk\?\.rule_short, rk\?\.direction, calibers\?\.rank_gain,/,
    '格子悬停必须转述接口那三句原话（规则短版／方向读法／净变化口径）');
  assert.match(groupBoardSrc, /近 \$\{dash\(d\.rank\?\.days\)\} 场排名/,
    '列头那个 N 必须读接口 rank.days —— 写死 5 会让改了窗口的列头继续说 5');

  // 除公用件自己，全站不许有第二处 rankTitle／trimNum 的定义，也不许有第二处给"当日名次"格子用的格式化
  const definers = srcFiles('../src/').filter(f => f !== '../src/lib/ranking.ts'
    && /export function (rankTitle|trimNum)\b/.test(read(f)));
  assert.deepEqual(definers, [], `名次悬停与去尾零又长出第二份实现：${definers}`);
  // `rankText` 这个名字在 `lib/quadrantTrack.ts` 里早有一位住户，但它管的是另一件事
  // （中位名次可以带 .5，还被段长那一列复用），不是"当日名次"这一格。这里钉住两边不许互相越界：
  // 那一屏不许接到 rank_in_day 上（接了就得走 ranking.ts 那一份）。
  const qt = read('../src/lib/quadrantTrack.ts');
  assert.doesNotMatch(qt, /rank_in_day/,
    '当日名次那一格走 `lib/ranking.ts`，别拿 quadrantTrack 那个带一位小数的 rankText 顶');
  for (const fn of ["rankText", "rankTitle", "trimNum"])
    assert.equal((ranking.match(new RegExp(`export function ${fn}\\(`, 'g')) ?? []).length, 1,
      `${fn} 在公用件里也只该有一份定义`);
});

test('倍量榜的名次列：sortKey 就是格子读的那个字段，两个方向都能点', () => {
  const head = vd.slice(vd.indexOf('<SortHeader label="代码"'), vd.indexOf('</tbody>'));
  assert.match(head, /label="名次" sortKey="rank_in_day" dir=\{vdSort\.dirOf\("rank_in_day"\)\} onSort=\{sortVd\}/,
    '列头键必须与格子真读的字段同名（写成别的就点了不换序）');
  assert.match(head, /cellProps\(gi, "rank_in_day"/, '格子的键与列头是同一个');
  assert.match(vd, /rank_in_day\?: number \| null;/, '行类型上得登记这个字段，否则排序取不到值');
  // 悬停的规则与分母都来自接口那两块（rank.rule / rank.universe），页面不自己拼
  assert.match(head, /title=\{rankTitle\(plain\(d\.rank\?\.rule \?\? ""\), d\.rank\?\.universe \?\? null\)/,
    '列头悬停：规则原文＋分母，全部读接口');
  assert.match(head, /rankTitle\(plain\(d\.rank\?\.rule \?\? ""\), d\.rank\?\.universe \?\? null, r\.rank_in_day \?\? null\)/,
    '格子悬停：再加一句「这一格是第几名」（缺分交 null，让公用件念那一句）');
  // 名次与这把尺是一套：那一行把 rank.rule/rule_short 与 universe 都念出来
  assert.match(vd, /<Row label="名次这把尺"/);
  assert.match(vd, /d\.rank\?\.rule_short/);
  assert.match(vd, /排的是 \$\{dash\(d\.rank\?\.by\)\}/, '这一列排的是哪一字段要念接口给的，不自己写 dbl_energy');
});

test('逐日块的今日名次：进固定列的清单，且母体只数按 rows_capped 说清', () => {
  const head = track.slice(track.indexOf('<thead>'), track.indexOf('</thead>'));
  assert.match(head, /label="今日名次" sortKey="rank_in_day" dir=\{sort\.dirOf\("rank_in_day"\)\} onSort=\{sortBy\}/);
  assert.match(track, /cellProps\(gi, "rank_in_day"/);
  assert.match(track, /rank_in_day\?: number \| null;/, 'TrackRow 上得登记，不然点了不换序');
  // 母体是"前 N 名"这个上限，边界并列一起进来 ⇒ 只数可以多于 N
  assert.match(track, /N 是上限，不是恰好 N 只/);
  assert.match(track, /rows_capped === true/);
  assert.match(track, /只数可以比 N 多几只/);
  assert.doesNotMatch(track, /当日前 \$\{dash\(d\.top_n\)\} 名（同分并列一起进来，所以实际只数略多于这个数）/,
    '旧那句含糊的"略多于"不许复活（要说清 N 是上限并给出实际只数）');
});

test('去尾零这一把尺接到的地方：scale／distribution／dbl_energy／dbl_step_gap 全走 trimNum', () => {
  for (const [name, code] of [["倍量榜", vd], ["逐日块", track], ["配置卡", panel]] as const) {
    for (const m of code.matchAll(/num\(([^)]*)\)/g)) {
      const arg = m[1];
      assert.doesNotMatch(arg, /scale|max_score|max_per_day|p50|p90|p99|dbl_energy|dbl_step_gap/,
        `${name} 还在用 toFixed 那把旧尺念加权分那一族数字：num(${arg})`);
    }
  }
  assert.match(vd, /满窗上限 \$\{trimNum\(d\.energy\?\.scale\?\.max_score\)\}/);
  assert.match(vd, /p50 \$\{trimNum\(d\.energy\?\.distribution\?\.p50\)\}/);
  assert.match(vd, /加权分 \$\{trimNum\(r\.dbl_energy\)\}/);
  assert.match(vd, /\{trimNum\(r\.dbl_step_gap\)\}/, '分段差那一格也走去尾零');
  assert.match(track, /<span className="font-semibold">\{trimNum\(r\.dbl_energy\)\}<\/span>/);
  assert.match(panel, /trimNum\(cfg\?\.scale\?\.max_score\)/);
  assert.match(panel, /trimNum\(dist\?\.p50\)/);
  assert.ok(vd.includes('import { rankText, rankTitle, trimNum } from "@/lib/ranking"')
    || panel.includes('import { trimNum } from "@/lib/ranking"'), '去尾零那把尺没接到配置卡');
});

test('公用件自己：中文串不嵌 ASCII 双引号、渲染文本没有裸 **（四个用它的文件一起查）', () => {
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');
  // 2026-10-19：个股量化档案那一块也接了这把尺（名次与加权分与倍量榜同一条规则），所以一起查。
  for (const [name, src0] of [["ranking", ranking], ["VolumeDouble", vd],
                              ["EnergyTrackBoard", track], ["VolumeEnergyPanel", panel],
                              ["StockDossier", dossierSrc]] as const) {
    const code = strip(src0);
    const hits = code.match(/[^\s]\*\*|\*\*[^\s]/g) ?? [];
    assert.deepEqual(hits, [], `${name}：这些地方有裸的 **，会原样显示：${hits.join(' ')}`);
    assert.doesNotMatch(code, /[\u4e00-\u9fff]"[\u4e00-\u9fff]/,
      `${name}：中文句子中间出现了 ASCII 双引号（要用「」）`);
    for (const b of code.match(/>([^<>{}]*[\u4e00-\u9fff][^<>{}]*)</g) ?? []) {
      assert.doesNotMatch(b, /"/, `${name}：JSX 文本节点里出现了 ASCII 双引号：${b}`);
    }
  }
});
