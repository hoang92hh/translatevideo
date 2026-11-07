# Web translation prompts — legacy

> File này không còn được ứng dụng sử dụng. Prompt đang hoạt động được tách riêng tại
> `reference/prompt_template/chatgpt_web/` và
> `reference/prompt_template/claude_web/`.

Workflow hiện tại dùng đúng một prompt và một request cho mỗi file đầu vào. Prompt yêu cầu provider đọc toàn bộ input, tự phân tích từng segment, dịch và tự kiểm tra. ChatGPT Web trả JSON trực tiếp trong chat để extension sao chép; Claude Web có thể trả file JSON kết quả. Khi chọn **Dịch toàn bộ segment của project trong một lượt**, input chứa toàn bộ segment; khi bỏ chọn, kích thước mỗi input lấy từ `Segments / batch`. Không còn request Analysis hoặc Review riêng.

Template đang hoạt động chỉ có một phần `## PROMPT`. `OUTPUT CONTRACT` và tên file đầu vào được quản lý ngay trong phần này; tên file kết quả chỉ còn dùng khi provider yêu cầu file.
