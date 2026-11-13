# AI Reminder — v5: code tìm trước, LLM phán sau · cổng phân loại · bộ đánh giá

Nov 13, 2026 · thay đổi trên prototype v4 đã implement (`backend/` + `frontend/`)

Tài liệu này đủ để implement trong một phiên Claude Code mới. Đọc kèm `AI Reminder — Prototype detection task v3.md` (đặc tả gốc: sổ cái, vòng đời, Validate, đánh giá), `AI Reminder — Prototype detection task v4.md` (task được link, kho task, kiểm trùng), `README.md` và `backend/DECISIONS.md` (#18–#44 là các quyết định của v4 mà v5 xây tiếp).

## 0. Vì sao có v5

Số đo của v4 (`backend/runs/compare.md`, 81 lượt, 15 nhãn) cho thấy:

- LLM **không bao giờ** tự yêu cầu tìm task (0/81 lượt) dù prompt có quy tắc riêng và trường `message_type`. Khi việc cũ không có trong prompt, model UPDATE nhầm vào task gần nhất hoặc bịa `T<n>`.
- LLM **bỏ qua** khối `<POSSIBLE_DUPLICATES>` 3/3 lần; ca m073 có task đúng trong ứng viên lần 2 nhưng model giữ lựa chọn ban đầu.
- Phần tìm việc cũ hiện chạy được là nhờ **code** (kiểm tra target, kiểm trùng có tính người được gọi tên), không nhờ model.
- Cổng `gpt-4o-mini` ở ngưỡng 0.05 chỉ giữ 79% lượt có việc thật. Trên chat thật, nơi phần lớn lượt không đổi sổ cái, đây là đòn bẩy chi phí lớn nhất và hiện chưa dùng được.
- 15 nhãn không phân biệt được 73% với 87% recall. Mọi quyết định #30–#44 dựa trên chênh lệch 1 nhãn.

Nguyên tắc của v5: **code tìm ứng viên trước khi gọi LLM; LLM chỉ đọc tin và phán trên ứng viên đã có; chỗ nào tín hiệu chắc chắn thì code quyết, không hỏi model.** Cổng là một bộ phân loại huấn luyện trên chính dữ liệu của tổ chức. Và trước khi đo thêm bất cứ gì, phải có bộ đánh giá đủ lớn.

Giữ nguyên toàn bộ phần đã đúng của v3/v4: sổ cái, vòng đời task, bằng chứng theo tin, đề xuất thay đổi chờ duyệt, người nhận duyệt, code tự tính ngày và gán người nhận, task được link qua reply/reaction, kho task `TaskStore`, kiểm trùng và kiểm tra target bằng code.

## 1. Tóm tắt thay đổi

| # | v4 (đã chạy) | v5 | Lý do |
|---|---|---|---|
| 1 | Ngữ cảnh = task được link + 3 task gần nhất. Việc cũ hơn chỉ đến khi LLM điền `search` | **`context_mode = retrieve_first`**: ngữ cảnh = task được link + **task khớp nội dung TIN MỚI** (code tìm trong kho bằng BM25 + embedding) + task gần nhất lấp cho đủ `candidate_tasks_max`. Bỏ trường `search` khỏi schema | LLM không search (0/81). Code đã có sẵn `TaskStore.search`; chỉ đổi thời điểm chạy từ "sau lần 1 nếu LLM xin" sang "trước lần 1, luôn luôn" |
| 2 | `message_type = old_task_not_in_prompt` → LLM phải tự soạn query | Giữ enum; khi model chọn giá trị này, **code tự tìm** bằng nội dung TIN MỚI rồi gọi lần 2 | Giữ một lối thoát không đòi model soạn query; đo tần suất, bỏ nếu vẫn 0 |
| 3 | Kiểm trùng: có ứng viên → hỏi lại LLM | Thêm **luật cứng**: ứng viên là task `declined`, cùng người, trùng mạnh → code **bỏ CREATE** (`dedup_hard_block`), không hỏi. Các ca khác vẫn hỏi lại như v4 | LLM bỏ qua gợi ý trùng 3/3; "người dùng đã từ chối" là tín hiệu chắc chắn |
| 4 | Cổng = model OpenAI nhỏ, `logprobs` yes/no | **`gate_impl = classifier`**: encoder nhỏ fine-tune trên lượt đã gán nhãn, chạy cục bộ, trả `p`. Giữ `gate_mode` none/shadow/llm như cũ; `llm` nay nghĩa là "chặn theo ngưỡng" với bất kỳ `gate_impl` | Recall cổng LLM 79%; classifier học đúng phân phối chat, ngưỡng chỉnh được, không phụ thuộc `logprobs` của nhà cung cấp, không tốn tiền |
| 5 | Embedding `text-embedding-3-small` qua OpenAI | Thêm **`embed_provider = local`** (sentence-transformers, `BAAI/bge-m3` hoặc `intfloat/multilingual-e5-base`) | Tiếng Việt yếu (ngưỡng tuyệt đối không tách được, DECISIONS #41); và chạy được trên chat thật mà không gửi ra ngoài |
| 6 | Dữ liệu: `data/sample` 78 tin, 15 nhãn, do người viết prompt sinh | **`data/eval/`** ≥ 100 nhãn, ≥ 1.000 lượt, do người khác gán; **nhãn theo lượt** `turn_labels.jsonl`; công cụ xuất lượt, gộp nhãn, đo đồng thuận, ẩn danh | Không có bộ này thì không đo được gì; nhãn lượt là dữ liệu huấn luyện cổng |
| 7 | Chỉ số v4 | Thêm **candidate recall** (task đúng có trong prompt lần 1 không, qua nguồn nào), chỉ số cổng phân loại, chặn cứng đúng/sai | Đo riêng từng tầng để biết lỗi ở tìm ứng viên hay ở model |

Không nằm trong v5 (ghi để không lẫn): tách bộ trích thành hai bước "trích sự kiện" và "link task" (mục 14 — chỉ làm nếu v5 đo thấy candidate recall cao mà link vẫn sai); model nội bộ thay OpenAI; UI thật; Teams/Graph.

## 2. Luồng mỗi lượt (mới)

```
Tin mới (hoặc nhóm tin đến dồn ≤ coalesce_seconds)
 → [code] Ngữ cảnh (context_mode = retrieve_first):
      A. LINKED_TASKS   — reply / reaction vào tin bằng chứng (giữ v4, không bao giờ bị cắt)
      B. CANDIDATE_TASKS — (B1) task KHỚP nội dung TIN MỚI: TaskStore.search(query = TIN MỚI, people = người liên quan)
                           (B2) task GẦN NHẤT lấp cho đủ candidate_tasks_max
      C. RECENT_HISTORY  — window_k tin trước + chuỗi reply (giữ)
      D. NEW_MESSAGES
 → [cổng] gate_impl = classifier | llm; gate_mode = none | shadow | llm
 → [LLM lần 1] {message_type, ops}
      a. ops rỗng, message_type ≠ old_task_not_in_prompt → dừng
      b. message_type = old_task_not_in_prompt           → [code] tìm bằng TIN MỚI, loại task đã trong prompt → [LLM lần 2]
      c. UPDATE/CANCEL                                   → [code] kiểm tra target (giữ v4) → có ứng viên → [LLM lần 2]
      d. CREATE                                          → [code] kiểm trùng (giữ v4)
                                                               ├ ứng viên declined + cùng người + trùng mạnh → BỎ CREATE (luật cứng, không gọi)
                                                               ├ ứng viên khác, còn lượt gọi → [LLM lần 2] <POSSIBLE_DUPLICATES>
                                                               └ không có ứng viên → áp
 → Validate → sổ cái → nháp / đề xuất → HITL (giữ v3)
```

Tối đa vẫn `extract_max_calls_per_turn = 2`. Kỳ vọng sau v5: tỉ lệ lượt gọi lần 2 giảm (ứng viên đã có từ lần 1); đo và ghi vào `compare.md`.

## 3. Dựng ứng viên trước lần gọi 1

### 3.1 Truy vấn từ TIN MỚI

- `query_text` = nội dung mọi tin nhắn trong TIN MỚI (không gồm reaction), bỏ @mention (như `TaskStore.query_text`). Tin reply mà tin được reply nằm ngoài cửa sổ K: nối thêm nội dung tin được reply (tối đa 1 tin) — "ok em, lùi sang thứ 6 nhé" một mình không có từ khoá nào.
- `people` = người gửi TIN MỚI + người được @mention + tác giả của tin được reply (nếu có) + tên thành viên viết hoa trong tin (`TaskStore.names_in_query`).

### 3.2 Nguồn ứng viên và thứ tự

| Nguồn | Cách lấy | Giới hạn | Nhãn trong prompt |
|---|---|---|---|
| A. linked | giữ v4 (`TaskStore.linked`, chuỗi reply ≤ `reply_chain_max`, mọi trạng thái trong `search_lookup_days`) | `linked_tasks_max` = 3 | khối riêng `<LINKED_TASKS>` |
| B1. matched | `TaskStore.search(key, query_text, people, since, limit = matched_tasks_max + 2)`, chỉ giữ hit có `keyword_overlap ≥ matched_min_keyword_overlap` **hoặc** `similarity ≥ semantic_floor`; **không** nhận hit chỉ trùng người; trạng thái `candidate / draft / approved / có pending` (như `RECENT_STATUSES`) | `matched_tasks_max` = 3 | `[matched: báo cáo, tuần · 0.52]` |
| B2. recent | `TaskStore.recent`, loại task đã ở A/B1 | lấp tới `candidate_tasks_max` = 5 tổng cho B | `[recent]` |

Task `declined` / `closed` / `cancelled` / `expired` / `stale` **không** vào B (giữ v4): chúng chỉ đến qua A, qua kiểm trùng, hoặc qua lần 2. Lý do: UPDATE/CANCEL lên các task này bị Validate loại (`target_not_open`, DECISIONS #10), đưa vào prompt chỉ thêm nhiễu.

Thứ tự trong `<CANDIDATE_TASKS>`: matched trước (theo điểm giảm dần), rồi recent (theo `last_mentioned_at`). `T<n>` đánh liên tục A → B1 → B2 → (lần 2) khối 6, như v4.

### 3.3 Ngân sách token

Thứ tự cắt khi vượt `context_token_budget`: bằng chứng kèm task ở B (`drop_candidate_evidence`) → giảm K tới 5 (`reduce_k`) → task recent từ cuối (`drop_recent_task`) → task matched từ cuối, giữ ít nhất 1 (`drop_matched_task`). **Không bao giờ cắt khối A.** Mỗi lần cắt ghi `budget_trim` như cũ.

### 3.4 Lần gọi 2 còn lại

Ba lý do, mỗi lượt tối đa một lần gọi thêm, ưu tiên theo thứ tự:

1. `llm_flag`: `message_type = old_task_not_in_prompt` (và ops rỗng — có ops thì bỏ cờ, ghi `llm_flag_with_ops_ignored`). Code tìm bằng `query_text` + `people` của mục 3.1, loại task đã trong prompt, lấy `search_max_results`. Gọi lần 2 với `<TASK_SEARCH_RESULTS source="llm_flag">`. Không có kết quả vẫn gọi, như v4.
2. `target_check`: giữ nguyên v4 (DECISIONS #31, #44).
3. `dedup`: giữ nguyên v4 **sau khi** đã áp luật cứng mục 4.1.

Lần 2 không có `search` để bỏ nữa; mọi cờ `search_*` trong trace của v4 giữ cho chế độ `linked_recent_search`, không dùng ở `retrieve_first`.

## 4. Luật cứng trong code

### 4.1 Chặn CREATE trùng task đã từ chối (`dedup_hard_block`)

Chạy trong `dedup_check`, trước khi quyết định có gọi lần 2 hay không. Với mỗi CREATE hợp lệ và mỗi hit từ kho:

- hit có `status = declined`, bị từ chối trong `declined_suppress_days`;
- cùng người: như v4 (người nhận sau resolve, người được gọi tên trong tin anchor, hoặc người giao — DECISIONS #27, #40);
- trùng mạnh: `keyword_overlap ≥ dedup_hard_keyword_overlap` (3) **hoặc** `similarity ≥ dedup_hard_similarity` (0.70 với embedder cục bộ; với `text-embedding-3-small` để trống = tắt phần ngữ nghĩa, vì đo được task sai tới 0.58).

→ Bỏ op, ghi op bị loại với lý do `dedup_hard_block` (như một lý do Validate), trace kèm `task_id` bị trùng, điểm, từ khớp. Không tính vào lượt gọi. Ứng viên `declined` **không** đạt ngưỡng cứng vẫn đi đường hỏi lại LLM như v4.

Không áp cho `cancelled`/`closed`: việc đã huỷ rồi được giao lại là ca hợp lệ ("thôi khỏi" → tuần sau "làm lại cái banner đi").

### 4.2 Ghi nhận op lệch task được link (`target_not_linked`)

Khi lượt có đúng một task được link và LLM trả UPDATE/CANCEL nhắm **task khác** không được link, có bằng chứng là chính tin reply: ghi trace `target_not_linked` (task link, task nhắm, từ khoá chung của TIN MỚI với mỗi bên). **Không tự sửa** trong v5; chỉ đếm để quyết định ở v6 có nên code ép target hay không.

### 4.3 Validate giữ nguyên v3/v4

Id phải có trong prompt; op phải trích TIN MỚI; `duplicate_anchor`; `no_op_change`; `declined_change_suppressed`; quy hạn; gán người nhận.

## 5. Prompt và schema (v5)

### 5.1 `prompts/extract_v5.txt`

Sao từ `extract_v4.txt`, giữ độ dài (đo được: rút xuống ~800 token làm hỏng chất lượng, DECISIONS #30). Sửa:

- Bỏ quy tắc 4 (SEARCH). Thay bằng: *"CANDIDATE_TASKS lists tasks that may be related: `[matched: …]` = found by content similarity with NEW_MESSAGES, `[recent]` = recently mentioned. NEW_MESSAGES usually refer to a task in LINKED_TASKS (if present) or a `[matched]` task. If NEW_MESSAGES clearly edit / cancel / accept / ask about a task assigned earlier and NO task in the prompt is that task, set message_type = old_task_not_in_prompt and return empty ops; the system will look it up. Never UPDATE a different task instead."*
- Quy tắc 5: thêm một câu: *"If POSSIBLE_DUPLICATES shows the same task, do not CREATE."* (giữ, dù đo được model ít nghe; luật cứng mục 4.1 mới là chốt chặn).
- OUTPUT FORMAT: bỏ `search`; `message_type` giữ bốn giá trị, mô tả `old_task_not_in_prompt` = "refers to a task assigned earlier that is not in the prompt → empty ops; the system will search".
- Mọi thẻ, trường, enum bằng tiếng Anh (DECISIONS #39). Nội dung chat, `title`, `notes`, `due_text` tiếng Việt.

`PROMPT_VERSION = "v5"` cho chế độ `retrieve_first`; `v4` giữ cho `linked_recent_search`; `v3` cho `scored`.

### 5.2 Schema đầu ra

```json
{ "message_type": "no_change | new_task | task_in_prompt | old_task_not_in_prompt", "ops": [ ...schema phẳng v3... ] }
```

`ExtractOutputV5` (pydantic, strict) không có `search`. `extract_schema(mode)`: `scored` → V3, `linked_recent_search` → V4, `retrieve_first` → V5. Cache đĩa của các lần chạy v4 vẫn trúng ở chế độ v4.

### 5.3 Định dạng khối B

```
<CANDIDATE_TASKS>
T2 [draft | assigned] "Gửi báo cáo tuần cho anh Hưng" — assignee: Linh — due: 2026-11-13 (thứ 6) — [matched: bao cao, tuan · 0.52] — evidence: #3 assign, #4 accept
T3 [approved | assigned] "Làm slide tổng kết sprint" — assignee: Linh — due: trước sprint review — [recent] — evidence: #7 assign
</CANDIDATE_TASKS>
```

Phần `[matched: …]` liệt kê tối đa 3 từ khớp (bỏ dấu) và cosine nếu có. Khối A giữ nguyên định dạng v4 (`<LINKED_TASKS>`, kèm tối đa `linked_evidence_per_task` tin).

## 6. Dữ liệu và công cụ gán nhãn

### 6.1 Bộ đánh giá `data/eval/`

Cùng định dạng `data/sample/` (`messages.jsonl`, `members.json`, `gold.jsonl`, `hard_negatives.txt`, `user_actions.jsonl`). Yêu cầu:

- ≥ 100 nhãn task có người nhận, ≥ 1.000 lượt, ≥ 3 ca mỗi tag ở v3 mục 9 và v4 mục 12.
- Tỉ lệ lượt có op ≤ 15% (chat chuyên nghiệp thật). `data/sample` hiện 46% — chỉ dùng để chạy thông code.
- Người gán nhãn **không** là người viết prompt. Hai người gán độc lập ít nhất 100 lượt để đo đồng thuận.
- Nguồn ưu tiên: chat thật của tổ chức đã ẩn danh (mục 6.4). Nếu chưa được phép: dữ liệu viết tay theo hướng dẫn v3 mục 3, vẫn do người khác viết.
- Tách sẵn `split.json`: `{"train": [conversation_key...], "dev": [...], "test": [...]}` theo hội thoại **và** thời gian (test = các tuần sau cùng). Test chỉ dùng để báo cáo, không dùng để chọn ngưỡng.

### 6.2 Nhãn theo lượt `turn_labels.jsonl`

Một dòng một lượt (lượt = kết quả của hàng đợi + gộp tin, mục 4.1 v3; **không** phụ thuộc LLM):

```json
{"turn_id": "ch_design:m041", "conversation_key": "ch_design", "new_event_ids": ["m041"],
 "label": 1, "kinds": ["assign", "due"], "annotator": "A", "note": ""}
```

- `turn_id` = `conversation_key:id sự kiện đầu của lượt`. `label` 1 = lượt phải sinh ít nhất một op (giao, nhận, tự hứa, nêu việc, đổi hạn/người/nội dung, huỷ); 0 = không. `kinds` ⊂ `assign | accept | self_commit | open_ask | due | assignee | content | cancel` (chỉ khi label 1; để chấm cổng theo loại).
- Nhãn suy từ gold (`derive`): mọi lượt chứa một tin trong `supporting_message_ids` của một nhãn → 1 (kinds suy từ vai trò nếu gold có, không thì rỗng); lượt chứa tin trong `hard_negatives.txt` → 0 và `hard: true`; còn lại → 0. Nhãn tay (`import`) ghi đè nhãn suy.

### 6.3 `python -m detect.label` (file mới `detect/label.py`)

| Lệnh | Làm gì |
|---|---|
| `export --data X --out turns.jsonl [--k 5]` | Chạy hàng đợi + gộp tin (không gọi LLM, không cổng) và ghi mỗi lượt: `turn_id`, `new_event_ids`, văn bản TIN MỚI, `k` tin trước (định dạng nén v3), reply/reaction/mention flags, cột `label` trống. Người gán nhãn điền `label`, `kinds`, `note` |
| `derive --data X --out turn_labels.jsonl` | Nhãn suy từ gold + hard negatives (mục 6.2) |
| `import --data X a.jsonl b.jsonl --out turn_labels.jsonl` | Gộp nhiều người gán; cùng `turn_id` khác nhãn → ghi `conflicts.jsonl`, nhãn cuối = nhãn tay của người được chỉ định (`--adjudicator A`) hoặc nhãn suy nếu không ai gán |
| `agree a.jsonl b.jsonl` | Cohen's kappa trên các `turn_id` cả hai cùng gán, kèm bảng chéo và danh sách bất đồng. Kappa < 0.7 → sửa `data/LABELING.md` trước khi gán tiếp |
| `stats turn_labels.jsonl` | Số lượt, tỉ lệ 1, phân bố `kinds`, số hard negative, theo split |

### 6.4 `python -m detect.anonymize --in raw.jsonl --members raw_members.json --out data/eval/` (file mới `detect/anonymize.py`)

Đầu vào đã là `messages.jsonl` + `members.json` (việc chuyển từ export Teams/Zalo sang định dạng này nằm ngoài v5; ghi hướng dẫn ngắn trong `data/LABELING.md`). Thay `user_id` → `u_<hash ngắn>`, tên hiển thị và alias → tên giả **giữ kiểu** (một từ Việt viết hoa, cố tình gồm vài tên trùng từ thời gian như Mai, Nam để giữ ca `name_collision`), thay cả tên trong `text` và `mentions` bằng bảng ánh xạ; bỏ file đính kèm thành `[file]`; thay URL thành `[link]`; dịch `created_at` sang một mốc giả cố định (giữ khoảng cách). Bảng ánh xạ ghi ra ngoài repo (`--map-out`), không commit.

### 6.5 `data/LABELING.md` — hướng dẫn gán nhãn (một trang)

- Định nghĩa "việc": v3 mục 1 (assigned / self_commit / open_ask); "để anh xem đã", hỏi ý kiến, báo tiến độ, báo xong **không** phải việc.
- Nhãn lượt: 1 khi lượt **tự nó** đổi sổ cái. "Dạ ok anh" là 1 (nhận việc), "em gửi rồi ạ" là 0, "lùi sang thứ 6 nhé" là 1 (đổi hạn), 👍 vào tin giao việc là 1.
- Ca biên và cách xử lý: báo tiến độ kèm hứa hẹn ("đang làm, mai xong") → 1 `self_commit` nếu có mốc; hỏi "ai rảnh làm X không" → 1 `open_ask`; "để em xem rồi báo lại" → 0.
- Gold task: `supporting_message_ids` là đúng các tin tạo nên task, không phải cả đoạn; anchor; `kind`; hạn theo quy ước v3 mục 6.
- Quy trình: gán độc lập → `agree` → phân xử → `import`.

### 6.6 Dữ liệu tự sinh `data/synth/` (tuỳ chọn)

Mở rộng `gen_sample.mjs` để sinh thêm ca hiếm (reaction nhận việc, hai việc một tin, huỷ việc, không dấu) với tỉ lệ lượt có op thấp. Chỉ dùng để **huấn luyện** cổng khi thiếu mẫu dương; không bao giờ vào dev/test.

## 7. Cổng phân loại

### 7.1 Đầu vào của classifier

Một chuỗi văn bản dựng từ `BuiltContext` (dùng chung cho huấn luyện và suy luận, hàm `clf/features.py: build_text(new_msgs, window, conv_type)`):

```
[GROUP] [NEW] Linh: Dạ ok anh [REPLY] [CTX] Hưng: @Linh gửi báo cáo tuần cho anh trước thứ 5 nhé [MENTION] | Nam: PR em push rồi | ...
```

- TIN MỚI đứng **trước**, các tin trước nối sau, **mới nhất trước**, tối đa `gate_window_k` tin; cắt tới `gate_clf_max_tokens` = 256 token của tokenizer model (mất ngữ cảnh cũ trước, không mất tin mới).
- Thẻ chữ: `[GROUP]`/`[DM]`, `[NEW]`, `[CTX]`, `[REPLY]` (tin mới reply), `[MENTION]` (có @mention), `[REACTION] 👍 → "<tin được thả>"` cho sự kiện reaction. Tên người giữ nguyên (đã ẩn danh ở 6.4).
- Không bỏ dấu, không chuẩn hoá: model đa ngôn ngữ xử lý được, và chữ không dấu là một phần phân phối cần học.

### 7.2 Gói huấn luyện `backend/clf/` (tách khỏi `detect/`, phụ thuộc riêng)

`requirements-clf.txt`: `sentence-transformers`, `scikit-learn`, `torch` (CPU đủ), `numpy`; `setfit` tuỳ chọn. Model lưu `backend/models/gate_clf/<name>/` (gitignore), kèm `meta.json`: tên model nền, ngưỡng đã chọn, chỉ số dev/test, hash tập huấn luyện, ngày.

| Lệnh | Làm gì |
|---|---|
| `python -m clf.train --data data/eval --labels turn_labels.jsonl --method e5-lr\|setfit\|finetune --base <hf-model> --target-recall 0.95 --out models/gate_clf/<name>` | Dựng `build_text` cho mọi lượt; tách theo `split.json`; huấn luyện; chọn **ngưỡng trên dev** = ngưỡng nhỏ nhất đạt `target-recall`; ghi `meta.json` |
| `python -m clf.evaluate --model models/gate_clf/<name> --data data/eval --labels … --split test` | Bảng chỉ số mục 7.5, đường PR, hiệu chuẩn 10 bin, lỗi theo `kinds` và theo cờ (`[REPLY]`, không dấu, tin ngắn ≤ 5 từ) |
| `python -m clf.predict --model … --text "…"` | Thử nhanh một chuỗi |

Ba phương pháp, làm theo thứ tự, dừng khi đủ:

| Phương pháp | Khi nào | Thiết lập | Thời gian huấn luyện |
|---|---|---|---|
| `e5-lr` | mốc đầu, mọi cỡ dữ liệu | embedding `intfloat/multilingual-e5-small` (đóng băng) → `LogisticRegression(class_weight="balanced")` | phút, CPU |
| `setfit` | 100–500 lượt dương | `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`, 20 cặp/mẫu, 1 epoch tương phản + đầu LR | ≤ 30 phút CPU |
| `finetune` | ≥ 300 lượt dương, ≥ 3.000 lượt | `xlm-roberta-base` hoặc `intfloat/multilingual-e5-base` + đầu phân loại; max_len 256, lr 2e-5, batch 16, 3–5 epoch, trọng số lớp theo tỉ lệ, dừng sớm theo recall@precision trên dev | vài phút GPU T4, ≤ 1 giờ CPU |

PhoBERT để sau: cần tách từ (VnCoreNLP) và yếu với chữ không dấu.

### 7.3 Suy luận `detect/gate_clf.py`

```python
class ClassifierGate:
    def __init__(self, path: str): ...        # nạp một lần (sentence-transformers / transformers), CPU
    def score(self, ctx: BuiltContext) -> GateResult   # p ∈ [0,1], latency_ms, raw = chuỗi đầu vào
```

Dùng `ctx.new_msgs`, `gate_window_k` tin cuối của cửa sổ, `conv_type`. Không gọi mạng. `tokens` = 0, tiền = 0 (`pricing["gate_clf"] = 0`).

### 7.4 Tích hợp

- `config.yaml`: `gate_impl: llm | classifier` (mặc định `llm` để không đổi hành vi cũ); `gate_clf_path`; `gate_clf_max_tokens`.
- `gate.py: run_gate`: `gate_mode = none` → không gọi; `gate_impl = classifier` → `ClassifierGate.score`, ngược lại `llm.gate` như cũ; `shadow`/`llm` xử lý `p` như cũ. `rec["gate"]["model"]` = `"clf:<name>"` hoặc tên model OpenAI; `rec["gate"]["impl"]`.
- `FakeLLM` thêm `gate_p_by_message` (đã có cho LLM) dùng chung; `FakeGate` không cần riêng.
- `probe` in thêm: classifier có nạp được không, thời gian nạp, p trên 3 chuỗi mẫu.

### 7.5 Đánh giá cổng

Mức classifier (`clf.evaluate`, tập test):

| Chỉ số | Cách tính | Ngưỡng |
|---|---|---|
| Recall lượt dương | tại ngưỡng đã chọn trên dev | ≥ 95% |
| Tỉ lệ cho qua | lượt `p ≥ ngưỡng` / tổng lượt | báo cáo (mục tiêu ≤ 25% trên chat thật) |
| Precision tại ngưỡng | | báo cáo |
| AUPRC | diện tích dưới đường precision–recall | báo cáo (không dùng ROC) |
| Báo nhầm hard negative | hard negative có `p ≥ ngưỡng` / tổng hard negative | báo cáo |
| Recall theo `kinds` | accept, cancel, due thường là tin ngắn, dễ sót | mỗi loại ≥ 90% |
| Hiệu chuẩn | 10 bin `p` vs tỉ lệ dương thực | báo cáo |
| Mốc so sánh | cổng từ khoá (regex giao/nhận/huỷ/hạn) và cổng LLM `gpt-4o-mini` (shadow) trên cùng test | classifier phải hơn cả hai ở recall@pass-rate |

Mức pipeline (hạ tầng v3 có sẵn): chạy `gate_mode = shadow, gate_impl = classifier` trên `data/eval` → bảng "recall cổng so với trần" theo ngưỡng (v3 mục 8), đặt cạnh đường cong của cổng LLM; rồi chạy `gate_mode = llm` ở ngưỡng chọn → so recall task, precision, độ trễ phát hiện, chi phí / 1.000 tin với `gate_mode = none`. Chấp nhận khi recall task giảm ≤ 2 điểm so với trần.

Khi chạy thật: task người dùng tự tạo hoặc duyệt mà lượt chứa tin gốc từng bị cổng chặn = bỏ sót đã xác nhận → `turn_labels` vòng sau (ngoài phạm vi v5, ghi để thiết kế trace đủ: trace `gate_skip` phải giữ `turn_id` và `p`).

## 8. Embedder cục bộ

- `config.yaml`: `embed_provider: openai | local | hash` (mặc định `openai` để giữ số cũ; `local` khi chạy chat thật), `embed_model_local: BAAI/bge-m3` (thay thế: `intfloat/multilingual-e5-base`; e5 cần tiền tố `query: ` / `passage: `, embedder tự thêm).
- `detect/embed.py: LocalEmbedder` qua sentence-transformers, cache đĩa `.cache/emb/<model>/` như `OpenAIEmbedder`; `make_embedder(llm_mode, cfg)` chọn theo `embed_provider`; không nạp được thư viện → lỗi rõ, không âm thầm rơi về `HashEmbedder`.
- Ngưỡng theo embedder: `TaskStore.threshold` đọc bảng mặc định theo `embedder.name` (`text-embedding-3-small`: 0.45 / 0.55; `bge-m3`: điểm khởi đầu 0.55 / 0.65; `multilingual-e5-base`: 0.80 / 0.85 vì e5 cosine dồn cao); `config` ghi đè như cũ. **Hiệu chỉnh lại** trên `data/eval` bằng cách in phân bố cosine task đúng / task sai (script `python -m detect.embed calibrate --data data/eval`), như đã làm ở DECISIONS #41.
- Đo: chạy `retrieve_first` với `openai` và `local` trên cùng dữ liệu; so candidate recall và ca `paraphrase`.

## 9. Tham số mới (`backend/config.yaml`)

| Khoá | Mặc định | Ý nghĩa |
|---|---|---|
| `context_mode` | `retrieve_first` | thêm giá trị mới; `scored`, `linked_recent_search` giữ để so sánh |
| `candidate_tasks_max` | 5 | tổng task ở khối B (matched + recent) |
| `matched_tasks_max` | 3 | task khớp nội dung tối đa |
| `matched_min_keyword_overlap` | 1 | hit vào B1 khi ≥ n từ khoá chung hoặc cosine ≥ `semantic_floor` |
| `llm_flag_search` | true | `message_type = old_task_not_in_prompt` → code tìm → lần 2 |
| `dedup_hard_block` | true | luật cứng mục 4.1 |
| `dedup_hard_keyword_overlap` | 3 | |
| `dedup_hard_similarity` | null | null = không dùng ngữ nghĩa cho luật cứng (bật khi có embedder cục bộ đã hiệu chỉnh) |
| `gate_impl` | `llm` | `llm` \| `classifier` |
| `gate_clf_path` | `models/gate_clf/current` | |
| `gate_clf_max_tokens` | 256 | |
| `embed_provider` | `openai` | `openai` \| `local` \| `hash` |
| `embed_model_local` | `BAAI/bge-m3` | |

Giữ nguyên: `linked_tasks_max`, `linked_evidence_per_task`, `recent_tasks_max` (chỉ dùng ở `linked_recent_search`), `search_max_results`, `search_lookup_days`, `extract_max_calls_per_turn`, `dedup_check_on_create`, `dedup_min_keyword_overlap`, `target_check_on_update`, `search_mode`, `semantic_*`, `rrf_k`, `prompt_cache_key`, mọi khoá v3. `search_enabled` chỉ còn ý nghĩa ở `linked_recent_search`.

`pricing` thêm `gate_clf: {input: 0, cached: 0, output: 0}` và model embedding cục bộ = 0.

## 10. Đánh giá thêm (`eval.py`, `report.md`, `compare.py`)

- **Candidate recall (tầng tìm ứng viên):** với mỗi lượt chứa tin bằng chứng của một nhãn mà task đúng **đã có trong kho** trước lượt đó: task đúng có trong prompt **lần 1** không, qua nguồn nào (`linked` / `matched` / `recent`); nếu không: có vào lần 2 không (`llm_flag` / `target_check` / `dedup`); hay bỏ lỡ hẳn. Bảng theo nguồn, kèm cỡ mẫu. Đây là chỉ số chính để biết lỗi ở code hay ở model.
- **Link accuracy (tầng model):** trong các lượt task đúng **có** trong prompt lần 1, model nhắm đúng task bao nhiêu phần trăm (UPDATE/CANCEL đúng target; CREATE khi lẽ ra UPDATE = sai).
- **Luật cứng:** số CREATE bị `dedup_hard_block`; trong đó bao nhiêu khớp nhãn thật (= chặn sai, phải = 0) và bao nhiêu là tạo lại task đã từ chối (= chặn đúng).
- **`target_not_linked`:** số lượt, và trong đó task được link có phải task đúng theo nhãn không.
- **Lần gọi 2 theo lý do:** `llm_flag` / `target_check` / `dedup`, tỉ lệ trên tổng lượt, tiền.
- **Cổng:** bảng mục 7.5 khi `gate_impl = classifier`; đường cong recall cổng so với trần đặt cạnh cổng LLM nếu có cả hai lần chạy shadow.
- Mọi chỉ số cắt theo `scenario_tags` và theo split. `compare.py` thêm các hàng trên; cột đầu là `gate_mode = none, context_mode = retrieve_first`.
- Phân loại FN thêm loại 8: *task đúng có trong prompt nhưng model nhắm sai hoặc không làm gì* (tách khỏi loại 1 "không được chọn vào prompt" và loại 7 "bỏ lỡ tìm kiếm" của v4).

## 11. Chỗ cần sửa trong code

| File | Việc |
|---|---|
| `backend/detect/config.py` | Khoá mục 9; `prompt_version("retrieve_first") = "v5"` |
| `backend/detect/models.py` | `ExtractOutputV5` (không `search`); `extract_schema` theo 3 chế độ; `PromptTask.source` thêm `matched`; `Counters`/trace không đổi |
| `backend/detect/store.py` | `search`: trả cả hit chỉ trùng người như cũ nhưng `SearchHit` có cờ `people_only`; hàm `candidates(key, query, people, since, cfg) -> (matched, recent)` gói logic mục 3.2; bảng ngưỡng theo embedder |
| `backend/detect/context.py` | `build_context_retrieve_first` (copy từ `build_context_linked`, thay khối B, nhãn `[matched]`/`[recent]`, thứ tự cắt mục 3.3); `build_context` rẽ nhánh theo `context_mode`; `extend_context` nhận `attrs='source="llm_flag"'` |
| `backend/detect/extract.py` | `dedup_check`: thêm luật cứng (trả thêm `hard_blocked: [...]`); `validate_and_apply` loại op với lý do `dedup_hard_block`; ghi `target_not_linked` |
| `backend/detect/run.py` | `process_turn`: ở `retrieve_first` bỏ nhánh `parsed.search`; thêm nhánh `llm_flag`; luật cứng trước khi hỏi lại; `rec["context"]["candidates"]` = danh sách `{local, task_id, source, why}`; `rec["gate"]["impl"]` |
| `backend/detect/gate.py` | `gate_impl`; `ClassifierGate` |
| `backend/detect/gate_clf.py` (mới) | mục 7.3 |
| `backend/detect/embed.py` | `LocalEmbedder`; `make_embedder(cfg)`; lệnh `calibrate` |
| `backend/detect/label.py` (mới) | mục 6.3 |
| `backend/detect/anonymize.py` (mới) | mục 6.4 |
| `backend/detect/llm.py` | `HeuristicLLM`/`FakeLLM`: schema v5 (không `search`); `probe` in trạng thái classifier và embedder cục bộ |
| `backend/detect/heuristic.py` | Bộ trích giả lập ở `retrieve_first`: không điền `search`; điền `message_type = old_task_not_in_prompt` khi tin dạng huỷ/đổi hạn không khớp task nào trong prompt |
| `backend/detect/eval.py`, `compare.py` | mục 10 |
| `backend/clf/` (mới) | `features.py`, `train.py`, `evaluate.py`, `predict.py`; `requirements-clf.txt` |
| `backend/prompts/extract_v5.txt` | mục 5.1 |
| `backend/data/LABELING.md`, `data/eval/` | mục 6 |
| `backend/data/gen_sample.mjs` | ca mục 12; `data/sample` sinh lại |
| `backend/.gitignore` | `models/`, `.cache/emb/` |
| `backend/tests/test_context_v5.py` (mới), `test_core.py` | mục 12 |
| `frontend/src/components/demo/PipelinePanel.tsx` | Bước 3: ba nhóm "Được link" / "Khớp nội dung" (từ khớp, cosine) / "Gần nhất"; bước Kiểm trùng hiện "chặn cứng" riêng; bước Cổng hiện `impl` và chuỗi đầu vào classifier |
| `frontend/src/lib/types.ts` | `candidates`, `hard_blocked`, `gate.impl` |
| `README.md`, `backend/DECISIONS.md` | Chế độ mới, lệnh mới, mọi lựa chọn trong lúc implement |

## 12. Ca test cần thêm

Dữ liệu mẫu (`gen_sample.mjs`, tag mới):

| Tag | Mô tả | Kết quả đúng |
|---|---|---|
| `matched_far_no_reply` | Không reply, việc cách > 5 task khác, tin có từ khoá của việc | Task vào prompt lần 1 qua `matched`; UPDATE đúng; **không** gọi lần 2 |
| `matched_paraphrase` | "weekly report" khi task là "báo cáo tuần" | Vào `matched` nhờ cosine (embedder thật) hoặc ghi nhận giới hạn khi `keyword` |
| `recreate_declined_hard` | Nhắc lại việc đã từ chối, cùng người, ≥ 3 từ khoá chung | CREATE bị `dedup_hard_block`, không gọi lần 2, không task mới |
| `recreate_declined_soft` | Nhắc lại việc đã từ chối, chỉ 2 từ khoá chung | Đi đường hỏi lại LLM như v4 |
| `cancelled_then_reassigned` | Việc đã huỷ, tuần sau giao lại | CREATE mới hợp lệ (luật cứng không áp cho `cancelled`) |
| `llm_flag_fallback` | Việc cũ không vào `matched` (không từ khoá chung, cosine thấp) | Model có thể cắm cờ → code tìm → lần 2; đo tần suất |
| `reply_but_other_task` | Reply vào tin của task A nhưng nội dung nói task B rõ ràng | Model nhắm B; trace `target_not_linked` ghi đúng |
| `budget_keep_matched` | Kênh ≥ 10 task, ngân sách thấp | Cắt recent trước, matched sau, không cắt linked |

Unit test (`FakeLLM`, `FakeEmbedder`, không mạng):

- [ ] `candidates`: matched trước recent; không nhận hit chỉ trùng người; loại task đã ở linked; giới hạn `matched_tasks_max` và `candidate_tasks_max`; trạng thái declined/closed không vào B.
- [ ] Truy vấn 3.1: nối tin được reply khi nó ngoài cửa sổ; bỏ @mention; people gồm tác giả tin được reply.
- [ ] Thứ tự cắt ngân sách 3.3; linked không bao giờ bị cắt.
- [ ] `llm_flag`: cờ + ops rỗng → tìm → lần 2; cờ + có ops → bỏ cờ; `llm_flag_search = false` → bỏ.
- [ ] Luật cứng: đúng ngưỡng, đúng trạng thái `declined`, hết `declined_suppress_days` thì không chặn; không áp cho `cancelled`; không tính vào lượt gọi; op bị loại có lý do `dedup_hard_block`.
- [ ] `target_not_linked` ghi đúng, không sửa op.
- [ ] `gate_impl = classifier` với một classifier giả (trả p theo chuỗi): shadow ghi p; llm chặn đúng ngưỡng; `tokens`/tiền = 0.
- [ ] `build_text`: TIN MỚI trước, thẻ đúng, cắt đúng độ dài, reaction có thẻ.
- [ ] `label derive`: nhãn 1 cho lượt chứa tin supporting, 0 + hard cho hard negative; `import` phân xử; `agree` tính kappa đúng trên ví dụ nhỏ.
- [ ] `anonymize`: không còn id/tên gốc trong bất kỳ trường nào; mentions và text khớp bảng ánh xạ; khoảng cách thời gian giữ nguyên.
- [ ] Hai chế độ cũ vẫn cho kết quả như trước (44 test hiện có xanh ở cả ba chế độ nếu áp dụng được).

## 13. Thứ tự làm, dừng sau mỗi bước để người dùng xem

**Bước A — code tìm trước, luật cứng (1–2 ngày).** Mục 3, 4, 5, 9, 10, 11 trừ cổng, embedder cục bộ và công cụ dữ liệu. Xong khi: unit test xanh; `data/sample` có ca mục 12; chạy OpenAI ba chế độ `scored` / `linked_recent_search` / `retrieve_first` trên `data/sample`, `compare.md` có candidate recall, link accuracy, lần gọi 2 theo lý do, chặn cứng, tiền / 1.000 tin. Kỳ vọng: recall ≥ scored (87%), trùng = linked (0%), lần gọi 2 giảm. Nếu không: dừng và xem lại mục 3.2 trước khi làm tiếp.

**Bước B — dữ liệu và công cụ (code 1 ngày; gán nhãn chạy song song, 1–2 tuần người).** Mục 6. Xong khi: `detect.label` và `detect.anonymize` có test; `LABELING.md` đã được hai người gán thử 100 lượt và kappa ≥ 0.7; `data/eval` đạt yêu cầu 6.1 (có thể nộp theo đợt: đợt 1 ≥ 1.000 lượt / ≥ 50 nhãn đủ để bắt đầu bước C).

**Bước C — embedder cục bộ và cổng phân loại (2–3 ngày sau khi có đợt 1).** Mục 7, 8. Thứ tự: `LocalEmbedder` + hiệu chỉnh ngưỡng → chạy lại bước A trên `data/eval` với `openai` và `local` → `e5-lr` → `setfit`/`finetune` khi đủ dữ liệu → shadow → chọn ngưỡng trên dev → chạy `gate_mode = llm` đầu cuối. Xong khi: bảng 7.5 trên test; đường cong cổng classifier cạnh cổng LLM; recall task ở ngưỡng chọn giảm ≤ 2 điểm so với trần; chi phí / 1.000 tin ghi trong `compare.md`.

**Bước D — quyết định hướng v6** từ số đo: candidate recall thấp → sửa tìm ứng viên (embedder, truy vấn); candidate recall cao nhưng link accuracy thấp → tách trích khỏi link (mục 14); cả hai cao → dừng tối ưu pipeline, chuyển sang model nội bộ.

## 14. Hướng sau v5 (không làm trong v5)

Nếu bước D cho thấy model nhắm sai dù task đúng có trong prompt: tách bộ trích thành (1) **trích sự kiện** từ TIN MỚI + cửa sổ gần, không có id task, trả `{event_type ∈ assign|accept|self_commit|open_ask|due_change|assignee_change|content|cancel|none, title, assignee_refs, due_*, evidence}`; (2) **link** bằng code trên kho với ứng viên đã tìm, quyết định thẳng khi tín hiệu rõ (reply trực tiếp; không ứng viên + event là assign/self_commit → CREATE); (3) **phán xử** bằng LLM chỉ khi mơ hồ, với tối đa 5 ứng viên và câu hỏi duy nhất "cùng task nào, hay task mới". Lợi ích: chấm riêng từng tầng; nhãn theo lượt (đã có từ bước B) đủ để chấm tầng 1 mà không cần nhãn task đầy đủ.

## 15. Xong khi

1. Toàn bộ unit test xanh (cũ + mục 12).
2. `compare.md` trên `data/sample` và `data/eval` có ba chế độ ngữ cảnh, hai embedder, hai cổng; mọi chỉ số mục 10 kèm cỡ mẫu và khoảng tin cậy.
3. `data/eval` đạt 6.1; `turn_labels.jsonl` có kappa ≥ 0.7 ghi trong `LABELING.md`.
4. `models/gate_clf/current/meta.json` có ngưỡng và chỉ số test; `probe` nạp được.
5. UI hiện nhóm "Khớp nội dung", chặn cứng, và `p` của cổng phân loại với chuỗi đầu vào.
6. `README.md` và `DECISIONS.md` cập nhật; DECISIONS ghi rõ mọi chỗ lệch tài liệu này.

## 16. Cần xác nhận trước khi làm bước B, C

1. **Quyền dùng chat thật** của tổ chức để gán nhãn và huấn luyện cục bộ (không gửi ra ngoài). Ai ký, kênh nào, khoảng thời gian nào. Không có thì bước B dùng dữ liệu viết tay và số đo cổng chỉ mang tính định hướng.
2. **Ai gán nhãn** (hai người, không phải người viết prompt) và ai phân xử.
3. Chạy bộ trích trên chat thật để lấy nhãn thô (mục 6.2) đòi model không gửi dữ liệu ra ngoài. Nếu chưa có model nội bộ, nhãn thô bỏ qua, gán tay toàn bộ (ước tính 10–15 giây/lượt).
4. Mục tiêu recall cổng 95% và mức chấp nhận "mất ≤ 2 điểm recall task" cần PM xác nhận, cùng ngưỡng recall/precision còn mở từ v3 mục 12.
5. Luật cứng chỉ áp cho `declined`: PM xác nhận "việc đã huỷ rồi giao lại" là ca hợp lệ, không phải tạo trùng.
