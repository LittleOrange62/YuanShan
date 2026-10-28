import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Loader2, Search } from "lucide-react";
import { probe } from "@/lib/probe";
import { plain } from "@/lib/agent";
import { cn } from "@/lib/utils";

/**
 * 「选一只票」的通用搜索框（用户 2026-10-15 点名：搜索个股的地方要支持**股票名称首字母**，
 * 而且这是个通用控件，整个程序里同类入口要统一）。
 *
 * 一份实现，多处调用：判据与匹配全在后端那一处
 * （`duanxian/security_search.py` → `app/db/repo/security.py::search`，
 * 代码 / 名称 / 全拼 / **拼音首字母** 四种），这个组件只负责摆出来与把选择交回调用方。
 * 页面**不许自己再写一份过滤**（各页各筛一份 ⇒ 一样的框三种行为）。
 *
 * 三条呈现纪律：
 *  ① 「没匹配上」与「字典读不到」必须分开念：后一种要原样印后端那句失败话。
 *     把库坏了读成"没这只票"，人会去翻代码，而实情是数据库没通（本项目已有同类事故）。
 *  ② 命中方式要跟着显示（`PAYH` 命中平安银行要说"拼音首字母"，`600` 要说"代码"），
 *     否则人以为这框在乱出结果。
 *  ③ 列表被上限截断必须说出口：只列出前 N 条 ≠ 只有 N 只。
 */
interface Hit {
  security_code?: string | null; symbol?: string | null; name?: string | null;
  market?: string | null; board?: string | null; asset_type?: string | null;
  pinyin?: string | null; pinyin_abbr?: string | null;
  matched_by?: string | null; asset_label?: string | null;
}
interface SearchResult {
  available?: boolean | null; reason?: string | null; query?: string | null;
  rows?: Hit[] | null; returned?: number | null; limit?: number | null;
  truncated?: boolean | null; kinds?: string[] | null;
  matched_by_meaning?: Record<string, string> | null; source?: string | null;
}

const DEBOUNCE_MS = 220;
/** 输入少于这么长不发请求：单字母 `A` 在字典里能命中一大片，列出来没有比较意义。 */
const MIN_CHARS = 2;

export function StockSearch({ value, onType, onPick, onEnter, placeholder, ariaLabel, kinds, widthClass = "w-64", submit, disabled = false }: {
  /** 输入框的内容（受控：调用方留着它才能"提交时校验格式"） */
  value: string;
  /** 每一次击键都交回调用方（本组件不自己存文本：存两份就出现"框里的码与分析的票不是同一只"） */
  onType: (v: string) => void;
  /** 选中一条建议时交回（裸 6 位码 + 名称）。带后缀的形状由调用方自己拼，本组件不猜。 */
  onPick: (code: string, name: string) => void;
  /** 列表没开/没有候选时按回车：交回调用方自己那套格式校验与提交（本组件不判代码格式） */
  onEnter?: () => void;
  placeholder?: string;
  ariaLabel: string;
  /** 只在可交易标的里搜时传 `stock,etf`；留空＝股票/ETF/指数/板块都搜得到（由标签说明它是什么） */
  kinds?: string;
  widthClass?: string;
  /** 调用方自己的「提交」按钮/动作，放在建议列表下面（没有就不渲染） */
  submit?: ReactNode;
  /** 调用方正在忙（比如一场研究在跑）时禁掉：不发请求、不弹建议、不接击键 */
  disabled?: boolean;
}) {
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [data, setData] = useState<SearchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement | null>(null);
  const seq = useRef(0);

  const kw = value.trim();

  useEffect(() => {
    if (disabled || kw.length < MIN_CHARS) { setHits(null); setData(null); setErr(""); setBusy(false); return; }
    let live = true;
    setBusy(true);
    const my = ++seq.current;
    const t = setTimeout(async () => {
      const s = await probe<SearchResult>("/quant/security-search", { q: kw, limit: 20, kinds: kinds || "" });
      if (!live || my !== seq.current) return;   // 乱序响应要丢：打字快时旧查询会后到
      setBusy(false);
      if (s.kind !== "ok") { setErr(s.kind === "loading" ? "" : `${s.kind}：${(s as { reason?: string }).reason ?? ""}`); setHits(null); return; }
      setErr("");
      setData(s.data);
      setHits(s.data.available ? (s.data.rows ?? []) : []);
      setActive(0);
    }, DEBOUNCE_MS);
    return () => { live = false; clearTimeout(t); };
  }, [kw, kinds, disabled]);

  // 点到框外收起（用 mousedown：click 会在 blur 之后才发，建议已经先被清掉了）
  useEffect(() => {
    const onDown = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const list = hits ?? [];
  const showList = open && kw.length >= MIN_CHARS && (list.length > 0 || !!data?.reason || !!err);

  function pick(h: Hit) {
    const code = String(h.symbol ?? String(h.security_code ?? "").split(".")[0]);
    onPick(code, String(h.name ?? code));
    setOpen(false);
  }

  const why = (h: Hit) => {
    const m = data?.matched_by_meaning ?? {};
    const tag = m[h.matched_by ?? ""] ?? "";
    return h.matched_by === "abbr" && h.pinyin_abbr ? `${tag}（${h.pinyin_abbr}）` : tag;
  };

  return (
    <div ref={box} className="relative">
      <div className="flex flex-wrap items-center gap-2">
        <div className={cn("relative", widthClass)}>
          <input value={value}
            onChange={e => { onType(e.target.value); setOpen(true); }}
            onFocus={() => setOpen(true)}
            onKeyDown={e => {
              if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive(a => Math.min(a + 1, Math.max(list.length - 1, 0))); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setActive(a => Math.max(a - 1, 0)); }
              else if (e.key === "Escape") setOpen(false);
              else if (e.key === "Enter") {
                if (open && list.length > 0) { e.preventDefault(); pick(list[active] ?? list[0]); }
                else if (onEnter) { e.preventDefault(); onEnter(); }
              }
            }}
            aria-label={ariaLabel}
            aria-expanded={showList}
            placeholder={placeholder ?? `代码 / 名称 / 拼音首字母，如 600519、茅台、GZMT${MIN_CHARS > 1 ? `（至少 ${MIN_CHARS} 个字）` : ""}`}
            className="w-full rounded-lg border border-border bg-black/20 px-2.5 py-1.5 pr-7 text-[12px] outline-none focus:border-primary/50" />
          {busy && <Loader2 className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 animate-spin text-muted-foreground" />}
          {!busy && !value && <Search className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />}
        </div>
        {submit}
      </div>

      {showList && (
        <ul className="absolute z-30 mt-1 max-h-72 w-[min(30rem,80vw)] overflow-auto rounded-lg border border-border bg-background p-1 shadow-lg">
          {list.map((h, i) => (
            <li key={`${h.security_code}-${i}`}>
              <button type="button" onMouseDown={e => { e.preventDefault(); pick(h); }}
                className={cn("flex w-full items-baseline gap-2 rounded px-2 py-1 text-left text-[11px]",
                  i === active ? "bg-primary/15 text-foreground" : "hover:bg-muted/60")}>
                <span className="font-mono text-[10px] text-muted-foreground">{h.security_code}</span>
                <span className="truncate">{h.name}</span>
                <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
                  {h.asset_label}{h.board ? ` · ${h.board}` : ""}｜{why(h)}
                </span>
              </button>
            </li>
          ))}
          {list.length === 0 && (
            <li className="px-2 py-1.5 text-[11px] text-warning">
              {err || plain(data?.reason ?? "后端没交出原因，也没交出结果 —— 这一条既不是「没这只票」，也不是「搜完了」，别当结论用。")}
            </li>
          )}
          {data?.truncated && (
            <li className="border-t border-dashed border-border px-2 py-1 text-[10px] text-muted-foreground">
              已到本次返回上限 {data.limit} 条，所以这是<b className="text-foreground">前 {data.limit} 条，不是全部匹配</b> —— 接着打字把范围缩小。
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
