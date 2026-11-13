"""Unit test cho tài liệu "Ngữ cảnh mới - task được link, 3 task gần nhất, tìm task" (mục 12), FakeLLM, không gọi mạng.

Trong FakeLLM: "@<message_id>" → #n, "@<task_id>" → T<n> của lần gọi đang chạy (kể cả task ở khối 6 của lần gọi 2).
"""
from __future__ import annotations

import json

import pytest

from detect.config import Config
from detect.dataset import load_prompts
from detect.llm import FakeLLM
from detect.models import Member, Message, Reaction, UserAction, empty_op
from detect.run import flush_actions, init_state, process_turn
from detect.store import TaskStore

PROMPTS = load_prompts()
MEMBERS = {"g": [Member(user_id="u_hung", display_name="Hưng"), Member(user_id="u_linh", display_name="Linh"),
                 Member(user_id="u_nam", display_name="Nam"), Member(user_id="u_tuan", display_name="Tuấn")]}
NAMES = {"u_hung": "Hưng", "u_linh": "Linh", "u_nam": "Nam", "u_tuan": "Tuấn"}
LINKED = dict(context_mode="linked_recent_search")


def at(t: str, day: int = 9) -> str:
    return f"2026-11-{day:02d}T{t}+07:00"


def msg(i: str, t: str, who: str, text: str, day: int = 9, **kw) -> Message:
    return Message(id=i, conversation_id="g", conversation_type="group", created_at=at(t, day), author_id=who,
                   author_name=NAMES[who], text=text, **kw)


def mention(*uids: str) -> list[dict]:
    return [{"user_id": u, "name": NAMES[u]} for u in uids]


def state_of(messages, actions=()):
    return init_state(dict(name="t", messages=messages, members=MEMBERS, gold=None, hard_negatives=[], user_actions=list(actions)))


def run_all(state, llm, cfg):
    out = []
    while (t := process_turn(state, cfg, llm, PROMPTS)) is not None:
        out.append(t)
    flush_actions(state, cfg)
    return out


def turn_of(turns, event_id):
    return next(t for t in turns if event_id in t["new_event_ids"])


def create(anchor: str, refs: list[str], title: str, kind="assigned", **kw):
    return empty_op("CREATE", kind=kind, anchor=f"@{anchor}", title=title, assignee_refs=refs, due_kind="none",
                    evidence=[{"msg": f"@{anchor}", "role": "assign"}], **kw)


def update_due(task: str, ev: str, weekday: int):
    due = {"due_text": f"thứ {weekday + 1}", "due_kind": "weekday", "due_weekday": weekday}
    return empty_op("UPDATE", target=f"@{task}", evidence=[{"msg": f"@{ev}", "role": "due"}],
                    changes=[{"field": "due", "value": json.dumps(due, ensure_ascii=False), "evidence_msg": f"@{ev}"}])


def fillers(start_min: int, n: int, day: int = 9, hour: int = 10) -> list[Message]:
    return [msg(f"f{day}{hour}{i:02d}", f"{hour:02d}:{start_min + i:02d}", "u_nam", f"chuyện phiếm số {i}", day) for i in range(n)]


def four_tasks() -> tuple[list[Message], dict]:
    """K1..K4 lần lượt lúc 09:00 / 09:10 / 09:20 / 09:30, mỗi task một người nhận (Linh / Nam / Tuấn / Linh)."""
    ms = [msg("a", "09:00", "u_hung", "@Linh gửi báo cáo tuần cho anh nhé", mentions=mention("u_linh")),
          msg("b", "09:10", "u_hung", "@Nam fix bug mã giảm giá nhé", mentions=mention("u_nam")),
          msg("c", "09:20", "u_hung", "@Tuấn làm lại icon set nhé", mentions=mention("u_tuan")),
          msg("d", "09:30", "u_hung", "@Linh làm slide tổng kết sprint nhé", mentions=mention("u_linh"))]
    by = {"a": {"ops": [create("a", ["u_linh"], "Gửi báo cáo tuần cho anh Hưng")]},
          "b": {"ops": [create("b", ["u_nam"], "Fix bug mã giảm giá")]},
          "c": {"ops": [create("c", ["u_tuan"], "Làm lại icon set")]},
          "d": {"ops": [create("d", ["u_linh"], "Làm slide tổng kết sprint")]}}
    return ms, by


# ---------------- A. task được link ----------------

def test_linked_direct_reply_chain_and_reaction():
    ms, by = four_tasks()
    ms += fillers(0, 12)
    ms += [msg("r1", "11:00", "u_hung", "lùi sang thứ 2 nhé", reply_to_id="a"),                 # reply trực tiếp
           msg("x", "11:05", "u_nam", "anh ơi việc này gấp không", reply_to_id="b"),
           msg("y", "11:06", "u_hung", "gấp nhé", reply_to_id="x"),                             # qua chuỗi y → x → b
           msg("z", "11:10", "u_nam", "ok", reactions=[])]
    ms[2].reactions.append(Reaction(user_id="u_tuan", type="like", created_at=at("11:20")))  # 👍 vào c sau nhiều tin
    turns = run_all(state_of(ms), FakeLLM(by), Config(**LINKED, window_k=10))
    r1 = turn_of(turns, "r1")["context"]
    assert [x["task_id"] for x in r1["linked"]] == ["K1"] and "reply to" in r1["linked"][0]["via"]
    assert "K1" not in [x["task_id"] for x in r1["recent"]]
    assert "<LINKED_TASKS>" in r1["prompt"] and "NEW_MESSAGES reply to" in r1["prompt"]
    y = turn_of(turns, "y")["context"]
    assert [x["task_id"] for x in y["linked"]] == ["K2"] and "via thread" in y["linked"][0]["via"]
    rc = turn_of(turns, "c#r1")["context"]
    assert [x["task_id"] for x in rc["linked"]] == ["K3"] and "react" in rc["linked"][0]["via"]
    # khối 3a kèm tối đa linked_evidence_per_task tin bằng chứng
    assert "báo cáo tuần" in r1["blocks"]["b3a"]


def test_linked_includes_closed_task_and_respects_max():
    ms = [msg("a", "09:00", "u_hung", "Linh làm A, Nam làm B, Tuấn làm C, Linh làm D nhé")]
    by = {"a": {"ops": [create("a", ["Linh"], "Làm A"), create("a", ["Nam"], "Làm B"),
                        create("a", ["Tuấn"], "Làm C"), create("a", ["Linh"], "Làm D")]}}
    ms += fillers(0, 12) + [msg("r", "11:00", "u_hung", "thôi huỷ hết nhé", reply_to_id="a")]
    s = state_of(ms)
    cfg = Config(**LINKED, linked_tasks_max=3)
    llm = FakeLLM(by)
    while (t := process_turn(s, cfg, llm, PROMPTS)) is not None:
        if t["new_event_ids"] == ["a"]:
            s.tasks[0].status = "closed"  # task đã đóng vẫn được link (người dùng reply vào việc cũ để sửa/huỷ)
        if "r" in t["new_event_ids"]:
            linked = [x["task_id"] for x in t["context"]["linked"]]
            assert len(linked) == 3 and "K1" in linked


# ---------------- B. 3 task gần nhất ----------------

def test_recent_order_ignores_approval_and_excludes_declined_and_linked():
    ms, by = four_tasks()
    ms += [msg("e", "10:00", "u_nam", "mọi người ăn trưa chưa"),
           msg("f", "10:30", "u_hung", "nhớ nhé", reply_to_id="c")]
    acts = [UserAction(at=at("09:40"), user_id="u_linh", message_id="a", action="approve"),   # duyệt K1 ≠ "được nhắc"
            UserAction(at=at("09:45"), user_id="u_linh", message_id="d", action="decline")]   # K4 declined
    s = state_of(ms, acts)
    turns = run_all(s, FakeLLM(by), Config(**LINKED))
    e = turn_of(turns, "e")["context"]
    assert [x["task_id"] for x in e["recent"]] == ["K3", "K2", "K1"]  # K4 declined bị loại, K1 không "gần" hơn vì được duyệt
    f = turn_of(turns, "f")["context"]
    assert [x["task_id"] for x in f["linked"]] == ["K3"]
    assert [x["task_id"] for x in f["recent"]] == ["K2", "K1"]
    k1 = next(t for t in s.tasks if t.task_id == "K1")
    assert k1.status == "approved" and k1.last_mentioned_at == at("09:00")


def test_last_mentioned_at_is_evidence_message_time():
    ms, by = four_tasks()
    ms += [msg("ok", "12:00", "u_linh", "dạ em gửi trước thứ 5 ạ", reply_to_id="a")]
    by["ok"] = {"ops": [update_due("K1", "ok", 4)]}
    s = state_of(ms)
    run_all(s, FakeLLM(by), Config(**LINKED))
    assert next(t for t in s.tasks if t.task_id == "K1").last_mentioned_at == at("12:00")


# ---------------- tìm kiếm trong kho ----------------

def test_store_search_diacritics_people_time_and_limit():
    ms = [msg("a", "09:00", "u_hung", "@Linh gửi báo cáo tuần nhé", mentions=mention("u_linh")),
          msg("b", "09:10", "u_hung", "@Nam gửi báo cáo tháng nhé", mentions=mention("u_nam")),
          msg("c", "09:20", "u_hung", "@Tuấn báo cáo chi phí nhé", mentions=mention("u_tuan"))]
    by = {"a": {"ops": [create("a", ["u_linh"], "Gửi báo cáo tuần")]},
          "b": {"ops": [create("b", ["u_nam"], "Gửi báo cáo tháng")]},
          "c": {"ops": [create("c", ["u_tuan"], "Báo cáo chi phí")]}}
    s = state_of(ms)
    run_all(s, FakeLLM(by), Config(**LINKED))
    st = TaskStore(s)
    hits = st.search("g", "bao cao tuan", None, None, 5)                     # không dấu khớp có dấu
    assert hits[0].task.task_id == "K1" and {"bao", "cao"} <= set(hits[0].matched)
    hits = st.search("g", "báo cáo", ["u_nam"], None, 5)                     # lọc / ưu tiên theo người
    assert hits[0].task.task_id == "K2" and hits[0].people_match
    hits = st.search("g", "báo cáo", ["Tuấn"], None, 5)                      # nguoi là tên cũng được
    assert hits[0].task.task_id == "K3"
    assert [h.task.task_id for h in st.search("g", "bao cao", None, at("09:15"), 5)] == ["K3"]  # lọc thời gian
    assert len(st.search("g", "bao cao", None, None, 2)) == 2                                    # giới hạn kết quả
    assert st.search("g", "deploy staging", None, None, 5) == []


# ---------------- vòng tối đa 2 lần gọi ----------------

def _far_update_dataset():
    ms, by = four_tasks()
    ms += [msg("e", "09:40", "u_hung", "@Nam review PR nhé", mentions=mention("u_nam"))]
    by["e"] = {"ops": [create("e", ["u_nam"], "Review PR")]}
    ms += [msg("u", "11:00", "u_hung", "@Linh báo cáo tuần lùi sang thứ 6 nhé", mentions=mention("u_linh"))]
    return ms, by


def test_search_then_update_on_second_call():
    ms, by = _far_update_dataset()
    by["u"] = [{"ops": [], "search": {"query": "báo cáo tuần", "people": ["u_linh"], "reason": "đổi hạn việc cũ"}},
               {"ops": [update_due("K1", "u", 5)], "search": None}]
    s = state_of(ms)
    llm = FakeLLM(by)
    turns = run_all(s, llm, Config(**LINKED))
    t = turn_of(turns, "u")
    assert "K1" not in [x["task_id"] for x in t["context"]["recent"]]          # K1 không trong 3 task gần nhất
    assert [c["reason"] for c in t["extract_calls"]] == ["initial", "search"]
    assert t["search"]["results"][0]["task_id"] == "K1" and t["search"]["people"] == ["u_linh"]
    assert "<TASK_SEARCH_RESULTS" in llm.calls[-1]["user"] and llm.calls[-1]["user"].startswith(llm.calls[-2]["user"])
    op = t["ops"][0]
    assert op["accepted"] and op["task_id"] == "K1"
    assert next(x for x in s.tasks if x.task_id == "K1").due_date == "2026-11-13"


def test_search_on_second_call_is_dropped():
    ms, by = _far_update_dataset()
    q = {"query": "báo cáo tuần", "people": None, "reason": "x"}
    by["u"] = [{"ops": [], "search": q}, {"ops": [], "search": q}]
    s = state_of(ms)
    turns = run_all(s, FakeLLM(by), Config(**LINKED))
    t = turn_of(turns, "u")
    assert len(t["extract_calls"]) == 2 and t["search"]["second_call_search_ignored"] == "báo cáo tuần"
    assert any(e["type"] == "search_on_second_call" for e in s.trace)


def test_search_with_ops_is_ignored():
    ms, by = _far_update_dataset()
    by["u"] = {"ops": [create("u", ["u_linh"], "Việc mới")], "search": {"query": "báo cáo tuần", "people": None, "reason": "x"}}
    s = state_of(ms)
    turns = run_all(s, FakeLLM(by), Config(**LINKED, dedup_check_on_create=False))
    t = turn_of(turns, "u")
    assert len(t["extract_calls"]) == 1 and t["search"]["ignored"] == "search_with_ops_ignored"
    assert t["ops"][0]["accepted"]


# ---------------- kiểm trùng khi CREATE ----------------

def _banner_dataset(decline: bool = True):
    ms, by = four_tasks()
    ms.insert(0, msg("bn", "08:50", "u_hung", "@Linh làm banner khuyến mãi Black Friday nhé", mentions=mention("u_linh")))
    by["bn"] = {"ops": [create("bn", ["u_linh"], "Làm banner khuyến mãi Black Friday")]}
    acts = [UserAction(at=at("08:55"), user_id="u_linh", message_id="bn", action="decline")] if decline else []
    ms.append(msg("z", "11:00", "u_tuan", "Linh ơi banner Black Friday làm luôn nhé"))
    return ms, by, acts


def test_dedup_triggers_and_llm_drops_create():
    ms, by, acts = _banner_dataset()
    by["z"] = [{"ops": [create("z", ["Linh"], "Làm banner Black Friday")], "search": None}, {"ops": [], "search": None}]
    s = state_of(ms, acts)
    llm = FakeLLM(by)
    turns = run_all(s, llm, Config(**LINKED))
    t = turn_of(turns, "z")
    assert t["dedup"]["triggered"] and t["dedup"]["checks"][0]["candidates"] == ["K1"]
    assert [c["reason"] for c in t["extract_calls"]] == ["initial", "dedup"]
    assert "<POSSIBLE_DUPLICATES>" in llm.calls[-1]["user"] and "You are about to CREATE" in llm.calls[-1]["user"]
    assert t["dedup"]["outcome"] == "create_dropped" and len(s.tasks) == 5


def test_dedup_not_triggered_below_threshold_or_when_in_prompt():
    # ứng viên đã có trong prompt (task draft gần nhất) → không hỏi lại
    ms, by, acts = _banner_dataset(decline=False)
    ms = [m for m in ms if m.id not in ("a", "b", "c", "d")]
    by["z"] = {"ops": [create("z", ["Linh"], "Làm banner Black Friday")], "search": None}
    s = state_of(ms, acts)
    t = turn_of(run_all(s, FakeLLM(by), Config(**LINKED)), "z")
    assert not t["dedup"]["triggered"] and t["dedup"]["checks"][0]["results"][0]["why_not"] == "đã có trong prompt"
    # chỉ 1 từ khoá chung (tên người không tính) → không phải ứng viên
    ms, by, acts = _banner_dataset()
    ms[-1] = msg("z", "11:00", "u_tuan", "Linh ơi làm poster sự kiện Black nhé")
    by["z"] = {"ops": [create("z", ["Linh"], "Làm poster sự kiện")], "search": None}
    s = state_of(ms, acts)
    t = turn_of(run_all(s, FakeLLM(by), Config(**LINKED)), "z")
    assert not t["dedup"]["triggered"] and len(t["extract_calls"]) == 1


def test_dedup_respects_call_limit():
    ms, by, acts = _banner_dataset()
    by["z"] = {"ops": [create("z", ["Linh"], "Làm banner Black Friday")], "search": None}
    s = state_of(ms, acts)
    t = turn_of(run_all(s, FakeLLM(by), Config(**LINKED, extract_max_calls_per_turn=1)), "z")
    assert t["dedup"]["triggered"] and t["dedup"]["skipped"] == "dedup_skipped_call_limit"
    assert len(t["extract_calls"]) == 1 and t["ops"][0]["accepted"]
    assert any(e["type"] == "dedup_skipped_call_limit" for e in s.trace)


def test_dedup_after_search_has_no_third_call():
    ms, by, acts = _banner_dataset()
    by["z"] = [{"ops": [], "search": {"query": "poster", "people": None, "reason": "x"}},
               {"ops": [create("z", ["Linh"], "Làm banner Black Friday")], "search": None}]
    s = state_of(ms, acts)
    t = turn_of(run_all(s, FakeLLM(by), Config(**LINKED)), "z")
    assert len(t["extract_calls"]) == 2 and t["dedup"]["skipped"] == "dedup_skipped_call_limit"


# ---------------- ngân sách token ----------------

def test_budget_never_cuts_linked():
    long = "nội dung rất dài " * 30
    ms, by = four_tasks()
    ms += [msg(f"h{i}", f"10:{i:02d}", "u_nam", f"{long} {i}") for i in range(12)]
    ms += [msg("r", "11:00", "u_hung", "lùi sang thứ 2 nhé", reply_to_id="a")]
    turns = run_all(state_of(ms), FakeLLM(by), Config(**LINKED, context_token_budget=900, message_max_tokens=80))
    c = turn_of(turns, "r")["context"]
    kinds = [t.split(" ")[0] for t in c["trims"]]
    assert kinds[0] == "drop_recent_evidence" and "reduce_k" in kinds and "drop_recent_task" in kinds
    assert kinds == sorted(kinds, key=["drop_recent_evidence", "reduce_k", "drop_recent_task"].index)
    assert [x["task_id"] for x in c["linked"]] == ["K1"] and c["tokens"]["b3a"] > 0 and len(c["recent"]) == 1


# ---------------- schema ----------------

@pytest.mark.parametrize("mode,has_search", [("scored", False), ("linked_recent_search", True)])
def test_schema_per_mode(mode, has_search):
    from detect.models import extract_schema

    assert ("search" in extract_schema(mode)["schema"]["properties"]) == has_search


# ---------------- kiểm tra target (bổ sung ngoài tài liệu) ----------------

def test_target_check_redirects_wrong_update():
    ms, by = _far_update_dataset()
    # lần 1: LLM nhắm nhầm task gần nhất (Review PR / K5); lần 2 (sau khi code đưa K1 vào khối 6): đúng K1
    by["u"] = [{"ops": [update_due("K5", "u", 5)], "search": None}, {"ops": [update_due("K1", "u", 5)], "search": None}]
    s = state_of(ms)
    llm = FakeLLM(by)
    t = turn_of(run_all(s, llm, Config(**LINKED)), "u")
    assert t["target_check"]["triggered"] and t["target_check"]["checks"][0]["candidates"][0] == "K1"
    assert [c["reason"] for c in t["extract_calls"]] == ["initial", "target_check"]
    assert 'source="target_check"' in llm.calls[-1]["user"]
    assert t["ops"][0]["accepted"] and t["ops"][0]["task_id"] == "K1"


def test_target_check_skips_linked_and_good_targets():
    ms, by = _far_update_dataset()
    ms[-1] = msg("u", "11:00", "u_hung", "báo cáo tuần lùi sang thứ 6 nhé", reply_to_id="a")  # reply → K1 được link
    by["u"] = {"ops": [update_due("K1", "u", 5)], "search": None}
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Config(**LINKED)), "u")
    assert not t["target_check"]["triggered"] and t["target_check"]["checks"] == [] and len(t["extract_calls"]) == 1
    # tắt bằng config
    ms, by = _far_update_dataset()
    by["u"] = {"ops": [update_due("K5", "u", 5)], "search": None}
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Config(**LINKED, target_check_on_update=False)), "u")
    assert t["target_check"] is None and len(t["extract_calls"]) == 1


def test_evidence_msg_with_copied_line_is_accepted():
    """Model đôi khi chép cả dòng tin vào evidence.msg ('#2 [thứ 2 …] Linh: …') — chỉ lấy id ở đầu."""
    ms = [msg("a", "09:00", "u_hung", "@Linh gửi báo cáo tuần nhé", mentions=mention("u_linh"))]
    op = create("a", ["u_linh"], "Gửi báo cáo tuần")
    op["evidence"] = [{"msg": "#1 [thứ 2 09/11 09:00] Hưng: @Linh gửi báo cáo tuần nhé", "role": "assign"}]  # a là tin duy nhất → #1
    s = state_of(ms)
    t = turn_of(run_all(s, FakeLLM({"a": {"ops": [op]}}), Config(**LINKED)), "a")
    assert t["ops"][0]["accepted"]


def test_dedup_counts_people_named_in_message():
    """LLM gán nhầm người nhận (SPEAKER = Tuấn) cho "Linh ơi banner…": vẫn bắt trùng nhờ tên Linh trong tin."""
    ms, by, acts = _banner_dataset()
    by["z"] = [{"ops": [create("z", ["SPEAKER:@z"], "Làm banner Black Friday")], "search": None}, {"ops": [], "search": None}]
    s = state_of(ms, acts)
    t = turn_of(run_all(s, FakeLLM(by), Config(**LINKED)), "z")
    c = t["dedup"]["checks"][0]
    assert c["people"] == ["u_tuan"] and c["named"] == ["u_linh"]
    assert t["dedup"]["triggered"] and c["candidates"] == ["K1"]


# ---------------- tìm kiếm hybrid (BM25 + embedding) ----------------

from detect.embed import FakeEmbedder  # noqa: E402

EMB = FakeEmbedder({"report": ["bao cao tuan", "weekly report"], "banner": ["banner", "anh khuyen mai", "bf"],
                    "review": ["review", "pr"]})


def _store_with(ms, by, cfg_kw=None):
    s = state_of(ms)
    run_all(s, FakeLLM(by), Config(**LINKED))
    return s


def test_hybrid_finds_paraphrase_keyword_does_not():
    ms, by = four_tasks()
    s = _store_with(ms, by)
    kw = TaskStore(s, Config(**LINKED, search_mode="keyword"), EMB).search("g", "weekly report", None, None, 5)
    hy = TaskStore(s, Config(**LINKED, search_mode="hybrid"), EMB).search("g", "weekly report", None, None, 5)
    assert [h.task.task_id for h in kw] == []
    assert hy[0].task.task_id == "K1" and hy[0].similarity > 0.9 and any(m.startswith("ngữ nghĩa") for m in hy[0].matched)
    # không có embedder → luôn về BM25
    assert TaskStore(s, Config(**LINKED, search_mode="hybrid"), None).mode == "keyword"


def test_hybrid_keeps_exact_ids_apart():
    """Ngữ nghĩa coi "PR #482" ≈ "PR #490"; phần BM25 của hybrid giữ cho đúng số xếp trước."""
    ms = [msg("a", "09:00", "u_hung", "@Nam review PR #490 nhé", mentions=mention("u_nam")),
          msg("b", "09:10", "u_hung", "@Nam review PR #482 nhé", mentions=mention("u_nam"))]
    by = {"a": {"ops": [create("a", ["u_nam"], "Review PR #490")]}, "b": {"ops": [create("b", ["u_nam"], "Review PR #482")]}}
    s = _store_with(ms, by)
    sem = TaskStore(s, Config(**LINKED, search_mode="semantic"), EMB).search("g", "PR 482 xong chưa", None, None, 5)
    hy = TaskStore(s, Config(**LINKED, search_mode="hybrid"), EMB).search("g", "PR 482 xong chưa", None, None, 5)
    assert sem[0].similarity == sem[1].similarity            # ngữ nghĩa không phân biệt được
    assert hy[0].task.title == "Review PR #482"               # hybrid thì có


def test_target_check_semantic_paraphrase():
    ms, by = _far_update_dataset()
    ms[-1] = msg("u", "11:00", "u_hung", "@Linh weekly report dời sang thứ 6 nhé", mentions=mention("u_linh"))
    by["u"] = [{"ops": [update_due("K5", "u", 5)], "search": None}, {"ops": [update_due("K1", "u", 5)], "search": None}]
    s = state_of(ms)
    llm = FakeLLM(by, embedder=EMB)
    t = turn_of(run_all(s, llm, Config(**LINKED)), "u")
    c = t["target_check"]["checks"][0]
    assert c["candidates"] == ["K1"] and c["results"][0]["overlap"] == 0   # không chung từ khoá nào, bắt nhờ ngữ nghĩa
    assert t["ops"][0]["task_id"] == "K1"


def test_dedup_semantic_paraphrase():
    ms, by, acts = _banner_dataset()
    ms[-1] = msg("z", "11:00", "u_tuan", "Linh ơi làm luôn ảnh khuyến mãi BF nhé")
    by["z"] = [{"ops": [create("z", ["Linh"], "Làm ảnh khuyến mãi BF")], "search": None}, {"ops": [], "search": None}]
    s = state_of(ms, acts)
    t = turn_of(run_all(s, FakeLLM(by, embedder=EMB), Config(**LINKED)), "z")
    row = t["dedup"]["checks"][0]["results"][0]
    assert row["task_id"] == "K1" and row["candidate"] and row["similarity"] > 0.9
    assert t["dedup"]["outcome"] == "create_dropped"
    # cùng dữ liệu, chế độ keyword: "khuyến mãi" chỉ là 1 từ khoá chung → không bắt được
    s = state_of(ms, acts)
    t = turn_of(run_all(s, FakeLLM(by, embedder=EMB), Config(**LINKED, search_mode="keyword")), "z")
    assert not t["dedup"]["triggered"]


def test_target_check_offers_several_semantic_candidates():
    """Đưa tới target_check_max_candidates ứng viên gần nghĩa để LLM tự chọn, không chỉ ứng viên hạng 1."""
    emb = FakeEmbedder({"a": ["bao cao tuan", "weekly report"], "b": ["bug ma giam gia", "loi coupon"]})
    ms, by = _far_update_dataset()
    ms[-1] = msg("u", "11:00", "u_hung", "@Linh weekly report với lỗi coupon dời sang thứ 6 nhé", mentions=mention("u_linh"))
    by["u"] = [{"ops": [update_due("K5", "u", 5)], "search": None}, {"ops": [update_due("K1", "u", 5)], "search": None}]
    llm = FakeLLM(by, embedder=emb)
    t = turn_of(run_all(state_of(ms), llm, Config(**LINKED, target_check_max_candidates=3)), "u")
    assert sorted(t["target_check"]["checks"][0]["candidates"]) == ["K1", "K2"]
    block = t["extract_calls"][1]["extra_block"]
    assert "Gửi báo cáo tuần" in block and "Fix bug mã giảm giá" in block
    t1 = turn_of(run_all(state_of(ms), FakeLLM(by, embedder=emb), Config(**LINKED, target_check_max_candidates=1)), "u")
    assert len(t1["target_check"]["checks"][0]["candidates"]) == 1
