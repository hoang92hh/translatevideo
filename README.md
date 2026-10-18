# TransLanguage

Ứng dụng desktop cho pipeline phiên dịch và lồng tiếng video. Phiên bản hiện tại có quản lý project và xử lý thật toàn bộ pipeline: chuẩn bị audio ở Step 1, nhận dạng lời nói bằng Faster Whisper ở Step 2, dịch bằng Google Gemini ở Step 3, tạo giọng nói ở Step 4, đồng bộ thời lượng bằng FFmpeg ở Step 5, ghép voice timeline ở Step 6 và render video hoàn chỉnh ở Step 7.

## Bản application Windows

Dự án có bộ build portable với `TransLanguage.exe` và
`Setup-TransLanguage.exe`. Runtime CPU/CUDA được cài riêng trên từng máy vào
`environment`; model, reference, extension và project nằm trong các thư mục
độc lập. Xem [hướng dẫn đóng gói và cài đặt](huong_dan_su_dung/13_dong_goi_va_cai_dat_application.md).

Speaker diarization được bật mặc định để giữ `SPEAKER_00`, `SPEAKER_01`… cho
phân tích ngữ cảnh và xưng hô ở Step 3. Với VieNeu và Melo/OpenVoice, Step 4
hỗ trợ bốn cấu hình giọng tham chiếu cho `SPEAKER_00`–`SPEAKER_03` hoặc một
giọng dùng chung cho cả bốn.

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

Ghi chú kỹ thuật về vị trí prompt, payload, schema và chỉ dẫn gửi AI của Step 03/05
nằm tại [`translation_prompt_customization.md`](reference/translation_prompt_customization.md).

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

Khi tạo project, người dùng chọn một thư mục input và đánh dấu các video nằm trực tiếp
trong thư mục đó. Ứng dụng không sao chép video nguồn. Danh tính video là đường dẫn
tuyệt đối cộng tên file; trạng thái `pending`, `processing`, `completed` hoặc `failed`
được lưu trong `.translanguage-videos.json` ngay tại thư mục input.

```text
<project>/
├── project.json
├── <video-name>/
│   ├── video.json
│   ├── extracted/
│   ├── audio_separation/
│   ├── transcripts/
│   ├── translations/
│   ├── generated_audio/
│   ├── synchronized_audio/
│   ├── built_audio/
│   ├── subtitles/
│   └── temp/
└── output/
    ├── <video-name>/
    │   ├── <render-candidate-1>/
    │   └── <render-candidate-2>/
    └── merged/
```

`project.json` lưu danh sách video, cấu hình batch và trạng thái cấp project. Mỗi
`video.json` giữ pipeline/candidate riêng của một video. Popup **Thiết lập project** gom
toàn bộ cấu hình Step 1–7 vào một màn hình lớn và được mở ngay trong luồng tạo project.
Các tab Step 1–7 dùng để kiểm tra output của từng bước và không còn nút chạy riêng. Nút
**Videos** hiển thị hàng đợi và bắt đầu xử lý tuần tự. Video lỗi được đánh dấu `failed` rồi
bỏ qua để chạy video tiếp theo. Step 4 tạm thời dùng một cấu hình giọng đọc chung cho mọi
speaker và video.

Batch dùng trạng thái xử lý riêng và không tự đổi video đang được xem trên các tab Step.
Select box **Video đang xem** chọn video cần kiểm tra output. Mỗi video lưu Step đang chạy,
Step lỗi và checkpoint; lần **Chạy batch** tiếp theo bỏ qua video đã hoàn thành, còn video
chưa hoàn thành hoặc bị lỗi sẽ tiếp tục từ Step sớm nhất cần xử lý lại.

Nút **Outputs** hiển thị một row cho mỗi video. Select box trong row chọn phiên bản
render cần xem; checkbox chọn các phiên bản cần ghép. Các row được ghép theo thứ tự
hiển thị và tạo một MP4 mới trong `output/merged/` hoặc vị trí do người dùng chọn.

## Trạng thái hiện tại

- Pipeline được chạy tập trung bằng **Chạy batch**. Mỗi video giữ checkpoint riêng; nếu một Step lỗi, video được đánh dấu thất bại, batch chuyển sang video tiếp theo và lần chạy sau tiếp tục từ Step lỗi.
- Step đang chạy sẽ hoàn tất hoặc trả lỗi; giao diện không cung cấp thao tác dừng thủ công giữa chừng. Nhãn trên thanh đầu trang hiển thị vị trí của tab hiện tại, hoặc step liền trước nếu tab hiện tại chưa có kết quả.
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
- Step 2 dùng Faster Whisper với word timestamp và pyannote Community-1 chạy local để nhận dạng lời nói, gắn `speaker_id`, rồi làm mượt nhiễu ranh giới trước khi tạo segment. Một lần đổi speaker được giữ khi kéo dài ít nhất `0,8` giây hoặc chứa ít nhất 2 từ; khoảng `SPEAKER_UNKNOWN` và speaker đơn lẻ ngắn hơn cả hai ngưỡng được gán về speaker ổn định gần nhất. Tùy chọn **Gộp segment liền kề cùng speaker** được bật mặc định; khi bật, các đoạn cùng speaker có độ lệch nhỏ hơn `0,01` giây được gộp, còn khi tắt thì từng đoạn nguyên tử được giữ riêng. Mọi segment đều lưu `merge_parts` gồm ID/timestamp gốc và tỷ lệ thời lượng; transcript cũng lưu `diarization_turns` thô để đối chiếu. Mỗi lần chạy thành công tạo một candidate riêng tại `transcripts/<candidate-id>/transcript.json`.
- Nếu Step 1–4 gặp lỗi không tạo được output hợp lệ, candidate chưa hoàn chỉnh không được đăng ký và input thành công trước đó được giữ nguyên. Riêng Step 5 giữ candidate `needs_edit` để người dùng sửa từng segment, nhưng không chọn candidate đó làm input Step 6 cho tới khi hết lỗi.
- Step 3 dùng workflow dịch độc lập provider. Gemini giữ tùy chọn chất lượng ba giai đoạn: phân tích toàn truyện, dịch theo batch và kiểm duyệt kết quả. Với ChatGPT Web và Claude Web, checkbox **Dịch toàn bộ segment của project trong một lượt** được bật mặc định: toàn bộ segment được gửi trong một file JSON và nhận một file kết quả; khi bỏ chọn, ứng dụng chia tuần tự theo `Segments / batch`. Mỗi file web được AI đọc trọn vẹn để phân tích ngữ cảnh trước khi dịch, được extension tải xuống và được ứng dụng xác thực đủ ID trước khi gửi lượt kế tiếp. Quy tắc tên riêng mặc định tự thích ứng theo cặp ngôn ngữ: Chinese → Vietnamese ưu tiên Hán–Việt; Chinese → ngôn ngữ khác ưu tiên Pinyin; người dùng có thể ép Hán–Việt, Pinyin hoặc giữ chữ gốc. Step 3 có giao diện tab cho Google Gemini API, ChatGPT Web và Claude Web; hai web provider dùng Chrome extension qua bridge localhost, còn model được chọn trực tiếp trên trang. Structured output giữ nguyên ID, timestamp, `speaker_id` và `merge_parts`; mỗi lần thành công tạo `translations/<candidate-id>/translated_segments.json` làm input Step 4 và `dialogue_profile.json` để tham chiếu vai trò/giọng cho từng speaker.
- Google API key được quản lý tập trung tại **Cài đặt → API & Providers** và lưu trong Windows Credential Locker. Speaker diarization chỉ đọc model local được chọn trong cùng màn hình; Step 2 không cần Hugging Face token và không gọi mạng khi xử lý.
- Faster Whisper hỗ trợ `small`, `medium`, `large-v3`, VAD và ba chế độ `Auto`, `CPU`, `GPU`. Model được lưu tại cache riêng của TransLanguage trong `%LOCALAPPDATA%`.
- Profile CUDA khóa cuBLAS `12.6.4.1` và cuDNN `9.6.0.74` để tiếp tục hỗ trợ GPU Pascal như GTX 1060.
- Lỗi provider được hiển thị theo nhóm nguyên nhân, kèm hướng xử lý và phần chi tiết kỹ thuật có thể mở rộng.
- Step 4 hỗ trợ VieNeu-TTS local cho tiếng Việt, Piper TTS local CPU/ONNX, MeloTTS + OpenVoice V2 local cho English/Spanish/Chinese/Japanese/Korean và Edge TTS online. Với VieNeu và Melo/OpenVoice, `SPEAKER_00`–`SPEAKER_03` có cấu hình giọng tham chiếu riêng; checkbox **Dùng cùng một giọng cho SPEAKER_00–03** cho phép dùng cấu hình `SPEAKER_00` cho cả bốn. `SPEAKER_04` trở lên và `SPEAKER_UNKNOWN` dùng cấu hình `SPEAKER_03` làm fallback. Piper dùng cặp voice model `.onnx` + `.onnx.json`; Edge dùng voice preset. Ánh xạ được giữ khi Step 5 tạo lại voice. Mỗi lần chạy thành công tạo một candidate riêng trong `generated_audio/`; candidate mới nhất tự động là input Step 5, còn lỗi không thay đổi input trước đó.
- `Auto` ở provider local chọn GPU khi runtime CUDA của chính provider khả dụng, nếu không chọn CPU. `CPU` và `GPU` tuân thủ đúng lựa chọn; chế độ `GPU` báo lỗi thay vì fallback CPU.
- XTTS-v2 chỉ nằm trong danh sách dưới dạng chưa triển khai và có ghi chú chỉ phi thương mại theo Coqui Public Model License.
- Step 5 cắt khoảng lặng thừa, tận dụng khoảng trống trước segment kế tiếp, tăng tốc trong giới hạn người dùng chọn và chèn khoảng lặng khi audio ngắn hơn timestamp gốc. Trong **Thiết lập project → Step 05**, tùy chọn **Auto Extend Video to Match TTS** có thể lập kế hoạch kéo dài cục bộ bằng `Freeze Frame`, `Slow Motion` hoặc `Hybrid`; chỉ đoạn có voice vượt khung bị biến đổi, còn các đoạn sau chỉ được dịch timestamp. Mỗi lần chạy tạo candidate riêng trong `synchronized_audio/`.
- Với segment vẫn quá dài, popup sửa lỗi có nút **Vay thời gian lân cận** riêng. Nút này ưu tiên cân B+C; chỉ dùng A+B+C (hoặc A+B khi không có C) nếu thời gian phía sau chưa đủ. Các voice trong nhóm dùng cùng tốc độ tối đa đã cấu hình, giữ nguyên thứ tự, không chồng nhau, lệch tối đa 0,5 giây và không vay qua khoảng nghỉ lớn hơn 1 giây.
- Segment không thể đặt vừa trong giới hạn tốc độ cùng segment liền trước/sau được đưa vào popup xử lý của Step 5. Nút **Sửa voice/speaker segment khác** mở cùng popup với toàn bộ segment và cho tìm trong nội dung bản dịch; mỗi kết quả kèm segment liền trước/sau. Checkbox, speaker và nội dung nháp được giữ qua nhiều lần tìm. Mọi row đều có thể đổi speaker, sửa bản dịch hoặc chọn để Gemini chỉnh sửa. Checkbox đồng thời quyết định chính xác các row được tạo lại voice; kết quả cập nhật speaker/nội dung/audio trực tiếp trong chuỗi candidate Step 2–5 hiện tại mà không đổi `id`, timestamp hoặc `merge_parts`. Candidate Step 5 chỉ trở thành input Step 6 sau khi hết lỗi.
- Step 6 đọc `adjusted_start`/`play_duration`, kiểm tra lại không chồng voice và ghép các segment thành một track WAV mono 48 kHz bằng FFmpeg. Track được bù khoảng lặng tới thời lượng timeline mới do Step 5 tính (hoặc video gốc khi không kéo dài). Step này chỉ xử lý voice, không trộn background hoặc video.
- Step 7 render MP4 H.264/AAC từ hình ảnh video gốc và voice track Step 6. Khi Step 5 có kế hoạch kéo dài, Step 7 mới thực sự slow/freeze đúng các đoạn cục bộ và retime voice gốc/background tương ứng; file nguồn không bị sửa. Subtitle SRT dùng timeline voice đã căn chỉnh; burn subtitle là tùy chọn.
- Local Model ở Step 3 được đánh dấu chưa triển khai.

Hướng dẫn chi tiết:

- [`huong_dan_su_dung/07_huong_dan_step_04_text_to_speech.md`](huong_dan_su_dung/07_huong_dan_step_04_text_to_speech.md)
- [`huong_dan_su_dung/08_huong_dan_step_05_audio_sync.md`](huong_dan_su_dung/08_huong_dan_step_05_audio_sync.md)
- [`huong_dan_su_dung/09_huong_dan_step_06_build_audio.md`](huong_dan_su_dung/09_huong_dan_step_06_build_audio.md)
- [`huong_dan_su_dung/10_huong_dan_step_07_render_export.md`](huong_dan_su_dung/10_huong_dan_step_07_render_export.md)
