"use client";
import clsx from "clsx";
import { BarChart3, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { pct } from "@/lib/format";
import type { EvalReport, Rate } from "@/lib/types";
import type { Engine } from "./useEngine";
import { Badge, Button, Empty } from "./ui";

function RateCell({ r }: { r: Rate }) {
  if (r.value == null) return <span className="text-ink-3">— (n=0)</span>;
  return (
    <span className="tabular-nums">
      <b className="text-ink">{pct(r.value)}</b>{" "}
      <span className="text-ink-3">
        {r.k}/{r.n} · CI95 {pct(r.lo)}–{pct(r.hi)}
      </span>
    </span>
  );
}

function Metric({ label, r, target, higher = true }: { label: string; r: Rate; target?: number; higher?: boolean }) {
  const pass = r.value == null || target == null ? null : higher ? r.value >= target : r.value <= target;
  return (
    <div className="rounded-lg border border-line bg-white p-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11.5px] font-medium text-ink-3">{label}</span>
        {pass != null && <Badge tone={pass ? "emerald" : "rose"}>{pass ? "đạt" : "chưa đạt"} {higher ? "≥" : "≤"} {pct(target)}</Badge>}
      </div>
      <div className="mt-1 text-xl font-semibold tabular-nums text-ink">{pct(r.value)}</div>
      <div className="text-[11px] text-ink-3">
        {r.k}/{r.n} · CI95 {pct(r.lo)}–{pct(r.hi)}
        {!r.enough && r.n > 0 && " · chưa đủ mẫu"}
      </div>
    </div>
  );
}

const KIND_TONE: Record<string, "rose" | "orange" | "violet" | "amber" | "slate"> = { fn: "rose", fp: "orange", dup: "violet", field: "amber", evidence: "amber", shown_empty: "rose" };
const KIND_LABEL: Record<string, string> = { fn: "Bỏ sót (FN)", fp: "Báo thừa (FP)", dup: "Trùng", field: "Sai trường", evidence: "Bằng chứng", shown_empty: "Hiện nhầm" };

export function EvalPanel({ engine }: { engine: Engine }) {
  const rep: EvalReport | null = engine.report;
  const s = engine.state!;
  const [showTp, setShowTp] = useState(false);
  useEffect(() => {
    if (!rep && s.turns.length && !engine.busy) engine.evaluate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.turns.length]);

  if (!s.turns.length) return <Empty icon={<BarChart3 size={28} />} title="Chưa có lượt nào để chấm">Chạy dataset rồi quay lại tab này.</Empty>;
  if (!rep) return <Empty title="Đang chấm điểm…" />;
  const m = rep.summary;
  const c = rep.cost;
  const issues = rep.items.filter((i) => i.kind !== "tp");
  const finished = engine.pending === 0;

  return (
    <div className="space-y-4 p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        {!rep.hasGold && <Badge tone="orange">Dataset không có gold.jsonl — chỉ có chỉ số chi phí</Badge>}
        {!finished && <Badge tone="amber">Đang chấm giữa chừng ({engine.pending} sự kiện chưa xử lý)</Badge>}
        <Badge tone="slate">Dữ liệu ít → chỉ đọc như tín hiệu định hướng</Badge>
        <Button size="sm" className="ml-auto" onClick={() => engine.evaluate()} disabled={engine.busy}>
          <RefreshCw size={13} /> Chấm lại
        </Button>
      </div>

      {rep.hasGold && (
        <>
          <section>
            <h3 className="mb-1.5 text-[12px] font-semibold uppercase tracking-wide text-ink-3">Phát hiện & trường</h3>
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
              <Metric label="Recall" r={m.recall} target={0.7} />
              <Metric label="Precision" r={m.precision} target={0.6} />
              <Metric label="Tỉ lệ trùng" r={m.dup_rate} target={0.05} higher={false} />
              <Metric label="Người nhận đúng" r={m.assignee_acc} target={0.85} />
              <Metric label="Hạn lịch đúng" r={m.due_acc} target={0.8} />
              <Metric label="Hạn sự kiện bị bịa" r={m.event_fabricated} target={0.05} higher={false} />
              <Metric label="Đề xuất thay đổi sai" r={m.wrong_changes} target={0.2} higher={false} />
              <Metric label="Báo nhầm hard negative" r={m.hard_negative_fp} />
            </div>
          </section>

          <section className="grid gap-3 lg:grid-cols-2">
            <div className="rounded-lg border border-line bg-white p-2.5">
              <h4 className="mb-1 font-semibold text-ink">Trích dẫn · kịp thời · gánh nặng duyệt</h4>
              <ul className="space-y-0.5 text-ink-2">
                <li>Precision bằng chứng: <b>{pct(m.ev_precision)}</b> (≥ 80%) · Recall bằng chứng: <b>{pct(m.ev_recall)}</b> (≥ 70%)</li>
                <li>Độ trễ phát hiện: p50 <b>{m.latency_msgs.p50 ?? "—"} tin / {m.latency_sec.p50 ?? "—"} s</b> · p90 {m.latency_msgs.p90 ?? "—"} tin / {m.latency_sec.p90 ?? "—"} s</li>
                <li>Số lần nháp bị sửa: p50 {m.draft_updates.p50 ?? "—"} · p90 {m.draft_updates.p90 ?? "—"}</li>
                <li>Đề xuất thay đổi: <b>{m.changes_proposed}</b> (superseded {m.changes_superseded}) / {m.approved_tasks} task đã duyệt</li>
                <li>Tạo lại task đã từ chối: <b className={m.declined_recreated ? "text-rose-600" : ""}>{m.declined_recreated}</b> (phải = 0) · Huỷ đúng: <RateCell r={m.cancel_correct} /></li>
                <li>Nhãn không người nhận giữ ở candidate: <RateCell r={m.empty_assignee_kept_candidate} /> · hiện nhầm {m.empty_assignee_shown}</li>
              </ul>
            </div>
            <div className="rounded-lg border border-line bg-white p-2.5">
              <h4 className="mb-1 font-semibold text-ink">Theo scenario_tags (recall)</h4>
              <div className="scroll-thin max-h-40 overflow-y-auto">
                <table className="w-full">
                  <tbody>
                    {rep.by_tag.map((t) => (
                      <tr key={t.tag} className="border-t border-line first:border-0">
                        <td className="py-0.5 font-mono text-[11px]">{t.tag}</td>
                        <td className="py-0.5 text-right"><RateCell r={t.recall} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        </>
      )}

      <section className="grid gap-3 lg:grid-cols-2">
        <div className="rounded-lg border border-line bg-white p-2.5">
          <h4 className="mb-1 font-semibold text-ink">Chi phí</h4>
          <ul className="space-y-0.5 text-ink-2">
            <li>{c.turns} lượt · {c.extract_calls} lần gọi bộ trích · {c.gate_calls} lần gọi cổng</li>
            <li>Lượt có op: <RateCell r={c.op_turn_ratio} /></li>
            <li>Token input {c.input_tokens.toLocaleString("vi")} (cache {c.cached_tokens.toLocaleString("vi")}) · output {c.output_tokens.toLocaleString("vi")}</li>
            <li>Ước tính ${c.cost_usd.toFixed(4)} · ${c.cost_per_1000_msgs.toFixed(3)} / 1.000 tin</li>
            <li>budget_trim: {Object.entries(c.budget_trims).map(([k, v]) => `${k} ×${v}`).join(", ") || "0"}</li>
          </ul>
          <table className="mt-2 w-full">
            <thead><tr className="text-left text-ink-3"><th className="font-medium">Khối</th><th className="text-right font-medium">p50</th><th className="text-right font-medium">p95</th></tr></thead>
            <tbody>
              {(["b1", "b2", "b3", "b4", "b5", "total"] as const).map((b) => (
                <tr key={b} className="border-t border-line">
                  <td>{{ b1: "1. Hướng dẫn", b2: "2. Lịch", b3: "3. Task liên quan", b4: "4. Lịch sử gần", b5: "5. Tin mới", total: "Tổng" }[b]}</td>
                  <td className="text-right tabular-nums">{c.tokens[b].p50 ?? "—"}</td>
                  <td className="text-right tabular-nums">{c.tokens[b].p95 ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="rounded-lg border border-line bg-white p-2.5">
          <h4 className="mb-1 font-semibold text-ink">Đường cong cổng</h4>
          {rep.gate_curve.every((x) => x.pass_rate.n === 0) ? (
            <p className="text-ink-3">Chạy với <b>gate_mode = shadow</b> để có đường cong (cổng chấm p mọi lượt nhưng không chặn).</p>
          ) : (
            <table className="w-full">
              <thead><tr className="text-left text-ink-3"><th className="font-medium">Ngưỡng</th><th className="font-medium">Recall so với trần</th><th className="font-medium">Lượt qua cổng</th></tr></thead>
              <tbody>
                {rep.gate_curve.map((g) => (
                  <tr key={g.threshold} className="border-t border-line">
                    <td className="font-mono">{g.threshold}</td>
                    <td className={clsx(g.recall_vs_ceiling.value != null && g.recall_vs_ceiling.value < 0.95 && "text-rose-600")}>{pct(g.recall_vs_ceiling.value)}</td>
                    <td>{pct(g.pass_rate.value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      {rep.hasGold && (
        <section>
          <div className="mb-1.5 flex items-center gap-2">
            <h3 className="text-[12px] font-semibold uppercase tracking-wide text-ink-3">Lỗi kèm đường đi ({issues.length})</h3>
            <button className="ml-auto cursor-pointer text-primary-ink hover:underline" onClick={() => setShowTp((v) => !v)}>
              {showTp ? "Ẩn task khớp" : `Hiện ${rep.matches.length} task khớp nhãn`}
            </button>
          </div>
          {issues.length === 0 && <p className="rounded-md bg-emerald-50 p-2 text-emerald-800">Không có lỗi nào trên dữ liệu này.</p>}
          <ul className="space-y-1.5">
            {[...issues, ...(showTp ? rep.items.filter((i) => i.kind === "tp") : [])].map((i, k) => (
              <li key={k} className="rounded-md border border-line bg-white p-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge tone={KIND_TONE[i.kind] ?? "emerald"}>{KIND_LABEL[i.kind] ?? "Khớp"}</Badge>
                  {i.gold_id && <span className="font-mono text-ink-3">{i.gold_id}</span>}
                  {i.task_id && <span className="font-mono text-ink-3">{i.task_id}</span>}
                  <span className="text-ink-2">{i.note}</span>
                </div>
                {i.path && i.path.length > 0 && (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-ink-3">Đường đi ({i.path.length} lượt)</summary>
                    <ul className="mt-1 space-y-0.5 font-mono text-[10.5px] text-ink-3">
                      {i.path.map((p, j) => <li key={j}>{p}</li>)}
                    </ul>
                  </details>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
