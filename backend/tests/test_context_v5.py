"""Unit test tài liệu v5 (mục 12): code tìm ứng viên trước lần gọi 1, llm_flag, luật cứng, target_not_linked.

FakeLLM, không gọi mạng. "@<message_id>" → #n, "@<task_id>" → T<n> của lần gọi đang chạy.
"""
from __future__ import annotations

import pytest

from detect.config import Config
from detect.models import UserAction, empty_op
from detect.run import process_turn
from detect.llm import FakeLLM
from test_context_v4 import (PROMPTS, _banner_dataset, at, create, fillers, four_tasks, mention, msg, run_all,  # noqa: F401
                             state_of, turn_of, update_due)

V5 = dict(context_mode="retrieve_first")


def six_tasks():
    """four_tasks (K1 báo cáo tuần · K2 bug mã giảm giá · K3 icon set · K4 slide sprint) + K5 review PR + K6 release note."""
    ms, by = four_tasks()
    ms += [msg("e", "09:40", "u_hung", "@Nam review PR nhé", mentions=mention("u_nam")),
           msg("f", "09:50", "u_hung", "@Tuấn viết release note nhé", mentions=mention("u_tuan"))]
    by["e"] = {"ops": [create("e", ["u_nam"], "Review PR")]}
    by["f"] = {"ops": [create("f", ["u_tuan"], "Viết release note")]}
    return ms, by


def cands(turn):
    """Khối B (matched + recent); `candidates` trong trace gồm cả task được link."""
    return [(c["task_id"], c["source"]) for c in turn["context"]["candidates"] if c["source"] != "linked"]


# ---------------- 3.2 ứng viên ----------------

def test_candidates_matched_first_then_recent():
    ms, by = six_tasks()
    ms += [msg("u", "11:00", "u_hung", "@Linh báo cáo tuần lùi sang thứ 6 nhé", mentions=mention("u_linh"))]
    llm = FakeLLM(by)
    t = turn_of(run_all(state_of(ms), llm, Config(**V5)), "u")
    # K1 khớp nội dung; K4 (cũng của Linh) chỉ trùng người → không vào matched mà vào recent
    assert cands(t) == [("K1", "matched"), ("K6", "recent"), ("K5", "recent"), ("K4", "recent"), ("K3", "recent")]
    assert "bao" in t["context"]["matched"][0]["via"] and t["context"]["matched"][0]["local"] == "T1"
    user = llm.calls[-1]["user"]
    assert "<CANDIDATE_TASKS>" in user and "[matched: bao, cao, tuan]" in user and "[recent]" in user
    assert "<RECENT_TASKS>" not in user and "last mentioned" not in user
    # câu "lùi", "tuần sau"… mô tả THAY ĐỔI, bị bỏ khỏi truy vấn
    assert "lùi" not in t["context"]["retrieval"]["query"] and "@Linh" not in t["context"]["retrieval"]["query"]


def test_candidates_limits():
    ms, by = six_tasks()
    ms += [msg("u", "11:00", "u_hung", "báo cáo tuần và bug mã giảm giá và icon set làm tới đâu rồi")]
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Config(**V5, matched_tasks_max=2, candidate_tasks_max=3)), "u")
    src = [s for _, s in cands(t)]
    assert src == ["matched", "matched", "recent"]
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Config(**V5, matched_tasks_max=3, candidate_tasks_max=2)), "u")
    assert [s for _, s in cands(t)] == ["matched", "matched"]


def test_candidates_exclude_linked_declined_and_closed():
    ms, by = six_tasks()
    ms += [msg("u", "11:00", "u_hung", "báo cáo tuần với icon set nhé", reply_to_id="a")]   # reply → K1 được link
    acts = [UserAction(at=at("10:00"), user_id="u_tuan", message_id="c", action="decline")]  # K3 declined
    s = state_of(ms, acts)
    t = turn_of(run_all(s, FakeLLM(by), Config(**V5)), "u")
    assert [x["task_id"] for x in t["context"]["linked"]] == ["K1"]
    ids = [i for i, _ in cands(t)]
    assert "K1" not in ids and "K3" not in ids and not t["context"]["matched"]


def test_people_only_hit_never_matched():
    ms, by = six_tasks()
    ms += [msg("u", "11:00", "u_hung", "@Tuấn ăn trưa chưa em", mentions=mention("u_tuan"))]
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Config(**V5)), "u")
    assert t["context"]["matched"] == []


# ---------------- 3.1 truy vấn ----------------

def test_query_joins_replied_message_outside_window():
    ms, by = six_tasks()
    ms += fillers(0, 6, hour=10)
    ms += [msg("u", "11:00", "u_linh", "ok anh, em gửi trước thứ 6 nhé", reply_to_id="a")]
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Config(**V5, window_k=3)), "u")
    r = t["context"]["retrieval"]
    assert r["joined_reply"] == "a" and "báo cáo tuần" in r["query"]
    assert "u_hung" in r["people"] and "u_linh" in r["people"]  # tác giả tin được reply
    # tin được reply nằm trong cửa sổ K → không nối
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Config(**V5, window_k=20)), "u")
    assert t["context"]["retrieval"]["joined_reply"] is None


# ---------------- 3.3 ngân sách ----------------

def test_budget_keep_matched_order_and_never_cut_linked():
    long = "nội dung rất dài " * 30
    ms = [msg("a", "09:00", "u_hung", "@Linh gửi báo cáo tuần nhé", mentions=mention("u_linh")),
          msg("b", "09:05", "u_hung", "@Nam gửi báo cáo tháng nhé", mentions=mention("u_nam")),
          msg("c", "09:10", "u_hung", "@Tuấn báo cáo chi phí nhé", mentions=mention("u_tuan")),
          msg("d", "09:15", "u_hung", "@Linh làm slide tổng kết sprint nhé", mentions=mention("u_linh")),
          msg("e", "09:20", "u_hung", "@Nam review PR nhé", mentions=mention("u_nam")),
          msg("f", "09:25", "u_hung", "@Tuấn làm lại icon set nhé", mentions=mention("u_tuan"))]
    by = {i: {"ops": [create(i, [u], ttl)]} for i, u, ttl in [
        ("a", "u_linh", "Gửi báo cáo tuần"), ("b", "u_nam", "Gửi báo cáo tháng"), ("c", "u_tuan", "Báo cáo chi phí"),
        ("d", "u_linh", "Làm slide tổng kết sprint"), ("e", "u_nam", "Review PR"), ("f", "u_tuan", "Làm lại icon set")]}
    ms += [msg(f"h{i}", f"10:{i:02d}", "u_nam", f"{long} {i}") for i in range(12)]
    ms += [msg("r", "11:00", "u_hung", "slide nhớ thêm số liệu báo cáo nhé", reply_to_id="d")]
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Config(**V5, context_token_budget=900)), "r")
    c = t["context"]
    order = ["drop_candidate_evidence", "reduce_k", "drop_recent_task", "drop_matched_task"]
    kinds = [x.split(" ")[0] for x in c["trims"]]
    assert kinds == sorted(kinds, key=order.index) and kinds[0] == "drop_candidate_evidence"
    assert "drop_recent_task" in kinds and "drop_matched_task" in kinds
    assert [x["task_id"] for x in c["linked"]] == ["K4"] and c["tokens"]["b3a"] > 0
    assert len(c["matched"]) == 1 and c["recent"] == []


# ---------------- 3.4 llm_flag ----------------

def _flag_dataset():
    ms, by = six_tasks()
    ms += [msg("u", "11:00", "u_hung", "@Linh cái báo cáo hôm trước lùi sang thứ 6 nhé", mentions=mention("u_linh"))]
    return ms, by


FLAG_CFG = dict(**V5, matched_tasks_max=0, candidate_tasks_max=2)  # K1 không vào prompt lần 1


def test_llm_flag_searches_then_second_call():
    ms, by = _flag_dataset()
    by["u"] = [{"ops": [], "message_type": "old_task_not_in_prompt"},
               {"ops": [update_due("K1", "u", 5)], "message_type": "task_in_prompt"}]
    s = state_of(ms)
    llm = FakeLLM(by)
    t = turn_of(run_all(s, llm, Config(**FLAG_CFG)), "u")
    assert "K1" not in [i for i, _ in cands(t)]
    assert [c["reason"] for c in t["extract_calls"]] == ["initial", "llm_flag"]
    assert t["search"]["source"] == "llm_flag" and t["search"]["results"][0]["task_id"] == "K1"
    assert 'source="llm_flag"' in llm.calls[-1]["user"] and llm.calls[-1]["user"].startswith(llm.calls[-2]["user"])
    assert "search must be null" not in llm.calls[-1]["user"]
    assert t["ops"][0]["accepted"] and t["ops"][0]["task_id"] == "K1"


def test_llm_flag_with_ops_or_disabled_is_ignored():
    ms, by = _flag_dataset()
    by["u"] = {"ops": [create("u", ["u_linh"], "Việc mới")], "message_type": "old_task_not_in_prompt"}
    s = state_of(ms)
    t = turn_of(run_all(s, FakeLLM(by), Config(**FLAG_CFG, dedup_check_on_create=False)), "u")
    assert len(t["extract_calls"]) == 1 and t["search"]["ignored"] == "llm_flag_with_ops_ignored"
    assert any(e["type"] == "llm_flag_with_ops_ignored" for e in s.trace)
    ms, by = _flag_dataset()
    by["u"] = {"ops": [], "message_type": "old_task_not_in_prompt"}
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Config(**FLAG_CFG, llm_flag_search=False)), "u")
    assert len(t["extract_calls"]) == 1 and t["search"]["ignored"] == "llm_flag_disabled"


def test_search_field_not_used_in_retrieve_first():
    ms, by = _flag_dataset()
    by["u"] = {"ops": [], "search": {"query": "báo cáo", "people": None, "reason": "x"}}
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Config(**FLAG_CFG)), "u")
    assert len(t["extract_calls"]) == 1 and t["search"] is None
    assert '"search"' not in t["extract_calls"][0]["raw"]


# ---------------- 4.1 luật cứng ----------------

def test_hard_block_declined_same_person_strong_overlap():
    ms, by, acts = _banner_dataset()   # "Linh ơi banner Black Friday làm luôn nhé" — 3 từ khoá chung, Linh đã từ chối
    by["z"] = {"ops": [create("z", ["Linh"], "Làm banner Black Friday")]}
    s = state_of(ms, acts)
    t = turn_of(run_all(s, FakeLLM(by), Config(**V5)), "z")
    assert len(t["extract_calls"]) == 1 and not t["dedup"]["triggered"]    # không hỏi lại LLM, không tính lượt gọi
    hb = t["dedup"]["hard_blocked"][0]
    assert hb["task_id"] == "K1" and hb["overlap"] >= 3 and {"banner", "black", "friday"} <= set(hb["matched"])
    assert t["ops"][0]["reason"] == "dedup_hard_block" and not t["ops"][0]["accepted"]
    assert len(s.tasks) == 5 and any(e["type"] == "dedup_hard_block" for e in s.trace)


def test_hard_block_soft_overlap_goes_to_llm():
    ms, by, acts = _banner_dataset()
    ms[-1] = msg("z", "11:00", "u_tuan", "Linh ơi banner Black làm luôn nhé")    # chỉ 2 từ khoá chung
    by["z"] = [{"ops": [create("z", ["Linh"], "Làm banner Black")]}, {"ops": []}]
    t = turn_of(run_all(state_of(ms, acts), FakeLLM(by), Config(**V5)), "z")
    assert t["dedup"]["hard_blocked"] == [] and t["dedup"]["triggered"]
    assert [c["reason"] for c in t["extract_calls"]] == ["initial", "dedup"]


def test_hard_block_expires_after_suppress_days():
    ms, by, acts = _banner_dataset()
    ms[-1] = msg("z", "11:00", "u_tuan", "Linh ơi banner Black Friday làm luôn nhé", day=17)
    by["z"] = [{"ops": [create("z", ["Linh"], "Làm banner Black Friday")]}, {"ops": []}]
    s = state_of(ms, acts)
    t = turn_of(run_all(s, FakeLLM(by), Config(**V5, declined_suppress_days=7)), "z")
    # hết hạn chặn: task đã từ chối chuyển closed → không chặn cứng, không hỏi lại, tạo lại hợp lệ
    assert next(x for x in s.tasks if x.task_id == "K1").status == "closed"
    assert t["dedup"]["hard_blocked"] == [] and not t["dedup"]["triggered"] and t["ops"][0]["accepted"]


def test_hard_block_not_for_cancelled_nor_old_modes():
    ms, by, _ = _banner_dataset(decline=False)
    ms.insert(1, msg("cx", "08:55", "u_hung", "thôi khỏi làm banner nhé", reply_to_id="bn"))
    by["cx"] = {"ops": [empty_op("CANCEL", target="@K1", evidence=[{"msg": "@cx", "role": "cancel"}])]}
    by["z"] = [{"ops": [create("z", ["Linh"], "Làm banner Black Friday")]}, {"ops": [create("z", ["Linh"], "Làm banner Black Friday")]}]
    s = state_of(ms)
    t = turn_of(run_all(s, FakeLLM(by), Config(**V5)), "z")
    assert next(x for x in s.tasks if x.task_id == "K1").status == "cancelled"
    assert t["dedup"]["hard_blocked"] == [] and any(o["accepted"] and o["op"] == "CREATE" for o in t["ops"])
    # task đã huỷ không là ứng viên trùng mềm → không hỏi lại LLM (DECISIONS #59)
    assert not t["dedup"]["triggered"] and len(t["extract_calls"]) == 1
    row = next(r for r in t["dedup"]["checks"][0]["results"] if r["task_id"] == "K1")
    assert "cancelled" in row["why_not"]
    # luật cứng chỉ ở retrieve_first; tắt bằng config
    ms, by, acts = _banner_dataset()
    by["z"] = [{"ops": [create("z", ["Linh"], "Làm banner Black Friday")]}, {"ops": []}]
    for kw in (dict(context_mode="linked_recent_search"), dict(**V5, dedup_hard_block=False)):
        t = turn_of(run_all(state_of(ms, acts), FakeLLM(by), Config(**kw)), "z")
        assert not t["dedup"].get("hard_blocked") and t["dedup"]["triggered"]


def test_hard_block_reapplied_after_second_call():
    """Lần gọi 2 (vì một CREATE khác trùng mềm) trả lại CREATE đã bị chặn → vẫn bị chặn."""
    ms, by, acts = _banner_dataset()
    ms[-1] = msg("z", "11:00", "u_tuan", "Linh ơi banner Black Friday làm luôn nhé, @Linh slide tổng kết sprint nữa",
                 mentions=mention("u_linh"))
    ops = [create("z", ["Linh"], "Làm banner Black Friday"), create("z", ["u_linh"], "Làm slide tổng kết sprint")]
    by["z"] = [{"ops": ops}, {"ops": ops}]
    s = state_of(ms, acts)
    t = turn_of(run_all(s, FakeLLM(by), Config(**V5, matched_tasks_max=0, candidate_tasks_max=0)), "z")
    assert [c["reason"] for c in t["extract_calls"]] == ["initial", "dedup"]
    assert [o["reason"] for o in t["ops"]][0] == "dedup_hard_block"


# ---------------- 4.2 target_not_linked ----------------

def test_target_not_linked_recorded_not_fixed():
    ms, by = six_tasks()
    ms += [msg("r", "11:00", "u_hung", "à mà @Nam bug mã giảm giá xong trước thứ 4 nhé", reply_to_id="a",
               mentions=mention("u_nam"))]
    by["r"] = {"ops": [update_due("K2", "r", 3)]}
    s = state_of(ms)
    t = turn_of(run_all(s, FakeLLM(by), Config(**V5)), "r")
    row = t["target_not_linked"][0]
    assert row["linked_task"] == "K1" and row["target_task"] == "K2" and "bug" in row["kw_target"]
    assert t["ops"][0]["accepted"] and t["ops"][0]["task_id"] == "K2"   # không sửa op
    assert any(e["type"] == "target_not_linked" for e in s.trace)
    # nhắm đúng task được link → không ghi
    by["r"] = {"ops": [update_due("K1", "r", 3)]}
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Config(**V5)), "r")
    assert t["target_not_linked"] == []


# ---------------- schema / prompt ----------------

@pytest.mark.parametrize("mode,props", [("scored", ["ops"]), ("linked_recent_search", ["message_type", "search", "ops"]),
                                        ("retrieve_first", ["message_type", "ops"])])
def test_schema_three_modes(mode, props):
    from detect.models import extract_schema

    assert list(extract_schema(mode)["schema"]["properties"]) == props


def test_prompt_version_and_instructions():
    from detect.config import prompt_version
    from detect.dataset import instructions_for

    assert [prompt_version(m) for m in ("scored", "linked_recent_search", "retrieve_first")] == ["v3", "v4", "v5"]
    v5 = instructions_for(PROMPTS, "retrieve_first")
    assert "CANDIDATE_TASKS" in v5 and "search =" not in v5 and "the system will look it up" in v5
    assert instructions_for(PROMPTS, "linked_recent_search") == PROMPTS["extract_v4"]


def test_v5_task_records_prompt_version():
    ms, by = six_tasks()
    s = state_of(ms)
    run_all(s, FakeLLM(by), Config(**V5))
    assert {t.prompt_version for t in s.tasks} == {"v5"}
    _ = process_turn  # noqa: F841 (import giữ cho FakeLLM dùng chung run loop)


# ---------------- chốt chặn trong code (DECISIONS #57, #58) ----------------

def test_speaker_on_assigner_message_uses_mentioned_person():
    """"@Nam viết script backup nhé" của Hưng mà model ghi SPEAKER → người nhận là Nam, không phải Hưng."""
    ms = [msg("a", "09:00", "u_hung", "@Nam viết script backup database nhé", mentions=mention("u_nam"))]
    by = {"a": {"ops": [create("a", ["SPEAKER:@a"], "Viết script backup database")]}}
    s = state_of(ms)
    t = turn_of(run_all(s, FakeLLM(by), Config(**V5)), "a")
    assert s.tasks[0].assignee_ids == ["u_nam"] and any("speaker_is_assigner" in n for n in t["ops"][0]["notes"])
    # chế độ cũ giữ nguyên hành vi
    s = state_of(ms)
    run_all(s, FakeLLM(by), Config(context_mode="linked_recent_search"))
    assert s.tasks[0].assignee_ids == ["u_hung"]
    # tự hứa kèm @mention người được báo → vẫn là người nói
    ms = [msg("b", "09:00", "u_tuan", "@Hưng em gửi bản thiết kế trước thứ 6 nhé", mentions=mention("u_hung"))]
    by = {"b": {"ops": [create("b", ["SPEAKER:@b"], "Gửi bản thiết kế", kind="self_commit")]}}
    s = state_of(ms)
    run_all(s, FakeLLM(by), Config(**V5))
    assert s.tasks[0].assignee_ids == ["u_tuan"]


def _accept(task: str, ev: str):
    return empty_op("UPDATE", target=f"@{task}", evidence=[{"msg": f"@{ev}", "role": "accept"}],
                    changes=[{"field": "assignee_refs", "value": f'["SPEAKER:@{ev}"]', "evidence_msg": f"@{ev}"}])


def test_accept_adds_to_existing_assignees():
    ms = [msg("a", "09:00", "u_hung", "Linh với Nam chuẩn bị slide demo cho khách hàng nhé"),
          msg("b", "09:05", "u_nam", "ok anh", reply_to_id="a"),
          msg("c", "09:06", "u_linh", "dạ em nhận", reply_to_id="a")]
    by = {"a": {"ops": [create("a", ["Linh"], "Chuẩn bị slide demo cho khách hàng")]},
          "b": {"ops": [_accept("K1", "b")]}, "c": {"ops": [_accept("K1", "c")]}}
    s = state_of(ms)
    turns = run_all(s, FakeLLM(by), Config(**V5))
    assert sorted(s.tasks[0].assignee_ids) == ["u_linh", "u_nam"]
    assert turn_of(turns, "c")["ops"][0]["accepted"]  # Linh đã có → không đổi, chỉ thêm bằng chứng
    # đổi người nhận bằng tên / @mention vẫn THAY như cũ
    ms2 = ms[:1] + [msg("d", "09:10", "u_hung", "thôi để Tuấn làm nhé", reply_to_id="a")]
    by2 = {"a": by["a"], "d": {"ops": [empty_op("UPDATE", target="@K1", evidence=[{"msg": "@d", "role": "assignee"}],
                                                 changes=[{"field": "assignee_refs", "value": '["Tuấn"]', "evidence_msg": "@d"}])]}}
    s = state_of(ms2)
    run_all(s, FakeLLM(by2), Config(**V5))
    assert s.tasks[0].assignee_ids == ["u_tuan"]


def test_accept_on_self_commit_replaces():
    """Model đọc nhầm lời nhờ thành tự hứa (người nhận = người nhờ); người kia nhận việc → THAY, không gộp."""
    ms = [msg("a", "09:00", "u_hung", "em làm giúp anh bảng so sánh giá nhé"),
          msg("b", "09:05", "u_linh", "Dạ vâng anh", reply_to_id="a")]
    by = {"a": {"ops": [create("a", ["SPEAKER:@a"], "Làm bảng so sánh giá", kind="self_commit")]},
          "b": {"ops": [_accept("K1", "b")]}}
    s = state_of(ms)
    run_all(s, FakeLLM(by), Config(**V5))
    assert s.tasks[0].assignee_ids == ["u_linh"]
