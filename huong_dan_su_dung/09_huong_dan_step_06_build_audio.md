# Hướng dẫn Step 06 — Build Audio

Step 06 ghép toàn bộ file voice đã đồng bộ ở Step 05 thành một track voice duy nhất. Step này không trộn background, audio gốc, subtitle hoặc video; các thành phần cuối cùng được lắp ghép tại Step 07.

## Đầu vào

Step 06 chỉ nhận candidate Step 05 đã hoàn chỉnh và đang được chọn làm input. Candidate còn segment lỗi không thể chạy Step 06.

Mỗi segment sử dụng:

- `synced_audio_file`: file voice đã đồng bộ.
- `adjusted_start`: vị trí bắt đầu trên timeline.
- `play_duration`: thời lượng voice được phát.

Trước khi ghép, ứng dụng kiểm tra file voice tồn tại, thời lượng hợp lệ và các voice không chồng nhau.

## Đầu ra

Mỗi lần chạy tạo một candidate riêng:

```text
<project>\built_audio\audio-<timestamp>-<id>\voice_track.wav
<project>\built_audio\audio-<timestamp>-<id>\manifest.json
```

Voice track có định dạng WAV mono PCM 48 kHz. Khoảng lặng đầu, giữa và cuối được giữ hoặc bổ sung để file dài đúng bằng video nguồn. Nếu voice cuối vượt quá thời lượng video, Step 06 dừng và yêu cầu cân lại timeline tại Step 05 thay vì cắt mất lời.

Để không vượt giới hạn độ dài dòng lệnh của Windows khi video có hàng trăm segment, Step 06 ghép theo từng lô tối đa 40 segment rồi ghép các track lô thành output cuối. Ứng dụng đo độ dài câu lệnh thực tế và tự đóng lô sớm hơn nếu đường dẫn project quá dài. Filter graph được truyền bằng `-filter_complex` để tương thích với FFmpeg 9 và các phiên bản cũ phổ biến. File tạm của các lô được tự động xóa sau khi hoàn thành hoặc khi xảy ra lỗi; timestamp và khoảng lặng vẫn giữ theo timeline Step 05.

## Nghe và chọn output

Màn hình Step 06 cho phép:

- Chuyển giữa các candidate đã tạo.
- Phát, tạm dừng, dừng và tua toàn bộ voice track.
- Điều chỉnh âm lượng nghe thử.
- Mở file hoặc thư mục output.
- Xóa một candidate.
- Chọn candidate làm input Step 07.

Candidate mới tạo thành công tự động trở thành input Step 07. Chọn một candidate khác sẽ làm kết quả Step 07 cũ mất hiệu lực.

## Phân chia trách nhiệm với Step 07

Step 06 chỉ tạo track voice. Step 07 sẽ lấy hình ảnh từ video nguồn, voice track đã chọn, background từ Step 01 nếu có và subtitle tùy chọn để render file cuối cùng.
