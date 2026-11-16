"""Nhãn theo lượt (tài liệu v5 mục 6.2, 6.3). Không gọi LLM, không cổng.

    python -m detect.label export --data data/eval --out turns.jsonl [--k 5]
    python -m detect.label derive --data data/eval --out data/eval/turn_labels.jsonl
    python -m detect.label import --data data/eval a.jsonl b.jsonl --out data/eval/turn_labels.jsonl [--adjudicator A]
    python -m detect.label agree a.jsonl b.jsonl
    python -m detect.label stats data/eval/turn_labels.jsonl [--data data/eval]

Lượt = kết quả hàng đợi + gộp tin (`run.next_batch`), nên `turn_id` khớp với lượt của engine.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import Config, load_config
from .context import _fmt_list, _to_prompt_msg
from .dataset import load_dataset, resolve_dir
from .models import StreamEvent
from .run import init_state, next_batch

KINDS = ("assign", "accept", "self_commit", "open_ask", "due", "assignee", "content", "cancel")
GOLD_KIND = {"assigned": "assign", "self_commit": "self_commit", "open_ask": "open_ask"}


def read_jsonl(p: str | Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def write_jsonl(p: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    rows = list(rows)
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return len(rows)


def turns(ds: dict[str, Any], cfg: Config) -> list[tuple[str, list[StreamEvent]]]:
    """Mọi lượt của dataset theo đúng hàng đợi của engine: [(conversation_key, sự kiện)]."""
    state = init_state(ds)
    out = []
    while nb := next_batch(state, cfg):
        out.append(nb)
        state.processed += [e.id for e in nb[1]]
    return out


def turn_id(key: str, batch: list[StreamEvent]) -> str:
    return f"{key}:{batch[0].id}"


# ---------------- export ----------------

def export(ds: dict[str, Any], cfg: Config, k: int) -> list[dict[str, Any]]:
    by_key: dict[str, list[StreamEvent]] = {}
    for e in init_state(ds).events:
        by_key.setdefault(e.key, []).append(e)
    rows = []
    for key, batch in turns(ds, cfg):
        evs = by_key[key]
        i = evs.index(batch[0])
        window = evs[max(0, i - k):i]

        def fmt(lst: list[StreamEvent]) -> str:
            pm = [_to_prompt_msg(e, "window") for e in lst]
            for m in pm:
                m.local = m.id
            return _fmt_list(pm, {e.id: e.id for e in evs}, 200)

        msgs = [e.message for e in batch if e.message]
        rows.append({
            "turn_id": turn_id(key, batch), "conversation_key": key, "new_event_ids": [e.id for e in batch],
            "new_text": fmt(batch), "context": fmt(window),
            "has_reply": any(m.reply_to_id for m in msgs), "has_reaction": any(e.kind == "reaction" for e in batch),
            "has_mention": any(m.mentions for m in msgs),
            "label": None, "kinds": [], "annotator": "", "note": "",
        })
    return rows


# ---------------- derive ----------------

def derive(ds: dict[str, Any], cfg: Config) -> list[dict[str, Any]]:
    """Lượt chứa tin supporting của một nhãn → 1; chứa hard negative → 0 + hard; còn lại → 0.

    kinds: gold không ghi vai trò từng tin → chỉ suy được loại ở tin anchor (theo `kind`) và reaction (= accept).
    """
    gold = ds["gold"] or []
    support = {i: g for g in gold for i in g.supporting_message_ids}
    hard = set(ds["hard_negatives"])
    rows = []
    for key, batch in turns(ds, cfg):
        ids = [e.id for e in batch]
        hit = [i for i in ids if i in support]
        row: dict[str, Any] = {"turn_id": turn_id(key, batch), "conversation_key": key, "new_event_ids": ids,
                               "label": int(bool(hit)), "kinds": [], "annotator": "derived", "note": ""}
        if hit:
            kinds = set()
            for i in hit:
                g = support[i]
                if i == g.anchor_message_id:
                    kinds.add(GOLD_KIND[g.kind])
                elif "#r" in i:
                    kinds.add("accept")
            row["kinds"] = [x for x in KINDS if x in kinds]
            row["note"] = "gold: " + ",".join(sorted({support[i].gold_id for i in hit}))
        elif set(ids) & hard:
            row["hard"] = True
        rows.append(row)
    return rows


# ---------------- import ----------------

def merge(derived: list[dict[str, Any]], files: list[list[dict[str, Any]]], names: list[str],
          adjudicator: Optional[str] = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Gộp nhãn tay lên nhãn suy. Cùng turn_id khác nhãn → conflict; nhãn cuối = nhãn của adjudicator nếu có, không thì nhãn suy."""
    hand: dict[str, dict[str, dict[str, Any]]] = {}
    for rows, name in zip(files, names):
        for r in rows:
            if r.get("label") is None:
                continue
            who = r.get("annotator") or name
            hand.setdefault(r["turn_id"], {})[who] = {**r, "annotator": who}
    out, conflicts = [], []
    for d in derived:
        votes = hand.pop(d["turn_id"], {})
        final = d
        if votes:
            labels = {int(v["label"]) for v in votes.values()}
            if len(labels) == 1:
                final = next(iter(votes.values()))
            else:
                pick = votes.get(adjudicator) if adjudicator else None
                final = pick or d
                conflicts.append({"turn_id": d["turn_id"], "labels": {w: int(v["label"]) for w, v in votes.items()},
                                  "derived": d["label"], "final": int(final["label"]), "by": final.get("annotator")})
        out.append({**{k: d[k] for k in ("turn_id", "conversation_key", "new_event_ids")},
                    "label": int(final["label"]), "kinds": final.get("kinds") or [], "annotator": final.get("annotator", ""),
                    "note": final.get("note", ""), **({"hard": True} if d.get("hard") else {})})
    if hand:
        raise SystemExit(f"turn_id không có trong dataset (gán nhãn trên bản export cũ?): {sorted(hand)[:5]}")
    return out, conflicts


# ---------------- agree ----------------

def kappa(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> dict[str, Any]:
    la = {r["turn_id"]: int(r["label"]) for r in a if r.get("label") is not None}
    lb = {r["turn_id"]: int(r["label"]) for r in b if r.get("label") is not None}
    common = sorted(la.keys() & lb.keys())
    n = len(common)
    tab = Counter((la[t], lb[t]) for t in common)
    if not n:
        return {"n": 0, "kappa": None, "table": {}, "disagree": []}
    po = (tab[0, 0] + tab[1, 1]) / n
    pa1, pb1 = (tab[1, 0] + tab[1, 1]) / n, (tab[0, 1] + tab[1, 1]) / n
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    k = 1.0 if pe == 1 else (po - pe) / (1 - pe)
    return {"n": n, "agreement": po, "kappa": k, "table": {f"{x}{y}": tab[x, y] for x in (0, 1) for y in (0, 1)},
            "disagree": [t for t in common if la[t] != lb[t]]}


# ---------------- stats ----------------

def stats(rows: list[dict[str, Any]], split: Optional[dict[str, list[str]]] = None) -> dict[str, Any]:
    def one(rs: list[dict[str, Any]]) -> dict[str, Any]:
        pos = [r for r in rs if r["label"] == 1]
        return {"turns": len(rs), "positive": len(pos), "positive_rate": round(len(pos) / len(rs), 3) if rs else None,
                "kinds": dict(Counter(k for r in pos for k in r.get("kinds") or [])),
                "hard_negative": sum(1 for r in rs if r.get("hard"))}
    out = {"all": one(rows)}
    for name, keys in (split or {}).items():
        out[name] = one([r for r in rows if r["conversation_key"] in set(keys)])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Nhãn theo lượt (v5 mục 6.3)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("export"); p.add_argument("--data", required=True); p.add_argument("--out", required=True); p.add_argument("--k", type=int, default=5)
    p = sub.add_parser("derive"); p.add_argument("--data", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("import"); p.add_argument("--data", required=True); p.add_argument("files", nargs="+")
    p.add_argument("--out", required=True); p.add_argument("--adjudicator", default=None)
    p = sub.add_parser("agree"); p.add_argument("a"); p.add_argument("b")
    p = sub.add_parser("stats"); p.add_argument("labels"); p.add_argument("--data", default=None)
    for s in sub.choices.values():
        s.add_argument("--set", action="append", default=[], help="ghi đè config, vd. coalesce_seconds=5")
    a = ap.parse_args()
    cfg = load_config(sets=a.set)

    if a.cmd == "export":
        print(f"{write_jsonl(a.out, export(load_dataset(a.data), cfg, a.k))} lượt → {a.out}")
    elif a.cmd == "derive":
        print(f"{write_jsonl(a.out, derive(load_dataset(a.data), cfg))} lượt → {a.out}")
    elif a.cmd == "import":
        rows, conflicts = merge(derive(load_dataset(a.data), cfg), [read_jsonl(f) for f in a.files],
                                [Path(f).stem for f in a.files], a.adjudicator)
        write_jsonl(a.out, rows)
        cpath = Path(a.out).with_name("conflicts.jsonl")
        write_jsonl(cpath, conflicts)
        print(f"{len(rows)} lượt → {a.out}; {len(conflicts)} bất đồng → {cpath}")
    elif a.cmd == "agree":
        r = kappa(read_jsonl(a.a), read_jsonl(a.b))
        print(json.dumps({k: v for k, v in r.items() if k != "disagree"}, ensure_ascii=False))
        for t in r["disagree"]:
            print("  ≠", t)
        if r["kappa"] is not None and r["kappa"] < 0.7:
            print("kappa < 0.7 → sửa data/LABELING.md trước khi gán tiếp")
    elif a.cmd == "stats":
        sp = resolve_dir(a.data) / "split.json" if a.data else None
        split = json.loads(sp.read_text(encoding="utf-8")) if sp and sp.exists() else None
        print(json.dumps(stats(read_jsonl(a.labels), split), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
