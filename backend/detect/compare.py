"""Ghép report của nhiều lần chạy thành một bảng (gate_mode = none trước).

    python -m detect.compare "runs/cmp_*" [--data data/sample] [--out runs/compare.md]
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

from .eval import evaluate, load_run


def _cs(r, f):
    c = r.get("candidates") or {}
    return f(c) if c.get("n_turns") is not None else "—"


ROWS = [
    ("Recall", lambda r: r["summary"]["recall"]),
    ("Precision", lambda r: r["summary"]["precision"]),
    ("Tỉ lệ trùng", lambda r: r["summary"]["dup_rate"]),
    ("Người nhận đúng", lambda r: r["summary"]["assignee_acc"]),
    ("Hạn lịch đúng", lambda r: r["summary"]["due_acc"]),
    ("Đề xuất sai", lambda r: r["summary"]["wrong_changes"]),
    ("Lượt có op", lambda r: r["cost"]["op_turn_ratio"]),
    ("Tạo lại task đã từ chối", lambda r: r["summary"]["declined_recreated"]),
    ("Candidate recall (task đúng trong prompt lần 1)", lambda r: _cs(r, lambda c: c["first_call_recall"])),
    ("— qua linked / matched / recent", lambda r: _cs(r, lambda c: "{linked} / {matched} / {recent}".format(**c["by_source"]))),
    ("— không có ở lần 1 → vào lần 2 / bỏ lỡ hẳn", lambda r: _cs(r, lambda c: f"{c['second_found']} / {c['missed']}")),
    ("Link accuracy (task đúng trong prompt → nhắm đúng)", lambda r: _cs(r, lambda c: c["link_accuracy"])),
    ("— nhắm task khác / CREATE thay UPDATE / không làm gì", lambda r: _cs(r, lambda c: "{wrong_target} / {create_instead} / {no_op}".format(**c["link_outcomes"]))),
    ("Lượt gọi lần 2 (kiểm tra target)", lambda r: _cs(r, lambda c: c["second_call_turns"])),
]


def fmt(x) -> str:
    if isinstance(x, dict) and "value" in x:
        return "—" if x["value"] is None else f"{x['value'] * 100:.0f}% ({x['k']}/{x['n']})"
    return str(x)


def main() -> None:
    args = sys.argv[1:]
    out, data = "runs/compare.md", None
    if "--out" in args:
        i = args.index("--out")
        out = args[i + 1]
        del args[i: i + 2]
    if "--data" in args:
        from .dataset import load_dataset

        i = args.index("--data")
        data = load_dataset(args[i + 1])
        del args[i: i + 2]
    paths = [Path(p) for a in args for p in glob.glob(a)] or list(Path("runs").iterdir())
    runs = []
    for p in paths:
        if not (p / "state.json").exists():
            continue
        state, meta = load_run(p)
        if data:
            state.gold, state.hardNegatives = data["gold"], data["hard_negatives"]
        runs.append((p.name, meta, evaluate(state)))
    runs.sort(key=lambda r: (r[1].get("config", {}).get("gate_mode") != "none", r[0]))
    if not runs:
        print("Không có lần chạy nào (cần runs/<id>/state.json).")
        return
    cost = lambda r: r["cost"]  # noqa: E731
    lines = ["| Chỉ số | " + " | ".join(f"{n}<br>{m.get('extract_model')} · gate {m.get('config', {}).get('gate_mode')}" for n, m, _ in runs) + " |",
             "|---|" + "---|" * len(runs)]
    for name, f in ROWS:
        lines.append(f"| {name} | " + " | ".join(fmt(f(r)) for _, _, r in runs) + " |")
    extra = [
        ("Token prompt lần 1 p50 (ước tính)", lambda r: cost(r)["tokens"]["total"]["p50"]),
        ("Khối 1 hướng dẫn (token)", lambda r: cost(r)["tokens"]["b1"]["p50"]),
        ("LINKED / CANDIDATE p50", lambda r: f"{cost(r)['tokens']['b3a']['p50']} / {cost(r)['tokens']['b3b']['p50']}"),
        ("Token input/lượt p50 · p95 (mọi lần gọi)", lambda r: f"{cost(r)['turn_input_tokens']['p50']} · {cost(r)['turn_input_tokens']['p95']}"),
        ("Lần gọi bộ trích (lần 1 + lần 2)", lambda r: " + ".join(str(v["calls"]) for _, v in sorted(cost(r)["by_call"].items())) or "—"),
        ("Tỉ lệ token được cache", lambda r: "—" if cost(r)["cache_ratio"] is None else f"{cost(r)['cache_ratio'] * 100:.0f}%"),
        ("Embedding (token, tiền)", lambda r: f"{cost(r)['embed_tokens']} tok, ${cost(r)['embed_cost_usd']:.4f}"),
        ("Tổng tiền", lambda r: f"${cost(r)['cost_usd']:.4f}"),
        ("Chi phí / 1.000 tin", lambda r: f"${cost(r)['cost_per_1000_msgs']:.3f}"),
    ]
    for name, f in extra:
        lines.append(f"| {name} | " + " | ".join(str(f(r)) for _, _, r in runs) + " |")
    md = "\n".join(lines) + "\n"
    print(md)
    Path(out).write_text(md, encoding="utf-8")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
