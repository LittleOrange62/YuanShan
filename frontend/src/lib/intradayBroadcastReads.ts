/**
 * 系统配置 →「盘中播报」的**纯函数层**：类型、交上去的键、拦下来的说法、留痕话术。
 *
 * 为什么单独一个文件而不是和 `loadBroadcast` 放一起：那份要 `import './api.ts'`，
 * 而 `api.ts` 里有 TypeScript 参数属性（`constructor(message, readonly status)`），
 * `node --test` 的 strip-only 模式直接报 `ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX` ——
 * 判据混在 I/O 里，测试就钉不住它。这一层不接任何 I/O，所以能直接 import 来测。
 *
 * 后端那份真源是 `app/intraday/tasks.py`（配置落在作业配置 `jobs/config.json` 的
 * `intraday_broadcast`，执行是 Windows 计划任务）。这里**不抄判据、不抄默认值**：
 * 每一格的可用项、出厂值、当前值都由接口带回来（`config` / `defaults` / `tasks`），
 * 抄一份默认值到前端，第二天就会和后端不一致，用户在界面上改的就不是发出去的那套节奏。
 */

export type ConfigValue = string | number | boolean;
export type BroadcastConfig = Record<string, ConfigValue>;

export interface RegisteredTask {
  state?: string | null; last?: string | null; next?: string | null;
  result?: string | null; args?: string | null; triggers?: string[] | null;
}

export interface LedgerNote { what: string; n: number; at?: string | null; err?: string | null }

export interface BroadcastLedger {
  rounds: number; sent: number; failed: number; skipped: number;
  notes: LedgerNote[]; last_err?: string | null;
  first_at?: string | null; last_at?: string | null;
}

export interface BroadcastTask {
  key: string; label: string; task_name: string; module: string;
  available: boolean; on: boolean; editable: boolean;
  /** 有值 = 这一路的时点由那一格推导（如概念热度跟 `push_times` +10 秒），界面上只读 */
  derived?: string | null;
  /** `record` = 这一路只把数落库、本来就不往外发（涨跌停池事件差）；缺省是 `push` */
  mode?: string | null;
  /** `mode='record'` 时那一行要指出去：发送归哪一路（屏上名字由后端给，前端不抄） */
  push_label?: string | null;
  /** 有值＝这一行的时点格叫什么（`delta` 那一行交的是 `push_times`）；null＝没有自己的时点格 */
  times_field?: string | null;
  schedule: string; channels: string;
  registered: RegisteredTask | null; drift: string[]; ledger: BroadcastLedger | null;
}

export interface ChannelOption { key: string; label: string; usable: boolean }

export interface ApplyRow {
  key: string; task_name: string; action: string; ok: boolean; detail?: string | null;
}

export interface BroadcastState {
  generated_at?: string | null;
  config?: BroadcastConfig; defaults?: BroadcastConfig;
  warnings?: string[]; tasks?: BroadcastTask[];
  readable?: boolean; reason?: string | null;
  global_enabled?: boolean;
  changed?: { changed_at?: string | null; changed_by?: string | null } | null;
  ledger_day?: string | null; ledger_reason?: string | null;
  channel_options?: ChannelOption[]; channel_options_reason?: string | null;
  can_edit?: boolean;
  apply?: ApplyRow[]; apply_note?: string | null;
}

const TIME_RE = /^([01]?\d|2[0-3]):([0-5]\d)$/;

/**
 * 一条播报要提交的那几格。watcher 是"起始 + 间隔 + 时长"；其余每一行的时点格叫什么，
 * 由后端那一格 `times_field` 说死（`delta` 行交的是 `push_times`，推导出来的 heat 行没有自己的格）。
 * 键名从后端在册表来（`defaults` 里有没有那个键），这里不另起一套名字。
 */
export function taskPatch(key: string, cfg: BroadcastConfig,
                          task?: Pick<BroadcastTask, 'derived' | 'times_field'>): BroadcastConfig {
  // 开关那一格一律交 Boolean：草稿里没这个键时按"缺键＝按默认（开着）"读，
  // 交 `undefined` 会被 JSON 丢掉，那一笔就变成"什么都没改"。
  const out: BroadcastConfig = { [`${key}_enabled`]: cfg[`${key}_enabled`] !== false };
  if (key === 'watcher') {
    for (const suffix of ['start', 'interval_min', 'duration_h']) {
      const name = `${key}_${suffix}`;
      if (cfg[name] !== undefined) out[name] = cfg[name];
    }
    return out;
  }
  // 六点那一格归 `delta`（`push_times`）：可改的是它，跟着推导出来的那一路只交开关 ——
  // 让 heat 也带一份时点就会有两处可改、一处生效。
  const field = timesField(key, task);
  if (field && cfg[field] !== undefined) out[field] = cfg[field];
  return out;
}

/** 这一行的时点格叫什么：后端给 `times_field`，没给才按老规矩推（`delta`→`push_times`，推导那一路没有）。 */
export function timesField(key: string, task?: Pick<BroadcastTask, 'derived' | 'times_field'>): string | null {
  if (task?.times_field) return task.times_field;
  if (task?.derived) return null;
  return key === 'delta' ? 'push_times' : `${key}_times`;
}

/** 脏键比对：只有真的与后端现值不同的键才交上去（同块多键是一笔原子写）。 */
export function dirtyPatch(patch: BroadcastConfig, saved: BroadcastConfig): BroadcastConfig {
  const out: BroadcastConfig = {};
  for (const [k, v] of Object.entries(patch)) {
    if (String(saved[k] ?? '') !== String(v ?? '')) out[k] = v;
  }
  return out;
}

/**
 * 「还差哪几样」：主按钮拦下来时必须说得出原因，不许静默 disabled。
 * 这些判法与后端 `validate_patch` 同向（后端才是最终闸口），前端只是提前一句人话。
 */
export function blockingReason(key: string, cfg: BroadcastConfig,
  task?: Pick<BroadcastTask, 'available' | 'module' | 'derived' | 'times_field'>): string {
  if (task && !task.available) {    return `这一路的执行体（${task.module}）还没搬进仓库，存下时点也不会注册`;
  }
  if (cfg[`${key}_enabled`] === false) return '';      // 关着的这一路不需要时点
  if (key === 'watcher') {
    const start = String(cfg.watcher_start ?? '').trim();
    if (!TIME_RE.test(start)) return `起始时刻要写 HH:MM，现在是「${start}」`;
    const interval = Number(cfg.watcher_interval_min);
    if (!Number.isInteger(interval) || interval < 1 || interval > 30) {
      return `间隔要在 1~30 分钟，现在是「${cfg.watcher_interval_min}」`;
    }
    const hours = Number(cfg.watcher_duration_h);
    if (!Number.isInteger(hours) || hours < 1 || hours > 12) {
      return `持续时长要在 1~12 小时，现在是「${cfg.watcher_duration_h}」`;
    }
    return '';
  }
  const name = timesField(key, task);
  // 推导出来的那一路（概念热度跟 push_times +10 秒）没有自己的时点格：拿 `heat_times` 去验
  // 会把"只想关掉这一路"那一笔也拦下来；而 `delta` 那一行虽然也标着 derived，它自己就是那一格
  if (!name) return '';
  const raw = String(cfg[name] ?? '').trim();
  if (raw) {
    const bad = raw.split(',').map(x => x.trim()).filter(x => x && !/^([01]?\d|2[0-3]):([0-5]\d)(:([0-5]\d))?$/.test(x));
    return bad.length ? `${name} 里这些不是 HH:MM 或 HH:MM:SS：${bad.join('、')}` : '';
  }
  return '至少要留一个时点（不想播这一路请把开关关掉，别留空的）';
}

/** 任务计划程序的回体码说人话（`0` 在屏上像"零条"，其实那句是"上一次跑得成功"）。
 *  码表按 `SCHED_S_*`（0x41300 起）来，不靠猜：`267011` 是 0x41303「还没跑过」，
 *  把它当成"成功"会把一个从没启动过的任务读成刚跑完。 */
export function resultCodeText(raw?: string | number | null): string {
  const s = String(raw ?? '').trim();
  if (!s) return '—';
  if (s === '0') return '成功（0x0）';
  const known: Record<string, string> = {
    '267008': '就绪（还没跑过）', '267009': '正在跑', '267010': '已被禁用',
    '267011': '还没跑过', '267012': '排期已结束（一次性任务跑完了）',
    '267013': '没有有效触发器',
  };
  if (known[s]) return known[s];
  // 其余是执行体自己的退出码或 HRESULT（`1`、`-1073741502`＝被终止、`3221225786`＝超时被调度器收掉）
  return `上一次不是正常结束（码 ${s}）`;
}

/** 任务计划程序里"这条任务从没跑过"回的是零值时刻（本机显示成 `1999/11/30 0:00:00`），
 *  那不是"上一次跑的时间" —— 屏上要落成空，由调用方渲染成 `—`。
 *
 *  ⚠️ 同时**把秒位丢掉**：本机实测 `Get-ScheduledTaskInfo` 的 `LastRunTime`/`NextRunTime`
 *  秒位永远等于分钟位（14:18 整分那一针回成 `14:18:18`、9:25 那一发回成 `9:25:25`），
 *  是 CIM 时间值转出来的假数。宁可只到分，也不许在屏上写一个机器上没发生过的秒。
 *  要精确到秒看行末那一格「今天 … 最后 …」（那是脚本自己写进库的）。 */
export function runAtText(v?: string | null): string {
  const s = String(v ?? '').trim();
  if (!s || s.startsWith('1999')) return '';
  const parts = s.split(':');
  return parts.length >= 3 ? parts.slice(0, 2).join(':') : s;
}

/** 那一行的留痕话术：针数与"真发出去几条"要分开说，混着讲就成了"今天 51 条消息"。
 *  `mode='record'` 的那一路根本不发送，屏上就不能出现"发 0 条"这种读起来像坏掉的句子。 */
export function ledgerText(t: Pick<BroadcastTask, 'on' | 'ledger' | 'mode' | 'push_label'>,
                           day?: string | null): string {
  const l = t.ledger;
  if (!l) return t.on ? `${day ?? '今天'} 还没有这一路的留痕（库里没有这一路的行）` : '';
  const record = t.mode === 'record';
  const bits = record
    ? [`${l.rounds} 针`, `只落库不发送${t.push_label ? `（发送归「${t.push_label}」那一行）` : ''}`]
    : [`${l.rounds} 针`, `发 ${l.sent} 条`];
  if (!record && l.failed) bits.push(`失败 ${l.failed} 条`);
  if (l.skipped) bits.push(`非时段跳过 ${l.skipped}`);
  if (l.notes.length) bits.push(`缺数/降级 ${l.notes.length} 类`);
  const last = String(l.last_at ?? '');
  if (last) bits.push(`最后 ${last.length > 10 ? last.slice(11, 19) : last}`);
  return `${day ?? '今天'} ${bits.join(' · ')}`;
}
