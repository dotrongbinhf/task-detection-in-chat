"use client";
import clsx from "clsx";
import { Check, ChevronDown, CircleSlash, Code2, Filter, Inbox, Layers, Link2, Search, ShieldCheck, Sparkles, Timer, X, Workflow } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { fmtDayTime, fmtTime } from "@/lib/format";
import type { CandidateRow, EngineState, ExtractCall, TraceEntry, TurnRecord } from "@/lib/types";
import { Badge, Empty, Mono, StatusBadge } from "./ui";

type BlockKey = "b1" | "b2" | "b3a" | "b3b" | "b4" | "b5";
const BLOCKS: { k: BlockKey; label: string; color: string }[] = [
  { k: "b1", label: "1 · Hướng dẫn", color: "bg-slate-400" },
  { k: "b2", label: "2 · Lịch 14 ngày", color: "bg-sky-400" },
  { k: "b3a", label: "3a · Được link", color: "bg-amber-500" },
  { k: "b3b", label: "3b · Ứng viên", color: "bg-orange-300" },
  { k: "b4", label: "4 · Lịch sử gần", color: "bg-teal-500" },
  { k: "b5", label: "5 · Tin mới", color: "bg-violet-500" },
];

const CALL_REASON: Record<string, string> = { initial: "lần 1", target_check: "lần 2 · sau kiểm tra target" };
const usd = (v: number) => (v === 0 ? "$0" : `$${v.toFixed(v < 0.001 ? 5 : 4)}`);

const SKIP_LABEL: Record<string, string> = {
  reaction_not_relevant: "reaction không liên quan → chỉ ghi lịch sử",
  gate_skip: "cổng chặn (p < ngưỡng)",
  parse_failure: "JSON hỏng sau khi retry",
  refusal: "model từ chối",
  llm_error: "lỗi gọi LLM",
};

export function TurnList({ state, selected, onSelect }: { state: EngineState; selected: number | null; onSelect: (n: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector(`[data-turn="${selected}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selected]);
  if (!state.turns.length) return null;
  return (
    <div ref={ref} className="scroll-thin flex gap-1 overflow-x-auto border-b border-line bg-surface-2 px-2 py-1.5" role="listbox" aria-label="Danh sách lượt xử lý">
      {state.turns.map((t) => {
        const ok = t.ops.filter((o) => o.accepted).length;
        const bad = t.ops.filter((o) => !o.accepted).length;
        return (
          <button
            key={t.turn}
            data-turn={t.turn}
            role="option"
            aria-selected={selected === t.turn}
            onClick={() => onSelect(t.turn)}
            title={`${t.new_event_ids.join(" + ")} · ${fmtDayTime(t.at)}`}
            className={clsx(
              "flex h-9 shrink-0 cursor-pointer flex-col items-start justify-center rounded-md border px-2 text-left transition-colors",
              selected === t.turn ? "border-primary bg-white ring-2 ring-primary/20" : "border-transparent hover:border-line hover:bg-white",
            )}
          >
            <span className="flex items-center gap-1 text-[11px] font-semibold text-ink">
              #{t.turn}
              {ok > 0 && <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-label={`${ok} op áp dụng`} />}
              {bad > 0 && <span className="h-1.5 w-1.5 rounded-full bg-rose-500" aria-label={`${bad} op bị loại`} />}
              {t.skipped && <span className="h-1.5 w-1.5 rounded-full bg-slate-400" aria-label="bỏ qua" />}
            </span>
            <span className="font-mono text-[10px] text-ink-3">{t.new_event_ids[0]}{t.new_event_ids.length > 1 ? `+${t.new_event_ids.length - 1}` : ""}</span>
          </button>
        );
      })}
    </div>
  );
}

function Stage({ n, title, icon, summary, status, children, defaultOpen = true }: {
  n: number; title: string; icon: ReactNode; summary?: ReactNode; status: "done" | "skip" | "na" | "warn"; children?: ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => setOpen(defaultOpen), [defaultOpen]);
  return (
    <li className="animate-rise relative pl-9" style={{ animationDelay: `${n * 45}ms` }}>
      <span
        className={clsx(
          "absolute left-0 top-1 flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-bold",
          status === "done" && "bg-primary text-white",
          status === "warn" && "bg-accent text-white",
          (status === "skip" || status === "na") && "bg-slate-200 text-slate-500",
        )}
      >
        {n}
      </span>
      <span className="absolute left-[11px] top-8 bottom-[-10px] w-0.5 bg-line" aria-hidden />
      <button onClick={() => setOpen((v) => !v)} aria-expanded={open} className="flex w-full cursor-pointer items-center gap-2 py-1 text-left">
        <span className="text-ink-3">{icon}</span>
        <span className="text-[13px] font-semibold text-ink">{title}</span>
        <span className="min-w-0 flex-1 truncate text-xs text-ink-3">{summary}</span>
        <ChevronDown size={14} className={clsx("shrink-0 text-ink-3 transition-transform", open && "rotate-180")} aria-hidden />
      </button>
      {open && children && <div className="mt-1 mb-3 rounded-md border border-line bg-white p-2.5 text-xs text-ink-2">{children}</div>}
    </li>
  );
}

function evText(state: EngineState, id: string) {
  const e = state.events.find((x) => x.id === id);
  if (!e) return id;
  if (e.kind === "reaction") return `${e.user_name} thả ${e.type} vào ${e.target_id}`;
  return `${e.message!.author_name}: ${e.message!.text}`;
}

export function describeEvent(e: TraceEntry): string {
  const t = e.type;
  const id = e.task_id ? `${e.task_id} ` : "";
  switch (t) {
    case "draft_shown": return `${id}nháp HIỆN cho ${(e.to as string[]).join(", ")}`;
    case "draft_updated": return `${id}nháp cập nhật tại chỗ: ${(e.changes as string[]).join("; ")}`;
    case "draft_cancelled": return `${id}báo đã huỷ`;
    case "task_created": return `${id}tạo "${e.title}" (${e.kind})`;
    case "change_proposed": return `${id}đề xuất ${e.change_id}: ${e.field} ${e.old} → ${e.new} · người duyệt: ${(e.approvers as string[]).join(", ")}`;
    case "change_superseded": return `${id}đề xuất ${e.change_id} bị thay thế (superseded)`;
    case "change_approved": return `${id}${e.user_id} duyệt đề xuất ${e.change_id}`;
    case "change_declined": return `${id}${e.user_id} từ chối đề xuất ${e.change_id}`;
    case "change_rejected": return `${id}thay đổi ${e.field} bị loại: ${e.reason}`;
    case "status_change": case "expiry": return `${id}${e.from} → ${e.to} (${e.reason})`;
    case "user_action": return `${e.user_id} ${e.action} ${e.task_id ?? ""} [${e.source}]`;
    case "user_action_unauthorized": return `${e.user_id} ${e.action} → BỊ BỎ QUA: ${e.reason}`;
    case "user_action_unmatched": return `${e.user_id} ${e.action} ${e.message_id} → không khớp nháp/đề xuất nào`;
    case "notified": return `${id}thông báo cho người nhận cũ: ${(e.user_ids as string[]).join(", ")}`;
    case "op_applied": return `${e.op} áp vào ${e.task_id}`;
    case "op_rejected": return `${e.op} ${e.target ?? ""} bị loại: ${e.reason}`;
    case "fabricated_date_blocked": return "LLM điền ngày cho hạn sự kiện → đã bỏ";
    case "reaction_history_only": return `reaction ${(e.ids as string[]).join(", ")} chỉ ghi lịch sử`;
    default: return JSON.stringify(e);
  }
}

const EVENT_TONE: Record<string, "teal" | "amber" | "emerald" | "rose" | "violet" | "slate" | "orange"> = {
  draft_shown: "amber", draft_updated: "amber", draft_cancelled: "rose", task_created: "teal", change_proposed: "violet",
  change_superseded: "slate", change_approved: "emerald", change_declined: "rose", change_rejected: "rose", status_change: "slate",
  expiry: "slate", user_action: "emerald", user_action_unauthorized: "rose", user_action_unmatched: "rose", op_rejected: "rose",
};

export function EventLine({ e }: { e: TraceEntry }) {
  return (
    <li className="flex items-start gap-2">
      <Badge tone={EVENT_TONE[e.type] ?? "slate"} className="mt-px shrink-0 font-mono">{e.type}</Badge>
      <span className="text-ink-2">{describeEvent(e)}</span>
    </li>
  );
}

export function PipelineView({ state, turn, onPrompt, selectTask }: { state: EngineState; turn: TurnRecord | null; onPrompt: (t: TurnRecord) => void; selectTask: (id: string) => void }) {
  const ctx = turn?.context;
  const totalTok = ctx?.tokens.total ?? 0;
  const calls: ExtractCall[] = turn?.extract_calls?.length ? turn.extract_calls : turn?.extract ? [{ ...turn.extract, call: 1, reason: "initial", prompt_tokens_est: totalTok, extra_block: "", extra_tokens: 0, cost_usd: 0 }] : [];
  const callCost = calls.reduce((a, c) => a + (c.cost_usd ?? 0), 0);
  const callIn = calls.reduce((a, c) => a + c.input_tokens, 0);
  const opsOk = turn?.ops.filter((o) => o.accepted) ?? [];
  const ledgerEvents = useMemo(() => (turn?.events ?? []).filter((e) => !["op_applied", "op_rejected"].includes(e.type)), [turn]);

  if (!turn)
    return (
      <Empty icon={<Workflow size={28} />} title="Chưa có lượt xử lý nào">
        Mỗi tin mới (hoặc nhóm tin đến dồn) sinh <b>một lượt</b>: hệ thống hỏi “tin mới này thay đổi sổ cái thế nào?”. Bấm <b>Lượt kế</b> để xem từng bước.
      </Empty>
    );

  const skipAt = turn.skipped === "reaction_not_relevant" ? 2 : turn.skipped === "gate_skip" ? 4 : turn.skipped ? 5 : 99;
  const tcheck = turn.target_check ?? null;
  const st = (i: number): "done" | "skip" | "na" => (i > skipAt ? "skip" : "done");
  const budget = 3000;

  return (
    <ol className="px-3 py-3">
      {/* 1 */}
      <Stage n={1} title="Sự kiện vào hàng đợi" icon={<Inbox size={15} />} status="done"
        summary={<>{turn.new_event_ids.length} sự kiện · {turn.key} · {fmtDayTime(turn.at)}{turn.coalesced && " · gộp tin đến dồn"}</>}>
        <ul className="space-y-1">
          {turn.new_event_ids.map((id) => (
            <li key={id} className="flex gap-2">
              <Mono className="shrink-0 text-primary-ink">{id}</Mono>
              <span className="text-ink-2">{evText(state, id)}</span>
            </li>
          ))}
        </ul>
        {turn.coalesced && <p className="mt-2 text-ink-3">Các tin cách tin đầu ≤ <Mono>coalesce_seconds</Mono> được xử lý chung và cùng là TIN MỚI.</p>}
        {turn.skipped === "reaction_not_relevant" && (
          <p className="mt-2 rounded bg-slate-50 p-1.5 text-ink-3">Reaction vào tin nằm ngoài K tin gần nhất và không là bằng chứng của task đang mở → chỉ ghi vào lịch sử, không gọi LLM.</p>
        )}
      </Stage>

      {/* 2 */}
      <Stage n={2} title="Trước lượt: hết hạn & thao tác duyệt" icon={<Timer size={15} />} status="done" defaultOpen={turn.pre.expirations.length + turn.pre.user_actions.length > 0}
        summary={turn.pre.expirations.length + turn.pre.user_actions.length ? `${turn.pre.user_actions.filter((e) => e.type.startsWith("user_action")).length} user_action · ${turn.pre.expirations.length} hết hạn` : "không có gì"}>
        {turn.pre.expirations.length + turn.pre.user_actions.length === 0 ? (
          <p className="text-ink-3">Không có luật hết hạn hay user_action nào tới thời điểm này.</p>
        ) : (
          <ul className="space-y-1">{[...turn.pre.user_actions, ...turn.pre.expirations].map((e, i) => <EventLine key={i} e={e} />)}</ul>
        )}
      </Stage>

      {/* 3 */}
      <Stage n={3} title="Dựng ngữ cảnh" icon={<Layers size={15} />} status={ctx ? "done" : "skip"}
        summary={ctx ? `${totalTok} token · ${bySrc(ctx.candidates, "linked").length} được link · ${bySrc(ctx.candidates, "matched").length} khớp nội dung · ${bySrc(ctx.candidates, "recent").length} gần nhất · K=${ctx.window_k_used}${ctx.trims.length ? ` · cắt ${ctx.trims.length}` : ""}` : "bỏ qua"}>
        {ctx && (
          <div className="space-y-3">
            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="font-medium text-ink">Token theo khối</span>
                <button onClick={() => onPrompt(turn)} className="inline-flex cursor-pointer items-center gap-1 rounded px-1.5 py-0.5 text-primary-ink hover:bg-teal-50">
                  <Code2 size={13} /> Xem prompt
                </button>
              </div>
              <div className="flex h-3 w-full overflow-hidden rounded-full bg-slate-100" role="img" aria-label={`Tổng ${totalTok} token`}>
                {BLOCKS.map((b) => (
                  <span key={b.k} className={b.color} style={{ width: `${((ctx.tokens[b.k] ?? 0) / Math.max(totalTok, budget)) * 100}%` }} title={`${b.label}: ${ctx.tokens[b.k] ?? 0}`} />
                ))}
              </div>
              <div className="mt-1.5 grid grid-cols-3 gap-x-3 gap-y-0.5 sm:grid-cols-6">
                {BLOCKS.map((b) => (
                  <span key={b.k} className="flex items-center gap-1 text-[11px] text-ink-3">
                    <span className={clsx("h-2 w-2 rounded-sm", b.color)} />
                    {b.label.split(" · ")[1]} <b className="tabular-nums text-ink-2">{ctx.tokens[b.k] ?? 0}</b>
                  </span>
                ))}
              </div>
              {ctx.trims.length > 0 && (
                <p className="mt-1.5 text-orange-700">Vượt ngân sách → budget_trim: <Mono>{ctx.trims.join(" → ")}</Mono></p>
              )}
            </div>

            <LinkedGroups candidates={ctx.candidates} retrieval={ctx.retrieval} selectTask={selectTask} />

            <div className="grid grid-cols-1 gap-1 text-[11.5px] text-ink-3 sm:grid-cols-3">
              <span className="font-medium text-ink-2 sm:col-span-3">Lịch sử gần</span>
              <span>Cửa sổ K: <b className="text-ink-2">{ctx.window_ids.length}</b> tin</span>
              <span>Chuỗi reply ngoài K: <b className="text-ink-2">{ctx.reply_chain_ids.join(", ") || "—"}</b></span>
              <span>Bằng chứng kèm task: <b className="text-ink-2">{ctx.evidence_ids.join(", ") || "—"}</b></span>
            </div>
          </div>
        )}
      </Stage>

      {/* 4 */}
      <Stage n={4} title="Cổng lọc" icon={<Filter size={15} />} status={!turn.gate ? "na" : turn.gate.skipped ? "warn" : st(4)} defaultOpen={!!turn.gate}
        summary={!ctx ? "bỏ qua" : !turn.gate ? "gate_mode = none → mọi lượt đều gọi bộ trích (trần chất lượng)" : `p = ${turn.gate.p?.toFixed(2)} · ${turn.gate.mode}${turn.gate.skipped ? " · CHẶN" : ""}`}>
        {turn.gate && (
          <div className="space-y-2">
            <div className="relative h-3 w-full rounded-full bg-slate-100">
              <span className={clsx("absolute inset-y-0 left-0 rounded-full", turn.gate.skipped ? "bg-slate-400" : "bg-primary")} style={{ width: `${(turn.gate.p ?? 0) * 100}%` }} />
              <span className="absolute -top-1 bottom-[-4px] w-0.5 bg-accent" style={{ left: `${turn.gate.threshold * 100}%` }} title={`ngưỡng ${turn.gate.threshold}`} />
            </div>
            <p>
              <Mono>p = P(yes)/(P(yes)+P(no))</Mono> = <b>{turn.gate.p?.toFixed(3)}</b> · ngưỡng {turn.gate.threshold} ·{" "}
              {turn.gate.mode === "shadow" ? "shadow: chỉ ghi p, không chặn" : turn.gate.skipped ? "llm: bỏ lượt (gate_skip)" : "llm: cho qua"} · model <Mono>{turn.gate.model}</Mono>
            </p>
            {turn.gate.warning && <p className="text-orange-700">{turn.gate.warning}</p>}
          </div>
        )}
      </Stage>

      {/* 5 */}
      <Stage n={5} title="Bộ trích (LLM)" icon={<Sparkles size={15} />} status={turn.extract ? (turn.extract.error ? "warn" : "done") : "skip"} defaultOpen={!!turn.extract && (turn.ops.length > 0 || !!turn.extract.error || calls.length > 1)}
        summary={turn.extract ? `${calls.length} lần gọi · ${turn.ops.length ? `${turn.ops.length} op` : "ops rỗng — không đổi sổ cái"} · ${callIn} tok · ${usd(callCost)}${turn.extract.disk_cache ? " · cache đĩa" : ""}` : turn.skipped ? SKIP_LABEL[turn.skipped] : "—"}>
        {turn.extract && (
          <div className="space-y-3">
            {calls.map((c) => <CallCard key={c.call} c={c} />)}
            {calls.length > 1 && (
              <p className="text-[11.5px] text-ink-3">Lần gọi 2 dùng <b>cùng prompt lần 1</b> (để cache trúng) + kết quả kiểm tra target; kết quả lần 2 thay cho lần 1. Tổng lượt: {callIn} token input · {usd(callCost)}.</p>
            )}
          </div>
        )}
      </Stage>

      {/* 6 */}
      <Stage n={6} title="Kiểm tra target" icon={<Search size={15} />} status={!tcheck ? "na" : tcheck.triggered ? "warn" : "done"} defaultOpen={!!tcheck?.triggered}
        summary={!tcheck ? "không có UPDATE / CANCEL cần kiểm tra"
          : tcheck.triggered ? `${tcheck.checks.flatMap((c) => c.candidates).length} task ngoài prompt khớp hơn → hỏi lại LLM`
          : "task được nhắm khớp TIN MỚI"}>
        {tcheck && <TargetCheckView t={tcheck} selectTask={selectTask} />}
      </Stage>

      {/* 7 */}
      <Stage n={7} title="Validate & quy đổi" icon={<ShieldCheck size={15} />} status={turn.ops.some((o) => !o.accepted) ? "warn" : turn.extract ? "done" : "skip"} defaultOpen={turn.ops.length > 0}
        summary={turn.ops.length ? `${opsOk.length} áp dụng · ${turn.ops.length - opsOk.length} bị loại` : "không có op"}>
        {turn.ops.length > 0 && (
          <ul className="space-y-2">
            {turn.ops.map((o, i) => (
              <li key={i} className={clsx("rounded-md border p-2", o.accepted ? "border-emerald-200 bg-emerald-50/50" : "border-rose-200 bg-rose-50/60")}>
                <div className="flex flex-wrap items-center gap-1.5">
                  {o.accepted ? <Check size={14} className="text-emerald-600" aria-label="hợp lệ" /> : <X size={14} className="text-rose-600" aria-label="bị loại" />}
                  <b className="font-mono text-ink">{o.op}</b>
                  {o.target && <Mono>{o.target}</Mono>}
                  {o.task_id && <button onClick={() => selectTask(o.task_id!)} className="cursor-pointer font-mono text-primary-ink hover:underline">→ {o.task_id}</button>}
                  {o.reason && <Badge tone="rose" className="font-mono">{o.reason}</Badge>}
                </div>
                {o.effect && <p className="mt-1 font-medium text-ink">{o.effect}</p>}
                {o.notes.length > 0 && (
                  <ul className="mt-1 space-y-0.5 text-[11.5px] text-ink-3">
                    {o.notes.map((n, j) => (
                      <li key={j} className={clsx(/unresolved|unresolvable|fabricated|no_op|suppressed/.test(n) && "text-orange-700")}>• {n}</li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </Stage>

      {/* 8 */}
      <Stage n={8} title="Cập nhật sổ cái" icon={<Workflow size={15} />} status={ledgerEvents.length ? "done" : "na"} defaultOpen={ledgerEvents.length > 0}
        summary={ledgerEvents.length ? `${ledgerEvents.length} sự kiện` : "sổ cái không đổi"}>
        {ledgerEvents.length > 0 && <ul className="space-y-1">{ledgerEvents.map((e, i) => <EventLine key={i} e={e} />)}</ul>}
      </Stage>
      {turn.skipped && (
        <li className="pl-9 text-xs text-ink-3">
          <CircleSlash size={13} className="mr-1 inline" aria-hidden /> Lượt dừng ở đây: {SKIP_LABEL[turn.skipped]}. Tin vẫn nằm trong LỊCH SỬ GẦN của lượt sau.
        </li>
      )}
    </ol>
  );
}

function StatusDot({ status }: { status: string }) {
  return <StatusBadge status={status as never} />;
}

function TaskRow({ t, selectTask, children }: { t: { local: string; task_id: string; title: string; status: string }; selectTask: (id: string) => void; children?: ReactNode }) {
  return (
    <li onClick={() => selectTask(t.task_id)} className="cursor-pointer rounded px-1 py-1 hover:bg-slate-50">
      <div className="flex items-center gap-2">
        <Mono className="w-7 shrink-0 text-primary-ink">{t.local || "—"}</Mono>
        <span className="min-w-0 flex-1 truncate font-medium text-ink">{t.title}</span>
        <StatusDot status={t.status} />
      </div>
      {children && <div className="ml-9 text-[11px] text-ink-3">{children}</div>}
    </li>
  );
}

const bySrc = (c: CandidateRow[], ...src: string[]) => c.filter((t) => src.includes(t.source));

function LinkedGroups({ candidates, retrieval, selectTask }: {
  candidates: CandidateRow[]; retrieval: { query: string; people: string[]; joined_reply: string | null }; selectTask: (id: string) => void;
}) {
  const linked = bySrc(candidates, "linked");
  const matched = bySrc(candidates, "matched");
  const recent = bySrc(candidates, "recent");
  const ev = (t: CandidateRow) => (t.evidence_shown.length ? ` · kèm ${t.evidence_shown.join(", ")}` : "");
  return (
    <div className="space-y-2">
      <div className="rounded-md border border-amber-300 bg-amber-50/70 p-2">
        <div className="mb-1 flex items-center gap-1.5 font-medium text-amber-900">
          <Link2 size={13} aria-hidden /> Được link <span className="font-normal text-amber-800">— người gửi tự reply / thả reaction vào tin bằng chứng · khối 3a, không bao giờ bị cắt</span>
        </div>
        {linked.length === 0 ? (
          <p className="text-ink-3">TIN MỚI không reply/react vào tin bằng chứng của task nào.</p>
        ) : (
          <ul>{linked.map((t) => <TaskRow key={t.task_id} t={t} selectTask={selectTask}>{t.why} · {t.task_id}{ev(t)}</TaskRow>)}</ul>
        )}
      </div>
      <div className="rounded-md border border-teal-300 bg-teal-50/60 p-2">
        <div className="mb-1 flex items-center gap-1.5 font-medium text-teal-900">
          <Search size={13} aria-hidden /> Khớp nội dung <span className="font-normal text-teal-800">— code tìm trong kho bằng TIN MỚI trước lần gọi 1 (BM25 + embedding) · nhãn [matched]</span>
        </div>
        <p className="mb-1 truncate text-[11px] text-ink-3" title={retrieval.query}>
          truy vấn: <Mono>{retrieval.query || "(trống)"}</Mono> · người: {retrieval.people.join(", ") || "—"}{retrieval.joined_reply ? ` · nối tin được reply ${retrieval.joined_reply}` : ""}
        </p>
        {matched.length === 0 ? (
          <p className="text-ink-3">Không task nào khớp từ khoá / ngữ nghĩa (task chỉ trùng người không tính).</p>
        ) : (
          <ul>{matched.map((t) => <TaskRow key={t.task_id} t={t} selectTask={selectTask}><Mono>{t.why}</Mono> · {t.task_id}{ev(t)}</TaskRow>)}</ul>
        )}
      </div>
      <div className="rounded-md border border-line p-2">
        <div className="mb-1 font-medium text-ink">Gần nhất <span className="font-normal text-ink-3">— theo lần được nhắc cuối trong chat · lấp cho đủ candidate_tasks_max · nhãn [recent]</span></div>
        {recent.length === 0 ? (
          <p className="text-ink-3">Chưa có task đang mở nào khác trong hội thoại này.</p>
        ) : (
          <ul>{recent.map((t) => <TaskRow key={t.task_id} t={t} selectTask={selectTask}>{t.task_id}{ev(t)}</TaskRow>)}</ul>
        )}
      </div>
    </div>
  );
}

function CallCard({ c }: { c: ExtractCall }) {
  const [showBlock, setShowBlock] = useState(false);
  return (
    <div className="rounded-md border border-line p-2">
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
        <b className="text-ink">Gọi {CALL_REASON[c.reason] ?? c.reason}</b>
        <Badge>input {c.input_tokens} tok</Badge>
        <Badge>cache {c.cached_tokens}</Badge>
        <Badge>output {c.output_tokens}</Badge>
        <Badge tone="teal">{usd(c.cost_usd ?? 0)}</Badge>
        <Badge>{c.latency_ms} ms</Badge>
        {c.attempts > 1 && <Badge tone="orange">retry {c.attempts - 1}</Badge>}
        {c.disk_cache && <Badge>cache đĩa</Badge>}
        {c.model === "heuristic" && <Badge tone="orange" title="Không có OPENAI_API_KEY: bộ trích giả lập bằng luật, chỉ để xem luồng">giả lập — không phải LLM thật</Badge>}
        {c.extra_block && (
          <button onClick={() => setShowBlock((v) => !v)} className="cursor-pointer text-primary-ink hover:underline">
            {showBlock ? "Ẩn kết quả kiểm tra" : `Xem kết quả kiểm tra target (${c.extra_tokens} tok)`}
          </button>
        )}
      </div>
      {showBlock && <pre className="scroll-thin mb-1.5 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-amber-50 p-2 font-mono text-[11px] leading-4 text-ink-2">{c.extra_block}</pre>}
      {c.error && <p className="text-rose-700">{c.error}</p>}
      <pre className="scroll-thin max-h-64 overflow-auto rounded bg-slate-900 p-2.5 font-mono text-[11px] leading-4 text-slate-100">{pretty(c.raw)}</pre>
    </div>
  );
}

function TargetCheckView({ t, selectTask }: { t: NonNullable<TurnRecord["target_check"]>; selectTask: (id: string) => void }) {
  return (
    <div className="space-y-1.5">
      <p className="text-[11.5px] text-ink-3">UPDATE/CANCEL nhắm task không được link → code tìm trong kho bằng nội dung TIN MỚI; task ngoài prompt khớp hơn target (từ khoá hoặc ngữ nghĩa, hoặc T&lt;n&gt; không tồn tại) → đưa vào lần gọi 2, LLM quyết.</p>
      {t.checks.map((c, i) => (
        <div key={i} className="rounded-md border border-line p-2">
          <p><b className="font-mono">{c.op}</b> {c.target} → {c.target_title ? `“${c.target_title}” (${c.target_overlap} từ khoá chung${c.target_similarity != null ? `, ngữ nghĩa ${c.target_similarity.toFixed(2)}` : ""} với tin mới${c.threshold != null ? ` · ngưỡng ${c.threshold}` : ""})` : <span className="text-rose-700">không có trong prompt</span>}</p>
          {c.results.length > 0 && (
            <ul className="mt-1">{c.results.map((r) => (
              <TaskRow key={r.task_id} t={{ ...r, local: "" }} selectTask={selectTask}>
                {c.candidates.includes(r.task_id) ? <b className="text-orange-700">ứng viên</b> : "không đủ"} · {r.overlap} từ khoá chung ({r.matched.join(", ")}) · điểm {r.score.toFixed(2)}
              </TaskRow>
            ))}</ul>
          )}
        </div>
      ))}
      {t.call && <p className="rounded bg-teal-50 p-1.5 text-teal-900">Đã hỏi lại LLM (lần gọi {t.call}) → {t.ops_after?.join(", ") || "ops rỗng"}.</p>}
    </div>
  );
}

function pretty(raw: string) {
  try {
    return JSON.stringify(JSON.parse(raw), (k, v) => (v === null ? undefined : v), 2);
  } catch {
    return raw;
  }
}

export { fmtTime };
