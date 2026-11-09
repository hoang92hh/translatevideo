from __future__ import annotations

from .prompt_templates import PROMPT_TEMPLATE_ROOT


TIMING_REWRITE_PROMPT_PATH = PROMPT_TEMPLATE_ROOT / "prompt_rut_gon.md"


def load_timing_rewrite_prompt(target_language: str) -> str:
    """Load the shared Step 5 timing rewrite prompt for every supported provider."""
    prompt = TIMING_REWRITE_PROMPT_PATH.read_text(encoding="utf-8-sig").strip()
    if not prompt:
        raise ValueError(f"Prompt rút gọn đang trống: {TIMING_REWRITE_PROMPT_PATH}")
    return prompt.replace("{target_language}", target_language)
