# AI Reminder — Ngữ cảnh mới: task được link, 3 task gần nhất, tìm task

Nov 13, 2026 · thay đổi trên prototype v3 đã implement (`backend/` + `frontend/`)

Tài liệu này đủ để implement trong một phiên Claude Code mới, không cần đọc lại cuộc chat trước. Đọc kèm `AI Reminder — Prototype nhanh phát hiện và tạo task v3.md` (đặc tả gốc), `README.md` và `backend/DECISIONS.md`.

## 0. Tóm tắt thay đổi

| # | Hiện tại (v3, đã chạy) | Mới |
|---|---|---|
| 1 | Code chấm điểm mọi task đang mở (reply +100, bằng chứng trong lịch sử gần +20, cùng người +10, từ khoá +3, chạm trong 24h +5), lấy top 5 + tối đa 2 task cũ | Bỏ bảng điểm. Ngữ cảnh mỗi lượt = **task được link** (nếu có) + **3 task được nhắc gần nhất** + ~10 tin gần + tin mới |
| 2 | Task được reply chỉ là một dòng như mọi task khác | **Task được link** (tin mới reply hoặc thả reaction vào tin bằng chứng của nó) có **khối riêng, đứng trước**, ghi rõ là người gửi tự link tới, kèm nhiều bằng chứng hơn |
| 3 | Việc cũ ngoài top 5 thì LLM không thấy → tạo trùng hoặc bỏ sót | LLM được **yêu cầu tìm task**: trả về một truy vấn, code tìm trong kho task, gọi LLM lần 2 với kết quả |
| 4 | Chống trùng chỉ dựa vào `duplicate_anchor` | Thêm **kiểm trùng khi CREATE**: code tìm task gần giống trong kho; nếu có và chưa nằm trong prompt thì hỏi lại LLM một lần |
| 5 | Phần hướng dẫn 1.353 token, cache chỉ ăn 14% | Rút gọn hướng dẫn + thử `prompt_cache_key` (mục 8) |

Lý do (đã thống nhất với người dùng, 12–13/11):

- Trong môi trường chuyên nghiệp, tin mới hầu như nói về **việc vừa bàn**, hoặc **reply thẳng** vào tin giao việc. Bảng điểm đặt tay không có cơ sở đo; reply là tín hiệu chắc chắn duy nhất.
- Không đưa nhiều task vào prompt vì tốn token. Việc cũ hơn được lấy **theo nhu cầu** qua tìm kiếm, kể cả việc đã duyệt/đã đóng (ca "sửa, huỷ, đổi hạn sau khi việc đã chốt" — chỗ hỏng của ý tưởng khoanh vùng ban đầu).
- Kho task là **database** (prototype: cấu trúc trong bộ nhớ, sau này SQLite/Postgres). Kho to bao nhiêu cũng không tốn token, chỉ phần đưa vào prompt mới tốn.

Lưu ý thực tế về chi phí: đổi từ 5 task xuống 3 chỉ tiết kiệm ~5–10% token mỗi lượt. Thay đổi này chủ yếu để **đúng hơn và chịu được kênh nhiều task**. Tiền thật nằm ở "mỗi tin một lần gọi" và 1.353 token hướng dẫn (mục 8).

## 1. Luồng mỗi lượt (mới)

```
Tin mới (hoặc nhóm tin đến dồn ≤ coalesce_seconds)
 → [code] Ngữ cảnh:
      A. TASK ĐƯỢC LINK — tin mới reply/react vào tin bằng chứng của task (đi ngược chuỗi reply ≤ reply_chain_max)
      B. 3 TASK GẦN NHẤT — theo last_mentioned_at, trừ task đã có ở A
      C. LỊCH SỬ GẦN — window_k tin trước (+ chuỗi reply ngoài K, như hiện tại)
      D. TIN MỚI
 → [LLM lần 1] trả {ops, search}
      a. ops rỗng, search = null        → dừng (phần lớn các lượt)
      b. UPDATE/CANCEL task trong A/B    → Validate → áp
      c. CREATE                          → [code] kiểm trùng trong kho
                                              ├ không có ứng viên → áp
                                              └ có ứng viên ngoài prompt → [LLM lần 2] kèm <KHA_NANG_TRUNG> → áp kết quả lần 2
      d. search ≠ null (ops phải rỗng)   → [code] tìm trong kho → [LLM lần 2] kèm <KET_QUA_TIM_TASK> → b hoặc c (lần 2 không được search tiếp)
```

Tối đa **2 lần gọi** bộ trích mỗi lượt (`extract_max_calls_per_turn = 2`). Lần 2 không được search nữa, nhưng vẫn qua kiểm trùng khi CREATE. Nếu kiểm trùng muốn hỏi lại lần 3 thì không hỏi, chấp nhận CREATE và ghi trace `dedup_skipped_call_limit`.

## 2. Dữ liệu task

Thêm vào `Task` (`backend/detect/models.py`):

- `last_mentioned_at: str` — `created_at` của **tin bằng chứng mới nhất trong chat** (tin hoặc reaction). Cập nhật mỗi khi thêm evidence. **Không** cập nhật khi người dùng duyệt/từ chối (thao tác UI không phải "được nhắc tới"). `last_touched_at` giữ nguyên ý nghĩa cho luật hết hạn.

Kho task: giữ `state.tasks`, nhưng gom mọi truy cập đọc vào một lớp `TaskStore` (file mới `backend/detect/store.py`) để sau này thay bằng DB mà không sửa chỗ khác:

```python
class TaskStore:
    def linked(self, key, message_ids: list[str], since) -> list[Task]       # task có evidence ∈ message_ids
    def recent(self, key, n, exclude: set[str]) -> list[Task]                # theo last_mentioned_at desc
    def search(self, key, query, people: list[str] | None, since, limit) -> list[tuple[Task, float, list[str]]]  # (task, điểm, lý do khớp)
```

## 3. Chọn task cho ngữ cảnh

### A. Task được link

- Lấy `reply_to_id` của mỗi tin mới (và `target_id` của mỗi reaction mới); đi ngược chuỗi reply tối đa `reply_chain_max` bước. Mọi id gặp được mà là evidence của một task trong cùng `conversation_key` → task đó là **được link**.
- Lấy mọi trạng thái trong `search_lookup_days` (kể cả `approved`, `declined`, `closed`, `cancelled`) — người dùng reply vào việc cũ thường để sửa/huỷ.
- Nhiều task cùng được link (tin giao 2 việc "Linh làm A, Nam làm B") → đưa tất cả, tối đa `linked_tasks_max = 3`.

### B. 3 task gần nhất

- Trạng thái `candidate`, `draft`, `approved`, và task có đề xuất `pending`. **Không** gồm `declined`/`expired`/`stale`/`closed`/`cancelled` (những task này chỉ đến qua A, tìm kiếm, hoặc kiểm trùng).
- Sắp theo `last_mentioned_at` giảm dần, lấy `recent_tasks_max = 3`, bỏ task đã có ở A.

### C, D giữ nguyên v3

`window_k`, chuỗi reply, định dạng tin nén `#n`, cắt tin dài, lịch 14 ngày.

### Ngân sách token

Thứ tự cắt khi vượt `context_token_budget`: bằng chứng kèm task ở B → giảm K (tối thiểu 5) → task thứ 3, thứ 2 ở B. **Không bao giờ cắt khối A.**

## 4. Định dạng prompt

Thứ tự khối (phần ít đổi trước để tận dụng cache):

| Khối | Nội dung |
|---|---|
| 1 | Hướng dẫn cố định (rút gọn — mục 8) |
| 2 | `<LICH>` 14 ngày |
| 3a | `<TASK_DUOC_LINK>` — chỉ có khi có task được link |
| 3b | `<TASK_GAN_DAY>` |
| 4 | `<LICH_SU_GAN>` |
| 5 | `<TIN_MOI>` |
| 6 | `<KET_QUA_TIM_TASK>` hoặc `<KHA_NANG_TRUNG>` — chỉ ở lần gọi 2 |

Khối 3a được đánh dấu đặc biệt và kèm **tối đa 3 tin bằng chứng** (tin giao + tin gần nhất), không chỉ 1:

```
<TASK_DUOC_LINK>
Người gửi TIN MỚI đã reply trực tiếp vào tin bằng chứng của task dưới đây. Mặc định TIN MỚI nói về task này,
trừ khi nội dung rõ ràng nói về việc khác.
T1 [approved | assigned] "Gửi báo cáo tuần cho anh Hưng" — người nhận: Linh — hạn: thứ 5 (12/11) — TIN MỚI reply vào #3
    #3 [thứ 2 09/11 09:05] Hưng: @Linh gửi báo cáo tuần cho anh trước thứ 5 nhé (@Linh=u_linh)
    #4 [thứ 2 09/11 09:06] Linh: Dạ ok anh ↩#3
</TASK_DUOC_LINK>
<TASK_GAN_DAY>
T2 [draft | assigned] "Làm slide tổng kết sprint" — người nhận: Linh — hạn: trước buổi sprint review — bằng chứng: #7 assign, #8 accept
...
</TASK_GAN_DAY>
```

Id `T<n>` đánh liên tục qua 3a → 3b → (lần 2) kết quả tìm kiếm/khả năng trùng. Code giữ bảng ánh xạ như hiện tại.

Lần gọi 2 = **cùng prompt lần 1** (để cache trúng) + khối 6:

```
<KET_QUA_TIM_TASK truy_van="báo cáo tuần" nguoi="u_linh">
T4 [approved | assigned] "Gửi báo cáo tuần cho anh Hưng" — người nhận: Linh — hạn: thứ 5 (12/11) — nhắc lần cuối: thứ 2 09/11
    #15 [thứ 2 09/11 09:05] Hưng: @Linh gửi báo cáo tuần cho anh trước thứ 5 nhé
(không còn kết quả nào khác)
</KET_QUA_TIM_TASK>
Đây là lần gọi cuối: chỉ trả ops, search phải là null.
```

`<KHA_NANG_TRUNG>` cùng định dạng, kèm câu: "Bạn định CREATE việc X. Các task dưới đây có thể là cùng việc. Nếu đúng là cùng việc, dùng UPDATE/không đổi gì thay vì CREATE."

### Câu chữ thêm vào hướng dẫn (khối 1)

- TIN MỚI thường nói về TASK_DUOC_LINK (nếu có) hoặc một trong TASK_GAN_DAY.
- Nếu TIN MỚI rõ ràng nói về **một việc đã có từ trước** (đổi hạn, nhận, huỷ, hỏi tiến độ của "báo cáo tuần", "cái PR hôm qua"…) mà việc đó **không có** trong ngữ cảnh: trả `ops: []` và điền `search` (truy vấn ngắn bằng từ khoá của việc, người liên quan nếu biết). Không CREATE việc đó.
- Không search khi TIN MỚI là chuyện ngoài lề, báo đã xong, hỏi ý kiến chung.
- Không chắc thì không đổi gì.

## 5. Schema đầu ra (thêm trường `search`)

```json
{
  "ops": [ ...giữ nguyên schema phẳng v3... ],
  "search": null | {"query": "báo cáo tuần", "nguoi": ["u_linh"] | null, "ly_do": "đổi hạn một việc không có trong ngữ cảnh"}
}
```

- Thêm vào `ExtractOutput` (pydantic) và `EXTRACT_JSON_SCHEMA` (strict: mọi trường required, `nguoi` nullable).
- Validate: `search ≠ null` và `ops` không rỗng → bỏ `search`, ghi `search_with_ops_ignored`. `search ≠ null` ở lần gọi 2 → bỏ, ghi `search_on_second_call`.
- Dùng trường JSON thay vì function calling của OpenAI: một schema duy nhất, chạy được với `HeuristicLLM` và `FakeLLM`, dễ cache đĩa và dễ test. (Function calling là phương án thay thế nếu sau này cần nhiều bước tìm.)

## 6. Tìm kiếm trong kho task (`TaskStore.search`)

Prototype: tìm từ khoá trong bộ nhớ; sau này thay bằng SQLite FTS5 / Postgres full-text, rồi mới tới embedding nếu đo thấy hụt.

- **Phạm vi:** cùng `conversation_key`; mọi trạng thái; `last_mentioned_at` trong `search_lookup_days = 30`.
- **Văn bản được đánh chỉ mục mỗi task:** `title`, `notes`, nội dung các tin bằng chứng, tên người nhận/người giao. Bỏ dấu, chữ thường, bỏ stopword (`detect/text.py: keywords`).
- **Điểm:** BM25 đơn giản trên token đã bỏ dấu, cộng điểm khi `nguoi` trùng người nhận/người giao, cộng nhẹ theo độ mới. Trả về `search_max_results = 5` task, kèm điểm và các từ đã khớp (để trace và UI hiển thị).
- **Không có kết quả** → vẫn gọi lần 2 với `<KET_QUA_TIM_TASK>` rỗng ("không tìm thấy task nào"), để LLM quyết định CREATE hay không đổi gì.

## 7. Kiểm trùng khi CREATE

Chạy trên mỗi CREATE hợp lệ, **trước khi áp**:

1. Truy vấn = tiêu đề CREATE + nội dung tin anchor; `people` = người nhận đã gán (sau resolve).
2. Ứng viên = kết quả `search` có ≥ `dedup_min_keyword_overlap = 2` từ khoá chung **và** (trùng người nhận hoặc trùng người giao), **không** nằm trong prompt lần này. Bao gồm cả task `declined` (thay cho heuristic `declinedSimilar` hiện tại).
3. Có ứng viên → nếu còn lượt gọi: gọi lại kèm `<KHA_NANG_TRUNG>`, dùng kết quả lần gọi đó thay cho lần trước. Hết lượt gọi → áp CREATE, trace `dedup_skipped_call_limit`.
4. Giữ luật `duplicate_anchor` của v3.

## 8. Giảm chi phí (làm cùng đợt)

1. **Rút gọn khối 1** xuống ≤ ~800 token: bỏ ví dụ thừa, gộp quy tắc trùng ý. Giữ nguyên ý các quy tắc v3 mục 5.2. Chạy lại để chắc chất lượng không giảm.
2. **`prompt_cache_key`**: truyền `prompt_cache_key` (ví dụ `"extract_v3"`) trong mọi lời gọi bộ trích để OpenAI định tuyến cùng máy cache; so `cached_tokens` trước/sau (hiện 19.456 / 140.388 ≈ 14%). Nếu SDK/model không nhận tham số thì ghi lại vào DECISIONS.
3. **Cổng** để mặc định `none` (đã đo: `gpt-4o-mini` ở ngưỡng 0.05 chỉ giữ 79% lượt có việc thật).

## 9. Tham số (`backend/config.yaml`)

| Khoá | Mặc định | Ý nghĩa |
|---|---|---|
| `context_mode` | `linked_recent_search` | `scored` = v3 hiện tại (giữ để so sánh) \| `linked_recent_search` = tài liệu này |
| `linked_tasks_max` | 3 | Task được link tối đa |
| `linked_evidence_per_task` | 3 | Tin bằng chứng kèm task được link |
| `recent_tasks_max` | 3 | Task gần nhất |
| `search_enabled` | true | Cho phép LLM yêu cầu tìm |
| `search_max_results` | 5 | Kết quả trả về |
| `search_lookup_days` | 30 | Phạm vi thời gian tìm (cũng dùng cho task được link) |
| `extract_max_calls_per_turn` | 2 | Tối đa lần gọi bộ trích mỗi lượt |
| `dedup_check_on_create` | true | Bật kiểm trùng |
| `dedup_min_keyword_overlap` | 2 | Ngưỡng từ khoá chung |

Các khoá v3 khác giữ nguyên; `open_tasks_max`, `closed_tasks_max`, `closed_lookup_days` chỉ dùng khi `context_mode = scored`.

## 10. Chỗ cần sửa trong code

| File | Việc |
|---|---|
| `backend/detect/models.py` | `Task.last_mentioned_at`; `ExtractOutput.search`; cập nhật `EXTRACT_JSON_SCHEMA` |
| `backend/detect/store.py` (mới) | `TaskStore`: `linked`, `recent`, `search` (BM25 bỏ dấu) |
| `backend/detect/context.py` | Nhánh `context_mode`: dựng khối 3a/3b; khối 6 cho lần gọi 2; ngân sách token mới; trả về `linked`, `recent` thay cho `selected/not_selected` (giữ trường cũ cho chế độ `scored`) |
| `backend/detect/extract.py` | Cập nhật `last_mentioned_at` khi thêm evidence; validate `search`; target hợp lệ = id trong 3a/3b/khối 6; kiểm trùng khi CREATE |
| `backend/detect/run.py` | `process_turn`: vòng tối đa 2 lần gọi; ghi `rec["search"]`, `rec["dedup"]`, `rec["extract_calls"]` (danh sách các lần gọi, mỗi lần có token/latency/raw) |
| `backend/detect/llm.py` | `prompt_cache_key`; `FakeLLM` hỗ trợ đầu ra theo lần gọi (`by_message[id] = [lần1, lần2]`) |
| `backend/detect/heuristic.py` | Chế độ giả lập: nếu tin có từ khoá trùng một task không có trong ngữ cảnh → điền `search`; thay `declinedSimilar` bằng kiểm trùng của code |
| `backend/prompts/extract_v3.txt` | Bản rút gọn + quy tắc mục 4 → lưu thành `extract_v4.txt`, `PROMPT_VERSION = "v4"` |
| `backend/detect/eval.py` | Chỉ số mục 11 |
| `backend/data/gen_sample.mjs` | Ca test mục 12 (sinh lại `data/sample/`) |
| `frontend/src/components/demo/PipelinePanel.tsx` | Bước 3 hiện 3 nhóm "Được link" / "Gần nhất" / "Lịch sử gần"; thêm bước **"Tìm task"** (truy vấn, kết quả kèm điểm và từ khớp) và **"Kiểm trùng"** giữa Bộ trích và Validate; mỗi lần gọi LLM hiện token và tiền riêng |
| `frontend/src/lib/types.ts` | Kiểu cho `search`, `dedup`, `extract_calls` |
| `backend/DECISIONS.md` | Ghi mọi lựa chọn trong lúc implement |

## 11. Đánh giá thêm

Thêm vào `eval.py` / `report.md`:

- **Tỉ lệ lượt có tìm kiếm** và **có gọi lần 2** (theo lý do: search / kiểm trùng).
- **Bỏ lỡ tìm kiếm:** false negative hoặc task trùng mà task đúng **đã có trong kho** nhưng không có trong ngữ cảnh và LLM **không** search. Thêm làm loại FN số 7 trong danh sách phân loại mục 8 của v3.
- **Tìm sai:** LLM search nhưng task đúng không có trong kết quả (lỗi truy vấn hoặc lỗi chỉ mục).
- **Kiểm trùng:** số lần kích hoạt; số lần LLM đổi từ CREATE sang UPDATE/không đổi; số lần vẫn CREATE (đúng/sai theo nhãn).
- **Chi phí theo lần gọi:** token và tiền của lần 1, lần 2; token/lượt p50, p95 theo khối (3a, 3b tách riêng).
- `compare.py` so hai chế độ `scored` và `linked_recent_search` trên cùng dữ liệu.

## 12. Ca test cần thêm vào dữ liệu mẫu

| Tag | Mô tả | Kết quả đúng |
|---|---|---|
| `reply_link_old` | Reply vào tin giao việc từ hôm trước: "lùi sang thứ 2 nhé" | Task được link; 1 đề xuất đổi hạn; không search |
| `far_update_no_reply` | Không reply, cách > 3 task khác: "@Linh báo cáo tuần lùi sang thứ 6 nhé" | Search "báo cáo tuần"; UPDATE đúng task |
| `cancel_old_no_reply` | "thôi khỏi làm cái icon set nhé" khi icon set không trong 3 gần nhất | Search; đề xuất huỷ đúng task |
| `busy_channel_10` | Kênh ≥ 10 task mở; tin mới cập nhật task thứ 8 theo độ mới | Search hoặc link; UPDATE đúng; không tạo trùng |
| `paraphrase` | Giao "báo cáo tuần", sau đó nhắc "weekly report" | Search tìm được (hoặc ghi nhận là giới hạn của tìm từ khoá) |
| `recreate_declined_far` | Nhắc lại việc đã bị từ chối sau nhiều task khác | Kiểm trùng bắt được; không tạo lại |
| `ambiguous_no_reply` | "cái đó xong chưa em" không reply, không rõ việc | Không đổi gì; không search |
| `reaction_link` | 👍 vào tin giao việc cũ cho nhóm | Task được link; gán người thả reaction |

Unit test (FakeLLM, không gọi mạng):

- [ ] `linked`: reply trực tiếp, reply qua chuỗi, reaction; task đã đóng vẫn được link; tối đa `linked_tasks_max`.
- [ ] `recent`: sắp theo `last_mentioned_at`; thao tác duyệt không làm task "gần" hơn; loại task đã có ở A; loại declined/closed.
- [ ] `search`: bỏ dấu ("bao cao tuan" khớp "báo cáo tuần"), lọc người, lọc thời gian, giới hạn kết quả.
- [ ] Vòng 2 lần gọi: search → kết quả → UPDATE; search ở lần 2 bị bỏ; search kèm ops bị bỏ search.
- [ ] Kiểm trùng: kích hoạt đúng ngưỡng; không kích hoạt khi ứng viên đã trong prompt; tôn trọng `extract_max_calls_per_turn`.
- [ ] Ngân sách token: không bao giờ cắt khối 3a.
- [ ] Chế độ `scored` vẫn cho kết quả như trước (31 test hiện có vẫn xanh).

## 13. Điểm xuất phát (số liệu hiện tại)

- Chạy: `cd backend && .venv\Scripts\activate`, key trong `backend/.env` (đừng đưa key vào `.env.example`). Model: bộ trích `gpt-4.1-mini`, cổng `gpt-4o-mini` (`gpt-4.1-nano` đã ngừng).
- Lần chạy tham chiếu `runs/openai_shadow_v2` (chế độ `scored`, cổng shadow): 55 lượt, recall 11/12, precision 11/11, người nhận đúng 10/11, hạn đúng 5/6; **$0.059** ≈ $1.04 / 1.000 tin; token input/lượt trung vị 2.146 (khối 1 = 1.353); cache 14%.
- Đường chạy so sánh: `python -m detect.run --data data/sample --mode openai --set context_mode=scored --id cmp_scored` và `--set context_mode=linked_recent_search --id cmp_linked`, rồi `python -m detect.eval ...` cho từng run và `python -m detect.compare "runs/cmp_*"`.

## 14. Xong khi

1. Toàn bộ unit test xanh (cũ + mới).
2. Dữ liệu mẫu có các ca mục 12, gold cập nhật.
3. Chạy hai chế độ qua OpenAI, `compare.md` có recall, trùng, bỏ lỡ tìm kiếm, tỉ lệ lượt gọi lần 2, token và tiền / 1.000 tin.
4. UI hiện được khối "Được link", "Gần nhất", bước "Tìm task" và "Kiểm trùng" với input/output thật.
5. `DECISIONS.md` cập nhật.

## 15. Câu hỏi còn mở

1. Tìm kiếm có nên vượt ra ngoài hội thoại hiện tại (việc giao trong DM, nhắc lại trong kênh nhóm) không? Tài liệu này giới hạn trong cùng `conversation_key`.
2. `search_lookup_days = 30` có đủ không?
3. Khi tìm từ khoá hụt vì diễn đạt khác (`paraphrase`), có đáng thêm embedding ngay trong prototype không, hay đo trước?
