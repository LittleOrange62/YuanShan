import { readFileSync } from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";

/**
 * 「主要功能点是回溯的，都住在 个股研究与回测 · 策略回测」（用户 2026-11-07 点名）。
 *
 * 这一份不查任何判据算得对不对，只查**一件东西只有一个家**：
 *  ① 搬过来的八块（一、二档）真的挂在策略回测屏，且只有那里挂；
 *  ② 原屏只剩一句指路，不许留第二份挂载 —— 两处各挂一份，改天就是两个说法；
 *  ③ 自检清单里那几个端点的落点跟着搬（清单说在 A 屏、A 屏找不到＝没人验过的那条老毛病）；
 *  ④ 第三档（主功能点也是回溯、但和所在页共用一份取数）本轮**判定过没搬**，
 *     所以钉住它们还在原屏且文档写了为什么 —— 「没做」与「忘了有没有做」是两件事。
 */

const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");
const host = read("../src/pages/StrategyBacktest.tsx");
const volumeDouble = read("../src/pages/VolumeDouble.tsx");
const confluence = read("../src/pages/Confluence.tsx");
const router = read("../src/router.tsx");
const system = read("../src/pages/System.tsx");
const features = read("../src/lib/workspace/features.ts");
const modules = read("../src/lib/workspace/modules.ts");

/** 一、二档：自带取数、搬起来不接续费的回溯块（顺序＝屏上从上到下） */
const MOVED = [
  "<Backtest />",
  "<EnergyTrendBoard />",
  "<StartupVolumeBoard />",
  "<FirstBoardShapeBoard />",
  "<StartupQuadrantTrackBoard />",
  "<StartupSpikeDaysBoard />",
  "<SystemEntryCohort />",
  "<OutlookBacktestBoard />",
];

test("八块回溯全挂在策略回测屏，且只有那里挂", () => {
  for (const tag of MOVED)
    assert.ok(host.includes(tag), `${tag} 没挂在策略回测屏`);
  // 顺序：整屏搬来的涨停样本统计在最前，11-06 第一批搬进来的明日展望回溯在最后
  assert.ok(host.indexOf("<Backtest />") < host.indexOf("<EnergyTrendBoard />"), "回溯这一组从涨停样本统计开始");
  assert.ok(host.indexOf("<SystemEntryCohort />") < host.indexOf("<OutlookBacktestBoard />"), "明日展望那份回溯留在最后");
  // 原屏只剩指路，不留第二份挂载
  for (const tag of ["<EnergyTrendBoard", "<StartupVolumeBoard", "<FirstBoardShapeBoard",
    "<StartupQuadrantTrackBoard", "<StartupSpikeDaysBoard"])
    assert.ok(!volumeDouble.includes(tag), `倍量形态屏还挂着 ${tag} —— 两处各一份就是两个说法`);
  assert.ok(!confluence.includes("<SystemEntryCohort"), "选股汇总屏还挂着走完三条的名单");
});

test("原屏各留一句指路，指向的那一屏真的有内容", () => {
  for (const [file, src] of [["倍量形态", volumeDouble], ["选股汇总", confluence]] as const) {
    const body = src.replace(/\/\*[\s\S]*?\*\//g, "");
    assert.match(body, /to="\/backtest-agent"/, `${file}屏那句指路没指向那一屏`);
    assert.match(body, /历史回溯/, `${file}屏那句指路写的还是旧名字`);
  }
});

/** 序号那一栏该配的标签名（从上面那张清单推出来，不另抄一份，抄了就会分叉） */
const MOVED_TAGS = MOVED.map(t => t.replace(/^</, "").replace(/ \/>$/, ""));

test("这一屏只剩编号回溯块：会话回测那半屏整块拆掉，八块各带一个序号", () => {
  const body = host.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  // 用户 11-07 点名「以下内容不要了」—— 反向钉住，防止哪天又被顺手加回来
  for (const gone of ["整理回测条件", "想验证什么", "确认参数并运行", "最近回测报告",
    "这套回测支持哪些市场", "ExecutionView", "agentRequest", "astock-backtest-pending-v1",
    "AI 探索 · 接入设置"])
    assert.ok(!body.includes(gone), `那一半屏已经不要了，屏上却又长出「${gone}」`);
  // 名字跟着改：屏上没有策略回测了，导航与标题不许还写着它
  assert.match(body, /title="历史回溯"/, "页面标题还写着旧名，而屏上已经没有那件事");
  assert.ok(!modules.includes("title: '策略回测'"), "侧栏那条入口还叫「策略回测」");
  assert.match(features, /to: '\/backtest-agent', title: '历史回溯'/, "系统配置那面卡片清单的名字没跟着改");
  // 序号：真 DOM 文本、1..8 连续、顺序就是挂载顺序（数字不是 CSS 生成的，innerText 读得到）
  const seqs = [...host.matchAll(/<Retro n=\{(\d)\}><(\w+) \/>/g)].map(m => [Number(m[1]), m[2]]);
  assert.deepEqual(seqs.map(s => s[0]), [1, 2, 3, 4, 5, 6, 7, 8], `序号不是 1..8 连续：${JSON.stringify(seqs)}`);
  assert.deepEqual(seqs.map(s => s[1]), MOVED_TAGS, "序号的顺序与挂载的块对不上（改了挂载顺序忘了改号）");
});

test("回溯块各自取数：宿主屏不代劳、也不串选中态", () => {
  const body = host.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  assert.ok(!/useStockRowInteraction\(/.test(body), "宿主屏幕起一份实例＝又把这些表的选中态串回一张表");
  assert.doesNotMatch(body, /useProbe<[^>]*>\(`\/quant\/(energy-trend|startup-volume|first-board-shape)/,
    "取数住在各块组件里，宿主屏不许再发同一件事的第二份请求");
});

test("自检清单里这几个端点的落点跟着搬，features 与导航也只留一处入口", () => {
  for (const p of ["/quant/energy-trend", "/quant/startup-volume", "/quant/first-board-shape",
    "/quant/startup-quadrant-track", "/quant/startup-spike-days", "/quant/system-entry-cohort"])
    assert.match(system, new RegExp(`path: "${p}"[^\\n]*where: "\\/backtest-agent"`),
      `${p} 的落点还写着旧屏：清单说在 A 屏而 A 屏没有＝没人验过`);
  // /backtest 只剩跳转；导航与卡片都不再给它第二条入口
  assert.match(router, /path: "\/backtest", element: <Navigate to="\/backtest-agent" replace \/>/);
  assert.ok(!modules.includes("to: '/backtest'"), "modules 里还留着 /backtest 那条导航条目");
  assert.match(features, /to: '\/backtest-agent'/, "系统配置那面功能卡片清单里还没有策略回测");
  assert.ok(!features.includes("to: '/backtest'"), "卡片清单还留着 /backtest");
});

test("第三档本轮判定过、没搬：它们还在原屏，文档写着为什么", () => {
  // 这三块吃宿主页那一份取数，搬走要先补独立读侧入口，不是"还没想到"
  assert.ok(read("../src/pages/Quadrant.tsx").includes("<MomentumFollowBoard"), "上一场榜后续表现那块被顺手搬走了");
  assert.ok(read("../src/pages/Grouped.tsx").includes("<GroupAnalyticsBoard"), "分组整体分析那块被顺手搬走了");
  assert.ok(read("../src/pages/AgentWeekly.tsx").includes("龙头"), "近5天热度那句龙头留存被顺手删了");
  const doc = read("../../docs/功能点说明/个股研究与回测/回测.md");
  assert.match(doc, /本轮没搬/, "文档要写下不搬的理由，否则下一轮会重新纠结一遍");
});

test("宿主屏源码正文没有裸 markdown 星号（回溯块自己的那几份由各自测试管）", () => {
  const body = host.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  assert.ok(!body.includes("**"), "策略回测屏源码里有字面 ** —— 会被当正文渲染出来");
});
