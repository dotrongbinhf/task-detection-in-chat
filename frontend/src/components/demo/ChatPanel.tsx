"use client";
import clsx from "clsx";
import { AtSign, CornerUpLeft, FileText, Hourglass, MessagesSquare, Send, ThumbsUp, X } from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { dayKey, fmtDay, fmtTime } from "@/lib/format";
import type { EngineState, Member, Message, Task, TurnRecord } from "@/lib/types";
import type { Engine } from "./useEngine";
import { Avatar, Badge, Button, PanelHeader, ROLE_META } from "./ui";

const REACT_EMOJI: Record<string, string> = { like: "👍", love: "❤️", heart: "❤️", ok: "👌", check: "✅", haha: "😆" };

export function convLabel(id: string, members: Member[]) {
  if (id.startsWith("dm_")) return `DM · ${members.map((m) => m.display_name).join(" – ")}`;
  return `# ${id.replace(/^ch_/, "")}`;
}

const OFFSETS = [
  { v: 5, l: "+5 giây" },
  { v: 60, l: "+1 phút" },
  { v: 600, l: "+10 phút" },
  { v: 3600, l: "+1 giờ" },
  { v: 86400, l: "+1 ngày" },
];

const EXAMPLES = [
  "@Nam viết test cho API voucher trước thứ 6 nhé",
  "Ai rảnh review giúp mình PR #501 không?",
  "để em làm cho",
  "lùi sang thứ 2 tuần sau nhé",
  "thôi khỏi làm cái đó nhé",
  "Mai em gửi anh bản tổng hợp nhé",
  "Để anh xem đã",
];

type Props = {
  engine: Engine;
  conv: string;
  setConv: (c: string) => void;
  turn: TurnRecord | null;
  task: Task | null;
  onSelectTurnForEvent: (id: string) => void;
  onActivity?: () => void;
};

export function ChatPanel({ engine, conv, setConv, turn, task, onSelectTurnForEvent, onActivity }: Props) {
  const s = engine.state!;
  const members = s.members[conv] ?? [];
  const processed = useMemo(() => new Set(s.processed), [s.processed]);
  const msgs = useMemo(
    () => s.events.filter((e) => e.kind === "message" && e.message!.conversation_id === conv && processed.has(e.id)).map((e) => e.message!),
    [s.events, conv, processed],
  );
  const shownReactions = useMemo(() => {
    const m = new Map<string, { user: string; type: string; id: string }[]>();
    for (const e of s.events)
      if (e.kind === "reaction" && processed.has(e.id)) m.set(e.target_id!, [...(m.get(e.target_id!) ?? []), { user: e.user_name ?? "", type: e.type ?? "", id: e.id }]);
    return m;
  }, [s.events, processed]);
  const pendingInConv = s.events.filter((e) => !processed.has(e.id) && e.key.split(";")[0] === conv);
  const nextAny = s.events.find((e) => !processed.has(e.id));

  // đánh dấu từ lượt đang chọn
  const newIds = new Set(turn?.new_event_ids ?? []);
  const localOf = useMemo(() => {
    const m = new Map<string, string>();
    for (const [l, id] of Object.entries(turn?.context?.id_map ?? {})) m.set(id, l);
    return m;
  }, [turn]);
  const evRoles = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const e of task?.evidence ?? []) m.set(e.message_id, [...(m.get(e.message_id) ?? []), e.role]);
    return m;
  }, [task]);

  const listRef = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);
  useEffect(() => {
    const el = listRef.current;
    if (el && follow) el.scrollTop = el.scrollHeight;
  }, [msgs.length, follow, conv]);
  // chọn lượt → cuộn tới TIN MỚI của lượt đó
  useEffect(() => {
    const id = turn?.new_event_ids[0]?.split("#")[0];
    const el = id ? document.getElementById(`msg-${id}`) : null;
    const box = listRef.current;
    if (!el || !box) return;
    const r = el.getBoundingClientRect();
    const b = box.getBoundingClientRect();
    if (r.top < b.top || r.bottom > b.bottom) el.scrollIntoView({ block: "center" });
  }, [turn?.turn, turn?.new_event_ids]);
  useEffect(() => {
    if (!task) return;
    const first = task.evidence[0]?.message_id;
    const el = first ? document.getElementById(`msg-${first.split("#")[0]}`) : null;
    if (el) {
      setFollow(false);
      el.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [task]);

  // ---- composer ----
  const [author, setAuthor] = useState(members[0]?.user_id ?? "");
  const [text, setText] = useState("");
  const [offset, setOffset] = useState(60);
  const [reply, setReply] = useState<Message | null>(null);
  const [mentionOpen, setMentionOpen] = useState(false);
  useEffect(() => {
    if (!members.some((m) => m.user_id === author)) setAuthor(members[0]?.user_id ?? "");
    setReply(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conv]);

  const mentionsIn = (t: string) => members.filter((m) => t.includes(`@${m.display_name}`)).map((m) => m.user_id);
  const send = async () => {
    const t = text.trim();
    if (!t || !author || engine.busy) return;
    setFollow(true);
    onActivity?.();
    const r = await engine.post({ conversation_id: conv, author_id: author, text: t, mentions: mentionsIn(t), reply_to_id: reply?.id ?? null, offset_seconds: offset });
    if (r) {
      setText("");
      setReply(null);
    }
  };
  const react = (m: Message) => {
    setFollow(true);
    onActivity?.();
    engine.react({ target_id: m.id, user_id: author, type: "like", offset_seconds: 30 });
  };

  const convs = Object.keys(s.members);
  let lastDay = "";
  const authorName = members.find((m) => m.user_id === author)?.display_name ?? "";

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-line bg-surface" aria-label="Chat">
      <PanelHeader
        title="Chat"
        icon={<MessagesSquare size={16} aria-hidden />}
        sub={`${msgs.length} tin đã đến`}
        right={
          <select
            aria-label="Chọn hội thoại"
            value={conv}
            onChange={(e) => setConv(e.target.value)}
            className="h-7 cursor-pointer rounded-md border border-line bg-white px-2 text-xs text-ink-2"
          >
            {convs.map((c) => (
              <option key={c} value={c}>
                {convLabel(c, s.members[c])}
              </option>
            ))}
          </select>
        }
      />

      <div
        ref={listRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
        }}
        className="scroll-thin flex-1 overflow-y-auto px-3 py-2"
      >
        {msgs.length === 0 && (
          <div className="py-12 text-center text-xs text-ink-3">
            Chưa có tin nào trong hội thoại này. {s.dataset === "empty" ? "Hãy tự nhập tin bên dưới." : "Bấm “Lượt kế” hoặc “Chạy” để tin đến."}
          </div>
        )}
        {msgs.map((m) => {
          const dk = dayKey(m.created_at);
          const showDay = dk !== lastDay;
          lastDay = dk;
          const isNew = newIds.has(m.id);
          const local = localOf.get(m.id);
          const roles = evRoles.get(m.id);
          const reacts = shownReactions.get(m.id) ?? [];
          const replyMsg = m.reply_to_id ? msgs.find((x) => x.id === m.reply_to_id) : null;
          return (
            <Fragment key={m.id}>
              {showDay && (
                <div className="my-2 flex justify-center">
                  <span className="rounded-full border border-line bg-white px-2.5 py-0.5 text-[11px] font-medium text-ink-3">{fmtDay(m.created_at)}</span>
                </div>
              )}
              <div
                id={`msg-${m.id}`}
                onClick={() => onSelectTurnForEvent(m.id)}
                className={clsx(
                  "group relative -mx-1 flex cursor-pointer gap-2.5 rounded-md px-1.5 py-1.5 transition-colors",
                  isNew && "animate-flash bg-teal-50 ring-1 ring-teal-300",
                  !isNew && roles && "bg-orange-50 ring-1 ring-orange-200",
                  !isNew && !roles && local && "bg-slate-50",
                  !isNew && !roles && !local && "hover:bg-slate-50",
                )}
              >
                {local && <span className="absolute -left-1 top-2 h-[calc(100%-16px)] w-0.5 rounded bg-teal-400" aria-hidden />}
                <Avatar name={m.author_name} id={m.author_id} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                    <span className="text-[13px] font-semibold text-ink">{m.author_name}</span>
                    <span className="text-[11px] tabular-nums text-ink-3">{fmtTime(m.created_at)}</span>
                    <span className="font-mono text-[10.5px] text-ink-3">{m.id}</span>
                    {m.source === "user" && <Badge tone="indigo">bạn nhập</Badge>}
                    {local && (
                      <Badge tone={isNew ? "teal" : "slate"} title="id cục bộ trong prompt của lượt đang chọn">
                        {local}
                        {isNew ? " · TIN MỚI" : ""}
                      </Badge>
                    )}
                    {roles?.map((r) => (
                      <Badge key={r} tone={ROLE_META[r as keyof typeof ROLE_META]?.tone ?? "orange"} title="vai trò bằng chứng của task đang chọn">
                        {ROLE_META[r as keyof typeof ROLE_META]?.label ?? r}
                      </Badge>
                    ))}
                  </div>
                  {replyMsg && (
                    <div className="mt-0.5 flex items-center gap-1 truncate text-[11.5px] text-ink-3">
                      <CornerUpLeft size={12} aria-hidden /> <span className="font-medium">{replyMsg.author_name}:</span>
                      <span className="truncate">{replyMsg.text}</span>
                    </div>
                  )}
                  <p className="whitespace-pre-wrap break-words text-[13.5px] leading-5 text-ink-2">{renderText(m)}</p>
                  {m.attachments?.map((a) => (
                    <span key={a} className="mt-1 mr-1 inline-flex items-center gap-1 rounded border border-line bg-white px-1.5 py-0.5 text-[11px] text-ink-2">
                      <FileText size={12} aria-hidden /> {a}
                    </span>
                  ))}
                  {reacts.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {reacts.map((r) => (
                        <span
                          key={r.id}
                          className={clsx(
                            "inline-flex items-center gap-1 rounded-full border px-1.5 text-[11px]",
                            newIds.has(r.id) ? "border-teal-300 bg-teal-50" : evRoles.has(r.id) ? "border-orange-300 bg-orange-50" : "border-line bg-white",
                          )}
                          title={r.id}
                        >
                          {REACT_EMOJI[r.type] ?? r.type} {r.user}
                          {localOf.get(r.id) && <span className="font-mono text-[10px] text-teal-700">{localOf.get(r.id)}</span>}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="absolute right-1 top-1 hidden gap-0.5 rounded-md border border-line bg-white p-0.5 shadow-sm group-hover:flex group-focus-within:flex">
                  <button
                    aria-label="Trả lời tin này"
                    title="Trả lời"
                    onClick={(e) => {
                      e.stopPropagation();
                      setReply(m);
                    }}
                    className="cursor-pointer rounded p-1 text-ink-3 hover:bg-slate-100 hover:text-ink"
                  >
                    <CornerUpLeft size={14} />
                  </button>
                  <button
                    aria-label={`Thả 👍 với tư cách ${authorName}`}
                    title={`Thả 👍 (với tư cách ${authorName})`}
                    disabled={engine.busy}
                    onClick={(e) => {
                      e.stopPropagation();
                      react(m);
                    }}
                    className="cursor-pointer rounded p-1 text-ink-3 hover:bg-slate-100 hover:text-ink"
                  >
                    <ThumbsUp size={14} />
                  </button>
                </div>
              </div>
            </Fragment>
          );
        })}
      </div>

      {(pendingInConv.length > 0 || nextAny) && (
        <div className="flex items-center gap-2 border-t border-dashed border-line bg-surface-2 px-3 py-1.5 text-[11.5px] text-ink-3">
          <Hourglass size={13} aria-hidden />
          {pendingInConv.length} sự kiện chưa đến ở hội thoại này
          {nextAny && (
            <span className="truncate">
              · kế tiếp: <span className="font-mono">{nextAny.id}</span> lúc {fmtTime(nextAny.at)}
            </span>
          )}
        </div>
      )}

      {/* composer */}
      <div className="border-t border-line bg-white p-2.5">
        <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
          <label className="text-[11px] font-medium text-ink-3" htmlFor="composer-author">
            Gửi với tư cách
          </label>
          <select
            id="composer-author"
            value={author}
            onChange={(e) => setAuthor(e.target.value)}
            className="h-7 cursor-pointer rounded-md border border-line bg-white px-1.5 text-xs"
          >
            {members.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {m.display_name}
              </option>
            ))}
          </select>
          <label className="ml-1 text-[11px] font-medium text-ink-3" htmlFor="composer-offset">
            Thời điểm
          </label>
          <select
            id="composer-offset"
            value={offset}
            onChange={(e) => setOffset(Number(e.target.value))}
            className="h-7 cursor-pointer rounded-md border border-line bg-white px-1.5 text-xs"
            title="Thời gian mô phỏng của tin = thời điểm hiện tại + khoảng này"
          >
            {OFFSETS.map((o) => (
              <option key={o.v} value={o.v}>
                {o.l}
              </option>
            ))}
          </select>
          <div className="relative ml-auto">
            <Button size="sm" variant="ghost" aria-label="Chèn @mention" onClick={() => setMentionOpen((v) => !v)}>
              <AtSign size={14} /> Nhắc tên
            </Button>
            {mentionOpen && (
              <div className="absolute bottom-8 right-0 z-30 w-40 rounded-md border border-line bg-white p-1 shadow-lg">
                {members.map((m) => (
                  <button
                    key={m.user_id}
                    onClick={() => {
                      setText((t) => `${t}${t && !t.endsWith(" ") ? " " : ""}@${m.display_name} `);
                      setMentionOpen(false);
                    }}
                    className="flex w-full cursor-pointer items-center gap-2 rounded px-2 py-1 text-left text-xs hover:bg-slate-100"
                  >
                    <Avatar name={m.display_name} id={m.user_id} size={18} /> {m.display_name}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        {reply && (
          <div className="mb-1.5 flex items-center gap-1.5 rounded-md bg-slate-50 px-2 py-1 text-[11.5px] text-ink-2">
            <CornerUpLeft size={12} aria-hidden /> Trả lời <b>{reply.author_name}</b>: <span className="truncate">{reply.text}</span>
            <button aria-label="Bỏ trả lời" onClick={() => setReply(null)} className="ml-auto cursor-pointer rounded p-0.5 hover:bg-slate-200">
              <X size={12} />
            </button>
          </div>
        )}
        <div className="flex items-end gap-2">
          <label htmlFor="composer-text" className="sr-only">
            Nội dung tin nhắn
          </label>
          <textarea
            id="composer-text"
            rows={2}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder={`Nhập tin của ${authorName}… (Enter để gửi, Shift+Enter xuống dòng)`}
            className="scroll-thin min-h-[44px] flex-1 resize-none rounded-md border border-line px-2.5 py-2 text-[13.5px] leading-5 outline-none placeholder:text-ink-3 focus:border-primary focus:ring-2 focus:ring-primary/20"
          />
          <Button variant="primary" className="h-[44px] px-3.5" onClick={send} disabled={!text.trim() || engine.busy} aria-label="Gửi và xử lý">
            <Send size={15} /> Gửi
          </Button>
        </div>
        <div className="mt-1.5 flex flex-wrap gap-1">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => setText(ex)}
              className="cursor-pointer rounded-full border border-line bg-surface-2 px-2 py-0.5 text-[11px] text-ink-3 transition-colors hover:border-primary hover:text-primary-ink"
            >
              {ex}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

function renderText(m: Message) {
  if (!m.mentions.length) return m.text;
  const names = m.mentions.map((x) => `@${x.name}`);
  const parts = m.text.split(new RegExp(`(${names.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "g"));
  return parts.map((p, i) =>
    names.includes(p) ? (
      <span key={i} className="rounded bg-teal-50 px-0.5 font-medium text-primary-ink">
        {p}
      </span>
    ) : (
      <Fragment key={i}>{p}</Fragment>
    ),
  );
}

export type { EngineState };
