/**
 * 「这一格里的字是谁写的」判定。单独拎成一个叶子模块是为了能真的测它 ——
 * 用户 09-28 选定的方案是"默认值预填进正文格，但每格留来源标记"，
 * 那么"未改 / 已改 / 纯手写"这三档一旦判错，这份笔记日后再看就分不清
 * 哪天是自己想的、哪天是机器给的。这正是这个文件存在的唯一理由。
 *
 * `machine_baseline`（后端 `ys.review_note.machine_baseline`）存的是**第一次保存时**
 * 那一格的机器原文，之后只增不改；`draft` 是**这一次**算出来的草稿（还没保存过）。
 */
export type Provenance = "machine_untouched" | "machine_edited" | "human" | "empty_with_draft" | "empty";

export interface MarkInput {
  /** 这一格现在的正文（界面上的编辑框内容） */
  value: string;
  /** 这一次算出来的草稿，没算出来给空串 */
  draft?: string;
  /** 库里存下来的"第一次保存时的机器原文"，这一格没留痕就没有 */
  baseline?: string;
}

export function markOf({ value, draft = "", baseline = "" }: MarkInput): Provenance {
  const v = (value ?? "").trim();
  if (!v) return (draft ?? "").trim() ? "empty_with_draft" : "empty";
  const b = (baseline ?? "").trim();
  if (b && v === b) return "machine_untouched";
  if (b) return "machine_edited";
  // 没有留痕（还没保存过）时，只能拿这一次算出来的草稿比：一字不差才算"机器填入"
  const d = (draft ?? "").trim();
  if (d && v === d) return "machine_untouched";
  return "human";
}

export const MARK_LABEL: Record<Provenance, string> = {
  machine_untouched: "机器填入·未改",
  machine_edited: "初始为机器填入·你已改",
  human: "你写的",
  empty_with_draft: "空格·有草稿可填",
  empty: "空的",
};

/** 一句话说明这一档到底意味着什么 —— 只给标签不给解释，用户会猜错"未改"是谁没改。 */
export const MARK_HINT: Record<Provenance, string> = {
  machine_untouched: "内容与机器给的那一份一字不差。保存前它比的是这一次算出来的草稿，保存后比的是库里留痕的那一份",
  machine_edited: "起点是机器草稿，但你自己动过 —— 这一格里有你的判断",
  human: "机器在这一格没给出东西，或者你从零写的",
  empty_with_draft: "这一格还空着，但这一次算出了草稿，点「填入草稿」才进去",
  empty: "这一格是空的",
};

/** 统计一屏的来源，用来在顶部说一句"这 13 格里机器占了几格"。 */
export function tallyMarks(marks: Provenance[]) {
  const out = { machine_untouched: 0, machine_edited: 0, human: 0, empty_with_draft: 0, empty: 0 };
  for (const m of marks) if (m in out) out[m] += 1;
  return out;
}
