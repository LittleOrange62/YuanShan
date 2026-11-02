import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

const panel = readFileSync(new URL('../src/components/ui/DataPanel.tsx', import.meta.url), 'utf8');
const grouped = readFileSync(new URL('../src/pages/Grouped.tsx', import.meta.url), 'utf8');

/**
 * 通用说明框（用户 2026-10-14 点名）：说明与定义折起来、默认收起，屏幕留给读数。
 * 这一份测试钉的是**不许被下一次会话顺手改掉的那两处**：
 * ① 折叠框默认必须是收着的；② caveat 类必须还在屏上 —— 藏起告警等于让屏幕说假话。
 */

test('说明框用的是原生 details，且默认不带 open', () => {
  const box = panel.slice(panel.indexOf('export function ExplainBox'), panel.indexOf('export function Row'));
  assert.ok(box.length > 100, '没找到 ExplainBox');
  assert.match(box, /<details className=/);
  assert.doesNotMatch(box, /<details[^>]*\sopen/, '默认必须是收起状态（用户点名的就是这一条）');
  assert.match(box, /summary/, '没有 summary 就点不开，等于把说明删了');
  assert.match(box, /\$\{count\} 条/, '条数要说出来，人才知道框里有多少东西');
});

test('面板的口径说明走说明出口（一处改，每屏都跟着收）', () => {
  // 2026-10-31 用户点名：那颗按钮要长在标题行最右边，`Panel` 的 note 因此从「正文开头那口 ExplainBox」
  // 换成「标题行右侧那口 DocDrawer」——判据没变（note 绝不平摊在屏上、全站一处出口），只是钉子搬了家。
  assert.match(panel, /\{note && !quiet && \(\s*<span className="ml-auto"><DocDrawer title=\{title\}>\{plain\(note\)\}<\/DocDrawer>/,
    'Panel 的 note 必须经共用的说明抽屉 —— 各屏的 note 都在这一处上屏（10-22 起：空态那一支除外，见下面那条）');
  assert.doesNotMatch(panel, /\{note && !quiet && <p/, 'note 不许退回平摊在屏上（那就是用户点名的"占一行说明文字"）');
  assert.match(panel, /<QuietFold title=\{title\} text=\{plain\(text\)\} note=\{note \? plain\(note\) : undefined\}/,
    '空态那一支里 note 也没丢：它作为 `note` 交给 QuietFold，摆在 summary 之下（bare，不再套一层 details）');
  assert.match(panel, /<ExplainBox title="这些字段是什么意思" count=\{entries.length\} block=\{block\}>/,
    'Definitions 也走同一块，不再自己写一份 details（2026-10-29 起多一个 block 口：卡片外面才有标题的那几块要交块名）');
});

test('右侧 AI 栏那两句逐屏摊着的描述也收进了框（但 caution 留在 summary 上）', () => {
  // 这一栏挂在每一屏上：它头顶那两句在每开一屏都重复一次，是"过多占屏"里最贵的一笔
  //（用户 2026-10-21「类似问题到处都是，全部查一遍」时逐屏抓到）。
  const rail = readFileSync(new URL('../src/components/workspace/AiAnalysisPanel.tsx', import.meta.url), 'utf8');
  assert.match(rail, /import \{ ExplainBox \} from '@\/components\/ui\/DataPanel'/,
    '用共用说明框，不在栏里自己写一份 details');
  assert.match(rail, /<ExplainBox title="这一栏是什么（逐条点了才跑 · 只存本机浏览器 · 风险自担）">/,
    '「风险自担」要写在 summary 上 —— 收起不等于把这句提醒藏掉');
  assert.match(rail, /<ExplainBox title=\{`带的是哪一份材料/,
    '「带的是哪一份材料」那句逐屏重复，收进框；label 仍来自登记点，不自己编');
  assert.match(rail, /本产品不校准、不负责）`}?>/, '不校准、不负责也要在 summary 上');
  assert.ok(rail.indexOf('      </div>') < rail.indexOf('<ExplainBox title="这一栏是什么'),
    '说明框要在栏头那一行**外面**：栏头是 flex 横排，塞进去会和「AI分析」标题挤成一行');
  // 这条栏里的**告警**没被顺手折掉：持仓不登记那句是解释"为什么这里没有输入框"，必须看得见
  const noRegister = rail.slice(rail.indexOf('这一屏没有登记可带的本页材料'), rail.indexOf('这一屏没有登记可带的本页材料') + 600);
  assert.doesNotMatch(noRegister, /ExplainBox|<details/, '空态与红线那句要摊着，折起来等于让人以为栏坏了');
});

test('告警一类没有被折进去', () => {
  // Caveats / CaveatText **组件本身**不含折叠：默认摊在屏上。
  // 用户 2026-10-19 夜点名「四维象限那些内容都默认收缩起来」之后，多了一个**受控的**出口
  // `CaliberFold`（见下面那条）：它可以把「口径声明 / 字段解释 / 覆盖 / 出处」收进一个
  // 默认收起的块，但 summary 必须摆出每一类的条数，且**告警与截断声明依旧不许进**。
  // 所以这条断言管的还是组件自己：`Caveats` 内部不能出现 details，否则每一屏都会静默少一段话。
  const cavs = panel.slice(panel.indexOf('export function Caveats'), panel.indexOf('export function CacheLine'));
  assert.doesNotMatch(cavs, /ExplainBox|<details/, 'caveat 组件自己不许带折叠');
  const one = panel.slice(panel.indexOf('export function CaveatText'), panel.indexOf('export function Definitions'));
  assert.doesNotMatch(one, /ExplainBox|<details/, '单句 caveat 同样不许折');
});

test('CaliberFold 那一处折叠：收的是描述，不收告警，而且要说清有多少条', () => {
  const fold = panel.slice(panel.indexOf('export function CaliberFold'),
    panel.indexOf('/** 一行"标签：值"，缺失一律 `—` */'));
  assert.ok(fold.length > 200, '共用折叠块不在了');
  assert.match(fold, /<summary[\s\S]{0,200}\{/, 'summary 要摆条数，收起不等于藏掉');
  assert.match(fold, /条口径声明|个字段解释|段覆盖区间|出处 /, '每一类都要有计数，不写「（若干）」那种含糊话');
  // 渲染顺序 = 源码顺序：告警那份 Caveats 必须排在 `<details>` **之前**，所以它不可能被折进去
  assert.ok(fold.indexOf('<Caveats items={visible} />') < fold.indexOf('<details'),
    'visible 那一份 Caveats 排到了 details 里面 —— 缺数／截断声明会被藏掉');
  const body = fold.slice(fold.indexOf('<div className="mt-1'));
  assert.match(body, /<Caveats items=\{held\} \/>/, '折叠体里只该有折起来的那一份 caveat');
  assert.doesNotMatch(body, /text-destructive|role="status"/, '折叠体里不许出现告警');
  // 浏览器实测（10-20 夜，/quadrant）抓到过套娃：CaliberFold 自己是一层 details，
  // 里面的 Definitions 又带一层 ⇒ 同一份 25 条字段解释有两行摘要，收起时看着像两个东西。
  assert.match(body, /<Definitions map=\{definitions\} bare \/>/,
    '折叠体里的字段字典必须走 bare（只交内容，摘要由外层那一条 unified 出来）');
  assert.doesNotMatch(body, /<Definitions map=\{definitions\} \/>/,
    '不带 bare 就是把 ExplainBox 套进 CaliberFold');
  assert.doesNotMatch(fold, /popHint/, '弹窗被拦这类页面级告警不该出现在共用折叠块里');
  // 唯一允许的 text-warning 是 summary 上那句「另有 N 条没折，在上方」的指路牌（点开之前就一直看得见）
  const warnBits = fold.split('text-warning').length - 1;
  assert.equal(warnBits, 1, `text-warning 只该出现一次（summary 那句指路牌），实际 ${warnBits} 次`);
  // 10-28 加了抽屉那一版之后，指路牌抽成一个 `guide`（就地折叠与抽屉两个壳共用同一句，
  // 所以屏幕上只可能有一句）。这条钉子管的还是那件事：**点开之前就必须看得见，
  // 而且不许待在折叠体里** —— 位置随壳走，判据不松。
  assert.match(fold, /const guide = /, '指路牌那一句要在，并且只写一份');
  assert.match(fold, /另有 \{visible\.length\} 条/, '要说清"还有几条没收进来"，否则收起会被读成"这里什么都没有了"');
  const foldBody = fold.slice(fold.indexOf('const body = ('), fold.indexOf('const tallyBits'));
  assert.ok(foldBody.length > 60, '折叠体那一段没取到（CaliberFold 的结构变了，这条要跟着改位置，不是删掉）');
  assert.doesNotMatch(foldBody, /\{guide\}|text-warning/, '指路牌不许落到折叠体里：收起来就读不见了');
  assert.match(fold.slice(fold.indexOf('return (')), /\{guide\}/,
    '两个壳都要在摘要行／说明按钮旁边把它摆出来（点开之前就看得见）');
  // 用户点名的那一屏确实改走这一处（三块：选股象限 / 名次稳定榜 / 名次稳步前进榜）
  for (const f of ['../src/pages/Quadrant.tsx',
    '../src/components/quant/RankStabilityBoard.tsx',
    '../src/components/quant/SteadyRankBoard.tsx']) {
    assert.match(readFileSync(new URL(f, import.meta.url), 'utf8'), /<CaliberFold/,
      `${f} 没有接共用折叠块`);
  }
});

test('分组卡片一行一个组', () => {
  assert.match(grouped, /<ul className="mt-2 grid gap-1">/, '不许再出现 sm:grid-cols-2（一行塞两个组）');
  assert.doesNotMatch(grouped, /grid gap-1 sm:grid-cols-2/);
  // 排序说明里的"第一行第一个/按行从左到右填"是两列时代的说法
  assert.doesNotMatch(grouped, /第一行第一个|按行从左到右/, '一行一个组之后，这话就是错的');
});

test('分组自选那一屏：说明并到标题行那一颗按钮，截断与告警仍摊在表下（用户 10-31 一模块一颗）', () => {
  // 10-31 第 1、2 条（个股组）：这一块原来一张卡里四颗同名「这块的说明」（Panel note + 正文两只
  // ExplainBox + CaliberFold），说明散在正文里像"数下面的一段话"。现在自选明细自己写标题行：
  // 唯一一颗按钮在 h2 那一行最右，四段说明并进它的抽屉（`DrawerSection` 分节，句子原样搬）。
  // 判前先剥注释 —— 注释里写着 ExplainBox/CaliberFold 是历史说明，不是调用点。
  const code = grouped.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.doesNotMatch(code, /<ExplainBox\b|<CaliberFold\b/,
    '正文里不许再长第二、第三颗说明按钮（自选明细的抽屉在标题行）');
  assert.match(code, /<DocDrawer title=\{open == null \? "自选明细（全部组）" : "自选明细（所选组）"\}/,
    '抽屉标题跟着屏上那一行 h2 走，两处不许分叉');
  for (const h of ['日均与区间 · 操作分怎么读', '字段义 · 操作分与象限怎么来的 · 单位',
    '这一张表怎么点 · 双击去哪里', '出处']) {
    assert.ok(code.includes(`heading="${h}"`), `标题行那颗抽屉里少了这一段：${h}`);
  }
  // 截断那一句（先排序再截 40 行）与弹窗被拦的告警仍在屏上：JSX 的先后就是渲染的先后，
  // 它们在 `</table>` 之后 —— 而整口抽屉在 `<Panel` 之前，落进抽屉就等于把上限与告警藏了。
  const trunc = code.indexOf('再截前 40 行');
  const drawer = code.indexOf('</DocDrawer>');
  assert.ok(trunc > 0 && drawer > 0 && trunc > drawer, '截断声明不许落在说明抽屉里');
  assert.match(code, /<NoticeBar kind="truncated">/, '上限那一句仍走三型出口');
  assert.match(code, /<NoticeBar kind="warning">\{popHint\}<\/NoticeBar>/, '弹窗被拦的告警仍摊在表下');
});

test('全站不许出现"折叠框里藏着 caveat／告警"的写法', () => {
  let boxes = 0;
  for (const dir of ['../src/pages/', '../src/components/']) {
    const found = readdirSync(new URL(dir, import.meta.url), { recursive: true, encoding: 'utf8' } as never) as string[];
    for (const rel of found.filter(f => f.endsWith('.tsx'))) {
      const src = readFileSync(new URL(dir + rel.replace(/\\/g, '/'), import.meta.url), 'utf8');
      for (const m of src.matchAll(/<ExplainBox[\s\S]{0,4000}?<\/ExplainBox>/g)) {
        boxes += 1;
        assert.doesNotMatch(m[0], /<Caveats |<CaveatText |text-warning|text-destructive/,
          `${rel}：说明框里出现了告警 —— 告警必须默认在屏上`);
      }
    }
  }
  assert.ok(boxes >= 15, `只扫到 ${boxes} 个说明框：全站那一轮收编大概没落地`);
});

/** 2026-10-19 夜用户点名「把选项留出，描述内容都收缩起来」后补的自查闸：
 *  这几块把裸渲染的"怎么点"描述收进了默认收起的说明框，而截断声明与 caveat 留在框外。
 *  2026-10-31 用户再裁定：那颗按钮要摆在这一块标题行的最右边 —— 这一屏的 `ExplainBox`
 *  与 `CaliberFold` 的抽屉体一起搬进标题行那颗 `DocDrawer`，三类诚实性文字仍留在屏上。 */
test('10-19 夜「描述收缩」自查（自选股新高）· 10-31 搬到标题行：说明按钮在标题行，缓存年龄/截断/告警留在抽屉外', () => {
  const nh = readFileSync(new URL('../src/components/quant/NewHighBoard.tsx', import.meta.url), 'utf8');
  const boxStart = nh.indexOf('<DocDrawer title="自选股新高（库内，不是上市以来）"');
  const boxEnd = nh.indexOf('</DocDrawer>', boxStart);
  assert.ok(boxStart > 0 && boxEnd > boxStart, '新高榜的说明要在标题行那颗按钮里（10-31 起不再是正文里的 ExplainBox）');
  assert.ok(boxStart > nh.indexOf('<h2 className="text-sm font-bold">自选股新高'),
    '说明按钮就在标题这一行、紧跟 h2');
  assert.ok(boxEnd < nh.indexOf('<label className="text-[11px] text-muted-foreground" htmlFor="nh-group">'),
    '抽屉整段排在范围/窗口控件之前 —— 它是标题行最右边那一簇');
  const box = nh.slice(boxStart, boxEnd);
  assert.match(box, /\{STOCK_ROW_HINT\}/, '"怎么点"那句在抽屉里');
  assert.doesNotMatch(box, /popHint|text-warning|text-destructive/, '告警不进抽屉');
  assert.match(box, /<Caveats items=\{cav\.foldable\}/, '只有纯口径一族的 caveat 允许进抽屉（分类只住 splitCaveats）');
  const after = nh.slice(boxEnd);
  assert.match(after, /取出 \/ 截断/, '「取前 N 行、没列出来的是更远不是没算」那行截断声明渲染在抽屉之外');
  assert.match(after, /这份数实算于/, '缓存年龄声明不许折');
  assert.match(after, /库内新高/, '「不是上市以来新高」这句防误读的话不许折');
  assert.match(after, /<Caveats items=\{cav\.visible\}/, '缺数／截断一族仍摊在屏上');
  assert.ok(after.indexOf('x.popHint') > 0, '弹窗被拦的告警留在说明抽屉外面');
});
