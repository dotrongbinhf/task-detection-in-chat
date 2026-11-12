# AI Reminder — Prototype nhanh: phát hiện và tạo task (v2)

Nov 11, 2026 · @dotrongbinhf

## 0. Thay đổi so với v1

| # | Thay đổi | Lý do |
| --- | --- | --- |
| 1 | Bỏ cơ chế **nháp**; thay bằng task có `status` (`tentative` / `active` / `cancelled`) và trường `commitment` | Nháp chỉ xử lý phiên đóng vì `cap`, trong khi phần lớn task bị cắt ngang do `idle`/`streak` (vd. hỏi "ai làm được" rồi 20 phút sau mới có "ok em"). Task `tentative` xử lý mọi lý do đóng phiên bằng một cơ chế UPDATE duy nhất. |
| 2 | `idle_close_seconds` 180 → 1800 | 3 phút quá ngắn so với nhịp chat thật. |
| 3 | Luật thời gian còn kích hoạt khi hội thoại **có task vừa được chạm tới** (`recent_task_hours`), không chỉ khi có phiên mở | Hạn hay được chốt muộn sau khi phiên đã đóng. |
| 4 | Regex thời gian viết lại, khớp trên văn bản đã bỏ dấu; bỏ `mai`, `hạn` trơn | `mai` trùng tên người, `hạn` khớp "hạn chế", "giới hạn"; v1 bỏ sót "thứ năm", "T5", "thu 5". |
| 5 | **LLM2 không tự tính ngày**: trả biểu thức hạn có cấu trúc, code quy ra `due_date` | Model nhỏ hay tính sai thứ ↔ ngày; code thì tất định và test được. |
| 6 | Người nhận là **danh sách**; thêm tham chiếu `SPEAKER` / `ADDRESSEE` | Nhiều người nhận, tự nhận việc, và DM ("em làm nhé"). |
| 7 | Thêm op `CANCEL`; thêm `commitment = self_commit` | Huỷ việc và tự nhận việc rất phổ biến; thiếu chúng sinh nhắc thừa hoặc bỏ sót. |
| 8 | Tin nhắn có `reactions`; reaction được đưa vào luồng như một sự kiện | 👍 là cách nhận việc phổ biến trên Teams. |
| 9 | Thứ tự làm: **dữ liệu + eval + baseline trước**, LLM1 sau | Cần mức sàn để biết LLM1 và task `tentative` có đáng giữ không. |
| 10 | Ghép nhãn chặt hơn; thêm đường cong ngưỡng LLM1; ngưỡng chỉ số có ghi lý do; ≥ 100 task nhãn | Tránh số liệu đẹp giả do `supporting_message_ids` quá rộng. |

Các quyết định trên được chép vào `DECISIONS.md` khi bắt đầu code.

## 1. Mục tiêu và phạm vi

Xây một **script chạy offline** đọc luồng tin nhắn từ file, phát hiện việc được giao và xuất ra danh sách task, rồi chấm so với bộ nhãn do người gán. Chỉ một câu hỏi cần trả lời: **luồng LLM1 → phiên → LLM2 có nhận ra đúng và đủ task không, và hỏng ở đâu.**

Bài toán: trong chat công việc, việc được giao bằng lời ("Linh gửi anh báo cáo trước thứ 5 nhé"), thường rải qua nhiều tin, có khi cách nhau hàng chục phút. Hệ thống cần biến chúng thành task có nội dung, người nhận, hạn, và truy ngược được về tin gốc.

**Trong phạm vi "task":**

- Việc được giao cho người cụ thể (`commitment = assigned`).
- Việc tự nhận không ai giao: "mai em gửi anh bản cuối nhé" (`self_commit`).
- Lời hỏi "ai làm được" chưa có người nhận (`open_ask`): giữ ở `tentative`, chỉ xuất khi có người nhận.
- Huỷ một task đã có: "thôi khỏi làm cái đó nhé" → `CANCEL`.

**Ngoài phạm vi:** đánh dấu hoàn thành ("em gửi rồi ạ") — hệ thống chỉ cần **không tạo task mới** từ tin này. Teams, Graph, Jira, card, người dùng xác nhận, nhắc việc, cơ sở dữ liệu, UI, xử lý tin sửa/xoá. Không dựng interface "để sau này cắm vào" — code ngắn nhất có thể để trả lời câu hỏi trên.

LLM là model nhỏ của OpenAI, chọn qua biến môi trường (mục 7). Vì gọi API bên ngoài, bản này **chỉ chạy trên dữ liệu tự sinh**.

## 2. Pipeline

Mỗi sự kiện (tin nhắn hoặc reaction) đi qua Trigger đúng một lần; mỗi phiên đóng sinh đúng một lượt gọi LLM2. Toàn bộ chạy tuần tự trong một process.

```
messages.jsonl  → dàn thành luồng sự kiện (tin + reaction), sắp theo created_at, nhóm theo conversation_key
   ▼
Trigger:  LLM1(tin + 3 tin trước) → p  +  luật (@mention, reply/reaction vào tin đã đánh dấu, mốc thời gian)
   ▼
Session:  mở / kéo dài / đóng phiên theo conversation_key, có watermark
   ▼
LLM2:     [ngữ cảnh chỉ đọc] + [task đang mở trong hội thoại, gồm tentative] + [lõi]
          → ops: CREATE | UPDATE | CANCEL
   ▼
Validate: loại op sai luật; quy biểu thức hạn ra ngày bằng code; gán người nhận
   ▼
Task store (trong bộ nhớ): tentative / active / cancelled, có field_history
   ▼
Hết file → tasks.jsonl (active + cancelled) + trace.jsonl  →  eval.py  →  report.md
```

Thời gian là thời gian mô phỏng lấy từ `created_at`, không dùng đồng hồ hệ thống: trước khi xử lý mỗi sự kiện, đóng mọi phiên đã im lặng quá hạn tính tới `created_at` của sự kiện đó; hết file thì đóng tất cả.

**Reaction thành sự kiện:** mỗi reaction sinh một sự kiện giả có id `<message_id>#r<n>`, thời điểm là `created_at` của reaction, hiển thị trong prompt dạng `[m041#r1 | 09:20 thứ 2 09/11 | Linh] 👍 (reaction vào m041)`. Sự kiện reaction **không gọi LLM1**; nó chỉ được tính là relevant qua luật `reply_to_flagged`.

## 3. Dữ liệu

Ba file trong `data/<dataset>/`, thêm `hard_negatives.txt`.

**`messages.jsonl`** — mỗi dòng một tin:

```json
{"id": "m041", "conversation_id": "ch_design", "conversation_type": "group", "thread_root_id": null,
 "created_at": "2026-11-09T09:12:00+07:00", "author_id": "u_hung", "author_name": "Hưng",
 "text": "Linh gửi báo cáo tuần cho anh trước thứ 5 nhé",
 "mentions": [{"user_id": "u_linh", "name": "Linh"}], "reply_to_id": null,
 "reactions": [{"user_id": "u_linh", "type": "like", "created_at": "2026-11-09T09:20:00+07:00"}]}
```

- `conversation_type` ∈ `group` | `dm`.
- `conversation_key` = `conversation_id`, nối thêm `;thread_root_id` nếu có.

**`members.json`** — `{conversation_id: [{user_id, display_name, aliases}]}`, dùng để gán người nhận.

**`gold.jsonl`** — mỗi dòng một task thật do người gán:

```json
{"gold_id": "g07", "conversation_key": "ch_design",
 "anchor_message_id": "m041", "supporting_message_ids": ["m041", "m043", "m045"],
 "title": "Gửi báo cáo tuần cho anh Hưng", "commitment": "assigned",
 "assignee_ids": ["u_linh"], "due_type": "calendar", "due_date": "2026-11-12",
 "final_status": "active", "scenario_tags": ["multi_message"]}
```

- `commitment` ∈ `assigned` | `self_commit` | `open_ask`. Nhãn `open_ask` chỉ tồn tại nếu cuối cùng **có người nhận**; khi đó ghi `commitment = assigned` và `anchor_message_id` = câu hỏi ban đầu. Lời hỏi không ai nhận thì **không có nhãn**, và id của nó vào `hard_negatives.txt`.
- `assignee_ids = []` khi không xác định được.
- `due_type` ∈ `calendar` | `event` | `none`; với `event` thì `due_date = null`.
- `final_status` ∈ `active` | `cancelled`.

**Hướng dẫn viết dữ liệu đánh giá** (để lỗi thật lộ ra):

- Khoảng cách giữa các tin giống thật: phần lớn vài chục giây đến vài phút, **có chỗ 10–60 phút, có chỗ qua ngày**.
- Ít nhất 30% tin viết không dấu, viết tắt ("e", "a", "dc", "ok a", "ko"), xen tiếng Anh ("deadline", "EOD", "review giúp a").
- Có kênh nhóm nhiều chủ đề xen kẽ, có DM, có thread.
- Có người tên trùng từ thời gian (Mai, Năm…), có "hạn chế", "giới hạn", phân số "1/2".

Claude Code tạo `data/sample/` khoảng 40 tin, 5 task để chạy thông code. **Bộ dữ liệu đánh giá do người viết**, không phải người viết prompt, mục tiêu **≥ 100 task nhãn** và ≥ 3 ca mỗi tag ở mục 9.

## 4. Thành phần

### 4.1 Trigger

- **LLM1** nhận tin hiện tại + `llm1_context` tin trước trong cùng `conversation_key`, trả `p` = xác suất chuẩn hoá của `yes` so với `no`, tính từ `top_logprobs` (mục 7).
- **Luật** tính bằng code, trên văn bản đã chuẩn hoá (chữ thường, bỏ dấu, `đ→d`):
  - `has_mention`: tin @mention một người.
  - `reply_to_flagged`: tin reply, hoặc reaction, vào một tin đã relevant hoặc là bằng chứng của task đang mở.
  - `has_time_expr`: regex thời gian bên dưới. **Chỉ tính** khi hội thoại đang có phiên mở, **hoặc** có task được tạo/cập nhật trong `recent_task_hours` giờ gần nhất.
- `relevant = p >= llm1_threshold OR has_mention OR reply_to_flagged OR has_time_expr`.
- `trigger_mode`:
  - `all` — mọi sự kiện đều relevant. Phiên chỉ đóng vì `idle` hoặc `cap`, nên đây chính là **baseline** "cắt theo khoảng lặng rồi gửi hết cho LLM2".
  - `llm1` (mặc định) — công thức trên.
  - `rules_only` — công thức trên, bỏ `p`.

**Regex thời gian** (khớp trên văn bản bỏ dấu, có `\b` hai đầu):

```
thu\s*(2|3|4|5|6|7|hai|ba|tu|nam|sau|bay)     t[2-7]     chu nhat|\bcn\b
(hom|sang|chieu|toi) nay      (ngay|sang|chieu|toi) mai      mai\s+(gui|xong|lam|nop|truoc)
cuoi tuan|dau tuan|tuan (nay|sau|toi)         eod|eow|asap
truoc\s*\d{1,2}\s*(h|gio)     \d{1,2}h(\d{2})?
(ngay\s*)?\d{1,2}/\d{1,2}(?!/?\d)             deadline|\bdl\b|han (chot|nop|cuoi)|han (la|vao|truoc)|han:
```

Kèm `tests/test_time_regex.py` liệt kê các câu **phải khớp** ("thu 5 nhe", "T5 gui a", "sang mai", "EOD hom nay", "han la thu 6") và **không được khớp** ("chi Mai oi", "han che bot", "gioi han 3 nguoi", "chia 1/2 ra"). Câu nào sai thì sửa regex, không sửa test.

### 4.2 Session

Máy trạng thái theo `conversation_key`:

```
KHÔNG CÓ PHIÊN:  relevant → mở phiên, lõi bắt đầu từ sự kiện này
PHIÊN MỞ:       thêm mọi sự kiện vào lõi; relevant → streak = 0, ngược lại streak += 1
ĐÓNG khi (ghi close_reason):
  streak : streak >= close_after_irrelevant        (không áp dụng ở trigger_mode=all)
  idle   : im lặng >= idle_close_seconds
  cap    : token(lõi) >= core_token_cap
```

- Đóng vì `cap`: cắt tại khoảng cách thời gian lớn nhất trong `cap_search_tail` tin cuối; phần sau điểm cắt mở ngay phiên mới.
- **Watermark**: lõi phiên sau bắt đầu sau sự kiện cuối của lõi trước. Assert: không sự kiện nào thuộc hai lõi.
- **Ngữ cảnh chỉ đọc** = `context_messages` tin ngay trước lõi, được phép trùng lõi cũ.

Task bị cắt ngang qua ranh giới phiên (bất kể lý do đóng) không cần cơ chế riêng: phiên trước tạo task (có thể `tentative` hoặc thiếu trường), phiên sau thấy nó trong khối TASK ĐANG MỞ và UPDATE.

### 4.3 Task store và trạng thái

| `status` | Khi nào | Xuất ra `tasks.jsonl`? |
| --- | --- | --- |
| `tentative` | `commitment = open_ask` và chưa có người nhận | Không. Hết file mà vẫn `tentative` → ghi `expired_open_ask` vào trace. |
| `active` | Mọi task còn lại; `open_ask` chuyển sang `active` khi UPDATE gán người nhận (và `commitment` → `assigned`) | Có |
| `cancelled` | Sau `CANCEL` | Có, kèm `status`, để eval chấm việc huỷ |

- `tentative_enabled` (mặc định `true`): khi `false`, Validate loại mọi CREATE có `commitment = open_ask` (quay về hành vi v1: "chưa ai nhận thì không phải task"). Đây là thí nghiệm thay cho bật/tắt nháp.
- Mọi UPDATE/CANCEL ghi vào `field_history` (giá trị cũ, giá trị mới, tin bằng chứng, phiên).

### 4.4 LLM2 và prompt

Prompt gồm ba khối theo thứ tự: ngữ cảnh chỉ đọc, task đang mở trong cùng `conversation_key`, lõi.

- **Task đang mở** = task `tentative` + `active` được tạo/cập nhật trong `open_tasks_days` ngày gần nhất, tối đa `open_tasks_max`, mới chạm tới trước. Mỗi task: id, `status`, `commitment`, tiêu đề, người nhận, hạn, và tối đa `open_task_evidence_max` tin bằng chứng gần nhất (nguyên văn).
- Mỗi tin dạng `[m041 | 09:12 thứ 2 09/11 | Hưng] nội dung (@Linh=u_linh)`; reaction như mục 2.
- Kèm: thời điểm tham chiếu = `created_at` sự kiện cuối lõi; loại hội thoại (`group`/`dm`); **bảng lịch 14 ngày** bắt đầu từ thứ 2 tuần hiện tại (để LLM hiểu "tuần này/tuần sau", dù code mới là bên tính ngày).

Op:

- `CREATE`: anchor trong lõi, không khớp task đang mở.
- `UPDATE`: sửa task đang mở; mỗi trường đổi phải có `evidence_message_id` trong lõi. Áp thẳng vào task, ghi `field_history`.
- `CANCEL`: huỷ task đang mở; `evidence_message_id` trong lõi.
- Phiên mà LLM2 trả rỗng: ghi `llm1_false_trigger` vào trace.

### 4.5 Validate

**Loại op** (ghi lý do vào trace) khi: CREATE có anchor ngoài lõi; id tin không có trong prompt; UPDATE/CANCEL thiếu hoặc sai bằng chứng, hoặc `target_id` không tồn tại; CREATE `open_ask` khi `tentative_enabled = false`.

**Quy hạn ra ngày** (`resolve_due`, thuần code, có unit test):

- `due_kind = none` → `due_type = none`.
- `due_kind = event` → `due_type = event`, `due_date = null`. Nếu LLM vẫn điền bất kỳ trường ngày nào → bỏ đi, ghi `fabricated_date_blocked`.
- Các `due_kind` còn lại → `due_type = calendar`, tính `due_date` từ thời điểm tham chiếu theo quy ước thời gian (mục 6). Không quy được (vd. ngày 31/2) → `due_date = null`, ghi `unresolvable_due`.

**Gán người nhận** — với mỗi phần tử của `assignee_refs`:

1. Là `user_id` có trong `mentions` của tin bằng chứng → dùng luôn.
2. `SPEAKER` → `author_id` của `assignee_evidence_message_id` (tự nhận việc, "để em làm", reaction 👍).
3. `ADDRESSEE` → chỉ hợp lệ khi `conversation_type = dm`: người còn lại trong DM. Ở nhóm → bỏ, ghi `addressee_in_group`.
4. Còn lại là tên: bỏ dấu, không phân biệt hoa thường, bỏ tiền tố xưng hô (`anh`, `chị`, `em`, `bạn`, `a`, `c`, `e`), khớp với `display_name`/`aliases` trong `members.json`. Khớp đúng một người mới gán.

Phần tử không gán được thì bỏ; giữ nguyên chuỗi gốc trong `assignee_refs_raw` để báo cáo.

**JSON hỏng** → retry `llm_max_retries` lần rồi bỏ phiên, ghi `parse_failure`.

### 4.6 Đầu ra

`runs/<id>/tasks.jsonl`: mỗi task gồm `task_id`, `conversation_key`, `anchor_message_id`, `supporting_message_ids`, `title`, `commitment`, `status`, `assignee_refs_raw`, `assignee_ids`, `due_text`, `due_kind` + các trường hạn thô, `due_type`, `due_date`, bằng chứng từng trường, `field_history`, `sessions` (các phiên đã chạm tới task), `prompt_version`, `model`. Khoá duy nhất `(conversation_key, anchor_message_id)`; vi phạm là lỗi, không nuốt im.

`runs/<id>/trace.jsonl`: mỗi sự kiện (p, tín hiệu luật, relevant), mỗi phiên (lõi, ngữ cảnh, task đang mở đưa vào prompt, `close_reason`), mỗi lượt gọi (prompt, đầu ra thô, token, độ trễ, cache hit), mỗi op bị loại, mỗi `expired_open_ask`.

## 5. Prompt và JSON schema

Prompt lưu thành file trong `prompts/` (`llm1_v2.txt`, `llm2_v2.txt`), version nằm trong tên file và được ghi vào mọi bản ghi. Dưới đây là bản khởi đầu; Claude Code giữ nguyên ý, được chỉnh câu chữ.

### 5.1 LLM1

```text
Bạn phân loại tin nhắn trong chat công việc tiếng Việt (có thể không dấu, viết tắt, xen tiếng Anh).
Câu hỏi: tin nhắn CUỐI CÙNG có tham gia vào việc giao hoặc nhận một việc cụ thể không?
Tính là "yes" nếu tin đó: giao việc; nhận việc ("ok em", "để em làm", "dc a");
tự hứa sẽ làm một việc ("mai em gửi anh nhé"); hỏi ai làm được việc gì;
bổ sung hạn / người làm / nội dung cho một việc vừa được nói tới; hoặc huỷ một việc đã giao.
Tính là "no" nếu chỉ là thảo luận, hỏi ý kiến, chia sẻ thông tin, báo đã làm xong, chào hỏi,
hoặc câu lửng lơ như "để anh xem đã" mà không ai nhận việc.
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
Bạn trích xuất việc cần làm từ chat công việc tiếng Việt (có thể không dấu, viết tắt, xen tiếng Anh).
Thời điểm hiện tại: {now}, múi giờ Asia/Ho_Chi_Minh. Loại hội thoại: {conversation_type}.
Lịch tham chiếu:
{calendar_14_days}
Quy ước thời gian: {time_conventions}

QUY TẮC
1. Chỉ CREATE khi câu giao việc / tự nhận việc / hỏi ai làm (anchor) nằm trong khối LÕI.
   Khối NGỮ CẢNH chỉ để hiểu, không tạo task từ đó.
2. Nếu LÕI bổ sung hoặc thay đổi thông tin cho một TASK ĐANG MỞ → UPDATE, không CREATE.
   Ví dụ: có người nhận việc cho task tentative; đổi hạn; đổi người làm.
   Mỗi trường thay đổi phải ghi id tin bằng chứng nằm trong LÕI.
3. Nếu LÕI huỷ một TASK ĐANG MỞ ("thôi khỏi", "không cần nữa") → CANCEL kèm id tin bằng chứng.
4. commitment:
   - assigned: có người được giao hoặc đã nhận.
   - self_commit: người nói tự hứa làm, không ai giao.
   - open_ask: hỏi ai làm được nhưng trong LÕI chưa ai nhận.
5. Người nhận (assignee_refs), có thể nhiều người:
   - Người được @mention: ghi user_id hiển thị sau dấu "=".
   - Người nói của tin bằng chứng tự nhận việc hoặc thả reaction đồng ý: ghi "SPEAKER".
   - Trong DM, giao cho người đối diện bằng đại từ ("em làm nhé"): ghi "ADDRESSEE".
   - Còn lại: ghi đúng tên như xuất hiện trong tin. Không chắc thì để danh sách rỗng. Không đoán.
6. Hạn: KHÔNG tự tính ngày. Điền due_kind và các trường tương ứng:
   - "mai", "hôm nay", "ngày kia" → relative_day, due_day_offset = 1 / 0 / 2.
   - "thứ 5", "T6 tuần sau" → weekday, due_weekday (thứ 2 = 1 … chủ nhật = 7), due_week_offset (0 = tuần này, 1 = tuần sau; không nói rõ thì null).
   - "cuối tuần", "đầu tuần sau" → week_part, due_week_part = end / start, due_week_offset.
   - "12/11", "ngày 15" → absolute, due_abs_day, due_abs_month (không nói tháng thì null).
   - "trước buổi UAT", "xong trước release" → event. KHÔNG điền trường ngày nào.
   - Không nhắc hạn, hoặc "sớm nhất có thể" → none.
   Luôn ghi due_text đúng như trong tin.
7. Thảo luận, hỏi ý kiến, "để anh xem đã", hoặc báo đã làm xong ("em gửi rồi ạ") → không phải task mới.
8. Một LÕI có thể chứa 0, 1 hoặc nhiều op. Không có gì → ops rỗng.

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
      "commitment": "assigned",
      "assignee_refs": ["u_linh"],
      "assignee_evidence_message_id": "m041",
      "due_text": "trước thứ 5",
      "due_kind": "weekday",
      "due_day_offset": null, "due_weekday": 4, "due_week_offset": 0,
      "due_week_part": null, "due_abs_day": null, "due_abs_month": null,
      "due_evidence_message_id": "m045",
      "summary": "Anh Hưng giao Linh gửi báo cáo tuần, hạn thứ 5."
    },
    {
      "op": "UPDATE",
      "target_id": "t_2",
      "changes": [
        {"field": "assignee_refs", "value": ["SPEAKER"], "evidence_message_id": "m052"},
        {"field": "commitment", "value": "assigned", "evidence_message_id": "m052"}
      ]
    },
    {"op": "CANCEL", "target_id": "t_3", "evidence_message_id": "m060"}
  ]
}
```

Viết bằng pydantic, xuất JSON schema để truyền vào `response_format` dạng `json_schema` strict (mục 7). Ví dụ trên viết gọn cho dễ đọc; schema thật là bản phẳng, mọi op có đủ trường và trường không dùng = `null`.

- `op` ∈ `CREATE` | `UPDATE` | `CANCEL`.
- `commitment` ∈ `assigned` | `self_commit` | `open_ask`.
- `due_kind` ∈ `none` | `event` | `relative_day` | `weekday` | `week_part` | `absolute`; `due_week_part` ∈ `end` | `start` | `null`.
- `UPDATE.changes[].field` ∈ `title` | `commitment` | `assignee_refs` | `due` ; với `due`, `value` là object chứa `due_text`, `due_kind` và các trường hạn. Để strict mode chấp nhận một kiểu duy nhất, `value` khai báo là chuỗi JSON (`string`) và Validate parse lại; ví dụ ở trên hiển thị giá trị đã parse cho dễ đọc.
- Strict mode không kiểm tra được "trường nào bắt buộc với op nào"; việc đó do Validate làm.

## 6. Tham số

Một file `config.yaml`, ghi đè bằng `--set key=value`. Giá trị là điểm khởi đầu, chưa đo.

| Khoá | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `trigger_mode` | `llm1` | `all` (baseline) \| `llm1` \| `rules_only` |
| `llm1_context` | 3 | Số tin trước đi kèm tin cần chấm |
| `llm1_threshold` | 0.30 | Ngưỡng `p`; thấp vì LLM1 lo recall |
| `recent_task_hours` | 24 | Luật thời gian vẫn tính nếu có task chạm tới trong N giờ |
| `context_messages` | 5 | Ngữ cảnh chỉ đọc trước lõi |
| `close_after_irrelevant` | 5 | Đóng sau K tin liên tiếp không liên quan |
| `idle_close_seconds` | 1800 | Đóng sau T giây im lặng |
| `core_token_cap` | 3000 | Trần lõi theo token |
| `cap_search_tail` | 6 | Số tin cuối để tìm khoảng lặng khi cắt |
| `tentative_enabled` | `true` | Bật/tắt task `open_ask` tentative để so sánh |
| `open_tasks_max` | 10 | Task đang mở đưa vào prompt |
| `open_tasks_days` | 7 | Chỉ đưa task chạm tới trong N ngày |
| `open_task_evidence_max` | 3 | Tin bằng chứng nguyên văn mỗi task đang mở |
| `llm_max_retries` | 2 | Retry khi lỗi parse hoặc mạng |

**Quy ước thời gian** — đưa nguyên vào prompt LLM2 và cài trong `resolve_due`; **cần business xác nhận**:

- "cuối tuần" = thứ 6 tuần này; "cuối tuần sau" = thứ 6 tuần sau.
- "đầu tuần sau" = thứ 2 tuần sau.
- "thứ N" không nói tuần: nếu thứ N **sau hôm nay** trong tuần này → tuần này; nếu **đã qua hoặc là hôm nay** → tuần sau. ("thứ 5" nói vào thứ 5 = thứ 5 tuần sau; muốn hôm nay thì người ta nói "hôm nay".)
- "ngày D" không nói tháng: tháng này nếu D chưa qua, ngược lại tháng sau.
- "sớm nhất có thể", "ASAP" = `due_type: none`.
- Giờ trong ngày ("trước 3h") không lưu ở bản này; chỉ giữ trong `due_text`.
- Múi giờ `Asia/Ho_Chi_Minh`.

## 7. LLM: OpenAI cho bản thử nghiệm

Prototype gọi thẳng OpenAI **Chat Completions API** bằng model nhỏ. Không dùng Responses API vì LLM1 cần `logprobs`.

- Biến môi trường: `OPENAI_API_KEY`, `LLM1_MODEL`, `LLM2_MODEL`, và `OPENAI_BASE_URL` (tuỳ chọn, để sau này trỏ sang endpoint khác mà không sửa code).
- Mặc định gợi ý: LLM1 = `gpt-4.1-nano`, LLM2 = `gpt-4.1-mini` — model **không suy luận**, nên nhận `temperature` và trả `logprobs`. Danh sách model của OpenAI thay đổi nhanh và model cũ có thể đã ngừng; **chạy `probe` trước tiên**, nếu model mặc định không còn thì chọn model không suy luận nhỏ nhất còn hỗ trợ `logprobs` và ghi vào `DECISIONS.md`.
- **`probe`** gọi thử từng model và in ra: có trả `logprobs`/`top_logprobs` không, có nhận `response_format` dạng `json_schema` với `strict: true` không, có nhận `temperature` không. Ghi vào `runs/<id>/capabilities.json`. Model nào thiếu khả năng cần thiết thì `run` dừng với thông báo rõ, không chạy tiếp với số liệu sai.

**LLM1 (OpenAI không có `guided_choice`):** prompt yêu cầu trả đúng một từ `yes` hoặc `no`; gọi với `max_tokens=1`, `temperature=0`, `logprobs=true`, `top_logprobs=10`. Tính `p = P(yes) / (P(yes) + P(no))`, cộng dồn các biến thể token (`yes`, `Yes`, ` yes`…). Nếu cả hai đều không có trong top thì `p = 0.5` và ghi cảnh báo vào trace.

**LLM2:** `response_format = {"type": "json_schema", "json_schema": {"name": "ops", "strict": true, "schema": …}}`. Strict mode đòi mọi trường đều nằm trong `required` và `additionalProperties: false`, nên dùng **schema phẳng**: mỗi op là một object có đủ mọi trường, trường không áp dụng = `null`. Validate (mục 4.5) kiểm tra theo `op`. Vẫn parse lại bằng pydantic. Nếu model trả `refusal` thay vì nội dung, ghi `refusal` vào trace và bỏ phiên.

- `temperature=0` và `seed` cố định, nhưng API không đảm bảo kết quả giống hệt giữa các lần gọi. **Cache đĩa** (khoá = hash model + prompt + tham số) là thứ làm chạy lại cho cùng kết quả; `--no-cache` để bỏ qua.
- Prompt cố định đứng đầu, phần biến đổi ở cuối, để hưởng prompt caching tự động của API.
- Retry có backoff khi gặp lỗi 429 hoặc 5xx. Trace ghi token vào/ra, độ trễ, số lần retry, cache hit. Báo cáo in tổng token và chi phí ước tính theo bảng giá khai báo trong `config.yaml`.
- `FakeLLM` cho unit test: trả kết quả định sẵn theo id tin (LLM1) và theo tập id lõi (LLM2).

**Hai lưu ý phải ghi trong README:**

1. **Chỉ gửi dữ liệu tự sinh lên API bên ngoài.** Không dùng chat thật của tổ chức trong bản thử nghiệm này.
2. **Số liệu không chuyển được sang model khác.** Kết quả trên model OpenAI trả lời câu hỏi *thiết kế pipeline có ổn không*, không phải *model nội bộ có đạt không*. Khi chuyển sang model nội bộ phải chạy lại toàn bộ đánh giá.

## 8. Đánh giá

`eval.py` đọc `tasks.jsonl`, `trace.jsonl`, `gold.jsonl`, `hard_negatives.txt` và in `report.md`. Đo ở cấp task, không ở cấp tin.

**Ghép task với nhãn:** cùng `conversation_key`, và **một trong hai**: `anchor_message_id` của task nằm trong `supporting_message_ids` của nhãn; hoặc `anchor_message_id` của nhãn nằm trong `supporting_message_ids` của task. Ghép tham lam, ưu tiên cặp trùng anchor, sau đó theo số tin giao nhau; mỗi nhãn một task. Task không ghép được = false positive; task thứ hai khớp cùng nhãn = **trùng**, đếm riêng. Báo cáo thêm **tỉ lệ trùng anchor chính xác** trong các cặp đã ghép.

**Ngưỡng** giả định sản phẩm sau này cho người dùng xác nhận task trước khi nhắc, nên bỏ sót tốn hơn báo thừa. **Cần PM xác nhận giả định này**; nếu nhắc tự động không qua xác nhận thì nâng ngưỡng precision.

| Chỉ số | Cách tính | Ngưỡng |
| --- | --- | --- |
| Recall | nhãn được khớp / tổng nhãn | ≥ 70% |
| Precision | task khớp / tổng task | ≥ 60% |
| Recall kích hoạt | nhãn có anchor nằm trong lõi một phiên / tổng nhãn | ≥ 95% |
| Tỉ lệ trùng | task trùng / tổng task | ≤ 5% |
| Người nhận đúng | tập `assignee_ids` trùng khớp, trên task khớp có nhãn người nhận | ≥ 85% |
| Hạn sự kiện bị bịa ngày | nhãn `event` mà LLM2 điền trường ngày (trước Validate) | ≤ 5% |
| Hạn lịch đúng | nhãn `calendar`, `due_date` trùng khớp | ≥ 80% |
| Trạng thái huỷ đúng | nhãn `cancelled` mà task khớp có `status = cancelled` | báo cáo |
| Báo nhầm trên hard negative | hard negative là anchor của một task xuất ra / tổng | báo cáo |
| `expired_open_ask` | số lời hỏi tentative hết file chưa ai nhận | báo cáo |
| Lượt gọi và token | LLM1, LLM2, quy về 1.000 tin | báo cáo |
| Phân bố `close_reason`, số phiên mỗi task | từ trace | báo cáo |

- Mọi tỉ lệ kèm cỡ mẫu và khoảng tin cậy 95% Wilson; dưới 30 mẫu thì ghi rõ "chưa đủ để kết luận".
- Cắt mọi chỉ số theo `scenario_tags`.
- **Đường cong LLM1:** với `llm1_threshold` từ 0.05 tới 0.9, vẽ (bảng là đủ) *recall kích hoạt* theo *% sự kiện bị kích hoạt*, cho `llm1` và `llm1` bỏ luật. Nhờ cache, LLM1 chỉ gọi một lần cho mọi ngưỡng; LLM2 phải chạy lại cho mỗi ngưỡng muốn đo đầu-cuối, nên chỉ đo đầu-cuối ở 2–3 ngưỡng.
- **Phần quan trọng nhất của báo cáo:** liệt kê từng false positive, false negative, task trùng và lỗi trường (người nhận, hạn, trạng thái), kèm các tin liên quan và đường đi qua pipeline (điểm LLM1 từng tin, luật nào bật, phiên nào, `close_reason`, task đang mở có trong prompt không, op LLM2 trả về, op bị loại). Mỗi false negative ghi rõ hỏng ở đâu: *không được kích hoạt* / *được kích hoạt nhưng LLM2 bỏ sót* / *LLM2 trả nhưng Validate loại* / *còn tentative khi hết file*.

**Thí nghiệm chính:** chạy cùng dữ liệu với `trigger_mode` = `all`, `llm1`, `rules_only`, và `tentative_enabled` = `true`/`false`. `compare.py` ghép các `report` thành một bảng, cột đầu là baseline `all`.

## 9. Kịch bản test

Bộ dữ liệu đánh giá phải có ít nhất 3 ca cho mỗi tag dưới đây. Các ca biên hiếm gặp ngẫu nhiên, nên phải viết chủ đích.

| Tag | Mô tả | Kết quả đúng |
| --- | --- | --- |
| `single_message` | Giao việc gọn trong một tin, có @mention và hạn | 1 task đủ trường |
| `multi_message` | Giao ở tin 1, nhận ở tin 3, chốt hạn ở tin 5 | 1 task, hạn từ tin 5 |
| `implicit_ask` | "Cái này ai làm được không" → vài tin sau "ok em" | 1 task, người nhận là người nói "ok em" |
| `late_accept` | Như `implicit_ask` nhưng "ok em" đến sau khi phiên đã đóng (> `idle_close_seconds`) | 1 task, anchor là câu hỏi |
| `unanswered_ask` | "Ai rảnh làm cái này không" và không ai nhận | 0 task, 1 `expired_open_ask` |
| `reaction_accept` | Giao cho nhóm, một người thả 👍 | 1 task, người nhận là người thả reaction |
| `self_commit` | "Mai em gửi anh bản cuối nhé", không ai giao | 1 task, người nhận là người nói |
| `hard_negative` | "Để anh xem đã", "mình nên làm hướng nào", "em gửi rồi ạ" | 0 task |
| `event_deadline` | "Xong trước buổi UAT nhé" | `due_type=event`, `due_date=null` |
| `relative_deadline` | "Cuối tuần", "mai", "thứ 5" khi hôm nay là thứ 5 hoặc thứ 6 | Ngày đúng quy ước |
| `late_deadline` | Task đã tạo không hạn; phiên đóng; 1 giờ sau "thứ 6 nhé" | UPDATE hạn, không task mới |
| `no_assignee` | "Em làm giúp anh cái này" trong nhóm nhiều người | `assignee_ids=[]` |
| `dm_addressee` | Trong DM: "em làm giúp anh cái này nhé" | Người nhận là người còn lại |
| `multi_assignee` | "Linh với Nam chuẩn bị slide" | 1 task, 2 người nhận |
| `reassign` | "À thôi để Nam làm" | UPDATE người nhận |
| `cancel` | Task đã tạo; sau đó "thôi khỏi làm cái đó nhé" | Task `cancelled`, không task mới |
| `two_tasks_one_session` | Hai việc giao liền nhau trong 6 tin | 2 task, không trộn trường |
| `cap_split` | Task 1 ở tin 1–15, task 2 ở tin 16–30, không khoảng lặng | 2 task, không có task thứ 3 |
| `long_task` | Một task bàn 50–60 tin, đổi hạn giữa chừng, có task khác chen vào | 2 task; hạn là giá trị sau cùng |
| `later_update` | Task đã tạo; nhiều tin sau "chốt thứ 6 nhé" | UPDATE task cũ, không tạo task mới |
| `informal_text` | Giao việc viết không dấu / viết tắt / xen Anh ("e review giup a cai PR truoc EOD nhe") | 1 task đủ trường |
| `name_collision` | Có người tên Mai / Năm; "chị Mai ơi", "anh Năm gửi giúp em" | Không kích hoạt nhầm luật thời gian; gán đúng người |

Unit test với `FakeLLM`, không gọi mạng:

- [ ] Không sự kiện nào thuộc lõi của hai phiên.
- [ ] `streak`, `idle`, `cap` đóng phiên đúng lúc và ghi đúng `close_reason`; `cap` cắt đúng khoảng lặng lớn nhất; `streak` không áp dụng ở `all`.
- [ ] Reaction sinh sự kiện đúng thời điểm và kích hoạt `reply_to_flagged`.
- [ ] `has_time_expr` chỉ tính khi có phiên mở hoặc task chạm tới trong `recent_task_hours`; regex qua hết câu phải khớp / không được khớp.
- [ ] Validate loại CREATE có anchor ngoài lõi, UPDATE/CANCEL thiếu bằng chứng, CREATE `open_ask` khi `tentative_enabled=false`; bỏ trường ngày khi `event`.
- [ ] `resolve_due` đúng mọi quy ước mục 6, kể cả "thứ N" nói đúng vào thứ N, qua tháng, qua năm.
- [ ] Gán người nhận: mention, `SPEAKER`, `ADDRESSEE` (DM và nhóm), tên có xưng hô, tên trùng hai người.
- [ ] Task `open_ask` ở `tentative`, chuyển `active` khi có người nhận, không xuất nếu hết file vẫn `tentative`.
- [ ] Chạy lại với cache cho kết quả giống hệt.

## 10. Cấu trúc và cách làm

Python 3.11+, `pydantic` v2, `openai` SDK (Chat Completions API), `pyyaml`, `pytest`. Không cơ sở dữ liệu, không framework agent, không model embedding.

```
proto/
  README.md  DECISIONS.md
  config.yaml
  prompts/llm1_v2.txt  prompts/llm2_v2.txt
  data/sample/  messages.jsonl  members.json  gold.jsonl  hard_negatives.txt
  detect/
    models.py       # pydantic: Message, Event, Op*, Task
    llm.py          # client, probe, cache đĩa, FakeLLM
    timeexpr.py     # chuẩn hoá bỏ dấu, regex thời gian, resolve_due
    trigger.py      # LLM1 + luật
    session.py      # phiên, watermark, cắt theo khoảng lặng
    extract.py      # dựng prompt, gọi LLM2, validate, gán người nhận
    store.py        # task store: tentative / active / cancelled, field_history
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

**Bốn bước, dừng sau mỗi bước để người dùng xem:**

1. **Dữ liệu và chấm điểm.** `models.py`, `data/sample/`, `eval.py` chạy được trên một `tasks.jsonl` viết tay. Song song: người viết bộ đánh giá bắt đầu theo hướng dẫn mục 3. Xong khi `eval` in đủ bảng mục 8 trên dữ liệu giả.
2. **Logic thuần với `FakeLLM`:** `timeexpr`, trigger, session, validate, gán người nhận, store. Xong khi unit test mục 9 xanh.
3. **Baseline thật:** `probe`, LLM2, cache; chạy `trigger_mode=all` hết `data/sample`. Xong khi có `report.md` đầu tiên — đây là mức sàn.
4. **LLM1 và thí nghiệm:** LLM1, `llm1`, `rules_only`, `tentative_enabled`, `compare`, đường cong ngưỡng. Xong khi `compare` ra bảng và báo cáo có danh sách lỗi kèm đường đi.

Khi đặc tả thiếu hoặc mâu thuẫn: hỏi lại thay vì tự đoán; nếu đã chọn một cách hiểu thì ghi vào `DECISIONS.md`. Ưu tiên code ngắn, dễ đọc hơn là tổng quát.

## 11. Câu hỏi prototype phải trả lời

- [ ] Model OpenAI đã chọn có trả `logprobs` và nhận `json_schema` strict không? (`probe` trả lời; đây là việc đầu tiên.)
- [ ] Baseline `all` đạt tới đâu? Cổng LLM1 có tăng precision và giảm chi phí so với baseline mà không làm tụt recall kích hoạt dưới 95% không? Luật đơn thuần đạt tới đâu?
- [ ] False negative chủ yếu hỏng ở đâu: kích hoạt, LLM2, Validate, hay tentative không được nhận?
- [ ] Task `tentative` có thực sự cứu được `implicit_ask` / `late_accept` mà không làm tăng báo nhầm (so `tentative_enabled` bật/tắt)?
- [ ] Danh sách task đang mở trong prompt có đủ để tránh trùng ở `cap_split`, `long_task`, `later_update`, `late_deadline` không?
- [ ] LLM2 có điền ngày cho hạn sự kiện không, trước khi Validate sửa? Hạn lịch đúng bao nhiêu khi code tính ngày?
- [ ] Model nhỏ (`nano`) có đủ cho LLM2 không, hay cần model lớn hơn? Chạy LLM2 với hai model trên cùng dữ liệu để biết.

Nếu bộ đánh giá có dưới khoảng 100 task nhãn, kết quả chỉ nên đọc như tín hiệu định hướng, chưa đủ để kết luận.

## 12. Cần xác nhận với PM / business

1. Sản phẩm có bước người dùng xác nhận task trước khi nhắc không? (Quyết định ngưỡng recall/precision ở mục 8.)
2. Tự nhận việc (`self_commit`) có nằm trong phạm vi nhắc việc không?
3. Quy ước thời gian ở mục 6, đặc biệt "cuối tuần" và "thứ N" nói đúng vào thứ N.
4. Lời hỏi không ai nhận (`expired_open_ask`) có cần hiện ở đâu đó cho người hỏi không, hay bỏ hẳn?
