"use client";
import { Settings2, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import type { Config, TurnRecord } from "@/lib/types";
import type { Engine } from "./useEngine";
import { Button } from "./ui";

function Shell({ title, onClose, children, wide, footer }: { title: ReactNode; onClose: () => void; children: ReactNode; wide?: boolean; footer?: ReactNode }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        className={`animate-rise flex max-h-[88dvh] w-full flex-col overflow-hidden rounded-xl border border-line bg-white shadow-2xl ${wide ? "max-w-5xl" : "max-w-2xl"}`}
      >
        <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          <button onClick={onClose} aria-label="Đóng" className="cursor-pointer rounded p-1 text-ink-3 hover:bg-slate-100 hover:text-ink">
            <X size={16} />
          </button>
        </div>
        <div className="scroll-thin flex-1 overflow-y-auto">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-line px-4 py-2.5">{footer}</div>}
      </div>
    </div>
  );
}

export function PromptDialog({ turn, instructions, onClose }: { turn: TurnRecord; instructions: string; onClose: () => void }) {
  const [tab, setTab] = useState<"user" | "system" | "gate">("user");
  const ctx = turn.context!;
  const text = tab === "system" ? instructions : tab === "gate" ? turn.gate?.prompt ?? "(gate_mode = none)" : ctx.prompt;
  return (
    <Shell title={`Prompt gửi bộ trích — lượt #${turn.turn}`} onClose={onClose} wide>
      <div className="flex gap-1 border-b border-line px-4 py-2 text-xs">
        {([
          ["user", `Khối 2–5 (đổi theo lượt) · ${ctx.tokens.b2 + ctx.tokens.b3a + ctx.tokens.b3b + ctx.tokens.b4 + ctx.tokens.b5} tok`],
          ["system", `Khối 1 — hướng dẫn cố định · ${ctx.tokens.b1} tok`],
          ["gate", "Ngữ cảnh rút gọn của cổng"],
        ] as const).map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} className={`cursor-pointer rounded-md px-2.5 py-1 ${tab === k ? "bg-teal-50 font-medium text-primary-ink" : "text-ink-3 hover:bg-slate-100"}`}>
            {l}
          </button>
        ))}
      </div>
      <pre className="whitespace-pre-wrap break-words px-4 py-3 font-mono text-[11.5px] leading-5 text-ink-2">{text}</pre>
      <p className="border-t border-line bg-surface-2 px-4 py-2 text-[11px] text-ink-3">
        Khối 1 đứng đầu và giống hệt nhau giữa mọi lượt để tận dụng prompt caching. Id <code>#n</code> và <code>T&lt;n&gt;</code> là id cục bộ; code giữ bảng ánh xạ về message_id / task_id.
      </p>
    </Shell>
  );
}

export function ConfigDialog({ engine, onClose }: { engine: Engine; onClose: () => void }) {
  const [draft, setDraft] = useState<Config>(engine.config);
  const help = engine.info?.configHelp ?? {};
  const keys = Object.keys(engine.info?.defaultConfig ?? draft);
  const apply = () => {
    engine.setConfig(draft);
    onClose();
    engine.toast("Đã đổi tham số — bấm “Chạy lại từ đầu” để áp dụng cho toàn bộ luồng", "info");
  };
  return (
    <Shell
      title={<span className="inline-flex items-center gap-2"><Settings2 size={16} /> Tham số (config.yaml)</span>}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={() => setDraft(engine.info?.defaultConfig ?? draft)}>Về mặc định</Button>
          <Button variant="primary" onClick={apply}>Áp dụng</Button>
        </>
      }
    >
      <div className="grid gap-x-4 gap-y-3 p-4 sm:grid-cols-2">
        {keys.map((k) => {
          const v = draft[k];
          const enumOpts = k === "draft_min" ? ["assignee", "assignee_due"] : k === "gate_mode" ? ["none", "shadow", "llm"] : null;
          return (
            <div key={k}>
              <label htmlFor={`cfg-${k}`} className="block font-mono text-[11.5px] font-medium text-ink">{k}</label>
              {enumOpts ? (
                <select id={`cfg-${k}`} value={String(v)} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} className="mt-1 h-8 w-full cursor-pointer rounded-md border border-line bg-white px-2 text-xs">
                  {enumOpts.map((o) => <option key={o}>{o}</option>)}
                </select>
              ) : (
                <input id={`cfg-${k}`} type="number" step="any" value={String(v)} onChange={(e) => setDraft({ ...draft, [k]: Number(e.target.value) })}
                  className="mt-1 h-8 w-full rounded-md border border-line px-2 text-xs tabular-nums focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20" />
              )}
              <p className="mt-0.5 text-[11px] leading-4 text-ink-3">{help[k]}</p>
            </div>
          );
        })}
      </div>
    </Shell>
  );
}
