# AI Reminder — phát hiện và tạo task từ chat (prototype v3)

Cài đặt theo tài liệu `AI Reminder — Prototype nhanh phát hiện và tạo task v3.md`.

```
task-detection-in-chat/
  backend/     Python — engine đúng mục 10 của docs (gói `detect`) + API FastAPI mỏng cho UI
    detect/    models · llm · timeexpr · ledger · context · gate · extract · run · eval · compare · server
    prompts/   gate_v3.txt · extract_v3.txt
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
- **Pipeline** — mỗi lượt gồm 7 bước: hàng đợi/gộp tin → hết hạn & user_actions → dựng ngữ cảnh (token theo khối, task được chọn kèm điểm, budget_trim, xem prompt) → cổng (p) → bộ trích (JSON thô) → Validate (lý do loại, gán người nhận, quy hạn) → sự kiện sổ cái.
- **Sổ cái** — task theo trạng thái (nháp / đã duyệt / ẩn / huỷ…), bằng chứng, đề xuất thay đổi; **Hộp duyệt** theo từng người nhận (duyệt / từ chối nháp và đề xuất).
- **Trace / Đánh giá** — trace.jsonl và báo cáo mục 8 (Wilson CI, theo tag, đường cong cổng, lỗi kèm đường đi).
- Thanh điều khiển: Lượt kế · Tự chạy · Chạy hết · Tua thời gian (để thấy luật hết hạn) · đổi `gate_mode` và mọi tham số mục 6.

Không có `OPENAI_API_KEY` thì UI và CLI chạy **bộ trích giả lập bằng luật** (ghi rõ trên giao diện). Nó chỉ dùng để xem luồng, **không** đo chất lượng; con số thật phải chạy với OpenAI.
