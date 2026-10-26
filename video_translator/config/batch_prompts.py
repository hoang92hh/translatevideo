from __future__ import annotations

import re
from typing import Any

from .gemini import GEMINI_PROVIDER_NAME
from .prompt_templates import PROMPT_TEMPLATE_ROOT, PromptTemplate, render_prompt_template
from .web_translation import CHATGPT_WEB_PROVIDER_NAME


PROMPT_BATCH_ROOT = PROMPT_TEMPLATE_ROOT.parent / "prompt_batch"
PROVIDER_FOLDERS = {
    GEMINI_PROVIDER_NAME: "google_gemini",
    CHATGPT_WEB_PROVIDER_NAME: "chatgpt_web",
}


def load_batch_prompt(provider: str, mode: str = "step") -> PromptTemplate:
    folder = PROVIDER_FOLDERS.get(provider)
    if folder is None:
        raise ValueError("Batch Step 3 dùng story hiện hỗ trợ Google Gemini và ChatGPT Web.")
    if mode == "legacy":
        path = PROMPT_TEMPLATE_ROOT / folder / "translation_review.md"
        template_id = "legacy_translation_review"
    else:
        path = PROMPT_BATCH_ROOT / folder / "translation.md"
        template_id = "step_translation_review"
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ValueError(f"Prompt batch trống: {path}")
    title = text.splitlines()[0].lstrip("# ").strip()
    return PromptTemplate(provider, template_id, title, text, path)


def prepare_translation_prompt(
    provider: str, template_id: str, stage: str,
    *, batch_context: dict[str, Any] | None = None, **values: str,
) -> str:
    """Only input preparation differs; providers retain their existing transport."""
    if stage != "TRANSLATION" or batch_context is None:
        return render_prompt_template(provider, template_id, stage, **values)
    if not str(batch_context.get("story_content", "")).strip():
        raise ValueError("Batch Step 3 chưa có nội dung story tham chiếu.")
    variables = {**values, **{key: str(value) for key, value in batch_context.items()}}
    mode = str(batch_context.get("batch_prompt_mode", "step"))
    template = load_batch_prompt(provider, mode)
    # Substitute once: braces in user-edited stories must stay literal.
    return re.sub(
        r"\{([a-z_]+)\}",
        lambda match: variables.get(match.group(1), match.group(0)),
        template.prompt,
    )
