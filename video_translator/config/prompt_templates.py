from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
import os

from .gemini import GEMINI_PROVIDER_NAME
from .web_translation import CHATGPT_WEB_PROVIDER_NAME, CLAUDE_WEB_PROVIDER_NAME


PROMPT_TEMPLATE_ROOT = Path(os.environ.get("TRANSLANGUAGE_DISTRIBUTION_ROOT", Path(__file__).resolve().parents[2])) / "reference" / "prompt_template"
DEFAULT_PROMPT_TEMPLATE_ID = "standard"
GEMINI_DEFAULT_PROMPT_TEMPLATE_ID = "Tu_tien"
GEMINI_LEGACY_TEMPLATE_IDS = {
    "standard": GEMINI_DEFAULT_PROMPT_TEMPLATE_ID,
    "cotrang-chiensu": "Cotrang_chiensu",
}
PROMPT_SECTION = "PROMPT"
PROMPT_TEMPLATE_VARIABLES = (
    "source_language",
    "target_language",
    "input_filename",
    "output_filename",
    "segment_start",
    "segment_end",
    "segment_count",
    "batch_index",
    "batch_total",
)
PROVIDER_PROMPT_FOLDERS = {
    GEMINI_PROVIDER_NAME: "google_gemini",
    CHATGPT_WEB_PROVIDER_NAME: "chatgpt_web",
    CLAUDE_WEB_PROVIDER_NAME: "claude_web",
}


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    provider: str
    template_id: str
    name: str
    prompt: str
    path: Path

    @property
    def stages(self) -> dict[str, str]:
        """Compatibility view for callers that still expect the old stage mapping."""
        return {PROMPT_SECTION: self.prompt, "TRANSLATION": self.prompt}


def _template_folder(provider: str) -> Path:
    folder_name = PROVIDER_PROMPT_FOLDERS.get(provider)
    if not folder_name:
        raise ValueError(f"Provider không hỗ trợ prompt template: {provider}")
    return PROMPT_TEMPLATE_ROOT / folder_name


def _parse_template(provider: str, path: Path) -> PromptTemplate:
    text = path.read_text(encoding="utf-8")
    if provider == GEMINI_PROVIDER_NAME:
        prompt = text.strip()
        if not prompt:
            raise ValueError(f"Prompt template {path.name} trống.")
        return PromptTemplate(provider, path.stem, path.stem, prompt, path)
    name_match = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    name = name_match.group(1).strip() if name_match else path.stem
    headings = list(re.finditer(r"^##\s+([A-Z_]+)\s*$", text, re.MULTILINE))
    sections: dict[str, str] = {}
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        sections[match.group(1)] = text[match.end() : end].strip()
    prompt = sections.get(PROMPT_SECTION) or sections.get("TRANSLATION", "")
    if not prompt:
        raise ValueError(
            f"Prompt template {path.name} thiếu phần ## {PROMPT_SECTION}."
        )
    return PromptTemplate(provider, path.stem, name, prompt, path)


def list_prompt_templates(provider: str) -> list[PromptTemplate]:
    folder = _template_folder(provider)
    if not folder.is_dir():
        return []
    templates = [
        _parse_template(provider, path)
        for path in folder.glob("*.md")
        if path.stem != "translation_review"
    ]
    return sorted(
        templates,
        key=lambda item: (
            item.template_id != (
                GEMINI_DEFAULT_PROMPT_TEMPLATE_ID
                if provider == GEMINI_PROVIDER_NAME else DEFAULT_PROMPT_TEMPLATE_ID
            ),
            item.name.casefold(),
        ),
    )


def load_prompt_template(
    provider: str,
    template_id: str = DEFAULT_PROMPT_TEMPLATE_ID,
) -> PromptTemplate:
    normalized = Path(template_id.strip() or DEFAULT_PROMPT_TEMPLATE_ID).stem
    if provider == GEMINI_PROVIDER_NAME:
        normalized = GEMINI_LEGACY_TEMPLATE_IDS.get(normalized, normalized)
    path = _template_folder(provider) / f"{normalized}.md"
    if not path.is_file():
        raise ValueError(
            f"Không tìm thấy prompt template '{normalized}' của {provider}."
        )
    return _parse_template(provider, path)


def strip_prompt_marker(prompt: str) -> str:
    """Remove standalone PROMPT headings from content sent to Gemini."""
    return re.sub(
        r"^[ \t]*##[ \t]*PROMPT[ \t]*(?:\r?\n|$)",
        "",
        prompt,
        flags=re.IGNORECASE | re.MULTILINE,
    ).strip()


def render_prompt_template(
    provider: str,
    template_id: str,
    stage: str = PROMPT_SECTION,
    **values: str,
) -> str:
    template = load_prompt_template(provider, template_id)
    prompt = strip_prompt_marker(template.prompt) if provider == GEMINI_PROVIDER_NAME else template.prompt
    for name, value in values.items():
        prompt = prompt.replace("{" + name + "}", value)
    return prompt


def step3_batch_filenames(
    stage: str,
    first_segment_id: int,
    last_segment_id: int,
) -> tuple[str, str]:
    normalized_stage = stage.strip().lower()
    stem = f"step3-{normalized_stage}-{first_segment_id:04d}-{last_segment_id:04d}"
    return f"{stem}.json", f"step3-{normalized_stage}-result-{first_segment_id:04d}-{last_segment_id:04d}.json"


def _safe_template_id(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return slug or "prompt"


def save_prompt_template(
    provider: str,
    name: str,
    prompt: str | dict[str, str],
    template_id: str = "",
) -> PromptTemplate:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Tên prompt không được để trống.")
    if isinstance(prompt, dict):
        clean_prompt = str(
            prompt.get(PROMPT_SECTION) or prompt.get("TRANSLATION") or ""
        ).strip()
    else:
        clean_prompt = str(prompt).strip()
    if not clean_prompt:
        raise ValueError("Nội dung prompt không được để trống.")
    folder = _template_folder(provider)
    folder.mkdir(parents=True, exist_ok=True)
    if provider == GEMINI_PROVIDER_NAME and not template_id and not re.fullmatch(r"[\w-]+", clean_name):
        raise ValueError("Tên prompt Gemini phải là tên file hợp lệ, không gồm .md.")
    normalized_id = (
        Path(template_id).stem if template_id else
        clean_name if provider == GEMINI_PROVIDER_NAME else _safe_template_id(clean_name)
    )
    if not template_id:
        base = normalized_id
        suffix = 2
        while (folder / f"{normalized_id}.md").exists() or (
            provider == GEMINI_PROVIDER_NAME and normalized_id in GEMINI_LEGACY_TEMPLATE_IDS
        ):
            normalized_id = f"{base}-{suffix}"
            suffix += 1
    path = folder / f"{normalized_id}.md"
    if provider == GEMINI_PROVIDER_NAME:
        path.write_text(clean_prompt.rstrip() + "\n", encoding="utf-8")
    else:
        content = [f"# {clean_name}", "", f"## {PROMPT_SECTION}", "", clean_prompt]
        path.write_text("\n".join(content).rstrip() + "\n", encoding="utf-8")
    return _parse_template(provider, path)


def delete_prompt_template(provider: str, template_id: str) -> None:
    normalized = Path(template_id).stem
    if normalized == DEFAULT_PROMPT_TEMPLATE_ID or (
        provider == GEMINI_PROVIDER_NAME and normalized == GEMINI_DEFAULT_PROMPT_TEMPLATE_ID
    ):
        raise ValueError("Prompt tiêu chuẩn không thể bị xóa.")
    path = _template_folder(provider) / f"{normalized}.md"
    if not path.is_file():
        raise ValueError(f"Không tìm thấy prompt template '{normalized}'.")
    path.unlink()
