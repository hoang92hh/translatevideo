# Hướng dẫn Step 07 — Render & Export

Step 07 lắp ghép các thành phần cuối cùng thành video MP4 hoàn chỉnh. Mỗi lần chạy tạo một output riêng, không ghi đè các lần render trước.

## Thành phần đầu vào

Hai thành phần luôn bắt buộc và không thể tắt:

- **Hình ảnh video gốc**: Step 07 chỉ lấy luồng hình ảnh, không lấy audio của video nguồn.
- **Voice mới từ Step 06**: candidate đang được chọn làm input Step 07.

Các thành phần tùy chọn:

- **Âm thanh nền gốc (Background)**: stem `Background` được tách ở Step 01. Thành phần này được chọn mặc định khi file còn tồn tại và có thanh điều chỉnh âm lượng từ 0% đến 100%.
- **Subtitle SRT**: tạo file phụ đề rời từ bản dịch. Với segment đã gộp, nội dung
  được chia offline theo tỷ lệ thời lượng trong `merge_parts` và đặt lên timeline
  voice đã căn chỉnh của Step 05.
- **Burn subtitle vào video**: ghi trực tiếp subtitle lên hình ảnh; tùy chọn này chỉ bật được khi đã chọn tạo subtitle SRT.

`Original Mix` không được sử dụng trong Step 07. Nếu candidate Step 01 không có file `Background`, checkbox Background bị khóa, ứng dụng hiển thị cảnh báo và vẫn cho phép render hình ảnh cùng voice mới.

## Cấu hình mặc định

Khi mở Step 07:

- Hình ảnh video gốc: bật, bắt buộc.
- Voice mới: bật, bắt buộc.
- Background: bật ở mức 100% nếu file tồn tại.
- Subtitle SRT: tắt.
- Burn subtitle: tắt.

## Đầu ra

Mỗi lần render thành công tạo một candidate riêng:

```text
<project>\output\render-<timestamp>-<id>\translated_video.mp4
<project>\output\render-<timestamp>-<id>\translated.srt   # khi chọn subtitle
<project>\output\render-<timestamp>-<id>\manifest.json
```

Video dùng H.264, audio dùng AAC 48 kHz. Track audio được bù hoặc cắt theo đúng thời lượng video nguồn. Audio gốc nằm trong video nguồn không được map sang output.

Việc chia text dùng tỷ lệ thời lượng làm mục tiêu và ưu tiên ranh giới từ. Vì vậy
subtitle không cắt giữa một từ, nhưng số ký tự thực tế của mỗi cue có thể lệch nhẹ
so với tỷ lệ để giữ khả năng đọc. Đây là xử lý local, không gọi thêm API.

Danh sách **Các video đã render** cho phép mở video, mở subtitle, mở thư mục hoặc xóa toàn bộ candidate. Output mới nhất được đánh dấu để dễ nhận biết. Nếu lần render mới gặp lỗi, output thành công gần nhất vẫn được giữ lại.

## Lỗi thường gặp

- **Thiếu voice track**: quay lại Step 06 và chọn một candidate còn đủ file WAV cùng manifest.
- **Không có Background**: có thể tiếp tục render chỉ với voice mới; không cần dùng `Original Mix` thay thế.
- **Không burn được subtitle**: kiểm tra bản FFmpeg có hỗ trợ filter `subtitles`/libass và xem phần chi tiết kỹ thuật của thông báo lỗi.
- **Không tìm thấy FFmpeg**: bảo đảm `ffmpeg` và `ffprobe` có trong `PATH` trước khi chạy ứng dụng.
