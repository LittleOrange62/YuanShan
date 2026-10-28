import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { GlassCard } from "@/components/ui/GlassCard";
import { useNonce, useProbe } from "@/lib/probe";
import { dash } from "@/lib/datastate";
import { plain } from "@/lib/agent";
import { cn } from "@/lib/utils";

/**
 * 系统配置 →「原始文本日志（尾部）」。
 *
 * 这是日志分层的**最后一层**：台账 `jobs/state.json` 与逐步台账只留结论，
 * 而"线程活着、页面上写着正常、其实每轮都在抛异常"这类事只有原文能证明
 * （2026-10-10 的 tdx_watch 就是靠 `.local/uvicorn.out.log` 里每两分钟一行的
 * `AttributeError` 定性的 —— 当时界面上什么都看不出来，只能开终端）。
 *
 * 三条纪律：
 *  - **只点名，不给路径**：下拉里那两份与那几个归档号都由后端列出来，
 *    页面不拼路径（这台机器开着局域网访问，接口也不接受路径参数）；
 *  - **尾巴要说清是尾巴**：`truncated`/`skipped_bytes` 原样转述，不写成"全部日志"；
 *  - **多久没写要看得见**：mtime 的年龄和大小放在最上面，
 *    "日志停在 14:37"这种坑只有摆出来才发现得了（曾经因为 stdout 块缓冲整块丢掉）。
 */

interface LogFile { name: string; desc: string; path: string; exists: boolean; size: number | null; mtime: string | null; archives: number }
interface LogData {
  available?: boolean; reason?: string; note?: string; name?: string; desc?: string; path?: string;
  is_archive?: boolean; run?: string | null; size?: number; mtime?: string; mtime_age_seconds?: number;
  returned?: number; asked?: number; skipped_bytes?: number;
  lines?: string[]; runs?: string[]; generated_at?: string;
  index?: { files?: LogFile[]; limits?: { lines_min?: number; lines_max?: number } };
}

const AGE = (s?: number | null) => {
  if (s == null) return "—";
  if (s < 90) return `${s} 秒前`;
  if (s < 5400) return `${Math.round(s / 60)} 分钟前`;
  if (s < 172800) return `${(s / 3600).toFixed(1)} 小时前`;
  return `${Math.round(s / 86400)} 天前`;
};

export function LogTailPanel() {
  const [nonce, refresh] = useNonce();
  const [name, setName] = useState("uvicorn.out");
  const [run, setRun] = useState("");
  const [lines, setLines] = useState(200);
  const st = useProbe<LogData>("/system/log", nonce, { name, lines, run: run || undefined });
  const d = st.kind === "ok" ? st.data : null;
  const files = d?.index?.files ?? [];
  const runs = d?.runs ?? [];

  return (
    <GlassCard>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">原始文本日志（尾部）</h2>
        <span className="text-[11px] text-muted-foreground">
          台账只留结论，这一层是现场原文 —— 循环"活着但每轮抛异常"只有在这里看得出来
        </span>
        <button type="button" onClick={refresh}
          className="ml-auto flex items-center gap-1 rounded border border-border px-2 py-0.5 text-[11px] hover:bg-muted/50">
          <RefreshCw className="h-3 w-3" /> 重新取数
        </button>
      </div>

      {/* 有哪几份、各自多大、多久没写 —— 这几行本身就是"日志文件放在哪里"的答案。
          用堆叠行不用表格：这一页的壳是 max-w-4xl（文字页），表格的路径列会被挤到折行。 */}
      <ul className="mt-2 space-y-1">
        {files.map(f => (
          <li key={f.name}>
            <label className={cn("flex flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded-lg border border-border/60 px-2 py-1.5 text-[11px]",
                                 f.name === name && "border-primary/60 bg-primary/5")}>
              <input type="radio" name="logfile" checked={f.name === name} onChange={() => { setName(f.name); setRun(""); }} />
              <span className="font-mono font-medium">{f.name}</span>
              <span className="text-muted-foreground">{plain(f.desc)}</span>
              <span className="ml-auto font-mono text-muted-foreground">
                {f.exists ? `${((f.size ?? 0) / 1024).toFixed(1)} KB · 最后写 ${dash(f.mtime)}` : "文件不存在"}
                {f.name === name && d?.available ? `（${AGE(d.mtime_age_seconds)}）` : ""}
                {` · 归档 ${f.archives} 份`}
              </span>
              <span className="w-full break-all font-mono text-[10px] text-muted-foreground/60">{dash(f.path)}</span>
            </label>
          </li>
        ))}
        {files.length === 0 && (
          <li className="text-[11px] text-muted-foreground">后端没交出日志清单（接口没连上，或白名单是空的）。</li>
        )}
      </ul>

      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
        <label className="flex items-center gap-1">行数
          <select value={lines} onChange={e => setLines(Number(e.target.value))} className="rounded border border-border bg-transparent px-1 py-0.5">
            {[100, 200, 400, 800].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1">读哪一次运行
          <select value={run} onChange={e => setRun(e.target.value)} className="max-w-56 rounded border border-border bg-transparent px-1 py-0.5">
            <option value="">当前这份（正在写）</option>
            {runs.map(r => <option key={r} value={r}>归档 {r}（上一次启动之前）</option>)}
          </select>
        </label>
        {!runs.length && <span className="text-muted-foreground/70">没有归档 = 这次启动之后还没重启过（旧的那一份在 10-10 之前会被直接覆盖掉）</span>}
        {d?.available && (
          <span className="ml-auto text-muted-foreground">
            尾部 {dash(d.returned)} 行 · 前面 {((d.skipped_bytes ?? 0) / 1024).toFixed(0)} KB 没读 · 生成 {dash(d.generated_at)}
          </span>
        )}
      </div>

      {d?.note && <p className="mt-1 text-[11px] leading-relaxed text-warning/90">{plain(d.note)}</p>}
      {st.kind === "error" && <p className="mt-1 text-[11px] text-destructive">读日志这件事本身失败了：接口没答上来（不是"没有日志"）。</p>}
      {st.kind === "not_wired" && <p className="mt-1 text-[11px] text-warning">接口尚未提供（后端是旧版本时才会这样）—— 重启一次 8910。</p>}
      {d && d.available === false && (
        <p className="mt-1 text-[11px] leading-relaxed text-warning">{plain(d.reason ?? "后端没说为什么读不到，也没说读不到的是什么")}</p>
      )}

      {d?.available && (
        <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted/40 p-2 font-mono text-[11px] leading-5">
          {(d.lines ?? []).join("\n") || "（这一份现在是空的：文件在，但还没写过东西）"}
        </pre>
      )}
      <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground/70">
        只看尾巴，不在页面上翻整份文件（一天能长几百 KB，全量翻页会把面板变成文件浏览器）。
        要全文：{files.map(f => f.path).join("　·　")}
      </p>
    </GlassCard>
  );
}
