# TransLanguage

Ứng dụng desktop cho pipeline phiên dịch và lồng tiếng video. Phiên bản hiện tại có quản lý project và xử lý thật toàn bộ pipeline: chuẩn bị audio ở Step 1, nhận dạng lời nói bằng Faster Whisper ở Step 2, dịch bằng Google Gemini ở Step 3, tạo giọng nói ở Step 4, đồng bộ thời lượng bằng FFmpeg ở Step 5, ghép voice timeline ở Step 6 và render video hoàn chỉnh ở Step 7.

## Chạy ứng dụng

Yêu cầu Python 3.11 trở lên.

Chọn một profile xử lý MDX phù hợp với máy. `Auto` trong ứng dụng sẽ chỉ dùng
GPU khi cả PyTorch CUDA và ONNX CUDA Execution Provider hoạt động:

```powershell
# Máy chỉ dùng CPU
python -m pip install -e ".[cpu]"

# Máy có NVIDIA GPU
python -m pip install -e ".[cuda]"

python main.py
```

Profile CUDA cài ONNX Runtime GPU cùng cuBLAS 12.6 và cuDNN 9.6 trong `.venv`.
Nếu `torch.cuda.is_available()` vẫn trả về
`False`, cài bản PyTorch CUDA phù hợp theo hướng dẫn chính thức của PyTorch.
Không cài đồng thời `onnxruntime`, `onnxruntime-gpu` và
`onnxruntime-directml` trong cùng environment. Khi đổi profile trên một máy đã
cài dependency, nên dùng virtual environment mới để tránh giữ lại package ONNX
Runtime của profile cũ.

Profile `[cuda]` hiện ưu tiên khả năng tương thích rộng và đã được xác minh trên
GTX 1060/Pascal. GTX 16xx thuộc Turing, không cùng kiến trúc với GTX 10xx; vì vậy
không nên chọn runtime chỉ dựa vào tên `GTX` hoặc `RTX`. Bảng kiến trúc và hướng
dẫn chọn phiên bản nằm trong
[`03_cai_dat_va_chay_tool_tren_may_khac.md`](huong_dan_su_dung/03_cai_dat_va_chay_tool_tren_may_khac.md).

Ghi chú kỹ thuật về vị trí prompt, payload, schema và chỉ dẫn gửi AI của Step 03/05
nằm tại [`translation_prompt_customization.md`](reference/translation_prompt_customization.md).

Với GPU NVIDIA Pascal như GTX 10xx, profile `cuda` khóa ONNX Runtime ở dòng
1.20.x để dùng CUDA 12.x. Worker chỉ báo `NVIDIA GPU (CUDA)` sau khi session của
model thực sự kích hoạt `CUDAExecutionProvider`; việc provider chỉ xuất hiện
trong danh sách khả dụng là chưa đủ.

Faster Whisper dùng CTranslate2 độc lập với PyTorch và ONNX Runtime. Step 2 chạy
được bằng CPU sau khi cài dependency cơ bản. Profile `[cuda]` cài cuBLAS/cuDNN
vào `.venv`; worker tự đăng ký thư mục DLL nên không yêu cầu sửa `PATH` hệ thống.
Chế độ `Auto` chỉ chọn GPU khi CTranslate2 nhìn thấy GPU và tải được đầy đủ
runtime; nếu không sẽ chọn CPU. Trong lúc chạy, Auto chỉ fallback CPU với lỗi
CUDA hoặc thiếu VRAM; lựa chọn `GPU` sẽ báo lỗi thay vì tự đổi thiết bị. Dự án
khóa PyAV dưới phiên bản 19 để tương thích Faster Whisper 1.2.1.

## Pipeline trên giao diện

1. Input & Extract
2. Speech to Text
3. Translation
4. Text to Speech
5. Audio Sync
6. Build Audio
7. Render & Export

Mỗi tab nhận kết quả của tab trước. Khi một step được chạy lại hoặc dữ liệu được chỉnh sửa, kết quả của các step phía sau sẽ được đánh dấu cần cập nhật và bị loại khỏi luồng hiện tại.

## Project workspace

Khi tạo project, người dùng chọn một thư mục input và đánh dấu các video nằm trực tiếp
trong thư mục đó. Ứng dụng không sao chép video nguồn. Danh tính video là đường dẫn
tuyệt đối cộng tên file; trạng thái `pending`, `processing`, `completed` hoặc `failed`
được lưu trong `.translanguage-videos.json` ngay tại thư mục input.

```text
<project>/
├── project.json
├── <video-name>/
│   ├── video.json
│   ├── extracted/
│   ├── audio_separation/
│   ├── transcripts/
│   ├── translations/
│   ├── generated_audio/
│   ├── synchronized_audio/
│   ├── built_audio/
│   ├── subtitles/
│   └── temp/
└── output/
    ├── <video-name>/
    │   ├── <render-candidate-1>/
    │   └── <render-candidate-2>/
    └── merged/
```

`project.json` lưu danh sách video, cấu hình batch và trạng thái cấp project. Mỗi
`video.json` giữ pipeline/candidate riêng của một video. Popup **Thiết lập project** gom
toàn bộ cấu hình Step 1–7 vào một màn hình lớn và được mở ngay trong luồng tạo project.
Các tab Step 1–7 dùng để kiểm tra output của từng bước và không còn nút chạy riêng. Nút
**Batch** hiển thị hàng đợi và bắt đầu xử lý tuần tự. Video lỗi được đánh dấu `failed` rồi
bỏ qua để chạy video tiếp theo. Step 4 tạm thời dùng một cấu hình giọng đọc chung cho mọi
speaker và video.

Batch dùng trạng thái xử lý riêng và không tự đổi video đang được xem trên các tab Step.
Nút **Batch** mở bảng chọn video và Step bắt đầu để chạy batch; nút **Videos** mở bảng
chỉ đọc gồm STT, Group, Step lỗi cao nhất, Step hoàn thành cao nhất trong Step 1–6,
số output tương ứng và số video Step 7 đã render còn tồn tại. Step hoàn thành cao nhất
được lấy từ toàn bộ output đã lưu, kể cả lượt trước; thiếu output ở Step tiếp theo
không tự được coi là lỗi.
**Thiết lập project → Project & Input** là nơi duy nhất chỉnh thứ tự video. Bảng giữ
checkbox chọn video và thêm cột **STT**; các nút **Di chuyển lên / Di chuyển xuống** nằm
phía trên bảng. Khi di chuyển, dòng được chọn và checkbox đi theo video, STT cập nhật ngay.
Bấm **Lưu thiết lập** (hoặc **Tạo project** khi tạo mới) để ghi nhận thứ tự; Cancel không lưu
các thay đổi thứ tự. Video mới được phát hiện/thêm nằm cuối danh sách hiện có.
Thứ tự chung lưu trong `project.json`, tiếp tục dùng khóa `step_batch_video_order` để giữ
tương thích và giữ nguyên các thứ tự đã lưu trước đây. Video không còn tồn tại trong project
được bỏ khỏi danh sách thứ tự; các video chưa có ID trong thứ tự được nối vào cuối.
Popup **Videos**, **Outputs**, **Chạy batch theo step**, select box **Video đang xem** và các
hàng đợi đều dùng cùng thứ tự này. Các popup không có nút di chuyển hoặc lưu thứ tự riêng.
Checkbox chọn chạy/chọn ghép và các thao tác nghiệp vụ khác vẫn giữ nguyên.
Nút **Chạy batch theo step** mở popup chọn video để chạy từng bước theo thứ tự chung.
Trong **Thiết lập project → Project & Input**, trường **Số video mỗi Group** mặc định là 10.
Group được tính trực tiếp từ STT chung: với kích thước 10, STT 1–10 thuộc Group 1,
11–20 thuộc Group 2. Khi đổi thứ tự hoặc kích thước Group, cột Group được tính lại;
liên kết cũ của Group có thành viên thay đổi bị bỏ, rồi ứng dụng tự liên kết lại nếu tìm thấy
`stories/story_group_N.md` tương ứng. Khi liên kết đã lưu thiếu hoặc trỏ tới file không còn tồn tại,
Step 3 và Step 2.5 cũng tự tìm file story theo số Group. Nội dung story cần được xem lại nếu thành viên Group đã đổi.
Bốn cột trạng thái Step 1&2, Step 3, Step 4&5, Step 6&7 có thể kéo chỉnh. Cột ghép hiển thị một trạng thái: ưu tiên step đang chạy, lỗi, cần cập nhật, chưa chạy; nếu cả hai step cùng trạng thái thì ghi step số thấp hơn, còn khi cả hai hoàn thành thì ghi "Hoàn thành". Tooltip vẫn hiển thị chi tiết từng step. Popup mở ở 90% vùng màn hình khả dụng; phần danh sách video và nút chạy chiếm khoảng 2/3 chiều cao, phần story chiếm khoảng 1/3 và có thể kéo thanh chia. Hàng nút đặt **Chạy lại từ 1–2**, combo chọn step và **Chạy step** bên trái; **Chạy Step 3**, **Chạy Step 4&5**, **Chạy Step 6&7** bên phải.
**Chạy Step 2.5** là lệnh độc lập cho các video tích chọn, không tự chạy trong chuỗi Step 1 → 3. Với mỗi video, ứng dụng chỉ tìm `<tên-video>.txt` trong thư mục input. Nếu có TXT UTF-8/UTF-16, ứng dụng dùng các dòng không trắng để đối chiếu text với segment của transcript Step 2 gốc theo thứ tự; file `transcript_rows.txt` cạnh `transcript.json` ghi mỗi dòng TXT không trắng trên một dòng với số dòng vật lý gốc (kể cả các dòng trắng đã bỏ qua khi đối chiếu). Nếu thiếu TXT, ứng dụng đọc story đã lưu của Group và gửi story cùng ID, timeline, source text của từng segment tới provider mặc định ở thiết lập Step 3. AI sửa chữ Trung bị ASR nhận sai và trả `new_content` theo đúng ID; ứng dụng kiểm tra phản hồi, đặt rỗng dòng ngắn hơn khi hai câu liền kề trùng trên 80% và câu dài hơn có trên 4 ký tự, rồi dùng danh sách đó như TXT giả lập. Với cặp segment trùng được xử lý sau mapping, segment giữ text nhận Start = End của segment đầu và End = End của segment sau; segment rỗng có ID nhỏ hơn nhận Start = End = Start của segment đầu, còn segment rỗng có ID lớn hơn nhận Start = End = End của segment sau. Nếu thiếu story hoặc AI lỗi, video được tính là lỗi và batch chuyển sang video tiếp theo. Khi chạy Step 2.5 lại từ một output đối chiếu, ứng dụng lần về transcript Step 2 gốc. Output Step 2 mới luôn được tạo, giữ bản gốc và được chọn; Step 3 chỉ chạy sau khi hết lỗi timeline. Kết quả batch báo số video thành công/lỗi và tóm tắt từng video.
Khi mở popup, ứng dụng nạp nền model local đã cấu hình cho Step 2 (gồm diarization nếu bật) và Step 4, giữ phiên qua các video rồi giải phóng khi đóng popup. Step 4&5 chạy hết video đã chọn ở Step 4 trước khi chạy Step 5 trên các video thành công; Step 6&7 hoạt động tương tự. Step 7 dùng FFmpeg và không nạp AI model.
**Chạy Step 1 → 3** chạy hết Step 1 cho các video tích chọn, sau đó hết Step 2, tạo story còn thiếu
từ transcript đã lưu của toàn bộ video trong các Group liên quan, chờ 10 giây bằng timer rồi mới
dịch Step 3 cho các video tích chọn. Video lỗi được ghi nhận và các video khác
vẫn tiếp tục. Popup khóa thao tác và không cho đóng trong toàn bộ chuỗi.
Sau Step 2, tác vụ nền gom `source_text` theo từng Group, không gửi speaker hoặc
timestamp, rồi dùng provider/model đang cấu hình ở **Thiết lập project → Step 3**
(Google Gemini hoặc ChatGPT Web) để viết một hồ sơ câu chuyện tiếng Việt cho mỗi Group.
Prompt tạo story nằm tại `reference/prompt_batch/google_gemini/story.md` hoặc
`reference/prompt_batch/chatgpt_web/story.md` theo provider đã chọn.
Story mới không thêm danh sách Group/video vào nội dung API trả về; story cũ được làm sạch
khối thông tin này khi nạp hoặc dùng để dịch. Batch Step 3 với Gemini ghép phần
`## PROMPT` của template đã chọn trong **Thiết lập project → Step 3** vào
`{topic_content}` của `reference/prompt_batch/google_gemini/translation.md`.
Tên file cố định là `stories/story_group_<group>.md`. Chuỗi Step 1 → 3 không tạo lại Group
đã có story hợp lệ. **Chạy lại từ 1–2** luôn tạo candidate và file Step 1 mới rồi
output Step 2 mới cho video đã tích, kể cả khi đã có output; nút này không tạo story
hoặc chạy Step 3.
Vùng Story có bộ chọn Group để xem, checkbox đánh dấu các Group cần tạo lại và nút
**Chạy lại**. Tác vụ này gom segment từ transcript đã lưu của toàn bộ video trong từng
Group đã đánh dấu. Story đang dùng chỉ được thay sau khi story mới tạo thành công.
Story ghi bối cảnh, cuộc trò chuyện, nhân vật chính/phụ, quan hệ, cách xưng hô, tên riêng,
địa danh, sự kiện và những điểm chưa chắc chắn. Transcript có lời thoại nhưng lỗi timeline
vẫn được dùng làm bối cảnh và ghi chú để kiểm tra trước khi dịch; video không có lời thoại
được ghi rõ là chưa được bao phủ. Nếu AI lỗi hoặc không có lời thoại, không tạo story trống
và không làm mất transcript đã lưu. Story mới ghi rõ STT, tên file và mã video nguồn để đối chiếu
kể cả khi thứ tự bảng được thay đổi sau đó.
Nút **Step 3** chạy tuần tự các video được tích chọn với story mới nhất được liên kết đúng Group.
Video thuộc Group chưa có story báo lỗi riêng và batch tiếp tục video kế tiếp.
Nếu story đang sửa, cần lưu hoặc bỏ thay đổi trước khi chạy. Nội dung story được chốt lúc bắt đầu
batch và điền vào mỗi prompt cùng STT/tên/mã video, lượt dịch và khoảng ID segment.
Khâu chuẩn bị dùng prompt riêng tại `reference/prompt_batch/google_gemini/translation.md`
hoặc `reference/prompt_batch/chatgpt_web/translation.md`: đọc story, phân tích đoạn hiện tại,
rồi dịch và tự kiểm tra trong cùng một request. Luồng chia segment, gửi/nhận, xác thực và lưu
output dùng chung Step 3 hiện tại; cấu hình `Segments / batch` và tùy chọn dịch toàn bộ một lượt
giữ nguyên ý nghĩa trong project. Output segment vẫn gồm `id`, `speaker_id`, `source_text`,
`translated_text`; metadata lưu tên story và video để đối chiếu. Luồng dịch ngoài popup vẫn
dùng `prompt_template`. Story trống/không tồn tại hoặc thiếu đầu vào Step 2 sẽ báo lỗi cho video đó.
Riêng Gemini trong popup trả object gồm `Noi_dung` và `segment_list`; ứng dụng lưu
tóm tắt đã xác thực và gửi vào batch kế tiếp. Batch đầu không có diễn biến trước.
ChatGPT Web tiếp tục dùng định dạng mảng segment hiện tại.
**Kiểm tra / sửa bản dịch** ở Step 3 mặc định chỉ liệt kê câu còn chữ Trung Quốc và cặp câu
dịch có nội dung liền kề giống từ 70% hoặc chứa nhau theo chuỗi từ đầy đủ. Khi so sánh, bỏ qua
hoa/thường, dấu câu, khoảng trắng thừa và segment rỗng do gộp/bỏ qua; giữ dấu tiếng Việt và thứ tự từ.
Độ giống tính bằng `2 × số từ khớp theo thứ tự / tổng số từ hai câu`. Popup hiển thị cả hai
câu nghi trùng cùng một câu có nội dung trước và một câu sau; các dòng ngữ cảnh ghi **Câu lân cận**.
Không tự gộp các cặp dịch giống hệt nhau để người dùng còn có thể đối chiếu. Nút **Đánh giá lại**
và thao tác lưu cập nhật danh sách cảnh báo; vẫn có lựa chọn hiển thị toàn bộ segment.
Đây là cảnh báo tham khảo: Step 3 vẫn hoàn thành, output vẫn là input hợp lệ cho Step 4.
Batch theo video dừng chuỗi tự động của video có cảnh báo sau Step 3, lưu checkpoint Step 4 rồi
chuyển video kế tiếp; thống kê cuối batch cho biết số video đã dừng để kiểm tra. Người dùng chạy
từ Step 4 bằng các thao tác hiện có, không bắt buộc sửa và không có nút tiếp tục mới.
Batch theo step vẫn lưu output Step 3, báo số video có cảnh báo và dịch tiếp video kế tiếp.
Popup hiển thị riêng các cột **Step 1–7** cho mỗi video: chưa chạy, đang chạy,
hoàn thành, lỗi hoặc cần cập nhật, dựa trên dữ liệu đã lưu. Di chuột lên ô để xem
chi tiết; nếu không đọc được dữ liệu, ô hiển thị **Không rõ**.
Checkbox **Chọn** dùng để chọn/bỏ chọn từng video; các nút **Chọn tất cả / Bỏ chọn tất cả**
nằm phía trên bảng. Lựa chọn đi theo video khi đổi thứ tự và chỉ giữ trong lần mở popup.
Các nút Step chỉ bật khi có video được tích chọn.
Popup chia theo chiều dọc: bảng video và các nút Step ở trên, vùng **Output Step 2 — Story**
ở dưới; kéo thanh phân cách để đổi chiều cao. Combobox liệt kê các Group và các file story
khác trong project. Có thể sửa nội dung và **Lưu story**; đổi story hoặc đóng khi chưa
lưu sẽ hỏi xác nhận. Story mới được tự chọn sau khi tạo xong; trong khi batch đang chạy,
vùng story chỉ cho đọc để tránh mất nội dung chỉnh sửa.
Select box **Video đang xem** chọn video cần kiểm tra output. Danh sách output tại Step 1–7
xếp mới nhất lên đầu theo `created_at` và mặc định hiển thị bản mới nhất mỗi lần nạp/làm mới,
kể cả khi đổi video hoặc chuyển tab. Tóm tắt và đường dẫn đi theo output đang xem. Người dùng
vẫn có thể chọn bản cũ để đối chiếu; việc hiển thị không tự đổi output đang dùng làm input
hay vô hiệu hóa step sau. Nhãn **Input Step …** giữ nguyên theo lựa chọn đã lưu.
Mỗi video lưu riêng kết quả gần nhất của **Chạy batch video đã chọn** trong các trường
`selected_batch_status`, `selected_batch_step`, `selected_batch_error`; batch theo step và
việc chọn output không thay đổi ba trường này. Khi chưa có lịch sử batch video đã chọn,
`output_count < 1` hiển thị **Chưa tạo · Step 04**, còn video đã có output render hiển thị
**Hoàn thành · Đã xong Step 07**. Sau khi đã chạy, bảng hiển thị đúng step đang xử lý,
step thực tế bị lỗi hoặc Step 07 hoàn thành của lần chạy gần nhất. **Step bắt đầu** cho phép
người dùng chọn Step 1–7 cho từng video. **Chạy batch video đã chọn** chạy đúng từ lựa chọn của người dùng đến Step 7,
không dùng step gợi ý để thay đổi điểm bắt đầu. Chọn Step 4 thì chạy Step 4–7; video đã xong
Step 7 vẫn có thể chạy lại từ bước được chọn. Lựa chọn chưa thiết lập để trống, cần chọn
cho mọi video đã tích trước khi chạy. **Lưu Step bắt đầu** hoặc nút chạy batch ghi lựa chọn
vào trường `batch_start_step` của từng video trong `project.json`; đóng khi chưa lưu sẽ hỏi
lưu hoặc bỏ thay đổi.
Trong popup **Batch**, **Chọn tất cả** và **Bỏ chọn tất cả** nằm phía trên bên trái;
select box **Step chung** phía trên bên phải chọn Step 1–7. Chỉ khi bấm **Set Step**
ngay bên phải select box, Step đó mới được áp dụng cho các video đang tích.
Vẫn có thể chỉnh Step riêng từng dòng. **Lưu Step bắt đầu** lưu cả Step và trạng thái
checkbox cuối cùng; nút chạy batch cũng tự lưu hai lựa chọn này trước khi chạy.
Thiếu đầu vào hợp lệ cho step đã chọn thì báo lỗi video đó rồi chuyển video kế tiếp,
không tự lùi về step thấp hơn. Chỉ kiểm tra các output làm đầu vào trước step
được chọn, không bắt buộc output cũ của bước sắp chạy lại phải còn hợp lệ. Với Step bắt đầu
do người dùng chọn, batch chỉ lấy output đang được đánh dấu **Input Step …** ngay trước step
đó; không lần ngược hoặc bắt buộc các candidate lịch sử khác còn cùng chuỗi. Sau đó pipeline
chạy tuần tự đến Step 7. Batch không lưu cưỡng bức state của video
đang xem trước khi đọc hàng đợi, tránh ghi đè checkpoint mới bằng state giao diện cũ. Nếu chuỗi
input trực tiếp bị lỗi, video dừng đúng tại step người dùng yêu cầu và không tự chạy lại step thấp hơn.
Thanh trạng thái hiển thị vị trí video và step thực tế trong lượt chạy. Thông báo cuối chỉ thống kê
video của lượt batch hiện tại theo dạng thành công/tổng số, đồng thời liệt kê tên video và step lỗi.
Việc mở bảng
chỉ đọc thông tin, không tạo workspace hay ghi manifest. Nút **Chạy batch** tự động và các nút
batch theo từng step vẫn giữ chế độ hiện có.

Nút **Outputs** hiển thị một row cho mỗi video. Select box trong row chọn phiên bản
render cần xem; checkbox chọn các phiên bản cần ghép. Các row được ghép theo thứ tự
chung đã lưu trong thiết lập project và tạo một MP4 mới trong `output/merged/` hoặc vị trí
do người dùng chọn. Để đổi thứ tự ghép, chỉnh thứ tự video tại **Project & Input** trước.

## Trạng thái hiện tại

- Pipeline được chạy tập trung bằng **Chạy batch**. Mỗi video giữ checkpoint riêng; nếu một Step lỗi, video được đánh dấu thất bại, batch chuyển sang video tiếp theo và lần chạy sau tiếp tục từ Step lỗi.
- Step đang chạy sẽ hoàn tất hoặc trả lỗi; giao diện không cung cấp thao tác dừng thủ công giữa chừng. Nhãn trên thanh đầu trang hiển thị vị trí của tab hiện tại, hoặc step liền trước nếu tab hiện tại chưa có kết quả.
- Giao diện và mock handler của mỗi step nằm trong module riêng.
- Cấu hình hiển thị theo provider được chọn.
- Bảng segment giữ ID xuyên suốt pipeline và cho phép chỉnh sửa nội dung.
- Step 1 dùng FFmpeg để tạo Original Mix và có thể dùng MDX qua `audio-separator` để tạo `Voice` + `Background`.
- MDX hỗ trợ `Auto`, `CPU` và `NVIDIA GPU (CUDA)`. Mỗi lần tách chạy trong một worker process riêng để có thể đổi thiết bị mà không cần khởi động lại ứng dụng. `Auto` chỉ chọn CUDA khi cả PyTorch và ONNX Runtime xác nhận backend CUDA hoạt động, nếu không sẽ dùng CPU.
- Mỗi lần chạy MDX tạo một candidate riêng trong `audio_separation/`; output thành công mới nhất tự trở thành input cho Step 2. Người dùng vẫn có thể nghe, so sánh và chọn lại candidate cũ bằng một thao tác duy nhất.
- Demucs và RoFormer đã có vị trí trong danh sách provider nhưng được đánh dấu chưa triển khai.
- Có thể tự tìm FFmpeg trong `PATH` hoặc chọn trực tiếp `ffmpeg.exe`; FFprobe trong cùng thư mục được dùng để tính tiến độ và thời lượng.
- Trạng thái project lưu output được người dùng chọn gần nhất ở mỗi step. Khi pipeline đầy đủ được triển khai, đó là input duy nhất được chuyển sang step kế tiếp.
- Khi chạy lại Step 1, audio player được giải phóng trước; MDX tái sử dụng Original Mix có cấu hình phù hợp thay vì ghi đè file đang nghe.
- Khi chạy MDX, ứng dụng tự đưa thư mục chứa FFmpeg/FFprobe đã chọn vào môi trường của provider; không bắt buộc cấu hình PATH toàn hệ thống.
- Step 2 dùng Faster Whisper với word timestamp và pyannote Community-1 chạy local để nhận dạng lời nói, gắn `speaker_id`, rồi làm mượt nhiễu ranh giới trước khi tạo segment. Một lần đổi speaker được giữ khi kéo dài ít nhất `0,8` giây hoặc chứa ít nhất 2 từ; khoảng `SPEAKER_UNKNOWN` và speaker đơn lẻ ngắn hơn cả hai ngưỡng được gán về speaker ổn định gần nhất. Tùy chọn **Segment recovery** mặc định tắt; chỉ khi bật cùng VAD, worker mới nhận dạng lại các vùng nghi thiếu lời thoại. VAD của lượt nhận dạng chính vẫn hoạt động khi tắt Segment recovery. Tùy chọn **Gộp segment liền kề cùng speaker** được bật mặc định; khi bật, các đoạn cùng speaker có độ lệch nhỏ hơn `0,01` giây được gộp, còn khi tắt thì từng đoạn nguyên tử được giữ riêng. Mọi segment đều lưu `merge_parts` gồm ID/timestamp gốc và tỷ lệ thời lượng; transcript cũng lưu `diarization_turns` thô để đối chiếu. Mỗi lần chạy thành công tạo một candidate riêng tại `transcripts/<candidate-id>/transcript.json`.
- Step 2 có nút **Nhập TXT/SRT thành output mới**. Với TXT, mỗi dòng không trắng thay text của một segment trong transcript đang chọn theo đúng thứ tự; số dòng phải bằng số segment, còn ID/timestamp/speaker được giữ nguyên. Với SRT, mỗi mục phụ đề tạo một segment có text và timestamp từ file; ID được cấp theo thứ tự, speaker là `SPEAKER_UNKNOWN`, không cần transcript Step 2 có sẵn. Cả hai cách đều tạo candidate transcript mới, giữ output cũ và chọn output mới làm input Step 3. File sai định dạng, sai số dòng, thời gian chồng nhau hoặc text trống sẽ có thông báo lỗi cụ thể.
- Step 2 có **Xuất CSV** và **Nhập CSV thành output mới** cho transcript đang chọn. CSV UTF-8 BOM gồm đúng 5 cột `ID, Speaker, Start, End, Source`. Có thể sửa nội dung và thời gian rồi nhập lại; phải giữ nguyên đầy đủ các ID, không thêm hoặc xóa segment. File nhập tạo candidate mới và được chọn làm input Step 3; các mốc thời gian chưa hợp lệ tiếp tục được đánh dấu trong **Kiểm tra / sửa timeline**.
- Nếu Step 1–4 gặp lỗi không tạo được output hợp lệ, candidate chưa hoàn chỉnh không được đăng ký và input thành công trước đó được giữ nguyên. Riêng Step 5 giữ candidate `needs_edit` để người dùng sửa từng segment, nhưng không chọn candidate đó làm input Step 6 cho tới khi hết lỗi.
- Step 3 dùng workflow dịch độc lập provider. Gemini giữ tùy chọn chất lượng ba giai đoạn: phân tích toàn truyện, dịch theo batch và kiểm duyệt kết quả. Với ChatGPT Web và Claude Web, checkbox **Dịch toàn bộ segment của project trong một lượt** được bật mặc định: toàn bộ segment được gửi trong một file JSON và nhận một file kết quả; khi bỏ chọn, ứng dụng chia tuần tự theo `Segments / batch`. Mỗi file web được AI đọc trọn vẹn để phân tích ngữ cảnh trước khi dịch, được extension tải xuống và được ứng dụng xác thực đủ ID trước khi gửi lượt kế tiếp. Quy tắc tên riêng mặc định tự thích ứng theo cặp ngôn ngữ: Chinese → Vietnamese ưu tiên Hán–Việt; Chinese → ngôn ngữ khác ưu tiên Pinyin; người dùng có thể ép Hán–Việt, Pinyin hoặc giữ chữ gốc. Step 3 có giao diện tab cho Google Gemini API, ChatGPT Web và Claude Web; hai web provider dùng Chrome extension qua bridge localhost, còn model được chọn trực tiếp trên trang. Structured output giữ nguyên ID, timestamp, `speaker_id` và `merge_parts`; mỗi lần thành công tạo `translations/<candidate-id>/translated_segments.json` làm input Step 4 và `dialogue_profile.json` để tham chiếu vai trò/giọng cho từng speaker.
- Tại Step 3, chọn một output rồi bấm **Xuất CSV** để chỉnh danh sách segment bên ngoài. CSV có các cột `STT, ID, Start, End, Speaker, Translation, Source`; `STT` chỉ để tham khảo. Muốn thêm segment, chèn một hàng với `ID` và `Source` để trống, nhập mốc thời gian tính bằng giây, `Speaker` và `Translation`. **Nhập CSV thành output mới** tạo một candidate JSON riêng, tự cấp ID cho hàng mới, giữ output cũ và chọn candidate mới làm input Step 4. Các đoạn có lời không được chồng thời gian; sau khi nhập có thể chạy lại Step 4 → 7. Segment nhập mới chỉ thuộc Step 3 trở đi, không ghi ngược transcript Step 2.
- Google API key được quản lý tập trung tại **Cài đặt → API & Providers** và lưu trong Windows Credential Locker. Speaker diarization chỉ đọc model local được chọn trong cùng màn hình; phần nhận dạng/diarization Step 2 không cần Hugging Face token và không gọi mạng khi xử lý. Riêng tác vụ tạo story sau batch Step 2 gửi lời thoại đến provider AI đã cấu hình.
- Faster Whisper hỗ trợ `small`, `medium`, `large-v3`, VAD và ba chế độ `Auto`, `CPU`, `GPU`. Model được lưu tại cache riêng của TransLanguage trong `%LOCALAPPDATA%`.
- Profile CUDA khóa cuBLAS `12.6.4.1` và cuDNN `9.6.0.74` để tiếp tục hỗ trợ GPU Pascal như GTX 1060.
- Lỗi provider được hiển thị theo nhóm nguyên nhân, kèm hướng xử lý và phần chi tiết kỹ thuật có thể mở rộng.
- Step 4 tách từng TTS thành component riêng, chọn bằng các nút dạng tab con và có thể đặt một provider làm mặc định cho các lần mở ứng dụng sau. Các provider gồm VieNeu-TTS local cho tiếng Việt, Piper TTS local CPU/ONNX, MeloTTS + OpenVoice V2 local cho English/Spanish/Chinese/Japanese/Korean và Edge TTS online. `SPEAKER_00`, `SPEAKER_01`, `SPEAKER_02` có cấu hình giọng/model riêng; mọi speaker còn lại dùng chung một cấu hình. VieNeu và Melo/OpenVoice dùng audio tham chiếu; Piper dùng cặp voice model `.onnx` + `.onnx.json`, hỗ trợ `speaker_id` của model đa giọng và các tham số `SynthesisConfig` native; Edge dùng voice preset. Ánh xạ này được giữ khi Step 5 tạo lại voice. Mỗi lần chạy thành công tạo một candidate riêng trong `generated_audio/`; candidate mới nhất tự động là input Step 5, còn lỗi không thay đổi input trước đó.
- `Auto` ở provider local chọn GPU khi runtime CUDA của chính provider khả dụng, nếu không chọn CPU. `CPU` và `GPU` tuân thủ đúng lựa chọn; chế độ `GPU` báo lỗi thay vì fallback CPU.
- XTTS-v2 chỉ nằm trong danh sách dưới dạng chưa triển khai và có ghi chú chỉ phi thương mại theo Coqui Public Model License.
- Step 5 cắt khoảng lặng thừa, tận dụng khoảng trống trước segment kế tiếp, tăng tốc trong giới hạn người dùng chọn và chèn khoảng lặng khi audio ngắn hơn timestamp gốc. Trong **Thiết lập project → Step 05**, tùy chọn **Auto Extend Video to Match TTS** có thể lập kế hoạch kéo dài cục bộ bằng `Freeze Frame`, `Slow Motion` hoặc `Hybrid`; chỉ đoạn có voice vượt khung bị biến đổi, còn các đoạn sau chỉ được dịch timestamp. Mỗi lần chạy tạo candidate riêng trong `synchronized_audio/`.
- Timeline Step 2 là dữ liệu nhận dạng gốc và chỉ làm tham chiếu đầu vào; sửa voice/timeline tại Step 5 không ghi ngược vào manifest Step 2, Step 3 hoặc Step 4. Candidate Step 5 lưu `source_start/source_end` cùng timeline hiệu lực riêng. Voice tạo lại nằm trong thư mục `repairs/` của candidate Step 5, không ghi đè audio Step 4. Nội dung rỗng được xem là chủ động bỏ voice: interval Step 5 được thu về `start = end`, không cần audio và không tính là lỗi; Step 6 tự bỏ qua segment đó nhưng vẫn giữ ID trong manifest.
- Với segment vẫn quá dài, popup sửa lỗi có nút **Vay thời gian lân cận** riêng. Nút này ưu tiên cân B+C; chỉ dùng A+B+C (hoặc A+B khi không có C) nếu thời gian phía sau chưa đủ. Các voice trong nhóm dùng cùng tốc độ tối đa đã cấu hình, giữ nguyên thứ tự, không chồng nhau, lệch tối đa 0,5 giây và không vay qua khoảng nghỉ lớn hơn 1 giây.
- Segment không thể đặt vừa trong giới hạn tốc độ cùng segment liền trước/sau được đưa vào popup xử lý của Step 5. Nút **Sửa voice/speaker segment khác** mở cùng popup với toàn bộ segment và cho tìm trong nội dung bản dịch; mỗi kết quả kèm segment liền trước/sau. Checkbox, speaker và nội dung nháp được giữ qua nhiều lần tìm. Mọi row đều có thể đổi speaker, sửa bản dịch hoặc chọn để Gemini chỉnh sửa. Checkbox đồng thời quyết định chính xác các row được tạo lại voice; kết quả cập nhật speaker/nội dung/audio trực tiếp trong chuỗi candidate Step 2–5 hiện tại mà không đổi `id`, timestamp hoặc `merge_parts`. Candidate Step 5 chỉ trở thành input Step 6 sau khi hết lỗi.
- Step 6 đọc `adjusted_start`/`play_duration`, kiểm tra lại không chồng voice và ghép các segment thành một track WAV mono 48 kHz bằng FFmpeg. Track được bù khoảng lặng tới thời lượng timeline mới do Step 5 tính (hoặc video gốc khi không kéo dài). Step này chỉ xử lý voice, không trộn background hoặc video.
- Step 7 render MP4 H.264/AAC từ hình ảnh video gốc và voice track Step 6. Khi Step 5 có kế hoạch kéo dài, Step 7 mới thực sự slow/freeze đúng các đoạn cục bộ và retime voice gốc/background tương ứng; file nguồn không bị sửa. Subtitle SRT dùng timeline voice đã căn chỉnh; burn subtitle là tùy chọn.
- Local Model ở Step 3 được đánh dấu chưa triển khai.

Hướng dẫn chi tiết:

- [`huong_dan_su_dung/07_huong_dan_step_04_text_to_speech.md`](huong_dan_su_dung/07_huong_dan_step_04_text_to_speech.md)
- [`huong_dan_su_dung/08_huong_dan_step_05_audio_sync.md`](huong_dan_su_dung/08_huong_dan_step_05_audio_sync.md)
- [`huong_dan_su_dung/09_huong_dan_step_06_build_audio.md`](huong_dan_su_dung/09_huong_dan_step_06_build_audio.md)
- [`huong_dan_su_dung/10_huong_dan_step_07_render_export.md`](huong_dan_su_dung/10_huong_dan_step_07_render_export.md)
