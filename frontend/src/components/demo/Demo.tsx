"use client";
import clsx from "clsx";
import {
  BellRing, ChevronsRight, Clock, FastForward, Info, Pause, Play, RotateCcw, ScrollText, Settings2, ServerCrash, StepForward, Workflow, BarChart3,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { fmtDayTime } from "@/lib/format";
import { ChatPanel } from "./ChatPanel";
import { ConfigDialog, PromptDialog } from "./Dialogs";
import { EvalPanel } from "./EvalPanel";
import { LedgerPanel } from "./LedgerPanel";
import { PipelineView, TurnList } from "./PipelinePanel";
import { TracePanel } from "./TracePanel";
import { Button, PanelHeader, Segmented } from "./ui";
import { useEngine } from "./useEngine";
import type { TurnRecord } from "@/lib/types";

const DATASET_LABEL: Record<string, string> = { sample: "Dữ liệu mẫu (53 tin · 13 task nhãn)", empty: "Trống — tự nhập chat" };

export default function Demo() {
  const engine = useEngine();
  const { state, info } = engine;
  const [pinnedTurn, setPinnedTurn] = useState<number | null>(null);
  const [selTask, setSelTask] = useState<string | null>(null);
  const [conv, setConv] = useState("ch_design");
  const [viewer, setViewer] = useState("u_linh");
  const [centerTab, setCenterTab] = useState<"pipeline" | "trace" | "eval">("pipeline");
  const [mobileTab, setMobileTab] = useState<"chat" | "pipeline" | "ledger">("chat");
  const [promptTurn, setPromptTurn] = useState<TurnRecord | null>(null);
  const [showConfig, setShowConfig] = useState(false);

  const latest = state?.turns[state.turns.length - 1] ?? null;
  const turn = useMemo(() => (pinnedTurn != null ? state?.turns.find((t) => t.turn === pinnedTurn) ?? latest : latest), [pinnedTurn, state, latest]);
  const task = state?.tasks.find((t) => t.task_id === selTask) ?? null;

  // theo lượt mới nhất: chuyển chat sang hội thoại của lượt đó
  useEffect(() => {
    if (pinnedTurn == null && latest) setConv(latest.key.split(";")[0]);
  }, [latest, pinnedTurn]);
  useEffect(() => {
    if (state && !state.members[conv]) setConv(Object.keys(state.members)[0]);
  }, [state, conv]);

  if (engine.backendDown) {
    return (
      <main className="flex min-h-dvh items-center justify-center p-6">
        <div className="max-w-lg rounded-xl border border-line bg-white p-6 text-sm text-ink-2 shadow-sm">
          <div className="mb-2 flex items-center gap-2 text-base font-semibold text-ink"><ServerCrash size={18} className="text-rose-600" /> Chưa kết nối được backend Python</div>
          <p className="mb-3">UI gọi engine qua <code>/api</code> → <code>http://127.0.0.1:8000</code>. Mở terminal và chạy:</p>
          <pre className="mb-3 rounded-md bg-slate-900 p-3 font-mono text-xs text-slate-100">cd backend{"\n"}.venv\Scripts\activate{"\n"}uvicorn detect.server:app --port 8000 --reload</pre>
          <Button variant="primary" onClick={() => engine.loadInfo().then((ok) => { if (ok) engine.init(engine.dataset); })}>Thử kết nối lại</Button>
        </div>
      </main>
    );
  }
  if (!state || !info) {
    return <main className="flex min-h-dvh items-center justify-center text-sm text-ink-3">Đang tải engine…</main>;
  }

  const total = state.events.length;
  const done = state.processed.length;
  const selectTurnForEvent = (id: string) => {
    const t = [...state.turns].reverse().find((x) => x.new_event_ids.includes(id));
    if (t) {
      setPinnedTurn(t.turn);
      setCenterTab("pipeline");
    }
  };

  return (
    <div className="flex h-dvh min-h-0 flex-col">
      {/* ---------- header ---------- */}
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-white px-4 py-2">
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-white"><BellRing size={17} aria-hidden /></span>
          <div className="leading-tight">
            <h1 className="text-[15px] font-semibold text-ink">AI Reminder <span className="font-normal text-ink-3">· phát hiện task từ chat</span></h1>
            <p className="text-[11px] text-ink-3">Prototype v3 — sổ cái cập nhật theo từng tin · bằng chứng theo task · người nhận duyệt</p>
          </div>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2 text-xs">
          <label className="text-ink-3" htmlFor="ds">Dữ liệu</label>
          <select id="ds" value={engine.dataset} onChange={(e) => { engine.setDataset(e.target.value); engine.init(e.target.value); setPinnedTurn(null); setSelTask(null); }}
            className="h-8 cursor-pointer rounded-md border border-line bg-white px-2">
            {info.datasets.map((d) => <option key={d} value={d}>{DATASET_LABEL[d] ?? d}</option>)}
          </select>
          <label className="ml-1 text-ink-3" htmlFor="mode">LLM</label>
          <select id="mode" value={engine.mode} onChange={(e) => engine.setMode(e.target.value as "openai" | "heuristic")}
            className="h-8 cursor-pointer rounded-md border border-line bg-white px-2">
            <option value="openai" disabled={!info.hasKey}>OpenAI · {info.extractModel}{info.hasKey ? "" : " (chưa có key)"}</option>
            <option value="heuristic">Giả lập bằng luật (offline)</option>
          </select>
          <label className="ml-1 text-ink-3" htmlFor="gm">Cổng</label>
          <select id="gm" value={String(engine.config.gate_mode ?? "none")} onChange={(e) => engine.setConfig({ ...engine.config, gate_mode: e.target.value })}
            className="h-8 cursor-pointer rounded-md border border-line bg-white px-2">
            <option value="none">none — trần chất lượng</option>
            <option value="shadow">shadow — chấm p, không chặn</option>
            <option value="llm">llm — chặn khi p &lt; {engine.config.gate_threshold}</option>
          </select>
          <Button onClick={() => setShowConfig(true)} aria-label="Tham số"><Settings2 size={14} /> Tham số</Button>
        </div>
      </header>

      {engine.mode === "heuristic" && (
        <div className="flex items-center gap-2 border-b border-orange-200 bg-orange-50 px-4 py-1.5 text-[12px] text-orange-900">
          <Info size={14} aria-hidden />
          Đang dùng <b>bộ trích giả lập bằng luật</b> để xem luồng offline — không phản ánh chất lượng LLM thật.
          {!info.hasKey && <> Điền <code className="rounded bg-white/70 px-1">OPENAI_API_KEY</code> vào <code className="rounded bg-white/70 px-1">backend/.env</code> rồi khởi động lại backend để dùng OpenAI.</>}
        </div>
      )}

      {/* ---------- điều khiển ---------- */}
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-white px-4 py-2">
        <Button onClick={() => { engine.init(engine.dataset); setPinnedTurn(null); setSelTask(null); }} disabled={engine.busy} title="Nạp lại dataset với tham số hiện tại">
          <RotateCcw size={14} /> Chạy lại từ đầu
        </Button>
        <Button variant="primary" onClick={() => { setPinnedTurn(null); engine.step(1); }} disabled={engine.busy || engine.pending === 0}>
          <StepForward size={14} /> Lượt kế
        </Button>
        <Button onClick={() => { setPinnedTurn(null); engine.setPlaying(!engine.playing); }} disabled={engine.pending === 0 && !engine.playing} aria-pressed={engine.playing}>
          {engine.playing ? <><Pause size={14} /> Tạm dừng</> : <><Play size={14} /> Tự chạy</>}
        </Button>
        <label htmlFor="speed" className="sr-only">Tốc độ</label>
        <select id="speed" value={engine.speed} onChange={(e) => engine.setSpeed(Number(e.target.value))} className="h-8 cursor-pointer rounded-md border border-line bg-white px-1.5 text-xs">
          <option value={1800}>Chậm</option>
          <option value={900}>Vừa</option>
          <option value={250}>Nhanh</option>
        </select>
        <Button onClick={() => { setPinnedTurn(null); engine.step(-1); }} disabled={engine.busy || engine.pending === 0}>
          <ChevronsRight size={14} /> Chạy hết
        </Button>

        <div className="mx-2 flex min-w-40 flex-1 items-center gap-2">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100" role="progressbar" aria-valuenow={done} aria-valuemax={total} aria-label="Tiến độ luồng sự kiện">
            <div className="h-full rounded-full bg-primary transition-[width] duration-300" style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
          </div>
          <span className="whitespace-nowrap text-[11.5px] tabular-nums text-ink-3">{done}/{total} sự kiện · {state.turns.length} lượt</span>
          {engine.busy && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary border-t-transparent" aria-label="Đang xử lý" />}
        </div>

        <div className="flex items-center gap-1.5 text-xs">
          <Clock size={14} className="text-ink-3" aria-hidden />
          <span className="text-ink-3">Giờ mô phỏng</span>
          <b className="tabular-nums text-ink">{fmtDayTime(state.simNow)}</b>
          <span className="ml-1 text-ink-3">Tua:</span>
          {[1, 24, 72].map((h) => (
            <Button key={h} size="sm" onClick={() => engine.advance(h)} disabled={engine.busy} title="Tua thời gian để thấy luật hết hạn (candidate 48h, nháp 7 ngày…)">
              <FastForward size={12} /> {h < 24 ? `${h} giờ` : `${h / 24} ngày`}
            </Button>
          ))}
        </div>
      </div>

      {/* ---------- tab cho màn hình nhỏ ---------- */}
      <div className="border-b border-line bg-white px-3 py-1.5 xl:hidden">
        <Segmented label="Chọn khung" value={mobileTab} onChange={setMobileTab}
          options={[{ value: "chat", label: "Chat" }, { value: "pipeline", label: "Pipeline" }, { value: "ledger", label: "Sổ cái" }]} />
      </div>

      {/* ---------- 3 cột ---------- */}
      <main className="grid min-h-0 flex-1 grid-cols-1 gap-3 p-3 xl:grid-cols-[minmax(340px,0.95fr)_minmax(460px,1.3fr)_minmax(360px,1fr)]">
        <div className={clsx("min-h-0", mobileTab !== "chat" && "hidden xl:flex", "flex flex-col")}>
          <ChatPanel engine={engine} conv={conv} setConv={(c) => { setConv(c); }} turn={turn} task={task} onSelectTurnForEvent={selectTurnForEvent} onActivity={() => { setPinnedTurn(null); setCenterTab("pipeline"); }} />
        </div>

        <section className={clsx("min-h-0 flex-col overflow-hidden rounded-lg border border-line bg-surface", mobileTab !== "pipeline" ? "hidden xl:flex" : "flex")} aria-label="Pipeline">
          <PanelHeader
            title={centerTab === "pipeline" ? (turn ? `Lượt #${turn.turn}` : "Pipeline") : centerTab === "trace" ? "trace.jsonl" : "Đánh giá (mục 8)"}
            icon={centerTab === "pipeline" ? <Workflow size={16} /> : centerTab === "trace" ? <ScrollText size={16} /> : <BarChart3 size={16} />}
            sub={centerTab === "pipeline" && turn ? (pinnedTurn != null ? "đang ghim — bấm “Mới nhất” để theo dõi" : "theo dõi lượt mới nhất") : undefined}
            right={
              <div className="flex items-center gap-2">
                {centerTab === "pipeline" && pinnedTurn != null && <Button size="sm" onClick={() => setPinnedTurn(null)}>Mới nhất</Button>}
                <Segmented label="Khung giữa" value={centerTab} onChange={setCenterTab}
                  options={[{ value: "pipeline", label: "Pipeline" }, { value: "trace", label: "Trace" }, { value: "eval", label: "Đánh giá" }]} />
              </div>
            }
          />
          {centerTab === "pipeline" && <TurnList state={state} selected={turn?.turn ?? null} onSelect={(n) => setPinnedTurn(n)} />}
          <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
            {centerTab === "pipeline" && <PipelineView key={turn?.turn} state={state} turn={turn} onPrompt={setPromptTurn} selectTask={(id) => { setSelTask(id); setMobileTab("ledger"); }} />}
            {centerTab === "trace" && <TracePanel state={state} />}
            {centerTab === "eval" && <EvalPanel engine={engine} />}
          </div>
        </section>

        <div className={clsx("min-h-0 flex-col", mobileTab !== "ledger" ? "hidden xl:flex" : "flex")}>
          <LedgerPanel engine={engine} viewer={viewer} setViewer={setViewer} selectedTask={selTask} onSelectTask={setSelTask} />
        </div>
      </main>

      {/* ---------- toast ---------- */}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2" aria-live="polite">
        {engine.toasts.map((t) => (
          <div key={t.id} className={clsx("animate-rise pointer-events-auto rounded-lg border px-3 py-2 text-xs shadow-lg",
            t.tone === "error" && "border-rose-200 bg-rose-50 text-rose-800",
            t.tone === "success" && "border-emerald-200 bg-emerald-50 text-emerald-800",
            t.tone === "info" && "border-line bg-white text-ink-2")}>
            {t.text}
          </div>
        ))}
      </div>

      {promptTurn && <PromptDialog turn={promptTurn} instructions={info.prompts.extract} onClose={() => setPromptTurn(null)} />}
      {showConfig && <ConfigDialog engine={engine} onClose={() => setShowConfig(false)} />}
    </div>
  );
}
