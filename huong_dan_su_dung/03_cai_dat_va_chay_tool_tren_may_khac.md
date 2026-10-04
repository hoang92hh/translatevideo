# Cài đặt và chạy TransLanguage trên máy tính khác

Tài liệu này áp dụng cho Windows 10/11. Mỗi máy chỉ cần cài đặt môi trường một
lần. Những lần sử dụng sau chỉ cần chạy ứng dụng bằng Python trong `.venv`.

## 1. Chọn cấu hình phù hợp

- Máy có NVIDIA GPU và driver hỗ trợ CUDA 12.6: dùng profile `cuda`.
- Máy không có NVIDIA GPU: dùng profile `cpu`.
- AMD/Intel GPU hiện chưa được hỗ trợ trên giao diện; hãy dùng profile `cpu`.

Profile CUDA hiện được cấu hình cho CUDA 12.x và tương thích GPU Pascal như GTX
10xx. Không tự thay ONNX Runtime hoặc PyTorch bằng bản CUDA 13 trên những GPU
này.

### 1.1. Chọn runtime theo dòng GPU

Không chọn dependency chỉ theo chữ `GTX` hoặc `RTX`. Ví dụ GTX 1060 là Pascal,
nhưng GTX 1660 là Turing và có khả năng tương thích khác.

| Dòng GPU phổ biến | Kiến trúc | Cách cài khuyến nghị | Ghi chú |
|---|---|---|---|
| GTX 9xx | Maxwell | Thử profile `[cuda]`; chuyển `[cpu]` nếu backend không khởi tạo | Phần cứng cũ, hiệu năng và khả năng hỗ trợ có thể hạn chế |
| GTX 10xx | Pascal | Profile `[cuda]`: cuBLAS `12.6.4.1`, cuDNN `9.6.0.74` | Cấu hình GTX 1060 đã được xác minh bằng inference thực tế |
| GTX 16xx, RTX 20xx | Turing | Profile `[cuda]` hiện tại | Có thể dùng runtime mới hơn, nhưng không bắt buộc |
| RTX 30xx | Ampere | Profile `[cuda]` hiện tại | FP16 thường hiệu quả hơn Pascal |
| RTX 40xx | Ada | Profile `[cuda]` hiện tại | Có thể cân nhắc runtime mới hơn sau khi kiểm tra support matrix |
| RTX 50xx / GPU NVIDIA mới hơn | Blackwell hoặc mới hơn | Đối chiếu support matrix và dùng driver/cuDNN hỗ trợ đúng GPU | Profile Pascal hiện tại chưa được xác minh trên nhóm này |
| NVIDIA Tesla/Quadro | Phụ thuộc model | Xác định kiến trúc/compute capability trước rồi đối chiếu hàng tương ứng | Tên thương mại không đủ để chọn runtime |
| AMD hoặc Intel GPU | Không dùng CUDA | Profile `[cpu]` | CTranslate2 CUDA chỉ hỗ trợ NVIDIA |
| Apple Silicon | Không áp dụng cho hướng dẫn Windows này | Dùng CPU hoặc xây dựng profile riêng | Chưa có profile Metal trong ứng dụng |

Profile hiện tại là baseline an toàn cho máy đã được kiểm tra và nhiều GPU NVIDIA
từ Turing đến Ada. Runtime mới nhất không luôn tốt hơn: các bản cuDNN mới có thể
ngừng hỗ trợ kiến trúc cũ hoặc yêu cầu driver mới hơn.

Nếu cần thay phiên bản cho một kiến trúc khác:

1. Tra tên GPU và driver bằng `nvidia-smi`.
2. Xác định kiến trúc và compute capability trên tài liệu NVIDIA.
3. Đối chiếu cuDNN Support Matrix cho đúng CUDA, driver và compute capability.
4. Sửa đồng thời hai dòng `nvidia-cublas-cu12` và `nvidia-cudnn-cu12` trong
   nhóm `[project.optional-dependencies].cuda` của `pyproject.toml`.
5. Tạo `.venv` mới rồi cài lại `.[cuda]`; không nâng riêng một package trong môi
   trường đang dùng.
6. Xác nhận bằng một inference ngắn trước khi xử lý toàn bộ video.

Không thay hai phiên bản đã khóa nếu máy đang dùng GTX 1060/Pascal và pipeline
đang hoạt động ổn định.

## 2. Cài công cụ nền

Mở **Command Prompt** và chạy:

```cmd
winget install --exact --id Python.Python.3.13
winget install --exact --id Gyan.FFmpeg
```

Đóng Command Prompt, mở lại rồi kiểm tra:

```cmd
py -3.13 --version
ffmpeg -version
ffprobe -version
```

Nếu dùng NVIDIA GPU, cài hoặc cập nhật NVIDIA Driver rồi kiểm tra:

```cmd
nvidia-smi
```

Lệnh phải hiển thị tên GPU và phiên bản driver trước khi tiếp tục.

`CUDA Version` trong `nvidia-smi` là mức CUDA tối đa mà driver hỗ trợ, không xác
nhận cuBLAS/cuDNN đã được cài. Hai runtime đó được profile `[cuda]` cài vào `.venv`.

## 3. Chuẩn bị source code

Sao chép hoặc clone repository sang máy mới. Không sao chép thư mục `.venv` từ
máy cũ vì virtual environment chứa đường dẫn và binary riêng của từng máy.

Các ví dụ bên dưới giả sử repository nằm tại:

```text
G:\workspace\translanguage
```

Nếu repository nằm ở nơi khác, thay đường dẫn này bằng vị trí thực tế.

## 4. Cài đặt cho máy NVIDIA GPU

### 4.1. Tạo môi trường riêng

```cmd
cd /d G:\workspace\translanguage
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
```

### 4.2. Cài TransLanguage và runtime GPU

```cmd
.venv\Scripts\python.exe -m pip install -e ".[cuda]"
```

Profile này cài:

- `onnxruntime-gpu 1.20.x` cho Step 01.
- `nvidia-cublas-cu12 12.6.4.1` và `nvidia-cudnn-cu12 9.6.0.74` cho Step 02.

Hai runtime NVIDIA của Step 02 nằm trong `.venv`. Worker tự đăng ký thư mục DLL,
không cần thêm chúng vào `PATH` hệ thống. Các phiên bản được khóa để hỗ trợ GPU
Pascal như GTX 1060.

### 4.3. Cài PyTorch CUDA 12.6

```cmd
.venv\Scripts\python.exe -m pip install --force-reinstall --no-deps torch==2.11.0+cu126 torchvision==0.26.0+cu126 --index-url https://download.pytorch.org/whl/cu126
```

Lệnh này thay PyTorch CPU bằng PyTorch CUDA nhưng chỉ bên trong `.venv` của
TransLanguage, không ảnh hưởng project Python khác.

### 4.4. Kiểm tra PyTorch CUDA

```cmd
.venv\Scripts\python.exe -c "import torch; print('PyTorch:', torch.__version__); print('CUDA:', torch.version.cuda); print('Available:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'Khong co')"
```

Kết quả mong đợi:

```text
PyTorch: 2.11.0+cu126
CUDA: 12.6
Available: True
GPU: NVIDIA ...
```

### 4.5. Kiểm tra ONNX Runtime

```cmd
.venv\Scripts\python.exe -c "import onnxruntime as ort; print('ONNX Runtime:', ort.__version__); print('Providers:', ort.get_available_providers())"
```

Kết quả cần có:

```text
ONNX Runtime: 1.20.2
CUDAExecutionProvider
CPUExecutionProvider
```

Danh sách có `CUDAExecutionProvider` mới chỉ xác nhận package hỗ trợ CUDA. Khi
Step 01 load model, TransLanguage còn kiểm tra provider của session thực tế. Chỉ
khi session model kích hoạt thành công CUDA thì candidate mới được ghi là
`NVIDIA GPU (CUDA)`.

### 4.6. Kiểm tra dependency

```cmd
.venv\Scripts\python.exe -m pip check
```

Kết quả mong đợi:

```text
No broken requirements found.
```

### 4.7. Kiểm tra CUDA cho Faster Whisper

Step 2 dùng CTranslate2, không dùng backend PyTorch hoặc ONNX Runtime của Step 1.
Khi cài profile `[cuda]`, cuBLAS và cuDNN được cài trực tiếp vào `.venv` và được
worker tìm tự động. Không cần cài toàn bộ CUDA Toolkit chỉ để chạy Step 2.

Kiểm tra CTranslate2 nhìn thấy GPU:

```cmd
.venv\Scripts\python.exe -c "import ctranslate2; print('CUDA devices:', ctranslate2.get_cuda_device_count()); print('Compute types:', ctranslate2.get_supported_compute_types('cuda'))"
```

`CUDA devices` cần lớn hơn `0`. Có thể xác nhận thêm các package runtime:

```cmd
.venv\Scripts\python.exe -m pip show nvidia-cublas-cu12 nvidia-cudnn-cu12
```

Nếu thiếu package, cài lại profile CUDA bằng lệnh ở mục 4.2. Không tự nâng cuDNN
lên bản mới nhất trên GTX 10xx vì các nhánh mới đã ngừng hỗ trợ Pascal.

Để xác nhận GPU thực sự inference, chạy một đoạn audio ngắn và kiểm tra metadata
`actual_device: NVIDIA GPU (CUDA)`. Task Manager cần chuyển biểu đồ từ `3D` sang
`CUDA` hoặc `Compute_0`; có thể dùng `nvidia-smi` để theo dõi thêm.

## 5. Cài đặt cho máy chỉ dùng CPU

Không chạy các lệnh cài PyTorch CUDA ở phần trên. Chạy:

```cmd
cd /d G:\workspace\translanguage
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.venv\Scripts\python.exe -m pip install -e ".[cpu]"
.venv\Scripts\python.exe -m pip check
```

## 6. Chạy TransLanguage

Không cần kích hoạt `.venv`. Gọi trực tiếp Python của project:

```cmd
cd /d G:\workspace\translanguage
.venv\Scripts\python.exe main.py
```

Trong Step 01 — Audio Preparation:

- `Auto`: ưu tiên CUDA khi cả PyTorch và session ONNX hoạt động; nếu CUDA không
  khởi tạo được thì thử lại bằng CPU.
- `NVIDIA GPU (CUDA)`: bắt buộc dùng GPU; nếu session model không kích hoạt được
  CUDA, step dừng và hiển thị lỗi.
- `CPU`: bắt buộc dùng CPU.

Khi chạy GPU thành công, kết quả/candidate hiển thị `NVIDIA GPU (CUDA)` và
metadata ghi `execution_provider: CUDAExecutionProvider`.

Trong Step 02 — Speech to Text:

- `Auto`: chỉ chọn GPU khi CTranslate2 phát hiện GPU và tải được cuBLAS/cuDNN;
  nếu cấu hình CUDA chưa đầy đủ thì chọn CPU ngay. Auto chỉ fallback trong lúc
  chạy khi gặp lỗi CUDA hoặc thiếu VRAM.
- `GPU`: bắt buộc dùng NVIDIA CUDA; lỗi CUDA sẽ được hiển thị và không fallback.
- `CPU`: bắt buộc dùng CPU, không tự chuyển sang GPU.
- Lần chạy đầu của mỗi model cần Internet để tải model vào cache của ứng dụng.

## 7. Những lần chạy sau

Không cài lại dependency. Chỉ chạy:

```cmd
cd /d G:\workspace\translanguage
.venv\Scripts\python.exe main.py
```

Chỉ cần cài lại khi:

- Chuyển repository sang máy khác.
- Xóa hoặc làm hỏng thư mục `.venv`.
- Thay đổi profile CPU/CUDA.
- Dependency của project được cập nhật.

## 8. Tạo file chạy nhanh

Tạo file `run.cmd` trong thư mục repository với nội dung:

```bat
@echo off
cd /d G:\workspace\translanguage
.venv\Scripts\python.exe main.py
```

Sau đó có thể bấm đúp `run.cmd` để mở ứng dụng. Nếu repository nằm ở đường dẫn
khác, sửa dòng `cd /d` cho phù hợp.

## 9. Theo dõi GPU đúng cách

Task Manager thường hiển thị biểu đồ `3D`, không phản ánh tải CUDA. Trong phần
GPU, đổi biểu đồ sang `CUDA` hoặc `Compute_0`.

Có thể theo dõi nhanh bằng Command Prompt:

```cmd
nvidia-smi
```

CPU vẫn được sử dụng cho đọc audio, chia chunk, NumPy và ghi WAV. Điều này bình
thường; phần inference nặng phải được xác nhận bằng `CUDAExecutionProvider`.

## 10. Xử lý lỗi thường gặp

### `torch.cuda.is_available()` trả về `False`

Kiểm tra ứng dụng có được chạy bằng đúng `.venv` không:

```cmd
.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
```

Đường dẫn phải kết thúc bằng:

```text
translanguage\.venv\Scripts\python.exe
```

Sau đó cài lại PyTorch CUDA 12.6 theo mục 4.3.

### Có `CUDAExecutionProvider` nhưng model vẫn dùng CPU

Kiểm tra phiên bản ONNX Runtime:

```cmd
.venv\Scripts\python.exe -c "import onnxruntime as ort; print(ort.__version__)"
```

Với profile CUDA 12.x của project, phiên bản phải thuộc dòng `1.20.x`. Chạy lại
lệnh sau để đồng bộ dependency theo `pyproject.toml`:

```cmd
.venv\Scripts\python.exe -m pip install --upgrade -e ".[cuda]"
```

### Không tìm thấy FFmpeg

Kiểm tra:

```cmd
where ffmpeg
where ffprobe
```

Nếu PATH chưa cập nhật, đóng Command Prompt và ứng dụng rồi mở lại. Cũng có thể
chọn trực tiếp `ffmpeg.exe` trong cấu hình Step 01.

### PowerShell chặn `Activate.ps1`

Không cần kích hoạt virtual environment. Luôn có thể chạy trực tiếp:

```cmd
.venv\Scripts\python.exe main.py
```

## 11. Gỡ và tạo lại môi trường

Nếu `.venv` bị lỗi, đóng TransLanguage trước. Xóa riêng thư mục `.venv`, sau đó
tạo lại theo mục 4 hoặc mục 5. Không xóa thư mục `projects` vì đây là nơi chứa
project và các artifact đã xử lý.
