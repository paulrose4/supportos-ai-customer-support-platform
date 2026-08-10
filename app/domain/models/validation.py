from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResponseValidationResult:
    is_valid: bool
    reason_code: str | None = None
