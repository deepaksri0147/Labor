"""Shared labor/validation schemas reused by the DITA service."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ValidationError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    rule_id: str
    severity: str
    observed: Any = None
    expected: Any = None
    repair_hint: str | None = None
