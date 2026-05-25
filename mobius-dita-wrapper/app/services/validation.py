"""Structured-output validation.

Runs a real JSON Schema check and translates failures into the typed ValidationError
contract (path / rule_id / severity / observed / expected / repair_hint) so the repair
step is deterministic.
"""
from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

from app.schemas.labor import ValidationError as VErr


def validate_against_schema(instance: Any, schema: dict[str, Any]) -> list[VErr]:
    """Return a list of typed errors; empty list means valid."""
    validator = Draft202012Validator(schema)
    errors: list[VErr] = []
    for err in sorted(validator.iter_errors(instance), key=lambda e: list(e.path)):
        pointer = "/" + "/".join(str(p) for p in err.absolute_path)
        errors.append(
            VErr(
                path=pointer or "/",
                rule_id=err.validator if isinstance(err.validator, str) else "schema",
                severity="error",
                observed=_safe(err.instance),
                expected=_expected(err),
                repair_hint=_hint(err),
            )
        )
    return errors


def _safe(value: Any) -> Any:
    # keep payload small and JSON-serializable in the error record
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, dict)):
        return f"<{type(value).__name__} len={len(value)}>"
    return str(value)


def _expected(err) -> Any:
    v = err.validator
    if v == "required":
        return f"required keys: {err.validator_value}"
    if v in {"type", "enum", "pattern", "minimum", "maximum", "minLength", "maxLength"}:
        return err.validator_value
    return None


def _hint(err) -> str:
    v = err.validator
    if v == "required":
        return f"Add the missing required field(s): {err.validator_value}."
    if v == "type":
        return f"Coerce value at this path to type {err.validator_value!r}."
    if v == "enum":
        return f"Replace with one of the allowed values: {err.validator_value}."
    if v == "additionalProperties":
        return "Remove properties not declared in the schema."
    return f"Fix constraint {v!r}={getattr(err, 'validator_value', None)!r} at this path."
