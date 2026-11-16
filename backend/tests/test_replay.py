"""Phát lại (detect.replay) và chạy theo split, FakeLLM, không gọi mạng."""
from __future__ import annotations

from detect.config import Config
from detect.dataset import load_dataset, resolve_dir
from detect.llm import FakeLLM
from detect.replay import ReplayLLM, pick_turns
from detect.run import init_state
from test_context import PROMPTS, four_tasks, msg, run_all, state_of, update_due  # noqa: F401


def _base():
    ms, by = four_tasks()
    ms.append(msg("u", "11:00", "u_hung", "slide sprint lùi sang thứ 6 nhé", reply_to_id="d"))
    by["u"] = {"ops": [update_due("K4", "u", 5)]}
    base = state_of(ms)
    run_all(base, FakeLLM(by), Config())
    return ms, base


def _summary(state):
    return sorted((t.anchor_message_id, tuple(t.assignee_ids), t.due_date) for t in state.tasks)


def test_replay_without_selected_turns_reproduces_base():
    ms, base = _base()
    s = state_of(ms)
    llm = ReplayLLM(FakeLLM({}), s, base, selected={})
    run_all(s, llm, Config())
    assert llm.real_calls == 0 and _summary(s) == _summary(base)


def test_replay_calls_real_llm_and_remaps_task_ids():
    """Lượt "a" được chọn và LLM thật không tạo task → mọi task sau lùi số (K4 cũ thành K3) nhưng UPDATE phát lại
    vẫn trúng đúng task "slide sprint" nhờ ghép theo tin anchor."""
    ms, base = _base()
    s = state_of(ms)
    llm = ReplayLLM(FakeLLM({}), s, base, selected={"a": "có việc"})
    run_all(s, llm, Config())
    assert llm.real_calls == 1
    assert [t.anchor_message_id for t in s.tasks] == ["b", "c", "d"]
    slide = next(t for t in s.tasks if t.anchor_message_id == "d")
    assert slide.task_id == "K3" and slide.due_date == "2026-11-13"


def test_split_and_pick_turns():
    ds = load_dataset("data/eval", "dev")
    keys = {m.conversation_id for m in ds["messages"]}
    assert keys == {"ch_app", "ch_data", "ch_qa", "ch_infra"} and ds["gold"]
    assert all(g.conversation_key in keys for g in ds["gold"])
    state = init_state(ds)
    picked = pick_turns(resolve_dir("data/eval"), {e.id for e in state.events}, 10, 7)
    assert sum(1 for v in picked.values() if v == "không có việc (mẫu)") == 10
    assert sum(1 for v in picked.values() if v == "có việc") > 0
    assert all(i in {e.id for e in state.events} for i in picked)
