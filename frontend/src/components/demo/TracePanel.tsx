"use client";
import { useMemo, useState } from "react";
import { fmtDayTime } from "@/lib/format";
import type { EngineState } from "@/lib/types";
import { describeEvent } from "./PipelinePanel";
import { Badge, Empty } from "./ui";

export function TracePanel({ state }: { state: EngineState }) {
  const types = useMemo(() => [...new Set(state.trace.map((e) => e.type))].sort(), [state.trace]);
  const [type, setType] = useState("all");
  const rows = state.trace.filter((e) => (type === "all" ? e.type !== "turn" : e.type === type)).slice().reverse();
  if (!state.trace.length) return <Empty title="trace.jsonl đang trống" />;
  return (
    <div className="p-3 text-xs">
      <div className="mb-2 flex items-center gap-2">
        <label htmlFor="trace-type" className="text-ink-3">Loại sự kiện</label>
        <select id="trace-type" value={type} onChange={(e) => setType(e.target.value)} className="h-7 cursor-pointer rounded-md border border-line bg-white px-1.5">
          <option value="all">Tất cả (trừ turn)</option>
          {types.map((t) => (
            <option key={t} value={t}>{t} ({state.trace.filter((e) => e.type === t).length})</option>
          ))}
        </select>
        <span className="ml-auto text-ink-3">{rows.length} dòng</span>
      </div>
      <ul className="divide-y divide-line rounded-md border border-line bg-white">
        {rows.map((e, i) => (
          <li key={i} className="flex items-start gap-2 px-2 py-1.5">
            <span className="w-24 shrink-0 tabular-nums text-ink-3">{fmtDayTime(e.at)}</span>
            {e.turn != null && <span className="w-9 shrink-0 font-mono text-ink-3">#{e.turn}</span>}
            <Badge className="shrink-0 font-mono">{e.type}</Badge>
            <span className="min-w-0 break-words text-ink-2">{e.type === "turn" ? JSON.stringify(e) : describeEvent(e)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
