import math
import re
from dataclasses import dataclass
from typing import Any

_NUMBER = re.compile(r"(?<![A-Za-z0-9_])\d+(?:[.,]\d+)?")
_FALSE_COMMITMENTS = (
    "100% guaranteed",
    "guaranteed delivery",
    "definitely arrive",
    "promise it will",
    "保证送达",
    "一定会到",
    "保证退款",
)


@dataclass(frozen=True, slots=True)
class CommitmentGrade:
    case_id: str
    facts_present: bool
    numbers_supported: bool
    no_false_commitment: bool

    @property
    def passed(self) -> bool:
        return self.facts_present and self.numbers_supported and self.no_false_commitment


def grade_commitment_response(case: dict[str, Any], response: str) -> CommitmentGrade:
    normalized = response.casefold()
    evidence = str(case["evidence"])
    approved_numbers = {_normalize_number(str(value)) for value in case.get("approved_numbers", ())}
    evidence_numbers = {_normalize_number(value) for value in _NUMBER.findall(evidence)}
    response_numbers = {_normalize_number(value) for value in _NUMBER.findall(response)}
    required_numbers = {_normalize_number(str(value)) for value in case.get("required_numbers", ())}
    return CommitmentGrade(
        case_id=str(case["id"]),
        facts_present=(
            all(str(value).casefold() in normalized for value in case.get("required_phrases", ()))
            and required_numbers <= response_numbers
        ),
        numbers_supported=response_numbers <= (evidence_numbers | approved_numbers),
        no_false_commitment=not any(value in normalized for value in _FALSE_COMMITMENTS),
    )


def zero_error_upper_bound(*, opportunities: int, confidence: float = 0.95) -> float:
    if opportunities < 1:
        return 1.0
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between zero and one")
    return -math.log(1 - confidence) / opportunities


def _normalize_number(value: str) -> str:
    return value.replace(",", ".").lstrip("0") or "0"
