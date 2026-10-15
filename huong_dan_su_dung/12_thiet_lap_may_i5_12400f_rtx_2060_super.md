# Thiết lập TransLanguage trên máy i5-12400F + RTX 2060 SUPER

Tài liệu này dành cho máy có cấu hình:

- Windows 10 Pro 64-bit, build 19045.
- Intel Core i5-12400F, 6 nhân/12 luồng.
- NVIDIA GeForce RTX 2060 SUPER, 8 GB VRAM.
- RAM 16 GB.
- NVIDIA driver hiển thị trong Windows là `32.0.16.1062`.

RTX 2060 SUPER thuộc kiến trúc Turing và phù hợp với profile CUDA 12.6 hiện tại
của TransLanguage. RAM 16 GB và VRAM 8 GB đủ để chạy pipeline, nhưng không nên
chạy đồng thời nhiều tác vụ AI hoặc mở nhiều ứng dụng dùng GPU.

## 1. Chuẩn bị dung lượng và vị trí cài đặt

Nên đặt source code, môi trường Python và cache model trên SSD. Dành ít nhất
25 GB trống; 50 GB trở lên được khuyến nghị nếu dùng nhiều model Faster Whisper,
MDX, MeloTTS/OpenVoice hoặc lưu nhiều project video.

Các lệnh trong tài liệu giả sử source được đặt tại:

```text
G:\workspace\translanguage
```

Nếu dùng ổ đĩa hoặc thư mục khác, thay đường dẫn trên bằng vị trí thực tế. Không
sao chép `.venv` hoặc `.runtimes` từ máy cũ vì chúng chứa đường dẫn và binary phụ
thuộc máy. Các thư mục `projects` và `models` có thể sao chép nguyên vẹn.

## 2. Các tệp và phần mềm cần chuẩn bị

### 2.1. Thành phần bắt buộc

| Thành phần | Cách lấy | Mục đích |
|---|---|---|
| Source code TransLanguage | Sao chép toàn bộ repository hoặc clone từ nơi lưu source | Chạy ứng dụng |
| Python 3.13 x64 | `winget install --exact --id Python.Python.3.13` | Môi trường `.venv` chính |
| FFmpeg + FFprobe | `winget install --exact --id Gyan.FFmpeg` | Xử lý audio/video ở Step 1, 5, 6 và 7 |
| NVIDIA driver | Cài từ NVIDIA nếu `nvidia-smi` không hoạt động hoặc driver quá cũ | CUDA cho Step 1, 2 và TTS local |
| Google Chrome 120 trở lên | Cài Chrome bản 64-bit | ChatGPT Web, Claude Web và extension bridge |
| Model pyannote Community-1 | Sao chép thư mục model local từ máy đang hoạt động | Nhận diện speaker ở Step 2 |

Thư mục model pyannote mặc định phải nằm tại:

```text
<thư mục TransLanguage>\models\pyannote-speaker-diarization-community-1
```

Tối thiểu phải có đủ các file dữ liệu thật sau, không phải Git LFS pointer:

```text
config.yaml
embedding\pytorch_model.bin
segmentation\pytorch_model.bin
plda\plda.npz
plda\xvec_transform.npz
```

### 2.2. Thành phần chỉ cần khi sử dụng tính năng tương ứng

| Thành phần | Khi nào cần |
|---|---|
| Google API key | Dịch bằng Gemini hoặc các chức năng Gemini khác |
| Tài khoản ChatGPT/Claude | Dịch qua provider web ở Step 3 |
| Python 3.9 x64 | Chỉ khi cài MeloTTS + OpenVoice V2 |
| Piper voice `.onnx` và `.onnx.json` | Chỉ khi dùng Piper TTS |
| Audio giọng tham chiếu hợp lệ | Khi dùng VieNeu hoặc OpenVoice để mô phỏng/chuyển giọng |

Không cần cài toàn bộ CUDA Toolkit chỉ để chạy TransLanguage. Profile CUDA của
project cài cuBLAS và cuDNN cần thiết trực tiếp vào `.venv`.

## 3. Kiểm tra máy trước khi cài

Mở **Command Prompt** mới và chạy:

```cmd
py -3.13 --version
ffmpeg -version
ffprobe -version
nvidia-smi
```

Yêu cầu:

- Python phải là bản 3.13 64-bit.
- `ffmpeg` và `ffprobe` đều chạy được.
- `nvidia-smi` phải hiển thị `NVIDIA GeForce RTX 2060 SUPER`.
- Mục `CUDA Version` của `nvidia-smi` phải từ 12.6 trở lên.

Số driver `32.0.16.1062` trong Device Manager chưa đủ để kết luận CUDA đã sẵn
sàng. Dùng kết quả `nvidia-smi` làm căn cứ. Nếu lệnh không tồn tại, không thấy
GPU hoặc báo CUDA thấp hơn 12.6, hãy cài/cập nhật NVIDIA driver rồi khởi động lại
Windows trước khi tiếp tục.

## 4. Tạo `.venv` mới và cài dependency CUDA

Đóng TransLanguage trước khi thao tác. Nếu repository vừa được sao chép sang máy
mới thì không mang theo `.venv` cũ. Nếu thư mục `.venv` đã tồn tại và cần giữ để
đối chiếu, đổi tên nó thủ công thành `.venv-backup` trước khi thực hiện các lệnh
bên dưới.

Mở **Command Prompt** tại repository:

```cmd
cd /d G:\workspace\translanguage
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.venv\Scripts\python.exe -m pip install -e ".[cuda]"
```

Profile `cuda` của project cài các thành phần chính sau:

- `onnxruntime-gpu 1.20.x` cho Step 1.
- `nvidia-cublas-cu12 12.6.4.1` và `nvidia-cudnn-cu12 9.6.0.74` cho
  CTranslate2/Faster Whisper ở Step 2.
- Các dependency chung được khai báo trong `pyproject.toml`.

Sau đó cài PyTorch CUDA 12.6 vào đúng `.venv`:

```cmd
.venv\Scripts\python.exe -m pip install --force-reinstall --no-deps torch==2.11.0+cu126 torchvision==0.26.0+cu126 --index-url https://download.pytorch.org/whl/cu126
```

Không dùng lệnh `pip` chung của Windows. Luôn gọi
`.venv\Scripts\python.exe -m pip` để tránh cài nhầm sang Python khác.

## 5. Xác nhận `.venv` sau khi cài

### 5.1. Đúng Python của project

```cmd
.venv\Scripts\python.exe -c "import sys,struct; print(sys.executable); print('Python:', sys.version); print('Bits:', struct.calcsize('P') * 8)"
```

Kết quả phải trỏ vào `translanguage\.venv\Scripts\python.exe` và hiển thị
`Bits: 64`.

### 5.2. PyTorch nhìn thấy RTX 2060 SUPER

```cmd
.venv\Scripts\python.exe -c "import torch; print('PyTorch:', torch.__version__); print('CUDA:', torch.version.cuda); print('Available:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'Khong co')"
```

Kết quả mong đợi:

```text
PyTorch: 2.11.0+cu126
CUDA: 12.6
Available: True
GPU: NVIDIA GeForce RTX 2060 SUPER
```

### 5.3. ONNX Runtime có CUDA provider

```cmd
.venv\Scripts\python.exe -c "import onnxruntime as ort; print('ONNX Runtime:', ort.__version__); print('Providers:', ort.get_available_providers())"
```

Phiên bản phải thuộc dòng `1.20.x` và danh sách phải có cả:

```text
CUDAExecutionProvider
CPUExecutionProvider
```

Việc provider xuất hiện mới xác nhận package đã được cài. Khi chạy Step 1, tool
còn kiểm tra session của model thực sự kích hoạt `CUDAExecutionProvider`.

### 5.4. CTranslate2/Faster Whisper nhìn thấy GPU

```cmd
.venv\Scripts\python.exe -c "import ctranslate2; print('CUDA devices:', ctranslate2.get_cuda_device_count()); print('Compute types:', ctranslate2.get_supported_compute_types('cuda'))"
.venv\Scripts\python.exe -m pip show nvidia-cublas-cu12 nvidia-cudnn-cu12
```

`CUDA devices` phải lớn hơn `0`. Hai package NVIDIA phải lần lượt là
`12.6.4.1` và `9.6.0.74`.

### 5.5. Kiểm tra dependency

```cmd
.venv\Scripts\python.exe -m pip check
```

Kết quả mong đợi:

```text
No broken requirements found.
```

Nếu một trong các bước trên lỗi, chưa nên chạy toàn bộ pipeline. Xem mục 11 để
xử lý theo đúng backend bị lỗi.

## 6. Chuẩn bị model cho Step 1 và Step 2

### 6.1. Model MDX ở Step 1

Model MDX được tải vào cache khi sử dụng lần đầu, vì vậy lần chạy đầu cần Internet.
Trong Step 1 nên chọn thiết bị `Auto`. Chỉ khi candidate ghi nhận
`NVIDIA GPU (CUDA)` và metadata có `CUDAExecutionProvider` mới xác nhận MDX đang
inference bằng GPU.

### 6.2. Faster Whisper ở Step 2

Model Faster Whisper được tải ở lần sử dụng đầu và lưu tại:

```text
%LOCALAPPDATA%\TransLanguage\faster-whisper-models
```

Với RAM 16 GB và VRAM 8 GB, cấu hình khởi đầu được khuyến nghị:

- Model: `medium`.
- Thiết bị: `Auto`.
- Đóng game, phần mềm dựng video và ứng dụng AI khác trước khi chạy.
- Chỉ thử `large-v3` khi cần độ chính xác cao hơn và hệ thống còn đủ RAM/VRAM.
- Nếu báo thiếu bộ nhớ, dùng model nhỏ hơn hoặc chuyển thiết bị sang `CPU`.

### 6.3. Speaker diarization local

Mở **Cài đặt → API & Providers** và kiểm tra đường dẫn model pyannote. Nếu đã đặt
model đúng vị trí mặc định trong repository, ứng dụng sẽ tự tìm thấy. Step 2 không
cần Hugging Face token và không tự tải model pyannote.

## 7. Cài Chrome extension cho ChatGPT và Claude

Extension nằm trong thư mục:

```text
G:\workspace\translanguage\extension
```

Thực hiện:

1. Mở `chrome://extensions`.
2. Bật **Developer mode**.
3. Chọn **Load unpacked**.
4. Chọn đúng thư mục `extension`, không chọn thư mục repository cha.
5. Kiểm tra thẻ extension hiển thị phiên bản `0.16.0` hoặc đúng phiên bản trong
   `extension\manifest.json` nếu source đã được cập nhật sau tài liệu này.
6. Đăng nhập ChatGPT và Claude trong Chrome.
7. Chỉ để một tab đang dùng cho mỗi provider, rồi tải lại tab sau khi nạp hoặc
   reload extension.

Đối với ChatGPT, mở đúng Project URL đã cấu hình trong Step 3. Đối với Claude,
mở đúng cuộc trò chuyện/project muốn dùng. Trước khi chạy Step 3, để ô nhập nội
dung trống và bảo đảm không còn file đính kèm từ job lỗi trước.

Nếu cập nhật source extension:

1. Mở lại `chrome://extensions`.
2. Nhấn **Reload** trên thẻ extension.
3. Xác nhận số phiên bản mới.
4. Tải lại tab ChatGPT/Claude và chờ vài giây rồi mới chạy Step 3.

## 8. Cấu hình provider và credential

### Gemini

Mở **Cài đặt → API & Providers** để nhập Google API key. Key được lưu trong
Windows Credential Locker của tài khoản Windows hiện tại. Có thể dùng biến môi
trường `GEMINI_API_KEY` hoặc `GOOGLE_API_KEY` thay thế.

### ChatGPT Web và Claude Web

Không sao chép cookie từ máy cũ. Đăng nhập trực tiếp trong Chrome trên máy mới.
Model được chọn trên trang web; TransLanguage giao tiếp với tab qua extension và
bridge localhost.

Tường lửa hoặc phần mềm bảo mật phải cho phép ứng dụng và Chrome kết nối tới
`127.0.0.1`. Không mở bridge localhost ra mạng LAN hoặc Internet.

## 9. TTS ở Step 4

### Thành phần đã có trong `.venv` chính

Lệnh cài project đã cài `vieneu`, `edge-tts` và `soundfile`:

- VieNeu có thể dùng local; bắt đầu với thiết bị `Auto`.
- Edge TTS chạy online và không dùng GPU local.

Khi dùng audio tham chiếu, chỉ sử dụng giọng mà người vận hành có quyền hoặc đã
được chủ giọng đồng ý.

### MeloTTS + OpenVoice V2, tùy chọn

Melo/OpenVoice dùng runtime Python 3.9 riêng để tránh xung đột `.venv` chính.
Cài Python 3.9 x64, sau đó chạy trong PowerShell tại repository:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_melo_runtime.ps1 -Device cuda
```

Script tạo `.runtimes\melo`, cài runtime CUDA 11.8 và tải checkpoint OpenVoice V2.
Không chạy lệnh này nếu không sử dụng Melo/OpenVoice vì dung lượng tải khá lớn.

### Piper TTS, tùy chọn

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_piper_runtime.ps1
```

Piper chạy CPU/ONNX. Script chỉ cài engine vào `.runtimes\piper`, không tự tải
voice. Mỗi voice phải có hai file đặt cạnh nhau:

```text
ten_voice.onnx
ten_voice.onnx.json
```

Đọc model card và giấy phép riêng của voice trước khi sử dụng hoặc phân phối.

## 10. Chạy ứng dụng và cấu hình ban đầu

Không cần kích hoạt `.venv`. Mở **Command Prompt** và chạy trực tiếp:

```cmd
cd /d G:\workspace\translanguage
.venv\Scripts\python.exe main.py
```

Cấu hình khởi đầu khuyến nghị cho máy này:

| Step | Thiết lập khuyến nghị |
|---|---|
| Step 1 — Audio Preparation | MDX, thiết bị `Auto`; nếu lỗi CUDA thì thử CPU |
| Step 2 — Speech to Text | Faster Whisper `medium`, thiết bị `Auto`, bật diarization khi model local hợp lệ |
| Step 3 — Translation | Chỉ chọn một provider; với web provider phải kiểm tra extension và tab trước |
| Step 4 — Text to Speech | VieNeu `Auto` hoặc Edge TTS; không chạy Melo đồng thời với Step 1/2 |
| Step 5–7 | Đảm bảo FFmpeg/FFprobe khả dụng và ổ đĩa còn đủ dung lượng |

Trong Task Manager, đổi biểu đồ GPU từ `3D` sang `CUDA` hoặc `Compute_0` để theo
dõi đúng tải inference. Có thể mở một cửa sổ Command Prompt khác và chạy
`nvidia-smi` trong lúc xử lý.

## 11. Xử lý lỗi thường gặp

### `torch.cuda.is_available()` là `False`

Xác nhận đang dùng đúng Python:

```cmd
.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
```

Sau đó kiểm tra `nvidia-smi` và cài lại PyTorch CUDA bằng đúng lệnh ở mục 4.

### Có `CUDAExecutionProvider` nhưng Step 1 vẫn dùng CPU

Kiểm tra ONNX Runtime phải thuộc dòng `1.20.x`:

```cmd
.venv\Scripts\python.exe -c "import onnxruntime as ort; print(ort.__version__); print(ort.get_available_providers())"
```

Nếu sai phiên bản, cài lại profile project:

```cmd
.venv\Scripts\python.exe -m pip install --upgrade -e ".[cuda]"
```

### Faster Whisper báo thiếu CUDA DLL hoặc không thấy GPU

Kiểm tra lại CTranslate2 và hai runtime NVIDIA theo mục 5.4. Nếu package thiếu,
cài lại profile `.[cuda]`. Không tự nâng riêng cuDNN lên bản mới hơn vì project
đang khóa cặp phiên bản đã kiểm tra tương thích.

### Thiếu RAM hoặc VRAM

- Đóng Chrome tab không cần thiết, game, ứng dụng dựng video và công cụ AI khác.
- Chỉ chạy một step dùng GPU tại một thời điểm.
- Dùng Faster Whisper `medium` hoặc model nhỏ hơn.
- Chuyển Step 1/2/TTS sang CPU khi cần; xử lý sẽ chậm hơn nhưng ổn định hơn.
- Khởi động lại ứng dụng nếu một worker lỗi khiến VRAM chưa được giải phóng.

### Không tìm thấy FFmpeg

Đóng rồi mở lại Command Prompt sau khi cài bằng Winget. Nếu `ffmpeg -version`
vẫn lỗi, chọn trực tiếp `ffmpeg.exe` trong giao diện Step 1; `ffprobe.exe` phải nằm
cùng thư mục.

### Extension không kết nối hoặc không gửi prompt

- Kiểm tra version tại `chrome://extensions` khớp `extension\manifest.json`.
- Nhấn **Reload** extension, sau đó tải lại tab provider.
- Chỉ giữ một tab của provider đang được dùng.
- Xóa prompt/file còn sót trong composer trước khi chạy lại.
- Đảm bảo tab đang đăng nhập, đúng URL và ô nhập sẵn sàng.

### Model pyannote không hợp lệ

Kiểm tra đủ năm file bắt buộc ở mục 2. File tải bằng Git LFS nhưng chỉ chứa dòng
pointer không phải model thật; cần sao chép lại toàn bộ model đã tải hoàn chỉnh từ
máy đang hoạt động.

## 12. Checklist hoàn tất

Trước khi xử lý video thật, xác nhận lần lượt:

- [ ] `py -3.13 --version` hoạt động.
- [ ] `ffmpeg -version` và `ffprobe -version` hoạt động.
- [ ] `nvidia-smi` nhận đúng RTX 2060 SUPER và CUDA từ 12.6.
- [ ] `.venv` được tạo mới trên chính máy này.
- [ ] `pip check` không báo dependency hỏng.
- [ ] PyTorch trả về `Available: True`.
- [ ] ONNX Runtime có `CUDAExecutionProvider`.
- [ ] CTranslate2 có ít nhất một CUDA device.
- [ ] Model pyannote local có đủ file dữ liệu thật.
- [ ] Chrome extension đúng version và tab provider đã được reload.
- [ ] Credential/provider cần dùng đã được cấu hình.
- [ ] Chạy thử một video/audio ngắn trước khi chạy project dài.

Sau khi checklist đạt yêu cầu, những lần mở ứng dụng tiếp theo chỉ cần:

```cmd
cd /d G:\workspace\translanguage
.venv\Scripts\python.exe main.py
```
