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

## Step 1.5 — Tạo SRT từ voice

Provider Whisper dùng Faster-Whisper kết hợp Stable-ts theo pipeline `reference/colab_srt/windows_srt_standalone.py`. VAD luôn bật. Ngôn ngữ nhận dạng lấy từ project. Mặc định là `large-v2` + `int8_float32`; model gồm `medium`, `large-v2`, `large-v3`, kiểu tính toán gồm `int8_float32`, `float16`, `float32`. Thiết bị gồm Auto/CPU/GPU. CTranslate2 kiểm tra khả năng hỗ trợ kiểu tính toán trên thiết bị đã chọn.

1. Đọc WAV đã chọn từ Step 1 bằng `soundfile` ở dạng float32, lấy trung bình các kênh nếu là stereo và resample về 16 kHz bằng `scipy.signal.resample_poly` (tỷ lệ rút gọn bằng GCD). Kiểm tra audio không rỗng và không có mẫu không hữu hạn; nhận dạng toàn bộ audio gốc và giữ ranh giới segment ASR. Silero VAD chạy với ngưỡng 0,35 để xác định vùng lời nói.
2. Căn lại những segment có timing lỗi, chia cue theo mốc từ tại khoảng nghỉ 0,3 giây hoặc dấu kết câu, với mục tiêu tối đa 6 giây và 40 ký tự. Segment thiếu timing từ được giữ để rà soát và có thể vượt các mục tiêu này.
3. Đối chiếu cue với VAD, nhận dạng bổ sung vùng lời nói chưa được phủ từ 0,4 giây và rà 15 giây đầu/cuối audio. Cửa sổ bổ sung dài tối đa 15 giây, có ngữ cảnh và chỉ thêm phần từ có timing nằm trong khoảng chưa được phủ.
4. Kiểm tra text các cue giữ đủ nội dung từng segment trước khi ghép. Ghép các cue recovery liên tiếp có mốc từ với khoảng nghỉ giữa từ cuối cue trước và từ đầu cue sau từ 0 đến dưới 0,3 giây, giữ danh sách segment nguồn và đánh lại ID. Quy tắc ghép này độc lập với tùy chọn **Gộp segment liền kề**; sau ghép, cue có thể vượt 6 giây hoặc 40 ký tự và được gắn cờ rà soát.
5. Stable-ts refine start với `steps="s"`, `word_level=False`, bước 0,02 giây, giới hạn dịch chuyển 0,5 giây. Cue recovery đã ghép được bỏ qua refine với lý do `merged_recovery_word_timing`, đồng thời giữ ranh giới để tránh refine cue sau chồng lên cue đã ghép. Kiểm tra lại giới hạn thời lượng/ký tự và chồng thời gian sau ghép/refine. Báo cáo lưu mốc gốc, mốc đề xuất, trạng thái và lý do.
6. Khi bật **Speaker diarization**, pyannote chạy trên voice gốc và gán speaker cho từng cue theo thời lượng giao nhau. Tùy chọn **Gộp segment liền kề** tiếp tục áp dụng sau ASR.

Step 1.5 ghi SRT bằng Whisper và thêm `_speaker.srt` khi bật diarization. File `_step1_5_device.json` ghi thiết bị, kiểu tính toán và thông tin khi Auto chuyển sang CPU. Báo cáo ASR nằm tại `srt/diagnostics/<tên-video>/<lần-chạy>/report.json`.

Khi bật **OCR** trong Thiết lập project → Step 2, Step 1.5 dùng video gốc và `srt/<tên-video>.srt` vừa được Whisper tạo trong cùng lượt chạy. Tua video xem trước và kéo chọn vùng phụ đề, hoặc chỉnh các ô X, Y, chiều rộng và chiều cao theo phần trăm. Vùng này áp dụng cho các video trong project. RapidOCR CUDA dùng logic V25 và mở rộng cửa sổ mặc định 0,5 giây mỗi phía. Mục trên 2 ký tự quét từ giữa ra hai phía với bước 0,2 giây. Mục tối đa 2 ký tự quét từ trái sang phải với bước 0,1 giây. Khi thiếu một phần chữ ở mục dài, OCR quét bổ sung phía còn thiếu với bước 0,1 giây và ghép tối đa 2 block. V25 dùng confidence trên 95% để chốt sớm bản đọc. Mặc định dùng model RapidOCR, ảnh Original, Det+Rec, đọc theo Timestamp và Text score 0,8. Nếu thiếu chữ, mục nhận `A`, hoặc `B+` kèm chữ SRT với mục tối đa 2 ký tự khi bật tùy chọn dự phòng (mặc định tắt). Kết quả được ghi vào `srt/<tên-video>_ocr.srt` với cùng số thứ tự và timestamp. Nếu OCR lỗi, SRT Whisper vẫn được giữ lại.

## Step 2 — Nhận dạng lời nói và speaker

Ngoài nhận dạng audio, Step 2 có một nút nhập file `.txt` hoặc `.srt` để tạo transcript candidate mới. TXT đọc từng dòng không trắng và thay text theo thứ tự các segment của transcript đang chọn; số dòng phải khớp, ID/Start/End/Speaker giữ nguyên. SRT đọc riêng từng mục phụ đề, dùng timestamp và text trong file để tạo segment mới với ID liên tiếp và `SPEAKER_UNKNOWN`; các mục không được chồng thời gian. Khi nhập thất bại, ứng dụng báo file và nguyên nhân; output cũ không bị thay đổi. Khi thành công, output mới được chọn làm input Step 3.

- **Đầu vào:** file SRT đã tạo ở Step 1.5 hoặc SRT được nhập vào.
- **Xử lý:** đọc từng cue thành segment, kiểm tra timeline và tạo transcript candidate cho Step 3.
- **Đầu ra:** `transcripts/<candidate-id>/transcript.json` với ID, `start`, `end`, `speaker_id` và câu gốc. Luồng chuyển SRT dùng `_ocr.srt` khi bật OCR và SRT gốc khi tắt.
- **Nguồn code:** [pipeline Step 1.5 và Step 2](../video_translator/pipeline/mock_steps/speech_to_text.py), [pipeline Whisper + VAD](../video_translator/steps/step2_transcription/providers/faster_whisper/srt_pipeline.py), [service và diarization](../video_translator/steps/step2_transcription/service.py).

## Step 3 — Dịch văn bản

Để chỉnh output bằng bảng tính, chọn một candidate ở Step 3 và xuất CSV. Sửa các cột `ID, Start, End, Speaker, Translation, Source`; cột `STT` không dùng khi nhập. Hàng thêm mới có thể để trống `ID` để ứng dụng cấp ID duy nhất và để trống `Source` nếu chỉ cần sinh voice. Nhập lại CSV sẽ tạo `translated_segments.json` trong một candidate mới, giữ candidate nguồn. Output mới được chọn làm input Step 4; chạy lại Step 4 → 7 để tạo voice và video. Với hàng mới chỉ có từ Step 3, sửa voice ở Step 5 không yêu cầu ID phải có trong transcript Step 2.

- **Đầu vào:** các segment của transcript Step 2, cặp ngôn ngữ, provider/model, prompt và kích thước batch.
- **Xử lý:** gửi toàn bộ segment của file trong một batch hoặc chia batch theo cấu hình; kèm ngữ cảnh trước/sau và các bản dịch trước đó. Kết quả được kiểm tra ID rồi gắn bản dịch vào segment. Segment có bản dịch rỗng được đặt `start = end` để các bước sau bỏ qua. Với Gemini, thông tin request/response/error được lưu trong thư mục diagnostics.
- **Model/engine:** mặc định **Google Gemini `gemini-3.5-flash-lite`**; có các model Gemini khác, **DeepSeek `deepseek-flash`** và provider **ChatGPT Web / Claude Web**. Model thực tế là lựa chọn của lần chạy, được ghi trong translation candidate. Mỗi folder cài nạp Google và DeepSeek API key từ file `api_keys.env` của chính folder đó.
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
- **Đầu ra:** MP4 cùng tên gốc với video input (ví dụ `01.mov` → `01.mp4`), tùy chọn `translated.srt` và manifest Step 7.
- **Điểm cần xem xét:** code hiện **luôn mã hóa lại toàn bộ video**. Khi có slow motion/freeze hoặc burn subtitle, xử lý khung hình là cần thiết; khi **không** có hai điều đó, có cơ hội copy luồng hình gốc và chỉ mã hóa audio mới. Với trường hợp buộc render hình, có thể đánh giá preset nhanh hơn hoặc hardware encoder nhưng cần đo tốc độ, chất lượng và tính tương thích trên cùng video.
- **Nguồn code:** [pipeline Step 7](../video_translator/pipeline/mock_steps/render.py), [FFmpeg render](../video_translator/steps/step7_export/service.py).

## Thứ tự khảo sát cải tiến đề xuất

1. **Chất lượng Step 2:** cảnh báo câu thiếu, khoảng thời gian bất thường và cho phép sửa transcript/timestamp trước khi chạy tiếp.
2. **Tốc độ Step 7:** đo thời gian render và thử nhánh copy video khi không có chỉnh thời gian hình hoặc burn subtitle; so sánh với mã hóa hiện tại.
3. **Chất lượng Step 4:** phát hiện WAV lặp câu hoặc dài bất thường trước khi đồng bộ.
4. **Overhead Step 5–6:** ghi thời gian theo segment/lô và số lần gọi FFmpeg để chọn điểm tối ưu đúng.

Các ưu tiên trên chưa phải cam kết triển khai; cần đo và chốt tiêu chí chất lượng/tốc độ trước khi thay đổi code.
