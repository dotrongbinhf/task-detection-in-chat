"""Unit test mục 9 với FakeLLM (không gọi mạng). Trong FakeLLM: "@<message_id>" → #n, "@<task_id>" → T<n>."""
from __future__ import annotations

import json

import pytest

from detect.config import Config
from detect.context import build_context
from detect.dataset import load_dataset, load_prompts
from detect.extract import resolve_assignees
from detect.llm import FakeLLM, HeuristicLLM
from detect.models import Member, Message, UserAction, empty_op
from detect.run import catch_up, flush_actions, init_state, next_batch, process_turn, ui_user_action
from detect.timeexpr import resolve_due

PROMPTS = load_prompts()


@pytest.fixture(autouse=True, params=["scored", "linked_recent_search"])
def context_mode(request):
    """Mọi test v3 chạy ở cả hai chế độ: `scored` phải giữ nguyên hành vi v3; `linked_recent_search` không được
    làm hỏng luật sổ cái / Validate / hàng đợi (đổi giá trị mặc định của Config trong lúc chạy test)."""
    old = Config.model_fields["context_mode"].default
    Config.model_fields["context_mode"].default = request.param
    Config.model_rebuild(force=True)
    yield request.param
    Config.model_fields["context_mode"].default = old
    Config.model_rebuild(force=True)
MEMBERS = {
    "g": [Member(user_id="u_hung", display_name="Hưng"), Member(user_id="u_linh", display_name="Linh"),
          Member(user_id="u_nam", display_name="Nam"), Member(user_id="u_nam2", display_name="Nam")],
    "dm": [Member(user_id="u_hung", display_name="Hưng"), Member(user_id="u_linh", display_name="Linh")],
}
NAMES = {"u_hung": "Hưng", "u_linh": "Linh", "u_nam": "Nam", "u_nam2": "Nam"}


def msg(i: str, t: str, who: str, text: str, conv: str = "g", **kw) -> Message:
    return Message(id=i, conversation_id=conv, conversation_type="dm" if conv == "dm" else "group",
                   created_at=f"2026-11-09T{t}+07:00", author_id=who, author_name=NAMES[who], text=text, **kw)


def state_of(messages, actions=()):
    return init_state(dict(name="t", messages=messages, members=MEMBERS, gold=None, hard_negatives=[], user_actions=list(actions)))


def run_all(state, llm, cfg=None):
    cfg = cfg or Config()
    out = []
    while (t := process_turn(state, cfg, llm, PROMPTS)) is not None:
        out.append(t)
    flush_actions(state, cfg)
    return out


def create(anchor: str, refs: list[str], kind="assigned", title="Việc A", **kw):
    return empty_op("CREATE", kind=kind, anchor=f"@{anchor}", title=title, assignee_refs=refs, due_kind="none",
                    evidence=[{"msg": f"@{anchor}", "role": "assign"}], **kw)


def update(task: str, ev: str, changes: list[tuple[str, object]]):
    return empty_op("UPDATE", target=f"@{task}", evidence=[{"msg": f"@{ev}", "role": "accept"}],
                    changes=[{"field": f, "value": json.dumps(v, ensure_ascii=False), "evidence_msg": f"@{ev}"} for f, v in changes])


# ---------------- resolve_due ----------------

@pytest.mark.parametrize("fields,ref,expected", [
    ({"due_kind": "relative_day", "due_day_offset": 1}, "2026-11-30T10:00:00+07:00", "2026-12-01"),  # qua tháng
    ({"due_kind": "relative_day", "due_day_offset": 1}, "2026-12-31T10:00:00+07:00", "2027-01-01"),  # qua năm
    ({"due_kind": "weekday", "due_weekday": 4, "due_week_offset": None}, "2026-11-09T10:00:00+07:00", "2026-11-12"),  # T2 nói thứ 5
    ({"due_kind": "weekday", "due_weekday": 4, "due_week_offset": None}, "2026-11-12T10:00:00+07:00", "2026-11-19"),  # thứ 5 nói thứ 5 → tuần sau
    ({"due_kind": "weekday", "due_weekday": 4, "due_week_offset": None}, "2026-11-13T10:00:00+07:00", "2026-11-19"),  # thứ 6 nói thứ 5
    ({"due_kind": "weekday", "due_weekday": 5, "due_week_offset": 1}, "2026-11-09T10:00:00+07:00", "2026-11-20"),
    ({"due_kind": "week_part", "due_week_part": "end", "due_week_offset": None}, "2026-11-10T10:00:00+07:00", "2026-11-13"),
    ({"due_kind": "week_part", "due_week_part": "start", "due_week_offset": 1}, "2026-11-10T10:00:00+07:00", "2026-11-16"),
    ({"due_kind": "absolute", "due_abs_day": 5, "due_abs_month": None}, "2026-11-10T10:00:00+07:00", "2026-12-05"),
    ({"due_kind": "absolute", "due_abs_day": 10, "due_abs_month": None}, "2026-11-10T10:00:00+07:00", "2026-11-10"),
    ({"due_kind": "absolute", "due_abs_day": 5, "due_abs_month": 1}, "2026-12-20T10:00:00+07:00", "2027-01-05"),
])
def test_resolve_due(fields, ref, expected):
    assert resolve_due(fields, ref).due_date == expected


def test_event_due_blocks_fabricated_date():
    r = resolve_due({"due_kind": "event", "due_weekday": 3}, "2026-11-09T10:00:00+07:00")
    assert r.due_type == "event" and r.due_date is None and "fabricated_date_blocked" in r.notes


def test_unresolvable_due():
    assert "unresolvable_due" in resolve_due({"due_kind": "weekday"}, "2026-11-09T10:00:00+07:00").notes


# ---------------- hàng đợi ----------------

def test_queue_coalesce_and_order():
    s = state_of([msg("a", "09:00:00", "u_hung", "1"), msg("b", "09:00:03", "u_hung", "2"),
                  msg("c", "09:00:20", "u_hung", "3"), msg("x", "09:00:01", "u_linh", "dm", conv="dm")])
    key, batch = next_batch(s, Config())
    assert [e.id for e in batch] == ["a", "b"]  # x thuộc key khác
    turns = run_all(s, FakeLLM())
    assert [t["new_event_ids"] for t in turns] == [["a", "b"], ["x"], ["c"]]


def test_reaction_only_triggers_when_relevant():
    old = msg("m0", "08:00", "u_hung", "tin cũ", reactions=[{"user_id": "u_linh", "type": "like", "created_at": "2026-11-09T10:00:00+07:00"}])
    filler = [msg(f"f{i}", f"09:{i:02d}", "u_nam", f"filler {i}") for i in range(12)]
    s = state_of([old, *filler])
    turns = run_all(s, FakeLLM(), Config(window_k=10))
    r = next(t for t in turns if "m0#r1" in t["new_event_ids"])
    assert r["skipped"] == "reaction_not_relevant"


# ---------------- dựng ngữ cảnh ----------------

def test_reply_chain_and_maps():
    ms = [msg("root", "08:00", "u_hung", "gốc")] + [msg(f"f{i}", f"08:{10 + i}", "u_nam", f"x{i}") for i in range(12)]
    ms.append(msg("new", "09:00", "u_linh", "trả lời", reply_to_id="root"))
    s = state_of(ms)
    cfg = Config(window_k=5)
    while True:
        key, batch = next_batch(s, cfg)
        if batch[0].id == "new":
            break
        s.processed += [e.id for e in batch]
    ctx = build_context(s, key, batch, cfg, PROMPTS["extract"], PROMPTS["gate"])
    assert "root" in ctx.reply_ids
    for local, mid in ctx.id_map.items():
        assert ctx.rev_map[mid] == local
    assert ctx.new_msgs[0].id == "new"


def test_budget_trim_order(context_mode):
    if context_mode != "scored":
        pytest.skip("thứ tự cắt của v3; chế độ linked có test riêng (test_context_v4.py::test_budget_never_cuts_linked)")
    ms = [msg(f"m{i}", f"08:{i:02d}", "u_hung", ("Linh làm việc số %d nhé " % i) * 8, mentions=[{"user_id": "u_linh", "name": "Linh"}]) for i in range(20)]
    fake = FakeLLM({f"m{i}": {"ops": [create(f"m{i}", ["u_linh"], title=f"Việc {i}")]} for i in range(20)})
    s = state_of(ms)
    turns = run_all(s, fake, Config(context_token_budget=1700, message_max_tokens=80))
    trims = [t for tr in turns for t in (tr["context"] or {}).get("trims", [])]
    assert trims, "phải có budget_trim"
    kinds = [t.split(" ")[0] for t in trims]
    order = ["drop_db_task", "drop_evidence", "drop_open_task", "reduce_k"]
    first_turn_trims = [t.split(" ")[0] for t in turns[-1]["context"]["trims"]]
    assert first_turn_trims == sorted(first_turn_trims, key=order.index)
    assert all(k in order for k in kinds)
    assert len(turns[-1]["context"]["selected"]) >= 3


# ---------------- validate ----------------

def test_validate_rules():
    ms = [msg("a", "09:00", "u_hung", "Linh làm A nhé", mentions=[{"user_id": "u_linh", "name": "Linh"}]),
          msg("b", "09:01", "u_linh", "ok anh"),
          msg("c", "09:02", "u_hung", "nhắc lại")]
    fake = FakeLLM({
        "a": {"ops": [create("a", ["u_linh"])]},
        "b": {"ops": [create("a", ["u_linh"]) | {"evidence": [{"msg": "@a", "role": "assign"}, {"msg": "@b", "role": "accept"}]},  # duplicate_anchor
                      empty_op("UPDATE", target="T9", evidence=[{"msg": "@b", "role": "accept"}], changes=[])]},  # unknown_task
        "c": {"ops": [empty_op("CREATE", kind="assigned", anchor="@a", title="x", assignee_refs=[], due_kind="none",
                               evidence=[{"msg": "@a", "role": "assign"}]),  # không trích TIN MỚI
                      update("K1", "c", [("title", "y")]) | {"changes": [{"field": "title", "value": '"y"', "evidence_msg": "#99"}]}]},
    })
    s = state_of(ms)
    turns = run_all(s, fake)
    reasons = [o["reason"] for t in turns for o in t["ops"] if not o["accepted"]]
    assert any(r.startswith("duplicate_anchor") for r in reasons)
    assert any(r.startswith("unknown_task") for r in reasons)
    assert "not_from_new_message" in reasons
    assert any(r.startswith("missing_evidence") for r in reasons)
    assert len(s.tasks) == 1


def test_duplicate_anchor_blocks_declined():
    ms = [msg("a", "09:00", "u_hung", "Linh làm A", mentions=[{"user_id": "u_linh", "name": "Linh"}]),
          msg("b", "10:00", "u_hung", "làm A đi", reply_to_id="a")]
    fake = FakeLLM({"a": {"ops": [create("a", ["u_linh"])]},
                    "b": {"ops": [create("a", ["u_linh"]) | {"evidence": [{"msg": "@a", "role": "assign"}, {"msg": "@b", "role": "content"}]}]}})
    s = state_of(ms, [UserAction(at="2026-11-09T09:30:00+07:00", user_id="u_linh", message_id="a", action="decline")])
    turns = run_all(s, fake)
    assert s.tasks[0].status == "declined"
    assert any((o["reason"] or "").startswith("duplicate_anchor") for o in turns[-1]["ops"])


# ---------------- vòng đời & quyền duyệt ----------------

def test_candidate_to_draft_and_expiry():
    ms = [msg("a", "09:00", "u_hung", "Ai làm được cái này không"), msg("b", "09:05", "u_nam", "để em làm"),
          msg("c", "09:10", "u_hung", "Cần ai viết doc")]
    fake = FakeLLM({"a": {"ops": [create("a", [], kind="open_ask")]},
                    "b": {"ops": [update("K1", "b", [("assignee_refs", ["SPEAKER:@b"])])]},
                    "c": {"ops": [create("c", [], kind="open_ask")]}})
    s = state_of(ms)
    run_all(s, fake)
    t1, t2 = s.tasks
    assert t1.status == "draft" and t1.assignee_ids == ["u_nam"] and t1.kind == "assigned"
    assert t2.status == "candidate"
    catch_up(s, "2026-11-11T09:11:00+07:00", Config())
    assert t2.status == "expired"


def test_authorization_and_multi_assignee():
    ms = [msg("a", "09:00", "u_hung", "Linh với Nam chuẩn bị slide")]
    fake = FakeLLM({"a": {"ops": [create("a", ["u_linh", "u_nam"])]}})
    s = state_of(ms)
    run_all(s, fake)
    cfg = Config()
    out = ui_user_action(s, UserAction(at="", user_id="u_hung", message_id="a", action="approve"), cfg)
    assert out[0]["type"] == "user_action_unauthorized"
    ui_user_action(s, UserAction(at="", user_id="u_linh", message_id="a", action="approve"), cfg)
    assert s.tasks[0].status == "approved"
    ui_user_action(s, UserAction(at="", user_id="u_nam", message_id="a", action="decline"), cfg)
    assert s.tasks[0].approvals == {"u_linh": "approved", "u_nam": "declined"} and s.tasks[0].status == "approved"


def test_all_decline_makes_declined():
    s = state_of([msg("a", "09:00", "u_hung", "x")])
    run_all(s, FakeLLM({"a": {"ops": [create("a", ["u_linh"])]}}))
    ui_user_action(s, UserAction(at="", user_id="u_linh", message_id="a", action="decline"), Config())
    assert s.tasks[0].status == "declined"


# ---------------- đề xuất thay đổi ----------------

def _approved_task_state(extra_msgs, fake_extra, actions=()):
    ms = [msg("a", "09:00", "u_hung", "Linh làm A thứ 5", mentions=[{"user_id": "u_linh", "name": "Linh"}]), *extra_msgs]
    fake = FakeLLM({"a": {"ops": [create("a", ["u_linh"])]}, **fake_extra})
    acts = [UserAction(at="2026-11-09T09:01:00+07:00", user_id="u_linh", message_id="a", action="approve"), *actions]
    s = state_of(ms, acts)
    run_all(s, fake)
    return s


DUE_FRI = {"due_text": "thứ 6", "due_kind": "weekday", "due_weekday": 5}
DUE_SAT = {"due_text": "thứ 7", "due_kind": "weekday", "due_weekday": 6}


def test_change_proposal_supersede_and_approve():
    s = _approved_task_state(
        [msg("b", "10:00", "u_hung", "lùi sang thứ 6"), msg("c", "10:05", "u_hung", "thôi thứ 7 nhé")],
        {"b": {"ops": [update("K1", "b", [("due", DUE_FRI)])]}, "c": {"ops": [update("K1", "c", [("due", DUE_SAT)])]}},
        [UserAction(at="2026-11-09T10:10:00+07:00", user_id="u_linh", message_id="c", action="approve_change")],
    )
    t = s.tasks[0]
    assert [c.status for c in s.changes] == ["superseded", "approved"]
    assert t.due_date == "2026-11-14"


def test_approved_task_not_modified_until_approved():
    s = _approved_task_state([msg("b", "10:00", "u_hung", "lùi sang thứ 6")], {"b": {"ops": [update("K1", "b", [("due", DUE_FRI)])]}})
    assert s.tasks[0].due_date is None and s.changes[0].status == "pending"


def test_declined_change_suppressed_and_no_op():
    s = _approved_task_state(
        [msg("b", "10:00", "u_hung", "lùi sang thứ 6"), msg("c", "11:00", "u_hung", "thứ 6 nhé"), msg("d", "11:05", "u_hung", "ok")],
        {"b": {"ops": [update("K1", "b", [("due", DUE_FRI)])]}, "c": {"ops": [update("K1", "c", [("due", DUE_FRI)])]},
         "d": {"ops": [update("K1", "d", [("title", "Việc A")])]}},
        [UserAction(at="2026-11-09T10:30:00+07:00", user_id="u_linh", message_id="b", action="decline_change")],
    )
    reasons = [e["reason"] for e in s.trace if e["type"] == "change_rejected"]
    assert "declined_change_suppressed" in reasons and "no_op_change" in reasons


def test_cancel_approved_is_proposal_and_pending_keeps_open():
    s = _approved_task_state([msg("b", "10:00", "u_hung", "thôi khỏi làm")],
                             {"b": {"ops": [empty_op("CANCEL", target="@K1", evidence=[{"msg": "@b", "role": "cancel"}])]}})
    assert s.tasks[0].status == "approved" and s.changes[0].field == "status"
    catch_up(s, "2026-12-30T10:00:00+07:00", Config())
    assert s.tasks[0].status == "approved"  # có đề xuất pending → không bị closed


def test_reassign_approved_by_new_assignee():
    s = _approved_task_state([msg("b", "10:00", "u_hung", "à thôi để Nam làm")],
                             {"b": {"ops": [update("K1", "b", [("assignee_refs", ["u_nam"])])]}})
    c = s.changes[0]
    assert c.approver_ids == ["u_nam"] and c.notified_ids == ["u_linh"]


# ---------------- gán người nhận ----------------

def test_resolve_assignees():
    ms = [msg("a", "09:00", "u_hung", "@Linh làm nhé", mentions=[{"user_id": "u_linh", "name": "Linh"}]),
          msg("b", "09:01", "u_linh", "ok")]
    s = state_of(ms)
    cfg = Config()
    s.processed.append("a")
    key, batch = next_batch(s, cfg)
    ctx = build_context(s, key, batch, cfg, PROMPTS["extract"], PROMPTS["gate"])
    loc_b = ctx.rev_map["b"]
    ids, _ = resolve_assignees(["u_linh", f"SPEAKER:{loc_b}", "anh Hưng", "chị linh"], ctx, "a")
    assert ids == ["u_linh", "u_hung"]
    ids, det = resolve_assignees(["Nam"], ctx, "a")  # hai người tên Nam → không gán
    assert ids == [] and "trùng" in det[0]["how"]
    ids, det = resolve_assignees(["ADDRESSEE"], ctx, "a")  # nhóm → không hợp lệ
    assert ids == []


def test_addressee_in_dm():
    s = state_of([msg("d", "09:00", "u_hung", "em làm giúp anh nhé", conv="dm")])
    cfg = Config()
    key, batch = next_batch(s, cfg)
    ctx = build_context(s, key, batch, cfg, PROMPTS["extract"], PROMPTS["gate"])
    assert resolve_assignees(["ADDRESSEE"], ctx, "d")[0] == ["u_linh"]


# ---------------- parse failure & chạy lại cho cùng kết quả ----------------

def test_parse_failure_skips_turn_without_losing_message():
    s = state_of([msg("a", "09:00", "u_hung", "x"), msg("b", "09:01", "u_hung", "y")])
    turns = run_all(s, FakeLLM({"a": "{not json"}))
    assert turns[0]["skipped"] == "parse_failure"
    assert "a" in turns[1]["context"]["id_map"].values()


def test_rerun_is_deterministic():
    def go():
        s = init_state(load_dataset("sample"))
        run_all(s, HeuristicLLM())
        return [t.model_dump(exclude={"model"}) for t in s.tasks]
    assert go() == go()
