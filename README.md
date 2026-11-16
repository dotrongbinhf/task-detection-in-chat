# AI Reminder — phát hiện và tạo task từ chat (prototype v5.1)

Cài đặt theo tài liệu `AI Reminder — Prototype detection task v3.md` (sổ cái, Validate, đánh giá), `… v4.md` (task được link,
kho task) và `… v5.md` (code tìm ứng viên trước, LLM phán sau). **v5.1** là bản đã dọn gọn: chỉ còn một cách dựng ngữ cảnh,
một lý do gọi LLM lần 2, không còn luật đếm từ quyết định thay LLM (lý do: `backend/DECISIONS.md` mục *v5.1*).
Đã có bộ đánh giá `data/eval` viết tay, chỉ hội thoại nhóm: 1.617 lượt, 102 nhãn có người nhận, 15% lượt có việc.

```
task-detection-in-chat/
  backend/     Python — engine (gói `detect`) + API FastAPI mỏng cho UI
    detect/    models · llm · timeexpr · ledger · store · context · gate · extract · run · replay · eval · compare · server · label · anonymize
    prompts/   extract.txt (bộ trích) · gate.txt (cổng tuỳ chọn)
    data/sample/  messages.jsonl · members.json · gold.jsonl · hard_negatives.txt · user_actions.jsonl
    data/eval/    bộ đánh giá dựng từ data/eval_src (xem data/LABELING.md)
    tests/     unit test với FakeLLM (không gọi mạng)
    config.yaml  .env.example  DECISIONS.md
  frontend/    Next.js + Tailwind — chỉ là UI demo, gọi backend qua /api
```

> **Hai lưu ý bắt buộc**
> 1. **Chỉ gửi dữ liệu tự sinh lên API bên ngoài.** Không dùng chat thật của tổ chức.
> 2. **Số liệu không chuyển được sang model khác.** Khi chuyển sang model nội bộ phải chạy lại toàn bộ đánh giá.

## Mỗi lượt xử lý gì

Một lượt = một tin mới (hoặc vài tin đến dồn trong `coalesce_seconds`).

1. **Hết hạn & thao tác duyệt** tới thời điểm của lượt.
2. **Dựng ngữ cảnh — code tìm task trước.** Prompt gồm:
   - `<LINKED_TASKS>`: task có tin bằng chứng mà TIN MỚI reply / thả reaction vào (đi ngược chuỗi reply). Không bao giờ bị cắt.
   - `<CANDIDATE_TASKS>`: task **khớp nội dung** TIN MỚI (`[matched: …]`, tìm bằng BM25 bỏ dấu + embedding, trộn RRF), rồi task
     **nhắc gần nhất** (`[recent]`) lấp cho đủ `candidate_tasks_max`.
   - `<RECENT_HISTORY>` (K tin trước) và `<NEW_MESSAGES>`.
3. **Cổng** (tuỳ chọn, mặc định tắt `gate_mode = none`).
4. **Bộ trích (LLM)** trả `{message_type, ops}`. Nếu một UPDATE / CANCEL nhắm task khớp TIN MỚI kém hơn một task ngoài prompt,
   code đưa các task đó vào **lần gọi 2** (`target_check`) để LLM chọn lại. Đây là lý do duy nhất để gọi lần 2.
5. **Validate** (id có trong prompt, op phải dựa vào TIN MỚI, không trùng anchor, quy hạn, gán người nhận), rồi áp vào **sổ cái**.
   Hai chốt chặn dựa trên tín hiệu có cấu trúc: `SPEAKER` trỏ vào tin @mention người khác thì người nhận là người được @mention;
   "nhận việc" bằng `SPEAKER` thì thêm người nhận chứ không thay.
6. Task có người nhận hiện **nháp** cho người đó duyệt. Code không tự chặn nháp theo số từ trùng; người duyệt quyết.

## 1. Backend (Python 3.11+)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # rồi điền OPENAI_API_KEY (bỏ trống = chạy bộ trích giả lập offline)
```

```bash
python -m detect.llm probe                                         # kiểm tra model có logprobs + json_schema strict
python -m detect.run  --data data/sample --id my_run               # → runs/my_run/tasks.jsonl, trace.jsonl, state.json
python -m detect.eval --run runs/my_run --data data/sample         # → runs/my_run/report.md
python -m detect.compare "runs/*" --data data/sample               # → runs/compare.md
pytest -q
```

`--mode heuristic` ép dùng bộ trích giả lập kể cả khi có key; `--no-cache` bỏ qua cache đĩa (`.cache/llm/`);
`--set key=value` ghi đè `config.yaml`.

### Chạy nhanh khi đang chỉnh

`data/eval` có 1.617 lượt, mỗi lượt ít nhất một lần gọi LLM (20–30 phút). Khi đang chỉnh prompt / code, dùng hai mức nhanh:

```bash
# Mức 1 — chỉ gọi LLM thật ở lượt đáng xem (lượt có việc + hard negative + 40 lượt không có việc), khoảng 1–2 phút
python -m detect.replay --base runs/<lần chạy nền> --data data/eval --split dev --id try1    # → runs/try1/replay.md
# Mức 2 — chạy đủ một phần của split.json, khoảng 4–5 phút
python -m detect.run --data data/eval --split dev --id dev1
python -m detect.eval --run runs/dev1 --data data/eval                                        # tự chỉ chấm nhãn của dev
```

Mức 1 dựng lại sổ cái thật bằng code hiện tại; lượt không được chọn dùng lại đầu ra của lần chạy nền (`--base`, nên là lần chạy
đủ cùng split hoặc cả bộ). `replay.md` liệt kê lượt được chọn có kết quả khác lần chạy nền. Chốt thay đổi bằng mức 2, rồi cả bộ
(bỏ `--split`). Phần `test` chỉ chạy khi báo cáo kết quả cuối.

Báo cáo có mục *Tầng tìm ứng viên & tầng model*: **candidate recall** (task đúng có trong prompt lần 1 không, qua nguồn linked /
matched / recent, hay vào lần 2, hay bỏ lỡ) và **link accuracy** (task đúng có trong prompt → model nhắm đúng không).

Dữ liệu mẫu sinh lại bằng `node data/gen_sample.mjs` (trong `backend/`).

### Nhãn theo lượt và ẩn danh

Hướng dẫn gán nhãn và quy trình: `backend/data/LABELING.md`. Lượt = hàng đợi + gộp tin của engine (không gọi LLM).

```
python -m detect.label export --data data/sample --out turns.jsonl --k 5        # file cho người gán nhãn
python -m detect.label derive --data data/sample --out turn_labels.jsonl         # nhãn suy từ gold + hard negatives
python -m detect.label agree a.jsonl b.jsonl                                     # Cohen's kappa
python -m detect.label import --data data/eval a.jsonl b.jsonl --adjudicator A --out data/eval/turn_labels.jsonl
python -m detect.label stats turn_labels.jsonl --data data/eval                  # theo split.json nếu có
python -m detect.build_eval --src data/eval_src --out data/eval                 # dựng data/eval từ chat viết tay
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

- **Chat** — luồng tin theo thời gian mô phỏng; tự nhập tin (chọn người gửi, @mention, trả lời, thả 👍, chọn “+1 phút / +1 giờ / +1 ngày”).
- **Pipeline** — mỗi lượt 8 bước: hàng đợi/gộp tin → hết hạn & user_actions → dựng ngữ cảnh (token theo khối; nhóm **Được link** /
  **Khớp nội dung** (truy vấn, từ khớp, cosine) / **Gần nhất**; xem prompt) → cổng (p) → bộ trích (từng lần gọi kèm token, cache,
  tiền, JSON thô) → **kiểm tra target** → Validate (lý do loại, gán người nhận, quy hạn) → sự kiện sổ cái.
- **Sổ cái** — task theo trạng thái, bằng chứng, đề xuất thay đổi; **Hộp duyệt** theo từng người nhận.
- **Trace / Đánh giá** — trace.jsonl và báo cáo (Wilson CI, theo tag, candidate recall, link accuracy, lỗi kèm đường đi).
- Thanh điều khiển: Lượt kế · Tự chạy · Chạy hết · Tua thời gian · đổi `gate_mode` và các tham số trong `config.yaml`.

Không có `OPENAI_API_KEY` thì UI và CLI chạy **bộ trích giả lập bằng luật** (ghi rõ trên giao diện). Nó chỉ dùng để xem luồng,
**không** đo chất lượng; con số thật phải chạy với OpenAI.
