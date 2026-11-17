import test from 'node:test';
import assert from 'node:assert/strict';
import { ghostList, pidList, probeTimeLine, readTri, watchVerdict } from '../src/lib/tdxWatch.ts';

/**
 * 通达信看护那一格的三态判读。核心只有一条：**"未探测到"不许显示成"没在跑"**，
 * 也不许用本机时钟把"没有探测记录"糊成一个看起来刚刚探过的时间。
 */

test('三态各自成形，null 不塌成 false', () => {
  assert.equal(readTri(true, "在跑", "不在").tone, 'ok');
  assert.equal(readTri(false, "在跑", "不在").text, '不在');
  const unknown = readTri(null, "在跑", "不在");
  assert.equal(unknown.tone, 'unknown');
  assert.match(unknown.text, /未探测到/);
  assert.equal(readTri(undefined, "在跑", "不在").tone, 'unknown');
});

test('没有探测记录 → 说"不知道"，不当成正常也不当成坏', () => {
  const v = watchVerdict({ last_probe_at: null });
  assert.equal(v.tone, 'unknown');
  assert.match(v.text, /没有探测记录/);
  // 空白串同样算没给，别把 "  " 当成一个时间
  assert.equal(watchVerdict({ last_probe_at: '   ' }).tone, 'unknown');
});

test('登录框弹出优先于一切：自动链路停在这里', () => {
  const v = watchVerdict({ last_probe_at: '2026-10-06 15:40', process_running: true, login_dialog: true, syncing: true });
  assert.equal(v.tone, 'bad');
  assert.match(v.text, /登录框/);
});

test('探测时间有、进程状态没交 → 判断不了，不默认成"在跑"', () => {
  const v = watchVerdict({ last_probe_at: '2026-10-06 15:40' });
  assert.equal(v.tone, 'unknown');
  assert.match(v.text, /判断不了/);
});

test('进程在但窗口不可见 → 是坏事（自动化取数拿不到数）', () => {
  const v = watchVerdict({ last_probe_at: '2026-10-06 15:40', process_running: true, window_visible: false });
  assert.equal(v.tone, 'bad');
  assert.match(v.text, /窗口/);
});

test('正常在跑 / 正在同步 → ok', () => {
  assert.equal(watchVerdict({ last_probe_at: '2026-10-06 15:40', process_running: true, window_visible: true }).tone, 'ok');
  const s = watchVerdict({ last_probe_at: '2026-10-06 15:40', process_running: true, syncing: true });
  assert.match(s.text, /正在同步/);
});

/** 这一行是"最近探测时间"：空值给"没有记录"，绝不给"刚刚"。 */
test('探测时间行：无记录就说无记录，有记录才带年龄', () => {
  assert.equal(probeTimeLine({}, () => '刚刚'), '最近探测：没有记录');
  assert.match(probeTimeLine({ last_probe_at: '2026-10-06 15:40' }, () => '刚刚'), /2026-10-06 15:40/);
  assert.doesNotMatch(probeTimeLine({ last_probe_at: '2026-10-06 15:40' }, () => '刚刚'), /刚刚/, '后端没交 age_seconds 就不许编一个年龄');
  assert.match(probeTimeLine({ last_probe_at: '2026-10-06 15:40', age_seconds: 90 }, () => '1 分钟'), /1 分钟前/);
});

/**
 * 2026-11-11：判活只认线程数。`TdxW.exe`(45956) 线程 0、窗口 0、映像路径读不出，
 * 而 tasklist 照样列它 —— 旧判据把它当"在跑"，于是每轮先关它、关不掉、判「旧进程关不掉」，
 * 一整天的日线一根都没落。屏幕上必须把这一行说破，而不是让用户对着任务管理器猜。
 */
test('残影条目：说破它已死、杀不掉，也不当成"在跑"', () => {
  const v = watchVerdict({ last_probe_at: '2026-11-11 16:00', process_running: false, ghost: [45956] });
  assert.equal(v.tone, 'bad');
  assert.match(v.text, /残影/);
  assert.match(v.text, /45956/);
  assert.match(v.text, /不算在跑/);
  // 没有残影时还是原来那句，不许凭空多出一段解释
  assert.equal(watchVerdict({ last_probe_at: '2026-11-11 16:00', process_running: false, ghost: [] }).text, '进程不在');
});

test('后端交了可用性就以它为准：不可用念处境标签', () => {
  const v = watchVerdict({ last_probe_at: '2026-11-11 16:00', process_running: true,
    usable: false, state_label: '不可用（活着但主窗口消息线程不响应＝假死）' });
  assert.equal(v.tone, 'bad');
  assert.match(v.text, /假死/);
  // 标签没交也不许塌成"正常"
  assert.match(watchVerdict({ last_probe_at: 'x', process_running: true, usable: false }).text, /用不了/);
});

test('缩到托盘但后端判可用 → 不许报成坏事', () => {
  const v = watchVerdict({ last_probe_at: '2026-11-11 16:00', process_running: true,
    window_visible: false, usable: true, state_label: '可用（活着、没看到登录框；主窗口枚举不到，多半缩到托盘了）' });
  assert.equal(v.tone, 'ok');
  assert.match(v.text, /托盘/);
});

test('可用性没交时不猜：退回原来那几项读数', () => {
  const v = watchVerdict({ last_probe_at: '2026-11-11 16:00', process_running: true, window_visible: false });
  assert.equal(v.tone, 'bad');
  assert.match(v.text, /窗口/);
  assert.match(v.text, /不替它判/);
});

test('pid 清单与残影清单：空清单是读数，没交才是未知', () => {
  assert.equal(pidList(null, '没有').tone, 'unknown');
  assert.equal(pidList([], '没有').text, '没有');
  assert.equal(pidList([111, 222], '没有').text, '111、222');
  assert.equal(ghostList(undefined).tone, 'unknown');
  assert.equal(ghostList([]).text, '没有');
  const g = ghostList([45956]);
  assert.equal(g.tone, 'bad');
  assert.match(g.text, /45956/);
  assert.match(g.text, /不挡新实例启动/);
});

// ---- 收尾租约（用户 2026-11-12：程序自己拉起的那一个，到点就收）----

import { leaseLine } from '../src/lib/tdxWatch.ts';

test('租约：后端没交这一项 = 未知，不当成「没有租约」', () => {
  assert.equal(leaseLine(undefined).tone, 'unknown');
  assert.equal(leaseLine(null).tone, 'unknown');
  assert.match(leaseLine(null).text, /未探测到/);
});

test('租约：没账本是一个读数（不是程序开的），不是未知', () => {
  const r = leaseLine({ available: false, reason: '没有租约记录：这个客户端不是程序拉起的' });
  assert.equal(r.tone, 'ok');
  assert.match(r.text, /不是程序拉起的/);
});

test('租约：keep_alive 档与开关关着都要说清为什么不收', () => {
  assert.match(leaseLine({ available: true, exempt_keep_alive: true }).text, /keep_alive/);
  const off = leaseLine({ available: true, enabled: false });
  assert.equal(off.tone, 'ok');
  assert.match(off.text, /lease_close_enabled=false/);
});

test('租约：未到点交出还差几分钟与拉起时刻', () => {
  const r = leaseLine({ available: true, pid: 71124, launched_at: '2026-11-12T09:32:00+08:00',
    why: '收盘链 ③c', left_min: 20, after_min: 30, status: 'open', deferred_count: 0 });
  assert.equal(r.tone, 'ok');
  assert.match(r.text, /71124/);
  assert.match(r.text, /09:32/);
  assert.match(r.text, /还差 20 分钟/);
  assert.match(r.text, /收盘链 ③c/);
});

test('租约：到点且在顺延要说清顺延几次、最近一次为什么', () => {
  const r = leaseLine({ available: true, pid: 71124, launched_at: 'x', left_min: -5,
    status: 'deferred', deferred_count: 2, last_defer: { why: '收盘链 daily_close 正在跑' },
    due_at: '2026-11-12T10:02:00+08:00', max_defer_min: 120 });
  assert.match(r.text, /已到点/);
  assert.match(r.text, /已顺延 2 次/);
  assert.match(r.text, /daily_close/);
});

test('租约：已收掉 / 已作废 / 顺延用满三档各自成形', () => {
  const closed = leaseLine({ available: true, pid: 1, launched_at: 'x', status: 'closed',
    closed_at: '2026-11-12T10:05:00+08:00' });
  assert.equal(closed.tone, 'ok');
  assert.match(closed.text, /已到点关闭/);
  const cleared = leaseLine({ available: true, pid: 1, launched_at: 'x', status: 'cleared',
    close_reason: '不是同一个实例（人重启过）' });
  assert.match(cleared.text, /账本已作废/);
  assert.match(cleared.text, /人重启过/);
  const expired = leaseLine({ available: true, pid: 1, launched_at: 'x', status: 'expired',
    max_defer_min: 120 });
  assert.equal(expired.tone, 'bad');
  assert.match(expired.text, /顺延已用满 120 分钟/);
});
