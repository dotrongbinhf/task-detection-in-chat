# AI Reminder — phát hiện và tạo task từ chat (prototype v6)

Cài đặt theo `AI Reminder — Prototype detection task v6.md` (đo cho đúng trước · người nhận bằng `user_id` · danh mục task ·
đợi hội thoại yên). Phần không đổi (sổ cái, Validate, người nhận duyệt) có lý do ở tài liệu v3–v5 và `backend/DECISIONS.md`.
Bộ đánh giá `data/eval` viết tay, chỉ hội thoại nhóm: 21 hội thoại, 1.467 tin, 195 nhãn (189 có người nhận), 113 hard negative có tag.

```
task-detection-in-chat/
  backend/     Python — engine (gói `detect`) + API FastAPI mỏng cho UI
    detect/    models · llm · embed · timeexpr · ledger · store · context · extract · run · eval · compare · server · label · build_eval · anonymize
    prompts/   extract.txt
    data/sample/  dữ liệu demo nhỏ (UI)
    data/eval/    bộ đánh giá dựng từ data/eval_src (xem data/LABELING.md)
    tests/     unit test với FakeLLM (không gọi mạng)
    config.yaml  .env.example  DECISIONS.md
  frontend/    Next.js + Tailwind — chỉ là UI demo, gọi backend qua /api
```

> **Hai lưu ý bắt buộc**
> 1. **Chỉ gửi dữ liệu tự sinh lên API bên ngoài.** Không dùng chat thật của tổ chức.
> 2. **Số liệu không chuyển được sang model khác.** Model tham chiếu là `gpt-4o-mini`; đổi model phải chạy lại toàn bộ đánh giá.

## Mỗi lượt xử lý gì

Một lượt = các tin của một hội thoại đến liền nhau cho tới khi hội thoại **yên** `quiet_seconds` (60 s; trần 5 phút / 10 tin).
Độ trễ thật = độ trễ đo + `quiet_seconds`. Reaction không sinh lượt — in kèm dòng tin (`[👍 Nam]`), không đổi task.

1. **Hết hạn & thao tác duyệt** tới thời điểm của lượt. Thao tác duyệt trỏ tới tin chưa xử lý được hoãn tới ngay sau lượt chứa tin đó.
2. **Dựng ngữ cảnh** — `Conversation type` + `<MEMBERS>` (user_id, tên, alias) · `<CALENDAR>` · ba khối task, mỗi task đúng một lần,
   `T<n>` đánh liên tục:
   - `<LINKED_TASKS>`: task có tin bằng chứng mà TIN MỚI reply vào (đi ngược chuỗi reply). Chi tiết đầy đủ, không bao giờ bị cắt.
   - `<TASK_DETAILS>`: 3 task mở nhắc gần nhất + 2 task mở gần nghĩa nhất với TIN MỚI (embedding, chỉ xếp hạng, không ngưỡng).
   - `<TASK_INDEX>`: mọi task mở còn lại (+ task bị từ chối gần đây), một dòng một task, ≤ 30; tràn thì 25 gần nhất + 5 gần nghĩa nhất.
   - `<RECENT_HISTORY>` (K tin, dòng tin có `user_id` tác giả) và `<NEW_MESSAGES>`.
3. **Bộ trích (LLM)** trả `{message_type, ops}`. Người nhận là `user_id`. UPDATE dùng trường phẳng như CREATE: trường khác null =
   thay đổi; `assignees_add` / `_remove`, `notes_add`, `due_shift_days`.
4. **`detail_check`** (hiếm): UPDATE thay `notes` / `title` trên task chỉ có ở `<TASK_INDEX>` → gọi lần 2 kèm chi tiết task đó.
5. **Validate** (id có trong prompt, op dựa vào TIN MỚI, không trùng anchor, `user_id` lạ → khớp tên hoặc bỏ, quy hạn), rồi áp vào **sổ cái**.
6. Task có người nhận hiện **nháp** cho người đó duyệt. Task hết hạn chuyển `closed` kèm `closed_reason`.

## 1. Backend (Python 3.11+)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # rồi điền OPENAI_API_KEY (bắt buộc: v6 không còn bộ trích giả lập)
```

```bash
python -m detect.llm probe                                                  # model nhận json_schema strict không
python -m detect.run  --data data/eval --split dev --jobs 4 --id dev1       # → runs/dev1/ (tasks, trace, state)
python -m detect.eval --run runs/dev1 --data data/eval                      # → runs/dev1/report.md
python -m detect.compare --pair runs/dev1 runs/dev2 --data data/eval        # nhãn được thêm / bị mất
python -m detect.compare "runs/dev*" --data data/eval                       # bảng nhiều lần chạy
pytest -q
```

- `--jobs N` (mặc định 4): mỗi hội thoại một `EngineState` trên một luồng, xong thì gộp; kết quả như chạy tuần tự (`--jobs 1`).
- `--seed N`: seed của lời gọi LLM (nằm trong khoá cache đĩa) — chạy 3 seed để biết mức nhiễu.
- `--no-cache` bỏ qua cache đĩa (`.cache/llm/`, `.cache/emb/`); `--set key=value` ghi đè `config.yaml`.
- `test` chỉ chạy khi báo cáo kết quả cuối; khi chỉnh dùng `--split dev`.

Báo cáo (`report.md`): **nháp đúng hoàn toàn** (nhãn có người nhận, ghép được task, đúng người, đúng hạn lịch, đúng trạng thái huỷ),
recall, precision, người nhận / hạn đúng — kèm khoảng **bootstrap theo hội thoại** (2.000 lần); op theo lượt từ `turn_labels.jsonl`
(lượt báo nhầm, bỏ sót, đúng loại, báo nhầm theo loại hard negative); candidate recall theo tầng `linked` / `details` / `index`;
link accuracy; số lượt `detail_check`, kích thước lượt, số task trong `<TASK_INDEX>`.

### Nhãn theo lượt và dữ liệu

Hướng dẫn gán nhãn: `backend/data/LABELING.md`. Lượt = hàng đợi của engine (không gọi LLM).

```
python -m detect.build_eval --src data/eval_src --out data/eval                 # dựng data/eval từ chat viết tay (in tỉ lệ v6 mục 10)
python -m detect.label stats data/eval/turn_labels.jsonl --data data/eval       # nhãn lượt + tỉ lệ dữ liệu
python -m detect.label export --data data/eval --out turns.jsonl --k 5          # file cho người gán nhãn
python -m detect.label agree a.jsonl b.jsonl                                    # Cohen's kappa
python -m detect.anonymize --in raw/messages.jsonl --members raw/members.json --out data/eval/ --map-out <ngoài repo>/map.json
```

## 2. UI demo

Chạy backend (cổng 8000) rồi frontend (cổng 3000):

```bash
cd backend && .venv\Scripts\activate && uvicorn detect.server:app --port 8000 --reload
```

```bash
cd frontend && npm install && npm run dev
```

Mở http://localhost:3000:

- **Chat** — luồng tin theo thời gian mô phỏng; tự nhập tin (người gửi, @mention, trả lời, thả reaction — chỉ gắn vào tin, không sinh lượt).
- **Pipeline** — mỗi lượt 7 bước: hàng đợi → hết hạn & thao tác duyệt → dựng ngữ cảnh (token theo khối, ba tầng task, xem prompt) →
  bộ trích (từng lần gọi: token, cache, tiền, JSON thô) → `detail_check` → Validate → sổ cái.
- **Sổ cái** — task theo trạng thái (`closed_reason`), bằng chứng, đề xuất thay đổi; **Hộp duyệt** theo từng người nhận.
- **Trace / Đánh giá** — trace.jsonl và báo cáo (khoảng bootstrap, op theo lượt, theo tag, lỗi kèm đường đi).

Không có `OPENAI_API_KEY`: UI hiện thông báo, engine không chạy (xem được dữ liệu và chấm điểm).
