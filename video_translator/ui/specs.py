from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import StepId


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str = "text"
    default: Any = ""
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    fields: tuple[FieldSpec, ...] = ()
    available: bool = True


@dataclass(frozen=True)
class StepSpec:
    step: StepId
    number: str
    title: str
    description: str
    providers: tuple[ProviderSpec, ...] = field(default_factory=tuple)


DEVICE_FIELD = FieldSpec("device", "Thiết bị", "choice", "Auto", ("Auto", "CPU", "GPU"))

