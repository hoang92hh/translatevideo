# Build bộ ứng dụng Windows

Thư mục phát hành chỉ có `extension`, `huong_dan_su_dung`, `models`, `reference`, `tool`, `setup.exe` và `translatevideo.exe`. Code giao diện và worker được đóng trong `translatevideo.exe`; cấu hình dependency được đóng trong `setup.exe`. `environment` được tạo trên máy nhận sau khi chạy setup, không đưa vào file nén.

## Tạo hoặc cập nhật translatevideo.exe

Sau khi sửa tính năng thuần code, chạy tại gốc repository:

```powershell
.\environment\app\.venv\Scripts\python.exe -m pip install pyinstaller
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_application.ps1 -Target app
```

File mới: `build_application\release\translatevideo.exe`. Nếu dependency không đổi, người nhận chỉ cần thay file exe rồi mở lại ứng dụng. Worker được lấy từ mã trong exe, không có file worker rời trong gói phát hành.

## Tạo hoặc cập nhật setup.exe

Sau khi thay đổi `pyproject.toml` hoặc quy tắc cài đặt trong `scripts\translatevideo_setup.py`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_application.ps1 -Target setup
```

File mới: `build_application\release\setup.exe`. Máy nhận cần chạy setup mới để có dependency mới. Setup yêu cầu Windows x64, Python 3.13 x64 với `py` launcher và Internet. Setup tạo `environment` cạnh hai exe, phát hiện NVIDIA và thử profile CUDA 12.6; nếu CUDA không đạt thì tạo lại môi trường CPU. Chạy `setup.exe --cpu` để chọn CPU ngay từ đầu.

Setup không ghi đè `environment` đã tồn tại. Muốn cài lại, đóng app rồi đổi tên hoặc chuyển môi trường cũ trước. File `environment\profile.txt` chỉ được tạo khi cài thành công.

## Bố cục gửi cho người khác

```text
release/
  extension/
  huong_dan_su_dung/
  models/
  reference/
  tool/ffmpeg/bin/ffmpeg.exe
  tool/ffmpeg/bin/ffprobe.exe
  setup.exe
  translatevideo.exe
```

Chạy cả hai lệnh build ít nhất một lần trước khi nén `release`. Script chép các thư mục tài nguyên vào `release` nếu chưa có; khi sửa tài liệu, extension hoặc reference, hãy sao chép lại thư mục tương ứng. `models` để trống nếu chưa có model cần phân phối. Không đưa `video_translator`, `pyproject.toml`, `scripts`, `translanguage.egg-info`, `.git`, `environment` từ máy build vào file nén.

Ứng dụng sử dụng `reference` và `models` ở cạnh exe. Chrome extension cần được nạp thủ công. Runtime riêng của Piper/Melo chưa được setup này cài; hai provider đó cần cách cài bổ sung trước khi sử dụng. Bản cài hiện là online, GPU CUDA tự động dùng nhánh 12.6 đã kiểm chứng; máy không tương thích sẽ dùng CPU.
