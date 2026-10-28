import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

/**
 * 名次稳定榜（近 3/7/11 场全市场名次之和从低到高、前 50）与
 * 「单击交叉高亮 + 双击开个股深度复盘」这一份通用交互实现。
 *
 * 用户 2026-10-15 两段话各钉一族：
 *  · 「四维象限每天计算出来的个股的全市场排名…我需要你记录，并且再加一块功能点，
 *    就是显示近3天、7天、11天的名次之和。按照和的总和从低到高显示前50位」
 *  · 「这边的个股都支持单击高亮行和列，双击跳转到个股深度复盘的新页面 ——
 *    这个也应该是一个全软件的通用脚本或者插件或者代码块哦。但凡有用到的地方，都应该引用他才对」
 *
 * 所以这里钉的是**接线**，不是判据（判据在 `duanxian/quant.py::rank_stability`，
 * 由 `tests/test_rank_stability.py` 对真库钉）：
 *  ① 表体只有一份实现，四维象限那一屏是调用方（留第二份就是两份必漂）；
 *  ② 列头 `sortKey` 与格子读的字段**逐列同序同名**（写错不报错、只是那一列永不亮）；
 *  ③ 窗口只有 3/7/11 三档，页面不出现第四档，也不自己算和；
 *  ④ 分母与"被排除多少只"上屏（`excluded_partial`），0 行的那句话说的是"场次没读全"不是"市场没票"；
 *  ⑤ 交互只有 `lib/useStockRowInteraction.ts` 一份：全站任何页面里不许再出现第二个 `window.open`
 *     去开复盘页，也不许再有页面自己 `useState` 一份弹窗告警；
 *  ⑥ 弹窗被拦是**告警**，必须渲染在默认收起的说明框外面（藏在折叠框里等于没写）。
 */

const board = readFileSync(new URL('../src/components/quant/RankStabilityBoard.tsx', import.meta.url), 'utf8');
const page = readFileSync(new URL('../src/pages/Quadrant.tsx', import.meta.url), 'utf8');
const hook = readFileSync(new URL('../src/lib/useStockRowInteraction.ts', import.meta.url), 'utf8');
const system = readFileSync(new URL('../src/pages/System.tsx', import.meta.url), 'utf8');

const srcFiles = (dir: string, out: string[] = []): string[] => {
  for (const e of readdirSync(new URL(dir, import.meta.url), { withFileTypes: true })) {
    const rel = `${dir}${e.name}`;
    if (e.isDirectory()) srcFiles(`${rel}/`, out);
    else if (/\.(ts|tsx)$/.test(e.name)) out.push(rel);
  }
  return out;
};
const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), 'utf8');

test('名次稳定榜的表体只有一份实现，四维象限那一屏是调用方', () => {
  assert.match(page, /import \{ RankStabilityBoard, type RankStabilityData \} from "@\/components\/quant\/RankStabilityBoard"/);
  assert.match(page, /<RankStabilityBoard state=\{rs\} days=\{rsDays\}/);
  assert.doesNotMatch(page, /名次稳定榜（近 N 场/, '标题只在组件里有一份（页面再写一遍就是两份，改标题只会改到一处）');
  assert.doesNotMatch(page, /rank_sum/, '本屏不许留一份自己的格子（那就是第二张表体）');
  // 一只票一份数：页面不重算和、不重算平均
  assert.doesNotMatch(page, /reduce\([^)]*rank_sum/, '名次之和是后端算的，页面加一遍就是两套口径');
});

test('接口路径不带 /api 前缀，参数只有那三档窗口与前 N', () => {
  assert.match(page, /useProbe<RankStabilityData>\("\/quant\/rank-stability"/,
    '页面里写 /api 会被 probe 再补一次前缀（拼出 /api/api/…）');
  assert.match(page, /\{ days: rsDays, limit: 50, refresh: rsFresh \? 1 : undefined \}/,
    '「清缓存重算」必须真的把 refresh 带出去（10-05 那一族缺陷：文案承诺看新数、请求却不带 refresh）');
  assert.match(board, /export const RANK_WINDOW_OPTIONS = \[3, 7, 11\]/);
  assert.match(page, /const \[rsDays, setRsDays\] = useState\(7\)/);
  const n = (board.match(/RANK_WINDOW_OPTIONS/g) ?? []).length;
  assert.ok(n >= 2, `窗口档位只该有一处定义、一处遍历（现在出现 ${n} 次）`);
});

test('列头 sortKey 与格子读的字段逐列同序同名', () => {
  const cols = [...board.matchAll(/\["([a-z_]+)", "([^"]*)"/g)].map(m => m[1]);
  assert.deepEqual(cols, [
    'board_rank', 'security_code', 'name', 'rank_sum', 'avg_rank', 'best_rank',
    'worst_rank', 'rank_spread', 'latest_rank', 'rank_path', 'quadrant', 'oper_value'],
    `判据列清单变了要连文档一起改：${cols}`);
  const cells = [...board.slice(board.indexOf('<tbody>'), board.indexOf('</tbody>'))
    .matchAll(/x\.cellProps\(i, "([a-z_]+)"/g)].map(m => m[1]);
  assert.deepEqual(cells, cols, `格子与列头不同序 ⇒ 点那一列不报错、但永不亮：${cells}`);
});

test('分母、被排除的只数、0 行那句话都上屏', () => {
  assert.match(board, /excluded_partial/, '缺场次被排除多少只必须交出并上屏（否则"和"会被读成全市场的数）');
  assert.match(board, /凑满 \$\{dash\(d\.window\?\.days\)\} 场/);
  assert.match(board, /这一窗有打分的共 \$\{dash\(u\.scored_codes\)\} 只代码/);
  assert.match(board, /不是市场没有票/, '0 行的原因是这一窗场次没读全，不许念成市场里没票');
  assert.match(board, /is_trading_days/, '「近 N 天」按交易日数这件事要上屏');
});

test('弹窗被拦的告警渲染在收起的说明框外面', () => {
  const box = board.slice(board.indexOf('<ExplainBox'), board.indexOf('</ExplainBox>'));
  assert.doesNotMatch(box, /role="status"/, '告警折进默认收起的说明框等于没写');
  assert.doesNotMatch(box, /popHint/);
  assert.match(board, /\{x\.popHint && <p role="status" className="[^"]*text-warning"/);
});

/** 用户 2026-10-19 夜：「把选项留出，描述内容都收缩起来」——
 *  稳定榜同一刀分层：母体那句描述（后端 `universe.note`，口径原句一类）与判据原句折进说明框；
 *  三行读数（哪天 / 几只 / 被排除几只）与 0 行那句告警留在默认可见位置。措辞一字未改。 */
test('母体描述折进说明框，读数行与 0 行告警留在框外', () => {
  const boxStart = board.indexOf('<ExplainBox');
  const boxEnd = board.indexOf('</ExplainBox>');
  assert.ok(boxStart > 0 && boxEnd > boxStart, '稳定榜要有说明框（原生 details 不带 open ⇒ 默认收起，闸在 explainBox.test.ts）');
  const box = board.slice(boxStart, boxEnd);
  assert.match(box, /\{u\.note && <p>\{u\.note\}<\/p>\}/, 'universe.note 那句折进框里，且仍原样渲染后端文字');
  assert.match(box, /d\.rule/, '判据原句在框里');
  assert.doesNotMatch(box, /凑满|excluded_partial/, '分母与被排除只数不许跟着搬进框');
  const before = board.slice(0, boxStart);
  assert.match(before, /看的是哪几天/, '哪几天是读数行，默认留外面');
  assert.match(before, /进榜的票/);
  assert.match(before, /excluded_partial/, '被排除多少只必须默认在屏上');
  const after = board.slice(boxEnd);
  assert.match(after, /不是市场没有票/, '0 行那句告警渲染在框之后、不在框内');
});

test('交互实现全站只有一份：没有第二个 window.open，也没有页面自留弹窗告警 state', () => {
  const files = srcFiles('../src/');
  const openers = files.filter(f => /window\.open\(/.test(read(f)));
  assert.deepEqual(openers, ['../src/lib/openStockReview.ts'],
    `开复盘新窗口的动作只准住在那一份 helper 里：${openers}`);
  // 除 helper 自己，任何页面都不该再抄一份"点击＝记住行号+列名"的状态
  const inline = files.filter(f => !f.endsWith('useStockRowInteraction.ts') && !f.endsWith('useTableInteraction.ts')
    && /useState<\{ row: number; col: string(\s*\|\s*null)?\s*\}>/.test(read(f)));
  assert.deepEqual(inline, [], `交叉高亮的状态又长出第二份实现：${inline}`);
});

test('用到这一份交互的屏都真的引用了它', () => {
  const users = srcFiles('../src/').filter(f => /useStockRowInteraction\(/.test(read(f)) && !f.endsWith('useStockRowInteraction.ts'));
  for (const must of ['../src/pages/Quadrant.tsx', '../src/pages/Grouped.tsx', '../src/pages/Confluence.tsx',
                      '../src/pages/Spike.tsx', '../src/pages/Portfolio.tsx', '../src/pages/Watchlist.tsx',
                      '../src/pages/VolumeDouble.tsx', '../src/pages/VolumeBreakout.tsx',
                      '../src/pages/FirstBoard.tsx', '../src/pages/Screen.tsx', '../src/pages/Journal.tsx',
                      '../src/components/quant/SystemEntryBoard.tsx',
                      '../src/components/quant/SystemEntryCohort.tsx',
                      '../src/components/quant/StartupVolumeBoard.tsx',
                      '../src/components/quant/AmbushBoard.tsx', '../src/components/quant/NewHighBoard.tsx',
                      '../src/components/quant/RankStabilityBoard.tsx', '../src/components/quant/TrendStockBoard.tsx',
                      '../src/components/quant/VolumeSimilarityBoard.tsx',
                      '../src/components/home/AbnormalMoveBoard.tsx',
                      '../src/components/home/FirstBoardDailyPanel.tsx',
                      '../src/components/quant/FirstBoardShapeBoard.tsx',
                      '../src/components/quant/StartupQuadrantTrackBoard.tsx',
                      '../src/components/quant/StartupSpikeDaysBoard.tsx',
                      '../src/components/quant/SteadyRankBoard.tsx',
                      '../src/components/quant/MomentumFollowBoard.tsx'])
    assert.ok(users.includes(must), `${must} 没接上（又退回自己写一份或干脆没有交互）`);
  // 每屏必须把"这一屏叫什么"交给 hook（复盘页要说清这只票是从哪一屏双击带过来的）
  for (const f of users)
    assert.match(read(f), /useStockRowInteraction\("[^"\u201c\u201d]+"\)/, `${f} 没交 from 标签`);
  // 按票一行的屏清单变了，就要连台账一起改（`frontend/docs/公用组件：…读数块清单.md` §六 有这一张表）
  // 10-17 加「启动前 30 场四维象限轨迹」的明细榜（StartupQuadrantTrackBoard）⇒ 20 → 21。
  // 10-18 加「启动前爆量与突然拉高」的逐票明细榜（StartupSpikeDaysBoard，同一形状：只取 openReview
  // 与 popHint、不接交叉高亮）⇒ 21 → 22；台账 §六 那一行必须同时存在，不许只改这里的数字。
  // 10-19 加「名次稳步前进榜」（SteadyRankBoard，四维象限那一屏的第二张榜）⇒ 22 → 23；
  // 台账 §六 那一行必须同时存在，不许只改这里的数字。
  // 10-20 加「上一场稳步前进榜 · 后续表现」（MomentumFollowBoard，长在母体榜正下方的第三张榜）⇒ 23 → 24；
  // 台账 §六 那一行必须同时存在，不许只改这里的数字。
  // 10-21 加首页「首板分析」（components/home/FirstBoardDailyPanel，用户点名挂在换手率 TOP20 上面，
  // 整张表都接：交叉高亮 + 双击都生效）⇒ 24 → 25；台账 §六 那一行必须同时存在，不许只改这里的数字。
  // 10-22 加「走完三条的个股近六个月名单」（components/quant/SystemEntryCohort，挂在交易体系榜之后，
  // 整张表都接、只有"位次轨迹"那一格刻意不接交叉高亮）⇒ 25 → 26；台账 §六 那一行同时写了。
  assert.equal(users.length, 26, `调用点数目与台账 §六 不一致：${users.length} → ${users.join(', ')}`);
});

test('hook 自己：双击带 noopener、点击不整行吞、告警与高亮一起清', () => {
  assert.match(hook, /const openReview = useCallback\(\(code: string \| null \| undefined\) => \{/);
  assert.match(hook, /const rowProps = useCallback\(\(i: number, code: string \| null \| undefined/,
    '行的形状（含双击与悬停文字）由 hook 交，页面不许各自拼一份');
  assert.match(hook, /onDoubleClick: \(\) => openReview\(code\)/);
  assert.doesNotMatch(hook, /onClick: \(\) => cell\.handleCellClick\(i, "row"\)/,
    '整行吞掉点击会把"点的是哪一列"抹平（列高亮就没得选了）');
  assert.match(hook, /setPopHint\(""\)/, 'clearAll 要连告警一起清：换排序后留着上一屏的弹窗提示是假话');
  assert.match(hook, /export const STOCK_ROW_HINT/, '怎么点这句话也只有一份（各屏自己写就会漂）');
});

test('新端点登记进系统配置的自检清单，落点写的是本屏路由', () => {
  assert.match(system, /path: "\/quant\/rank-stability\?days=7&limit=50"/,
    '端点跑了却没进自检清单＝没人验（本项目这一族已犯过多次）');
  assert.match(system, /where: "\/quadrant"/);
});
