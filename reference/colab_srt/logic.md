# Quy trình tạo segment SRT từ WAV trên Colab

Áp dụng quy trình này khi dùng [colab_srt_t4.py](./colab_srt_t4.py) để tạo phụ đề tiếng Trung từ WAV. Tài liệu mô tả hành vi của phiên bản hiện tại, gồm thứ tự xử lý, tham số, cách chọn nội dung và các trường hợp còn cần kiểm tra.

Khi tái sử dụng, đọc lại các giá trị trong script trước khi chạy. Các giá trị dưới đây là mặc định của phiên bản đang được mô tả. Nếu thay đổi script, cập nhật phần tương ứng trong tài liệu.

**1. Nhận thư mục WAV và chuẩn bị môi trường**

Chọn thư mục trong Colab rồi lấy các file WAV nằm trực tiếp trong thư mục đó. Sắp xếp theo tên và xử lý lần lượt từng file.

Script cài các phiên bản thư viện sau khi chạy ô Colab.

| Thành phần | Giá trị hiện tại |
|---|---|
| Stable-ts | `2.19.1` |
| Faster-Whisper | `1.2.1` |
| Silero VAD | `6.2.3` |
| PyAV | Nhỏ hơn `19` |
| Model nhận dạng | `large-v2` |
| Thiết bị | CUDA GPU |
| Kiểu tính toán | `float16` |
| Ngôn ngữ | `zh`, tiếng Trung |

Mỗi WAV có một thư mục kết quả riêng chứa tên file và thời điểm chạy. Kiểm tra file tồn tại, CUDA khả dụng và các tham số hợp lệ trước khi nhận dạng.

**2. Đọc và chuẩn hóa audio**

Đọc WAV thành mảng số thực `float32`. Nếu audio có nhiều kênh, lấy trung bình các kênh thành mono. Nếu tần số lấy mẫu khác 16.000 Hz, resample về 16.000 Hz.

Kiểm tra audio có dữ liệu và mọi mẫu âm thanh đều là số hữu hạn. Dừng xử lý file nếu WAV rỗng hoặc chứa mẫu không hợp lệ.

Toàn bộ các bước sau dùng mảng audio này làm hệ thời gian. Một giây tương ứng 16.000 mẫu âm thanh.

**3. Dùng VAD đánh dấu các vùng có tiếng nói**

Chạy Silero VAD trên toàn bộ audio và lưu danh sách thời gian nghi có tiếng nói vào `vad_regions`.

| Tham số | Giá trị | Ý nghĩa trong bước VAD |
|---|---:|---|
| `vad_threshold` | `0.35` | Ngưỡng để nhận một vùng là tiếng nói |
| `min_speech_duration_ms` | `100` ms | Thời lượng tiếng nói tối thiểu được giữ thành vùng VAD |
| `min_silence_duration_ms` | `200` ms | Khoảng im lặng dùng để tách các vùng VAD |
| `speech_pad_ms` | `50` ms | Phần mở rộng hai phía mỗi vùng VAD |

Giữ danh sách này để tìm khoảng có tiếng nói nhưng chưa được phụ đề phủ. Nhận dạng chính vẫn sử dụng toàn bộ audio gốc đã chuẩn hóa.

Nếu VAD lỗi hoặc trả về danh sách rỗng, ghi cảnh báo và tiếp tục bước nhận dạng.

**4. Để Whisper tạo các segment gốc và timestamp từng từ**

Đưa toàn bộ WAV vào `model.transcribe_original()`. Faster-Whisper xử lý audio theo các khối nội bộ có `chunk_length=25.0` giây. Nó trả về các segment do model xác định, mỗi segment gồm text, `start`, `end` và danh sách từ có timestamp.

Một khối xử lý 25 giây có thể sinh nhiều segment. Ranh giới segment gốc do kết quả nhận dạng và timestamp của model quyết định.

| Tham số | Giá trị hiện tại | Tác dụng |
|---|---|---|
| `without_timestamps` | `False` | Yêu cầu model sinh timestamp |
| `word_timestamps` | `True` | Lấy mốc bắt đầu và kết thúc từng từ |
| `vad_filter` | `False` | Đưa toàn bộ audio của lượt gọi vào nhận dạng |
| `no_speech_threshold` | `None` | Tắt điều kiện bỏ qua theo xác suất không có tiếng nói |
| `hallucination_silence_threshold` | `None` | Tắt cơ chế bỏ qua theo khoảng im lặng và nghi vấn hallucination |
| `condition_on_previous_text` | `False` | Các khối nhận dạng dùng ngữ cảnh text độc lập |
| `initial_prompt` | `None` | Dùng ngữ cảnh mặc định của model |
| `beam_size` | `5` | Số nhánh tìm kiếm khi giải mã bằng beam search |
| `repetition_penalty` | `1.0` | Hệ số phạt lặp ở mức mặc định trung tính |

Thử nhận dạng với `temperature=0.0` trước. Khi lượt này hoàn tất và có text, dùng kết quả đó. Nếu lượt đầu rỗng hoặc gặp lỗi, thử thêm với `temperature=0.2`. Mỗi lần thử được lưu trong `decode_attempts`.

Khi một lần thử trả về segment, danh sách đó trở thành kết quả đang được chọn. Nếu các lần thử chỉ trả về kết quả một phần, giữ phần đã thu được và đánh dấu `incomplete_recognition`. Code chọn theo kết quả các lần thực thi, chưa có bước chấm từng bản text để chọn bản đầy đủ nhất bằng đối chiếu âm thanh.

Lưu dữ liệu gốc của lượt chính trong `main-0001.json`. Độ đầy đủ và tính chính xác của text vẫn phụ thuộc vào nhận dạng. Các bộ lọc được nới có thể làm tăng nội dung nhận nhầm trong khoảng im lặng.

**5. Kiểm tra chất lượng timestamp của từng segment gốc**

Xử lý riêng từng segment Whisper trả về. So sánh text của segment với text ghép từ danh sách từ, bỏ qua khác biệt khoảng trắng khi so sánh.

Đánh dấu các trường hợp sau.

| Điều kiện | Cờ trong dữ liệu |
|---|---|
| Timestamp chứa NaN hoặc vô cực | `nonfinite_timestamp` |
| `start < -0.04`, `end` vượt thời lượng audio hơn `0.04` giây hoặc `end <= start` | `invalid_timestamp` |
| Một từ kéo dài hơn `2.0` giây | `long_word` |
| Từ bắt đầu sớm hơn cuối từ trước quá `0.08` giây | `overlapping_words` |
| Xác suất từ thiếu, không hữu hạn hoặc nhỏ hơn `0.15` | `low_probability` |

Nếu text khớp, có danh sách từ và các cờ chỉ gồm `low_probability`, giữ mốc từ của ASR. Cờ độ tin cậy thấp vẫn đi cùng segment để kiểm tra sau.

**6. Căn lại những segment có timing bất thường**

Khi thiếu mốc từ, text từ chưa khớp text segment hoặc có lỗi timing khác, lấy vùng audio từ `start - 0.5` đến `end + 0.5` giây của segment. Giới hạn vùng trích trong phạm vi audio đang xử lý.

Gọi forced alignment với text của chính segment để tìm lại vị trí phát âm từng từ trong vùng audio này.

Thử `token_step=100`, rồi thử `200` nếu vẫn còn vấn đề. Đây là số token căn trong một lượt nội bộ của thư viện. Cấu hình `remove_instant_words=False` giữ phần text có thời lượng bằng 0, `nonspeech_skip=None` giữ toàn bộ vùng audio để căn, còn `regroup=False` giữ cách xử lý từng segment. Hai tham số `suppress_silence=False` và `suppress_word_ts=False` tắt bước tự điều chỉnh theo im lặng sau alignment.

Chỉ chấp nhận bản alignment có text khớp với text segment sau khi bỏ khoảng trắng. So sánh bản gốc và các bản căn lại theo thứ tự ưu tiên sau.

1. Ít từ có timestamp không hợp lệ hoặc không hữu hạn hơn.
2. Ít từ bị đánh dấu vấn đề hơn.

Nếu điểm bằng nhau, giữ bản đã được chọn trước. Timestamp trong vùng trích được cộng vị trí bắt đầu vùng trích để trở về hệ thời gian của audio đang xử lý. Với lượt rà bổ sung, cộng tiếp vị trí bắt đầu lượt rà để trở về WAV gốc.

Nếu vẫn thiếu mốc từ dùng được, giữ text và `start/end` của segment ASR gốc, kèm cờ `missing_word_timing`. Trường hợp này cần nghe kiểm tra.

**7. Chuẩn bị danh sách từ để chia phụ đề ngắn**

Đi qua từng segment gốc độc lập, theo thứ tự thời gian. Giữ nội dung của các segment riêng biệt trong quá trình chia nhỏ.

Nếu một từ có `start == end`, giữ text của nó cùng từ đứng trước. Nếu nó nằm đầu segment, giữ text để ghép với từ có thời lượng hợp lệ tiếp theo. Phần text này dùng chung khoảng thời gian với từ lân cận và được đánh dấu `untimed_text_attached_to_neighbor`.

Nếu gặp timestamp không hữu hạn, thời gian đảo ngược hoặc danh sách từ sau xử lý chưa khớp text gốc, xuất một cue giữ text và thời gian segment ASR gốc. Cue này mang cờ cần kiểm tra và có thể dài hơn mục tiêu chia đoạn.

**8. Tạo các segment SRT ngắn từ mốc từng từ**

Bắt đầu một nhóm từ rỗng. Thêm lần lượt các từ trong segment gốc vào nhóm. Trước khi thêm một từ mới có chữ hoặc số, kiểm tra bốn điều kiện sau.

| Điều kiện | Hành động |
|---|---|
| Từ mới cách cuối từ trước ít nhất `0.3` giây | Kết thúc nhóm hiện tại, bắt đầu nhóm mới |
| Từ trước kết thúc bằng `。`, `？`, `！`, `?` hoặc `!` | Kết thúc nhóm hiện tại, bắt đầu nhóm mới |
| Thêm từ mới làm nhóm dài hơn `6.0` giây | Cắt trước từ mới |
| Thêm từ mới làm nhóm vượt `40` ký tự | Cắt trước từ mới |

Các tham số tương ứng trong phần cấu hình Colab là `SPLIT_GAP=0.3`, `MAX_SEGMENT_SECONDS=6.0` và `MAX_CHARS=40`.

Dấu câu đi cùng nhóm từ đang xử lý. Điểm cắt nằm giữa các từ có timestamp. Mỗi nhóm trở thành một segment SRT, được biểu diễn trong code bằng một `cue`.

- Text của cue là toàn bộ text từ trong nhóm, ghép theo thứ tự nhận dạng.
- `start` là mốc bắt đầu sớm nhất của các từ có chữ hoặc số trong nhóm.
- `end` là mốc kết thúc muộn nhất của các từ đó.
- Cue giữ ID segment gốc để truy nguyên nội dung.

Ví dụ, từ trước kết thúc ở `5.20` giây và từ sau bắt đầu ở `5.58` giây thì khoảng nghỉ là `0.38` giây. Với ngưỡng `0.3`, hai từ thuộc hai cue khác nhau. Cue trước kết thúc theo mốc từ trước, cue sau bắt đầu theo mốc từ sau.

Giới hạn 6 giây và 40 ký tự là mục tiêu khi có mốc từ phù hợp. Một từ đơn lẻ quá dài hoặc segment thiếu timing vẫn có thể vượt các giá trị này. Code đánh dấu `long_segment_needs_review` khi cue vượt `6.04` giây và `long_text_needs_review` khi text vượt 40 ký tự.

**9. Tìm vùng tiếng nói chưa được phụ đề phủ**

Lấy các khoảng `start/end` hợp lệ của cue đã tạo. Mở rộng phạm vi phủ mỗi cue thêm `0.08` giây hai phía để giảm báo thiếu do sai lệch nhỏ ở biên.

Đối chiếu phạm vi phủ với `vad_regions`. Nếu một khoảng trong vùng VAD chưa được phủ dài ít nhất `0.4` giây, lưu nó vào danh sách nghi thiếu.

Bước này kiểm tra độ phủ thời gian. Một cue có text nhận dạng thiếu vẫn có thể phủ kín vùng audio tương ứng. Cue đang có cờ nghi vấn cũng được tính vào độ phủ nếu `start/end` hợp lệ. Vì vậy, danh sách nghi thiếu phản ánh các khoảng thời gian cần rà thêm.

**10. Lập danh sách lượt nhận dạng bổ sung**

Tạo các khoảng audio cần đọc lại từ ba nguồn.

- Tối đa 15 giây đầu file.
- Tối đa 15 giây cuối file.
- Mỗi vùng nghi thiếu, mở rộng thêm 1 giây hai phía để model có ngữ cảnh.

Chia các khoảng dài thành cửa sổ tối đa 15 giây, chồng lấn 3 giây theo mặc định. Hai giá trị thực tế dùng `min(15.0, window_seconds)` và `min(3.0, overlap_seconds)`.

Loại các cửa sổ có cặp `start/end` giống hệt nhau và bỏ cửa sổ trùng toàn bộ WAV đã chạy ở lượt chính. Sắp xếp các cửa sổ còn lại theo thời gian rồi thực thi một lượt bổ sung.

Danh sách được lập trước khi rà. Các cửa sổ giao nhau vẫn được chạy riêng. Khi một lượt đã bổ sung được nội dung, các lượt còn lại trong danh sách vẫn tiếp tục chạy. Đây là nguyên nhân các dòng `Rà vùng thiếu` có thể chồng thời gian và số lượt rà có thể lớn.

**11. Chọn phần bổ sung được đưa vào SRT**

Mỗi cửa sổ bổ sung được nhận dạng và kiểm tra timing bằng cùng quy trình của lượt chính. Lưu dữ liệu vào `recovery-XXXX.json`.

Đi qua từng từ trong kết quả bổ sung. Chỉ lấy từ có timestamp hữu hạn, `end > start` và nằm ngoài phạm vi cue đã giữ. Khi so sánh, tính cả khoảng bảo vệ khoảng `0.04` giây ở biên cue.

Gom các từ liên tiếp đủ điều kiện thành segment bổ sung. Đưa segment đó qua quy tắc chia ngắn ở bước 8. Cập nhật phạm vi đã được bảo vệ để các lượt sau kiểm tra theo kết quả mới nhất.

Phần kết quả nằm chồng với cue đã có được lưu trong dữ liệu đối chiếu `alternatives`. Lượt chính được ưu tiên khi hai kết quả cùng phủ một khoảng thời gian. Bản nhận dạng bổ sung tốt hơn về text nhưng chồng timeline câu cũ cũng chịu quy tắc này.

Cách chọn này phụ thuộc vào độ chính xác của timing đã giữ. Khi timing câu cũ phủ rộng hơn lời nói thực tế, một phần lời nói bổ sung có thể bị giữ lại trong dữ liệu đối chiếu thay vì được xuất ra SRT.

**12. Kiểm tra toàn bộ cue sau lượt bổ sung**

Sắp xếp cue theo thời gian và đánh lại ID. Thực hiện các kiểm tra sau.

| Kiểm tra | Cách xử lý |
|---|---|
| Cue nằm ngoài mọi vùng VAD | Thêm cờ `outside_vad_speech` |
| Hai cue chồng thời gian quá `0.02` giây | Thêm cờ `overlapping_cues` cho cả hai |
| Text ghép từ cue khác text của segment nguồn sau khi bỏ khoảng trắng | Ghi `text_mismatch_segment_ids` |
| Một lượt nhận dạng thất bại | Ghi `failed_windows` và cảnh báo |
| Vẫn còn vùng VAD chưa được cue phủ | Ghi `uncovered_speech` |

So sánh text ở đây áp dụng cho các segment đã được chọn để xuất, gồm segment lượt chính và phần bổ sung đã nhận. Các bản text lưu trong `alternatives` được giữ để đối chiếu riêng.

Các kiểm tra cuối ghi nhận nghi vấn. Timeline và text đang được chọn vẫn được đưa vào SRT, kèm chi tiết cần kiểm tra trong báo cáo JSON.

**13. Ghi SRT, báo cáo và ZIP**

Chuyển timestamp sang định dạng `HH:MM:SS,mmm`, làm tròn đến mili giây. Ghi mỗi cue thành một mục SRT gồm ID, `start/end` và text.

Lưu `report.json` với cấu hình, phiên bản thư viện, các vùng VAD, kết quả nhận dạng, segment được chọn, cue cuối cùng, vùng nghi thiếu và các cờ cần kiểm tra. Các file JSON của từng lượt vẫn được giữ để truy nguyên.

Nếu còn cue nghi vấn, vùng nghi thiếu hoặc cảnh báo, đặt trạng thái `needs_review` và in thông báo `CẦN KIỂM TRA`. Nếu các kiểm tra tự động đi qua, đặt `automatic_checks_passed`. Trường `human_verified` luôn là `False` trong script hiện tại.

Nếu toàn bộ quá trình chưa tạo được cue nào, lưu báo cáo rồi báo lỗi. Khi tất cả WAV đã xử lý xong, tạo ZIP chứa các SRT và thư mục `diagnostics` với các file JSON.

Khi bàn giao kết quả, nêu số segment SRT, số cue cần kiểm tra và số vùng tiếng nghi thiếu. Dùng thông tin trong `issues` để phân biệt lỗi timing, độ tin cậy thấp, cue chồng thời gian và vùng ngoài VAD. Số cue cần kiểm tra có thể gồm nhiều loại vấn đề cùng lúc.
