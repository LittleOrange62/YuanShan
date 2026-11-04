import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

/**
 * 说明抽屉 + 三类声明这两个公共组件的形状钉子（2026-10-28 用户定稿）。
 * 钉的都是"写错了不报错、屏幕上却出问题"那一类：被父卡片裁掉、Esc 关不掉、
 * 以及最危险的一条 —— 把该留在屏幕上的缺数／截断／告警折进弹窗里藏起来。
 */
const DRAWER = fs.readFileSync(new URL('../src/components/ui/DocDrawer.tsx', import.meta.url), 'utf8');
const NOTICE = fs.readFileSync(new URL('../src/components/ui/NoticeBar.tsx', import.meta.url), 'utf8');

test('DocDrawer：portal 到 body、Esc 与 X 都能关、关掉把焦点还给按钮', () => {
  assert.match(DRAWER, /createPortal\(/, '就地渲染会被带 overflow 的卡片裁掉半截');
  assert.match(DRAWER, /document\.body\,?\s*\)/, 'portal 目标必须是 body');
  assert.match(DRAWER, /e\.key === 'Escape'/, 'Esc 必须能关（键盘用户不该只有一只鼠标可点）');
  assert.match(DRAWER, /trigger\.current\?\.focus\(\)/, '关闭后焦点要回到那个说明按钮');
  assert.match(DRAWER, /aria-expanded=\{open\}/);
  assert.match(DRAWER, /aria-modal="false"/,
    '这是"边看图边读说明"的非模态抽屉：做成模态＋锁滚动等于逼读者二选一');
  assert.doesNotMatch(DRAWER, /overflow:\s*hidden|document\.body\.style\.overflow/,
    '不许锁页面滚动');
});

test('DocDrawer：扁长形状就是用户要的那只（下方 40vh、横向占满）', () => {
  assert.match(DRAWER, /h-\[40vh\]/);
  assert.match(DRAWER, /inset-x-0 bottom-0/);
  assert.match(DRAWER, /overflow-y-auto overscroll-contain/, '内容长要能滚，且滚到底不许把页面一起带走');
});

test('NoticeBar：该事实不存在就整条不渲染，也不画空框', () => {
  assert.match(NOTICE, /if \(!when \|\| children == null/, '条件为假必须彻底消失，不许留一条"今天没问题"');
  assert.match(NOTICE, /const live = items\.filter\(x => x\.when !== false/, 'NoticeList 同一条规则');
  assert.match(NOTICE, /if \(!live\.length\) return null/);
  // 三种类型是定稿的那三种，多一种少一种都要先改这里（免得有人往里塞"解释性长文"）
  assert.match(NOTICE, /export type NoticeKind = 'missing' \| 'truncated' \| 'warning'/);
});

test('红线：三类声明与说明抽屉互不相干（诚实性文字不许被搬进弹窗）', () => {
  // 只看代码，不看注释：DocDrawer 的注释里本来就要写明「这三类走 NoticeBar」，
  // 按整份文本比会把解释本身当成违规（`lazySection.test.ts` 同一条教训）。
  assert.doesNotMatch(DRAWER, /import .*NoticeBar|<NoticeBar\b|<NoticeList\b/,
    '抽屉里渲染声明＝把缺数／上限／告警藏起来，那是让页面说假话');
  assert.doesNotMatch(NOTICE, /import .*DocDrawer|<DocDrawer\b|createPortal/,
    '声明自己不该有折叠形态：它永远留在块内');
});

// ===== 2026-10-29 整理这一轮补的三条 =====

const PANEL = fs.readFileSync(new URL('../src/components/ui/DataPanel.tsx', import.meta.url), 'utf8');
const CARD = fs.readFileSync(new URL('../src/components/ui/GlassCard.tsx', import.meta.url), 'utf8');

function allTsx(): { path: string; text: string }[] {
  const out: { path: string; text: string }[] = [];
  const walk = (dir: URL) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = new URL(e.name + (e.isDirectory() ? "/" : ""), dir);
      if (e.isDirectory()) { if (e.name !== "node_modules") walk(p); }
      else if (e.name.endsWith(".tsx")) out.push({ path: p.pathname, text: fs.readFileSync(p, 'utf8') });
    }
  };
  walk(new URL('../src/', import.meta.url));
  return out;
}

test('NoticeBar 是活的：告警行要用 role=status（读屏的人要被告知）', () => {
  assert.match(NOTICE, /<p role="status"/, '声明条必须是 live region，原来散着写的那 27 行都有');
  // 逐块原来靠 `text-warning` 上色，收进组件后这条颜色语义只能在这里钉住
  assert.match(NOTICE, /warning: '[^']*text-warning/, '告警那一型必须是警示色，不许念成普通小字');
});

test('抽屉标题不许组件自己编：由块标题往下发', () => {
  assert.match(DRAWER, /export const BlockTitleCtx/, '标题口在这一个文件');
  assert.match(PANEL, /<BlockTitleCtx\.Provider value=\{title\}>/,
    '走 Panel 的每一块自动把屏上那行 h2 发下去');
  assert.match(CARD, /docTitle/, '不走 Panel 的那几块（首页带 h3 的）用这个口');
  // 兜底那句只能出现一次（`CaliberFold` 里），不许逐块各写一个「这一屏」
  assert.equal((PANEL.match(/"这一屏"/g) ?? []).length, 1, '兜底标题不许被逐块抄成第二份');
});

test('同一时刻只留一口抽屉（点第二块不许把第一块叠在下面）', () => {
  assert.match(DRAWER, /let openDrawer/, '模块级记着当前开着的那口');
  assert.match(DRAWER, /if \(prev && prev !== myClose\.current\) prev\(\);/,
    '新开的先收掉旧的 —— 抽屉贴屏底，两口叠在一起时挡住的那口既看不见也点不着');
});

test('全站每一口抽屉都有真名：说明出口的标题来源逐个核（用户 10-31 后正文出口搬进标题行，DocDrawer 那颗也算）', () => {
  const files = allTsx();
  const missing: string[] = [];
  let sites = 0;
  for (const f of files) {
    // 10-31 定形后，一块的说明出口可以是正文里的 ExplainBox/CaliberFold/Definitions，
    // 也可以是标题行那一颗 DocDrawer（后者必须交 `title=`）—— 都数进来，少一处就是有一块的说明被拆掉了。
    const calls = (f.text.match(/<(ExplainBox|CaliberFold|Definitions|DocDrawer)\b/g) ?? []).length;
    if (!calls) continue;
    if (f.path.includes('/components/ui/')) continue;   // 出口自己不算调用点
    sites += calls;
    // 标题的来源：Panel 的 h2、GlassCard 的 docTitle、显式 block，或 DocDrawer 直接交的那一句 title=
    const 有来源 = f.text.includes('<Panel') || f.text.includes('docTitle') || f.text.includes('block=')
      || /<DocDrawer title=/.test(f.text);
    if (!有来源) missing.push(`${f.path.split('/').pop()}（${calls} 处）`);
  }
  // 旧例外作废：QdCompare 那处 ExplainBox 已改成标题行那颗 DocDrawer（自己交 title），不再需要白名单。
  assert.deepEqual(missing, [], `这些文件的抽屉只能念兜底的「这一屏」：${missing.join('、')}`);
  assert.ok(sites >= 120, `说明出口调用点数只有 ${sites} 个，比上一轮少了一个以上 ⇒ 有块被拆掉了`);
});

test('三型声明都在册：缺数／上限／告警各有调用点（少一型就是有人把某一型又改回散着写）', () => {
  const files = allTsx();
  const count = (kind: string) => files.reduce((n, f) => n + (f.text.match(new RegExp(`<NoticeBar kind="${kind}"`, 'g')) ?? []).length, 0);
  const list = files.reduce((n, f) => n + (f.text.match(/<NoticeList\b/g) ?? []).length, 0);
  assert.ok(count('warning') >= 20, `告警型只剩 ${count('warning')} 处`);
  assert.ok(count('missing') >= 3, `缺数型只剩 ${count('missing')} 处`);
  assert.ok(count('truncated') >= 2, `上限型只剩 ${count('truncated')} 处`);
  assert.ok(list >= 1, '一批条件声明（NoticeList）也得在册');
});

test('弹窗被拦那句全站收成 NoticeBar 一种形状（散着的 role=status 写法归零）', () => {
  const files = allTsx();
  const old = files.filter(f => /\.popHint && <p role="status"/.test(f.text) || /popHint && <p role="status"/.test(f.text));
  assert.deepEqual(old.map(f => f.path.split("/").pop()), [],
    '告警散着写迟早出现"措辞是告警、配色是普通小字"那一族（台账 §15 那条）');
  const wrapped = files.filter(f => /<NoticeBar kind="warning">\{[\w.]+\.popHint\}<\/NoticeBar>/.test(f.text));
  assert.ok(wrapped.length >= 20, `popHint 走公用组件的文件数：${wrapped.length}`);
});

test('10-29 补：NoticeBar 是出口，字符串 children 过 plain()，JSX 那种不动（后端散文带成对粗体）', () => {
  // 实测漏过一次：`/first-board` 上「涨停分析」念后端 `caliber_note`，屏上出现「那些说的都是**全场**」。
  // 那条句子是后端的散文（不是数据值），按全站纪律应在**共用出口**剥成对粗体，
  // 而不是逐块各 `plain()` 一遍（`test/abnormalMoveReads.test.ts:235` 钉的就是"别两处剥"）。
  assert.match(NOTICE, /typeof children === 'string' \? plain\(children\) : children/,
    '字符串才剥：调用方自己排的 JSX 富文本不能被当字符串处理');
  assert.match(NOTICE, /import \{ plain \} from '@\/lib\/agent'/);
  assert.match(NOTICE, /<span className="min-w-0">\{text\}<\/span>/, '剥过的那份才上屏，不许下面又用一次原始 children');
  // 数据值不许被剥（星号可能是词本身，如凭据掩码 ******）—— 这条与 firstBoardDaily 同一条红线
  assert.doesNotMatch(NOTICE, /plain\(\s*(String\()?[a-zA-Z_$][\w$]*\.[a-z_]+/, '出口只处理传进来的那段文字，不去碰字段值');
});

test('10-29 再补：抽屉那一行标题不许念成「…的说明的说明」（后缀只在一处补，且结尾已是「说明」就不补）', () => {
  // 实测丑话：`Panel` 那颗固定叫「这一屏的口径说明」，与组件补的后缀拼在一起就成了
  // 「左：日线 中：周线 右：月线 · 这一屏的口径说明的说明」，全站 124 处一起念错。
  assert.match(DRAWER, /export const docHeading/, '拼法收成一个导出的函数，别逐处各拼一遍');
  assert.match(DRAWER, /t\.trim\(\)\.endsWith\('说明'\)/, '结尾已经是「说明」就不再补第二遍');
  assert.match(DRAWER, /const heading = docHeading\(title\)/);
  // 四个出口都用拼好的那一份：悬停、dialog 的 aria-label、抽屉里那行 h2、关闭按钮的 aria-label
  assert.match(DRAWER, /title=\{open \? `收起\$\{heading\}` : `展开\$\{heading\}（口径 · 字段解释 · 算例）`\}/);
  assert.match(DRAWER, /aria-label=\{heading\}/);
  assert.match(DRAWER, /font-semibold">\{heading\}<\/h2>/);
  assert.match(DRAWER, /aria-label=\{`关闭\$\{heading\}`\}/);
  // 旧的两种拼法必须一起退场：`${title}` 这个插值**只许出现在 docHeading 那一处**（组件里别处再拼一遍＝两个真源）
  assert.equal((DRAWER.match(/\$\{title\}/g) ?? []).length, 1,
    '`  ${title}  拼法只许住在 docHeading 里；渲染处出现第二次就是有人又手拼了一遍');
});

test('11-01 补：一块里多处出口时按钮不许都叫同一个名字（带小节名，取不到才用默认串）', () => {
  // 实测 `/stock-review`：量化档案一块 3 颗、窗口趋势 4 颗、相似样本 4 颗同名「这块的说明」，
  // 四颗同名按钮摆一起时读者只知道"这里有说明"、不知道点哪颗是自己要的那一段。
  assert.match(DRAWER, /export function docLabelFor/, '取法收成一个函数，别逐块各写一个 label');
  assert.match(DRAWER, /const shownLabel = docLabelFor\(title, useBlockTitle\(\), label\)/,
    '小节名从「块名 · 那一节」那个现成拼式里拿（块名由 Panel/GlassCard 往下发）');
  assert.match(DRAWER, /\{shownLabel\}/, '渲染的必须是算过的那一份');
  assert.doesNotMatch(DRAWER, /label = DEFAULT_DOC_LABEL/, '默认值不许再写在参数上——那样小节名永远拼不进去');
});

test('11-01 再补：CaliberFold 那颗默认标题要带节名，否则和 Panel 标题行那颗同名（实测过 3 张卡各撞 3 颗）', () => {
  const PANEL = fs.readFileSync(new URL('../src/components/ui/DataPanel.tsx', import.meta.url), 'utf8');
  assert.match(PANEL, /drawerTitle \?\? \(blockTitle \? `\$\{blockTitle\} · 口径与覆盖` : "这一屏"\)/,
    '没交 drawerTitle 时补「· 口径与覆盖」；按钮文字由 docLabelFor 把这一节拼上去');
  // 他们那条"兜底那句只许出现一次"的闸不许被我这条改动顶掉
  assert.equal((PANEL.match(/"这一屏"/g) ?? []).length, 1, '「这一屏」兜底串全站只住一处');
});

test('11-01 再补：节名当按钮文字要短得下来（自己起名的那种也认，切掉括号与后半句，太长就退回默认）', () => {
  // `/grouped` 那块里三处出口是自己起的名字（「这一块的口径」「这一块不做什么」「各组的分数趋势（这 20 场怎么走过来的）」），
  // 它们不等于块名 ⇒ 前一次只认「块名 · 那一节」的写法会把三颗都退回同名「这块的说明」（实测 7 颗同名）。
  assert.match(DRAWER, /section = section\.split\(\/\[\uff08\(：:；;·\]\/\)\[0\]\.trim\(\);/,
    '括号、冒号、分号、中点之后的补充都切掉');
  assert.match(DRAWER, /section\.length > 12/, '长过 12 个字就退回默认串：一长串按钮文字会把标题行挤成两行，比同名更难读');
  assert.match(DRAWER, /title\.trim\(\) !== block\.trim\(\)/, '自己起了名（不等于块名）就用它当节名');
});

test('11-01 补：一块里不许「标题行一颗按钮 ＋ 正文又一颗」并存（用户第 4 条：一个模块一颗说明按钮）', () => {
  // 实测过两次：/stock-review 的量化档案 2 颗同名、全站 15 块「note 在标题行、CaliberFold 在正文」。
  // 读者看到两颗都叫「这块的说明」，不知道点哪颗才是这一段 —— 现在把 note 那句话搬进正文那颗抽屉的第一段。
  const bad: string[] = [];
  for (const f of allTsx()) {
    if (f.path.includes('/components/ui/')) continue;
    const lines = f.text.split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
      if (!/<Panel[\s(>]/.test(lines[i])) continue;
      let depth = 0, end = -1;
      for (let k = i; k < lines.length; k++) {
        depth += (lines[k].match(/<Panel[\s(>]/g) || []).length - (lines[k].match(/<\/Panel>/g) || []).length;
        if (depth === 0 && k > i) { end = k; break; }
      }
      if (end < 0) continue;
      // `note=` 常写在参数列表更下面（`slowHint` / `controls` 那一叠 JSX 之后），只看前 16 行会漏 ——
      // 11-02「名次稳步前进榜」那颗藏到第 40 行的 note 就是这么溜过去的。
      // 所以取到正文回调（`{d => …}`）之前那一段当「标题行参数区」。
      let headEnd = -1;
      for (let k = i; k < end; k++) {
        if (/^\s*\{[\w$]+\s*=>/.test(lines[k])) { headEnd = k; break; }
      }
      if (headEnd < 0) headEnd = Math.min(i + 40, end);
      const head = lines.slice(i, headEnd).join('\n');
      const inner = lines.slice(headEnd, end).join('\n');
      const outlets = (inner.match(/<CaliberFold|<ExplainBox|<DocDrawer/g) || []).length;
      if (/(^|[\s])note=/.test(head) && outlets >= 1) {
        bad.push(`${f.path.split('/').pop()}:${i + 1}（note ＋ 正文 ${outlets} 处）`);
      }
    }
  }
  assert.deepEqual(bad, [], `这些块又是两颗按钮并排（点了两次才看全）：${bad.join('、')}`);
});
