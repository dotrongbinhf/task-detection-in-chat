# AI Reminder — phát hiện và tạo task từ chat (prototype v5)

Cài đặt theo tài liệu `AI Reminder — Prototype detection task v3.md` (sổ cái, Validate, đánh giá), `… v4.md` (task được link,
kho task, kiểm trùng) và `… v5.md` (code tìm ứng viên trước, luật cứng). **v5 hiện xong Bước A** (mục 13 tài liệu v5);
Bước B (bộ đánh giá `data/eval`, công cụ gán nhãn) và Bước C (embedder cục bộ, cổng phân loại) chưa làm.

```
task-detection-in-chat/
  backend/     Python — engine đúng mục 10 của docs (gói `detect`) + API FastAPI mỏng cho UI
    detect/    models · llm · timeexpr · ledger · store · context · gate · extract · run · eval · compare · server
    prompts/   gate_v3.txt · extract_v3.txt (scored) · extract_v4.txt (linked_recent_search) · extract_v5.txt (retrieve_first)
    data/sample/  messages.jsonl · members.json · gold.jsonl · hard_negatives.txt · user_actions.jsonl
    tests/     unit test với FakeLLM (không gọi mạng)
    config.yaml  .env.example  DECISIONS.md
  frontend/    Next.js + Tailwind — chỉ là UI demo, gọi backend qua /api
```

> **Hai lưu ý bắt buộc**
> 1. **Chỉ gửi dữ liệu tự sinh lên API bên ngoài.** Không dùng chat thật của tổ chức.
> 2. **Số liệu không chuyển được sang model khác.** Khi chuyển sang model nội bộ phải chạy lại toàn bộ đánh giá.

## 1. Backend (Python 3.11+)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # rồi điền OPENAI_API_KEY (bỏ trống = chạy bộ trích giả lập offline)
```

Lệnh theo docs:

```bash
python -m detect.llm probe                                         # kiểm tra model có logprobs + json_schema strict
python -m detect.run  --data data/sample --set gate_mode=shadow    # → runs/<id>/tasks.jsonl, trace.jsonl, state.json
python -m detect.eval --run runs/<id> --data data/sample           # → runs/<id>/report.md
python -m detect.compare "runs/*"                                  # → runs/compare.md
pytest -q                                                          # unit test mục 9
```

`--mode heuristic` ép dùng bộ trích giả lập kể cả khi có key; `--no-cache` bỏ qua cache đĩa (`.cache/llm/`).

### Ba chế độ ngữ cảnh (`context_mode`)

- `retrieve_first` (mặc định, v5) — **code tìm trước, LLM phán sau.** Trước lần gọi 1, code dựng khối `<CANDIDATE_TASKS>`:
  task **khớp nội dung TIN MỚI** (`[matched: bao, cao, tuan · 0.52]` — tìm trong kho bằng BM25 + embedding, truy vấn = nội dung
  TIN MỚI bỏ @mention và bỏ từ mô tả thay đổi, nối tin được reply nếu nó ngoài cửa sổ K) rồi task **gần nhất** (`[recent]`) lấp
  cho đủ `candidate_tasks_max`; task được link giữ khối `<LINKED_TASKS>` như v4. Schema bỏ `search`: model chỉ báo
  `message_type = old_task_not_in_prompt` → code tự tìm → lần 2 (`llm_flag`). **Luật cứng** `dedup_hard_block`: CREATE trùng
  mạnh (≥ 3 từ khoá chung với tiêu đề) một task **đã bị từ chối** của cùng người → code bỏ op, không hỏi LLM. UPDATE/CANCEL
  nhắm task khác task được link duy nhất → ghi `target_not_linked` (chỉ đếm, không sửa). Kiểm tra target và kiểm trùng giữ như v4.
- `linked_recent_search` (v4) — tài liệu *Ngữ cảnh mới*: task **được link** (TIN MỚI reply / thả reaction vào tin bằng chứng, khối 3a) + **3 task được nhắc gần nhất** (3b) + lịch sử gần. Việc cũ hơn: LLM điền `search` → code tìm trong kho task (`detect/store.py`, BM25 bỏ dấu) → gọi lần 2; trước khi áp CREATE code **kiểm trùng** trong kho; UPDATE/CANCEL nhắm task lệch nội dung được **kiểm tra target**. Tìm trong kho mặc định là **hybrid** (`search_mode`): BM25 bỏ dấu + embedding `text-embedding-3-small`, trộn bằng RRF; không có key thì dùng embedder n-gram offline (không hiểu nghĩa). Tối đa 2 lần gọi bộ trích mỗi lượt.
- `scored` — v3 (bảng điểm, top 5), giữ để so sánh.

So sánh ba chế độ trên cùng dữ liệu (`compare` đặt `retrieve_first` ở cột đầu):

```bash
python -m detect.run --data data/sample --mode openai --set context_mode=retrieve_first --id cmp5_retrieve_first
python -m detect.run --data data/sample --mode openai --set context_mode=linked_recent_search --id cmp5_linked_recent_search
python -m detect.run --data data/sample --mode openai --set context_mode=scored --id cmp5_scored
python -m detect.eval --run runs/cmp5_retrieve_first --data data/sample      # (lặp cho từng lần chạy)
python -m detect.compare "runs/cmp5_*" --data data/sample --out runs/compare_v5.md
```

Báo cáo v5 thêm mục *Tầng tìm ứng viên & tầng model*: **candidate recall** (task đúng có trong prompt lần 1 không, qua nguồn
linked / matched / recent, hay vào lần 2, hay bỏ lỡ), **link accuracy** (task đúng có trong prompt → model nhắm đúng không),
lần gọi 2 theo lý do, chặn cứng đúng / sai, `target_not_linked`, FN loại 8.

Dữ liệu mẫu sinh lại bằng `node data/gen_sample.mjs` (trong `backend/`).

## 2. UI demo

Chạy backend (cổng 8000) rồi frontend (cổng 3000):

```bash
cd backend && .venv\Scripts\activate && uvicorn detect.server:app --port 8000 --reload
```

```bash
cd frontend && npm install && npm run dev
```

Mở http://localhost:3000:

- **Chat** — luồng tin theo thời gian mô phỏng; tự nhập tin (chọn người gửi, @mention, trả lời, thả 👍, chọn “+1 phút / +1 giờ / +1 ngày”). Tin của lượt đang chọn có id cục bộ `#n` trong prompt; tin bằng chứng của task đang chọn được tô cam kèm vai trò.
- **Pipeline** — mỗi lượt gồm 9 bước: hàng đợi/gộp tin → hết hạn & user_actions → dựng ngữ cảnh (token theo khối; nhóm **Được link** / **Khớp nội dung** (truy vấn, từ khớp, cosine) / **Gần nhất** / Lịch sử gần, hoặc bảng điểm ở chế độ scored; budget_trim; xem prompt) → cổng (p) → bộ trích (từng lần gọi kèm token, cache, tiền, JSON thô, khối 6) → **Tìm task** (truy vấn, kết quả kèm điểm và từ khớp; kiểm tra target) → **Kiểm trùng** (kèm **chặn cứng**) → Validate (kèm `target_not_linked`) (lý do loại, gán người nhận, quy hạn) → sự kiện sổ cái.
- **Sổ cái** — task theo trạng thái (nháp / đã duyệt / ẩn / huỷ…), bằng chứng, đề xuất thay đổi; **Hộp duyệt** theo từng người nhận (duyệt / từ chối nháp và đề xuất).
- **Trace / Đánh giá** — trace.jsonl và báo cáo mục 8 (Wilson CI, theo tag, đường cong cổng, lỗi kèm đường đi).
- Thanh điều khiển: Lượt kế · Tự chạy · Chạy hết · Tua thời gian (để thấy luật hết hạn) · đổi `gate_mode` và mọi tham số mục 6.

Không có `OPENAI_API_KEY` thì UI và CLI chạy **bộ trích giả lập bằng luật** (ghi rõ trên giao diện). Nó chỉ dùng để xem luồng, **không** đo chất lượng; con số thật phải chạy với OpenAI.
