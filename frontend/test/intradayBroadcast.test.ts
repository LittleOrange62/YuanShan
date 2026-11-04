import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { blockingReason, dirtyPatch, ledgerText, resultCodeText, runAtText, taskPatch } from '../src/lib/intradayBroadcastReads.ts';

/**
 * 系统配置 →「盘中播报」这一屏的几条纪律（用户 11-02 点名要把时点与渠道交进界面，
 * 11-03 又点名把「取数落库」与「到点推送」分层）。
 *
 * 前面钉纯函数（交上去的键、脏键比对、拦下来的说法、留痕话术），后面读源码钉结构性的事 ——
 * "页面自己抄一份默认时点"这种退化，跑 tsc 与浏览器实测都看不出来。
 */

const panel = readFileSync(new URL('../src/components/IntradayBroadcastPanel.tsx', import.meta.url), 'utf8');
const io = readFileSync(new URL('../src/lib/intradayBroadcast.ts', import.meta.url), 'utf8');
const reads = readFileSync(new URL('../src/lib/intradayBroadcastReads.ts', import.meta.url), 'utf8');

test('watcher 交三格、其余只交时点串，键名一律来自后端在册表', () => {
  const w = taskPatch('watcher', {
    watcher_enabled: true, watcher_start: '09:15', watcher_interval_min: 5,
    watcher_duration_h: 6, channels: 'feishu-main',
  });
  assert.deepEqual(Object.keys(w).sort(),
    ['watcher_duration_h', 'watcher_enabled', 'watcher_interval_min', 'watcher_start']);
  assert.ok(!('channels' in w), '保存一行时不许顺手把渠道也写进去（那是另一格的事）');

  const a = taskPatch('auction', { auction_enabled: false, auction_times: '09:25:30', note: 'x' });
  assert.deepEqual(a, { auction_enabled: false, auction_times: '09:25:30' });
});

test('时点写坏了要当场说人话，不能只 disabled', () => {
  assert.match(blockingReason('auction', { auction_times: '' }), /至少要留一个时点/);
  assert.equal(blockingReason('auction', { auction_times: '09:25:30' }), '');
  assert.match(blockingReason('watcher', {
    watcher_start: '9:70', watcher_interval_min: 5, watcher_duration_h: 6,
  }), /HH:MM/);
  assert.match(blockingReason('watcher', {
    watcher_start: '09:15', watcher_interval_min: 99, watcher_duration_h: 6,
  }), /1~30/);
  // 执行体还没搬进来时，保存这一路本来就注定被拒 —— 这句话必须在按之前就讲出来
  assert.match(blockingReason('noon', { noon_times: '12:10' },
    { available: false, module: 'ingest.jobs.push.t05_noon_review' } as never),
    /还没搬进仓库/);
});

test('针数与真发出去几条分开说，混着讲就成了「今天 51 条消息」', () => {
  const text = ledgerText({
    key: 'watcher', label: 'x', task_name: 'y', module: 'z',
    available: true, on: true, editable: true, schedule: '', channels: '', drift: [],
    ledger: { rounds: 51, sent: 17, failed: 0, skipped: 22, notes: [], last_at: '2026-11-02 15:00:02' },
  } as never, '2026-11-02');
  assert.match(text, /51 针/);
  assert.match(text, /发 17 条/);
  assert.doesNotMatch(text, /51 条/);
  assert.match(text, /非时段跳过 22/);
});

test('没有留痕不等于没有跑：要说「还没有留痕」而不是 0 针', () => {
  assert.match(ledgerText({ key: 'noon', on: true, ledger: null } as never, '2026-11-02'),
    /还没有这一路的留痕/);
});

test('只落库那一行不许写成「发 0 条」：它本来就不发送，还要指出去发送归谁', () => {
  const text = ledgerText({ on: true, mode: 'record', push_label: '盘中涨跌停变化量（读库汇总后推送）',
    ledger: { rounds: 51, sent: 0, failed: 0, skipped: 22, notes: [], last_at: '2026-11-04 15:00:01' } } as never,
  '2026-11-04');
  assert.match(text, /51 针/);
  assert.match(text, /只落库不发送/);
  assert.match(text, /发送归「盘中涨跌停变化量/, '那句名字由后端的在册表给，前端不许自己编一个');
  assert.doesNotMatch(text, /发 0 条/, '把"本来就不发"写成"发 0 条"，屏上看着就是坏了');
});

test('默认时点与渠道名一份都不抄进前端', () => {
  // 抄一份 09:25:30 到界面里，第二天后端改了这里就在说谎；可用项、当前值都由接口带回
  assert.doesNotMatch(reads, /09:25:30|10:05,10:55|feishu-main/, '纯函数层不许抄后端默认值');
  assert.doesNotMatch(io, /09:25:30|10:05,10:55|feishu-main/, 'I/O 层不许抄后端默认值');
  assert.doesNotMatch(panel, /09:25:30|10:05,10:55/, '面板不许抄后端默认值');
});

test('脏键比对：没动过的键不交上去，数字与字符串同值不算改动', () => {
  const saved = { watcher_interval_min: 5, watcher_start: '09:15' };
  assert.deepEqual(dirtyPatch({ watcher_interval_min: '5', watcher_start: '09:20' }, saved),
    { watcher_start: '09:20' }, '5 与 "5" 是同一格，不该触发一次写');
});

test('每行并排给「机器上真注册成什么」与「库里今天真发了几条」', () => {
  assert.match(panel, /reg\?\.state/);
  assert.match(panel, /reg\?\.next/);
  assert.match(panel, /task\.ledger/);
  assert.match(panel, /与机器不一致/);
});

test('保存会动计划任务：按钮上就写「重注册」，抽屉里讲清关掉那一路是注销', () => {
  assert.match(panel, /保存并重注册/);
  assert.match(panel, /注销/);
  assert.match(panel, /任务计划程序/);
});

test('没跑过的任务显示「没跑过」，不拿 1999/11/30 那个零值当时间', () => {
  assert.equal(runAtText('1999-11-30T00:00:00'), '');
  assert.equal(runAtText('1999/11/30 0:00:00'), '');
  assert.equal(runAtText(null), '');
  assert.match(panel, /runAtText\(reg\?\.last\)/);
  assert.match(panel, /没跑过/);
  // 结果码必须跟"上次时间"同一个闸：新任务的 LastTaskResult 是 0，单显示它等于谎称"上次成功"
  assert.match(panel, /\{!!runAtText\(reg\?\.last\) && <span>上次结果/);
});

test('任务计划程序回的时间只到分：它那一位"秒"是假的', () => {
  // 本机实测 `Get-ScheduledTaskInfo` 把秒位印成分钟位（真跑在 09:25:30 的那次回 9:25:25，
  // 14:18:00 回 14:18:18），拿它上屏就是写一个机器上没发生过的秒。库里那一格才有真秒。
  assert.equal(runAtText('2026/11/3 12:10:10'), '2026/11/3 12:10');
  assert.equal(runAtText('2026/11/4 9:25:25'), '2026/11/4 9:25');
  assert.equal(runAtText('2026/11/3 9:15'), '2026/11/3 9:15');   // 没有秒位时原样留
});

test('结果码按 SCHED_S_* 认，267011 是「还没跑过」不是「成功」', () => {
  assert.equal(resultCodeText('0'), '成功（0x0）');
  assert.match(resultCodeText('267011'), /还没跑过/);
  assert.match(resultCodeText('267010'), /禁用/);
  assert.match(resultCodeText('1'), /不是正常结束/);
  assert.equal(resultCodeText(null), '—');
});

test('六点那一格只有一份：delta 交 push_times，跟它推导的热度只交开关', () => {
  const d = taskPatch('delta', { delta_enabled: true, push_times: '09:35,11:00' },
    { derived: 'push_times', times_field: 'push_times' });
  assert.deepEqual(d, { delta_enabled: true, push_times: '09:35,11:00' });
  // 概念热度没有自己的时点格了：就算草稿里还躺着一条旧的 `heat_times` 也不许交上去
  // （交了会被后端整笔拒掉，屏上就成了"我明明只关了个开关，怎么保存失败"）
  const h = taskPatch('heat', { heat_enabled: false, heat_times: '10:05' },
    { derived: 'push_times+10s', times_field: null });
  assert.deepEqual(h, { heat_enabled: false });
  assert.equal(blockingReason('heat', { heat_enabled: true },
    { available: true, module: 'x', derived: 'push_times+10s', times_field: null }), '',
    '推导那一路不许因为"时点为空"把只想关开关这一笔也拦下来');
  // 反过来：`delta` 那一行虽然也标着 derived，它自己就是那一格 —— 写坏了必须在这里提前拦，
  // 否则要点了保存、被后端拒了才看见（同一句话后端才是最终闸口）
  const own = { available: true, module: 'y', derived: 'push_times', times_field: 'push_times' };
  assert.match(blockingReason('delta', { push_times: '09:35,25:99' }, own), /25:99/);
  assert.match(blockingReason('delta', { push_times: '' }, own), /至少要留一个时点/);
  // 上面几条守的是纯函数，这几条守**调用点真的把那一行传进去了**、且时点格名来自后端：
  // 不传 task / 前端自己拼 `${key}_times` 的话，delta 那行会把不存在的 `delta_times` 交上去，纯函数测试照样全绿。
  assert.match(panel, /taskPatch\(task\.key, cfg, task\)/);
  assert.match(panel, /blockingReason\(task\.key, cfg, task\)/);
  assert.match(panel, /task\.times_field/);
});

test('后端拒绝的原话要带出来，不许折成一句「保存失败」', () => {
  assert.match(io, /body\?\.detail \|\| body\?\.reason/);
  assert.match(panel, /后端没有采纳这次修改/);
});

test('这一整块从系统配置搬进「推送任务」那一屏，且只有一处挂它', () => {
  const page = readFileSync(new URL('../src/pages/PushTasks.tsx', import.meta.url), 'utf8');
  const system = readFileSync(new URL('../src/pages/System.tsx', import.meta.url), 'utf8');
  const modules = readFileSync(new URL('../src/lib/workspace/modules.ts', import.meta.url), 'utf8');
  const router = readFileSync(new URL('../src/router.tsx', import.meta.url), 'utf8');
  // 新屏真的挂了它，系统配置那一屏不再挂（两处都挂＝两处可改，正是这块最怕的不一致）
  assert.match(page, /<IntradayBroadcastPanel \/>/);
  assert.doesNotMatch(system, /<IntradayBroadcastPanel \/>/, '系统配置里还留着第二处挂载');
  assert.doesNotMatch(system, /import \{ IntradayBroadcastPanel \}/);
  assert.match(system, /to="\/push-tasks"/, '系统配置原位置要留得下跳转，不能只剩"找不到这块"');
  assert.match(router, /path: "\/push-tasks", element: <PushTasks \/>/);
  assert.match(modules, /to: '\/push-tasks', title: '推送任务'/, '没挂进导航就是孤儿页');
  assert.match(modules, /个股研究与回测[\s\S]{0,400}\/push-tasks/, '用户点名挂在这个板块下');
  // 每一行给出作业脚本路径：改哪一行去找哪个文件不该靠猜（序号=行序由后端那份在册表给）
  assert.match(panel, /task\.module\.replace/);
});
