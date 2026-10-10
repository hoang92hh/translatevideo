# Hướng dẫn Step 03 — Translation

Step 03 dùng Google Gemini để dịch các segment được chọn ở Step 02. ID, timestamp,
`speaker_id`, `merge_parts` và source text được giữ nguyên; chỉ trường
`translated_text` được bổ sung.

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
- `Quy tắc tên riêng`:
  - `Tự động theo ngôn ngữ đích` (mặc định): Chinese → Vietnamese ưu tiên âm
    Hán–Việt; Chinese → English, Spanish và các ngôn ngữ khác ưu tiên Pinyin cho
    tên người. Địa danh ưu tiên cách gọi phổ biến trong ngôn ngữ đích khi có.
  - `Hán–Việt`: buộc dùng âm Hán–Việt.
  - `Pinyin`: buộc dùng Hanyu Pinyin không dấu thanh.
  - `Giữ nguyên chữ gốc`: không chuyển tự tên riêng.
- `Chế độ chất lượng — phân tích toàn truyện và kiểm duyệt`: bật mặc định. Khi bật,
  Step 03 gửi toàn bộ transcript để phân tích mọi speaker trước khi dịch và kiểm duyệt
  lại toàn bộ kết quả sau khi dịch. Tắt tùy chọn này để dùng luồng một lượt nhanh và
  tiết kiệm request hơn; khi đó không có hồ sơ nhân vật do AI phân tích.

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

Khi bật chế độ nhất quán toàn bộ hội thoại, Step 03 chạy ba giai đoạn:

1. **Phân tích tổng quan**: AI nhận toàn bộ transcript theo thứ tự thời gian trong một
   request, với mỗi dòng có dạng `[ID] SPEAKER_ID: câu nguồn`. AI trả một phần tử trong
   `speakers` cho từng nhân vật, cùng `addressing_rules` có hướng từ người nói tới người
   được nói với, phong cách nói, cách tự xưng/cách gọi, thuật ngữ và bảng `names` ánh xạ
   chữ gốc sang đúng một `canonical_name`. `speaker_id` giữ đúng một người xuyên suốt;
   AI không được tự khẳng định giới tính, tuổi hoặc quan hệ khi nội dung chưa đủ dữ kiện.
2. **Dịch theo batch**: mỗi batch nhận hồ sơ chung, ba segment trước/sau ở ranh giới và
   các bản dịch gần nhất. Mỗi segment còn nhận đúng hồ sơ của speaker và các quy tắc
   xưng hô đang áp dụng cho đoạn đó. Tên đã có trong bảng phải dùng nguyên
   `canonical_name`, không được tự chuyển lại trong từng batch. AI chỉ trả về ID thuộc
   batch cần dịch.
3. **Kiểm duyệt tổng thể**: AI nhận toàn bộ source và bản dịch trong một request, đối
   chiếu lại với hồ sơ speaker. AI chỉ trả về các ID thật sự cần sửa cùng loại lỗi và lý
   do; câu đúng không bị viết lại chỉ vì khác phong cách.

Ứng dụng điều phối, chia batch, kiểm tra ID và lưu kết quả; provider AI xử lý phần hiểu
ngữ nghĩa. Workflow dùng giao diện `TranslationProvider` trung lập. Gemini là provider
đang hoạt động; extension ChatGPT, Claude hoặc provider khác có thể đăng ký adapter cùng
giao diện về sau mà không thay đổi ba giai đoạn. Ứng dụng không đọc hoặc lưu token/cookie
phiên đăng nhập của extension.

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

Chế độ ba giai đoạn tạo thêm request phân tích và rà soát nên tốn thời gian và chi phí
hơn luồng một lượt. Nếu một batch ở bất kỳ giai đoạn nào lỗi, Step 03 dừng toàn bộ lần chạy. Không candidate mới nào được
đăng ký và input Step 04 thành công trước đó được giữ nguyên.

## 4. Output và lựa chọn cho Step 04

Mỗi lần chạy thành công tạo một thư mục riêng với hai file:

```text
<project>\translations\translate-YYYYMMDD-HHMMSS-xxxxxx\translated_segments.json
<project>\translations\translate-YYYYMMDD-HHMMSS-xxxxxx\dialogue_profile.json
```

`dialogue_profile.json` lưu kết quả structured JSON đã chuẩn hóa từ giai đoạn phân
tích, gồm toàn bộ `speakers`, `addressing_rules`, bảng tên, thuật ngữ, ngôn ngữ,
provider và model. Mỗi speaker có thêm `voice_description` trung lập provider để tham
chiếu tuổi giọng, cao độ, năng lượng, nhịp và sắc thái khi ánh xạ sang lựa chọn giọng ở
Step 04; Step 04 hiện chưa tự động chọn giọng từ hồ sơ.
Khi chạy chế độ nhanh, file vẫn được tạo nhưng có `analysis_performed: false` và hồ sơ
trống.

Output mới tự động trở thành input Step 04. Khối **Chọn output bản dịch** cho phép:

- Chọn bản dịch cũ và nhấn **Dùng làm input Step 4**.
- Mở file hoặc thư mục chứa file.
- Xóa riêng một candidate.

Manifest phiên bản 3 lưu thêm `dialogue_profile`, `proper_name_policy`, trạng thái
`context_consistency`, chiến lược dịch, số request của từng giai đoạn và danh sách lỗi
đã được giai đoạn kiểm duyệt sửa để đối chiếu.
Candidate phiên bản cũ vẫn được đọc bình thường.

Khi cần thay đổi prompt, payload, schema hoặc chỉ dẫn gửi provider, xem bản đồ file/hàm
trong [`translation_prompt_customization.md`](../reference/translation_prompt_customization.md).

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
