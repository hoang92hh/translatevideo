"""RapidOCR CUDA and image processing for OCR V25."""

import math

from .ocr_segment_matcher import is_subtitle_character, join_ocr_fragments


MODEL_SIZES = ("tiny", "small", "medium")


def crop_region(frame, region):
    height, width = frame.shape[:2]
    x, y, w, h = region
    x1, y1 = round(x * width), round(y * height)
    x2, y2 = round((x + w) * width), round((y + h) * height)
    x1, y1 = max(0, min(width - 1, x1)), max(0, min(height - 1, y1))
    x2, y2 = max(x1 + 1, min(width, x2)), max(y1 + 1, min(height, y2))
    return frame[y1:y2, x1:x2]


def preprocess(img, mode, cv):
    if mode == "Original":
        return img
    if mode == "Upscale 2x":
        return cv.resize(img, None, fx=2, fy=2, interpolation=cv.INTER_CUBIC)
    gray = cv.cvtColor(img, cv.COLOR_BGR2GRAY)
    if mode == "Threshold Otsu":
        _, gray = cv.threshold(gray, 0, 255, cv.THRESH_BINARY | cv.THRESH_OTSU)
    return cv.cvtColor(gray, cv.COLOR_GRAY2BGR)


IMAGE_MODES = ("Original", "Grayscale", "Threshold Otsu", "Upscale 2x")


OCR_METHODS = ("Det+Rec", "Rec only")


SEEK_MODES = ("Timestamp", "Frame index")


def validate_ocr_options(image_mode, method, seek, text_score):
    if image_mode not in IMAGE_MODES or method not in OCR_METHODS or seek not in SEEK_MODES:
        raise ValueError("Chế độ OCR không hợp lệ")
    if not 0 <= text_score <= 1:
        raise ValueError("Text score phải từ 0 đến 1")


def run_ocr_image(engine, crop, cv, image_mode, method, text_score):
    image = preprocess(crop, image_mode, cv)
    full = method == "Det+Rec"
    return engine(image.copy(), use_det=full, use_cls=full, use_rec=True, text_score=text_score)


def extract_text(ocr_result, minimum_score: float) -> tuple[str, float]:
    """Keep raw fragments for V25 matching and score only subtitle content."""
    texts = getattr(ocr_result, "txts", None)
    scores = getattr(ocr_result, "scores", None)
    texts = [] if texts is None else list(texts)
    scores = [] if scores is None else list(scores)
    fragments = []
    content_scores = []
    for index, raw in enumerate(texts):
        text = str(raw)
        score = float(scores[index]) if index < len(scores) else 0.0
        if not text.strip() or not math.isfinite(score) or not minimum_score <= score <= 1.0:
            continue
        fragments.append(text)
        if any(is_subtitle_character(char) for char in text):
            content_scores.append(score)
    confidence = sum(content_scores) / len(content_scores) if content_scores else 0.0
    return join_ocr_fragments(fragments), confidence


def initialize_cuda_ocr(report, det_model, rec_model, default_model, method):
    """Start RapidOCR on CUDA and verify real detection/recognition sessions.
    Reject silent CPU fallback. Requires ONNX Runtime GPU and NVIDIA CUDA DLLs.
    """
    import cv2
    import numpy as np
    import onnxruntime as ort
    from rapidocr import RapidOCR, EngineType, ModelType, OCRVersion
    report("Đang nạp CUDA/cuDNN từ môi trường Python…")
    # Load CUDA/cuDNN from NVIDIA wheels, matching the standalone OCR environment.
    if not hasattr(ort, "preload_dlls"):
        raise RuntimeError("OCR cần onnxruntime-gpu 1.23.2 để nạp CUDA/cuDNN.")
    ort.preload_dlls(directory="")
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        raise RuntimeError("ONNX Runtime không có CUDAExecutionProvider. Hãy kiểm tra onnxruntime-gpu.")
    report("Đang khởi tạo RapidOCR bằng GPU CUDA…")
    params = {
        "EngineConfig.onnxruntime.use_cuda": True,
        "Det.engine_type": EngineType.ONNXRUNTIME,
        "Det.ocr_version": OCRVersion.PPOCRV6,
        "Det.model_type": ModelType(det_model),
        "Det.lang_type": "ch",
        "Rec.engine_type": EngineType.ONNXRUNTIME,
        "Rec.ocr_version": OCRVersion.PPOCRV6,
        "Rec.model_type": ModelType(rec_model),
        "Rec.lang_type": "ch",
    }
    if default_model:
        params = {"EngineConfig.onnxruntime.use_cuda": True}
    engine = RapidOCR(params=params)
    report(f"Model thực tế: {engine.cfg.Det.ocr_version}/{engine.cfg.Det.model_type}, "
           f"{engine.cfg.Rec.ocr_version}/{engine.cfg.Rec.model_type}")
    # RapidOCR lazily creates ONNX sessions. Trigger BOTH detector and recognizer.
    # White test images alone do not create a recognition session.
    sample = np.full((160, 800, 3), 255, dtype=np.uint8)
    cv2.putText(sample, "HELLO WORLD", (50, 100),
                cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 4)
    full = method == "Det+Rec"
    probe = engine(sample, use_det=full, use_cls=full, use_rec=True, text_score=0.5)
    probe_text = getattr(probe, "txts", None)
    if probe_text is None or len(probe_text) == 0:
        raise RuntimeError("Không kích hoạt được mô hình Recognition trong bước thử GPU.")
    components = [("Recognition", engine.text_rec)]
    if full:
        components.insert(0, ("Detection", engine.text_det))
    for label, component in components:
        # In RapidOCR 3.10.0: component.session = OrtInferSession;
        # component.session.session = onnxruntime.InferenceSession.
        real_session = getattr(getattr(component, "session", None), "session", None)
        if real_session is None or not hasattr(real_session, "get_providers"):
            raise RuntimeError(f"Không thể kiểm tra ONNX Session của {label}.")
        providers = real_session.get_providers()
        report(f"{label}: {providers}")
        if not providers or providers[0] != "CUDAExecutionProvider":
            raise RuntimeError(f"{label} không sử dụng CUDA; đã chặn chạy ngầm trên CPU: {providers}")
    report(f"Đã xác nhận GPU CUDA cho chế độ {method}.")
    return engine
