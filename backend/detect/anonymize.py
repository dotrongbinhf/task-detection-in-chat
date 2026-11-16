"""Ẩn danh chat thật trước khi gán nhãn (tài liệu v5 mục 6.4).

    python -m detect.anonymize --in raw/messages.jsonl --members raw/members.json --out data/eval/ --map-out ../private/map.json

- user_id → u_<hash có salt>; tên hiển thị + alias → tên giả giữ kiểu (một từ Việt viết hoa; pool có Mai, Nam để giữ ca name_collision).
- Tên trong text, author_name, mentions thay theo cùng bảng; URL → [link]; file đính kèm → "file".
- created_at (tin + reaction) dịch về mốc giả, **bội số nguyên tuần** để giữ thứ trong tuần và giờ (hạn "thứ 5" vẫn đúng).
- Bảng ánh xạ ghi ra --map-out, phải nằm NGOÀI repo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any

from .config import ROOT
from .text import strip_diacritics
from .timeexpr import parse, to_iso

FAKE_NAMES = ["Mai", "Nam", "An", "Bảo", "Chi", "Dũng", "Giang", "Hà", "Hiếu", "Hoa", "Huy", "Khang", "Khoa", "Lan", "Long",
              "Minh", "My", "Ngọc", "Nhi", "Phong", "Phúc", "Quân", "Quỳnh", "Sơn", "Tâm", "Thảo", "Thư", "Trang", "Trí", "Vy",
              "Yến", "Đạt", "Hạnh", "Kiên", "Loan", "Tùng", "Uyên", "Vinh", "Xuân", "Duy"]
FAKE_SURNAMES = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Vũ", "Đặng", "Bùi", "Đỗ", "Hồ", "Ngô", "Dương"]
# Tên trùng từ thường: chữ thường thì giữ (để "mai" = ngày mai không bị đổi). shortcut: danh sách tay, mở rộng khi gặp tên mới.
COMMON_WORDS = {"mai", "nam", "an", "hà", "hoa", "xuân", "thu", "đông", "hạ", "sơn", "hải", "minh", "bình", "tâm", "lan",
                "long", "phúc", "vinh", "khang", "trang", "my", "chi", "giang", "ngọc"}
URL_RE = re.compile(r"https?://\S+|www\.\S+")
BASE = "2026-11-09T08:00:00+07:00"  # thứ 2


def _fake_alias(alias: str, real: str, fake: str, surnames: dict[str, str]) -> str:
    """'anh Hưng' → 'anh <fake>'; 'Hung' → bỏ dấu của fake; 'Linh Nguyễn' → '<fake> <họ giả>'."""
    out = []
    for w in alias.split():
        if w == real:
            out.append(fake)
        elif w == strip_diacritics(real):
            out.append(strip_diacritics(fake))
        elif w[:1].isupper():
            s = surnames.setdefault(strip_diacritics(w).lower(), FAKE_SURNAMES[len(surnames) % len(FAKE_SURNAMES)])
            out.append(s if w != strip_diacritics(w) else strip_diacritics(s))
        else:
            out.append(w)
    return " ".join(out)


def build_map(members: dict[str, list[dict[str, Any]]], salt: str) -> dict[str, Any]:
    users: dict[str, dict[str, Any]] = {}
    for lst in members.values():
        for m in lst:
            users.setdefault(m["user_id"], m)
    rng = random.Random(salt)
    pool = FAKE_NAMES[:2] + rng.sample(FAKE_NAMES[2:], len(FAKE_NAMES) - 2)
    ids, names, surnames, by_uid = {}, {}, {}, {}
    for i, (uid, m) in enumerate(users.items()):
        ids[uid] = "u_" + hashlib.sha256((salt + uid).encode()).hexdigest()[:8]
        fake = pool[i] if i < len(pool) else f"{pool[i % len(pool)]}{i // len(pool) + 1}"
        real = m["display_name"]
        names[real] = by_uid[uid] = fake
        for a in m.get("aliases", []):
            names[a] = _fake_alias(a, real, fake, surnames)
        names.setdefault(strip_diacritics(real), strip_diacritics(fake))
    return {"user_ids": ids, "names": names, "fake_by_user": by_uid}


def _name_re(names: dict[str, str]) -> re.Pattern[str]:
    alts = sorted(names, key=len, reverse=True)
    return re.compile(r"(?<!\w)(" + "|".join(re.escape(n) for n in alts) + r")(?!\w)", re.IGNORECASE)


def scrub(text: str, names: dict[str, str], rx: re.Pattern[str]) -> str:
    lower = {k.lower(): v for k, v in names.items()}

    def sub(m: re.Match[str]) -> str:
        s = m.group(0)
        if s in names:
            return names[s]
        if s.islower() and s in COMMON_WORDS:
            return s
        return lower[s.lower()]
    return rx.sub(sub, URL_RE.sub("[link]", text))


def anonymize(messages: list[dict[str, Any]], members: dict[str, list[dict[str, Any]]], salt: str
              ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    mp = build_map(members, salt)
    ids, names = mp["user_ids"], mp["names"]
    rx = _name_re(names)
    uid = lambda u: ids.get(u) or ids.setdefault(u, "u_" + hashlib.sha256((salt + u).encode()).hexdigest()[:8])  # noqa: E731
    first = min((parse(m["created_at"]) for m in messages), default=parse(BASE))
    shift = timedelta(weeks=round((parse(BASE) - first) / timedelta(weeks=1)))
    sh = lambda iso: to_iso(parse(iso) + shift)  # noqa: E731
    # tên người theo user_id (không scrub chuỗi tên gốc: họ tên đầy đủ có thể không nằm trong alias)
    nm = lambda u, s: mp["fake_by_user"].get(u) or scrub(s, names, rx)  # noqa: E731
    out = []
    for m in messages:
        out.append({**m, "created_at": sh(m["created_at"]), "author_id": uid(m["author_id"]),
                    "author_name": nm(m["author_id"], m["author_name"]), "text": scrub(m["text"], names, rx),
                    "mentions": [{"user_id": uid(x["user_id"]), "name": nm(x["user_id"], x["name"])} for x in m.get("mentions", [])],
                    "reactions": [{**r, "user_id": uid(r["user_id"]), "created_at": sh(r["created_at"])} for r in m.get("reactions", [])],
                    "attachments": ["file" for _ in m.get("attachments", [])]})
    mem = {k: [{"user_id": uid(x["user_id"]), "display_name": names[x["display_name"]],
                "aliases": [names[a] for a in x.get("aliases", [])]} for x in v] for k, v in members.items()}
    return out, mem, {**mp, "salt": salt, "time_shift_days": shift.days}


def main() -> None:
    ap = argparse.ArgumentParser(description="Ẩn danh messages.jsonl + members.json (v5 mục 6.4)")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--members", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--map-out", required=True, help="bảng ánh xạ — phải nằm ngoài repo")
    ap.add_argument("--salt", default=None, help="mặc định ngẫu nhiên; truyền lại salt cũ để ẩn danh đợt sau nhất quán")
    a = ap.parse_args()
    map_out = Path(a.map_out).resolve()
    if map_out.is_relative_to(ROOT.parent.resolve()):
        raise SystemExit(f"--map-out phải nằm ngoài repo: {map_out}")
    msgs = [json.loads(l) for l in Path(a.inp).read_text(encoding="utf-8").splitlines() if l.strip()]
    members = json.loads(Path(a.members).read_text(encoding="utf-8"))
    out, mem, mp = anonymize(msgs, members, a.salt or secrets.token_hex(8))
    d = Path(a.out)
    d.mkdir(parents=True, exist_ok=True)
    (d / "messages.jsonl").write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in out), encoding="utf-8")
    (d / "members.json").write_text(json.dumps(mem, ensure_ascii=False, indent=2), encoding="utf-8")
    map_out.parent.mkdir(parents=True, exist_ok=True)
    map_out.write_text(json.dumps(mp, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(out)} tin, {len(mp['user_ids'])} người → {d}; bảng ánh xạ → {map_out}")


if __name__ == "__main__":
    main()
