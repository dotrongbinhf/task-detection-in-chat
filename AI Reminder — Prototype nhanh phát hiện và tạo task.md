# AI Reminder — Prototype nhanh: phát hiện và tạo task

Nov 9, 2026 · @dotrongbinhf

## 1. Mục tiêu và phạm vi

Xây một **script chạy offline** đọc luồng tin nhắn từ file, phát hiện việc được giao và xuất ra danh sách task, rồi chấm so với bộ nhãn do người gán. Chỉ một câu hỏi cần trả lời: **luồng LLM1 → phiên → LLM2 có nhận ra đúng và đủ task không, và hỏng ở đâu.**

Bài toán: trong chat công việc, việc được giao bằng lời ("Linh gửi anh báo cáo trước thứ 5 nhé"), thường rải qua nhiều tin. Hệ thống cần biến chúng thành task có nội dung, người nhận, hạn, và truy ngược được về tin gốc.

**Làm:** đọc file JSONL; LLM1 chấm từng tin; gom phiên; LLM2 trích task; kiểm tra đầu ra; xử lý task bị cắt ngang; xuất `tasks.jsonl`; chấm điểm và in báo cáo lỗi.

**Không làm:** Teams, Graph, Jira, card, người dùng xác nhận, nhắc việc, cơ sở dữ liệu, UI, xử lý tin sửa/xoá. Không dựng interface "để sau này cắm vào" — code ngắn nhất có thể để trả lời câu hỏi trên.

LLM là model nhỏ của OpenAI, chọn qua biến môi trường (mục 7). Vì gọi API bên ngoài, bản này **chỉ chạy trên dữ liệu tự sinh**.

## 2. Pipeline

Mỗi tin đi qua LLM1 đúng một lần; mỗi phiên đóng sinh đúng một lượt gọi LLM2. Toàn bộ chạy tuần tự trong một process.

```
messages.jsonl  (sắp theo created_at, nhóm theo conversation_key)
   ▼
Trigger:  LLM1(tin + 3 tin trước) → p  +  luật (@mention, reply vào tin đã đánh dấu, mốc thời gian)
   ▼
Session:  mở / kéo dài / đóng phiên theo conversation_key, có watermark
   ▼
LLM2:     [nháp] + [ngữ cảnh chỉ đọc] + [task đã tạo trong hội thoại này] + [lõi]
          → ops: CREATE | UPDATE | FINALIZE
   ▼
Validate: loại op sai luật, ép due_date=null cho hạn sự kiện
   ▼
Drafts:   task chạm ranh giới cắt → nháp, mang sang phiên sau
   ▼
tasks.jsonl + trace.jsonl  →  eval.py  →  report.md
```

Thời gian là thời gian mô phỏng lấy từ `created_at`, không dùng đồng hồ hệ thống: trước khi xử lý mỗi tin, đóng mọi phiên đã im lặng quá hạn tính tới `created_at` của tin đó; hết file thì đóng tất cả.

## 3. Dữ liệu

Ba file trong `data/<dataset>/`.

**`messages.jsonl`** — mỗi dòng một tin:

```json
{"id": "m041", "conversation_id": "ch_design", "thread_root_id": null,
 "created_at": "2026-11-09T09:12:00+07:00", "author_id": "u_hung", "author_name": "Hưng",
 "text": "Linh gửi báo cáo tuần cho anh trước thứ 5 nhé",
 "mentions": [{"user_id": "u_linh", "name": "Linh"}], "reply_to_id": null}
```

`conversation_key` = `conversation_id`, nối thêm `;thread_root_id` nếu có.

**`members.json`** — `{conversation_id: [{user_id, display_name, aliases}]}`, dùng để gán người nhận.

**`gold.jsonl`** — mỗi dòng một task thật do người gán:

```json
{"gold_id": "g07", "conversation_key": "ch_design",
 "anchor_message_id": "m041", "supporting_message_ids": ["m041", "m043", "m045"],
 "title": "Gửi báo cáo tuần cho anh Hưng", "assignee_id": "u_linh",
 "due_type": "calendar", "due_date": "2026-11-12", "scenario_tags": ["multi_message"]}
```

- `due_type` ∈ `calendar` | `event` | `none`; với `event` thì `due_date = null`. `assignee_id = null` khi không xác định được.
- Tin "gần giống giao việc" nhưng không phải thì liệt kê trong `hard_negatives.txt` (mỗi dòng một `message_id`).

Claude Code tạo `data/sample/` khoảng 40 tin, 5 task để chạy thông code. **Bộ dữ liệu đánh giá do người viết**, không phải người viết prompt, để số liệu không đẹp giả.

## 4. Thành phần

### 4.1 Trigger

- LLM1 nhận tin hiện tại + `llm1_context` tin trước trong cùng `conversation_key`, trả `p` = xác suất chuẩn hoá của `yes` so với `no`, tính từ `top_logprobs` (cách gọi ở mục 7).
- Luật tính bằng code: `has_mention` (@mention một người); `reply_to_flagged` (reply vào tin đã được đánh dấu); `has_time_expr` (regex: `thứ [2-7]`, `chủ nhật`, `hôm nay`, `mai`, `cuối tuần`, `tuần sau`, `trước \d{1,2}h`, `\d{1,2}/\d{1,2}`, `deadline`, `hạn`), chỉ tính khi đang có phiên mở hoặc nháp.
- `relevant = p >= llm1_threshold OR has_mention OR reply_to_flagged OR has_time_expr`.
- `trigger_mode`: `llm1` (mặc định) | `all` (mọi tin đều kích hoạt, không cổng lọc) | `rules_only`. Ba chế độ này là thí nghiệm chính của prototype.

### 4.2 Session

Máy trạng thái theo `conversation_key`:

```
KHÔNG CÓ PHIÊN:  relevant → mở phiên, lõi bắt đầu từ tin này
PHIÊN MỞ:       thêm mọi tin vào lõi; relevant → streak = 0, ngược lại streak += 1
ĐÓNG khi (ghi close_reason):
  streak : streak >= close_after_irrelevant
  idle   : im lặng >= idle_close_seconds
  cap    : token(lõi) >= core_token_cap
```

- Đóng vì `cap`: cắt tại khoảng cách thời gian lớn nhất trong `cap_search_tail` tin cuối; phần sau điểm cắt mở ngay phiên mới.
- **Watermark**: lõi phiên sau bắt đầu sau tin cuối của lõi trước. Assert: không tin nào thuộc hai lõi.
- **Ngữ cảnh chỉ đọc** = `context_messages` tin ngay trước lõi, được phép trùng lõi cũ.

### 4.3 LLM2 và prompt

Prompt gồm bốn khối theo thứ tự: nháp đang dở (trạng thái + tin bằng chứng, tối đa `draft_evidence_max`), ngữ cảnh chỉ đọc, task đã tạo trong cùng `conversation_key` (tối đa `open_tasks_max`, chỉ id + tiêu đề + người nhận + hạn), lõi. Mỗi tin dạng `[m041 | 09:12 thứ 2 09/11 | Hưng] nội dung (@Linh)`. Kèm thời điểm tham chiếu = `created_at` tin cuối lõi.

- `CREATE`: anchor trong lõi, không khớp nháp hay task đã có.
- `UPDATE`: sửa nháp hoặc task đã tạo; mỗi trường đổi phải có `evidence_message_id` trong lõi. Trong prototype, UPDATE áp thẳng vào task và ghi `field_history`.
- `FINALIZE`: nháp không còn được nhắc tới.
- Phiên mà LLM2 trả rỗng: ghi `llm1_false_trigger` vào trace.

### 4.4 Validate

Loại op, ghi lý do vào trace, khi: CREATE có anchor ngoài lõi; id tin không có trong prompt; UPDATE thiếu hoặc sai bằng chứng, hoặc `target_id` không tồn tại. Không loại mà sửa: `due_type=event` có ngày → ép `null`, ghi `fabricated_date_blocked`. JSON hỏng → retry `llm_max_retries` lần rồi bỏ phiên, ghi `parse_failure`.

Người nhận: dùng `user_id` của @mention nếu LLM2 trỏ tới; nếu không, khớp `assignee_mention` (bỏ dấu, không phân biệt hoa thường) với `members.json` của hội thoại; khớp đúng một người mới gán, còn lại để `null`.

### 4.5 Nháp

- Có cờ `drafts_enabled` (mặc định `true`) để chạy so sánh có/không có cơ chế nháp.
- Chỉ khi phiên đóng vì `cap`: task có tin bằng chứng cuối nằm trong `draft_boundary_tail` tin cuối lõi → nháp, chưa xuất.
- Nháp lưu trạng thái các trường, mỗi trường kèm tin bằng chứng; không lưu cả cuộc trò chuyện.
- Chốt và xuất khi: `FINALIZE`; phiên sau đóng mà không nhắc tới; đã mang qua `draft_max_carries` phiên. Ghi `carries` vào task.

### 4.6 Đầu ra

`runs/<id>/tasks.jsonl`: mỗi task gồm `task_id`, `conversation_key`, `anchor_message_id`, `supporting_message_ids`, `title`, `assignee_mention`, `assignee_id`, `due_text`, `due_type`, `due_date`, bằng chứng từng trường, `field_history`, `close_reason`, `carries`, `prompt_version`, `model`. Khoá duy nhất `(conversation_key, anchor_message_id)`; vi phạm là lỗi, không nuốt im.

`runs/<id>/trace.jsonl`: mỗi tin (p, tín hiệu luật, relevant), mỗi phiên (lõi, ngữ cảnh, close\_reason), mỗi lượt gọi (prompt, đầu ra thô, token, độ trễ), mỗi op bị loại.

## 5. Prompt và JSON schema

Prompt lưu thành file trong `prompts/` (`llm1_v1.txt`, `llm2_v1.txt`), version nằm trong tên file và được ghi vào mọi bản ghi. Dưới đây là bản khởi đầu; Claude Code giữ nguyên ý, được chỉnh câu chữ.

### 5.1 LLM1

```text
Bạn phân loại tin nhắn trong chat công việc tiếng Việt.
Câu hỏi: tin nhắn CUỐI CÙNG có tham gia vào việc giao một việc cụ thể cho ai đó không?
Tính là "yes" nếu tin đó: giao việc, nhận việc ("ok em", "để em làm"), hỏi ai làm được việc gì,
hoặc bổ sung hạn / người làm / nội dung cho một việc vừa được nói tới.
Tính là "no" nếu chỉ là thảo luận, hỏi ý kiến, chia sẻ thông tin, chào hỏi, hoặc câu lỪng lửng
như "để anh xem đã" mà không ai nhận việc.
Các tin trước chỉ để hiểu ngữ cảnh. Chỉ trả lời "yes" hoặc "no".

<tin trước>
{context}
</tin trước>
<tin cuối cùng>
{message}
</tin cuối cùng>
```

### 5.2 LLM2

```text
Bạn trích xuất việc được giao từ chat công việc tiếng Việt. Thời điểm hiện tại: {now}, múi giờ Asia/Ho_Chi_Minh.
Quy ước thời gian: {time_conventions}

QUY TẮC
1. Chỉ tạo task mới (CREATE) khi câu giao việc (anchor) nằm trong khối LÕI.
   Khối NGỮ CẢNH và NHÁP chỉ để hiểu, không tạo task từ đó.
2. Nếu LÕI bổ sung thông tin cho một NHÁP hoặc TASK ĐANG MỞ → UPDATE, không CREATE.
   Mỗi trường thay đổi phải ghi id tin bằng chứng nằm trong LÕI.
3. NHÁP không được nhắc tới trong LÕI → FINALIZE.
4. Người nhận: ghi đúng tên như xuất hiện trong tin. Không chắc thì để null. Không đoán.
5. Hạn: "thứ 5", "cuối tuần", "mai" → due_type=calendar, tính due_date (YYYY-MM-DD).
   "trước buổi UAT", "xong trước release" → due_type=event, due_date=null. KHÔNG được bịa ngày.
   Không nhắc hạn → due_type=none.
6. Thảo luận, hỏi ý kiến, "để anh xem đã" mà không ai nhận việc → không phải task.
7. Một LÕI có thể chứa 0, 1 hoặc nhiều task. Không có gì → ops rỗng.

<NHAP_DANG_DO>{drafts}</NHAP_DANG_DO>
<NGU_CANH_CHI_DOC>{context}</NGU_CANH_CHI_DOC>
<TASK_DANG_MO>{open_tasks}</TASK_DANG_MO>
<LOI>{core}</LOI>
```

### 5.3 Schema đầu ra của LLM2

```json
{
  "ops": [
    {
      "op": "CREATE",
      "anchor_message_id": "m041",
      "supporting_message_ids": ["m041", "m043", "m045"],
      "title": "Gửi báo cáo tuần cho anh Hưng",
      "assignee_mention": "Linh",
      "assignee_evidence_message_id": "m041",
      "due_text": "trước thứ 5",
      "due_type": "calendar",
      "due_date": "2026-11-12",
      "due_evidence_message_id": "m045",
      "summary": "Anh Hưng giao Linh gửi báo cáo tuần, hạn thứ 5."
    },
    {
      "op": "UPDATE",
      "target_kind": "draft",
      "target_id": "d_2",
      "changes": [
        {"field": "due_date", "value": "2026-11-16", "evidence_message_id": "m047"}
      ]
    },
    {"op": "FINALIZE", "target_id": "d_3"}
  ]
}
```

Viết bằng pydantic, xuất JSON schema để truyền vào `response_format` dạng `json_schema` strict (mục 7). Ví dụ dưới đây viết gọn cho dễ đọc; schema thật là bản phẳng, mọi op có đủ trường và trường không dùng = `null`.

- `op` ∈ `CREATE` | `UPDATE` | `FINALIZE`.
- `UPDATE` dùng `target_kind` (`draft` | `task`), `target_id` và `changes`; mỗi phần tử `changes` có `field` ∈ `title` | `assignee_mention` | `due_text` | `due_type` | `due_date`, `value`, `evidence_message_id`.
- `due_type` ∈ `calendar` | `event` | `none`; `due_date` là `YYYY-MM-DD` hoặc `null`.
- Strict mode không kiểm tra được "trường nào bắt buộc với op nào"; việc đó do Validate làm.

## 6. Tham số

Một file `config.yaml`, ghi đè bằng `--set key=value`. Giá trị là điểm khởi đầu, chưa đo.

| Khoá | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `trigger_mode` | `llm1` | `llm1` \| `all` \| `rules_only` |
| `llm1_context` | 3 | Số tin trước đi kèm tin cần chấm |
| `llm1_threshold` | 0.30 | Ngưỡng `p`; thấp vì LLM1 lo recall |
| `context_messages` | 5 | Ngữ cảnh chỉ đọc trước lõi |
| `close_after_irrelevant` | 5 | Đóng sau K tin liên tiếp không liên quan |
| `idle_close_seconds` | 180 | Đóng sau T giây im lặng |
| `core_token_cap` | 3000 | Trần lõi theo token |
| `cap_search_tail` | 6 | Số tin cuối để tìm khoảng lặng khi cắt |
| `drafts_enabled` | `true` | Bật/tắt cơ chế nháp để so sánh |
| `draft_boundary_tail` | 5 | Bằng chứng trong N tin cuối → nháp |
| `draft_evidence_max` | 5 | Tin bằng chứng tối đa mỗi nháp |
| `draft_max_carries` | 3 | Số phiên tối đa nháp được mang qua |
| `open_tasks_max` | 10 | Task đã tạo đưa vào prompt |
| `llm_max_retries` | 2 | Retry khi lỗi parse hoặc mạng |

Quy ước thời gian, đưa nguyên vào prompt LLM2, cần business xác nhận: "cuối tuần" = thứ 6 tuần này; "đầu tuần sau" = thứ 2 tuần sau; "sớm nhất có thể" = `due_type: none`; "thứ N" đã qua trong tuần = thứ N tuần sau. Múi giờ `Asia/Ho_Chi_Minh`.

## 7. LLM: OpenAI cho bản thử nghiệm

Prototype gọi thẳng OpenAI **Chat Completions API** bằng model nhỏ. Không dùng Responses API vì LLM1 cần `logprobs`.

- Biến môi trường: `OPENAI_API_KEY`, `LLM1_MODEL`, `LLM2_MODEL`, và `OPENAI_BASE_URL` (tuỳ chọn, để sau này trỏ sang endpoint khác mà không sửa code).
- Mặc định gợi ý: LLM1 = `gpt-4.1-nano`, LLM2 = `gpt-4.1-mini`. Lý do chọn: đây là model **không suy luận**, nên nhận `temperature` và trả `logprobs`. Các model suy luận (dòng o-series và nhiều model GPT-5.x) thường từ chối `logprobs` hoặc `temperature`. Danh sách model của OpenAI thay đổi nhanh; muốn thử model khác chỉ cần đổi biến môi trường rồi chạy `probe`.
- **`probe`** gọi thử từng model và in ra: có trả `logprobs`/`top_logprobs` không, có nhận `response_format` dạng `json_schema` với `strict: true` không, có nhận `temperature` không. Ghi vào `runs/<id>/capabilities.json`. Model nào thiếu khả năng cần thiết thì `run` dừng với thông báo rõ, không chạy tiếp với số liệu sai.

**LLM1 (OpenAI không có `guided_choice`):** prompt yêu cầu trả đúng một từ `yes` hoặc `no`; gọi với `max_tokens=1`, `temperature=0`, `logprobs=true`, `top_logprobs=10`. Tính `p = P(yes) / (P(yes) + P(no))`, cộng dồn các biến thể token (`yes`, `Yes`, `  yes `…). Nếu cả hai đều không có trong top thì `p = 0.5` và ghi cảnh báo vào trace.

**LLM2:** `response_format = {"type": "json_schema", "json_schema": {"name": "ops", "strict": true, "schema": …}}`. Strict mode đòi mọi trường đều nằm trong `required` và `additionalProperties: false`, nên dùng **schema phẳng**: mỗi op là một object có đủ mọi trường, trường không áp dụng = `null` (kiểu `["string","null"]`). Validate (mục 4.4) kiểm tra theo `op`. Vẫn parse lại bằng pydantic. Nếu model trả `refusal` thay vì nội dung, ghi `refusal` vào trace và bỏ phiên.

- `temperature=0` và `seed` cố định, nhưng API không đảm bảo kết quả giống hệt giữa các lần gọi. **Cache đĩa** (khoá = hash model + prompt + tham số) là thứ làm chạy lại cho cùng kết quả; `--no-cache` để bỏ qua.
- Prompt cố định đứng đầu, phần biến đổi ở cuối, để hưởng prompt caching tự động của API.
- Retry có backoff khi gặp lỗi 429 hoặc 5xx. Trace ghi token vào/ra, độ trễ, số lần retry, cache hit. Báo cáo in tổng token và chi phí ước tính theo bảng giá khai báo trong `config.yaml`.
- `FakeLLM` cho unit test giữ nguyên.

**Hai lưu ý phải ghi trong README:**

1. **Chỉ gửi dữ liệu tự sinh lên API bên ngoài.** Không dùng chat thật của tổ chức trong bản thử nghiệm này.
2. **Số liệu không chuyển được sang model khác.** Kết quả trên model OpenAI trả lời câu hỏi *thiết kế pipeline có ổn không*, không phải *model nội bộ có đạt không*. Khi chuyển sang model nội bộ phải chạy lại toàn bộ đánh giá.

## 8. Đánh giá

`eval.py` đọc `tasks.jsonl`, `trace.jsonl`, `gold.jsonl` và in `report.md`. Đo ở cấp task, không ở cấp tin.

**Ghép task với nhãn:** cùng `conversation_key`, và `anchor_message_id` của task nằm trong `supporting_message_ids` của nhãn (hoặc hai tập `supporting_message_ids` giao nhau ít nhất 2 tin). Ghép tham lam theo số tin giao nhau, mỗi nhãn một task. Task không ghép được = false positive; task thứ hai khớp cùng nhãn = **trùng**, đếm riêng.

| Chỉ số | Cách tính | Ngưỡng |
| --- | --- | --- |
| Precision | task khớp / tổng task | ≥ 80% |
| Recall | nhãn được khớp / tổng nhãn | ≥ 50% |
| Recall kích hoạt | nhãn có anchor nằm trong lõi một phiên / tổng nhãn | ≥ 95% |
| Tỉ lệ trùng | task trùng / tổng task | ≤ 5% |
| Người nhận đúng | trên task khớp có `assignee_id` | ≥ 90% |
| Hạn sự kiện bị bịa ngày | nhãn `event` mà task có `due_date` (trước khi Validate sửa) | ≤ 5% |
| Hạn lịch đúng | nhãn `calendar`, `due_date` trùng khớp | báo cáo |
| Báo nhầm trên hard negative | hard negative là anchor của một task / tổng | báo cáo |
| Lượt gọi và token | LLM1, LLM2, quy về 1.000 tin | báo cáo |
| Phiên đóng vì trần, phân bố `carries` | từ trace | báo cáo |

- Mọi tỉ lệ kèm cỡ mẫu và khoảng tin cậy 95% Wilson; dưới 30 mẫu thì ghi rõ "chưa đủ để kết luận".
- Cắt mọi chỉ số theo `scenario_tags`.
- **Phần quan trọng nhất của báo cáo:** liệt kê từng false positive, false negative và task trùng, kèm các tin liên quan và đường đi qua pipeline (điểm LLM1 từng tin, phiên nào, `close_reason`, op LLM2 trả về, op bị loại). Mỗi false negative ghi rõ hỏng ở đâu: *không được kích hoạt* hay *được kích hoạt nhưng LLM2 bỏ sót*.

**Thí nghiệm chính:** chạy cùng dữ liệu với `trigger_mode` = `all`, `llm1`, `rules_only`, và `drafts_enabled` = `true`/`false`. `compare.py` ghép các `report` thành một bảng. Nhờ cache, LLM1 chỉ gọi một lần cho mọi ngưỡng.

## 9. Kịch bản test

Bộ dữ liệu đánh giá phải có ít nhất 3 ca cho mỗi tag dưới đây. Các ca biên hiếm gặp ngẫu nhiên, nên phải viết chủ đích.

| Tag | Mô tả | Kết quả đúng |
| --- | --- | --- |
| `single_message` | Giao việc gọn trong một tin, có @mention và hạn | 1 task đủ trường |
| `multi_message` | Giao ở tin 1, nhận ở tin 3, chốt hạn ở tin 5 | 1 task, hạn từ tin 5 |
| `implicit_ask` | "Cái này ai làm được không" → vài tin sau "ok em" | 1 task, người nhận là người nói "ok em" |
| `hard_negative` | "Để anh xem đã", "mình nên làm hướng nào" | 0 task |
| `event_deadline` | "Xong trước buổi UAT nhé" | `due_type=event`, `due_date=null` |
| `relative_deadline` | "Cuối tuần", "mai", "thứ 5" khi hôm nay là thứ 6 | Ngày đúng quy ước |
| `no_assignee` | "Em làm giúp anh cái này" trong nhóm nhiều người | `assignee_id=null` |
| `two_tasks_one_session` | Hai việc giao liền nhau trong 6 tin | 2 task, không trộn trường |
| `cap_split` | Task 1 ở tin 1–15, task 2 ở tin 16–30, không khoảng lặng | 2 task, không có task thứ 3 |
| `long_task` | Một task bàn 50–60 tin, đổi hạn giữa chừng, có task khác chen vào | 2 task; hạn là giá trị sau cùng |
| `later_update` | Task đã tạo; nhiều tin sau "chốt thứ 6 nhé" | UPDATE task cũ, không tạo task mới |

Unit test với `FakeLLM` (trả kết quả định sẵn theo id tin), không gọi mạng:

- [ ] Không tin nào thuộc lõi của hai phiên.
- [ ] `streak`, `idle`, `cap` đóng phiên đúng lúc và ghi đúng `close_reason`; `cap` cắt đúng khoảng lặng lớn nhất.
- [ ] Validate loại CREATE có anchor ngoài lõi và UPDATE thiếu bằng chứng; ép `due_date=null` khi `event`.
- [ ] Nháp được tạo, mang sang và chốt đúng cả ba điều kiện; bằng chứng mang sang không vượt `draft_evidence_max`.
- [ ] Chạy lại với cache cho kết quả giống hệt.

## 10. Cấu trúc và cách làm

Python 3.11+, `pydantic` v2, `openai` SDK (Chat Completions API), `pyyaml`, `pytest`. Không cơ sở dữ liệu, không framework agent, không model embedding.

```
proto/
  README.md
  config.yaml
  prompts/llm1_v1.txt  prompts/llm2_v1.txt
  data/sample/  messages.jsonl  members.json  gold.jsonl
  detect/
    models.py       # pydantic: Message, Op*, Draft, Task
    llm.py          # client, probe, cache đĩa, FakeLLM
    trigger.py      # LLM1 + luật
    session.py      # phiên, watermark, cắt theo khoảng lặng
    extract.py      # dựng prompt, gọi LLM2, validate, gán người nhận
    drafts.py
    run.py          # đọc file → tasks.jsonl + trace.jsonl
    eval.py  compare.py
  tests/
  runs/
```

```
python -m detect.llm probe
python -m detect.run  --data data/sample [--set trigger_mode=all]
python -m detect.eval --run runs/<id> --data data/sample
python -m detect.compare runs/*
```

**Ba bước, dừng sau mỗi bước để người dùng xem:**

1. Logic thuần với `FakeLLM`: trigger, session, validate, nháp. Xong khi unit test mục 9 xanh.
2. Nối LLM thật: `probe`, LLM1, LLM2, cache. Xong khi chạy hết `data/sample` ra `tasks.jsonl`.
3. `eval` và `compare`. Xong khi báo cáo có đủ chỉ số mục 8 và danh sách lỗi kèm đường đi.

Khi đặc tả thiếu hoặc mâu thuẫn: hỏi lại thay vì tự đoán; nếu đã chọn một cách hiểu thì ghi vào `DECISIONS.md`. Ưu tiên code ngắn, dễ đọc hơn là tổng quát.

## 11. Câu hỏi prototype phải trả lời

- [ ] Model OpenAI đã chọn có trả `logprobs` và nhận `json_schema` strict không? (`probe` trả lời; đây là việc đầu tiên.)
- [ ] Cổng LLM1 có tăng precision so với `all` mà không làm tụt recall kích hoạt dưới 95% không? Luật đơn thuần đạt tới đâu?
- [ ] False negative chủ yếu do *không được kích hoạt* hay do *LLM2 bỏ sót*?
- [ ] Cơ chế nháp có thực sự giảm task trùng ở `cap_split` và `long_task` không (so `drafts_enabled` bật/tắt)?
- [ ] LLM2 có bịa ngày cho hạn sự kiện không, trước khi Validate sửa?
- [ ] Model nhỏ (`nano`) có đủ cho LLM2 không, hay cần model lớn hơn? Chạy LLM2 với hai model trên cùng dữ liệu để biết.

Nếu bộ đánh giá có dưới khoảng 100 task nhãn, kết quả chỉ nên đọc như tín hiệu định hướng, chưa đủ để kết luận.
