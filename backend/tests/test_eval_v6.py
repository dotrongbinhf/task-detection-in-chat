"""Eval v6 (mục 11): nháp đúng hoàn toàn, ghép hai nhãn chung anchor, lượt báo nhầm, bootstrap tất định."""
from __future__ import annotations

from detect.eval import bootstrap, evaluate
from detect.models import EngineState, Evidence, GoldTask, Member, Message, StreamEvent, Task

AT = "2026-11-09T09:00:00+07:00"


def _msg(i: str, who: str = "u_a", text: str = "x") -> StreamEvent:
    m = Message(id=i, conversation_id="c", created_at=AT, author_id=who, author_name=who, text=text)
    return StreamEvent(kind="message", id=i, key="c", at=AT, message=m)


def _task(tid: str, anchor: str, title: str, who: list[str], due: str | None = None, status: str = "draft") -> Task:
    return Task(task_id=tid, conversation_key="c", status=status, kind="assigned", title=title, assignee_ids=who,
                due_type="calendar" if due else "none", due_date=due, anchor_message_id=anchor,
                evidence=[Evidence(message_id=anchor, role="assign", added_at=AT)], created_at=AT, created_turn=1,
                last_touched_at=AT, status_changed_at=AT, shown_at=AT, prompt_version="t", model="t")


def _gold(gid: str, anchor: str, title: str, who: list[str], due: str | None = None) -> GoldTask:
    return GoldTask(gold_id=gid, conversation_key="c", anchor_message_id=anchor, supporting_message_ids=[anchor], title=title,
                    kind="assigned", assignee_ids=who, due_type="calendar" if due else "none", due_date=due)


def _state(tasks, gold, turns=(), labels=()) -> EngineState:
    ids = {t.anchor_message_id for t in tasks} | {g.anchor_message_id for g in gold} | {i for tr in turns for i in tr["new_event_ids"]}
    return EngineState(dataset="t", members={"c": [Member(user_id="u_a", display_name="A")]}, events=[_msg(i) for i in sorted(ids)],
                       simNow=AT, tasks=list(tasks), gold=list(gold), turns=list(turns), turnLabels=list(labels))


def test_strict_correct_needs_assignee_and_due():
    gold = [_gold("g1", "m1", "Gửi báo cáo", ["u_a"], "2026-11-12"), _gold("g2", "m2", "Sửa lỗi", ["u_b"], "2026-11-12")]
    tasks = [_task("K1", "m1", "Gửi báo cáo", ["u_a"], "2026-11-12"), _task("K2", "m2", "Sửa lỗi", ["u_b"], "2026-11-13")]
    s = evaluate(_state(tasks, gold), {})["summary"]
    assert s["recall"]["k"] == 2 and s["strict_correct"]["k"] == 1 and s["strict_ids"] == ["g1"]


def test_two_labels_sharing_anchor_are_not_swapped():
    # "Trâm export data, Đức kiểm tra số liệu" — một tin, hai việc
    gold = [_gold("g_exp", "m1", "Export data tháng 10", ["u_tram"]), _gold("g_chk", "m1", "Kiểm tra số liệu GMV", ["u_duc"])]
    tasks = [_task("K1", "m1", "Kiểm tra số liệu GMV", ["u_duc"]), _task("K2", "m1", "Export data tháng 10", ["u_tram"])]
    rep = evaluate(_state(tasks, gold), {})
    assert {m["gold_id"]: m["task_id"] for m in rep["matches"]} == {"g_exp": "K2", "g_chk": "K1"}
    assert rep["summary"]["strict_correct"]["k"] == 2


def _turn(n: int, ids: list[str], ops: list[dict]) -> dict:
    return {"turn": n, "key": "c", "at": AT, "new_event_ids": ids, "ops": ops, "events": [], "context": None}


def test_false_alarm_and_missed_turns():
    create = {"op": "CREATE", "accepted": True, "task_id": "K9", "fields": []}
    evidence_only = {"op": "UPDATE", "accepted": True, "task_id": "K9", "fields": []}
    turns = [_turn(1, ["m1"], [create]), _turn(2, ["m2"], [evidence_only]), _turn(3, ["m3"], []),
             _turn(4, ["m4"], [{"op": "UPDATE", "accepted": True, "task_id": "K9", "fields": ["due"]}]),
             _turn(5, ["m5"], [create])]  # giao việc kèm hạn = một CREATE, đúng loại
    labels = [{"conversation_key": "c", "new_event_ids": ["m1"], "label": 0, "kinds": []},
              {"conversation_key": "c", "new_event_ids": ["m2"], "label": 0, "kinds": []},
              {"conversation_key": "c", "new_event_ids": ["m3"], "label": 1, "kinds": ["assign"]},
              {"conversation_key": "c", "new_event_ids": ["m5"], "label": 1, "kinds": ["assign", "due"]},
              {"conversation_key": "c", "new_event_ids": ["m4"], "label": 1, "kinds": ["due"]}]
    rep = evaluate(_state([], [], turns, labels), {})
    to = rep["turn_ops"]
    assert rep["summary"]["false_alarm_turns"]["k"] == 1 and rep["summary"]["false_alarm_turns"]["n"] == 2  # bằng chứng không tính
    assert to["missed"]["k"] == 1 and to["type_correct"]["k"] == 2 and to["type_correct"]["n"] == 2


def test_bootstrap_is_deterministic():
    by = {"a": (3, 5), "b": (1, 4), "c": (4, 4)}
    x, y = bootstrap(by, seed=3), bootstrap(by, seed=3)
    assert x == y and x["k"] == 8 and x["n"] == 13 and x["lo"] <= x["value"] <= x["hi"]


def test_confirm_message_counts_as_valid_evidence():
    t = _task("K1", "m1", "Gửi báo cáo", ["u_a"])
    t.evidence.append(Evidence(message_id="m2", role="accept", added_at=AT))  # "Dạ ok anh"
    g = _gold("g", "m1", "Gửi báo cáo", ["u_a"]).model_copy(update={"confirm_message_ids": ["m2"]})
    s = evaluate(_state([t], [g]), {})["summary"]
    assert s["ev_precision"] == 1.0 and s["ev_recall"] == 1.0
