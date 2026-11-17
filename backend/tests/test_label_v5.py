"""Unit test v5 mục 12: detect.label (derive / import / agree) và detect.anonymize. Không mạng."""
from __future__ import annotations

import json

import pytest

from detect.anonymize import anonymize
from detect.config import Config
from detect.dataset import load_dataset
from detect.label import derive, export, kappa, merge, stats
from detect.timeexpr import parse


@pytest.fixture(scope="module")
def sample():
    return load_dataset("sample")


# ---------------- derive / export ----------------

def test_derive_labels_supporting_and_hard(sample):
    rows = derive(sample, Config())
    by_id = {i: r for r in rows for i in r["new_event_ids"]}
    assert by_id["m003"]["label"] == 1 and by_id["m003"]["kinds"] == ["assign"]  # anchor g01
    assert by_id["m004"]["label"] == 1                                         # "Dạ ok anh" (supporting)
    assert by_id["m011"]["label"] == 0 and by_id["m011"].get("hard")
    assert by_id["m001"]["label"] == 0 and not by_id["m001"].get("hard")
    assert len({r["turn_id"] for r in rows}) == len(rows)


def test_export_matches_derive_turns(sample):
    ex, de = export(sample, Config(), 5), derive(sample, Config())
    assert [r["turn_id"] for r in ex] == [r["turn_id"] for r in de]
    m4 = next(r for r in ex if "m004" in r["new_event_ids"])  # v6: m003 + m004 cùng một lượt (đợi hội thoại yên)
    assert m4["has_reply"] and "m003" in m4["new_event_ids"] and m4["label"] is None


# ---------------- import ----------------

def _d(tid, label, **kw):
    return {"turn_id": tid, "conversation_key": "c", "new_event_ids": [tid], "label": label, "kinds": [], **kw}


def test_merge_adjudicates_conflicts():
    derived = [_d("t1", 0), _d("t2", 1), _d("t3", 0, hard=True), _d("t4", 0)]
    a = [_d("t1", 1, annotator="A"), _d("t2", 1, annotator="A"), _d("t3", 1, annotator="A")]
    b = [_d("t1", 1, annotator="B"), _d("t2", 0, annotator="B"), _d("t3", 0, annotator="B")]
    rows, conf = merge(derived, [a, b], ["a", "b"], adjudicator="B")
    lab = {r["turn_id"]: r["label"] for r in rows}
    assert lab == {"t1": 1, "t2": 0, "t3": 0, "t4": 0}     # đồng ý → nhãn tay; bất đồng → B; không ai gán → suy
    assert {c["turn_id"] for c in conf} == {"t2", "t3"}
    rows, _ = merge(derived, [a, b], ["a", "b"])            # không có người phân xử → nhãn suy
    assert {r["turn_id"]: r["label"] for r in rows}["t2"] == 1
    assert next(r for r in rows if r["turn_id"] == "t3").get("hard")


def test_merge_rejects_unknown_turn():
    with pytest.raises(SystemExit):
        merge([_d("t1", 0)], [[_d("zz", 1)]], ["a"])


# ---------------- agree / stats ----------------

def test_kappa_small_example():
    # 10 lượt chung: bảng 11=4, 00=4, 10=1, 01=1 → po=0.8, pe=0.5 → kappa=0.6
    a = [_d(f"t{i}", l) for i, l in enumerate([1, 1, 1, 1, 0, 0, 0, 0, 1, 0])] + [_d("only_a", 1)]
    b = [_d(f"t{i}", l) for i, l in enumerate([1, 1, 1, 1, 0, 0, 0, 0, 0, 1])]
    r = kappa(a, b)
    assert r["n"] == 10 and r["kappa"] == pytest.approx(0.6) and r["disagree"] == ["t8", "t9"]
    assert kappa(a, a)["kappa"] == 1.0


def test_stats_by_split():
    rows = [_d("t1", 1, kinds=["due"]), _d("t2", 0, hard=True), {**_d("t3", 1), "conversation_key": "d"}]
    s = stats(rows, {"train": ["c"], "test": ["d"]})
    assert s["all"]["positive"] == 2 and s["all"]["hard_negative"] == 1
    assert s["train"]["turns"] == 2 and s["test"]["positive"] == 1 and s["all"]["kinds"] == {"due": 1}


# ---------------- anonymize ----------------

RAW_MEMBERS = {"ch_x": [{"user_id": "29:1abcDEF", "display_name": "Hưng", "aliases": ["anh Hưng", "Hung"]},
                        {"user_id": "29:9xyzQRS", "display_name": "Thuỷ", "aliases": ["Thuỷ Lê"]}]}
RAW_MSGS = [
    {"id": "a1", "conversation_id": "ch_x", "conversation_type": "group", "created_at": "2025-03-06T09:00:00+07:00",
     "author_id": "29:1abcDEF", "author_name": "Hưng Trịnh Văn", "text": "@Thuỷ gửi https://x.com/a cho anh Hưng, mai nhé Thuỷ Lê",
     "mentions": [{"user_id": "29:9xyzQRS", "name": "Thuỷ"}], "reactions": [], "attachments": ["luong_q1.xlsx"]},
    {"id": "a2", "conversation_id": "ch_x", "conversation_type": "group", "created_at": "2025-03-06T09:30:00+07:00",
     "author_id": "29:9xyzQRS", "author_name": "Thuỷ", "text": "ok Hung, thuỷ làm luôn",
     "mentions": [], "reply_to_id": "a1", "reactions": [{"user_id": "29:1abcDEF", "type": "like", "created_at": "2025-03-06T09:31:00+07:00"}]},
]


def test_anonymize_removes_ids_and_names():
    out, mem, mp = anonymize(RAW_MSGS, RAW_MEMBERS, "s1")
    dump = json.dumps([out, mem], ensure_ascii=False)
    for s in ("29:1abcDEF", "29:9xyzQRS", "Hưng", "Hung", "Thuỷ", "thuỷ", "Trịnh", "luong_q1", "x.com"):
        assert s not in dump, s
    fh, ft = mp["fake_by_user"]["29:1abcDEF"], mp["fake_by_user"]["29:9xyzQRS"]
    assert {fh, ft} <= {"Mai", "Nam"}                       # pool bắt đầu bằng tên trùng từ thời gian
    m1, m2 = out
    assert m1["author_name"] == fh and m1["mentions"] == [{"user_id": mp["user_ids"]["29:9xyzQRS"], "name": ft}]
    assert m1["text"].startswith(f"@{ft} gửi [link] cho anh {fh}, mai nhé {ft} ")  # "mai" thường giữ nguyên
    assert m2["text"].startswith(f"ok {fh.replace('ư', 'u')}") and m2["reactions"][0]["user_id"] == mp["user_ids"]["29:1abcDEF"]
    assert m1["attachments"] == ["file"]
    assert mem["ch_x"][0]["aliases"] == [f"anh {fh}", fh]   # "Hung" → tên giả bỏ dấu (Mai/Nam không dấu)


def test_anonymize_keeps_gaps_and_weekday():
    out, _, mp = anonymize(RAW_MSGS, RAW_MEMBERS, "s1")
    a, b = (parse(m["created_at"]) for m in out)
    assert (b - a).total_seconds() == 1800 and mp["time_shift_days"] % 7 == 0
    assert a.weekday() == parse(RAW_MSGS[0]["created_at"]).weekday() and a.hour == 9
    assert (parse(out[1]["reactions"][0]["created_at"]) - b).total_seconds() == 60


# ---------------- build_eval ----------------

SRC = """conv ch_t group dev
member hung Hưng | anh Hưng
member linh Linh
gold bc | Gửi báo cáo | assigned | linh | 2026-11-19 | active | single_message
day 2026-11-16
09:00 hung: chào
09:05 hung [a]: @Linh gửi báo cáo trước thứ 5 nhé   ## bc:assign bc:due
09:06:30 +linh like a
09:06:40 linh ^a: dạ em gửi trước thứ 5   ## bc:confirm
09:07 linh ^a: để em xem   ## hn:defer
09:20 hung [d]: lùi sang thứ 6 nhé   ## bc:due
act 09:10 linh approve bc
act 09:25 linh approve_change d
"""


def test_build_eval_parses_source(tmp_path):
    from detect.build_eval import build
    (tmp_path / "a.txt").write_text(SRC, encoding="utf-8")
    d, split, roles = build(tmp_path)
    assert split["dev"] == ["ch_t"]
    m = {x["id"]: x for x in d["messages"]}
    assert m["ch_t_002"]["mentions"] == [{"user_id": "u_linh", "name": "Linh"}]
    assert m["ch_t_003"]["reply_to_id"] == "ch_t_002" and m["ch_t_002"]["reactions"][0]["user_id"] == "u_linh"
    g = d["gold"][0]
    assert g["anchor_message_id"] == "ch_t_002" and g["supporting_message_ids"] == ["ch_t_002", "ch_t_005"]
    assert g["confirm_message_ids"] == ["ch_t_003"]  # "dạ" của người đã được giao: không thuộc supporting
    assert d["hn"] == [("ch_t_004", "defer")] and roles["ch_t_002"] == ["assign", "due"]
    assert [a["message_id"] for a in d["acts"]] == ["ch_t_002", "ch_t_005"]


def test_build_eval_rejects_unknown_mention(tmp_path):
    from detect.build_eval import build
    (tmp_path / "a.txt").write_text(SRC.replace("@Linh", "@Lan"), encoding="utf-8")
    with pytest.raises(SystemExit):
        build(tmp_path)


def test_build_eval_rejects_labeled_reaction(tmp_path):
    from detect.build_eval import build
    (tmp_path / "a.txt").write_text(SRC.replace("09:06:30 +linh like a\n", "09:06:30 +linh like a   ## bc:accept\n"), encoding="utf-8")
    with pytest.raises(SystemExit):
        build(tmp_path)


def test_build_eval_requires_hn_tag(tmp_path):
    from detect.build_eval import build
    (tmp_path / "a.txt").write_text(SRC.replace("## hn:defer", "## hn"), encoding="utf-8")
    with pytest.raises(SystemExit):
        build(tmp_path)


def test_derive_confirm_only_turn_is_negative(tmp_path):
    from detect.build_eval import build
    from detect.models import GoldTask
    (tmp_path / "a.txt").write_text(SRC, encoding="utf-8")
    d, _, _ = build(tmp_path)
    from detect.models import Member, Message
    ds = dict(name="t", messages=[Message(**m) for m in d["messages"]],
              members={k: [Member(**x) for x in v] for k, v in d["members"].items()},
              gold=[GoldTask(**g) for g in d["gold"]], hard_negatives=[i for i, _ in d["hn"]],
              hard_negative_tags=dict(d["hn"]), user_actions=[], turn_labels=[])
    rows = {i: r for r in derive(ds, Config()) for i in r["new_event_ids"]}
    assert rows["ch_t_003"]["label"] == 0 and rows["ch_t_003"]["kinds"] == ["confirm"]
    assert rows["ch_t_004"]["hard"] and rows["ch_t_004"]["hn_tags"] == ["defer"]
