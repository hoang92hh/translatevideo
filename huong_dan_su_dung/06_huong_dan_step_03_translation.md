# Hướng dẫn Step 03 — Translation

Step 03 dùng Google Gemini để dịch các segment được chọn ở Step 02. ID, timestamp
và source text được giữ nguyên; chỉ trường `translated_text` được bổ sung.

## 1. Thiết lập Google API key

API key là thiết lập dùng chung cho toàn bộ TransLanguage và mọi project.

1. Mở **Cài đặt → API & Providers** từ màn hình Projects hoặc thanh trên cùng.
2. Dán Google API key vào ô được che dạng mật khẩu.
3. Nhấn **Lưu / Thay thế**.
4. Có thể nhấn **Kiểm tra kết nối** để xác nhận key truy cập được Gemini.

Key được lưu trong Windows Credential Locker theo tài khoản Windows hiện tại.
Ứng dụng không ghi key vào `project.json`, output, metadata hoặc log. Nếu không
có key đã lưu, ứng dụng lần lượt kiểm tra `GEMINI_API_KEY` và `GOOGLE_API_KEY`.

Khi chuyển sang máy hoặc tài khoản Windows khác, cần nhập lại key hoặc thiết lập
biến môi trường trên máy đó.

## 2. Cấu hình dịch

- `gemini-3.5-flash-lite`: mặc định và được khuyên dùng cho dịch thuật, ưu tiên chi phí và tốc độ.
- `gemini-3.8-flash`: mạnh hơn cho tác vụ phức tạp nhưng chi phí cao hơn.
- `gemini-2.5-flash`: model legacy; Google giới hạn quyền truy cập đối với project mới.
- `gemini-2.5-pro`: model legacy cho suy luận phức tạp, thường không cần cho subtitle và có chi phí cao.
- `Segments / batch`: số segment gửi trong mỗi request. Giá trị mặc định là 30.

Local Model hiện chưa được triển khai và bị vô hiệu hóa trên giao diện.

Danh sách model, model mặc định và model kiểm tra kết nối được khai báo tập trung tại
`video_translator/config/gemini.py`. Khi Google bổ sung hoặc ngừng cung cấp model, cập
nhật các hằng số `GEMINI_MODELS`, `GEMINI_DEFAULT_MODEL` và
`GEMINI_CONNECTION_TEST_MODEL` trong file này.

### 2.1. Giá tham khảo

Giá dưới đây là giá Standard cho mỗi 1 triệu token, tham chiếu ngày 04/10/2026:

| Model | Input | Output | Ghi chú |
|---|---:|---:|---|
| `gemini-3.5-flash-lite` | 0,30 USD | 2,50 USD | Khuyên dùng cho dịch subtitle |
| `gemini-3.8-flash` | 0,75 USD | 3,75 USD | Giá ưu đãi đến hết 31/12/2026 |
| `gemini-2.5-flash` | 0,30 USD | 2,50 USD | Quyền truy cập project mới có thể bị giới hạn |
| `gemini-2.5-pro` | 1,25 USD | 10,00 USD | Prompt không quá 200.000 token |

`Segments / batch` chỉ gom nhiều segment vào một request đồng bộ. Đây không phải
Google Batch API nên không được áp dụng mức giảm giá của Batch API. Free Tier có
quota và rate limit riêng; project đã bật billing sẽ tính phí theo token thực tế.

## 3. Quy tắc xử lý

Gemini nhận từng batch dưới dạng danh sách gồm ID, source text, timestamp bắt đầu,
timestamp kết thúc và thời lượng của từng segment, sau đó trả structured JSON.
Ứng dụng kiểm tra mỗi ID xuất hiện đúng một lần, không thiếu, không dư và không có
bản dịch trống.

Chiến lược `timing_aware_v1` coi thời lượng là mục tiêu mềm. Gemini được yêu cầu:

- Dùng ngữ cảnh của các segment liền kề nhưng vẫn trả riêng từng ID.
- Ưu tiên câu nói tự nhiên, rõ ràng và đủ súc tích để đọc trong thời lượng gốc.
- Loại bỏ từ đệm hoặc cách diễn đạt dư thừa khi có thể.
- Không bỏ hoặc làm sai ý chính, câu phủ định, ý định người nói, tên riêng, con số
  và quan hệ nguyên nhân–kết quả.
- Cho phép bản dịch dài hơn thời lượng nếu việc ép ngắn sẽ tạo câu cụt, khó hiểu
  hoặc làm mất thông tin quan trọng.

Đây không phải giới hạn cứng theo số ký tự. Step 05 vẫn là nơi đo thời lượng audio
TTS thực tế và liệt kê các segment cần sửa thêm.

Nếu một batch lỗi, Step 03 dừng toàn bộ lần chạy. Không candidate mới nào được
đăng ký và input Step 04 thành công trước đó được giữ nguyên.

## 4. Output và lựa chọn cho Step 04

Mỗi lần chạy thành công tạo một thư mục riêng:

```text
<project>\translations\translate-YYYYMMDD-HHMMSS-xxxxxx\translated_segments.json
```

Output mới tự động trở thành input Step 04. Khối **Chọn output bản dịch** cho phép:

- Chọn bản dịch cũ và nhấn **Dùng làm input Step 4**.
- Mở file hoặc thư mục chứa file.
- Xóa riêng một candidate.

Khi đổi input Step 04, kết quả từ Step 04 trở về sau được đánh dấu cần chạy lại.
Nếu xóa candidate đang dùng, ứng dụng chọn candidate hợp lệ mới nhất còn lại.

## 5. Lỗi thường gặp

### Chưa cấu hình API key

Mở **Cài đặt → API & Providers**, lưu key và kiểm tra kết nối.

### API key không hợp lệ hoặc thiếu quyền

- Lỗi `401`: key thiếu, sai hoặc hết hạn; tạo hoặc kiểm tra key trong Google AI Studio.
- Lỗi `403`: key có thể vẫn hợp lệ nhưng project không có quyền dùng model/API. Với
  project mới, chọn `gemini-3.5-flash-lite` hoặc `gemini-3.8-flash` thay cho dòng 2.5.
- Lỗi `402`: project hết credit hoặc chưa đáp ứng yêu cầu thanh toán.
- Lỗi `404`: tên model không tồn tại hoặc model không còn khả dụng.

### Quota hoặc rate limit

Chờ rồi chạy lại, giảm batch size hoặc kiểm tra quota/billing của Google project.

### Model không khả dụng

Chọn model khác. Quyền truy cập model phụ thuộc Google project và trạng thái API.

### Mạng hoặc dịch vụ tạm thời lỗi

Kiểm tra Internet và chạy lại. Output thành công cũ vẫn được giữ làm input Step 04.
