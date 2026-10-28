import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const rd = (p: string) => fs.readFileSync(new URL(`../src/${p}`, import.meta.url), 'utf8');
const rdPy = (p: string) => fs.readFileSync(new URL(`../../${p}`, import.meta.url), 'utf8');

/**
 * 用户 2026-10-14：「重点关注（持仓）这里能选分组，不能选个股，你这个功能没做好」。
 *
 * 「显示范围」下拉原本只有 `all` / `watchlist` / `group:<id>` 三档 —— 分组能选，一只票选不了。
 * 这一轮加第四档 `code:<6 位裸码>`，候选先取当前持仓，并且**留一格手输**：这台机器此刻
 * `holdings.length = 0`（2026-10-14 现打 `/api/positions` 实测），只列持仓的话这一档永远是空的，
 * 等于没做。这几条钉的是五件容易写歪的事：
 *  ① 候选先取持仓，代码统一取 6 位（持仓那边带 `.SH`，档位值不带）；
 *  ② 这一档**不依赖自选名单读取**，那句"自选名单没读到"不许套在它头上；
 *  ③ 当前那一只不在持仓里时仍列在候选里并标明，否则 select 显示成空白、读成"范围没生效"；
 *  ④ 空表那一屏点名是"这一只没仓"，不套分组那一档的话；
 *  ⑤ 前后端认同一个形状（`duanxian/ui_pref.py` 的 pattern 是唯一那道门）。
 */

const PAGE = rd('pages/Portfolio.tsx');
const PREF = rdPy('duanxian/ui_pref.py');

test('第四档在下拉里，候选先取当前持仓；持仓为空时还有手输那一格', () => {
  assert.match(PAGE, /<optgroup label=\{`只看某一只（当前持仓 \$\{allHoldings\.length\} 只）`\}>/,
    '这一档的标题带"当前持仓 N 只"，人才知道列的是谁');
  assert.match(PAGE, /value=\{`code:\$\{s\.code\}`\}/, '值形如 code:600519，与 ui_pref 登记的那一支对得上');
  const opt = PAGE.match(/const stockOptions = useMemo\(\(\) => \{[\s\S]*?\}, \[[^\]]*\]\);/)![0];
  assert.match(opt, /for \(const h of allHoldings\)/, '候选来自持仓');
  assert.match(PAGE, /（账上此刻没有未平仓的票 —— 用旁边那一格搜一只，代码 \/ 名称 \/ 拼音首字母都行）/,
    '持仓 0 只时下拉里要说一句为什么这一档是空的，而不是留一个空 optgroup');
  // 10-15：这一格换成全站共用的 StockSearch（可搜代码/名称/拼音首字母），兜底入口这件事没变，
  // 只是措辞与控件换了宿主 —— 断言跟着改读取形状，**不是放宽**。
  assert.match(PAGE, /ariaLabel="只看某一只：输入代码、名称或拼音首字母"/, '这一档的兜底入口就是那只共用控件');
  assert.match(PAGE, /<StockSearch value={draftCode} kinds="stock,etf"/, '必须用共用控件，不在这页另写一份过滤');
  assert.match(PAGE, /onClick=\{pickTyped\}/, '点「看这一只」走同一个 pickScope，不另开一条存法');
});

test('手输的判据与后端正对，且不把注定被拒的值发出去', () => {
  const typed = PAGE.match(/const pickTyped = \(\) => \{[\s\S]*?\};/)![0];
  assert.match(typed, /replace\(\/\\D\/g, ""\)\.slice\(0, 6\)/, '非数字剔掉、只取 6 位');
  assert.match(typed, /6 位代码；想按名称或拼音首字母找/, '夹不住就在屏上说收到的是什么，并给出下一条走得通的路');
  assert.match(typed, /当前范围没动/, '输了个错的：不能说成"这次没记住"（那是"存不住"那句话）');
  assert.match(typed, /void pickScope\(`code:\$\{c\}`\)/, '夹过了才走 pickScope（写库还是那一条路）');
});

test('这一档不看自选名单：筛子在读名单之前就把它拦下来', () => {
  const codes = PAGE.match(/const scopeCodes[^=]*= useMemo\(\(\) => \{[\s\S]*?\}, \[[^\]]*\]\);/)![0];
  const atCode = codes.indexOf('scope.startsWith("code:")');
  const atWatch = codes.indexOf('watchState.kind !== "ok"');
  assert.ok(atCode > 0 && atWatch > 0, '两处判断都在这个 memo 里');
  assert.ok(atCode < atWatch, 'code: 要排在"名单没读到"之前，否则会被那句假原因盖掉');
  assert.match(codes, /return new Set\(\[code6\(scope\.slice\(5\)\)\]\)/, '筛子就是那一只');
});

test('不在持仓里的那一只仍列出来并标明，空表那句点的是它', () => {
  assert.match(PAGE, /if \(saved && !out\.has\(saved\)\) out\.set\(saved, \{ code: saved, name: nameOfCode\(saved\), gone: !!data \}\)/,
    '持仓还没读回来时不标"不在"——那时分不清是不在了还是还没看到');
  assert.match(PAGE, /（不在当前持仓）/, '标明这件事写在 option 文本里');
  const empty = PAGE.match(/shown\.length === 0 \? \([\s\S]*?\n        \) : \(/)![0];
  assert.match(empty, /当前持仓里没有/, '点名是"这一只不在持仓里"');
  assert.match(empty, /scope\.startsWith\("code:"\)/, '这一档走自己那句，且排在 allHoldings.length 判据之前');
  assert.match(empty, /没有未平仓的交易/, '账上真的一只没有时那一句还要出');
});

test('前后端认同一个形状：pattern 是唯一那道门', () => {
  const py = PREF.match(/"positions\.scope": \{[\s\S]*?\},/);
  assert.ok(py, 'positions.scope 仍在登记清单里');
  assert.match(py![0], /code:\[0-9\]\{6\}/, '后端收 code:<6 位数字>（不收字母、不收 5 位、不收带后缀）');
  assert.match(py![0], /group:\[1-9\]\[0-9\]\{0,5\}/, '原来那三档一个不许掉');
  assert.match(PAGE, /const code6 = \(v: unknown\) => String\(v \?\? ""\)\.slice\(0, 6\)/,
    '代码统一取前 6 位（持仓带 .SH 后缀，档位值不带）');
});

test('上次存的分组被同步删了，下拉仍给它一个说清来历的 option', () => {
  // 这一条是这一屏自己文档里挂了三天、标着「未实测」的那个洞（select 匹配不到 option ⇒ 显示空白，
  // 读成"范围没生效"）。个股那一档顺手把同一种写法用到了分组上。
  assert.match(PAGE, /const groupGone = useMemo/, '判"这一组还在不在名单里"只住一处');
  assert.match(PAGE, /if \(!scope\.startsWith\("group:"\) \|\| groupsState\.kind !== "ok"\) return null/,
    '名单没读回来时不宣布"已删除"——那是"还不知道"，不是"没了"');
  assert.match(PAGE, /（自选分组 #\{groupGone\} 已不在名单里/, 'option 文本要说清是谁删的');
});

test('口径里写清这几档互斥、以及个股那一档列的是谁', () => {
  assert.match(PAGE, /这几档是\*\*互斥的一档\*\*（下拉单选）/, '互斥要说出来，不然人会去找"叠筛"的开关');
  assert.match(PAGE, /候选\*\*先列当前持仓里真有的那些\*\*/, '列的是持仓，手输只是兜底');
  assert.match(PAGE, /范围已存库/, '写了还要读回，读回才说"已记住"（这条本来就在，不许退）');
});
