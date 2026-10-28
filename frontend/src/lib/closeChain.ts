import { apiUrl } from './base.ts';
import { authHeaders } from './api.ts';

/**
 * 收盘链开关（15:59 两段）的取数层。判定与落盘都在后端 `vr/jobsboard.py::chain_status`，
 * 这里只转述与提交。
 *
 * 这一格存在的理由写在后端注释里，界面这边同样不能丢：`enabled=false` 是**会一直留着**
 * 的持久状态（默认值救不回来），而它原先在页面上只剩一个小灰标签「已停用」——
 * 那种措辞分不出"用户自己不想跑"与"被谁误关"。所以这里必须同时给出：
 *  ① 现在开还是关、以及默认本该是什么；② 谁、什么时候、从哪条路改的（留痕，自动盖章）；
 *  ③ 人写的理由 `note`；④ 今天到底会不会跑（日历 + 宿主循环 + 开关三样合起来）。
 */
export interface ChainStage {
  key: string; name: string; enabled: boolean; default_enabled: boolean;
  run_at: string | null; run_at_source: string | null;
  last_run_at: string | null; last_ok_at: string | null;
  consecutive_failures: number | null; last_error: string | null;
  changed_at: string | null; changed_by: string | null; note: string | null;
  warnings: string[];
  /** 只在获取段（`daily_close`）有：宿主的重试节奏，表在 `duanxian/jobs.py:DAILY_CLOSE_RETRY_MIN`。
   *  `next_attempt_at` 有值 = 上游的数据还没到，但**今天到那个时刻还会自动再试一次** ——
   *  这一格存在的意义就是分清"晚到"与"没了"：09-28 那天数据 17:22 才落盘，
   *  而链 16:31 就放弃了，页面上只剩一句失败，没人知道它其实还会回来。 */
  attempts_used?: number | null; attempts_max?: number | null;
  next_attempt_at?: string | null;
  /** 只在获取段有（用户 2026-10-07 要的两格）：提前几分钟拉起并登录通达信（0 = 不提前），
   *  以及是否只在交易日执行。`launch_at` 是后端算好的实际拉起时刻 —— 页面不许自己减分钟，
   *  减错了看不出来。 */
  launch_lead_min?: number | null; launch_at?: string | null;
  trading_days_only?: boolean | null;
  /** 「链收工后自动关闭通达信客户端」（用户 10-08 要加的档，**默认关**）。
   *  关着的含义要写在旁边：保持登录是历史决定（TQ 直取要它在、次日免重登），不是漏配。 */
  close_client_after?: boolean | null; close_client_default?: boolean | null;
}

/** 「条数完整性」那一格：判"这一场的数全不全"的规则现在长什么样。
 *  偏离值是**条数**（不是百分比），两个数都在页面上改得动，且要看得见"这一场是按哪个判的"。 */
export interface CompletenessKind {
  kind: string; label?: string | null; unit?: string | null;
  latest?: string | null; count?: number | null;
  baseline?: number | null; baseline_date?: string | null;
  baseline_basis?: string | null; sessions_back?: number | null;
  deviation?: number | null; deviation_from?: string | null;
  sessions?: number | null; floor?: number | null; measured?: string | null;
}
export interface Completeness {
  available?: boolean; reason?: string | null;
  rule?: string | null; explain?: string | null;
  deviation?: number | null; deviation_minute?: number | null;
  /** 这两个数是**默认档**还是**有人在系统配置里改过的** —— 只报数字的话，
   *  下一次判错时会去查一个并不存在的改动（与 `run_at_source` 同一个规矩）。 */
  deviation_from?: string | null; deviation_minute_from?: string | null;
  kinds?: CompletenessKind[] | null;
}

/** 系统自己动通达信界面的每一次记录（`duanxian/gui_trail.py`）。
 *  `status !== "done"` 的那些就是**待人工清单**：用户 2026-10-06 的条件是
 *  "万一我当时也在操作电脑，跟你的指令冲突或者误取消了，也可以我主动的去找出来，
 *  哪些没执行的我手动执行下" —— 所以每条必须自带 `manual`（人怎么补）。 */
export interface TrailItem {
  at?: string | null; day?: string | null; step?: string | null; what?: string | null;
  status?: string | null; detail?: string | null; manual?: string | null;
  target?: string | null; source?: string | null;
}

export interface ChainState {
  generated_at: string | null; slot: string | null; slot_source: string | null;
  stages: ChainStage[]; host_loop_alive: boolean; host_loop_missing: string[];
  is_trade_day: boolean | null; today_will_run: boolean; explain: string | null;
  /** 日历覆盖到哪天。页面上那句"日历只登到这里"必须读它，不许写死日期 ——
   *  写死的明年就变成一句假话（唯一的源实测只登到 2026-12-31）。 */
  calendar_through?: string | null;
  /** 今天这个"是不是交易日"是**哪一层**答的：calendar（日历明说）/ weekend（星期几自判）/
   *  quote（实时行情的时间戳自证）/ unknown（三层都答不上，判不了）。
   *  读数一旦来自 quote 或 weekend，就说明日历已经排不住今天了。 */
  trade_day_evidence?: string | null;
  /** 日历余量（与 calendar_through 同一份读数，后端 `pg.calendar_coverage()`）。 */
  max_date?: string | null; days_left?: number | null; running_out?: boolean | null;
  /** 通达信看护那一格。**没有记录必须给 null，不许回退成"在跑"** ——
   *  10-06 之前的真实状态就是"监控代码在、没人调用"，界面上什么都没有才看得出什么都没有。 */
  tdx?: { loop_alive?: boolean | null; last_run_at?: string | null;
    ok?: boolean | null; running?: boolean | null; login_box?: boolean | null;
    sync?: string | null; reason?: string | null; heal?: string | null;
    consecutive_failures?: number | null } | null;
  tdx_trail?: { generated_at?: string | null; path?: string | null; count?: number | null;
    by_status?: Record<string, number> | null; pending?: TrailItem[] | null } | null;
  completeness?: Completeness | null;
  available?: boolean; reason?: string | null;
}

/** 后端 `stages` 缺失/不是数组时按空表处理，别让它变成 `undefined.map()`。 */
function normalize(data: ChainState): ChainState {
  const trail = data.tdx_trail;
  return {
    ...data,
    stages: Array.isArray(data.stages) ? data.stages : [],
    host_loop_missing: Array.isArray(data.host_loop_missing) ? data.host_loop_missing : [],
    // 后端没给这一格就还是没给（旧后端 ≠ "待人工清单是空的"，两个读法完全不同）
    tdx_trail: trail ? { ...trail, pending: Array.isArray(trail.pending) ? trail.pending : [] } : trail,
  };
}

export async function loadChain(): Promise<ChainState> {
  let resp: Response;
  try {
    resp = await fetch(apiUrl('/api/jobs/chain'), { headers: authHeaders() });
  } catch {
    throw new Error('连不到后端，读不到收盘链开关（不是"没在跑"，是读不到）');
  }
  const body = await resp.json().catch(() => null) as { data?: ChainState } | null;
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = body?.data;
  if (!data || typeof data !== 'object') throw new Error('后端返回的形状不是对象，按读不到处理');
  return normalize(data);
}

/** 一次只改一件事（后端也是这么要求的）：开关 / 时点 / 理由 / 提前量 / 是否只在交易日。 */
export async function patchChain(key: string,
  what: 'enabled' | 'run_at' | 'note' | 'launch_lead_min' | 'trading_days_only'
        | 'close_client_after',
  value: boolean | string | number): Promise<{ data: ChainState; reason: string }> {
  const resp = await fetch(apiUrl('/api/jobs/chain'), {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, [what]: value }),
  });
  const body = await resp.json().catch(() => null) as { data?: ChainState; detail?: string; reason?: string } | null;
  if (!resp.ok) throw new Error(body?.detail || `后端拒绝本次修改（HTTP ${resp.status}）`);
  if (!body?.data) throw new Error('改完了但后端没回新状态，按读不到处理（别在页面上假装成功）');
  return { data: normalize(body.data), reason: body.reason || '已保存' };
}

export async function loadCompleteness(): Promise<Completeness> {
  let resp: Response;
  try {
    resp = await fetch(apiUrl('/api/jobs/completeness'), { headers: authHeaders() });
  } catch {
    throw new Error('连不到后端，读不到条数完整性的判据（不是"没有规则"，是读不到）');
  }
  const body = await resp.json().catch(() => null) as { data?: Completeness } | null;
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  if (!body?.data || typeof body.data !== 'object') throw new Error('形状不是对象，按读不到处理');
  return body.data;
}

/** 偏离值一次只改一个数（后端同一条规矩）。`0` 是合法值 = 少一条都算不全。 */
export async function patchCompleteness(what: 'deviation' | 'deviation_minute',
  value: number): Promise<{ data: Completeness; reason: string }> {
  const resp = await fetch(apiUrl('/api/jobs/completeness'), {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ [what]: value }),
  });
  const body = await resp.json().catch(() => null) as { data?: Completeness; detail?: string; reason?: string } | null;
  if (!resp.ok) throw new Error(body?.detail || `后端拒绝本次修改（HTTP ${resp.status}）`);
  if (!body?.data) throw new Error('改完了但后端没回新读数，按读不到处理');
  return { data: body.data, reason: body.reason || '已保存' };
}

/**
 * 一条常驻循环的读数（`/api/jobs/loops`）。
 *
 * 这一格为什么现在才上界面：接口 10-06 就交齐了（开关、时点与来源、最近记账、留痕、
 * 关掉的代价），但**全仓没有任何前端消费它** —— 于是"7 条循环里只有收盘链那两段能在
 * 界面上关"，其余只能命令行。用户 10-07 问"是不是有一大堆定时任务、这块在系统配置里吗"，
 * 答案就该是这一屏。
 */
export interface LoopRow {
  key: string; name?: string | null; loop?: string | null;
  alive?: boolean | null; switchable?: boolean | null; unswitchable_reason?: string | null;
  enabled?: boolean | null; default_enabled?: boolean | null;
  run_at?: string | null; run_at_source?: string | null; has_slot?: boolean | null;
  cadence?: string | null; off_effect?: string | null;
  sync_mode?: string | null; sync_mode_choices?: Record<string, string> | null;
  /** 通达信客户端的两档（`tdx_watch.client_mode`）：需要时才运行 / 保持运行。
   *  后端只在**本键真在册**时才交，别的行交 null ⇒ 不渲染那个框（改了也不生效的框就是新的假开关）。 */
  client_mode?: string | null; client_mode_choices?: Record<string, string> | null;
  /** 请退之后那只「退出 / 重新登录 / 取消」要不要程序替你点（`tdx_watch.exit_confirm_click`）。
   *  同样只在本键真在册时才交；`undefined/null` 就当"这一行没有这一格"，不渲染勾选框。 */
  exit_confirm_click?: boolean | null; exit_confirm_click_help?: string | null;
  /** 保持在线的时间带（`tdx_watch.client_band_lo/hi`，用户 2026-10-19 点名可配）。
   *  **空串是合法值**＝那一头仍按收盘链推导，所以这里交 `""` 而不是 null；`undefined` 才表示
   *  这一行根本没有这一格（别的循环）。后面两格是「每日定时重启」（默认 15:38）。 */
  client_band_lo?: string | null; client_band_hi?: string | null;
  client_band_help?: string | null;
  restart_daily_enabled?: boolean | null; restart_daily_at?: string | null;
  restart_daily_help?: string | null;
  last_run_at?: string | null; last_ok_at?: string | null;
  consecutive_failures?: number | null; last_error?: string | null;
  never_recorded?: boolean | null; changed_at?: string | null; changed_by?: string | null;
  note?: string | null; warnings?: string[] | null;
  /** 关掉之后**当前这个进程**停不停：只有循环体每轮重读开关的那几条是 true。
   * 后端 10-08 起才交这一格，旧版服务没交 ⇒ undefined，界面按"不知道"处理，
   * 绝不默认成 true（那正好把最难查的假生效说成已经生效）。 */
  live_switch?: boolean | null; switch_effect?: string | null;
  /** 频度（用户 10-08 点名：同花顺云端同步要能选每日/每周）。
   * 只有后端 `has_freq=true` 的那几行才渲染控件，别的行不给一个改了也不生效的框。 */
  has_freq?: boolean | null; run_freq?: string | null; weekly_days?: string | null;
  freq_warning?: string | null;
  /** ok / fail / **skipped** 三态：休市里按口径停住的那条不该显示成"连败 N"。 */
  last_status?: string | null;
  /** 失败重试阶梯（`step_retry` 那一行专有；别的行后端不给 = 界面不渲染控件）。
   *  `times` 是逗号分隔的 HH:MM（早于 12:00 的算次日），**逐格可改、可加、可删**；
   *  `times_default` 让"删过头"时能一键退回出厂那串。 */
  times?: string | null; times_default?: string | null;
  retry?: RetryStatus | null;
}

/** 阶梯读数：待补名单、档期、下一档、是否已放弃。判据全在后端 `step_retry.status()`。 */
export interface RetryStatus {
  available?: boolean; reason?: string | null;
  enabled?: boolean; times?: string | null; catch_up_min?: number | null;
  target?: string | null; written_at?: string | null;
  pending?: string[]; pending_soft?: string[];
  lanes?: { label: string; at?: string | null; broken?: boolean;
            ran_at?: string | null; outcome?: string | null }[];
  ran?: number; next_at?: string | null; next_label?: string | null;
  gave_up?: boolean; last_lane?: string | null;
  last_detail?: Record<string, unknown> | null;
}
export interface LoopsState {
  available?: boolean | null; reason?: string | null; generated_at?: string | null;
  loops?: LoopRow[] | null; alive_count?: number | null; declared_count?: number | null;
  missing_loops?: string[] | null; can_edit?: boolean | null;
  live_switch_count?: number | null; needs_restart_count?: number | null;
  needs_restart_loops?: string[] | null;
  note?: string | null; explain?: string | null;
}

/** 判据层住 `@/lib/hostLoops`（那边能被 `node --test` 直接驱动），这里只把名字转出去，
 * 这样消费方一处 import 就够，也不会出现两份实现。 */
export { WEEK_DAY_LABELS, freqProblem, parseWeeklyDays, switchBadge, type LoopSwitchView }
  from "@/lib/hostLoops";

function normalizeLoops(data: LoopsState): LoopsState {
  return { ...data, loops: Array.isArray(data.loops) ? data.loops : [],
    missing_loops: Array.isArray(data.missing_loops) ? data.missing_loops : [] };
}

export async function loadLoops(): Promise<LoopsState> {
  let resp: Response;
  try {
    resp = await fetch(apiUrl('/api/jobs/loops'), { headers: authHeaders() });
  } catch {
    throw new Error('连不到后端，读不到常驻循环清单（不是"没有循环在跑"，是读不到）');
  }
  const body = await resp.json().catch(() => null) as { data?: LoopsState } | null;
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  if (!body?.data || typeof body.data !== 'object') throw new Error('形状不是对象，按读不到处理');
  return normalizeLoops(body.data);
}

/** 一次只改一件事（与收盘链、偏离值同一条规矩，后端 `_job_patch_guard` 会拒一次改多件）。 */
export async function patchLoop(key: string,
  what: 'enabled' | 'run_at' | 'note' | 'sync_mode' | 'run_freq' | 'weekly_days' | 'times'
    | 'client_mode' | 'exit_confirm_click'
    | 'client_band_lo' | 'client_band_hi' | 'restart_daily_enabled' | 'restart_daily_at',
  value: boolean | string): Promise<{ data: LoopsState; reason: string }> {
  const resp = await fetch(apiUrl('/api/jobs/loops'), {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, [what]: value }),
  });
  const body = await resp.json().catch(() => null) as { data?: LoopsState; detail?: string; reason?: string } | null;
  if (!resp.ok) throw new Error(body?.detail || `后端拒绝本次修改（HTTP ${resp.status}）`);
  if (!body?.data) throw new Error('改完了但后端没回新状态，按读不到处理（别在页面上假装成功）');
  return { data: normalizeLoops(body.data), reason: body.reason || '已保存' };
}
