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

Một lượt = một tin mới (hoặc vài tin đến dồn trong vài giây) — xem cột `new_text` trong file export; `context` chỉ để đọc hiểu.

`label = 1` khi lượt **tự nó** làm thay đổi danh sách việc. `kinds` (chọn một hoặc nhiều):

| `kinds` | Khi nào | Ví dụ → nhãn |
|---|---|---|
| `assign` / `self_commit` / `open_ask` | nêu việc mới | xem bảng trên → 1 |
| `accept` | nhận việc (chữ hoặc 👍 vào tin giao việc) | "Dạ ok anh" → 1 · 👍 vào tin giao việc → 1 |
| `due` | đổi hạn | "lùi sang thứ 6 nhé" → 1 |
| `assignee` | đổi người làm | "thôi để Nam làm" → 1 |
| `content` | đổi nội dung việc | "làm thêm bản tiếng Anh nữa" → 1 |
| `cancel` | huỷ / thôi | "cái banner thôi khỏi làm nhé" → 1 |

`label = 0`: mọi thứ còn lại. Ví dụ "em gửi rồi ạ" → 0, "để em xem rồi báo lại" → 0.

Ca biên:

- Báo tiến độ kèm hứa có mốc ("đang làm, mai xong") → 1 `self_commit`. Không có mốc ("đang làm") → 0.
- "Ai rảnh làm X không" → 1 `open_ask`.
- Tin vừa báo xong vừa giao việc mới → 1 (theo phần giao việc).
- Không chắc → ghi lý do vào `note`, vẫn chọn 0 hoặc 1.

## 3. Nhãn task (`gold.jsonl`)

- `supporting_message_ids`: **đúng** các tin tạo nên task (giao, nhận, đổi hạn/người/nội dung, huỷ; reaction ghi `<id tin>#r<n>`), không phải cả đoạn chat.
- `anchor_message_id`: tin đầu tiên nêu việc. `kind`: như mục 1. `assignee_ids`: người làm cuối cùng.
- Hạn (`due_type`, `due_date`): theo quy ước v3 mục 6 ("thứ 5" = thứ 5 gần nhất sau tin, "cuối tuần" = thứ 6…). Hạn là sự kiện ("sau buổi demo") → `event`.
- `final_status`: `cancelled` nếu việc bị huỷ trong chat.

## 4. Quy trình

1. `python -m detect.label export --data data/eval --out turns.jsonl` → mỗi người một bản, điền `label`, `kinds`, `note`, `annotator`.
2. Hai người cùng gán ít nhất 100 lượt giống nhau → `python -m detect.label agree a.jsonl b.jsonl`. **Kappa < 0.7 → sửa tài liệu này** (thêm ví dụ cho các ca bất đồng) rồi gán lại.
3. Phân xử bất đồng → `python -m detect.label import --data data/eval a.jsonl b.jsonl --adjudicator A --out data/eval/turn_labels.jsonl` (bất đồng ghi ở `conflicts.jsonl`).
4. `python -m detect.label stats data/eval/turn_labels.jsonl --data data/eval`.

Kappa đã đo: _chưa có_ (ghi số, ngày, số lượt ở đây).

## 4b. Trạng thái `data/eval` hiện tại (13/11/2026)

- **Đợt 0, viết tay**: 21 hội thoại nhóm (không DM) trong `data/eval_src/*.txt` (định dạng nguồn: docstring `detect/build_eval.py`), dựng bằng
  `python -m detect.build_eval --src data/eval_src --out data/eval`. **Không sửa tay file trong `data/eval/`** — sửa nguồn rồi dựng lại.
- 1.649 tin, 1.617 lượt, 15,0% lượt dương; 106 nhãn (102 có người nhận); 34 hard negative; mọi tag v3 §9, v4 §12, v5 §12 ≥ 3 ca (trừ `dm_addressee`).
- Lượt không có việc là **chat công việc** (bàn kỹ thuật, báo tiến độ, hỏi thông tin), ít chuyện phiếm — như tổ chức chuyên nghiệp.
- Split theo hội thoại và tuần: train = 16–21/11 (10 hội thoại), dev = 23–27/11 (4), test = 30/11–4/12 (7).
- **Giới hạn:** người viết dữ liệu = người gán nhãn = người viết prompt (Claude, được người dùng cho phép). Chỉ có một người
  gán nên chưa đo được kappa; nhãn lượt `annotator = "author"`. Số đo trên bộ này thiên về các ca prompt đã biết — thay bằng
  chat thật đã ẩn danh khi có quyền.

## 5. Đưa chat thật vào

Chuyển export Teams/Zalo sang `messages.jsonl` + `members.json` cùng định dạng `data/sample/` (một tin một dòng: `id`, `conversation_id`, `conversation_type`, `created_at` ISO có múi giờ, `author_id`, `author_name`, `text`, `mentions`, `reply_to_id`, `reactions`, `attachments`). Việc chuyển này nằm ngoài v5. Sau đó **luôn** ẩn danh trước khi gán nhãn:

```
python -m detect.anonymize --in raw/messages.jsonl --members raw/members.json --out data/eval/ --map-out <ngoài repo>/map.json [--salt <salt đợt trước>]
```

Bảng ánh xạ (`map.json`) không bao giờ commit. Đọc lướt `messages.jsonl` sau ẩn danh: tên viết hoa đầu câu trùng từ thường ("Mai em gửi…") bị đổi thành tên giả — sửa tay nếu gặp.
