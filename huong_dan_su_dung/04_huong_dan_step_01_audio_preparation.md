# Hướng dẫn Step 01 — Audio Preparation

Step 01 chuẩn bị audio đầu vào cho pipeline. Người dùng có thể giữ nguyên âm
thanh từ video dưới dạng **Original Mix**, hoặc dùng MDX-Net để tách thành
**Voice** và **Background**. Mỗi lần tách MDX thành công tạo một candidate mới
để nghe, so sánh và lựa chọn.

## 1. Đầu vào và đầu ra

### Đầu vào

- Video gốc của project.
- Provider xử lý được chọn.
- Đường dẫn FFmpeg.
- Sample rate và số kênh audio.
- Model và thiết bị xử lý nếu dùng MDX-Net.

### Đầu ra

Tùy provider, Step 01 tạo:

- `Original Mix`: audio PCM WAV được FFmpeg trích từ video.
- `Voice`: giọng nói/giọng hát do MDX tách ra.
- `Background`: phần âm thanh nền còn lại do MDX tách ra.

`Voice` hoặc `Original Mix` có thể được chọn làm đầu vào cho Step 02. File
`Background` được giữ lại để sử dụng ở các bước ghép audio sau.

## 2. Các khu vực chính trên giao diện

### 2.1. Thanh trạng thái Step 01

Góc trên bên phải hiển thị trạng thái hiện tại:

- `ĐANG CHỜ`: project chưa sẵn sàng để chạy step.
- `SẴN SÀNG`: có thể chạy Step 01.
- `ĐANG CHẠY`: FFmpeg hoặc MDX đang xử lý.
- `HOÀN THÀNH`: đã có kết quả hợp lệ.
- `CẦN CẬP NHẬT`: dữ liệu đầu vào đã thay đổi và cần chạy lại.
- `CÓ LỖI`: lần chạy gần nhất không thành công.

### 2.2. Khối Đầu vào

Hiển thị đường dẫn video đang được Step 01 xử lý. Đây là video đã được quản lý
trong workspace của project, không nhất thiết là đường dẫn file ban đầu bên
ngoài project.

### 2.3. Khối Thông tin project

Khối này gồm:

- `Project`: tên project đang mở.
- `Ngôn ngữ`: cặp ngôn ngữ nguồn và đích.
- `Video gốc`: đường dẫn video đầu vào của project.

Các giá trị này được thiết lập khi tạo hoặc mở project và không chỉnh sửa trực
tiếp trong Step 01.

### 2.4. Khối Phương án xử lý

Danh sách provider hiện có:

- `MDX-Net — Audio Separator`: trích audio và tách Voice/Background.
- `Original Audio — FFmpeg`: chỉ trích Original Mix, không tách stem.
- `Demucs — Chưa triển khai`: đã có vị trí trên giao diện nhưng chưa sử dụng
  được.
- `RoFormer — Chưa triển khai`: đã có vị trí trên giao diện nhưng chưa sử dụng
  được.

Các trường cấu hình thay đổi theo provider được chọn.

## 3. Provider Original Audio — FFmpeg

Provider này phù hợp khi muốn giữ nguyên toàn bộ âm thanh của video hoặc chưa
cần tách Voice/Background.

### FFmpeg executable

- `Auto`: tool tìm `ffmpeg` trong biến môi trường `PATH`.
- Có thể nhấn `Chọn file…` và chọn trực tiếp `ffmpeg.exe`.
- Tool cũng cần tìm thấy `ffprobe.exe`, thông thường nằm cùng thư mục với
  `ffmpeg.exe`.

### Sample rate

Các lựa chọn:

- `16 kHz`
- `44.1 kHz`
- `48 kHz`

`44.1 kHz` phù hợp với phần lớn tác vụ audio thông thường. `16 kHz` tạo file
nhỏ hơn nhưng giảm dải tần. `48 kHz` thường dùng cho video và hậu kỳ.

### Audio channels

- `Stereo`: giữ hai kênh trái/phải.
- `Mono`: trộn thành một kênh, giảm dung lượng và lượng dữ liệu xử lý.

### Kết quả

Provider tạo candidate `Original Mix` với stem `Original`. Candidate này là
artifact cơ sở nên không thể xóa trên giao diện.

## 4. Provider MDX-Net — Audio Separator

Provider này thực hiện hai giai đoạn:

1. FFmpeg trích Original Mix từ video.
2. MDX tách Original Mix thành Voice và Background.

Nếu Original Mix hiện có cùng sample rate và số kênh yêu cầu, tool tái sử dụng
file đó thay vì trích lại từ video.

### FFmpeg executable

Cách sử dụng giống provider Original Audio. FFmpeg và FFprobe phải khả dụng
trước khi MDX bắt đầu xử lý.

### MDX model

Các model hiện được khai báo:

- `UVR-MDX-NET-Inst_HQ_4.onnx`
- `UVR-MDX-NET-Voc_FT.onnx`
- `Kim_Vocal_2.onnx`

Model được tải vào cache ở lần sử dụng đầu tiên. Mỗi model có thể cho kết quả
khác nhau tùy loại giọng, nhạc nền và chất lượng audio. Có thể chạy nhiều model
để tạo nhiều candidate rồi nghe và so sánh.

### Working sample rate

- `44.1 kHz`
- `48 kHz`

Sample rate được dùng cho Original Mix đưa vào MDX. Nếu thay đổi giá trị này,
tool có thể phải trích lại audio nguồn thay vì tái sử dụng Original Mix cũ.

### Audio channels

- `Stereo`: nên dùng khi cần giữ không gian âm thanh và chất lượng background.
- `Mono`: giảm dữ liệu xử lý nhưng loại bỏ thông tin stereo.

### Thiết bị

#### Auto

Tool kiểm tra cả PyTorch CUDA và ONNX Runtime. Nếu CUDA khả dụng, worker ép
model dùng `cuda` và yêu cầu `CUDAExecutionProvider`.

Sau khi load model, tool kiểm tra session thực tế:

- Session dùng CUDA: tiếp tục xử lý bằng GPU.
- CUDA không khởi tạo được: dừng worker CUDA và thử lại bằng worker CPU.

`Auto` là lựa chọn khuyến nghị khi tool được sử dụng trên nhiều máy khác nhau.

#### CPU

Worker ẩn CUDA trước khi nạp PyTorch/ONNX, sau đó ép:

```text
torch device: CPU
ONNX provider: CPUExecutionProvider
```

Chế độ này chậm hơn nhưng hữu ích khi GPU thiếu VRAM, driver lỗi hoặc cần dành
GPU cho ứng dụng khác.

#### NVIDIA GPU (CUDA)

Worker ép:

```text
torch device: cuda
ONNX provider: CUDAExecutionProvider
```

Tool chỉ tiếp tục khi session model thực sự kích hoạt CUDA. Nếu CUDA provider
chỉ xuất hiện trong danh sách nhưng không tải được DLL/runtime, Step 01 báo lỗi
thay vì âm thầm chạy inference trên CPU.

## 5. Thanh tiến độ và thông tin thiết bị

Trong khi chạy, giao diện hiển thị phần trăm và thông báo theo từng giai đoạn:

- Chuẩn bị hoặc tái sử dụng Original Mix.
- Chọn CPU/GPU.
- Load engine và model MDX.
- Xác nhận provider thực tế.
- Tách Voice và Background.
- Chuẩn hóa tên và hoàn thiện file output.

Worker ghi diagnostic trước và sau `load_model()`, gồm:

- Thiết bị người dùng yêu cầu.
- Thiết bị được giải quyết thực tế.
- Phiên bản PyTorch và CUDA.
- Tên GPU.
- ONNX providers khả dụng.
- Torch device của model instance.
- Provider thực tế của ONNX session.
- Model dùng ONNX inference hay PyTorch inference.

Diagnostic được lưu trong metadata của candidate và kết quả Step 01 để hỗ trợ
kiểm tra lỗi.

## 6. Khối Kết quả

Sau khi hoàn thành, phần tóm tắt hiển thị:

- Số candidate được tạo trong lần chạy.
- Thời lượng audio.
- Dung lượng Original Mix.
- Sample rate.
- Mono hoặc Stereo.
- Thiết bị thực tế nếu dùng MDX.

Khối artifact hiển thị đường dẫn các file đầu ra được đề xuất cho pipeline.

## 7. Khối Nghe và chọn output

### 7.1. Candidate selector

Danh sách đầu tiên cho phép chọn giữa:

- `Original Mix`.
- Các candidate MDX được tạo ở những lần chạy khác nhau.

Candidate MDX hiển thị model và thiết bị thực tế, ví dụ:

```text
MDX · UVR-MDX-NET-Inst_HQ_4.onnx · NVIDIA GPU (CUDA)
```

Các nhãn bổ sung:

- `Step 2`: candidate/stem hiện đang được dùng làm đầu vào Step 02.
- `Mặc định`: candidate/stem mặc định khi chạy toàn pipeline.

### 7.2. Stem selector

Danh sách thứ hai hiển thị stem thuộc candidate đang chọn:

- Original Mix: `Original`.
- MDX candidate: `Voice` và `Background`.

### 7.3. Thông tin audio

Dòng thông tin bên dưới hiển thị:

- Provider.
- Model.
- Thiết bị thực tế.
- Stem đang chọn.
- Trạng thái file.
- Đường dẫn đầy đủ của file.

### 7.4. Trình phát audio

Các component:

- `Phát`: phát stem đang chọn.
- `Tạm dừng`: tạm dừng và giữ vị trí hiện tại.
- `Dừng`: dừng phát audio.
- Thanh thời gian: tua tới vị trí khác trong file.
- Nhãn thời gian: vị trí hiện tại và tổng thời lượng.
- Thanh âm lượng: điều chỉnh âm lượng nghe thử.

> **Lưu ý:** nút `Dừng` trong khối này chỉ dừng trình phát audio. Nó không hủy
> tiến trình FFmpeg hoặc MDX đang xử lý. Giao diện hiện chưa có nút hủy worker
> đang chạy.

## 8. Các nút thao tác với candidate

### Dùng cho Step 2

Chọn candidate/stem hiện tại làm đầu vào cho Step 02.

Chỉ chấp nhận:

- `Voice`
- `Original`

`Background` không thể làm input trực tiếp cho Step 02.

### Đặt làm mặc định

Đặt candidate/stem hiện tại làm đầu vào mặc định của pipeline. Khi chạy toàn bộ
pipeline, tool ưu tiên tái sử dụng lựa chọn mặc định còn hợp lệ.

Chỉ `Voice` hoặc `Original` có thể được đặt làm mặc định.

### Mở file

Mở file audio hiện tại bằng ứng dụng mặc định của Windows.

### Mở thư mục

Mở thư mục chứa file audio trong File Explorer.

### Xóa candidate

Xóa candidate MDX và các file Voice/Background tương ứng sau khi người dùng xác
nhận.

Candidate `Original Mix` không thể xóa vì đây là artifact cơ sở của Step 01.

Nếu candidate bị xóa đang được dùng cho Step 02 hoặc làm mặc định, state của
project sẽ loại bỏ tham chiếu không còn hợp lệ.

## 9. Nút Chạy step 01

Khi nhấn `Chạy step 01`:

1. Trình phát audio được dừng và giải phóng file đang mở.
2. Cấu hình provider được đọc từ giao diện.
3. Step chuyển sang trạng thái `ĐANG CHẠY`.
4. FFmpeg tạo hoặc tái sử dụng Original Mix.
5. Nếu dùng MDX, tool tạo worker subprocess riêng cho lần chạy.
6. Worker load runtime/model trên thiết bị đã chọn.
7. Kết quả hợp lệ được thêm vào danh sách candidate.
8. Project được cập nhật để các step sau có thể sử dụng output.

Trong lúc step đang chạy, nút chạy bị vô hiệu hóa để tránh khởi động hai worker
cùng lúc.

## 10. Quy trình sử dụng khuyến nghị

### Trường hợp cần tách Voice

1. Mở hoặc tạo project.
2. Chọn `MDX-Net — Audio Separator`.
3. Để FFmpeg ở `Auto` hoặc chọn đúng `ffmpeg.exe`.
4. Chọn model MDX.
5. Chọn `44.1 kHz` và `Stereo` nếu không có yêu cầu đặc biệt.
6. Chọn `Auto` hoặc `NVIDIA GPU (CUDA)`.
7. Nhấn `Chạy step 01`.
8. Chờ candidate mới xuất hiện.
9. Chọn `Voice`, nghe thử và so sánh với Original Mix.
10. Nhấn `Dùng cho Step 2`.
11. Nếu muốn pipeline luôn dùng candidate này, nhấn `Đặt làm mặc định`.

### Trường hợp không cần tách Voice

1. Chọn `Original Audio — FFmpeg`.
2. Chọn sample rate và số kênh.
3. Nhấn `Chạy step 01`.
4. Chọn stem `Original`.
5. Nhấn `Dùng cho Step 2` hoặc `Đặt làm mặc định`.

## 11. Vị trí file trong project

Original Mix:

```text
<project>\extracted\<ten_video>_source_audio.wav
```

Mỗi candidate MDX có thư mục riêng:

```text
<project>\audio_separation\mdx-<timestamp>-<id>\voice.wav
<project>\audio_separation\mdx-<timestamp>-<id>\background.wav
```

Model MDX được cache dùng chung cho TransLanguage:

```text
%LOCALAPPDATA%\TransLanguage\audio-separator-models
```

Xóa một candidate trên giao diện chỉ xóa thư mục candidate đó, không xóa model
cache hoặc các candidate khác.

## 12. Xác nhận GPU hoạt động

Sau khi chạy thành công, kiểm tra:

1. Tên candidate có `NVIDIA GPU (CUDA)`.
2. Thông tin audio hiển thị thiết bị CUDA.
3. Metadata có:

```text
actual_device: NVIDIA GPU (CUDA)
execution_provider: CUDAExecutionProvider
```

Trong Task Manager, đổi biểu đồ GPU từ `3D` sang `CUDA` hoặc `Compute_0`. Có thể
kiểm tra thêm bằng:

```cmd
nvidia-smi
```

CPU vẫn được dùng cho đọc file, chia chunk, NumPy và ghi WAV. CPU có hoạt động
không đồng nghĩa model đã fallback, miễn metadata xác nhận
`CUDAExecutionProvider`.

## 13. Lỗi thường gặp

### Không tìm thấy FFmpeg hoặc FFprobe

- Kiểm tra `ffmpeg -version` và `ffprobe -version` trong Command Prompt.
- Chọn trực tiếp `ffmpeg.exe` trong Step 01.
- Đảm bảo `ffprobe.exe` nằm cùng thư mục hoặc có trong `PATH`.

### NVIDIA GPU chưa sẵn sàng

- Kiểm tra `nvidia-smi`.
- Kiểm tra `torch.cuda.is_available()`.
- Kiểm tra profile CUDA đã được cài trong đúng `.venv`.
- Xem tài liệu `03_cai_dat_va_chay_tool_tren_may_khac.md`.

### Có CUDAExecutionProvider nhưng model vẫn không dùng GPU

Worker kiểm tra session model thực tế. Nếu ONNX không tải được DLL CUDA/cuDNN,
Step 01 sẽ báo lỗi hoặc `Auto` sẽ thử lại bằng CPU. Mở `Show Details…` để xem
diagnostic trước/sau load model.

### Không đủ VRAM hoặc RAM

- Đóng các ứng dụng đang dùng GPU.
- Thử model khác.
- Chọn `CPU` nếu GPU không đủ bộ nhớ.

### File audio không phát được

- Kiểm tra trạng thái `Sẵn sàng` trong phần thông tin audio.
- Nhấn `Mở file` hoặc `Mở thư mục` để xác nhận file còn tồn tại.
- Nếu file đã bị xóa bên ngoài tool, chạy lại Step 01 để tạo candidate mới.

### Không thể xóa candidate

`Original Mix` không thể xóa trên giao diện. Chỉ các candidate MDX có thể được
xóa.
