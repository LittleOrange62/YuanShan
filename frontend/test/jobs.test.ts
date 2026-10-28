import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { counterLine, dash, deriveJobView, hasValue, successLabel, NULL_TEXT } from '../src/lib/jobsView.ts';
import { MODULES } from '../src/lib/workspace/modules.ts';

/** 源码路径按本文件位置解析：从仓库根 `node --test frontend/test/*.test.ts`
 *  与从 frontend/ 里跑，都必须同一条命令成立（README 记的是前者）。 */
const SRC = new URL('../src/', import.meta.url).pathname.replace(/^\//, '');

/**
 * 钉住契约 `docs/页面展示工作指南/03-任务调度页契约.md` §4 的**判定顺序**，
 * 以及 §6 的三条 NULL 纪律。改判定必须先改契约再改这里 —— 顺序本身就是语义。
 *
 * 这些用例全部打在 `jobsView.ts`（零 import 的纯函数）上，是因为判定只允许存在
 * 一份；如果哪天有人在页面里又写了一遍 `if (last_run_at == null)`，这里不会红，
 * 但 §8 的变更纪律要求两条一起动。
 */

const ON = { jobsAvailable: true, schedulerAvailable: true, scheduler: { enabled: true, dry_run: false } };
/**  healthy 作业：跑过、成功过、不落后 */
const healthy = { key: 'k', name: 'n', state: 'idle', enabled: true, available: true, last_run_at: '2026-09-24T17:00:00+08:00', last_ok_at: '2026-09-24T17:00:03+08:00' };

test('§4-1 jobs_available=false 压过一切：整页只说"清单不可读"', () => {
  const v = deriveJobView({ ...healthy, lock_state: 'stuck', state: 'never_ran' },
    { jobsAvailable: false, schedulerAvailable: true, scheduler: { enabled: true } });
  assert.equal(v.label, '作业清单不可读');
  assert.equal(v.tone, 'bad');
});

test('§4-2 available=false 时不显示 state，且 reason 缺失要自己出声', () => {
  const withReason = deriveJobView({ ...healthy, available: false, reason: '缺 ft_etl 凭据', state: 'idle' }, ON);
  assert.equal(withReason.label, '不可用');
  assert.equal(withReason.detail, '缺 ft_etl 凭据');
  assert.ok(!withReason.detail?.includes('idle'), 'state 不得与 reason 各说一套');

  const noReason = deriveJobView({ ...healthy, available: false, state: 'idle' }, ON);
  assert.match(String(noReason.detail), /未带 reason/);
});

test('§4-3 读不到调度器 ≠ 调度器关着', () => {
  const v = deriveJobView({ ...healthy, last_run_at: null, last_ok_at: null, next_due_at: null },
    { jobsAvailable: true, schedulerAvailable: false, scheduler: null });
  assert.equal(v.label, '调度器状态未知');
  assert.equal(v.neverRan, false, '调度器状态未知时不得断定"从未运行"');
});

test('§4-4 锁卡住优先级高于"已停用"和"未调度"', () => {
  const v = deriveJobView({ ...healthy, lock_state: 'stuck', lock_held_by: 'tq-client', lock_waited_ms: 4200, enabled: false }, ON);
  assert.equal(v.label, '锁被占住');
  assert.match(String(v.detail), /tq-client/);
  assert.match(String(v.detail), /4200/);
});

test('§4-6 总开关关着时，"从未运行"必须重新解释成"未调度"（FinanceTool 那次事故）', () => {
  const off = { jobsAvailable: true, schedulerAvailable: true, scheduler: { enabled: false, note: '调度器总开关未打开' } };
  const v = deriveJobView({ ...healthy, last_run_at: null, last_ok_at: null, next_due_at: null }, off);
  assert.equal(v.label, '未调度');
  assert.equal(v.neverRan, false);
  assert.match(String(v.detail), /总开关未打开/);
});

test('§4-7 总开关开着且从没跑过 → 从未运行，且 neverRan 为真（样式最重）', () => {
  const v = deriveJobView({ ...healthy, last_run_at: null, last_ok_at: null, next_due_at: null, state: 'never_ran' }, ON);
  assert.equal(v.label, '从未运行');
  assert.equal(v.tone, 'bad');
  assert.equal(v.neverRan, true);
});

test('§4-7 有 next_due_at 就不是"从未运行"（排队中 vs 从没登记过）', () => {
  const v = deriveJobView({ ...healthy, last_run_at: null, last_ok_at: null, next_due_at: '2026-09-25T09:00:00+08:00' }, ON);
  assert.notEqual(v.label, '从未运行');
  assert.equal(v.neverRan, false);
});

test('§4-8 连续失败压过数据落后', () => {
  const v = deriveJobView({ ...healthy, consecutive_failures: 3, last_error: 'TQ 未登录', trade_day_aware: true, days_behind: 5 }, ON);
  assert.equal(v.label, '连续失败 3 次');
  assert.equal(v.detail, 'TQ 未登录');
});

test('§4-9 trade_day_aware 才判数据落后；否则非交易日不跑不算红', () => {
  const late = deriveJobView({ ...healthy, trade_day_aware: true, days_behind: 2, last_data_date: '2026-09-22', expected_data_date: '2026-09-24' }, ON);
  assert.equal(late.label, '数据落后 2 个交易日');
  assert.equal(late.tone, 'bad');

  const notAware = deriveJobView({ ...healthy, trade_day_aware: false, days_behind: 2, last_data_date: '2026-09-22' }, ON);
  assert.notEqual(notAware.label, '数据落后 2 个交易日');

  const sameDay = deriveJobView({ ...healthy, trade_day_aware: true, days_behind: 0, last_data_date: '2026-09-24', expected_data_date: '2026-09-24' }, ON);
  assert.ok(!sameDay.label.includes('落后'), '落后 0 个交易日不是故障，不该出红标');
});

test('§4 两条不许之一：days_behind=null 不得显示成"不落后"，也不得显示成"落后 0 天"', () => {
  const v = deriveJobView({ ...healthy, trade_day_aware: true, days_behind: null, last_data_date: '2026-09-22', expected_data_date: '2026-09-24' }, ON);
  assert.equal(v.label, '数据日期未知');
  assert.equal(v.tone, 'warn');
});

test('§4-10 running 与"跑过但没有成功记录"是两件事', () => {
  const running = deriveJobView({ ...healthy, state: 'running' }, ON);
  assert.equal(running.label, '运行中');

  const ranButFailedSilently = deriveJobView({ ...healthy, state: 'idle', last_ok_at: null, last_error: '写入被拒' }, ON);
  assert.equal(ranButFailedSilently.label, '跑过但没有成功记录');
  assert.equal(ranButFailedSilently.detail, '写入被拒');

  const ok = deriveJobView({ ...healthy, state: 'idle', next_due_at: '2026-09-25T17:00:00+08:00' }, ON);
  assert.equal(ok.label, 'idle');
});

/**
 * 09-30 自查第一条：`last_run_at` 为空必须显示成"没跑过"，绝不能回退成看起来像完成。
 * 原来只有"next_due_at 也为空"才被接住，于是**登记了、排队中、但一次都没执行**的作业
 * 一路掉到底下那句「跑过但没有成功记录」—— 把从没跑过说成跑过。
 * （自选同步那条 20:40 的作业连败 91 而页面照显旧存量，是同一类错的另一面。）
 */
test('§4-7 排队中但一次没跑过 → 「还没有运行记录」，不许掉进"跑过但没有成功记录"', () => {
  const v = deriveJobView({ ...healthy, last_run_at: null, last_ok_at: null,
    next_due_at: '2026-10-01T20:40:00+08:00', state: 'idle' }, ON);
  assert.equal(v.label, '还没有运行记录');
  assert.match(String(v.detail), /2026-10-01T20:40:00/);
  assert.doesNotMatch(v.label + String(v.detail), /跑过但没有成功记录/, '从没跑过 ≠ 跑过没成功');
  assert.equal(v.neverRan, false, '登记好了的排队不占"从未运行"那格最重的样式');
});

test('§4-7 没有 next_due_at 又没有 last_run_at → 才是"从未运行"（没人触发）', () => {
  const v = deriveJobView({ ...healthy, last_run_at: null, last_ok_at: null, next_due_at: null }, ON);
  assert.equal(v.label, '从未运行');
  assert.equal(v.neverRan, true);
});

test('§6 空值渲染成 —，但 0 是有效值（NULL≠0 是这项目最容易骗人的一条）', () => {
  assert.equal(dash(null), NULL_TEXT);
  assert.equal(dash(undefined), NULL_TEXT);
  assert.equal(dash('   '), NULL_TEXT);
  assert.equal(dash(0), '0');
  assert.equal(dash(false), 'false');
  assert.equal(hasValue(0), true);
  assert.equal(hasValue(false), true);
  assert.equal(hasValue(''), false);
});

test('§6 dry_run 下的成功显示「试跑（已回滚）」，不许显示"成功"', () => {
  assert.equal(successLabel({ dry_run: true }), '试跑（已回滚）');
  assert.equal(successLabel({ dry_run: false }), '成功');
  assert.equal(successLabel(null), '成功', 'dry_run 未知时按正式写入处理，但仍要显示 dry_run 字段本身');
});

test('§6 计数不发明百分比：窗口未知就说未知，unit 缺失要出声', () => {
  const line = counterLine({ counters: { scope: 'cumulative', unit: '条', written: 12, already_had: 40, dropped: 0, total_seen: 52 } });
  assert.match(line, /累计/);
  assert.match(line, /dropped 0/, '0 必须出现，不能被过滤掉');
  assert.match(line, /共见 52/);
  assert.ok(!line.includes('%'), '不得合成百分比');
  assert.match(counterLine({ counters: { written: 1 } }), /窗口未知/);
  assert.equal(counterLine({}), NULL_TEXT);
});

test('§1 位置纪律：/jobs 在侧栏底部运维区，且不混进一级模块与首页卡片', () => {
  const layout = readFileSync(SRC + 'components/layout/Layout.tsx', 'utf8');
  assert.match(layout, /to="\/jobs"[\s\S]{0,200}任务调度/);
  assert.equal(MODULES.some(m => m.pages.some(p => p.to === '/jobs')), false,
    '调度是运维入口，不是工作流阶段（契约 §1）');
});

test('§7 实况：后端 /api/jobs 目前未实现时页面必须说"接口未提供"，不能说"没有作业"', async () => {
  const src = readFileSync(SRC + 'lib/jobs.ts', 'utf8');
  assert.match(src, /HTTP 404/);
  assert.match(src, /不代表没有作业/);
});

/**
 * 09-25 接口首次 200 时看到的矛盾：`news` / `intraday_snapshot` 的 `last_run_at` 有值、
 * `run_history` 却是空数组（常驻采集循环不写历史）。页面当时无条件写着「确实一次都没跑过」，
 * 与同屏的"上次跑"直接打架 —— 那是我这侧的假话，不是后端的。
 * 这条把它钉住：那句结论只允许在"确实没有运行痕迹"的分支里出现。
 */
test('run_history 为空不得无条件读成"从未运行"', () => {
  const src = readFileSync(SRC + 'pages/Jobs.tsx', 'utf8');
  const block = src.slice(src.indexOf('function HistoryBlock'), src.indexOf('function HistoryBlock') + 1600);
  assert.ok(block.includes('确实一次都没跑过'), '两种情况仍要分别给结论');
  const guard = block.match(/if \(hist\.length === 0\)[\s\S]*?确实一次都没跑过/);
  assert.ok(guard && /hasValue\(job\.last_run_at\)/.test(guard[0]),
    '空数组分支必须先查 job.last_run_at / job.last_ok_at 才允许下「没跑过」的结论');
});

/**
 * 台账环形缓冲（`jobs.py` 的 `runs`）存的是**旧→新**（后端测试钉着 `runs[0]` 是最旧的、
 * 丢的也必须是最旧的），而这一屏那一段的标题写着「最近运行记录」—— 照存储顺序渲染
 * 就是最新的一次落在列表最底下，读者要点到底才知道"刚才那一下怎么样了"（10-11 实测）。
 * 所以显示层必须倒过来，并且把"新在前"写在标题上，不许只靠读者猜。
 */
test('run_history 显示要新在前（存储是旧在新前面）', () => {
  const src = readFileSync(SRC + 'pages/Jobs.tsx', 'utf8');
  const block = src.slice(src.indexOf('function HistoryBlock'), src.indexOf('function RunConfirm'));
  assert.ok(/最近运行记录（\{hist\.length\} 条，新在前）/.test(block), '标题要写明新在前');
  assert.ok(/hist\.slice\(\)\.reverse\(\)\.map\(/.test(block), '不许原地 reverse 改到共享数组');
});
