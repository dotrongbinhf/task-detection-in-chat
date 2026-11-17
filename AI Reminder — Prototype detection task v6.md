# AI Reminder — v6: đo cho đúng trước · người nhận bằng user_id · danh mục task · đợi hội thoại yên

Nov 17, 2026 · thay đổi trên prototype v5.1 đã implement (`backend/` + `frontend/`)

Tài liệu này đủ để implement trong một phiên Claude Code mới. Đọc kèm `backend/DECISIONS.md` (mục *v5.1* là trạng thái code hiện tại), `README.md` (luồng mỗi lượt), `backend/data/LABELING.md`. Các tài liệu v3–v5 chỉ cần khi muốn biết lý do của phần không đổi (sổ cái, Validate, người nhận duyệt).

Giao thức prompt giữ quy ước cũ: thẻ khối, trường JSON, enum, hướng dẫn cho LLM viết **tiếng Anh**; nội dung chat, `title`, `notes`, `due_text` giữ tiếng Việt.

## 0. Vì sao có v6

Rà soát v5.1 trên `backend/runs/eval51_4omini` (gpt-4o-mini, cả `data/eval`: 1.617 lượt, 102 nhãn có người nhận) cho thấy ba nhóm vấn đề.

**Số đo chưa đủ tin để dẫn hướng.**

- Bộ trích bằng regex (`heuristic.py`) đạt recall 86% và người nhận đúng 95%, cao hơn gpt-4o-mini (75% và 71%). 72/106 tin giao việc có @mention, 82/106 có "nhé / giúp". Dữ liệu quá khuôn mẫu.
- 0 báo nhầm trên khoảng 1.370 lượt âm và 34 hard negative. Precision 96% thực chất chỉ đo task trùng.
- Link accuracy báo 80%, nhưng 21/24 ca "model không làm gì" là tin "dạ" khi task đã có người nhận, tức không cần đổi gì.
- Báo cáo ghi recall 75%, trong khi nháp đúng việc, đúng người, đúng hạn chỉ đạt 51/102.
- `dev` có 17 nhãn (1 nhãn = 6 điểm). Split `train` không dùng vào đâu vì không có classifier.
- Chưa đo dao động giữa các lần chạy; khoảng tin cậy Wilson coi các nhãn cùng hội thoại là độc lập.

**Điểm yếu thật là người nhận.** 32/102 nhãn hỏng vì người nhận: 22 task sai người (cả 22 có `SPEAKER:#n`, trong đó 16 ca người giao bị gán thành người nhận) và 10 task không hiện vì thiếu người (4 ca `ADDRESSEE` trong nhóm, 4 ca tự hứa bị để trống, 1 ca tên không dấu không @, 1 ca chưa ai nhận). Prompt không gỡ tên; gốc lỗi là cơ chế tham chiếu gián tiếp.

**Nhiều phần phức tạp không đem lại gì đo được.**

- Tìm ứng viên (BM25 + embedding + RRF + các danh sách tay tiếng Việt): mỗi hội thoại chỉ có 1–13 task, prompt chứa 5, nên candidate recall 96% gần như tự có.
- `target_check`: 2/1.617 lượt, tìm ra 0 task.
- Cổng: đã quyết không làm (DECISIONS v5.1 #68) nhưng code, prompt, UI vẫn còn.
- Reaction: cả bộ có 9 reaction; chỉ đổi được sổ cái khi việc chưa có người nhận và ai đó thả 👍.
- Gộp tin 5 giây: 35/1.617 lượt được gộp.

Nguyên tắc của v6: **sửa phần đo trước, rồi mới gọt pipeline; mỗi thay đổi pipeline đo riêng trên bộ dữ liệu mới.** Trong pipeline: model trả thẳng `user_id`; op ghi "thay đổi" chứ không ghi "trạng thái mới đầy đủ"; code giữ phần so sánh, gộp, kiểm tra.

Giữ nguyên: sổ cái và vòng duyệt (nháp → người nhận duyệt, đề xuất thay đổi cho task đã duyệt), Validate (id phải có trong prompt, op phải dựa vào tin mới, chống trùng anchor, `target_not_open`), `message_type` điền trước, task được link qua reply, prompt caching, cache đĩa.

## 1. Tóm tắt thay đổi

| # | v5.1 (đã chạy) | v6 | Lý do |
|---|---|---|---|
| 1 | Eval: recall, precision, link accuracy, Wilson | Thêm **nháp đúng hoàn toàn**, chấm op theo lượt, bootstrap theo hội thoại, so hai lần chạy theo từng nhãn; sửa link accuracy và ghép nhãn | Mục 0 |
| 2 | `data/eval`: train / dev / test, lượt âm dễ, `accept` luôn là lượt dương | Gộp `train` vào `dev`; thêm lượt âm khó, hội thoại 40+ task mở; tách `confirm` khỏi lượt dương | Mục 0 |
| 3 | Người nhận: `SPEAKER:#n`, `ADDRESSEE`, tên; 4 chốt chặn (#57, #58, #71b, #72a/b) | Dòng tin có `user_id` tác giả, khối `<MEMBERS>`; model trả `user_id` | 32/102 nhãn hỏng vì người nhận |
| 4 | UPDATE: `changes[{field, value (chuỗi JSON), evidence_msg}]` | UPDATE dùng trường phẳng như CREATE; danh sách dùng `*_add` / `*_remove` | JSON trong chuỗi JSON dễ hỏng; thay cả danh sách mà không thấy giá trị cũ thì xoá nhầm (#58) |
| 5 | `<CANDIDATE_TASKS>`: 3 matched (BM25 + embedding, RRF) + recent, tổng 5 | Ba tầng: `<LINKED_TASKS>`, `<TASK_DETAILS>` (≈ 5 task), `<TASK_INDEX>` (dòng gọn, ≤ 30). Chỉ còn embedding để xếp hạng | Kho nhỏ; danh sách tay không phủ hết |
| 6 | Lần gọi 2 = `target_check` | Lần gọi 2 = `detail_check`: op sửa nội dung trên task chỉ có dòng gọn | `target_check` tìm ra 0 task |
| 7 | Reaction là sự kiện, có `#n` riêng | Reaction không sinh lượt; in kèm dòng tin | 9 reaction cả bộ; 👍 mơ hồ |
| 8 | Gộp tin trong 5 giây từ tin đầu | Đợi hội thoại yên 60 giây (trần 5 phút / 10 tin) | Giảm số lần gọi; LLM thấy cả cụm tin |
| 9 | Cổng tuỳ chọn (`gate_mode`) | Xoá | Đã quyết không làm |
| 10 | `expired` / `stale` / `closed` | Một trạng thái `closed` + `closed_reason` | Ba tên cho một việc |
| 11 | `heuristic.py`, `HashEmbedder`, `replay.py` | Xoá; chạy song song theo hội thoại (`--jobs`) | Phải sửa theo mỗi lần đổi schema; phát lại chỉ tồn tại vì chạy tuần tự chậm |
| 12 | Khối lịch 14 ngày + hạn theo trường cấu trúc | Thí nghiệm một lần (mục 9), giữ bên thắng | Lịch tốn 175 token mà model bị cấm tính ngày |

## 2. Luồng mỗi lượt (mới)

Một lượt = các tin của một hội thoại đến liền nhau cho tới khi hội thoại yên `quiet_seconds`.

1. **Hết hạn & thao tác duyệt** tới thời điểm của lượt.
2. **Dựng ngữ cảnh.** `<MEMBERS>` · `<CALENDAR>` (hoặc dòng `Today`, mục 9) · `<LINKED_TASKS>` · `<TASK_DETAILS>` · `<TASK_INDEX>` · `<RECENT_HISTORY>` · `<NEW_MESSAGES>`.
3. **Bộ trích (LLM)** trả `{message_type, ops}`.
4. **`detail_check`** (hiếm): op thay `notes` / `title` của task chỉ có trong `<TASK_INDEX>` → gọi lần 2 kèm chi tiết task đó.
5. **Validate** rồi áp vào **sổ cái**.
6. Task có người nhận hiện **nháp** cho người đó duyệt.

Không còn: cổng, reaction là sự kiện, `target_check`, tìm kiếm BM25.

## 3. Hàng đợi: đợi hội thoại yên

`run.next_batch` đổi cách gộp:

- Lấy sự kiện đầu chưa xử lý; nối thêm sự kiện kế tiếp cùng `conversation_key` chừng nào **khoảng cách tới tin liền trước ≤ `quiet_seconds`** (60), tổng thời lượng lượt ≤ `max_wait_seconds` (300) và số tin ≤ `max_batch_messages` (10).
- Thời điểm của lượt giữ là `batch[-1].at` (hạn vẫn quy theo giờ của tin). Độ trễ thật = độ trễ đo + `quiet_seconds`; báo cáo ghi rõ điều này ở dòng độ trễ.
- Bỏ `coalesce_seconds`.

**Thao tác duyệt trong dataset.** Tin 09:00–09:06 nay có thể thành một lượt xử lý lúc 09:06, trong khi dataset ghi `act 09:05 … approve`. Quy tắc mô phỏng: `catch_up` **không áp** thao tác có `message_id` chưa nằm trong `state.processed`; thao tác đó được áp ngay sau lượt chứa tin ấy (nghĩa: "duyệt khi nháp hiện ra, không sớm hơn `at`"). Thao tác trỏ thẳng `task_id` / `change_id` (từ UI) giữ như cũ.

`label.turns` dùng chung `next_batch` nên `turn_labels.jsonl` phải dựng lại (`build_eval`).

## 4. Reaction không còn là sự kiện

- `run.build_events` chỉ sinh sự kiện `message`. Bỏ id `<message_id>#r<n>`, nhánh `reaction_not_relevant`, link qua reaction (`linked_message_ids` chỉ đi theo `reply_to_id`), dòng reaction có `#n` riêng.
- Reaction in kèm dòng tin (ở cả lịch sử lẫn tin mới), chỉ reaction có `created_at` ≤ thời điểm lượt: `#4 [09:00] Minh (u_minh): … [👍 Nam, ❤️ Linh]`.
- Prompt: reaction không tạo, không sửa task. Bỏ câu "A 👍/ok reaction … = the sender accepts it" ở quy tắc 3.
- UI: vẫn thả được reaction (`add_user_reaction` chỉ gắn vào tin), không sinh lượt.
- Dữ liệu: xem mục 10.4.

Mất: ca nhận việc chỉ bằng 👍. Người đó vẫn được gán khi nhắn chữ.

## 5. Người nhận bằng `user_id`

### 5.1 Prompt

- Dòng tin: `#12 [thứ 2 23/11 09:00] Minh (u_minh): @Hà dựng dashboard… (@Hà=u_ha) ↩#7`. Thêm `(user_id)` sau tên tác giả; giữ hậu tố mention.
- Khối `<MEMBERS>` đặt ngay sau dòng `Conversation type` (tĩnh theo hội thoại, tốt cho cache):

  ```
  <MEMBERS>
  u_minh Minh (anh Minh, Minh Hoàng)
  u_ha Hà (Ha)
  </MEMBERS>
  ```

  Hội thoại có hơn `members_max` (30) người: chỉ liệt kê tác giả các tin trong prompt, người được @mention, người nhận / người giao của task trong prompt.

### 5.2 Schema và quy tắc

- Mọi trường người nhận chứa `user_id` lấy từ `<MEMBERS>` hoặc từ dòng tin. Bỏ `SPEAKER:#n`, `ADDRESSEE`.
- Quy tắc 8 của prompt viết lại: người làm là người được nhờ / được @mention / tự nhận; **người giao không phải người nhận** trừ khi tự nói sẽ làm; "em / mình / anh / chị + sẽ làm" → `user_id` của tác giả tin đó; tên trùng từ chỉ thời gian (Mai, Năm) đọc theo ngữ cảnh; không chắc thì để trống.

### 5.3 Validate

- `user_id` không thuộc thành viên hội thoại → thử khớp tên như `resolve_assignees` hiện tại (bỏ dấu, bỏ xưng hô, đúng một người); không khớp → bỏ id đó, ghi `assignee_unknown`.
- **Bỏ cả bốn chốt chặn** #57 (`speaker_is_assigner`), #58 (nhận việc thì gộp), #71b (`ADDRESSEE` trong nhóm), #72a/b (vai trò bằng chứng). Ngữ nghĩa `add` / `remove` thay cho #58.
- Thêm lại **một** chốt chặn chỉ khi đo trên `dev` thấy ≥ 3 ca "người giao bị gán": `assignees_add` chứa tác giả tin anchor của task `kind = assigned` đang có người nhận khác, và op không có bằng chứng vai trò `assignee` → bỏ id đó (`assigner_add_dropped`).

## 6. Task trong prompt: ba tầng

Mỗi task xuất hiện đúng một lần, ở tầng cao nhất mà nó thuộc về. `T<n>` đánh liên tục qua cả ba khối.

### 6.1 `<LINKED_TASKS>` — như v5.1

Task có tin bằng chứng mà tin mới reply vào (đi ngược chuỗi reply ≤ `reply_chain_max`). Mọi trạng thái. Chi tiết đầy đủ. Không bao giờ bị cắt.

### 6.2 `<TASK_DETAILS>` — vài task có khả năng liên quan nhất

Nguồn, theo thứ tự, bỏ task đã ở tầng trên:

1. `detail_recent_max` (3) task đang mở nhắc gần nhất (`last_mentioned_at`).
2. `detail_similar_max` (2) task đang mở có cosine embedding cao nhất với tin mới. **Không dùng ngưỡng tuyệt đối** (DECISIONS #41: không tách được); chỉ lấy theo hạng. Không có embedder (unit test) → bỏ nguồn này.

Văn bản truy vấn = nội dung tin mới, bỏ @mention; nếu tin mới reply một tin nằm ngoài cửa sổ K thì nối thêm nội dung tin đó (giữ hành vi `joined_reply`). Văn bản task = `TaskStore.embed_text` hiện tại (tiêu đề + ghi chú + tin giao việc).

Định dạng mỗi task:

```
T3 [approved | assigned] "Dựng dashboard doanh thu theo tỉnh" — assignee: Hà (u_ha) — due: 2026-11-27 (thứ 6)
    notes: thêm filter kênh bán
    #2 [thứ 2 23/11 09:00] Minh (u_minh): @Hà dựng dashboard doanh thu theo tỉnh nhé, cuối tuần xong (@Hà=u_ha)
```

Dòng đầu giữ phần `PENDING APPROVAL` như `_task_head` hiện tại. Thêm dòng `notes:` khi có. Kèm tin giao việc (anchor) nếu chưa có trong lịch sử / tin mới.

### 6.3 `<TASK_INDEX>` — danh mục gọn

- Mọi task còn lại ở trạng thái `candidate` / `draft` / `approved` hoặc có đề xuất đang chờ, **cộng** task `declined` còn trong `declined_suppress_days`.
- Xếp theo `last_mentioned_at` giảm dần, tối đa `index_tasks_max` (30).
- Một dòng một task, không kèm bằng chứng, người giao, ghi chú:

  ```
  T7 "Làm báo cáo churn tháng 11" — An (u_an) — due 2026-12-01 (thứ 3)
  T8 "Clean bảng customer trùng email" — (none) — no due
  T9 [declined] "Viết unit test cho module voucher" — Nam (u_nam) — no due
  ```

- **Phần tràn.** Số task đủ điều kiện vượt `index_tasks_max`: giữ `index_tasks_max − index_overflow_max` (25) task gần nhất, rồi lấp `index_overflow_max` (5) chỗ cuối bằng task có cosine cao nhất với tin mới trong phần còn lại. Không có embedder → chỉ theo độ mới.

Prompt nêu rõ: `<TASK_INDEX>` là danh mục để **nhận ra** task; UPDATE / CANCEL được nhắm bất kỳ `T<n>` nào trong cả ba khối.

### 6.4 Lần gọi 2: `detail_check`

Kích hoạt bởi **cấu trúc op**, không trông vào việc LLM tự xin: sau lần gọi 1, có op UPDATE đặt `notes` hoặc `title` trên task chỉ nằm trong `<TASK_INDEX>` → gọi lại với nguyên prompt lần 1 + khối

```
<TASK_DETAILS source="detail_check">
(chi tiết các task đó, định dạng mục 6.2)
</TASK_DETAILS>
This is the final call: return ops only.
```

Dùng lại `context.extend_context` (đánh số `T<n>` giữ nguyên vì task đã có trong prompt; tin bằng chứng mới đánh `#n` nối tiếp). Op `notes_add`, đổi hạn, đổi người, CANCEL **không** kích hoạt. Tối đa 2 lần gọi mỗi lượt.

### 6.5 Ngân sách token

`context_token_budget` = 3.500. Thứ tự cắt: tin anchor kèm `<TASK_DETAILS>` → giảm K (≥ 5) → bỏ dòng `<TASK_INDEX>` từ cuối (giữ ≥ 10) → bỏ task `<TASK_DETAILS>` từ cuối (giữ ≥ 1). Không bao giờ cắt `<LINKED_TASKS>`, `<MEMBERS>`.

### 6.6 Bỏ khỏi `store.py` / `text.py` / `context.py`

BM25, RRF, `PEOPLE_BONUS`, `RECENCY_BONUS`, `names_in_query`, `resolve_people`, `document`, `is_matched`, `search`, `candidates`, `SearchHit`; `CHANGE_WORDS_RE`, `retrieval_query` (thay bằng hàm dựng văn bản truy vấn ngắn); `text.terms`, `SEARCH_STOPWORDS`, `keywords`, `STOPWORDS` nếu không còn nơi nào dùng (`HONORIFICS`, `norm` còn dùng cho khớp tên). `TaskStore` còn: `linked`, `recent`, `similarity`, `embed_text`, và hàm chọn ba tầng.

## 7. Schema đầu ra và prompt

### 7.1 Schema (`ledger_ops`, strict, mọi trường luôn có mặt, không dùng = null)

```json
{"message_type": "no_change | new_task | task_in_prompt",
 "ops": [{
   "op": "CREATE | UPDATE | CANCEL",
   "target": "T7",
   "anchor": "#12",
   "kind": "assigned | self_commit | open_ask",
   "title": "…",
   "notes": "…",
   "notes_add": "…",
   "assignees": ["u_ha"],
   "assignees_add": ["u_nam"],
   "assignees_remove": ["u_ha"],
   "due_text": "thứ 6", "due_kind": "…", "…các trường hạn như v5.1…": null,
   "due_shift_days": 2,
   "evidence": [{"msg": "#12", "role": "assign | accept | assignee | due | content | cancel"}]
 }]}
```

| Trường | CREATE | UPDATE | CANCEL |
|---|---|---|---|
| `target` | null | bắt buộc | bắt buộc |
| `anchor`, `kind` | bắt buộc | `kind`: đặt lại nếu khác null | null |
| `title` | bắt buộc | khác null = thay tiêu đề | null |
| `notes` | tuỳ | khác null = **thay** ghi chú (kích hoạt `detail_check` nếu task chỉ ở index) | null |
| `notes_add` | null | khác null = **nối thêm** vào ghi chú | null |
| `assignees` | danh sách người nhận | null (không dùng) | null |
| `assignees_add` / `_remove` | null | thêm / bớt người nhận | null |
| trường hạn | hạn của việc | `due_kind` null = không đổi; `"none"` = bỏ hạn; giá trị khác = hạn mới | null |
| `due_shift_days` | null | "lùi thêm 2 ngày" → 2; code cộng vào hạn lịch hiện tại | null |
| `evidence` | ≥ 1 | ≥ 1 tin trong tin mới | ≥ 1, vai trò `cancel` |

Bỏ `changes[]`, `Change`, `value` dạng chuỗi JSON, `evidence_msg` theo từng thay đổi.

### 7.2 Validate cho UPDATE

- Mỗi trường khác null thành một thay đổi; bằng chứng của thay đổi = tin đầu tiên trong `evidence` thuộc tin mới có vai trò khớp (`due` → `due`; người nhận → `accept` / `assignee`; nội dung → `content`), không có thì tin đầu tiên thuộc tin mới.
- Người nhận mới = (hiện tại ∪ `assignees_add`) − `assignees_remove`.
- `due_shift_days` trên task không có hạn lịch → bỏ, ghi `due_shift_no_base`.
- Giữ nguyên: `no_op_change`, `declined_change_suppressed`, task `approved` thì thành đề xuất chờ duyệt, `not_from_new_message`, `target_not_open`, `duplicate_anchor`, `resolve_msg` (model chép cả dòng tin).
- Đổi `expired` / `stale` thành `closed` (mục 8.2).

### 7.3 `prompts/extract.txt` — những chỗ phải viết lại

- Quy tắc 3, 4: thay mô tả `CANDIDATE_TASKS [matched] / [recent]` bằng ba khối mục 6; giữ câu "không thấy task trong prompt thì trả ops rỗng, không UPDATE task khác, không CREATE lại".
- Quy tắc 8: mục 5.2.
- Thêm: reaction không đổi task (mục 4).
- Phần OUTPUT FORMAT: bảng mục 7.1 thay cho đoạn "UPDATE uses changes…".
- Quy tắc 9 (hạn): giữ, thêm `due_shift_days`; có thể đổi sau thí nghiệm mục 9.
- `PROMPT_VERSION = "v6"`.

## 8. Dọn dẹp

### 8.1 Xoá cổng

`detect/gate.py`, `prompts/gate.txt`, `llm.gate` + `GateResult`, `gate_system` / `gate_user` trong `BuiltContext`, `gate_mode` / `gate_threshold` / `gate_window_k`, đường cong cổng và chi phí cổng trong `eval.py`, kiểm tra logprobs trong `llm.probe`, sắp xếp theo `gate_mode` trong `compare.py`, bước cổng trên UI.

### 8.2 Vòng đời

`expired`, `stale`, `closed` → một trạng thái `closed`, thêm `closed_reason` ∈ `candidate_ttl | draft_ttl | past_due | undated | declined_ttl`. Các tham số TTL giữ nguyên giá trị. Sửa `ledger.apply_expiry`, `models`, `eval.py`, `frontend/src/lib/types.ts`, `LedgerPanel.tsx`.

### 8.3 Xoá bộ trích giả lập và phát lại

`detect/heuristic.py`, `HeuristicLLM`, `embed.HashEmbedder`, `--mode heuristic`, `detect/replay.py`. Không có `OPENAI_API_KEY`: CLI và server trả lỗi rõ ràng; UI hiện thông báo cần key. Unit test vẫn dùng `FakeLLM` / `FakeEmbedder`.

### 8.4 Chạy song song theo hội thoại

`python -m detect.run --jobs N` (mặc định 4): mỗi hội thoại chạy trong một `EngineState` riêng trên một luồng, xong thì gộp. Kết quả từng hội thoại không phụ thuộc thứ tự chạy.

- `task_id` = `K<số thứ tự hội thoại>.<n>`, `change_id` tương tự; sửa chỗ `int(t.task_id[1:])` trong `store.recent` thành so theo `created_turn`.
- Gộp: nối `tasks`, `changes`, `turns`, `trace`; `turn` đánh lại theo thời gian.
- Thay thế quyết định v5.1 #70 ("không chạy song song").

## 9. Thí nghiệm hạn (một lần, không để lại chế độ)

Sau khi mục 5–7 đã ổn, so hai cách trên `dev`, giữ bên có "hạn lịch đúng" cao hơn, xoá bên kia:

- **D1** — giữ trường hạn cấu trúc; thay khối lịch 14 ngày bằng một dòng `Today: thứ 4 2026-11-25` (tiết kiệm ≈ 160 token mỗi lượt).
- **D2** — giữ khối lịch; model trả `due_date` ISO + `due_text` + `due_kind` ∈ `date | event | none`; code chỉ kiểm tra ngày hợp lệ và nằm trong [ngày của tin, +365 ngày]; bỏ `resolve_due` và các trường `due_weekday`, `due_week_offset`…

Hoà (chênh ≤ 1 nhãn, trong mức nhiễu đo ở mục 11.5) → giữ D1.

## 10. Dữ liệu (`data/eval_src`, `build_eval.py`, `LABELING.md`)

Người viết và gán nhãn vẫn là Claude (quyết định cũ của người dùng); chưa dùng chat thật.

### 10.1 Split

Chuyển mọi hội thoại `train` sang `dev`. Còn `dev` (13 hội thoại, ≈ 74 nhãn) để chỉnh và `test` (6 hội thoại, ≈ 32 nhãn) chỉ chạy khi báo cáo cuối. Hội thoại mới thêm chia sao cho `test` ≥ 40 nhãn.

### 10.2 Bớt khuôn mẫu

- Tin giao việc có @mention ≤ 50%; có "nhé / giúp / nha" ≤ 40% (đo bằng script, in trong `label stats`).
- Thêm cách giao việc khác: câu hỏi ("em xem được cái này không"), câu kể ("cái này chắc phải nhờ Hà"), giao trong đoạn tin dài nhiều ý, không dấu, viết tắt, xen tiếng Anh.
- Giờ tin có giây, khoảng cách thật (cụm 3–4 tin trong 30 giây, quãng lặng dài) để hàng đợi mới được dùng tới.
- Độ dài tin: trung vị ≥ 8 từ (hiện 5).

### 10.3 Lượt âm khó (mỗi loại ≥ 5 ca, đánh `## hn` kèm tag loại)

Kể lại việc đã xong; hỏi về việc ("ai làm X chưa?", "cái Y tới đâu rồi"); câu điều kiện ("nếu kịp thì…"); phủ định ("không cần Linh làm đâu"); việc của người ngoài nhóm / của đội khác; trích lại lời người khác; ý tưởng chưa chốt ("hay là mình làm…"); nhắc việc đã có (không tạo task mới); đùa.

`hard_negatives.txt` thêm cột tag (`<message_id>\t<tag>`); báo cáo ghi báo nhầm theo tag.

### 10.4 Reaction và `confirm`

- Nhãn dòng mới `## <gold>:confirm`: tin xác nhận không đổi sổ cái ("dạ", "ok anh" khi task đã có đúng người nhận đó). `accept` chỉ dùng khi tin đó **làm thay đổi** người nhận.
- `gold.jsonl` thêm `confirm_message_ids`; `supporting_message_ids` chỉ gồm tin làm đổi sổ cái.
- Lượt chỉ có `confirm` → `label = 0`, `kinds = ["confirm"]`. Cập nhật `label.KINDS`, `LABELING.md` (bảng mục 2 và ca biên).
- Chú thích trên dòng reaction bị bỏ khỏi `supporting`. Nhãn nào chỉ có người nhận nhờ reaction: sửa nguồn (thêm tin chữ nhận việc, hoặc đổi nhãn thành không có người nhận). Tag `reaction_accept`, `reaction_link` đổi nghĩa: "reaction không được làm đổi sổ cái".

### 10.5 Hội thoại nhiều task

Thêm hai hội thoại (một ở `dev`, một ở `test`), mỗi cái có lúc ≥ 40 task mở, dài ≥ 10 ngày (để TTL 7 và 14 ngày chạy thật), có: tiêu đề gần giống nhau ("PR #482" / "PR #490", báo cáo tuần của hai người), nhắc việc cũ không reply, sửa nội dung (`bỏ phần…`) trên việc cũ. Tag: `index_overflow`, `similar_titles`, `detail_check`, `ttl_long`.

### 10.6 Sửa tài liệu

`LABELING.md`, `README.md`, DECISIONS: số hội thoại hiện ghi 21 (10 / 4 / 7), thực tế 19 (9 / 4 / 6). Ghi lại số đúng sau khi dựng lại.

## 11. Đánh giá (`eval.py`, `compare.py`, `report.md`)

### 11.1 Chỉ số đầu-cuối

`strict_correct` = nhãn có người nhận, ghép được task đã hiện, **và** người nhận trùng, **và** hạn trùng (khi nhãn có hạn lịch), **và** trạng thái huỷ trùng (khi nhãn `cancelled`). Đặt ở dòng đầu mục *Phát hiện*: "Nháp đúng hoàn toàn". Kiểm: trên `runs/eval51_4omini` ra 51/102.

### 11.2 Ghép nhãn với task

Khoá sắp xếp cặp: trùng anchor → số tin bằng chứng chung → **số người nhận chung** → **số từ chung giữa hai tiêu đề (bỏ dấu)** → thời điểm tạo. Hai nhãn chung một tin anchor ("Trâm export…, Đức kiểm tra…") không còn bị tráo.

### 11.3 Link accuracy

Đơn vị chỉ gồm lượt có tin thuộc `supporting_message_ids` (đã loại `confirm` ở 10.4). Trước khi dữ liệu mới có: lượt mà `kinds` ⊆ {`accept`} và người gửi đã là người nhận của task → đếm riêng "không cần đổi", không tính vào mẫu số. Kiểm: trên `eval51_4omini` "không làm gì" giảm từ 24 xuống khoảng 3.

### 11.4 Chấm op theo lượt

Dùng `turn_labels.jsonl`:

- **Lượt báo nhầm** = lượt `label = 0` có ít nhất một op được áp làm đổi trường / tạo task / huỷ task (op chỉ bổ sung bằng chứng không tính). Báo tỉ lệ trên mọi lượt âm, và riêng theo tag hard negative.
- **Lượt bỏ sót** = lượt `label = 1` không có op nào được áp.
- **Đúng loại** = trong lượt dương có op được áp, loại op khớp `kinds`: `assign` / `self_commit` / `open_ask` ↔ CREATE; `due` ↔ đổi hạn; `accept` / `assignee` ↔ đổi người nhận; `content` ↔ `title` / `notes` / `notes_add`; `cancel` ↔ CANCEL.

### 11.5 Khoảng tin cậy và nhiễu

- Các chỉ số chính (recall, precision, nháp đúng hoàn toàn, người nhận đúng, hạn đúng, lượt báo nhầm): **bootstrap theo hội thoại**, 2.000 lần lấy mẫu lại, seed cố định, báo khoảng 2,5–97,5%. Bỏ cờ "chưa đủ để kết luận" theo n ≥ 30; thay bằng in khoảng. `wilson` chỉ còn dùng cho bảng theo tag.
- `detect.run --seed N` (đưa vào `seed` của lời gọi LLM, nằm trong khoá cache đĩa). Chạy `dev` với 3 seed một lần cho mỗi model tham chiếu để biết mức nhiễu; ghi vào DECISIONS.
- `python -m detect.compare --pair runs/A runs/B`: in nhãn **được thêm** / **bị mất** (theo `gold_id`) cho recall và nháp đúng hoàn toàn, thay cho so hai tỉ lệ. Chênh lệch nhỏ hơn mức nhiễu thì ghi "trong mức nhiễu".

### 11.6 Báo cáo

Bỏ: đường cong cổng, dòng `target_check`, nguồn `matched` / `recent`. Candidate recall chia theo `linked` / `details` / `index` / bỏ lỡ. Thêm: số lượt `detail_check`, kích thước lượt (số tin mỗi lượt p50 / p95), số task trong `<TASK_INDEX>` p50 / p95 / max.

## 12. Tham số (`backend/config.yaml`)

Bỏ: `gate_mode`, `gate_threshold`, `gate_window_k`, `coalesce_seconds`, `evidence_per_task`, `candidate_tasks_max`, `matched_tasks_max`, `matched_min_keyword_overlap`, `search_max_results`, `semantic_floor`, `rrf_k`, `target_check_on_update`, `semantic_min_similarity`, `target_check_max_candidates`.

Thêm:

```yaml
quiet_seconds: 60
max_wait_seconds: 300
max_batch_messages: 10
members_max: 30
detail_recent_max: 3
detail_similar_max: 2
index_tasks_max: 30
index_overflow_max: 5
context_token_budget: 3500
```

Giữ: `draft_min`, `window_k`, `reply_chain_max`, `linked_tasks_max`, `linked_evidence_per_task`, `message_max_tokens`, các TTL, `llm_max_retries`, `search_lookup_days` (phạm vi task được link), `prompt_cache_key`, `pricing` (bỏ dòng `heuristic`).

## 13. Thứ tự làm — dừng sau mỗi bước để người dùng xem

| Bước | Việc | Xong khi |
|---|---|---|
| **A. Eval** | Mục 11 (trừ phần cần dữ liệu mới). Không gọi LLM. | Chạy lại `detect.eval` trên `runs/eval51_4omini`: nháp đúng hoàn toàn 51/102; "không làm gì" còn ≈ 3; `pytest` xanh. |
| **B. Dữ liệu** | Mục 10. | `build_eval` chạy thông; `label stats` in các tỉ lệ ở 10.2, 10.3, 10.5 đạt ngưỡng. |
| **B′. Mốc** | Chạy **code v5.1 hiện tại** (chỉ kèm sửa eval của bước A) trên dữ liệu mới: `dev`, 3 seed → `runs/base_v51_s1..3`. | Có mức nhiễu và mốc để so mọi bước sau. |
| **C1. Dọn** | Mục 8.1, 8.2, 8.3, 8.4; mục 4 (reaction). | `dev` so với mốc: không chỉ số chính nào xấu đi quá mức nhiễu. |
| **C2. Người nhận + schema** | Mục 5, 7. | Báo cáo `--pair` so với C1; ghi vào DECISIONS số ca "người giao bị gán" (quyết định có thêm chốt chặn 5.3 không). |
| **C3. Ba tầng task** | Mục 6. | So với C2; candidate recall theo tầng; hội thoại 40+ task chạy không bị cắt `<LINKED_TASKS>`. |
| **C4. Hàng đợi** | Mục 3. | So với C3; số lần gọi LLM giảm; thao tác duyệt không còn `user_action_unmatched` do đến sớm. |
| **D. Hạn** | Mục 9. | Giữ một bên, xoá bên kia. |
| **E. Chốt** | Chạy `test` một lần; cập nhật `README.md`, `DECISIONS.md` (mục *v6*), `LABELING.md`, UI (`PipelinePanel`, `types.ts`), `docs/pipeline.html` (sửa hoặc xoá nếu lỗi thời). | Báo cáo `test` kèm khoảng bootstrap. |

Mỗi bước C là một commit riêng để lùi được.

### Test cần thêm (`backend/tests`, dùng `FakeLLM`)

- Hàng đợi: nối chuỗi theo khoảng cách; dừng ở `max_wait_seconds` và `max_batch_messages`; thao tác duyệt đến trước lượt được hoãn.
- Reaction không sinh lượt; in kèm dòng tin; reaction sau thời điểm lượt không hiện.
- `<MEMBERS>` bị cắt đúng khi vượt `members_max`; `user_id` lạ → khớp tên → `assignee_unknown`.
- UPDATE: `assignees_add` / `_remove`; `notes_add` nối; `due_kind` null không đổi hạn, `"none"` bỏ hạn; `due_shift_days` có / không có hạn gốc.
- Ba tầng: task không lặp giữa các khối; `declined` có trong index kèm nhãn; phần tràn; ngân sách không cắt `<LINKED_TASKS>`.
- `detail_check`: kích hoạt với `notes` / `title` trên task ở index; không kích hoạt với `notes_add`, đổi hạn, task ở `<TASK_DETAILS>`.
- Eval: `strict_correct`; ghép hai nhãn chung anchor; lượt báo nhầm; bootstrap tất định theo seed.
- `--jobs 1` và `--jobs 4` cho cùng kết quả (với `FakeLLM`).

## 14. Đã xác nhận với người dùng (17/11/2026)

1. **Xoá `heuristic.py`** (8.3): đồng ý. UI và CLI không chạy khi thiếu `OPENAI_API_KEY`.
2. **Chạy song song** (8.4): đồng ý, thay quyết định v5.1 #70.
3. **Model tham chiếu** cho mốc B′ và mọi bước C, D, E: **`gpt-4o-mini`** (rẻ hơn `gpt-4.1-mini`: 0,15 / 0,60 so với 0,40 / 1,60 USD mỗi 1M token input / output). Đổi mặc định của `llm.extract_model_name()` sang `gpt-4o-mini`.
4. **Chưa thử model mạnh hơn** trong v6.

## 15. Không làm trong v6

Chạy thử model mạnh hơn để biết trần của thiết kế; chat thật và người gán nhãn thứ hai (kappa); DM; classifier cho cổng; embedder cục bộ; chuyển `changes` sang danh sách `{field, op, value}` có kiểu (chỉ cần khi số trường sửa được vượt khoảng 10); thêm trường task mới (ưu tiên, checklist, tag…) — khi thêm thì theo quy tắc: trường một giá trị dùng `đặt`, trường danh sách dùng `_add` / `_remove`, và dòng `<TASK_INDEX>` không dài ra.
