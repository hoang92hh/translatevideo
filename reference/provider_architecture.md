# Kiến trúc provider

Provider được cô lập dưới `video_translator/providers/`. Mỗi provider tự khai
báo `provider.py`, `config.py`, `manifest.py` và `requirements.txt`; thư mục
`tests/` dành cho kiểm thử riêng của provider khi được bổ sung sau này.

Các bước pipeline được nhóm tại `video_translator/steps/step1_*` đến
`step7_*`. Mỗi step sở hữu implementation `ui/`, `service.py`,
`interface.py`, `models.py` và `providers/`. Các module điều phối mock trong
`pipeline/` vẫn được giữ để phục vụ luồng mô phỏng hiện tại, nhưng không còn là
nơi sở hữu service nghiệp vụ.

UI dùng chung và service dùng chung chỉ phụ thuộc vào registry và manifest:

```text
shared UI/service -> providers.tts.registry -> provider manifest/config
                                             -> provider component/runtime
```

`manifest.py` là hợp đồng metadata ổn định. ID provider và schema dữ liệu phải
được version hóa khi có thay đổi không tương thích. Runtime riêng của Piper và
Melo được mô tả trong manifest, vì vậy việc chọn provider không sửa môi trường
của provider khác.

Dependency cài đặt của provider nằm trong `requirements.txt` riêng. Không commit
`.venv`, `.runtimes`, model weights, cache hoặc local config.

Khi cần cherry-pick, nên tách commit theo thứ tự: interface/schema, provider,
registry/service integration, UI integration và tài liệu. Module độc lập giúp
giảm conflict nhưng không loại bỏ conflict khi hai nhánh thay đổi cùng một
interface hoặc schema.
