from __future__ import annotations

from typing import Any


MINOR_MALE_PROFILE = "__minor_male__"
MINOR_FEMALE_PROFILE = "__minor_female__"
MINOR_UNKNOWN_PROFILE = "__minor_unknown__"
LEGACY_SHARED_PROFILE = "__shared__"
REFERENCE_SPEAKERS = (
    "SPEAKER_00",
    "SPEAKER_01",
    "SPEAKER_02",
    "SPEAKER_03",
)

GROUP_LABELS = {
    MINOR_MALE_PROFILE: "Nhân vật phụ — Nam",
    MINOR_FEMALE_PROFILE: "Nhân vật phụ — Nữ",
    MINOR_UNKNOWN_PROFILE: "Nhân vật phụ — Chưa xác định",
    LEGACY_SHARED_PROFILE: "SPEAKER_04 trở đi / khác",
}


def speaker_profile_assignments(
    speakers: tuple[str, ...],
    dialogue_profile: dict[str, Any] | None,
) -> tuple[dict[str, str], dict[str, dict[str, Any]], bool]:
    raw_items = (dialogue_profile or {}).get("speakers", [])
    items = [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
    by_id = {
        str(item.get("speaker_id", "")).strip(): item
        for item in items
        if str(item.get("speaker_id", "")).strip()
    }
    classified = any(str(item.get("importance", "")).strip() for item in items)
    assignments: dict[str, str] = {}
    profiles: dict[str, dict[str, Any]] = {}

    if not classified:
        for speaker_id in speakers:
            assignments[speaker_id] = (
                speaker_id
                if speaker_id in REFERENCE_SPEAKERS
                else LEGACY_SHARED_PROFILE
            )
            profiles[speaker_id] = by_id.get(speaker_id, {})
        return assignments, profiles, False

    group_for_gender = {
        "male": MINOR_MALE_PROFILE,
        "female": MINOR_FEMALE_PROFILE,
        "unknown": MINOR_UNKNOWN_PROFILE,
    }
    for speaker_id in speakers:
        profile = by_id.get(speaker_id, {})
        profiles[speaker_id] = profile
        importance = str(profile.get("importance", "minor")).strip().lower()
        gender = str(profile.get("gender", "unknown")).strip().lower()
        assignments[speaker_id] = (
            speaker_id
            if importance in {"main", "supporting"}
            else group_for_gender.get(gender, MINOR_UNKNOWN_PROFILE)
        )
    return assignments, profiles, True


def resolve_speaker_profile(settings: dict[str, Any], speaker_id: str) -> dict[str, Any]:
    if bool(settings.get("single_voice", False)):
        shared = settings.get("shared_speaker_profile", {})
        return dict(shared) if isinstance(shared, dict) else {}
    profiles = settings.get("speaker_profiles", {})
    profiles = profiles if isinstance(profiles, dict) else {}
    assignments = settings.get("speaker_profile_assignments", {})
    assignments = assignments if isinstance(assignments, dict) else {}
    group_profiles = settings.get("speaker_group_profiles", {})
    group_profiles = group_profiles if isinstance(group_profiles, dict) else {}
    profile_key = str(assignments.get(speaker_id, speaker_id))
    raw_profile = (
        profiles.get(profile_key, {})
        if profile_key == speaker_id
        else group_profiles.get(profile_key, {})
    )
    if not isinstance(raw_profile, dict):
        raw_profile = {}
    if not raw_profile and speaker_id not in REFERENCE_SPEAKERS:
        fallback = profiles.get("SPEAKER_03", {})
        if isinstance(fallback, dict):
            raw_profile = fallback
    if not raw_profile:
        shared = settings.get("shared_speaker_profile", {})
        if isinstance(shared, dict):
            raw_profile = shared
    return dict(raw_profile)
