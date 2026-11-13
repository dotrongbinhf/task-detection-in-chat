# AI Reminder — Prototype nhanh: phát hiện và tạo task (v3)

Nov 11, 2026 · @dotrongbinhf

## 0. Thay đổi so với v2

| # | Thay đổi | Lý do |
| --- | --- | --- |
| 1 | Bỏ **phiên** (mở/đóng, watermark, `idle`, `streak`, `cap`). Thay bằng **sổ cái task** cập nhật theo **từng tin mới** | Chat công việc là luồng liên tục, không có ranh giới tự nhiên như thread hay email. Mọi cách cắt đoạn (mention, khoảng lặng, số tin) đều sinh ra task bị cắt ngang, tạo task chậm và phải chống trùng. |
| 2 | **Bằng chứng gắn theo từng task** (danh sách tin + vai trò), không theo khoảng tin liên tiếp | Task đan xen nhau thì không cắt khoảng liên tiếp nào cho đúng. Người dùng bấm vào task thấy đúng các tin tạo nên nó, không lẫn tin nhiễu. Ranh giới là **đầu ra**, không phải thứ phải đoán trước. |
| 3 | Mỗi lượt gọi chỉ hỏi: **"tin mới này thay đổi sổ cái thế nào?"**, với ngữ cảnh có giới hạn | Chi phí mỗi tin là hằng số, không tăng theo độ dài lịch sử. Chống trùng có sẵn vì LLM tham chiếu task theo id. |
| 4 | Ngữ cảnh = K tin gần nhất (cửa sổ trượt) + chuỗi reply + task đang mở chọn theo mức liên quan + task cũ truy vấn từ DB | Thông tin ở xa đến được LLM qua sổ cái và DB, nên K nhỏ là đủ. Cửa sổ trượt chồng lên nhau nên không có tin nào bị tách khỏi tin liền trước nó. |
| 5 | Task có vòng đời riêng (`candidate` → `draft` → `approved` / `declined`…); **sổ cái không bao giờ đóng** | Hội thoại chạy mãi nhưng tập task đang mở luôn nhỏ, vì từng task tự rời đi theo luật rõ ràng. |
| 6 | LLM1 trở thành **cổng lọc tuỳ chọn có thấy sổ cái**; mặc định prototype chạy **không cổng** để đo chất lượng trần, kèm chế độ `shadow` để chấm cổng mà không chặn | Cổng chỉ là tối ưu chi phí. Câu hỏi "có cần cổng không" trở thành con số đo được. |
| 7 | Có **ngân sách token** và định dạng tin nén (id ngắn, cắt tin dài) | Môi trường chuyên nghiệp có nhiều task đang mở hơn, nên danh sách task là phần dễ phình to nhất. |
| 8 | Mô phỏng HITL (`user_actions.jsonl`: duyệt / từ chối) | Để kiểm tra task đã bị từ chối không bị tạo lại, và task đã duyệt vẫn được cập nhật. |
| 9 | Chỉ số mới: **độ trễ phát hiện**, **chất lượng trích dẫn**, **số lần nháp bị sửa**, **recall cổng so với trần**, **token theo thành phần** | Đây là những thứ quyết định trải nghiệm và chi phí của thiết kế mới. |
| 10 | Giữ từ v2: code tự tính ngày, gán người nhận (`SPEAKER` / `ADDRESSEE`), `CANCEL`, reaction, regex **không** dùng làm trigger | |
| 11 | **Nháp hiện ngay khi có việc + người nhận đã xác định**, chưa cần hạn. Chưa xác định được người nhận thì task giữ ẩn (`candidate`). Trường tối thiểu là tham số `draft_min` để tinh chỉnh sau | Đã xác nhận với PM (11/11): nháp không có người nhận thì không có ai để hiện cho. |
| 12 | **Mọi thay đổi trên task đã duyệt đều phải duyệt**: chat chỉ sinh *đề xuất thay đổi*, task giữ giá trị đã duyệt tới khi người dùng chấp nhận | Đã xác nhận với PM (11/11). |
| 13 | **Người duyệt là người nhận** (nháp lẫn đề xuất thay đổi) | Đã xác nhận với PM (11/11): người nhận quên thì việc không được làm; người giao quên thì sẽ nhớ lại khi người nhận trả kết quả. |

## 1. Mục tiêu và phạm vi

Xây một **script chạy offline** đọc luồng tin nhắn từ file theo đúng thứ tự thời gian, mô phỏng việc xử lý từng tin khi nó đến, xuất ra các task nháp kèm bằng chứng, rồi chấm so với bộ nhãn do người gán.

Câu hỏi cần trả lời: **cập nhật sổ cái theo từng tin có nhận ra task đúng, đủ, kịp thời, trích dẫn đúng tin không; tốn bao nhiêu token; và cổng lọc làm mất bao nhiêu so với chạy mọi tin.**

**Trong phạm vi "task"** (giữ từ v2):

- Việc được giao cho người cụ thể (`kind = assigned`).
- Việc tự nhận không ai giao: "mai em gửi anh bản cuối nhé" (`self_commit`).
- Việc được nêu ra nhưng chưa ai nhận: "cái này ai làm được không", "mình cần cập nhật lại tài liệu API" (`open_ask`). Giữ **ẩn** ở `candidate` tới khi có người nhận (mặc định `draft_min = assignee`).
- Huỷ task đã có: "thôi khỏi làm cái đó nhé" → `CANCEL`.

**HITL** chỉ gồm duyệt / từ chối / sửa task nháp và đề xuất thay đổi, do **người nhận** thực hiện. Không có đường chủ động kiểu "bấm nút tạo task".

**Ngoài phạm vi:** đánh dấu hoàn thành ("em gửi rồi ạ"; hệ thống chỉ cần **không tạo task mới** từ tin này), Teams, Graph, Jira, UI thật, nhắc việc, tin sửa/xoá, embedding. Không dựng interface "để sau này cắm vào" — code ngắn nhất có thể để trả lời câu hỏi trên.

LLM là model nhỏ của OpenAI, chọn qua biến môi trường (mục 7). Vì gọi API bên ngoài, bản này **chỉ chạy trên dữ liệu tự sinh**.

## 2. Ý tưởng và pipeline

Mỗi hội thoại có một **sổ cái**: tập các task hệ thống đang theo dõi, kể cả task ẩn. Mỗi tin mới (hoặc một nhóm tin đến dồn) sinh **một lượt xử lý**:

```
messages.jsonl → luồng sự kiện (tin + reaction) theo created_at
   ▼
Hàng đợi theo conversation_key, xử lý tuần tự; tin đến dồn trong coalesce_seconds → xử lý chung
   ▼
Dựng ngữ cảnh (ngân sách token):
   hướng dẫn cố định │ lịch │ task liên quan (đang mở + truy vấn DB) │ K tin gần nhất + chuỗi reply │ TIN MỚI
   ▼
[gate_mode = llm]   Cổng nano: "tin mới có thể thay đổi sổ cái không?" → p; p < ngưỡng → bỏ qua
[gate_mode = shadow] Cổng vẫn chấm và ghi p, nhưng không chặn
   ▼
Bộ trích (mini): ops = [] | CREATE | UPDATE | CANCEL, mỗi op trích tin bằng chứng kèm vai trò
   ▼
Validate: id hợp lệ, op phải dựa vào TIN MỚI, chống tạo lại, quy hạn ra ngày, gán người nhận
   ▼
Cập nhật sổ cái → sự kiện nháp (hiện / cập nhật / huỷ) → mô phỏng HITL (user_actions)
   ▼
Hết file → tasks.jsonl + trace.jsonl → eval.py → report.md
```

Thời gian là thời gian mô phỏng lấy từ `created_at`; không dùng đồng hồ hệ thống. Trước mỗi lượt xử lý, áp các luật hết hạn trong sổ cái (mục 4.2) và các `user_actions` có `at` ≤ thời điểm đó.

## 3. Dữ liệu

Các file trong `data/<dataset>/`.

**`messages.jsonl`** — giữ nguyên v2:

```json
{"id": "m041", "conversation_id": "ch_design", "conversation_type": "group", "thread_root_id": null,
 "created_at": "2026-11-09T09:12:00+07:00", "author_id": "u_hung", "author_name": "Hưng",
 "text": "Linh gửi báo cáo tuần cho anh trước thứ 5 nhé",
 "mentions": [{"user_id": "u_linh", "name": "Linh"}], "reply_to_id": null,
 "reactions": [{"user_id": "u_linh", "type": "like", "created_at": "2026-11-09T09:20:00+07:00"}]}
```

`conversation_key` = `conversation_id`, nối thêm `;thread_root_id` nếu có. Mỗi `conversation_key` có sổ cái riêng.

**`members.json`** — `{conversation_id: [{user_id, display_name, aliases}]}`.

**`gold.jsonl`** — mỗi dòng một task thật:

```json
{"gold_id": "g07", "conversation_key": "ch_design",
 "anchor_message_id": "m041",
 "supporting_message_ids": ["m041", "m044", "m047"],
 "title": "Gửi báo cáo tuần cho anh Hưng", "kind": "assigned",
 "assignee_ids": ["u_linh"], "due_type": "calendar", "due_date": "2026-11-12",
 "final_status": "active", "scenario_tags": ["multi_message", "interleaved_topics"]}
```

- `supporting_message_ids` = **đúng các tin tạo nên task** (giao, nhận, hạn, bổ sung, huỷ), không phải cả khoảng hội thoại. Đây là nhãn chuẩn để chấm trích dẫn.
- `kind` ∈ `assigned` | `self_commit` | `open_ask`. Lời hỏi được người nhận về sau thì ghi `assigned`, `anchor_message_id` = câu hỏi. Việc được nêu ra mà **không ai nhận** vẫn có nhãn, `kind = open_ask`, `assignee_ids = []`. Với `draft_min = assignee` (mặc định), eval bỏ **mọi nhãn có `assignee_ids = []`** khỏi mẫu số recall (hệ thống không được phép hiện chúng), nhưng vẫn báo cáo riêng xem hệ thống có giữ chúng ở `candidate` đúng không.
- Câu "để anh xem đã", "mình nên làm hướng nào" **không phải** việc được nêu ra; chúng vào `hard_negatives.txt`.
- `assignee_ids = []` khi không xác định được; `due_type` ∈ `calendar` | `event` | `none`; `final_status` ∈ `active` | `cancelled`.

**`hard_negatives.txt`** — mỗi dòng một `message_id` "gần giống giao việc" nhưng không phải.

**`user_actions.jsonl`** (tuỳ chọn) — mô phỏng người dùng:

```json
{"at": "2026-11-09T10:00:00+07:00", "user_id": "u_linh", "message_id": "m050", "action": "decline"}
```

Tại thời điểm `at`:

- `action` ∈ `approve` | `decline`: áp cho task **nháp** có `message_id` nằm trong bằng chứng.
- `action` ∈ `approve_change` | `decline_change`: áp cho **đề xuất thay đổi** đang chờ (mục 4.2) có `message_id` là tin bằng chứng của đề xuất.
- `user_id` phải là **người được quyền duyệt** (mục 4.2), ngược lại ghi `user_action_unauthorized` và bỏ qua.

Không có gì khớp thì ghi `user_action_unmatched` vào trace.

**Hướng dẫn viết dữ liệu đánh giá** (môi trường làm việc chuyên nghiệp):

- Phần lớn tin là **công việc nhưng không thay đổi task**: thảo luận chuyên môn, báo cáo tiến độ, hỏi đáp, gửi file. Tin chuyện phiếm chỉ chiếm phần nhỏ. Như vậy cổng lọc không được đánh giá quá đẹp.
- Hội thoại kéo dài **nhiều ngày**; một kênh bận có **≥ 10 task mở cùng lúc** để thử việc chọn task liên quan.
- Có task **đan xen** (hai, ba việc bàn xen kẽ cùng lúc), có tham chiếu **xa** (nhận việc sau vài giờ, cách hàng chục tin), có **tràng tin** gửi liên tiếp trong vài giây.
- Khoảng cách thời gian giống thật; ≥ 30% tin không dấu, viết tắt, xen tiếng Anh; có tên trùng từ thời gian (Mai, Năm…).

Claude Code tạo `data/sample/` khoảng 60 tin, 6 task để chạy thông code. **Bộ dữ liệu đánh giá do người viết**, không phải người viết prompt, mục tiêu **≥ 100 task nhãn** và ≥ 3 ca mỗi tag ở mục 9.

## 4. Thành phần

### 4.1 Luồng sự kiện và hàng đợi

- Tin và reaction được dàn thành một luồng sự kiện theo thời gian. Reaction có id `<message_id>#r<n>`, hiển thị trong prompt như một dòng `Linh 👍 (reaction vào #4)`.
- Reaction **chỉ sinh lượt xử lý** khi tin được thả reaction nằm trong K tin gần nhất hoặc là bằng chứng của một task đang mở; ngược lại chỉ ghi vào lịch sử. (Đây là luật cắt chi phí: reaction vào tin cũ không liên quan tới task nào thì không thể thay đổi sổ cái.)
- **Gộp tin đến dồn:** các sự kiện có `created_at` cách sự kiện đầu tiên chưa xử lý ≤ `coalesce_seconds` được xử lý chung trong một lượt và cùng là "TIN MỚI". Đây là mô phỏng việc tin đến trong lúc lượt trước đang chạy, **không phải ngưỡng số lượng**: không có tin dồn thì mỗi tin là một lượt.

### 4.2 Sổ cái và vòng đời task

Mỗi task trong sổ cái:

```
task_id, conversation_key, status, kind, title, notes,
assignee_refs_raw, assignee_ids, due_text, due_kind (+ các trường hạn), due_type, due_date,
anchor_message_id,
evidence: [{message_id, role, added_at}],      role ∈ assign | accept | assignee | due | content | cancel | context
field_history: [{field, old, new, evidence_message_id, at}],
created_at, last_touched_at, shown_at (lần đầu thành draft)
```

| `status` | Vào khi | Người dùng thấy? | Rời tập "đang mở" khi |
| --- | --- | --- | --- |
| `candidate` | CREATE chưa thoả `draft_min`. Mặc định: `assignee_ids` rỗng sau khi gán người nhận — gồm `open_ask` và việc giao mà không xác định được cho ai ("em làm giúp anh cái này" trong nhóm) | Không | Quá `candidate_ttl_hours` không được chạm tới → `expired` |
| `draft` | CREATE thoả `draft_min` (mặc định: có ít nhất một người nhận đã xác định, chưa cần hạn); hoặc `candidate` được UPDATE và gán được người nhận | Có — thẻ nháp **cho từng người nhận**, cập nhật tại chỗ | Người nhận duyệt/từ chối; hoặc quá `draft_ttl_days` không được chạm tới → `stale` |
| `approved` | Người dùng duyệt | Có — task thật | Quá `due_date` + `approved_grace_days`; không có hạn thì quá `undated_open_days` kể từ lần chạm cuối → `closed` |
| `declined` | Người dùng từ chối | Không | Quá `declined_suppress_days` → `closed` |
| `cancelled` | `CANCEL` trên nháp; hoặc đề xuất huỷ trên task đã duyệt được người dùng chấp nhận | Có — báo đã huỷ | Ngay lập tức → `closed` |

- **Tập đang mở** = `candidate` + `draft` + `approved` + `declined` (chưa hết hạn). `declined` nằm trong tập này chỉ để **chặn tạo lại**.
- Task `expired` / `stale` / `closed` không bị xoá; chúng nằm trong "DB" (cùng cấu trúc, trong bộ nhớ) và có thể được truy vấn lại (mục 4.3).
**Thay đổi cần duyệt.** Nguyên tắc: hệ thống không bao giờ tự sửa thứ người dùng đã duyệt.

- Task ở `draft` hoặc `candidate`: UPDATE áp **thẳng** vào nháp (nháp chưa được duyệt, người dùng sẽ duyệt trạng thái mới nhất của nó). Sinh sự kiện `draft_updated`.
- Task ở `approved`: UPDATE và CANCEL **không áp thẳng**, mà tạo **đề xuất thay đổi** (`pending_change`):

  ```
  change_id, task_id, field, old_value (giá trị đã duyệt), new_value, evidence_message_id,
  created_at, status ∈ pending | approved | declined | superseded
  ```

  `CANCEL` trên task đã duyệt là đề xuất `field = status`, `new_value = cancelled`.
- Mỗi trường của một task có **tối đa một đề xuất `pending`**. Đề xuất mới cho cùng trường thay thế đề xuất cũ (cũ → `superseded`), để người dùng không phải duyệt một hàng dài đề xuất lỗi thời ("lùi sang thứ 6" rồi "thôi thứ 7 nhé" → chỉ còn một đề xuất "thứ 7").
- `approve_change` → áp `new_value` vào task, ghi `field_history`. `decline_change` → giữ nguyên, đề xuất thành `declined`.
- Đề xuất `pending` không bao giờ tự áp hay tự hết hạn; nó mất đi chỉ khi được duyệt, bị từ chối hoặc bị thay thế.

**Ai duyệt.** Người duyệt là **người nhận**:

- Nháp hiện cho từng người trong `assignee_ids`. Với nhiều người nhận, **mỗi người duyệt phần của mình**: task lưu `approvals: {user_id: pending | approved | declined}`. Task là `approved` khi có ít nhất một người nhận duyệt; `declined` khi **mọi** người nhận đều từ chối. (Prototype không tách task theo từng người.)
- Đề xuất thay đổi hiện cho những người nhận đã duyệt task. Riêng đề xuất **đổi người nhận** ("à thôi để Nam làm") do **người nhận mới** duyệt; người nhận cũ chỉ được ghi `notified` trong trace.
- Người giao không duyệt gì trong prototype.
- Task đã duyệt có đề xuất `pending` vẫn thuộc tập đang mở (không bị `closed`) tới khi đề xuất được xử lý.

### 4.3 Dựng ngữ cảnh

Prompt có thứ tự cố định để tận dụng prompt caching: phần ít đổi trước, phần đổi nhiều sau.

| Khối | Nội dung | Giới hạn |
| --- | --- | --- |
| 1. Hướng dẫn + schema + quy ước thời gian | Cố định | — |
| 2. Lịch 14 ngày | Bắt đầu từ thứ 2 tuần hiện tại | Đổi theo ngày |
| 3. TASK LIÊN QUAN | Task đang mở được chọn + task cũ truy vấn từ DB | `open_tasks_max`, `closed_tasks_max` |
| 4. LỊCH SỬ GẦN | K tin trước TIN MỚI + chuỗi reply của TIN MỚI (nếu tin được reply nằm ngoài K) | `window_k`, `reply_chain_max` |
| 5. TIN MỚI | Sự kiện của lượt này | — |

**Định dạng tin nén:**

- Mỗi tin trong prompt có id cục bộ `#1`, `#2`… theo thứ tự xuất hiện; code giữ bảng ánh xạ `#n ↔ message_id`.
- Dòng tin: `#7 [09:12] Hưng: Linh gửi báo cáo tuần cho anh trước thứ 5 nhé (@Linh=u_linh) ↩#3`. Ghi đủ ngày-giờ (`thứ 2 09/11 09:12`) khi tin đầu tiên của một ngày mới hoặc cách tin trước > 1 giờ.
- Tin dài hơn `message_max_tokens` thì cắt, thêm `…[cắt]`. File đính kèm thành `[file: ten_file]`.

**Chọn task đang mở** — chấm điểm bằng code, lấy cao nhất tới `open_tasks_max`:

| Tín hiệu | Điểm |
| --- | --- |
| TIN MỚI reply hoặc thả reaction vào một tin bằng chứng của task | +100 |
| Tin bằng chứng của task nằm trong LỊCH SỬ GẦN | +20 |
| Người gửi TIN MỚI hoặc người được nhắc trong TIN MỚI là người giao / người nhận của task | +10 |
| Từ khoá chung giữa TIN MỚI và `title`/`notes` (bỏ dấu, bỏ stopword) | +3 mỗi từ |
| Độ mới: chạm tới trong 24 giờ | +5 |

Task `declined` chỉ đưa vào nếu điểm ≥ 20.

**Truy vấn task cũ trong DB** (`expired`/`stale`/`closed` trong `closed_lookup_days`): cùng cách chấm điểm, chỉ đưa vào nếu điểm ≥ 13 (vd. cùng người + ít nhất một từ khoá chung), tối đa `closed_tasks_max`.

**Mỗi task trong prompt** một dòng, kèm tối đa `evidence_per_task` tin bằng chứng gần nhất (nếu tin đó chưa có trong LỊCH SỬ GẦN):

```
T2 [draft | assigned] "Gửi báo cáo tuần cho anh Hưng" — người nhận: Linh — hạn: thứ 5 (12/11) — bằng chứng: #2 assign, #5 accept
T4 [approved | assigned] "Cập nhật tài liệu API" — người nhận: Nam — hạn: thứ 6 (13/11), ĐANG CHỜ DUYỆT: thứ 2 (16/11) — bằng chứng: #3 assign
```

Task đã duyệt hiển thị **giá trị đã duyệt** kèm **đề xuất đang chờ** (nếu có), để LLM không đề xuất lại cùng một thay đổi.

`T<n>` là id cục bộ; code ánh xạ về `task_id`.

**Ngân sách token:** nếu tổng vượt `context_token_budget`, bỏ dần theo thứ tự: task cũ từ DB → tin bằng chứng kèm task → task đang mở có điểm thấp nhất (giữ tối thiểu 3) → giảm K (tối thiểu 5). Mỗi lần cắt ghi `budget_trim` vào trace. Token đếm bằng `tiktoken` theo model đang dùng.

### 4.4 Cổng (tuỳ chọn)

- `gate_mode`:
  - `none` (mặc định) — mọi lượt đều gọi bộ trích. Đây là **trần chất lượng**.
  - `shadow` — gọi cổng ở mọi lượt, ghi `p`, **không chặn**. Dùng để vẽ đường cong ngưỡng mà không phải chạy lại bộ trích.
  - `llm` — `p < gate_threshold` thì bỏ qua lượt, ghi `gate_skip`.
- Ngữ cảnh của cổng là bản rút gọn: khối 1 riêng của cổng + tiêu đề các task đang mở được chọn (không kèm bằng chứng) + `gate_window_k` tin trước + TIN MỚI.
- `p` = xác suất chuẩn hoá của `yes` so với `no` từ `top_logprobs` (mục 7).
- Câu hỏi của cổng **không phải** "tin có liên quan công việc không" (trong môi trường chuyên nghiệp gần như luôn là có), mà là "tin có giao / nhận / tự hứa / nêu việc cần người làm / đổi hạn-người-nội dung / huỷ một việc không".

### 4.5 Bộ trích

Op:

- **`ops` rỗng** — tin mới không thay đổi sổ cái. Đây là kết quả của phần lớn lượt.
- **`CREATE`** — task mới. `anchor` là tin khởi đầu việc; có thể là TIN MỚI hoặc một tin trong LỊCH SỬ GẦN (vd. "mình cần cập nhật tài liệu" chỉ thành việc khi TIN MỚI là "để em làm"). Kèm `evidence` với vai trò.
- **`UPDATE`** — sửa task trong khối TASK LIÊN QUAN; mỗi `change` có `evidence_msg`. Có thể kèm `evidence` bổ sung không đổi trường (vai trò `content` / `context`).
- **`CANCEL`** — huỷ task trong khối TASK LIÊN QUAN, kèm tin bằng chứng.

Một lượt có thể trả nhiều op (vd. "Linh làm A, Nam làm B" → hai CREATE; "ok em, mà hạn lùi sang thứ 6 nhé" → UPDATE hai trường).

### 4.6 Validate

**Loại op** (ghi lý do vào trace) khi:

1. Id tin (`#n`) hoặc id task (`T<n>`) không có trong prompt.
2. **Op không trích ít nhất một tin trong TIN MỚI.** Đây là luật chống trùng chính: mỗi op phải do tin mới gây ra, nên LLM không thể trích lại việc cũ đã xử lý từ lịch sử.
3. CREATE có `anchor` đã là **anchor** của một task được tạo ở **lượt trước** (trong sổ cái hoặc DB, kể cả `declined`) → `duplicate_anchor`. Nhiều CREATE cùng anchor **trong cùng một lượt** vẫn hợp lệ ("Linh làm A, Nam làm B" → hai task cùng anchor).
4. UPDATE/CANCEL nhắm vào task không nằm trong khối TASK LIÊN QUAN, hoặc `change` thiếu `evidence_msg`.
5. `change` có giá trị trùng giá trị hiện tại của task, hoặc trùng đề xuất đang `pending` của cùng trường → `no_op_change`.
6. `change` trên task đã duyệt có giá trị trùng một đề xuất đã bị `declined` cho cùng trường trong `declined_suppress_days` → `declined_change_suppressed`.

**Áp op hợp lệ:** theo trạng thái task — áp thẳng vào `draft`/`candidate`, tạo `pending_change` cho `approved` (mục 4.2).

**Không loại mà sửa:**

- Quy hạn ra ngày bằng `resolve_due` như v2: `due_kind = event` mà LLM điền trường ngày → bỏ đi, ghi `fabricated_date_blocked`; không quy được → `due_date = null`, ghi `unresolvable_due`.
- Gán người nhận như v2. Phần tử trong `assignee_refs` có thể là:
  - `user_id` có trong `mentions` của tin bằng chứng → dùng luôn;
  - `SPEAKER:#n` → `author_id` của tin `#n` (tự nhận, "ok em", reaction đồng ý);
  - `ADDRESSEE` → chỉ hợp lệ trong DM: người còn lại;
  - tên → bỏ dấu, bỏ xưng hô, khớp `members.json`; khớp đúng một người mới gán.

**JSON hỏng** → retry `llm_max_retries` lần rồi bỏ lượt, ghi `parse_failure`. Lượt bị bỏ **không** làm mất tin: tin vẫn nằm trong LỊCH SỬ GẦN của lượt sau.

### 4.7 Đầu ra

`runs/<id>/tasks.jsonl` — trạng thái cuối của **mọi task từng được tạo**, kể cả `candidate`/`expired`, gồm đủ các trường mục 4.2 + `prompt_version`, `model`.

`runs/<id>/trace.jsonl`:

- mỗi lượt: id TIN MỚI, task được chọn đưa vào prompt (kèm điểm), task từ DB, `budget_trim`, `p` của cổng, `gate_skip`, prompt, đầu ra thô, token **theo từng khối** (1–5) và token ra, độ trễ, cache hit;
- mỗi op được áp / bị loại (kèm lý do);
- mỗi sự kiện nháp: `draft_shown`, `draft_updated`, `draft_cancelled`, kèm thời gian mô phỏng;
- mỗi đề xuất thay đổi: `change_proposed`, `change_superseded`, `change_approved`, `change_declined`;
- mỗi `user_action`, mỗi lần task chuyển trạng thái do hết hạn.

## 5. Prompt và JSON schema

Prompt lưu trong `prompts/` (`gate_v3.txt`, `extract_v3.txt`); version ghi vào mọi bản ghi. Claude Code giữ nguyên ý, được chỉnh câu chữ.

### 5.1 Cổng

```text
Bạn đọc chat công việc tiếng Việt (có thể không dấu, viết tắt, xen tiếng Anh).
Câu hỏi: TIN MỚI có làm thay đổi danh sách việc cần làm không? Tức là TIN MỚI:
giao việc; nhận việc ("ok em", "dc a", reaction đồng ý); tự hứa sẽ làm;
nêu một việc cần người làm hoặc hỏi ai làm được; đổi hạn, người làm hay nội dung của một việc;
hoặc huỷ một việc.
Trả lời "no" nếu TIN MỚI chỉ là thảo luận chuyên môn, báo cáo tiến độ, hỏi đáp, gửi file,
báo đã làm xong, hoặc chuyện ngoài lề.
Chỉ trả lời "yes" hoặc "no".

<VIEC_DANG_THEO_DOI>{task_titles}</VIEC_DANG_THEO_DOI>
<LICH_SU_GAN>{recent}</LICH_SU_GAN>
<TIN_MOI>{new}</TIN_MOI>
```

### 5.2 Bộ trích

```text
Bạn cập nhật danh sách việc cần làm từ chat công việc tiếng Việt (có thể không dấu, viết tắt, xen tiếng Anh).
Loại hội thoại: {conversation_type}. Múi giờ Asia/Ho_Chi_Minh.
Quy ước thời gian: {time_conventions}

NHIỆM VỤ: chỉ xét TIN MỚI. TIN MỚI thay đổi danh sách việc thế nào? Phần lớn trường hợp là không đổi gì → ops rỗng.

QUY TẮC
1. Mọi op phải dựa vào ít nhất một tin trong TIN MỚI. Không tạo hay sửa việc chỉ dựa vào LỊCH SỬ GẦN.
2. CREATE khi TIN MỚI khởi đầu một việc mới, hoặc làm cho một tin trong LỊCH SỬ GẦN trở thành việc
   (vd. "để em làm" nhận một việc vừa được nêu). anchor = tin khởi đầu việc.
   Không CREATE nếu việc đó đã có trong TASK LIÊN QUAN — khi đó dùng UPDATE.
3. UPDATE khi TIN MỚI nhận việc, đổi/bổ sung hạn, người làm, nội dung cho một việc trong TASK LIÊN QUAN.
   Mỗi thay đổi ghi tin bằng chứng.
4. CANCEL khi TIN MỚI huỷ một việc trong TASK LIÊN QUAN ("thôi khỏi", "không cần nữa").
5. Việc bị đánh dấu declined là người dùng đã từ chối: không tạo lại.
   Với việc approved, chỉ đề xuất thay đổi khi TIN MỚI thật sự đổi giá trị; không đề xuất lại
   giá trị đã có hoặc giá trị đang chờ duyệt. Hệ thống sẽ tự chuyển đề xuất tới người dùng duyệt.
6. kind:
   - assigned: có người được giao hoặc đã nhận.
   - self_commit: người nói tự hứa làm, không ai giao.
   - open_ask: việc cụ thể được nêu ra hoặc hỏi ai làm, nhưng chưa ai nhận. Vẫn CREATE.
     Câu lửng lơ ("để anh xem đã", "mình nên làm hướng nào") không phải việc được nêu ra.
7. evidence: liệt kê đúng các tin tạo nên việc, kèm vai trò:
   assign (giao/nêu việc), accept (nhận việc), assignee (chỉ định người), due (hạn), content (bổ sung nội dung), cancel (huỷ).
   Không liệt kê tin không liên quan.
8. Người nhận (assignee_refs), có thể nhiều người:
   - người được @mention: ghi user_id sau dấu "=";
   - người gửi tin #n tự nhận việc hoặc thả reaction đồng ý: ghi "SPEAKER:#n";
   - trong DM, giao cho người đối diện bằng đại từ: ghi "ADDRESSEE";
   - còn lại: ghi đúng tên như trong tin. Không chắc thì để rỗng. Không đoán.
9. Hạn: KHÔNG tự tính ngày. Điền due_kind và các trường tương ứng:
   - "mai", "hôm nay", "ngày kia" → relative_day, due_day_offset = 1 / 0 / 2.
   - "thứ 5", "T6 tuần sau" → weekday, due_weekday (thứ 2 = 1 … chủ nhật = 7), due_week_offset (0 tuần này, 1 tuần sau, không rõ thì null).
   - "cuối tuần", "đầu tuần sau" → week_part, due_week_part = end / start, due_week_offset.
   - "12/11", "ngày 15" → absolute, due_abs_day, due_abs_month (không nói tháng thì null).
   - "trước buổi UAT", "xong trước release" → event. KHÔNG điền trường ngày nào.
   - Không nhắc hạn, hoặc "sớm nhất có thể" → none.
   Luôn ghi due_text đúng như trong tin.
10. Thảo luận, hỏi ý kiến, báo tiến độ, "để anh xem đã", báo đã làm xong → không phải việc mới.

<LICH>{calendar_14_days}</LICH>
<TASK_LIEN_QUAN>{tasks}</TASK_LIEN_QUAN>
<LICH_SU_GAN>{recent}</LICH_SU_GAN>
<TIN_MOI>{new}</TIN_MOI>
```

### 5.3 Schema đầu ra của bộ trích

```json
{
  "ops": [
    {
      "op": "CREATE",
      "target": null,
      "kind": "assigned",
      "anchor": "#7",
      "title": "Gửi báo cáo tuần cho anh Hưng",
      "notes": null,
      "assignee_refs": ["u_linh"],
      "due_text": "trước thứ 5", "due_kind": "weekday",
      "due_day_offset": null, "due_weekday": 4, "due_week_offset": 0,
      "due_week_part": null, "due_abs_day": null, "due_abs_month": null,
      "evidence": [{"msg": "#7", "role": "assign"}, {"msg": "#7", "role": "due"}],
      "changes": null
    },
    {
      "op": "UPDATE",
      "target": "T2",
      "kind": null, "anchor": null, "title": null, "notes": null, "assignee_refs": null,
      "due_text": null, "due_kind": null, "due_day_offset": null, "due_weekday": null, "due_week_offset": null,
      "due_week_part": null, "due_abs_day": null, "due_abs_month": null,
      "evidence": [{"msg": "#12", "role": "accept"}],
      "changes": [
        {"field": "assignee_refs", "value": "[\"SPEAKER:#12\"]", "evidence_msg": "#12"},
        {"field": "kind", "value": "\"assigned\"", "evidence_msg": "#12"}
      ]
    },
    {
      "op": "CANCEL", "target": "T3",
      "evidence": [{"msg": "#13", "role": "cancel"}]
    }
  ]
}
```

- Viết bằng pydantic, xuất JSON schema strict. Schema thật là **bản phẳng**: mọi op có đủ mọi trường, trường không dùng = `null` (ví dụ `CANCEL` ở trên viết gọn cho dễ đọc).
- `op` ∈ `CREATE` | `UPDATE` | `CANCEL`; `kind` ∈ `assigned` | `self_commit` | `open_ask`; `role` ∈ `assign` | `accept` | `assignee` | `due` | `content` | `cancel` | `context`.
- `due_kind` ∈ `none` | `event` | `relative_day` | `weekday` | `week_part` | `absolute`; `due_week_part` ∈ `end` | `start` | `null`.
- `changes[].field` ∈ `title` | `notes` | `kind` | `assignee_refs` | `due`. `value` luôn là **chuỗi JSON** để strict mode chỉ cần một kiểu; với `due`, `value` là object gồm `due_text`, `due_kind` và các trường hạn. Validate parse lại.
- Khi UPDATE trên task `candidate` làm `assignee_ids` (sau khi gán người nhận) không còn rỗng → task chuyển `draft`, hiện cho người nhận.
- Với task `approved`, mỗi phần tử `changes` (và mỗi `CANCEL`) thành một `pending_change` riêng.
- Strict mode không kiểm tra "trường nào bắt buộc với op nào"; Validate làm việc đó.

## 6. Tham số

Một file `config.yaml`, ghi đè bằng `--set key=value`. Giá trị là điểm khởi đầu, chưa đo.

| Khoá | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `draft_min` | `assignee` | `assignee`: nháp hiện khi có ít nhất một người nhận đã xác định \| `assignee_due`: cần thêm hạn |
| `gate_mode` | `none` | `none` \| `shadow` \| `llm` |
| `gate_threshold` | 0.20 | Ngưỡng `p` khi `gate_mode = llm` |
| `gate_window_k` | 5 | Số tin trước trong ngữ cảnh của cổng |
| `window_k` | 10 | Số tin trước trong ngữ cảnh của bộ trích |
| `reply_chain_max` | 3 | Số tin tối đa lần ngược theo chuỗi reply |
| `open_tasks_max` | 5 | Task đang mở tối đa đưa vào prompt |
| `evidence_per_task` | 1 | Tin bằng chứng gần nhất kèm mỗi task |
| `closed_lookup_days` | 30 | Phạm vi truy vấn task cũ trong DB |
| `closed_tasks_max` | 2 | Task cũ tối đa đưa vào prompt |
| `context_token_budget` | 3000 | Trần token input của bộ trích |
| `message_max_tokens` | 80 | Cắt tin dài |
| `coalesce_seconds` | 5 | Gộp tin đến dồn (mô phỏng độ trễ xử lý) |
| `candidate_ttl_hours` | 48 | Task ẩn chưa ai nhận thì hết hạn |
| `draft_ttl_days` | 7 | Nháp không được chạm tới thì thành `stale` |
| `approved_grace_days` | 3 | Task đã duyệt còn mở sau hạn |
| `undated_open_days` | 14 | Task đã duyệt không có hạn còn mở kể từ lần chạm cuối |
| `declined_suppress_days` | 7 | Task / đề xuất thay đổi bị từ chối còn dùng để chặn tạo lại, đề xuất lại |
| `llm_max_retries` | 2 | Retry khi lỗi parse hoặc mạng |

**Quy ước thời gian** — giữ nguyên v2, **cần business xác nhận**:

- "cuối tuần" = thứ 6 tuần này; "cuối tuần sau" = thứ 6 tuần sau; "đầu tuần sau" = thứ 2 tuần sau.
- "thứ N" không nói tuần: sau hôm nay → tuần này; đã qua **hoặc là hôm nay** → tuần sau.
- "ngày D" không nói tháng: tháng này nếu D chưa qua, ngược lại tháng sau.
- "sớm nhất có thể", "ASAP" = `due_type: none`. Giờ trong ngày chỉ giữ trong `due_text`.
- Ngày tham chiếu = ngày của tin chứa hạn (không phải ngày của lượt xử lý). Múi giờ `Asia/Ho_Chi_Minh`.

## 7. LLM: OpenAI cho bản thử nghiệm

Gọi thẳng OpenAI **Chat Completions API**; không dùng Responses API vì cổng cần `logprobs`.

- Biến môi trường: `OPENAI_API_KEY`, `GATE_MODEL`, `EXTRACT_MODEL`, `OPENAI_BASE_URL` (tuỳ chọn).
- Mặc định gợi ý: cổng = `gpt-4.1-nano`, bộ trích = `gpt-4.1-mini` (model không suy luận, nhận `temperature`, trả `logprobs`). Model có thể đã ngừng hỗ trợ: **chạy `probe` trước tiên**, đổi model thì ghi vào `DECISIONS.md`.
- **`probe`** in ra từng model có trả `logprobs`/`top_logprobs`, nhận `json_schema` strict, nhận `temperature` không; ghi `runs/<id>/capabilities.json`. Thiếu khả năng cần thiết thì `run` dừng.
- **Cổng:** `max_tokens=1`, `temperature=0`, `logprobs=true`, `top_logprobs=10`; `p = P(yes) / (P(yes) + P(no))`, cộng dồn biến thể token; không có cả hai trong top → `p = 0.5`, ghi cảnh báo.
- **Bộ trích:** `response_format` dạng `json_schema` strict, parse lại bằng pydantic; `refusal` → ghi trace, bỏ lượt.
- **Prompt caching:** khối 1 (hướng dẫn) đứng đầu và giống hệt nhau giữa mọi lượt; trace ghi `cached_tokens` từ `usage` để đo tỉ lệ cache thật.
- `temperature=0`, `seed` cố định; **cache đĩa** (khoá = hash model + prompt + tham số) để chạy lại cho cùng kết quả; `--no-cache` để bỏ qua.
- Retry có backoff với 429/5xx. Báo cáo in token và chi phí ước tính theo bảng giá trong `config.yaml` (giá input, giá input được cache, giá output cho từng model).
- `FakeLLM` cho unit test: trả kết quả định sẵn theo id TIN MỚI.

**Hai lưu ý phải ghi trong README:**

1. **Chỉ gửi dữ liệu tự sinh lên API bên ngoài.** Không dùng chat thật của tổ chức.
2. **Số liệu không chuyển được sang model khác.** Khi chuyển sang model nội bộ phải chạy lại toàn bộ đánh giá.

## 8. Đánh giá

`eval.py` đọc `tasks.jsonl`, `trace.jsonl`, `gold.jsonl`, `hard_negatives.txt` và in `report.md`.

**Task dự đoán** = mọi task từng ở trạng thái `draft` (kể cả sau đó bị `declined` hay `stale`). `candidate` không bao giờ hiện thì không tính.

**Giá trị dùng để chấm trường** = giá trị hệ thống *đề xuất* sau cùng: với task đã duyệt là giá trị đã duyệt cộng đề xuất `pending` mới nhất (nếu có). Mục đích là đo hệ thống hiểu chat đúng không, tách khỏi việc người dùng mô phỏng có duyệt hay không.

**Ghép task với nhãn:** cùng `conversation_key`, và `anchor` của task nằm trong `supporting_message_ids` của nhãn **hoặc** `anchor_message_id` của nhãn nằm trong bằng chứng của task. Ghép tham lam, ưu tiên trùng anchor, sau đó theo số tin bằng chứng giao nhau; mỗi nhãn một task. Không ghép được = false positive; task thứ hai khớp cùng nhãn = **trùng**.

**Ngưỡng** giả định sản phẩm có bước người dùng duyệt nháp, nên bỏ sót tốn hơn báo thừa. **Cần PM xác nhận.**

| Nhóm | Chỉ số | Cách tính | Ngưỡng |
| --- | --- | --- | --- |
| Phát hiện | Recall | nhãn được khớp / tổng nhãn | ≥ 70% |
| | Precision | task khớp / tổng task dự đoán | ≥ 60% |
| | Tỉ lệ trùng | task trùng / tổng task dự đoán | ≤ 5% |
| | Báo nhầm hard negative | hard negative là anchor của task dự đoán / tổng | báo cáo |
| | Tạo lại task đã từ chối | trên các ca `declined_not_recreated`: số task mới tạo sau khi bị từ chối mà khớp cùng nhãn / hard negative với task `declined` | = 0 |
| Trường | Người nhận đúng | tập `assignee_ids` trùng khớp, trên task khớp | ≥ 85% |
| | Hạn lịch đúng | nhãn `calendar`, `due_date` trùng | ≥ 80% |
| | Hạn sự kiện bị bịa ngày | nhãn `event` mà LLM điền trường ngày (trước Validate) | ≤ 5% |
| | Huỷ đúng | nhãn `cancelled` mà task khớp ở `cancelled` | báo cáo |
| Trích dẫn | Precision bằng chứng | \|bằng chứng ∩ supporting\| / \|bằng chứng\|, trung bình trên task khớp | ≥ 80% |
| | Recall bằng chứng | \|bằng chứng ∩ supporting\| / \|supporting\| | ≥ 70% |
| Kịp thời | Độ trễ phát hiện | từ tin anchor của nhãn tới `draft_shown` của task khớp: số tin và giây; p50, p90 | báo cáo |
| | Độ trễ đủ trường | từ tin cuối trong `supporting` tới lúc người nhận + hạn của task đều đúng | báo cáo |
| | Số lần nháp bị sửa | số `draft_updated` mỗi task khớp; p50, p90 | báo cáo |
| Gánh nặng duyệt | Đề xuất thay đổi / task đã duyệt | số `change_proposed`; tách số bị `superseded` | báo cáo |
| | Đề xuất thay đổi sai | đề xuất có `new_value` không khớp nhãn, hoặc nhắm vào task không khớp nhãn / tổng đề xuất | ≤ 20% |
| | Đề xuất lặp | đề xuất bị Validate loại vì `no_op_change` hoặc `declined_change_suppressed` | báo cáo |
| Chi phí | Token / lượt | theo từng khối 1–5, input được cache, output; p50, p95 | báo cáo |
| | Lượt gọi và chi phí / 1.000 tin | cổng, bộ trích | báo cáo |
| | Tỉ lệ lượt có op | lượt có ops không rỗng / tổng lượt (ở `gate_mode = none`) | báo cáo |
| | `budget_trim` | số lượt phải cắt ngữ cảnh, theo loại cắt | báo cáo |
| Cổng | Recall cổng so với trần | từ lần chạy `shadow`: lượt có op được áp mà `p ≥ ngưỡng` / tổng lượt có op được áp, với ngưỡng từ 0.05 tới 0.9 | bảng |
| | Tỉ lệ lượt qua cổng | lượt có `p ≥ ngưỡng` / tổng lượt, cùng các ngưỡng | bảng |

- Mọi tỉ lệ kèm cỡ mẫu và khoảng tin cậy 95% Wilson; dưới 30 mẫu ghi "chưa đủ để kết luận". Cắt mọi chỉ số theo `scenario_tags`.
- **Phần quan trọng nhất của báo cáo:** từng false positive, false negative, task trùng và lỗi trường/bằng chứng, kèm đường đi: các lượt đã chạm tới task (TIN MỚI, task được chọn vào prompt kèm điểm, `budget_trim`, `p` cổng, op trả về, op bị loại và lý do). Mỗi false negative phân loại:
  1. *task liên quan không được chọn vào prompt* (lỗi chọn task / ngân sách);
  2. *tin cần thiết nằm ngoài ngữ cảnh* (ngoài K, không có trong reply, không là bằng chứng);
  3. *bộ trích bỏ sót*;
  4. *Validate loại*;
  5. *cổng chặn* (chỉ ở `gate_mode = llm`);
  6. *phát hiện nhưng chưa hiện*: task khớp nhãn nhưng vẫn ở `candidate` vì không gán được người nhận (lỗi gán người nhận, không phải lỗi phát hiện).

Báo cáo riêng: trong các nhãn `assignee_ids = []`, bao nhiêu được giữ đúng ở `candidate` và bao nhiêu bị hiện nhầm thành nháp (người nhận bị gán bừa).

**Thí nghiệm:** chạy cùng dữ liệu với

- `gate_mode = shadow` (trần + đường cong cổng, một lần chạy);
- `gate_mode = llm` ở 1–2 ngưỡng chọn từ đường cong (đo đầu-cuối, vì sổ cái thay đổi khi lượt bị bỏ);
- `window_k` ∈ {5, 10, 15}; `open_tasks_max` ∈ {3, 5, 10};
- `draft_min` ∈ {`assignee`, `assignee_due`};
- bộ trích `gpt-4.1-mini` và `gpt-4.1-nano`.

`compare.py` ghép các `report` thành một bảng, cột đầu là `shadow`.

## 9. Kịch bản test

Bộ dữ liệu đánh giá có ít nhất 3 ca cho mỗi tag.

| Tag | Mô tả | Kết quả đúng |
| --- | --- | --- |
| `single_message` | Giao việc gọn trong một tin, có @mention và hạn | 1 task đủ trường, bằng chứng 1 tin |
| `multi_message` | Giao ở tin 1, nhận ở tin 3, chốt hạn ở tin 5 | 1 task; nháp hiện từ tin 1, được cập nhật ở tin 3, 5 |
| `implicit_ask` | "Cái này ai làm được không" → vài tin sau "ok em" | `candidate` ở câu hỏi; nháp hiện cho người nói "ok em" ngay khi họ nhận |
| `late_anchor` | "Mình cần cập nhật lại tài liệu API" (không hỏi ai) → vài tin sau "để em làm" | 1 task, anchor là câu nêu việc |
| `far_reference` | Nhận việc hoặc chốt hạn sau vài giờ, cách > 30 tin | UPDATE đúng task, không task mới |
| `unanswered_ask` | Nêu việc cụ thể nhưng không ai nhận | 0 nháp; 1 `candidate` rồi `expired` |
| `reaction_accept` | Giao cho nhóm, một người thả 👍 | Người nhận là người thả reaction |
| `self_commit` | "Mai em gửi anh bản cuối nhé", không ai giao | 1 task, người nhận là người nói |
| `hard_negative` | "Để anh xem đã", "mình nên làm hướng nào", "em gửi rồi ạ", báo cáo tiến độ | 0 task |
| `interleaved_topics` | 2–3 việc bàn xen kẽ cùng lúc, cùng người tham gia | Mỗi việc 1 task, bằng chứng không lẫn |
| `multi_task_message` | "Linh làm A, Nam làm B" | 2 task, cùng một tin là bằng chứng của cả hai |
| `burst` | 4–6 tin gửi liên tiếp trong vài giây, chứa một lần giao việc | 1 task, không trùng |
| `event_deadline` | "Xong trước buổi UAT nhé" | `due_type=event`, `due_date=null` |
| `relative_deadline` | "Cuối tuần", "mai", "thứ 5" khi hôm nay là thứ 5 hoặc thứ 6 | Ngày đúng quy ước |
| `late_deadline` | Task đã tạo không hạn; 1 giờ sau "thứ 6 nhé" | UPDATE hạn |
| `no_assignee` | "Em làm giúp anh cái này" trong nhóm nhiều người | `assignee_ids=[]`, giữ `candidate`, **không** hiện nháp; nếu sau đó có người "dạ em làm" thì hiện cho người đó |
| `dm_addressee` | Trong DM: "em làm giúp anh cái này nhé" | Người nhận là người còn lại |
| `multi_assignee` | "Linh với Nam chuẩn bị slide" | 1 task, 2 người nhận, nháp hiện cho cả hai, mỗi người duyệt phần mình |
| `reassign` | "À thôi để Nam làm" | Trên nháp: đổi người nhận, nháp chuyển sang Nam. Trên task đã duyệt: đề xuất đổi người nhận do **Nam** duyệt |
| `unauthorized_action` | Người giao (không phải người nhận) bấm duyệt nháp | Bị bỏ qua, ghi `user_action_unauthorized` |
| `cancel` | Task đã tạo; sau đó "thôi khỏi làm cái đó nhé" | Task `cancelled` |
| `later_update_approved` | Task đã duyệt; hôm sau "lùi sang thứ 2 nhé" | 1 đề xuất thay đổi hạn; task giữ hạn cũ tới khi đề xuất được duyệt |
| `cancel_approved` | Task đã duyệt; sau đó "thôi khỏi làm nhé" | 1 đề xuất huỷ; task chưa bị huỷ tới khi được duyệt |
| `change_supersede` | Task đã duyệt; "lùi sang thứ 6" rồi "thôi thứ 7 nhé" trước khi người dùng duyệt | Chỉ còn 1 đề xuất `pending` là thứ 7, đề xuất thứ 6 `superseded` |
| `declined_change_not_repeated` | Đề xuất đổi hạn bị từ chối; vài tin sau có người nhắc lại hạn đó | Không đề xuất lại |
| `declined_not_recreated` | Nháp bị người dùng từ chối; vài tin sau có người nhắc lại việc đó | Không tạo task mới |
| `long_running` | Một việc bàn qua 2–3 ngày, đổi hạn giữa chừng, nhiều việc khác chen vào | 1 task; hạn là giá trị sau cùng |
| `busy_channel` | Kênh có ≥ 10 task mở cùng lúc; tin mới cập nhật một task cũ | Task đúng được chọn vào prompt và được UPDATE |
| `informal_text` | "e review giup a cai PR truoc EOD nhe" | 1 task đủ trường |
| `name_collision` | Có người tên Mai / Năm | Gán đúng người, không hiểu nhầm thành thời gian |

**Unit test với `FakeLLM`** (không gọi mạng):

- [ ] Hàng đợi: sự kiện xử lý đúng thứ tự; gộp tin trong `coalesce_seconds`; reaction chỉ sinh lượt khi đúng điều kiện.
- [ ] Dựng ngữ cảnh: chọn task đúng theo điểm; truy vấn DB đúng ngưỡng; chuỗi reply được kéo vào khi nằm ngoài K; ánh xạ `#n`/`T<n>` hai chiều; cắt theo ngân sách đúng thứ tự và ghi `budget_trim`.
- [ ] Validate: loại op không trích TIN MỚI; loại `duplicate_anchor` (kể cả với task `declined`); loại id không có trong prompt; UPDATE thiếu bằng chứng.
- [ ] Vòng đời: task chưa gán được người nhận giữ `candidate`, chuyển `draft` ngay khi gán được; hết hạn đúng từng luật; `user_actions` áp đúng task.
- [ ] Quyền duyệt: chỉ người nhận duyệt được nháp; nhiều người nhận duyệt riêng (`approvals`), `approved` khi ≥ 1 người duyệt, `declined` khi tất cả từ chối; đề xuất đổi người nhận do người nhận mới duyệt; hành động của người khác bị `user_action_unauthorized`.
- [ ] Đề xuất thay đổi: UPDATE/CANCEL trên task `approved` tạo `pending_change`, không sửa task; mỗi trường tối đa một `pending`, đề xuất mới thay thế cũ; `approve_change` áp giá trị, `decline_change` giữ nguyên; `no_op_change` và `declined_change_suppressed` bị loại; task có đề xuất `pending` không bị `closed`.
- [ ] `resolve_due` đúng mọi quy ước, kể cả qua tháng, qua năm, "thứ N" nói đúng vào thứ N.
- [ ] Gán người nhận: mention, `SPEAKER:#n`, `ADDRESSEE` (DM và nhóm), tên có xưng hô, tên trùng hai người.
- [ ] Chạy lại với cache cho kết quả giống hệt.

## 10. Cấu trúc và cách làm

Python 3.11+, `pydantic` v2, `openai` SDK, `tiktoken`, `pyyaml`, `pytest`. Không cơ sở dữ liệu (sổ cái và "DB" là cấu trúc trong bộ nhớ), không framework agent, không embedding.

```
proto/
  README.md  DECISIONS.md
  config.yaml
  prompts/gate_v3.txt  prompts/extract_v3.txt
  data/sample/  messages.jsonl  members.json  gold.jsonl  hard_negatives.txt  user_actions.jsonl
  detect/
    models.py       # pydantic: Message, Event, Op*, Task, Evidence
    llm.py          # client, probe, cache đĩa, FakeLLM
    timeexpr.py     # resolve_due, bảng lịch
    ledger.py       # sổ cái, vòng đời, hết hạn, user_actions, truy vấn task cũ
    context.py      # chọn task, cửa sổ, chuỗi reply, định dạng nén, ngân sách token
    gate.py         # cổng nano
    extract.py      # gọi bộ trích, validate, gán người nhận
    run.py          # luồng sự kiện → tasks.jsonl + trace.jsonl
    eval.py  compare.py
  tests/
  runs/
```

```
python -m detect.llm probe
python -m detect.run  --data data/sample [--set gate_mode=shadow]
python -m detect.eval --run runs/<id> --data data/sample
python -m detect.compare runs/*
```

**Bốn bước, dừng sau mỗi bước để người dùng xem:**

1. **Dữ liệu và chấm điểm.** `models.py`, `data/sample/`, `eval.py` chạy được trên `tasks.jsonl` + `trace.jsonl` viết tay. Song song: người viết bộ đánh giá theo hướng dẫn mục 3. Xong khi `eval` in đủ bảng mục 8 trên dữ liệu giả.
2. **Logic thuần với `FakeLLM`:** `timeexpr`, `ledger`, `context`, validate, gán người nhận. Xong khi unit test mục 9 xanh.
3. **Trần chất lượng:** `probe`, cổng, bộ trích, cache; chạy `gate_mode=shadow` hết `data/sample`. Xong khi có `report.md` đầu tiên kèm token theo khối và đường cong cổng.
4. **Thí nghiệm:** `gate_mode=llm`, `window_k`, `open_tasks_max`, mini/nano, `compare`. Xong khi `compare` ra bảng và báo cáo có danh sách lỗi kèm đường đi.

Khi đặc tả thiếu hoặc mâu thuẫn: hỏi lại thay vì tự đoán; nếu đã chọn một cách hiểu thì ghi vào `DECISIONS.md`. Ưu tiên code ngắn, dễ đọc hơn là tổng quát.

## 11. Câu hỏi prototype phải trả lời

- [ ] Model đã chọn có trả `logprobs` và nhận `json_schema` strict không? (`probe` — việc đầu tiên.)
- [ ] Ở trần chất lượng (`shadow`), recall / precision / trùng / trích dẫn đạt tới đâu?
- [ ] Task xuất hiện nhanh tới mức nào sau tin giao việc, và nháp bị sửa bao nhiêu lần trước khi ổn định?
- [ ] False negative chủ yếu do đâu: chọn task, ngữ cảnh thiếu, bộ trích, Validate, hay cổng?
- [ ] Input thật mỗi lượt bao nhiêu token, khối nào lớn nhất, tỉ lệ cache bao nhiêu? K và `open_tasks_max` nhỏ tới mức nào mà chưa mất recall?
- [ ] Bao nhiêu phần trăm lượt thật sự có op (đo trên dữ liệu môi trường chuyên nghiệp)? Cổng ở ngưỡng nào giữ ≥ 95% recall so với trần, và khi đó cắt được bao nhiêu lượt gọi bộ trích?
- [ ] Bộ trích dùng `nano` có đủ không, hay cần `mini`?
- [ ] Mỗi task đã duyệt sinh bao nhiêu đề xuất thay đổi cần duyệt, bao nhiêu trong đó là sai hoặc thừa? (Gánh nặng duyệt có chấp nhận được không.)
- [ ] Bao nhiêu task được phát hiện nhưng không hiện được vì không gán được người nhận? Hệ thống có gán bừa người nhận cho việc chưa ai nhận không?
- [ ] Đòi thêm hạn (`draft_min = assignee_due`) làm chậm và mất bao nhiêu task so với chỉ cần người nhận?

Nếu bộ đánh giá có dưới khoảng 100 task nhãn, kết quả chỉ nên đọc như tín hiệu định hướng.

## 12. Cần xác nhận với PM / business

**Đã xác nhận (11/11):**

- Có bước duyệt: duyệt / từ chối / sửa nháp, do **người nhận** thực hiện (người nhận quên thì việc không được làm; người giao quên sẽ nhớ lại khi nhận kết quả).
- Nháp hiện khi **có việc + người nhận**, chưa cần hạn; chưa có người nhận thì chưa hiện. Trường tối thiểu sẽ tinh chỉnh sau (tham số `draft_min`).
- **Mọi thay đổi** trên task đã duyệt đều phải duyệt (đề xuất thay đổi, mục 4.2).

**Còn mở:**

1. Ngưỡng recall / precision ở mục 8.
2. Tự nhận việc (`self_commit`) có nằm trong phạm vi nhắc việc không?
3. Quy ước thời gian ở mục 6.
4. Nhiều người nhận: duyệt riêng từng người như mục 4.2 có ổn không, hay nên tách thành một task cho mỗi người?
5. Người giao có cần được thông báo khi người nhận từ chối nháp hay đề xuất thay đổi không? (Prototype chỉ ghi vào trace.)
6. Việc giao mà không xác định được người nhận ("em làm giúp anh cái này" trong nhóm đông) hiện đang giữ ẩn: có cần báo cho người giao để họ chỉ định người không?
