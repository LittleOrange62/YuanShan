import { jobLabelClass } from "@/lib/colors";
import { cn } from "@/lib/utils";
import { plain } from "@/lib/agent";

/**
 * 系统自己动通达信界面的每一次记录 —— **待人工清单**。
 * 判定与落盘都在后端 `duanxian/gui_trail.py`，这里只转述。
 *
 * 为什么必须有一处看得见：用户 2026-10-06 批准"收盘时系统自己拉起并登录、下载好了自己关窗"，
 * 同一句话里加了条件 ——「万一我当时也在操作电脑，跟你的指令冲突或者误取消了，
 * 也可以我主动的去找出来，哪些没执行的我手动执行下」。
 * 界面上有两个手（人和自动化）碰同一个窗口，冲突时谁赢不保证，
 * 所以没做成的那几条不能只留在日志里：它们就是给人的工单，每条自带"人怎么补"。
 */

export interface TrailItem {
  at?: string | null; day?: string | null; step?: string | null; what?: string | null;
  status?: string | null; detail?: string | null; manual?: string | null;
  target?: string | null; source?: string | null;
}

const STATUS_TEXT: Record<string, string> = {
  need_human: "要人来", failed: "做了没成", skipped: "按闸门没做",
};

export function TdxTrail({ count, pending, path }: {
  count?: number | null; pending?: TrailItem[] | null; path?: string | null;
}) {
  const pend = pending ?? [];
  return (
    <div className="mt-3 rounded-lg border border-border p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-xs font-semibold">通达信界面动作 · 待人工清单</h3>
        <span className={cn("rounded-md px-1.5 py-0.5 text-[11px]",
          pend.length ? jobLabelClass("warn") : jobLabelClass("normal"))}>
          {pend.length ? `待人工 ${pend.length} 条` : "没有要人补的"}
        </span>
        <span className="text-[11px] text-muted-foreground">
          最近三天登记 {count ?? 0} 次（做成干净的不列，全量在 <code>{path ?? ""}</code>）
        </span>
      </div>
      {pend.length === 0 ? (
        <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
          没有"没点成"的动作。系统动过的每一步（拉起、登录、开下载窗、勾日线/分钟线、关窗、
          收进程）都登记在这里，一旦哪步失败或按闸门跳过就会出现在上面那一列。
        </p>
      ) : (
        <ul className="mt-1.5 space-y-1.5">
          {pend.map(x => (
            <li key={`${x.at}-${x.step}-${x.what}`} className="rounded-md bg-warning/10 px-2 py-1.5">
              <p className="text-[11px] font-medium text-foreground">
                {x.at ?? ""}　{x.what ?? x.step ?? ""}
                <span className="ml-1 font-normal text-warning">
                  （{STATUS_TEXT[x.status ?? ""] ?? x.status}）
                </span>
                {x.target ? <span className="ml-1 font-normal text-muted-foreground">目标 {x.target}</span> : null}
              </p>
              {x.detail && <p className="text-[11px] leading-4 text-muted-foreground">{plain(x.detail)}</p>}
              <p className="text-[11px] leading-4 text-foreground">
                手动补：<span className="text-muted-foreground">
                  {x.manual ?? "见《系统配置工作指南/07-故障恢复手册》"}</span>
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
