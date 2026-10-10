from __future__ import annotations

PROPER_NAME_MODE_AUTO = "Tự động theo ngôn ngữ đích"
PROPER_NAME_MODE_HAN_VIET = "Hán–Việt"
PROPER_NAME_MODE_PINYIN = "Pinyin"
PROPER_NAME_MODE_ORIGINAL = "Giữ nguyên chữ gốc"

PROPER_NAME_MODES = (
    PROPER_NAME_MODE_AUTO,
    PROPER_NAME_MODE_HAN_VIET,
    PROPER_NAME_MODE_PINYIN,
    PROPER_NAME_MODE_ORIGINAL,
)


def resolve_proper_name_policy(
    source_language: str,
    target_language: str,
    requested_mode: str,
) -> dict[str, object]:
    mode = requested_mode if requested_mode in PROPER_NAME_MODES else PROPER_NAME_MODE_AUTO
    source_is_chinese = source_language.strip().casefold() == "chinese"
    target_is_vietnamese = target_language.strip().casefold() == "vietnamese"

    if mode == PROPER_NAME_MODE_HAN_VIET:
        person_style = "sino_vietnamese_reading"
        place_style = "established_target_name_else_sino_vietnamese"
    elif mode == PROPER_NAME_MODE_PINYIN:
        person_style = "hanyu_pinyin_without_tone_marks"
        place_style = "established_target_name_else_pinyin"
    elif mode == PROPER_NAME_MODE_ORIGINAL:
        person_style = "preserve_source_script"
        place_style = "preserve_source_script"
    elif source_is_chinese and target_is_vietnamese:
        person_style = "sino_vietnamese_reading"
        place_style = "established_vietnamese_name_else_sino_vietnamese"
    elif source_is_chinese:
        person_style = "hanyu_pinyin_without_tone_marks"
        place_style = "established_target_name_else_pinyin"
    else:
        person_style = "established_target_name_else_preserve"
        place_style = "established_target_name_else_preserve"

    return {
        "requested_mode": mode,
        "source_language": source_language,
        "target_language": target_language,
        "person_name_style": person_style,
        "place_name_style": place_style,
        "foreign_name_style": "restore_original_or_established_target_name_when_confident",
        "uncertain_name_style": "preserve_source_form_and_mark_uncertain",
        "name_order": "preserve_source_culture_order_unless_target_has_an_established_form",
    }
