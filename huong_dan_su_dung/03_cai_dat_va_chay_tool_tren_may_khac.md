1. Di chuyển đến thư mục dự án
Mở PowerShell:
cd G:\workspace\multiple_voice\translatevideo


2. Tạo thư mục môi trường
New-Item -ItemType Directory -Force -Path `
    "environment\app", `
    "environment\providers", `
    "environment\models"


3. Tạo môi trường Python 3.13
py -3.13 -m venv environment\app\.venv


Kiểm tra phiên bản:
.\environment\app\.venv\Scripts\python.exe --version


Phiên bản đã sử dụng: Python 3.13.15.
4. Cài công cụ Python
.\environment\app\.venv\Scripts\python.exe -m pip install --upgrade pip wheel "setuptools<82"


Sử dụng giới hạn setuptools<82 để tránh xung đột với PyTorch 2.11.0.

Phiên bản đã kiểm tra thành công: setuptools 81.0.0.
5. Kiểm tra công cụ hệ thống
py -3.13 --version
ffmpeg -version
ffprobe -version
nvidia-smi


Các phiên bản đã xác nhận trên máy:
Thành phần	Phiên bản
Python	3.13.15
FFmpeg	9.0.2
FFprobe	9.0.2
NVIDIA Driver	560.94
CUDA Driver hỗ trợ	12.6
GPU	NVIDIA GTX 1060 6GB
Nếu cài trên máy khác và thiếu Python hoặc FFmpeg, sử dụng:
winget install --exact --id Python.Python.3.13
winget install --exact --id Gyan.FFmpeg


6. Cài dependencies dự án với profile CUDA
Đảm bảo pyproject.toml tồn tại ở thư mục gốc:
Test-Path .\pyproject.toml


Sau đó cài đặt:
.\environment\app\.venv\Scripts\python.exe -m pip install -e ".[cuda]"


Lệnh này sử dụng profile [cuda] trong pyproject.toml của dự án.
7. Cài PyTorch CUDA 12.6
.\environment\app\.venv\Scripts\python.exe -m pip install --force-reinstall --no-deps torch==2.11.0+cu126 torchvision==0.26.0+cu126 --index-url https://download.pytorch.org/whl/cu126


Phiên bản đã cài thành công:
- PyTorch 2.11.0+cu126
- Torchvision 0.26.0+cu126
8. Kiểm tra PyTorch sử dụng GPU
.\environment\app\.venv\Scripts\python.exe -c "import torch; print('PyTorch:', torch.__version__); print('CUDA:', torch.version.cuda); print('Available:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'Khong co')"


Kết quả thực tế:
PyTorch: 2.11.0+cu126
CUDA: 12.6
Available: True
GPU: NVIDIA GeForce GTX 1060 6GB


9. Kiểm tra ONNX Runtime
.\environment\app\.venv\Scripts\python.exe -c "import onnxruntime as ort; print('ONNX Runtime:', ort.__version__); print('Providers:', ort.get_available_providers())"


Kết quả thực tế:
ONNX Runtime: 1.20.2
Providers: ['TensorrtExecutionProvider', 'CUDAExecutionProvider', 'CPUExecutionProvider']


10. Kiểm tra CTranslate2 (Faster Whisper)
.\environment\app\.venv\Scripts\python.exe -c "import ctranslate2; print('CUDA devices:', ctranslate2.get_cuda_device_count()); print('Compute types:', ctranslate2.get_supported_compute_types('cuda'))"


Kết quả thực tế:
CUDA devices: 1
Compute types: {'int8_float32', 'float32', 'int8'}


11. Kiểm tra cuBLAS và cuDNN
.\environment\app\.venv\Scripts\python.exe -m pip show nvidia-cublas-cu12 nvidia-cudnn-cu12


Phiên bản đã xác nhận:
Thư viện	Phiên bản
nvidia-cublas-cu12	12.6.4.1
nvidia-cudnn-cu12	9.6.0.74
12. Kiểm tra xung đột dependencies
.\environment\app\.venv\Scripts\python.exe -m pip check


Kết quả thực tế:
No broken requirements found.


13. Khởi chạy ứng dụng
.\environment\app\.venv\Scripts\python.exe -m video_translator.app

.\environment\app\.venv\Scripts\translanguage.exe


Lưu ý: Môi trường đã vượt qua các kiểm tra dependency ở trên, nhưng ứng dụng vẫn đang có lỗi import trong source code và chưa được xác nhận khởi chạy thành công.

14. Lưu danh sách phiên bản đã cài
Đây là bước bổ sung, chưa thực hiện trong quá trình cài trước đó. Để lưu toàn bộ package và phiên bản hiện có:
.\environment\app\.venv\Scripts\python.exe -m pip freeze > environment\requirements-installed.txt