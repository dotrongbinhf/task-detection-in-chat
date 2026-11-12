# DECISIONS — các cách hiểu đã chọn khi đặc tả thiếu / mâu thuẫn

| # | Chỗ | Quyết định | Lý do |
|---|---|---|---|
| 1 | Cấu trúc | `backend/` (Python, gói `detect` đúng mục 10) + `frontend/` (Next.js, chỉ UI). Docs đặt tên `proto/`; đổi tên cho rõ vai trò. | Người dùng yêu cầu UI Next.js + Tailwind; logic AI giữ Python như docs. |
| 2 | UI ↔ engine | FastAPI `detect/server.py` **không giữ trạng thái**: client gửi `EngineState`, server chạy một lệnh (`step`/`post`/`action`/`advance`/`evaluate`) và trả state mới. | Không phải quản lý session; `--reload` không làm mất dữ liệu demo. |
| 3 | Khi không có key | `HeuristicLLM` (bộ trích giả lập bằng luật) đóng vai LLM, trả đúng schema `#n`/`T<n>`. Ghi rõ “giả lập” trên UI và báo cáo. | Docs nói regex **không** làm trigger — đây không phải trigger trong thiết kế, chỉ là chỗ thay LLM để demo offline. Không dùng số liệu của nó để kết luận. |
| 4 | `conversation_type` trong prompt | Chuyển dòng “Loại hội thoại” từ khối 1 sang đầu tin người dùng. | Giữ khối 1 giống hệt nhau giữa mọi lượt (nhóm lẫn DM) để tận dụng prompt caching. |
| 5 | Đánh số `#n` | Đánh theo **thời gian** trên mọi tin xuất hiện trong prompt (bằng chứng kèm task, chuỗi reply, cửa sổ K, TIN MỚI). | Docs ghi “theo thứ tự xuất hiện”, nhưng khối 3 đứng trước khối 4 nên đánh theo vị trí sẽ làm số trong lịch sử không tăng dần; đánh theo thời gian dễ đọc hơn, ý nghĩa giữ nguyên. |
| 6 | Reaction | Reaction có id cục bộ `#n` riêng (dòng `#9 [..] Linh 👍 (reaction vào #4)`) để LLM trích được `SPEAKER:#9`. Id gốc `<message_id>#r<n>` dùng làm bằng chứng. | Cần một id để gán người thả reaction đồng ý làm người nhận. |
| 7 | Chấm điểm task khi cắt K | Điểm “bằng chứng trong LỊCH SỬ GẦN” tính theo cửa sổ K gốc, không tính lại sau khi `reduce_k`. | Tránh việc cắt ngân sách làm đổi tập task đã chọn. |
| 8 | UPDATE không đổi trường | UPDATE chỉ có `evidence` (vd. “dc a” khi đã có người nhận) được chấp nhận và chỉ bổ sung bằng chứng, không đếm là `draft_updated`. | Docs cho phép “evidence bổ sung không đổi trường”. |
| 9 | `no_op_change` / `declined_change_suppressed` | Loại **từng change**, không loại cả op; op chỉ bị loại khi không còn change nào và không có bằng chứng mới. | Một op có thể vừa có change hợp lệ vừa có change lặp. |
| 10 | UPDATE/CANCEL lên task `declined`/`expired`/… | Loại với lý do `target_not_open`. | Task đã bị từ chối chỉ dùng để chặn tạo lại. |
| 11 | Người duyệt nhiều người nhận | Task `approved` rồi thì người nhận còn `pending` vẫn duyệt/từ chối được phần của mình (không đổi trạng thái task). | Mục 4.2: mỗi người duyệt phần của mình. |
| 12 | `user_actions` sau tin cuối | Khi hết luồng tin, áp nốt `user_actions` còn lại tại đúng thời điểm của chúng (`flush_actions`). | Docs chỉ áp “trước mỗi lượt”; không có lượt sau thì hành động cuối sẽ bị bỏ. |
| 13 | Tin do người dùng nhập trên UI | `created_at` = max(giờ mô phỏng, tin đã xử lý cuối) + khoảng chọn; xử lý ngay các lượt tới khi tin đó xong. Id `u001`… | Cho phép chèn tin vào giữa luồng dataset. |
| 14 | Thao tác duyệt trên UI | Áp tại giờ mô phỏng hiện tại; có thể chỉ thẳng `task_id`/`change_id` thay vì `message_id`. | Nút trên thẻ đã biết task cụ thể. |
| 15 | Chi phí cổng | Ước tính input cổng ≈ 1/2 ngữ cảnh bộ trích khi chưa có `usage` của cổng. | Cổng chỉ trả 1 token; chỉ để có con số định hướng. Khi chạy OpenAI nên đo thật. |
| 16 | Model (probe 12/11/2026) | Cổng: **`gpt-4o-mini`** thay cho `gpt-4.1-nano`. Bộ trích giữ `gpt-4.1-mini` (probe: logprobs ✓, json_schema strict ✓, temperature ✓). | `gpt-4.1-nano` trả 404 “has been deprecated”. `gpt-5-nano` không cho logprobs (403); `gpt-5.4-nano` là model suy luận, không trả được 1 token. `gpt-4o-mini` là model nhỏ không suy luận rẻ nhất còn trả `top_logprobs`. Thí nghiệm “bộ trích nano” ở mục 8 cần chọn lại model. |
| 17 | `OPENAI_BASE_URL=` để trống | Bỏ biến rỗng khỏi môi trường trước khi tạo client. | SDK openai đọc biến rỗng như URL → lỗi “Connection error”. |
