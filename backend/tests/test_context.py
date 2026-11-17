"""Unit test dựng ngữ cảnh: ba tầng task (LINKED / DETAILS / INDEX), detail_check, người nhận bằng user_id, UPDATE phẳng.

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

PROMPTS = load_prompts()

def Cfg(**kw) -> Config:
    """Config cho test v5 cũ: mỗi tin một lượt (quiet_seconds = 0) trừ khi test tự đặt."""
    return Config(**{"quiet_seconds": 0, **kw})

MEMBERS = {"g": [Member(user_id="u_hung", display_name="Hưng"), Member(user_id="u_linh", display_name="Linh"),
                 Member(user_id="u_nam", display_name="Nam"), Member(user_id="u_tuan", display_name="Tuấn")]}
NAMES = {"u_hung": "Hưng", "u_linh": "Linh", "u_nam": "Nam", "u_tuan": "Tuấn"}
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
    """task_id trong prompt lần 1 theo tầng (linked / details / index), theo thứ tự trong prompt."""
    return [c["task_id"] for c in turn["context"]["candidates"] if c["source"] in sources]


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
    turns = run_all(state_of(ms), FakeLLM(by), Cfg(window_k=10))
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
    cfg = Cfg(linked_tasks_max=3)
    llm = FakeLLM(by)
    while (t := process_turn(s, cfg, llm, PROMPTS)) is not None:
        if t["new_event_ids"] == ["a"]:
            s.tasks[0].status = "closed"  # task đã đóng vẫn được link (người dùng reply vào việc cũ để sửa/huỷ)
        if "r" in t["new_event_ids"]:
            linked = src(t, "linked")
            assert len(linked) == 3 and "K1.1" in linked


# ---------------- ba tầng: LINKED_TASKS · TASK_DETAILS · TASK_INDEX (v6 mục 6) ----------------

def test_details_recent_order_ignores_approval_and_declined_goes_to_index():
    ms, by = four_tasks()
    ms += [msg("e", "10:00", "u_nam", "mọi người xem lịch họp chiều nay chưa"),
           msg("f", "10:30", "u_hung", "nhớ nhé", reply_to_id="c")]
    acts = [UserAction(at=at("09:40"), user_id="u_linh", message_id="a", action="approve"),   # duyệt K1.1 ≠ "được nhắc"
            UserAction(at=at("09:45"), user_id="u_linh", message_id="d", action="decline")]   # K1.4 declined
    s = state_of(ms, acts)
    turns = run_all(s, FakeLLM(by), Cfg())
    e = turn_of(turns, "e")
    assert src(e, "details") == ["K1.3", "K1.2", "K1.1"] and src(e, "index") == ["K1.4"]
    assert '[declined] "Làm slide tổng kết sprint"' in e["context"]["blocks"]["b3c"]
    f = turn_of(turns, "f")
    assert src(f, "linked") == ["K1.3"] and src(f, "details") == ["K1.2", "K1.1"]
    k1 = next(t for t in s.tasks if t.task_id == "K1.1")
    assert k1.status == "approved" and k1.last_mentioned_at == at("09:00")


def test_last_mentioned_at_is_evidence_message_time():
    ms, by = four_tasks()
    ms += [msg("ok", "12:00", "u_linh", "dạ em gửi trước thứ 5 ạ", reply_to_id="a")]
    by["ok"] = {"ops": [update_due("K1.1", "ok", 4)]}
    s = state_of(ms)
    run_all(s, FakeLLM(by), Cfg())
    assert next(t for t in s.tasks if t.task_id == "K1.1").last_mentioned_at == at("12:00")


def test_each_task_once_numbering_continuous_and_formats():
    ms, by = six_tasks()
    ms += [msg("u", "11:00", "u_hung", "báo cáo tuần lùi sang thứ 6 nhé", reply_to_id="b")]
    llm = FakeLLM(by, embedder=EMB)
    t = turn_of(run_all(state_of(ms), llm, Cfg(detail_similar_max=1)), "u")
    c = t["context"]["candidates"]
    assert [x["local"] for x in c] == [f"T{i}" for i in range(1, 7)]               # T<n> liên tục qua ba khối
    assert len({x["task_id"] for x in c}) == 6                                       # mỗi task đúng một lần
    assert src(t, "linked") == ["K1.2"]
    assert src(t, "details")[:3] == ["K1.6", "K1.5", "K1.4"]                         # 3 gần nhất
    assert src(t, "details")[3] == "K1.1"                                            # gần nghĩa nhất ("báo cáo tuần")
    user = llm.calls[-1]["user"]
    assert "— assignee: Linh (u_linh) — due: none" in user
    assert '"Làm lại icon set" — Tuấn (u_tuan) — no due' in t["context"]["blocks"]["b3c"]
    assert "@Linh" not in t["context"]["retrieval"]["query"]


def test_details_without_embedder_is_recency_only():
    ms, by = six_tasks()
    ms += [msg("u", "11:00", "u_hung", "báo cáo tuần lùi sang thứ 6 nhé")]
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Cfg()), "u")
    assert src(t, "details") == ["K1.6", "K1.5", "K1.4"] and src(t, "index") == ["K1.3", "K1.2", "K1.1"]


def test_index_overflow_keeps_recent_then_most_similar():
    ms = [msg(f"t{i:02d}", f"09:{i:02d}", "u_hung", f"@Nam làm việc số {i} nhé", mentions=mention("u_nam")) for i in range(12)]
    ms[0] = msg("t00", "09:00", "u_hung", "@Nam gửi báo cáo tuần nhé", mentions=mention("u_nam"))
    by = {m.id: {"ops": [create(m.id, ["u_nam"], "Gửi báo cáo tuần" if m.id == "t00" else f"Làm việc số {int(m.id[1:])}")]} for m in ms}
    ms += [msg("u", "11:00", "u_hung", "weekly report gửi chưa em")]
    cfg = Cfg(detail_recent_max=1, detail_similar_max=0, index_tasks_max=4, index_overflow_max=1)
    t = turn_of(run_all(state_of(ms), FakeLLM(by, embedder=EMB), cfg), "u")
    assert src(t, "details") == ["K1.12"]
    assert src(t, "index") == ["K1.11", "K1.10", "K1.9", "K1.1"]  # 3 gần nhất + chỗ tràn cho task gần nghĩa nhất
    assert t["context"]["index_count"] == 4 and t["context"]["retrieval"]["index_total"] == 11


def test_query_joins_replied_message_outside_window():
    ms, by = six_tasks()
    ms += fillers(0, 6, hour=10)
    ms += [msg("u", "11:00", "u_linh", "ok anh, em gửi trước thứ 6 nhé", reply_to_id="a")]
    r = turn_of(run_all(state_of(ms), FakeLLM(by), Cfg(window_k=3)), "u")["context"]["retrieval"]
    assert r["joined_reply"] == "a" and "báo cáo tuần" in r["query"]
    # tin được reply nằm trong cửa sổ K → không nối
    assert turn_of(run_all(state_of(ms), FakeLLM(by), Cfg(window_k=20)), "u")["context"]["retrieval"]["joined_reply"] is None


def test_budget_trim_order_never_cuts_linked():
    long = "nội dung rất dài " * 30
    ms = [msg(f"t{i:02d}", f"09:{i:02d}", "u_hung", f"@Nam làm việc số {i} nhé", mentions=mention("u_nam")) for i in range(16)]
    by = {m.id: {"ops": [create(m.id, ["u_nam"], f"Làm việc số {int(m.id[1:])}")]} for m in ms}
    ms += [msg(f"h{i}", f"10:{i:02d}", "u_nam", f"{long} {i}") for i in range(12)]
    ms += [msg("r", "11:00", "u_hung", "việc này nhớ thêm số liệu nhé", reply_to_id="t03")]
    t = turn_of(run_all(state_of(ms), FakeLLM(by), Cfg(context_token_budget=700)), "r")
    order = ["drop_details_anchor", "reduce_k", "drop_index", "drop_details"]
    kinds = [x.split(" ")[0] for x in t["context"]["trims"]]
    assert kinds == sorted(kinds, key=order.index) and kinds[0] == "drop_details_anchor" and "drop_details" in kinds
    assert src(t, "linked") == ["K1.4"] and t["context"]["tokens"]["b3a"] > 0       # LINKED không bao giờ bị cắt
    assert len(src(t, "index")) == 10 and len(src(t, "details")) == 1               # giữ ≥ 10 dòng index, ≥ 1 task chi tiết
    assert "<MEMBERS>" in t["context"]["prompt"]


# ---------------- detail_check (v6 mục 6.4) ----------------

def _index_state(op_for_u, extra=()):
    ms, by = six_tasks()
    ms += [msg("u", "11:00", "u_hung", "slide tổng kết sprint bỏ phần demo đi nhé")]
    by["u"] = op_for_u
    return state_of(ms), FakeLLM(by)


def test_detail_check_on_notes_for_index_task():
    upd = empty_op("UPDATE", target="@K1.1", evidence=[{"msg": "@u", "role": "content"}], notes="bỏ phần demo")
    s, llm = _index_state([{"ops": [upd]}, {"ops": [upd]}])
    t = turn_of(run_all(s, llm, Cfg()), "u")
    assert "K1.1" in src(t, "index")
    assert t["detail_check"]["tasks"] == ["K1.1"] and len(t["extract_calls"]) == 2
    block = t["extract_calls"][1]["extra_block"]
    assert '<TASK_DETAILS source="detail_check">' in block and "báo cáo tuần" in block and "final call" in block
    assert llm.calls[-1]["task_map"] == llm.calls[-2]["task_map"] and llm.calls[-1]["call"] == 2                     # T<n> giữ nguyên
    assert t["ops"][0]["accepted"] and s.tasks[0].notes == "bỏ phần demo"


def test_detail_check_not_triggered():
    cases = [empty_op("UPDATE", target="@K1.1", evidence=[{"msg": "@u", "role": "content"}], notes_add="thêm số liệu"),
             empty_op("UPDATE", target="@K1.1", evidence=[{"msg": "@u", "role": "due"}], due_kind="weekday", due_weekday=6),
             empty_op("UPDATE", target="@K1.6", evidence=[{"msg": "@u", "role": "content"}], notes="x"),  # K1.6 ở TASK_DETAILS
             empty_op("CANCEL", target="@K1.1", evidence=[{"msg": "@u", "role": "cancel"}])]
    for op in cases:
        s, llm = _index_state({"ops": [op]})
        t = turn_of(run_all(s, llm, Cfg()), "u")
        assert t["detail_check"] is None and len(t["extract_calls"]) == 1, op


# ---------------- Validate / schema / prompt ----------------

def test_evidence_msg_with_copied_line_is_accepted():
    """Model đôi khi chép cả dòng tin vào evidence.msg ('#1 [thứ 2 …] Hưng: …') — chỉ lấy id ở đầu."""
    ms = [msg("a", "09:00", "u_hung", "@Linh gửi báo cáo tuần nhé", mentions=mention("u_linh"))]
    op = create("a", ["u_linh"], "Gửi báo cáo tuần")
    op["evidence"] = [{"msg": "#1 [thứ 2 09/11 09:00] Hưng (u_hung): @Linh gửi báo cáo tuần nhé", "role": "assign"}]
    t = turn_of(run_all(state_of(ms), FakeLLM({"a": {"ops": [op]}}), Cfg()), "a")
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
    turns = run_all(s, FakeLLM({"a": {"ops": [op]}, "b": {"ops": [acc]}}), Cfg())
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
    t = turn_of(run_all(s, FakeLLM(by), Cfg()), "z")
    assert len(t["extract_calls"]) == 1 and t["ops"][0]["accepted"] and s.tasks[-1].status == "draft"


def test_schema_and_prompt():
    props = EXTRACT_JSON_SCHEMA["schema"]["properties"]
    assert list(props) == ["message_type", "ops"]
    assert "old_task_not_in_prompt" not in json.dumps(props)
    p = PROMPTS["extract"]
    assert "TASK_INDEX" in p and "CANDIDATE_TASKS" not in p and "SPEAKER" not in p and "ADDRESSEE" not in p
    s = state_of(six_tasks()[0])
    run_all(s, FakeLLM(six_tasks()[1]), Cfg())
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
    turns = run_all(s, FakeLLM(by), Cfg())
    assert s.tasks[0].assignee_ids == ["u_nam", "u_tuan"]          # tên → khớp thành viên
    assert turn_of(turns, "c")["ops"][0]["fields"] == ["assignee_refs"]


def test_unknown_user_id_is_dropped():
    ms = [msg("a", "09:00", "u_hung", "@Linh gửi báo cáo tuần nhé", mentions=mention("u_linh"))]
    s = state_of(ms)
    t = turn_of(run_all(s, FakeLLM({"a": {"ops": [create("a", ["u_linh", "u_khach"], "Gửi báo cáo tuần")]}}), Cfg()), "a")
    assert s.tasks[0].assignee_ids == ["u_linh"] and any(n.startswith("assignee_unknown") for n in t["ops"][0]["notes"])


def test_notes_add_appends_and_notes_replaces():
    ms = [msg("a", "09:00", "u_hung", "@Linh làm banner Black Friday", mentions=mention("u_linh")),
          msg("b", "09:30", "u_hung", "banner thêm bản dọc cho story nhé", reply_to_id="a"),
          msg("c", "10:00", "u_hung", "bỏ hết yêu cầu cũ, chỉ cần bản ngang", reply_to_id="a")]
    by = {"a": {"ops": [create("a", ["u_linh"], "Làm banner Black Friday", notes="3 kích thước")]},
          "b": {"ops": [_upd("K1.1", "b", "content", notes_add="thêm bản dọc cho story")]}}
    s = state_of(ms)
    run_all(s, FakeLLM(by), Cfg())
    assert s.tasks[0].notes == "3 kích thước; thêm bản dọc cho story"
    by["c"] = {"ops": [_upd("K1.1", "c", "content", notes="chỉ cần bản ngang")]}
    s = state_of(ms)
    run_all(s, FakeLLM(by), Cfg())
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
    turns = run_all(s, FakeLLM(by), Cfg())
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
    turns = run_all(s, FakeLLM({"a": {"ops": [op]}, "b": {"ops": [lui]}}), Cfg())
    assert any("2026-11-19" in n for n in turn_of(turns, "a")["ops"][0]["notes"])
    assert s.tasks[0].due_date == "2026-11-23"
    assert any("due_rolled_forward" in n for n in turn_of(turns, "b")["ops"][0]["notes"])
