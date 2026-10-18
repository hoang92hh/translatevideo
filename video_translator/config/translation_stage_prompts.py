from __future__ import annotations

from .prompt_templates import (
    DEFAULT_PROMPT_TEMPLATE_ID,
    load_prompt_template,
    render_prompt_template,
)
from .web_translation import CHATGPT_WEB_PROVIDER_NAME


def load_web_translation_prompts() -> dict[str, str]:
    """Compatibility wrapper for integrations that still import the old helper."""
    return dict(
        load_prompt_template(
            CHATGPT_WEB_PROVIDER_NAME,
            DEFAULT_PROMPT_TEMPLATE_ID,
        ).stages
    )


def render_web_translation_prompt(stage: str, **values: str) -> str:
    """Render the standard ChatGPT Web template for legacy callers."""
    return render_prompt_template(
        CHATGPT_WEB_PROVIDER_NAME,
        DEFAULT_PROMPT_TEMPLATE_ID,
        stage,
        **values,
    )
