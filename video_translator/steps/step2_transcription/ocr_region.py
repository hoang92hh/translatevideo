"""Normalized subtitle crop shared by the settings preview and the OCR worker."""

from __future__ import annotations

import math
from collections.abc import Mapping

from ...common.models import StepId
from ...config.step_defaults import with_defaults

OcrRegion = tuple[float, float, float, float]


def resolve_region(settings: Mapping[str, object]) -> OcrRegion:
    values = with_defaults(StepId.STT, settings)
    region = values["ocr_region_normalized"]
    if region is None:
        # Older projects used a centered crop measured from the bottom.
        width = float(values["ocr_width_percent"]) / 100
        height = float(values["ocr_height_percent"]) / 100
        bottom = float(values["ocr_bottom_margin_percent"]) / 100
        region = ((1 - width) / 2, max(0.0, 1 - height - bottom), width, height)
    if not isinstance(region, (list, tuple)) or len(region) != 4:
        raise ValueError("Vùng OCR phải gồm tọa độ X, Y, chiều rộng và chiều cao.")
    x, y, width, height = map(float, region)
    if (not all(map(math.isfinite, (x, y, width, height))) or x < 0 or y < 0
            or width <= 0 or height <= 0 or x + width > 1 + 1e-9 or y + height > 1 + 1e-9):
        raise ValueError("Vùng OCR phải nằm trong khung hình video.")
    return x, y, width, height
