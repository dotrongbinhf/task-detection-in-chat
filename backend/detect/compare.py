"""So nhiều lần chạy thành một bảng, hoặc so hai lần chạy theo từng nhãn.

    python -m detect.compare "runs/cmp_*" [--data data/eval] [--out runs/compare.md]
    python -m detect.compare --pair runs/A runs/B [--data data/eval] [--noise 2]

`--pair`: in nhãn ĐƯỢC THÊM / BỊ MẤT (theo gold_id) cho recall và nháp đúng hoàn toàn thay vì so hai tỉ lệ (v6 mục 11.5).
Chênh lệch ròng ≤ `--noise` nhãn (mức dao động đo bằng 3 seed, ghi trong DECISIONS) → "trong mức nhiễu".
"""
from __future__ import annotations

import argparse
import glob
from pathlib import Path
from typing import Any

from .eval import evaluate, load_run


def _cs(r, f):
    c = r.get("candidates") or {}
    return f(c) if c.get("n_turns") is not None else "—"


def _to(r, key):
    t = r.get("turn_ops") or {}
    return t.get(key, "—") if t.get("available") else "—"


ROWS = [
    ("Nháp đúng hoàn toàn", lambda r: r["summary"]["strict_correct"]),
    ("Recall", lambda r: r["summary"]["recall"]),
    ("Precision", lambda r: r["summary"]["precision"]),
    ("Tỉ lệ trùng", lambda r: r["summary"]["dup_rate"]),
    ("Người nhận đúng", lambda r: r["summary"]["assignee_acc"]),
    ("Hạn lịch đúng", lambda r: r["summary"]["due_acc"]),
    ("Lượt báo nhầm", lambda r: r["summary"]["false_alarm_turns"]),
    ("Lượt bỏ sót", lambda r: _to(r, "missed")),
    ("Đúng loại op", lambda r: _to(r, "type_correct")),
    ("Đề xuất sai", lambda r: r["summary"]["wrong_changes"]),
    ("Tạo lại task đã từ chối", lambda r: r["summary"]["declined_recreated"]),
    ("Candidate recall (task đúng trong prompt lần 1)", lambda r: _cs(r, lambda c: c["first_call_recall"])),
    ("— theo tầng", lambda r: _cs(r, lambda c: " / ".join(f"{k} {v}" for k, v in c["by_source"].items()) or "—")),
    ("— vào lần 2 / bỏ lỡ", lambda r: _cs(r, lambda c: f"{c['second_found']} / {c['missed']}")),
    ("Link accuracy", lambda r: _cs(r, lambda c: c["link_accuracy"])),
    ("— nhắm task khác / CREATE thay UPDATE / không làm gì", lambda r: _cs(r, lambda c: "{wrong_target} / {create_instead} / {no_op}".format(**c["link_outcomes"]))),
]


def fmt(x: Any) -> str:
    if isinstance(x, dict) and "value" in x:
        if x["value"] is None:
            return "—"
        ci = f", {x['lo'] * 100:.0f}–{x['hi'] * 100:.0f}" if x.get("method") == "bootstrap" else ""
        return f"{x['value'] * 100:.0f}% ({x['k']}/{x['n']}{ci})"
    return str(x)


def _load(p: Path, data: Any) -> tuple[str, dict, dict]:
    state, meta = load_run(p)
    if data:
        from .dataset import apply_labels

        apply_labels(state, data)
    return p.name, meta, evaluate(state)


def table(runs: list[tuple[str, dict, dict]]) -> str:
    cost = lambda r: r["cost"]  # noqa: E731
    lines = ["| Chỉ số | " + " | ".join(f"{n}<br>{m.get('extract_model')} · seed {m.get('seed', '-')}" for n, m, _ in runs) + " |",
             "|---|" + "---|" * len(runs)]
    for name, f in ROWS:
        lines.append(f"| {name} | " + " | ".join(fmt(f(r)) for _, _, r in runs) + " |")
    extra = [
        ("Token prompt lần 1 p50 (ước tính)", lambda r: cost(r)["tokens"]["total"]["p50"]),
        ("Token input/lượt p50 · p95 (mọi lần gọi)", lambda r: f"{cost(r)['turn_input_tokens']['p50']} · {cost(r)['turn_input_tokens']['p95']}"),
        ("Lượt / lần gọi bộ trích (lần 1 + lần 2)", lambda r: f"{cost(r)['turns']} / " + (" + ".join(str(v["calls"]) for _, v in sorted(cost(r)["by_call"].items())) or "—")),
        ("Tỉ lệ token được cache", lambda r: "—" if cost(r)["cache_ratio"] is None else f"{cost(r)['cache_ratio'] * 100:.0f}%"),
        ("Tổng tiền", lambda r: f"${cost(r)['cost_usd']:.4f}"),
        ("Chi phí / 1.000 tin", lambda r: f"${cost(r)['cost_per_1000_msgs']:.3f}"),
    ]
    for name, f in extra:
        lines.append(f"| {name} | " + " | ".join(str(f(r)) for _, _, r in runs) + " |")
    return "\n".join(lines) + "\n"


def pair(a: tuple[str, dict, dict], b: tuple[str, dict, dict], noise: int) -> str:
    """Nhãn được thêm / bị mất từ A sang B cho recall và nháp đúng hoàn toàn."""
    L = [f"# So `{a[0]}` → `{b[0]}`", ""]
    for name, key in (("Recall", "recall_ids"), ("Nháp đúng hoàn toàn", "strict_ids")):
        sa, sb = set(a[2]["summary"][key]), set(b[2]["summary"][key])
        gained, lost = sorted(sb - sa), sorted(sa - sb)
        net = len(gained) - len(lost)
        verdict = "trong mức nhiễu" if abs(net) <= noise else ("tốt hơn" if net > 0 else "xấu hơn")
        L += [f"## {name}: {len(sa)} → {len(sb)} ({net:+d}, {verdict}; nhiễu ±{noise} nhãn)",
              f"- Được thêm ({len(gained)}): " + (", ".join(gained) or "—"),
              f"- Bị mất ({len(lost)}): " + (", ".join(lost) or "—"), ""]
    L += ["## Bảng", "", table([a, b])]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="*")
    ap.add_argument("--pair", nargs=2, metavar=("A", "B"))
    ap.add_argument("--data", default=None)
    ap.add_argument("--noise", type=int, default=2, help="mức nhiễu (số nhãn) khi so --pair")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    data = None
    if a.data:
        from .dataset import load_dataset

        data = load_dataset(a.data)
    if a.pair:
        md = pair(_load(Path(a.pair[0]), data), _load(Path(a.pair[1]), data), a.noise)
        out = a.out or f"runs/pair_{Path(a.pair[0]).name}__{Path(a.pair[1]).name}.md"
    else:
        paths = [Path(p) for x in a.runs for p in glob.glob(x)] or list(Path("runs").iterdir())
        runs = sorted((_load(p, data) for p in paths if (p / "state.json").exists()), key=lambda r: r[0])
        if not runs:
            print("Không có lần chạy nào (cần runs/<id>/state.json).")
            return
        md = table(runs)
        out = a.out or "runs/compare.md"
    print(md)
    Path(out).write_text(md, encoding="utf-8")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
