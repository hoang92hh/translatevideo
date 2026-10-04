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
    └── segment_XXXX/
        └── attempt-XXXXXXXX/
```

Candidate không có lỗi mới tự động trở thành input mặc định của Step 6. Có thể chọn lại candidate hoàn chỉnh cũ bằng nút **Dùng làm input Step 6**. Nếu lần chạy mới còn segment lỗi, input Step 6 đã chọn trước đó không thay đổi.

## Segment quá dài

Khi audio cần tốc độ cao hơn giới hạn đã chọn, Step 5 không cắt mất lời và không cho âm thanh chồng lên segment kế tiếp. Tool vẫn xử lý những segment còn lại rồi liệt kê riêng các segment cần sửa.

Để sửa:

1. Chọn candidate có nhãn **Cần sửa**.
2. Chọn segment trong phần **Segment cần sửa**.
3. Nghe file TTS gốc, xem thời lượng audio và thời lượng cho phép.
4. Rút gọn câu dịch nhưng giữ nguyên ý.
5. Bấm **Tạo lại giọng và đồng bộ segment này**.

Tool sử dụng lại provider, giọng, tốc độ và thiết bị của candidate Step 4 để tạo lại đúng một segment. Các segment đã thành công không bị tạo lại. Nếu audio mới vẫn quá dài, segment tiếp tục nằm trong danh sách lỗi để sửa thêm.

Khi lỗi cuối cùng được xử lý xong, candidate tự động hoàn thành và trở thành input Step 6.

## Phạm vi của bản sửa

Bản sửa tại Step 5 là sửa cục bộ:

- `original_translated_text` giữ câu dịch nhận từ Step 4.
- `translated_text` giữ câu đã sửa tại Step 5.
- Audio tạo lại và thông tin đồng bộ được lưu trong candidate Step 5.
- Candidate Step 3 và Step 4 không bị ghi đè.

Nếu sau này chọn một candidate Step 4 khác và chạy Step 5 lại từ đầu, các bản sửa cục bộ của candidate Step 5 cũ không tự động áp dụng cho lần chạy mới. Candidate cũ vẫn được lưu để nghe, so sánh hoặc chọn lại.

## Kiểm tra trước khi sang Step 6

Danh sách preview cho phép nghe cả **TTS gốc** và file **đã đồng bộ** của từng segment. Nên kiểm tra các đoạn có hệ số tăng tốc cao và các đoạn đã sử dụng khoảng trống trước khi chọn output cho Step 6.
