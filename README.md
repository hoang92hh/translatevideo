# TransLanguage

Khung ứng dụng desktop cho pipeline phiên dịch và lồng tiếng video. Phiên bản hiện tại có quản lý project, GUI theo từng step, luồng dữ liệu giữa các step và kiến trúc cấu hình provider; các tác vụ xử lý media/AI đang dùng dữ liệu mô phỏng.

## Chạy ứng dụng

Yêu cầu Python 3.11 trở lên.

```powershell
python -m pip install -e .
python main.py
```

## Pipeline trên giao diện

1. Input & Extract
2. Speech to Text
3. Translation
4. Text to Speech
5. Audio Sync
6. Build Audio
7. Render & Export

Mỗi tab nhận kết quả của tab trước. Khi một step được chạy lại hoặc dữ liệu được chỉnh sửa, kết quả của các step phía sau sẽ được đánh dấu cần cập nhật và bị loại khỏi luồng hiện tại.

## Project workspace

Khi tạo project, ứng dụng sao chép video nguồn và tạo cấu trúc:

```text
<project>/
├── project.json
├── input/
├── extracted/
├── transcripts/
├── translations/
├── generated_audio/
├── synchronized_audio/
├── subtitles/
├── temp/
└── output/
```

`project.json` lưu thông tin project, cặp ngôn ngữ và kết quả hiện tại của pipeline. Màn hình đầu tiên cho phép tạo project, mở `project.json` hoặc chọn lại project gần đây.

## Trạng thái hiện tại

- Có thể chạy từng step hoặc toàn bộ pipeline.
- Giao diện và mock handler của mỗi step nằm trong module riêng.
- Cấu hình hiển thị theo provider được chọn.
- Bảng segment giữ ID xuyên suốt pipeline và cho phép chỉnh sửa nội dung.
- Đầu ra hiện được mô phỏng bởi pipeline trong `video_translator/pipeline/mock_steps/`; chưa gọi FFmpeg, Faster Whisper, Gemini hoặc VieNeu-TTS.
