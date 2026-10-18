# Đóng gói và cài đặt TransLanguage Application

## Cấu trúc bản phát hành

```text
TransLanguage/
├── TransLanguage.exe
├── Setup-TransLanguage.exe
├── app/
├── environment/
├── downloads/
├── models/
├── reference/
├── extensions/
├── projects/
├── tools/
└── scripts/
```

- `app`: mã ứng dụng, không chứa môi trường Python của máy build.
- `environment`: runtime được tạo riêng trên từng máy. `main`, `piper` và
  `melo` không dùng chung dependency.
- `downloads`: cache tải package và model trong quá trình thiết lập.
- `models`: model Faster Whisper, Audio Separator, diarization, Piper và
  OpenVoice.
- `reference`: prompt template và file giọng tham chiếu.
- `extensions`: Chrome extension dùng cho ChatGPT Web và Claude Web.
- `projects`: project do người dùng tạo.
- `tools`: công cụ portable như FFmpeg/FFprobe.

## Tạo bản phát hành

Trên máy phát triển, cài công cụ build một lần:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[build]"
```

Tạo bản portable kèm model local và FFmpeg hiện có trên máy:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1 `
  -IncludeLocalModels `
  -IncludePortableFFmpeg
```

Bản phát hành được tạo tại `release\TransLanguage`. Script không sao chép
`.venv`, `.runtimes` hoặc dữ liệu trong `projects`.

## Cài trên máy người dùng

1. Giải nén toàn bộ thư mục `TransLanguage`.
2. Chạy `Setup-TransLanguage.exe`, hoặc chạy `TransLanguage.exe` để ứng dụng
   đề nghị thiết lập khi chưa có runtime.
3. Setup tự chọn `CUDA12` khi phát hiện GPU NVIDIA được hỗ trợ; máy không có
   NVIDIA dùng `CPU`.
4. Sau khi hoàn tất, chạy `TransLanguage.exe`.

Setup có thể tự cài Python 3.13 bằng Winget khi máy chưa có Python. Người dùng
không cần tự tạo virtual environment, cài CUDA Toolkit, cuBLAS hoặc cuDNN.
NVIDIA Driver vẫn phải được cài trước nếu muốn dùng GPU.

Profile CUDA cài dependency theo thứ tự cố định để `onnxruntime-gpu` là backend
được nạp cuối cùng, đồng thời giữ metadata `onnxruntime` mà Faster Whisper yêu
cầu. Không nâng cấp hoặc cài lại riêng một trong hai package này trong runtime
đã được setup quản lý.

RTX 50xx hiện được chuyển về profile CPU vì profile CUDA dành cho Blackwell
chưa được xác minh. AMD và Intel GPU cũng dùng profile CPU.

## Runtime TTS tùy chọn

Có thể cài từ PowerShell trong thư mục ứng dụng:

```powershell
# Piper
powershell -ExecutionPolicy Bypass -File scripts\setup_application.ps1 `
  -Profile Auto -IncludePiper

# Melo/OpenVoice
powershell -ExecutionPolicy Bypass -File scripts\setup_application.ps1 `
  -Profile Auto -IncludeMelo
```

Speaker diarization vẫn được giữ để Step 3 nhận biết người nói và dịch cách
xưng hô chính xác. Với VieNeu và Melo/OpenVoice, Step 4 hỗ trợ bốn cấu hình
giọng tham chiếu cho `SPEAKER_00`–`SPEAKER_03` hoặc một giọng dùng chung.
