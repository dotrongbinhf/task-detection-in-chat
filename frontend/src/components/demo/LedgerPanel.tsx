"use client";
import clsx from "clsx";
import { ArrowRight, BookOpenCheck, CalendarClock, Check, CircleUserRound, EyeOff, History, Inbox, Link2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { displayDue, fmtDayTime } from "@/lib/format";
import type { EngineState, Member, PendingChange, Task } from "@/lib/types";
import type { Engine } from "./useEngine";
import { Avatar, Badge, Button, Empty, KIND_LABEL, PanelHeader, ROLE_META, Segmented, StatusBadge } from "./ui";

type Filter = "open" | "hidden" | "all";

export function LedgerPanel({ engine, viewer, setViewer, selectedTask, onSelectTask }: {
  engine: Engine; viewer: string; setViewer: (u: string) => void; selectedTask: string | null; onSelectTask: (id: string | null) => void;
}) {
  const s = engine.state!;
  const [filter, setFilter] = useState<Filter>("open");
  const allMembers = useMemo(() => {
    const m = new Map<string, Member>();
    Object.values(s.members).flat().forEach((x) => m.set(x.user_id, x));
    return [...m.values()];
  }, [s.members]);
  const nameOf = (u: string) => allMembers.find((m) => m.user_id === u)?.display_name ?? u;

  const pending = s.changes.filter((c) => c.status === "pending");
  const inboxDrafts = s.tasks.filter((t) => (t.status === "draft" || t.status === "approved") && t.approvals[viewer] === "pending");
  const inboxChanges = pending.filter((c) => c.approver_ids.includes(viewer));
  const inboxCount = inboxDrafts.length + inboxChanges.length;

  const counts = {
    open: s.tasks.filter((t) => ["draft", "approved", "declined"].includes(t.status)).length,
    hidden: s.tasks.filter((t) => t.status === "candidate").length,
    all: s.tasks.length,
  };
  const tasks = [...s.tasks]
    .filter((t) => (filter === "open" ? ["draft", "approved", "declined"].includes(t.status) : filter === "hidden" ? t.status === "candidate" : true))
    .reverse();

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-line bg-surface" aria-label="Sổ cái task">
      <PanelHeader title="Sổ cái task" icon={<BookOpenCheck size={16} aria-hidden />} sub={`${s.tasks.length} task · ${pending.length} đề xuất chờ`} />

      {/* Hộp duyệt */}
      <div className="border-b border-line bg-gradient-to-b from-orange-50/70 to-white px-3 py-2.5">
        <div className="mb-2 flex items-center gap-2">
          <Inbox size={15} className="text-accent" aria-hidden />
          <span className="text-[13px] font-semibold text-ink">Hộp duyệt của</span>
          <label htmlFor="viewer" className="sr-only">Xem với tư cách</label>
          <select id="viewer" value={viewer} onChange={(e) => setViewer(e.target.value)} className="h-7 cursor-pointer rounded-md border border-line bg-white px-1.5 text-xs font-medium">
            {allMembers.map((m) => (
              <option key={m.user_id} value={m.user_id}>{m.display_name}</option>
            ))}
          </select>
          {inboxCount > 0 && <span className="rounded-full bg-accent px-1.5 text-[11px] font-semibold leading-5 text-white">{inboxCount}</span>}
        </div>
        {inboxCount === 0 ? (
          <p className="text-xs text-ink-3">Không có nháp hay đề xuất nào chờ {nameOf(viewer)} duyệt. Người duyệt luôn là <b>người nhận</b>.</p>
        ) : (
          <ul className="scroll-thin max-h-56 space-y-1.5 overflow-y-auto">
            {inboxDrafts.map((t) => (
              <li key={t.task_id} className="animate-rise rounded-md border border-amber-200 bg-white p-2">
                <div className="flex items-start gap-2">
                  <button onClick={() => onSelectTask(t.task_id)} className="min-w-0 flex-1 cursor-pointer text-left">
                    <div className="flex items-center gap-1.5">
                      <Badge tone="amber">{t.status === "draft" ? "Nháp mới" : "Phần của bạn"}</Badge>
                      <span className="font-mono text-[10.5px] text-ink-3">{t.task_id}</span>
                    </div>
                    <p className="mt-0.5 truncate text-[13px] font-medium text-ink">{t.title}</p>
                    <p className="text-[11.5px] text-ink-3">Hạn: {displayDue(t)}{t.assigner_id ? ` · giao bởi ${nameOf(t.assigner_id)}` : ""}</p>
                  </button>
                  <div className="flex shrink-0 gap-1">
                    <Button size="sm" variant="success" disabled={engine.busy} onClick={() => engine.act({ user_id: viewer, action: "approve", task_id: t.task_id })} aria-label={`Duyệt ${t.title}`}>
                      <Check size={13} /> Duyệt
                    </Button>
                    <Button size="sm" variant="danger" disabled={engine.busy} onClick={() => engine.act({ user_id: viewer, action: "decline", task_id: t.task_id })} aria-label={`Từ chối ${t.title}`}>
                      <X size={13} />
                    </Button>
                  </div>
                </div>
              </li>
            ))}
            {inboxChanges.map((c) => {
              const t = s.tasks.find((x) => x.task_id === c.task_id)!;
              return (
                <li key={c.change_id} className="animate-rise rounded-md border border-violet-200 bg-white p-2">
                  <div className="flex items-start gap-2">
                    <button onClick={() => onSelectTask(t.task_id)} className="min-w-0 flex-1 cursor-pointer text-left">
                      <div className="flex items-center gap-1.5">
                        <Badge tone="violet">Đề xuất thay đổi</Badge>
                        <span className="font-mono text-[10.5px] text-ink-3">{c.change_id} · {t.task_id}</span>
                      </div>
                      <p className="mt-0.5 truncate text-[13px] font-medium text-ink">{t.title}</p>
                      <ChangeDiff c={c} />
                    </button>
                    <div className="flex shrink-0 gap-1">
                      <Button size="sm" variant="success" disabled={engine.busy} onClick={() => engine.act({ user_id: viewer, action: "approve_change", change_id: c.change_id })} aria-label="Chấp nhận đề xuất">
                        <Check size={13} /> Chấp nhận
                      </Button>
                      <Button size="sm" variant="danger" disabled={engine.busy} onClick={() => engine.act({ user_id: viewer, action: "decline_change", change_id: c.change_id })} aria-label="Từ chối đề xuất">
                        <X size={13} />
                      </Button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="flex items-center justify-between border-b border-line px-3 py-1.5">
        <Segmented
          label="Lọc task"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "open", label: `Hiển thị (${counts.open})` },
            { value: "hidden", label: <><EyeOff size={12} aria-hidden /> Ẩn ({counts.hidden})</> },
            { value: "all", label: `Tất cả (${counts.all})` },
          ]}
        />
      </div>

      <div className="scroll-thin flex-1 space-y-2 overflow-y-auto p-2.5">
        {tasks.length === 0 && (
          <Empty title={filter === "hidden" ? "Không có task ẩn" : "Sổ cái đang trống"}>
            {filter === "hidden"
              ? "Task ẩn (candidate) là việc đã phát hiện nhưng chưa xác định người nhận — chưa hiện cho ai."
              : "Task xuất hiện ở đây khi bộ trích tạo CREATE hợp lệ."}
          </Empty>
        )}
        {tasks.map((t) => (
          <TaskCard key={t.task_id} t={t} state={s} nameOf={nameOf} selected={selectedTask === t.task_id} onSelect={() => onSelectTask(selectedTask === t.task_id ? null : t.task_id)}>
            {t.status === "draft" && !t.assignee_ids.includes(viewer) && (
              <Button size="sm" variant="ghost" className="mt-1.5 text-ink-3" disabled={engine.busy}
                title="Người duyệt là người nhận — thao tác này sẽ bị ghi user_action_unauthorized"
                onClick={() => engine.act({ user_id: viewer, action: "approve", task_id: t.task_id })}>
                Thử duyệt với tư cách {nameOf(viewer)} (không phải người nhận)
              </Button>
            )}
          </TaskCard>
        ))}
      </div>
    </section>
  );
}

function ChangeDiff({ c }: { c: PendingChange }) {
  const field = { due: "Hạn", assignee_refs: "Người nhận", status: "Trạng thái", title: "Tiêu đề", notes: "Ghi chú", kind: "Loại" }[c.field];
  return (
    <p className="mt-0.5 flex flex-wrap items-center gap-1 text-[11.5px] text-ink-2">
      <span className="text-ink-3">{field}:</span>
      <span className="text-ink-3 line-through">{c.old_display}</span>
      <ArrowRight size={11} aria-hidden />
      <b className="text-violet-700">{c.new_display}</b>
      <span className="text-ink-3">· bằng chứng {c.evidence_message_id}</span>
    </p>
  );
}

function TaskCard({ t, state, nameOf, selected, onSelect, children }: { t: Task; state: EngineState; nameOf: (u: string) => string; selected: boolean; onSelect: () => void; children?: React.ReactNode }) {
  const changes = state.changes.filter((c) => c.task_id === t.task_id);
  const pending = changes.filter((c) => c.status === "pending");
  const [showHist, setShowHist] = useState(false);
  const faded = ["cancelled", "expired", "stale", "closed", "declined"].includes(t.status);
  return (
    <article
      className={clsx(
        "rounded-lg border bg-white p-2.5 transition-shadow",
        selected ? "border-accent ring-2 ring-accent/25" : "border-line hover:border-line-strong",
        t.status === "candidate" && "border-dashed",
        faded && "opacity-70",
      )}
    >
      <button onClick={onSelect} className="block w-full cursor-pointer text-left" aria-pressed={selected}>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-[11px] font-semibold text-ink-3">{t.task_id}</span>
          <StatusBadge status={t.status} />
          <Badge tone="slate">{KIND_LABEL[t.kind]}</Badge>
          {t.draft_update_count > 0 && <Badge tone="slate" title="số lần nháp được cập nhật tại chỗ">sửa {t.draft_update_count}×</Badge>}
          <span className="ml-auto text-[10.5px] text-ink-3">lượt {t.created_turn}</span>
        </div>
        <h3 className={clsx("mt-1 text-[13.5px] font-semibold leading-5 text-ink", t.status === "cancelled" && "line-through")}>{t.title}</h3>
        <div className="mt-1.5 grid grid-cols-1 gap-1 text-xs text-ink-2 sm:grid-cols-2">
          <div className="flex min-w-0 items-center gap-1.5">
            <CircleUserRound size={13} className="shrink-0 text-ink-3" aria-hidden />
            {t.assignee_ids.length === 0 ? (
              <span className="text-ink-3">Chưa có người nhận</span>
            ) : (
              <span className="flex flex-wrap gap-1">
                {t.assignee_ids.map((u) => (
                  <span key={u} className="inline-flex items-center gap-1">
                    <Avatar name={nameOf(u)} id={u} size={16} />
                    {nameOf(u)}
                    {t.approvals[u] === "approved" && <Check size={12} className="text-emerald-600" aria-label="đã duyệt" />}
                    {t.approvals[u] === "declined" && <X size={12} className="text-rose-600" aria-label="đã từ chối" />}
                  </span>
                ))}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <CalendarClock size={13} className="shrink-0 text-ink-3" aria-hidden />
            <span>{displayDue(t)}</span>
            {t.due_text && t.due_type === "calendar" && <span className="truncate text-ink-3">“{t.due_text}”</span>}
          </div>
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          <Link2 size={12} className="text-ink-3" aria-hidden />
          {t.evidence.map((e, i) => (
            <Badge key={i} tone={ROLE_META[e.role].tone} className="font-mono" title={`bằng chứng: ${ROLE_META[e.role].label}`}>
              {e.message_id}·{ROLE_META[e.role].label}
            </Badge>
          ))}
        </div>
      </button>

      {pending.map((c) => (
        <div key={c.change_id} className="mt-2 rounded-md border border-violet-200 bg-violet-50/60 px-2 py-1.5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium text-violet-800">
            Đề xuất {c.change_id} đang chờ · người duyệt: {c.approver_ids.map(nameOf).join(", ")}
            {c.notified_ids.length > 0 && <span className="text-ink-3">· báo cho {c.notified_ids.map(nameOf).join(", ")}</span>}
          </div>
          <ChangeDiff c={c} />
        </div>
      ))}

      {(t.field_history.length > 0 || changes.length > pending.length) && (
        <div className="mt-1.5">
          <button onClick={() => setShowHist((v) => !v)} className="inline-flex cursor-pointer items-center gap-1 text-[11px] text-ink-3 hover:text-ink" aria-expanded={showHist}>
            <History size={12} aria-hidden /> Lịch sử ({t.field_history.length + changes.length - pending.length})
          </button>
          {showHist && (
            <ul className="mt-1 space-y-0.5 border-l-2 border-line pl-2 text-[11px] text-ink-3">
              {t.field_history.map((h, i) => (
                <li key={i}>
                  {fmtDayTime(h.at)} · <b>{h.field}</b> → {String(h.new).slice(0, 60)} <span className="font-mono">({h.evidence_message_id})</span> · {h.via === "draft_update" ? "sửa nháp" : "duyệt đề xuất"}
                </li>
              ))}
              {changes.filter((c) => c.status !== "pending").map((c) => (
                <li key={c.change_id}>
                  {c.change_id} {c.field} → {c.new_display} · <b>{c.status}</b>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {children}
    </article>
  );
}
