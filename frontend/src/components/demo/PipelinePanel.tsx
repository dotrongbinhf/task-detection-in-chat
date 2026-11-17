"use client";
import clsx from "clsx";
import { Check, ChevronDown, CircleSlash, Code2, Inbox, Layers, Link2, List, Search, ShieldCheck, Sparkles, Timer, X, Workflow } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { fmtDayTime, fmtTime } from "@/lib/format";
import type { CandidateRow, EngineState, ExtractCall, TraceEntry, TurnRecord } from "@/lib/types";
import { Badge, Empty, Mono, StatusBadge } from "./ui";

type BlockKey = "b1" | "b0" | "b2" | "b3a" | "b3b" | "b3c" | "b4" | "b5";
const BLOCKS: { k: BlockKey; label: string; color: string }[] = [
  { k: "b1", label: "1 · Hướng dẫn", color: "bg-slate-400" },
  { k: "b0", label: "0 · MEMBERS", color: "bg-slate-300" },
  { k: "b2", label: "2 · Today", color: "bg-sky-400" },
  { k: "b3a", label: "3a · LINKED", color: "bg-amber-500" },
  { k: "b3b", label: "3b · DETAILS", color: "bg-orange-300" },
  { k: "b3c", label: "3c · INDEX", color: "bg-yellow-200" },
  { k: "b4", label: "4 · Lịch sử gần", color: "bg-teal-500" },
  { k: "b5", label: "5 · Tin mới", color: "bg-violet-500" },
];

const CALL_REASON: Record<string, string> = { initial: "lần 1", detail_check: "lần 2 · detail_check" };
const usd = (v: number) => (v === 0 ? "$0" : `$${v.toFixed(v < 0.001 ? 5 : 4)}`);

const SKIP_LABEL: Record<string, string> = {
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
  const r = e.message!.reactions.length ? ` [${e.message!.reactions.map((x) => x.type).join(", ")}]` : "";
  return `${e.message!.author_name}: ${e.message!.text}${r}`;
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
        Các tin đến liền nhau của một hội thoại (tới khi yên <Mono>quiet_seconds</Mono>) là <b>một lượt</b>: hệ thống hỏi “tin mới này thay đổi sổ cái thế nào?”. Bấm <b>Lượt kế</b> để xem từng bước.
      </Empty>
    );

  const dcheck = turn.detail_check ?? null;
  const budget = 3500;
  const post = turn.post?.user_actions ?? [];

  return (
    <ol className="px-3 py-3">
      {/* 1 */}
      <Stage n={1} title="Sự kiện vào hàng đợi" icon={<Inbox size={15} />} status="done"
        summary={<>{turn.new_event_ids.length} tin · {turn.key} · {fmtDayTime(turn.at)}</>}>
        <ul className="space-y-1">
          {turn.new_event_ids.map((id) => (
            <li key={id} className="flex gap-2">
              <Mono className="shrink-0 text-primary-ink">{id}</Mono>
              <span className="text-ink-2">{evText(state, id)}</span>
            </li>
          ))}
        </ul>
        {turn.new_event_ids.length > 1 && <p className="mt-2 text-ink-3">Các tin cách tin liền trước ≤ <Mono>quiet_seconds</Mono> (trần <Mono>max_wait_seconds</Mono>, <Mono>max_batch_messages</Mono>) cùng là TIN MỚI của lượt này. Reaction không sinh lượt — in kèm dòng tin.</p>}
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
        summary={ctx ? `${totalTok} token · ${bySrc(ctx.candidates, "linked").length} link · ${bySrc(ctx.candidates, "details").length} chi tiết · ${bySrc(ctx.candidates, "index").length} trong index · K=${ctx.window_k_used}${ctx.trims.length ? ` · cắt ${ctx.trims.length}` : ""}` : "bỏ qua"}>
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
              <div className="mt-1.5 grid grid-cols-3 gap-x-3 gap-y-0.5 sm:grid-cols-4">
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

            <TierGroups candidates={ctx.candidates} retrieval={ctx.retrieval} selectTask={selectTask} />

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
      <Stage n={4} title="Bộ trích (LLM)" icon={<Sparkles size={15} />} status={turn.extract ? (turn.extract.error ? "warn" : "done") : "skip"} defaultOpen={!!turn.extract && (turn.ops.length > 0 || !!turn.extract.error || calls.length > 1)}
        summary={turn.extract ? `${calls.length} lần gọi · ${turn.ops.length ? `${turn.ops.length} op` : "ops rỗng — không đổi sổ cái"} · ${callIn} tok · ${usd(callCost)}${turn.extract.disk_cache ? " · cache đĩa" : ""}` : turn.skipped ? SKIP_LABEL[turn.skipped] : "—"}>
        {turn.extract && (
          <div className="space-y-3">
            {calls.map((c) => <CallCard key={c.call} c={c} />)}
            {calls.length > 1 && (
              <p className="text-[11.5px] text-ink-3">Lần gọi 2 dùng <b>cùng prompt lần 1</b> (để cache trúng) + chi tiết task chỉ có ở index; kết quả lần 2 thay cho lần 1. Tổng lượt: {callIn} token input · {usd(callCost)}.</p>
            )}
          </div>
        )}
      </Stage>

      {/* 5 */}
      <Stage n={5} title="detail_check" icon={<Search size={15} />} status={dcheck ? "warn" : "na"} defaultOpen={!!dcheck}
        summary={dcheck ? `UPDATE thay notes / title trên ${dcheck.tasks.join(", ")} (chỉ có ở index) → gọi lần 2 kèm chi tiết` : "không cần lần gọi 2"}>
        {dcheck && (
          <p>
            Lần 1: <Mono>{dcheck.ops_before.join(", ") || "—"}</Mono> → lần 2: <Mono>{dcheck.ops_after?.join(", ") || "ops rỗng"}</Mono>.
            Kích hoạt bởi cấu trúc op (không trông vào model tự xin); <Mono>notes_add</Mono>, đổi hạn, đổi người, CANCEL không kích hoạt.
          </p>
        )}
      </Stage>

      {/* 6 */}
      <Stage n={6} title="Validate & quy đổi" icon={<ShieldCheck size={15} />} status={turn.ops.some((o) => !o.accepted) ? "warn" : turn.extract ? "done" : "skip"} defaultOpen={turn.ops.length > 0}
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
                      <li key={j} className={clsx(/unknown|unresolvable|fabricated|no_op|suppressed|no_base/.test(n) && "text-orange-700")}>• {n}</li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </Stage>

      {/* 7 */}
      <Stage n={7} title="Cập nhật sổ cái" icon={<Workflow size={15} />} status={ledgerEvents.length + post.length ? "done" : "na"} defaultOpen={ledgerEvents.length + post.length > 0}
        summary={ledgerEvents.length + post.length ? `${ledgerEvents.length} sự kiện${post.length ? ` · ${post.length} thao tác duyệt được hoãn tới sau lượt` : ""}` : "sổ cái không đổi"}>
        {ledgerEvents.length + post.length > 0 && <ul className="space-y-1">{[...ledgerEvents, ...post].map((e, i) => <EventLine key={i} e={e} />)}</ul>}
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

function TierGroups({ candidates, retrieval, selectTask }: {
  candidates: CandidateRow[]; retrieval: { query: string; joined_reply: string | null; index_total?: number; similarity?: Record<string, number> }; selectTask: (id: string) => void;
}) {
  const linked = bySrc(candidates, "linked");
  const details = bySrc(candidates, "details");
  const index = bySrc(candidates, "index");
  const ev = (t: CandidateRow) => (t.evidence_shown.length ? ` · kèm ${t.evidence_shown.join(", ")}` : "");
  const sim = (t: CandidateRow) => (retrieval.similarity?.[t.task_id] != null ? ` · cos ${retrieval.similarity[t.task_id].toFixed(2)}` : "");
  return (
    <div className="space-y-2">
      <div className="rounded-md border border-amber-300 bg-amber-50/70 p-2">
        <div className="mb-1 flex items-center gap-1.5 font-medium text-amber-900">
          <Link2 size={13} aria-hidden /> LINKED_TASKS <span className="font-normal text-amber-800">— TIN MỚI reply vào tin bằng chứng · không bao giờ bị cắt</span>
        </div>
        {linked.length === 0 ? (
          <p className="text-ink-3">TIN MỚI không reply vào tin bằng chứng của task nào.</p>
        ) : (
          <ul>{linked.map((t) => <TaskRow key={t.task_id} t={t} selectTask={selectTask}>{t.why} · {t.task_id}{ev(t)}</TaskRow>)}</ul>
        )}
      </div>
      <div className="rounded-md border border-teal-300 bg-teal-50/60 p-2">
        <div className="mb-1 flex items-center gap-1.5 font-medium text-teal-900">
          <Search size={13} aria-hidden /> TASK_DETAILS <span className="font-normal text-teal-800">— 3 task mở nhắc gần nhất + 2 gần nghĩa nhất với TIN MỚI (embedding, chỉ xếp hạng)</span>
        </div>
        <p className="mb-1 truncate text-[11px] text-ink-3" title={retrieval.query}>
          truy vấn: <Mono>{retrieval.query || "(trống)"}</Mono>{retrieval.joined_reply ? ` · nối tin được reply ${retrieval.joined_reply}` : ""}
        </p>
        {details.length === 0 ? (
          <p className="text-ink-3">Chưa có task đang mở nào khác trong hội thoại này.</p>
        ) : (
          <ul>{details.map((t) => <TaskRow key={t.task_id} t={t} selectTask={selectTask}>{t.why} · {t.task_id}{sim(t)}{ev(t)}</TaskRow>)}</ul>
        )}
      </div>
      <div className="rounded-md border border-line p-2">
        <div className="mb-1 flex items-center gap-1.5 font-medium text-ink">
          <List size={13} aria-hidden /> TASK_INDEX <span className="font-normal text-ink-3">— mọi task mở còn lại + task bị từ chối gần đây, một dòng một task · {index.length}/{retrieval.index_total ?? index.length}</span>
        </div>
        {index.length === 0 ? (
          <p className="text-ink-3">Không còn task nào khác.</p>
        ) : (
          <ul className="scroll-thin max-h-48 overflow-y-auto">{index.map((t) => <TaskRow key={t.task_id} t={t} selectTask={selectTask}>{t.why === "overflow" ? "chỗ tràn (gần nghĩa)" : "gần nhất"} · {t.task_id}{sim(t)}</TaskRow>)}</ul>
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
        {c.extra_block && (
          <button onClick={() => setShowBlock((v) => !v)} className="cursor-pointer text-primary-ink hover:underline">
            {showBlock ? "Ẩn khối detail_check" : `Xem khối detail_check (${c.extra_tokens} tok)`}
          </button>
        )}
      </div>
      {showBlock && <pre className="scroll-thin mb-1.5 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-amber-50 p-2 font-mono text-[11px] leading-4 text-ink-2">{c.extra_block}</pre>}
      {c.error && <p className="text-rose-700">{c.error}</p>}
      <pre className="scroll-thin max-h-64 overflow-auto rounded bg-slate-900 p-2.5 font-mono text-[11px] leading-4 text-slate-100">{pretty(c.raw)}</pre>
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
