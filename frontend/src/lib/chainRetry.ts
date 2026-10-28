/**
 * 收盘链某一段「今天还会不会自己再试」的**纯判定层**（零 import，能被 `node --test` 钉住）。
 *
 * 这一格要回答的是 09-28 那次没人答得出的问题：数据 17:22 才落盘，链 16:31 就放弃了，
 * 页面上只剩一句失败 —— 没人知道它其实还会回来。所以三种情况必须分开：
 *  · 还没到最后一档 → 什么都不说（说多了就没人看）；
 *  · 档用完 → 说"不会再自动试"，并给手动补跑的那条路；
 *  · **后端没交节奏** → 说"判断不了"，不许默认成"还会试"，也不许默认成"放弃了"。
 * `attempts_max = 0` 是后端明确说"没有重试档"，不是"没给"，两者走不同分支（01 第三条：0 是有效值）。
 */

export interface RetryFacts {
  last_run_at?: string | null;
  consecutive_failures?: number | null;
  attempts_used?: number | null;
  attempts_max?: number | null;
  next_attempt_at?: string | null;
}

export interface RetryVerdict {
  tone: "warn" | "bad";
  text: string;
}

const filled = (v: unknown) => v !== null && v !== undefined && !(typeof v === "string" && v.trim() === "");

export function retryVerdict(st: RetryFacts): RetryVerdict | null {
  const max = typeof st.attempts_max === "number" ? st.attempts_max : null;
  const used = typeof st.attempts_used === "number" ? st.attempts_used : null;

  if (filled(st.next_attempt_at)) {
    return { tone: "warn",
      text: `上游的数还没到 —— ${st.next_attempt_at} 会自动再试一次`
        + `（今天第 ${used ?? "?"}／${max ?? "?"} 档）。`
        + " 通达信的盘后文件实测会晚到 17:2x，这不是坏了。" };
  }

  if (max === null) {
    // 没交节奏本身不是故障：正常运行过的段本来就没有"下一次"。
    // 只有"看起来已经不行了"的时候，缺节奏才真的判断不了 —— 那时必须说出来。
    const failing = (typeof st.consecutive_failures === "number" ? st.consecutive_failures : 0) > 0;
    if (!filled(st.last_run_at) || failing) {
      return { tone: "warn",
        text: "后端没交重试节奏（attempts_max 缺失），这一项到底还会不会自己再试，界面上判断不了 —— 不默认成「还会试」" };
    }
    return null;
  }

  if (max === 0 && used === 0) {
    return { tone: "bad", text: "这一段没有重试档（attempts_max=0），一次没成就不会再自动试 —— 要补就手动跑" };
  }
  if (used !== null && used >= max) {
    return { tone: "bad",
      text: `今天 ${max} 档已用完，不会再自动试 —— 要补这一场就手动跑那条无桌面的路（c12_tq_day + e1_fill_all --skip tdxdl）` };
  }
  return null;
}
