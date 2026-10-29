# Logic xử lý từ Step 1 đến Step 7

## Ghi chú quan trọng

- Tài liệu mô tả **luồng đang được triển khai trong mã nguồn**, không phải kết quả đo thời gian của một project cụ thể. Model thực tế phụ thuộc cấu hình đã chọn; candidate/manifest của từng lần chạy ghi lại model, thiết bị và đầu ra tương ứng.
- Step 1 có thể chỉ dùng FFmpeg hoặc dùng model MDX; Step 2–4 dùng model/nhà cung cấp được chọn. Step 5–7 **không dùng AI model trong luồng chính** mà chủ yếu dùng FFmpeg. Công cụ sửa lỗi tùy chọn ở Step 5 có thể gọi lại model dịch và TTS.
- Sai nội dung, thiếu câu hoặc sai timestamp ở Step 2 có thể lan sang Step 3–7. Step 5 chỉ lập kế hoạch kéo dài hình; Step 7 mới thực sự render hình theo kế hoạch đó.
- **Step 7 hiện luôn mã hóa lại toàn bộ hình ảnh bằng H.264**, kể cả khi không cần chỉnh thời gian hình và không burn subtitle. Chức năng nối video trong Outputs là luồng riêng, không phải Step 7.
- Các nhận định về điểm nghẽn và cải tiến bên dưới là suy luận từ code; cần đo trên cùng bộ video và thiết lập để xác định mức lợi ích thực tế.

## Step 1 — Chuẩn bị và tách âm thanh

- **Đầu vào:** video nguồn; chế độ Original Audio hoặc MDX-Net; sample rate, số kênh và thiết bị xử lý.
- **Xử lý:** FFmpeg trích âm thanh thành WAV Original Mix. Nếu chọn MDX-Net, hệ thống tách tiếp thành voice và background. Khi Original Mix hiện có phù hợp sample rate/số kênh, nhánh MDX có thể tái sử dụng thay vì trích lại.
- **Model/engine:** Original Audio **không có AI model**, chỉ FFmpeg. Nhánh tách âm dùng **MDX-Net**, mặc định `UVR-MDX-NET-Inst_HQ_4.onnx`; UI còn có `UVR-MDX-NET-Voc_FT.onnx` và `Kim_Vocal_2.onnx`. Demucs và RoFormer chưa triển khai.
- **Đầu ra:** Original Mix WAV; nếu dùng MDX, thêm voice WAV và background WAV dưới dạng các audio candidate.
- **Điểm cần xem xét:** thời gian tải/chạy MDX; chất lượng voice tách ra ảnh hưởng trực tiếp khả năng nhận dạng ở Step 2. Nên đo riêng thời gian trích FFmpeg và thời gian tách MDX trước khi tối ưu.
- **Nguồn code:** [pipeline Step 1](../video_translator/pipeline/mock_steps/extract.py), [cấu hình UI](../video_translator/steps/step1_separation/ui/page.py).

## Step 2 — Nhận dạng lời nói và speaker

Ngoài nhận dạng audio, Step 2 có một nút nhập file `.txt` hoặc `.srt` để tạo transcript candidate mới. TXT đọc từng dòng không trắng và thay text theo thứ tự các segment của transcript đang chọn; số dòng phải khớp, ID/Start/End/Speaker giữ nguyên. SRT đọc riêng từng mục phụ đề, dùng timestamp và text trong file để tạo segment mới với ID liên tiếp và `SPEAKER_UNKNOWN`; các mục không được chồng thời gian. Khi nhập thất bại, ứng dụng báo file và nguyên nhân; output cũ không bị thay đổi. Khi thành công, output mới được chọn làm input Step 3.

- **Đầu vào:** audio được chọn từ Step 1; ngôn ngữ nguồn, model, CPU/GPU, VAD, Segment recovery và tùy chọn speaker diarization.
- **Xử lý:** Faster-Whisper nhận dạng lời nói, xuất câu cùng timestamp từng từ. VAD mặc định bật. Segment recovery mặc định tắt; khi bật cùng VAD và có thời lượng audio, worker rà các vùng nghi bị bỏ sót và thử nhận dạng phục hồi. Tắt Segment recovery không tắt VAD của lượt nhận dạng chính. Nếu bật diarization, hệ thống gán speaker theo vùng tiếng nói, làm mượt các lần đổi speaker; có tùy chọn gộp những đoạn liền kề.
- **Model/engine:** **Faster-Whisper** (mặc định `medium`; UI có `small`, `medium`, `large-v3`). Diarization là tùy chọn, dùng model local **`pyannote-speaker-diarization-community-1`**. FFmpeg/decoder hỗ trợ đọc audio. Thiết bị thực tế có thể là CPU hoặc GPU theo cấu hình và khả năng runtime.
- **Đầu ra:** `transcript.json` với ID, `start`, `end`, `speaker_id`, câu gốc, `merge_parts` và metadata về ASR/recovery/diarization.
- **Điểm cần xem xét:** đây là điểm kiểm soát chất lượng quan trọng nhất trước các bước sau. Cần phát hiện lời nói chưa được ghi, câu rất ngắn nhưng chiếm khoảng thời gian bất thường, timestamp lệch và gán speaker sai. Model lớn hoặc diarization tăng thời gian xử lý; chạy lại trên CPU cũng có thể chậm hơn đáng kể.
- **Nguồn code:** [pipeline Step 2](../video_translator/pipeline/mock_steps/speech_to_text.py), [worker Faster-Whisper](../video_translator/steps/step2_transcription/providers/faster_whisper/worker.py), [cấu hình diarization](../video_translator/config/diarization.py).

### Khi segment phục hồi đè lên timeline của segment chính

Các bước dưới đây chỉ chạy khi bật **Segment recovery** và **VAD** trong thiết lập Step 2. Project cũ chưa lưu tùy chọn Segment recovery sẽ mặc định tắt cho lần chạy tiếp theo. Transcript ghi lựa chọn này ở `segment_recovery` và trạng thái chạy thực tế ở `asr_recovery.enabled`.

1. **Tìm vùng nghi thiếu:** khi bật VAD, hệ thống lấy khoảng thời gian của từng từ đã nhận dạng (mở rộng mỗi đầu 0,15 giây; nếu không có mốc từ thì dùng cả segment) làm vùng đã phủ. Các khoảng bị VAD loại hoặc khoảng có tiếng nói nhưng chưa được phủ, dài ít nhất **1 giây** và có hoạt động âm thanh, trở thành vùng ứng viên. Hệ thống mở thêm ngữ cảnh **0,75 giây** mỗi bên và nhận dạng lại bằng Faster-Whisper, không dùng VAD cho lượt phục hồi.
2. **Giới hạn vào vùng cần phục hồi:** kết quả nhận dạng lại phải giao với vùng ứng viên ít nhất **25% thời lượng** của chính kết quả đó. `start/end` được **cắt về ranh giới vùng ứng viên**; code vẫn giữ thêm `alignment_start/end` là phạm vi gốc của các từ liên quan để kiểm tra về sau. Đây là cắt timestamp của ứng viên, **không** sửa segment chính.
3. **Lọc ngay trong worker:** bỏ kết quả có bằng chứng âm thanh yếu/không chắc theo các điều kiện trong code. Bỏ ứng viên nếu phần đè lên **một segment chính hoặc một ứng viên đã nhận** chiếm ít nhất **35% thời lượng ứng viên**, dù nội dung chữ khác. Ngoài ra, nếu chữ giống nhau hoặc độ tương đồng từ **0,82**, ứng viên bị coi là trùng khi mức đè đạt **45% thời lượng đoạn ngắn hơn**, hoặc hai ranh giới chỉ cách nhau tối đa khoảng **0,15 giây**. Lượt này nhằm tránh nhận lại lời đã có từ cửa sổ ngữ cảnh.
4. **Kiểm tra hậu xử lý trong service:** so lại từng ứng viên với **từng segment chính**. Nếu mức đè lớn nhất lên một segment chính **vượt 0,1 giây**, ứng viên bị loại. Khi bật diarization, kiểm tra lần đầu dùng phạm vi từ gốc `alignment_start/end` (trước khi cắt), sau khi dựng lại segment theo speaker còn kiểm tra lần nữa bằng `start/end` cuối. Khi không bật diarization vẫn có kiểm tra trước và sau bằng `start/end`.
5. **Kết quả:** chỉ các segment phục hồi vượt qua các lớp lọc mới được ghép với segment chính, sắp theo thời điểm bắt đầu rồi đánh lại ID trong transcript. Segment phục hồi bị loại **không được tự động dời sang khoảng trống, không thay thế và không rút ngắn segment chính**. Metadata `asr_recovery` ghi `rejected_duplicate_count`, `rejected_postprocess_overlaps`, `final_accepted_segment_count` để đối chiếu; các ứng viên bị loại ở worker vì trùng không nằm trong `rejected_postprocess_overlaps`.

**Hệ quả cần lưu ý:** nếu timestamp của segment chính sai và bao trùm vùng thực sự có câu bị thiếu, câu phục hồi có thể bị loại vì “đè timeline”, dù nghe audio gốc có lời. Trường hợp đó cần sửa/đánh giá lại segment chính và mốc thời gian nguồn; chỉ nới ngưỡng lọc trùng có nguy cơ tạo câu lặp. Chi tiết triển khai ở [lọc phục hồi trong worker](../video_translator/steps/step2_transcription/providers/faster_whisper/worker.py) và [kiểm tra overlap sau xử lý](../video_translator/steps/step2_transcription/service.py).

## Step 3 — Dịch văn bản

Để chỉnh output bằng bảng tính, chọn một candidate ở Step 3 và xuất CSV. Sửa các cột `ID, Start, End, Speaker, Translation, Source`; cột `STT` không dùng khi nhập. Hàng thêm mới có thể để trống `ID` để ứng dụng cấp ID duy nhất và để trống `Source` nếu chỉ cần sinh voice. Nhập lại CSV sẽ tạo `translated_segments.json` trong một candidate mới, giữ candidate nguồn. Output mới được chọn làm input Step 4; chạy lại Step 4 → 7 để tạo voice và video. Với hàng mới chỉ có từ Step 3, sửa voice ở Step 5 không yêu cầu ID phải có trong transcript Step 2.

- **Đầu vào:** các segment của transcript Step 2, cặp ngôn ngữ, provider/model, prompt và kích thước batch.
- **Xử lý:** gửi toàn bộ segment của file trong một batch hoặc chia batch theo cấu hình; kèm ngữ cảnh trước/sau và các bản dịch trước đó. Kết quả được kiểm tra ID rồi gắn bản dịch vào segment. Segment có bản dịch rỗng được đặt `start = end` để các bước sau bỏ qua. Với Gemini, thông tin request/response/error được lưu trong thư mục diagnostics.
- **Model/engine:** mặc định **Google Gemini `gemini-3.5-flash-lite`**; có các model Gemini khác trong cấu hình và provider **ChatGPT Web / Claude Web**. Model thực tế là lựa chọn của lần chạy, được ghi trong translation candidate.
- **Đầu ra:** `translated_segments.json` và `dialogue_profile.json`. Workflow hiện chạy lượt dịch chính; số lượt analysis/review trong metadata bằng 0.
- **Điểm cần xem xét:** kiểm soát thiếu/trùng ID và độ đúng nội dung quan trọng hơn việc so khớp từng ký tự của câu gốc do AI trả về. Độ dài câu dịch quyết định áp lực tạo voice và đồng bộ ở Step 4–5. Batch quá lớn có thể khó xử lý lỗi; batch nhỏ tăng số lượt gọi provider.
- **Nguồn code:** [pipeline Step 3](../video_translator/pipeline/mock_steps/translation.py), [workflow dịch](../video_translator/steps/step3_translation/service.py), [provider Gemini](../video_translator/steps/step3_translation/providers/gemini/provider.py).

## Step 4 — Tạo tiếng nói TTS

- **Đầu vào:** bản dịch Step 3, provider TTS, voice/reference theo speaker và các thiết lập tốc độ/thiết bị.
- **Xử lý:** tạo **một WAV riêng cho mỗi segment còn thời lượng**; segment có `start = end` không được tạo voice. Thông tin provider, model, voice, reference và thiết bị thực tế được ghi cho từng segment. Một số provider chuẩn hóa định dạng âm thanh hoặc đổi tốc độ sau khi tổng hợp. Thiết lập project ở Step 04 có Auto Normalize bật mặc định và mức điều chỉnh voice từ −10 đến +10 dB (mặc định +2 dB). Khi bật, gain được áp dụng cho từng WAV nhưng tự giới hạn theo đỉnh âm thanh ở −1 dBFS để tránh clipping; đoạn đã lớn có thể nhận mức tăng thực tế thấp hơn. Thiết lập này cũng được dùng khi Step 05 tạo lại voice.
- **Model/engine:** mặc định cho tiếng Việt là **VieNeu-TTS v3 Turbo**. Các lựa chọn đã triển khai khác gồm **Piper TTS** (voice model ONNX), **MeloTTS + OpenVoice V2** và **Edge TTS** online. XTTS-v2 chưa triển khai. FFmpeg có thể tham gia chuẩn hóa WAV.
- **Đầu ra:** thư mục WAV từng segment và `manifest.json` của TTS candidate.
- **Điểm cần xem xét:** tổng thời gian tăng theo số segment và tốc độ tổng hợp của provider. Cần kiểm tra WAV có nội dung lặp hoặc dài bất thường trước Step 5; đồng bộ thời gian không thể sửa lỗi model đã đọc lặp trong chính WAV.
- **Nguồn code:** [pipeline Step 4](../video_translator/pipeline/mock_steps/text_to_speech.py), [worker TTS](../video_translator/steps/step4_tts/worker.py).

## Step 5 — Đồng bộ voice với thời gian video

- **Đầu vào:** WAV của Step 4, mốc thời gian segment, thời lượng video và cấu hình đồng bộ.
- **Xử lý:** với mỗi WAV, đo thời lượng; tùy chọn cắt im lặng đầu/cuối; tính thời gian có thể dùng gồm slot gốc và khoảng trống sau câu; tăng tốc voice tối đa theo cấu hình (mặc định `1.35×`), rồi đệm/cắt WAV. Nếu voice vẫn dài và bật kéo dài video, tạo `video_timing_plan`. Chế độ Hybrid ưu tiên làm chậm hình tới tốc độ tối thiểu (mặc định `0.85×`), phần thiếu tiếp theo mới đóng băng hình trong giới hạn. Các segment phía sau nhận offset tích lũy. **Step này chưa render video.**
- **Model/engine:** luồng chính **không có AI model**; dùng FFprobe/FFmpeg và thuật toán thời gian. Chức năng sửa lỗi tùy chọn có thể mượn thời gian của đoạn lân cận hoặc gọi lại **model dịch Step 3** để viết ngắn và **TTS Step 4** để tạo lại voice.
- **Đầu ra:** WAV đồng bộ từng segment và manifest chứa `adjusted_start`, `play_duration`, lỗi đồng bộ, chiến lược xử lý và `video_timing_plan`.
- **Điểm cần xem xét:** hiện mỗi segment có thể cần nhiều lượt FFprobe/FFmpeg; số lượng segment lớn gây overhead. Timestamp Step 2 sai có thể khiến voice được đặt lệch dù WAV Step 4 hoàn toàn đúng.
- **Nguồn code:** [pipeline Step 5](../video_translator/pipeline/mock_steps/audio_sync.py), [dịch tốc độ/đệm âm thanh](../video_translator/steps/step5_alignment/service.py), [kế hoạch Hybrid](../video_translator/steps/step5_alignment/alignment.py).

## Step 6 — Dựng voice track hoàn chỉnh

- **Đầu vào:** manifest và các WAV đã đồng bộ của Step 5; thời lượng video sau khi tính kéo dài.
- **Xử lý:** đặt từng WAV tại `adjusted_start`, trộn các đoạn giao nhau. Timeline được chia thành lô tối đa 40 segment để hạn chế độ dài lệnh FFmpeg trên Windows; mỗi lô tạo một WAV, sau đó các lô được trộn thành một track dài bằng video.
- **Model/engine:** **không có AI model**; FFmpeg thực hiện delay, mix, limiter, resample.
- **Đầu ra:** `voice_track.wav` mono 48 kHz và manifest Step 6.
- **Điểm cần xem xét:** nhiều file đầu vào, WAV tạm và hai tầng trộn có thể làm tăng thời gian/I/O. Nên đo riêng thời gian tạo từng lô và lần trộn cuối; không nên thay đổi chiến lược trộn nếu chưa kiểm tra các trường hợp voice chồng nhau.
- **Nguồn code:** [pipeline Step 6](../video_translator/pipeline/mock_steps/build_audio.py), [dựng timeline audio](../video_translator/steps/step6_render/service.py).

## Step 7 — Render video cuối

- **Đầu vào:** video gốc, `voice_track.wav`, tùy chọn voice gốc/background/phụ đề và `video_timing_plan` từ Step 5.
- **Xử lý:** trộn voice mới với các nguồn âm thanh tùy chọn. Nếu có chỉnh thời gian hình, FFmpeg cắt thành các khoảng, làm chậm hoặc đóng băng theo plan rồi nối lại. Nếu bật burn subtitle, phụ đề được vẽ trực tiếp lên khung hình; nếu chỉ tạo SRT, file phụ đề được lưu riêng. Xuất MP4 và ghi manifest.
- **Model/engine:** **không có AI model**; FFmpeg mã hóa video **H.264 `libx264`, preset `medium`, CRF 20**, âm thanh **AAC 192 kb/s**.
- **Đầu ra:** `translated_video.mp4`, tùy chọn `translated.srt` và manifest Step 7.
- **Điểm cần xem xét:** code hiện **luôn mã hóa lại toàn bộ video**. Khi có slow motion/freeze hoặc burn subtitle, xử lý khung hình là cần thiết; khi **không** có hai điều đó, có cơ hội copy luồng hình gốc và chỉ mã hóa audio mới. Với trường hợp buộc render hình, có thể đánh giá preset nhanh hơn hoặc hardware encoder nhưng cần đo tốc độ, chất lượng và tính tương thích trên cùng video.
- **Nguồn code:** [pipeline Step 7](../video_translator/pipeline/mock_steps/render.py), [FFmpeg render](../video_translator/steps/step7_export/service.py).

## Thứ tự khảo sát cải tiến đề xuất

1. **Chất lượng Step 2:** cảnh báo câu thiếu, khoảng thời gian bất thường và cho phép sửa transcript/timestamp trước khi chạy tiếp.
2. **Tốc độ Step 7:** đo thời gian render và thử nhánh copy video khi không có chỉnh thời gian hình hoặc burn subtitle; so sánh với mã hóa hiện tại.
3. **Chất lượng Step 4:** phát hiện WAV lặp câu hoặc dài bất thường trước khi đồng bộ.
4. **Overhead Step 5–6:** ghi thời gian theo segment/lô và số lần gọi FFmpeg để chọn điểm tối ưu đúng.

Các ưu tiên trên chưa phải cam kết triển khai; cần đo và chốt tiêu chí chất lượng/tốc độ trước khi thay đổi code.
