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
  /** created_at của tin bằng chứng mới nhất (không đổi khi duyệt) — dùng chọn "3 task gần nhất" */
  last_mentioned_at?: string;
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

export type TaskSource = "open" | "db" | "linked" | "recent" | "search" | "dedup";
export type ScoredTask = { local: string; task_id: string; title: string; status: TaskStatus; score: number; reasons: string[]; source: TaskSource };
/** Task trong khối 3a (được link) / 3b (gần nhất) của chế độ linked_recent_search */
export type ContextTask = ScoredTask & { via: string; last_mentioned_at: string; evidence_shown: string[] };
export type Tokens = { b1: number; b2: number; b3: number; b4: number; b5: number; total: number; b3a?: number; b3b?: number; b6?: number };
export type ContextMode = "scored" | "linked_recent_search";

/** Một lần gọi bộ trích trong lượt (lần 1 = ban đầu, lần 2 = sau khi tìm task / kiểm trùng) */
export type ExtractCall = {
  call: number;
  reason: "initial" | "search" | "target_check" | "dedup";
  model: string;
  raw: string;
  attempts: number;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  disk_cache: boolean;
  prompt_tokens_est: number;
  extra_block: string;
  extra_tokens: number;
  cost_usd: number;
  error: string | null;
  failure?: string;
};
export type SearchHitRow = { local: string; task_id: string; title: string; status: TaskStatus; score: number; matched: string[]; similarity?: number | null; already_in_prompt: boolean };
export type SearchRecord = {
  requested: { query: string; people: string[] | null; reason: string };
  ignored: string | null;
  query: string;
  people: string[];
  results: SearchHitRow[];
  call: number | null;
  second_call_search_ignored?: string;
};
export type TargetCheckRecord = {
  checks: { op: string; target: string | null; task_id: string | null; target_title: string | null; target_overlap: number; target_similarity?: number | null; threshold?: number | null; query: string; people: string[];
    results: { task_id: string; title: string; status: TaskStatus; score: number; matched: string[]; overlap: number }[]; candidates: string[] }[];
  triggered: boolean;
  call: number | null;
  ops_after?: string[];
};
export type DedupRow = { task_id: string; title: string; status: TaskStatus; score: number; matched: string[]; overlap: number; similarity?: number | null; same_people: boolean; candidate: boolean; why_not: string | null };
export type DedupRecord = {
  checks: { title: string | null; anchor: string; query: string; people: string[]; named?: string[]; assigner: string | null; results: DedupRow[]; candidates: string[] }[];
  triggered: boolean;
  call: number | null;
  skipped: string | null;
  outcome: "still_create" | "create_dropped" | null;
  creates_before?: number;
  creates_after?: number;
  ops_after?: string[];
};
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
    mode?: ContextMode;
    linked?: ContextTask[];
    recent?: ContextTask[];
    linked_chain_ids?: string[];
    task_map_final?: Record<string, string>;
    id_map_final?: Record<string, string>;
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
    blocks: Record<string, string>;
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
    calls?: number;
  } | null;
  extract_calls?: ExtractCall[];
  search?: SearchRecord | null;
  target_check?: TargetCheckRecord | null;
  dedup?: DedupRecord | null;
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
    tokens: Record<string, { p50: number | null; p95: number | null }>;
    input_tokens: number; cached_tokens: number; output_tokens: number; cost_usd: number; cost_per_1000_msgs: number;
    budget_trims: Record<string, number>; messages: number;
    by_call?: Record<string, { calls: number; input_tokens: number; cached_tokens: number; output_tokens: number; cost_usd: number }>;
    turn_input_tokens?: { p50: number | null; p95: number | null };
    cache_ratio?: number | null;
  };
  search?: {
    mode_linked: boolean; search_turns: Rate; search_ignored: number; second_call_turns: Rate; second_call_by: { search: number; dedup: number; target_check?: number };
    need_search: number; search_found: number; search_wrong: number; search_missed: number; search_not_needed: number; need_search_items: string[];
    dedup_triggered: number; dedup_dropped: number; dedup_still_create: number; dedup_still_bad: number; dedup_still_ok: number; dedup_skipped: number;
  };
  gate_curve: { threshold: number; recall_vs_ceiling: Rate; pass_rate: Rate }[];
  by_tag: { tag: string; recall: Rate }[];
  items: { kind: string; gold_id?: string; task_id?: string; note: string; path?: string[] }[];
  matches: { gold_id: string; task_id: string }[];
};
