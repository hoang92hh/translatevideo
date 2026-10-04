# TransLanguage

Ứng dụng desktop cho pipeline phiên dịch và lồng tiếng video. Phiên bản hiện tại có quản lý project, xử lý audio thật ở Step 1, nhận dạng lời nói bằng Faster Whisper ở Step 2, dịch bằng Google Gemini ở Step 3, tạo giọng nói thật ở Step 4 và đồng bộ thời lượng bằng FFmpeg ở Step 5. Step 6–7 vẫn dùng dữ liệu mô phỏng.

## Chạy ứng dụng

Yêu cầu Python 3.11 trở lên.

Chọn một profile xử lý MDX phù hợp với máy. `Auto` trong ứng dụng sẽ chỉ dùng
GPU khi cả PyTorch CUDA và ONNX CUDA Execution Provider hoạt động:

```powershell
# Máy chỉ dùng CPU
python -m pip install -e ".[cpu]"

# Máy có NVIDIA GPU
python -m pip install -e ".[cuda]"

python main.py
```

Profile CUDA cài ONNX Runtime GPU cùng cuBLAS 12.6 và cuDNN 9.6 trong `.venv`.
Nếu `torch.cuda.is_available()` vẫn trả về
`False`, cài bản PyTorch CUDA phù hợp theo hướng dẫn chính thức của PyTorch.
Không cài đồng thời `onnxruntime`, `onnxruntime-gpu` và
`onnxruntime-directml` trong cùng environment. Khi đổi profile trên một máy đã
cài dependency, nên dùng virtual environment mới để tránh giữ lại package ONNX
Runtime của profile cũ.

Profile `[cuda]` hiện ưu tiên khả năng tương thích rộng và đã được xác minh trên
GTX 1060/Pascal. GTX 16xx thuộc Turing, không cùng kiến trúc với GTX 10xx; vì vậy
không nên chọn runtime chỉ dựa vào tên `GTX` hoặc `RTX`. Bảng kiến trúc và hướng
dẫn chọn phiên bản nằm trong
[`03_cai_dat_va_chay_tool_tren_may_khac.md`](huong_dan_su_dung/03_cai_dat_va_chay_tool_tren_may_khac.md).

Với GPU NVIDIA Pascal như GTX 10xx, profile `cuda` khóa ONNX Runtime ở dòng
1.20.x để dùng CUDA 12.x. Worker chỉ báo `NVIDIA GPU (CUDA)` sau khi session của
model thực sự kích hoạt `CUDAExecutionProvider`; việc provider chỉ xuất hiện
trong danh sách khả dụng là chưa đủ.

Faster Whisper dùng CTranslate2 độc lập với PyTorch và ONNX Runtime. Step 2 chạy
được bằng CPU sau khi cài dependency cơ bản. Profile `[cuda]` cài cuBLAS/cuDNN
vào `.venv`; worker tự đăng ký thư mục DLL nên không yêu cầu sửa `PATH` hệ thống.
Chế độ `Auto` chỉ chọn GPU khi CTranslate2 nhìn thấy GPU và tải được đầy đủ
runtime; nếu không sẽ chọn CPU. Trong lúc chạy, Auto chỉ fallback CPU với lỗi
CUDA hoặc thiếu VRAM; lựa chọn `GPU` sẽ báo lỗi thay vì tự đổi thiết bị. Dự án
khóa PyAV dưới phiên bản 19 để tương thích Faster Whisper 1.2.1.

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
├── audio_separation/
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
- Step 1 dùng FFmpeg để tạo Original Mix và có thể dùng MDX qua `audio-separator` để tạo `Voice` + `Background`.
- MDX hỗ trợ `Auto`, `CPU` và `NVIDIA GPU (CUDA)`. Mỗi lần tách chạy trong một worker process riêng để có thể đổi thiết bị mà không cần khởi động lại ứng dụng. `Auto` chỉ chọn CUDA khi cả PyTorch và ONNX Runtime xác nhận backend CUDA hoạt động, nếu không sẽ dùng CPU.
- Mỗi lần chạy MDX tạo một candidate riêng trong `audio_separation/`; output thành công mới nhất tự trở thành input cho Step 2. Người dùng vẫn có thể nghe, so sánh và chọn lại candidate cũ bằng một thao tác duy nhất.
- Demucs và RoFormer đã có vị trí trong danh sách provider nhưng được đánh dấu chưa triển khai.
- Có thể tự tìm FFmpeg trong `PATH` hoặc chọn trực tiếp `ffmpeg.exe`; FFprobe trong cùng thư mục được dùng để tính tiến độ và thời lượng.
- Trạng thái project lưu output được người dùng chọn gần nhất ở mỗi step. Khi pipeline đầy đủ được triển khai, đó là input duy nhất được chuyển sang step kế tiếp.
- Khi chạy lại Step 1, audio player được giải phóng trước; MDX tái sử dụng Original Mix có cấu hình phù hợp thay vì ghi đè file đang nghe.
- Khi chạy MDX, ứng dụng tự đưa thư mục chứa FFmpeg/FFprobe đã chọn vào môi trường của provider; không bắt buộc cấu hình PATH toàn hệ thống.
- Step 2 dùng Faster Whisper để nhận dạng audio đã chọn, giữ timestamp và segment ID. Mỗi lần chạy thành công tạo một candidate riêng tại `transcripts/<candidate-id>/transcript.json`; candidate mới nhất tự trở thành input cho Step 3.
- Nếu Step 1–4 gặp lỗi không tạo được output hợp lệ, candidate chưa hoàn chỉnh không được đăng ký và input thành công trước đó được giữ nguyên. Riêng Step 5 giữ candidate `needs_edit` để người dùng sửa từng segment, nhưng không chọn candidate đó làm input Step 6 cho tới khi hết lỗi.
- Step 3 dùng Google Gemini và structured output để giữ nguyên ID của mọi segment. Request gửi kèm timestamp/thời lượng để model ưu tiên câu nói súc tích theo giới hạn mềm, nhưng không được hy sinh ý chính hoặc độ rõ ràng. Mỗi lần dịch thành công tạo `translations/<candidate-id>/translated_segments.json`; output được tạo hoặc chọn gần nhất trở thành input Step 4.
- Google API key được quản lý tập trung tại **Cài đặt → API & Providers** và lưu trong Windows Credential Locker. Mọi project dùng chung credential; API key không được ghi vào `project.json` hoặc artifact.
- Faster Whisper hỗ trợ `small`, `medium`, `large-v3`, VAD và ba chế độ `Auto`, `CPU`, `GPU`. Model được lưu tại cache riêng của TransLanguage trong `%LOCALAPPDATA%`.
- Profile CUDA khóa cuBLAS `12.6.4.1` và cuDNN `9.6.0.74` để tiếp tục hỗ trợ GPU Pascal như GTX 1060.
- Lỗi provider được hiển thị theo nhóm nguyên nhân, kèm hướng xử lý và phần chi tiết kỹ thuật có thể mở rộng.
- Step 4 hỗ trợ VieNeu-TTS local cho tiếng Việt, MeloTTS + OpenVoice V2 local cho English/Spanish/Chinese/Japanese/Korean và Edge TTS online. Mỗi lần chạy thành công tạo một candidate riêng trong `generated_audio/`; candidate mới nhất tự động là input Step 5, còn lỗi không thay đổi input trước đó.
- `Auto` ở provider local chọn GPU khi runtime CUDA của chính provider khả dụng, nếu không chọn CPU. `CPU` và `GPU` tuân thủ đúng lựa chọn; chế độ `GPU` báo lỗi thay vì fallback CPU.
- XTTS-v2 chỉ nằm trong danh sách dưới dạng chưa triển khai và có ghi chú chỉ phi thương mại theo Coqui Public Model License.
- Step 5 cắt khoảng lặng thừa, tận dụng khoảng trống trước segment kế tiếp, tăng tốc trong giới hạn người dùng chọn và chèn khoảng lặng khi audio ngắn hơn timestamp gốc. Mỗi lần chạy tạo candidate riêng trong `synchronized_audio/`.
- Segment không thể đặt vừa trong giới hạn tốc độ được đưa vào popup xử lý của Step 5. Checkbox chỉ chọn các câu cần Gemini rút gọn; các câu còn lại có thể sửa tay trong grid. Nút tạo voice cập nhật trực tiếp segment tương ứng trong candidate Step 3, Step 4 và Step 5 hiện tại, không tạo candidate mới. Candidate Step 5 chỉ trở thành input Step 6 sau khi hết lỗi.
- Local Model ở Step 3 được đánh dấu chưa triển khai. Step 6–7 hiện vẫn được mô phỏng.

Hướng dẫn chi tiết:

- [`huong_dan_su_dung/07_huong_dan_step_04_text_to_speech.md`](huong_dan_su_dung/07_huong_dan_step_04_text_to_speech.md)
- [`huong_dan_su_dung/08_huong_dan_step_05_audio_sync.md`](huong_dan_su_dung/08_huong_dan_step_05_audio_sync.md)
