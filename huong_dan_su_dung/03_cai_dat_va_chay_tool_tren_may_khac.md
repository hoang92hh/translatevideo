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

### 4.2. Cài TransLanguage và ONNX Runtime GPU

```cmd
.venv\Scripts\python.exe -m pip install -e ".[cuda]"
```

Profile này cài `onnxruntime-gpu 1.20.x`, tương thích CUDA 12.x.

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
