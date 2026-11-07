"""Use the project's translation provider to correct Chinese ASR with story context."""

from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from ...config.batch_prompts import PROMPT_BATCH_ROOT
from ...config.gemini import GEMINI_DEFAULT_MODEL, GEMINI_PROVIDER_NAME
from ...config.web_translation import CHATGPT_WEB_PROVIDER_NAME
from ...steps.step3_translation.providers import create_translation_provider
from ...story_content import load_story_content
from ...state import ProjectState
from .txt_repair import source_transcript_for_repair


def _comparable(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", text).casefold()
        if char.isalnum()
    )


def correct_asr_with_story(
    state: ProjectState, story_path: Path, settings: dict[str, object],
    video_name: str, provider: object | None = None,
) -> tuple[dict[int, str], list[tuple[int, int]]]:
    if not story_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy story của Group: {story_path}")
    story = load_story_content(story_path).strip()
    if not story:
        raise ValueError(f"Story của Group đang trống: {story_path}")
    if not settings:
        raise ValueError("Chưa cấu hình provider mặc định tại Thiết lập project → Step 3.")
    _source_id, _payload, source_result = source_transcript_for_repair(state)
    segments = source_result.segments
    if not segments:
        raise ValueError("Transcript Step 2 không có segment để sửa ASR.")
    provider_name = str(settings.get("provider", GEMINI_PROVIDER_NAME))
    if provider is None:
        provider = create_translation_provider(
            provider_name, str(settings.get("model", GEMINI_DEFAULT_MODEL)),
            "Chinese", "Chinese", settings,
        )
    content = {
        "video_name": video_name,
        "segments": [
            {"id": segment.id, "start": segment.start, "end": segment.end,
             "source_text": segment.source_text}
            for segment in segments
        ],
    }
    if provider_name != CHATGPT_WEB_PROVIDER_NAME:
        content["story_content"] = story
    if provider_name == CHATGPT_WEB_PROVIDER_NAME:
        prompt_dir = PROMPT_BATCH_ROOT / "chatgpt_web" / "chinh_sua"
        first_prompt = (prompt_dir / "prompt1.md").read_text(encoding="utf-8")
        later_prompt = (prompt_dir / "prompt2.md").read_text(encoding="utf-8")
        response = provider.correct_asr_in_chat(
            content, str(story_path), story, first_prompt, later_prompt,
        )
    else:
        input_name = "asr-repair-input.json"
        output_name = "asr-repair-response.json"
        prompt = (PROMPT_BATCH_ROOT / "asr_repair.md").read_text(encoding="utf-8")
        prompt = prompt.replace("{input_filename}", input_name)
        response = provider.correct_asr(content, prompt, input_name, output_name)
    if not isinstance(response, list) or len(response) != len(segments):
        raise ValueError("AI không trả đủ một dòng new_content cho mỗi segment.")
    corrected: dict[int, str] = {}
    for segment, item in zip(segments, response, strict=True):
        if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] != segment.id:
            raise ValueError(f"ID phản hồi AI không khớp segment {segment.id}.")
        if (type(item.get("start")) not in (int, float)
                or type(item.get("end")) not in (int, float)
                or item["start"] != segment.start or item["end"] != segment.end
                or item.get("source_text") != segment.source_text):
            raise ValueError(f"AI đã thay đổi timeline hoặc source_text của segment {segment.id}.")
        value = item.get("new_content")
        if not isinstance(value, str):
            raise ValueError(f"new_content của segment {segment.id} không phải chuỗi.")
        value = value.strip()
        if segment.source_text.strip() and not value:
            raise ValueError(f"AI trả nội dung rỗng tại segment {segment.id}.")
        corrected[segment.id] = value

    # Check adjacent, disjoint pairs against the corrected text, not the ASR input.
    duplicate_pairs: list[tuple[int, int]] = []
    index = 0
    while index + 1 < len(segments):
        first, second = segments[index:index + 2]
        left, right = _comparable(corrected[first.id]), _comparable(corrected[second.id])
        if left and right and max(len(left), len(right)) > 4:
            similarity = SequenceMatcher(None, left, right, autojunk=False).ratio()
            if similarity > 0.80:
                removed = second if len(left) >= len(right) else first
                corrected[removed.id] = ""
                duplicate_pairs.append((first.id, second.id))
                index += 2
                continue
        index += 1
    return corrected, duplicate_pairs
