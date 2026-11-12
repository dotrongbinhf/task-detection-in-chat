"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Config, EngineState, EvalReport, UserAction } from "@/lib/types";

export type Info = {
  hasKey: boolean;
  gateModel: string;
  extractModel: string;
  datasets: string[];
  prompts: { extract: string; gate: string };
  defaultConfig: Config;
  configHelp: Record<string, string>;
};

export type Toast = { id: number; tone: "info" | "error" | "success"; text: string };

async function call<T = { state: EngineState }>(body: Record<string, unknown>): Promise<T> {
  const r = await fetch("/api/engine", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  let j: { detail?: unknown; error?: string } & T;
  try {
    j = await r.json();
  } catch {
    throw new Error(r.status >= 500 ? "Không kết nối được backend Python (cổng 8000). Hãy chạy: uvicorn detect.server:app --port 8000" : `HTTP ${r.status}`);
  }
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j));
  return j;
}

export function useEngine() {
  const [info, setInfo] = useState<Info | null>(null);
  const [backendDown, setBackendDown] = useState(false);
  const [state, setState] = useState<EngineState | null>(null);
  const [dataset, setDataset] = useState("sample");
  const [mode, setMode] = useState<"heuristic" | "openai">("heuristic");
  const [config, setConfig] = useState<Config>({});
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(900);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [report, setReport] = useState<EvalReport | null>(null);
  const stateRef = useRef<EngineState | null>(null);
  stateRef.current = state;
  const busyRef = useRef(false);

  const toast = useCallback((text: string, tone: Toast["tone"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, tone, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);

  const guard = useCallback(
    async <T,>(fn: () => Promise<T>): Promise<T | null> => {
      if (busyRef.current) return null;
      busyRef.current = true;
      setBusy(true);
      try {
        return await fn();
      } catch (e) {
        toast(String((e as Error).message), "error");
        setPlaying(false);
        return null;
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [toast],
  );

  const run = useCallback(
    (body: Record<string, unknown>) =>
      guard(async () => {
        const r = await call({ ...body, state: stateRef.current, config, mode });
        setState(r.state);
        setReport(null);
        return r.state;
      }),
    [guard, config, mode],
  );

  const init = useCallback(
    (ds: string) =>
      guard(async () => {
        setPlaying(false);
        const r = await call({ cmd: "init", dataset: ds });
        setState(r.state);
        setReport(null);
        return r.state;
      }),
    [guard],
  );

  const loadInfo = useCallback(async () => {
    try {
      const r = await fetch("/api/info");
      if (!r.ok) throw new Error();
      const j = (await r.json()) as Info;
      setInfo(j);
      setBackendDown(false);
      setConfig((c) => (Object.keys(c).length ? c : j.defaultConfig));
      if (j.hasKey) setMode("openai");
      return true;
    } catch {
      setBackendDown(true);
      return false;
    }
  }, []);

  useEffect(() => {
    loadInfo().then((ok) => {
      if (ok) init("sample");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pending = state ? state.events.length - state.processed.length : 0;
  const step = useCallback((count = 1) => run({ cmd: "step", count }), [run]);

  useEffect(() => {
    if (!playing) return;
    if (pending === 0) {
      setPlaying(false);
      return;
    }
    const id = setTimeout(() => {
      if (!busyRef.current) step(1);
    }, speed);
    return () => clearTimeout(id);
  }, [playing, pending, speed, step, state]);

  const post = useCallback(
    (message: { conversation_id: string; author_id: string; text: string; mentions: string[]; reply_to_id: string | null; offset_seconds: number }) =>
      run({ cmd: "post", message }),
    [run],
  );
  const react = useCallback(
    (reaction: { target_id: string; user_id: string; type: string; offset_seconds: number }) => run({ cmd: "react", reaction }),
    [run],
  );
  const act = useCallback(
    async (action: UserAction) => {
      const before = stateRef.current?.trace.length ?? 0;
      const s = await run({ cmd: "action", action: { message_id: "", ...action, at: "" } });
      if (!s) return;
      const added = s.trace.slice(before);
      const bad = added.find((e) => e.type === "user_action_unauthorized");
      if (bad) toast(`Bị bỏ qua — user_action_unauthorized: ${String(bad.reason)}`, "error");
      else if (added.some((e) => e.type === "user_action_unmatched")) toast("user_action_unmatched: không có nháp/đề xuất khớp", "error");
      else toast("Đã ghi nhận thao tác của người nhận", "success");
    },
    [run, toast],
  );
  const advance = useCallback((hours: number) => run({ cmd: "advance", hours }), [run]);
  const evaluate = useCallback(
    () =>
      guard(async () => {
        const r = await call<{ report: EvalReport }>({ cmd: "evaluate", state: stateRef.current, config, mode });
        setReport(r.report);
        return r.report;
      }),
    [guard, config, mode],
  );

  return {
    info, backendDown, loadInfo, state, dataset, setDataset, mode, setMode, config, setConfig, busy, playing, setPlaying,
    speed, setSpeed, toasts, toast, init, step, post, react, act, advance, evaluate, report, pending,
  };
}

export type Engine = ReturnType<typeof useEngine>;
