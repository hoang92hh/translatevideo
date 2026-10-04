# Step 05 — Audio Sync

Step 5 nhận candidate TTS đang được chọn ở Step 4 và căn thời lượng từng file giọng nói theo timestamp của segment gốc. Tool chỉ so sánh thời lượng; không so sánh nội dung hoặc dạng sóng của hai ngôn ngữ.

## Cách xử lý

Với mỗi segment, Step 5 thực hiện theo thứ tự:

1. Cắt khoảng lặng thừa ở đầu và cuối nếu bật **Cắt khoảng lặng đầu/cuối**.
2. Giữ tốc độ tự nhiên nếu audio đã nằm vừa trong khung thời gian.
3. Tận dụng khoảng trống từ cuối segment hiện tại tới đầu segment kế tiếp nếu bật **Tận dụng khoảng trống kế tiếp**.
4. Nếu audio vẫn dài, tăng tốc nhưng không vượt quá **Tốc độ tối đa**.
5. Nếu audio ngắn hơn khung gốc, chèn khoảng lặng ở cuối.

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

## Segment quá dài

Khi audio cần tốc độ cao hơn giới hạn đã chọn, Step 5 không cắt mất lời và không cho âm thanh chồng lên segment kế tiếp. Tool vẫn xử lý những segment còn lại rồi liệt kê riêng các segment cần sửa.

Để sửa:

1. Chọn candidate có nhãn **Cần sửa** và mở **Danh sách xử lý segment**.
2. Popup chỉ hiển thị các segment có `seq > 0`. Checkbox dùng riêng để chọn những câu cần Gemini rút gọn.
   Có thể dùng **Chọn tất cả** hoặc **Bỏ chọn tất cả** để thay đổi nhanh checkbox AI. Row **Đã xử lý** được giữ để theo dõi nhưng bị khóa, không được chọn AI hoặc tạo voice lại.
3. Bấm **AI chỉnh sửa các segment đã chọn**. Nếu chưa chọn checkbox nào, ứng dụng chỉ hiện thông báo và không gọi API.
4. Xem nội dung AI trả về ngay trong grid. Các row không chọn có thể sửa tay tại cột nội dung hiện tại.
5. Bấm **Tạo lại voice và đồng bộ** để xử lý toàn bộ row chưa đạt hoặc vừa được thay đổi.

Nút AI chỉ cập nhật nội dung nháp, chưa tạo audio. Nút tạo voice sử dụng lại provider, giọng, tốc độ và thiết bị của candidate Step 4, sau đó cập nhật trực tiếp segment tương ứng trong candidate Step 3, Step 4 và Step 5 hiện tại. Thao tác sửa lỗi không tạo candidate mới. Nếu audio mới vẫn quá dài, segment giữ trạng thái chưa đạt để tiếp tục sửa.

`seq` ghi nhận số lần đồng bộ không đạt của từng segment:

- Thành công ngay lần chạy Step 5 đầu tiên: `seq = 0`.
- Lỗi ở lần đầu: `seq = 1`.
- Mỗi lần xử lý lại vẫn lỗi: tăng thêm `1`.
- Khi xử lý thành công, giữ nguyên `seq`; row được đánh dấu **Đã xử lý** và vẫn còn trong popup.

Khi lỗi cuối cùng được xử lý xong, candidate tự động hoàn thành và trở thành input Step 6.

## Phạm vi của bản sửa

Bản sửa được áp dụng cho đúng chuỗi candidate nguồn của output Step 5 đang mở:

- `original_translated_text` giữ câu dịch trước lần sửa đầu tiên.
- Nội dung hiện tại được ghi vào segment tương ứng của candidate Step 3.
- Voice mới thay thế audio của segment tương ứng trong candidate Step 4.
- Kết quả đồng bộ và trạng thái được cập nhật trong candidate Step 5.
- Các candidate khác không bị thay đổi.

Candidate mới chỉ được tạo khi người dùng chạy lại toàn bộ Step 3, Step 4 hoặc Step 5 bằng nút chạy step tương ứng.

## Kiểm tra trước khi sang Step 6

Danh sách preview cho phép nghe cả **TTS gốc** và file **đã đồng bộ** của từng segment. Nên kiểm tra các đoạn có hệ số tăng tốc cao và các đoạn đã sử dụng khoảng trống trước khi chọn output cho Step 6.
