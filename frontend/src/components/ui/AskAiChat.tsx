import { randomId } from "@/lib/random-id";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { readMode } from "@/lib/workspace/state";
import { useState, useRef, useEffect } from "react";
import { Link } from "react-router-dom";
import { Settings, Send, Loader2, Wrench, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { hasLlm, chatStream, type ChatMsg } from "@/lib/llm";
import { ApiError } from "@/lib/api";
import { SaveNoteButton } from "@/components/ui/SaveNoteButton";

/**
 * 「问 AI · 本页上下文」的对话本体 —— 挂在右侧「AI分析」栏里（不再是浮层抽屉）。
 *
 * 用户 2026-10-07：首页盘面数据那个「问 AI」按钮取消，这一套移到右侧。
 * 三件事是刻意的：
 *  - **上下文由页面登记进来**（`lib/workspace/pageAskAi`），这里不猜、不补、不拿旧内容兜底；
 *    父组件按 `label` 重挂本页，换页时上一屏的对话不会留着被读成"这一屏的答案"；
 *  - **发出去的是什么必须能核对**：下面那个 `<details>` 展开就是原文，不是"已包含若干数据"这种话；
 *  - 结论由用户自己配置的模型给出，本产品不校准、不负责 —— 这句话在没接入和已接入两种时候都要在。
 */
interface Props {
  context: string;
  suggestions?: string[];
}

const TOOL_LABEL: Record<string, string> = {
  query_quote: "查行情",
  query_valuation: "查估值",
  query_reports: "查研报",
  query_news: "查新闻",
};

// 数据溯源：把工具调用的关键参数压成一小段（查了哪只/哪些代码）。
const argStr = (a: Record<string, unknown>): string => {
  if (Array.isArray(a.codes)) return (a.codes as unknown[]).join(",");
  if (typeof a.code === "string") return a.code;
  return "";
};

interface ToolUse { name: string; arg: string }

export function AskAiChat({ context, suggestions = [] }: Props) {
  const [configured, setConfigured] = useState(hasLlm);
  const [msgs, setMsgs] = useState<(ChatMsg & { tools?: ToolUse[] })[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const sessionId = useRef(randomId());
  // 在跑的流式请求：组件卸载/换页时中止，省用户的订阅额度，也防迟到 chunk 写进新气泡
  const abortRef = useRef<AbortController | null>(null);

  // 换页时把在跑的请求收掉（父组件按 label 重挂，这里再兜一道，不留两个流）
  useEffect(() => () => abortRef.current?.abort(), []);
  // 「AI 探索」页刚保存好来源时，回到这一栏不该还显示"没接入"（context 每轮刷新都会重查一次）
  useEffect(() => { setConfigured(hasLlm()); }, [context]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [msgs, loading]);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || loading) return;
    setInput("");
    setErr(null);
    const history: ChatMsg[] = [...msgs.map(({ role, content }) => ({ role, content })), { role: "user", content: q }];
    // 先放用户气泡 + 一个空的 assistant 气泡，流式往里填。
    setMsgs((m) => [...m, { role: "user", content: q }, { role: "assistant", content: "", tools: [] }]);
    setLoading(true);
    const patchLast = (fn: (msg: ChatMsg & { tools?: ToolUse[] }) => ChatMsg & { tools?: ToolUse[] }) =>
      setMsgs((m) => m.map((msg, i) => (i === m.length - 1 && msg.role === "assistant" ? fn(msg) : msg)));
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    // 只有仍是「当前这次请求」才允许写 UI —— 旧请求的迟到 chunk 直接丢弃
    const alive = () => abortRef.current === ac && !ac.signal.aborted;
    try {
      await chatStream(history, context, {
        sessionId: sessionId.current,
        onTool: (tool, args) => { if (alive()) patchLast((msg) => ({ ...msg, tools: [...(msg.tools || []), { name: tool, arg: argStr(args) }] })); },
        onDelta: (t) => { if (alive()) patchLast((msg) => ({ ...msg, content: msg.content + t })); },
      }, ac.signal);
    } catch (e) {
      // 出错/中止：去掉尾部空 assistant 气泡；主动中止不算错误，不提示
      setMsgs((m) => m.filter((msg, i) => !(i === m.length - 1 && msg.role === "assistant" && !msg.content)));
      if (!ac.signal.aborted) setErr(e instanceof ApiError ? e.message : "对话失败");
    } finally {
      if (abortRef.current === ac) {
        abortRef.current = null;
        setLoading(false);
      }
    }
  };

  const preview = (
    <details className="mt-2 rounded-lg border border-border/60 bg-black/20">
      <summary className="cursor-pointer px-2 py-1.5 text-[10px] text-muted-foreground hover:text-foreground">
        看要发出去的本页上下文（{context.length.toLocaleString()} 字）
      </summary>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap border-t border-border/60 p-2 font-mono text-[10px] leading-relaxed text-muted-foreground">
{context}
      </pre>
    </details>
  );

  if (!configured) {
    return (
      <div className="space-y-2 text-[11px] leading-relaxed">
        <p className="rounded-lg border border-warning/30 bg-warning/5 p-2 text-muted-foreground">
          还没接入 AI，问不了。这一栏只负责把本页数据打包成上下文、并让 AI 能调数据工具，
          <b className="text-foreground">不校准、不背书、不对结果负责</b>。
        </p>
        {preview}
        <Link to="/settings?tab=access"
          className="flex items-center justify-center gap-1.5 rounded-lg bg-primary/15 px-2 py-2 text-[11px] font-medium text-primary hover:bg-primary/25">
          <Settings className="h-3.5 w-3.5" /> 先接入你的 AI（订阅 / API）
        </Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div ref={scrollRef} className="max-h-64 space-y-2 overflow-auto text-[11px] leading-relaxed">
        {msgs.length === 0 && (
          <p className="rounded-lg border border-warning/30 bg-warning/5 p-2 text-muted-foreground">
            {readMode()
              ? "本次结合本页资料，可查询公开行情、估值和研报。"
              : "本次仅阅读本页已提供资料；开启左侧 Agent 后才可查询更多公开资料。"}
            结论由你的模型给出，<b className="text-foreground">不构成投资建议</b>。
          </p>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
            <div className={cn("max-w-[92%] rounded-xl px-2.5 py-1.5",
              m.role === "user" ? "bg-primary/20 text-foreground" : "bg-muted/40 text-foreground")}>
              {m.tools && m.tools.length > 0 && (
                <div className="mb-1.5 flex flex-wrap items-center gap-1">
                  <span className="text-[10px] text-muted-foreground/70">数据来源</span>
                  {m.tools.map((t, j) => (
                    <span key={j} className="inline-flex items-center gap-0.5 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                      <Wrench className="h-2.5 w-2.5" /> {TOOL_LABEL[t.name] || t.name}{t.arg ? ` ${t.arg}` : ""}
                    </span>
                  ))}
                </div>
              )}
              <div className="prose prose-sm max-w-none dark:prose-invert">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
              </div>
              {m.role === "assistant" && m.content && !(loading && i === msgs.length - 1) && (
                <div className="mt-1"><SaveNoteButton kind="问AI" title={`问 AI · ${msgs[i - 1]?.content?.slice(0, 24) || "对话"}`} content={m.content} /></div>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <p className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" /> {readMode() ? "正在分析，实际查询记录会显示在回答上方…" : "正在阅读本页资料并整理回答…"}
          </p>
        )}
        {err && (
          <p className="flex items-start gap-1.5 rounded-lg border border-destructive/30 bg-destructive/5 p-2 text-destructive">
            <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" /> {err}
          </p>
        )}
        {msgs.length === 0 && suggestions.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {suggestions.map((s) => (
              <button key={s} type="button" onClick={() => void send(s)}
                className="rounded-full border border-border bg-muted/40 px-2 py-1 text-[10px] hover:border-primary/40 hover:text-primary">
                {s}
              </button>
            ))}
          </div>
        )}
      </div>

      {preview}

      <div className="flex items-end gap-1.5">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) { e.preventDefault(); void send(input); } }}
          rows={2}
          placeholder="就本页内容提问…"
          aria-label="就本页内容提问"
          className="min-w-0 flex-1 resize-none rounded-lg border border-border bg-black/20 px-2 py-1.5 text-[11px] outline-none focus:border-primary/50"
        />
        <button type="button" onClick={() => void send(input)} disabled={loading || !input.trim()}
          aria-label="发送"
          className="rounded-lg bg-primary/15 p-2 text-primary hover:bg-primary/25 disabled:opacity-40">
          <Send className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
