"""Dựng data/eval/ từ chat viết tay dạng nguồn gọn (data/eval_src/*.txt). v5 mục 6.1.

    python -m detect.build_eval --src data/eval_src --out data/eval

Định dạng một file = một hội thoại:

    conv ch_backend group dev            # id, group|dm, split (dev|test)
    member hung Hưng | anh Hưng, Hung    # khoá ngắn, tên hiển thị | alias
    gold bc | Gửi báo cáo tuần | assigned | linh | 2026-11-19 | active | single_message,relative_deadline
                                         # khoá | tiêu đề | kind | người nhận (a,b hoặc -) | ngày|event|none | active|cancelled | tags
    day 2026-11-16
    09:05 hung: Chào mọi người
    09:06 hung [x1]: @Linh gửi báo cáo tuần trước thứ 5 nhé   ## bc:assign
    09:07 linh ^x1: Dạ ok anh                                ## bc:confirm
    09:07:30 +nam like x1
    09:08 linh: để em xem đã                                 ## hn:defer
    act 09:20 linh approve bc

Reaction `+người loại [nhãn]` (bỏ nhãn = tin ngay trước); reaction KHÔNG được mang nhãn (v6: reaction không đổi sổ cái).
`act ... <đích>`: đích = nhãn tin `[x]` (dùng cho approve_change) hoặc khoá gold (= tin anchor). Dòng bắt đầu `#` = chú thích.

`[nhãn]` đặt tên tin; `^nhãn` = reply. `@Tên` khớp tên hiển thị → mentions. Sau `##`: `<gold>:<kind>` (kind ∈ label.KINDS)
và/hoặc `hn:<tag>` (tag ∈ HN_TAGS). `confirm` = tin xác nhận không đổi sổ cái ("dạ" khi task đã giao đúng người đó) — không vào
`supporting_message_ids` mà vào `confirm_message_ids`; `accept` chỉ dùng khi tin đó LÀM ĐỔI người nhận (v6 mục 10.4).
Nhãn lượt của người viết ghi ra `labels_author.jsonl`, rồi gộp như người gán thứ nhất (`label.merge`).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .config import load_config
from .dataset import load_dataset
from .label import HN_TAGS, KINDS, data_stats, derive, merge, stats, write_jsonl

LINE = re.compile(r"^(\d\d:\d\d(?::\d\d)?)\s+(.*)$")


def parse_file(path: Path) -> dict[str, Any]:
    conv: dict[str, Any] = {"members": {}, "gold": {}, "msgs": [], "acts": [], "hn": [], "roles": {}}
    labels: dict[str, str] = {}
    day = None
    n = 0

    def err(msg: str) -> SystemExit:
        return SystemExit(f"{path.name}:{ln}: {msg}")

    for ln, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        head, _, rest = line.partition(" ")
        if head == "conv":
            conv["id"], conv["type"], conv["split"] = rest.split()
        elif head == "member":
            key, _, names = rest.partition(" ")
            disp, _, al = names.partition("|")
            conv["members"][key] = {"user_id": f"u_{key}", "display_name": disp.strip(),
                                    "aliases": [a.strip() for a in al.split(",") if a.strip()]}
        elif head == "gold":
            f = [x.strip() for x in rest.split("|")]
            if len(f) != 7:
                raise err("gold cần 7 trường")
            conv["gold"][f[0]] = dict(title=f[1], kind=f[2], assignees=[] if f[3] == "-" else f[3].split(","),
                                      due=f[4], status=f[5], tags=[t for t in f[6].split(",") if t])
        elif head == "day":
            day = rest.strip()
        elif head == "act":
            t, user, action, gkey = rest.split()
            conv["acts"].append((f"{day}T{t if len(t) > 5 else t + ':00'}+07:00", user, action, gkey))
        else:
            m = LINE.match(line)
            if not m or not day:
                raise err(f"không hiểu dòng: {raw}")
            t, body = m.groups()
            at = f"{day}T{t if len(t) > 5 else t + ':00'}+07:00"
            body, _, ann = body.partition("##")
            notes = ann.split()
            if body.startswith("+"):  # reaction
                p = body[1:].split()
                user, typ = p[0], p[1]
                if notes:
                    raise err("reaction không được mang nhãn (v6 mục 10.4: reaction không làm đổi sổ cái)")
                tgt = labels[p[2]] if len(p) > 2 else conv["msgs"][-1]["id"]
                msg = next(x for x in conv["msgs"] if x["id"] == tgt)
                eid = f"{tgt}#r{len(msg['reactions']) + 1}"
                msg["reactions"].append({"user_id": f"u_{user}", "type": typ, "created_at": at})
            else:
                who, _, text = body.partition(":")
                parts = who.split()
                user = parts[0]
                if user not in conv["members"]:
                    raise err(f"không có thành viên {user}")
                n += 1
                eid = f"{conv['id']}_{n:03d}"
                reply = None
                for p in parts[1:]:
                    if p.startswith("[") and p.endswith("]"):
                        labels[p[1:-1]] = eid
                    elif p.startswith("^"):
                        if p[1:] not in labels:
                            raise err(f"không có nhãn tin {p}")
                        reply = labels[p[1:]]
                text = text.strip()
                by_name = {v["display_name"]: v for v in conv["members"].values()}
                mentions = []
                for name in re.findall(r"@(\w+)", text):
                    if name not in by_name:
                        raise err(f"@{name} không phải thành viên")
                    mentions.append({"user_id": by_name[name]["user_id"], "name": name})
                conv["msgs"].append({"id": eid, "conversation_id": conv["id"], "conversation_type": conv["type"],
                                     "thread_root_id": None, "created_at": at, "author_id": f"u_{user}",
                                     "author_name": conv["members"][user]["display_name"], "text": text,
                                     "mentions": mentions, "reply_to_id": reply, "reactions": []})
            for x in notes:
                if x.startswith("hn"):
                    tag = x.partition(":")[2]
                    if tag not in HN_TAGS:
                        raise err(f"hard negative cần tag ∈ {', '.join(HN_TAGS)}: {x}")
                    conv["hn"].append((eid, tag))
                    continue
                g, _, role = x.partition(":")
                if g not in conv["gold"] or role not in KINDS:
                    raise err(f"nhãn sai: {x}")
                conv["roles"].setdefault(eid, []).append((g, role))
    conv["labels"] = labels
    return conv


def build(src: Path) -> tuple[dict[str, Any], dict[str, list[str]], dict[str, list[str]]]:
    msgs, members, gold, hn, acts, split = [], {}, [], [], [], {"dev": [], "test": []}
    roles: dict[str, list[str]] = {}
    for path in sorted(src.glob("*.txt")):
        c = parse_file(path)
        cid = c["id"]
        split[c["split"]].append(cid)
        members[cid] = list(c["members"].values())
        msgs += c["msgs"]
        hn += c["hn"]
        sup: dict[str, list[str]] = {}
        conf: dict[str, list[str]] = {}
        for eid, rs in c["roles"].items():
            for g, role in rs:
                (conf if role == "confirm" else sup).setdefault(g, []).append(eid)
                roles.setdefault(eid, []).append(role)
        for g, d in c["gold"].items():
            if g not in sup:
                raise SystemExit(f"{path.name}: nhãn {g} không có tin nào")
            pos = {m["id"]: k for k, m in enumerate(c["msgs"])}
            ids = sorted(set(sup[g]), key=lambda i: (pos[i.split("#")[0]], i))  # tin trước reaction của nó
            due_type = d["due"] if d["due"] in ("event", "none") else "calendar"
            gold.append({"gold_id": f"{cid}:{g}", "conversation_key": cid, "anchor_message_id": ids[0],
                         "supporting_message_ids": ids, "title": d["title"], "kind": d["kind"],
                         "assignee_ids": [f"u_{a}" for a in d["assignees"]], "due_type": due_type,
                         "due_date": d["due"] if due_type == "calendar" else None, "final_status": d["status"],
                         "scenario_tags": d["tags"], "confirm_message_ids": sorted(conf.get(g, []), key=lambda i: pos[i])})
        anchor = {g["gold_id"].split(":", 1)[1]: g["anchor_message_id"] for g in gold if g["conversation_key"] == cid}
        for at, user, action, g in c["acts"]:
            mid = c["labels"].get(g) or anchor.get(g)
            if not mid:
                raise SystemExit(f"{path.name}: act trỏ tới {g} không có")
            acts.append({"at": at, "user_id": f"u_{user}", "message_id": mid, "action": action})
    return {"messages": msgs, "members": members, "gold": gold, "hn": hn, "acts": acts}, split, roles


def main() -> None:
    ap = argparse.ArgumentParser(description="Dựng data/eval từ data/eval_src")
    ap.add_argument("--src", default="data/eval_src")
    ap.add_argument("--out", default="data/eval")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    d, split, roles = build(Path(a.src))
    write_jsonl(out / "messages.jsonl", sorted(d["messages"], key=lambda m: (m["created_at"], m["id"])))
    (out / "members.json").write_text(json.dumps(d["members"], ensure_ascii=False, indent=1), encoding="utf-8")
    write_jsonl(out / "gold.jsonl", d["gold"])
    (out / "hard_negatives.txt").write_text("".join(f"{i}\t{t}\n" for i, t in d["hn"]), encoding="utf-8")
    write_jsonl(out / "user_actions.jsonl", sorted(d["acts"], key=lambda x: x["at"]))
    (out / "split.json").write_text(json.dumps(split, ensure_ascii=False, indent=1), encoding="utf-8")

    cfg = load_config()
    derived = derive(load_dataset(str(out)), cfg)
    author = [{**r, "kinds": [k for k in KINDS if any(k in roles.get(i, []) for i in r["new_event_ids"])],
               "annotator": "author", "note": ""} for r in derived]
    write_jsonl(out / "labels_author.jsonl", author)
    rows, conflicts = merge(derived, [author], ["author"], "author")
    write_jsonl(out / "turn_labels.jsonl", rows)
    if conflicts:
        raise SystemExit(f"nhãn tác giả lệch nhãn suy: {conflicts[:3]}")
    s = stats(rows, split)
    print(json.dumps(s, ensure_ascii=False))
    print(json.dumps(data_stats(load_dataset(str(out)), split), ensure_ascii=False, indent=1))
    print(f"{len(d['messages'])} tin, {len(d['gold'])} nhãn ({sum(1 for g in d['gold'] if g['assignee_ids'])} có người nhận) → {out}")


if __name__ == "__main__":
    main()
