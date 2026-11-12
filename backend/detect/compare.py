"""Ghép report của nhiều lần chạy thành một bảng; cột đầu là lần chạy gate_mode=shadow.

    python -m detect.compare runs/*
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

from .eval import evaluate, load_run

ROWS = [
    ("Recall", lambda r: r["summary"]["recall"]),
    ("Precision", lambda r: r["summary"]["precision"]),
    ("Tỉ lệ trùng", lambda r: r["summary"]["dup_rate"]),
    ("Người nhận đúng", lambda r: r["summary"]["assignee_acc"]),
    ("Hạn lịch đúng", lambda r: r["summary"]["due_acc"]),
    ("Đề xuất sai", lambda r: r["summary"]["wrong_changes"]),
    ("Lượt có op", lambda r: r["cost"]["op_turn_ratio"]),
]


def fmt(x) -> str:
    if isinstance(x, dict) and "value" in x:
        return "—" if x["value"] is None else f"{x['value'] * 100:.0f}% ({x['k']}/{x['n']})"
    return str(x)


def main() -> None:
    paths = [Path(p) for a in sys.argv[1:] for p in glob.glob(a)] or [p for p in Path("runs").iterdir()]
    runs = []
    for p in paths:
        if not (p / "state.json").exists():
            continue
        state, meta = load_run(p)
        runs.append((p.name, meta, evaluate(state)))
    runs.sort(key=lambda r: (r[1].get("config", {}).get("gate_mode") != "shadow", r[0]))
    if not runs:
        print("Không có lần chạy nào (cần runs/<id>/state.json).")
        return
    head = "| Chỉ số | " + " | ".join(f"{n}<br>{m.get('extract_model')} · {m.get('config', {}).get('gate_mode')}" for n, m, _ in runs) + " |"
    lines = [head, "|---|" + "---|" * len(runs)]
    for name, f in ROWS:
        lines.append(f"| {name} | " + " | ".join(fmt(f(r)) for _, _, r in runs) + " |")
    lines.append("| Token/lượt p50 | " + " | ".join(str(r["cost"]["tokens"]["total"]["p50"]) for _, _, r in runs) + " |")
    lines.append("| Chi phí / 1.000 tin | " + " | ".join(f"${r['cost']['cost_per_1000_msgs']:.3f}" for _, _, r in runs) + " |")
    md = "\n".join(lines) + "\n"
    print(md)
    Path("runs/compare.md").write_text(md, encoding="utf-8")


if __name__ == "__main__":
    main()
