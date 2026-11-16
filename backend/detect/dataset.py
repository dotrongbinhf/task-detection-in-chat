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


def load_dataset(name: str, split: str | None = None) -> dict[str, Any]:
    """'empty' = cùng thành viên với sample nhưng không có tin nào — để người dùng tự nhập chat.

    split: tên một phần trong `<dataset>/split.json` (dev / test) → chỉ giữ các hội thoại của phần đó."""
    d = resolve_dir("sample" if name == "empty" else name)
    members = {k: [Member(**m) for m in v] for k, v in json.loads((d / "members.json").read_text(encoding="utf-8")).items()}
    if name == "empty":
        return dict(name=name, messages=[], members=members, gold=None, hard_negatives=[], hard_negative_tags={},
                    user_actions=[], turn_labels=[])
    hn = d / "hard_negatives.txt"
    # một dòng một hard negative: `<message_id>` hoặc `<message_id>	<tag>` (v6 mục 10.3)
    hn_rows = [l.strip().split("	") for l in hn.read_text(encoding="utf-8").splitlines() if l.strip()] if hn.exists() else []
    gold = [GoldTask(**g) for g in _jsonl(d / "gold.jsonl")]
    ds = dict(
        name=Path(name).name,
        messages=[Message(**m) for m in _jsonl(d / "messages.jsonl")],
        members=members,
        gold=gold or None,
        hard_negatives=[r[0] for r in hn_rows],
        hard_negative_tags={r[0]: r[1] for r in hn_rows if len(r) > 1},
        user_actions=[UserAction(**a) for a in _jsonl(d / "user_actions.jsonl")],
        turn_labels=_jsonl(d / "turn_labels.jsonl"),
    )
    return subset(ds, d, split) if split else ds


def subset(ds: dict[str, Any], d: Path, split: str) -> dict[str, Any]:
    """Chỉ giữ hội thoại thuộc `split` (tin, nhãn, hard negative, thao tác duyệt)."""
    p = d / "split.json"
    if not p.exists():
        raise SystemExit(f"{d} không có split.json")
    parts = json.loads(p.read_text(encoding="utf-8"))
    if split not in parts:
        raise SystemExit(f"split.json không có phần '{split}' (có: {', '.join(parts)})")
    keys = set(parts[split])
    msgs = [m for m in ds["messages"] if m.conversation_id in keys]
    ids = {m.id for m in msgs}
    return {**ds, "name": f"{ds['name']}:{split}", "messages": msgs,
            "members": {k: v for k, v in ds["members"].items() if k in keys},
            "gold": [g for g in ds["gold"] or [] if g.conversation_key.split(";")[0] in keys] or None,
            "hard_negatives": [h for h in ds["hard_negatives"] if h.split("#")[0] in ids],
            "turn_labels": [r for r in ds["turn_labels"] if r["conversation_key"].split(";")[0] in keys],
            "user_actions": [a for a in ds["user_actions"] if a.message_id.split("#")[0] in ids]}


def apply_labels(state: Any, ds: dict[str, Any]) -> None:
    """Gắn nhãn mới nhất của dataset vào state của một lần chạy cũ (eval / compare)."""
    state.gold, state.hardNegatives = ds["gold"], ds["hard_negatives"]
    state.hardNegativeTags, state.turnLabels = ds.get("hard_negative_tags", {}), ds.get("turn_labels", [])


def load_prompts() -> dict[str, str]:
    extract = (ROOT / "prompts" / "extract.txt").read_text(encoding="utf-8").replace("{time_conventions}", TIME_CONVENTIONS)
    gate = (ROOT / "prompts" / "gate.txt").read_text(encoding="utf-8")
    return {"extract": extract.strip(), "gate": gate.strip()}
