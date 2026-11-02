import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { blockingReason, dirtyPatch, ledgerText, taskPatch } from '../src/lib/intradayBroadcastReads.ts';

/**
 * 系统配置 →「盘中播报」这一屏的四条纪律（用户 11-02 点名要把时点与渠道交进界面）。
 *
 * 前四条钉纯函数（交上去的键、脏键比对、拦下来的说法、留痕话术），后四条读源码钉结构性的事 ——
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

  const h = taskPatch('heat', { heat_enabled: false, heat_times: '10:05,14:18', note: 'x' });
  assert.deepEqual(h, { heat_enabled: false, heat_times: '10:05,14:18' });
});

test('时点写坏了要当场说人话，不能只 disabled', () => {
  assert.match(blockingReason('heat', { heat_times: '' }), /至少要留一个时点/);
  assert.equal(blockingReason('heat', { heat_times: '10:05,10:55' }), '');
  assert.match(blockingReason('watcher', {
    watcher_start: '9:70', watcher_interval_min: 5, watcher_duration_h: 6,
  }), /HH:MM/);
  assert.match(blockingReason('watcher', {
    watcher_start: '09:15', watcher_interval_min: 99, watcher_duration_h: 6,
  }), /1~30/);
  // 执行体还没搬进来时，保存这一路本来就注定被拒 —— 这句话必须在按之前就讲出来
  assert.match(blockingReason('noon', { noon_times: '12:10' },
    { available: false, module: 'ingest.jobs.noon_review' } as never),
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

test('后端拒绝的原话要带出来，不许折成一句「保存失败」', () => {
  assert.match(io, /body\?\.detail \|\| body\?\.reason/);
  assert.match(panel, /后端没有采纳这次修改/);
});
