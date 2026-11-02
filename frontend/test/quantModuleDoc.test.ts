import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

/**
 * 「这块的说明」搬进模块标题行（用户 2026-10-31 两点要求）在量化选股这一组的源码级钉子。
 *  1. 每块自写 `GlassCard` + 标题的模块，说明按钮（`DocDrawer` 触发器）必须在标题行的
 *     `ml-auto` 那一簇里（屏上原来长在正文里的 `ExplainBox` 折框不算数）；
 *  2. 一块只许一颗：这些文件里抽屉数量与清单一致，正文里不许再留被搬走的那几颗同名按钮；
 *  3. 抽屉不许是空壳：每口抽屉体里得有真说明文字（≥20 个中文字）或接口口径字段。
 * 判前一律剥注释 —— 注释里写着「这块的说明」「判据六分项」这类中文词，不剥会让正则假绿
 * （`lazySection.test.ts` 同一条教训）。走 `<Panel note=...>` 的模块由 `DataPanel.tsx` 那一处
 * 统一负责（用户 10-31 明说不用再动），这里只钉自写卡片。
 */

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/[^\r\n]*/gm, '');

function src(rel: string): string {
  return strip(fs.readFileSync(new URL(rel, import.meta.url), 'utf8'));
}

/** 每个 `<DocDrawer` 到最近一个 `</DocDrawer>` 之间的抽屉体（这些文件里没有套娃抽屉） */
function drawerBodies(text: string): string[] {
  const out: string[] = [];
  let at = 0;
  for (;;) {
    const start = text.indexOf('<DocDrawer', at);
    if (start < 0) return out;
    const end = text.indexOf('</DocDrawer>', start);
    assert.ok(end > start, 'DocDrawer 没有闭合');
    out.push(text.slice(start, end));
    at = end + 1;
  }
}

const CJK = /[\u4e00-\u9fff]/g;

function assertTitleRowDrawers(rel: string, expect: number) {
  const text = src(rel);
  const bodies = drawerBodies(text);
  assert.equal(bodies.length, expect,
    `${rel}：标题行说明按钮应是 ${expect} 颗，实到 ${bodies.length} 颗（一块一颗，不许在同张卡里长出两颗同名的）`);
  for (const b of bodies) {
    const cjk = (b.match(CJK) ?? []).length;
    assert.ok(cjk >= 20 || /cfg\?\.scale\?\.formula|cfg\.rank_rule|d\.calibers|d\.definitions/.test(b),
      `抽屉是空壳（正文剥完注释只剩 ${cjk} 个中文字）：${b.slice(0, 60)}…`);
    // 按钮必须长在标题行那一簇：往前找 500 字以内要有 h2/h3 标题，往后 400 字以内不得再出现正文级 h2
    const before = text.slice(Math.max(0, text.indexOf(b) - 500), text.indexOf(b));
    assert.ok(/<(h2|h3)\b/.test(before), 'DocDrawer 前面 500 字内没有标题 —— 它不在标题行');
    assert.ok(/ml-auto/.test(before), 'DocDrawer 没排进标题行最右边的 ml-auto 那一簇');
  }
  return text;
}

test('SectorHeat：三块自写卡片（判据 / 榜上为何看不到 / 算不出的项）说明都在标题行', () => {
  const text = assertTitleRowDrawers('../src/pages/SectorHeat.tsx', 3);
  for (const t of ['趋势形成度与低吸：判据（逻辑就在屏上，不藏在代码里）',
    '为什么榜上看不到「昨日涨停 / 连板 / 突涨」', '算不出的项与它的后果']) {
    assert.ok(text.includes(`<DocDrawer title="${t}">`), `缺标题行按钮：${t}`);
  }
  // 搬走的三颗正文折框不许留在原地（同一句不许既摊在正文又进抽屉）
  assert.doesNotMatch(text, /<ExplainBox title="判据六分项|<ExplainBox title="下面那张表的来源|<ExplainBox title="为什么这几类天然不在分母里|<ExplainBox title="同类读数在别的屏/);
  // 抽屉里的说明都是从前正文搬来的原句
  assert.match(text, /最小二乘斜率同号才算成形状/);
  assert.match(text, /全部 <code>sector_type='指数'<\/code>/);
  assert.match(text, /两者为何不合并见 <code>docs\/页面展示工作指南\/02<\/code>/);
  // 留在屏上的：档位顺序互斥那段（含「过热」误读告警）与逐档实际数
  assert.match(text, /<p className="mt-2 text-\[11px\] leading-relaxed text-muted-foreground">\s*<b className="text-foreground">档位按顺序互斥<\/b>/);
});

test('Quadrant：两套象限那块卡片的说明在标题行，缺数读数留在屏上', () => {
  const text = assertTitleRowDrawers('../src/pages/Quadrant.tsx', 1);
  assert.doesNotMatch(text, /<ExplainBox/);
  assert.match(text, /<DocDrawer title="两套象限为什么必须分开看，而且不得互相翻译">/);
  assert.match(text, /不存在「Q1 就是判对且赚钱」/);
  // 「四格相加不等于全市场票数」那段是实际缺数读数，必须还在屏上（抽屉体之外）
  const drawerEnd = text.indexOf('</DocDrawer>');
  assert.match(text.slice(drawerEnd), /四格相加不等于全市场票数/);
});

test('Screen：跑筛选那张卡片的说明在标题行，开关不生效那句留在屏上', () => {
  const text = assertTitleRowDrawers('../src/pages/Screen.tsx', 1);
  assert.match(text, /<DocDrawer title=\{`跑这一次筛选（条件可以叠 \$\{MAX_CONDS\} 条）`\}>/);
  assert.match(text, /白名单是执行引擎那道硬门/);
  assert.doesNotMatch(text, /<ExplainBox title="这些参数怎么给/);
  // 「只有一条条件时并且/或者这个开关不起作用」说的是控件当前生不生效 —— 不许进抽屉
  assert.match(text.slice(text.indexOf('</DocDrawer>')), /只有一条条件时「并且\/或者」这个开关不起作用/);
  // 登记表里那条逐条条件的「判据原文」折叠是列表项自己的出口，不在这条纪律内（Panel 那一支归 DataPanel 统一管）
  assert.equal((text.match(/<ExplainBox\b/g) ?? []).length, 1, 'Screen 正文只剩判据原文那一颗（Panel 子树里的，10-31 那轮不动）');
});

test('SectorFlowCumulative：表怎么读+字段契约+字段字典合并成标题行一颗，缺料与告警留在屏上', () => {
  const text = assertTitleRowDrawers('../src/components/SectorFlowCumulative.tsx', 1);
  assert.doesNotMatch(text, /<ExplainBox/);
  // 屏上标题与抽屉标题同一份真源（两处各拼一遍迟早分叉）
  assert.match(text, /const blockTitle = `覆盖以来累计净流入/);
  assert.match(text, /<h2 className="text-sm font-bold">\{blockTitle\}<\/h2>/);
  assert.match(text, /<DocDrawer title=\{blockTitle\}>/);
  assert.match(text, /默认序 = 窗口累计由大到小/);
  assert.match(text, /duanxian\/sector_flow\.aggregate/);
  // 按钮排在「重新取数」之前（标题行最右边那一簇里，元信息与动作按钮都在它后面）
  assert.ok(text.indexOf('<DocDrawer') < text.indexOf('重新取数'), '说明按钮要排在取数按钮前面');
  // 不满窗告警、CaveatText（覆盖以来只有 N 场）、后端 caveats、出处行都留在屏上
  const after = text.slice(text.indexOf('</DocDrawer>'));
  assert.match(after, /远不满 3 个月的窗/);
  assert.match(after, /<CaveatText/);
  assert.match(after, /<Caveats items=\{d\.caveats\}/);
  assert.match(after, /<Provenance/);
});

test('VolumeEnergyPanel：倍量加权分档那颗说明在标题行、排在指纹与同批徽标前面', () => {
  const text = assertTitleRowDrawers('../src/components/VolumeEnergyPanel.tsx', 1);
  assert.match(text, /<DocDrawer title="倍量加权分档（倍量榜那把尺子）">/);
  // 抽屉只放接口交出的口径原文（scale.formula / scale.unit / scale.note / rank_rule / 出厂档）
  assert.match(text, /plain\(cfg\.scale\.formula\)/);
  assert.match(text, /plain\(cfg\.scale\.unit\)/);
  assert.match(text, /plain\(cfg\.scale\.note\)/);
  assert.match(text, /plain\(cfg\.rank_rule\)/);
  // 「保存了不等于已生效」与「后端没交」两句缺数声明留在屏上（抽屉段以外）
  const after = text.slice(text.indexOf('</DocDrawer>'));
  assert.match(after, /保存了不等于已生效/);
  assert.match(after, /后端没交 rank_rule/);
  // 按钮排在指纹/同批徽标这些元信息之前
  assert.ok(text.indexOf('<DocDrawer') < text.indexOf('{dash(cfg?.fingerprint)}'));
});

test('IndicatorGrid：技术指标那格的指路说明在标题行，quiet 一档不长空按钮', () => {
  const text = assertTitleRowDrawers('../src/components/quant/IndicatorGrid.tsx', 1);
  assert.doesNotMatch(text, /<ExplainBox/);
  assert.match(text, /<DocDrawer title=\{title\}>/);
  assert.match(text, /八项能力加权分与它的窗口斜率/);
  // quiet（后端 available:false）那一支标题行整个不画 ⇒ 不会有第二颗/空壳按钮
  assert.ok(text.indexOf('<QuietFold') > text.indexOf('</DocDrawer>'));
});

test('NewHighBoard：出处+慢与缓存+纯口径 caveat 合成标题行一颗，缺数与截断留在屏上', () => {
  const text = assertTitleRowDrawers('../src/components/quant/NewHighBoard.tsx', 1);
  assert.doesNotMatch(text, /<ExplainBox|<CaliberFold/);
  assert.match(text, /<Caveats items=\{cav\.foldable\}/);
  const after = text.slice(text.indexOf('</DocDrawer>'));
  assert.match(after, /<Caveats items=\{cav\.visible\}/, '缺数／截断一族的 caveat 必须仍摊在屏上');
  assert.match(after, /这份数实算于/);
});

test('GroupAnalyticsBoard：趋势图 / 口径 / 不做什么三块各一颗，都在标题行', () => {
  const text = assertTitleRowDrawers('../src/components/quant/GroupAnalyticsBoard.tsx', 3);
  assert.doesNotMatch(text, /<ExplainBox/);
  for (const t of ['各组的分数趋势（这 20 场怎么走过来的）', '这一块的口径', '这一块不做什么']) {
    assert.ok(text.includes(`<DocDrawer title="${t}">`), `缺标题行按钮：${t}`);
  }
  // 嵌着 <GroupChips/> 功能按钮的那段留在屏上，没跟着进抽屉
  assert.match(text.slice(text.indexOf('</DocDrawer>')), /<GroupChips d=\{cur\} \/>/);
  // 没取到数据时「不预先描述算法」那句缺数声明在屏上，且此时抽屉不渲染（不长空按钮）
  assert.match(text, /\{cur\?\.calibers && \(\n\s+<DocDrawer title="这一块的口径">/);
  assert.match(text, /这一块没取到数据时不预先描述算法/);
});

test('StartupVolumeBoard：母体切换那块说明搬进段落标题行，Panel 子树里那两颗按 10-31 裁定不动（11-01 口径那颗并入折叠块）', () => {
  const text = assertTitleRowDrawers('../src/components/quant/StartupVolumeBoard.tsx', 1);
  assert.match(text, /<DocDrawer title="启动前量能分布">/);
  assert.doesNotMatch(text, /<ExplainBox title="为什么这一屏没有合计榜/);
  assert.match(text, /并到一列排名就是拿一个档位的尺子量另一个档位的票/);
  // 按钮排在「重新取数」前面
  assert.ok(text.indexOf('<DocDrawer') < text.indexOf('重新取数'));
  // 其余两颗 ExplainBox 都在 `<Panel>` 子树里（各自带小节名，讲的就是那一段），
  // 11-01「一模块一颗」把第三颗（表尾那句取数口径）并进了 CaliberFold 的 provenance。
  assert.equal((text.match(/<ExplainBox\b/g) ?? []).length, 2);
  assert.match(text, /provenance=\{\[d\.caliber\]\}/, '取数口径那句改由这一颗抽屉交代');
});

test('红线：搬进抽屉的都是口径与读法，三类诚实性文字（缺数/截断/告警）不跟（本轮九个文件全扫）', () => {
  const files = ['../src/pages/SectorHeat.tsx', '../src/pages/Quadrant.tsx', '../src/pages/Screen.tsx',
    '../src/components/SectorFlowCumulative.tsx', '../src/components/VolumeEnergyPanel.tsx',
    '../src/components/quant/IndicatorGrid.tsx', '../src/components/quant/NewHighBoard.tsx',
    '../src/components/quant/GroupAnalyticsBoard.tsx', '../src/components/quant/StartupVolumeBoard.tsx'];
  for (const f of files) {
    const text = src(f);
    for (const b of drawerBodies(text)) {
      assert.doesNotMatch(b, /NoticeBar|role="status"/,
        `${f}：缺数／上限／告警那三类被搬进了抽屉 —— 藏起来等于让屏幕说假话`);
      assert.doesNotMatch(b, /<Caveats items=\{cav\.visible|<CaveatText|items=\{d\.caveats\}/,
        `${f}：留在屏上的那一份 caveat 混进了抽屉体`);
    }
    // 渲染上屏的抽屉正文不许带 markdown 粗体星号（后端散文走 plain）
    for (const b of drawerBodies(text)) {
      for (const m of b.matchAll(/\{([^{}]*\*\*[^{}]*)\}/g)) {
        assert.fail(`${f}：抽屉插值里出现裸 ** 且没走 plain()：${m[1].slice(0, 50)}`);
      }
    }
  }
});

test('中文串里不嵌 ASCII 双引号：本轮新写的抽屉小节标题与插值标题用「」', () => {
  const files = ['../src/pages/SectorHeat.tsx', '../src/pages/Quadrant.tsx', '../src/pages/Screen.tsx',
    '../src/components/SectorFlowCumulative.tsx', '../src/components/VolumeEnergyPanel.tsx',
    '../src/components/quant/IndicatorGrid.tsx', '../src/components/quant/NewHighBoard.tsx',
    '../src/components/quant/GroupAnalyticsBoard.tsx', '../src/components/quant/StartupVolumeBoard.tsx'];
  for (const f of files) {
    const text = src(f);
    // 违规形状：heading="…中文"中文…" —— 内嵌 ASCII 双引号会让属性提前收尾，闭合引号后紧跟汉字
    assert.doesNotMatch(text, /heading="[^"]*"[\u4e00-\u9fff]/,
      `${f}：抽屉小节标题里疑似嵌了 ASCII 双引号（该用「」）`);
  }
});
