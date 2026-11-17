"use client";
import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { EvidenceRole, TaskStatus } from "@/lib/types";

export function Button({
  variant = "secondary",
  size = "md",
  className,
  ...p
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger" | "success"; size?: "sm" | "md" }) {
  return (
    <button
      {...p}
      className={clsx(
        "inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-md font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-45",
        size === "sm" ? "h-7 px-2 text-xs" : "h-8 px-3 text-[13px]",
        variant === "primary" && "bg-primary text-white hover:bg-primary-ink",
        variant === "secondary" && "border border-line bg-surface text-ink-2 hover:border-line-strong hover:bg-surface-2",
        variant === "ghost" && "text-ink-2 hover:bg-slate-100",
        variant === "danger" && "border border-rose-200 bg-white text-rose-700 hover:bg-rose-50",
        variant === "success" && "bg-emerald-600 text-white hover:bg-emerald-700",
        className,
      )}
    />
  );
}

export function Badge({ children, tone = "slate", className, title }: { children: ReactNode; tone?: Tone; className?: string; title?: string }) {
  return (
    <span title={title} className={clsx("inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-medium leading-4", TONES[tone], className)}>
      {children}
    </span>
  );
}

type Tone = "slate" | "teal" | "amber" | "emerald" | "rose" | "violet" | "sky" | "orange" | "zinc" | "indigo";
const TONES: Record<Tone, string> = {
  slate: "bg-slate-100 text-slate-700",
  teal: "bg-teal-50 text-teal-800 ring-1 ring-inset ring-teal-200",
  amber: "bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200",
  emerald: "bg-emerald-50 text-emerald-800 ring-1 ring-inset ring-emerald-200",
  rose: "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200",
  violet: "bg-violet-50 text-violet-700 ring-1 ring-inset ring-violet-200",
  sky: "bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-200",
  orange: "bg-orange-50 text-orange-800 ring-1 ring-inset ring-orange-200",
  zinc: "bg-zinc-100 text-zinc-500",
  indigo: "bg-indigo-50 text-indigo-700 ring-1 ring-inset ring-indigo-200",
};

export const STATUS_META: Record<TaskStatus, { label: string; tone: Tone; hint: string }> = {
  candidate: { label: "Ẩn · candidate", tone: "slate", hint: "Chưa xác định người nhận → chưa hiện cho ai" },
  draft: { label: "Nháp", tone: "amber", hint: "Đang hiện cho người nhận, chờ duyệt" },
  approved: { label: "Đã duyệt", tone: "emerald", hint: "Task thật; mọi thay đổi sau đều phải duyệt" },
  declined: { label: "Từ chối", tone: "rose", hint: "Người nhận từ chối — giữ lại để chặn tạo lại" },
  cancelled: { label: "Đã huỷ", tone: "zinc", hint: "Bị huỷ" },
  closed: { label: "Đóng", tone: "zinc", hint: "Rời tập đang mở — lý do ở closed_reason (hết TTL, quá hạn, từ chối lâu)" },
};

export function StatusBadge({ status }: { status: TaskStatus }) {
  const m = STATUS_META[status];
  return (
    <Badge tone={m.tone} title={m.hint}>
      {m.label}
    </Badge>
  );
}

export const ROLE_META: Record<EvidenceRole, { label: string; tone: Tone }> = {
  assign: { label: "giao", tone: "teal" },
  accept: { label: "nhận", tone: "emerald" },
  assignee: { label: "người", tone: "violet" },
  due: { label: "hạn", tone: "amber" },
  content: { label: "nội dung", tone: "sky" },
  cancel: { label: "huỷ", tone: "rose" },
  context: { label: "ngữ cảnh", tone: "slate" },
};

export const KIND_LABEL: Record<string, string> = { assigned: "được giao", self_commit: "tự nhận", open_ask: "chưa ai nhận" };

const AVATAR_COLORS = ["bg-teal-600", "bg-orange-600", "bg-violet-600", "bg-sky-600", "bg-rose-600", "bg-emerald-600", "bg-amber-600", "bg-indigo-600"];
export function Avatar({ name, id, size = 28 }: { name: string; id: string; size?: number }) {
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return (
    <span
      aria-hidden
      className={clsx("inline-flex shrink-0 select-none items-center justify-center rounded-full font-semibold text-white", AVATAR_COLORS[h % AVATAR_COLORS.length])}
      style={{ width: size, height: size, fontSize: size * 0.42 }}
    >
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
}

export function PanelHeader({ title, icon, right, sub }: { title: string; icon?: ReactNode; right?: ReactNode; sub?: ReactNode }) {
  return (
    <div className="flex min-h-11 items-center justify-between gap-2 border-b border-line bg-surface px-3 py-2">
      <div className="flex min-w-0 items-center gap-2">
        {icon && <span className="text-primary">{icon}</span>}
        <h2 className="truncate text-[13px] font-semibold text-ink">{title}</h2>
        {sub && <span className="truncate text-xs text-ink-3">{sub}</span>}
      </div>
      {right}
    </div>
  );
}

export function Segmented<T extends string>({ value, onChange, options, label }: { value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode; badge?: number }[]; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="inline-flex rounded-md bg-slate-100 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          role="tab"
          aria-selected={value === o.value}
          onClick={() => onChange(o.value)}
          className={clsx(
            "inline-flex h-7 cursor-pointer items-center gap-1.5 rounded px-2.5 text-xs font-medium transition-colors",
            value === o.value ? "bg-white text-ink shadow-sm" : "text-ink-3 hover:text-ink",
          )}
        >
          {o.label}
          {!!o.badge && <span className="rounded-full bg-accent px-1.5 text-[10px] leading-4 text-white">{o.badge}</span>}
        </button>
      ))}
    </div>
  );
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center">
      {icon && <div className="text-ink-3">{icon}</div>}
      <p className="text-sm font-medium text-ink-2">{title}</p>
      {children && <div className="max-w-sm text-xs leading-5 text-ink-3">{children}</div>}
    </div>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <code className={clsx("font-mono text-[11.5px]", className)}>{children}</code>;
}
