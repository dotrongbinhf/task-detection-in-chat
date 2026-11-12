// Kiểu dữ liệu nhận từ backend Python (detect/models.py). Chỉ dùng để hiển thị.

export type Mention = { user_id: string; name: string };
export type Reaction = { user_id: string; type: string; created_at: string };
export type Message = {
  id: string;
  conversation_id: string;
  conversation_type: "group" | "dm";
  thread_root_id: string | null;
  created_at: string;
  author_id: string;
  author_name: string;
  text: string;
  mentions: Mention[];
  reply_to_id: string | null;
  reactions: Reaction[];
  attachments: string[];
  source: "dataset" | "user";
};
export type Member = { user_id: string; display_name: string; aliases: string[] };
export type StreamEvent = {
  kind: "message" | "reaction";
  id: string;
  key: string;
  at: string;
  message: Message | null;
  target_id: string | null;
  user_id: string | null;
  user_name: string | null;
  type: string | null;
};

export type TaskStatus = "candidate" | "draft" | "approved" | "declined" | "cancelled" | "expired" | "stale" | "closed";
export type EvidenceRole = "assign" | "accept" | "assignee" | "due" | "content" | "cancel" | "context";
export type Evidence = { message_id: string; role: EvidenceRole; added_at: string };
export type Task = {
  task_id: string;
  conversation_key: string;
  status: TaskStatus;
  kind: "assigned" | "self_commit" | "open_ask";
  title: string;
  notes: string | null;
  assignee_ids: string[];
  assignee_refs_raw: string[];
  assigner_id: string | null;
  due_text: string | null;
  due_kind: string | null;
  due_type: "calendar" | "event" | "none";
  due_date: string | null;
  anchor_message_id: string;
  evidence: Evidence[];
  field_history: { field: string; old: unknown; new: unknown; evidence_message_id: string | null; at: string; via: string }[];
  approvals: Record<string, "pending" | "approved" | "declined">;
  created_at: string;
  created_turn: number;
  last_touched_at: string;
  shown_at: string | null;
  status_changed_at: string;
  draft_update_count: number;
  model: string;
};
export type PendingChange = {
  change_id: string;
  task_id: string;
  field: "title" | "notes" | "kind" | "assignee_refs" | "due" | "status";
  new_display: string;
  old_display: string;
  evidence_message_id: string;
  created_at: string;
  status: "pending" | "approved" | "declined" | "superseded";
  approver_ids: string[];
  notified_ids: string[];
  resolved_at: string | null;
};
export type TraceEntry = { type: string; at: string; turn?: number; [k: string]: unknown };

export type ScoredTask = { local: string; task_id: string; title: string; status: TaskStatus; score: number; reasons: string[]; source: "open" | "db" };
export type Tokens = { b1: number; b2: number; b3: number; b4: number; b5: number; total: number };
export type OpResult = {
  op: string;
  target: string | null;
  task_id: string | null;
  accepted: boolean;
  reason: string | null;
  notes: string[];
  raw: Record<string, unknown>;
  effect: string | null;
};
export type TurnRecord = {
  turn: number;
  key: string;
  at: string;
  new_event_ids: string[];
  coalesced: boolean;
  skipped: string | null;
  pre: { expirations: TraceEntry[]; user_actions: TraceEntry[] };
  context: {
    selected: ScoredTask[];
    not_selected: ScoredTask[];
    window_ids: string[];
    reply_chain_ids: string[];
    evidence_ids: string[];
    id_map: Record<string, string>;
    task_map: Record<string, string>;
    trims: string[];
    tokens: Tokens;
    window_k_used: number;
    prompt: string;
    blocks: Record<"b1" | "b2" | "b3" | "b4" | "b5", string>;
  } | null;
  gate: { mode: string; p: number | null; threshold: number; skipped: boolean; warning: string | null; model: string; latency_ms: number; prompt: string; cached: boolean; raw: string } | null;
  extract: {
    model: string;
    raw: string;
    attempts: number;
    latency_ms: number;
    input_tokens: number;
    output_tokens: number;
    cached_tokens: number;
    disk_cache: boolean;
    error: string | null;
  } | null;
  ops: OpResult[];
  events: TraceEntry[];
};

export type UserAction = {
  at?: string;
  user_id: string;
  message_id?: string;
  action: "approve" | "decline" | "approve_change" | "decline_change";
  task_id?: string;
  change_id?: string;
};

export type GoldTask = { gold_id: string; title: string; assignee_ids: string[]; scenario_tags: string[] };

export type EngineState = {
  dataset: string;
  members: Record<string, Member[]>;
  events: StreamEvent[];
  processed: string[];
  simNow: string;
  tasks: Task[];
  changes: PendingChange[];
  turns: TurnRecord[];
  trace: TraceEntry[];
  userActions: UserAction[];
  counters: { task: number; change: number; turn: number; msg: number };
  gold: GoldTask[] | null;
  hardNegatives: string[];
};

export type Config = Record<string, string | number>;

export type Rate = { k: number; n: number; value: number | null; lo: number; hi: number; enough: boolean };
export type EvalReport = {
  hasGold: boolean;
  summary: {
    recall: Rate; precision: Rate; dup_rate: Rate; hard_negative_fp: Rate; declined_recreated: number;
    assignee_acc: Rate; due_acc: Rate; event_fabricated: Rate; cancel_correct: Rate;
    ev_precision: number | null; ev_recall: number | null;
    latency_msgs: { p50: number | null; p90: number | null }; latency_sec: { p50: number | null; p90: number | null };
    draft_updates: { p50: number | null; p90: number | null };
    changes_proposed: number; changes_superseded: number; approved_tasks: number; wrong_changes: Rate;
    repeated_changes: Record<string, number>; empty_assignee_kept_candidate: Rate; empty_assignee_shown: number;
  };
  cost: {
    turns: number; extract_calls: number; gate_calls: number; op_turn_ratio: Rate;
    tokens: Record<"b1" | "b2" | "b3" | "b4" | "b5" | "total", { p50: number | null; p95: number | null }>;
    input_tokens: number; cached_tokens: number; output_tokens: number; cost_usd: number; cost_per_1000_msgs: number;
    budget_trims: Record<string, number>; messages: number;
  };
  gate_curve: { threshold: number; recall_vs_ceiling: Rate; pass_rate: Rate }[];
  by_tag: { tag: string; recall: Rate }[];
  items: { kind: string; gold_id?: string; task_id?: string; note: string; path?: string[] }[];
  matches: { gold_id: string; task_id: string }[];
};
