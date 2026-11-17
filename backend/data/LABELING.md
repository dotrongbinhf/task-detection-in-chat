# Hướng dẫn gán nhãn (v5 mục 6.5)

Người gán nhãn **không** là người viết prompt. Mỗi lượt gán độc lập, không xem nhãn của người khác, không xem kết quả của model.

## 1. "Việc" là gì

Một câu tạo ra trách nhiệm làm một việc cụ thể (v3 mục 1):

| Loại | Ví dụ |
|---|---|
| `assigned` — giao cho người khác | "@Linh gửi báo cáo tuần cho anh trước thứ 5 nhé" |
| `self_commit` — tự hứa | "Để em làm slide, chiều em gửi" |
| `open_ask` — nêu việc, chưa có người nhận | "Ai rảnh review PR này giúp mình không?" |

**Không** phải việc: "để anh xem đã", hỏi ý kiến, báo tiến độ ("đang làm"), báo xong ("em gửi rồi ạ"), chào hỏi, bàn luận.

## 2. Nhãn lượt (`turn_labels.jsonl`)

Một lượt = các tin của một hội thoại đến liền nhau cho tới khi hội thoại yên (`quiet_seconds`) — xem cột `new_text` trong file export; `context` chỉ để đọc hiểu.

`label = 1` khi lượt **tự nó** làm thay đổi danh sách việc. `kinds` (chọn một hoặc nhiều):

| `kinds` | Khi nào | Ví dụ → nhãn |
|---|---|---|
| `assign` / `self_commit` / `open_ask` | nêu việc mới | xem bảng trên → 1 |
| `accept` | nhận việc **làm đổi người nhận** (việc chưa có người, hoặc người nhận khác) | "Ai làm được?" → "để em làm" → 1 |
| `due` | đổi hạn | "lùi sang thứ 6 nhé" → 1 |
| `assignee` | đổi người làm | "thôi để Nam làm" → 1 |
| `content` | đổi nội dung việc | "làm thêm bản tiếng Anh nữa" → 1 |
| `cancel` | huỷ / thôi | "cái banner thôi khỏi làm nhé" → 1 |

`label = 0`: mọi thứ còn lại. Ví dụ "em gửi rồi ạ" → 0, "để em xem rồi báo lại" → 0.

`confirm` (nhãn 0): tin xác nhận không đổi sổ cái — "dạ", "ok anh" khi task đã giao đúng người đó (được @mention hoặc gọi tên trong tin giao). Trong nguồn ghi `## <gold>:confirm`; tin này vào `confirm_message_ids` của nhãn, **không** vào `supporting`.

Reaction (👍…) **không** làm đổi sổ cái: không gắn nhãn cho dòng reaction. Việc chỉ có người nhận nhờ reaction thì thêm tin chữ nhận việc, hoặc để nhãn không có người nhận.

Ca biên:

- Báo tiến độ kèm hứa có mốc ("đang làm, mai xong") → 1 `self_commit`. Không có mốc ("đang làm") → 0.
- "Ai rảnh làm X không" → 1 `open_ask`.
- "Dạ" của người đã được giao → 0 `confirm`. "Dạ" của người chưa được giao mà nhận việc → 1 `accept`.
- Tin vừa báo xong vừa giao việc mới → 1 (theo phần giao việc).
- Không chắc → ghi lý do vào `note`, vẫn chọn 0 hoặc 1.

## 3. Nhãn task (`gold.jsonl`)

- `supporting_message_ids`: **đúng** các tin làm đổi sổ cái (giao, nhận làm đổi người, đổi hạn/người/nội dung, huỷ), không phải cả đoạn chat, không gồm reaction và tin `confirm` (`confirm_message_ids`).
- `anchor_message_id`: tin đầu tiên nêu việc. `kind`: như mục 1. `assignee_ids`: người làm cuối cùng.
- Hạn (`due_type`, `due_date`): theo quy ước v3 mục 6 ("thứ 5" = thứ 5 gần nhất sau tin, "cuối tuần" = thứ 6…). Hạn là sự kiện ("sau buổi demo") → `event`.
- `final_status`: `cancelled` nếu việc bị huỷ trong chat.

## 4. Quy trình

1. `python -m detect.label export --data data/eval --out turns.jsonl` → mỗi người một bản, điền `label`, `kinds`, `note`, `annotator`.
2. Hai người cùng gán ít nhất 100 lượt giống nhau → `python -m detect.label agree a.jsonl b.jsonl`. **Kappa < 0.7 → sửa tài liệu này** (thêm ví dụ cho các ca bất đồng) rồi gán lại.
3. Phân xử bất đồng → `python -m detect.label import --data data/eval a.jsonl b.jsonl --adjudicator A --out data/eval/turn_labels.jsonl` (bất đồng ghi ở `conflicts.jsonl`).
4. `python -m detect.label stats data/eval/turn_labels.jsonl --data data/eval`.

Kappa đã đo: _chưa có_ (ghi số, ngày, số lượt ở đây).

## 4b. Trạng thái `data/eval` hiện tại (v6, 17/11/2026)

- **Viết tay**, 21 hội thoại nhóm (không DM) trong `data/eval_src/*.txt` (định dạng nguồn: docstring `detect/build_eval.py`), dựng bằng
  `python -m detect.build_eval --src data/eval_src --out data/eval`. **Không sửa tay file trong `data/eval/`** — sửa nguồn rồi dựng lại.
- Split: `dev` 14 hội thoại (114 nhãn có người nhận), `test` 7 hội thoại (75 nhãn) — v6 gộp `train` cũ vào `dev`. `test` chỉ chạy
  khi báo cáo cuối.
- 1.467 tin (940 lượt), 195 nhãn (189 có người nhận), 113 hard negative có tag (`hard_negatives.txt`: `<message_id>\t<tag>`), mỗi loại v6 ≥ 5:
  `done_report`, `status_question`, `conditional`, `negation`, `other_team`, `quote`, `idea`, `reminder`, `joke`; thêm `defer`,
  `name_time_word` từ v5.
- `ch_ecom` (dev) và `ch_saas` (test): 11 ngày, đỉnh 44 / 43 task mở, tiêu đề gần giống nhau ("PR #482" / "#490", báo cáo tuần
  của hai người, hai tuần), nhắc việc cũ không reply, sửa nội dung việc cũ — tag `index_overflow`, `similar_titles`,
  `detail_check`, `ttl_long`.
- v6 mục 10.2 (in bởi `label stats --data`): tin giao việc có @mention 38% (≤ 50%), có "nhé / giúp / nha" 20% (≤ 40%),
  trung vị 8 từ / tin (≥ 8). Để đạt, các hội thoại cũ đã bỏ 526 tin đệm ≤ 3 từ không nhãn ("ok", "dạ" không nhãn, "hay") và bỏ
  "nhé / nha" cuối câu ở tin giao việc.
- **Giới hạn:** người viết dữ liệu = người gán nhãn = người viết prompt (Claude, được người dùng cho phép). Chỉ có một người
  gán nên chưa đo được kappa; nhãn lượt `annotator = "author"`. Số đo trên bộ này thiên về các ca prompt đã biết — thay bằng
  chat thật đã ẩn danh khi có quyền.

## 5. Đưa chat thật vào

Chuyển export Teams/Zalo sang `messages.jsonl` + `members.json` cùng định dạng `data/sample/` (một tin một dòng: `id`, `conversation_id`, `conversation_type`, `created_at` ISO có múi giờ, `author_id`, `author_name`, `text`, `mentions`, `reply_to_id`, `reactions`, `attachments`). Việc chuyển này nằm ngoài v5. Sau đó **luôn** ẩn danh trước khi gán nhãn:

```
python -m detect.anonymize --in raw/messages.jsonl --members raw/members.json --out data/eval/ --map-out <ngoài repo>/map.json [--salt <salt đợt trước>]
```

Bảng ánh xạ (`map.json`) không bao giờ commit. Đọc lướt `messages.jsonl` sau ẩn danh: tên viết hoa đầu câu trùng từ thường ("Mai em gửi…") bị đổi thành tên giả — sửa tay nếu gặp.
