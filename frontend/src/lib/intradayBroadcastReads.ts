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
 * 一条播报要提交的那几格。watcher 是"起始 + 间隔 + 时长"，其余三条是一个时点串。
 * 键名从后端在册表来（`defaults` 里有没有那个键），这里不另起一套名字。
 */
export function taskPatch(key: string, cfg: BroadcastConfig): BroadcastConfig {
  // 开关那一格一律交 Boolean：草稿里没这个键时按"缺键＝按默认（开着）"读，
  // 交 `undefined` 会被 JSON 丢掉，那一笔就变成"什么都没改"。
  const out: BroadcastConfig = { [`${key}_enabled`]: cfg[`${key}_enabled`] !== false };
  if (key === 'watcher') {
    for (const suffix of ['start', 'interval_min', 'duration_h']) {
      const name = `${key}_${suffix}`;
      if (cfg[name] !== undefined) out[name] = cfg[name];
    }
  } else if (cfg[`${key}_times`] !== undefined) {
    out[`${key}_times`] = cfg[`${key}_times`];
  }
  return out;
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
                              task?: Pick<BroadcastTask, 'available' | 'module'>): string {
  if (task && !task.available) {
    return `这一路的执行体（${task.module}）还没搬进仓库，存下时点也不会注册`;
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
  const raw = String(cfg[`${key}_times`] ?? '').trim();
  return raw ? '' : '至少要留一个时点（不想播这一路请把开关关掉，别留空的）';
}

/** 任务计划程序的回体码说人话（`0` 在屏上像"零条"，其实那句是"上一次跑得成功"）。 */
export function resultCodeText(raw?: string | number | null): string {
  const s = String(raw ?? '').trim();
  if (!s) return '—';
  if (s === '0' || s === '267011') return '成功（0x0）';
  if (s === '267009' || s === '267010') return '在跑 / 待跑';
  if (s === '267016') return '已被禁用';
  if (s === '-2147024894' || s === '1') return '上一次失败（退出码 1）';
  return `退出码 ${s}`;
}

/** 那一行的留痕话术：针数与"真发出去几条"要分开说，混着讲就成了"今天 51 条消息"。 */
export function ledgerText(t: Pick<BroadcastTask, 'on' | 'ledger'>,
                           day?: string | null): string {
  const l = t.ledger;
  if (!l) return t.on ? `${day ?? '今天'} 还没有这一路的留痕（库里没有这一路的行）` : '';
  const bits = [`${l.rounds} 针`, `发 ${l.sent} 条`];
  if (l.failed) bits.push(`失败 ${l.failed} 条`);
  if (l.skipped) bits.push(`非时段跳过 ${l.skipped}`);
  if (l.notes.length) bits.push(`缺数/降级 ${l.notes.length} 类`);
  const last = String(l.last_at ?? '');
  if (last) bits.push(`最后 ${last.length > 10 ? last.slice(11, 19) : last}`);
  return `${day ?? '今天'} ${bits.join(' · ')}`;
}
