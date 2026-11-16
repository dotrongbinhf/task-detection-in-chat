"""Unit test dựng ngữ cảnh (task được link, task khớp nội dung, task gần nhất), kho task, kiểm tra target, chốt chặn code.

FakeLLM, không gọi mạng. "@<message_id>" → #n, "@<task_id>" → T<n> của lần gọi đang chạy (kể cả task ở lần gọi 2).
"""
from __future__ import annotations

import json

from detect.config import PROMPT_VERSION, Config
from detect.dataset import load_prompts
from detect.embed import FakeEmbedder
from detect.llm import FakeLLM
from detect.models import EXTRACT_JSON_SCHEMA, Member, Message, UserAction, empty_op
from detect.run import flush_actions, init_state, process_turn
from detect.store import TaskStore
from detect.text import terms

PROMPTS = load_prompts()
MEMBERS = {"g": [Member(user_id="u_hung", display_name="Hưng"), Member(user_id="u_linh", display_name="Linh"),
                 Member(user_id="u_nam", display_name="Nam"), Member(user_id="u_tuan", display_name="Tuấn")]}
NAMES = {"u_hung": "Hưng", "u_linh": "Linh", "u_nam": "Nam", "u_tuan": "Tuấn"}
# Chỉ 1 task gần nhất, không task khớp nội dung → task đúng nằm ngoài prompt lần 1 (để thử kiểm tra target).
NARROW = dict(matched_tasks_max=0, candidate_tasks_max=1)
EMB = FakeEmbedder({"report": ["bao cao tuan", "weekly report"], "banner": ["banner", "anh khuyen mai", "bf"],
                    "review": ["review", "pr"]})


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


def src(turn, *sources):
    """task_id trong prompt lần 1 theo nguồn (linked / matched / recent)."""
    return [c["task_id"] for c in turn["context"]["candidates"] if c["source"] in sources]


def cands(turn):
    """Khối CANDIDATE_TASKS (matched + recent), theo thứ tự trong prompt."""
    return [(c["task_id"], c["source"]) for c in turn["context"]["candidates"] if c["source"] != "linked"]


def create(anchor: str, refs: list[str], title: str, kind="assigned", **kw):
    return empty_op("CREATE", kind=kind, anchor=f"@{anchor}", title=title, assignees=refs, due_kind="none",
                    evidence=[{"msg": f"@{anchor}", "role": "assign"}], **kw)


def update_due(task: str, ev: str, weekday: int):
    """UPDATE phẳng đổi hạn sang thứ (weekday ISO + 1 = số như trong chữ)."""
    return empty_op("UPDATE", target=f"@{task}", evidence=[{"msg": f"@{ev}", "role": "due"}],
                    due_text=f"thứ {weekday + 1}", due_kind="weekday", due_weekday=weekday + 1)


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


def six_tasks():
    """four_tasks + K1.5 review PR + K1.6 release note."""
    ms, by = four_tasks()
    ms += [msg("e", "09:40", "u_hung", "@Nam review PR nhé", mentions=mention("u_nam")),
           msg("f", "09:50", "u_hung", "@Tuấn viết release note nhé", mentions=mention("u_tuan"))]
    by["e"] = {"ops": [create("e", ["u_nam"], "Review PR")]}
    by["f"] = {"ops": [create("f", ["u_tuan"], "Viết release note")]}
    return ms, by


# ---------------- task được link ----------------

def test_linked_direct_reply_chain():
    ms, by = four_tasks()
    ms += fillers(0, 12)
    ms += [msg("r1", "11:00", "u_hung", "lùi sang thứ 2 nhé", reply_to_id="a"),                 # reply trực tiếp
           msg("x", "11:05", "u_nam", "anh ơi việc này gấp không", reply_to_id="b"),
           msg("y", "11:06", "u_hung", "gấp nhé", reply_to_id="x"),                             # qua chuỗi y → x → b
           msg("z", "11:10", "u_nam", "ok", reactions=[])]
    turns = run_all(state_of(ms), FakeLLM(by), Config(window_k=10))
    r1 = turn_of(turns, "r1")
    assert src(r1, "linked") == ["K1.1"] and "K1.1" not in src(r1, "matched", "recent")
    assert "<LINKED_TASKS>" in r1["context"]["prompt"] and "NEW_MESSAGES reply to" in r1["context"]["prompt"]
    assert "báo cáo tuần" in r1["context"]["blocks"]["b3a"]  # kèm tin bằng chứng
    y = turn_of(turns, "y")
    assert src(y, "linked") == ["K1.2"] and "via thread" in y["context"]["candidates"][0]["why"]


def test_linked_includes_closed_task_and_respects_max():
    ms = [msg("a", "09:00", "u_hung", "Linh làm A, Nam làm B, Tuấn làm C, Linh làm D nhé")]
    by = {"a": {"ops": [create("a", ["Linh"], "Làm A"), create("a", ["Nam"], "Làm B"),
                        create("a", ["Tuấn"], "Làm C"), create("a", ["Linh"], "Làm D")]}}
    ms += fillers(0, 12) + [msg("r", "11:00", "u_hung", "thôi huỷ hết nhé", reply_to_id="a")]
    s = state_of(ms)
    cfg = Config(linked_tasks_max=3)
    llm = FakeLLM(by)
    while (t := process_turn(s, cfg, llm, PROMPTS)) is not None:
        if t["new_event_ids"] == ["a"]:
            s.tasks[0].status = "closed"  # task đã đóng vẫn được link (người dùng reply vào việc cũ để sửa/huỷ)
        if "r" in t["new_event_ids"]:
            linked = src(t, "linked")
            assert len(linked) == 3 and "K1.1" in linked


# ---------------- ứng viên: matched rồi recent ----------------

def test_recent_order_ignores_approval_and_excludes_declined_and_linked():
    ms, by = four_tasks()
    ms += [msg("e", "10:00", "u_nam", "mọi người ăn trưa chưa"),
           msg("f", "10:30", "u_hung", "nhớ nhé", reply_to_id="c")]
    acts = [UserAction(at=at("09:40"), user_id="u_linh", message_id="a", action="approve"),   # duyệt K1.1 ≠ "được nhắc"
            UserAction(at=at("09:45"), user_id="u_linh", message_id="d", action="decline")]   # K1.4 declined
    s = state_of(ms, acts)
    turns = run_all(s, FakeLLM(by), Config())
    assert src(turn_of(turns, "e"), "recent") == ["K1.3", "K1.2", "K1.1"]  # K1.4 declined bị loại, K1.1 không "gần" hơn vì được duyệt
    f = turn_of(turns, "f")
    assert src(f, "linked") == ["K1.3"] and src(f, "recent") == ["K1.2", "K1.1"]
    k1 = next(t for t in s.tasks if t.task_id == "K1.1")
    assert k1.status == "approved" and k1.last_mentioned_at == at("09:00")


def test_last_mentioned_at_is_evidence_message_time():
    ms, by = four_tasks()
    ms += [msg("ok", "12:00", "u_linh", "dạ em gửi trước thứ 5 ạ", reply_to_id="a")]
    by["ok"] = {"ops": [update_due("K1.1", "ok", 4)]}
    s = state_of(ms)
    run_all(s, FakeLLM(by), Config())
    assert next(t for t in s.tasks if t.task_id == "K1.1").last_mentioned_at == at("12:00")


def test_candidates_matched_first_then_recent():
    ms, by = six_tasks()
    ms += [msg("u", "11:00", "u_hung", "@Linh báo cáo tuần lùi sang thứ 6 nhé", mentions=mention("u_linh"))]
    llm = FakeLLM(by)
    t = turn_of(run_all(state_of(ms), llm, Config()), "u")
    # K1.1 khớp nội dung; K1.4 (cũng của Linh) chỉ trùng người → không vào matched mà vào recent
    assert cands(t) == [("K1.1", "matched"), ("K1.6", "recent"), ("K1.5", "recent"), ("K1.4", "recent"), ("K1.3", "recent")]
    first = t["context"]["candidates"][0]
    assert "bao" in first["why"] and first["local"] == "T1"
    user = llm.calls[-1]["user"]
    assert "<CANDIDATE_TASKS>" in user and "[matched: bao, cao, tuan]" in user and "[recent]" in user
    # "lùi", "tuần sau"… mô tả THAY ĐỔI, bị bỏ khỏi truy vấn
    assert "lùi" not in t["context"]["retrieval"]["query"] and "@Linh" not in t["context"]["retrieval"]["query"]


def test_candidates_limits():
    ms, by = six_tasks()
    ms += [msg("u", "11:00", "u_hung", "báo cáo tuần và bug mã giảm giá và icon set làm tới đâu rồi")]
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Config(matched_tasks_max=2, candidate_tasks_max=3)), "u")
    assert [s for _, s in cands(t)] == ["matched", "matched", "recent"]
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Config(matched_tasks_max=3, candidate_tasks_max=2)), "u")
    assert [s for _, s in cands(t)] == ["matched", "matched"]


def test_candidates_exclude_linked_declined_and_closed():
    ms, by = six_tasks()
    ms += [msg("u", "11:00", "u_hung", "báo cáo tuần với icon set nhé", reply_to_id="a")]   # reply → K1.1 được link
    acts = [UserAction(at=at("10:00"), user_id="u_tuan", message_id="c", action="decline")]  # K1.3 declined
    t = turn_of(run_all(state_of(ms, acts), FakeLLM(by), Config()), "u")
    assert src(t, "linked") == ["K1.1"]
    ids = [i for i, _ in cands(t)]
    assert "K1.1" not in ids and "K1.3" not in ids and not src(t, "matched")


def test_people_only_hit_never_matched():
    ms, by = six_tasks()
    ms += [msg("u", "11:00", "u_hung", "@Tuấn ăn trưa chưa em", mentions=mention("u_tuan"))]
    assert src(turn_of(run_all(state_of(ms), FakeLLM(by), Config()), "u"), "matched") == []


def test_query_joins_replied_message_outside_window():
    ms, by = six_tasks()
    ms += fillers(0, 6, hour=10)
    ms += [msg("u", "11:00", "u_linh", "ok anh, em gửi trước thứ 6 nhé", reply_to_id="a")]
    r = turn_of(run_all(state_of(ms), FakeLLM(by), Config(window_k=3)), "u")["context"]["retrieval"]
    assert r["joined_reply"] == "a" and "báo cáo tuần" in r["query"]
    assert "u_hung" in r["people"] and "u_linh" in r["people"]  # tác giả tin được reply
    # tin được reply nằm trong cửa sổ K → không nối
    assert turn_of(run_all(state_of(ms), FakeLLM(by), Config(window_k=20)), "u")["context"]["retrieval"]["joined_reply"] is None


def test_budget_trim_order_never_cuts_linked():
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
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Config(context_token_budget=900)), "r")
    order = ["drop_candidate_evidence", "reduce_k", "drop_candidate"]
    kinds = [x.split(" ")[0] for x in t["context"]["trims"]]
    assert kinds == sorted(kinds, key=order.index) and kinds[0] == "drop_candidate_evidence" and "drop_candidate" in kinds
    assert src(t, "linked") == ["K1.4"] and t["context"]["tokens"]["b3a"] > 0
    assert [s for _, s in cands(t)] == ["matched"]  # cắt recent trước, giữ ≥ 1 ứng viên


# ---------------- kho task ----------------

def test_store_search_diacritics_people_time_and_limit():
    ms = [msg("a", "09:00", "u_hung", "@Linh gửi báo cáo tuần nhé", mentions=mention("u_linh")),
          msg("b", "09:10", "u_hung", "@Nam gửi báo cáo tháng nhé", mentions=mention("u_nam")),
          msg("c", "09:20", "u_hung", "@Tuấn báo cáo chi phí nhé", mentions=mention("u_tuan"))]
    by = {"a": {"ops": [create("a", ["u_linh"], "Gửi báo cáo tuần")]},
          "b": {"ops": [create("b", ["u_nam"], "Gửi báo cáo tháng")]},
          "c": {"ops": [create("c", ["u_tuan"], "Báo cáo chi phí")]}}
    s = state_of(ms)
    run_all(s, FakeLLM(by), Config())
    st = TaskStore(s)
    hits = st.search("g", "bao cao tuan", None, None, 5)                     # không dấu khớp có dấu
    assert hits[0].task.task_id == "K1.1" and {"bao", "cao"} <= set(hits[0].matched)
    hits = st.search("g", "báo cáo", ["u_nam"], None, 5)                     # ưu tiên theo người
    assert hits[0].task.task_id == "K1.2" and hits[0].people_match
    assert st.search("g", "báo cáo", ["Tuấn"], None, 5)[0].task.task_id == "K1.3"                 # người là tên cũng được
    assert [h.task.task_id for h in st.search("g", "bao cao", None, at("09:15"), 5)] == ["K1.3"]  # lọc thời gian
    assert len(st.search("g", "bao cao", None, None, 2)) == 2                                    # giới hạn kết quả
    assert st.search("g", "deploy staging", None, None, 5) == []


def test_hybrid_finds_paraphrase_keyword_does_not():
    ms, by = four_tasks()
    s = state_of(ms)
    run_all(s, FakeLLM(by), Config())
    assert TaskStore(s, Config(), None).mode == "keyword"  # không có embedder → chỉ BM25
    assert TaskStore(s, Config(), None).search("g", "weekly report", None, None, 5) == []
    hy = TaskStore(s, Config(), EMB).search("g", "weekly report", None, None, 5)
    assert hy[0].task.task_id == "K1.1" and hy[0].similarity > 0.9 and any(m.startswith("ngữ nghĩa") for m in hy[0].matched)


def test_hybrid_keeps_exact_ids_apart():
    """Embedding coi "PR #482" ≈ "PR #490"; phần BM25 giữ cho đúng số xếp trước."""
    ms = [msg("a", "09:00", "u_hung", "@Nam review PR #490 nhé", mentions=mention("u_nam")),
          msg("b", "09:10", "u_hung", "@Nam review PR #482 nhé", mentions=mention("u_nam"))]
    by = {"a": {"ops": [create("a", ["u_nam"], "Review PR #490")]}, "b": {"ops": [create("b", ["u_nam"], "Review PR #482")]}}
    s = state_of(ms)
    run_all(s, FakeLLM(by), Config())
    hy = TaskStore(s, Config(), EMB).search("g", "PR 482 xong chưa", None, None, 5)
    assert hy[0].similarity == hy[1].similarity and hy[0].task.title == "Review PR #482"


def test_terms_keep_non_latin_scripts():
    assert terms("báo cáo tuần") == ["bao", "cao", "tuan"]
    assert terms("发送周报") == ["发送周报"] and "明日" in terms("gửi báo cáo 明日")


# ---------------- kiểm tra target (lần gọi 2 duy nhất) ----------------

def _far_update_dataset():
    ms, by = four_tasks()
    ms += [msg("e", "09:40", "u_hung", "@Nam review PR nhé", mentions=mention("u_nam"))]
    by["e"] = {"ops": [create("e", ["u_nam"], "Review PR")]}
    ms += [msg("u", "11:00", "u_hung", "@Linh báo cáo tuần lùi sang thứ 6 nhé", mentions=mention("u_linh"))]
    return ms, by


def test_target_check_redirects_wrong_update():
    ms, by = _far_update_dataset()
    # lần 1: LLM nhắm nhầm task gần nhất (Review PR / K1.5); lần 2 (sau khi code đưa K1.1 vào): đúng K1.1
    by["u"] = [{"ops": [update_due("K1.5", "u", 5)]}, {"ops": [update_due("K1.1", "u", 5)]}]
    s = state_of(ms)
    llm = FakeLLM(by)
    t = turn_of(run_all(s, llm, Config(**NARROW)), "u")
    assert "K1.1" not in src(t, "matched", "recent")
    assert t["target_check"]["triggered"] and t["target_check"]["checks"][0]["candidates"][0] == "K1.1"
    assert [c["reason"] for c in t["extract_calls"]] == ["initial", "target_check"]
    assert 'source="target_check"' in llm.calls[-1]["user"] and llm.calls[-1]["user"].startswith(llm.calls[-2]["user"])
    assert t["ops"][0]["accepted"] and t["ops"][0]["task_id"] == "K1.1"
    assert next(x for x in s.tasks if x.task_id == "K1.1").due_date == "2026-11-13"


def test_target_check_skips_linked_and_can_be_disabled():
    ms, by = _far_update_dataset()
    ms[-1] = msg("u", "11:00", "u_hung", "báo cáo tuần lùi sang thứ 6 nhé", reply_to_id="a")  # reply → K1.1 được link
    by["u"] = {"ops": [update_due("K1.1", "u", 5)]}
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Config(**NARROW)), "u")
    assert not t["target_check"]["triggered"] and t["target_check"]["checks"] == [] and len(t["extract_calls"]) == 1
    ms, by = _far_update_dataset()
    by["u"] = {"ops": [update_due("K1.5", "u", 5)]}
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Config(**NARROW, target_check_on_update=False)), "u")
    assert t["target_check"] is None and len(t["extract_calls"]) == 1


def test_target_check_semantic_paraphrase():
    ms, by = _far_update_dataset()
    ms[-1] = msg("u", "11:00", "u_hung", "@Linh weekly report dời sang thứ 6 nhé", mentions=mention("u_linh"))
    by["u"] = [{"ops": [update_due("K1.5", "u", 5)]}, {"ops": [update_due("K1.1", "u", 5)]}]
    t = turn_of(run_all(state_of(ms), FakeLLM(by, embedder=EMB), Config(**NARROW)), "u")
    c = t["target_check"]["checks"][0]
    assert c["candidates"] == ["K1.1"] and c["results"][0]["overlap"] == 0   # không chung từ khoá nào, bắt nhờ ngữ nghĩa
    assert t["ops"][0]["task_id"] == "K1.1"


def test_target_check_offers_several_semantic_candidates():
    """Đưa tới target_check_max_candidates ứng viên gần nghĩa để LLM tự chọn, không chỉ ứng viên hạng 1."""
    emb = FakeEmbedder({"a": ["bao cao tuan", "weekly report"], "b": ["bug ma giam gia", "loi coupon"]})
    ms, by = _far_update_dataset()
    ms[-1] = msg("u", "11:00", "u_hung", "@Linh weekly report với lỗi coupon dời sang thứ 6 nhé", mentions=mention("u_linh"))
    by["u"] = [{"ops": [update_due("K1.5", "u", 5)]}, {"ops": [update_due("K1.1", "u", 5)]}]
    t = turn_of(run_all(state_of(ms), FakeLLM(by, embedder=emb), Config(**NARROW, target_check_max_candidates=3)), "u")
    assert sorted(t["target_check"]["checks"][0]["candidates"]) == ["K1.1", "K1.2"]
    block = t["extract_calls"][1]["extra_block"]
    assert "Gửi báo cáo tuần" in block and "Fix bug mã giảm giá" in block
    t1 = turn_of(run_all(state_of(ms), FakeLLM(by, embedder=emb), Config(**NARROW, target_check_max_candidates=1)), "u")
    assert len(t1["target_check"]["checks"][0]["candidates"]) == 1


# ---------------- Validate / schema / prompt ----------------

def test_evidence_msg_with_copied_line_is_accepted():
    """Model đôi khi chép cả dòng tin vào evidence.msg ('#1 [thứ 2 …] Hưng: …') — chỉ lấy id ở đầu."""
    ms = [msg("a", "09:00", "u_hung", "@Linh gửi báo cáo tuần nhé", mentions=mention("u_linh"))]
    op = create("a", ["u_linh"], "Gửi báo cáo tuần")
    op["evidence"] = [{"msg": "#1 [thứ 2 09/11 09:00] Hưng (u_hung): @Linh gửi báo cáo tuần nhé", "role": "assign"}]
    t = turn_of(run_all(state_of(ms), FakeLLM({"a": {"ops": [op]}}), Config()), "a")
    assert t["ops"][0]["accepted"]


def test_message_text_instead_of_id_is_mapped():
    """Model nhỏ ghi nội dung tin (có khi kèm "(@Nam=u_nam)") thay vì #n → ghép về đúng tin; trùng nội dung → ưu tiên TIN MỚI."""
    ms = [msg("x", "08:00", "u_linh", "ok anh"),
          msg("a", "09:00", "u_hung", "@Nam làm màn hình lịch sử giao dịch nhé", mentions=mention("u_nam")),
          msg("b", "09:05", "u_nam", "ok anh", reply_to_id="a")]
    op = create("a", ["u_nam"], "Làm màn hình lịch sử giao dịch")
    op["anchor"] = op["evidence"][0]["msg"] = "@Nam làm màn hình lịch sử giao dịch nhé (@Nam=u_nam)"
    acc = empty_op("UPDATE", target="@K1.1", evidence=[{"msg": "ok anh", "role": "accept"}])
    s = state_of(ms)
    turns = run_all(s, FakeLLM({"a": {"ops": [op]}, "b": {"ops": [acc]}}), Config())
    assert turn_of(turns, "a")["ops"][0]["accepted"] and s.tasks[0].anchor_message_id == "a"
    assert turn_of(turns, "b")["ops"][0]["accepted"] and "b" in {e.message_id for e in s.tasks[0].evidence}


def test_recreating_declined_task_is_left_to_reviewer():
    """Không còn luật đếm từ chặn CREATE: việc đã bị từ chối được giao lại thì hiện nháp, người nhận quyết."""
    ms, by = four_tasks()
    ms.insert(0, msg("bn", "08:50", "u_hung", "@Linh làm banner khuyến mãi Black Friday nhé", mentions=mention("u_linh")))
    by["bn"] = {"ops": [create("bn", ["u_linh"], "Làm banner khuyến mãi Black Friday")]}
    ms.append(msg("z", "11:00", "u_tuan", "Linh ơi banner Black Friday làm luôn nhé"))
    by["z"] = {"ops": [create("z", ["Linh"], "Làm banner Black Friday")]}
    acts = [UserAction(at=at("08:55"), user_id="u_linh", message_id="bn", action="decline")]
    s = state_of(ms, acts)
    t = turn_of(run_all(s, FakeLLM(by), Config()), "z")
    assert len(t["extract_calls"]) == 1 and t["ops"][0]["accepted"] and s.tasks[-1].status == "draft"


def test_schema_and_prompt():
    props = EXTRACT_JSON_SCHEMA["schema"]["properties"]
    assert list(props) == ["message_type", "ops"]
    assert "old_task_not_in_prompt" not in json.dumps(props)
    p = PROMPTS["extract"]
    assert "CANDIDATE_TASKS" in p and "old_task_not_in_prompt" not in p and "POSSIBLE_DUPLICATES" not in p
    s = state_of(six_tasks()[0])
    run_all(s, FakeLLM(six_tasks()[1]), Config())
    assert {t.prompt_version for t in s.tasks} == {PROMPT_VERSION}


# ---------------- v6 mục 5, 7: người nhận bằng user_id, UPDATE phẳng ----------------

def _upd(task: str, ev: str, role: str = "accept", **fields):
    return empty_op("UPDATE", target=f"@{task}", evidence=[{"msg": f"@{ev}", "role": role}], **fields)


def test_assignees_add_and_remove():
    ms = [msg("a", "09:00", "u_hung", "Linh với Nam chuẩn bị slide demo cho khách hàng"),
          msg("b", "09:05", "u_nam", "ok anh", reply_to_id="a"),
          msg("c", "09:10", "u_hung", "thôi Linh để Tuấn làm thay nhé", reply_to_id="a")]
    by = {"a": {"ops": [create("a", ["u_linh"], "Chuẩn bị slide demo cho khách hàng")]},
          "b": {"ops": [_upd("K1.1", "b", assignees_add=["u_nam"])]},
          "c": {"ops": [_upd("K1.1", "c", "assignee", assignees_add=["Tuấn"], assignees_remove=["u_linh"])]}}
    s = state_of(ms)
    turns = run_all(s, FakeLLM(by), Config())
    assert s.tasks[0].assignee_ids == ["u_nam", "u_tuan"]          # tên → khớp thành viên
    assert turn_of(turns, "c")["ops"][0]["fields"] == ["assignee_refs"]


def test_unknown_user_id_is_dropped():
    ms = [msg("a", "09:00", "u_hung", "@Linh gửi báo cáo tuần nhé", mentions=mention("u_linh"))]
    s = state_of(ms)
    t = turn_of(run_all(s, FakeLLM({"a": {"ops": [create("a", ["u_linh", "u_khach"], "Gửi báo cáo tuần")]}}), Config()), "a")
    assert s.tasks[0].assignee_ids == ["u_linh"] and any(n.startswith("assignee_unknown") for n in t["ops"][0]["notes"])


def test_notes_add_appends_and_notes_replaces():
    ms = [msg("a", "09:00", "u_hung", "@Linh làm banner Black Friday", mentions=mention("u_linh")),
          msg("b", "09:30", "u_hung", "banner thêm bản dọc cho story nhé", reply_to_id="a"),
          msg("c", "10:00", "u_hung", "bỏ hết yêu cầu cũ, chỉ cần bản ngang", reply_to_id="a")]
    by = {"a": {"ops": [create("a", ["u_linh"], "Làm banner Black Friday", notes="3 kích thước")]},
          "b": {"ops": [_upd("K1.1", "b", "content", notes_add="thêm bản dọc cho story")]}}
    s = state_of(ms)
    run_all(s, FakeLLM(by), Config())
    assert s.tasks[0].notes == "3 kích thước; thêm bản dọc cho story"
    by["c"] = {"ops": [_upd("K1.1", "c", "content", notes="chỉ cần bản ngang")]}
    s = state_of(ms)
    run_all(s, FakeLLM(by), Config())
    assert s.tasks[0].notes == "chỉ cần bản ngang"


def test_due_kind_null_none_and_shift():
    ms = [msg("a", "09:00", "u_hung", "@Nam làm báo cáo, thứ 5 gửi anh nhé", day=16, mentions=mention("u_nam")),
          msg("b", "10:00", "u_hung", "thêm phần chi phí nữa", day=16, reply_to_id="a"),
          msg("c", "11:00", "u_hung", "lùi thêm 2 ngày nhé", day=16, reply_to_id="a"),
          msg("d", "12:00", "u_hung", "thôi không cần hạn nữa", day=16, reply_to_id="a"),
          msg("e", "13:00", "u_hung", "lùi thêm 1 ngày", day=16, reply_to_id="a")]
    op = create("a", ["u_nam"], "Làm báo cáo")
    op.update(due_text="thứ 5", due_kind="weekday", due_weekday=5)
    by = {"a": {"ops": [op]},
          "b": {"ops": [_upd("K1.1", "b", "content", notes_add="thêm phần chi phí")]},       # due_kind null → hạn giữ nguyên
          "c": {"ops": [_upd("K1.1", "c", "due", due_text="lùi thêm 2 ngày", due_shift_days=2)]},
          "d": {"ops": [_upd("K1.1", "d", "due", due_kind="none")]},
          "e": {"ops": [_upd("K1.1", "e", "due", due_shift_days=1)]}}
    s = state_of(ms)
    turns = run_all(s, FakeLLM(by), Config())
    hist = [(h.field, h.new) for h in s.tasks[0].field_history]
    assert ("due", "thứ 7 (21/11)") in hist                                  # 19/11 + 2 ngày
    assert s.tasks[0].due_type == "none" and s.tasks[0].due_date is None      # "none" = bỏ hạn
    assert any(n.startswith("due_shift_no_base") for n in turn_of(turns, "e")["ops"][0]["notes"])
    assert turn_of(turns, "b")["ops"][0]["fields"] == ["notes"]


def test_weekday_written_number_and_roll_forward():
    # thứ 2 16/11: "thứ 5" → 19/11 (model ghi 5); chiều cùng ngày "lùi sang thứ 2", model ghi tuần này (+0) → ra
    # chính hôm nay → đẩy sang thứ 2 tuần sau (23/11)
    ms = [msg("a", "09:00", "u_hung", "@Nam làm báo cáo, thứ 5 gửi anh nhé", day=16, mentions=mention("u_nam")),
          msg("b", "15:00", "u_nam", "anh ơi em xin lùi sang thứ 2 nhé", day=16, reply_to_id="a")]
    op = create("a", ["u_nam"], "Làm báo cáo")
    op.update(due_text="thứ 5", due_kind="weekday", due_weekday=5)
    lui = _upd("K1.1", "b", "due", due_text="thứ 2", due_kind="weekday", due_weekday=2, due_week_offset=0)
    s = state_of(ms)
    turns = run_all(s, FakeLLM({"a": {"ops": [op]}, "b": {"ops": [lui]}}), Config())
    assert any("2026-11-19" in n for n in turn_of(turns, "a")["ops"][0]["notes"])
    assert s.tasks[0].due_date == "2026-11-23"
    assert any("due_rolled_forward" in n for n in turn_of(turns, "b")["ops"][0]["notes"])
