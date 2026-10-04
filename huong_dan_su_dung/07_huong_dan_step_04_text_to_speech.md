# Step 04 — Text to Speech

Step 4 đọc output bản dịch đang được chọn ở Step 3 và tạo một file WAV mono PCM 48 kHz cho từng segment. Mỗi lần chạy thành công tạo thư mục riêng:

```text
generated_audio/<tts-candidate-id>/
├── manifest.json
└── segments/
    ├── segment_0001.wav
    └── ...
```

Output thành công mới nhất tự động trở thành input Step 5. Có thể chọn lại output cũ bằng nút **Dùng làm input Step 5**. Nếu lần chạy mới lỗi, candidate chưa hoàn chỉnh bị xóa và input mặc định trước đó không đổi.

## Nghe và kiểm tra output

Chọn một output TTS, sau đó chọn segment trong danh sách hoặc bấm trực tiếp vào dòng tương ứng trong bảng. Trình phát hỗ trợ đoạn trước/tiếp theo, phát/tạm dừng, dừng, tua và chỉnh âm lượng.

Việc nghe thử hoặc chuyển qua lại giữa các output không thay đổi pipeline. Chỉ nút **Dùng làm input Step 5** mới đặt output đang xem thành đầu vào của bước kế tiếp. Nếu chất lượng chưa đạt, có thể đổi model, giọng, tốc độ hoặc thiết bị rồi chạy lại Step 4; output thành công cũ vẫn được giữ để so sánh.

## Ba provider đã triển khai

### VieNeu-TTS — Local

- Dành cho tiếng Việt.
- `Auto`: dùng PyTorch/CUDA khi runtime nhìn thấy GPU; nếu không dùng CPU/ONNX.
- `CPU`: ép ONNX CPU.
- `GPU`: ép CUDA và báo lỗi nếu CUDA không khả dụng, không tự rơi về CPU.
- Giấy phép model/checkpoint Apache-2.0. Có thể dùng thương mại với giọng preset; nếu dùng audio tham chiếu, người dùng phải có quyền hoặc sự đồng ý của chủ giọng.

### MeloTTS + OpenVoice V2 — Local

- MeloTTS tạo giọng nền; khi có audio tham chiếu, OpenVoice V2 chuyển màu giọng.
- Hỗ trợ English, Spanish, Chinese, Japanese và Korean; không hỗ trợ tiếng Việt.
- `Auto`, `CPU`, `GPU` tuân theo lựa chọn giống quy tắc trên. `GPU` không fallback âm thầm.
- Cả hai dự án dùng giấy phép MIT, nhưng giấy phép phần mềm không thay thế quyền sử dụng giọng của một người thật.
- Runtime được cô lập ở `.runtimes/melo` vì dependency chính thức cũ hơn và có thể xung đột với Step 1–3.

Cài runtime riêng trên Windows (OpenVoice chính thức khuyến nghị Python 3.9):

```powershell
# Máy NVIDIA, gồm GTX 1060 hiện tại
powershell -ExecutionPolicy Bypass -File scripts/setup_melo_runtime.ps1 -Device cuda

# Máy chỉ dùng CPU
powershell -ExecutionPolicy Bypass -File scripts/setup_melo_runtime.ps1 -Device cpu
```

Có thể dùng runtime ở vị trí khác bằng biến `TRANSLANGUAGE_MELO_PYTHON`. Có thể đổi thư mục checkpoint bằng `TRANSLANGUAGE_OPENVOICE_CHECKPOINTS`.

### Edge TTS — Online

- Gọi dịch vụ giọng nói của Microsoft Edge qua Internet; không dùng CPU/GPU để suy luận trên máy.
- Không cần API key, nhưng cần mạng và có thể phụ thuộc thay đổi của dịch vụ.
- Đây không phải Azure Speech có SLA/hợp đồng API. Với sản phẩm thương mại, cần tự kiểm tra điều khoản hiện hành hoặc chuyển sang Azure Speech.

## XTTS-v2

XTTS-v2 chỉ xuất hiện dưới dạng provider bị khóa: **chưa triển khai**. Coqui Public Model License của checkpoint XTTS-v2 giới hạn việc dùng model cho mục đích phi thương mại, vì vậy không dùng provider này trong pipeline thương mại.

## Clone/chuyển giọng

Khi chọn audio tham chiếu, phải đánh dấu **Tôi có quyền sử dụng giọng**. Xác nhận này chỉ là chốt an toàn trên giao diện; người dùng vẫn chịu trách nhiệm lưu bằng chứng đồng ý và tuân thủ pháp luật/quyền hình ảnh, giọng nói tại nơi phát hành.
