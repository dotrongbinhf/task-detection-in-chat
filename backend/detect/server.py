"""API HTTP mỏng cho UI demo. Không giữ trạng thái: client gửi EngineState, server chạy một lệnh và trả state mới.

    uvicorn detect.server:app --port 8000 --reload
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import CONFIG_HELP, load_config
from .dataset import list_datasets, load_dataset, load_prompts
from .eval import evaluate
from .llm import extract_model_name, gate_model_name, has_openai_key, make_llm, probe
from .models import EngineState, UserAction
from .run import (add_user_message, add_user_reaction, advance_time, flush_actions, init_state, now_plus, pending_events,
                  process_turn, ui_user_action)
from .tokens import set_model

app = FastAPI(title="AI Reminder — task detection v3")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class PostMessage(BaseModel):
    conversation_id: str
    author_id: str
    text: str
    mentions: list[str] = []
    reply_to_id: Optional[str] = None
    offset_seconds: float = 60


class PostReaction(BaseModel):
    target_id: str
    user_id: str
    type: str = "like"
    offset_seconds: float = 30


class Command(BaseModel):
    cmd: Literal["init", "step", "post", "react", "action", "advance", "evaluate"]
    dataset: Optional[str] = None
    state: Optional[EngineState] = None
    config: dict[str, Any] = {}
    mode: Literal["openai", "heuristic"] = "heuristic"
    noCache: bool = False
    count: int = 1  # -1 = tới hết
    message: Optional[PostMessage] = None
    reaction: Optional[PostReaction] = None
    action: Optional[UserAction] = None
    hours: float = 1


@app.get("/api/info")
def info() -> dict[str, Any]:
    cfg = load_config()
    return {
        "hasKey": has_openai_key(),
        "gateModel": gate_model_name(),
        "extractModel": extract_model_name(),
        "datasets": list_datasets() + ["empty"],
        "prompts": load_prompts(),
        "defaultConfig": cfg.model_dump(exclude={"pricing"}),
        "configHelp": CONFIG_HELP,
    }


@app.post("/api/probe")
def api_probe() -> dict[str, Any]:
    return probe()


@app.post("/api/engine")
def engine(body: Command) -> dict[str, Any]:
    cfg = load_config(overrides=body.config)
    if body.cmd == "init":
        state = init_state(load_dataset(body.dataset or "sample"))
        return {"state": state.model_dump(mode="json")}
    if body.state is None:
        raise HTTPException(400, "thiếu state")
    state = body.state
    if body.cmd == "evaluate":
        return {"report": evaluate(state, cfg.pricing)}
    try:
        llm = make_llm(body.mode, body.noCache)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    set_model(llm.extract_model)
    prompts = load_prompts()

    def run_until(pred, limit: int = 2000) -> None:
        for _ in range(limit):
            if not pred() or process_turn(state, cfg, llm, prompts) is None:
                break

    if body.cmd == "step":
        n = 2000 if body.count < 0 else body.count
        run_until(lambda: bool(pending_events(state)), n)
        if not pending_events(state):
            flush_actions(state, cfg)
    elif body.cmd == "post" and body.message:
        m = body.message
        msg = add_user_message(state, m.conversation_id, m.author_id, m.text, m.mentions, m.reply_to_id, now_plus(state, m.offset_seconds))
        run_until(lambda: msg.id not in state.processed)
    elif body.cmd == "react" and body.reaction:
        r = body.reaction
        ev = add_user_reaction(state, r.target_id, r.user_id, r.type, now_plus(state, r.offset_seconds))
        if ev:
            run_until(lambda: ev.id not in state.processed)
    elif body.cmd == "action" and body.action:
        ui_user_action(state, body.action, cfg)
    elif body.cmd == "advance":
        advance_time(state, body.hours, cfg)
    return {"state": state.model_dump(mode="json")}
