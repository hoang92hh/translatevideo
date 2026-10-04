from __future__ import annotations


VIENEU_PROVIDER = "VieNeu-TTS — Local"
MELO_OPENVOICE_PROVIDER = "MeloTTS + OpenVoice V2 — Local"
EDGE_TTS_PROVIDER = "Edge TTS — Online"
XTTS_V2_PROVIDER = "XTTS-v2 — Chưa triển khai · Chỉ phi thương mại"

VIENEU_VOICES = ("Default",)
EDGE_VOICES = (
    "vi-VN-HoaiMyNeural",
    "vi-VN-NamMinhNeural",
    "en-US-JennyNeural",
    "en-US-GuyNeural",
    "es-ES-ElviraNeural",
    "es-ES-AlvaroNeural",
    "zh-CN-XiaoxiaoNeural",
    "ja-JP-NanamiNeural",
    "ko-KR-SunHiNeural",
)
MELO_SPEAKERS = ("EN-Default", "ES-Default", "ZH-Default", "JP-Default", "KR-Default")

MELO_LANGUAGE_CODES = {
    "English": "EN",
    "Spanish": "ES",
    "Chinese": "ZH",
    "Japanese": "JP",
    "Korean": "KR",
}

PROVIDER_LICENSE_NOTES = {
    VIENEU_PROVIDER: (
        "Apache-2.0. Có thể dùng thương mại với giọng preset; giọng tham chiếu chỉ được dùng "
        "khi bạn có quyền hoặc sự đồng ý của chủ giọng."
    ),
    MELO_OPENVOICE_PROVIDER: (
        "MeloTTS và OpenVoice V2 dùng giấy phép MIT. Việc clone/chuyển giọng vẫn cần quyền "
        "hoặc sự đồng ý của chủ giọng."
    ),
    EDGE_TTS_PROVIDER: (
        "Dịch vụ online không chính thức dựa trên Microsoft Edge. Cần Internet; với sản phẩm "
        "thương mại cần tự kiểm tra điều khoản dịch vụ, nên dùng Azure Speech khi cần SLA/quyền rõ ràng."
    ),
    XTTS_V2_PROVIDER: (
        "Coqui Public Model License chỉ cho phép sử dụng mô hình phi thương mại. Provider này "
        "được giữ trong danh sách để tham khảo và chưa được triển khai."
    ),
}

