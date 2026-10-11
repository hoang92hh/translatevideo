from __future__ import annotations

import re
from pathlib import Path


PROMPT_FILE = Path(__file__).resolve().parents[2] / "reference" / "web_translation_prompts.md"
REQUIRED_STAGES = ("ANALYSIS", "TRANSLATION", "REVIEW")


def load_web_translation_prompts() -> dict[str, str]:
    text = PROMPT_FILE.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    matches = list(re.finditer(r"^##\s+(ANALYSIS|TRANSLATION|REVIEW)\s*$", text, re.MULTILINE))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1)] = text[match.end() : end].strip()
    missing = [stage for stage in REQUIRED_STAGES if not sections.get(stage)]
    if missing:
        raise ValueError(f"Thiếu phần prompt: {', '.join(missing)} trong {PROMPT_FILE}")
    return sections


def render_web_translation_prompt(stage: str, **values: str) -> str:
    prompt = load_web_translation_prompts()[stage]
    for name, value in values.items():
        prompt = prompt.replace("{" + name + "}", value)
    return prompt
