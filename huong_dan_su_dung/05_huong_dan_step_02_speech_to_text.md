# Hướng dẫn Step 02 — Speech to Text

Step 02 dùng Faster Whisper để chuyển audio từ Step 01 thành các đoạn văn bản có
timestamp. Kết quả được lưu trong project và trở thành đầu vào của Step 03.

Dự án sử dụng PyAV 18.x vì Faster Whisper 1.2.1 chưa tương thích với thay đổi API
giải mã audio trong PyAV 19.

## 1. Chuẩn bị đầu vào

Hoàn thành Step 01 và chọn một trong hai loại stem:

- `Voice`: nên dùng khi MDX đã tách giọng rõ ràng.
- `Original`: dùng khi bản tách Voice làm mất tiếng nói hoặc tạo nhiễu.

`Background` không được dùng làm đầu vào nhận dạng. Có thể đổi candidate ngay
trong hộp **Audio input** của Step 02; đổi đầu vào sẽ làm mất hiệu lực kết quả từ
Step 02 trở về sau.

## 2. Cấu hình Faster Whisper

### Model

- `small`: nhanh, dùng ít RAM/VRAM, độ chính xác thấp hơn.
- `medium`: lựa chọn mặc định cân bằng tốc độ và chất lượng.
- `large-v3`: chất lượng tốt nhất trong danh sách, cần nhiều RAM/VRAM và thời gian hơn.

Lần chạy đầu tiên của một model cần Internet. Model được lưu tại:

```text
%LOCALAPPDATA%\TransLanguage\faster-whisper-models
```

### Thiết bị

- `Auto`: chỉ chọn NVIDIA GPU khi CTranslate2 phát hiện GPU và tải được CUDA,
  cuBLAS, cuDNN; nếu không sẽ chọn CPU. Trong lúc inference, Auto chỉ fallback
  CPU khi có lỗi CUDA hoặc thiếu VRAM.
- `CPU`: bắt buộc dùng CPU, không tự chuyển sang GPU.
- `GPU`: bắt buộc dùng NVIDIA GPU, không tự fallback khi cấu hình CUDA lỗi.

GPU của Step 02 cần cuBLAS 12 và cuDNN 9. Profile cài đặt `[cuda]` cung cấp hai
runtime này trong `.venv`, và worker tự đăng ký thư mục DLL. Đây là backend riêng,
không phụ thuộc việc PyTorch hoặc ONNX Runtime của Step 01 có nhận GPU hay không.
Với GPU Pascal như GTX 1060, dự án khóa cuBLAS `12.6.4.1` và cuDNN `9.6.0.74`.

#### Chọn profile theo GPU

- GTX 9xx/10xx (Maxwell/Pascal): giữ runtime đã khóa; GTX 1060 đã được xác minh.
- GTX 16xx và RTX 20xx (Turing): profile hiện tại tương thích.
- RTX 30xx (Ampere) và RTX 40xx (Ada): profile hiện tại là baseline; runtime mới
  hơn chỉ nên dùng sau khi kiểm tra driver và NVIDIA Support Matrix.
- RTX 50xx/Blackwell hoặc GPU tương lai: kiểm tra support matrix và cập nhật đồng
  bộ cuBLAS/cuDNN trong `pyproject.toml`; profile hiện tại chưa được xác minh.
- AMD/Intel GPU: chọn `CPU`; CTranslate2 CUDA không hỗ trợ các GPU này.

GTX 1060 và GTX 1660 không cùng kiến trúc dù đều mang tên GTX. Xem bảng đầy đủ
và quy trình đổi phiên bản trong
[`03_cai_dat_va_chay_tool_tren_may_khac.md`](03_cai_dat_va_chay_tool_tren_may_khac.md).

### Voice activity detection

VAD lọc các khoảng im lặng trước khi nhận dạng. Nên bật trong hầu hết trường hợp.
Nếu Step 02 báo không phát hiện lời nói dù audio có giọng nói, thử tắt VAD hoặc
chọn `Original` thay cho `Voice`.

## 3. Chạy nhận dạng

1. Kiểm tra audio đang chọn trong **Audio input**.
2. Chọn model, thiết bị và VAD.
3. Nhấn **Chạy step 02**.
4. Theo dõi giai đoạn tải model, phân tích audio và timestamp đang xử lý.

Không đóng ứng dụng trong khi worker đang chạy. Thời gian thực thi phụ thuộc độ
dài audio, model và thiết bị.

## 4. Kết quả

Mỗi dòng kết quả gồm:

- ID tăng dần từ `1`.
- Thời điểm bắt đầu và kết thúc theo giây.
- Nội dung nhận dạng ở cột `Source`.

Transcript được ghi tại:

```text
<project>\transcripts\stt-YYYYMMDD-HHMMSS-xxxxxx\transcript.json
```

Mỗi lần chạy thành công tạo một thư mục candidate mới và không ghi đè các lần
trước. File chứa đường dẫn audio đầu vào, ngôn ngữ, model, thiết bị yêu cầu/thực
tế, lý do Auto chọn thiết bị, compute type, thời lượng và danh sách segment.
`project.json` lưu danh sách candidate, output đang dùng cho Step 03 và output
mặc định để mở lại project và tiếp tục pipeline.

Candidate mới nhất chỉ được đăng ký sau khi `transcript.json` đã ghi thành công;
lúc đó nó tự trở thành input hiện tại và mặc định cho Step 03. Nếu nhận dạng hoặc
ghi file thất bại, input mặc định thành công trước đó không thay đổi.

Khối **Chọn output transcript** cho phép:

- Chọn một kết quả cũ và nhấn **Dùng cho Step 3**.
- Đặt kết quả đang xem làm mặc định.
- Mở file, mở thư mục hoặc xóa riêng một candidate.

Khi đổi transcript dùng cho Step 03, kết quả từ Step 03 trở về sau được đánh dấu
cần chạy lại. Nếu xóa candidate đang dùng, ứng dụng chuyển sang candidate hợp lệ
mới nhất còn lại; nếu không còn candidate thì Step 02 cần chạy lại.

Có thể sửa nội dung cột `Source` trên giao diện trước khi chạy Step 03. Việc sửa
sẽ cập nhật dữ liệu trong `project.json` và làm mất hiệu lực các step phía sau;
file artifact `transcript.json` của candidate đó vẫn là kết quả gốc của lần nhận
dạng tương ứng.

File legacy `<project>\transcripts\transcript.json` từ phiên bản cũ được giữ
nguyên và tự đăng ký thành candidate khi mở project; ứng dụng không tự di chuyển
hoặc xóa file này.

## 5. Lỗi thường gặp

### Thiếu Faster Whisper

Cài lại dependency bằng đúng Python của project:

```cmd
.venv\Scripts\python.exe -m pip install -e .
```

### Không tải được model

Kiểm tra Internet, proxy và firewall. Chạy lại sau khi kết nối ổn định; dữ liệu
đã tải một phần vẫn nằm trong cache model.

### GPU không khởi tạo được

Cài lại profile CUDA để khôi phục cuBLAS/cuDNN trong `.venv`:

```cmd
.venv\Scripts\python.exe -m pip install --upgrade -e ".[cuda]"
```

Sau đó đóng và mở lại ứng dụng. Với `Auto`, ứng dụng sẽ chọn CPU nếu runtime GPU
chưa sẵn sàng; với `GPU`, lỗi được báo và không fallback.

### Không đủ RAM hoặc VRAM

Đóng bớt ứng dụng, chọn model nhỏ hơn hoặc dùng CPU.

### Không phát hiện lời nói

Nghe lại audio từ Step 01, thử `Original`, tắt VAD hoặc tạo lại candidate Voice.
