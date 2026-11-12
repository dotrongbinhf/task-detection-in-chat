"""Đọc data/<dataset>/ và prompts/."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import ROOT
from .models import GoldTask, Member, Message, UserAction
from .timeexpr import TIME_CONVENTIONS

DATA = ROOT / "data"


def _jsonl(p: Path) -> list[dict[str, Any]]:
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def list_datasets() -> list[str]:
    return sorted(d.name for d in DATA.iterdir() if d.is_dir() and (d / "members.json").exists()) if DATA.exists() else []


def resolve_dir(name: str) -> Path:
    p = Path(name)
    if p.is_dir():
        return p
    return DATA / re.sub(r"[^a-zA-Z0-9_-]", "", name)


def load_dataset(name: str) -> dict[str, Any]:
    """'empty' = cùng thành viên với sample nhưng không có tin nào — để người dùng tự nhập chat."""
    d = resolve_dir("sample" if name == "empty" else name)
    members = {k: [Member(**m) for m in v] for k, v in json.loads((d / "members.json").read_text(encoding="utf-8")).items()}
    if name == "empty":
        return dict(name=name, messages=[], members=members, gold=None, hard_negatives=[], user_actions=[])
    hn = d / "hard_negatives.txt"
    gold = [GoldTask(**g) for g in _jsonl(d / "gold.jsonl")]
    return dict(
        name=Path(name).name,
        messages=[Message(**m) for m in _jsonl(d / "messages.jsonl")],
        members=members,
        gold=gold or None,
        hard_negatives=[l.strip() for l in hn.read_text(encoding="utf-8").splitlines() if l.strip()] if hn.exists() else [],
        user_actions=[UserAction(**a) for a in _jsonl(d / "user_actions.jsonl")],
    )


def load_prompts() -> dict[str, str]:
    extract = (ROOT / "prompts" / "extract_v3.txt").read_text(encoding="utf-8").replace("{time_conventions}", TIME_CONVENTIONS)
    gate = (ROOT / "prompts" / "gate_v3.txt").read_text(encoding="utf-8")
    return {"extract": extract.strip(), "gate": gate.strip()}
