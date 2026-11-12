/**
 * 通达信客户端看护（tdx_watch）只读探测的**三态判读层**（零 import，可被 `node --test` 钉住）。
 *
 * 为什么单独成文件：这一格最容易犯的错是**把"没探测到"显示成"没在跑"**。
 * 二者在屏幕上必须长得不一样 —— 前者是"界面不知道"（该去看探测本身坏没坏），
 * 后者是"知道，且结论是坏的"（该去开客户端）。混成一句话，用户就会对着
 * 一个其实正常运行的客户端报"看护没生效"，或反过来把没探测当成"没事"。
 *
 * `available:true/false` 也一样：`false` 是后端明确说这一项今天没数，
 * 缺失是后端根本没表态 —— 表态缺失由 `probeVerdict.ts` 判成 `200?`，这里不重复判。
 */

export type TriState = true | false | null | undefined;

export interface WatchFacts {
  /** 最近一次探测成功的时间 —— 这一格必须带它，否则"在跑"没有落点 */
  last_probe_at?: string | null;
  age_seconds?: number | null;
  process_running?: TriState;
  window_visible?: TriState;
  /** 登录框弹出 = 需要人来点，自动链路到这里就停了 */
  login_dialog?: TriState;
  syncing?: TriState;
  /** 后端那一份可用性判据（残影／假死／登录框／缩到托盘都在它里面判）。没交＝不知道，不猜。 */
  usable?: TriState;
  /** 六档处境的中文标签，措辞只住后端 `watchman.STATE_LABELS`，这里不抄第二份 */
  state_label?: string | null;
  /** 真正活着的实例 pid（线程数 > 0） */
  live?: number[] | null;
  /** 残影条目：tasklist 还列着但 0 线程 —— 进程已死，杀不掉也不挡启动 */
  ghost?: number[] | null;
  note?: string | null;
  reason?: string | null;
}

export type WatchTone = "ok" | "bad" | "unknown";

export interface WatchReading {
  tone: WatchTone;
  text: string;
}

/** 三态读法：`null/undefined` 一律"未探测到"，绝不塌成"不在"。 */
export function readTri(v: TriState, yes: string, no: string): WatchReading {
  if (v === true) return { tone: "ok", text: yes };
  if (v === false) return { tone: "bad", text: no };
  return { tone: "unknown", text: "未探测到（后端没交这个字段，界面不猜）" };
}

/** 这一格总结论。顺序就是优先级：探测本身没落点 → 先说这个。
 *
 *  后端交了 `usable` 就以它为准（2026-11-11 起判活只认线程数，残影／假死／登录框／
 *  缩到托盘都在 `watchman.probe()` 一处判）；没交才退回下面这几项读数自己拼。 */
export function watchVerdict(f: WatchFacts): WatchReading {
  const at = (f.last_probe_at ?? "").trim();
  if (!at) {
    return { tone: "unknown",
      text: "没有探测记录（不知道上一次探测是什么时候 —— 这一格现在还不能说明客户端在不在跑）" };
  }
  if (f.usable === false) {
    return { tone: "bad", text: (f.state_label ?? "").trim() || "客户端用不了（后端没交处境标签）" };
  }
  if (f.login_dialog === true) return { tone: "bad", text: "登录框弹出，自动链路停在这里，要人工点一次" };
  if (f.process_running === false) {
    const ghost = f.ghost ?? [];
    if (ghost.length) {
      return { tone: "bad",
        text: `只剩残影条目 ${ghost.join("、")}：进程已经死了，任务管理器里那一行不算在跑，也杀不掉` };
    }
    return { tone: "bad", text: "进程不在" };
  }
  if (f.process_running === null || f.process_running === undefined) {
    return { tone: "unknown", text: `探测时间在 ${at}，但没交进程状态 —— 判断不了在不在` };
  }
  if (f.syncing === true) return { tone: "ok", text: "在跑，正在同步" };
  if (f.usable === true) {
    return { tone: "ok", text: (f.state_label ?? "").trim() || "在跑" };
  }
  if (f.window_visible === false) {
    return { tone: "bad", text: "进程在，但主窗口不可见（后端没交可用性判据，界面不替它判）" };
  }
  return { tone: "ok", text: "在跑" };
}

/** pid 清单那一格：没交＝未探测到；空清单是一个**读数**（"没有"），不是未知。 */
export function pidList(v: number[] | null | undefined, empty: string): WatchReading {
  if (v === null || v === undefined) {
    return { tone: "unknown", text: "未探测到（后端没交这个字段，界面不猜）" };
  }
  if (!v.length) return { tone: "ok", text: empty };
  return { tone: "ok", text: v.join("、") };
}

/** 残影那一格：有残影要说破（它杀不掉、也不挡启动），没有就是"没有"。 */
export function ghostList(v: number[] | null | undefined): WatchReading {
  if (v === null || v === undefined) {
    return { tone: "unknown", text: "未探测到（后端没交这个字段，界面不猜）" };
  }
  if (!v.length) return { tone: "ok", text: "没有" };
  return { tone: "bad", text: `${v.join("、")}（进程已死、条目未回收；杀不掉，也不挡新实例启动）` };
}

/** 探测时间那一行：空值说"没有探测记录"，不许拿本机时钟填一个"刚刚"。 */
export function probeTimeLine(f: WatchFacts, ageText: (s: number | null | undefined) => string): string {
  const at = (f.last_probe_at ?? "").trim();
  if (!at) return "最近探测：没有记录";
  const age = typeof f.age_seconds === "number" ? `（${ageText(f.age_seconds)}前）` : "";
  return `最近探测：${at}${age}`;
}
