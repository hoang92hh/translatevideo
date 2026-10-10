# Step 05 — Audio Sync

Step 5 nhận candidate TTS đang được chọn ở Step 4 và căn thời lượng từng file giọng nói theo timestamp của segment gốc. Tool chỉ so sánh thời lượng; không so sánh nội dung hoặc dạng sóng của hai ngôn ngữ.

## Cách xử lý

Với mỗi segment, Step 5 thực hiện theo thứ tự:

1. Mặc định giữ nguyên WAV từ Step 4. Tùy chọn **Tự động cắt silence dư khi audio vượt khung** mặc định tắt.
2. Khi bật tùy chọn này, Step 5 chỉ phân tích silence nếu WAV dài hơn `allowed_duration`. Silence phải nằm liên tục ở đầu hoặc cuối và dài ít nhất 0,25 giây; tool luôn giữ lại 0,12 giây đệm an toàn, chỉ cắt lượng cần thiết để đưa audio vào khung. Khoảng nghỉ bên trong câu luôn được giữ nguyên.
3. Giữ tốc độ tự nhiên nếu audio đã nằm vừa trong khung thời gian.
4. Tận dụng khoảng trống từ cuối segment hiện tại tới đầu segment kế tiếp nếu bật **Tận dụng khoảng trống kế tiếp**.
5. Nếu audio vẫn dài, tăng tốc nhưng không vượt quá **Tốc độ tối đa**.
6. Nếu audio ngắn hơn khung gốc, chèn khoảng lặng ở cuối.

Manifest ghi riêng `detected_leading_silence`, `detected_trailing_silence`, `trimmed_leading_silence`, `trimmed_trailing_silence` và `silence_trim_decision` cho từng segment để có thể kiểm tra quyết định của Step 5.

Luồng chạy Step 5 thông thường không tự động cân lại các hàng xóm. Nếu một segment vẫn quá dài, có thể dùng nút **Vay thời gian lân cận** riêng trong popup sửa lỗi. Mỗi segment lỗi B được thử với C trước; chỉ khi B+C chưa đủ mới thử A+B+C, hoặc A+B nếu không có C. Mọi voice trong nhóm được tạo lại từ audio Step 4 với cùng **Tốc độ tối đa**, phát lần lượt và không chồng nhau. Mỗi segment chỉ được lệch tối đa 0,5 giây; khoảng nghỉ lớn hơn 1 giây là ranh giới không vay qua.

Output được chuẩn hóa thành WAV mono PCM 48 kHz.

## Candidate và input Step 6

Mỗi lần chạy tạo một thư mục độc lập:

```text
synchronized_audio/<sync-candidate-id>/
├── manifest.json
├── segments/
│   ├── segment_0001.wav
│   └── ...
└── repairs/
    └── batch-XXXXXXXX/
        ├── segment_XXXX.wav
        └── ...
```

Candidate không có lỗi mới tự động trở thành input mặc định của Step 6. Có thể chọn lại candidate hoàn chỉnh cũ bằng nút **Dùng làm input Step 6**. Nếu lần chạy mới còn segment lỗi, input Step 6 đã chọn trước đó không thay đổi.

Khi chạy Step 5 từ một candidate Step 4 mới, segment có `end == start` được ghi vào manifest ở trạng thái sẵn sàng với `play_duration = 0`; Step 5 không yêu cầu WAV và không thực hiện đồng bộ audio cho segment đó.

## Segment quá dài

Khi audio cần tốc độ cao hơn giới hạn đã chọn, Step 5 không cắt mất lời và không cho âm thanh chồng lên segment kế tiếp. Tool vẫn xử lý những segment còn lại rồi liệt kê riêng các segment cần sửa.

Để sửa:

1. Chọn candidate có nhãn **Cần sửa** và mở **Danh sách xử lý segment**.
2. Popup chỉ lấy các segment đang lỗi (`status` khác `ready`) cùng segment liền trước và liền sau của từng lỗi. Các segment đã xử lý không còn được giữ lại chỉ vì có `seq > 0`; row lân cận vẫn xuất hiện kể cả khi đang không lỗi.
   Tất cả row đều có thể sửa nội dung và chọn checkbox. Có thể dùng **Chọn tất cả** hoặc **Bỏ chọn tất cả** để thay đổi nhanh lựa chọn.
3. Bấm **AI chỉnh sửa các segment đã chọn**. Nếu chưa chọn checkbox nào, ứng dụng chỉ hiện thông báo và không gọi API.
4. Xem nội dung AI trả về ngay trong grid. Checkbox của các row AI vừa sửa được giữ nguyên; các row khác có thể sửa tay tại cột nội dung hiện tại.
5. Bấm **Tạo lại voice và đồng bộ** để xử lý đúng các row được chọn, kể cả segment đang lỗi, đã xử lý hoặc segment lân cận bình thường. Nếu chưa chọn checkbox, ứng dụng không gọi TTS.
6. Với các row vẫn quá dài và không thể rút gọn thêm, bấm **Vay thời gian lân cận**. Nút này xử lý toàn bộ row chưa đạt trong danh sách, không phụ thuộc checkbox và không gọi AI/TTS.

Popup cho phép sửa trực tiếp `start`, `end`, đổi `speaker_id` bằng danh sách ở cột **Speaker** và sửa bản dịch ở cột **Nội dung hiện tại**. Nút AI chỉ cập nhật nội dung nháp, chưa tạo audio. Nút tạo voice sử dụng lại provider, tốc độ, thiết bị và đúng cấu hình giọng theo speaker đã chọn, sau đó cập nhật trực tiếp thời gian/speaker ở Step 2, thời gian/speaker/nội dung ở Step 3, thời gian/audio ở Step 4 và kết quả đồng bộ Step 5. `id`, số lượng segment và `merge_parts` không thay đổi. `End` phải lớn hơn hoặc bằng `Start`. Segment có `End > Start` bắt buộc phải có speaker và nội dung. Khi `End == Start`, nội dung được phép để trống; segment vẫn được giữ trong manifest nhưng không tạo lại voice và có thời lượng phát bằng 0. Nếu một segment lân cận vốn bình thường có voice mới quá dài, nó trở thành segment lỗi và `seq` tăng. Nút vay thời gian render lại cả segment lỗi B và segment lân cận cần dùng (B+C, A+B+C hoặc A+B) từ voice mới nhất ở Step 4. Thao tác sửa lỗi không tạo candidate mới.

Ngoài danh sách lỗi, nút **Sửa voice/speaker segment khác** mở cùng popup với toàn bộ segment. Nhập từ khóa trong nội dung bản dịch rồi bấm **Tìm kiếm**; kết quả luôn gồm segment khớp cùng segment liền trước và liền sau. Có thể tìm nhiều lần mà không mất checkbox, speaker hoặc nội dung đang sửa. Nhập từ khóa rỗng để trở lại danh sách mặc định. **Chọn tất cả** chỉ chọn các hàng đang hiển thị, trong khi những checkbox đã chọn ở kết quả trước vẫn được giữ và được tính trong dòng tổng kết.

Nếu còn bất kỳ row nào ở trạng thái **Chờ tạo voice**, ứng dụng yêu cầu tạo voice trước khi vay thời gian để tránh sử dụng nhầm voice cũ.

`seq` ghi nhận số lần đồng bộ không đạt của từng segment:

- Thành công ngay lần chạy Step 5 đầu tiên: `seq = 0`.
- Lỗi ở lần đầu: `seq = 1`.
- Mỗi lần xử lý lại vẫn lỗi: tăng thêm `1`.
- Khi xử lý thành công, giữ nguyên `seq`; row được đánh dấu **Đã xử lý** và vẫn còn trong popup.

Khi lỗi cuối cùng được xử lý xong, candidate tự động hoàn thành và trở thành input Step 6.

Các trường `adjusted_start`, `adjusted_end`, `play_duration`, `borrowed_before`, `borrowed_after` và `sync_strategy` trong manifest mô tả lịch phát đã cân. Step 6 chỉ ghép audio theo lịch này và kiểm tra lại điều kiện không chồng voice.

## Phạm vi của bản sửa

Bản sửa được áp dụng cho đúng chuỗi candidate nguồn của output Step 5 đang mở:

- `original_translated_text` giữ câu dịch trước lần sửa đầu tiên.
- Nội dung hiện tại được ghi vào segment tương ứng của candidate Step 3.
- `start` và `end` được ghi vào segment tương ứng trong chuỗi candidate Step 2–5.
- Voice mới thay thế audio của segment tương ứng trong candidate Step 4.
- Kết quả đồng bộ và trạng thái được cập nhật trong candidate Step 5.
- Các candidate khác không bị thay đổi.

Candidate mới chỉ được tạo khi người dùng chạy lại toàn bộ Step 3, Step 4 hoặc Step 5 bằng nút chạy step tương ứng.

## Kiểm tra trước khi sang Step 6

Step 5 hiển thị trạng thái đồng bộ và danh sách segment cần xử lý nhưng không phát lại từng file audio. Có thể nghe voice ở Step 4; sau khi Step 6 ghép xong, nghe toàn bộ voice track hoàn chỉnh tại Step 6 trước khi chọn output cho Step 7.
