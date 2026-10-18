# Web translation prompts — legacy

> File này không còn được ứng dụng sử dụng. Prompt đang hoạt động được tách riêng tại
> `reference/prompt_template/chatgpt_web/` và
> `reference/prompt_template/claude_web/`.

Workflow hiện tại dùng đúng một prompt và một request cho mỗi file. Prompt yêu cầu provider đọc toàn bộ file, tự phân tích từng segment, dịch, tự kiểm tra và chỉ trả file JSON kết quả cuối. Khi chọn **Dịch toàn bộ segment của project trong một lượt**, file chứa toàn bộ segment; khi bỏ chọn, kích thước mỗi file lấy từ `Segments / batch`. Không còn request Analysis hoặc Review riêng.

Template đang hoạt động chỉ có một phần `## PROMPT`. `OUTPUT CONTRACT`, tên file đầu vào và tên file kết quả được quản lý ngay trong phần này.
